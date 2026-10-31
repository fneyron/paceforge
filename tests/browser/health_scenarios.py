"""Synthetic Health edge cases, using only the preview's temporary SQLite database.

Run a separate localhost process per scenario:
    PF_HEALTH_SCENARIO=conflict .venv/bin/uvicorn tests.browser.health_scenarios:app --port 8868
    PF_HEALTH_SCENARIO=missing .venv/bin/uvicorn tests.browser.health_scenarios:app --port 8869
    PF_HEALTH_SCENARIO=stages .venv/bin/uvicorn tests.browser.health_scenarios:app --port 8870
"""
import os
from contextlib import asynccontextmanager
from datetime import datetime, time, timedelta, timezone

from sqlalchemy import delete, update

from tests.browser import preview_app as preview
from app.models.activity import Activity
from app.models.health import HealthMetric, HealthSample
from app.services import sante

SCENARIO = os.environ.get("PF_HEALTH_SCENARIO", "conflict")
if SCENARIO not in {"conflict", "missing", "stages"}:
    raise ValueError("Use the synthetic conflict, missing or stages Health scenario")


@asynccontextmanager
async def lifespan(app):
    async with preview.lifespan(app):
        today = datetime.now(timezone.utc).date()
        async with preview.factory() as db:
            uid = app.state.user_id
            # A recorded ultra that ended at 22:00 thirteen days ago. Its
            # neutral context must remain even after the old effort windows.
            db.add(Activity(
                user_id=uid, strava_activity_id=900001,
                name="Ultra synthétique", sport_type="TrailRun",
                start_date=datetime.combine(today - timedelta(days=13), time(6), timezone.utc),
                distance=100_000, moving_time=15 * 3600, elapsed_time=16 * 3600,
                total_elevation_gain=4500, average_heartrate=142, max_heartrate=180,
                raw_data={"utc_offset": 0, "workout_type": 1},
            ))
            if SCENARIO == "conflict":
                # 56 bpm is above the usual seven-day band (baseline 50–54),
                # below the two-night alert threshold. RMSSD stays habitual.
                for metric, value in (("hr_night", 56), ("hrv", 65)):
                    await db.execute(update(HealthMetric).where(
                        HealthMetric.user_id == uid,
                        HealthMetric.metric == metric,
                        HealthMetric.date >= today - timedelta(days=6),
                    ).values(value=value))
            elif SCENARIO == "missing":
                # Today's daily measures still exist. They cannot manufacture
                # a night or a new nocturnal index from yesterday's reading.
                await db.execute(delete(HealthMetric).where(
                    HealthMetric.user_id == uid, HealthMetric.date == today,
                    HealthMetric.metric.in_(("sleep", "hr_night", "hrv")),
                ))
            else:
                # Garmin supplies stage intervals. Keep the source coherent
                # across the reference and never synthesize intervals in app code.
                await db.execute(update(HealthMetric).where(
                    HealthMetric.user_id == uid,
                ).values(source="Garmin"))
                start = datetime.combine(today - timedelta(days=1), time(23))
                await db.execute(update(HealthMetric).where(
                    HealthMetric.user_id == uid, HealthMetric.date == today,
                    HealthMetric.metric == "sleep",
                ).values(details={"main_start": start.isoformat(),
                                  "main_end": datetime.combine(today, time(7)).isoformat(),
                                  "timeline": True}))
                for kind, minutes in (("core", 70), ("deep", 55), ("core", 80), ("rem", 30),
                                      ("awake", 10), ("core", 75), ("deep", 35), ("core", 55),
                                      ("rem", 60), ("awake", 10)):
                    end = start + timedelta(minutes=minutes)
                    db.add(HealthSample(user_id=uid, metric="sleep", kind=kind, start_at=start,
                                        end_at=end, value=minutes, source="Garmin"))
                    start = end
            await db.commit()
        app.state.health_scenario = SCENARIO
        app.state.health_scenario_day = today.isoformat()
        # Keep the pending-night example reproducible at any hour: the page reads the athlete's clock at 09:00 on the
        # fixture's day (before noon, a missing night keeps yesterday's « Dernier bilan », sante.cycle_day). The
        # router imported health_page by name, so its reference is pinned too (pinning the module's alone left the
        # page on the real clock: « Aujourd'hui » after noon). This process serves only synthetic preview data.
        from app.routers import sante as sante_router

        original_health_page = sante.health_page

        async def morning_page(*args, **kwargs):
            kwargs.setdefault("now", datetime.combine(today, time(9), timezone.utc))
            return await original_health_page(*args, **kwargs)

        sante.health_page = sante_router.health_page = morning_page
        try:
            yield
        finally:
            sante.health_page = sante_router.health_page = original_health_page


app = preview.app
app.router.lifespan_context = lifespan


@app.get("/__preview/health-scenario")
async def scenario():
    return {"synthetic": True, "scenario": SCENARIO, "day": app.state.health_scenario_day, "clock": "09:00 UTC"}
