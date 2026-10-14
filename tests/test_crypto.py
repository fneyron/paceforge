"""Secrets at rest: Strava tokens encrypted like the others, keys that can change without losing what is
stored, a value no key reads any more asking to reconnect (never a crash), and no start with a guessable
SECRET_KEY."""
from unittest.mock import AsyncMock

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select, text

from app.config import settings
from app.crypto import decrypt_secret, encrypt_secret, legacy_key
from app.main import create_app, lifespan
from app.models.oauth_attempt import OAuthAttempt
from app.models.user import User
from app.services.strava import StravaService, strava_status
from tests.test_settings_links import _callback, _logged_in


def decrypt_secret_or_none(value: str):
    try:
        return decrypt_secret(value.encode())
    except Exception:
        return None


def _foreign(value: str) -> str:
    """A Fernet value written with a key PaceForge no longer has."""
    return Fernet(Fernet.generate_key()).encrypt(value.encode()).decode()


async def _raw(db, user_id: int, column: str):
    return (await db.execute(text(f"SELECT {column} FROM users WHERE id = :id"), {"id": user_id})).scalar_one()


async def test_strava_tokens_are_stored_encrypted_and_read_back_in_clear(db_session, test_user):
    stored = await _raw(db_session, test_user.id, "strava_access_token")
    assert stored.startswith("gAAAAA") and "test_access_token" not in stored
    assert decrypt_secret(stored.encode()) == "test_access_token"
    assert "test_refresh_token" not in await _raw(db_session, test_user.id, "strava_refresh_token")
    db_session.expire(test_user)
    await db_session.refresh(test_user)
    assert (test_user.strava_access_token, test_user.strava_refresh_token) == ("test_access_token",
                                                                               "test_refresh_token")
    # a 40-character Strava token takes 140 characters encrypted: String(512) holds up to 319
    assert len(encrypt_secret("a" * 40)) == 140 and len(encrypt_secret("a" * 319)) <= 512


async def test_a_token_stored_in_clear_before_still_works_and_is_encrypted_when_it_changes(db_session, test_user):
    await db_session.execute(text("UPDATE users SET strava_access_token = :t, strava_refresh_token = :r WHERE id = :id"),
                             {"t": "0123456789abcdef0123456789abcdef01234567", "r": "f" * 40, "id": test_user.id})
    db_session.expire(test_user)
    await db_session.refresh(test_user)
    assert test_user.strava_access_token == "0123456789abcdef0123456789abcdef01234567"
    assert test_user.strava_refresh_token == "f" * 40 and strava_status(test_user)["connected"]
    test_user.strava_access_token = "a" * 40  # the next refresh
    await db_session.flush()
    assert (await _raw(db_session, test_user.id, "strava_access_token")).startswith("gAAAAA")


async def test_new_keys_encrypt_and_every_older_value_stays_readable(db_session, test_user, monkeypatch):
    before = encrypt_secret("written-before-the-keys")  # the key derived from SECRET_KEY, as today
    new = Fernet.generate_key().decode()
    monkeypatch.setattr(settings, "ENCRYPTION_KEYS", f" {new} ")
    assert decrypt_secret(before) == "written-before-the-keys"
    after = encrypt_secret("written-with-the-new-key")
    assert Fernet(new.encode()).decrypt(after) == b"written-with-the-new-key"  # the first key encrypts
    db_session.expire(test_user)
    await db_session.refresh(test_user)
    assert test_user.strava_access_token == "test_access_token"  # written under the old key
    # SECRET_KEY can then change: its old derived key, kept at the end of ENCRYPTION_KEYS, still reads
    old = legacy_key(settings.SECRET_KEY).decode()
    newer = Fernet.generate_key().decode()
    monkeypatch.setattr(settings, "ENCRYPTION_KEYS", f"{newer},{new},{old}")
    monkeypatch.setattr(settings, "SECRET_KEY", "another-long-random-secret-key-value")
    assert decrypt_secret(before) == "written-before-the-keys"
    assert decrypt_secret(after) == "written-with-the-new-key"


async def test_a_strava_link_no_key_reads_asks_to_reconnect(db_session, test_user):
    await db_session.execute(text("UPDATE users SET strava_access_token = :t, strava_refresh_token = :r WHERE id = :id"),
                             {"t": _foreign("a" * 40), "r": _foreign("b" * 40), "id": test_user.id})
    db_session.expire(test_user)
    await db_session.refresh(test_user)  # loading the athlete never fails
    assert test_user.strava_access_token is None and test_user.strava_refresh_token is None
    assert strava_status(test_user)["connected"] is False  # « Connecter Strava » again


async def test_app_keys_no_key_reads_are_typed_again(db_session, test_user):
    await db_session.execute(text("UPDATE users SET strava_client_id = '4242', strava_client_secret_encrypted = :s "
                                  "WHERE id = :id"), {"s": _foreign("s3cret").encode(), "id": test_user.id})
    db_session.expire(test_user)
    await db_session.refresh(test_user)
    assert test_user.strava_client_secret is None and not test_user.has_own_strava_app
    assert strava_status(test_user)["needs_reauth"]  # « À reconnecter », never a crash


@pytest.mark.parametrize("secret_key", ["", "   ", "change-me-to-a-random-secret-key"])
async def test_no_start_with_an_empty_or_default_secret_key(monkeypatch, secret_key):
    monkeypatch.setattr(settings, "SECRET_KEY", secret_key)
    monkeypatch.setattr(settings, "DEBUG", False)
    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        async with lifespan(create_app()):
            pass
    monkeypatch.setattr(settings, "DEBUG", True)  # a developer's machine
    async with lifespan(create_app()):
        pass


async def test_no_start_with_a_malformed_encryption_key(monkeypatch):
    monkeypatch.setattr(settings, "SECRET_KEY", "a-long-random-secret-key-for-this-test")
    monkeypatch.setattr(settings, "ENCRYPTION_KEYS", "not-a-fernet-key")
    with pytest.raises(RuntimeError, match="ENCRYPTION_KEYS"):
        async with lifespan(create_app()):
            pass
    monkeypatch.setattr(settings, "ENCRYPTION_KEYS", Fernet.generate_key().decode())
    async with lifespan(create_app()):
        pass


async def test_a_strava_attempt_no_key_reads_is_an_expired_link(client, db_session, monkeypatch):
    exchange = AsyncMock()
    monkeypatch.setattr(StravaService, "exchange_token", exchange)
    monkeypatch.setattr(db_session, "commit", db_session.flush)
    async with _logged_in(client, db_session) as (c, user):
        await c.post("/setup/credentials", data={"client_id": "4242", "client_secret": "s3cret"})
        attempt = (await db_session.execute(select(OAuthAttempt))).scalar_one()
        attempt.credentials = _foreign('{"client_id": "4242", "client_secret": "s3cret"}').encode()
        await db_session.flush()
        r = await _callback(c, code="abc")
        assert r.status_code == 302 and r.headers["location"] == "/setup?error=auth_failed"
    assert exchange.await_count == 0




# ── no start when stored secrets no configured key opens ────────────────────

@pytest.fixture
async def stored(tmp_path):
    """A database of its own (as the app's at startup), and a session on it."""
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from app.database import Base

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/start.db")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)() as db:
        yield engine, db
    await engine.dispose()


def _coros_link(user_id):
    from datetime import datetime, timedelta, timezone

    from app.models.coros import CorosConnection

    return CorosConnection(user_id=user_id, issuer="https://coros", client_id="cid", resource_url="https://coros/mcp",
                           token_endpoint="https://coros/token", access_token_encrypted=encrypt_secret("at-1"),
                           refresh_token_encrypted=encrypt_secret("rt-1"),
                           expires_at=datetime.now(timezone.utc) + timedelta(days=30))


async def test_no_start_when_the_key_that_encrypted_the_links_is_missing(stored, monkeypatch, caplog):
    """The owner set ENCRYPTION_KEYS, the tokens were refreshed under it, then one deploy went without it: the
    app used to start and its first sync round flagged every link « to reconnect » for good."""
    from app.main import check_stored_secrets
    from app.models.user import User

    engine, db = stored
    monkeypatch.setattr(settings, "SECRET_KEY", "a-long-random-production-secret-key")
    key = Fernet.generate_key().decode()
    monkeypatch.setattr(settings, "ENCRYPTION_KEYS", key)
    user = User(email="a@x.fr")
    db.add(user)
    await db.flush()
    db.add(_coros_link(user.id))
    await db.commit()
    await check_stored_secrets(engine)  # the key is there: starts
    monkeypatch.setattr(settings, "ENCRYPTION_KEYS", "")  # the deploy without it
    with pytest.raises(RuntimeError, match="COROS") as refused:
        await check_stored_secrets(engine)
    assert "ENCRYPTION_KEYS" in str(refused.value)
    monkeypatch.setattr(settings, "ENCRYPTION_KEYS", key)
    await check_stored_secrets(engine)


async def test_each_kind_of_stored_secret_is_checked_on_its_first_and_last_written_value(stored, monkeypatch):
    from datetime import datetime, timedelta, timezone

    from sqlalchemy import update

    from app.crypto import unreadable_secrets
    from app.models.garmin import GarminConnection
    from app.models.user import User

    engine, db = stored
    old = User(email="old@x.fr")
    db.add(old)
    await db.flush()
    raw = text("UPDATE users SET strava_refresh_token = :t, updated_at = :at WHERE id = :id")  # no column type
    await db.execute(raw, {"t": "b" * 40, "at": "2026-01-01 00:00:00", "id": old.id})  # stored in clear: skipped
    await db.commit()
    async with engine.connect() as conn:
        assert await unreadable_secrets(conn) == []  # nothing encrypted yet, the clear token ignored
    new = User(email="new@x.fr", strava_refresh_token="c" * 40, strava_client_id="4242",
               strava_client_secret_encrypted=encrypt_secret("s3cret"))
    db.add_all([new, GarminConnection(user_id=old.id, di_client_id="d", access_token_encrypted=_foreign("g").encode(),
                                      expires_at=datetime.now(timezone.utc) + timedelta(hours=1))])
    await db.commit()
    async with engine.connect() as conn:
        assert await unreadable_secrets(conn) == ["Garmin"]
    # the first written value under a key now gone, the last one fine: still caught
    await db.execute(raw, {"t": _foreign("b" * 40), "at": "2026-01-02 00:00:00", "id": old.id})
    await db.execute(update(User).where(User.id == new.id).values(updated_at=datetime.now(timezone.utc)))
    await db.commit()
    async with engine.connect() as conn:
        assert await unreadable_secrets(conn) == ["Garmin", "Strava"]


async def test_a_database_that_does_not_answer_never_blocks_the_start(tmp_path, caplog):
    import logging

    from sqlalchemy.ext.asyncio import create_async_engine

    from app.main import check_stored_secrets

    caplog.set_level(logging.WARNING)
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/missing/dir/x.db")  # cannot open
    await check_stored_secrets(engine)
    assert "not checked at start" in caplog.text
    await engine.dispose()


def test_the_secret_key_refusal_says_how_to_keep_the_tokens(monkeypatch):
    from app.main import check_secrets

    monkeypatch.setattr(settings, "SECRET_KEY", "")
    monkeypatch.setattr(settings, "DEBUG", False)
    with pytest.raises(RuntimeError, match=r"first append legacy_key\(old SECRET_KEY\) to ENCRYPTION_KEYS"):
        check_secrets()


# ── Strava tokens stored in clear are encrypted once, by the hourly upkeep ──

async def test_tokens_left_in_clear_are_encrypted_by_the_hourly_upkeep(db_session, test_user, monkeypatch):
    """A refresh token Strava hands back unchanged is never rewritten by the refresh, so it stayed in clear for
    good; the hourly task now rewrites every token still in clear, once."""
    from contextlib import asynccontextmanager

    import httpx

    from app.tasks import oauth_cleanup

    monkeypatch.setattr(db_session, "commit", db_session.flush)
    clear = text("UPDATE users SET strava_access_token = :t, strava_refresh_token = :r, strava_token_expires_at = 1, "
                 "strava_client_id = '4242' WHERE id = :id")
    await db_session.execute(clear, {"t": "a" * 40, "r": "f" * 40, "id": test_user.id})
    other = User(email="other@x.fr")
    db_session.add(other)
    await db_session.flush()
    other_id = other.id
    await db_session.execute(text("UPDATE users SET strava_refresh_token = :r WHERE id = :id"),
                             {"r": _foreign("lost" * 10), "id": other_id})  # unreadable: left alone
    db_session.expire_all()
    await db_session.refresh(test_user)

    async def post(self, url, **kwargs):  # Strava's /oauth/token: a new access token, the same refresh token
        return httpx.Response(200, json={"access_token": "c" * 40, "refresh_token": "f" * 40, "expires_at": 9999999999},
                              request=httpx.Request("POST", url))
    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    await StravaService(db_session, client_id="4242", client_secret="s3cret").refresh_token_if_needed(test_user)
    await db_session.flush()
    assert (await _raw(db_session, test_user.id, "strava_refresh_token")) == "f" * 40  # still in clear

    @asynccontextmanager
    async def session():
        yield db_session
    monkeypatch.setattr("app.database.get_task_session", session)
    assert await oauth_cleanup.encrypt_clear_strava_tokens(db_session) == 1
    for column in ("strava_access_token", "strava_refresh_token"):
        assert (await _raw(db_session, test_user.id, column)).startswith("gAAAAA")
    db_session.expire_all()
    await db_session.refresh(test_user)
    assert (test_user.strava_access_token, test_user.strava_refresh_token) == ("c" * 40, "f" * 40)
    stored = await _raw(db_session, other_id, "strava_refresh_token")
    assert stored.startswith("gAAAAA") and decrypt_secret_or_none(stored) is None  # left as it was
    assert await oauth_cleanup.encrypt_clear_strava_tokens(db_session) == 0  # once done, nothing more
    await oauth_cleanup._run()  # the hourly task runs it (and purges the expired attempts)
