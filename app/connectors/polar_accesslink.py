"""Polar AccessLink v3: the athlete's exercises, announced by Polar's webhook when one is uploaded and read back
after connecting and twice a day (docs/official-connectors.md says what is verified and what is assumed). Read
only: nothing is ever written to Polar (registering the athlete with the client is the API's own step). Exercises
only: no daily activity, sleep or physical information is read, so no consent to health data is asked for (as
Strava).

What Polar publishes (www.polar.com/accesslink-api, public): OAuth 2.0 authorization code at
flow.polar.com/oauth2/authorization (response_type=code, client_id, redirect_uri, scope=accesslink.read_all, state);
the code exchanged at polarremote.com/v2/oauth2/token with the client's id and secret in HTTP Basic (form:
grant_type=authorization_code, code, redirect_uri); the answer holds access_token, expires_in (years: there is no
refresh token, a lapsed token means connecting again) and x_user_id (the athlete's Polar id, what the webhook names).
The athlete is then registered with the client: POST /v3/users {"member-id": …} (409: already registered, fine);
DELETE /v3/users/{x_user_id} undoes it (« Déconnecter », a deleted account). GET /v3/exercises lists the exercises
of the last 30 days, GET /v3/exercises/{id} one of them, GET /v3/exercises/{id}/fit its FIT file. One webhook per
client (not per athlete), created once by the owner (python -m app.connectors.polar_accesslink create-webhook):
POST /v3/webhooks {events: ["EXERCISE"], url}, its answer giving the signature_secret_key
(POLAR_ACCESSLINK_WEBHOOK_SECRET); each push is JSON {event: "EXERCISE" | "PING", user_id, entity_id, timestamp,
url}, signed in the Polar-Webhook-Signature header (hex HMAC-SHA256 of the raw body with that key).
ASSUMED until checked against a real exercise of the owner's account: the exercise's field names (id, start_time on
the athlete's clock with start_time_utc_offset in minutes, duration in ISO 8601, distance in metres, sport,
detailed_sport_info, heart_rate {average, maximum}, calories, ascent), the sport table (SPORTS, small: what is
unknown is a « Workout », as COROS and Garmin do), a PING answered 200 without its signature checked (Polar sends
one when the webhook is created, before PaceForge holds the key; it carries nothing). Where the FIT file is read
(public format: app.connectors.fit), its values take precedence over the exercise's.

Mapping: an exercise → Activity by its id (polar_activity_id, a short string; a twin another service brought is
linked, never duplicated), its laps in Strava's shape from the FIT file. Training load, running index and the like
(brand scores) are not read.
"""
import hashlib
import hmac
import logging
import re
import sys
from datetime import datetime, timedelta, timezone
from urllib.parse import quote, urlencode

import httpx

from app.config import settings
from app.connectors import base, fit

logger = logging.getLogger(__name__)

PROVIDER, NAME, SOURCE = "polar", "Polar", "Polar"
VIA = "polar_accesslink_api"
PKCE = False
HEALTH = False  # exercises only: no consent to health data asked for
BACKFILL = True  # the exercises of the last 30 days read right after connecting (in the worker)
CONNECTED = "Polar connecté. Tes séances des 30 derniers jours arrivent, puis chaque nouvelle séance après sa synchro."
SCOPE = "accesslink.read_all"
REFRESH_MARGIN = timedelta(days=1)  # no refresh token: a token about to lapse means connecting again
EXPIRED = "La connexion à Polar a expiré ou a été retirée : reconnecte-toi."

API_PATHS = {
    "users": "/v3/users",
    "exercises": "/v3/exercises",
    "webhooks": "/v3/webhooks",
}
FIT_PER_RUN = 10  # FIT files read per push or pull, newest first; the others at the next one
SYNC_EVERY = timedelta(hours=12)  # the webhook announces each new exercise; the pull catches a missed one
CLAIM_TTL = timedelta(minutes=15)
ENTITY_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")  # what a push may name as an exercise (it becomes a URL path)


def urls() -> dict[str, str]:
    api = settings.POLAR_ACCESSLINK_API_BASE_URL.rstrip("/")
    defaults = {"authorize": settings.POLAR_ACCESSLINK_AUTHORIZE_URL, "token": settings.POLAR_ACCESSLINK_TOKEN_URL,
                **{key: api + path for key, path in API_PATHS.items()}}
    return base.endpoints(defaults, settings.POLAR_ACCESSLINK_URLS)


def _client_auth() -> tuple[str, str]:
    return settings.POLAR_ACCESSLINK_CLIENT_ID, settings.POLAR_ACCESSLINK_CLIENT_SECRET


# ── OAuth ───────────────────────────────────────────────────────────────────

def authorize_url(state: str, challenge: str | None = None) -> str:
    params = {"response_type": "code", "client_id": settings.POLAR_ACCESSLINK_CLIENT_ID,
              "redirect_uri": base.redirect_uri(PROVIDER), "scope": SCOPE, "state": state}
    url = urls()["authorize"]
    return url + ("&" if "?" in url else "?") + urlencode(params, quote_via=quote)


async def exchange(client: httpx.AsyncClient, code: str, verifier: str | None = None) -> dict:
    return await base.token_request(client, urls()["token"], {
        "grant_type": "authorization_code", "code": code, "redirect_uri": base.redirect_uri(PROVIDER)},
        auth=_client_auth())


async def refresh(client: httpx.AsyncClient, refresh_token: str, current: str | None = None) -> dict:
    """Polar gives no refresh token: never reached for a Polar link (base.access_token asks to reconnect)."""
    raise base.TokenError("invalid_grant")


def _bearer(token: str, accept: str = "application/json") -> dict:
    return {"Authorization": f"Bearer {token}", "Accept": accept}


def member_id(user_id: int) -> str:
    """The opaque id PaceForge registers the athlete under at Polar: derived from the account id with the app's
    secret, never the e-mail (Polar learns nothing about the account from it)."""
    key = settings.SECRET_KEY.encode()
    return "pf-" + hmac.new(key, f"polar-member-id:{user_id}".encode(), hashlib.sha256).hexdigest()[:32]


async def register(client: httpx.AsyncClient, token: str, user_id: int) -> None:
    """The athlete registered with the client (Polar's step before any data): 200 or 201 done, 409 already done;
    anything else fails the connection (ConnectorError)."""
    r = await client.post(urls()["users"], json={"member-id": member_id(user_id)}, headers=_bearer(token))
    if r.status_code in (200, 201, 409):
        return
    logger.warning("Polar user registration answered %s", r.status_code)
    raise base.ConnectorError("Polar n’a pas accepté l’inscription de ton compte. Réessaie.")


async def identify(client: httpx.AsyncClient, tokens: dict,
                   account_id: int | None = None) -> tuple[str | None, str | None]:
    """(x_user_id, scope): the Polar id comes with the token answer; the athlete is registered with the client here
    (it needs PaceForge's account id: the member id is derived from it)."""
    uid = tokens.get("x_user_id")
    if uid in (None, "") or account_id is None:
        return None, None
    await register(client, str(tokens["access_token"]), account_id)
    return str(uid)[:128], str(tokens.get("scope") or SCOPE)


async def deregister(client: httpx.AsyncClient, conn, token: str) -> None:
    """The athlete's registration with the client deleted (DELETE /v3/users/{x_user_id})."""
    r = await client.delete(f"{urls()['users']}/{quote(str(conn.provider_user_id), safe='')}",
                            headers=_bearer(token))
    if r.status_code not in (200, 202, 204):
        logger.info("Polar user deletion answered %s", r.status_code)


def missing_permissions(scopes: str | None) -> list[str]:
    return []


# ── the webhook ─────────────────────────────────────────────────────────────

SIGNATURE_HEADER = "polar-webhook-signature"


class PayloadError(ValueError):
    pass


def signature_ok(raw: bytes, signature: str | None) -> bool:
    """Polar-Webhook-Signature against the hex HMAC-SHA256 of the raw body with POLAR_ACCESSLINK_WEBHOOK_SECRET
    (constant time); no secret configured refuses all."""
    secret = settings.POLAR_ACCESSLINK_WEBHOOK_SECRET.strip()
    if not secret or not signature:
        return False
    expected = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    return hmac.compare_digest(signature.strip().lower().encode(), expected.encode())


def parse_push(body) -> dict:
    """{event, user_id, entity_id} of a push; PayloadError when it is not an AccessLink event object, or names an
    exercise id that is not one."""
    if not isinstance(body, dict) or not isinstance(body.get("event"), str):
        raise PayloadError("not an event")
    if body["event"] != "EXERCISE":
        return {"event": body["event"]}
    uid, entity = body.get("user_id"), body.get("entity_id")
    if uid in (None, "") or not isinstance(entity, str) or not ENTITY_ID.fullmatch(entity):
        raise PayloadError("no exercise")
    return {"event": "EXERCISE", "user_id": str(uid), "entity_id": entity}


# ── exercises → Activity ────────────────────────────────────────────────────

# Polar's sport names (detailed_sport_info first, then sport) → Strava's sport types. ASSUMED (AccessLink's sport
# names as its examples write them): the FIT file's sport takes precedence whenever it is read; an unknown one is a
# « Workout ».
SPORTS = {
    "RUNNING": "Run", "ROAD_RUNNING": "Run", "TRAIL_RUNNING": "TrailRun", "TREADMILL_RUNNING": "Run",
    "TRACK_AND_FIELD_RUNNING": "Run", "INDOOR_RUNNING": "Run", "ULTRARUNNING_RUNNING": "TrailRun",
    "CYCLING": "Ride", "ROAD_BIKING": "Ride", "MOUNTAIN_BIKING": "MountainBikeRide", "INDOOR_CYCLING": "VirtualRide",
    "WALKING": "Walk", "HIKING": "Hike", "SWIMMING": "Swim", "POOL_SWIMMING": "Swim", "OPEN_WATER_SWIMMING": "Swim",
    "STRENGTH_TRAINING": "WeightTraining", "YOGA": "Yoga", "CROSS_COUNTRY_SKIING": "NordicSki", "ROWING": "Rowing",
    "INDOOR_ROWING": "Rowing",
}
INDOOR = {"TREADMILL_RUNNING", "INDOOR_RUNNING", "INDOOR_CYCLING", "INDOOR_ROWING"}
_DURATION = re.compile(r"P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?)?")


def duration_s(value) -> float | None:
    """An ISO 8601 duration (PT1H2M3S, PT2H44M45.123S) in seconds; None when it is not one."""
    m = _DURATION.fullmatch(str(value or "").strip())
    if not m or not any(m.groups()):
        return None
    d, h, mi, s = m.groups()
    return int(d or 0) * 86400 + int(h or 0) * 3600 + int(mi or 0) * 60 + float(s or 0)


def _num(v) -> float | None:
    if isinstance(v, bool) or v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f and abs(f) != float("inf") else None


def _start(rec: dict) -> tuple[datetime, float | None] | None:
    """(the start in UTC, the offset in seconds east of UTC): start_time is on the athlete's clock (no offset
    written), start_time_utc_offset its offset in minutes; an instant written with its offset is read as such."""
    try:
        t = datetime.fromisoformat(str(rec.get("start_time")).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    offset = _num(rec.get("start_time_utc_offset"))
    if offset is not None and abs(offset) > 15 * 60:
        offset = None
    if t.tzinfo is not None:
        return t.astimezone(timezone.utc), (offset * 60 if offset is not None else None)
    seconds = offset * 60 if offset is not None else 0.0
    return (t - timedelta(seconds=seconds)).replace(tzinfo=timezone.utc), (seconds if offset is not None else None)


def _sport(rec: dict) -> tuple[str, bool]:
    for key in ("detailed_sport_info", "sport"):
        name = str(rec.get(key) or "").upper()
        if name in SPORTS:
            return SPORTS[name], name in INDOOR
    return "Workout", False


def exercise_fields(rec: dict) -> dict | None:
    """The Activity columns of one exercise (field names assumed), in Strava's units; None when unreadable (no id,
    no start, no duration)."""
    eid = rec.get("id")
    if not isinstance(eid, (str, int)) or isinstance(eid, bool) or not ENTITY_ID.fullmatch(str(eid)):
        return None
    start = _start(rec)
    seconds = duration_s(rec.get("duration"))
    if start is None or not seconds or seconds <= 0:
        return None
    meters = _num(rec.get("distance")) or 0.0
    sport, indoor = _sport(rec)
    given = rec.get("heart_rate")
    hr: dict = given if isinstance(given, dict) else {}
    avg, peak = _num(hr.get("average")), _num(hr.get("maximum"))
    lo, hi = base.HR_PLAUSIBLE
    raw = {k: v for k, v in rec.items() if k not in ("polar_user",)}
    raw.update({"source": "polar", "via": VIA})
    if start[1] is not None:
        raw["utc_offset"] = start[1]  # the key Strava uses: the session's local day
    if indoor:
        raw["trainer"] = True
    return {
        "polar_activity_id": str(eid),
        "sport_type": sport,
        "name": str(rec.get("name") or "Activité Polar")[:255],
        "start_date": start[0],
        "distance": meters,
        "moving_time": round(seconds),
        "elapsed_time": round(seconds),
        "total_elevation_gain": _num(rec.get("ascent")) or 0.0,
        "average_speed": meters / seconds if meters else None,
        "average_heartrate": avg if avg and lo <= avg <= hi else None,
        "max_heartrate": peak if peak and lo <= peak <= hi else None,
        "calories": _num(rec.get("calories")),
        "raw_data": raw,
    }


async def _get(client: httpx.AsyncClient, url: str, token: str):
    """One API call. 401/403 raise PermissionError (no refresh possible: the caller asks to reconnect)."""
    r = await client.get(url, headers=_bearer(token))
    if r.status_code in (401, 403):
        raise PermissionError(r.status_code)
    if r.status_code == 404:
        return None
    if r.status_code >= 400:
        raise base.ConnectorError(f"Polar ne répond pas pour l’instant (erreur {r.status_code}).")
    if r.status_code == 204 or not r.content:
        return None
    try:
        return r.json()
    except ValueError as e:
        raise base.ConnectorError("Polar a renvoyé une réponse illisible.") from e


async def import_exercises(db, conn, client: httpx.AsyncClient, token: str, records: list[dict]) -> dict:
    """Exercises into Activity, newest first, each with its FIT file's values when it can be read (up to
    FIT_PER_RUN a run; one already read is not read again, nor overwritten by its summary's rougher values)."""
    rows, fits, kept = [], 0, 0
    readable = [f for rec in records if (f := exercise_fields(rec)) is not None]
    for fields in sorted(readable, key=lambda f: f["start_date"], reverse=True):
        wanted = await base.fit_wanted(db, conn.user_id, PROVIDER, fields)
        if wanted is None:
            kept += 1
            continue
        if wanted and fits < FIT_PER_RUN:
            fits += 1
            url = f"{urls()['exercises']}/{quote(fields['polar_activity_id'], safe='')}/fit"
            try:
                data = await base.download_fit(client, url, headers=_bearer(token, "*/*"))
                if data:
                    fields = base.with_fit(fields, fit.decode(data))
            except (fit.FitError, httpx.HTTPError):
                logger.info("Polar FIT file of an exercise not read (user %d)", conn.user_id)
        rows.append(fields)
    counts = await base.import_activities(db, conn.user_id, PROVIDER, rows) if rows else {}
    return {**counts, "kept": counts.get("kept", 0) + kept, "fit": fits}


# ── processing (the Celery worker: app.tasks.official_connectors) ───────────

async def process_push(db, conn, items: list[dict]) -> dict:
    """A push's exercises for one link: each read with the athlete's token (never from the push's url: PaceForge
    builds it), then imported."""
    async with base.http_client() as client:
        token = await base.access_token(db, conn, client)
        records = []
        for item in items:
            entity = str(item.get("entity_id") or "")
            if not ENTITY_ID.fullmatch(entity):
                continue
            try:
                rec = await _get(client, f"{urls()['exercises']}/{quote(entity, safe='')}", token)
            except PermissionError:
                raise await base.mark_reauth(db, conn, EXPIRED) from None
            if isinstance(rec, dict):
                records.append(rec)
        result = await import_exercises(db, conn, client, token, records)
    conn.last_push_at = datetime.now(timezone.utc)
    return result


async def sync(db, conn, *, backfill: bool = False) -> dict:
    """Read the exercises Polar lists (its last 30 days), the same right after connecting and later."""
    async with base.http_client() as client:
        token = await base.access_token(db, conn, client)
        try:
            data = await _get(client, urls()["exercises"], token)
        except PermissionError:
            raise await base.mark_reauth(db, conn, EXPIRED) from None
        if isinstance(data, dict):  # a list, or (assumed) an object holding one
            data = data.get("exercises") or data.get("data") or []
        records = [r for r in data if isinstance(r, dict)] if isinstance(data, list) else []
        return {"activities": await import_exercises(db, conn, client, token, records)}


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
        logger.warning("Polar official pull failed for user %d: %r", conn.user_id, e)
        await db.rollback()
        conn.last_error = str(e) if isinstance(e, base.ConnectorError) else "Polar ne répond pas pour l’instant."
        outcome = {"ok": False, "error": conn.last_error}
    conn.sync_claimed_at = None
    await db.commit()
    return outcome


# ── the client's webhook (once, by the owner) ───────────────────────────────

def webhook_url() -> str:
    return settings.BASE_URL.rstrip("/") + "/webhook/polar"


async def create_webhook(url: str | None = None) -> dict:
    """The client's one webhook (EXERCISE events) created at Polar: its answer holds signature_secret_key, to set
    as POLAR_ACCESSLINK_WEBHOOK_SECRET. The client's credentials in HTTP Basic; nothing about any athlete."""
    async with base.http_client() as client:
        r = await client.post(urls()["webhooks"], json={"events": ["EXERCISE"], "url": url or webhook_url()},
                              auth=httpx.BasicAuth(*_client_auth()), headers={"Accept": "application/json"})
    body = r.json() if r.content else {}
    if r.status_code not in (200, 201) or not isinstance(body, dict):
        raise base.ConnectorError(f"Polar a refusé la création du webhook (erreur {r.status_code}).")
    data = body.get("data")
    return data if isinstance(data, dict) else body


async def show_webhook() -> dict | list | None:
    """The client's webhook as Polar has it (GET /v3/webhooks): its url, events, whether it is active."""
    async with base.http_client() as client:
        r = await client.get(urls()["webhooks"], auth=httpx.BasicAuth(*_client_auth()),
                             headers={"Accept": "application/json"})
    if r.status_code == 204 or not r.content:
        return None
    if r.status_code != 200:
        raise base.ConnectorError(f"Polar ne répond pas pour l’instant (erreur {r.status_code}).")
    body = r.json()
    return body.get("data") if isinstance(body, dict) and "data" in body else body


def main(argv: list[str]) -> int:
    """python -m app.connectors.polar_accesslink create-webhook [url] | show-webhook (the owner, once: the client's
    credentials must be set; the key printed goes into Coolify as POLAR_ACCESSLINK_WEBHOOK_SECRET)."""
    import asyncio
    import json

    if not (settings.POLAR_ACCESSLINK_CLIENT_ID.strip() and settings.POLAR_ACCESSLINK_CLIENT_SECRET.strip()):
        print("POLAR_ACCESSLINK_CLIENT_ID and POLAR_ACCESSLINK_CLIENT_SECRET must be set.", file=sys.stderr)
        return 2
    if argv[:1] == ["create-webhook"]:
        print(json.dumps(asyncio.run(create_webhook(argv[1] if len(argv) > 1 else None)), indent=2))
        return 0
    if argv[:1] == ["show-webhook"]:
        print(json.dumps(asyncio.run(show_webhook()), indent=2))
        return 0
    print("usage: python -m app.connectors.polar_accesslink create-webhook [url] | show-webhook", file=sys.stderr)
    return 2


if __name__ == "__main__":  # pragma: no cover - an owner's command
    sys.exit(main(sys.argv[1:]))
