# Development shortcuts (README.md). The venv is .venv (uv venv && uv pip install -r requirements-dev.lock);
# `make dev` reads .env (copy .env.example).
PYTHON ?= .venv/bin/python
UV ?= uv

.PHONY: dev test test-pg lint lock

dev:  ## the app on http://localhost:8000, reloaded on every change
	$(PYTHON) -m uvicorn app.main:app --reload --port 8000

test:  ## the suite on SQLite (about 2.5 min)
	$(PYTHON) -m pytest -q -p no:cacheprovider

test-pg:  ## the suite on PostgreSQL, as in production: TEST_DATABASE_URL=postgresql+asyncpg://… make test-pg
	@test -n "$(TEST_DATABASE_URL)" || { echo "set TEST_DATABASE_URL (see .env.example)"; exit 1; }
	DATABASE_URL=$(TEST_DATABASE_URL) SECRET_KEY=x-test DEBUG=true $(PYTHON) -m alembic upgrade head
	TEST_DATABASE_URL=$(TEST_DATABASE_URL) $(PYTHON) -m pytest -q -p no:cacheprovider

lint:  ## ruff on the application and the tests
	$(PYTHON) -m ruff check app tests

lock:  ## resolve pyproject.toml into uv.lock and export the hashed requirements the Dockerfile and CI install (uv 0.8.17, as CI checks them)
	$(UV) lock
	$(UV) export --format requirements-txt --no-emit-project -o requirements.lock
	$(UV) export --format requirements-txt --no-emit-project --extra dev -o requirements-dev.lock
	$(UV) export --format requirements-txt --no-emit-project --extra research -o requirements-research.lock
