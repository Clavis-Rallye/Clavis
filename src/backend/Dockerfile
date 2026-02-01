# Multi-stage Dockerfile for the Clavis API
# Build context: ./src/backend
#
# Python 3.12 — pinned to the SAME minor version CI tests on. Otherwise
# production runs an interpreter the test suite never exercised. Bump both
# together.
#
# Both install stages run `uv sync --locked` against uv.lock — the same
# lock file CI installs from — so tests, CI and the shipped image run the
# same versions. A dependency changes only through the lock file.
#
# The base images (python, uv) are pinned by digest, so a rebuild of the same
# code runs on the same Python and OS packages. Raise a digest on purpose,
# together with OS_PATCH_DATE below.

# Stage 1: Builder
FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b AS builder

# Install system dependencies for building Python packages
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# uv, pinned to a full release tag — the version this lock file is
# maintained with. CI installs the same lock file through setup-uv.
COPY --from=ghcr.io/astral-sh/uv:0.12.15@sha256:62f8c047d0a0e9ece6b53fc63df902585a67a47a7f318ddec4a37db586edc8e3 /uv /uvx /usr/local/bin/

# Same virtual environment path the runtime stage copies below; uv
# creates it from the image's Python 3.12 on first sync
ENV UV_PROJECT_ENVIRONMENT=/opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copy only requirements-related files first for better caching
COPY pyproject.toml uv.lock README.md /app/
WORKDIR /app

# Install Python dependencies from the lock file (no dev tools)
RUN uv sync --locked --no-dev

# Stage 2: Development (for hot-reload with docker-compose.dev.yml)
FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b AS development

# Install system dependencies (including build tools for editable install)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    libpq5 \
    curl \
    && rm -rf /var/lib/apt/lists/*

# uv, pinned to the same full release tag as the builder stage
COPY --from=ghcr.io/astral-sh/uv:0.12.15@sha256:62f8c047d0a0e9ece6b53fc63df902585a67a47a7f318ddec4a37db586edc8e3 /uv /uvx /usr/local/bin/

# Same virtual environment path the runtime image uses
ENV UV_PROJECT_ENVIRONMENT=/opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Set working directory
WORKDIR /app

# Copy requirements files
COPY pyproject.toml uv.lock README.md ./

# Install locked dependencies incl. dev tools. The application code
# copied below is found through WORKDIR: uvicorn's --app-dir defaults
# to the working directory.
RUN uv sync --locked --extra dev

# Copy application code
COPY . .

# Create log directory (must match LOG_DIR=/var/log/clavis)
RUN mkdir -p /var/log/clavis

# Expose port
EXPOSE 8000

# Development command with hot-reload
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]

# Stage 3: Runtime
FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b

# Install runtime dependencies. apt-get upgrade: the base image lags behind Debian's security fixes,
# and an image scan reports every fixable package of a running image.
# A build with a layer cache, for example on a host that built an earlier release, reuses this layer,
# so a new release keeps the old packages. Raise OS_PATCH_DATE to take the current Debian fixes.
ARG OS_PATCH_DATE=2026-11-15
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    curl \
    && apt-get upgrade -y \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user. The user id stays 1000, so the log volume of an
# existing installation keeps its owner.
RUN groupadd -r clavis && \
    useradd -r -g clavis -u 1000 -m -s /bin/bash clavis

# Copy virtual environment from builder
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
ENV PYTHONPATH="/app"

# pip is a build tool, and the running API never calls it. The copies of msgpack, urllib3 and
# setuptools inside pip carry high advisories, in the venv and in the system Python. The app's
# own urllib3 is a separate package and stays.
RUN rm -rf /opt/venv/lib/python3.12/site-packages/pip \
           /opt/venv/lib/python3.12/site-packages/pip-*.dist-info \
           /opt/venv/bin/pip /opt/venv/bin/pip3 /opt/venv/bin/pip3.12 \
           /usr/local/lib/python3.12/site-packages/pip \
           /usr/local/lib/python3.12/site-packages/pip-*.dist-info \
           /usr/local/bin/pip /usr/local/bin/pip3 /usr/local/bin/pip3.12 \
           /usr/local/lib/python3.12/ensurepip/_bundled

# Set working directory
WORKDIR /app

# Copy application code
COPY --chown=clavis:clavis . /app/

# Create log directory with correct permissions (must match LOG_DIR=/var/log/clavis)
RUN mkdir -p /var/log/clavis && \
    chown -R clavis:clavis /var/log/clavis

# Third-party licence notices: MIT/BSD/ISC/Apache-2.0 ask for
# their notice in every copy, and the installed site-packages are one. Collected
# at build time from the venv this image actually ships, after the app code is
# in place and before the user switch.
RUN python -m app.third_party_licenses > /app/THIRD_PARTY_LICENSES.txt && \
    chown clavis:clavis /app/THIRD_PARTY_LICENSES.txt

# Switch to non-root user
USER clavis

# Expose port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD curl -f http://localhost:8000/api/health || exit 1

# Default command (can be overridden by docker-compose)
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
