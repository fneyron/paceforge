import logging
import math
import statistics

logger = logging.getLogger(__name__)

RUNNING_SPORTS = {"Run", "TrailRun", "VirtualRun"}
CYCLING_SPORTS = {"Ride", "VirtualRide", "EBikeRide"}
SWIMMING_SPORTS = {"Swim"}


def compute_advanced_metrics(
    sport_type: str,
    streams: dict[str, list] | None,
    activity_data: dict,
    ftp: float | None = None,
    max_hr: float | None = None,
) -> dict:
    """Compute all applicable advanced metrics. Returns a dict for JSON storage."""
    metrics = {}

    if not streams and sport_type not in SWIMMING_SPORTS:
        return metrics

    # Common metrics (all sports with HR)
    if streams and "heartrate" in streams:
        if max_hr:
            metrics["hr_zones_distribution"] = _hr_zones_distribution(streams["heartrate"], max_hr)
            metrics["effort_classification"] = _classify_effort(streams["heartrate"], max_hr)
        metrics["cardiac_drift"] = _cardiac_drift(streams["heartrate"])

    # Interval detection
    if streams and ("heartrate" in streams or "velocity_smooth" in streams):
        intervals = _detect_intervals(streams)
        if intervals:
            metrics["intervals_detected"] = intervals

    # Sport-specific
    try:
        if sport_type in RUNNING_SPORTS:
            metrics.update(_running_metrics(streams or {}, activity_data))
        elif sport_type in CYCLING_SPORTS:
            metrics.update(_cycling_metrics(streams or {}, activity_data, ftp=ftp))
        elif sport_type in SWIMMING_SPORTS:
            metrics.update(_swimming_metrics(activity_data))
    except Exception:
        logger.exception("Failed sport-specific metrics for %s", sport_type)

    return metrics


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def _format_pace(seconds_per_km: float) -> str:
    """Convert seconds/km to 'M:SS/km' format."""
    if not math.isfinite(seconds_per_km) or seconds_per_km <= 0:
        return "0:00/km"
    minutes = int(seconds_per_km) // 60
    seconds = int(seconds_per_km) % 60
    return f"{minutes}:{seconds:02d}/km"


def _rolling_average(data: list, window: int) -> list:
    """Compute a rolling average over *data* with the given *window* size.

    Returns a list of the same length as *data*.  The first ``window - 1``
    values are computed with whatever samples are available (expanding window).
    """
    if not data or window <= 0:
        return []
    result = []
    cumsum = 0.0
    for i, val in enumerate(data):
        cumsum += val
        if i >= window:
            cumsum -= data[i - window]
            result.append(cumsum / window)
        else:
            result.append(cumsum / (i + 1))
    return result


# ---------------------------------------------------------------------------
# Structured workout detection (shared by all sports)
# ---------------------------------------------------------------------------

def _detect_structured_workout(activity_data: dict, is_running: bool = False) -> dict | None:
    """Detect structured intervals from manual laps.

    Groups laps by similar duration to find repeating patterns.
    Returns workout dict or None.
    """
    try:
        laps = activity_data.get("laps", [])
        total_dist_km = activity_data.get("distance", 0) / 1000
        expected_auto_laps = round(total_dist_km) if total_dist_km > 0 else 0
        is_manual = len(laps) != expected_auto_laps and len(laps) >= 3

        if not laps or (not is_manual and len(laps) < 5):
            return None

        lap_data = []
        for lap in laps:
            dist = lap.get("distance", 0)
            mt = lap.get("moving_time", 0)
            if dist > 0 and mt > 0:
                lap_data.append({
                    "distance": round(dist),
                    "moving_time": mt,
                    "pace_ms": dist / mt,
                    "avg_hr": lap.get("average_heartrate"),
                    "avg_cadence": lap.get("average_cadence"),
                    "avg_watts": lap.get("average_watts"),
                })

        if len(lap_data) < 3:
            return None

        # Group core laps by similar duration (skip warmup/cooldown)
        core = lap_data[1:-1] if len(lap_data) > 4 else lap_data
        groups: dict[int, list] = {}
        for lap in core:
            bucket = round(lap["moving_time"] / 30) * 30
            groups.setdefault(bucket, []).append(lap)

        sorted_groups = sorted(groups.values(), key=len, reverse=True)

        if len(sorted_groups) >= 2:
            a, b = sorted_groups[0], sorted_groups[1]
            avg_a = statistics.mean([l["pace_ms"] for l in a])
            avg_b = statistics.mean([l["pace_ms"] for l in b])
            work_laps, rest_laps = (a, b) if avg_a >= avg_b else (b, a)
        elif len(sorted_groups) == 1 and len(sorted_groups[0]) >= 3:
            work_laps, rest_laps = sorted_groups[0], []
        else:
            return None

        if len(work_laps) < 2:
            return None

        # Time-based or distance-based?
        work_times = [l["moving_time"] for l in work_laps]
        work_dists = [l["distance"] for l in work_laps]
        time_cv = (statistics.stdev(work_times) / statistics.mean(work_times) * 100) if len(work_times) >= 2 and statistics.mean(work_times) > 0 else 999
        dist_cv = (statistics.stdev(work_dists) / statistics.mean(work_dists) * 100) if len(work_dists) >= 2 and statistics.mean(work_dists) > 0 else 999
        is_time_based = time_cv < dist_cv and time_cv < 25

        result: dict = {
            "type": "structured_intervals",
            "repetitions": len(work_laps),
            "interval_type": "time" if is_time_based else "distance",
        }

        if is_time_based:
            avg_t = statistics.mean(work_times)
            mins, secs = int(avg_t // 60), int(avg_t % 60)
            result["avg_interval_duration_s"] = round(avg_t)
            result["avg_interval_duration_fmt"] = f"{mins}min{secs:02d}" if secs else f"{mins}min"
            result["time_consistency_cv"] = round(time_cv, 1)
        else:
            result["avg_interval_distance_m"] = round(statistics.mean(work_dists))
            avg_pace = statistics.mean([l["pace_ms"] for l in work_laps])
            if avg_pace > 0:
                result["avg_interval_pace"] = _format_pace(1000 / avg_pace)
            result["pace_consistency_cv"] = round(dist_cv, 1)

        # Consistency
        cv = time_cv if is_time_based else dist_cv
        result["consistency"] = "excellent" if cv < 5 else "bon" if cv < 10 else "irrégulier"

        # HR stats
        work_hrs = [l["avg_hr"] for l in work_laps if l["avg_hr"]]
        if work_hrs:
            result["avg_interval_hr"] = round(statistics.mean(work_hrs))
            if len(work_hrs) >= 4:
                h1 = statistics.mean(work_hrs[:len(work_hrs)//2])
                h2 = statistics.mean(work_hrs[len(work_hrs)//2:])
                result["interval_hr_drift_pct"] = round((h2 - h1) / h1 * 100, 1)

        # Power stats
        work_watts = [l["avg_watts"] for l in work_laps if l["avg_watts"]]
        if work_watts:
            result["avg_interval_watts"] = round(statistics.mean(work_watts))
            if len(work_watts) >= 2:
                result["power_consistency_cv"] = round(statistics.stdev(work_watts) / statistics.mean(work_watts) * 100, 1)

        # Cadence (running: x2 for Strava per-foot)
        work_cad = [l["avg_cadence"] for l in work_laps if l["avg_cadence"]]
        if work_cad:
            avg_cad = statistics.mean(work_cad)
            result["avg_interval_cadence_spm"] = round(avg_cad * 2 if is_running else avg_cad)

        # Recovery stats
        if rest_laps:
            rest_hrs = [l["avg_hr"] for l in rest_laps if l["avg_hr"]]
            if rest_hrs:
                result["avg_recovery_hr"] = round(statistics.mean(rest_hrs))
                if work_hrs:
                    result["work_rest_hr_delta"] = round(statistics.mean(work_hrs) - statistics.mean(rest_hrs))
            rest_watts = [l["avg_watts"] for l in rest_laps if l["avg_watts"]]
            if rest_watts and work_watts:
                result["avg_recovery_watts"] = round(statistics.mean(rest_watts))
                result["work_rest_power_ratio"] = round(statistics.mean(work_watts) / statistics.mean(rest_watts), 2)

        return result
    except Exception:
        logger.exception("Error detecting structured workout")
        return None


# ---------------------------------------------------------------------------
# Heart-rate helpers
# ---------------------------------------------------------------------------

def _hr_zones_distribution(hr_data: list, max_hr: float) -> dict:
    """Count seconds in each HR zone and return percentages."""
    try:
        if not hr_data or max_hr <= 0:
            return {}

        zone_defs = [
            ("Z1", "Recuperation", 0, 60),
            ("Z2", "Endurance", 60, 70),
            ("Z3", "Tempo", 70, 80),
            ("Z4", "Seuil", 80, 90),
            ("Z5", "VO2max", 90, 100),
        ]

        zones = {}
        for key, label, low_pct, high_pct in zone_defs:
            zones[key] = {
                "label": label,
                "seconds": 0,
                "low_bpm": round(max_hr * low_pct / 100),
                "high_bpm": round(max_hr * high_pct / 100),
            }

        for hr in hr_data:
            pct = hr / max_hr * 100
            if pct < 60:
                zones["Z1"]["seconds"] += 1
            elif pct < 70:
                zones["Z2"]["seconds"] += 1
            elif pct < 80:
                zones["Z3"]["seconds"] += 1
            elif pct < 90:
                zones["Z4"]["seconds"] += 1
            else:
                zones["Z5"]["seconds"] += 1

        total = len(hr_data)
        result = {}
        for zone_key, zone_val in zones.items():
            result[zone_key] = {
                "pct": round(zone_val["seconds"] / total * 100, 1) if total else 0,
                "seconds": zone_val["seconds"],
                "label": zone_val["label"],
                "low_bpm": zone_val["low_bpm"],
                "high_bpm": zone_val["high_bpm"],
            }
        return result
    except Exception:
        logger.exception("Error computing HR zones distribution")
        return {}


def _classify_effort(hr_data: list, max_hr: float) -> dict:
    """Classify effort based on average HR as percentage of max HR."""
    try:
        if not hr_data or max_hr <= 0:
            return {}

        avg_hr = statistics.mean(hr_data)
        avg_pct = avg_hr / max_hr * 100

        if avg_pct < 60:
            classification = "récupération"
        elif avg_pct < 70:
            classification = "endurance"
        elif avg_pct < 80:
            classification = "tempo"
        elif avg_pct < 90:
            classification = "seuil"
        elif avg_pct < 95:
            classification = "VO2max"
        else:
            classification = "anaérobie"

        return {"classification": classification, "avg_hr_pct": round(avg_pct, 1)}
    except Exception:
        logger.exception("Error classifying effort")
        return {}


def _cardiac_drift(hr_data: list) -> dict:
    """Compute cardiac drift between the first and second half of the session."""
    try:
        if not hr_data or len(hr_data) < 2:
            return {}

        mid = len(hr_data) // 2
        first_half_avg = statistics.mean(hr_data[:mid])
        second_half_avg = statistics.mean(hr_data[mid:])

        if first_half_avg == 0:
            return {}

        drift_pct = (second_half_avg - first_half_avg) / first_half_avg * 100

        if abs(drift_pct) < 8:
            assessment = "normal"
        elif abs(drift_pct) < 12:
            assessment = "modéré"
        else:
            assessment = "élevé"

        return {
            "drift_pct": round(drift_pct, 1),
            "first_half_avg": round(first_half_avg, 1),
            "second_half_avg": round(second_half_avg, 1),
            "assessment": assessment,
        }
    except Exception:
        logger.exception("Error computing cardiac drift")
        return {}


# ---------------------------------------------------------------------------
# Interval detection
# ---------------------------------------------------------------------------

def _detect_intervals(streams: dict) -> list[dict]:
    """Detect work/rest intervals from velocity or heart-rate data."""
    try:
        # Prefer velocity_smooth, fall back to heartrate
        if "velocity_smooth" in streams and streams["velocity_smooth"]:
            raw = streams["velocity_smooth"]
        elif "heartrate" in streams and streams["heartrate"]:
            raw = streams["heartrate"]
        else:
            return []

        if len(raw) < 60:
            return []

        smoothed = _rolling_average(raw, 30)

        avg = statistics.mean(smoothed)
        if len(smoothed) >= 2:
            sd = statistics.stdev(smoothed)
        else:
            sd = 0.0

        threshold = avg + 0.5 * sd

        hr_data = streams.get("heartrate")

        intervals: list[dict] = []
        current_type = "work" if smoothed[0] >= threshold else "rest"
        start = 0

        for i in range(1, len(smoothed)):
            new_type = "work" if smoothed[i] >= threshold else "rest"
            if new_type != current_type:
                duration = i - start
                if duration >= 30:
                    interval: dict = {
                        "type": current_type,
                        "start_s": start,
                        "end_s": i,
                        "duration_s": duration,
                        "avg_hr": None,
                    }
                    if hr_data and len(hr_data) > start:
                        segment = hr_data[start : min(i, len(hr_data))]
                        if segment:
                            interval["avg_hr"] = round(statistics.mean(segment), 1)
                    intervals.append(interval)
                start = i
                current_type = new_type

        # Close the last interval
        duration = len(smoothed) - start
        if duration >= 30:
            interval = {
                "type": current_type,
                "start_s": start,
                "end_s": len(smoothed),
                "duration_s": duration,
                "avg_hr": None,
            }
            if hr_data and len(hr_data) > start:
                segment = hr_data[start : min(len(smoothed), len(hr_data))]
                if segment:
                    interval["avg_hr"] = round(statistics.mean(segment), 1)
            intervals.append(interval)

        return intervals[:20]
    except Exception:
        logger.exception("Error detecting intervals")
        return []


# ---------------------------------------------------------------------------
# Running metrics
# ---------------------------------------------------------------------------

def _running_metrics(streams: dict, activity_data: dict) -> dict:
    """Compute running-specific metrics."""
    metrics = {}

    # Stride length
    try:
        velocity = streams.get("velocity_smooth", [])
        cadence = streams.get("cadence", [])
        if velocity and cadence and len(velocity) == len(cadence):
            strides = []
            for v, c in zip(velocity, cadence):
                if c > 0 and v > 0:
                    # Strava returns running cadence per foot (strides/min),
                    # multiply by 2 to get total steps/min
                    total_cadence = c * 2
                    stride = v / (total_cadence / 60.0)
                    strides.append(stride)
            if strides:
                avg_stride = statistics.mean(strides)
                if avg_stride < 1.0:
                    assessment = "courte"
                elif avg_stride > 1.4:
                    assessment = "longue"
                else:
                    assessment = "optimale"
                metrics["stride_length"] = {
                    "avg_m": round(avg_stride, 2),
                    "assessment": assessment,
                }
    except Exception:
        logger.exception("Error computing stride length")

    # Cadence analysis
    try:
        cadence = streams.get("cadence", [])
        if cadence:
            non_zero = [c for c in cadence if c > 0]
            if non_zero:
                # Strava returns running cadence per foot, multiply by 2
                avg_cad = statistics.mean(non_zero) * 2
                in_range = sum(1 for c in non_zero if 170 <= c * 2 <= 185)
                pct_optimal = in_range / len(non_zero) * 100

                if avg_cad < 170:
                    assessment = "basse"
                elif avg_cad > 185:
                    assessment = "élevée"
                else:
                    assessment = "optimal"

                metrics["cadence_analysis"] = {
                    "avg_spm": round(avg_cad),
                    "pct_in_optimal_range": round(pct_optimal, 1),
                    "assessment": assessment,
                }
    except Exception:
        logger.exception("Error computing running cadence analysis")

    # Pace variability
    try:
        splits = activity_data.get("splits_metric", [])
        if splits:
            # Only keep full splits (distance >= 900m)
            full_splits = [s for s in splits if s.get("distance", 0) >= 900]
            if len(full_splits) >= 2:
                paces = []
                for s in full_splits:
                    dist = s.get("distance", 0)
                    time = s.get("moving_time", 0)
                    if dist > 0 and time > 0:
                        pace_per_km = time / dist * 1000
                        paces.append(pace_per_km)

                if len(paces) >= 2:
                    avg_pace = statistics.mean(paces)
                    sd_pace = statistics.stdev(paces)
                    cv = sd_pace / avg_pace * 100 if avg_pace > 0 else 0

                    fastest = min(paces)
                    slowest = max(paces)

                    if cv < 5:
                        assessment = "régulier"
                    elif cv < 10:
                        assessment = "variable"
                    else:
                        assessment = "irrégulier"

                    metrics["pace_variability"] = {
                        "cv_pct": round(cv, 1),
                        "fastest_split_pace": _format_pace(fastest),
                        "slowest_split_pace": _format_pace(slowest),
                        "assessment": assessment,
                    }
    except Exception:
        logger.exception("Error computing pace variability")

    # Aerobic decoupling
    try:
        velocity = streams.get("velocity_smooth", [])
        hr_data = streams.get("heartrate", [])
        if velocity and hr_data:
            length = min(len(velocity), len(hr_data))
            if length >= 2:
                mid = length // 2
                vel_first = velocity[:mid]
                hr_first = hr_data[:mid]
                vel_second = velocity[mid:length]
                hr_second = hr_data[mid:length]

                avg_hr_first = statistics.mean(hr_first)
                avg_hr_second = statistics.mean(hr_second)
                avg_vel_first = statistics.mean(vel_first)
                avg_vel_second = statistics.mean(vel_second)

                if avg_hr_first > 0 and avg_hr_second > 0:
                    ratio1 = avg_vel_first / avg_hr_first
                    ratio2 = avg_vel_second / avg_hr_second

                    if ratio1 > 0:
                        decoupling = (ratio1 - ratio2) / ratio1 * 100

                        if decoupling < 5:
                            assessment = "bon"
                        elif decoupling < 10:
                            assessment = "à surveiller"
                        else:
                            assessment = "insuffisant"

                        metrics["aerobic_decoupling"] = {
                            "decoupling_pct": round(decoupling, 1),
                            "assessment": assessment,
                        }
    except Exception:
        logger.exception("Error computing aerobic decoupling")

    # Split analysis (negative/positive/even split)
    try:
        splits = activity_data.get("splits_metric", [])
        if splits:
            full_splits = [s for s in splits if s.get("distance", 0) >= 900]
            if len(full_splits) >= 2:
                paces = []
                for s in full_splits:
                    dist = s.get("distance", 0)
                    time = s.get("moving_time", 0)
                    if dist > 0 and time > 0:
                        paces.append(time / dist * 1000)

                if len(paces) >= 2:
                    mid = len(paces) // 2
                    first_half_avg = statistics.mean(paces[:mid])
                    second_half_avg = statistics.mean(paces[mid:])
                    diff = second_half_avg - first_half_avg  # positive = slower 2nd half

                    if abs(diff) < 3:
                        split_type = "even"
                    elif diff < 0:
                        split_type = "negative"  # 2nd half faster
                    else:
                        split_type = "positive"  # 2nd half slower

                    metrics["split_analysis"] = {
                        "type": split_type,
                        "magnitude_s_per_km": round(abs(diff)),
                    }
    except Exception:
        logger.exception("Error computing split analysis")

    # Stop time percentage
    try:
        elapsed = activity_data.get("elapsed_time", 0)
        moving = activity_data.get("moving_time", 0)
        if elapsed > 0:
            stop_pct = (elapsed - moving) / elapsed * 100
            metrics["stop_time_pct"] = {"pct": round(stop_pct, 1)}
    except Exception:
        logger.exception("Error computing stop time percentage")

    # Structured workout detection (shared function for all sports)
    sw = _detect_structured_workout(activity_data, is_running=True)
    if sw:
        metrics["structured_workout"] = sw

    # Running power analysis
    try:
        watts = streams.get("watts", [])
        if watts:
            non_zero_watts = [w for w in watts if w > 0]
            if non_zero_watts:
                avg_power = statistics.mean(non_zero_watts)
                max_power = max(non_zero_watts)

                # Normalized power (30s rolling avg, raise to 4th, avg, 4th root)
                np_value = None
                if len(non_zero_watts) >= 30:
                    rolling = _rolling_average(non_zero_watts, 30)
                    fourth_powers = [v ** 4 for v in rolling]
                    np_value = statistics.mean(fourth_powers) ** 0.25

                power_data: dict = {
                    "avg_watts": round(avg_power),
                    "max_watts": max_power,
                }
                if np_value:
                    power_data["normalized_power"] = round(np_value)
                    if avg_power > 0:
                        power_data["variability_index"] = round(np_value / avg_power, 2)

                # Power-to-weight ratio (if weight available)
                athlete_weight = activity_data.get("athlete", {}).get("weight")
                if not athlete_weight:
                    # Estimate from kilojoules and moving time if available
                    pass
                if athlete_weight and athlete_weight > 0:
                    power_data["power_to_weight"] = round(avg_power / athlete_weight, 2)

                metrics["running_power"] = power_data

        # Power/pace efficiency (watts per m/s)
        velocity = streams.get("velocity_smooth", [])
        if watts and velocity:
            length = min(len(watts), len(velocity))
            pairs = [
                (w, v) for w, v in zip(watts[:length], velocity[:length])
                if w > 0 and v > 0.5  # filter stops
            ]
            if pairs:
                avg_w = statistics.mean(p[0] for p in pairs)
                avg_v = statistics.mean(p[1] for p in pairs)
                if avg_v > 0:
                    efficiency = avg_w / avg_v  # W per m/s
                    # Lower is more efficient; typical range 60-100 W/(m/s)
                    if efficiency < 70:
                        assessment = "excellent"
                    elif efficiency < 85:
                        assessment = "bon"
                    else:
                        assessment = "à améliorer"
                    metrics["power_pace_efficiency"] = {
                        "watts_per_ms": round(efficiency, 1),
                        "assessment": assessment,
                    }

        # Power/HR ratio for running
        hr_data = streams.get("heartrate", [])
        if watts and hr_data:
            length = min(len(watts), len(hr_data))
            pairs = [
                (w, h) for w, h in zip(watts[:length], hr_data[:length])
                if w > 0 and h > 60
            ]
            if pairs:
                avg_w = statistics.mean(p[0] for p in pairs)
                avg_hr = statistics.mean(p[1] for p in pairs)
                if avg_hr > 0:
                    metrics["running_power_hr_ratio"] = {
                        "ratio": round(avg_w / avg_hr, 2),
                        "unit": "W/bpm",
                    }
    except Exception:
        logger.exception("Error computing running power metrics")

    return metrics


# ---------------------------------------------------------------------------
# Cycling metrics
# ---------------------------------------------------------------------------

def _cycling_metrics(streams: dict, activity_data: dict, ftp: float | None = None) -> dict:
    """Compute cycling-specific metrics."""
    metrics = {}

    np_value = activity_data.get("weighted_average_watts")
    ap_value = activity_data.get("average_watts")

    # Variability index
    try:
        if np_value and ap_value and ap_value > 0:
            vi = np_value / ap_value
            if vi < 1.05:
                assessment = "régulier"
            elif vi < 1.15:
                assessment = "variable"
            else:
                assessment = "très variable"
            metrics["variability_index"] = {
                "vi": round(vi, 2),
                "assessment": assessment,
            }
    except Exception:
        logger.exception("Error computing variability index")

    # Intensity factor
    try:
        if np_value and ftp and ftp > 0:
            if_value = np_value / ftp
            metrics["intensity_factor"] = {"if_value": round(if_value, 2)}
    except Exception:
        logger.exception("Error computing intensity factor")

    # TSS estimate
    try:
        if np_value and ftp and ftp > 0:
            if_value = np_value / ftp
            moving_time = activity_data.get("moving_time", 0)
            if moving_time > 0:
                tss = (moving_time * np_value * if_value) / (ftp * 3600) * 100
                metrics["tss_estimate"] = {"tss": round(tss)}
    except Exception:
        logger.exception("Error computing TSS estimate")

    # Power zones distribution
    try:
        watts = streams.get("watts", [])
        if watts and ftp and ftp > 0:
            zones = {
                "Z1": {"seconds": 0},
                "Z2": {"seconds": 0},
                "Z3": {"seconds": 0},
                "Z4": {"seconds": 0},
                "Z5": {"seconds": 0},
                "Z6": {"seconds": 0},
            }
            for w in watts:
                pct = w / ftp * 100
                if pct < 55:
                    zones["Z1"]["seconds"] += 1
                elif pct < 75:
                    zones["Z2"]["seconds"] += 1
                elif pct < 90:
                    zones["Z3"]["seconds"] += 1
                elif pct < 105:
                    zones["Z4"]["seconds"] += 1
                elif pct < 120:
                    zones["Z5"]["seconds"] += 1
                else:
                    zones["Z6"]["seconds"] += 1

            total = len(watts)
            result = {}
            for zone_key, zone_val in zones.items():
                result[zone_key] = {
                    "pct": round(zone_val["seconds"] / total * 100, 1) if total else 0,
                    "seconds": zone_val["seconds"],
                }
            metrics["power_zones_distribution"] = result
    except Exception:
        logger.exception("Error computing power zones distribution")

    # Cadence analysis
    try:
        cadence = streams.get("cadence", [])
        if cadence:
            non_zero = [c for c in cadence if c > 0]
            if non_zero:
                avg_cad = statistics.mean(non_zero)
                in_range = sum(1 for c in non_zero if 80 <= c <= 95)
                pct_optimal = in_range / len(non_zero) * 100

                if avg_cad < 80:
                    assessment = "basse"
                elif avg_cad > 95:
                    assessment = "élevée"
                else:
                    assessment = "optimal"

                metrics["cadence_analysis"] = {
                    "avg_spm": round(avg_cad),
                    "pct_in_optimal_range": round(pct_optimal, 1),
                    "assessment": assessment,
                }
    except Exception:
        logger.exception("Error computing cycling cadence analysis")

    # Power / HR ratio
    try:
        watts = streams.get("watts", [])
        hr_data = streams.get("heartrate", [])
        if watts and hr_data:
            length = min(len(watts), len(hr_data))
            if length > 0:
                avg_w = statistics.mean(watts[:length])
                avg_hr = statistics.mean(hr_data[:length])
                if avg_hr > 0:
                    metrics["power_hr_ratio"] = {
                        "ratio": round(avg_w / avg_hr, 2),
                        "unit": "W/bpm",
                    }
    except Exception:
        logger.exception("Error computing power/HR ratio")

    # Normalized power (computed from stream)
    try:
        watts = streams.get("watts", [])
        if watts and len(watts) >= 30:
            rolling = _rolling_average(watts, 30)
            # Raise each to 4th power, average, then 4th root
            fourth_powers = [v ** 4 for v in rolling]
            avg_fourth = statistics.mean(fourth_powers)
            np_computed = avg_fourth ** 0.25
            metrics["normalized_power_computed"] = {"np_watts": round(np_computed)}
    except Exception:
        logger.exception("Error computing normalized power")

    # Structured workout detection (same logic as running, shared function)
    sw = _detect_structured_workout(activity_data)
    if sw:
        metrics["structured_workout"] = sw

    return metrics


# ---------------------------------------------------------------------------
# Swimming metrics
# ---------------------------------------------------------------------------

def _swimming_metrics(activity_data: dict) -> dict:
    """Compute swimming-specific metrics from laps (no streams available)."""
    metrics = {}

    laps = activity_data.get("laps", [])

    # Filter valid laps (must have distance and moving_time)
    valid_laps = [
        lap for lap in laps
        if lap.get("distance", 0) > 0 and lap.get("moving_time", 0) > 0
    ]

    if not valid_laps:
        return metrics

    # Compute pace per 100m for each lap
    def _lap_pace_100m(lap: dict) -> float:
        return lap["moving_time"] / lap["distance"] * 100

    paces = [_lap_pace_100m(lap) for lap in valid_laps]

    # Pace degradation
    try:
        if len(paces) >= 6:
            first_3 = statistics.mean(paces[:3])
            last_3 = statistics.mean(paces[-3:])
            if first_3 > 0:
                degradation = (last_3 - first_3) / first_3 * 100

                def _format_swim_pace(sec_per_100: float) -> str:
                    minutes = int(sec_per_100) // 60
                    seconds = int(sec_per_100) % 60
                    return f"{minutes}:{seconds:02d}/100m"

                metrics["pace_degradation"] = {
                    "degradation_pct": round(degradation, 1),
                    "first_laps_pace": _format_swim_pace(first_3),
                    "last_laps_pace": _format_swim_pace(last_3),
                }
    except Exception:
        logger.exception("Error computing swim pace degradation")

    # Consistency score
    try:
        if len(paces) >= 2:
            avg_pace = statistics.mean(paces)
            sd_pace = statistics.stdev(paces)
            cv = sd_pace / avg_pace * 100 if avg_pace > 0 else 0

            if cv < 4:
                assessment = "constant"
            elif cv < 8:
                assessment = "variable"
            else:
                assessment = "irrégulier"

            metrics["consistency_score"] = {
                "cv_pct": round(cv, 1),
                "assessment": assessment,
            }
    except Exception:
        logger.exception("Error computing swim consistency score")

    return metrics
