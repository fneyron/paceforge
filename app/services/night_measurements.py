"""Select timed sleep readings and keep incompatible nightly series separate."""

from datetime import datetime

FILTER_READ = "night_filter_v1"
ASLEEP = "sleep_stages_v1"
WINDOW = "main_window_v1"
SLEEP_KINDS = {"core", "deep", "rem", "asleep"}


def select_readings(readings, start: datetime, end: datetime, intervals) -> tuple[list, dict]:
    """Use half-open intervals. Explicit wake wins over any overlapping sleep.

    A known timeline only admits readings in an asleep stage; gaps are unknown,
    never invented sleep. Without a timeline, retain a labelled window estimate.
    """
    stages = [
        (kind, max(a, start), min(b, end))
        for kind, a, b in intervals
        if kind in SLEEP_KINDS | {"awake"} and max(a, start) < min(b, end)
    ]
    # Retransmitted samples must not inflate the 12-reading minimum or their weight.
    inside = sorted({t: v for t, v in readings if start <= t < end}.items())
    if not stages:
        return [v for _, v in inside], {"scope": WINDOW}
    values, awake, unknown = [], 0, 0
    for t, value in inside:
        if any(kind == "awake" and a <= t < b for kind, a, b in stages):
            awake += 1
        elif any(kind in SLEEP_KINDS and a <= t < b for kind, a, b in stages):
            values.append(value)
        else:
            unknown += 1
    return values, {"scope": ASLEEP, "excluded_awake": awake, "excluded_unknown": unknown}


def series_source(source: str | None, details: dict | None, metric: str) -> str | None:
    """A readable series identity, used by means, reference bands and alerts."""
    details = details or {}
    if source == "Garmin":
        if metric == "resp" and details.get("method") == "garmin_summary":
            return "Garmin (résumé)"
        if details.get("scope") == ASLEEP:
            return "Garmin (sommeil)"
        if details.get("scope") == WINDOW:
            return "Garmin (nuit sans phases)"
    return source


def measurement_note(source: str | None, details: dict | None, metric: str) -> str:
    details = details or {}
    if source == "Garmin":
        if details.get("scope") == ASLEEP:
            return "éveils détectés et périodes sans phase exclus"
        if details.get("scope") == WINDOW:
            return "éveils possibles : phases indisponibles"
        return "éveils possibles : ancien calcul"
    if source == "COROS":
        if metric == "hrv":
            return "éveils intermédiaires possibles"
        return "moyenne COROS : exclusion des éveils non vérifiable"
    return "exclusion des éveils non vérifiable"
