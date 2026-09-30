"""Recovery: a wiped local index is rebuilt from snapshots stored as memories.

These run against the real gateway boundary, the real offline mock and the real
SQLite index. The model is faked because the model is not under test.
"""

from __future__ import annotations

from collections.abc import Sequence

from core.config import Settings
from core.container import build_memory_gateway
from core.database import Database
from core.errors import DependencyUnavailableError
from crud.contradiction_crud import ContradictionCrud
from crud.memory_crud import MemoryCrud
from crud.turn_crud import TurnCrud
from crud.user_crud import UserCrud
from models.entities.memory_index_model import INDEX_QUERY, decode_snapshot
from services.conversation_service import ConversationService
from services.memory_admin_service import MemoryAdminService
from tests.services.test_conversation_service import FakeLlm


class IndexFailingGateway:
    """Fails writes to the companion index namespace, passes the rest through."""

    def __init__(self, inner) -> None:
        self._inner = inner

    async def remember(self, text: str, namespace: str, idempotency_key: str | None = None):
        if namespace.endswith(".idx"):
            raise DependencyUnavailableError("walrus-memory", "index namespace unavailable")
        return await self._inner.remember(text, namespace, idempotency_key=idempotency_key)

    def __getattr__(self, name: str):
        return getattr(self._inner, name)


class Harness:
    def __init__(
        self,
        facts: Sequence[dict[str, object]],
        verdict: str = "DIFFERENT",
        gateway_factory=None,
    ) -> None:
        self.database = Database(":memory:")
        self.database.migrate()
        self.settings = Settings(database_path=":memory:", memwal_namespace_prefix="ranti")
        self.users = UserCrud(self.database)
        self.memories = MemoryCrud(self.database)
        self.turns = TurnCrud(self.database)
        self.contradictions = ContradictionCrud(self.database)
        self.llm = FakeLlm(facts, verdict)
        base = build_memory_gateway(self.settings)
        self.gateway = gateway_factory(base) if gateway_factory is not None else base
        self.service = ConversationService(
            users=self.users,
            memories=self.memories,
            turns=self.turns,
            contradictions=self.contradictions,
            memory_gateway=self.gateway,
            llm_gateway=self.llm,
            settings=self.settings,
        )
        self.admin = MemoryAdminService(
            users=self.users,
            memories=self.memories,
            turns=self.turns,
            contradictions=self.contradictions,
            memory_gateway=self.gateway,
            settings=self.settings,
        )

    async def say(self, text: str, surface: str = "telegram"):
        return await self.service.handle_turn(surface, "42", "Ada", text)

    def index_namespace(self, user_id: str) -> str:
        user = self.users.get_by_id(user_id)
        assert user is not None
        return self.settings.index_namespace(user.memory_key)

    def wipe_index(self, user_id: str) -> None:
        for memory in self.memories.list_for_user(user_id, None, 1000):
            self.memories.delete_by_blob_id(memory.blob_id)


async def test_teaching_a_fact_writes_a_snapshot_into_the_index_namespace() -> None:
    harness = Harness([{"text": "Ada is allergic to peanuts", "importance": 1.0}])

    result = await harness.say("I am allergic to peanuts")

    namespace = harness.index_namespace(result.user_id)
    outcome = await harness.gateway.recall(INDEX_QUERY, namespace, limit=5)
    assert outcome.memories, "no snapshot was stored in the companion namespace"
    sequence, records = decode_snapshot(outcome.memories[0].text)
    assert sequence >= 0
    assert [item.blob_id for item in records] == [result.stored_facts[0].blob_id]
    assert records[0].text == "Ada is allergic to peanuts"
    assert records[0].origin_surface == "telegram"


async def test_a_wiped_index_is_rebuilt_from_the_snapshot_and_recalls_again() -> None:
    harness = Harness([{"text": "Ada is allergic to peanuts", "importance": 1.0}])
    result = await harness.say("I am allergic to peanuts")
    expected_blob_id = result.stored_facts[0].blob_id

    harness.wipe_index(result.user_id)
    assert harness.memories.list_for_user(result.user_id) == []

    report = await harness.admin.rebuild_index(result.user_id)

    assert report["records_recovered"] == 1
    assert report["records_already_present"] == 0
    assert report["newest_sequence"] >= 0
    assert str(report["namespace"]).endswith(".idx")
    restored = harness.memories.list_for_user(result.user_id)
    assert len(restored) == 1
    assert restored[0].blob_id == expected_blob_id
    assert restored[0].importance == 1.0
    assert restored[0].status == "active"
    assert restored[0].origin_surface == "telegram"

    _, recalled, degraded, _ = await harness.service.recall_context(
        result.user_id, "peanuts", budget=5
    )
    assert degraded is False
    assert [memory.text for memory in recalled] == ["Ada is allergic to peanuts"]


async def test_rebuilding_twice_reports_the_second_pass_as_already_present() -> None:
    harness = Harness([{"text": "Ada is allergic to peanuts", "importance": 1.0}])
    result = await harness.say("I am allergic to peanuts")
    harness.wipe_index(result.user_id)

    first = await harness.admin.rebuild_index(result.user_id)
    second = await harness.admin.rebuild_index(result.user_id)

    assert first["records_recovered"] == 1
    assert first["records_already_present"] == 0
    assert second["records_recovered"] == 0
    assert second["records_already_present"] == 1


async def test_rebuild_without_a_snapshot_returns_empty_without_raising() -> None:
    harness = Harness([])
    result = await harness.say("hello")

    report = await harness.admin.rebuild_index(result.user_id)

    assert report["snapshots_scanned"] == 0
    assert report["records_recovered"] == 0
    assert report["newest_sequence"] == -1


async def test_a_turn_still_succeeds_when_the_snapshot_write_fails() -> None:
    harness = Harness(
        [{"text": "Ada is allergic to peanuts", "importance": 1.0}],
        gateway_factory=IndexFailingGateway,
    )

    result = await harness.say("I am allergic to peanuts")

    assert result.stored_facts[0].verdict == "new"
    assert result.stored_facts[0].blob_id
    stored = harness.memories.list_for_user(result.user_id)
    assert [memory.text for memory in stored] == ["Ada is allergic to peanuts"]

    namespace = harness.index_namespace(result.user_id)
    outcome = await harness.gateway.recall(INDEX_QUERY, namespace, limit=5)
    assert outcome.memories == ()
