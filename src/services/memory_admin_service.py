"""The memory inspection use case: what is stored, what conflicts, what to export."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from core.config import Settings
from core.errors import NotFoundError, ValidationError
from core.protocols import MemoryGatewayProtocol
from crud.contradiction_crud import ContradictionCrud
from crud.memory_crud import MemoryCrud
from crud.turn_crud import TurnCrud
from crud.user_crud import UserCrud
from models.entities.memory_index_model import (
    INDEX_QUERY,
    IndexedMemoryRecord,
    decode_snapshot,
)
from models.entities.memory_model import (
    STATUS_ACTIVE,
    STATUS_CONTRADICTED,
    STATUS_SUPERSEDED,
)
from models.entities.memory_passport_model import build_passport
from models.entities.memory_phrasing_model import (
    is_person_fact,
    record_facing,
    subject_names,
)
from models.entities.memory_repair_model import (
    KIND_CONTRADICTION,
    KIND_DUPLICATE,
    RepairAction,
    plan_repairs,
)
from schemas.memory_schema import MemoryStatsSchema, MemoryViewSchema

logger = logging.getLogger("ranti.service.memory_admin")


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

    def _view(self, memory, display_names: tuple[str, ...]) -> MemoryViewSchema:
        return MemoryViewSchema(
            blob_id=memory.blob_id,
            text=record_facing(memory, display_names),
            status=memory.status,
            importance=memory.importance,
            origin_surface=memory.origin_surface,
            superseded_by=memory.superseded_by,
            occurred_at=memory.occurred_at,
        )

    def _names_for(self, user) -> tuple[str, ...]:
        """The name forms every surface uses to resolve a record's subject."""
        return subject_names(user.display_name)

    def _scope_user_ids(self, user_id: str) -> list[str]:
        """Every local identity whose rows belong to this person's memory space.

        A shared handle is one Walrus namespace, but the local index rows are
        stored per surface identity. Reading only the caller's rows is why a
        freshly paired client listed three notes while the originator listed
        forty-eight; every listing, repair, stats and passport read now covers
        the whole shared space.
        """
        user = self._users.get_by_id(user_id)
        if user is None or user.memory_handle is None:
            return [user_id]
        ids = [member.id for member in self._users.list_by_memory_handle(user.memory_handle)]
        if user_id not in ids:
            ids.append(user_id)
        return ids

    def _scope_records(
        self, user_id: str, status: str | None = None, limit: int = 1000
    ) -> list:
        return self._memories.list_for_scope(self._scope_user_ids(user_id), status, limit)

    def list_memories(self, user_id: str, include_inactive: bool = False) -> list[MemoryViewSchema]:
        user = self._users.get_by_id(user_id)
        if user is None:
            raise NotFoundError(f"user {user_id} does not exist")
        # The listing is where bad state is noticed, so it is where it is
        # collapsed: exact duplicates, self-contradictions and facts about the
        # assistant are retired in the local index before the cards render.
        self.repair_memories(user_id)
        records = self._scope_records(user_id, None, 500)
        if not include_inactive:
            records = [record for record in records if record.status == STATUS_ACTIVE]
        # A record about the assistant is not part of what is known about the
        # person, and every record is rendered as it is said to them.
        display_names = self._names_for(user)
        return [
            self._view(record, display_names)
            for record in records
            if is_person_fact(record.text)
        ]

    def repair_memories(self, user_id: str) -> list[RepairAction]:
        """Collapse exact duplicates and self-contradictions for one person.

        Returns the actions actually applied, so a caller can report exactly
        what was collapsed. Append-only Walrus storage is untouched; only the
        local index that drives recall and the listing changes.
        """
        user = self._users.get_by_id(user_id)
        if user is None:
            raise NotFoundError(f"user {user_id} does not exist")
        records = self._scope_records(user_id, None, 1000)
        actions = plan_repairs(records, self._names_for(user))
        for action in actions:
            if action.kind == KIND_DUPLICATE:
                self._memories.mark_status(
                    action.retired_id, STATUS_SUPERSEDED, action.kept_blob_id or "duplicate"
                )
            elif action.kind == KIND_CONTRADICTION:
                self._memories.mark_status(action.retired_id, STATUS_CONTRADICTED, None)
            else:
                self._memories.mark_status(action.retired_id, STATUS_SUPERSEDED, "not-about-you")
            logger.info(
                "memory listing repair retired an active record",
                extra={
                    "event": "memory_repair",
                    "kind": action.kind,
                    "retired_blob_id": action.retired_blob_id,
                    "kept_blob_id": action.kept_blob_id,
                    "reason": action.reason,
                },
            )
        return actions

    def retire_memory(self, user_id: str, blob_id: str) -> dict[str, object]:
        """Retire one note from this person's listing, by its blob id."""
        user = self._users.get_by_id(user_id)
        if user is None:
            raise NotFoundError(f"user {user_id} does not exist")
        record = self._memories.get_by_blob_id(blob_id)
        if record is None or record.user_id != user_id:
            raise NotFoundError(f"memory {blob_id} does not exist for this user")
        self._memories.mark_status(record.id, STATUS_SUPERSEDED, "user-retracted")
        return {
            "id": record.id,
            "blob_id": record.blob_id,
            "status": STATUS_SUPERSEDED,
            "text": record_facing(record, self._names_for(user)),
        }

    async def correct_memory(
        self, user_id: str, blob_id: str, text: str
    ) -> dict[str, object]:
        """Replace one note with the person's own wording, append-only.

        The corrected text is written to Walrus Memory and a new active record
        is indexed; the old record is retired and points at the new blob.
        """
        user = self._users.get_by_id(user_id)
        if user is None:
            raise NotFoundError(f"user {user_id} does not exist")
        record = self._memories.get_by_blob_id(blob_id)
        if record is None or record.user_id != user_id:
            raise NotFoundError(f"memory {blob_id} does not exist for this user")
        corrected = " ".join(text.split())
        if not corrected:
            raise ValidationError("a correction must not be blank")
        if not is_person_fact(corrected):
            raise ValidationError("a correction must be a fact about the person")

        namespace = self._settings.memory_namespace(user.memory_key)
        written = await self._memory.remember(corrected, namespace)
        existing = self._memories.get_by_blob_id(written.blob_id)
        if existing is not None:
            # The relayer returned a blob id this index already holds, so reuse
            # that row instead of violating UNIQUE(blob_id). The superseded note
            # is still retired below.
            self._memories.mark_status(record.id, STATUS_SUPERSEDED, written.blob_id)
            return {
                "retired_id": record.id,
                "retired_text": record_facing(record, self._names_for(user)),
                "id": existing.id,
                "blob_id": existing.blob_id,
                "text": corrected,
            }
        created = self._memories.create(
            user_id=user_id,
            blob_id=written.blob_id,
            namespace=namespace,
            text=corrected,
            importance=record.importance,
            origin_surface=user.surface,
            occurred_at=datetime.now(UTC).isoformat(timespec="seconds"),
        )
        self._memories.mark_status(record.id, STATUS_SUPERSEDED, written.blob_id)
        return {
            "retired_id": record.id,
            "retired_text": record_facing(record, self._names_for(user)),
            "id": created.id,
            "blob_id": written.blob_id,
            "text": corrected,
        }

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
            active=self._memories.count_for_scope(self._scope_user_ids(user_id), STATUS_ACTIVE),
            superseded=self._memories.count_for_scope(self._scope_user_ids(user_id), STATUS_SUPERSEDED),
            contradicted=self._memories.count_for_scope(self._scope_user_ids(user_id), STATUS_CONTRADICTED),
            open_contradictions=len(self._contradictions.list_open_for_user(user_id)),
            turns=self._turns.count_for_user(user_id),
            relayer_memory_count=stored_on_walrus,
            relayer_storage_bytes=storage_bytes,
            relayer_degraded=listing.degraded,
        )

    async def rebuild_index(self, user_id: str, max_snapshots: int = 20) -> dict[str, object]:
        """Rebuild the local index from the newest snapshot in the companion space.

        The memory SDK cannot enumerate memories, so recovery cannot walk the
        relayer. Instead the conversation path stores a compact snapshot of the
        index as an ordinary memory; this recalls that fixed marker query,
        decodes every hit, and trusts the highest sequence. A user with no
        snapshot gets an empty report, never an error.
        """
        user = self._users.get_by_id(user_id)
        if user is None:
            raise NotFoundError(f"user {user_id} does not exist")

        index_namespace = self._settings.index_namespace(user.memory_key)
        outcome = await self._memory.recall(INDEX_QUERY, index_namespace, limit=max_snapshots)

        snapshots_scanned = 0
        newest_sequence = -1
        newest_records: list[IndexedMemoryRecord] = []
        for hit in outcome.memories:
            sequence, records = decode_snapshot(hit.text)
            if sequence < 0:
                continue
            snapshots_scanned += 1
            if sequence > newest_sequence:
                newest_sequence = sequence
                newest_records = records

        memory_namespace = self._settings.memory_namespace(user.memory_key)
        known = {
            record.blob_id for record in self._scope_records(user_id, None, 1000)
        }
        records_recovered = 0
        records_already_present = 0
        for record in newest_records:
            if record.blob_id in known:
                records_already_present += 1
                continue
            created = self._memories.create(
                user_id=user_id,
                blob_id=record.blob_id,
                namespace=memory_namespace,
                text=record.text,
                importance=record.importance,
                origin_surface=record.origin_surface,
                occurred_at=record.occurred_at,
            )
            # create() always starts a row active; restore the encoded status
            # and its forwarding pointer when the snapshot says otherwise.
            if record.status != STATUS_ACTIVE or record.superseded_by is not None:
                self._memories.mark_status(created.id, record.status, record.superseded_by)
            known.add(record.blob_id)
            records_recovered += 1

        return {
            "snapshots_scanned": snapshots_scanned,
            "newest_sequence": newest_sequence,
            "records_recovered": records_recovered,
            "records_already_present": records_already_present,
            "namespace": index_namespace,
        }

    def open_contradictions(self, user_id: str) -> list[dict[str, str]]:
        user = self._users.get_by_id(user_id)
        if user is None:
            raise NotFoundError(f"user {user_id} does not exist")
        display_names = self._names_for(user)
        views: list[dict[str, str]] = []
        for contradiction in self._contradictions.list_open_for_user(user_id):
            left = self._memories.get_by_blob_id(contradiction.left_blob_id)
            right = self._memories.get_by_blob_id(contradiction.right_blob_id)
            views.append(
                {
                    "id": contradiction.id,
                    "reason": contradiction.reason,
                    "created_at": contradiction.created_at,
                    "left": record_facing(left, display_names) if left else contradiction.left_blob_id,
                    "right": record_facing(right, display_names) if right else contradiction.right_blob_id,
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

        The format itself lives in the entity so the Telegram export button can
        reuse it without one service importing another.
        """
        user = self._users.get_by_id(user_id)
        if user is None:
            raise NotFoundError(f"user {user_id} does not exist")
        records = self._scope_records(user_id, None, 1000)
        return build_passport(user, records, self._settings.memory_namespace(user.memory_key))

    async def import_passport(self, payload: dict[str, object]) -> dict[str, object]:
        """Bring a memory space to a different surface identity.

        Namespaces are derived per identity, so an import is a genuine rewrite
        into the destination space, not a pointer swap. Duplicate text is
        skipped, which makes the import safe to run twice.
        """
        raw_user = payload.get("user")
        if not isinstance(raw_user, dict):
            raise ValidationError("passport is missing its user block")
        surface = str(raw_user.get("surface", "cli"))
        surface_user_id = str(raw_user.get("surface_user_id", ""))
        display_name = str(raw_user.get("display_name", "Imported"))
        if not surface_user_id:
            raise ValidationError("passport is missing user.surface_user_id")

        raw_memories = payload.get("memories")
        if not isinstance(raw_memories, list):
            raise ValidationError("passport is missing its memories list")

        user = self._users.get_or_create(surface, surface_user_id, display_name)
        namespace = self._settings.memory_namespace(user.memory_key)
        seen = {record.text for record in self._scope_records(user.id, None, 1000)}

        imported = 0
        skipped = 0
        for item in raw_memories:
            if not isinstance(item, dict):
                continue
            text = str(item.get("text", "")).strip()
            if not text:
                continue
            if text in seen:
                skipped += 1
                continue
            written = await self._memory.remember(text, namespace)
            if self._memories.get_by_blob_id(written.blob_id) is not None:
                # The relayer returned a blob id this index already holds, for
                # example an identical note written into a second namespace.
                # Keep the existing row rather than violating UNIQUE(blob_id).
                skipped += 1
                continue
            try:
                importance = float(item.get("importance", 0.5))
            except (TypeError, ValueError):
                importance = 0.5
            self._memories.create(
                user_id=user.id,
                blob_id=written.blob_id,
                namespace=namespace,
                text=text,
                importance=min(1.0, max(0.0, importance)),
                origin_surface=str(item.get("origin_surface", "passport")),
                occurred_at=str(
                    item.get("occurred_at") or datetime.now(UTC).isoformat(timespec="seconds")
                ),
            )
            seen.add(text)
            imported += 1

        return {
            "user_id": user.id,
            "namespace": namespace,
            "imported": imported,
            "skipped": skipped,
        }

    async def evidence_leaderboard(self) -> list[MemoryStatsSchema]:
        """One row per known user. This is the artifact that proves real use."""
        listing = await self._memory.list_namespaces(limit=500)
        relayer_counts = {
            summary.name: (summary.memory_count, summary.storage_used)
            for summary in listing.namespaces
        }

        rows: list[MemoryStatsSchema] = []
        for user in self._users.list(limit=500):
            namespace = self._settings.memory_namespace(user.memory_key)
            memory_count, storage = relayer_counts.get(namespace, (0, 0))
            rows.append(
                MemoryStatsSchema(
                    user_id=user.id,
                    display_name=user.display_name,
                    surface=user.surface,
                    namespace=namespace,
                    active=self._memories.count_for_user(user.id, STATUS_ACTIVE),
                    superseded=self._memories.count_for_user(user.id, STATUS_SUPERSEDED),
                    contradicted=self._memories.count_for_user(user.id, STATUS_CONTRADICTED),
                    open_contradictions=len(self._contradictions.list_open_for_user(user.id)),
                    turns=self._turns.count_for_user(user.id),
                    relayer_memory_count=memory_count,
                    relayer_storage_bytes=storage,
                    relayer_degraded=listing.degraded,
                )
            )
        return rows
