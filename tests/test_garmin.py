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

ROOT = pathlib.Path(__file__).resolve().parents[1]
MIG = ROOT / "alembic" / "versions" / "w7f8a9b0c1d2_add_garmin.py"


def jwt(client_id: str = "GARMIN_CONNECT_MOBILE_ANDROID_DI_2025Q2", ttl: timedelta = timedelta(hours=20),
        tag: str = "") -> str:
    enc = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()  # noqa: E731
    exp = int((datetime.now(timezone.utc) + ttl).timestamp())
    return f"{enc({'alg': 'RS256'})}.{enc({'exp': exp, 'client_id': client_id, 'tag': tag})}.sig"


def _ms(dt: datetime) -> int:
    return int(dt.replace(tzinfo=timezone.utc).timestamp() * 1000)


def night(day: date, levels: bool = True, hrv: float | None = 62.0) -> dict:
    """dailySleepData of the night ending on `day`: 23:00 → 06:30 local, the
    athlete 2 h ahead of GMT."""
    start, end = datetime.combine(day - timedelta(days=1), datetime.min.time()) + timedelta(hours=23), \
        datetime.combine(day, datetime.min.time()) + timedelta(hours=6, minutes=30)
    gmt = timedelta(hours=2)
    dto = {"calendarDate": day.isoformat(), "sleepTimeSeconds": 25200,
           "sleepStartTimestampLocal": _ms(start), "sleepEndTimestampLocal": _ms(end),
           "sleepStartTimestampGMT": _ms(start - gmt), "sleepEndTimestampGMT": _ms(end - gmt),
           "deepSleepSeconds": 5400, "lightSleepSeconds": 13800, "remSleepSeconds": 6000,
           "awakeSleepSeconds": 1800}
    out = {"dailySleepDTO": dto, "avgOvernightHrv": hrv, "restingHeartRate": 44}
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
        if path == "/wellness-service/wellness/dailySleepData/runner%2042":
            d = date.fromisoformat(params["date"])
            return httpx.Response(200, json=night(d) if (t - d).days % 10 != 9 else {"dailySleepDTO": {}})
        if path == "/usersummary-service/usersummary/daily/runner%2042":
            return httpx.Response(200, json=summary(date.fromisoformat(params["calendarDate"])))
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
    assert n["stages"] == {"deep": 90, "core": 230, "rem": 100, "awake": 30}
    assert n["hrv"] == 62.0 and n["rhr"] == 44
    samples = garmin.night_samples(n)
    assert [(s.kind, s.start.strftime("%H:%M"), s.value) for s in samples] == [
        ("core", "23:00", 90), ("deep", "00:30", 90), ("core", "02:00", 120), ("awake", "04:00", 30),
        ("rem", "04:30", 120)]
    assert all(s.source == "Garmin" for s in samples)


def test_sleep_without_levels_is_built_from_the_minutes():
    n = garmin.parse_sleep(night(date(2026, 10, 5), levels=False))
    samples = garmin.night_samples(n)
    assert samples[0].start == datetime(2026, 10, 4, 23, 0)
    assert {s.kind: s.value for s in samples} == {"deep": 90, "core": 230, "rem": 100, "awake": 30}


def test_parse_readiness_status_predictions_and_hrv():
    t = date(2026, 10, 6)
    r = garmin.parse_readiness([{"calendarDate": str(t), "timestampLocal": "2026-10-06T06:00:00", "score": 30,
                                 "level": "POOR", "recoveryTime": 2400},
                                {"calendarDate": str(t), "timestampLocal": "2026-10-06T10:00:00", "score": 85,
                                 "level": "PRIME", "recoveryTime": 0}])
    assert r == {"day": t, "pct": 85, "level": "Ready for high intensity", "full_h": 0.0}
    s = garmin.parse_training_status({"mostRecentTrainingStatus": {"latestTrainingStatusData": {
        "1": {"calendarDate": str(t), "acuteTrainingLoadDTO": {"dailyTrainingLoadAcute": 400,
                                                               "dailyTrainingLoadChronic": 500}}}}})
    assert s == {"day": t, "short": 400, "long": 500, "ratio": 0.8, "comment": None}
    assert garmin.parse_predictions({"time5K": 1190.4, "timeMarathon": 0}) == {"5k": 1190}
    values, ranges = garmin.parse_hrv({"hrvSummaries": [
        {"calendarDate": "2026-10-06", "lastNightAvg": 58, "baseline": {"balancedLow": 52, "balancedUpper": 66}},
        {"calendarDate": "2026-10-05", "lastNightAvg": None}]})
    assert values == {t: 58} and ranges == {t: {"lo": 52, "hi": 66, "base": None}}


def test_parsers_survive_unknown_shapes():
    for bad in (None, [], {}, "x", 3, {"dailySleepDTO": None}, [{"generic": None}], [None]):
        assert garmin.parse_sleep(bad) is None
        assert garmin.parse_hrv(bad) == ({}, {})
        assert garmin.parse_summary(bad) is None
        assert garmin.parse_vo2max(bad) == {}
        assert garmin.parse_readiness(bad) is None
        assert garmin.parse_training_status(bad) is None
        assert garmin.parse_predictions(bad) == {}
        assert garmin.activity_fields(bad if isinstance(bad, dict) else {}) is None
    assert garmin.parse_sleep({"dailySleepDTO": {"calendarDate": "2026-10-05", "sleepStartTimestampLocal": 10,
                                                 "sleepEndTimestampLocal": 5}}) is None  # ends before it starts


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
    data = {"load": {"day": t, "short": 812, "long": 520, "ratio": 99, "comment": "Excessive"},
            "readiness": {"day": None, "pct": 140, "level": "Rest", "full_h": 3},
            "summaries": {t: {"stress": -2, "steps": 0, "kcal": 0, "bb_wake": None, "bb_high": 77, "bb_low": 5}},
            "hrv_range": {t: {"lo": 70, "hi": 60, "base": None}},
            "vo2max": {t: 120}, "predictions": {"5k": 30, "10k": 2480}}
    rows = {r.metric: r for r in garmin.build_daily(data, t)}
    assert set(rows) == {"load", "body_battery", "fitness"}
    assert rows["load"].details == {"long": 520, "ratio": None, "comment": "Excessive"}
    assert rows["body_battery"].value == 77
    assert rows["fitness"].details == {"pred": {"10k": 2480}}


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
    assert [p for p, _ in fake.paths("/hrv-service/")] == [
        f"/hrv-service/hrv/daily/{t - timedelta(days=27)}/{t}",
        f"/hrv-service/hrv/daily/{t - timedelta(days=55)}/{t - timedelta(days=28)}",
        f"/hrv-service/hrv/daily/{t - timedelta(days=59)}/{t - timedelta(days=56)}"]
    act_q = fake.paths("/activitylist-service/")[0][1]
    assert act_q["startDate"] == str(t - timedelta(days=179)) and act_q["start"] == "0"

    rows = (await db_session.execute(select(HealthMetric).where(HealthMetric.user_id == test_user.id))).scalars().all()
    by = {}
    for r in rows:
        by.setdefault(r.metric, {})[r.date] = r
    assert by["sleep"][t].value == 420 and by["sleep"][t].source == "Garmin"  # 7 h asleep, the awake half hour out
    assert by["sleep"][t].details["bedtime"] == "23:00" and by["sleep"][t].details["wake"] == "06:30"
    assert len(by["sleep"]) == 54  # one night in ten had no data
    assert by["hrv"][t].value == 60 + t.day % 7 and by["hrv"][t].source == "Garmin"
    assert by["rhr"][t].value == 45
    assert by["vo2max"][t - timedelta(days=3)].value == 54.3
    assert by["recovery"][t].value == 72 and by["recovery"][t].details == {"level": "Moderate", "full_h": 25.0}
    assert by["load"][t].value == 812 and by["load"][t].details["comment"] == "Excessive"
    assert by["steps"][t].details == {"kcal": 2650.0, "exercise": 55}
    assert by["stress"][t].value == 28 and by["body_battery"][t].value == 80
    assert by["hrv_norm"][t].details == {"lo": 55, "hi": 70}
    assert by["fitness"][t].details == {"vo2max": 54.3, "pred": {"5k": 1190, "10k": 2480, "half": 5520,
                                                                 "marathon": 11800}}

    acts = (await db_session.execute(select(Activity).where(Activity.user_id == test_user.id)
                                     .order_by(Activity.start_date))).scalars().all()
    assert [(a.strava_activity_id, a.garmin_activity_id, a.sport_type) for a in acts] == [
        (555, 9002, "TrailRun"), (None, 9001, "Run")]
    assert acts[0].name == "Strava trail"  # Strava's row is left as it is
    assert outcome["result"]["activities"] == {"inserted": 1, "linked": 1, "updated": 0}

    # then the last 7 days; the sessions already there are not duplicated
    fake.calls.clear()
    conn.last_sync_at = datetime.now(timezone.utc) - timedelta(hours=7)
    outcome = await garmin.run_sync(db_session, conn)
    assert outcome["ok"] and len(fake.paths("/wellness-service/wellness/dailySleepData/")) == 7
    assert fake.paths("/activitylist-service/")[0][1]["startDate"] == str(t - timedelta(days=6))
    assert not fake.paths("/userprofile-service/")  # the display name is kept
    assert outcome["result"]["activities"] == {"inserted": 0, "linked": 0, "updated": 1}
    n = (await db_session.execute(select(func.count(Activity.id)).where(Activity.user_id == test_user.id))).scalar()
    assert n == 2


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


async def test_a_claimed_sync_is_not_run_twice(db_session: AsyncSession, test_user: User, fake, no_commit):
    conn = await _link(db_session, test_user, sync_claimed_at=datetime.now(timezone.utc))
    assert await garmin.run_sync(db_session, conn) is None and not fake.calls
    conn.sync_claimed_at = datetime.now(timezone.utc) - timedelta(hours=1)  # a crashed worker's claim
    await db_session.flush()
    assert (await garmin.run_sync(db_session, conn))["ok"]


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
    assert hrv.value == 60 + fake.today.day % 7 and hrv.source == "Garmin"  # not mixed with Apple's SDNN


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
                         ("POST", "/settings/garmin/sync"), ("POST", "/settings/garmin/disconnect")):
        r = await client.request(method, path)
        assert r.status_code == 307 and r.headers["location"] == "/", path


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
    assert "Garmin connecté. Tes 60 derniers jours" in page and 'id="garmin-status"' in page
    r = await as_user.get("/garmin/login")  # the ticket is gone once used
    assert "La connexion à Garmin a expiré. Recommence." in r.text


async def test_settings_and_sante_sync_and_disconnect(as_user: AsyncClient, db_session: AsyncSession,
                                                      test_user: User, fake):
    await _link(db_session, test_user)
    sante = (await as_user.get("/sante")).text
    assert "Pas encore de données de Garmin" in sante and 'id="garmin-status"' in sante
    assert "Connecter COROS" not in sante

    r = await as_user.post("/settings/garmin/sync")
    assert r.status_code == 200 and "Synchro terminée" in r.text and "HX-Refresh" not in r.headers
    r = await as_user.post("/settings/garmin/sync", headers={"HX-Request": "true", "HX-Current-URL": "https://test/sante"})
    assert r.headers.get("HX-Refresh") == "true"
    assert "Récupération" in (await as_user.get("/sante")).text

    n = (await db_session.execute(select(func.count(HealthSample.id)).where(HealthSample.user_id == test_user.id))).scalar()
    r = await as_user.post("/settings/garmin/disconnect")
    assert r.status_code == 303 and r.headers["location"] == "/settings#garmin"
    assert (await db_session.execute(select(GarminConnection))).scalar_one_or_none() is None
    n2 = (await db_session.execute(select(func.count(HealthSample.id)).where(HealthSample.user_id == test_user.id))).scalar()
    assert n == n2 > 0  # imported data stays
    page = (await as_user.get("/settings")).text
    assert "Garmin déconnecté" in page and 'hx-post="/garmin/connect"' in page


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
    assert ScriptDirectory.from_config(cfg).get_heads() == [mig.revision]

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
