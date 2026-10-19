"""One HR reference for health, load and race guidance.

An observed reference is a transparent data-quality heuristic, not a validated
physiological HRmax estimate. Nothing is persisted as a confirmed maximum.
"""
import math
from collections.abc import Sequence
from datetime import date, timedelta
from statistics import median
from typing import Any


def valid_max_hr(value: float | None) -> bool:
    """Input sanity bounds, not individual physiological limits."""
    return bool(value is not None and math.isfinite(value) and 100 <= value <= 230 and value == int(value))


def observed_reference(sessions: Sequence[Any], today: date) -> dict | None:
    """Median of the highest cluster within 5 bpm, on ≥2 independent days.

    (H) Completed runs ≥10 min over a year; one corroborating day within 90 d.
    Each peak needs ≥2 laps/splits of 30 s–10 min totalling ≥3 min within 10 bpm
    below it, with ≥10 min (or half the run) of coherent HR coverage overall.
    Reject excessive lap coverage; summaries alone never qualify.
    """
    days: dict[date, tuple[float, int]] = {}
    for s in sessions:
        if (s.sport not in {"Run", "TrailRun"} or not today - timedelta(days=365) <= s.day <= s.end_day <= today
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


def resolve(sessions: Sequence[Any], today: date | None, maximum: float | None = None) -> dict:
    """Confirmed profile value first, else corroborated completed runs as of the day."""
    result: dict[str, Any] = {"reference": None, "source": "unavailable", "estimated": False, "evidence": []}
    if maximum is not None and valid_max_hr(maximum):
        return {**result, "reference": int(maximum), "source": "confirmed"}
    observed = observed_reference(sessions, today) if today is not None else None
    return {**result, **observed, "source": "observed", "estimated": True} if observed else result


async def for_athlete(db, user_id: int, today: date, *, sessions=None, zone=None) -> dict:
    """Read only this athlete's profile and detailed activities before resolving."""
    from app.models.user import User
    from app.services import sante_training as st

    user = await db.get(User, user_id)
    sessions = sessions if sessions is not None else await st.load_sessions(db, user_id, today, zone=zone)
    await st.load_hr_segments(db, user_id, sessions, today - timedelta(days=st.HISTORY_DAYS))
    return resolve(sessions, today, getattr(user, "max_heart_rate", None))
