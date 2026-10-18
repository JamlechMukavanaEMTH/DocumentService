"""FastAPI application factory and lifespan management."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from arq import create_pool
from arq.connections import RedisSettings
from fastapi import FastAPI

# Import all models so SQLAlchemy mappers are configured before routes are used.
from app.api.v1.api import api_router
from app.core.config import settings


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Startup
    redis = None
    try:
        redis = await create_pool(RedisSettings.from_dsn(settings.REDIS_URL))
    except Exception as exc:  # noqa: BLE001
        # Redis may not be available yet (e.g. local dev without Docker).
        # Defer failure until a job actually needs enqueuing.
        import logging

        logging.getLogger("docservice").warning(
            "Redis unavailable at startup: %s", exc
        )
    app.state.redis = redis
    yield
    # Shutdown
    if redis is not None:
        await redis.close()


def create_app() -> FastAPI:
    app = FastAPI(
        title="Document Service",
        description="Enterprise document service with OCR, storage, bulk upload, and compression.",
        version="0.1.0",
        lifespan=lifespan,
    )

    app.include_router(api_router, prefix="/api/v1")

    return app


application = create_app()
