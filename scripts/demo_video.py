#!/usr/bin/env python
"""Record real demo videos for Cheta with Playwright.

WHAT THIS DOES
    Starts the API against a throwaway SQLite database on a fixed port, then
    records three videos by actually driving the software:

      1. web widget demo   - the real chat at /app/, the onboarding tour, two
                             durable facts, a recall turn, the per-turn
                             "Show it without memory" comparison, and the
                             memory dashboard.
      2. extension demo    - extension/sidepanel.html loaded directly, with the
                             small chrome.* surface it uses shimmed in-page.
                             The panel page, not a live installed extension.
      3. cli demo          - a Three.js terminal scene that types out a real
                             captured `python -m cli` session.

    Every video is really recorded from a running browser. Nothing is faked.
    Answers come from the deterministic offline model and memory from the
    in-process Walrus Memory mock, so the whole run is local, fast and
    repeatable. See the module constants below to change that.

HOW TO RUN (one command, from the repository root)
    .venv/bin/python scripts/demo_video.py

    Useful switches:
      --skip-web / --skip-extension / --skip-cli   record only some surfaces
      --port 8101                                  API port to bind
      --memory walrus                              use the live Walrus relayer
      --llm groq                                   use the configured providers
      --keep-server                                leave the API running

OUTPUT
    artifacts/demo/web-widget-demo.webm
    artifacts/demo/extension-demo.webm
    artifacts/demo/cli-demo.webm
    artifacts/demo/frame-*.png        (one still from each video)
    artifacts/demo/console-report.txt (browser console per video)
    artifacts/demo/report.json        (durations, sizes, frames, model)

ASCII ONLY BY POLICY: no emojis, no smart punctuation anywhere in this file or
in the files it writes. Converted CLI output is sanitised before it is drawn.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import random
import re
import shutil
import subprocess
import sys
import textwrap
import time
import urllib.error
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

# ---------------------------------------------------------------------------
# Paths and fixed facts
# ---------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "artifacts" / "demo"
RAW = DEMO / "raw"
VENDOR_THREE = ROOT / "src" / "web" / "vendor" / "three.min.js"
VENV_PY = ROOT / ".venv" / "bin" / "python"
CHROME = Path("/usr/bin/google-chrome")
FFMPEG = Path.home() / ".cache" / "ms-playwright" / "ffmpeg-1011" / "ffmpeg-linux"
CLI_IDENTITY = DEMO / "cli-identity.json"

# Chrome flags: the bundled Playwright browser is a version mismatch on this
# machine, so the system Chrome is launched explicitly.
CHROME_ARGS = ["--no-sandbox", "--disable-dev-shm-usage"]

# Human typing speed, milliseconds per character.
TYPE_MIN_MS = 60
TYPE_MAX_MS = 120

# Web messages. The recall question is worded to genuinely match the stored
# fact under the mock's deterministic token-overlap recall: "wallpaper" style
# phrasing must not be used to make the demo look better than it is.
WEB_FACTS = [
    "I prefer an aisle seat when I fly",
    "My final year project is about multi-agent reinforcement learning for TCP congestion control",
]
WEB_RECALL = "What seat do I prefer when I fly?"

EXT_FACT = "I prefer a window seat on long flights"
EXT_RECALL = "What seat do I prefer on long flights?"

CLI_FACTS = [
    "I prefer an aisle seat when I fly",
    "My final year project is about multi-agent reinforcement learning for TCP congestion control",
]
CLI_RECALL = "What seat do I prefer when I fly?"
CLI_WARMUP = "hello there"

WALLPAPER = "#0a0a0a"
ACCENT = "#d4a853"


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def log(message: str) -> None:
    print("[demo] " + message, flush=True)


def ascii_text(value: str) -> str:
    """Keep the demo text pure ASCII, as the project requires."""
    out = []
    for char in value:
        code = ord(char)
        if char in "\n\t":
            out.append(char)
        elif 32 <= code < 127:
            out.append(char)
        else:
            out.append("?")
    return "".join(out)


def write_text(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(ascii_text(body), encoding="ascii")


# ---------------------------------------------------------------------------
# The API server
# ---------------------------------------------------------------------------


def server_env(memory: str, llm: str) -> dict:
    env = dict(os.environ)
    env["RANTI_DATABASE_PATH"] = "artifacts/demo.db"
    env["PYTHONPATH"] = "src"
    # A throwaway database keeps real local data untouched. Blanking the
    # credentials also keeps the run off shared infrastructure, which matters
    # here because the live relayer is rate limited and slow.
    if memory == "mock":
        env["MEMWAL_PRIVATE_KEY"] = ""
        env["MEMWAL_ACCOUNT_ID"] = ""
    if llm == "offline":
        env["GROQ_API_KEY"] = ""
        env["GEMINI_API_KEY"] = ""
        env["OLLAMA_BASE_URL"] = ""
    return env


def start_server(port: int, memory: str, llm: str) -> subprocess.Popen:
    db = ROOT / "artifacts" / "demo.db"
    for suffix in ("", "-shm", "-wal"):
        candidate = Path(str(db) + suffix)
        if candidate.exists():
            candidate.unlink()
    log("starting API on port %d (memory=%s, llm=%s)" % (port, memory, llm))
    logfile = open(DEMO / "server.log", "w", encoding="utf-8")
    return subprocess.Popen(
        [
            str(VENV_PY),
            "-m",
            "uvicorn",
            "main:app",
            "--port",
            str(port),
            "--app-dir",
            "src",
        ],
        cwd=str(ROOT),
        env=server_env(memory, llm),
        stdout=logfile,
        stderr=subprocess.STDOUT,
    )


def wait_health(base: str, timeout_sec: float = 60.0) -> dict:
    deadline = time.time() + timeout_sec
    last = ""
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(base + "/health", timeout=3) as response:
                return json.loads(response.read().decode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            last = str(exc)
            time.sleep(0.5)
    raise RuntimeError("API never became healthy: " + last)


def stop_server(proc: subprocess.Popen | None) -> None:
    if proc is None:
        return
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=10)
    log("API stopped")


# ---------------------------------------------------------------------------
# Video helpers
# ---------------------------------------------------------------------------


def media_info(path: Path) -> dict:
    result = subprocess.run(
        [str(FFMPEG), "-i", str(path)], capture_output=True, text=True
    )
    text = result.stderr
    info = {"duration": None, "width": None, "height": None}
    match = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.\d+)", text)
    if match:
        info["duration"] = (
            int(match.group(1)) * 3600 + int(match.group(2)) * 60 + float(match.group(3))
        )
    match = re.search(r"Video:.*?(\d{2,5})x(\d{2,5})", text)
    if match:
        info["width"] = int(match.group(1))
        info["height"] = int(match.group(2))
    return info


def grab_frame(video: Path, at_seconds: float, out: Path) -> None:
    subprocess.run(
        [
            str(FFMPEG),
            "-y",
            "-ss",
            "%.2f" % at_seconds,
            "-i",
            str(video),
            "-frames:v",
            "1",
            str(out),
        ],
        capture_output=True,
    )


def finalize_video(scene: str, raw_dir: Path, dest: Path) -> Path:
    found = sorted(glob.glob(str(raw_dir / "*.webm")))
    if not found:
        raise RuntimeError("no video was written for scene " + scene)
    shutil.move(found[0], str(dest))
    return dest


# ---------------------------------------------------------------------------
# Browser plumbing
# ---------------------------------------------------------------------------


class ConsoleRecorder:
    """Collects console output and page errors for one page."""

    def __init__(self, scene: str) -> None:
        self.scene = scene
        self.lines: list[str] = []

    def attach(self, page) -> None:
        page.on(
            "console",
            lambda msg: self.lines.append("[console.%s] %s" % (msg.type, msg.text)),
        )
        page.on("pageerror", lambda err: self.lines.append("[pageerror] %s" % err))
        page.on(
            "requestfailed",
            lambda req: self.lines.append(
                "[requestfailed] %s %s" % (req.url, req.failure)
            ),
        )

    def errors(self) -> list[str]:
        bad = []
        for line in self.lines:
            if line.startswith("[pageerror]") or line.startswith("[console.error]"):
                bad.append(line)
            elif line.startswith("[requestfailed]"):
                bad.append(line)
        return bad


def install_cors(page, base: str) -> None:
    """Add CORS headers to API responses for the file:// extension page.

    The panel is loaded as a local file, so its fetch calls to the API are
    cross-origin. Rather than disabling web security, this rewrites the
    responses on the way through Playwright. The panel itself is untouched.
    """

    def handler(route, request):
        if request.method == "OPTIONS":
            route.fulfill(
                status=204,
                headers={
                    "access-control-allow-origin": "*",
                    "access-control-allow-methods": "GET,POST,OPTIONS",
                    "access-control-allow-headers": "content-type,accept",
                    "access-control-max-age": "600",
                },
            )
            return
        try:
            response = route.fetch()
        except Exception:  # noqa: BLE001
            route.abort()
            return
        headers = dict(response.headers)
        headers["access-control-allow-origin"] = "*"
        route.fulfill(status=response.status, headers=headers, body=response.body())

    page.route(base + "/**", handler)


EXTENSION_SHIM = """
window.chrome = window.chrome || {};
(function () {
  var store = {
    "ranti.extension.base_url": "__BASE__",
    "ranti.extension.display_name": "Ada"
  };
  function pick(keys, cb) {
    var out = {};
    if (keys === null || keys === undefined) {
      Object.keys(store).forEach(function (k) { out[k] = store[k]; });
    } else if (typeof keys === "string") {
      if (Object.prototype.hasOwnProperty.call(store, keys)) { out[keys] = store[keys]; }
    } else if (Array.isArray(keys)) {
      keys.forEach(function (k) {
        if (Object.prototype.hasOwnProperty.call(store, k)) { out[k] = store[k]; }
      });
    } else if (typeof keys === "object") {
      Object.keys(keys).forEach(function (k) {
        out[k] = Object.prototype.hasOwnProperty.call(store, k) ? store[k] : keys[k];
      });
    }
    window.setTimeout(function () { cb(out); }, 0);
  }
  chrome.storage = { local: {
    get: function (keys, cb) { pick(keys, cb); },
    set: function (values, cb) {
      Object.keys(values).forEach(function (k) { store[k] = values[k]; });
      if (cb) { window.setTimeout(cb, 0); }
    }
  } };
  chrome.runtime = chrome.runtime || {};
  chrome.tabs = {
    query: function (info, cb) {
      window.setTimeout(function () {
        cb([{ id: 1, url: "https://example.org/deep-sea-cables",
              title: "How deep sea cables carry the internet" }]);
      }, 0);
    },
    create: function (props, cb) {
      if (cb) { window.setTimeout(function () { cb({ id: 2 }); }, 0); }
    }
  };
  chrome.scripting = {
    executeScript: function (injection, cb) {
      window.setTimeout(function () {
        cb([{ result: {
          title: "How deep sea cables carry the internet",
          hostname: "example.org",
          text: "Submarine fibre optic cables carry almost all intercontinental " +
                "internet traffic. A cable is laid by a ship and can carry " +
                "terabits per second between landing stations.",
          truncated: false,
          totalLength: 186
        } }]);
      }, 0);
    }
  };
  chrome.sidePanel = { setPanelBehavior: function () { return Promise.resolve(); } };
  chrome.sidebarAction = { open: function () { return Promise.resolve(); } };
})();
"""


def human_type(page, selector: str, text: str) -> None:
    page.click(selector)
    page.fill(selector, "")
    page.keyboard.type(text, delay=random.randint(TYPE_MIN_MS, TYPE_MAX_MS))


def wait_for_appended(page, selector: str, before: int, timeout: int = 60000) -> None:
    page.wait_for_function(
        "([sel, n]) => document.querySelectorAll(sel).length > n",
        arg=[selector, before],
        timeout=timeout,
    )


def wait_idle(page, send_selector: str, timeout: int = 60000) -> None:
    page.wait_for_function(
        "sel => { var b = document.querySelector(sel); return b && !b.disabled; }",
        arg=send_selector,
        timeout=timeout,
    )


def step_tour(page, pause_ms: int, console: ConsoleRecorder) -> int:
    """Click through the onboarding tour, pausing so it is readable on camera."""
    steps = 0
    for _ in range(16):
        classes = page.get_attribute("#tour", "class") or ""
        if "hidden" in classes and "is-visible" not in classes:
            break
        if not page.is_visible("#tour-card"):
            break
        page.wait_for_timeout(pause_ms)
        progress = ""
        if page.is_visible("#tour-progress"):
            progress = ascii_text(page.inner_text("#tour-progress"))
        button = ascii_text(page.inner_text("#tour-next"))
        console.lines.append("[tour] %s -> %s" % (progress, button))
        page.click("#tour-next")
        steps += 1
        page.wait_for_timeout(700)
    return steps


# ---------------------------------------------------------------------------
# Scene 1: the web widget
# ---------------------------------------------------------------------------


def record_web(browser, base: str, console: ConsoleRecorder) -> Path:
    raw_dir = RAW / "web"
    raw_dir.mkdir(parents=True, exist_ok=True)
    context = browser.new_context(
        viewport={"width": 1280, "height": 720},
        record_video_dir=str(raw_dir),
        record_video_size={"width": 1280, "height": 720},
    )
    page = context.new_page()
    console.attach(page)

    log("web: loading /app/")
    page.goto(base + "/app/", wait_until="load")
    page.wait_for_timeout(1500)

    log("web: walking the onboarding tour")
    if page.is_visible("#tour-card"):
        step_tour(page, 1500, console)
    page.wait_for_timeout(1200)

    log("web: storing two durable facts")
    for fact in WEB_FACTS:
        before = page.locator(".turn.assistant").count()
        human_type(page, "#chat-input", fact)
        page.wait_for_timeout(500)
        page.keyboard.press("Enter")
        wait_for_appended(page, ".turn.assistant", before)
        wait_idle(page, "#send-button")
        page.wait_for_timeout(1800)

    log("web: recall turn")
    before = page.locator(".turn.assistant").count()
    human_type(page, "#chat-input", WEB_RECALL)
    page.wait_for_timeout(500)
    page.keyboard.press("Enter")
    wait_for_appended(page, ".turn.assistant", before)
    wait_idle(page, "#send-button")
    page.wait_for_timeout(2600)
    recalled = page.locator(".turn.assistant .recalled").count()
    console.lines.append("[web] recalled blocks after recall turn: %d" % recalled)
    if recalled:
        console.lines.append(
            "[web] recalled text: "
            + ascii_text(page.locator(".turn.assistant .recalled").last.inner_text())
        )
    else:
        console.lines.append("[web] NOTE: no memories were recalled on this turn")

    log("web: per-turn without-memory comparison")
    toggle = page.locator(".turn.assistant .cf-toggle").last
    if toggle.count():
        toggle.click()
        try:
            page.wait_for_selector(".turn.assistant .cf-result", timeout=60000)
            page.wait_for_timeout(5200)
            console.lines.append(
                "[web] comparison text: "
                + ascii_text(page.locator(".cf-result").last.inner_text())[:600]
            )
        except Exception as exc:  # noqa: BLE001
            console.lines.append("[web] comparison failed: %s" % exc)
    else:
        console.lines.append("[web] NOTE: no without-memory control was present")

    log("web: opening the memory dashboard")
    page.click("a.head-link")
    page.wait_for_selector("#memory-list .memory-card", timeout=30000)
    page.wait_for_timeout(1500)
    page.evaluate(
        "document.getElementById('memory-list')"
        ".scrollIntoView({ block: 'center' })"
    )
    page.wait_for_timeout(5200)

    context.close()
    return finalize_video("web", raw_dir, DEMO / "web-widget-demo.webm")


# ---------------------------------------------------------------------------
# Scene 2: the extension side panel
# ---------------------------------------------------------------------------


def record_extension(browser, base: str, console: ConsoleRecorder) -> Path:
    raw_dir = RAW / "extension"
    raw_dir.mkdir(parents=True, exist_ok=True)
    context = browser.new_context(
        viewport={"width": 480, "height": 800},
        record_video_dir=str(raw_dir),
        record_video_size={"width": 480, "height": 800},
    )
    context.add_init_script(EXTENSION_SHIM.replace("__BASE__", base))
    page = context.new_page()
    console.attach(page)
    install_cors(page, base)

    log("extension: loading sidepanel.html with a shimmed chrome API")
    page.goto((ROOT / "extension" / "sidepanel.html").as_uri(), wait_until="load")
    page.wait_for_timeout(1500)
    console.lines.append(
        "[extension] conn-status: " + ascii_text(page.locator("#conn-status").inner_text())
    )

    log("extension: onboarding tour")
    if page.is_visible("#tour-card"):
        step_tour(page, 1300, console)
    page.wait_for_timeout(1000)

    log("extension: storing a durable fact")
    before = page.locator(".msg.assistant").count()
    human_type(page, "#chat-input", EXT_FACT)
    page.wait_for_timeout(500)
    page.keyboard.press("Enter")
    wait_for_appended(page, ".msg.assistant", before)
    wait_idle(page, "#send-button")
    page.wait_for_timeout(1800)

    log("extension: recall turn")
    before = page.locator(".msg.assistant").count()
    human_type(page, "#chat-input", EXT_RECALL)
    page.wait_for_timeout(500)
    page.keyboard.press("Enter")
    wait_for_appended(page, ".msg.assistant", before)
    wait_idle(page, "#send-button")
    page.wait_for_timeout(3000)
    recalled = page.locator(".msg.assistant .recalled").count()
    console.lines.append("[extension] recalled blocks after recall turn: %d" % recalled)
    if recalled:
        console.lines.append(
            "[extension] recalled text: "
            + ascii_text(page.locator(".msg.assistant .recalled").last.inner_text())
        )

    log("extension: Use this page")
    page.click("#use-page")
    page.wait_for_timeout(2500)
    console.lines.append(
        "[extension] page-status: " + ascii_text(page.locator("#page-status").inner_text())
    )
    page.wait_for_timeout(3200)

    context.close()
    return finalize_video("extension", raw_dir, DEMO / "extension-demo.webm")


# ---------------------------------------------------------------------------
# Scene 3: the CLI, typed out in a Three.js terminal
# ---------------------------------------------------------------------------


def run_cli(args: list[str], stdin_text: str, base: str, timeout: int = 240) -> str:
    env = dict(os.environ)
    env["RANTI_API_URL"] = base
    env["RANTI_CLI_CONFIG"] = str(CLI_IDENTITY)
    env["PYTHONPATH"] = "src"
    proc = subprocess.run(
        [str(VENV_PY), "-m", "cli"] + args,
        cwd=str(ROOT),
        env=env,
        input=stdin_text,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return proc.stdout + proc.stderr


def capture_cli_transcript(base: str) -> list[dict]:
    """Run the real CLI and build a scene transcript from its real output."""
    if CLI_IDENTITY.exists():
        CLI_IDENTITY.unlink()
    # A warm-up turn first, so the recorded session is not the person's first:
    # that keeps the very long onboarding paragraph out of a 30 second scene.
    run_cli(["chat"], "Ada\n" + CLI_WARMUP + "\n/quit\n", base)

    inputs = list(CLI_FACTS) + [CLI_RECALL, "/quit"]
    chat = run_cli(["chat"], "\n".join(inputs) + "\n", base)

    marker = "you> "
    segments = chat.split(marker)
    head = segments[0]
    outputs = segments[1:]
    head = head.replace("display name [cli-user]: ", "display name [cli-user]: Ada", 1)

    lines: list[dict] = []

    def add(kind: str, text: str) -> None:
        lines.append({"t": ascii_text(text), "k": kind})

    for row in head.rstrip("\n").split("\n"):
        add("out", row)
    for index, raw_input in enumerate(inputs):
        add("cmd", "you> " + raw_input)
        if index < len(outputs):
            for row in outputs[index].rstrip("\n").split("\n"):
                add("out", row)

    for argv in (
        ["memories", "--all", "--size", "10"],
        ["stats"],
        ["recall", "seat"],
    ):
        add("cmd", "$ python -m cli " + " ".join(argv))
        text = run_cli(argv, "", base)
        for row in text.rstrip("\n").split("\n"):
            add("out", row)
        add("out", "")
    return lines


def wrap_transcript(lines: list[dict], width: int = 106) -> list[dict]:
    wrapped: list[dict] = []
    for line in lines:
        text = line["t"]
        if text == "":
            wrapped.append({"t": "", "k": line["k"]})
            continue
        if len(text) <= width:
            wrapped.append({"t": text, "k": line["k"]})
            continue
        parts = textwrap.wrap(text, width, subsequent_indent="    ") or [""]
        for part in parts:
            wrapped.append({"t": part, "k": line["k"]})
    return wrapped


CLI_SCENE_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="color-scheme" content="dark">
    <title>cheta cli</title>
    <style>
      html, body { margin: 0; padding: 0; height: 100%; background: #0a0a0a; overflow: hidden; }
      #stage { position: fixed; inset: 0; }
      canvas { display: block; }
    </style>
  </head>
  <body>
    <div id="stage"></div>
    <script src="__THREE_SRC__"></script>
    <script>
    (function () {
      "use strict";
      var LINES = __LINES_JSON__;
      var TYPE_MS = __TYPE_MS__;
      var CW = 2048, CH = 1152;
      var BG = "#0a0a0a", PANEL = "#0e0e0e", LINE = "#262626";
      var TEXT = "#ededed", SOFT = "#c9c9c9", MUTED = "#8a8a8a", ACCENT = "#d4a853";
      var FONT = '28px "DejaVu Sans Mono", "Liberation Mono", monospace';
      var M = 28, TB = 64, LH = 34, PAD = M + 40;
      var BODY_TOP = M + TB + 28;
      var BODY_BOTTOM = CH - M - 28;
      var VISIBLE = Math.floor((BODY_BOTTOM - BODY_TOP) / LH);

      var c2 = document.createElement("canvas");
      c2.width = CW; c2.height = CH;
      var ctx = c2.getContext("2d");

      var units = 0;
      for (var i = 0; i < LINES.length; i++) { units += LINES[i].t.length + 6; }
      var charDelay = TYPE_MS / Math.max(1, units);
      var linePause = charDelay * 6;
      var cur = 0, ch = 0, done = false, dirty = true;

      function roundRect(x, y, w, h, r) {
        ctx.beginPath();
        ctx.moveTo(x + r, y);
        ctx.arcTo(x + w, y, x + w, y + h, r);
        ctx.arcTo(x + w, y + h, x, y + h, r);
        ctx.arcTo(x, y + h, x, y, r);
        ctx.arcTo(x, y, x + w, y, r);
        ctx.closePath();
      }

      function drawFrame(t) {
        ctx.fillStyle = BG;
        ctx.fillRect(0, 0, CW, CH);

        ctx.fillStyle = PANEL;
        roundRect(M, M, CW - 2 * M, CH - 2 * M, 20);
        ctx.fill();
        ctx.strokeStyle = LINE;
        ctx.lineWidth = 2;
        ctx.stroke();

        ctx.strokeStyle = LINE;
        ctx.beginPath();
        ctx.moveTo(M, M + TB);
        ctx.lineTo(CW - M, M + TB);
        ctx.stroke();

        ctx.fillStyle = ACCENT;
        ctx.beginPath();
        ctx.arc(M + 34, M + TB / 2, 9, 0, Math.PI * 2);
        ctx.fill();
        ctx.fillStyle = "#3a3a3a";
        ctx.beginPath();
        ctx.arc(M + 66, M + TB / 2, 9, 0, Math.PI * 2);
        ctx.fill();
        ctx.beginPath();
        ctx.arc(M + 98, M + TB / 2, 9, 0, Math.PI * 2);
        ctx.fill();

        ctx.font = 'bold 30px "DejaVu Sans Mono", "Liberation Mono", monospace';
        ctx.textBaseline = "middle";
        ctx.fillStyle = TEXT;
        ctx.fillText("cheta", M + 140, M + TB / 2);
        ctx.fillStyle = ACCENT;
        ctx.fillText("cli", M + 140 + ctx.measureText("cheta ").width, M + TB / 2);
        ctx.fillStyle = MUTED;
        ctx.font = '22px "DejaVu Sans Mono", "Liberation Mono", monospace';
        ctx.textAlign = "right";
        ctx.fillText("python -m cli", CW - M - 40, M + TB / 2);
        ctx.textAlign = "left";

        ctx.save();
        ctx.beginPath();
        ctx.rect(M + 2, BODY_TOP - 6, CW - 2 * M - 4, BODY_BOTTOM - BODY_TOP + 12);
        ctx.clip();

        var from = Math.max(0, cur + 1 - VISIBLE);
        var row = 0;
        for (var index = from; index <= cur && index < LINES.length; index++) {
          var line = LINES[index];
          var shown = index < cur ? line.t : line.t.slice(0, Math.floor(ch));
          var y = BODY_TOP + row * LH + LH * 0.5;
          ctx.font = FONT;
          ctx.textBaseline = "middle";
          if (line.k === "cmd") {
            var sp = shown.indexOf(" ");
            var head = sp === -1 ? shown : shown.slice(0, sp);
            var rest = sp === -1 ? "" : shown.slice(sp);
            ctx.fillStyle = ACCENT;
            ctx.fillText(head, PAD, y);
            var w = ctx.measureText(head).width;
            ctx.fillStyle = TEXT;
            ctx.fillText(rest, PAD + w, y);
          } else {
            ctx.fillStyle = SOFT;
            ctx.fillText(shown, PAD, y);
          }
          row += 1;
        }

        if (!done && Math.floor(t / 450) % 2 === 0) {
          var last = LINES[cur];
          if (last) {
            var text = last.t.slice(0, Math.floor(ch));
            ctx.font = FONT;
            var width = ctx.measureText(text).width;
            ctx.fillStyle = ACCENT;
            ctx.fillRect(PAD + width + 3, BODY_TOP + (row - 1) * LH + 6, 15, LH - 12);
          }
        }
        ctx.restore();
      }

      function advance(dt, t) {
        if (done) return;
        var budget = dt;
        var guard = 0;
        while (budget > 0 && cur < LINES.length && guard < 200000) {
          guard += 1;
          var line = LINES[cur];
          var remain = line.t.length - ch;
          if (remain > 0) {
            var take = Math.min(remain, budget / charDelay);
            ch += take;
            budget -= take * charDelay;
            if (ch < line.t.length - 0.001) { break; }
          }
          budget -= linePause;
          cur += 1;
          ch = 0;
        }
        if (cur >= LINES.length) { done = true; }
        dirty = true;
      }

      var started = 0, lastFrame = 0, doneAt = 0, rendered = false;

      function loop(now) {
        if (!started) { started = now; lastFrame = now; }
        var t = now - started;
        var dt = Math.min(120, now - lastFrame);
        lastFrame = now;
        advance(dt, t);
        drawFrame(t);

        if (renderer) {
          camera.position.x = Math.sin(t * 0.00013) * 0.42;
          camera.position.y = Math.cos(t * 0.00010) * 0.16;
          camera.position.z = 5.9 - Math.min(t / 30000, 1) * 0.55;
          camera.lookAt(0, 0, 0);
          plane.rotation.y = Math.sin(t * 0.00009) * 0.05;
          plane.rotation.x = Math.cos(t * 0.00007) * 0.02;
          texture.needsUpdate = true;
          renderer.render(scene, camera);
        } else if (!rendered) {
          c2.style.position = "fixed";
          c2.style.left = "0";
          c2.style.top = "0";
          c2.style.width = "100%";
          c2.style.height = "100%";
          document.getElementById("stage").appendChild(c2);
          rendered = true;
        }

        if (done) {
          if (!doneAt) { doneAt = t; }
          if (t - doneAt > 1500) {
            window.SCENE_DONE = true;
            window.SCENE_STATE = { lines: LINES.length, ms: t };
          }
        }
        requestAnimationFrame(loop);
      }

      var renderer = null, scene = null, camera = null, plane = null, texture = null;
      try {
        var THREE = window.THREE;
        renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false });
        renderer.setPixelRatio(1);
        renderer.setSize(window.innerWidth, window.innerHeight);
        renderer.setClearColor(0x0a0a0a, 1);
        document.getElementById("stage").appendChild(renderer.domElement);
        scene = new THREE.Scene();
        scene.background = new THREE.Color(0x0a0a0a);
        camera = new THREE.PerspectiveCamera(
          45, window.innerWidth / window.innerHeight, 0.1, 100);
        camera.position.set(0, 0, 5.9);
        texture = new THREE.CanvasTexture(c2);
        if (THREE.SRGBColorSpace) { texture.colorSpace = THREE.SRGBColorSpace; }
        if (THREE.LinearFilter) { texture.minFilter = THREE.LinearFilter; }
        if (THREE.SRGBColorSpace && renderer.outputColorSpace !== undefined) {
          renderer.outputColorSpace = THREE.SRGBColorSpace;
        }
        var H = 4.2, W = H * (CW / CH);
        plane = new THREE.Mesh(
          new THREE.PlaneGeometry(W, H),
          new THREE.MeshBasicMaterial({ map: texture })
        );
        scene.add(plane);
        window.addEventListener("resize", function () {
          camera.aspect = window.innerWidth / window.innerHeight;
          camera.updateProjectionMatrix();
          renderer.setSize(window.innerWidth, window.innerHeight);
        });
      } catch (err) {
        renderer = null;
        window.SCENE_ERROR = String(err);
      }

      requestAnimationFrame(loop);
    })();
    </script>
  </body>
</html>
"""


def build_cli_scene(lines: list[dict], out_path: Path, type_ms: int) -> None:
    rel = os.path.relpath(str(VENDOR_THREE), str(out_path.parent))
    body = CLI_SCENE_TEMPLATE
    body = body.replace("__THREE_SRC__", rel)
    body = body.replace("__TYPE_MS__", str(type_ms))
    payload = json.dumps(lines)
    payload = payload.replace("</", "<\\/")
    body = body.replace("__LINES_JSON__", payload)
    write_text(out_path, body)


def record_cli(browser, scene_path: Path, console: ConsoleRecorder) -> Path:
    raw_dir = RAW / "cli"
    raw_dir.mkdir(parents=True, exist_ok=True)
    context = browser.new_context(
        viewport={"width": 1280, "height": 720},
        record_video_dir=str(raw_dir),
        record_video_size={"width": 1280, "height": 720},
    )
    page = context.new_page()
    console.attach(page)
    page.goto(scene_path.as_uri(), wait_until="load")
    try:
        page.wait_for_function("window.SCENE_DONE === true", timeout=90000)
    except Exception as exc:  # noqa: BLE001
        console.lines.append("[cli] SCENE_DONE wait failed: %s" % exc)
    page.wait_for_timeout(5500)
    state = page.evaluate("window.SCENE_STATE || null")
    error = page.evaluate("window.SCENE_ERROR || null")
    console.lines.append("[cli] scene state: %s" % json.dumps(state))
    if error:
        console.lines.append("[cli] SCENE_ERROR: %s" % error)
    context.close()
    return finalize_video("cli", raw_dir, DEMO / "cli-demo.webm")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Record the Cheta demo videos.")
    parser.add_argument("--port", type=int, default=8101)
    parser.add_argument("--memory", choices=["mock", "walrus"], default="mock")
    parser.add_argument("--llm", choices=["offline", "groq"], default="offline")
    parser.add_argument("--skip-web", action="store_true")
    parser.add_argument("--skip-extension", action="store_true")
    parser.add_argument("--skip-cli", action="store_true")
    parser.add_argument("--keep-server", action="store_true")
    args = parser.parse_args(argv)

    for tool in (VENV_PY, CHROME, VENDOR_THREE):
        if not tool.exists():
            raise SystemExit("required path is missing: " + str(tool))
    if not FFMPEG.exists():
        log("note: bundled ffmpeg not found; durations and frames will be skipped")

    random.seed(20261001)
    DEMO.mkdir(parents=True, exist_ok=True)
    if RAW.exists():
        shutil.rmtree(RAW)
    RAW.mkdir(parents=True)

    base = "http://127.0.0.1:%d" % args.port
    server = start_server(args.port, args.memory, args.llm)
    report: dict = {
        "port": args.port,
        "memory_backend": args.memory,
        "llm_backend": args.llm,
        "videos": {},
        "console": {},
    }
    try:
        health = wait_health(base)
        report["health"] = health
        log("API healthy: %s" % json.dumps(health))

        transcript: list[dict] = []
        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                executable_path=str(CHROME), args=CHROME_ARGS
            )
            try:
                # The browser scenes run first so the memory dashboard, which
                # lists the first registered user, shows the web user's own
                # memories rather than the CLI identity's.
                if not args.skip_web:
                    rec = ConsoleRecorder("web")
                    path = record_web(browser, base, rec)
                    report["videos"]["web"] = {"path": str(path)}
                    report["console"]["web"] = rec.lines
                if not args.skip_extension:
                    rec = ConsoleRecorder("extension")
                    path = record_extension(browser, base, rec)
                    report["videos"]["extension"] = {"path": str(path)}
                    report["console"]["extension"] = rec.lines
                if not args.skip_cli:
                    log("cli: capturing a real CLI session")
                    raw_lines = capture_cli_transcript(base)
                    transcript = wrap_transcript(raw_lines)
                    log("cli: captured %d transcript lines" % len(transcript))
                    scene_path = DEMO / "cli-scene.html"
                    build_cli_scene(transcript, scene_path, type_ms=21000)
                    rec = ConsoleRecorder("cli")
                    path = record_cli(browser, scene_path, rec)
                    report["videos"]["cli"] = {"path": str(path)}
                    report["console"]["cli"] = rec.lines
            finally:
                browser.close()
    finally:
        if not args.keep_server:
            stop_server(server)

    # Measure every video and pull one still from each.
    frame_times = {"web": 0.62, "extension": 0.66, "cli": 0.70}
    for name, entry in report["videos"].items():
        path = Path(entry["path"])
        if not path.exists():
            continue
        entry["bytes"] = path.stat().st_size
        info = media_info(path)
        entry.update(info)
        frame = DEMO / ("frame-%s.png" % name)
        if FFMPEG.exists() and info.get("duration"):
            grab_frame(path, info["duration"] * frame_times[name], frame)
            entry["frame"] = str(frame)

    # Console report, plus a machine readable summary.
    report_lines = []
    for scene in ("web", "extension", "cli"):
        report_lines.append("=== %s ===" % scene)
        messages = report["console"].get(scene, [])
        if not messages:
            report_lines.append("(no console output captured)")
        for line in messages:
            report_lines.append(line)
        report_lines.append("")
    write_text(DEMO / "console-report.txt", "\n".join(report_lines) + "\n")
    write_text(DEMO / "report.json", json.dumps(report, indent=2) + "\n")

    log("")
    log("results")
    for name in ("web", "extension", "cli"):
        entry = report["videos"].get(name)
        if not entry:
            continue
        log(
            "  %-9s %s  %sx%s  %ss  %s bytes"
            % (
                name,
                entry["path"],
                entry.get("width"),
                entry.get("height"),
                ("%.2f" % entry["duration"]) if entry.get("duration") else "?",
                entry.get("bytes"),
            )
        )
    errors = []
    for scene in ("web", "extension", "cli"):
        for line in report["console"].get(scene, []):
            if line.startswith("[pageerror]") or line.startswith("[console.error]"):
                errors.append("[%s] %s" % (scene, line))
    if errors:
        log("console errors:")
        for line in errors:
            log("  " + line)
    else:
        log("console errors: none")
    log("report: " + str(DEMO / "report.json"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
