"""Conversation routes. Parse, call one service, shape the response."""

from __future__ import annotations

import base64

from fastapi import APIRouter, Depends

from core.container import Container, get_container
from schemas.memory_schema import RecallRequestSchema
from schemas.page_tool_schema import PagePlanRequestSchema, PagePlanSchema
from schemas.turn_schema import (
    CounterfactualSchema,
    DocumentUploadSchema,
    RecalledMemoryView,
    TurnRequestSchema,
    TurnSchema,
)


def _decode_document_content(
    container: Container, filename: str, content_base64: str
) -> str:
    try:
        raw_bytes = base64.b64decode(content_base64)
    except Exception:
        return ""
    extracted_text = ""
    parser = container.conversation_service._attachment_parser
    if parser is not None:
        kind = parser.classify(filename, "")
        if kind:
            try:
                extracted_text = parser.extract(kind, filename, raw_bytes)
            except Exception:
                extracted_text = ""
    if not extracted_text:
        extracted_text = raw_bytes.decode("utf-8", errors="replace")[:12000]
    return extracted_text[:12000]


def build_router() -> APIRouter:
    router = APIRouter(prefix="/chat", tags=["chat"])

    @router.get("/tools")
    async def list_tools(
        container: Container = Depends(get_container),
    ) -> list[dict[str, object]]:
        """List all registered agentic capabilities and their schemas."""
        tools = container.conversation_service._tools
        if tools is None:
            return []
        items = []
        for defn in tools.definitions():
            spec = tools.get(defn.name)
            items.append(
                {
                    "name": defn.name,
                    "description": defn.description,
                    "parameters": defn.parameters,
                    "timeout_seconds": spec.timeout_seconds if spec else 10.0,
                }
            )
        return items

    @router.post("/turn", response_model=TurnSchema)
    async def take_turn(
        payload: TurnRequestSchema, container: Container = Depends(get_container)
    ) -> TurnSchema:
        doc_text = payload.document_text
        if not doc_text and payload.document_base64:
            fname = payload.document_name or "document.txt"
            doc_text = _decode_document_content(container, fname, payload.document_base64)

        return await container.conversation_service.handle_turn(
            surface=payload.surface,
            surface_user_id=payload.surface_user_id,
            display_name=payload.display_name,
            text=payload.text,
            memory_enabled=payload.memory_enabled,
            document_text=doc_text,
            recall_query=payload.recall_query,
        )

    @router.post("/upload", response_model=TurnSchema)
    async def upload_document(
        payload: DocumentUploadSchema,
        container: Container = Depends(get_container),
    ) -> TurnSchema:
        """Upload a document in JSON, parse locally, and run a turn with document_question."""
        extracted_text = _decode_document_content(
            container, payload.filename, payload.content_base64
        )
        user_query = (
            payload.question.strip()
            or f"Please read the attached {payload.filename} and summarize its key points."
        )
        return await container.conversation_service.handle_turn(
            surface=payload.surface,
            surface_user_id=payload.surface_user_id,
            display_name=payload.display_name,
            text=user_query,
            memory_enabled=payload.memory_enabled,
            document_text=extracted_text,
        )

    @router.post("/recall", response_model=list[RecalledMemoryView])
    async def recall(
        payload: RecallRequestSchema, container: Container = Depends(get_container)
    ) -> list[RecalledMemoryView]:
        _, recalled, _, _ = await container.conversation_service.recall_context(
            user_id=payload.user_id, query=payload.query, budget=payload.budget
        )
        return recalled

    @router.post("/page-plan", response_model=PagePlanSchema)
    async def plan_page_tool(
        payload: PagePlanRequestSchema, container: Container = Depends(get_container)
    ) -> PagePlanSchema:
        """Decide which page tool, if any, a plain request is asking for."""
        return await container.conversation_service.plan_page_tool(
            payload.request, payload.tools
        )

    @router.post("/counterfactual/{turn_id}", response_model=CounterfactualSchema)
    async def counterfactual(
        turn_id: str, container: Container = Depends(get_container)
    ) -> CounterfactualSchema:
        return await container.conversation_service.replay_without_memory(turn_id)

    return router
