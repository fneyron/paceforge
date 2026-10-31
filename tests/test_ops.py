"""Operations: the deep health check, the optional error reporting, the Celery limits, and the invariants of
the production compose file and of .env.example (every setting wired and documented, one beat, rotated logs)."""
import re
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import Settings, settings

ROOT = Path(__file__).resolve().parent.parent
AI_SETTINGS = {"ANTHROPIC_API_KEY", "CLAUDE_MODEL"}  # the AI chain's, deleted: they must never come back in config, compose or .env.example


class _Redis:
    """redis.asyncio.Redis stand-in: answers PING."""

    @classmethod
    def from_url(cls, url, **kwargs):
        return cls()

    async def ping(self):
        return True

    async def aclose(self):
        pass


def _fake_redis(monkeypatch):
    import redis.asyncio as aioredis

    monkeypatch.setattr(aioredis.Redis, "from_url", _Redis.from_url)


# ── /health ──────────────────────────────────────────────────────────────────

async def test_health_stays_shallow_by_default(client):
    body = (await client.get("/health")).json()
    assert body["status"] == "ok" and "db" not in body and "redis" not in body


async def test_deep_health_reports_the_database_and_redis(client, engine, monkeypatch):
    from app import database

    monkeypatch.setattr(database, "engine", engine)
    _fake_redis(monkeypatch)
    response = await client.get("/health?deep=1")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok" and body["db"] is True and body["redis"] is True and len(body["build"]) == 64
    assert response.headers["cache-control"] == "no-store"


async def test_deep_health_is_503_when_redis_is_down(client, engine, monkeypatch):
    from app import database

    monkeypatch.setattr(database, "engine", engine)
    monkeypatch.setattr(settings, "REDIS_URL", "redis://127.0.0.1:1/0")  # nothing listens there
    response = await client.get("/health?deep=1")
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "degraded" and body["db"] is True and body["redis"] is False


async def test_deep_health_is_503_when_the_database_is_down(client, monkeypatch):
    from app import database

    dead = create_async_engine("postgresql+asyncpg://nobody@127.0.0.1:1/none")  # nothing listens there
    monkeypatch.setattr(database, "engine", dead)
    _fake_redis(monkeypatch)
    try:
        response = await client.get("/health?deep=1")
    finally:
        await dead.dispose()
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "degraded" and body["db"] is False and body["redis"] is True


# ── Sentry ───────────────────────────────────────────────────────────────────

def test_sentry_does_nothing_without_a_dsn(monkeypatch):
    from app import sentry

    monkeypatch.setattr(settings, "SENTRY_DSN", " ")
    monkeypatch.setattr(sentry, "_initialised", False)
    monkeypatch.delitem(sys.modules, "sentry_sdk", raising=False)
    assert sentry.init_sentry() is False
    assert "sentry_sdk" not in sys.modules  # not even imported


def test_sentry_initialises_once_per_process_with_every_integration(monkeypatch):
    """A web worker imports app.celery_app to queue a task and a Celery worker imports the app's modules: the
    second call must keep the first client, with the Starlette, FastAPI and Celery integrations all on (a second
    sentry_sdk.init would replace it with a client missing the first caller's integrations). The real SDK, a
    DSN nothing listens on."""
    sentry_sdk = pytest.importorskip("sentry_sdk")
    from app import sentry
    from app.build import BUILD_ID

    monkeypatch.setattr(settings, "SENTRY_DSN", "https://0123456789abcdef0123456789abcdef@127.0.0.1:1/1")
    monkeypatch.setattr(sentry, "_initialised", False)
    client = None
    try:
        assert sentry.init_sentry() is True
        client = sentry_sdk.get_client()
        assert client.is_active()
        assert {"starlette", "fastapi", "celery"} <= set(client.integrations)
        assert not {"redis", "httpx", "sqlalchemy"} & set(client.integrations)  # no breadcrumbs with URLs or SQL
        assert client.options["send_default_pii"] is False and client.options["traces_sample_rate"] is None
        assert client.options["max_request_body_size"] == "never" and client.options["include_local_variables"] is False
        assert client.options["before_send"] is sentry._scrub
        assert BUILD_ID.startswith(client.options["release"])
        assert sentry.init_sentry() is True  # the other module's turn: a no-op
        assert sentry_sdk.get_client() is client
    finally:
        if client is not None:
            client.close(timeout=0)
        sentry_sdk.get_global_scope().set_client(None)
    assert not sentry_sdk.is_initialized()


def test_sentry_never_sends_bodies_local_variables_queries_or_cookies(monkeypatch):
    """Off in production today (no DSN), safe the day it is set: the SDK told to keep request bodies and frame
    variables out, and before_send dropping what an integration still attached (body, query, cookies, credentials)
    and masking the tokens a logged URL carries."""
    sentry_sdk = pytest.importorskip("sentry_sdk")
    from app import sentry

    init = MagicMock()
    monkeypatch.setattr(sentry_sdk, "init", init)
    monkeypatch.setattr(sentry_sdk, "is_initialized", lambda: False)
    monkeypatch.setattr(settings, "SENTRY_DSN", "https://0123456789abcdef0123456789abcdef@127.0.0.1:1/1")
    monkeypatch.setattr(sentry, "_initialised", False)
    assert sentry.init_sentry() is True
    options = init.call_args.kwargs
    assert options["max_request_body_size"] == "never" and options["include_local_variables"] is False
    assert options["send_default_pii"] is False and options["auto_enabling_integrations"] is False
    event = {
        "request": {"url": "https://paceforge.fr/auth/reset-password?token=S3CRET", "method": "POST",
                    "query_string": "token=S3CRET", "data": {"password": "hunter22", "token": "S3CRET"},
                    "cookies": {"paceforge_session": "abc"},
                    "headers": {"Cookie": "paceforge_session=abc", "Authorization": "Bearer t0k", "User-Agent": "UA",
                                "X-Forwarded-For": "203.0.113.9"}},
        "logentry": {"message": "GET %s failed", "params": ["/auth/verify-email?token=S3CRET"]},
        "breadcrumbs": {"values": [{"category": "uvicorn.access", "message": "GET /x?code=C0DE&state=S7 200"}]},
        "exception": {"values": [{"type": "ValueError"}]},
    }
    scrubbed = options["before_send"](event, {})
    assert scrubbed["request"] == {"url": "https://paceforge.fr/auth/reset-password", "method": "POST",
                                   "headers": {"User-Agent": "UA"}}
    assert scrubbed["logentry"]["params"] == ["/auth/verify-email?token=***"]
    assert scrubbed["breadcrumbs"]["values"][0]["message"] == "GET /x?code=***&state=*** 200"
    assert scrubbed["exception"] == {"values": [{"type": "ValueError"}]}  # the error itself is what is reported
    for secret in ("S3CRET", "hunter22", "abc", "t0k", "C0DE", "203.0.113.9"):
        assert secret not in repr(scrubbed)


# ── Celery ───────────────────────────────────────────────────────────────────

def test_celery_bounds_every_task_and_keeps_the_rounds_off_the_poll_minutes():
    from app.celery_app import celery_app

    conf = celery_app.conf
    assert conf.task_ignore_result is True and conf.result_expires == 3600
    assert 0 < conf.task_soft_time_limit < conf.task_time_limit <= 3600
    assert conf.broker_transport_options["visibility_timeout"] == 2 * conf.task_time_limit
    poll = conf.beat_schedule["poll-new-activities"]["schedule"].minute
    for name in ("sync-coros", "sync-garmin"):
        assert conf.beat_schedule[name]["schedule"].minute.isdisjoint(poll), name


# ── docker-compose.prod.yml and .env.example ─────────────────────────────────

def _settings_fields() -> set[str]:
    return set(Settings.model_fields) - AI_SETTINGS


def test_the_production_compose_wires_every_setting_with_one_beat_and_bounded_services():
    compose = yaml.safe_load((ROOT / "docker-compose.prod.yml").read_text())
    env = compose["x-env"]
    fields = _settings_fields()
    assert fields <= set(env), fields - set(env)
    assert set(env) - fields == {"MALLOC_ARENA_MAX"}
    for key in fields:
        value = str(env[key])
        if Settings.model_fields[key].annotation in (bool, int):  # an empty string never reaches pydantic
            assert re.fullmatch(r"\$\{%s:-[^}]+\}|true|false|\d+" % key, value), (key, value)
    services = compose["services"]
    assert services["app"]["environment"] is env and services["celery-worker"]["environment"] is env
    commands = [str(s.get("command", "")) for s in services.values()]
    assert sum("--beat" in c for c in commands) == 1
    assert sorted(re.search(r"-Q (\w+)", c)[1] for c in commands if "celery_app worker" in c) == ["celery", "long"]
    for name, service in services.items():
        assert service["logging"]["options"] == {"max-size": "10m", "max-file": "5"}, name
        assert service["mem_limit"], name
    assert services["celery-worker"]["stop_grace_period"] == "12m" and services["app"]["stop_grace_period"] == "30s"
    assert "pgbackups" in compose["volumes"] and services["db-backup"]["volumes"] == ["pgbackups:/backups"]
    for weak in ("POSTGRES_PASSWORD:-", "STRAVA_WEBHOOK_VERIFY_TOKEN:-", "ANTHROPIC"):
        assert weak not in (ROOT / "docker-compose.prod.yml").read_text(), weak


def test_env_example_documents_every_setting_and_nothing_stale():
    lines = (ROOT / ".env.example").read_text().splitlines()
    keys = {line.split("=", 1)[0] for line in lines if re.match(r"[A-Z][A-Z0-9_]*=", line)}
    fields = _settings_fields()
    assert fields <= keys, fields - keys
    compose_only = {"POSTGRES_PASSWORD", "BACKUP_S3_URL", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY",
                    "AWS_ENDPOINT_URL", "AWS_DEFAULT_REGION"}
    assert keys - fields <= compose_only, keys - fields - compose_only
    assert not keys & AI_SETTINGS


# ── the Dockerfile's uvicorn ─────────────────────────────────────────────────

def _uvicorn_command() -> str:
    cmd = re.search(r'^CMD \["sh", "-c", "(.*)"\]$', (ROOT / "Dockerfile").read_text(encoding="utf-8"), re.M)[1]
    return cmd.split("exec ", 1)[1]


async def test_uvicorn_trusts_the_proxy_headers_from_the_private_networks_only():
    """Traefik reaches the app over a private network: the X-Forwarded-For hop uvicorn takes as the client (its access
    log, request.client) is the right-most one outside those networks, the address Traefik saw, never a first hop the
    client wrote itself; the limits' client_ip reads the same one. No Server header either."""
    import ipaddress

    from starlette.requests import Request
    from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

    from app.services.auth import client_ip

    command = _uvicorn_command()
    assert "--proxy-headers" in command and "--no-server-header" in command
    trusted = re.search(r"--forwarded-allow-ips='([^']+)'", command)[1]
    assert "*" not in trusted
    for entry in trusted.split(","):
        assert ipaddress.ip_network(entry).is_private, entry  # loopback, RFC 1918, IPv6 unique local
    seen = {}

    async def app(scope, receive, send):
        seen["client"], seen["limits"] = scope["client"][0], client_ip(Request(scope))

    async def call(peer, forwarded):
        scope = {"type": "http", "headers": [(b"x-forwarded-for", forwarded.encode())], "client": (peer, 4000)}
        await ProxyHeadersMiddleware(app, trusted_hosts=trusted)(scope, None, None)
        return seen["client"], seen["limits"]

    for traefik in ("172.18.0.2", "10.0.1.7", "192.168.5.4", "127.0.0.1"):
        assert await call(traefik, "6.6.6.6, 203.0.113.7") == ("203.0.113.7", "203.0.113.7")  # the forged hop ignored
    assert (await call("198.51.100.4", "6.6.6.6"))[0] == "198.51.100.4"  # not the proxy: its header is not read
