"""Telegram webhook contract. The bot must always answer 200 or Telegram retries."""

from __future__ import annotations

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


def make_client(secret: str = "", with_llm: bool = True):
    channel = RecordingReplyChannel()
    container, _, _ = build_test_container(
        [{"text": "Ada is allergic to peanuts", "importance": 1.0}],
        reply_channel=channel,
        with_llm=with_llm,
        webhook_secret=secret,
    )
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


def test_a_non_text_update_is_acknowledged_without_handling() -> None:
    client, channel = make_client(secret="s3cret")

    response = client.post(
        "/webhooks/telegram/s3cret",
        json={"update_id": 2, "message": {"chat": {"id": 1}, "photo": [{"file_id": "x"}]}},
    )

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
