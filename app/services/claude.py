import json
import logging

import anthropic

from app.config import settings
from app.exceptions import ClaudeAPIError
from app.prompts.coach_chat import COACH_CHAT_SYSTEM_PROMPT, build_coach_context
from app.prompts.coaching import COACHING_SYSTEM_PROMPT, build_activity_prompt
from app.prompts.race_strategy import RACE_STRATEGY_SYSTEM_PROMPT, build_race_strategy_prompt
from app.prompts.weekly_digest import WEEKLY_DIGEST_SYSTEM_PROMPT, build_weekly_digest_prompt
from app.prompts.workout import WORKOUT_SYSTEM_PROMPT, build_workout_prompt
from app.schemas.analysis import ClaudeCoachingOutput
from app.schemas.simulator import ClaudeRaceStrategyOutput
from app.schemas.weekly_digest import ClaudeWeeklyDigestOutput
from app.schemas.workout import ClaudeWorkoutOutput

logger = logging.getLogger(__name__)

MAX_RETRIES = 3
RETRY_DELAYS = [2, 5, 10]


class ClaudeService:
    def __init__(self) -> None:
        self.client = anthropic.AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
        self.model = settings.CLAUDE_MODEL

    async def analyze_activity(
        self,
        activity_data: dict,
        training_load: dict,
        recent_activities: list[dict],
    ) -> tuple[ClaudeCoachingOutput, str]:
        """
        Analyze an activity using Claude.

        Returns:
            Tuple of (parsed coaching output, raw response text)
        """
        user_message = build_activity_prompt(
            activity_data=activity_data,
            training_load=training_load,
            recent_activities=recent_activities,
        )

        logger.info(
            "Calling Claude (%s) for activity analysis (%d chars prompt)",
            self.model,
            len(user_message),
        )

        raw_response = await self._call_claude(user_message)
        coaching_output = self._parse_response(raw_response)

        return coaching_output, raw_response

    async def generate_workout(
        self,
        sport: str,
        goal: str | None,
        training_load: dict,
        recent_activities: list[dict],
    ) -> ClaudeWorkoutOutput:
        """Generate a workout session using Claude."""
        user_message = build_workout_prompt(
            sport=sport,
            goal=goal,
            training_load=training_load,
            recent_activities=recent_activities,
        )

        logger.info(
            "Calling Claude (%s) for workout generation (%d chars prompt)",
            self.model,
            len(user_message),
        )

        raw_response = await self._call_claude(
            user_message, system_prompt=WORKOUT_SYSTEM_PROMPT
        )
        return self._parse_workout_response(raw_response)

    async def generate_weekly_digest(
        self,
        week_activities: list[dict],
        this_week_load: dict,
        prev_week_load: dict,
        avg_4w_load: dict,
        race_goal: dict | None = None,
    ) -> tuple[ClaudeWeeklyDigestOutput, str]:
        """Generate a weekly training digest using Claude."""
        user_message = build_weekly_digest_prompt(
            week_activities=week_activities,
            this_week_load=this_week_load,
            prev_week_load=prev_week_load,
            avg_4w_load=avg_4w_load,
            race_goal=race_goal,
        )

        logger.info(
            "Calling Claude (%s) for weekly digest (%d chars prompt)",
            self.model,
            len(user_message),
        )

        raw_response = await self._call_claude(
            user_message, system_prompt=WEEKLY_DIGEST_SYSTEM_PROMPT
        )
        output = self._parse_digest_response(raw_response)
        return output, raw_response

    async def chat(
        self,
        messages: list[dict],
        athlete_context: str,
    ) -> str:
        """Chat with the coach. Returns the assistant's reply text."""
        system = COACH_CHAT_SYSTEM_PROMPT + "\n\n" + athlete_context

        last_error = None
        for attempt in range(MAX_RETRIES):
            try:
                response = await self.client.messages.create(
                    model=self.model,
                    max_tokens=1024,
                    temperature=0.7,
                    system=system,
                    messages=messages,
                )
                if not response.content:
                    raise ClaudeAPIError("Empty response from Claude")

                text = response.content[0].text
                logger.info(
                    "Coach chat response: %d chars, %d input tokens, %d output tokens",
                    len(text),
                    response.usage.input_tokens,
                    response.usage.output_tokens,
                )
                return text

            except anthropic.RateLimitError as e:
                last_error = e
                if attempt < MAX_RETRIES - 1:
                    import asyncio
                    await asyncio.sleep(RETRY_DELAYS[attempt])
            except anthropic.APIError as e:
                last_error = e
                if attempt < MAX_RETRIES - 1 and e.status_code and e.status_code >= 500:
                    import asyncio
                    await asyncio.sleep(RETRY_DELAYS[attempt])
                else:
                    raise ClaudeAPIError(f"Claude API error: {e}") from e

        raise ClaudeAPIError(f"Claude API failed after {MAX_RETRIES} attempts: {last_error}")

    async def generate_race_strategy(
        self,
        course_data: dict,
        athlete_flat_pace: float,
        data_points: int,
        training_load: dict | None = None,
        race_name: str | None = None,
    ) -> ClaudeRaceStrategyOutput:
        """Generate a race strategy using Claude."""
        user_message = build_race_strategy_prompt(
            course_data=course_data,
            athlete_flat_pace=athlete_flat_pace,
            data_points=data_points,
            training_load=training_load,
            race_name=race_name,
        )

        logger.info(
            "Calling Claude (%s) for race strategy (%d chars prompt)",
            self.model,
            len(user_message),
        )

        raw_response = await self._call_claude(
            user_message,
            system_prompt=RACE_STRATEGY_SYSTEM_PROMPT,
            max_tokens=2048,
        )
        return self._parse_race_strategy_response(raw_response)

    async def _call_claude(self, user_message: str, system_prompt: str | None = None, max_tokens: int = 1024) -> str:
        last_error = None

        for attempt in range(MAX_RETRIES):
            try:
                response = await self.client.messages.create(
                    model=self.model,
                    max_tokens=max_tokens,
                    temperature=0.7,
                    system=system_prompt or COACHING_SYSTEM_PROMPT,
                    messages=[
                        {"role": "user", "content": user_message},
                    ],
                )

                if not response.content:
                    raise ClaudeAPIError("Empty response from Claude")

                text = response.content[0].text
                logger.info(
                    "Claude response received: %d chars, %d input tokens, %d output tokens",
                    len(text),
                    response.usage.input_tokens,
                    response.usage.output_tokens,
                )
                return text

            except anthropic.RateLimitError as e:
                last_error = e
                if attempt < MAX_RETRIES - 1:
                    import asyncio
                    delay = RETRY_DELAYS[attempt]
                    logger.warning(
                        "Claude rate limited, retrying in %ds (attempt %d/%d)",
                        delay,
                        attempt + 1,
                        MAX_RETRIES,
                    )
                    await asyncio.sleep(delay)

            except anthropic.APIError as e:
                last_error = e
                if attempt < MAX_RETRIES - 1 and e.status_code and e.status_code >= 500:
                    import asyncio
                    delay = RETRY_DELAYS[attempt]
                    logger.warning(
                        "Claude API error %s, retrying in %ds (attempt %d/%d)",
                        e.status_code,
                        delay,
                        attempt + 1,
                        MAX_RETRIES,
                    )
                    await asyncio.sleep(delay)
                else:
                    raise ClaudeAPIError(f"Claude API error: {e}") from e

        raise ClaudeAPIError(f"Claude API failed after {MAX_RETRIES} attempts: {last_error}")

    def _parse_workout_response(self, raw_response: str) -> ClaudeWorkoutOutput:
        """Parse Claude's JSON response into a workout output."""
        text = self._strip_code_fences(raw_response)
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            logger.error("Failed to parse workout response as JSON: %s", raw_response[:200])
            raise ClaudeAPIError(f"Invalid JSON from Claude: {e}") from e
        try:
            return ClaudeWorkoutOutput(**data)
        except Exception as e:
            logger.error("Failed to validate workout response: %s", data)
            raise ClaudeAPIError(f"Invalid workout output: {e}") from e

    def _parse_digest_response(self, raw_response: str) -> ClaudeWeeklyDigestOutput:
        """Parse Claude's JSON response into a weekly digest output."""
        text = self._strip_code_fences(raw_response)
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            logger.error("Failed to parse digest response as JSON: %s", raw_response[:200])
            raise ClaudeAPIError(f"Invalid JSON from Claude: {e}") from e
        try:
            return ClaudeWeeklyDigestOutput(**data)
        except Exception as e:
            logger.error("Failed to validate digest response: %s", data)
            raise ClaudeAPIError(f"Invalid digest output: {e}") from e

    def _strip_code_fences(self, text: str) -> str:
        """Strip markdown code fences from response."""
        text = text.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            lines = [l for l in lines if not l.strip().startswith("```")]
            text = "\n".join(lines)
        return text

    def _parse_race_strategy_response(self, raw_response: str) -> ClaudeRaceStrategyOutput:
        """Parse Claude's JSON response into a race strategy output."""
        text = self._strip_code_fences(raw_response)
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            logger.error("Failed to parse race strategy response as JSON: %s", raw_response[:200])
            raise ClaudeAPIError(f"Invalid JSON from Claude: {e}") from e
        try:
            return ClaudeRaceStrategyOutput(**data)
        except Exception as e:
            logger.error("Failed to validate race strategy response: %s", data)
            raise ClaudeAPIError(f"Invalid race strategy output: {e}") from e

    def _parse_response(self, raw_response: str) -> ClaudeCoachingOutput:
        """Parse Claude's JSON response into structured output."""
        text = self._strip_code_fences(raw_response)

        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            logger.error("Failed to parse Claude response as JSON: %s", raw_response[:200])
            raise ClaudeAPIError(f"Invalid JSON from Claude: {e}") from e

        try:
            return ClaudeCoachingOutput(**data)
        except Exception as e:
            logger.error("Failed to validate Claude response: %s", data)
            raise ClaudeAPIError(f"Invalid coaching output: {e}") from e
