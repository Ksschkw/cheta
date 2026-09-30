"""Health routes. Report liveness and dependency readiness without leaking internals."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from core.container import Container, get_container


def build_router() -> APIRouter:
    router = APIRouter(tags=["health"])

    @router.get("/health")
    def health(container: Container = Depends(get_container)) -> dict[str, object]:
        return {
            "status": "ok",
            "environment": container.settings.environment,
            "memory": {
                "mode": "walrus" if container.settings.memwal_configured else "mock",
                "degraded": container.memory_gateway.degraded,
            },
            "llm": {
                "providers": [provider.name for provider in container.settings.llm_providers],
            },
            "telegram": {"configured": container.settings.telegram_configured},
        }

    return router
