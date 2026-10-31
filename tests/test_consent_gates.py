"""No health data without the consent (GDPR art. 9.2.a), for the accounts that already have a source too: Garmin's
login asks for it first (around its router), the storage writes nothing without it (COROS's and Garmin's syncs,
the phone's nights), COROS is asked for the sessions only, the phone's pushes are refused with a 403 the app keeps
its data on. The activities keep coming."""
import logging
from datetime import date, datetime, timedelta, timezone
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select

from app.dependencies import get_current_user
from app.models import Activity, HealthMetric, HealthSample, MobileDaily, MobileDevice, User
from app.services import coros, garmin, health_consent
from app.services.health import Daily, Sample, store_daily, store_samples
from tests.test_coros import _link, fake  # noqa: F401 (a fixture)
from tests.test_mobile import login, paired


@pytest.fixture
def no_commit(db_session, monkeypatch):
    monkeypatch.setattr(db_session, "commit", db_session.flush)


@pytest.fixture
async def newcomer(db_session) -> User:
    """An account without the consent: one from before it was asked, with its sources still linked."""
    user = User(email="old@x.fr", email_verified=True, firstname="Camille", timezone="Europe/Paris")
    db_session.add(user)
    await db_session.flush()
    return user


def _as(client: AsyncClient, user: User) -> AsyncClient:
    client._transport.app.dependency_overrides[get_current_user] = lambda: user  # type: ignore[attr-defined]
    return AsyncClient(transport=client._transport, base_url="https://test")


async def _count(db, model, user_id) -> int:
    return await db.scalar(select(func.count()).select_from(model).where(model.user_id == user_id)) or 0


# ── Garmin's login ─────────────────────────────────────────────────────────

async def test_garmin_connect_asks_for_the_consent_first(client, newcomer, no_commit, monkeypatch):
    monkeypatch.setattr(garmin, "start_login", AsyncMock(return_value="ticket"))
    form = {"email": "me@garmin.example", "password": "secret"}
    async with _as(client, newcomer) as c:
        r = await c.post("/garmin/connect", data={**form, "next": "devices"})  # the app's connection page
        assert r.status_code == 303
        target = urlsplit(r.headers["location"])
        assert target.path == "/sante/consentement" and parse_qs(target.query)["next"] == ["/connections/garmin"]
        r = await c.post("/garmin/connect", data=form, headers={"HX-Request": "true"})  # Réglages' form (htmx)
        assert r.status_code == 204 and r.headers["HX-Redirect"] == health_consent.consent_url("/settings#garmin")
        assert not r.content
        garmin.start_login.assert_not_called()  # Garmin never asked, the password never used
        health_consent.record(newcomer)
        r = await c.post("/garmin/connect", data=form, headers={"HX-Request": "true"})
        assert r.status_code == 200 and "Connexion à Garmin" in r.text  # unchanged once given
        garmin.start_login.assert_awaited_once_with(newcomer.id, "me@garmin.example", "secret")


async def test_garmin_steps_after_the_login_and_disconnect_are_not_gated(client, newcomer, no_commit, monkeypatch):
    monkeypatch.setattr(garmin, "login_state", AsyncMock(return_value=None))
    async with _as(client, newcomer) as c:
        r = await c.get("/garmin/login")
        assert r.status_code == 200 and "expiré" in r.text
        r = await c.post("/settings/garmin/disconnect")
        assert r.status_code == 303 and r.headers["location"] == "/settings#garmin"


async def test_the_apps_garmin_page_asks_before_the_password(client, db_session, newcomer, no_commit):
    login(client, newcomer)
    r = await client.get("/connections/garmin")
    assert r.status_code == 303 and r.headers["location"] == health_consent.consent_url("/connections/garmin")
    page = (await client.get(r.headers["location"])).text
    assert 'href="/settings#connections">Pas maintenant</a>' in page  # the app opens its connections there
    health_consent.record(newcomer)
    await db_session.flush()
    r = await client.get("/connections/garmin")
    assert r.status_code == 200 and 'action="/garmin/connect"' in r.text


# ── the storage ───────────────────────────────────────────────────────────

async def test_the_storage_writes_no_health_data_without_the_consent(db_session, newcomer, caplog):
    day = date(2026, 10, 30)
    rows = [Daily("sleep", day, 420, {"main_start": "2026-10-29T23:00"}), Daily("steps", day, 9000)]
    start = datetime(2026, 10, 29, 23, 0)
    samples = [Sample("sleep", "deep", start, start + timedelta(minutes=40), 40, "Garmin")]
    with caplog.at_level(logging.INFO, logger="app.services.health"):
        daily = await store_daily(db_session, newcomer.id, rows, "Garmin")
        raw = await store_samples(db_session, newcomer.id, samples)
    assert daily["no_consent"] and daily["inserted"] == 0 and raw["no_consent"] and raw["inserted"] == 0
    assert await _count(db_session, HealthMetric, newcomer.id) == 0
    assert await _count(db_session, HealthSample, newcomer.id) == 0
    assert "2 health daily values of user" in caplog.text and "no consent to health data" in caplog.text
    health_consent.record(newcomer)
    await db_session.flush()
    assert (await store_daily(db_session, newcomer.id, rows, "Garmin"))["inserted"] == 2
    assert (await store_samples(db_session, newcomer.id, samples))["inserted"] == 1


async def test_an_older_consent_text_counts_as_none(db_session, newcomer):
    newcomer.health_consent_at, newcomer.health_consent_version = datetime.now(timezone.utc), "2026-10-25"
    await db_session.flush()
    assert not await health_consent.allowed(db_session, newcomer.id)
    await store_daily(db_session, newcomer.id, [Daily("steps", date(2026, 10, 30), 9000)], "COROS")
    assert await _count(db_session, HealthMetric, newcomer.id) == 0


# ── COROS: the sessions only ───────────────────────────────────────────────

HEALTH_TOOLS = {"querySleepOverview", "querySleepHrv", "queryAvgHeartRate", "queryDailyHealthData"}


async def test_coros_without_the_consent_brings_the_sessions_only(db_session, newcomer, fake, no_commit):  # noqa: F811
    now = datetime.now(timezone.utc).replace(hour=10, minute=0, second=0, microsecond=0)
    fake.sessions = [{"label": 222, "code": 102, "type": "Trail Run", "name": "Crêtes",
                      "start": now - timedelta(days=1), "seconds": 7200, "km": 18.4}]
    conn = await _link(db_session, newcomer)
    outcome = await coros.run_sync(db_session, conn)
    assert outcome["ok"], outcome
    called = {name for name, _ in fake.tool_calls}
    assert not called & HEALTH_TOOLS and "querySportRecords" in called  # no health call at all
    assert await _count(db_session, HealthMetric, newcomer.id) == 0
    (act,) = (await db_session.execute(select(Activity).where(Activity.user_id == newcomer.id))).scalars().all()
    assert act.coros_activity_id == 222 and act.name == "Crêtes"
    # placed on the athlete's zone (the HRV answer that gives it otherwise was never asked)
    paris = (now - timedelta(days=1)).astimezone(ZoneInfo("Europe/Paris")).utcoffset().total_seconds()
    assert act.raw_data["utc_offset"] == paris
    assert conn.last_error is None  # not a history « cut short »

    health_consent.record(newcomer)  # given: the next sync reads the health history too
    fake.tool_calls.clear()
    outcome = await coros.run_sync(db_session, conn)
    assert outcome["ok"] and {name for name, _ in fake.tool_calls} >= HEALTH_TOOLS
    assert await _count(db_session, HealthMetric, newcomer.id) > 0


async def test_a_consent_given_after_a_gap_reads_the_coros_history_whole(db_session, newcomer, fake,  # noqa: F811
                                                                          no_commit):
    """The days the syncs could not store before a consent (a first one, or a new text) are read once it is given:
    the week a sync reads would leave them out. Only for two days after it: a watch left in a drawer never makes
    every day's first sync a 60-day read."""
    from sqlalchemy import delete

    def hrv_calls() -> int:
        return len([a for n, a in fake.tool_calls if n == "querySleepHrv"])

    health_consent.record(newcomer)
    conn = await _link(db_session, newcomer)
    assert (await coros.run_sync(db_session, conn))["ok"] and hrv_calls() == 9  # the first sync: the 60 days
    today = datetime.now(timezone.utc).date()
    db_session.add(HealthMetric(user_id=newcomer.id, date=today - timedelta(days=30), metric="steps", value=9000,
                                source=coros.SOURCE))  # a day stored long before
    # the syncs then stored nothing for 20 days (the consent text changed, given again only now)
    await db_session.execute(delete(HealthMetric).where(HealthMetric.user_id == newcomer.id,
                                                        HealthMetric.date > today - timedelta(days=20)))
    newcomer.health_consent_at = datetime.now(timezone.utc) - timedelta(days=3)  # given 3 days ago: too late
    conn.last_sync_at -= timedelta(days=1)
    fake.tool_calls.clear()
    assert (await coros.run_sync(db_session, conn))["ok"] and hrv_calls() == 1  # the week only
    await db_session.execute(delete(HealthMetric).where(HealthMetric.user_id == newcomer.id,
                                                        HealthMetric.date > today - timedelta(days=20)))
    health_consent.record(newcomer)  # given now
    conn.last_sync_at -= timedelta(days=1)
    fake.tool_calls.clear()
    assert (await coros.run_sync(db_session, conn))["ok"] and hrv_calls() == 9  # the gap read whole
    conn.last_sync_at -= timedelta(days=1)
    fake.tool_calls.clear()
    assert (await coros.run_sync(db_session, conn))["ok"] and hrv_calls() == 1  # filled: the week again


# ── the phone's pushes ─────────────────────────────────────────────────────

def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def test_the_phone_pushes_are_refused_without_the_consent_and_the_phone_stays_paired(client, db_session,
                                                                                          test_user):
    from tests.test_apple_nights import packet
    from tests.test_mobile import day

    account, _ = await paired(client, test_user)
    test_user.health_consent_version = "2026-10-25"  # an older text: asked again
    await db_session.flush()
    for path, body in (("/api/mobile/daily", {"days": [day()]}), ("/api/mobile/nights", packet())):
        r = await client.post(path, json=body, headers=_bearer(account["token"]))
        assert r.status_code == 403, path
        assert r.json() == {"detail": "Accepte le suivi de tes données de santé dans PaceForge pour envoyer tes "
                                      "mesures.", "code": "health_consent_required"}
        assert r.headers["cache-control"] == "no-store"
    assert await _count(db_session, MobileDaily, test_user.id) == 0
    assert await _count(db_session, HealthMetric, test_user.id) == 0
    assert await _count(db_session, MobileDevice, test_user.id) == 1  # still paired: nothing to do again
    health_consent.record(test_user)
    await db_session.flush()
    r = await client.post("/api/mobile/daily", json={"days": [day()]}, headers=_bearer(account["token"]))
    assert r.status_code == 200 and r.json()["received"] == 1
    r = await client.post("/api/mobile/nights", json=packet(), headers=_bearer(account["token"]))
    assert r.status_code == 200 and r.json()["received"] == 4


async def test_a_refused_push_is_never_parsed(client, db_session, test_user, monkeypatch):
    from app.routers import mobile

    account, _ = await paired(client, test_user)
    test_user.health_consent_at = None
    await db_session.flush()
    parsed = []
    monkeypatch.setattr(mobile, "validated", lambda *a: parsed.append(a))
    r = await client.post("/api/mobile/daily", content=b"{not json", headers=_bearer(account["token"]))
    assert r.status_code == 403 and not parsed
