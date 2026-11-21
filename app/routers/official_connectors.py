"""The official COROS, Garmin, Wahoo and Polar connections (app.connectors): OAuth start and callback, disconnect, and
the providers' pushes. Every route answers 404 while its connector is not active (enabled, with its credentials).

URLs registered with the providers (docs/official-connectors.md):
https://paceforge.fr/connect/{coros,garmin,wahoo,polar}/callback for OAuth, https://paceforge.fr/webhook/coros,
https://paceforge.fr/webhook/garmin[/{summary type}], https://paceforge.fr/webhook/wahoo and
https://paceforge.fr/webhook/polar for the pushes. They never collide with the former links' (/coros/callback,
/connections/…, /garmin/…) nor Strava's webhook.

The pushes are answered fast: checked (COROS: its client id and secret in the headers; Garmin: its garmin-client-id
header, a summary type it sends, user ids PaceForge knows; Wahoo: the webhook token in the body; Polar: the HMAC
signature of the body), matched to their links, health items dropped for the athletes who have not consented, then
queued for the worker (app.tasks.official_connectors). Unknown users are
ignored without saying so (every accepted push gets the same answer). A broker that does not take the work answers
503: the provider sends it again (Garmin pushes each summary only once otherwise).
"""
import json
import logging

import httpx
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.exceptions import HTTPException

from app.connectors import NAMES, PROVIDERS, base, ready, visible_to
from app.dependencies import get_current_user, get_db
from app.models.user import User
from app.services import health_consent
from app.services.app_return import app_return, is_app_return, with_result

logger = logging.getLogger(__name__)

router = APIRouter(tags=["official-connectors"])


def _pending_key(provider: str) -> str:
    return f"official_{provider}_oauth"


def _settings_anchor(provider: str) -> str:
    return f"/settings#official-{provider}"


def _known(provider: str) -> None:
    if provider not in PROVIDERS or not ready(provider):
        raise HTTPException(status_code=404)


def _back(request: Request, provider: str, target: str | None, *, error: str | None = None, ok: str | None = None,
          result: str | None = None) -> RedirectResponse:
    url = target or _settings_anchor(provider)
    if is_app_return(url):  # the app's sign-in sheet closes on it: the outcome as a code, no flash
        return RedirectResponse(with_result(url, result or ("ok" if ok else "failed")), status_code=303)
    if error:
        request.session[f"official_{provider}_error"] = error
    if ok:
        request.session[f"official_{provider}_ok"] = ok
    return RedirectResponse(url, status_code=303)


def _next(provider: str, next_: str | None) -> str:
    if provider not in base.LEGACY:  # Wahoo and Polar: Réglages only (no app sheet, no connection page)
        return _settings_anchor(provider)
    if next_ == "app":  # started from the app's screen of that connection, in its sign-in sheet
        return app_return(provider)
    if next_ == "devices":
        return f"/connections/{provider}"
    return _settings_anchor(provider)


@router.get("/connect/{provider}")
async def connect(provider: str, request: Request, next: str | None = None, user: User = Depends(get_current_user)):
    """Send the athlete to the provider's consent page (state, and PKCE for Garmin). The consent to health data
    first for COROS and Garmin: they bring nights, HRV, heart rate (Wahoo and Polar bring activities only, as
    Strava: not asked)."""
    _known(provider)
    if not visible_to(provider, user.id):
        raise HTTPException(status_code=404)
    spec = base.module(provider)
    if spec.HEALTH and not health_consent.given(user):
        here = request.url.path + (f"?{request.url.query}" if request.url.query else "")
        return RedirectResponse(health_consent.consent_url(here), status_code=303)
    verifier, challenge = base.pkce_pair() if spec.PKCE else (None, None)
    state = base.new_state()
    request.session[_pending_key(provider)] = {
        "state": base.seal(state), "verifier": base.seal(verifier) if verifier else None,
        "next": _next(provider, next)}
    return RedirectResponse(spec.authorize_url(state, challenge), status_code=302)


@router.get("/connect/{provider}/callback")
async def callback(provider: str, request: Request, code: str | None = None, state: str | None = None,
                   error: str | None = None, user: User = Depends(get_current_user),
                   db: AsyncSession = Depends(get_db)):
    _known(provider)
    name, spec = NAMES[provider], base.module(provider)
    pending = request.session.pop(_pending_key(provider), None) or {}
    target = pending.get("next") if isinstance(pending, dict) else None
    expired = f"La connexion à {name} a expiré. Réessaie."
    if not isinstance(pending, dict) or not base.state_matches(state, base.unseal(pending.get("state"))):
        return _back(request, provider, target, error=expired, result="expired")
    verifier = base.unseal(pending.get("verifier")) if spec.PKCE else None
    if spec.PKCE and not verifier:
        return _back(request, provider, target, error=expired, result="expired")
    if error:
        logger.info("%s official authorization error for user %d: %s", name, user.id, str(error)[:40])
        if error == "access_denied":
            return _back(request, provider, target, error=f"Connexion à {name} annulée.", result="cancelled")
        return _back(request, provider, target, error=f"{name} a refusé la connexion. Réessaie.")
    if not code:
        return _back(request, provider, target, error=f"{name} n’a pas renvoyé d’autorisation. Réessaie.")
    try:
        async with base.http_client() as client:
            tokens = await spec.exchange(client, code, verifier)
            provider_user_id, scopes = await spec.identify(client, tokens, user.id)
    except base.TokenError as e:
        logger.warning("%s official code exchange failed for user %d: %s", name, user.id, e.code)
        return _back(request, provider, target, error=f"La connexion à {name} a échoué. Réessaie.")
    except (base.ConnectorError, httpx.HTTPError, ValueError, KeyError):
        logger.exception("%s official code exchange failed for user %d", name, user.id)
        return _back(request, provider, target,
                     error=f"{name} ne répond pas pour l’instant. Réessaie dans quelques minutes.")
    if not provider_user_id:
        logger.warning("%s official link of user %d: no provider user id", name, user.id)
        return _back(request, provider, target, error=f"La connexion à {name} a échoué. Réessaie.")
    try:
        conn = await base.store_connection(db, user.id, provider, provider_user_id, tokens, scopes)
    except base.AlreadyLinkedError as e:
        return _back(request, provider, target, error=str(e))
    await base.replace_legacy(db, user.id, provider)
    await db.commit()  # the worker reads the link from its own session
    if spec.BACKFILL:  # Garmin pushes what is new only (no history on this program)
        _queue_backfill(provider, conn.id)
    return _back(request, provider, target, ok=spec.CONNECTED)


def _queue_backfill(provider: str, conn_id: int) -> None:
    from app.tasks.official_connectors import SYNC_TASKS

    try:
        SYNC_TASKS[provider].apply_async(args=[conn_id, True], retry=False)
    except Exception:  # the hourly round pulls it then
        logger.exception("%s official backfill not queued for connection %d", NAMES[provider], conn_id)


@router.post("/connect/{provider}/disconnect")
async def disconnect(provider: str, request: Request, next: str = Form(""), user: User = Depends(get_current_user),
                     db: AsyncSession = Depends(get_db)):
    _known(provider)
    conn = await base.connection_for(db, user.id, provider)
    if conn is not None:
        await base.disconnect(db, conn)
        await db.commit()
    target = (f"/connections/{provider}" if next == "devices" and provider in base.LEGACY
              else _settings_anchor(provider))
    return _back(request, provider, target, ok=f"{NAMES[provider]} déconnecté. Les données déjà reçues restent.")


# ── pushes ──────────────────────────────────────────────────────────────────

def _json(raw: bytes):
    try:
        return json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        return None


def _queue(task, *args) -> bool:
    try:
        task.apply_async(args=list(args), retry=False)
        return True
    except Exception:
        logger.exception("Official connector push not queued (broker unavailable?)")
        return False


@router.post("/webhook/garmin")
@router.post("/webhook/garmin/{summary_type}")
async def garmin_webhook(request: Request, summary_type: str | None = None, db: AsyncSession = Depends(get_db)):
    from app.connectors import garmin_connect as garmin
    from app.tasks.official_connectors import garmin_push

    _known("garmin")
    if summary_type is not None and summary_type not in garmin.SUMMARY_TYPES:
        raise HTTPException(status_code=404)
    if not garmin.client_header_ok(request.headers.get("garmin-client-id")):
        logger.warning("Garmin push refused: client header missing or wrong")
        return JSONResponse({"detail": "Unauthorized"}, status_code=401)
    try:
        pushes = garmin.parse_push(_json(await request.body()), summary_type)
    except garmin.PayloadError:
        logger.warning("Garmin push refused: not a summary payload (%s)", summary_type or "root")
        return JSONResponse({"detail": "Bad request"}, status_code=400)
    for kind, items in pushes.items():
        if kind in garmin.IGNORED_TYPES or not items:
            continue
        links = await base.connections_of(db, "garmin", (i.get("userId") for i in items))
        health = kind in garmin.HEALTH_TYPES
        consent = {c.user_id: await health_consent.allowed(db, c.user_id) for c in links.values()} if health else {}
        kept = [(links[str(i["userId"])], i) for i in items if str(i["userId"]) in links]
        if health:  # no health data without the consent: not even queued
            kept = [(c, i) for c, i in kept if consent.get(c.user_id)]
        unknown = len(items) - sum(1 for i in items if str(i["userId"]) in links)
        if unknown:
            logger.info("Garmin push %s: %d item(s) of unknown users ignored", kind, unknown)
        if not kept:
            continue
        reduced = garmin.entry_items(kind, [i for _, i in kept])
        entries = [{"conn": c.id, "item": i} for (c, _), i in zip(kept, reduced, strict=True)]
        if not _queue(garmin_push, kind, entries):
            return JSONResponse({"detail": "Try again later"}, status_code=503)
    return JSONResponse({"status": "ok"})


@router.post("/webhook/coros")
async def coros_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    from app.connectors import coros_partner as coros
    from app.tasks.official_connectors import coros_push

    _known("coros")
    if not coros.push_authentic(request.headers):
        logger.warning("COROS push refused: client id or secret missing or wrong")
        return JSONResponse({"result": "1001", "message": "Unauthorized"}, status_code=401)
    try:
        items = coros.parse_push(_json(await request.body()))
    except coros.PayloadError:
        logger.warning("COROS push refused: no workout list")
        return JSONResponse({"result": "1002", "message": "Bad request"}, status_code=400)
    links = await base.connections_of(db, "coros", coros.open_ids(items))
    entries = [{"conn": links[coros.open_id(i)].id, "item": i} for i in items if coros.open_id(i) in links]
    if len(entries) < len(items):
        logger.info("COROS push: %d workout(s) of unknown users ignored", len(items) - len(entries))
    if entries and not _queue(coros_push, entries):
        return JSONResponse({"result": "5000", "message": "Try again later"}, status_code=503)
    return JSONResponse({"result": "0000", "message": "OK"})


@router.post("/webhook/wahoo")
async def wahoo_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    """Wahoo's workout_summary events: the webhook token checked (constant time), the athlete matched to a link,
    the summary queued. Other events, and unknown athletes, get the same answer and nothing more."""
    from app.connectors import wahoo_cloud as wahoo
    from app.tasks.official_connectors import wahoo_push

    _known("wahoo")
    body = _json(await request.body())
    if not wahoo.token_ok(body.get("webhook_token") if isinstance(body, dict) else None):
        logger.warning("Wahoo push refused: webhook token missing or wrong")
        return JSONResponse({"detail": "Unauthorized"}, status_code=401)
    try:
        _, uid, summary = wahoo.parse_push(body)
    except wahoo.PayloadError:
        logger.warning("Wahoo push refused: not an event")
        return JSONResponse({"detail": "Bad request"}, status_code=400)
    if summary is None or uid is None:
        return JSONResponse({"status": "ok"})
    links = await base.connections_of(db, "wahoo", [uid])
    if uid not in links:
        logger.info("Wahoo push: a workout of an unknown user ignored")
        return JSONResponse({"status": "ok"})
    if not _queue(wahoo_push, [{"conn": links[uid].id, "user": uid, "item": summary}]):
        return JSONResponse({"detail": "Try again later"}, status_code=503)
    return JSONResponse({"status": "ok"})


@router.post("/webhook/polar")
async def polar_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    """Polar's events: a PING answered (Polar sends one when the webhook is created, before PaceForge holds its key;
    it carries nothing), an EXERCISE only with a valid Polar-Webhook-Signature (HMAC-SHA256 of the raw body,
    constant time), its athlete matched to a link, the exercise id queued (the worker reads it from Polar)."""
    from app.connectors import polar_accesslink as polar
    from app.tasks.official_connectors import polar_push

    _known("polar")
    raw = await request.body()
    body = _json(raw)
    if isinstance(body, dict) and body.get("event") == "PING":
        return JSONResponse({"status": "ok"})
    if not polar.signature_ok(raw, request.headers.get(polar.SIGNATURE_HEADER)):
        logger.warning("Polar push refused: signature missing or wrong")
        return JSONResponse({"detail": "Unauthorized"}, status_code=401)
    try:
        event = polar.parse_push(body)
    except polar.PayloadError:
        logger.warning("Polar push refused: not an exercise event")
        return JSONResponse({"detail": "Bad request"}, status_code=400)
    if event["event"] != "EXERCISE":
        return JSONResponse({"status": "ok"})
    links = await base.connections_of(db, "polar", [event["user_id"]])
    if event["user_id"] not in links:
        logger.info("Polar push: an exercise of an unknown user ignored")
        return JSONResponse({"status": "ok"})
    entry = {"conn": links[event["user_id"]].id, "user": event["user_id"], "item": {"entity_id": event["entity_id"]}}
    if not _queue(polar_push, [entry]):
        return JSONResponse({"detail": "Try again later"}, status_code=503)
    return JSONResponse({"status": "ok"})
