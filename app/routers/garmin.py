"""Garmin link from Réglages and Santé: login on Garmin's own page (first
choice), or email + password then the MFA code (fallback), manual sync,
disconnect.

The page login: /garmin/authorize sends the athlete to Garmin with a state that
rides in the session cookie and in the return URL; /garmin/callback checks it
and exchanges the ticket. The password login runs in the background (app.services.garmin.start_login): the page
polls its state with HTMX. The ticket rides in the (signed) session cookie;
the password is never stored.
"""
import logging
import secrets
from urllib.parse import urlsplit

import httpx

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.templating import Jinja2Templates

from app.dependencies import get_current_user, get_db
from app.models.user import User
from app.services import garmin

logger = logging.getLogger(__name__)
templates = Jinja2Templates(directory="app/templates")

router = APIRouter(tags=["garmin"])

_SETTINGS = "/settings#garmin"
_TICKET = "garmin_login"
_SSO = "garmin_sso"


def _back(request: Request, error: str | None = None, ok: str | None = None) -> RedirectResponse:
    if error:
        request.session["garmin_error"] = error
    if ok:
        request.session["garmin_ok"] = ok
    return RedirectResponse(url=_SETTINGS, status_code=303)


@router.get("/garmin/authorize")
async def garmin_authorize(request: Request, region: str | None = None, user: User = Depends(get_current_user)):
    """Send the athlete to Garmin's sign-in page; Garmin comes back to /garmin/callback."""
    region = region if region in garmin.REGIONS else garmin.DEFAULT_REGION
    state = secrets.token_urlsafe(24)
    request.session[_SSO] = {"state": state, "region": region}
    domain = garmin.region_domain(region)
    return RedirectResponse(url=garmin.sso_url(domain, garmin.sso_service(state)), status_code=302)


@router.get("/garmin/callback")
async def garmin_callback(
    request: Request,
    state: str | None = None,
    ticket: str | None = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    pending = request.session.pop(_SSO, None)
    if not pending or not state or not secrets.compare_digest(str(state), str(pending.get("state"))):
        return _back(request, error="La connexion à Garmin a expiré. Réessaie.")
    if not ticket or not ticket.startswith("ST-"):
        return _back(request, error="Garmin n'a pas renvoyé d'autorisation. Réessaie.")
    domain = garmin.region_domain(pending.get("region"))
    try:
        tokens = await garmin.exchange_ticket(domain, ticket, garmin.sso_service(state))
    except garmin.GarminError as e:
        return _back(request, error=str(e))
    except httpx.HTTPError:
        logger.exception("Garmin ticket exchange failed for user %d", user.id)
        return _back(request, error="Garmin ne répond pas pour l'instant. Réessaie dans quelques minutes.")
    await garmin.save_connection(db, user.id, domain, tokens)
    await db.commit()  # the background sync reads the link from its own session
    garmin.schedule_sync(user.id)
    return _back(request, ok="Garmin connecté. Tes 60 derniers jours et tes séances arrivent : compte quelques minutes.")


def _login_partial(request: Request, state: dict | None, region: str | None = None) -> HTMLResponse:
    return templates.TemplateResponse(request, "partials/garmin_login.html", context={
        "request": request, "login": state, "region": region or garmin.DEFAULT_REGION,
        "regions": garmin.REGIONS})


def _done(request: Request) -> Response:
    """The link is stored: back to Réglages, where the first sync shows."""
    request.session.pop(_TICKET, None)
    request.session["garmin_ok"] = "Garmin connecté. Tes 60 derniers jours et tes séances arrivent : compte quelques minutes."
    return Response(status_code=204, headers={"HX-Redirect": _SETTINGS})


@router.post("/garmin/connect", response_class=HTMLResponse)
async def garmin_connect(
    request: Request,
    email: str = Form(""),
    password: str = Form(""),
    region: str = Form(garmin.DEFAULT_REGION),
    user: User = Depends(get_current_user),
):
    region = region if region in garmin.REGIONS else garmin.DEFAULT_REGION
    if not email.strip() or not password:
        return _login_partial(request, {"state": "error", "message": "Indique ton email et ton mot de passe Garmin."},
                              region)
    try:
        ticket = await garmin.start_login(user.id, email, password, region)
    except Exception:
        logger.exception("Garmin login could not start for user %d", user.id)
        return _login_partial(request, {"state": "error", "message": "La connexion à Garmin n'a pas pu démarrer. Réessaie."},
                              region)
    request.session[_TICKET] = ticket
    return _login_partial(request, {"state": "running"}, region)


@router.get("/garmin/login", response_class=HTMLResponse)
async def garmin_login_state(request: Request, user: User = Depends(get_current_user)):
    """Polled by the login partial until the login is done, fails or needs a code."""
    state = await garmin.login_state(request.session.get(_TICKET), user.id)
    if state is None:
        return _login_partial(request, {"state": "error", "message": "La connexion à Garmin a expiré. Recommence."})
    if state["state"] == "done":
        return _done(request)
    return _login_partial(request, state)


@router.post("/garmin/mfa", response_class=HTMLResponse)
async def garmin_mfa(request: Request, code: str = Form(""), user: User = Depends(get_current_user)):
    ticket = request.session.get(_TICKET)
    if not code.strip().isdigit():
        state = await garmin.login_state(ticket, user.id)
        if state and state["state"] == "mfa":
            return _login_partial(request, {"state": "mfa", "message": "Le code est fait de chiffres."})
    if not await garmin.submit_code(ticket, user.id, code):
        return _login_partial(request, {"state": "error", "message": "La connexion à Garmin a expiré. Recommence."})
    return _login_partial(request, {"state": "running", "message": "Vérification du code…"})


@router.post("/settings/garmin/sync", response_class=HTMLResponse)
async def garmin_sync_now(
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """« Synchroniser maintenant »: runs the sync here and shows how it went."""
    conn = await garmin.connection_for(db, user.id)
    outcome = await garmin.run_sync(db, conn) if conn else None
    ctx = {"request": request, "garmin": await garmin.garmin_status(db, user.id),
           "outcome": outcome or {"busy": True}}
    response = templates.TemplateResponse(request, "partials/garmin_status.html", context=ctx)
    # from the Santé page: reload it, so the new values show
    if outcome and outcome.get("ok") and urlsplit(request.headers.get("HX-Current-URL", "")).path == "/sante":
        response.headers["HX-Refresh"] = "true"
    return response


@router.post("/settings/garmin/disconnect")
async def garmin_disconnect(
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    conn = await garmin.connection_for(db, user.id)
    if conn:
        await garmin.disconnect(db, conn)
    request.session["garmin_ok"] = "Garmin déconnecté. Les données déjà reçues restent."
    return RedirectResponse(url=_SETTINGS, status_code=303)
