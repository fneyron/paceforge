"""Coaching (beta, docs/coaching.md): the athlete's plan and calendar, the coach's view, the calendar feed.

Open to every signed-in account by default. COACHING_BETA_USERS can restrict it to listed emails or switch it off:
every route then answers 404 for an account that does not have it. A coach's routes (/coaching/athletes/<id>/…) also
check, at every request, that the link athlete → coach exists and that the athlete still has Coaching: 404 otherwise. The calendar feed takes no session: its token is the
key (the one documented exception to « never a token in a URL »: a calendar app cannot send a header).

The same handlers serve the athlete (/coaching/…) and the coach (/coaching/athletes/<id>/…): `Scope` says who looks
at whose plan. A tap swaps the calendar part in place (htmx); without JavaScript the same forms post and come back
to the page.
"""
import logging
from dataclasses import dataclass
from datetime import date
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.templating import Jinja2Templates

from app import features
from app.config import settings
from app.dependencies import get_current_user, get_db
from app.models.coaching import CoachingPlan, CoachingSession
from app.models.user import User
from app.services import coaching as co
from app.services import coaching_ics as ics
from app.services import coaching_plan as cp
from app.services import coaching_view as cv
from app.services import race_prep as rp
from app.services.viz import d_short, fr_int, fr_num, hm

logger = logging.getLogger(__name__)
templates = Jinja2Templates(directory="app/templates")
templates.env.filters["fr_num"] = fr_num
templates.env.filters["fr_int"] = fr_int

router = APIRouter(tags=["coaching"])

CODE_ERROR = "Code invalide ou expiré."


async def coaching_user(user: User = Depends(get_current_user)) -> User:
    """The signed-in account, when it has Coaching (every account by default); 404 otherwise (the feature does not
    exist for it)."""
    if not features.coaching_for(user):
        raise HTTPException(status_code=404)
    return user


@dataclass
class Scope:
    viewer: User  # who is signed in
    athlete: User  # whose plan
    coach: bool  # the viewer follows the athlete (never their own plan)

    @property
    def base(self) -> str:
        return f"/coaching/athletes/{self.athlete.id}" if self.coach else "/coaching"

    @property
    def origin(self) -> str:
        return "coach" if self.coach else "athlete"


async def own_scope(user: User = Depends(coaching_user)) -> Scope:
    return Scope(user, user, False)


async def coach_scope(athlete_id: int, user: User = Depends(coaching_user), db: AsyncSession = Depends(get_db)) -> Scope:
    """Checked at every request: the link exists, the athlete still has Coaching."""
    athlete = await db.get(User, athlete_id) if await co.link_of(db, athlete_id, user.id) else None
    if athlete is None or not features.coaching_for(athlete):
        raise HTTPException(status_code=404)
    return Scope(user, athlete, True)


def _day(value: str | None) -> date | None:
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


def _int(value) -> int | None:
    text = str(value or "").strip()
    return int(text) if text.isdigit() else None


def _htmx(request: Request) -> bool:
    return bool(request.headers.get("HX-Request"))


async def _hr_reference(db: AsyncSession, athlete: User, c: co.Clock) -> float | None:
    """The athlete's maximum heart rate when one is known (confirmed, else corroborated by the runs: the reference
    Santé's zones read), for the sessions' heart-rate ranges."""
    try:
        from app.services import heart_rate_reference

        return (await heart_rate_reference.for_athlete(db, athlete.id, c.today, zone=c.zone))["reference"]
    except Exception:
        logger.exception("Coaching: the heart-rate reference failed for user %d", athlete.id)
        return athlete.max_heart_rate


async def _state(db: AsyncSession, scope: Scope) -> co.State:
    plan = await co.get_plan(db, scope.athlete.id)
    if plan is None:
        raise HTTPException(status_code=404)
    return await co.sync(db, plan, scope.athlete)


async def _main(request: Request, db: AsyncSession, scope: Scope, state: co.State, *, d: date | None = None,
                w: date | None = None, v: str | None = None, move: int | None = None, error: str | None = None,
                status_code: int = 200) -> Response:
    """The calendar part (partials/coaching_main.html), for an htmx swap."""
    p = cv.page(state, d=d, w=w, month=v == "mois", move=move, coach=scope.coach,
                hr_reference=await _hr_reference(db, scope.athlete, state.clock))
    return templates.TemplateResponse(request, "partials/coaching_main.html", status_code=status_code, context={
        "p": p, "scope": scope, "error": error})


async def _after(request: Request, db: AsyncSession, scope: Scope, *, d: date | None, v: str | None,
                 error: str | None = None) -> Response:
    """After a change: the plan read again (matching, adaptation, regeneration), then the calendar part in place, or
    the page for a browser without JavaScript."""
    state = await _state(db, scope)
    await db.commit()
    if not _htmx(request):
        query = "&".join(x for x in (f"d={d.isoformat()}" if d else "", "v=mois" if v == "mois" else "") if x)
        return RedirectResponse(scope.base + (f"?{query}" if query else ""), status_code=303)
    return await _main(request, db, scope, state, d=d, v=v, error=error)


async def _row(db: AsyncSession, scope: Scope, session_id: int) -> tuple[CoachingPlan, CoachingSession]:
    plan = await co.get_plan(db, scope.athlete.id)
    row = await db.get(CoachingSession, session_id) if plan is not None else None
    if plan is None or row is None or row.plan_id != plan.id or row.status == co.CANCELLED:
        raise HTTPException(status_code=404)
    return plan, row


# ── the calendar feed (no session: the token is the key) ────────────────────

def feed_urls(plan: CoachingPlan) -> dict | None:
    if not plan.feed_token:
        return None
    base = settings.BASE_URL.rstrip("/")
    https = f"{base}/coaching/cal/{plan.feed_token}.ics"
    return {"https": https, "webcal": "webcal://" + https.split("://", 1)[-1]}


@router.get("/coaching/cal/{token}.ics", include_in_schema=False)
async def calendar_feed(token: str, request: Request, db: AsyncSession = Depends(get_db)):
    """The plan's sessions as an iCalendar feed (services/coaching_ics). 404 while Coaching is off, for an unknown
    or regenerated token, or for an owner the switch leaves out. Read-only: nothing is matched or regenerated here."""
    from app.services.auth import client_ip, over_quota

    if not features.coaching_enabled():
        raise HTTPException(status_code=404)
    if await over_quota("coaching_feed", ip=client_ip(request)):
        return Response("Trop de requêtes.", status_code=429, headers={"Retry-After": "600"})
    plan = await co.plan_by_feed_token(db, token)
    owner = await db.get(User, plan.user_id) if plan is not None else None
    if plan is None or owner is None or not features.coaching_for(owner):
        raise HTTPException(status_code=404)
    c = await co.clock(db, owner)
    rows = await co.sessions_of(db, plan)
    zones = cv.hr_rows(owner.max_heart_rate)  # the confirmed maximum only: no heavy read on a public address
    body = ics.render(rows, c.today, host=urlsplit(settings.BASE_URL).hostname or "paceforge.fr",
                      guidance=lambda row: cv.guidance(row.kind, row.structure, zones))
    return Response(body, media_type="text/calendar; charset=utf-8", headers={
        "Content-Disposition": 'inline; filename="paceforge-coaching.ics"',
        "Cache-Control": "private, max-age=300", "Referrer-Policy": "no-referrer", "X-Robots-Tag": "noindex"})


# ── the athlete's page, the onboarding ──────────────────────────────────────

async def _onboarding(request: Request, db: AsyncSession, user: User, *, plan: CoachingPlan | None,
                      error: str | None = None, form: dict | None = None, status_code: int = 200) -> Response:
    c = await co.clock(db, user)
    races = await co.goal_options(db, user.id, c.today)
    computed = await co.starting_volume(db, user, c)
    days = plan.weekdays if plan else ()
    chosen = {"goal": str(plan.route_id) if plan and plan.route_id else ("none" if plan or not races else None),
              "days": list(days), "long_day": plan.long_day if plan else None,
              # the starting volume is computed again when the plan is made again (the plan's own without a history)
              "base": computed or (plan.base_minutes if plan else None)}
    if form:
        chosen.update(form)
    if chosen["goal"] is None and races:
        chosen["goal"] = str(races[0].id)
    base = chosen["base"]
    volumes = sorted({*range(60, 901, 15), *([base] if base else [])})
    return templates.TemplateResponse(request, "coaching_onboarding.html", status_code=status_code, context={
        "user": user, "redo": plan is not None, "error": error, "chosen": chosen, "computed": computed,
        "races": [{"id": r.id, "name": r.name, "when": d_short(day), "j": rp.j_label(-(day - c.today).days)}
                  for r, day in ((r, rp.race_day(r)) for r in races) if day is not None],
        "weekdays": list(enumerate(("lun.", "mar.", "mer.", "jeu.", "ven.", "sam.", "dim."))),
        "volumes": [(m, hm(m)) for m in volumes],
        "choices": [(m, f"{m // 60}\u00a0h" + (" et +" if m == cp.START_CHOICES[-1] else ""))
                    for m in cp.START_CHOICES]})


async def _tools(db: AsyncSession, scope: Scope, plan: CoachingPlan, code: str | None = None) -> dict:
    return {"feed": feed_urls(plan), "coaches": [{"id": u.id, "name": u.firstname or "Coach"}
                                                 for u in await co.coaches_of(db, scope.athlete.id)], "code": code}


async def _page(request: Request, db: AsyncSession, scope: Scope, d: str | None, w: str | None, v: str | None):
    state = await _state(db, scope)
    await db.commit()
    p = cv.page(state, d=_day(d), w=_day(w), month=v == "mois", coach=scope.coach,
                hr_reference=await _hr_reference(db, scope.athlete, state.clock))
    return templates.TemplateResponse(request, "coaching.html", context={
        "user": scope.viewer, "scope": scope, "head": cv.header(state), "p": p, "method": cv.METHOD,
        "athlete_name": scope.athlete.firstname or "Athlète", "error": None,
        "tools": None if scope.coach else await _tools(db, scope, state.plan)})


@router.get("/coaching", response_class=HTMLResponse)
async def coaching_page(request: Request, d: str | None = None, w: str | None = None, v: str | None = None,
                        refaire: str | None = None, scope: Scope = Depends(own_scope),
                        db: AsyncSession = Depends(get_db)):
    plan = await co.get_plan(db, scope.athlete.id)
    if plan is None and refaire != "1" and await co.athletes_of(db, scope.viewer.id):
        return RedirectResponse("/coaching/athletes", status_code=303)  # a coach without a plan of their own
    if plan is None or refaire == "1":
        return await _onboarding(request, db, scope.viewer, plan=plan)
    return await _page(request, db, scope, d, w, v)


@router.post("/coaching/plan")
async def coaching_save_plan(request: Request, scope: Scope = Depends(own_scope), db: AsyncSession = Depends(get_db)):
    """The onboarding's answers: the plan is made (or made again), its sessions generated."""
    form = await request.form()
    goal, days = str(form.get("goal") or "none"), form.getlist("days")
    user = scope.viewer
    c = await co.clock(db, user)
    try:
        plan = await co.save_plan(db, user, c, route_id=int(goal) if goal.isdigit() else None, days=days,
                                  long_day=form.get("long_day"), base=form.get("base"))
    except co.Invalid as e:  # nothing was written: the answers are checked first. Answered 200: the app's shell
        # replaces any document answered 4xx by « Écran indisponible », hiding the message the form shows
        return await _onboarding(request, db, user, plan=await co.get_plan(db, user.id), error=str(e), status_code=200,
                                 form={"goal": goal, "days": list(co.clean_days(days)),
                                       "long_day": _int(form.get("long_day")), "base": _int(form.get("base"))})
    await co.sync(db, plan, user)
    await db.commit()
    logger.info("Coaching: plan saved for user %d", user.id)
    return RedirectResponse("/coaching", status_code=303)


@router.post("/coaching/stop")
async def coaching_stop(scope: Scope = Depends(own_scope), db: AsyncSession = Depends(get_db)):
    """« Arrêter le coaching » (the page confirms first): the plan, its sessions and its sharing are deleted."""
    await co.delete_plan(db, scope.athlete.id)
    await db.commit()
    logger.info("Coaching: plan deleted for user %d", scope.athlete.id)
    return RedirectResponse("/coaching", status_code=303)


@router.post("/coaching/share", response_class=HTMLResponse)
async def coaching_share(request: Request, scope: Scope = Depends(own_scope), db: AsyncSession = Depends(get_db)):
    """« Partager avec un coach »: a code shown once, good for 7 days and one coach."""
    state = await _state(db, scope)
    code = await co.new_invite(db, scope.athlete.id, state.clock.now)
    await db.commit()
    return templates.TemplateResponse(request, "partials/coaching_tools.html", context={
        "tools": await _tools(db, scope, state.plan, code=code), "scope": scope, "open": "share"})


@router.post("/coaching/coaches/{coach_id}/revoke", response_class=HTMLResponse)
async def coaching_revoke(coach_id: int, request: Request, scope: Scope = Depends(own_scope),
                          db: AsyncSession = Depends(get_db)):
    """The athlete ends a coach's access: at once."""
    plan = await co.get_plan(db, scope.athlete.id)
    if plan is None:
        raise HTTPException(status_code=404)
    await co.unlink(db, scope.athlete.id, coach_id)
    await db.commit()
    if not _htmx(request):
        return RedirectResponse("/coaching", status_code=303)
    return templates.TemplateResponse(request, "partials/coaching_tools.html", context={
        "tools": await _tools(db, scope, plan), "scope": scope, "open": "share"})


@router.post("/coaching/feed/regenerate", response_class=HTMLResponse)
async def coaching_feed_regenerate(request: Request, scope: Scope = Depends(own_scope),
                                   db: AsyncSession = Depends(get_db)):
    """« Régénérer le lien »: a new feed address, the old one stops working."""
    plan = await co.get_plan(db, scope.athlete.id)
    if plan is None:
        raise HTTPException(status_code=404)
    co.new_feed_token(plan)
    await db.commit()
    if not _htmx(request):
        return RedirectResponse("/coaching", status_code=303)
    return templates.TemplateResponse(request, "partials/coaching_tools.html", context={
        "tools": await _tools(db, scope, plan), "scope": scope, "open": "feed"})


# ── the coach's pages ───────────────────────────────────────────────────────

async def _athletes(request: Request, db: AsyncSession, user: User, error: str | None = None,
                    status_code: int = 200) -> Response:
    cards = []
    for athlete in await co.athletes_of(db, user.id):
        plan = await co.get_plan(db, athlete.id)
        if plan is None:
            continue
        cards.append(cv.athlete_card(await co.sync(db, plan, athlete), athlete))
    await db.commit()
    return templates.TemplateResponse(request, "coaching_athletes.html", status_code=status_code, context={
        "user": user, "athletes": cards, "error": error, "has_plan": await co.get_plan(db, user.id) is not None})


@router.get("/coaching/athletes", response_class=HTMLResponse)
async def coaching_athletes(request: Request, user: User = Depends(coaching_user), db: AsyncSession = Depends(get_db)):
    return await _athletes(request, db, user)


@router.post("/coaching/athletes/join")
async def coaching_join(request: Request, code: str = Form(default=""), user: User = Depends(coaching_user),
                        db: AsyncSession = Depends(get_db)):
    """The coach types the athlete's code: the link is made, the code is spent."""
    from app.services.auth import over_quota

    if await over_quota("coaching_code", user=user.id):
        return await _athletes(request, db, user, error="Trop d’essais. Réessaie dans une heure.", status_code=200)
    c = await co.clock(db, user)
    athlete = await co.accept_invite(db, user, code, c.now)
    if athlete is None:
        return await _athletes(request, db, user, error=CODE_ERROR, status_code=200)  # 200: as the onboarding
    await db.commit()
    logger.info("Coaching: user %d now follows user %d", user.id, athlete.id)
    return RedirectResponse(f"/coaching/athletes/{athlete.id}", status_code=303)


@router.post("/coaching/athletes/{athlete_id}/leave")
async def coaching_leave(scope: Scope = Depends(coach_scope), db: AsyncSession = Depends(get_db)):
    """The coach stops following an athlete: at once."""
    await co.unlink(db, scope.athlete.id, scope.viewer.id)
    await db.commit()
    return RedirectResponse("/coaching/athletes", status_code=303)


@router.get("/coaching/athletes/{athlete_id}", response_class=HTMLResponse)
async def coaching_athlete_page(request: Request, d: str | None = None, w: str | None = None, v: str | None = None,
                                scope: Scope = Depends(coach_scope), db: AsyncSession = Depends(get_db)):
    return await _page(request, db, scope, d, w, v)


# ── the calendar's taps: the athlete's (/coaching/…) and the coach's (/coaching/athletes/<id>/…) ──

def _session_routes(prefix: str, scope_of) -> APIRouter:
    r = APIRouter()

    @r.get(prefix + "/calendar", response_class=HTMLResponse)
    async def calendar_part(request: Request, d: str | None = None, w: str | None = None, v: str | None = None,
                            move: int | None = None, scope: Scope = Depends(scope_of),
                            db: AsyncSession = Depends(get_db)):
        """A day, a week or a month chosen; `move`: the session being moved (the days become its targets)."""
        if not _htmx(request):  # an address someone opened by itself: the page
            query = str(request.url.query)
            return RedirectResponse(scope.base + (f"?{query}" if query else ""), status_code=303)
        state = await _state(db, scope)
        await db.commit()
        return await _main(request, db, scope, state, d=_day(d), w=_day(w), v=v, move=move)

    @r.post(prefix + "/sessions/{session_id}/status", response_class=HTMLResponse)
    async def session_status(session_id: int, request: Request, value: str = Form(...), minutes: str = Form(default=""),
                             v: str = Form(default=""), scope: Scope = Depends(scope_of),
                             db: AsyncSession = Depends(get_db)):
        """« Fait » (optional minutes), « Pas faite », or the tick undone. The athlete's own."""
        if scope.coach:
            raise HTTPException(status_code=404)
        _plan, row = await _row(db, scope, session_id)
        c = await co.clock(db, scope.athlete)
        error = None
        try:
            if value == "done":
                co.set_done(row, c.today, minutes)
            elif value == "missed":
                co.set_missed(row, c.today)
            elif value == "reset":
                co.reset_status(row)
            else:
                raise co.Invalid("Action inconnue.")
        except co.Invalid as e:
            error = str(e)
        return await _after(request, db, scope, d=row.day, v=v, error=error)

    @r.post(prefix + "/sessions/{session_id}/ressenti", response_class=HTMLResponse)
    async def session_ressenti(session_id: int, request: Request, value: str = Form(...), v: str = Form(default=""),
                               scope: Scope = Depends(scope_of), db: AsyncSession = Depends(get_db)):
        """« C'était comment ? »: saved on the tap, changeable; the adaptation is read again."""
        if scope.coach:
            raise HTTPException(status_code=404)
        _plan, row = await _row(db, scope, session_id)
        error = None
        try:
            co.set_ressenti(row, value)
        except co.Invalid as e:
            error = str(e)
        return await _after(request, db, scope, d=row.day, v=v, error=error)

    @r.post(prefix + "/sessions/{session_id}/note", response_class=HTMLResponse)
    async def session_note(session_id: int, request: Request, note: str = Form(default=""), v: str = Form(default=""),
                           scope: Scope = Depends(scope_of), db: AsyncSession = Depends(get_db)):
        """The athlete's note (280 characters), or the coach's comment: each writes their own."""
        _plan, row = await _row(db, scope, session_id)
        if scope.coach:
            row.coach_comment = co.clean_text(note)
        else:
            row.note = co.clean_text(note)
        return await _after(request, db, scope, d=row.day, v=v)

    @r.post(prefix + "/sessions/{session_id}/move", response_class=HTMLResponse)
    async def session_move(session_id: int, request: Request, to: str = Form(...), v: str = Form(default=""),
                           scope: Scope = Depends(scope_of), db: AsyncSession = Depends(get_db)):
        plan, row = await _row(db, scope, session_id)
        c = await co.clock(db, scope.athlete)
        target, error = _day(to), None
        try:
            if target is None:
                raise co.Invalid("Jour inconnu.")
            co.move(await co.sessions_of(db, plan), row, target, c.today, c.now, scope.origin)
        except co.Invalid as e:
            error = str(e)
        return await _after(request, db, scope, d=row.day, v=v, error=error)

    @r.post(prefix + "/sessions/{session_id}/edit", response_class=HTMLResponse)
    async def session_edit(session_id: int, request: Request, kind: str = Form(...), minutes: str = Form(...),
                           v: str = Form(default=""), scope: Scope = Depends(scope_of),
                           db: AsyncSession = Depends(get_db)):
        _plan, row = await _row(db, scope, session_id)
        c = await co.clock(db, scope.athlete)
        error = None
        try:
            if row.status != co.PLANNED:
                raise co.Invalid("Cette séance ne se modifie plus.")
            co.edit(row, c.now, kind=kind, minutes=minutes, origin=scope.origin)
        except co.Invalid as e:
            error = str(e)
        return await _after(request, db, scope, d=row.day, v=v, error=error)

    @r.post(prefix + "/sessions/{session_id}/delete", response_class=HTMLResponse)
    async def session_delete(session_id: int, request: Request, v: str = Form(default=""),
                             scope: Scope = Depends(scope_of), db: AsyncSession = Depends(get_db)):
        """A coach removes a session."""
        if not scope.coach:
            raise HTTPException(status_code=404)
        _plan, row = await _row(db, scope, session_id)
        c = await co.clock(db, scope.athlete)
        day = row.day
        await co.remove(db, row, c.today, c.now)
        return await _after(request, db, scope, d=day, v=v)

    @r.post(prefix + "/sessions", response_class=HTMLResponse)
    async def session_add(request: Request, day: str = Form(...), kind: str = Form(...), minutes: str = Form(...),
                          v: str = Form(default=""), scope: Scope = Depends(scope_of),
                          db: AsyncSession = Depends(get_db)):
        """A coach adds a session."""
        if not scope.coach:
            raise HTTPException(status_code=404)
        plan = await co.get_plan(db, scope.athlete.id)
        target = _day(day)
        if plan is None or target is None:
            raise HTTPException(status_code=404)
        c = await co.clock(db, scope.athlete)
        error = None
        try:
            co.add(db, plan, target, c.today, c.now, kind=kind, minutes=minutes, origin="coach")
        except co.Invalid as e:
            error = str(e)
        return await _after(request, db, scope, d=target, v=v, error=error)

    return r


router.include_router(_session_routes("/coaching", own_scope))
router.include_router(_session_routes("/coaching/athletes/{athlete_id}", coach_scope))
