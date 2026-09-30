"""Application entry point: builds the container, the app, and the route table."""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from core.config import Settings
from core.container import Container, build_container
from core.errors import RantiError
from routers import health_router, user_router

logging.basicConfig(
    level=logging.INFO,
    format='{"level":"%(levelname)s","logger":"%(name)s","message":"%(message)s"}',
)


def create_app(container: Container | None = None, settings: Settings | None = None) -> FastAPI:
    resolved = container or build_container(settings)

    app = FastAPI(
        title="Ranti",
        description="One mind, every app. Portable Walrus Memory that consolidates instead of rot.",
        version="0.1.0",
    )
    app.state.container = resolved

    @app.exception_handler(RantiError)
    async def _domain_error(_: Request, exc: RantiError) -> JSONResponse:
        status = {
            "not_found": 404,
            "conflict": 409,
            "validation_error": 422,
            "configuration_error": 500,
        }.get(exc.code, 503)
        # Only the typed code and a safe message cross the boundary.
        return JSONResponse(status_code=status, content={"error": exc.code, "detail": exc.message})

    app.include_router(health_router.build_router())
    app.include_router(user_router.build_router())
    return app


app = create_app()
