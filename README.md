# Cheta

Cheta (the Igbo word for "remember") is a memory-first assistant built for the
Walrus Session 8 hackathon. It keeps one portable memory space per person across
four surfaces - a Telegram bot, a CLI, a web widget served at `/app`, and a
Chromium extension in [extension/](extension) - using Walrus Memory through the
`memwal` Python SDK. It is for developers who need an assistant to still be
accurate after weeks of use, and for anyone testing whether Walrus Memory holds
up under real conversation.

The persona was renamed to Cheta without moving any memory. The Python package,
the repository and the environment variables keep the original `ranti` name on
purpose: `MEMWAL_NAMESPACE_PREFIX=ranti` and every memory namespace are
unchanged, so the rename orphaned nothing. Self-introductions use the one name
configured in `BOT_NAME`; a retired name is never surfaced, even when an old
stored record still contains it, because stored blobs are never rewritten.

The problem it solves is memory rot. Walrus Memory is permanent, encrypted and
portable, but it is append-only, and the Python SDK's high-level recall is plain
cosine top-K. Nothing stops a fact written last month from sitting next to its
replacement today, so the longer a naive bot runs, the noisier its context gets.

## Why this is not decorative memory

Concrete `memwal` 0.1.11 behaviours this project is built around:

- **Append-only writes create duplicates and stale values.** `remember` and
  `analyze` only ever add. There is no update or delete in the Python SDK, so a
  changed preference (a new main programming language, a new city) becomes a
  second blob competing with the old one. Whichever blob the embedding search
  happens to rank higher wins the prompt.
- **High-level recall is cosine top-K.** `MemWal.recall` returns the nearest
  `limit` results with no recency term, no importance term and, by default, no
  relevance floor (`max_distance` defaults to `None`). Old and low-value memories
  are returned as readily as new, important ones, and `RecallResult` carries no
  timestamp.
- **`ScoringWeights` is only reachable through the manual path.** Composite
  recency/importance scoring exists, but `ScoringWeights` is an option of
  `recall_manual(RecallManualOptions(...))` alone. `recall_manual` returns
  `blob_id` and `distance` pairs with no decrypted text, so an application that
  wants ranking must also implement its own download and decrypt step.

Ranti adds the missing layer on top, without changing what Walrus Memory is:

- **Consolidation on ingest.** Each turn is mined for durable facts, each fact
  is compared against its nearest existing memories, and an LLM adjudicates the
  ambiguous band into `new`, `duplicate`, `updates` or `contradicts`. Duplicates
  are skipped before the write, updates mark the old blob superseded, and
  contradictions are recorded for review.
- **Salience-aware recall.** Recall fetches a wide candidate set, drops
  superseded and duplicate entries, re-ranks by semantic distance, recency,
  importance and origin surface, then fills a token budget with the memories
  that earn their place. See `select_context` and `RankingWeights` in
  [src/models/entities/memory_rank_model.py](src/models/entities/memory_rank_model.py).
- **Counterfactual replay.** Any past turn can be answered again with memory
  switched off, producing a reproducible before/after pair instead of a staged
  screenshot.
- **A portable index.** Consolidation state is written back to Walrus Memory as
  compact index records, and a Memory Passport can export and re-import them, so
  the intelligence is not trapped in the local SQLite file.

## Architecture

Layered, one file per entity, under [src/](src):

```text
src/
  models/entities/   innermost models, no project imports    user_model.py
  schemas/           transport DTOs (pydantic), no persistence
  crud/              one file per entity, SQLite only
  services/          use cases: conversation, memory admin, user
  routers/           parse the request, call one service, shape the response
  core/              config, database, errors, resilience, protocols, gateways
```

The dependency rule points inward: `routers -> services -> crud ->
models.entities`. `schemas` is transport-only, and `core.gateways` never imports
the layers that call it. The rule is enforced by the import-linter contracts in
[pyproject.toml](pyproject.toml) and checked by
[tests/test_architecture.py](tests/test_architecture.py).

Every outbound call goes through one `Boundary` from
[src/core/resilience.py](src/core/resilience.py): timeout, circuit breaker,
bulkhead and bounded jittered retry, with one policy per dependency. Boundaries
are constructed only in [src/core/container.py](src/core/container.py), the
composition root, and wrap the three gateways in
[src/core/gateways/](src/core/gateways): Walrus Memory, the LLM failover chain,
and Telegram.

Memory writes are deliberately never blind-retried. The memory boundary is built
with `retry_writes=False`, so a `remember` call is attempted exactly once while
reads are retried with jittered backoff. The relayer is append-only, so a write
that timed out after the relayer accepted it would be duplicated by a retry.
Writes carry a deterministic `idempotency_key` instead. If a write genuinely
fails, the turn is reported as degraded rather than silently retried.

## Quick start

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install fastapi "uvicorn[standard]" httpx openai pydantic memwal
python -m pip install pypdf python-docx python-pptx openpyxl PyYAML
python -m pip install pytest pytest-asyncio import-linter respx
cp .env.example .env
```

The dependency list mirrors `[project].dependencies` and
`[project.optional-dependencies].dev` in [pyproject.toml](pyproject.toml).
An editable install also works and is the shorter path:

```bash
python -m pip install -e ".[dev]"
```

Required environment variables:

- `MEMWAL_PRIVATE_KEY` and `MEMWAL_ACCOUNT_ID` - the Walrus Memory account and
  its delegate key. Create the account at https://memory.walrus.xyz
  (Enoki-sponsored, no gas). The site issues the delegate key used as
  `MEMWAL_PRIVATE_KEY`. `MEMWAL_SERVER_URL` already defaults to the hosted
  relayer `https://relayer.memory.walrus.xyz`.
- Optional but recommended, at least one LLM provider: `GROQ_API_KEY` (primary,
  `GROQ_MODEL=qwen3-32b`), `GEMINI_API_KEY` (secondary), or a local Ollama at
  `OLLAMA_BASE_URL`. All providers speak the OpenAI wire format; the first
  configured provider wins and the rest form the failover chain. See
  [src/core/config.py](src/core/config.py). When none is configured the app falls
  back to a deterministic offline model
  ([src/core/gateways/offline_llm_gateway.py](src/core/gateways/offline_llm_gateway.py)),
  which is loud about being a fallback and is good enough to demonstrate the
  consolidation behaviour but is not a real model.
- `GROQ_API_KEYS` (optional) - several Groq keys, comma separated with no spaces
  required, for example `key_one,key_two,key_three`. When set it wins over the
  single `GROQ_API_KEY`; when it is not set, `GROQ_API_KEY` behaves exactly as
  before. Each key is trimmed, empty entries and duplicates are dropped, and
  order is preserved so the first key is preferred. On a `429` the provider tries
  the next key at once instead of sleeping on the SDK's backoff, rests the
  rate-limited key until the provider's `Retry-After` (60 seconds when it gives
  none) and then returns it to rotation. A key that returns `401` or `403` is
  retired for the process and logged by index and key hash, never by value. The
  whole rotation shares one provider budget, so a long list of keys cannot each
  spend the full timeout.
- `BOT_NAME` (default `Cheta`) - the assistant's own user-facing name. It never
  touches the memory namespaces, which stay on `MEMWAL_NAMESPACE_PREFIX`, so
  renaming cannot orphan a stored memory. Every self-introduction, including the
  first-turn onboarding, renders this one name, so a retired name cannot leak
  back into a greeting.
- `RANTI_RETIRED_NAMES` (default empty) - assistant names retired by an earlier
  rename, comma separated. Stored blobs are append-only and never rewritten, so
  an old record about the assistant can still name one of them. Any record that
  is about the assistant under one of these names is treated as not a fact about
  the person: it is filtered from the listing, the greeting, the prompt and the
  repair pass, exactly like a record whose subject is "the assistant". A record
  is only matched when it ties the name to the assistant role ("Ranti is a
  memory-first assistant", "the assistant is called Ranti"), so a legitimate
  fact about a namesake person is left alone. Every entry is trimmed, empty
  entries and repeats are dropped, and an empty value disables the filter.
- `RANTI_MEMORY_RECEIPTS` (default `0`) - internal plumbing such as
  `1 accepted, persisting` appended to a reply. Off by default so it never
  reaches a real conversation; turn it on only to show the memory engine at work
  in a demo.
- `RANTI_RESUME_AFTER_HOURS` (default `6`) - how long a known user must have
  been away before the first reply of a returning session opens by naming one or
  two facts that are actually stored. The line is service-authored, comes only
  from stored records, is appended to the model's own reply, and is suppressed
  entirely when Walrus Memory is degraded. Below the threshold, a fast
  back-and-forth is treated as one conversation and is not re-greeted.

Newness is decided by one predicate, `is_new_person`, and every first-contact
decision goes through it. A person is new only when no local turn, no stored
memory record (live or recovered from an index snapshot), no unreachable
snapshot and no shared handle exist. It is evaluated after index recovery, so a
redeploy that wipes the local SQLite file never turns an established person into
a first contact and never replays the onboarding block. When the relayer cannot
be read, the absence of a snapshot cannot be assumed, so the person is treated
as established rather than new.

Run the API:

```bash
python -m uvicorn main:app --app-dir src --reload
```

Or use the helper, which creates the virtualenv, installs the dependencies and
loads `.env`:

```bash
bash scripts/run_local.sh
```

Then open http://127.0.0.1:8000/app for the widget and
http://127.0.0.1:8000/docs for the API. **With no credentials at all the app
still completes full memory cycles**: it runs against the offline `MemWalMock`
client (`/health` reports `"memory": {"mode": "mock"}`) and the deterministic
offline model (`/health` reports `"llm": {"providers": ["offline"]}`). That path
is covered by
[tests/core/test_offline_llm_gateway.py](tests/core/test_offline_llm_gateway.py),
so the clone-and-run claim is executable rather than aspirational. Point
`MEMWAL_*` and a provider key at the real services to leave the fallback behind.

### Verify your Walrus Memory credentials first

Credential problems on Walrus Memory surface as 401s that are hard to attribute,
so check them with a real round trip before debugging anything else:

```bash
python scripts/verify_memwal.py
```

It checks the relayer health, writes one small memory into a dedicated
`ranti.verify` namespace, waits for it to persist, and reads it back. Every step
prints `[OK]` or a `[FAIL]` that names the cause, including the two common ones:
a delegate key that is not registered on the account, and a mainnet/staging
mismatch. It exits non-zero on failure and never prints a traceback.

The live integration test is the same round trip as a test. It is skipped unless
you opt in explicitly, so the default run stays offline:

```bash
RANTI_LIVE_TESTS=1 MEMWAL_PRIVATE_KEY=... MEMWAL_ACCOUNT_ID=... \
  python -m pytest tests/integration -q
```

## The four surfaces

Each surface records which client produced a memory (`origin_surface`) and, by
default, derives its memory namespace from its own identity:
`{MEMWAL_NAMESPACE_PREFIX}.user.{surface}-{surface_user_id}` plus an `.idx`
companion, built in [src/core/config.py](src/core/config.py). A person can join
those spaces with `/link <handle>`, or prove a new client belongs to them with
`/pair` (a one-time code from a client already in use), and every client on the
same handle then reads one memory space. Until a person links, two surfaces are
deliberately two spaces, which is what keeps existing memories where they are.
`VALID_SURFACES` in [src/models/entities/user_model.py](src/models/entities/user_model.py)
is `("telegram", "cli", "web", "extension")`. [docs/surfaces.md](docs/surfaces.md)
has the per-surface detail, including the extension load steps.

**Telegram.** Set `TELEGRAM_BOT_TOKEN`, `TELEGRAM_WEBHOOK_SECRET` and
`PUBLIC_BASE_URL` (a public HTTPS URL), then register the webhook:

```bash
python scripts/register_telegram_webhook.py
```

The script reads `.env`, builds
`{PUBLIC_BASE_URL}/webhooks/telegram/{TELEGRAM_WEBHOOK_SECRET}` and calls the
Telegram `setWebhook` API. The FastAPI route is
[src/routers/telegram_router.py](src/routers/telegram_router.py). The surface
identity is the Telegram chat id. The bot also has:

- **Commands answered without a model call.** `/start` and `/help` print the
  command list, `/memories` lists what is stored for that chat, and `/forget N`
  retires note N. `/link <handle>` puts clients on one shared space, and `/pair`
  mints a one-time code to add another client without typing a handle. `POST
  /chat/turn` dispatches the same commands on the HTTP path, so the CLI, the web
  widget and the extension get them too.
- **Inline buttons.** `/start` and `/help` carry a keyboard with "What I know
  about you", "Export my memory" and "Forget one". The export button sends the
  passport as a Telegram document; the forget button opens a numbered keyboard.
  A tap is answered with `answerCallbackQuery` and never stored as a turn.
- **Local document parsing.** Attachments up to 20 MB are downloaded and parsed
  locally, and the extracted text (capped at 12000 characters) is used for that
  turn. The advertised list is generated from the parser's own table, so it
  cannot drift: PDF, DOCX, PPTX, XLSX/XLSM, XML, JSON, YAML, HTML, CSV/TSV and
  any plain text or source file. Legacy `.doc`, `.ppt` and `.xls` are refused by
  name with the format to save as, and images, video, audio and archives are
  refused. Voice notes are transcribed instead when a transcription key is set.
  A document with a caption is one turn: the caption is the question and the
  document is the material. The system prompt states that the document was read,
  names it, and forbids claiming it is absent, so the model cannot answer from a
  document and deny it in the same reply.

These Telegram paths are described from the source in this checkout; they were
not exercised end to end as part of the web-widget verification. See
[docs/surfaces.md](docs/surfaces.md) for the honest status of each one.

**CLI.** The terminal client in [src/cli.py](src/cli.py) talks to the running
API over HTTP only, so it stays honest about what the public API can do. Run it
with the wrapper:

```bash
bash scripts/ranti
```

or directly, with `src` on `PYTHONPATH`:

```bash
PYTHONPATH=src python -m cli
```

It reads `RANTI_API_URL` (default `http://127.0.0.1:8000`) and persists its
surface identity in `~/.ranti_cli.json` (`RANTI_CLI_CONFIG` overrides the path).
Every turn is `POST /chat/turn` with `surface="cli"`; the raw API is also usable
directly:

```bash
curl -s http://127.0.0.1:8000/chat/turn \
  -H 'Content-Type: application/json' \
  -d '{"surface":"cli","surface_user_id":"you","display_name":"You","text":"Remember that I am allergic to peanuts."}'
```

See [scripts/seed_demo_user.py](scripts/seed_demo_user.py) for a scripted
multi-turn run on this surface.

**Web widget.** Served at `/app` by the static mount in
[src/main.py](src/main.py). [src/web/index.html](src/web/index.html) is the chat
widget, with a memory on/off toggle, a live recalled-memory count, and visible
`/memories` and `/help` command buttons; [src/web/dashboard.html](src/web/dashboard.html)
at `/app/dashboard.html` is the per-user evidence dashboard.

**Chrome extension.** [extension/](extension) is a Manifest V3 side panel that
targets `POST /chat/turn` with `surface="extension"`. Load it unpacked from
`chrome://extensions` (Edge: `edge://extensions`) with Developer mode on and
the `extension/` folder selected. The default API base URL is
`https://ranti-gkn7.onrender.com`. `VALID_SURFACES` accepts `"extension"`, so a
turn from the panel is served like any other surface.

## Tests and the architecture check

They are the same command:

```bash
python -m pytest
```

The suite includes the architecture check, so a broken dependency direction
fails the default test run. The check enforces:

- the layer contract `routers -> services -> crud -> models.entities`;
- `models.entities` is innermost and imports no other project layer;
- `schemas` is transport-only and never imports crud, services, routers or
  gateways;
- `crud` never reaches outward into schemas, services or routers;
- routers call services and never import crud or gateways directly;
- no service imports another service;
- gateways never import the layers that call them;
- no `utils.py`, `helpers.py` or `common.py` dumping grounds;
- no non-ASCII characters in `src/`.

The contracts live in [pyproject.toml](pyproject.toml), and the test is
[tests/test_architecture.py](tests/test_architecture.py).

## Health and the per-turn budget

`GET /health` reports liveness plus the numbers that make slowness visible
instead of a mystery:

- `memory.requests` - every boundary call made to Walrus Memory since process
  start. A turn that spends several is the turn that trips the relayer's
  per-minute limit, so this is the count to watch.
- `memory.degraded` - true once any call was served by a fallback.
- `performance` - the measured per-turn latency of the last observed turns:
  `last_ms`, `p50_ms`, `p95_ms`, `max_ms` and `turns_measured`.

Index recovery is a relayer round trip and runs at most once per user per
process, not on every turn. `/pair` skips recovery entirely because it never
reads the index, and copying notes into a shared space is a background task, so
pairing answers immediately instead of sitting on the relayer's write latency.

## Evidence

`GET /evidence/users` returns one row per known user, combining the local index
with `list_namespaces` from Walrus Memory. It is produced by
`evidence_leaderboard` in
[src/services/memory_admin_service.py](src/services/memory_admin_service.py) and
shaped by `MemoryStatsSchema` in
[src/schemas/memory_schema.py](src/schemas/memory_schema.py).

How to read a row:

- `active`, `superseded`, `contradicted` - consolidation state in the local
  index. Superseded counts memories replaced by a newer value; contradicted
  counts memories caught in an unresolved conflict.
- `open_contradictions` - conflicts still awaiting a decision.
- `turns` - conversation turns served for this user.
- `relayer_memory_count`, `relayer_storage_bytes` - what the relayer reports for
  the user's namespace, so local counters can be checked against the source of
  truth.
- `relayer_degraded` - true when the relayer could not be listed and the counts
  are local only. Never read it as "no memories".

Run [scripts/seed_demo_user.py](scripts/seed_demo_user.py) against a live API to
populate the leaderboard and print the counters in one command.

## Known limitations

- **Namespaces are flat.** A namespace is
  `{MEMWAL_NAMESPACE_PREFIX}.user.{surface}-{surface_user_id}` plus an `.idx`
  companion, or `{MEMWAL_NAMESPACE_PREFIX}.user.shared-{handle}` once a person
  links clients. There is no hierarchy and no wildcard query, so per-user
  isolation is exact but grouping across users is not expressible.
- **Recall has no default relevance floor.** `MemWal.recall` defaults to
  `max_distance=None`. Ranti passes a threshold only for the consolidation
  neighbour search, not for ordinary recall, so a weak match can still be
  returned; salience re-ranking only reorders the candidate set.
- **The Python SDK has no delete or forget method.** Superseding marks a record
  inactive in the local index, but the blob stays on Walrus Memory. Erasure is
  not available from the Python client.
- **The local index is rebuildable.** SQLite holds only the working index and
  counters. Facts and the portable consolidation index live in Walrus Memory,
  and `GET /memories/{user_id}/passport` can export and re-import them, so a
  wiped host loses no memory.

## Deployment

A Dockerfile and deployment notes for a free always-on host are in
[deployment/README.md](deployment/README.md).
