#!/bin/sh
# Write .env for the standalone server: the domain and fresh secrets.
#
#   ./init-env.sh rallye.example.org
#
# It refuses to touch an existing .env, because new secrets would lock the
# installation out of its own database and object store.
set -eu

cd "$(dirname "$0")"

domain="${1:-}"
if [ -z "$domain" ]; then
    echo "usage: $0 <domain>, for example $0 rallye.example.org" >&2
    exit 2
fi
if [ -e .env ]; then
    echo ".env exists already. Edit it by hand, or move it away first." >&2
    exit 1
fi

# n random bytes as hex characters
hex() {
    od -An -tx1 -N"$1" /dev/urandom | tr -d ' \n'
}

umask 077
awk \
    -v domain="$domain" \
    -v pg="$(hex 32)" \
    -v jwt="$(hex 32)" \
    -v hmac="$(hex 32)" \
    -v rpc="$(hex 32)" \
    -v access="GK$(hex 12)" \
    -v secret="$(hex 32)" '
    /^CLAVIS_DOMAIN=$/     { print "CLAVIS_DOMAIN=" domain; next }
    /^POSTGRES_PASSWORD=$/ { print "POSTGRES_PASSWORD=" pg; next }
    /^JWT_SECRET_KEY=$/    { print "JWT_SECRET_KEY=" jwt; next }
    /^HMAC_AUDIT_KEY=$/    { print "HMAC_AUDIT_KEY=" hmac; next }
    /^GARAGE_RPC_SECRET=$/ { print "GARAGE_RPC_SECRET=" rpc; next }
    /^S3_ACCESS_KEY=$/     { print "S3_ACCESS_KEY=" access; next }
    /^S3_SECRET_KEY=$/     { print "S3_SECRET_KEY=" secret; next }
    { print }
' .env.example > .env

echo "Wrote $(pwd)/.env for https://$domain"
echo "Keep a copy of .env in a safe place: the backup is useless without these secrets."
