"""Network tools: fetch_url, web_search, crawl, wikipedia and weather.

Every one of these goes through the injected web gateway, which is one outbound
boundary with a timeout and a circuit breaker and one address policy. The
handlers only shape the request and the answer; they never open a socket.

NOT BUILT, ON PURPOSE: a tool that logs into a third-party account with a
password the person types into the chat. Accepting a credential in a chat
message and replaying it against an arbitrary site is credential custody: it is
indistinguishable from phishing, it would make us store secrets we cannot
protect, and it breaks the terms of most sites. It also cannot work on this
deployment, which has no headless browser on the free tier. If the owner wants
actions in a logged-in session, the browser extension can perform them in the
person's own session, where the server never sees a credential. That is a later
piece of work and it belongs to the extension, not to a server-side tool.
"""

from __future__ import annotations

import asyncio
import json
import logging
from urllib.parse import quote, quote_plus, urlparse
from urllib.robotparser import RobotFileParser

from core.errors import BlockedUrlError, DependencyUnavailableError
from core.protocols import WebGatewayProtocol
from core.tools.tool_registry import (
    ToolContext,
    ToolSpec,
    optional_int,
    require_string,
)
from schemas.tool_schema import ToolResultSchema

logger = logging.getLogger("ranti.tools.web")

FETCH_URL_MAX_BYTES = 512 * 1024
FETCH_URL_MAX_CHARS = 6000
SEARCH_DEFAULT_LIMIT = 5
SEARCH_MAX_LIMIT = 8
SEARCH_MAX_CHARS = 4000
CRAWL_DEFAULT_PAGES = 3
CRAWL_HARD_MAX_PAGES = 8
CRAWL_MAX_BYTES = 256 * 1024
CRAWL_MAX_TOTAL_CHARS = 12000
CRAWL_DELAY_SECONDS = 0.5
CRAWL_USER_AGENT = "RantiBot/0.1"

WIKIPEDIA_SEARCH_URL = "https://en.wikipedia.org/w/rest.php/v1/search/page"
WIKIPEDIA_SUMMARY_URL = "https://en.wikipedia.org/api/rest_v1/page/summary/"
GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

# WMO weather interpretation codes, as published by Open-Meteo.
WEATHER_CODES = {
    0: "clear sky",
    1: "mainly clear",
    2: "partly cloudy",
    3: "overcast",
    45: "fog",
    48: "depositing rime fog",
    51: "light drizzle",
    53: "moderate drizzle",
    55: "dense drizzle",
    56: "light freezing drizzle",
    57: "dense freezing drizzle",
    61: "slight rain",
    63: "moderate rain",
    65: "heavy rain",
    66: "light freezing rain",
    67: "heavy freezing rain",
    71: "slight snowfall",
    73: "moderate snowfall",
    75: "heavy snowfall",
    77: "snow grains",
    80: "slight rain showers",
    81: "moderate rain showers",
    82: "violent rain showers",
    85: "slight snow showers",
    86: "heavy snow showers",
    95: "thunderstorm",
    96: "thunderstorm with slight hail",
    99: "thunderstorm with heavy hail",
}


def _origin(url: str) -> str:
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}"


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "\n[truncated]"


def _normalize_target_url(raw: str) -> str:
    cleaned = raw.strip(" '\"<>")
    if not (cleaned.startswith("http://") or cleaned.startswith("https://")):
        if cleaned.startswith("http:/"):
            cleaned = "http://" + cleaned[6:].lstrip("/")
        elif cleaned.startswith("https:/"):
            cleaned = "https://" + cleaned[7:].lstrip("/")
        elif cleaned.startswith("http:"):
            cleaned = "http://" + cleaned[5:].lstrip("/")
        elif cleaned.startswith("https:"):
            cleaned = "https://" + cleaned[6:].lstrip("/")
        else:
            cleaned = "https://" + cleaned
    return cleaned


async def _fetch_url(
    arguments: dict[str, object], context: ToolContext, gateway: WebGatewayProtocol
) -> ToolResultSchema:
    url = _normalize_target_url(require_string(arguments, "url"))
    try:
        gateway.validate_url(url)
        page = await gateway.fetch_page(url, FETCH_URL_MAX_BYTES)
    except BlockedUrlError as error:
        return ToolResultSchema.failure("fetch_url", f"that URL was refused: {error.message}")
    except DependencyUnavailableError as error:
        return ToolResultSchema.failure(
            "fetch_url", f"the page could not be fetched ({error.message})"
        )
    if not page.readable:
        described = page.content_type or "no content"
        return ToolResultSchema.failure(
            "fetch_url",
            f"the URL returned {described}, which is not readable text",
        )
    return ToolResultSchema.success(
        "fetch_url",
        f"Readable text from {page.final_url}:\n{_truncate(page.text, FETCH_URL_MAX_CHARS)}",
    )


async def _web_search(
    arguments: dict[str, object], context: ToolContext, gateway: WebGatewayProtocol
) -> ToolResultSchema:
    query = require_string(arguments, "query")
    limit = optional_int(arguments, "limit", SEARCH_DEFAULT_LIMIT, 1, SEARCH_MAX_LIMIT)
    try:
        results = await gateway.search(query, limit)
    except DependencyUnavailableError as error:
        return ToolResultSchema.failure(
            "web_search",
            "the web search could not be reached, so I could not search "
            f"({error.message}); the backend may be rate limiting",
        )
    if not results:
        # An empty list is not "nothing exists"; it is almost always a blocked or
        # rate-limited backend. Saying so is the honest answer, and it stops the
        # model presenting stale memory as a fresh search.
        return ToolResultSchema.failure(
            "web_search",
            "the web search returned no usable results, most likely because the "
            "keyless backend is rate limiting; I could not confirm anything",
        )
    lines = [
        "Search results. These are snippets, not the full pages.",
        "",
    ]
    total = 0
    for index, result in enumerate(results, start=1):
        block = f"{index}. {result.title}\n{result.url}\n{result.snippet}".strip()
        if total + len(block) > SEARCH_MAX_CHARS:
            lines.append("[more results omitted]")
            break
        total += len(block)
        lines.append(block)
        lines.append("")
    return ToolResultSchema.success("web_search", "\n".join(lines).strip())


async def _crawl(
    arguments: dict[str, object], context: ToolContext, gateway: WebGatewayProtocol
) -> ToolResultSchema:
    start = _normalize_target_url(require_string(arguments, "url"))
    max_pages = optional_int(
        arguments, "max_pages", CRAWL_DEFAULT_PAGES, 1, CRAWL_HARD_MAX_PAGES
    )
    same_origin = arguments.get("same_origin")
    same_origin = same_origin if isinstance(same_origin, bool) else True
    try:
        gateway.validate_url(start)
    except BlockedUrlError as error:
        return ToolResultSchema.failure("crawl", f"that URL was refused: {error.message}")

    origin = _origin(start)
    robots = await gateway.fetch_robots(origin)
    parser: RobotFileParser | None = None
    if robots is None:
        # Conservative: robots.txt could not be read, so only the starting page
        # is fetched and no discovered link is followed.
        follow_links = False
    else:
        follow_links = True
        parser = RobotFileParser()
        parser.parse(robots.splitlines())

    visited: set[str] = set()
    queue: list[str] = [start]
    sections: list[str] = []
    refused: list[str] = []
    total = 0
    pages_read = 0

    key_resources: list[str] = []

    while queue and pages_read < max_pages and total < CRAWL_MAX_TOTAL_CHARS:
        url = queue.pop(0)
        if url in visited:
            continue
        visited.add(url)
        if same_origin and _origin(url) != origin:
            continue
        if parser is not None and not parser.can_fetch(CRAWL_USER_AGENT, url):
            refused.append(url)
            continue
        try:
            gateway.validate_url(url)
        except BlockedUrlError as error:
            # This is the important case: an internal address discovered as a
            # link on a page is refused here, before any request is made.
            refused.append(f"{url} ({error.message})")
            continue
        try:
            page = await gateway.fetch_page(url, CRAWL_MAX_BYTES)
        except BlockedUrlError as error:
            refused.append(f"{url} ({error.message})")
            continue
        except DependencyUnavailableError as error:
            refused.append(f"{url} (unavailable: {error.message})")
            continue
        pages_read += 1
        if page.readable:
            block = f"Source: {page.final_url}\n{page.text}"
            remaining = CRAWL_MAX_TOTAL_CHARS - total
            if remaining <= 0:
                break
            sections.append(_truncate(block, remaining))
            total += len(sections[-1])
        if page.links:
            for lk in page.links:
                lk_lower = lk.lower()
                if any(x in lk_lower for x in (".apk", ".zip", ".tar.gz", ".pdf", ".exe", ".dmg", "/download", "/start", "/signup", "/register", "/app")):
                    if lk not in key_resources and len(key_resources) < 8:
                        key_resources.append(lk)
        if follow_links and pages_read < max_pages:
            for link in page.links:
                if link in visited:
                    continue
                if same_origin and _origin(link) != origin:
                    continue
                queue.append(link)
        if queue and pages_read < max_pages and total < CRAWL_MAX_TOTAL_CHARS:
            await asyncio.sleep(CRAWL_DELAY_SECONDS)

    if not sections:
        detail = "; ".join(refused[:3]) if refused else "no readable text was found"
        return ToolResultSchema.failure(
            "crawl", f"I could not read anything from {start}: {detail}"
        )

    header = (
        f"Crawled {pages_read} page(s) starting at {start}. "
        f"Only readable text is included; each section names its source."
    )
    if refused:
        header += (
            f" Refused {len(refused)} link(s), including non-public addresses: "
            + "; ".join(refused[:3])
        )
    footer = ""
    if key_resources:
        footer = "\n\nDiscovered Key Action & Download Links:\n" + "\n".join(f"- {lk}" for lk in key_resources)
    return ToolResultSchema.success(
        "crawl", header + "\n\n" + "\n\n".join(sections) + footer
    )


async def _wikipedia(
    arguments: dict[str, object], context: ToolContext, gateway: WebGatewayProtocol
) -> ToolResultSchema:
    from core.gateways.html_extract import extract_text

    query = require_string(arguments, "query")
    search_url = f"{WIKIPEDIA_SEARCH_URL}?q={quote_plus(query)}&limit=1"
    try:
        search_page = await gateway.fetch_page(search_url, 256 * 1024)
        search_data = json.loads(search_page.text or "{}")
    except (DependencyUnavailableError, json.JSONDecodeError):
        return ToolResultSchema.failure(
            "wikipedia", "Wikipedia could not be reached or answered unclearly"
        )
    pages = search_data.get("pages") if isinstance(search_data, dict) else None
    if not isinstance(pages, list) or not pages:
        return ToolResultSchema.failure("wikipedia", f"no Wikipedia article matched {query!r}")

    first = pages[0] if isinstance(pages[0], dict) else {}
    title = str(first.get("title") or first.get("key") or query)
    key = str(first.get("key") or first.get("title") or query)
    summary_url = WIKIPEDIA_SUMMARY_URL + quote(key, safe="")
    text = ""
    try:
        summary_page = await gateway.fetch_page(summary_url, 256 * 1024)
        summary = json.loads(summary_page.text or "{}")
        if isinstance(summary, dict):
            text = str(summary.get("extract") or "")
    except (DependencyUnavailableError, json.JSONDecodeError):
        text = ""
    if not text:
        text = extract_text(str(first.get("excerpt") or ""))
    if not text:
        return ToolResultSchema.failure("wikipedia", f"the article {title!r} had no readable summary")
    return ToolResultSchema.success("wikipedia", f"{title}: {_truncate(text, 3000)}")


async def _weather(
    arguments: dict[str, object], context: ToolContext, gateway: WebGatewayProtocol
) -> ToolResultSchema:
    location = require_string(arguments, "location")
    geo_url = f"{GEOCODING_URL}?name={quote_plus(location)}&count=1&language=en&format=json"
    try:
        geo_page = await gateway.fetch_page(geo_url, 256 * 1024)
        geo = json.loads(geo_page.text or "{}")
    except (DependencyUnavailableError, json.JSONDecodeError):
        return ToolResultSchema.failure("weather", "the location service could not be reached")
    results = geo.get("results") if isinstance(geo, dict) else None
    if not isinstance(results, list) or not results:
        return ToolResultSchema.failure("weather", f"no place named {location!r} was found")
    place = results[0] if isinstance(results[0], dict) else {}
    latitude = place.get("latitude")
    longitude = place.get("longitude")
    if latitude is None or longitude is None:
        return ToolResultSchema.failure("weather", f"no coordinates for {location!r}")
    label = ", ".join(
        str(part)
        for part in (place.get("name"), place.get("country"))
        if isinstance(part, str) and part
    ) or location
    forecast_url = (
        f"{FORECAST_URL}?latitude={latitude}&longitude={longitude}"
        "&current=temperature_2m,relative_humidity_2m,apparent_temperature,"
        "precipitation,weather_code,wind_speed_10m"
        "&daily=temperature_2m_max,temperature_2m_min,precipitation_probability_max"
        "&timezone=auto&forecast_days=3"
    )
    try:
        forecast_page = await gateway.fetch_page(forecast_url, 256 * 1024)
        forecast = json.loads(forecast_page.text or "{}")
    except (DependencyUnavailableError, json.JSONDecodeError):
        return ToolResultSchema.failure("weather", "the forecast service could not be reached")
    current = forecast.get("current") if isinstance(forecast, dict) else None
    if not isinstance(current, dict):
        return ToolResultSchema.failure("weather", "the forecast service returned no current weather")
    code = current.get("weather_code")
    condition = WEATHER_CODES.get(code, "unknown conditions") if isinstance(code, int) else "unknown"
    lines = [
        f"Current weather for {label}: {condition}, "
        f"{current.get('temperature_2m')} C, feels like "
        f"{current.get('apparent_temperature')} C, humidity "
        f"{current.get('relative_humidity_2m')} percent, wind "
        f"{current.get('wind_speed_10m')} km/h, precipitation "
        f"{current.get('precipitation')} mm."
    ]
    daily = forecast.get("daily")
    if isinstance(daily, dict):
        days = daily.get("time") or []
        highs = daily.get("temperature_2m_max") or []
        lows = daily.get("temperature_2m_min") or []
        chances = daily.get("precipitation_probability_max") or []
        for index, day in enumerate(days[:3]):
            high = highs[index] if index < len(highs) else "?"
            low = lows[index] if index < len(lows) else "?"
            chance = chances[index] if index < len(chances) else "?"
            lines.append(f"{day}: high {high} C, low {low} C, rain chance {chance} percent.")
    return ToolResultSchema.success("weather", "\n".join(lines))


def build_web_tools(gateway: WebGatewayProtocol) -> list[ToolSpec]:
    """Build the network tools bound to one gateway instance."""

    async def fetch_url(arguments: dict[str, object], context: ToolContext) -> ToolResultSchema:
        return await _fetch_url(arguments, context, gateway)

    async def web_search(arguments: dict[str, object], context: ToolContext) -> ToolResultSchema:
        return await _web_search(arguments, context, gateway)

    async def crawl(arguments: dict[str, object], context: ToolContext) -> ToolResultSchema:
        return await _crawl(arguments, context, gateway)

    async def wikipedia(arguments: dict[str, object], context: ToolContext) -> ToolResultSchema:
        return await _wikipedia(arguments, context, gateway)

    async def weather(arguments: dict[str, object], context: ToolContext) -> ToolResultSchema:
        return await _weather(arguments, context, gateway)

    return [
        ToolSpec(
            name="fetch_url",
            description=(
                "Fetch one public web page and return its readable text. Only "
                "http and https URLs are allowed, non-public addresses are "
                "refused, and at most "
                f"{FETCH_URL_MAX_CHARS} characters are returned. Use this for a "
                "specific URL someone gives you; use web_search to look something up."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "The public http(s) URL to read."}
                },
                "required": ["url"],
                "additionalProperties": False,
            },
            handler=fetch_url,
            timeout_seconds=20.0,
            uses_network=True,
        ),
        ToolSpec(
            name="web_search",
            description=(
                "Search the public web and return up to "
                f"{SEARCH_MAX_LIMIT} results as title, URL and snippet. The "
                "snippet is not the page text, so fetch_url a result before "
                "quoting it. The keyless backend can rate limit; if it does, the "
                "call fails and you must tell the person you could not search "
                "rather than answering from memory as if you had."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "What to search for."},
                    "limit": {
                        "type": "integer",
                        "description": f"Results to return, 1 to {SEARCH_MAX_LIMIT}.",
                    },
                },
                "required": ["query"],
                "additionalProperties": False,
            },
            handler=web_search,
            timeout_seconds=20.0,
            uses_network=True,
        ),
        ToolSpec(
            name="crawl",
            description=(
                "Read the public text reachable from a URL, following links. "
                f"Bounds, enforced in code: at most {CRAWL_HARD_MAX_PAGES} pages "
                f"(default {CRAWL_DEFAULT_PAGES}), {CRAWL_MAX_BYTES // 1024} KB per "
                f"page, {CRAWL_MAX_TOTAL_CHARS} characters total, one request "
                "timeout per page and a short delay between pages. robots.txt is "
                "honoured for the RantiBot user agent; if robots.txt cannot be "
                "fetched, only the starting page is read and no link is followed. "
                "Links are followed only within the starting origin unless "
                "same_origin is false. Non-public addresses are refused on every "
                "request, including links found on a page."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "The public http(s) start URL."},
                    "max_pages": {
                        "type": "integer",
                        "description": f"Pages to read, 1 to {CRAWL_HARD_MAX_PAGES}.",
                    },
                    "same_origin": {
                        "type": "boolean",
                        "description": "Follow links only within the start origin. Default true.",
                    },
                },
                "required": ["url"],
                "additionalProperties": False,
            },
            handler=crawl,
            timeout_seconds=60.0,
            uses_network=True,
        ),
        ToolSpec(
            name="wikipedia",
            description=(
                "Look up a topic on Wikipedia (the free MediaWiki REST API, no "
                "key) and return the article summary. Good for settled facts; for "
                "anything current use web_search."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "The topic to look up."}
                },
                "required": ["query"],
                "additionalProperties": False,
            },
            handler=wikipedia,
            timeout_seconds=20.0,
            uses_network=True,
        ),
        ToolSpec(
            name="weather",
            description=(
                "Get the current weather and a three-day forecast for a place "
                "name, from the free Open-Meteo API (no key)."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "location": {
                        "type": "string",
                        "description": "A city or place name, for example Lagos.",
                    }
                },
                "required": ["location"],
                "additionalProperties": False,
            },
            handler=weather,
            timeout_seconds=20.0,
            uses_network=True,
        ),
    ]
