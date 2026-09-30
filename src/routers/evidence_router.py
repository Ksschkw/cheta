"""Evidence routes. The submission needs proof of real use, not a claim."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from core.container import Container, get_container
from schemas.memory_schema import MemoryStatsSchema


def build_router() -> APIRouter:
    router = APIRouter(prefix="/evidence", tags=["evidence"])

    @router.get("/users", response_model=list[MemoryStatsSchema])
    async def users(container: Container = Depends(get_container)) -> list[MemoryStatsSchema]:
        return await container.memory_admin_service.evidence_leaderboard()

    return router
