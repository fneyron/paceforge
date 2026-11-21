"""Official connectors: the COROS Partner API, the Garmin Connect Developer Program, the Wahoo Cloud API and Polar
AccessLink v3 (docs/official-connectors.md).

Off until the provider's credentials arrive. A connector is ACTIVE only when its *_ENABLED flag is on and its client
id and secret are set (config.Settings.coros_partner_ready, garmin_connect_ready, wahoo_cloud_ready,
polar_accesslink_ready); until then its routes answer 404,
no page shows it, nothing is scheduled and nothing calls the provider. OFFICIAL_CONNECTORS_PILOT_USERS limits who
sees and can start them (a first try on the owner's own account); links already made keep receiving their data.

- base: OAuth (state, PKCE where the provider wants it), encrypted tokens and their refresh (rotation safe),
  disconnecting (the provider's deregistration too), the link's status, activities into Activity (one row per outing).
- coros_partner, garmin_connect, wahoo_cloud, polar_accesslink: what each provider says and how it maps into
  PaceForge. Wahoo and Polar bring activities only (no health data: no consent to health data asked for them).
- fit: the laps and session of a FIT file (a COROS, Wahoo or Polar workout's detail).
This module stays light (no database, no HTTP): config, the schedule and the templates read it.
"""
from celery.schedules import crontab

from app.config import settings

PROVIDERS = ("coros", "garmin", "wahoo", "polar")
NAMES = {"coros": "COROS", "garmin": "Garmin", "wahoo": "Wahoo", "polar": "Polar"}
# the ones that bring health data (nights, HRV, daily data): the consent to health data before connecting, and
# withdrawing it lets them go; Wahoo and Polar bring activities only, as Strava
HEALTH_PROVIDERS = ("coros", "garmin")


def ready(provider: str) -> bool:
    """Enabled, with its client id and secret."""
    if provider == "coros":
        return settings.coros_partner_ready
    if provider == "garmin":
        return settings.garmin_connect_ready
    if provider == "wahoo":
        return settings.wahoo_cloud_ready
    if provider == "polar":
        return settings.polar_accesslink_ready
    return False


def pilot_ids() -> set[int]:
    return {int(x) for x in settings.OFFICIAL_CONNECTORS_PILOT_USERS.replace(" ", "").split(",") if x.isdigit()}


def visible_to(provider: str, user_id: int | None) -> bool:
    """Whether this account may see and start the official connection (active, and in the pilot when there is one)."""
    if not ready(provider):
        return False
    pilot = pilot_ids()
    return not pilot or user_id in pilot


def active_providers() -> list[str]:
    return [p for p in PROVIDERS if ready(p)]


def beat_schedule() -> dict:
    """What celery beat runs for the official connectors: nothing while none is active (read once, when the worker
    starts: switching a connector on or off takes a redeploy, as any setting)."""
    if not active_providers():
        return {}
    # one hourly round, off the other rounds' minutes (the poll's :07, :17… :57, COROS :12, purge :37, Garmin :42):
    # the COROS, Wahoo and Polar pulls that are due and the tokens to refresh before they lapse
    return {"official-connectors": {"task": "paceforge.official_round", "schedule": crontab(minute=22)}}
