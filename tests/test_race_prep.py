"""Race page › Préparation (#prep, v3, moved from Santé › Course): the taper
against the −41–60 % band, nights J-14 → J-1 against usual + 30–60 min, the
race week, the hot-race line; nothing after the race (v4.3: the recovery part,
« Cœur la nuit » J+1 → J+14 and the driving line, is gone: recovery is
Santé's)."""
import json
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_current_user
from app.models.activity import Activity
from app.models.health import HealthMetric
from app.models.route import Route
from app.models.user import User
from app.services import nights as nt
from app.services import race_prep as rp
from app.services import sante_training as st
from app.services.sante_training import Session
from tests.test_nights import night_rows
from tests.test_race_plan_services import _course

ROOT = Path(__file__).resolve().parent.parent
D = date(2026, 10, 7)  # Wednesday
NOW = datetime(2026, 10, 7, 12, tzinfo=timezone.utc)


def route(rd: date, km: float = 160, hour: int | None = 21, target: int | None = None, result=None, weather=None,
          name: str = "Transjeju 100M", rid: int = 7):
    return SimpleNamespace(id=rid, name=name, race_date=rd.isoformat(), start_hour=hour, start_minute=0,
                           total_distance_km=km, total_elevation_gain=6000, target_time_s=target,
                           sport_type="trail", result_json=result, weather_json=weather)


def S(day: date, minutes: float = 110, km: float = 18, i: int = 0, **kw) -> Session:
    start = datetime(day.year, day.month, day.day, 7, 0, tzinfo=timezone.utc) + timedelta(minutes=i)
    return Session(id=kw.pop("id", day.toordinal() * 10 + i), start=start, day=day, sport="Run", minutes=minutes,
                   dplus=kw.pop("dplus", 200), km=km, speed=km * 1000 / (minutes * 60), hr=140, hr_peak=180,
                   suffer=None, workout_type=kw.pop("workout_type", 0), temp=None, **kw)


def steady(until: date, days: int = 120) -> list[Session]:
    """Four outings of 1h50 a week (7h20) up to `until` (excluded)."""
    return [S(until - timedelta(days=k)) for k in range(1, days)
            if (until - timedelta(days=k)).weekday() in (1, 3, 5, 6)]


def data(c: dict) -> dict:
    return json.loads(c["data"].replace("<\\/", "</"))


# ── before the race ─────────────────────────────────────────────────────────

def test_taper_bars_against_the_41_60_percent_band_of_the_base():
    rd = D + timedelta(days=5)  # Monday 12 Oct: S0 is its week, S-1 this one
    ss = steady(D)
    c = rp.taper(ss + [S(rd, minutes=900, km=160, workout_type=1)], route(rd), rd, D, NOW)
    d = data(c)
    assert [col["label"] for col in c["cols"]] == ["S‑6", "S‑5", "S‑4", "S‑3", "S‑2", "S‑1", "S0"]
    # a Monday race: J-14 → J-1 is S-2 and S-1 whole; S0 holds no day before the race, so no target
    assert [bool(col.get("tgt")) for col in c["cols"]] == [False] * 4 + [True, True, False]
    assert d["r"][5][0] == "sem. du 5 oct. · en cours" and c["cols"][5]["cur"]
    assert d["r"][5][2] == "cible 2h55–4h20" and d["r"][4][2] == "cible 2h55–4h20"  # base 7h20: −41 to −60 %
    assert d["r"][6][1] == "à venir" and d["r"][6][2] == "sans la course"
    assert d["r"][1][2] == "dans ta base"
    # resting readout: this week's hours are Activités' (its week heading), so the base and the target instead
    assert c["rest"] and d["sel"] == 7 and c["read"] == ["S‑6 → S0", "base : 7h20 par semaine",
                                                        "cible J‑14 → J‑1 : 2h55–4h20 par semaine"]
    assert d["back"] == 5  # ‹ from the rest: this week, not S0 « à venir »
    assert "extrapolation" in c["summary"]  # 160 km: beyond what was studied
    assert c["sentence"] == "Moins de volume, même intensité\u00a0: c'est elle qui entretient ta forme."
    heavy = ss + [S(D - timedelta(days=1), minutes=300, i=1)]  # 1h50 + 5 h this week: above 4h20
    assert rp.taper(heavy, route(rd), rd, D, NOW)["sentence"].startswith("Déjà au-dessus de ta cible")
    early = rp.taper(ss, route(D + timedelta(days=20)), D + timedelta(days=20), D, NOW)
    assert early["sentence"] == "Affûtage dès le mar. 13 oct.\u00a0: 41 à 60\u202f% de volume en moins, même intensité."
    assert rp.taper([], route(rd), rd, D, NOW) is None
    # a race inside the base weeks is no training volume: the base leaves it out
    raced = ss + [S(D - timedelta(days=17), minutes=900, km=100, workout_type=1, i=2)]
    assert data(rp.taper(raced, route(rd), rd, D, NOW))["r"][5][2] == "cible 2h55–4h20"


def test_a_partial_taper_week_is_compared_with_its_own_days():
    """L-F7: a Wednesday race. J-14 → J-1 holds 5 days of S-2, S-1 and 2 days of
    S0: S0's target is 2/7 of the band, S-2's its 2 base days + 5/7 of the band;
    a textbook 50 % taper sits inside every band, base volume never does."""
    rd = date(2026, 10, 21)  # Wednesday
    today, now = rd - timedelta(days=1), datetime(2026, 10, 20, 12, tzinfo=timezone.utc)
    base = steady(rd - timedelta(days=14), days=60)  # 7h20 a week until J-14
    per_day = 440 / 7 / 2  # half the base, every day of J-14 → J-1
    taper_days = [S(rd - timedelta(days=k), minutes=per_day, i=5) for k in range(1, 15)]
    c = rp.taper(base + taper_days, route(rd), rd, today, now)
    d = data(c)
    assert [col.get("tgt") is not None for col in c["cols"]] == [False] * 4 + [True] * 3
    assert d["r"][6][2] == "sans la course · cible 50 min–1h15 · 2 jours d'affûtage"  # 2/7 × 2h55–4h20
    assert d["r"][4][2].startswith("cible ") and d["r"][4][2].endswith(" · 5 jours d'affûtage")
    for col in c["cols"][4:]:  # each bar inside its own band (y grows downwards)
        assert col["tgt"]["y"] <= col["y"] <= col["tgt"]["y"] + col["tgt"]["h"], col
    # base volume through the taper: above the band this week, and the sentence says so
    full = base + [S(rd - timedelta(days=k), minutes=440 / 7, i=5) for k in range(1, 15)]
    c = rp.taper(full, route(rd), rd, today, now)
    assert c["cols"][6]["y"] < c["cols"][6]["tgt"]["y"]
    assert c["sentence"].startswith("Déjà au-dessus de ta cible")
    assert rp.taper_days(date(2026, 10, 5), rd) == (2, 5) and rp.taper_days(date(2026, 10, 19), rd) == (0, 2)


def _pre_race_nights(rd: date, nap_day: date | None = None, days=range(0, 90)):
    rows = night_rows(days, asleep=440)
    if nap_day:
        rows["nap"][nap_day] = (40, {"windows": [[f"{nap_day}T13:30", f"{nap_day}T14:15"]]}, "Garmin")
    nights = nt.build_nights(rows, D)
    nt.tag_nights(nights, [], [(rd, "Course")], {})
    return nights


def test_nights_before_the_race_against_usual_plus_30_to_60_min_the_eve_never_flagged():
    rd = D + timedelta(days=5)
    c = rp.night_bars(_pre_race_nights(rd, nap_day=D - timedelta(days=1)), rd, D)
    d = data(c)
    assert c["n"] == 14 and d["d"][0] == (rd - timedelta(days=14)).isoformat() and c["goal"]
    i = d["d"].index((D - timedelta(days=1)).isoformat())
    assert d["r"][i][1] == "Nuit 7h20 · sieste 40 min · 8h00 sur 24 h" and c["cols"][i]["nap"]
    assert d["r"][i][2] == "J‑6 · cible 7h50–8h20"
    assert d["r"][-1] == ["nuit du sam. 10 au dim. 11", "à venir", "J‑1 · veille de course"]
    assert c["cols"][-1]["future"]
    # resting readout: the last night's total is Santé › Sommeil's, printed once there
    assert c["rest"] and d["sel"] == 14 and c["read"] == ["J‑14 → J‑1", "cible 7h50–8h20 par jour", ""]
    assert d["back"] == d["d"].index(D.isoformat())  # ‹ from the rest: last night, not J-1 « à venir »
    assert not any(col.get("short") for col in c["cols"])  # nothing outlined on this page


def test_nights_without_a_usual_have_no_band_and_without_a_watch_say_so():
    rd = D + timedelta(days=5)
    c = rp.night_bars(_pre_race_nights(rd, days=range(0, 8)), rd, D)
    assert c["goal"] is None and "cible" not in json.dumps(data(c), ensure_ascii=False)
    assert c["read"] == ["J‑14 → J‑1", "sommeil sur 24 h", ""]
    assert rp.night_bars({}, rd, D) == {"empty": True}


def test_race_week_food_and_the_hot_line():
    f = rp.food_plan(route(D, hour=6), 60780, 72)
    assert "720 à 860 g pour tes 72 kg" in f["load"] and "(vers 03:00)" in f["breakfast"]
    assert rp.food_plan(route(D), 3600, 72)["short"].startswith("Pas besoin de surcharge")
    assert rp.food_plan(route(D), None, 72) is None
    assert "Ajoute ton poids" in rp.food_plan(route(D), 60780, None)["weight_link"]["text"]
    assert rp.hot(route(D, weather={"heat_factor": 1.08, "temperature_c": 27}))
    assert not rp.hot(route(D, weather={"heat_factor": 1.02, "temperature_c": 19})) and not rp.hot(route(D))


# ── the page ────────────────────────────────────────────────────────────────

async def _race(db: AsyncSession, user: User, rd: date, **kw) -> int:
    r = Route(user_id=user.id, name=kw.pop("name", "Ultra des Cimes"), total_distance_km=80, total_elevation_gain=4000,
              course_json=_course().model_dump(), race_date=rd.isoformat(), start_hour=kw.pop("hour", 6),
              start_minute=0, sport_type="trail", **kw)
    db.add(r)
    await db.flush()
    return r.id


async def _seed(db: AsyncSession, user: User, today: date):
    for k in range(1, 70):
        d = today - timedelta(days=k)
        if d.weekday() in (1, 3, 5, 6):
            db.add(Activity(user_id=user.id, strava_activity_id=770_000 + k, sport_type="Run", name=f"Sortie {k}",
                            start_date=datetime(d.year, d.month, d.day, 6, tzinfo=timezone.utc), distance=18_000,
                            moving_time=6600, elapsed_time=6700, total_elevation_gain=200, average_speed=2.7,
                            average_heartrate=140, max_heartrate=180, raw_data={"utc_offset": 7200}))
    for k in range(0, 30):
        d = today - timedelta(days=k)
        s, e = datetime.combine(d - timedelta(days=1), datetime.min.time()).replace(hour=23), \
            datetime.combine(d, datetime.min.time()).replace(hour=6, minute=40)
        db.add(HealthMetric(user_id=user.id, date=d, metric="sleep", value=440, source="Garmin", n_samples=1,
                            details={"main_start": s.isoformat(timespec="minutes"),
                                     "main_end": e.isoformat(timespec="minutes")}))
        db.add(HealthMetric(user_id=user.id, date=d, metric="hr_night", value=45, source="Garmin", n_samples=1,
                            details={"method": "points"}))
    await db.flush()


async def test_race_page_prep_before_and_after_the_race(client: AsyncClient, db_session: AsyncSession,
                                                        test_user: User, monkeypatch, same_today):
    monkeypatch.setattr(db_session, "commit", db_session.flush)
    client._transport.app.dependency_overrides[get_current_user] = lambda: test_user  # type: ignore[attr-defined]
    today = await st.athlete_today(db_session, test_user.id)
    await _seed(db_session, test_user, today)
    test_user.weight_kg = 70
    soon = await _race(db_session, test_user, today + timedelta(days=5),
                       weather_json={"heat_factor": 1.09, "temperature_c": 28})
    html = (await client.get(f"/simulator/routes/{soon}")).text
    assert 'id="prep"' in html and "Affûtage · J‑5" in html and "pf-viz.js" in html
    assert 'data-viz-key="affutage"' in html and 'data-viz-key="nuits-course"' in html
    assert "Vise 30 à 60 min de plus par jour, siestes comprises." in html
    assert "Semaine de course" in html and "700 à 840 g" in html and "Nutrition ›" in html
    assert "Course chaude" in html
    assert "Prêt pour la distance" not in html and "Cœur la nuit" not in html
    day = await _race(db_session, test_user, today, name="Aujourd'hui")
    assert "Jour J" in (await client.get(f"/simulator/routes/{day}")).text
    # after the race: no section at all (v4.3: recovery is Santé's)
    done = await _race(db_session, test_user, today - timedelta(days=5), name="Trail passé")
    html = (await client.get(f"/simulator/routes/{done}")).text
    assert 'id="prep"' not in html and "Récupération ·" not in html and "Cœur la nuit" not in html
    assert 'data-viz-key="recup"' not in html and "Trail passé" in html
    yday = await _race(db_session, test_user, today - timedelta(days=1), name="Hier")
    assert 'id="prep"' not in (await client.get(f"/simulator/routes/{yday}")).text
    far = await _race(db_session, test_user, today + timedelta(days=60), name="Plus tard")
    assert 'id="prep"' not in (await client.get(f"/simulator/routes/{far}")).text


async def test_race_page_sleep_banking_the_last_week_and_the_eve_reassurance(client: AsyncClient,
                                                                            db_session: AsyncSession,
                                                                            test_user: User, monkeypatch,
                                                                            same_today):
    """v4.2 (Walsh 2021): « Vise 30 à 60 min de plus » only J-7 → J-1 (« even just 1 week »); the race-eve
    reassurance once on the nights block, J-7 → J0; neither at J-10."""
    monkeypatch.setattr(db_session, "commit", db_session.flush)
    client._transport.app.dependency_overrides[get_current_user] = lambda: test_user  # type: ignore[attr-defined]
    today = await st.athlete_today(db_session, test_user.id)
    await _seed(db_session, test_user, today)
    bank, eve = "Vise 30 à 60 min de plus par jour, siestes comprises.", rp.EVE_LINE.replace("'", "&#39;")
    for k, lines in ((10, (False, False)), (5, (True, True)), (0, (False, True))):
        rid = await _race(db_session, test_user, today + timedelta(days=k), name=f"Course J-{k}")
        html = (await client.get(f"/simulator/routes/{rid}")).text
        assert 'data-viz-key="nuits-course"' in html, k
        assert (bank in html, eve in html) == lines, k
        assert html.count(eve) <= 1
        # the target J-7 → J-2 (when a usual exists): the band and both its edges start at J-7, never at the left
        chart = html.split('data-viz-key="nuits-course"')[1]
        band = re.search(r'<rect x="([\d.]+)" y="[\d.]+" width="([\d.]+)" height="[\d.]+" class="pf-viz-band"/>\s*'
                         r'<line x1="([\d.]+)" x2="([\d.]+)"[^>]*class="pf-viz-edge"/><line x1="([\d.]+)" '
                         r'x2="([\d.]+)"', chart)
        assert bool(band) is ("cible" in chart.split("</figcaption>")[0]), k
        if band:
            x, w, *edges = (float(v) for v in band.groups())
            assert x > 0 and edges == pytest.approx([x, x + w, x, x + w]), k


def test_no_recovery_part_after_the_race():
    """v4.3 (owner: « Enlève la partie récupération sous le plan d'une course »): the race page's section shows
    J-42 → J0 only; « Cœur la nuit » J+1 → J+14 and the driving line went with the recovery part (Santé's)."""
    for gone in ("recovery", "drive_line", "night_out", "race_duration", "PREP_AFTER", "NIGHT_OUT_AT", "_patch"):
        assert not hasattr(rp, gone), gone
    html = (ROOT / "app/templates/partials/race_prep.html").read_text()
    for gone in ("p.heart", "p.drive", "Cœur la nuit", "J+14", "viz_band"):
        assert gone not in html, gone
