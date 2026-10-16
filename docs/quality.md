# Qualité du code : ruff, mypy et la suite de tests

Trois gardes, les mêmes en local et dans [quality.yml](../.github/workflows/quality.yml) : ruff, mypy tenu
à « aucune nouvelle erreur », et une suite de tests qui donne le même résultat quelle que soit l’heure, la
date ou la charge de la machine.

## ruff

La configuration est dans `pyproject.toml` (`[tool.ruff]`) : 120 colonnes (le code est écrit ainsi), les
familles E, F, W, I, N, UP, B, A, SIM, TCH ; `B008` est ignorée (`Depends()` en valeur par défaut est l’idiome de
FastAPI), E501 reste active.

```sh
.venv/bin/ruff check .                           # en local
ruff check --output-format github .              # en CI : chaque constat annote le commit ou la PR
```

État au 2026-10-15 : 1 602 constats, dont 710 lignes trop longues et 42 de la famille F (imports inutilisés,
redéfinitions, variables mortes) ; 589 se corrigent avec `ruff check --fix`. Le balayage prévu après la fusion
des branches les ramène à zéro ; jusque-là le job ruff est rouge, mais il ne bloque rien (voir CI).

## mypy

`[tool.mypy]` est volontairement minimal et non strict : Python 3.12, le plugin pydantic, `files = ["app"]`,
`ignore_missing_imports`. Le code n’est pas typé partout ; plutôt que de corriger 315 erreurs d’un coup, on
tient la ligne :

- `mypy-baseline.txt` contient les constats du 2026-10-15 (315, dans 45 fichiers), une ligne par constat sans
  numéro de ligne (le code bouge) : `chemin: error: message  [code]`.
- `python scripts/mypy_baseline.py check` relance mypy et échoue sur tout constat absent de la baseline (il
  l’affiche avec sa ligne, et l’annote dans Actions). C’est le job de CI.
- `python scripts/mypy_baseline.py update` réécrit la baseline avec les constats du jour : après avoir corrigé
  des erreurs (le fichier raccourcit), ou pour accepter en connaissance de cause un constat nouveau (il
  s’allonge : dire pourquoi dans le commit). Relire le diff de `mypy-baseline.txt` avant de le committer.

mypy prend ≈ 30 s sur `app/` ; `check` signale aussi les constats de la baseline qui ont disparu, pour la
resserrer.

## CI

`quality.yml` tourne à chaque push sur `main` et sur chaque PR : ruff puis mypy (mypy tourne même si ruff a
échoué, pour avoir les deux rapports). Le déploiement (`deploy.yml`) ne l’attend pas encore : pour qu’un
constat bloque un déploiement, une fois `main` propre, déplacer le job dans `deploy.yml` et l’ajouter au
`needs` du job `deploy`, ou exiger le check « ruff and mypy » dans un ruleset de `main`.

## Makefile

Les cibles `typecheck` et `typecheck-baseline` sont dans [Makefile.quality](Makefile.quality), à coller dans le
Makefile (`lint`, `test` et `test-pg` y sont déjà).

## La suite de tests

```sh
.venv/bin/python -m pytest -q                                            # SQLite, ≈ 2 min
DATABASE_URL=$PG SECRET_KEY=x-test DEBUG=true .venv/bin/alembic upgrade head   # une fois : le schéma des migrations
TEST_DATABASE_URL=$PG .venv/bin/python -m pytest -q                      # PostgreSQL, comme en production
```

Ce qui la rend déterministe (`tests/conftest.py`) :

- **Une base par test, sur SQLite.** Le schéma vide est construit une fois par processus dans le répertoire
  temporaire de pytest, puis copié pour chaque test (≈ 1 ms ; le reconstruire coûtait ≈ 50 ms par test, une
  minute sur la suite) et supprimé après lui. Rien n’est écrit dans le dépôt (`./test.db` était partagé : deux
  suites lancées en parallèle se supprimaient les tables), plusieurs suites peuvent tourner en même temps, et
  rien ne survit à un test : ni un commit, ni une connexion cassée, ni une table supprimée.
- **Une transaction par test, sur PostgreSQL.** Le schéma est celui des migrations (`TEST_DATABASE_URL`, plus
  jamais `DATABASE_URL` : la suite refuse de démarrer sur la base de l’application) ; ses tables sont vidées au
  début de la suite, et chaque test ouvre ses propres connexions. `db_session` est liée à une transaction
  externe, annulée en fin de test : un `commit()`, celui d’une route comme celui d’un test, ne libère qu’un
  point de sauvegarde, et les lignes ne sortent pas du test (une table réellement supprimée ou une ligne écrite
  par un autre moteur sortiraient, elles). Une seule suite à la fois par base : deux suites sur la même base se
  videraient l’une l’autre. Un schéma par test y coûterait 150 ms (trois minutes sur la suite) et testerait le
  schéma des modèles, non celui des migrations : écarté.
- **bcrypt à 4 tours** sous pytest (fixture `_cheap_password_hashes`), 12 en production : les tests
  d’authentification passaient 33 s à hacher.
- **Pas d’horloge murale.** Santé lit le jour de l’athlète par `sante_training.athlete_today` : un test qui
  sème des données « d’aujourd’hui » prend la fixture `same_today` (Paris est déjà demain à partir de 22 h UTC).
  Un test lié à une date (les lois de course lisent l’âge des courses) fige l’horloge avec la fixture
  `time_machine` (dépendance de développement `time-machine`). Pour vérifier : `faketime '2026-12-28 10:00:00'
  pytest …` ou `libfaketime` en `LD_PRELOAD`.
- **Des comptes de requêtes, pas des chronos.** `test_sante_performance` compte les requêtes de la page Santé
  et vérifie que la lecture des lignes du jour ne ramène pas leurs relevés bruts (`details`) ; un budget en
  secondes échouait sous coverage ou sur une machine chargée.
