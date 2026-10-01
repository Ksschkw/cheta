"""Telegram webhook contract. The bot must always answer 200 or Telegram retries.

Covers the two features added for a real user complaint: inline keyboard buttons
with callback handling, and honest local attachment reading.
"""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from main import create_app
from tests.routers.test_chat_and_memory_routers import (
    RecordingReplyChannel,
    build_test_container,
)


def update(chat_id: int, text: str, first_name: str = "Ada") -> dict:
    return {
        "update_id": 1,
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
    assert body["ok"] is True
    assert body["degraded"] is True
    assert body["reason"] == "dependency_unavailable"
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

    assert response.json() == {"ok": True, "handled": True, "callback": True}
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

    assert response.json() == {"ok": True, "handled": True, "callback": True}
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
    assert response.json()["attachment"] is True
    assert gateway.path_calls == ["file-1"]
    assert gateway.download_calls == ["files/report.pdf"]
    # The model actually saw the extracted text, which is what "used in the
    # turn" means.
    assert llm is not None
    assert "Ada keeps a spare key under the blue flowerpot." in llm.user_messages[-1]
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

    assert response.json()["attachment"] is True
    assert llm is not None
    assert "blue cabinet" in llm.user_messages[-1]
    assert len(channel.sent) == 1


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

    assert response.json()["refused"] is True
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
