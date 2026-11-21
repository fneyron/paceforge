"""The official Wahoo Cloud API and Polar AccessLink v3 connectors (app.connectors, docs/official-connectors.md): off,
nothing shows, runs or calls out; on, the OAuth flows (state, every error, no consent to health data asked: they
bring activities only), Wahoo's tokens refreshed with rotation, Polar's user registered (409 included), the webhooks
checked (Wahoo's token, Polar's HMAC signature) and queued, the workouts and exercises mapped to one Activity each
(source « wahoo » / « polar », the FIT file's values first), disconnecting at the provider, the pages, the privacy
page, the schedule, the migration.

The provider payloads below are ASSUMED shapes (the providers' public documentation, not a real account's
answers), labelled so: replace them with real ones once a first workout and exercise have been logged."""
import base64
import hashlib
import hmac
import importlib.util
import json
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
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
from app.connectors import base, polar_accesslink, wahoo_cloud
from app.crypto import decrypt_secret, encrypt_secret
from app.dependencies import get_current_user
from app.middleware.body_limit import DEFAULT_LIMIT, body_limit
from app.models import Activity, OfficialConnection, User
from tests import official_connector_fixtures as fx

ROOT = Path(__file__).resolve().parents[1]
MIG = ROOT / "alembic/versions/l3a4b5c6d7e8_official_wahoo_polar_activities.py"
WAHOO = "https://api.wahooligan.com"
POLAR_TOKEN = "https://polarremote.com/v2/oauth2/token"
POLAR_API = "https://www.polaraccesslink.com/v3"
WAHOO_USER, POLAR_USER = "60462", "475"
WAHOO_FIT = "https://cdn.wahooligan.com/wahoo-cloud/production/uploads/workout_file/file/x/2001.fit"
START = fx.ts(2026, 11, 14, 7, 30)


# ── assumed payloads (the providers' public documentation) ──────────────────

def wahoo_workout(**over) -> dict:
    """A workout of GET /v1/workouts (ASSUMED shape: Wahoo's public field names, its numbers as strings)."""
    w = {"id": 2001, "starts": "2026-11-14T07:30:00.000Z", "minutes": 75, "name": "Sortie longue",
         "workout_token": "tok", "workout_type_id": 1, "created_at": "2026-11-14T09:00:00.000Z",
         "workout_summary": {"id": 9001, "distance_accum": "12000.0", "duration_active_accum": "4500.0",
                             "duration_total_accum": "4700.0", "heart_rate_avg": "150.0", "ascent_accum": "300.0",
                             "calories_accum": "800.0", "power_avg": None, "file": {"url": WAHOO_FIT}}}
    return {**w, **over}


def wahoo_event(**over) -> dict:
    """A webhook POST (ASSUMED shape): the summary, its workout inside."""
    w = wahoo_workout()
    summary = {**w.pop("workout_summary"), "workout": w}
    return {"event_type": "workout_summary", "webhook_token": "wahoo-hook", "user": {"id": int(WAHOO_USER)},
            "workout_summary": summary, **over}


def polar_exercise(**over) -> dict:
    """An exercise of GET /v3/exercises (ASSUMED shape: AccessLink's public example's field names)."""
    e = {"id": "2AC312F", "upload_time": "2026-11-14T10:00:00.000Z", "polar_user": f"{POLAR_API}/users/{POLAR_USER}",
         "device": "Polar Vantage V3", "start_time": "2026-11-14T08:30:00", "start_time_utc_offset": 60,
         "duration": "PT1H15M20.5S", "calories": 820, "distance": 12100.0,
         "heart_rate": {"average": 148, "maximum": 175}, "sport": "RUNNING", "detailed_sport_info": "TRAIL_RUNNING",
         "has_route": True, "training_load": 120.0}
    return {**e, **over}


def run_fit(start: int = START + 5, sport: int = 1, sub_sport: int = 0) -> bytes:
    return fx.build_fit(
        {"start": start, "lat": 45.19, "lng": 5.72, "sport": sport, "sub_sport": sub_sport, "elapsed": 4710,
         "timer": 4520, "distance": 12034.5, "avg_hr": 151, "max_hr": 182, "ascent": 310},
        [{"start": start, "elapsed": 2400, "timer": 2300, "distance": 6012.3, "avg_hr": 145, "max_hr": 170},
         {"start": start + 2400, "elapsed": 2310, "timer": 2220, "distance": 6022.2, "avg_hr": 157, "max_hr": 182}])


# ── a fake of both providers ────────────────────────────────────────────────

class FakeProviders:
    def __init__(self):
        self.calls: list[httpx.Request] = []
        self.token_calls: list[dict] = []
        self.n = 1
        self.token_error: str | None = None
        self.refresh_error: str | None = None
        self.register_status = 200
        self.registered: list[dict] = []
        self.deleted: list[str] = []
        self.workouts: list[dict] = []
        self.exercises: list[dict] = [polar_exercise()]
        self.fit = run_fit()

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        url = str(request.url).split("?")[0]
        form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()} if request.method == "POST" else {}
        bearer = request.headers.get("authorization", "")
        if url == f"{WAHOO}/oauth/token":
            self.token_calls.append(form)
            assert form["client_id"] == "wahoo-client" and form["client_secret"] == "wahoo-secret"
            if form["grant_type"] == "authorization_code":
                assert form["redirect_uri"].endswith("/connect/wahoo/callback") and "code_verifier" not in form
                if self.token_error:
                    return httpx.Response(400, json={"error": self.token_error})
            elif self.refresh_error:
                return httpx.Response(400, json={"error": self.refresh_error})
            self.n += 1
            return httpx.Response(200, json={"access_token": f"at-{self.n}", "refresh_token": f"rt-{self.n}",
                                             "expires_in": 7200, "token_type": "Bearer",
                                             "scope": "user_read workouts_read offline_data"})
        if url == f"{WAHOO}/v1/user":
            assert bearer.startswith("Bearer at-")
            return httpx.Response(200, json={"id": int(WAHOO_USER), "email": "athlete@example.com"})
        if url == f"{WAHOO}/v1/workouts":
            assert bearer.startswith("Bearer at-") and request.url.params.get("per_page") == "30"
            page = int(request.url.params.get("page", "1"))
            return httpx.Response(200, json={"workouts": self.workouts if page == 1 else [], "total": 1})
        if url == f"{WAHOO}/v1/permissions" and request.method == "DELETE":
            self.deleted.append(f"wahoo {bearer}")
            return httpx.Response(200, json={"success": True})
        if url == WAHOO_FIT:
            return httpx.Response(200, content=self.fit)
        if url == POLAR_TOKEN:
            self.token_calls.append({**form, "authorization": request.headers.get("authorization")})
            if self.token_error:
                return httpx.Response(400, json={"error": self.token_error})
            return httpx.Response(200, json={"access_token": "polar-at", "token_type": "bearer",
                                             "expires_in": 315359999, "x_user_id": int(POLAR_USER)})
        if url == f"{POLAR_API}/users" and request.method == "POST":
            assert bearer == "Bearer polar-at"
            self.registered.append(json.loads(request.content))
            return httpx.Response(self.register_status, json={"polar-user-id": int(POLAR_USER)})
        if url == f"{POLAR_API}/users/{POLAR_USER}" and request.method == "DELETE":
            self.deleted.append(f"polar {bearer}")
            return httpx.Response(204)
        if url == f"{POLAR_API}/exercises":
            assert bearer == "Bearer polar-at"
            return httpx.Response(200, json=self.exercises)
        for e in self.exercises:
            if url == f"{POLAR_API}/exercises/{e['id']}":
                return httpx.Response(200, json=e)
            if url == f"{POLAR_API}/exercises/{e['id']}/fit":
                return httpx.Response(200, content=self.fit)
        if url == f"{POLAR_API}/webhooks" and request.method == "POST":
            body = json.loads(request.content)
            return httpx.Response(201, json={"data": {"id": "abdf33", "events": body["events"], "url": body["url"],
                                                      "signature_secret_key": "the-secret-key"}})
        return httpx.Response(404)


@pytest.fixture
def fake(monkeypatch) -> FakeProviders:
    f = FakeProviders()
    monkeypatch.setattr(base, "_transport", httpx.MockTransport(f.handler))
    return f


@pytest.fixture
def wahoo_on(monkeypatch):
    monkeypatch.setattr(settings, "WAHOO_CLOUD_ENABLED", True)
    monkeypatch.setattr(settings, "WAHOO_CLOUD_CLIENT_ID", "wahoo-client")
    monkeypatch.setattr(settings, "WAHOO_CLOUD_CLIENT_SECRET", "wahoo-secret")
    monkeypatch.setattr(settings, "WAHOO_CLOUD_WEBHOOK_TOKEN", "wahoo-hook")


@pytest.fixture
def polar_on(monkeypatch):
    monkeypatch.setattr(settings, "POLAR_ACCESSLINK_ENABLED", True)
    monkeypatch.setattr(settings, "POLAR_ACCESSLINK_CLIENT_ID", "polar-client")
    monkeypatch.setattr(settings, "POLAR_ACCESSLINK_CLIENT_SECRET", "polar-secret")
    monkeypatch.setattr(settings, "POLAR_ACCESSLINK_WEBHOOK_SECRET", "polar-hook-key")


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

    jobs: dict[str, list] = {"wahoo": [], "polar": [], "wahoo_sync": [], "polar_sync": []}
    for name, task in (("wahoo", tasks.wahoo_push), ("polar", tasks.polar_push), ("wahoo_sync", tasks.wahoo_sync),
                       ("polar_sync", tasks.polar_sync)):
        monkeypatch.setattr(task, "apply_async", lambda args, *a, _n=name, **k: jobs[_n].append(args))
    return jobs


def _q(location: str) -> dict:
    return {k: v[0] for k, v in parse_qs(urlsplit(location).query).items()}


async def _link(db, user: User, provider: str = "wahoo", access: str | None = None, refresh: str | None = "rt-1",
                expires_in=timedelta(hours=1), **extra) -> OfficialConnection:
    polar = provider == "polar"
    conn = OfficialConnection(
        user_id=user.id, provider=provider, provider_user_id=POLAR_USER if polar else WAHOO_USER,
        access_token_encrypted=encrypt_secret(access or ("polar-at" if polar else "at-1")),
        refresh_token_encrypted=None if polar or refresh is None else encrypt_secret(refresh),
        expires_at=datetime.now(timezone.utc) + (timedelta(days=3000) if polar else expires_in), **extra)
    db.add(conn)
    await db.flush()
    return conn


def _signed(body: dict, key: str = "polar-hook-key") -> tuple[bytes, dict]:
    raw = json.dumps(body).encode()
    sig = hmac.new(key.encode(), raw, hashlib.sha256).hexdigest()
    return raw, {"content-type": "application/json", "Polar-Webhook-Signature": sig}


# ── off: nothing shows, runs or calls out ───────────────────────────────────

ROUTES = (("GET", "/connect/wahoo"), ("GET", "/connect/polar"), ("GET", "/connect/wahoo/callback?code=c&state=s"),
          ("GET", "/connect/polar/callback?code=c&state=s"), ("POST", "/connect/wahoo/disconnect"),
          ("POST", "/connect/polar/disconnect"), ("POST", "/webhook/wahoo"), ("POST", "/webhook/polar"))


async def test_off_every_route_answers_404_and_nothing_is_scheduled_shown_or_called(as_user, monkeypatch):
    def no_call(request):  # pragma: no cover - the point is that it never runs
        raise AssertionError(f"called {request.url}")
    monkeypatch.setattr(base, "_transport", httpx.MockTransport(no_call))
    # the webhook secrets alone switch nothing on
    monkeypatch.setattr(settings, "WAHOO_CLOUD_WEBHOOK_TOKEN", "wahoo-hook")
    monkeypatch.setattr(settings, "POLAR_ACCESSLINK_WEBHOOK_SECRET", "polar-hook-key")
    assert not connectors.ready("wahoo") and not connectors.ready("polar")
    assert not connectors.active_providers() and connectors.beat_schedule() == {}
    for method, path in ROUTES:
        kw = {"content": json.dumps(wahoo_event()).encode(), "headers": {"content-type": "application/json"}}
        r = await as_user.request(method, path, **(kw if method == "POST" else {}))
        assert r.status_code == 404, (method, path, r.status_code)
    assert body_limit("/webhook/wahoo") == DEFAULT_LIMIT == body_limit("/webhook/polar")
    page = (await as_user.get("/settings")).text
    assert 'id="official-wahoo"' not in page and 'id="official-polar"' not in page and "/connect/" not in page
    privacy = (await as_user.get("/privacy")).text
    assert "Wahoo" not in privacy and "Polar" not in privacy


async def test_enabled_without_its_credentials_is_still_off(as_user, monkeypatch):
    monkeypatch.setattr(settings, "WAHOO_CLOUD_ENABLED", True)
    monkeypatch.setattr(settings, "WAHOO_CLOUD_CLIENT_ID", "wahoo-client")
    monkeypatch.setattr(settings, "POLAR_ACCESSLINK_ENABLED", True)
    monkeypatch.setattr(settings, "POLAR_ACCESSLINK_CLIENT_SECRET", "polar-secret")
    assert not settings.wahoo_cloud_ready and not settings.polar_accesslink_ready
    assert (await as_user.get("/connect/wahoo")).status_code == 404
    assert (await as_user.get("/connect/polar")).status_code == 404
    monkeypatch.setattr(settings, "WAHOO_CLOUD_CLIENT_SECRET", "wahoo-secret")
    assert connectors.active_providers() == ["wahoo"]
    assert connectors.beat_schedule()["official-connectors"]["task"] == "paceforge.official_round"


async def test_the_pilot_list_limits_who_sees_and_starts_them(as_user, test_user, wahoo_on, polar_on, monkeypatch):
    monkeypatch.setattr(settings, "OFFICIAL_CONNECTORS_PILOT_USERS", f"{test_user.id + 1000}")
    for provider in ("wahoo", "polar"):
        assert (await as_user.get(f"/connect/{provider}")).status_code == 404
    monkeypatch.setattr(settings, "OFFICIAL_CONNECTORS_PILOT_USERS", f"{test_user.id}")
    for provider in ("wahoo", "polar"):
        assert (await as_user.get(f"/connect/{provider}")).status_code == 302


# ── Wahoo OAuth, tokens ─────────────────────────────────────────────────────

async def test_wahoo_connect_needs_no_health_consent_and_redirects_with_scope_and_state(
        client, db_session, wahoo_on, fake, no_commit):
    newcomer = User(email="new@x.fr", email_verified=True)  # no consent to health data: activities only
    db_session.add(newcomer)
    await db_session.flush()
    client._transport.app.dependency_overrides[get_current_user] = lambda: newcomer  # type: ignore[attr-defined]
    async with AsyncClient(transport=client._transport, base_url="https://test") as c:
        r = await c.get("/connect/wahoo")
    assert r.status_code == 302
    loc, q = urlsplit(r.headers["location"]), _q(r.headers["location"])
    assert f"{loc.scheme}://{loc.netloc}{loc.path}" == "https://api.wahooligan.com/oauth/authorize"
    assert q["client_id"] == "wahoo-client" and q["response_type"] == "code" and len(q["state"]) >= 24
    assert q["scope"] == "user_read workouts_read offline_data" and "code_challenge" not in q
    assert q["redirect_uri"] == f"{settings.BASE_URL.rstrip('/')}/connect/wahoo/callback"
    assert not fake.calls


async def test_wahoo_callback_stores_the_link_encrypted_and_reads_the_recent_workouts(
        as_user, db_session, test_user, wahoo_on, fake, queued):
    state = _q((await as_user.get("/connect/wahoo")).headers["location"])["state"]
    r = await as_user.get(f"/connect/wahoo/callback?code=the-code&state={state}")
    assert r.status_code == 303 and r.headers["location"] == "/settings#official-wahoo"
    conn = (await db_session.execute(select(OfficialConnection))).scalar_one()
    assert conn.provider == "wahoo" and conn.provider_user_id == WAHOO_USER and conn.user_id == test_user.id
    assert b"at-2" not in conn.access_token_encrypted and decrypt_secret(conn.access_token_encrypted) == "at-2"
    assert decrypt_secret(conn.refresh_token_encrypted) == "rt-2"
    assert conn.scopes == "user_read workouts_read offline_data"
    assert timedelta(minutes=110) < base.as_utc(conn.expires_at) - datetime.now(timezone.utc) <= timedelta(hours=2)
    assert fake.token_calls[0]["grant_type"] == "authorization_code" and fake.token_calls[0]["code"] == "the-code"
    assert queued["wahoo_sync"] == [[conn.id, True]]  # the backfill, in the worker
    page = (await as_user.get("/settings")).text
    assert "Wahoo connecté" in page and 'action="/connect/wahoo/disconnect"' in page
    # the state is single use; a forged one, a refusal, a failed exchange: no link, nothing more asked
    await as_user.get(f"/connect/wahoo/callback?code=the-code&state={state}")
    assert "expiré" in (await as_user.get("/settings")).text and len(fake.token_calls) == 1


async def test_wahoo_callback_errors(as_user, db_session, wahoo_on, fake, queued):
    state = _q((await as_user.get("/connect/wahoo")).headers["location"])["state"]
    await as_user.get(f"/connect/wahoo/callback?error=access_denied&state={state}")
    assert "Connexion à Wahoo annulée" in (await as_user.get("/settings")).text
    fake.token_error = "invalid_grant"
    state = _q((await as_user.get("/connect/wahoo")).headers["location"])["state"]
    await as_user.get(f"/connect/wahoo/callback?code=bad&state={state}")
    assert "La connexion à Wahoo a échoué" in (await as_user.get("/settings")).text
    assert (await db_session.execute(select(func.count(OfficialConnection.id)))).scalar() == 0
    assert not queued["wahoo_sync"]


async def test_wahoo_refresh_rotates_and_survives_a_later_rollback(committing_session, wahoo_on, fake):
    db = committing_session
    user = User(email="r@x.fr")
    db.add(user)
    await db.flush()
    conn = await _link(db, user, expires_in=timedelta(minutes=5))  # within Wahoo's margin (10 min)
    await db.commit()
    async with base.http_client() as client:
        assert await base.access_token(db, conn, client) == "at-2"
        assert fake.token_calls[-1] == {"client_id": "wahoo-client", "client_secret": "wahoo-secret",
                                        "grant_type": "refresh_token", "refresh_token": "rt-1"}
        assert await base.access_token(db, conn, client, rejected="at-1") == "at-2"  # rotated meanwhile
        assert len(fake.token_calls) == 1
        assert await base.access_token(db, conn, client, rejected="at-2") == "at-3"
        assert fake.token_calls[-1]["refresh_token"] == "rt-2"  # the rotated one, never the dead one
    db.add(User(email="later@x.fr"))
    await db.rollback()
    await db.refresh(conn)
    assert decrypt_secret(conn.refresh_token_encrypted) == "rt-3"


async def test_a_refused_wahoo_refresh_asks_to_reconnect(db_session, test_user, wahoo_on, fake, no_commit):
    conn = await _link(db_session, test_user, expires_in=timedelta(hours=-1))
    fake.refresh_error = "invalid_grant"
    async with base.http_client() as client:
        with pytest.raises(base.ConnectorAuthError):
            await base.access_token(db_session, conn, client)
    assert conn.status == "reauth" and "Wahoo" in conn.last_error
    assert (await base.status(db_session, test_user.id, "wahoo"))["needs_reauth"]


# ── Polar OAuth, registration ───────────────────────────────────────────────

async def test_polar_connect_redirects_and_the_callback_registers_the_user(as_user, db_session, test_user, polar_on,
                                                                          fake, queued):
    r = await as_user.get("/connect/polar")
    loc, q = urlsplit(r.headers["location"]), _q(r.headers["location"])
    assert f"{loc.scheme}://{loc.netloc}{loc.path}" == "https://flow.polar.com/oauth2/authorization"
    assert q["response_type"] == "code" and q["client_id"] == "polar-client" and q["scope"] == "accesslink.read_all"
    assert q["redirect_uri"] == f"{settings.BASE_URL.rstrip('/')}/connect/polar/callback" and not fake.calls
    r = await as_user.get(f"/connect/polar/callback?code=the-code&state={q['state']}")
    assert r.status_code == 303 and r.headers["location"] == "/settings#official-polar"
    token_call = fake.token_calls[0]
    assert token_call["authorization"] == "Basic " + base64.b64encode(b"polar-client:polar-secret").decode()
    assert token_call["grant_type"] == "authorization_code" and token_call["code"] == "the-code"
    assert "client_secret" not in token_call  # in HTTP Basic only
    conn = (await db_session.execute(select(OfficialConnection))).scalar_one()
    assert conn.provider == "polar" and conn.provider_user_id == POLAR_USER and conn.refresh_token_encrypted is None
    assert decrypt_secret(conn.access_token_encrypted) == "polar-at"
    assert base.as_utc(conn.expires_at) > datetime.now(timezone.utc) + timedelta(days=3000)
    (registered,) = fake.registered
    member = registered["member-id"]
    assert member == polar_accesslink.member_id(test_user.id) and "@" not in member
    assert str(test_user.id) != member and len(member) >= 16  # opaque, derived from the account id
    assert queued["polar_sync"] == [[conn.id, True]]
    assert "Polar connecté" in (await as_user.get("/settings")).text


async def test_a_polar_user_already_registered_is_fine_another_refusal_is_not(as_user, db_session, polar_on, fake,
                                                                             queued):
    fake.register_status = 409
    state = _q((await as_user.get("/connect/polar")).headers["location"])["state"]
    await as_user.get(f"/connect/polar/callback?code=c&state={state}")
    assert (await db_session.execute(select(func.count(OfficialConnection.id)))).scalar() == 1
    await db_session.execute(sa.delete(OfficialConnection))
    fake.register_status = 500
    state = _q((await as_user.get("/connect/polar")).headers["location"])["state"]
    await as_user.get(f"/connect/polar/callback?code=c&state={state}")
    assert "Polar ne répond pas" in (await as_user.get("/settings")).text
    assert (await db_session.execute(select(func.count(OfficialConnection.id)))).scalar() == 0


async def test_a_polar_token_never_refreshes_it_asks_to_reconnect(db_session, test_user, polar_on, fake, no_commit):
    conn = await _link(db_session, test_user, "polar")
    conn.expires_at = datetime.now(timezone.utc) + timedelta(hours=1)  # within its margin
    await db_session.flush()
    async with base.http_client() as client:
        with pytest.raises(base.ConnectorAuthError):
            await base.access_token(db_session, conn, client)
    assert conn.status == "reauth" and not fake.token_calls


# ── webhooks ────────────────────────────────────────────────────────────────

def _wahoo_post(c: AsyncClient, body):
    return c.post("/webhook/wahoo", content=json.dumps(body).encode(), headers={"content-type": "application/json"})


async def test_wahoo_pushes_need_the_webhook_token_and_known_users(as_user, db_session, test_user, wahoo_on, queued,
                                                                   monkeypatch):
    conn = await _link(db_session, test_user)
    assert (await _wahoo_post(as_user, wahoo_event(webhook_token="wrong"))).status_code == 401
    no_token = wahoo_event()
    del no_token["webhook_token"]
    assert (await _wahoo_post(as_user, no_token)).status_code == 401
    assert (await _wahoo_post(as_user, ["x"])).status_code == 401
    assert (await _wahoo_post(as_user, {"webhook_token": "wahoo-hook"})).status_code == 400
    assert not queued["wahoo"]
    stranger = wahoo_event(user={"id": 1})
    r = await _wahoo_post(as_user, stranger)
    assert r.status_code == 200 and r.json() == {"status": "ok"} and not queued["wahoo"]
    other = await _wahoo_post(as_user, {"event_type": "something_else", "webhook_token": "wahoo-hook"})
    assert other.status_code == 200 and not queued["wahoo"]
    r = await _wahoo_post(as_user, wahoo_event())
    assert r.status_code == 200 and r.json() == {"status": "ok"}  # the same answer: nothing tells who is known
    assert queued["wahoo"] == [[[{"conn": conn.id, "user": WAHOO_USER, "item": wahoo_event()["workout_summary"]}]]]
    monkeypatch.setattr(settings, "WAHOO_CLOUD_WEBHOOK_TOKEN", "")  # none configured: every push refused
    assert (await _wahoo_post(as_user, wahoo_event(webhook_token=""))).status_code == 401


async def test_polar_pushes_need_a_valid_signature_a_ping_is_answered(as_user, db_session, test_user, polar_on,
                                                                      queued, monkeypatch):
    conn = await _link(db_session, test_user, "polar")
    ping = await as_user.post("/webhook/polar", content=b'{"event": "PING", "timestamp": "2026-11-21T10:00:00Z"}')
    assert ping.status_code == 200  # sent when the webhook is created, before its key is known: carries nothing
    event = {"event": "EXERCISE", "user_id": int(POLAR_USER), "entity_id": "2AC312F",
             "timestamp": "2026-11-14T10:00:00Z", "url": f"{POLAR_API}/exercises/2AC312F"}
    raw, headers = _signed(event, key="another-key")
    assert (await as_user.post("/webhook/polar", content=raw, headers=headers)).status_code == 401
    raw, headers = _signed(event)
    assert (await as_user.post("/webhook/polar", content=raw + b" ", headers=headers)).status_code == 401  # altered
    assert (await as_user.post("/webhook/polar", content=raw)).status_code == 401  # unsigned
    bad, bad_headers = _signed({**event, "entity_id": "../users/1"})
    assert (await as_user.post("/webhook/polar", content=bad, headers=bad_headers)).status_code == 400
    assert not queued["polar"]
    stranger, s_headers = _signed({**event, "user_id": 1})
    assert (await as_user.post("/webhook/polar", content=stranger, headers=s_headers)).status_code == 200
    assert not queued["polar"]
    r = await as_user.post("/webhook/polar", content=raw, headers=headers)
    assert r.status_code == 200 and r.json() == {"status": "ok"}
    assert queued["polar"] == [[[{"conn": conn.id, "user": POLAR_USER, "item": {"entity_id": "2AC312F"}}]]]
    monkeypatch.setattr(settings, "POLAR_ACCESSLINK_WEBHOOK_SECRET", "")
    assert (await as_user.post("/webhook/polar", content=raw, headers=headers)).status_code == 401


async def test_a_broker_down_answers_503(as_user, db_session, test_user, wahoo_on, monkeypatch):
    from app.tasks import official_connectors as tasks

    await _link(db_session, test_user)

    def down(*a, **k):
        raise ConnectionError("broker down")
    monkeypatch.setattr(tasks.wahoo_push, "apply_async", down)
    assert (await _wahoo_post(as_user, wahoo_event())).status_code == 503


# ── processing: workouts and exercises into Activity ────────────────────────

async def test_a_wahoo_workout_reads_its_fit_file_and_stays_one_row(db_session, test_user, wahoo_on, fake, no_commit):
    conn = await _link(db_session, test_user)
    for _ in range(2):  # the webhook, then the pull: one row, the FIT file read once
        await wahoo_cloud.process_push(db_session, conn, [wahoo_event()["workout_summary"]])
    act = (await db_session.execute(select(Activity))).scalar_one()
    assert act.wahoo_activity_id == 2001 and act.sport_type == "Run" and act.user_id == test_user.id
    assert act.distance == 12034.5 and act.moving_time == 4520 and act.total_elevation_gain == 310  # the FIT's
    assert act.average_heartrate == 151 and act.max_heartrate == 182 and act.calories == 800
    assert act.laps == [{"moving_time": 2300, "average_heartrate": 145.0, "max_heartrate": 170.0, "distance": 6012.3},
                        {"moving_time": 2220, "average_heartrate": 157.0, "max_heartrate": 182.0, "distance": 6022.2}]
    assert act.raw_data["source"] == "wahoo" and act.raw_data["via"] == "wahoo_cloud_api"
    assert act.raw_data["start_latlng"] == [45.19, 5.72] and "workout_token" not in act.raw_data
    assert "file" not in act.raw_data["summary"] and conn.last_push_at is not None
    assert sum(str(c.url) == WAHOO_FIT for c in fake.calls) == 1


async def test_a_wahoo_summary_alone_is_mapped_in_strava_units(db_session, test_user, wahoo_on, fake, no_commit,
                                                               monkeypatch):
    monkeypatch.setattr(settings, "WAHOO_CLOUD_FIT_HOSTS", "elsewhere.example")  # the FIT file's host not allowed
    conn = await _link(db_session, test_user)
    fake.workouts = [wahoo_workout(), wahoo_workout(id=2002, workout_type_id=5, starts="2026-11-15T07:30:00Z"),
                     wahoo_workout(id=2003, workout_type_id=12, starts="2026-11-16T07:30:00Z"),
                     wahoo_workout(id=2004, workout_type_id=4, starts="2026-11-17T07:30:00Z"),
                     wahoo_workout(id=2005, workout_type_id=999, starts="2026-11-18T07:30:00Z"),
                     wahoo_workout(id="nope")]
    outcome = await wahoo_cloud.run_sync(db_session, conn, backfill=True)
    assert outcome["ok"] and conn.last_sync_at is not None and conn.sync_claimed_at is None
    assert not any("cdn.wahooligan.com" in str(c.url) for c in fake.calls)
    acts = {a.wahoo_activity_id: a for a in (await db_session.execute(select(Activity))).scalars()}
    assert set(acts) == {2001, 2002, 2003, 2004, 2005}
    run = acts[2001]
    assert run.sport_type == "Run" and run.distance == 12000 and run.moving_time == 4500 and run.elapsed_time == 4700
    assert run.total_elevation_gain == 300 and run.average_heartrate == 150 and run.calories == 800
    assert run.average_speed == pytest.approx(12000 / 4500) and not run.laps
    assert base.as_utc(run.start_date) == datetime(2026, 11, 14, 7, 30, tzinfo=timezone.utc)
    assert acts[2002].sport_type == "Run" and acts[2002].raw_data["trainer"] is True  # treadmill
    assert acts[2003].sport_type == "VirtualRide" and acts[2004].sport_type == "TrailRun"
    assert acts[2005].sport_type == "Workout"  # an unknown type is still imported


async def test_a_session_strava_brought_is_never_duplicated(db_session, test_user, wahoo_on, fake, no_commit):
    conn = await _link(db_session, test_user)
    strava = Activity(user_id=test_user.id, strava_activity_id=42, sport_type="Run", name="Strava",
                      start_date=datetime(2026, 11, 14, 7, 30, 40, tzinfo=timezone.utc), distance=12010,
                      moving_time=4510, elapsed_time=4700, raw_data={})
    db_session.add(strava)
    await db_session.flush()
    await wahoo_cloud.process_push(db_session, conn, [wahoo_event()["workout_summary"]])
    assert (await db_session.execute(select(func.count(Activity.id)))).scalar() == 1
    assert strava.wahoo_activity_id == 2001 and strava.name == "Strava" and strava.laps is None  # Strava's stay
    assert not any(str(c.url) == WAHOO_FIT for c in fake.calls)  # Strava's own details stand


async def test_a_polar_push_reads_the_exercise_and_its_fit_file(db_session, test_user, polar_on, fake, no_commit):
    conn = await _link(db_session, test_user, "polar")
    fake.fit = run_fit(start=fx.ts(2026, 11, 14, 7, 30, 5), sport=1, sub_sport=3)
    for _ in range(2):
        await polar_accesslink.process_push(db_session, conn, [{"entity_id": "2AC312F"}])
    act = (await db_session.execute(select(Activity))).scalar_one()
    assert act.polar_activity_id == "2AC312F" and act.sport_type == "TrailRun" and act.user_id == test_user.id
    assert act.distance == 12034.5 and act.moving_time == 4520 and len(act.laps) == 2  # the FIT's
    assert act.raw_data["source"] == "polar" and act.raw_data["via"] == "polar_accesslink_api"
    assert act.raw_data["utc_offset"] == 3600 and "polar_user" not in act.raw_data
    assert conn.last_push_at is not None
    fits = [c for c in fake.calls if str(c.url).endswith("/fit")]
    assert len(fits) == 1 and fits[0].headers["authorization"] == "Bearer polar-at"
    # a push naming an id that is not one never reaches Polar
    fake.calls.clear()
    await polar_accesslink.process_push(db_session, conn, [{"entity_id": "../users"}])
    assert not fake.calls[1:] and not any("users" in str(c.url) for c in fake.calls)


async def test_a_polar_exercise_alone_is_mapped_in_strava_units(db_session, test_user, polar_on, fake, no_commit):
    conn = await _link(db_session, test_user, "polar")
    fake.fit = b"not a fit file"
    fake.exercises = [polar_exercise(),
                      polar_exercise(id="B1", detailed_sport_info="TREADMILL_RUNNING", start_time="2026-11-15T08:30:00"),
                      polar_exercise(id="B2", sport="CYCLING", detailed_sport_info="ROAD_BIKING",
                                     start_time="2026-11-16T08:30:00", heart_rate={"average": 300}),
                      polar_exercise(id="B3", sport="OTHER", detailed_sport_info="FUNCTIONAL_TRAINING",
                                     start_time="2026-11-17T08:30:00", distance=None),
                      polar_exercise(id="B4", duration="nonsense")]
    outcome = await polar_accesslink.run_sync(db_session, conn, backfill=True)
    assert outcome["ok"]
    acts = {a.polar_activity_id: a for a in (await db_session.execute(select(Activity))).scalars()}
    assert set(acts) == {"2AC312F", "B1", "B2", "B3"}
    trail = acts["2AC312F"]
    assert trail.sport_type == "TrailRun" and trail.distance == 12100 and trail.moving_time == 4520
    assert base.as_utc(trail.start_date) == datetime(2026, 11, 14, 7, 30, tzinfo=timezone.utc)  # 08:30 at UTC+1
    assert trail.average_heartrate == 148 and trail.max_heartrate == 175 and trail.calories == 820
    assert acts["B1"].sport_type == "Run" and acts["B1"].raw_data["trainer"] is True
    assert acts["B2"].sport_type == "Ride" and acts["B2"].average_heartrate is None  # implausible: left out
    assert acts["B3"].sport_type == "Workout" and acts["B3"].distance == 0
    assert polar_accesslink.duration_s("PT2H44M45S") == 9885 and polar_accesslink.duration_s("P1D") == 86400
    assert polar_accesslink.duration_s("1:02:03") is None


async def test_the_worker_runs_a_push_on_its_own_session_and_never_trusts_a_mismatch(db_session, test_user, wahoo_on,
                                                                                     fake, no_commit):
    from app.tasks import official_connectors as tasks

    conn = await _link(db_session, test_user)

    @asynccontextmanager
    async def session():
        yield db_session
    item = wahoo_event()["workout_summary"]
    with patch("app.database.get_task_session", session):
        await tasks._push("wahoo", [{"conn": conn.id, "user": "someone-else", "item": item}])
        assert (await db_session.execute(select(func.count(Activity.id)))).scalar() == 0
        await tasks._push("wahoo", [{"conn": conn.id, "user": WAHOO_USER, "item": item}])
        assert (await db_session.execute(select(func.count(Activity.id)))).scalar() == 1


def test_nothing_runs_once_off(wahoo_on, polar_on, monkeypatch):
    from app.tasks import official_connectors as tasks

    ran = []

    async def push(*a):
        ran.append(a)
    monkeypatch.setattr(tasks, "_push", push)
    tasks.wahoo_push([])
    tasks.polar_push([])
    assert len(ran) == 2
    monkeypatch.setattr(settings, "WAHOO_CLOUD_ENABLED", False)
    monkeypatch.setattr(settings, "POLAR_ACCESSLINK_CLIENT_SECRET", "")
    tasks.wahoo_push([])
    tasks.polar_push([])
    assert tasks.wahoo_sync(1, True) is None and tasks.polar_sync(1, True) is None
    assert len(ran) == 2


async def test_the_round_pulls_the_links_due_and_refreshes_no_wahoo_token(db_session, test_user, wahoo_on, fake,
                                                                          no_commit):
    from app.tasks import official_connectors as tasks

    conn = await _link(db_session, test_user, expires_in=timedelta(minutes=50))  # fresh: no refresh needed
    fake.workouts = [wahoo_workout()]

    @asynccontextmanager
    async def session():
        yield db_session
    with patch("app.database.get_task_session", session):
        outcome = await tasks._round()
        assert outcome == {"pulled": 1, "refreshed": 0, "failed": 0} and not fake.token_calls
        assert conn.last_sync_at is not None
        assert (await tasks._round())["pulled"] == 0  # not due again for 12 hours


# ── disconnecting, pages, privacy, consent ──────────────────────────────────

async def test_disconnecting_tells_the_provider_and_forgets_the_link(as_user, db_session, test_user, wahoo_on,
                                                                     polar_on, fake):
    await _link(db_session, test_user)
    await _link(db_session, test_user, "polar")
    page = (await as_user.get("/settings")).text
    assert 'action="/connect/wahoo/disconnect"' in page and 'action="/connect/polar/disconnect"' in page
    r = await as_user.post("/connect/wahoo/disconnect", data={"next": "devices"})
    assert r.status_code == 303 and r.headers["location"] == "/settings#official-wahoo"  # no connection page
    r = await as_user.post("/connect/polar/disconnect")
    assert r.status_code == 303 and r.headers["location"] == "/settings#official-polar"
    assert fake.deleted == ["wahoo Bearer at-1", "polar Bearer polar-at"]
    assert (await db_session.execute(select(OfficialConnection))).scalar_one_or_none() is None
    page = (await as_user.get("/settings")).text
    assert "Polar déconnecté" in page


async def test_an_expired_wahoo_token_is_refreshed_to_deregister(db_session, test_user, wahoo_on, fake, no_commit):
    conn = await _link(db_session, test_user, expires_in=timedelta(hours=-1))
    await base.disconnect(db_session, conn)
    assert fake.deleted == ["wahoo Bearer at-2"] and fake.token_calls[-1]["grant_type"] == "refresh_token"


async def test_reglages_offer_both_and_say_what_they_bring(as_user, wahoo_on, polar_on):
    page = (await as_user.get("/settings")).text
    assert 'id="official-wahoo"' in page and 'href="/connect/wahoo"' in page and "Connecter Wahoo" in page
    assert 'id="official-polar"' in page and 'href="/connect/polar"' in page and "Connecter Polar" in page
    assert "Elle remplace la connexion" not in page  # no former link to replace


async def test_the_privacy_page_names_them_only_while_active(client, wahoo_on, monkeypatch):
    page = (await client.get("/privacy")).text
    assert "Wahoo</strong> (Wahoo Fitness LLC" in page and "Activités importées de Strava, COROS, Wahoo ou Garmin" in page
    assert "Polar Electro Oy" not in page
    monkeypatch.setattr(settings, "POLAR_ACCESSLINK_ENABLED", True)
    monkeypatch.setattr(settings, "POLAR_ACCESSLINK_CLIENT_ID", "polar-client")
    monkeypatch.setattr(settings, "POLAR_ACCESSLINK_CLIENT_SECRET", "polar-secret")
    page = (await client.get("/privacy")).text
    assert "Polar</strong> (Polar Electro Oy, Finlande)" in page and "identifiant anonyme" in page


async def test_withdrawing_the_health_consent_keeps_them_as_strava(as_user, db_session, test_user, wahoo_on, polar_on,
                                                                   fake):
    await _link(db_session, test_user)
    await _link(db_session, test_user, "polar")
    r = await as_user.post("/settings/health-consent/withdraw")
    assert r.status_code == 303
    providers = sorted((await db_session.execute(select(OfficialConnection.provider))).scalars())
    assert providers == ["polar", "wahoo"] and not fake.deleted


async def test_the_polar_webhook_is_created_once_by_the_owner(polar_on, fake, capsys):
    data = await polar_accesslink.create_webhook()
    assert data["signature_secret_key"] == "the-secret-key"
    call = next(c for c in fake.calls if str(c.url) == f"{POLAR_API}/webhooks")
    assert json.loads(call.content) == {"events": ["EXERCISE"],
                                        "url": f"{settings.BASE_URL.rstrip('/')}/webhook/polar"}
    assert call.headers["authorization"] == "Basic " + base64.b64encode(b"polar-client:polar-secret").decode()


def test_the_api_names_the_new_sources():
    from app.routers.api_v1 import SOURCES

    assert ("wahoo", "wahoo_activity_id") in SOURCES and ("polar", "polar_activity_id") in SOURCES


def test_migration_round_trips():
    from alembic.config import Config
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from alembic.script import ScriptDirectory

    spec = importlib.util.spec_from_file_location("mig_wahoo_polar", MIG)
    mig = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mig)
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    script = ScriptDirectory.from_config(cfg)
    assert len(script.get_heads()) == 1 and mig.down_revision == "k2f3a4b5c6d7"
    assert mig.revision in {r.revision for r in script.walk_revisions()}
    eng = sa.create_engine("sqlite://")
    with eng.begin() as c:
        c.execute(sa.text("CREATE TABLE activities (id INTEGER PRIMARY KEY, strava_activity_id BIGINT, "
                          "garmin_activity_id BIGINT, coros_activity_id BIGINT, name VARCHAR(255))"))
        c.execute(sa.text("CREATE TABLE official_connections (id INTEGER PRIMARY KEY, provider VARCHAR(16))"))
        with Operations.context(MigrationContext.configure(c)):
            mig.upgrade()
        cols = {col["name"] for col in sa.inspect(c).get_columns("activities")}
        assert {"wahoo_activity_id", "polar_activity_id"} <= cols
        c.execute(sa.text("INSERT INTO activities (id, wahoo_activity_id, name) VALUES (1, 2001, 'w')"))
        c.execute(sa.text("INSERT INTO activities (id, polar_activity_id, strava_activity_id, name) "
                          "VALUES (2, '2AC312F', 9, 's')"))
        for dup in ("INSERT INTO activities (id, wahoo_activity_id, name) VALUES (3, 2001, 'dup')",
                    "INSERT INTO activities (id, polar_activity_id, name) VALUES (3, '2AC312F', 'dup')"):
            with pytest.raises(sa.exc.IntegrityError), c.begin_nested():
                c.execute(sa.text(dup))
        c.execute(sa.text("INSERT INTO official_connections (id, provider) VALUES (1, 'wahoo'), (2, 'coros')"))
        with Operations.context(MigrationContext.configure(c)):
            mig.downgrade()
        assert [r[0] for r in c.execute(sa.text("SELECT id FROM activities"))] == [2]  # Strava's row stays
        assert [r[0] for r in c.execute(sa.text("SELECT provider FROM official_connections"))] == ["coros"]
        assert "wahoo_activity_id" not in {col["name"] for col in sa.inspect(c).get_columns("activities")}


def test_the_url_overrides_take_https_urls_of_known_endpoints_only(monkeypatch):
    monkeypatch.setattr(settings, "WAHOO_CLOUD_URLS", json.dumps({"workouts": "https://api.wahooligan.com/v2/w",
                                                                  "token": "http://insecure"}))
    assert wahoo_cloud.urls()["workouts"] == "https://api.wahooligan.com/v2/w"
    assert wahoo_cloud.urls()["token"] == f"{WAHOO}/oauth/token"
    monkeypatch.setattr(settings, "POLAR_ACCESSLINK_URLS", "not json")
    assert polar_accesslink.urls()["exercises"] == f"{POLAR_API}/exercises"
