"""Sample payloads for the official connectors' tests (app.connectors).

ASSUMED SHAPES, NOT PROVIDER DOCUMENTATION. Garmin's and COROS's own documentation is behind their partner portals
and was not read: these samples follow the field names public integrations use (Open Wearables' Garmin handlers and
tests, Nango's provider list, Garmin's public OAuth2 PKCE specification for the OAuth and user id answers, the
Health/Activity API names known publicly). Replace them with real payloads once the programs' documentation (or a
first real push, logged on the owner's own account) is at hand; docs/official-connectors.md lists what to check.

The FIT file builder writes real FIT records (the FIT SDK's public profile): it is not an assumption.
"""
import struct
from datetime import datetime, timezone

GARMIN_USER = "garmin-user-1"
COROS_OPEN_ID = "coros-open-1"


def ts(*args) -> int:
    return int(datetime(*args, tzinfo=timezone.utc).timestamp())


# ── Garmin pushes (assumed) ─────────────────────────────────────────────────

SLEEP_START = ts(2026, 11, 13, 22, 0)  # 23:00 at UTC+1, the night of the 14th
OFFSET = 3600
ACTIVITY_START = ts(2026, 11, 14, 8, 0)


def garmin_activity(**over) -> dict:
    a = {"userId": GARMIN_USER, "summaryId": "5001968355", "activityId": 5001968355,
         "activityName": "Trail du matin", "activityType": "TRAIL_RUNNING", "startTimeInSeconds": ACTIVITY_START,
         "startTimeOffsetInSeconds": OFFSET, "durationInSeconds": 5400, "distanceInMeters": 14200.5,
         "totalElevationGainInMeters": 820.0, "averageHeartRateInBeatsPerMinute": 148,
         "maxHeartRateInBeatsPerMinute": 176, "averageSpeedInMetersPerSecond": 2.63,
         "maxSpeedInMetersPerSecond": 4.1, "averageRunCadenceInStepsPerMinute": 164.0, "activeKilocalories": 980,
         "deviceName": "forerunner965", "startingLatitudeInDegree": 45.18, "startingLongitudeInDegree": 5.72}
    return {**a, **over}


def garmin_details() -> dict:
    summary = {k: v for k, v in garmin_activity().items() if k != "userId"}
    samples = [{"startTimeInSeconds": ACTIVITY_START + t, "heartRate": 130 + (t // 1800) * 15 + (t % 7),
                "totalDistanceInMeters": t * 2.63, "timerDurationInSeconds": t, "elevationInMeters": 300 + t / 10}
               for t in range(0, 5400, 10)]
    return {"userId": GARMIN_USER, "summaryId": "5001968355-detail", "activityId": 5001968355, "summary": summary,
            "samples": samples, "laps": [{"startTimeInSeconds": ACTIVITY_START + s} for s in (0, 1800, 3600)]}


# stages, seconds from the start: light, deep, light, rem, awake (asleep 25 200 s, the window 27 000 s)
STAGES = (("light", 0, 3600), ("deep", 3600, 9000), ("light", 9000, 19800), ("rem", 19800, 25200),
          ("awake", 25200, 27000))


def garmin_sleep(**over) -> dict:
    levels: dict[str, list] = {}
    for kind, a, b in STAGES:
        levels.setdefault(kind, []).append({"startTimeInSeconds": SLEEP_START + a, "endTimeInSeconds": SLEEP_START + b})
    s = {"userId": GARMIN_USER, "summaryId": "x3f-sleep-1114", "calendarDate": "2026-11-14",
         "startTimeInSeconds": SLEEP_START, "startTimeOffsetInSeconds": OFFSET, "durationInSeconds": 25200,
         "deepSleepDurationInSeconds": 5400, "lightSleepDurationInSeconds": 14400, "remSleepInSeconds": 5400,
         "awakeDurationInSeconds": 1800, "validation": "ENHANCED_FINAL", "sleepLevelsMap": levels,
         "timeOffsetSleepRespiration": {str(t): 14.0 + (t // 300) % 3 for t in range(0, 27000, 300)},
         "naps": [{"napStartTimeInSeconds": ts(2026, 11, 14, 13, 0), "napDurationInSeconds": 1800,
                   "napOffsetInSeconds": OFFSET}],
         "overallSleepScore": {"value": 84, "qualifierKey": "GOOD"}}
    return {**s, **over}


def garmin_hrv(**over) -> dict:
    h = {"userId": GARMIN_USER, "summaryId": "x3f-hrv-1114", "calendarDate": "2026-11-14",
         "startTimeInSeconds": SLEEP_START, "startTimeOffsetInSeconds": OFFSET, "durationInSeconds": 27000,
         "lastNightAvg": 99, "lastNight5MinHigh": 120,
         "hrvValues": {str(t): (40 + (t // 300) % 7) if t < 25200 else 200 for t in range(300, 27000, 300)}}
    return {**h, **over}


def garmin_daily(**over) -> dict:
    midnight = ts(2026, 11, 13, 23, 0)  # 00:00 at UTC+1 on the 14th
    d = {"userId": GARMIN_USER, "summaryId": "x3f-daily-1114", "calendarDate": "2026-11-14",
         "startTimeInSeconds": midnight, "startTimeOffsetInSeconds": OFFSET, "durationInSeconds": 86400,
         "steps": 12500, "distanceInMeters": 9500.5, "activeKilocalories": 650, "bmrKilocalories": 1800,
         "moderateIntensityDurationInSeconds": 1200, "vigorousIntensityDurationInSeconds": 2400,
         "averageHeartRateInBeatsPerMinute": 62, "minHeartRateInBeatsPerMinute": 44,
         "maxHeartRateInBeatsPerMinute": 171, "restingHeartRateInBeatsPerMinute": 47, "averageStressLevel": 32,
         "maxStressLevel": 88, "bodyBatteryChargedValue": 60, "bodyBatteryDrainedValue": 55,
         "timeOffsetHeartRateSamples": {str(k * 900): (48 + k % 4) if k < 28 else (70 + k % 9) for k in range(96)}}
    return {**d, **over}


def garmin_body(**over) -> dict:
    b = {"userId": GARMIN_USER, "summaryId": "x3f-body-1", "measurementTimeInSeconds": ts(2026, 11, 14, 6, 40),
         "measurementTimeOffsetInSeconds": OFFSET, "weightInGrams": 68400, "bodyFatInPercent": 14.2,
         "bodyMassIndex": 21.5}
    return {**b, **over}


# ── COROS (assumed) ─────────────────────────────────────────────────────────

WORKOUT_START = ts(2026, 11, 14, 7, 30)
FIT_URL = "https://oss.coros.com/fit/432112345678.fit"


def coros_workout(**over) -> dict:
    w = {"openId": COROS_OPEN_ID, "labelId": "432112345678", "mode": 15, "subMode": 1,
         "startTime": WORKOUT_START, "endTime": WORKOUT_START + 4700, "startTimezone": 4, "endTimezone": 4,
         "distance": 12000, "duration": 4500, "avgHeartRate": 150, "calorie": 800000, "fitUrl": FIT_URL,
         "deviceName": "COROS APEX 2 Pro"}
    return {**w, **over}


# ── a FIT activity file (the FIT SDK's public profile) ──────────────────────

FIT_EPOCH = ts(1989, 12, 31)


def _definition(local: int, number: int, fields: list[tuple[int, int, int]], dev: list | None = None) -> bytes:
    header = 0x40 | local | (0x20 if dev else 0)
    out = struct.pack("<BBBHB", header, 0, 0, number, len(fields))
    out += b"".join(struct.pack("<BBB", *f) for f in fields)
    if dev:
        out += struct.pack("<B", len(dev)) + b"".join(struct.pack("<BBB", *d) for d in dev)
    return out


def build_fit(session: dict, laps: list[dict]) -> bytes:
    """A small FIT activity: a file_id, a record definition with a developer field and a few records (one with a
    compressed timestamp header), the laps, the session. Times are UTC epoch seconds, distances metres."""
    body = b""
    body += _definition(0, 0, [(0, 1, 0x00), (4, 4, 0x86)])  # file_id: type, time_created
    body += struct.pack("<BBI", 0, 4, session["start"] - FIT_EPOCH)
    body += _definition(1, 20, [(253, 4, 0x86), (3, 1, 0x02)], dev=[(0, 2, 0)])  # record: timestamp, HR + dev field
    for i in range(3):
        body += struct.pack("<BIB", 1, session["start"] - FIT_EPOCH + i, 120 + i) + b"\x00\x00"
    body += _definition(2, 20, [(3, 1, 0x02)])  # the record again, for compressed-timestamp messages (no field 253)
    body += struct.pack("<BB", 0x80 | (2 << 5) | 4, 125)  # compressed timestamp header, local type 2
    lap_fields = [(2, 4, 0x86), (7, 4, 0x86), (8, 4, 0x86), (9, 4, 0x86), (15, 1, 0x02), (16, 1, 0x02), (21, 2, 0x84)]
    body += _definition(0, 19, lap_fields)  # local type 0 redefined
    for lap in laps:
        body += struct.pack("<BIIIIBBH", 0, lap["start"] - FIT_EPOCH, int(lap["elapsed"] * 1000),
                            int(lap["timer"] * 1000), int(lap["distance"] * 100), lap["avg_hr"], lap["max_hr"],
                            lap.get("ascent", 0xFFFF))
    session_fields = [(2, 4, 0x86), (3, 4, 0x85), (4, 4, 0x85), (5, 1, 0x00), (6, 1, 0x00), (7, 4, 0x86),
                      (8, 4, 0x86), (9, 4, 0x86), (16, 1, 0x02), (17, 1, 0x02), (22, 2, 0x84)]
    body += _definition(1, 18, session_fields)  # local type 1 redefined
    semi = 2 ** 31 / 180
    body += struct.pack("<BIiiBBIIIBBH", 1, session["start"] - FIT_EPOCH, int(session["lat"] * semi),
                        int(session["lng"] * semi), session["sport"], session["sub_sport"],
                        int(session["elapsed"] * 1000), int(session["timer"] * 1000), int(session["distance"] * 100),
                        session["avg_hr"], session["max_hr"], session["ascent"])
    header = struct.pack("<BBHI4sH", 14, 0x20, 2132, len(body), b".FIT", 0)
    return header + body + b"\x00\x00"  # the CRC, not checked by the reader


def coros_fit() -> bytes:
    start = WORKOUT_START + 5
    return build_fit(
        {"start": start, "lat": 45.1885, "lng": 5.7245, "sport": 1, "sub_sport": 3, "elapsed": 4710, "timer": 4520,
         "distance": 12034.5, "avg_hr": 151, "max_hr": 182, "ascent": 655},
        [{"start": start, "elapsed": 2400, "timer": 2300, "distance": 6012.3, "avg_hr": 145, "max_hr": 170,
          "ascent": 320},
         {"start": start + 2400, "elapsed": 2310, "timer": 2220, "distance": 6022.2, "avg_hr": 157, "max_hr": 182}])
