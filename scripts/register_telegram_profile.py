#!/usr/bin/env python3
"""Give the Telegram bot a description and a command menu.

A bare bot with no description and no command list looks unfinished, and real
users said so: "why did you not have a start response and an icon and a
description like most Telegram bots do?"

The API can set the short description (shown before a chat starts), the
description (shown on an empty chat) and the command menu (the button beside the
message box). The bot's display name and profile picture can only be changed by
hand in @BotFather, so those are left alone.

    python scripts/register_telegram_profile.py
    python scripts/register_telegram_profile.py --dry-run

ASCII only, and it exits non-zero if Telegram rejects anything.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import httpx

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from core.config import load_environment  # noqa: E402

TELEGRAM_API = "https://api.telegram.org"

SHORT_DESCRIPTION = "Explain yourself once."

DESCRIPTION = (
    "I am Cheta. I keep a private memory per person, shared across Telegram, a CLI, "
    "a browser widget and a Chrome extension. I collapse repeated facts, retire what "
    "you changed your mind about, and flag conflicts. I read PDF, DOCX, PPTX, XLSX, "
    "XML, JSON, CSV and any text or code file, and I transcribe voice notes. I search "
    "the web, crawl pages, check Wikipedia and weather, do exact arithmetic, set "
    "reminders and make calendar files. /memories shows all; /forget removes; /pair "
    "links a client."
)

COMMANDS = (
    ("start", "Start here: what I am and what I do"),
    ("memories", "Show everything I have stored about you"),
    ("forget", "Remove a note by its number"),
    ("pair", "Link another client to this memory space"),
    ("sessions", "List the clients sharing this memory space"),
    ("unpair", "Return this client to its own space"),
    ("help", "The full list of commands and capabilities"),
)


def call(client: httpx.Client, token: str, method: str, payload: dict) -> dict:
    response = client.post(f"{TELEGRAM_API}/bot{token}/{method}", json=payload)
    try:
        body = response.json()
    except ValueError:
        raise RuntimeError(f"{method} returned a non-JSON response") from None
    if not body.get("ok"):
        raise RuntimeError(f"{method} rejected: {body.get('description')}")
    return body


def main() -> int:
    sys.stdout.reconfigure(line_buffering=True)
    parser = argparse.ArgumentParser(description="Set the Telegram bot profile.")
    parser.add_argument("--dry-run", action="store_true", help="print the plan only")
    args = parser.parse_args()

    token = (load_environment().get("TELEGRAM_BOT_TOKEN") or "").strip()
    if not token:
        print("[FAIL] TELEGRAM_BOT_TOKEN is empty. Create a bot with @BotFather first.")
        return 1

    print("[OK] setting the Telegram bot profile")
    print(f"     short description: {SHORT_DESCRIPTION}")
    print(f"     commands: {', '.join('/' + name for name, _ in COMMANDS)}")

    if args.dry_run:
        return 0

    try:
        with httpx.Client(timeout=30.0) as client:
            call(client, token, "setMyShortDescription", {"short_description": SHORT_DESCRIPTION})
            print("[OK] short description set")
            call(client, token, "setMyDescription", {"description": DESCRIPTION})
            print("[OK] description set")
            call(
                client,
                token,
                "setMyCommands",
                {"commands": [{"command": n, "description": d} for n, d in COMMANDS]},
            )
            print("[OK] commands set")
            me = call(client, token, "getMe", {}).get("result", {})
    except (httpx.HTTPError, RuntimeError) as error:
        print(f"[FAIL] {type(error).__name__}: {error}")
        return 1

    print(f"[OK] bot is @{me.get('username')} ({me.get('first_name')})")
    print("     the display name and profile picture still need @BotFather by hand")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
