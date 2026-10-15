"""Synthetic populated loading preview, localhost only; no provider connection.

Run from the repository with the verification dependencies installed::

    python -m uvicorn tests.browser.loading_preview:app --host 127.0.0.1 --port 8891

The existing preview lifespan owns and removes the temporary SQLite database.
Browser checks: PF_PREVIEW_URL=http://127.0.0.1:8891 followed by
``node tests/browser/{tab-navigation,chart-readouts}.cjs`` with Playwright available.
"""

import os

os.environ.update(
    DEBUG="false", COROS_SYNC="false", GARMIN_SYNC="false", ACTIVITY_ENV_FETCH="false"
)
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from sqlalchemy import delete, event
from tests.browser import preview_app as preview
from app.models.activity import Activity
from app.models.health import HealthMetric
from app.models.user import User
from app.services.health import HRV_METHOD

COUNTS = {"days": 1095, "activities": 0, "metrics": 0}
SQL = []


@event.listens_for(preview.engine.sync_engine, "before_cursor_execute")
def before(conn, cursor, statement, parameters, context, executemany):
    context._pf_started = time.perf_counter()


@event.listens_for(preview.engine.sync_engine, "after_cursor_execute")
def after(conn, cursor, statement, parameters, context, executemany):
    SQL.append({"ms": (time.perf_counter() - context._pf_started) * 1000, "sql": statement[:160]})


@asynccontextmanager
async def lifespan(app):
    async with preview.lifespan(app):
        now = datetime.now(timezone.utc)
        today = now.date()
        raw = {"utc_offset": 7200, "synthetic_payload": "x" * 50000}
        streams = {"time": list(range(8000)), "heartrate": [130 + i % 15 for i in range(8000)]}
        async with preview.factory() as db:
            await db.execute(delete(Activity))
            await db.execute(delete(HealthMetric))
            user = await db.get(User, app.state.user_id)
            user.timezone = "Europe/Paris"
            user.health_source = "COROS"
            for i in range(COUNTS["days"]):
                day = today - timedelta(days=i)
                sleep = 430 + i % 6 * 12
                for metric, value, det in [
                    (
                        "sleep",
                        sleep,
                        dict(
                            main_start=str(day - timedelta(days=1)) + "T22:55",
                            main_end=str(day) + "T07:00",
                            timeline=False,
                            stages=dict(awake=15, light=270, deep=85, rem=110),
                        ),
                    ),
                    ("hr_night", 43 + i % 5, {}),
                    ("hrv", 72 + i % 15, dict(method=HRV_METHOD, n=20)),
                ]:
                    db.add(
                        HealthMetric(
                            user_id=user.id,
                            date=day,
                            metric=metric,
                            value=value,
                            source="COROS",
                            details=det,
                        )
                    )
                    COUNTS["metrics"] += 1
                for metric, value in [
                    ("resp_night", 14.1 + i % 3 * 0.2),
                    ("hr_day", 63 + i % 4),
                    ("weight", 67.4 + i % 7 * 0.1),
                    ("body_fat", 16.5 + i % 3 * 0.1),
                    ("steps", 8500 + i % 9 * 300),
                    ("stress", 25 + i % 12),
                ]:
                    db.add(
                        HealthMetric(
                            user_id=user.id,
                            date=day,
                            metric=metric,
                            value=value,
                            source="Garmin",
                            details={"synthetic_raw_day": "x" * 22000},
                        )
                    )
                    COUNTS["metrics"] += 1
                if i % 7 != 6:
                    sport, name, km, minutes, elev, hr = (
                        ("Run", "Footing facile", 9, 55, 45, 132)
                        if i % 3
                        else ("Workout", "Renforcement et mobilité", 0, 40, 0, 110)
                    )
                    if i == 17:
                        sport, name, km, minutes, elev, hr = (
                            "TrailRun",
                            "Ultra de démonstration",
                            105,
                            1140,
                            6000,
                            141,
                        )
                    elif i % 7 == 5:
                        sport, name, km, minutes, elev, hr = (
                            "TrailRun",
                            "Sortie longue en montagne",
                            24,
                            180,
                            1200,
                            143,
                        )
                    elif i % 7 == 2:
                        sport, name, km, minutes, elev, hr = "Ride", "Vélo facile", 35, 95, 250, 126
                    db.add(
                        Activity(
                            user_id=user.id,
                            strava_activity_id=900000 + i,
                            name=name,
                            sport_type=sport,
                            start_date=now.replace(hour=5, minute=0, second=0, microsecond=0)
                            - timedelta(days=i),
                            distance=km * 1000,
                            moving_time=minutes * 60,
                            elapsed_time=minutes * 60 + 30,
                            average_heartrate=hr,
                            average_speed=km * 1000 / (minutes * 60),
                            max_heartrate=180,
                            total_elevation_gain=elev,
                            raw_data=raw,
                            streams_data=streams,
                            splits_metric=[
                                {
                                    "distance": 1000,
                                    "moving_time": 360,
                                    "average_heartrate": hr,
                                    "elevation_difference": 3,
                                }
                            ]
                            * max(1, int(km)),
                        )
                    )
                    COUNTS["activities"] += 1
            await db.commit()
        SQL.clear()
        yield


app = preview.app
app.router.lifespan_context = lifespan


@app.middleware("http")
async def measurements(request, call_next):
    n = len(SQL)
    started = time.perf_counter()
    response = await call_next(request)
    ms = (time.perf_counter() - started) * 1000
    rows = SQL[n:]
    response.headers["Server-Timing"] = (
        f'pf;dur={ms:.2f}, sql;dur={sum(r["ms"] for r in rows):.2f};desc="{len(rows)} queries"'
    )
    return response


@app.get("/__preview/performance")
async def fixture():
    return {"synthetic": True, **COUNTS}
