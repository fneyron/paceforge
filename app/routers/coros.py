"""COROS link from Réglages: OAuth connect/callback, manual sync, disconnect.

The OAuth state and PKCE verifier ride in the (signed) session cookie between
/coros/connect and /coros/callback, like the Strava setup credentials do.
"""
import logging
import secrets

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.templating import Jinja2Templates

from app.dependencies import get_current_user, get_db
from app.models.user import User
from app.services import coros

logger = logging.getLogger(__name__)
templates = Jinja2Templates(directory="app/templates")

router = APIRouter(tags=["coros"])

_SETTINGS = "/settings#coros"
_PENDING = "coros_oauth"


def _back(request: Request, error: str | None = None, ok: str | None = None) -> RedirectResponse:
    if error:
        request.session["coros_error"] = error
    if ok:
        request.session["coros_ok"] = ok
    return RedirectResponse(url=_SETTINGS, status_code=303)


@router.get("/coros/connect")
async def coros_connect(
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Discover COROS's OAuth setup, make sure PaceForge is registered there,
    then send the athlete to the COROS login."""
    retried = bool(request.session.pop("coros_retry", False))
    redirect = coros.redirect_uri()
    try:
        async with coros.http_client() as client:
            disc = await coros.discover(client)
            client_id = await coros.client_id_for(db, client, disc, redirect)
    except (coros.CorosError, httpx.HTTPError, ValueError, KeyError, IndexError):
        logger.exception("COROS connect failed for user %d", user.id)
        return _back(request, error="COROS ne répond pas pour l'instant. Réessaie dans quelques minutes.")
    await db.commit()  # the registered client is shared: keep it even if the athlete gives up

    verifier, challenge = coros.pkce_pair()
    state = secrets.token_urlsafe(24)
    request.session[_PENDING] = {
        "state": state, "verifier": verifier, "client_id": client_id, "retried": retried,
        "issuer": disc["issuer"], "resource": disc["resource"],
        "token_endpoint": disc["token_endpoint"], "revocation_endpoint": disc["revocation_endpoint"],
    }
    return RedirectResponse(url=coros.authorize_url(disc, client_id, redirect, state, challenge), status_code=302)


async def _retry_with_new_client(request: Request, db: AsyncSession, pending: dict) -> RedirectResponse | None:
    """COROS no longer knows our client: register a fresh one, once."""
    if pending.get("retried"):
        return None
    await coros.forget_client(db, pending["issuer"])
    await db.commit()
    request.session["coros_retry"] = True
    return RedirectResponse(url="/coros/connect", status_code=302)


@router.get("/coros/callback")
async def coros_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    pending = request.session.pop(_PENDING, None)
    if not pending or not state or not secrets.compare_digest(str(state), str(pending.get("state"))):
        return _back(request, error="La connexion à COROS a expiré. Réessaie.")
    if error:
        logger.info("COROS authorization error for user %d: %s", user.id, error)
        if error in ("invalid_client", "unauthorized_client"):
            retry = await _retry_with_new_client(request, db, pending)
            if retry:
                return retry
        if error == "access_denied":
            return _back(request, error="Connexion à COROS annulée.")
        return _back(request, error="COROS a refusé la connexion. Réessaie.")
    if not code:
        return _back(request, error="COROS n'a pas renvoyé d'autorisation. Réessaie.")

    try:
        await coros.complete_connection(db, user.id, pending, code)
    except coros.TokenError as e:
        logger.warning("COROS code exchange failed for user %d: %s", user.id, e.code)
        if e.code == "invalid_client":
            retry = await _retry_with_new_client(request, db, pending)
            if retry:
                return retry
        return _back(request, error="La connexion à COROS a échoué. Réessaie.")
    except (coros.CorosError, httpx.HTTPError):
        logger.exception("COROS code exchange failed for user %d", user.id)
        return _back(request, error="COROS ne répond pas pour l'instant. Réessaie dans quelques minutes.")
    await db.commit()  # the background sync reads the link from its own session
    coros.schedule_sync(user.id)
    return _back(request, ok="COROS connecté. Tes 60 derniers jours arrivent : compte une minute.")


@router.post("/settings/coros/sync", response_class=HTMLResponse)
async def coros_sync_now(
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """« Synchroniser maintenant »: runs the sync here and shows how it went."""
    conn = await coros.connection_for(db, user.id)
    outcome = await coros.run_sync(db, conn) if conn else None
    ctx = {"request": request, "coros": await coros.coros_status(db, user.id),
           "outcome": outcome or {"busy": True}}
    return templates.TemplateResponse(request, "partials/coros_status.html", context=ctx)


@router.post("/settings/coros/disconnect")
async def coros_disconnect(
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    conn = await coros.connection_for(db, user.id)
    if conn:
        await coros.disconnect(db, conn)
    return _back(request, ok="COROS déconnecté. Les données déjà reçues restent.")
