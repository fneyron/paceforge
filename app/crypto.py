"""Secrets at rest (Strava, COROS and Garmin tokens, an athlete's own Strava app secret): Fernet.

ENCRYPTION_KEYS holds Fernet keys, newest first: the first encrypts, every one
decrypts. The key PaceForge used before them (derived from SECRET_KEY, which
also signs sessions and email links) always decrypts too, so nothing stored
before breaks; a value moves to the newest key when it is next written (a token
refresh). To change SECRET_KEY later, first add legacy_key(old SECRET_KEY) at
the end of ENCRYPTION_KEYS: what it encrypted stays readable.

A value no configured key opens raises InvalidToken here; the columns below
read it as empty instead, so its link asks to be made again (never a crash).
"""
import base64
import hashlib
import logging
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from sqlalchemy import LargeBinary, String
from sqlalchemy.types import TypeDecorator

from app.config import settings

logger = logging.getLogger(__name__)

FERNET_PREFIX = "gAAAAA"  # every Fernet token starts so (version byte, then a 64-bit timestamp)


def legacy_key(secret_key: str) -> bytes:
    """The Fernet key derived from a SECRET_KEY (the only key before ENCRYPTION_KEYS)."""
    return base64.urlsafe_b64encode(hashlib.sha256(secret_key.encode()).digest())


@lru_cache(maxsize=8)
def _fernet(secret_key: str, encryption_keys: str) -> MultiFernet:
    keys = [Fernet(k.strip().encode()) for k in encryption_keys.split(",") if k.strip()]
    return MultiFernet([*keys, Fernet(legacy_key(secret_key))])


def _get_fernet() -> MultiFernet:
    return _fernet(settings.SECRET_KEY, settings.ENCRYPTION_KEYS)


def check_keys() -> None:
    """ValueError when an ENCRYPTION_KEYS entry is not a Fernet key (checked at startup)."""
    _get_fernet()


def encrypt_secret(plaintext: str) -> bytes:
    return _get_fernet().encrypt(plaintext.encode())


def decrypt_secret(ciphertext: bytes, ttl: int | None = None) -> str:
    """InvalidToken also when `ttl` is given and the value is older than that (seconds)."""
    return _get_fernet().decrypt(ciphertext, ttl=ttl).decode()


class EncryptedString(TypeDecorator):
    """Text stored Fernet-encrypted, read back in clear. A value written in clear
    before the column was encrypted is read as is (it is encrypted when it next
    changes); one no configured key opens reads as None."""

    impl = String
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return None if value is None else encrypt_secret(value).decode()

    def process_result_value(self, value, dialect):
        if value is None or not value.startswith(FERNET_PREFIX):
            return value
        try:
            return decrypt_secret(value.encode())
        except InvalidToken:
            logger.warning("A stored token no configured key decrypts: read as empty (to reconnect)")
            return None


class SealedBytes(TypeDecorator):
    """Fernet ciphertext the code encrypts itself (encrypt_secret), read as None
    when no configured key opens it: the secret is then simply missing."""

    impl = LargeBinary
    cache_ok = True

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        try:
            _get_fernet().decrypt(bytes(value))
        except InvalidToken:
            logger.warning("A stored secret no configured key decrypts: read as missing (to type again)")
            return None
        return value
