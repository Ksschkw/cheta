# Ranti browser extension surface

This folder is a Manifest V3 Chromium extension (Chrome and Edge) that opens
Ranti in the browser side panel. It is the fourth surface of the project, next
to the Telegram bot, the CLI, and the web widget.

The panel is a chat window against the deployed Ranti API. Its job is to make
memory visible: under every reply it shows the exact memories that were
recalled for that turn, with the memory text, the salience, the origin surface,
and the Walrus blob id. It also has a memory panel that lists what is stored
for the current identity, and a per-turn control that replays the same question
with memory switched off so the difference is visible side by side.

There are no emojis, no web fonts, no external scripts, and no build step. The
extension is plain HTML, CSS, and JavaScript and loads unpacked as-is.

## Load it unpacked

1. Open `chrome://extensions` (Edge: `edge://extensions`).
2. Turn on **Developer mode** (top right).
3. Click **Load unpacked**.
4. Select this `extension/` folder, the one that contains `manifest.json`.
5. Click the Ranti toolbar button. The side panel opens on the right.

The toolbar button is wired in `background.js` with
`chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true })`, so the
panel opens from the button in both Chrome and Edge. Requires Chrome or Edge
114 or newer.

## Point it at a different API base URL

The default API base URL is `https://ranti-gkn7.onrender.com`.

1. Open the side panel.
2. Expand **Settings** at the top.
3. Edit **API base URL**, for example `http://127.0.0.1:8000` for a local
   server.
4. Click **Save settings**. The value is stored in `chrome.storage.local` and
   reused after every reload. **Reset to default** restores the deployed URL.

The panel calls `GET /health` after saving, so the status line reports whether
the new base URL is reachable and whether Walrus Memory is degraded.

The same Settings block holds the **Display name** and shows the generated
**Surface user id**. The display name is editable; the surface user id is
generated once with `crypto.randomUUID()` and kept in `chrome.storage.local`
so memory stays attached to the same person across reloads. Both are sent with
every turn.

## Requests it makes

All requests go to the configured base URL.

- `POST /chat/turn` with
  `{surface: "extension", surface_user_id, display_name, text, memory_enabled}`.
  `surface` is always `extension`. The response supplies the reply, the
  recalled memories, the stored facts, and the `memory_degraded` flag.
- `POST /chat/counterfactual/{turn_id}` for the per-turn
  "Show it without memory" control. The response supplies `with_memory`,
  `without_memory`, `recalled_count`, `reply_changed`, and `summary`.
- `GET /memories/{user_id}` for the memory panel, with
  `include_inactive=false` by default and `include_inactive=true` when the
  "Include inactive" box is checked.
- `GET /health` for the status line.

These are the same request and response shapes the web widget and the CLI use,
defined in `src/schemas/turn_schema.py` and `src/schemas/memory_schema.py`.

## Commands

Typing `/start`, `/memories`, or `/help` answers as a command. The panel
answers these three commands itself with no model call, reading the stored
memories over `GET /memories/{user_id}` when an identity exists, because the
`POST /chat/turn` path does not dispatch commands (only the Telegram push path
does). Nothing is stored for a command reply.

## Honest degradation

When a turn returns `memory_degraded`, the reply carries a `[WARN]` notice
saying Walrus Memory was unreachable for that turn, and the panel never implies
the person has no memories. Status words are `[OK]`, `[WARN]`, and `[FAIL]`.
When the memory toggle is off, the turn is sent with `memory_enabled: false`
and the reply is marked `[NO MEMORY]` with a dashed border.

## What it shares with the other surfaces

The extension shares one memory space per person with the Telegram bot, the
CLI, and the web widget:

- The same `POST /chat/turn` contract and the same `surface` field vocabulary
  (`telegram`, `cli`, `web`, `extension`).
- The same Walrus Memory records. A fact stored on one surface comes back as a
  recalled memory on another, and the recalled chip shows `origin_surface`.
- The same `/memories/{user_id}` listing and `/chat/counterfactual/{turn_id}`
  replay used elsewhere.

Identity is per surface. The extension keeps its own generated
`surface_user_id` and display name locally, so it is a distinct first-class
surface that shares memory with the others rather than a copy of the widget.
