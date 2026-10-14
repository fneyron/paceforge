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
from app.services.strava import StravaService, strava_status
from tests.test_settings_links import _callback, _logged_in


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


