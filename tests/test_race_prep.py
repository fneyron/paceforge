"""Race page › Préparation (#prep, v3, moved from Santé › Course): the taper
against the −41–60 % band, nights J-14 → J-1 against usual + 30–60 min, the
race week, the hot-race line; after the race « Cœur la nuit » J+1 → J+14 and
the driving line. The owner's Transjeju 100M (02/10 21:00, 16h53) as a fixture."""
import json
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

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
from tests.test_nights import night_rows, owner_rows
from tests.test_race_plan_services import _course

D = date(2026, 10, 7)  # Wednesday
NOW = datetime(2026, 10, 7, 12, tzinfo=timezone.utc)
TRANSJEJU = date(2026, 10, 2)


def route(rd: date, km: float = 160, hour: int | None = 21, target: int | None = None, result=None, weather=None,
          name: str = "Transjeju 100M", rid: int = 7):
    return SimpleNamespace(id=rid, name=name, race_date=rd.isoformat(), start_hour=hour, start_minute=0,
                           total_distance_km=km, total_elevation_gain=6000, target_time_s=target,
                           sport_type="trail", result_json=result, weather_json=weather)


OWNER_RACE = route(TRANSJEJU, result={"total_actual_s": 16 * 3600 + 53 * 60})


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


# ── after the race: the owner ───────────────────────────────────────────────

def test_owner_after_transjeju_heart_at_night_dots_only_never_judged():
    nights = nt.build_nights(owner_rows(), D)
    nt.tag_nights(nights, [], [(TRANSJEJU, "Transjeju 100M")], {})
    c = rp.recovery(nights, TRANSJEJU, D)
    d = data(c)
    assert d["d"][0] == "2026-10-03" and d["d"][-1] == "2026-10-16" and c["n"] == 14
    assert not c["banded"] and all(not p["band"] for p in c["panels"])  # 0–1 night before J-7: dots only
    assert d["r"][4] == ["nuit du mar. 6 au mer. 7", "VFC 95 ms · FC 37 bpm", ""]  # on a tap
    # resting readout: last night's values are Santé › Sommeil's, printed once there
    assert c["rest"] and c["sel"] == d["sel"] == 14 and c["read"] == d["rest"] == ["J+1 → J+14", "VFC et FC de nuit", ""]
    assert "95" not in " ".join(c["read"]) and "37" not in " ".join(c["read"])
    assert d["back"] == 4  # ‹ from the rest: the latest measured night, not J+14 « à venir »
    assert d["r"][0][1] == "—" and "pas de montre cette nuit" in d["r"][0][2]  # 3 Oct: the race's own night
    assert d["r"][5] == ["nuit du mer. 7 au jeu. 8", "à venir", "J+6"]
    text = json.dumps(d, ensure_ascii=False)
    assert "au-dessus" not in text and "en dessous" not in text and "incomplète" not in text
    assert not any(dot["out"] for p in c["panels"] for dot in p["dots"])


def test_recovery_against_the_pre_race_band_without_labels():
    rd = D - timedelta(days=4)
    rows = night_rows(range(4, 90), hr=45, hrv=60)  # every night, the race window included
    rows["hr_night"][D] = (55, {"method": "points"}, "Garmin")  # well above: drawn, not judged
    nights = nt.build_nights(rows, D)
    nt.tag_nights(nights, [], [(rd, "Course")], {})
    c = rp.recovery(nights, rd, D)
    assert c["banded"] and c["panels"][1]["band"]
    i = data(c)["d"].index(D.isoformat())
    assert "FC 55 bpm" in data(c)["r"][i][1] and "au-dessus" not in data(c)["r"][i][1]
    assert data(c)["r"][i][2] == "normale FC 42–48"  # no HRV that night: its normal is not printed


def test_driving_line_after_a_race_run_through_a_night_only():
    assert rp.night_out(OWNER_RACE) == date(2026, 10, 3)  # still running at 03:00 on 3 Oct, done at 13:53
    assert rp.drive_line(OWNER_RACE, [], date(2026, 10, 3)) and rp.drive_line(OWNER_RACE, [], date(2026, 10, 4))
    assert rp.drive_line(OWNER_RACE, [], D) is None  # J+5
    assert rp.night_out(route(D, hour=6, result={"total_actual_s": 10 * 3600})) is None
    assert rp.night_out(route(D, hour=21, result={"total_actual_s": 5 * 3600})) is None  # done at 02:00
    assert rp.night_out(route(D, hour=None, result={"total_actual_s": 30 * 3600})) is None


# ── before the race ─────────────────────────────────────────────────────────

def test_taper_bars_against_the_41_60_percent_band_of_the_base():
    rd = D + timedelta(days=5)  # Monday 12 Oct: S0 is its week, S-1 this one
    ss = steady(D)
    c = rp.taper(ss + [S(rd, minutes=900, km=160, workout_type=1)], route(rd), rd, D, NOW)
    d = data(c)
    assert [col["label"] for col in c["cols"]] == ["S‑6", "S‑5", "S‑4", "S‑3", "S‑2", "S‑1", "S0"]
    assert [bool(col.get("tgt")) for col in c["cols"]] == [False] * 5 + [True, True]
    assert d["r"][5][0] == "sem. du 5 oct. · en cours" and c["cols"][5]["cur"]
    assert d["r"][5][2] == "cible 2h55–4h20"  # base 7h20: −41 to −60 %
    assert d["r"][6][1] == "à venir" and d["r"][6][2] == "sans la course · cible 2h55–4h20"
    assert d["r"][1][2] == "dans ta base"
    # resting readout: this week's hours are Activités' (its week heading), so the base and the target instead
    assert c["rest"] and d["sel"] == 7 and c["read"] == ["S‑6 → S0", "base : 7h20 par semaine", "cible S‑1 et S0 : 2h55–4h20"]
    assert d["back"] == 5  # ‹ from the rest: this week, not S0 « à venir »
    assert "extrapolation" in c["summary"]  # 160 km: beyond what was studied
    assert c["sentence"] == "Moins de volume, même intensité\u00a0: c'est elle qui entretient ta forme."
    heavy = ss + [S(D - timedelta(days=1), minutes=300, i=1)]  # 1h50 + 5 h this week: above 4h20
    assert rp.taper(heavy, route(rd), rd, D, NOW)["sentence"].startswith("Déjà au-dessus de ta cible")
    early = rp.taper(ss, route(D + timedelta(days=20)), D + timedelta(days=20), D, NOW)
    assert early["sentence"] == "Affûtage dès le lun. 19 oct.\u00a0: 41 à 60\u202f% de volume en moins, même intensité."
    assert rp.taper([], route(rd), rd, D, NOW) is None
    # a race inside the base weeks is no training volume: the base leaves it out
    raced = ss + [S(D - timedelta(days=17), minutes=900, km=100, workout_type=1, i=2)]
    assert data(rp.taper(raced, route(rd), rd, D, NOW))["r"][5][2] == "cible 2h55–4h20"


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
                                                        test_user: User, monkeypatch):
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
    assert "Semaine de course" in html and "700 à 840 g" in html and "Ravitaillement ›" in html
    assert "Course chaude" in html
    assert "Prêt pour la distance" not in html and "Cœur la nuit" not in html

    done = await _race(db_session, test_user, today - timedelta(days=5), name="Trail passé")
    html = (await client.get(f"/simulator/routes/{done}")).text
    assert "Récupération · J+5" in html and "Cœur la nuit" in html and 'data-viz-key="recup"' in html
    assert "Tes nuits" not in html and "Semaine de course" not in html

    far = await _race(db_session, test_user, today + timedelta(days=60), name="Plus tard")
    assert 'id="prep"' not in (await client.get(f"/simulator/routes/{far}")).text
