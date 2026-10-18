"""Conventional running HR zones from an athlete-confirmed maximum.

Activity peaks, even repeated ones, do not establish a personal HRmax. Missing
or invalid input leaves the card empty. Percentages follow Polar's five-zone
model; they do not measure lactate or ventilatory thresholds.
"""
import math

LABELS = ("Très facile", "Facile", "Modérée", "Soutenue", "Élevée")


def valid_max_hr(value: float | None) -> bool:
    """Input sanity bounds, not individual physiological limits."""
    return bool(value is not None and math.isfinite(value) and 100 <= value <= 230 and value == int(value))


def running_zones(max_heart_rate: float | None) -> dict:
    result = {"ready": False, "rows": [], "reference": None}
    if max_heart_rate is None or not valid_max_hr(max_heart_rate):
        return {**result, "message": "Renseigne une FC maximale fiable pour afficher tes zones."}
    reference = int(max_heart_rate)
    bounds = [math.ceil(reference * percent / 100) for percent in (50, 60, 70, 80, 90, 100)]
    rows = [{"number": i + 1, "label": label, "low": bounds[i],
             "high": bounds[i + 1] - (1 if i < 4 else 0), "percent": f"{50 + 10 * i}–{60 + 10 * i} %"}
            for i, label in enumerate(LABELS)]
    return {**result, "ready": True, "reference": reference, "rows": rows}
