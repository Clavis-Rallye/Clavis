"""
FastAPI Application Factory

Creates and configures the FastAPI application with:
- Lifespan context manager for startup/shutdown
- CORS middleware
- Request context middleware (request_id propagation)
- Security headers middleware
- Router inclusion
"""

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from starlette.formparsers import MultiPartException
from starlette.responses import Response

from app import __version__
from app.api_docs import install_api_docs
from app.exceptions import CodedHTTPException
from app.middleware.rate_limit import limiter
from app.middleware.request_context import request_context_middleware
from app.services.richtext import RichTextTooLongError


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Start the services of the API at startup and stop them at shutdown.

    Startup:
    - Validate the configuration (fails fast on a missing or invalid variable)
    - Configure logging, log the startup warnings and recover the audit chain
    - Initialize the database engine
    - Ensure the S3 bucket exists
    - Start PgNotifyListener, RetentionSweeper and AutoStarter (skipped if the TESTING env var is set)

    Shutdown: stop those three, then dispose the database engine.
    """
    # The import runs the validation.
    from app.config import settings

    print("✅ Configuration loaded successfully")
    print(f"  • Environment: {settings.environment}")
    print(f"  • Database: {settings.database.database_url.split('@')[0]}@***")
    print(f"  • S3 store: {settings.storage.endpoint}")
    print(f"  • Session timeout: {settings.auth.session_timeout_minutes} minutes")

    from app.logging import ensure_logging_configured

    ensure_logging_configured()
    print(f"✅ Logging configured (log dir: {settings.logging.log_dir})")

    # Settings an operator most likely forgot. They never stop the start: an
    # installation without mail must still serve. The structlog line puts them
    # into the JSON log of a production stack too.
    import structlog

    from app.config import startup_warnings

    for warning in startup_warnings(settings):
        print(f"⚠️  {warning}")
        structlog.get_logger(__name__).warning("config_incomplete", detail=warning)

    # Recover the audit-chain hash so entries appended after a restart chain
    # to the last on-disk hash instead of resetting to genesis.
    from app.audit_handler import init_audit_chain

    init_audit_chain()
    print("✅ Audit chain recovered from sidecar")

    from app.database import dispose_db, init_db

    await init_db()
    print("✅ Database initialized")

    from app.storage import ensure_bucket

    ensure_bucket()
    print(f"✅ S3 bucket '{settings.storage.bucket}' ready")

    pg_listener = None
    is_testing = os.getenv("TESTING", "").lower() in ("true", "1", "yes")

    if not is_testing:
        from app.services.pg_notify import PgNotifyListener

        pg_listener = PgNotifyListener(settings.database.database_url)
        await pg_listener.start()
        print("✅ PgNotifyListener started (real-time WebSocket notifications enabled)")
    else:
        print("⚠️  PgNotifyListener skipped (TESTING mode)")

    retention_sweeper = None
    if not is_testing:
        from app.services.retention import RetentionSweeper

        retention_sweeper = RetentionSweeper()
        await retention_sweeper.start()
        print("✅ RetentionSweeper started (daily GDPR retention sweep)")
    else:
        print("⚠️  RetentionSweeper skipped (TESTING mode)")

    # Not started in tests, which call start_due_runs_once with a moved clock instead.
    auto_starter = None
    if not is_testing:
        from app.services.run_start import AutoStarter

        auto_starter = AutoStarter()
        await auto_starter.start()
        print("✅ AutoStarter started (planned runs start at their time)")

    yield

    # Stop the background services before the database closes.
    if auto_starter is not None:
        await auto_starter.stop()
        print("✅ AutoStarter stopped")
    if retention_sweeper is not None:
        await retention_sweeper.stop()
        print("✅ RetentionSweeper stopped")
    if pg_listener is not None:
        await pg_listener.stop()
        print("✅ PgNotifyListener stopped")

    await dispose_db()
    print("✅ Database connection closed")


async def add_security_headers(request: Request, call_next: Any) -> Response:
    """
    Middleware to add security headers to all responses.

    Headers added:
    - Content-Security-Policy (CSP)
    - X-Content-Type-Options
    - X-Frame-Options
    - Referrer-Policy
    """
    response = await call_next(request)

    # This header covers what the API itself serves as a page: /api/docs and
    # /api/redoc. The app's own pages get their policy from the frontend
    # server (src/frontend/nginx.conf). No directive names a foreign host, so
    # a page of the API cannot make a browser contact a third party.
    # A route that answered with its own Content-Security-Policy keeps it. The
    # file endpoint sets a no-script policy for uploaded objects.
    if "Content-Security-Policy" not in response.headers:
        csp_directives = [
            "default-src 'self'",
            "script-src 'self' 'unsafe-inline'",  # 'unsafe-inline' needed for React dev
            "style-src 'self' 'unsafe-inline'",
            "font-src 'self'",
            "img-src 'self' data: blob:",
            "connect-src 'self' ws: wss:",  # ws: and wss: for WebSocket connections
            "frame-ancestors 'none'",
            "base-uri 'self'",
            "form-action 'self'",
        ]
        response.headers["Content-Security-Policy"] = "; ".join(csp_directives)

    response.headers["X-Content-Type-Options"] = "nosniff"

    response.headers["X-Frame-Options"] = "DENY"

    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"

    return response  # type: ignore[no-any-return]  # call_next returns Any


async def _multipart_too_large_handler(_request: Request, _exc: Exception) -> JSONResponse:
    """Map a Starlette ``MultiPartException`` (an upload part exceeding the raised
    ``MAX_MULTIPART_PART_SIZE`` cap, or otherwise malformed multipart) to a clean
    413 instead of an unhandled 500. Parts within the cap but over MAX_FILE_SIZE
    are caught by the upload endpoints' own size check."""
    return JSONResponse(
        status_code=status.HTTP_413_CONTENT_TOO_LARGE,
        content={"detail": "Uploaded file is too large."},
    )


async def _rich_text_too_long_handler(_request: Request, exc: Exception) -> JSONResponse:
    """Answer 422 for a rich text that is too long, in the shape of a validation error of the body.

    The write filter raises it for a text that is too long as it would be stored: cleaning can make a text
    longer than it came in. ``loc`` names the field, and for a text in several languages the language, so
    the client marks the field like any other validation error.
    """
    if not isinstance(exc, RichTextTooLongError):  # pragma: no cover - defensive
        raise exc
    message = f"String should have at most {exc.max_length} characters"
    if exc.cleaned:
        message += " after cleaning. Special characters and links take more room in the stored text"
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        content={
            "detail": [
                {
                    "type": "string_too_long",
                    "loc": ["body", *exc.loc],
                    "msg": message,
                    "ctx": {"max_length": exc.max_length},
                }
            ]
        },
    )


async def _coded_http_exception_handler(_request: Request, exc: Exception) -> JSONResponse:
    """Serialize a ``CodedHTTPException`` into the documented ``ErrorResponse``
    shape (top-level ``error_code`` next to ``detail``) so clients can branch on
    the code instead of string-matching the message."""
    # Narrow explicitly rather than with `assert`: assertions are stripped under
    # `python -O`, which would turn a mis-registered handler into an AttributeError
    # on `exc.error_code` instead of a clean re-raise.
    if not isinstance(exc, CodedHTTPException):  # pragma: no cover - defensive
        raise exc
    return JSONResponse(
        status_code=exc.status_code,
        content={**exc.extra, "detail": exc.detail, "error_code": exc.error_code},
        headers=exc.headers,
    )


def create_app() -> FastAPI:
    """Create the FastAPI application: lifespan, middleware, exception handlers and routers."""
    app = FastAPI(
        title="Clavis API",
        description="DIWA 4.0 - Mobile-first city rallye platform",
        version=__version__,
        lifespan=lifespan,
        # The two documentation pages come from app/api_docs.py, which serves
        # their files from this server instead of a CDN.
        docs_url=None,
        redoc_url=None,
        openapi_url="/api/openapi.json",
    )
    install_api_docs(app, docs_url="/api/docs", redoc_url="/api/redoc")

    from app.config import settings

    allowed_origins = settings.cors.get_origins_list()

    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID"],
    )

    # Binds request_id to the structlog contextvars. It must be registered
    # before every other middleware that may log.
    app.middleware("http")(request_context_middleware)

    app.middleware("http")(add_security_headers)

    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)  # type: ignore[arg-type]
    app.add_exception_handler(MultiPartException, _multipart_too_large_handler)
    app.add_exception_handler(CodedHTTPException, _coded_http_exception_handler)
    app.add_exception_handler(RichTextTooLongError, _rich_text_too_long_handler)

    from app.routers import (
        admin,
        analytics,
        analytics_compare,
        auth,
        challenges,
        chat,
        feed,
        feedback_router,
        files,
        geocode,
        guest,
        health,
        instance,
        instance_auth,
        invitations,
        me,
        media,
        organizations,
        public,
        qa_feedback,
        rallyes,
        runs,
        scores,
        stations,
        submissions,
        team_progress,
        teams,
        two_factor,
        users,
        ws,
    )

    app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
    app.include_router(two_factor.router, prefix="/api/auth/2fa", tags=["2fa"])

    # Instance-operator auth (system-level, above all organizations).
    # No route creates an operator; accounts exist only through the CLI.
    app.include_router(instance_auth.router, prefix="/api/instance/auth", tags=["instance-auth"])
    # Instance-organization management (list, create, archive, restore).
    app.include_router(instance.router, prefix="/api/instance/organizations", tags=["instance-organizations"])
    # Operator endpoints that are not organization-scoped, e.g. the audit log.
    app.include_router(instance.operator_router, prefix="/api/instance", tags=["instance-operator"])
    app.include_router(geocode.router, prefix="/api/geocode", tags=["geocode"])
    app.include_router(health.router, prefix="/api", tags=["health"])
    app.include_router(me.router, prefix="/api/me", tags=["me"])

    # Guest join endpoint (system-level, no auth required)
    app.include_router(guest.router, prefix="/api/join", tags=["guest"])

    # Public read-only endpoints (system-level, no auth required)
    app.include_router(public.router, prefix="/api/public", tags=["public"])

    # Organizations - system-level create endpoint
    app.include_router(organizations.router, prefix="/api/organizations", tags=["organizations"])
    # Organizations - tenant-scoped endpoints (GET/PUT/DELETE)
    app.include_router(organizations.slug_router, prefix="/api/orgs/{slug}", tags=["organizations"])

    # Domain model routers (tenant-scoped)
    # Stations and challenges are an org-wide LIBRARY composed into rallyes by
    # reference. The rallyes router also serves the rallye<->station composition
    # endpoints; the stations router serves the station<->challenge composition.
    app.include_router(rallyes.router, prefix="/api/orgs/{slug}/rallyes", tags=["rallyes"])
    app.include_router(stations.router, prefix="/api/orgs/{slug}/stations", tags=["stations"])
    app.include_router(challenges.router, prefix="/api/orgs/{slug}/challenges", tags=["challenges"])

    # User management router (tenant-scoped)
    app.include_router(users.router, prefix="/api/orgs/{slug}/users", tags=["users"])

    # Invitations — admin side is tenant-scoped, the accept side cannot be:
    # the invitee has no account and no org context until they redeem the token.
    app.include_router(invitations.admin_router, prefix="/api/orgs/{slug}/invitations", tags=["invitations"])
    app.include_router(invitations.public_router, prefix="/api/invitations", tags=["invitations"])

    # Admin router (tenant-scoped, org_admin only)
    app.include_router(admin.router, prefix="/api/orgs/{slug}/admin", tags=["admin"])

    # Runtime layer routers (tenant-scoped)
    app.include_router(runs.router, prefix="/api/orgs/{slug}/runs", tags=["runs"])
    app.include_router(team_progress.router, prefix="/api/orgs/{slug}/runs", tags=["runs"])
    app.include_router(teams.router, prefix="/api/orgs/{slug}/runs/{run_id}/teams", tags=["teams"])
    app.include_router(submissions.router, prefix="/api/orgs/{slug}/runs/{run_id}/submissions", tags=["submissions"])
    app.include_router(files.router, prefix="/api/orgs/{slug}/runs/{run_id}/submissions/upload", tags=["files"])
    app.include_router(files.serve_router, prefix="/api/files", tags=["files"])
    app.include_router(media.router, prefix="/api/orgs/{slug}/media", tags=["media"])
    app.include_router(scores.router, prefix="/api/orgs/{slug}/runs/{run_id}/scores", tags=["scores"])
    app.include_router(feed.router, prefix="/api/orgs/{slug}/runs/{run_id}/feed", tags=["feed"])
    app.include_router(chat.router, prefix="/api/orgs/{slug}/runs/{run_id}/teams/{team_id}/chat", tags=["chat"])
    app.include_router(feedback_router.router, prefix="/api/orgs/{slug}/runs/{run_id}/feedback", tags=["feedback"])
    app.include_router(analytics.router, prefix="/api/orgs/{slug}/runs/{run_id}/analytics", tags=["analytics"])
    app.include_router(analytics.timeseries_router, prefix="/api/orgs/{slug}/analytics", tags=["analytics"])
    app.include_router(analytics_compare.compare_router, prefix="/api/orgs/{slug}/analytics", tags=["analytics"])

    # The feedback module for test sessions. Always mounted, the router's
    # own dependency answers 404 while QA_FEEDBACK_ENABLED is off.
    app.include_router(qa_feedback.router, prefix="/api/qa", tags=["qa-feedback"])

    # WebSocket endpoints (include without prefix - router defines full paths)
    app.include_router(ws.router, tags=["websocket"])

    @app.get("/", include_in_schema=False)
    async def root() -> JSONResponse:
        """Root endpoint - basic API connectivity check."""
        return JSONResponse(
            content={
                "message": "Clavis API",
                "version": __version__,
                "docs": "/api/docs",
            }
        )

    return app


app = create_app()
