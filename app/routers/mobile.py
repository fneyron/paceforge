"""Native companion: PKCE pairing, scoped imports and revocation.

Daily phone aggregates stay outside the nocturnal/recovery tables. No password,
health record, bearer token or authorization code is put in a URL or a log.

The phone also syncs in the background, with its token only: the token's expiry
slides with use, a rotated token keeps working a few days, each phone has a
request budget, and bodies are capped before they are parsed.
"""
import asyncio
import hashlib
import logging
import re
import secrets
import time
from datetime import date, datetime, timedelta, timezone
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Form, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError, model_validator
from sqlalchemy import and_, cast, delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.templating import Jinja2Templates

from app.dependencies import get_current_user, get_db
from app.models.mobile import MobileDaily, MobileDevice
from app.models.user import User
from app.models.health import HealthMetric
from app.services import apple_nights
from app.services.apple_nights import AppleNight

logger = logging.getLogger(__name__)
router = APIRouter(tags=["mobile"])
templates = Jinja2Templates(directory="app/templates")
HEADERS = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer", "X-Frame-Options": "DENY"}
Platform = Literal["ios", "android"]
LIMITS = {"weight": (25, 300), "body_fat": (1, 75), "steps": (0, 200000), "hr_day": (25, 230), "resp_day": (4, 60)}
UNITS = {"weight": "kg", "body_fat": "%", "steps": "count", "hr_day": "bpm", "resp_day": "rpm"}
TOKEN_LIFETIME = timedelta(days=180)
# Renewed on use once fewer days remain: at most one write a month per phone.
RENEW_BELOW = timedelta(days=150)
SEEN_EVERY = timedelta(hours=1)
# A rotated token keeps working this long, until the new one is first used.
PREVIOUS_TOKEN_GRACE = timedelta(days=7)
# 160 daily rows are ~35 kB. A night at its validation maxima is ~1.8 MB.
MAX_DAILY_BYTES = 64 * 1024
MAX_NIGHT_BYTES = 2 * 1024 * 1024


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


class PhoneLimiter:
    """At most `limit` authenticated phone requests per device and window.

    Redis shares the count between web workers; without it each worker counts
    alone, which still bounds a looping phone. A full manual sync is ~62 requests.
    """

    def __init__(self, limit: int = 200, window: int = 600):
        self.limit, self.window = limit, window
        self.local: dict[int, tuple[int, int]] = {}
        self.redis = None
        self.redis_down_until = 0.0

    async def _redis_count(self, key: str) -> int | None:
        if time.monotonic() < self.redis_down_until:
            return None
        try:
            if self.redis is None:
                import redis.asyncio as aioredis

                from app.config import settings
                self.redis = aioredis.Redis.from_url(settings.REDIS_URL, socket_connect_timeout=0.25,
                                                     socket_timeout=0.25)
            async with self.redis.pipeline(transaction=True) as pipe:
                count, _ = await pipe.incr(key).expire(key, self.window).execute()
            return int(count)
        except Exception:
            # Not a request failure: count in this process for a minute, then retry Redis.
            logger.warning("Phone rate limit: Redis unavailable, counting per worker")
            self.redis, self.redis_down_until = None, time.monotonic() + 60
            return None

    def _local_count(self, device_id: int, slot: int) -> int:
        seen_slot, count = self.local.get(device_id, (slot, 0))
        count = count + 1 if seen_slot == slot else 1
        if len(self.local) > 10000:
            self.local = {k: v for k, v in self.local.items() if v[0] == slot}
        self.local[device_id] = (slot, count)
        return count

    async def check(self, device_id: int) -> None:
        now = time.time()
        slot = int(now // self.window)
        count = await self._redis_count(f"mobile:rate:{device_id}:{slot}")
        if count is None:
            count = self._local_count(device_id, slot)
        if count > self.limit:
            retry = max(1, int((slot + 1) * self.window - now))
            raise HTTPException(429, "Trop d’envois depuis ce téléphone. Réessaie plus tard.",
                                headers={"Retry-After": str(retry)})


limiter = PhoneLimiter()


async def capped_body(request: Request, limit: int) -> bytes:
    """Read at most `limit` bytes, before any JSON parsing or validation."""
    declared = request.headers.get("content-length")
    if declared is not None and (not declared.isdigit() or int(declared) > limit):
        raise HTTPException(413, "Envoi trop volumineux.")
    chunks, size = [], 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > limit:
            raise HTTPException(413, "Envoi trop volumineux.")
        chunks.append(chunk)
    return b"".join(chunks)


def validated(model: type[BaseModel], raw: bytes):
    """Same 422 shape as a typed FastAPI body (the phone reads only field names)."""
    try:
        return model.model_validate_json(raw)
    except ValidationError as exc:
        raise RequestValidationError(
            [{**err, "loc": ("body", *err.get("loc", ()))} for err in exc.errors(include_url=False)]) from None


def utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def csrf(request: Request) -> str:
    if "mobile_csrf" not in request.session:
        request.session["mobile_csrf"] = secrets.token_urlsafe(32)
    return request.session["mobile_csrf"]


def check_csrf(request: Request, token: str) -> None:
    if not secrets.compare_digest(request.session.get("mobile_csrf", ""), token) or not token:
        raise HTTPException(403, "Recharge la page puis réessaie.")


def page(request: Request, **context):
    return templates.TemplateResponse(request, "mobile.html", context=context, headers=HEADERS)


@router.get("/mobile/privacy")
async def privacy(request: Request):
    return page(request, mode="privacy")


@router.get("/mobile/session")
async def session(request: Request, nonce: str, user: User = Depends(get_current_user)):
    if not re.fullmatch(r"[a-f0-9]{64}", nonce):
        raise HTTPException(400)
    message = {"type": "session", "nonce": nonce, "user_id": user.id}
    if request.headers.get("accept") == "application/json":
        return JSONResponse(message, headers=HEADERS)
    return page(request, mode="session", message=message)


@router.get("/mobile/connections")
async def connections(request: Request, nonce: str, user: User = Depends(get_current_user),
                      db: AsyncSession = Depends(get_db)):
    """Read-only, session-bound status for the native list; never provider tokens."""
    if not re.fullmatch(r"[a-f0-9]{64}", nonce):
        raise HTTPException(400)
    from app.services.coros import coros_status
    from app.services.garmin import garmin_status
    from app.services.strava import strava_status
    states = {"coros": await coros_status(db, user.id), "garmin": await garmin_status(db, user.id),
              "strava": strava_status(user)}
    providers = {}
    for key, state in states.items():
        providers[key] = ("reconnect" if state.get("needs_reauth") else
                          "disconnected" if not state["connected"] else
                          "syncing" if state.get("syncing") else
                          "attention" if state.get("last_error") or state.get("interrupted") else "connected")
    message = {"type": "connections", "nonce": nonce, "user_id": user.id,
               "name": user.firstname or "Athlète", "providers": providers}
    if request.headers.get("accept") == "application/json":
        return JSONResponse(message, headers=HEADERS)
    return page(request, mode="connections", message=message)


@router.get("/mobile/connect")
async def connect(request: Request, challenge: str, platform: Platform, user: User = Depends(get_current_user)):
    if not re.fullmatch(r"[a-f0-9]{64}", challenge):
        raise HTTPException(400)
    return page(request, mode="connect", user=user, challenge=challenge, platform=platform, csrf=csrf(request))


@router.post("/mobile/connect")
async def pair(request: Request, challenge: str = Form(), platform: Platform = Form(),
               csrf_token: str = Form(), user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    check_csrf(request, csrf_token)
    if not re.fullmatch(r"[a-f0-9]{64}", challenge):
        raise HTTPException(400)
    now = datetime.now(timezone.utc)
    await db.execute(delete(MobileDevice).where(MobileDevice.user_id == user.id, MobileDevice.expires_at < now))
    count = len((await db.scalars(select(MobileDevice.id).where(MobileDevice.user_id == user.id))).all())
    if count >= 10:
        raise HTTPException(409, "Supprime une ancienne association dans Téléphones associés.")
    code = secrets.token_urlsafe(32)
    db.add(MobileDevice(user_id=user.id, platform=platform, code_hash=digest(code), challenge=challenge,
                       expires_at=now + timedelta(minutes=2)))
    await db.flush()
    return page(request, mode="paired", message={"type": "pair", "challenge": challenge, "code": code})


class Exchange(BaseModel):
    code: str = Field(min_length=43, max_length=128)
    verifier: str = Field(min_length=43, max_length=128)


@router.post("/api/mobile/exchange")
async def exchange(body: Exchange, db: AsyncSession = Depends(get_db)):
    now = datetime.now(timezone.utc)
    token = secrets.token_urlsafe(48)
    # Atomic consume: two concurrent exchanges cannot create two valid credentials.
    row = (await db.execute(update(MobileDevice).where(
        MobileDevice.code_hash == digest(body.code), MobileDevice.challenge == digest(body.verifier),
        MobileDevice.expires_at > now,
    ).values(code_hash=None, challenge=None, token_hash=digest(token), expires_at=now + timedelta(days=180))
       .returning(MobileDevice.user_id))).first()
    if not row:
        raise HTTPException(401, "Association expirée ou invalide.")
    user = await db.get(User, row.user_id)
    return JSONResponse({"token": token, "user_id": user.id, "name": user.firstname or "Athlète"}, headers=HEADERS)


def bearer(authorization: str) -> str:
    if not authorization.startswith("Bearer ") or len(authorization) > 200:
        raise HTTPException(401, "Téléphone non associé.")
    return digest(authorization[7:])


async def device(authorization: str = Header(default=""), db: AsyncSession = Depends(get_db)) -> MobileDevice:
    used = bearer(authorization)
    now = datetime.now(timezone.utc)
    row = await db.scalar(select(MobileDevice).where(MobileDevice.token_hash == used))
    if row is not None and row.prev_token_hash is not None and utc(row.expires_at) > now:
        # The phone has stored its rotated token: the previous one stops working.
        row.prev_token_hash = row.prev_expires_at = None
    elif row is None:
        row = await db.scalar(select(MobileDevice).where(MobileDevice.prev_token_hash == used))
        if row is not None and (row.prev_expires_at is None or utc(row.prev_expires_at) <= now):
            row = None
    if row is None or utc(row.expires_at) <= now:
        raise HTTPException(401, "Associe à nouveau ce téléphone.")
    # Sliding expiry: a phone that keeps syncing, including in the background,
    # is never logged out; an unused one still expires 180 days after its last use.
    if utc(row.expires_at) - now < RENEW_BELOW:
        row.expires_at = now + TOKEN_LIFETIME
    if row.last_seen_at is None or now - utc(row.last_seen_at) >= SEEN_EVERY:
        row.last_seen_at = now
    return row


async def limited(phone: MobileDevice = Depends(device)) -> MobileDevice:
    await limiter.check(phone.id)
    return phone


@router.post("/api/mobile/token")
async def rotate(authorization: str = Header(default=""), phone: MobileDevice = Depends(limited),
                 db: AsyncSession = Depends(get_db)):
    """A new token for this phone (asked in the foreground only).

    The token that asked stays valid for 7 days, until the new one is first used:
    a response lost on the way, or a phone killed before storing it, keeps working.
    """
    now = datetime.now(timezone.utc)
    token = secrets.token_urlsafe(48)
    phone.prev_token_hash, phone.prev_expires_at = bearer(authorization), now + PREVIOUS_TOKEN_GRACE
    phone.token_hash, phone.expires_at = digest(token), now + TOKEN_LIFETIME
    await db.flush()
    return JSONResponse({"token": token, "user_id": phone.user_id, "expires_at": phone.expires_at.isoformat()},
                        headers=HEADERS)


class Daily(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    date: date
    metric: Literal["weight", "body_fat", "steps", "hr_day", "resp_day"]
    value: float
    unit: str
    sources: list[Annotated[str, StringConstraints(min_length=1, max_length=150)]] = Field(min_length=1, max_length=20)
    measured_at: datetime | None = None

    @model_validator(mode="after")
    def validate_measure(self):
        low, high = LIMITS[self.metric]
        now = datetime.now(timezone.utc)
        if not low <= self.value <= high or self.unit != UNITS[self.metric]:
            raise ValueError("Valeur ou unité invalide.")
        if not now.date()-timedelta(days=90) <= self.date <= now.date()+timedelta(days=1):
            raise ValueError("Date hors de la fenêtre autorisée.")
        if self.metric in ("weight", "body_fat") and self.measured_at is None:
            raise ValueError("Date de mesure requise.")
        if self.measured_at is not None:
            if self.measured_at.tzinfo is None or self.measured_at > now + timedelta(minutes=5):
                raise ValueError("Horodatage invalide.")
            if abs((self.measured_at.date()-self.date).days) > 1:
                raise ValueError("La mesure ne correspond pas au jour importé.")
        if self.metric == "steps" and self.value != int(self.value):
            raise ValueError("Nombre de pas entier requis.")
        return self


class Import(BaseModel):
    model_config = ConfigDict(extra="forbid")
    days: list[Daily] = Field(max_length=160)

    @model_validator(mode="after")
    def unique_days(self):
        keys = [(d.date, d.metric) for d in self.days]
        if len(set(keys)) != len(keys):
            raise ValueError("Une valeur par jour et type de mesure.")
        return self


def read_at(value: str | None, now: datetime) -> datetime | None:
    """When the phone read the values it sends (`X-PaceForge-Read-At`), if plausible."""
    if not value or len(value) > 40:
        return None
    try:
        at = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if at.tzinfo is None or not now - timedelta(days=1) <= at <= now + timedelta(minutes=5):
        return None
    return at.astimezone(timezone.utc)


async def latest_per_metric(db: AsyncSession, phone: MobileDevice) -> dict:
    """The retained row of each metric's most recent day: one row per metric."""
    scope = (MobileDaily.user_id == phone.user_id, MobileDaily.platform == phone.platform)
    last = select(MobileDaily.metric, func.max(MobileDaily.date).label("day")).where(*scope) \
        .group_by(MobileDaily.metric).subquery()
    rows = await db.scalars(select(MobileDaily).join(last, and_(
        MobileDaily.metric == last.c.metric, MobileDaily.date == last.c.day)).where(*scope))
    return {row.metric: {"date": row.date.isoformat(), "value": row.value,
                         "measured_at": utc(row.measured_at).isoformat() if row.measured_at else None}
            for row in rows}


def daily_upsert(dialect: str, day: Daily, phone: MobileDevice, seen: datetime | None, now: datetime):
    """One day's insert-or-update; a no-op when nothing changed."""
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert
    postgres = dialect != "sqlite"
    values = day.model_dump(exclude={"unit"})
    values.update(user_id=phone.user_id, platform=phone.platform, device_id=phone.id, updated_at=now)
    stmt = (pg_insert if postgres else sqlite_insert)(MobileDaily).values(**values)
    new = stmt.excluded
    # The production column is `json` (migration d4a5b6c7d8e9), which has no equality
    # operator on PostgreSQL: compare the sources as jsonb there.
    sources = (cast(MobileDaily.sources, JSONB), cast(new.sources, JSONB)) if postgres else (MobileDaily.sources, new.sources)
    # Identical re-sends (every background run) change nothing: no updated_at,
    # receipt count or Santé data version bump.
    changed = or_(MobileDaily.value.is_distinct_from(new.value), sources[0].is_distinct_from(sources[1]),
                  MobileDaily.measured_at.is_distinct_from(new.measured_at))
    if day.metric in ("weight", "body_fat"):
        # An older snapshot must not replace a later weigh-in: not from another
        # device, nor from a delayed request. The same phone may replace its own
        # day when it read Santé after that row was stored: the later weigh-in
        # was deleted there, and the day's previous one is now its latest.
        ordered = MobileDaily.measured_at.is_(None) | (new.measured_at >= MobileDaily.measured_at)
        if seen is not None:
            ordered = ordered | ((MobileDaily.device_id == phone.id) & (MobileDaily.updated_at <= seen))
        changed = changed & ordered
    return stmt.on_conflict_do_update(
        index_elements=["user_id", "platform", "date", "metric"],
        set_={key: getattr(new, key) for key in ("value", "sources", "measured_at", "device_id", "updated_at")},
        where=changed,
    ).returning(MobileDaily)


@router.post("/api/mobile/daily")
async def ingest(request: Request, phone: MobileDevice = Depends(limited), db: AsyncSession = Depends(get_db),
                 x_paceforge_read_at: str | None = Header(default=None)):
    body = validated(Import, await capped_body(request, MAX_DAILY_BYTES))
    now = datetime.now(timezone.utc)
    seen = read_at(x_paceforge_read_at, now)
    received = 0
    for day in body.days:
        result = await db.scalars(daily_upsert(db.bind.dialect.name, day, phone, seen, now),
                                  execution_options={"populate_existing": True})
        received += len(result.all())
    if received:
        phone.last_sync_at = now
    await db.flush()
    # A receipt describes what the server retained, including a later weigh-in
    # already sent by another phone. An ignored old snapshot is not an update.
    return JSONResponse({"received": received, "skipped": len(body.days) - received,
                         "checked_at": now.isoformat(), "latest": await latest_per_metric(db, phone)},
                        headers=HEADERS)


@router.post("/api/mobile/nights")
async def ingest_night(request: Request, phone: MobileDevice = Depends(limited), db: AsyncSession = Depends(get_db)):
    raw = await capped_body(request, MAX_NIGHT_BYTES)
    if phone.platform != "ios":
        raise HTTPException(422, "Les nuits Apple Santé nécessitent un iPhone.")
    # Up to ~25 000 validated objects: parse off the event loop.
    body = await asyncio.to_thread(validated, AppleNight, raw)
    try:
        received = await apple_nights.ingest(db, phone.user_id, body)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    if received:
        phone.last_sync_at = datetime.now(timezone.utc)
    await db.flush()
    return {"received": received, "checked_at": datetime.now(timezone.utc).isoformat()}


@router.delete("/api/mobile/device")
async def disconnect(phone: MobileDevice = Depends(device), db: AsyncSession = Depends(get_db)):
    await db.delete(phone)
    return {"disconnected": True}


@router.get("/mobile/devices")
async def devices(request: Request, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    phones = (await db.scalars(select(MobileDevice).where(MobileDevice.user_id == user.id,
              MobileDevice.token_hash.is_not(None)).order_by(MobileDevice.created_at.desc()))).all()
    return page(request, mode="devices", user=user, phones=phones, csrf=csrf(request))


@router.post("/mobile/devices/revoke")
async def revoke(request: Request, device_id: int = Form(), csrf_token: str = Form(),
                 user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    check_csrf(request, csrf_token)
    await db.execute(delete(MobileDevice).where(MobileDevice.user_id == user.id, MobileDevice.id == device_id))
    return RedirectResponse("/mobile/devices", status_code=303)


@router.post("/mobile/data/delete")
async def erase(request: Request, csrf_token: str = Form(), user: User = Depends(get_current_user),
                db: AsyncSession = Depends(get_db)):
    check_csrf(request, csrf_token)
    await db.execute(delete(MobileDaily).where(MobileDaily.user_id == user.id))
    await db.execute(delete(HealthMetric).where(HealthMetric.user_id == user.id,
                     HealthMetric.source.startswith(apple_nights.PREFIX)))
    await db.execute(delete(MobileDevice).where(MobileDevice.user_id == user.id))
    return RedirectResponse("/mobile/devices", status_code=303)
