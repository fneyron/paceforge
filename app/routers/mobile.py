"""Native companion: PKCE pairing, scoped imports and revocation.

Daily phone aggregates stay outside the nocturnal/recovery tables. No password,
health record, bearer token or authorization code is put in a URL or a log.
"""
import hashlib
import re
import secrets
from datetime import date, datetime, timedelta, timezone
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Form, Header, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.templating import Jinja2Templates

from app.dependencies import get_current_user, get_db
from app.models.mobile import MobileDaily, MobileDevice
from app.models.user import User

router = APIRouter(tags=["mobile"])
templates = Jinja2Templates(directory="app/templates")
HEADERS = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer", "X-Frame-Options": "DENY"}
Platform = Literal["ios", "android"]
LIMITS = {"weight": (25, 300), "body_fat": (1, 75), "steps": (0, 200000), "hr_day": (25, 230), "resp_day": (4, 60)}
UNITS = {"weight": "kg", "body_fat": "%", "steps": "count", "hr_day": "bpm", "resp_day": "rpm"}


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


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
    return page(request, mode="session", message={"type": "session", "nonce": nonce, "user_id": user.id})


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
    from fastapi.responses import JSONResponse
    return JSONResponse({"token": token, "user_id": user.id, "name": user.firstname or "Athlète"}, headers=HEADERS)


async def device(authorization: str = Header(default=""), db: AsyncSession = Depends(get_db)) -> MobileDevice:
    if not authorization.startswith("Bearer ") or len(authorization) > 200:
        raise HTTPException(401, "Téléphone non associé.")
    row = await db.scalar(select(MobileDevice).where(MobileDevice.token_hash == digest(authorization[7:])))
    if not row or utc(row.expires_at) <= datetime.now(timezone.utc):
        raise HTTPException(401, "Associe à nouveau ce téléphone.")
    return row


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


@router.post("/api/mobile/daily")
async def ingest(body: Import, phone: MobileDevice = Depends(device), db: AsyncSession = Depends(get_db)):
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert
    insert = sqlite_insert if db.bind.dialect.name == "sqlite" else pg_insert
    now = datetime.now(timezone.utc)
    for day in body.days:
        values = day.model_dump(exclude={"unit"})
        values.update(user_id=phone.user_id, platform=phone.platform, updated_at=now)
        stmt = insert(MobileDaily).values(**values)
        result = await db.scalars(stmt.on_conflict_do_update(
            index_elements=["user_id", "platform", "date", "metric"],
            set_={key: getattr(stmt.excluded, key) for key in ("value", "sources", "measured_at", "updated_at")},
            # An older phone snapshot must not replace a later weigh-in from
            # another device on the same platform. Same-time corrections work.
            where=((MobileDaily.measured_at.is_(None) | (stmt.excluded.measured_at >= MobileDaily.measured_at))
                   if day.metric in ("weight", "body_fat") else None),
        ).returning(MobileDaily), execution_options={"populate_existing": True})
        result.all()
    phone.last_sync_at = now
    await db.flush()
    return {"received": len(body.days), "checked_at": now.isoformat()}


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
    await db.execute(delete(MobileDevice).where(MobileDevice.user_id == user.id))
    return RedirectResponse("/mobile/devices", status_code=303)
