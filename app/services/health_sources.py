"""Stable source selection, independent of import order. Originals stay stored."""

from collections import defaultdict

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.health import HealthMetric
from app.models.user import User

WATCH_PRIORITY = ("COROS", "Garmin")
SOURCE_NOTE = (
    "Une seule mesure par jour : ta montre prioritaire, puis l’autre si la donnée manque. "
    "En automatique, COROS précède Garmin. Les données originales sont conservées séparément."
)


async def preference(db: AsyncSession, user_id: int) -> str | None:
    user = await db.get(User, user_id)
    return user.health_source if user else None


def select_metrics(rows, preferred: str | None = None) -> dict[tuple, HealthMetric]:
    """One row per (day, metric), direct watches before phone copies.

    A night follows its sleep source where possible; a missing heart metric
    may use the other watch with its own baseline. Never add two daily totals
    or take naps from a second watch over an already selected main night.
    Callers validate supported measurement methods before selection.
    """
    order = list(
        dict.fromkeys(([preferred] if preferred in WATCH_PRIORITY else []) + list(WATCH_PRIORITY))
    )

    def rank(row, anchor=None):
        src = row.source or ""
        return (
            0 if anchor is not None and src == anchor else 1,
            order.index(src) if src in order else len(order),
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
