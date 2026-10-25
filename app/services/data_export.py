"""The athlete's data, downloaded from Réglages (GET /settings/export): one ZIP of JSON files (RGPD access and
portability).

What it holds: the account (never its password hash, email or reset tokens, Strava tokens or keys, the Apple
Health key), every activity with its laps, splits, streams and the provider's payload, the health measurements and
samples, the phone's daily values, the « ressenti » answers, the race plans with their checkpoints (waypoints,
cut-offs, pinned times), the lactate tests, the nutrition products, and which services and phones are linked, with
their dates (never a token, a pairing code or a phone's key).

Streamed: the ZIP is written as it is sent (zipfile on an output that cannot seek: each entry ends with a data
descriptor), the tables read by keyset batches (a few rows at a time for activities and plans) and compressed off
the event loop; the session commits after each batch, so a slow download holds no database connection between
them. Memory stays at one batch, whatever the account's size.
"""
import asyncio
import json
import zipfile
from collections.abc import AsyncIterator, Iterable
from datetime import date, datetime, time, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.activity import Activity
from app.models.coros import CorosConnection
from app.models.garmin import GarminConnection
from app.models.health import HealthMetric, HealthSample
from app.models.lactate import LactateTest
from app.models.mobile import MobileDaily, MobileDevice
from app.models.nutrition import NutritionProduct
from app.models.route import Route, RouteCheckpoint
from app.models.user import User

# rows per batch: a batch is what the export holds in memory (an activity with its streams is ~0.3 MB, a plan with
# its course up to ~2 MB)
BATCH, ACTIVITY_BATCH, ROUTE_BATCH = 500, 25, 10
FEEL = "feel"  # the « ressenti » answers' metric in health_metrics (services/sante_checkin)

# the account's own fields; every other users column is a secret, a token or internal bookkeeping
ACCOUNT_FIELDS = ("id", "email", "email_verified", "firstname", "lastname", "profile_picture_url", "created_at",
                  "updated_at", "timezone", "weight_kg", "weight_set_at", "max_heart_rate", "ftp_watts",
                  "health_source", "preferred_sports", "weekly_volume_target_km", "auto_post_comments", "race_name",
                  "race_date", "race_distance_km", "initial_sync_done")
LINK_FIELDS = ("connected_at", "last_sync_at", "last_error", "needs_reauth")
PHONE_FIELDS = ("platform", "created_at", "last_seen_at", "last_sync_at", "expires_at")

README = """Tes données PaceForge, au format JSON (UTF-8). Dates en ISO 8601 ; les heures sans fuseau sont en UTC.

compte.json             ton compte : identité, réglages, objectif de course
connexions.json         les services et téléphones liés, et leurs dates (jamais un jeton ni une clé)
activites.json          tes activités : résumé, tours, flux (fréquence cardiaque, GPS…), données reçues du service
sante_mesures.json      tes mesures de santé par jour (sommeil, VFC, fréquence cardiaque au repos, poids…)
sante_echantillons.json les échantillons bruts de ces mesures
ressenti.json           tes réponses « ressenti » du matin
telephone_jours.json    les valeurs du jour envoyées par ton téléphone (pas, poids…)
parcours.json           tes parcours et plans de course, avec leurs points de passage
lactate.json            tes tests de lactate
nutrition.json          tes produits de nutrition
"""


def _plain(value: Any) -> Any:
    if isinstance(value, datetime | date | time):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, bytes | memoryview):  # a sealed secret: never exported
        return None
    return value


def _row(mapping: Any, skip: Iterable[str] = ()) -> dict[str, Any]:
    skipped = set(skip)
    return {key: _plain(value) for key, value in mapping.items() if key not in skipped}


def _dumps(value: Any, indent: int | None = None) -> bytes:
    return json.dumps(value, ensure_ascii=False, default=str, indent=indent).encode()


def account(user: User) -> dict[str, Any]:
    return {field: _plain(getattr(user, field)) for field in ACCOUNT_FIELDS}


async def connections(db: AsyncSession, user: User) -> dict[str, Any]:
    """Which services and phones are linked, since when, their last sync: no token, code, key or app secret."""
    coros = await db.scalar(select(CorosConnection).where(CorosConnection.user_id == user.id))
    garmin = await db.scalar(select(GarminConnection).where(GarminConnection.user_id == user.id))
    phones = (await db.scalars(select(MobileDevice).where(MobileDevice.user_id == user.id,
                                                          MobileDevice.token_hash.is_not(None))
                               .order_by(MobileDevice.created_at))).all()
    return {
        "strava": {"lie": user.has_strava_linked, "athlete_id": user.strava_athlete_id,
                   "application": ("la tienne" if user.has_own_strava_app else
                                   "celle de PaceForge" if user.strava_client_id else None),
                   "derniere_synchro": _plain(user.last_activity_poll_at)},
        "coros": {field: _plain(getattr(coros, field)) for field in LINK_FIELDS} if coros else None,
        "garmin": ({field: _plain(getattr(garmin, field)) for field in LINK_FIELDS + ("display_name",)}
                   if garmin else None),
        "telephones": [{field: _plain(getattr(p, field)) for field in PHONE_FIELDS} for p in phones],
        "raccourci_apple_sante": {"cree_le": _plain(user.health_key_created_at),
                                  "dernier_envoi": _plain(user.health_last_push_at),
                                  "valeurs_du_dernier_envoi": user.health_last_push_count},
    }


async def _batches(db: AsyncSession, model: Any, user_id: int, *conditions: Any,
                   size: int = BATCH) -> AsyncIterator[list[dict[str, Any]]]:
    """The account's rows of `model` by keyset batches (plain dicts, no ORM objects kept), committing after each
    batch: the connection goes back to the pool while the batch is compressed and sent."""
    table = model.__table__
    last = 0
    while True:
        rows = (await db.execute(select(table).where(table.c.user_id == user_id, table.c.id > last, *conditions)
                                 .order_by(table.c.id).limit(size))).mappings().all()
        await db.commit()
        if not rows:
            return
        last = rows[-1]["id"]
        yield [_row(r) for r in rows]


async def _routes(db: AsyncSession, user_id: int) -> AsyncIterator[list[dict[str, Any]]]:
    """Each plan with its checkpoints (waypoints, aid stations, cut-offs, pinned passage times)."""
    checkpoints = RouteCheckpoint.__table__
    async for batch in _batches(db, Route, user_id, size=ROUTE_BATCH):
        ids = [route["id"] for route in batch]
        rows = (await db.execute(select(checkpoints).where(checkpoints.c.route_id.in_(ids))
                                 .order_by(checkpoints.c.route_id, checkpoints.c.distance_km))).mappings().all()
        await db.commit()
        by_route: dict[int, list[dict[str, Any]]] = {}
        for row in rows:
            by_route.setdefault(row["route_id"], []).append(_row(row))
        yield [{**route, "checkpoints": by_route.get(route["id"], [])} for route in batch]


class _Sink:
    """The ZIP's output: what zipfile wrote since the last take (it cannot seek: data descriptors)."""

    def __init__(self) -> None:
        self.buffer = bytearray()

    def write(self, data: bytes) -> int:
        self.buffer += data
        return len(data)

    def flush(self) -> None:
        pass

    def take(self) -> bytes:
        out = bytes(self.buffer)
        self.buffer.clear()
        return out


async def _json_array(zf: zipfile.ZipFile, sink: _Sink, name: str,
                      batches: AsyncIterator[list[dict[str, Any]]]) -> AsyncIterator[bytes]:
    with zf.open(name, "w", force_zip64=True) as entry:
        first = True
        entry.write(b"[")
        async for batch in batches:
            chunk = b",\n".join(_dumps(row) for row in batch)
            await asyncio.to_thread(entry.write, (b"\n" if first else b",\n") + chunk)  # deflate off the loop
            first = False
            yield sink.take()
        entry.write(b"\n]\n" if not first else b"]\n")
    yield sink.take()


async def export_zip(db: AsyncSession, user: User) -> AsyncIterator[bytes]:
    """The ZIP's bytes, as they are made."""
    await db.refresh(user)  # every column loaded now (one written earlier in the request may be expired)
    uid = user.id
    head = {"compte.json": account(user), "connexions.json": await connections(db, user)}
    await db.commit()
    sink = _Sink()
    zf = zipfile.ZipFile(sink, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6)
    zf.writestr("LISEZMOI.txt", README)
    for name, value in head.items():
        zf.writestr(name, _dumps(value, indent=1))
    yield sink.take()
    sections: tuple[tuple[str, AsyncIterator[list[dict[str, Any]]]], ...] = (
        ("activites.json", _batches(db, Activity, uid, size=ACTIVITY_BATCH)),
        ("sante_mesures.json", _batches(db, HealthMetric, uid, HealthMetric.__table__.c.metric != FEEL)),
        ("sante_echantillons.json", _batches(db, HealthSample, uid)),
        ("ressenti.json", _batches(db, HealthMetric, uid, HealthMetric.__table__.c.metric == FEEL)),
        ("telephone_jours.json", _batches(db, MobileDaily, uid)),
        ("parcours.json", _routes(db, uid)),
        ("lactate.json", _batches(db, LactateTest, uid)),
        ("nutrition.json", _batches(db, NutritionProduct, uid)),
    )
    for name, batches in sections:
        async for chunk in _json_array(zf, sink, name, batches):
            if chunk:
                yield chunk
    zf.close()
    yield sink.take()


def filename(today: date | None = None) -> str:
    return f"paceforge-mes-donnees-{(today or datetime.now(timezone.utc).date()).isoformat()}.zip"
