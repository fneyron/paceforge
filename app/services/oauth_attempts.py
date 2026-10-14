import hashlib
import json
import secrets
from datetime import UTC, datetime, timedelta

from cryptography.fernet import InvalidToken
from sqlalchemy import delete

from app.crypto import decrypt_secret, encrypt_secret
from app.models.oauth_attempt import OAuthAttempt


async def purge_expired(db) -> int:
    """Delete the attempts past their 10 minutes (hourly from beat, and before each new attempt)."""
    result = await db.execute(delete(OAuthAttempt).where(OAuthAttempt.expires_at <= datetime.now(UTC))
                              .execution_options(synchronize_session="fetch"))
    return result.rowcount or 0


async def begin_strava(request, db, user_id: int, credentials=None) -> str:
    """A new single-use attempt. `credentials` are the app keys typed in the setup
    wizard (or an athlete's stored ones); for the shared app only {mode, client_id}:
    its secret stays in the server configuration."""
    now = datetime.now(UTC)
    await purge_expired(db)
    await db.execute(delete(OAuthAttempt).where(OAuthAttempt.user_id == user_id))
    state = secrets.token_urlsafe(32)
    db.add(OAuthAttempt(
        state_hash=hashlib.sha256(state.encode()).hexdigest(), user_id=user_id,
        credentials=encrypt_secret(json.dumps(credentials)) if credentials else None,
        expires_at=now + timedelta(minutes=10),
    ))
    await db.commit()
    # Only a nonce belongs in the signed (readable) session cookie.
    request.session.pop("pending_client_id", None)
    request.session.pop("pending_client_secret", None)
    request.session["strava_state"] = state
    return state


async def consume_strava(request, db, user_id: int, state: str | None):
    expected = request.session.get("strava_state")
    if (not state or not state.isascii() or not expected
            or not secrets.compare_digest(state, expected)):
        return None
    request.session.pop("strava_state", None)
    result = await db.execute(delete(OAuthAttempt).where(
        OAuthAttempt.state_hash == hashlib.sha256(state.encode()).hexdigest(),
        OAuthAttempt.user_id == user_id,
        OAuthAttempt.expires_at > datetime.now(UTC),
    ).returning(OAuthAttempt.credentials).execution_options(synchronize_session="fetch"))
    row = result.first()
    # Consume before the external exchange, including failed/cancelled exchanges.
    await db.commit()
    if row is None:
        return None
    try:
        return json.loads(decrypt_secret(row[0])) if row[0] else {}
    except InvalidToken:  # no configured key reads it (app.crypto): as good as expired
        return None
