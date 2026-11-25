"""An activity's « Intervalles » chart (owner, 25 nov. 2026: « Met le graph avec les intervalles de séances »): one
bar per lap in order, its width its time, its height its pace (a bike's power, else its speed) above a floor taken
from the session, the efforts in the accent, a readout per lap; the page draws it with the laps' table only."""
import json
import pathlib
import re
from datetime import datetime, timedelta, timezone

import pytest

from app.dependencies import get_current_user
from app.models.activity import Activity
from app.services import activity_laps as al
from app.services.viz import NBSP, NNBSP, X1
from tests.browser import laps_fixture

ROOT = pathlib.Path(__file__).resolve().parents[1]


def data(c: dict) -> dict:
    return json.loads(c["data"].replace("<\\/", "</"))


def by_i(c: dict) -> dict:
    return {b["i"]: b for b in c["bars"]}


def test_one_bar_per_lap_in_order_its_width_its_time():
    laps = [{"distance": 1000, "moving_time": 300}, {"distance": 400, "moving_time": 80},
            {"distance": 1000, "moving_time": 600}]
    c = al.lap_chart(laps, "run")
    bars = c["bars"]
    assert [b["i"] for b in bars] == [0, 1, 2]
    assert bars[0]["x"] == 0 and bars[0]["x"] < bars[1]["x"] < bars[2]["x"]
    assert bars[0]["w"] / bars[2]["w"] == pytest.approx(300 / 600, rel=1e-3)
    assert bars[1]["w"] / bars[0]["w"] == pytest.approx(80 / 300, rel=1e-3)
    assert bars[2]["x"] + bars[2]["w"] == pytest.approx(X1, abs=0.05)  # the session spans the plot
    assert bars[1]["x"] - (bars[0]["x"] + bars[0]["w"]) == pytest.approx(al.GAP, abs=0.02)


def test_height_is_the_speed_above_a_floor_from_the_session():
    laps = [{"distance": 1000, "moving_time": 330}, {"distance": 300, "moving_time": 60},
            {"distance": 100, "moving_time": 75}]  # 5:30/km, 3:20/km, a walked 12:30/km
    c = al.lap_chart(laps, "trail")
    b = by_i(c)
    assert b[1]["y"] == al.TOP  # the fastest reaches the top
    assert b[1]["h"] > b[0]["h"] > b[2]["h"] >= al.MIN_H  # the walk still shows
    slow, fast = 100 / 75, 300 / 60
    assert c["floor"] == pytest.approx(min(slow * al.FLOOR_SLOW, fast * al.FLOOR_FAST))
    assert c["floor"] > 0  # never zero speed: the session would be flat
    # the height grows with the speed, in a straight line above the floor
    span = fast - c["floor"]
    assert b[0]["h"] == pytest.approx((1000 / 330 - c["floor"]) / span * (al.BASE - al.TOP), abs=0.15)


def test_the_track_session_shows_its_repetitions_as_efforts():
    laps = laps_fixture.track_session()
    c = al.lap_chart(laps, "run")
    b = by_i(c)
    reps = [i for i, lap in enumerate(laps) if lap["distance"] in (300, 302)]
    assert len(reps) == 16 and len(c["bars"]) == len(laps) == 33
    assert {i for i, bar in b.items() if bar["effort"]} == set(reps)
    assert not b[0]["effort"] and not b[len(laps) - 1]["effort"]  # warm-up, cool-down
    assert min(b[i]["h"] for i in reps) > b[0]["h"] > max(b[i]["h"] for i in range(len(laps)) if laps[i]["distance"] == 100)
    d = data(c)
    assert d["rest"] == [f"33 intervalles{NBSP}· 1h02", "3:21/km", f"le plus rapide{NBSP}: n°{NBSP}20"]
    assert d["sel"] == 33 and d["rows"] == "#laps-rows"
    assert c["summary"].startswith("Intervalles : 33 tours en 1 heure 02. Le plus rapide : le numéro 20, 3 minutes 21")
    assert "16 tours nettement plus rapides" in c["summary"]
    assert [t["label"] for t in c["refs"]] == ["4:00", "6:00", "10:00"]
    ys = [t["y"] for t in c["refs"]]
    assert ys == sorted(ys) and all(b - a >= al.REF_GAP for a, b in zip(ys, ys[1:]))
    assert all(al.TOP < y < al.BASE for y in ys)


def test_a_steady_run_with_auto_laps_shows_no_effort():
    laps = [{"distance": 1000, "moving_time": 300 + (i % 3 - 1) * 9, "average_heartrate": 150} for i in range(12)]
    laps.append({"distance": 420, "moving_time": 130})  # the last bit, a little faster
    c = al.lap_chart(laps, "run")
    assert not any(b["effort"] for b in c["bars"])
    hs = [b["h"] for b in c["bars"]]
    assert min(hs) > 0.8 * max(hs)  # level, near the top: no noise magnified
    assert len(c["refs"]) >= 2 and "nettement" not in c["summary"]


def test_a_tempo_block_stands_out():
    laps = ([{"distance": 1000, "moving_time": 330}] * 3 + [{"distance": 1000, "moving_time": 255}] * 6
            + [{"distance": 1000, "moving_time": 340}] * 2)
    c = al.lap_chart(laps, "run")
    assert [b["effort"] for b in c["bars"]] == [False] * 3 + [True] * 6 + [False] * 2


def test_the_readout_says_only_what_the_lap_has():
    laps = [{"distance": 3000, "moving_time": 990},
            {"distance": 300, "moving_time": 62, "average_heartrate": 172.6},
            {"distance": 1250, "moving_time": 3725, "average_heartrate": 120},
            {"moving_time": 90, "average_heartrate": 110},
            {}]
    d = data(al.lap_chart(laps, "run"))
    assert d["r"][1] == [f"Intervalle{NBSP}2{NBSP}· 1'02\"{NBSP}· 300{NNBSP}m", "3:26/km", f"172{NNBSP}bpm"]
    assert d["a"][1] == "Intervalle 2 : 1 minute 02, 300 mètres, 3 minutes 26 au kilomètre, 172 battements par minute"
    assert d["r"][0] == [f"Intervalle{NBSP}1{NBSP}· 16'30\"{NBSP}· 3,00{NNBSP}km", "5:30/km", ""]
    assert d["r"][2][0].endswith(f"62'05\"{NBSP}· 1,25{NNBSP}km")  # the table's own lap time
    assert d["r"][3] == [f"Intervalle{NBSP}4{NBSP}· 1'30\"", "—", f"110{NNBSP}bpm"]
    assert d["r"][4] == [f"Intervalle{NBSP}5", "—", ""] and d["a"][4] == "Intervalle 5 : pas de mesure"


def test_missing_or_zero_values_draw_no_bar_and_break_nothing():
    laps = [{"distance": 1000, "moving_time": 300}, {"distance": None, "moving_time": 120},
            {"distance": 400, "moving_time": None}, {"distance": "400", "moving_time": "80"}, "not a lap",
            {"distance": 0, "moving_time": 0}, {"distance": float("nan"), "moving_time": 60},
            {"distance": -5, "moving_time": True}, {"distance": 1000, "moving_time": 280}]
    c = al.lap_chart(laps, "run")
    assert sorted(by_i(c)) == [0, 8]
    d = data(c)
    assert len(d["r"]) == len(d["x"]) == len(d["e"]) == len(d["a"]) == 9 and c["n"] == 9
    # a lap with a time and nothing to pace it keeps its room, empty; one without a time takes none
    b = by_i(c)
    room = X1 - al.GAP * 3  # four laps with a time: 300, 120, 60, 280 s
    assert b[0]["w"] == pytest.approx(room * 300 / 760, abs=0.02)
    assert b[8]["x"] + b[8]["w"] == pytest.approx(X1, abs=0.05)
    e = d["e"]
    assert e == sorted(e) and e[0] == 0
    assert e[2] == e[3] == e[4] == e[5] == e[6] < e[7] == e[8]  # no time: no room, its edge the next lap's
    assert al.lap_chart([{"moving_time": 300}, {"distance": 0}], "run") is None
    assert al.lap_chart([], "run") is None and al.lap_chart(None, "bike") is None


def test_laps_without_a_time_after_the_last_bar_are_never_under_the_finger():
    d = data(al.lap_chart([{"distance": 1000, "moving_time": 300}, {"distance": 100}, {}], "run"))
    assert d["e"][1] > 320 and d["e"][2] > 320


def test_a_bike_draws_its_power_when_the_laps_carry_it_else_its_speed():
    laps = laps_fixture.ride()
    laps.insert(2, {"distance": 500, "moving_time": 60})  # a lap without power: its room, no bar
    c = al.lap_chart(laps, "bike")
    assert c["kind"] == "power" and c["unit"] == "W" and 2 not in by_i(c)
    assert [i for i, b in by_i(c).items() if b["effort"]] == [1, 4]
    d = data(c)
    assert d["r"][1] == [f"Intervalle{NBSP}2{NBSP}· 10'00\"{NBSP}· 6,00{NNBSP}km", f"290{NBSP}W",
                         f"36,0{NNBSP}km/h{NBSP}· 158{NNBSP}bpm"]
    assert d["rest"][1] == f"295{NBSP}W" and d["rest"][2].startswith("le plus fort")
    assert all(t["label"].isdigit() for t in c["refs"]) and len(c["refs"]) >= 2
    no_power = [{k: v for k, v in lap.items() if k != "average_watts"} for lap in laps_fixture.ride()]
    c = al.lap_chart(no_power, "bike")
    assert c["kind"] == "speed" and c["unit"] == "km/h"
    assert data(c)["r"][1][1] == f"36,0{NBSP}km/h" and len(c["bars"]) == 5
    # a run's laps with watts (a running power meter) stay a pace, as the table shows them
    assert al.lap_chart(laps_fixture.ride(), "run")["kind"] == "pace"


def test_hundreds_of_laps_stay_inside_the_plot():
    laps = laps_fixture.thirty_thirty() + [{"distance": 90, "moving_time": 30}] * 160  # 400 laps
    c = al.lap_chart(laps, "run")
    assert len(c["bars"]) == 400
    assert all(0 <= b["x"] and b["x"] + b["w"] <= X1 + 0.6 and b["w"] >= al.MIN_W for b in c["bars"])
    assert all(b["h"] >= al.MIN_H for b in c["bars"])
    xs = [b["x"] for b in c["bars"]]
    assert xs == sorted(xs)
    one = al.lap_chart([{"distance": 1000, "moving_time": 300}], "run")
    assert one["bars"][0]["w"] == pytest.approx(X1)


def test_the_kit_draws_it_and_the_script_follows_its_edges_and_rows():
    from tests.test_viz import render

    c = al.lap_chart(laps_fixture.track_session(), "run")
    html = render("{{ v.viz_laps(c) }}", c=c)
    assert html.count('class="pf-viz-bar pf-lap is-effort"') == 16 and html.count('class="pf-viz-bar pf-lap"') == 17
    assert html.count('class="pf-viz-hair"') == 3 and ">/km</text>" in html and ">4:00</text>" in html
    assert 'aria-label="Intervalles : choisis un intervalle"' in html
    assert 'aria-label="Intervalle précédent"' in html and 'aria-label="Intervalle suivant"' in html
    assert '<figcaption class="sr-only">Intervalles : 33 tours en 1 heure 02.' in html
    assert 'class="pf-viz-cross" style="visibility: hidden"' in html and 'class="pf-lap-at"' in html
    assert re.search(r'aria-valuetext="33 intervalles en 1 heure 02\. Le plus rapide', html)
    js = (ROOT / "app/static/js/pf-viz.js").read_text()
    assert "if (D.e) {" in js and "D.rows ? document.querySelector(D.rows)" in js
    css = (ROOT / "app/static/css/interface.css").read_text()
    assert ".pf-lap.is-effort { fill: rgb(var(--pf-accent)); }" in css
    assert ".pf-lap-row.is-sel { background: rgb(var(--pf-accent-soft)); box-shadow: inset 3px 0 0 rgb(var(--pf-accent)); }" in css


@pytest.fixture
async def as_user(client, test_user):
    client._transport.app.dependency_overrides[get_current_user] = lambda: test_user
    return client


def _activity(user, sid, laps, distance, moving, sport="Run"):
    return Activity(strava_activity_id=sid, user_id=user.id, sport_type=sport, name=f"Séance {sid}",
                    start_date=datetime.now(timezone.utc) - timedelta(days=1), distance=distance,
                    moving_time=moving, elapsed_time=moving, average_speed=distance / moving, raw_data={},
                    laps=laps)


async def test_the_page_draws_the_laps_above_their_table_and_only_with_it(as_user, db_session, test_user):
    track = laps_fixture.track_session()
    session = _activity(test_user, 9951, track, *laps_fixture.totals(track))
    auto = [{"distance": 1000, "moving_time": 300} for _ in range(10)]
    steady = _activity(test_user, 9952, auto, 10000, 3000)  # auto-laps, one per km: no table, no chart
    bare = _activity(test_user, 9953, None, 8000, 2400)
    broken = [{"distance": None, "moving_time": None}, {"moving_time": 60}, {"distance": 0}, {}]
    odd = _activity(test_user, 9954, broken, 5000, 1500)  # a table, nothing to draw
    db_session.add_all([session, steady, bare, odd])
    await db_session.flush()
    page = (await as_user.get(f"/activity/{session.id}")).text
    assert page.count('data-viz-key="intervalles"') == 1 and "pf-viz.js" in page
    card = page.split("Intervalles <span")[1]
    assert card.index('data-viz-key="intervalles"') < card.index('<tbody id="laps-rows"')
    assert card.count('class="pf-lap-row hover:bg-gray-50/50" data-i="') == 33
    assert 'data-i="32"' in card.split('<tbody id="laps-rows"')[1]
    for a in (steady, bare):
        page = (await as_user.get(f"/activity/{a.id}")).text
        assert "Intervalles <span" not in page and "intervalles" not in page and "pf-viz.js" not in page
    page = (await as_user.get(f"/activity/{odd.id}")).text
    assert "Intervalles <span" in page and 'data-viz-key="intervalles"' not in page and "pf-viz.js" not in page
