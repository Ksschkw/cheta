# No extract-only endpoint: analyze() always persists, so callers cannot dedupe or review facts before write

Reproduced locally: partly (verified from the route table, the request struct, and the handler contract; a live authenticated call was not made). Classification: capability gap and enhancement request, not a defect. This is filed as a feature-shaped report; it is not a crash, data loss, or regression.

## Labels

`enhancement`, `relayer`, `python-sdk`, `api-parity`

## Environment

- OS: Linux Mint 22.3 (Zena), kernel `Linux 7.0.0-29-generic x86_64`
- Python: `Python 3.12.3`
- SDK: `memwal==0.1.11` (`analyze` posts `/api/analyze` and always enqueues jobs)
- Relayer: `https://relayer.memory.walrus.xyz`, `GET /health` HTTP 200, `apiVersion: "1.0.0"`, `prompt_versions.extract: "extract.v6"`
- Network: relayer reachable; no write was attempted.

## Summary

`POST /api/analyze` does extraction and persistence in one call: it runs the LLM extractor and then, for every fact, embeds, SEAL-encrypts, uploads to Walrus, and stores the vector row. There is no endpoint, and no request flag, that returns extracted facts without persisting them. A hygiene layer that wants to deduplicate, filter, or let a user review facts before they become memories can only do so after the write, at which point the unwanted rows already exist and must be cleaned up with `/api/forget` (whose granularity is a whole namespace, not a fact). The relayer already has the "compute without storing" pattern for embeddings via `POST /api/embed`; extraction has no equivalent.

## Steps to reproduce

1. Inspect the route table and confirm no extract-only route exists:

```bash
grep -n "\.route(" services/server/src/main.rs | grep -i "analyze\|extract\|embed"
```

Result: only `/api/analyze` (line 2325) and `/api/embed` (line 2326); there is no `/api/extract` or equivalent.

2. Inspect the analyze request struct: `services/server/src/types.rs` `AnalyzeRequest` has only `text`, `namespace`, and `occurred_at`. There is no `dry_run`, `persist`, or `store` flag.

3. Inspect the handler contract at `services/server/src/routes/analyze.rs:132-137`: the documented flow is "2. Call LLM to extract memorable facts from text / 3. For each fact concurrently: embed + encrypt -> Walrus upload -> store". The response is `202 Accepted` with `job_ids`, i.e. the write is already queued.

4. Contrast with `POST /api/embed` (`docs/relayer/api-reference.md:434`): "Return an embedding vector for `text` without storing a memory." That is the shape extraction lacks.

## Expected behaviour

A way to get `extract.v6` facts back without writing them, so a caller can dedupe against existing memories, drop facts that fail a policy check, or show them for confirmation, and then persist only the accepted set. This should not require the caller to run their own LLM or reimplement the versioned extractor.

## Actual behaviour

There is no extract-only surface. The only extraction path always persists: `analyze` extracts synchronously and returns `job_ids` for background storage, and `analyze_and_wait` waits for those writes to finish. A caller that wants pre-write review must either (a) accept the writes and later forget the whole namespace, losing good memories too, or (b) run a separate LLM call with their own prompt, which will drift from the server's `extract.v6` behaviour and bypass its dedup context. The Python SDK exposes no dry-run either: `MemWal.analyze` builds `{"text", "namespace", optional "occurred_at"}` and posts `/api/analyze`.

## Impact

Builders of memory-quality or "hippocampus" layers cannot prevent bad facts from being written through the supported API. Every cleanup becomes an extra write plus a namespace-wide forget, which is coarse and disruptive, and the ingestion cost (embedding, SEAL encryption, Walrus upload) is paid for facts that are immediately discarded. It also makes it hard to audit what the extractor produced before it is durably stored, which matters for user-visible memory controls. This is a real workflow blocker, but it is a missing capability rather than incorrect behaviour, so it belongs under the feature-request label.

## Suggested fix or API shape

Reuse the existing extraction contract and add a non-persisting mode rather than a new pipeline. Two reasonable shapes:

1. A flag on analyze:

```json
POST /api/analyze
{ "text": "...", "namespace": "demo", "occurred_at": "...", "dry_run": true }
```

Response (no `job_ids`, no writes):

```json
{
  "facts": [
    { "text": "User lives in Hanoi", "importance": 0.5 },
    { "text": "User prefers dark mode", "importance": 0.5 }
  ],
  "fact_count": 2,
  "status": "preview",
  "owner": "0x..."
}
```

2. A dedicated read-shaped endpoint, mirroring `/api/embed`:

```json
POST /api/extract
{ "text": "...", "namespace": "demo", "occurred_at": "..." }
```

Either way, keep the response aligned with the current `facts` array (including `text` and the importance bucket) so callers can feed accepted facts back into `remember`/`remember_manual`. In the Python SDK, expose this as `analyze(text, dry_run=True)` returning facts with no `job_ids`, and document that `dry_run` skips Walrus upload and indexing.

## Evidence

Repository files read (`/home/ksschkw/kss/IDK/.research/MemWal`):

- `services/server/src/main.rs:2325-2326` - only `/api/analyze` and `/api/embed` are routed; no extract-only route.
- `services/server/src/types.rs` `AnalyzeRequest` (around lines 1938-1965) - fields are `text`, `namespace`, `occurred_at`; no dry-run or persist flag.
- `services/server/src/routes/analyze.rs:132-137` - handler docstring: extract, then "embed + encrypt -> Walrus upload -> store".
- `services/server/src/services/extractor.rs:203` - `FACT_EXTRACTION_PROMPT_VERSION = "extract.v6"`.
- `docs/relayer/api-reference.md:399-433` - analyze always enqueues `job_ids`.
- `docs/relayer/api-reference.md:434-455` - `/api/embed` is the non-storing analogue.

Files read (installed 0.1.11 wheel, `/home/ksschkw/kss/IDK/.research/venv/lib/python3.12/site-packages/memwal/`):

- `memwal/client.py:745-818` - `analyze()` posts `/api/analyze` with only `text`, `namespace`, and optional `occurred_at`; no non-persisting option.
- `memwal/client.py:820-848` - `analyze_and_wait()` always waits on the persistence jobs.

Commands run:

- `grep -n "\.route(" services/server/src/main.rs | grep -i "analyze\|extract\|embed"` - only `/api/analyze` and `/api/embed`.
- `grep -rni "extract.only\|extract_only\|/api/extract\|analyze_only" services/server/src docs/ packages/` - no matches.
- `curl -sS https://relayer.memory.walrus.xyz/health` - HTTP 200, `extract.v6`.
