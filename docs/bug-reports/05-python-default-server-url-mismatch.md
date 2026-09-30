# Python SDK defaults server_url to http://localhost:8000 while the TypeScript SDK defaults to the hosted relayer

Related: #1012 documents another TypeScript versus Python divergence, so this class of issue is already on the maintainers' radar.

Reproduced locally: yes (default value and resulting connection failure observed; no credentials needed).

## Labels

`bug`, `python-sdk`, `docs`, `api-parity`

## Environment

- OS: Linux Mint 22.3 (Zena), kernel `Linux 7.0.0-29-generic x86_64`
- Python: `Python 3.12.3` (tested with `/home/ksschkw/kss/IDK/.research/venv/bin/python`)
- SDK: `memwal==0.1.11`
- Relayer: `https://relayer.memory.walrus.xyz` is reachable and healthy (`GET /health` HTTP 200); `http://localhost:8000` refuses connections in this environment.
- Network: outbound HTTPS works, no local relayer listening.

## Summary

`MemWalConfig.server_url` and `MemWal.create(server_url=...)` default to `http://localhost:8000` (`memwal/types.py:21,51`; `memwal/client.py:305`). The TypeScript SDK defaults the same field to `https://relayer.memory.walrus.xyz` (`packages/sdk/src/memwal.ts:391`), and the docs state the Python SDK "mirrors the TypeScript `MemWal` client exactly" (`docs/sdk/overview.md:74`). A Python user who copies the minimal create call (as shown in the package docstring and the README's context-manager example, both of which omit `server_url`/`env`) gets a connection error instead of the managed relayer, while the equivalent TypeScript code works.

## Steps to reproduce

1. Show the default and the connection failure without needing credentials:

```python
import os, asyncio
from memwal import MemWal, MemWalConfig

key = os.urandom(32).hex()
cfg = MemWalConfig(key=key, account_id="0xabc")
print("Python MemWalConfig default server_url:", cfg.server_url)

async def main():
    m = MemWal.create(key=key, account_id="0xabc")
    try:
        await m.health()
    except Exception as e:
        print(f"health() against default raised {type(e).__module__}.{type(e).__name__}: {e}")
    await m.close()

asyncio.run(main())
```

2. Compare the TypeScript default: `packages/sdk/src/memwal.ts:391` uses `config.serverUrl ?? "https://relayer.memory.walrus.xyz"`.

3. Confirm the parity claim: `docs/sdk/overview.md:74` says the Python SDK "mirrors the TypeScript `MemWal` client exactly" with "same methods, same relayer, same auth flow".

4. Optionally confirm the hosted relayer works from the same machine: `curl -sS https://relayer.memory.walrus.xyz/health` returns HTTP 200.

## Expected behaviour

Either the Python default matches the TypeScript default (`https://relayer.memory.walrus.xyz`), or the docs and examples stop claiming exact parity and every Python example passes `server_url`/`env` explicitly. A user copying the minimal `MemWal.create(key=..., account_id=...)` form should reach the same relayer as the equivalent TypeScript code.

## Actual behaviour

```
Python MemWalConfig default server_url: http://localhost:8000
health() against default raised httpx.ConnectError: All connection attempts failed
```

The installed package declares the default at `memwal/types.py:21` (`DEFAULT_SERVER_URL = "http://localhost:8000"`) and the `MemWal.create` signature at `memwal/client.py:305` also defaults `server_url="http://localhost:8000"`. The TypeScript SDK at `packages/sdk/src/memwal.ts:391` defaults to the hosted relayer, so the two SDKs disagree. The Python README's async and sync quick-start examples do pass an explicit `server_url` (`packages/python-sdk-memwal/README.md:49,84`), but the module docstring (`memwal/__init__.py:10-22`) and the README "Context Manager" example (`packages/python-sdk-memwal/README.md:109-121`) omit it, and those are the minimal forms users copy.

## Impact

A Python builder following the documented minimal create call hits `httpx.ConnectError` unless they already know to pass `server_url` or `env`, while the same code shape in TypeScript talks to production. This is a bad first-run experience and, worse, it can send a developer down a long debugging path (is the relayer down? is my key wrong? is the network firewalled?) when the real cause is a differing default that the docs claim does not exist. It also creates a security-shaped inconsistency: TypeScript defaults to TLS, Python defaults to plaintext on loopback, so any test that "works locally" with the Python default is exercising a different transport from production.

## Suggested fix or API shape

Align the defaults and make the choice explicit:

In `memwal/types.py`:

```python
DEFAULT_SERVER_URL = "https://relayer.memory.walrus.xyz"
```

If keeping `localhost:8000` as a developer convenience is intentional, then do not claim exact parity. Update `docs/sdk/overview.md:74` and the `MemWal`/`MemWalSync` docstrings to state that the Python default targets a local relayer and that `env="prod"` (or `server_url=...`) is required for the hosted deployment, and add `server_url`/`env` to the module docstring and the README context-manager example. Prefer failing loudly: when `server_url` is left at the localhost default, emit the same warning the SDK already uses for non-localhost plaintext HTTP, or require `env`/`server_url` explicitly.

## Evidence

Files read (installed 0.1.11 wheel, `/home/ksschkw/kss/IDK/.research/venv/lib/python3.12/site-packages/memwal/`):

- `memwal/types.py:18-29` - `DEFAULT_SERVER_URL = "http://localhost:8000"` and `ENV_PRESETS` (`prod` -> `https://relayer.memory.walrus.xyz`).
- `memwal/types.py:49-66` - `MemWalConfig.server_url: str = DEFAULT_SERVER_URL`; `env` only fills `server_url` when it is still the default.
- `memwal/client.py:301-331` - `MemWal.create(..., server_url: str = "http://localhost:8000", ...)`.
- `memwal/__init__.py:6-23` - module docstring quick start uses `key`/`account_id` only.
- `memwal/client.py:1607-1620` - `MemWalSync.create` with the same localhost default.

Repository files read (`/home/ksschkw/kss/IDK/.research/MemWal`):

- `packages/sdk/src/memwal.ts:388-405` - TypeScript default `https://relayer.memory.walrus.xyz` and the create docstring.
- `packages/sdk/src/types.ts:20` - TypeScript config comment "(default: https://relayer.memory.walrus.xyz)".
- `packages/python-sdk-memwal/memwal/types.py:21` and `.../client.py:344,1749` - same localhost default in the repository Python SDK at 0.1.11.
- `docs/sdk/overview.md:74` - "The Python SDK mirrors the TypeScript `MemWal` client exactly".
- `packages/python-sdk-memwal/README.md:40-52,75-86` - quick-start examples that pass an explicit hosted `server_url`.
- `packages/python-sdk-memwal/README.md:109-121` - context-manager example that omits `server_url`, so it would use localhost.

Commands run:

- The default/connect script above via `/home/ksschkw/kss/IDK/.research/venv/bin/python` (observed output quoted verbatim).
- `curl -sS --max-time 5 http://localhost:8000/health` - `curl: (7) Failed to connect to localhost port 8000`.
- `curl -sS https://relayer.memory.walrus.xyz/health` - HTTP 200.
