"""Coaching: a recorded run that is not the day's session (docs/coaching.md › « Une sortie qui n’est pas la séance
prévue »).

The owner, 25 Nov 2026: « je n'ai pas fait ma séance du jour mais une vma club je fais quoi il se passe quoi ? C'est
recalculé automatiquement ? », then, on the card that asked: « simplifie encore ou alors ne fait pas en préventif pas
d'actions utilisateurs quand tu vois que ce n'est pas la séance attendu car tu ne peux pas la rapprocher alors tu met
à jour ». So nothing is asked: each reading of the plan (`coaching.sync`: the page, the follower's page, the daily
round) reads such a run by itself and updates the plan, then the athlete's page says what it did.
- which runs: within DEBRIEF_DAYS, from the plan's first day — a run linked to the day's session that does not look
  like it (`coaching.misfit`), a run linked to an easy session whose duration fits but that reads as a hard session,
  a run on a day whose session was ticked « Pas faite » by hand, a run on a day with no session (« hors plan »);
  never the race, never a run already read or said « Ce n’est pas cette séance »;
- the reading (`reading`): a hard session when its laps show repeated fast efforts, when its average heart rate
  reaches the plan's « soutenu » zone, or when, on flat ground, its average speed reaches the plan's threshold pace;
  else an easy outing (no data: the easy side, nothing rebuilt);
- hard: the run becomes the day's hard session (`coaching_plan.declared_structure`) and one of the week's quality
  sessions — the fixed session's rules for that week only (`coaching_plan.after_hard`): the changes are written once,
  on the sessions the plan may change, each marked with why (`coaching.marked`: no later reading rewrites them) —
  unless that weekday shows a hard-session habit (`habit`): then the plan keeps a hard session there every week, by
  itself, as « Ma séance fixe » does (owner, 26 Nov 2026: « On ne sait pas si c'est une sortie club du coup je ne
  comprends pas la question … on ne peut pas faire mieux ou autrement ? »: the question that asked is gone), and one
  quiet link, « Pas tous les mercredis », takes it back for good;
- easy: nothing is rebuilt; a quality session planned that day, not done, goes to the first day the move rules allow
  (`coaching_plan.move_day`), or stays not done when none does; a run with no session that day stays « hors plan »
  (its ways: held onto a session, « C’est ma séance de … »), its reading kept on a note (a cancelled row, `note`);
- the reading is kept on the session that holds the run (`structure["answer"]`, with the state of every session it
  changed before the change): a later reading never applies it again; the one correction link (« C’était une sortie
  tranquille » / « C’était une séance dure ») puts everything back, then applies the other reading.
The card is the athlete's: the person who follows the plan sees only the sessions and their lines. No AI.
"""
import statistics
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.coaching import CoachingPlan, CoachingSession
from app.models.user import User
from app.services import coaching as co
from app.services import coaching_pace as pace
from app.services import coaching_plan as cp
from app.services import coaching_start as cst
from app.services import sante_training as st

HARD, EASY = "dure", "facile"  # the readings
MISFIT, MISSED, FREE, BRISK = "autrement", "pas_faite", "hors_plan", "soutenue"  # the cases
# a postponement's or a habit's state (NONE: a postponement with no day; a habit: YES set by itself, NO taken back by
# « Pas tous les mercredis »; "ask", left by the question of before 26 Nov 2026, reads as nothing)
YES, NO, NONE = "oui", "non", "aucun"
WRITTEN, AGAIN = 1, 2  # what `settle` did: written; written and a fixed session set (the runs left read once more)
EASY_KINDS = (cp.FOOTING, cp.RECUP)  # the planned sessions a run may outdo in intensity at the same duration


@dataclass
class Card:
    """One run to read, or read and still said (`answer`): its case, the session linked to it or ticked « Pas faite »
    that day (`row`), and the session that holds the reading (`holder`)."""
    run: st.Session
    case: str
    row: CoachingSession | None = None
    holder: CoachingSession | None = None
    answer: dict | None = None


def answer_of(row: CoachingSession) -> dict | None:
    value = (row.structure or {}).get("answer")
    return value if isinstance(value, dict) else None


def _answered(rows: list) -> set:
    out: set = set()
    for r in rows:
        a = answer_of(r)
        if a is not None:
            out.add(a.get("run"))
        out |= co.not_runs(r)
    return out


def _since(state: co.State) -> date:
    return max(state.clock.today - timedelta(days=co.DEBRIEF_DAYS), min(r.day for r in state.rows))


def candidates(state: co.State) -> list[Card]:
    """The runs a reading of the plan has to read, the most recent first."""
    today = state.clock.today
    if not state.rows:
        return []
    live = [r for r in state.rows if r.status != co.CANCELLED]
    since = _since(state)
    answered = _answered(state.rows)
    by_run = {r.activity_id: r for r in live if r.activity_id is not None}
    race_days = {r.day for r in live if r.kind == cp.RACE}
    out = []
    for run in state.runs:
        if not since <= run.day <= today or run.id in answered or run.day in race_days:
            continue
        row = by_run.get(run.id)
        if row is not None:
            if co.misfit(row, run):
                out.append(Card(run, MISFIT, row))
            elif row.kind in EASY_KINDS and not cp.is_fixed(row.structure):
                out.append(Card(run, BRISK, row))  # read: only a hard reading changes anything
            continue
        day = [r for r in live if r.day == run.day]
        missed = next((r for r in day if r.status == co.MISSED and r.manual), None)
        if missed is not None:
            out.append(Card(run, MISSED, missed))
        elif not day:  # a day with a session done (a second run) or still to come is not read
            out.append(Card(run, FREE))
    return sorted(out, key=lambda c: (c.run.day, c.run.start), reverse=True)


def worth_saying(answer: dict) -> bool:
    """A reading with something to say: a hard session (it counts for the week), a session changed or postponed, a
    postponement with no day, a fixed session set by the habit, a correction — or a run left outside the plan (the
    notice is where its reading can be corrected: it has no ressenti to ask)."""
    return bool(answer.get("what") == HARD or said_changes(answer) or (answer.get("report") or {}).get("state")
                or (answer.get("recur") or {}).get("state") == YES or answer.get("corrected")
                or answer.get("case") in (FREE, MISSED))


def is_note(row: CoachingSession) -> bool:
    """The note that keeps the reading of a run left outside the plan: a cancelled row, no run linked."""
    a = answer_of(row)
    return a is not None and row.status == co.CANCELLED and row.activity_id is None and a.get("case") == FREE


def notices(state: co.State) -> list[Card]:
    """The runs read whose notice the athlete's page shows, the most recent first: within DEBRIEF_DAYS, until
    « Fermer », while it has something to say or the run still waits for its ressenti."""
    if not state.rows:
        return []
    runs = {run.id: run for run in state.runs}
    since = state.clock.today - timedelta(days=co.DEBRIEF_DAYS)
    out = []
    for r in state.rows:
        a = answer_of(r)
        run = runs.get((a or {}).get("run"))
        if a is None or run is None or not a.get("open") or run.day < since:
            continue
        if worth_saying(a) or (r.status == co.DONE and r.ressenti is None):
            row = next((x for x in state.rows if x.id == a.get("row")), None) if a.get("row") else None
            out.append(Card(run, a.get("case") or FREE, row, r, a))
    return sorted(out, key=lambda c: (c.run.day, c.run.start), reverse=True)


def holder_of(state: co.State, run_id: int) -> CoachingSession | None:
    return next((r for r in state.rows if (answer_of(r) or {}).get("run") == run_id), None)


# ── the reading: the plan's own definitions of intensity ────────────────────
FAST_LAP = 1.2  # choix pratique: a lap 20 % faster than the run's typical lap (the median of its laps' speeds)…
FAST_LAPS = 3  # …three times or more, each time after slower laps: repeated fast efforts
LAP_MIN = (100, 20)  # choix pratique: laps shorter than 100 m or 20 s (a stop, a lap pressed twice) are left out
HR_LEVEL = 3  # the plan's « soutenu » efforts: their heart-rate zone (coaching_plan.HR_ZONES[3]: zone 4)
# choix pratique: the pace says something only on flat ground — at most 10 m of climb per km (1 % on average); a
# hillier run is read by its laps and its heart rate only (the plan gives no pace on a climb either)
FLAT_CLIMB = 10.0


def looks_like_intervals(laps) -> bool:
    """Several laps clearly faster than the run's typical lap, separated by slower ones (choix pratique, FAST_LAP,
    FAST_LAPS): conservative — auto laps of a kilometre rarely show it, laps pressed at each effort do."""
    speeds = []
    for lap in laps if isinstance(laps, list) else []:
        if not isinstance(lap, dict):
            continue
        try:
            metres = float(lap.get("distance") or 0)
            seconds = float(lap.get("moving_time") or lap.get("elapsed_time") or 0)
        except (TypeError, ValueError):
            continue
        if metres >= LAP_MIN[0] and seconds >= LAP_MIN[1]:
            speeds.append(metres / seconds)
    if len(speeds) < 2 * FAST_LAPS:
        return False
    ordered = sorted(speeds)
    mid = len(ordered) // 2
    typical = ordered[mid] if len(ordered) % 2 else (ordered[mid - 1] + ordered[mid]) / 2
    fast = [v >= FAST_LAP * typical for v in speeds]
    blocks = sum(1 for i, f in enumerate(fast) if f and (i == 0 or not fast[i - 1]))
    return blocks >= FAST_LAPS and not all(fast)


def hr_floor(hr_max: float | None) -> int | None:
    """The lowest heart rate of the zone the plan gives its « soutenu » efforts (the app's own zones,
    heart_rate_zones, from a known maximum), else None."""
    from app.services.heart_rate_zones import running_zones

    if not hr_max:
        return None
    zones = running_zones(hr_max)
    if not zones.get("ready"):
        return None
    return int(zones["rows"][cp.HR_ZONES[HR_LEVEL][0] - 1]["low"])


def reading(*, laps=None, hr: float | None = None, hr_max: float | None = None, speed: float | None = None,
            km: float = 0.0, dplus: float | None = None, cs: float | None = None) -> tuple[str, str | None]:
    """A run read as « une séance dure » or « une sortie tranquille », and the criterion that decided (« tours »,
    « cardio », « allure », None for an easy outing):
    - its laps show repeated fast efforts (`looks_like_intervals`);
    - its average heart rate reaches the « soutenu » zone (`hr_floor`, from the athlete's known maximum);
    - on flat ground (FLAT_CLIMB), its average speed reaches the threshold pace (the lower bound of
      coaching_pace.THRESHOLD × the athlete's critical speed).
    Without laps, heart rate or a usable pace: an easy outing — nothing is rebuilt, the conservative side."""
    if looks_like_intervals(laps):
        return HARD, "tours"
    floor = hr_floor(hr_max)
    if hr and floor and hr >= floor:
        return HARD, "cardio"
    if speed and cs and km and km > 0 and (dplus or 0) / km <= FLAT_CLIMB and speed >= pace.THRESHOLD[0] * cs:
        return HARD, "allure"
    return EASY, None


def _speed(run) -> float | None:
    if run.speed:
        return float(run.speed)
    return run.km * 1000 / (run.minutes * 60) if run.km and run.minutes else None


async def _hr_max(db: AsyncSession, user: User, c: co.Clock) -> float | None:
    """The athlete's known maximum, the reference the page's heart-rate ranges read (a confirmed value, else one the
    runs corroborate)."""
    try:
        from app.services import heart_rate_reference

        return (await heart_rate_reference.for_athlete(db, user.id, c.today, zone=c.zone))["reference"]
    except Exception:
        co.logger.exception("Coaching: the heart-rate reference failed for user %d", user.id)
        return user.max_heart_rate


async def _cs(db: AsyncSession, user: User, c: co.Clock) -> float | None:
    """The athlete's critical speed, the one the page's paces read; None when the runs do not give one."""
    try:
        critical = await pace.for_athlete(db, user.id, c.today)
    except Exception:
        co.logger.exception("Coaching: the critical speed failed for user %d", user.id)
        return None
    return critical.cs if critical is not None else None


async def facts(db: AsyncSession, state: co.State, user: User, runs: list) -> dict[int, dict]:
    """What `reading` reads of each run: its laps (one query), its average heart rate with the athlete's maximum,
    its speed, distance and climb with the critical speed — the maximum and the critical speed read only when a run
    still needs them."""
    from app.models.activity import Activity

    if not runs:
        return {}
    ids = [run.id for run in runs]
    laps = {i: v for i, v in (await db.execute(select(Activity.id, Activity.laps).where(Activity.id.in_(ids)))).all()}
    out = {run.id: {"laps": laps.get(run.id), "hr": run.hr, "speed": _speed(run), "km": run.km, "dplus": run.dplus}
           for run in runs}
    left = [i for i, f in out.items() if not looks_like_intervals(f["laps"])]
    if any(out[i]["hr"] for i in left):
        hr_max = await _hr_max(db, user, state.clock)
        for i in out:
            out[i]["hr_max"] = hr_max
        left = [i for i in left if reading(**out[i])[0] == EASY]
    if any(out[i]["speed"] for i in left):
        cs = await _cs(db, user, state.clock)
        for i in out:
            out[i]["cs"] = cs
    return out


async def settle(db: AsyncSession, state: co.State, user: User) -> int:
    """Every run of `candidates` read and applied, the oldest first (each week's rules see what came before): a hard
    session as « Une séance dure » did, an easy one as « Une sortie tranquille » did, the postponement included. Called
    by every reading of the plan (coaching.sync); idempotent — a run read is never read again. 0: nothing written;
    WRITTEN; AGAIN: a fixed session was just set by the habit — the reading stops there, the plan is written again
    around it (coaching.sync, as coaching.set_fixed does) and the runs left are read after."""
    linked = {r.activity_id for r in state.rows if r.activity_id is not None and r.status != co.CANCELLED}
    stale = [r for r in state.rows if is_note(r) and (answer_of(r) or {}).get("run") in linked]
    for r in stale:  # a run outside the plan since linked to a session (held onto it, « C’est ma séance de … »)
        state.rows.remove(r)
        await db.delete(r)
    cards = candidates(state)
    if not cards:
        if stale:
            await db.flush()
        return WRITTEN if stale else 0
    known = await facts(db, state, user, [c.run for c in cards])
    seen: set[int] = set()
    while True:
        left = [c for c in candidates(state) if c.run.id not in seen and c.run.id in known]
        if not left:
            break
        card = min(left, key=lambda c: (c.run.day, c.run.start))
        seen.add(card.run.id)
        value, _why = reading(**known[card.run.id])
        if await apply(db, state, user, card, value):
            await db.flush()
            return AGAIN
    await db.flush()
    return WRITTEN


# ── the state a change starts from, and back ────────────────────────────────
FIELDS = ("kind", "minutes", "structure", "status", "manual", "activity_id", "done_minutes", "ressenti", "pinned",
          "origin", "slot_min", "slot_chosen", "slot_flag")


def snapshot(row: CoachingSession) -> dict:
    return {"day": row.day.isoformat(), **{k: getattr(row, k) for k in FIELDS}}


def _now(row: CoachingSession) -> dict:
    return {"day": row.day.isoformat(), "kind": row.kind, "minutes": row.minutes, "status": row.status}


def restore(row: CoachingSession, was: dict, now: datetime) -> None:
    row.day = date.fromisoformat(was["day"])
    for k in FIELDS:
        if k in was:
            setattr(row, k, was[k])
    co.touch(row, now)


def _set_answer(row: CoachingSession, answer: dict | None) -> None:
    structure = {k: v for k, v in (row.structure or {}).items() if k != "answer"}
    if answer is not None:
        structure["answer"] = answer
    row.structure = structure


def _why(structure: dict | None, why: dict) -> dict:
    return {**{k: v for k, v in (structure or {}).items() if k != "answer"}, "why": why}


def _new_row(db: AsyncSession, plan: CoachingPlan, rows: list, day: date, kind: str, minutes: int, structure: dict,
             now: datetime, **more) -> CoachingSession:
    fields = {"status": co.PLANNED, "origin": "auto", "manual": False, "pinned": False, **more}
    row = CoachingSession(plan_id=plan.id, user_id=plan.user_id, day=day, auto_day=None, kind=kind, minutes=minutes,
                          structure=structure, seq=0, updated_at=now, **fields)
    db.add(row)
    rows.append(row)
    return row


def _run_row(db: AsyncSession, plan: CoachingPlan, rows: list, run, kind: str, structure: dict,
             now: datetime) -> CoachingSession:
    """The session a run outside the plan becomes once read: done, linked, the athlete's. It holds the day the
    generator would fill only when no session holds it already (a session made for today would sit beside it)."""
    minutes = int(round(run.minutes))
    held = any(r.auto_day == run.day for r in rows)
    row = _new_row(db, plan, rows, run.day, kind, minutes, structure, now, status=co.DONE, activity_id=run.id,
                   done_minutes=minutes, origin="athlete", pinned=True)
    row.auto_day = None if held else run.day
    return row


def _note_row(db: AsyncSession, plan: CoachingPlan, rows: list, run, now: datetime) -> CoachingSession:
    """The note a run left outside the plan keeps its reading on: a cancelled row (no calendar, no board, no feed, no
    regeneration reads it), on the run's day."""
    return _new_row(db, plan, rows, run.day, cp.FOOTING, max(1, int(round(run.minutes))), {}, now,
                    status=co.CANCELLED, origin="athlete", pinned=True)


def _held(state: co.State, holder: CoachingSession) -> list[cp.Held]:
    today = state.clock.today
    return [cp.Held(r.id, r.day, r.kind, int(r.done_minutes or r.minutes) if r.status == co.DONE else r.minutes,
                    r.structure, r.status, co.replaceable(r, today) and not cp.is_fixed(r.structure))
            for r in state.rows if r is not holder and r.status != co.CANCELLED and r.id is not None]


def week_count(state: co.State, day: date) -> int | None:
    """The quality sessions the rules give `day`'s week (coaching_plan.week_quality), None for a week the plan no
    longer writes (behind)."""
    week = next((w for w in state.weeks if w.monday == cp.monday_of(day)), None)
    if week is None:
        return None
    info = {"target": week.target, "light": week.light, "easy": week.reason in ("ressenti", "manquees", cp.FORM)}
    return cp.week_quality(week.monday, state.inputs, info, week.phase, week.index)


async def _apply(db: AsyncSession, state: co.State, changes: list[cp.Change], decl: date) -> list[dict]:
    """The changes written on the sessions, each marked with why; returns what the correction reads: every session
    touched, its state before (`was`) and after (`now`), a session made here (`new`), and what the card says."""
    now, rows = state.clock.now, state.rows
    by_id = {r.id: r for r in rows}
    records: dict[int, dict] = {}

    def keep(row: CoachingSession) -> dict:
        return records.setdefault(row.id, {"id": row.id, "was": snapshot(row)})

    said = []
    for c in changes:
        row = by_id[c.id]
        if c.what in ("eased", "recup", "reps"):
            keep(row)
            if c.what != "reps":
                row.kind = cp.RECUP if c.what == "recup" else cp.FOOTING
            row.structure = _why(c.structure, {"what": c.what, "decl": decl.isoformat()})
            co.touch(row, now)
            said.append({"what": c.what, "day": row.day.isoformat(), "minutes": row.minutes})
        elif c.what == "moved" and c.to is not None:
            to = c.to
            keep(row)
            content = (row.kind, row.minutes, {k: v for k, v in (row.structure or {}).items() if k != "why"})
            moved = {"what": "moved", "decl": decl.isoformat(), "from": row.day.isoformat(), "to": to.isoformat()}
            if c.swap is not None:  # the easy session of that day takes its place, as a low morning's move does
                there = by_id[c.swap]
                keep(there)
                row.kind, row.minutes = cp.RECUP, there.minutes
                row.structure = _why(cp.steady(there.minutes, 1), {"what": "swapped", "from": to.isoformat()})
                there.kind, there.minutes, there.structure = content[0], content[1], _why(content[2], moved)
                for r in (row, there):
                    r.slot_min, r.slot_chosen, r.slot_flag = None, False, None
                    co.touch(r, now)
            else:  # an empty day: the session goes there, its own day stays free (kept, cancelled, until corrected)
                new = _new_row(db, state.plan, rows, to, content[0], content[1], _why(content[2], moved), now)
                await db.flush()
                records[new.id] = {"id": new.id, "new": True}
                row.status = co.CANCELLED
                row.structure = _why(row.structure, {"what": "gone", "to": to.isoformat()})
                co.touch(row, now)
            said.append({"what": "moved", "day": c.day.isoformat(), "to": to.isoformat()})
    await db.flush()
    out = []
    for rec in records.values():
        out.append({**rec, "now": _now(next(r for r in rows if r.id == rec["id"]))})
    return out + ([{"said": said}] if said else [])


async def _revert(db: AsyncSession, state: co.State, records: list[dict]) -> None:
    """The sessions a change touched, back as they were — those still as it left them (a session edited, moved or
    done since is the athlete's now, and stays)."""
    by_id = {r.id: r for r in state.rows}
    for rec in reversed(records):
        row = by_id.get(rec.get("id"))
        if row is None or "now" not in rec or _now(row) != rec["now"]:
            continue
        if rec.get("new"):
            state.rows.remove(row)
            await db.delete(row)
        else:
            restore(row, rec["was"], state.clock.now)
    await db.flush()


# ── the readings applied ────────────────────────────────────────────────────

def report_day(state: co.State, row: CoachingSession, origin: date) -> date | None:
    """Where a quality session planned on `origin` and not done may go: the first day from tomorrow (from today for
    a day behind) of that week that the move rules allow (coaching_plan.move_day: an available day, empty or holding
    one easy session the plan may change, never beside a hard day; Stanley 2013)."""
    today = state.clock.today
    slots = [cp.Slot(r.day, r.kind, co.replaceable(r, today) and not co.marked(r)) for r in state.rows
             if r is not row and r.status in (co.PLANNED, co.DONE) and r.day > origin]
    return cp.move_day(origin, state.plan.weekdays, slots, row.kind, state.goal.race_day, first=today)


def _quality(row: CoachingSession | None) -> bool:
    return (row is not None and row.kind in (cp.QUALITY, cp.HILLS) and not cp.is_fixed(row.structure)
            and not cp.is_declared(row.structure))


async def apply(db: AsyncSession, state: co.State, user: User, card: Card, value: str, *,
                corrected: bool = False) -> bool:
    """A run read (`value`: HARD or EASY) and the plan updated as that reading says. An easy run on an easy
    session whose duration fits (BRISK) changes nothing: it is kept as read, the day's debrief asks its ressenti.
    True when the habit just set a fixed session (the plan is then to be written again around it)."""
    fixed = False
    if value == HARD:
        fixed = await _hard(db, state, user, card, corrected)
    elif card.case == BRISK and not corrected and card.row is not None:
        _set_answer(card.row, {"run": card.run.id, "what": EASY, "case": BRISK, "row": card.row.id, "open": False,
                               "was": None, "created": False, "changes": []})
    else:
        await _easy(db, state, card, corrected)
    await db.flush()
    return fixed


# ── the habit: a hard session the same weekday most weeks (docs/coaching.md › « La séance fixe ») ──
# choix pratique, no study: among the FIXED_WEEKS (8) complete weeks before the run's week, a run on that weekday in
# at least RECUR_FROM (5) of them — most weeks — AND at least HABIT_HARD of those weeks with a run read as hard by
# `reading` itself (laps, heart rate, pace). Fewer: no habit (a weekly easy jog on Wednesdays is not a hard session).
HABIT_HARD = 3


async def habit(db: AsyncSession, state: co.State, user: User, day: date) -> cp.Fixed | None:
    """The fixed session the plan sets by itself after a run read hard on `day`, or None: never with a fixed session
    already, never on a declined weekday, never where « Ma séance fixe » would refuse it (`clean_fixed`: the long
    run's day, a seventh day). Its duration: the median of those hard runs, rounded as the option rounds (5 min, 30
    min to 3 h); its name: the option's default (FIXED_NAME, « Séance fixe »). The past runs are read only here: one
    query for their laps (`facts`), no stream."""
    plan = state.plan
    if co.fixed_of(plan) is not None or day.weekday() in co.declined_days(plan) \
            or not cst.weekly_habit(state.runs, day):
        return None
    past = cst.weekday_runs(state.runs, day)
    known = await facts(db, state, user, past)
    hard = [run for run in past if reading(**known[run.id])[0] == HARD]
    if len({run.day for run in hard}) < HABIT_HARD:
        return None
    try:
        return co.clean_fixed(day.weekday(), statistics.median(run.minutes for run in hard), None, plan.weekdays,
                              plan.long_day)
    except co.Invalid:
        return None


async def _hard(db: AsyncSession, state: co.State, user: User, card: Card, corrected: bool) -> bool:
    run, plan, now = card.run, state.plan, state.clock.now
    minutes = int(round(run.minutes))
    structure = cp.declared_structure(minutes)
    if card.row is not None:  # the day's session — linked, or ticked « Pas faite »: it was this run
        holder, was, created = card.row, snapshot(card.row), False
        holder.kind, holder.minutes, holder.structure = cp.QUALITY, minutes, structure
        holder.status, holder.activity_id, holder.done_minutes, holder.manual = co.DONE, run.id, minutes, False
        holder.pinned, holder.origin = True, "athlete"
        co.touch(holder, now)
    else:
        holder, was, created = _run_row(db, plan, state.rows, run, cp.QUALITY, structure, now), None, True
    await db.flush()
    fixed = await habit(db, state, user, run.day)
    recur, records = None, []
    if fixed is not None:  # the fixed session's rules take this week too: no one-off change is written
        recur = {"day": fixed.day, "minutes": fixed.minutes, "state": YES, "was": plan.fixed}
        plan.fixed = co.fixed_state(fixed, plan)  # as coaching.set_fixed: the plan is then read again (`settle`)
    else:
        changes = cp.after_hard(run.day, minutes, _held(state, holder), week_count(state, run.day),
                                state.clock.today, plan.weekdays, state.goal.race_day)
        records = await _apply(db, state, changes, run.day)
    _set_answer(holder, {"run": run.id, "what": HARD, "case": card.case, "row": card.row.id if card.row else None,
                         "open": True, "corrected": corrected, "was": was, "created": created, "changes": records,
                         "recur": recur})
    return fixed is not None


async def _easy(db: AsyncSession, state: co.State, card: Card, corrected: bool) -> None:
    run, plan, now = card.run, state.plan, state.clock.now
    planned = card.row
    if planned is not None:  # linked: the run stays the day's session (the day's run is the day's load); ticked
        holder, was, created = planned, snapshot(planned), False  # « Pas faite »: it keeps the reading
    else:  # no session that day: the run stays « hors plan », its reading on a note
        holder, was, created = _note_row(db, plan, state.rows, run, now), None, True
    await db.flush()
    answer = {"run": run.id, "what": EASY, "case": card.case, "row": planned.id if planned else None, "open": True,
              "corrected": corrected, "was": was, "created": created, "changes": [], "report": None}
    if card.case in (MISFIT, MISSED) and _quality(planned) and planned is not None:
        to = report_day(state, planned, run.day)
        answer["report"] = {"id": planned.id, "state": NONE}
        if to is not None:
            answer["report"] = {"id": planned.id, "to": to.isoformat(), "state": YES}
            answer["changes"] = await _postpone(db, state, planned, to)
    _set_answer(holder, answer)


async def _postpone(db: AsyncSession, state: co.State, row: CoachingSession, to: date) -> list[dict]:
    """The quality session not done goes to `to` (an easy session there gives it its place); the run stays the day's
    easy session. Returns the records the correction reads."""
    now, by_day = state.clock.now, [r for r in state.rows if r.day == to and r.status == co.PLANNED]
    content = (row.kind, row.minutes, {k: v for k, v in (row.structure or {}).items()
                                       if k not in ("answer", "why", "not")})
    why = {"what": "postponed", "from": row.day.isoformat(), "to": to.isoformat()}
    records: dict[int, dict] = {}
    if row.status == co.DONE:  # the run kept the session: it becomes the day's easy run, the session goes
        records[row.id] = {"id": row.id, "was": snapshot(row)}
        minutes = int(round(row.done_minutes or row.minutes))
        row.kind, row.minutes, row.structure = cp.FOOTING, minutes, cp.steady(minutes, 2)
    else:  # ticked « Pas faite »: it leaves its day (kept, cancelled, until corrected)
        records[row.id] = {"id": row.id, "was": snapshot(row)}
        row.status, row.structure = co.CANCELLED, _why(row.structure, {"what": "gone", "to": to.isoformat()})
    co.touch(row, now)
    if by_day:
        there = by_day[0]
        records[there.id] = {"id": there.id, "was": snapshot(there)}
        there.kind, there.minutes, there.structure = content[0], content[1], _why(content[2], why)
        there.slot_min, there.slot_chosen, there.slot_flag = None, False, None
        co.touch(there, now)
    else:
        new = _new_row(db, state.plan, state.rows, to, content[0], content[1], _why(content[2], why), now)
        await db.flush()
        records[new.id] = {"id": new.id, "new": True}
    await db.flush()
    out = [{**rec, "now": _now(next(r for r in state.rows if r.id == rec["id"]))} for rec in records.values()]
    return out + [{"said": [{"what": "postponed", "day": why["from"], "to": why["to"]}]}]


async def _undo(db: AsyncSession, state: co.State, holder: CoachingSession, a: dict) -> None:
    """A reading taken back: the sessions it changed, the session that holds the run (its kind, its link) and a
    fixed session the habit set with it, back as they were."""
    await _revert(db, state, [r for r in a.get("changes") or [] if "id" in r])
    _unset(state.plan, a)
    if a.get("created"):
        state.rows.remove(holder)
        await db.delete(holder)
    elif a.get("was"):
        restore(holder, a["was"], state.clock.now)
    else:
        _set_answer(holder, None)
    await db.flush()


def _read(state: co.State, run_id: int) -> tuple[CoachingSession, dict, Card]:
    holder = holder_of(state, run_id)
    a = answer_of(holder) if holder is not None else None
    run = next((s for s in state.runs if s.id == run_id), None)
    if holder is None or a is None or run is None:
        raise co.Invalid("Cette sortie n’a pas été lue.")
    row = next((r for r in state.rows if r.id == a.get("row")), None) if a.get("row") else None
    return holder, a, Card(run, a.get("case") or FREE, row, holder, a)


async def correct(db: AsyncSession, state: co.State, user: User, run_id: int) -> None:
    """« C’était une sortie tranquille » / « C’était une séance dure »: the reading flipped — what the first one
    changed put back, then the other one applied; the ressenti already said stays."""
    holder, a, card = _read(state, run_id)
    feel = holder.ressenti
    await _undo(db, state, holder, a)
    if card.row is not None:  # the session as the run found it (restored above when it held the reading)
        card = Card(card.run, card.case, card.row)
    else:
        card = Card(card.run, card.case)
    await apply(db, state, user, card, EASY if a.get("what") == HARD else HARD, corrected=True)
    now_holder = holder_of(state, run_id)
    if feel is not None and now_holder is not None and now_holder.status == co.DONE:
        now_holder.ressenti = feel
    await db.flush()


async def not_this(db: AsyncSession, state: co.State, run_id: int) -> None:
    """« Ce n’est pas cette séance »: the reading taken back; the session « Pas faite », the run outside the plan,
    never read again."""
    holder, a, card = _read(state, run_id)
    if card.row is None or card.case not in (MISFIT, BRISK):
        raise co.Invalid("Cette sortie n’est liée à aucune séance.")
    await _undo(db, state, holder, a)
    co.set_missed(card.row, state.clock.today)
    await db.flush()


async def attach(db: AsyncSession, state: co.State, run_id: int, session_id: int) -> None:
    """« C’est ma séance de mardi » (a run on a day with no session): the reading taken back, that session moved to
    the run's day — the next reading links them."""
    holder, a, card = _read(state, run_id)
    row = next((r for r in state.rows if r.id == session_id), None)
    if card.case != FREE or row is None:
        raise co.Invalid("Cette séance ne se déplace pas.")
    await _undo(db, state, holder, a)
    co.move(state.rows, row, card.run.day, state.clock.today, state.clock.now, "athlete", free={card.run.day})
    await db.flush()


def habit_kept(plan: CoachingPlan, a: dict) -> bool:
    """Whether the fixed session the habit set with this reading is still the plan's (not removed nor moved to
    another day since)."""
    offer, kept = a.get("recur") or {}, co.fixed_of(plan)
    return offer.get("state") == YES and kept is not None and kept.day == offer.get("day")


def _unset(plan: CoachingPlan, a: dict) -> None:
    """The fixed session the habit set with this reading removed, when it is still the plan's (the declined
    weekdays stay)."""
    if habit_kept(plan, a):
        plan.fixed = co.fixed_state(None, plan)


async def not_weekly(db: AsyncSession, state: co.State, user: User, run_id: int) -> None:
    """« Pas tous les mercredis »: the fixed session the habit set goes, that weekday is never set again by itself
    (coaching.decline), and the run's week is put as the one-off reading had it — the plan written again without
    the fixed session (as coaching.set_fixed does), then the fixed session's rules for that week only (`after_hard`)."""
    holder, a, card = _read(state, run_id)
    if not habit_kept(state.plan, a):
        raise co.Invalid("Cette séance n’est plus fixée.")
    plan, offer = state.plan, a["recur"]
    _unset(plan, a)
    co.decline(plan, int(offer["day"]))
    state = await co.sync(db, plan, user, settle=False)
    holder = holder_of(state, run_id) or holder
    run = card.run
    changes = cp.after_hard(run.day, int(round(run.minutes)), _held(state, holder), week_count(state, run.day),
                            state.clock.today, plan.weekdays, state.goal.race_day)
    records = await _apply(db, state, changes, run.day)
    _set_answer(holder, {**(answer_of(holder) or a), "changes": records, "recur": {**offer, "state": NO}})
    await db.flush()


async def drop_fixed(db: AsyncSession, state: co.State, user: User) -> None:
    """« Ma séance fixe » › « Retirer »: no fixed session any more, and its weekday is never set again by itself
    (otherwise a habit read there would bring it back the next week). One the habit set goes as « Pas tous les
    mercredis » does (its week put as the one-off reading had it); any other as coaching.set_fixed(None)."""
    kept = co.fixed_of(state.plan)
    by_habit = [r for r in state.rows if habit_kept(state.plan, answer_of(r) or {})]
    if kept is not None and by_habit:
        run_id = int((answer_of(max(by_habit, key=lambda r: r.day)) or {}).get("run") or 0)
        try:
            await not_weekly(db, state, user, run_id)
            return
        except co.Invalid:  # its run no longer read (deleted): removed as any other
            pass
    co.left_by_hand(state.plan, None)
    await co.set_fixed(db, state.plan, user, None)


def close(state: co.State, run_id: int) -> None:
    """« Fermer »: the notice goes (the plan stays as it is)."""
    holder = holder_of(state, run_id)
    a = answer_of(holder) if holder is not None else None
    if holder is not None and a is not None:
        _set_answer(holder, {**a, "open": False})


def said_changes(answer: dict) -> list[dict]:
    """What the card says changed, in order."""
    return [x for rec in answer.get("changes") or [] for x in rec.get("said", [])]
