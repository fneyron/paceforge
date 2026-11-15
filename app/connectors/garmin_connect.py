"""Garmin Connect Developer Program (Business « Starter » access): Garmin PUSHES the athlete's data, PaceForge never
reads Garmin Connect itself (docs/official-connectors.md says what is verified and what is assumed).

OAuth 2.0 with PKCE, as Garmin's public specification describes it (« Garmin Connect Developer Program OAuth2.0
PKCE Specification », developerportal.garmin.com/sites/default/files/OAuth2PKCE.pdf, read 2026-11-15): authorize at
connect.garmin.com/oauth2Confirm (S256), the code exchanged with the client secret and the verifier, an access token
of a day and a refresh token of ~90 days returned anew at each refresh (rotation), the API user id read at
/wellness-api/rest/user/id (stable across re-authorizations: what every push names), the permissions at
/wellness-api/rest/user/permissions, the registration deleted (DELETE /wellness-api/rest/user/registration) when the
athlete disconnects or deletes their account. The token host: the specification says connectapi.garmin.com, public
integrations (Nango, Open Wearables) use diauth.garmin.com: GARMIN_CONNECT_TOKEN_URL says which.

Pushes (one URL per summary type, /webhook/garmin/{type}, or all on /webhook/garmin): JSON objects {type: [items]},
each item naming its userId. ASSUMED until Garmin's own documentation is read (behind its portal): the field names
below come from public integrations (Open Wearables, Terra, Nango) and match the Health and Activity API names
known publicly; tests/official_connector_fixtures.py holds samples in that shape, labelled so. What is read:
- activities (and manuallyUpdatedActivities): one Activity per session, by its activityId — the same id the former
  Garmin link stored (garmin_activity_id): a session it brought is the same row. A multisport parent is skipped (its
  legs come as their own sessions, one row each, as Strava has them).
- activityDetails: the session's laps in Strava's shape (time, average and max HR, distance), from its samples cut
  at its laps' start times (Santé's Entraînement dial reads them); reduced in the web request, the samples dropped.
- dailies: steps (with kcal and intensity minutes: the « watch worn » marker), the day's heart rate (24-h samples),
  the day's average Garmin stress (context only, as the former link writes it); the night's heart rate from those
  samples once the night is known.
- sleeps: the main sleep (window, minutes asleep, stage minutes), its stage intervals (the hypnogram), the naps, the
  night's respiration from its own readings.
- hrv: PaceForge's nightly HRV, exp(mean ln RMSSD) of the readings inside the night's asleep stages (never Garmin's
  lastNightAvg nor its status).
- bodyComps: weigh-ins (weight, body fat), dated.
- deregistrations, userPermissionsChange: the link forgotten (unless Garmin still confirms it: a late notification of
  an older grant), the permissions kept.
Received and NOT stored: epochs, stress details (and their Body Battery), user metrics (VO2 max, fitness age), pulse
ox, all-day respiration, health snapshots, skin temperature, blood pressure, Move IQ, activity files (the details
give the laps), menstrual cycle data: either a brand score or nothing PaceForge reads (and not in the consent's list).
No brand score is ever written (Training Readiness, Body Battery, sleep score, HRV status, VO2 max, load).

Every health row: source « Garmin », the former link's label (Santé's watch preference, its timeline and its notes
key on it), details["via"] = "garmin_connect_api". Without the consent to health data, a health push is dropped in the
web request already (health.store_daily refuses it too); activities always come.
"""
import logging
import math
import statistics
from datetime import date, datetime, timedelta, timezone
from urllib.parse import quote, urlencode

import httpx
from sqlalchemy import select

from app.config import settings
from app.connectors import base
from app.services.health import (
    HRV_METHOD,
    STAGES_READ,
    Daily,
    Sample,
    drop_stale_intervals,
    iso_min,
    main_window,
    nap_daily,
    store_daily,
    store_samples,
)
from app.services.night_measurements import FILTER_READ, select_readings

logger = logging.getLogger(__name__)

PROVIDER, NAME, SOURCE = "garmin", "Garmin", "Garmin"
VIA = "garmin_connect_api"
PKCE = True
# Garmin: « subtract 600 seconds or more from the expiration value » (access tokens last a day)
REFRESH_MARGIN = timedelta(minutes=15)
EXPIRED = "La connexion à Garmin a expiré ou a été retirée : reconnecte-toi."

API_PATHS = {
    "user_id": "/wellness-api/rest/user/id",
    "permissions": "/wellness-api/rest/user/permissions",
    "registration": "/wellness-api/rest/user/registration",
}
# what the athlete grants on Garmin's consent page (the specification's example list); the two PaceForge reads
ACTIVITY_EXPORT, HEALTH_EXPORT = "ACTIVITY_EXPORT", "HEALTH_EXPORT"
PERMISSION_LABELS = {ACTIVITY_EXPORT: "activités", HEALTH_EXPORT: "données de santé"}

ACTIVITY_TYPES = ("activities", "manuallyUpdatedActivities")
DETAIL_TYPES = ("activityDetails",)
HEALTH_TYPES = ("dailies", "sleeps", "hrv", "bodyComps")
LIFECYCLE_TYPES = ("deregistrations", "userPermissionsChange")
IGNORED_TYPES = ("epochs", "stressDetails", "userMetrics", "pulseox", "allDayRespiration", "healthSnapshot",
                 "skinTemp", "bloodPressures", "moveIQActivities", "activityFiles", "mct", "menstrualCycleTracking")
SUMMARY_TYPES = ACTIVITY_TYPES + DETAIL_TYPES + HEALTH_TYPES + LIFECYCLE_TYPES + IGNORED_TYPES


def urls() -> dict[str, str]:
    api = settings.GARMIN_CONNECT_API_BASE_URL.rstrip("/")
    defaults = {"authorize": settings.GARMIN_CONNECT_AUTHORIZE_URL, "token": settings.GARMIN_CONNECT_TOKEN_URL,
                **{key: api + path for key, path in API_PATHS.items()}}
    return base.endpoints(defaults, settings.GARMIN_CONNECT_URLS)


# ── OAuth ───────────────────────────────────────────────────────────────────

def authorize_url(state: str, challenge: str | None) -> str:
    params = {"response_type": "code", "client_id": settings.GARMIN_CONNECT_CLIENT_ID,
              "code_challenge": challenge, "code_challenge_method": "S256",
              "redirect_uri": base.redirect_uri(PROVIDER), "state": state}
    url = urls()["authorize"]
    return url + ("&" if "?" in url else "?") + urlencode(params, quote_via=quote)


async def exchange(client: httpx.AsyncClient, code: str, verifier: str | None) -> dict:
    return await base.token_request(client, urls()["token"], {
        "grant_type": "authorization_code", "client_id": settings.GARMIN_CONNECT_CLIENT_ID,
        "client_secret": settings.GARMIN_CONNECT_CLIENT_SECRET, "code": code, "code_verifier": verifier,
        "redirect_uri": base.redirect_uri(PROVIDER)})


async def refresh(client: httpx.AsyncClient, refresh_token: str, current: str | None = None) -> dict:
    """A new pair (Garmin returns a new refresh token every time: committed at once by base.access_token)."""
    return await base.token_request(client, urls()["token"], {
        "grant_type": "refresh_token", "client_id": settings.GARMIN_CONNECT_CLIENT_ID,
        "client_secret": settings.GARMIN_CONNECT_CLIENT_SECRET, "refresh_token": refresh_token})


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}", "Accept": "application/json"}


async def user_id(client: httpx.AsyncClient, token: str) -> str | None:
    r = await client.get(urls()["user_id"], headers=_bearer(token))
    if r.status_code != 200:
        return None
    try:
        uid = r.json().get("userId")
    except (ValueError, AttributeError):
        return None
    return str(uid)[:128] if uid else None


def parse_permissions(body) -> list[str] | None:
    """A JSON list of permission names (the specification's example), or {"permissions": [...]} (as public
    integrations read it)."""
    if isinstance(body, dict):
        body = body.get("permissions")
    if not isinstance(body, list):
        return None
    return sorted({str(p) for p in body if isinstance(p, str)})


async def identify(client: httpx.AsyncClient, tokens: dict) -> tuple[str | None, str | None]:
    """(API user id, permissions as one space-separated string): the user id is required, the permissions best
    effort (their changes are pushed: userPermissionsChange)."""
    token = str(tokens["access_token"])
    uid = await user_id(client, token)
    scopes = None
    try:
        r = await client.get(urls()["permissions"], headers=_bearer(token))
        perms = parse_permissions(r.json()) if r.status_code == 200 else None
        scopes = " ".join(perms) if perms is not None else None
    except (httpx.HTTPError, ValueError):
        logger.info("Garmin permissions not read at connect")
    return uid, scopes


async def deregister(client: httpx.AsyncClient, conn, token: str) -> None:
    """Required by Garmin's program for a « Déconnecter » or a deleted account."""
    r = await client.delete(urls()["registration"], headers=_bearer(token))
    if r.status_code not in (200, 202, 204):
        logger.info("Garmin user registration deletion answered %s", r.status_code)


def missing_permissions(scopes: str | None) -> list[str]:
    """What the athlete did not grant of what PaceForge reads (nothing said while the permissions are unknown)."""
    if scopes is None:
        return []
    granted = set(scopes.split())
    return [label for perm, label in PERMISSION_LABELS.items() if perm not in granted]


def client_header_ok(value: str | None) -> bool:
    """The push's garmin-client-id header against this app's client id (constant time). Public integrations read
    Garmin's pushes as carrying it; GARMIN_CONNECT_REQUIRE_CLIENT_HEADER=false accepts a push without it (the user
    id must still be a known link's)."""
    import secrets

    expected = settings.GARMIN_CONNECT_CLIENT_ID.strip()
    if value is None:
        return not settings.GARMIN_CONNECT_REQUIRE_CLIENT_HEADER
    return bool(expected) and secrets.compare_digest(value.strip().encode(), expected.encode())


# ── push payloads ───────────────────────────────────────────────────────────

class PayloadError(ValueError):
    pass


def parse_push(body, summary_type: str | None) -> dict[str, list[dict]]:
    """{type: [items]} of a push: a JSON object whose keys are summary types, each a list of objects naming a userId.
    On /webhook/garmin/{type} the object must hold that type; unknown keys are refused (PayloadError)."""
    if not isinstance(body, dict) or not body:
        raise PayloadError("not an object")
    if summary_type is not None and summary_type not in body:
        raise PayloadError("not this summary type")
    out = {}
    for key, items in body.items():
        if key not in SUMMARY_TYPES or not isinstance(items, list):
            raise PayloadError("unknown summary type")
        out[key] = [i for i in items if isinstance(i, dict) and i.get("userId") is not None]
    return out


def _f(v) -> float | None:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) else None


def _int(v) -> int | None:
    f = _f(v)
    return int(f) if f is not None else None


def _local(ts, offset) -> datetime | None:
    """A Garmin time (epoch seconds, UTC) on the athlete's wall clock (+ its offset in seconds), naive."""
    t, off = _f(ts), _f(offset) or 0.0
    if t is None or abs(off) > 15 * 3600:
        return None
    try:
        return datetime.fromtimestamp(t + off, tz=timezone.utc).replace(tzinfo=None)
    except (OverflowError, OSError, ValueError):
        return None


def _day(s) -> date | None:
    try:
        return date.fromisoformat(str(s)[:10])
    except (TypeError, ValueError):
        return None


def _ok(value, lo: float, hi: float) -> bool:
    return isinstance(value, (int, float)) and lo <= value <= hi


def _offset_readings(values, start, offset, lo: float, hi: float) -> list[tuple[datetime, float]]:
    """{seconds since `start`: value} (Garmin's timeOffset… maps) → [(local time, value)], implausible ones out."""
    out: list[tuple[datetime, float]] = []
    origin = _f(start)
    if not isinstance(values, dict) or origin is None:
        return out
    for k, v in values.items():
        try:
            delta = int(k)
        except (TypeError, ValueError):
            continue
        t, value = _local(origin + delta, offset), _f(v)
        if t is not None and value is not None and lo <= value <= hi:
            out.append((t, value))
    return sorted(out)


# ── activities ──────────────────────────────────────────────────────────────

def _sport(type_key: str) -> str:
    from app.services.garmin import SPORT_TYPES  # the former link's table (Garmin's type keys, lower case)

    return SPORT_TYPES.get(type_key, "Workout")


INDOOR_TYPES = {"treadmill_running", "indoor_running", "virtual_run", "indoor_cycling", "virtual_ride",
                "indoor_rowing"}


def activity_fields(a: dict) -> dict | None:
    """The Activity columns of one pushed session, in Strava's units (m, s, m/s, run cadence per leg); None for a
    multisport parent or an unreadable one."""
    gid, start = _int(a.get("activityId")), _f(a.get("startTimeInSeconds"))
    if gid is None or gid <= 0 or start is None or a.get("isParent") is True:
        return None
    elapsed = _f(a.get("durationInSeconds")) or 0
    moving = _f(a.get("movingDurationInSeconds")) or elapsed
    if moving <= 0:
        return None
    type_key = str(a.get("activityType") or "").lower()
    spm = _f(a.get("averageRunCadenceInStepsPerMinute"))
    distance = _f(a.get("distanceInMeters")) or 0.0
    raw = {**a, "source": "garmin", "via": VIA}
    offset = _f(a.get("startTimeOffsetInSeconds"))
    if offset is not None:
        raw["utc_offset"] = offset  # the key Strava uses: the session's local day
    lat, lng = _f(a.get("startingLatitudeInDegree")), _f(a.get("startingLongitudeInDegree"))
    if lat is not None and lng is not None and -90 <= lat <= 90 and -180 <= lng <= 180 and (lat, lng) != (0, 0):
        raw["start_latlng"] = [lat, lng]
    if type_key in INDOOR_TYPES:
        raw["trainer"] = True
    return {
        "garmin_activity_id": gid,
        "sport_type": _sport(type_key),
        "name": str(a.get("activityName") or "Activité Garmin")[:255],
        "start_date": datetime.fromtimestamp(start, tz=timezone.utc),
        "distance": distance,
        "moving_time": round(moving),
        "elapsed_time": round(max(elapsed, moving)),
        "total_elevation_gain": _f(a.get("totalElevationGainInMeters")) or _f(a.get("elevationGainInMeters")) or 0.0,
        "average_speed": _f(a.get("averageSpeedInMetersPerSecond")) or (distance / moving if distance else None),
        "max_speed": _f(a.get("maxSpeedInMetersPerSecond")),
        "average_heartrate": _f(a.get("averageHeartRateInBeatsPerMinute")),
        "max_heartrate": _f(a.get("maxHeartRateInBeatsPerMinute")),
        "average_cadence": spm / 2 if spm else (_f(a.get("averageBikeCadenceInRoundsPerMinute"))
                                                or _f(a.get("averageBikingCadenceInRevPerMinute"))),
        "average_watts": _f(a.get("averagePowerInWatts")),
        "calories": _f(a.get("activeKilocalories")),
        "raw_data": raw,
    }


HR_PLAUSIBLE = (40, 250)


def detail_laps(item: dict) -> dict:
    """An activityDetails item reduced to what PaceForge keeps: {userId, activityId, summary (the session's own
    summary, an activity's fields), laps (Strava's shape), hr_max}. Each lap runs from its start time to the next
    one's (the last to the end): its timer time (or clock time), the mean and max of its heart-rate samples (40 to
    250 bpm), its distance from the cumulative distance. A lap without a plausible average HR is left out."""
    given = item.get("summary")
    summary: dict = {**(given if isinstance(given, dict) else {}), "userId": item.get("userId")}
    if item.get("activityId") is not None and summary.get("activityId") is None:
        summary["activityId"] = item.get("activityId")
    # (time, heart rate, timer seconds, cumulative distance) of each timed sample, in order
    samples: list[tuple[float, float | None, float | None, float | None]] = sorted(
        ((t, _f(s.get("heartRate")), _f(s.get("timerDurationInSeconds")), _f(s.get("totalDistanceInMeters")))
         for s in item.get("samples") or []
         if isinstance(s, dict) and (t := _f(s.get("startTimeInSeconds"))) is not None),
        key=lambda s: s[0])
    starts = sorted({t for lap in item.get("laps") or []
                     if isinstance(lap, dict) and (t := _f(lap.get("startTimeInSeconds"))) is not None})
    plausible = [h for _, h, _, _ in samples if h is not None and HR_PLAUSIBLE[0] <= h <= HR_PLAUSIBLE[1]]
    peak = max(plausible, default=None)
    laps = []
    if samples and starts:
        first, last = samples[0][0], samples[-1][0]
        end = (_f(summary.get("startTimeInSeconds")) or first) + (_f(summary.get("durationInSeconds")) or last - first)
        for a, b in zip(starts, starts[1:] + [max(end, last + 1)], strict=True):
            inside = [s for s in samples if a <= s[0] < b]
            hr = [h for _, h, _, _ in inside if h is not None and HR_PLAUSIBLE[0] <= h <= HR_PLAUSIBLE[1]]
            if not hr:
                continue
            timer = [t for _, _, t, _ in inside if t is not None]
            seconds = timer[-1] - timer[0] if len(timer) >= 2 else b - a
            lap: dict = {"moving_time": round(seconds if seconds > 0 else b - a),
                         "average_heartrate": round(statistics.fmean(hr), 1), "max_heartrate": max(hr)}
            dist = [d for _, _, _, d in inside if d is not None]
            if len(dist) >= 2 and dist[-1] > dist[0]:
                lap["distance"] = round(dist[-1] - dist[0], 1)
            laps.append(lap)
    return {"userId": item.get("userId"), "activityId": item.get("activityId") or summary.get("activityId"),
            "summary": summary, "laps": laps, "hr_max": peak}


# ── health ──────────────────────────────────────────────────────────────────

MIN_READINGS = 12  # (H) readings retained after the sleep filter, as the former link


def daily_rows(item: dict) -> list[Daily]:
    """A daily summary: steps with kcal and intensity minutes (0 steps: the watch was not worn, no row), the day's
    heart rate from its 24-h samples (12 at least), its average Garmin stress (0 to 100; -1 is Garmin's « not
    enough data »). Resting HR, Body Battery and the like are not read."""
    d = _day(item.get("calendarDate"))
    if d is None:
        return []
    out = []
    steps = _f(item.get("steps"))
    if steps is not None and _ok(steps, 1, 200_000):
        active, bmr = _f(item.get("activeKilocalories")), _f(item.get("bmrKilocalories"))
        kcal = (active or 0) + (bmr or 0) if active is not None or bmr is not None else None
        mod = _f(item.get("moderateIntensityDurationInSeconds"))
        vig = _f(item.get("vigorousIntensityDurationInSeconds"))
        exercise = round(((mod or 0) + (vig or 0)) / 60) if mod is not None or vig is not None else None
        out.append(Daily("steps", d, steps, {"kcal": kcal, "exercise": exercise, "via": VIA}))
    hr = [v for t, v in _offset_readings(item.get("timeOffsetHeartRateSamples"), item.get("startTimeInSeconds"),
                                          item.get("startTimeOffsetInSeconds"), 25, 230) if t.date() == d]
    if len(hr) >= MIN_READINGS:
        out.append(Daily("hr_day", d, round(statistics.fmean(hr), 1),
                         {"min": min(hr), "max": max(hr), "n": len(hr), "method": "garmin_24h", "via": VIA}))
    stress = _f(item.get("averageStressLevel"))
    if stress is not None and _ok(stress, 0, 100):
        out.append(Daily("stress", d, stress, {"method": "garmin_daily", "via": VIA}))
    return out


def daily_night_readings(item: dict) -> tuple[date, list[tuple[datetime, float]]] | None:
    """The heart-rate samples of a day's morning (before 14:00 local), for that night's heart rate once its window
    is known (the part of the night after midnight: the day before holds the rest)."""
    d = _day(item.get("calendarDate"))
    if d is None:
        return None
    readings = [(t, v) for t, v in _offset_readings(item.get("timeOffsetHeartRateSamples"),
                                                   item.get("startTimeInSeconds"),
                                                   item.get("startTimeOffsetInSeconds"), 25, 200)
                if t.date() == d and t.hour < 14]
    return (d, readings) if len(readings) >= MIN_READINGS else None


_STAGES = {"deep": "deep", "light": "core", "rem": "rem", "awake": "awake"}


def parse_sleep(item: dict) -> dict | None:
    """A sleep summary (named by its wake-up day): the main window on the local clock (its end the last stage's when
    the awake stages run past the asleep time), minutes asleep, stage minutes, stage intervals, naps, the night's
    respiration readings, the offset (minutes east of UTC)."""
    d = _day(item.get("calendarDate"))
    offset = _f(item.get("startTimeOffsetInSeconds")) or 0.0
    start = _local(item.get("startTimeInSeconds"), offset)
    duration = _f(item.get("durationInSeconds"))
    if d is None or start is None or not duration or duration <= 0:
        return None
    intervals = []
    given = item.get("sleepLevelsMap")
    levels: dict = given if isinstance(given, dict) else {}
    for key, rows in levels.items():
        kind = _STAGES.get(str(key).lower())
        for iv in rows if kind and isinstance(rows, list) else []:
            if not isinstance(iv, dict):
                continue
            a, b = _local(iv.get("startTimeInSeconds"), offset), _local(iv.get("endTimeInSeconds"), offset)
            if a and b and b > a:
                intervals.append((kind, a, b))
    end = max([start + timedelta(seconds=duration)] + [b for _, _, b in intervals])
    if not timedelta(0) < end - start <= timedelta(hours=16):
        return None
    stages = {}
    for kind, key in (("deep", "deepSleepDurationInSeconds"), ("light", "lightSleepDurationInSeconds"),
                      ("rem", "remSleepInSeconds"), ("awake", "awakeDurationInSeconds")):
        v = _f(item.get(key))
        if v is not None:
            stages[kind] = round(v / 60)
    asleep = sum(stages.get(k, 0) for k in ("deep", "light", "rem")) or round(duration / 60)
    naps, nap_windows = 0.0, []
    for n in item.get("naps") or []:
        if not isinstance(n, dict):
            continue
        secs = _f(n.get("napDurationInSeconds"))
        a = _local(n.get("napStartTimeInSeconds"), n.get("napOffsetInSeconds", offset))
        if secs and secs > 0:
            naps += secs
            if a:
                nap_windows.append((a, a + timedelta(seconds=secs)))
    return {
        "day": d, "start": start, "end": end, "asleep": asleep, "stages": stages, "intervals": sorted(
            intervals, key=lambda x: x[1]), "tz": round(offset / 60),
        "resp": _offset_readings(item.get("timeOffsetSleepRespiration"), item.get("startTimeInSeconds"), offset, 4, 40),
        "naps": ({"asleep": round(naps / 60), "windows": nap_windows} if naps >= 60 else None),
        "validation": item.get("validation"),
    }


def sleep_samples(night: dict) -> list[Sample]:
    """The night's stage intervals, clipped to its window: the hypnogram's timeline (source « Garmin », as the
    former link's: Santé reads those)."""
    out = []
    for kind, a, b in night["intervals"]:
        a, b = max(a, night["start"]), min(b, night["end"])
        if b > a:
            out.append(Sample("sleep", kind, a, b, round((b - a).total_seconds() / 60, 2), SOURCE))
    return out


def sleep_rows(night: dict) -> list[Daily]:
    """The main sleep (its stage minutes when Garmin gives deep, light and REM), the naps (health.nap_daily's guards),
    the night's respiration (mean of its readings inside the asleep stages, 12 at least)."""
    d, out = night["day"], []
    if _ok(night.get("asleep"), 1, 16 * 60):
        det = {"main_start": iso_min(night["start"]), "main_end": iso_min(night["end"]),
               "period": round((night["end"] - night["start"]).total_seconds() / 60),
               "bedtime": night["start"].strftime("%H:%M"), "wake": night["end"].strftime("%H:%M"),
               "timeline": bool(sleep_samples(night)), "tz": night["tz"], STAGES_READ: True, FILTER_READ: True,
               "via": VIA}
        st = night["stages"]
        if all(st.get(k) is not None for k in ("deep", "light", "rem")) and st["deep"] + st["light"] + st["rem"]:
            det["stages"] = {"deep": st["deep"], "light": st["light"], "rem": st["rem"], "awake": st.get("awake") or 0}
        out.append(Daily("sleep", d, night["asleep"], det))
    if night.get("naps"):
        row = nap_daily(d, night["naps"]["asleep"], None, night["naps"]["windows"], (night["start"], night["end"]))
        if row:
            row.details = {**(row.details or {}), "via": VIA}
            out.append(row)
    values, scope = select_readings(night["resp"], night["start"], night["end"], night["intervals"])
    if len(values) >= MIN_READINGS:
        out.append(Daily("resp_night", d, round(statistics.fmean(values), 1),
                         {"n": len(values), "method": "points", **scope, "via": VIA}))
    return out


def hrv_readings(item: dict) -> tuple[date, list[tuple[datetime, float]], tuple[datetime, datetime] | None] | None:
    """An HRV summary: (its night's wake-up day, the readings (local time, RMSSD ms, 5 to 300), its own window — the
    sleep it was measured in — for when the night's sleep never comes). Garmin's lastNightAvg is not read."""
    d = _day(item.get("calendarDate"))
    offset = item.get("startTimeOffsetInSeconds")
    readings = _offset_readings(item.get("hrvValues"), item.get("startTimeInSeconds"), offset, 5, 300)
    if d is None or not readings:
        return None
    start, duration = _local(item.get("startTimeInSeconds"), offset), _f(item.get("durationInSeconds"))
    window = (start, start + timedelta(seconds=duration)) if start and duration and duration > 0 else None
    return d, readings, window


def body_rows(item: dict) -> list[Daily]:
    """A weigh-in: weight (kg, 25 to 300) and body fat (%, 1 to 75), dated (athlete_weight and Santé read
    details.measured_at, UTC)."""
    ts = _f(item.get("measurementTimeInSeconds"))
    local = _local(ts, item.get("measurementTimeOffsetInSeconds"))
    if ts is None or local is None:
        return []
    at = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
    out = []
    grams = _f(item.get("weightInGrams"))
    if grams is not None and _ok(grams / 1000, 25, 300):
        out.append(Daily("weight", local.date(), round(grams / 1000, 2), {"measured_at": at, "via": VIA}))
    fat = _f(item.get("bodyFatInPercent"))
    if fat is not None and _ok(fat, 1, 75):
        out.append(Daily("body_fat", local.date(), round(fat, 1), {"measured_at": at, "via": VIA}))
    return out


# ── processing (the Celery worker: app.tasks.official_connectors) ───────────

async def apply_details(db, conn, reduced: dict) -> str:
    """The laps of a session (its summary imported first when the session itself has not come yet): set on
    Garmin's own row, never over Strava's laps or another service's row."""
    from app.models.activity import Activity

    fields = activity_fields(reduced.get("summary") or {})
    act = None
    if fields:
        _, act = await base.import_activity(db, conn.user_id, PROVIDER, fields)
    elif (gid := _int(reduced.get("activityId"))) is not None:
        act = (await db.execute(select(Activity).where(Activity.garmin_activity_id == gid,
                                                       Activity.user_id == conn.user_id))).scalar_one_or_none()
    if act is None or act.strava_activity_id is not None or act.coros_activity_id is not None:
        return "kept"
    if reduced.get("laps"):
        act.laps = reduced["laps"]
        act.computed_metrics = None
    if reduced.get("hr_max") and not act.max_heartrate:
        act.max_heartrate = reduced["hr_max"]
    act.raw_data = {**(act.raw_data or {}), "laps_read": True}
    return "detailed"


async def store_sleep(db, conn, item: dict) -> dict:
    night = parse_sleep(item)
    if night is None:
        return {}
    samples = sleep_samples(night)
    result = await store_daily(db, conn.user_id, sleep_rows(night), SOURCE)
    if result.get("no_consent"):
        return result
    await store_samples(db, conn.user_id, samples)
    await drop_stale_intervals(db, conn.user_id, SOURCE, {night["day"]: (night["start"], night["end"])}, samples)
    return result


async def night_window(db, user_id: int, day: date):
    """The night of `day` as stored (its sleep row's main window, Garmin's stage intervals inside it), or None."""
    from app.models.health import HealthMetric, HealthSample

    row = (await db.execute(select(HealthMetric).where(
        HealthMetric.user_id == user_id, HealthMetric.date == day, HealthMetric.metric == "sleep",
        HealthMetric.source == SOURCE))).scalar_one_or_none()
    window = main_window(day, row.details) if row is not None else None
    if window is None:
        return None
    start, end = window
    stages = (await db.execute(select(HealthSample.kind, HealthSample.start_at, HealthSample.end_at).where(
        HealthSample.user_id == user_id, HealthSample.metric == "sleep", HealthSample.source == SOURCE,
        HealthSample.start_at >= start - timedelta(hours=1), HealthSample.start_at < end))).all()
    return start, end, [(k, a, b) for k, a, b in stages], (row.details or {}).get("tz")


NIGHT_METRICS = {"hrv": (5, 300), "hr_night": (25, 200)}


async def night_value(db, conn, day: date, metric: str, readings: list[tuple[datetime, float]],
                      fallback: tuple[datetime, datetime] | None, final: bool) -> str:
    """The night's HRV or heart rate from its readings inside the asleep stages of the stored night: « stored »,
    « waiting » (the night has not come yet: asked again later), or « dropped » (fewer than 12 readings, or no
    night at the last try: the HRV then falls back on its own window, labelled as such)."""
    lo, hi = NIGHT_METRICS[metric]
    readings = [(t, v) for t, v in readings if lo <= v <= hi]
    night = await night_window(db, conn.user_id, day)
    if night is not None:
        start, end, intervals, tz = night
    elif final and metric == "hrv" and fallback:
        (start, end), intervals, tz = fallback, [], None
    elif not final:
        return "waiting"
    else:
        return "dropped"
    values, scope = select_readings(readings, start, end, intervals)
    if len(values) < MIN_READINGS:
        return "dropped"
    if metric == "hrv":
        row = Daily("hrv", day, round(math.exp(statistics.fmean(math.log(v) for v in values)), 1),
                    {"n": len(values), "tz": tz, "method": HRV_METHOD, **scope, "via": VIA})
    else:
        mean = statistics.fmean(values)
        if not 25 <= mean <= 120:
            return "dropped"
        row = Daily("hr_night", day, round(mean, 1),
                    {"min": min(values), "max": max(values), "n": len(values), "method": "points", "nap_day": False,
                     **scope, "partial": start.date() < day, "via": VIA})
    result = await store_daily(db, conn.user_id, [row], SOURCE)
    return "dropped" if result.get("no_consent") else "stored"


def pack(readings: list[tuple[datetime, float]]) -> list[list]:
    """Readings for a queued task (JSON): [[local ISO time, value]]."""
    return [[t.isoformat(timespec="seconds"), v] for t, v in readings]


def unpack(packed) -> list[tuple[datetime, float]]:
    out = []
    for row in packed or []:
        try:
            out.append((datetime.fromisoformat(row[0]), float(row[1])))
        except (TypeError, ValueError, IndexError):
            continue
    return out


def entry_items(summary_type: str, items: list[dict]) -> list[dict]:
    """What a push queues of its items: activity details reduced to their laps (the samples dropped, so the queue
    never carries megabytes), the rest as Garmin sent it."""
    if summary_type in DETAIL_TYPES:
        return [detail_laps(i) for i in items]
    return items


async def process(db, summary_type: str, entries: list[dict]) -> list[dict]:
    """One push's items, each for its link ({"conn": id, "item": …}): stored, then committed. Returns the nightly
    values still waiting for their night ({conn, day, metric, readings, fallback}), queued again later. An item
    whose userId is not its link's provider id is skipped (never trust a mismatch); a link gone since is skipped."""
    from app.models.official_connection import OfficialConnection

    ids = sorted({e["conn"] for e in entries if isinstance(e.get("conn"), int)})
    conns = {c.id: c for c in (await db.execute(select(OfficialConnection).where(
        OfficialConnection.id.in_(ids), OfficialConnection.provider == PROVIDER,
        OfficialConnection.status == base.ACTIVE))).scalars()} if ids else {}
    waiting, gone = [], set()
    now = datetime.now(timezone.utc)
    for entry in entries:
        conn, item = conns.get(entry.get("conn")), entry.get("item")
        if conn is None or conn.id in gone or not isinstance(item, dict):
            continue
        if str(item.get("userId")) != conn.provider_user_id:
            continue
        if summary_type in ACTIVITY_TYPES:
            fields = activity_fields(item)
            if fields:
                await base.import_activities(db, conn.user_id, PROVIDER, [fields])
        elif summary_type in DETAIL_TYPES:
            await apply_details(db, conn, item)
        elif summary_type == "dailies":
            await store_daily(db, conn.user_id, daily_rows(item), SOURCE)
            night = daily_night_readings(item)
            if night and await night_value(db, conn, night[0], "hr_night", night[1], None, False) == "waiting":
                waiting.append({"conn": conn.id, "day": night[0].isoformat(), "metric": "hr_night",
                                "readings": pack(night[1]), "fallback": None})
        elif summary_type == "sleeps":
            await store_sleep(db, conn, item)
        elif summary_type == "hrv":
            hrv = hrv_readings(item)
            if hrv and await night_value(db, conn, hrv[0], "hrv", hrv[1], hrv[2], False) == "waiting":
                waiting.append({"conn": conn.id, "day": hrv[0].isoformat(), "metric": "hrv", "readings": pack(hrv[1]),
                                "fallback": pack([(hrv[2][0], 0), (hrv[2][1], 0)]) if hrv[2] else None})
        elif summary_type == "bodyComps":
            await store_daily(db, conn.user_id, body_rows(item), SOURCE)
        elif summary_type == "userPermissionsChange":
            perms = parse_permissions(item.get("permissions"))
            if perms is not None:
                conn.scopes = " ".join(perms)
        elif summary_type == "deregistrations":
            if not await still_granted(db, conn):
                logger.info("Garmin deregistered the official link of user %d", conn.user_id)
                await base.disconnect(db, conn, tell_provider=False)
                gone.add(conn.id)
                continue
            logger.info("Garmin deregistration of user %d ignored: the current grant is confirmed", conn.user_id)
        conn.last_push_at = now
    await db.commit()
    return waiting


async def deferred_night(db, conn_id: int, day: str, metric: str, readings, fallback, final: bool) -> str:
    """A queued nightly value tried again (app.tasks.official_connectors.garmin_night)."""
    from app.models.official_connection import OfficialConnection

    conn = await db.get(OfficialConnection, conn_id)
    if conn is None or conn.provider != PROVIDER or conn.status != base.ACTIVE or metric not in NIGHT_METRICS:
        return "dropped"
    bounds = unpack(fallback)
    window = (bounds[0][0], bounds[1][0]) if len(bounds) == 2 else None
    outcome = await night_value(db, conn, date.fromisoformat(day), metric, unpack(readings), window, final)
    await db.commit()
    return outcome


async def still_granted(db, conn) -> bool:
    """Whether Garmin still confirms this link (its user id read with the current token): a deregistration can be
    the late notice of an older grant, the athlete having reconnected since. Anything short of a confirmation
    counts as gone."""
    try:
        async with base.http_client() as client:
            token = await base.access_token(db, conn, client)
            return await user_id(client, token) == conn.provider_user_id
    except (base.ConnectorError, httpx.HTTPError):
        return False
