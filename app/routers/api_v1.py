"""The personal API (owner, 2026-11-15: « chaque utilisateur crée une clé dans Réglages et lit ses données en
JSON »): read-only, versioned under /api/v1, one athlete's own data per key.

A call carries its key in `Authorization: Bearer pf_…` only: a key in the query string is refused (and masked in
the logs, app.log_redaction). The key is looked up by its SHA-256 (api_tokens), a revoked one refused at once; each
key has its budget (60 calls a minute, 2 000 a UTC day: fixed windows, 429 with Retry-After), an address its budget
of refused keys, an account its budget of plan computations. Its last use is written at most every SEEN_EVERY, and
committed before the answer (get_db commits only once the response is sent). The health data need the key's
« health » scope and the account's current consent to them (health_consent), checked at every call; the former
feel's rows are never read. Anything not the caller's is a 404, like anything that does not exist.

Field names are English snake_case and stable; dates ISO 8601 with their offset; SI units (metres, seconds, m/s),
heart rates in bpm, HRV in ms; lists by pages of `limit` (100 at most) with an opaque `cursor`. The response models
below are the contract (FastAPI drops any field they do not name) and the source of /api/v1/openapi.json; the
examples the documentation page (/developpeurs) prints are checked against them (tests/test_api_v1.py).
"""
import base64
import binascii
import json
import logging
import math
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Generic, Literal, TypeVar

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import defer
from starlette.templating import Jinja2Templates

from app.config import GOOGLE_SITE_VERIFICATION, settings
from app.dependencies import get_db, get_optional_user
from app.exceptions import ApiError
from app.models.activity import Activity
from app.models.api_token import ApiToken
from app.models.health import HealthMetric
from app.models.mobile import MobileDaily
from app.models.route import Route, RouteCheckpoint
from app.models.user import User
from app.routers.mobile import PhoneLimiter
from app.services import api_tokens, health_consent
from app.services.auth import QUOTAS, client_ip, over_quota

logger = logging.getLogger(__name__)
templates = Jinja2Templates(directory="app/templates")
templates.env.globals["google_site_verification"] = GOOGLE_SITE_VERIFICATION  # /developpeurs' head

NBSP = " "
MAX_PAGE, DEFAULT_PAGE = 100, 50
MAX_HEALTH_DAYS = 366
SEEN_EVERY = timedelta(minutes=5)  # a key's last use is written at most this often
PER_MINUTE, PER_DAY = 60, 2000
minute_limit = PhoneLimiter(limit=PER_MINUTE, window=60, prefix="api:rate:minute")
day_limit = PhoneLimiter(limit=PER_DAY, window=86400, prefix="api:rate:day")
# a query parameter that looks like a credential: the key goes in the Authorization header, never in a URL
SECRET_PARAM = re.compile(r"(?i)token|key|secret|authorization|bearer")


# ── the contract ─────────────────────────────────────────────────────────────

class Error(BaseModel):
    detail: str
    code: str


ERRORS: dict[int | str, dict[str, Any]] = {status: {"model": Error} for status in (400, 401, 403, 404, 422, 429)}


class TokenInfo(BaseModel):
    name: str
    prefix: str
    scopes: list[str]
    created_at: datetime


class Me(BaseModel):
    id: int
    firstname: str | None
    timezone: str
    units: Literal["metric"] = "metric"
    health_consent: bool
    token: TokenInfo


class PlanSummary(BaseModel):
    id: int
    name: str
    sport: str
    race_date: date | None
    start_time: str
    distance_m: float
    elevation_gain_m: float
    elevation_loss_m: float
    objective_s: int | None
    checkpoints: int
    created_at: datetime
    updated_at: datetime


class PassageTime(BaseModel):
    elapsed_s: int
    clock: str
    day: int
    at: datetime | None


class PassageTimes(BaseModel):
    target: PassageTime
    fast: PassageTime | None = None
    safe: PassageTime | None = None


class Cutoff(BaseModel):
    clock: str
    elapsed_s: int | None
    margin_s: int | None
    at: datetime | None


class Passage(BaseModel):
    name: str
    distance_m: float
    elevation_m: float | None
    elevation_gain_m: float
    elevation_loss_m: float
    aid: Literal["water", "full", "base"] | None
    crew: bool
    drop_bag: bool
    stop_s: int
    pinned: bool
    pinned_elapsed_s: int | None
    cutoff: Cutoff | None
    times: PassageTimes


class Switch(BaseModel):
    name: str
    distance_m: float
    late_s: int | None


class Scenarios(BaseModel):
    fast_pct: float
    safe_pct: float
    finish_s: dict[Literal["target", "fast", "safe"], int]
    switch: Switch | None


class PlanDetail(PlanSummary):
    timezone: str | None
    start_at: datetime | None
    estimate_s: int | None
    plan_total_s: int | None
    basis: Literal["objective", "pinned", "estimate", "race_day"] | None
    scenarios: Scenarios | None
    passages: list[Passage]


class ActivitySummary(BaseModel):
    id: int
    name: str
    sport: str
    sport_group: Literal["run", "trail", "bike", "other"]
    start: datetime
    distance_m: float
    moving_time_s: int
    elapsed_time_s: int
    elevation_gain_m: float | None
    average_heartrate_bpm: float | None
    max_heartrate_bpm: float | None
    average_speed_mps: float | None
    max_speed_mps: float | None
    calories_kcal: float | None
    sources: list[Literal["strava", "garmin", "coros"]]


class Split(BaseModel):
    index: int
    distance_m: float
    moving_time_s: int | None
    elapsed_time_s: int | None
    elevation_difference_m: float | None
    average_speed_mps: float | None
    average_heartrate_bpm: float | None


class ActivityDetail(ActivitySummary):
    splits: list[Split]


class HealthValue(BaseModel):
    metric: str
    value: float
    unit: str
    source: str
    details: dict[str, Any] = Field(default_factory=dict)


class HealthDay(BaseModel):
    date: date
    metrics: list[HealthValue]


class HealthDaily(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    from_: date = Field(alias="from")
    to: date
    days: list[HealthDay]


T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    data: list[T]
    next_cursor: str | None


# ── the caller ───────────────────────────────────────────────────────────────

bearer = HTTPBearer(auto_error=False, scheme_name="Clé d’API",
                    description="Ta clé d’API (Réglages › Accès API) : Authorization: Bearer pf_…")
WWW = {"WWW-Authenticate": 'Bearer realm="PaceForge"'}
MISSING = f"Clé d’API manquante{NBSP}: envoie l’en-tête Authorization: Bearer pf_…"
INVALID = "Clé d’API invalide."
REVOKED = "Cette clé a été révoquée. Crée-en une autre dans Réglages › Accès API."
IN_URL = f"La clé se passe dans l’en-tête Authorization, jamais dans l’URL{NBSP}: révoque celle-ci si elle a circulé."
TOO_MANY_FAILURES = "Trop de clés refusées depuis cette adresse. Réessaie plus tard."
CURSOR_INVALID = f"Curseur invalide{NBSP}: reprends-le tel que la page précédente l’a donné."


@dataclass
class Caller:
    token: ApiToken
    user: User


def _aware(moment: datetime | None) -> datetime | None:
    """An instant as stored (SQLite keeps them naive, in UTC), to the second."""
    if moment is None:
        return None
    moment = moment.replace(tzinfo=timezone.utc) if moment.tzinfo is None else moment
    return moment.replace(microsecond=0)


async def _refused(request: Request, status: int, detail: str, code: str) -> ApiError:
    """An unknown or revoked key, counted per address: past QUOTAS["api_auth"] the address waits."""
    if await over_quota("api_auth", ip=client_ip(request)):
        return ApiError(429, TOO_MANY_FAILURES, "rate_limited", {"Retry-After": str(QUOTAS["api_auth"][0])})
    return ApiError(status, detail, code, WWW if status == 401 else None)


async def caller(request: Request, response: Response,
                 credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
                 db: AsyncSession = Depends(get_db)) -> Caller:
    response.headers["Cache-Control"] = "no-store"
    if any(SECRET_PARAM.search(name) for name in request.query_params):
        raise ApiError(400, IN_URL, "token_in_url")
    if credentials is None:
        raise ApiError(401, MISSING, "token_missing", WWW)
    raw = credentials.credentials
    if not api_tokens.SHAPE.fullmatch(raw):
        raise await _refused(request, 401, INVALID, "token_invalid")
    token = await db.scalar(select(ApiToken).where(ApiToken.token_hash == api_tokens.digest(raw)))
    if token is None:
        raise await _refused(request, 401, INVALID, "token_invalid")
    if token.revoked_at is not None:
        raise await _refused(request, 401, REVOKED, "token_revoked")
    key = token.token_hash[:16]  # the budget's key: never the key itself, nor an id a fresh database repeats
    for limit, per in ((minute_limit, "minute"), (day_limit, "jour")):
        if (retry := await limit.over(key)) is not None:
            raise ApiError(429, f"Trop de requêtes avec cette clé ({limit.limit} par {per}). "
                                f"Réessaie dans {retry}{NBSP}s.", "rate_limited", {"Retry-After": str(retry)})
    user = await db.get(User, token.user_id)
    if user is None:
        raise ApiError(401, INVALID, "token_invalid", WWW)
    if user.email and not user.email_verified:
        raise ApiError(403, "Confirme ton adresse e-mail pour utiliser l’API.", "email_unverified")
    now = datetime.now(timezone.utc)
    last = _aware(token.last_used_at)
    if last is None or now - last >= SEEN_EVERY:
        token.last_used_at = now
        await db.commit()  # now: no row stays locked while the answer is computed, nor after it is sent
    return Caller(token=token, user=user)


# ── helpers ──────────────────────────────────────────────────────────────────

def _cursor(*parts: Any) -> str:
    return base64.urlsafe_b64encode("|".join(str(p) for p in parts).encode()).decode().rstrip("=")


def _uncursor(cursor: str, n: int) -> list[str]:
    try:
        parts = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode().split("|")
    except (binascii.Error, UnicodeDecodeError, ValueError):
        parts = []
    if len(parts) != n:
        raise ApiError(400, CURSOR_INVALID, "invalid_cursor")
    return parts


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return float(value)


def _int(value: Any) -> int | None:
    number = _number(value)
    return int(round(number)) if number is not None else None


async def _zone(db: AsyncSession, user: User):
    from app.services import sante_training

    return await sante_training.athlete_clock(db, user.id, user)


def _zone_name(zone) -> str:
    return getattr(zone, "key", None) or str(zone)


def _clock(clock_s: int) -> str:
    rem = int(clock_s) % 86400
    return f"{rem // 3600:02d}:{rem % 3600 // 60:02d}"


# ── me ───────────────────────────────────────────────────────────────────────

router = APIRouter(prefix="/api/v1", tags=["API"], responses=ERRORS)


@router.get("/me", response_model=Me, summary="Ton compte et la clé utilisée")
async def me(c: Caller = Depends(caller), db: AsyncSession = Depends(get_db)):
    zone = await _zone(db, c.user)
    return {"id": c.user.id, "firstname": c.user.firstname, "timezone": _zone_name(zone), "units": "metric",
            "health_consent": health_consent.given(c.user),
            "token": {"name": c.token.name, "prefix": c.token.prefix, "scopes": c.token.scope_list,
                      "created_at": _aware(c.token.created_at)}}


# ── plans ────────────────────────────────────────────────────────────────────

def _start(route: Route) -> tuple[int, int]:
    """The plan's start time, as the plan computes it (06:00 when none is saved)."""
    return (route.start_hour if route.start_hour is not None else 6), (route.start_minute or 0)


def _race_date(route: Route) -> date | None:
    try:
        return date.fromisoformat(str(route.race_date)[:10]) if route.race_date else None
    except ValueError:
        return None


def _plan_summary(route: Route, objective_s: int | None, checkpoints: int) -> dict:
    hour, minute = _start(route)
    return {"id": route.id, "name": route.name, "sport": route.sport_type or "trail", "race_date": _race_date(route),
            "start_time": f"{hour:02d}:{minute:02d}", "distance_m": round(float(route.total_distance_km or 0) * 1000, 1),
            "elevation_gain_m": round(float(route.total_elevation_gain or 0)),
            "elevation_loss_m": round(float(route.total_elevation_loss or 0)),
            "objective_s": objective_s, "checkpoints": checkpoints,
            "created_at": _aware(route.created_at), "updated_at": _aware(route.updated_at)}


def _visible_routes(user: User):
    from app.features import hidden_sports

    query = select(Route).where(Route.user_id == user.id)
    return query.where(Route.sport_type.notin_(hidden_sports())) if hidden_sports() else query


async def _checkpoint_counts(db: AsyncSession, ids: list[int]) -> dict[int, int]:
    if not ids:
        return {}
    return dict((await db.execute(select(RouteCheckpoint.route_id, func.count(RouteCheckpoint.id))
                                  .where(RouteCheckpoint.route_id.in_(ids)).group_by(RouteCheckpoint.route_id))).all())


@router.get("/plans", response_model=Page[PlanSummary], summary="Tes plans de course, les plus récents d’abord")
async def plans(limit: int = Query(DEFAULT_PAGE, ge=1, le=MAX_PAGE), cursor: str | None = None,
                c: Caller = Depends(caller), db: AsyncSession = Depends(get_db)):
    from app.routers import simulator

    query = _visible_routes(c.user).options(
        defer(Route.course_json), defer(Route.weather_json), defer(Route.nutrition_json), defer(Route.reference_json))
    if cursor:
        last = _uncursor(cursor, 1)[0]
        if not last.isdigit():
            raise ApiError(400, CURSOR_INVALID, "invalid_cursor")
        query = query.where(Route.id < int(last))
    routes = list((await db.scalars(query.order_by(Route.id.desc()).limit(limit + 1))).all())
    more = len(routes) > limit
    routes = routes[:limit]
    ids = [rt.id for rt in routes]
    light = {rid: (imp, has) for rid, imp, has in (await db.execute(
        select(Route.id, Route.course_json["predicted_total_time_s"], Route.course_json.is_not(None))
        .where(Route.id.in_(ids)))).all()} if ids else {}
    for rt in routes:  # the objective as « Mes courses » reads it (an older page's saved estimate is none)
        imp, has = light.get(rt.id, (None, False))
        setattr(rt, "imported_estimate_s", imp if isinstance(imp, (int, float)) else 0)  # read-time metadata
        setattr(rt, "has_course", bool(has))
    await simulator._read_legacy_estimates(routes, db, c.user)
    counts = await _checkpoint_counts(db, ids)
    data = [_plan_summary(rt, simulator._objective_s(rt, legacy_s=getattr(rt, "legacy_estimate_s", 0),
                                                     imported_s=getattr(rt, "imported_estimate_s", 0)),
                          counts.get(rt.id, 0))
            for rt in routes]
    return {"data": data, "next_cursor": _cursor(routes[-1].id) if more and routes else None}


def _instant(b: dict, route: Route, elapsed: int | None) -> datetime | None:
    """The moment `elapsed` seconds after the start: in the race's zone (the plan's clock), else at the race's UTC
    offset when only that is known; None without a race date."""
    if elapsed is None:
        return None
    clock = b["clock"]
    if clock.zoned:
        return clock.instant(elapsed).replace(microsecond=0)
    offset = (b.get("race_clock") or {}).get("utc_offset_s")
    day = _race_date(route)
    if offset is None or day is None:
        return None
    start = datetime(day.year, day.month, day.day, tzinfo=timezone(timedelta(seconds=int(offset))))
    return start + timedelta(seconds=int(b["start_offset_s"]) + int(elapsed))


def _time(b: dict, route: Route, elapsed: int, clock_s: int | None) -> dict:
    clock_s = int(clock_s) if clock_s is not None else b["clock"].clock_s(elapsed)
    return {"elapsed_s": int(elapsed), "clock": _clock(clock_s), "day": clock_s // 86400,
            "at": _instant(b, route, elapsed)}


def _passages(snap: dict, route: Route) -> list[dict]:
    """One row per checkpoint and the finish: where (metres, D+ and D− so far), what is there, the cut-off and its
    margin, the pinned time, and the passage of each column (Cible, Optimiste, Sécurité; Cible alone once replanned
    on race day)."""
    b, sections, scenarios = snap["bundle"], snap["sections"], snap["scenarios"]
    clock = b["clock"]
    rows = scenarios["rows"] if scenarios else [None] * len(sections)
    out, gain, loss = [], 0.0, 0.0
    for s, row in zip(sections, rows, strict=True):
        gain += float(s.get("elevation_gain") or 0)
        loss += float(s.get("elevation_loss") or 0)
        if row is not None:
            times = {key: _time(b, route, row[f"{key}_elapsed_s"], row[f"{key}_s"]) for key in ("target", "fast", "safe")}
        else:
            plan = s.get("adjusted_elapsed_s") is not None or s.get("adjusted_clock_time_s") is not None
            elapsed = clock.elapsed_of(s, plan)
            times = {"target": _time(b, route, elapsed, s.get("adjusted_clock_time_s" if plan else "clock_time_s"))}
        cut = s.get("cutoff_elapsed_s")
        kind = s.get("kind") or "none"
        out.append({
            "name": "Arrivée" if s.get("end_checkpoint_index") is None and s is sections[-1] else s["end_name"],
            "distance_m": round(float(s["end_km"]) * 1000, 1),
            "elevation_m": _number(s.get("end_elevation")), "elevation_gain_m": round(gain), "elevation_loss_m": round(loss),
            "aid": kind if kind in ("water", "full", "base") else None,
            "crew": bool(s.get("crew")), "drop_bag": bool(s.get("drop_bag")), "stop_s": int(s.get("stop_s") or 0),
            "pinned": bool(s.get("pinned")), "pinned_elapsed_s": _int(s.get("pin_target_s")) if s.get("pinned") else None,
            "cutoff": ({"clock": s["cutoff_clock"], "elapsed_s": _int(cut), "margin_s": _int(s.get("cutoff_margin_s")),
                        "at": _instant(b, route, _int(cut))} if s.get("cutoff_clock") else None),
            "times": times,
        })
    return out


def _scenarios(scenarios: dict | None) -> dict | None:
    if not scenarios:
        return None
    switch = scenarios.get("switch")
    totals = scenarios["totals"]
    return {"fast_pct": float(scenarios["fast_pct"]), "safe_pct": float(scenarios["safe_pct"]),
            "finish_s": {"target": int(totals["target_s"]), "fast": int(totals["fast_s"]), "safe": int(totals["safe_s"])},
            "switch": ({"name": switch["name"], "distance_m": round(float(switch["km"]) * 1000, 1),
                        "late_s": scenarios.get("switch_late_s")} if switch else None)}


@router.get("/plans/{plan_id}", response_model=PlanDetail,
            summary="Un plan et ses temps de passage (Cible, Optimiste, Sécurité)")
async def plan(plan_id: int, c: Caller = Depends(caller), db: AsyncSession = Depends(get_db)):
    from app.routers import simulator

    route = await db.scalar(_visible_routes(c.user).where(Route.id == plan_id))
    if route is None:
        raise ApiError(404, "Ce plan n’existe pas.", "not_found")
    if await over_quota("api_plan", user=c.user.id):
        window, allowed = QUOTAS["api_plan"]
        raise ApiError(429, f"Trop de plans calculés ({allowed['user']} par heure). Réessaie plus tard.",
                       "rate_limited", {"Retry-After": str(window)})
    counts = await _checkpoint_counts(db, [route.id])
    # without a course (or for a triathlon, whose legs the page plans apart) the plan has no passage times
    detail: dict[str, Any] = {"timezone": None, "start_at": None, "estimate_s": None, "plan_total_s": None,
                              "basis": None, "scenarios": None, "passages": []}
    objective = simulator._objective_s(route)
    if route.course_json and route.sport_type != "triathlon":
        snap = await simulator._plan_snapshot(route, db, c.user)
        b = snap["bundle"]
        objective = b["objective_s"]
        passages = _passages(snap, route)
        detail.update(
            timezone=(b.get("race_clock") or {}).get("tz"), start_at=_instant(b, route, 0),
            estimate_s=_int(b.get("estimate_s")),
            plan_total_s=(_int(b.get("plan_total_s"))
                          or (passages[-1]["times"]["target"]["elapsed_s"] if passages else None)),
            basis=("race_day" if snap["live_applied"] else "pinned" if b.get("pinned") else
                   "objective" if objective is not None else "estimate"),
            scenarios=_scenarios(snap["scenarios"]), passages=passages)
    return {**_plan_summary(route, objective, counts.get(route.id, 0)), **detail}


# ── activities ───────────────────────────────────────────────────────────────

SOURCES = (("strava", "strava_activity_id"), ("garmin", "garmin_activity_id"), ("coros", "coros_activity_id"))
ACTIVITY_COLUMNS = (Activity.id, Activity.name, Activity.sport_type, Activity.start_date, Activity.distance,
                    Activity.moving_time, Activity.elapsed_time, Activity.total_elevation_gain,
                    Activity.average_heartrate, Activity.max_heartrate, Activity.average_speed, Activity.max_speed,
                    Activity.calories, Activity.strava_activity_id, Activity.garmin_activity_id,
                    Activity.coros_activity_id)


def _activity(row: Any, zone) -> dict:
    from app.services.activity_dedupe import sport_group

    start = _aware(row.start_date)
    return {"id": row.id, "name": row.name, "sport": row.sport_type, "sport_group": sport_group(row.sport_type),
            "start": start.astimezone(zone) if start else None, "distance_m": float(row.distance or 0),
            "moving_time_s": int(row.moving_time or 0), "elapsed_time_s": int(row.elapsed_time or 0),
            "elevation_gain_m": _number(row.total_elevation_gain),
            "average_heartrate_bpm": _number(row.average_heartrate), "max_heartrate_bpm": _number(row.max_heartrate),
            "average_speed_mps": _number(row.average_speed), "max_speed_mps": _number(row.max_speed),
            "calories_kcal": _number(row.calories),
            "sources": [name for name, column in SOURCES if getattr(row, column) is not None]}


def _bound(value: str | None, zone, name: str, end: bool) -> datetime | None:
    """`since` / `until`: a day (YYYY-MM-DD, on the athlete's clock: `until`'s day included) or an ISO 8601 instant
    (without an offset, on the athlete's clock); in UTC, as the activities are stored."""
    if not value:
        return None
    try:
        if len(value) == 10:
            day = date.fromisoformat(value) + timedelta(days=1 if end else 0)
            moment = datetime(day.year, day.month, day.day, tzinfo=zone)
        else:
            moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
            moment = moment if moment.tzinfo else moment.replace(tzinfo=zone)
    except ValueError:
        raise ApiError(422, f"Paramètre invalide{NBSP}: {name} (AAAA-MM-JJ ou un instant ISO 8601).",
                       "invalid_parameter") from None
    return moment.astimezone(timezone.utc)


@router.get("/activities", response_model=Page[ActivitySummary], summary="Tes activités, les plus récentes d’abord")
async def activities(since: str | None = None, until: str | None = None, sport: str | None = Query(None, max_length=50),
                     limit: int = Query(DEFAULT_PAGE, ge=1, le=MAX_PAGE), cursor: str | None = None,
                     c: Caller = Depends(caller), db: AsyncSession = Depends(get_db)):
    from app.services.activity_dedupe import SPORT_GROUPS

    zone = await _zone(db, c.user)
    query = select(*ACTIVITY_COLUMNS).where(Activity.user_id == c.user.id)
    if (low := _bound(since, zone, "since", end=False)) is not None:
        query = query.where(Activity.start_date >= low)
    if (high := _bound(until, zone, "until", end=True)) is not None:
        query = query.where(Activity.start_date < high if until and len(until) == 10 else Activity.start_date <= high)
    if sport in SPORT_GROUPS:
        query = query.where(Activity.sport_type.in_(SPORT_GROUPS[sport]))
    elif sport == "other":
        query = query.where(Activity.sport_type.not_in([t for types in SPORT_GROUPS.values() for t in types]))
    elif sport:
        query = query.where(Activity.sport_type == sport)
    if cursor:
        at, last = _uncursor(cursor, 2)
        try:
            moment, last_id = datetime.fromisoformat(at).astimezone(timezone.utc), int(last)
        except ValueError:
            raise ApiError(400, CURSOR_INVALID, "invalid_cursor") from None
        query = query.where(or_(Activity.start_date < moment,
                                and_(Activity.start_date == moment, Activity.id < last_id)))
    rows = (await db.execute(query.order_by(Activity.start_date.desc(), Activity.id.desc()).limit(limit + 1))).all()
    more = len(rows) > limit
    rows = rows[:limit]
    next_cursor = None
    if more and rows:
        last_row = rows[-1]
        start = last_row.start_date if last_row.start_date.tzinfo else last_row.start_date.replace(tzinfo=timezone.utc)
        next_cursor = _cursor(start.astimezone(timezone.utc).isoformat(), last_row.id)
    return {"data": [_activity(row, zone) for row in rows], "next_cursor": next_cursor}


def _splits(raw: Any) -> list[dict]:
    """The per-kilometre splits as stored (Strava's shape, the watches' alike), their numbers only."""
    out = []
    for i, split in enumerate(raw if isinstance(raw, list) else [], start=1):
        if not isinstance(split, dict) or _number(split.get("distance")) is None:
            continue
        out.append({"index": _int(split.get("split")) or i, "distance_m": _number(split.get("distance")),
                    "moving_time_s": _int(split.get("moving_time")), "elapsed_time_s": _int(split.get("elapsed_time")),
                    "elevation_difference_m": _number(split.get("elevation_difference")),
                    "average_speed_mps": _number(split.get("average_speed")),
                    "average_heartrate_bpm": _number(split.get("average_heartrate"))})
    return out


@router.get("/activities/{activity_id}", response_model=ActivityDetail, summary="Une activité et ses kilomètres")
async def activity(activity_id: int, c: Caller = Depends(caller), db: AsyncSession = Depends(get_db)):
    row = (await db.execute(select(*ACTIVITY_COLUMNS, Activity.splits_metric).where(
        Activity.id == activity_id, Activity.user_id == c.user.id))).first()
    if row is None:
        raise ApiError(404, "Cette activité n’existe pas.", "not_found")
    return {**_activity(row, await _zone(db, c.user)), "splits": _splits(row.splits_metric)}


# ── health ───────────────────────────────────────────────────────────────────

# what Santé reads, per metric: its unit and the factor from the stored value (minutes of sleep → seconds)
HEALTH_METRICS = {"sleep": ("s", 60), "nap": ("s", 60), "hrv": ("ms", 1), "hr_night": ("bpm", 1),
                  "resp_night": ("rpm", 1), "hr_day": ("bpm", 1), "steps": ("count", 1), "stress": ("score", 1),
                  "weight": ("kg", 1)}
PHONE_METRICS = {"weight": ("kg", 1), "body_fat": ("%", 1), "steps": ("count", 1), "hr_day": ("bpm", 1),
                 "resp_day": ("rpm", 1)}
ORDER = ("sleep", "nap", "hrv", "hr_night", "resp_night", "hr_day", "resp_day", "steps", "stress", "weight", "body_fat")
PHONES = {"ios": "Apple Santé", "android": "Health Connect"}
CONSENT_REQUIRED = (f"Les données de santé demandent ton consentement{NBSP}: donne-le dans PaceForge "
                    "(Réglages › Données et confidentialité).")
SCOPE_REQUIRED = (f"Cette clé n’ouvre pas tes données de santé{NBSP}: crée une clé avec « Inclure mes données de "
                  "santé » dans Réglages › Accès API.")


def _local(raw: Any, tz_minutes: Any) -> str | None:
    """A night's local time as stored (« 2026-11-14T23:10 »), with its offset when the source gave one."""
    try:
        moment = datetime.fromisoformat(str(raw))
    except (TypeError, ValueError):
        return None
    minutes = _number(tz_minutes)
    if minutes is not None and moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone(timedelta(minutes=minutes)))
    return moment.isoformat()


def _health_details(metric: str, main_start, main_end, tz, stages, low, high, method, measured_at) -> dict:
    details: dict[str, Any] = {}
    if metric == "sleep":
        if (start := _local(main_start, tz)) and (end := _local(main_end, tz)):
            details.update(start=start, end=end)
        if isinstance(stages, dict):
            phases = {k: int(round(v * 60)) for k, v in stages.items() if isinstance(k, str) and _number(v) is not None}
            if phases:
                details["stages_s"] = phases
    if metric in ("hr_night", "hr_day"):
        if (value := _number(low)) is not None:
            details["min_bpm"] = value
        if (value := _number(high)) is not None:
            details["max_bpm"] = value
    if isinstance(method, str) and method:
        details["method"] = method
    if metric == "weight" and isinstance(measured_at, str) and measured_at:
        details["measured_at"] = measured_at
    return details


@router.get("/health/daily", response_model=HealthDaily, summary="Tes mesures de santé par jour")
async def health_daily(from_: date | None = Query(None, alias="from"), to: date | None = None,
                       c: Caller = Depends(caller), db: AsyncSession = Depends(get_db)):
    from app.services import sante_training

    if not health_consent.given(c.user):
        raise ApiError(403, CONSENT_REQUIRED, "health_consent_required")
    if not c.token.health:
        raise ApiError(403, SCOPE_REQUIRED, "health_scope_required")
    if to is None:
        to = await sante_training.athlete_today(db, c.user.id, zone=await _zone(db, c.user))
    first = from_ or to - timedelta(days=29)
    if first > to:
        raise ApiError(422, f"Paramètre invalide{NBSP}: from est après to.", "invalid_parameter")
    if (to - first).days + 1 > MAX_HEALTH_DAYS:
        raise ApiError(422, f"{MAX_HEALTH_DAYS} jours au plus par appel{NBSP}: découpe la période.", "invalid_parameter")
    details = HealthMetric.details
    # the keys the API returns only: a Garmin day row also carries its raw readings (≈ 22 KiB each)
    rows = (await db.execute(select(
        HealthMetric.date, HealthMetric.metric, HealthMetric.value, HealthMetric.source,
        details["main_start"].as_string(), details["main_end"].as_string(), details["tz"], details["stages"],
        details["min"], details["max"], details["method"].as_string(), details["measured_at"].as_string(),
    ).where(HealthMetric.user_id == c.user.id, HealthMetric.metric.in_(HEALTH_METRICS),  # never the former feel
            HealthMetric.date >= first, HealthMetric.date <= to))).all()
    days: dict[date, list[dict]] = {}
    for day, metric, value, source, *extra in rows:
        unit, factor = HEALTH_METRICS[metric]
        if _number(value) is None:
            continue
        days.setdefault(day, []).append({"metric": metric, "value": round(float(value) * factor, 3), "unit": unit,
                                         "source": source or "Inconnue",
                                         "details": _health_details(metric, *extra)})
    phone = (await db.execute(select(MobileDaily.date, MobileDaily.metric, MobileDaily.value, MobileDaily.platform,
                                     MobileDaily.sources, MobileDaily.measured_at).where(
        MobileDaily.user_id == c.user.id, MobileDaily.metric.in_(PHONE_METRICS),
        MobileDaily.date >= first, MobileDaily.date <= to))).all()
    for day, metric, value, platform, origins, measured_at in phone:
        if _number(value) is None:
            continue
        info: dict[str, Any] = {}
        if (at := _aware(measured_at)) is not None:
            info["measured_at"] = at.isoformat()
        if isinstance(origins, list):
            info["apps"] = [str(o) for o in origins][:20]
        days.setdefault(day, []).append({"metric": metric, "value": float(value), "unit": PHONE_METRICS[metric][0],
                                         "source": PHONES.get(platform, platform), "details": info})
    rank = {m: i for i, m in enumerate(ORDER)}
    return {"from": first, "to": to, "days": [
        {"date": day, "metrics": sorted(values, key=lambda v: (rank.get(v["metric"], 99), v["source"]))}
        for day, values in sorted(days.items())]}


# ── the schema and the documentation page ────────────────────────────────────

_schema: dict | None = None


@router.get("/openapi.json", include_in_schema=False)
async def openapi_schema():
    """The API's own OpenAPI document (the app's is off in production): public, like the documentation."""
    global _schema
    if _schema is None:
        from fastapi.openapi.utils import get_openapi

        _schema = get_openapi(
            title="PaceForge API", version="1.0.0",
            description="Lecture seule de tes données PaceForge. Documentation : /developpeurs",
            routes=[route for route in router.routes if getattr(route, "include_in_schema", False)],
            servers=[{"url": settings.BASE_URL.rstrip("/")}])
    return JSONResponse(_schema, headers={"Cache-Control": "public, max-age=3600"})


pages = APIRouter(include_in_schema=False)

# the documentation's examples, trimmed: tests/test_api_v1.py validates each against its response model
EXAMPLES: dict[str, Any] = {
    "me": {"id": 42, "firstname": "Camille", "timezone": "Europe/Paris", "units": "metric", "health_consent": True,
           "token": {"name": "Mon tableur", "prefix": "pf_Ab3dE", "scopes": ["read", "health"],
                     "created_at": "2026-11-15T09:12:00Z"}},
    "plans": {"data": [{"id": 7, "name": "Grand Trail des Crêtes", "sport": "trail", "race_date": "2027-06-12",
                        "start_time": "05:00", "distance_m": 82400.0, "elevation_gain_m": 4630,
                        "elevation_loss_m": 4630, "objective_s": 50400, "checkpoints": 9,
                        "created_at": "2026-10-02T18:40:11Z", "updated_at": "2026-11-14T21:03:52Z"}],
              "next_cursor": None},
    "plan": {"id": 7, "name": "Grand Trail des Crêtes", "sport": "trail", "race_date": "2027-06-12",
             "start_time": "05:00", "distance_m": 82400.0, "elevation_gain_m": 4630, "elevation_loss_m": 4630,
             "objective_s": 50400, "checkpoints": 9, "created_at": "2026-10-02T18:40:11Z",
             "updated_at": "2026-11-14T21:03:52Z", "timezone": "Europe/Paris",
             "start_at": "2027-06-12T05:00:00+02:00", "estimate_s": 52260, "plan_total_s": 50400,
             "basis": "objective",
             "scenarios": {"fast_pct": 6.0, "safe_pct": 9.0,
                           "finish_s": {"target": 50400, "fast": 47376, "safe": 54936},
                           "switch": {"name": "Col de la Croix", "distance_m": 49800.0, "late_s": 1800}},
             "passages": [{"name": "Col de la Croix", "distance_m": 49800.0, "elevation_m": 1778.0,
                           "elevation_gain_m": 2950, "elevation_loss_m": 2310, "aid": "full", "crew": True,
                           "drop_bag": False, "stop_s": 480, "pinned": False, "pinned_elapsed_s": None,
                           "cutoff": {"clock": "15:30", "elapsed_s": 37800, "margin_s": 7020,
                                      "at": "2027-06-12T15:30:00+02:00"},
                           "times": {"target": {"elapsed_s": 30780, "clock": "13:33", "day": 0,
                                                "at": "2027-06-12T13:33:00+02:00"},
                                     "fast": {"elapsed_s": 28933, "clock": "13:02", "day": 0,
                                              "at": "2027-06-12T13:02:13+02:00"},
                                     "safe": {"elapsed_s": 33550, "clock": "14:19", "day": 0,
                                              "at": "2027-06-12T14:19:10+02:00"}}}]},
    "activities": {"data": [{"id": 1532, "name": "Sortie longue", "sport": "TrailRun", "sport_group": "trail",
                             "start": "2026-11-14T08:03:00+01:00", "distance_m": 21430.0, "moving_time_s": 9120,
                             "elapsed_time_s": 9655, "elevation_gain_m": 1180.0, "average_heartrate_bpm": 142.0,
                             "max_heartrate_bpm": 171.0, "average_speed_mps": 2.35, "max_speed_mps": 5.1,
                             "calories_kcal": 1450.0, "sources": ["strava", "coros"]}],
                   "next_cursor": "MjAyNi0xMS0xNFQwNzowMzowMCswMDowMHwxNTMy"},
    "activity_split": {"index": 1, "distance_m": 1000.0, "moving_time_s": 402, "elapsed_time_s": 410,
                       "elevation_difference_m": 64.0, "average_speed_mps": 2.49, "average_heartrate_bpm": 131.0},
    "health": {"from": "2026-11-14", "to": "2026-11-14", "days": [{"date": "2026-11-14", "metrics": [
        {"metric": "sleep", "value": 26700, "unit": "s", "source": "Garmin",
         "details": {"start": "2026-11-13T23:12:00+01:00", "end": "2026-11-14T06:49:00+01:00",
                     "stages_s": {"deep": 5160, "light": 14280, "rem": 6060, "awake": 1200}}},
        {"metric": "hrv", "value": 58.3, "unit": "ms", "source": "Garmin", "details": {"method": "ln_mean_main"}},
        {"metric": "hr_night", "value": 47.0, "unit": "bpm", "source": "Garmin",
         "details": {"min_bpm": 42.0, "max_bpm": 61.0}},
        {"metric": "weight", "value": 64.8, "unit": "kg", "source": "Apple Santé",
         "details": {"measured_at": "2026-11-14T06:58:00Z", "apps": ["Balance"]}}]}]},
}


def _pretty(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)


@pages.get("/developpeurs", response_class=HTMLResponse)
async def developers(request: Request, user: User | None = Depends(get_optional_user)):
    """The API's documentation: public, indexable (main.PUBLIC_PAGES)."""
    return templates.TemplateResponse(request, "developers.html", context={
        "viewer": user, "base": settings.BASE_URL.rstrip("/"), "examples": {k: _pretty(v) for k, v in EXAMPLES.items()},
        "per_minute": PER_MINUTE, "per_day": f"{PER_DAY:,}".replace(",", NBSP), "max_page": MAX_PAGE,
        "plan_quota": QUOTAS["api_plan"][1]["user"], "max_keys": api_tokens.MAX_ACTIVE,
        "max_health_days": MAX_HEALTH_DAYS})
