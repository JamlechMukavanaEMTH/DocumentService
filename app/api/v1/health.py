"""Health check endpoints."""

from typing import Any

from fastapi import APIRouter
from sqlalchemy import text

from app.api.deps import AsyncSessionLocal
from app.core.config import settings

router = APIRouter()


@router.get("")
async def health() -> dict[str, Any]:
    """Overall health check."""
    return {"status": "ok", "service": "document-service"}


@router.get("/ready")
async def ready() -> dict[str, Any]:
    """Readiness check - verifies DB and Redis connectivity."""
    issues = []

    # DB check
    try:
        async with AsyncSessionLocal() as session:
            await session.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001
        issues.append(f"db: {exc}")

    # Redis check
    try:
        import redis

        r = redis.Redis.from_url(settings.REDIS_URL)
        r.ping()
    except Exception as exc:  # noqa: BLE001
        issues.append(f"redis: {exc}")

    if issues:
        return {"status": "degraded", "issues": issues}

    return {"status": "ready"}
