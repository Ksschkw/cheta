# Python recall() cannot pass scoring_weights, so recency and importance ranking are unreachable from the high-level API

Related: #968 (closed) asked for a default relevance cutoff in the TypeScript SDK. This is a different gap: the ranking machinery already exists and is wired to the manual path, but the high-level Python recall() cannot reach it.

Reproduced locally: yes (wire-body capture with a stubbed transport; no live relayer write).

## Labels

`bug`, `python-sdk`, `api-parity`

## Environment

- OS: Linux Mint 22.3 (Zena), kernel `Linux 7.0.0-29-generic x86_64`
- Python: `Python 3.12.3` (tested with `/home/ksschkw/kss/IDK/.research/venv/bin/python`)
- SDK: `memwal==0.1.11`
- Relayer: `https://relayer.memory.walrus.xyz`, `GET /health` HTTP 200, `apiVersion: "1.0.0"` (server code at the same revision is in the checked-out repo)
- Network: not required for this repro; the wire body was captured by overriding `_signed_request`, so no request left the machine.

## Summary

The relayer's `POST /api/recall` request struct accepts `scoring_weights` (semantic, recency, recency_half_life_days, importance) and a `sort` mode, and the TypeScript SDK's `recall()` sends `scoring_weights`. The Python high-level `recall()` sends only `query`, `limit`, and `namespace`, and its `RecallParams` dataclass has no field for weights, so composite ranking cannot be requested. The only Python path that sends weights is `recall_manual()`, which takes a pre-computed vector and returns blob ids plus distances with no decrypted text, so a normal Python caller cannot get both ranked order and text.

## Steps to reproduce

1. Run this script. It subclasses `MemWal` and overrides `_signed_request` to capture the exact JSON body that would go on the wire; nothing is sent.

```python
import asyncio, os, dataclasses
from memwal import MemWal, RecallManualOptions, RecallParams, ScoringWeights

captured = []

class CaptureMemWal(MemWal):
    async def _ensure_compatible_relayer(self):
        return {"apiVersion": "1.0.0", "relayerVersion": "capture",
                "minSupportedSdk": {"python": "0.0.0"}}
    async def _signed_request(self, method, path, body, accepted_statuses=(200,),
                              include_seal_session=True):
        captured.append((method, path, body))
        if path == "/api/recall":
            return {"results": [], "total": 0}
        if path == "/api/recall/manual":
            return {"results": [{"blob_id": "blob-1", "distance": 0.12}], "total": 1}
        return {}

async def main():
    c = CaptureMemWal.create(key=os.urandom(32).hex(), account_id="0xabc")
    await c.recall(RecallParams(query="food allergies", limit=5, namespace="profile"))
    await c.recall_manual(RecallManualOptions(vector=[0.1]*16, limit=5,
                                               namespace="profile",
                                               scoring_weights=ScoringWeights(recency=2.0,
                                                                              importance=1.0)))
    for m, p, b in captured:
        print(m, p, b)
    print("RecallParams fields:",
          [f.name for f in dataclasses.fields(RecallParams)])
    try:
        await c.recall(RecallParams(query="q", scoring_weights=ScoringWeights(recency=1.0)))
    except TypeError as e:
        print("TypeError:", e)

asyncio.run(main())
```

2. Compare with the TypeScript SDK, which does send the field for the same call shape: `packages/sdk/src/memwal.ts` builds the `/api/recall` body with `scoring_weights: scoringWeightsToWire(options.scoringWeights)`.

3. Compare with the server contract: `services/server/src/types.rs` `RecallRequest` includes `scoring_weights` and `sort`.

## Expected behaviour

`RecallParams` carries optional scoring weights (and, ideally, `sort`), and `recall()` forwards them to `POST /api/recall`, mirroring the TypeScript SDK. Passing `RecallParams(query=..., scoring_weights=ScoringWeights(recency=...))` should produce a body containing `"scoring_weights": {"recency": ...}` and the relayer should return results whose composite `score` reflects recency and importance.

## Actual behaviour

Captured wire bodies:

```
POST /api/recall {'query': 'food allergies', 'limit': 5, 'namespace': 'profile'}
POST /api/recall/manual {'vector': [0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1], 'limit': 5, 'namespace': 'profile', 'scoring_weights': {'recency': 2.0, 'importance': 1.0}}
RecallParams fields: ['query', 'limit', 'namespace', 'max_distance']
TypeError: RecallParams.__init__() got an unexpected keyword argument 'scoring_weights'
```

`dataclasses.fields(RecallManualResult.results[0])` is `['blob_id', 'distance']`, so the manual path returns no text. The installed `MemWal.recall` signature is:

```
(self, query: "'str | RecallParams'", limit: 'int' = 10, namespace: 'Optional[str]' = None, max_distance: 'Optional[float]' = None) -> 'RecallResult'
```

## Impact

A Python builder whose memories have different ages or importance cannot ask the relayer to rank by recency or importance from the normal text-returning recall path. To get recency ranking they must switch to the manual API, which means computing embeddings client-side, downloading and SEAL-decrypting blobs themselves, and reconstructing text, a large and undocumented amount of work. The practical result is that "newest/most important memory wins" is not implementable from the Python SDK's standard surface, and the Python and TypeScript SDKs return different orderings for the same query while the docs claim they mirror each other.

## Suggested fix or API shape

Add optional fields to `RecallParams` and forward them in `recall()`:

```python
@dataclass
class RecallParams:
    query: str
    limit: int = 10
    namespace: Optional[str] = None
    max_distance: Optional[float] = None
    scoring_weights: Optional[ScoringWeights] = None
    sort: Optional[str] = None  # "relevance" | "recent"
```

In `MemWal.recall`, build the body and only include the keys when set, so the default request stays byte-identical (matching the TypeScript SDK's `undefined`-is-dropped behavior):

```python
body = {"query": query_text, "limit": limit, "namespace": namespace or self._namespace}
if params.scoring_weights is not None:
    body["scoring_weights"] = params.scoring_weights.to_wire()
if params.sort is not None:
    body["sort"] = params.sort
data = await self._signed_request("POST", "/api/recall", body)
```

Also surface the returned composite `score` on `RecallMemory` (see the companion report on `created_at`/`score` being dropped), because otherwise a caller cannot tell whether ranking actually changed.

## Evidence

Files read (installed 0.1.11 wheel, `/home/ksschkw/kss/IDK/.research/venv/lib/python3.12/site-packages/memwal/`):

- `memwal/client.py:673-743` - `recall()`; the `/api/recall` body at `client.py:716-720` contains only `query`, `limit`, `namespace`.
- `memwal/client.py:1097-1127` - `recall_manual()`; `client.py:1114-1115` is the only `scoring_weights` send in the package.
- `memwal/client.py:1109-1113` - manual body; `client.py:1123-1124` - maps hits to `RecallManualHit(blob_id, distance)` only.
- `memwal/types.py:93-108` - `RecallParams` has no `scoring_weights`/`sort`.
- `memwal/types.py:122-150` - `ScoringWeights` with `to_wire()`.
- `memwal/types.py:308-331` - `RecallManualOptions.scoring_weights` and `RecallManualHit(blob_id, distance)`.

Repository files read (`/home/ksschkw/kss/IDK/.research/MemWal`):

- `services/server/src/types.rs:1513-1536` - `RecallRequest` has `scoring_weights: Option<ScoringWeights>` and `sort: Option<RecallSort>`.
- `services/server/src/types.rs:1890-1906` - `RecallManualRequest.scoring_weights`.
- `packages/sdk/src/memwal.ts:939` - TypeScript `recall()` sends `scoring_weights: scoringWeightsToWire(options.scoringWeights)`.
- `packages/sdk/src/types.ts:184` - TypeScript `RecallOptions.scoringWeights`; `sort` at `types.ts:200`.
- `docs/relayer/api-reference.md:312-330` - documents `scoring_weights` as working on `/api/recall`, `/api/recall/manual`, and `/api/ask`.
- `docs/sdk/overview.md:74` - claims the Python SDK mirrors the TypeScript `MemWal` client exactly.

Commands run:

- The capture script above via `/home/ksschkw/kss/IDK/.research/venv/bin/python` (observed output quoted verbatim).
- `grep -rn "scoring_weights\|ScoringWeights" .research/venv/lib/python3.12/site-packages/memwal/` - only `client.py:1114-1115`, `types.py:123-149,316,322`.
- `curl -sS https://relayer.memory.walrus.xyz/health` - HTTP 200.
