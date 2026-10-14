"""Read-only HealthKit nights, kept per writer/device and per measurement method.

Raw intervals are validated and reduced here, never trusted as an already
computed recovery score. Explicit wake takes precedence; gaps remain unknown.
Daily phone averages never enter this pipeline.
"""
import hashlib
import math
import statistics
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.health import HealthMetric
from app.services.health import Daily

PREFIX = "Apple Santé:"
SDNN_METHOD = "apple_sleep_sdnn_v1"
HR_METHOD = "apple_sleep_hr_v1"
RESP_METHOD = "apple_sleep_resp_v1"


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Interval(Strict):
    start: datetime
    end: datetime
    stage: Literal["asleep", "core", "deep", "rem", "awake"]

    @model_validator(mode="after")
    def times(self):
        if self.start.tzinfo is None or self.end.tzinfo is None:
            raise ValueError("Fuseau requis.")
        if not timedelta(0) < self.end - self.start <= timedelta(hours=18):
            raise ValueError("Intervalle de sommeil invalide.")
        self.start = self.start.astimezone(timezone.utc)
        self.end = self.end.astimezone(timezone.utc)
        return self


class Reading(Strict):
    at: datetime
    value: float

    @model_validator(mode="after")
    def timestamp(self):
        if self.at.tzinfo is None:
            raise ValueError("Fuseau requis.")
        self.at = self.at.astimezone(timezone.utc)
        return self


class AppleNight(Strict):
    date: date
    source_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_name: str = Field(min_length=1, max_length=150)
    timezone: str = Field(min_length=1, max_length=100)
    sleep: list[Interval] = Field(default_factory=list, max_length=1000)
    naps: list[list[Interval]] = Field(default_factory=list, max_length=12)
    hr: list[Reading] = Field(default_factory=list, max_length=5000)
    hrv_sdnn: list[Reading] = Field(default_factory=list, max_length=1000)
    resp: list[Reading] = Field(default_factory=list, max_length=5000)

    @model_validator(mode="after")
    def validate_night(self):
        try:
            zone = ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError("Fuseau inconnu.") from None
        now = datetime.now(timezone.utc)
        if not now.astimezone(zone).date() - timedelta(days=90) <= self.date <= now.astimezone(zone).date():
            raise ValueError("Jour hors de la fenêtre autorisée.")
        if not self.sleep and not self.naps:
            raise ValueError("Aucun intervalle de sommeil.")
        for intervals in [self.sleep, *self.naps]:
            if len(intervals) > 1000:
                raise ValueError("Trop d’intervalles.")
            if not intervals:
                continue
            lo, hi = min(s.start for s in intervals), max(s.end for s in intervals)
            if hi > now or hi - lo > timedelta(hours=18) or hi.astimezone(zone).date() != self.date:
                raise ValueError("Fenêtre de sommeil incohérente.")
        for readings, low, high in ((self.hr, 25, 230), (self.hrv_sdnn, 1, 400), (self.resp, 4, 60)):
            if any(not low <= r.value <= high or r.at > now for r in readings):
                raise ValueError("Mesure nocturne invalide.")
        return self


def segments(intervals):
    """Disjoint measured intervals; wake wins, conflicting stages stay unspecified."""
    edges = sorted({t for s in intervals for t in (s.start, s.end)})
    out = []
    for a, b in zip(edges, edges[1:]):
        kinds = {s.stage for s in intervals if s.start <= a and b <= s.end}
        if not kinds:
            continue
        specific = kinds - {"asleep"}
        kind = "awake" if "awake" in kinds else next(iter(specific)) if len(specific) == 1 else "asleep"
        if out and out[-1][0] == kind and out[-1][2] == a:
            out[-1] = (kind, out[-1][1], b)
        else:
            out.append((kind, a, b))
    return out


def filtered(readings, segs):
    # Deduplicate timestamps; a contradictory copy does not get double weight.
    by_time = defaultdict(set)
    for r in readings:
        if any(k != "awake" and a <= r.at < b for k, a, b in segs):
            by_time[r.at].add(r.value)
    return sorted((at, next(iter(values))) for at, values in by_time.items() if len(values) == 1)


def reduce_night(packet: AppleNight):
    zone = ZoneInfo(packet.timezone)
    local = lambda t: t.astimezone(zone).replace(tzinfo=None).isoformat(timespec="seconds")
    rows = []
    segs = segments(packet.sleep)
    asleep = [(k, a, b) for k, a, b in segs if k != "awake"]
    if asleep:
        start, end = asleep[0][1], asleep[-1][2]
        minutes = sum((b-a).total_seconds()/60 for _, a, b in asleep)
        if minutes < 180:
            raise ValueError("La nuit principale doit contenir au moins trois heures de sommeil.")
        signature = hashlib.sha256(str([(k, a.isoformat(), b.isoformat()) for k, a, b in segs]).encode()).hexdigest()
        detail = {"main_start": local(start), "main_end": local(end), "timezone": packet.timezone,
                  "utc_start": start.isoformat(), "utc_end": end.isoformat(), "window_id": signature,
                  "tz": int(end.astimezone(zone).utcoffset().total_seconds()/60),
                  "timeline": start.astimezone(zone).utcoffset() == end.astimezone(zone).utcoffset(), "writer": packet.source_name,
                  "segments": [[k, local(a), local(b)] for k, a, b in segs]}
        if all(k != "asleep" for k, _, _ in asleep):
            detail["stages"] = {name: sum((b-a).total_seconds()/60 for k, a, b in segs if k == kind)
                                for name, kind in (("light", "core"), ("deep", "deep"), ("rem", "rem"), ("awake", "awake"))}
        rows.append(Daily("sleep", packet.date, minutes, detail))
        for metric, samples, method, minimum in (("hr_night", packet.hr, HR_METHOD, 12),
                                                 ("hrv", packet.hrv_sdnn, SDNN_METHOD, 3),
                                                 ("resp_night", packet.resp, RESP_METHOD, 3)):
            readings = filtered(samples, segs)
            # Sparse snippets must not masquerade as a whole-night summary (H).
            if len(readings) < minimum or readings[-1][0]-readings[0][0] < timedelta(hours=2):
                continue
            values = [v for _, v in readings]
            value = math.exp(statistics.fmean(math.log(v) for v in values)) if metric == "hrv" else statistics.fmean(values)
            rows.append(Daily(metric, packet.date, value, {"method": method, "scope": "sleep_stages_v1",
                              "n": len(values), "min": min(values), "window_id": signature,
                              "writer": packet.source_name, "tz": detail["tz"]}))
    nap_windows, nap_parts, nap_minutes = [], [], 0.0
    for nap in packet.naps:
        parts = [(k, a, b) for k, a, b in segments(nap) if k != "awake"]
        if not parts:
            continue
        a, b = parts[0][1], parts[-1][2]
        if segs and a < segs[-1][2] and b > segs[0][1]:
            raise ValueError("Une sieste ne peut pas recouvrir la nuit principale.")
        if b-a > timedelta(hours=6) or any(a < y and b > x for x, y in nap_windows):
            raise ValueError("Siestes incohérentes.")
        nap_windows.append((a,b))
        measured = sum((y-x).total_seconds()/60 for _, x, y in parts)
        nap_minutes += measured
        nap_parts.append([local(a), local(b), measured])
    if nap_minutes > 600:
        raise ValueError("Durée des siestes incohérente.")
    if nap_windows:
        rows.append(Daily("nap", packet.date, nap_minutes, {"windows": [[local(a),local(b)] for a,b in nap_windows],
                         "writer": packet.source_name, "asleep_windows": nap_parts}))
    return rows


def athlete_zone_name(name: str) -> str | None:
    """The packet's zone if it is a real place (« Europe/Paris »), never a fixed offset."""
    from app.services.timezones import zone_of
    if "/" not in name or name.startswith("Etc/") or zone_of(name).key != name:
        return None
    return name


async def ingest(db, user_id, packet, rows=None):
    import asyncio
    # Overlap checks are quadratic in the intervals: keep them off the event loop
    # (the phone endpoint reduces the night after the token check, with no connection held).
    if rows is None:
        rows = await asyncio.to_thread(reduce_night, packet)
    from app.models.user import User
    user = await db.get(User, user_id)
    if user is not None and not user.timezone and (zone := athlete_zone_name(packet.timezone)):
        # Until another service sets it, the phone's night zone defines the athlete's local day.
        user.timezone = zone
    source = PREFIX + packet.source_id
    # Existing summaries survive empty/denied reads. A corrected sleep window
    # invalidates old heart summaries at read time through window_id matching.
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert
    insert = sqlite_insert if db.bind.dialect.name == "sqlite" else pg_insert
    for row in rows:
        stmt = insert(HealthMetric).values(user_id=user_id, source=source, date=row.day,
                metric=row.metric, value=row.value, details=row.details, n_samples=1,
                updated_at=datetime.now(timezone.utc))
        await db.execute(stmt.on_conflict_do_update(
            index_elements=["user_id", "date", "metric", "source"],
            set_={key: getattr(stmt.excluded, key) for key in ("value", "details", "n_samples", "updated_at")}))
    return len(rows)


def usable(row, sleep_rows):
    if not row.source.startswith(PREFIX):
        return True
    if row.metric in ("hrv", "hr_night", "resp_night"):
        sleep = sleep_rows.get((row.date, row.source))
        expected = {"hrv": SDNN_METHOD, "hr_night": HR_METHOD, "resp_night": RESP_METHOD}[row.metric]
        return bool(sleep and (row.details or {}).get("method") == expected and
                    (row.details or {}).get("window_id") == (sleep.details or {}).get("window_id"))
    return True


def supported_hrv(source, details):
    from app.services.health import HRV_METHOD
    return (details or {}).get("method") == HRV_METHOD or (
        bool(source and source.startswith(PREFIX)) and (details or {}).get("method") == SDNN_METHOD)
