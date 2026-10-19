"""Auto-calculate training zones from athlete data."""

import logging
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.activity import Activity
from app.services import heart_rate_reference
from app.services import sante_training as st
from app.services.heart_rate_zones import running_zones

logger = logging.getLogger(__name__)


async def estimate_training_zones(db: AsyncSession, user_id: int, today: date | None = None) -> dict:
    """Same HR reference and boundaries as Santé; pace estimates stay separate."""
    today = today if today is not None else await st.athlete_today(db, user_id)
    reference = await heart_rate_reference.for_athlete(db, user_id, today)
    zones: dict = {"hr_reference": reference}
    if reference["reference"] is not None:
        zones["max_hr"] = reference["reference"]
        rows = running_zones(reference["reference"])["rows"]
        zones["hr_zones"] = [{"zone": row["number"], "label": row["label"],
                              "min": row["low"], "max": row["high"]} for row in rows]

    # Estimate pace zones from best recent efforts
    # Use average speed from recent runs, ordered by speed
    result_speeds = await db.execute(
        select(Activity.average_speed)
        .where(
            Activity.user_id == user_id,
            Activity.sport_type.in_(["Run", "TrailRun"]),
            Activity.average_speed.is_not(None),
            Activity.average_speed > 0,
            Activity.distance > 3000,  # At least 3km
        )
        .order_by(Activity.average_speed.desc())
        .limit(20)
    )
    speeds = [row[0] for row in result_speeds.all()]

    if len(speeds) >= 3:
        # Fastest pace = threshold estimate
        # Use top 10% as VMA estimate
        vma_speed = speeds[0]  # m/s
        threshold_speed = speeds[max(0, len(speeds) // 5)]  # top 20%
        easy_speed = speeds[len(speeds) * 2 // 3]  # typical easy pace

        def speed_to_pace(s: float) -> str:
            if s <= 0:
                return "—"
            pace = 1000 / s  # seconds per km
            m, sec = divmod(int(pace), 60)
            return f"{m}:{sec:02d}/km"

        zones["vma_speed"] = round(vma_speed * 3.6, 1)  # km/h
        zones["pace_zones"] = [
            {"zone": 1, "label": f"Récup {speed_to_pace(easy_speed * 0.85)} - {speed_to_pace(easy_speed * 0.95)}",
             "min_speed": easy_speed * 0.85, "max_speed": easy_speed * 0.95},
            {"zone": 2, "label": f"Endurance {speed_to_pace(easy_speed * 0.95)} - {speed_to_pace(easy_speed)}",
             "min_speed": easy_speed * 0.95, "max_speed": easy_speed},
            {"zone": 3, "label": f"Tempo {speed_to_pace(threshold_speed * 0.90)} - {speed_to_pace(threshold_speed * 0.95)}",
             "min_speed": threshold_speed * 0.90, "max_speed": threshold_speed * 0.95},
            {"zone": 4, "label": f"Seuil {speed_to_pace(threshold_speed * 0.95)} - {speed_to_pace(threshold_speed)}",
             "min_speed": threshold_speed * 0.95, "max_speed": threshold_speed},
            {"zone": 5, "label": f"VMA {speed_to_pace(vma_speed * 0.95)} - {speed_to_pace(vma_speed)}",
             "min_speed": vma_speed * 0.95, "max_speed": vma_speed},
        ]

    return zones
