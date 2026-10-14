"""The database connection budget (app.config): the pools never ask PostgreSQL for more than it allows."""
import re
from pathlib import Path

from app.config import settings
from app.database import engine


def test_the_database_pools_fit_postgresql_connection_budget():
    """4 uvicorn workers and 2 Celery processes, doubled while a deploy overlaps the old and new containers,
    plus Alembic, stay under PostgreSQL's 100 connections (3 kept for superusers)."""
    root = Path(__file__).resolve().parent.parent
    workers = int(re.search(r"--workers (\d+)", (root / "Dockerfile").read_text())[1])
    tasks = int(re.search(r"--concurrency=(\d+)", (root / "docker-compose.prod.yml").read_text())[1])
    assert (engine.sync_engine.pool.size(), engine.sync_engine.pool._max_overflow) == (settings.DB_POOL_SIZE,
                                                                                        settings.DB_MAX_OVERFLOW)
    web = workers * (settings.DB_POOL_SIZE + settings.DB_MAX_OVERFLOW)
    celery = tasks * (settings.DB_TASK_POOL_SIZE + settings.DB_TASK_MAX_OVERFLOW)
    assert 2 * (web + celery) + 1 <= 97
