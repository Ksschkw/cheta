"""The Telegram gateway's real HTTP methods, against an injected fake client.

The router tests use a recording reply channel, which proves the service calls
the right protocol methods but not that the gateway builds the right requests.
These cover the wire shape for the new keyboard, document and file calls.
"""

from __future__ import annotations

from typing import Any

from core.gateways.telegram_gateway import TelegramGateway
from core.resilience import Boundary, ResiliencePolicy, StructuredLogMetricSink


class FakeResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload
        self.content = b""

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self._payload


class RecordingClient:
    """A stand-in httpx.AsyncClient used as an async context manager."""

    requests: list[dict[str, Any]] = []
    payload: dict[str, Any] = {"ok": True, "result": {}}

    def __init__(self, **kwargs: Any) -> None:
        self._kwargs = kwargs

    async def __aenter__(self) -> RecordingClient:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    async def post(
        self,
        url: str,
        json: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
        files: dict[str, Any] | None = None,
    ) -> FakeResponse:
        RecordingClient.requests.append(
            {"url": url, "json": json, "data": data, "files": files}
        )
        return FakeResponse(RecordingClient.payload)

    async def get(self, url: str) -> FakeResponse:
        RecordingClient.requests.append({"url": url, "get": True})
        response = FakeResponse({})
        response.content = b"%PDF-1.4 fake bytes"
        return response


def make_gateway() -> TelegramGateway:
    RecordingClient.requests = []
    RecordingClient.payload = {"ok": True, "result": {}}
    boundary = Boundary(
        ResiliencePolicy(
            dependency="telegram",
            timeout_seconds=5.0,
            failure_threshold=3,
            reset_timeout_seconds=15.0,
            max_attempts=1,
            max_concurrency=2,
        ),
        StructuredLogMetricSink("telegram"),
    )
    return TelegramGateway(
        token="123456:test", boundary=boundary, client_factory=RecordingClient
    )


async def test_send_message_attaches_the_inline_keyboard() -> None:
    gateway = make_gateway()

    await gateway.send_message("555", "hi", reply_markup={"inline_keyboard": [["x"]]})

    assert RecordingClient.requests[-1]["json"]["reply_markup"] == {
        "inline_keyboard": [["x"]]
    }


async def test_send_message_without_a_keyboard_omits_the_key() -> None:
    gateway = make_gateway()

    await gateway.send_message("555", "hi")

    assert "reply_markup" not in RecordingClient.requests[-1]["json"]


async def test_send_document_posts_the_bytes_as_multipart() -> None:
    gateway = make_gateway()

    await gateway.send_document("555", "passport.json", b'{"a":1}', caption="export")

    request = RecordingClient.requests[-1]
    assert request["url"].endswith("/sendDocument")
    assert request["data"]["chat_id"] == "555"
    assert request["data"]["caption"] == "export"
    assert request["files"]["document"][0] == "passport.json"
    assert request["files"]["document"][1] == b'{"a":1}'


async def test_answer_callback_query_posts_the_id() -> None:
    gateway = make_gateway()

    await gateway.answer_callback_query("cb-9", text="done")

    request = RecordingClient.requests[-1]
    assert request["url"].endswith("/answerCallbackQuery")
    assert request["json"] == {"callback_query_id": "cb-9", "text": "done"}


async def test_get_file_path_returns_the_telegram_path() -> None:
    gateway = make_gateway()
    RecordingClient.payload = {"ok": True, "result": {"file_path": "docs/a.pdf"}}

    path = await gateway.get_file_path("file-1")

    assert path == "docs/a.pdf"
    assert RecordingClient.requests[-1]["json"] == {"file_id": "file-1"}


async def test_download_file_fetches_the_file_url() -> None:
    gateway = make_gateway()

    content = await gateway.download_file("docs/a.pdf")

    assert content == b"%PDF-1.4 fake bytes"
    assert RecordingClient.requests[-1]["url"].endswith("/file/bot123456:test/docs/a.pdf")
