"""Santé › Tendances: monthly curves, changes above noise only, « ce qui va ensemble »."""
import math
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

from starlette.templating import Jinja2Templates

from app.services import sante_trends as tr
from app.services.sante_training import Session

T = date(2026, 10, 6)
MONTHS = tr.months_axis(T)


def run(days_ago: int, hr: float, i: int = 0) -> Session:
    """A flat easy 10 km at 5:30/km."""
    day = T - timedelta(days=days_ago)
    return Session(id=days_ago * 10 + i, start=datetime(day.year, day.month, day.day, 7, tzinfo=timezone.utc),
                   day=day, sport="Run", minutes=55, dplus=40, km=10, speed=10000 / 3300, hr=hr, hr_peak=185,
                   suffer=None, workout_type=0, temp=None, load=80)


def runs(hr_at, days: int = 400) -> list[Session]:
    return [run(k, hr_at(k)) for k in range(0, days, 3)]


def ctl(fn, days: int = 500) -> dict:
    return {T - timedelta(days=k): (fn(k), 0.0) for k in range(days)}


def nightly(fn, days: int = 200) -> dict:
    return {T - timedelta(days=k): fn(k) for k in range(days)}


def drift_down(k):  # −5 bpm over the last 6 months
    return 145 - 5 * max(0, 182 - k) / 182


def card(t: dict, key: str) -> dict | None:
    return next((c for c in t["cards"] if c["key"] == key), None)


def render(trends) -> str:
    return Jinja2Templates(directory="app/templates").get_template("partials/sante_trends.html").render(trends=trends)


# ── building blocks ─────────────────────────────────────────────────────────

def test_monthly_points_need_four_values():
    assert MONTHS[0] == (2025, 11) and MONTHS[-1] == (2026, 10) and len(MONTHS) == 12
    vals = {date(2026, 9, d): v for d, v in ((1, 50), (2, 60), (3, 70))}  # 3 values: no point
    vals.update({date(2026, 8, d): v for d, v in ((1, 40), (2, 50), (3, 60), (4, 100))})
    pts = tr.monthly(vals, MONTHS)
    assert pts[10] is None and pts[9] == 55  # the median, not the mean
    assert tr.monthly({date(2025, 10, d): 1 for d in range(1, 20)}, MONTHS) == [None] * 12  # before the axis


def test_change_chip_only_above_noise():
    bpm = lambda d: f"{tr.signed(d)} bpm"  # noqa: E731
    noise = [None] * 6 + [48, 49, 48, 47, 48, 47.5]
    assert tr.chip(tr.change(noise, tr.RHR_THR, hold=2), bpm, better_down=True) == {"text": "≈ stable",
                                                                                     "tone": "muted"}
    drop = [None] * 5 + [50, 50, 50, 49, 47, 46.5, 46.8]
    c = tr.chip(tr.change(drop, tr.RHR_THR, hold=2), bpm, better_down=True)
    assert c == {"text": "↘ −3 bpm en 6 mois · en mieux", "tone": "ok"}
    blip = [None] * 5 + [50, 50, 50, 50, 50, 50, 46]  # one month only: not held
    assert tr.chip(tr.change(blip, tr.RHR_THR, hold=2), bpm, better_down=True)["text"] == "à confirmer"
    rise = [None] * 5 + [50, 50, 50, 51, 53, 54, 54]
    assert tr.chip(tr.change(rise, tr.RHR_THR, hold=2), bpm, better_down=True) == {"text": "↗ +4 bpm en 6 mois",
                                                                                    "tone": "muted"}
    pct = lambda r: f"{tr.signed(r * 100)} %"  # noqa: E731
    assert tr.chip(tr.change([None] * 8 + [100, 100, 100, 108], tr.FOND_THR, back=(3,), ratio=True),
                   pct)["text"] == "≈ stable"
    assert tr.chip(tr.change([None] * 8 + [100, 100, 100, 112], tr.FOND_THR, back=(3,), ratio=True),
                   pct)["text"] == "↗ +12 % en 3 mois"
    assert tr.change([None] * 11 + [50], 2) is None and tr.chip(None, pct) is None  # nothing to compare with


def test_curve_has_a_fixed_frame_and_breaks_across_short_months():
    pts = [None, 50, 51, None, 52, 53, 54, None, None, None, None, None]
    c = tr.curve(pts, [(date(2026, 2, 10), 51.5)], MONTHS, [{"race_date": "2026-06-14", "name": "Templiers"},
                                                            {"race_date": "2024-06-14", "name": "Old"}],
                 T, lambda v: tr.num(v), "Test")
    assert (c["w"], c["h"]) == (340, 110)
    assert len(c["paths"]) == 2 and len(c["dots"]) == 5 and len(c["points"]) == 1
    assert [(lb["text"], lb["last"]) for lb in c["labels"]] == [("50", False), ("54", True)]
    assert "".join(m["text"] for m in c["months"]) == "NDJFMAMJJASO"
    assert len(c["grid"]) == 2 and len(c["races"]) == 1 and "Templiers" in c["races"][0]["name"]
    assert all(0 <= x <= 340 and 0 <= y <= 110 for x, y in c["dots"])


# ── cards ───────────────────────────────────────────────────────────────────

def test_fond_is_the_ctl_against_its_12_month_peak():
    def f(k):  # 50 → peak 100 on 15/04 → 80 at the end of July → 92 today
        d = T - timedelta(days=k)
        pts = [(date(2026, 3, 1), 50), (date(2026, 4, 15), 100), (date(2026, 7, 31), 80), (T, 92)]
        if d <= pts[0][0]:
            return 50.0
        for (d0, v0), (d1, v1) in zip(pts, pts[1:]):
            if d <= d1:
                return v0 + (v1 - v0) * (d - d0).days / (d1 - d0).days
        return 92.0
    races = [SimpleNamespace(race_date="2026-04-26", name="Trail des Templiers")]
    c = card(tr.trends_tab([], {}, {}, races, T, ctl(f)), "fond")
    assert c["value"] == "92 %" and c["unit"] == "de ton meilleur niveau de l'année"
    assert "d'avril" in c["line"] and "avant Trail des Templiers" in c["line"] and "remontes" in c["line"]
    assert c["chip"]["text"] == "↗ +15 % en 3 mois"  # 92 against 80 at the end of July
    assert c["chart"]["labels"][-1]["text"] == "92 %" and len(c["chart"]["races"]) == 1


def test_fond_waits_for_six_weeks_and_three_months():
    t = tr.trends_tab([], {}, {}, [], T, ctl(lambda k: 60.0, days=60))  # 6 weeks of warm-up, then 18 days
    assert card(t, "fond") is None and "Ton fond : 2 mois d'activités — il en faut 3" in t["locked"]
    assert "Ton fond : aucune activité en 12 mois" in tr.trends_tab([], {}, {}, [], T, {})["locked"]


def test_easy_hr_drifting_down_reads_en_mieux():
    t = tr.trends_tab(runs(drift_down), {}, {}, [], T, ctl(lambda k: 60.0))
    c = card(t, "easy_hr")
    assert c["unit"].startswith("bpm à 5:30/km") and 140 <= int(c["value"]) <= 142
    assert c["chip"]["tone"] == "ok" and c["chip"]["text"].startswith("↘ −") and c["chip"]["text"].endswith("en mieux")
    assert "travaille moins" in c["line"] and c["caption"] == "plus bas = plus en forme"
    assert len(c["chart"]["points"]) > 50  # each run, faint
    flat = card(tr.trends_tab(runs(lambda k: 145.0), {}, {}, [], T, {}), "easy_hr")
    assert flat["chip"]["text"] == "≈ stable"


def test_vo2_card_short_history_then_curve():
    since = {d: 61.0 for d in (date(2026, 8, 8) + timedelta(days=i) for i in range(60))}
    fit = {T - timedelta(days=1): {"vo2max": 61, "threshold_s": 200}}
    c = card(tr.trends_tab([], {"vo2max": since}, {"fitness": fit}, [], T, {}), "vo2")
    assert c["value"] == "61" and c["sub"] == "Allure seuil 3:20/km" and c["chart"] is None and c["chip"] is None
    assert c["note"] == "Historique depuis le 8 août : la tendance s'affiche à 4 mois."
    assert c["caption"] == "estimation de la montre, à ±10 près : regarde la direction"
    year = nightly(lambda k: 55 + 4 * max(0, 150 - k) / 150, days=300)  # held two months above +2
    c = card(tr.trends_tab([], {"vo2max": year}, {"fitness": fit}, [], T, {}), "vo2")
    assert c["chart"] and c["chip"]["text"] == "↗ +3 en 6 mois" and "progresser" in c["line"]


def test_night_cards_hrv_on_ln_and_sleep_with_score():
    hrv = nightly(lambda k: 50 if k % 2 else 72)  # skewed: the ln median sits between
    sleep = nightly(lambda k: 425 + (k % 3 - 1) * 20)
    # on the 20th the month in progress counts (before the 14th it waits)
    t = tr.trends_tab([], {"hrv": hrv, "sleep": sleep, "sleep_score": nightly(lambda k: 74), "rhr": nightly(
        lambda k: 47 + k % 3)}, {}, [], date(2026, 10, 20), {})
    v = card(t, "hrv")
    assert v["value"] == tr.num(math.exp((math.log(50) + math.log(72)) / 2)) and v["unit"] == "ms"
    assert v["chip"]["text"] == "≈ stable" and v["window"] == "200 nuits"
    s = card(t, "sleep")
    assert s["value"] == "7h05" and s["unit"] == "· score 74" and s["chart"]["labels"][-1]["text"] == "7h05"
    assert card(t, "rhr")["value"] == "48" and [c["key"] for c in t["cards"]] == ["rhr", "hrv", "sleep"]


def test_day_low_hr_stands_in_only_without_night_rhr():
    days = {T - timedelta(days=k): {"min": 52 + k % 3, "max": 150} for k in range(200)}
    t = tr.trends_tab([], {}, {"hr_day": days}, [], T, {})
    c = card(t, "hr_day_min")
    assert c["caption"] == "mesurée le jour, pas la nuit : compare-la seulement à elle-même"
    assert c["title"] == "FC la plus basse de la journée" and c["window"] == "200 jours"
    with_nights = tr.trends_tab([], {"rhr": nightly(lambda k: 47.0)}, {"hr_day": days}, [], T, {})
    assert card(with_nights, "rhr") and card(with_nights, "hr_day_min") is None


def test_locked_lines():
    few = {T - timedelta(days=k): 60.0 for k in (1, 40, 80)}
    t = tr.trends_tab([], {"hrv": few, "rhr": nightly(lambda k: 47.0)}, {}, [], T, {})
    assert "VFC : 3 nuits en 12 mois — il en faut 4 par mois pendant 3 mois" in t["locked"]
    assert "Sommeil : aucune nuit en 12 mois" in t["locked"]
    none = tr.trends_tab([], {}, {}, [], T, {})
    assert "VFC, FC au repos, sommeil : aucune nuit mesurée en 12 mois" in none["locked"]
    assert none["locked_n"] == 6 and len(none["locked"]) == 4  # fond, easy HR, VO2, the three nights
    assert tr.locked_line("FC au repos", 20, "nuit", "nuits", 2) == "FC au repos : 2 mois avec 4 nuits ou plus — il en faut 3"
    assert tr.locked_line("VFC", 1, "nuit", "nuits", 0) == "VFC : 1 nuit en 12 mois — il en faut 4 par mois pendant 3 mois"


# ── « Ce qui va ensemble » ──────────────────────────────────────────────────

def test_l1_hours_pay():
    t = tr.trends_tab(runs(drift_down), {}, {}, [], T, ctl(lambda k: 60 + 30 * max(0, 150 - k) / 150))
    assert [lk["key"] for lk in t["links"]] == ["pay"] and t["links_note"] is None
    lk = t["links"][0]
    assert lk["title"] == "Tes heures paient" and lk["text"].startswith("Depuis juin, ton fond monte")
    assert "ta FC à 5:30/km a baissé de" in lk["text"] and lk["text"].endswith("sans doute tes heures qui paient.")
    assert lk["foot"] == "chez toi, 4 mois · lien observé, pas forcément cause"
    assert [c["text"] for c in lk["slope"]["cols"]] == ["juin", "sept."]
    fond, hr = lk["slope"]["rows"]
    assert fond["t0"].endswith(" %") and fond["y1"] < fond["y0"]  # up
    assert hr["t1"].endswith(" bpm") and hr["y1"] > hr["y0"]  # down


def test_l2_heart_does_not_follow():
    t = tr.trends_tab(runs(lambda k: 140 + 6 * max(0, 80 - k) / 80), {}, {}, [], T,
                      ctl(lambda k: 60 + 30 * max(0, 150 - k) / 150))
    assert [lk["key"] for lk in t["links"]] == ["heart"]  # never next to « tes heures paient »
    text = t["links"][0]["text"]
    assert "ton cœur ne suit pas" in text and "(+5 bpm)" in text and "depuis juillet" in text


def test_l3_detraining_but_not_after_a_race():
    hr_up, falling = (lambda k: 140 + 6 * max(0, 80 - k) / 80), ctl(lambda k: 60 + 30 * min(k, 70) / 70)
    t = tr.trends_tab(runs(hr_up), {}, {}, [], T, falling)
    assert [lk["key"] for lk in t["links"]] == ["detrain"]
    assert t["links"][0]["text"].startswith("Ton fond baisse depuis juillet et ta FC à 5:30/km remonte (+5 bpm)")
    raced = tr.trends_tab(runs(hr_up), {}, {}, [SimpleNamespace(race_date="2026-09-05", name="UTMB")], T, falling)
    assert raced["links"] == [] and raced["links_note"].startswith("Pas de lien net")


def test_no_link_without_enough_common_months():
    t = tr.trends_tab([], {}, {}, [], T, ctl(lambda k: 60 + 30 * max(0, 150 - k) / 150))
    assert t["links"] == [] and t["links_note"] is None


# ── the partial ─────────────────────────────────────────────────────────────

def test_partial_renders_cards_links_and_locked():
    t = tr.trends_tab(runs(drift_down), {"rhr": nightly(lambda k: 47.0 + k % 3), "hrv": {T: 60.0}},
                      {"fitness": {T: {"vo2max": 58, "threshold_s": 200}}},
                      [SimpleNamespace(race_date="2026-06-14", name="Trail <des> Templiers")], T,
                      ctl(lambda k: 60 + 30 * max(0, 150 - k) / 150))
    html = render(t)
    for s in ("Tes courbes sur 12 mois", "Ce qui va ensemble", "Ton fond", "FC à allure facile", "VO2 max (montre)",
              "FC au repos (nuit)", "plus bas = plus en forme", "Tes heures paient", "lien observé, pas forcément cause",
              'viewBox="0 0 340 110"', 'class="pf-cards pf-cards-2 pf-cards-3"', '<details class="pf-fold',
              "Pas encore de courbe (", "VFC : 1 nuit en 12 mois", "Trail &lt;des&gt; Templiers"):
        assert s in html, s
    assert 'preserveAspectRatio="none"' not in html and "<details class=\"pf-fold pf-trend-locked\" open" not in html
    assert html.count("<article") == len(t["cards"]) + len(t["links"])


def test_partial_renders_with_nothing():
    t = tr.trends_tab([], {}, {}, [], T, {})
    assert t["cards"] == [] and t["links"] == []
    html = render(t)
    assert "Tes courbes sur 12 mois" in html and "Pas encore de courbe (6)" in html and "<svg" not in html
    assert "Ce qui va ensemble" not in html
    assert render(None).strip() == ""


def test_a_change_not_held_yet_is_neither_a_change_nor_stable():
    ch = tr.change([None] * 5 + [50, 50, 50, 50, 50, 50, 46], tr.RHR_THR, hold=2)
    assert ch["pending"] and not ch["real"]
    line = tr._line(ch, tr.months_axis(T), up="monte", down="baisse", stable="Même niveau qu'en {m}.")
    assert line == "Plus bas qu'en avril : à confirmer le mois prochain."


def test_the_month_in_progress_waits_for_its_14th_day():
    months = tr.months_axis(T)
    vals = {T - timedelta(days=k): 50.0 for k in range(6)}
    assert tr.monthly(vals, months, today=T)[-1] is None
    assert tr.monthly(vals, months, today=date(2026, 10, 20))[-1] == 50.0
