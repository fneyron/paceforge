"""Running zones from a confirmed maximum, else a corroborated observed reference.

The automatic reference is a data-quality heuristic, not a physiological HRmax
estimator. Lap averages must support the peaks on independent days. Repetition
alone cannot validate a sensor, and submaximal outings can underestimate HRmax.
"""
import math
from collections.abc import Sequence
from datetime import date, timedelta
from statistics import median

from app.services.sante_training import RUNS, Session

LABELS = ("Très facile", "Facile", "Modérée", "Soutenue", "Élevée")


def valid_max_hr(value: float | None) -> bool:
    """Input sanity bounds, not individual physiological limits."""
    return bool(value is not None and math.isfinite(value) and 100 <= value <= 230 and value == int(value))


def observed_reference(sessions: Sequence[Session], today: date) -> dict | None:
    """Median of the highest cluster within 5 bpm, on ≥2 independent days.

    (H) Completed runs ≥10 min over a year; one corroborating day within 90 d.
    Each peak needs ≥2 laps/splits of 30 s–10 min totalling ≥3 min within 10 bpm
    below it, with ≥10 min (or half the run) of coherent HR coverage overall.
    Reject excessive lap coverage; summaries alone never qualify.
    """
    days: dict[date, tuple[float, int]] = {}
    for s in sessions:
        if (s.sport not in RUNS or not today - timedelta(days=365) <= s.day <= s.end_day <= today
                or not math.isfinite(s.minutes) or s.minutes < 10
                or s.hr_peak is None or not math.isfinite(s.hr_peak) or not 100 <= s.hr_peak <= 230
                or s.hr is None or not math.isfinite(s.hr) or not 25 <= s.hr <= s.hr_peak):
            continue
        segments = [(minutes, hr) for minutes, hr in s.segs or ()
                    if math.isfinite(minutes) and math.isfinite(hr)
                    and .5 <= minutes <= 10 and 25 <= hr <= s.hr_peak]
        coverage = sum(minutes for minutes, _ in segments)
        high = [minutes for minutes, hr in segments if hr >= s.hr_peak - 10]
        if (not min(10, s.minutes / 2) <= coverage <= s.minutes * 1.25
                or len(high) < 2 or sum(high) < 3):
            continue
        if s.day not in days or s.hr_peak > days[s.day][0]:
            days[s.day] = (s.hr_peak, s.id)
    for peak in sorted({peak for peak, _ in days.values()}, reverse=True):
        support = [(day, value, sid) for day, (value, sid) in days.items() if peak - 5 <= value <= peak]
        if len(support) >= 2 and max(day for day, _, _ in support) >= today - timedelta(days=90):
            return {"reference": math.floor(median(value for _, value, _ in support) + .5),
                    "evidence": [{"day": day, "peak": round(value), "id": sid}
                                 for day, value, sid in sorted(support, reverse=True)]}
    return None


def running_zones(max_heart_rate: float | None, sessions: Sequence[Session] = (), today: date | None = None) -> dict:
    result = {"ready": False, "rows": [], "reference": None, "estimated": False, "evidence": []}
    if max_heart_rate is not None and valid_max_hr(max_heart_rate):
        reference = int(max_heart_rate)
    else:
        observed = observed_reference(sessions, today) if today is not None else None
        if observed is None:
            return {**result, "message": "Les passages détaillés de tes sorties ne suffisent pas encore à établir "
                    "un repère automatique. Tu peux aussi renseigner une FC maximale confirmée."}
        result.update(observed, estimated=True)
        reference = observed["reference"]
    bounds = [math.ceil(reference * percent / 100) for percent in (50, 60, 70, 80, 90, 100)]
    rows = [{"number": i + 1, "label": label, "low": bounds[i],
             "high": bounds[i + 1] - (1 if i < 4 else 0), "percent": f"{50 + 10 * i}–{60 + 10 * i} %"}
            for i, label in enumerate(LABELS)]
    return {**result, "ready": True, "reference": reference, "rows": rows}
