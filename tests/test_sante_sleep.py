"""Santé v4 › the Sommeil section (app/services/sante_sleep.py, SANTE_V4_SPEC.md
§4): last night's hero (the times and the nap, the total only when the ring
does not print it), its timeline (one bar, or the hypnogram from real
intervals; naps on their own lane), the 14 nuits / 3 mois bars with the 7 h
line, the habits (medians to 5 min, regularity from 8 nights), the nights'
table, the method fold; no race, no « Cœur la nuit », no clock-window chart."""
import json
from datetime import date, datetime, timedelta

from app.services import nights as nt
from app.services import sante_sleep as sl
from app.services import viz
from tests.test_nights import night_rows as _night_rows

D = date(2026, 10, 8)


def night_rows(days, **kw):
    kw.setdefault("today", D)
    return _night_rows(days, **kw)


def _nights(rows, today=D):
    nights = nt.build_nights(rows, today)
    nt.tag_activities(nights)
    return nights


def test_no_night_ever_is_nothing_and_an_old_series_one_line():
    assert sl.sleep_section({}, D)["state"] == "never"
    old = sl.sleep_section(_nights(night_rows(range(120, 140))), D)
    assert old["state"] == "old" and "hero" not in old


def test_three_months_only_with_a_night_14_to_90_days_old_and_r_is_honoured():
    recent = _nights(night_rows(range(0, 10)))
    assert sl.offered_ranges(recent, D) == ["14"]
    both = _nights(night_rows(range(0, 60)))
    assert sl.offered_ranges(both, D) == ["14", "90"]
    s = sl.sleep_section(both, D, "90")
    assert s["r"] == "90" and set(s["bars"]) == {"14", "90"} and s["bars"]["90"]["n"] == 90
    assert sl.sleep_section(both, D, "365")["r"] == "14"  # « 1 an » is gone: the first range
    # nothing in the last 14 days, a series 3 to 8 weeks ago: « 3 mois » opens first
    mid = _nights(night_rows(range(20, 60)))
    assert sl.sleep_section(mid, D)["r"] == "90"


def test_the_hero_prints_the_times_the_nap_and_this_mornings_hours():
    """v4.4: the Sommeil row at the top says this morning's 24 h as a percentage of 8 h, so the hero prints its
    hours (once on the page), with the night and the nap it counts."""
    rows = night_rows([0], asleep=350, start=(23, 35), end=(5, 38))
    rows["nap"][D] = (140, {"windows": [[f"{D}T06:42", f"{D}T09:07"]]}, "Garmin")
    h = sl.hero(_nights(rows), D)
    assert (h["label"], h["times"], h["night"], h["nap"], h["total"]) == ("Cette nuit", "23:35 → 05:40",
                                                                          "nuit 5h50", "+ sieste 2h20",
                                                                          "8h10 sur 24\u202fh")
    t = h["timeline"]
    assert len(t["naps"]) == 1 and [nm for nm, _, _ in t["lanes"]] == ["nuit", "sieste"] and not h["out_naps"]
    # an older night (none this morning): the ring is empty, the hero prints its total
    older = sl.hero(_nights(night_rows([2])), D)
    assert older["label"] == "nuit du lun. 5 au mar. 6" and older["total"] == "7h20 sur 24 h"
    assert older["night"] is None and older["nap"] is None


def test_a_nap_outside_the_axis_is_listed_with_its_times():
    rows = night_rows([0])
    rows["nap"][D] = (60, {"windows": [[f"{D}T14:10", f"{D}T15:15"]]}, "Garmin")
    h = sl.hero(_nights(rows), D)
    assert h["out_naps"] == ["sieste 14:10 → 15:15"] and not h["timeline"]["naps"]


def test_the_timeline_is_one_bar_or_the_hypnogram():
    start, end = datetime(2026, 10, 7, 22, 40), datetime(2026, 10, 8, 7, 30)
    t = viz.timeline(start, end)
    assert t["main"] and not t["segs"]
    assert [k["label"] for k in t["ticks"]] == ["22:00", "00:00", "02:00", "04:00", "06:00", "08:00"]
    stages = [("core", start, start + timedelta(hours=2)), ("deep", start + timedelta(hours=2),
                                                             start + timedelta(hours=3)),
              ("rem", start + timedelta(hours=3), end), ("light", start, end)]  # an unknown stage is left out
    t = viz.timeline(start, end, stages=stages)
    assert t["main"] is None and len(t["segs"]) == 3 and len(t["steps"]) == 2 and t["stages"]
    assert [nm for nm, _, _ in t["lanes"]] == ["Éveil", "Paradoxal", "Léger", "Profond"]  # named as the legend
    assert [s["k"] for s in t["segs"]] == ["light", "deep", "rem"]  # each lane in its stage's colour
    # the axis never runs past 18:00 the evening before → 14:00
    t = viz.timeline(datetime(2026, 10, 7, 17, 30), datetime(2026, 10, 8, 13, 50))
    assert t["ticks"][0]["label"] == "18:00" and t["ticks"][-1]["label"] == "14:00"


def test_the_bars_rest_on_the_mean_and_print_a_night_on_a_tap():
    rows = night_rows(range(1, 14))
    rows["sleep"].update(night_rows([0], asleep=516, start=(22, 42), end=(7, 30))["sleep"])
    c = sl.bars(_nights(rows), D, "14")
    d = json.loads(c["data"])
    mean = round((13 * 440 + 516) / 14 / 5) * 5
    # one line at rest, never the toggle's « 14 nuits » nor the title's « sur 24 h » again
    assert c["read"] == [viz.hm(mean), "en moyenne", ""]
    assert d["r"][13] == ["8h36", "", "nuit du mer. 7 au jeu. 8 · 22:40 → 07:30"]
    assert c["ref"]["label"] == "7\u202fh" and c["bars"][13]["today"] and c["trend"] is None
    assert all(not b.get("miss") for b in c["bars"])
    gap = sl.bars(_nights(night_rows([0, 2])), D, "14")
    assert gap["bars"][12]["miss"] and json.loads(gap["data"])["r"][12] == ["—", "", "nuit du mar. 6 au mer. 7 · "
                                                                                  "pas de mesure"]


def test_a_short_main_night_is_said_plainly():
    """No more « nuit incomplète ? » (a hedge with a question mark): a short night is its length and its times;
    a nap without a night says so plainly."""
    rows = night_rows([0], asleep=150, start=(4, 0), end=(6, 40))
    d = json.loads(sl.bars(_nights(rows), D, "14")["data"])
    assert d["r"][13] == ["2h30", "", "nuit du mer. 7 au jeu. 8 · 04:00 → 06:40"]
    nap = {"nap": {D: (82, {"windows": [[f"{D}T01:23", f"{D}T02:52"]]}, "COROS")}}
    d = json.loads(sl.bars(_nights(nap), D, "14")["data"])
    assert d["r"][13] == ["1h22", "sieste seule", "nuit du mer. 7 au jeu. 8 · nuit non enregistrée"]
    assert "?" not in json.dumps(d, ensure_ascii=False)


def test_three_months_draws_the_7_night_mean_over_faint_bars():
    """Oura's long ranges: 90 slivers become faint 24-h bars (nap and night in one) under the 7-night mean."""
    rows = night_rows(range(0, 60))
    rows["nap"][D - timedelta(days=3)] = (60, {"windows": [[f"{D - timedelta(days=3)}T13:00",
                                                            f"{D - timedelta(days=3)}T14:00"]]}, "Garmin")
    c = sl.bars(_nights(rows), D, "90")
    assert c["trend"].startswith("M") and not any(b.get("top") for b in c["bars"])
    assert c["bars"][-4]["h"] > c["bars"][-5]["h"]  # the nap is in that day's one bar
    assert c["read"][1] == "en moyenne"


def test_habits_medians_to_5_min_and_regularity_from_8_nights():
    few = _nights(night_rows(range(0, 4)))
    assert sl.habits(few, D) is None  # 4 nights: no median yet (H)
    five = _nights(night_rows(range(0, 5), start=(23, 12), end=(7, 18)))
    assert sl.habits(five, D)["stats"] == [("Coucher", "23:10"), ("Lever", "07:20")]
    nine = night_rows(range(0, 9), start=(23, 0), end=(7, 0))
    nine["sleep"].update(night_rows([0], start=(0, 10), end=(7, 0))["sleep"])
    h = sl.habits(_nights(nine), D)
    # v4.2: no « ± 35 min », the nights within 1 h of the usual bedtime (RU-SATED: Ravyts 2021), no colour
    assert len(h["stats"]) == 2 and h["regular"] == "8 nuits sur 9 à moins d'1\u00a0h de ton coucher habituel"
    assert sl.habits(five, D)["regular"] is None  # under 8 nights (H)


def test_habits_leave_out_time_zone_nights_and_the_nights_after_an_ultra():
    """Out of the medians and the regularity (v4.2): the time-zone nights and the 4 nights after an ultra
    (Fachan 2026; Kishi 2024); the nights after a 6–10 h effort count (only their HR and HRV stay out)."""
    rows = night_rows(range(0, 8), start=(23, 0), end=(7, 0))
    rows["sleep"].update(night_rows([0, 1, 2], start=(3, 0), end=(10, 0))["sleep"])  # 3 nights at odd times
    nights = _nights(rows)
    assert sl.habits(nights, D)["stats"][0] == ("Coucher", "23:00")  # the median holds
    for k in (0, 1, 2):
        nights[D - timedelta(days=k)].tags.add("big")
    assert sl.habits(nights, D)["n"] == 8  # après grosse sortie: still in
    for k in (0, 1):
        nights[D - timedelta(days=k)].tags = {"ultra"}
    nights[D - timedelta(days=2)].tags = {"tz"}
    h = sl.habits(nights, D)
    assert h["n"] == 5 and h["stats"][:2] == [("Coucher", "23:00"), ("Lever", "07:00")]


def test_the_nights_table_30_days_newest_first_tags_as_words_never_a_race():
    """v4.3 (owner: « Ne mentionne pas les sorties dans la partie Santé »): a night after an effort says
    « récupération », as the cards do (« hors voyage, altitude et récupération »), never the outing."""
    nights = _nights(night_rows(range(0, 40)))
    nights[D - timedelta(days=2)].tags |= {"big", "race"}
    nights[D - timedelta(days=3)].tags |= {"late", "jetlag"}
    out = sl.rows(nights, D)
    assert len(out) == 30 and out[0]["iso"] == D.isoformat()
    marks = {r["iso"]: r["marks"] for r in out}
    assert marks[(D - timedelta(days=2)).isoformat()] == "◇ après un gros effort"
    assert marks[(D - timedelta(days=3)).isoformat()] == "◇ décalage horaire · ◇ effort intense le soir"
    assert not any("sortie" in m or "ultra" in m for m in marks.values())
    assert out[0]["tst"] == "7h20" and out[0]["hr"] == "45" and out[0]["hrv"] == "60"


def test_the_method_fold_is_five_plain_bullets_and_names_no_race():
    """« Comment je lis tes nuits » (v4.3, owner: « trop d'explication, simplifie et synthétise, ne mets pas les
    citations »): 5 one-line bullets in plain words, no citation, no « (H) »; its references stay in the code
    (REFS), listed on /sante/sources. v4.4: active sentences of 15 words at most, the cards' words."""
    import re

    from app.services import sante_score as sc

    assert sl.METHOD == ["Je compte ton sommeil sur 24 h, siestes comprises.",
                         "7 h ou plus en moyenne, c'est ce qui est recommandé. Une nuit sous 6 h est courte.",
                         "Ta montre estime les phases : elles montrent la forme de ta nuit, pas sa qualité.",
                         "Les nuits en voyage, en altitude ou après un gros effort ne comptent pas.",
                         "Ta montre détecte tes heures de coucher et de lever."]
    text = sc.flat(sl.METHOD)
    assert "course" not in text and "séance" not in text and "J-" not in text and "8 à 10" not in text
    assert "(H)" not in text and not re.search(r"[A-Z][a-z]+ (19|20)\d\d", text)
    assert [g for g, _ in sl.REFS] == ["Recommandations officielles", "Études scientifiques"]
    labels = [n for _, items in sl.REFS for n, _ in items]
    assert len(labels) == len(set(labels)) <= 16
    links = dict(n_l for _, items in sc.linked(sl.REFS) for n_l in items)
    assert links["CTA/NSF 2052.1‑A"] == "https://www.thensf.org/wp-content/uploads/2022/10/ANSI-CTA-NSF-2052.1-A-FINAL.pdf"
    assert links["Watson 2015b"] == "https://doi.org/10.5665/sleep.4886"
