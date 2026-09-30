# Python SDK has no forget or delete method while the relayer exposes POST /api/forget

Reproduced locally: yes.

## Labels

`bug`, `python-sdk`, `api-parity`

## Environment

- OS: Linux Mint 22.3 (Zena), kernel `Linux 7.0.0-29-generic x86_64`
- Python: `Python 3.12.3` (tested with `/home/ksschkw/kss/IDK/.research/venv/bin/python`)
- SDK: `memwal==0.1.11` (`pip show memwal` reports version 0.1.11; `memwal.__version__ == "0.1.11"`)
- Relayer: `https://relayer.memory.walrus.xyz`, `GET /health` returned HTTP 200 with `write_ready: true`, `mode: "production"`, `apiVersion: "1.0.0"`, and `prompt_versions.extract: "extract.v6"`
- Network: outbound HTTPS to the managed relayer works. `http://localhost:8000` (the SDK default) is not running here.

Full `/health` body observed:

```json
{"status":"ok","version":"0.1.0","relayerVersion":"0.1.0","apiVersion":"1.0.0","minSupportedSdk":{"typescript":"0.0.4","python":"0.1.0","mcp":"0.0.1"},"featureFlags":{"auth.accountBoundNonce":true,"auth.sealSessionHeader":true,"config.publicDeploymentMetadata":true,"remember.asyncJobs":true,"remember.bulk":true,"runtime.versionEndpoint":true},"deprecations":[{"surface":"header:x-delegate-key","deprecatedSince":"1.0.0","removalApiVersion":"2.0.0","guidance":"Use x-seal-session for relayer-managed SEAL decrypt flows; manual-mode requests should send no decrypt credential."},{"surface":"env:SEAL_KEY_SERVERS","deprecatedSince":"1.0.0","removalApiVersion":"2.0.0","guidance":"Use SEAL_SERVER_CONFIGS so independent and committee key-server configs share one JSON schema."}],"build":{"commit":"0f52364949a66312a039180981ba68c8f3b1161a"},"mode":"production","prompt_versions":{"extract":"extract.v6","ask":"ask.v2"},"write_ready":true,"writes":"ok"}
```

## Summary

The relayer exposes `POST /api/forget`, which deletes this owner's vector index rows for a namespace, and the SDK's own `MemWalMock`/`MemWalMockSync` classes expose `forget(blob_id)`. The live `MemWal` and `MemWalSync` classes expose no `forget`, `delete`, or `erase` method at all, and nothing in the package ever calls `/api/forget`. A Python builder therefore has no in-SDK erasure path and must hand-roll Ed25519-signed HTTP requests to a private endpoint to forget a memory.

Note: the maintainers' feature template states that forget/delete "already have threads" (`.github/ISSUE_TEMPLATE/feature.yml`), so this may be a duplicate at the product level. It is filed anyway because the specific mismatch below is concrete and reproducible: server endpoint exists, mock exposes it, live client does not.

## Steps to reproduce

1. Install the SDK: `pip install memwal==0.1.11` (or activate the venv used here: `/home/ksschkw/kss/IDK/.research/venv/bin/python`).
2. Run this script:

```python
import inspect, os
from memwal import MemWal, MemWalSync, MemWalMock, MemWalMockSync

print("memwal version:", __import__("memwal").__version__)
for name, cls in [("MemWal", MemWal), ("MemWalSync", MemWalSync),
                  ("MemWalMock", MemWalMock), ("MemWalMockSync", MemWalMockSync)]:
    print(f"{name}: has forget = {hasattr(cls, 'forget')}")

key = os.urandom(32).hex()
client = MemWalSync.create(key=key, account_id="0xabc")
try:
    client.forget("mock-blob-000001")
except AttributeError as e:
    print("AttributeError:", e)
finally:
    client.close()
```

3. Optionally confirm the endpoint is not wrapped anywhere: `grep -rn "api/forget" $(python -c "import memwal, os; print(os.path.dirname(memwal.__file__))")` returns nothing.

## Expected behaviour

`MemWal` and `MemWalSync` expose an erasure method that wraps the documented `POST /api/forget` endpoint (namespace-scoped `{"namespace": "..."}` returning `{"deleted": N, "namespace": "...", "owner": "..."}`), matching the server API documented at `docs/relayer/api-reference.md`. `MemWalMock.forget` and the live client should accept the same argument shape so tests can stand in for production.

## Actual behaviour

The live classes have no such attribute; the mocks do:

```
memwal version: 0.1.11
MemWal: has forget = False
MemWalSync: has forget = False
MemWalMock: has forget = True
MemWalMockSync: has forget = True
AttributeError: 'MemWalSync' object has no attribute 'forget'
```

`grep -rn "forget("` across the installed package matches only `mock.py:458` and `mock.py:683-684`; `client.py`, `types.py`, `utils.py`, `middleware.py`, and `compatibility.py` contain no forget/delete implementation. There is also no `/api/forget` string in the package.

## Impact

A Python builder who stores user memories through the SDK cannot delete them through the SDK. This blocks GDPR-style erasure and "clear this conversation's memory" features, which are exactly the write-side controls a memory product needs. The only workaround is to reimplement the SDK's signed-request format (timestamp, method, path, body hash, nonce, account id) against `/api/forget`, which is undocumented for Python callers and easy to get wrong. It also makes the mock actively misleading: code that passes against `MemWalMock.forget(blob_id)` raises `AttributeError` against the real client.

## Suggested fix or API shape

Add an async method (and a sync wrapper), keeping the server's namespace-scoped semantics rather than the mock's per-blob signature:

```python
@dataclass
class ForgetResult:
    deleted: int
    namespace: str
    owner: str

async def forget(self, namespace: Optional[str] = None) -> ForgetResult:
    data = await self._signed_request("POST", "/api/forget", {
        "namespace": namespace or self._namespace,
    })
    return ForgetResult(deleted=data["deleted"], namespace=data["namespace"], owner=data["owner"])
```

Also reconcile the mock: either change `MemWalMock.forget` to the same namespace-scoped `(namespace=None) -> ForgetResult` shape, or keep the per-blob helper under a different name so the mock and the live client do not disagree. If the server later gains per-blob deletion, add `ForgetOptions(blob_id=..., namespace=...)` so both exist.

## Evidence

Files read (installed 0.1.11 wheel under `/home/ksschkw/kss/IDK/.research/venv/lib/python3.12/site-packages/memwal/`):

- `memwal/__init__.py:129` - `__version__ = "0.1.11"`; `__init__.py:75-127` - `__all__` contains no forget/delete export.
- `memwal/mock.py:458-464` - `MemWalMock.forget(self, blob_id: str) -> bool`.
- `memwal/mock.py:683-684` - `MemWalMockSync.forget`.
- `memwal/client.py` - no `forget`/`delete` method (method list at client.py:275-1814).
- `memwal/types.py` - no `ForgetResult`/`ForgetOptions` type.

Repository files read (`/home/ksschkw/kss/IDK/.research/MemWal`):

- `packages/python-sdk-memwal/memwal/mock.py:458,683` - same mock-only `forget`; `packages/python-sdk-memwal/memwal/client.py` - no forget (the checked-out tree is the same 0.1.11, so this is not fixed on `dev`).
- `services/server/src/main.rs:2331` - `.route("/api/forget", post(routes::forget))`.
- `services/server/src/routes/admin.rs:51-95` - `forget` handler, owner-scoped namespace delete.
- `services/server/src/types.rs:1977-1985` - `ForgetRequest { namespace }` and `ForgetResponse { deleted, namespace, owner }`.
- `docs/relayer/api-reference.md:517-547` - documented `POST /api/forget`.
- `.github/ISSUE_TEMPLATE/feature.yml` - states forget/delete "already have threads".

Commands run:

- `.research/venv/bin/python` script above (observed output quoted verbatim).
- `grep -rn "api/forget\|forget(" .research/venv/lib/python3.12/site-packages/memwal/` - matches only `mock.py`.
- `curl -sS https://relayer.memory.walrus.xyz/health` - HTTP 200, body quoted above.
- `pip show memwal` - `Version: 0.1.11`.
