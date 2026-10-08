"""Activités, top of the page (v3, moved from Santé): A1 « Semaines », A2 « FC
en footing » — pure functions on synthetic sessions, then the page itself.
Training only (v4.1): no recovery line, no taper line, no « Fond et fatigue »
(recovery is Santé's: no redundancy), never a planned race."""
import json
import re
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_current_user
from app.models.activity import Activity
from app.models.route import Route
from app.models.user import User
from app.services import race_prep as rp
from app.services import sante_training as st
from app.services import training_view as tv
from app.services.sante_training import Session

T = date(2026, 10, 7)  # a Wednesday
NOW = datetime(2026, 10, 7, 12, tzinfo=timezone.utc)


def S(days_ago: int, minutes: float = 60, km: float = 10, dplus: float = 50, hr: float | None = 140,
      sport: str = "Run", i: int = 0, **kw) -> Session:
    day = T - timedelta(days=days_ago)
    start = datetime(day.year, day.month, day.day, 7, 0, tzinfo=timezone.utc) + timedelta(minutes=i)
    return Session(id=kw.pop("id", days_ago * 100 + i), start=start, day=day, sport=sport, minutes=minutes,
                   dplus=dplus, km=km, speed=km * 1000 / (minutes * 60) if km else None, hr=hr,
                   hr_peak=kw.pop("hr_peak", 185), suffer=None, workout_type=kw.pop("workout_type", 0),
                   temp=kw.pop("temp", None), name=kw.pop("name", "Footing"), **kw)


def race(days: int, name: str = "Transjeju 100M", rid: int = 7, km: float = 160, hour: int = 21,
         target: int | None = None, result=None):
    return SimpleNamespace(id=rid, name=name, race_date=(T + timedelta(days=days)).isoformat(), start_hour=hour,
                           start_minute=0, total_distance_km=km, total_elevation_gain=6000, target_time_s=target,
                           sport_type="trail", result_json=result)


def steady(days: int = 200) -> list[Session]:
    """Four outings a week, 1h50 each (7h20 a week), 10 km."""
    ss = [S(k, minutes=110, km=18, i=k) for k in range(1, days) if (T - timedelta(days=k)).weekday() in (1, 3, 5, 6)]
    st.set_loads(ss, 50, 185)
    return ss


def data(c: dict) -> dict:
    return json.loads(c["data"].replace("<\\/", "</"))


# ── A1 Semaines ─────────────────────────────────────────────────────────────

def test_a_run_more_than_10_percent_longer_than_the_months_longest_is_a_spike_races_never():
    base = [S(k, km=18, minutes=100, i=k) for k in range(5, 35, 3)]
    since = T - timedelta(days=9)
    assert tv.spikes(base + [S(1, km=19.5, minutes=110)], since) == []  # +8 %
    sp = tv.spikes(base + [S(1, km=22, minutes=125, id=1)], since)
    assert [s.id for s, _ in sp] == [1] and round((sp[0][1] - 1) * 100) == 22
    assert tv.spikes(base + [S(1, km=40, minutes=300, workout_type=1)], since) == []  # a race on Strava
    # an ultra neither marked on Strava nor planned in the app (a COROS import: no workout_type) is a race too: an
    # effort of 6 h or more is marked ◆, never flagged (OWN-2 / OWN-C: from the activity alone, no Route read)
    ultra = S(5, km=150, minutes=935, elapsed=1013, workout_type=None, id=9, name="Ultra du Mont")
    assert tv.spikes(base + [ultra], since) == [] and tv.is_race(ultra)
    assert tv.spikes(base + [S(1, km=40, minutes=300)], since)  # 5 h, not a race: flagged, whatever the plan
    assert tv.spikes(base + [S(1, km=40, sport="Ride", minutes=90)], since) == []  # runs only
    new = [S(k, km=5 + k % 3, minutes=40, i=k) for k in range(1, 20)]  # 3 weeks of history: nothing to compare with
    assert tv.spikes(new, T - timedelta(days=30)) == []


def test_weeks_rest_on_the_usual_week_and_print_a_week_only_on_a_tap():
    ss = steady()
    c = tv.semaines(ss, T, NOW)
    d = data(c)
    assert c["n"] == 12 and len(d["x"]) == 12
    # resting readout: the usual week (median of the active weeks), nothing selected
    assert d["rest"] == ["12 semaines", "semaine type : 7h20", ""] and d["sel"] == 12 and c["rest"]
    assert c["read"][1] == "semaine type : 7h20" and c["band"] is not None
    # a week on a tap: its hours and D+, the sessions, the link to it in the list (page 1: in place)
    last = d["r"][-2]
    assert last[0] == "sem. du 28 sept." and last[1] == "7h20 · +200 m" and last[2] == "4 activités"
    assert d["h"][-2] == {"href": "#week-2026-09-28", "label": "Voir la semaine ›"}
    assert d["h"][0]["href"] == "/activities?page=2#week-2026-07-20"
    assert d["r"][-1][0] == "sem. du 5 oct. · en cours" and c["cols"][-1]["cur"]
    assert "%" not in json.dumps(d, ensure_ascii=False)  # no weekly % anywhere
    # before 8 active weeks there is no usual week: the readout still rests, on words (no week printed twice)
    young = tv.semaines([s for s in ss if s.day > T - timedelta(days=30)], T, NOW)
    assert young["band"] is None and young["rest"] and young["read"] == ["12 semaines", "heures par semaine",
                                                                         "dénivelé en dessous"]


def test_weeks_mark_long_outings_and_the_spike_never_a_race_flag():
    """No race flag on the weekly bars (owner, 2026-10-08: everything is not planned in the app): a race is an
    outing like any other, its week keeps the ◆ of a long outing."""
    ss = steady() + [S(16, minutes=200, km=18, dplus=1800, id=2, name="Grand tour")]  # long, not longer
    ss += [S(2, minutes=150, km=26, id=3)]  # +44 % on the 30-day longest (18 km): a spike, and the line
    ss += [S(40, minutes=300, km=40, dplus=2400, id=4, workout_type=1, name="Trail des Crêtes")]  # a Strava race
    st.set_loads(ss, 50, 185)
    c = tv.semaines(ss, T, NOW)
    d = data(c)
    assert c["races"] == [] and len(c["longs"]) == 2 and len(c["spikes"]) == 1
    assert "▲ sortie 44\u202f% plus longue" in d["r"][-1][2]  # a spike by its shape too, never colour alone
    assert "◆ sortie de 3h20" in d["r"][-3][2] and "◆ sortie de 5h00" in d["r"][-7][2]
    assert "⚑" not in json.dumps(d, ensure_ascii=False) and "course" not in json.dumps(d, ensure_ascii=False)
    assert c["line"] == {"text": "Sortie de lundi 44\u202f% plus longue que ta plus longue du mois.",
                         "href": "/activity/3"}


def test_the_line_is_the_spike_only_never_recovery_nor_taper():
    """v4.1 (owner: « Concernant récupération, j'ai vu que c'est aussi dans Activités … ce ne doit pas être
    redondant »): no « Récupération après ta sortie … » (Santé's), no « Affûtage pour … » (a planned race: the race
    page's); the line is a spike of the last 10 days, or nothing. An unflagged ultra keeps its week's ◆."""
    ss = steady()
    assert tv.a1_line(ss, T) is None
    ultra = S(5, minutes=935, km=160, dplus=6000, id=77, workout_type=None, elapsed=1013, name="Transjeju 100M")
    assert tv.a1_line(ss + [ultra], T) is None  # neither a spike nor a recovery line
    c = tv.semaines(ss + [ultra], T, NOW)
    assert c["spikes"] == [] and len(c["longs"]) == 1 and c["line"] is None
    assert "◆ sortie de 15h35" in data(c)["r"][-2][2]  # its week (28 sept.): the moving time, as the list
    assert "Récupération" not in json.dumps(data(c), ensure_ascii=False)
    assert not hasattr(tv, "fond_fatigue") and "routes" not in tv.semaines.__code__.co_varnames


# ── A2 FC en footing ────────────────────────────────────────────────────────

def easy(days_ago: int, hr: float, temp: float | None = None, i: int = 0) -> Session:
    return S(days_ago, minutes=60, km=10, dplus=40, hr=hr, temp=temp, i=i, id=50_000 + days_ago * 10 + i)


def test_easy_pace_hr_dots_hollow_hot_runs_and_a_band_from_the_28_days_before():
    runs = [easy(k, 140 + (k % 3) - 1) for k in range(3, 200, 4)] + [easy(6, 150, temp=29, i=1)]
    c = tv.footing(runs, T, 185)
    d = data(c)
    assert c["pace"] == "6:00/km" and not c["flagged"] and c["line"] is None
    hot = [p for p in c["dots"] if p["hot"]]
    assert len(hot) == 1
    i = next(k for k, r in enumerate(d["r"]) if "journée chaude" in r[2])
    assert d["r"][i][1] == "150 bpm" and "normale 137–143" in d["r"][i][2]
    assert d["h"][i] == {"href": f"/activity/{50_000 + 6 * 10 + 1}", "label": "Ouvrir la sortie ›"}
    assert c["band"]  # the rolling band is drawn


def test_easy_pace_hr_is_flagged_after_two_runs_3_bpm_above():
    runs = [easy(k, 140) for k in range(10, 200, 3)]
    assert not tv.footing(runs + [easy(2, 144)], T, 185)["flagged"]  # one run
    c = tv.footing(runs + [easy(5, 144), easy(2, 146)], T, 185)
    assert c["flagged"] and c["line"] == "Tes 2 dernières sorties faciles : cœur au-dessus de ta normale, à même allure."
    assert not tv.footing(runs + [easy(5, 144), easy(2, 142)], T, 185)["flagged"]  # +2: within the band
    assert not tv.footing(runs + [easy(5, 150, temp=30), easy(2, 150, temp=30)], T, 185)["flagged"]  # hot
    assert tv.footing([easy(k, 140) for k in range(3, 17, 3)], T, 185) is None  # under 6 runs (H)
    # 6 runs in 6 weeks draw the line; 8 spread over a year with 1 in the last 6 weeks do not (row 16)
    assert tv.footing([easy(k, 140) for k in range(3, 20, 3)], T, 185) is not None
    assert tv.footing([easy(k, 140) for k in (3, 60, 100, 150, 200, 250, 300, 350)], T, 185) is None


# ── the page ────────────────────────────────────────────────────────────────

async def _add_runs(db: AsyncSession, user: User, today: date, n: int = 140):
    for k in range(1, n):
        d = today - timedelta(days=k)
        if d.weekday() not in (1, 3, 5, 6):
            continue
        start = datetime(d.year, d.month, d.day, 6, 0, tzinfo=timezone.utc)
        db.add(Activity(user_id=user.id, strava_activity_id=880_000 + k, sport_type="Run", name=f"Footing {k}",
                        start_date=start, distance=10_000, moving_time=3600, elapsed_time=3700,
                        total_elevation_gain=40, average_speed=10_000 / 3600, average_heartrate=140 + k % 3,
                        max_heartrate=185, raw_data={"utc_offset": 7200}))
    await db.flush()


async def test_activities_page_has_the_two_blocks_on_page_one_only(client: AsyncClient, db_session: AsyncSession,
                                                                   test_user: User):
    """Semaines and FC en footing, training only: no recovery line, no taper line for a planned race, no « Fond et
    fatigue » (v4.1: recovery lives on Santé; nothing twice, nothing complicated); every mark has a legend."""
    client._transport.app.dependency_overrides[get_current_user] = lambda: test_user  # type: ignore[attr-defined]
    today = datetime.now(timezone.utc).date()
    await _add_runs(db_session, test_user, today)
    db_session.add(Route(user_id=test_user.id, name="Trail des Crêtes", total_distance_km=42, total_elevation_gain=2000,
                         race_date=(today + timedelta(days=3)).isoformat(), start_hour=7, sport_type="trail"))
    d = today - timedelta(days=4)
    db_session.add(Activity(user_id=test_user.id, coros_activity_id=4242, sport_type="TrailRun", name="Ultra du Mont",
                            start_date=datetime(d.year, d.month, d.day, 4, tzinfo=timezone.utc), distance=150_000,
                            moving_time=56100, elapsed_time=60807, total_elevation_gain=6000,
                            raw_data={"utc_offset": 7200, "source": "coros"}))
    await db_session.flush()
    r = await client.get("/activities")
    assert r.status_code == 200
    html = r.text
    assert 'id="semaines"' in html and "semaine type" in html and "pf-viz.js" in html
    assert '<details id="fc-facile" class="pf-train-fold">' in html  # folded: not flagged
    for gone in ('id="fatigue"', "Fond et fatigue", "Affûtage", "Trail des Crêtes", "#prep", "Récupération",
                 "volume bas", "c'est voulu"):
        assert gone not in html, gone
    assert html.count("7 j :") == 1 and "28 j :" in html  # the header stays, once
    assert html.index('id="semaines"') < html.index('aria-label="Filtrer les activités par sport"')
    semaines = html.split('id="semaines"')[1].split("</section>")[0]
    # every mark named, by shape too (never colour alone): the weeks, this one in progress, the usual band, D+, ◆
    legend = semaines.split('<p class="pf-viz-legend" aria-hidden="true">')[1].split("</p>")[0]
    for item in ('<i class="pf-lg is-week"></i>heures', '<i class="pf-lg is-cur"></i>en cours',
                 '<i class="pf-lg is-usual"></i>semaine type', '<i class="pf-lg is-dplus"></i>D+',
                 '<i class="pf-lg is-long"></i>sortie longue'):
        assert item in legend, item
    assert "pf-viz-long is-warn" not in semaines  # the unflagged ultra: ◆, never ▲ (OWN-2)
    for url in ("/activities?sport=run", "/activities?page=2"):
        assert 'id="semaines"' not in (await client.get(url)).text


async def test_a_week_reads_the_same_in_a1_and_in_its_list_heading(client: AsyncClient, db_session: AsyncSession,
                                                                   test_user: User):
    """L-F11 / UX9: 2 runs of 8 990 s (a 50 s remainder: 4h59m40s) and 2 400.6 m
    D+: the list heading and A1's tap readout print « +2 401 m » and « 5h00 »."""
    client._transport.app.dependency_overrides[get_current_user] = lambda: test_user  # type: ignore[attr-defined]
    today = datetime.now(timezone.utc).date()
    await _add_runs(db_session, test_user, today - timedelta(days=21), n=120)  # a usual week for A1
    last_monday = today - timedelta(days=today.weekday() + 7)
    for i, day in enumerate((last_monday, last_monday + timedelta(days=2))):
        db_session.add(Activity(user_id=test_user.id, strava_activity_id=990_000 + i, sport_type="TrailRun",
                                name=f"Long {i}", start_date=datetime(day.year, day.month, day.day, 6,
                                                                      tzinfo=timezone.utc),
                                distance=30_000, moving_time=8990, elapsed_time=9000, total_elevation_gain=1200.3,
                                average_speed=30_000 / 8990, raw_data={"utc_offset": 7200}))
    await db_session.flush()
    html = (await client.get("/activities")).text
    heading = re.search(r'<section id="week-' + last_monday.isoformat() + r'">.*?<p class="pf-label[^>]*>(.*?)</p>',
                        html, re.S).group(1)
    heading = re.sub(r"[ \n]+", " ", re.sub(r"<[^>]+>", "", heading)).strip()
    assert heading == "60 km · +2 401 m · 5h00"
    d = json.loads(re.search(r'data-viz-key="semaines".*?<script type="application/json" class="pf-viz-data">(.*?)'
                             r'</script>', html, re.S).group(1))
    week = d["r"][d["d"].index(last_monday.isoformat())]
    assert week[1] == "5h00 · +2 401 m"


async def test_the_races_are_read_without_their_course(db_session: AsyncSession, test_user: User):
    """R1: Activités, Santé and the race page list the races without loading
    the course, plan or weather JSON (≈ 1 MB a 100-mile course)."""
    from sqlalchemy import inspect

    db_session.add(Route(user_id=test_user.id, name="UTMB", total_distance_km=171, race_date="2026-08-28",
                         course_json={"points": [[0, 0]] * 10}, sport_type="trail"))
    await db_session.flush()
    db_session.expunge_all()
    [r] = await rp.load_races(db_session, test_user.id, date(2026, 10, 7))
    unloaded = inspect(r).unloaded
    assert {"course_json", "weather_json", "live_json"} <= unloaded
    assert r.name == "UTMB" and rp.race_day(r) == date(2026, 8, 28) and "result_json" not in unloaded
