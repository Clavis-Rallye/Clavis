#!/usr/bin/env bash
# Local preflight — mirrors the CI gates so PRs arrive green.
#
# Run before every push (see CONTRIBUTING.md):
#   ./scripts/preflight.sh            # lint + types + unit tests (fast, ~2 min)
#   ./scripts/preflight.sh --smoke    # + E2E @smoke against the local stack
#
# The --smoke tier needs the dev stack up:
#   docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d --wait
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND="$ROOT/src/backend"
FRONTEND="$ROOT/src/frontend"
FAIL=0

step() { echo; echo "── $1"; }
run()  { "$@" || { echo "   ✗ FAILED: $*"; FAIL=1; }; }

PY="$BACKEND/.venv/bin/python"
[ -x "$PY" ] || { echo "backend venv missing — run: uv sync --directory src/backend --extra dev"; exit 1; }

step "Backend: ruff check"
run "$PY" -m ruff check "$BACKEND/app" "$BACKEND/tests" --quiet

step "Backend: ruff format --check"
run "$PY" -m ruff format --check --quiet "$BACKEND/app" "$BACKEND/tests"

step "Backend: mypy"
(cd "$BACKEND" && run "$PY" -m mypy app/)

step "Frontend: eslint (errors only)"
(cd "$FRONTEND" && run npx eslint src --quiet)

# The build, not `tsc --noEmit`, is the check CI actually runs. `--noEmit -p
# tsconfig.app.json` type-checks the app project alone, while `npm run build`
# is `tsc -b && vite build`: the build resolves project references (test files
# included) and then compiles. Everything the narrower check misses is real —
# widening a union at its use site but not in the type it is written into
# passes `--noEmit` and fails the build.
# Costs a couple of seconds here and saves a red PR.
step "Frontend: build (tsc -b + vite build — the CI gate)"
(cd "$FRONTEND" && run npm run build)

step "Frontend: vitest"
(cd "$FRONTEND" && run npx vitest run --reporter=dot)

if [ "${1:-}" = "--smoke" ]; then
  step "E2E @smoke (local stack must be up)"
  if curl -fsS --max-time 3 http://localhost/api/health >/dev/null 2>&1; then
    (cd "$FRONTEND" && run npx playwright test --grep "@smoke" --reporter=list)
  else
    echo "   ✗ local stack not reachable on http://localhost — start it first"
    FAIL=1
  fi
fi

echo
if [ "$FAIL" -eq 0 ]; then
  echo "✅ preflight green — safe to push"
else
  echo "❌ preflight failed — fix before pushing"
fi
exit "$FAIL"
