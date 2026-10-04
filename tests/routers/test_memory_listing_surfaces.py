"""Every surface renders a listing, and the interactive actions really exist.

The Telegram keyboard, the web and extension cards and the CLI pages all expose
the same two per-memory actions in the same words. These tests prove the listing
still renders on each surface and that the controls are wired, not decorative.
"""

from __future__ import annotations

import re
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from cli import main
from main import create_app
from models.entities.memory_phrasing_model import record_facing, subject_names
from tests.routers.test_chat_and_memory_routers import build_test_container

REPO_ROOT = Path(__file__).resolve().parents[2]

ACTION_FORGET = "Forget this one"
ACTION_CORRECT = "Correct this one"

NOTE_LINE = re.compile(r"^\d+\. ")


def _note_lines(text: str) -> int:
    return len([line for line in text.splitlines() if NOTE_LINE.match(line)])


async def store_one(container, text: str = "I am allergic to peanuts") -> str:
    result = await container.conversation_service.handle_turn(
        "telegram", "555", "Ada", text
    )
    return result.user_id


async def test_the_telegram_listing_is_paginated_with_per_note_actions() -> None:
    container, _, channel = build_test_container(
        [{"text": "Ada is allergic to peanuts", "importance": 1.0}]
    )
    await store_one(container)

    await container.conversation_service.handle_callback_query(
        "telegram", "555", "Ada", "555", "cb-1", "mem:list"
    )

    recipient, text = channel.sent[-1]
    assert recipient == "555"
    assert "Your memory" in text
    assert "You are allergic to peanuts" in text
    markup = channel.markups[-1]
    assert markup is not None
    buttons = [button for row in markup["inline_keyboard"] for button in row]
    labels = [button["text"] for button in buttons]
    assert f"{ACTION_FORGET} (1)" in labels
    assert f"{ACTION_CORRECT} (1)" in labels


async def test_the_telegram_listing_pages_rather_than_dumping_every_note() -> None:
    container, _, channel = build_test_container(
        [{"text": "Ada is allergic to peanuts", "importance": 1.0}]
    )
    user_id = await store_one(container)
    service = container.conversation_service
    user = service._users.get_by_id(user_id)
    for index in range(1, 8):
        service._memories.create(
            user_id=user_id,
            blob_id=f"blob-{index}",
            namespace=service._settings.memory_namespace(user.memory_key),
            text=f"Fact number {index}",
            importance=0.5,
            origin_surface="telegram",
            occurred_at="2026-10-01T00:00:00+00:00",
        )

    await service.handle_callback_query(
        "telegram", "555", "Ada", "555", "cb-1", "mem:list"
    )
    first_page = channel.sent[-1][1]
    first_markup = channel.markups[-1]

    assert "Page 1 of 2" in first_page
    assert _note_lines(first_page) == 5
    nav = [
        button["callback_data"]
        for row in first_markup["inline_keyboard"]
        for button in row
        if button["callback_data"].startswith("mem:page:")
    ]
    assert "mem:page:2" in nav

    await service.handle_callback_query(
        "telegram", "555", "Ada", "555", "cb-2", "mem:page:2"
    )
    second_page = channel.sent[-1][1]

    assert "Page 2 of 2" in second_page
    assert _note_lines(second_page) == 3


async def test_the_correct_callback_says_how_to_correct_and_the_command_works() -> None:
    container, _, channel = build_test_container(
        [{"text": "Ada is allergic to peanuts", "importance": 1.0}]
    )
    user_id = await store_one(container)
    service = container.conversation_service

    await service.handle_callback_query(
        "telegram", "555", "Ada", "555", "cb-1", "mem:correct:1"
    )
    prompt = channel.sent[-1][1]
    assert "/correct 1" in prompt

    reply = await service.answer_command(
        "/correct", "telegram", "555", "Ada", "1 You are allergic to shellfish"
    )

    assert "retired" in reply
    await service.await_pending_writes()
    texts = {
        record.text: record.status
        for record in service._memories.list_for_user(user_id, None, 100)
    }
    assert texts["You are allergic to shellfish"] == "active"


async def test_the_web_listing_renders_second_person_cards_not_a_wall() -> None:
    container, _, _ = build_test_container(
        [{"text": "The user is a software engineering student.", "importance": 0.9}]
    )
    user_id = await store_one(container, "I am a software engineering student")

    views = container.memory_admin_service.list_memories(user_id)

    assert [view.text for view in views] == ["You are a software engineering student."]


async def test_the_web_can_forget_and_correct_a_note_by_id() -> None:
    container, _, _ = build_test_container(
        [{"text": "Ada is allergic to peanuts", "importance": 1.0}]
    )
    user_id = await store_one(container)
    service = container.conversation_service
    admin = container.memory_admin_service
    target = admin.list_memories(user_id)[0]

    corrected = await admin.correct_memory(
        user_id, target.blob_id, "You are allergic to shellfish"
    )
    assert corrected["text"] == "You are allergic to shellfish"
    retired = [view for view in admin.list_memories(user_id) if view.status == "active"]
    assert [view.text for view in retired] == ["You are allergic to shellfish"]

    admin.retire_memory(user_id, retired[0].blob_id)
    assert admin.list_memories(user_id) == []


def test_the_cli_listing_pages_and_names_the_actions(cli_env, capsys) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/users":
            return httpx.Response(
                201,
                json={
                    "id": "user-1",
                    "surface": "cli",
                    "surface_user_id": "abc",
                    "display_name": "tester",
                    "created_at": "2024-01-01T00:00:00Z",
                    "memory_namespace": "ns-cli",
                },
            )
        if request.url.path == "/memories/user-1":
            return httpx.Response(
                200,
                json=[
                    {
                        "blob_id": "blob-1",
                        "text": "You are allergic to peanuts.",
                        "status": "active",
                        "importance": 0.9,
                        "origin_surface": "cli",
                        "superseded_by": None,
                        "occurred_at": "2026-10-01T00:00:00Z",
                    }
                ],
            )
        raise AssertionError(f"unexpected request {request.url.path}")

    code = main(["memories", "--page", "1", "--size", "5"], transport=httpx.MockTransport(handler))

    assert code == 0
    out = capsys.readouterr().out
    assert "You are allergic to peanuts." in out
    assert "Page 1 of 1" in out
    assert ACTION_FORGET.lower() in out.lower()
    assert ACTION_CORRECT.lower() in out.lower()


def test_the_forget_and_correct_routes_are_wired_end_to_end() -> None:
    container, _, _ = build_test_container(
        [{"text": "Ada is allergic to peanuts", "importance": 1.0}]
    )
    with TestClient(create_app(container=container)) as client:
        body = client.post(
            "/chat/turn",
            json={
                "surface": "web",
                "surface_user_id": "widget-1",
                "display_name": "Ada",
                "text": "I am allergic to peanuts",
            },
        ).json()
        user_id = body["user_id"]
        blob_id = client.get(f"/memories/{user_id}").json()[0]["blob_id"]

        corrected = client.post(
            f"/memories/{user_id}/{blob_id}/correct",
            json={"text": "You are allergic to shellfish"},
        )
        assert corrected.status_code == 200
        active = client.get(f"/memories/{user_id}").json()
        assert [row["text"] for row in active] == ["You are allergic to shellfish"]

        retired = client.post(f"/memories/{user_id}/{active[0]['blob_id']}/forget")
        assert retired.status_code == 200
        assert client.get(f"/memories/{user_id}").json() == []


def test_the_extension_listing_renders_cards_with_the_same_actions() -> None:
    sidepanel = (REPO_ROOT / "extension" / "sidepanel.js").read_text(encoding="utf-8")
    markup = (REPO_ROOT / "extension" / "sidepanel.html").read_text(encoding="utf-8")

    assert "memory-list" in markup
    assert "memory-card" in sidepanel
    assert ACTION_FORGET in sidepanel
    assert ACTION_CORRECT in sidepanel
    assert "Previous" in sidepanel
    assert "Next" in sidepanel


def test_the_extension_discovers_and_calls_webmcp_page_tools() -> None:
    """The page registers WebMCP tools in the main world; the panel must reach them."""
    sidepanel = (REPO_ROOT / "extension" / "sidepanel.js").read_text(encoding="utf-8")

    # Both API names are feature-detected, so an older or newer page works.
    assert "document.modelContext" in sidepanel
    assert "navigator.modelContext" in sidepanel
    # Discovery and invocation are the two WebMCP operations.
    assert "getTools" in sidepanel
    assert "executeTool" in sidepanel
    # The API only exists in the page's main world, so the injection must ask
    # for it; without this the content script sees nothing.
    assert 'world: "MAIN"' in sidepanel
    # WebMCP tools are tagged so the invoke path routes them to the page rather
    # than to an HTTP endpoint.
    assert 'tool.source = "webmcp"' in sidepanel
    # A tool that can change state or spend money is labelled and needs an
    # explicit confirmation before it can run.
    assert "mcpToolRisk" in sidepanel
    assert "state-changing" in sidepanel
    assert "mcp-confirm" in sidepanel
    assert "Confirm and run" in sidepanel


def test_the_extension_plans_a_page_tool_from_plain_words() -> None:
    """The page tool list asked people to write raw JSON; the chat now drives it."""
    sidepanel = (REPO_ROOT / "extension" / "sidepanel.js").read_text(encoding="utf-8")

    assert "/chat/page-plan" in sidepanel
    assert "function planToolFor" in sidepanel
    assert "function currentPageTools" in sidepanel
    assert "function handlePlannedPageTool" in sidepanel
    assert "function runPlannedPageTool" in sidepanel
    # Read-only runs at once; a state-changing plan opens the confirmation form.
    assert 'mcpToolRisk(tool) === "state-changing"' in sidepanel
    # The form still takes plain words and fills the JSON itself.
    assert 'id: "mcp-request"' in sidepanel
    assert "What should it do? (plain words)" in sidepanel
    # The heads-up hides itself so it never sits in front of the conversation.
    assert "MCP_NOTICE_MS" in sidepanel
    assert "showMcpNotice" in sidepanel
    # Opening the list re-probes, so a tool registered after the first look is
    # still found rather than missing forever.
    assert "refreshPageTools" in sidepanel


def test_the_extension_drops_a_read_page_when_the_tab_changes() -> None:
    """Reading one page hid the Read page button for the rest of the session.

    The panel only ever showed the last page it read, so switching to a new tab
    left no way to read the page in front. The tab events now clear the read
    page and bring the button back.
    """
    sidepanel = (REPO_ROOT / "extension" / "sidepanel.js").read_text(encoding="utf-8")

    assert "watchTabs" in sidepanel
    assert "onActivated" in sidepanel
    assert "onUpdated" in sidepanel
    assert "onRemoved" in sidepanel
    assert "You switched tabs. Read page to use the page in front." in sidepanel
    # Only the panel's own window is followed, so another window cannot clear it.
    assert "pageWindowId" in sidepanel


def test_the_extension_does_not_promise_a_failed_pairing_changed_nothing() -> None:
    """A real /pair redemption failed with a lost reply.

    The panel printed "memory is unchanged" even though a pairing that never
    returned can still have applied on the server, which invited a retry against
    a space the client had already joined.
    """
    sidepanel = (REPO_ROOT / "extension" / "sidepanel.js").read_text(encoding="utf-8")

    assert "commandMayChangeMemorySpace" in sidepanel
    assert "can still take effect on the" in sidepanel
    assert "run /sessions here before you" in sidepanel
    # The plain path keeps its honest, narrower promise.
    assert "Nothing was saved for it, and memory is unchanged. Please try again." in sidepanel


def test_the_web_listing_renders_cards_with_the_same_actions() -> None:
    app_js = (REPO_ROOT / "src" / "web" / "app.js").read_text(encoding="utf-8")

    assert "memory-card" in app_js
    assert ACTION_FORGET in app_js
    assert ACTION_CORRECT in app_js
    assert "Previous" in app_js
    assert "Next" in app_js


async def test_the_forget_menu_label_matches_the_listing_line_for_the_same_record() -> None:
    container, _, _ = build_test_container(
        [{"text": "Ada hates long meetings", "importance": 0.9}]
    )
    user_id = await store_one(container, "I hate long meetings")
    service = container.conversation_service
    user = service._users.get_by_id(user_id)
    assert user is not None
    names = subject_names(user.display_name)

    listing = service.command_reply("/memories", "telegram", "555", "Ada")
    listing_line = next(line for line in listing.splitlines() if line.startswith("1. "))

    records, _ = service._listing_records(user_id, names)
    markup = service._forget_menu_markup(records, names)
    menu_label = markup["inline_keyboard"][0][0]["text"]

    assert menu_label == listing_line


async def test_every_forget_menu_position_resolves_to_the_record_its_label_shows() -> None:
    container, _, _ = build_test_container([])
    service = container.conversation_service
    await service.handle_turn("telegram", "555", "Ada", "hello")
    user = service._users.get_by_identity("telegram", "555")
    assert user is not None
    namespace = service._settings.memory_namespace(user.memory_key)
    for index in range(10):
        service._memories.create(
            user_id=user.id,
            blob_id=f"blob-{index}",
            namespace=namespace,
            text=f"Preference number {index}",
            importance=0.5,
            origin_surface="telegram",
            occurred_at=f"2026-10-{index + 1:02d}T00:00:00Z",
        )

    names = subject_names(user.display_name)
    records = service._forget_records(user.id)
    markup = service._forget_menu_markup(records, names)
    buttons = [button for row in markup["inline_keyboard"] for button in row]
    assert len(buttons) == 10

    shown: dict[int, str] = {}
    for position, button in enumerate(buttons, start=1):
        label = button["text"]
        assert label.startswith(f"{position}. ")
        shown[position] = label
        # The number printed in the label resolves in the same ordered list.
        positional, resolved_position = service._record_for_token(records, str(position))
        assert resolved_position == position
        assert shown[position] == f"{position}. {record_facing(positional, names)}"

    # A rebuild reorders the index. Each button's stable payload must still
    # resolve to the record the label showed, whatever the new position is.
    for record in list(records):
        service._memories.delete_by_blob_id(record.blob_id)
    for record in reversed(list(records)):
        service._memories.create(
            user_id=user.id,
            blob_id=record.blob_id,
            namespace=record.namespace,
            text=record.text,
            importance=record.importance,
            origin_surface=record.origin_surface,
            occurred_at=record.occurred_at,
        )
    rebuilt = service._forget_records(user.id)
    assert [record.blob_id for record in rebuilt] != [record.blob_id for record in records]

    for position, button in enumerate(buttons, start=1):
        token = button["callback_data"][len("mem:forget:") :]
        target, _ = service._record_for_token(rebuilt, token)
        assert target is not None
        assert f"{position}. {record_facing(target, names)}" == shown[position]


async def test_the_listing_and_the_forget_menu_agree_after_index_recovery() -> None:
    container, _, channel = build_test_container(
        [{"text": "Ada is allergic to peanuts", "importance": 1.0}]
    )
    user_id = await store_one(container)
    service = container.conversation_service
    await service.await_pending_writes()
    user = service._users.get_by_id(user_id)
    assert user is not None

    def wipe() -> None:
        for record in service._memories.list_for_user(user_id, None, 1000):
            service._memories.delete_by_blob_id(record.blob_id)

    # A listing recovers the index from the snapshot and shows the note.
    wipe()
    await service.handle_callback_query(
        "telegram", "555", "Ada", "555", "cb-list", "mem:list"
    )
    listing = channel.sent[-1][1]
    listing_lines = [line for line in listing.splitlines() if line.startswith("1. ")]
    assert listing_lines, "the listing must find the note recovered from the snapshot"

    # A different instance handles the forget tap with an empty local index.
    wipe()
    await service.handle_callback_query(
        "telegram", "555", "Ada", "555", "cb-forget", "mem:forget"
    )
    menu = channel.markups[-1]
    assert menu is not None, "the forget menu must recover the same notes"
    labels = [
        button["text"]
        for row in menu["inline_keyboard"]
        for button in row
    ]
    assert [
        line.split(". ", 1)[1] for line in listing_lines
    ] == [label.split(". ", 1)[1] for label in labels]


async def test_the_forget_menu_is_never_shown_with_a_nothing_stored_message() -> None:
    container, _, channel = build_test_container(
        [{"text": "Ada is allergic to peanuts", "importance": 1.0}]
    )
    user_id = await store_one(container)
    service = container.conversation_service
    await service.await_pending_writes()
    for record in service._memories.list_for_user(user_id, None, 1000):
        service._memories.delete_by_blob_id(record.blob_id)

    channel.sent.clear()
    channel.markups.clear()
    await service.handle_callback_query(
        "telegram", "555", "Ada", "555", "cb-forget", "mem:forget"
    )

    assert channel.markups[-1] is not None, "a menu of notes must have been shown"
    assert not any(
        "there is nothing to forget" in text for _, text in channel.sent
    ), "a menu of notes and a nothing-stored message must never coexist"


async def test_no_surface_tells_a_person_with_notes_that_they_are_new() -> None:
    container, _, channel = build_test_container([])
    service = container.conversation_service
    # The person exists and has notes but has never been through a turn: the
    # exact shape that used to be greeted as a first contact.
    user = service._users.get_or_create("telegram", "555", "Ada")
    namespace = service._settings.memory_namespace(user.memory_key)
    for index in range(19):
        service._memories.create(
            user_id=user.id,
            blob_id=f"blob-{index}",
            namespace=namespace,
            text=f"Preference number {index}",
            importance=0.5,
            origin_surface="telegram",
            occurred_at=f"2026-10-{index + 1:02d}T00:00:00Z",
        )

    greeting = await service.answer_command("/start", "telegram", "555", "Ada")
    listing = await service.answer_command("/memories", "telegram", "555", "Ada")

    channel.sent.clear()
    channel.markups.clear()
    await service.handle_callback_query(
        "telegram", "555", "Ada", "555", "cb-forget", "mem:forget"
    )

    assert "Hi, I am" not in greeting
    assert "Welcome back" in greeting
    assert "Preference number 0" in listing
    assert "nothing stored" not in listing.lower()
    assert channel.markups[-1] is not None
    assert not any(
        "there is nothing to forget" in text for _, text in channel.sent
    )

    result = await service.handle_turn(
        "telegram", "555", "Ada", "Tell me about the weather in Oslo"
    )
    assert result is not None
    # The one newness predicate says established: stored notes exist even though
    # the local turns table is empty.
    assert result.first_turn is False
    assert result.onboarding_note is None
    rendered = service._render_reply(result)
    assert "Tell me a few things about yourself" not in rendered

    recalled, _, _, stored = await service._assemble_context(
        user.id, namespace, "Tell me about the weather in Oslo", 6, subject_names("Ada")
    )
    system = service._build_prompt(
        "Ada", "Tell me about the weather in Oslo", recalled, stored
    )[0].content
    assert "nothing stored about this person yet" not in system


@pytest.fixture
def cli_env(tmp_path, monkeypatch):
    config = tmp_path / "ranti_cli.json"
    monkeypatch.setenv("RANTI_CLI_CONFIG", str(config))
    monkeypatch.setenv("RANTI_API_URL", "http://api.test:8000")
    return config
