#!/bin/sh
# Try Clavis on this machine in one command.
#
#   ./scripts/quickstart.sh
#
# The script
#   1. writes .env from .env.example with two fresh keys, unless .env exists,
#   2. builds and starts the local stack (docker-compose.yml),
#   3. creates an operator account, a first organization "Demo" with its
#      administrator, and loads the demo rallye into it, with the languages
#      German and English,
#   4. prints the address and the two sign-ins. They are also kept in
#      .quickstart-accounts.
#
# Run it again at any time: it rebuilds and restarts the stack, keeps its data
# and prints the sign-ins again.
# To start from scratch: docker compose down -v && rm .env .quickstart-accounts
#
# Needs Docker with Compose v2 and curl. The stack answers on http://localhost.
# If port 80 is taken, start it with another port, for example
#   CLAVIS_HTTP_PORT=8080 ./scripts/quickstart.sh
# The first run writes the port into .env, and the stack answers on
# http://localhost:8080.
# This is a trial setup, not a server installation: deployment/README.md.
set -eu

cd "$(dirname "$0")/.."

fail() {
    echo "quickstart: $*" >&2
    exit 1
}

command -v docker >/dev/null 2>&1 || fail "Docker is missing. Install Docker Engine or Docker Desktop first."
docker compose version >/dev/null 2>&1 || fail "Docker Compose v2 is missing (the command 'docker compose')."
command -v curl >/dev/null 2>&1 || fail "curl is missing."

# The HTTP status of a request, or 000 when the server cannot be reached.
# curl runs without -f and without a visible error message, so the caller
# decides what to tell the person; the script never prints a bare curl error.
http_status() {
    curl -s -o /dev/null -w '%{http_code}' "$@" 2>/dev/null || true
}

# n random bytes as hex characters
hex() {
    od -An -tx1 -N"$1" /dev/urandom | tr -d ' \n'
}

# 1. Settings
if [ ! -e .env ]; then
    awk -v jwt="$(hex 32)" -v hmac="$(hex 32)" '
        /^JWT_SECRET_KEY=/ { print "JWT_SECRET_KEY=" jwt; next }
        /^HMAC_AUDIT_KEY=/ { print "HMAC_AUDIT_KEY=" hmac; next }
        { print }
    ' .env.example > .env
    # A port given on the command line stays in .env for later docker compose calls.
    if [ -n "${CLAVIS_HTTP_PORT:-}" ]; then
        printf '\nCLAVIS_HTTP_PORT=%s\n' "$CLAVIS_HTTP_PORT" >> .env
    fi
    echo "==> Wrote .env with fresh keys"
fi

port="${CLAVIS_HTTP_PORT:-$(sed -n 's/^CLAVIS_HTTP_PORT=//p' .env | tail -n 1)}"
base="http://localhost${port:+:$port}"

# 2. The stack. The first build takes several minutes. A running stack keeps
# serving while the images build.
echo "==> Building the images"
docker compose build \
    || fail "The images did not build. Look at the messages above, fix the cause, then run this script again."
# A stack of an earlier run goes down first, so that up creates its networks
# anew. An update can change a network. Compose 2.33 then fails at up with
# "network not found", and Compose 5.7 restarts the unchanged services without
# their names on the new network, so Caddy answers 502. down without -v keeps
# the data volumes.
if [ -n "$(docker compose ps -a -q 2>/dev/null)" ]; then
    echo "==> Removing the containers and networks of the existing stack. The data volumes stay."
    docker compose down --remove-orphans \
        || fail "Could not remove the existing stack. Look at the messages above, then run this script again."
fi
echo "==> Starting the stack"
docker compose up -d --wait \
    || fail "The stack did not start. Look at the messages above and at 'docker compose logs', fix the cause, then run this script again."
status="$(http_status "$base/api/health")"
[ "$status" = 200 ] || fail "$base/api/health does not answer (status $status). Look at 'docker compose logs api caddy', then run this script again."

# 3. Accounts and demo content, once
if [ ! -e .quickstart-accounts ]; then
    operator_email=operator@example.org
    admin_email=admin@example.org
    # Upper and lower case, a digit and a special character, for the password policy
    operator_password="Clavis-$(hex 8)-1"
    admin_password="Clavis-$(hex 8)-2"

    echo "==> Creating the operator account $operator_email"
    docker compose exec -T -e INSTANCE_ADMIN_PASSWORD="$operator_password" api \
        python -m app.cli instance-admin create --email "$operator_email" \
        || fail "Could not create the operator. An earlier run may have created it: start from scratch, see the top of this script."

    echo "==> Creating the organization Demo with its administrator $admin_email"
    status="$(http_status -X POST "$base/api/auth/register" \
        -H 'Content-Type: application/json' \
        -d "{\"organization_name\": \"Demo\", \"organization_slug\": \"demo\", \"email\": \"$admin_email\", \"password\": \"$admin_password\", \"display_name\": \"Demo Admin\"}")"
    case "$status" in
        2??) ;;
        000) fail "Could not reach $base to create the organization. Look at 'docker compose logs api caddy', then start from scratch, see the top of this script." ;;
        *) fail "Could not create the organization (status $status). Only the first organization of an installation registers itself this way. Start from scratch, see the top of this script." ;;
    esac

    # The demo content is in German and English: the command adds both
    # languages to the organization together with the content.
    echo "==> Loading the demo rallye"
    docker compose exec -T api python -m app.cli demo-content load --org-slug demo --enable-languages \
        || fail "Could not load the demo rallye. Look at the message above, then start from scratch, see the top of this script."

    umask 077
    cat > .quickstart-accounts <<EOT
Clavis is running at $base

Organization "Demo", with the demo rallye:
  sign in at  $base/login
  e-mail      $admin_email
  password    $admin_password

Operator console, above all organizations:
  sign in at  $base/instance/login
  e-mail      $operator_email
  password    $operator_password

Both accounts set up an authenticator app at the first sign-in.
EOT
fi

echo
cat .quickstart-accounts
echo
echo "Stop:            docker compose stop"
echo "Remove all data: docker compose down -v && rm .env .quickstart-accounts"
