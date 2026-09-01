SHELL := /bin/bash
.ONESHELL:
.SHELLFLAGS := -eu -o pipefail -c

# Test Garage on a deliberate high port so it never collides with the dev stack's Garage
# (compose, 3900); the admin API gets a second local port (readiness is GET /health there).
# The key pair is fixed and obviously fake — GK + 24 hex zeros, 64 hex zeros — the same
# shape as a real one, valid only inside test stacks. CI starts its own throwaway Garage
# the same way (ci.yml).
S3_TEST_PORT ?= 59000
S3_TEST_ADMIN_PORT ?= 59003
S3_ENDPOINT ?= 127.0.0.1:$(S3_TEST_PORT)
# Worker caps. Vitest forks one worker per CPU when uncapped and the backend suite starts
# Postgres testcontainers next to it. Together they can run a developer machine out of memory.
CLAVIS_TEST_WORKERS ?= 4
PYTEST_WORKERS ?= 2

.PHONY: test test-backend test-frontend lint

## the full verification: backend and frontend, mirroring ci.yml
test: test-backend test-frontend

## backend lint, types and tests, with the same env block as ci.yml and no developer .env
test-backend:
	@# writable cache for sandboxes that cannot write under ~/.cache
	if [ -z "$${UV_CACHE_DIR:-}" ]
	then
		uv_dir="$$(uv cache dir 2>/dev/null)"
		if ! mkdir -p "$$uv_dir" 2>/dev/null || ! [ -w "$$uv_dir" ]
		then
			export UV_CACHE_DIR=/tmp/uv-cache
		fi
	fi
	# Garage for the storage tests: reuse a running one, start one when the port is closed
	if [ "$(S3_ENDPOINT)" = "127.0.0.1:$(S3_TEST_PORT)" ] && ! (exec 3<>"/dev/tcp/127.0.0.1/$(S3_TEST_PORT)") 2>/dev/null
	then
		docker rm -f clavis-test-garage >/dev/null 2>&1 || true
		docker run -d --name clavis-test-garage \
			-p 127.0.0.1:$(S3_TEST_PORT):3900 -p 127.0.0.1:$(S3_TEST_ADMIN_PORT):3903 \
			-e GARAGE_RPC_SECRET=0000000000000000000000000000000000000000000000000000000000000000 \
			-e GARAGE_DEFAULT_ACCESS_KEY=GK000000000000000000000000 \
			-e GARAGE_DEFAULT_SECRET_KEY=0000000000000000000000000000000000000000000000000000000000000000 \
			-e GARAGE_DEFAULT_BUCKET=clavis-test \
			-v "$(CURDIR)/deployment/garage.toml:/etc/garage.toml:ro" \
			dxflrs/garage:v2.4.1 /garage server --single-node --default-bucket >/dev/null
		for i in $$(seq 1 30)
		do
			curl -fsS "http://127.0.0.1:$(S3_TEST_ADMIN_PORT)/health" >/dev/null 2>&1 && break
			sleep 1
		done
	fi
	cd src/backend
	export CLAVIS_ENV_FILE=none
	export DATABASE_URL=postgresql+asyncpg://clavis:clavis@localhost:5432/clavis_test
	export JWT_SECRET_KEY=test-secret-key-at-least-32-characters-long-for-ci
	export HMAC_AUDIT_KEY=test-hmac-key-for-audit-chain-integrity
	export S3_ENDPOINT="$(S3_ENDPOINT)"
	export S3_ACCESS_KEY=GK000000000000000000000000
	export S3_SECRET_KEY=0000000000000000000000000000000000000000000000000000000000000000
	export S3_BUCKET=clavis-test
	export S3_SECURE=false
	export SESSION_TIMEOUT_MINUTES=60
	export PASSWORD_MIN_LENGTH=8
	export PASSWORD_CRITERIA_COUNT=3
	export MAX_LOGIN_ATTEMPTS=5
	export LOCKOUT_DURATION_MINUTES=15
	export WEBAUTHN_RP_ID=localhost
	export WEBAUTHN_RP_NAME="Clavis Test"
	export WEBAUTHN_ORIGIN=http://localhost:3000
	export CORS_ORIGINS=http://localhost:3000
	export LOG_DIR=/tmp/clavis-logs
	export AUDIT_LOG_RETENTION_DAYS=365
	export APP_LOG_RETENTION_DAYS=90
	export ERROR_LOG_RETENTION_DAYS=90
	export TESTCONTAINERS_RYUK_DISABLED=true
	uv run --extra dev ruff check .
	uv run --extra dev ruff format --check .
	uv run --extra dev mypy app/
	# The WebSocket tests fail under pytest-sugar, so the plugin is off.
	uv run --extra dev pytest -q --tb=short -p no:sugar -n $(PYTEST_WORKERS) --dist loadfile

## frontend types, lint and unit tests with a capped worker pool (the build stays with CI)
test-frontend:
	@if [ -z "$${npm_config_cache:-}" ]
	then
		npm_dir="$$(npm config get cache 2>/dev/null)"
		if ! mkdir -p "$$npm_dir" 2>/dev/null || ! [ -w "$$npm_dir" ]
		then
			export npm_config_cache=/tmp/npm-cache
		fi
	fi
	cd src/frontend
	if [ ! -d node_modules ]
	then
		npm ci
	fi
	npx tsc -b
	npm run lint
	echo "frontend tests: up to $(CLAVIS_TEST_WORKERS) vitest workers (CLAVIS_TEST_WORKERS)"
	CLAVIS_TEST_WORKERS=$(CLAVIS_TEST_WORKERS) npx vitest run

## static checks only
lint:
	@# The recipe runs in one shell (.ONESHELL), so each cd starts from CURDIR.
	cd "$(CURDIR)/src/backend"
	uv run --extra dev ruff check .
	uv run --extra dev mypy app/
	cd "$(CURDIR)/src/frontend"
	if [ ! -d node_modules ]
	then
		npm ci
	fi
	# tsc -b builds the referenced projects. tsconfig.json lists no files of its own.
	npx tsc -b
	npm run lint
	# The router table of the API document must match src/backend/app/main.py. CI runs the same check.
	cd "$(CURDIR)"
	python3 scripts/docs-router-table.py --check
