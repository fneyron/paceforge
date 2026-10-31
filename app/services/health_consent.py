"""Explicit consent to health data (GDPR art. 9.2.a): its text, its version, and where a new health source starts.

The consent is the athlete's, dated and versioned on the account (users.health_consent_at, health_consent_version).
A new version of the text below means a new consent: bump HEALTH_CONSENT_VERSION when its meaning changes.
Withdrawing it (Réglages) disconnects the health sources and erases the health data, never the activities or plans.
Without it, no health data is written (allowed(): health.store_daily and store_samples, the phone's nights and
days, the feel), while the activities keep coming.
"""
from datetime import datetime, timezone
from urllib.parse import urlencode, urlsplit, parse_qsl
from zoneinfo import ZoneInfo

from sqlalchemy import delete, exists, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.user import User

# the bike plans (features.cycling_enabled, off in production) use the latest weigh-in too: said only while they are
# open, and the version follows, so opening them asks everyone again (the public pages make no bike claim otherwise)
_BIKE = bool(settings.CYCLING_ENABLED)
# 2026-10-31: the list as collected (naps, sleep phases, kcal and exercise minutes; no VO2 max), the weigh-ins' other
# uses, what a withdrawal keeps
HEALTH_CONSENT_VERSION = "2026-10-31" + ("-v" if _BIKE else "")

NBSP = "\u00a0"
TITLE = "Tes données de santé"
# what is collected: what the syncs write (health.store_daily: COROS, Garmin; apple_nights), the phone's days
# (mobile.Daily), the feel, the lactate tests
DATA = ("sommeil et ses phases, siestes, VFC, fréquence cardiaque de nuit et du jour, respiration, pas, calories, "
        "minutes d’exercice, stress et altitude (Garmin), poids, masse grasse, ressenti et tests de lactate")
# what a withdrawal keeps (settings.withdraw_health_consent erases the health data only)
KEPT = (f"Strava, tes activités avec leur fréquence cardiaque, tes plans, le poids et la FC{NBSP}max saisis dans "
        "Réglages")
TEXT = (
    f"PaceForge enregistre les données de santé de tes sources et de tes saisies{NBSP}: {DATA}.",
    # the weigh-ins: athlete_weight (race_prep's carbohydrates, the bike plans' power)
    f"Elles servent à Santé et à la préparation de tes courses{NBSP}; ta dernière pesée sert aussi à tes glucides "
    f"d’avant-course{' et à ta puissance à vélo' if _BIKE else ''}. Elles ne sont ni vendues ni partagées.",
    f"Tu peux retirer ton accord à tout moment dans Réglages{NBSP}: tes sources sont déconnectées, tes téléphones "
    f"dissociés et ces données effacées. Restent {KEPT}.",
)
LABEL = "J’accepte que PaceForge traite mes données de santé pour ce suivi."
MISSING = "Coche la case pour continuer."
# a phone's push refused without the consent (403): the app keeps its measurements and sends them again later
PHONE_REFUSED = {"detail": "Accepte le suivi de tes données de santé dans PaceForge pour envoyer tes mesures.",
                 "code": "health_consent_required"}

# Where the consent page may send the athlete back (anything else: Santé). Only these paths, and for each only
# these query values: the page never redirects to a place its link chose.
_NEXT_QUERY = {
    "/coros/connect": {"region": ("monde", "europe", "chine"), "next": ("devices", "app")},
    "/sante": {}, "/settings": {}, "/sante/lactate": {}, "/connections/coros": {}, "/connections/garmin": {},
}
_FRAGMENTS = ("coros", "garmin", "connections", "donnees", "nouveau-test", "ressenti")
# asked by a source connection: « Pas maintenant » goes back to the connections, never to the page that asks again
_SOURCES = ("/coros/connect", "/connections/coros", "/connections/garmin")
DEFAULT_NEXT = "/sante"


def texts() -> dict:
    """What a page shows of the consent: its title, its paragraphs, the box's label, its error."""
    return {"consent_title": TITLE, "consent_text": TEXT, "consent_label": LABEL, "consent_missing": MISSING}


def given(user: User | None) -> bool:
    """Whether the athlete consented to the current text."""
    return bool(user and user.health_consent_at and user.health_consent_version == HEALTH_CONSENT_VERSION)


async def allowed(db: AsyncSession, user_id: int) -> bool:
    """given() for an account known by its id (a sync, a phone): asked before any health data is written."""
    row = (await db.execute(select(User.health_consent_at, User.health_consent_version).where(
        User.id == user_id))).one_or_none()
    return bool(row and row[0] and row[1] == HEALTH_CONSENT_VERSION)


def record(user: User) -> None:
    user.health_consent_at = datetime.now(timezone.utc)
    user.health_consent_version = HEALTH_CONSENT_VERSION


def safe_next(raw: str | None) -> str:
    """`raw` when it is one of this site's allowed targets (rebuilt from its parts), else Santé."""
    try:
        parts = urlsplit((raw or "").strip())
    except ValueError:
        return DEFAULT_NEXT
    allowed = _NEXT_QUERY.get(parts.path)
    if parts.scheme or parts.netloc or allowed is None:
        return DEFAULT_NEXT
    query = [(k, v) for k, v in parse_qsl(parts.query) if v in allowed.get(k, ())]
    url = parts.path + (f"?{urlencode(query)}" if query else "")
    return url + (f"#{parts.fragment}" if parts.fragment in _FRAGMENTS else "")


def consent_url(next_url: str) -> str:
    return "/sante/consentement?" + urlencode({"next": safe_next(next_url)})


def not_now_url(next_url: str) -> str:
    """« Pas maintenant »: back to `next` (the feel, a lactate test, Réglages), or to Réglages › Connexions when a
    source connection asked (its page would ask again; the phone app opens its own connections screen there)."""
    target = safe_next(next_url)
    parts = urlsplit(target)
    if parts.path in _SOURCES or (parts.path == "/settings" and parts.fragment in ("coros", "garmin")):
        return "/settings#connections"
    return target


def given_on(user: User) -> str:
    """The day of the consent, in the athlete's zone: « 25 octobre 2026 »."""
    from app.services.model_stats import fmt_date

    at = user.health_consent_at
    if at is None:
        return ""
    at = at if at.tzinfo else at.replace(tzinfo=timezone.utc)
    try:
        at = at.astimezone(ZoneInfo(user.timezone or "Europe/Paris"))
    except (ValueError, KeyError):
        pass
    return fmt_date(at.date().isoformat())


async def holds_health_data(db: AsyncSession, user_id: int) -> bool:
    """A health source linked (COROS, Garmin, a paired phone) or health data stored (measurements, phone days,
    lactate tests): the Santé page then asks for the consent the athlete has not given yet."""
    from app.models.coros import CorosConnection
    from app.models.garmin import GarminConnection
    from app.models.health import HealthMetric, HealthSample
    from app.models.lactate import LactateTest
    from app.models.mobile import MobileDaily, MobileDevice

    held = or_(*(exists().where(model.user_id == user_id) for model in (
        CorosConnection, GarminConnection, MobileDevice, HealthMetric, HealthSample, MobileDaily, LactateTest)))
    return bool(await db.scalar(select(held)))


async def erase_health_data(db: AsyncSession, user_id: int) -> None:
    """The health data of the account, and its phones' access: measurements (watches, phones, the self-report),
    raw samples, phone days, lactate tests. Activities, plans and the profile stay."""
    from app.models.health import HealthMetric, HealthSample
    from app.models.lactate import LactateTest
    from app.models.mobile import MobileDaily, MobileDevice

    for model in (HealthMetric, HealthSample, MobileDaily, LactateTest, MobileDevice):
        await db.execute(delete(model).where(model.user_id == user_id))
    await db.execute(update(User).where(User.id == user_id).values(
        health_consent_at=None, health_consent_version=None, health_key_hash=None, health_key_prefix=None,
        health_key_created_at=None, health_last_push_at=None, health_last_push_count=None))
