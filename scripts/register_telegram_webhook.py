#!/usr/bin/env python3
"""Register the Ranti Telegram webhook with the Telegram Bot API.

Reads configuration from the repository .env file, then from the process
environment (environment wins). The webhook URL is:

    {PUBLIC_BASE_URL}/webhooks/telegram/{TELEGRAM_WEBHOOK_SECRET}

which matches the route in src/routers/telegram_router.py.

Usable both as a script and as an importable module:

    python scripts/register_telegram_webhook.py
    from register_telegram_webhook import register_webhook

Exit codes:
    0  webhook registered
    1  configuration missing or the Telegram API rejected the call

Plain ASCII only.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import Any, Mapping

import httpx

REPO_ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = REPO_ROOT / ".env"
TELEGRAM_API_BASE = "https://api.telegram.org"
# Telegram allows 1-256 characters from A-Z, a-z, 0-9, underscore and hyphen.
SECRET_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,256}$")

FAILURE = 1


def load_env_file(path: Path = ENV_FILE) -> dict[str, str]:
    """Parse a plain KEY=VALUE .env file. Missing file yields an empty mapping."""
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            values[key] = value
    return values


def resolve_settings(env: Mapping[str, str] | None = None) -> tuple[str, str, str]:
    """Return (bot_token, webhook_secret, public_base_url).

    The process environment overrides the .env file. Raises ValueError with a
    human-readable message when a required value is missing.
    """
    merged = dict(load_env_file())
    merged.update({key: value for key, value in os.environ.items() if value})
    if env is not None:
        merged.update({key: value for key, value in env.items() if value})

    token = merged.get("TELEGRAM_BOT_TOKEN", "").strip()
    secret = merged.get("TELEGRAM_WEBHOOK_SECRET", "").strip()
    base_url = merged.get("PUBLIC_BASE_URL", "").strip().rstrip("/")

    missing = [
        name
        for name, value in (
            ("TELEGRAM_BOT_TOKEN", token),
            ("TELEGRAM_WEBHOOK_SECRET", secret),
            ("PUBLIC_BASE_URL", base_url),
        )
        if not value
    ]
    if missing:
        raise ValueError(
            "missing required configuration: "
            + ", ".join(missing)
            + "; copy .env.example to .env and fill it in, or export the variables"
        )
    if not base_url.startswith(("http://", "https://")):
        raise ValueError(f"PUBLIC_BASE_URL must start with http:// or https://, got {base_url!r}")
    return token, secret, base_url


def webhook_url(public_base_url: str, webhook_secret: str) -> str:
    """Build the exact path the FastAPI router serves."""
    return f"{public_base_url.rstrip('/')}/webhooks/telegram/{webhook_secret}"


def register_webhook(
    token: str,
    webhook_secret: str,
    public_base_url: str,
    *,
    client: httpx.Client | None = None,
    timeout: float = 15.0,
) -> dict[str, Any]:
    """Call Telegram setWebhook. Raises RuntimeError on any API-level failure."""
    url = webhook_url(public_base_url, webhook_secret)
    # callback_query is required or button taps are never delivered to the
    # webhook, which makes every inline keyboard button silently do nothing.
    payload: dict[str, Any] = {
        "url": url,
        "allowed_updates": ["message", "callback_query"],
    }
    if SECRET_TOKEN_PATTERN.match(webhook_secret):
        # Optional in the Bot API; sent only when Telegram will accept the value.
        payload["secret_token"] = webhook_secret

    owns_client = client is None
    http = client or httpx.Client(timeout=timeout)
    try:
        response = http.post(f"{TELEGRAM_API_BASE}/bot{token}/setWebhook", json=payload)
        response.raise_for_status()
        body = response.json()
    except httpx.HTTPStatusError as exc:
        raise RuntimeError(
            f"Telegram returned HTTP {exc.response.status_code}: {exc.response.text[:300]}"
        ) from exc
    except httpx.HTTPError as exc:
        raise RuntimeError(f"could not reach the Telegram API: {exc}") from exc
    finally:
        if owns_client:
            http.close()

    if not isinstance(body, dict) or not body.get("ok", False):
        description = body.get("description", "unknown error") if isinstance(body, dict) else body
        raise RuntimeError(f"Telegram rejected setWebhook: {description}")
    return body


def main(argv: list[str] | None = None) -> int:
    del argv  # no command-line options; configuration comes from .env and the environment
    try:
        token, secret, base_url = resolve_settings()
    except ValueError as exc:
        print(f"[FAIL] {exc}")
        return FAILURE

    url = webhook_url(base_url, secret)
    print("[OK] registering Telegram webhook")
    print(f"     url: {url}")
    try:
        body = register_webhook(token, secret, base_url)
    except RuntimeError as exc:
        print(f"[FAIL] {exc}")
        return FAILURE

    print(f"[OK] Telegram accepted the webhook: {body.get('description', 'ok')}")
    print("[OK] send the bot a text message to verify the round trip")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
