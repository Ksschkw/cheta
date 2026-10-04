"""The agent loop: tool execution, bounds, timeout survival, every surface."""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from core.config import Settings
from core.container import (
    Container,
    build_memory_gateway,
)
from core.database import Database
from core.telegram_dispatch import TelegramUpdateRunner
from core.tools.tool_registry import ToolContext, ToolRegistry, ToolSpec
from crud.contradiction_crud import ContradictionCrud
from crud.memory_crud import MemoryCrud
from crud.seen_update_crud import SeenUpdateCrud
from crud.turn_crud import TurnCrud
from crud.user_crud import UserCrud
from main import create_app
from schemas.attachment_schema import AttachmentSchema
from schemas.llm_schema import ChatMessageSchema, CompletionSchema, ToolCallSchema
from schemas.tool_schema import ToolResultSchema
from services.conversation_service import MAX_TOOL_ROUNDS, ConversationService
from services.memory_admin_service import MemoryAdminService
from services.user_service import UserService

EXTRACT_MARKER = "extract durable facts"
ADJUDICATE_MARKER = "compare one remembered fact"


class ScriptedLlm:
    """A model that asks for one tool, then answers using the tool result.

    ``always_call`` makes it keep asking, which is how the round bound is tested.
    """

    def __init__(self, tool_name: str = "spy", arguments: str = '{"value": "peek"}') -> None:
        self.tool_name = tool_name
        self.arguments = arguments
        self.always_call = False
        self.rounds = 0
        self.last_user_text: str | None = None

    async def complete_with_tools(
        self, messages, tools, *, temperature: float = 0.2, max_tokens: int = 800
    ) -> CompletionSchema:
        self.rounds += 1
        for message in messages:
            if message.role == "user":
                self.last_user_text = message.content
                break
        tool_contents = [m.content for m in messages if m.role == "tool"]
        if self.always_call or not tool_contents:
            return CompletionSchema(
                text="",
                provider="fake",
                model="fake",
                tool_calls=(
                    ToolCallSchema(
                        id=f"call-{self.rounds}",
                        name=self.tool_name,
                        arguments=self.arguments,
                    ),
                ),
            )
        return CompletionSchema(
            text=f"FINAL saw [{tool_contents[-1]}]", provider="fake", model="fake"
        )

    async def complete(
        self, messages, *, temperature: float = 0.2, max_tokens: int = 800
    ) -> CompletionSchema:
        system = messages[0].content if messages else ""
        if EXTRACT_MARKER in system:
            return CompletionSchema(text="[]", provider="fake", model="fake")
        if ADJUDICATE_MARKER in system:
            return CompletionSchema(text="DIFFERENT", provider="fake", model="fake")
        for message in messages:
            if message.role == "user":
                self.last_user_text = message.content
                break
        return CompletionSchema(text="FINAL plain", provider="fake", model="fake")


class SpyTool:
    def __init__(
        self, name: str = "spy", result: str = "SPY_OK", delay: float = 0.0, timeout: float = 5.0
    ) -> None:
        self.name = name
        self.result = result
        self.delay = delay
        self.timeout = timeout
        self.calls: list[dict[str, object]] = []

    async def handler(
        self, arguments: dict[str, object], context: ToolContext
    ) -> ToolResultSchema:
        self.calls.append(dict(arguments))
        if self.delay:
            await asyncio.sleep(self.delay)
        return ToolResultSchema.success(self.name, self.result)

    def spec(self) -> ToolSpec:
        return ToolSpec(
            name=self.name,
            description="A spy tool used by tests. It records its arguments.",
            parameters={
                "type": "object",
                "properties": {"value": {"type": "string"}},
                "additionalProperties": False,
            },
            handler=self.handler,
            timeout_seconds=self.timeout,
        )


class RecordingChannel:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []
        self.typing: list[str] = []

    async def send_message(self, recipient_id: str, text: str, reply_markup=None) -> None:
        self.sent.append((recipient_id, text))

    async def send_document(self, recipient_id, filename, content, caption=None) -> None:
        return None

    async def send_photo(self, recipient_id, image_path, caption=None, reply_markup=None) -> None:
        return None

    async def answer_callback_query(self, callback_query_id, text=None) -> None:
        return None

    async def send_typing(self, recipient_id: str) -> None:
        self.typing.append(recipient_id)


class FakeTranscription:
    def __init__(self, text: str) -> None:
        self.text = text
        self.calls: list[tuple[str, int, str]] = []

    async def transcribe(self, filename: str, content: bytes, mime_type: str) -> str:
        self.calls.append((filename, len(content), mime_type))
        return self.text


class FakeAttachmentGateway:
    def __init__(self, content: bytes) -> None:
        self.content = content
        self.paths: list[str] = []

    async def get_file_path(self, file_id: str) -> str:
        self.paths.append(file_id)
        return "voice/file.ogg"

    async def download_file(self, file_path: str) -> bytes:
        return self.content


def build_harness(
    llm: ScriptedLlm,
    tools: ToolRegistry | None,
    reply_channel: RecordingChannel | None = None,
    transcription_gateway=None,
    attachment_gateway=None,
) -> tuple[Container, ConversationService]:
    settings = Settings(database_path=":memory:", memwal_namespace_prefix="ranti")
    database = Database(":memory:")
    database.migrate()
    users = UserCrud(database)
    memories = MemoryCrud(database)
    turns = TurnCrud(database)
    contradictions = ContradictionCrud(database)
    gateway = build_memory_gateway(settings)
    service = ConversationService(
        users=users,
        memories=memories,
        turns=turns,
        contradictions=contradictions,
        memory_gateway=gateway,
        llm_gateway=llm,  # type: ignore[arg-type]
        settings=settings,
        reply_channel=reply_channel,  # type: ignore[arg-type]
        tools=tools,
        transcription_gateway=transcription_gateway,
        attachment_gateway=attachment_gateway,
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
        conversation_service=service,
        memory_admin_service=admin,
        telegram_runner=TelegramUpdateRunner(seen_updates=SeenUpdateCrud(database)),
        tools=tools,
    )
    return container, service


# ---------------------------------------------------------- required tests 1-3


async def test_the_agent_loop_executes_a_tool_and_feeds_the_result_back() -> None:
    spy = SpyTool()
    llm = ScriptedLlm()
    _, service = build_harness(llm, ToolRegistry([spy.spec()]))

    result = await service.handle_turn("web", "u1", "Ada", "please use your tool")

    assert spy.calls == [{"value": "peek"}]
    # The tool result reached the model, which used it in the final answer.
    assert "SPY_OK" in result.reply


async def test_the_loop_is_bounded_and_cannot_exceed_its_round_limit() -> None:
    spy = SpyTool()
    llm = ScriptedLlm()
    llm.always_call = True
    _, service = build_harness(llm, ToolRegistry([spy.spec()]))

    result = await service.handle_turn("web", "u1", "Ada", "keep going")

    assert llm.rounds == MAX_TOOL_ROUNDS
    assert len(spy.calls) == MAX_TOOL_ROUNDS
    # Bounded means it still ends in an answer, not an exception. The first turn
    # also carries onboarding, which is appended after the answer.
    assert result.reply.startswith("FINAL plain")


async def test_a_tool_timeout_is_a_readable_failure_and_the_turn_completes() -> None:
    spy = SpyTool(delay=0.5, timeout=0.05)
    llm = ScriptedLlm()
    _, service = build_harness(llm, ToolRegistry([spy.spec()]))

    result = await service.handle_turn("web", "u1", "Ada", "use the slow tool")

    assert spy.calls == [{"value": "peek"}]
    assert "timed out" in result.reply
    # The failure says what happened and what to try next.
    assert "try again" in result.reply
    assert result.reply


async def test_the_turn_reports_the_tools_it_ran() -> None:
    """The agent's work is reported, not hidden inside the reply text."""
    spy = SpyTool()
    llm = ScriptedLlm()
    _, service = build_harness(llm, ToolRegistry([spy.spec()]))

    result = await service.handle_turn("web", "u1", "Ada", "please use your tool")

    assert result.tool_activity == ["spy"]


async def test_a_turn_without_tools_reports_no_activity() -> None:
    llm = ScriptedLlm()
    _, service = build_harness(llm, None)

    result = await service.handle_turn("web", "u1", "Ada", "just talk to me")

    assert result.tool_activity == []


async def test_the_pushed_reply_names_the_tools_it_ran() -> None:
    """A pushed surface that says what it did is easier to trust."""
    spy = SpyTool()
    llm = ScriptedLlm()
    _, service = build_harness(llm, ToolRegistry([spy.spec()]))

    result = await service.handle_turn("telegram", "42", "Ada", "use your tool")

    assert "tools: spy" in service._render_reply(result)


# ------------------------------------------------ tool on every surface


@pytest.mark.parametrize("surface", ["telegram", "web", "cli", "extension"])
async def test_a_tool_executes_on_every_surface(surface: str) -> None:
    spy = SpyTool()
    llm = ScriptedLlm()
    if surface == "telegram":
        channel = RecordingChannel()
        _, service = build_harness(llm, ToolRegistry([spy.spec()]), reply_channel=channel)
        result = await service.handle_surface_turn(
            "telegram", "42", "Ada", "please use your tool", "42"
        )
        assert result is not None
        assert spy.calls == [{"value": "peek"}]
        assert any("SPY_OK" in text for _, text in channel.sent)
        return

    # web, cli and extension reach the loop through the HTTP route.
    container, _ = build_harness(llm, ToolRegistry([spy.spec()]))
    client = TestClient(create_app(container=container))
    response = client.post(
        "/chat/turn",
        json={
            "surface": surface,
            "surface_user_id": "user-1",
            "display_name": "Ada",
            "text": "please use your tool",
        },
    )

    assert response.status_code == 200, response.text
    assert spy.calls == [{"value": "peek"}]
    assert "SPY_OK" in response.json()["reply"]


# --------------------------------------------------------------- required 7


async def test_a_voice_note_is_transcribed_and_becomes_the_turn_text() -> None:
    spy = SpyTool()
    llm = ScriptedLlm()
    llm.always_call = False
    audio = b"OggS-not-really-audio"
    transcription = FakeTranscription("remind me to call my mother")
    attachment = FakeAttachmentGateway(audio)
    channel = RecordingChannel()
    _, service = build_harness(
        llm,
        ToolRegistry([spy.spec()]),
        reply_channel=channel,
        transcription_gateway=transcription,
        attachment_gateway=attachment,
    )

    result = await service.handle_surface_voice(
        "telegram",
        "42",
        "Ada",
        "42",
        AttachmentSchema(
            media_kind="voice",
            file_id="voice-1",
            mime_type="audio/ogg",
            file_size=len(audio),
        ),
    )

    assert transcription.calls == [("voice.ogg", len(audio), "audio/ogg")]
    # The person sees what was heard, before the answer.
    assert channel.sent[0] == ("42", "I heard: remind me to call my mother")
    # The transcript is the turn's user text.
    assert llm.last_user_text == "remind me to call my mother"
    assert result is not None and result.reply


async def test_a_voice_note_is_refused_clearly_when_transcription_is_unavailable() -> None:
    llm = ScriptedLlm()
    channel = RecordingChannel()
    _, service = build_harness(
        llm,
        None,
        reply_channel=channel,
        transcription_gateway=None,
        attachment_gateway=FakeAttachmentGateway(b"audio"),
    )

    result = await service.handle_surface_voice(
        "telegram",
        "42",
        "Ada",
        "42",
        AttachmentSchema(media_kind="voice", file_id="voice-1", file_size=5),
    )

    assert result is None
    assert "cannot transcribe" in channel.sent[0][1]
    assert llm.last_user_text is None


def test_the_telegram_router_routes_a_voice_note_into_transcription() -> None:
    """The router must send message.voice to the voice path, not the media refusal."""
    llm = ScriptedLlm()
    channel = RecordingChannel()
    audio = b"OggS-voice"
    transcription = FakeTranscription("what is the weather in Lagos")
    container, _ = build_harness(
        llm,
        None,
        reply_channel=channel,
        transcription_gateway=transcription,
        attachment_gateway=FakeAttachmentGateway(audio),
    )
    client = TestClient(create_app(container=container))
    payload = {
        "update_id": 3,
        "message": {
            "message_id": 30,
            "from": {"id": 555, "is_bot": False, "first_name": "Ada"},
            "chat": {"id": 555, "type": "private"},
            "voice": {
                "file_id": "voice-9",
                "duration": 3,
                "mime_type": "audio/ogg",
                "file_size": len(audio),
            },
        },
    }

    response = client.post("/webhooks/telegram/voice", json=payload)

    assert response.status_code == 200, response.text
    assert response.json() == {"ok": True, "handled": True, "accepted": True}
    assert transcription.calls
    assert channel.sent[0] == ("555", "I heard: what is the weather in Lagos")
    assert llm.last_user_text == "what is the weather in Lagos"
