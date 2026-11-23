"""Coaching › la cible (docs/coaching.md › « L’intensité »): the critical speed read from the athlete's own runs
(Smyth & Muniz-Pumares 2020), the paces and heart-rate ranges it gives each kind of session, the ticket's « Cible »
row — planned, done, for the person who follows the plan, without data — and the calendar feed's line."""
import re
from datetime import date, timedelta

import pytest

from app.models.coaching import CoachingLink
from app.services import coaching as co
from app.services import coaching_pace as pace
from app.services import coaching_plan as cp
from app.services import coaching_view as cv
from tests.test_coaching import HTMX, _as, _past_session, _plan, _rows, _run, beta, people  # noqa: F401

CS, D_PRIME = 3.69, 136.0  # Smyth & Muniz-Pumares 2020's best model: 3.69 m/s, 136 m


def _effort(distance: float, cs: float = CS, d_prime: float = D_PRIME) -> pace.Effort:
    """An effort on the line distance = D′ + CS × time."""
    return pace.Effort(distance, (distance - d_prime) / cs)


def _runs(efforts, n: int = pace.MIN_RUNS, sport: str = "Run") -> list[pace.Run]:
    return [pace.Run(sport, tuple(efforts))] + [pace.Run(sport) for _ in range(n - 1)]


# ── the critical speed (pure) ───────────────────────────────────────────────

def test_the_constants_are_the_paper_s_and_the_marked_choices():
    assert (pace.WINDOW_DAYS, pace.MIN_RUNS) == (112, 24), "16 weeks, 24 runs (Smyth & Muniz-Pumares 2020)"
    assert pace.TARGETS == (400, 800, 1000, 1500, 3000, 5000) and pace.BEST_MODEL == (400, 800, 5000)
    assert pace.EASY_CAP == 0.823, "the first threshold at 82.3 % of CS (Hunter 2024)"
    assert pace.THRESHOLD == (0.93, 0.97), "MLSS at 92.7 % of CS, V̇O2 steady 0.5 km/h under it (Nixon 2021)"
    assert pace.VO2MAX_TIME_S == (505, 303), "the time held at the speed of V̇O2max, 404 ± 101 s (Billat 1994)"
    assert cp.HR_FAST == (90, 95) and cp.LAG_MIN == 3, "90–95 % of the maximum (Helgerud 2007), 3 min (H)"


def test_enough_data_gives_the_line_through_400_800_and_5000_m():
    c = pace.estimate(_runs([_effort(400), _effort(805), _effort(1000), _effort(5000), _effort(10000)]))
    assert c is not None and c.runs == pace.MIN_RUNS
    assert c.cs == pytest.approx(CS, abs=1e-6) and c.d_prime == pytest.approx(D_PRIME, abs=1e-3)
    assert [round(e.distance) for e in c.points] == [400, 805, 5000], "the best model, Strava's 1/2 mile for 800 m"
    # the fastest effort of each distance wins, whichever run it comes from
    slow = [pace.Effort(e.distance, e.seconds * 1.1) for e in c.points]
    assert pace.estimate(_runs(slow) + [pace.Run("Run", c.points)]).cs == pytest.approx(CS, abs=1e-6)


def test_without_the_best_model_three_distances_from_1000_m_to_3000_m_or_none():
    c = pace.estimate(_runs([_effort(1000), _effort(1609), _effort(5000)]))
    assert c is not None and c.cs == pytest.approx(CS, abs=1e-6)
    assert pace.estimate(_runs([_effort(400), _effort(5000)])) is None, "two distances: not the method"
    assert pace.estimate(_runs([_effort(400), _effort(805), _effort(1000)])) is None, "all short: not spread"
    assert pace.estimate(_runs([_effort(1609), _effort(3219), _effort(5000)])) is None, "all long: not spread"


def test_too_little_data_gives_nothing():
    efforts = [_effort(400), _effort(805), _effort(5000)]
    assert pace.estimate(_runs(efforts, n=pace.MIN_RUNS - 1)) is None, "23 runs in 16 weeks"
    rides = _runs(efforts, n=pace.MIN_RUNS - 1) + [pace.Run("Ride"), pace.Run("VirtualRun")]
    assert pace.estimate(rides) is None, "other sports do not count"
    assert pace.estimate([]) is None


def test_garbage_is_left_out_never_a_default():
    # faster than the world record over its distance: a GPS jump
    assert not pace.plausible(pace.Effort(400, 40)) and pace.plausible(pace.Effort(400, 44))
    assert pace.strava_efforts([{"distance": 400, "elapsed_time": 30}, {"distance": 805, "elapsed_time": None},
                                {"distance": "x"}, "nonsense", {"distance": 21097, "elapsed_time": 6000}]) == []
    assert pace.strava_efforts("not a list") == []
    # D′ not positive (a 400 m slower than the line), or far beyond Smyth & Muniz-Pumares' 136 ± 39 m
    assert pace.estimate(_runs([_effort(d, d_prime=-50) for d in (400, 805, 5000)])) is None
    assert pace.estimate(_runs([_effort(d, d_prime=600) for d in (400, 805, 5000)])) is None
    # a walker's line, outside CS_RANGE
    assert pace.estimate(_runs([_effort(d, cs=1.2, d_prime=50) for d in (400, 805, 5000)])) is None
    assert pace.fit([]) is None and pace.fit([pace.Effort(400, 90), pace.Effort(800, 90)]) is None


def test_an_effort_run_downhill_is_left_out_when_the_altitude_is_known():
    efforts = [{"distance": 400, "elapsed_time": 80, "start_index": 0, "end_index": 80},
               {"distance": 5000, "elapsed_time": 1400, "start_index": 100, "end_index": 1500}]
    streams = {"altitude": [500.0] * 81 + [495.0] * 19 + [500.0 - 0.1 * i for i in range(1401)]}
    kept = pace.strava_efforts(efforts, streams)
    assert [e.distance for e in kept] == [400], "the 5 km lost 140 m (2.8 %): left out; the 400 m lost 0"
    assert len(pace.strava_efforts(efforts)) == 2, "no altitude: nothing to say"
    assert pace.downhill(100, 95, 400) and not pace.downhill(100, 97, 400) and not pace.downhill(None, 95, 400)


def test_the_fastest_rolling_efforts_from_the_streams():
    t = list(range(0, 1800))
    d = [3.0 * s for s in t]  # 3 m/s, …
    for s in range(600, 800):  # 200 s at 4 m/s in the middle
        d[s] = d[599] + 4.0 * (s - 599)
    for s in range(800, 1800):
        d[s] = d[799] + 3.0 * (s - 799)
    got = {round(e.distance): e.seconds for e in pace.stream_efforts(t, d)}
    assert got[400] == pytest.approx(100, abs=1) and got[800] == pytest.approx(200, abs=1)
    assert got[5000] < 5000 / 3.0
    # a GPS jump (a step faster than MAX_STEP_SPEED) or a gap makes every window across it unusable
    jumped = [x + (300 if s >= 700 else 0) for s, x in enumerate(d)]
    assert {round(e.distance): e.seconds for e in pace.stream_efforts(t, jumped)}[400] > 99
    gap = t[:700] + [s + 60 for s in t[700:]]
    assert 5000 not in {round(e.distance) for e in pace.stream_efforts(gap, [3.0 * s for s in range(1800)])}
    # downhill windows are skipped; too few points give nothing
    alt = [1000 - 0.05 * x for x in [3.0 * s for s in t]]  # a steady 5 % descent
    assert pace.stream_efforts(t, [3.0 * s for s in t], alt) == []
    assert pace.stream_efforts([0], [0]) == [] and pace.stream_efforts([0, 1], [0, None]) == []


async def test_the_loader_reads_the_runs_of_the_window_once(db_session, people, same_today):
    athlete = people.athlete
    for n in range(pace.MIN_RUNS):
        run = _run(athlete, same_today - timedelta(days=2 + 4 * n), 40, n)
        run.best_efforts = [{"distance": d, "elapsed_time": round((d - D_PRIME) / CS)} for d in (400, 805, 5000)] \
            if n == 3 else []
        db_session.add(run)
    old = _run(athlete, same_today - timedelta(days=200), 40, 900)
    old.best_efforts = [{"distance": 400, "elapsed_time": 50}]  # outside the 16 weeks: not read
    db_session.add(old)
    await db_session.flush()
    c = await pace.for_athlete(db_session, athlete.id, same_today)
    assert c is not None and c.cs == pytest.approx(CS, abs=0.01)
    # a run without best efforts but with streams: its rolling efforts count
    plain = _run(athlete, same_today - timedelta(days=1), 30, 950)
    plain.streams_data = {"time": list(range(1500)), "distance": [4.5 * s for s in range(1500)]}
    db_session.add(plain)
    await db_session.flush()
    assert (await pace.for_athlete(db_session, athlete.id, same_today)).cs > CS
    assert (await pace.for_athlete(db_session, athlete.id, same_today, streams=False)).cs == pytest.approx(CS, abs=0.01)
    assert await pace.for_athlete(db_session, people.coach.id, same_today) is None, "no runs: nothing"


# ── the targets, kind by kind (pure) ────────────────────────────────────────

C = pace.Critical(CS, D_PRIME, (), 30)


def _t(kind, structure, reference=186, critical=C, goal=None):
    return cv.target(kind, structure, reference=reference, critical=critical, goal=goal)


def _vals(t):
    return [(v["label"], v["value"], v["unit"]) for v in t["vals"]]


def test_each_kind_of_session_has_its_target():
    q = {key: cp.quality_session(key, 4) for key in cp.TEMPLATES}
    # easy and very easy: « pas plus vite que » 82.3 % of CS, and the app's zones
    easy = f"Pas plus vite que, {cv.FLAT}"
    assert _vals(_t(cp.FOOTING, cp.steady(40, 2))) == [(easy, "5:30", "/km"), ("Cardio", "112–148", "bpm")]
    assert _vals(_t(cp.RECUP, cp.steady(30, 1))) == [(easy, "5:30", "/km"), ("Cardio", "93–130", "bpm")]
    assert _vals(_t(cp.LONG, cp.steady(90, 2))) == [(easy, "5:30", "/km"), ("Cardio", "112–148", "bpm")]
    climb = {**cp.steady(90, 2), "climb": 600}
    assert _vals(_t(cp.LONG, climb)) == [("Cardio", "112–148", "bpm")], "a climb to aim at: no pace"
    # threshold efforts: 93–97 % of CS, zone 4; intervals: the speed of V̇O2max on the athlete's line, 90–95 %
    seuil = _t(q["seuil"][0], q["seuil"][2])
    assert seuil["efforts"] and _vals(seuil) == [(f"Allure, {cv.FLAT}", "4:40–4:50", "/km"), ("Cardio", "149–167", "bpm")]
    inter = _t(q["intervalles"][0], q["intervalles"][2])
    assert _vals(inter) == [(f"Allure, {cv.FLAT}", "4:00–4:15", "/km"), ("Cardio en fin d’effort", "168–177", "bpm")]
    # hills: never a pace; long hills (4 min) a heart rate, short hills (1 min) the feel
    assert _vals(_t(q["cotes_longues"][0], q["cotes_longues"][2])) == [("Cardio en fin d’effort", "168–177", "bpm")]
    for key in ("cotes_courtes", "fartlek", "rappels"):
        t = _t(q[key][0], q[key][2])
        assert t["vals"] == [] and t["note"] == cv.rp.fr(cv.TARGET_SHORT), key + ": too short for the heart rate"
    # the fixed session: its content is the club's
    assert _t(cp.QUALITY, cp.fixed_structure(cp.Fixed(1, 75, "Club"))) is None
    # a session a person made: the same rules
    assert _vals(_t(cp.QUALITY, cp.default_structure(cp.QUALITY, 50)))[0][1] == "4:40–4:50"
    assert _vals(_t(cp.QUALITY, cp.steady(30, 3))) == [("Cardio", "149–167", "bpm")], "a steady « Séance »: no pace"


def test_the_race_says_its_pace_only_for_a_road_race_with_an_objective():
    road = cp.Goal(date(2027, 4, 24), 50.0, False, None, "10 km", co.OBJECTIVE, None, 10.0)
    assert _vals(_t(cp.RACE, cp.steady(50, 3, "course"), goal=road)) == [("Allure de course", "5:00", "/km")]
    assert _t(cp.RACE, cp.steady(50, 3, "course"), goal=cp.Goal(road.race_day, 50.0, False, None, "x", co.ESTIMATE,
                                                                  None, 10.0)) is None, "an estimate is not a target"
    assert _t(cp.RACE, cp.steady(300, 3, "course"), goal=cp.Goal(road.race_day, 300.0, True, 1, "Trail",
                                                                   co.OBJECTIVE, 400, 42.0)) is None, "a trail"
    assert _t(cp.RACE, cp.steady(50, 3, "course")) is None


def test_nothing_known_shows_nothing_but_what_a_maximum_would_give():
    t = _t(cp.FOOTING, cp.steady(40, 2), reference=None, critical=None)
    assert t == {"efforts": False, "vals": [], "note": None, "hr": True}
    only_pace = _t(cp.FOOTING, cp.steady(40, 2), reference=None)
    assert _vals(only_pace) == [(f"Pas plus vite que, {cv.FLAT}", "5:30", "/km")]
    assert _vals(_t(cp.FOOTING, cp.steady(40, 2), critical=None)) == [("Cardio", "112–148", "bpm")]


def test_the_calendar_feed_says_the_target_in_one_line():
    inter = cp.quality_session("intervalles", 5)[2]
    line = cv.guidance(cp.QUALITY, inter, 186, C)
    assert line == ("Efforts vifs : parler devient impossible. Cible pendant les efforts : "
                    "4:00–4:15 /km sur le plat, 168–177 bpm.")
    assert cv.guidance(cp.FOOTING, cp.steady(40, 2), None, C).endswith("Cible : pas plus vite que 5:30 /km "
                                                                        "sur le plat.")
    assert cv.guidance(cp.FOOTING, cp.steady(40, 2)) == "Facile : tu peux tenir une conversation."
    assert cv.guidance(cp.QUALITY, cp.quality_session("fartlek", 5)[2], 186, C).endswith(cv.rp.fr(cv.TARGET_SHORT))


def test_paces_are_said_to_five_seconds():
    assert cv.pace_label(272.4) == "4:30" and cv.pace_label(273) == "4:35" and cv.pace_label(299) == "5:00"
    assert cv.pace_range(280, 281) == "4:40" and cv.pace_range(240, 255) == "4:00–4:15"


# ── the ticket ──────────────────────────────────────────────────────────────

async def _runner(db, athlete, today):
    """24 runs in the last 16 weeks, one with best efforts on Smyth & Muniz-Pumares' mean line."""
    for n in range(pace.MIN_RUNS):
        run = _run(athlete, today - timedelta(days=3 + 4 * n), 40, n)
        if n == 0:
            run.best_efforts = [{"distance": d, "elapsed_time": round((d - D_PRIME) / CS)} for d in (400, 805, 5000)]
        db.add(run)
    await db.flush()


def _ticket(page: str, session_id: int) -> str:
    start = page.index(f'aria-labelledby="coach-s-{session_id}"')
    return page[start:page.index("</article>", start)]


async def test_the_ticket_shows_the_target_large_and_the_feel_under_it(client, people, beta, db_session, same_today):
    athlete = people.athlete
    athlete.max_heart_rate = 186
    await _runner(db_session, athlete, same_today)
    plan = await _plan(client, db_session, athlete, base="240")
    easy = next(r for r in await _rows(db_session, athlete) if r.day > same_today and r.kind == cp.FOOTING)
    page = (await _as(client, athlete).get(f"/coaching?d={easy.day}")).text
    ticket = _ticket(page, easy.id)
    block = ticket[ticket.index('class="pf-coach-ticket"'):ticket.index('class="pf-coach-s-guide"')]
    assert 'class="pf-coach-target"' in block, "inside the coloured block"
    assert re.search(r">Cible</p>", block) and "pendant les efforts" not in block, "a steady run: no efforts"
    assert ">Pas plus vite que, sur le plat<" in block and ">5:30<small>/km</small>" in block
    assert ">112–148<small>bpm</small>" in block and "Cardio : 112 à 148 bpm" in block
    assert page.count("112–148") == 1, "the old line under the ticket is gone: once"
    assert re.search(r'class="pf-coach-s-guide">Facile\s: tu peux tenir une conversation\.</p>', ticket)
    assert 'href="/coaching?outil=methode#coach-tool-how" data-tool-open="coach-tool-how"' in block
    assert "Les allures valent sur le plat" in page.split('id="coach-tool-how"')[1], "« Comment ça marche » says it"
    assert not re.search(r"Hunter|Nixon|Billat|Smyth|\(H\)", page), "sources stay in the docs"
    # a threshold session: the efforts' target
    q = next(r for r in await _rows(db_session, athlete) if r.day > easy.day and r.status == co.PLANNED
             and r.kind == cp.FOOTING)
    await client.post(f"/coaching/sessions/{q.id}/edit", data={"kind": cp.QUALITY, "minutes": "50"}, headers=HTMX)
    ticket = _ticket((await client.get(f"/coaching?d={q.day}")).text, q.id)
    assert "Cible<span> · pendant les efforts</span>" in ticket and ">4:40–4:50<small>/km</small>" in ticket
    assert ">149–167<small>bpm</small>" in ticket
    # done: the ticket says what was run, no target
    done = await _past_session(db_session, plan, same_today - timedelta(days=1), status=co.DONE, done_minutes=45,
                              manual=True)
    ticket = _ticket((await client.get(f"/coaching?d={done.day}")).text, done.id)
    assert "pf-coach-target" not in ticket and "pf-coach-s-guide" not in ticket
    # missed: the target stays
    missed = await _past_session(db_session, plan, same_today - timedelta(days=2), status=co.MISSED, manual=True)
    assert "pf-coach-target" in _ticket((await client.get(f"/coaching?d={missed.day}")).text, missed.id)


async def test_the_person_who_follows_the_plan_never_reads_a_heart_rate(client, people, beta, db_session,
                                                                        same_today):
    athlete, coach = people.athlete, people.coach
    athlete.max_heart_rate = 186
    await _runner(db_session, athlete, same_today)
    await _plan(client, db_session, athlete, base="240")
    db_session.add(CoachingLink(athlete_id=athlete.id, coach_id=coach.id))
    await db_session.flush()
    easy = next(r for r in await _rows(db_session, athlete) if r.day > same_today and r.kind == cp.FOOTING)
    page = (await _as(client, coach).get(f"/coaching/athletes/{athlete.id}?d={easy.day}")).text
    ticket = _ticket(page, easy.id)
    assert ">5:30<small>/km</small>" in ticket, "the pace, from the runs whose summary they already read"
    assert "bpm" not in page and "112–148" not in page and "Cardio" not in page
    assert "Confirme ta FC max" not in page and "D’où viennent" not in page and "/settings" not in ticket
    # nothing known of the pace either: no Cible at all for them
    athlete.max_heart_rate = None
    page = (await _as(client, coach).get(f"/coaching/athletes/{athlete.id}?d={easy.day}")).text
    assert "Confirme ta FC max" not in page


async def test_without_data_one_quiet_line_leads_to_the_maximum(client, people, beta, db_session, same_today):
    athlete = people.athlete
    athlete.max_heart_rate = None
    await _plan(client, db_session, athlete)
    rows = await _rows(db_session, athlete)
    easy = next(r for r in rows if r.day > same_today and r.kind == cp.FOOTING)
    ticket = _ticket((await _as(client, athlete).get(f"/coaching?d={easy.day}")).text, easy.id)
    assert '<a class="pf-coach-target-link" href="/settings#max-heart-rate">Confirme ta FC max pour voir ta zone</a>' \
        in ticket
    assert "/km" not in ticket and "bpm" not in ticket and "D’où viennent" not in ticket


async def test_the_feed_carries_the_target(client, people, beta, db_session, same_today):
    from app.dependencies import get_current_user

    athlete = people.athlete
    athlete.max_heart_rate = 186
    await _runner(db_session, athlete, same_today)
    plan = await _plan(client, db_session, athlete, base="240")
    client._transport.app.dependency_overrides.pop(get_current_user)
    body = (await client.get(f"/coaching/cal/{plan.feed_token}.ics")).text.replace("\r\n ", "")
    assert "Cible : pas plus vite que 5:30 /km sur le plat\\, 112–148 bpm." in body
