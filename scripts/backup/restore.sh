#!/usr/bin/env bash
# Restore a dump made by backup.sh into $PGDATABASE. The public schema is dropped and recreated first, so the
# restore also works when the database's schema has moved past the dump (a rollback: the tables, constraints
# or columns a later migration added would otherwise block the drops); pg_restore then fills it in a single
# transaction. It refuses to run while anything else is connected: stop the app and the workers first, and
# start them again with the code that matches the dump (docs/ops-runbook.md).
#
#   restore.sh /backups/daily/paceforge-2026-10-15.dump
#
# The connection comes from libpq's variables (PGHOST, PGPORT, PGUSER, PGPASSWORD, PGDATABASE).
set -euo pipefail

dump=${1:?usage: restore.sh <dump>}
export PGDATABASE=${PGDATABASE:-paceforge}
export PGOPTIONS="-c client_min_messages=warning"  # no notice per table the drop cascades to

[ -r "$dump" ] || { echo "restore: $dump is not readable" >&2; exit 1; }
list=$(mktemp)
trap 'rm -f "$list"' EXIT
# a readable archive before anything is dropped; its entry for the schema itself is left out (recreated below)
pg_restore --list "$dump" | grep -Ev '^[0-9]+; [0-9]+ [0-9]+ SCHEMA - public ' > "$list"
others=$(psql -At -c "select count(*) from pg_stat_activity where datname = current_database() and pid <> pg_backend_pid()")
if [ "$others" != 0 ]; then
    echo "restore: $others other connection(s) to $PGDATABASE: stop the app and the workers first" >&2
    exit 1
fi
echo "restore: $dump into $PGDATABASE on ${PGHOST:-the local socket}"
psql -v ON_ERROR_STOP=1 -q -c "DROP SCHEMA public CASCADE" -c "CREATE SCHEMA public"
pg_restore --dbname="$PGDATABASE" --no-owner --no-privileges --single-transaction --use-list="$list" "$dump"
tables=$(psql -At -c "select count(*) from information_schema.tables where table_schema = 'public'")
revision=$(psql -At -c "select version_num from alembic_version" 2>/dev/null || echo "none")
echo "restore: done, $tables tables, Alembic revision $revision"
