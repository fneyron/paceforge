"""Synthetic laps for the browser previews: a club track session (warm-up, 2 × (8 × 300 m) with 100 m recoveries
and a jog between the series, cool-down), a 30/30 session of 240 laps, a ride with power. Strava's lap shape:
moving_time (s), distance (m), average_heartrate, average_watts."""


def track_session() -> list[dict]:
    laps = [{"distance": 3000.0, "moving_time": 990, "average_heartrate": 138}]
    for series in range(2):
        for k in range(8):
            laps.append({"distance": 300.0 + k % 2 * 2, "moving_time": 61 + (k * 7 + series) % 4,
                         "average_heartrate": 166 + k + series * 2})
            if k < 7:
                laps.append({"distance": 100.0, "moving_time": 55 + k % 3 * 5, "average_heartrate": 142})
        if series == 0:
            laps.append({"distance": 600.0, "moving_time": 240, "average_heartrate": 128})
    laps.append({"distance": 2000.0, "moving_time": 690, "average_heartrate": 134})
    return laps


def thirty_thirty() -> list[dict]:
    return [{"distance": 150.0 if i % 2 == 0 else 85.0, "moving_time": 30,
             "average_heartrate": 150 + i % 2 * 12} for i in range(240)]


def ride() -> list[dict]:
    return [{"distance": d, "moving_time": t, "average_watts": w, "average_heartrate": hr, "average_cadence": 88}
            for d, t, w, hr in ((8000, 1080, 170, 128), (6000, 600, 290, 158), (3000, 420, 140, 132),
                                (6000, 600, 295, 161), (8500, 1140, 175, 135))]


def totals(laps: list[dict]) -> tuple[float, int]:
    return sum(lap["distance"] for lap in laps), sum(lap["moving_time"] for lap in laps)
