"""Apple Health push from the iPhone Shortcut ("Raccourci").

POST /api/health/samples, header `Authorization: Bearer pfh_…` (the personal
key from Settings → Apple Santé); no session cookie. See
app.services.health.normalize_payload for the accepted body shapes.
"""
import json
import logging
import time
from collections import defaultdict, deque
from datetime import datetime, timezone
from urllib.parse import parse_qsl

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_db
from app.services.health import (
    MAX_SAMPLES_PER_PUSH,
    METRIC_LABELS,
    key_from_header,
    normalize_payload,
    store_samples,
    user_for_key,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["health"])

MAX_BODY_BYTES = 5 * 1024 * 1024
# A daily automation plus a few manual tests; per worker process
PUSHES_PER_HOUR = 30
_pushes: dict[int, deque] = defaultdict(deque)


def _error(status: int, message: str, **headers) -> JSONResponse:
    return JSONResponse({"ok": False, "message": message}, status_code=status, headers=headers or None)


def _rate_limited(user_id: int) -> bool:
    now = time.monotonic()
    log = _pushes[user_id]
    while log and now - log[0] > 3600:
        log.popleft()
    if len(log) >= PUSHES_PER_HOUR:
        return True
    log.append(now)
    return False


async def _read_body(request: Request) -> bytes | None:
    """The body, or None past MAX_BODY_BYTES (checked while reading, not after)."""
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > MAX_BODY_BYTES:
        return None
    chunks, size = [], 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_BODY_BYTES:
            return None
        chunks.append(chunk)
    return b"".join(chunks)


def _decode(raw: bytes, content_type: str):
    """JSON (what the Shortcut sends when 'JSON' is picked) or a form body
    (when 'Formulaire' is picked instead)."""
    text = raw.decode("utf-8-sig", errors="replace").strip()
    if not text:
        return {}
    if "application/x-www-form-urlencoded" in content_type:
        return dict(parse_qsl(text, keep_blank_values=False))
    return json.loads(text)


@router.post("/api/health/samples")
async def push_samples(request: Request, db: AsyncSession = Depends(get_db)):
    user = await user_for_key(db, key_from_header(request.headers.get("authorization")))
    if user is None:
        return _error(401, "Clé PaceForge absente ou invalide : recopie-la depuis Réglages → Apple Santé.",
                      **{"WWW-Authenticate": "Bearer"})
    if _rate_limited(user.id):
        return _error(429, "Trop d'envois cette heure-ci : réessaie plus tard.", **{"Retry-After": "3600"})

    raw = await _read_body(request)
    if raw is None:
        return _error(413, "Envoi trop gros (5 Mo max) : réduis la période à 3 jours.")
    try:
        body = _decode(raw, request.headers.get("content-type", ""))
    except (ValueError, UnicodeDecodeError):
        return _error(400, "Corps illisible : choisis « JSON » comme corps de la requête.")

    samples, errors = normalize_payload(body)
    if len(samples) > MAX_SAMPLES_PER_PUSH:
        return _error(413, f"Trop de mesures en un envoi ({len(samples)}) : réduis la période.")
    if errors and not samples:
        return JSONResponse({"ok": False, "message": "Aucune mesure lisible : " + errors[0],
                             "errors": errors[:10]}, status_code=422)

    result = await store_samples(db, user.id, samples) if samples else {
        "received": 0, "inserted": 0, "updated": 0, "unchanged": 0, "by_metric": {}, "days": {}}
    user.health_last_push_at = datetime.now(timezone.utc)
    user.health_last_push_count = len(samples)
    await db.flush()
    logger.info("Health push user %d: %d samples (%d new), %d rejected",
                user.id, len(samples), result["inserted"], len(errors))

    parts = [f"{METRIC_LABELS[m]} {n}" for m, n in result["by_metric"].items()]
    message = f"PaceForge : {len(samples)} mesure{'s' if len(samples) != 1 else ''} reçue{'s' if len(samples) != 1 else ''}"
    message += f" ({', '.join(parts)})" if parts else ""
    if errors:
        message += f", {len(errors)} ignorée{'s' if len(errors) > 1 else ''}"
    return {
        "ok": True,
        "message": message + ".",
        "received": len(samples),
        "new": result["inserted"],
        "updated": result["updated"],
        "ignored": len(errors),
        "by_metric": result["by_metric"],
        "days": result["days"],
        "errors": errors[:10],
    }
