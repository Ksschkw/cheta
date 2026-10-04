"""Telegram webhook contract. The bot must always answer 200 or Telegram retries.

Covers the two features added for a real user complaint: inline keyboard buttons
with callback handling, and honest local attachment reading.
"""

from __future__ import annotations

import asyncio
import json
import re

from fastapi.testclient import TestClient

from main import create_app
from schemas.llm_schema import CompletionSchema
from tests.routers.test_chat_and_memory_routers import (
    RecordingReplyChannel,
    build_test_container,
)


def update(
    chat_id: int, text: str, first_name: str = "Ada", update_id: int = 1
) -> dict:
    return {
        "update_id": update_id,
        "message": {
            "message_id": 10,
            "from": {"id": chat_id, "is_bot": False, "first_name": first_name},
            "chat": {"id": chat_id, "type": "private"},
            "text": text,
        },
    }


def callback_update(chat_id: int, data: str, callback_id: str = "cb-1") -> dict:
    return {
        "update_id": 7,
        "callback_query": {
            "id": callback_id,
            "from": {"id": chat_id, "is_bot": False, "first_name": "Ada"},
            "message": {
                "message_id": 11,
                "chat": {"id": chat_id, "type": "private"},
            },
            "data": data,
        },
    }


def document_update(
    chat_id: int,
    file_name: str,
    file_size: int,
    mime_type: str = "application/pdf",
    caption: str | None = None,
    file_id: str = "file-1",
) -> dict:
    message: dict = {
        "message_id": 12,
        "from": {"id": chat_id, "is_bot": False, "first_name": "Ada"},
        "chat": {"id": chat_id, "type": "private"},
        "document": {
            "file_id": file_id,
            "file_name": file_name,
            "mime_type": mime_type,
            "file_size": file_size,
        },
    }
    if caption is not None:
        message["caption"] = caption
    return {"update_id": 8, "message": message}


def photo_update(chat_id: int) -> dict:
    return {
        "update_id": 9,
        "message": {
            "message_id": 13,
            "from": {"id": chat_id, "is_bot": False, "first_name": "Ada"},
            "chat": {"id": chat_id, "type": "private"},
            "photo": [{"file_id": "photo-1", "file_unique_id": "u1", "width": 90, "height": 90}],
        },
    }


class FakeAttachmentGateway:
    """Records the getFile/download calls so a refusal can prove it did neither."""

    def __init__(self, content: bytes, file_path: str = "files/report.pdf") -> None:
        self.content = content
        self.file_path = file_path
        self.path_calls: list[str] = []
        self.download_calls: list[str] = []

    async def get_file_path(self, file_id: str) -> str:
        self.path_calls.append(file_id)
        return self.file_path

    async def download_file(self, file_path: str) -> bytes:
        self.download_calls.append(file_path)
        return self.content


def build_pdf(text: str) -> bytes:
    """Build a minimal single-page PDF that pypdf can extract text from."""
    escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    stream = f"BT /F1 12 Tf 72 720 Td ({escaped}) Tj ET".encode("latin-1")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
            b"/Resources << /Font << /F1 5 0 R >> >> >>"
        ),
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref_at = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += b"trailer\n"
    out += f"<< /Size {len(objects) + 1} /Root 1 0 R >>\n".encode()
    out += f"startxref\n{xref_at}\n%%EOF\n".encode()
    return bytes(out)


def make_container(
    facts: list[dict[str, object]] | None = None,
    with_llm: bool = True,
    secret: str = "",
    attachment_gateway: object | None = None,
):
    channel = RecordingReplyChannel()
    container, llm, channel = build_test_container(
        facts
        if facts is not None
        else [{"text": "Ada is allergic to peanuts", "importance": 1.0}],
        reply_channel=channel,
        with_llm=with_llm,
        webhook_secret=secret,
        attachment_gateway=attachment_gateway,
    )
    return container, llm, channel


def make_client(secret: str = "", with_llm: bool = True):
    container, _, channel = make_container(with_llm=with_llm, secret=secret)
    client = TestClient(create_app(container=container))
    return client, channel


def test_a_valid_update_is_handled_and_replies_on_the_channel() -> None:
    client, channel = make_client(secret="s3cret")

    response = client.post("/webhooks/telegram/s3cret", json=update(555, "hello"))

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["handled"] is True
    assert len(channel.sent) == 1
    recipient, text = channel.sent[0]
    assert recipient == "555"
    assert text.startswith("I have no memory of you.")


def test_internal_memory_plumbing_is_not_shown_to_users() -> None:
    """Real users saw "memory: 1 accepted, persisting" and asked what it was.

    This replaces an earlier test asserting the receipt was present. Showing
    plumbing in the chat was the wrong default, so the contract changed.
    """
    client, channel = make_client(secret="s3cret")

    client.post("/webhooks/telegram/s3cret", json=update(555, "I am allergic to peanuts"))

    _, text = channel.sent[0]
    assert "memory:" not in text
    assert "persisting" not in text


def test_a_wrong_secret_is_refused_and_nothing_is_sent() -> None:
    client, channel = make_client(secret="s3cret")

    response = client.post("/webhooks/telegram/wrong-secret", json=update(555, "hello"))

    assert response.status_code == 200
    assert response.json() == {"ok": False, "reason": "bad_secret"}
    assert channel.sent == []


def test_an_empty_update_is_acknowledged_without_handling() -> None:
    client, channel = make_client(secret="s3cret")

    response = client.post("/webhooks/telegram/s3cret", json={"update_id": 2})

    assert response.status_code == 200
    assert response.json() == {"ok": True, "handled": False}
    assert channel.sent == []


def test_a_bot_sender_is_ignored() -> None:
    client, channel = make_client(secret="s3cret")
    payload = update(555, "hello")
    payload["message"]["from"]["is_bot"] = True

    response = client.post("/webhooks/telegram/s3cret", json=payload)

    assert response.json() == {"ok": True, "handled": False}
    assert channel.sent == []


def test_a_failing_turn_apologises_instead_of_silently_dropping_the_message() -> None:
    client, channel = make_client(secret="s3cret", with_llm=False)

    response = client.post("/webhooks/telegram/s3cret", json=update(555, "hello"))

    assert response.status_code == 200
    body = response.json()
    assert body == {"ok": True, "handled": True, "accepted": True}
    # The turn failed in the background, and the person still hears about it,
    # exactly once.
    assert len(channel.sent) == 1
    _, text = channel.sent[0]
    assert "briefly unavailable" in text
    assert "Nothing you said was lost" in text


def test_a_typing_indicator_is_shown_before_the_reply() -> None:
    """Turns take seconds, and real users read the silence as a frozen bot."""
    client, channel = make_client(secret="s3cret")

    client.post("/webhooks/telegram/s3cret", json=update(555, "hello there"))

    assert channel.typing == ["555"]
    assert len(channel.sent) == 1


def test_a_command_skips_the_typing_indicator_because_it_is_instant() -> None:
    client, channel = make_client(secret="s3cret")

    client.post("/webhooks/telegram/s3cret", json=update(555, "/start"))

    assert channel.typing == []
    # /start is delivered as the welcome picture, carrying the reply as caption.
    assert len(channel.photos) == 1
    image, caption, markup = channel.photos[0]
    assert image.endswith("cheta-welcome.png")
    assert caption and "/memories" in caption
    assert markup is not None
    assert len(channel.sent) == 0


# ------------------------------------------------------- feature 1: keyboards


def test_start_attaches_an_inline_keyboard_with_the_three_buttons() -> None:
    client, channel = make_client(secret="s3cret")

    client.post("/webhooks/telegram/s3cret", json=update(555, "/start"))

    markup = channel.markups[0]
    assert markup is not None
    rows = markup["inline_keyboard"]
    buttons = [button for row in rows for button in row]
    assert [button["text"] for button in buttons] == [
        "What I know about you",
        "Export my memory",
        "Forget one",
    ]
    assert [button["callback_data"] for button in buttons] == [
        "mem:list",
        "mem:export",
        "mem:forget",
    ]


def test_help_attaches_the_same_keyboard() -> None:
    client, channel = make_client(secret="s3cret")

    client.post("/webhooks/telegram/s3cret", json=update(555, "/help"))

    assert channel.markups[0] is not None


def test_an_ordinary_reply_carries_no_keyboard() -> None:
    """Buttons appear where they make sense, never on a normal answer."""
    client, channel = make_client(secret="s3cret")

    client.post("/webhooks/telegram/s3cret", json=update(555, "hello"))

    assert channel.markups == [None]


def test_the_list_callback_answers_and_sends_the_listing_without_the_model() -> None:
    client, channel = make_client(secret="s3cret")
    client.post("/webhooks/telegram/s3cret", json=update(555, "I am allergic to peanuts"))
    llm = client.app.state.container.llm_gateway
    reply_calls_before = llm.reply_calls

    response = client.post("/webhooks/telegram/s3cret", json=callback_update(555, "mem:list"))

    assert response.json() == {"ok": True, "handled": True, "accepted": True}
    assert channel.callback_answers == [("cb-1", None)]
    assert "You are allergic to peanuts" in channel.sent[-1][1]
    assert llm.reply_calls == reply_calls_before


def test_the_forget_callback_shows_one_numbered_button_per_note() -> None:
    client, channel = make_client(secret="s3cret")
    client.post("/webhooks/telegram/s3cret", json=update(555, "I am allergic to peanuts"))

    client.post("/webhooks/telegram/s3cret", json=callback_update(555, "mem:forget"))

    markup = channel.markups[-1]
    assert markup is not None
    buttons = [button for row in markup["inline_keyboard"] for button in row]
    assert buttons[0]["text"].startswith("1. ")
    assert buttons[0]["callback_data"].startswith("mem:forget:")
    assert "peanuts" in buttons[0]["text"]


def test_a_forget_callback_retires_the_first_note() -> None:
    client, channel = make_client(secret="s3cret")
    client.post("/webhooks/telegram/s3cret", json=update(555, "I am allergic to peanuts"))
    container = client.app.state.container
    user = container.conversation_service._users.get_by_identity("telegram", "555")
    before = container.conversation_service._memories.list_for_user(user.id, None, 10)
    assert before[0].status == "active"

    response = client.post("/webhooks/telegram/s3cret", json=callback_update(555, "mem:forget:1"))

    assert response.json() == {"ok": True, "handled": True, "accepted": True}
    assert channel.callback_answers == [("cb-1", None)]
    assert "will not bring up" in channel.sent[-1][1]
    after = container.conversation_service._memories.get_by_id(before[0].id)
    assert after.status == "superseded"


def test_a_callback_query_is_never_stored_as_a_conversation_turn() -> None:
    client, _ = make_client(secret="s3cret")
    client.post("/webhooks/telegram/s3cret", json=update(555, "I am allergic to peanuts"))
    container = client.app.state.container
    user = container.conversation_service._users.get_by_identity("telegram", "555")
    before = container.conversation_service._turns.count_for_user(user.id)

    client.post("/webhooks/telegram/s3cret", json=callback_update(555, "mem:list"))

    after = container.conversation_service._turns.count_for_user(user.id)
    assert after == before


def test_the_export_callback_sends_the_passport_as_a_document() -> None:
    client, channel = make_client(secret="s3cret")
    client.post("/webhooks/telegram/s3cret", json=update(555, "I am allergic to peanuts"))

    client.post("/webhooks/telegram/s3cret", json=callback_update(555, "mem:export"))

    assert channel.callback_answers == [("cb-1", None)]
    recipient, filename, content = channel.documents[-1]
    assert recipient == "555"
    assert filename == "ranti-passport-555.json"
    passport = json.loads(content)
    assert passport["format"] == "ranti.memory-passport.v1"
    assert passport["memories"][0]["text"] == "Ada is allergic to peanuts"


# ---------------------------------------------------- feature 2: attachments


def test_a_pdf_document_is_downloaded_extracted_and_used_in_the_turn() -> None:
    pdf = build_pdf("Ada keeps a spare key under the blue flowerpot.")
    gateway = FakeAttachmentGateway(pdf)
    container, llm, channel = make_container(
        facts=[], secret="s3cret", attachment_gateway=gateway
    )
    client = TestClient(create_app(container=container))

    response = client.post(
        "/webhooks/telegram/s3cret",
        json=document_update(555, "notes.pdf", len(pdf)),
    )

    assert response.status_code == 200
    assert response.json()["handled"] is True
    assert response.json()["accepted"] is True
    assert gateway.path_calls == ["file-1"]
    assert gateway.download_calls == ["files/report.pdf"]
    # The model actually saw the extracted text, which is what "used in the
    # turn" means. It is carried as the document material in the system prompt,
    # with the caption as the user's question.
    assert llm is not None
    assert "Ada keeps a spare key under the blue flowerpot." in llm.system_messages[-1]
    assert "notes.pdf" in llm.system_messages[-1]
    assert len(channel.sent) == 1
    # Extracting is not remembering: the raw document text is not a memory.
    user = container.conversation_service._users.get_by_identity("telegram", "555")
    stored = container.conversation_service._memories.list_for_user(user.id, None, 50)
    assert all("spare key under the blue flowerpot" not in record.text for record in stored)


def test_a_document_with_no_caption_is_still_processed() -> None:
    pdf = build_pdf("The launch code is stored in the blue cabinet.")
    gateway = FakeAttachmentGateway(pdf)
    container, llm, channel = make_container(
        facts=[], secret="s3cret", attachment_gateway=gateway
    )
    client = TestClient(create_app(container=container))

    response = client.post(
        "/webhooks/telegram/s3cret",
        json=document_update(555, "notes.pdf", len(pdf), caption=None),
    )

    assert response.json()["accepted"] is True
    assert llm is not None
    assert "blue cabinet" in llm.system_messages[-1]
    assert len(channel.sent) == 1


DOCUMENT_MARKER = "DOCUMENT ATTACHED"
EXTRACT_MARKER = "extract durable facts"
ADJUDICATE_MARKER = "compare one remembered fact"
DOCUMENT_FENCE = re.compile(r"<<<[0-9a-f]+>>>\n(.*?)\n<<<END", re.DOTALL)


class DocumentAwareLlm:
    """A model that answers from a document only when the prompt says it exists.

    That is the real failure shape: the turn carried a readable document, but
    nothing in the prompt said so, so the model answered correctly from the
    material and then denied that any document was attached. With the prompt
    statement present it answers from the document and never denies it.
    """

    def __init__(self) -> None:
        self.system_messages: list[str] = []

    async def complete(self, messages, *, temperature: float = 0.2, max_tokens: int = 800):
        system = messages[0].content if messages else ""
        if EXTRACT_MARKER in system:
            return CompletionSchema(text="[]", provider="fake", model="fake-1")
        if ADJUDICATE_MARKER in system:
            return CompletionSchema(text="DIFFERENT", provider="fake", model="fake-1")
        self.system_messages.append(system)
        if DOCUMENT_MARKER in system:
            match = DOCUMENT_FENCE.search(system)
            body = " ".join(match.group(1).split()) if match else ""
            return CompletionSchema(
                text=f"Reading the attached document: {body}",
                provider="fake",
                model="fake-1",
            )
        return CompletionSchema(
            text="I don't see any document attached to your message.",
            provider="fake",
            model="fake-1",
        )

    async def complete_with_tools(self, messages, tools, *, temperature: float = 0.2, max_tokens: int = 800):
        return await self.complete(messages, temperature=temperature, max_tokens=max_tokens)


def test_a_document_with_a_caption_produces_exactly_one_reply() -> None:
    pdf = build_pdf("The quarterly numbers are in this deck.")
    gateway = FakeAttachmentGateway(pdf)
    container, _, channel = make_container(
        facts=[], secret="s3cret", attachment_gateway=gateway
    )
    client = TestClient(create_app(container=container))

    response = client.post(
        "/webhooks/telegram/s3cret",
        json=document_update(
            555,
            "deck.pdf",
            len(pdf),
            caption="I have uploaded the ppt, did you parse it?",
        ),
    )

    assert response.json()["accepted"] is True
    # One update, one answer. The caption must not be run again as its own turn.
    assert len(channel.sent) == 1
    assert channel.sent[0][0] == "555"
    assert channel.typing == ["555"]


def test_the_document_reply_answers_from_the_document_and_never_denies_it() -> None:
    pdf = build_pdf("The launch code is stored in the blue cabinet.")
    gateway = FakeAttachmentGateway(pdf)
    container, _, channel = make_container(
        facts=[], secret="s3cret", attachment_gateway=gateway
    )
    model = DocumentAwareLlm()
    container.conversation_service._llm = model
    client = TestClient(create_app(container=container))

    client.post(
        "/webhooks/telegram/s3cret",
        json=document_update(
            555,
            "deck.pdf",
            len(pdf),
            caption="I have uploaded the ppt, did you parse it?",
        ),
    )

    reply = channel.sent[-1][1]
    assert DOCUMENT_MARKER in model.system_messages[-1]
    # The answer came from the document...
    assert "blue cabinet" in reply
    # ...and never claims the document is missing in the same exchange.
    assert "no document" not in reply.lower()
    assert "don't see any document" not in reply.lower()


def test_an_unsupported_document_is_refused_specifically() -> None:
    gateway = FakeAttachmentGateway(b"PK\x03\x04")
    container, _, channel = make_container(
        facts=[], secret="s3cret", attachment_gateway=gateway
    )
    client = TestClient(create_app(container=container))

    response = client.post(
        "/webhooks/telegram/s3cret",
        json=document_update(555, "archive.zip", 1024, mime_type="application/zip"),
    )

    assert response.json()["accepted"] is True
    text = channel.sent[-1][1]
    lowered = text.lower()
    assert "cannot read archive.zip" in lowered
    assert "i can read zip" not in lowered
    assert "not supported" in lowered
    # A refusal must not download what it cannot parse.
    assert gateway.path_calls == []
    assert gateway.download_calls == []


def test_an_image_is_refused_by_name_and_never_claims_to_read_it() -> None:
    client, channel = make_client(secret="s3cret")

    client.post("/webhooks/telegram/s3cret", json=photo_update(555))

    text = channel.sent[-1][1]
    lowered = text.lower()
    assert "cannot read images" in lowered
    assert "i can read images" not in lowered
    assert "documents only" in lowered


def test_a_document_over_the_cap_is_refused_before_download() -> None:
    gateway = FakeAttachmentGateway(b"x")
    container, _, channel = make_container(
        facts=[], secret="s3cret", attachment_gateway=gateway
    )
    client = TestClient(create_app(container=container))

    response = client.post(
        "/webhooks/telegram/s3cret",
        json=document_update(555, "huge.pdf", 21 * 1024 * 1024),
    )

    assert response.json()["handled"] is True
    assert "20 MB" in channel.sent[-1][1]
    assert "did not download" in channel.sent[-1][1]
    assert gateway.path_calls == []
    assert gateway.download_calls == []


# ------------------------------------- acknowledgement, dedup and delivery order


def _asgi_scope(path: str, body: bytes) -> dict:
    return {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": [
            (b"host", b"testserver"),
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode()),
        ],
        "client": ("testclient", 50000),
        "server": ("testserver", 80),
    }


async def _post_update(app, payload: dict, on_response_body=None) -> list[dict]:
    """Drive the ASGI app directly so the response body can be observed early."""
    body = json.dumps(payload).encode()
    scope = _asgi_scope("/webhooks/telegram/s3cret", body)
    messages: list[dict] = []

    async def receive() -> dict:
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message: dict) -> None:
        messages.append(message)
        if (
            on_response_body is not None
            and message["type"] == "http.response.body"
            and not message.get("more_body")
        ):
            on_response_body()

    await app(scope, receive, send)
    return messages


async def test_the_webhook_route_returns_without_awaiting_the_turn() -> None:
    """A turn that never finishes must not hold the 200 hostage.

    Telegram retries an unacknowledged update. If the route awaited the turn,
    the response body would arrive only after the turn ended; this test fails
    unless the body is already on the wire while the turn is still running.
    """
    container, _, _ = make_container(secret="s3cret")
    app = create_app(container=container)
    release = asyncio.Event()

    async def never_finishes(*args, **kwargs) -> None:
        await release.wait()

    container.conversation_service.handle_surface_turn = never_finishes

    body_sent = asyncio.Event()
    task = asyncio.create_task(
        _post_update(app, update(555, "hello"), on_response_body=body_sent.set)
    )
    await asyncio.wait_for(body_sent.wait(), timeout=1.0)
    assert not task.done(), "the route was still awaiting the turn after the body"

    release.set()
    messages = await asyncio.wait_for(task, timeout=1.0)
    body_message = next(m for m in messages if m["type"] == "http.response.body")
    assert json.loads(body_message["body"]) == {
        "ok": True,
        "handled": True,
        "accepted": True,
    }


def test_the_same_update_id_delivered_twice_produces_exactly_one_reply() -> None:
    client, channel = make_client(secret="s3cret")
    delivered = update(555, "hello")

    first = client.post("/webhooks/telegram/s3cret", json=delivered)
    second = client.post("/webhooks/telegram/s3cret", json=delivered)

    assert first.json() == {"ok": True, "handled": True, "accepted": True}
    assert second.json() == {"ok": True, "handled": True, "duplicate": True}
    assert len(channel.sent) == 1


def test_a_redelivered_update_after_a_restart_is_still_seen(tmp_path) -> None:
    """The seen id must outlive the process, exactly when a redeploy causes it."""
    database_path = str(tmp_path / "ranti.db")
    delivered = update(555, "hello")

    first_container, _, first_channel = build_test_container(
        [], webhook_secret="s3cret", database_path=database_path
    )
    with TestClient(create_app(container=first_container)) as first_client:
        first_client.post("/webhooks/telegram/s3cret", json=delivered)
    assert len(first_channel.sent) == 1

    second_container, _, second_channel = build_test_container(
        [], webhook_secret="s3cret", database_path=database_path
    )
    with TestClient(create_app(container=second_container)) as second_client:
        response = second_client.post("/webhooks/telegram/s3cret", json=delivered)

    assert response.json() == {"ok": True, "handled": True, "duplicate": True}
    assert second_channel.sent == []


async def test_two_messages_from_one_person_are_answered_in_order() -> None:
    container, _, _ = make_container(secret="s3cret")
    app = create_app(container=container)
    order: list[str] = []
    first_started = asyncio.Event()

    async def slow_first(*args, **kwargs) -> None:
        text = kwargs["text"]
        if text == "first":
            first_started.set()
            await asyncio.sleep(0.05)
        order.append(text)

    container.conversation_service.handle_surface_turn = slow_first

    first = asyncio.create_task(_post_update(app, update(555, "first", update_id=11)))
    await asyncio.wait_for(first_started.wait(), timeout=1.0)
    second = asyncio.create_task(_post_update(app, update(555, "second", update_id=12)))
    await asyncio.gather(first, second)

    assert order == ["first", "second"]


async def test_two_people_are_not_serialised_behind_each_other() -> None:
    container, _, _ = make_container(secret="s3cret")
    app = create_app(container=container)
    started = 0
    both_started = asyncio.Event()

    async def wait_for_the_other(*args, **kwargs) -> None:
        nonlocal started
        started += 1
        if started == 2:
            both_started.set()
        await asyncio.wait_for(both_started.wait(), timeout=1.0)

    container.conversation_service.handle_surface_turn = wait_for_the_other

    first = asyncio.create_task(_post_update(app, update(555, "a", update_id=21)))
    second = asyncio.create_task(_post_update(app, update(556, "b", update_id=22)))
    await asyncio.gather(first, second)

    assert started == 2

