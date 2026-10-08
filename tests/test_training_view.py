"""Activités, top of the page (v3, moved from Santé): A1 « Semaines », A2 « FC
en footing » — pure functions on synthetic sessions, then the page itself.
Training only (v4.1): no recovery line, no taper line, no « Fond et fatigue »
(recovery is Santé's: no redundancy), never a planned race."""
import json
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_current_user
from app.models.activity import Activity
from app.models.route import Route
from app.models.user import User
from app.services import race_prep as rp
from app.services import training_view as tv
from app.services.sante_training import Session

ROOT = Path(__file__).resolve().parent.parent

T = date(2026, 10, 7)  # a Wednesday
NOW = datetime(2026, 10, 7, 12, tzinfo=timezone.utc)


def S(days_ago: int, minutes: float = 60, km: float = 10, dplus: float = 50, hr: float | None = 140,
      sport: str = "Run", i: int = 0, **kw) -> Session:
    day = T - timedelta(days=days_ago)
    start = datetime(day.year, day.month, day.day, 7, 0, tzinfo=timezone.utc) + timedelta(minutes=i)
    return Session(id=kw.pop("id", days_ago * 100 + i), start=start, day=day, sport=sport, minutes=minutes,
                   dplus=dplus, km=km, speed=km * 1000 / (minutes * 60) if km else None, hr=hr,
                   hr_peak=kw.pop("hr_peak", 185), workout_type=kw.pop("workout_type", 0),
                   temp=kw.pop("temp", None), name=kw.pop("name", "Footing"), **kw)


def race(days: int, name: str = "Transjeju 100M", rid: int = 7, km: float = 160, hour: int = 21,
         target: int | None = None, result=None):
    return SimpleNamespace(id=rid, name=name, race_date=(T + timedelta(days=days)).isoformat(), start_hour=hour,
                           start_minute=0, total_distance_km=km, total_elevation_gain=6000, target_time_s=target,
                           sport_type="trail", result_json=result)


def steady(days: int = 200) -> list[Session]:
    """Four outings a week, 1h50 each (7h20 a week), 10 km."""
    return [S(k, minutes=110, km=18, i=k) for k in range(1, days) if (T - timedelta(days=k)).weekday() in (1, 3, 5, 6)]


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


async def test_a_week_reads_the_same_in_a1_and_in_its_list_heading(client: AsyncClient, db_session: AsyncSession,
                                                                   test_user: User):
    """L-F11 / UX9: 2 runs of 8 990 s (a 50 s remainder: 4h59m40s) and 2 400.6 m D+: the list heading and A1's
    tap readouts print « 5h00 », « 60 km » and « +2 401 m » (the readout's value with plain no-break spaces: the
    display font has no narrow one)."""
    client._transport.app.dependency_overrides[get_current_user] = lambda: test_user  # type: ignore[attr-defined]
    today = datetime.now(timezone.utc).date()
    await _add_runs(db_session, test_user, today - timedelta(days=21), n=120)  # weeks before for the average
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
    assert heading == "60 km · +2\u202f401\u202fm · 5h00"

    def week(key):
        d = json.loads(re.search(r'data-viz-key="semaines-' + key + r'".*?<script type="application/json" '
                                 r'class="pf-viz-data">(.*?)</script>', html, re.S).group(1))
        return d["r"][d["d"].index(last_monday.isoformat())][1].replace(" ", " ")
    assert (week("duree"), week("distance"), week("dplus")) == ("5h00", "60 km", "+2 401 m")
    assert heading.replace(" ", " ") == f"{week('distance')} · {week('dplus')} · {week('duree')}"


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


def _weeks(minutes, km=None, dplus=None, count=None, last=date(2026, 10, 5)):
    """12 week dicts as week_totals gives them, oldest first, the last one current (Monday `last`)."""
    out = []
    for i, m in enumerate(minutes):
        monday = last - timedelta(weeks=len(minutes) - 1 - i)
        n = (count[i] if count else (1 if m else 0))
        out.append({"monday": monday, "current": i == len(minutes) - 1, "count": n, "minutes": m,
                    "km": (km[i] if km else m / 6), "dplus": (dplus[i] if dplus else 0.0)})
    return out


def test_weeks_one_measure_twelve_bars_and_the_average():
    """v4.3 (owner: « Base-toi sur l'état de l'art … il y a trop d'infos »; Strava's weekly progress, Garmin's
    weekly volume): one measure at a time, « Durée » by default; 12 bars, the current week « en cours »; it rests on
    the average of the complete weeks (« 12h20 par semaine en moyenne », « sur 11 semaines »); a week's own total
    only on a tap (« sem. du 28 sept. » « 18h56 »: its list heading prints it)."""
    minutes = [700, 760, 720, 800, 680, 740, 760, 720, 700, 780, 1136, 190]  # 11 complete weeks, mean 745,1
    c = tv.semaines(_weeks(minutes), date(2025, 1, 6), None)
    assert [k for k, _ in c["measures"]] == ["duree", "distance"] and c["m"] == "duree"  # no D+: no D+ toggle
    d = data(c["charts"]["duree"])
    mean = round(sum(minutes[:11]) / 11 / 5) * 5  # 745 → to 5 min
    assert d["rest"] == ["sur 11 semaines", tv.hm(mean), "par semaine en moyenne"] and d["sel"] == 12
    assert d["restA"] == (f"{tv.hm_long(mean)} par semaine en moyenne, sur 11 semaines complètes. Choisis une "
                          "semaine pour son total.")
    assert d["r"][-2] == ["sem. du 28 sept.", "18h56", ""] and d["r"][-1] == ["sem. du 5 oct.", "3h10", "en cours"]
    assert d["a"][-2] == "Semaine du lundi 28 septembre : 18 heures 56"
    assert d["a"][-1] == "Semaine du lundi 5 octobre, en cours : 3 heures 10"
    chart = c["charts"]["duree"]
    assert chart["cols"][-1]["cur"] and chart["cur"] and chart["avg"] is not None
    assert "%" not in json.dumps(d, ensure_ascii=False) and "◆" not in json.dumps(d, ensure_ascii=False)
    # D+ only when the 12 weeks hold some; an activity without D+ adds 0; distance only when some has one
    hilly = tv.semaines(_weeks(minutes, dplus=[0] * 11 + [400]), date(2025, 1, 6), "dplus")
    assert [k for k, _ in hilly["measures"]] == ["duree", "distance", "dplus"] and hilly["m"] == "dplus"
    assert data(hilly["charts"]["dplus"])["r"][-1] == ["sem. du 5 oct.", "+400 m", "en cours"]
    yoga = tv.semaines(_weeks(minutes, km=[0] * 12), date(2025, 1, 6), "distance")
    assert [k for k, _ in yoga["measures"]] == ["duree"] and yoga["m"] == "duree"  # an unoffered measure: Durée
    # a new athlete: the average reads the complete weeks since the first activity's week only
    new = tv.semaines(_weeks([0] * 9 + [300, 360, 60]), date(2026, 9, 21), None)
    assert data(new["charts"]["duree"])["rest"] == ["sur 2 semaines", "5h30", "par semaine en moyenne"]
    only_now = tv.semaines(_weeks([0] * 11 + [60]), date(2026, 10, 5), None)
    assert data(only_now["charts"]["duree"])["rest"] == ["12 semaines", "Durée par semaine", ""]
    assert only_now["charts"]["duree"]["avg"] is None  # no complete week: no average line
    assert tv.semaines(_weeks([0] * 12), None, None) is None  # nothing in 12 weeks: no chart
    # a week without any activity: « — » and « aucune activité »
    gap = data(tv.semaines(_weeks([600] * 5 + [0] + [600] * 6), date(2025, 1, 6), None)["charts"]["duree"])
    assert gap["r"][5] == ["sem. du 24 août", "—", "aucune activité"] and gap["h"][5] is None


def test_the_spike_is_a_tag_on_its_row_never_a_mark_on_the_chart():
    """v4.3: the single-run spike (> 10 % longer, on distance, than the longest of the 30 days before it; never a
    race nor an effort of 6 h or more: Frandsen 2025) leaves the chart: the run's row in the list says « plus
    longue que d'habitude »."""
    ss = steady() + [S(2, minutes=150, km=26, id=3)]  # +44 % on the 30-day longest (18 km)
    ss += [S(40, minutes=300, km=40, dplus=2400, id=4, workout_type=1, name="Trail des Crêtes")]  # a Strava race
    assert [s.id for s, _ in tv.spikes(ss, T - timedelta(days=60))] == [3]
    assert tv.SPIKE_TAG == "plus longue que d'habitude"
    assert not hasattr(tv, "a1_line")
    row = (ROOT / "app/templates/partials/activity_row.html").read_text()
    assert ("{% if spikes and activity.id in spikes %}<span class=\"pf-item-flag\">plus longue que d'habitude</span>"
            "{% endif %}") in row


def test_an_ultra_is_never_a_spike_nor_a_recovery_line():
    """v4.1 (owner: « ce ne doit pas être redondant »): no « Récupération après ta sortie … » (Santé's), no
    « Affûtage pour … » (the race page's); an unflagged ultra is an outing of its week like any other."""
    ss = steady()
    ultra = S(5, minutes=935, km=160, dplus=6000, id=77, workout_type=None, elapsed=1013, name="Transjeju 100M")
    assert tv.spikes(ss + [ultra], T - timedelta(days=9)) == [] and tv.is_race(ultra)
    assert not hasattr(tv, "fond_fatigue") and "routes" not in tv.semaines.__code__.co_varnames


async def _week_page(client, db_session, user, url="/activities"):
    client._transport.app.dependency_overrides[get_current_user] = lambda: user  # type: ignore[attr-defined]
    return (await client.get(url)).text


async def test_activities_page_semaines_then_fc_en_footing_on_page_one(client: AsyncClient, db_session: AsyncSession,
                                                                       test_user: User):
    """Semaines (the sport filter right under its title, its measure toggle, one measure's 12 bars and nothing else:
    no band, no ◆, no ▲, no D+ marks; the current week « en cours » in the legend; a dashed « moy. » line) and FC
    en footing, training only: no recovery line, no taper line for a planned race, no « Fond et fatigue »."""
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
    html = await _week_page(client, db_session, test_user)
    assert 'id="semaines"' in html and "pf-viz.js" in html
    assert '<details id="fc-facile" class="pf-train-fold">' in html  # folded: not flagged
    for gone in ('id="fatigue"', "Fond et fatigue", "Affûtage", "Trail des Crêtes", "#prep", "Récupération",
                 "volume bas", "c'est voulu", "semaine type"):
        assert gone not in html, gone
    assert html.count("7 j :") == 1 and "28 j :" in html  # the header stays, once
    semaines = html.split('id="semaines"')[1].split("</section>")[0]
    # the title, the filter right under it, the measure toggle (a GET form: it works without JS), then the chart
    top, below = semaines.split('<nav aria-label="Filtrer les activités par sport"', 1)
    assert '<h2 id="semaines-h" class="pf-h2">Semaines</h2>' in top and "pf-viz-ranges" not in top
    toggle = below.split('data-viz-key="semaines-duree"')[0]
    assert re.search(r'<form class="pf-seg pf-seg-sm pf-viz-ranges" data-viz-ranges data-viz-param="m" role="group" '
                     r'aria-label="Mesure" method="get" action="/activities#semaines">', toggle)
    assert re.findall(r'name="m" value="(\w+)" data-range="\w+" aria-pressed="(\w+)">([^<]+)<', toggle) == [
        ("duree", "true", "Durée"), ("distance", "false", "Distance"), ("dplus", "false", "D+")]
    assert html.count('aria-label="Filtrer les activités par sport"') == 1
    # one measure shown, the others in place for the toggle; nothing but bars, a base line and « moy. »
    assert '<div data-range-panel="duree">' in semaines and '<div data-range-panel="distance" hidden>' in semaines
    svg = semaines.split('data-viz-key="semaines-duree"')[1].split("</svg>")[0]
    for gone in ("pf-viz-band", "pf-viz-long", "pf-viz-sec", "pf-viz-edge", "is-warn", "◆", "▲"):
        assert gone not in svg, gone
    assert re.search(r'class="pf-viz-avg"/><text x="320" y="[\d.]+" text-anchor="end" class="pf-viz-strong">moy\.</text>',
                     svg)
    legend = semaines.split('<p class="pf-viz-legend" aria-hidden="true">')[1].split("</p>")[0]
    assert legend == '<span><i class="pf-lg is-cur"></i>semaine en cours</span>'
    # page 2: the filter alone; a filter on page 1: Semaines for that sport
    assert 'id="semaines"' not in (await client.get("/activities?page=2")).text
    run = (await client.get("/activities?sport=run")).text
    assert 'id="semaines"' in run and 'aria-current="true"\n       >Course</a>' in run


async def test_the_filter_applies_to_the_chart_and_the_list(client: AsyncClient, db_session: AsyncSession,
                                                            test_user: User):
    """« Course · Trail · Vélo · Autre » filter Semaines and the list alike, the week totals counted as the list's
    headings count them; the measure is kept across a filter (?m=), the filter across a measure (a hidden field);
    a cyclist's rides without D+: no D+ toggle; « FC en footing » only under the running filters."""
    client._transport.app.dependency_overrides[get_current_user] = lambda: test_user  # type: ignore[attr-defined]
    today = datetime.now(timezone.utc).date()
    await _add_runs(db_session, test_user, today, n=60)
    for k in range(2, 60, 7):  # a ride a week, flat (no D+ recorded)
        d = today - timedelta(days=k)
        db_session.add(Activity(user_id=test_user.id, strava_activity_id=770_000 + k, sport_type="Ride",
                                name=f"Vélo {k}", start_date=datetime(d.year, d.month, d.day, 8, tzinfo=timezone.utc),
                                distance=60_000, moving_time=7200, elapsed_time=7300, total_elevation_gain=0,
                                raw_data={"utc_offset": 7200}))
    await db_session.flush()
    bike = (await client.get("/activities?sport=bike&m=distance")).text
    semaines = bike.split('id="semaines"')[1].split("</section>")[0]
    assert re.findall(r'data-range="(\w+)"', semaines) == ["duree", "distance"]  # no D+ on these rides
    assert '<input type="hidden" name="sport" value="bike">' in semaines  # the toggle keeps the filter
    assert '<div data-range-panel="distance">' in semaines and '<div data-range-panel="duree" hidden>' in semaines
    d = json.loads(re.search(r'data-viz-key="semaines-distance".*?class="pf-viz-data">(.*?)</script>', semaines,
                             re.S).group(1))
    assert {r[1] for r in d["r"] if r[1] != "—"} == {"60 km"}  # the rides only, a ride a week
    assert "Footing" not in bike.split('id="activity-list"')[1] and "Vélo 2" in bike  # the list: rides only
    assert 'id="fc-facile"' not in bike  # FC en footing: runs only
    # each filter keeps the measure shown; « Tout » too
    assert re.findall(r'<a href="(/activities[^"]*)" data-viz-carry="m"', semaines) == [
        "/activities?m=distance", "/activities?sport=run&amp;m=distance", "/activities?sport=trail&amp;m=distance",
        "/activities?sport=bike&amp;m=distance", "/activities?sport=other&amp;m=distance"]
    run = (await client.get("/activities?sport=run")).text
    d = json.loads(re.search(r'data-viz-key="semaines-duree".*?class="pf-viz-data">(.*?)</script>', run,
                             re.S).group(1))
    assert "2h00" not in {r[1] for r in d["r"]} and 'id="fc-facile"' in run  # no ride in the runs' weeks
    assert "Vélo" not in run.split('id="activity-list"')[1]
    other = (await client.get("/activities?sport=other")).text  # nothing there: the filter alone, the empty state
    assert 'id="semaines"' not in other and 'aria-current="true"\n       >Autre</a>' in other
    assert "Aucune activité dans cette catégorie" in other


async def test_the_spike_tag_is_on_its_row(client: AsyncClient, db_session: AsyncSession, test_user: User):
    """The list: the run more than 10 % longer than the longest of its 30 days says « plus longue que
    d'habitude » under its date; the others nothing."""
    client._transport.app.dependency_overrides[get_current_user] = lambda: test_user  # type: ignore[attr-defined]
    today = datetime.now(timezone.utc).date()
    await _add_runs(db_session, test_user, today - timedelta(days=3), n=60)  # 10 km runs
    d = today - timedelta(days=1)
    db_session.add(Activity(user_id=test_user.id, strava_activity_id=880_999, sport_type="Run", name="Grande sortie",
                            start_date=datetime(d.year, d.month, d.day, 6, tzinfo=timezone.utc), distance=14_000,
                            moving_time=5000, elapsed_time=5100, total_elevation_gain=40, average_speed=2.8,
                            average_heartrate=141, max_heartrate=185, raw_data={"utc_offset": 7200}))
    await db_session.flush()
    html = (await client.get("/activities")).text
    rows = re.findall(r'<a href="/activity/\d+"\s+id="activity-row-\d+".*?</a>', html, re.S)
    tagged = [r for r in rows if "plus longue que d'habitude" in r]
    assert len(tagged) == 1 and "Grande sortie" in tagged[0]
    assert '<span class="pf-item-flag">plus longue que d\'habitude</span>' in tagged[0]
    assert "pf-viz-long is-warn" not in html and "▲" not in html.split('id="semaines"')[1].split("</section>")[0]
