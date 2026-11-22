"""Coaching: the plan's rules (app/services/coaching_plan.py, docs/coaching.md), as properties over many inputs —
every set of available days, bases from 1 h to 10 h, races on every weekday from 1 to 30 weeks away, road and
trail, and no race at all. Each value is pinned here with its mark — a study's, a figure of ours (H), a practical
choice: changing one is a decision, and a rule ships with its study or says it has none (owner, 22 Nov. 2026)."""
import itertools
import re
from datetime import date, timedelta
from pathlib import Path

import pytest

from app.services import coaching_plan as cp
from app.services import race_prep as rp

ROOT = Path(__file__).resolve().parent.parent
TODAY = date(2026, 11, 21)  # a Saturday
M0 = cp.monday_of(TODAY)

DAY_SETS = [days for n in range(2, 7) for days in itertools.combinations(range(7), n)]
BASES = (60, 95, 150, 180, 240, 300, 420, 600)


def _inputs(days, base, race=None, minutes=300, trail=True, long_day=None, adaptations=None, start=M0):
    goal = cp.Goal(race, minutes, trail, 1, "Trail des Crêtes") if race else cp.Goal()
    return cp.Inputs(tuple(days), cp.default_long_day(days) if long_day is None else long_day, base, start, goal,
                     adaptations or {})


def _cases():
    """A wide sample: every day set with a few bases and goals (≈ 1 500 plans)."""
    races = [None] + [TODAY + timedelta(days=d) for d in (9, 20, 33, 54, 75, 100, 124, 150, 210)]
    for i, days in enumerate(DAY_SETS):
        for j in range(3):
            base = BASES[(i + 3 * j) % len(BASES)]
            race = races[(i + j) % len(races)]
            minutes = (55, 240, 600, 1500)[(i + j) % 4]
            for long_day in {cp.default_long_day(days), days[0]}:
                yield _inputs(days, base, race, minutes, trail=(i + j) % 2 == 0, long_day=long_day)


CASES = list(_cases())


def _training(week):
    return [s for s in week.sessions if s.kind != cp.RACE]


# ── every value, pinned and marked ──────────────────────────────────────────

# a figure of PaceForge's: a study backs the principle, or none does — its line says (H)
FIGURES = {
    "GROWTH": 0.10, "LIGHT_EVERY": 4, "LIGHT_FACTOR": 0.75, "SPECIFIC_WEEKS": (6, 8), "SPECIFIC_LONG_FROM": 16,
    "CEILING": (150, 0.7), "CEILING_RANGE": (180, 720), "FREE_CEILING": (1.5, 180, 480), "LONG_SHARE": 0.35,
    "LONG_RACE_SHARE": 0.55, "LONG_CAP_RANGE": (120, 210), "FREE_LONG_CAP": 150, "NO_LONG_BEFORE_RACE": 3,
    "QUALITY_FROM_DAYS": 3, "TWO_QUALITY": (5, 240), "REP_EVERY": 2, "ADAPT_FACTOR": 0.8, "SIGNAL_FROM": 4,
    "SIGNAL_DAYS": 7, "MISSED_FROM": 2, "CLIMB_START": 0.6, "CLIMB_RATE_CAP": 500, "CLIMB_MIN": 100,
    "FORM_FOOTING_SHARE": 0.6, "FORM_FOOTING_CAP": 45, "FORM_LOW_COUNT": 3, "FORM_LOW_DAYS": 5,
}
# not a training decision: how far ahead to write, a rounding, a stable reading — its line says « choix pratique »
PRACTICAL = {
    "STEP": 5, "WEEK_FLOOR": 60, "HORIZON_WEEKS": 24, "BLOCK_WEEKS": 12, "START_CHOICES": (60, 120, 180, 240, 300),
    "BASE_RANGE": (60, 900), "RACE_TIME_STEP": 15, "CLIMB_STEP": 50,
}
# the value is a study's: its line names it, and the study is in the document's sources with its DOI
CITED = {
    "MIN_SESSION": (30, "Garber 2011"), "SPIKE": (0.10, "Frandsen 2025"), "SPIKE_DAYS": (30, "that study"),
    "HARD_SHARE": (0.20, "Muñoz 2014"),
}


def _line(name):
    source = (ROOT / "app/services/coaching_plan.py").read_text(encoding="utf-8")
    return next(ln for ln in source.splitlines() if re.match(rf"{name}\s*=", ln))


@pytest.mark.parametrize("name,value", FIGURES.items())
def test_each_figure_of_ours_is_pinned_and_marked_h(name, value):
    assert getattr(cp, name) == value
    assert "(H)" in _line(name), f"{name} is a figure no study fixes: its line says (H)"


@pytest.mark.parametrize("name,value", PRACTICAL.items())
def test_each_practical_choice_is_pinned_and_says_so(name, value):
    assert getattr(cp, name) == value
    assert "choix pratique" in _line(name) and "(H)" not in _line(name)


@pytest.mark.parametrize("name,expected", CITED.items())
def test_each_value_taken_from_a_study_names_it(name, expected):
    value, study = expected
    assert getattr(cp, name) == value
    assert study in _line(name) and "(H)" not in _line(name)


def test_every_study_the_rules_name_is_in_the_sources_with_its_doi():
    """A rule ships with its study: each « Name 2013 » written in the rules' module is a line of the document's
    « Sources », with a DOI or a PubMed link."""
    source = (ROOT / "app/services/coaching_plan.py").read_text(encoding="utf-8")
    doc = (ROOT / "docs/coaching.md").read_text(encoding="utf-8")
    sources = doc[doc.index("## Sources"):]
    named = set(re.findall(r"([A-ZÀ-Ý][\w-]+(?: (?:&|et) [A-ZÀ-Ý][\w-]+)?) ((?:19|20)\d\d)\b", source))
    assert len(named) >= 20, named
    entries = [e for e in re.split(r"\n- ", sources) if re.search(r"\((?:19|20)\d\d\)", e)]
    for name, year in sorted(named):
        first = name.split(" ")[0]
        entry = next((e for e in entries if e.lstrip().startswith(first) and f"({year})" in e), None)
        assert entry, f"{name} {year} is named in the rules and missing from the sources"
        assert "https://doi.org/" in entry or "https://pubmed.ncbi.nlm.nih.gov/" in entry or "sportsci.org" in entry, entry


def test_the_cited_rules_are_the_race_pages():
    """The taper is race_prep's (Bosquet 2007; Mujika 2003), not a copy; the hard share is Seiler's 20 %, counted in
    time as Muñoz 2014 did."""
    assert (rp.TAPER_SHARE_EARLY, rp.TAPER_SHARE_LATE, rp.TAPER_DAYS) == ((0.55, 0.70), (0.35, 0.50), 14)
    assert cp.HARD_SHARE == 0.20
    doc = (ROOT / "docs/coaching.md").read_text(encoding="utf-8")
    for source in ("Bosquet", "Mujika", "Seiler", "Muñoz", "(H)", "choix pratique"):
        assert source in doc


# ── properties over many inputs ─────────────────────────────────────────────

def test_the_sample_is_wide():
    assert len(CASES) > 250 and {len(c.days) for c in CASES} == {2, 3, 4, 5, 6}


@pytest.mark.parametrize("inp", CASES[::1])
def test_every_plan_keeps_the_rules(inp):
    weeks = cp.build(inp, TODAY)
    rd = inp.goal.race_day
    assert weeks, "a plan always has weeks while the race is ahead"
    assert weeks[0].monday == M0 and all(b.monday - a.monday == timedelta(weeks=1) for a, b in zip(weeks, weeks[1:]))
    assert len(weeks) == (cp.BLOCK_WEEKS if rd is None else min(cp.HORIZON_WEEKS, (cp.monday_of(rd) - M0).days // 7 + 1))
    sessions = [s for w in weeks for s in w.sessions]
    # one session a day at most, every duration a multiple of 5
    assert len({s.day for s in sessions}) == len(sessions)
    for w in weeks:
        for s in _training(w):
            assert s.day.weekday() in inp.days, "only on the available days"
            assert s.minutes >= cp.MIN_SESSION and s.minutes % cp.STEP == 0, "never under 30 min, to 5 min"
            assert cp.monday_of(s.day) == w.monday
            assert sum(step.get("min", 0) or step["reps"] * step["effort"] + (step["reps"] - 1) * step["recup"]
                       for step in s.structure["steps"]) == s.minutes, "the structure adds up to the duration"
        easy = [s for s in _training(w) if s.kind in (cp.FOOTING, cp.RECUP, cp.LONG)]
        # the week's sessions add up to its target (nothing lost in the rounding) — or every easy session is as long
        # as the single-session rule lets it be, and the week asks for less than its target
        if _training(w):
            assert w.minutes == w.target or (
                w.limit and w.minutes < w.target and all(s.minutes == w.limit for s in easy)), (w.index, w.limit)
        # no session more than 10 % longer than the longest of the 30 days before the week (Frandsen 2025)
        before = [x.minutes for x in sessions if x.kind != cp.RACE
                  and w.monday - timedelta(days=cp.SPIKE_DAYS) <= x.day < w.monday]
        assert w.limit == cp.session_limit(max(before, default=None), taper=w.phase == "affutage")
        if w.limit:
            assert all(s.minutes <= w.limit for s in _training(w)), (w.index, w.limit)
        # at most 20 % of the week in hard efforts
        hard = sum(cp.hard_minutes(s.structure) for s in _training(w))
        assert hard <= cp.HARD_SHARE * w.minutes + 1e-9
        # the long run on its day, never longer than its cap
        longs = [s for s in _training(w) if s.kind == cp.LONG]
        assert len(longs) <= 1
        for s in longs:
            assert s.day.weekday() == inp.long_day and s.minutes <= cp.long_cap(inp.goal)
            assert s.minutes >= max((x.minutes for x in _training(w) if x.kind in (cp.FOOTING, cp.RECUP)), default=0) or (
                s.minutes == cp.long_cap(inp.goal)), "the long run is the longest easy session, unless capped"
        # quality: none under 3 available days, one from 3, two only from 5 days and a 4-h base
        quality = [s for s in _training(w) if s.kind in (cp.QUALITY, cp.HILLS)]
        most = 0 if len(inp.days) < 3 else 2 if len(inp.days) >= 5 and inp.base >= 240 else 1
        assert len(quality) <= most
    # never two hard days in a row (quality, long run, race), across the weeks too
    hard_days = sorted(s.day for s in sessions if s.kind in cp.HARD)
    assert all((b - a).days >= 2 for a, b in zip(hard_days, hard_days[1:])), hard_days
    # the race day is a « Course » session, the last one
    if rd is not None and cp.monday_of(rd) <= weeks[-1].monday:
        race = [s for s in sessions if s.kind == cp.RACE]
        assert [s.day for s in race] == [rd] and race[0].minutes == round(inp.goal.race_minutes)
        assert max(s.day for s in sessions) == rd, "nothing after the race"
        assert not [s for s in sessions if s.kind == cp.LONG and 0 < (rd - s.day).days <= cp.NO_LONG_BEFORE_RACE]
    else:
        assert not [s for s in sessions if s.kind == cp.RACE]


@pytest.mark.parametrize("inp", CASES[::2])
def test_weekly_volume_grows_within_the_cap_with_a_lighter_fourth_week(inp):
    rd = inp.goal.race_day
    until = (cp.taper_start(rd) - timedelta(weeks=1)) if rd else M0 + timedelta(weeks=30)
    targets = cp.week_targets(inp, M0 + timedelta(weeks=30))
    weeks = [(m, t) for m, t in sorted(targets.items()) if m <= until]
    top = cp.ceiling(inp.goal, inp.base)
    assert top >= inp.base
    best = None  # the highest loading week so far
    for k, (m, info) in enumerate(weeks):
        target = info["target"]
        assert target >= cp.WEEK_FLOOR and target % 5 == 0 and target <= top + 2.5
        assert info["light"] == (k % 4 == 3)
        if k == 0:
            assert target == cp.round5(max(inp.base, cp.WEEK_FLOOR)), "the plan starts at the athlete's base"
        if info["light"]:
            assert target <= max(cp.round5(best * cp.LIGHT_FACTOR) + 5, cp.WEEK_FLOOR), "the 4th week is lighter"
            assert target < best or best <= cp.WEEK_FLOOR
        else:
            if best is not None:
                assert target <= best * (1 + cp.GROWTH) + 5, "never more than +10 % over the last loading week"
                assert target >= best - 5, "a loading week never goes down"
            best = target


@pytest.mark.parametrize("weekday", range(7))
@pytest.mark.parametrize("base", (120, 200, 330))
def test_the_taper_follows_the_race_pages_bands(weekday, base):
    race = M0 + timedelta(weeks=10, days=weekday)
    inp = _inputs((1, 3, 5, 6), base, race)
    targets = cp.week_targets(inp, cp.monday_of(race))
    first = cp.taper_start(race)
    before = [targets[m]["target"] for m in sorted(targets) if m < first][-4:]
    reference = sorted(before)[1:3]
    reference = sum(reference) / 2  # the median of the 4 weeks before the taper
    for m in sorted(targets):
        band = rp.week_target(reference, m, race)
        if m < first:
            assert band is None
            continue
        if band is None:  # a race on a Monday: its week has no day before it
            assert m == cp.monday_of(race) and targets[m]["target"] == 0
            continue
        lo, hi = band
        assert lo - 2.5 <= targets[m]["target"] <= hi + 2.5, (m, targets[m], band)
        assert not targets[m]["light"]
    weeks = {w.monday: w for w in cp.build(inp, TODAY)}
    for m in sorted(weeks):
        if m >= first:
            assert weeks[m].phase == "affutage"
            quality = [s for s in weeks[m].sessions if s.kind in (cp.QUALITY, cp.HILLS)]
            assert all(s.structure["key"] == "rappels" for s in quality), "intensity kept: short reminders"
    # with enough volume, each taper week keeps its reminder
    if base >= 200:
        assert any(s.kind == cp.QUALITY for s in weeks[first + timedelta(weeks=1)].sessions)


def test_the_phases_run_base_specific_taper_then_the_race():
    race = M0 + timedelta(weeks=20, days=6)
    inp = _inputs((1, 3, 5, 6), 200, race)
    phases = [w.phase for w in cp.build(inp, TODAY)]
    assert phases == ["base"] * 10 + ["specifique"] * 8 + ["affutage"] * 3
    short = [w.phase for w in cp.build(_inputs((1, 3, 5, 6), 200, M0 + timedelta(weeks=9, days=6)), TODAY)]
    assert short == ["base"] + ["specifique"] * 6 + ["affutage"] * 3
    # a race further than 24 weeks away: 24 weeks generated, base first
    far = cp.build(_inputs((1, 3, 5, 6), 200, M0 + timedelta(weeks=40, days=6)), TODAY)
    assert len(far) == 24 and {w.phase for w in far[:10]} == {"base"}
    # no race: 12 weeks of « Progression », extended as the weeks pass
    free = cp.build(_inputs((1, 3, 5, 6), 200), TODAY)
    assert [w.phase for w in free] == ["progression"] * 12
    later = cp.build(_inputs((1, 3, 5, 6), 200), TODAY + timedelta(weeks=5))
    assert later[0].monday == M0 + timedelta(weeks=5) and later[-1].monday == M0 + timedelta(weeks=16)
    assert later[0].index == 5, "the progression still counts from week 0"
    # once the race is behind: nothing
    assert cp.build(_inputs((1, 3, 5, 6), 200, TODAY - timedelta(days=1), start=M0 - timedelta(weeks=8)), TODAY) == []


def test_the_quality_session_follows_its_phase_and_says_its_structure():
    trail = cp.build(_inputs((1, 3, 5, 6), 240, M0 + timedelta(weeks=14, days=6)), TODAY)
    by_phase = {}
    for w in trail:
        for s in w.sessions:
            if s.kind in (cp.QUALITY, cp.HILLS):
                by_phase.setdefault(w.phase, set()).add(s.structure["key"])
    assert by_phase == {"base": {"cotes_courtes", "fartlek"}, "specifique": {"seuil", "cotes_longues"},
                        "affutage": {"rappels"}}
    road = cp.build(_inputs((1, 3, 5, 6), 240, M0 + timedelta(weeks=14, days=6), trail=False), TODAY)
    assert {s.structure["key"] for w in road if w.phase == "specifique" for s in w.sessions
            if s.kind in (cp.QUALITY, cp.HILLS)} == {"seuil", "intervalles"}
    kind, minutes, structure = cp.quality_session("intervalles", 6)
    assert (kind, minutes, structure["text"]) == (cp.QUALITY, 55, "6 × 3 min vite, récup 2 min")
    assert [s["t"] for s in structure["steps"]] == ["echauffement", "bloc", "retour"]
    assert cp.hard_minutes(structure) == 18 and cp.main_level(kind, structure) == 4
    # the repetitions grow through a phase, from the fewest
    seuil = [s.structure["steps"][1]["reps"] for w in trail if w.phase == "specifique" for s in w.sessions
             if s.structure.get("key") == "seuil" and not w.light]
    assert seuil[0] == 2 and seuil == sorted(seuil) and seuil[-1] > seuil[0]


def test_two_quality_sessions_need_five_days_and_a_four_hour_base():
    def count(days, base):
        return max(len([s for s in w.sessions if s.kind in (cp.QUALITY, cp.HILLS)])
                   for w in cp.build(_inputs(days, base), TODAY))
    assert count((1, 2, 3, 4, 6), 240) == 2
    assert count((1, 2, 3, 4, 6), 235) == 1
    assert count((0, 2, 3, 5, 6), 240) == 1, "Wednesday and Thursday are neighbours: no room for two"
    assert count((1, 3, 5, 6), 400) == 1
    assert count((2, 6), 400) == 0 and count((1, 3, 6), 150) == 1


def test_the_same_inputs_give_the_same_plan_whatever_the_day_it_is_asked():
    inp = _inputs((1, 3, 5, 6), 180, M0 + timedelta(weeks=12, days=6))
    first = {s.day: (s.kind, s.minutes, s.structure) for w in cp.build(inp, TODAY) for s in w.sessions}
    for later in (3, 10, 31):
        again = {s.day: (s.kind, s.minutes, s.structure) for w in cp.build(inp, TODAY + timedelta(days=later))
                 for s in w.sessions}
        assert again and all(first[d] == v for d, v in again.items())


# ── one session against the longest run of the last 30 days (Frandsen 2025) ─

def test_a_session_is_held_to_ten_percent_over_the_longest_run_of_the_last_thirty_days():
    assert (cp.SPIKE, cp.SPIKE_DAYS) == (0.10, 30), "the study's threshold and its window"
    assert cp.session_limit(None) is None and cp.session_limit(0) is None, "no run to compare with: no limit"
    # 10 % more, to 5 min, rounded DOWN: 100 → 110, 131 → 144.1 → 140, 60 → 66 → 65
    assert [cp.session_limit(m) for m in (100, 131, 60, 200)] == [110, 140, 65, 220]
    # one step of 5 min is always left (a plan in 5-min steps could not grow under 50 min), never under 30 min
    assert [cp.session_limit(m) for m in (20, 30, 44, 45, 50, 59)] == [30, 35, 45, 50, 55, 60]
    for m in range(50, 400):
        assert m <= cp.session_limit(m) <= m * (1 + cp.SPIKE) + 1e-9, "from 50 min on: never more than 10 %"
    # a week of Affûtage: nothing longer than the longest run before it (its volume comes down: Bosquet 2007)
    assert [cp.session_limit(m, taper=True) for m in (20, 62, 100, 125)] == [30, 60, 100, 125]
    assert cp.session_limit(None, taper=True) is None


def _sessions(inp, today=TODAY):
    return {s.day: s for w in cp.build(inp, today) for s in w.sessions}


def test_the_real_runs_of_the_last_thirty_days_hold_the_first_week_and_the_plan_grows_from_there():
    """She ran 40 min at most this month and asks for 3 h on three days: no session of 70 min the first week."""
    days, base = (1, 3, 6), 180
    free = _inputs(days, base)
    runs = tuple((TODAY - timedelta(days=d), m) for d, m in ((3, 40), (9, 35), (20, 38), (45, 120)))
    held = cp.Inputs(free.days, free.long_day, free.base, free.start_monday, free.goal, {}, runs)
    was, now = cp.build(free, TODAY), cp.build(held, TODAY)
    assert was[0].limit is None and max(s.minutes for s in was[0].sessions) == 70
    assert now[0].limit == 45 == cp.session_limit(40), "the run of 45 days ago is outside the window"
    assert [s.minutes for s in now[0].sessions] == [45, 45, 45] and now[0].minutes == 135 < now[0].target == 180
    assert [s.kind for s in now[0].sessions] == [s.kind for s in was[0].sessions], "its quality session stays"
    # then each week reads the sessions the plan wrote before it: 10 % at most, week after week, up to the plan's own
    longest = [max(s.minutes for s in w.sessions) for w in now]
    assert longest[:6] == [45, 50, 55, 60, 65, 70] == [w.limit for w in now[:6]], "5 min a week, here"
    for i, w in enumerate(now[1:], 1):
        before = [s.minutes for x in now[:i] for s in x.sessions if (w.monday - s.day).days <= cp.SPIKE_DAYS]
        assert max(s.minutes for s in w.sessions) <= cp.session_limit(max(before))
    assert all(a.target == b.target for a, b in zip(was, now)), "the weeks' targets are the plan's, untouched"
    # a long run in the window: nothing to hold, the plan is the one without any run recorded
    long = cp.Inputs(free.days, free.long_day, free.base, free.start_monday, free.goal, {},
                     ((TODAY - timedelta(days=6), 95),))
    assert [(s.kind, s.minutes) for s in cp.build(long, TODAY)[0].sessions] == [(s.kind, s.minutes) for s in was[0].sessions]
    # a run dated after today is not a run yet
    ahead = cp.Inputs(free.days, free.long_day, free.base, free.start_monday, free.goal, {},
                      ((TODAY + timedelta(days=1), 20),))
    assert cp.build(ahead, TODAY)[0].limit is None


def test_mid_week_the_days_behind_are_the_real_runs_and_the_days_ahead_the_plans():
    inp = _inputs((1, 3, 5, 6), 240, start=M0 - timedelta(weeks=3))
    wednesday = M0 + timedelta(weeks=1, days=2)
    runs = ((wednesday - timedelta(days=1), 50), (wednesday - timedelta(days=8), 62))
    held = cp.Inputs(inp.days, inp.long_day, inp.base, inp.start_monday, inp.goal, {}, runs)
    this, nxt = cp.build(held, wednesday)[:2]
    assert this.limit == 65 == cp.session_limit(62)
    left = [s for s in this.sessions if s.day >= wednesday]
    assert left and max(s.minutes for s in left) == 65
    assert nxt.limit == cp.session_limit(65) == 70, "next week reads what this week still plans"


def test_the_starting_volume_is_the_median_of_the_complete_weeks():
    assert cp.starting_minutes([120, 200, 0, 180]) == 150  # the median, to 5 min
    assert cp.starting_minutes([122, 148, 151, 175]) == 150
    assert cp.starting_minutes([0, 0, 0, 0]) is None and cp.starting_minutes([]) is None
    assert cp.starting_minutes([10, 0, 0, 20]) == 60  # held to the floor
    assert cp.default_long_day((0, 2, 6)) == 6 and cp.default_long_day((0, 2, 4)) == 4


def test_the_ceiling_and_the_long_run_scale_with_the_race():
    ten_k, marathon, ultra = (cp.Goal(TODAY, m) for m in (60, 240, 1800))
    assert [cp.ceiling(g, 100) for g in (ten_k, marathon, ultra)] == [190, 320, 720]
    assert cp.ceiling(ten_k, 400) == 400, "never under the athlete's base"
    assert cp.ceiling(cp.Goal(), 200) == 300 and cp.ceiling(cp.Goal(), 100) == 180 and cp.ceiling(cp.Goal(), 400) == 480
    assert [cp.long_cap(g) for g in (ten_k, marathon, ultra)] == [120, 130, 210] and cp.long_cap(cp.Goal()) == 150


def test_the_rules_read_the_race_time_to_the_quarter_hour():
    """An estimate that moves by a minute from one day to the next leaves the weeks as they are; the race's own
    session keeps the exact time."""
    assert [cp.rule_minutes(cp.Goal(TODAY, m)) for m in (422, 427, 428, 437, 5)] == [420, 420, 435, 435, 15]
    a, b = (_inputs((1, 3, 5, 6), 240, TODAY + timedelta(days=75), minutes) for minutes in (422, 427))
    plain = lambda inp: [(s.day, s.kind, s.minutes) for w in cp.build(inp, TODAY) for s in w.sessions if s.kind != cp.RACE]  # noqa: E731
    assert plain(a) == plain(b)
    race = [s.minutes for inp in (a, b) for w in cp.build(inp, TODAY) for s in w.sessions if s.kind == cp.RACE]
    assert race == [422, 427]


# ── a trail race's climb ────────────────────────────────────────────────────

def _trail(rate, race=TODAY + timedelta(days=75), minutes=360, trail=True, days=(1, 3, 5, 6), base=300):
    goal = cp.Goal(race, minutes, trail, 1, "Trail des Crêtes", "objectif", rate)
    return cp.Inputs(tuple(days), 6, base, M0, goal, {})


def test_the_climb_target_follows_the_races_dplus_per_hour_capped_and_rounded():
    goal = cp.Goal(TODAY, 360, True, climb_rate=400)
    assert cp.climb_target(goal, 120) == 800 and cp.climb_target(goal, 120, 0.6) == 500, "rate × share × hours"
    assert cp.climb_target(goal, 100, 0.6) == 400 and cp.climb_target(goal, 95, 0.6) == 400, "to 50 m"
    steep = cp.Goal(TODAY, 60, True, climb_rate=1000)  # a vertical kilometre: held to 500 m an hour
    assert cp.climb_target(steep, 120) == 1000 == cp.CLIMB_RATE_CAP * 2
    assert cp.climb_target(cp.Goal(TODAY, 360, True, climb_rate=40), 90) is None, "under 100 m: no target"
    assert cp.climb_target(cp.Goal(TODAY, 240, False, climb_rate=400), 120) is None, "a road race: none"
    assert cp.climb_target(cp.Goal(None, None, True, climb_rate=400), 120) is None, "no race: none"
    assert cp.climb_target(cp.Goal(TODAY, 360, True), 120) is None


def test_the_climb_grows_week_by_week_to_the_whole_before_the_taper():
    inp = _trail(400)
    first_taper = cp.taper_start(inp.goal.race_day)
    mondays = cp.horizon(inp, TODAY)
    shares = [cp.climb_share(m, inp) for m in mondays]
    before = [s for m, s in zip(mondays, shares) if m < first_taper]
    assert before[0] == cp.CLIMB_START and before[-1] == 1.0 and before == sorted(before)
    assert len(set(round(b - a, 9) for a, b in zip(before, before[1:]))) == 1, "equal steps"
    assert all(s == 1.0 for m, s in zip(mondays, shares) if m >= first_taper)
    assert cp.climb_share(M0, cp.Inputs((1, 3, 6), 6, 200, M0, cp.Goal(), {})) == 1.0


@pytest.mark.parametrize("rate", (150, 400, 900))
def test_only_the_long_run_and_the_long_hills_carry_a_climb(rate):
    seen = set()
    for days in ((1, 3, 6), (0, 1, 3, 5, 6), (1, 2, 3, 4, 5, 6)):
        inp = _trail(rate, days=days)
        for w in cp.build(inp, TODAY):
            share = cp.climb_share(w.monday, inp)
            for s in w.sessions:
                carries = s.kind == cp.LONG or s.structure.get("key") in cp.CLIMB_KEYS
                want = cp.climb_target(inp.goal, s.minutes, share) if carries else None
                assert s.structure.get("climb") == want, (s.day, s.kind)
                if want:
                    assert want % cp.CLIMB_STEP == 0 and want >= cp.CLIMB_MIN
                    assert want <= cp.CLIMB_RATE_CAP * s.minutes / 60 + cp.CLIMB_STEP / 2
                    seen.add(s.structure.get("key") or s.kind)
    assert seen == {cp.LONG, "cotes_longues"}
    for inp in (_trail(rate, trail=False), _trail(None)):
        assert not [s for w in cp.build(inp, TODAY) for s in w.sessions if "climb" in s.structure]


# ── the ressenti's adaptation ───────────────────────────────────────────────

W1 = M0 - timedelta(weeks=1)  # the week before « this week »


def _facts(*rows):
    return [cp.Fact(W1 + timedelta(days=d), kind, status, feel) for d, kind, status, feel in rows]


def test_one_very_hard_easy_session_or_two_hard_ones_lighten_the_next_week():
    done = {W1: 150.0}
    one = _facts((1, cp.FOOTING, "done", 5), (3, cp.QUALITY, "done", 3), (6, cp.LONG, "done", 3))
    assert cp.adaptation(one, M0, done, 150) == {"reason": "ressenti", "factor": 0.8}
    two = _facts((1, cp.FOOTING, "done", 4), (6, cp.LONG, "done", 4))
    assert cp.adaptation(two, M0, done, 150) == {"reason": "ressenti", "factor": 0.8}
    # a single « Dur », or a hard quality session: the progression goes on
    assert cp.adaptation(_facts((1, cp.FOOTING, "done", 4), (6, cp.LONG, "done", 3)), M0, done, 150) is None
    assert cp.adaptation(_facts((3, cp.QUALITY, "done", 5), (4, cp.HILLS, "done", 5)), M0, done, 150) is None
    # two signals more than 7 days apart: nothing; within 7 days across two weeks: the later one's week counts
    apart = [cp.Fact(W1 - timedelta(days=6), cp.FOOTING, "done", 4), cp.Fact(W1 + timedelta(days=5), cp.RECUP, "done", 4)]
    assert cp.adaptation(apart, M0, done, 150) is None
    near = [cp.Fact(W1 - timedelta(days=1), cp.LONG, "done", 4), cp.Fact(W1 + timedelta(days=1), cp.FOOTING, "done", 4)]
    assert cp.adaptation(near, M0, done, 150) == {"reason": "ressenti", "factor": 0.8}
    assert cp.adaptation(near, W1, done, 150) is None, "the week of the first signal alone is not lightened"


def test_missed_sessions_lighten_repeat_or_restart():
    some = _facts((1, cp.FOOTING, "missed", None), (3, cp.QUALITY, "missed", None), (6, cp.LONG, "done", 3))
    assert cp.adaptation(some, M0, {W1: 70.0}, 150) == {"reason": "manquees", "factor": 0.8}
    assert cp.adaptation(_facts((1, cp.FOOTING, "missed", None), (6, cp.LONG, "done", 3)), M0, {W1: 70.0}, 150) is None
    whole = _facts((1, cp.FOOTING, "missed", None), (3, cp.QUALITY, "missed", None), (6, cp.LONG, "missed", None))
    assert cp.adaptation(whole, M0, {}, 150) == {"reason": "semaine_manquee"}
    # she ran outside the plan that week: not a week missed, « several sessions missed »
    assert cp.adaptation(whole, M0, {W1: 45.0}, 150) == {"reason": "manquees", "factor": 0.8}
    # a week still running can only lighten the next one
    assert cp.adaptation(whole, M0, {}, 150, closed=False) == {"reason": "manquees", "factor": 0.8}
    two = whole + [cp.Fact(W1 - timedelta(days=5), cp.FOOTING, "missed", None)]
    assert cp.adaptation(two, M0, {}, 95) == {"reason": "reprise", "base": 95}
    assert cp.adaptation(two, M0, {}, None) == {"reason": "reprise", "base": 60}, "no run at all: the floor"


def test_an_adaptation_changes_the_weeks_volume_and_its_quality_session():
    days, base = (1, 3, 5, 6), 200
    plain = cp.week_targets(_inputs(days, base), M0 + timedelta(weeks=8))
    key = (M0 + timedelta(weeks=2)).isoformat()
    for reason in ("ressenti", "manquees"):
        inp = _inputs(days, base, adaptations={key: {"reason": reason, "factor": 0.8}})
        light = cp.week_targets(inp, M0 + timedelta(weeks=8))
        m = M0 + timedelta(weeks=2)
        assert abs(light[m]["target"] - plain[m]["target"] * 0.8) <= 5 and light[m]["reason"] == reason
        assert {x: light[x]["target"] for x in light if x != m} == {x: plain[x]["target"] for x in plain if x != m}, \
            "the planned progression goes on after it"
        week = next(w for w in cp.build(inp, TODAY) if w.monday == m)
        assert not [s for s in week.sessions if s.kind in (cp.QUALITY, cp.HILLS)], "its quality session is a footing"
        assert week.reason == reason and cp.REASONS[reason].startswith("Semaine allégée")
    repeat = cp.week_targets(_inputs(days, base, adaptations={key: {"reason": "semaine_manquee"}}),
                             M0 + timedelta(weeks=8))
    assert repeat[M0 + timedelta(weeks=2)]["target"] == plain[M0 + timedelta(weeks=1)]["target"], "the same volume"
    assert repeat[M0 + timedelta(weeks=4)]["target"] == plain[M0 + timedelta(weeks=2)]["target"], "shifted by a week"
    restart = cp.week_targets(_inputs(days, base, adaptations={key: {"reason": "reprise", "base": 120}}),
                              M0 + timedelta(weeks=8))
    assert [restart[M0 + timedelta(weeks=i)]["target"] for i in (2, 3, 4, 5)] == [120, 130, 145, 110]


def test_the_ressenti_never_cuts_a_taper_further():
    race = M0 + timedelta(weeks=6, days=6)
    key = (cp.monday_of(race) - timedelta(weeks=1)).isoformat()
    plain = cp.build(_inputs((1, 3, 5, 6), 240, race), TODAY)
    light = cp.build(_inputs((1, 3, 5, 6), 240, race, adaptations={key: {"reason": "ressenti", "factor": 0.8}}), TODAY)
    a, b = plain[-2], light[-2]
    assert a.target == b.target and b.reason == "ressenti"
    assert [s for s in a.sessions if s.kind == cp.QUALITY] and not [s for s in b.sessions if s.kind == cp.QUALITY]


def test_a_person_s_session_gets_a_structure_that_fits_its_duration():
    for kind in cp.EDITABLE:
        for minutes in (20, 30, 45, 60, 90, 180):
            structure = cp.default_structure(kind, minutes)
            total = sum(s.get("min", 0) or s["reps"] * s["effort"] + (s["reps"] - 1) * s["recup"]
                        for s in structure["steps"])
            assert total == minutes, (kind, minutes, structure)
    assert cp.default_structure(cp.QUALITY, 55)["text"] == "3 × 8 min soutenu, récup 2 min"
    assert cp.describe(cp.FOOTING, cp.default_structure(cp.FOOTING, 45)) == ""
    assert cp.main_level(cp.RECUP, cp.default_structure(cp.RECUP, 30)) == 1


# ── the form: a low morning ─────────────────────────────────────────────────

def test_the_form_cites_the_hrv_guided_studies_and_marks_the_rest_h():
    doc = (ROOT / "docs/coaching.md").read_text(encoding="utf-8")
    for source in ("Kiviniemi", "Vesterinen", "Javaloyes", "10.1007/s00421-007-0552-2", "10.1249/MSS.0000000000000910",
                   "10.1123/ijspp.2018-0122"):
        assert source in doc, source


def test_the_footing_of_a_low_morning_is_shorter_and_capped_but_keeps_a_tapers_duration():
    assert [cp.form_footing(m) for m in (35, 45, 55, 70, 90, 150, 210)] == [30, 30, 35, 40, 45, 45, 45]
    assert all(cp.MIN_SESSION <= cp.form_footing(m) <= min(m, cp.FORM_FOOTING_CAP) for m in range(30, 400, 5))
    assert [cp.form_footing(m, taper=True) for m in (35, 60)] == [35, 60], "a taper's volume is never cut"


def test_three_low_mornings_within_five_days_lighten_the_week_after_the_last_of_them():
    d = lambda k: M0 + timedelta(days=k)  # noqa: E731
    nxt = M0 + timedelta(weeks=1)
    assert cp.form_week([d(0), d(2), d(4)], nxt), "Monday, Wednesday, Friday: three within five days"
    assert not cp.form_week([d(0), d(2), d(5)], nxt), "the third is the sixth day"
    assert not cp.form_week([d(0), d(1)], nxt) and not cp.form_week([], nxt)
    assert cp.form_week([d(-2), d(-1), d(1)], nxt), "across two weeks, the last of them this week"
    assert not cp.form_week([d(0), d(1), d(2)], nxt + timedelta(weeks=1)), "only the week right after"
    assert cp.form_week([d(-3), d(-2), d(-1)], M0) and not cp.form_week([d(-3), d(-2), d(-1)], nxt)


def test_the_form_and_the_ressenti_never_stack():
    assert cp.with_form(None) == {"reason": "forme", "factor": cp.ADAPT_FACTOR}
    ressenti = {"reason": "ressenti", "factor": cp.ADAPT_FACTOR}
    assert cp.with_form(ressenti) is ressenti, "the same reduction: one, under the ressenti's name"
    assert cp.with_form({"reason": "manquees", "factor": 0.9}) == {"reason": "forme", "factor": cp.ADAPT_FACTOR}
    assert cp.with_form({"reason": "ressenti", "factor": 0.7})["factor"] == 0.7, "the larger reduction wins"
    for other in ({"reason": "semaine_manquee"}, {"reason": "reprise", "base": 90}):
        assert cp.with_form(other) is other
    plain = _inputs((1, 3, 5, 6), 240, start=M0 - timedelta(weeks=1))
    nxt = (M0 + timedelta(weeks=1)).isoformat()
    form = cp.Inputs(plain.days, plain.long_day, plain.base, plain.start_monday, plain.goal,
                     {nxt: {"reason": "forme", "factor": 0.8}})
    a, b = (cp.week_targets(i, M0 + timedelta(weeks=1))[M0 + timedelta(weeks=1)] for i in (plain, form))
    assert b["target"] == cp.round5(a["target"] * 0.8) and b["easy"] and b["reason"] == "forme"


@pytest.mark.parametrize("inp", CASES[::3])
def test_a_moved_hard_session_never_makes_two_hard_days_in_a_row(inp):
    """On every day of the first weeks that holds a hard session: where it would go on a low morning keeps the rule
    (today is a footing by then), is an available day of the same week, later than today, free or holding one easy
    session, and never puts a long run within three days of the race."""
    weeks = cp.build(inp, TODAY)[:4]
    sessions = [s for w in weeks for s in w.sessions]
    rd = inp.goal.race_day
    for s in sessions:
        if s.kind not in (cp.LONG, cp.QUALITY, cp.HILLS):
            continue
        slots = [cp.Slot(x.day, x.kind) for x in sessions if s.day <= x.day <= cp.monday_of(s.day) + timedelta(days=7)]
        to = cp.move_day(s.day, inp.days, slots, s.kind, rd)
        if to is None:
            continue
        assert s.day < to <= cp.monday_of(s.day) + timedelta(days=6) and to.weekday() in inp.days
        there = [x for x in sessions if x.day == to]
        assert len(there) <= 1 and all(x.kind in (cp.FOOTING, cp.RECUP) for x in there)
        hard = {x.day for x in sessions if x.kind in cp.HARD and x.day != s.day} | {to}
        ordered = sorted(hard)
        assert all((b - a).days >= 2 for a, b in zip(ordered, ordered[1:])), (s.day, to)
        if s.kind == cp.LONG and rd is not None:
            assert not 0 < (rd - to).days <= cp.NO_LONG_BEFORE_RACE


def test_a_session_a_person_holds_is_never_the_target_of_a_move():
    mon = M0
    slots = [cp.Slot(mon + timedelta(days=1), cp.QUALITY), cp.Slot(mon + timedelta(days=3), cp.FOOTING, free=False),
             cp.Slot(mon + timedelta(days=5), cp.FOOTING), cp.Slot(mon + timedelta(days=6), cp.LONG)]
    days = (1, 3, 4, 5, 6)
    assert cp.move_day(mon + timedelta(days=1), days, slots, cp.QUALITY) == mon + timedelta(days=4), \
        "Thursday is pinned, Friday is free and empty"
    assert cp.move_day(mon + timedelta(days=1), (1, 3, 5, 6), slots, cp.QUALITY) is None, \
        "Thursday pinned, Saturday the eve of the long run: dropped"
