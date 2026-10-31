"""« Télécharger mes données » (GET /settings/export): the athlete's data as a ZIP of JSON files, theirs only,
without a secret, behind the sign-in and a quota; the link in Réglages."""
import io
import json
import os
import re
import zipfile
from datetime import date, datetime, timedelta, timezone

import pytest
from httpx import AsyncClient

from app.crypto import encrypt_secret
from app.models import (
    Activity,
    CorosConnection,
    GarminConnection,
    HealthMetric,
    HealthSample,
    LactateTest,
    MobileDaily,
    MobileDevice,
    NutritionProduct,
    Route,
    RouteCheckpoint,
    User,
)
from app.routers.mobile import digest
from app.services import data_export
from app.services.auth import hash_password, hash_token
from tests.test_security import as_user  # noqa: F401  (a fixture)

NOW = datetime.now(timezone.utc)
FILES = {"LISEZMOI.txt", "compte.json", "connexions.json", "activites.json", "sante_mesures.json",
         "sante_echantillons.json", "ressenti.json", "telephone_jours.json", "parcours.json", "lactate.json",
         "nutrition.json"}


async def _seed(db, user: User, tag: str, activities: int = 1) -> None:
    """One row of each kind for `user`, every value marked with `tag`."""
    uid = user.id
    for i in range(activities):
        db.add(Activity(user_id=uid, strava_activity_id=uid * 1000 + i, name=f"Sortie {tag} {i}", sport_type="Run",
                        start_date=NOW - timedelta(days=i), distance=10000, moving_time=3600, elapsed_time=3700,
                        total_elevation_gain=120, raw_data={"id": i, "tag": tag},
                        streams_data={"heartrate": [120, 130], "latlng": [[45.1, 6.1], [45.2, 6.2]]}))
    route = Route(user_id=uid, name=f"Course {tag}", total_distance_km=42, target_time_s=5 * 3600,
                  course_json={"name": tag, "segments": []})
    db.add(route)
    await db.flush()
    db.add_all([
        RouteCheckpoint(route_id=route.id, name=f"Ravito {tag}", distance_km=12.5, kind="aid", target_s=4500,
                        cutoff_clock="10:30"),
        HealthMetric(user_id=uid, date=NOW.date(), metric="sleep", value=470, source="COROS"),
        HealthMetric(user_id=uid, date=NOW.date(), metric="feel", value=1, source="PaceForge",
                     details={"answers": {"feel": "good"}, "tag": tag}),
        HealthSample(user_id=uid, metric="hrv", kind="", start_at=NOW.replace(tzinfo=None),
                     end_at=NOW.replace(tzinfo=None), value=61, source=f"COROS {tag}"),
        MobileDevice(user_id=uid, platform="ios", token_hash=digest(f"phone-token-{tag}"),
                     prev_token_hash=digest(f"old-token-{tag}"), expires_at=NOW + timedelta(days=90)),
        MobileDaily(user_id=uid, platform="ios", date=NOW.date(), metric="steps", value=12345, sources=[tag]),
        LactateTest(user_id=uid, tested_on=date(2026, 9, 1), mode="pace", device="Lactate Pro", protocol=tag,
                    stages=[{"pace": 300, "lactate": 1.8}]),
        NutritionProduct(user_id=uid, name=f"Gel {tag}", carbs_g=25),
        CorosConnection(user_id=uid, issuer="https://coros.example", client_id=f"coros-client-{tag}",
                        access_token_encrypted=encrypt_secret(f"coros-access-{tag}"),
                        refresh_token_encrypted=encrypt_secret(f"coros-refresh-{tag}"),
                        expires_at=NOW + timedelta(days=30), resource_url="https://coros.example/mcp",
                        token_endpoint="https://coros.example/token", last_sync_at=NOW),
        GarminConnection(user_id=uid, di_client_id="GARMIN", access_token_encrypted=encrypt_secret(f"garmin-{tag}")),
    ])
    await db.flush()


def _zip(response) -> dict[str, bytes]:
    archive = zipfile.ZipFile(io.BytesIO(response.content))
    assert archive.testzip() is None
    return {name: archive.read(name) for name in archive.namelist()}


async def test_the_export_holds_the_athletes_data_theirs_only_and_no_secret(as_user: AsyncClient,  # noqa: F811
                                                                            test_user, db_session, monkeypatch):
    monkeypatch.setattr(data_export, "ACTIVITY_BATCH", 2)  # several batches, whatever the size
    monkeypatch.setattr(data_export, "BATCH", 1)
    test_user.email, test_user.email_verified = "me@x.fr", True
    test_user.password_hash = hash_password("pw-12345678")
    test_user.password_reset_token = hash_token("reset-me")
    test_user.strava_client_id, test_user.strava_client_secret_encrypted = "4242", encrypt_secret("strava-secret")
    test_user.health_key_hash, test_user.health_key_prefix = digest("apple-key"), "pfk_abcd"
    other = User(email="other@x.fr", email_verified=True, password_hash=hash_password("their-password"),
                 strava_access_token="their-strava-token")
    db_session.add(other)
    await db_session.flush()
    await _seed(db_session, test_user, "MINE", activities=5)
    await _seed(db_session, other, "THEIRS")

    response = await as_user.get("/settings/export")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    assert re.fullmatch(r'attachment; filename="paceforge-mes-donnees-\d{4}-\d{2}-\d{2}\.zip"',
                        response.headers["content-disposition"])
    assert "no-store" in response.headers["cache-control"]
    files = _zip(response)
    assert set(files) == FILES
    data = {name: json.loads(body) for name, body in files.items() if name.endswith(".json")}

    assert data["compte.json"]["email"] == "me@x.fr" and data["compte.json"]["id"] == test_user.id
    assert [a["name"] for a in data["activites.json"]] == [f"Sortie MINE {i}" for i in range(5)]
    assert data["activites.json"][0]["streams_data"]["heartrate"] == [120, 130]
    assert [m["metric"] for m in data["sante_mesures.json"]] == ["sleep"]
    assert data["ressenti.json"][0]["details"]["answers"] == {"feel": "good"}
    assert data["sante_echantillons.json"][0]["value"] == 61
    assert data["telephone_jours.json"][0]["value"] == 12345
    (route,) = data["parcours.json"]
    assert route["name"] == "Course MINE" and route["target_time_s"] == 5 * 3600
    assert route["checkpoints"][0] | {"id": None, "route_id": None, "updated_at": None} == {
        "id": None, "route_id": None, "updated_at": None, "name": "Ravito MINE", "distance_km": 12.5,
        "elevation": None, "kind": "aid", "crew": False, "drop_bag": False, "cutoff_clock": "10:30", "stop_s": None,
        "target_s": 4500}
    assert data["lactate.json"][0]["stages"] == [{"pace": 300, "lactate": 1.8}]
    assert data["nutrition.json"][0]["name"] == "Gel MINE"
    links = data["connexions.json"]
    assert links["strava"]["lie"] is True and links["strava"]["application"] == "la tienne"
    assert links["coros"]["last_sync_at"] and links["garmin"] is not None
    assert [p["platform"] for p in links["telephones"]] == ["ios"]

    everything = b"".join(files.values()).decode()
    assert "THEIRS" not in everything and "other@x.fr" not in everything  # nobody else's
    for secret in (test_user.password_hash, hash_token("reset-me"), "test_access_token", "test_refresh_token",
                   "strava-secret", digest("apple-key"), "pfk_abcd", digest("phone-token-MINE"),
                   digest("old-token-MINE"), "coros-access-MINE", "coros-refresh-MINE", "coros-client-MINE",
                   "garmin-MINE"):
        assert secret not in everything, secret
    for field in ("password_hash", "password_reset_token", "email_verify_token", "strava_access_token",
                  "strava_refresh_token", "strava_client_secret_encrypted", "health_key_hash", "session_version"):
        assert field not in data["compte.json"]


async def test_the_export_needs_a_signed_in_account(client: AsyncClient):
    response = await client.get("/settings/export")
    assert response.status_code in (302, 303) and response.headers["location"] == "/auth/login"


async def test_the_export_has_a_quota_per_account(as_user: AsyncClient, monkeypatch):  # noqa: F811
    from app.services import auth as auth_service

    monkeypatch.setitem(auth_service.QUOTAS, "data_export", (3600, {"user": 1}))
    assert (await as_user.get("/settings/export")).status_code == 200
    response = await as_user.get("/settings/export")
    assert response.status_code == 429 and response.headers["retry-after"] == "3600"


async def test_the_export_holds_the_consent_and_explains_its_times(as_user: AsyncClient, test_user):  # noqa: F811
    files = _zip(await as_user.get("/settings/export"))
    account = json.loads(files["compte.json"])
    assert account["health_consent_version"] == test_user.health_consent_version
    assert account["health_consent_at"].startswith("2026-10-25")
    readme = files["LISEZMOI.txt"].decode()
    assert "heures sans fuseau sont en UTC" not in readme  # the samples and the nights are local times
    assert "sante_echantillons.json), à l’heure locale" in readme


@pytest.mark.skipif(bool(os.getenv("TEST_DATABASE_URL")),
                    reason="the legacy tables are made here as on an old database; alembic made the server's")
async def test_the_export_holds_the_legacy_tables_rows_of_the_account(as_user: AsyncClient, test_user,  # noqa: F811
                                                                      db_session):
    from sqlalchemy import text

    from tests.test_account_deletion import LEGACY, LEGACY_DDL, _legacy_rows

    other = User(email="other@x.fr", email_verified=True)
    db_session.add(other)
    await db_session.flush()
    await _seed(db_session, test_user, "MINE")
    await _seed(db_session, other, "THEIRS")
    for ddl in LEGACY_DDL:
        await db_session.execute(text(ddl))
    await _legacy_rows(db_session, other, 1)
    await _legacy_rows(db_session, test_user, 2)
    files = _zip(await as_user.get("/settings/export"))
    assert {name for name in files if name.startswith("anciennes_fonctions/")} == {
        f"anciennes_fonctions/{table}.json" for table in LEGACY}
    for table in LEGACY:
        assert [row["id"] for row in json.loads(files[f"anciennes_fonctions/{table}.json"])] == [2], table
    assert "anciennes_fonctions/" in files["LISEZMOI.txt"].decode()


async def test_without_the_legacy_tables_the_export_has_none_of_their_files(as_user: AsyncClient):  # noqa: F811
    # absent (a database made from the models), or present without a row of the account (alembic's, as in production)
    files = _zip(await as_user.get("/settings/export"))
    assert not [name for name in files if name.startswith("anciennes_fonctions/")]


async def test_reglages_links_the_export_beside_the_deletion(as_user: AsyncClient):  # noqa: F811
    page = (await as_user.get("/settings")).text
    link = page.index('href="/settings/export"')
    assert "Télécharger mes données" in page[link:link + 200]
    assert link < page.index("Supprimer mon compte")
