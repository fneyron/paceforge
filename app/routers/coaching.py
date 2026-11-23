"""Coaching (beta, docs/coaching.md): the athlete's plan and calendar, the coach's view, the calendar feed.

Open to every signed-in account by default. COACHING_BETA_USERS can restrict it to listed emails or switch it off:
every route then answers 404 for an account that does not have it. A coach's routes (/coaching/athletes/<id>/…) also
check, at every request, that the link athlete → coach exists and that the athlete still has Coaching: 404 otherwise. The calendar feed takes no session: its token is the
key (the one documented exception to « never a token in a URL »: a calendar app cannot send a header).

The same handlers serve the athlete (/coaching/…) and the coach (/coaching/athletes/<id>/…): `Scope` says who looks
at whose plan. A tap swaps the page's part in place (the road, the board, the day: htmx); without JavaScript the same
forms post and come back to the page.
"""
import logging
from dataclasses import dataclass
from datetime import date, timedelta, timezone
from typing import Any
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
from app.services import coaching_accounts as accounts
from app.services import coaching_agenda as ag
from app.services import coaching_ics as ics
from app.services import coaching_plan as cp
from app.services import coaching_slots as cs
from app.services import coaching_start as cst
from app.services import coaching_view as cv
from app.services import race_prep as rp
from app.services.viz import d_long, d_short, fr_int, fr_num, hm

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


async def _critical(db: AsyncSession, athlete: User, c: co.Clock, streams: bool = True):
    """The athlete's critical speed from their own runs (coaching_pace), for the sessions' paces; None when the runs
    do not give one."""
    from app.services import coaching_pace

    try:
        return await coaching_pace.for_athlete(db, athlete.id, c.today, streams=streams)
    except Exception:
        logger.exception("Coaching: the critical speed failed for user %d", athlete.id)
        return None


async def _targets(db: AsyncSession, scope: Scope, state: co.State) -> dict:
    """What the tickets' « Cible » reads, once per page: the critical speed, and the maximum heart rate for the
    athlete's own page only — the person who follows a plan never reads a heart rate (docs/coaching.md)."""
    return {"critical": await _critical(db, scope.athlete, state.clock),
            "hr_reference": None if scope.coach else await _hr_reference(db, scope.athlete, state.clock)}


async def _state(db: AsyncSession, scope: Scope) -> co.State:
    plan = await co.get_plan(db, scope.athlete.id)
    if plan is None:
        raise HTTPException(status_code=404)
    return await co.sync(db, plan, scope.athlete)


async def _names(db: AsyncSession, scope: Scope) -> dict:
    """The first names the pages sign with: the athlete's, and — for the athlete's own page — the one person who
    follows the plan (several, or none left: no name, a comment's author is not kept)."""
    followers = [] if scope.coach else await co.coaches_of(db, scope.athlete.id)
    return {"athlete": scope.athlete.firstname or None,
            "follower": followers[0].firstname or None if len(followers) == 1 else None}


async def _main(request: Request, db: AsyncSession, scope: Scope, state: co.State, *, d: date | None = None,
                w: date | None = None, v: str | None = None, move: int | None = None, error: str | None = None,
                status_code: int = 200) -> Response:
    """The swapped part (partials/coaching_main.html), for an htmx swap. `v`: the address of the « Mois » view of
    before, still answered — with the week board (the road took its place)."""
    p = cv.page(state, d=d, w=w, move=move, coach=scope.coach, **await _targets(db, scope, state),
                names=await _names(db, scope))
    return templates.TemplateResponse(request, "partials/coaching_main.html", status_code=status_code, context={
        "p": p, "scope": scope, "error": error})


async def _after(request: Request, db: AsyncSession, scope: Scope, *, d: date | None, v: str | None,
                 error: str | None = None) -> Response:
    """After a change: the plan read again (matching, adaptation, regeneration), then the swapped part in place, or
    the page for a browser without JavaScript. Every form of the board says which week it was showing (`w`); the
    debrief asked at the top says which day was open (`back`): the board stays where it was."""
    form = await request.form()
    w, back = _day(str(form.get("w") or "")), _day(str(form.get("back") or ""))
    d = back or d
    state = await _state(db, scope)
    await db.commit()
    if not _htmx(request):
        query = "&".join(x for x in (f"d={d.isoformat()}" if d else "", f"w={w.isoformat()}" if w else "") if x)
        return RedirectResponse(scope.base + (f"?{query}" if query else ""), status_code=303)
    return await _main(request, db, scope, state, d=d, w=w, v=v, error=error)


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
    # the confirmed maximum only, and the critical speed from the best efforts alone: no heavy read on a public
    # address
    reference = owner.max_heart_rate
    critical = await _critical(db, owner, c, streams=False)
    goal = await co.goal_light(db, plan)
    body = ics.render(rows, c.today, host=urlsplit(settings.BASE_URL).hostname or "paceforge.fr",
                      guidance=lambda row: cv.guidance(row.kind, row.structure, reference, critical, goal)
                      if row.status in (co.PLANNED, co.MISSED) else cv.feel(row.kind, row.structure), zone=c.zone)
    return Response(body, media_type="text/calendar; charset=utf-8", headers={
        "Content-Disposition": 'inline; filename="paceforge-coaching.ics"',
        "Cache-Control": "private, max-age=300", "Referrer-Policy": "no-referrer", "X-Robots-Tag": "noindex"})


# ── the athlete's page, the onboarding ──────────────────────────────────────

WEEKDAYS = ("lun.", "mar.", "mer.", "jeu.", "ven.", "sam.", "dim.")
WEEKDAYS_L = ("lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche")


NEW_RACE, OWN_RACE = "new", "race"  # the goal's values beside a Route's id and « none »
RACE_FIELDS = ("race_name", "race_date", "race_km", "race_h", "race_min")


def _race_fields(values) -> dict:
    """What « Ajouter une course » typed, as the form sends it (kept to fill the form again)."""
    return {k: str(values.get(k) or "").strip()[:80] for k in RACE_FIELDS}


def _own_race(plan: CoachingPlan | None, today: date) -> tuple | None:
    """The plan's race without a course, still to come: (name, day, km, target_s)."""
    if plan is None or plan.race_date is None or plan.race_date < today:
        return None
    return plan.race_name or "", plan.race_date, plan.race_km, plan.race_target_s


def _custom_race(goal: str | None, fields: dict, plan: CoachingPlan | None, today: date) -> tuple | None:
    """The race without a course the answers aim at — typed now (« Ajouter une course »), or the plan's own —, else
    None (a Route, or no race). Invalid says what is missing."""
    if goal == NEW_RACE:
        return co.clean_race(fields.get("race_name"), fields.get("race_date"), fields.get("race_km"),
                             fields.get("race_h"), fields.get("race_min"), today)
    if goal == OWN_RACE:
        own = _own_race(plan, today)
        if own is None:
            raise co.Invalid("Cette course n’est plus disponible.")
        return own
    return None


FIXED_FIELDS = ("fixed_day", "fixed_minutes", "fixed_name")


def _fixed_fields(values) -> dict:
    """« J’ai déjà une séance chaque semaine »: whether it is ticked, and its fields as the form sends them (kept to
    fill the form again)."""
    return {"on": bool(values.get("fixed_on")), **{k: str(values.get(k) or "").strip()[:80] for k in FIXED_FIELDS}}


def _fixed_answer(fields: dict | None) -> tuple | None:
    """The fixed session's answers, (day, minutes, name) as co.clean_fixed reads them; None when it is not ticked."""
    if not fields or not fields.get("on"):
        return None
    return fields.get("fixed_day"), fields.get("fixed_minutes"), fields.get("fixed_name")


def _intent(value) -> str | None:
    """The intent as the screen sends it; None when it sends none (a screen opened before the profiles)."""
    value = str(value or "").strip()
    return value if value in cp.INTENTS else None


async def _preview(db: AsyncSession, user: User, c: co.Clock, *, goal: str | None, days, long_day, base,
                   fields: dict | None = None, plan: CoachingPlan | None = None, intent: str | None = None,
                   fixed: dict | None = None) -> dict:
    """The onboarding's week, drawn from the answers as they stand (coaching_view.first_week: the generator's own
    sessions, with the intent's rules). Reads only. Answers that cannot make a plan give {"error"}: the screen says
    what is missing where the week would be."""
    try:
        clean_days, clean_long, clean_base = co.clean_answers(days, long_day, base)
        answer = _fixed_answer(fixed)
        own = co.clean_fixed(answer[0], answer[1], answer[2], clean_days, clean_long) if answer is not None else None
        if intent in (cp.ENTRETIEN, cp.PROGRESSER):  # no race aimed at
            target = cp.Goal()
        else:
            if intent == cp.COURSE and goal in (None, "", "none"):
                raise co.Invalid("Choisis ta course, ou ajoute-la.")
            custom = _custom_race(goal, fields or {}, plan, c.today)
            if custom is not None:
                target = co.custom_goal(*custom)
            else:
                target = await co.first_goal(db, user, int(goal) if goal and goal.isdigit() else None, c.today)
    except co.Invalid as e:
        picked = co.clean_days(days)
        # days out of range are said by the line under the days (`silent`: not twice)
        return {"error": str(e), "silent": not 2 <= len(picked) <= 6, "label": "", "aria": str(e), "empty": None,
                "cols": [{"wd": w, "bars": []} for w in WEEKDAYS], "legend": [], "first": None, "note": None,
                "lines": [], "level": None, "long_day": _int(long_day), "long_days": [(d, WEEKDAYS_L[d]) for d in picked]}
    level = None
    if intent is not None:  # the level the plan would read, from the same history (never asked)
        rows = await co.sessions_of(db, plan) if plan is not None else []
        level = cp.read_level(co.history(await co.runs_of(db, user.id, c), rows), c.today, clean_base)
    return {**cv.first_week(clean_days, clean_long, clean_base, target, c.today, intent=intent, level=level,
                            fixed=own),
            "error": None,
            "silent": False, "long_day": clean_long, "long_days": [(d, WEEKDAYS_L[d]) for d in clean_days]}


async def _onboarding(request: Request, db: AsyncSession, user: User, *, plan: CoachingPlan | None,
                      error: str | None = None, form: dict | None = None, status_code: int = 200,
                      course: int | None = None) -> Response:
    """One screen, a plan already proposed: every answer is filled in before the athlete touches anything (the next
    race, else « Progresser, sans course »; the days they usually run and the day of their usual longest run,
    coaching_start; the computed volume), and the week these answers make is drawn. `course`: a race chosen before
    the screen opens (the race page's « Préparer cette course avec Coaching »)."""
    c = await co.clock(db, user)
    races = await co.goal_options(db, user.id, c.today)
    if course is not None and not form and course in {r.id for r in races}:
        form = {"goal": str(course)}
    runs = await co.runs_of(db, user.id, c)
    computed = cp.starting_minutes(co.weekly_minutes(runs, c.today))
    if plan is not None:  # « Refaire le plan »: its own answers
        days, long_day = plan.weekdays, plan.long_day
    else:
        proposed = cst.proposal(runs, c.today)
        days, long_day = proposed.days, proposed.long_day
    own = _own_race(plan, c.today)
    aimed = bool(plan and (plan.route_id or own))
    kept = co.fixed_of(plan) if plan is not None else None
    chosen: dict[str, Any] = {
        "goal": str(plan.route_id) if plan and plan.route_id else OWN_RACE if own else (
            "none" if plan or not races else None),
        # the intent: the plan's own (a plan made before the profiles: « Préparer une course » when it aims at one,
        # else « Progresser »); a new plan: « Préparer une course » when a race is to come, else « Progresser »
        "intent": (plan.intent if plan.intent in cp.INTENTS and (plan.intent != cp.COURSE or aimed) else
                   cp.COURSE if aimed else cp.PROGRESSER) if plan else (cp.COURSE if races else cp.PROGRESSER),
        "days": list(days), "long_day": long_day,
        # the starting volume is computed again when the plan is made again (the plan's own without a history)
        "base": computed or (plan.base_minutes if plan else None), "race": _race_fields({}),
        # « Ma séance fixe »: the plan's own (« Refaire le plan »); else closed, no weekday guessed
        "fixed": {"on": True, "fixed_day": str(kept.day), "fixed_minutes": str(kept.minutes),
                  "fixed_name": "" if kept.name == cp.FIXED_NAME else kept.name} if kept else
        {"on": False, "fixed_day": "", "fixed_minutes": "", "fixed_name": ""}}
    if form:
        chosen.update(form)
    if chosen["goal"] in (None, "none") and races:
        chosen["goal"] = str(races[0].id)
    if chosen["goal"] in (None, "none") and chosen["intent"] == cp.COURSE:
        chosen["goal"] = NEW_RACE  # « Préparer une course » without one: its fields, open
    if not chosen["base"]:
        chosen["base"] = cp.START_CHOICES[1]  # no history: 2 h a week, until the athlete says otherwise
    base = chosen["base"]
    volumes = sorted({*range(60, 901, 15), base})
    pv = await _preview(db, user, c, goal=chosen["goal"], days=chosen["days"], long_day=chosen["long_day"], base=base,
                        fields=chosen["race"], plan=plan, intent=chosen["intent"], fixed=chosen["fixed"])
    options = [{"id": str(r.id), "name": r.name, "when": d_short(day), "j": rp.j_label(-(day - c.today).days)}
               for r, day in ((r, rp.race_day(r)) for r in races) if day is not None]
    if own is not None:  # the plan's race without a course, offered again beside the courses imported since
        options.append({"id": OWN_RACE, "name": own[0], "when": d_short(own[1]),
                        "j": rp.j_label(-(own[1] - c.today).days)})
    return templates.TemplateResponse(request, "coaching_onboarding.html", status_code=status_code, context={
        "user": user, "redo": plan is not None, "error": error, "chosen": chosen, "computed": computed, "pv": pv,
        "races": options, "today": c.today.isoformat(), "intents": cv.INTENT_CARDS,
        "fixed": _fixed_view(chosen["fixed"], cst.fixed_proposals(runs, c.today)),
        "race_max": (c.today + timedelta(days=co.RACE_AHEAD_DAYS)).isoformat(),
        "weekdays": list(enumerate(zip(WEEKDAYS, WEEKDAYS_L, strict=True))),
        "volumes": [(m, hm(m)) for m in volumes],
        "choices": [(m, f"{m // 60}\u00a0h" + (" et +" if m == cp.START_CHOICES[-1] else ""))
                    for m in cp.START_CHOICES]})


FIXED_DURATIONS = tuple(range(cp.FIXED_RANGE[0], cp.FIXED_RANGE[1] + 1, cp.STEP))


def _fixed_view(fields: dict, proposals: list[dict]) -> dict:
    """What « Ma séance fixe » draws, on the start screen and in the options' sheet: the weekday chips (one
    choice), the durations, the name, and for each weekday the duration proposed and why (coaching_start: read on
    the athlete's runs of that weekday — the screen fills it in when a weekday is chosen, never a weekday by
    itself)."""
    day = _int(fields.get("fixed_day"))
    minutes = _int(fields.get("fixed_minutes"))
    if minutes not in FIXED_DURATIONS:
        minutes = proposals[day]["minutes"] if day is not None and 0 <= day <= 6 else cp.FIXED_DEFAULT
    return {"on": bool(fields.get("on")), "day": day, "minutes": minutes, "name": fields.get("fixed_name") or "",
            "placeholder": cp.FIXED_NAME, "name_max": cp.FIXED_NAME_MAX,
            "why": proposals[day]["why"] if day is not None and 0 <= day <= 6 else "",
            "props": proposals, "durations": [(m, hm(m)) for m in FIXED_DURATIONS],
            "weekdays": list(enumerate(zip(WEEKDAYS, WEEKDAYS_L, strict=True)))}


async def _fixed_tool(db: AsyncSession, plan: CoachingPlan, c: co.Clock, runs: list | None) -> dict:
    """« Ma séance fixe » in the options' sheet: the plan's, or empty; its line when it sits next to the long run;
    what it says in its row's summary (« mercredi · 1h15 · Séance club »)."""
    kept = co.fixed_of(plan)
    runs = runs if runs is not None else await co.runs_of(db, plan.user_id, c)
    fields = ({"on": True, "fixed_day": str(kept.day), "fixed_minutes": str(kept.minutes),
               "fixed_name": "" if kept.name == cp.FIXED_NAME else kept.name} if kept else {"on": False})
    return {**_fixed_view(fields, cst.fixed_proposals(runs, c.today)), "set": kept is not None,
            "said": f"{WEEKDAYS_L[kept.day].capitalize()} · {hm(kept.minutes)} · {kept.name}" if kept else None,
            "warning": co.fixed_warning(kept, plan.long_day)}


async def _tools(db: AsyncSession, scope: Scope, plan: CoachingPlan, code: str | None = None,
                 runs: list | None = None) -> dict:
    # the form option: offered only with the health consent and an index in Santé; said « activé le … » once on
    c = await co.clock(db, scope.athlete)
    offer = await co.form_offer(db, plan, scope.athlete, c)
    if offer is not None and offer["since"] is not None:
        since = offer["since"] if offer["since"].tzinfo else offer["since"].replace(tzinfo=timezone.utc)
        offer = {**offer, "since": d_long(since.astimezone(c.zone).date())}
    return {"feed": feed_urls(plan), "coaches": [{"id": u.id, "name": u.firstname or "Quelqu’un"}
                                                 for u in await co.coaches_of(db, scope.athlete.id)], "code": code,
            "form": offer, "agenda": await _agenda(db, plan, c), "fixed": await _fixed_tool(db, plan, c, runs),
            "method": cv.METHOD}


def _read_word(row, c: co.Clock) -> str:
    """How a calendar's last reading went, in a few words (never anything the calendar's server said)."""
    if row.status is None:
        return ag.PENDING
    if row.status != ag.OK:
        return ag.STATUS.get(row.status, ag.STATUS["network"])
    at = (row.fetched_at if row.fetched_at.tzinfo else row.fetched_at.replace(tzinfo=timezone.utc)).astimezone(c.zone)
    day = "aujourd’hui" if at.date() == c.today else d_short(at.date())
    return f"Lu {day} à {cv.clock_time(at.hour * 60 + at.minute)}"


async def _agenda(db: AsyncSession, plan: CoachingPlan, c: co.Clock) -> dict:
    """« Mon agenda »: the connected accounts, then the addresses, masked, with how their last reading went; the
    buttons of the services configured and not connected yet (none while no connector is: the screen of before);
    the moments chosen."""
    calendars = sorted(await ag.calendars_of(db, plan.user_id), key=lambda row: row.provider == ag.ICAL)
    chosen = ag.moments_of(plan)
    connected = {row.provider for row in calendars}
    return {"calendars": [{"id": row.id, "masked": ag.label(row), "word": _read_word(row, c),
                           "ok": row.status in (None, ag.OK), "account": row.provider != ag.ICAL,
                           "reconnect": row.provider if row.status == "reauth" and accounts.ready(row.provider)
                           else None} for row in calendars],
            "connect": [{"key": p, "name": accounts.NAMES[p]} for p in accounts.active() if p not in connected],
            "accounts": bool(accounts.active()),
            "room": sum(1 for row in calendars if row.provider == ag.ICAL) < ag.MAX_CALENDARS,
            "moments": [(m, cs.MOMENT_WORDS[m], m in chosen) for m in cs.MOMENTS]}


def _queue_agenda(user_id: int) -> None:
    """The athlete's calendars read by a worker — never in this request (a user-typed address is fetched there,
    under services/safe_fetch's rules). A broker that is down: the 3-hourly round reads them."""
    from app.tasks.coaching import coaching_agenda

    try:
        coaching_agenda.apply_async(args=[user_id], retry=False)
    except Exception:
        logger.exception("Coaching: calendars not queued for user %d", user_id)


async def _page(request: Request, db: AsyncSession, scope: Scope, d: str | None, w: str | None, v: str | None,
                tool: str | None = None, tool_error: str | None = None, tool_note: str | None = None):
    """`tool`: what the options' sheet opens on (« agenda », « forme »…), with the agenda's error or note."""
    state = await _state(db, scope)
    await db.commit()
    p = cv.page(state, d=_day(d), w=_day(w), coach=scope.coach, **await _targets(db, scope, state),
                names=await _names(db, scope))
    return templates.TemplateResponse(request, "coaching.html", context={
        "user": scope.viewer, "scope": scope, "p": p,
        "athlete_name": scope.athlete.firstname or "Athlète", "error": None, "open": tool,
        "agenda_error": tool_error if tool == "agenda" else None, "agenda_note": tool_note,
        "fixed_error": tool_error if tool == "fixed" else None,
        "tools": None if scope.coach else await _tools(db, scope, state.plan, runs=state.runs)})


@router.get("/coaching", response_class=HTMLResponse)
async def coaching_page(request: Request, d: str | None = None, w: str | None = None, v: str | None = None,
                        refaire: str | None = None, course: str | None = None, outil: str | None = None,
                        connexion: str | None = None, resultat: str | None = None,
                        scope: Scope = Depends(own_scope), db: AsyncSession = Depends(get_db)):
    plan = await co.get_plan(db, scope.athlete.id)
    if plan is None and refaire != "1" and await co.athletes_of(db, scope.viewer.id):
        return RedirectResponse("/coaching/athletes", status_code=303)  # a coach without a plan of their own
    if plan is None or refaire == "1":
        return await _onboarding(request, db, scope.viewer, plan=plan, course=_int(course))
    # back from a Google Agenda or Outlook sign-in: its outcome in one line (two codes, never a message)
    said = accounts.result_line(connexion, resultat) if outil == "agenda" else None
    return await _page(request, db, scope, d, w, v, tool=TOOLS.get(outil or ""),
                       tool_error=said[0] if said and not said[1] else None,
                       tool_note=AGENDA_ASKED if outil == "agenda-lu" else said[0] if said and said[1] else None)


@router.get("/coaching/apercu", response_class=HTMLResponse)
async def coaching_preview(request: Request, scope: Scope = Depends(own_scope), db: AsyncSession = Depends(get_db)):
    """The onboarding's week, drawn again as a chip changes (htmx): the sessions the generator would make from the
    answers as they stand. Reads only — no plan, no session, nothing is written; always answered 200 (answers that
    cannot make a plan say so in the drawing's place)."""
    if not _htmx(request):  # an address someone opened by itself: the screen
        return RedirectResponse("/coaching", status_code=303)
    q = request.query_params
    user = scope.viewer
    pv = await _preview(db, user, await co.clock(db, user), goal=q.get("goal"), days=q.getlist("days"),
                        long_day=q.get("long_day"), base=q.get("base"), fields=_race_fields(q),
                        plan=await co.get_plan(db, user.id), intent=_intent(q.get("intent")), fixed=_fixed_fields(q))
    return templates.TemplateResponse(request, "partials/coaching_preview.html", context={"pv": pv, "part": "swap"})


@router.post("/coaching/plan")
async def coaching_save_plan(request: Request, scope: Scope = Depends(own_scope), db: AsyncSession = Depends(get_db)):
    """The onboarding's answers: the plan is made (or made again), its sessions generated."""
    form = await request.form()
    goal, days = str(form.get("goal") or "none"), form.getlist("days")
    user = scope.viewer
    c = await co.clock(db, user)
    fields = _race_fields(form)
    intent = _intent(form.get("intent"))
    try:
        if "intent" in form and intent is None:
            raise co.Invalid("Choisis ce que tu veux : garder la forme, progresser ou préparer une course.")
        aims = intent in (None, cp.COURSE)  # « Garder la forme » and « Progresser » aim at no race
        race = _custom_race(goal, fields, await co.get_plan(db, user.id), c.today) if aims else None
        route_id = int(goal) if aims and goal.isdigit() else None
        co.clean_intent(intent, race is not None or route_id is not None)
        plan = await co.save_plan(db, user, c, route_id=route_id, days=days, intent=intent,
                                  long_day=form.get("long_day"), base=form.get("base"), race=race,
                                  # « Ma séance fixe »: a screen opened before it does not ask (left as it was)
                                  fixed=_fixed_answer(_fixed_fields(form)) if "fixed_asked" in form else co.KEEP,
                                  # no longer asked here (« Mon agenda » asks it); a screen opened before still sends it
                                  moments=form.getlist("moments") if "moments_asked" in form else None)
    except co.Invalid as e:  # nothing was written: the answers are checked first. Answered 200: the app's shell
        # replaces any document answered 4xx by « Écran indisponible », hiding the message the form shows
        return await _onboarding(request, db, user, plan=await co.get_plan(db, user.id), error=str(e), status_code=200,
                                 form={"goal": goal, "days": list(co.clean_days(days)), "race": fields,
                                       "intent": intent or (cp.PROGRESSER if goal in ("", "none") else cp.COURSE),
                                       "long_day": _int(form.get("long_day")), "base": _int(form.get("base")),
                                       "fixed": _fixed_fields(form)})
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


@router.post("/coaching/form")
async def coaching_form(value: str = Form(default=""), scope: Scope = Depends(own_scope),
                        db: AsyncSession = Depends(get_db)):
    """« Adapter mes séances à ma forme »: the athlete's explicit switch, off by default. On: recorded with its
    moment; off: what the form had kept is cleared and the plan is its own again. The page is read again (today's
    session may change at once)."""
    plan = await co.get_plan(db, scope.athlete.id)
    if plan is None:
        raise HTTPException(status_code=404)
    try:
        await co.set_form(db, plan, scope.athlete, value == "on", await co.clock(db, scope.athlete))
    except co.Invalid:
        pass  # no longer offered (the consent withdrawn meanwhile): the page says so by not showing it
    await co.sync(db, plan, scope.athlete)
    await db.commit()
    logger.info("Coaching: form option %s for user %d", "on" if plan.form_on_at else "off", scope.athlete.id)
    return RedirectResponse("/coaching?outil=forme#coach-form", status_code=303)


# ── « Ma séance fixe » (docs/coaching.md › « La séance fixe ») ──────────────

FIXED = "/coaching?outil=fixe#coach-tool-fixed"


@router.post("/coaching/fixe")
async def coaching_fixed(request: Request, scope: Scope = Depends(own_scope), db: AsyncSession = Depends(get_db)):
    """« Ma séance fixe » set or changed: its weekday, duration and name kept on the plan, and the plan written again
    around it from today (co.set_fixed: the one regeneration path). A refusal is said in its row (answered 200: the
    app's shell replaces a 4xx document)."""
    plan = await _agenda_plan(db, scope)
    form = await request.form()
    try:
        fixed = co.clean_fixed(form.get("fixed_day"), form.get("fixed_minutes"), form.get("fixed_name"),
                               plan.weekdays, plan.long_day)
    except co.Invalid as e:
        return await _page(request, db, scope, None, None, None, tool="fixed", tool_error=str(e))
    await co.set_fixed(db, plan, scope.athlete, fixed)
    await db.commit()
    logger.info("Coaching: fixed session set for user %d", scope.athlete.id)
    return RedirectResponse(FIXED, status_code=303)


@router.post("/coaching/fixe/retirer")
async def coaching_fixed_remove(scope: Scope = Depends(own_scope), db: AsyncSession = Depends(get_db)):
    """« Retirer »: no fixed session any more; the plan places its own quality sessions again from today."""
    plan = await _agenda_plan(db, scope)
    await co.set_fixed(db, plan, scope.athlete, None)
    await db.commit()
    logger.info("Coaching: fixed session removed for user %d", scope.athlete.id)
    return RedirectResponse(FIXED, status_code=303)


# ── « Mon agenda »: the athlete's calendar addresses (never fetched in a request: app.tasks.coaching) ──

AGENDA = "/coaching?outil=agenda#coach-tool-agenda"
# ?outil=…: what the options' sheet opens on when the page comes back from one of its forms (without JavaScript: the
# same section at the end of the page, the fragment on the tool)
TOOLS = {"agenda": "agenda", "agenda-lu": "agenda", "forme": "form", "options": "options", "fixe": "fixed",
         "calendrier": "feed", "partage": "share", "methode": "how"}
AGENDA_ASKED = "Lecture demandée : recharge la page dans un instant."
AGENDA_WAIT = "Une actualisation par minute : réessaie dans un instant."
AGENDA_BUSY = "Trop de changements pour l’instant. Réessaie dans une heure."


async def _agenda_plan(db: AsyncSession, scope: Scope) -> CoachingPlan:
    plan = await co.get_plan(db, scope.athlete.id)
    if plan is None:
        raise HTTPException(status_code=404)
    return plan


@router.post("/coaching/agenda")
async def coaching_agenda_add(request: Request, url: str = Form(default=""), scope: Scope = Depends(own_scope),
                              db: AsyncSession = Depends(get_db)):
    """A calendar address added: checked as typed (https, a public host name), stored encrypted, then read by a
    worker. A refusal is said in the fold (answered 200: the app's shell replaces a 4xx document)."""
    from app.services.auth import over_quota

    await _agenda_plan(db, scope)
    error = None
    if await over_quota("coaching_agenda_save", user=scope.athlete.id):
        error = AGENDA_BUSY
    else:
        try:
            await ag.add_calendar(db, scope.athlete.id, url)
        except ag.Invalid as e:
            error = str(e)
    if error:
        return await _page(request, db, scope, None, None, None, tool="agenda", tool_error=error)
    await db.commit()  # the worker reads the address from its own session
    _queue_agenda(scope.athlete.id)
    logger.info("Coaching: calendar address added for user %d", scope.athlete.id)
    return RedirectResponse(AGENDA, status_code=303)


@router.post("/coaching/agenda/refresh")
async def coaching_agenda_refresh(request: Request, scope: Scope = Depends(own_scope),
                                  db: AsyncSession = Depends(get_db)):
    """« Actualiser »: the calendars read again by a worker, once a minute at most."""
    from app.services.auth import over_quota

    await _agenda_plan(db, scope)
    if await over_quota("coaching_agenda", user=scope.athlete.id):
        return await _page(request, db, scope, None, None, None, tool="agenda", tool_error=AGENDA_WAIT)
    _queue_agenda(scope.athlete.id)
    return RedirectResponse("/coaching?outil=agenda-lu#coach-tool-agenda", status_code=303)


@router.post("/coaching/agenda/moments")
async def coaching_agenda_moments(request: Request, scope: Scope = Depends(own_scope),
                                  db: AsyncSession = Depends(get_db)):
    """When the athlete can run (Matin, Midi, Soir; none ticked: all three): the times follow at once."""
    plan = await _agenda_plan(db, scope)
    ag.set_moments(plan, (await request.form()).getlist("moments"))
    await co.sync(db, plan, scope.athlete)
    await db.commit()
    return RedirectResponse(AGENDA, status_code=303)


@router.post("/coaching/agenda/{calendar_id}/remove")
async def coaching_agenda_remove(calendar_id: int, scope: Scope = Depends(own_scope),
                                 db: AsyncSession = Depends(get_db)):
    """An address removed, with the busy time read from it; the times are found again without it."""
    plan = await _agenda_plan(db, scope)
    if not await ag.remove_calendar(db, scope.athlete.id, calendar_id):
        raise HTTPException(status_code=404)
    await co.sync(db, plan, scope.athlete)
    await db.commit()
    logger.info("Coaching: calendar address removed for user %d", scope.athlete.id)
    return RedirectResponse(AGENDA, status_code=303)


# ── « Mon agenda »: Google Agenda and Outlook connected by OAuth (services/coaching_accounts) ──
# Every route answers 404 while its connector is not configured. The state and the PKCE verifier ride, sealed, in the
# signed session cookie for 10 minutes; the callback is refused in another browser or with another state.

AGENDA_PENDING = "coaching_agenda_oauth"


def _account(provider: str) -> None:
    if provider not in accounts.PROVIDERS or not accounts.ready(provider):
        raise HTTPException(status_code=404)


@router.get("/coaching/agenda/{provider}/connect")
async def coaching_agenda_connect(provider: str, request: Request, app: str | None = None,
                                  scope: Scope = Depends(own_scope), db: AsyncSession = Depends(get_db)):
    """« Connecter Google Agenda », « Connecter Outlook »: to the provider's consent page (the narrow scope, PKCE,
    a state). `app=1`: started in the app's sign-in sheet, which the callback closes."""
    from app.connectors import base

    _account(provider)
    await _agenda_plan(db, scope)
    verifier, challenge = base.pkce_pair()
    state = base.new_state()
    request.session[AGENDA_PENDING] = {"provider": provider, "state": base.seal(state),
                                       "verifier": base.seal(verifier), "app": app == "1"}
    return RedirectResponse(accounts.authorize_url(provider, state, challenge), status_code=302)


@router.get("/coaching/agenda/{provider}/callback")
async def coaching_agenda_callback(provider: str, request: Request, code: str | None = None,
                                   state: str | None = None, error: str | None = None,
                                   error_description: str | None = None, scope: Scope = Depends(own_scope),
                                   db: AsyncSession = Depends(get_db)):
    """The provider's answer: the state checked, the code exchanged with the PKCE verifier, the tokens kept
    encrypted, the busy time read by a worker; back on « Mon agenda » (or the app) with the outcome in one line.
    Nothing of the code, the tokens or the provider's own words is logged."""
    import httpx

    from app.connectors import base

    _account(provider)
    pending = request.session.pop(AGENDA_PENDING, None)
    pending = pending if isinstance(pending, dict) else {}
    app = pending.get("app") is True
    name = accounts.NAMES[provider]

    def back(result: str) -> RedirectResponse:
        return RedirectResponse(accounts.back_to(provider, result, app), status_code=303)

    if pending.get("provider") != provider or not base.state_matches(state, base.unseal(pending.get("state"))):
        return back("expire")
    verifier = base.unseal(pending.get("verifier"))
    if not verifier:
        return back("expire")
    if error:
        logger.info("Coaching: %s refused the agenda connection of user %d (%s)", name, scope.athlete.id,
                    str(error)[:40])
        return back(accounts.refusal(str(error), error_description))
    if not code:
        return back("refus")
    await _agenda_plan(db, scope)
    try:
        async with base.http_client(timeout=accounts.TIMEOUT_S) as client:
            tokens = await accounts.exchange(client, provider, code, verifier)
    except base.TokenError as e:
        logger.warning("Coaching: %s code exchange failed for user %d (%s)", name, scope.athlete.id, e.code)
        return back("refus")
    except (httpx.HTTPError, ValueError, KeyError) as e:
        logger.warning("Coaching: %s code exchange failed for user %d (%s)", name, scope.athlete.id,
                       type(e).__name__)
        return back("panne")
    await ag.connect_account(db, scope.athlete.id, provider, tokens)
    await db.commit()  # the worker reads the tokens from its own session
    _queue_agenda(scope.athlete.id)
    logger.info("Coaching: %s connected to the agenda of user %d", name, scope.athlete.id)
    return back("ok")


@router.post("/coaching/agenda/{provider}/app")
async def coaching_agenda_app(provider: str, scope: Scope = Depends(own_scope), db: AsyncSession = Depends(get_db)):
    """The app's button: the link of its sign-in sheet (Google refuses a WebView), signed in by a one-time ticket in
    the link's fragment (app.routers.mobile). The app opens it in the system's sheet."""
    from fastapi.responses import JSONResponse

    from app.routers import mobile

    _account(provider)
    await _agenda_plan(db, scope)
    link = f"{settings.BASE_URL.rstrip('/')}/mobile/oauth/agenda/{provider}#{mobile.agenda_ticket(scope.athlete, provider)}"
    return JSONResponse({"link": link}, headers={"Cache-Control": "no-store"})


@router.post("/coaching/share", response_class=HTMLResponse)
async def coaching_share(request: Request, scope: Scope = Depends(own_scope), db: AsyncSession = Depends(get_db)):
    """« Partager mon plan »: a code shown once, good for 7 days and one person."""
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
        return RedirectResponse("/coaching?outil=partage#coach-tool-share", status_code=303)
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
        return RedirectResponse("/coaching?outil=calendrier#coach-tool-feed", status_code=303)
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
        """A day or a week chosen; `move`: the session being moved (the days become its targets)."""
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
        """« Déplacer »: to any day from today on (services/coaching.move); an earlier day of this week when it
        holds a run no session took. Never refused for two hard days side by side: the calendar warns."""
        plan, row = await _row(db, scope, session_id)
        c = await co.clock(db, scope.athlete)
        target, error = _day(to), None
        try:
            if target is None:
                raise co.Invalid("Jour inconnu.")
            rows = await co.sessions_of(db, plan)
            free = {run.day for run in co.hors_plan(await co.runs_of(db, scope.athlete.id, c), rows)}
            co.move(rows, row, target, c.today, c.now, scope.origin, free)
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

    @r.post(prefix + "/sessions/{session_id}/slot", response_class=HTMLResponse)
    async def session_slot(session_id: int, request: Request, v: str = Form(default=""),
                           scope: Scope = Depends(scope_of), db: AsyncSession = Depends(get_db)):
        """« Autre créneau »: the next free time of the session's day, then the athlete's. Never a coach's route:
        a coach reads the times, nothing of the calendar behind them."""
        if scope.coach:
            raise HTTPException(status_code=404)
        _plan, row = await _row(db, scope, session_id)
        state = await _state(db, scope)
        error = None
        try:
            co.choose_slot(state, row)
        except co.Invalid as e:
            error = str(e)
        return await _after(request, db, scope, d=row.day, v=v, error=error)

    @r.post(prefix + "/sessions/{session_id}/lighten", response_class=HTMLResponse)
    async def session_lighten(session_id: int, request: Request, v: str = Form(default=""),
                              scope: Scope = Depends(scope_of), db: AsyncSession = Depends(get_db)):
        """« Alléger aujourd'hui »: the athlete's own tap, on a low morning, for a session the plan leaves alone (one
        they pinned, or their coach's). Never a coach's route: nothing of the form exists on their side."""
        if scope.coach:
            raise HTTPException(status_code=404)
        _plan, row = await _row(db, scope, session_id)
        state = await _state(db, scope)
        error = None
        try:
            co.lighten_by_hand(state.plan, scope.athlete, row, state.inputs, state.clock)
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
