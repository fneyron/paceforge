"""services/coaching_slots: where a session goes in the day (Coaching › « Mon agenda », docs/coaching.md). The
rules, then properties over many generated weeks and many random calendars: a time never overlaps busy time, sits in
a moment the athlete chose, a full day sends its session to another available day without two hard days in a row,
a placed time is kept while it is free, and nothing a person holds ever moves."""
import random
import re
from datetime import date, timedelta
from pathlib import Path

import pytest

from app.services import coaching_plan as cp
from app.services import coaching_slots as cs

ROOT = Path(__file__).resolve().parent.parent
TODAY = date(2026, 11, 23)  # a Monday
ALL = cs.MOMENTS

HEURISTICS = {"WINDOWS": {"matin": (360, 540), "midi": (705, 840), "soir": (1050, 1260)}, "MARGIN": 30, "CYCLE": 30,
              "USUAL_WEEKS": 8, "USUAL_MIN_RUNS": 3}


@pytest.mark.parametrize("name,value", HEURISTICS.items())
def test_each_heuristic_is_pinned_and_marked_h(name, value):
    assert getattr(cs, name) == value
    source = (ROOT / "app/services/coaching_slots.py").read_text(encoding="utf-8")
    line = next(ln for ln in source.splitlines() if re.match(rf"{name}\s*=", ln))
    assert "(H)" in line, f"{name} is a heuristic: its line says (H)"
    assert f"`{name}`" in (ROOT / "docs/coaching.md").read_text(encoding="utf-8"), "and the page says it"


def _item(key, day, kind=cp.FOOTING, minutes=45, **more):
    more.setdefault("home", day)
    more.setdefault("movable", True)
    return cs.Item(key=key, day=day, kind=kind, minutes=minutes, **more)


def _agenda(busy=None, away=(), until=None):
    return cs.Agenda(busy or {}, frozenset(away), until)


D = [TODAY + timedelta(days=k) for k in range(14)]


# ── one day ─────────────────────────────────────────────────────────────────

def test_a_session_needs_its_duration_and_the_margin_free_inside_a_moment():
    assert cs.candidates(45, cp.FOOTING, [], ALL)[0] == 6 * 60, "an easy session: the first moment that has room"
    # busy 6:00–8:00: 45 + 30 min no longer fit before 9:00 → midday
    assert cs.candidates(45, cp.FOOTING, [(360, 480)], ALL)[0] == 11 * 60 + 45
    assert cs.candidates(30, cp.FOOTING, [(360, 480)], ALL)[0] == 8 * 60, "30 + 30 fit from 8:00 to 9:00"
    # a meeting at 13:00: the session and its shower must end before it
    assert cs.candidates(45, cp.FOOTING, [(0, 705), (780, 840)], ALL)[0] == 11 * 60 + 45
    assert cs.candidates(50, cp.FOOTING, [(0, 705), (780, 840)], ALL)[0] == 17 * 60 + 30, "80 min do not fit before 13:00"
    assert cs.candidates(45, cp.FOOTING, [(0, 24 * 60)], ALL) == [], "a full day"
    assert cs.is_free(735, 45, [(690, 735), (810, 870)]) and not cs.is_free(735, 46, [(810, 870)])
    for t in cs.candidates(60, cp.FOOTING, [(400, 430), (700, 760), (1100, 1130)], ALL):
        assert t % cs.STEP == 0 and cs.is_free(t, 60, [(400, 430), (700, 760), (1100, 1130)])


def test_the_moment_follows_a_fixed_order_by_kind():
    assert cs.order(cp.LONG, ALL) == ("matin", "midi", "soir"), "the long run in the morning"
    assert cs.order(cp.QUALITY, ALL) == ("soir", "matin", "midi"), "a quality session in the evening, unless…"
    assert cs.order(cp.HILLS, ALL, usual="midi") == ("midi", "soir", "matin"), "…the athlete really trains at midday"
    assert cs.order(cp.FOOTING, ALL) == cs.order(cp.RECUP, ALL) == ("matin", "midi", "soir")
    assert cs.order(cp.QUALITY, ("matin", "midi")) == ("matin", "midi"), "only the moments the athlete chose"
    assert cs.candidates(55, cp.QUALITY, [], ALL)[0] == 17 * 60 + 30
    assert cs.candidates(55, cp.QUALITY, [], ALL, usual="matin")[0] == 6 * 60
    assert cs.candidates(120, cp.LONG, [], ALL)[0] == 6 * 60
    assert cs.candidates(120, cp.LONG, [], ("soir",))[0] == 17 * 60 + 30
    assert cs.clean_moments(["soir", "MATIN", "x"]) == ("matin", "soir") and cs.clean_moments([]) == ALL
    assert cs.clean_moments(None) == ALL == ("matin", "midi", "soir")


def test_a_session_longer_than_the_moment_starts_at_its_start():
    """3 h 30 + 30 min fit in no moment: the long run starts at 6:00 and runs past 9:00, if those hours are free."""
    assert cs.candidates(210, cp.LONG, [], ALL) == [360, 705, 1050]
    assert cs.candidates(210, cp.LONG, [(570, 630)], ALL) == [705, 1050], "a meeting at 9:30 is in its way"
    assert cs.candidates(210, cp.LONG, [(600, 720)], ALL) == [360, 1050], "one from 10:00 leaves the morning"


def test_the_usual_moment_is_where_the_runs_really_start():
    assert cs.usual_moment([]) is None and cs.usual_moment([420, 425]) is None, "under 3 runs: not known"
    assert cs.usual_moment([420, 425, 1100, 430]) == "matin"
    assert cs.usual_moment([735, 760, 1100, 800, 400]) == "midi"
    assert cs.usual_moment([1150, 1200, 1300, 420]) == "soir", "22:00 is nearest the evening"
    assert cs.usual_moment([600, 610, 620]) == "matin", "10:00 is nearer 9:00 than 11:45"


def test_another_slot_cycles_through_the_days_free_times():
    agenda = _agenda({D[1]: [(360, 540), (720, 780)]})
    item = _item(1, D[1], minutes=45, start=1050)
    seen, t = [], 1050
    for _ in range(12):
        item.start = t
        t = cs.next_choice(item, agenda, ALL, TODAY)
        seen.append(t)
    slots = cs.candidates(45, cp.FOOTING, agenda.busy[D[1]], ALL)
    assert slots[0] == 13 * 60 + 15 - 15 or slots[0] in slots  # the first free time of the day
    assert set(seen) == set(slots) and len(slots) > 3 and all(b - a >= cs.CYCLE for a, b in zip(slots, slots[1:]) if b > a)
    assert cs.next_choice(_item(2, D[2], start=None), _agenda({D[2]: [(0, 1440)]}), ALL, TODAY) is None
    assert cs.next_choice(_item(3, D[3], start=360), _agenda(away=[D[3]]), ALL, TODAY) is None


# ── a week ──────────────────────────────────────────────────────────────────

def test_each_session_gets_the_first_free_time_of_its_day():
    items = [_item(1, D[1], cp.FOOTING, 45), _item(2, D[3], cp.QUALITY, 55), _item(3, D[6], cp.LONG, 120)]
    agenda = _agenda({D[1]: [(9 * 60, 12 * 60), (14 * 60, 18 * 60)], D[3]: [(17 * 60, 19 * 60)]})
    out = cs.place(items, agenda, (1, 3, 5, 6), ALL, TODAY)
    assert (out[1].day, out[1].start) == (D[1], 6 * 60)
    assert (out[2].day, out[2].start) == (D[3], 19 * 60), "the evening, after the meeting"
    assert (out[3].day, out[3].start) == (D[6], 6 * 60)
    assert all(p.flag is None and p.moved_from is None for p in out.values())


def test_a_full_day_sends_its_session_to_the_nearest_available_day():
    full = [(0, 24 * 60)]
    items = [_item(1, D[1], cp.FOOTING, 45), _item(2, D[3], cp.QUALITY, 55), _item(3, D[6], cp.LONG, 120)]
    out = cs.place(items, _agenda({D[1]: full}), (1, 2, 3, 6), ALL, TODAY)
    assert (out[1].day, out[1].moved_from, out[1].start) == (D[2], D[1], 360), "Tuesday is full: Wednesday"
    # the quality session's day is full: Wednesday and Friday are free and available, the later on a tie… but
    # never the eve of the long run
    out = cs.place(items, _agenda({D[3]: full}), (1, 2, 3, 4, 5, 6), ALL, TODAY)
    assert out[2].day == D[4] and out[2].moved_from == D[3]
    out = cs.place(items, _agenda({D[3]: full}), (1, 3, 5, 6), ALL, TODAY)
    assert (out[2].day, out[2].start, out[2].flag) == (D[3], None, cs.FULL), "Saturday is the eve of the long run"
    # an « away » day counts as full, a day that is not available is never a target, nor one that holds a session
    out = cs.place(items, _agenda(away=[D[1]]), (1, 3, 6), ALL, TODAY)
    assert (out[1].day, out[1].flag) == (D[1], cs.FULL)
    out = cs.place(items, _agenda(away=[D[1], D[2]]), (1, 2, 4, 3, 6), ALL, TODAY)
    assert out[1].day == D[4], "Wednesday is away too, Thursday holds the quality session: Friday"


def test_only_a_generated_session_nobody_touched_moves_and_never_todays():
    full = [(0, 24 * 60)]
    held = _item(1, D[2], cp.FOOTING, 45, movable=False)  # moved by hand, or a coach's
    out = cs.place([held], _agenda({D[2]: full}), (1, 2, 3), ALL, TODAY)
    assert (out[1].day, out[1].start, out[1].flag) == (D[2], None, cs.FULL), "it keeps its day"
    out = cs.place([held], _agenda({D[2]: [(360, 540)]}), (1, 2, 3), ALL, TODAY)
    assert (out[1].day, out[1].start) == (D[2], 11 * 60 + 45), "and only gets a time"
    today = _item(2, TODAY, cp.FOOTING, 45)
    out = cs.place([today], _agenda({TODAY: full}), (0, 1, 2), ALL, TODAY)
    assert (out[2].day, out[2].flag) == (TODAY, cs.FULL), "today's session is never sent to another day"
    out = cs.place([today], _agenda(), (0, 1, 2), ALL, TODAY, now_min=15 * 60)
    assert (out[2].day, out[2].start) == (TODAY, 17 * 60 + 30), "today: a time still ahead"
    out = cs.place([today], _agenda(), (0, 1, 2), ALL, TODAY, now_min=22 * 60)
    assert (out[2].day, out[2].start, out[2].flag) == (TODAY, None, None), "the hours have passed: nothing is said"
    race = cs.Item(key=3, day=D[5], kind=cp.RACE, minutes=300, fixed=True)
    done = cs.Item(key=4, day=D[1], kind=cp.FOOTING, minutes=40, fixed=True)
    out = cs.place([race, done, _item(5, D[4], cp.QUALITY, 50)], _agenda({D[4]: full}), (1, 3, 4, 5, 6), ALL, TODAY)
    assert set(out) == {5} and out[5].day in (D[2], D[3]), "the race and a done session are never placed; nor crowded"


def test_a_placed_time_is_kept_while_it_is_free_and_a_chosen_one_whatever_happens():
    placed = _item(1, D[2], cp.FOOTING, 45, start=18 * 60)
    out = cs.place([placed], _agenda({D[2]: [(8 * 60, 9 * 60)]}), (1, 2, 3), ALL, TODAY)
    assert out[1].start == 18 * 60, "still free: no flapping to the morning"
    out = cs.place([placed], _agenda({D[2]: [(18 * 60 + 30, 19 * 60)]}), (1, 2, 3), ALL, TODAY)
    assert out[1].start == 6 * 60, "a meeting landed on it: found again"
    chosen = _item(2, D[2], cp.FOOTING, 45, start=18 * 60, chosen=True, movable=False)
    out = cs.place([chosen], _agenda({D[2]: [(0, 24 * 60)]}), (1, 2, 3), ALL, TODAY)
    assert (out[2].day, out[2].start, out[2].flag) == (D[2], 18 * 60, None), "the athlete's own choice stands"
    # a session the calendar sent elsewhere stays there while its time is free; it comes home when it must be
    # placed again and its own day has room
    moved = _item(3, D[2], cp.FOOTING, 45, home=D[1], start=6 * 60)
    out = cs.place([moved], _agenda({D[1]: [(0, 24 * 60)]}), (1, 2, 3), ALL, TODAY)
    assert (out[3].day, out[3].start, out[3].moved_from) == (D[2], 6 * 60, D[1])
    out = cs.place([moved], _agenda({D[2]: [(0, 24 * 60)]}), (1, 2, 3), ALL, TODAY)
    assert (out[3].day, out[3].moved_from) == (D[1], None)


def test_beyond_the_busy_time_known_nothing_is_placed():
    items = [_item(1, D[2]), _item(2, D[9])]
    out = cs.place(items, _agenda(until=D[7]), (1, 2, 3), ALL, TODAY)
    assert out[1].start == 360 and (out[2].day, out[2].start, out[2].flag) == (D[9], None, None)


# ── properties: many generated weeks, many calendars ────────────────────────

def _weeks():
    rng = random.Random(20261123)
    for n in range(120):
        days = tuple(sorted(rng.sample(range(7), rng.randint(2, 6))))
        base = rng.choice((90, 150, 200, 240, 300, 420))
        race = rng.choice((None, TODAY + timedelta(days=rng.randint(9, 120))))
        goal = cp.Goal(race, rng.choice((120, 300, 600)), True, 1, "Course") if race else cp.Goal()
        inp = cp.Inputs(days, cp.default_long_day(days), base, TODAY, goal, {})
        busy, away = {}, set()
        for k in range(14):
            d = TODAY + timedelta(days=k)
            roll = rng.random()
            if roll < 0.12:
                busy[d] = [(0, 24 * 60)]
            elif roll < 0.18:
                away.add(d)
            elif roll < 0.85:
                spans = []
                for _ in range(rng.randint(1, 6)):
                    a = rng.randrange(6 * 60, 21 * 60, 15)
                    spans.append((a, a + rng.choice((30, 45, 60, 90, 120, 240))))
                busy[d] = sorted(spans)
        moments = tuple(m for m in cs.MOMENTS if rng.random() < 0.8) or cs.MOMENTS
        yield n, inp, cs.Agenda(busy, frozenset(away), None), moments, rng.choice((None, "matin", "midi", "soir"))


CASES = list(_weeks())


def _items(inp, rng=None):
    out = []
    for n, s in enumerate(x for w in cp.build(inp, TODAY)[:2] for x in w.sessions):
        fixed = s.kind == cp.RACE
        out.append(cs.Item(key=n, day=s.day, kind=s.kind, minutes=s.minutes, home=s.day, movable=not fixed, fixed=fixed))
    return out


@pytest.mark.parametrize("n,inp,agenda,moments,usual", CASES)
def test_every_placement_keeps_the_rules(n, inp, agenda, moments, usual):
    items = _items(inp)
    out = cs.place(items, agenda, inp.days, moments, TODAY, 0, usual)
    by = {i.key: i for i in items}
    assert set(out) == {i.key for i in items if not i.fixed}
    final = {i.key: (out[i.key].day if i.key in out else i.day) for i in items}
    for key, p in out.items():
        i = by[key]
        if p.start is not None:
            lo_hi = [cs.WINDOWS[m] for m in moments]
            assert any(lo <= p.start <= hi for lo, hi in lo_hi), "starts in a moment the athlete chose"
            assert p.start % cs.STEP == 0 and p.day not in agenda.away
            assert cs.is_free(p.start, i.minutes, agenda.busy.get(p.day, [])), "the session and its margin are free"
            assert p.flag is None
        else:
            assert p.day == i.day and p.flag == cs.FULL, "no room anywhere: the day stays, flagged"
            assert not cs.candidates(i.minutes, i.kind, agenda.busy.get(i.day, []), moments, usual) or i.day in agenda.away
        if p.day != i.day:
            assert p.moved_from == i.day and i.day > TODAY and p.day > TODAY, "only a future session moves"
            assert cp.monday_of(p.day) == cp.monday_of(i.day) and p.day.weekday() in inp.days
            assert not cs.candidates(i.minutes, i.kind, agenda.busy.get(i.day, []), moments, usual) or i.day in agenda.away
        else:
            assert p.moved_from is None
    days = sorted(final.values())
    assert len(set(days)) == len(days), "never two sessions on one day"
    hard = sorted(final[i.key] for i in items if i.kind in cs.HARD_KINDS)
    before = sorted(i.day for i in items if i.kind in cs.HARD_KINDS)
    assert all((b - a).days >= 2 for a, b in zip(before, before[1:]))
    assert all((b - a).days >= 2 for a, b in zip(hard, hard[1:])), "never two hard days in a row"
    # stable: placing again from the result changes nothing; nor does placing a third time
    again = [cs.Item(key=i.key, day=final[i.key], kind=i.kind, minutes=i.minutes, home=i.home, movable=i.movable,
                     fixed=i.fixed, start=out[i.key].start if i.key in out else None) for i in items]
    second = cs.place(again, agenda, inp.days, moments, TODAY, 0, usual)
    assert {k: (p.day, p.start, p.flag) for k, p in second.items()} == {k: (p.day, p.start, p.flag) for k, p in out.items()}


@pytest.mark.parametrize("n,inp,agenda,moments,usual", CASES[::4])
def test_without_any_busy_time_every_session_keeps_its_day_and_gets_a_time(n, inp, agenda, moments, usual):
    items = _items(inp)
    out = cs.place(items, cs.Agenda(), inp.days, cs.MOMENTS, TODAY, 0, usual)
    for i in items:
        if not i.fixed:
            assert out[i.key].day == i.day and out[i.key].start is not None and out[i.key].flag is None
