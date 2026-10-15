from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Strava
    # Enable only after Strava grants this app the required athlete capacity.
    STRAVA_SHARED_APP_ENABLED: bool = False
    STRAVA_CLIENT_ID: str = ""
    STRAVA_CLIENT_SECRET: str = ""
    STRAVA_WEBHOOK_VERIFY_TOKEN: str = "paceforge-verify"
    # the shared app's webhook subscription id; optional, discovered when empty
    STRAVA_SUBSCRIPTION_ID: str = ""

    # Anthropic
    ANTHROPIC_API_KEY: str = ""
    CLAUDE_MODEL: str = "claude-sonnet-4-6"

    # Database
    DATABASE_URL: str = "postgresql+asyncpg://paceforge:paceforge@localhost:5432/paceforge"
    # Connection pools, against PostgreSQL's max_connections (100 by default, 3 of them kept for superusers):
    # web: 4 uvicorn workers × (DB_POOL_SIZE + DB_MAX_OVERFLOW) = 40, twice while a deploy runs the old and
    # the new container side by side = 80; Celery: 2 processes, one task each, every task its own engine of
    # at most DB_TASK_POOL_SIZE + DB_TASK_MAX_OVERFLOW = 3 → 6, twice in a deploy = 12; Alembic at start: 1.
    # 93 at worst. Raise them only with max_connections (or behind PgBouncer).
    DB_POOL_SIZE: int = 5
    DB_MAX_OVERFLOW: int = 5
    DB_TASK_POOL_SIZE: int = 1
    DB_TASK_MAX_OVERFLOW: int = 2

    # Redis
    REDIS_URL: str = "redis://localhost:6379/0"
    CELERY_BROKER_URL: str = "redis://localhost:6379/1"

    # SMTP
    SMTP_HOST: str = "ssl0.ovh.net"
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_FROM: str = "PaceForge <contact@paceforge.fr>"

    # App
    BASE_URL: str = "http://localhost:8000"
    # signs sessions and email links; the app refuses to start with an empty or default one unless DEBUG
    SECRET_KEY: str = "change-me-to-a-random-secret-key"
    # Fernet keys for the secrets at rest (provider tokens), comma-separated, newest first: the first encrypts,
    # all decrypt, and so does the key derived from SECRET_KEY used before them (app.crypto)
    ENCRYPTION_KEYS: str = ""
    APP_NAME: str = "PaceForge"
    # a browser session ends this long after the sign-in, however often it is used (app.services.sessions)
    SESSION_LIFETIME_DAYS: int = 90
    DEBUG: bool = False
    # Fetch each trace's road/track/trail surface from OpenStreetMap (off in tests)
    SURFACE_FETCH: bool = True
    # COROS: whether syncs may run in the background (off in tests)
    COROS_SYNC: bool = True
    # Garmin: the same for Garmin Connect
    GARMIN_SYNC: bool = True
    # Each outdoor activity's ground altitude and its start's weather from Open-Meteo, in the syncs (off in tests)
    ACTIVITY_ENV_FETCH: bool = True
    # Bike and triathlon planning: hidden for now (code kept, routes and data untouched)
    CYCLING_ENABLED: bool = False

    @property
    def strava_shared_ready(self) -> bool:
        return bool(self.STRAVA_SHARED_APP_ENABLED and self.STRAVA_CLIENT_ID and self.STRAVA_CLIENT_SECRET)

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


settings = Settings()
