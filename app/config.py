from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Strava
    STRAVA_CLIENT_ID: str = ""
    STRAVA_CLIENT_SECRET: str = ""
    STRAVA_WEBHOOK_VERIFY_TOKEN: str = "paceforge-verify"

    # Anthropic
    ANTHROPIC_API_KEY: str = ""
    CLAUDE_MODEL: str = "claude-sonnet-4-20250514"

    # Database
    DATABASE_URL: str = "postgresql+asyncpg://paceforge:paceforge@localhost:5432/paceforge"

    # Redis
    REDIS_URL: str = "redis://localhost:6379/0"
    CELERY_BROKER_URL: str = "redis://localhost:6379/1"

    # App
    BASE_URL: str = "http://localhost:8000"
    SECRET_KEY: str = "change-me-to-a-random-secret-key"
    APP_NAME: str = "PaceForge"
    DEBUG: bool = False

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


settings = Settings()
