"""Coaching: what the pages draw, from a plan in sync (coaching.State). Every string is made here, each number once:
a week's « fait / prévu » under its title, a session's duration on its ticket, the activity's own figures on its line.

The page (docs/coaching.md › « Les écrans »): the road to the race — every week of the plan a bar as tall as its
volume, tinted by its phase —, the session done that waits for its ressenti, the week shown as a board (each day a
column, each session a tile as tall as it is long, coloured by its kind, shaped by its status, the runs done outside
the plan in grey), the next week as a compact row, and the day chosen as a ticket.
"""
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
BAR_SCALE_MIN = 60  # the start screen's week: the tallest bar is the longest session shown, 1 h at least
BAR_MIN = 22  # % of a column's room: a short session still shows
WINDOW_WEEKS = 2  # the board's week, and the next one as a compact row
# a session's tile on the board, in % of its day's slot: as tall as it is long on one scale for every week (30 min
# and less: TILE_MIN; 3 h 30 and more: TILE_MIN + TILE_SPAN); the race fills its slot
TILE_MIN, TILE_SPAN, TILE_FROM, TILE_RANGE = 28, 67, 30, 180
ROAD_WEEKS = 30  # the road: 30 weeks at most (its bars stay a finger wide at 320 px)
EXTRA_WORD = "Sortie"  # a run outside the plan, as a day's block reads
WEEK_NAMES = {0: "Cette semaine", 1: "Semaine prochaine", -1: "Semaine dernière"}
HARD_PAIR = "{a} et {b} : deux jours durs de suite."
UNDO = {co.DONE: "Annuler « fait »", co.MISSED: "Annuler « pas faite »"}
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
# the start screen's three intents (docs/coaching.md › « Les profils »): a word, an icon, and the one line each says
INTENT_CARDS = (
    (cp.ENTRETIEN, "Garder la forme", "heart",
     "On garde ton rythme : le même volume chaque semaine, et une séance un peu rythmée."),
    (cp.PROGRESSER, "Progresser", "chart",
     "Ton volume monte peu à peu, une semaine sur quatre plus légère, avec une séance de qualité par semaine."),
    (cp.COURSE, "Préparer une course", "flag",
     "Le plan mène à ta course : il monte, puis s’allège les deux dernières semaines."),
)
# « pour finir » or « pour performer »: read from the race (a time aimed at, or none), never asked
AIM_LINE = {cp.FINIR: "Sans temps visé, une seule séance de qualité par semaine : le reste en aisance.",
            cp.PERFORMER: "Avec un temps visé : jusqu’à deux séances de qualité par semaine, à partir de 5 jours "
                          "et 4 h."}
# the level read from the history (never asked), said to the athlete in one line with its reason — never to the
# person who follows the plan (they read the plan itself)
LEVEL_LEAD = {cp.DEBUTANT: "Tu débutes", cp.REGULIER: "Tu cours régulièrement", cp.ENTRAINE: "Tu t’entraînes beaucoup"}
NOVICE_RULE = ("huit semaines en aisance d’abord, quatre jours de course au plus.",
               "une séance de qualité par semaine au plus, courte, et quatre jours de course au plus.")
# two days a week (docs/coaching.md › « Deux jours par semaine »): what the plan honestly says, at creation and in
# « Comment ça marche »
TWO_DAYS = ("Deux séances par semaine suffisent pour préparer un 10 km et progresser quand on court depuis peu.",
            "Pour aller plus loin, c’est une troisième séance, facile, qui aide le plus.")
SIDE_BY_SIDE = "Deux jours de suite : la séance rythmée devient un footing."
# the fixed session (docs/coaching.md › « La séance fixe »): what the start screen says once, the race week's line,
# and the word its day carries in the calendar (« Club » for the name given by default, else its first word)
FIXED_LINE = "{name} est ta séance de qualité de la semaine : le plan écrit le reste autour."
FIXED_RACE_WEEK = "Semaine de la course : ta séance fixe laisse la place au rappel du plan."
FIXED_SHORT = "Club"
QUALITY_PAIR = "Tu peux courir la séance en footing."
METHOD = (
    "Ton plan est en temps, pas en kilomètres. Il part de ton volume habituel et monte d’environ 10 % par semaine ; "
    "une semaine sur quatre est plus légère.",
    "Aucune séance ne dépasse de plus de 10 % ta plus longue sortie des 30 derniers jours : la semaine peut alors "
    "demander un peu moins que prévu.",
    "La semaine, c’est surtout du facile : au moins 80 % du temps en aisance, une sortie longue, et une séance "
    "plus soutenue dès deux séances par semaine, jamais la veille ou le lendemain d’un autre jour dur.",
    "Deux jours par semaine : une séance avec du rythme, une sortie plus longue et facile. " + TWO_DAYS[0] + " "
    + TWO_DAYS[1] + " Deux jours de suite : la séance rythmée devient un footing.",
    "Jamais deux jours durs de suite, pas de séance de moins de 30 minutes, et seulement tes jours disponibles.",
    "Avant une course : deux semaines d’affûtage, le volume baisse et quelques efforts courts restent.",
    "Garder la forme : le même volume chaque semaine (75 minutes au moins), sans semaine allégée, une séance "
    "rythmée courte et une sortie longue d’1 h 30 au plus. Progresser, ou une course sans temps visé : une seule "
    "séance de qualité par semaine ; deux, au plus, avec un temps visé.",
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


def short_title(kind: str, structure: dict | None) -> str:
    """The one word a day of the calendar carries: the kind's, or the fixed session's (« Club »; a name typed: its
    first word)."""
    if cp.is_fixed(structure):
        name = cp.title(kind, structure)
        return FIXED_SHORT if name == cp.FIXED_NAME else name.split()[0]
    return KIND_SHORT.get(kind, kind)


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


def countdown(goal: cp.Goal, today: date, intent: str | None = None) -> str:
    if goal.race_day is None:
        return "Garder la forme" if intent == cp.ENTRETIEN else "Progresser, sans course"
    k = (goal.race_day - today).days
    if k < 0:
        return f"Course passée · {goal.name}"
    return f"{rp.j_label(-k)} · {goal.name}"


def road_head(goal: cp.Goal, today: date, intent: str | None = None) -> tuple[str, str | None]:
    """The road's heading: (« J‑54 », « Trail des Crêtes ») before a race; its intent's words without one
    (« Progresser », « sans course »)."""
    if goal.race_day is None:
        return ("Garder la forme", None) if intent == cp.ENTRETIEN else ("Progresser", "sans course")
    k = (goal.race_day - today).days
    return ("Course passée" if k < 0 else rp.j_label(-k)), goal.name


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
    if (state.agenda is None or row.status != co.PLANNED or row.day < state.clock.today or row.kind == cp.RACE
            or cp.is_fixed(row.structure)):  # the club's hour is the club's
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
    # the ticket's large duration: the time run once done (the run's, else the one ticked by hand), with the time
    # planned beside it when they differ; the race's time is the road's (« Objectif 5h00 »): printed once
    did = (run.minutes if run is not None else row.done_minutes) if row.status == co.DONE else None
    shown = int(round(did)) if did else row.minutes
    timed_race = row.kind == cp.RACE and goal_time(state.goal) is not None
    return {
        "id": row.id, "kind": row.kind, "title": cp.title(row.kind, row.structure),
        # the race's time is the header's (« Objectif 6h30 »): printed once
        "duration": "" if timed_race else hm(row.minutes),
        "big": None if timed_race else big_duration(shown),
        "planned_said": f"prévu {hm(row.minutes)}" if did and shown != row.minutes else None,
        "minutes": row.minutes, "cls": KIND_CLASS.get(row.kind, "easy"), "status": row.status,
        "text": text[:1].upper() + text[1:], "profile": None if steady else profile,
        "gauge": {"level": level, "aria": f"Effort {cp.LEVELS.get(level, '')} : niveau {level} sur 4"} if steady
        else None,
        "guidance": feel_and_range(row.kind, row.structure, rows_hr),
        "edited": {"coach": f"Modifiée par {follower or FOLLOWER}", "athlete": "Modifiée par toi"}.get(row.origin)
        if not coach else {"coach": "Modifiée par toi", "athlete": f"Modifiée par {athlete}"}.get(row.origin),
        "comment_by": follower or COMMENT,
        "activity": _activity(run, link=not coach, climb=climb_of(row)) if run else None,
        # a run that does not look like the session (co.FIT): said, with the way to take it back out of the plan
        "misfit": f"{hm(int(round(run.minutes)))} au lieu de {hm(row.minutes)}"
        if run is not None and co.misfit(row, run) else None,
        "can_unlink": not coach and run is not None and row.status == co.DONE and row.kind != cp.RACE,
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
    """A session's bar on the start screen's week, in % of its column: its duration against the longest shown."""
    return int(min(100, max(BAR_MIN, round(100 * minutes / max(scale, 1)))))


def tile_height(minutes: float) -> int:
    """A session's tile on the board, in % of its day's slot: one scale for every week, so that a long run looks long
    whatever the week around it (30 min: TILE_MIN; 3 h 30 and more: TILE_MIN + TILE_SPAN)."""
    share = min(1.0, max(0.0, (minutes - TILE_FROM) / TILE_RANGE))
    return int(round(TILE_MIN + TILE_SPAN * share))


def tile_label(minutes: float) -> str:
    """What a tile carries, short: « 40′ », « 1h55 », « 2h »."""
    m = int(round(minutes))
    if m < 60:
        return f"{m}′"
    return f"{m // 60}h{m % 60:02d}" if m % 60 else f"{m // 60}h"


def big_duration(minutes: float) -> dict:
    """The ticket's large duration, its unit smaller: {"h": "1", "m": "05"} or {"h": None, "m": "40"}."""
    m = int(round(minutes))
    return {"h": str(m // 60), "m": f"{m % 60:02d}" if m % 60 else ""} if m >= 60 else {"h": None, "m": str(m)}


def hard_neighbours(rows: list, today: date) -> list[tuple[date, date]]:
    """The pairs of days in a row, from today on, that each hold a hard session still to do (coaching_plan.HARD).
    The generator never makes one; a person moving a session may: the calendar says so, and lets them."""
    days = sorted({r.day for r in rows if r.status == co.PLANNED and r.kind in cp.HARD and r.day >= today})
    return [(x, y) for x, y in zip(days, days[1:], strict=False) if (y - x).days == 1]


def quality_in(rows: list, days: tuple[date, date]) -> bool:
    """Whether a quality session still to do is one of the two days side by side (« Tu peux courir la séance en
    footing. »: a suggestion — what a person moved is never rewritten)."""
    return any(r.day in days and r.kind in (cp.QUALITY, cp.HILLS) and r.status == co.PLANNED for r in rows)


def hard_days(live: list, moving) -> frozenset[date]:
    """The days that hold a hard session other than the one being moved (planned or done): its neighbours there
    would make two hard days side by side."""
    return frozenset(r.day for r in live if r is not moving and r.kind in cp.HARD and r.status in (co.PLANNED, co.DONE))


def drop(day: date, moving, today: date, free: frozenset, hard: frozenset) -> tuple[bool, bool]:
    """Whether `day` takes the session being moved (services/coaching.move: from today on, or an earlier day of
    this week that holds a run no session took), and whether it would sit next to another hard session — a
    warning on the day, never a refusal."""
    this_week = cp.monday_of(day) == cp.monday_of(today)
    target = day != moving.day and (day >= today or (day in free and this_week))
    return target, bool(target and moving.kind in cp.HARD and any(day + timedelta(days=k) in hard for k in (-1, 1)))


def drops(row, shown: list[date], live: list, today: date, free: frozenset) -> dict:
    """What the calendar needs to let a session be picked up and dropped on a day by hand (coaching-calendar.js):
    the days shown that take it, those of them that would put two hard days side by side — the same answers as
    « Déplacer » (`drop`) — and the words its block carries while it is held."""
    hard = hard_days(live, row)
    said = [(day, *drop(day, row, today, free, hard)) for day in shown]
    return {"to": " ".join(d.isoformat() for d, ok, _ in said if ok),
            "warn": " ".join(d.isoformat() for d, _, warn in said if warn),
            "what": f"{short_title(row.kind, row.structure)} {hm(row.minutes)}"}


def _cell(day: date, rows: list, extra: float, state: co.State, selected: date, *,
          waiting: frozenset = frozenset(), moving=None, free: frozenset = frozenset(), hard: frozenset = frozenset(),
          by_hand: dict | None = None, run_by_hand: bool = False) -> dict:
    """One day of the board: its sessions as tiles (kind, status, height, the short duration they carry), the run done
    outside the plan as a grey one, one word under them, and what a screen reader says of it all (the time of day
    the agenda gave is said there and on the ticket; the column stays clean). `extra`: the minutes run outside the
    plan; `waiting`: the ids of the sessions that wait for a debrief; `moving`: the session being moved (the day then
    says whether it can take it, and whether that would put two hard days side by side — `hard`: the days that hold
    another hard session to do). `by_hand`: {session id: drops(...)} for the sessions that can be picked up and
    dropped on another day; `run_by_hand`: the grey tile may be dropped on a session of the week (« C’est ma séance
    de … », by hand)."""
    today = state.clock.today
    by_hand = by_hand or {}
    said, blocks = [], []
    for r in rows:
        slot = clock_time(r.slot_min) if r.status == co.PLANNED and r.slot_min is not None else None
        said.append(f"{cp.title(r.kind, r.structure)} {hm_long(r.minutes)}, {STATUS_WORD.get(r.status, '')}"
                    + (f" à {slot}" if slot else "") + (", à débriefer" if r.id in waiting else ""))
        minutes = r.done_minutes if r.status == co.DONE and r.done_minutes else r.minutes
        blocks.append({"cls": KIND_CLASS.get(r.kind, "easy"), "status": r.status, "race": r.kind == cp.RACE,
                       "h": 100 if r.kind == cp.RACE else tile_height(minutes), "label": tile_label(minutes),
                       "id": r.id, "hand": by_hand.get(r.id) if by_hand.get(r.id, {}).get("to") else None})
    if extra:
        said.append("une sortie hors plan")
        blocks.append({"cls": "extra", "status": co.DONE, "race": False, "h": tile_height(extra),
                       "label": tile_label(extra), "run": day.isoformat() if run_by_hand else None, "what": EXTRA_WORD})
    if len(blocks) > 1:  # several the same day share the slot
        for block in blocks:
            block["h"] = max(TILE_MIN // 2, block["h"] // len(blocks))
    word = short_title(rows[0].kind, rows[0].structure) if rows else (EXTRA_WORD if extra else "")
    out = {"iso": day.isoformat(), "wd": JOURS[day.weekday()], "num": day.day, "today": day == today,
           "selected": day == selected, "past": day < today, "blocks": blocks, "extra": bool(extra),
           "word": word + (f" +{len(rows) - 1}" if len(rows) > 1 else ""),
           "todo": any(r.id in waiting for r in rows),
           "aria": f"{JOURS_L[day.weekday()]} {day.day} {MOIS_L[day.month - 1]} : " + (" ; ".join(said) or "repos")}
    if moving is not None:
        out["target"], out["warn"] = drop(day, moving, today, free, hard)
    return out


def bounds(state: co.State) -> tuple[date, date]:
    """The first and last Mondays the calendar shows."""
    days = [r.day for r in state.rows if r.status != co.CANCELLED]
    m0 = cp.monday_of(state.clock.today)
    first = min([state.plan.start_monday, m0] + [cp.monday_of(d) for d in days])
    last = max([m0] + [cp.monday_of(d) for d in days])
    return first, last


def window(state: co.State, d: date | None, w: date | None) -> tuple[date, list[date]]:
    """(the day opened, the Mondays of the board's week and of the next one).
    - The board shows `w`'s week; without `w`, this week — the week of `d` whenever the day asked is not in it (a
      link to a day, a tile of the next week, a session dropped there: the board follows the day).
    - The day: the one asked; else today when it is on the board, else the board's first session, else its Monday.
      The page opens on today, a rest day too (it names the next session)."""
    today = state.clock.today
    first, last = bounds(state)
    lo, hi = first, last + timedelta(days=6)
    if d is not None:
        d = min(max(d, lo), hi)
    start = cp.monday_of(w) if w is not None else cp.monday_of(today)
    if d is not None and not start <= d < start + timedelta(weeks=1):
        start = cp.monday_of(d)
    start = min(max(start, first), last)
    mondays = [start + timedelta(weeks=i) for i in range(WINDOW_WEEKS)]
    end = start + timedelta(days=6)
    if d is not None:
        return d, mondays
    if start <= today <= end:
        return today, mondays
    inside = sorted(r.day for r in state.rows if r.status != co.CANCELLED and start <= r.day <= end)
    return (inside[0] if inside else start), mondays


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
    what = cp.title(row.kind, row.structure) + ("" if row.kind == cp.RACE else f" {hm(row.minutes)}")
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
                "line": f"{hm(planned)} prévues" if planned else "",
                "aria": f"{hm_long(planned)} prévues"}
    fill = round(min(1.0, did / planned), 3) if planned else (1.0 if did else 0)
    return {"planned": planned, "done": did, "fill": fill,
            "label": f"{hm(did)} / {hm(planned)}" if planned else (hm(did) if did else ""),
            "line": f"{hm(did)} sur {hm(planned)}" if planned else (f"{hm(did)} faites" if did else ""),
            "aria": f"{hm_long(did)} faites sur {hm_long(planned)} prévues" if planned else
            (f"{hm_long(did)} faites, rien de prévu" if did else "Rien de prévu")}


def week_note(state: co.State, monday: date, coach: bool = False) -> dict:
    """The week's phase, whether it is the lighter 4th one, the one line an adaptation gives it (a week the form
    lightened says why to the athlete only), and — a plan with a fixed session — the race week's line."""
    entry = (state.plan.adaptations or {}).get(monday.isoformat()) or {}
    phase = cp.phase_of(monday, state.inputs) if monday >= state.plan.start_monday else None
    week = next((x for x in state.weeks if x.monday == monday), None)
    reason = entry.get("reason") or ""
    rd = state.goal.race_day
    return {"phase": cp.PHASES.get(phase) if phase and phase != "apres" else None,
            "light": bool(week and week.light and not reason),
            "reason": COACH_WEEK if coach and reason == cp.FORM else cp.REASONS.get(reason),
            "fixed": rp.fr(FIXED_RACE_WEEK) if state.inputs.fixed is not None and rd is not None
            and cp.monday_of(rd) == monday else None}


def road(state: co.State, shown: date, coach: bool = False) -> dict | None:
    """The road to the race: every week of the plan a bar as tall as the time it plans (against the plan's biggest
    week), tinted by its phase; the current week, the week the board shows, the race's week (its bar full, a flag on
    it). Each bar says its dates, time and phase (`label`: what the road reads while a finger scrubs, `aria`) and is
    the week's address. ROAD_WEEKS at most: the plan's first weeks give way first, never the current one."""
    first, last = bounds(state)
    last = max([last] + [w.monday for w in state.weeks])
    m0 = cp.monday_of(state.clock.today)
    n = (last - first).days // 7 + 1
    if n > ROAD_WEEKS:
        first = min(m0, last - timedelta(weeks=ROAD_WEEKS - 1))
        last = first + timedelta(weeks=ROAD_WEEKS - 1)
        n = ROAD_WEEKS
    mondays = [first + timedelta(weeks=i) for i in range(n)]
    planned: dict[date, int] = {}
    for r in state.rows:
        if r.status != co.CANCELLED and r.kind != cp.RACE:
            planned[cp.monday_of(r.day)] = planned.get(cp.monday_of(r.day), 0) + r.minutes
    if not any(planned.get(m) for m in mondays):
        return None
    peak = max(planned.get(m, 0) for m in mondays)
    race_day = state.goal.race_day
    race_week = cp.monday_of(race_day) if race_day else None
    start = state.plan.start_monday
    bars, runs = [], []
    for m in mondays:
        key = cp.phase_of(m, state.inputs) if m >= start else ""
        word = cp.PHASES.get(key, "") if key != "apres" else ""
        minutes = planned.get(m, 0)
        race = m == race_week
        said = [week_label(m), f"{hm(minutes)} prévues" if minutes else "rien de prévu"] + ([word] if word else [])
        bars.append({"monday": m.isoformat(), "phase": key if word else "none", "race": race,
                     "h": 100 if race else (round(22 + 78 * minutes / peak) if minutes else 6),
                     "current": m == m0, "shown": m == shown, "label": " · ".join(said),
                     "aria": ("Semaine de la course, " if race else "Semaine en cours, " if m == m0 else "")
                     + ", ".join(said)})
        if runs and runs[-1]["key"] == bars[-1]["phase"]:
            runs[-1]["weeks"] += 1
        else:
            runs.append({"key": bars[-1]["phase"], "label": word, "weeks": 1})
    total = (last - start).days // 7 + 1 if last >= start else 0
    index = (shown - start).days // 7 + 1
    return {"bars": bars, "phases": runs, "race": race_week in mondays,
            "tip": f"Semaine {index} sur {total}" if 1 <= index <= total else "Avant le plan",
            "aria": f"Le plan, {n} semaine{'s' if n > 1 else ''}" + (" jusqu’à la course" if race_week else "")}


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
            "what": f"{cp.title(row.kind, row.structure)} {hm(row.minutes)}"}


def day_word_title(day: date, today: date) -> str:
    """The ticket's title: « Aujourd’hui », « Hier », « Demain », else the weekday (« Samedi »)."""
    gap = (day - today).days
    return {0: "Aujourd’hui", -1: "Hier", 1: "Demain"}.get(gap) or JOURS_L[day.weekday()].capitalize()


def page(state: co.State, *, d: date | None = None, w: date | None = None, move: int | None = None,
         coach: bool = False, hr_reference: float | None = None, names: dict | None = None) -> dict:
    """Everything the swapped part draws: the road and its heading (`road`, `head`), the board (`cal`: the week shown
    with its line and its seven days, then the next week as a compact row), the day opened as tickets, the session
    waiting for a debrief (`debrief`; `waiting`: how many do — what a notification may count). `names`: the first
    names a change or a comment is signed with (see _session). A coach is never asked for a ressenti and sees no
    « à débriefer » mark."""
    today = state.clock.today
    selected, mondays = window(state, d, w)
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
    # the next week's row only while the plan has one
    mondays = [m for m in mondays if m <= last] or mondays[:1]
    shown = [m + timedelta(days=i) for m in mondays for i in range(7)]
    hard = hard_days(live, moving) if moving is not None else frozenset()
    free = frozenset(extra_days)
    # by hand (the board's own gestures, coaching-calendar.js): what « Déplacer » takes, the same rights for the
    # person who follows the plan; none while « Déplacer » has turned the days into targets
    by_hand = {} if moving is not None else {
        r.id: drops(r, shown, live, today, free) for day in shown for r in by_day.get(day, []) if co.movable(r, today)}
    pairs = hard_neighbours(live, today)
    weeks = []
    for m in mondays:
        days = [_cell(day, by_day.get(day, []), extra_days.get(day, 0), state, selected, waiting=waiting_ids,
                      moving=moving, free=free, hard=hard, by_hand=by_hand, run_by_hand=not coach and moving is None)
                for day in (m + timedelta(days=i) for i in range(7))]
        said = [rp.fr(HARD_PAIR.format(a=JOURS_L[x.weekday()].capitalize(), b=JOURS_L[y.weekday()])
                      + (" " + QUALITY_PAIR if quality_in(live, (x, y)) and not coach else ""))
                for x, y in pairs if cp.monday_of(x) == m]
        weeks.append({"monday": m.isoformat(), "name": week_name(m, today), "current": m == cp.monday_of(today),
                      "summary": week_summary(state, m, done), **week_note(state, m, coach),
                      "moves": week_moves(state, m, coach), "hard": " ".join(x for x in said if x) or None,
                      "days": days})
    if len(weeks) > 1 and weeks[1]["name"] not in WEEK_NAMES.values():
        weeks[1]["name"] = "Semaine suivante"
    board = mondays[0]
    nav = {"anchor": board.isoformat(),
           "prev": (board - timedelta(weeks=1)).isoformat() if board > first else None,
           "next": (board + timedelta(weeks=1)).isoformat() if board < last else None}
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
        "today": today.isoformat(), "selected": selected.isoformat(), "is_today": selected == today,
        "day_title": day_word_title(selected, today), "day_long": viz.d_long(selected),
        "head": header(state, coach), "road": road(state, board, coach),
        "cal": {**nav, "weeks": weeks, "by_hand": any(x["to"] for x in by_hand.values()),
                "has_today": board <= today <= board + timedelta(days=6)},
        "debrief": ask, "waiting": len(waiting),
        "next": {"iso": coming.day.isoformat(), "title": cp.title(coming.kind, coming.structure),
                 "duration": "" if coming.kind == cp.RACE else hm(coming.minutes),
                 "cls": KIND_CLASS.get(coming.kind, "easy"),
                 "when": "demain" if coming.day == today + timedelta(days=1) else viz.d_long(coming.day)}
        if coming else None,
        "sessions": sessions, "extras": runs_out,
        "moving": {"id": moving.id, "title": cp.title(moving.kind, moving.structure),
                   "what": f"{cp.title(moving.kind, moving.structure)} {hm(moving.minutes)}",
                   "hard": moving.kind in cp.HARD} if moving else None,
        "can_add": coach and selected >= today,
        "ressenti": list(enumerate(cp.RESSENTI, start=1)),
        "kinds": [(k, cp.KINDS[k]) for k in cp.EDITABLE],
    }


FIRST_EMPTY = "Aucune séance à placer cette semaine-là."


def level_line(level: cp.Level | None, week: int = 0) -> dict | None:
    """« Tu débutes : huit semaines en aisance d’abord, quatre jours de course au plus. », and why — what the level
    read (`week`: the plan's week, the easy weeks said only while they last)."""
    if level is None or level.level not in LEVEL_LEAD:
        return None
    if level.history:
        why = f"Lu sur tes {cp.LEVEL_WEEKS} dernières semaines : {level.weeks} avec une sortie"
        if level.level == cp.ENTRAINE:
            why += f", {level.busy} avec 3 sorties ou plus"
        why += f", {hm(level.median)} par semaine." if level.median else "."
    else:
        why = f"Aucune sortie enregistrée\u202f; lu sur ton volume, {hm(level.base)} par semaine."
    if level.level == cp.DEBUTANT:
        text = NOVICE_RULE[0 if week < cp.NOVICE_EASY_WEEKS else 1]
    else:
        text = why[:1].lower() + why[1:]
        why = ""
    return {"lead": LEVEL_LEAD[level.level], "text": rp.fr(text) or text, "why": rp.fr(why) or why,
            "said": f"{LEVEL_LEAD[level.level]} : {text}" + (f" {why}" if why else "")}


def side_by_side(inp: cp.Inputs) -> bool:
    """Two days only, and they are neighbours (Sunday and Monday too): the plan writes no session with rhythm — a
    fixed session stays where the athlete put it (co.fixed_warning says it instead)."""
    days = cp.days_used(inp)
    return (inp.intent is not None and len(days) == 2 and cp.circular(*days) == 1 and not cp.novice(inp)
            and inp.fixed is None)


def short_race(inp: cp.Inputs, weeks: list) -> str | None:
    """A race closer than the long run can grow (one step of 5 min a loading week: the single-session rule): said at
    creation — « Ta course arrive vite : … »."""
    if inp.intent is None or inp.goal.race_day is None or not inp.goal.race_minutes:
        return None
    longest = max((s.minutes for w in weeks if w.phase not in ("affutage", "apres") for s in w.sessions
                   if s.kind == cp.LONG), default=0)
    race = int(round(inp.goal.race_minutes))
    if longest >= race - cp.STEP:
        return None
    if not longest:
        return f"Ta course arrive vite : pas de sortie longue avant elle, seulement l’affûtage ({hm(race)} de course)."
    return (f"Ta course arrive vite : ta plus longue sortie atteindra {hm(longest)}, moins que le temps de ta course "
            f"({hm(race)}).")


def profile_lines(inp: cp.Inputs, weeks: list | None = None) -> list[str]:
    """What the start screen says of the rules these answers bring, one short line each: the fixed session, and next
    to the long run when it is; a race « pour finir » or « pour performer » (read from its time aimed at; a
    « débutant » always « pour finir »); two days a week, what they give and what a third would add; two days side
    by side; a race closer than the long run can grow."""
    lines = []
    if inp.fixed is not None:
        lines.append(rp.fr(FIXED_LINE.format(name=inp.fixed.name)) or FIXED_LINE)
        warning = co.fixed_warning(inp.fixed, inp.long_day)
        if warning:
            lines.append(rp.fr(warning) or warning)
    aim = cp.aim(inp.goal)
    if inp.intent == cp.COURSE and aim:
        lines.append(AIM_LINE[cp.FINIR if cp.novice(inp) else aim])
    if inp.intent is not None and len(cp.days_used(inp)) == 2:
        lines.extend(TWO_DAYS)
        if side_by_side(inp):
            lines.append(rp.fr(SIDE_BY_SIDE) or SIDE_BY_SIDE)
    short = short_race(inp, weeks or [])
    if short:
        lines.append(rp.fr(short) or short)
    return lines


def first_week(days: tuple[int, ...], long_day: int, base: int, goal: cp.Goal, today: date,
               intent: str | None = None, level: cp.Level | None = None, fixed: cp.Fixed | None = None) -> dict:
    """The onboarding's preview: the first complete week of the plan these answers would make — this week on a
    Monday, the next one otherwise (the only week left before a race this week) — drawn day by day, and the first
    session to come. The sessions are the generator's own (coaching_plan.build, the call sync makes for a plan
    saved now, with the intent's rules); nothing is read or written."""
    inp = cp.Inputs(tuple(days), long_day, base, cp.monday_of(today), goal, {}, intent=intent,
                    level=level.level if level is not None and intent is not None else None, fixed=fixed)
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
            name = cp.title(s.kind, s.structure)
            said.append(f"{JOURS_L[i]} : {name if cp.is_fixed(s.structure) else name.lower()}"
                        + ("" if s.kind == cp.RACE else f" {hm_long(s.minutes)}"))
            if cls not in kinds:
                kinds.append(cls)
        cols.append({"wd": JOURS[i], "bars": bars})
    first = ahead[0] if ahead else None
    if first is not None:
        when = ("aujourd’hui" if first.day == today else "demain" if first.day == today + timedelta(days=1)
                else viz.d_long(first.day))
        what = cp.title(first.kind, first.structure) + ("" if first.kind == cp.RACE else f" {hm(first.minutes)}")
    total = sum(s.minutes for s in shown if s.kind != cp.RACE)
    # a day chosen that holds no session that week (a volume too short for all its days, a race week) is said
    chosen = sorted(set(days) | ({fixed.day} if fixed else set()))  # the fixed session's weekday joins them
    idle = [JOURS_L[d] for d in chosen if shown and not any(s.day.weekday() == d for s in shown)
            and monday + timedelta(days=d) >= today]
    note = (" et ".join([", ".join(idle[:-1]), idle[-1]] if len(idle) > 1 else idle)
            + (" restent libres" if len(idle) > 1 else " reste libre") + " cette semaine-là.") if idle else None
    return {"label": week_label(monday), "cols": cols, "empty": FIRST_EMPTY if not shown else None,
            "note": note[:1].upper() + note[1:] if note else None,
            "legend": [(cls, word) for cls, word in LEGEND if cls in kinds],
            "aria": f"Semaine du {week_label(monday)}, {len(shown)} séance{'s' if len(shown) > 1 else ''}"
                    + (f", {hm_long(total)}" if total else "") + (" : " + " ; ".join(said) if said else "") + ".",
            "first": {"when": when, "what": what} if first is not None else None,
            "lines": profile_lines(inp, weeks),
            "level": level_line(level) if intent is not None else None}


def header(state: co.State, coach: bool = False) -> dict:
    """The goal and its countdown, the time the plan runs on (once), the phase, and the line of a lost race."""
    today = state.clock.today
    monday = cp.monday_of(today)
    # a plan that lost its race starts again next Monday: this week keeps no phase
    phase = cp.phase_of(monday, state.inputs) if monday >= state.plan.start_monday else "apres"
    week = (monday - state.plan.start_monday).days // 7
    lead, race = road_head(state.goal, today, state.inputs.intent)
    return {"goal": countdown(state.goal, today, state.inputs.intent), "lead": lead, "race": race,
            "time": goal_time(state.goal, coach),
            "level": None if coach else level_line(co.level_of(state.plan), week),
            "notes": [rp.fr(SIDE_BY_SIDE)] if not coach and side_by_side(state.inputs) else [],
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
