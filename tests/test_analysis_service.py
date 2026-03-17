import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.activity import Activity
from app.models.user import User
from app.schemas.analysis import ClaudeCoachingOutput
from app.services.analysis import AnalysisOrchestrator


@pytest.fixture
def mock_strava_activity():
    return {
        "id": 999999,
        "name": "Test Run",
        "sport_type": "Run",
        "type": "Run",
        "start_date": "2024-03-10T08:00:00Z",
        "distance": 10000,
        "moving_time": 3000,
        "elapsed_time": 3100,
        "total_elevation_gain": 50,
        "average_speed": 3.33,
        "max_speed": 4.0,
        "average_heartrate": 150,
        "max_heartrate": 168,
        "average_cadence": 85,
        "splits_metric": [
            {"distance": 1000, "moving_time": 300, "average_heartrate": 145},
        ],
    }


@pytest.fixture
def mock_coaching_output():
    return ClaudeCoachingOutput(
        summary="Bonne séance test.",
        strengths=["Test strength"],
        improvements=["Test improvement"],
        next_workout_tip="Test tip",
        strava_comment="Test comment",
        fatigue_note=None,
    )


@pytest.mark.asyncio
async def test_process_activity_full_pipeline(
    db_session: AsyncSession,
    test_user: User,
    mock_strava_activity: dict,
    mock_coaching_output: ClaudeCoachingOutput,
):
    orchestrator = AnalysisOrchestrator(db_session)

    # Mock StravaService.for_user to return a mock service
    mock_strava = MagicMock()
    mock_strava.refresh_token_if_needed = AsyncMock(return_value=test_user)
    mock_strava.get_activity = AsyncMock(return_value=mock_strava_activity)
    mock_strava.get_activity_streams = AsyncMock(return_value=None)

    with patch(
        "app.services.analysis.StravaService.for_user",
        return_value=mock_strava,
    ), patch.object(
        orchestrator.claude,
        "analyze_activity",
        new_callable=AsyncMock,
        return_value=(mock_coaching_output, '{"test": true}'),
    ):
        analysis = await orchestrator.process_activity(
            owner_strava_id=test_user.strava_athlete_id,
            strava_activity_id=999999,
        )

    assert analysis.summary == "Bonne séance test."
    assert analysis.strengths == ["Test strength"]
    assert analysis.improvements == ["Test improvement"]
    assert analysis.next_workout_tip == "Test tip"
    assert analysis.comment_posted is False


@pytest.mark.asyncio
async def test_process_activity_unknown_user(db_session: AsyncSession):
    orchestrator = AnalysisOrchestrator(db_session)

    from app.exceptions import UserNotFoundError

    with pytest.raises(UserNotFoundError):
        await orchestrator.process_activity(
            owner_strava_id=99999999,
            strava_activity_id=12345,
        )
