# Architecture Overview

## System Topology

The platform runs as five Docker containers orchestrated with Docker Compose:

```
┌─────────────────────────────────────────────────────────────┐
│  Docker Compose                                             │
│                                                             │
│  ┌──────────┐    ┌──────────────┐    ┌──────────────────┐  │
│  │  Caddy    │───▶│  Frontend    │    │  PostgreSQL 18   │  │
│  │  :80/:443 │    │  (React/Vite)│    │  :5432           │  │
│  │          │    └──────────────┘    └────────▲─────────┘  │
│  │          │                                 │            │
│  │          │    ┌──────────────┐    ┌────────┴─────────┐  │
│  │          │───▶│  FastAPI     │───▶│  Garage (S3)     │  │
│  │          │    │  :8000       │    │  :3900           │  │
│  └──────────┘    └──────────────┘    └──────────────────┘  │
│                                                             │
│  Networks:  frontend (Caddy ↔ Frontend ↔ API)              │
│             backend  (API ↔ PostgreSQL ↔ Garage)           │
└─────────────────────────────────────────────────────────────┘
```

| Service | Image | Purpose | Networks |
|---------|-------|---------|----------|
| `caddy` | `caddy:2-alpine` | Reverse proxy, TLS termination, security headers | frontend |
| `frontend` | Custom (React/Vite build) | SPA serving static assets | frontend |
| `api` | Custom (FastAPI/uvicorn) | REST API + WebSocket endpoints | frontend, backend |
| `db` | `postgres:18-alpine` | Primary datastore | backend |
| `garage` | `dxflrs/garage:v2.4.1` | S3-compatible object storage for media | backend |

The store is Garage and not MinIO, another S3 store, because MinIO stopped
publishing community images in September 2026 and left two 2026 security
advisories unfixed outside its commercial edition. The App speaks plain S3
calls.

## Network Isolation

Two bridge networks enforce separation of concerns:

- **`clavis-frontend`**: Caddy can reach both the frontend (static files) and the API (for `/api/*` proxying). The frontend container only needs to serve static files, so it doesn't need backend access.
- **`clavis-backend`**: The API can reach PostgreSQL and Garage. Neither the frontend nor Caddy can reach the database directly.

The API container bridges both networks — it serves HTTP/WebSocket requests from Caddy and connects to the database and storage backend.

## Request Flow

### HTTP Requests

```
Browser → Caddy (:80/:443)
              │
              ├─ /api/*  → FastAPI (:8000)
              │              ├─ Request Context Middleware (binds request_id)
              │              ├─ CORS Middleware
              │              ├─ Security Headers Middleware
              │              ├─ Rate Limiter (slowapi, in-memory)
              │              ├─ Router dispatch
              │              ├─ Auth dependencies (JWT extraction, session check)
              │              ├─ Org resolution (slug → org, membership check)
              │              ├─ Repository layer (async SQLAlchemy)
              │              └─ PostgreSQL / Garage
              │
              └─ /*      → Frontend (:80, nginx serving built assets)
```

### WebSocket Connections

```
Browser → Caddy → FastAPI WebSocket endpoint
                     │
                     ├─ JWT auth from ?token= query param
                     ├─ Org + role verification
                     ├─ ConnectionManager.subscribe(ws, channel)
                     │
                     └─ Keepalive loop (client: "ping" → server: "pong")

Meanwhile, on write operations:
    API handler → DB commit → pg_notify('channel', payload)
                                   │
                   PgNotifyListener (dedicated asyncpg connection)
                                   │
                   ConnectionManager.broadcast(ws_channel, message)
                                   │
                              WebSocket clients
```

## Application Factory

The FastAPI app is created via `create_app()` in `app/main.py`. The factory:

1. Configures the lifespan context manager (startup/shutdown)
2. Adds middleware in order: CORS → request context → security headers → rate limiter
3. Includes all routers with their URL prefixes
4. Returns the configured app instance

### Startup Sequence

The lifespan context manager runs these steps on startup (fail-fast on any error):

1. **Load and validate config** — Pydantic settings parse all env vars; missing/invalid values crash immediately
2. **Configure logging** — structlog with 4 JSON log streams (audit, app, error, access)
3. **Initialize database** — Create async engine, bind to session maker
4. **Ensure Garage bucket** — Create default bucket if it doesn't exist
5. **Start PgNotifyListener** — Dedicated asyncpg connection for LISTEN/NOTIFY (skipped in test mode)

### Shutdown Sequence

1. Stop PgNotifyListener (close dedicated connection)
2. Dispose database engine (close all pool connections)

## Configuration

All configuration flows through Pydantic settings classes in `app/config.py`. Settings are organized into nested groups:

| Group | Env Prefix | Key Settings |
|-------|-----------|-------------|
| `DatabaseSettings` | (none) | `DATABASE_URL`, pool_size, max_overflow |
| `StorageSettings` | `S3_` | endpoint, access_key, secret_key, bucket, secure, region |
| `AuthSettings` | (none) | `JWT_SECRET_KEY`, session timeout, password policy, lockout |
| `LoggingSettings` | (none) | `HMAC_AUDIT_KEY`, log_dir, retention periods |
| `WebAuthnSettings` | `WEBAUTHN_` | `WEBAUTHN_RP_NAME` is the name that the authenticator app shows next to the code. `WEBAUTHN_ORIGIN` is the public origin for links in mail and the result link when `PUBLIC_BASE_URL` is empty. `WEBAUTHN_RP_ID` is required at start, and no feature reads it |
| `CORSSettings` | (none) | `CORS_ORIGINS` (comma-separated) |

Settings are cached via `@lru_cache` — parsed once on first access. Critical secrets (`JWT_SECRET_KEY`, `HMAC_AUDIT_KEY`) have minimum length validators that enforce 32+ characters at startup.

## Resource Limits

Each container has CPU and memory limits defined in `docker-compose.yml`:

| Service | CPU Limit | Memory Limit | CPU Reserved | Memory Reserved |
|---------|-----------|--------------|--------------|-----------------|
| db | 2.0 | 1 GB | 0.5 | 512 MB |
| api | 2.0 | 1 GB | 0.5 | 512 MB |
| garage | 1.0 | 512 MB | 0.25 | 256 MB |
| frontend | 1.0 | 512 MB | 0.25 | 256 MB |
| caddy | 0.5 | 256 MB | 0.1 | 128 MB |

## Development vs Production

| Aspect | Development | Production |
|--------|-------------|------------|
| Reverse proxy | Caddy (in docker-compose) | Standalone server: Caddy. Shared services: the operator's reverse proxy, for example Nginx Proxy Manager (NPM) |
| TLS | Off (`auto_https off` in Caddyfile) | Standalone server: Caddy's automatic HTTPS with Let's Encrypt, and Caddy's TLS defaults (TLS 1.2 and 1.3 with forward-secret AEAD ciphers). Shared services: the operator's proxy |
| HSTS | Not set | `max-age=31536000; includeSubDomains`, set by `deployment/standalone/Caddyfile` or by the operator's proxy |
| SQL echo | Enabled (`echo=True`) | Disabled |
| Console logs | Colored structlog output to stdout | JSON file output only |
| Hot reload | `docker-compose.dev.yml` overlay mounts source | Built images, no mounts |
| Garage S3 API | Port 3900 exposed locally (host pytest needs it) | Not exposed; Garage has no browser console |
| CSP | Allows `unsafe-eval` for React dev tools | Stricter policy |
| Monitoring | None | An uptime monitor with alerting, run by the operator |
| Container mgmt | `docker compose` CLI | `docker compose` CLI or a container manager of the operator's choice |

**Dev mode**: Caddy runs inside docker-compose as the reverse proxy, routing `/api/*` to the backend and `/*` to the frontend. The `docker-compose.dev.yml` overlay adds volume mounts for hot-reload.

**Production**: [deployment/README.md](../../deployment/README.md) describes two layouts. The standalone server keeps Caddy as its reverse proxy, with automatic HTTPS. In the shared-services layout, a reverse proxy of the operator, such as Nginx Proxy Manager (NPM), replaces Caddy. It handles TLS termination and single-origin path routing on one domain: `/api/*` (including the WebSocket upgrade) goes to `clavis-api:8000`, everything else to `clavis-web:80`. In both layouts the frontend and the API set their own security headers. There is no staging environment. Pre-release verification happens against the local compose stack and the CI E2E stack. Each layout runs on a single server.
