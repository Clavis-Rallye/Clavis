# Getting Started

## Prerequisites

### Required Software

| Tool | Minimum Version | Purpose |
|------|----------------|---------|
| **Docker** | 24.0+ | Container orchestration |
| **Docker Compose** | 2.20+ | Multi-container deployment |
| **Node.js** | 22.22.2 or a later 22, 24.15.0 or a later 24, or 26 or later | Frontend development outside Docker. The project builds and tests with Node 24 |
| **Python** | 3.12+ | Backend development outside Docker |
| **uv** | current | Python environments and the lock file of the backend |
| **Git** | 2.30+ | Version control |

The project tests with Compose 2.20, 2.33 and 5.7. The stack alone needs only Docker and git. Node.js, Python and uv are for work outside Docker and
for the tests.

### Optional Tools

- **PostgreSQL client** (psql) - Database inspection
- **HTTPie** or **curl** - API testing
- **VS Code** - Recommended IDE with extensions:
  - Python (Microsoft)
  - ESLint
  - Prettier
  - Docker

## Getting Started

`./scripts/quickstart.sh` runs steps 1 to 6 in one command, see the README. The steps by hand:

### 1. Clone and Setup

```bash
git clone https://github.com/Clavis-Rallye/Clavis.git
cd Clavis

# Copy environment template
cp .env.example .env

# Replace JWT_SECRET_KEY and HMAC_AUDIT_KEY in .env, each with the output of
openssl rand -hex 32
```

### 2. Start Services

```bash
# Start all services (production-like build, plain HTTP)
docker compose up -d --build --wait

# OR start with hot-reload for development
docker compose -f docker-compose.yml -f docker-compose.dev.yml up

# Verify all services are healthy
docker compose ps
```

Caddy is the only entry. It routes `/api/*` to the API and everything else to the frontend, on
one origin:
- **Application**: http://localhost
- **API docs**: http://localhost/api/docs (Swagger UI) and http://localhost/api/redoc
- **Garage S3 API**: only with the dev override, on localhost:3900 (access key
  `GK000000000000000000000000`, see `docker-compose.yml`). Garage has no browser console.

If port 80 is taken, set `CLAVIS_HTTP_PORT=8080` in `.env`. The stack then answers on
http://localhost:8080, and `docker-compose.yml` moves the origins of the API (`WEBAUTHN_ORIGIN`,
`CORS_ORIGINS`, `PUBLIC_BASE_URL`) to that port. Leave the variable unset for port 80. A second
stack on the same machine also needs `STACK_SUFFIX` (container, volume and network names) and
`COMPOSE_PROJECT_NAME`. The dev override publishes Garage on `CLAVIS_S3_PORT` (default 3900).
Continuous integration sets all of them per job.

### 3. Database Migrations

The API container of `docker-compose.yml` runs `alembic upgrade head` before it starts serving, so
a fresh volume needs no manual step. The dev override replaces that command, so there:

```bash
# Apply migrations
docker compose exec api alembic upgrade head

# Check migration status
docker compose exec api alembic current
```

### 4. Verify Installation

```bash
# Check API health
curl http://localhost/api/health

# Expected response:
# {"status":"ok","database":"connected","storage":"connected"}
```

### 5. Create a Local Instance Operator

The operator console at http://localhost/instance/login needs an
operator account — the identity above every organization (see
[roles-and-permissions.md](../domain/roles-and-permissions.md)). Accounts
exist only through the CLI, never through an HTTP route:

```bash
# Password via env var…
docker compose exec -e INSTANCE_ADMIN_PASSWORD='DevPass123!' api \
  python -m app.cli instance-admin create --email dev@example.org

# …or the interactive prompt
docker compose exec api python -m app.cli instance-admin create --email dev@example.org
```

The first login returns the TOTP provisioning URI once — scan it into an
authenticator app and confirm the code. From then on, the operator signs in
with password and code.

The rest of the account lifecycle — `instance-admin list|disable|enable
|reset-totp|set-password` — is also server-only, never an HTTP route; see
[deployment/README.md](../../deployment/README.md#operator-account-commands).

### 6. Create an Organization and Load the Demo Content

The first organization registers itself at http://localhost/register/organization, with its
administrator. This page works only while no organization exists. Further organizations come from
the operator console, "Organizations", "New organization". The console invites the first
administrator by mail. Without `SMTP_HOST` the app writes the mail to its log instead of sending
it. To receive such mail locally, run a mail catcher such as Mailpit and set `SMTP_HOST`,
`SMTP_PORT` and `SMTP_TLS_MODE=none` in `.env`.

To fill an organization with the ready demo rallye, run
`docker compose exec api python -m app.cli demo-content load --org-slug <slug>`
(see [deployment/README.md](../../deployment/README.md#demo-organization)). The demo is in German
and English, so enable English in the settings of the organization.

## Project Structure

```
Clavis/
├── docker-compose.yml              # The local stack, production-like build
├── docker-compose.dev.yml          # Development overrides (hot-reload)
├── Caddyfile                       # Reverse proxy configuration
├── .env.example                    # Environment variable template
│
├── src/
│   ├── backend/                    # FastAPI application
│   │   ├── Dockerfile
│   │   ├── pyproject.toml         # Python dependencies
│   │   ├── alembic.ini            # Migration config
│   │   ├── alembic/
│   │   │   └── versions/          # Database migrations
│   │   ├── app/
│   │   │   ├── main.py            # Application factory
│   │   │   ├── config.py          # Settings (env vars)
│   │   │   ├── database.py        # SQLAlchemy async engine
│   │   │   ├── logging.py         # Structured logging
│   │   │   ├── storage.py         # S3 client (Garage)
│   │   │   ├── models/            # Database models
│   │   │   ├── repositories/      # Data access layer
│   │   │   ├── auth/              # Authentication & authorization
│   │   │   ├── routers/           # API endpoints
│   │   │   ├── services/          # Business logic
│   │   │   ├── middleware/        # Request/response processing
│   │   │   └── tools/             # CLI utilities
│   │   └── tests/                 # Backend tests
│   │
│   └── frontend/                   # React + Vite application
│       ├── Dockerfile
│       ├── package.json           # Node dependencies
│       ├── vite.config.ts         # Build configuration
│       ├── vitest.config.ts       # Test configuration
│       ├── public/                # Static assets
│       └── src/
│           ├── main.tsx           # Application entry
│           ├── App.tsx            # Route configuration
│           ├── theme.ts           # MUI theme (design tokens)
│           ├── api/               # API client
│           ├── auth/              # Auth context & routes
│           ├── layouts/           # App shell & layouts
│           ├── pages/             # Page components
│           └── test/              # Frontend tests
│
└── docs/                           # Documentation
    ├── development/               # Technical docs (you are here)
    ├── domain/                    # Domain model, roles, run lifecycle
    └── design/                    # Design system
```

## Development Workflow

### Backend Development

#### Running Locally (outside Docker)

```bash
cd src/backend

# Install dependencies (the locked versions CI and the API image run)
uv sync --locked --extra dev
source .venv/bin/activate

# Lint
ruff check .

# Type check
mypy app/

# Security audit (pip-audit needs a real pip in the venv, so seed it
# first — same workaround as CI, see ci.yml)
uv pip install pip
pip-audit
```

The development server does not run outside Docker, because its settings are missing. Start it in the development stack:

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up
```

Run the tests with `make test-backend` from the repository root. It sets every setting the tests need. A bare `pytest` stops at start with `database_url: Field required`.

#### Creating Database Migrations

The development stack cannot write a new migration, because it mounts the migrations read-only. Use the local stack, `docker-compose.yml` alone, which has no source mount.

```bash
# Build the changed models into the image
docker compose up -d --build --wait

# Generate the migration in the container. The command prints the path of the file
docker compose exec api alembic revision --autogenerate -m "description of change"

# Copy the file into the checkout
docker compose cp api:/app/alembic/versions/<the printed file name> src/backend/alembic/versions/
```

The database carries comments and server defaults that the migrations wrote. The models differ from them in comments, in server defaults and in the storage of six enum types. So autogenerate also lists those changes. Review the file in `src/backend/alembic/versions/` and keep only the intended change.

The API runs `alembic upgrade head` when it starts, so `docker compose up -d --build --wait` applies the new migration. To roll one migration back:

```bash
docker compose exec api alembic downgrade -1
```

### Frontend Development

#### Running Locally (outside Docker)

```bash
cd src/frontend

# Install dependencies
npm install

# Start dev server
npm run dev

# Lint
npx eslint src/

# Type check
npx tsc -b

# Run tests
npm test

# Build for production
npm run build

# Security audit
npm audit
```

### Docker Development Mode

The `docker-compose.dev.yml` override provides:
- **Hot-reload**: Changes to source code automatically restart services
- **Volume mounts**: Local `src/` directories mounted into containers
- **Debug ports**: Direct access to PostgreSQL (5432) and the Garage S3 API (3900)

```bash
# Start with dev overrides
docker compose -f docker-compose.yml -f docker-compose.dev.yml up

# Rebuild after dependency changes
docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build

# Frontend dependency changes need one more flag. The frontend keeps
# node_modules in an anonymous volume, and compose carries that volume over
# to the recreated container, so the new image's node_modules never shows up.
docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build --renew-anon-volumes frontend
```

## Testing Guide

### The whole suite in one command

`make test` runs what CI runs, in CI's environment. It covers backend lint, types and
pytest, then frontend types, lint and vitest. It ignores your developer `.env` (`CLAVIS_ENV_FILE=none`),
so a local setting cannot make the suite pass here and fail in CI. It starts a throwaway
Garage container, `clavis-test-garage`, on `127.0.0.1:59000` when that port is closed, and reuses
one already running there, and pytest brings up its own Postgres through testcontainers.
On Windows with WSL 2, Windows can reserve that port range, and `make test-backend` then stops with "ports are not available". Pass other ports:
`make test-backend S3_TEST_PORT=59300 S3_TEST_ADMIN_PORT=59303`. CI starts its
own Garage container fresh per run instead, on a Docker-assigned port, and never reuses one
(`ci.yml`). Either way, `S3_REGION` (default `us-east-1`) must equal the store's configured
`s3_region` in `deployment/garage.toml`, or storage calls fail with `AuthorizationHeaderMalformed`.

```bash
make test               # everything, 30 to 60 minutes
make test-backend       # ruff, mypy, pytest
make test-frontend      # tsc, eslint, vitest
make lint               # static checks only
```

Two worker caps keep the suite from starving a machine that also runs an editor:
`CLAVIS_TEST_WORKERS` (vitest, `make test` sets 4, and without it the config takes cores minus one, at most 4) and `PYTEST_WORKERS` (pytest-xdist, default 2). Raise them
on a big machine, lower them next to a browser. The sections below are the finer-grained commands
for working on one part.

The backend environment in CI and under `make test` is managed by **uv**
against `src/backend/uv.lock`: every backend CI job installs with
`uv sync --locked --extra dev` on a managed CPython 3.12, and `--locked` fails
the build when the lock file is out of sync with `pyproject.toml` instead of
quietly resolving fresh, unpinned versions — the lock file, not
`pyproject.toml` alone, is what CI actually tests. `make test-backend` drives
the same environment with `uv run --extra dev` (every `uv run` re-syncs the
environment exactly, which is why each command repeats the dev extra). The
API image installs from the same `uv.lock` — its Dockerfile runs
`uv sync --locked` in both stages — so CI and the shipped image run the
locked versions too, and a dependency changes only through the lock file.

### Backend Tests

Tests use **testcontainers** to spin up real PostgreSQL instances. No mocking of database interactions.

Run the tests from the repository root:

```bash
make test-backend
```

`make test-backend` sets every setting the tests need. A bare `pytest` outside it stops at start, because settings such as `database_url` are missing (`database_url: Field required`).

**Test structure**:
- `conftest.py` - Fixtures (testcontainer DB, test client, factories)
- `factories.py` - Test data factories (users, organizations)
- `test_*.py` - Test modules organized by feature

**Environment for tests**:
- Set `TESTCONTAINERS_RYUK_DISABLED=true` if Ryuk container causes issues in WSL

### Frontend Tests

Uses **Vitest** with **@testing-library/react** in jsdom environment.

```bash
cd src/frontend

# Run tests
npm test

# Run with coverage
npm test -- --coverage

# Run in watch mode
npm test -- --watch

# Run specific test file
npm test -- Login.test.tsx
```

**Test structure**:
- Component tests in `tests/*.test.tsx`
- Test utilities and mocks alongside tests
- Uses `@testing-library/react` for component rendering

### Integration Testing

Full-stack integration test checklist:

1. **User Registration**
   ```bash
   This works only while no organization exists, or with `ALLOW_UNLIMITED_ORG_REGISTRATIONS=true`.

   ```bash
   curl -X POST http://localhost/api/auth/register \
     -H "Content-Type: application/json" \
     -d '{
       "organization_name": "Test Org",
       "organization_slug": "test-org",
       "email": "admin@example.com",
       "password": "SecurePass123!",
       "display_name": "Admin User"
     }'
   ```

2. **Login** (check for Set-Cookie header)
   ```bash
   curl -X POST http://localhost/api/auth/login \
     -H "Content-Type: application/json" \
     -d '{"email": "admin@example.com", "password": "SecurePass123!"}'
   ```

3. **Access Protected Resource**
   ```bash
   curl http://localhost/api/organizations \
     --cookie "access_token=<token-from-login>"
   ```

4. **Verify Audit Log**
   ```bash
   docker compose exec api python -m app.tools.verify_audit_log /var/log/clavis/audit.log
   ```

## Environment Variables Reference

`.env.example` lists every setting with its default. [deployment/README.md](../../deployment/README.md#configuration), section "Configuration", explains the settings of a server.

## Architecture Overview

The architecture is in these documents:

- [architecture-overview.md](architecture-overview.md): the services, the request flow and the networks.
- [authentication.md](authentication.md): tokens, the second factor, roles and sessions.
- [data-layer.md](data-layer.md): models, repositories, tenancy and migrations.
- [realtime.md](realtime.md): WebSockets and the change feed.
- [api-structure.md](api-structure.md): URL patterns, routers and schemas.

## Security Summary

The security design is in [security-and-compliance.md](security-and-compliance.md).

## Common Issues & Solutions

### Docker

**Issue**: Services fail to start with "port already in use"
```bash
# Check what's using the port
lsof -i :80

# Stop the conflicting service, or set CLAVIS_HTTP_PORT=8080 in .env
```

**Issue**: `docker compose up` hangs on database health check
```bash
# Check database logs
docker compose logs db

# Restart database
docker compose restart db
```

### Backend

**Issue**: Alembic migration fails with "relation already exists"
```bash
# Reset database (DESTROYS DATA!)
docker compose down -v
docker compose up -d --build --wait
```

**Issue**: Tests fail with testcontainers connection error
```bash
# Ensure Docker daemon is accessible
docker ps

# For WSL, set environment variable
export TESTCONTAINERS_RYUK_DISABLED=true
make test-backend
```

**Issue**: Import errors in tests
```bash
# Re-create the locked environment (installs the backend editable)
cd src/backend
uv sync --locked --extra dev
```

### Frontend

**Issue**: CORS errors when accessing API
```bash
# The browser calls the API on its own origin. Check that VITE_API_URL is empty
# in .env, and rebuild the frontend after a change: docker compose up -d --build frontend
VITE_API_URL=
```

**Issue**: Frontend shows blank page
```bash
# Check browser console for errors
# Verify API is reachable
curl http://localhost/api/health
```

**Issue**: Changes not appearing (hot-reload not working)
```bash
# Restart dev server
npm run dev
```

### Logs & Debugging

**View service logs**:
```bash
# All services
docker compose logs -f

# Specific service
docker compose logs -f api

# Last 100 lines
docker compose logs --tail=100 api
```

**Access service shells**:
```bash
# Backend
docker compose exec api bash

# Frontend
docker compose exec frontend sh

# Database
docker compose exec db psql -U clavis
```

**Verify audit log integrity**:
```bash
docker compose exec api python -m app.tools.verify_audit_log /var/log/clavis/audit.log
```

## Hardware & Capacity Planning

CPU, memory, disk and ports for a server are in [deployment/README.md](../../deployment/README.md#what-you-need).

## Additional Resources

- **API Documentation**: http://localhost/api/docs (Swagger UI)
- **Domain Model**: [docs/domain/domain-model.md](../domain/domain-model.md)
- **Design System**: [docs/design/design-system.md](../design/design-system.md)
- **FastAPI Docs**: https://fastapi.tiangolo.com
- **SQLAlchemy 2.0 Docs**: https://docs.sqlalchemy.org/en/20/
- **MUI Documentation**: https://mui.com/material-ui/
- **Vite Guide**: https://vitejs.dev/guide/
