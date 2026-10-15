FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# curl serves the healthcheck; the service runs as `app`, never as root
RUN apt-get update && \
    apt-get install -y --no-install-recommends curl && \
    rm -rf /var/lib/apt/lists/* && \
    useradd --create-home --uid 1000 --user-group app

# The locked dependencies first: this layer is rebuilt when the lock changes (make lock), not on every code
# change. Hashes are enforced: a build installs exactly what the lock names, or fails.
COPY requirements.lock ./
RUN pip install --require-hashes -r requirements.lock

# The rest of what app/build.py fingerprints: the migrations, and the files that shape this image, so the
# container reports the same build id as the tree it was built from (the deploy's smoke test compares them).
COPY alembic.ini Dockerfile docker-compose.prod.yml pyproject.toml requirements-dev.lock requirements-research.lock ./
COPY alembic/ ./alembic/
COPY app/ ./app/

USER app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# The migrations first, then uvicorn replaces the shell (exec) as PID 1: Docker's SIGTERM reaches it and the
# requests in flight get 20 s to finish instead of being dropped at every deploy. Keep-alive 75 s: longer than
# the proxy's idle timeout, so Traefik never reuses a connection the app has just closed (sporadic 502s).
# Port 8000 is only exposed inside the compose network: the proxy headers can be trusted from any address.
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 4 --proxy-headers --forwarded-allow-ips='*' --timeout-graceful-shutdown 20 --timeout-keep-alive 75"]
