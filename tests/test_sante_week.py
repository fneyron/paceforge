"""Santé › Entraînement: the header sentence, the weekly bars, the fatigue chart, « chez toi »."""
from datetime import date, datetime, timedelta, timezone

from app.services import sante_training as st
from app.services import sante_week as wk
from tests.test_sante_training import S, T

NOW = datetime(2026, 10, 6, 12, tzinfo=timezone.utc)  # Tuesday


def _weeks(hours: list[float], longest: float = 180) -> list[dict]:
    """Sessions giving the weekly hours (oldest first, the current week last):
    the longest outing on Saturday, the rest in 3 runs early in the week."""
    ss, n = [], len(hours)
    for i, h in enumerate(hours):
        monday = date(2026, 10, 5) - timedelta(weeks=n - 1 - i)
        if not h:
            continue
        long_ = min(longest, h * 60)
        days = [5, 0, 1, 2] if monday + timedelta(days=5) <= T else [0, 1, 1, 1]
        parts = [long_] + [(h * 60 - long_) / 3] * 3
        for k, (d, m) in enumerate(zip(days, parts)):
            if m > 0:
                ss.append(S((T - monday - timedelta(days=d)).days, minutes=m, i=10 * i + k))
    st.set_loads(ss, 50, 185)
    return ss


def test_header_needs_four_weeks():
    ss = _weeks([0] * 9 + [6, 6, 2])
    assert wk.header(st.weeks(ss, NOW), ss, None, T, None).startswith("Il faut 4 semaines d'activités")
    assert wk.header(st.weeks([], NOW), [], None, T, None).startswith("Connecte Strava")


def test_header_weekly_jump_and_steady():
    ss = _weeks([8] * 10 + [11, 2])
    text = wk.header(st.weeks(ss, NOW), ss, None, T, None)
    assert text.startswith("Semaine du 28/09 : +3") and "d'un coup (11h00 contre 8h00 en moyenne)" in text
    assert "Vise 8 à 9 h cette semaine." in text and "risque" not in text
    ss = _weeks([8] * 11 + [2])
    assert wk.header(st.weeks(ss, NOW), ss, None, T, None) == (
        "Semaines régulières autour de 8 h : continue, ou ajoute 10 % pour progresser.")
    assert wk.header(st.weeks(ss, NOW), ss, None, T, "Affûtage : vise 5 à 6 h.") == "Affûtage : vise 5 à 6 h."


def test_long_outing_jump_from_ten_percent():
    ss = [S(k, minutes=150, i=k) for k in range(12, 40, 7)] + [S(2, minutes=170, id=1)]  # +13 %
    sp = wk.spike(ss, T)
    assert sp and sp[0].id == 1 and round(sp[1], 2) == 1.13
    assert wk.spike([S(k, minutes=150, i=k) for k in range(12, 40, 7)] + [S(2, minutes=160, id=1)], T) is None


def test_bars_geometry_and_links():
    ss = _weeks([8] * 10 + [11, 2])
    b = wk.bars(st.weeks(ss, NOW), ss, {}, {}, None)
    cols = b["cols"]
    assert len(cols) == 12 and cols[-1]["current"] and cols[-1]["date"] == "en cours"
    assert cols[-2]["jump"] and not cols[-3]["jump"] and cols[-2]["label"] == "11 h"
    assert all(8 <= c["x"] and c["x"] + 20 <= 352 for c in cols)
    assert cols[-2]["href"] == "/activities?page=1#week-2026-09-28"
    assert b["median"]["label"] == "8 h" and not b["has_sleep"]
    assert all(c["show_date"] for c in cols[1::2])  # every second label on a phone


def test_bars_show_the_weeks_nights_only_with_three():
    ss = _weeks([8] * 12)
    monday = date(2026, 9, 28)
    nights = {monday + timedelta(days=k): 400 for k in range(3)} | {date(2026, 9, 21): 420}
    hrv = {monday + timedelta(days=k): 50 for k in range(3)}
    b = wk.bars(st.weeks(ss, NOW), ss, nights, hrv, (60, 70))
    assert b["has_sleep"] and b["cols"][-2]["sleep"] == "6h40" and b["cols"][-3]["sleep"] is None
    assert b["cols"][-2]["hrv"] == "▼" and b["h"] > 184


def test_fatigue_chart_and_line():
    ss = [S(k, minutes=60, i=k) for k in range(1, 400)]
    st.set_loads(ss, 50, 185)
    tr = st.form(ss, T)
    first = st.weeks(ss, NOW)[0]["monday"]
    fc = wk.fatigue_chart(tr, first, T, [T - timedelta(days=20)])
    assert fc and fc["line"].startswith("M") and len(fc["bands"]) == 5 and fc["races"]
    assert all(10 <= b["y"] <= 130 for b in fc["bands"]) and fc["today"]["x"] <= 352
    assert wk.fatigue_line(tr, T) == ("Charge stable : ton fond ne bouge plus. Pour progresser, ajoute environ 10 % "
                                      "ou une sortie longue.")
    heavy = ss + [S(k, minutes=150, i=900 + k) for k in range(0, 12)]
    st.set_loads(heavy, 50, 185)
    assert wk.fatigue_line(st.form(heavy, T), T).endswith("Prévois 4 à 5 jours faciles.")


def test_welch_and_the_long_outing_link():
    d, t = wk.welch([5, 6, 4, 5, 6, 5, 4, 6], [0, 1, -1, 0, 1, 0, -1, 0])
    assert round(d, 3) == 5.125 and t > 10
    # 12 long outings; the easy runs 1–3 days after run 4 bpm higher
    runs, resid, ss = [], {}, []
    for k in range(12):
        day = 170 - 14 * k
        ss.append(S(day, minutes=200, id=1000 + k))
        for j, lag in enumerate((1, 2, 6, 9)):
            r = S(day - lag, km=10, minutes=55, id=2000 + 10 * k + j)
            runs.append(r)
            resid[r.id] = (4 if lag <= 3 else 0) + (0.5 if j % 2 else -0.5)
    out = wk.insights(ss + runs, runs, resid, [], {}, T)
    card = next(c for c in out["cards"] if c["key"] == "h2")
    assert "ta FC à allure facile est plus haute" in card["text"] and "pas forcément cause" in card["foot"]
    assert "bpm" not in card["text"]  # the numbers are on the bars only
    assert [x["value"] for x in card["bars"]] == ["+4 bpm", "0"] or card["bars"][0]["hot"]
    assert any(lk["text"].startswith("Effet de tes heures sur ton sommeil") for lk in out["locked"])
