#!/usr/bin/env bash
# log-cleanup.sh — Compress rotated logs older than N days; delete logs past retention.
# Run daily via cron (see deployment/README.md, "Log retention"):
#   0 4 * * * LOG_DIR=<log directory> /opt/clavis/deployment/scripts/log-cleanup.sh >> /var/log/clavis-log-cleanup.log 2>&1
#
# The retention periods are the App's own settings, with the App's defaults:
#   AUDIT_LOG_RETENTION_DAYS   365
#   APP_LOG_RETENTION_DAYS      90
#   ERROR_LOG_RETENTION_DAYS    90
#   ACCESS_LOG_RETENTION_DAYS   90
# A value comes from the environment first, then from the deployment .env
# (ENV_FILE, default: the .env beside this scripts/ folder), then the default.
# The App reads the same variables, so the files it keeps and the files this
# script deletes follow one number per stream.
#
# The compression delays are script settings with the same lookup:
#   AUDIT_LOG_COMPRESS_AFTER_DAYS   7
#   LOG_COMPRESS_AFTER_DAYS         3   (application, error, access)
# The log views of the App read compressed files too.

set -euo pipefail

LOG_DIR="${LOG_DIR:-/var/log/clavis}"
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" > /dev/null 2>&1 && pwd)"
ENV_FILE="${ENV_FILE:-$SCRIPT_DIR/../.env}"

# setting NAME DEFAULT: prints the number of days for NAME.
# A value that is not a whole number from 1 to 3650 (the App's own limits)
# falls back to DEFAULT with a warning, so a typo never deletes too much.
setting() {
  local name="$1" default="$2" value
  value="${!name:-}"
  if [ -z "$value" ] && [ -f "$ENV_FILE" ]; then
    value="$(sed -n "s/^${name}=//p" "$ENV_FILE" | tail -n 1 | tr -d "\"' \r")"
  fi
  if [ -z "$value" ]; then
    value="$default"
  elif ! [[ "$value" =~ ^[0-9]+$ ]] || [ "$((10#$value))" -lt 1 ] || [ "$((10#$value))" -gt 3650 ]; then
    echo "[log-cleanup] $name=$value is not a number of days from 1 to 3650, using $default" >&2
    value="$default"
  fi
  printf '%s' "$((10#$value))"
}

if [ ! -d "$LOG_DIR" ]; then
  echo "[log-cleanup] log directory $LOG_DIR does not exist, set LOG_DIR" >&2
  exit 1
fi

compress_after() {
  local stream="$1" days="$2"
  find "$LOG_DIR" -maxdepth 1 -name "${stream}.log.*" -not -name "*.gz" -mtime "+${days}" -exec gzip {} \;
}

# gzip keeps the modification time of the original, so a compressed file ages
# from the day it was written, not from the day it was compressed.
delete_after() {
  local stream="$1" days="$2"
  find "$LOG_DIR" -maxdepth 1 -name "${stream}.log.*" -mtime "+${days}" -delete
}

audit_days="$(setting AUDIT_LOG_RETENTION_DAYS 365)"
app_days="$(setting APP_LOG_RETENTION_DAYS 90)"
error_days="$(setting ERROR_LOG_RETENTION_DAYS 90)"
access_days="$(setting ACCESS_LOG_RETENTION_DAYS 90)"
audit_compress="$(setting AUDIT_LOG_COMPRESS_AFTER_DAYS 7)"
compress="$(setting LOG_COMPRESS_AFTER_DAYS 3)"

# Compression (gzip rotated files past threshold)
compress_after audit "$audit_compress"
compress_after application "$compress"
compress_after error "$compress"
compress_after access "$compress"

# Retention deletion (gzip'd and plain)
delete_after audit "$audit_days"
delete_after application "$app_days"
delete_after error "$error_days"
delete_after access "$access_days"

echo "[log-cleanup] $(date -u +%Y-%m-%dT%H:%M:%SZ) done. Retention days: audit $audit_days, application $app_days, error $error_days, access $access_days"
