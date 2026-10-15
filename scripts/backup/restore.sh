#!/usr/bin/env bash
# Restore a dump made by backup.sh into $PGDATABASE: the tables are dropped and recreated from the archive in
# a single transaction (nothing half restored). Stop the app and the workers first; the exact commands are in
# docs/ops-runbook.md.
#
#   restore.sh /backups/daily/paceforge-2026-10-15.dump
#
# The connection comes from libpq's variables (PGHOST, PGPORT, PGUSER, PGPASSWORD, PGDATABASE).
set -euo pipefail

dump=${1:?usage: restore.sh <dump>}
export PGDATABASE=${PGDATABASE:-paceforge}

[ -r "$dump" ] || { echo "restore: $dump is not readable" >&2; exit 1; }
pg_restore --list "$dump" > /dev/null  # a readable archive before anything is dropped
echo "restore: $dump into $PGDATABASE on ${PGHOST:-the local socket}"
pg_restore --dbname="$PGDATABASE" --clean --if-exists --no-owner --no-privileges --single-transaction "$dump"
tables=$(psql -At -c "select count(*) from information_schema.tables where table_schema = 'public'")
revision=$(psql -At -c "select version_num from alembic_version" 2>/dev/null || echo "none")
echo "restore: done, $tables tables, Alembic revision $revision"
