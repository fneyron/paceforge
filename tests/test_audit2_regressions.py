"""Regressions of the confirmed bugs of the second October 2026 audit: the double sign-up, the page number, the
rename, the bike family's speed, the verification link opened twice, the texts, the decimal comma, Strava's
details stored once."""
from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient

from app.dependencies import get_current_user
from app.models.activity import Activity
from app.models.user import User
from app.routers.dashboard import imported_label
from app.schemas.activity import ActivitySummary
from app.services.auth import hash_password, verification_token
from app.services.strava_activities import activity_from_strava, apply_detail
from app.services.viz import fr_num
from tests.test_simulator_routes import _create_route


@pytest.fixture(autouse=True)
def no_commit(db_session, monkeypatch):
    monkeypatch.setattr(db_session, "commit", db_session.flush)


@pytest.fixture
async def as_user(client, test_user):
    client._transport.app.dependency_overrides[get_current_user] = lambda: test_user
    return client


async def test_a_double_sign_up_lands_on_the_existing_account_page(client, db_session, monkeypatch):
    """Two submits of « Créer mon compte » at once: both pass the email check, the second insert hits the unique
    index — the normal « un compte existe déjà » page, not a 500."""
    monkeypatch.setattr("app.routers.auth.send_verification_email", lambda *args: True)
    form = {"email": "twice@x.fr", "password": "pw-12345678"}
    assert (await client.post("/auth/register", data=form)).status_code == 302
    real_execute = db_session.execute

    class Nobody:
        def scalar_one_or_none(self):
            return None

    async def blind(stmt, *a, **kw):  # the check-then-insert race: the check sees no account yet
        return Nobody() if "users.email" in str(stmt) else await real_execute(stmt, *a, **kw)
    monkeypatch.setattr(db_session, "execute", blind)
    r = await client.post("/auth/register", data=form)
    assert r.status_code == 200 and "Un compte existe déjà avec cet email." in r.text


async def test_the_activities_page_bounds_its_page_number(as_user):
    assert (await as_user.get("/activities?page=5000")).status_code == 200
    assert (await as_user.get("/activities?page=100000")).status_code == 422  # no OverflowError page
    assert (await as_user.get("/partials/activities?page=99999999999")).status_code == 422


async def test_a_rename_is_stripped_and_capped_like_a_save(as_user):
    rid = await _create_route(as_user)
    r = await as_user.patch(f"/api/simulator/routes/{rid}", data={"name": "  " + "N" * 300 + "  "})
    assert r.status_code == 200 and r.json()["name"] == "N" * 200


def test_every_bike_sport_shows_a_speed():
    def row(sport):
        return ActivitySummary(id=1, sport_type=sport, name="x", start_date=datetime(2026, 1, 1), distance=40000,
                               moving_time=5400, average_speed=40000 / 5400)
    for sport in ("Ride", "GravelRide", "MountainBikeRide", "EBikeRide", "VirtualRide"):
        assert row(sport).pace_formatted == "26.7 km/h", sport
    assert row("TrailRun").pace_formatted == "2:15/km"


async def test_a_verification_link_opened_twice_says_verified(client, db_session):
    token = verification_token()
    db_session.add(User(email="twice-link@x.fr", password_hash=hash_password("pw-12345678"), email_verify_token=token))
    await db_session.flush()
    async with AsyncClient(transport=client._transport, base_url="https://test") as c:
        for _ in range(2):  # a mail client fetching the link first, then the athlete's own click
            r = await c.get("/auth/verify-email", params={"token": token})
            assert r.status_code == 200 and "Email vérifié" in r.text and "Lien invalide" not in r.text


def test_the_texts_are_french_and_strava_details_are_stored_once():
    assert imported_label(1) == "1 nouvelle activité importée"
    assert imported_label(3) == "3 nouvelles activités importées"
    row = activity_from_strava({"id": 1, "start_date": "2026-10-01T06:00:00Z", "laps": [{"lap_index": 1}],
                                "splits_metric": [{"split": 1}], "best_efforts": [{"name": "5k"}]}, user_id=1)
    assert (row.name, row.sport_type) == ("Sans titre", "Workout")
    assert (row.laps, row.splits_metric, row.best_efforts) == ([{"lap_index": 1}], [{"split": 1}], [{"name": "5k"}])
    assert not {"laps", "splits_metric", "best_efforts"} & set(row.raw_data)
    row.raw_data = {**row.raw_data, "paceforge_dplus": {"gain": 10}}
    apply_detail(row, {"id": 1, "name": "Footing", "resource_state": 3, "laps": [{"lap_index": 2}], "calories": 500})
    assert row.laps == [{"lap_index": 2}] and row.calories == 500 and row.splits_metric == [{"split": 1}]
    assert row.raw_data == {"id": 1, "name": "Footing", "resource_state": 3, "calories": 500, "paceforge_dplus": {"gain": 10}}


def test_fr_num_keeps_the_digits_and_changes_the_separator():
    assert fr_num(12.35) == "12,35" and fr_num(12.0) == "12,0" and fr_num(12) == "12"
    assert fr_num(3, 1) == "3,0" and fr_num(1234.56, 1) == "1234,6"


async def test_the_activity_pages_print_the_decimal_comma(as_user, db_session, test_user):
    laps = [{"distance": 1234.5, "moving_time": 300, "elapsed_time": 300} for _ in range(3)]
    act = Activity(strava_activity_id=4343, user_id=test_user.id, sport_type="Run", name="Footing",
                   start_date=datetime.now(timezone.utc) - timedelta(days=1), distance=12350, moving_time=3600,
                   elapsed_time=3700, total_elevation_gain=50, average_speed=12350 / 3600, raw_data={}, laps=laps)
    db_session.add(act)
    await db_session.flush()
    assert ">12,35<small" in (await as_user.get("/activities")).text
    detail = (await as_user.get(f"/activity/{act.id}")).text
    assert "12,35 <span" in detail and ">1,2km<" in detail
