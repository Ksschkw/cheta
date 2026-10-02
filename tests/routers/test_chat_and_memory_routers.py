"""HTTP contracts for the chat, memory, evidence and passport routes."""

from __future__ import annotations

import json
from collections.abc import Sequence

import pytest
from fastapi.testclient import TestClient

from core.attachment_parser import AttachmentParser
from core.config import Settings
from core.container import Container, build_memory_gateway
from core.database import Database
from crud.contradiction_crud import ContradictionCrud
from crud.memory_crud import MemoryCrud
from crud.turn_crud import TurnCrud
from crud.user_crud import UserCrud
from main import create_app
from schemas.llm_schema import ChatMessageSchema, CompletionSchema
from services.conversation_service import PAGE_PLAN_MARKER, ConversationService
from services.memory_admin_service import MemoryAdminService
from services.user_service import UserService

EXTRACT_MARKER = "extract durable facts"
ADJUDICATE_MARKER = "compare one remembered fact"


class FakeLlm:
    def __init__(
        self,
        facts: Sequence[dict[str, object]],
        verdict: str = "DIFFERENT",
        plan: str = "",
    ) -> None:
        self.facts = list(facts)
        self.verdict = verdict
        # The raw answer to the page-tool planning prompt, when a test wants to
        # exercise the planner. Empty means "no tool", which is the safe answer.
        self.plan = plan
        self.reply_calls = 0
        # The user content of every reply call, so a test can prove what the
        # model actually saw (for example the text extracted from a PDF).
        self.user_messages: list[str] = []

    async def complete(
        self,
        messages: Sequence[ChatMessageSchema],
        *,
        temperature: float = 0.2,
        max_tokens: int = 800,
    ) -> CompletionSchema:
        system = messages[0].content
        if PAGE_PLAN_MARKER in system:
            text = self.plan or json.dumps(
                {"tool": None, "arguments": {}, "explanation": ""}
            )
        elif EXTRACT_MARKER in system:
            text = json.dumps(self.facts)
        elif ADJUDICATE_MARKER in system:
            text = self.verdict
        else:
            self.reply_calls += 1
            if len(messages) > 1:
                self.user_messages.append(messages[1].content)
            text = "I remember." if "<<<" in system else "I have no memory of you."
        return CompletionSchema(text=text, provider="fake", model="fake-1")


class RecordingReplyChannel:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []
        self.typing: list[str] = []
        self.photos: list[tuple[str, str | None, dict | None]] = []
        # Parallel to `sent`: the inline keyboard (or None) for each message.
        self.markups: list[dict[str, object] | None] = []
        self.documents: list[tuple[str, str, bytes]] = []
        self.callback_answers: list[tuple[str, str | None]] = []

    async def send_message(
        self, recipient_id: str, text: str, reply_markup: dict[str, object] | None = None
    ) -> None:
        self.sent.append((recipient_id, text))
        self.markups.append(reply_markup)

    async def send_document(
        self,
        recipient_id: str,
        filename: str,
        content: bytes,
        caption: str | None = None,
    ) -> None:
        self.documents.append((recipient_id, filename, content))

    async def answer_callback_query(
        self, callback_query_id: str, text: str | None = None
    ) -> None:
        self.callback_answers.append((callback_query_id, text))

    async def send_typing(self, recipient_id: str) -> None:
        """Kept separate from `sent`, which holds delivered messages."""
        self.typing.append(recipient_id)

    async def send_photo(
        self,
        recipient_id: str,
        image_path: str,
        caption: str | None = None,
        reply_markup: dict | None = None,
    ) -> None:
        self.photos.append((image_path, caption, reply_markup))
        # The keyboard travels with the picture, so record it the same way a
        # text message with a keyboard would be recorded.
        self.markups.append(reply_markup)


def build_test_container(
    facts: Sequence[dict[str, object]],
    verdict: str = "DIFFERENT",
    reply_channel: RecordingReplyChannel | None = None,
    with_llm: bool = True,
    webhook_secret: str = "",
    attachment_gateway: object | None = None,
    attachment_parser: object | None = None,
    plan: str = "",
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
    llm = FakeLlm(facts, verdict, plan) if with_llm else None
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
        attachment_gateway=attachment_gateway,  # type: ignore[arg-type]
        attachment_parser=attachment_parser or AttachmentParser(),  # type: ignore[arg-type]
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


def page_plan_client(plan: str) -> TestClient:
    """A client whose model answers the page-tool planner with `plan`."""
    container, _, _ = build_test_container([], plan=plan)
    return TestClient(create_app(container=container))


PIZZA_TOOL = {
    "name": "add_topping",
    "description": "Add one or more toppings to the pizza",
    "input_schema": {
        "type": "object",
        "properties": {"topping": {"type": "string"}},
        "required": ["topping"],
    },
    "state_changing": True,
}


def test_the_page_plan_route_names_an_offered_tool_with_its_arguments() -> None:
    plan = json.dumps(
        {
            "tool": "add_topping",
            "arguments": {"topping": "pepperoni"},
            "explanation": "Adding pepperoni to the pizza.",
        }
    )
    with page_plan_client(plan) as client:
        response = client.post(
            "/chat/page-plan",
            json={"request": "add pepperoni", "tools": [PIZZA_TOOL]},
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["tool"] == "add_topping"
    assert body["arguments"] == {"topping": "pepperoni"}
    assert "pepperoni" in body["explanation"]


def test_the_page_plan_route_drops_a_tool_the_page_never_offered() -> None:
    plan = json.dumps(
        {"tool": "delete_everything", "arguments": {}, "explanation": "on it"}
    )
    with page_plan_client(plan) as client:
        response = client.post(
            "/chat/page-plan",
            json={"request": "delete everything", "tools": [PIZZA_TOOL]},
        )

    body = response.json()
    assert body["tool"] is None
    assert body["arguments"] == {}


def test_the_page_plan_route_names_no_tool_when_the_page_has_none() -> None:
    plan = json.dumps(
        {"tool": "add_topping", "arguments": {}, "explanation": "sure"}
    )
    with page_plan_client(plan) as client:
        response = client.post(
            "/chat/page-plan", json={"request": "add pepperoni", "tools": []}
        )

    assert response.json()["tool"] is None


def test_the_page_plan_route_survives_an_unparseable_plan() -> None:
    with page_plan_client("I would rather just chat about pizza.") as client:
        response = client.post(
            "/chat/page-plan",
            json={"request": "add pepperoni", "tools": [PIZZA_TOOL]},
        )

    assert response.status_code == 200, response.text
    assert response.json()["tool"] is None


def test_a_turn_returns_the_reply_and_the_stored_fact(client: TestClient) -> None:
    body = turn(client, "I am allergic to peanuts")

    # The first turn now also carries onboarding, so the model's answer leads
    # and the service-authored onboarding follows it.
    assert body["reply"].startswith("I have no memory of you.")
    assert body["first_turn"] is True
    assert body["onboarding_note"] in body["reply"]
    assert body["memory_namespace"].endswith("web-widget-1")
    assert len(body["stored_facts"]) == 1
    assert body["stored_facts"][0]["blob_id"]
    assert body["memory_degraded"] is False


def test_the_next_turn_recalls_what_the_first_one_taught(client: TestClient) -> None:
    turn(client, "I am allergic to peanuts")

    body = turn(client, "I am allergic to peanuts")

    assert body["reply"] == "I remember."
    assert [memory["text"] for memory in body["recalled"]] == ["You are allergic to peanuts"]
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


def test_a_command_works_over_http_the_same_as_on_telegram(client: TestClient) -> None:
    """Commands were Telegram-only: /help over HTTP was sent to the model.

    A browser extension found this by checking the deployed OpenAPI and finding
    no command route. Every surface must behave the same.
    """
    response = client.post(
        "/chat/turn",
        json={
            "surface": "web",
            "surface_user_id": "widget-cmd",
            "display_name": "Ada",
            "text": "/help",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["command"] == "/help"
    assert body["provider"] == "command"
    assert body["turn_id"] == ""
    assert "/memories" in body["reply"]

    # And nothing was stored as a conversation turn.
    stats = client.get(f"/memories/{body['user_id']}/stats").json()
    assert stats["turns"] == 0
