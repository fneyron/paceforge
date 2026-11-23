"""The athlete's data, downloaded from Réglages (GET /settings/export): one ZIP of JSON files (RGPD access and
portability).

What it holds: the account (never its password hash, email or reset tokens, Strava tokens or keys, the Apple
Health key; with the consent to health data's date and version), every activity with its laps, splits, streams and
the provider's payload, the health measurements and samples, the phone's daily values, the former feel's answers, the
race plans with their checkpoints (waypoints, cut-offs, pinned times), the lactate tests, the nutrition products,
which services and phones are linked, with their dates (never a token, a pairing code or a phone's key), the
Coaching plan with its sessions, ressenti and notes, who it is shared with and whom the account follows (first names
and dates: never the calendar feed's token nor a coach's code), the API
keys' names, first characters, scopes and dates (never a key nor its hash), and the account's rows in the tables of the features switched off, while the database still has them (legacy_tables).

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

from sqlalchemy import case, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.activity import Activity
from app.models.api_token import ApiToken
from app.models.coaching import (
    CoachingBusy,
    CoachingCalendar,
    CoachingInvite,
    CoachingLink,
    CoachingPlan,
    CoachingSession,
)
from app.models.coros import CorosConnection
from app.models.garmin import GarminConnection
from app.models.health import HealthMetric, HealthSample
from app.models.lactate import LactateTest
from app.models.mobile import MobileDaily, MobileDevice
from app.models.nutrition import NutritionProduct
from app.models.official_connection import OfficialConnection
from app.models.route import Route, RouteCheckpoint
from app.models.user import User
from app.services.health import FEEL  # the former feel's answers (no longer written since 2026-10-31): their own file

# rows per batch: a batch is what the export holds in memory (an activity with its streams is ~0.3 MB, a plan with
# its course up to ~2 MB)
BATCH, ACTIVITY_BATCH, ROUTE_BATCH = 500, 25, 10

# the account's own fields; every other users column is a secret, a token or internal bookkeeping
ACCOUNT_FIELDS = ("id", "email", "email_verified", "firstname", "lastname", "profile_picture_url", "created_at",
                  "updated_at", "timezone", "weight_kg", "weight_set_at", "max_heart_rate", "ftp_watts",
                  "health_source", "preferred_sports", "weekly_volume_target_km", "auto_post_comments", "race_name",
                  "race_date", "race_distance_km", "initial_sync_done", "health_consent_at", "health_consent_version")
LINK_FIELDS = ("connected_at", "last_sync_at", "last_error", "needs_reauth")
# an official link (app.connectors): its metadata, never its tokens
OFFICIAL_FIELDS = ("provider", "provider_user_id", "scopes", "status", "created_at", "updated_at", "last_push_at",
                   "last_sync_at", "last_error")
PHONE_FIELDS = ("platform", "created_at", "last_seen_at", "last_sync_at", "expires_at")
API_KEY_FIELDS = ("name", "prefix", "scopes", "created_at", "last_used_at", "revoked_at")  # never token_hash

README = """Tes données PaceForge, au format JSON (UTF-8). Dates en ISO 8601. Les heures portent leur fuseau, sauf
les heures des nuits et des échantillons de santé (sante_echantillons.json), à l’heure locale ; les données reçues
des services gardent leur format.

compte.json             ton compte : identité, réglages, objectif de course, consentement aux données de santé
connexions.json         les services, téléphones et clés d’API liés, et leurs dates (jamais un jeton ni une clé)
activites.json          tes activités : résumé, tours, flux (fréquence cardiaque, GPS…), données reçues du service
sante_mesures.json      tes mesures de santé par jour (sommeil, VFC, fréquence cardiaque de nuit, pas…)
sante_echantillons.json les phases de sommeil détaillées (Garmin)
ressenti.json           tes réponses à l’ancien « Ressenti du jour », fonction retirée le 31 octobre 2026
telephone_jours.json    les valeurs du jour envoyées par ton téléphone (pas, poids…)
parcours.json           tes parcours et plans de course, avec leurs points de passage
lactate.json            tes tests de lactate
nutrition.json          tes produits de nutrition
coaching.json           ton plan de coaching : objectif (une course ajoutée sans fichier : nom, date, distance, temps
                        visé), jours, volume, ce que le ressenti a changé, les personnes qui
                        suivent ton plan et celles dont tu suis le plan (prénoms et dates), ton agenda (l’hôte de chaque adresse, jamais
                        l’adresse, et tes heures occupées : début et fin)
coaching_seances.json   tes séances de coaching : jour, type, durée, statut, ressenti, note, commentaire de qui suit ton plan
anciennes_fonctions/    s’il y en a, ce qu’avaient enregistré les fonctions retirées (analyses, messages,
                        bilans de la semaine, plans générés, simulations)
"""
LEGACY_DIR = "anciennes_fonctions/"


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
    """Which services, phones and API keys are linked, since when, their last use: no token, code, key, key hash or
    app secret."""
    coros = await db.scalar(select(CorosConnection).where(CorosConnection.user_id == user.id))
    garmin = await db.scalar(select(GarminConnection).where(GarminConnection.user_id == user.id))
    official = (await db.scalars(select(OfficialConnection).where(OfficialConnection.user_id == user.id)
                                 .order_by(OfficialConnection.provider))).all()
    phones = (await db.scalars(select(MobileDevice).where(MobileDevice.user_id == user.id,
                                                          MobileDevice.token_hash.is_not(None))
                               .order_by(MobileDevice.created_at))).all()
    keys = (await db.scalars(select(ApiToken).where(ApiToken.user_id == user.id).order_by(ApiToken.id))).all()
    return {
        "strava": {"lie": user.has_strava_linked, "athlete_id": user.strava_athlete_id,
                   "application": ("la tienne" if user.has_own_strava_app else
                                   "celle de PaceForge" if user.strava_client_id else None),
                   "derniere_synchro": _plain(user.last_activity_poll_at)},
        "coros": {field: _plain(getattr(coros, field)) for field in LINK_FIELDS} if coros else None,
        "garmin": ({field: _plain(getattr(garmin, field)) for field in LINK_FIELDS + ("display_name",)}
                   if garmin else None),
        # the links through the providers' official programs (COROS Partner API, Garmin Connect Developer Program),
        # when there are some
        **({"connexions_officielles": [{field: _plain(getattr(link, field)) for field in OFFICIAL_FIELDS}
                                       for link in official]} if official else {}),
        "telephones": [{field: _plain(getattr(p, field)) for field in PHONE_FIELDS} for p in phones],
        "raccourci_apple_sante": {"cree_le": _plain(user.health_key_created_at),
                                  "dernier_envoi": _plain(user.health_last_push_at),
                                  "valeurs_du_dernier_envoi": user.health_last_push_count},
        "cles_api": [{field: _plain(getattr(k, field)) for field in API_KEY_FIELDS} for k in keys],
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


async def _legacy_batches(db: AsyncSession, table: str, user_id: int,
                          size: int = BATCH) -> AsyncIterator[list[dict[str, Any]]]:
    """The account's rows of a legacy table (legacy_tables: unmapped, its name from its constant), by keyset
    batches as _batches."""
    from app.services import legacy_tables

    query = text(f"SELECT * FROM {table} WHERE {legacy_tables.of_account(table)} AND id > :last "
                 "ORDER BY id LIMIT :size")
    last = 0
    while True:
        rows = (await db.execute(query, {"uid": user_id, "last": last, "size": size})).mappings().all()
        await db.commit()
        if not rows:
            return
        last = rows[-1]["id"]
        yield [_row(r) for r in rows]


# the plan's own fields: never the calendar feed's token (nor its hash)
COACHING_PLAN_FIELDS = ("intent", "level", "level_read", "route_id", "race_name", "race_date", "race_km", "race_target_s", "days", "long_day", "base_minutes", "start_monday", "generated_at", "adaptations",
                        "goal_state", "form_on_at", "form_state", "moments", "fixed", "created_at", "updated_at")


async def coaching(db: AsyncSession, user: User) -> dict[str, Any]:
    """The Coaching plan and its sharing: the plan's answers and what the ressenti changed; the coaches the athlete
    shared it with and the athletes the account follows (first names and dates); the codes made, by their dates
    (never a code, its hash, nor the calendar feed's token). The sessions are their own file."""
    plan = await db.scalar(select(CoachingPlan).where(CoachingPlan.user_id == user.id))
    links = (await db.execute(
        select(CoachingLink, User.firstname).join(
            User, User.id == case((CoachingLink.athlete_id == user.id, CoachingLink.coach_id),
                                  else_=CoachingLink.athlete_id))
        .where(or_(CoachingLink.athlete_id == user.id, CoachingLink.coach_id == user.id))
        .order_by(CoachingLink.id))).all()
    invites = (await db.scalars(select(CoachingInvite).where(CoachingInvite.athlete_id == user.id)
                                .order_by(CoachingInvite.id))).all()
    # « Mon agenda »: the calendars by their host and dates (an address is a secret of another service: never
    # written), a connected Google Agenda or Outlook by its service and dates (never its tokens), and the busy time
    # read from them (a start and an end: nothing else was ever kept)
    from urllib.parse import urlsplit

    calendars = (await db.scalars(select(CoachingCalendar).where(CoachingCalendar.user_id == user.id)
                                  .order_by(CoachingCalendar.id))).all()
    busy = (await db.execute(select(CoachingBusy.start_at, CoachingBusy.end_at, CoachingBusy.away).where(
        CoachingBusy.user_id == user.id).order_by(CoachingBusy.start_at, CoachingBusy.id))).all()
    return {
        "agenda": [{**({"hote": urlsplit(c.url or "").hostname} if c.provider == "ical" else
                       {"service": {"google": "Google Agenda", "microsoft": "Outlook"}.get(c.provider, c.provider)}),
                    "ajoute_le": _plain(c.created_at), "lu_le": _plain(c.fetched_at), "etat": c.status}
                   for c in calendars],
        "heures_occupees": [{"debut": _plain(a), "fin": _plain(b), "absence": bool(w)} for a, b, w in busy],
        "plan": {field: _plain(getattr(plan, field)) for field in COACHING_PLAN_FIELDS} if plan else None,
        "coachs": [{"prenom": name, "depuis": _plain(link.created_at)} for link, name in links
                   if link.athlete_id == user.id],
        "athletes_suivis": [{"prenom": name, "depuis": _plain(link.created_at)} for link, name in links
                            if link.coach_id == user.id],
        "codes": [{"cree_le": _plain(i.created_at), "expire_le": _plain(i.expires_at), "utilise_le": _plain(i.used_at)}
                  for i in invites],
    }


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

    def write(self, data: bytes, /) -> int:
        self.buffer += data
        return len(data)

    def flush(self) -> None:
        pass

    def close(self) -> None:
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
    from app.services import legacy_tables

    await db.refresh(user)  # every column loaded now (one written earlier in the request may be expired)
    uid = user.id
    head = {"compte.json": account(user), "connexions.json": await connections(db, user),
            "coaching.json": await coaching(db, user)}
    # only the legacy tables the account has rows in: a database migrated by alembic still has all of them
    legacy = [table for table in await legacy_tables.present(db) if await legacy_tables.holds(db, table, uid)]
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
        ("coaching_seances.json", _batches(db, CoachingSession, uid)),
        *((f"{LEGACY_DIR}{table}.json", _legacy_batches(db, table, uid)) for table in legacy),
    )
    for name, batches in sections:
        async for chunk in _json_array(zf, sink, name, batches):
            if chunk:
                yield chunk
    zf.close()
    yield sink.take()


def filename(today: date | None = None) -> str:
    return f"paceforge-mes-donnees-{(today or datetime.now(timezone.utc).date()).isoformat()}.zip"
