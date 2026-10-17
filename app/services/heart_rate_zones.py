"""Indicative running HR zones, with the observed reference exposed to the athlete.

Five conventional bands of maximum HR (50/60/70/80/90/100 %, Polar's HR-zone model).
Activity summaries cannot establish a physiological maximum or lactate thresholds. We only use an observed
running peak corroborated within 5 bpm on three distinct days in the past year (H), never the training-load
model's default 190 bpm or nightly HR as a resting-HR measurement. This filter reduces isolated spikes; it
does not establish sensor accuracy or prove that the athlete reached their maximum. No time-in-zone is inferred
from average HR. Existing training loads and race pacing are unchanged.
"""

import math
from datetime import date, timedelta

from app.services.sante_training import RUNS, Session

MIN_DAYS = 3
PEAK_TOLERANCE = 5  # bpm, heuristic corroboration window
HISTORY_DAYS = 365
RECENT_DAYS = 90
LABELS = ("Très facile", "Facile", "Modérée", "Soutenue", "Élevée")


def running_zones(sessions: list[Session], today: date) -> dict:
    """Return a display model, including an honest empty state, for the logged-in athlete's sessions.

    The highest repeated peak is a provisional reference, not a measured HRmax. Multiple imports on the same
    day never increase corroboration. Durations/ranges/recency are data-quality heuristics, not medical limits.
    """
    days: dict[date, float] = {}
    for session in sessions:
        peak = session.hr_peak
        if (session.sport not in RUNS or not today - timedelta(days=HISTORY_DAYS) <= session.day <= today
                or session.end_day > today or not math.isfinite(session.minutes) or session.minutes < 10
                or peak is None or not math.isfinite(peak) or not 100 < peak <= 230):
            continue
        if session.hr is not None and (not math.isfinite(session.hr) or not 25 <= session.hr <= peak):
            continue
        days[session.day] = max(peak, days.get(session.day, 0))

    result = {"ready": False, "rows": [], "days": len(days), "reference": None}
    if len(days) < MIN_DAYS:
        return {**result, "message": "Au moins 3 jours de course avec une FC maximale sont nécessaires."}

    for peak in sorted(set(days.values()), reverse=True):
        support = [d for d, value in days.items() if peak - PEAK_TOLERANCE <= value <= peak]
        if len(support) >= MIN_DAYS:
            break
    else:
        return {**result, "message": "Les pics de tes sorties ne concordent pas encore assez pour proposer des zones."}

    latest = max(support)
    if latest < today - timedelta(days=RECENT_DAYS):
        return {**result,
                "message": "Les pics concordants datent de plus de 3 mois. Une sortie récente est nécessaire."}

    reference = round(peak)
    bounds = [math.ceil(reference * percent / 100) for percent in (50, 60, 70, 80, 90, 100)]
    rows = [{"number": i + 1, "label": label, "low": bounds[i],
             "high": bounds[i + 1] - (1 if i < 4 else 0), "percent": f"{50 + 10 * i}–{60 + 10 * i} %"}
            for i, label in enumerate(LABELS)]
    return {**result, "ready": True, "reference": reference, "rows": rows,
            "support_days": len(support), "latest": latest.strftime("%d/%m/%Y"),
            "isolated_peak": round(max(days.values())) if max(days.values()) > peak else None}
