"""Explicit consent to health data (GDPR art. 9.2.a): dated and versioned when given, asked before a new health
source starts (COROS, a phone, a lactate test), asked on Santé from the accounts that hold health data without it,
and withdrawn in Réglages, which disconnects the sources and erases exactly the health data."""
import re
from datetime import datetime, timezone
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlsplit

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select

from app.dependencies import get_current_user
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
from app.services import coros, health_consent
from app.services.health_consent import HEALTH_CONSENT_VERSION, safe_next
from tests.test_account_deletion import _athlete, fake_coros, strava  # noqa: F401 (fixtures)
from tests.test_lactate import fields
from tests.test_mobile import login

HEALTH = (HealthMetric, HealthSample, MobileDaily, MobileDevice, LactateTest, CorosConnection, GarminConnection)
KEPT = (Activity, Route, RouteCheckpoint, NutritionProduct)


@pytest.fixture
def no_commit(db_session, monkeypatch):
    monkeypatch.setattr(db_session, "commit", db_session.flush)


@pytest.fixture
async def newcomer(db_session) -> User:
    """An athlete who has not consented (an account from before the question, or a new one)."""
    user = User(email="new@x.fr", email_verified=True, firstname="Camille")
    db_session.add(user)
    await db_session.flush()
    return user


def _as(client: AsyncClient, user: User) -> AsyncClient:
    client._transport.app.dependency_overrides[get_current_user] = lambda: user  # type: ignore[attr-defined]
    return AsyncClient(transport=client._transport, base_url="https://test")


@pytest.mark.parametrize("raw, expected", [
    ("/coros/connect?region=europe", "/coros/connect?region=europe"),
    ("/coros/connect?next=app", "/coros/connect?next=app"),
    ("/coros/connect?next=devices&region=chine", "/coros/connect?next=devices&region=chine"),
    ("/coros/connect?region=evil&next=https://evil.example", "/coros/connect"),
    ("/settings#donnees", "/settings#donnees"),
    ("/sante/lactate", "/sante/lactate"),
    ("https://evil.example/sante", "/sante"),
    ("//evil.example/sante", "/sante"),
    ("/\\evil.example", "/sante"),
    ("javascript:alert(1)", "/sante"),
    ("/auth/logout", "/sante"),
    ("/settings#<script>", "/settings"),
    ("", "/sante"),
    (None, "/sante"),
])
def test_next_is_one_of_the_allowed_targets(raw, expected):
    assert safe_next(raw) == expected


async def test_the_consent_is_recorded_with_its_version(client, db_session, newcomer, no_commit):
    async with _as(client, newcomer) as c:
        page = await c.get("/sante/consentement", params={"next": "/coros/connect?region=europe"})
        assert page.status_code == 200
        box = re.search(r'<input type="checkbox" name="accept"[^>]*>', page.text)[0]
        assert "checked" not in box and "required" in box  # never ticked for the athlete
        assert health_consent.TEXT[0] in page.text and 'value="/coros/connect?region=europe"' in page.text
        refused = await c.post("/sante/consentement", data={"next": "/sante"})
        assert refused.status_code == 400 and 'role="alert"' in refused.text
        assert newcomer.health_consent_at is None and not health_consent.given(newcomer)
        before = datetime.now(timezone.utc)
        r = await c.post("/sante/consentement", data={"next": "/coros/connect?region=europe", "accept": "1"})
        assert r.status_code == 303 and r.headers["location"] == "/coros/connect?region=europe"
    assert newcomer.health_consent_version == HEALTH_CONSENT_VERSION
    assert newcomer.health_consent_at >= before and health_consent.given(newcomer)
    # asked again only for another text
    newcomer.health_consent_version = "2020-01-01"
    assert not health_consent.given(newcomer)


async def test_the_consent_page_never_sends_elsewhere(client, newcomer, no_commit):
    async with _as(client, newcomer) as c:
        r = await c.post("/sante/consentement", data={"next": "https://evil.example/", "accept": "1"})
        assert r.headers["location"] == "/sante"
        # once given, the page itself goes on
        r = await c.get("/sante/consentement", params={"next": "//evil.example"})
        assert r.status_code == 303 and r.headers["location"] == "/sante"


async def test_coros_connect_asks_first_then_goes_on(client, db_session, newcomer, no_commit, monkeypatch):
    monkeypatch.setattr(coros, "discover", AsyncMock(side_effect=coros.CorosError("offline")))
    async with _as(client, newcomer) as c:
        r = await c.get("/coros/connect?region=europe&next=devices")
        assert r.status_code == 303
        target = urlsplit(r.headers["location"])
        assert target.path == "/sante/consentement"
        assert parse_qs(target.query)["next"] == ["/coros/connect?region=europe&next=devices"]
        coros.discover.assert_not_called()
        health_consent.record(newcomer)
        r = await c.get("/coros/connect?region=europe&next=devices")
        assert r.headers["location"] == "/connections/coros"  # the gate passed: COROS itself was asked
        coros.discover.assert_awaited_once()


async def test_the_app_sheet_asks_on_its_confirmation_page(client, newcomer):
    async with _as(client, newcomer) as c:
        page = (await c.get("/mobile/oauth/coros/confirm")).text
        assert 'action="/sante/consentement"' in page and 'value="/coros/connect?next=app"' in page
        assert 'name="accept"' in page and "Continuer avec COROS" in page
        strava_page = (await c.get("/mobile/oauth/strava/confirm")).text
        assert "/sante/consentement" not in strava_page  # Strava brings activities only


async def test_pairing_a_phone_needs_the_box(client, db_session, newcomer):
    login(client, newcomer)
    challenge = "a" * 64
    page = await client.get(f"/mobile/connect?platform=ios&challenge={challenge}")
    assert 'name="health_consent"' in page.text and health_consent.TEXT[0] in page.text
    csrf = re.search(r'name="csrf_token" value="([^"]+)"', page.text)[1]
    form = {"challenge": challenge, "platform": "ios", "csrf_token": csrf}
    refused = await client.post("/mobile/connect", data=form)
    assert refused.status_code == 400 and health_consent.MISSING in refused.text
    assert not (await db_session.scalars(select(MobileDevice))).all()
    paired = await client.post("/mobile/connect", data={**form, "health_consent": "1"})
    assert paired.status_code == 200 and "paceforge-bridge-message" in paired.text
    await db_session.refresh(newcomer)
    assert newcomer.health_consent_version == HEALTH_CONSENT_VERSION
    page = await client.get(f"/mobile/connect?platform=ios&challenge={challenge}")
    assert 'name="health_consent"' not in page.text  # asked once


async def test_a_lactate_test_needs_the_box(client, db_session, newcomer):
    login(client, newcomer)
    page = await client.get("/sante/lactate")
    assert 'name="health_consent"' in page.text
    csrf = re.search(r'name="csrf_token" value="([^"]+)"', page.text)[1]
    f = {**fields(), "csrf_token": csrf}
    assert "aria-invalid" not in re.search(r'<input type="checkbox" name="health_consent"[^>]*>', page.text)[0]
    refused = await client.post("/sante/lactate", data=f)
    assert refused.status_code == 400 and health_consent.MISSING in refused.text
    assert 'value="Lactate Pro 2"' in refused.text  # what was typed stays
    # the error beside the box, tied to it (as on the consent page)
    assert f'<p id="lactate-consent-error" class="pf-lactate-error" role="alert">{health_consent.MISSING}</p>' in refused.text
    box = re.search(r'<input type="checkbox" name="health_consent"[^>]*>', refused.text)[0]
    assert 'aria-invalid="true"' in box and 'aria-describedby="lactate-consent-error"' in box
    assert refused.text.count(health_consent.MISSING) == 1
    assert await db_session.scalar(select(func.count(LactateTest.id))) == 0
    saved = await client.post("/sante/lactate", data={**f, "health_consent": "1"})
    assert saved.status_code == 303 and await db_session.scalar(select(func.count(LactateTest.id))) == 1
    await db_session.refresh(newcomer)
    assert health_consent.given(newcomer)


async def test_the_sante_card_asks_the_accounts_holding_health_data(client, db_session, newcomer, no_commit):
    async with _as(client, newcomer) as c:
        assert "consent-card-title" not in (await c.get("/sante")).text  # nothing held: nothing asked
        db_session.add(HealthMetric(user_id=newcomer.id, date=datetime.now(timezone.utc).date(), metric="sleep",
                                    value=420, source="COROS"))
        await db_session.flush()
        page = (await c.get("/sante")).text
        assert "consent-card-title" in page and "J’accepte" in page
        card = page[page.index('class="pf-consent-card"'):page.index("</section>", page.index("pf-consent-card"))]
        assert 'action="/settings/health-consent/withdraw"' in card and "Refuser et effacer" in card
        assert ERASE_CONFIRM in card and 'href="/sante/consentement?next=%2Fsante">Ce qui est enregistré' in card
        assert 'class="pf-panel' not in card  # white, as the rings' card
        assert page.index("consent-card-title") < page.find('class="pf-dials') or 'class="pf-dials' not in page
        r = await c.post("/sante/consentement", data={"next": "/sante", "accept": "1"})
        assert r.headers["location"] == "/sante"
        assert "consent-card-title" not in (await c.get("/sante")).text


async def test_a_linked_source_alone_asks_too(client, db_session, newcomer, no_commit):
    db_session.add(GarminConnection(user_id=newcomer.id, di_client_id="GARMIN", access_token_encrypted=b"x"))
    await db_session.flush()
    assert await health_consent.holds_health_data(db_session, newcomer.id)


ERASE_CONFIRM = ("var msg = 'Effacer définitivement tes données de santé ? Tes sources seront déconnectées et "
                 "tes téléphones dissociés.'; if (window.pfConfirm) { pfConfirm(msg, function () { f.submit(); }, "
                 "'Effacer'); }")


async def test_settings_show_the_consent_and_its_withdrawal(client, db_session, test_user, newcomer, no_commit):
    async with _as(client, test_user) as c:
        page = (await c.get("/settings")).text
        assert "consentement donné le 25 octobre 2026" in page
        fold = page[page.index('<details class="pf-consent-withdraw"'):page.index("</details>",
                                                                                   page.index("pf-consent-withdraw"))]
        assert 'action="/settings/health-consent/withdraw"' in fold and "Retirer mon consentement" in fold
        assert ERASE_CONFIRM in fold  # confirmed before anything is erased
        assert "Tes sources seront déconnectées, tes téléphones dissociés et tes données de santé effacées." in fold
        assert "Restent Strava, tes activités avec leur fréquence cardiaque, tes plans" in fold
        assert fold.index('href="/settings/export"') < fold.index('type="submit"')  # the export first
    async with _as(client, newcomer) as c:
        page = (await c.get("/settings")).text
        assert "pas de consentement" in page and "/settings/health-consent/withdraw" not in page  # nothing held
        db_session.add(MobileDaily(user_id=newcomer.id, platform="ios", date=datetime.now(timezone.utc).date(),
                                   metric="steps", value=8000, sources=["iPhone"]))
        await db_session.flush()
        page = (await c.get("/settings")).text
        fold = page[page.index('<details class="pf-consent-withdraw"'):]
        assert "<summary class=\"pf-link\">Refuser et effacer</summary>" in fold and ERASE_CONFIRM in fold


async def test_settings_link_the_legal_pages_and_say_where_the_export_is_in_the_app(client, test_user, no_commit):
    async with _as(client, test_user) as c:
        page = (await c.get("/settings")).text
    legal = page[page.index('class="pf-set-legal"'):]
    for href in ("/privacy", "/cgu", "/mentions-legales"):
        assert f'href="{href}"' in legal[:400], href
    assert '<p class="pf-native-only text-sm text-gray-500">Export&nbsp;: depuis paceforge.fr dans un navigateur.</p>' in page


async def test_refusing_erases_the_health_data_of_an_account_that_never_consented(client, db_session, newcomer,
                                                                                  no_commit):
    db_session.add_all([HealthMetric(user_id=newcomer.id, date=datetime.now(timezone.utc).date(), metric="sleep",
                                     value=420, source="COROS"),
                        GarminConnection(user_id=newcomer.id, di_client_id="GARMIN", access_token_encrypted=b"x")])
    await db_session.flush()
    async with _as(client, newcomer) as c:
        r = await c.post("/settings/health-consent/withdraw")
        assert r.status_code == 303 and r.headers["location"] == "/settings#donnees"
        page = (await c.get("/settings")).text
    assert 'role="status">Données de santé effacées. Tes sources sont déconnectées.</p>' in page
    assert not await health_consent.holds_health_data(db_session, newcomer.id)
    assert "consent-card-title" not in page


async def _count(db, model, user_id) -> int:
    return await db.scalar(select(func.count()).select_from(model).where(model.user_id == user_id)) or 0


async def test_withdrawal_erases_exactly_the_health_data(committing_session, committing_client, fake_coros,
                                                         strava):
    db = committing_session
    other = await _athlete(db, "other@x.fr")
    user = await _athlete(db, "leaving@x.fr")
    health_consent.record(user)
    health_consent.record(other)
    await db.commit()
    assert all([await _count(db, model, user.id) for model in HEALTH + (Activity, Route, NutritionProduct)])
    committing_client._transport.app.dependency_overrides[get_current_user] = lambda: user  # type: ignore[attr-defined]
    r = await committing_client.post("/settings/health-consent/withdraw")
    assert r.status_code == 303 and r.headers["location"] == "/settings#donnees"
    assert [h["token_type_hint"] for h in fake_coros.revoked] == ["refresh_token", "access_token"]  # COROS let go
    strava["deauthorize"].assert_not_called()  # Strava (activities) stays linked
    await db.rollback()  # only what was committed counts
    for model in HEALTH:
        assert await _count(db, model, user.id) == 0, model.__name__
        assert await _count(db, model, other.id) > 0, model.__name__  # the other athlete keeps everything
    for model in (Activity, Route, NutritionProduct):
        assert await _count(db, model, user.id) == 1, model.__name__
    assert await db.scalar(select(func.count(RouteCheckpoint.id))) == 2
    await db.refresh(user)
    assert user.health_consent_at is None and user.health_consent_version is None
    assert user.strava_access_token and not health_consent.given(user)


def test_migration_starts_everyone_without_consent_and_round_trips():
    import importlib.util
    from pathlib import Path

    from alembic.config import Config
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from alembic.script import ScriptDirectory
    from sqlalchemy import create_engine, inspect, text

    root = Path(__file__).parents[1]
    spec = importlib.util.spec_from_file_location("health_consent_migration",
                                                  root / "alembic/versions/j1b2c3d4e5f6_health_consent.py")
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    assert migration.down_revision == "j0a1b2c3d4e5"
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "alembic"))
    script = ScriptDirectory.from_config(cfg)
    assert migration.revision in {r.revision for r in script.walk_revisions()} and len(script.get_heads()) == 1
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE users (id INTEGER PRIMARY KEY, firstname VARCHAR(100))"))
        conn.execute(text("INSERT INTO users VALUES (1, 'Camille')"))
        with Operations.context(MigrationContext.configure(conn)):
            migration.upgrade()
        assert tuple(conn.execute(text("SELECT health_consent_at, health_consent_version FROM users")).one()) == (None, None)
        with Operations.context(MigrationContext.configure(conn)):
            migration.downgrade()
        columns = {c["name"] for c in inspect(conn).get_columns("users")}
        assert not columns & {"health_consent_at", "health_consent_version"}
        assert conn.execute(text("SELECT firstname FROM users WHERE id = 1")).scalar() == "Camille"
    engine.dispose()


FEEL_CONSENT = "/sante/consentement?next=%2Fsante%23ressenti"


async def _feels(db, user_id) -> list:
    from app.services import sante_checkin

    rows = (await db.execute(select(HealthMetric.details).where(HealthMetric.user_id == user_id,
                                                                HealthMetric.metric == "feel"))).scalars().all()
    return [(details or {}).get(sante_checkin.KEY, {}).get("feel") for details in rows]


async def test_the_feel_waits_for_the_consent_then_is_saved(client, db_session, newcomer, no_commit):
    """The one-tap feel is health data too: without the consent, a tap leads to the consent page, nothing stored;
    the tap waits in the session and is saved right after the consent, back on the feel (not before it)."""
    from app.services.sante import athlete_today

    async with _as(client, newcomer) as c:
        data = {"day": (await athlete_today(db_session, newcomer.id)).isoformat(), "feel": "bad"}
        r = await c.post("/sante/ressenti", data=data, headers={"HX-Request": "true"})
        assert r.status_code == 204 and r.headers["HX-Redirect"] == FEEL_CONSENT
        assert await _feels(db_session, newcomer.id) == []
        page = (await c.get(FEEL_CONSENT)).text
        assert 'value="/sante#ressenti"' in page
        assert re.search(r'<a class="pf-link" href="/sante#ressenti">Pas maintenant</a>', page)  # back to Santé
        r = await c.post("/sante/consentement", data={"next": "/sante#ressenti", "accept": "1"})
        assert r.status_code == 303 and r.headers["location"] == "/sante#ressenti"
        assert await _feels(db_session, newcomer.id) == ["bad"]


async def test_a_waiting_feel_of_another_day_or_another_target_is_dropped(client, db_session, newcomer, no_commit):
    async with _as(client, newcomer) as c:
        r = await c.post("/sante/ressenti", data={"day": "2020-01-01", "feel": "good"})  # the page of another day
        assert r.status_code == 303 and r.headers["location"] == FEEL_CONSENT
        await c.post("/sante/consentement", data={"next": "/sante#ressenti", "accept": "1"})
        assert await _feels(db_session, newcomer.id) == []
        newcomer.health_consent_at = None
        await c.post("/sante/ressenti", data={"day": datetime.now(timezone.utc).date().isoformat(), "feel": "ok"})
        await c.post("/sante/consentement", data={"next": "/settings#donnees", "accept": "1"})  # asked elsewhere
        assert await _feels(db_session, newcomer.id) == []


async def test_not_now_goes_back_where_the_consent_was_asked(client, newcomer):
    async with _as(client, newcomer) as c:
        for asked, back in (("/sante/lactate", "/sante/lactate"), ("/settings#donnees", "/settings#donnees"),
                            ("/connections/garmin", "/settings#connections"),
                            ("/coros/connect?region=europe", "/settings#connections"),
                            ("/settings#garmin", "/settings#connections")):
            page = (await c.get("/sante/consentement", params={"next": asked})).text
            assert f'<a class="pf-link" href="{back}">Pas maintenant</a>' in page, asked


async def test_the_deprecated_feel_route_asks_for_the_consent_too(client, db_session, newcomer, no_commit):
    async with _as(client, newcomer) as c:
        r = await c.post("/sante/feel", data={"feel": "3", "why": ["sick"]})
        assert r.status_code == 303 and r.headers["location"] == FEEL_CONSENT
        r = await c.post("/sante/feel", data={"feel": "1"}, headers={"HX-Request": "true"})
        assert r.status_code == 204 and r.headers["HX-Redirect"] == FEEL_CONSENT
        assert not await db_session.scalar(select(func.count()).select_from(HealthMetric).where(
            HealthMetric.user_id == newcomer.id))
        health_consent.record(newcomer)
        assert (await c.post("/sante/feel", data={"feel": "1"})).headers["location"] == "/sante"
        assert await db_session.scalar(select(func.count()).select_from(HealthMetric).where(
            HealthMetric.user_id == newcomer.id)) == 1


async def test_giving_the_consent_from_reglages_is_said_there(client, newcomer, no_commit):
    async with _as(client, newcomer) as c:
        r = await c.post("/sante/consentement", data={"next": "/settings#donnees", "accept": "1"})
        assert r.headers["location"] == "/settings#donnees"
        page = (await c.get("/settings")).text
        assert 'role="status">Consentement enregistré.</p>' in page
