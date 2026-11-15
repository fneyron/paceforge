"""The personal API (/api/v1) and its keys (Réglages › Accès API): a key made, shown once, listed, revoked; the
calls refused without a valid key, past their budget, or for anyone else's data; each endpoint's shape and pages;
the health data behind the key's scope and the account's consent; the keys revoked by a password reset, emptied of
their health scope by a withdrawn consent, deleted with the account, listed without their hash by the export; the
key never written to a log or a Sentry event; the documentation page, its examples and the OpenAPI document."""
import base64
import json
import logging
import re
import time
from datetime import date, datetime, timedelta, timezone

import pytest
from httpx import AsyncClient
from itsdangerous import TimestampSigner
from sqlalchemy import func, select

from app.config import settings
from app.models.activity import Activity
from app.models.api_token import ApiToken
from app.models.health import HealthMetric
from app.models.mobile import MobileDaily
from app.models.route import Route, RouteCheckpoint
from app.models.user import User
from app.routers import api_v1
from app.routers.mobile import PhoneLimiter
from app.services import api_tokens, data_export
from app.services.health_consent import HEALTH_CONSENT_VERSION
from tests.test_race_plan_services import CPS, _course

NOW = datetime.now(timezone.utc)


def login(client: AsyncClient, user: User) -> None:
    """A session as dependencies.sign_in makes one (production cookies are Secure: over HTTPS)."""
    client.base_url = "https://test"
    value = base64.b64encode(json.dumps({"user_id": user.id, "sid": "s" * 22, "iat": int(time.time())}).encode())
    client.cookies.set("paceforge_session", TimestampSigner(settings.SECRET_KEY).sign(value).decode())


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
async def athlete(db_session, test_user) -> User:
    """The suite's athlete with a confirmed email (a key needs one) and the consent to health data (test_user's)."""
    test_user.email, test_user.email_verified = "camille@x.fr", True
    await db_session.flush()
    return test_user


@pytest.fixture
async def other(db_session) -> User:
    user = User(email="other@x.fr", email_verified=True, firstname="Autre",
                health_consent_at=NOW, health_consent_version=HEALTH_CONSENT_VERSION)
    db_session.add(user)
    await db_session.flush()
    return user


async def _key(db, user: User, health: bool = False, name: str = "Script") -> str:
    _, token = await api_tokens.create(db, user.id, name, health)
    return token


def _csrf(html: str) -> str:
    return re.search(r'name="csrf_token" value="([^"]+)"', html)[1]


def _session(client: AsyncClient) -> str:
    """What the browser holds in its session cookies (signed, readable: their JSON, decoded)."""
    values = [c.value for c in client.cookies.jar if c.name == "paceforge_session"]
    return " ".join(base64.b64decode(v.split(".")[0] + "==").decode() for v in values)


# ── Réglages › Accès API ─────────────────────────────────────────────────────

async def test_a_key_is_made_shown_once_listed_and_revoked(client, db_session, athlete):
    login(client, athlete)
    page = await client.get("/settings")
    assert page.status_code == 200 and 'id="api"' in page.text and "Accès API" in page.text
    assert 'href="/developpeurs"' in page.text and "Documentation de l’API" in page.text
    assert 'name="health" value="1"' in page.text  # the consent is given: the box is offered, unticked
    assert 'name="health" value="1" checked' not in page.text
    made = await client.post("/settings/api-tokens", data={"name": "  Mon   tableur ", "health": "1",
                                                           "csrf_token": _csrf(page.text)})
    assert made.status_code == 303 and made.headers["location"] == "/settings#api"
    row = (await db_session.scalars(select(ApiToken).where(ApiToken.user_id == athlete.id))).one()
    assert row.name == "Mon tableur" and row.scopes == "read health" and row.revoked_at is None
    shown = await client.get("/settings")
    token = re.search(r'<textarea id="api-new-key"[^>]*>(pf_[A-Za-z0-9_-]{43})</textarea>', shown.text)[1]
    assert "Copie-la maintenant&nbsp;: elle ne sera plus affichée." in shown.text
    assert row.token_hash == api_tokens.digest(token) and row.prefix == token[:8]
    assert (await db_session.scalar(select(func.count()).select_from(ApiToken).where(
        ApiToken.token_hash == token))) == 0  # never stored in clear
    again = await client.get("/settings")
    assert token not in again.text and "api-new-key" not in again.text  # once
    assert f"{row.prefix}…" in again.text and "Mon tableur" in again.text and "Lecture et santé" in again.text
    assert (await client.get("/api/v1/me", headers=bearer(token))).status_code == 200
    revoked = await client.post(f"/settings/api-tokens/{row.id}/revoke", data={"csrf_token": _csrf(again.text)})
    assert revoked.status_code == 303 and revoked.headers["location"] == "/settings#api"
    await db_session.refresh(row)
    assert row.revoked_at is not None
    after = await client.get("/settings")
    assert "Clé révoquée" in after.text and f"{row.prefix}…" not in after.text
    refused = await client.get("/api/v1/me", headers=bearer(token))
    assert refused.status_code == 401 and refused.json()["code"] == "token_revoked"


async def test_the_fresh_key_crosses_the_session_encrypted(client, athlete):
    """The session cookie is signed, not secret: the key rides in it encrypted, for one page, then leaves it."""
    login(client, athlete)
    page = await client.get("/settings")
    await client.post("/settings/api-tokens", data={"name": "Clé", "csrf_token": _csrf(page.text)})
    payload = _session(client)
    assert "api_new" in payload and "pf_" not in payload
    token = re.search(r"(pf_[A-Za-z0-9_-]{43})", (await client.get("/settings")).text)[1]
    assert token not in _session(client) and "api_new" not in _session(client)


async def test_at_most_five_live_keys(client, db_session, athlete):
    for i in range(api_tokens.MAX_ACTIVE):
        await _key(db_session, athlete, name=f"Clé {i}")
    login(client, athlete)
    page = await client.get("/settings")
    assert "Créer la clé" not in page.text and "5 clés au plus" in page.text
    await client.post("/settings/api-tokens", data={"name": "Sixième", "csrf_token": _csrf(page.text)})
    assert "5 clés au plus" in (await client.get("/settings")).text
    assert await api_tokens.active_count(db_session, athlete.id) == api_tokens.MAX_ACTIVE


async def test_no_key_without_a_confirmed_email_nor_health_without_the_consent(client, db_session, test_user):
    test_user.email, test_user.email_verified = "pending@x.fr", False
    await db_session.flush()
    login(client, test_user)
    refused = await client.post("/settings/api-tokens", data={"name": "Clé", "csrf_token": "x"})
    assert refused.status_code == 303 and refused.headers["location"] == "/auth/check-email"
    test_user.email = None  # an account without an email has none confirmed either
    test_user.health_consent_at = test_user.health_consent_version = None
    await db_session.flush()
    client.cookies.clear()
    login(client, test_user)
    page = await client.get("/settings")
    assert "Accès API" in page.text and 'name="health"' not in page.text  # no consent: no box
    await client.post("/settings/api-tokens", data={"name": "Clé", "csrf_token": _csrf(page.text)})
    assert "Confirme ton adresse e-mail" in (await client.get("/settings")).text
    test_user.email, test_user.email_verified = "camille@x.fr", True
    await db_session.flush()
    page = await client.get("/settings")
    await client.post("/settings/api-tokens", data={"name": "Clé", "health": "1", "csrf_token": _csrf(page.text)})
    assert "Donne d’abord ton accord" in (await client.get("/settings")).text
    assert await api_tokens.active_count(db_session, test_user.id) == 0


async def test_making_or_revoking_a_key_needs_the_form_token(client, db_session, athlete):
    token = await _key(db_session, athlete)
    row = await db_session.scalar(select(ApiToken).where(ApiToken.token_hash == api_tokens.digest(token)))
    login(client, athlete)
    await client.get("/settings")
    assert (await client.post("/settings/api-tokens", data={"name": "Clé"})).status_code == 403
    assert (await client.post(f"/settings/api-tokens/{row.id}/revoke", data={"csrf_token": "nope"})).status_code == 403
    assert await api_tokens.active_count(db_session, athlete.id) == 1


async def test_a_key_of_another_account_cannot_be_revoked(client, db_session, athlete, other):
    theirs = await _key(db_session, other)
    login(client, athlete)
    page = await client.get("/settings")
    row = await db_session.scalar(select(ApiToken).where(ApiToken.token_hash == api_tokens.digest(theirs)))
    await client.post(f"/settings/api-tokens/{row.id}/revoke", data={"csrf_token": _csrf(page.text)})
    await db_session.refresh(row)
    assert row.revoked_at is None
    assert (await client.get("/api/v1/me", headers=bearer(theirs))).json()["id"] == other.id


# ── authentication ───────────────────────────────────────────────────────────

async def test_calls_without_a_valid_key_are_refused(client, db_session, athlete):
    missing = await client.get("/api/v1/me")
    assert missing.status_code == 401 and missing.json()["code"] == "token_missing"
    assert missing.headers["www-authenticate"].startswith("Bearer")
    assert missing.headers["cache-control"] == "no-store"
    for header in ("Bearer abc", "Bearer " + "x" * 46, "Token pf_" + "a" * 43,
                   "Bearer pf_" + "a" * 42 + "!", "Bearer " + "a" * 64):  # malformed, wrong prefix, a phone's
        r = await client.get("/api/v1/me", headers={"Authorization": header})
        assert r.status_code == 401 and r.json()["code"] in ("token_invalid", "token_missing"), header
    unknown = await client.get("/api/v1/me", headers=bearer("pf_" + "a" * 43))
    assert unknown.status_code == 401 and unknown.json() == {"detail": api_v1.INVALID, "code": "token_invalid"}
    token = await _key(db_session, athlete)
    in_url = await client.get(f"/api/v1/me?api_key={token}", headers=bearer(token))
    assert in_url.status_code == 400 and in_url.json()["code"] == "token_in_url"
    assert (await client.get(f"/api/v1/me?token={token}")).json()["code"] == "token_in_url"
    assert (await client.get("/api/v1/me", headers=bearer(token))).status_code == 200


async def test_an_address_trying_keys_is_held(client, monkeypatch):
    from app.services import auth

    monkeypatch.setitem(auth.QUOTAS, "api_auth", (600, {"ip": 3}))
    codes = [(await client.get("/api/v1/me", headers=bearer("pf_" + "b" * 43))).status_code for _ in range(5)]
    assert codes == [401, 401, 401, 429, 429]


async def test_each_key_has_its_budget(client, db_session, athlete, monkeypatch):
    monkeypatch.setattr(api_v1, "minute_limit", PhoneLimiter(limit=2, window=60, prefix="test:api:m"))
    token, spare = await _key(db_session, athlete), await _key(db_session, athlete, name="Autre")
    answers = [await client.get("/api/v1/me", headers=bearer(token)) for _ in range(3)]
    assert [a.status_code for a in answers] == [200, 200, 429]
    assert answers[2].json()["code"] == "rate_limited" and 1 <= int(answers[2].headers["retry-after"]) <= 60
    assert (await client.get("/api/v1/me", headers=bearer(spare))).status_code == 200  # another key, its own
    monkeypatch.setattr(api_v1, "day_limit", PhoneLimiter(limit=1, window=86400, prefix="test:api:d"))
    third = await _key(db_session, athlete, name="Troisième")
    assert (await client.get("/api/v1/me", headers=bearer(third))).status_code == 200
    day = await client.get("/api/v1/me", headers=bearer(third))
    assert day.status_code == 429 and "par jour" in day.json()["detail"]


async def test_the_last_use_is_written_now_and_then(client, db_session, athlete):
    token = await _key(db_session, athlete)
    row = await db_session.scalar(select(ApiToken).where(ApiToken.token_hash == api_tokens.digest(token)))
    await client.get("/api/v1/me", headers=bearer(token))
    first = row.last_used_at
    assert first is not None
    await client.get("/api/v1/me", headers=bearer(token))
    assert row.last_used_at == first  # within SEEN_EVERY: no write
    row.last_used_at = NOW - timedelta(minutes=10)
    await db_session.flush()
    await client.get("/api/v1/me", headers=bearer(token))
    assert api_v1._aware(row.last_used_at) > NOW - timedelta(minutes=1)


async def test_errors_are_json_in_french(client, db_session, athlete):
    token = await _key(db_session, athlete)
    nowhere = await client.get("/api/v1/nowhere", headers={**bearer(token), "Accept": "text/html"})
    assert nowhere.status_code == 404 and nowhere.json() == {"detail": "Introuvable.", "code": "not_found"}
    post = await client.post("/api/v1/me", headers=bearer(token))
    assert post.status_code == 405 and post.json()["code"] == "method_not_allowed"
    bad = await client.get("/api/v1/activities?limit=1000", headers=bearer(token))
    assert bad.status_code == 422 and bad.json() == {"detail": "Paramètre invalide : limit.",
                                                     "code": "invalid_parameter"}
    cursor = await client.get("/api/v1/activities?cursor=nimporte", headers=bearer(token))
    assert cursor.status_code == 400 and cursor.json()["code"] == "invalid_cursor"
    # the phone's own API keeps FastAPI's shape (it reads the field names)
    phone = await client.post("/api/mobile/exchange", json={})
    assert phone.status_code == 422 and isinstance(phone.json()["detail"], list)


async def test_the_answers_keep_the_security_headers_and_open_no_cors(client, db_session, athlete):
    """Server-to-server use: no CORS header for another site's page, the app's security headers kept."""
    token = await _key(db_session, athlete)
    r = await client.get("/api/v1/me", headers={**bearer(token), "Origin": "https://ailleurs.example"})
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    assert not any(name.startswith("access-control-") for name in r.headers)
    assert r.headers["x-content-type-options"] == "nosniff" and r.headers["x-frame-options"] == "DENY"
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
    preflight = await client.options("/api/v1/me", headers={"Origin": "https://ailleurs.example",
                                                             "Access-Control-Request-Method": "GET"})
    assert preflight.status_code == 405 and not any(n.startswith("access-control-") for n in preflight.headers)


# ── the endpoints ────────────────────────────────────────────────────────────

async def test_me(client, db_session, athlete):
    token = await _key(db_session, athlete, health=True, name="Mon tableur")
    body = (await client.get("/api/v1/me", headers=bearer(token))).json()
    assert body["id"] == athlete.id and body["firstname"] == "Test" and body["units"] == "metric"
    assert body["timezone"] == "Europe/Paris" and body["health_consent"] is True
    assert body["token"]["name"] == "Mon tableur" and body["token"]["scopes"] == ["read", "health"]
    assert body["token"]["prefix"] == token[:8] and token not in json.dumps(body)
    assert "email" not in body


async def _plan(db, user: User, **overrides) -> Route:
    course = _course()
    route = Route(user_id=user.id, name="Trail test", course_json=course.model_dump(),
                  total_distance_km=course.total_distance_km, total_elevation_gain=course.total_elevation_gain,
                  total_elevation_loss=course.total_elevation_loss, target_time_s=5 * 3600, start_hour=6,
                  start_minute=0, race_date="2027-06-12", sport_type="trail", params_json={"target_v": 2})
    for key, value in overrides.items():
        setattr(route, key, value)
    db.add(route)
    await db.flush()
    for cp in CPS:
        db.add(RouteCheckpoint(route_id=route.id, **cp))
    await db.flush()
    return route


async def test_the_plans_and_their_passage_times(client, db_session, athlete, other):
    token = await _key(db_session, athlete)
    first = await _plan(db_session, athlete, name="Premier")
    second = await _plan(db_session, athlete, name="Second", target_time_s=None, params_json={})
    theirs = await _plan(db_session, other, name="Le leur")
    page = (await client.get("/api/v1/plans?limit=1", headers=bearer(token))).json()
    assert [p["name"] for p in page["data"]] == ["Second"] and page["next_cursor"]
    rest = (await client.get(f"/api/v1/plans?limit=1&cursor={page['next_cursor']}", headers=bearer(token))).json()
    assert [p["name"] for p in rest["data"]] == ["Premier"] and rest["next_cursor"] is None
    summary = rest["data"][0]
    assert summary == {**summary, "id": first.id, "sport": "trail", "race_date": "2027-06-12", "start_time": "06:00",
                       "distance_m": 30000.0, "objective_s": 18000, "checkpoints": 3}
    assert page["data"][0]["objective_s"] is None
    assert theirs.id not in [p["id"] for p in page["data"] + rest["data"]]

    plan = (await client.get(f"/api/v1/plans/{first.id}", headers=bearer(token))).json()
    assert plan["basis"] == "objective" and plan["plan_total_s"] == 18000 and plan["estimate_s"] > 0
    assert plan["timezone"] == "Europe/Paris" and plan["start_at"] == "2027-06-12T06:00:00+02:00"
    names = [p["name"] for p in plan["passages"]]
    assert names == ["Eau 1", "Col", "Village", "Arrivée"]
    col = plan["passages"][1]
    assert col["distance_m"] == 10000.0 and col["aid"] == "full" and col["crew"] and col["drop_bag"]
    assert col["elevation_gain_m"] == plan["passages"][0]["elevation_gain_m"] + round(
        sum(s["elevation_gain"] for s in _course().model_dump()["segments"][6:10]))
    assert col["cutoff"]["clock"] == "10:30" and col["cutoff"]["at"] == "2027-06-12T10:30:00+02:00"
    assert col["cutoff"]["margin_s"] == col["cutoff"]["elapsed_s"] - col["times"]["target"]["elapsed_s"] // 60 * 60
    for p in plan["passages"]:
        t = p["times"]
        assert t["fast"]["elapsed_s"] < t["target"]["elapsed_s"] < t["safe"]["elapsed_s"]
        at = datetime.fromisoformat(t["target"]["at"])
        assert at == datetime(2027, 6, 12, 4, 0, tzinfo=timezone.utc) + timedelta(seconds=t["target"]["elapsed_s"])
        assert t["target"]["clock"] == at.strftime("%H:%M") and t["target"]["day"] == 0
    assert abs(plan["passages"][-1]["times"]["target"]["elapsed_s"] - 18000) <= 60
    assert plan["scenarios"]["finish_s"]["target"] == plan["passages"][-1]["times"]["target"]["elapsed_s"]
    assert plan["passages"][-1]["aid"] is None and plan["passages"][0]["aid"] == "water"
    estimate = (await client.get(f"/api/v1/plans/{second.id}", headers=bearer(token))).json()
    assert estimate["basis"] == "estimate" and estimate["objective_s"] is None
    assert (await client.get(f"/api/v1/plans/{theirs.id}", headers=bearer(token))).status_code == 404
    assert (await client.get("/api/v1/plans/999999", headers=bearer(token))).json()["code"] == "not_found"
    assert "course_json" not in json.dumps(plan) and "weather" not in json.dumps(plan)


async def test_a_pinned_time_and_a_race_day_passage_show(client, db_session, athlete):
    token = await _key(db_session, athlete)
    route = await _plan(db_session, athlete)
    col = await db_session.scalar(select(RouteCheckpoint).where(RouteCheckpoint.route_id == route.id,
                                                                RouteCheckpoint.name == "Col"))
    col.target_s = 2 * 3600
    await db_session.flush()
    plan = (await client.get(f"/api/v1/plans/{route.id}", headers=bearer(token))).json()
    pinned = next(p for p in plan["passages"] if p["name"] == "Col")
    assert plan["basis"] == "pinned" and pinned["pinned"] and pinned["pinned_elapsed_s"] == 7200
    assert pinned["times"]["target"]["elapsed_s"] == 7200
    route.live_json = {"anchor_km": 20, "anchor_clock": "09:30", "anchor_name": "Village"}
    await db_session.flush()
    live = (await client.get(f"/api/v1/plans/{route.id}", headers=bearer(token))).json()
    assert live["basis"] == "race_day" and live["scenarios"] is None
    village = next(p for p in live["passages"] if p["name"] == "Village")
    assert village["times"] == {"target": {**village["times"]["target"], "clock": "09:30"}, "fast": None, "safe": None}


async def test_plan_computations_have_an_hourly_budget(client, db_session, athlete, monkeypatch):
    from app.services import auth

    monkeypatch.setitem(auth.QUOTAS, "api_plan", (3600, {"user": 1}))
    token = await _key(db_session, athlete)
    route = await _plan(db_session, athlete)
    assert (await client.get(f"/api/v1/plans/{route.id}", headers=bearer(token))).status_code == 200
    again = await client.get(f"/api/v1/plans/{route.id}", headers=bearer(token))
    assert again.status_code == 429 and again.headers["retry-after"] == "3600"


async def _activity(db, user: User, i: int, **overrides) -> Activity:
    values = dict(user_id=user.id, strava_activity_id=user.id * 1000 + i, name=f"Sortie {i}", sport_type="TrailRun",
                  start_date=datetime(2026, 11, 10 + i, 7, 0, tzinfo=timezone.utc), distance=10000.0 + i,
                  moving_time=3600, elapsed_time=3700, total_elevation_gain=500.0, average_heartrate=140.0,
                  max_heartrate=170.0, average_speed=2.8, max_speed=4.0, calories=700.0,
                  raw_data={"secret_token": "RAW-DATA"}, streams_data={"heartrate": [1, 2]},
                  splits_metric=[{"split": 1, "distance": 1000.0, "moving_time": 400, "elapsed_time": 410,
                                  "elevation_difference": 60.0, "average_speed": 2.5, "average_heartrate": 135.0},
                                 "abîmé"])
    values.update(overrides)
    activity = Activity(**values)
    db.add(activity)
    await db.flush()
    return activity


async def test_the_activities_their_pages_and_filters(client, db_session, athlete, other):
    token = await _key(db_session, athlete)
    mine = [await _activity(db_session, athlete, i) for i in range(3)]
    road = await _activity(db_session, athlete, 3, sport_type="Run", garmin_activity_id=77, name="Route")
    theirs = await _activity(db_session, other, 9)
    page = (await client.get("/api/v1/activities?limit=2", headers=bearer(token))).json()
    assert [a["name"] for a in page["data"]] == ["Route", "Sortie 2"] and page["next_cursor"]
    rest = (await client.get(f"/api/v1/activities?limit=2&cursor={page['next_cursor']}",
                             headers=bearer(token))).json()
    assert [a["name"] for a in rest["data"]] == ["Sortie 1", "Sortie 0"] and rest["next_cursor"] is None
    first = page["data"][0]
    assert first == {"id": road.id, "name": "Route", "sport": "Run", "sport_group": "run",
                     "start": "2026-11-13T08:00:00+01:00", "distance_m": 10003.0, "moving_time_s": 3600,
                     "elapsed_time_s": 3700, "elevation_gain_m": 500.0, "average_heartrate_bpm": 140.0,
                     "max_heartrate_bpm": 170.0, "average_speed_mps": 2.8, "max_speed_mps": 4.0,
                     "calories_kcal": 700.0, "sources": ["strava", "garmin"]}
    trail = (await client.get("/api/v1/activities?sport=trail", headers=bearer(token))).json()["data"]
    assert [a["name"] for a in trail] == ["Sortie 2", "Sortie 1", "Sortie 0"]
    exact = (await client.get("/api/v1/activities?sport=Run", headers=bearer(token))).json()["data"]
    assert [a["id"] for a in exact] == [road.id]
    window = (await client.get("/api/v1/activities?since=2026-11-11&until=2026-11-12",
                               headers=bearer(token))).json()["data"]
    assert [a["name"] for a in window] == ["Sortie 2", "Sortie 1"]  # days on the athlete's clock, until included
    instant = (await client.get("/api/v1/activities?since=2026-11-12T07:30:00Z", headers=bearer(token))).json()
    assert [a["name"] for a in instant["data"]] == ["Route"]
    bad = await client.get("/api/v1/activities?since=hier", headers=bearer(token))
    assert bad.status_code == 422 and bad.json()["code"] == "invalid_parameter"
    detail = (await client.get(f"/api/v1/activities/{mine[0].id}", headers=bearer(token))).json()
    assert detail["splits"] == [{"index": 1, "distance_m": 1000.0, "moving_time_s": 400, "elapsed_time_s": 410,
                                 "elevation_difference_m": 60.0, "average_speed_mps": 2.5,
                                 "average_heartrate_bpm": 135.0}]
    assert "RAW-DATA" not in json.dumps(detail) and "streams" not in json.dumps(detail)
    assert (await client.get(f"/api/v1/activities/{theirs.id}", headers=bearer(token))).status_code == 404
    assert theirs.id not in [a["id"] for a in page["data"] + rest["data"]]


async def _health_rows(db, user: User, day: date) -> None:
    db.add_all([
        HealthMetric(user_id=user.id, date=day, metric="sleep", value=445, source="Garmin",
                     details={"main_start": f"{day - timedelta(days=1)}T23:12", "main_end": f"{day}T06:37", "tz": 60,
                              "stages": {"deep": 86, "light": 238, "rem": 101, "awake": 20},
                              "readings": list(range(500))}),
        HealthMetric(user_id=user.id, date=day, metric="hrv", value=58.3, source="Garmin",
                     details={"method": "ln_mean_main", "n": 210}),
        HealthMetric(user_id=user.id, date=day, metric="hr_night", value=47, source="COROS",
                     details={"min": 42, "max": 61}),
        HealthMetric(user_id=user.id, date=day, metric="feel", value=2, source="PaceForge",
                     details={"daily_checkin": {"v": 1, "feel": "ok"}}),
        HealthMetric(user_id=user.id, date=day, metric="body_battery", value=80, source="Garmin"),
        MobileDaily(user_id=user.id, platform="ios", date=day, metric="weight", value=64.8, sources=["Balance"],
                    measured_at=datetime(day.year, day.month, day.day, 6, 58, tzinfo=timezone.utc)),
    ])
    await db.flush()


async def test_the_health_data_need_the_scope_and_the_consent(client, db_session, athlete, other):
    day = date(2026, 11, 14)
    await _health_rows(db_session, athlete, day)
    await _health_rows(db_session, other, day)
    plain = await _key(db_session, athlete)
    scoped = await _key(db_session, athlete, health=True)
    no_scope = await client.get("/api/v1/health/daily?from=2026-11-14&to=2026-11-14", headers=bearer(plain))
    assert no_scope.status_code == 403 and no_scope.json()["code"] == "health_scope_required"
    r = await client.get("/api/v1/health/daily?from=2026-11-14&to=2026-11-14", headers=bearer(scoped))
    assert r.status_code == 200
    body = r.json()
    assert body["from"] == body["to"] == "2026-11-14" and len(body["days"]) == 1
    metrics = {m["metric"]: m for m in body["days"][0]["metrics"]}
    assert set(metrics) == {"sleep", "hrv", "hr_night", "weight"}  # never the feel, nor a brand's own value
    assert metrics["sleep"] == {"metric": "sleep", "value": 26700.0, "unit": "s", "source": "Garmin", "details": {
        "start": "2026-11-13T23:12:00+01:00", "end": "2026-11-14T06:37:00+01:00",
        "stages_s": {"deep": 5160, "light": 14280, "rem": 6060, "awake": 1200}}}
    assert metrics["hrv"]["unit"] == "ms" and metrics["hrv"]["details"] == {"method": "ln_mean_main"}
    assert metrics["hr_night"]["details"] == {"min_bpm": 42.0, "max_bpm": 61.0}
    assert metrics["weight"] == {"metric": "weight", "value": 64.8, "unit": "kg", "source": "Apple Santé",
                                 "details": {"measured_at": "2026-11-14T06:58:00+00:00", "apps": ["Balance"]}}
    assert "readings" not in r.text and "feel" not in r.text and "daily_checkin" not in r.text
    assert len(body["days"][0]["metrics"]) == 4  # the other athlete's rows are theirs
    default = (await client.get("/api/v1/health/daily", headers=bearer(scoped))).json()
    assert (date.fromisoformat(default["to"]) - date.fromisoformat(default["from"])).days == 29
    too_long = await client.get("/api/v1/health/daily?from=2025-01-01&to=2026-11-14", headers=bearer(scoped))
    assert too_long.status_code == 422 and "366 jours" in too_long.json()["detail"]
    reversed_ = await client.get("/api/v1/health/daily?from=2026-11-14&to=2026-11-01", headers=bearer(scoped))
    assert reversed_.status_code == 422

    athlete.health_consent_version = "2020-01-01"  # a newer text not agreed to yet: no health data
    await db_session.flush()
    stale = await client.get("/api/v1/health/daily", headers=bearer(scoped))
    assert stale.status_code == 403 and stale.json()["code"] == "health_consent_required"
    assert (await client.get("/api/v1/me", headers=bearer(scoped))).json()["health_consent"] is False


async def test_a_withdrawn_consent_closes_the_health_data_to_every_key(client, db_session, athlete, monkeypatch):
    scoped = await _key(db_session, athlete, health=True)
    assert (await client.get("/api/v1/health/daily", headers=bearer(scoped))).status_code == 200
    login(client, athlete)
    assert (await client.post("/settings/health-consent/withdraw")).status_code == 303
    refused = await client.get("/api/v1/health/daily", headers=bearer(scoped))
    assert refused.status_code == 403 and refused.json()["code"] == "health_consent_required"
    row = await db_session.scalar(select(ApiToken).where(ApiToken.token_hash == api_tokens.digest(scoped)))
    await db_session.refresh(row)
    assert row.scopes == "read" and row.revoked_at is None  # plans and activities still readable
    assert (await client.get("/api/v1/plans", headers=bearer(scoped))).status_code == 200
    await db_session.refresh(athlete)
    athlete.health_consent_at, athlete.health_consent_version = NOW, HEALTH_CONSENT_VERSION  # given again
    await db_session.flush()
    later = await client.get("/api/v1/health/daily", headers=bearer(scoped))
    assert later.status_code == 403 and later.json()["code"] == "health_scope_required"  # a new key for it


# ── what happens to the keys elsewhere ───────────────────────────────────────

async def test_a_password_reset_revokes_every_key_of_the_account(client, db_session, athlete, other):
    from app.services.auth import hash_token

    mine, theirs = await _key(db_session, athlete), await _key(db_session, other)
    athlete.password_reset_token = hash_token("reset-token")
    athlete.password_reset_expires_at = NOW + timedelta(hours=1)
    await db_session.flush()
    client.base_url = "https://test"
    r = await client.post("/auth/reset-password", data={"token": "reset-token", "password": "pw-87654321"})
    assert r.status_code == 302
    refused = await client.get("/api/v1/me", headers=bearer(mine))
    assert refused.status_code == 401 and refused.json()["code"] == "token_revoked"
    assert (await client.get("/api/v1/me", headers=bearer(theirs))).status_code == 200


async def test_the_account_deletion_takes_the_keys(committing_session, committing_client, monkeypatch):
    """Foreign keys enforced and commits for real (tests/test_account_deletion empties every table this way)."""
    from unittest.mock import AsyncMock

    from app.dependencies import get_current_user
    from app.routers import settings as settings_router

    db = committing_session
    user = User(email="leaving@x.fr", email_verified=True)
    db.add(user)
    await db.flush()
    token = await _key(db, user)
    await db.commit()
    monkeypatch.setattr(settings_router, "_revoke_links", AsyncMock())  # no provider linked here
    app = committing_client._transport.app  # type: ignore[attr-defined]
    app.dependency_overrides[get_current_user] = lambda: user
    r = await committing_client.post("/settings/delete-account", data={"confirmation": "SUPPRIMER"})
    assert r.status_code == 302
    del app.dependency_overrides[get_current_user]
    await db.rollback()  # only what was committed counts
    assert (await db.scalar(select(func.count()).select_from(ApiToken))) == 0
    assert (await committing_client.get("/api/v1/me", headers=bearer(token))).status_code == 401


async def test_the_export_lists_the_keys_without_their_hash(db_session, athlete, other):
    mine = await _key(db_session, athlete, health=True, name="Mon tableur")
    theirs = await _key(db_session, other, name="La leur")
    await api_tokens.revoke(db_session, athlete.id, (await api_tokens.active(db_session, athlete.id))[0].id)
    links = await data_export.connections(db_session, athlete)
    (key,) = links["cles_api"]
    assert key["name"] == "Mon tableur" and key["prefix"] == mine[:8] and key["scopes"] == "read health"
    assert key["created_at"] and key["revoked_at"] and set(key) == set(data_export.API_KEY_FIELDS)
    dumped = json.dumps(links)
    for secret in (mine, api_tokens.digest(mine), theirs[:8], "La leur", "token_hash"):
        assert secret not in dumped


async def test_revoked_keys_are_deleted_after_thirty_days(db_session, athlete):
    old, recent = await _key(db_session, athlete, name="Ancienne"), await _key(db_session, athlete, name="Récente")
    rows = {r.name: r for r in (await db_session.scalars(select(ApiToken))).all()}
    rows["Ancienne"].revoked_at = NOW - timedelta(days=31)
    rows["Récente"].revoked_at = NOW - timedelta(days=1)
    await db_session.flush()
    assert await api_tokens.purge_revoked(db_session) == 1
    assert [r.name for r in (await db_session.scalars(select(ApiToken))).all()] == ["Récente"]
    assert old and recent


async def test_a_plan_hidden_by_its_sport_is_neither_listed_nor_read(client, db_session, athlete):
    """Bike and triathlon plans stay out of sight while cycling is off (features.hidden_sports), here too."""
    token = await _key(db_session, athlete)
    bike = Route(user_id=athlete.id, name="Vélo", total_distance_km=80, sport_type="bike")
    bare = Route(user_id=athlete.id, name="Sans tracé", total_distance_km=21, target_time_s=7200,
                 params_json={"target_v": 2})
    db_session.add_all([bike, bare])
    await db_session.flush()
    listed = (await client.get("/api/v1/plans", headers=bearer(token))).json()["data"]
    assert [p["name"] for p in listed] == ["Sans tracé"]
    assert (await client.get(f"/api/v1/plans/{bike.id}", headers=bearer(token))).status_code == 404
    plain = (await client.get(f"/api/v1/plans/{bare.id}", headers=bearer(token))).json()
    assert plain["passages"] == [] and plain["basis"] is None and plain["objective_s"] == 7200


# ── the key never in a log ───────────────────────────────────────────────────

async def test_the_key_is_never_logged(client, db_session, athlete, caplog):
    token = await _key(db_session, athlete, health=True)
    route = await _plan(db_session, athlete)
    caplog.set_level(logging.DEBUG)
    for path in ("/api/v1/me", "/api/v1/plans", f"/api/v1/plans/{route.id}", "/api/v1/activities",
                 "/api/v1/health/daily"):
        assert (await client.get(path, headers=bearer(token))).status_code == 200
    await client.get(f"/api/v1/me?api_key={token}")
    assert token not in caplog.text and token[3:] not in caplog.text


def test_the_access_log_and_sentry_mask_a_key():
    from app import sentry
    from app.log_redaction import SecretQueryFilter, redact

    token = "pf_" + "Zq9" * 14 + "x"
    assert redact(f"GET /api/v1/me?api_key={token} 400") == "GET /api/v1/me?api_key=*** 400"
    assert redact(f"GET /api/v1/{token}/me 404") == "GET /api/v1/pf_***/me 404"
    record = logging.LogRecord("uvicorn.access", logging.INFO, "", 0, '%s - "%s %s HTTP/%s" %d',
                               ("203.0.113.9:1", "GET", f"/api/v1/me?key={token}", "1.1", 400), None)
    SecretQueryFilter().filter(record)
    assert token not in record.getMessage() and len(record.args) == 5
    event = {"request": {"url": f"https://paceforge.fr/api/v1/me?api_key={token}", "query_string": f"api_key={token}",
                         "headers": {"Authorization": f"Bearer {token}", "User-Agent": "curl"}},
             "message": f"bad key {token}",
             "exception": {"values": [{"type": "ValueError", "value": f"refused {token}"}]},
             "breadcrumbs": {"values": [{"message": f"Bearer {token}", "data": {"url": f"/x?token={token}"}}]}}
    scrubbed = sentry._scrub(event)
    assert scrubbed["request"] == {"url": "https://paceforge.fr/api/v1/me", "headers": {"User-Agent": "curl"}}
    assert token not in repr(scrubbed) and scrubbed["exception"]["values"][0]["value"] == "refused pf_***"


# ── the documentation ────────────────────────────────────────────────────────

async def test_the_documentation_page_is_public_and_in_the_sitemap(client):
    from app.main import PUBLIC_PAGES

    r = await client.get("/developpeurs")
    assert r.status_code == 200 and "/developpeurs" in PUBLIC_PAGES
    html = r.text
    for path in ("/api/v1/me", "/api/v1/plans", "/api/v1/plans/{id}", "/api/v1/activities",
                 "/api/v1/activities/{id}", "/api/v1/health/daily", "/api/v1/openapi.json"):
        assert path in html, path
    for words in ("Authorization: Bearer pf_", "Retry-After", "health_scope_required", "health_consent_required",
                  "Garde-la secrète", "révoque-la", "token_revoked", "60 requêtes par minute"):
        assert words in html, words
    assert '<link rel="canonical" href="https://paceforge.fr/developpeurs">' in html
    assert "<loc>" in (await client.get("/sitemap.xml")).text and "/developpeurs</loc>" in (
        await client.get("/sitemap.xml")).text
    assert "Disallow: /developpeurs" not in (await client.get("/robots.txt")).text


def test_the_documented_examples_are_the_contract():
    api_v1.Me.model_validate(api_v1.EXAMPLES["me"])
    api_v1.Page[api_v1.PlanSummary].model_validate(api_v1.EXAMPLES["plans"])
    api_v1.PlanDetail.model_validate(api_v1.EXAMPLES["plan"])
    api_v1.Page[api_v1.ActivitySummary].model_validate(api_v1.EXAMPLES["activities"])
    api_v1.Split.model_validate(api_v1.EXAMPLES["activity_split"])
    api_v1.HealthDaily.model_validate(api_v1.EXAMPLES["health"])
    for name, model in (("me", api_v1.Me), ("plan", api_v1.PlanDetail)):  # every field named, none forgotten
        assert set(api_v1.EXAMPLES[name]) == set(model.model_fields), name


async def test_the_openapi_document_covers_the_api_only(client):
    r = await client.get("/api/v1/openapi.json")
    assert r.status_code == 200
    doc = r.json()
    assert set(doc["paths"]) == {"/api/v1/me", "/api/v1/plans", "/api/v1/plans/{plan_id}", "/api/v1/activities",
                                 "/api/v1/activities/{activity_id}", "/api/v1/health/daily"}
    assert all(set(ops) == {"get"} for ops in doc["paths"].values())
    assert [s["scheme"] for s in doc["components"]["securitySchemes"].values()] == ["bearer"]
    assert "token_hash" not in r.text and "raw_data" not in r.text
