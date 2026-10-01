# Ranti surfaces

Ranti has four client surfaces: a Telegram bot, a CLI, a web widget served at
`/app`, and a Chromium extension in [extension/](../extension). All four are
clients of the same FastAPI service. None of them talks to Walrus Memory
directly; the service owns the `memwal` client and is the only writer.

## How memory is scoped

Read this before comparing surfaces.

- The API derives a memory namespace as
  `{MEMWAL_NAMESPACE_PREFIX}.user.{surface}-{surface_user_id}`, with a `.idx`
  companion. The surface name is part of the namespace, so two surfaces are two
  namespaces even when they use the same `surface_user_id`.
- The product goal is one memory space per person. Today that is reached in two
  ways: use one surface, where the persisted `surface_user_id` keeps the same
  namespace across restarts; or move a space between surfaces with the Memory
  Passport (`GET /memories/{user_id}/passport` and
  `POST /memories/passport/import`). Importing twice is safe because duplicate
  text is skipped. Sharing across different surface names is not automatic.
- Every stored memory records the surface that produced it. The web widget and
  the extension show that value as `origin_surface` on recalled memory chips.
- Memory recall and listing are namespace-scoped. A fact stored on one surface
  does not appear on another surface until it is imported there.

## Commands

`/start`, `/help`, `/memories` and `/forget` are answered directly by the API
with no model call and no stored turn. `POST /chat/turn` dispatches them on the
HTTP path as well as on Telegram, and returns `provider: "command"` with an
empty `turn_id`. `/memories` lists what is stored for the caller; `/help`
prints the command list; `/forget N` retires note N (it marks the record
superseded, because the Python SDK has no delete).

## Telegram bot

Purpose: the messaging surface, and the only push surface.

Run it by setting the Telegram environment variables and registering the
webhook against the running API:

```bash
python scripts/register_telegram_webhook.py
```

Required variables are `TELEGRAM_BOT_TOKEN`, `TELEGRAM_WEBHOOK_SECRET` and
`PUBLIC_BASE_URL` (a public HTTPS URL). The webhook route is
`POST /webhooks/telegram/{secret}` in
[src/routers/telegram_router.py](../src/routers/telegram_router.py). The
surface identity is the Telegram chat id.

Features, as implemented in
[src/services/conversation_service.py](../src/services/conversation_service.py):

- Commands: `/start`, `/help`, `/memories` and `/forget N` answer with no model
  call.
- Inline buttons on `/start` and `/help`: "What I know about you" (lists
  memories), "Export my memory" (sends the passport as a Telegram document) and
  "Forget one" (a numbered keyboard that retires one note). A button tap is
  answered with `answerCallbackQuery` and never becomes a conversation turn.
- Attachments: documents are downloaded and parsed locally. Readable formats
  are PDF, plain text, markdown, CSV and DOCX, up to 20 MB; extracted text is
  capped at 12000 characters. Images, audio, video, archives, spreadsheets and
  presentations are refused by name rather than silently ignored.

These Telegram features are read from the source in this checkout. They were
not exercised against the deployed bot during the web-widget verification, so
treat their live behaviour as unverified here.

## CLI

Purpose: a terminal client that stays honest about the public API, because it
talks HTTP only.

```bash
bash scripts/ranti
```

or directly, with `src` on `PYTHONPATH`:

```bash
PYTHONPATH=src python -m cli
```

It reads `RANTI_API_URL` (default `http://127.0.0.1:8000`) and persists its
surface identity in `~/.ranti_cli.json` (`RANTI_CLI_CONFIG` overrides the
path). Every turn is `POST /chat/turn` with `surface="cli"`. See
[scripts/seed_demo_user.py](../scripts/seed_demo_user.py) for a scripted
multi-turn run.

## Web widget

Purpose: a zero-install browser surface and the evidence dashboard.

Run the API (`python -m uvicorn main:app --app-dir src --reload` or
`bash scripts/run_local.sh`) and open `http://127.0.0.1:8000/app`. The files
are [src/web/index.html](../src/web/index.html),
[src/web/styles.css](../src/web/styles.css) and
[src/web/app.js](../src/web/app.js); [src/main.py](../src/main.py) mounts the
folder at `/app`. The per-user evidence dashboard is at `/app/dashboard.html`.

The chat widget keeps its `surface_user_id` in browser local storage, so the
same identity and namespace are reused across reloads. Each reply shows the
memories that were recalled for that turn, a memory on/off toggle, and a per
turn "Show it without memory" replay. `/memories` and `/help` are available as
buttons and as typed commands. There is no build step, no framework and no
external request; every API call is a same-origin root-relative path.

## Chrome extension

Purpose: a side panel that makes memory visible next to any page.

Load it unpacked:

1. Open `chrome://extensions` (Edge: `edge://extensions`).
2. Turn on Developer mode (top right).
3. Click Load unpacked.
4. Select the `extension/` folder, the one that contains `manifest.json`.
5. Click the Ranti toolbar button. The side panel opens on the right.

Requires Chrome or Edge 114 or newer. The default API base URL is
`https://ranti-gkn7.onrender.com`; the Settings block in the panel can change
it, and the value is kept in `chrome.storage.local`. The panel sends
`surface="extension"` on every turn.

Current limitation: the deployed API rejects `surface="extension"` with HTTP
500. `VALID_SURFACES` in
[src/models/entities/user_model.py](../src/models/entities/user_model.py) is
`("telegram", "cli", "web")`, and a request whose surface is not in that tuple
raises before a turn is served. The extension cannot complete a turn until the
backend accepts `"extension"`. This was observed against
`https://ranti-gkn7.onrender.com`, not assumed.

## What all surfaces share

- One API and one set of request and response shapes, defined in
  [src/schemas/turn_schema.py](../src/schemas/turn_schema.py) and
  [src/schemas/memory_schema.py](../src/schemas/memory_schema.py).
- The same `POST /chat/turn` contract with a `surface` field
  (`"telegram"`, `"cli"`, `"web"`, and `"extension"` once accepted).
- The same Walrus Memory service, so a namespace written by one surface is
  readable by any other surface that is pointed at the same namespace, for
  example through a passport import.
- The same command vocabulary and the same degradation rules: a turn that could
  not reach Walrus Memory is reported as degraded, never as "no memories".
