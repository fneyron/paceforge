"""Coaching: the plan's rules (docs/coaching.md). Pure functions, no database, no clock: the same inputs give the
same plan, and every rule is a constant here. No AI.

Every rule carries its study, or says it has none (docs/coaching.md › « Sources » gives each reference and what it
found). Three marks on the constants below:
- a study's name alone: the value is the study's;
- (H): a figure of PaceForge's — a study backs the principle and is named, or none does and the line says so;
- « choix pratique »: not a training decision (how far ahead to write, a rounding, a stable reading).

The plan is in TIME (minutes, to 5), week by week, Monday to Sunday on the athlete's clock:
- phases: Base → Spécifique (the last 6 or 8 weeks before the taper, H) → Affûtage (the weeks holding J-14 → J-1:
  race_prep's bands, J-14 → J-8 at 55–70 % of the base and J-7 → J-1 at 35–50 %, intensity kept: Bosquet 2007;
  Mujika 2003) → the race day, a « Course » session. Without a race: a rolling block of 12 weeks;
- volume: the athlete's base, +10 % a loading week (H: no study makes 10 % protective, Buist 2008; the weekly
  jumps found with more injuries are larger, Nielsen 2014, Damsted 2019), every 4th week at 75 % (H), under a
  ceiling scaled to the race (H, no study), over a floor;
- one session: never more than 10 % longer than the longest run of the 30 days before it (Frandsen 2025: the
  spike found with more overuse injuries is the single session's, not the week's);
- mix: hard time (the efforts of the quality sessions) for 20 % of the week at most (Seiler 2010; in time: Muñoz
  2014); the long run about 35 % of the week (H), capped; one quality session from 3 available days, two at most
  (Seiler & Tønnessen 2009), from 5 days and 4 h a week (H); never two hard days (quality, long run, race) in a
  row (Stanley 2013); no session under 30 min (Garber 2011); sessions on the available days only;
- a trail race: the long run and the long-hill session carry a climb to aim at, the race's D+ per hour scaled to
  the session, growing across the plan and capped (H, `climb_target`; uphill running is its own ability: Balducci
  2016; Ehrström 2018); a road race, or no race: none;
- the ressenti (`adaptation`): a lighter week, a repeated week or a restart, each kept with its reason (what the
  athlete reports follows the load better than what is measured: Saw 2016; the figures are ours, H);
- the form, for an athlete who turned the option on (docs/coaching.md › « La forme »): on a morning Santé's index
  reads low, the day's hard session becomes a short footing and moves to the next available day of the week that
  keeps « never two hard days in a row », else is dropped (hard sessions only when recovered: Kiviniemi 2007;
  Vesterinen 2016; Javaloyes 2019 — the durations, the move and the 3-mornings rule are ours, H).
tests/test_coaching_plan.py pins each value and its mark.
"""
import math
import statistics
from dataclasses import dataclass, field
from datetime import date, timedelta

from app.services import race_prep as rp

# ── kinds ───────────────────────────────────────────────────────────────────
FOOTING, LONG, QUALITY, HILLS, RECUP, RACE = "footing", "sortie_longue", "seance", "cotes", "recup", "course"
KINDS = {FOOTING: "Footing", LONG: "Sortie longue", QUALITY: "Séance", HILLS: "Côtes", RECUP: "Récup",
         RACE: "Course"}
HARD = frozenset({LONG, QUALITY, HILLS, RACE})  # a hard day: never two in a row
EASY = frozenset({FOOTING, LONG, RECUP})  # where « Dur » is a signal
EDITABLE = (FOOTING, LONG, QUALITY, HILLS, RECUP)  # what a person may turn a session into

# ── the rules' constants ────────────────────────────────────────────────────
STEP = 5  # minutes: every duration is rounded to it (choix pratique)
MIN_SESSION = 30  # no session shorter: the ACSM's 30 min a day at moderate intensity (Garber 2011)
GROWTH = 0.10  # (H) +10 % a loading week: under the jumps found with more injuries (Nielsen 2014; Damsted 2019)
SPIKE = 0.10  # no session more than 10 % longer than the longest run of the 30 days before it (Frandsen 2025)
SPIKE_DAYS = 30  # …the 30 days of that study
LIGHT_EVERY = 4  # (H) every 4th week lighter: the load varies (Foster 1998); no study fixes the cycle
LIGHT_FACTOR = 0.75  # (H) −25 % against the last loading week (no study)
WEEK_FLOOR = 60  # choix pratique: a training week never under 1 h (two sessions of MIN_SESSION)
HORIZON_WEEKS = 24  # choix pratique: weeks written at most; a race further away: base weeks first
BLOCK_WEEKS = 12  # choix pratique: without a race, 12 weeks ahead, extended as the weeks pass
SPECIFIC_WEEKS = (6, 8)  # (H) Spécifique: 6 weeks before the taper, 8 when the plan has 16 weeks or more before it
SPECIFIC_LONG_FROM = 16  # (H) …the interval studies ran 6 to 10 weeks (Barnes 2013; Seiler 2013; Esfarjani 2007)
CEILING = (150, 0.7)  # (H) weekly ceiling = 150 min + 70 % of the expected race time… (no study)
CEILING_RANGE = (180, 720)  # (H) …between 3 h and 12 h (no study)
FREE_CEILING = (1.5, 180, 480)  # (H) without a race: 1.5 × the base, between 3 h and 8 h (no study)
HARD_SHARE = 0.20  # at most 20 % of the week's time in hard efforts (Seiler 2010; counted in time: Muñoz 2014)
LONG_SHARE = 0.35  # (H) the long run: about 35 % of the week (no study fixes the share)
LONG_RACE_SHARE = 0.55  # (H) never longer than 55 % of the expected race time… (no study)
LONG_CAP_RANGE = (120, 210)  # (H) …that cap itself between 2 h and 3 h 30 (no study)
FREE_LONG_CAP = 150  # (H) without a race: 2 h 30 (no study)
NO_LONG_BEFORE_RACE = 3  # (H) no long run from J-3 on: the taper cuts the volume (Bosquet 2007), the day is ours
QUALITY_FROM_DAYS = 3  # (H) one quality session from 3 available days
TWO_QUALITY = (5, 240)  # (H) two — never more (Seiler & Tønnessen 2009) — from 5 available days and a base of 4 h
WARM_UP, COOL_DOWN = 15, 10  # (H) around the efforts: a warm-up helps (Fradkin 2010); a cool-down, not shown to
REP_EVERY = 2  # (H) one more repetition every 2 weeks of a phase (no study)
ADAPT_FACTOR = 0.8  # (H) a lighter week after the ressenti: −20 % (the principle: Saw 2016; Meeusen 2013)
SIGNAL_FROM = 4  # (H) « Dur » (4) or « Très dur » (5) on an easy session is a signal
SIGNAL_DAYS = 7  # (H) two signals within 7 days
MISSED_FROM = 2  # (H) two missed sessions in a week (no study)
RESSENTI = ("Très facile", "Facile", "Bien", "Dur", "Très dur")  # 1 … 5
START_CHOICES = (60, 120, 180, 240, 300)  # choix pratique: the 5 coarse starting volumes offered without a history
BASE_RANGE = (60, 900)  # choix pratique: what a typed starting volume is held to
BASE_WEEKS = 4  # the starting volume: the median of the last 4 complete weeks of running
RACE_TIME_STEP = 15  # choix pratique: the rules read the race time to 15 min, a minute more leaves the plan as is
CLIMB_START = 0.6  # (H) the climb aimed at starts at 60 % of the race's D+ per hour, 100 % the week before the taper
CLIMB_RATE_CAP = 500  # (H) never more than 500 m of climb per hour of session
CLIMB_MIN = 100  # (H) no climb target under 100 m
CLIMB_STEP = 50  # metres: a climb target is rounded to it (choix pratique)
CLIMB_KEYS = ("cotes_longues",)  # the quality sessions that carry one, with the long run
FORM_FOOTING_SHARE = 0.6  # (H) the footing that replaces a hard session on a low morning: 60 % of its duration…
FORM_FOOTING_CAP = 45  # (H) …and 45 min at most (never under MIN_SESSION): Kiviniemi 2007's easy session lasts 40 min
FORM_LOW_COUNT = 3  # (H) three low mornings…
FORM_LOW_DAYS = 5  # (H) …within five days: the next week is lighter (ADAPT_FACTOR, as the ressenti's)
FORM_KEEP_DAYS = 14  # the low mornings kept on the plan (their dates): what the 3-in-5 rule reads, no more
FORM_RETRY_MIN = 10  # minutes between two readings of Santé while this morning's night has not arrived
FORM = "forme"  # the adaptation's reason
LIGHTENED, MOVED = "allegee", "reportee"  # a session's `form`: lightened on a low morning; moved from one

PHASES = {"base": "Base", "specifique": "Spécifique", "affutage": "Affûtage", "progression": "Progression",
          "apres": "Après la course"}
REASONS = {
    "ressenti": "Semaine allégée : tes séances faciles t’ont paru dures.",
    "manquees": "Semaine allégée : plusieurs séances manquées la semaine dernière.",
    "semaine_manquee": "Même volume que la semaine dernière, restée sans séance.",
    "reprise": "Reprise en douceur après deux semaines sans séance.",
    FORM: "Semaine allégée : ta récupération est restée basse plusieurs matins.",
}
# the levels of intensity: by feel first (the talk test: Persinger 2004; Foster 2008), a heart-rate range when a
# maximum is known (the app's own five zones, heart_rate_zones: level 1 = zones 1–2, level 2 = zones 2–3, level 3 =
# zone 4; none for the short hard efforts, where the heart rate lags). Speech turns difficult at the ventilatory
# threshold, about 86–89 % of the peak heart rate on a treadmill (Persinger 2004): « facile » stays under it,
# « soutenu » (80–90 %) holds it. The zones' bounds are the app's (H).
LEVELS = {1: "très facile", 2: "facile", 3: "soutenu", 4: "vite"}
FEEL = {1: "Très facile : tu parles sans y penser.", 2: "Facile : tu peux tenir une conversation.",
        3: "Soutenu : quelques mots, pas des phrases.", 4: "Vite : parler devient impossible."}
HR_ZONES = {1: (1, 2), 2: (2, 3), 3: (4, 4)}

# the quality sessions: (kind, minutes of an effort, minutes of its recovery, level, fewest and most repetitions,
# how the effort is said, how the recovery is said). Each format is one a study ran, or sits inside what it ran
# (docs/coaching.md › « Les séances de qualité »); the fewest repetitions, lower than the studies', are ours (H):
# - seuil: 4 × 8 min at 90 % of the peak heart rate did better than 4 × 4 and 4 × 16 min (Seiler 2013);
# - intervalles: efforts of 3 to 5 min load the most (Seiler & Sjursen 2004), run hard — 90–95 % of the maximal
#   heart rate (Helgerud 2007), up to 8 repetitions (Esfarjani 2007): « vite », not « soutenu »;
# - fartlek: 2-min efforts with equal recoveries reach about 92 % of VO2max (Seiler & Sjursen 2004);
# - côtes longues: 6 × 4 min uphill (Seiler & Hetlelid 2005); côtes courtes: any hard uphill interval helps a
#   5-km time (Barnes 2013), 1-min efforts load less (Seiler & Sjursen 2004) — the base phase's;
# - recoveries: about 2 min is enough between 4-min efforts, 1 to 4 min changes little (Seiler & Hetlelid 2005);
# - rappels: a taper keeps the intensity (Bosquet 2007); their format is ours (H).
TEMPLATES = {
    "cotes_courtes": (HILLS, 1, 2, 4, 6, 10, "en côte", "récup en descente"),
    "fartlek": (QUALITY, 2, 2, 4, 5, 8, "vite", "récup 2 min"),
    "seuil": (QUALITY, 8, 2, 3, 2, 4, "soutenu", "récup 2 min"),
    "cotes_longues": (HILLS, 4, 3, 4, 3, 6, "en côte", "récup en descente"),
    "intervalles": (QUALITY, 3, 2, 4, 4, 8, "vite", "récup 2 min"),
    "rappels": (QUALITY, 2, 2, 3, 3, 5, "soutenu", "récup 2 min"),
}
# which one, by phase: the week's parity picks in the pair (the second quality session of a week takes the other)
BY_PHASE = {"base": ("cotes_courtes", "fartlek"), "specifique_trail": ("seuil", "cotes_longues"),
            "specifique_route": ("seuil", "intervalles"), "affutage": ("rappels", "rappels"),
            "progression": ("fartlek", "cotes_courtes", "seuil")}


@dataclass(frozen=True)
class Goal:
    """The race aimed at; `race_day` None: « Progresser, sans course »."""
    race_day: date | None = None
    race_minutes: float | None = None  # the race page's objective, else its estimate, else race_prep.expected_s
    trail: bool = True
    route_id: int | None = None
    name: str = ""
    time_kind: str = ""  # which one `race_minutes` is: objectif | estimation | approx
    climb_rate: float | None = None  # a trail race's D+ per hour of race (metres); None: no climb target


@dataclass(frozen=True)
class Inputs:
    days: tuple[int, ...]  # weekdays available (0 = Monday)
    long_day: int
    base: int  # starting weekly minutes
    start_monday: date  # week 0
    goal: Goal = Goal()
    adaptations: dict = field(default_factory=dict)  # {Monday ISO: {"reason", "factor", "base"}}
    # the athlete's real runs of the last SPIKE_DAYS days, (day, minutes): what the single-session rule reads for
    # the days behind; empty (no run recorded): the plan's own sessions stand for them
    recent: tuple[tuple[date, int], ...] = ()


@dataclass
class Planned:
    day: date
    kind: str
    minutes: int
    structure: dict


@dataclass
class Week:
    monday: date
    index: int
    phase: str
    target: int  # the week's training minutes aimed at
    sessions: list[Planned]
    light: bool = False  # the 4th week
    reason: str | None = None  # an adaptation's
    limit: int | None = None  # the longest a session of this week may be (`session_limit`); None: no run before it

    @property
    def minutes(self) -> int:
        """Training minutes planned (the race itself left out)."""
        return sum(s.minutes for s in self.sessions if s.kind != RACE)


# ── small helpers ───────────────────────────────────────────────────────────

def monday_of(d: date) -> date:
    return d - timedelta(days=d.weekday())


def round5(x: float) -> int:
    return int(math.floor(x / STEP + 0.5)) * STEP


def ceil5(x: float) -> int:
    return int(math.ceil(x / STEP - 1e-9)) * STEP


def floor5(x: float) -> int:
    return int(math.floor(x / STEP + 1e-9)) * STEP


def clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def circular(a: int, b: int) -> int:
    """Days between two weekdays, around the week (Sunday and Monday are neighbours)."""
    d = abs(a - b) % 7
    return min(d, 7 - d)


def default_long_day(days: tuple[int, ...]) -> int:
    """Sunday when it is available, else the last available day."""
    return 6 if 6 in days else max(days)


def starting_minutes(weekly: list[float]) -> int | None:
    """The starting volume from the last complete weeks' running minutes (the median, to 5 min, held to
    BASE_RANGE's floor); None without any running in them: the athlete is asked."""
    if not weekly or not any(m > 0 for m in weekly):
        return None
    return int(clamp(round5(statistics.median(weekly)), BASE_RANGE[0], BASE_RANGE[1]))


def rule_minutes(goal: Goal) -> float:
    """The expected race time as the rules read it: to RACE_TIME_STEP, so that an estimate moving by a minute from
    one day to the next does not rewrite the weeks (the race's own session keeps the exact time)."""
    return max(RACE_TIME_STEP, math.floor((goal.race_minutes or 0) / RACE_TIME_STEP + 0.5) * RACE_TIME_STEP)


def ceiling(goal: Goal, base: int) -> int:
    """The weekly ceiling: scaled to the race, never under the base (the plan never asks for less than the habit)."""
    if goal.race_day is not None and goal.race_minutes:
        top = clamp(CEILING[0] + CEILING[1] * rule_minutes(goal), *CEILING_RANGE)
    else:
        top = clamp(FREE_CEILING[0] * base, FREE_CEILING[1], FREE_CEILING[2])
    return int(max(round5(top), base))


def long_cap(goal: Goal) -> int:
    if goal.race_day is not None and goal.race_minutes:
        return int(round5(clamp(LONG_RACE_SHARE * rule_minutes(goal), *LONG_CAP_RANGE)))
    return FREE_LONG_CAP


# ── one session against the longest run of the last 30 days ─────────────────

def session_limit(longest: float | None, taper: bool = False) -> int | None:
    """The longest a session may be when the longest run of the SPIKE_DAYS days before it lasted `longest` minutes:
    10 % more (Frandsen 2025: past it, the rate of overuse injuries rises — 5 205 runners, in distance; here in
    time). To 5 min, rounded down; one step of 5 min is always left, or a plan in 5-min steps could never grow
    under 50 min — that step is more than 10 % there, the rounding's doing, not the study's. Never under
    MIN_SESSION. None without a run to compare with. `taper`: a week of Affûtage, where no session grows at all —
    its volume comes down (Bosquet 2007), so nothing in it is longer than the longest run before it."""
    if not longest or longest <= 0:
        return None
    if taper:
        return int(max(MIN_SESSION, floor5(longest)))
    return int(max(MIN_SESSION, floor5((1 + SPIKE) * longest), floor5(longest) + STEP))


# ── a trail race's climb ────────────────────────────────────────────────────

def climb_share(monday: date, inp: Inputs) -> float:
    """How much of the race's D+ per hour a week aims at: CLIMB_START on week 0, growing by equal steps to the whole
    on the last week before the taper, and the whole from then on."""
    rd = inp.goal.race_day
    if rd is None:
        return 1.0
    last = taper_start(rd) - timedelta(weeks=1)
    span = (last - inp.start_monday).days // 7
    if span <= 0 or monday >= last:
        return 1.0
    k = max(0, (monday - inp.start_monday).days // 7)
    return CLIMB_START + (1 - CLIMB_START) * k / span


def climb_target(goal: Goal, minutes: int, share: float = 1.0) -> int | None:
    """The climb a session of `minutes` aims at for a trail race (metres, to CLIMB_STEP): the race's D+ per hour
    (held to CLIMB_RATE_CAP) × the week's share × the session's hours; None for a road race, without a race, or
    under CLIMB_MIN."""
    if goal.race_day is None or not goal.trail or not goal.climb_rate or goal.climb_rate <= 0:
        return None
    metres = min(goal.climb_rate, CLIMB_RATE_CAP) * share * minutes / 60
    target = int(math.floor(metres / CLIMB_STEP + 0.5)) * CLIMB_STEP
    return target if target >= CLIMB_MIN else None


def with_climb(session: "Planned", goal: Goal, share: float) -> "Planned":
    """The long run and the long-hill session with their climb target (in the structure: {"climb": metres})."""
    if session.kind == LONG or (session.structure or {}).get("key") in CLIMB_KEYS:
        target = climb_target(goal, session.minutes, share)
        if target:
            session.structure = {**session.structure, "climb": target}
    return session


# ── phases ──────────────────────────────────────────────────────────────────

def taper_start(race_day: date) -> date:
    """The Monday of the week holding J-14: the first week of Affûtage."""
    return monday_of(race_day - timedelta(days=rp.TAPER_DAYS))


def specific_weeks(start_monday: date, race_day: date) -> int:
    before = max(0, (taper_start(race_day) - start_monday).days // 7)
    return min(before, SPECIFIC_WEEKS[1] if before >= SPECIFIC_LONG_FROM else SPECIFIC_WEEKS[0])


def phase_of(monday: date, inp: Inputs) -> str:
    rd = inp.goal.race_day
    if rd is None:
        return "progression"
    if monday > monday_of(rd):
        return "apres"
    first = taper_start(rd)
    if monday >= first:
        return "affutage"
    weeks_to_taper = (first - monday).days // 7
    return "specifique" if weeks_to_taper <= specific_weeks(inp.start_monday, rd) else "base"


def horizon(inp: Inputs, today: date) -> list[date]:
    """The Mondays generated: from this week to the race week (24 weeks at most), or 12 weeks without a race; none
    once the race is behind."""
    m0, rd = monday_of(today), inp.goal.race_day
    if rd is None:
        n = BLOCK_WEEKS
    elif rd < today:
        return []
    else:
        n = min(HORIZON_WEEKS, (monday_of(rd) - m0).days // 7 + 1)
    return [m0 + timedelta(weeks=i) for i in range(n)]


# ── weekly volume ───────────────────────────────────────────────────────────

def week_targets(inp: Inputs, until: date) -> dict[date, dict]:
    """{Monday: {"target", "light", "reason", "easy"}} from week 0 to `until`. A chain: each week's volume reads the
    weeks before it (a repeated week shifts the progression by one, a restart starts it again from its new base),
    so it is computed from week 0 every time: the same answer whatever the day it is asked."""
    rd = inp.goal.race_day
    first_taper = taper_start(rd) if rd else None
    out: dict[date, dict] = {}
    base, anchor, shift, top = inp.base, 0, 0, ceiling(inp.goal, inp.base)
    k, m = 0, inp.start_monday
    while m <= until:
        a = (inp.adaptations or {}).get(m.isoformat()) or {}
        reason = a.get("reason")
        if reason == "reprise" and a.get("base"):
            base, anchor, shift = int(a["base"]), k, 0
            top = ceiling(inp.goal, base)
        elif reason == "semaine_manquee":
            shift += 1
        rel = k - anchor
        light = rel % LIGHT_EVERY == LIGHT_EVERY - 1
        # loading weeks behind it (a lighter week: 75 % of the last loading week, which the next one builds on)
        steps = max(0, rel - rel // LIGHT_EVERY - shift - (1 if light else 0))
        volume = min(top, base * (1 + GROWTH) ** steps) * (LIGHT_FACTOR if light else 1)
        easy = reason in ("ressenti", "manquees", FORM)
        if rd and first_taper and m >= first_taper:
            # Affûtage: race_prep's bands on the base = the median of the 4 weeks before it (its own rule); the
            # ressenti never cuts a taper further, it only turns its reminder into a footing
            before = [out[x]["target"] for x in sorted(out) if x < first_taper][-4:]
            band = rp.week_target(statistics.median(before) if before else base, m, rd)
            volume, light = (sum(band) / 2 if band else 0), False
            if m > monday_of(rd):
                volume = 0
        else:
            previous = out.get(m - timedelta(weeks=1))
            if reason == "semaine_manquee" and previous:
                volume, light = previous["target"], False  # the week missed, again; the progression waits a week
            if easy:
                volume *= float(a.get("factor") or ADAPT_FACTOR)
            volume = max(volume, WEEK_FLOOR)
        out[m] = {"target": round5(volume), "light": light, "reason": reason if reason in REASONS else None,
                  "easy": easy}
        k, m = k + 1, m + timedelta(weeks=1)
    return out


# ── one session's structure ─────────────────────────────────────────────────

def steady(minutes: int, level: int, kind: str = "continu") -> dict:
    return {"steps": [{"t": kind, "min": int(minutes), "level": level}], "text": ""}


def quality_session(key: str, reps: int) -> tuple[str, int, dict]:
    """(kind, minutes, structure) of a quality session: warm-up, `reps` efforts with their recoveries (none after the
    last one), cool-down padded so that the whole is a multiple of 5 min."""
    kind, effort, recov, level, _lo, _hi, said, rest = TEMPLATES[key]
    block = reps * effort + (reps - 1) * recov
    total = ceil5(WARM_UP + block + COOL_DOWN)
    text = f"{reps} × {effort} min {said}, {rest}"
    return kind, total, {"key": key, "text": text, "steps": [
        {"t": "echauffement", "min": WARM_UP, "level": 2},
        {"t": "bloc", "reps": reps, "effort": effort, "recup": recov, "level": level},
        {"t": "retour", "min": total - WARM_UP - block, "level": 1}]}


def hard_minutes(structure: dict | None) -> float:
    """Minutes of hard effort (level 3 or more) in a session's structure."""
    total = 0.0
    for step in (structure or {}).get("steps", []):
        if step.get("t") == "bloc" and step.get("level", 0) >= 3:
            total += step["reps"] * step["effort"]
    return total


def default_structure(kind: str, minutes: int) -> dict:
    """What a session a person added or retyped is made of: a steady effort for the easy kinds; for a quality kind,
    its first template with as many repetitions as fit the duration."""
    if kind in (QUALITY, HILLS):
        key = "seuil" if kind == QUALITY else "cotes_courtes"
        _k, effort, recov, _l, lo, hi, _s, _r = TEMPLATES[key]
        room = minutes - WARM_UP - COOL_DOWN
        reps = int(clamp((room + recov) // (effort + recov), 1, hi)) if room >= effort else 0
        if reps >= 1:
            _kind, total, structure = quality_session(key, reps)
            pad = minutes - total  # the cool-down takes what is left (or gives it back)
            if structure["steps"][-1]["min"] + pad >= 5:
                structure["steps"][-1]["min"] += pad
                return structure
        return steady(minutes, 3)
    return steady(minutes, 1 if kind == RECUP else 3 if kind == RACE else 2, "course" if kind == RACE else "continu")


def describe(kind: str, structure: dict | None) -> str:
    """The structure in words: « 3 × 8 min soutenu, récup 2 min » for a quality session, nothing for a steady one."""
    return (structure or {}).get("text") or ""


def main_level(kind: str, structure: dict | None) -> int:
    levels = [s.get("level", 2) for s in (structure or {}).get("steps", []) if s.get("t") in ("bloc", "continu",
                                                                                              "course")]
    return max(levels) if levels else (1 if kind == RECUP else 2)


# ── one week ────────────────────────────────────────────────────────────────

def _quality_keys(phase: str, index: int, trail: bool) -> tuple[str, ...]:
    if phase == "specifique":
        pair = BY_PHASE["specifique_trail" if trail else "specifique_route"]
    else:
        pair = BY_PHASE.get(phase, BY_PHASE["base"])
    i = index % len(pair)
    return pair[i:] + pair[:i]


def _reps(key: str, budget: float, weeks_in_phase: int, least: bool) -> int | None:
    """How many repetitions: one more every REP_EVERY weeks of the phase from the fewest, never more hard minutes
    than `budget`; None when even the fewest would not fit (no quality session then)."""
    _kind, effort, _recov, _level, lo, hi, _s, _r = TEMPLATES[key]
    reps = min(hi, lo if least else lo + weeks_in_phase // REP_EVERY, int(budget // effort))
    return reps if reps >= lo else None


def place(days: list[int], long_day: int | None, anchors: set[int], count: int) -> list[int]:
    """The weekdays of `count` quality sessions among `days`: never next to a hard day (`anchors`: the long run's
    weekday, the race's, around the week), the furthest from them first, the earliest on a tie."""
    hard, out = set(anchors), []
    for _ in range(count):
        free = [d for d in days if d != long_day and d not in out and all(circular(d, h) >= 2 for h in hard)]
        if not free:
            break
        best = max(free, key=lambda d: (min((circular(d, h) for h in hard), default=7), -d))
        out.append(best)
        hard.add(best)
    return out


def week_sessions(monday: date, inp: Inputs, info: dict, phase: str, index: int, weeks_in_phase: int = 0,
                  limit: int | None = None) -> list:
    """The sessions of one week for its target volume (see the module's rules). `limit`: the longest any of them may
    be (`session_limit`); a week that cannot hold its volume in sessions that short asks for less — the session's
    length is what the study ties to injuries, not the week's total."""
    goal, rd = inp.goal, inp.goal.race_day
    race_in_week = rd if rd is not None and monday_of(rd) == monday else None  # the race day, in its own week
    race_week = race_in_week is not None
    out: list[Planned] = []
    if race_in_week is not None:
        minutes = int(round(goal.race_minutes or 60))
        out.append(Planned(race_in_week, RACE, minutes, {**steady(minutes, 3, "course"), "text": goal.name or ""}))
    if phase == "apres":
        return out
    volume = int(info["target"])
    days = [d for d in sorted(inp.days) if race_in_week is None or d < race_in_week.weekday()]
    n = min(len(days), volume // MIN_SESSION)
    if n <= 0:
        return out

    def fits(minutes: float) -> bool:
        return limit is None or minutes <= limit

    # the hard days this week's sessions must keep away from: the long run's weekday (last week's too) and the
    # race's (a race on the Monday after counts for this week's Sunday)
    near_race = rd if rd is not None and monday <= rd <= monday + timedelta(days=7) else None
    anchors = {inp.long_day} | ({near_race.weekday()} if near_race is not None else set())
    long_date = monday + timedelta(days=inp.long_day)
    has_long = (n >= 2 and inp.long_day in days and not race_week
                and not (rd is not None and 0 < (rd - long_date).days <= NO_LONG_BEFORE_RACE))
    wanted = 0
    if len(inp.days) >= QUALITY_FROM_DAYS and n >= QUALITY_FROM_DAYS and not info.get("easy"):
        wanted = 2 if (len(inp.days) >= TWO_QUALITY[0] and inp.base >= TWO_QUALITY[1]
                       and phase in ("base", "specifique", "progression") and not info.get("light")) else 1
    keys = _quality_keys(phase, index, goal.trail)
    cap = long_cap(goal) if limit is None else min(long_cap(goal), limit)
    least = bool(info.get("light")) or phase == "affutage"
    chosen: tuple[list, int, int, int] | None = None
    for q in range(wanted, -1, -1):
        spots = place(days, inp.long_day if has_long else None, anchors, q)
        slots = n - len(spots)
        # the week the hard share is counted on: its target, or what its sessions can hold once each is held to
        # `limit` (the quality sessions keep their place; the easy ones give the minutes up)
        basis = volume if limit is None else min(volume, n * limit)
        while True:
            quals = []
            for i, wd in enumerate(spots):
                key = keys[i % len(keys)]
                reps = _reps(key, HARD_SHARE * basis / max(len(spots), 1), weeks_in_phase, least)
                if reps is not None and fits(quality_session(key, reps)[1]):
                    quals.append((wd, *quality_session(key, reps)))
            used = sum(x[2] for x in quals)
            easy = volume - used  # what is left for the long run and the footings, on `slots` days
            if limit is not None:
                easy = min(easy, slots * limit)
            if len(quals) < q or sum(hard_minutes(x[3]) for x in quals) <= HARD_SHARE * (used + easy) + 1e-9:
                break
            basis = used + easy  # a shorter week than its target: fewer repetitions
        if len(quals) < q:
            continue
        if easy < 0 or (slots == 0 and easy != 0):
            continue
        found = None
        if not has_long:
            for k in range(slots, 0, -1):
                if easy >= MIN_SESSION * k and fits(ceil5(easy / k)):
                    found = (0, k)
                    break
            if found is None and easy == 0:
                found = (0, 0)
        else:
            # as many footings as fit; the long run about 35 % of the week and never shorter than a footing
            for k in range(slots - 1, -1, -1):
                long_minutes = int(min(cap, max(round5(LONG_SHARE * volume), ceil5(easy / (k + 1)))))
                rest = easy - long_minutes
                if k == 0 and rest >= 0 and long_minutes + rest <= cap:
                    found = (long_minutes + rest, 0)
                elif k > 0 and rest >= MIN_SESSION * k and long_minutes >= MIN_SESSION and fits(ceil5(rest / k)):
                    found = (long_minutes, k)
                if found:
                    break
        if found is None:
            continue
        chosen = (quals, found[0], found[1], easy - found[0])
        break
    if chosen is None:  # only when the long run's cap leaves no room: the same week without a long run
        chosen, has_long = ([], 0, n, volume if limit is None else min(volume, n * limit)), False
    quals, long_minutes, k, rest = chosen
    hard = set(anchors) | {wd for wd, *_ in quals}
    for wd, kind, minutes, structure in quals:
        out.append(Planned(monday + timedelta(days=wd), kind, minutes, structure))
    if has_long:
        out.append(Planned(monday + timedelta(days=inp.long_day), LONG, long_minutes, steady(long_minutes, 2)))
    taken = {wd for wd, *_ in quals} | ({inp.long_day} if has_long else set())
    free = sorted((d for d in days if d not in taken),
                  key=lambda d: (-min((circular(d, h) for h in hard), default=7), d))[:k]
    if free:
        each, extra = divmod(rest // STEP, len(free))
        # the day after a hard one is a recovery jog (level 1), and takes the shorter share
        recov = {d for d in free if (d - 1) % 7 in hard}
        for i, wd in enumerate(sorted(free, key=lambda d: (d in recov, d))):
            minutes = (each + (1 if i < extra else 0)) * STEP
            kind = RECUP if wd in recov else FOOTING
            out.append(Planned(monday + timedelta(days=wd), kind, minutes, steady(minutes, 1 if kind == RECUP else 2)))
    share = climb_share(monday, inp)
    return sorted((with_climb(s, goal, share) for s in out), key=lambda s: s.day)


# ── the plan ────────────────────────────────────────────────────────────────

def build(inp: Inputs, today: date) -> list[Week]:
    """The weeks from this one to the horizon, with their sessions (every day of each week: the caller keeps the
    days it may write).

    The single-session rule runs along the weeks: each week's sessions are held to `session_limit` of the longest
    run of the 30 days before it — the athlete's real runs for the days behind (`inp.recent`), the plan's own
    sessions for the days ahead (they are what the athlete is about to run). Without any real run to read, the
    plan's sessions stand for the days behind too, from week 0: the same plan whatever the day it is asked."""
    mondays = horizon(inp, today)
    if not mondays:
        return []
    targets = week_targets(inp, mondays[-1])
    out, run, last = [], 0, None
    m = inp.start_monday
    counts: dict[date, int] = {}
    while m <= mondays[-1]:  # weeks spent in the phase so far, from week 0: the repetitions' progression
        ph = phase_of(m, inp)
        run = run + 1 if ph == last else 0
        counts[m], last = run, ph
        m += timedelta(weeks=1)
    real = [(d, minutes) for d, minutes in inp.recent if d <= today]
    ahead: list[tuple[date, int]] = []  # the plan's sessions that stand for runs: those to come, or all without `real`
    m = mondays[0] if real else min(inp.start_monday, mondays[0])
    while m <= mondays[-1]:
        info, phase = targets[m], phase_of(m, inp)
        index = (m - inp.start_monday).days // 7
        start = max(m, today) if real else m  # the week's first day still to run
        first = start - timedelta(days=SPIKE_DAYS)
        limit = session_limit(max([minutes for d, minutes in real if first <= d <= start]
                                  + [minutes for d, minutes in ahead if first <= d < start], default=None),
                              taper=phase == "affutage")
        sessions = week_sessions(m, inp, info, phase, index, counts.get(m, 0), limit)
        ahead += [(s.day, s.minutes) for s in sessions if s.kind != RACE and (not real or s.day >= today)]
        if m >= mondays[0]:
            out.append(Week(m, index, phase, info["target"], sessions, light=info["light"], reason=info["reason"],
                            limit=limit))
        m += timedelta(weeks=1)
    return out


# ── the ressenti's adaptation ───────────────────────────────────────────────

@dataclass(frozen=True)
class Fact:
    """What the adaptation reads of a stored session."""
    day: date
    kind: str
    status: str  # planned | done | missed
    ressenti: int | None = None


def signals(facts: list[Fact]) -> list[Fact]:
    """The easy sessions done that felt « Dur » or « Très dur »."""
    return sorted((f for f in facts if f.kind in EASY and f.status == "done" and (f.ressenti or 0) >= SIGNAL_FROM),
                  key=lambda f: f.day)


def lighter_reason(facts: list[Fact], week: date) -> str | None:
    """Why the week after `week` is lighter, or None: a « Très dur » on an easy session of `week`, two signals
    within 7 days the later of them in `week`, or two sessions or more missed in `week` (not all of them: a whole
    week missed is `adaptation`'s other rule)."""
    end = week + timedelta(days=6)
    sig = signals(facts)
    mine = [f for f in sig if week <= f.day <= end]
    if any(f.ressenti == 5 for f in mine):
        return "ressenti"
    for f in mine:
        if any(o is not f and timedelta(0) <= f.day - o.day < timedelta(days=SIGNAL_DAYS) for o in sig):
            return "ressenti"
    missed = [f for f in facts if week <= f.day <= end and f.status == "missed" and f.kind != RACE]
    if len(missed) >= MISSED_FROM:
        return "manquees"
    return None


def week_missed(facts: list[Fact], week: date, done_minutes: float) -> bool:
    """A whole week missed: it had sessions, none was done, and no run was recorded in it."""
    mine = [f for f in facts if week <= f.day <= week + timedelta(days=6) and f.kind != RACE]
    return bool(mine) and all(f.status == "missed" for f in mine) and done_minutes <= 0


def adaptation(facts: list[Fact], week: date, done: dict[date, float], fresh_base: int | None,
               closed: bool = True) -> dict | None:
    """What the week of Monday `week` gets from the week before it: None (the planned progression goes on), a lighter
    week ({"reason": "ressenti" | "manquees", "factor": 0.8}), the same volume again after a whole week missed
    ({"reason": "semaine_manquee"}), or a restart from the base read again from the real activities after two
    ({"reason": "reprise", "base": …}). `closed`: the week before is over (a week still running can only make the
    next one lighter: its missed sessions are not all known yet). `done`: {Monday: minutes run that week}."""
    before = week - timedelta(weeks=1)
    if closed and week_missed(facts, before, done.get(before, 0)):
        earlier = before - timedelta(weeks=1)
        if week_missed(facts, earlier, done.get(earlier, 0)):
            base = int(clamp(fresh_base or WEEK_FLOOR, WEEK_FLOOR, BASE_RANGE[1]))
            return {"reason": "reprise", "base": base}
        return {"reason": "semaine_manquee"}
    reason = lighter_reason(facts, before)
    return {"reason": reason, "factor": ADAPT_FACTOR} if reason else None


# ── the form: a low morning (docs/coaching.md › « La forme ») ───────────────

@dataclass(frozen=True)
class Slot:
    """What the form's move reads of a stored session: its day, its kind, and whether the plan may rewrite it (a
    generated session still planned that nobody edited)."""
    day: date
    kind: str
    free: bool = True


def form_footing(minutes: int, taper: bool = False) -> int:
    """The footing a hard session becomes on a low morning: FORM_FOOTING_SHARE of its duration, between MIN_SESSION
    and FORM_FOOTING_CAP; in a taper week the same duration (its volume is never cut: only the intensity goes)."""
    if taper:
        return int(minutes)
    return int(clamp(round5(FORM_FOOTING_SHARE * minutes), MIN_SESSION, FORM_FOOTING_CAP))


def move_day(today: date, days: tuple[int, ...], slots: list[Slot], kind: str,
             race_day: date | None = None) -> date | None:
    """Where the hard session of a low morning goes: the next available day of the same week (to Sunday) that holds
    nothing, or one easy session the plan may rewrite (it takes its place), and keeps « never two hard days in a
    row » — today no longer counts, it is a footing now; next Monday's session does. A long run never from J-3.
    None: no such day, the session is dropped. `slots`: the live sessions from today to next Monday."""
    hard = {s.day for s in slots if s.kind in HARD and s.day != today}
    by: dict[date, list[Slot]] = {}
    for s in slots:
        by.setdefault(s.day, []).append(s)
    d, sunday = today + timedelta(days=1), monday_of(today) + timedelta(days=6)
    while d <= sunday:
        there = by.get(d, [])
        near_race = kind == LONG and race_day is not None and 0 < (race_day - d).days <= NO_LONG_BEFORE_RACE
        if (d.weekday() in days and not near_race and not {d - timedelta(days=1), d, d + timedelta(days=1)} & hard
                and (not there or (len(there) == 1 and there[0].free and there[0].kind in (FOOTING, RECUP)))):
            return d
        d += timedelta(days=1)
    return None


def form_week(low: list[date], week: date) -> bool:
    """Whether the form lightens the week of Monday `week`: FORM_LOW_COUNT low mornings within FORM_LOW_DAYS days,
    the last of them in the week before."""
    span = timedelta(days=FORM_LOW_DAYS)
    return any(sum(1 for o in low if timedelta(0) <= d - o < span) >= FORM_LOW_COUNT
               for d in low if week - timedelta(weeks=1) <= d < week)


def with_form(adaptation: dict | None) -> dict:
    """The week's adaptation once the form asks for a lighter week: one reduction, never two stacked — the larger
    of the form's and the ressenti's (the ressenti's on a tie: its reason is the one a coach may read); a repeated
    week or a restart stands as it is."""
    mine = {"reason": FORM, "factor": ADAPT_FACTOR}
    if adaptation is None:
        return mine
    if adaptation.get("reason") in ("ressenti", "manquees"):
        return mine if ADAPT_FACTOR < float(adaptation.get("factor") or ADAPT_FACTOR) else adaptation
    return adaptation
