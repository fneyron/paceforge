"""« Forme du jour », fixes from the review of feat/score (SCORE_SPEC.md wins,
then SANTE_DECISIONS.md and evidence_final.md):
- the 14-day history is each day's score as the page computed it THAT day:
  no alert episode, Strava race or easy-pace model from a later day;
- the race eve is never flagged on race morning (no « nuit courte » cap, no
  « moins de 6 h » tile, Sommeil not counted);
- the race itself is not the Charge's « big outing » (the cap does it);
- a known tone always has a score, from one signal on;
- half up on the exact value;
- the copy: the building line, the no-score line, the missing signals, the
  method fold; forced colours and marks ≥ 3:1;
- the history computes its bands and alerts once (CPU), Sommeil its bands once;
- the race page's « (provisoire) » names the provisional band only."""
import json
import math
import re
from collections import Counter
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User
from app.services import nights as nt
from app.services import race_prep as rp
from app.services import sante
from app.services import sante_score as sc
from app.services import sante_sleep as sl
from app.services import sante_training as st
from tests import test_coros
from tests.test_coros import _link
from tests.test_nights import D, night_rows
from tests.test_sante import _add
from tests.test_sante_score import _feel, _nights, _rich_rows, _runs, _session, _view

as_user, no_commit = test_coros.as_user, test_coros.no_commit
ROOT = Path(__file__).resolve().parent.parent


def _as_of(rows, sessions, feel, routes, today):
    """The page as health_page built it on `today`: from the rows stored by then only."""
    r = {m: {d: v for d, v in vals.items() if d <= today} for m, vals in rows.items()}
    nights = nt.build_nights(r, today)
    ss = [s for s in sessions if s.day <= today]
    f = {d: v for d, v in feel.items() if d <= today}
    races = rp.all_races(routes, ss)
    peak = st.hr_max(ss, today)
    nt.tag_nights(nights, ss, races, f, nt.rest_hr(nights, today), peak)
    nt.tag_alerts(nights, today, races)
    nr = next((x for x in routes if rp.race_day(x) >= today), None)
    lr = next((x for x in routes if today - timedelta(days=14) <= rp.race_day(x) < today), None)
    return sante._today_view(nights, f, ss, peak, nr, lr, races, today, True, routes=routes)


def _points(view) -> dict:
    """{day: (score, action)} of the 14-day line."""
    data = json.loads(view["score"]["spark"]["data"])
    return {date.fromisoformat(d): (int(r[1].split(" ")[0]), r[2]) for d, r in zip(data["d"], data["r"], strict=True)}


def _history_is_each_days_page(rows, sessions, feel, routes):
    """Every point of today's line is the score and action the page gave on that day."""
    points = _points(_as_of(rows, sessions, feel, routes, D))
    for k in range(1, sc.HISTORY_DAYS):
        d = D - timedelta(days=k)
        then = _as_of(rows, sessions, feel, routes, d)
        want = (then["score"]["value"], then["verdict"]["headline"]) if then["score"]["value"] is not None else None
        assert points.get(d) == want, d
    return points


# ── the history: what each day knew ─────────────────────────────────────────

def test_history_first_night_of_an_alert_episode_is_tagged_from_the_next_morning():
    """A full band (median ≈ 45, alert line 50); 28/09 → 03/10 at 47,5 bpm;
    04/10 and 05/10 at 56 bpm with HRV 35 ms. On 04/10 the page said « Garde
    ta séance facile » (R7); the alert fires on 05/10. Today's line used to
    show 04/10 green « Séance prévue » (99): night 04/10 carried the alert tag
    05/10 gave it, so it left 04/10's own 7-night means."""
    def hr(k):
        return 56.0 if k in (2, 3) else 47.5 if 4 <= k <= 9 else 44.0 + k % 3
    rows = night_rows(range(0, 70), hr=hr, hrv=lambda k: 35.0 if k in (2, 3) else 70 * math.exp(0.08 * (k % 5 - 2)))
    first = D - timedelta(days=3)
    then = _as_of(rows, _runs(), {}, [], first)
    assert (then["verdict"]["headline"], then["score"]["word"]) == ("Garde ta séance facile", "moyen")
    assert _as_of(rows, _runs(), {}, [], first + timedelta(days=1))["verdict"]["rule"] == "ill"
    points = _history_is_each_days_page(rows, _runs(), {}, [])
    assert points[first] == (then["score"]["value"], "Garde ta séance facile")


def test_history_a_race_known_only_once_run_tags_nothing_before_it():
    """A 5-h Strava race on 06/10 (workout_type 1, no Route): its J-7 → J-1
    nights are « autour de la course » from 06/10 on, not on 03/10 → 05/10,
    whose HRV under the band still counted that day."""
    rows = night_rows(range(0, 70), hr=lambda k: 44.0 + k % 3,
                      hrv=lambda k: 52.0 if 2 <= k <= 12 else 70 * math.exp(0.08 * (k % 5 - 2)))
    ss = _runs(12, start=3) + [replace(_session(D - timedelta(days=1), 300, dplus=2000, sid=999), workout_type=1)]
    then = _as_of(rows, ss, {}, [], D - timedelta(days=2))
    assert "VFC" in then["score"]["chips"] and then["score"]["value"] < 100
    points = _history_is_each_days_page(rows, ss, {}, [])
    assert points[D - timedelta(days=2)][0] == then["score"]["value"]


def test_history_reads_the_easy_pace_model_of_its_own_day():
    """« malade » on 27/09; 5 cool easy runs before (25/07 → 09/09), the 6th on
    01/10. On 28/09 → 30/09 there was no easy-pace model (6 cool runs needed):
    « Reprise » had nothing to hold, the day was « Séance prévue ». Today's
    model gives a pre-illness reference (18/08) and used to hold « Reprise en
    douceur » (orange, capped) on 29/09 and 30/09."""
    easy = [date(2026, 7, 25), date(2026, 7, 30), date(2026, 8, 1), date(2026, 8, 18), date(2026, 9, 9),
            date(2026, 10, 1)]
    ss = [_session(d, 50, sid=200 + i) for i, d in enumerate(easy)]
    ss += [_session(date(2026, 9, 3) + timedelta(days=4 * i), 50, dplus=300, sid=300 + i) for i in range(7)]  # hilly
    feel = {date(2026, 9, 27): _feel(3, ["sick"]), date(2026, 9, 29): _feel(1), date(2026, 9, 30): _feel(1),
            D: _feel(1)}
    assert st.easy_model([s for s in ss if s.day <= date(2026, 9, 30)], date(2026, 9, 30), 190) is None
    assert st.easy_model(ss, D, 190) is not None
    then = _as_of({}, ss, feel, [], date(2026, 9, 30))
    assert then["verdict"]["rule"] == "plan" and then["score"]["word"] == "bon"
    points = _history_is_each_days_page({}, ss, feel, [])
    assert points[date(2026, 9, 29)][1] == points[date(2026, 9, 30)][1] == "Séance prévue : rien ne s'y oppose"
    assert points[date(2026, 9, 28)][1] == "Reprise en douceur"  # « malade » the day before: that day's own Reprise


def test_history_computes_its_bands_and_alerts_once(monkeypatch):
    """R1: the 13 past days share the nights' bands and alerts (nights.memo):
    under 200 alert checks for a rich wearer (68 of them the tagging before
    the view), not 1 300."""
    calls = Counter()
    real_alert, real_band = nt._illness_alert, nt._band

    def alert(*a, **k):
        calls["alert"] += 1
        return real_alert(*a, **k)

    def band(*a, **k):
        calls["band"] += 1
        return real_band(*a, **k)
    monkeypatch.setattr(nt, "_illness_alert", alert)
    monkeypatch.setattr(nt, "_band", band)
    a = _view(_nights(_rich_rows()), {D: _feel(1)}, _runs())
    assert len(_points(a)) == 14
    assert calls["alert"] <= 200 and calls["band"] <= 320, calls


def test_sommeil_computes_each_rolling_band_once(monkeypatch):
    """R2: Cœur la nuit's bands and their « provisoire » flags read one band per night and metric."""
    seen = Counter()
    real = nt._band

    def band(nights, metric, until, source=None, full=False):
        seen[(metric, until, source, full)] += 1
        return real(nights, metric, until, source, full)
    monkeypatch.setattr(nt, "_band", band)
    nights = _nights(_rich_rows())
    days = [D - timedelta(days=k) for k in range(13, -1, -1)]
    sl._panels(nights, days, alert=False)
    assert seen and max(seen.values()) == 1
    assert sum(1 for m, *_ in seen if m == "hr") == len(days)


# ── race morning, and the race itself ───────────────────────────────────────

def _route(day, name="UTMB"):
    return SimpleNamespace(id=1, name=name, race_date=day.isoformat(), result_json={}, total_distance_km=170,
                           target_time_s=None, sport_type="trail")


@pytest.mark.parametrize("asleep", [330, 200])
def test_race_eve_is_never_flagged_on_race_morning(asleep):
    """evidence_final row 23 (Lastella 2014): « Any alarm about race eve » is a
    don't. A 5h30 or 3h20 race eve: no « nuit courte » cap, no « moins de 6 h »
    tile, Sommeil not counted (« veille de course »), the score as without it."""
    rows = _rich_rows()
    rows["sleep"].update(night_rows([0], asleep=asleep, start=(23, 30), end=(5, 30))["sleep"])
    a = _as_of(rows, _runs(), {}, [_route(D)], D)
    s = a["score"]
    assert a["verdict"]["headline"] == "Jour de course" and s["cap_line"] is None
    assert "sleep" not in [r["key"] for r in s["rows"]]
    assert re.search(r"pas compté : .*Sommeil \(veille de course\)", s["absent_line"]), s["absent_line"]
    assert all(t.get("word") != "moins de 6 h" and t["label"] != "Sommeil · 24 h" for t in a["tiles"])
    assert s["value"] == 100  # every other signal at 100
    # the day before (J-1) was already never flagged: unchanged
    day_before = _as_of(rows, _runs(), {}, [_route(D + timedelta(days=1))], D)
    assert day_before["score"]["cap_line"] is None and "sleep" in [r["key"] for r in day_before["score"]["rows"]]


def test_the_race_itself_is_not_the_load_the_cap_does_it():
    """SCORE_SPEC §2.4: « Post-race is handled by the cap (§3), not here ». J+1
    after a 7-h race marked on Strava, 4h30 asleep: Sommeil 15, Charge 100 (the
    race left out), raw (20 × 15 + 15 × 100) / 35 = 51,4 capped at 40 after
    the race: 40 + 29 × 40 / 100 = 51,6 → 52, and the cap says so."""
    rows = _rich_rows()
    rows["sleep"].update(night_rows([0], asleep=270, start=(0, 30), end=(5, 30))["sleep"])
    ss = _runs() + [replace(_session(D - timedelta(days=1), 420, dplus=3000, sid=999), workout_type=1)]
    a = _as_of(rows, ss, {}, [], D)
    s = a["score"]
    assert a["verdict"]["headline"] == "Récupère"
    assert {r["key"]: r["sub"] for r in s["rows"]} == {"sleep": 15, "load": 100}
    assert s["value"] == 52 == sc.place("easy", 40) and s["cap_line"] == "Plafonné : après la course."
    # an outing that is no race still weighs: 50 after 3 h (R6, the legs rule)
    big = _view(_nights(_rich_rows()), {}, _runs() + [_session(D - timedelta(days=1), 200, sid=99)])
    assert {r["key"]: r["sub"] for r in big["score"]["rows"]}["load"] == 50


# ── a score from one signal, rounding ───────────────────────────────────────

def test_a_known_tone_always_has_a_score():
    """SCORE_SPEC §1: the one case without a score is the tone unknown. With
    nothing measured at all (a race tomorrow, no data) there is no number to
    place: one line."""
    one = _view(_nights({}), {}, _runs(), has_watch=False)
    assert one["score"]["value"] == 100 and one["score"]["base"] == "basé sur 1 signal sur 5"
    feel = _view(_nights({}), {D: _feel(3)}, _runs(2), has_watch=False)  # « moins bien » alone (R9; no Charge)
    assert feel["verdict"]["tone"] == "easy" and feel["score"]["chips"] == ["Ressenti"]
    assert feel["score"]["value"] == sc.place("easy", 50) == 55
    day = sante._decide_day(_nights({}), {}, [], None, [], [_route(D + timedelta(days=1))], [], D, False)
    assert day["verdict"]["rule"] == "race"
    s = sc.score_of(day, {})
    assert s["value"] is None and sc.view(s, [], has_watch=False, has_sessions=False)["line"] == \
        "Pas de score aujourd'hui : rien de mesuré."


@pytest.mark.parametrize("tone,raw,score", [
    ("rest", 50 * (15 / 45), 7),  # 39 × 16,67 % = 6,5: half up, never 6,4999… → 6
    ("rest", 100 * (15 / 45) + 75 * (10 / 45), 20),  # 49,999… is 50: 19,5 → 20
    ("easy", 65, 59), ("ok", 0, 70),
])
def test_place_rounds_half_up_on_the_exact_value(tone, raw, score):
    assert sc.place(tone, raw) == score


def test_rounding_end_to_end():
    """No band (one night of 3h20), « malade » with « alcool hier », a 3-h
    outing yesterday: rest, Sommeil 0, Charge 50, Ressenti 0; raw 750 / 45 =
    16,67, 39 × 16,67 % = 6,5 → 7."""
    rows = night_rows([0], asleep=200, start=(1, 0), end=(4, 30))
    a = _view(_nights(rows), {D: _feel(3, ["sick"], alcohol=True)}, _runs() + [_session(D - timedelta(days=1), 180,
                                                                                         sid=99)])
    s = a["score"]
    assert a["verdict"]["tone"] == "rest" and {r["key"]: r["sub"] for r in s["rows"]} == {"sleep": 0, "load": 50,
                                                                                          "feel": 0}
    assert s["value"] == 7


# ── the copy ────────────────────────────────────────────────────────────────

def test_the_missing_signals_say_measured_or_not_counted():
    """EUX-4: a new watch's 5 nights are printed by the tiles: « pas compté »
    (normale en construction), never « pas mesuré »; without a watch one
    reason, « pas de montre », not night counts."""
    new = _view(_nights(night_rows(range(0, 5))), {}, _runs())
    line = new["score"]["absent_line"]
    assert line == ("pas mesuré : Ressenti (pas encore répondu) · "
                    "pas compté : VFC et FC de nuit (normale en construction)")
    assert {t["key"] for t in new["tiles"] if t["value"]} >= {"hr", "hrv"}  # their values are on the tiles
    none = _view(_nights({}), {D: _feel(2)}, _runs(), has_watch=False)
    assert none["score"]["absent_line"] == "pas mesuré : VFC, FC de nuit et Sommeil (pas de montre)"


def test_the_no_score_line_never_asks_a_connected_athlete_to_connect():
    """EUX-3: tone unknown with Strava (3 runs) or a watch (no session): the
    action's sentence says what is missing, once; « connecte ta montre ou
    Strava » only for an athlete with neither."""
    strava = _view(_nights({}), {}, _runs(3), has_watch=False)
    assert strava["verdict"]["tone"] == "unknown"
    assert strava["verdict"]["text"] == "Il me faut 6 séances sur 6 semaines."
    assert strava["score"] == {"value": None, "line": "Pas encore de score."}
    watch = _view(_nights(night_rows(range(0, 5))), {}, [], has_watch=True)
    assert watch["verdict"]["tone"] == "unknown" and watch["score"]["line"] == "Pas encore de score."
    assert sc.view({"value": None, "why": "unknown"}, [], has_watch=False, has_sessions=False)["line"] == \
        "Pas encore de score : connecte ta montre ou Strava."


def test_the_method_fold_marks_every_choice_h():
    """EUX-5: the point values are PaceForge's (H), the studies give the thresholds; Ressenti and the sleep debt
    are in the fold; the action comes from studies AND PaceForge's choices."""
    text = " ".join(sc.METHOD)
    assert "de règles tirées des études et de choix de PaceForge (H)" in text
    assert "Sommeil (H) : pleine note dès 7 h (le seuil de Johnston 2020), 60 à 6 h (le seuil de Craven 2022)" in text
    assert "7 h = pleine note (Johnston 2020)" not in text and "6 h = 60 (Craven 2022)" not in text
    assert "90 min sous ton habitude" in text
    assert "Ressenti (H)" in text and "mieux 100, comme d'habitude 85, moins bien 50, −10 par raison" in text
    assert "malade 10" in text and "il en faut 2" not in text
    assert "Lastella 2014" in text and ("Lastella 2014", "10.1080/17461391.2012.660505") in sc.REFS
    assert "La course elle-même n'y entre pas" in text


async def test_a_new_watch_says_its_normal_is_being_built_without_a_second_count(
        as_user: AsyncClient, db_session: AsyncSession, test_user: User, monkeypatch):
    """EUX-2: 5 nights: Aujourd'hui says « ta normale se construit » and
    leaves the count to Sommeil (« 5 nuits sur 7 »): no « 14 nuits »."""
    async def today(*a, **k):
        return D
    monkeypatch.setattr(sante, "athlete_today", today)
    await _link(db_session, test_user)
    for metric, per_day in night_rows(range(0, 5), source="COROS").items():
        for d, (v, det, src) in per_day.items():
            _add(db_session, test_user, metric, d, v, det, src)
    await db_session.flush()
    page = (await as_user.get("/sante")).text
    assert "Pas encore de statut : ta normale se construit. <a" in page
    assert "se construit sur 14 nuits" not in page and "Ta normale se construit : 5 nuits sur 7." in page


def test_forced_colours_and_marks():
    """EUX-6: every tone's arc is CanvasText in forced colours (two-class rules
    outranked the one-class one); EUX-7: the turned chevron has no backplate
    over « Détail »; EUX-8: the bar's track edge and the 40/70 zone lines are
    marks (gray-400, ≥ 3:1 on the panel), not hairlines."""
    css = (ROOT / "app/static/css/interface.css").read_text()
    forced = css[css.index("@media (forced-colors: active) {\n    .pf-gauge-track"):]
    forced = forced[:forced.index("\n}")]
    assert ".pf-gauge-arc, .pf-gauge-arc.is-warn, .pf-gauge-arc.is-danger { stroke: CanvasText; }" in forced
    assert ".pf-score-more::after { forced-color-adjust: none; color: LinkText; }" in forced
    bar = re.search(r"\.pf-score-bar \{[^}]*\}", css).group(0)
    assert "inset 0 0 0 1px rgb(var(--pf-gray-400))" in bar
    macro = (ROOT / "app/templates/partials/_viz.html").read_text()
    score = macro[macro.index("{% macro viz_score"):macro.index("{% endmacro %}", macro.index("{% macro viz_score"))]
    assert 'class="pf-viz-edge"' in score and "pf-viz-grid" not in score


# ── the race page ───────────────────────────────────────────────────────────

def test_the_race_page_names_the_provisional_band_only():
    """R4: a full HRV normal and a provisional HR one (COROS nap days keep HR
    nights out): « (FC provisoire) », not the whole band « (provisoire) »."""
    rd = D - timedelta(days=3)
    rows = night_rows(range(21, 45))  # 24 nights before J-7
    for i, d in enumerate(sorted(rows["hr_night"])):
        if i % 2:  # every other night a nap day: out of the HR normal (COROS sleep summary)
            v, det, src = rows["hr_night"][d]
            rows["hr_night"][d] = (v, {**det, "method": "coros_sleep_summary", "nap_day": True}, src)
    nights = _nights(rows)
    until = rd - timedelta(days=nt.RACE_WINDOW)
    assert not nt.band(nights, "hrv", until)["provisional"] and nt.band(nights, "hr", until)["provisional"]
    assert rp.recovery(nights, rd, D)["read"][2] == "bande : ta normale avant la course (FC provisoire)"


def test_one_signal_weighs_100_percent_on_one_line():
    """One signal (« basé sur 1 signal sur 5 ») is « 100 % » in the weight column: it fits, never wraps."""
    css = (ROOT / "app/static/css/interface.css").read_text()
    rows = re.search(r"\.pf-score-rows li \{[^}]*\}", css).group(0)
    assert "grid-template-columns: minmax(0, 1fr) 88px 48px" in rows
    assert "white-space: nowrap" in re.search(r"^\.pf-score-w \{[^}]*\}", css, re.M).group(0)
