# The backup sidecar (docker-compose.prod.yml, db-backup): PostgreSQL 16 client tools (pg_dump, pg_restore,
# psql) — the same major as the db service: pg_dump must not be older than the server —, bash for the scripts
# and the AWS CLI for the optional off-host copy. Runs as postgres, never as root.
FROM postgres:16-alpine

RUN apk add --no-cache bash aws-cli && \
    mkdir -p /backups && chown postgres:postgres /backups

COPY backup.sh restore.sh /usr/local/bin/
RUN chmod 755 /usr/local/bin/backup.sh /usr/local/bin/restore.sh

USER postgres

# the image's entrypoint runs any command that is not `postgres` as given
CMD ["backup.sh", "loop"]
