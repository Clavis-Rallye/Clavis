# Clavis Backend

FastAPI-based backend for Clavis.

## Development Setup

### Prerequisites

- Python 3.12 or later (CI tests 3.12)
- [uv](https://docs.astral.sh/uv/)
- Docker (for PostgreSQL and Garage)

### Installation

From `src/backend`:

```bash
uv sync --locked --extra dev
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
```

The lock file `uv.lock` holds the versions that CI and the API image use.

### Running

The development server runs in the Docker development stack, because the API needs the database and the store:

```bash
# From the repository root
docker compose -f docker-compose.yml -f docker-compose.dev.yml up
```

### Testing

Outside Docker the tests run with `make test-backend` from the repository root. It sets every setting the tests need and starts the test store, and pytest starts its own Postgres through testcontainers. A bare `pytest` stops at start because the settings are missing.

```bash
# From the repository root
make test-backend
```

### Code Quality

```bash
# Lint
ruff check .

# Format
ruff format .

# Type check
mypy app/

# Security audit
uv pip install pip
pip-audit
```

A uv environment has no pip, and `pip-audit` needs one, so the audit installs it first.

## Docker Build

Build context must be `./src/backend` from project root:

```bash
# From project root
docker build -f src/backend/Dockerfile -t clavis-backend:latest src/backend
```

## Project Structure

```
src/backend/
├── app/                    # Application code
│   ├── __init__.py
│   ├── main.py            # FastAPI app factory
│   ├── config.py          # Pydantic Settings
│   ├── database.py        # SQLAlchemy setup
│   ├── logging.py         # structlog config
│   ├── storage.py         # S3 client (Garage)
│   ├── models/            # SQLAlchemy models
│   ├── repositories/      # Data access layer
│   ├── auth/              # Authentication & authorization
│   ├── routers/           # API endpoints
│   ├── services/          # Business logic
│   ├── middleware/        # FastAPI middleware
│   └── tools/             # CLI utilities
├── alembic/               # Database migrations
├── tests/                 # Test suite
├── pyproject.toml         # Dependencies & config
├── Dockerfile             # Multi-stage production build
└── README.md              # This file
```

## Environment Variables

See `.env.example` at project root for all required variables.

## License

EUPL-1.2. See the LICENSE file in the repository root.
