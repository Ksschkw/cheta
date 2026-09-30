#!/usr/bin/env python3
"""Verify the Walrus Memory credentials with a real round trip.

Run this before anything else once MEMWAL_PRIVATE_KEY and MEMWAL_ACCOUNT_ID are
set. It answers one question: can this account actually write a memory, wait for
it to persist, and read it back? Credential problems in Walrus Memory surface as
401s that are hard to attribute, so this prints which part failed.

    python scripts/verify_memwal.py

Exit code 0 means the round trip worked. It writes one small memory into a
dedicated namespace (ranti.verify) so it never touches a user's memory space.
There is no SDK method to delete it; the relayer is append only. That is a
finding in its own right and is written up in docs/bug-reports/.
"""

from __future__ import annotations

import asyncio
import os
import sys
import uuid
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
VERIFY_NAMESPACE = "ranti.verify"


def load_env_file(path: Path) -> dict[str, str]:
    """Minimal .env reader. Real environment variables win over the file."""
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip()
    return values


def merged_environment() -> dict[str, str]:
    values = load_env_file(REPO_ROOT / ".env")
    values.update(os.environ)
    return values


def fail(message: str) -> int:
    print(f"[FAIL] {message}")
    return 1


async def run_verification(env: dict[str, str]) -> int:
    try:
        from memwal import MemWal
    except ImportError:
        return fail("the memwal package is not installed. Run: pip install -e .")

    key = env.get("MEMWAL_PRIVATE_KEY", "").strip()
    account_id = env.get("MEMWAL_ACCOUNT_ID", "").strip()
    server_url = (
        env.get("MEMWAL_SERVER_URL", "").strip() or "https://relayer.memory.walrus.xyz"
    )

    print("Walrus Memory credential verification")
    print(f"  relayer   : {server_url}")
    print(f"  account   : {account_id[:10] + '...' if account_id else '(missing)'}")
    print(f"  delegate  : {'set' if key else '(missing)'}")
    print(f"  namespace : {VERIFY_NAMESPACE}")
    print()

    if not key:
        return fail(
            "MEMWAL_PRIVATE_KEY is empty. Create the account and delegate key at "
            "https://memory.walrus.xyz and put it in .env"
        )
    if not account_id:
        return fail(
            "MEMWAL_ACCOUNT_ID is empty. It is the MemWalAccount object id shown in "
            "the dashboard at https://memory.walrus.xyz"
        )

    try:
        client = MemWal.create(
            key=key,
            account_id=account_id,
            server_url=server_url,
            namespace=VERIFY_NAMESPACE,
        )
    except Exception as error:  # noqa: BLE001 - reporting layer
        return fail(
            f"the credentials could not be loaded: {type(error).__name__}: {error}. "
            "MEMWAL_PRIVATE_KEY must be a hex-encoded Ed25519 seed of exactly 32 bytes "
            "(64 hex characters). Copy it again from https://memory.walrus.xyz"
        )

    try:
        health = await client.health()
    except Exception as error:  # noqa: BLE001 - reporting layer
        return fail(f"the relayer is unreachable at {server_url}: {type(error).__name__}: {error}")
    print(f"[OK] relayer health: {health.status} (api {health.api_version})")
    if getattr(health, "write_ready", True) is False:
        return fail("the relayer reports write_ready=false, so writes are paused")

    marker = f"Ranti verification marker {uuid.uuid4()}"
    try:
        stored = await client.remember_and_wait(
            marker,
            VERIFY_NAMESPACE,
            timeout_ms=90_000,
            idempotency_key=f"ranti-verify-{uuid.uuid4()}",
        )
    except Exception as error:  # noqa: BLE001 - reporting layer
        return fail(
            "the write failed: "
            f"{type(error).__name__}: {error}. A 401 here usually means the delegate key "
            "is not registered on this account, or the account and the relayer are on "
            "different networks (mainnet versus testnet)."
        )
    print(f"[OK] wrote a memory, blob_id={stored.blob_id}")

    try:
        recalled = await client.recall(
            "Ranti verification marker", limit=10, namespace=VERIFY_NAMESPACE
        )
    except Exception as error:  # noqa: BLE001 - reporting layer
        return fail(f"the read failed: {type(error).__name__}: {error}")

    hit = next(
        (memory for memory in recalled.results if memory.blob_id == stored.blob_id), None
    )
    if hit is None:
        return fail(
            f"the memory was written as {stored.blob_id} but recall did not return it. "
            "Indexing can lag; rerun once. If it persists, that is a bug worth reporting."
        )

    print(f"[OK] recalled the same memory, distance={hit.distance:.4f}")
    print()
    print("[OK] Walrus Memory is fully working with these credentials.")
    print("     Note: the verification memory stays in the ranti.verify namespace.")
    print("     The Python SDK has no delete method, so it cannot be removed from here.")

    await client.close()
    return 0


def main() -> int:
    try:
        return asyncio.run(run_verification(merged_environment()))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
