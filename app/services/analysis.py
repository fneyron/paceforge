import logging
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import UserNotFoundError
from app.models.activity import Activity
from app.models.analysis import Analysis
from app.models.user import User
from app.services.claude import ClaudeService
from app.services.strava import StravaService
from app.services.training_load import calculate_training_load

logger = logging.getLogger(__name__)


class AnalysisOrchestrator:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.strava = StravaService(db)
        self.claude = ClaudeService()

    async def process_activity(
        self,
        owner_strava_id: int,
        strava_activity_id: int,
    ) -> Analysis:
        """
        Full pipeline: fetch from Strava, compute training load,
        analyze with Claude, store results.
        """
        # 1. Find user
        result = await self.db.execute(
            select(User).where(User.strava_athlete_id == owner_strava_id)
        )
        user = result.scalar_one_or_none()
        if not user:
            raise UserNotFoundError(f"strava_athlete_id={owner_strava_id}")

        # 2. Refresh token if needed
        user = await self.strava.refresh_token_if_needed(user)

        # 3. Fetch activity from Strava
        strava_data = await self.strava.get_activity(user, strava_activity_id)
        logger.info(
            "Fetched activity %d from Strava: %s (%s)",
            strava_activity_id,
            strava_data.get("name"),
            strava_data.get("sport_type"),
        )

        # 4. Upsert Activity
        activity = await self._upsert_activity(user.id, strava_activity_id, strava_data)

        # 5. Calculate training load
        training_load = await calculate_training_load(
            self.db,
            user.id,
            activity.start_date,
        )

        # 6. Fetch recent activities for context
        recent = await self._get_recent_activities(user.id, activity.start_date)

        # 7. Call Claude
        coaching_output, raw_response = await self.claude.analyze_activity(
            activity_data=strava_data,
            training_load=training_load.model_dump(),
            recent_activities=recent,
        )

        # 8. Store analysis
        analysis = await self._store_analysis(
            activity=activity,
            coaching_output=coaching_output,
            training_load=training_load,
            raw_response=raw_response,
        )

        logger.info("Analysis %d stored for activity %d", analysis.id, activity.id)
        return analysis

    async def _upsert_activity(
        self, user_id: int, strava_activity_id: int, data: dict
    ) -> Activity:
        result = await self.db.execute(
            select(Activity).where(Activity.strava_activity_id == strava_activity_id)
        )
        activity = result.scalar_one_or_none()

        start_date = data.get("start_date")
        if isinstance(start_date, str):
            start_date = datetime.fromisoformat(start_date.replace("Z", "+00:00"))

        fields = {
            "user_id": user_id,
            "strava_activity_id": strava_activity_id,
            "sport_type": data.get("sport_type", data.get("type", "Unknown")),
            "name": data.get("name", "Untitled"),
            "start_date": start_date,
            "distance": data.get("distance", 0),
            "moving_time": data.get("moving_time", 0),
            "elapsed_time": data.get("elapsed_time", 0),
            "total_elevation_gain": data.get("total_elevation_gain", 0),
            "average_speed": data.get("average_speed"),
            "max_speed": data.get("max_speed"),
            "average_heartrate": data.get("average_heartrate"),
            "max_heartrate": data.get("max_heartrate"),
            "average_cadence": data.get("average_cadence"),
            "average_watts": data.get("average_watts"),
            "max_watts": data.get("max_watts"),
            "weighted_average_watts": data.get("weighted_average_watts"),
            "suffer_score": data.get("suffer_score"),
            "calories": data.get("calories"),
            "laps": data.get("laps"),
            "splits_metric": data.get("splits_metric"),
            "best_efforts": data.get("best_efforts"),
            "raw_data": data,
        }

        if activity:
            for key, value in fields.items():
                setattr(activity, key, value)
        else:
            activity = Activity(**fields)
            self.db.add(activity)

        await self.db.flush()
        await self.db.refresh(activity)
        return activity

    async def _get_recent_activities(
        self, user_id: int, before_date: datetime, limit: int = 10
    ) -> list[dict]:
        result = await self.db.execute(
            select(Activity)
            .where(
                Activity.user_id == user_id,
                Activity.start_date < before_date,
            )
            .order_by(Activity.start_date.desc())
            .limit(limit)
        )
        activities = result.scalars().all()
        return [
            {
                "sport_type": a.sport_type,
                "distance": a.distance,
                "moving_time": a.moving_time,
                "start_date": a.start_date.isoformat() if a.start_date else None,
                "average_speed": a.average_speed,
                "average_heartrate": a.average_heartrate,
                "total_elevation_gain": a.total_elevation_gain,
            }
            for a in activities
        ]

    async def _store_analysis(
        self,
        activity: Activity,
        coaching_output,
        training_load,
        raw_response: str,
    ) -> Analysis:
        from app.config import settings

        # Delete existing analysis if re-analyzing
        result = await self.db.execute(
            select(Analysis).where(Analysis.activity_id == activity.id)
        )
        existing = result.scalar_one_or_none()
        if existing:
            await self.db.delete(existing)
            await self.db.flush()

        analysis = Analysis(
            activity_id=activity.id,
            summary=coaching_output.summary,
            strengths=coaching_output.strengths,
            improvements=coaching_output.improvements,
            next_workout_tip=coaching_output.next_workout_tip,
            strava_comment=coaching_output.strava_comment,
            fatigue_note=coaching_output.fatigue_note,
            training_load_7d_km=training_load.volume_7d_km,
            training_load_7d_hours=training_load.volume_7d_hours,
            training_load_7d_count=training_load.count_7d,
            training_load_28d_km=training_load.volume_28d_km,
            training_load_28d_hours=training_load.volume_28d_hours,
            training_load_28d_count=training_load.count_28d,
            model_used=settings.CLAUDE_MODEL,
            raw_response=raw_response,
        )
        self.db.add(analysis)
        await self.db.flush()
        await self.db.refresh(analysis)
        return analysis
