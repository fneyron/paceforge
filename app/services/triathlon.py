"""Triathlon: chain swim + T1 + bike + T2 + run into one timeline. The bike
and run legs reuse their own saved plans (bike power/wind model, run pace
model); the swim is distance × pace since you can't plan a swim from a GPX."""

from __future__ import annotations

import statistics
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.activity import Activity

FORMATS = {
    "S": {"label": "S · 750 m / 20 km / 5 km", "swim_m": 750},
    "M": {"label": "M · 1,5 km / 40 km / 10 km", "swim_m": 1500},
    "half": {"label": "Half · 1,9 km / 90 km / 21 km", "swim_m": 1900},
    "full": {"label": "Full · 3,8 km / 180 km / 42 km", "swim_m": 3800},
}
DEFAULT_T1_S = 3 * 60
DEFAULT_T2_S = 2 * 60


async def estimate_swim_pace(db: AsyncSession, user_id: int, months: int = 12) -> float | None:
    """Median swim pace (s per 100 m) over the athlete's recent Strava swims."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=months * 30)
    result = await db.execute(
        select(Activity)
        .where(
            Activity.user_id == user_id,
            Activity.sport_type == "Swim",
            Activity.start_date >= cutoff,
            Activity.distance >= 500,
            Activity.moving_time > 0,
        )
        .order_by(Activity.start_date.desc())
        .limit(30)
    )
    paces = []
    for a in result.scalars().all():
        pace = a.moving_time / (a.distance / 100.0)
        if 60 <= pace <= 240:  # 1:00 to 4:00 per 100 m
            paces.append(pace)
    return round(statistics.median(paces), 1) if paces else None


def build_timeline(start_offset_s: int, swim_s: int, t1_s: int, bike_s: int, t2_s: int, run_s: int) -> list[dict]:
    """Rows Natation → T1 → Vélo → T2 → Course with clock in/out."""
    rows = []
    t = int(start_offset_s)
    for key, label, dur in (("swim", "Natation", swim_s), ("t1", "T1", t1_s), ("bike", "Vélo", bike_s), ("t2", "T2", t2_s), ("run", "Course à pied", run_s)):
        rows.append({"key": key, "label": label, "duration_s": int(dur), "start_clock_s": t, "end_clock_s": t + int(dur), "elapsed_end_s": t + int(dur) - int(start_offset_s)})
        t += int(dur)
    return rows


def parse_pace_100m(text: str | None) -> float | None:
    """'1:59' or '119' → seconds per 100 m."""
    if not text:
        return None
    text = text.strip().replace("'", ":").replace('"', "")
    try:
        if ":" in text:
            m, s = text.split(":")[:2]
            return int(m) * 60 + int(s)
        return float(text)
    except ValueError:
        return None


def fmt_pace_100m(seconds: float | None) -> str:
    if not seconds:
        return ""
    return f"{int(seconds // 60)}:{int(seconds % 60):02d}"
