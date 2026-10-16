"""Synthetic multisport activities preview; temporary database, localhost only.

PF_ACTIVITIES_SCENARIO=mixed|empty uvicorn tests.browser.activities_visual:app
    --host 127.0.0.1 --port 8881
"""
import os
from contextlib import asynccontextmanager
from datetime import datetime, time, timedelta, timezone

from sqlalchemy import delete

from tests.browser import preview_app as preview
from app.models.activity import Activity


SCENARIO = os.environ.get("PF_ACTIVITIES_SCENARIO", "mixed")
if SCENARIO not in {"mixed", "empty"}:
    raise ValueError("Use a synthetic mixed or empty activities scenario")


@asynccontextmanager
async def lifespan(app):
    async with preview.lifespan(app):
        today = datetime.now(timezone.utc).date()
        async with preview.factory() as db:
            await db.execute(delete(Activity))
            if SCENARIO == "mixed":
                # Distances in metres; durations in seconds. Never infer a GPS track.
                records = [
                    (0, "Run", "Footing au bord du lac", 6800, 2450, 28, 132),
                    (1, "WeightTraining", "Renforcement · jambes et gainage", 0, 2280, 0, 112),
                    (2, "TrailRun", "Sentier des crêtes", 12400, 5130, 620, 146),
                    (3, "Workout", "Mobilité après la sortie", 0, 1200, 0, None),
                    (4, "Ride", "Boucle des villages", 42800, 6200, 390, 128),
                    (5, "Swim", "Natation en bassin", 1500, 2190, 0, None),
                ]
                for week in range(1, 17):
                    records.extend([
                        (week * 7, "Run", "Course facile", 7500 + week % 3 * 500,
                         2700 + week % 3 * 180, 18, 131 + week % 4),
                        (week * 7 + 3, "TrailRun", "Sortie nature", 11000 + week % 4 * 1000,
                         4800 + week % 4 * 300, 350 + week % 4 * 80, 143 + week % 5),
                        (week * 7 + 5, "Workout", "Mobilité et récupération", 0, 1200, 0, None),
                    ])
                for index, (age, sport, name, distance, duration, elevation, hr) in enumerate(records):
                    db.add(Activity(
                        user_id=app.state.user_id, strava_activity_id=900000 + index,
                        name=name, sport_type=sport,
                        start_date=datetime.combine(today - timedelta(days=age), time(7), timezone.utc),
                        distance=distance, moving_time=duration, elapsed_time=duration + 60,
                        total_elevation_gain=elevation, average_heartrate=hr,
                        average_speed=distance / duration if distance else None,
                        raw_data={"utc_offset": 0},
                    ))
            await db.commit()
        app.state.activities_preview_day = today.isoformat()
        yield


app = preview.app
app.router.lifespan_context = lifespan


@app.get("/__preview/activities")
async def fixture():
    return {"synthetic": True, "scenario": SCENARIO, "day": app.state.activities_preview_day}
