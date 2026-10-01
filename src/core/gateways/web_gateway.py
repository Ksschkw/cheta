"""Outbound web access: one bounded, SSRF-guarded boundary for every tool.

Every request in this module goes through one ``Boundary`` (timeout, breaker,
bulkhead, bounded retry), including the search backend and robots.txt. Address
policy is enforced before a connection is attempted and again on every redirect
hop, so a link found on a page is checked exactly like the URL the person
typed.
"""

from __future__ import annotations

import ipaddress
import logging
import socket
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import quote_plus, urljoin, urlparse

from core.errors import BlockedUrlError, DependencyUnavailableError
from core.gateways.html_extract import (
    extract_links,
    extract_text,
    parse_duckduckgo_results,
)
from core.resilience import Boundary, failure_fallback
from schemas.web_schema import FetchedPageSchema, SearchResultSchema

logger = logging.getLogger("ranti.gateway.web")

DEFAULT_USER_AGENT = "RantiBot/0.1 (+https://github.com/; text-only reader)"
MAX_REDIRECTS = 3
READABLE_CONTENT_TYPES = ("text/html", "application/xhtml", "text/plain", "application/json",
                          "application/xml", "text/xml", "text/csv")


@dataclass(frozen=True)
class _RawFetch:
    """One fetch attempt, including a refusal that happened before the network."""

    status: int
    final_url: str
    content_type: str
    body: bytes = b""
    blocked_reason: str | None = None


class SearchBackendProtocol(Protocol):
    """A search provider. The keyless DuckDuckGo backend is the default.

    A keyed backend added later implements this same one-method interface and is
    injected in the composition root; the tool and the gateway do not change.
    """

    async def search(self, query: str, limit: int) -> list[SearchResultSchema]: ...


def default_resolve(host: str) -> list[str]:
    """Resolve a hostname to every address it answers with."""
    infos = socket.getaddrinfo(host, None)
    return [info[4][0] for info in infos]


def is_public_address(address: str) -> bool:
    """True only for a globally routable address.

    Loopback, private, link-local (including the cloud metadata range),
    multicast, reserved and unspecified addresses all return False. The check is
    on the numeric address, after resolution, so a hostname cannot smuggle one
    through.
    """
    try:
        ip = ipaddress.ip_address(address.split("%", 1)[0])
    except ValueError:
        return False
    if (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    ):
        return False
    return bool(ip.is_global)


class DuckDuckGoSearchBackend:
    """Keyless search through DuckDuckGo's no-JavaScript HTML endpoint."""

    ENDPOINT = "https://html.duckduckgo.com/html/?q="

    def __init__(self, fetch_markup: Callable[[str], Awaitable[str]]) -> None:
        self._fetch_markup = fetch_markup

    async def search(self, query: str, limit: int) -> list[SearchResultSchema]:
        markup = await self._fetch_markup(self.ENDPOINT + quote_plus(query))
        rows = parse_duckduckgo_results(markup)
        return [
            SearchResultSchema(title=title, url=url, snippet=snippet)
            for title, url, snippet in rows[:limit]
        ]


class WebGateway:
    """Bounded, validated HTTP GET plus a pluggable search backend."""

    def __init__(
        self,
        boundary: Boundary,
        *,
        user_agent: str = DEFAULT_USER_AGENT,
        resolver: Callable[[str], list[str]] | None = None,
        client_factory: Callable[..., Any] | None = None,
        search_backend: SearchBackendProtocol | None = None,
        timeout_seconds: float = 10.0,
    ) -> None:
        self._boundary = boundary
        self._user_agent = user_agent
        self._resolver = resolver or default_resolve
        self._client_factory = client_factory
        self._timeout = timeout_seconds
        self._search_backend = search_backend or DuckDuckGoSearchBackend(self._fetch_markup)

    # ------------------------------------------------------------------ policy

    def validate_url(self, url: str) -> None:
        """Refuse a non-http(s) scheme or a non-public destination.

        Raises ``BlockedUrlError`` before any socket is opened. Called for the
        requested URL, for every redirect target, and by the crawler for every
        link it discovers.
        """
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            raise BlockedUrlError(
                f"only http and https URLs are allowed, not {parsed.scheme or 'a relative URL'}"
            )
        host = parsed.hostname
        if not host:
            raise BlockedUrlError("the URL has no host")
        lowered = host.strip("[]").lower()
        if lowered == "localhost" or lowered.endswith(".localhost"):
            raise BlockedUrlError("localhost is not reachable from here")
        try:
            literal = ipaddress.ip_address(lowered)
        except ValueError:
            literal = None
        if literal is not None:
            # A numeric address is decided directly. It must never fall through
            # to the resolver, or a private literal could be replaced by a
            # public answer and slip past the check.
            if not is_public_address(lowered):
                raise BlockedUrlError(f"{lowered} is not a public address and was refused")
            return
        # A hostname must resolve, and every answer must be public.
        try:
            addresses = self._resolver(lowered)
        except OSError as error:
            raise BlockedUrlError(f"the host {lowered} could not be resolved") from error
        if not addresses:
            raise BlockedUrlError(f"the host {lowered} could not be resolved")
        for address in addresses:
            if not is_public_address(address):
                raise BlockedUrlError(
                    f"{lowered} resolves to a non-public address and was refused"
                )

    # ------------------------------------------------------------------ fetch

    async def fetch_page(self, url: str, max_bytes: int) -> FetchedPageSchema:
        """Fetch one URL, following at most ``MAX_REDIRECTS`` validated hops.

        Raises ``BlockedUrlError`` for a refused destination and
        ``DependencyUnavailableError`` when the network itself failed.
        """
        async def operation() -> _RawFetch:
            return await self._fetch_following_redirects(url, max_bytes)

        outcome = await self._boundary.call(
            operation, failure_fallback("web", "fetch_failed"), idempotent=True
        )
        if not outcome.ok or outcome.value is None:
            raise DependencyUnavailableError("web", outcome.error or "the page could not be fetched")
        raw = outcome.value
        if raw.blocked_reason:
            raise BlockedUrlError(raw.blocked_reason)

        content_type = raw.content_type.split(";", 1)[0].strip().lower()
        body = raw.body
        text = ""
        links: tuple[str, ...] = ()
        if any(kind in content_type for kind in READABLE_CONTENT_TYPES):
            markup = body.decode("utf-8", errors="replace")
            text = extract_text(markup) if "html" in content_type or "xml" in content_type else markup
            if "html" in content_type:
                links = tuple(extract_links(markup, raw.final_url))
        return FetchedPageSchema(
            url=url,
            final_url=raw.final_url,
            content_type=content_type,
            text=text,
            byte_count=len(body),
            links=links,
            status=raw.status,
        )

    async def fetch_robots(self, origin: str) -> str | None:
        """Return robots.txt text, "" when the site has none, None when unknown.

        ``None`` is the conservative answer: the crawler then reads only the
        starting page and follows nothing.
        """
        try:
            page = await self.fetch_page(origin.rstrip("/") + "/robots.txt", 64 * 1024)
        except (BlockedUrlError, DependencyUnavailableError):
            return None
        if page.status == 404:
            return ""
        if page.status != 200:
            return None
        return page.text

    async def search(self, query: str, limit: int) -> list[SearchResultSchema]:
        async def operation() -> list[SearchResultSchema]:
            return await self._search_backend.search(query, limit)

        outcome = await self._boundary.call(
            operation, failure_fallback("web", "search_failed"), idempotent=True
        )
        if not outcome.ok or outcome.value is None:
            raise DependencyUnavailableError(
                "web", outcome.error or "the web search could not be reached"
            )
        return outcome.value

    # -------------------------------------------------------------- internals

    def _client(self) -> Any:
        factory = self._client_factory
        if factory is None:
            import httpx

            factory = httpx.AsyncClient
        return factory(timeout=self._timeout, follow_redirects=False)

    async def _fetch_markup(self, url: str) -> str:
        """Raw markup fetch used by the search backend, inside the boundary."""
        raw = await self._fetch_following_redirects(url, 2 * 1024 * 1024)
        if raw.blocked_reason:
            raise BlockedUrlError(raw.blocked_reason)
        if raw.status >= 400:
            raise DependencyUnavailableError("web", f"the search backend answered {raw.status}")
        return raw.body.decode("utf-8", errors="replace")

    async def _fetch_following_redirects(self, url: str, max_bytes: int) -> _RawFetch:
        current = url
        for _ in range(MAX_REDIRECTS + 1):
            try:
                self.validate_url(current)
            except BlockedUrlError as error:
                return _RawFetch(
                    status=0, final_url=current, content_type="", blocked_reason=error.message
                )
            try:
                async with self._client() as client:
                    async with client.stream(
                        "GET", current, headers={"User-Agent": self._user_agent}
                    ) as response:
                        if response.status_code in (301, 302, 303, 307, 308):
                            location = response.headers.get("location")
                            if not location:
                                return _RawFetch(
                                    status=response.status_code,
                                    final_url=current,
                                    content_type="",
                                )
                            current = urljoin(current, location)
                            continue
                        content_type = response.headers.get("content-type", "")
                        body = await self._read_bounded(response, max_bytes)
                        return _RawFetch(
                            status=response.status_code,
                            final_url=str(response.url),
                            content_type=content_type,
                            body=body,
                        )
            except BlockedUrlError:
                raise
            except Exception as error:  # noqa: BLE001 - the boundary decides what to do
                raise DependencyUnavailableError(
                    "web", f"{type(error).__name__}: {error}"
                ) from error
        return _RawFetch(
            status=0,
            final_url=current,
            content_type="",
            blocked_reason=f"more than {MAX_REDIRECTS} redirects",
        )

    @staticmethod
    async def _read_bounded(response: Any, max_bytes: int) -> bytes:
        chunks: list[bytes] = []
        size = 0
        async for chunk in response.aiter_bytes():
            chunks.append(chunk)
            size += len(chunk)
            if size >= max_bytes:
                break
        return b"".join(chunks)[:max_bytes]
