"""Coaching: what the pages draw, from a plan in sync (coaching.State). Every string is made here, each number once:
a week's « fait / prévu » on its line of the calendar, a session's duration in its title, the activity's own figures
on its line; the plan's chart shows a week's total only on a tap (viz.rest), as Activités' weeks do.

The page is a calendar (docs/coaching.md › « Les écrans »): this week and the next (or a month), each day its
sessions as blocks — a colour and a word for the kind, a height for the duration, a shape for the status — and the
runs done outside the plan; the day chosen opens under it; a session done that waits for its ressenti is asked about
first.
"""
import calendar
from datetime import date, timedelta

from app.services import coaching as co
from app.services import coaching_agenda as ag
from app.services import coaching_plan as cp
from app.services import coaching_slots as cs
from app.services import race_prep as rp
from app.services import viz
from app.services.heart_rate_zones import running_zones
from app.services.viz import JOURS, JOURS_L, MOIS, MOIS_L, NBSP, NNBSP, d_long, hm, hm_long

KIND_SHORT = {cp.FOOTING: "Footing", cp.LONG: "Longue", cp.QUALITY: "Séance", cp.HILLS: "Côtes", cp.RECUP: "Récup",
              cp.RACE: "Course"}
KIND_CLASS = {cp.FOOTING: "easy", cp.RECUP: "recup", cp.LONG: "long", cp.QUALITY: "hard", cp.HILLS: "hard",
              cp.RACE: "race"}
STATUS_WORD = {co.PLANNED: "prévue", co.DONE: "faite", co.MISSED: "pas faite"}
LEGEND = (("easy", "Footing"), ("long", "Sortie longue"), ("hard", "Séance"), ("race", "Course"))
BAR_SCALE_MIN = 60  # the calendar's blocks: the tallest is the longest session shown, 1 h at least
BAR_MIN = 22  # % of the cell's room: a short session still shows
WINDOW_WEEKS = 2  # the calendar opens on this week and the next
EXTRA_WORD = "Sortie"  # a run outside the plan, as a day's block reads
WEEK_NAMES = {0: "Cette semaine", 1: "Semaine prochaine", -1: "Semaine dernière"}
HARD_PAIR = "{a} et {b} : deux jours durs de suite."
UNDO = {co.DONE: "Annuler « fait »", co.MISSED: "Annuler « pas faite »"}
CHART_WEEKS = 26  # the plan's chart: 26 weeks at most
# which time the plan runs on, said once in the header: the race page's objective, the simulator's estimate, or —
# no prediction — a rough guess
TIME_WORD = {co.OBJECTIVE: "Objectif", co.ESTIMATE: "Estimation", co.ROUGH: "Environ"}
# the form (docs/coaching.md › « La forme »): the athlete reads why, with the way to Santé, never the number; a
# coach reads that the session was adapted, never why (no health word reaches a coach's page)
FORM_ATHLETE = "Séance allégée : récupération basse ce matin"
FORM_COACH = "Séance adaptée ce matin"
FORM_MOVED = "Séance reportée de {day}"
FORM_SUGGEST = "Récupération basse ce matin."
COACH_WEEK = "Semaine allégée."  # a week the form lightened, as a coach reads it
# the agenda (docs/coaching.md › « Mon agenda »): the athlete reads why a session changed day; a coach reads the
# times and that it moved, never that a day is full (nothing of the athlete's calendar reaches a coach)
MOVED = "{home} est plein : séance déplacée à {day}."
MOVED_COACH = "Séance déplacée de {home} à {day}."
ENDED = {co.PASSED: "{name} est derrière toi : {when}le plan continue sans course.",
         co.GONE: "{name} n’est plus dans tes courses : {when}le plan continue sans course."}
METHOD = (
    "Ton plan est en temps, pas en kilomètres. Il part de ton volume habituel et monte d’environ 10 % par semaine ; "
    "une semaine sur quatre est plus légère.",
    "Aucune séance ne dépasse de plus de 10 % ta plus longue sortie des 30 derniers jours : la semaine peut alors "
    "demander un peu moins que prévu.",
    "La semaine, c’est surtout du facile : au moins 80 % du temps en aisance, une sortie longue, et une séance "
    "plus soutenue quand tu as trois jours ou plus.",
    "Jamais deux jours durs de suite, pas de séance de moins de 30 minutes, et seulement tes jours disponibles.",
    "Avant une course : deux semaines d’affûtage, le volume baisse et quelques efforts courts restent.",
    "Le plan vise le temps de ta course : ton objectif, sinon l’estimation de sa page. Pour un trail, tes sorties "
    "longues visent aussi un dénivelé, qui monte au fil des semaines.",
    "Après chaque séance, dis comment c’était. Si le facile te paraît dur, ou si des séances sautent, la semaine "
    "suivante s’allège toute seule.",
    "Si tu l’actives, le plan lit ta récupération de Santé chaque matin : basse, ta séance dure devient un footing "
    "et se décale. Une bonne récupération n’ajoute jamais rien.",
    "Tes sorties enregistrées cochent les séances du jour. Sans montre, touche « C’est fait ».",
    "Si tu relies ton agenda, chaque séance prend un créneau libre, hors réunions et rendez-vous ; un jour plein "
    "décale sa séance. PaceForge n’en garde que tes heures occupées.",
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


def goal_time(goal: cp.Goal, coach: bool = False) -> dict | None:
    """« Objectif 6h30 » or « Estimation 7h10 »: the time the plan runs on and where it comes from, said once, with
    the way to the race page (the athlete's own: a coach reads the words only)."""
    if goal.race_day is None or not goal.race_minutes or goal.time_kind not in TIME_WORD:
        return None
    return {"label": f"{TIME_WORD[goal.time_kind]}{NBSP}{hm(goal.race_minutes)}",
            "href": f"/simulator/routes/{goal.route_id}" if goal.route_id and not coach else None}


def ended_line(state: co.State) -> str | None:
    """The one line of a plan that lost its race (deleted, or behind): it goes on without one from next Monday."""
    ended = co.goal_ended(state.plan)
    if ended is None or ended["ended"] not in ENDED:
        return None
    when = "dès lundi, " if ended["from"] > state.clock.today else ""
    return rp.fr(ENDED[ended["ended"]].format(name=ended["name"] or "Ta course", when=when))


def climb_of(row) -> int | None:
    """The climb a session aims at (a trail race's long run or long-hill session), in metres."""
    try:
        value = int((row.structure or {}).get("climb") or 0)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def climb_line(row) -> str | None:
    target = climb_of(row)
    return f"vise {viz.dplus(target)}" if target else None


def _activity(run, link: bool, climb: int | None = None) -> dict:
    """An activity's own figures on one line; `climb`: the session's climb target, said against the run's."""
    parts = []
    if run.km > 0:
        parts.append(f"{viz.fr_num(run.km, 1)}{NNBSP}km")
    parts.append(hm(run.minutes))
    if climb:
        parts.append(f"{viz.dplus(run.dplus)} sur {viz.dplus(climb)} visés")
    elif run.dplus >= 1:
        parts.append(viz.dplus(run.dplus))
    return {"id": run.id, "name": run.name or "Sortie", "line": " · ".join(parts),
            "href": f"/activity/{run.id}" if link else None}


def clock_time(minutes: int) -> str:
    """A time of day: 735 → « 12 h 15 » (a duration is « 1h05 »: the two never read alike)."""
    minutes %= 24 * 60
    return f"{minutes // 60}{NNBSP}h{NNBSP}{minutes % 60:02d}"


def slot_label(row) -> str | None:
    """« 12 h 15 – 13 h 20 »: the time a session was given, its start to its end."""
    if row.slot_min is None:
        return None
    return f"{clock_time(row.slot_min)} – {clock_time(row.slot_min + row.minutes)}"


def _other_slot(row, state: co.State) -> bool:
    """Whether « Autre créneau » has another free time to offer for this session."""
    if state.agenda is None or row.status != co.PLANNED or row.day < state.clock.today or row.kind == cp.RACE:
        return False
    local = state.clock.now.astimezone(state.clock.zone)
    return ag.next_slot(row, state.agenda, state.plan, state.clock.today, local.hour * 60 + local.minute,
                        state.usual) is not None


def week_moves(state: co.State, monday: date, coach: bool = False) -> str | None:
    """« Mardi est plein : séance déplacée à mercredi. » for each session of the week the agenda sent to another
    day (still to do); a coach reads only that it moved."""
    out = []
    for r in state.rows:
        if (r.status == co.PLANNED and cp.monday_of(r.day) == monday and r.auto_day is not None
                and r.auto_day != r.day and not r.pinned and r.origin == "auto" and not r.form):
            home, day = JOURS_L[r.auto_day.weekday()], JOURS_L[r.day.weekday()]
            text = (MOVED_COACH if coach else MOVED).format(home=home, day=day)
            out.append(rp.fr(text[:1].upper() + text[1:]) or text)
    return " ".join(out) or None


def _form(row, state: co.State, coach: bool) -> dict | None:
    """What a session the form touched says: lightened on a low morning — to the athlete why, with the way to Santé
    and where the session went; to a coach only that it was adapted — or moved here from that morning."""
    if not row.form:
        return None
    today, monday = state.clock.today, cp.monday_of(row.day)
    week = [r for r in state.rows if r.form and r.status != co.CANCELLED and cp.monday_of(r.day) == monday]
    then = "" if row.day == today else "-là"
    if row.form == cp.LIGHTENED:
        if coach:
            return {"text": FORM_COACH + then, "href": None, "moved": None}
        to = next((r for r in week if r.form == cp.MOVED and r.day > row.day), None)
        return {"text": rp.fr(FORM_ATHLETE + then), "href": "/sante",
                "moved": f"La séance prévue passe à {JOURS_L[to.day.weekday()]}." if to else None}
    if row.form == cp.MOVED:
        origin = next((r for r in reversed(week) if r.form == cp.LIGHTENED and r.day < row.day), None)
        if origin is not None:
            return {"text": FORM_MOVED.format(day=JOURS_L[origin.day.weekday()]), "href": None, "moved": None}
    return None


# what the pages call the person who follows a plan when no first name can sign for them (the word « coach » is
# never shown: the plan is automatic, nobody needs a coach to have one)
FOLLOWER = "la personne qui suit ton plan"
COMMENT = "Commentaire"


def _session(row, state: co.State, rows_hr, runs_by_id: dict, coach: bool, names: dict | None = None) -> dict:
    """`names`: {"athlete": first name, "follower": the first name of the one person who follows the plan, None
    when there are several or none} — a session someone else changed, or a comment, is signed with a first name."""
    today = state.clock.today
    names = names or {}
    follower, athlete = names.get("follower"), names.get("athlete") or "l’athlète"
    run = runs_by_id.get(row.activity_id) if row.activity_id else None
    planned = row.status == co.PLANNED
    # the structure in words, then the climb to aim at while the session is still to do (once done, the activity's
    # line says it against the run's own climb)
    said = [cp.describe(row.kind, row.structure), climb_line(row) if planned else None]
    text = " · ".join(x for x in said if x)
    # how hard it is, at a glance: a steady effort is one of four levels (a gauge); a structured session keeps its
    # profile, block by block
    profile = viz.intensity_profile(row.structure)
    steady = profile is None or len(profile["blocks"]) < 2
    level = cp.main_level(row.kind, row.structure)
    can_tick = not coach and row.day <= today and run is None and row.status != co.CANCELLED
    return {
        "id": row.id, "kind": row.kind, "title": cp.KINDS.get(row.kind, row.kind),
        # the race's time is the header's (« Objectif 6h30 »): printed once
        "duration": "" if row.kind == cp.RACE and goal_time(state.goal) else hm(row.minutes),
        "minutes": row.minutes, "cls": KIND_CLASS.get(row.kind, "easy"), "status": row.status,
        "text": text[:1].upper() + text[1:], "profile": None if steady else profile,
        "gauge": {"level": level, "aria": f"Effort {cp.LEVELS.get(level, '')} : niveau {level} sur 4"} if steady
        else None,
        "guidance": feel_and_range(row.kind, row.structure, rows_hr),
        "edited": {"coach": f"Modifiée par {follower or FOLLOWER}", "athlete": "Modifiée par toi"}.get(row.origin)
        if not coach else {"coach": "Modifiée par toi", "athlete": f"Modifiée par {athlete}"}.get(row.origin),
        "comment_by": follower or COMMENT,
        "activity": _activity(run, link=not coach, climb=climb_of(row)) if run else None,
        "done_manual": hm(row.done_minutes) if row.status == co.DONE and run is None and row.done_minutes else None,
        "done_min": row.done_minutes, "ressenti": row.ressenti, "note": row.note, "coach_comment": row.coach_comment,
        # the agenda: the time the session was given (a coach reads it too); for the athlete, a day with no room
        # and the way to another free time
        "slot": slot_label(row) if planned else None,
        "full": planned and not coach and row.slot_flag == cs.FULL and row.slot_min is None,
        "can_slot": not coach and _other_slot(row, state),
        "form": _form(row, state, coach),
        # a low morning and a hard session the plan does not change by itself (pinned, or a coach's): the athlete
        # is offered to lighten it — never shown to a coach
        "suggest": FORM_SUGGEST if (not coach and planned and row.day == today and today in state.low
                                    and row.kind in co.HARD_KINDS and not row.form) else None,
        # one main action, « C’est fait »; the rest is quiet: « Pas faite » while it is still to do, and the way
        # back from a tick made by hand
        "can_tick": can_tick, "can_done": can_tick and row.status != co.DONE,
        "can_missed": can_tick and row.status == co.PLANNED,
        "undo": UNDO.get(row.status) if can_tick and (row.status == co.DONE or (
            row.status == co.MISSED and row.manual)) else None,
        # « Déplacer »: any session still to do; one missed earlier this week is « reportée »
        "can_move": co.movable(row, today), "move_word": "Reporter" if row.status == co.MISSED else "Déplacer",
        "can_edit": planned and row.kind != cp.RACE and (coach or row.day >= today),
        "race_href": f"/simulator/routes/{state.goal.route_id}" if row.kind == cp.RACE and state.goal.route_id
        and not coach else None,
    }


def bar_height(minutes: float, scale: float) -> int:
    """A session's block, in % of its cell's room: its duration against the longest session shown."""
    return int(min(100, max(BAR_MIN, round(100 * minutes / max(scale, 1)))))


def hard_neighbours(rows: list, today: date) -> list[tuple[date, date]]:
    """The pairs of days in a row, from today on, that each hold a hard session still to do (coaching_plan.HARD).
    The generator never makes one; a person moving a session may: the calendar says so, and lets them."""
    days = sorted({r.day for r in rows if r.status == co.PLANNED and r.kind in cp.HARD and r.day >= today})
    return [(x, y) for x, y in zip(days, days[1:], strict=False) if (y - x).days == 1]


def _cell(day: date, rows: list, extra: float, state: co.State, selected: date, scale: float, *,
          waiting: frozenset = frozenset(), moving=None, free: frozenset = frozenset(), hard: frozenset = frozenset()
          ) -> dict:
    """One day of the calendar: its sessions as blocks (kind, status, height), the run done outside the plan as a
    grey one, one word, the time given, and what a screen reader says of it all. `extra`: the minutes run outside
    the plan; `scale`: the minutes a full-height block stands for; `waiting`: the ids of the sessions that wait for
    a debrief; `moving`: the session being moved (the day then says whether it can take it, and whether that would
    put two hard days side by side — `hard`: the days that hold another hard session to do)."""
    today = state.clock.today
    said, blocks, time = [], [], None
    for r in rows:
        slot = clock_time(r.slot_min) if r.status == co.PLANNED and r.slot_min is not None else None
        time = time or slot
        said.append(f"{cp.KINDS.get(r.kind, r.kind)} {hm_long(r.minutes)}, {STATUS_WORD.get(r.status, '')}"
                    + (f" à {slot}" if slot else "") + (", à débriefer" if r.id in waiting else ""))
        blocks.append({"cls": KIND_CLASS.get(r.kind, "easy"), "status": r.status,
                       "h": 100 if r.kind == cp.RACE else bar_height(r.minutes, scale)})
    if extra:
        said.append("une sortie hors plan")
        blocks.append({"cls": "extra", "status": co.DONE, "h": bar_height(extra, scale)})
    if len(blocks) > 1:  # several the same day share the cell's room
        for block in blocks:
            block["h"] = max(BAR_MIN // 2, block["h"] // len(blocks))
    word = KIND_SHORT.get(rows[0].kind, rows[0].kind) if rows else (EXTRA_WORD if extra else "")
    out = {"iso": day.isoformat(), "wd": JOURS[day.weekday()], "num": day.day, "today": day == today,
           "selected": day == selected, "past": day < today, "blocks": blocks, "extra": bool(extra),
           "word": word + (f" +{len(rows) - 1}" if len(rows) > 1 else ""), "time": time,
           "todo": any(r.id in waiting for r in rows),
           "aria": f"{JOURS_L[day.weekday()]} {day.day} {MOIS_L[day.month - 1]} : " + (" ; ".join(said) or "repos")}
    if moving is not None:
        this_week = cp.monday_of(day) == cp.monday_of(today)
        out["target"] = day != moving.day and (day >= today or (day in free and this_week))
        # a hard session next to another one: a warning on the day, never a refusal
        out["warn"] = bool(out["target"] and moving.kind in cp.HARD
                           and any(day + timedelta(days=k) in hard for k in (-1, 1)))
    return out


def bounds(state: co.State) -> tuple[date, date]:
    """The first and last Mondays the calendar shows."""
    days = [r.day for r in state.rows if r.status != co.CANCELLED]
    m0 = cp.monday_of(state.clock.today)
    first = min([state.plan.start_monday, m0] + [cp.monday_of(d) for d in days])
    last = max([m0] + [cp.monday_of(d) for d in days])
    return first, last


def span_label(first: date, last: date) -> str:
    """« 16–29 nov. », « 23 nov. – 6 déc. »: the days a calendar of whole weeks shows."""
    if first.month == last.month:
        return f"{first.day}–{last.day}{NBSP}{MOIS[first.month - 1]}"
    return f"{first.day}{NBSP}{MOIS[first.month - 1]} – {last.day}{NBSP}{MOIS[last.month - 1]}"


def window(state: co.State, d: date | None, w: date | None, month: bool) -> tuple[date, list[date], date]:
    """(the day opened, the Mondays of the weeks shown, the first day shown — of the month, in a month).
    - Weeks: WINDOW_WEEKS weeks from `w`'s; without `w`, this week and the next — the week of `d` when the day
      asked is outside them (a link to a day opens the calendar on it).
    - Month: the weeks that hold the month of `w` (else of `d`, else of today).
    - The day: the one asked; else today when it is shown, else the first session shown, else the first day shown.
      The page opens on today, a rest day too (it names the next session)."""
    today = state.clock.today
    m0 = cp.monday_of(today)
    first, last = bounds(state)
    lo, hi = first, last + timedelta(days=6)
    if d is not None:
        d = min(max(d, lo), hi)
    if month:
        anchor = min(max(w or d or today, lo), hi)
        start = anchor.replace(day=1)
        end = start.replace(day=calendar.monthrange(start.year, start.month)[1])
        mondays, m = [], cp.monday_of(start)
        while m <= end:
            mondays.append(m)
            m += timedelta(weeks=1)
    else:
        span = timedelta(weeks=WINDOW_WEEKS)
        if w is not None:
            start = cp.monday_of(w)
            if d is not None and not start <= d < start + span:
                start = cp.monday_of(d)
        else:
            start = m0 if d is None or m0 <= d < m0 + span else cp.monday_of(d)
        start = min(max(start, first), last)
        mondays = [start + timedelta(weeks=i) for i in range(WINDOW_WEEKS)]
        end = mondays[-1] + timedelta(days=6)
    if d is not None:
        return d, mondays, start
    if start <= today <= end:
        return today, mondays, start
    inside = sorted(r.day for r in state.rows if r.status != co.CANCELLED and start <= r.day <= end)
    return (inside[0] if inside else min(max(start, lo), hi)), mondays, start


def week_name(monday: date, today: date) -> str:
    """« Cette semaine », « Semaine prochaine », « Semaine dernière », else its dates."""
    return WEEK_NAMES.get((monday - cp.monday_of(today)).days // 7) or week_label(monday)


def day_word(day: date, today: date) -> str:
    """« d’aujourd’hui », « d’hier », « de mardi », « du mardi 10 novembre »: a day of the last week, after « de »."""
    gap = (today - day).days
    if gap == 0:
        return "d’aujourd’hui"
    if gap == 1:
        return "d’hier"
    return f"de {JOURS_L[day.weekday()]}" if 1 < gap < 7 else f"du {d_long(day)}"


def debrief(state: co.State, waiting: list, d: date | None, runs_by_id: dict) -> dict | None:
    """The calendar's first word: the session done that waits for its ressenti — the day asked's when it has one,
    else the most recent. « Ta sortie de mardi est arrivée — c’était comment ? », the five levels in one tap."""
    row = next((r for r in waiting if r.day == d), None) or (waiting[0] if waiting else None)
    if row is None:
        return None
    run = runs_by_id.get(row.activity_id) if row.activity_id else None
    when = day_word(row.day, state.clock.today)
    title = f"Ta sortie {when} est arrivée" if run else f"Ta séance {when} est faite"
    what = cp.KINDS.get(row.kind, row.kind) + ("" if row.kind == cp.RACE else f" {hm(row.minutes)}")
    return {"id": row.id, "day": row.day.isoformat(), "title": rp.fr(title) or title, "ask": "C’était comment ?",
            "line": " · ".join(x for x in (what, _activity(run, link=False)["line"] if run else None) if x),
            "cls": KIND_CLASS.get(row.kind, "easy"), "more": len(waiting) - 1}


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


def week_note(state: co.State, monday: date, coach: bool = False) -> dict:
    """The week's phase, whether it is the lighter 4th one, and the one line an adaptation gives it (a week the form
    lightened says why to the athlete only)."""
    entry = (state.plan.adaptations or {}).get(monday.isoformat()) or {}
    phase = cp.phase_of(monday, state.inputs) if monday >= state.plan.start_monday else None
    week = next((x for x in state.weeks if x.monday == monday), None)
    reason = entry.get("reason") or ""
    return {"phase": cp.PHASES.get(phase) if phase and phase != "apres" else None,
            "light": bool(week and week.light and not reason),
            "reason": COACH_WEEK if coach and reason == cp.FORM else cp.REASONS.get(reason)}


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


def _attach(run, live: list, today: date) -> dict | None:
    """A run outside the plan may be a session of the week done on another day: one missed earlier this week
    first, else one still planned; among them the closest duration — « C’est ma séance de mardi »."""
    week = cp.monday_of(run.day)
    if week != cp.monday_of(today):
        return None
    left = [r for r in live if co.movable(r, today) and r.day != run.day and cp.monday_of(r.day) == week
            and r.activity_id is None]
    if not left:
        return None
    row = min(left, key=lambda r: (r.status != co.MISSED, abs(r.minutes - run.minutes), r.day))
    return {"id": row.id, "day": run.day.isoformat(),
            "label": f"C’est ma séance de {JOURS_L[row.day.weekday()]}",
            "what": f"{cp.KINDS.get(row.kind, row.kind)} {hm(row.minutes)}"}


def page(state: co.State, *, d: date | None = None, w: date | None = None, month: bool = False,
         move: int | None = None, coach: bool = False, hr_reference: float | None = None,
         names: dict | None = None) -> dict:
    """Everything the swapped part draws: the calendar (`cal`: its weeks, each with its line and its seven days),
    the day opened under it, the session waiting for a debrief (`debrief`; `waiting`: how many do — what a
    notification may count), the plan's chart. `names`: the first names a change or a comment is signed with (see
    _session). A coach is never asked for a ressenti and sees no « à débriefer » mark."""
    today = state.clock.today
    selected, mondays, start = window(state, d, w, month)
    first, last = bounds(state)
    live = [r for r in state.rows if r.status != co.CANCELLED]
    by_day: dict[date, list] = {}
    for r in live:
        by_day.setdefault(r.day, []).append(r)
    extras = co.hors_plan(state.runs, state.rows)
    extra_days: dict[date, float] = {}
    for run in extras:
        extra_days[run.day] = extra_days.get(run.day, 0.0) + max(run.minutes, 1)
    done = co.done_by_week(state.runs, state.rows)
    rows_hr = hr_rows(hr_reference)
    runs_by_id = {run.id: run for run in state.runs}
    moving = next((r for r in live if r.id == move and co.movable(r, today)), None) if move else None
    waiting = [] if coach else co.awaiting_debrief(live, today)
    waiting_ids = frozenset(r.id for r in waiting)
    ask = debrief(state, waiting, d, runs_by_id)
    shown = [m + timedelta(days=i) for m in mondays for i in range(7)]
    # the blocks: each as tall as its session is long, against the longest shown (the race fills its cell)
    scale = max([BAR_SCALE_MIN] + [r.minutes for day in shown for r in by_day.get(day, []) if r.kind != cp.RACE]
                + [extra_days.get(day, 0) for day in shown])
    hard = frozenset(r.day for r in live if moving is not None and r is not moving and r.kind in cp.HARD
                     and r.status in (co.PLANNED, co.DONE))
    pairs = hard_neighbours(live, today)
    weeks = []
    for m in mondays:
        days = [{**_cell(day, by_day.get(day, []), extra_days.get(day, 0), state, selected, scale,
                         waiting=waiting_ids, moving=moving, free=frozenset(extra_days), hard=hard),
                 "out": month and day.month != start.month} for day in (m + timedelta(days=i) for i in range(7))]
        said = [rp.fr(HARD_PAIR.format(a=JOURS_L[x.weekday()].capitalize(), b=JOURS_L[y.weekday()])) for x, y in pairs
                if cp.monday_of(x) == m]
        weeks.append({"monday": m.isoformat(), "name": week_name(m, today), "current": m == cp.monday_of(today),
                      "summary": week_summary(state, m, done), **week_note(state, m, coach),
                      "moves": week_moves(state, m, coach), "hard": " ".join(x for x in said if x) or None,
                      "days": days})
    if month:
        end = start.replace(day=calendar.monthrange(start.year, start.month)[1])
        before, after = start - timedelta(days=1), end + timedelta(days=1)
        nav = {"label": f"{MOIS_L[start.month - 1]} {start.year}", "anchor": start.isoformat(),
               "prev": before.replace(day=1).isoformat() if cp.monday_of(before) >= first else None,
               "next": after.isoformat() if cp.monday_of(after) <= last else None}
    else:
        nav = {"label": span_label(mondays[0], mondays[-1] + timedelta(days=6)), "anchor": mondays[0].isoformat(),
               "prev": (mondays[0] - timedelta(weeks=1)).isoformat() if mondays[0] > first else None,
               "next": (mondays[0] + timedelta(weeks=1)).isoformat() if mondays[0] < last else None}
    # a day without a session, today or later: the next session to come is named (and one tap away)
    coming = next((r for r in live if r.day > selected and r.status == co.PLANNED), None) \
        if selected >= today and not by_day.get(selected) else None
    sessions = [_session(r, state, rows_hr, runs_by_id, coach, names) for r in by_day.get(selected, [])]
    for one in sessions:  # the ressenti of the session asked about at the top is said there, once
        one["asked"] = ask is not None and one["id"] == ask["id"]
    runs_out: list[dict] = []
    for run in extras:
        if run.day == selected:
            runs_out.append({**_activity(run, link=not coach),
                             "attach": None if coach or runs_out else _attach(run, live, today)})
    return {
        "today": today.isoformat(), "selected": selected.isoformat(), "view": "mois" if month else "semaine",
        "is_today": selected == today,
        "day_label": ("Aujourd’hui · " if selected == today else "") + viz.d_long(selected),
        "cal": {**nav, "heads": JOURS, "weeks": weeks,
                "has_today": mondays[0] <= today <= mondays[-1] + timedelta(days=6)},
        "debrief": ask, "waiting": len(waiting),
        "next": {"iso": coming.day.isoformat(), "title": cp.KINDS.get(coming.kind, coming.kind),
                 "duration": "" if coming.kind == cp.RACE else hm(coming.minutes),
                 "cls": KIND_CLASS.get(coming.kind, "easy"),
                 "when": "demain" if coming.day == today + timedelta(days=1) else viz.d_long(coming.day)}
        if coming else None,
        "sessions": sessions, "extras": runs_out,
        "moving": {"id": moving.id, "title": cp.KINDS.get(moving.kind, moving.kind),
                   "what": f"{cp.KINDS.get(moving.kind, moving.kind)} {hm(moving.minutes)}",
                   "hard": moving.kind in cp.HARD} if moving else None,
        "can_add": coach and selected >= today,
        "ressenti": list(enumerate(cp.RESSENTI, start=1)),
        "kinds": [(k, cp.KINDS[k]) for k in cp.EDITABLE],
        "chart": chart(state, done),
    }


FIRST_EMPTY = "Aucune séance à placer cette semaine-là."


def first_week(days: tuple[int, ...], long_day: int, base: int, goal: cp.Goal, today: date) -> dict:
    """The onboarding's preview: the first complete week of the plan these answers would make — this week on a
    Monday, the next one otherwise (the only week left before a race this week) — drawn day by day, and the first
    session to come. The sessions are the generator's own (coaching_plan.build, the call sync makes for a plan
    saved now); nothing is read or written."""
    inp = cp.Inputs(tuple(days), long_day, base, cp.monday_of(today), goal, {})
    weeks = cp.build(inp, today)
    ahead = [s for w in weeks for s in w.sessions if s.day >= today]
    week = weeks[0] if weeks and (today.weekday() == 0 or len(weeks) == 1) else weeks[1] if len(weeks) > 1 else None
    monday = week.monday if week else cp.monday_of(today) + timedelta(weeks=0 if today.weekday() == 0 else 1)
    shown = [s for s in week.sessions if s.day >= today] if week else []
    scale = max([BAR_SCALE_MIN] + [s.minutes for s in shown if s.kind != cp.RACE])
    cols, said, kinds = [], [], []
    for i in range(7):
        day = monday + timedelta(days=i)
        bars = []
        for s in shown:
            if s.day != day:
                continue
            cls = KIND_CLASS.get(s.kind, "easy")
            bars.append({"cls": cls, "h": 100 if s.kind == cp.RACE else bar_height(s.minutes, scale),
                         "duration": "" if s.kind == cp.RACE else hm(s.minutes).replace(" min", NNBSP + "min")})
            said.append(f"{JOURS_L[i]} : {cp.KINDS.get(s.kind, s.kind).lower()}"
                        + ("" if s.kind == cp.RACE else f" {hm_long(s.minutes)}"))
            if cls not in kinds:
                kinds.append(cls)
        cols.append({"wd": JOURS[i], "bars": bars})
    first = ahead[0] if ahead else None
    if first is not None:
        when = ("aujourd’hui" if first.day == today else "demain" if first.day == today + timedelta(days=1)
                else viz.d_long(first.day))
        what = cp.KINDS.get(first.kind, first.kind) + ("" if first.kind == cp.RACE else f" {hm(first.minutes)}")
    total = sum(s.minutes for s in shown if s.kind != cp.RACE)
    # a day chosen that holds no session that week (a volume too short for all its days, a race week) is said
    idle = [JOURS_L[d] for d in sorted(days) if shown and not any(s.day.weekday() == d for s in shown)
            and monday + timedelta(days=d) >= today]
    note = (" et ".join([", ".join(idle[:-1]), idle[-1]] if len(idle) > 1 else idle)
            + (" restent libres" if len(idle) > 1 else " reste libre") + " cette semaine-là.") if idle else None
    return {"label": week_label(monday), "cols": cols, "empty": FIRST_EMPTY if not shown else None,
            "note": note[:1].upper() + note[1:] if note else None,
            "legend": [(cls, word) for cls, word in LEGEND if cls in kinds],
            "aria": f"Semaine du {week_label(monday)}, {len(shown)} séance{'s' if len(shown) > 1 else ''}"
                    + (f", {hm_long(total)}" if total else "") + (" : " + " ; ".join(said) if said else "") + ".",
            "first": {"when": when, "what": what} if first is not None else None}


def header(state: co.State, coach: bool = False) -> dict:
    """The goal and its countdown, the time the plan runs on (once), the phase, and the line of a lost race."""
    today = state.clock.today
    monday = cp.monday_of(today)
    # a plan that lost its race starts again next Monday: this week keeps no phase
    phase = cp.phase_of(monday, state.inputs) if monday >= state.plan.start_monday else "apres"
    return {"goal": countdown(state.goal, today), "time": goal_time(state.goal, coach),
            "phase": cp.PHASES.get(phase) if phase != "apres" else None, "ended": ended_line(state)}


def athlete_card(state: co.State, user) -> dict:
    """The coach's list: one athlete at a glance — this week's bar, the last sessions with status and ressenti."""
    today = state.clock.today
    done = co.done_by_week(state.runs, state.rows)
    recent = [r for r in state.rows if r.status in (co.DONE, co.MISSED) and r.day <= today][-5:]
    return {"id": user.id, "name": user.firstname or "Athlète", **header(state, coach=True),
            "summary": week_summary(state, cp.monday_of(today), done),
            "recent": [{"day": viz.d_short(r.day), "kind": KIND_SHORT.get(r.kind, r.kind),
                        "cls": KIND_CLASS.get(r.kind, "easy"), "status": r.status,
                        "word": STATUS_WORD.get(r.status, ""),
                        "ressenti": cp.RESSENTI[r.ressenti - 1] if r.ressenti else None,
                        "level": r.ressenti} for r in reversed(recent)]}
