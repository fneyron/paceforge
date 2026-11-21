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

    # Database
    DATABASE_URL: str = "postgresql+asyncpg://paceforge:paceforge@localhost:5432/paceforge"
    # Connection pools, against PostgreSQL's max_connections (150 in docker-compose.prod.yml, 3 of them kept
    # for superusers): web: 4 uvicorn workers × (DB_POOL_SIZE + DB_MAX_OVERFLOW) = 40, twice while a deploy
    # runs the old and the new container side by side = 80; Celery: two workers of 2 processes, one task each,
    # every task its own engine of at most DB_TASK_POOL_SIZE + DB_TASK_MAX_OVERFLOW = 3 → 12, twice in a
    # deploy = 24; Alembic at start: 1; the nightly pg_dump: 1. 106 at worst (tests/test_config.py). Raise
    # them only with max_connections (or behind PgBouncer).
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
    # Error reporting (Sentry) when set; nothing is loaded or sent while empty (app/sentry.py)
    SENTRY_DSN: str = ""

    # Official connectors (app/connectors, docs/official-connectors.md). Each one is active only when it is enabled
    # AND its client id and secret are set: until then its routes answer 404, nothing is scheduled, no page shows it
    # and nothing calls the provider. The URLs default to what public sources say; *_URLS overrides any single
    # endpoint, as a JSON object ({"token": "https://…"}), once the provider's own documentation is read.
    # COROS Partner API (support.coros.com « Partner API Access »)
    COROS_PARTNER_ENABLED: bool = False
    COROS_PARTNER_CLIENT_ID: str = ""
    COROS_PARTNER_CLIENT_SECRET: str = ""
    COROS_PARTNER_BASE_URL: str = "https://open.coros.com"
    COROS_PARTNER_URLS: str = ""
    # the hosts a workout's FIT file may be downloaded from (comma-separated; a host or any of its subdomains)
    COROS_PARTNER_FIT_HOSTS: str = "coros.com"
    # Garmin Connect Developer Program (OAuth 2.0 PKCE, push delivery)
    GARMIN_CONNECT_ENABLED: bool = False
    GARMIN_CONNECT_CLIENT_ID: str = ""
    GARMIN_CONNECT_CLIENT_SECRET: str = ""
    GARMIN_CONNECT_AUTHORIZE_URL: str = "https://connect.garmin.com/oauth2Confirm"
    GARMIN_CONNECT_TOKEN_URL: str = "https://diauth.garmin.com/di-oauth2-service/oauth/token"
    GARMIN_CONNECT_API_BASE_URL: str = "https://apis.garmin.com"
    GARMIN_CONNECT_URLS: str = ""
    # a push must carry the garmin-client-id header with this app's client id (as public integrations read it)
    GARMIN_CONNECT_REQUIRE_CLIENT_HEADER: bool = True
    # Wahoo Cloud API (cloud-api.wahooligan.com): read-only, activities only
    WAHOO_CLOUD_ENABLED: bool = False
    WAHOO_CLOUD_CLIENT_ID: str = ""
    WAHOO_CLOUD_CLIENT_SECRET: str = ""
    # the token set on the app's webhook in Wahoo's developer portal: every push carries it (webhook_token)
    WAHOO_CLOUD_WEBHOOK_TOKEN: str = ""
    WAHOO_CLOUD_BASE_URL: str = "https://api.wahooligan.com"
    WAHOO_CLOUD_URLS: str = ""
    # the hosts a workout's FIT file may be downloaded from (comma-separated; a host or any of its subdomains)
    WAHOO_CLOUD_FIT_HOSTS: str = "wahooligan.com"
    # Polar AccessLink v3 (www.polar.com/accesslink-api): read-only, exercises only
    POLAR_ACCESSLINK_ENABLED: bool = False
    POLAR_ACCESSLINK_CLIENT_ID: str = ""
    POLAR_ACCESSLINK_CLIENT_SECRET: str = ""
    # the signature_secret_key Polar returns when the client's webhook is created (python -m
    # app.connectors.polar_accesslink create-webhook): every push is signed with it (Polar-Webhook-Signature)
    POLAR_ACCESSLINK_WEBHOOK_SECRET: str = ""
    POLAR_ACCESSLINK_AUTHORIZE_URL: str = "https://flow.polar.com/oauth2/authorization"
    POLAR_ACCESSLINK_TOKEN_URL: str = "https://polarremote.com/v2/oauth2/token"
    POLAR_ACCESSLINK_API_BASE_URL: str = "https://www.polaraccesslink.com"
    POLAR_ACCESSLINK_URLS: str = ""
    # while set (PaceForge account ids, comma-separated), only those accounts see and can start the official
    # connections: a first try in production on the owner's own account. Empty: every account.
    OFFICIAL_CONNECTORS_PILOT_USERS: str = ""

    # Coaching (beta, docs/coaching.md), open to every account by default: « * » every signed-in account; emails
    # (comma-separated, case-insensitive) only those accounts; empty or « off » the feature does not exist (every
    # route answers 404, no menu entry, no card, no line in the privacy policy, no scheduled task).
    COACHING_BETA_USERS: str = "*"

    @property
    def strava_shared_ready(self) -> bool:
        return bool(self.STRAVA_SHARED_APP_ENABLED and self.STRAVA_CLIENT_ID and self.STRAVA_CLIENT_SECRET)

    @property
    def coros_partner_ready(self) -> bool:
        return bool(self.COROS_PARTNER_ENABLED and self.COROS_PARTNER_CLIENT_ID.strip()
                    and self.COROS_PARTNER_CLIENT_SECRET.strip())

    @property
    def garmin_connect_ready(self) -> bool:
        return bool(self.GARMIN_CONNECT_ENABLED and self.GARMIN_CONNECT_CLIENT_ID.strip()
                    and self.GARMIN_CONNECT_CLIENT_SECRET.strip())

    @property
    def wahoo_cloud_ready(self) -> bool:
        return bool(self.WAHOO_CLOUD_ENABLED and self.WAHOO_CLOUD_CLIENT_ID.strip()
                    and self.WAHOO_CLOUD_CLIENT_SECRET.strip())

    @property
    def polar_accesslink_ready(self) -> bool:
        return bool(self.POLAR_ACCESSLINK_ENABLED and self.POLAR_ACCESSLINK_CLIENT_ID.strip()
                    and self.POLAR_ACCESSLINK_CLIENT_SECRET.strip())

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


settings = Settings()


# Google Search Console's ownership tag for paceforge.fr (public by design: printed in the public pages' head)
GOOGLE_SITE_VERIFICATION = "2TUENyE5jUvbgFdkAC953TfiC41LSPmK23l1XrudDFE"
