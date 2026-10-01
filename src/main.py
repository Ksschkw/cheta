"""Application entry point: builds the container, the app, and the route table."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from core.config import Settings
from core.container import Container, build_container
from core.errors import RantiError
from core.keepalive import KeepAlivePinger
from routers import (
    chat_router,
    evidence_router,
    health_router,
    memory_router,
    telegram_router,
    user_router,
)

WEB_ROOT = Path(__file__).resolve().parent / "web"

logging.basicConfig(
    level=logging.INFO,
    format='{"level":"%(levelname)s","logger":"%(name)s","message":"%(message)s"}',
)


def create_app(container: Container | None = None, settings: Settings | None = None) -> FastAPI:
    resolved = container or build_container(settings)

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        pinger = getattr(application.state, "keepalive", None)
        if pinger is not None:
            pinger.start()
        try:
            yield
        finally:
            if pinger is not None:
                await pinger.stop()

    app = FastAPI(
        lifespan=lifespan,
        title="Ranti",
        description="One mind, every app. Portable Walrus Memory that consolidates instead of rot.",
        version="0.1.0",
    )
    app.state.container = resolved
    # create_app accepts either a Container or a Settings.
    target = resolved.settings.keepalive_target
    app.state.keepalive = KeepAlivePinger(target) if target else None

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
    app.include_router(chat_router.build_router())
    app.include_router(memory_router.build_router())
    app.include_router(evidence_router.build_router())
    app.include_router(telegram_router.build_router())

    if WEB_ROOT.is_dir():
        app.mount("/app", StaticFiles(directory=WEB_ROOT, html=True), name="app")

    return app


app = create_app()
