"""The official COROS and Garmin connectors (app.connectors, docs/official-connectors.md): off, nothing shows, runs
or calls out; on, the OAuth flows (state, PKCE for Garmin, every error), tokens encrypted and refreshed with
rotation, the pushes checked (credentials, payload shape, known users only, consent) and stored idempotently in the
same places as the former links (Activity, HealthMetric, HealthSample: source « COROS » / « Garmin »), the
lifecycle notices, the pages, the phone app's flow, the privacy page, the schedule.

The provider payloads are tests/official_connector_fixtures.py's: ASSUMED shapes, labelled so."""
import base64
import hashlib
import importlib.util
import json
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy import func, select

from app import connectors
from app.config import settings
from app.connectors import base, coros_partner, fit, garmin_connect
from app.crypto import decrypt_secret, encrypt_secret
from app.dependencies import get_current_user
from app.middleware.body_limit import DEFAULT_LIMIT, body_limit
from app.models import Activity, CorosConnection, GarminConnection, HealthMetric, HealthSample, OfficialConnection, User
from app.services import health_consent
from app.services.health import HRV_METHOD
from app.services.night_measurements import ASLEEP, WINDOW
from tests import official_connector_fixtures as fx

ROOT = Path(__file__).resolve().parents[1]
MIG = ROOT / "alembic/versions/k2f3a4b5c6d7_official_connections.py"
GARMIN_TOKEN = "https://diauth.garmin.com/di-oauth2-service/oauth/token"
GARMIN_API = "https://apis.garmin.com/wellness-api/rest"
COROS = "https://open.coros.com"


# ── a fake of both providers (their answers as public sources describe them) ──

class FakeProviders:
    def __init__(self):
        self.calls: list[httpx.Request] = []
        self.token_calls: list[dict] = []
        self.challenge: str | None = None
        self.n = 1
        self.token_error: str | None = None
        self.refresh_error: str | None = None
        self.garmin_user = fx.GARMIN_USER
        self.user_id_status = 200
        self.coros_refresh_extends = False
        self.deregistered: list[str] = []
        self.deauthorized: list[str] = []
        self.workouts: list[dict] = []
        self.daily: list[dict] = []
        self.fit = fx.coros_fit()

    def _tokens(self, **extra) -> httpx.Response:
        self.n += 1
        return httpx.Response(200, json={"access_token": f"at-{self.n}", "refresh_token": f"rt-{self.n}",
                                         "expires_in": 86400, "token_type": "bearer", **extra})

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        url = str(request.url).split("?")[0]
        form = dict(parse_qs(request.content.decode())) if request.method == "POST" else {}
        form = {k: v[0] for k, v in form.items()}
        bearer = request.headers.get("authorization", "")
        if url == GARMIN_TOKEN:
            self.token_calls.append(form)
            assert form["client_id"] == "garmin-client" and form["client_secret"] == "garmin-secret"
            if form["grant_type"] == "authorization_code":
                if self.token_error:
                    return httpx.Response(400, json={"error": self.token_error})
                digest = base64.urlsafe_b64encode(hashlib.sha256(form["code_verifier"].encode()).digest())
                assert digest.rstrip(b"=").decode() == self.challenge, "the verifier must match the challenge"
                assert form["redirect_uri"].endswith("/connect/garmin/callback")
                return self._tokens(refresh_token_expires_in=7775998)
            if self.refresh_error:
                return httpx.Response(400, json={"error": self.refresh_error})
            return self._tokens(refresh_token_expires_in=7775998)
        if url == f"{GARMIN_API}/user/id":
            if self.user_id_status != 200 or not bearer.startswith("Bearer at-"):
                return httpx.Response(self.user_id_status if self.user_id_status != 200 else 401)
            return httpx.Response(200, json={"userId": self.garmin_user})
        if url == f"{GARMIN_API}/user/permissions":
            return httpx.Response(200, json=["ACTIVITY_EXPORT", "HEALTH_EXPORT", "WORKOUT_IMPORT"])
        if url == f"{GARMIN_API}/user/registration" and request.method == "DELETE":
            self.deregistered.append(bearer)
            return httpx.Response(204)
        if url == f"{COROS}/oauth2/accesstoken":
            self.token_calls.append(form)
            assert form["client_secret"] == "coros-secret" and "code_verifier" not in form
            if self.token_error:
                return httpx.Response(400, json={"error": self.token_error})
            return self._tokens(openId=fx.COROS_OPEN_ID, expires_in=2592000)
        if url == f"{COROS}/oauth2/refresh-token":
            self.token_calls.append(form)
            if self.coros_refresh_extends:
                return httpx.Response(200, json={"result": "0000", "message": "OK"})
            return self._tokens(expires_in=2592000)
        if url == f"{COROS}/oauth2/deauthorize":
            self.deauthorized.append(request.url.params.get("token"))
            return httpx.Response(200, json={"result": "0000"})
        if url == f"{COROS}/v2/coros/sport/list":
            assert request.url.params.get("openId") == fx.COROS_OPEN_ID and request.url.params.get("token")
            return httpx.Response(200, json={"result": "0000", "message": "OK", "data": self.workouts})
        if url == f"{COROS}/v2/coros/daily/list":
            return httpx.Response(200, json={"result": "0000", "message": "OK", "data": self.daily})
        if url == fx.FIT_URL:
            return httpx.Response(200, content=self.fit)
        return httpx.Response(404)


@pytest.fixture
def fake(monkeypatch) -> FakeProviders:
    f = FakeProviders()
    monkeypatch.setattr(base, "_transport", httpx.MockTransport(f.handler))
    return f


@pytest.fixture
def garmin_on(monkeypatch):
    monkeypatch.setattr(settings, "GARMIN_CONNECT_ENABLED", True)
    monkeypatch.setattr(settings, "GARMIN_CONNECT_CLIENT_ID", "garmin-client")
    monkeypatch.setattr(settings, "GARMIN_CONNECT_CLIENT_SECRET", "garmin-secret")


@pytest.fixture
def coros_on(monkeypatch):
    monkeypatch.setattr(settings, "COROS_PARTNER_ENABLED", True)
    monkeypatch.setattr(settings, "COROS_PARTNER_CLIENT_ID", "coros-client")
    monkeypatch.setattr(settings, "COROS_PARTNER_CLIENT_SECRET", "coros-secret")


@pytest.fixture
def no_commit(db_session, monkeypatch):
    """The connectors commit (tokens must survive a later rollback): flush instead, the test's rollback cleans up."""
    monkeypatch.setattr(db_session, "commit", db_session.flush)


@pytest.fixture
async def as_user(client: AsyncClient, test_user: User, no_commit):
    client._transport.app.dependency_overrides[get_current_user] = lambda: test_user  # type: ignore[attr-defined]
    async with AsyncClient(transport=client._transport, base_url="https://test") as c:  # Secure session cookie
        yield c


@pytest.fixture
def queued(monkeypatch) -> dict[str, list]:
    """What the routes hand to the worker, instead of a broker."""
    from app.tasks import official_connectors as tasks

    jobs: dict[str, list] = {"garmin": [], "coros": [], "coros_sync": [], "night": []}
    for name, task in (("garmin", tasks.garmin_push), ("coros", tasks.coros_push), ("coros_sync", tasks.coros_sync),
                       ("night", tasks.garmin_night)):
        monkeypatch.setattr(task, "apply_async", lambda args, *a, _n=name, **k: jobs[_n].append(args))
    return jobs


def _q(location: str) -> dict:
    return {k: v[0] for k, v in parse_qs(urlsplit(location).query).items()}


async def _link(db, user: User, provider: str = "garmin", access="at-1", refresh="rt-1",
                expires_in=timedelta(hours=20), **extra) -> OfficialConnection:
    conn = OfficialConnection(
        user_id=user.id, provider=provider,
        provider_user_id=fx.GARMIN_USER if provider == "garmin" else fx.COROS_OPEN_ID,
        access_token_encrypted=encrypt_secret(access), refresh_token_encrypted=encrypt_secret(refresh),
        expires_at=datetime.now(timezone.utc) + expires_in, **extra)
    db.add(conn)
    await db.flush()
    return conn


# ── off: nothing shows, runs or calls out ───────────────────────────────────

ROUTES = (("GET", "/connect/coros"), ("GET", "/connect/garmin"), ("GET", "/connect/coros/callback?code=c&state=s"),
          ("GET", "/connect/garmin/callback?code=c&state=s"), ("POST", "/connect/garmin/disconnect"),
          ("POST", "/connect/coros/disconnect"), ("POST", "/webhook/coros"), ("POST", "/webhook/garmin"),
          ("POST", "/webhook/garmin/dailies"), ("POST", "/webhook/garmin/deregistrations"),
          ("GET", "/mobile/oauth/garmin"))


async def test_off_every_route_answers_404_and_nothing_is_scheduled_shown_or_called(as_user, monkeypatch):
    def no_call(request):  # pragma: no cover - the point is that it never runs
        raise AssertionError(f"called {request.url}")
    monkeypatch.setattr(base, "_transport", httpx.MockTransport(no_call))
    assert not connectors.active_providers() and connectors.beat_schedule() == {}
    from app.celery_app import celery_app
    assert not any(k.startswith("official") for k in celery_app.conf.beat_schedule)
    for method, path in ROUTES:
        body = {"content": b'{"dailies": []}', "headers": {"garmin-client-id": "x", "content-type": "application/json"}}
        r = await as_user.request(method, path, **(body if method == "POST" else {}))
        assert r.status_code == 404, (method, path, r.status_code)
    assert body_limit("/webhook/garmin/activityDetails") == DEFAULT_LIMIT == body_limit("/webhook/coros")
    page = (await as_user.get("/settings")).text
    assert "connexion officielle" not in page and "/connect/" not in page and 'id="official-' not in page
    privacy = (await as_user.get("/privacy")).text
    assert "connexion officielle" not in privacy
    as_user.headers["Accept"] = "application/json"
    payload = (await as_user.get(f"/mobile/connections?nonce={'c' * 64}")).json()
    assert "official" not in payload


async def test_enabled_without_its_credentials_is_still_off(as_user, monkeypatch):
    monkeypatch.setattr(settings, "GARMIN_CONNECT_ENABLED", True)
    monkeypatch.setattr(settings, "GARMIN_CONNECT_CLIENT_ID", "garmin-client")
    assert not connectors.ready("garmin")
    assert (await as_user.get("/connect/garmin")).status_code == 404
    monkeypatch.setattr(settings, "GARMIN_CONNECT_CLIENT_SECRET", "garmin-secret")
    assert connectors.ready("garmin") and connectors.active_providers() == ["garmin"]
    schedule = connectors.beat_schedule()["official-connectors"]
    assert schedule["task"] == "paceforge.official_round"
    from app.celery_app import celery_app
    poll = celery_app.conf.beat_schedule["poll-new-activities"]["schedule"].minute
    assert schedule["schedule"].minute.isdisjoint(poll)


async def test_the_pilot_list_limits_who_sees_and_starts_it(as_user, test_user, garmin_on, monkeypatch):
    monkeypatch.setattr(settings, "OFFICIAL_CONNECTORS_PILOT_USERS", f"{test_user.id + 1000}")
    assert (await as_user.get("/connect/garmin")).status_code == 404
    assert 'id="official-garmin"' not in (await as_user.get("/settings")).text
    monkeypatch.setattr(settings, "OFFICIAL_CONNECTORS_PILOT_USERS", f"{test_user.id + 1000}, {test_user.id}")
    assert (await as_user.get("/connect/garmin")).status_code == 302
    assert 'id="official-garmin"' in (await as_user.get("/settings")).text


# ── Garmin OAuth (PKCE) ─────────────────────────────────────────────────────

async def _garmin_authorize(c: AsyncClient, fake: FakeProviders, next_: str = "") -> str:
    r = await c.get("/connect/garmin" + (f"?next={next_}" if next_ else ""))
    assert r.status_code == 302
    q = _q(r.headers["location"])
    fake.challenge = q["code_challenge"]
    return q["state"]


async def test_garmin_connect_redirects_with_state_and_pkce(as_user, garmin_on, fake):
    r = await as_user.get("/connect/garmin")
    loc = urlsplit(r.headers["location"])
    q = _q(r.headers["location"])
    assert f"{loc.scheme}://{loc.netloc}{loc.path}" == "https://connect.garmin.com/oauth2Confirm"
    assert q["response_type"] == "code" and q["client_id"] == "garmin-client"
    assert q["code_challenge_method"] == "S256" and len(q["code_challenge"]) == 43 and len(q["state"]) >= 24
    assert q["redirect_uri"] == f"{settings.BASE_URL.rstrip('/')}/connect/garmin/callback"
    assert not fake.calls  # nothing asked of Garmin before the athlete comes back


async def test_garmin_connect_asks_for_the_health_consent_first(client, db_session, garmin_on, fake, no_commit):
    newcomer = User(email="new@x.fr", email_verified=True)
    db_session.add(newcomer)
    await db_session.flush()
    client._transport.app.dependency_overrides[get_current_user] = lambda: newcomer  # type: ignore[attr-defined]
    async with AsyncClient(transport=client._transport, base_url="https://test") as c:
        r = await c.get("/connect/garmin?next=app")
        assert r.status_code == 303 and r.headers["location"].startswith("/sante/consentement?")
        assert _q(r.headers["location"])["next"] == "/connect/garmin?next=app"  # the consent page sends back here
    assert health_consent.not_now_url("/connect/garmin") == "/settings#connections"


async def test_garmin_callback_stores_the_link_encrypted_and_replaces_the_former_one(
        as_user, db_session, test_user, garmin_on, fake, monkeypatch):
    db_session.add(GarminConnection(user_id=test_user.id, di_client_id="d", access_token_encrypted=encrypt_secret("g")))
    await db_session.flush()
    state = await _garmin_authorize(as_user, fake)
    r = await as_user.get(f"/connect/garmin/callback?code=the-code&state={state}")
    assert r.status_code == 303 and r.headers["location"] == "/settings#official-garmin"
    conn = (await db_session.execute(select(OfficialConnection))).scalar_one()
    assert conn.user_id == test_user.id and conn.provider == "garmin" and conn.provider_user_id == fx.GARMIN_USER
    assert b"at-2" not in conn.access_token_encrypted and decrypt_secret(conn.access_token_encrypted) == "at-2"
    assert decrypt_secret(conn.refresh_token_encrypted) == "rt-2" and conn.refresh_expires_at is not None
    assert conn.scopes == "ACTIVITY_EXPORT HEALTH_EXPORT WORKOUT_IMPORT" and conn.status == "active"
    assert (await db_session.execute(select(GarminConnection))).scalar_one_or_none() is None  # one link per brand
    page = (await as_user.get("/settings")).text
    assert "Garmin connecté" in page and 'action="/connect/garmin/disconnect"' in page
    # the state is single use
    r = await as_user.get(f"/connect/garmin/callback?code=the-code&state={state}")
    assert "expiré" in (await as_user.get("/settings")).text and len(fake.token_calls) == 1


async def test_garmin_callback_refuses_a_forged_state_a_refusal_and_a_failed_exchange(
        as_user, db_session, garmin_on, fake):
    await _garmin_authorize(as_user, fake)
    r = await as_user.get("/connect/garmin/callback?code=the-code&state=forged")
    assert r.status_code == 303 and not fake.token_calls
    assert "expiré" in (await as_user.get("/settings")).text
    state = await _garmin_authorize(as_user, fake)
    await as_user.get(f"/connect/garmin/callback?error=access_denied&state={state}")
    assert "Connexion à Garmin annulée" in (await as_user.get("/settings")).text
    fake.token_error = "invalid_grant"
    state = await _garmin_authorize(as_user, fake)
    await as_user.get(f"/connect/garmin/callback?code=bad&state={state}")
    assert "La connexion à Garmin a échoué" in (await as_user.get("/settings")).text
    fake.token_error, fake.user_id_status = None, 500  # no user id: nothing to match the pushes with
    state = await _garmin_authorize(as_user, fake)
    await as_user.get(f"/connect/garmin/callback?code=the-code&state={state}")
    assert "a échoué" in (await as_user.get("/settings")).text
    assert (await db_session.execute(select(func.count(OfficialConnection.id)))).scalar() == 0


async def test_a_garmin_account_linked_elsewhere_is_refused(as_user, db_session, garmin_on, fake):
    other = User(email="other@x.fr")
    db_session.add(other)
    await db_session.flush()
    await _link(db_session, other)
    state = await _garmin_authorize(as_user, fake)
    await as_user.get(f"/connect/garmin/callback?code=the-code&state={state}")
    assert "déjà relié à un autre compte" in (await as_user.get("/settings")).text
    assert (await db_session.execute(select(func.count(OfficialConnection.id)))).scalar() == 1


async def test_from_the_app_the_outcome_returns_to_it_as_a_code(as_user, garmin_on, fake):
    for params, result in (("code=the-code", "ok"), ("error=access_denied", "cancelled"), ("code=x", "expired")):
        state = await _garmin_authorize(as_user, fake, "app")
        if result == "expired":
            state = "forged"
        r = await as_user.get(f"/connect/garmin/callback?{params}&state={state}")
        assert r.headers["location"] == f"paceforge://devices?connection=garmin&result={result}", params


# ── COROS OAuth (no PKCE: none listed) ──────────────────────────────────────

async def test_coros_connect_and_callback_store_the_open_id_replace_the_mcp_link_and_backfill(
        as_user, db_session, test_user, coros_on, fake, queued):
    from tests.test_coros import FakeCoros

    db_session.add(CorosConnection(
        user_id=test_user.id, issuer=FakeCoros.META["issuer"], client_id="cid",
        access_token_encrypted=encrypt_secret("m"), resource_url=FakeCoros.PRM["resource"],
        token_endpoint=FakeCoros.META["token_endpoint"]))
    await db_session.flush()
    r = await as_user.get("/connect/coros")
    q = _q(r.headers["location"])
    assert r.headers["location"].startswith("https://open.coros.com/oauth2/authorize?")
    assert q["client_id"] == "coros-client" and q["response_type"] == "code" and "code_challenge" not in q
    assert q["redirect_uri"] == f"{settings.BASE_URL.rstrip('/')}/connect/coros/callback"
    with patch("app.services.coros.http_client", side_effect=httpx.ConnectError("no MCP here")):
        r = await as_user.get(f"/connect/coros/callback?code=c&state={q['state']}")
    assert r.status_code == 303 and r.headers["location"] == "/settings#official-coros"
    conn = (await db_session.execute(select(OfficialConnection))).scalar_one()
    assert conn.provider_user_id == fx.COROS_OPEN_ID and decrypt_secret(conn.access_token_encrypted) == "at-2"
    assert (await db_session.execute(select(CorosConnection))).scalar_one_or_none() is None
    assert queued["coros_sync"] == [[conn.id, True]]  # three months, in the worker


# ── tokens: refresh with rotation ───────────────────────────────────────────

async def test_garmin_refresh_rotates_and_survives_a_later_rollback(committing_session, garmin_on, fake):
    db = committing_session
    user = User(email="r@x.fr")
    db.add(user)
    await db.flush()
    conn = await _link(db, user, expires_in=timedelta(minutes=5))  # within Garmin's margin
    await db.commit()
    async with base.http_client() as client:
        assert await base.access_token(db, conn, client) == "at-2"
        assert fake.token_calls[-1] == {"grant_type": "refresh_token", "client_id": "garmin-client",
                                        "client_secret": "garmin-secret", "refresh_token": "rt-1"}
        # rejected by Garmin after another worker already rotated it: no second refresh
        assert await base.access_token(db, conn, client, rejected="at-1") == "at-2"
        assert len(fake.token_calls) == 1
        assert await base.access_token(db, conn, client, rejected="at-2") == "at-3"
        assert fake.token_calls[-1]["refresh_token"] == "rt-2"  # the rotated one, never the dead one
    db.add(User(email="later@x.fr"))
    await db.rollback()  # a later failure: the rotated pair was committed first
    await db.refresh(conn)
    assert decrypt_secret(conn.refresh_token_encrypted) == "rt-3"


async def test_a_refused_refresh_asks_to_reconnect(db_session, test_user, garmin_on, fake, no_commit):
    conn = await _link(db_session, test_user, expires_in=timedelta(hours=-1))
    fake.refresh_error = "invalid_grant"
    async with base.http_client() as client:
        with pytest.raises(base.ConnectorAuthError):
            await base.access_token(db_session, conn, client)
    assert conn.status == "reauth" and "reconnecte-toi" in conn.last_error
    s = await base.status(db_session, test_user.id, "garmin")
    assert s["connected"] and s["needs_reauth"]


async def test_a_coros_refresh_answering_ok_alone_extends_the_token(db_session, test_user, coros_on, fake, no_commit):
    fake.coros_refresh_extends = True
    conn = await _link(db_session, test_user, "coros", expires_in=timedelta(hours=2))
    async with base.http_client() as client:
        assert await base.access_token(db_session, conn, client) == "at-1"
    assert base.as_utc(conn.expires_at) > datetime.now(timezone.utc) + timedelta(days=29)


# ── pushes: checked, matched, queued ────────────────────────────────────────

def _garmin_post(c: AsyncClient, path: str, body, header: str | None = "garmin-client"):
    headers = {"content-type": "application/json", **({"garmin-client-id": header} if header else {})}
    return c.post(path, content=json.dumps(body).encode(), headers=headers)


async def test_garmin_pushes_are_checked_and_unknown_users_ignored(as_user, db_session, test_user, garmin_on,
                                                                   queued):
    conn = await _link(db_session, test_user)
    activity = {"activities": [fx.garmin_activity()]}
    assert (await _garmin_post(as_user, "/webhook/garmin/activities", activity, header=None)).status_code == 401
    assert (await _garmin_post(as_user, "/webhook/garmin/activities", activity, header="other")).status_code == 401
    assert (await _garmin_post(as_user, "/webhook/garmin/activities", ["x"])).status_code == 400
    assert (await _garmin_post(as_user, "/webhook/garmin/activities", {"dailies": []})).status_code == 400
    assert (await _garmin_post(as_user, "/webhook/garmin", {"nonsense": []})).status_code == 400
    assert (await _garmin_post(as_user, "/webhook/garmin/nonsense", activity)).status_code == 404
    assert not queued["garmin"]
    stranger = {"activities": [fx.garmin_activity(userId="someone-else")]}
    r = await _garmin_post(as_user, "/webhook/garmin/activities", stranger)
    assert r.status_code == 200 and r.json() == {"status": "ok"} and not queued["garmin"]
    r = await _garmin_post(as_user, "/webhook/garmin/activities", activity)
    assert r.status_code == 200 and r.json() == {"status": "ok"}  # the same answer: nothing tells who is known
    assert queued["garmin"] == [["activities", [{"conn": conn.id, "item": fx.garmin_activity()}]]]
    # the summaries PaceForge does not read: acknowledged, never queued
    assert (await _garmin_post(as_user, "/webhook/garmin/pulseox", {"pulseox": [{"userId": fx.GARMIN_USER}]})
            ).status_code == 200
    assert len(queued["garmin"]) == 1


async def test_a_garmin_header_is_optional_only_when_set_so(as_user, db_session, test_user, garmin_on, queued,
                                                           monkeypatch):
    await _link(db_session, test_user)
    monkeypatch.setattr(settings, "GARMIN_CONNECT_REQUIRE_CLIENT_HEADER", False)
    body = {"activities": [fx.garmin_activity()]}
    assert (await _garmin_post(as_user, "/webhook/garmin/activities", body, header=None)).status_code == 200
    assert (await _garmin_post(as_user, "/webhook/garmin/activities", body, header="wrong")).status_code == 401


async def test_without_the_consent_health_pushes_are_dropped_activities_still_come(
        as_user, db_session, test_user, garmin_on, queued):
    test_user.health_consent_at = None
    await _link(db_session, test_user)
    for kind, item in (("dailies", fx.garmin_daily()), ("sleeps", fx.garmin_sleep()), ("hrv", fx.garmin_hrv()),
                       ("bodyComps", fx.garmin_body())):
        assert (await _garmin_post(as_user, f"/webhook/garmin/{kind}", {kind: [item]})).status_code == 200
    assert not queued["garmin"]
    both = {"activities": [fx.garmin_activity()], "sleeps": [fx.garmin_sleep()]}
    await _garmin_post(as_user, "/webhook/garmin", both)
    assert [job[0] for job in queued["garmin"]] == ["activities"]


async def test_activity_details_are_reduced_to_laps_before_being_queued(as_user, db_session, test_user, garmin_on,
                                                                        queued):
    await _link(db_session, test_user)
    await _garmin_post(as_user, "/webhook/garmin/activityDetails", {"activityDetails": [fx.garmin_details()]})
    (kind, entries), = queued["garmin"]
    item = entries[0]["item"]
    assert kind == "activityDetails" and "samples" not in item and len(item["laps"]) == 3
    assert item["laps"][0]["moving_time"] == 1790 and 130 <= item["laps"][0]["average_heartrate"] <= 137


async def test_a_broker_down_answers_503_so_garmin_sends_again(as_user, db_session, test_user, garmin_on,
                                                               monkeypatch):
    from app.tasks import official_connectors as tasks

    await _link(db_session, test_user)

    def down(*a, **k):
        raise ConnectionError("broker down")
    monkeypatch.setattr(tasks.garmin_push, "apply_async", down)
    r = await _garmin_post(as_user, "/webhook/garmin/activities", {"activities": [fx.garmin_activity()]})
    assert r.status_code == 503


async def test_coros_pushes_need_its_client_id_and_secret(as_user, db_session, test_user, coros_on, queued):
    conn = await _link(db_session, test_user, "coros")
    body = json.dumps({"sportDataList": [fx.coros_workout(), fx.coros_workout(openId="stranger")]}).encode()
    for headers in ({}, {"client": "coros-client"}, {"client": "coros-client", "secret": "wrong"},
                    {"client": "other", "secret": "coros-secret"}):
        assert (await as_user.post("/webhook/coros", content=body, headers=headers)).status_code == 401
    assert (await as_user.post("/webhook/coros", content=b"[1]",
                               headers={"client": "coros-client", "secret": "coros-secret"})).status_code == 200
    assert (await as_user.post("/webhook/coros", content=b"{}",
                               headers={"client": "coros-client", "secret": "coros-secret"})).status_code == 400
    r = await as_user.post("/webhook/coros", content=body, headers={"client_id": "coros-client",
                                                                     "client_secret": "coros-secret"})
    assert r.status_code == 200 and r.json()["result"] == "0000"
    assert queued["coros"] == [[[{"conn": conn.id, "item": fx.coros_workout()}]]]  # the stranger's ignored


# ── processing: the same places as the former links ─────────────────────────

async def test_a_garmin_activity_is_one_row_however_often_it_comes(db_session, test_user, garmin_on, no_commit):
    conn = await _link(db_session, test_user)
    for _ in range(2):  # Garmin's retries, a replay
        assert await garmin_connect.process(db_session, "activities",
                                            [{"conn": conn.id, "item": fx.garmin_activity()}]) == []
    act = (await db_session.execute(select(Activity))).scalar_one()
    assert act.garmin_activity_id == 5001968355 and act.sport_type == "TrailRun" and act.user_id == test_user.id
    assert act.distance == 14200.5 and act.moving_time == 5400 and act.total_elevation_gain == 820.0
    assert act.average_heartrate == 148 and act.max_heartrate == 176 and act.average_cadence == 82.0
    assert act.raw_data["via"] == "garmin_connect_api" and act.raw_data["utc_offset"] == 3600
    assert act.raw_data["start_latlng"] == [45.18, 5.72] and act.start_date.replace(tzinfo=timezone.utc) == \
        datetime.fromtimestamp(fx.ACTIVITY_START, tz=timezone.utc)
    assert conn.last_push_at is not None
    # a multisport parent is not a session (its legs come as their own), another user's item is never trusted
    await garmin_connect.process(db_session, "activities", [
        {"conn": conn.id, "item": fx.garmin_activity(activityId=7, isParent=True)},
        {"conn": conn.id, "item": fx.garmin_activity(activityId=8, userId="someone-else")}])
    assert (await db_session.execute(select(func.count(Activity.id)))).scalar() == 1


async def test_a_session_the_former_link_or_strava_brought_is_never_duplicated(db_session, test_user, garmin_on,
                                                                              no_commit):
    conn = await _link(db_session, test_user)
    start = datetime.fromtimestamp(fx.ACTIVITY_START, tz=timezone.utc)
    strava = Activity(user_id=test_user.id, strava_activity_id=42, sport_type="TrailRun", name="Strava",
                      start_date=start + timedelta(seconds=40), distance=14100, moving_time=5300, elapsed_time=5500,
                      raw_data={})
    db_session.add(strava)
    await db_session.flush()
    await garmin_connect.process(db_session, "activities", [{"conn": conn.id, "item": fx.garmin_activity()}])
    assert (await db_session.execute(select(func.count(Activity.id)))).scalar() == 1
    assert strava.garmin_activity_id == 5001968355 and strava.name == "Strava"  # Strava's fields stay
    # the details never overwrite Strava's own laps
    await garmin_connect.process(db_session, "activityDetails",
                                 [{"conn": conn.id, "item": garmin_connect.detail_laps(fx.garmin_details())}])
    assert strava.laps is None


async def test_activity_details_give_garmins_own_row_its_laps(db_session, test_user, garmin_on, no_commit):
    conn = await _link(db_session, test_user)
    reduced = garmin_connect.detail_laps(fx.garmin_details())
    await garmin_connect.process(db_session, "activityDetails", [{"conn": conn.id, "item": reduced}])
    act = (await db_session.execute(select(Activity))).scalar_one()  # the details came first: the session made
    assert len(act.laps) == 3 and act.raw_data["laps_read"] is True
    assert act.laps[1]["distance"] == pytest.approx(1790 * 2.63, abs=1) and act.laps[2]["max_heartrate"] <= 250
    await garmin_connect.process(db_session, "activities", [{"conn": conn.id, "item": fx.garmin_activity()}])
    assert len(act.laps) == 3 and act.raw_data["laps_read"] is True  # the summary keeps them


async def _health(db, user) -> dict:
    rows = (await db.execute(select(HealthMetric).where(HealthMetric.user_id == user.id))).scalars().all()
    return {(r.metric, r.date.isoformat()): r for r in rows}


async def test_a_night_its_hrv_and_its_heart_rate_whatever_order_they_come_in(db_session, test_user, garmin_on,
                                                                             no_commit):
    conn = await _link(db_session, test_user)
    entry = lambda item: [{"conn": conn.id, "item": item}]  # noqa: E731
    # the HRV and the day before their night: waiting (queued again by the worker)
    waiting = await garmin_connect.process(db_session, "hrv", entry(fx.garmin_hrv()))
    waiting += await garmin_connect.process(db_session, "dailies", entry(fx.garmin_daily()))
    assert sorted(w["metric"] for w in waiting) == ["hr_night", "hrv"]
    rows = await _health(db_session, test_user)
    assert set(rows) == {("steps", "2026-11-14"), ("hr_day", "2026-11-14"), ("stress", "2026-11-14")}
    assert rows[("steps", "2026-11-14")].value == 12500 and rows[("steps", "2026-11-14")].source == "Garmin"
    steps = rows[("steps", "2026-11-14")].details
    assert steps["kcal"] == 2450 and steps["exercise"] == 60
    assert rows[("stress", "2026-11-14")].details["method"] == "garmin_daily"
    # the night
    await garmin_connect.process(db_session, "sleeps", entry(fx.garmin_sleep()))
    rows = await _health(db_session, test_user)
    sleep = rows[("sleep", "2026-11-14")]
    assert sleep.value == 420 and sleep.source == "Garmin"
    assert sleep.details["main_start"] == "2026-11-13T23:00" and sleep.details["main_end"] == "2026-11-14T06:30"
    assert sleep.details["stages"] == {"deep": 90, "light": 240, "rem": 90, "awake": 30} and sleep.details["timeline"]
    assert rows[("resp_night", "2026-11-14")].details["scope"] == ASLEEP
    assert rows[("nap", "2026-11-14")].value == 30
    assert not any(m in ("sleep_score", "recovery", "body_battery", "rhr") for m, _ in rows)  # no brand score
    samples = (await db_session.execute(select(HealthSample).where(HealthSample.source == "Garmin"))).scalars().all()
    assert sorted({s.kind for s in samples}) == ["awake", "core", "deep", "rem"]
    # the waiting values tried again (the worker): stored inside the asleep stages
    for w in waiting:
        assert await garmin_connect.deferred_night(db_session, w["conn"], w["day"], w["metric"], w["readings"],
                                                   w["fallback"], final=False) == "stored"
    rows = await _health(db_session, test_user)
    hrv = rows[("hrv", "2026-11-14")]
    assert hrv.details["method"] == HRV_METHOD and hrv.details["scope"] == ASLEEP
    assert 40 <= hrv.value <= 47 and hrv.details["excluded_awake"] > 0  # Garmin's lastNightAvg (99) is not read
    hr_night = rows[("hr_night", "2026-11-14")]
    assert 48 <= hr_night.value <= 51 and hr_night.details["partial"] is True  # the part of the night after midnight
    # again, the same summaries: still one row each
    await garmin_connect.process(db_session, "sleeps", entry(fx.garmin_sleep()))
    assert len(await _health(db_session, test_user)) == len(rows)


async def test_an_hrv_whose_night_never_comes_falls_back_on_its_own_window(db_session, test_user, garmin_on,
                                                                           no_commit):
    conn = await _link(db_session, test_user)
    (w,) = await garmin_connect.process(db_session, "hrv", [{"conn": conn.id, "item": fx.garmin_hrv()}])
    assert await garmin_connect.deferred_night(db_session, w["conn"], w["day"], "hrv", w["readings"], w["fallback"],
                                               final=True) == "stored"
    hrv = (await _health(db_session, test_user))[("hrv", "2026-11-14")]
    assert hrv.details["scope"] == WINDOW


async def test_weigh_ins_are_dated_and_read_as_the_athletes_weight(db_session, test_user, garmin_on, no_commit):
    from app.services.athlete_weight import athlete_weight

    conn = await _link(db_session, test_user)
    await garmin_connect.process(db_session, "bodyComps", [{"conn": conn.id, "item": fx.garmin_body()}])
    rows = await _health(db_session, test_user)
    assert rows[("weight", "2026-11-14")].value == 68.4 and rows[("body_fat", "2026-11-14")].value == 14.2
    reading = await athlete_weight(db_session, test_user, now=datetime(2026, 11, 15, tzinfo=timezone.utc))
    assert reading.kg == 68.4 and reading.source == "Garmin"


async def test_no_health_row_without_the_consent_even_if_queued(db_session, test_user, garmin_on, no_commit):
    test_user.health_consent_at = None
    conn = await _link(db_session, test_user)
    for kind, item in (("dailies", fx.garmin_daily()), ("sleeps", fx.garmin_sleep()), ("bodyComps", fx.garmin_body())):
        await garmin_connect.process(db_session, kind, [{"conn": conn.id, "item": item}])
    await garmin_connect.process(db_session, "activities", [{"conn": conn.id, "item": fx.garmin_activity()}])
    assert not await _health(db_session, test_user)
    assert (await db_session.execute(select(func.count(HealthSample.id)))).scalar() == 0
    assert (await db_session.execute(select(func.count(Activity.id)))).scalar() == 1


async def test_a_deregistration_forgets_the_link_unless_garmin_still_confirms_it(db_session, test_user, garmin_on,
                                                                                 fake, no_commit):
    conn = await _link(db_session, test_user)
    notice = [{"conn": conn.id, "item": {"userId": fx.GARMIN_USER}}]
    await garmin_connect.process(db_session, "deregistrations", notice)  # Garmin still answers for this grant
    assert (await db_session.execute(select(OfficialConnection))).scalar_one() is conn
    fake.user_id_status = 401
    await garmin_connect.process(db_session, "deregistrations", notice)
    assert (await db_session.execute(select(OfficialConnection))).scalar_one_or_none() is None
    assert not fake.deregistered  # Garmin did it: nothing to call


async def test_a_permission_change_is_kept_and_said(db_session, test_user, garmin_on, no_commit):
    conn = await _link(db_session, test_user, scopes="ACTIVITY_EXPORT HEALTH_EXPORT")
    await garmin_connect.process(db_session, "userPermissionsChange", [
        {"conn": conn.id, "item": {"userId": fx.GARMIN_USER, "permissions": ["ACTIVITY_EXPORT"]}}])
    assert conn.scopes == "ACTIVITY_EXPORT"
    assert (await base.status(db_session, test_user.id, "garmin"))["missing"] == ["données de santé"]


# ── COROS: pushes and pulls ─────────────────────────────────────────────────

async def test_a_coros_push_reads_the_fit_file_and_stays_one_row(db_session, test_user, coros_on, fake, no_commit):
    conn = await _link(db_session, test_user, "coros")
    for _ in range(2):
        await coros_partner.process_push(db_session, conn, [fx.coros_workout()])
    act = (await db_session.execute(select(Activity))).scalar_one()
    assert act.coros_activity_id == 432112345678 and act.sport_type == "TrailRun"
    assert act.distance == 12034.5 and act.moving_time == 4520 and act.total_elevation_gain == 655  # the FIT's
    assert act.average_heartrate == 151 and act.max_heartrate == 182 and act.calories == 800
    assert act.laps == [{"moving_time": 2300, "average_heartrate": 145.0, "max_heartrate": 170.0, "distance": 6012.3},
                        {"moving_time": 2220, "average_heartrate": 157.0, "max_heartrate": 182.0, "distance": 6022.2}]
    assert act.raw_data["start_latlng"] == [45.1885, 5.7245] and act.raw_data["utc_offset"] == 3600
    assert "fitUrl" not in act.raw_data and conn.last_push_at is not None
    assert sum(str(c.url) == fx.FIT_URL for c in fake.calls) == 1  # read once


async def test_a_fit_file_is_fetched_only_from_coros_hosts(db_session, test_user, coros_on, fake, no_commit):
    conn = await _link(db_session, test_user, "coros")
    await coros_partner.process_push(db_session, conn, [fx.coros_workout(fitUrl="https://evil.example/x.fit")])
    act = (await db_session.execute(select(Activity))).scalar_one()
    assert not any("evil" in str(c.url) for c in fake.calls)
    assert act.distance == 12000 and act.sport_type == "TrailRun" and not act.laps  # the summary's (assumed) values


async def test_a_coros_pull_reads_workouts_and_with_the_consent_only_the_days(db_session, test_user, coros_on, fake,
                                                                             no_commit):
    conn = await _link(db_session, test_user, "coros")
    fake.workouts = [fx.coros_workout()]
    fake.daily = [{"happenDay": "20261114", "steps": 11000, "calorie": 2300, "rhr": 45}]
    outcome = await coros_partner.run_sync(db_session, conn, backfill=True)
    assert outcome["ok"] and conn.last_sync_at is not None and conn.sync_claimed_at is None
    windows = [c for c in fake.calls if "/sport/list" in str(c.url)]
    assert len(windows) == 4  # three months and the day ahead, 30 days a call
    rows = await _health(db_session, test_user)
    assert rows[("steps", "2026-11-14")].value == 11000 and rows[("steps", "2026-11-14")].source == "COROS"
    assert set(rows) == {("steps", "2026-11-14")}  # the resting HR is not read
    test_user.health_consent_at = None
    fake.calls.clear()
    await coros_partner.run_sync(db_session, conn)
    assert not any("/daily/" in str(c.url) for c in fake.calls)


async def test_the_mcp_links_row_is_the_same_session(db_session, test_user, coros_on, fake, no_commit):
    conn = await _link(db_session, test_user, "coros")
    mcp = Activity(user_id=test_user.id, coros_activity_id=999, sport_type="TrailRun", name="COROS",
                   start_date=datetime.fromtimestamp(fx.WORKOUT_START, tz=timezone.utc), distance=12010,
                   moving_time=4510, elapsed_time=4700, raw_data={"source": "coros"})
    db_session.add(mcp)
    await db_session.flush()
    for _ in range(2):
        await coros_partner.process_push(db_session, conn, [fx.coros_workout()])
    assert (await db_session.execute(select(func.count(Activity.id)))).scalar() == 1  # its twin, under another id
    assert mcp.coros_activity_id == 999 and not any(str(c.url) == fx.FIT_URL for c in fake.calls)  # its own detail


# ── the FIT reader ──────────────────────────────────────────────────────────

def test_the_fit_reader_reads_laps_and_session_and_survives_damage():
    data = fx.coros_fit()
    decoded = fit.decode(data)
    assert decoded["session"]["sport"] == 1 and decoded["session"]["sub_sport"] == 3
    assert decoded["session"]["distance"] == 12034.5 and decoded["session"]["ascent"] == 655
    assert [lap["avg_hr"] for lap in decoded["laps"]] == [145, 157] and "ascent" not in decoded["laps"][1]  # invalid
    assert fit.sport_type(decoded["session"]) == "TrailRun" and not fit.indoor(decoded["session"])
    with pytest.raises(fit.FitError):
        fit.decode(b"not a fit file at all")
    cut = fit.decode(data[: len(data) - 30])  # cut inside the session: the laps read before stay
    assert len(cut["laps"]) == 2 and cut["session"] is None


# ── pages, the app, privacy, deletion, export ───────────────────────────────

async def test_reglages_offer_connect_then_show_the_link_and_disconnect_it(as_user, db_session, test_user, garmin_on,
                                                                          coros_on, fake):
    page = (await as_user.get("/settings")).text
    assert 'id="official-garmin"' in page and 'href="/connect/garmin"' in page and "Connecter Garmin" in page
    assert 'href="/connect/coros"' in page and "Connecter COROS" in page
    conn = await _link(db_session, test_user)
    conn.last_push_at = datetime.now(timezone.utc)
    page = (await as_user.get("/settings")).text
    assert "Dernières données reçues" in page and 'action="/connect/garmin/disconnect"' in page
    r = await as_user.post("/connect/garmin/disconnect")
    assert r.status_code == 303 and r.headers["location"] == "/settings#official-garmin"
    assert fake.deregistered == ["Bearer at-1"]  # Garmin's program: the registration deleted
    assert (await db_session.execute(select(OfficialConnection))).scalar_one_or_none() is None
    assert "Garmin déconnecté" in (await as_user.get("/settings")).text


async def test_the_privacy_page_names_a_connector_only_while_it_is_active(client, garmin_on):
    page = (await client.get("/privacy")).text
    assert "Garmin</strong> (Garmin Ltd." in page and "connexion officielle" in page
    assert "COROS</strong> (COROS Wearables Inc.)" not in page


async def test_the_app_uses_the_official_flow_while_it_is_active(client, db_session, test_user, garmin_on, coros_on,
                                                                 no_commit):
    from tests.test_mobile import login

    login(client, test_user)
    client.headers["Accept"] = "application/json"
    nonce = "c" * 64
    payload = (await client.get(f"/mobile/connections?nonce={nonce}")).json()
    assert payload["official"] == {"coros": True, "garmin": True} and payload["providers"]["garmin"] == "disconnected"
    r = await client.post(f"/mobile/connections?nonce={nonce}&provider=garmin&action=link")
    assert r.status_code == 200 and "/mobile/oauth/garmin#" in r.json()["link"]
    assert (await client.get("/mobile/oauth/garmin")).status_code == 200
    page = (await client.get("/mobile/oauth/coros/confirm")).text
    assert "/connect/coros?next=app" in page
    page = (await client.get("/mobile/oauth/garmin/confirm")).text
    assert "/connect/garmin?next=app" in page
    conn = await _link(db_session, test_user)
    conn.last_push_at = datetime.now(timezone.utc)
    payload = (await client.get(f"/mobile/connections?nonce={nonce}")).json()
    assert payload["providers"]["garmin"] == "connected" and payload["details"]["garmin"]["last_sync"]
    r = await client.post(f"/mobile/connections?nonce={nonce}&provider=garmin&action=sync")
    assert r.json()["result"] == "Garmin envoie tes données dès que ta montre se synchronise."


async def test_the_export_lists_the_links_never_their_tokens(as_user, db_session, test_user, garmin_on):
    import io
    import zipfile

    await _link(db_session, test_user, access="secret-access", refresh="secret-refresh", scopes="ACTIVITY_EXPORT")
    r = await as_user.get("/settings/export")
    archive = zipfile.ZipFile(io.BytesIO(r.content))
    files = {name: archive.read(name) for name in archive.namelist()}
    links = json.loads(files["connexions.json"])["connexions_officielles"]
    assert links[0]["provider"] == "garmin" and links[0]["provider_user_id"] == fx.GARMIN_USER
    assert links[0]["scopes"] == "ACTIVITY_EXPORT" and "access_token_encrypted" not in links[0]
    everything = b"".join(files.values())
    assert b"secret-access" not in everything and b"secret-refresh" not in everything


async def test_the_worker_runs_a_push_on_its_own_session(db_session, test_user, garmin_on, no_commit):
    from app.tasks import official_connectors as tasks

    conn = await _link(db_session, test_user)

    @asynccontextmanager
    async def session():
        yield db_session
    with patch("app.database.get_task_session", session):
        waiting = await tasks._garmin_push("hrv", [{"conn": conn.id, "item": fx.garmin_hrv()}])
        assert [w["metric"] for w in waiting] == ["hrv"]
        await garmin_connect.process(db_session, "sleeps", [{"conn": conn.id, "item": fx.garmin_sleep()}])
        w = waiting[0]
        assert await tasks._garmin_night(w["conn"], w["day"], "hrv", w["readings"], w["fallback"], False) == "stored"


def test_a_waiting_night_is_queued_again_until_its_last_try_and_nothing_runs_once_off(garmin_on, queued,
                                                                                     monkeypatch):
    from app.tasks import official_connectors as tasks

    waiting = [{"conn": 7, "day": "2026-11-14", "metric": "hrv", "readings": [["2026-11-14T01:00:00", 40]],
                "fallback": None}]

    async def push(*a):
        return waiting
    outcomes = iter(["waiting", "stored"])

    async def night(*a):
        return next(outcomes)
    monkeypatch.setattr(tasks, "_garmin_push", push)
    monkeypatch.setattr(tasks, "_garmin_night", night)
    tasks.garmin_push("hrv", [])
    assert queued["night"] == [[7, "2026-11-14", "hrv", waiting[0]["readings"], None, 1]]
    tasks.garmin_night(7, "2026-11-14", "hrv", [], None, 1)  # still waiting: once more, the next try
    assert queued["night"][-1][-1] == 2
    tasks.garmin_night(7, "2026-11-14", "hrv", [], None, 2)  # stored: done
    assert len(queued["night"]) == 2
    monkeypatch.setattr(settings, "GARMIN_CONNECT_ENABLED", False)
    tasks.garmin_push("hrv", [])
    assert len(queued["night"]) == 2


def test_migration_round_trips():
    from alembic.config import Config
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from alembic.script import ScriptDirectory

    spec = importlib.util.spec_from_file_location("mig_official", MIG)
    mig = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mig)
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    script = ScriptDirectory.from_config(cfg)
    assert mig.revision in {r.revision for r in script.walk_revisions()} and len(script.get_heads()) == 1
    eng = sa.create_engine("sqlite://")
    with eng.begin() as c:
        c.execute(sa.text("CREATE TABLE users (id INTEGER PRIMARY KEY, email VARCHAR(255))"))
        c.execute(sa.text("INSERT INTO users (id, email) VALUES (1, 'a@b.c'), (2, 'd@e.f')"))
        with Operations.context(MigrationContext.configure(c)):
            mig.upgrade()
        cols = {col["name"] for col in sa.inspect(c).get_columns("official_connections")}
        assert {"provider", "provider_user_id", "access_token_encrypted", "refresh_token_encrypted", "expires_at",
                "scopes", "status", "last_push_at", "last_error"} <= cols
        row = ("INSERT INTO official_connections (user_id, provider, provider_user_id, access_token_encrypted, "
               "status, created_at, updated_at) VALUES ({}, '{}', '{}', x'00', 'active', '2026-11-15', '2026-11-15')")
        c.execute(sa.text(row.format(1, "garmin", "g-1")))
        c.execute(sa.text(row.format(1, "coros", "c-1")))
        for dup in (row.format(1, "garmin", "g-2"), row.format(2, "garmin", "g-1")):
            # one link per athlete and provider, one PaceForge account per provider account
            with pytest.raises(sa.exc.IntegrityError), c.begin_nested():
                c.execute(sa.text(dup))
        with Operations.context(MigrationContext.configure(c)):
            mig.downgrade()
        assert "official_connections" not in sa.inspect(c).get_table_names()


def test_the_url_overrides_take_https_urls_of_known_endpoints_only(monkeypatch):
    monkeypatch.setattr(settings, "GARMIN_CONNECT_URLS", json.dumps({
        "user_id": "https://apis.garmin.com/partner-gateway/rest/user/id", "token": "http://insecure", "x": "https://y"}))
    urls = garmin_connect.urls()
    assert urls["user_id"] == "https://apis.garmin.com/partner-gateway/rest/user/id"
    assert urls["token"] == GARMIN_TOKEN and "x" not in urls
    monkeypatch.setattr(settings, "COROS_PARTNER_URLS", "not json")
    assert coros_partner.urls()["workouts"] == "https://open.coros.com/v2/coros/sport/list"
    monkeypatch.setattr(settings, "COROS_PARTNER_BASE_URL", "https://opentest.coros.com/")
    assert coros_partner.urls()["token"] == "https://opentest.coros.com/oauth2/accesstoken"


def test_dates_of_a_garmin_day_are_the_athletes():
    rows = garmin_connect.daily_rows(fx.garmin_daily(timeOffsetHeartRateSamples={}, averageStressLevel=-1, steps=0))
    assert rows == []  # not worn: no steps, no day HR, Garmin's « not enough data » stress
    assert garmin_connect.body_rows(fx.garmin_body(weightInGrams=500_000, bodyFatInPercent=None)) == []
    assert date.fromisoformat("2026-11-14") == garmin_connect.parse_sleep(fx.garmin_sleep())["day"]
