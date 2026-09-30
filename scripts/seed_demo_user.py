#!/usr/bin/env python3
"""Drive a full multi-turn demo through the running Ranti HTTP API.

The script posts a scripted conversation to POST /chat/turn and demonstrates,
in order:

  (a) a durable fact being learned and written to Walrus Memory
  (b) the same fact being recalled on a later turn
  (c) a changed preference superseding the old value
  (d) a contradiction between two remembered facts being flagged

It then prints the user's counters from GET /memories/{user_id}/stats and
replays the recall turn with memory disabled via
POST /chat/counterfactual/{turn_id}. Every step is echoed so the command
output can be pasted into a write-up as evidence.

Usage:
    python scripts/seed_demo_user.py
    python scripts/seed_demo_user.py --base-url http://127.0.0.1:8000
    python scripts/seed_demo_user.py --strict

Exit codes:
    0  every request succeeded (semantic expectations may still be reported as
       [WARN] unless --strict is passed)
    1  a request failed, or --strict was passed and an expectation was unmet

Plain ASCII only.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from typing import Any

import httpx

DEFAULT_BASE_URL = "http://127.0.0.1:8000"
SURFACE = "cli"
DEFAULT_SURFACE_USER_ID = "demo-reviewer"
DEFAULT_DISPLAY_NAME = "Demo Reviewer"

REPLY_PREVIEW_CHARS = 400


@dataclass
class TurnResult:
    """Everything the API returned for one turn, plus the parsed fields we print."""

    step_id: str
    label: str
    text: str
    body: dict[str, Any]
    ok: bool
    checks: dict[str, bool] = field(default_factory=dict)


def preview(text: str, limit: int = REPLY_PREVIEW_CHARS) -> str:
    collapsed = " ".join(str(text).split())
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: limit - 3] + "..."


class DemoRunner:
    def __init__(self, base_url: str, surface_user_id: str, display_name: str, timeout: float):
        self.base_url = base_url.rstrip("/")
        self.surface_user_id = surface_user_id
        self.display_name = display_name
        self.client = httpx.Client(base_url=self.base_url, timeout=timeout)
        self.turns: list[TurnResult] = []
        self.user_id: str | None = None
        self.recall_turn_id: str | None = None
        self.expectations: list[tuple[str, bool]] = []

    # ------------------------------------------------------------------ HTTP
    def _post(self, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            response = self.client.post(path, json=payload or {})
        except httpx.HTTPError as exc:
            raise RuntimeError(f"POST {path} failed: {exc}") from exc
        if response.status_code >= 400:
            raise RuntimeError(
                f"POST {path} returned HTTP {response.status_code}: {preview(response.text, 300)}"
            )
        try:
            body = response.json()
        except ValueError as exc:
            raise RuntimeError(f"POST {path} returned non-JSON body: {preview(response.text, 300)}") from exc
        if not isinstance(body, dict):
            raise RuntimeError(f"POST {path} returned {type(body).__name__}, expected an object")
        return body

    def _get(self, path: str) -> dict[str, Any]:
        try:
            response = self.client.get(path)
        except httpx.HTTPError as exc:
            raise RuntimeError(f"GET {path} failed: {exc}") from exc
        if response.status_code >= 400:
            raise RuntimeError(
                f"GET {path} returned HTTP {response.status_code}: {preview(response.text, 300)}"
            )
        body = response.json()
        if not isinstance(body, dict):
            raise RuntimeError(f"GET {path} returned {type(body).__name__}, expected an object")
        return body

    # ------------------------------------------------------------------ steps
    def take_turn(self, step_id: str, label: str, text: str) -> TurnResult:
        print()
        print(f"--- step {step_id}: {label} ---")
        print(f"[TURN] {text}")
        body = self._post(
            "/chat/turn",
            {
                "surface": SURFACE,
                "surface_user_id": self.surface_user_id,
                "display_name": self.display_name,
                "text": text,
                "memory_enabled": True,
            },
        )
        if self.user_id is None:
            self.user_id = str(body.get("user_id", ""))
        result = TurnResult(step_id=step_id, label=label, text=text, body=body, ok=True)
        self.turns.append(result)
        self._print_turn(result)
        return result

    def _print_turn(self, result: TurnResult) -> None:
        body = result.body
        recalled = body.get("recalled") or []
        facts = body.get("stored_facts") or []
        print(f"[OK] turn_id={body.get('turn_id')} provider={body.get('provider')}")
        print(f"[REPLY] {preview(body.get('reply', ''))}")
        print(f"[RECALLED] {len(recalled)} memory(ies)")
        for memory in recalled:
            print(
                "  - "
                f"salience={memory.get('salience')} importance={memory.get('importance')} "
                f"surface={memory.get('origin_surface')} :: {preview(memory.get('text', ''), 200)}"
            )
        print(f"[WROTE] {len(facts)} extracted fact(s)")
        for fact in facts:
            print(f"  - verdict={fact.get('verdict')} blob_id={fact.get('blob_id')} :: {preview(fact.get('text', ''), 200)}")
        print(f"[SKIPPED_DUPLICATES] {body.get('skipped_duplicates', 0)}")
        print(f"[CONTRADICTIONS_THIS_TURN] {body.get('contradiction_count', 0)}")
        if body.get("memory_degraded"):
            print(f"[WARN] memory degraded this turn: {body.get('memory_note')}")

    # ---------------------------------------------------------------- checks
    def mark(self, label: str, passed: bool) -> None:
        marker = "[OK]" if passed else "[WARN]"
        print(f"{marker} expectation: {label}")
        self.expectations.append((label, passed))

    def run(self) -> int:
        print("=" * 72)
        print("Ranti multi-turn memory demo")
        print(f"base_url       : {self.base_url}")
        print(f"surface        : {SURFACE}")
        print(f"surface_user_id: {self.surface_user_id}")
        print(f"display_name   : {self.display_name}")
        print("=" * 72)

        health = self._get("/health")
        print()
        print("[OK] health:", json.dumps(health, sort_keys=True))
        memory_mode = (health.get("memory") or {}).get("mode")
        if memory_mode == "mock":
            print("[WARN] memory mode is 'mock': no Walrus Memory credentials, so this run is offline")
        providers = (health.get("llm") or {}).get("providers") or []
        if not providers:
            print("[FAIL] no LLM provider is configured; /chat/turn will answer 503")
            print("       set GROQ_API_KEY, GEMINI_API_KEY, or run Ollama locally")
            return 1
        print(f"[OK] llm providers: {', '.join(providers)}")
        if providers == ["offline"]:
            print("[WARN] no real model configured: the deterministic offline model is answering.")
            print("       It extracts first-person facts and adjudicates by predicate, so the")
            print("       consolidation behaviour below is real. Answer quality is not.")
            print("       Set GROQ_API_KEY or GEMINI_API_KEY for genuine replies.")
        if memory_mode == "mock":
            # The offline SDK mock scores recall by query-token coverage, so a long
            # natural question falls below the relevance floor. Step b therefore
            # uses a short query. With a real embedding model it does not matter.
            print("[WARN] recall is lexical here, so step b keeps its query short on purpose")
        if providers == ["ollama"]:
            # Ollama needs no API key, so it always looks configured. It only
            # works if the daemon is actually running on the local machine.
            print("[WARN] only the local Ollama fallback is listed; it must be running")
            print("       a hosted provider is recommended for this demo")

        try:
            # (a) learn a durable fact
            learned = self.take_turn(
                "a",
                "fact learned",
                "Hi, I am Priya. I am allergic to peanuts and I always take my coffee black.",
            )
            facts = learned.body.get("stored_facts") or []
            self.mark(
                "step a wrote at least one durable fact",
                any(fact.get("verdict") not in ("duplicate", "write_failed") for fact in facts),
            )

            # (b) recall the same fact on a later turn
            recalled = self.take_turn(
                "b",
                "fact recalled on a later turn",
                "Peanuts and coffee",
            )
            self.recall_turn_id = str(recalled.body.get("turn_id") or "")
            recalled_text = " ".join(
                str(memory.get("text", "")) for memory in (recalled.body.get("recalled") or [])
            ).lower()
            self.mark(
                "step b recalled the peanut or coffee fact",
                "peanut" in recalled_text or "coffee" in recalled_text,
            )

            # (c) changed preference supersedes the old value
            self.take_turn("c1", "preference before the change", "My main programming language is Python.")
            changed = self.take_turn(
                "c2", "changed preference", "My main programming language is Rust now."
            )
            verdicts = {str(fact.get("verdict")) for fact in (changed.body.get("stored_facts") or [])}
            self.mark("step c2 produced an 'updates' verdict", "updates" in verdicts)

            # (d) contradiction flagged
            self.take_turn("d1", "first conflicting fact", "I do not eat meat at all.")
            conflicting = self.take_turn(
                "d2", "second conflicting fact", "I eat meat every Friday."
            )
            self.mark(
                "step d2 flagged a contradiction",
                int(conflicting.body.get("contradiction_count", 0)) > 0,
            )

            if not self.user_id:
                print("[FAIL] the API did not return a user_id; cannot read stats")
                return 1

            print()
            print("=" * 72)
            print("USER STATS from GET /memories/{user_id}/stats")
            print("=" * 72)
            stats = self._get(f"/memories/{self.user_id}/stats")
            print(json.dumps(stats, indent=2, sort_keys=True))
            self.mark("stats report at least one active memory", int(stats.get("active", 0)) > 0)
            self.mark(
                "stats report a superseded memory from step c",
                int(stats.get("superseded", 0)) > 0,
            )
            self.mark(
                "stats report a contradiction from step d",
                int(stats.get("open_contradictions", 0)) > 0 or int(stats.get("contradicted", 0)) > 0,
            )

            print()
            print("=" * 72)
            print(f"COUNTERFACTUAL REPLAY of step b turn {self.recall_turn_id}")
            print("=" * 72)
            replay = self._post(f"/chat/counterfactual/{self.recall_turn_id}")
            print(json.dumps(replay, indent=2, sort_keys=True))
            self.mark("counterfactual replay returned both answers", bool(replay.get("with_memory")) and bool(replay.get("without_memory")))
        except RuntimeError as exc:
            print(f"[FAIL] {exc}")
            return 1

        print()
        print("=" * 72)
        print("EVIDENCE SUMMARY")
        print("=" * 72)
        passed = sum(1 for _, ok in self.expectations if ok)
        for label, ok in self.expectations:
            print(f"{'[OK]' if ok else '[WARN]'} {label}")
        print(f"[{passed}/{len(self.expectations)}] expectations met")
        if passed != len(self.expectations):
            print("[WARN] some expectations were not met; see the per-step output above")
        return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Seed a scripted demo through the Ranti HTTP API.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="Ranti API base URL")
    parser.add_argument("--surface-user-id", default=DEFAULT_SURFACE_USER_ID, help="demo identity")
    parser.add_argument("--display-name", default=DEFAULT_DISPLAY_NAME, help="display name stored with the user")
    parser.add_argument("--timeout", type=float, default=60.0, help="per-request timeout in seconds")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="exit 1 when an expectation is unmet, not only when a request fails",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    runner = DemoRunner(args.base_url, args.surface_user_id, args.display_name, args.timeout)
    try:
        code = runner.run()
    finally:
        runner.client.close()
    if code == 0 and args.strict:
        unmet = [label for label, ok in runner.expectations if not ok]
        if unmet:
            print(f"[FAIL] --strict: unmet expectations: {', '.join(unmet)}")
            return 1
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
