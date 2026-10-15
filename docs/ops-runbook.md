# Exploitation de PaceForge

Un serveur, Coolify, la pile de `docker-compose.prod.yml` : `app` (uvicorn, 4 workers), `celery-worker` (les
synchros longues et beat, le planificateur), `celery-worker-default` (les sondages Strava, les événements du
webhook, la purge horaire), `db` (PostgreSQL 16, volume `pgdata`), `redis` (volume `redisdata`) et
`db-backup` (les sauvegardes, volume `pgbackups`). Chaque conteneur a une limite mémoire et des journaux
tournants (5 × 10 Mo). Les variables viennent de l’environnement de Coolify ; `.env.example` les décrit
toutes.

## Déployer

1. Un push sur `main` lance `.github/workflows/deploy.yml` : les tests sur SQLite et sur PostgreSQL (schéma
   construit par les migrations), la répétition sauvegarde/restauration, puis le job `deploy` appelle le
   webhook Coolify et attend que `https://paceforge.fr/health` serve l’empreinte du commit (`build`, calculée
   par `app/build.py` sur `app/`). Rouge après 10 min : le build Coolify a échoué ou l’app redémarre en
   boucle — ouvrir le journal de déploiement dans Coolify.
2. Coolify reconstruit les images (les dépendances viennent de `requirements.lock`, une couche gardée en cache
   tant que le verrou ne change pas) et remplace les conteneurs. `app` exécute `alembic upgrade head` au
   démarrage, puis uvicorn ; l’ancien conteneur reçoit SIGTERM et finit ses requêtes (20 s) ; un worker a
   12 min pour finir la synchro en cours.
3. Une pull request lance les mêmes tests sans déployer.

Pas d’environnement de préproduction : les tests et la vérification de `/health` sont le filet.

## Revenir en arrière

- Le code : `git revert <sha> && git push` (3 à 4 min, tests compris) ; ou, dans Coolify, « Deploy » sur un
  commit précédent (sans les tests).
- La base : **les migrations ne reviennent jamais en arrière en production**. Une migration est additive
  (ajouter la colonne, la remplir, et seulement un déploiement plus tard retirer l’ancienne) ; un défaut se
  corrige par une nouvelle migration poussée sur `main`. Si une migration casse le démarrage, `app` redémarre
  en boucle et le site est indisponible : corriger et pousser, ou, si des données ont été altérées, remettre
  le code précédent puis restaurer la dernière sauvegarde (ci-dessous). Ne jamais lancer `alembic downgrade`
  sur la base de production.

## Sauvegardes

`db-backup` fait chaque jour à 03:10 UTC un `pg_dump -Fc` de la base dans le volume `pgbackups`
(`/backups/daily/paceforge-AAAA-MM-JJ.dump`), vérifié avec `pg_restore --list` ; les 14 quotidiennes et les
8 hebdomadaires (celle du dimanche, dans `/backups/weekly/`) les plus récentes sont gardées. Une sauvegarde
est aussi faite au démarrage du conteneur quand celle du jour manque. Le conteneur passe « unhealthy » quand
aucune sauvegarde n’a moins de 26 h : les notifications Coolify le signalent.

Lister, ou lancer à la main :

    docker compose -f docker-compose.prod.yml exec db-backup ls -l /backups/daily /backups/weekly
    docker compose -f docker-compose.prod.yml exec db-backup backup.sh

Sur le serveur Coolify, remplacer `docker compose -f docker-compose.prod.yml` par
`docker compose -p <nom du projet Coolify>` (`docker compose ls` le donne), ou utiliser
`docker exec <conteneur db-backup> …` (`docker ps` donne son nom).

### Copie hors du serveur

Le volume est sur le même disque que la base : il protège d’une erreur, pas de la perte du serveur. Avec
`BACKUP_S3_URL` (par exemple `s3://mon-bucket/paceforge`), `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` et,
pour un stockage compatible S3 (OVH, Scaleway, Hetzner…), `AWS_ENDPOINT_URL` et `AWS_DEFAULT_REGION`, chaque
sauvegarde quotidienne est copiée dans le bucket après sa vérification. La rotation dans le bucket est celle
de ses règles de cycle de vie (en garder 30 jours, par exemple). Dans Coolify, *Settings → S3 Storages* sert
aux sauvegardes des bases que Coolify gère lui-même ; celle de PaceForge étant un service du compose, ce
sont ces variables, dans l’environnement de l’application, qui font la copie. Une copie en échec se voit
dans les journaux de `db-backup` (« FAILED: the copy »).

### Restaurer

1. Arrêter ce qui écrit dans la base :

        docker compose -f docker-compose.prod.yml stop app celery-worker celery-worker-default

2. Restaurer — les tables sont supprimées et recréées depuis l’archive, en une transaction :

        docker compose -f docker-compose.prod.yml exec db-backup restore.sh /backups/daily/paceforge-2026-10-15.dump

   Une copie hors site se rapatrie d’abord dans le volume :

        docker compose -f docker-compose.prod.yml exec db-backup aws s3 cp s3://mon-bucket/paceforge/daily/paceforge-2026-10-15.dump /backups/daily/

3. Redémarrer :

        docker compose -f docker-compose.prod.yml start app celery-worker celery-worker-default

   `app` rejoue au démarrage les migrations que la sauvegarde précède.

### La répétition

`scripts/backup/drill.sh` sauvegarde une base, la restaure dans une base neuve et compare les tables, le
nombre de lignes de chacune et la révision Alembic. La CI l’exécute à chaque run (job *Backup and restore
drill*) ; à la main, contre un PostgreSQL local dont le schéma a été construit par `alembic upgrade head` :

    PGHOST=127.0.0.1 PGPORT=5432 PGUSER=postgres PGDATABASE=paceforge_test scripts/backup/drill.sh

## Alertes

- **Sentry** (les erreurs de l’app et des workers) : créer un projet, copier son DSN dans la variable
  `SENTRY_DSN` de Coolify, redéployer. Sans DSN rien n’est chargé. Pas de traces de performance ni de données
  personnelles par défaut ; `release` est l’empreinte du build, celle de `/health`.
- **Disponibilité** : un moniteur externe (Better Stack, UptimeRobot, Uptime Kuma…) sur
  `https://paceforge.fr/health?deep=1` toutes les 1 à 5 min, attendu : HTTP 200 et `"status":"ok"`. Cette
  route interroge la base (`SELECT 1`) et Redis (`PING`) avec 2 s de délai et répond 503 avec le drapeau en
  défaut (`"db": false` ou `"redis": false`) ; un 503 sous charge signale un pool de connexions saturé.
  `/health` sans paramètre reste l’épreuve rapide des conteneurs et du proxy.
- **Coolify** : *Settings → Notifications* (e-mail, Telegram, Discord…) — les déploiements échoués et les
  conteneurs arrêtés ou unhealthy (ce qui couvre `db-backup`).
- **GitHub** : les e-mails d’échec des Actions (`deploy.yml`, `race-data.yml`) signalent un déploiement non
  vérifié ou une collecte en défaut.

## Lire les journaux

Coolify → l’application → *Logs*, par conteneur ; ou sur le serveur :

    docker compose -f docker-compose.prod.yml logs -f --tail 200 celery-worker celery-worker-default

Repères : `Strava round:`, `COROS sync round:`, `Garmin sync round:` (chaque ronde), `… round failed` (une
ronde en échec, remontée aussi à Sentry), `Strava quota of app` (les synchros attendent la prochaine
fenêtre), `Garmin sync budget reached`, `SoftTimeLimitExceeded` (une tâche a dépassé 55 min : la ronde
reprend à l’heure suivante), `backup: FAILED` dans `db-backup`.

## Dépendances

`pyproject.toml` donne les contraintes, `uv.lock` la résolution, `requirements.lock`, `requirements-dev.lock`
et `requirements-research.lock` (avec hashes) ce que le Dockerfile et la CI installent. Après toute
modification de `pyproject.toml` ou une pull request Dependabot (hebdomadaire : pip, npm de `mobile/`,
GitHub Actions) : `make lock`, puis commit des quatre fichiers.

## Variables à renseigner dans Coolify

Obligatoires : `POSTGRES_PASSWORD` et `STRAVA_WEBHOOK_VERIFY_TOKEN` (aléatoires ; le compose refuse de
démarrer sans), `SECRET_KEY`, `BASE_URL`, `SMTP_USER`, `SMTP_PASSWORD`. Recommandées : `ENCRYPTION_KEYS`,
`SENTRY_DSN`, `BACKUP_S3_URL` et ses clés. Tout le reste a la valeur par défaut de `app/config.py`.
