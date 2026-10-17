"""Synthetic explanation/respiration fixtures; temporary SQLite, localhost only.

PF_HEALTH_EXPLANATION_SCENARIO=low|isolated|respiration|day-only|mixed
    uvicorn tests.browser.health_explanations:app --host 127.0.0.1 --port 8871
"""
import math
import os
from contextlib import asynccontextmanager
from datetime import datetime, time, timedelta, timezone

from sqlalchemy import select

from tests.browser import preview_app as preview
from app.models.health import HealthMetric, HealthSample
from app.models.mobile import MobileDaily
from app.routers import sante as sante_routes
from app.services import sante
from app.services.apple_nights import SDNN_METHOD
from app.services.health import HRV_METHOD
from app.services.night_measurements import ASLEEP

SCENARIO = os.environ.get("PF_HEALTH_EXPLANATION_SCENARIO", "low")
if SCENARIO not in {"low", "isolated", "respiration", "day-only", "mixed"}:
    raise ValueError("Use a synthetic low, isolated, respiration, day-only or mixed scenario")


@asynccontextmanager
async def lifespan(app):
    async with preview.lifespan(app):
        today = datetime.now(timezone.utc).date()
        async with preview.factory() as db:
            uid = app.state.user_id
            rows = (await db.scalars(select(HealthMetric).where(HealthMetric.user_id == uid))).all()
            for row in rows:
                age = (today - row.date).days
                if SCENARIO == "respiration":
                    row.source = "Garmin"
                if row.metric == "hrv":
                    row.value = 40 if SCENARIO in {"low", "day-only"} and age < 7 else 70 * math.exp(.08 * (age % 5 - 2))
                    row.details = {"method": HRV_METHOD, "n": 24}
                    if SCENARIO == "mixed" and age == 0:
                        row.value, row.source = 30, "Apple Santé:" + "b" * 64
                        row.details = {"method": SDNN_METHOD, "window_id": "synthetic-night", "n": 3}
                elif SCENARIO == "mixed" and row.metric == "sleep" and age == 0:
                    row.source = "Apple Santé:" + "b" * 64
                    row.details = {**row.details, "window_id": "synthetic-night"}
                elif row.metric == "hr_night":
                    row.value = 44 + age % 3
                    row.details = {"method": "points" if SCENARIO == "respiration" else "coros_sleep_summary"}
                if SCENARIO == "respiration" and row.metric in {"hrv", "hr_night"}:
                    row.details = {**row.details, "scope": ASLEEP}
                if SCENARIO == "respiration" and row.metric == "sleep" and row.date == today:
                    row.details = {**row.details, "timeline": True}
            if SCENARIO == "respiration":
                for age in range(60):
                    db.add(HealthMetric(user_id=uid, date=today - timedelta(days=age), metric="resp_night",
                                        value=18 if age < 2 else 14 + age % 3 / 10, source="Garmin",
                                        details={"method": "points", "scope": ASLEEP, "n": 24}))
                start = datetime.combine(today - timedelta(days=1), time(23))
                for kind, minutes in (("core", 70), ("deep", 55), ("core", 80), ("rem", 30), ("awake", 10),
                                      ("core", 75), ("deep", 35), ("core", 55), ("rem", 60), ("awake", 10)):
                    end = start + timedelta(minutes=minutes)
                    db.add(HealthSample(user_id=uid, metric="sleep", kind=kind, start_at=start,
                                        end_at=end, value=minutes, source="Garmin"))
                    start = end
            if SCENARIO == "day-only":
                for age in range(15):
                    db.add(MobileDaily(user_id=uid, platform="ios", date=today - timedelta(days=age),
                                       metric="resp_day", value=16 + age % 3 / 10, sources=["Apple Watch"]))
            await db.commit()
        app.state.health_explanation_day = today.isoformat()
        original = sante.health_page

        async def morning(*args, **kwargs):
            kwargs.setdefault("now", datetime.combine(today, time(9), timezone.utc))
            return await original(*args, **kwargs)

        sante.health_page = sante_routes.health_page = morning  # the router's own reference is the one /sante calls
        try:
            yield
        finally:
            sante.health_page = sante_routes.health_page = original


app = preview.app
app.router.lifespan_context = lifespan


@app.get("/__preview/health-explanation")
async def fixture():
    return {"synthetic": True, "scenario": SCENARIO, "day": app.state.health_explanation_day}
