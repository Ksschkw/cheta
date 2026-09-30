"""The memory inspection use case: what is stored, what conflicts, what to export."""

from __future__ import annotations

from datetime import UTC, datetime

from core.config import Settings
from core.errors import NotFoundError
from core.protocols import MemoryGatewayProtocol
from crud.contradiction_crud import ContradictionCrud
from crud.memory_crud import MemoryCrud
from crud.turn_crud import TurnCrud
from crud.user_crud import UserCrud
from models.entities.memory_model import (
    STATUS_ACTIVE,
    STATUS_CONTRADICTED,
    STATUS_SUPERSEDED,
)
from schemas.memory_schema import MemoryStatsSchema, MemoryViewSchema


class MemoryAdminService:
    def __init__(
        self,
        users: UserCrud,
        memories: MemoryCrud,
        turns: TurnCrud,
        contradictions: ContradictionCrud,
        memory_gateway: MemoryGatewayProtocol,
        settings: Settings,
    ) -> None:
        self._users = users
        self._memories = memories
        self._turns = turns
        self._contradictions = contradictions
        self._memory = memory_gateway
        self._settings = settings

    def _view(self, memory) -> MemoryViewSchema:
        return MemoryViewSchema(
            blob_id=memory.blob_id,
            text=memory.text,
            status=memory.status,
            importance=memory.importance,
            origin_surface=memory.origin_surface,
            superseded_by=memory.superseded_by,
            occurred_at=memory.occurred_at,
        )

    def list_memories(self, user_id: str, include_inactive: bool = False) -> list[MemoryViewSchema]:
        user = self._users.get_by_id(user_id)
        if user is None:
            raise NotFoundError(f"user {user_id} does not exist")
        records = self._memories.list_for_user(user_id, None, 500)
        if not include_inactive:
            records = [record for record in records if record.status == STATUS_ACTIVE]
        return [self._view(record) for record in records]

    async def stats(self, user_id: str) -> MemoryStatsSchema:
        user = self._users.get_by_id(user_id)
        if user is None:
            raise NotFoundError(f"user {user_id} does not exist")

        namespace = self._settings.memory_namespace(user.memory_key)
        listing = await self._memory.list_namespaces(limit=200)
        stored_on_walrus = 0
        storage_bytes = 0
        for summary in listing.namespaces:
            if summary.name == namespace:
                stored_on_walrus = summary.memory_count
                storage_bytes = summary.storage_used
                break

        return MemoryStatsSchema(
            user_id=user_id,
            display_name=user.display_name,
            surface=user.surface,
            namespace=namespace,
            active=self._memories.count_for_user(user_id, STATUS_ACTIVE),
            superseded=self._memories.count_for_user(user_id, STATUS_SUPERSEDED),
            contradicted=self._memories.count_for_user(user_id, STATUS_CONTRADICTED),
            open_contradictions=len(self._contradictions.list_open_for_user(user_id)),
            turns=self._turns.count_for_user(user_id),
            relayer_memory_count=stored_on_walrus,
            relayer_storage_bytes=storage_bytes,
            relayer_degraded=listing.degraded,
        )

    def open_contradictions(self, user_id: str) -> list[dict[str, str]]:
        user = self._users.get_by_id(user_id)
        if user is None:
            raise NotFoundError(f"user {user_id} does not exist")
        views: list[dict[str, str]] = []
        for contradiction in self._contradictions.list_open_for_user(user_id):
            left = self._memories.get_by_blob_id(contradiction.left_blob_id)
            right = self._memories.get_by_blob_id(contradiction.right_blob_id)
            views.append(
                {
                    "id": contradiction.id,
                    "reason": contradiction.reason,
                    "created_at": contradiction.created_at,
                    "left": left.text if left else contradiction.left_blob_id,
                    "right": right.text if right else contradiction.right_blob_id,
                }
            )
        return views

    def resolve_contradiction(self, contradiction_id: str) -> dict[str, str]:
        resolved = self._contradictions.resolve(contradiction_id)
        if resolved is None:
            raise NotFoundError(f"contradiction {contradiction_id} is not open")
        return {"id": resolved.id, "status": resolved.status, "resolved_at": resolved.resolved_at or ""}

    def export_passport(self, user_id: str) -> dict[str, object]:
        """A portable bundle of everything known about one user.

        The index is data, not application state: it travels with the memories.
        """
        user = self._users.get_by_id(user_id)
        if user is None:
            raise NotFoundError(f"user {user_id} does not exist")
        records = self._memories.list_for_user(user_id, None, 1000)
        return {
            "format": "ranti.memory-passport.v1",
            "exported_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "user": {
                "display_name": user.display_name,
                "surface": user.surface,
                "surface_user_id": user.surface_user_id,
            },
            "namespace": self._settings.memory_namespace(user.memory_key),
            "memories": [
                {
                    "blob_id": record.blob_id,
                    "text": record.text,
                    "status": record.status,
                    "importance": record.importance,
                    "origin_surface": record.origin_surface,
                    "superseded_by": record.superseded_by,
                    "occurred_at": record.occurred_at,
                }
                for record in records
            ],
        }
