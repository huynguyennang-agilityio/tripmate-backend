"""The API process (FastAPI on Uvicorn): `python -m app.main` serves on PORT."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI

from app.platform import health, whoami
from app.platform.access import install_access_contract, install_access_keys, install_device_id
from app.platform.config import Settings, load_settings, load_settings_or_exit
from app.platform.db import create_engine, install_sessions
from app.platform.errors import install_error_handlers, install_openapi, install_request_id
from app.platform.modules import include_routes, scan_modules
from app.platform.redis_client import create_redis


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the app from the settings (read from the environment when not given).

    Scans `app/` for modules on every call. Neither PostgreSQL nor Redis is
    reached here or at start-up, so the app starts when either is down.
    """
    settings = settings or load_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # The only place the secret addresses are unwrapped.
        engine = create_engine(settings.database_url.get_secret_value())
        redis = create_redis(settings.redis_url.get_secret_value())
        app.state.resources = health.Resources(settings.release_version, engine, redis)
        install_sessions(app, engine)
        try:
            yield
        finally:
            await redis.aclose()
            await engine.dispose()

    app = FastAPI(title="TripMate", version=settings.release_version, lifespan=lifespan)
    install_error_handlers(app)
    install_openapi(app)
    install_access_contract(app)
    app.state.settings = settings
    install_access_keys(app, settings.access_token_public_keys)
    app.include_router(health.router)
    app.include_router(whoami.router)
    registry = scan_modules()
    app.state.registry = registry
    include_routes(app, registry)
    # Inside the request ID middleware, which answers its `AppError`.
    install_device_id(app)
    # Added last, so it is the outermost middleware (see RequestIdMiddleware).
    install_request_id(app)
    return app


def main() -> None:
    settings = load_settings_or_exit()
    uvicorn.run(create_app(settings), host="0.0.0.0", port=settings.port)  # noqa: S104


if __name__ == "__main__":
    main()
