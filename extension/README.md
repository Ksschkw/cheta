# Cheta browser extension surface

This folder is a Manifest V3 extension that opens Cheta in the browser's side
panel (Chromium) or sidebar (Firefox). The shared code is identical in both
browsers; only the manifest differs. It is the fourth surface of the project,
next to the Telegram bot, the CLI, and the web widget.

The panel is a chat window against the deployed API. Its job is to make memory
visible without exposing plumbing: under every reply it shows the plain text of
the memories that were recalled for that turn. It also has a memory panel that
lists what is stored for the current identity, and a per-turn control that
replays the same question with memory switched off so the difference is visible
side by side.

It is also an agent on the open tab. **Use this page** reads the visible text of
the tab that is active when the extension is invoked, shows the tab title and
hostname, and then offers three named actions instead of a generic chat box:
**Summarise this page**, **Save the useful facts to my memory**, and **Explain
this to me like I am new to it**. Each action sends the page text to
`POST /chat/turn` on the `extension` surface with the same persisted
`surface_user_id` the chat uses.

There are no emojis, no web fonts, no external scripts, and no build step for
the code itself. The extension is plain HTML, CSS, and JavaScript. The only
script is the packaging step below, which copies files and picks the right
manifest.

## Two build targets

Chromium and Firefox cannot share one manifest. Chromium needs `side_panel`
plus `background.service_worker`; Firefox has neither, needs `sidebar_action`
plus `background.scripts`, and only ever reads a file named `manifest.json`.
The shared code lives in `extension/` and both manifests point at the same
files.

Run the packaging script, which uses only the Python standard library:

```sh
python3 scripts/build_extension.py
```

It writes two complete, loadable folders:

| Target | Folder | Manifest written as `manifest.json` |
| --- | --- | --- |
| Chromium | `extension-dist/chrome/` | `extension/manifest.json` |
| Firefox | `extension-dist/firefox/` | `extension/manifest.firefox.json` |

### What differs between the browsers

| Concern | Chromium | Firefox | Handled in |
| --- | --- | --- | --- |
| Panel surface | `side_panel` key, `chrome.sidePanel` | `sidebar_action` key, `browser.sidebarAction` | the two manifests |
| Open from the toolbar | `sidePanel.setPanelBehavior({openPanelOnActionClick: true})` | No equivalent. The sidebar is chosen from Firefox's own Sidebar UI, or toggled by `sidebarAction.toggle()` from the `open-cheta-sidebar` keyboard command | `background.js` |
| Background context | `background.service_worker` | `background.scripts` event page; Firefox MV3 has no service worker | the two manifests |
| Namespace | `chrome.*`, callbacks with `runtime.lastError`, promises on newer builds | `browser.*` promises, `chrome.*` callbacks | `browser-api.js` |
| Storage, tabs, scripting, sidebar calls | callback or promise | always promise | `browser-api.js` |

`browser-api.js` exposes one global, `ChetaBrowserApi`, and is loaded before
`sidepanel.js`. Every extension API call in this folder goes through it, so the
namespace and calling-convention differences exist in exactly one place.
`background.js` chooses a branch by capability rather than by user-agent
string: if `setPanelBehavior` exists it uses the Chromium path, otherwise if
`sidebarAction` exists it uses the Firefox path. In Firefox it deliberately
does not bind an action click, because Firefox cannot open a sidebar that way;
the keyboard command is the explicit control.

## Working on the active tab

### Reading on demand, not a content script on every site

The extension requests `activeTab` and `scripting`, not host permissions for the
purpose of reading pages. When the person clicks **Use this page**, the panel:

1. Looks up the tab that is active in the current window through
   `ChetaBrowserApi.tabsQuery`.
2. Injects `readPageInTab` into that one tab through
   `ChetaBrowserApi.executeScript({ target: { tabId }, func })`, which is
   `scripting.executeScript` in both browsers.
3. Takes the returned object's string fields and discards everything else.

There is deliberately no `content_scripts` entry and no permanent script on all
sites. The read happens only on a click, only in the tab the person was looking
at, and only while the `activeTab` grant is in force. A permanent content script
would run in every page in every tab at page load, which is far more access than
this feature needs. `activeTab` is granted by the user gesture that invokes the
extension (in Firefox, clicking **Use this page** on the panel, or the
`open-cheta-sidebar` keyboard command), so no additional host permission is
needed to read.

The broad `host_permissions` in the manifest are not for reading pages. They
exist for `fetch` to the user-configurable API base URL from the panel, which is
a cross-origin request and would otherwise be blocked by CORS.

### Page text is untrusted data

The page is treated as data, never as instructions:

- The read uses `innerText` and `textContent` only. The panel never reads or
  parses `innerHTML`, never builds a `script` element, and never evaluates page
  content.
- Everything returned from the tab is shown and sent as plain text. DOM nodes
  are built with `textContent`, so markup, URLs, or script text found on a page
  cannot become part of the panel's own DOM or behaviour.
- The message sent to `POST /chat/turn` wraps the page text in explicit markers:
  a `[PAGE CONTENT - untrusted data]` header that says the block is data and not
  instructions, `--- BEGIN PAGE CONTENT ---` / `--- END PAGE CONTENT ---` around
  the text, and the action instruction placed after the closing marker so it
  cannot be mistaken for page content.

### Bounds and truncation

The transport caps the whole turn text at 8000 characters
(`src/schemas/turn_schema.py`, `TurnRequestSchema.text`), so page text is bounded
at 6000 characters to leave room for the untrusted-data framing and the action.
The injected read also hard-caps what it collects at 20000 characters. When the
page is longer, the panel says on screen that only the first 6000 characters are
sent, and the message itself carries a line saying it was truncated. Nothing is
trimmed silently.

The transcript keeps only the short action label, such as
`Summarise this page: <title> (<host>)`, not the page body, so page text is not
written into `storage.local` by the transcript.

### When a tab cannot be read

The panel names the specific reason rather than failing silently:

- `chrome://`, `edge://`, `about:`, and `devtools:` pages: browsers do not expose
  these to extensions.
- Other extension pages: extensions cannot read each other.
- Local `file:` pages: the person must enable "Allow access to file URLs" for
  Cheta on the extensions page.
- `view-source:` tabs.
- PDF viewers: browsers do not expose the words of a PDF as page text.
- A tab whose URL the browser withholds (special schemes such as `chrome://`):
  the panel attempts the injection and shows the browser's own refusal, plus a
  note naming browser pages as the likely reason. An invocation on that tab is
  also required for the read.
- An injection that the browser refuses: the browser's own message is shown.
- A page with no visible text: the panel says the page may be empty, still
  loading, or drawn on a canvas.

## Design

Minimal monochrome with a single accent color. The palette is closed:

- Background `#0a0a0a`, surfaces `#141414`, hairline borders `#262626`.
- Text `#ededed` primary, `#8a8a8a` secondary.
- One accent, `#35d0ba`, used only for the send button, focus rings, and link
  hover.

System font stack, generous spacing, no gradients, no glow, no heavy shadows,
no decorative characters. The composer is pinned at the bottom and is laid out
for a 320px wide side panel first.

The normal view shows conversation text and recalled memory text. Nothing else:
no verdicts, no pending or persisting wording, no blob ids, no salience numbers,
and no "facts accepted this turn" panel. The API base URL is never printed
outside its editable field in Settings.

## Load it

Build both targets first:

```sh
python3 scripts/build_extension.py
```

### Chrome or Edge

1. Open `chrome://extensions` (Edge: `edge://extensions`).
2. Turn on **Developer mode** (top right).
3. Click **Load unpacked**.
4. Select `extension-dist/chrome/`, the folder that contains `manifest.json`.
5. Click the toolbar button. The side panel opens on the right.

The toolbar button is wired in `background.js` through the compatibility layer
with `setPanelBehavior({ openPanelOnActionClick: true })`, so the panel opens
from the button. Requires Chrome or Edge 114 or newer.

### Firefox

1. Open `about:debugging`.
2. Click **This Firefox**.
3. Click **Load Temporary Add-on**.
4. Select `extension-dist/firefox/manifest.json`.

Firefox shows Cheta in its own Sidebar menu (`View` > `Sidebar` > `Cheta`); the
sidebar also opens when the add-on loads. `Ctrl+Shift+Y` (`Command+Shift+Y` on
macOS) opens or closes it through the `open-cheta-sidebar` command declared in
`manifest.firefox.json`. Firefox has no way for an extension to make an ordinary
toolbar click open a sidebar the way Chromium's `setPanelBehavior` does, so that
path is not emulated; opening the sidebar from the keyboard command also grants
`activeTab` for the tab in front.

A temporary add-on is removed when Firefox restarts: repeat the four steps above
after every restart. Temporary loading does not require the
`browser_specific_settings.gecko.id` in the Firefox manifest, where Firefox can
assign a temporary id, but a stable id keeps stored settings attached to the
same extension identity and is required for a signed, permanently installed
add-on.

## Point it at a different API base URL

The default API base URL is `https://ranti-gkn7.onrender.com`. It is the only
place a URL appears in the interface, and the editable base URL field is the
only place it can be changed.

1. Open the side panel.
2. Expand **Settings** at the top.
3. Edit **API base URL**, for example `http://127.0.0.1:8000` for a local
   server.
4. Click **Save**. The value is stored in `storage.local` and reused
   after every reload. **Reset URL** restores the deployed default.

The panel calls `GET /health` after saving, and the status line reports the
result in plain words, for example "Connected." or "Cannot reach the server.
Check Settings." It never prints the address.

The same Settings block holds the **Display name** and shows the generated
**Browser identity**. The display name is editable; the identity is generated
once with `crypto.randomUUID()` and kept in `storage.local` so memory
stays attached to the same person across reloads and across this redesign. The
storage keys are frozen internal identifiers for exactly that reason.

## Requests it makes

All requests go to the configured base URL.

- `POST /chat/turn` with
  `{surface: "extension", surface_user_id, display_name, text, memory_enabled}`.
  `surface` is always `extension`. The response supplies the reply and the
  recalled memories. The panel renders the recalled memory text only. A page
  action uses the same request: `text` is the untrusted-data block described
  above, and the transcript shows a short label such as
  `Summarise this page: <title> (<host>)` instead of the page body.
- `POST /chat/counterfactual/{turn_id}` for the per-turn
  "Show it without memory" control. The response supplies `with_memory`,
  `without_memory`, and `summary`.
- `GET /memories/{user_id}` for the memory panel, with
  `include_inactive=false` by default and `include_inactive=true` when the
  "Include superseded and contradicted" box is checked.
- `GET /health` for the status line.

These are the same request and response shapes the web widget and the CLI use,
defined in `src/schemas/turn_schema.py` and `src/schemas/memory_schema.py`.

## Permissions

The manifests declare only what the panel uses. Chromium's `manifest.json` and
Firefox's `manifest.firefox.json` carry the same three permissions; only the
panel and background keys differ, as the table above shows.

- `sidePanel` (Chromium only) - opens `sidepanel.html` as the browser side
  panel. Firefox uses the `sidebar_action` key instead, which is a manifest key
  rather than a permission.
- `storage` - `storage.local` keeps the generated `surface_user_id`, the
  display name, the memory toggle, the base URL, and the transcript.
- `activeTab` - the temporary grant from a user gesture (the toolbar button in
  Chromium, clicking **Use this page** or the keyboard command in Firefox) that
  lets the panel read the URL, title, and text of the tab the person invoked the
  extension on. Used by `ChetaBrowserApi.tabsQuery` and the injection in
  `sidepanel.js` (`activeTab`, `unreadableReason`, `injectPageRead`).
- `scripting` - `scripting.executeScript` runs `readPageInTab` once, on demand,
  in that tab. Used only in `injectPageRead`.
- `host_permissions` (`https://*/*`, `http://*/*`) - needed so `fetch` from the
  panel can reach the user-configurable API base URL without CORS. Not used to
  read pages.

There is no `tabs` permission and no `content_scripts` entry. The Firefox
manifest also declares a `commands` entry for the sidebar keyboard shortcut; a
command is a manifest key rather than a permission.

## Commands

Typing `/start`, `/memories`, or `/help` answers as a command. The panel
answers these three commands itself with no model call, reading the stored
memories over `GET /memories/{user_id}` when an identity exists, because the
`POST /chat/turn` path does not dispatch commands (only the Telegram push path
does). Nothing is stored for a command reply.

## Honest degradation

When a turn returns `memory_degraded`, the reply carries a notice saying memory
was unreachable for that turn, so nothing could be recalled or saved, and that
this is not the same as having no memories. The same wording is used in the
memory panel and in the command replies, so an unreachable server never implies
an empty memory. When the memory toggle is off, the turn is sent with
`memory_enabled: false` and the reply says memory was off for that turn.

## What it shares with the other surfaces

The extension shares one memory space per person with the Telegram bot, the
CLI, and the web widget:

- The same `POST /chat/turn` contract and the same `surface` field vocabulary
  (`telegram`, `cli`, `web`, `extension`).
- The same memory records. A fact stored on one surface comes back as a
  recalled memory on another.
- The same `/memories/{user_id}` listing and `/chat/counterfactual/{turn_id}`
  replay used elsewhere.

Identity is per surface. The extension keeps its own generated
`surface_user_id` and display name locally, so it is a distinct first-class
surface that shares memory with the others rather than a copy of the widget.

## Verification status

Checked without a browser: every file is ASCII only, `node --check` passes on
every script, both manifests parse, every DOM id referenced by `sidepanel.js`
exists in `sidepanel.html`, the packaging script rebuilds both targets, and each
declared permission is used as listed above.

Not verified here, because they need a real browser: the Firefox sidebar
actually opening or toggling, the Firefox `activeTab` grant, the temporary
add-on loading from `extension-dist/firefox/`, whether Firefox grants the
declared `host_permissions` on temporary install or asks for them first, the
Chromium `activeTab` grant, the script injection into a live tab, the exact
browser wording for a refused tab, and the rendering of the page bar. They can
only be confirmed by loading each build and clicking through.
