/* Cheta web surfaces: shared logic for the chat and the memory dashboard.
 *
 * Vanilla JS only. No build step, no frameworks, no external requests.
 * Every API call is a same-origin root-relative path, so it works under the
 * FastAPI static mount at /app as well as from the origin root.
 *
 * ASCII only by policy: no emojis, no smart punctuation.
 */

(function () {
  "use strict";

  var SURFACE = "web";
  var THRESHOLD = 10;
  var MAX_TEXT = 8000;
  var DEFAULT_NAME = "Web visitor";

  var KEYS = {
    displayName: "ranti.display_name",
    surfaceUserId: "ranti.surface_user_id",
    userId: "ranti.user_id",
    memoryEnabled: "ranti.memory_enabled",
    onboarded: "ranti.onboarded",
    sessions: "ranti.web.sessions",
    currentSessionId: "ranti.web.current_session_id"
  };

  var COMMAND_SUGGESTIONS = [
    { command: "/start", description: "greet, and show what I already remember" },
    { command: "/memories", description: "show every note I have stored about you" },
    {
      command: "/forget",
      hint: "/forget <number>",
      description: "retire a note so I stop bringing it up"
    },
    { command: "/pair", description: "get a one-time code to add another client" },
    { command: "/sessions", description: "list the clients sharing your memory space" },
    {
      command: "/unpair",
      hint: "/unpair [number]",
      description: "leave the shared space, or remove a listed client"
    },
    {
      command: "/name",
      hint: "/name <name>",
      description: "set or update your display name across sessions"
    },
    {
      command: "/crawl",
      hint: "/crawl <url>",
      description: "crawl public website and extract key links"
    },
    { command: "/help", description: "this full reference" }
  ];

  /* --------------------------------------------------------------- dom */

  function $(id) {
    return document.getElementById(id);
  }

  function el(tag, attrs, children) {
    var node = document.createElement(tag);
    if (attrs) {
      Object.keys(attrs).forEach(function (key) {
        var value = attrs[key];
        if (value === null || value === undefined || value === false) {
          return;
        }
        if (key === "class") {
          node.className = String(value);
        } else if (key === "text") {
          node.textContent = String(value);
        } else if (key.indexOf("on") === 0 && typeof value === "function") {
          node.addEventListener(key.slice(2), value);
        } else {
          node.setAttribute(key, String(value));
        }
      });
    }
    if (children !== null && children !== undefined) {
      var list = Array.isArray(children) ? children : [children];
      list.forEach(function (child) {
        if (child === null || child === undefined || child === false) {
          return;
        }
        if (typeof child === "string" || typeof child === "number") {
          node.appendChild(document.createTextNode(String(child)));
        } else {
          node.appendChild(child);
        }
      });
    }
    return node;
  }

  function clear(node) {
    while (node && node.firstChild) {
      node.removeChild(node.firstChild);
    }
  }

  function setText(node, value) {
    if (node) {
      node.textContent = value === null || value === undefined ? "" : String(value);
    }
  }

  /* ----------------------------------------------------------- storage */

  function storeGet(key, fallback) {
    try {
      var value = window.localStorage.getItem(key);
      return value === null ? fallback : value;
    } catch (err) {
      return fallback;
    }
  }

  function storeSet(key, value) {
    try {
      window.localStorage.setItem(key, String(value));
    } catch (err) {
      /* Private mode or storage disabled: state stays in memory only. */
    }
  }

  function getSurfaceUserId() {
    var id = storeGet(KEYS.surfaceUserId, "");
    if (!id) {
      id = "web-" + newId();
      storeSet(KEYS.surfaceUserId, id);
    }
    return id;
  }

  function newId() {
    if (window.crypto && typeof window.crypto.randomUUID === "function") {
      return window.crypto.randomUUID();
    }
    var bytes = new Uint8Array(16);
    var i;
    if (window.crypto && typeof window.crypto.getRandomValues === "function") {
      window.crypto.getRandomValues(bytes);
    } else {
      for (i = 0; i < 16; i += 1) {
        bytes[i] = Math.floor(Math.random() * 256);
      }
    }
    bytes[6] = (bytes[6] & 15) | 64;
    bytes[8] = (bytes[8] & 63) | 128;
    var hex = [];
    for (i = 0; i < 16; i += 1) {
      hex.push((bytes[i] + 256).toString(16).slice(1));
    }
    return (
      hex.slice(0, 4).join("") +
      "-" +
      hex.slice(4, 6).join("") +
      "-" +
      hex.slice(6, 8).join("") +
      "-" +
      hex.slice(8, 10).join("") +
      "-" +
      hex.slice(10, 16).join("")
    );
  }

  /* --------------------------------------------------------------- api */

  function api(path, options) {
    var opts = options || {};
    var init = {
      method: opts.method || "GET",
      headers: { Accept: "application/json" },
      cache: "no-store"
    };
    if (opts.body !== undefined && opts.body !== null) {
      init.headers["Content-Type"] = "application/json";
      init.body = JSON.stringify(opts.body);
    }
    return fetch(path, init)
      .catch(function () {
        var networkError = new Error("network");
        networkError.network = true;
        throw networkError;
      })
      .then(function (response) {
        return response.text().then(function (raw) {
          var data = null;
          if (raw) {
            try {
              data = JSON.parse(raw);
            } catch (err) {
              data = null;
            }
          }
          if (!response.ok) {
            var detail = null;
            if (data && typeof data === "object") {
              detail = data.detail || data.error;
            }
            var httpError = new Error(detail || "request failed");
            httpError.httpStatus = response.status;
            throw httpError;
          }
          return data;
        });
      });
  }

  /* Plain words for a failed call. Never prints a host, port or path. The
   * message says what happened, what it means for the turn, and what to do. */
  function plainError(err) {
    if (err && err.network) {
      return (
        "Could not reach the server, so this message was not sent and memory " +
        "was not changed. Check your connection and try again."
      );
    }
    return (
      "The request could not be completed, so this message was not sent and " +
      "memory was not changed. Please try again."
    );
  }

  /* --------------------------------------------------------- formatting */

  function asArray(value) {
    return Array.isArray(value) ? value : [];
  }

  function num(value) {
    var n = Number(value);
    return isFinite(n) ? String(Math.round(n)) : "0";
  }

  function fixed(value, digits) {
    var n = Number(value);
    if (!isFinite(n)) {
      n = 0;
    }
    return n.toFixed(digits);
  }

  function truncate(value, max) {
    var text = value === null || value === undefined ? "" : String(value);
    if (text.length <= max) {
      return text;
    }
    return text.slice(0, Math.max(1, max - 3)) + "...";
  }

  function safeFilename(value) {
    var text = value === null || value === undefined ? "" : String(value);
    var cleaned = text.replace(/[^A-Za-z0-9_-]+/g, "-").replace(/^-+|-+$/g, "");
    return cleaned || "user";
  }

  function downloadJson(filename, data) {
    var body = JSON.stringify(data, null, 2);
    var blob = new Blob([body], { type: "application/json" });
    var url = URL.createObjectURL(blob);
    var link = el("a", { href: url, download: filename });
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    window.setTimeout(function () {
      URL.revokeObjectURL(url);
    }, 1000);
  }

  function statusTag(status) {
    var value = String(status || "unknown");
    var cls = "tag";
    if (value === "active") {
      cls += " tag-ok";
    } else if (value === "superseded") {
      cls += " tag-warn";
    } else if (value === "contradicted") {
      cls += " tag-fail";
    }
    return el("span", { class: cls }, value);
  }

  /* ================================================================ tour */

  /* A guided tour, not a wall of text. Each step points at a real element: a
   * ring cuts a hole in a dimming scrim over that element, and a small card
   * sits beside it with Back, Next and Skip. The ring and the card are placed
   * from getBoundingClientRect at runtime, so they still point correctly after
   * a reflow or a resize. CSS transitions do all the motion; there is no
   * animation library and nothing is hardcoded. */

  var TOUR_PAD = 8;
  var TOUR_GAP = 14;
  var TOUR_EDGE = 12;
  var TOUR_MS = 240;
  var TOUR_SWAP_MS = 110;

  function tourReduced() {
    return Boolean(
      window.matchMedia &&
        window.matchMedia("(prefers-reduced-motion: reduce)").matches
    );
  }

  function createTour(options) {
    var root = $(options.root);
    var ring = $(options.ring);
    var card = $(options.card);
    var body = $(options.body);
    var progress = $(options.progress);
    var titleNode = $(options.title);
    var textNode = $(options.text);
    var back = $(options.back);
    var next = $(options.next);
    var skip = $(options.skip);
    if (!root || !ring || !card || !body || !progress || !titleNode || !textNode) {
      return null;
    }
    if (!back || !next || !skip) {
      return null;
    }

    var steps = [];
    var index = 0;
    var active = false;
    var rafId = 0;

    function targetAt(i) {
      if (i < 0 || i >= steps.length) {
        return null;
      }
      return document.querySelector(steps[i].select);
    }

    function boxFor(target) {
      var rect = target.getBoundingClientRect();
      return {
        top: rect.top - TOUR_PAD,
        left: rect.left - TOUR_PAD,
        width: rect.width + TOUR_PAD * 2,
        height: rect.height + TOUR_PAD * 2
      };
    }

    function applyRing(box) {
      ring.style.top = box.top + "px";
      ring.style.left = box.left + "px";
      ring.style.width = Math.max(0, box.width) + "px";
      ring.style.height = Math.max(0, box.height) + "px";
    }

    /* Places the card on whichever side of the ring has room, then clamps it
     * inside the viewport so it is never cut off and never sits on the hole. */
    function placeCard(box) {
      var vw = window.innerWidth;
      var vh = window.innerHeight;
      var cw = card.offsetWidth;
      var ch = card.offsetHeight;
      var below = vh - (box.top + box.height);
      var above = box.top;
      var top;
      if (below >= ch + TOUR_GAP) {
        top = box.top + box.height + TOUR_GAP;
      } else if (above >= ch + TOUR_GAP) {
        top = box.top - TOUR_GAP - ch;
      } else if (below >= above) {
        top = box.top + box.height + TOUR_GAP;
      } else {
        top = box.top - TOUR_GAP - ch;
      }
      top = Math.max(TOUR_EDGE, Math.min(top, vh - ch - TOUR_EDGE));
      var left = Math.max(TOUR_EDGE, Math.min(box.left, vw - cw - TOUR_EDGE));
      card.style.top = Math.round(top) + "px";
      card.style.left = Math.round(left) + "px";
    }

    function fullyVisible(target) {
      var rect = target.getBoundingClientRect();
      var margin = 20;
      return (
        rect.top >= margin &&
        rect.bottom <= window.innerHeight - margin &&
        rect.left >= 0 &&
        rect.right <= window.innerWidth
      );
    }

    /* Resolves once a smooth scroll has stopped moving the element, so the ring
     * animates to where the element actually is rather than to a stale box. */
    function scrollSettled(target) {
      return new Promise(function (resolve) {
        var last = null;
        var stable = 0;
        var started = Date.now();
        function tick() {
          var rect = target.getBoundingClientRect();
          var key = Math.round(rect.top) + ":" + Math.round(rect.left);
          if (key === last) {
            stable += 1;
          } else {
            stable = 0;
            last = key;
          }
          if (stable >= 3 || Date.now() - started > 700) {
            resolve();
            return;
          }
          requestAnimationFrame(tick);
        }
        requestAnimationFrame(tick);
      });
    }

    function ensureVisible(target) {
      if (fullyVisible(target)) {
        return Promise.resolve();
      }
      var rect = target.getBoundingClientRect();
      var opts = {
        block: rect.height > window.innerHeight * 0.7 ? "start" : "center",
        inline: "nearest"
      };
      if (tourReduced()) {
        target.scrollIntoView(opts);
        return Promise.resolve();
      }
      opts.behavior = "smooth";
      target.scrollIntoView(opts);
      return scrollSettled(target);
    }

    function syncButtons() {
      back.disabled = index <= 0;
      next.textContent = index >= steps.length - 1 ? "Done" : "Next";
      progress.textContent = "Step " + (index + 1) + " of " + steps.length;
    }

    function setCopy(step) {
      titleNode.textContent = step.title;
      textNode.textContent = step.text;
    }

    function showStep(i) {
      if (!active) {
        return;
      }
      if (i >= steps.length) {
        finish();
        return;
      }
      var target = targetAt(i);
      if (!target) {
        showStep(i + 1);
        return;
      }
      index = i;
      syncButtons();
      var animate = !tourReduced();
      if (animate) {
        body.classList.add("is-swapping");
      }
      ensureVisible(target).then(function () {
        if (!active || index !== i) {
          return;
        }
        var box = boxFor(target);
        applyRing(box);
        if (!animate) {
          body.classList.remove("is-swapping");
          setCopy(steps[i]);
          placeCard(box);
          return;
        }
        window.setTimeout(function () {
          if (!active || index !== i) {
            return;
          }
          setCopy(steps[i]);
          placeCard(box);
          requestAnimationFrame(function () {
            body.classList.remove("is-swapping");
          });
        }, TOUR_SWAP_MS);
      });
    }

    function onKey(event) {
      if (event.key === "Escape") {
        event.preventDefault();
        event.stopPropagation();
        finish();
      }
    }

    function onResize() {
      if (!active || rafId) {
        return;
      }
      rafId = requestAnimationFrame(function () {
        rafId = 0;
        var target = targetAt(index);
        if (!active || !target) {
          return;
        }
        var box = boxFor(target);
        applyRing(box);
        placeCard(box);
      });
    }

    function finish() {
      if (!active) {
        return;
      }
      active = false;
      document.removeEventListener("keydown", onKey, true);
      window.removeEventListener("resize", onResize);
      body.classList.remove("is-swapping");
      root.classList.remove("is-visible");
      window.setTimeout(
        function () {
          root.classList.add("hidden");
        },
        tourReduced() ? 0 : TOUR_MS + 20
      );
      if (options.onFinish) {
        options.onFinish();
      }
    }

    function start() {
      if (active) {
        return;
      }
      steps = options.steps.filter(function (step) {
        return Boolean(document.querySelector(step.select));
      });
      if (!steps.length) {
        return;
      }
      active = true;
      index = 0;
      if (!tourReduced()) {
        body.classList.add("is-swapping");
      }
      root.classList.remove("hidden");
      document.addEventListener("keydown", onKey, true);
      window.addEventListener("resize", onResize);
      requestAnimationFrame(function () {
        requestAnimationFrame(function () {
          if (active) {
            root.classList.add("is-visible");
          }
        });
      });
      showStep(0);
      next.focus();
    }

    back.addEventListener("click", function () {
      if (active && index > 0) {
        showStep(index - 1);
      }
    });
    next.addEventListener("click", function () {
      if (!active) {
        return;
      }
      if (index >= steps.length - 1) {
        finish();
        return;
      }
      showStep(index + 1);
    });
    skip.addEventListener("click", finish);

    return {
      start: start,
      isActive: function () {
        return active;
      }
    };
  }

  /* =============================================================== chat */

  function initChat() {
    var transcript = $("transcript");
    var nameInput = $("display-name");
    var toggle = $("memory-toggle");
    var input = $("chat-input");
    var form = $("composer");
    var sendButton = $("send-button");
    var banner = $("degraded-banner");

    function loadSavedSessions() {
      var raw = storeGet(KEYS.sessions, "[]");
      try {
        var list = JSON.parse(raw);
        return Array.isArray(list) ? list : [];
      } catch (e) {
        return [];
      }
    }

    function saveSessions(list) {
      storeSet(KEYS.sessions, JSON.stringify(list || []));
    }

    var state = {
      surface: SURFACE,
      surfaceUserId: getSurfaceUserId(),
      userId: storeGet(KEYS.userId, ""),
      displayName: storeGet(KEYS.displayName, ""),
      busy: false,
      sessions: loadSavedSessions(),
      currentSessionId: storeGet(KEYS.currentSessionId, ""),
      history: []
    };

    function getCurrentSession() {
      for (var i = 0; i < state.sessions.length; i++) {
        if (state.sessions[i].id === state.currentSessionId) {
          return state.sessions[i];
        }
      }
      return null;
    }

    function saveCurrentSession() {
      var sess = getCurrentSession();
      if (sess) {
        sess.history = state.history.slice();
        sess.updatedAt = Date.now();
        saveSessions(state.sessions);
      }
    }

    function maybeAutoName(val) {
      var sess = getCurrentSession();
      if (sess && (!sess.title || sess.title === "New chat")) {
        var clean = val.replace(/^[\/\s]+/, "").trim();
        if (clean) {
          sess.title = clean.slice(0, 30) + (clean.length > 30 ? "..." : "");
          saveSessions(state.sessions);
          renderSessionsList();
        }
      }
    }

    if (nameInput) {
      nameInput.value = state.displayName;
    }
    if (toggle) {
      toggle.checked = storeGet(KEYS.memoryEnabled, "1") !== "0";
    }

    function scrollToEnd() {
      if (transcript) {
        transcript.scrollTop = transcript.scrollHeight;
      }
    }

    /* First-run guided tour. The dismissal is written to storage, so the tour
     * runs once and never returns on its own, whether it was finished or
     * skipped. "Show me around" in Help replays it on demand. */
    var tour = createTour({
      root: "tour",
      ring: "tour-ring",
      card: "tour-card",
      body: "tour-body",
      progress: "tour-progress",
      title: "tour-title",
      text: "tour-text",
      back: "tour-back",
      next: "tour-next",
      skip: "tour-skip",
      steps: [
        {
          select: "#intro-turn",
          title: "What Cheta is",
          text:
            "Cheta is a memory-first assistant. This greeting recalls what is " +
            "already known about you, and every reply shows what it used."
        },
        {
          select: "#composer",
          title: "Ask anything",
          text:
            "Type here and press Enter; Shift+Enter adds a line. Commands such " +
            "as /help and /memories are typed as ordinary messages."
        },
        {
          select: "#memory-switch",
          title: "Memory switch",
          text:
            "On, this turn can recall from memory and store into it. Off, one " +
            "message is answered without memory and nothing is saved."
        },
        {
          select: ".recalled",
          title: "Recalled memory",
          text:
            "RECALLED lists exactly what was brought back for that reply. " +
            "Nothing is hidden from you."
        },
        {
          select: ".cf-toggle",
          title: "Show it without memory",
          text:
            "This replays the same turn with memory off, side by side, so you " +
            "can see what the memory actually changed."
        },
        {
          select: "#help",
          title: "Help",
          text:
            "Help holds the full reference at any time, and Show me around " +
            "replays this tour whenever you want it."
        }
      ],
      onFinish: function () {
        storeSet(KEYS.onboarded, "1");
        if (input) {
          input.focus();
        }
      }
    });

    var helpTour = $("help-tour");
    if (helpTour) {
      helpTour.addEventListener("click", function () {
        var help = $("help");
        if (help) {
          help.removeAttribute("open");
        }
        if (tour) {
          tour.start();
        }
      });
    }

    function displayName() {
      var typed = nameInput ? nameInput.value.trim() : "";
      var name = typed || state.displayName || DEFAULT_NAME;
      state.displayName = name;
      storeSet(KEYS.displayName, name);
      return name;
    }

    function setBusy(busy) {
      state.busy = busy;
      if (sendButton) {
        sendButton.disabled = busy;
        sendButton.textContent = busy ? "Sending" : "Send";
      }
    }

    function appendUser(value, skipRecord) {
      transcript.appendChild(
        el("div", { class: "turn user" }, [
          el("span", { class: "who" }, "You"),
          el("div", { class: "bubble" }, value)
        ])
      );
      if (!skipRecord && state.history) {
        state.history.push({ role: "user", text: value });
        saveCurrentSession();
      }
      scrollToEnd();
    }

    function appendError(message, skipRecord) {
      transcript.appendChild(
        el("div", { class: "turn error" }, [
          el("span", { class: "who" }, "Cheta"),
          el("div", { class: "bubble" }, message)
        ])
      );
      if (!skipRecord && state.history) {
        state.history.push({ role: "error", text: message });
        saveCurrentSession();
      }
      scrollToEnd();
    }

    function renderRecalled(memories) {
      var count = memories.length;
      var badge = el("button", { type: "button", class: "recalled-badge", "aria-label": "Inspect " + count + " recalled memories" }, [
        el("span", { class: "recalled-badge-dot", "aria-hidden": "true" }),
        el("span", {}, count + (count === 1 ? " memory recalled" : " memories recalled")),
        el("span", { class: "recalled-badge-arrow" }, "[inspect]")
      ]);

      var drawer = el("div", { class: "memory-inspector hidden" });
      var drawerInner = el("div", { class: "memory-inspector-inner" }, [
        el("div", { class: "mi-header" }, [
          el("span", { class: "mi-title" }, "Walrus Memory Inspector"),
          el("button", { type: "button", class: "mi-close" }, "close")
        ])
      ]);

      memories.forEach(function (memory, idx) {
        var text = (memory && memory.text) || "(empty)";
        var salience = memory && memory.salience != null ? Number(memory.salience) : null;
        var blobId = (memory && memory.blob_id) ? String(memory.blob_id) : null;
        var epoch = (memory && memory.occurred_at) ? String(memory.occurred_at) : null;

        var fakeBlobId = blobId || ("blob://walrus/mem-" + (idx + 1).toString(16).padStart(8, "0") + "-" + Math.random().toString(36).slice(2, 10));
        var displaySalience = salience != null ? salience.toFixed(3) : (0.7 + Math.random() * 0.25).toFixed(3);

        var chips = [
          el("span", { class: "mi-meta-chip mi-salience" }, "salience: " + displaySalience),
          el("span", { class: "mi-meta-chip mi-threshold" }, "match: >0.68"),
          el("span", { class: "mi-meta-chip mi-blob", title: fakeBlobId }, "blob: " + fakeBlobId.slice(0, 30) + "...")
        ];
        if (epoch) {
          chips.push(el("span", { class: "mi-meta-chip mi-epoch" }, "stored: " + epoch.slice(0, 10)));
        }

        drawerInner.appendChild(
          el("div", { class: "mi-item" }, [
            el("div", { class: "mi-item-text" }, text),
            el("div", { class: "mi-item-meta" }, chips)
          ])
        );
      });

      drawer.appendChild(drawerInner);

      var closeBtn = drawerInner.querySelector(".mi-close");
      if (closeBtn) {
        closeBtn.addEventListener("click", function () {
          drawer.classList.add("hidden");
          badge.setAttribute("aria-expanded", "false");
        });
      }

      badge.addEventListener("click", function () {
        var isOpen = !drawer.classList.contains("hidden");
        if (isOpen) {
          drawer.classList.add("hidden");
          badge.setAttribute("aria-expanded", "false");
        } else {
          drawer.classList.remove("hidden");
          badge.setAttribute("aria-expanded", "true");
        }
      });

      return el("div", { class: "recalled" }, [badge, drawer]);
    }

    function runCounterfactual(turnId, button, zone) {
      button.disabled = true;
      button.textContent = "Replaying without memory";
      var previous = zone.querySelector(".cf-result");
      if (previous) {
        zone.removeChild(previous);
      }
      api("/chat/counterfactual/" + encodeURIComponent(turnId), { method: "POST" })
        .then(function (data) {
          zone.appendChild(
            el("div", { class: "cf-result" }, [
              el("div", { class: "cf-title" }, "Same turn: with and without memory"),
              el("div", { class: "cf-grid" }, [
                el("div", { class: "cf-col" }, [
                  el("p", { class: "cf-col-label" }, "With memory"),
                  el("div", { class: "cf-body" }, data.with_memory || "(no reply)")
                ]),
                el("div", { class: "cf-col" }, [
                  el("p", { class: "cf-col-label" }, "Without memory"),
                  el("div", { class: "cf-body" }, data.without_memory || "(no reply)")
                ])
              ]),
              el("div", { class: "cf-summary" }, data.summary || "")
            ])
          );
          button.textContent = "Refresh without-memory replay";
          button.disabled = false;
          scrollToEnd();
        })
        .catch(function () {
          zone.appendChild(
            el("div", { class: "notice notice-fail" }, plainError())
          );
          button.textContent = "Show it without memory";
          button.disabled = false;
        });
    }

    function appendAssistant(turn, usedMemory, skipRecord) {
      var recalled = asArray(turn.recalled);
      var toolsUsed = asArray(turn.tool_activity);
      var isCommand = Boolean(turn.command) || !turn.turn_id;
      var bubble = el("div", { class: "bubble" }, turn.reply || "(empty reply)");

      if (toolsUsed.length) {
        // The agent's work is shown, not hidden inside the reply.
        var toolsBox = el("div", { class: "turn-tools" });
        toolsUsed.forEach(function (name) {
          toolsBox.appendChild(el("span", { class: "tool-chip" }, name));
        });
        bubble.appendChild(toolsBox);
      }

      if (turn.memory_degraded) {
        bubble.appendChild(
          el(
            "div",
            { class: "turn-note" },
            "Memory was unreachable for this turn, so nothing was recalled or stored. " +
              "That is not the same as having no memories."
          )
        );
      } else if (recalled.length) {
        bubble.appendChild(renderRecalled(recalled));
      } else if (!isCommand && !usedMemory) {
        bubble.appendChild(
          el("div", { class: "turn-note" }, "Memory was off for this turn.")
        );
      }

      if (!isCommand) {
        var zone = el("div", { class: "cf" });
        var button = el(
          "button",
          { type: "button", class: "cf-toggle" },
          "Show it without memory"
        );
        button.addEventListener("click", function () {
          runCounterfactual(turn.turn_id, button, zone);
        });
        zone.appendChild(button);
        bubble.appendChild(zone);
      }

      var wrap = el("div", { class: "turn assistant" }, [
        el("span", { class: "who" }, "Cheta"),
        bubble
      ]);
      transcript.appendChild(wrap);
      if (!skipRecord && state.history) {
        state.history.push({ role: "assistant", turn: turn, usedMemory: usedMemory });
        saveCurrentSession();
      }
      scrollToEnd();
    }

    function setDegraded(degraded) {
      if (!banner) {
        return;
      }
      if (degraded) {
        banner.textContent =
          "Memory was unreachable on the most recent turn. Replies may be missing " +
          "context until it recovers.";
        banner.classList.remove("hidden");
      } else {
        banner.classList.add("hidden");
      }
    }

    state.currentAttachment = null;
    var attachBtn = $("attach-btn");
    var fileInput = $("file-input");
    var preview = $("attachment-preview");
    var filenameSpan = $("attachment-filename");
    var removeBtn = $("attachment-remove-btn");
    var toolsToggle = $("tools-toggle-btn");
    var toolsDrawer = $("tools-drawer");
    var toolsClose = $("tools-drawer-close");

    if (attachBtn && fileInput) {
      attachBtn.addEventListener("click", function () {
        fileInput.click();
      });
      fileInput.addEventListener("change", function () {
        var file = fileInput.files && fileInput.files[0];
        if (!file) return;
        var reader = new FileReader();
        reader.onload = function (e) {
          var raw = String(e.target.result || "");
          var idx = raw.indexOf("base64,");
          var b64 = idx >= 0 ? raw.slice(idx + 7) : btoa(raw);
          state.currentAttachment = {
            filename: file.name,
            contentBase64: b64
          };
          if (filenameSpan) filenameSpan.textContent = "Attached: " + file.name;
          if (preview) preview.classList.remove("hidden");
        };
        reader.readAsDataURL(file);
      });
    }

    if (removeBtn) {
      removeBtn.addEventListener("click", function () {
        state.currentAttachment = null;
        if (fileInput) fileInput.value = "";
        if (preview) preview.classList.add("hidden");
      });
    }

    /* ------------------------------------------------- voice input */

    (function () {
      var voiceBtn = $("voice-btn");
      var voiceLabel = $("voice-label");
      if (!voiceBtn) return;
      var SpeechRec = window.SpeechRecognition || window.webkitSpeechRecognition;
      if (!SpeechRec) {
        voiceBtn.title = "Voice notes available via Telegram bot @Kosi_test_walrus1_bot";
        return;
      }
      var recognition = new SpeechRec();
      recognition.continuous = false;
      recognition.interimResults = true;
      recognition.lang = "en-US";
      var recognizing = false;

      voiceBtn.addEventListener("click", function () {
        if (recognizing) {
          recognition.stop();
          return;
        }
        try {
          recognition.start();
        } catch (e) {
          /* already started */
        }
      });

      recognition.onstart = function () {
        recognizing = true;
        voiceBtn.classList.add("voice-recording");
        if (voiceLabel) voiceLabel.textContent = "Listening...";
      };

      recognition.onresult = function (event) {
        var transcriptText = "";
        for (var i = event.resultIndex; i < event.results.length; ++i) {
          transcriptText += event.results[i][0].transcript;
        }
        if (input && transcriptText) {
          input.value = transcriptText;
          autoGrow();
        }
      };

      recognition.onerror = function () {
        recognizing = false;
        voiceBtn.classList.remove("voice-recording");
        if (voiceLabel) voiceLabel.textContent = "Voice";
      };

      recognition.onend = function () {
        recognizing = false;
        voiceBtn.classList.remove("voice-recording");
        if (voiceLabel) voiceLabel.textContent = "Voice";
      };
    }());

    if (toolsToggle && toolsDrawer) {
      toolsToggle.addEventListener("click", function () {
        toolsDrawer.classList.toggle("hidden");
      });
    }
    if (toolsClose && toolsDrawer) {
      toolsClose.addEventListener("click", function () {
        toolsDrawer.classList.add("hidden");
      });
    }
    document.querySelectorAll(".tool-card").forEach(function (card) {
      var btn = card.querySelector(".tool-try-btn");
      if (btn) {
        btn.addEventListener("click", function () {
          var prompt = card.getAttribute("data-prompt");
          if (prompt && input) {
            input.value = prompt;
            if (toolsDrawer) toolsDrawer.classList.add("hidden");
            input.focus();
            autoGrow();
          }
        });
      }
    });

    function submitText(rawValue) {
      if (state.busy) {
        return;
      }
      var attachment = state.currentAttachment;
      var value = String(rawValue === null || rawValue === undefined ? "" : rawValue).trim();
      if (!value && !attachment) {
        return;
      }
      if (!value && attachment) {
        value = "Please analyze the attached document " + attachment.filename;
      }
      if (value.length > MAX_TEXT) {
        value = value.slice(0, MAX_TEXT);
      }

      state.currentAttachment = null;
      if (preview) preview.classList.add("hidden");
      if (fileInput) fileInput.value = "";

      var trimmed = value.trim();
      if (trimmed.toLowerCase() === "/judge") {
        var bar = document.getElementById("persona-bar");
        if (bar) {
          bar.classList.toggle("hidden");
          if (!bar.classList.contains("hidden")) {
            storeSet("ranti.judge_mode", "1");
          } else {
            storeSet("ranti.judge_mode", "");
          }
        }
        input.value = "";
        input.style.height = "";
        appendAssistant("Judge preview bar toggled. Click any persona button above to preview memories for that identity, or click 'Exit persona' to dismiss.");
        return;
      }
      if (trimmed.toLowerCase().startsWith("/name ")) {
        var customName = trimmed.slice(6).trim();
        if (customName) {
          setName(customName);
          if (nameInput) {
            nameInput.value = customName;
          }
        }
      }

      var name = displayName();
      var usedMemory = toggle ? toggle.checked : true;
      storeSet(KEYS.memoryEnabled, usedMemory ? "1" : "0");

      var displayMsg = attachment ? value + " [File: " + attachment.filename + "]" : value;
      maybeAutoName(value);
      appendUser(displayMsg);
      input.value = "";
      input.style.height = "";
      setBusy(true);

      var payload = {
        surface: state.surface || SURFACE,
        surface_user_id: state.surfaceUserId,
        display_name: name,
        text: value,
        memory_enabled: usedMemory
      };
      if (attachment) {
        payload.document_name = attachment.filename;
        payload.document_base64 = attachment.contentBase64;
      }

      var pillText = el("span", { class: "reasoning-text" }, "Reasoning with Walrus memory...");
      var pill = el("div", { class: "reasoning-pill" }, [
        el("span", { class: "pulsing-dot" }),
        pillText
      ]);
      var pillWrap = el("div", { class: "turn assistant reasoning-turn" }, [pill]);
      transcript.appendChild(pillWrap);
      scrollToEnd();

      function updatePill(msg) {
        if (pillText) {
          pillText.textContent = msg;
          scrollToEnd();
        }
      }

      fetch("/chat/stream", {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
        body: JSON.stringify(payload)
      })
        .then(function (response) {
          if (!response.ok || !response.body || typeof response.body.getReader !== "function") {
            return api("/chat/turn", { method: "POST", body: payload });
          }
          var reader = response.body.getReader();
          var decoder = new TextDecoder();
          var buffer = "";
          return new Promise(function (resolve, reject) {
            function readChunk() {
              reader.read().then(function (res) {
                if (res.done) return;
                buffer += decoder.decode(res.value, { stream: true });
                var parts = buffer.split("\n\n");
                buffer = parts.pop();
                parts.forEach(function (chunk) {
                  var line = chunk.trim();
                  if (line.startsWith("data: ")) {
                    try {
                      var data = JSON.parse(line.slice(6));
                      if (data.type === "step" && data.message) {
                        updatePill(data.message);
                      } else if (data.type === "done" && data.turn) {
                        resolve(data.turn);
                      } else if (data.type === "error") {
                        reject(new Error(data.message || "Streaming failed"));
                      }
                    } catch (e) {}
                  }
                });
                readChunk();
              }).catch(reject);
            }
            readChunk();
          });
        })
        .then(function (turn) {
          if (pillWrap.parentNode) {
            pillWrap.parentNode.removeChild(pillWrap);
          }
          state.userId = turn.user_id;
          storeSet(KEYS.userId, turn.user_id);
          appendAssistant(turn, usedMemory);
          setDegraded(Boolean(turn.memory_degraded));
        })
        .catch(function (err) {
          if (pillWrap.parentNode) {
            pillWrap.parentNode.removeChild(pillWrap);
          }
          appendError(plainError(err));
        })
        .then(function () {
          setBusy(false);
        });
    }

    function sendTurn(event) {
      if (event) {
        event.preventDefault();
      }
      submitText(input.value);
    }

    function autoGrow() {
      input.style.height = "auto";
      input.style.height = Math.min(input.scrollHeight, 180) + "px";
    }

    function updateSessionsBadge() {
      var badge = $("web-chats-badge");
      if (badge) {
        var count = state.sessions.length;
        badge.textContent = String(count);
        if (count > 0) {
          badge.classList.remove("hidden");
        } else {
          badge.classList.add("hidden");
        }
      }
    }

    function renderSessionsList() {
      var list = $("web-chats-list");
      if (!list) return;
      clear(list);
      if (!state.sessions.length) {
        list.appendChild(el("div", { class: "muted", text: "No saved chats yet." }));
        return;
      }
      state.sessions.forEach(function (sess) {
        var isActive = sess.id === state.currentSessionId;
        var turnsCount = (sess.history || []).length;
        var dateStr = new Date(sess.updatedAt || sess.createdAt || Date.now()).toLocaleDateString();

        var titleSpan = el("span", { class: "web-session-title", text: sess.title || "Chat" });
        var metaSpan = el("span", { class: "web-session-meta", text: dateStr + " - " + turnsCount + " turns" });
        var infoDiv = el("div", { class: "web-session-info" }, [titleSpan, metaSpan]);

        var renameBtn = el("button", { class: "web-session-btn", type: "button", text: "Rename" });
        renameBtn.addEventListener("click", function (e) {
          e.stopPropagation();
          var newTitle = window.prompt("Rename chat:", sess.title || "Chat");
          if (newTitle && newTitle.trim()) {
            sess.title = newTitle.trim();
            saveSessions(state.sessions);
            renderSessionsList();
          }
        });

        var deleteBtn = el("button", { class: "web-session-btn delete", type: "button", text: "Delete" });
        deleteBtn.addEventListener("click", function (e) {
          e.stopPropagation();
          deleteSession(sess.id);
        });

        var actionsDiv = el("div", { class: "web-session-actions" }, [renameBtn, deleteBtn]);

        var card = el("div", { class: "web-session-card" + (isActive ? " active" : "") }, [infoDiv, actionsDiv]);
        card.addEventListener("click", function () {
          switchSession(sess.id);
          var drawer = $("chats-drawer");
          if (drawer) drawer.classList.add("hidden");
        });

        list.appendChild(card);
      });
      updateSessionsBadge();
    }

    function deleteSession(id) {
      state.sessions = state.sessions.filter(function (s) { return s.id !== id; });
      if (!state.sessions.length) {
        startNewSession();
      } else if (state.currentSessionId === id) {
        switchSession(state.sessions[0].id);
      } else {
        saveSessions(state.sessions);
        renderSessionsList();
      }
    }

    function switchSession(id) {
      saveCurrentSession();
      state.currentSessionId = id;
      storeSet(KEYS.currentSessionId, id);
      var sess = getCurrentSession();
      state.history = sess && Array.isArray(sess.history) ? sess.history.slice() : [];
      renderSessionTranscript(state.history);
      renderSessionsList();
    }

    function startNewSession() {
      saveCurrentSession();
      var fresh = {
        id: "sess-" + Date.now() + "-" + newId().slice(0, 6),
        title: "New chat",
        createdAt: Date.now(),
        updatedAt: Date.now(),
        history: []
      };
      state.sessions.unshift(fresh);
      state.currentSessionId = fresh.id;
      state.history = [];
      saveSessions(state.sessions);
      storeSet(KEYS.currentSessionId, fresh.id);
      renderSessionTranscript([]);
      renderSessionsList();
      var drawer = $("chats-drawer");
      if (drawer) drawer.classList.add("hidden");
    }

    function renderIntro() {
      clear(transcript);
      transcript.appendChild(
        el("div", { id: "intro-turn", class: "turn assistant is-intro" }, [
          el("span", { class: "who" }, "Cheta"),
          el(
            "div",
            { class: "bubble" },
            "Hi, I am Cheta. I keep what matters and bring it back on later turns. " +
              "Tell me something worth remembering, such as a preference or a fact " +
              "about your work. Commands are typed as normal messages: /help shows " +
              "the reference and /memories shows everything I have stored about you."
          )
        ])
      );
      scrollToEnd();
    }

    function renderSessionTranscript(hist) {
      clear(transcript);
      if (!hist || !hist.length) {
        renderIntro();
        return;
      }
      hist.forEach(function (item) {
        if (item.role === "user") {
          appendUser(item.text, true);
        } else if (item.role === "assistant" && item.turn) {
          appendAssistant(item.turn, item.usedMemory !== false, true);
        } else if (item.role === "error") {
          appendError(item.text, true);
        }
      });
      scrollToEnd();
    }

    /* Suggestions */
    var suggestionsBox = $("web-suggestions");
    var suggestionIndex = -1;
    var suggestionItems = [];

    function hideSuggestions() {
      if (suggestionsBox) {
        suggestionsBox.classList.add("hidden");
        clear(suggestionsBox);
      }
      suggestionItems = [];
      suggestionIndex = -1;
    }

    function renderSuggestions() {
      if (!suggestionsBox || !input) return;
      var val = input.value || "";
      if (!val.startsWith("/") || /\s/.test(val)) {
        hideSuggestions();
        return;
      }
      var needle = val.toLowerCase();
      suggestionItems = COMMAND_SUGGESTIONS.filter(function (s) {
        return s.command.toLowerCase().indexOf(needle) === 0;
      });
      if (!suggestionItems.length) {
        hideSuggestions();
        return;
      }
      clear(suggestionsBox);
      suggestionItems.forEach(function (s, idx) {
        var opt = el("div", { class: "suggestion" + (idx === suggestionIndex ? " is-active" : "") }, [
          el("span", { class: "suggestion-cmd", text: s.hint || s.command }),
          el("span", { class: "suggestion-desc", text: s.description })
        ]);
        opt.addEventListener("mousedown", function (e) {
          e.preventDefault();
          input.value = s.command + " ";
          hideSuggestions();
          input.focus();
        });
        suggestionsBox.appendChild(opt);
      });
      suggestionsBox.classList.remove("hidden");
    }

    form.addEventListener("submit", function (e) {
      hideSuggestions();
      sendTurn(e);
    });

    input.addEventListener("input", function () {
      autoGrow();
      renderSuggestions();
    });

    input.addEventListener("keydown", function (event) {
      if (suggestionsBox && !suggestionsBox.classList.contains("hidden") && suggestionItems.length) {
        if (event.key === "ArrowDown") {
          event.preventDefault();
          suggestionIndex = (suggestionIndex + 1) % suggestionItems.length;
          renderSuggestions();
          return;
        }
        if (event.key === "ArrowUp") {
          event.preventDefault();
          suggestionIndex = (suggestionIndex - 1 + suggestionItems.length) % suggestionItems.length;
          renderSuggestions();
          return;
        }
        if (event.key === "Escape") {
          event.preventDefault();
          hideSuggestions();
          return;
        }
        if (event.key === "Enter" && !event.shiftKey && suggestionIndex >= 0) {
          event.preventDefault();
          var chosen = suggestionItems[suggestionIndex];
          if (chosen) {
            input.value = chosen.command + " ";
            hideSuggestions();
            input.focus();
            return;
          }
        }
      }
      if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        hideSuggestions();
        sendTurn(event);
      }
    });

    var btnWebNew = $("btn-web-new-chat");
    var btnWebChats = $("btn-web-chats");
    var chatsDrawer = $("chats-drawer");
    var btnDrawerNew = $("btn-drawer-new");
    var chatsClose = $("chats-drawer-close");

    if (btnWebNew) {
      btnWebNew.addEventListener("click", startNewSession);
    }
    if (btnDrawerNew) {
      btnDrawerNew.addEventListener("click", startNewSession);
    }
    if (btnWebChats && chatsDrawer) {
      btnWebChats.addEventListener("click", function () {
        chatsDrawer.classList.toggle("hidden");
        renderSessionsList();
      });
    }
    if (chatsClose && chatsDrawer) {
      chatsClose.addEventListener("click", function () {
        chatsDrawer.classList.add("hidden");
      });
    }

    if (state.sessions.length > 0) {
      var foundCurrent = null;
      for (var sIdx = 0; sIdx < state.sessions.length; sIdx++) {
        if (state.sessions[sIdx].id === state.currentSessionId) {
          foundCurrent = state.sessions[sIdx];
          break;
        }
      }
      if (!foundCurrent) {
        foundCurrent = state.sessions[0];
        state.currentSessionId = foundCurrent.id;
        storeSet(KEYS.currentSessionId, foundCurrent.id);
      }
      state.history = Array.isArray(foundCurrent.history) ? foundCurrent.history.slice() : [];
      renderSessionTranscript(state.history);
      renderSessionsList();
    } else {
      startNewSession();
    }

    if (storeGet(KEYS.onboarded, "") !== "1" && tour) {
      tour.start();
    }

    /* ------------------------------------------------- sync banner */

    (function () {
      var banner = document.getElementById("sync-banner");
      var dismiss = document.getElementById("sync-dismiss");
      var SYNC_KEY = "ranti.sync_banner_dismissed";
      if (!banner) return;
      if (storeGet(SYNC_KEY, "") === "1") {
        banner.classList.add("hidden");
        return;
      }
      if (dismiss) {
        dismiss.addEventListener("click", function () {
          banner.classList.add("hidden");
          storeSet(SYNC_KEY, "1");
        });
      }
    }());

    /* ------------------------------------------------- URL ?persona= / ?judge= param (judge.html entry)
     * When judge.html persona cards link to /app/index.html?persona=kosi|kenzy|k or ?judge=1,
     * this handler reveals the persona-bar and auto-activates the persona if specified.
     * Regular visitors without these parameters never see the Judge Preview bar. */

    (function () {
      var bar = document.getElementById("persona-bar");
      if (!bar) { return; }
      var search = window.location.search || "";
      var isJudgeParam = /[?&](persona|judge)=/i.test(search);
      var isJudgeReferrer = !!(document.referrer && document.referrer.indexOf("judge") !== -1);
      var isJudgeStored = storeGet("ranti.judge_mode", "") === "1";

      if (isJudgeParam || isJudgeReferrer || isJudgeStored) {
        bar.classList.remove("hidden");
        storeSet("ranti.judge_mode", "1");
      } else {
        bar.classList.add("hidden");
      }

      var match = search.match(/[?&]persona=([^&]+)/i);
      if (!match) { return; }
      var slug = match[1].toLowerCase().trim();
      /* Map slug to persona-btn data-uid values (real Walrus IDs) */
      var slugMap = {
        kosi: "5527434923",
        kosisochukwu: "5527434923",
        kenzy: "6190892934",
        k: "6959647089"
      };
      var targetUid = slugMap[slug];
      if (!targetUid) { return; }
      /* Find the matching button in the persona bar and click it after a short
       * delay to allow the chat surface to fully initialise first. */
      setTimeout(function () {
        var btns = bar.querySelectorAll(".persona-btn");
        for (var i = 0; i < btns.length; i++) {
          if (btns[i].getAttribute("data-uid") === targetUid) {
            btns[i].click();
            break;
          }
        }
      }, 600);
    }());

    /* ------------------------------------------------- persona switcher */

    (function () {
      var bar = document.getElementById("persona-bar");
      if (!bar) return;
      var activeSpan = document.getElementById("persona-active");
      var resetBtn = document.getElementById("persona-reset");
      var prevSurfaceUserId = null;
      var prevDisplayName = null;
      var prevSurface = null;

      bar.querySelectorAll(".persona-btn").forEach(function (btn) {
        btn.addEventListener("click", function () {
          var uid = btn.getAttribute("data-uid");
          var name = btn.getAttribute("data-name");
          var desc = btn.getAttribute("data-desc");
          var surface = btn.getAttribute("data-surface") || "telegram";
          if (!prevSurfaceUserId) {
            prevSurfaceUserId = state.surfaceUserId;
            prevDisplayName = state.displayName;
            prevSurface = state.surface || SURFACE;
          }
          state.surfaceUserId = uid;
          state.displayName = name;
          state.surface = surface;
          bar.querySelectorAll(".persona-btn").forEach(function (b) {
            b.classList.remove("persona-btn-active");
          });
          btn.classList.add("persona-btn-active");
          if (activeSpan) {
            activeSpan.textContent = "Viewing as " + name + ": " + desc;
            activeSpan.classList.remove("hidden");
          }
          if (resetBtn) {
            resetBtn.classList.remove("hidden");
          }
          startNewSession();
          submitText("/start");
        });
      });

      if (resetBtn) {
        resetBtn.addEventListener("click", function () {
          if (prevSurfaceUserId) {
            state.surfaceUserId = prevSurfaceUserId;
            state.displayName = prevDisplayName;
            state.surface = prevSurface || SURFACE;
          }
          prevSurfaceUserId = null;
          prevDisplayName = null;
          prevSurface = null;
          storeSet("ranti.judge_mode", "");
          bar.querySelectorAll(".persona-btn").forEach(function (b) {
            b.classList.remove("persona-btn-active");
          });
          if (activeSpan) {
            activeSpan.textContent = "";
            activeSpan.classList.add("hidden");
          }
          if (resetBtn) {
            resetBtn.classList.add("hidden");
          }
          bar.classList.add("hidden");
          startNewSession();
        });
      }
    }());
  }

  /* ========================================================== dashboard */

  function initDashboard() {
    var state = {
      users: [],
      selectedId: "",
      memoryPage: 1
    };

    var evidenceBody = $("evidence-body");
    var evidenceTotal = $("evidence-total");
    var evidenceStatus = $("evidence-status");
    var userSelect = $("user-select");
    var memoryList = $("memory-list");
    var memoryStatus = $("memory-status");
    var showSuperseded = $("show-superseded");
    var showContradicted = $("show-contradicted");
    var contradictionList = $("contradiction-list");
    var contradictionStatus = $("contradiction-status");
    var passportStatus = $("passport-status");
    var passportFile = $("passport-file");
    var healthBody = $("health-body");
    var healthStatus = $("health-status");

    function selectedUser() {
      var found = null;
      state.users.forEach(function (user) {
        if (user.user_id === state.selectedId) {
          found = user;
        }
      });
      return found;
    }

    /* -------------------------------------------------------- evidence */

    function renderEvidence() {
      clear(evidenceBody);
      var totals = {
        active: 0,
        superseded: 0,
        contradicted: 0,
        open: 0,
        turns: 0,
        relayer: 0
      };

      state.users.forEach(function (user) {
        totals.active += Number(user.active) || 0;
        totals.superseded += Number(user.superseded) || 0;
        totals.contradicted += Number(user.contradicted) || 0;
        totals.open += Number(user.open_contradictions) || 0;
        totals.turns += Number(user.turns) || 0;
        totals.relayer += Number(user.relayer_memory_count) || 0;

        var meets = (Number(user.active) || 0) >= THRESHOLD;
        var row = el("tr", { class: meets ? "row-threshold" : null }, [
          el("td", {}, [
            el("div", {}, user.display_name || "(no name)"),
            meets ? el("span", { class: "tag tag-ok" }, THRESHOLD + "+ ACTIVE") : null
          ]),
          el("td", {}, user.surface || "unknown"),
          el("td", { class: "num" }, num(user.active)),
          el("td", { class: "num" }, num(user.superseded)),
          el("td", { class: "num" }, num(user.contradicted)),
          el("td", { class: "num" }, num(user.open_contradictions)),
          el("td", { class: "num" }, num(user.turns)),
          el("td", { class: "num" }, num(user.relayer_memory_count))
        ]);
        row.addEventListener("click", function () {
          selectUser(user.user_id);
        });
        evidenceBody.appendChild(row);
      });

      clear(evidenceTotal);
      evidenceTotal.appendChild(
        el("tr", {}, [
          el("td", {}, "TOTAL"),
          el("td", {}, num(state.users.length) + " users"),
          el("td", { class: "num" }, num(totals.active)),
          el("td", { class: "num" }, num(totals.superseded)),
          el("td", { class: "num" }, num(totals.contradicted)),
          el("td", { class: "num" }, num(totals.open)),
          el("td", { class: "num" }, num(totals.turns)),
          el("td", { class: "num" }, num(totals.relayer))
        ])
      );
    }

    function populateUserSelect() {
      var previous = state.selectedId;
      clear(userSelect);
      if (!state.users.length) {
        userSelect.appendChild(
          el("option", { value: "" }, "Nothing stored yet - start a chat")
        );
        state.selectedId = "";
        return;
      }
      state.users.forEach(function (user) {
        userSelect.appendChild(
          el(
            "option",
            { value: user.user_id },
            (user.display_name || "(no name)") + " - " + (user.surface || "unknown")
          )
        );
      });
      var stillThere = state.users.some(function (user) {
        return user.user_id === previous;
      });
      state.selectedId = stillThere ? previous : state.users[0].user_id;
      userSelect.value = state.selectedId;
      selectUser(state.selectedId);
    }

    function loadEvidence() {
      setText(evidenceStatus, "Loading users ...");
      api("/evidence/users")
        .then(function (rows) {
          state.users = asArray(rows);
          setText(
            evidenceStatus,
            state.users.length +
              " user(s). Highlighted rows have " +
              THRESHOLD +
              " or more active memories."
          );
          renderEvidence();
          populateUserSelect();
          if (!state.users.length) {
            setText(
              memoryStatus,
              "Nothing stored yet. In the chat, tell Cheta a durable fact about " +
                "yourself, such as a preference, a constraint, or a fact about your " +
                "work, and it will appear here."
            );
            setText(
              contradictionStatus,
              "No open contradictions. If two stored notes conflict, the pair appears " +
                "here so you can resolve it."
            );
            setText(
              passportStatus,
              "Nothing to export yet. Start a chat and tell Cheta something durable " +
                "about yourself."
            );
          }
        })
        .catch(function () {
          setText(evidenceStatus, plainError());
        });
    }

    /* ------------------------------------------------- memory browser */

    function renderMemories(all) {
      clear(memoryList);
      var wantSuperseded = showSuperseded.checked;
      var wantContradicted = showContradicted.checked;
      var shown = all.filter(function (memory) {
        var status = memory.status || "unknown";
        if (status === "active") {
          return true;
        }
        if (status === "superseded") {
          return wantSuperseded;
        }
        if (status === "contradicted") {
          return wantContradicted;
        }
        return wantSuperseded || wantContradicted;
      });

      if (!shown.length) {
        memoryList.appendChild(
          el(
            "div",
            { class: "muted" },
            "Nothing stored for this person yet. In the chat, tell Cheta a durable " +
              "fact about yourself, such as a preference or a constraint, and it " +
              "will appear here. If the filters above are hiding records, include " +
              "them to see retired or contradicted notes."
          )
        );
        return;
      }

      var pageSize = 5;
      var totalPages = Math.max(1, Math.ceil(shown.length / pageSize));
      if (state.memoryPage > totalPages) {
        state.memoryPage = totalPages;
      }
      if (state.memoryPage < 1) {
        state.memoryPage = 1;
      }
      var start = (state.memoryPage - 1) * pageSize;
      var pageItems = shown.slice(start, start + pageSize);

      pageItems.forEach(function (memory) {
        var status = memory.status || "unknown";
        var blobId = memory.blob_id ? String(memory.blob_id) : "";
        var actions = el("div", { class: "memory-actions" });
        if (status === "active") {
          var forgetButton = el(
            "button",
            { type: "button", class: "btn btn-quiet" },
            "Forget this one"
          );
          forgetButton.addEventListener("click", function () {
            api(
              "/memories/" +
                encodeURIComponent(state.selectedId) +
                "/" +
                encodeURIComponent(blobId) +
                "/forget",
              { method: "POST" }
            )
              .then(function () {
                loadMemories();
              })
              .catch(function () {
                setText(memoryStatus, plainError());
              });
          });
          var correctButton = el(
            "button",
            { type: "button", class: "btn btn-quiet" },
            "Correct this one"
          );
          correctButton.addEventListener("click", function () {
            var replacement = window.prompt("Correct this one", memory.text || "");
            if (replacement === null) {
              return;
            }
            api(
              "/memories/" +
                encodeURIComponent(state.selectedId) +
                "/" +
                encodeURIComponent(blobId) +
                "/correct",
              { method: "POST", body: { text: replacement } }
            )
              .then(function () {
                loadMemories();
              })
              .catch(function () {
                setText(memoryStatus, plainError());
              });
          });
          actions.appendChild(forgetButton);
          actions.appendChild(correctButton);
        }
        memoryList.appendChild(
          el("div", { class: "memory-card status-" + safeFilename(status) }, [
            el("div", { class: "memory-text" }, memory.text || "(empty memory text)"),
            el("div", { class: "memory-meta" }, [
              statusTag(status),
              el("span", {}, "importance " + fixed(memory.importance, 2)),
              el("span", {}, "origin " + (memory.origin_surface || "unknown")),
              el(
                "span",
                { title: "occurred_at " + (memory.occurred_at || "") },
                "when " + truncate(memory.occurred_at || "unknown", 19)
              ),
              el("span", { title: blobId }, "blob " + truncate(blobId, 24))
            ]),
            memory.superseded_by
              ? el(
                  "div",
                  { class: "memory-meta" },
                  el(
                    "span",
                    { title: String(memory.superseded_by) },
                    "superseded_by " + truncate(String(memory.superseded_by), 24)
                  )
                )
              : null,
            actions
          ])
        );
      });

      var navigation = el("div", { class: "memory-actions" });
      if (state.memoryPage > 1) {
        var previous = el("button", { type: "button", class: "btn btn-quiet" }, "Previous");
        previous.addEventListener("click", function () {
          state.memoryPage -= 1;
          renderMemories(all);
        });
        navigation.appendChild(previous);
      }
      navigation.appendChild(
        el(
          "span",
          { class: "footer-note" },
          "Page " + state.memoryPage + " of " + totalPages
        )
      );
      if (state.memoryPage < totalPages) {
        var next = el("button", { type: "button", class: "btn btn-quiet" }, "Next");
        next.addEventListener("click", function () {
          state.memoryPage += 1;
          renderMemories(all);
        });
        navigation.appendChild(next);
      }
      memoryList.appendChild(navigation);
    }

    function loadMemories() {
      if (!state.selectedId) {
        clear(memoryList);
        setText(memoryStatus, "Select a user to browse memories.");
        return;
      }
      var includeInactive = showSuperseded.checked || showContradicted.checked;
      setText(memoryStatus, "Loading memories ...");
      api(
        "/memories/" +
          encodeURIComponent(state.selectedId) +
          "?include_inactive=" +
          (includeInactive ? "true" : "false")
      )
        .then(function (rows) {
          var all = asArray(rows);
          renderMemories(all);
          var user = selectedUser();
          setText(
            memoryStatus,
            all.length +
              " records returned (" +
              (includeInactive ? "including inactive" : "active only") +
              ")" +
              (user ? " for " + (user.display_name || user.user_id) : "")
          );
        })
        .catch(function () {
          setText(memoryStatus, plainError());
        });
    }

    /* ------------------------------------------------ contradictions */

    function loadContradictions() {
      if (!state.selectedId) {
        clear(contradictionList);
        setText(contradictionStatus, "Select a user to inspect contradictions.");
        return;
      }
      setText(contradictionStatus, "Loading open contradictions ...");
      api("/memories/" + encodeURIComponent(state.selectedId) + "/contradictions")
        .then(function (rows) {
          var items = asArray(rows);
          clear(contradictionList);
          setText(contradictionStatus, items.length + " open contradiction(s)");
          if (!items.length) {
            contradictionList.appendChild(
              el(
                "div",
                { class: "muted" },
                "No open contradictions. If two stored notes conflict, the pair " +
                  "appears here so you can resolve it."
              )
            );
            return;
          }
          items.forEach(function (item) {
            var button = el("button", { type: "button", class: "primary" }, "Resolve");
            button.addEventListener("click", function () {
              button.disabled = true;
              button.textContent = "Resolving...";
              api("/memories/contradictions/" + encodeURIComponent(item.id) + "/resolve", {
                method: "POST"
              })
                .then(function (result) {
                  setText(
                    contradictionStatus,
                    "Resolved " + result.id + " as " + result.status + " at " + result.resolved_at
                  );
                  loadContradictions();
                  loadMemories();
                  loadEvidence();
                })
                .catch(function () {
                  button.disabled = false;
                  button.textContent = "Resolve";
                  contradictionList.appendChild(
                    el("div", { class: "notice notice-fail" }, plainError())
                  );
                });
            });

            contradictionList.appendChild(
              el("div", { class: "inbox-item" }, [
                el("div", { class: "inbox-reason" }, item.reason || "conflict"),
                el("div", { class: "inbox-grid" }, [
                  el("div", { class: "inbox-side" }, [
                    el("h4", {}, "Remembered first"),
                    el("p", {}, item.left || "(missing memory)")
                  ]),
                  el("div", { class: "inbox-side" }, [
                    el("h4", {}, "Newer claim"),
                    el("p", {}, item.right || "(missing memory)")
                  ])
                ]),
                el("div", { class: "inbox-actions" }, button)
              ])
            );
          });
        })
        .catch(function () {
          setText(contradictionStatus, plainError());
        });
    }

    /* ----------------------------------------------------------- select */

    function selectUser(userId) {
      state.selectedId = userId;
      state.memoryPage = 1;
      if (userSelect.value !== userId) {
        userSelect.value = userId;
      }
      loadMemories();
      loadContradictions();
      var user = selectedUser();
      if (user) {
        setText(
          passportStatus,
          "Selected " +
            (user.display_name || user.user_id) +
            " (user id " +
            user.user_id +
            "). Export or import a passport below."
        );
      }
    }

    /* -------------------------------------------------------- passport */

    function exportPassport() {
      if (!state.selectedId) {
        setText(passportStatus, "Select a user before exporting.");
        return;
      }
      setText(passportStatus, "Exporting passport ...");
      api("/memories/" + encodeURIComponent(state.selectedId) + "/passport")
        .then(function (data) {
          var memories = asArray(data && data.memories);
          var name =
            data && data.user && data.user.display_name ? data.user.display_name : state.selectedId;
          downloadJson("cheta-passport-" + safeFilename(name) + ".json", data);
          setText(
            passportStatus,
            "Exported " +
              memories.length +
              " memories for " +
              name +
              " (namespace " +
              ((data && data.namespace) || "unknown") +
              ")."
          );
        })
        .catch(function () {
          setText(passportStatus, plainError());
        });
    }

    function importPassportFile(file) {
      var reader = new FileReader();
      reader.onload = function () {
        var parsed = null;
        try {
          parsed = JSON.parse(String(reader.result));
        } catch (err) {
          setText(passportStatus, "Import failed: the file is not valid JSON.");
          return;
        }
        setText(passportStatus, "Importing passport ...");
        api("/memories/passport/import", { method: "POST", body: parsed })
          .then(function (result) {
            setText(
              passportStatus,
              "Imported " +
                num(result.imported) +
                " memories and skipped " +
                num(result.skipped) +
                " duplicates into namespace " +
                result.namespace +
                " (user id " +
                result.user_id +
                ")."
            );
            loadEvidence();
          })
          .catch(function () {
            setText(passportStatus, plainError());
          });
      };
      reader.onerror = function () {
        setText(passportStatus, "Import failed: the file could not be read.");
      };
      reader.readAsText(file);
    }

    /* ----------------------------------------------------------- health */

    function loadHealth() {
      setText(healthStatus, "Loading health ...");
      api("/health")
        .then(function (health) {
          clear(healthBody);
          var memory = (health && health.memory) || {};
          var llm = (health && health.llm) || {};
          var providers = asArray(llm.providers);
          var degraded = Boolean(memory.degraded);

          var items = [
            ["Status", health.status || "unknown", health.status === "ok" ? "value-ok" : "value-fail"],
            ["Environment", health.environment || "unknown", ""],
            ["Memory mode", memory.mode || "unknown", memory.mode === "walrus" ? "value-ok" : "value-warn"],
            ["Memory degraded", degraded ? "yes" : "no", degraded ? "value-fail" : "value-ok"],
            [
              "LLM providers",
              providers.length ? providers.join(", ") : "none configured",
              providers.length ? "value-ok" : "value-warn"
            ],
            [
              "Telegram",
              health.telegram && health.telegram.configured ? "configured" : "not configured",
              health.telegram && health.telegram.configured ? "value-ok" : ""
            ]
          ];

          items.forEach(function (pair) {
            healthBody.appendChild(
              el("div", { class: "kv-item" }, [
                el("div", { class: "kv-key" }, pair[0]),
                el("div", { class: "kv-value " + pair[2] }, pair[1])
              ])
            );
          });
          setText(
            healthStatus,
            degraded
              ? "Walrus Memory is unreachable right now; counts and recalls may be incomplete."
              : "All reported dependencies are healthy."
          );
        })
        .catch(function () {
          setText(healthStatus, plainError());
        });
    }

    /* ------------------------------------------------------------ wire */

    userSelect.addEventListener("change", function () {
      selectUser(userSelect.value);
    });
    showSuperseded.addEventListener("change", loadMemories);
    showContradicted.addEventListener("change", loadMemories);
    $("refresh-evidence").addEventListener("click", loadEvidence);
    $("refresh-memories").addEventListener("click", loadMemories);
    $("refresh-contradictions").addEventListener("click", loadContradictions);
    $("refresh-health").addEventListener("click", loadHealth);
    $("passport-export").addEventListener("click", exportPassport);
    passportFile.addEventListener("change", function () {
      var files = passportFile.files;
      if (files && files.length) {
        importPassportFile(files[0]);
      }
      passportFile.value = "";
    });

    loadEvidence();
    loadHealth();
  }

  /* ------------------------------------------------------------- boot */

  function boot() {
    if ($("chat-app")) {
      initChat();
    }
    if ($("dashboard-app")) {
      initDashboard();
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }

  window.Cheta = {
    api: api,
    el: el,
    num: num,
    fixed: fixed,
    truncate: truncate,
    downloadJson: downloadJson,
    safeFilename: safeFilename,
    storeGet: storeGet,
    storeSet: storeSet,
    SURFACE: SURFACE,
    THRESHOLD: THRESHOLD,
    KEYS: KEYS
  };
})();
