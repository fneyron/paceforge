#!/usr/bin/env bash
# The restore drill, run by CI on every push (deploy.yml, backup-drill) and by hand against any PostgreSQL:
# back $PGDATABASE up with backup.sh, restore the dump with restore.sh into a fresh database, and check that
# both hold the same tables, the same number of rows in each and the same Alembic revision.
#
#   PGHOST=127.0.0.1 PGPORT=55440 PGUSER=postgres PGDATABASE=pf_f1 scripts/backup/drill.sh
#
# The dump goes to $BACKUP_DIR (a temporary directory by default); the restored database, named after the
# source with `_restored`, is dropped at the end. Never run it against production.
set -euo pipefail

here=$(cd "$(dirname "$0")" && pwd)
source=${PGDATABASE:?PGDATABASE names the database to back up}
target=${DRILL_DATABASE:-${source}_restored}
export BACKUP_DIR=${BACKUP_DIR:-$(mktemp -d)}
unset BACKUP_S3_URL  # the drill proves the local path

"$here/backup.sh"
dump=$(find "$BACKUP_DIR/daily" -name '*.dump' | sort | tail -n 1)
dropdb --if-exists "$target"
createdb "$target"
PGDATABASE=$target "$here/restore.sh" "$dump"
# the rollback case: a schema that moved past the dump (a later migration's table, foreign key and column)
psql -v ON_ERROR_STOP=1 -q -d "$target" \
    -c "create table drill_extra (id integer primary key, user_id integer not null references users (id))" \
    -c "alter table users add column drill_extra_flag boolean"
PGDATABASE=$target "$here/restore.sh" "$dump"

tables="select table_name from information_schema.tables where table_schema = 'public' order by 1"
columns="select table_name, column_name, data_type from information_schema.columns where table_schema = 'public' order by 1, 2"
diff <(psql -Atd "$source" -c "$tables") <(psql -Atd "$target" -c "$tables")
diff <(psql -Atd "$source" -c "$columns") <(psql -Atd "$target" -c "$columns")
for table in $(psql -Atd "$source" -c "$tables"); do
    count="select count(*) from \"$table\""
    [ "$(psql -Atd "$source" -c "$count")" = "$(psql -Atd "$target" -c "$count")" ] || {
        echo "drill: $table has a different number of rows after the restore" >&2
        exit 1
    }
done
revision="select version_num from alembic_version"
[ "$(psql -Atd "$source" -c "$revision")" = "$(psql -Atd "$target" -c "$revision")" ] || {
    echo "drill: the Alembic revision differs after the restore" >&2
    exit 1
}
echo "drill: $(psql -Atd "$source" -c "$tables" | wc -l) tables, every column and row and the Alembic revision restored from $dump, over a schema that had moved on"
dropdb "$target"
