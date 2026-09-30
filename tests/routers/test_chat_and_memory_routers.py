"""HTTP contracts for the chat, memory, evidence and passport routes."""

from __future__ import annotations

import json
from collections.abc import Sequence

import pytest
from fastapi.testclient import TestClient

from core.config import Settings
from core.container import Container, build_memory_gateway
from core.database import Database
from crud.contradiction_crud import ContradictionCrud
from crud.memory_crud import MemoryCrud
from crud.turn_crud import TurnCrud
from crud.user_crud import UserCrud
from main import create_app
from schemas.llm_schema import ChatMessageSchema, CompletionSchema
from services.conversation_service import ConversationService
from services.memory_admin_service import MemoryAdminService
from services.user_service import UserService

EXTRACT_MARKER = "extract durable facts"
ADJUDICATE_MARKER = "compare one remembered fact"


class FakeLlm:
    def __init__(self, facts: Sequence[dict[str, object]], verdict: str = "DIFFERENT") -> None:
        self.facts = list(facts)
        self.verdict = verdict
        self.reply_calls = 0

    async def complete(
        self,
        messages: Sequence[ChatMessageSchema],
        *,
        temperature: float = 0.2,
        max_tokens: int = 800,
    ) -> CompletionSchema:
        system = messages[0].content
        if EXTRACT_MARKER in system:
            text = json.dumps(self.facts)
        elif ADJUDICATE_MARKER in system:
            text = self.verdict
        else:
            self.reply_calls += 1
            text = "I remember." if "<<<" in system else "I have no memory of you."
        return CompletionSchema(text=text, provider="fake", model="fake-1")


class RecordingReplyChannel:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    async def send_message(self, recipient_id: str, text: str) -> None:
        self.sent.append((recipient_id, text))


def build_test_container(
    facts: Sequence[dict[str, object]],
    verdict: str = "DIFFERENT",
    reply_channel: RecordingReplyChannel | None = None,
    with_llm: bool = True,
    webhook_secret: str = "",
) -> tuple[Container, FakeLlm | None, RecordingReplyChannel]:
    settings = Settings(
        database_path=":memory:",
        memwal_namespace_prefix="ranti",
        telegram_webhook_secret=webhook_secret,
    )
    database = Database(":memory:")
    database.migrate()

    users = UserCrud(database)
    memories = MemoryCrud(database)
    turns = TurnCrud(database)
    contradictions = ContradictionCrud(database)
    gateway = build_memory_gateway(settings)
    llm = FakeLlm(facts, verdict) if with_llm else None
    channel = reply_channel or RecordingReplyChannel()

    conversation = ConversationService(
        users=users,
        memories=memories,
        turns=turns,
        contradictions=contradictions,
        memory_gateway=gateway,
        llm_gateway=llm,
        settings=settings,
        reply_channel=channel,
    )
    admin = MemoryAdminService(
        users=users,
        memories=memories,
        turns=turns,
        contradictions=contradictions,
        memory_gateway=gateway,
        settings=settings,
    )
    container = Container(
        settings=settings,
        database=database,
        memory_gateway=gateway,
        llm_gateway=llm,  # type: ignore[arg-type]
        telegram_gateway=None,
        user_service=UserService(users=users, settings=settings),
        conversation_service=conversation,
        memory_admin_service=admin,
    )
    return container, llm, channel


@pytest.fixture
def client() -> TestClient:
    container, _, _ = build_test_container(
        [{"text": "Ada is allergic to peanuts", "importance": 1.0}]
    )
    with TestClient(create_app(container=container)) as test_client:
        yield test_client


def turn(client: TestClient, text: str, memory_enabled: bool = True) -> dict:
    response = client.post(
        "/chat/turn",
        json={
            "surface": "web",
            "surface_user_id": "widget-1",
            "display_name": "Ada",
            "text": text,
            "memory_enabled": memory_enabled,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_a_turn_returns_the_reply_and_the_stored_fact(client: TestClient) -> None:
    body = turn(client, "I am allergic to peanuts")

    assert body["reply"] == "I have no memory of you."
    assert body["memory_namespace"].endswith("web-widget-1")
    assert len(body["stored_facts"]) == 1
    assert body["stored_facts"][0]["blob_id"]
    assert body["memory_degraded"] is False


def test_the_next_turn_recalls_what_the_first_one_taught(client: TestClient) -> None:
    turn(client, "I am allergic to peanuts")

    body = turn(client, "I am allergic to peanuts")

    assert body["reply"] == "I remember."
    assert [memory["text"] for memory in body["recalled"]] == ["Ada is allergic to peanuts"]
    assert body["recalled"][0]["salience"] > 0
    assert body["recalled"][0]["origin_surface"] == "web"
    assert body["skipped_duplicates"] == 1


def test_memory_can_be_switched_off_for_a_turn(client: TestClient) -> None:
    turn(client, "I am allergic to peanuts")

    body = turn(client, "I am allergic to peanuts", memory_enabled=False)

    assert body["recalled"] == []
    assert body["stored_facts"] == []
    assert body["reply"] == "I have no memory of you."


def test_counterfactual_endpoint_contrasts_the_two_answers(client: TestClient) -> None:
    turn(client, "I am allergic to peanuts")
    second = turn(client, "I am allergic to peanuts")

    response = client.post(f"/chat/counterfactual/{second['turn_id']}")

    assert response.status_code == 200
    body = response.json()
    assert body["recalled_count"] == 1
    assert body["reply_changed"] is True
    assert body["with_memory"] != body["without_memory"]


def test_counterfactual_for_unknown_turn_is_a_clean_404(client: TestClient) -> None:
    response = client.post("/chat/counterfactual/does-not-exist")

    assert response.status_code == 404
    assert response.json()["error"] == "not_found"


def test_missing_model_configuration_returns_503_without_leaking_internals() -> None:
    container, _, _ = build_test_container([], with_llm=False)
    with TestClient(create_app(container=container)) as client:
        response = client.post(
            "/chat/turn",
            json={
                "surface": "web",
                "surface_user_id": "widget-9",
                "display_name": "Ada",
                "text": "hello",
            },
        )

    assert response.status_code == 503
    assert response.json()["error"] == "dependency_unavailable"
    for forbidden in ("traceback", "site-packages", "openai", "/home/"):
        assert forbidden not in response.text.lower()


def test_stats_report_real_counts(client: TestClient) -> None:
    body = turn(client, "I am allergic to peanuts")

    response = client.get(f"/memories/{body['user_id']}/stats")

    assert response.status_code == 200
    stats = response.json()
    assert stats["active"] == 1
    assert stats["turns"] == 1
    assert stats["surface"] == "web"
    assert stats["relayer_degraded"] is False


def test_stats_for_unknown_user_is_404(client: TestClient) -> None:
    assert client.get("/memories/nobody/stats").status_code == 404


def test_evidence_leaderboard_lists_every_user(client: TestClient) -> None:
    turn(client, "I am allergic to peanuts")

    response = client.get("/evidence/users")

    assert response.status_code == 200
    rows = response.json()
    assert len(rows) == 1
    assert rows[0]["display_name"] == "Ada"
    assert rows[0]["active"] >= 1


def test_passport_round_trip_moves_memories_to_another_identity(client: TestClient) -> None:
    body = turn(client, "I am allergic to peanuts")
    passport = client.get(f"/memories/{body['user_id']}/passport").json()
    assert passport["format"] == "ranti.memory-passport.v1"
    assert len(passport["memories"]) == 1

    passport["user"] = {
        "display_name": "Ada on CLI",
        "surface": "cli",
        "surface_user_id": "ada-cli",
    }
    imported = client.post("/memories/passport/import", json=passport)

    assert imported.status_code == 200
    assert imported.json()["imported"] == 1
    assert imported.json()["namespace"].endswith("cli-ada-cli")

    again = client.post("/memories/passport/import", json=passport)
    assert again.status_code == 200
    assert again.json()["imported"] == 0
    assert again.json()["skipped"] == 1


def test_import_rejects_a_passport_without_an_identity(client: TestClient) -> None:
    response = client.post("/memories/passport/import", json={"memories": []})

    assert response.status_code == 422
    assert response.json()["error"] == "validation_error"
