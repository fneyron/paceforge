"""Running zones from a confirmed maximum, else a corroborated observed reference.

The automatic reference is a data-quality heuristic, not a physiological HRmax
estimator. Lap averages must support the peaks on independent days. Repetition
alone cannot validate a sensor, and submaximal outings can underestimate HRmax.
"""
import math
from collections.abc import Sequence
from datetime import date
from typing import Any

from app.services.heart_rate_reference import observed_reference as observed_reference
from app.services.heart_rate_reference import resolve
from app.services.heart_rate_reference import valid_max_hr as valid_max_hr

LABELS = ("Très facile", "Facile", "Modérée", "Soutenue", "Élevée")


def running_zones(max_heart_rate: float | None, sessions: Sequence[Any] = (), today: date | None = None) -> dict:
    reference_data = resolve(sessions, today, max_heart_rate)
    result = {"ready": False, "rows": [], **reference_data}
    reference = reference_data["reference"]
    if reference is None:
        return {**result, "message": "Les passages détaillés de tes sorties ne suffisent pas encore à établir "
                "un repère automatique. Tu peux aussi renseigner une FC maximale confirmée."}
    bounds = [math.ceil(reference * percent / 100) for percent in (50, 60, 70, 80, 90, 100)]
    rows = [{"number": i + 1, "label": label, "low": bounds[i],
             "high": bounds[i + 1] - (1 if i < 4 else 0), "percent": f"{50 + 10 * i}–{60 + 10 * i} %"}
            for i, label in enumerate(LABELS)]
    return {**result, "ready": True, "reference": reference, "rows": rows}
