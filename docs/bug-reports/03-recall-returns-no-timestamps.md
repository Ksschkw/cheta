# Python recall() drops the created_at and score fields the relayer returns and the TypeScript SDK exposes

Related: no existing issue found. #993 concerns seal and sui dependencies, not the fields recall() returns.

Reproduced locally: partially (field presence proven by running the mock and by reading the response mapper; no live authenticated recall was run).

## Labels

`bug`, `python-sdk`, `api-parity`

## Environment

- OS: Linux Mint 22.3 (Zena), kernel `Linux 7.0.0-29-generic x86_64`
- Python: `Python 3.12.3` (tested with `/home/ksschkw/kss/IDK/.research/venv/bin/python`)
- SDK: `memwal==0.1.11`
- Relayer: `https://relayer.memory.walrus.xyz`, `GET /health` HTTP 200, `apiVersion: "1.0.0"`
- Network: local mock used for the observed output; the server-side field shapes were verified by reading the checked-out server source at the same revision.

## Summary

The relayer's `/api/recall` response includes `created_at` (the write time of the memory, added for WALM-383) and, when composite ranking runs, `score`. The TypeScript SDK's `RecallMemory` interface declares `created_at`, but the Python SDK's `RecallMemory` dataclass has only `blob_id`, `text`, and `distance`, and `recall()` maps only those three keys from each result. The manual path drops the same server fields (`created_at`, `importance`) from `RecallManualHit`. A Python caller therefore cannot tell when a memory was written or what score ranked it, even though the relayer sent both.

Note: the maintainers' feature template lists timestamps as already having threads (`.github/ISSUE_TEMPLATE/feature.yml`), so the broader feature request may be a duplicate. The specific defect filed here is narrower and is not just "please add a feature": the data is already on the wire and is being discarded by the Python response mapper.

## Steps to reproduce

1. Run this script to show the Python result type has no timestamp or score field:

```python
import asyncio, dataclasses
from memwal import MemWalMock

async def main():
    m = MemWalMock()
    await m.analyze("My name is Alice. I live in Berlin. I am allergic to peanuts.")
    result = await m.recall("Alice Berlin")
    print("RecallMemory fields:",
          [f.name for f in dataclasses.fields(result.results[0])])
    print("result:", result.results[0])

asyncio.run(main())
```

2. Read the relayer contract at `services/server/src/types.rs`: `RecallResult` serializes `created_at: Option<DateTime<Utc>>` and `score: Option<f64>`, and `SearchHit.created_at` is documented as "Always present (column is NOT NULL in migration 001)".

3. Read the TypeScript contract at `packages/sdk/src/types.ts`: `RecallMemory` declares `created_at?: string` (the field is passed through as the `RecallResult` JSON type).

4. Read the Python mapper at `memwal/client.py:721-728` and confirm only `blob_id`, `text`, and `distance` are read.

## Expected behaviour

`RecallMemory` carries `created_at` (RFC 3339 string, optional for older relayers) and `score` (optional), and `recall()` maps them from the response, matching the TypeScript SDK. `RecallManualHit` carries `created_at` and `importance`, which the server's `/api/recall/manual` response already includes.

## Actual behaviour

```
RecallMemory fields: ['blob_id', 'text', 'distance']
```

The server response struct it is parsed from (repo `services/server/src/types.rs:1596-1630`) is, in field order: `blob_id`, `text`, `distance`, `score` (optional), `created_at` (optional). Neither optional field appears on the Python dataclass, and `memwal/client.py:721-728` constructs `RecallMemory(blob_id=..., text=..., distance=...)` with no attempt to read `m["created_at"]` or `m["score"]`. Likewise `memwal/client.py:1123-1124` maps manual hits to `RecallManualHit(blob_id=..., distance=...)`, dropping `created_at` and `importance` even though `docs/relayer/api-reference.md` shows them in the `/api/recall/manual` response.

## Impact

Without `created_at`, a Python builder cannot order recalled memories by write time, implement "newest wins", or show provenance of when a fact was learned without maintaining a separate local index keyed by blob id. Without `score`, a caller cannot tell whether the server's composite ranking actually ran or see the blended relevance value used to order results, which makes relevance thresholds and debugging guesswork. The TypeScript SDK exposes `created_at`, so the same workload produces different observable results in the two SDKs, contradicting the documented parity.

## Suggested fix or API shape

Add the fields to the dataclasses and map them defensively (older relayers omit both):

```python
@dataclass
class RecallMemory:
    blob_id: str
    text: str
    distance: float
    score: Optional[float] = None
    created_at: Optional[str] = None

@dataclass
class RecallManualHit:
    blob_id: str
    distance: float
    created_at: Optional[str] = None
    importance: Optional[float] = None
```

And in `MemWal.recall`:

```python
RecallMemory(
    blob_id=m["blob_id"],
    text=m["text"],
    distance=m["distance"],
    score=m.get("score"),
    created_at=m.get("created_at"),
)
```

Keeping both optional preserves compatibility with relayers that omit them.

## Evidence

Files read (installed 0.1.11 wheel, `/home/ksschkw/kss/IDK/.research/venv/lib/python3.12/site-packages/memwal/`):

- `memwal/types.py:84-90` - `RecallMemory(blob_id, text, distance)` only.
- `memwal/types.py:325-331` - `RecallManualHit(blob_id, distance)` only.
- `memwal/client.py:721-728` - recall response mapping reads only the three core keys.
- `memwal/client.py:1123-1124` - manual response mapping reads only `blob_id` and `distance`.

Repository files read (`/home/ksschkw/kss/IDK/.research/MemWal`):

- `services/server/src/types.rs:1596-1630` - `RecallResult` serializes `score` and `created_at`.
- `services/server/src/types.rs:1632-1648` - `SearchHit.created_at` is "Always present (column is NOT NULL in migration 001)".
- `packages/sdk/src/types.ts:70-85` - TypeScript `RecallMemory.created_at?: string`.
- `docs/relayer/api-reference.md:413` - "the relayer stores no separate event-time metadata and cannot filter or rank by event time" (this is about event time in `occurred_at`, not the write-time `created_at` the recall response returns; worth keeping distinct in the fix).
- `docs/relayer/api-reference.md:367-397` - manual response includes `created_at` and `importance`.

Commands run:

- The mock script above via `/home/ksschkw/kss/IDK/.research/venv/bin/python` (observed output quoted verbatim).
- `grep -n "created_at\|score" .research/venv/lib/python3.12/site-packages/memwal/client.py .research/venv/lib/python3.12/site-packages/memwal/types.py` - no recall mapping of either field.
- `curl -sS https://relayer.memory.walrus.xyz/health` - HTTP 200.
