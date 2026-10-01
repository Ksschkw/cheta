"""SSRF policy: a model-directed fetch must not reach internal addresses.

Every case here is refused before a connection is attempted, and a redirect is
refused on the hop that points inward. The transport is an injected fake, so no
test makes a real outbound call.
"""

from __future__ import annotations

import httpx
import pytest

from core.errors import BlockedUrlError
from core.gateways.web_gateway import WebGateway
from core.resilience import Boundary, ResiliencePolicy


def public_resolver(_: str) -> list[str]:
    return ["93.184.216.34"]


def make_gateway(handler) -> WebGateway:
    return WebGateway(
        boundary=Boundary(
            ResiliencePolicy(dependency="web", timeout_seconds=2.0, max_attempts=1)
        ),
        resolver=public_resolver,
        client_factory=lambda **_: httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ),
    )


def no_network(request: httpx.Request) -> httpx.Response:
    raise AssertionError(f"a request was attempted to {request.url}")


BLOCKED_URLS = [
    "file:///etc/passwd",
    "ftp://example.com/x",
    "javascript:alert(1)",
    "http://localhost/",
    "http://localhost:8080/admin",
    "http://127.0.0.1/",
    "http://127.0.0.1:8000/health",
    "http://10.0.0.5/",
    "http://10.255.255.1/",
    "http://192.168.1.10/",
    "http://169.254.169.254/latest/meta-data/",
    "http://[::1]/",
    "http://0.0.0.0/",
]


@pytest.mark.parametrize("url", BLOCKED_URLS)
def test_a_blocked_url_is_refused_before_any_request(url: str) -> None:
    gateway = make_gateway(no_network)

    with pytest.raises(BlockedUrlError):
        gateway.validate_url(url)


def test_a_private_hostname_is_refused_after_resolution() -> None:
    gateway = WebGateway(
        boundary=Boundary(ResiliencePolicy(dependency="web", max_attempts=1)),
        resolver=lambda _: ["192.168.0.7"],
    )

    with pytest.raises(BlockedUrlError):
        gateway.validate_url("http://internal.example/")


def test_a_non_resolving_host_is_refused_as_blocked() -> None:
    def boom(_: str) -> list[str]:
        raise OSError("name or service not known")

    gateway = WebGateway(
        boundary=Boundary(ResiliencePolicy(dependency="web", max_attempts=1)),
        resolver=boom,
    )

    with pytest.raises(BlockedUrlError):
        gateway.validate_url("http://does-not-resolve.invalid/")


async def test_a_public_page_is_fetched() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text="<html><body><p>hello world</p></body></html>",
        )

    gateway = make_gateway(handler)

    page = await gateway.fetch_page("https://example.com/page", 1024)

    assert page.text == "hello world"
    assert page.status == 200


async def test_a_redirect_to_an_internal_address_is_refused() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "example.com":
            return httpx.Response(
                302, headers={"location": "http://169.254.169.254/latest/meta-data/"}
            )
        raise AssertionError(f"the internal host was reached: {request.url}")

    gateway = make_gateway(handler)

    with pytest.raises(BlockedUrlError):
        await gateway.fetch_page("https://example.com/", 1024)


async def test_a_redirect_to_a_public_page_is_followed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/start":
            return httpx.Response(302, headers={"location": "https://example.com/final"})
        return httpx.Response(
            200, headers={"content-type": "text/plain"}, text="arrived"
        )

    gateway = make_gateway(handler)

    page = await gateway.fetch_page("https://example.com/start", 1024)

    assert page.text == "arrived"
    assert page.final_url.endswith("/final")


def test_a_literal_public_address_is_allowed() -> None:
    gateway = WebGateway(
        boundary=Boundary(ResiliencePolicy(dependency="web", max_attempts=1))
    )

    # 93.184.216.34 is example.com's documented address.
    gateway.validate_url("https://93.184.216.34/")
