#!/bin/sh
# Back up the standalone server: the database and the object store.
#
#   ./backup.sh                 # into ./backups
#   BACKUP_DIR=/srv/backup ./backup.sh
#
# Each run writes three files with the same time stamp:
#   clavis-db-<stamp>.dump        pg_dump of the database, taken while the app runs
#   clavis-objects-<stamp>.tar.gz the object store (photos, videos, audio, images)
#   clavis-logs-<stamp>.tar.gz    the log files of the API, with the audit log
# The object store stops for the seconds of the copy, so uploads in that moment
# fail. Run it at night, for example from cron:
#   15 3 * * * /opt/clavis/deployment/standalone/backup.sh >> /var/log/clavis-backup.log 2>&1
#
# Files older than BACKUP_KEEP_DAYS (default 30) are deleted. Set the setting
# BACKUP_ROTATION_DAYS of the API to the same number of days: the privacy text
# states it. Copy the files to another machine as well. A backup on the same
# disk does not survive the loss of the disk. The secrets in .env are needed for
# a restore, so keep a copy of .env apart from the backups.
#
# Restore: deployment/README.md, section "Backup and restore".
set -eu

cd "$(dirname "$0")"

BACKUP_DIR="${BACKUP_DIR:-./backups}"
BACKUP_KEEP_DAYS="${BACKUP_KEEP_DAYS:-30}"
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$BACKUP_DIR"
BACKUP_DIR="$(cd "$BACKUP_DIR" && pwd)"

echo "==> Database -> $BACKUP_DIR/clavis-db-$stamp.dump"
docker compose exec -T db pg_dump -U clavis -Fc clavis > "$BACKUP_DIR/clavis-db-$stamp.dump"

garage="$(docker compose ps -q garage)"
if [ -z "$garage" ]; then
    echo "The garage service is not running. Start the stack first." >&2
    exit 1
fi
echo "==> Object store -> $BACKUP_DIR/clavis-objects-$stamp.tar.gz"
docker compose stop garage
# Start the store again even when the copy fails.
trap 'docker compose start garage >/dev/null' EXIT
docker run --rm --volumes-from "$garage" -v "$BACKUP_DIR:/backup" alpine:3 sh -c "
    tar czf /backup/clavis-objects-$stamp.tar.gz -C / var/lib/garage/meta var/lib/garage/data &&
    chown $(id -u):$(id -g) /backup/clavis-objects-$stamp.tar.gz"
docker compose start garage
trap - EXIT

api="$(docker compose ps -q api)"
if [ -n "$api" ]; then
    echo "==> Logs -> $BACKUP_DIR/clavis-logs-$stamp.tar.gz"
    docker run --rm --volumes-from "$api" -v "$BACKUP_DIR:/backup" alpine:3 sh -c "
        tar czf /backup/clavis-logs-$stamp.tar.gz -C / var/log/clavis &&
        chown $(id -u):$(id -g) /backup/clavis-logs-$stamp.tar.gz"
fi

echo "==> Deleting backups older than $BACKUP_KEEP_DAYS days"
find "$BACKUP_DIR" -name 'clavis-*' -type f -mtime +"$BACKUP_KEEP_DAYS" -delete

ls -l "$BACKUP_DIR"/clavis-*-"$stamp".*
