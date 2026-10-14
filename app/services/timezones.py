"""The athlete's local time: one zone per athlete, every « day » read in it.

`users.timezone` holds an IANA name (« Europe/Paris »), set from what the athlete's
services report (Strava's athlete profile, the phone's night packets); until then
Europe/Paris, the app's home. Fixed UTC offsets are never stored: they go wrong at
the next daylight-saving change.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DEFAULT_ZONE = "Europe/Paris"


def zone_of(name: str | None) -> ZoneInfo:
    """A ZoneInfo for an IANA name, the default zone for anything unknown."""
    if name:
        try:
            return ZoneInfo(name)
        except (ZoneInfoNotFoundError, ValueError):
            pass
    return ZoneInfo(DEFAULT_ZONE)


def athlete_zone(user) -> ZoneInfo:
    return zone_of(getattr(user, "timezone", None))


def local_date(moment: datetime, zone: ZoneInfo) -> date:
    """The local day of an instant (naive datetimes are read as UTC, as the app stores them)."""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(zone).date()


def athlete_today(user, now: datetime | None = None) -> date:
    return local_date(now or datetime.now(timezone.utc), athlete_zone(user))


_STRAVA_TZ = re.compile(r"\)\s*([A-Za-z_]+(?:/[A-Za-z0-9_+\-]+)+)\s*$")


def zone_from_strava(value: str | None) -> str | None:
    """« (GMT+01:00) Europe/Paris » → « Europe/Paris »; None when it is not a known IANA zone."""
    if not value:
        return None
    m = _STRAVA_TZ.search(value.strip())
    name = m.group(1) if m else value.strip()
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return None
    return name
