import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool

from app.config import GOOGLE_SITE_VERIFICATION, settings
from app.crypto import encrypt_secret
from app.dependencies import get_current_user, get_db, get_optional_user, sign_in
from app.models.mobile import MobileDevice
from app.models.user import User
from app.services import sessions
from app.services.app_return import app_return, is_app_return, with_result
from app.services.auth import (
    DUMMY_HASH,
    PASSWORD_PREFIX,
    TOO_MANY,
    client_ip,
    count_attempt,
    forget_account_attempts,
    forget_attempts,
    generate_token,
    hash_password_async,
    hash_token,
    over_quota,
    valid_email,
    valid_verification_token,
    verification_token,
    verify_password_async,
)
from app.services.email import send_password_reset_email, send_verification_email
from app.services.oauth_attempts import begin_strava, consume_strava
from app.services.strava import StravaService

logger = logging.getLogger(__name__)

router = APIRouter(tags=["auth"])
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))
templates.env.globals["google_site_verification"] = GOOGLE_SITE_VERIFICATION  # /privacy's head
HOME = "/sante"  # Santé is the main dashboard
# Where a sign-in may lead instead of Santé: a connection page asked for before the login, or the race plans (the
# method page's « Créer mon plan », carried through sign-up and the email check in the session). Nothing else.
AFTER_LOGIN = {"coros": "/connections/coros", "garmin": "/connections/garmin", "strava": "/connections/strava",
               "simulator": "/simulator"}
_AFTER_LOGIN_KEY = "after_login"


def _next(value: str | None) -> str:
    """`value` when it is a known « next », else nothing (« /simulator » is read as « simulator »)."""
    value = (value or "").strip()
    value = "simulator" if value == "/simulator" else value
    return value if value in AFTER_LOGIN else ""


def _same_site(request: Request) -> None:
    """Login CSRF: an auth form (login, sign-up, password reset) posted from another site is refused, so no page
    elsewhere can sign this browser into an account of its choosing. Its own pages (the iPhone app's WebView
    loads them from paceforge.fr too) send Sec-Fetch-Site same-origin and their own Origin; a browser or a
    script sending neither header goes through."""
    if request.headers.get("sec-fetch-site") == "cross-site":
        raise HTTPException(status_code=403)
    origin = request.headers.get("origin")
    if origin is not None:
        ours = {host for host in (urlsplit(settings.BASE_URL).netloc, request.headers.get("host")) if host}
        if urlsplit(origin).netloc not in ours:  # another site's, or « null » (a sandboxed frame)
            raise HTTPException(status_code=403)


# ---------------------------------------------------------------------------
# Register
# ---------------------------------------------------------------------------

@router.get("/auth/register", response_class=HTMLResponse)
async def register_page(request: Request, next: str = ""):
    return templates.TemplateResponse(request, "auth/register.html", context={"next": _next(next)})


@router.post("/auth/register", dependencies=[Depends(_same_site)])
async def register(
    request: Request,
    db: AsyncSession = Depends(get_db),
    email: str = Form(...),
    password: str = Form(...),
    firstname: str = Form(default=""),
    lastname: str = Form(default=""),
    next: str = Form(default=""),
):
    email = email.strip().lower()
    firstname = firstname.strip()
    lastname = lastname.strip()
    next = _next(next)
    typed = {"email": email, "firstname": firstname, "lastname": lastname, "next": next}

    if not valid_email(email) or not password or len(firstname) > 100 or len(lastname) > 100:
        return templates.TemplateResponse(
            request, "auth/register.html",
            context={"error": "Vérifie ton adresse email et tes informations.",
                     "error_field": "email" if not valid_email(email) else None, **typed},
        )

    if not 8 <= len(password) <= 1024:
        return templates.TemplateResponse(
            request, "auth/register.html",
            context={"error": "Le mot de passe doit contenir entre 8 et 1 024 caractères.", "error_field": "password",
                     **typed},
        )

    if await count_attempt("register", client_ip(request), email):
        return templates.TemplateResponse(
            request, "auth/register.html", status_code=429,
            context={"error": TOO_MANY, **typed},
        )

    # Check if email already exists
    result = await db.execute(select(User).where(User.email == email))
    if result.scalar_one_or_none():
        return templates.TemplateResponse(
            request, "auth/register.html",
            context={"error": "Un compte existe déjà avec cet email.", "error_field": "email", **typed},
        )

    verify_token = verification_token()
    user = User(
        email=email,
        password_hash=await hash_password_async(password),
        firstname=firstname or None,
        lastname=lastname or None,
        email_verify_token=hash_token(verify_token),  # the email gets the token, the table its hash
    )
    db.add(user)
    try:
        await db.flush()
    except IntegrityError:  # the form sent twice at once (a double click): the first request made the account
        await db.rollback()
        return templates.TemplateResponse(
            request, "auth/register.html",
            context={"error": "Un compte existe déjà avec cet email.", "error_field": "email", **typed},
        )
    await db.refresh(user)
    await over_quota("resend", user=user.id)  # this first email counts for the resend's minute
    # committed before the email goes and before the redirect: the link is never dead on arrival, and get_db's
    # own commit comes only after the response (the browser follows the redirect at once)
    await db.commit()

    # Send verification email (SMTP blocks: off the event loop)
    sent = await run_in_threadpool(send_verification_email, email, verify_token)
    request.session.clear()
    request.session["pending_user_id"] = user.id
    request.session["verification_failed"] = not sent
    if next:  # led there after the email check and the first login, in this browser
        request.session[_AFTER_LOGIN_KEY] = next
    logger.info("User %d registered", user.id)

    return RedirectResponse(url="/auth/check-email", status_code=302)


@router.get("/auth/check-email", response_class=HTMLResponse)
async def check_email_page(request: Request):
    return templates.TemplateResponse(request, "auth/check_email.html", context={
        "can_resend": bool(request.session.get("pending_user_id")),
        "failed": request.session.pop("verification_failed", False),
    })


@router.post("/auth/resend-verification")
async def resend_verification(request: Request, db: AsyncSession = Depends(get_db)):
    user_id = request.session.get("pending_user_id")
    user = await db.get(User, user_id) if user_id else None
    # one email a minute per account, the sign-up's own counted (the stored token is a hash: it tells no age)
    if user and user.email and not user.email_verified and not await over_quota("resend", user=user.id):
        token = verification_token()
        user.email_verify_token = hash_token(token)
        await db.flush()
        sent = await run_in_threadpool(send_verification_email, user.email, token)
        request.session["verification_failed"] = not sent
    return RedirectResponse("/auth/check-email", status_code=303)


@router.get("/auth/verify-email")
async def verify_email(
    request: Request,
    token: str,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(User).where(User.email_verify_token == hash_token(token))
    )
    user = result.scalar_one_or_none()

    if not user or (not user.email_verified and not valid_verification_token(token)):
        return templates.TemplateResponse(
            request, "auth/verify_result.html",
            context={"success": False, "error": "Lien invalide ou expiré."},
        )

    if not user.email_verified:
        user.email_verified = True
        await db.flush()
        logger.info("Email verified for user %d", user.id)
    # the token stays on the account: the link opened again (a double click, an email client that fetched it
    # first) finds the account verified and says so, not « Lien invalide »

    # The link verifies, it never signs in: anyone can send someone the link of an account of their own
    # (login CSRF). A browser already signed in to that account goes on; any other signs in first
    # (« Continuer » leads to the login page).
    if request.session.get("user_id") == user.id:
        return RedirectResponse(url=HOME, status_code=302)
    return templates.TemplateResponse(request, "auth/verify_result.html", context={"success": True})


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------

@router.get("/auth/login", response_class=HTMLResponse)
async def login_page(request: Request, next: str = ""):
    return templates.TemplateResponse(request, "auth/login.html", context={
        "next": _next(next) or _next(request.session.get(_AFTER_LOGIN_KEY))})


@router.post("/auth/login", dependencies=[Depends(_same_site)])
async def login(
    request: Request,
    db: AsyncSession = Depends(get_db),
    email: str = Form(...),
    password: str = Form(...),
    next: str = Form(""),
):
    email = email.strip().lower()
    next = _next(next) or _next(request.session.get(_AFTER_LOGIN_KEY))
    ip = client_ip(request)
    # counted before anything else: parallel attempts cannot slip past the limit, and a refused one costs
    # neither a database connection nor a password check
    if await count_attempt("login", ip, email):
        return templates.TemplateResponse(
            request, "auth/login.html", status_code=429,
            context={"error": TOO_MANY, "email": email, "next": next},
        )

    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()

    # an unknown email is checked too, against a hash of nothing: the answer takes as long either way
    known = bool(user and user.password_hash)
    if not (await verify_password_async(password, user.password_hash if known else DUMMY_HASH) and known):
        return templates.TemplateResponse(
            request, "auth/login.html",
            context={"error": "Email ou mot de passe incorrect.", "error_field": "credentials", "email": email,
                     "next": next},
        )
    await forget_attempts("login", ip, email)

    if not user.password_hash.startswith(PASSWORD_PREFIX):
        user.password_hash = await hash_password_async(password)
        await db.flush()
    request.session.clear()
    if not user.email_verified:
        request.session["pending_user_id"] = user.id
        if next:
            request.session[_AFTER_LOGIN_KEY] = next
        return RedirectResponse("/auth/check-email", status_code=302)
    sign_in(request, user)
    logger.info("User %d logged in", user.id)

    return RedirectResponse(url=AFTER_LOGIN[next] if next else HOME, status_code=302)


async def _sign_out(request: Request, db: AsyncSession) -> None:
    """The session ends for good: its id is refused from now on, whatever cookie still carries it. When the shared
    store cannot take the id (Redis down), the account's session version is bumped instead: durable, seen by every
    worker, at the price of signing out the account's other sessions too."""
    sid, iat, user_id = request.session.get("sid"), request.session.get("iat"), request.session.get("user_id")
    request.session.clear()
    if sid and not await sessions.revoke(sid, iat) and user_id:
        await db.execute(update(User).where(User.id == user_id).values(session_version=User.session_version + 1))


@router.post("/auth/logout")
async def logout(request: Request, db: AsyncSession = Depends(get_db)):
    await _sign_out(request, db)
    # The phone app keys its own sign-out on a navigation to /auth/logout, which Android reports for a GET only:
    # the redirected GET is that navigation (a no-op on a session already dead), and it leads home.
    return RedirectResponse(url="/auth/logout", status_code=303)


@router.get("/auth/logout")
async def logout_link(request: Request, db: AsyncSession = Depends(get_db)):
    """An old link or bookmark, or the redirect after the POST: it signs out only when the browser says the
    navigation is its own (same-origin, or none for a typed address), never from a link on another site; home
    either way."""
    if request.headers.get("sec-fetch-site") in ("same-origin", "none"):
        await _sign_out(request, db)
    return RedirectResponse(url="/", status_code=302)


# ---------------------------------------------------------------------------
# Forgot / Reset password
# ---------------------------------------------------------------------------

@router.get("/auth/forgot-password", response_class=HTMLResponse)
async def forgot_password_page(request: Request):
    return templates.TemplateResponse(request, "auth/forgot_password.html", context={})


@router.post("/auth/forgot-password", dependencies=[Depends(_same_site)])
async def forgot_password(
    request: Request,
    db: AsyncSession = Depends(get_db),
    email: str = Form(...),
):
    email = email.strip().lower()
    if await count_attempt("forgot", client_ip(request), email):  # no more emails for now; nothing revealed
        return templates.TemplateResponse(request, "auth/forgot_password.html", context={"sent": True},
                                          status_code=429)

    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()

    email_task = None
    if user and user.password_hash:
        token = generate_token()
        user.password_reset_token = hash_token(token)  # the email gets the token, the table its hash
        user.password_reset_expires_at = datetime.now(timezone.utc) + timedelta(hours=1)
        # saved before the email goes (get_db commits only after the response and its background work): the
        # link is never dead on arrival, and no row lock is held through SMTP
        await db.commit()
        # sent once the answer is out: how long it takes never tells whether the email has an account
        email_task = BackgroundTask(send_password_reset_email, email, token)

    # Always show success (don't reveal if email exists)
    return templates.TemplateResponse(
        request, "auth/forgot_password.html",
        context={"sent": True}, background=email_task,
    )


@router.get("/auth/reset-password", response_class=HTMLResponse)
async def reset_password_page(request: Request, token: str):
    return templates.TemplateResponse(
        request, "auth/reset_password.html",
        context={"token": token},
    )


@router.post("/auth/reset-password", dependencies=[Depends(_same_site)])
async def reset_password(
    request: Request,
    db: AsyncSession = Depends(get_db),
    token: str = Form(...),
    password: str = Form(...),
):
    if not 8 <= len(password) <= 1024:
        return templates.TemplateResponse(
            request, "auth/reset_password.html",
            context={"token": token, "error": "Le mot de passe doit contenir entre 8 et 1 024 caractères.",
                     "error_field": "password"},
        )

    result = await db.execute(
        select(User).where(User.password_reset_token == hash_token(token))
    )
    user = result.scalar_one_or_none()

    if (not user or not user.password_reset_expires_at
            or user.password_reset_expires_at.replace(tzinfo=timezone.utc) < datetime.now(timezone.utc)):
        return templates.TemplateResponse(
            request, "auth/reset_password.html",
            context={"token": token, "error": "Lien expiré. Redemande un nouveau lien."},
        )

    user.password_hash = await hash_password_async(password)
    user.password_reset_token = None
    user.password_reset_expires_at = None
    user.session_version = (user.session_version or 0) + 1  # every other session of the account is signed out
    # and every paired phone (180-day tokens of their own): whoever reset the password pairs them again; the days
    # they sent stay (mobile_daily.device_id is set to NULL)
    await db.execute(delete(MobileDevice).where(MobileDevice.user_id == user.id))
    await db.commit()  # before the redirect: the request that follows it already checks the new session version
    # an account the per-account login cap holds can sign in again: whoever reset it holds its mailbox
    await forget_account_attempts("login", (user.email or "").strip().lower())

    request.session.clear()
    if not user.email_verified:
        request.session["pending_user_id"] = user.id
        return RedirectResponse("/auth/check-email", status_code=302)
    sign_in(request, user)
    logger.info("Password reset for user %d", user.id)

    return RedirectResponse(url=HOME, status_code=302)


# ---------------------------------------------------------------------------
# Strava linking (no longer auth — just account linking)
# ---------------------------------------------------------------------------

def _remember_next(request: Request, next: str | None) -> None:
    """A connect started from Réglages comes back there, whatever happens."""
    if next == "settings":
        request.session["strava_next"] = "/settings#strava"
    elif next == "devices":
        request.session["strava_next"] = "/connections/strava"
    elif next == "app":  # started from the app's screen of Strava, in its sign-in sheet
        request.session["strava_next"] = app_return("strava")
    else:
        request.session.pop("strava_next", None)


def _back_to_settings(request: Request, error: str, result: str = "failed") -> RedirectResponse | None:
    url = request.session.pop("strava_next", None)
    if not url:
        return None
    if is_app_return(url):  # the app's sign-in sheet closes on it: the outcome as a code, no flash
        return RedirectResponse(url=with_result(url, result), status_code=302)
    request.session["strava_error"] = error
    return RedirectResponse(url=url, status_code=302)


@router.get("/auth/strava")
async def strava_link(request: Request, next: str | None = None,
                      db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    _remember_next(request, next)
    request.session.pop("pending_client_id", None)
    request.session.pop("pending_client_secret", None)
    if user.has_own_strava_app and user.strava_credentials_valid:
        service = StravaService.for_user(db, user)
        mode = "stored"
    elif settings.strava_shared_ready:
        service = StravaService(db)
        mode = "shared"
        request.session.setdefault("strava_next", "/connections/strava")
    else:
        if next == "app":  # the keys are typed on the site, not in the app's sign-in sheet
            return RedirectResponse(with_result(app_return("strava"), "failed"), status_code=302)
        return RedirectResponse(f"/setup?next={next}" if next in ("settings", "devices") else "/setup", status_code=302)
    # Bind the attempt to its selected app, even if configuration changes meanwhile. The shared app's
    # secret stays in the server configuration: the attempt keeps its client id only.
    state = await begin_strava(request, db, user.id, {"mode": "shared", "client_id": service.client_id}
                               if mode == "shared" else
                               {"client_id": service.client_id, "client_secret": service.client_secret, "mode": mode})
    return RedirectResponse(service.get_authorize_url(state), status_code=302)


@router.get("/auth/strava/callback")
async def strava_callback(
    request: Request,
    code: str | None = None,
    error: str | None = None,
    state: str | None = None,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    pending = await consume_strava(request, db, user.id, state)
    if pending is None:
        back = _back_to_settings(request, "La connexion a expiré ou le lien est invalide. Recommence depuis Réglages.")
        return back or RedirectResponse("/setup?error=auth_failed", status_code=302)
    if error or not code:
        back = _back_to_settings(request, "Connexion à Strava annulée." if error == "access_denied"
                                 else "La connexion à Strava a échoué. Réessaie.",
                                 "cancelled" if error == "access_denied" else "failed")
        return back or RedirectResponse("/setup?error=auth_failed", status_code=302)

    pending_id, pending_secret = pending.get("client_id"), pending.get("client_secret")
    shared = pending.get("mode") == "shared"
    if shared:
        if not settings.strava_shared_ready or pending_id != settings.STRAVA_CLIENT_ID:
            return _back_to_settings(request, "La connexion Strava a changé. Recommence depuis Réglages.") or RedirectResponse("/connections/strava", status_code=302)
        pending_secret = settings.STRAVA_CLIENT_SECRET  # never stored with the attempt
    if pending_id and pending_secret:
        strava = StravaService(db, client_id=pending_id, client_secret=pending_secret)
    elif user.has_own_strava_app:
        strava = StravaService.for_user(db, user)
    else:
        return RedirectResponse(url="/setup?error=missing_credentials", status_code=302)

    # Exchange code for tokens
    try:
        token_data = await strava.exchange_token(code)
    except Exception as exc:
        logger.exception("Strava token exchange failed")
        typed = pending_id and not pending.get("mode")
        request.session.pop("pending_client_secret", None)
        # keys just typed in the wizard: type them again there (and still come
        # back to Réglages after); stored keys refused: say so in Réglages,
        # which then offers the wizard; anything else: just try again
        # only a 401 says the keys are wrong (a 400 is the code: reused by a
        # reload, or expired); never flag a link that is in place meanwhile
        refused = getattr(exc, "status_code", None) == 401
        if not typed and not shared and refused and user.has_own_strava_app and not user.strava_access_token:
            user.strava_credentials_valid = False
            await db.commit()
        back = None if typed else _back_to_settings(
            request, "La connexion Strava de PaceForge est temporairement indisponible. Réessaie plus tard." if shared and refused
            else "Strava refuse les clés de ton app : change-les." if refused
            else "La connexion à Strava a échoué. Réessaie.")
        return back or RedirectResponse(url="/setup?error=invalid_credentials", status_code=302)

    athlete = token_data.get("athlete", {})
    strava_athlete_id = athlete.get("id")

    if not strava_athlete_id:
        back = _back_to_settings(request, "La connexion à Strava a échoué. Réessaie.")
        if back:
            return back
        return RedirectResponse(url="/setup?error=auth_failed", status_code=302)

    # Check if another user already has this Strava athlete ID
    existing = await db.execute(
        select(User).where(
            User.strava_athlete_id == strava_athlete_id,
            User.id != user.id,
        )
    )
    old_user = existing.scalar_one_or_none()
    if old_user:
        back = _back_to_settings(request, "Ce compte Strava est déjà lié à un autre compte PaceForge. Aucune donnée n’a été déplacée.")
        return back or RedirectResponse("/setup?error=already_linked", status_code=302)

    # Link Strava to existing user
    user.strava_athlete_id = strava_athlete_id
    user.strava_access_token = token_data["access_token"]
    user.strava_refresh_token = token_data["refresh_token"]
    user.strava_token_expires_at = token_data["expires_at"]
    user.strava_credentials_valid = True  # the keys just worked
    user.firstname = user.firstname or athlete.get("firstname")
    user.lastname = user.lastname or athlete.get("lastname")
    user.profile_picture_url = athlete.get("profile")

    # Persist the selected app identity. Shared secrets stay in server configuration.
    if pending_id and pending_secret:
        if user.strava_webhook_subscription_id and user.has_own_strava_app and user.strava_client_id != pending_id:
            # another app: its subscription goes (with its own keys), the new app gets one below
            if not (settings.strava_shared_ready and user.strava_client_id == settings.STRAVA_CLIENT_ID):
                await StravaService.for_user(db, user).delete_webhook_subscription(user.strava_webhook_subscription_id)
            user.strava_webhook_subscription_id = None
        if shared:
            user.strava_webhook_subscription_id = None
        user.strava_client_id = pending_id
        user.strava_client_secret_encrypted = None if shared else encrypt_secret(pending_secret)
        user.strava_credentials_valid = True
        request.session.pop("pending_client_id", None)
        request.session.pop("pending_client_secret", None)

    await db.flush()
    logger.info("Strava linked for user %d (athlete %d)", user.id, strava_athlete_id)

    # Create webhook subscription
    if (user.has_own_strava_app and not user.strava_webhook_subscription_id
            and not (settings.strava_shared_ready and user.strava_client_id == settings.STRAVA_CLIENT_ID)):
        try:
            user_strava = StravaService.for_user(db, user)
            sub_id = await user_strava.create_webhook_subscription(user)
            if sub_id:
                user.strava_webhook_subscription_id = sub_id
                await db.flush()
        except Exception:
            logger.exception("Failed to create webhook for user %d", user.id)

    # Workers must see the committed link; broker failure must not undo it.
    await db.commit()
    if not user.initial_sync_done:
        from app.tasks.initial_sync import initial_sync
        try:
            initial_sync.delay(user.id)
        except Exception:
            logger.exception("Initial sync could not be queued for user %d", user.id)
            request.session["strava_error"] = "Strava connecté. La synchronisation n’a pas démarré ; réessaie depuis Réglages."

    next_url = request.session.pop("strava_next", None)
    if is_app_return(next_url):
        request.session.pop("strava_error", None)  # the sheet's session ends with it; the app reads the link's state
        return RedirectResponse(url=with_result(next_url, "ok"), status_code=302)
    if next_url and not request.session.get("strava_error"):
        request.session["strava_ok"] = "Strava connecté. Tes activités arrivent."
    return RedirectResponse(url=next_url or HOME, status_code=302)  # an explicit « next » first


# ---------------------------------------------------------------------------
# Setup (Strava API credentials)
# ---------------------------------------------------------------------------

@router.get("/setup", response_class=HTMLResponse)
async def setup_page(request: Request, error: str | None = None, next: str | None = None):
    if next is not None or error is None:  # a fresh visit; an error page keeps the way back
        _remember_next(request, next)
    return templates.TemplateResponse(
        request, "setup.html",
        context={
            "error": error,
            "client_id": request.session.get("pending_client_id", ""),
        },
    )


@router.post("/setup/credentials")
async def setup_credentials(
    request: Request,
    client_id: str = Form(...),
    client_secret: str = Form(...),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    client_id, client_secret = client_id.strip(), client_secret.strip()
    if not client_id.isascii() or not client_id.isdecimal() or len(client_id) > 20 or not 1 <= len(client_secret) <= 512:
        return RedirectResponse(url="/setup?error=missing_credentials", status_code=302)
    state = await begin_strava(request, db, user.id, {"client_id": client_id, "client_secret": client_secret})
    strava = StravaService(db, client_id=client_id, client_secret=client_secret)
    return RedirectResponse(strava.get_authorize_url(state), status_code=302)


# ---------------------------------------------------------------------------
# Static pages
# ---------------------------------------------------------------------------

@router.get("/privacy", response_class=HTMLResponse)
async def privacy(request: Request, user: User | None = Depends(get_optional_user)):
    from app.features import cycling_enabled
    from app.services import health_consent

    # the consent's own words (its data, its text): the policy never says another list than the one agreed to
    return templates.TemplateResponse(request, "privacy.html", context={
        "viewer": user, "consent_data": health_consent.DATA, "consent_text": health_consent.TEXT,
        "bike": cycling_enabled()})


@router.get("/mentions-legales", response_class=HTMLResponse)
async def legal_notice(request: Request, user: User | None = Depends(get_optional_user)):
    return templates.TemplateResponse(request, "legal_notice.html", context={"viewer": user})


@router.get("/cgu", response_class=HTMLResponse)
async def terms(request: Request, user: User | None = Depends(get_optional_user)):
    return templates.TemplateResponse(request, "terms.html", context={"viewer": user})
