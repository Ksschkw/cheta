#!/usr/bin/env python3
"""Run the Telegram bot by long polling instead of a webhook.

Telegram can deliver updates two ways: push them to a public HTTPS webhook, or
let the bot fetch them with getUpdates. Polling needs no public URL and no
inbound network access, so it is the fastest way to get real people talking to
the bot while the always-on host is still being set up.

This worker is deliberately thin: it forwards each raw update to the application's
own webhook route over local HTTP. That means parsing, identity, memory and reply
delivery all run through exactly the same code path as production webhook mode,
so polling cannot drift from the deployed behaviour.

    python scripts/telegram_poller.py
    python scripts/telegram_poller.py --api-url http://127.0.0.1:8093

Stop it with Ctrl+C. It calls deleteWebhook first, because a registered webhook
makes getUpdates return nothing. Remember to re-register the webhook with
scripts/register_telegram_webhook.py if you switch back to push mode.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

import httpx

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from core.config import load_environment  # noqa: E402

TELEGRAM_API = "https://api.telegram.org"
POLL_TIMEOUT_SECONDS = 30
BACKOFF_SECONDS = 3.0


async def delete_webhook(client: httpx.AsyncClient, token: str) -> bool:
    """A failure here must not stop the worker: polling still works."""
    try:
        response = await client.post(f"{TELEGRAM_API}/bot{token}/deleteWebhook")
        body = response.json()
    except (httpx.HTTPError, json.JSONDecodeError) as error:
        print(f"[WARN] deleteWebhook failed ({type(error).__name__}); polling anyway")
        return False
    ok = bool(body.get("ok"))
    print(f"[{'OK' if ok else 'WARN'}] deleteWebhook: {body.get('description', 'ok')}")
    return ok


async def forward(client: httpx.AsyncClient, api_url: str, secret: str, update: dict) -> bool:
    """Hand one raw update to the app's webhook route."""
    response = await client.post(
        f"{api_url}/webhooks/telegram/{secret}", json=update, timeout=300.0
    )
    if response.status_code != 200:
        print(f"[WARN] app returned HTTP {response.status_code} for update {update.get('update_id')}")
        return False
    body = response.json()
    if body.get("handled"):
        source = (update.get("message") or {}).get("chat", {}).get("id")
        print(
            f"[OK] update {update.get('update_id')} handled for chat {source} "
            f"recalled={body.get('recalled')}"
        )
    return True


async def poll(api_url: str, secret: str, token: str) -> int:
    offset: int | None = None
    # The httpx default timeout is 5 seconds, which a cold DNS plus TLS handshake
    # can exceed, crashing the worker on its very first call.
    limits = httpx.Timeout(POLL_TIMEOUT_SECONDS + 15, connect=20.0)
    async with httpx.AsyncClient(timeout=limits) as client:
        await delete_webhook(client, token)
        print(f"[OK] polling {TELEGRAM_API} and forwarding to {api_url}")
        print("     message the bot to test it. Ctrl+C to stop.")

        while True:
            payload: dict[str, object] = {
                "timeout": POLL_TIMEOUT_SECONDS,
                "allowed_updates": json.dumps(["message"]),
            }
            if offset is not None:
                payload["offset"] = offset

            try:
                response = await client.get(
                    f"{TELEGRAM_API}/bot{token}/getUpdates",
                    params=payload,
                    timeout=POLL_TIMEOUT_SECONDS + 15,
                )
                body = response.json()
            except (httpx.HTTPError, json.JSONDecodeError) as error:
                print(f"[WARN] getUpdates failed: {type(error).__name__}: {error}")
                await asyncio.sleep(BACKOFF_SECONDS)
                continue

            if not body.get("ok"):
                print(f"[WARN] getUpdates rejected: {body.get('description')}")
                await asyncio.sleep(BACKOFF_SECONDS)
                continue

            for update in body.get("result", []):
                update_id = update.get("update_id")
                if isinstance(update_id, int):
                    offset = update_id + 1
                await forward(client, api_url, secret, update)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the Telegram bot by long polling.")
    parser.add_argument(
        "--api-url",
        default=None,
        help="base URL of the running Ranti API (default: PUBLIC_BASE_URL or localhost:8000)",
    )
    args = parser.parse_args()

    env = load_environment()
    token = (env.get("TELEGRAM_BOT_TOKEN") or "").strip()
    secret = (env.get("TELEGRAM_WEBHOOK_SECRET") or "").strip()
    api_url = (
        args.api_url or (env.get("PUBLIC_BASE_URL") or "").strip() or "http://127.0.0.1:8000"
    ).rstrip("/")

    if not token:
        print("[FAIL] TELEGRAM_BOT_TOKEN is empty. Create a bot with @BotFather first.")
        return 1
    if not secret:
        print("[FAIL] TELEGRAM_WEBHOOK_SECRET is empty. Set any random string in .env.")
        return 1

    try:
        asyncio.run(poll(api_url, secret, token))
    except KeyboardInterrupt:
        print()
        print("[OK] stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
