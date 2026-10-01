"""Pure HTML to text and link extraction.

Kept separate from the gateway so the parsing rules are unit-testable with a
string and no network, and so the gateway stays about I/O and policy.
"""

from __future__ import annotations

import html
import re
from html.parser import HTMLParser
from urllib.parse import urljoin

_SKIP_TAGS = {"script", "style", "noscript", "template", "svg"}
_BLOCK_TAGS = {
    "p",
    "div",
    "br",
    "li",
    "tr",
    "section",
    "article",
    "header",
    "footer",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
}
_WHITESPACE = re.compile(r"[ \t\r\f\v]+")
_BLANK_LINES = re.compile(r"\n{3,}")


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip_depth = 0
        self._parts: list[str] = []
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
            return
        if tag == "a":
            href = dict(attrs).get("href")
            if href:
                self.links.append(href)
        if tag in _BLOCK_TAGS:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1
            return
        if tag in _BLOCK_TAGS:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0 and data:
            self._parts.append(data)

    def text(self) -> str:
        raw = "".join(self._parts)
        raw = html.unescape(raw)
        raw = _WHITESPACE.sub(" ", raw)
        lines = [line.strip() for line in raw.split("\n")]
        return _BLANK_LINES.sub("\n\n", "\n".join(line for line in lines if line)).strip()


def extract_text(markup: str) -> str:
    """Return readable text with scripts, styles and markup removed."""
    parser = _TextExtractor()
    try:
        parser.feed(markup)
        parser.close()
    except Exception:  # noqa: BLE001 - a malformed page must still yield what parsed
        pass
    return parser.text()


def extract_links(markup: str, base_url: str) -> list[str]:
    """Return absolute http(s) links found in the page, de-duplicated in order."""
    parser = _TextExtractor()
    try:
        parser.feed(markup)
        parser.close()
    except Exception:  # noqa: BLE001
        pass
    seen: set[str] = set()
    links: list[str] = []
    for href in parser.links:
        candidate = href.strip()
        if not candidate or candidate.startswith(("#", "mailto:", "javascript:", "tel:")):
            continue
        absolute = urljoin(base_url, candidate)
        if absolute.startswith(("http://", "https://")) and absolute not in seen:
            seen.add(absolute)
            links.append(absolute)
    return links


# DuckDuckGo's HTML endpoint wraps every result link in a redirect of the form
# //duckduckgo.com/l/?uddg=<url-encoded target>. The real URL is the uddg value.
_DDG_LINK = re.compile(r"[?&]uddg=([^&]+)")


def decode_duckduckgo_link(href: str) -> str:
    from urllib.parse import unquote

    match = _DDG_LINK.search(href)
    if match is None:
        return href
    return unquote(match.group(1))


class _DuckDuckGoParser(HTMLParser):
    """Collects result titles, links and snippets from the no-JS results page."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.results: list[dict[str, str]] = []
        self._mode: str | None = None
        self._buffer: list[str] = []
        self._href: str = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        classes = attributes.get("class") or ""
        if tag == "a" and "result__a" in classes:
            self._mode = "title"
            self._buffer = []
            self._href = attributes.get("href") or ""
        elif tag == "a" and "result__snippet" in classes:
            self._mode = "snippet"
            self._buffer = []

    def handle_endtag(self, tag: str) -> None:
        if tag != "a" or self._mode is None:
            return
        text = html.unescape("".join(self._buffer)).strip()
        text = _WHITESPACE.sub(" ", text)
        if self._mode == "title" and text:
            self.results.append(
                {"title": text, "url": decode_duckduckgo_link(self._href), "snippet": ""}
            )
        elif self._mode == "snippet" and text and self.results:
            if not self.results[-1]["snippet"]:
                self.results[-1]["snippet"] = text
        self._mode = None
        self._buffer = []

    def handle_data(self, data: str) -> None:
        if self._mode is not None:
            self._buffer.append(data)


def parse_duckduckgo_results(markup: str) -> list[tuple[str, str, str]]:
    """Return (title, url, snippet) rows, or an empty list when nothing parsed."""
    parser = _DuckDuckGoParser()
    try:
        parser.feed(markup)
        parser.close()
    except Exception:  # noqa: BLE001
        return []
    return [
        (row["title"], row["url"], row["snippet"])
        for row in parser.results
        if row["url"].startswith(("http://", "https://"))
    ]
