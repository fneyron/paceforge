"""Santé v4.3 + Activités « Semaines » (V43_BRIEF.md and the owner's messages of 2026-10-08 evening): what is not
pinned elsewhere — the owner's real Les Houches days read at the DB level, the time-zone tags from each user's own
data (COROS: the HRV series' timezone; Garmin: local against GMT sleep times; Strava: the activities' offsets; a
user with none of them), and every feature for every user (§H, owner: « Il faut que toutes les fonctionnalités
soient utilisables par les utilisateurs, pas juste moi »): a COROS-only user, a Garmin-only user, a Strava-only
user, a cyclist, a user living in UTC−5 and a brand-new user, each page rendered, its words coherent, no owner
data. Heuristic numbers read here are (H) where they are set."""
import re
from datetime import date, datetime, time, timedelta, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.activity import Activity
from app.models.health import HealthMetric
from app.models.user import User
from app.services import coros, garmin, sante
from app.services import nights as nt
from app.services import sante_score as sc
from app.services import sante_today as td
from app.services import sante_training as st
from tests import test_coros, test_garmin
from tests.owner_v4 import D8, seed_owner_v4
from tests.test_sante import rested  # noqa: F401 (a fixture: a 7h30 need, these 7h20 nights « suffisant »)

as_user, no_commit = test_coros.as_user, test_coros.no_commit
D = date(2026, 10, 8)
OWNER = ("Transjeju", "Morning Trail Run", "Les Houches", "Florent", "Jeju")  # never on another user's page


def _ms(dt: datetime) -> int:
    return int(dt.replace(tzinfo=timezone.utc).timestamp() * 1000)


def coros_rows(today: date, days, tz_q=lambda k: 8, asleep=440, hrv=60.0, hr=45.0) -> dict:
    """COROS nights as its sync writes them: sleep_dailies (the main window and the timezone of the night's HRV
    series, in quarter hours) and hrv_dailies (PaceForge's VFC from the raw readings: UTC timestamps, timezone);
    « Sleep HR » as the nightly HR. Each night 23:00 → 06:40 local."""
    overview, points, tz = {}, [], {}
    for k in days:
        d = today - timedelta(days=k)
        q = tz_q(k)
        start = datetime.combine(d - timedelta(days=1), time(23, 0))
        overview[d] = {"start": start, "end": start + timedelta(minutes=asleep + 20), "asleep": asleep,
                       "period": asleep + 20}
        tz[d] = q
        for i in range(20):
            local = start + timedelta(minutes=10 + 20 * i)
            points.append((int((local - timedelta(minutes=15 * q)).replace(tzinfo=timezone.utc).timestamp()), q,
                           hrv(k) if callable(hrv) else hrv))
    rows = {"sleep": {}, "hrv": {}, "hr_night": {}}
    for r in coros.sleep_dailies(overview, tz):
        rows["sleep"][r.day] = (r.value, r.details, "COROS")
    for r in coros.hrv_dailies(sorted(points), overview):
        rows["hrv"][r.day] = (r.value, r.details, "COROS")
    for d in overview:
        rows["hr_night"][d] = (hr(0) if callable(hr) else hr, {"method": "coros_sleep_summary", "nap_day": False},
                               "COROS")
    return rows


def garmin_rows(today: date, days, tz_min=lambda k: 120, hrv=60.0, hr=45.0) -> dict:
    """Garmin nights through its own parser: dailySleepData with the local and the GMT start (the night's UTC
    offset), raw HR and HRV readings every 20 min; night_dailies writes them as the sync does. 23:00 → 06:40."""
    rows = {"sleep": {}, "hrv": {}, "hr_night": {}}
    for k in days:
        d = today - timedelta(days=k)
        start = datetime.combine(d - timedelta(days=1), time(23, 0))
        end = start + timedelta(minutes=460)
        gmt = timedelta(minutes=tz_min(k))
        ts = [start - gmt + timedelta(minutes=10 + 20 * i) for i in range(22)]
        data = {"dailySleepDTO": {"calendarDate": d.isoformat(), "sleepTimeSeconds": 440 * 60,
                                  "sleepStartTimestampLocal": _ms(start), "sleepEndTimestampLocal": _ms(end),
                                  "sleepStartTimestampGMT": _ms(start - gmt)},
                "sleepHeartRate": [{"value": hr, "startGMT": _ms(t)} for t in ts],
                "hrvData": [{"value": hrv(k) if callable(hrv) else hrv, "startGMT": _ms(t)} for t in ts]}
        for r in garmin.night_dailies(garmin.parse_sleep(data), False):
            if r.metric in rows:
                rows[r.metric][r.day] = (r.value, r.details, "Garmin")
    return rows


async def _seed(db: AsyncSession, user: User, rows: dict):
    for metric, per_day in rows.items():
        for d, (v, det, src) in per_day.items():
            db.add(HealthMetric(user_id=user.id, date=d, metric=metric, value=v, source=src, details=det,
                                n_samples=1))
    await db.flush()


def _act(user: User, sid: int, day: date, hour: int, minutes: float, sport="Run", km=10.0, dplus=60.0,
         offset=7200, name="Footing", hr=140, **raw) -> Activity:
    """An activity as Strava writes it: its UTC start from the local `hour` and its utc_offset (s)."""
    start = datetime(day.year, day.month, day.day, hour, tzinfo=timezone.utc) - timedelta(seconds=offset)
    return Activity(user_id=user.id, strava_activity_id=sid, sport_type=sport, name=name, start_date=start,
                    distance=km * 1000, moving_time=int(minutes * 60), elapsed_time=int(minutes * 60) + 60,
                    total_elevation_gain=dplus, average_speed=km * 1000 / (minutes * 60), average_heartrate=hr,
                    max_heartrate=180, raw_data={"utc_offset": offset, **raw})


def _tags(nights) -> dict:
    return {d: sorted(n.tags) for d, n in nights.items() if n.tags}


@pytest.fixture
def on_day(monkeypatch):
    """Santé's and Activités' « today » is D (8 Oct 2026, a Thursday)."""
    async def today(*a, **k):
        return D
    monkeypatch.setattr(sante, "athlete_today", today)
    monkeypatch.setattr(st, "athlete_today", today)
    return D


def _main(html: str) -> str:
    return html.split('id="main-content"', 1)[1].split("</main>", 1)[0]


def _coherent(html: str) -> str:
    """The page's own content, checked for what must never show: a traceback, a template left open, a Python
    value printed raw, the owner's data."""
    main = _main(html)
    text = re.sub(r"<[^>]+>", " ", re.sub(r"<script.*?</script>", " ", main, flags=re.S))
    for bad in ("Traceback", "{{", "{%", "None", "undefined", "nan", "À ménager", "Bien récupéré", "Contributeurs"):
        assert not re.search(rf"\b{re.escape(bad)}\b", text), bad
    for name in OWNER:
        assert name not in main, name
    return main


# ── the owner's Les Houches days, from the DB (brief §A1, §F) ───────────────

async def test_owner_les_houches_two_efforts_from_his_activities(db_session: AsyncSession, test_user: User):
    """His real 18/09 (9h04 stops included, +3 511 m) and 19/09 (7h04, +2 847 m) at Les Houches: two Très
    longues, each its own window (45 to D+2, 65 to D+5): the last 65 day is 24/09, 25/09 is free. On 24/09 no
    night was measured: no score (owner, 2026-10-09, like WHOOP: « Mets un cadran vide, oui »), and « Effort
    récent » says it is the window's last day; on 25/09 there is no score and no Effort récent."""
    await seed_owner_v4(db_session, test_user)
    sessions = await st.load_sessions(db_session, test_user.id, D8)
    houches = [e for e in st.efforts(sessions) if e.start_day in (date(2026, 9, 18), date(2026, 9, 19))]
    assert [(e.kind, e.day, round(e.minutes), e.caps) for e in houches] == [
        ("very_long", date(2026, 9, 18), 544, ((2, 45), (5, 65))),
        ("very_long", date(2026, 9, 19), 424, ((2, 45), (5, 65)))]
    caps = {k: (st.effort_window(houches, date(2026, 9, k)) or {}).get("cap") for k in range(18, 26)}
    assert caps == {18: 45, 19: 45, 20: 45, 21: 45, 22: 65, 23: 65, 24: 65, 25: None}
    page = await sante.health_page(db_session, test_user.id, today=date(2026, 9, 24))
    assert (page["score"]["value"], page["state"], page["line"]) == (None, None, td.NO_NIGHT)
    assert (page["dials"][1]["value"], page["dials"][1]["sub"]) == ("—", "pas de score")
    effort = {r["key"]: r for r in page["rows"]}["effort"]
    assert (effort["value"], effort["word"], effort["tone"]) == ("dernier jour", "avant d'être récupéré", "warn")
    free = await sante.health_page(db_session, test_user.id, today=date(2026, 9, 25))
    assert free["score"]["value"] is None and free["state"] is None and free["line"] == td.NO_NIGHT
    assert "effort" not in {r["key"] for r in free["rows"]}


async def test_the_owner_s_sante_page_names_no_outing(as_user: AsyncClient, db_session: AsyncSession,
                                                     test_user: User, on_day):
    """Owner, 2026-10-08 evening: « Ne mentionne pas les sorties dans la partie Santé, ça complexifie : mets juste
    les scores. » His page on 08/10 (the Transjeju 6 days ago, Les Houches 3 weeks ago) names no activity and no
    outing — state line, Détail, history readouts, cards, the nights' table and its readouts — but for one plain
    bullet of « Comment je calcule »: Récupération limited after a big effort, up to 2 weeks after an ultra."""
    import html as H

    await seed_owner_v4(db_session, test_user)
    main = _main((await as_user.get("/sante")).text)
    for name in ("Transjeju", "Morning Trail Run", "Les Houches", "100M"):
        assert name not in main, name
    folds = re.findall(r'<details class="pf-fold pf-method">(.*?)</details>', main, flags=re.S)
    calc = re.findall(r"<li>(.*?)</li>", folds[0])
    assert [H.unescape(b) for b in calc if "sortie" in b or "ultra" in b] == [sc.typo(sc.METHOD)[1]]
    rest = H.unescape(main.replace(folds[0], ""))
    for word in ("sortie", "ultra", "course", "il y a", "Plafonné", "Grosse"):
        assert word not in rest, word
    assert "◇ après un gros effort" in rest and "Récupération en cours" in rest  # the 06/10 and 07/10 nights; today


# ── time zones: from each user's own data (brief §H) ────────────────────────

def test_coros_the_night_offset_comes_from_its_hrv_series():
    """COROS: a night's UTC offset is its HRV series' timezone (quarter hours): 20 nights at UTC+2 (8), then
    UTC+9 (36) from 5 nights ago: 7 zones east, « décalage horaire » on the first 7 of them (⌈1 × 7⌉, Janse van
    Rensburg 2021): they never fire the alert, and count in the normal like any night (2026-10-08); no activity
    needed."""
    rows = coros_rows(D, range(0, 25), tz_q=lambda k: 36 if k <= 4 else 8)
    nights = nt.build_nights(rows, D)
    assert {nights[D].tz, nights[D - timedelta(days=10)].tz} == {540, 120}
    nt.tag_activities(nights)
    assert _tags(nights) == {D - timedelta(days=k): ["jetlag"] for k in range(0, 5)}
    assert not any(nt.alert_night(nights, (), D - timedelta(days=k)) for k in range(0, 5))
    assert nt.mean7(nights, "hrv", D)["n"] == 7


def test_garmin_the_night_offset_comes_from_its_local_and_gmt_times():
    """Garmin: a night's UTC offset is its local start minus its GMT start: Paris (+2 h) then New York (−4 h), 6
    zones west: « décalage horaire » for ⌈0,5 × 6⌉ = 3 nights; a 1-h clock change at home is no change."""
    rows = garmin_rows(D, range(0, 20), tz_min=lambda k: -240 if k <= 5 else 120)
    nights = nt.build_nights(rows, D)
    assert nights[D].tz == -240 and nights[D - timedelta(days=9)].tz == 120
    nt.tag_activities(nights)
    assert _tags(nights) == {D - timedelta(days=k): ["jetlag"] for k in (5, 4, 3)}
    dst = nt.build_nights(garmin_rows(D, range(0, 20), tz_min=lambda k: 60 if k <= 5 else 120), D)
    nt.tag_activities(dst)
    assert _tags(dst) == {}


def test_strava_activities_give_the_offset_when_the_nights_have_none():
    """A watch that writes no timezone: the activities' offsets (Strava's local against UTC start) say it: a run
    in France (+2 h) then one in Korea (+9 h) two days later: the nights from that day on are « décalage
    horaire ». A user with neither (nights without an offset, activities without one): no tag, no crash."""
    rows = garmin_rows_without_tz(range(0, 20))
    nights = nt.build_nights(rows, D)
    assert all(n.tz is None for n in nights.values())
    fr = st.Session(id=1, start=datetime(2026, 10, 3, 6, tzinfo=timezone.utc), day=date(2026, 10, 3), sport="Run",
                    minutes=50, dplus=0, km=9, speed=3, hr=None, hr_peak=None, workout_type=None,
                    temp=None, offset=7200)
    kr = st.Session(id=2, start=datetime(2026, 10, 5, 0, tzinfo=timezone.utc), day=date(2026, 10, 5), sport="Run",
                    minutes=50, dplus=0, km=9, speed=3, hr=None, hr_peak=None, workout_type=None,
                    temp=None, offset=32400)
    nt.tag_activities(nights, [fr, kr])
    tagged = sorted(d for d, n in nights.items() if "jetlag" in n.tags)
    assert tagged[0] == date(2026, 10, 5) and len(tagged) == 4  # 05/10 → 08/10 (7 nights from 05/10: the rest ahead)
    bare = nt.build_nights(garmin_rows_without_tz(range(0, 20)), D)
    unknown = [st.Session(id=3, start=datetime(2026, 10, 3, 6, tzinfo=timezone.utc), day=date(2026, 10, 3),
                          sport="Run", minutes=50, dplus=0, km=9, speed=3, hr=None, hr_peak=None,
                          workout_type=None, temp=None, offset=None)]
    nt.tag_activities(bare, unknown)
    assert _tags(bare) == {}


def garmin_rows_without_tz(days) -> dict:
    """Garmin-like nights whose rows carry no timezone (an older sync, or a watch that writes none)."""
    rows = garmin_rows(D, days)
    for metric in rows:
        for d, (v, det, src) in list(rows[metric].items()):
            rows[metric][d] = (v, {k: x for k, x in det.items() if k != "tz"}, src)
    return rows


# ── every user (brief §H) ────────────────────────────────────────────────────

async def test_a_coros_only_user(as_user: AsyncClient, db_session: AsyncSession, test_user: User, on_day, rested):
    """COROS nights every night for 40 days, no activity: a full normal, the score from the nights (no Charge,
    no window), « Bonne récupération »; the dials, the Récupération card's rows and the chart cards' status lines;
    no Effort récent; no activity: « 0 min » over « pas encore d'habitude »; Activités empty."""
    await test_coros._link(db_session, test_user)
    await _seed(db_session, test_user, coros_rows(D, range(0, 40), hrv=lambda k: 60.0 + (k % 3) - 1))
    page = await sante.health_page(db_session, test_user.id, today=D)
    assert page["state"]["word"] == "Bonne récupération" and page["score"]["value"] == 100
    assert [p["key"] for p in page["score"]["parts"]] == ["hrv", "hr", "sleep"] and page["score"]["absent"] == []
    # its 7-night means at their usual values to the percent: « comme d'habitude » (« 0 % » said in words)
    assert page["vfc"]["status"]["text"] == page["fc"]["status"]["text"] == "comme d'habitude"
    assert [(d["key"], d["value"], d["unit"], d["sub"], d["tone"]) for d in page["dials"]] == [
        ("sommeil", "94", "%", "suffisant", "sleep"), ("recup", "100", "%", "bonne", "ok"),
        ("entrainement", "0 min", None, "pas encore d'habitude", "accent")]
    assert [(f["key"], f["value"], f["word"], f["tone"]) for f in page["rows"]] == [
        ("vfc", None, "comme d'habitude", "ok"), ("fc", None, "comme d'habitude", "ok")]
    main = _coherent((await as_user.get("/sante")).text)
    assert "Détail du score" not in main and "pf-card-status is-ok" in main and 'class="pf-row is-ok"' in main
    act = _coherent((await as_user.get("/activities")).text)
    assert 'id="semaines"' not in act and "Aucune activité pour l" in act


async def test_a_garmin_only_user(as_user: AsyncClient, db_session: AsyncSession, test_user: User, on_day):
    """Garmin nights through its own parser (12 nights, last night included: a « provisoire » normal from 7, H),
    no activity: VFC and FC de nuit in the score, the cards' readouts say « (provisoire) »."""
    await test_garmin._link(db_session, test_user)
    await _seed(db_session, test_user, garmin_rows(D, range(0, 12)))
    page = await sante.health_page(db_session, test_user.id, today=D)
    assert {p["key"] for p in page["score"]["parts"]} == {"hrv", "hr", "sleep"}
    assert all(p["prov"] for p in page["score"]["parts"] if p["key"] in ("hrv", "hr"))
    assert page["vfc"]["read"][2].endswith("(provisoire)") and page["state"]["word"] in td.WORDS.values()
    main = _coherent((await as_user.get("/sante")).text)
    assert "Récupération" in main and (await as_user.get("/activities")).status_code == 200


async def test_a_strava_only_user(as_user: AsyncClient, db_session: AsyncSession, test_user: User, on_day):
    """Strava, no watch: no nightly score (« Connecte ta montre pour ta récupération. », the connect links), no
    rows, no cards, no folds but the recovery one; Activités: « Semaines » with its three measures."""
    for k in range(1, 70, 2):
        db_session.add(_act(test_user, 50_000 + k, D - timedelta(days=k), 7, 55, dplus=120))
    await db_session.flush()
    page = await sante.health_page(db_session, test_user.id, today=D)
    assert page["state"] is None and page["line"] == td.NO_WATCH and page["rows"] == []
    main = _coherent((await as_user.get("/sante")).text)
    assert td.NO_WATCH in main and 'href="/settings#coros"' in main and "Les chiffres de chaque nuit" not in main
    act = _coherent((await as_user.get("/activities")).text)
    assert re.findall(r'data-range="(\w+)"', act) == ["duree", "distance", "dplus"]


async def test_a_cyclist(as_user: AsyncClient, db_session: AsyncSession, test_user: User, on_day):
    """Rides only, flat (no D+ recorded): a 7-h ride yesterday is one class lower (M4: a Longue, 65 until D+3, its
    night D+1 out of the normal by its time); Activités offers Durée and Distance, never D+."""
    await test_garmin._link(db_session, test_user)
    await _seed(db_session, test_user, garmin_rows(D, range(0, 40)))
    for k in range(3, 80, 4):
        db_session.add(_act(test_user, 60_000 + k, D - timedelta(days=k), 8, 120, sport="Ride", km=55, dplus=0,
                            name="Vélo", hr=125))
    db_session.add(_act(test_user, 60_999, D - timedelta(days=1), 7, 420, sport="Ride", km=180, dplus=0,
                        name="Grand tour", hr=128))
    await db_session.flush()
    page = await sante.health_page(db_session, test_user.id, today=D)
    w = page["score"]["parts"]
    assert {p["key"] for p in w} == {"hrv", "hr", "sleep"} and page["score"]["value"] == 65
    assert page["state"]["word"] == "Récupération en cours" and page["state"]["text"] is None
    marks = {r["iso"]: r["marks"] for r in page["sleep"]["rows"]}
    assert marks[D.isoformat()] == "◇ après un gros effort"  # 7 h: its nights follow its time (M4), no outing named
    _coherent((await as_user.get("/sante")).text)
    act = _coherent((await as_user.get("/activities?sport=bike")).text)
    assert re.findall(r'data-range="(\w+)"', act) == ["duree", "distance"] and "Vélo" in act


async def test_a_user_in_utc_minus_5(as_user: AsyncClient, db_session: AsyncSession, test_user: User,
                                     monkeypatch):
    """A user living in New York (UTC−4 in October, −5 in winter): the athlete's date is theirs (a run at 21:00
    local is on that local day, the morning after at 01:00 UTC still that day), their Garmin nights at −240
    without any time-zone tag (no change), the pages coherent."""
    for k in range(1, 40, 2):  # evening runs at 21:00 local: 01:00 UTC the next day
        db_session.add(_act(test_user, 70_000 + k, D - timedelta(days=k), 21, 50, offset=-14400))
    await test_garmin._link(db_session, test_user)
    await _seed(db_session, test_user, garmin_rows(D, range(0, 30), tz_min=lambda k: -240))
    await db_session.flush()
    sessions = await st.load_sessions(db_session, test_user.id, D)
    assert {s.day for s in sessions} == {D - timedelta(days=k) for k in range(1, 40, 2)}  # their local days
    assert all(s.start.hour == 1 for s in sessions)  # 01:00 UTC
    # at 02:00 UTC on 9 Oct it is still 8 Oct in New York
    assert await st.athlete_today(db_session, test_user.id, datetime(2026, 10, 9, 2, tzinfo=timezone.utc)) == D
    nights = await nt.load_nights(db_session, test_user.id, D, sessions=sessions, efforts=st.efforts(sessions))
    assert not any(n.tags & {"tz", "jetlag"} for n in nights.values())
    page = await sante.health_page(db_session, test_user.id, today=D)
    assert page["state"]["word"] in td.WORDS.values() and page["rows"]

    async def today(*a, **k):
        return D
    monkeypatch.setattr(sante, "athlete_today", today)
    _coherent((await as_user.get("/sante")).text)
    _coherent((await as_user.get("/activities")).text)


async def test_a_brand_new_user(as_user: AsyncClient, db_session: AsyncSession, test_user: User):
    """Nothing yet: Santé's connect panel, Activités' empty state (no « Semaines » title, the filter), the
    sources page; every filter and measure link works."""
    html = (await as_user.get("/sante")).text
    assert "Ta montre et tes activités" in html and "pf-rings" not in html
    act = (await as_user.get("/activities")).text
    assert 'id="semaines"' not in act and 'aria-label="Filtrer les activités par sport"' in act
    assert "Aucune activité pour l" in act
    for url in ("/activities?sport=bike", "/activities?m=dplus", "/activities?sport=other&m=distance",
                "/activities?m=nope&sport=nope", "/sante/sources"):
        r = await as_user.get(url)
        assert r.status_code == 200, url
    assert "Aucune activité dans cette catégorie" in (await as_user.get("/activities?sport=bike")).text


async def test_a_watch_without_hrv_never_says_its_normal_is_building(db_session: AsyncSession, test_user: User,
                                                                     rested):
    """A normal « being built » only after a signal measured lately: a watch that never measures HRV just misses
    it, and neither its card nor its row exists (v4.4: no « pas encore de normale » for a signal never seen)."""
    rows = garmin_rows(D, range(0, 40))
    rows.pop("hrv")
    await _seed(db_session, test_user, rows)
    page = await sante.health_page(db_session, test_user.id, today=D)
    assert page["score"]["absent"] == ["hrv"] and page["score"]["building"] == [] and page["vfc"] is None
    assert [f["key"] for f in page["rows"]] == ["fc"] and page["dials"][0]["sub"] == "suffisant"
    assert page["score"]["value"] == sc.rounded((25 * 100 + 30 * 100) / 55)
