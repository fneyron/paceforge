import base64
import hashlib
import re
import secrets

import bcrypt
from itsdangerous import BadSignature, TimestampSigner

from app.config import settings

PASSWORD_PREFIX = "bcrypt-sha256$"


def valid_email(email: str) -> bool:
    if len(email) > 254 or email.count("@") != 1:
        return False
    local, domain = email.rsplit("@", 1)
    return bool(
        0 < len(local) <= 64 and not local.startswith(".") and not local.endswith(".")
        and ".." not in local and re.fullmatch(r"[a-zA-Z0-9.!#$%&'*+/=?^_`{|}~-]+", local)
        and "." in domain and all(
            re.fullmatch(r"[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?", label)
            for label in domain.split(".")
        )
    )


def _password_bytes(password: str) -> bytes:
    return base64.b64encode(hashlib.sha256(password.encode()).digest())


def hash_password(password: str) -> str:
    return PASSWORD_PREFIX + bcrypt.hashpw(_password_bytes(password), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    if len(password) > 1024:
        return False
    try:
        if password_hash.startswith(PASSWORD_PREFIX):
            stored = password_hash[len(PASSWORD_PREFIX):].encode()
            return bcrypt.checkpw(_password_bytes(password), stored)
        # Legacy bcrypt used at most 72 bytes; upgrade after successful login.
        return bcrypt.checkpw(password.encode()[:72], password_hash.encode())
    except (ValueError, TypeError):
        return False


def generate_token() -> str:
    return secrets.token_urlsafe(32)


def verification_token() -> str:
    signer = TimestampSigner(settings.SECRET_KEY, salt="email-verification")
    return signer.sign(generate_token()).decode()


def valid_verification_token(token: str, max_age: int = 86400) -> bool:
    try:
        signer = TimestampSigner(settings.SECRET_KEY, salt="email-verification")
        signer.unsign(token, max_age=max_age)
        return True
    except BadSignature:
        return False
