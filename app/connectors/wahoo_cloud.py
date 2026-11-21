"""Wahoo Cloud API: the athlete's workouts, pushed by Wahoo when one is saved (webhook) and read back after connecting
and twice a day (docs/official-connectors.md says what is verified and what is assumed). Read only: nothing is ever
written to Wahoo. Activities only: no health data, so no consent to health data is asked for (as Strava).

What Wahoo publishes (cloud-api.wahooligan.com, public): OAuth 2.0 authorization code at
api.wahooligan.com/oauth/authorize (client_id, redirect_uri, response_type=code, scope, state), the code exchanged
at /oauth/token (form: client_id, client_secret, code, grant_type=authorization_code, redirect_uri), an access token
of about two hours, refreshed with grant_type=refresh_token (a NEW refresh token each time: rotation, committed at
once by base.access_token); scopes asked: user_read (the athlete's id), workouts_read (the workouts and their FIT
file), offline_data (the webhook and refreshing without the athlete). GET /v1/user → {id, …}; GET
/v1/workouts?page=&per_page= → {workouts: [{id, starts, minutes, name, workout_type_id, workout_summary: {id,
distance_accum, duration_active_accum, duration_total_accum, heart_rate_avg, ascent_accum, calories_accum,
power_avg, file: {url}}}]}; the webhook POSTs {event_type: "workout_summary", webhook_token, user: {id},
workout_summary: {…, file: {url}, workout: {id, starts, minutes, workout_type_id, …}}}; DELETE /v1/permissions with
the athlete's token revokes PaceForge's access (« Déconnecter », a deleted account).
ASSUMED until checked against a real workout of the owner's account (each a setting or one place below): the
numbers may come as strings (Wahoo's decimals: read either way), distance in metres, durations in seconds, the start
an ISO 8601 instant in UTC, the FIT file on a wahooligan.com host (WAHOO_CLOUD_FIT_HOSTS), the workout type table
(WORKOUT_TYPES, small: what is unknown is a « Workout », as COROS and Garmin do), the webhook answered by any 2xx.
Where the FIT file is read (public format: app.connectors.fit), its values take precedence over the summary's.

Mapping: a workout → Activity by its workout id (wahoo_activity_id; a twin another service brought is linked, never
duplicated), its laps in Strava's shape from the FIT file. Nothing else is read (no TSS, no normalized power kept as
a brand score).
"""
import logging
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import quote, urlencode

import httpx

from app.config import settings
from app.connectors import base, fit

logger = logging.getLogger(__name__)

PROVIDER, NAME, SOURCE = "wahoo", "Wahoo", "Wahoo"
VIA = "wahoo_cloud_api"
PKCE = False
HEALTH = False  # activities only: no consent to health data asked for
BACKFILL = True  # the recent workouts read right after connecting (in the worker)
CONNECTED = "Wahoo connecté. Tes séances récentes arrivent, puis chaque nouvelle séance dès qu’elle est enregistrée."
SCOPES = "user_read workouts_read offline_data"
REFRESH_MARGIN = timedelta(minutes=10)  # access tokens last about two hours
EXPIRED = "La connexion à Wahoo a expiré ou a été retirée : reconnecte-toi."

API_PATHS = {
    "authorize": "/oauth/authorize",
    "token": "/oauth/token",
    "user": "/v1/user",
    "workouts": "/v1/workouts",
    "permissions": "/v1/permissions",
}
PER_PAGE = 30
BACKFILL_PAGES = 4  # 120 workouts right after connecting, newest first
RECENT_PAGES = 1
FIT_PER_RUN = 10  # FIT files read per push or pull, newest first; the others at the next one
SYNC_EVERY = timedelta(hours=12)  # the webhook brings each new workout; the pull catches a missed one
CLAIM_TTL = timedelta(minutes=15)


def urls() -> dict[str, str]:
    root = settings.WAHOO_CLOUD_BASE_URL.rstrip("/")
    return base.endpoints({key: root + path for key, path in API_PATHS.items()}, settings.WAHOO_CLOUD_URLS)


# ── OAuth ───────────────────────────────────────────────────────────────────

def authorize_url(state: str, challenge: str | None = None) -> str:
    params = {"client_id": settings.WAHOO_CLOUD_CLIENT_ID, "redirect_uri": base.redirect_uri(PROVIDER),
              "response_type": "code", "scope": SCOPES, "state": state}
    url = urls()["authorize"]
    return url + ("&" if "?" in url else "?") + urlencode(params, quote_via=quote)


async def exchange(client: httpx.AsyncClient, code: str, verifier: str | None = None) -> dict:
    return await base.token_request(client, urls()["token"], {
        "client_id": settings.WAHOO_CLOUD_CLIENT_ID, "client_secret": settings.WAHOO_CLOUD_CLIENT_SECRET,
        "code": code, "grant_type": "authorization_code", "redirect_uri": base.redirect_uri(PROVIDER)})


async def refresh(client: httpx.AsyncClient, refresh_token: str, current: str | None = None) -> dict:
    """A new pair (Wahoo rotates the refresh token: committed at once by base.access_token)."""
    return await base.token_request(client, urls()["token"], {
        "client_id": settings.WAHOO_CLOUD_CLIENT_ID, "client_secret": settings.WAHOO_CLOUD_CLIENT_SECRET,
        "grant_type": "refresh_token", "refresh_token": refresh_token})


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}", "Accept": "application/json"}


async def identify(client: httpx.AsyncClient, tokens: dict,
                   account_id: int | None = None) -> tuple[str | None, str | None]:
    """(Wahoo user id, the scopes granted): the id read at /v1/user (what the webhook names)."""
    r = await client.get(urls()["user"], headers=_bearer(str(tokens["access_token"])))
    if r.status_code != 200:
        return None, None
    try:
        uid = r.json().get("id")
    except (ValueError, AttributeError):
        return None, None
    scope = tokens.get("scope")
    return (str(uid)[:128] if uid not in (None, "") else None), (str(scope) if scope else SCOPES)


async def deregister(client: httpx.AsyncClient, conn, token: str) -> None:
    """PaceForge's access revoked at Wahoo (DELETE /v1/permissions)."""
    r = await client.delete(urls()["permissions"], headers=_bearer(token))
    if r.status_code not in (200, 202, 204):
        logger.info("Wahoo permissions deletion answered %s", r.status_code)


def missing_permissions(scopes: str | None) -> list[str]:
    """Wahoo grants the scopes asked as a whole: nothing to say."""
    return []


# ── the webhook ─────────────────────────────────────────────────────────────

class PayloadError(ValueError):
    pass


def token_ok(value) -> bool:
    """The push's webhook_token against WAHOO_CLOUD_WEBHOOK_TOKEN (constant time); none configured refuses all."""
    expected = settings.WAHOO_CLOUD_WEBHOOK_TOKEN.strip()
    if not expected or not isinstance(value, str) or not value:
        return False
    return secrets.compare_digest(value.strip().encode(), expected.encode())


def parse_push(body) -> tuple[str, str | None, dict | None]:
    """(event_type, Wahoo user id, workout summary) of a push; PayloadError when it is not a Wahoo event object. An
    event other than workout_summary comes back without a summary (acknowledged, ignored)."""
    if not isinstance(body, dict) or not isinstance(body.get("event_type"), str):
        raise PayloadError("not an event")
    user = body.get("user")
    uid = user.get("id") if isinstance(user, dict) else None
    summary = body.get("workout_summary")
    if body["event_type"] != "workout_summary" or not isinstance(summary, dict):
        return body["event_type"], None, None
    return body["event_type"], (str(uid) if uid not in (None, "") else None), summary


# ── workouts → Activity ─────────────────────────────────────────────────────

# Wahoo workout_type_id → Strava's sport types. ASSUMED (Wahoo's public table of workout types, kept small): the
# FIT file's sport takes precedence whenever it is read; an unknown type is a « Workout ».
WORKOUT_TYPES: dict[int, str] = {
    # running, track, trail, treadmill, race, indoor virtual run
    1: "Run", 3: "Run", 4: "TrailRun", 5: "Run", 67: "Run", 71: "Run",
    0: "Ride", 11: "Ride", 13: "MountainBikeRide", 15: "Ride", 16: "Ride", 64: "EBikeRide",
    12: "VirtualRide", 49: "VirtualRide", 61: "VirtualRide", 68: "VirtualRide",
    6: "Walk", 7: "Walk", 56: "Walk", 9: "Hike", 25: "Swim", 26: "Swim", 39: "Rowing", 22: "Rowing", 66: "Yoga",
}
INDOOR_TYPES = {5, 71, 12, 49, 61, 68, 56, 22}  # treadmill, virtual run, indoor rides, treadmill walk, rower


def _num(v) -> float | None:
    if isinstance(v, bool) or v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f and abs(f) != float("inf") else None


def _instant(v) -> datetime | None:
    try:
        t = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def split(rec: dict) -> tuple[dict, dict]:
    """(workout, summary) of a record: a workout of /v1/workouts (its workout_summary inside), or a webhook's
    workout_summary (its workout inside)."""
    if isinstance(rec.get("workout"), dict):
        return rec["workout"], rec
    summary = rec.get("workout_summary")
    return rec, summary if isinstance(summary, dict) else {}


def file_url(rec: dict) -> str | None:
    _, summary = split(rec)
    f = summary.get("file")
    url = f.get("url") if isinstance(f, dict) else None
    return str(url) if url else None


def workout_fields(rec: dict) -> dict | None:
    """The Activity columns of one workout (field names as Wahoo publishes them, units assumed), in Strava's units;
    None when unreadable (no id, no start, no duration)."""
    workout, summary = split(rec)
    try:
        wid = int(str(workout.get("id")))
    except (TypeError, ValueError):
        return None
    start = _instant(workout.get("starts"))
    if wid <= 0 or start is None:
        return None
    minutes = _num(workout.get("minutes"))
    moving = _num(summary.get("duration_active_accum")) or (minutes * 60 if minutes else None) or 0
    if moving <= 0:
        return None
    elapsed = max(_num(summary.get("duration_total_accum")) or 0, moving)
    meters = _num(summary.get("distance_accum")) or 0.0
    type_id = _num(workout.get("workout_type_id"))
    kind = int(type_id) if type_id is not None else None
    raw = {k: v for k, v in workout.items() if k not in ("workout_summary", "workout_token")}
    raw["summary"] = {k: v for k, v in summary.items() if k not in ("file", "workout")}
    raw.update({"source": "wahoo", "via": VIA})
    if kind in INDOOR_TYPES:
        raw["trainer"] = True
    hr = _num(summary.get("heart_rate_avg"))
    return {
        "wahoo_activity_id": wid,
        "sport_type": WORKOUT_TYPES.get(kind, "Workout") if kind is not None else "Workout",
        "name": str(workout.get("name") or "Activité Wahoo")[:255],
        "start_date": start,
        "distance": meters,
        "moving_time": round(moving),
        "elapsed_time": round(elapsed),
        "total_elevation_gain": _num(summary.get("ascent_accum")) or 0.0,
        "average_speed": meters / moving if meters else None,
        "average_heartrate": hr if hr and base.HR_PLAUSIBLE[0] <= hr <= base.HR_PLAUSIBLE[1] else None,
        "max_heartrate": None,
        "average_watts": _num(summary.get("power_avg")),
        "calories": _num(summary.get("calories_accum")),
        "raw_data": raw,
    }


def fit_host_allowed(url: str | None) -> bool:
    return base.host_allowed(url, settings.WAHOO_CLOUD_FIT_HOSTS)


async def import_workouts(db, conn, client: httpx.AsyncClient, records: list[dict]) -> dict:
    """Workouts into Activity, newest first, each with its FIT file's values when it can be read (up to FIT_PER_RUN
    a run; one already read is not read again, nor overwritten by its summary's rougher values)."""
    rows, fits, kept = [], 0, 0
    readable = [(rec, f) for rec in records if (f := workout_fields(rec)) is not None]
    for rec, fields in sorted(readable, key=lambda x: x[1]["start_date"], reverse=True):
        wanted = await base.fit_wanted(db, conn.user_id, PROVIDER, fields)
        if wanted is None:
            kept += 1
            continue
        url = file_url(rec)
        if wanted and url and fits < FIT_PER_RUN:
            if not fit_host_allowed(url):
                logger.info("Wahoo FIT file on a host not allowed: skipped")
            else:
                fits += 1
                try:
                    data = await base.download_fit(client, url)
                    if data:
                        fields = base.with_fit(fields, fit.decode(data))
                except (fit.FitError, httpx.HTTPError):
                    logger.info("Wahoo FIT file of a workout not read (user %d)", conn.user_id)
        rows.append(fields)
    counts = await base.import_activities(db, conn.user_id, PROVIDER, rows) if rows else {}
    return {**counts, "kept": counts.get("kept", 0) + kept, "fit": fits}


# ── processing (the Celery worker: app.tasks.official_connectors) ───────────

async def process_push(db, conn, records: list[dict]) -> dict:
    """A push's workout summaries for one link (the FIT file sits on Wahoo's CDN: no token needed)."""
    async with base.http_client() as client:
        result = await import_workouts(db, conn, client, records)
    conn.last_push_at = datetime.now(timezone.utc)
    return result


async def _page(client: httpx.AsyncClient, token: str, page: int) -> list[dict]:
    r = await client.get(urls()["workouts"], params={"page": page, "per_page": PER_PAGE}, headers=_bearer(token))
    if r.status_code in (401, 403):
        raise PermissionError(r.status_code)
    if r.status_code >= 400:
        raise base.ConnectorError(f"Wahoo ne répond pas pour l’instant (erreur {r.status_code}).")
    try:
        body = r.json()
    except ValueError as e:
        raise base.ConnectorError("Wahoo a renvoyé une réponse illisible.") from e
    items = body.get("workouts") if isinstance(body, dict) else body
    return [w for w in items if isinstance(w, dict)] if isinstance(items, list) else []


async def sync(db, conn, *, backfill: bool = False) -> dict:
    """Read the latest workouts (a few pages right after connecting). A 401 refreshes once."""
    async with base.http_client() as client:
        token = await base.access_token(db, conn, client)
        records: list[dict] = []
        for page in range(1, (BACKFILL_PAGES if backfill else RECENT_PAGES) + 1):
            try:
                got = await _page(client, token, page)
            except PermissionError:
                token = await base.access_token(db, conn, client, rejected=token)
                try:
                    got = await _page(client, token, page)
                except PermissionError:
                    raise await base.mark_reauth(db, conn, EXPIRED) from None
            records += got
            if len(got) < PER_PAGE:
                break
        return {"activities": await import_workouts(db, conn, client, records)}


async def run_sync(db, conn, *, backfill: bool = False) -> dict | None:
    """Claim, pull, record how it went (a plain French error shown in Réglages). None when another worker holds it,
    or the link is paused (to reconnect, its tokens unreadable)."""
    if conn.status != base.ACTIVE or not base.tokens_readable(conn) or not await base.claim(db, conn, CLAIM_TTL):
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
        logger.warning("Wahoo official pull failed for user %d: %r", conn.user_id, e)
        await db.rollback()
        conn.last_error = str(e) if isinstance(e, base.ConnectorError) else "Wahoo ne répond pas pour l’instant."
        outcome = {"ok": False, "error": conn.last_error}
    conn.sync_claimed_at = None
    await db.commit()
    return outcome
