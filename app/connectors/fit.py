"""The laps and the session of a FIT activity file, what PaceForge reads of a COROS workout's detail.

FIT is Garmin's public binary format (the FIT SDK's profile, used by every watch brand, COROS included): a 12 or
14-byte header, then records — a definition message describes a local message type (its global number, fields,
sizes, base types, byte order), data messages carry values in that layout; a compressed-timestamp header is a data
message too. Only two global messages are read:
- session (18): start_time (2), start position (3, 4: semicircles), sport (5), sub_sport (6), total_elapsed_time
  (7, ms), total_timer_time (8, ms), total_distance (9, cm), avg_heart_rate (16), max_heart_rate (17), total_ascent
  (22, m);
- lap (19): start_time (2), total_elapsed_time (7, ms), total_timer_time (8, ms), total_distance (9, cm),
  avg_heart_rate (15), max_heart_rate (16), total_ascent (21, m).
Everything else is skipped by its declared size; an invalid value (all bits set, the profile's « invalid ») is
absent; a file cut short or malformed yields what was read before (FitError when nothing could be). The CRC is not
checked: the file comes over HTTPS from the provider, and every value read is range-checked by its caller.
"""
import struct
from datetime import datetime, timedelta, timezone

FIT_EPOCH = datetime(1989, 12, 31, tzinfo=timezone.utc)  # FIT timestamps count seconds from it
MAX_BYTES = 32 * 1024 * 1024
SESSION, LAP = 18, 19

# base type number (low 5 bits) → (struct code, size, invalid value)
_BASE = {
    0x00: ("B", 1, 0xFF), 0x01: ("b", 1, 0x7F), 0x02: ("B", 1, 0xFF), 0x03: ("h", 2, 0x7FFF),
    0x04: ("H", 2, 0xFFFF), 0x05: ("i", 4, 0x7FFFFFFF), 0x06: ("I", 4, 0xFFFFFFFF), 0x08: ("f", 4, None),
    0x09: ("d", 8, None), 0x0A: ("B", 1, 0x00), 0x0B: ("H", 2, 0x0000), 0x0C: ("I", 4, 0x00000000),
    0x0D: ("B", 1, 0xFF), 0x0E: ("q", 8, 0x7FFFFFFFFFFFFFFF), 0x0F: ("Q", 8, 0xFFFFFFFFFFFFFFFF),
    0x10: ("Q", 8, 0),
}

# the fields read, per message: field number → (name, scale)
_FIELDS = {
    SESSION: {2: ("start_time", 1), 3: ("start_lat", 1), 4: ("start_lng", 1), 5: ("sport", 1), 6: ("sub_sport", 1),
              7: ("elapsed", 1000), 8: ("timer", 1000), 9: ("distance", 100), 16: ("avg_hr", 1), 17: ("max_hr", 1),
              22: ("ascent", 1)},
    LAP: {2: ("start_time", 1), 7: ("elapsed", 1000), 8: ("timer", 1000), 9: ("distance", 100), 15: ("avg_hr", 1),
          16: ("max_hr", 1), 21: ("ascent", 1)},
}
SEMICIRCLE = 180 / 2 ** 31


class FitError(ValueError):
    pass


def _value(raw: bytes, base: int, little: bool):
    spec = _BASE.get(base & 0x1F)
    if spec is None or base & 0x1F == 0x07:  # a string, or a type this reader does not know
        return None
    code, size, invalid = spec
    if len(raw) < size:
        return None
    value = struct.unpack(("<" if little else ">") + code, raw[:size])[0]  # an array: its first element
    if invalid is not None and value == invalid:
        return None
    if isinstance(value, float) and value != value:  # NaN
        return None
    return value


def decode(data: bytes) -> dict:
    """{"session": {...} | None, "laps": [{...}]} with the fields above (names, scaled), times as aware UTC."""
    if len(data) > MAX_BYTES:
        raise FitError("file too large")
    if len(data) < 12 or data[8:12] != b".FIT":
        raise FitError("not a FIT file")
    header = data[0]
    if header not in (12, 14):
        raise FitError("unknown header")
    size = struct.unpack("<I", data[4:8])[0]
    end = min(len(data), header + size)
    pos = header
    defs: dict[int, tuple[int, bool, list[tuple[int, int, int]], int]] = {}
    session: dict | None = None
    laps: list[dict] = []
    try:
        while pos < end:
            rec = data[pos]
            pos += 1
            if rec & 0x80:  # compressed timestamp: a data message of local type bits 5-6
                local, is_def, dev = (rec >> 5) & 0x03, False, False
            else:
                local, is_def, dev = rec & 0x0F, bool(rec & 0x40), bool(rec & 0x20)
            if is_def:
                little = data[pos + 1] == 0
                number = struct.unpack("<H" if little else ">H", data[pos + 2:pos + 4])[0]
                count = data[pos + 4]
                pos += 5
                fields = [(data[pos + 3 * i], data[pos + 3 * i + 1], data[pos + 3 * i + 2]) for i in range(count)]
                pos += 3 * count
                dev_size = 0
                if dev:
                    dev_count = data[pos]
                    pos += 1
                    dev_size = sum(data[pos + 3 * i + 1] for i in range(dev_count))
                    pos += 3 * dev_count
                if pos > end:
                    break
                defs[local] = (number, little, fields, dev_size)
                continue
            if local not in defs:
                raise FitError("data before its definition")
            number, little, fields, dev_size = defs[local]
            wanted = _FIELDS.get(number)
            row: dict = {}
            for num, fsize, base in fields:
                if wanted and num in wanted:
                    value = _value(data[pos:pos + fsize], base, little)
                    if value is not None:
                        name, scale = wanted[num]
                        row[name] = value / scale if scale != 1 else value
                pos += fsize
            pos += dev_size
            if pos > end:
                break
            if number == SESSION and session is None:
                session = row
            elif number == LAP:
                laps.append(row)
    except (IndexError, struct.error):
        if session is None and not laps:
            raise FitError("file cut short") from None
    for read in ([session] if session else []) + laps:
        if "start_time" in read:
            read["start_time"] = FIT_EPOCH + timedelta(seconds=int(read["start_time"]))
        if "start_lat" in read and "start_lng" in read:
            read["start_latlng"] = [round(read.pop("start_lat") * SEMICIRCLE, 6),
                                    round(read.pop("start_lng") * SEMICIRCLE, 6)]
    return {"session": session, "laps": laps}


HR_PLAUSIBLE = (40, 250)


def strava_laps(laps: list[dict]) -> list[dict]:
    """The laps in Strava's shape (nights.hr_segments, km_splits read them): moving_time (s), average_heartrate,
    and max_heartrate and distance (m) when they have them; a lap without a time or a plausible average HR is left
    out."""
    out = []
    for lap in laps:
        t, hr = lap.get("timer") or lap.get("elapsed"), lap.get("avg_hr")
        if not t or round(t) <= 0 or hr is None or not HR_PLAUSIBLE[0] <= hr <= HR_PLAUSIBLE[1]:
            continue
        row = {"moving_time": round(t), "average_heartrate": float(hr)}
        if lap.get("max_hr") and HR_PLAUSIBLE[0] <= lap["max_hr"] <= HR_PLAUSIBLE[1]:
            row["max_heartrate"] = float(lap["max_hr"])
        if lap.get("distance") and lap["distance"] > 0:
            row["distance"] = round(float(lap["distance"]), 1)
        out.append(row)
    return out


# FIT sport / sub_sport (the SDK's profile) → the sport types the app knows (Strava's)
SPORTS = {1: "Run", 2: "Ride", 5: "Swim", 10: "Workout", 11: "Walk", 12: "NordicSki", 13: "AlpineSki",
          14: "Snowboard", 15: "Rowing", 16: "Hike", 17: "Hike", 19: "Kayaking", 21: "EBikeRide", 31: "RockClimbing",
          41: "Kayaking"}
SUB_SPORTS: dict[tuple, str] = {(1, 3): "TrailRun", (1, 1): "Run", (2, 6): "VirtualRide", (2, 8): "MountainBikeRide",
              (10, 20): "WeightTraining", (15, 14): "Rowing"}
INDOOR: set[tuple] = {(1, 1), (2, 6), (15, 14)}  # treadmill, indoor cycling, indoor rowing


def sport_type(session: dict | None) -> str | None:
    if not session or session.get("sport") is None:
        return None
    key = (session["sport"], session.get("sub_sport"))
    return SUB_SPORTS.get(key) or SPORTS.get(session["sport"])


def indoor(session: dict | None) -> bool:
    return session is not None and (session.get("sport"), session.get("sub_sport")) in INDOOR
