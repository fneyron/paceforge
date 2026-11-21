"""Coaching: what the pages draw, from a plan in sync (coaching.State). Every string is made here, each number once:
the week's « fait / prévu » on its bar, a session's duration in its title, the activity's own figures on its line; the
plan's chart shows a week's total only on a tap (viz.rest), as Activités' weeks do.
"""
import calendar
from datetime import date, timedelta

from app.services import coaching as co
from app.services import coaching_plan as cp
from app.services import race_prep as rp
from app.services import viz
from app.services.heart_rate_zones import running_zones
from app.services.viz import JOURS, JOURS_L, MOIS, MOIS_L, NBSP, NNBSP, d_long, hm, hm_long

KIND_SHORT = {cp.FOOTING: "Footing", cp.LONG: "Longue", cp.QUALITY: "Séance", cp.HILLS: "Côtes", cp.RECUP: "Récup",
              cp.RACE: "Course"}
KIND_CLASS = {cp.FOOTING: "easy", cp.RECUP: "recup", cp.LONG: "long", cp.QUALITY: "hard", cp.HILLS: "hard",
              cp.RACE: "race"}
STATUS_WORD = {co.PLANNED: "prévue", co.DONE: "faite", co.MISSED: "pas faite"}
LEGEND = (("easy", "Footing"), ("long", "Longue"), ("hard", "Séance"), ("race", "Course"))
CHART_WEEKS = 26  # the plan's chart: 26 weeks at most
METHOD = (
    "Ton plan est en temps, pas en kilomètres. Il part de ton volume habituel et monte d’environ 10 % par semaine ; "
    "une semaine sur quatre est plus légère.",
    "La semaine, c’est surtout du facile : au moins 80 % du temps en aisance, une sortie longue, et une séance "
    "plus soutenue quand tu as trois jours ou plus.",
    "Jamais deux jours durs de suite, pas de séance de moins de 30 minutes, et seulement tes jours disponibles.",
    "Avant une course : deux semaines d’affûtage, le volume baisse et quelques efforts courts restent.",
    "Après chaque séance, dis comment c’était. Si le facile te paraît dur, ou si des séances sautent, la semaine "
    "suivante s’allège toute seule.",
    "Tes sorties enregistrées cochent les séances du jour. Sans montre, coche « Fait ».",
    "Ce que tu modifies ou déplaces reste tel quel ; le reste du plan se recalcule autour.",
)


def week_label(monday: date) -> str:
    sunday = monday + timedelta(days=6)
    if monday.month == sunday.month:
        return f"{monday.day}–{sunday.day}{NBSP}{MOIS[monday.month - 1]}"
    return f"{monday.day}{NBSP}{MOIS[monday.month - 1]} – {sunday.day}{NBSP}{MOIS[sunday.month - 1]}"


def hr_rows(reference: float | None) -> list[dict] | None:
    """The app's five heart-rate zones for a known maximum (heart_rate_zones), else None."""
    if not reference:
        return None
    zones = running_zones(reference)
    return zones["rows"] if zones.get("ready") else None


def feel_and_range(kind: str, structure: dict | None, rows: list[dict] | None) -> tuple[str, str | None]:
    """(the feel, by the talk test; the heart-rate range when a maximum is known, else None)."""
    level = cp.main_level(kind, structure)
    text = cp.FEEL.get(level, cp.FEEL[2])
    if kind in (cp.QUALITY, cp.HILLS):
        text = {3: "Efforts soutenus : quelques mots, pas des phrases.",
                4: "Efforts vifs : parler devient impossible."}.get(level, text)
    span = cp.HR_ZONES.get(level)
    hr = f"{rows[span[0] - 1]['low']}–{rows[span[1] - 1]['high']}{NNBSP}bpm" if rows and span else None
    return rp.fr(text) or text, hr


def guidance(kind: str, structure: dict | None, rows: list[dict] | None) -> str:
    """One line: the feel, then the heart-rate range when there is one (the calendar feed's description)."""
    text, hr = feel_and_range(kind, structure, rows)
    return f"{text} {hr}." if hr else text


def countdown(goal: cp.Goal, today: date) -> str:
    if goal.race_day is None:
        return "Progresser, sans course"
    k = (goal.race_day - today).days
    if k < 0:
        return f"Course passée · {goal.name}"
    return f"{rp.j_label(-k)} · {goal.name}"


def _activity(run, link: bool) -> dict:
    parts = []
    if run.km > 0:
        parts.append(f"{viz.fr_num(run.km, 1)}{NNBSP}km")
    parts.append(hm(run.minutes))
    if run.dplus >= 1:
        parts.append(viz.dplus(run.dplus))
    return {"id": run.id, "name": run.name or "Sortie", "line": " · ".join(parts),
            "href": f"/activity/{run.id}" if link else None}


def _session(row, state: co.State, rows_hr, runs_by_id: dict, coach: bool) -> dict:
    today = state.clock.today
    run = runs_by_id.get(row.activity_id) if row.activity_id else None
    planned = row.status == co.PLANNED
    return {
        "id": row.id, "kind": row.kind, "title": cp.KINDS.get(row.kind, row.kind), "duration": hm(row.minutes),
        "minutes": row.minutes, "cls": KIND_CLASS.get(row.kind, "easy"), "status": row.status,
        "text": cp.describe(row.kind, row.structure), "profile": viz.intensity_profile(row.structure),
        "guidance": feel_and_range(row.kind, row.structure, rows_hr),
        "edited": {"coach": "Modifiée par ton coach", "athlete": "Modifiée par toi"}.get(row.origin) if not coach
        else {"coach": "Modifiée par toi", "athlete": "Modifiée par l’athlète"}.get(row.origin),
        "activity": _activity(run, link=not coach) if run else None,
        "done_manual": hm(row.done_minutes) if row.status == co.DONE and run is None and row.done_minutes else None,
        "done_min": row.done_minutes, "ressenti": row.ressenti, "note": row.note, "coach_comment": row.coach_comment,
        "can_tick": not coach and row.day <= today and run is None and row.status != co.CANCELLED,
        "can_move": planned and row.day >= today and row.kind != cp.RACE,
        "can_edit": planned and row.kind != cp.RACE and (coach or row.day >= today),
        "race_href": f"/simulator/routes/{state.goal.route_id}" if row.kind == cp.RACE and state.goal.route_id
        and not coach else None,
    }


def _marks(rows: list, cls_only: bool = False) -> list[dict]:
    return [{"cls": KIND_CLASS.get(r.kind, "easy"), "status": r.status} for r in rows]


def _cell(day: date, rows: list, extra: bool, state: co.State, selected: date) -> dict:
    said = [f"{cp.KINDS.get(r.kind, r.kind)} {hm_long(r.minutes)}, {STATUS_WORD.get(r.status, '')}" for r in rows]
    if extra:
        said.append("une sortie hors plan")
    return {"iso": day.isoformat(), "wd": JOURS[day.weekday()], "num": day.day, "today": day == state.clock.today,
            "selected": day == selected, "past": day < state.clock.today, "marks": _marks(rows), "extra": extra,
            "aria": f"{JOURS_L[day.weekday()]} {day.day} {MOIS_L[day.month - 1]} : " + (" ; ".join(said) or "repos")}


def bounds(state: co.State) -> tuple[date, date]:
    """The first and last Mondays the calendar shows."""
    days = [r.day for r in state.rows if r.status != co.CANCELLED]
    m0 = cp.monday_of(state.clock.today)
    first = min([state.plan.start_monday, m0] + [cp.monday_of(d) for d in days])
    last = max([m0] + [cp.monday_of(d) for d in days])
    return first, last


def pick_day(state: co.State, d: date | None, w: date | None, month: bool) -> date:
    """The day shown: the one asked; else, for a week (or month) asked, today when it is in it, else its first
    session; else today when it has a session or a run, else the next session, else today."""
    today = state.clock.today
    first, last = bounds(state)
    lo, hi = first, last + timedelta(days=6)
    live = [r for r in state.rows if r.status != co.CANCELLED]
    if d is not None:
        return min(max(d, lo), hi)
    if w is not None:
        if month:
            start = w.replace(day=1)
            end = start.replace(day=calendar.monthrange(start.year, start.month)[1])
        else:
            start = cp.monday_of(w)
            end = start + timedelta(days=6)
        start, end = max(start, lo), min(end, hi)
        if start <= today <= end:
            return today
        inside = sorted(r.day for r in live if start <= r.day <= end)
        return inside[0] if inside else min(max(start, lo), hi)
    if any(r.day == today for r in live) or any(s.day == today for s in co.hors_plan(state.runs, state.rows)):
        return today
    ahead = sorted(r.day for r in live if r.day > today)
    return ahead[0] if ahead else today


def week_summary(state: co.State, monday: date, done: dict[date, float]) -> dict:
    """The week's one bar: « fait / prévu » in time; a week still to come says its planned time alone."""
    planned = sum(r.minutes for r in state.rows if cp.monday_of(r.day) == monday and r.status != co.CANCELLED
                  and r.kind != cp.RACE)
    did = done.get(monday, 0)
    if monday > cp.monday_of(state.clock.today):
        return {"planned": planned, "done": 0, "fill": 0, "label": f"{hm(planned)} prévues" if planned else "",
                "aria": f"{hm_long(planned)} prévues"}
    fill = round(min(1.0, did / planned), 3) if planned else (1.0 if did else 0)
    return {"planned": planned, "done": did, "fill": fill,
            "label": f"{hm(did)} / {hm(planned)}" if planned else (hm(did) if did else ""),
            "aria": f"{hm_long(did)} faites sur {hm_long(planned)} prévues" if planned else
            (f"{hm_long(did)} faites, rien de prévu" if did else "Rien de prévu")}


def week_note(state: co.State, monday: date) -> dict:
    """The week's phase, whether it is the lighter 4th one, and the one line an adaptation gives it."""
    entry = (state.plan.adaptations or {}).get(monday.isoformat()) or {}
    phase = cp.phase_of(monday, state.inputs) if monday >= state.plan.start_monday else None
    week = next((x for x in state.weeks if x.monday == monday), None)
    return {"phase": cp.PHASES.get(phase) if phase and phase != "apres" else None,
            "light": bool(week and week.light and not entry.get("reason")),
            "reason": cp.REASONS.get(entry.get("reason") or "")}


def chart(state: co.State, done: dict[date, float]) -> dict | None:
    """The plan's weeks: planned (outline) against done (solid), the phases, the current week, the race."""
    first, last = bounds(state)
    last = max([last] + [w.monday for w in state.weeks])
    n = (last - first).days // 7 + 1
    if n > CHART_WEEKS:
        first = last - timedelta(weeks=CHART_WEEKS - 1)
        n = CHART_WEEKS
    mondays = [first + timedelta(weeks=i) for i in range(n)]
    m0 = cp.monday_of(state.clock.today)
    by: dict[date, int] = {}
    for r in state.rows:
        if r.status != co.CANCELLED and r.kind != cp.RACE:
            by[cp.monday_of(r.day)] = by.get(cp.monday_of(r.day), 0) + r.minutes
    planned = [by.get(m) or None for m in mondays]
    did = [(done.get(m, 0) if m <= m0 else None) for m in mondays]
    if not any(planned) and not any(did):
        return None
    phases, read, said = [], [], []
    for m, p, d in zip(mondays, planned, did, strict=True):
        key = cp.phase_of(m, state.inputs) if m >= state.plan.start_monday else ""
        label = cp.PHASES.get(key, "") if key != "apres" else ""
        phases.append((key if label else "", label))
        head = "Semaine en cours" if m == m0 else rp.week_label(m)
        if m <= m0:
            value = f"{hm(d or 0)} / {hm(p)}" if p else hm(d or 0)
            spoken = (f"{hm_long(d or 0)} faites sur {hm_long(p)} prévues" if p else f"{hm_long(d or 0)} faites")
        else:
            value = f"{hm(p)} prévues" if p else "—"
            spoken = f"{hm_long(p)} prévues" if p else "rien de prévu"
        read.append([head, value, label])
        said.append(f"Semaine du {d_long(m)} : {spoken}" + (f", phase {label}" if label else ""))
    goal = state.goal
    race_week = cp.monday_of(goal.race_day) if goal.race_day else None
    races = [(race_week, goal.name)] if race_week in mondays else []
    current = mondays.index(m0) if m0 in mondays else None
    c = viz.plan_weeks(mondays, planned, did, phases=phases, current=current, readouts=read, arias=said, races=races)
    c["summary"] = ("Les semaines du plan : le contour est le temps prévu, la barre pleine le temps fait. "
                    "Les phases sont marquées sous l’axe.")
    weeks = f"{n} semaine{'s' if n > 1 else ''}"
    return viz.rest(c, ["Prévu et fait", weeks, ""],
                    f"Le plan sur {weeks}. Choisis une semaine pour voir son temps prévu et fait.",
                    back=current if current is not None else n - 1)


def page(state: co.State, *, d: date | None = None, w: date | None = None, month: bool = False,
         move: int | None = None, coach: bool = False, hr_reference: float | None = None) -> dict:
    """Everything the calendar part draws for one selected day."""
    today = state.clock.today
    selected = pick_day(state, d, w, month)
    monday = cp.monday_of(selected)
    first, last = bounds(state)
    live = [r for r in state.rows if r.status != co.CANCELLED]
    by_day: dict[date, list] = {}
    for r in live:
        by_day.setdefault(r.day, []).append(r)
    extras = co.hors_plan(state.runs, state.rows)
    extra_days = {s.day for s in extras}
    done = co.done_by_week(state.runs, state.rows)
    rows_hr = hr_rows(hr_reference)
    runs_by_id = {s.id: s for s in state.runs}
    moving = next((r for r in live if r.id == move and r.status == co.PLANNED and r.day >= today
                   and r.kind != cp.RACE), None) if move else None
    strip = [_cell(monday + timedelta(days=i), by_day.get(monday + timedelta(days=i), []),
                   monday + timedelta(days=i) in extra_days, state, selected) for i in range(7)]
    out = {
        "today": today.isoformat(), "selected": selected.isoformat(), "view": "mois" if month else "semaine",
        "day_label": ("Aujourd’hui · " if selected == today else "") + viz.d_long(selected),
        "week": {"monday": monday.isoformat(), "label": week_label(monday), "strip": strip,
                 "prev": (monday - timedelta(weeks=1)).isoformat() if monday > first else None,
                 "next": (monday + timedelta(weeks=1)).isoformat() if monday < last else None,
                 "summary": week_summary(state, monday, done), **week_note(state, monday)},
        "sessions": [_session(r, state, rows_hr, runs_by_id, coach) for r in by_day.get(selected, [])],
        "extras": [_activity(s, link=not coach) for s in extras if s.day == selected],
        "moving": {"id": moving.id, "title": cp.KINDS.get(moving.kind, moving.kind)} if moving else None,
        "can_add": coach and selected >= today,
        "legend": LEGEND, "legend_extra": False, "ressenti": list(enumerate(cp.RESSENTI, start=1)),
        "kinds": [(k, cp.KINDS[k]) for k in cp.EDITABLE],
        "chart": chart(state, done),
    }
    out["legend_extra"] = any(c["extra"] for c in strip)
    if month:
        start = selected.replace(day=1)
        days_in = calendar.monthrange(start.year, start.month)[1]
        end = start.replace(day=days_in)
        grid, m = [], cp.monday_of(start)
        while m <= end:
            grid.append([{**_cell(m + timedelta(days=i), by_day.get(m + timedelta(days=i), []),
                                  m + timedelta(days=i) in extra_days, state, selected),
                          "out": (m + timedelta(days=i)).month != start.month} for i in range(7)])
            m += timedelta(weeks=1)
        before, after = start - timedelta(days=1), end + timedelta(days=1)
        out["legend_extra"] = any(c["extra"] for week in grid for c in week)
        out["month"] = {"label": f"{MOIS_L[start.month - 1]} {start.year}", "weeks": grid, "heads": JOURS,
                        "prev": before.replace(day=1).isoformat() if cp.monday_of(before) >= first else None,
                        "next": after.isoformat() if cp.monday_of(after) <= last else None}
    return out


def header(state: co.State) -> dict:
    today = state.clock.today
    phase = cp.phase_of(cp.monday_of(today), state.inputs)
    return {"goal": countdown(state.goal, today), "phase": cp.PHASES.get(phase) if phase != "apres" else None,
            "finished": state.goal.race_day is not None and state.goal.race_day < today}


def athlete_card(state: co.State, user) -> dict:
    """The coach's list: one athlete at a glance — this week's bar, the last sessions with status and ressenti."""
    today = state.clock.today
    done = co.done_by_week(state.runs, state.rows)
    recent = [r for r in state.rows if r.status in (co.DONE, co.MISSED) and r.day <= today][-5:]
    return {"id": user.id, "name": user.firstname or "Athlète", **header(state),
            "summary": week_summary(state, cp.monday_of(today), done),
            "recent": [{"day": viz.d_short(r.day), "kind": KIND_SHORT.get(r.kind, r.kind),
                        "cls": KIND_CLASS.get(r.kind, "easy"), "status": r.status,
                        "word": STATUS_WORD.get(r.status, ""),
                        "ressenti": cp.RESSENTI[r.ressenti - 1] if r.ressenti else None,
                        "level": r.ressenti} for r in reversed(recent)]}
