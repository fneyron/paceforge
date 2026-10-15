# PaceForge

Entraînement d’endurance à partir des données de la montre et de Strava : Santé (récupération, charge,
nuits, depuis COROS, Garmin, Apple Santé ou Health Connect), activités, et plans de course sur trace GPX
avec temps de passage, ravitaillements et barrières horaires. Une application web (FastAPI, Jinja2, htmx,
SQLAlchemy async, Celery) servie sur https://paceforge.fr ; une application iPhone / Android (Expo) sous
`mobile/`. « Less is more ».

## Démarrer en dix minutes

### Avec Docker Compose

    cp .env.example .env      # SECRET_KEY, mails… ; les hôtes « db » et « redis » sont ceux de compose
    docker compose up --build  # l’app sur http://localhost:8000, PostgreSQL publié sur 5433, Redis sur 6380

Les migrations s’exécutent au démarrage du conteneur `app`.

### Avec un venv et PostgreSQL

Prérequis : Python 3.12, [uv](https://docs.astral.sh/uv/), PostgreSQL 16 et Redis (les vôtres, ou ceux de
`docker compose up db redis`).

    uv venv && uv pip install -r requirements-dev.lock   # les versions verrouillées, celles de la production
    cp .env.example .env    # DATABASE_URL vers votre PostgreSQL (…@localhost:5433/paceforge avec le db de compose),
                            # REDIS_URL et CELERY_BROKER_URL vers localhost:6380, SECRET_KEY
    .venv/bin/alembic upgrade head
    make dev                # uvicorn --reload sur http://localhost:8000

Les synchronisations en arrière-plan demandent un worker : `.venv/bin/celery -A app.celery_app worker --beat
-Q celery,long --loglevel=info`. Sans Redis, l’app démarre quand même (limites de débit en mémoire, pas de
tâches de fond).

## Tests, lint, dépendances

- `make test` : la suite sur SQLite (environ 2,5 min, 1 280 tests).
- `TEST_DATABASE_URL=postgresql+asyncpg://… make test-pg` : la même sur PostgreSQL, schéma construit par les
  migrations, comme en production et en CI.
- `make lint` : ruff sur `app/` et `tests/`.
- `make lock` : après toute modification de `pyproject.toml` — régénère `uv.lock` et les `requirements*.lock`
  (avec hashes) que le Dockerfile et la CI installent.

## Déploiement

Chaque push sur `main` passe par `.github/workflows/deploy.yml` : tests sur SQLite et PostgreSQL, répétition
de sauvegarde/restauration, puis webhook Coolify et attente que `https://paceforge.fr/health` serve
l’empreinte du commit. Sauvegardes, restauration, retour arrière, alertes, journaux :
[docs/ops-runbook.md](docs/ops-runbook.md).

## Repères

- `app/` : routers FastAPI, services (modèle de course, Santé, synchros), tâches Celery, templates, statiques ;
  `alembic/` : migrations (une seule tête) ; `tests/` ; `scripts/` : données de course, sauvegardes, assets
  web ; `docs/` : notes de conception et d’exploitation ; `mobile/` : l’application Expo.
- Chaînes du produit en français, code et commentaires en anglais.
