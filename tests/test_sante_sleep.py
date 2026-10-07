"""Santé › Sommeil (app/services/sante_sleep.py): the range choice and its
empty states, the « 1 an » weekly view, the one-sentence rules (Johnston 2020
quiet line, shorter week, regularity from onset only after « rendormi »
mornings), the hypnogram from real intervals only, « Cœur la nuit » (never
judging a context night; respiration only with the HR alert), the nights'
table."""
import json
from datetime import date, timedelta

from app.services import nights as nt
from app.services import sante_sleep as sl
from tests.test_nights import D, night_rows, owner_rows


def _nights(rows, tag=True):
    nights = nt.build_nights(rows, D)
    if tag:
        nt.tag_nights(nights)
    return nights


def test_no_night_ever_is_one_line():
    s = sl.sleep_view({}, D, None)
    assert s["state"] == "never" and s["wear"].startswith("Porte ta montre au moins 3 nuits par semaine")
    assert "J-14 à J+14" in s["wear"]


def test_default_range_is_the_first_holding_data_and_an_empty_range_says_where_the_last_series_is():
    nights = _nights(night_rows(range(120, 160)))  # 40 nights, 4 to 5 months ago
    assert sl.offered_ranges(nights, D) == ["14", "90", "365"]  # ≥ 30 nights older than 90 days
    s = sl.sleep_view(nights, D, None)
    assert s["state"] == "ok" and s["r"] == "365" and s["weekly"]
    s = sl.sleep_view(nights, D, "14")
    first, last = D - timedelta(days=159), D - timedelta(days=120)
    assert s["state"] == "empty" and s["go"] == ("365", "Voir 1 an")
    assert (first, last) == (date(2026, 5, 1), date(2026, 6, 9))
    assert s["empty"] == "Rien sur 14 jours · ta dernière série : 1 mai → 9 juin"
    few = _nights(night_rows(range(100, 110)))  # too few old nights for « 1 an »
    s = sl.sleep_view(few, D, None)
    assert [k for k, _ in s["ranges"]] == ["14", "90"] and s["state"] == "empty" and s["go"] is None
    assert sl.sleep_view(few, D, "365")["r"] == "14"  # a range not offered falls back


def test_one_year_is_one_mark_per_week():
    nights = _nights(night_rows(range(0, 200)))
    s = sl.sleep_view(nights, D, "365")
    c = s["nights"]
    assert s["weekly"] and c["n"] == 30 and s["heart"]["n"] == 30  # weeks since the first data point
    data = json.loads(c["data"])
    assert data["r"][-1][0].startswith("sem. du lun. 5 oct. · moyenne de 3 nuits")
    assert c["cols"][-1]["night"] and not s["hyps"]
    assert "semaines" in c["summary"] and "médianes par semaine" in s["heart"]["summary"]


def test_the_quiet_14_day_line_and_the_shorter_week():
    nights = _nights(night_rows(range(0, 14), asleep=400))  # 6h40 a night
    s = sl.sleep_view(nights, D, "14")
    assert s["amount_line"] == "Moins de 7 h en moyenne sur tes nuits mesurées des 14 derniers jours."
    rows = night_rows(range(7, 60), asleep=480)
    rows["sleep"].update(night_rows(range(0, 7), asleep=430)["sleep"])
    s = sl.sleep_view(_nights(rows), D, "14")
    assert s["amount_line"] == "Nuits plus courtes que d'habitude cette semaine."
    assert s["nights"]["band"] and s["nights"]["mean"]  # the usual ± 30 min and the 7-night mean are drawn


def test_regularity_in_minutes_from_onset_only_after_rendormi_mornings():
    rows = night_rows(range(0, 10), start=(23, 0), end=(7, 0))
    for k in range(3):
        rows["sleep"][D - timedelta(days=k)] = night_rows([k], start=(23, 50), end=(6, 0))["sleep"][D - timedelta(days=k)]
    s = sl.sleep_view(_nights(rows), D, "14")
    assert s["timing_line"].startswith("D'une nuit à l'autre, ton coucher bouge de ± ") and ", ton lever de" in s["timing_line"]
    # a nap ≤ 3 h after the wake: that morning leaves the wake spread (H)
    for k in range(3):
        d = D - timedelta(days=k)
        rows["nap"][d] = (60, {"windows": [[f"{d}T07:30", f"{d}T08:30"]]}, "Garmin")
    s = sl.sleep_view(_nights(rows), D, "14")
    assert "lever" not in s["timing_line"]
    assert s["nights"]["med_bands"]  # the ±1 SD band around the median onset


def test_hypnogram_only_from_real_intervals_and_labelled_by_night():
    nights = _nights(night_rows(range(0, 3)))
    n = nights[D]
    segs = [("core", n.start, n.start + timedelta(hours=2)), ("deep", n.start + timedelta(hours=2), n.end)]
    s = sl.sleep_view(nights, D, "14", samples={D: segs})
    assert [i for i, _ in s["hyps"]] == [13] and s["hyp_has_sel"]
    assert s["hyps"][0][1]["aria"].startswith("Forme de la nuit du mar. 6 au mer. 7")
    assert sl.sleep_view(nights, D, "14", samples={})["hyps"] == []
    owner = _nights(owner_rows())
    assert sl.sleep_view(owner, D, "14", samples={})["hyps"] == []  # COROS: never a hypnogram


def test_heart_never_judges_one_night_and_breath_only_with_the_alert():
    rows = night_rows(range(1, 40), hr=45, hrv=60)
    rows["hr_night"][D] = (60, {"method": "points"}, "Garmin")
    rows["hrv"][D] = (40, {"method": "ln_mean_main", "n": 60}, "Garmin")
    rows["sleep"][D] = night_rows([0])["sleep"][D]
    rows["resp_night"] = {d: (14.0, {}, "Garmin") for d in rows["sleep"]}
    nights = _nights(rows)
    s = sl.sleep_view(nights, D, "14")
    # one night is never judged: its value and the normal, a plain dot (the trend is the 7-night line's)
    assert s["heart"]["read"][1].replace("\u202f", " ") == "VFC 40 ms · FC 60 bpm"
    assert s["heart"]["read"][2] == "normale VFC 59–62, FC 42–48"  # SD floored at 0.05 (H)
    assert not any(d["out"] for p in s["heart"]["panels"] for d in p["dots"])
    assert len(s["heart"]["panels"]) == 2 and s["heart_line"] is None
    nights[D].tags.add("alcohol")  # a context night: a diamond and its word
    s = sl.sleep_view(nights, D, "14")
    assert "◇ alcool" in s["heart"]["read"][2] and s["heart"]["panels"][0]["dots"][-1]["tag"]
    nights[D].tags.discard("alcohol")
    s = sl.sleep_view(nights, D, "14", alert=True)
    assert [p["name"] for p in s["heart"]["panels"]] == ["VFC", "FC", "Resp."]
    assert s["heart_line"] == "FC de nuit nettement au-dessus de ta normale 2 nuits de suite."


def test_the_nights_table_newest_first_dashes_for_missing():
    s = sl.sleep_view(_nights(owner_rows()), D, "14", races=[(date(2026, 10, 2), "Transjeju 100M")])
    rows = s["rows"]
    assert rows[0] == {"date": "mer. 7 oct.", "iso": "2026-10-07", "tst": "8h10", "night": "5h50", "nap": "2h20",
                       "times": "23:35 → 05:40", "hr": "37", "hrv": "95", "marks": "—"}
    lone = next(r for r in rows if r["iso"] == "2026-09-25")
    assert (lone["tst"], lone["night"], lone["nap"], lone["times"]) == ("—", "—", "1h22", "—")
    assert s["coverage"] == "5 nuits mesurées sur 14"
    assert s["building"] == "Ta normale se construit : 1 nuit sur 14."  # untagged here: no « hors course »
