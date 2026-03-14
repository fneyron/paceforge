import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.exceptions import ClaudeAPIError
from app.services.claude import ClaudeService


@pytest.fixture
def claude_service():
    with patch("app.services.claude.settings") as mock_settings:
        mock_settings.ANTHROPIC_API_KEY = "test-key"
        mock_settings.CLAUDE_MODEL = "claude-sonnet-4-20250514"
        service = ClaudeService()
        return service


@pytest.fixture
def sample_activity_data():
    return {
        "sport_type": "Run",
        "name": "Morning Run",
        "distance": 10000,
        "moving_time": 3000,
        "elapsed_time": 3100,
        "average_speed": 3.33,
        "average_heartrate": 150,
        "max_heartrate": 168,
        "average_cadence": 85,
        "total_elevation_gain": 50,
        "splits_metric": [
            {"distance": 1000, "moving_time": 300, "average_heartrate": 145},
            {"distance": 1000, "moving_time": 298, "average_heartrate": 148},
        ],
    }


@pytest.fixture
def sample_training_load():
    return {
        "volume_7d_km": 25.0,
        "volume_7d_hours": 2.5,
        "count_7d": 3,
        "volume_28d_km": 90.0,
        "volume_28d_hours": 9.0,
        "count_28d": 12,
        "sport_breakdown_7d": {"Run": 25.0},
    }


@pytest.fixture
def valid_claude_response():
    return json.dumps({
        "summary": "Bonne sortie d'endurance avec une allure régulière.",
        "strengths": ["Allure stable", "Cadence régulière"],
        "improvements": ["Légère dérive cardiaque"],
        "next_workout_tip": "Essaye de partir plus lent.",
        "strava_comment": "Analyse automatique 🧠\n\nBonne régularité.\n\n👉 Pars plus lent.\n\nAnalyse générée par PaceForge",
        "fatigue_note": None,
    })


@pytest.mark.asyncio
async def test_analyze_activity_success(
    claude_service: ClaudeService,
    sample_activity_data,
    sample_training_load,
    valid_claude_response,
):
    mock_response = MagicMock()
    mock_response.content = [MagicMock(text=valid_claude_response)]
    mock_response.usage = MagicMock(input_tokens=500, output_tokens=200)

    claude_service.client.messages.create = AsyncMock(return_value=mock_response)

    coaching_output, raw = await claude_service.analyze_activity(
        activity_data=sample_activity_data,
        training_load=sample_training_load,
        recent_activities=[],
    )

    assert coaching_output.summary == "Bonne sortie d'endurance avec une allure régulière."
    assert len(coaching_output.strengths) == 2
    assert len(coaching_output.improvements) == 1
    assert raw == valid_claude_response


@pytest.mark.asyncio
async def test_parse_response_with_code_fences(claude_service: ClaudeService):
    response_with_fences = '```json\n{"summary":"test","strengths":["a"],"improvements":["b"],"next_workout_tip":"c","strava_comment":"d"}\n```'

    result = claude_service._parse_response(response_with_fences)
    assert result.summary == "test"


@pytest.mark.asyncio
async def test_parse_response_invalid_json(claude_service: ClaudeService):
    with pytest.raises(ClaudeAPIError, match="Invalid JSON"):
        claude_service._parse_response("not valid json at all")


@pytest.mark.asyncio
async def test_parse_response_missing_fields(claude_service: ClaudeService):
    with pytest.raises(ClaudeAPIError, match="Invalid coaching output"):
        claude_service._parse_response('{"summary": "only summary"}')
