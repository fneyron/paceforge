"""Operations: the deep health check, the optional error reporting, the Celery limits, and the invariants of
the production compose file and of .env.example (every setting wired and documented, one beat, rotated logs)."""
import re
import sys
import types
from pathlib import Path

import yaml
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import Settings, settings

ROOT = Path(__file__).resolve().parent.parent
AI_SETTINGS = {"ANTHROPIC_API_KEY", "CLAUDE_MODEL"}  # the AI chain's, deleted with it: neither wired nor documented


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

def _stub_sentry(monkeypatch) -> list[dict]:
    """A sentry_sdk stand-in in sys.modules, recording every init call."""
    calls: list[dict] = []
    sdk = types.ModuleType("sentry_sdk")
    sdk.init = lambda **kwargs: calls.append(kwargs)
    monkeypatch.setitem(sys.modules, "sentry_sdk", sdk)
    monkeypatch.setitem(sys.modules, "sentry_sdk.integrations", types.ModuleType("sentry_sdk.integrations"))
    for name, cls in (("celery", "CeleryIntegration"), ("fastapi", "FastApiIntegration"),
                      ("starlette", "StarletteIntegration")):
        module = types.ModuleType(f"sentry_sdk.integrations.{name}")
        setattr(module, cls, type(cls, (), {}))
        monkeypatch.setitem(sys.modules, f"sentry_sdk.integrations.{name}", module)
    return calls


def test_sentry_does_nothing_without_a_dsn(monkeypatch):
    from app.sentry import init_sentry

    calls = _stub_sentry(monkeypatch)
    monkeypatch.setattr(settings, "SENTRY_DSN", " ")
    assert init_sentry() is False and init_sentry(celery=True) is False
    assert calls == []


def test_sentry_reports_without_pii_or_traces_when_a_dsn_is_set(monkeypatch):
    from app.build import BUILD_ID
    from app.sentry import init_sentry

    calls = _stub_sentry(monkeypatch)
    monkeypatch.setattr(settings, "SENTRY_DSN", "https://key@example.invalid/1")
    assert init_sentry() is True and init_sentry(celery=True) is True
    web, worker = calls
    for call in (web, worker):
        assert call["dsn"] == "https://key@example.invalid/1"
        assert call["send_default_pii"] is False and call["auto_enabling_integrations"] is False
        assert "traces_sample_rate" not in call and "profiles_sample_rate" not in call
        assert BUILD_ID.startswith(call["release"])
    assert [type(i).__name__ for i in web["integrations"]] == ["StarletteIntegration", "FastApiIntegration"]
    assert [type(i).__name__ for i in worker["integrations"]] == ["CeleryIntegration"]


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
