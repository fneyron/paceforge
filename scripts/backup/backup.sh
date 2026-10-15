#!/usr/bin/env bash
# PaceForge database backups (docs/ops-runbook.md).
#
#   backup.sh         one backup now: `pg_dump -Fc` of $PGDATABASE into $BACKUP_DIR/daily/, verified with
#                     `pg_restore --list`; the newest $BACKUP_KEEP_DAILY (14) are kept. On Sundays the dump is
#                     also kept in $BACKUP_DIR/weekly/ (the newest $BACKUP_KEEP_WEEKLY, 8). Each dump is copied
#                     to $BACKUP_S3_URL when it is set (AWS CLI: AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, and
#                     AWS_ENDPOINT_URL for an S3-compatible store); the bucket's own lifecycle rules prune it.
#   backup.sh loop    the sidecar: a backup at start when today's is missing, then daily at $BACKUP_AT (UTC).
#   backup.sh check   healthy (0) when a dump younger than 26 h exists: the sidecar's healthcheck.
#
# The connection comes from libpq's variables (PGHOST, PGPORT, PGUSER, PGPASSWORD, PGDATABASE); nothing here
# prints a password. Restore: restore.sh. Drill (backup, restore, compare): drill.sh.
set -euo pipefail

BACKUP_DIR=${BACKUP_DIR:-/backups}
BACKUP_AT=${BACKUP_AT:-03:10}
BACKUP_KEEP_DAILY=${BACKUP_KEEP_DAILY:-14}
BACKUP_KEEP_WEEKLY=${BACKUP_KEEP_WEEKLY:-8}
export PGDATABASE=${PGDATABASE:-paceforge}

log() { printf '%s backup: %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"; }

prune() {  # keep the newest $2 dumps of $1 (their names sort by date)
    local old
    find "$1" -maxdepth 1 -name '*.dump' | sort -r | tail -n +"$(($2 + 1))" | while IFS= read -r old; do
        rm -f -- "$old" && log "pruned $old"
    done
}

offsite() {  # copy $1 to the bucket, when one is configured
    [ -n "${BACKUP_S3_URL:-}" ] || return 0
    local v target
    for v in AWS_ENDPOINT_URL AWS_DEFAULT_REGION; do  # empty (compose's default) means unset to the CLI
        [ -n "${!v:-}" ] || unset "$v"
    done
    export AWS_DEFAULT_REGION=${AWS_DEFAULT_REGION:-us-east-1}  # the CLI insists on one; S3-compatible stores ignore it
    target="${BACKUP_S3_URL%/}/daily/$(basename "$1")"
    if aws s3 cp --only-show-errors "$1" "$target"; then
        log "copied to $target"
    else
        log "FAILED: the copy to $target"
        return 1
    fi
}

run_backup() {
    local day dump part
    day=$(date -u +%Y-%m-%d)
    mkdir -p "$BACKUP_DIR/daily" "$BACKUP_DIR/weekly"
    dump="$BACKUP_DIR/daily/$PGDATABASE-$day.dump"
    part="$dump.part"
    log "dumping $PGDATABASE from ${PGHOST:-the local socket} to $dump"
    if ! pg_dump --format=custom --compress=6 --no-owner --no-privileges --file="$part" "$PGDATABASE"; then
        rm -f -- "$part"
        log "FAILED: pg_dump"
        return 1
    fi
    if ! pg_restore --list "$part" > /dev/null; then  # a readable archive, or nothing is kept
        rm -f -- "$part"
        log "FAILED: the dump is not a readable archive"
        return 1
    fi
    mv -f -- "$part" "$dump"
    log "done: $dump, $(du -k "$dump" | cut -f1) kB, $(pg_restore --list "$dump" | grep -c ' TABLE DATA ') tables"
    if [ "$(date -u +%u)" = 7 ]; then
        ln -f -- "$dump" "$BACKUP_DIR/weekly/$PGDATABASE-$(date -u +%G-W%V).dump"  # the same file, a second name
    fi
    prune "$BACKUP_DIR/daily" "$BACKUP_KEEP_DAILY"
    prune "$BACKUP_DIR/weekly" "$BACKUP_KEEP_WEEKLY"
    offsite "$dump" || log "the dump is kept locally; the off-site copy is tried again with tomorrow's"
}

seconds_until_next() {  # until the next $BACKUP_AT, UTC
    local now h m target
    now=$(date -u +%s)
    h=${BACKUP_AT%%:*}
    m=${BACKUP_AT##*:}
    target=$((now - now % 86400 + 10#$h * 3600 + 10#$m * 60))
    [ "$target" -gt "$now" ] || target=$((target + 86400))
    echo $((target - now))
}

loop() {
    trap 'log "stopping"; exit 0' TERM INT
    local delay
    if [ ! -e "$BACKUP_DIR/daily/$PGDATABASE-$(date -u +%Y-%m-%d).dump" ]; then
        run_backup || log "FAILED: the backup; next try at $BACKUP_AT"
    fi
    while :; do
        delay=$(seconds_until_next)
        log "next backup in $((delay / 60)) min"
        sleep "$delay" & wait $!  # in the background: a stop signal ends the wait at once
        run_backup || log "FAILED: the backup; next try tomorrow at $BACKUP_AT"
    done
}

check() {  # the sidecar's healthcheck
    if find "$BACKUP_DIR/daily" -name '*.dump' -mmin -1560 2>/dev/null | grep -q .; then
        exit 0
    fi
    echo "no dump younger than 26 h in $BACKUP_DIR/daily" >&2
    exit 1
}

case ${1:-once} in
    once) run_backup ;;
    loop) loop ;;
    check) check ;;
    *) echo "usage: backup.sh [once|loop|check]" >&2; exit 2 ;;
esac
