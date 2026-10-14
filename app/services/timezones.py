"""The athlete's local time: one zone per athlete, every « day » read in it.

`users.timezone` holds an IANA name (« Europe/Paris »), set from what the athlete's
services report (Strava's athlete profile, the phone's night packets); until then
Europe/Paris, the app's home. Fixed UTC offsets are never stored: they go wrong at
the next daylight-saving change.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone, tzinfo
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


def known_zone(name: str | None) -> ZoneInfo | None:
    """A ZoneInfo for a known IANA name, else None."""
    if not name:
        return None
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return None


def clock_of(name: str | None, phone_zone: str | None = None,
             offset_at: tuple[float, datetime] | None = None) -> tzinfo:
    """The clock an athlete's days are read on, one rule for Santé and Activités (sante_training.athlete_clock
    reads its inputs; the second back-end review, 2026-10-14):
    1. users.timezone (`name`), when set;
    2. else the IANA zone of their latest phone night (`phone_zone`);
    3. else Europe/Paris, when their latest activity's UTC offset (`offset_at`: seconds east of UTC, and that
       activity's start) is Paris's at that moment, or when no offset is known — Paris's clock changes with daylight
       saving time, where a fixed offset is an hour off after 25 October;
    4. else that fixed offset (an athlete abroad: right until their own clock changes)."""
    if name:
        return zone_of(name)
    if (phone := known_zone(phone_zone)) is not None:
        return phone
    home = ZoneInfo(DEFAULT_ZONE)
    if offset_at is None:
        return home
    offset, at = offset_at
    at = at if at.tzinfo else at.replace(tzinfo=timezone.utc)
    if abs(at.astimezone(home).utcoffset().total_seconds() - offset) < 60:
        return home
    return timezone(timedelta(seconds=offset))


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
