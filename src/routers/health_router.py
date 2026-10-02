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
                # Every boundary call, which is what the relayer's per-minute
                # limit actually counts. A turn that spends several is the turn
                # that trips a 429.
                "requests": container.memory_gateway.request_count,
            },
            # A measured per-turn latency budget rather than a mystery: the last
            # observed turn, the median, p95 and the worst, in milliseconds.
            "performance": container.conversation_service.performance_report(),
            "llm": {
                "providers": (
                    list(container.llm_gateway.provider_names)
                    if container.llm_gateway is not None
                    else []
                ),
            },
            "telegram": {"configured": container.settings.telegram_configured},
        }

    return router
