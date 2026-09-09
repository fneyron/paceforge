"""Triathlon: chain swim + T1 + bike + T2 + run into one timeline and one
fuel plan. The bike and run legs reuse their own saved plans (bike power/wind
model, run pace model); the swim is distance × pace since you can't plan a
swim from a GPX. Nothing here is medical advice — these are training
guidelines (60–90 g/h, more on the bike than on the run)."""

from __future__ import annotations

import statistics
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.activity import Activity
from app.services.nutrition import default_targets

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


def triathlon_nutrition(bike_h: float, run_h: float, weight_kg: float | None = None, carbs_override: float | None = None) -> dict:
    """Carbs plan across the event: the clock starts at T1 (no eating in the
    water); the bike carries most of the intake, the run a bit less."""
    active_h = max(0.0, bike_h + run_h)
    base = float(carbs_override) if carbs_override else float(default_targets(active_h, None)["carbs_g_per_h"])
    bike_rate = min(base + 10, 100.0)
    run_rate = max(base - 10, 40.0)
    bike_g = bike_rate * bike_h
    run_g = run_rate * run_h
    total = bike_g + run_g
    notes = []
    if bike_rate > 90:
        notes.append("Au-delà de 90 g/h : mélange glucose + fructose et intestin entraîné, sinon vise 90.")
    if active_h >= 6:
        notes.append("Effort long : commence à manger dès les premières minutes de vélo, pas quand la faim arrive.")
    return {
        "base_g_per_h": round(base),
        "bike_g_per_h": round(bike_rate),
        "run_g_per_h": round(run_rate),
        "bike_g": round(bike_g),
        "run_g": round(run_g),
        "total_g": round(total),
        "gels_equiv": round(total / 25) if total else 0,
        "breakfast_g": (round(weight_kg * 1), round(weight_kg * 2)) if weight_kg else None,
        "active_h": round(active_h, 2),
        "notes": notes,
    }


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
