"""Tool-level behaviour of the web tools, including honest degradation."""

from __future__ import annotations

import httpx
import pytest

from core.errors import BlockedUrlError, DependencyUnavailableError
from core.gateways.web_gateway import WebGateway
from core.resilience import Boundary, ResiliencePolicy
from core.tools.tool_registry import ToolContext, ToolRegistry
from core.tools.web_tools import build_web_tools
from schemas.web_schema import FetchedPageSchema, SearchResultSchema

CTX = ToolContext(user_id="u1", namespace="ns", surface="web")


class FakeWebGateway:
    def __init__(
        self,
        *,
        page: FetchedPageSchema | None = None,
        search_results=None,
        search_error: bool = False,
        fetch_error: BlockedUrlError | None = None,
        robots: str | None = "",
    ) -> None:
        self._page = page
        self._search_results = search_results or []
        self._search_error = search_error
        self._fetch_error = fetch_error
        self._robots = robots
        self.fetched: list[str] = []
        self.validated: list[str] = []

    def validate_url(self, url: str) -> None:
        self.validated.append(url)

    async def fetch_page(self, url: str, max_bytes: int) -> FetchedPageSchema:
        if self._fetch_error is not None:
            raise self._fetch_error
        self.fetched.append(url)
        assert self._page is not None
        return self._page

    async def fetch_robots(self, origin: str) -> str | None:
        return self._robots

    async def search(self, query: str, limit: int) -> list[SearchResultSchema]:
        if self._search_error:
            raise DependencyUnavailableError("web", "backend rate limited")
        return self._search_results


def registry_for(gateway) -> ToolRegistry:
    return ToolRegistry(build_web_tools(gateway))


async def test_fetch_url_returns_readable_text() -> None:
    page = FetchedPageSchema(
        url="https://example.com/",
        final_url="https://example.com/",
        content_type="text/html",
        text="A readable page.",
        byte_count=16,
    )
    registry = registry_for(FakeWebGateway(page=page))

    result = await registry.execute("fetch_url", {"url": "https://example.com/"}, CTX)

    assert result.ok is True
    assert "A readable page." in result.content


async def test_fetch_url_reports_a_blocked_url_as_a_failure() -> None:
    registry = registry_for(
        FakeWebGateway(fetch_error=BlockedUrlError("that address is not public"))
    )

    result = await registry.execute("fetch_url", {"url": "http://127.0.0.1/"}, CTX)

    assert result.ok is False
    assert "refused" in (result.error or "")


async def test_fetch_url_refuses_binary_content() -> None:
    page = FetchedPageSchema(
        url="https://example.com/image.png",
        final_url="https://example.com/image.png",
        content_type="image/png",
        text="",
        byte_count=10,
    )
    registry = registry_for(FakeWebGateway(page=page))

    result = await registry.execute(
        "fetch_url", {"url": "https://example.com/image.png"}, CTX
    )

    assert result.ok is False
    assert "not readable text" in (result.error or "")


async def test_search_failure_is_an_honest_failure_not_an_empty_answer() -> None:
    registry = registry_for(FakeWebGateway(search_error=True))

    result = await registry.execute("web_search", {"query": "anything"}, CTX)

    assert result.ok is False
    assert "could not be reached" in (result.error or "")
    assert "rate limiting" in (result.error or "")


async def test_search_with_no_results_is_also_an_honest_failure() -> None:
    registry = registry_for(FakeWebGateway(search_results=[]))

    result = await registry.execute("web_search", {"query": "anything"}, CTX)

    assert result.ok is False
    assert "no usable results" in (result.error or "")


async def test_search_returns_bounded_titles_urls_and_snippets() -> None:
    results = [
        SearchResultSchema(title=f"Title {index}", url=f"https://x{index}.example/", snippet="s")
        for index in range(3)
    ]
    registry = registry_for(FakeWebGateway(search_results=results))

    result = await registry.execute("web_search", {"query": "x"}, CTX)

    assert result.ok is True
    assert "Title 0" in result.content
    assert "https://x0.example/" in result.content
    assert "not the full pages" in result.content


async def test_the_search_backend_is_pluggable() -> None:
    """A keyed provider can be injected later without touching the tool."""

    class FakeBackend:
        def __init__(self) -> None:
            self.calls: list[tuple[str, int]] = []

        async def search(self, query: str, limit: int) -> list[SearchResultSchema]:
            self.calls.append((query, limit))
            return [SearchResultSchema(title="t", url="https://e.example/", snippet="s")]

    backend = FakeBackend()
    gateway = WebGateway(
        boundary=Boundary(
            ResiliencePolicy(dependency="web", timeout_seconds=2.0, max_attempts=1)
        ),
        search_backend=backend,
    )

    results = await gateway.search("anything", 3)

    assert backend.calls == [("anything", 3)]
    assert results[0].title == "t"


# --------------------------------------------------------------- crawl bounds


def crawl_gateway(handler) -> WebGateway:
    return WebGateway(
        boundary=Boundary(
            ResiliencePolicy(dependency="web", timeout_seconds=2.0, max_attempts=1)
        ),
        resolver=lambda _: ["93.184.216.34"],
        client_factory=lambda **_: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


async def test_crawl_refuses_an_internal_address_discovered_on_a_followed_link() -> None:
    hosts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        hosts.append(request.url.host)
        if request.url.path == "/robots.txt":
            return httpx.Response(
                200,
                headers={"content-type": "text/plain"},
                text="User-agent: *\nAllow: /\n",
            )
        if request.url.path == "/page":
            return httpx.Response(
                200,
                headers={"content-type": "text/html"},
                text=(
                    "<html><body><p>Start page.</p>"
                    '<a href="http://169.254.169.254/latest/meta-data/">metadata</a>'
                    '<a href="https://example.com/next">next</a>'
                    "</body></html>"
                ),
            )
        if request.url.path == "/next":
            return httpx.Response(
                200, headers={"content-type": "text/html"}, text="<p>Second page.</p>"
            )
        raise AssertionError(f"unexpected request to {request.url}")

    registry = registry_for(crawl_gateway(handler))

    # same_origin is false here on purpose: the crawler is allowed to consider
    # off-origin links, and the internal one must still be refused by the SSRF
    # policy before any request is made.
    result = await registry.execute(
        "crawl",
        {"url": "https://example.com/page", "max_pages": 3, "same_origin": False},
        CTX,
    )

    assert result.ok is True, result.error
    assert "Start page." in result.content
    assert "Second page." in result.content
    # The internal link is named as refused, and no request reached it.
    assert "Refused" in result.content
    assert "169.254.169.254" in result.content
    assert set(hosts) == {"example.com"}


async def test_crawl_does_not_leave_the_origin_by_default() -> None:
    hosts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        hosts.append(request.url.host)
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /\n")
        if request.url.path == "/page":
            return httpx.Response(
                200,
                headers={"content-type": "text/html"},
                text='<a href="https://other.example/away">away</a><p>only page</p>',
            )
        raise AssertionError(f"left the origin: {request.url}")

    registry = registry_for(crawl_gateway(handler))

    result = await registry.execute(
        "crawl", {"url": "https://example.com/page", "max_pages": 3}, CTX
    )

    assert result.ok is True
    assert hosts == ["example.com", "example.com"]
    assert "only page" in result.content
