"""The compact signal summary preserves observations, their age and separate activity context."""
from datetime import timedelta
from dataclasses import replace

from app.services import sante, sante_context as context, sante_score as sc, sante_sleep as sl
from app.services import sante_training as st
from tests.test_sante_score import D, _day, _example, _nights, _rich, _session, night_rows


def summary(rows, sessions=(), calendar_day=D):
    nights = _nights(rows, sessions)
    day = _day(rows, sessions)
    return context.synthesis(day, nights, calendar_day, st.efforts(list(sessions)), sessions, sl.deficit(nights, D))


def test_six_nights_show_six_of_seven_and_one_remaining():
    nights = _nights(night_rows(range(6)))
    day = sante._assess(nights, [], [], D)
    card = sante._night_card(nights, "hrv", D, day)
    assert day["score"]["missing"]["label"] == "repères en construction (6/7)"
    assert card["reference"]["n"] == 6 and card["reference"]["remaining"] == 1
    assert card["reference"]["required"] == 7 and not card["reference"]["ready"]
    tomorrow = _day(night_rows(range(7)), today=D)
    assert tomorrow["score"]["value"] is not None and sc.quality(tomorrow)["provisional"]


def test_high_nightly_hr_cannot_be_hidden_by_usual_hrv():
    day = _example(z_hrv=0, z_hr=2)
    assert day["score"]["tone"] == "warn" and day["score"]["value"] == 63
    assert [r["key"] for r in day["score"]["reasons"]] == ["hr"]
    assert day["state"]["word"] == "Signaux à surveiller"


def test_index_and_history_do_not_claim_a_recovery_percentage():
    day = _day(_rich())
    dial = sc.dial(day["score"], day["state"])
    assert (dial["label"], dial["unit"]) == ("Récupération", "/100")
    assert "%" not in dial["aria"] and "indice nocturne" in dial["aria"]
    chart = sc.history_card([(D - timedelta(days=1), day["state"], day["score"]),
                             (D, day["state"], day["score"])], D)
    assert "/100" in chart["data"] and "%" not in chart["data"]
    assert sc.pct(70).endswith("%")  # sleep and training still have genuine reference ratios


def test_synthesis_limits_front_reasons_but_keeps_the_others_and_alert_explanation():
    x = _example(z_hrv=-2, z_hr=2, tst=330, resp={"days": []},
                 alert={"values": [55, 56], "resp_up": True}, window={"tone": "warn", "text": "Effort récent"})
    day = {**x["day"], "score": x["score"], "state": x["state"]}
    model = context.synthesis(day, {}, D, [], [], sl.deficit({}, D))
    assert len(model["reasons"]) == 2 and len(model["more_reasons"]) == 2
    assert model["reasons"][0]["key"] == "ill"
    assert "respiration" in model["reasons"][0]["text"]
    assert len({r["key"] for r in model["reasons"] + model["more_reasons"]}) == 4


def test_a_real_ultra_remains_context_at_j13_without_changing_the_index():
    race = _session(D - timedelta(days=13), 700, sid=912, name="Trail des Crêtes")
    plain, after = summary(_rich()), summary(_rich(), [race])
    assert plain["signal"] == after["signal"]
    assert after["context"][0]["label"] == "Après Trail des Crêtes · J+13"
    assert after["context"][0]["activity_id"] == 912 and after["context"][0]["tone"] == "neutral"
    assert "jusqu" not in after["context"][0]["detail"]
    assert not plain["context"]
    assert not context.recent_effort([], [_session(D + timedelta(days=1), 100, workout_type=1)], D)
    old = _session(D - timedelta(days=29), 700)
    assert not context.recent_effort(st.efforts([old]), [old], D)


def test_even_a_first_day_ultra_is_context_not_a_nightly_signal_cap():
    race = _session(D - timedelta(days=1), 700, sid=913, name="Ultra enregistré")
    plain, after = summary(_rich()), summary(_rich(), [race])
    assert after["signal"] == plain["signal"]
    assert after["context"][0]["label"] == "Après Ultra enregistré · J+1"
    assert not any(r["key"] == "effort" for r in after["reasons"] + after["more_reasons"])


def test_post_effort_calendar_date_uses_actual_finish_not_the_sleep_cycle_anchor():
    race = _session(D - timedelta(days=1), 700, elapsed=700, hour=21, sid=914)
    effort = st.effort_of(race)
    assert effort.end.date() == D
    anchored = replace(effort, day=D - timedelta(days=1))  # sleep after a morning finish can move the cycle
    after = context.recent_effort([anchored], [race], D)
    assert after[0]["date"] == D.isoformat()
    assert after[0]["label"].endswith("J+0") and after[0]["kind"] == "effort"


def test_a_recorded_short_race_has_context_but_an_ordinary_run_does_not():
    race = _session(D - timedelta(days=3), 40, workout_type=1, sid=41)
    easy = _session(D - timedelta(days=1), 50)
    assert context.recent_effort([], [race, easy], D)[0]["activity_id"] == 41
    assert not context.recent_effort([], [easy], D)
    assert context.recent_effort([], [replace(easy, name="TransJeju")], D) == []


def test_a_long_ordinary_run_is_an_effort_not_a_recorded_race():
    long_run = _session(D - timedelta(days=1), 200, name="Sortie longue")
    assert not long_run.is_race
    assert not context.recent_effort(st.efforts([long_run]), [long_run], D)
    day_out = replace(long_run, minutes=650, elapsed=650)
    context_after = context.recent_effort(st.efforts([day_out]), [day_out], D)
    assert context_after[0]["kind"] == "effort" and context_after[0]["key"] == "after_effort"


def test_pending_or_missing_night_remains_explicitly_dated():
    pending = summary(_rich(), calendar_day=D + timedelta(days=1))
    assert not pending["freshness"]["current"] and pending["freshness"]["missing"]
    assert "en attente" in pending["freshness"]["label"] and "8 oct." in pending["freshness"]["label"]
    missing = summary(night_rows(range(1, 30)))
    assert missing["signal"]["value"] is None
    assert missing["freshness"]["last_measured"] == (D - timedelta(days=1)).isoformat()
    assert "non reçue" in missing["freshness"]["label"]


def test_sleep_gap_is_separate_from_the_index_and_its_uncertainty_is_visible():
    complete = summary(night_rows(range(80), asleep=399))
    assert complete["sleep_gap"]["minutes"] == 570
    assert complete["sleep_gap"]["estimated"] and not complete["sleep_gap"]["uncertain"]
    assert "repère" in complete["sleep_gap"]["label"] and "besoin personnel" in complete["sleep_gap"]["detail"]
    sparse = summary(night_rows([0, 1, 3, 5], asleep=399))
    assert sparse["sleep_gap"]["uncertain"] and "4/7" in sparse["sleep_gap"]["detail"]
    assert "zéro sommeil" in sparse["sleep_gap"]["detail"]


def test_compute_exports_provenance_and_synthesis_for_the_template():
    nights = _nights(_rich())
    page = sante._compute(nights, [], D, D, None, {}, True, 190, {})
    model = page["synthesis"]
    hr = next(m for m in model["methods"] if m["key"] == "hr")
    assert "distincte de la FC de repos" in hr["detail"]
    assert model["freshness"]["current"] and model["signal"]["unit"] == "/100"
    assert page["sleep"]["hero"]["need"] == "repère 8h00"
