"""Santé › Course: countdown, taper, readiness, race-day freshness, race week,
recovery — pure functions on synthetic sessions and a stub race (no database)."""
import re
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from starlette.templating import Jinja2Templates

from app.services import sante_course as sc
from app.services import sante_training as st
from app.services.sante_training import Session

T = date(2026, 10, 6)  # a Tuesday


def S(days_ago: int, minutes: float = 60, sport: str = "Run", dplus: float = 50, km: float = 10,
      hr: float | None = 140, i: int = 0, **kw) -> Session:
    day = T - timedelta(days=days_ago)
    start = datetime(day.year, day.month, day.day, 7, 0, tzinfo=timezone.utc) + timedelta(minutes=i)
    return Session(id=kw.pop("id", days_ago * 100 + i), start=start, day=day, sport=sport, minutes=minutes,
                   dplus=dplus, km=km, speed=km * 1000 / (minutes * 60), hr=hr, hr_peak=180, suffer=None,
                   workout_type=0, temp=None, **kw)


def race(days: int, km: float = 68, dplus: float = 3400, target: int | None = 41400, sport: str = "trail",
         hour: int | None = 6, minute: int = 0, rid: int = 7, name: str = "Ultra des Cimes", result=None):
    return SimpleNamespace(id=rid, name=name, race_date=(T + timedelta(days=days)).isoformat(), start_hour=hour,
                           start_minute=minute, total_distance_km=km, total_elevation_gain=dplus,
                           target_time_s=target, sport_type=sport, result_json=result)


def steady(minutes: float = 108, days: int = 150, recent: float | None = None) -> list[Session]:
    """5 outings a week (Monday and Friday off): 9 h a week (`recent` minutes
    an outing over the last 4 weeks), loads set."""
    ss = [S(k, minutes=recent if recent and k < 28 else minutes, i=k) for k in range(0, days)
          if (T - timedelta(days=k)).weekday() not in (0, 4)]
    st.set_loads(ss, 50, 185)
    return ss


def tab(next_race=None, last_race=None, sessions=None, weight=72.0, usual=None):
    ss = steady() if sessions is None else sessions
    return sc.course_tab(next_race, last_race, ss, st.form(ss, T), T, weight, usual)


# ── the race and its taper ──────────────────────────────────────────────────

def test_expected_duration_is_the_objective_else_a_flagged_estimate():
    assert sc.expected_s(race(20, target=41400)) == (41400, False)
    s, est = sc.expected_s(race(20, km=68, dplus=3400, target=None))
    assert est and s == pytest.approx((68 + 34) / 7 * 3600, abs=1)
    s, est = sc.expected_s(race(20, km=42.2, dplus=120, target=None))  # road: 11 km/h
    assert est and s == pytest.approx(42.2 / 11 * 3600, abs=1)
    assert sc.expected_s(race(20, sport="bike", target=None)) == (None, False)
    assert sc.expected_s(race(20, sport="bike", target=18000)) == (18000, False)


def test_taper_lengths_and_targets():
    assert sc.taper_days(None) == 14 and sc.taper_days(3 * 3600) == 14 and sc.taper_days(2 * 3600) == 10
    sunday = date(2026, 11, 1)
    assert sc.taper_start(sunday, 5 * 3600) == date(2026, 10, 18)
    assert sc.taper_start(sunday, 2 * 3600) == date(2026, 10, 22)
    assert sc.taper_start(sunday, 11 * 3600) == date(2026, 10, 12)  # ≥ 10 h: from the Monday of S-2
    tg = sc.targets(9, 5 * 3600)
    assert set(tg) == {1, 0}
    assert tg[1] == pytest.approx((5.4, 6.75)) and tg[0] == pytest.approx((3.6, 4.5))
    long = sc.targets(9, 11 * 3600)
    assert long[2] == pytest.approx((7.2, 8.1))
    assert sc.range_txt(*tg[1]) == "5–7 h" and sc.range_txt(0.6, 0.75) == "35–45 min"


def test_base_is_the_four_complete_weeks_before_the_taper():
    mondays = [date(2026, 10, 5) - timedelta(weeks=i) for i in range(11, -1, -1)]
    wk = [{"monday": m, "minutes": 60 * (i + 1)} for i, m in enumerate(mondays)]  # 1 h … 12 h, this week last
    # taper still ahead: the last 4 complete weeks (8, 9, 10, 11 h)
    assert sc.base_hours(wk, date(2026, 10, 18)) == pytest.approx(9.5)
    # taper started on 21/09: the 4 weeks before it (6, 7, 8, 9 h)
    assert sc.base_hours(wk, date(2026, 9, 21)) == pytest.approx(7.5)
    assert sc.base_hours([dict(w, minutes=0) for w in wk], date(2026, 10, 18)) is None


def test_phases_countdown_and_sublabels():
    assert sc.countdown(26) == "dans 4 sem." and sc.countdown(10) == "J-10" and sc.countdown(14) == "J-14"
    assert sc.countdown(0) == "aujourd'hui" and sc.countdown(1) == "demain" and sc.countdown(90) == "dans 3 mois"
    far = tab(race(40, target=5 * 3600))
    assert far["state"] == "build" and far["sublabel"] == "dans 6 sem."
    assert tab(race(12, target=5 * 3600))["state"] == "taper"
    wk = tab(race(5, target=5 * 3600))
    assert wk["state"] == "race_week" and wk["sublabel"] == "J-5"
    none = tab()
    assert none == {"state": "none", "sublabel": None, "race": None, "recovery": None}


def test_header_shows_the_objective_only_when_the_athlete_set_one():
    r = tab(race(26))["race"]
    assert r["date"] == "dim. 1 nov. · départ 06:00" and r["when"] == "dans 4 sem."
    assert r["facts"] == f"68 km · {sc.dplus_txt(3400)} · objectif 11h30" and not r["estimated"]
    est = tab(race(26, target=None))["race"]
    assert "objectif" not in est["facts"] and est["estimated"]
    assert "estimée" in est["taper"]["why"]


def test_timeline_labels_fit_a_phone_and_avoid_today():
    tl = sc.timeline(T, date(2026, 10, 26), date(2026, 11, 15))  # 40 days out
    seg = {s["cls"]: s for s in tl["segs"]}
    assert seg["build"]["text"] == "Construction" and seg["build"]["tx"] > tl["today"]
    assert seg["taper"]["text"] == "Affûtage" and seg["rec"]["text"] is None  # 7 days of 61: too narrow
    assert [d["label"] for d in tl["dates"]] == ["26/10", "15/11"]
    assert sc.timeline(T, date(2026, 9, 28), date(2026, 10, 12))["segs"][0]["text"] == "Constr."
    assert sc.timeline(T, date(2026, 12, 1), date(2026, 12, 20)) == {"text": "Affûtage dans 8 sem., le 01/12."}


def test_taper_chart_prints_every_value_and_fits_a_phone():
    r = tab(race(19, target=5 * 3600))["race"]  # Sunday 25/10, this week is S-2
    ch = r["taper"]["chart"]
    cols = ch["cols"]
    assert [c["xlabel"] for c in cols] == ["S-8", "S-7", "S-6", "S-5", "S-4", "S-3", "S-2", "S-1", "S0"]
    past = [c for c in cols if c["bar"] and not c["current"]]
    assert len(past) == 6 and all(c["bar"]["label"] == "9 h" for c in past)
    assert all(c["href"].startswith("/activities?page=") for c in past)
    cur = next(c for c in cols if c["current"])
    assert cur["bar"]["label"] in ("2 h", "4 h")  # Tuesday: Sunday's and today's outings are not in it
    assert [c["target"]["label"] for c in cols if c["target"]] == ["5–7 h", "4–5 h"]
    assert ch["base_label"] == "ta base 9 h"
    assert all(c["x"] >= 0 and c["x"] + c["w"] <= 360 for c in cols)
    assert all(c["bar"]["ly"] >= 22 for c in cols if c["bar"])  # under the target row
    assert len(r["taper"]["bullets"]) == 2 and not re.search(r"\d+ à \d+ h", " ".join(r["taper"]["bullets"]))
    # far away: no chart, the bullets carry the hours
    far = tab(race(50, target=5 * 3600))["race"]["taper"]
    assert far["chart"] is None and not far["open"] and "5 à 7 h" in far["bullets"][0]


def test_taper_status_says_too_much_inside_the_taper():
    heavy = steady() + [S(0, minutes=400, id=1), S(1, minutes=300, id=2)]  # 11 h by Tuesday in S-1
    st.set_loads(heavy, 50, 185)
    r = tab(race(12, target=5 * 3600), sessions=heavy)["race"]  # Sunday 18/10: this week is S-1
    assert r["taper"]["status"].startswith("Déjà au-dessus de ta cible")
    build = tab(race(40, target=5 * 3600))["race"]["taper"]["status"]
    assert build.startswith("Encore 3 semaines de construction")


# ── prêt pour la distance ? ─────────────────────────────────────────────────

def test_readiness_words():
    assert sc.word(5, 4) == "bon" and sc.word(3.5, 4) == "juste" and sc.word(3, 4) == "court"


def test_readiness_repères_for_a_trail_ultra():
    ss = steady() + [S(10, minutes=310, dplus=2100, id=1), S(17, minutes=200, dplus=1500, id=2),
                     S(24, minutes=190, dplus=1200, id=3)]
    q = sc.readiness(race(40), ss, T, 41400, in_taper=False)
    rows = {r["key"]: r for r in q["rows"]}
    assert rows["long"]["value"] == f"5h10 · {sc.dplus_txt(2100)}" and rows["long"]["word"] == "bon"  # ≥ 4h36
    assert [e["label"] for e in rows["long"]["gauge"]["edges"]] == ["4h36", "5h45"]
    assert rows["dplus"]["word"] == "court"  # far from 70 % of 3 400 m
    assert rows["count"]["value"] == "3" and rows["count"]["word"] == "bon"
    assert "dénivelé" in q["advice"]
    # inside the taper: too late to build
    assert sc.readiness(race(10), ss, T, 41400, in_taper=True)["advice"].startswith("Trop tard pour construire")
    # no objective: no long-outing row, a link to set one; a short road race has no D+ or 3 h rows
    q = sc.readiness(race(40, target=None), ss, T, 52000, in_taper=False)
    assert "long" not in {r["key"] for r in q["rows"]} and q["goal_link"]["href"] == "/simulator/routes/7"
    assert sc.readiness(race(40, km=10, dplus=40, target=2700), ss, T, 2700, False)["rows"][0]["key"] == "long"
    assert len(sc.readiness(race(40, km=10, dplus=40, target=2700), ss, T, 2700, False)["rows"]) == 1


def test_following_the_taper_lands_fresh_on_race_day():
    lph, daily = 60.0, 9 * 60.0 / 7
    tg = sc.targets(9, 5 * 3600)
    first = T + timedelta(days=1)
    for days in range(12, 36):  # every race weekday, 2 to 5 weeks out
        rd = T + timedelta(days=days)
        # before a taper the last weeks sit above the fond (here +10 %): the mid targets land fresh
        pct = sc.project(daily / 1.1, daily, lph, first, rd, 9, tg)
        assert st.fatigue_band(pct)[0] == "fresh", (days, pct)
        # a perfectly steady athlete lands at the fresh edge; the top of the ranges is then fresh
        mid = sc.project(daily, daily, lph, first, rd, 9, tg)
        assert -27 <= mid < -5, (days, mid)
        assert st.fatigue_band(sc.project(daily, daily, lph, first, rd, 9, tg, which="hi"))[0] == "fresh"
    # no taper (the base all the way to the race): still balanced, not fresh
    assert sc.project(daily, daily, lph, first, T + timedelta(days=26), 9, {}) > -5
    # through the tab, from the athlete's own sessions (4 bigger weeks before the taper)
    f = tab(race(26, target=5 * 3600), sessions=steady(recent=125))["race"]["ready"]["fresh"]
    assert f["key"] == "fresh" and f["line"].startswith("si tu suis l'affûtage : −") and f["line"].endswith("(frais)")
    assert f["advice"] == "C'est la zone visée pour une course (repère)."
    rested = tab(race(26, target=5 * 3600), sessions=steady(days=300))["race"]["ready"]["fresh"]
    assert rested["key"] == "fresh" or rested["advice"].startswith("Très reposé : vise le haut")


# ── semaine de course ───────────────────────────────────────────────────────

def test_carbs_breakfast_and_fluid_from_the_weight():
    f = sc.food_plan(race(5), 41400, 72)
    assert "720 à 860 g pour tes 72 kg" in f["load"] and "à l'entraînement" in f["test"]
    assert f["equiv"] == "100 g de glucides ≈ 130 g de pâtes sèches."
    assert "70 à 145 g" in f["breakfast"] and "(vers 03:00)" in f["breakfast"]
    assert f["fluid"].startswith("Bois 350 à 500 ml")
    no_w = sc.food_plan(race(5), 41400, None)
    assert " g pour" not in no_w["load"] and no_w["weight_link"]["href"] == "/settings"
    assert sc.food_plan(race(5, km=10, dplus=0, target=2700), 2700, 72)["short"].startswith("Pas besoin de surcharge")
    assert sc.food_plan(race(5, sport="bike", target=None), None, 72) is None


def test_breakfast_is_left_to_the_triathlon_plan():
    f = sc.food_plan(race(5, sport="triathlon", target=5 * 3600), 5 * 3600, 70)
    assert f["breakfast"] is None and f["breakfast_link"]["href"] == "/simulator/routes/7"
    assert "g pour tes 70 kg" in f["load"]


def test_early_start_moves_bed_and_wake_day_by_day():
    usual = {"bed": 285, "wake": 810, "awake": 20}  # 22:45 → 07:30
    p = sc.sleep_plan(race(9, hour=6), T + timedelta(days=9), T, usual)
    assert p["early"].startswith("Départ 06:00 : réveil 04:00, 3h30 plus tôt que d'habitude")
    assert "30 min par jour" in p["early"]
    assert len(p["rows"]) == 7 and p["rows"][-1]["wake"] == "04:00" and p["rows"][-1]["day"] == "mer."
    assert p["rows"][0] == {"day": "jeu.", "bed": "21:25", "wake": "07:00"}
    assert p["eve"].startswith("Mal dormir la veille d'une course est courant")
    # a late enough start: no table, a bedtime for 9 h
    late = sc.sleep_plan(race(9, hour=9), T + timedelta(days=9), T, usual)
    assert not late["rows"] and "au lit vers 21:55" in late["lead"]
    # no measured nights: the generic advice
    gen = sc.sleep_plan(race(12, hour=6), T + timedelta(days=12), T, None)  # Sunday 18/10
    assert "si tu te lèves à 7 h, au lit vers 21:45" in gen["lead"]
    assert gen["early"] == "Le jour J, réveil 04:00 : avance ton lever de 30 min par jour dès mardi 13/10."
    soon = sc.sleep_plan(race(5, hour=6), T + timedelta(days=5), T, None)  # Sunday 11/10
    assert soon["early"].endswith("par jour dès demain.")
    # never in bed before 20:00, even for a very early start
    p = sc.sleep_plan(race(6, hour=5), T + timedelta(days=6), T, usual)
    assert min(r["bed"] for r in p["rows"]) == "20:00" and p["rows"][-1]["wake"] == "03:00"
    assert gen["note"].startswith("Porte ta montre")


def test_race_week_opens_at_j10_and_moves_up_at_j7():
    assert not tab(race(12))["race"]["week"]["open"]
    w = tab(race(9))["race"]["week"]
    assert w["open"] and not w["first"]
    assert tab(race(6))["race"]["week"]["first"]


# ── after the race ──────────────────────────────────────────────────────────

def test_recovery_after_a_long_race():
    last = race(-3, target=41400, rid=5, name="Trail du Lac")
    c = tab(last_race=last)
    assert c["state"] == "recovery" and c["sublabel"] == "récup J+3"
    rec = c["recovery"]
    assert rec["title"] == "Récupération · J+3" and rec["text"].startswith("Compare ton temps à ton plan")
    assert rec["debrief_href"] == "/simulator/routes/5" and rec["fatigue_href"] == "?vue=entrainement"
    # the real time decides when there is one
    short_real = race(-3, target=None, rid=5, km=10, dplus=0, result={"total_actual_s": 2700})
    assert tab(last_race=short_real)["recovery"]["text"].startswith("Course courte")
    assert tab(last_race=race(-9, km=10, dplus=0, target=2700))["recovery"] is None  # short race, J+9
    assert tab(last_race=race(-15))["recovery"] is None
    # a race soon takes the sublabel, a far one leaves it to the recovery
    assert tab(race(10), last)["sublabel"] == "J-10" and tab(race(10), last)["state"] == "taper"
    assert tab(race(30), last)["sublabel"] == "récup J+3"


def test_recovery_length_follows_the_race_length():
    # the date to resume is Aujourd'hui's; here the wording turns at J+8 (J+11 after 10 h)
    assert sc.recovery(race(-9, target=11 * 3600), T)["text"].startswith("Compare ton temps")
    assert sc.recovery(race(-9, target=5 * 3600), T)["text"].startswith("Tu peux reprendre")
    assert sc.recovery(race(-12, target=5 * 3600), T)["text"].startswith("Tu peux reprendre")


# ── the partial ─────────────────────────────────────────────────────────────

def _render(course: dict) -> str:
    return Jinja2Templates(directory="app/templates").get_template("partials/sante_course.html").render(course=course)


@pytest.mark.parametrize("make, expect", [
    (lambda: tab(), ["Pas de course prévue. Ajoute ta prochaine course : je te prépare le compte à rebours, "
                     "l'affûtage et la semaine de course.", 'href="/simulator"', "Ajouter une course"]),
    (lambda: tab(race(40)), ["Ultra des Cimes", "dans 6 sem.", "Plan de course →", "Prêt pour la distance ?",
                             "repère d'entraîneur, pas une règle", "Ton plan d'affûtage",
                             "Semaine de course : sommeil et glucides (s'ouvre à J-10)", "Construction"]),
    (lambda: tab(race(19, target=5 * 3600)), ['viewBox="0 0 360 170"', 'fill="url(#pf-race-hatch)"', "ta base 9 h",
                                              "Fraîcheur le jour J", "si tu suis l&#39;affûtage", "Affûtage"]),
    (lambda: tab(race(6), usual={"bed": 285, "wake": 810, "awake": 20}),
     ["Semaine de course : dormir et manger", "Départ 06:00", "pour tes 72 kg", "Ravitaillement →",
      "/simulator/routes/7#nutrition", "<table", "Trop tard pour construire"]),
    (lambda: tab(race(5, sport="triathlon", target=5 * 3600), weight=None),
     ["Petit-déjeuner : il est dans ton plan de triathlon →", "Ajoute ton poids dans Réglages"]),
    (lambda: tab(last_race=race(-3, rid=5)), ["Récupération · J+3", "Compare ton temps à ton plan",
                                              'href="/simulator/routes/5">Débrief →',
                                              'href="?vue=entrainement" data-vue-link>Ta fatigue →']),
    (lambda: tab(race(0)), ["aujourd&#39;hui", "Semaine de course : dormir et manger"]),
])
def test_partial_renders_each_state(make, expect):
    html = _render(make())
    for s in expect:
        assert s in html, s
    assert "blessure" not in html and "None" not in html
    assert 'preserveAspectRatio="none"' not in html
    assert "<section" not in html.split("<section", 1)[0]  # no wrapper: blocks only
