"""Transport DTOs for the outbound web boundary."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class SearchResultSchema:
    """One result from a search backend. The snippet is not the page text."""

    title: str
    url: str
    snippet: str = ""


@dataclass(frozen=True)
class FetchedPageSchema:
    """One successfully fetched, bounded page of readable text."""

    url: str
    final_url: str
    content_type: str
    text: str
    byte_count: int
    status: int = 200
    # Absolute URLs discovered in the page, already resolved and not yet
    # validated. Every one of them is revalidated before it is fetched.
    links: tuple[str, ...] = field(default_factory=tuple)

    @property
    def readable(self) -> bool:
        return bool(self.text.strip())
