"""COROS Partner API: the athlete's workouts pushed by COROS minutes after their watch syncs, their daily data read
every two hours (docs/official-connectors.md says what is verified and what is assumed).

What COROS publishes (support.coros.com « Partner API Access », read 2026-11-15): multi-user OAuth 2.0 (authorize,
access token, refresh, deauthorize, bind state); getUserInfo, getWorkoutRecords (30 days per call at most, 3 months
back), getDailyData; getWorkoutDetailFit (the workout's FIT file); a workout summary pushed ~5 min after a sync, with
retries, the client id and secret in its headers; 1 000 calls a minute. The OAuth endpoints come from Nango's public
provider list (open.coros.com/oauth2/authorize, /oauth2/accesstoken, /oauth2/refresh-token; the token answer carries
the athlete's openId); no PKCE is listed there, so none is sent.
ASSUMED until COROS's documentation (sent with the credentials) is read — each a setting (COROS_PARTNER_URLS) or
one place below: the API paths (DEFAULT_PATHS), the token and openId passed as query parameters, the {result:
"0000", data} envelope, a refresh that may answer « 0000 » alone (the token then simply lasts longer), the push's
body (a list of workout summaries under sportDataList) and its header names, the workout's field names (labelId,
mode/subMode or sportType, startTime, endTime, startTimezone in quarter hours, distance in metres, duration in
seconds, avgHeartRate, calorie, fitUrl) and the daily data's (happenDay, steps, calorie). Where a workout's FIT
file is available, its values (the FIT profile is public: app.connectors.fit) take precedence over the summary's:
sport, times, distance, D+, heart rate, laps.

Mapping: a workout → Activity by its labelId (coros_activity_id: the id the MCP link stores, assumed to be the same
id; a twin under another id is linked, never duplicated), its laps in Strava's shape from the FIT file. Daily data
→ steps (the « watch worn » marker, with kcal), source « COROS », the MCP link's label. No brand value is read
(recovery, load, stress, sleep score, fitness, the daytime resting HR), as services.coros never reads them. Nights
and HRV: not read by this connector until getDailyData's fields are known (the MCP link brings them meanwhile).
"""
import logging
import secrets
from datetime import date, datetime, timedelta, timezone
from urllib.parse import quote, urlencode, urlsplit

import httpx

from app.config import settings
from app.connectors import base, fit
from app.services.health import Daily

logger = logging.getLogger(__name__)

PROVIDER, NAME, SOURCE = "coros", "COROS", "COROS"
VIA = "coros_partner_api"
PKCE = False
REFRESH_MARGIN = timedelta(days=1)
TOKEN_LIFETIME_S = 30 * 24 * 3600  # (assumed) what a refresh answering « 0000 » alone gives the current token
EXPIRED = "La connexion à COROS a expiré ou a été retirée : reconnecte-toi."
OK = "0000"

DEFAULT_PATHS = {
    "authorize": "/oauth2/authorize",       # Nango's provider list
    "token": "/oauth2/accesstoken",         # Nango's provider list
    "refresh": "/oauth2/refresh-token",     # Nango's provider list
    "deauthorize": "/oauth2/deauthorize",   # assumed (COROS lists « deauthorize »)
    "user": "/coros/userinfosim",           # assumed (getUserInfo)
    "workouts": "/v2/coros/sport/list",     # assumed (getWorkoutRecords)
    "daily": "/v2/coros/daily/list",        # UNKNOWN (getDailyData): set COROS_PARTNER_URLS once read
    "fit": "/v2/coros/sport/detail/fit",    # UNKNOWN (getWorkoutDetailFit): set COROS_PARTNER_URLS once read
}
WINDOW_DAYS = 30  # getWorkoutRecords: 30 days per call at most
LOOKBACK_DAYS = 90  # … and 3 months back: the first import after connecting
RECENT_DAYS = 7
DAILY_BACKFILL_DAYS = 30
FIT_PER_RUN = 10  # FIT files read per push or pull, newest first; the others at the next one
SYNC_EVERY = timedelta(hours=2)
CLAIM_TTL = timedelta(minutes=15)


def urls() -> dict[str, str]:
    root = settings.COROS_PARTNER_BASE_URL.rstrip("/")
    return base.endpoints({key: root + path for key, path in DEFAULT_PATHS.items()}, settings.COROS_PARTNER_URLS)


# ── OAuth ───────────────────────────────────────────────────────────────────

def authorize_url(state: str, challenge: str | None = None) -> str:
    params = {"client_id": settings.COROS_PARTNER_CLIENT_ID, "redirect_uri": base.redirect_uri(PROVIDER),
              "state": state, "response_type": "code"}
    url = urls()["authorize"]
    return url + ("&" if "?" in url else "?") + urlencode(params, quote_via=quote)


def _tokens(body) -> dict:
    """The token fields of an answer: at its top level (OAuth), or under `data` (COROS's envelope, assumed)."""
    if isinstance(body, dict) and not body.get("access_token") and isinstance(body.get("data"), dict):
        return body["data"]
    return body if isinstance(body, dict) else {}


async def _token_call(client: httpx.AsyncClient, url: str, data: dict) -> dict:
    r = await client.post(url, data=data, headers={"Accept": "application/json"})
    try:
        body = r.json()
    except ValueError:
        body = {}
    tokens = _tokens(body)
    if r.status_code == 200 and tokens.get("access_token"):
        return tokens
    if r.status_code == 200 and isinstance(body, dict) and str(body.get("result")) == OK:
        return {"result": OK}
    code = (body.get("error") or body.get("result")) if isinstance(body, dict) else None
    raise base.TokenError(str(code or f"http_{r.status_code}")[:40])


async def exchange(client: httpx.AsyncClient, code: str, verifier: str | None = None) -> dict:
    tokens = await _token_call(client, urls()["token"], {
        "client_id": settings.COROS_PARTNER_CLIENT_ID, "client_secret": settings.COROS_PARTNER_CLIENT_SECRET,
        "code": code, "redirect_uri": base.redirect_uri(PROVIDER), "grant_type": "authorization_code"})
    if not tokens.get("access_token"):
        raise base.TokenError("no_token")
    return tokens


async def refresh(client: httpx.AsyncClient, refresh_token: str, current: str | None = None) -> dict:
    """A new pair, or (an answer « 0000 » alone, assumed) the current token lasting longer."""
    tokens = await _token_call(client, urls()["refresh"], {
        "client_id": settings.COROS_PARTNER_CLIENT_ID, "client_secret": settings.COROS_PARTNER_CLIENT_SECRET,
        "grant_type": "refresh_token", "refresh_token": refresh_token})
    if tokens.get("access_token"):
        return tokens
    if not current:
        raise base.TokenError("no_token")
    return {"access_token": current, "expires_in": TOKEN_LIFETIME_S}


async def identify(client: httpx.AsyncClient, tokens: dict) -> tuple[str | None, str | None]:
    """(openId, scopes): the openId comes with the token answer; COROS lists no scopes."""
    uid = tokens.get("openId") or tokens.get("openid") or tokens.get("open_id")
    return (str(uid)[:128] if uid else None), None


async def deregister(client: httpx.AsyncClient, conn, token: str) -> None:
    r = await client.post(urls()["deauthorize"], params={"token": token}, headers={"Accept": "application/json"})
    if r.status_code != 200:
        logger.info("COROS deauthorize answered %s", r.status_code)


def missing_permissions(scopes: str | None) -> list[str]:
    return []


# ── the push ────────────────────────────────────────────────────────────────

ID_HEADERS = ("client", "client_id", "clientid", "client-id", "x-client-id")
SECRET_HEADERS = ("secret", "client_secret", "clientsecret", "client-secret", "x-client-secret")


def push_authentic(headers) -> bool:
    """COROS sends this app's client id and secret in the push's headers (their names assumed: any of the usual
    spellings, both required), each compared in constant time."""
    cid, secret = settings.COROS_PARTNER_CLIENT_ID.strip(), settings.COROS_PARTNER_CLIENT_SECRET.strip()
    if not cid or not secret:
        return False
    got_id = next((str(headers.get(h)) for h in ID_HEADERS if headers.get(h)), "")
    got_secret = next((str(headers.get(h)) for h in SECRET_HEADERS if headers.get(h)), "")
    if not got_id or not got_secret:
        return False
    id_ok = secrets.compare_digest(got_id.strip().encode(), cid.encode())
    return secrets.compare_digest(got_secret.strip().encode(), secret.encode()) and id_ok


class PayloadError(ValueError):
    pass


def _open_id(rec: dict):
    return rec.get("openId") or rec.get("openid") or rec.get("open_id")


def parse_push(body) -> list[dict]:
    """The workout summaries of a push: a list, or an object holding one (sportDataList, assumed; data; or any list
    of workouts), each naming its openId and labelId. PayloadError for anything else."""
    items = None
    if isinstance(body, list):
        items = body
    elif isinstance(body, dict):
        for key in ("sportDataList", "data", "workouts", "list"):
            if isinstance(body.get(key), list):
                items = body[key]
                break
        if items is None and _open_id(body) and body.get("labelId") is not None:
            items = [body]
    if items is None:
        raise PayloadError("no workout list")
    return [r for r in items if isinstance(r, dict) and _open_id(r) and r.get("labelId") is not None]


def open_ids(items: list[dict]) -> set[str]:
    return {str(_open_id(r)) for r in items}


def open_id(rec: dict) -> str:
    return str(_open_id(rec))


# ── workouts → Activity ─────────────────────────────────────────────────────

# COROS mode/subMode → Strava's sport types. ASSUMED (from COROS's FIT exports as communities read them): the FIT
# file's sport takes precedence whenever it is read; an unknown code is a « Workout ».
MODE_SPORTS: dict[tuple[int | None, int | None], str] = {
    (8, 1): "Run", (8, 2): "Run", (9, 1): "Ride", (9, 2): "VirtualRide", (10, 1): "Swim", (10, 2): "Swim",
    (14, 1): "Hike", (15, 1): "TrailRun", (16, 1): "Hike", (20, 1): "Run"}
MODE_INDOOR: set[tuple[int | None, int | None]] = {(8, 2), (9, 2)}


def _num(v) -> float | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v))
    except (TypeError, ValueError):
        return None


def _first(rec: dict, *keys):
    return next((rec[k] for k in keys if rec.get(k) not in (None, "")), None)


def _epoch(v) -> float | None:
    t = _num(v)
    if t is None or t <= 0:
        return None
    return t / 1000 if t > 1e11 else t  # milliseconds


def _sport(rec: dict) -> tuple[str, bool]:
    from app.services.coros import INDOOR, SPORTS  # the MCP link's codes (sportType), when a record carries them

    code = _num(rec.get("sportType"))
    if code is not None:
        return SPORTS.get(int(code), "Workout"), int(code) in INDOOR
    mode, sub = _num(rec.get("mode")), _num(rec.get("subMode"))
    key = (int(mode) if mode is not None else None, int(sub) if sub is not None else None)
    return MODE_SPORTS.get(key, "Workout"), key in MODE_INDOOR


def workout_fields(rec: dict) -> dict | None:
    """The Activity columns of one workout summary (field names assumed), in Strava's units; None when unreadable."""
    try:
        label = int(str(rec.get("labelId")))
    except (TypeError, ValueError):
        return None
    start = _epoch(_first(rec, "startTime", "startTimestamp"))
    if label <= 0 or start is None:
        return None
    end = _epoch(_first(rec, "endTime", "endTimestamp"))
    span = end - start if end and end > start else None
    duration = _num(_first(rec, "duration", "totalTime", "workoutTime")) or span or 0
    if duration <= 0:
        return None
    meters = _num(rec.get("distance")) or 0.0
    sport, indoor = _sport(rec)
    raw = {k: v for k, v in rec.items() if k not in ("fitUrl", "token")}
    raw.update({"source": "coros", "via": VIA})
    tz = _num(_first(rec, "startTimezone", "timezone"))
    if tz is not None and abs(tz) <= 14 * 4:
        raw["utc_offset"] = tz * 900  # quarter hours (as COROS writes them elsewhere)
    if indoor:
        raw["trainer"] = True
    kcal = _num(_first(rec, "calorie", "calories"))
    if kcal is not None and kcal > 20_000:  # small calories (some COROS answers): kcal
        kcal = kcal / 1000
    return {
        "coros_activity_id": label,
        "sport_type": sport,
        "name": str(_first(rec, "name", "workoutName", "sportName") or "Activité COROS")[:255],
        "start_date": datetime.fromtimestamp(start, tz=timezone.utc),
        "distance": meters,
        "moving_time": round(duration),
        "elapsed_time": round(max(span or 0, duration)),
        "total_elevation_gain": _num(_first(rec, "ascent", "totalAscent", "elevationGain")) or 0.0,
        "average_speed": meters / duration if meters else None,
        "average_heartrate": _num(_first(rec, "avgHeartRate", "avgHr", "avgHeartrate")),
        "max_heartrate": _num(_first(rec, "maxHeartRate", "maxHr", "maxHeartrate")),
        "calories": kcal,
        "raw_data": raw,
    }


HR_PLAUSIBLE = (40, 250)


def with_fit(fields: dict, decoded: dict) -> dict:
    """The summary's columns with what its FIT file says (public format, its units known): sport, start, times,
    distance, D+, heart rate, start position, laps."""
    s = decoded.get("session") or {}
    out = {**fields, "raw_data": {**fields["raw_data"], "laps_read": True}}
    if fit.sport_type(s):
        out["sport_type"] = fit.sport_type(s)
        if fit.indoor(s):
            out["raw_data"]["trainer"] = True
    start = s.get("start_time")
    if isinstance(start, datetime) and abs((start - fields["start_date"]).total_seconds()) < 3600:
        out["start_date"] = start
    if s.get("timer") and s["timer"] > 0:
        out["moving_time"] = round(s["timer"])
    if s.get("elapsed") and s["elapsed"] > 0:
        out["elapsed_time"] = round(max(s["elapsed"], out["moving_time"]))
    if s.get("distance") is not None and s["distance"] >= 0:
        out["distance"] = round(float(s["distance"]), 1)
    if s.get("ascent") is not None and 0 <= s["ascent"] <= 20_000:
        out["total_elevation_gain"] = float(s["ascent"])
    for key, name in (("average_heartrate", "avg_hr"), ("max_heartrate", "max_hr")):
        if s.get(name) and HR_PLAUSIBLE[0] <= s[name] <= HR_PLAUSIBLE[1]:
            out[key] = float(s[name])
    if s.get("start_latlng") and s["start_latlng"] != [0, 0]:
        out["raw_data"]["start_latlng"] = s["start_latlng"]
    if out["distance"] and out["moving_time"]:
        out["average_speed"] = out["distance"] / out["moving_time"]
    laps = fit.strava_laps(decoded.get("laps") or [])
    if laps:
        out["laps"] = laps
    return out


def fit_host_allowed(url: str | None) -> bool:
    """An https URL on one of COROS_PARTNER_FIT_HOSTS (or a subdomain): never fetch a host a payload chose."""
    if not url:
        return False
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    host = (parts.hostname or "").lower()
    allowed = [h.strip().lower() for h in settings.COROS_PARTNER_FIT_HOSTS.split(",") if h.strip()]
    return parts.scheme == "https" and any(host == h or host.endswith("." + h) for h in allowed)


async def download_fit(client: httpx.AsyncClient, url: str) -> bytes | None:
    """A FIT file from an allowed host, streamed and capped (fit.MAX_BYTES); None when refused or unreadable."""
    if not fit_host_allowed(url):
        logger.info("COROS FIT file on a host not allowed: skipped")
        return None
    async with client.stream("GET", url) as r:
        if r.status_code != 200:
            return None
        chunks, size = [], 0
        async for chunk in r.aiter_bytes():
            size += len(chunk)
            if size > fit.MAX_BYTES:
                return None
            chunks.append(chunk)
    return b"".join(chunks)


async def _api(client: httpx.AsyncClient, url: str, token: str, conn, **params):
    """One API call (token and openId as query parameters, the {result, data} envelope: assumed). 401/403 raise
    PermissionError (the caller refreshes once)."""
    r = await client.get(url, params={"token": token, "openId": conn.provider_user_id, **params},
                         headers={"Accept": "application/json"})
    if r.status_code in (401, 403):
        raise PermissionError(r.status_code)
    if r.status_code >= 400:
        raise base.ConnectorError(f"COROS ne répond pas pour l’instant (erreur {r.status_code}).")
    try:
        body = r.json()
    except ValueError as e:
        raise base.ConnectorError("COROS a renvoyé une réponse illisible.") from e
    if isinstance(body, dict) and "result" in body and str(body["result"]) != OK:
        raise base.ConnectorError(f"COROS a refusé la demande ({str(body['result'])[:12]}).")
    return body.get("data") if isinstance(body, dict) and "data" in body else body


async def fit_url(client: httpx.AsyncClient, token: str, conn, rec: dict) -> str | None:
    """The workout's FIT file: its summary's fitUrl, else what getWorkoutDetailFit answers (path unknown)."""
    if rec.get("fitUrl"):
        return str(rec["fitUrl"])
    try:
        data = await _api(client, urls()["fit"], token, conn, labelId=rec.get("labelId"), mode=rec.get("mode"),
                          subMode=rec.get("subMode"))
    except (base.ConnectorError, PermissionError, httpx.HTTPError):
        return None
    if isinstance(data, dict):
        return data.get("fitUrl") or data.get("fileUrl") or data.get("url")
    return data if isinstance(data, str) else None


def _ymd(d: date) -> str:
    return d.strftime("%Y%m%d")


def _windows(lo: date, hi: date, size: int) -> list[tuple[date, date]]:
    out, end = [], hi
    while end >= lo:
        start = max(lo, end - timedelta(days=size - 1))
        out.append((start, end))
        end = start - timedelta(days=1)
    return out


def daily_rows(rec: dict) -> list[Daily]:
    """One day of getDailyData (field names assumed): its steps with kcal, the « watch worn » marker (0 steps: not
    worn, no row). Nothing else is read until COROS's documentation says what the day holds."""
    raw = str(_first(rec, "happenDay", "date", "day") or "")
    digits = raw.replace("-", "")
    try:
        d = date(int(digits[:4]), int(digits[4:6]), int(digits[6:8]))
    except (ValueError, IndexError):
        return []
    steps = _num(_first(rec, "steps", "step"))
    if steps is None or not 1 <= steps <= 200_000:
        return []
    kcal = _num(_first(rec, "calorie", "calories"))
    if kcal is not None and kcal > 20_000:
        kcal = kcal / 1000
    return [Daily("steps", d, steps, {"kcal": kcal, "via": VIA})]


# ── processing (the Celery worker: app.tasks.official_connectors) ───────────

async def _fit_wanted(db, user_id: int, fields: dict) -> bool | None:
    """Whether this workout's FIT file is worth reading: yes for a new session or this connector's own row not read
    yet; no for a row whose file was read (None: not even its summary is written again, it is rougher) or a row
    another service owns (Strava's, or the MCP link's under another id: their own details stand)."""
    from sqlalchemy import select

    from app.models.activity import Activity

    act = (await db.execute(select(Activity).where(
        Activity.coros_activity_id == fields["coros_activity_id"], Activity.user_id == user_id))).scalar_one_or_none()
    if act is not None:
        if (act.raw_data or {}).get("laps_read"):
            return None
        return act.strava_activity_id is None and act.garmin_activity_id is None
    twin = await base.find_twin(db, user_id, fields["sport_type"], fields["start_date"], fields["distance"],
                                fields["moving_time"])
    return twin is None


async def import_workouts(db, conn, client: httpx.AsyncClient, token: str | None, records: list[dict]) -> dict:
    """Workout summaries into Activity, newest first, each with its FIT file's values when it can be read (up to
    FIT_PER_RUN a run; one already read is not read again, nor overwritten by its summary's rougher values)."""
    rows, fits, kept = [], 0, 0
    for rec in sorted(records, key=lambda r: _epoch(_first(r, "startTime", "startTimestamp")) or 0, reverse=True):
        fields = workout_fields(rec)
        if fields is None:
            continue
        wanted = await _fit_wanted(db, conn.user_id, fields)
        if wanted is None:
            kept += 1
            continue
        if wanted and fits < FIT_PER_RUN:
            url = rec.get("fitUrl") or (await fit_url(client, token, conn, rec) if token else None)
            if url:
                fits += 1
                try:
                    data = await download_fit(client, str(url))
                    if data:
                        fields = with_fit(fields, fit.decode(data))
                except (fit.FitError, httpx.HTTPError):
                    logger.info("COROS FIT file of a workout not read (user %d)", conn.user_id)
        rows.append(fields)
    counts = await base.import_activities(db, conn.user_id, PROVIDER, rows) if rows else {}
    return {**counts, "kept": counts.get("kept", 0) + kept, "fit": fits}


async def process_push(db, conn, records: list[dict]) -> dict:
    """A push's workouts for one link (FIT files read with its token when their URL needs it)."""
    async with base.http_client() as client:
        token = None
        try:
            token = await base.access_token(db, conn, client)
        except base.ConnectorError:
            logger.info("COROS official link of user %d: no usable token for the FIT files", conn.user_id)
        result = await import_workouts(db, conn, client, token, records)
    conn.last_push_at = datetime.now(timezone.utc)
    return result


async def claim(db, conn) -> bool:
    """Take this link's pull for this worker; False when another one holds it."""
    from sqlalchemy import or_, update

    from app.models.official_connection import OfficialConnection

    now = datetime.now(timezone.utc)
    res = await db.execute(update(OfficialConnection).where(
        OfficialConnection.id == conn.id,
        or_(OfficialConnection.sync_claimed_at.is_(None), OfficialConnection.sync_claimed_at < now - CLAIM_TTL),
    ).values(sync_claimed_at=now).execution_options(synchronize_session=False))
    await db.commit()
    return res.rowcount == 1


def _records(data) -> list[dict]:
    if isinstance(data, dict):
        data = data.get("list") or data.get("dataList") or data.get("sportDataList") or []
    return [r for r in data if isinstance(r, dict)] if isinstance(data, list) else []


async def sync(db, conn, *, backfill: bool = False) -> dict:
    """Read the workouts of the last week (three months right after connecting, in 30-day windows) and, with the
    consent to health data only, the daily data (nothing of it is asked for without). A 401 refreshes once."""
    from app.services import health_consent
    from app.services.health import store_daily

    health = await health_consent.allowed(db, conn.user_id)
    today = datetime.now(timezone.utc).date()
    out: dict = {}
    async with base.http_client() as client:
        token = await base.access_token(db, conn, client)

        async def call(url: str, **params):
            nonlocal token
            try:
                return await _api(client, url, token, conn, **params)
            except PermissionError:
                token = await base.access_token(db, conn, client, rejected=token)
                try:
                    return await _api(client, url, token, conn, **params)
                except PermissionError:
                    raise await base.mark_reauth(db, conn, EXPIRED) from None

        records: list[dict] = []
        days = LOOKBACK_DAYS if backfill else RECENT_DAYS
        for lo, hi in _windows(today - timedelta(days=days - 1), today + timedelta(days=1), WINDOW_DAYS):
            records += _records(await call(urls()["workouts"], startDate=_ymd(lo), endDate=_ymd(hi)))
        out["activities"] = await import_workouts(db, conn, client, token, records)
        if health:
            lo = today - timedelta(days=(DAILY_BACKFILL_DAYS if backfill else RECENT_DAYS) - 1)
            try:
                data = await call(urls()["daily"], startDate=_ymd(lo), endDate=_ymd(today + timedelta(days=1)))
                rows = [row for rec in _records(data) for row in daily_rows(rec)]
                out["daily"] = await store_daily(db, conn.user_id, rows, SOURCE)
            except base.ConnectorAuthError:
                raise
            except (base.ConnectorError, httpx.HTTPError) as e:  # its path or shape unknown yet: the workouts stay
                logger.info("COROS official daily data not read for user %d: %r", conn.user_id, e)
                out["daily"] = {"error": True}
    return out


async def run_sync(db, conn, *, backfill: bool = False) -> dict | None:
    """Claim, pull, record how it went (a plain French error shown in Réglages). None when another worker holds it,
    or the link is paused (to reconnect, its tokens unreadable)."""
    if conn.status != base.ACTIVE or not base.tokens_readable(conn) or not await claim(db, conn):
        return None
    try:
        result = await sync(db, conn, backfill=backfill)
        conn.last_sync_at = datetime.now(timezone.utc)
        conn.last_error = None
        outcome = {"ok": True, "result": result}
    except base.ConnectorAuthError as e:
        outcome = {"ok": False, "error": str(e)}
    except base.ConnectorKeyError as e:
        await db.rollback()
        outcome = {"ok": False, "error": str(e)}
    except (base.ConnectorError, httpx.HTTPError) as e:
        logger.warning("COROS official pull failed for user %d: %r", conn.user_id, e)
        await db.rollback()
        conn.last_error = str(e) if isinstance(e, base.ConnectorError) else "COROS ne répond pas pour l’instant."
        outcome = {"ok": False, "error": conn.last_error}
    conn.sync_claimed_at = None
    await db.commit()
    return outcome
