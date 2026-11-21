"""Coaching (beta, docs/coaching.md): the plan in the database. The rules are coaching_plan's (pure); this module
reads the athlete's day and runs, stores the plan, matches its sessions with the recorded activities, re-reads the
adaptation and rewrites what a regeneration may rewrite. No AI.

`sync` is the one entry: called when a coaching page is rendered, after every change (a ressenti, a status, an
edit) and once a day for every plan (app.tasks.coaching). Nothing hooks into the provider syncs: a new activity is
matched the next time the plan is read (the page, the coach's page) or at the daily round at the latest.
"""
import hashlib
import logging
import secrets
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone, tzinfo

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import load_only

from app import features
from app.models.coaching import CoachingInvite, CoachingLink, CoachingPlan, CoachingSession
from app.models.route import Route
from app.models.user import User
from app.services import coaching_plan as cp
from app.services import health_consent
from app.services import race_prep as rp
from app.services import sante_training as st

logger = logging.getLogger(__name__)

INVITE_DAYS = 7  # a coach's code lives 7 days
CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"  # no 0/O, 1/I/L: read aloud or typed from a phone
CODE_LENGTH = 8
NOTE_MAX = 280
MINUTES_RANGE = (10, 600)  # what a typed duration is held to
MATCH_DAYS = 365  # sessions older than a year are no longer read against the activities
PLANNED, DONE, MISSED, CANCELLED = "planned", "done", "missed", "cancelled"


def is_run(s: st.Session) -> bool:
    """The activities the plan counts: the run family of sante_training.load_family (road, trail, treadmill)."""
    return st.load_family(s.sport) == "run"


@dataclass
class Clock:
    now: datetime
    zone: tzinfo
    today: date


async def clock(db: AsyncSession, user: User, now: datetime | None = None) -> Clock:
    """The athlete's « now », clock and day, as Activités and Santé read them."""
    now = now or datetime.now(timezone.utc)
    zone = await st.athlete_clock(db, user.id, user)
    today = await st.athlete_today(db, user.id, now, zone=zone)
    return Clock(now, zone, today)


async def runs_of(db: AsyncSession, user_id: int, c: Clock) -> list[st.Session]:
    return [s for s in await st.load_sessions(db, user_id, c.today, zone=c.zone) if is_run(s)]


def weekly_minutes(runs: list[st.Session], today: date, n: int = cp.BASE_WEEKS) -> list[float]:
    """Running minutes of each of the last `n` complete weeks (Monday to Sunday on the athlete's clock), oldest
    first."""
    m0 = cp.monday_of(today)
    by: dict[date, float] = {}
    for s in runs:
        by[cp.monday_of(s.day)] = by.get(cp.monday_of(s.day), 0) + s.minutes
    return [by.get(m0 - timedelta(weeks=i), 0.0) for i in range(n, 0, -1)]


def done_by_week(runs: list[st.Session], rows: list[CoachingSession]) -> dict[date, float]:
    """{Monday: minutes done}: every run of the week, in the plan or not, and the sessions ticked « Fait » without a
    recorded activity; the race itself (its session and the activity matched to it) left out."""
    race_ids = {r.activity_id for r in rows if r.kind == cp.RACE and r.activity_id}
    out: dict[date, float] = {}
    for s in runs:
        if s.id not in race_ids:
            out[cp.monday_of(s.day)] = out.get(cp.monday_of(s.day), 0) + s.minutes
    for r in rows:
        if r.status == DONE and r.activity_id is None and r.kind != cp.RACE:
            out[cp.monday_of(r.day)] = out.get(cp.monday_of(r.day), 0) + (r.done_minutes or r.minutes)
    return out


# ── the goal ────────────────────────────────────────────────────────────────

async def goal_options(db: AsyncSession, user_id: int, today: date) -> list[Route]:
    """The athlete's races to come, on foot, by date."""
    out = []
    for route in await rp.load_races(db, user_id, today):
        day = rp.race_day(route)
        if day is not None and day >= today and rp.sport(route) == "foot":
            out.append(route)
    return out


OBJECTIVE, ESTIMATE, ROUGH = "objectif", "estimation", "approx"  # which time the plan runs on (Goal.time_kind)
PASSED, GONE = "passee", "supprimee"  # why a plan lost its race (CoachingPlan.goal_state["ended"])
# what the plan reads of its race: the light columns, and the row's last change (a save on the race page)
GOAL_COLUMNS = (*rp.RACE_COLUMNS, Route.updated_at)


def goal_of(route, objective_s: int | None = None, estimate_s: int | None = None) -> cp.Goal:
    """The race aimed at. Its expected time: the race page's objective (`objective_s`), else the simulator's own
    estimate for this athlete (`estimate_s`: both from routers.simulator.race_times, the page's reading), else — no
    prediction at all — the saved time or race_prep's rough guess from the distance and the D+. A trail race also
    gives its D+ per hour of race (the climb the long runs aim at)."""
    rd = rp.race_day(route)
    if route is None or rd is None:
        return cp.Goal()
    km, up = route.total_distance_km or 0, route.total_elevation_gain or 0
    trail = (getattr(route, "sport_type", None) or "trail").lower() in ("trail", "ultra") or up >= 10 * km > 0
    if objective_s:
        seconds, kind = objective_s, OBJECTIVE
    elif estimate_s:
        seconds, kind = estimate_s, ESTIMATE
    else:
        seconds = rp.expected_s(route) or 3600
        kind = OBJECTIVE if getattr(route, "target_time_s", None) else ROUGH
    rate = up / (seconds / 3600) if trail and up > 0 else None
    return cp.Goal(rd, seconds / 60, trail, route.id, route.name or "", kind, rate)


async def plan_route(db: AsyncSession, plan: CoachingPlan):
    if not plan.route_id:
        return None
    return (await db.execute(select(Route).options(load_only(*GOAL_COLUMNS)).where(
        Route.id == plan.route_id, Route.user_id == plan.user_id))).scalar_one_or_none()


async def race_entry(db: AsyncSession, user: User, route) -> dict | None:
    """The race page's one line about Coaching, for a foot race still to come: {href, label} — the plan that
    prepares it (« Plan de coaching · semaine 5 sur 12 »), else the way to make one with this race chosen. None
    without Coaching, for another sport, a race behind or undated, or on any failure (the race page never blanks)."""
    try:
        rd = rp.race_day(route)
        if not features.coaching_for(user) or rd is None or rp.sport(route) != "foot":
            return None
        today = (await clock(db, user)).today
        if rd < today:
            return None
        plan = await get_plan(db, user.id)
        if plan is not None and plan.route_id == route.id:
            weeks = max(1, (cp.monday_of(rd) - plan.start_monday).days // 7 + 1)
            week = min(weeks, max(1, (cp.monday_of(today) - plan.start_monday).days // 7 + 1))
            return {"href": "/coaching", "label": f"Plan de coaching · semaine {week} sur {weeks}"}
        return {"href": f"/coaching?refaire=1&course={route.id}", "label": "Préparer cette course avec Coaching"}
    except Exception:
        logger.exception("Coaching: the race page's line failed for route %s", getattr(route, "id", None))
        return None


def signature(route) -> str:
    """What a save on the race page moves: its date, objective, distance, D+, start, and the row's last change."""
    return "|".join(str(getattr(route, name, None)) for name in (
        "id", "race_date", "target_time_s", "total_distance_km", "total_elevation_gain", "start_hour", "start_minute",
        "updated_at"))


async def race_times(db: AsyncSession, plan: CoachingPlan, route, today: date) -> tuple[int | None, int | None]:
    """(objective, estimate) of the plan's race in seconds, as its page prints them (routers.simulator.race_times:
    the simulator's own prediction, never a second estimator). A prediction reads the whole course: it is kept on
    the plan for the day (`goal_state`) and read again when the race page saved something (`signature`) or the day
    changed (the athlete's level moves the estimate). A failure reads as « no prediction » for the day: the plan
    then runs on the saved time or the rough guess, the page never blanks."""
    sig, kept = signature(route), plan.goal_state or {}
    if kept.get("sig") == sig and kept.get("day") == today.isoformat():
        return kept.get("objective_s"), kept.get("estimate_s")
    times = None
    try:
        from app.routers import simulator

        times = await simulator.race_times(db, plan.user_id, route.id)  # reads only
    except Exception:
        logger.exception("Coaching: the race's prediction failed for user %d", plan.user_id)
    objective, estimate = times or (None, None)
    plan.goal_state = {"route": route.id, "sig": sig, "day": today.isoformat(), "objective_s": objective,
                       "estimate_s": estimate}
    return objective, estimate


def goal_ended(plan: CoachingPlan) -> dict | None:
    """{ended, name, from (date)} while the plan says it lost its race, else None."""
    kept = plan.goal_state or {}
    if not kept.get("ended"):
        return None
    try:
        return {"ended": kept["ended"], "name": kept.get("name") or "", "from": date.fromisoformat(kept["from"])}
    except (KeyError, TypeError, ValueError):
        return None


async def follow_goal(db: AsyncSession, plan: CoachingPlan, route, rows: list, today: date):
    """The plan's race, or None once it is deleted (or lost its date) or behind: the plan then goes on as
    « Progresser, sans course » from the next week — the Monday after the race, or after the week its deletion is
    seen — which becomes its week 0; this week's sessions stay as they are, the deleted race's own session goes. One
    line says so (`goal_state`) until that first week is over. Returns (route, rows)."""
    ended = goal_ended(plan)
    if ended is not None:
        if today >= ended["from"] + timedelta(weeks=1):
            plan.goal_state = None
        return None, rows
    rd = rp.race_day(route)
    kept = plan.goal_state or {}
    if rd is not None and rd >= today:
        return route, rows
    race_rows = [r for r in rows if r.kind == cp.RACE and r.status == PLANNED and r.day >= today
                 and r.origin == "auto"]
    if rd is not None:  # behind
        kind, start, name = PASSED, cp.monday_of(rd) + timedelta(weeks=1), route.name or ""
    elif plan.route_id or kept.get("route") or race_rows:  # deleted, or its date removed on its page
        kind, start = GONE, cp.monday_of(today) + timedelta(weeks=1)
        name = (route.name if route is not None else None) or next(
            ((r.structure or {}).get("text") for r in race_rows), None) or ""
    else:
        return None, rows  # « Progresser, sans course » from the start
    for r in race_rows:
        rows = [x for x in rows if x is not r]
        await db.delete(r)
    plan.route_id, plan.start_monday = None, start
    plan.goal_state = {"ended": kind, "name": name, "from": start.isoformat()}
    return None, rows


def inputs(plan: CoachingPlan, goal: cp.Goal) -> cp.Inputs:
    return cp.Inputs(plan.weekdays, plan.long_day, plan.base_minutes, plan.start_monday, goal,
                     dict(plan.adaptations or {}))


# ── the plan's rows ─────────────────────────────────────────────────────────

async def get_plan(db: AsyncSession, user_id: int) -> CoachingPlan | None:
    return (await db.execute(select(CoachingPlan).where(CoachingPlan.user_id == user_id))).scalar_one_or_none()


async def sessions_of(db: AsyncSession, plan: CoachingPlan) -> list[CoachingSession]:
    return list((await db.execute(select(CoachingSession).where(CoachingSession.plan_id == plan.id)
                                  .order_by(CoachingSession.day, CoachingSession.id))).scalars().all())


def clean_days(values) -> tuple[int, ...]:
    out = set()
    for v in values or ():
        try:
            d = int(v)
        except (TypeError, ValueError):
            continue
        if 0 <= d <= 6:
            out.add(d)
    return tuple(sorted(out))


class Invalid(ValueError):
    """What the athlete typed cannot make a plan; its message is shown as is."""


async def save_plan(db: AsyncSession, user: User, c: Clock, *, route_id: int | None, days, long_day,
                    base) -> CoachingPlan:
    """The onboarding's answers kept: a new plan, or the same one made again (« Refaire le plan »: week 0 is this
    week again, what the ressenti changed is forgotten; the sessions done, past or edited by a person stay)."""
    days = clean_days(days)
    if not 2 <= len(days) <= 6:
        raise Invalid("Choisis entre 2 et 6 jours.")
    try:
        long_day = int(long_day)
    except (TypeError, ValueError):
        long_day = cp.default_long_day(days)
    if long_day not in days:
        long_day = cp.default_long_day(days)
    try:
        base = int(cp.clamp(cp.round5(float(base)), *cp.BASE_RANGE))
    except (TypeError, ValueError) as e:
        raise Invalid("Indique ton volume de départ.") from e
    if route_id is not None and route_id not in {r.id for r in await goal_options(db, user.id, c.today)}:
        raise Invalid("Cette course n’est plus disponible.")
    plan = await get_plan(db, user.id)
    if plan is None:
        plan = CoachingPlan(user_id=user.id)
        db.add(plan)
        new_feed_token(plan)
    plan.route_id, plan.days, plan.long_day, plan.base_minutes = route_id, ",".join(map(str, days)), long_day, base
    plan.start_monday, plan.adaptations, plan.generated_at = cp.monday_of(c.today), None, c.now
    plan.goal_state = None  # the race's times are read again; a lost race's line goes
    await db.flush()
    return plan


async def delete_plan(db: AsyncSession, user_id: int) -> None:
    """« Arrêter le coaching »: the plan and its sessions go (cascade), and with them the sharing: the coaches'
    links and the codes not used yet."""
    await db.execute(delete(CoachingSession).where(CoachingSession.user_id == user_id))
    await db.execute(delete(CoachingPlan).where(CoachingPlan.user_id == user_id))
    await db.execute(delete(CoachingLink).where(CoachingLink.athlete_id == user_id))
    await db.execute(delete(CoachingInvite).where(CoachingInvite.athlete_id == user_id))


def touch(row: CoachingSession, now: datetime) -> None:
    """A change a calendar must see: the feed's SEQUENCE and LAST-MODIFIED."""
    row.seq = (row.seq or 0) + 1
    row.updated_at = now


# ── matching with the recorded activities ───────────────────────────────────

def match(rows: list, runs: list, today: date) -> bool:
    """Each session against the runs of its local day; True when a row changed.
    - a run on the session's day marks it done and is linked to it; several that day: the closest duration first;
      its minutes are the activity's;
    - a session ticked « Fait » by hand takes the run when it arrives; one ticked « Pas faite » by hand is left alone;
    - a link whose activity is gone (deleted, or no longer that day's) is undone, the session read again;
    - a planned session whose day is over, without a run: missed."""
    changed = False
    by_day: dict[date, list] = {}
    for s in runs:
        by_day.setdefault(s.day, []).append(s)
    since = today - timedelta(days=MATCH_DAYS)  # within the runs read (sante_training.HISTORY_DAYS)
    live = [r for r in rows if r.status != CANCELLED and since <= r.day <= today]
    for r in live:
        acts = {s.id: s for s in by_day.get(r.day, [])}
        if r.activity_id is not None and r.activity_id not in acts:
            r.activity_id, changed = None, True
        if r.activity_id is None and r.status == DONE and not r.manual:
            r.status, r.done_minutes, changed = PLANNED, None, True
        if r.activity_id is not None:
            minutes = int(round(acts[r.activity_id].minutes))
            if r.status != DONE or r.done_minutes != minutes:
                r.status, r.done_minutes, changed = DONE, minutes, True
    linked = {r.activity_id for r in rows if r.activity_id is not None}
    for day in sorted({r.day for r in live}):
        free = [s for s in by_day.get(day, []) if s.id not in linked]
        open_rows = [r for r in live if r.day == day and r.activity_id is None
                     and not (r.manual and r.status == MISSED)]
        pairs = sorted(((abs(r.minutes - s.minutes), r.id or 0, s.id, r, s) for r in open_rows for s in free),
                       key=lambda p: p[:3])
        used_rows, used_runs = set(), set()
        for _gap, _rid, sid, r, s in pairs:
            if id(r) in used_rows or sid in used_runs:
                continue
            used_rows.add(id(r))
            used_runs.add(sid)
            linked.add(sid)
            r.activity_id, r.status, r.done_minutes, changed = sid, DONE, int(round(s.minutes)), True
    for r in live:
        if r.status == PLANNED and r.day < today:
            r.status, changed = MISSED, True
    return changed


def hors_plan(runs: list, rows: list) -> list:
    """The runs no session holds."""
    linked = {r.activity_id for r in rows if r.activity_id is not None}
    return [s for s in runs if s.id not in linked]


# ── the adaptation, kept on the plan ────────────────────────────────────────

def read_adaptations(plan: CoachingPlan, rows: list, done: dict[date, float], today: date,
                     fresh_base: int | None, low: list[date] | None = None) -> bool:
    """This week's and next week's adaptation read again from the sessions (coaching_plan.adaptation) and kept on
    the plan with their reason; the weeks behind keep what they were given. Week 0 (the week the plan was made, or
    made again) is always as chosen. A restart keeps the base it was first given. `low`: the low mornings of an
    athlete who turned the form option on (three within five days lighten the next week: one reduction, the larger
    of it and the ressenti's). True when something changed."""
    facts = [cp.Fact(r.day, r.kind, r.status, r.ressenti) for r in rows if r.status != CANCELLED]
    kept = dict(plan.adaptations or {})
    m0 = cp.monday_of(today)
    for week, closed in ((m0, True), (m0 + timedelta(weeks=1), False)):
        if week < plan.start_monday:
            continue  # a week before week 0 (the plan lost its race: this week stays as it was)
        key = week.isoformat()
        new = cp.adaptation(facts, week, done, fresh_base, closed) if week > plan.start_monday else None
        if low and week > plan.start_monday and cp.form_week(low, week):
            new = cp.with_form(new)
        old = kept.get(key)
        if new and old and new["reason"] == old.get("reason") == "reprise":
            new = old
        if new:
            kept[key] = new
        else:
            kept.pop(key, None)
    if kept != (plan.adaptations or {}):
        plan.adaptations = kept or None
        return True
    return False


# ── regeneration ────────────────────────────────────────────────────────────

def replaceable(row: CoachingSession, today: date) -> bool:
    """What a regeneration may rewrite: a generated session still to come that nobody edited, did or missed."""
    return (row.origin == "auto" and not row.pinned and row.status == PLANNED and row.day >= today
            and not row.form)  # what a low morning lightened or moved stays (services/coaching.lighten)


async def apply(db: AsyncSession, plan: CoachingPlan, rows: list[CoachingSession], weeks: list[cp.Week], today: date,
          now: datetime) -> list[CoachingSession]:
    """The generated sessions from today on written over the replaceable rows, each found by the day the generator
    gave it (`auto_day`: a session moved by a person keeps it, so its old day is not filled again; one a coach
    removed stays as a cancelled row for the same reason). Rows are updated in place: a session keeps its id (the
    calendar feed's UID). Returns the rows as they now stand."""
    generated = {s.day: s for w in weeks for s in w.sessions if s.day >= today}
    by_auto = {r.auto_day: r for r in rows if r.auto_day is not None}
    out = list(rows)
    for day, g in generated.items():
        row = by_auto.get(day)
        if row is None:
            row = CoachingSession(plan_id=plan.id, user_id=plan.user_id, day=day, auto_day=day, kind=g.kind,
                                  minutes=g.minutes, structure=g.structure, status=PLANNED, origin="auto",
                                  manual=False, pinned=False, seq=0, updated_at=now)
            db.add(row)
            out.append(row)
        elif replaceable(row, today) and (row.kind, row.minutes, row.structure) != (g.kind, g.minutes, g.structure):
            row.kind, row.minutes, row.structure = g.kind, g.minutes, g.structure
            touch(row, now)
    for row in rows:
        if row.auto_day is not None and replaceable(row, today) and row.auto_day not in generated:
            out.remove(row)
            await db.delete(row)
    return sorted(out, key=lambda r: (r.day, r.id or 0))


@dataclass
class State:
    """What a page needs once the plan is in sync."""
    plan: CoachingPlan
    clock: Clock
    goal: cp.Goal
    rows: list[CoachingSession]
    runs: list
    weeks: list[cp.Week]
    inputs: cp.Inputs
    low: tuple[date, ...] = ()  # the low mornings, only for an athlete who turned the form option on


async def sync(db: AsyncSession, plan: CoachingPlan, user: User, now: datetime | None = None) -> State:
    """The plan brought up to date: the sessions matched with the runs, the missed ones marked, the adaptation read
    again, the sessions to come regenerated (only those a regeneration may rewrite). Idempotent: a second call
    writes nothing."""
    c = await clock(db, user, now)
    runs = await runs_of(db, user.id, c)
    # one reading at a time per plan (the page, the coach's, the daily round): the second waits, then sees what the
    # first wrote, so a session is never generated twice
    await db.execute(select(CoachingPlan.id).where(CoachingPlan.id == plan.id).with_for_update())
    rows = await sessions_of(db, plan)
    match(rows, runs, c.today)
    route, rows = await follow_goal(db, plan, await plan_route(db, plan), rows, c.today)
    goal = goal_of(route, *(await race_times(db, plan, route, c.today) if route is not None else ()))
    fresh = cp.starting_minutes(weekly_minutes(runs, c.today))
    had_form = plan.form_on_at is not None
    low = await read_form(db, plan, user, c)  # nothing is read of Santé unless the athlete turned the option on
    if had_form and plan.form_on_at is None:  # the consent is no longer given: the form's marks were just cleared
        rows = await sessions_of(db, plan)
    read_adaptations(plan, rows, done_by_week(runs, rows), c.today, fresh, low)
    inp = inputs(plan, goal)
    # a plan that just lost its race starts again next Monday: until then nothing of this week is rewritten
    since = max(c.today, plan.start_monday)
    weeks = cp.build(inp, since)
    rows = await apply(db, plan, rows, weeks, since, c.now)
    if c.today in low:
        rows = lighten_today(db, plan, rows, inp, c)
    match(rows, runs, c.today)  # a session made just now for today takes today's run at once
    await db.flush()
    return State(plan, c, goal, rows, runs, weeks, inp, tuple(low))


# ── the form: Santé's index, for an athlete who turned the option on ────────
# docs/coaching.md › « La forme ». Health data (GDPR art. 9): read only with the option on AND the health consent
# given; what is kept is the dates of the low mornings (never the index, a measurement or a reason), cleared when
# the option is turned off, the consent withdrawn or the plan deleted. A coach is never shown any of it.

HARD_KINDS = (cp.LONG, cp.QUALITY, cp.HILLS)  # what a low morning lightens (never the race)


def form_on(plan: CoachingPlan, user: User) -> bool:
    return plan.form_on_at is not None and health_consent.given(user)


def low_mornings(plan: CoachingPlan) -> list[date]:
    out = []
    for value in (plan.form_state or {}).get("low") or []:
        try:
            out.append(date.fromisoformat(value))
        except (TypeError, ValueError):
            continue
    return sorted(out)


async def form_offer(db: AsyncSession, plan: CoachingPlan, user: User, c: Clock) -> dict | None:
    """What the page shows of the option: None, not offered (no health consent, or Santé has no index: one yes/no
    asked of Santé, no value read); else {"on", "since"}."""
    if not health_consent.given(user):
        return None
    if plan.form_on_at is not None:
        return {"on": True, "since": plan.form_on_at}
    try:
        from app.services import sante

        return {"on": False, "since": None} if await sante.has_index(db, user.id, c.today, c.zone) else None
    except Exception:
        logger.exception("Coaching: Santé's index could not be asked for user %d", user.id)
        return None


async def set_form(db: AsyncSession, plan: CoachingPlan, user: User, on: bool, c: Clock) -> None:
    """The switch: on records when (only with the health consent and an index to read); off clears everything."""
    if not on:
        await clear_form(db, plan.user_id)
        return
    offer = await form_offer(db, plan, user, c)
    if offer is None:
        raise Invalid("Cette option demande tes données de santé et un indice de récupération dans Santé.")
    if not offer["on"]:
        plan.form_on_at, plan.form_state = c.now, None


async def clear_form(db: AsyncSession, user_id: int) -> None:
    """The form's stored state gone (the option turned off, the health consent withdrawn): when it was turned on,
    the low mornings, the weeks it lightened, the marks on its sessions — which the next regeneration then rewrites
    as the plan's own again; a session it had moved to an empty day goes."""
    plan = await get_plan(db, user_id)
    if plan is None:
        return
    plan.form_on_at, plan.form_state = None, None
    kept = {k: v for k, v in (plan.adaptations or {}).items() if (v or {}).get("reason") != cp.FORM}
    if kept != (plan.adaptations or {}):
        plan.adaptations = kept or None
    await db.execute(delete(CoachingSession).where(
        CoachingSession.user_id == user_id, CoachingSession.form == cp.MOVED, CoachingSession.auto_day.is_(None),
        CoachingSession.status == PLANNED))
    await db.execute(update(CoachingSession).where(CoachingSession.user_id == user_id,
                                                   CoachingSession.form.is_not(None)).values(form=None))
    await db.flush()


async def read_form(db: AsyncSession, plan: CoachingPlan, user: User, c: Clock) -> list[date]:
    """The low mornings (their dates), this morning's read from Santé when it has an index: once it is read the day
    is settled (idempotent, no flapping); while the night has not arrived it is asked again, at most every
    FORM_RETRY_MIN minutes. Without the option, nothing is read and [] comes back; a consent no longer given clears
    what was kept. Only today is ever read: the past mornings stay as they were seen."""
    if plan.form_on_at is None:
        return []
    if not health_consent.given(user):
        await clear_form(db, plan.user_id)
        return []
    today, kept = c.today.isoformat(), dict(plan.form_state or {})
    floor = (c.today - timedelta(days=cp.FORM_KEEP_DAYS)).isoformat()
    low = sorted({d for d in kept.get("low") or [] if isinstance(d, str) and floor <= d <= today})
    state: dict = kept if kept.get("day") == today else {"day": today, "seen": False, "checked": None}
    if not state.get("seen") and _due(state.get("checked"), c.now):
        reading = None
        try:
            from app.services import sante

            reading = await sante.morning_index(db, user.id, c.today, c.zone)
        except Exception:
            logger.exception("Coaching: this morning's index could not be read for user %d", user.id)
        state = {**state, "checked": c.now.isoformat()}
        if reading is not None:
            state["seen"] = True
            if reading["low"] and today not in low:
                low.append(today)
    state = {**state, "low": low}
    if state != (plan.form_state or {}):
        plan.form_state = state
    return [date.fromisoformat(d) for d in low]


def _due(checked: str | None, now: datetime) -> bool:
    try:
        return checked is None or now - datetime.fromisoformat(checked) >= timedelta(minutes=cp.FORM_RETRY_MIN)
    except (TypeError, ValueError):
        return True


def _taper(inp: cp.Inputs, day: date) -> bool:
    return cp.phase_of(cp.monday_of(day), inp) == "affutage"


def lighten_today(db: AsyncSession, plan: CoachingPlan, rows: list[CoachingSession], inp: cp.Inputs,
                  c: Clock) -> list[CoachingSession]:
    """A low morning: today's hard session (a quality session, a long run), generated and untouched, becomes an
    easy footing (coaching_plan.form_footing) and moves to the next available day of the week that keeps « never
    two hard days in a row » (coaching_plan.move_day), else is dropped. A session a person pinned or a coach wrote
    is left alone (the page offers « Alléger aujourd'hui »); in a taper week only the swap to a footing, its
    duration kept. Both rows are marked (`form`): a regeneration leaves them, a second reading changes nothing."""
    today, out = c.today, list(rows)
    for row in [r for r in rows if r.day == today and r.kind in HARD_KINDS and replaceable(r, today)]:
        taper = _taper(inp, today)
        was = (row.kind, row.minutes, row.structure)
        target = None
        if not taper:
            horizon = cp.monday_of(today) + timedelta(days=7)
            slots = [cp.Slot(r.day, r.kind, replaceable(r, today)) for r in out
                     if r.status != CANCELLED and today <= r.day <= horizon]
            target = cp.move_day(today, plan.weekdays, slots, row.kind, inp.goal.race_day)
        there = next((r for r in out if r.day == target and r.status != CANCELLED), None) if target else None
        minutes = cp.form_footing(row.minutes, taper)
        if there is not None:  # it takes that easy session's place: the day's footing is no longer than it was
            minutes = min(minutes, there.minutes)
        row.kind, row.minutes, row.structure, row.form = cp.FOOTING, minutes, cp.steady(minutes, 2), cp.LIGHTENED
        touch(row, c.now)
        if there is not None:
            there.kind, there.minutes, there.structure, there.form = *was, cp.MOVED
            touch(there, c.now)
        elif target is not None:
            moved = CoachingSession(plan_id=plan.id, user_id=plan.user_id, day=target, auto_day=None, kind=was[0],
                                    minutes=was[1], structure=was[2], status=PLANNED, origin="auto", manual=False,
                                    pinned=False, seq=0, updated_at=c.now, form=cp.MOVED)
            db.add(moved)
            out.append(moved)
    return sorted(out, key=lambda r: (r.day, r.id or 0))


def lighten_by_hand(plan: CoachingPlan, user: User, row: CoachingSession, inp: cp.Inputs, c: Clock) -> None:
    """« Alléger aujourd'hui »: the athlete's own tap on a session the plan would not change by itself (pinned, or a
    coach's) on a low morning. It becomes the same footing; nothing is moved (the session stays theirs to place)."""
    if not form_on(plan, user) or c.today not in low_mornings(plan):
        raise Invalid("Rien à alléger ce matin.")
    if row.day != c.today or row.status != PLANNED or row.kind not in HARD_KINDS:
        raise Invalid("Cette séance ne s’allège pas.")
    minutes = cp.form_footing(row.minutes, _taper(inp, c.today))
    row.kind, row.minutes, row.structure = cp.FOOTING, minutes, cp.steady(minutes, 2)
    row.form, row.pinned, row.origin = cp.LIGHTENED, True, "athlete"
    touch(row, c.now)


async def starting_volume(db: AsyncSession, user: User, c: Clock) -> int | None:
    """The onboarding's computed value: the median weekly running minutes of the last 4 complete weeks; None
    without any (the athlete picks one of coaching_plan.START_CHOICES)."""
    return cp.starting_minutes(weekly_minutes(await runs_of(db, user.id, c), c.today))


# ── the calendar feed's token ───────────────────────────────────────────────

def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_feed_token(plan: CoachingPlan) -> str:
    """A new feed link (the old one stops working): 32 random bytes, stored encrypted (its owner reads the link
    again on the page) and by its SHA-256 (what a request is looked up by, as the API keys)."""
    token = secrets.token_urlsafe(32)
    plan.feed_token, plan.feed_token_hash = token, token_hash(token)
    return token


async def plan_by_feed_token(db: AsyncSession, token: str) -> CoachingPlan | None:
    if not token or len(token) > 128:
        return None
    digest = token_hash(token)
    plan = (await db.execute(select(CoachingPlan).where(CoachingPlan.feed_token_hash == digest))).scalar_one_or_none()
    if plan is None or not secrets.compare_digest(plan.feed_token_hash or "", digest):
        return None
    return plan


# ── athlete → coach ─────────────────────────────────────────────────────────

def normal_code(code: str) -> str:
    return "".join(ch for ch in (code or "").upper() if ch.isalnum())


def code_hash(code: str) -> str:
    return hashlib.sha256(("coaching|" + normal_code(code)).encode()).hexdigest()


async def new_invite(db: AsyncSession, athlete_id: int, now: datetime) -> str:
    """A code for a coach, shown once: the earlier ones not used yet stop working."""
    await db.execute(delete(CoachingInvite).where(CoachingInvite.athlete_id == athlete_id,
                                                  CoachingInvite.used_at.is_(None)))
    code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))
    db.add(CoachingInvite(athlete_id=athlete_id, code_hash=code_hash(code), created_at=now,
                          expires_at=now + timedelta(days=INVITE_DAYS)))
    await db.flush()
    return f"{code[:4]}-{code[4:]}"


async def accept_invite(db: AsyncSession, coach: User, code: str, now: datetime) -> User | None:
    """The coach types the code: the link is made, the code is spent. None for a code unknown, used, expired, the
    coach's own, or of an athlete who no longer has Coaching or a plan (one answer for all: nothing is told apart)."""
    if len(normal_code(code)) != CODE_LENGTH:
        return None
    invite = (await db.execute(select(CoachingInvite).where(CoachingInvite.code_hash == code_hash(code))
                               .with_for_update())).scalar_one_or_none()
    if invite is None or invite.used_at is not None or invite.athlete_id == coach.id:
        return None
    expires = invite.expires_at if invite.expires_at.tzinfo else invite.expires_at.replace(tzinfo=timezone.utc)
    if expires <= now:
        return None
    athlete = await db.get(User, invite.athlete_id)
    if athlete is None or not features.coaching_for(athlete) or await get_plan(db, athlete.id) is None:
        return None
    invite.used_at = now
    exists = (await db.execute(select(CoachingLink).where(CoachingLink.athlete_id == athlete.id,
                                                          CoachingLink.coach_id == coach.id))).scalar_one_or_none()
    if exists is None:
        db.add(CoachingLink(athlete_id=athlete.id, coach_id=coach.id, created_at=now))
    await db.flush()
    return athlete


async def link_of(db: AsyncSession, athlete_id: int, coach_id: int) -> CoachingLink | None:
    return (await db.execute(select(CoachingLink).where(CoachingLink.athlete_id == athlete_id,
                                                        CoachingLink.coach_id == coach_id))).scalar_one_or_none()


async def unlink(db: AsyncSession, athlete_id: int, coach_id: int) -> None:
    await db.execute(delete(CoachingLink).where(CoachingLink.athlete_id == athlete_id,
                                                CoachingLink.coach_id == coach_id))


async def coaches_of(db: AsyncSession, athlete_id: int) -> list[User]:
    return list((await db.execute(select(User).join(CoachingLink, CoachingLink.coach_id == User.id)
                                  .where(CoachingLink.athlete_id == athlete_id)
                                  .order_by(CoachingLink.created_at, CoachingLink.id))).scalars().all())


async def athletes_of(db: AsyncSession, coach_id: int) -> list[User]:
    """The athletes this coach follows who still have Coaching."""
    users = (await db.execute(select(User).join(CoachingLink, CoachingLink.athlete_id == User.id)
                              .where(CoachingLink.coach_id == coach_id)
                              .order_by(CoachingLink.created_at, CoachingLink.id))).scalars().all()
    return [u for u in users if features.coaching_for(u)]


# ── a person's changes to a session ─────────────────────────────────────────

def clean_minutes(value) -> int:
    try:
        return int(cp.clamp(cp.round5(float(value)), *MINUTES_RANGE))
    except (TypeError, ValueError) as e:
        raise Invalid("Indique une durée en minutes.") from e


def clean_text(value: str | None) -> str | None:
    text = " ".join((value or "").split())[:NOTE_MAX]
    return text or None


def set_done(row: CoachingSession, today: date, minutes=None) -> None:
    """« Fait », for a session without watch data (optional minutes)."""
    if row.day > today:
        raise Invalid("Cette séance est à venir.")
    row.status, row.manual = DONE, True
    if row.activity_id is None:
        row.done_minutes = clean_minutes(minutes) if str(minutes or "").strip() else row.minutes


def set_missed(row: CoachingSession, today: date) -> None:
    """« Pas faite »: the run matched to it, if any, goes back out of the plan."""
    if row.day > today:
        raise Invalid("Cette séance est à venir.")
    row.status, row.manual, row.activity_id, row.done_minutes, row.ressenti = MISSED, True, None, None, None


def reset_status(row: CoachingSession) -> None:
    """A tick undone: the session is planned again, the next sync reads it against the day's runs."""
    row.status, row.manual, row.activity_id, row.done_minutes = PLANNED, False, None, None


def set_ressenti(row: CoachingSession, value) -> None:
    try:
        level = int(value)
    except (TypeError, ValueError) as e:
        raise Invalid("Ressenti inconnu.") from e
    if row.status != DONE or not 1 <= level <= 5:
        raise Invalid("Ressenti inconnu.")
    row.ressenti = level


def edit(row: CoachingSession, now: datetime, *, kind: str, minutes, origin: str) -> None:
    """« Modifier »: the duration and the kind; the session is pinned (a regeneration never rewrites it)."""
    if row.kind == cp.RACE:
        raise Invalid("La course se modifie sur sa page.")
    if kind not in cp.EDITABLE:
        raise Invalid("Type de séance inconnu.")
    minutes = clean_minutes(minutes)
    if (kind, minutes) != (row.kind, row.minutes):
        row.kind, row.minutes, row.structure = kind, minutes, cp.default_structure(kind, minutes)
        touch(row, now)
    row.pinned, row.origin = True, origin


def move(rows: list[CoachingSession], row: CoachingSession, target: date, today: date, now: datetime,
         origin: str) -> None:
    """« Déplacer »: a planned session to another day from today on; a planned session already there takes its day
    (they swap). Both are pinned."""
    if row.status != PLANNED or row.kind == cp.RACE:
        raise Invalid("Cette séance ne se déplace pas.")
    if target < today or row.day < today:
        raise Invalid("Choisis un jour à partir d’aujourd’hui.")
    if target == row.day:
        return
    other = next((r for r in rows if r is not row and r.day == target and r.status == PLANNED
                  and r.kind != cp.RACE), None)
    if other is not None:
        other.day, other.pinned = row.day, True
        touch(other, now)
    row.day, row.pinned, row.origin = target, True, origin
    touch(row, now)


def add(db: AsyncSession, plan: CoachingPlan, day: date, today: date, now: datetime, *, kind: str, minutes,
        origin: str) -> CoachingSession:
    if day < today:
        raise Invalid("Choisis un jour à partir d’aujourd’hui.")
    if kind not in cp.EDITABLE:
        raise Invalid("Type de séance inconnu.")
    minutes = clean_minutes(minutes)
    row = CoachingSession(plan_id=plan.id, user_id=plan.user_id, day=day, auto_day=None, kind=kind, minutes=minutes,
                          structure=cp.default_structure(kind, minutes), status=PLANNED, origin=origin, pinned=True,
                          manual=False, seq=0, updated_at=now)
    db.add(row)
    return row


async def remove(db: AsyncSession, row: CoachingSession, today: date, now: datetime) -> None:
    """A coach removes a session: a generated one still to come stays as a cancelled row (so that the regeneration
    does not make it again; the calendar feed leaves it out), any other is deleted."""
    if row.auto_day is not None and row.auto_day >= today:
        row.status, row.pinned, row.activity_id, row.done_minutes = CANCELLED, True, None, None
        touch(row, now)
    else:
        await db.delete(row)
