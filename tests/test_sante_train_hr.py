"""Santé, the Entraînement dial from heart rate (owner, 2026-10-09, the approved mockup: « Oui vas-y »;
research_ind_train.md §5): the last 7 days' heart-rate load against the usual week's — each minute weighed by its
share of the heart-rate reserve, x·0.64·e^(1.92x) (Banister 1991), from the laps when there are any — with today's
bounds for every week; the minutes without a readable HR, and strength, at the athlete's own usual minute; the card
keeps the hours and adds « Intensité » in a word. Every threshold here is a PaceForge heuristic (H)."""
import json
import math
import re
import statistics
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from html import unescape
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from httpx import AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.crypto import encrypt_secret
from app.models.activity import Activity
from app.models.user import User
from app.services import coros, sante, viz
from app.services import nights as nt
from app.services import sante_score as sc
from app.services import sante_training as st
from tests import owner_coros as oc
from tests import test_coros
from tests.test_coros import _link
from tests.test_sante import _main, _visible
from tests.test_sante_score import _session

fake, no_commit, as_user = test_coros.fake, test_coros.no_commit, test_coros.as_user
ROOT = Path(__file__).resolve().parent.parent
D = date(2026, 10, 8)  # a Thursday: this week's Monday is 05/10
TUE = D - timedelta(days=2)
REST, PEAK = 50.0, 150.0  # a reserve of 100 bpm: x = (HR − 50) ÷ 100


def _w(hr: float) -> float:
    """One minute's weight at `hr` on REST and PEAK."""
    return st.minute_load(hr, REST, PEAK)


def _weeks(per_week: list, hr=120.0, sport="Run") -> list:
    """One session on the Wednesday of each of the complete weeks before D (the latest first), `hr` its average."""
    monday = D - timedelta(days=D.weekday())
    return [_session(monday - timedelta(days=7 * k) + timedelta(days=2), m, sid=k, hr=hr, sport=sport)
            for k, m in enumerate(per_week, start=1)]


def _train(sessions, rest=REST, peak=PEAK) -> dict:
    return sante.training(sessions, D, rest=rest, peak=peak)


# ── one activity's load ─────────────────────────────────────────────────────

def test_a_minute_weighs_more_when_the_pulse_is_high():
    """Banister 1991: x·0.64·e^(1.92x), x = (HR − repos) ÷ (max − repos) kept in [0, 1]: an easy minute at 60 % of
    the reserve ≈ 1.2 (the default without HR), a hard one at 90 % ≈ 3.2, 2.7 times as much; under the resting HR
    nothing, over the max the max's."""
    assert [round(_w(hr), 2) for hr in (100, 110, 120, 130, 140)] == [0.84, 1.22, 1.72, 2.38, 3.24]
    assert round(_w(140) / _w(110), 1) == 2.7 and round(_w(110), 1) == st.RATE_DEFAULT
    assert _w(45) == _w(50) == 0
    assert _w(170) == _w(150) == pytest.approx(0.64 * math.exp(1.92))


def test_a_session_from_its_laps_else_its_average():
    """Its laps' (else its km splits': `segs`) minutes × each one's weight, scaled to its moving time when they
    cover 80 % of it at least (H), else its moving minutes at its average HR. A steady run reads the same either way
    (within 1 %); 10 × (3 min at 92 % of the reserve + 2 min at 60 %) reads 10 % more from its laps (García-Ramos
    2015: ≈ 9 %); a lap with an impossible HR is left out, of the load and of what the laps cover."""
    steady = _session(D, 50, hr=130)
    assert st.session_load(steady, REST, PEAK) == pytest.approx(50 * _w(130))  # no laps: the average
    steady.segs = ((10, 129), (10, 131), (10, 128), (10, 132), (10, 130))
    assert st.session_load(steady, REST, PEAK) == pytest.approx(50 * _w(130), rel=0.01)
    intervals = _session(D, 50, hr=129.2)  # its average, by time
    assert round(st.session_load(intervals, REST, PEAK), 1) == 116.0
    intervals.segs = ((3, 142), (2, 110)) * 10
    assert round(st.session_load(intervals, REST, PEAK), 1) == 127.6  # + 10 %
    part = _session(D, 50, hr=129.2)
    part.segs = ((3, 142), (2, 110)) * 8  # 40 min of 50: 80 %, scaled to the 50
    assert st.session_load(part, REST, PEAK) == pytest.approx(50 * (24 * _w(142) + 16 * _w(110)) / 40)
    part.segs = ((3, 142), (2, 110)) * 7  # 35 min: 70 %, the average
    assert st.session_load(part, REST, PEAK) == pytest.approx(50 * _w(129.2))
    odd = _session(D, 50, hr=129.2)
    odd.segs = ((3, 142), (2, 110)) * 9 + ((3, 230), (2, 110))  # 230 bpm: over max + 10
    assert st.session_load(odd, REST, PEAK) == pytest.approx(50 * (27 * _w(142) + 20 * _w(110)) / 47)


def test_the_glitch_guard():
    """(H) An average under 40 bpm or over max + 10, or a run (road, trail, treadmill) under 30 % of the reserve (an
    optical dropout): no HR, the session counts by time; a ride or a walk that easy is just easy; strength, Workout,
    yoga and Pilates count by time whatever their HR (HR under-reads them: Polar; WHOOP's muscular load)."""
    def readable(**kw):
        return st.session_load(_session(D, 50, **kw), REST, PEAK) is not None
    assert not readable(hr=39, sport="Ride") and readable(hr=40, sport="Ride")  # a run that low: a dropout too
    assert readable(hr=160) and not readable(hr=161)  # max + 10
    assert not readable(hr=79) and readable(hr=80)  # a run: 29 % and 30 % of the reserve
    assert not readable(hr=79, sport="TrailRun") and not readable(hr=79, sport="VirtualRun")
    assert readable(hr=60, sport="Ride") and readable(hr=60, sport="Walk")
    assert not any(readable(hr=130, sport=s) for s in ("WeightTraining", "Crossfit", "Workout", "Yoga", "Pilates"))
    assert not readable(hr=None)


def test_a_minute_without_hr_counts_as_the_athletes_usual_minute():
    """(H) A session without a readable HR, and every strength, Workout, yoga or Pilates session, counts its moving
    minutes at the athlete's own median load per minute over the 12 months: that of its family (road, trail and
    treadmill runs together; the rides together; else its sport) with 5 sessions read from their HR at least, else
    that of all of them, else 1.2 (an easy minute)."""
    runs = [_session(D - timedelta(days=9 * k), 40, hr=140, sid=k) for k in range(1, 6)]  # 5 runs: their own rate
    walks = [_session(D - timedelta(days=9 * k + 3), 60, hr=90, sport="Walk", sid=10 + k) for k in range(1, 5)]
    ride = _session(D - timedelta(days=5), 60, hr=110, sport="Ride", sid=20)  # 1 ride: under 5
    every = statistics.median([_w(140)] * 5 + [_w(90)] * 4 + [_w(110)])
    assert every not in (_w(140), _w(90), _w(110))
    bare = [_session(D, 30, hr=None, sid=90), _session(D, 30, hr=None, sport="TrailRun", sid=91),
            _session(D, 30, hr=None, sport="Ride", sid=92), _session(D, 30, hr=None, sport="Walk", sid=93),
            _session(D, 45, hr=140, sport="WeightTraining", sid=94), _session(D, 30, hr=70, sid=95)]  # 95: a dropout
    got = st.loads(runs + walks + [ride] + bare, D, REST, PEAK)
    assert got[1] == (pytest.approx(40 * _w(140)), True) and got[20] == (pytest.approx(60 * _w(110)), True)
    assert got[90] == got[91] == got[95] == (pytest.approx(30 * _w(140)), False)  # the runs' minute
    assert got[92] == got[93] == (pytest.approx(30 * every), False)  # 1 ride, 4 walks: all of them
    assert got[94] == (pytest.approx(45 * every), False)  # strength: always all of them
    # under 5 sessions read from their HR in the 12 months (the older ones never count): 1.2 a minute
    old = [replace(s, day=s.day - timedelta(days=400)) for s in runs]
    assert st.loads(old + runs[:4] + [bare[0]], D, REST, PEAK)[90] == (pytest.approx(30 * 1.2), False)


# ── the dial ────────────────────────────────────────────────────────────────

def test_the_same_hours_harder_read_more_than_usual():
    """The dial weighs the minutes: the usual 5 hours run harder read « plus que d'habitude » (162 %) and the card
    says why, « Intensité : plus élevée que d'habitude », the hours unchanged; run easier, « moins que d'habitude »
    and « plus basse que d'habitude »; at the usual pulse, 100 % and « comme d'habitude » twice."""
    usual = _weeks([300] * 11, hr=120)
    cases = {120: ("100", "comme d'habitude", "comme d'habitude"),
             135: ("162", "plus que d'habitude", "plus élevée que d'habitude"),
             110: ("71", "moins que d'habitude", "plus basse que d'habitude")}
    for hr, (value, word, pace) in cases.items():
        ratio = _w(hr) / _w(120)
        assert sc.rounded(100 * ratio) == int(value)
        t = _train(usual + [_session(TUE, 300, hr=hr, sid=99)])
        assert (t["dial"]["value"], t["dial"]["sub"], t["intensity"]) == (value, word, pace), hr
        assert t["week"] == "5h00" and t["usual"] == f"ta semaine habituelle{viz.NBSP}: 5h00"
        assert t["dial"]["aria"] == f"Entraînement {sc.pct(int(value))} de ta semaine habituelle, {word}."
        assert t["dial"]["dash"] == viz.ring("x", ratio / 2, "", None)["dash"]  # the arc full at 200 %


def test_the_dial_is_the_hours_times_the_intensity():
    """No number contradicts another: the dial is the hours' ratio times the minutes' weight against the usual
    week's, so with « Intensité : comme d'habitude » it sits within 10 % of the hours' ratio, above it with « plus
    élevée », under it with « plus basse »."""
    usual = _weeks([300, 360, 240, 330, 270, 300, 300, 420, 180, 300, 300], hr=125)
    tu = statistics.fmean([300, 360, 240, 330, 270, 300, 300, 420, 180, 300, 300])
    seen = set()
    for minutes in (150, 300, 450):
        for hr in range(105, 150, 3):
            t = _train(usual + [_session(TUE, minutes / 2, hr=hr, sid=98),
                                _session(TUE - timedelta(days=1), minutes / 2, hr=hr - 4, sid=99)])
            hours, dial = 100 * minutes / tu, int(t["dial"]["value"])
            seen.add(t["intensity"])
            if t["intensity"] == "comme d'habitude":
                assert 0.9 * hours - 1 <= dial <= 1.1 * hours + 1, (minutes, hr)
            elif t["intensity"] == "plus élevée que d'habitude":
                assert dial >= 1.1 * hours - 1, (minutes, hr)
            else:
                assert t["intensity"] == "plus basse que d'habitude" and dial <= 0.9 * hours + 1, (minutes, hr)
    assert seen == set(sante.INTENSITY_WORDS)  # each word met


def test_an_athlete_without_hr_gets_the_time_dial():
    """No HR at all (a phone, a watch without a wrist sensor): every minute weighs the same, 1.2, and the dial is
    exactly the hours' (the time dial's values, words and arc), with no « Intensité » row; a gym session is no
    exception."""
    usual = [300, 400] * 5 + [350] + [900]  # a 12th week back: out of the 11 (mean 350)
    for minutes in (350, 420, 421, 280, 279, 437.5, 700, 1013, 0):
        sessions = [replace(s, hr=None) for s in _weeks(usual)]
        if minutes:
            sessions += [_session(TUE, minutes - 40, hr=None, sid=98),
                         _session(TUE, 40, hr=None, sport="WeightTraining", sid=99)]
        t = _train(sessions)
        ratio = minutes / 350
        word = ("plus que d'habitude" if ratio > 1.2 else "moins que d'habitude" if ratio < 0.8
                else "comme d'habitude")
        assert (t["dial"]["value"], t["dial"]["sub"], t["intensity"]) == (str(sc.rounded(100 * ratio)), word, None)
        assert t["dial"]["dash"] == viz.ring("x", ratio / 2, "", None)["dash"], minutes
    assert sc.rounded(100 * 437.5 / 350) == 125  # 125 % on the dot: no rounding drift


def test_todays_bounds_for_every_week():
    """(H) Every week compared is read with today's resting HR and max: a new max, or a new resting HR, never moves
    the dial by itself — the same week as usual reads 100 % whatever the pair."""
    same = _weeks([300] * 11, hr=130) + [_session(TUE, 300, hr=130, sid=99)]
    for rest, peak in ((50, 150), (50, 190), (40, 205), (60, 175)):
        t = _train(same, rest, peak)
        assert (t["dial"]["value"], t["intensity"]) == ("100", "comme d'habitude"), (rest, peak)


def test_a_usual_week_that_weighs_nothing_reads_its_hours():
    """The usual week's 4 weeks with an activity are counted on the activities, not on their load: rides at the
    resting HR weigh nothing, the dial then reads the hours' ratio (never a division by zero)."""
    t = _train(_weeks([300] * 11, hr=48, sport="Ride") + [_session(TUE, 330, hr=48, sport="Ride", sid=99)])
    assert (t["dial"]["value"], t["dial"]["sub"], t["intensity"]) == ("110", "comme d'habitude", "comme d'habitude")


# ── the card's « Intensité » ────────────────────────────────────────────────

def test_the_intensity_row():
    """« Intensité »: the minutes' weight against the usual week's, a word within ± 10 % (H) — « plus élevée que
    d'habitude », « comme d'habitude », « plus basse que d'habitude » —, never a number; no row when under half of the
    7 days' minutes, or of the usual weeks', were read from their HR (H; a gym hour is not), without a usual week,
    or without a minute in the 7 days."""
    usual = _weeks([300] * 11, hr=120)

    def row(this, weeks=usual):
        return _train(weeks + this)["intensity"]
    for hr, ratio, word in ((125, 1.18, "plus élevée que d'habitude"), (122, 1.07, "comme d'habitude"),
                            (118, 0.93, "comme d'habitude"), (115, 0.84, "plus basse que d'habitude")):
        assert round(_w(hr) / _w(120), 2) == ratio and row([_session(TUE, 300, hr=hr, sid=99)]) == word, hr
    wed = TUE + timedelta(days=1)
    assert row([_session(TUE, 150, hr=125, sid=99), _session(wed, 150, hr=None, sid=98)]) is not None  # half
    assert row([_session(TUE, 149, hr=125, sid=99), _session(wed, 151, hr=None, sid=98)]) is None
    assert row([_session(TUE, 149, hr=125, sid=99), _session(wed, 151, hr=140, sport="Workout", sid=98)]) is None
    bare = [replace(s, hr=None) if s.id <= 6 else s for s in usual]  # 6 of the 11 weeks without HR
    assert row([_session(TUE, 300, hr=125, sid=99)], bare) is None
    bare = [replace(s, hr=None) if s.id <= 5 else s for s in usual]  # 5 of 11: the row
    assert row([_session(TUE, 300, hr=125, sid=99)], bare) is not None
    assert row([]) is None  # no minute in the 7 days (0 %, « moins que d'habitude »)
    t = _train(_weeks([300] * 3, hr=120) + [_session(TUE, 300, hr=140, sid=99)])  # 3 weeks: no usual week
    assert (t["dial"]["value"], t["dial"]["sub"], t["intensity"]) == ("5h00", "pas encore d'habitude", None)


async def _seed_runs(db: AsyncSession, user: User, hr_usual: float, hr_now: float) -> None:
    """3 runs of 100 min a week (Monday → Wednesday) over the 11 complete weeks before D at `hr_usual`, and this
    week's 3 at `hr_now` (Strava rows, max HR 180 each)."""
    monday = D - timedelta(days=D.weekday())
    for k in range(0, 12):
        for j in range(3):
            d = monday - timedelta(days=7 * k) + timedelta(days=j)
            db.add(Activity(user_id=user.id, strava_activity_id=91_000 + 10 * k + j, sport_type="Run",
                            name=f"Footing {k}-{j}", start_date=datetime(d.year, d.month, d.day, 6, tzinfo=timezone.utc),
                            distance=16_000, moving_time=6000, elapsed_time=6100, total_elevation_gain=50,
                            average_heartrate=hr_now if k == 0 else hr_usual, max_heartrate=180,
                            raw_data={"utc_offset": 7200}))
    await db.flush()


async def test_the_card_on_the_page(as_user: AsyncClient, db_session: AsyncSession, test_user: User, monkeypatch):
    """The page (the approved mockup): the dial's percentage from heart rate (its aria as before), the card's rows
    « 7 derniers jours » (the hours, Activités') and « Intensité » with its word, a row like the others with its name
    on the left and its word on the right, no dot, no colour, no number; never « TRIMP », « charge » or points; each
    number printed once."""
    async def today(*a, **k):
        return D
    monkeypatch.setattr(sante, "athlete_today", today)
    await _seed_runs(db_session, test_user, hr_usual=130, hr_now=150)
    html = (await as_user.get("/sante")).text
    main = _main(html)
    p = sc.rounded(100 * st.minute_load(150, 50, 180) / st.minute_load(130, 50, 180))  # no night: 50; max 180
    assert p == 168
    assert ('<a class="pf-ring pf-ring-entrainement is-accent" href="#entrainement" aria-label="Entraînement '
            '168 % de ta semaine habituelle, plus que d&#39;habitude. 100 % représente ta semaine habituelle ; '
            'un tour complet représente 200 %.">') in main
    train = main.split('<details id="entrainement"')[1].split("</details>")[0]
    assert ('<div class="pf-row pf-intensity"><span class="pf-row-name"><span>Intensité</span></span><span '
            'class="pf-row-val"><span>plus élevée que d&#39;habitude</span></span></div>') in train
    words = unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", train.split(">", 1)[1])).strip())
    assert "ta semaine habituelle : 5h00 Intensité plus élevée que d'habitude" in words
    assert "pas à un objectif à atteindre" in words and "représente 200 %" in words
    assert "pf-dot" not in train and not re.search(r"(?i)trimp|charge|points?\b", words)
    seen = re.sub(r"\s+", " ", _visible(html).replace(" ", " "))
    assert len(re.findall(r"(?<![\d,h:])168 %", seen)) == 1 and len(re.findall(r"(?<![\d,h:])5h00", seen)) == 1
    rules = (ROOT / "app/static/css/interface.css").read_text(encoding="utf-8")
    assert ".pf-row.pf-intensity { border-top: 1px solid rgb(var(--pf-line)); }" in rules
    assert ".pf-intensity .pf-row-val > span { font-size: 15px; color: rgb(var(--pf-ink)); }" in rules


# ── the laps: read once, COROS's kept, Strava's webhook kept ──────────────────

async def test_the_laps_are_read_once_per_worker(db_session: AsyncSession, test_user: User, monkeypatch):
    """A year of laps is a heavy read: the segments are kept with the sessions (load_sessions' cache, per worker) and
    read again once a sync added or rewrote an activity, its laps included (their stored size is in the key)."""
    st._SEGS.clear()
    laps = [{"moving_time": 600, "average_heartrate": 150}, {"moving_time": 600, "average_heartrate": 120}]
    for k in range(3):
        d = D - timedelta(days=2 + k)
        db_session.add(Activity(user_id=test_user.id, strava_activity_id=93_000 + k, sport_type="Run", name=f"r{k}",
                                start_date=datetime(d.year, d.month, d.day, 6, tzinfo=timezone.utc), distance=5000,
                                moving_time=1200, elapsed_time=1200, average_heartrate=135, max_heartrate=170,
                                laps=laps, raw_data={"utc_offset": 7200}))
    await db_session.flush()
    read = []
    real = nt.read_segments

    async def spy(db, sessions):
        read.append(sorted(s.id for s in sessions if s.hr and s.segs is None))
        await real(db, sessions)
    monkeypatch.setattr(nt, "read_segments", spy)
    since = D - timedelta(days=st.RATE_DAYS)
    first = await st.load_sessions(db_session, test_user.id, D)
    await st.load_hr_segments(db_session, test_user.id, first, since)
    assert len(read[-1]) == 3 and all(s.segs == ((10.0, 150.0), (10.0, 120.0)) for s in first)
    again = await st.load_sessions(db_session, test_user.id, D)
    assert all(s.segs is None for s in again)  # copies of the cached sessions
    await st.load_hr_segments(db_session, test_user.id, again, since)
    assert read[-1] == [] and [s.segs for s in again] == [s.segs for s in first]  # from memory
    one = first[0].id
    await db_session.execute(update(Activity).where(Activity.id == one).values(laps=laps + [laps[0]]))
    fresh = await st.load_sessions(db_session, test_user.id, D)
    await st.load_hr_segments(db_session, test_user.id, fresh, since)
    assert len(read[-1]) == 3 and next(s for s in fresh if s.id == one).segs == ((10.0, 150.0), (10.0, 120.0),
                                                                                  (10.0, 150.0))


def test_coros_laps_in_stravas_shape():
    """queryActivityLapData (the owner's real answers, laps trimmed): each auto lap's `time` (s, the moving time:
    the whole row's 1806 s is the detail's « Workout Time 30:06 ») and `avgHr`, its `maxHr`, its `distance` (cm:
    the whole row's 617 322 is the detail's 6.17 km), in Strava's shape — what nights.hr_segments reads; a multisport
    record's laps are its legs'; a lap without a time or with an impossible HR is left out."""
    run = coros.parse_laps(oc.LAPS_2026_09_30)
    assert run == {"hr_max": 171, "laps": [
        {"moving_time": 312, "average_heartrate": 103, "max_heartrate": 130, "distance": 1000},
        {"moving_time": 120, "average_heartrate": 158, "max_heartrate": 162, "distance": 475.58},
        {"moving_time": 232, "average_heartrate": 151, "max_heartrate": 171, "distance": 772.65}]}
    assert nt.hr_segments(run["laps"], None) == ((5.2, 103.0), (2.0, 158.0), (232 / 60, 151.0))
    assert coros.parse_laps(oc.LAPS_PILATES)["laps"] == [
        {"moving_time": 3016, "average_heartrate": 67, "max_heartrate": 98}]  # its one plain lap
    tri = coros.parse_laps(oc.LAPS_TRIATHLON)["laps"]  # swim, bike, run: each leg's whole row (no lap has a time)
    assert [(x["moving_time"], x["average_heartrate"]) for x in tri] == [(2125, 137), (10072, 140), (4869, 152)]
    odd = json.dumps({"lapGroups": [{"type": 10, "laps": [{"time": 300, "avgHr": 0}, {"time": 0, "avgHr": 150},
                                                          {"time": 300, "avgHr": 151, "maxHr": 400}]}]})
    assert coros.parse_laps(odd) == {"laps": [{"moving_time": 300, "average_heartrate": 151}]}


def _intervals_answer() -> str:
    """A COROS-only run's queryActivityLapData (the real JSON shape): 6 auto laps of 5 min, hard and easy in turn,
    and the whole-activity row (30 min, 142 bpm, max 171)."""
    laps = [{"lapIndex": i + 1, "distance": 100000, "time": 300.0, "avgHr": 160 if i % 2 else 124,
             "maxHr": 171 if i % 2 else 130} for i in range(6)]
    return json.dumps({"source": "activityDetail", "lapGroups": [
        {"type": 10, "lapDistance": 100000, "laps": laps},
        {"type": -1, "laps": [{"lapIndex": 1, "distance": 617322, "time": 1806.13, "avgHr": 142, "maxHr": 171}]}],
        "sportDataDetails": []})


async def test_a_coros_only_run_keeps_its_laps(as_user: AsyncClient, db_session: AsyncSession, test_user: User, fake,
                                              no_commit):
    """The laps answer the sync already reads for the max HR (no other call) also gives each auto lap's time and
    average HR: kept in Activity.laps as Strava's laps are (the raw marker keeps the max HR alone), so Santé reads a
    COROS-only session's laps as a Strava one's: its Entraînement load from them, not from its average; its page in
    Activités reads them as a Strava one's too."""
    now = datetime.now(timezone.utc).replace(hour=3, minute=0, second=0, microsecond=0)
    fake.sessions = [{"label": 701, "code": 100, "start": now - timedelta(days=1), "seconds": 1806, "km": 6.17,
                      "hr": 142}]
    fake.laps = _intervals_answer()
    conn = await _link(db_session, test_user)
    assert (await coros.run_sync(db_session, conn))["ok"]
    names = [n for n, _ in fake.tool_calls]
    assert names.count("queryActivityLapData") == names.count("getActivityDetail") == 1
    act = (await db_session.execute(select(Activity).where(Activity.coros_activity_id == 701))).scalar_one()
    assert act.laps == [{"moving_time": 300, "average_heartrate": 160 if i % 2 else 124,
                         "max_heartrate": 171 if i % 2 else 130, "distance": 1000} for i in range(6)]
    assert act.raw_data["lap_data"] == {"hr_max": 171} and act.max_heartrate == 171
    assert (await as_user.get(f"/activity/{act.id}")).status_code == 200
    today = now.date()
    sessions = await st.load_sessions(db_session, test_user.id, today)
    [s] = sessions
    await nt.read_segments(db_session, sessions)
    assert s.segs == ((5.0, 124.0), (5.0, 160.0)) * 3
    rest, peak = 45.0, 171.0
    by_laps = s.minutes * statistics.fmean([st.minute_load(124, rest, peak), st.minute_load(160, rest, peak)])
    assert st.session_load(s, rest, peak) == pytest.approx(by_laps)
    assert by_laps > s.minutes * st.minute_load(142, rest, peak)  # its laps: more than its average says


async def test_the_strava_webhook_keeps_the_laps(db_session: AsyncSession, test_user: User, monkeypatch):
    """The webhook reads the activity's detail: its laps are kept with its splits (the poll's list has neither;
    reading each new activity's detail there would cost a call each)."""
    import app.database
    from app.services.strava import StravaService
    from app.tasks.webhook_sync import _run_webhook_sync

    test_user.strava_client_id = "4242"
    test_user.strava_client_secret_encrypted = encrypt_secret("s3cret")
    await db_session.flush()

    @asynccontextmanager
    async def session():
        yield db_session
    monkeypatch.setattr(app.database, "get_task_session", session)
    monkeypatch.setattr(db_session, "commit", db_session.flush)
    laps = [{"lap_index": i + 1, "moving_time": 600, "elapsed_time": 610, "distance": 2000.0,
             "average_heartrate": 140.0 + 5 * i} for i in range(3)]
    detail = {"id": 555_001, "sport_type": "Run", "name": "Fractionné", "start_date": "2026-10-06T06:00:00Z",
              "distance": 6000.0, "moving_time": 1800, "elapsed_time": 1830, "average_heartrate": 145.0,
              "max_heartrate": 172.0, "laps": laps, "splits_metric": [{"split": 1, "moving_time": 300}]}
    monkeypatch.setattr(StravaService, "get_activity", AsyncMock(return_value=detail))
    assert (await _run_webhook_sync(555_001, test_user.strava_athlete_id))["status"] == "saved"
    act = (await db_session.execute(select(Activity).where(Activity.strava_activity_id == 555_001))).scalar_one()
    assert act.laps == laps and act.splits_metric == detail["splits_metric"]
    assert nt.hr_segments(act.laps, act.splits_metric) == ((10.0, 140.0), (10.0, 145.0), (10.0, 150.0))
