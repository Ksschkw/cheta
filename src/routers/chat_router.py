"""Conversation routes. Parse, call one service, shape the response."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from core.container import Container, get_container
from schemas.memory_schema import RecallRequestSchema
from schemas.turn_schema import (
    CounterfactualSchema,
    RecalledMemoryView,
    TurnRequestSchema,
    TurnSchema,
)


def build_router() -> APIRouter:
    router = APIRouter(prefix="/chat", tags=["chat"])

    @router.post("/turn", response_model=TurnSchema)
    async def take_turn(
        payload: TurnRequestSchema, container: Container = Depends(get_container)
    ) -> TurnSchema:
        return await container.conversation_service.handle_turn(
            surface=payload.surface,
            surface_user_id=payload.surface_user_id,
            display_name=payload.display_name,
            text=payload.text,
            memory_enabled=payload.memory_enabled,
        )

    @router.post("/recall", response_model=list[RecalledMemoryView])
    async def recall(
        payload: RecallRequestSchema, container: Container = Depends(get_container)
    ) -> list[RecalledMemoryView]:
        _, recalled, _, _ = await container.conversation_service.recall_context(
            user_id=payload.user_id, query=payload.query, budget=payload.budget
        )
        return recalled

    @router.post("/counterfactual/{turn_id}", response_model=CounterfactualSchema)
    async def counterfactual(
        turn_id: str, container: Container = Depends(get_container)
    ) -> CounterfactualSchema:
        return await container.conversation_service.replay_without_memory(turn_id)

    return router
