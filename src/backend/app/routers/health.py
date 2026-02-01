"""
Health check endpoint.

Provides system health status including database and storage connectivity checks.
"""

from typing import Any

from fastapi import APIRouter, Response, status
from sqlalchemy import text

from app.database import get_engine

router = APIRouter()


@router.get("/health")
async def health_check(response: Response) -> dict[str, Any]:
    """Report whether the database and the object storage answer.

    Open without sign-in. The route runs a trivial query on the database and
    checks that the storage bucket exists. The response gives `status` and the
    state of `database` and `storage`. It answers 200 when both are connected
    and 503 with status `degraded` when one is not.
    """
    health_status = {
        "status": "ok",
        "database": "unknown",
        "storage": "unknown",
    }

    try:
        engine = get_engine()
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        health_status["database"] = "connected"
    except Exception as e:
        health_status["database"] = f"error: {str(e)}"
        health_status["status"] = "degraded"

    try:
        from app.config import settings
        from app.storage import get_minio_client

        client = get_minio_client()
        if client.bucket_exists(settings.storage.bucket):
            health_status["storage"] = "connected"
        else:
            health_status["storage"] = "bucket_missing"
            health_status["status"] = "degraded"
    except Exception as e:
        health_status["storage"] = f"error: {str(e)}"
        health_status["status"] = "degraded"

    if health_status["database"] != "connected" or health_status["storage"] != "connected":
        health_status["status"] = "degraded"
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return health_status
