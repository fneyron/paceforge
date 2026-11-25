"""Coaching: a recorded run that is not plainly the day's session (docs/coaching.md › « Les séances et les activités »).

The owner, 25 Nov 2026: « je n'ai pas fait ma séance du jour mais une vma club je fais quoi il se passe quoi ? C'est
recalculé automatiquement ? ». A card at the top of the athlete's page says what was understood, asks one thing, and
says what the plan changed:
- which runs: within DEBRIEF_DAYS, from the plan's first day — a run linked to the day's session that does not look
  like it (`coaching.misfit`), a run on a day whose session was ticked « Pas faite » by hand, a run on a day with no
  session (« hors plan »); never the race, never a run already answered or said « Ce n’est pas cette séance »;
- « Une séance dure »: the run becomes the day's hard session (`coaching_plan.declared_structure`) and one of the
  week's quality sessions — the fixed session's rules for that week only (`coaching_plan.after_hard`): the changes
  are written once, on the sessions the plan may change, each marked with why (`coaching.marked`: no later reading
  rewrites them), and offered once « Tous les mercredis ? » when the athlete runs that weekday most weeks;
- « Une sortie tranquille »: nothing is rebuilt; a quality session planned that day, not done, is offered once to
  move to the first day the move rules allow (`coaching_plan.move_day`);
- the answer is kept on the session that holds the run (`structure["answer"]`, with the state of every session it
  changed before the change): « Annuler » puts everything back; a reading of the plan never applies it again.
The card is the athlete's: the person who follows the plan sees only the sessions and their lines. No AI.
"""
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.coaching import CoachingPlan, CoachingSession
from app.models.user import User
from app.services import coaching as co
from app.services import coaching_plan as cp
from app.services import coaching_start as cst
from app.services import sante_training as st

HARD, EASY, NOT = "dure", "facile", "pas_celle"  # the answers
MISFIT, MISSED, FREE = "autrement", "pas_faite", "hors_plan"  # the cases
ASK, YES, NO = "ask", "oui", "non"  # an offer's state


@dataclass
class Card:
    """One run to ask about, or answered and still shown (`answer`): its case, the session linked to it or ticked
    « Pas faite » that day (`row`), and the session that holds the answer (`holder`)."""
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


def pending(state: co.State) -> list[Card]:
    """The runs that wait for « C’était quoi ? », the most recent first."""
    today = state.clock.today
    live = [r for r in state.rows if r.status != co.CANCELLED]
    if not state.rows:
        return []
    since = max(today - timedelta(days=co.DEBRIEF_DAYS), min(r.day for r in state.rows))
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
            continue
        day = [r for r in live if r.day == run.day]
        missed = next((r for r in day if r.status == co.MISSED and r.manual), None)
        if missed is not None:
            out.append(Card(run, MISSED, missed))
        elif not day:  # a day with a session done (a second run) or still to come is not asked about
            out.append(Card(run, FREE))
    return sorted(out, key=lambda c: (c.run.day, c.run.start), reverse=True)


def shown(state: co.State) -> list[Card]:
    """The cards answered and still open (until « Fermer » or the next visit), the most recent first."""
    runs = {run.id: run for run in state.runs}
    out = []
    for r in state.rows:
        a = answer_of(r)
        if a is not None and a.get("open") and a.get("run") in runs:
            out.append(Card(runs[a["run"]], a.get("case") or FREE, None, r, a))
    return sorted(out, key=lambda c: (c.run.day, c.run.start), reverse=True)


def holder_of(state: co.State, run_id: int) -> CoachingSession | None:
    return next((r for r in state.rows if (answer_of(r) or {}).get("run") == run_id), None)


def close_all(rows: list, keep: int | None = None) -> bool:
    """The next visit: the cards answered are closed (but the one a page without JavaScript was sent back to show)."""
    changed = False
    for r in rows:
        a = answer_of(r)
        if a is not None and a.get("open") and a.get("run") != keep:
            _set_answer(r, {**a, "open": False})
            changed = True
    return changed


# ── the laps' hint ──────────────────────────────────────────────────────────
FAST_LAP = 1.2  # choix pratique: a lap 20 % faster than the run's typical lap (the median of its laps' speeds)…
FAST_LAPS = 3  # …three times or more, each time after slower laps: « On dirait des intervalles. » — a hint, never a choice made for the athlete
LAP_MIN = (100, 20)  # choix pratique: laps shorter than 100 m or 20 s (a stop, a lap pressed twice) are left out


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


async def lap_hints(db: AsyncSession, run_ids: list[int]) -> set[int]:
    """The runs among `run_ids` whose laps look like intervals (one read)."""
    from app.models.activity import Activity

    if not run_ids:
        return set()
    rows = (await db.execute(select(Activity.id, Activity.laps).where(Activity.id.in_(run_ids)))).all()
    return {i for i, laps in rows if looks_like_intervals(laps)}


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
    """The session a run outside the plan becomes once answered: done, linked, the athlete's. It holds the day the
    generator would fill only when no session holds it already (a session made for today would sit beside it)."""
    minutes = int(round(run.minutes))
    held = any(r.auto_day == run.day for r in rows)
    row = _new_row(db, plan, rows, run.day, kind, minutes, structure, now, status=co.DONE, activity_id=run.id,
                   done_minutes=minutes, origin="athlete", pinned=True)
    row.auto_day = None if held else run.day
    return row


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
    """The changes written on the sessions, each marked with why; returns what « Annuler » reads: every session
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
            else:  # an empty day: the session goes there, its own day stays free (kept, cancelled, until « Annuler »)
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


def _said(answer: dict) -> list[dict]:
    return [x for rec in answer.get("changes") or [] for x in rec.get("said", [])]


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


# ── the answers ─────────────────────────────────────────────────────────────

def _card(state: co.State, run_id: int) -> Card:
    card = next((c for c in pending(state) if c.run.id == run_id), None)
    if card is None:
        raise co.Invalid("Cette sortie n’attend plus de réponse.")
    return card


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


async def answer(db: AsyncSession, state: co.State, user: User, run_id: int, value: str) -> None:
    """« C’était quoi ? » answered: « Une séance dure », « Une sortie tranquille », or « Ce n’est pas cette séance »."""
    card = _card(state, run_id)
    if value == HARD:
        await _hard(db, state, card)
    elif value == EASY:
        await _easy(db, state, card)
    elif value == NOT and card.row is not None:
        if card.case == MISFIT:
            co.set_missed(card.row, state.clock.today)
        else:
            card.row.structure = {**(card.row.structure or {}), "not": sorted(co.not_runs(card.row) | {run_id})}
    else:
        raise co.Invalid("Réponse inconnue.")
    await db.flush()


async def _hard(db: AsyncSession, state: co.State, card: Card) -> None:
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
    changes = cp.after_hard(run.day, minutes, _held(state, holder), week_count(state, run.day), state.clock.today,
                            plan.weekdays, state.goal.race_day)
    records = await _apply(db, state, changes, run.day)
    recur = None
    if co.fixed_of(plan) is None and run.day.weekday() not in co.declined_days(plan) \
            and cst.weekly_habit(state.runs, run.day):
        try:
            fixed = co.clean_fixed(run.day.weekday(), run.minutes, None, plan.weekdays, plan.long_day)
            recur = {"day": fixed.day, "minutes": fixed.minutes, "state": ASK}
        except co.Invalid:
            recur = None  # its long run's day, or a seventh day: not offered (« Ma séance fixe » would refuse it)
    _set_answer(holder, {"run": run.id, "what": HARD, "case": card.case, "open": True, "was": was,
                         "created": created, "changes": records, "recur": recur})


async def _easy(db: AsyncSession, state: co.State, card: Card) -> None:
    run, plan, now = card.run, state.plan, state.clock.now
    offer = None
    if card.case == MISFIT and card.row is not None:  # the run stays the day's session (the day's run is the
        holder, was, created = card.row, snapshot(card.row), False  # day's load): nothing rebuilt
    else:
        minutes = int(round(run.minutes))
        holder = _run_row(db, plan, state.rows, run, cp.FOOTING, cp.steady(minutes, 2), now)
        was, created = None, True
    await db.flush()
    planned = card.row
    if _quality(planned) and planned is not None:
        to = report_day(state, planned, run.day)
        if to is not None:
            offer = {"id": planned.id, "to": to.isoformat(), "state": ASK}
    _set_answer(holder, {"run": run.id, "what": EASY, "case": card.case, "open": True, "was": was,
                         "created": created, "changes": [], "report": offer})


async def report(db: AsyncSession, state: co.State, run_id: int, yes: bool) -> None:
    """« Reporter ta séance à vendredi ? »: once, one tap. Yes: the quality session goes to that day (an easy
    session there gives it its place) and the run stays the day's easy session."""
    holder = holder_of(state, run_id)
    a = answer_of(holder) if holder is not None else None
    offer = (a or {}).get("report")
    if holder is None or a is None or not offer or offer.get("state") != ASK:
        raise co.Invalid("Rien à reporter.")
    if not yes:
        _set_answer(holder, {**a, "report": {**offer, "state": NO}})
        return
    row = next((r for r in state.rows if r.id == offer["id"]), None)
    to = report_day(state, row, row.day) if row is not None else None
    if row is None or to is None:
        raise co.Invalid("Plus de jour libre pour cette séance cette semaine.")
    now, by_day = state.clock.now, [r for r in state.rows if r.day == to and r.status == co.PLANNED]
    content = (row.kind, row.minutes, {k: v for k, v in (row.structure or {}).items()
                                       if k not in ("answer", "why", "not")})
    why = {"what": "postponed", "from": row.day.isoformat(), "to": to.isoformat()}
    records: dict[int, dict] = {}
    if row is holder:  # the run kept the session: it becomes the day's easy run, the session goes
        records[row.id] = {"id": row.id, "was": snapshot(row)}
        minutes = int(round(row.done_minutes or row.minutes))
        row.kind, row.minutes = cp.FOOTING, minutes
        row.structure = {**cp.steady(minutes, 2), "answer": a}
    else:  # ticked « Pas faite »: it leaves its day (kept, cancelled, until « Annuler »)
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
    a = answer_of(holder) or a
    _set_answer(holder, {**a, "report": {**offer, "state": YES},
                         "changes": out + [{"said": [{"what": "postponed", "day": why["from"], "to": why["to"]}]}]})


async def recur(db: AsyncSession, state: co.State, user: User, run_id: int, yes: bool) -> None:
    """« Tu cours tous les mercredis avec ton club ? »: once. « Oui, tous les mercredis »: « Ma séance fixe » on that
    weekday at the run's duration (coaching.set_fixed and its checks: the plan written again around it, as the
    option does — this week's changes are put back first, the fixed session's rules take this week too). « Non,
    juste cette fois »: never asked again for that weekday."""
    holder = holder_of(state, run_id)
    a = answer_of(holder) if holder is not None else None
    offer = (a or {}).get("recur")
    if holder is None or a is None or not offer or offer.get("state") != ASK:
        raise co.Invalid("Plus de question en attente.")
    plan = state.plan
    if not yes:
        plan.fixed = {**(plan.fixed or {}), "declined": sorted(set(co.declined_days(plan)) | {int(offer["day"])})}
        _set_answer(holder, {**a, "recur": {**offer, "state": NO}})
        return
    fixed = co.clean_fixed(offer["day"], offer["minutes"], None, plan.weekdays, plan.long_day)
    await _revert(db, state, [r for r in a.get("changes") or [] if "id" in r])
    _set_answer(holder, {**a, "changes": [], "recur": {**offer, "state": YES, "was": plan.fixed}})
    await co.set_fixed(db, plan, user, fixed)


async def undo(db: AsyncSession, state: co.State, user: User, run_id: int) -> None:
    """« Annuler »: the sessions the answer changed, the session that holds the run (its kind, its link) and a fixed
    session set by « Oui, tous les mercredis », back as they were; the card asks again."""
    holder = holder_of(state, run_id)
    a = answer_of(holder) if holder is not None else None
    if holder is None or a is None:
        raise co.Invalid("Rien à annuler.")
    await _revert(db, state, [r for r in a.get("changes") or [] if "id" in r])
    if (a.get("recur") or {}).get("state") == YES:
        kept = (a["recur"].get("was") or {})
        state.plan.fixed = {**({k: v for k, v in kept.items() if k != "declined"}),
                            **({"declined": co.declined_days(state.plan)} if co.declined_days(state.plan) else {})} \
            or None
    if a.get("created"):
        state.rows.remove(holder)
        await db.delete(holder)
    elif a.get("was"):
        restore(holder, a["was"], state.clock.now)
    else:
        _set_answer(holder, None)
    await db.flush()


def close(state: co.State, run_id: int) -> None:
    """« Fermer »: the card answered goes."""
    holder = holder_of(state, run_id)
    a = answer_of(holder) if holder is not None else None
    if holder is not None and a is not None:
        _set_answer(holder, {**a, "open": False})


def said_changes(answer: dict) -> list[dict]:
    """What the card says changed, in order."""
    return _said(answer)
