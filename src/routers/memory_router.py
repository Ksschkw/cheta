"""Memory inspection routes: what is stored, what conflicts, what can travel."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from core.container import Container, get_container
from schemas.memory_schema import (
    ContradictionViewSchema,
    MemoryStatsSchema,
    MemoryViewSchema,
    PassportImportResultSchema,
)


def build_router() -> APIRouter:
    router = APIRouter(prefix="/memories", tags=["memories"])

    @router.get("/{user_id}", response_model=list[MemoryViewSchema])
    def list_memories(
        user_id: str,
        include_inactive: bool = False,
        container: Container = Depends(get_container),
    ) -> list[MemoryViewSchema]:
        return container.memory_admin_service.list_memories(user_id, include_inactive)

    @router.get("/{user_id}/stats", response_model=MemoryStatsSchema)
    async def stats(
        user_id: str, container: Container = Depends(get_container)
    ) -> MemoryStatsSchema:
        return await container.memory_admin_service.stats(user_id)

    @router.get("/{user_id}/contradictions", response_model=list[ContradictionViewSchema])
    def contradictions(
        user_id: str, container: Container = Depends(get_container)
    ) -> list[ContradictionViewSchema]:
        return [
            ContradictionViewSchema(**view)
            for view in container.memory_admin_service.open_contradictions(user_id)
        ]

    @router.get("/{user_id}/passport")
    def export_passport(user_id: str, container: Container = Depends(get_container)) -> dict:
        return container.memory_admin_service.export_passport(user_id)

    @router.post("/{user_id}/rebuild-index")
    async def rebuild_index(
        user_id: str, container: Container = Depends(get_container)
    ) -> dict:
        return await container.memory_admin_service.rebuild_index(user_id)

    @router.post("/passport/import", response_model=PassportImportResultSchema)
    async def import_passport(
        payload: dict, container: Container = Depends(get_container)
    ) -> PassportImportResultSchema:
        result = await container.memory_admin_service.import_passport(payload)
        return PassportImportResultSchema(**result)

    @router.post("/contradictions/{contradiction_id}/resolve")
    def resolve_contradiction(
        contradiction_id: str, container: Container = Depends(get_container)
    ) -> dict:
        return container.memory_admin_service.resolve_contradiction(contradiction_id)

    return router
