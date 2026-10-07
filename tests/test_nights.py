"""Santé v3 nights (app/services/nights.py): the owner's real October (Transjeju
100M started 02/10 21:00 in Korea, a 2h20 nap on 7 Oct), then each rule on
synthetic nights: the 24-h total, « rendormi », context tags and excluded
nights, one band per watch, 7-night means, the illness alert, « Reprise »."""
from datetime import date, datetime, timedelta, timezone

from app.services import coros, nights as nt
from app.services.sante_training import Session
from tests import owner_coros as oc

D = date(2026, 10, 7)
RACE = (date(2026, 10, 2), "Transjeju 100M")


def owner_rows():
    """The owner's rows as the COROS sync writes them (every night in Korea, UTC+9)."""
    ov = coros.parse_sleep_overview(oc.OVERVIEW_2026)
    naps = coros.parse_naps(oc.OVERVIEW_2026)
    daily = coros.parse_daily_sleep(oc.DAILY)
    out = {"sleep": {}, "nap": {}, "hrv": {}, "hr_night": {}}
    for r in coros.sleep_dailies(ov, {d: 36 for d in ov}):
        out["sleep"][r.day] = (r.value, r.details, "COROS")
    for r in coros.nap_dailies(naps, ov):
        out["nap"][r.day] = (r.value, r.details, "COROS")
    points = coros.parse_hrv_points(oc.HRV_2026_10_07)
    for r in coros.hrv_dailies(points, ov, {D, D - timedelta(days=1)}):
        out["hrv"][r.day] = (r.value, r.details, "COROS")
    for r in coros.hr_night_dailies(daily, ov, naps):
        out["hr_night"][r.day] = (r.value, r.details, "COROS")
    return out


def session(day, hour, minutes, hr=None, offset=7200, elev=None, sid=1):
    start = datetime(day.year, day.month, day.day, hour, tzinfo=timezone.utc) - timedelta(seconds=offset)
    return Session(id=sid, start=start, day=day, sport="Run", minutes=minutes, dplus=0, km=10, speed=3.0, hr=hr,
                   hr_peak=None, suffer=None, workout_type=None, temp=None, elapsed=minutes, offset=offset,
                   elev_high=elev)


# ── the owner ───────────────────────────────────────────────────────────────

def test_owner_october():
    nights = nt.build_nights(owner_rows(), D)
    # a France session before the trip, a Korea one after: the change shows on the first nights in Korea
    sessions = [session(date(2026, 9, 26), 8, 60, offset=7200), session(date(2026, 9, 28), 8, 60, offset=32400)]
    nt.tag_nights(nights, sessions, [RACE], {})
    n7 = nights[D]
    assert (n7.asleep, n7.nap_min, n7.tst24) == (350, 140, 490)  # 5h50 + 2h20 = 8h10 sur 24 h
    assert nt.day_tst24(nights, D) == 490 >= nt.SHORT_DAY_MIN  # no short-night line on 7 Oct
    assert (n7.bed5, n7.wake5) == (datetime(2026, 10, 6, 23, 35), datetime(2026, 10, 7, 5, 40))
    [(a, b, m)] = n7.naps
    assert (a, b, m) == (datetime(2026, 10, 7, 6, 42), datetime(2026, 10, 7, 9, 7), 140)
    assert n7.in_axis(a, b) and n7.resettled  # the nap sits in the 20:00 → 12:00 axis; « rendormi »
    assert n7.hrv == 95.1 and n7.hr == 37 and n7.hr_nap_day and not n7.usable("hr")
    # the nap-only day stays a nap: no night, no 24-h total
    assert nights[date(2026, 9, 25)].asleep is None and nights[date(2026, 9, 25)].tst24 is None
    # every night of the trip sits in J-7 → J+7: drawn, never judged
    owned = [d for d, n in nights.items() if n.asleep]
    assert owned == [date(2026, 9, 29), date(2026, 9, 30), date(2026, 10, 1), date(2026, 10, 6), D]
    assert all("race" in nights[d].tags and nights[d].excluded for d in owned)
    assert "tz" in nights[date(2026, 9, 29)].tags and "tz" in nights[date(2026, 9, 30)].tags
    assert "tz" not in nights[date(2026, 10, 6)].tags
    for metric in ("hr", "hrv", "tst24"):
        assert nt.mean7(nights, metric, D) is None and nt.band(nights, metric, D) is None
    sleep7 = nt.mean7(nights, "tst24", D, untagged=False)  # the sleep tile still shows its value
    assert sleep7["n"] == 3 and round(sleep7["value"]) == round((541 + 333 + 490) / 3)
    assert nt.illness_alert(nights, D, [RACE]) is None
    assert nt.timing(nights, D)["n"] == 0  # race nights never make a regularity comment


# ── synthetic nights ────────────────────────────────────────────────────────

def night_rows(days, hr=45.0, hrv=60.0, source="Garmin", today=D, asleep=440, start=(23, 0), end=(6, 40),
               hr_method="points", nap_day=False):
    """One row of each metric per day in `days` (offsets before `today`)."""
    rows = {"sleep": {}, "hrv": {}, "hr_night": {}, "nap": {}}
    for k in days:
        d = today - timedelta(days=k)
        s = datetime.combine(d - timedelta(days=1), datetime.min.time()).replace(hour=start[0], minute=start[1])
        e = datetime.combine(d, datetime.min.time()).replace(hour=end[0], minute=end[1])
        rows["sleep"][d] = (asleep, {"main_start": s.isoformat(timespec="minutes"),
                                     "main_end": e.isoformat(timespec="minutes"), "timeline": True}, source)
        v = hr(k) if callable(hr) else hr
        w = hrv(k) if callable(hrv) else hrv
        rows["hr_night"][d] = (v, {"method": hr_method, "nap_day": nap_day}, source)
        rows["hrv"][d] = (w, {"method": "ln_mean_main", "n": 60}, source)
    return rows


def test_bands_need_14_untagged_nights_on_one_watch():
    rows = night_rows(range(1, 30, 2), hr=lambda k: 44 + k % 3, hrv=lambda k: 55 + k % 10)  # 15 nights
    nights = nt.build_nights(rows, D)
    b = nt.band(nights, "hr", D)
    assert b["n"] == 15 and b["center"] == 45 and (b["lo"], b["hi"]) == (42, 48)
    assert b["alert"] == 50  # max(2 robust SD, 5 bpm) above the median
    h = nt.band(nights, "hrv", D)
    assert h["lo"] < h["center"] < h["hi"] and abs(h["center"] - 59) < 1.5
    s = nt.band(nights, "tst24", D)
    assert (s["center"], s["lo"], s["hi"]) == (440, 410, 470)
    nights[D - timedelta(days=1)].tags.add("alcohol")  # one tagged night: 14 left
    assert nt.band(nights, "hr", D)["n"] == 14
    nights[D - timedelta(days=3)].tags.add("long")
    assert nt.band(nights, "hr", D) is None and nt.band_count(nights, "hr", D) == 13
    # a new watch: its band starts again
    rows2 = night_rows(range(1, 30, 2))
    rows2["hr_night"][D] = (50.0, {"method": "coros_sleep_summary"}, "COROS")
    assert nt.band(nt.build_nights(rows2, D), "hr", D + timedelta(days=1)) is None


def test_coros_nap_day_hr_stays_out_until_verified():
    rows = night_rows(range(0, 7), hr_method="coros_sleep_summary", nap_day=True, source="COROS")
    nights = nt.build_nights(rows, D)
    assert nt.mean7(nights, "hr", D) is None and nt.mean7(nights, "hrv", D)["n"] == 7  # our own HRV stays in
    garmin = nt.build_nights(night_rows(range(0, 7), nap_day=True), D)  # Garmin's own readings: naps can't leak
    assert nt.mean7(garmin, "hr", D)["n"] == 7


def test_mean7_and_status():
    nights = nt.build_nights(night_rows(range(0, 40), hr=lambda k: 49 if k < 7 else 45), D)
    m = nt.mean7(nights, "hr", D)
    assert m == {"value": 49, "n": 7}
    assert nt.status(m["value"], nt.band(nights, "hr", D - timedelta(days=6))) == "above"
    assert nt.status(46, nt.band(nights, "hr", D - timedelta(days=6))) == "in"
    assert nt.status(46, None) is None
    sparse = nt.build_nights(night_rows([0, 3]), D)
    assert nt.mean7(sparse, "hr", D) is None  # 2 nights of 7: no mean


def test_context_tags_exclude_nights():
    rows = night_rows(range(0, 6))
    nights = nt.build_nights(rows, D)
    d = lambda k: D - timedelta(days=k)  # noqa: E731
    sessions = [
        session(d(1), 9, 95, sid=1),  # ≥ 90 min the day before: tags the night ending the next morning (D)
        session(d(3), 21, 60, hr=170, sid=2),  # vigorous, ends 22:00 local, sleep at 23:00: ≤ 1 h (Stutz 2019)
        session(d(4), 18, 45, hr=168, sid=3),  # hard evening session (Myllymäki 2012)
        session(d(5), 18, 45, hr=110, sid=4),  # easy evening run: nothing
        session(d(2), 9, 60, elev=2100, sid=5),  # at 2 100 m that day: the night after is at altitude
    ]
    feel = {d(1): {"value": 2, "why": [], "alcohol": True}}
    nt.tag_nights(nights, sessions, [], feel, rest=45, peak=190)
    assert nights[D].tags == {"long"}
    # the night after the high session, and the one before it (a morning run up there: slept there, H)
    assert nights[d(1)].tags == {"alcohol", "altitude"} and nights[d(2)].tags == {"late", "altitude"}
    assert nights[d(3)].tags == {"late"} and nights[d(4)].tags == set()
    assert all(nights[d(k)].excluded for k in range(0, 4)) and not nights[d(4)].excluded


def test_late_nap_annotates_without_excluding():
    rows = night_rows([0, 1])
    rows["nap"][D - timedelta(days=1)] = (40, {"windows": [["2026-10-06T16:20", "2026-10-06T17:00"]]}, "Garmin")
    nights = nt.build_nights(rows, D)
    nt.tag_nights(nights)
    assert "late_nap" in nights[D].tags and not nights[D].excluded  # 6 h before 23:00 (Mograss 2022)
    assert nights[D].tst24 == 440 and nt.day_tst24(nights, D) == 480  # a pre-night nap counts for the morning
    assert nights[D - timedelta(days=1)].tst24 == 480  # and on its own day's bar


def test_race_window_and_illness_days():
    nights = nt.build_nights(night_rows(range(0, 20)), D)
    sick = {D - timedelta(days=10): {"value": 3, "why": ["sick"]}, D - timedelta(days=8): {"value": 3, "why": ["sick"]}}
    nt.tag_nights(nights, (), [(D - timedelta(days=16), "Trail")], sick)
    race = sorted(k for k in range(20) if "race" in nights[D - timedelta(days=k)].tags)
    assert race == list(range(9, 20))  # J-3 → J+7 inside the 20 days
    ill = sorted(k for k in range(20) if "ill" in nights[D - timedelta(days=k)].tags)
    assert ill == [6, 7, 8, 9, 10]  # from the first « malade » to 2 days after the last
    assert nt.illness_days({D: {"why": ["fatigue"]}}) == set()


def test_illness_alert():
    base = night_rows(range(2, 60), hr=lambda k: 44 + k % 3)
    high = night_rows([0, 1], hr=52.0)
    rows = {m: {**base[m], **high[m]} for m in base}
    nights = nt.build_nights(rows, D)
    a = nt.illness_alert(nights, D)
    assert a["days"] == [D - timedelta(days=1), D] and a["values"] == [52, 52] and a["threshold"] == 50
    assert a["resp_up"] is None  # no respiration (COROS): it never starts one anyway
    assert nt.illness_alert(nights, D - timedelta(days=1)) is None  # one night is never enough
    nights[D].tags.add("alcohol")  # a context night never fires it
    assert nt.illness_alert(nights, D) is None
    nights[D].tags.discard("alcohol")
    assert nt.illness_alert(nights, D, [(D + timedelta(days=3), "Trail")])  # race week: it still fires
    assert nt.illness_alert(nights, D, [(D - timedelta(days=2), "Trail")]) is None  # the nights after a race: never
    assert nt.alert_episodes(nights, D) == {D - timedelta(days=1), D}
    loaded = nt.build_nights(rows, D)
    feel = {}
    nt.tag_nights(loaded, (), [], feel)
    for d in nt.alert_episodes(loaded, D):
        loaded[d].tags.add("ill")
    assert loaded[D].excluded and nt.band(loaded, "hr", D + timedelta(days=1))["n"] == 58  # out of the band later
    few = nt.build_nights({m: {**night_rows(range(2, 12))[m], **high[m]} for m in base}, D)
    assert nt.illness_alert(few, D) is None  # no band, no alert


def test_reprise():
    base = night_rows(range(3, 60), hr=lambda k: 44 + k % 3)
    rows = {m: dict(base[m]) for m in base}
    nights = nt.build_nights(rows, D)
    sick = {D - timedelta(days=2): {"value": 3, "why": ["sick"]}}
    r = nt.reprise(nights, sick, D)
    assert r is None  # no « malade » for 2 days, no night or run to wait for: closed
    sick[D - timedelta(days=1)] = {"value": 3, "why": ["sick"]}
    r = nt.reprise(nights, sick, D)
    assert r["since"] == D - timedelta(days=2) and r["cause"] == "malade" and r["gates"]["no_sick"] is False
    # the chip is gone, the first easy run is still 5 bpm high: open
    r = nt.reprise(nights, sick, D + timedelta(days=2), easy=[(D - timedelta(days=30), 0.5),
                                                               (D + timedelta(days=1), 5.0)])
    assert r["gates"] == {"no_sick": True, "easy_hr": False, "night_hr": True}
    assert nt.reprise(nights, sick, D + timedelta(days=2), easy=[(D - timedelta(days=30), 0.5),
                                                                  (D + timedelta(days=1), 2.0)]) is None
    late = nt.reprise(nights, sick, D + timedelta(days=13), easy=[(D - timedelta(days=30), 0.5)])
    assert late["see_doctor"] is True and late["days"] == 15


def test_timing_rounding_and_resettled_wakes():
    rows = night_rows(range(0, 10), start=(23, 7), end=(6, 38))
    rows["nap"][D] = (60, {"windows": [["2026-10-07T07:30", "2026-10-07T08:30"]]}, "Garmin")
    rows["sleep"][D] = (300, {"main_start": "2026-10-06T23:07", "main_end": "2026-10-07T04:52"}, "Garmin")
    nights = nt.build_nights(rows, D)
    t = nt.timing(nights, D)
    assert t["n"] == 10 and t["regular_ok"] and nt.clock5(t["bed"]) == "23:05" and nt.clock5(t["wake"]) == "06:40"
    assert t["bed_sd"] == 0 and t["wake_sd"] == 0  # the « rendormi » 04:52 is out of the wake spread
    assert nights[D].resettled and not nt.early_wake(nights[D - timedelta(days=1)], t)
    assert nt.early_wake(nights[D], t) and nt.early_wake(nights[D], {"wake": None})
    assert nt.nap_tip_hour(t) == "16:00" and nt.nap_tip_hour({"bed": nt.clock_min(datetime(2026, 1, 1, 21, 50))}) \
        == "14:50"
    assert nt.round5(datetime(2026, 10, 7, 5, 38)) == datetime(2026, 10, 7, 5, 40)
    assert nt.round5(datetime(2026, 10, 6, 23, 57, 40)) == datetime(2026, 10, 7, 0, 0)


def test_quiet_short_sleep_line():
    short = nt.build_nights(night_rows(range(0, 14, 2), asleep=400), D)
    assert nt.quiet_short_sleep(short, D)
    ok = nt.build_nights(night_rows(range(0, 14, 2), asleep=430), D)
    assert not nt.quiet_short_sleep(ok, D)


async def test_load_nights_reads_the_rows_and_the_check_ins(db_session, test_user):
    from app.models.health import HealthMetric

    rows = owner_rows()
    for metric, by_day in rows.items():
        for d, (v, det, src) in by_day.items():
            db_session.add(HealthMetric(user_id=test_user.id, date=d, metric=metric, value=v, details=det,
                                        source=src, n_samples=1))
    db_session.add(HealthMetric(user_id=test_user.id, date=D, metric="feel", value=3, source="PaceForge",
                                details={"legs_heavy": True, "alcohol": True}, n_samples=1))
    db_session.add(HealthMetric(user_id=test_user.id, date=D, metric="hrv_norm", value=77, source="COROS",
                                details={"lo": 70, "hi": 84}, n_samples=1))  # a brand row: never read
    await db_session.flush()
    nights, feel = await nt.load_nights(db_session, test_user.id, D, races=[RACE])
    assert feel[D] == {"value": 3, "why": ["legs"], "alcohol": True}
    assert nights[D].tst24 == 490 and {"race", "alcohol"} <= nights[D].tags
    assert set(nights) == {date(2026, 9, 25), date(2026, 9, 29), date(2026, 9, 30), date(2026, 10, 1),
                           date(2026, 10, 6), D}
