"""Santé's « Tendances » (owner, 2026-10-16: « les tendances sur poids fc pas »): up to 90 days, its values
as dots, its trailing mean as a line, and that mean's change over 4 weeks in the card's head. Context only:
never a recovery input, never a verdict.

The night HR reads the FC de nuit card's own 7-night mean (nights.mean7: the same line over more days); the
weigh-ins and the steps a plain trailing mean, enough values in the window or none (H).
The display zooms to 14 or 30 days when the measurements fit, keeping today and the calculation history.
"""
import statistics
from datetime import date, timedelta

from app.services import nights as nt
from app.services import viz

TREND_DAYS = 90  # the owner's « 3 mois »
MEAN_DAYS = {"weight": (14, 2), "body_fat": (14, 2), "steps": (7, 4)}  # (H) the window (days), the values it needs
DELTA_DAYS = 28  # (H) the head reads the latest mean against the mean 4 weeks before
LEGEND = {"weight": ("pesée", "moyenne 14 jours"), "body_fat": ("pesée", "moyenne 14 jours"),
          "steps": ("jour", "moyenne 7 jours"), "hr": ("nuit", "moyenne 7 jours")}
COUNT_WORD = {"weight": "pesées", "body_fat": "pesées", "steps": "jours", "hr": "nuits"}


def days_to(today: date) -> list[date]:
    return [today - timedelta(days=TREND_DAYS - 1 - i) for i in range(TREND_DAYS)]


def trailing(values: list, window: int, need: int, skip: int | None = None) -> tuple[list, list]:
    """Each day's mean of the values of its `window` days (itself included; `skip`, a day still in progress,
    never), None with fewer than `need`; and how many it holds."""
    means, counts = [], []
    for i in range(len(values)):
        kept = [v for k, v in enumerate(values[max(0, i - window + 1):i + 1], max(0, i - window + 1))
                if v is not None and k != skip]
        means.append(statistics.fmean(kept) if len(kept) >= need else None)
        counts.append(len(kept))
    return means, counts


def delta(means: list, digits: int, unit: str) -> str | None:
    """« −0,8 kg en 4 sem. »: the latest mean against the one DELTA_DAYS before it, None without both."""
    last = next((i for i in range(len(means) - 1, -1, -1) if means[i] is not None), None)
    if last is None or last < DELTA_DAYS or means[last - DELTA_DAYS] is None:
        return None
    change = means[last] - means[last - DELTA_DAYS]
    return f"{viz.signed(0.0 if round(change, digits) == 0 else change, digits, unit)} en 4 sem."


def chart(metric: str, key: str, name: str, days: list[date], values: list, means: list, counts: list, *,
          unit: str, digits: int, min_span: float, sources=None, notes=None, daytime: bool = True,
          mean_sources=None) -> dict | None:
    """Draw the shortest 14/30/90-day window containing the measurements and two days of left margin.

    Keep today on the right so old measurements never appear current. Means and four-week changes are computed
    before cropping; every aligned series is sliced together. The vertical minimum span remains unchanged.
    """
    if not any(v is not None for v in values):
        return None
    first = next(i for i, value in enumerate(values) if value is not None)
    needed = len(days) - first + 2
    window = next((n for n in (14, 30, TREND_DAYS) if n >= needed), TREND_DAYS)
    start = max(0, len(days) - window)
    days, values, means, counts = days[start:], values[start:], means[start:], counts[start:]
    sources = sources[start:] if sources is not None else None
    mean_sources = mean_sources[start:] if mean_sources is not None else None
    notes = notes[start:] if notes is not None else None
    n = len(days)
    c = viz.night_card(
        key, days, values, band=[None] * n, prov=[False] * n, mean=means, unit=unit, unit_long=unit, name=name,
        digits=digits, min_span=min_span, sources=sources, mean_sources=mean_sources, mean_counts=counts,
        notes=notes, daytime=daytime, zero_base=metric == "steps", compact=True, show_source=False,
        mean_label=LEGEND[metric][1], count_word=COUNT_WORD[metric])
    c["legend"] = [("is-dot", LEGEND[metric][0])] + ([("is-mean", "moyenne glissante")] if c["mean"] else [])
    c["period_label"] = f"{n} jours · {days[0]:%d/%m} – {days[-1]:%d/%m}"
    return viz.card_average(c, [v for i, v in enumerate(values)
                                if not notes or notes[i] != "journée en cours"], unit=unit, digits=digits)


def daily_trend(metric: str, card: dict, rows: list, today: date, source_of, signal: tuple) -> dict:
    """A daily card (sante_daily.daily_card's value, date and source) over the 90 days up to `today`, drawn as
    `signal` (sante_daily.SIGNALS': name, unit, digits, the y axis' narrowest span); today's steps, a day still
    in progress, stay a dot outside the mean."""
    days = days_to(today)
    by_day = {r.date: r for r in rows if days[0] <= r.date <= today}
    values = [by_day[d].value if d in by_day else None for d in days]
    window, need = MEAN_DAYS[metric]
    skip = len(days) - 1 if metric == "steps" else None
    means, counts = trailing(values, window, need, skip)
    _, unit, digits, min_span, _ = signal
    notes = [("journée en cours" if i == skip else "") for i in range(len(days))]
    # one line across the days without a value and across the sources (the mean holds them all), never broken
    c = chart(metric, "trend-" + metric, card["title"], days, values, means, counts, unit=unit, digits=digits,
              min_span=min_span, sources=[source_of(by_day[d]) if d in by_day else None for d in days], notes=notes,
              mean_sources=[None] * len(days))
    return {**card, "chart": c, "delta": delta(means, digits, unit) if c else None,
            "trend": f"Moyenne glissante sur {window} jours, à partir de {need} valeurs." if c else None}


def night_trend(nights, today: date) -> dict | None:
    """« FC de nuit » over the 90 nights up to `today`: every measured night a dot, the card's 7-night mean."""
    days = days_to(today)
    values = [nights[d].value("hr") if d in nights else None for d in days]
    averages = [nt.mean7(nights, "hr", d) or {} for d in days]
    means = [m.get("value") for m in averages]
    c = chart("hr", "trend-hr", "FC de nuit", days, values, means, [m.get("n", 0) for m in averages],
              unit="bpm", digits=0, min_span=8, daytime=False,
              sources=[nights[d].source_of("hr") if d in nights else None for d in days],
              mean_sources=[nt.mean_source(nights, "hr", d) for d in days])
    if c is None:
        return None
    last, value = max((i, v) for i, v in enumerate(values) if v is not None)
    return {"key": "hr_night", "title": "FC de nuit", "chart": c, "delta": delta(means, 0, "bpm"),
            "value": viz.num(value, unit="bpm"), "latest": days[last].strftime("%d/%m/%Y"),
            "stale": False}

