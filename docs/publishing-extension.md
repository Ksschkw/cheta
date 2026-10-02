# Publishing Cheta Browser Extension

Cheta ships with a unified extension codebase compatible with Chromium (Chrome, Edge, Brave, Opera) and Firefox. The build script `scripts/build_extension.py` automatically generates the store-ready packages:
- `extension-dist/cheta-chrome.zip` (for Chrome Web Store & Microsoft Edge Add-ons)
- `extension-dist/cheta-firefox.xpi` (for Mozilla Firefox Add-ons - AMO)
- `extension-dist/cheta-firefox.zip` (source archive for AMO submission)

---

## 1. Chrome Web Store Publishing

### Step 1: Chrome Developer Account
1. Visit the [Chrome Web Store Developer Dashboard](https://chrome.google.com/webstore/devconsole).
2. Sign in with your Google Account.
3. Pay the one-time $5 developer registration fee if you haven't already.

### Step 2: Upload Package
1. Click **Add new item**.
2. Drag and drop `extension-dist/cheta-chrome.zip`.
3. The store automatically extracts `manifest.json`, icon assets (`icons/icon128.png`, etc.), and background workers.

### Step 3: Store Listing Details
- **Item Name**: Cheta - Memory-First Autonomous Browser Agent
- **Summary**: Delete the app, keep the memory. Autonomous AI sidepanel with Walrus decentralized memory, page tool execution, and cross-device sync.
- **Detailed Description**:
  Explain Cheta's capabilities:
  - Persistent, privacy-preserving memory backed by Walrus decentralized storage.
  - Active page inspection, link extraction, text finding, and highlight tools.
  - Zero lock-in: memory spaces sync seamlessly with Cheta Telegram bot and CLI.
  - No trackers, no ad injection, no third-party telemetry.
- **Category**: Productivity / Tools.
- **Language**: English.
- **Graphic Assets**:
  - Small promo tile: 440 x 280 px
  - Large promo tile: 920 x 680 px
  - Marquee promo tile: 1400 x 560 px
  - At least one screenshot: 1280 x 800 px (capture the sidepanel open next to a webpage).

### Step 4: Privacy & Permissions Justification
Chrome requires justification for declared permissions:
- `sidePanel`: Required to render the conversational sidepanel alongside web content.
- `activeTab`: Required to inspect the current page when the user explicitly clicks "Read Tab" or invokes a page tool. No background reading of inactive tabs.
- `storage`: Required to persist user identity, preferences, and local turn history between browser sessions.
- `scripting`: Required to execute on-demand DOM tools (`page_highlight_text`, `page_find_text`, `page_extract_links`) in the active tab.

### Step 5: Submit for Review
1. Set visibility to **Public** or **Unlisted** (if you want to share a direct link first).
2. Click **Submit for Review**. Review typically takes 24 to 72 hours.

---

## 2. Firefox Add-ons (AMO) Publishing

### Step 1: Firefox Add-on Developer Hub
1. Go to [Mozilla Add-on Developer Hub](https://addons.mozilla.org/developers/).
2. Log in with your Firefox Account (free, no registration fee).

### Step 2: Submit Add-on
1. Click **Submit a New Add-on**.
2. Distribution choice:
   - Select **On this site** for public listing on AMO.
   - Or select **On your own** for self-distribution of signed `.xpi` files.
3. Upload `extension-dist/cheta-firefox.zip` or `extension-dist/cheta-firefox.xpi`.
4. Automated validation runs instantly against manifest version, permissions, and security policies.

### Step 3: Source Code Disclosure
Because Cheta is built with pure vanilla JavaScript without minification, obfuscation, or webpack/bundlers, choose **No** when asked if your add-on requires source code upload for review. The files in `sidepanel.js`, `browser-api.js`, and `background.js` are readable standard ES6.

### Step 4: Submission & Review
Fill in the description, category (Productivity), screenshots, and submit. AMO approvals typically complete in 2 to 24 hours.

---

## 3. Microsoft Edge Add-ons Publishing

1. Visit the [Microsoft Partner Center](https://partner.microsoft.com/dashboard/microsoftedge/overview).
2. Register for a free developer account.
3. Click **Create new extension** and upload `extension-dist/cheta-chrome.zip`.
4. Fill in the store listing information and submit for certification.

---

## 4. Local Testing & Unpacked Loading

### Chromium Browsers (Chrome, Edge, Brave, Opera)
1. Open `chrome://extensions` (or `edge://extensions`, `brave://extensions`).
2. Turn on **Developer mode** toggle in the top-right corner.
3. Click **Load unpacked**.
4. Select the directory `extension-dist/chrome/`.
5. Cheta appears in your extension toolbar. Click the icon to open the side panel.

### Firefox
1. Open `about:debugging#/runtime/this-firefox`.
2. Click **Load Temporary Add-on...**.
3. Select `extension-dist/firefox/manifest.json` or `extension-dist/cheta-firefox.xpi`.
4. Cheta opens in the Firefox sidebar.
