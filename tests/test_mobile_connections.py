"""The app's screens of COROS, Garmin and Strava (owner, 2026-10-17: the Apple Santé screen's two actions for each
one, « tout dans l'application »): their status details, « Synchroniser », « Déconnecter », and the sign-in sheet
a one-time ticket opens, back to the app with an outcome code."""
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlsplit

from httpx import AsyncClient

from app.config import settings
from app.routers import mobile
from app.services.strava import StravaService
from tests.test_mobile import login
from tests.test_settings_links import _callback, _logged_in, no_commit  # noqa: F401 (fixture)

NONCE = "c" * 64


async def test_status_details_say_the_last_sync_and_never_a_token(client, test_user):
    login(client, test_user)
    client.headers["Accept"] = "application/json"
    payload = (await client.get(f"/mobile/connections?nonce={NONCE}")).json()
    assert set(payload["details"]) == {"coros", "garmin", "strava"}
    assert payload["details"]["coros"] == {"last_sync": None, "notice": None}
    assert all(set(d) == {"last_sync", "notice"} for d in payload["details"].values())


async def test_actions_need_a_session_a_nonce_and_the_same_site(client, test_user):
    url = f"/mobile/connections?nonce={NONCE}&provider=coros&action=sync"
    assert (await client.post(url)).status_code in (303, 401)
    login(client, test_user)
    assert (await client.post(url.replace(NONCE, "bad"))).status_code == 400
    assert (await client.post(url.replace("coros", "suunto"))).status_code == 422
    assert (await client.post(url, headers={"Origin": "https://evil.example"})).status_code == 403
    r = await client.post(url)
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    assert r.json()["result"] == "COROS n’est pas connecté." and r.json()["nonce"] == NONCE
    assert (await client.post(url.replace("action=sync", "action=link").replace("coros", "garmin"))).status_code == 400


async def test_disconnect_strava_answers_the_new_status(client, db_session, test_user, monkeypatch, no_commit):
    monkeypatch.setattr(StravaService, "deauthorize", AsyncMock())
    test_user.strava_athlete_id, test_user.strava_access_token = 99, "at"
    await db_session.flush()
    login(client, test_user)
    r = await client.post(f"/mobile/connections?nonce={NONCE}&provider=strava&action=disconnect")
    assert r.json()["providers"]["strava"] == "disconnected"
    assert r.json()["result"] == "Strava déconnecté. Les données déjà reçues restent."
    assert test_user.strava_access_token is None


async def _ticket_link(client, db_session, provider="strava"):
    """Logged in (a real session), the app asks for the link its sign-in sheet opens."""
    r = await client.post(f"/mobile/connections?nonce={NONCE}&provider={provider}&action=link")
    link = r.json()["link"]
    assert link.startswith(f"{settings.BASE_URL.rstrip('/')}/mobile/oauth/{provider}#")
    return link.split("#", 1)[1]


async def test_the_ticket_signs_the_sheet_in_once_then_strava_returns_to_the_app(client, db_session, monkeypatch,
                                                                                 no_commit):
    monkeypatch.setattr(StravaService, "exchange_token", AsyncMock(return_value={
        "access_token": "at", "refresh_token": "rt", "expires_at": 2147483000,
        "athlete": {"id": 778, "firstname": "A", "lastname": "B", "profile": None}}))
    monkeypatch.setattr(StravaService, "create_webhook_subscription", AsyncMock(return_value=None))
    from app.crypto import encrypt_secret
    async with _logged_in(client, db_session, strava_client_id="4242", initial_sync_done=True,
                          strava_client_secret_encrypted=encrypt_secret("s3cret")) as (app, user):
        ticket = await _ticket_link(app, db_session)
        async with AsyncClient(transport=client._transport, base_url="https://test") as sheet:
            page = (await sheet.get("/mobile/oauth/strava")).text
            assert 'method="post"' in page and "location.hash" in page and ticket not in page
            r = await sheet.post("/mobile/oauth/strava", data={"ticket": ticket})
            assert r.status_code == 303 and r.headers["location"] == "/mobile/oauth/strava/confirm"
            confirm = (await sheet.get(r.headers["location"])).text  # the account, before Strava's consent
            assert "me@x.fr" in confirm and 'href="/auth/strava?next=app"' in confirm
            assert 'href="paceforge://devices?connection=strava&amp;result=cancelled"' in confirm
            r = await sheet.get("/auth/strava?next=app")
            assert parse_qs(urlsplit(r.headers["location"]).query)["client_id"] == ["4242"]
            r = await _callback(sheet, code="abc")
            assert r.headers["location"] == "paceforge://devices?connection=strava&result=ok"
            assert user.strava_access_token == "at"
        async with AsyncClient(transport=client._transport, base_url="https://test") as again:
            r = await again.post("/mobile/oauth/strava", data={"ticket": ticket})  # one use
            assert r.headers["location"] == "paceforge://devices?connection=strava&result=expired"
            r = await again.post("/mobile/oauth/coros", data={"ticket": await _ticket_link(app, db_session)})
            assert r.headers["location"] == "paceforge://devices?connection=coros&result=expired"  # its provider


async def test_an_old_or_forged_ticket_opens_nothing(client, db_session, monkeypatch, no_commit):
    async with _logged_in(client, db_session) as (app, user):
        ticket = await _ticket_link(app, db_session, "coros")
        monkeypatch.setattr(mobile, "TICKET_TTL_S", -1)
        async with AsyncClient(transport=client._transport, base_url="https://test") as sheet:
            for value in (ticket, "forged"):
                r = await sheet.post("/mobile/oauth/coros", data={"ticket": value})
                assert r.headers["location"] == "paceforge://devices?connection=coros&result=expired"
            assert not sheet.cookies.get("paceforge_session")  # never signed in


async def test_a_cancelled_strava_sheet_returns_its_code(client, db_session, no_commit):
    async with _logged_in(client, db_session, strava_client_id="4242") as (app, user):
        from app.crypto import encrypt_secret
        user.strava_client_secret_encrypted = encrypt_secret("s3cret")
        await db_session.flush()
        await app.get("/auth/strava?next=app")
        r = await _callback(app, error="access_denied")
        assert r.headers["location"] == "paceforge://devices?connection=strava&result=cancelled"
