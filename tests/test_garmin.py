"""Garmin: JSON parsers, sleep stages with Garmin's own times, DI tokens
(refresh with rotation, reconnect), the full sync into the health tables and
Activity (a session Strava has only gets its Garmin id), the login with its MFA
code, routes, migration. No network: Garmin is an httpx.MockTransport and the
login library a fake."""

import asyncio
import base64
import importlib.util
import json
import pathlib
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.crypto import decrypt_secret, encrypt_secret
from app.dependencies import get_current_user
from app.models.activity import Activity
from app.models.garmin import GarminConnection
from app.models.health import HealthMetric, HealthSample
from app.models.user import User
from app.services import garmin
from app.services.sante import health_page

ROOT = pathlib.Path(__file__).resolve().parents[1]
MIG = ROOT / "alembic" / "versions" / "w7f8a9b0c1d2_add_garmin.py"


def jwt(client_id: str = "GARMIN_CONNECT_MOBILE_ANDROID_DI_2025Q2", ttl: timedelta = timedelta(hours=20),
        tag: str = "") -> str:
    enc = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()  # noqa: E731
    exp = int((datetime.now(timezone.utc) + ttl).timestamp())
    return f"{enc({'alg': 'RS256'})}.{enc({'exp': exp, 'client_id': client_id, 'tag': tag})}.sig"


def _ms(dt: datetime) -> int:
    return int(dt.replace(tzinfo=timezone.utc).timestamp() * 1000)


def night(day: date, levels: bool = True, readings: bool = True, score: int | None = 82) -> dict:
    """dailySleepData of the night ending on `day`: 23:00 → 06:30 local, the
    athlete 2 h ahead of GMT, with its raw readings every 5 min (heart rate 48
    then 50, HRV 60/64, respiration 14) from 22:30 to 07:00 (the 3 readings
    before 23:00 and the 6 after 06:30 fall outside the main window), and the
    brand fields PaceForge must never read."""
    start, end = datetime.combine(day - timedelta(days=1), datetime.min.time()) + timedelta(hours=23), \
        datetime.combine(day, datetime.min.time()) + timedelta(hours=6, minutes=30)
    gmt = timedelta(hours=2)
    dto = {"calendarDate": day.isoformat(), "sleepTimeSeconds": 25200,
           "sleepStartTimestampLocal": _ms(start), "sleepEndTimestampLocal": _ms(end),
           "sleepStartTimestampGMT": _ms(start - gmt), "sleepEndTimestampGMT": _ms(end - gmt),
           "deepSleepSeconds": 5400, "lightSleepSeconds": 13800, "remSleepSeconds": 6000,
           "awakeSleepSeconds": 1800, "averageRespirationValue": 15.0}
    if score is not None:
        dto["sleepScores"] = {"totalDuration": {"qualifierKey": "EXCELLENT"},
                              "overall": {"value": score, "qualifierKey": "GOOD" if score else None}}
    out = {"dailySleepDTO": dto, "avgOvernightHrv": 99.0, "restingHeartRate": 44, "hrvStatus": "BALANCED",
           "sleepBodyBattery": [{"value": 80}]}
    if readings:
        t0 = start - gmt - timedelta(minutes=30)
        ts = [t0 + timedelta(minutes=5 * i) for i in range(103)]  # 22:30 → 07:00 local
        out["sleepHeartRate"] = [{"value": 48 + 2 * (i % 2), "startGMT": _ms(t)} for i, t in enumerate(ts)]
        out["hrvData"] = [{"value": 60.0 + 4 * (i % 2), "startGMT": _ms(t)} for i, t in enumerate(ts)]
        out["wellnessEpochRespirationDataDTOList"] = [{"startTimeGMT": _ms(t), "respirationValue": 14.0} for t in ts]
    if levels:
        g = start - gmt
        fmt = lambda t: t.strftime("%Y-%m-%dT%H:%M:%S.0")  # noqa: E731
        steps = [(0, 1, 90), (1, 0, 90), (2, 1, 120), (3, 3, 30), (4, 2, 120)]  # (i, level, minutes)
        t, out["sleepLevels"] = g, []
        for _, level, minutes in steps:
            out["sleepLevels"].append({"startGMT": fmt(t), "endGMT": fmt(t + timedelta(minutes=minutes)),
                                       "activityLevel": level})
            t += timedelta(minutes=minutes)
    return out


def summary(day: date, steps: int = 12000) -> dict:
    return {"calendarDate": day.isoformat(), "totalSteps": steps, "totalKilocalories": 2650.0,
            "moderateIntensityMinutes": 20, "vigorousIntensityMinutes": 35, "averageStressLevel": 28,
            "restingHeartRate": 45, "bodyBatteryHighestValue": 88, "bodyBatteryLowestValue": 21,
            "bodyBatteryAtWakeTime": 80}


def activity(gid: int, start: datetime, type_key: str = "running", km: float = 10.0) -> dict:
    return {"activityId": gid, "activityName": "Sortie du matin", "startTimeGMT": start.strftime("%Y-%m-%d %H:%M:%S"),
            "startTimeLocal": (start + timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S"),
            "activityType": {"typeKey": type_key}, "distance": km * 1000, "duration": 3000.0,
            "elapsedDuration": 3100.0, "movingDuration": 2950.0, "elevationGain": 120.0,
            "averageSpeed": 3.33, "maxSpeed": 4.8, "averageHR": 148.0, "maxHR": 171.0,
            "averageRunningCadenceInStepsPerMinute": 172.0, "calories": 690.0}


class FakeGarmin:
    """connectapi + diauth, as the app sees them."""

    def __init__(self, today: date):
        self.today = today
        self.calls: list[tuple[str, dict]] = []
        self.token_calls: list[dict] = []
        self.token_auth: list[str] = []
        self.api_status: int | None = None
        self.reject_tokens: set[str] = set()
        self.refresh_status = 200
        self.timeouts: set[str] = set()  # path prefixes whose call times out
        self.days_before: date | None = None  # nights and summaries of days before this one time out
        self.statuses: dict[str, int] = {}  # path prefix → HTTP status
        self.n = 0
        midnight = datetime.combine(today, datetime.min.time())
        self.activities = [activity(9001, midnight - timedelta(days=2) + timedelta(hours=6)),
                           activity(9002, midnight - timedelta(days=5) + timedelta(hours=7), "trail_running", 21.0)]

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = urlsplit(str(request.url))
        if url.netloc.startswith("diauth."):
            form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
            self.token_calls.append(form)
            self.token_auth.append(request.headers.get("authorization", ""))
            if self.refresh_status != 200:
                return httpx.Response(self.refresh_status, json={"error": "invalid_grant"})
            self.n += 1
            return httpx.Response(200, json={"access_token": jwt(tag=f"at-{self.n}"),
                                             "refresh_token": f"rt-{self.n + 1}", "expires_in": 72000})
        assert url.netloc == "connectapi.garmin.com", url.netloc
        params = {k: v[0] for k, v in parse_qs(url.query).items()}
        self.calls.append((url.path, params))
        token = request.headers.get("authorization", "").removeprefix("Bearer ")
        if token in self.reject_tokens:
            return httpx.Response(401, json={"message": "Token is not active"})
        if self.api_status:
            return httpx.Response(self.api_status, json={})
        assert request.headers["x-garmin-client-platform"] == "Android"
        path, t = url.path, self.today
        if any(path.startswith(p) for p in self.timeouts):
            raise httpx.ReadTimeout("timed out", request=request)
        for prefix, status in self.statuses.items():
            if path.startswith(prefix):
                return httpx.Response(status, json={})
        if path == "/userprofile-service/socialProfile":
            return httpx.Response(200, json={"displayName": "runner 42", "fullName": "Test Runner"})
        if path == f"/metrics-service/metrics/trainingreadiness/{t}":
            return httpx.Response(200, json=[
                {"calendarDate": str(t), "timestampLocal": f"{t}T06:40:00.0", "score": 41, "level": "LOW",
                 "recoveryTime": 900},
                {"calendarDate": str(t), "timestampLocal": f"{t}T09:10:00.0", "score": 72, "level": "MODERATE",
                 "recoveryTime": 1500}])
        if path == f"/metrics-service/metrics/trainingstatus/aggregated/{t}":
            return httpx.Response(200, json={"mostRecentTrainingStatus": {"latestTrainingStatusData": {
                "3442975999": {"calendarDate": str(t), "primaryTrainingDevice": True, "acuteTrainingLoadDTO": {
                    "dailyTrainingLoadAcute": 812, "dailyTrainingLoadChronic": 520,
                    "dailyAcuteChronicWorkloadRatio": 1.6, "acwrStatus": "VERY_HIGH"}}}}})
        if path.startswith("/hrv-service/hrv/daily/"):
            a, b = (date.fromisoformat(x) for x in path.rsplit("/", 2)[1:])
            days = [a + timedelta(days=i) for i in range((b - a).days + 1)]
            return httpx.Response(200, json={"hrvSummaries": [
                {"calendarDate": str(d), "lastNightAvg": 60 + d.day % 7, "weeklyAvg": 62,
                 "baseline": {"balancedLow": 55, "balancedUpper": 70, "markerValue": 0.4}} for d in days]})
        if path.startswith("/metrics-service/metrics/maxmet/daily/"):
            return httpx.Response(200, json=[{"generic": {"calendarDate": str(t - timedelta(days=3)),
                                                          "vo2MaxPreciseValue": 54.3, "vo2MaxValue": 54}}])
        if path == "/activitylist-service/activities/search/activities":
            return httpx.Response(200, json=self.activities if params.get("start") == "0" else [])
        if path == "/metrics-service/metrics/racepredictions/latest/runner%2042":
            return httpx.Response(200, json={"calendarDate": str(t), "time5K": 1190, "time10K": 2480,
                                             "timeHalfMarathon": 5520, "timeMarathon": 11800})
        day = params.get("date") or params.get("calendarDate")
        if self.days_before and day and date.fromisoformat(day) < self.days_before:
            raise httpx.ReadTimeout("timed out", request=request)
        if path == "/wellness-service/wellness/dailySleepData/runner%2042":
            d = date.fromisoformat(params["date"])
            if d > t:  # a day that hasn't come yet: empty, as Garmin answers
                return httpx.Response(200, json={"dailySleepDTO": {}})
            if (t - d).days % 10 == 9:
                return httpx.Response(200, json={"dailySleepDTO": {}})
            return httpx.Response(200, json=night(d, score=0 if (t - d).days == 1 else 82))  # yesterday: not scored
        if path == "/usersummary-service/usersummary/daily/runner%2042":
            d = date.fromisoformat(params["calendarDate"])
            if d > t:  # a day that hasn't come yet: Garmin's dated skeleton, every value null
                return httpx.Response(200, json={"calendarDate": str(d), "totalSteps": None,
                                                 "averageStressLevel": None, "bodyBatteryHighestValue": None})
            return httpx.Response(200, json=summary(d))
        return httpx.Response(404, json={})

    def paths(self, prefix: str) -> list:
        return [(p, q) for p, q in self.calls if p.startswith(prefix)]


@pytest.fixture
def fake(monkeypatch) -> FakeGarmin:
    f = FakeGarmin(datetime.now(timezone.utc).date())
    monkeypatch.setattr(garmin, "_transport", httpx.MockTransport(f.handler))
    monkeypatch.setattr(garmin, "CALL_DELAY_S", 0)
    return f


@pytest.fixture
def no_commit(db_session: AsyncSession, monkeypatch):
    """The service commits (tokens must survive a later rollback): in tests,
    flush instead, so each test's rollback still cleans up."""
    monkeypatch.setattr(db_session, "commit", db_session.flush)


@pytest.fixture
async def as_user(client: AsyncClient, test_user: User, no_commit):
    client._transport.app.dependency_overrides[get_current_user] = lambda: test_user  # type: ignore[attr-defined]
    async with AsyncClient(transport=client._transport, base_url="https://test") as c:  # Secure session cookie
        yield c


async def _link(db: AsyncSession, user: User, access: str | None = None, refresh="rt-1",
                ttl=timedelta(hours=20), **extra) -> GarminConnection:
    access = access or jwt(tag="at-0", ttl=ttl)
    conn = GarminConnection(user_id=user.id, domain="garmin.com", di_client_id="GARMIN_CONNECT_MOBILE_ANDROID_DI",
                            access_token_encrypted=encrypt_secret(access),
                            refresh_token_encrypted=encrypt_secret(refresh),
                            expires_at=datetime.now(timezone.utc) + ttl, **extra)
    db.add(conn)
    await db.flush()
    return conn


# ── parsers ─────────────────────────────────────────────────────────────────

def test_parse_sleep_keeps_garmin_stage_times_on_the_local_clock():
    d = date(2026, 10, 5)
    n = garmin.parse_sleep(night(d))
    assert n["day"] == d and n["start"] == datetime(2026, 10, 4, 23, 0) and n["end"] == datetime(2026, 10, 5, 6, 30)
    assert n["asleep"] == 420 and n["tz"] == 120
    assert n["hr"][0] == (datetime(2026, 10, 4, 22, 30), 48.0) and len(n["hrv"]) == 103 and len(n["resp"]) == 103
    assert not {"score", "qualifier", "rhr"} & set(n)  # brand fields are not read
    samples = garmin.night_samples(n)
    assert [(s.kind, s.start.strftime("%H:%M"), s.value) for s in samples] == [
        ("core", "23:00", 90), ("deep", "00:30", 90), ("core", "02:00", 120), ("awake", "04:00", 30),
        ("rem", "04:30", 120)]
    assert all(s.source == "Garmin" for s in samples)


def test_nightly_values_come_from_the_readings_inside_the_main_window():
    d = date(2026, 10, 5)
    rows = {r.metric: r for r in garmin.night_dailies(garmin.parse_sleep(night(d)), True)}
    assert set(rows) == {"sleep", "hrv", "hr_night", "resp_night"}
    # the stage minutes of the main night (the DTO's seconds): shown on Santé, never judged
    assert rows["sleep"].value == 420 and rows["sleep"].details == {
        "main_start": "2026-10-04T23:00", "main_end": "2026-10-05T06:30", "period": 450, "bedtime": "23:00",
        "wake": "06:30", "timeline": True, "tz": 120, "stages": {"deep": 90, "light": 230, "rem": 100, "awake": 30},
        "stages_read": True}
    # 91 readings from 23:00 to 06:30, 46 at 48 bpm / 60 ms and 45 at 50 bpm / 64 ms
    assert rows["hr_night"].value == 49.0 and rows["hr_night"].details == {
        "min": 48.0, "max": 50.0, "n": 91, "method": "points", "nap_day": False}
    assert rows["hrv"].value == 61.9 and rows["hrv"].details == {"n": 91, "tz": 120, "method": "ln_mean_main"}
    assert rows["resp_night"].value == 14.0 and rows["resp_night"].details["method"] == "points"
    # without readings: no HR, no HRV, Garmin's sleep respiration average as context; never avgOvernightHrv
    bare = {r.metric: r for r in garmin.night_dailies(garmin.parse_sleep(night(d, readings=False)), False)}
    assert set(bare) == {"sleep", "resp_night"} and bare["resp_night"].details == {"method": "garmin_summary"}


def test_sleep_without_levels_writes_no_timeline():
    n = garmin.parse_sleep(night(date(2026, 10, 5), levels=False))
    assert garmin.night_samples(n) == []  # minutes laid out as blocks would be an invented timeline
    assert garmin.night_dailies(n, False)[0].details["timeline"] is False


def test_parsers_survive_unknown_shapes():
    for bad in (None, [], {}, "x", 3, {"dailySleepDTO": None}, [{"generic": None}], [None]):
        assert garmin.parse_sleep(bad) is None
        assert garmin.parse_summary(bad) is None
        assert garmin.parse_naps(bad) is None
        assert garmin.activity_fields(bad if isinstance(bad, dict) else {}) is None
    assert garmin.parse_sleep({"dailySleepDTO": {"calendarDate": "2026-10-05", "sleepStartTimestampLocal": 10,
                                                 "sleepEndTimestampLocal": 5}}) is None  # ends before it starts
    odd = night(date(2026, 10, 5))
    odd["sleepHeartRate"] = [None, {"value": "x"}, {"startGMT": 3}, 7]
    assert garmin.parse_sleep(odd)["hr"] == []


def test_activity_fields_in_strava_units():
    f = garmin.activity_fields(activity(77, datetime(2026, 10, 4, 5, 30), "trail_running", 21.1))
    assert f["garmin_activity_id"] == 77 and f["sport_type"] == "TrailRun"
    assert f["start_date"] == datetime(2026, 10, 4, 5, 30, tzinfo=timezone.utc)
    assert f["distance"] == 21100 and f["moving_time"] == 2950 and f["elapsed_time"] == 3100
    assert f["average_cadence"] == 86  # per leg, like Strava
    assert f["raw_data"]["source"] == "garmin"
    assert garmin.activity_fields(activity(78, datetime(2026, 10, 4), "pickleball"))["sport_type"] == "Workout"


def test_build_daily_keeps_plausible_values_only():
    t = date(2026, 10, 6)
    data = {"summaries": {t: {"steps": 0, "kcal": 0}, t - timedelta(days=1): {"steps": 9000, "kcal": 2400,
                                                                               "exercise": 30}},
            "nights": {}, "naps": {}}
    rows = {(r.metric, r.day): r for r in garmin.build_daily(data, t)}
    assert set(rows) == {("steps", t - timedelta(days=1))}  # 0 steps: not worn
    assert garmin.parse_summary(summary(t)) == {"day": t, "steps": 12000, "kcal": 2650.0, "exercise": 55}


# ── tokens ──────────────────────────────────────────────────────────────────

async def test_expiring_token_is_refreshed_and_the_rotated_pair_kept(db_session: AsyncSession, test_user: User,
                                                                     fake, no_commit):
    conn = await _link(db_session, test_user, ttl=timedelta(minutes=5))
    async with garmin.http_client() as client:
        token = await garmin.access_token(db_session, conn, client)
    assert garmin._jwt_claims(token)["tag"] == "at-1"
    assert fake.token_calls == [{"grant_type": "refresh_token", "client_id": "GARMIN_CONNECT_MOBILE_ANDROID_DI",
                                 "refresh_token": "rt-1"}]
    assert fake.token_auth[0] == "Basic " + base64.b64encode(b"GARMIN_CONNECT_MOBILE_ANDROID_DI:").decode()
    assert decrypt_secret(conn.refresh_token_encrypted) == "rt-2"
    assert conn.di_client_id == "GARMIN_CONNECT_MOBILE_ANDROID_DI_2025Q2"  # read from the new JWT
    assert garmin._as_utc(conn.expires_at) > datetime.now(timezone.utc) + timedelta(hours=19)


async def test_rejected_token_refreshes_once_then_asks_to_reconnect(db_session: AsyncSession, test_user: User,
                                                                    fake, no_commit):
    old = jwt(tag="at-0")
    conn = await _link(db_session, test_user, access=old)
    fake.reject_tokens.add(old)
    outcome = await garmin.run_sync(db_session, conn)
    assert outcome["ok"], outcome
    assert len(fake.token_calls) == 1

    conn2_token = decrypt_secret(conn.access_token_encrypted)
    fake.reject_tokens.add(conn2_token)
    fake.refresh_status = 400
    conn.last_sync_at = None
    outcome = await garmin.run_sync(db_session, conn)
    assert outcome == {"ok": False, "error": "La connexion à Garmin a expiré ou a été retirée : reconnecte-toi."}
    assert conn.needs_reauth
    assert await garmin.run_sync(db_session, conn) is None  # no more syncs until reconnected


# ── sync ────────────────────────────────────────────────────────────────────

async def test_first_sync_backfills_health_and_sessions_then_last_week(db_session: AsyncSession, test_user: User,
                                                                       fake, no_commit):
    t = fake.today
    # Strava already has the second session (started 40 s apart, 2 % longer)
    strava = Activity(user_id=test_user.id, strava_activity_id=555, sport_type="TrailRun", name="Strava trail",
                      start_date=datetime.combine(t, datetime.min.time(), tzinfo=timezone.utc)
                      - timedelta(days=5) + timedelta(hours=7, seconds=40),
                      distance=21400, moving_time=7000, elapsed_time=7200, raw_data={})
    db_session.add(strava)
    conn = await _link(db_session, test_user)
    outcome = await garmin.run_sync(db_session, conn)
    assert outcome["ok"], outcome
    assert conn.display_name == "runner 42" and conn.last_sync_at and not conn.last_error
    assert len(fake.paths("/wellness-service/wellness/dailySleepData/")) == 60
    assert len(fake.paths("/usersummary-service/usersummary/daily/")) == 60
    # no brand value is asked for: HRV status and averages, readiness, load, VO2 max, race predictions
    assert not fake.paths("/hrv-service/") and not fake.paths("/metrics-service/")
    act_q = fake.paths("/activitylist-service/")[0][1]
    assert act_q["startDate"] == str(t - timedelta(days=179)) and act_q["start"] == "0"

    rows = (await db_session.execute(select(HealthMetric).where(HealthMetric.user_id == test_user.id))).scalars().all()
    by = {}
    for r in rows:
        by.setdefault(r.metric, {})[r.date] = r
    assert set(by) == {"sleep", "hrv", "hr_night", "resp_night", "steps"}
    assert by["sleep"][t].value == 420 and by["sleep"][t].source == "Garmin"  # 7 h asleep, the awake half hour out
    assert by["sleep"][t].details["main_start"] == f"{t - timedelta(days=1)}T23:00"
    assert by["sleep"][t].details["wake"] == "06:30" and by["sleep"][t].details["timeline"] is True
    assert len(by["sleep"]) == 54  # one night in ten had no data
    assert by["hrv"][t].value == 61.9 and by["hrv"][t].details["method"] == "ln_mean_main"
    assert by["hr_night"][t].value == 49.0 and by["resp_night"][t].value == 14.0
    assert by["steps"][t].details == {"kcal": 2650.0, "exercise": 55}
    timeline = (await db_session.execute(select(func.count(HealthSample.id)).where(
        HealthSample.user_id == test_user.id, HealthSample.metric == "sleep"))).scalar()
    assert timeline == 54 * 5  # Garmin's real intervals, the hypnogram's timeline

    acts = (await db_session.execute(select(Activity).where(Activity.user_id == test_user.id)
                                     .order_by(Activity.start_date))).scalars().all()
    assert [(a.strava_activity_id, a.garmin_activity_id, a.sport_type) for a in acts] == [
        (555, 9002, "TrailRun"), (None, 9001, "Run")]
    assert acts[0].name == "Strava trail"  # Strava's row is left as it is
    assert outcome["result"]["activities"] == {"inserted": 1, "linked": 1, "updated": 0, "merged": 0}

    # then the last 7 days; the sessions already there are not duplicated, nor the intervals
    fake.calls.clear()
    conn.last_sync_at = datetime.now(timezone.utc) - timedelta(hours=7)
    outcome = await garmin.run_sync(db_session, conn)
    assert outcome["ok"] and len(fake.paths("/wellness-service/wellness/dailySleepData/")) == 7
    assert fake.paths("/activitylist-service/")[0][1]["startDate"] == str(t - timedelta(days=6))
    assert not fake.paths("/userprofile-service/")  # the display name is kept
    assert outcome["result"]["activities"] == {"inserted": 0, "linked": 0, "updated": 1, "merged": 0}
    n = (await db_session.execute(select(func.count(Activity.id)).where(Activity.user_id == test_user.id))).scalar()
    assert n == 2
    assert (await db_session.execute(select(func.count(HealthSample.id)).where(
        HealthSample.user_id == test_user.id, HealthSample.metric == "sleep"))).scalar() == timeline


async def test_rate_limit_and_failures_are_shown_in_plain_french(as_user: AsyncClient, db_session: AsyncSession,
                                                                 test_user: User, fake):
    conn = await _link(db_session, test_user)
    fake.api_status = 429
    outcome = await garmin.run_sync(db_session, conn)
    assert outcome == {"ok": False,
                       "error": "Garmin limite les demandes pour l'instant : la synchro reprendra plus tard."}
    assert conn.last_sync_at is None and not conn.needs_reauth
    fake.api_status = 503
    outcome = await garmin.run_sync(db_session, conn)
    assert outcome == {"ok": False, "error": "Garmin n'a renvoyé aucune donnée lisible."}
    assert "Dernière synchro échouée" in (await as_user.get("/settings")).text


async def test_a_timeout_or_a_5xx_loses_that_call_not_the_night(db_session: AsyncSession, test_user: User,
                                                                 fake, no_commit):
    t = fake.today
    fake.statuses = {"/usersummary-service/": 502}
    conn = await _link(db_session, test_user)
    outcome = await garmin.run_sync(db_session, conn)
    assert outcome["ok"], outcome
    assert outcome["result"]["activities"]["inserted"] == 2  # the sync went on to the end
    rows = (await db_session.execute(select(HealthMetric).where(HealthMetric.user_id == test_user.id))).scalars().all()
    by = {(m.metric, m.date): m.value for m in rows}
    assert by[("sleep", t)] == 420 and by[("hr_night", t)] == 49.0
    assert not any(m == "steps" for m, _ in by)


async def test_a_first_sync_cut_short_is_redone_in_full(db_session: AsyncSession, test_user: User,
                                                         fake, no_commit):
    """Garmin stops answering (3 timeouts in a row) halfway through the history: what came is
    kept, the athlete is told, and the next sync asks for the whole history again."""
    t = fake.today
    fake.days_before = t - timedelta(days=10)
    conn = await _link(db_session, test_user)
    outcome = await garmin.run_sync(db_session, conn)
    assert outcome["ok"], outcome
    assert not fake.paths("/activitylist-service/")
    assert conn.last_sync_at and conn.last_error == garmin.PARTIAL
    rows = (await db_session.execute(select(HealthMetric).where(HealthMetric.user_id == test_user.id))).scalars().all()
    assert {(m.metric, m.date): m.value for m in rows}[("sleep", t)] == 420  # the nights stay

    fake.days_before, fake.calls = None, []
    outcome = await garmin.run_sync(db_session, conn)
    assert outcome["ok"] and len(fake.paths("/wellness-service/wellness/dailySleepData/")) == 60
    assert fake.paths("/activitylist-service/")[0][1]["startDate"] == str(t - timedelta(days=179))
    assert conn.last_error is None and outcome["result"]["activities"]["inserted"] == 2


async def test_a_claimed_sync_is_not_run_twice(db_session: AsyncSession, test_user: User, fake, no_commit):
    conn = await _link(db_session, test_user, sync_claimed_at=datetime.now(timezone.utc))
    assert await garmin.run_sync(db_session, conn) is None and not fake.calls
    conn.sync_claimed_at = datetime.now(timezone.utc) - timedelta(hours=1)  # a crashed worker's claim
    await db_session.flush()
    assert (await garmin.run_sync(db_session, conn))["ok"]


async def test_a_sync_that_fails_at_once_lets_go_of_the_link(db_session: AsyncSession, test_user: User, fake,
                                                              no_commit, monkeypatch):
    """Garmin down before the sync touched the database: the claim is released, so « Synchroniser
    maintenant » pressed again is a new try, not « déjà en cours » for the claim's 15 min."""
    conn = await _link(db_session, test_user)
    await db_session.refresh(conn)  # as read for a request: sync_claimed_at loaded (None)

    async def down(db, c):
        raise httpx.ConnectError("down")
    monkeypatch.setattr(garmin, "sync_connection", down)
    assert (await garmin.run_sync(db_session, conn))["ok"] is False
    claimed = (await db_session.execute(
        select(GarminConnection.sync_claimed_at).where(GarminConnection.id == conn.id))).scalar_one()
    assert claimed is None
    assert (await garmin.run_sync(db_session, conn)) is not None  # tried again


async def test_garmin_hrv_is_on_the_watch_scale(db_session: AsyncSession, test_user: User, fake, no_commit):
    from app.services.health import RMSSD_SOURCES

    assert "Garmin" in RMSSD_SOURCES
    conn = await _link(db_session, test_user)
    db_session.add(HealthSample(user_id=test_user.id, metric="hrv", kind="", source="Apple", value=35,
                                start_at=datetime.combine(fake.today, datetime.min.time()) + timedelta(hours=3),
                                end_at=datetime.combine(fake.today, datetime.min.time()) + timedelta(hours=3)))
    await db_session.flush()
    assert (await garmin.run_sync(db_session, conn))["ok"]
    hrv = (await db_session.execute(select(HealthMetric).where(
        HealthMetric.user_id == test_user.id, HealthMetric.metric == "hrv",
        HealthMetric.date == fake.today))).scalar_one()
    assert hrv.value == 61.9 and hrv.source == "Garmin"  # not mixed with Apple's SDNN, nor Garmin's own average


# ── login ───────────────────────────────────────────────────────────────────

class FakeLoginClient:
    def __init__(self, mfa=False, error: Exception | None = None, good_code="123456"):
        self.mfa, self.error, self.good_code = mfa, error, good_code
        self.di_token = self.di_refresh_token = self.di_client_id = None
        self.seen: list = []

    def _grant(self):
        self.di_token = jwt(tag="login")
        self.di_refresh_token = "rt-login"
        self.di_client_id = "GARMIN_CONNECT_MOBILE_ANDROID_DI_2025Q2"

    def login(self, email, password, prompt_mfa=None, return_on_mfa=False):
        self.seen.append(("login", email, password, return_on_mfa))
        if self.error:
            raise self.error
        if self.mfa:
            return "needs_mfa", None
        self._grant()
        return None, None

    def resume_login(self, _state, code):
        self.seen.append(("code", code))
        from garminconnect.exceptions import GarminConnectAuthenticationError

        if code != self.good_code:
            raise GarminConnectAuthenticationError("MFA verification failed")
        self._grant()
        return None, None


@pytest.fixture
def login(monkeypatch, db_session: AsyncSession, no_commit):
    """A fake login library, an in-process store, and the login's own DB
    session pointed at the test's."""
    holder = {"client": FakeLoginClient()}
    monkeypatch.setattr(garmin, "_new_login_client", lambda domain: holder.setdefault("domain", domain)
                        and holder["client"])
    monkeypatch.setattr(garmin, "_store", garmin.MemoryStore())
    monkeypatch.setattr(garmin, "_CODE_POLL_S", 0.01)

    @asynccontextmanager
    async def session():
        yield db_session

    import app.database

    monkeypatch.setattr(app.database, "async_session_factory", session)
    return holder


async def _settle():
    for _ in range(200):
        if not garmin._pending:
            return
        await asyncio.sleep(0.01)
    raise AssertionError("login still running")


async def _until(ticket, user_id, state):
    for _ in range(200):
        s = await garmin.login_state(ticket, user_id)
        if s and s["state"] == state:
            return s
        await asyncio.sleep(0.01)
    raise AssertionError(f"never reached {state}: {s}")


async def test_login_stores_the_tokens_never_the_password(db_session: AsyncSession, test_user: User, login):
    ticket = await garmin.start_login(test_user.id, " me@x.fr ", "s3cret")
    await _settle()
    assert (await garmin.login_state(ticket, test_user.id))["state"] == "done"
    assert await garmin.login_state(ticket, test_user.id + 1) is None  # another athlete sees nothing
    assert login["domain"] == "garmin.com" and login["client"].seen == [("login", "me@x.fr", "s3cret", True)]
    conn = await garmin.connection_for(db_session, test_user.id)
    assert conn.domain == "garmin.com" and decrypt_secret(conn.refresh_token_encrypted) == "rt-login"
    assert garmin._jwt_claims(decrypt_secret(conn.access_token_encrypted))["tag"] == "login"
    stored = json.dumps([str(getattr(conn, c.name)) for c in GarminConnection.__table__.columns])
    assert "s3cret" not in stored and "me@x.fr" not in stored


async def test_login_with_mfa_retries_a_wrong_code(db_session: AsyncSession, test_user: User, login):
    login["client"] = FakeLoginClient(mfa=True)
    ticket = await garmin.start_login(test_user.id, "me@x.fr", "pw")
    await _until(ticket, test_user.id, "mfa")
    assert await garmin.submit_code(ticket, test_user.id, "000000")
    s = await _until(ticket, test_user.id, "mfa")
    assert s["message"] == "Code refusé : vérifie-le et réessaie."
    assert not await garmin.submit_code(ticket, test_user.id + 1, "123456")
    assert await garmin.submit_code(ticket, test_user.id, " 123456 ")
    await _settle()
    assert (await garmin.login_state(ticket, test_user.id))["state"] == "done"
    assert login["client"].seen[1:] == [("code", "000000"), ("code", "123456")]
    assert await garmin.connection_for(db_session, test_user.id) is not None


async def test_login_errors_in_plain_french(test_user: User, login):
    from garminconnect.exceptions import (
        GarminConnectAuthenticationError,
        GarminConnectConnectionError,
        GarminConnectTooManyRequestsError,
    )

    for error, message in ((GarminConnectAuthenticationError("401"), "Email ou mot de passe Garmin incorrect."),
                           (GarminConnectTooManyRequestsError("429"),
                            "Garmin bloque les connexions pour l'instant. Réessaie dans une heure."),
                           (GarminConnectConnectionError("boom"),
                            "Garmin ne répond pas pour l'instant. Réessaie dans quelques minutes.")):
        login["client"] = FakeLoginClient(error=error)
        ticket = await garmin.start_login(test_user.id, "me@x.fr", "pw")
        await _settle()
        assert await garmin.login_state(ticket, test_user.id) == {"user_id": test_user.id, "state": "error",
                                                                 "message": message}


# ── routes ──────────────────────────────────────────────────────────────────

async def test_routes_require_login(client: AsyncClient):
    for method, path in (("POST", "/garmin/connect"), ("GET", "/garmin/login"), ("POST", "/garmin/mfa"),
                         ("POST", "/sante/sync"), ("POST", "/settings/garmin/disconnect")):
        r = await client.request(method, path)
        assert r.status_code == 303 and r.headers["location"] == "/auth/login", path


async def test_connect_page_flow_with_mfa(as_user: AsyncClient, db_session: AsyncSession, test_user: User, login):
    page = (await as_user.get("/settings")).text
    assert 'id="garmin"' in page and 'hx-post="/garmin/connect"' in page and "PaceForge ne le garde pas" in page
    assert "/garmin/authorize" not in page and 'name="region"' not in page  # one login, one server

    r = await as_user.post("/garmin/connect", data={"email": "", "password": ""})
    assert "Indique ton email et ton mot de passe Garmin." in r.text

    login["client"] = FakeLoginClient(mfa=True)
    r = await as_user.post("/garmin/connect", data={"email": "me@x.fr", "password": "pw"})
    assert 'hx-get="/garmin/login"' in r.text and "Connexion à Garmin" in r.text
    for _ in range(200):
        r = await as_user.get("/garmin/login")
        if 'hx-post="/garmin/mfa"' in r.text:
            break
        await asyncio.sleep(0.01)
    assert 'name="code"' in r.text

    r = await as_user.post("/garmin/mfa", data={"code": "abc"})
    assert "Le code est fait de chiffres." in r.text
    r = await as_user.post("/garmin/mfa", data={"code": "123456"})
    assert "Vérification du code…" in r.text
    await _settle()
    r = await as_user.get("/garmin/login")
    assert r.status_code == 204 and r.headers["HX-Redirect"] == "/settings#garmin"
    page = (await as_user.get("/settings")).text
    assert "Garmin connecté. Tes 60 derniers jours" in page and "Première synchro en cours" in page
    r = await as_user.get("/garmin/login")  # the ticket is gone once used
    assert "La connexion à Garmin a expiré. Recommence." in r.text


async def test_settings_and_sante_sync_and_disconnect(as_user: AsyncClient, db_session: AsyncSession,
                                                      test_user: User, fake):
    await _link(db_session, test_user)
    sante = (await as_user.get("/sante")).text
    assert "Pas encore de données de Garmin" in sante and "Connecter COROS" not in sante
    # Santé: no sync button, no link status (Réglages': owner, 2026-10-08)
    assert "/sante/sync\"" not in sante and "synchro" not in sante.lower()
    settings_page = (await as_user.get("/settings")).text
    block = settings_page.split('id="garmin"')[1]
    assert "Première synchro en cours" in block and "Synchroniser maintenant" in block
    assert 'hx-post="/settings/garmin/sync" hx-target="#garmin-sync-state"' in block

    r = await as_user.post("/settings/garmin/sync")
    assert r.status_code == 200 and "Synchro faite : tes nouvelles données sont dans Santé." in r.text
    sante = (await as_user.get("/sante")).text
    assert 'id="sommeil"' in sante and 'data-viz-key="sommeil-14"' in sante  # Sommeil: the nights' bars
    for brand in ("Charge <small>", "Training Readiness", "Body Battery", "forte hausse"):
        assert brand not in sante, brand
    assert "Dernière synchro" in (await as_user.get("/settings")).text

    n = (await db_session.execute(select(func.count(HealthSample.id)).where(HealthSample.user_id == test_user.id))).scalar()
    r = await as_user.post("/settings/garmin/disconnect")
    assert r.status_code == 303 and r.headers["location"] == "/settings#garmin"
    assert (await db_session.execute(select(GarminConnection))).scalar_one_or_none() is None
    n2 = (await db_session.execute(select(func.count(HealthSample.id)).where(HealthSample.user_id == test_user.id))).scalar()
    assert n == n2 > 0  # imported data stays
    page = (await as_user.get("/settings")).text
    assert "Garmin déconnecté" in page and 'hx-post="/garmin/connect"' in page


async def test_reglages_sync_says_what_went_wrong(as_user: AsyncClient, db_session: AsyncSession,
                                                  test_user: User, fake):
    conn = await _link(db_session, test_user)
    fake.api_status = 503
    r = await as_user.post("/settings/garmin/sync")
    assert "HX-Refresh" not in r.headers
    assert "Dernière synchro échouée : Garmin n&#39;a renvoyé aucune donnée lisible." in r.text
    fake.api_status = None
    conn.sync_claimed_at = datetime.now(timezone.utc)  # another worker is on it
    await db_session.flush()
    r = await as_user.post("/settings/garmin/sync")
    assert "Une synchro est déjà en cours" in r.text
    # a Santé page left open since v4: its old button reloads it, harmlessly
    r = await as_user.post("/sante/sync")
    assert r.headers.get("HX-Refresh") == "true"


async def test_sante_offers_both_watches_when_none_is_linked(as_user: AsyncClient):
    page = (await as_user.get("/sante")).text
    assert 'href="/settings#coros"' in page and 'href="/settings#garmin"' in page


# ── migration ───────────────────────────────────────────────────────────────

def test_migration_is_the_head_and_round_trips():
    from alembic.config import Config
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from alembic.script import ScriptDirectory

    spec = importlib.util.spec_from_file_location("mig_garmin", MIG)
    mig = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mig)
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    script = ScriptDirectory.from_config(cfg)
    assert mig.revision in {r.revision for r in script.walk_revisions()}  # in the chain (COROS sessions came after)

    eng = sa.create_engine("sqlite://")
    with eng.begin() as c:
        c.execute(sa.text("CREATE TABLE users (id INTEGER PRIMARY KEY, email VARCHAR(255))"))
        c.execute(sa.text("INSERT INTO users (id, email) VALUES (1, 'a@b.c')"))
        c.execute(sa.text("CREATE TABLE activities (id INTEGER PRIMARY KEY, strava_activity_id BIGINT NOT NULL, "
                          "name VARCHAR(255))"))
        c.execute(sa.text("INSERT INTO activities (id, strava_activity_id, name) VALUES (1, 11, 's')"))
        with Operations.context(MigrationContext.configure(c)):
            mig.upgrade()
        insp = sa.inspect(c)
        assert "garmin_connections" in insp.get_table_names()
        assert {"garmin_activity_id"} <= {col["name"] for col in insp.get_columns("activities")}
        c.execute(sa.text("INSERT INTO activities (id, strava_activity_id, garmin_activity_id, name) "
                          "VALUES (2, NULL, 99, 'g')"))
        with pytest.raises(sa.exc.IntegrityError):
            c.execute(sa.text("INSERT INTO activities (id, garmin_activity_id, name) VALUES (3, 99, 'dup')"))
        row = ("INSERT INTO garmin_connections (user_id, domain, di_client_id, access_token_encrypted, connected_at) "
               "VALUES (1, 'garmin.com', 'c', x'00', '2026-10-06 08:00:00')")
        c.execute(sa.text(row))
        with pytest.raises(sa.exc.IntegrityError):
            c.execute(sa.text(row))  # one link per athlete
        with Operations.context(MigrationContext.configure(c)):
            mig.downgrade()
        insp = sa.inspect(c)
        assert "garmin_connections" not in insp.get_table_names()
        assert c.execute(sa.text("SELECT id FROM activities")).scalars().all() == [1]  # Garmin-only rows go


async def test_the_nights_reach_the_athletes_today_ahead_of_utc(db_session: AsyncSession, test_user: User,
                                                                fake, no_commit):
    conn = await _link(db_session, test_user, last_sync_at=datetime.now(timezone.utc) - timedelta(hours=3))
    db_session.add(HealthMetric(user_id=test_user.id, date=fake.today, metric="steps", value=1,
                                source="Garmin", n_samples=1))
    await db_session.flush()
    assert (await garmin.run_sync(db_session, conn))["ok"]
    days = [q["date"] for _, q in fake.paths("/wellness-service/wellness/dailySleepData/")]
    assert days[0] == str(fake.today + timedelta(days=1)) and len(days) == 7
    # the empty day ahead doesn't move « today »: the page stays on the UTC day
    nights = (await db_session.execute(select(HealthMetric.date).where(
        HealthMetric.user_id == test_user.id, HealthMetric.metric == "sleep"))).scalars().all()
    assert max(nights) == fake.today
    noon = datetime(fake.today.year, fake.today.month, fake.today.day, 12, tzinfo=timezone.utc)
    assert (await health_page(db_session, test_user.id, now=noon))["today"] == fake.today


def test_garmin_naps_on_the_local_clock():
    data = {"dailySleepDTO": {"calendarDate": "2026-10-06", "sleepStartTimestampGMT": 1791237600000,
                              "sleepStartTimestampLocal": 1791237600000 + 2 * 3600_000},
            "dailyNapDTOS": [{"napTimeSec": 1500, "napStartTimestampGMT": "2026-10-06T12:10:00.0",
                              "napEndTimestampGMT": "2026-10-06T12:38:00.0", "calendarDate": "2026-10-06"}]}
    day, nap = garmin.parse_naps(data)
    assert day == date(2026, 10, 6) and nap["asleep"] == 25 and nap["period"] == 28
    assert nap["windows"] == [(datetime(2026, 10, 6, 14, 10), datetime(2026, 10, 6, 14, 38))]  # 14:10 local
    [row] = garmin.build_daily({"summaries": {}, "nights": {}, "naps": {day: nap}}, day)
    assert row.details == {"period": 28, "windows": [["2026-10-06T14:10", "2026-10-06T14:38"]]}
    assert garmin.parse_naps({"dailySleepDTO": {"calendarDate": "2026-10-06", "napTimeSeconds": 1200}})[1][
        "asleep"] == 20
    assert garmin.parse_naps({"dailySleepDTO": {"calendarDate": "2026-10-06"}}) is None


async def test_old_format_nights_beyond_the_backfill_never_ask_for_60_days_again(db_session: AsyncSession,
                                                                                  test_user: User, fake, no_commit):
    """R2: a night stored before 2026-10 (no main window) 100 days ago, and none
    since (the watch no longer worn at night): the re-read cannot rewrite it,
    so it never asks for the 60 days; one inside the window asks once."""
    from app.services.health import nights_upgraded

    t = fake.today
    old = {"bedtime": "23:00", "wake": "07:00"}
    db_session.add(HealthMetric(user_id=test_user.id, date=t - timedelta(days=100), metric="sleep", value=450,
                                details=old, source="Garmin", n_samples=1))
    db_session.add(HealthMetric(user_id=test_user.id, date=t - timedelta(days=1), metric="steps", value=9000,
                                details={}, source="Garmin", n_samples=1))
    await db_session.flush()
    assert await nights_upgraded(db_session, test_user.id, "Garmin", today=t, days=60)
    conn = await _link(db_session, test_user, last_sync_at=datetime.now(timezone.utc) - timedelta(hours=2))
    fake.statuses = {"/wellness-service/wellness/dailySleepData/": 204}
    for _ in range(3):
        fake.calls.clear()
        assert (await garmin.run_sync(db_session, conn))["ok"]
        assert len(fake.paths("/wellness-service/wellness/dailySleepData/")) == 7
    # an old-format night inside the window: read again (once it is rewritten, never again)
    db_session.add(HealthMetric(user_id=test_user.id, date=t - timedelta(days=20), metric="sleep", value=450,
                                details=old, source="Garmin", n_samples=1))
    await db_session.flush()
    assert not await nights_upgraded(db_session, test_user.id, "Garmin", today=t, days=60)
    db_session.add(HealthMetric(user_id=test_user.id, date=t - timedelta(days=2), metric="sleep", value=450,
                                details={"main_start": f"{t - timedelta(days=3)}T23:00"}, source="Garmin",
                                n_samples=1))
    await db_session.flush()
    assert await nights_upgraded(db_session, test_user.id, "Garmin", today=t, days=60)


def test_stage_minutes_from_the_levels_when_the_dto_has_none():
    """A DTO without its stage seconds: the minutes summed from the real sleepLevels inside the main window; neither:
    no stages (a watch that does not track them), still marked read."""
    d = date(2026, 10, 5)
    raw = night(d)
    for k in ("deepSleepSeconds", "lightSleepSeconds", "remSleepSeconds", "awakeSleepSeconds"):
        raw["dailySleepDTO"].pop(k)
    n = garmin.parse_sleep(raw)
    assert garmin.night_stages(n) == {"deep": 90, "light": 210, "rem": 120, "awake": 30}
    bare = garmin.parse_sleep({**raw, "sleepLevels": []})
    [sleep] = [r for r in garmin.night_dailies(bare, False) if r.metric == "sleep"]
    assert "stages" not in sleep.details and sleep.details["stages_read"]


async def test_garmin_nights_written_before_the_stages_are_read_again_once(db_session: AsyncSession,
                                                                           test_user: User, fake, no_commit):
    from app.services.health import STAGES_READ, nights_upgraded

    t = fake.today
    conn = await _link(db_session, test_user, last_sync_at=datetime.now(timezone.utc) - timedelta(hours=2))
    db_session.add(HealthMetric(user_id=test_user.id, date=t - timedelta(days=2), metric="sleep", value=450,
                                details={"main_start": f"{t - timedelta(days=3)}T23:00"}, source="Garmin",
                                n_samples=1))
    db_session.add(HealthMetric(user_id=test_user.id, date=t - timedelta(days=1), metric="steps", value=9000,
                                details={}, source="Garmin", n_samples=1))
    await db_session.flush()
    assert not await nights_upgraded(db_session, test_user.id, "Garmin", today=t, key=STAGES_READ)
    fake.calls.clear()
    assert (await garmin.run_sync(db_session, conn))["ok"]
    assert len(fake.paths("/wellness-service/wellness/dailySleepData/")) == 60  # the 60 days, once
    fake.calls.clear()
    assert (await garmin.run_sync(db_session, conn))["ok"]
    assert len(fake.paths("/wellness-service/wellness/dailySleepData/")) == 7  # then a week


async def test_every_password_form_in_reglages_posts(as_user: AsyncClient):
    """Without JS (htmx blocked or not loaded), a form without a method is a GET putting the password in the URL, and
    in the server's logs: Garmin's login and code forms post."""
    import re

    page = (await as_user.get("/settings")).text
    forms = [f for f in re.findall(r"<form\b[^>]*>.*?</form>", page, re.S) if 'type="password"' in f]
    assert forms  # Garmin's
    for form in forms:
        head = form.split(">", 1)[0]
        assert 'method="post"' in head and 'action="/garmin/connect"' in head, head
    mfa = (ROOT / "app/templates/partials/garmin_login.html").read_text()
    assert '<form method="post" action="/garmin/mfa"' in mfa
