"""The nights (app/services/nights.py): the owner's real October (Transjeju
100M started 02/10 21:00 in Korea, a 2h20 nap on 7 Oct), then each rule on
synthetic nights: the 24-h total, « rendormi », context tags and excluded
nights, one band per watch, 7-night means, the illness alert. The race window
and the check-in tags stay for the race page only (Santé v4 reads neither)."""
from datetime import date, datetime, timedelta, timezone

from app.services import coros
from app.services import nights as nt
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
    for r in coros.sleep_dailies(ov, {d: 36 for d in ov}, daily):
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
    assert n7.tst24 >= nt.SHORT_DAY_MIN  # no short night on 7 Oct
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
    assert not b["provisional"] and nt.band(nights, "hr", D, full=True) == b
    nights[D - timedelta(days=1)].tags.add("alcohol")  # one tagged night: 14 left
    assert nt.band(nights, "hr", D)["n"] == 14 and not nt.band(nights, "hr", D)["provisional"]
    nights[D - timedelta(days=3)].tags.add("long")
    # 13 nights: « provisoire » (owner decision: from 7, H), never a full band (the alert's)
    assert nt.band(nights, "hr", D)["provisional"] and nt.band(nights, "hr", D)["n"] == 13
    assert nt.band(nights, "hr", D, full=True) is None
    for k in (5, 7, 9, 11, 13, 15):  # 7 nights left: still provisional; 6: none
        nights[D - timedelta(days=k)].tags.add("alcohol")
    assert nt.band(nights, "hr", D)["n"] == 7 and nt.band(nights, "hr", D)["provisional"]
    nights[D - timedelta(days=17)].tags.add("alcohol")
    assert nt.band(nights, "hr", D) is None
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
        session(d(3), 21, 60, hr=170, sid=2),  # vigorous, ends 22:00 local, sleep at 23:00: ≤ 2 h (H; Stutz 2019)
        session(d(4), 18, 45, hr=168, sid=3),  # vigorous but ending 18:45, 4 h before sleep: nothing (§8)
        session(d(5), 18, 45, hr=110, sid=4),  # easy evening run: nothing
        session(d(2), 9, 60, elev=2100, sid=5),  # at 2 100 m that day: the night after is at altitude
    ]
    feel = {d(1): {"value": 2, "why": [], "alcohol": True}}
    nt.tag_nights(nights, sessions, [], feel, rest=45, peak=190)
    assert nights[D].tags == {"long"}
    # the night after the high session, and the one before it (a morning run up there: slept there, H)
    assert nights[d(1)].tags == {"alcohol", "altitude"} and nights[d(2)].tags == {"late", "altitude"}
    assert nights[d(3)].tags == set() and nights[d(4)].tags == set()
    assert all(nights[d(k)].excluded for k in range(0, 3)) and not nights[d(3)].excluded


def test_an_easy_evening_run_leaves_the_night_in_the_normal():
    """« sortie intense le soir » (§8): only a vigorous session — average HR ≥ 80 % of the heart-rate reserve, or
    ≥ 20 min above it in its laps (else its km splits) — ending ≤ 2 h before sleep onset (Stutz 2019; Myllymäki
    2012; the 80 % and the 2 h are H). The owner's easy 40-min evening runs, 66 % of his reserve (136 bpm on a rest
    of 35 and a peak of 188), never drop a night from his normal, even 1 h before bed; a hard 30 min at 165 bpm
    (85 %) ending 1 h 30 before sleep does; the same 5 h before sleep does not; an interval session averaging 70 %
    with 24 min of laps above 80 % does."""
    nights = nt.build_nights(night_rows(range(0, 5)), D)  # sleep onset 23:00
    d = lambda k: D - timedelta(days=k)  # noqa: E731
    easy = session(d(1), 21, 40, hr=136, sid=1)  # ends 21:40, 1 h 20 before sleep: easy
    hard = session(d(2), 21, 30, hr=165, sid=2)  # ends 21:30
    early = session(d(3), 17, 45, hr=170, sid=3)  # ends 17:45, 5 h 15 before sleep
    intervals = session(d(4), 20, 60, hr=142, sid=4)  # ends 21:00, average 70 %
    intervals.segs = ((6, 168),) * 4 + ((3, 120),) * 12  # 4 × 6 min at 168: 24 min above 157
    nt.tag_nights(nights, [easy, hard, early, intervals], [], {}, rest=35, peak=188)
    hard_hr = 35 + nt.VIGOROUS_HRR * (188 - 35)
    assert 136 < 142 < hard_hr < 165 and nights[D].tags == set()
    assert nights[d(1)].tags == {"late"} and nights[d(2)].tags == set() and nights[d(3)].tags == {"late"}
    intervals.segs = ((6, 168),) * 3 + ((3, 120),) * 12  # 18 min above: under 20, no tag
    again = nt.build_nights(night_rows(range(0, 5)), D)
    nt.tag_nights(again, [intervals], [], {}, rest=35, peak=188)
    assert again[d(3)].tags == set()


def test_hr_segments_read_the_laps_else_the_splits():
    laps = [{"moving_time": 360, "average_heartrate": 168.0}, {"elapsed_time": 180, "average_heartrate": 120},
            {"moving_time": 60}, "noise", {"moving_time": "x", "average_heartrate": 150}]
    assert nt.hr_segments(laps, None) == ((6.0, 168.0), (3.0, 120.0))
    splits = [{"moving_time": 300, "average_heartrate": 150.5}]
    assert nt.hr_segments([], splits) == ((5.0, 150.5),) and nt.hr_segments(None, None) == ()
    assert nt.hr_segments([{"moving_time": 60}], splits) == ((5.0, 150.5),)  # laps without HR: the splits


async def test_load_nights_reads_the_laps_of_a_session_close_to_sleep(db_session, test_user):
    """An interval session ending 1 h before sleep, its average under 80 % of the reserve, 24 min of its laps
    above: the night after it is « sortie intense le soir »; an easy evening run's laps are never read."""
    from app.models.activity import Activity
    from app.models.health import HealthMetric
    from app.services import sante_training as st

    today = date(2026, 10, 7)
    for metric, by_day in night_rows(range(0, 20), today=today).items():  # Garmin, sleep 23:00 → 06:40
        for d, (v, det, src) in by_day.items():
            db_session.add(HealthMetric(user_id=test_user.id, date=d, metric=metric, value=v, details=det,
                                        source=src, n_samples=1))
    eve = today - timedelta(days=1)
    laps = [{"moving_time": 360, "average_heartrate": 172}] * 4 + [{"moving_time": 180, "average_heartrate": 120}] * 8
    for sid, (hour, hr, lp) in enumerate(((20, 140, laps), (18, 130, None))):
        db_session.add(Activity(user_id=test_user.id, strava_activity_id=4400 + sid, sport_type="Run", name=f"r{sid}",
                                start_date=datetime(eve.year, eve.month, eve.day, hour, tzinfo=timezone.utc)
                                - timedelta(hours=2) - timedelta(days=sid), distance=12000,
                                moving_time=3600, elapsed_time=3600, total_elevation_gain=20, average_heartrate=hr,
                                max_heartrate=185, laps=lp, raw_data={"utc_offset": 7200}))
    await db_session.flush()
    sessions = await st.load_sessions(db_session, test_user.id, today)
    nights = await nt.load_nights(db_session, test_user.id, today, sessions=sessions, rest=45, peak=185)
    assert "late" in nights[today].tags  # the intervals: 24 min above 45 + 0,8 × 140 = 157 bpm
    late, easy = sorted(sessions, key=lambda s: s.day, reverse=True)
    assert late.segs and easy.segs is None  # ended 1 h before sleep: read; the other, 2 days ago at 19:00: never


def test_late_nap_annotates_without_excluding():
    rows = night_rows([0, 1])
    rows["nap"][D - timedelta(days=1)] = (40, {"windows": [["2026-10-06T16:20", "2026-10-06T17:00"]]}, "Garmin")
    nights = nt.build_nights(rows, D)
    nt.tag_nights(nights)
    assert "late_nap" in nights[D].tags and not nights[D].excluded  # 6 h before 23:00 (Mograss 2022)
    assert nights[D].tst24 == 440  # a nap belongs to the day it ends: the day before's 24-h total
    assert nights[D - timedelta(days=1)].tst24 == 480


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


def test_timing_rounding_and_resettled_wakes():
    rows = night_rows(range(0, 10), start=(23, 7), end=(6, 38))
    rows["nap"][D] = (60, {"windows": [["2026-10-07T07:30", "2026-10-07T08:30"]]}, "Garmin")
    rows["sleep"][D] = (300, {"main_start": "2026-10-06T23:07", "main_end": "2026-10-07T04:52"}, "Garmin")
    nights = nt.build_nights(rows, D)
    t = nt.timing(nights, D)
    assert t["n"] == 10 and t["regular_ok"] and nt.clock5(t["bed"]) == "23:05" and nt.clock5(t["wake"]) == "06:40"
    assert t["bed_sd"] == 0 and t["wake_sd"] == 0  # the « rendormi » 04:52 is out of the wake spread
    assert nights[D].resettled and not nights[D - timedelta(days=1)].resettled
    assert nt.round5(datetime(2026, 10, 7, 5, 38)) == datetime(2026, 10, 7, 5, 40)
    assert nt.round5(datetime(2026, 10, 6, 23, 57, 40)) == datetime(2026, 10, 7, 0, 0)


async def test_load_nights_reads_the_nights_never_the_check_ins_nor_a_race(db_session, test_user):
    """Santé v4: the nights tagged from the activities alone; a stored check-in (« alcool hier », « malade ») and
    a planned race are never read (owner, 2026-10-08)."""
    from app.models.health import HealthMetric
    from app.services import sante_training as st

    rows = owner_rows()
    for metric, by_day in rows.items():
        for d, (v, det, src) in by_day.items():
            db_session.add(HealthMetric(user_id=test_user.id, date=d, metric=metric, value=v, details=det,
                                        source=src, n_samples=1))
    db_session.add(HealthMetric(user_id=test_user.id, date=D, metric="feel", value=3, source="PaceForge",
                                details={"why": ["sick", "legs"], "alcohol": True}, n_samples=1))
    db_session.add(HealthMetric(user_id=test_user.id, date=D, metric="hrv_norm", value=77, source="COROS",
                                details={"lo": 70, "hi": 84}, n_samples=1))  # a brand row: never read
    await db_session.flush()
    race = session(date(2026, 10, 2), 21, 935, offset=32400, sid=8)
    race.elapsed, race.workout_type = 1013, 1
    nights = await nt.load_nights(db_session, test_user.id, D, sessions=[race], efforts=st.efforts([race]))
    # no « alcool », no « malade », no race window; 07/10 is D+4 after the 16h53 ultra (it ended on 03/10)
    assert nights[D].tst24 == 490 and nights[D].tags == {"ultra"}
    assert nights[date(2026, 10, 6)].tags == {"ultra"}  # D+3: « après ultra », D+1 → D+4 (v4.2)
    assert set(nights) == {date(2026, 9, 25), date(2026, 9, 29), date(2026, 9, 30), date(2026, 10, 1),
                           date(2026, 10, 6), D}


def test_a_session_without_a_known_offset_never_changes_the_time_zone():
    nights = nt.build_nights(night_rows(range(0, 4)), D)
    for n in nights.values():
        n.tz = 120
    blind = session(D - timedelta(days=1), 9, 40, offset=0)
    blind.offset = None  # e.g. a manual entry: its local clock is unknown
    nt.tag_nights(nights, [blind])
    assert not any("tz" in n.tags for n in nights.values())


# ── review fixes (v3) ───────────────────────────────────────────────────────

async def test_alert_nights_say_fc_de_nuit_haute_never_malade(db_session, test_user):
    """F1: an alert episode with no check-in tags its nights « FC de nuit
    haute » (out of the band like « malade »); « malade » is the chip's only."""
    from app.models.health import HealthMetric
    from app.services import race_prep as rp
    from app.services import sante_sleep as sl

    base = night_rows(range(2, 61), hr=lambda k: 47 + k % 3)
    high = night_rows([0, 1], hr=57.0)
    for metric in base:
        for d, (v, det, src) in {**base[metric], **high[metric]}.items():
            db_session.add(HealthMetric(user_id=test_user.id, date=d, metric=metric, value=v, details=det,
                                        source=src, n_samples=1))
    await db_session.flush()
    nights = await nt.load_nights(db_session, test_user.id, D)
    assert nights[D].tags == {"alert"} and nights[D - timedelta(days=1)].tags == {"alert"}
    assert nights[D].excluded and not nights[D].usable("hr")  # out of the band and the means, as before
    assert nt.TAG_WORDS["alert"] == "FC de nuit haute" and "malade" not in nt.TAG_WORDS["alert"]
    text = repr(sl.rows(nights, D))
    assert "◇ FC de nuit haute" in text and "malade" not in text
    prep = await rp._nights(db_session, test_user.id, D, D - timedelta(days=70), [], [])
    assert prep[D].tags == {"alert"}
    # the athlete's own chip: « malade »
    sick = {D: {"value": 3, "why": ["sick"]}}
    chip = nt.build_nights(night_rows(range(0, 3)), D)
    nt.tag_nights(chip, (), [], sick)
    assert nt.TAG_WORDS[sorted(chip[D].tags)[0]] == "malade"


def test_a_new_watch_is_never_read_against_the_old_watchs_band():
    """F2: 60 Garmin nights at 45 bpm, then a COROS watch: no status, no alert
    until the new watch has its own 14 nights (Dial 2025)."""
    from app.services import sante

    rows = night_rows(range(3, 63), hr=45.0, hrv=95.0)
    new = night_rows(range(0, 3), hr=49.0, hrv=70.0, source="COROS")
    nights = nt.build_nights({m: {**rows[m], **new[m]} for m in rows}, D)
    assert nt.mean_source(nights, "hr", D) == "COROS" and nt.mean7(nights, "hr", D)["value"] == 49
    for metric in ("hr", "hrv"):
        st = sante._night_stat(nights, metric, D)
        assert st["value"] is not None and st["status"] is None and st["normal"] is None  # no band yet: no word
    # 2 COROS nights at 51 after Garmin nights at 44–46: never the Garmin alert line
    rows = night_rows(range(2, 62), hr=lambda k: 44 + k % 3)
    hot = night_rows([0, 1], hr=51.0, source="COROS")
    two = nt.build_nights({m: {**rows[m], **hot[m]} for m in rows}, D)
    assert nt.illness_alert(two, D) is None and nt.alert_episodes(two, D) == set()
    # one night on each watch: never an alert either
    mixed = night_rows([1], hr=51.0, source="COROS")
    one = nt.build_nights({m: {**rows[m], **mixed[m], **night_rows([0], hr=51.0)[m]} for m in rows}, D)
    assert nt.illness_alert(one, D) is None
    # the same watch throughout: the alert still fires
    same = nt.build_nights({m: {**rows[m], **night_rows([0, 1], hr=51.0)[m]} for m in rows}, D)
    assert nt.illness_alert(same, D)["source"] == "Garmin"


def test_a_clock_change_at_home_is_no_time_zone_change():
    """L-F10: France, nights at UTC+2 until Sun 25 Oct 2026 then UTC+1: no
    « fuseau changé »; a real trip (UTC+2 → UTC+9) still is."""
    day = date(2026, 10, 30)
    nights = nt.build_nights(night_rows(range(0, 12), today=day), day)
    for d, n in nights.items():
        n.tz = 120 if d <= date(2026, 10, 25) else 60
    nt.tag_nights(nights)
    assert not any("tz" in n.tags for n in nights.values())
    sessions = [session(date(2026, 10, 20) + timedelta(days=k), 8, 50, offset=7200 if k < 5 else 3600, sid=k)
                for k in range(10)]
    blind = nt.build_nights(night_rows(range(0, 12), today=day), day)
    nt.tag_nights(blind, sessions)  # no night tz: Strava's utc_offset 7200 → 3600
    assert not any("tz" in n.tags for n in blind.values())
    trip = nt.build_nights(night_rows(range(0, 12), today=day), day)
    for d, n in trip.items():
        n.tz = 120 if d <= date(2026, 10, 25) else 540
    nt.tag_nights(trip)
    assert sorted(d for d, n in trip.items() if "tz" in n.tags) == [date(2026, 10, 26), date(2026, 10, 27),
                                                                   date(2026, 10, 28)]


def test_a_race_marked_on_strava_only_keeps_its_nights_out_and_never_fires_the_alert():
    """F4: a marathon logged as a race on Strava (no Route): J-7 → J+7 tagged
    « autour de la course », the nights after it never fire the alert."""
    from app.services import race_prep as rp

    race = Session(id=5, start=datetime(2026, 10, 4, 7, tzinfo=timezone.utc), day=date(2026, 10, 4), sport="Run",
                   minutes=200, dplus=200, km=42.2, speed=3.5, hr=160, hr_peak=180, suffer=None, workout_type=1,
                   temp=None, name="Marathon de Lyon")
    races = rp.all_races([], [race, session(D - timedelta(days=9), 8, 60, sid=9)])
    assert races == [(date(2026, 10, 4), "Marathon de Lyon")]
    base = night_rows(range(4, 70), hr=lambda k: 47 + k % 3)
    after = night_rows([0, 1, 2], hr=lambda k: {2: 60.0, 1: 56.0, 0: 55.0}[k])
    nights = nt.build_nights({m: {**base[m], **after[m]} for m in base}, D)
    nt.tag_nights(nights, [race], races, {})
    assert all("race" in nights[D - timedelta(days=k)].tags for k in (0, 1, 2, 4))
    assert nt.illness_alert(nights, D, races) is None and nt.alert_episodes(nights, D, races) == set()
    # a Route that day and the Strava session: one race
    route = type("R", (), {"race_date": "2026-10-04", "name": "Marathon de Lyon (Route)"})()
    assert rp.all_races([route], [race]) == [(date(2026, 10, 4), "Marathon de Lyon (Route)")]
