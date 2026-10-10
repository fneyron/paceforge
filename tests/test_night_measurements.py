"""Wake filtering, honest fallbacks and isolation of historical calculations."""

import json
from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import select

from app.models.health import HealthMetric
from app.models.user import User
from app.services import garmin, nights, viz
from app.services.health import _daily_series
from app.services.night_measurements import ASLEEP, WINDOW, measurement_note, select_readings
from tests.test_garmin import night
from tests.test_nights import night_rows

D = date(2026, 10, 10)


@pytest.mark.parametrize("offset", [-300, 120, 540])
def test_awake_and_wake_boundary_values_cannot_change_sleep_means(offset):
    raw = night(D)
    shift = timedelta(minutes=120 - offset)
    for key in ("sleepStartTimestampGMT", "sleepEndTimestampGMT"):
        raw["dailySleepDTO"][key] += shift.total_seconds() * 1000
    for key, time_key in (
        ("sleepHeartRate", "startGMT"),
        ("hrvData", "startGMT"),
        ("wellnessEpochRespirationDataDTOList", "startTimeGMT"),
    ):
        for point in raw[key]:
            point[time_key] += shift.total_seconds() * 1000
    for stage in raw["sleepLevels"]:
        for key in ("startGMT", "endGMT"):
            stage[key] = (datetime.fromisoformat(stage[key]) + shift).isoformat()
    parsed = garmin.parse_sleep(raw)
    assert parsed["tz"] == offset
    for key, asleep_value, awake_value in (("hr", 50, 120), ("hrv", 80, 10), ("resp", 14, 30)):
        parsed[key] = [
            (
                t,
                awake_value
                if 4 <= t.hour < 5 and t.minute < 30 or t >= parsed["end"]
                else asleep_value,
            )
            for t, _ in parsed[key]
        ]
    rows = {r.metric: r for r in garmin.night_dailies(parsed, True)}
    assert rows["hr_night"].value == 50
    assert rows["hrv"].value == 80
    assert rows["resp_night"].value == 14
    for metric in ("hr_night", "hrv", "resp_night"):
        assert rows[metric].details["n"] == 84
        assert rows[metric].details["excluded_awake"] == 6
        assert rows[metric].details["scope"] == ASLEEP


def test_sleep_gaps_overlap_and_half_open_boundaries():
    start = datetime(2026, 10, 9, 23)

    def at(m):
        return start + timedelta(minutes=m)

    intervals = [
        ("core", at(-5), at(30)),
        ("rem", at(20), at(40)),
        ("awake", at(25), at(35)),
        ("deep", at(50), at(70)),
    ]
    points = [(at(m), m) for m in (0, 24, 25, 30, 34, 35, 40, 45, 50, 59, 60)]
    values, details = select_readings(points, start, at(60), intervals)
    assert values == [0, 24, 35, 50, 59]
    assert details == {"scope": ASLEEP, "excluded_awake": 3, "excluded_unknown": 2}


@pytest.mark.parametrize("count", [11, 12])
def test_minimum_count_is_after_filtering_and_deduplication(count):
    parsed = garmin.parse_sleep(night(D))
    start, end = parsed["start"], parsed["end"]
    parsed["intervals"] = [
        ("core", start, start + timedelta(minutes=count)),
        ("awake", start + timedelta(minutes=count), end),
    ]
    parsed["hrv"] = [(start + timedelta(minutes=i), 80) for i in range(count)]
    parsed["hrv"] += [parsed["hrv"][0]] * 20
    parsed["hrv"] += [(start + timedelta(hours=2, minutes=i), 10) for i in range(30)]
    rows = {r.metric: r for r in garmin.night_dailies(parsed, True)}
    assert ("hrv" in rows) == (count == 12)
    if count == 12:
        assert rows["hrv"].value == 80 and rows["hrv"].details["n"] == 12


def test_missing_stages_are_labelled_and_awake_only_never_falls_back():
    parsed = garmin.parse_sleep(night(D, levels=False))
    rows = {r.metric: r for r in garmin.night_dailies(parsed, False)}
    assert rows["hrv"].details["scope"] == WINDOW and rows["hrv"].details["n"] == 90
    assert "éveils possibles" in measurement_note("Garmin", rows["hrv"].details, "hrv")
    parsed["intervals"] = [("awake", parsed["start"], parsed["end"])]
    rows = {r.metric: r for r in garmin.night_dailies(parsed, True)}
    assert "hrv" not in rows and "hr_night" not in rows
    assert rows["resp_night"].details == {"method": "garmin_summary"}


def test_unknown_stage_codes_do_not_become_sleep_or_crash():
    raw = night(D)
    for bad in (float("inf"), float("nan"), 1.9, -1, None):
        raw["sleepLevels"][0]["activityLevel"] = bad
        parsed = garmin.parse_sleep(raw)
        assert len(parsed["intervals"]) == 4


def test_new_means_bands_and_alerts_never_use_unfiltered_history():
    rows = night_rows(range(25), today=D, hr=45, hrv=100)
    for k in range(3):
        d = D - timedelta(days=k)
        rows["hrv"][d] = (50, {"method": "ln_mean_main", "scope": ASLEEP, "n": 20}, "Garmin")
        rows["hr_night"][d] = (80, {"method": "points", "scope": ASLEEP}, "Garmin")
    ns = nights.build_nights(rows, D)
    assert nights.mean7(ns, "hrv", D) == {"value": pytest.approx(50), "n": 3}
    assert nights.mean7(ns, "hr", D) == {"value": 80, "n": 3}
    assert nights.normal(ns, "hrv", D) is None  # 3 filtered nights, not 25 comparable nights
    assert nights.illness_alert(ns, D) is None
    assert nights.rest_hr(ns, D) == 80


def test_respiratory_context_cannot_compare_filtered_values_with_an_unfiltered_band():
    rows = night_rows(range(25), today=D, hr=45)
    rows["resp_night"] = {}
    for k in range(25):
        d = D - timedelta(days=k)
        rows["hr_night"][d] = (60 if k < 2 else 45, {"method": "points", "scope": ASLEEP}, "Garmin")
        rows["resp_night"][d] = (
            25 if k < 2 else 14,
            {"method": "points", **({"scope": ASLEEP} if k < 2 else {})},
            "Garmin",
        )
    ns = nights.build_nights(rows, D)
    assert nights.illness_alert(ns, D)["resp_up"] is None
    assert nights.resp_up_nights(ns, D) is None


def test_selected_night_explains_its_own_method_including_for_screen_readers():
    days = [D - timedelta(days=2), D - timedelta(days=1), D]
    sources = ["COROS", "Garmin", "Garmin (sommeil)"]
    notes = [
        measurement_note("COROS", {}, "hrv"),
        measurement_note("Garmin", {}, "hrv"),
        measurement_note("Garmin", {"scope": ASLEEP}, "hrv"),
    ]
    card = viz.night_card(
        "vfc",
        days,
        [80, 90, 75],
        band=[(70, 90)] * 3,
        prov=[False] * 3,
        mean=[80, 90, 75],
        unit="ms",
        unit_long="millisecondes",
        name="VFC",
        sources=sources,
        notes=notes,
    )
    data = json.loads(card["data"])
    for i, note in enumerate(notes):
        assert note in data["r"][i][2] and note in data["a"][i]
    # Each calculation's isolated mean stays visible, without joining methods.
    assert card["mean"].count("M") == card["mean"].count("L") == 3
    assert "non vérifiable" in measurement_note("COROS", {"method": "coros_sleep_summary"}, "hr")


async def test_only_recomputed_rejected_values_are_removed(db_session, test_user):
    other = User(email="other-night@example.com")
    db_session.add(other)
    await db_session.flush()
    parsed = {}
    for k in range(5):
        day = D - timedelta(days=k)
        n = garmin.parse_sleep(night(day))
        n["intervals"] = [("awake", n["start"], n["end"])]
        parsed[day] = n
        db_session.add(
            HealthMetric(
                user_id=test_user.id,
                date=day,
                metric="hrv",
                value=90,
                source="Garmin",
                details={"method": "ln_mean_main"},
            )
        )
    missing = D - timedelta(days=6)
    parsed[missing] = garmin.parse_sleep(night(missing, readings=False))
    for uid, day, source in [
        (test_user.id, missing, "Garmin"),
        (test_user.id, D - timedelta(days=7), "COROS"),
        (other.id, D, "Garmin"),
    ]:
        db_session.add(
            HealthMetric(
                user_id=uid,
                date=day,
                metric="hrv",
                value=90,
                source=source,
                details={"method": "ln_mean_main"},
            )
        )
    await db_session.flush()
    rows = [r for n in parsed.values() for r in garmin.night_dailies(n, True)]
    assert await garmin.drop_rejected_values(db_session, test_user.id, parsed, rows) == 5
    remaining = (await db_session.execute(select(HealthMetric))).scalars().all()
    assert {(r.user_id, r.date, r.source) for r in remaining} == {
        (test_user.id, missing, "Garmin"),
        (test_user.id, D - timedelta(days=7), "COROS"),
        (other.id, D, "Garmin"),
    }


async def test_legacy_fitness_reader_also_separates_calculations(db_session, test_user):
    for k, scope in [(0, ASLEEP), (1, ASLEEP), (2, None), (3, WINDOW)]:
        for metric in ("hrv", "hr_night"):
            db_session.add(
                HealthMetric(
                    user_id=test_user.id,
                    date=D - timedelta(days=k),
                    metric=metric,
                    value=80,
                    source="Garmin",
                    details={"method": "ln_mean_main", "scope": scope},
                )
            )
    await db_session.flush()
    series, _ = await _daily_series(db_session, test_user.id, D - timedelta(days=10), D)
    assert set(series["hrv"]) == set(series["rhr"]) == {D, D - timedelta(days=1)}
