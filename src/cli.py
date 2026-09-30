"""Ranti terminal client.

A genuinely separate deployment surface: this module talks to a running Ranti
API over HTTP only. It imports nothing from the application's services, crud or
database, so the CLI stays honest about what the public API can do.

Run it with ``python -m cli`` from a checkout with ``PYTHONPATH=src``, or with
the ``scripts/ranti`` wrapper.

Environment:
    RANTI_API_URL     Base URL of the API (default http://127.0.0.1:8000).
    RANTI_CLI_CONFIG  Path to the persisted identity file (~/.ranti_cli.json).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import textwrap
import uuid
from pathlib import Path

import httpx

DEFAULT_API_URL = "http://127.0.0.1:8000"
DEFAULT_CONFIG_PATH = "~/.ranti_cli.json"
SURFACE = "cli"
DEFAULT_DISPLAY_NAME = "cli-user"
REQUEST_TIMEOUT = 30.0

TEXT_LIMIT = 60
BLOB_LIMIT = 12
EVIDENCE_THRESHOLD = 10


class ApiError(Exception):
    """A non-2xx response from the API."""

    def __init__(self, code: str, status: int, detail: str) -> None:
        super().__init__(f"{code} (HTTP {status}): {detail}")
        self.code = code
        self.status = status
        self.detail = detail


class ConnectionFailure(Exception):
    """The API could not be reached at all."""


class IdentityError(Exception):
    """The persisted identity file could not be read or written."""


def api_url() -> str:
    return os.environ.get("RANTI_API_URL", DEFAULT_API_URL)


def create_client(transport: httpx.BaseTransport | None = None) -> httpx.Client:
    """Build the one HTTP client the CLI ever uses.

    ``transport`` exists so tests can inject ``httpx.MockTransport`` and exercise
    the real argument parsing and request construction without a live server.
    """
    return httpx.Client(base_url=api_url(), timeout=REQUEST_TIMEOUT, transport=transport)


def _validation_line(item: object) -> str:
    if isinstance(item, dict):
        location = ".".join(str(part) for part in item.get("loc", []))
        message = str(item.get("msg", ""))
        return f"{location}: {message}" if location else message
    return str(item)


def _error_info(response: httpx.Response) -> tuple[str, int, str]:
    code = "http_error"
    detail = ""
    try:
        body = response.json()
    except ValueError:
        body = None
    if isinstance(body, dict):
        raw_code = body.get("error")
        if raw_code:
            code = str(raw_code)
        raw_detail = body.get("detail")
        if isinstance(raw_detail, list):
            detail = "; ".join(_validation_line(item) for item in raw_detail)
        elif raw_detail is not None:
            detail = str(raw_detail)
    if not detail:
        detail = (response.text or "").strip()[:200] or "no detail returned"
    return code, response.status_code, detail


def call(client: httpx.Client, method: str, path: str, **kwargs: object) -> httpx.Response:
    """Issue one request, translating transport and status failures into types."""
    try:
        response = client.request(method, path, **kwargs)
    except httpx.RequestError as exc:
        raise ConnectionFailure(
            f"cannot reach RANTI_API_URL={client.base_url} "
            f"({exc.__class__.__name__}: {exc})"
        ) from exc
    if not 200 <= response.status_code < 300:
        code, status, detail = _error_info(response)
        raise ApiError(code, status, detail)
    return response


# ---------------------------------------------------------------------------
# Persisted identity
# ---------------------------------------------------------------------------


def config_path() -> Path:
    return Path(os.environ.get("RANTI_CLI_CONFIG", DEFAULT_CONFIG_PATH)).expanduser()


def load_identity() -> dict:
    path = config_path()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return data


def save_identity(data: dict) -> None:
    """Persist the identity file, always with owner-only permissions."""
    path = config_path()
    if path.parent and not path.parent.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, indent=2, sort_keys=True) + "\n"
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    except OSError as exc:
        raise IdentityError(f"cannot write identity file {path}: {exc}") from exc
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(payload)
    finally:
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass


def prompt_display_name() -> str:
    try:
        raw = input(f"display name [{DEFAULT_DISPLAY_NAME}]: ").strip()
    except EOFError:
        raw = ""
    return raw or DEFAULT_DISPLAY_NAME


def ensure_identity(*, ask_display_name: bool = False) -> dict:
    """Load the persisted CLI identity, generating and saving what is missing.

    The surface_user_id is generated once and then reused forever, so a person
    keeps the same memory namespace across invocations.
    """
    data = load_identity()
    changed = False
    if data.get("surface") != SURFACE:
        data["surface"] = SURFACE
        changed = True
    if not data.get("surface_user_id"):
        data["surface_user_id"] = uuid.uuid4().hex
        changed = True
    if not data.get("display_name"):
        data["display_name"] = prompt_display_name() if ask_display_name else DEFAULT_DISPLAY_NAME
        changed = True
    if changed:
        save_identity(data)
    return data


def register_user(client: httpx.Client, identity: dict) -> str:
    """Resolve the API user id for the persisted identity (idempotent)."""
    payload = {
        "surface": SURFACE,
        "surface_user_id": identity["surface_user_id"],
        "display_name": identity["display_name"],
    }
    response = call(client, "POST", "/users", json=payload)
    data = response.json()
    user_id = data.get("id") if isinstance(data, dict) else None
    if not user_id:
        raise ApiError("invalid_response", response.status_code, "POST /users returned no id")
    return str(user_id)


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------


def truncate(value: object, limit: int) -> str:
    text = " ".join(str(value).split())
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def fmt_number(value: object, digits: int = 2) -> str:
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return str(value)


def print_table(headers: list[str], rows: list[list[str]]) -> None:
    widths = [len(header) for header in headers]
    for row in rows:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], len(cell))
    print("  ".join(header.ljust(widths[index]) for index, header in enumerate(headers)))
    print("  ".join("-" * width for width in widths))
    for row in rows:
        print("  ".join(str(cell).ljust(widths[index]) for index, cell in enumerate(row)))


def fail(message: str) -> None:
    print(f"[FAIL] {message}", file=sys.stderr)


def wrap_cell(text: object, width: int) -> list[str]:
    lines: list[str] = []
    for paragraph in str(text).splitlines() or [""]:
        lines.extend(textwrap.wrap(paragraph, width) or [""])
    return lines


def print_side_by_side(left_title: str, left_text: str, right_title: str, right_text: str) -> None:
    width = 44
    left_lines = wrap_cell(left_text, width)
    right_lines = wrap_cell(right_text, width)
    print(f"{left_title.ljust(width)}  {right_title}")
    print(f"{'-' * width}  {'-' * width}")
    for index in range(max(len(left_lines), len(right_lines))):
        left = left_lines[index] if index < len(left_lines) else ""
        right = right_lines[index] if index < len(right_lines) else ""
        print(f"{left.ljust(width)}  {right}")


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def command_chat(args: argparse.Namespace, client: httpx.Client) -> int:
    identity = ensure_identity(ask_display_name=True)
    user_id = register_user(client, identity)
    memory_enabled = True
    print(f"[OK] chat as {identity['display_name']} (surface={SURFACE}, user_id={user_id})")
    print("Commands: /nomem toggles memory, /quit exits.")

    while True:
        try:
            raw = input("you> ")
        except (EOFError, KeyboardInterrupt):
            print()
            break
        text = raw.strip()
        if not text:
            continue
        if text == "/quit":
            break
        if text == "/nomem":
            memory_enabled = not memory_enabled
            print(f"[OK] memory {'on' if memory_enabled else 'off'} for subsequent turns")
            continue

        payload = {
            "surface": SURFACE,
            "surface_user_id": identity["surface_user_id"],
            "display_name": identity["display_name"],
            "text": text,
            "memory_enabled": memory_enabled,
        }
        response = call(client, "POST", "/chat/turn", json=payload)
        data = response.json()
        print(str(data.get("reply", "")))
        print("MEMORY")
        recalled = data.get("recalled") or []
        if not recalled:
            print("  no memories recalled")
        else:
            for item in recalled:
                print(
                    f"  - {item.get('text', '')} "
                    f"(salience={fmt_number(item.get('salience'))}, "
                    f"surface={item.get('origin_surface', 'unknown')})"
                )
        if data.get("memory_degraded"):
            note = data.get("memory_note") or "relayer unavailable"
            print(f"[WARN] memory degraded: {note}")
    return 0


def command_recall(args: argparse.Namespace, client: httpx.Client) -> int:
    identity = ensure_identity()
    user_id = register_user(client, identity)
    response = call(
        client,
        "POST",
        "/chat/recall",
        json={"user_id": user_id, "query": args.query, "budget": args.budget},
    )
    items = response.json() or []
    if not items:
        print("[OK] no memories recalled")
        return 0
    rows = [
        [
            str(index + 1),
            truncate(item.get("text", ""), TEXT_LIMIT),
            fmt_number(item.get("salience")),
            fmt_number(item.get("importance")),
            str(item.get("origin_surface", "unknown")),
            truncate(item.get("blob_id", ""), BLOB_LIMIT),
        ]
        for index, item in enumerate(items)
    ]
    print_table(["#", "text", "salience", "importance", "surface", "blob_id"], rows)
    return 0


def command_memories(args: argparse.Namespace, client: httpx.Client) -> int:
    identity = ensure_identity()
    user_id = register_user(client, identity)
    response = call(
        client,
        "GET",
        f"/memories/{user_id}",
        params={"include_inactive": "true" if args.all else "false"},
    )
    items = response.json() or []
    if not items:
        print("[OK] no memories stored")
        return 0
    rows = [
        [
            truncate(item.get("text", ""), TEXT_LIMIT),
            str(item.get("status", "")),
            fmt_number(item.get("importance")),
            str(item.get("origin_surface", "unknown")),
            truncate(item.get("blob_id", ""), BLOB_LIMIT),
        ]
        for item in items
    ]
    print_table(["text", "status", "importance", "surface", "blob_id"], rows)
    return 0


STATS_FIELDS = [
    ("display_name", "display name"),
    ("surface", "surface"),
    ("namespace", "namespace"),
    ("active", "active"),
    ("superseded", "superseded"),
    ("contradicted", "contradicted"),
    ("open_contradictions", "open contradictions"),
    ("turns", "turns"),
    ("relayer_memory_count", "relayer memories"),
    ("relayer_storage_bytes", "relayer bytes"),
]


def command_stats(args: argparse.Namespace, client: httpx.Client) -> int:
    identity = ensure_identity()
    user_id = register_user(client, identity)
    response = call(client, "GET", f"/memories/{user_id}/stats")
    data = response.json()
    rows = [[label, str(data.get(key, ""))] for key, label in STATS_FIELDS]
    print_table(["field", "value"], rows)
    if data.get("relayer_degraded"):
        print("[WARN] relayer counts are degraded")
    return 0


def command_evidence(args: argparse.Namespace, client: httpx.Client) -> int:
    response = call(client, "GET", "/evidence/users")
    items = response.json() or []
    if not items:
        print("[OK] no users registered")
        return 0
    rows = []
    for item in items:
        active = int(item.get("active", 0) or 0)
        rows.append(
            [
                truncate(item.get("display_name", ""), 24),
                str(item.get("surface", "")),
                str(active),
                str(item.get("superseded", 0)),
                str(item.get("contradicted", 0)),
                str(item.get("turns", 0)),
                "[OK]" if active >= EVIDENCE_THRESHOLD else "-",
            ]
        )
    print_table(
        ["display_name", "surface", "active", "superseded", "contradicted", "turns", "proof"],
        rows,
    )
    return 0


def command_counterfactual(args: argparse.Namespace, client: httpx.Client) -> int:
    response = call(client, "POST", f"/chat/counterfactual/{args.turn_id}")
    data = response.json()
    print(f"turn_id: {data.get('turn_id', args.turn_id)}")
    print(f"user text: {data.get('user_text', '')}")
    print("")
    print_side_by_side(
        "WITH MEMORY",
        str(data.get("with_memory", "")),
        "WITHOUT MEMORY",
        str(data.get("without_memory", "")),
    )
    print("")
    print(f"summary: {data.get('summary', '')}")
    if data.get("reply_changed"):
        print("[WARN] memory changed the reply")
    return 0


def command_passport_export(args: argparse.Namespace, client: httpx.Client) -> int:
    identity = ensure_identity()
    user_id = register_user(client, identity)
    response = call(client, "GET", f"/memories/{user_id}/passport")
    data = response.json()
    target = Path(args.path).expanduser()
    try:
        target.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    except OSError as exc:
        fail(f"cannot write passport to {target}: {exc}")
        return 1
    print(f"[OK] passport written to {target}")
    return 0


def command_passport_import(args: argparse.Namespace, client: httpx.Client) -> int:
    source = Path(args.path).expanduser()
    if not source.is_file():
        fail(f"passport file not found: {source}")
        return 1
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except ValueError as exc:
        fail(f"passport file is not valid JSON: {exc}")
        return 1
    response = call(client, "POST", "/memories/passport/import", json=payload)
    data = response.json()
    print(f"[OK] imported {data.get('imported', 0)} memories, skipped {data.get('skipped', 0)}")
    return 0


# ---------------------------------------------------------------------------
# Argument parsing and entry point
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cli",
        description="Ranti terminal client. Talks to a running Ranti API over HTTP.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    chat = subparsers.add_parser("chat", help="interactive conversation loop")
    chat.set_defaults(func=command_chat)

    recall = subparsers.add_parser("recall", help="ranked recall for a query")
    recall.add_argument("query")
    recall.add_argument("--budget", type=int, default=6)
    recall.set_defaults(func=command_recall)

    memories = subparsers.add_parser("memories", help="list stored memories")
    memories.add_argument("--all", action="store_true", help="include inactive memories")
    memories.set_defaults(func=command_memories)

    stats = subparsers.add_parser("stats", help="per-user memory counts")
    stats.set_defaults(func=command_stats)

    evidence = subparsers.add_parser("evidence", help="evidence leaderboard")
    evidence.set_defaults(func=command_evidence)

    counterfactual = subparsers.add_parser(
        "counterfactual", help="replay a turn with and without memory"
    )
    counterfactual.add_argument("turn_id")
    counterfactual.set_defaults(func=command_counterfactual)

    passport = subparsers.add_parser("passport", help="export or import a memory passport")
    passport_actions = passport.add_subparsers(dest="action", required=True)

    export = passport_actions.add_parser("export", help="write the passport to a file")
    export.add_argument("path")
    export.set_defaults(func=command_passport_export)

    import_ = passport_actions.add_parser("import", help="read a passport file and POST it")
    import_.add_argument("path")
    import_.set_defaults(func=command_passport_import)

    return parser


def main(argv: list[str] | None = None, transport: httpx.BaseTransport | None = None) -> int:
    """Run one CLI invocation.

    Returns a process exit code. ``transport`` is a test seam only.
    """
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        with create_client(transport) as client:
            return int(args.func(args, client))
    except ConnectionFailure as exc:
        fail(str(exc))
        return 1
    except ApiError as exc:
        fail(f"{exc.code} (HTTP {exc.status}): {exc.detail}")
        return 1
    except IdentityError as exc:
        fail(str(exc))
        return 1
    except OSError as exc:
        fail(f"filesystem error: {exc}")
        return 1
    except KeyboardInterrupt:
        print()
        fail("interrupted")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
