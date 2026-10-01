"""Keep-alive behaviour and when it is allowed to run.

Holding a free instance awake is a deliberate trade: one always-on free service
consumes essentially the whole monthly instance-hour allowance, so it must be
easy to turn off and must never run against a local URL where it would be a
pointless loop.
"""

from __future__ import annotations

import httpx
import pytest

from core.config import Settings
from core.keepalive import KeepAlivePinger


def test_a_non_https_target_is_refused() -> None:
    with pytest.raises(ValueError):
        KeepAlivePinger("http://127.0.0.1:8000")


def test_only_an_https_deployment_is_kept_awake() -> None:
    local = Settings(database_path=":memory:", public_base_url="http://127.0.0.1:8000")
    hosted = Settings(
        database_path=":memory:", public_base_url="https://ranti-gkn7.onrender.com"
    )

    assert local.keepalive_target is None
    assert hosted.keepalive_target == "https://ranti-gkn7.onrender.com"


def test_keep_alive_can_be_switched_off_for_a_hosted_deployment() -> None:
    disabled = Settings.from_env(
        {
            "PUBLIC_BASE_URL": "https://ranti-gkn7.onrender.com",
            "RANTI_KEEPALIVE": "0",
        }
    )
    enabled = Settings.from_env({"PUBLIC_BASE_URL": "https://ranti-gkn7.onrender.com"})

    assert disabled.keepalive_target is None
    assert enabled.keepalive_target == "https://ranti-gkn7.onrender.com"


async def test_a_successful_ping_reports_its_status_code() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/health"
        return httpx.Response(200, json={"status": "ok"})

    pinger = KeepAlivePinger(
        "https://example.invalid",
        client_factory=lambda **kwargs: httpx.AsyncClient(
            transport=httpx.MockTransport(handler), **kwargs
        ),
    )

    assert await pinger.ping_once() == 200


async def test_a_failing_ping_is_swallowed_not_raised() -> None:
    """A keep-alive that can crash the app it keeps alive is worse than none."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host")

    pinger = KeepAlivePinger(
        "https://example.invalid",
        client_factory=lambda **kwargs: httpx.AsyncClient(
            transport=httpx.MockTransport(handler), **kwargs
        ),
    )

    assert await pinger.ping_once() is None


async def test_start_and_stop_are_idempotent_and_report_state() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200)

    pinger = KeepAlivePinger(
        "https://example.invalid",
        interval_seconds=60,
        client_factory=lambda **kwargs: httpx.AsyncClient(
            transport=httpx.MockTransport(handler), **kwargs
        ),
    )

    assert pinger.running is False
    pinger.start()
    pinger.start()
    assert pinger.running is True
    await pinger.stop()
    assert pinger.running is False
    await pinger.stop()
