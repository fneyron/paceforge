"""Stable source selection, independent of import order. Originals stay stored."""

from bisect import bisect_left, bisect_right
from collections import defaultdict
from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.health import HealthMetric
from app.models.user import User

WATCH_PRIORITY = ("COROS", "Garmin", "Apple Santé")
SOURCE_NOTE = (
    "Une seule mesure par jour : ta source prioritaire, puis les autres si la donnée manque. "
    "En automatique, COROS précède Garmin, puis Apple Santé. Les données originales sont conservées séparément."
)


def family(source):
    return "Apple Santé" if source and source.startswith("Apple Santé:") else source


async def preference(db: AsyncSession, user_id: int) -> str | None:
    user = await db.get(User, user_id)
    return user.health_source if user else None


SERIES_DAYS = 28  # owner, 2026-10-14: scientific basis — between writers of one family, the most worn of 28 days


def select_metrics(rows, preferred: str | None = None) -> dict[tuple, HealthMetric]:
    """One row per (day, metric), direct watches before phone copies.

    A night follows its sleep source where possible; a missing heart metric
    may use the other watch with its own baseline. Never add two daily totals
    or take naps from a second watch over an already selected main night.
    Between two writers of one family (two Apple devices), the one with the most
    days of that metric in the 28 days up to the day (owner, 2026-10-14:
    scientific basis — they were ranked by their hash, so a second device could
    take the night over by its name), then the name.
    Callers validate supported measurement methods before selection.
    """
    rows = list(rows)
    order = list(
        dict.fromkeys(([preferred] if preferred in WATCH_PRIORITY else []) + list(WATCH_PRIORITY))
    )
    days: dict[tuple, list] = defaultdict(list)  # (source, metric) → its days, sorted
    for row in rows:
        days[row.source or "", row.metric].append(row.date)
    for v in days.values():
        v.sort()

    def worn(row) -> int:
        """The days of this row's metric its source holds in the 28 days up to the row's day (no later day)."""
        ds = days[row.source or "", row.metric]
        return bisect_right(ds, row.date) - bisect_left(ds, row.date - timedelta(days=SERIES_DAYS - 1))

    def rank(row, anchor=None):
        src = row.source or ""
        return (
            0 if anchor is not None and src == anchor else 1,
            order.index(family(src)) if family(src) in order else len(order),
            -worn(row),
            src,
        )

    grouped = defaultdict(list)
    for row in rows:
        grouped[row.date, row.metric].append(row)
    chosen = {key: min(items, key=rank) for key, items in grouped.items()}
    for (day, metric), items in grouped.items():
        sleep = chosen.get((day, "sleep"))
        if sleep is None or metric not in ("nap", "hrv", "hr_night", "resp_night"):
            continue
        same = [r for r in items if r.source == sleep.source]
        if metric == "nap" and not same:
            chosen.pop((day, metric), None)
        else:
            chosen[day, metric] = min(same or items, key=rank)
    return chosen
