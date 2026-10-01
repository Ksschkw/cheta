/* Ranti extension surface: side panel chat against the deployed API.
 *
 * Vanilla JS only. No build step, no frameworks, no external requests beyond
 * the configured Ranti API base URL. chrome.storage.local keeps the identity,
 * the display name, the memory toggle, and the session transcript.
 *
 * ASCII only by policy: no emojis, no smart punctuation.
 */

(function () {
  "use strict";

  var SURFACE = "extension";
  var DEFAULT_BASE_URL = "https://ranti-gkn7.onrender.com";
  var MAX_TEXT = 8000;
  var HISTORY_LIMIT = 60;
  var CHAT_TIMEOUT_MS = 90000;
  var READ_TIMEOUT_MS = 30000;
  var HEALTH_TIMEOUT_MS = 12000;
  var DEFAULT_DISPLAY_NAME = "Extension visitor";

  var KEYS = {
    baseUrl: "ranti.extension.base_url",
    surfaceUserId: "ranti.extension.surface_user_id",
    displayName: "ranti.extension.display_name",
    userId: "ranti.extension.user_id",
    memoryEnabled: "ranti.extension.memory_enabled",
    history: "ranti.extension.history"
  };

  var HELP_TEXT =
    "Commands:\n" +
    "/start     welcome and what is remembered about you\n" +
    "/memories  show everything stored about you\n" +
    "/help      this message\n\n" +
    "Send a normal message to talk. Turn memory off to answer one turn without it.";

  var GENERIC_INTRO =
    "Hi, I am Ranti. I am a memory-first assistant: what you tell me is stored in " +
    "your own private memory space and comes back in later conversations, on any " +
    "of my surfaces.";

  var state = {
    baseUrl: DEFAULT_BASE_URL,
    surfaceUserId: "",
    displayName: "",
    userId: "",
    memoryEnabled: true,
    history: [],
    busy: false
  };

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

  function hasChromeStorage() {
    return (
      typeof chrome !== "undefined" &&
      chrome.storage &&
      chrome.storage.local &&
      typeof chrome.storage.local.get === "function"
    );
  }

  function storageGet(keys) {
    return new Promise(function (resolve) {
      if (hasChromeStorage()) {
        try {
          chrome.storage.local.get(keys, function (items) {
            resolve(items || {});
          });
          return;
        } catch (err) {
          resolve({});
          return;
        }
      }
      var out = {};
      try {
        keys.forEach(function (key) {
          var raw = window.localStorage.getItem(key);
          if (raw !== null) {
            try {
              out[key] = JSON.parse(raw);
            } catch (parseErr) {
              out[key] = raw;
            }
          }
        });
      } catch (err) {
        /* Storage disabled: state stays in memory only. */
      }
      resolve(out);
    });
  }

  function storageSet(values) {
    return new Promise(function (resolve) {
      if (hasChromeStorage()) {
        try {
          chrome.storage.local.set(values, function () {
            resolve();
          });
          return;
        } catch (err) {
          resolve();
          return;
        }
      }
      try {
        Object.keys(values).forEach(function (key) {
          window.localStorage.setItem(key, JSON.stringify(values[key]));
        });
      } catch (err) {
        /* Storage disabled: state stays in memory only. */
      }
      resolve();
    });
  }

  function persist(key, value) {
    var values = {};
    values[key] = value;
    return storageSet(values);
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

  function normalizeBaseUrl(raw) {
    var value = String(raw === null || raw === undefined ? "" : raw).trim();
    if (!value) {
      return DEFAULT_BASE_URL;
    }
    if (!/^https?:\/\//i.test(value)) {
      if (/^[a-z][a-z0-9+.-]*:\/\//i.test(value)) {
        return null;
      }
      value = "https://" + value;
    }
    return value.replace(/\/+$/, "");
  }

  function baseUrl() {
    return state.baseUrl.replace(/\/+$/, "");
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

    var url = baseUrl() + path;
    var controller = null;
    var timer = null;
    if (typeof AbortController === "function") {
      controller = new AbortController();
      init.signal = controller.signal;
      timer = window.setTimeout(function () {
        controller.abort();
      }, opts.timeoutMs || CHAT_TIMEOUT_MS);
    }

    return fetch(url, init)
      .catch(function (err) {
        var reason =
          err && err.name === "AbortError"
            ? "the request timed out"
            : (err && err.message) || "network error";
        throw new Error(reason + " (" + url + ")");
      })
      .then(function (response) {
        return response.text().then(function (raw) {
          if (timer) {
            window.clearTimeout(timer);
          }
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
            throw new Error(detail || "HTTP " + response.status + " from " + path);
          }
          return data;
        });
      });
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

  function safeClass(value) {
    var text = String(value === null || value === undefined ? "" : value);
    var cleaned = text.replace(/[^A-Za-z0-9_-]+/g, "-").replace(/^-+|-+$/g, "");
    return cleaned || "unknown";
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

  /* ---------------------------------------------------------- rendering */

  function scrollToEnd() {
    var transcript = $("transcript");
    if (transcript) {
      transcript.scrollTop = transcript.scrollHeight;
    }
  }

  function hideEmptyHint() {
    var hint = $("empty-hint");
    if (hint) {
      hint.classList.add("hidden");
    }
  }

  function userNode(text) {
    return el("div", { class: "msg user" }, [
      el("div", { class: "msg-head" }, [el("span", { class: "who" }, "You")]),
      el("div", { class: "bubble" }, text)
    ]);
  }

  function errorNode(message) {
    return el("div", { class: "msg error" }, [
      el("div", { class: "msg-head" }, [el("span", { class: "who" }, "[FAIL] Error")]),
      el("div", { class: "bubble" }, message)
    ]);
  }

  function commandNode(text) {
    return el("div", { class: "msg command" }, [
      el("div", { class: "msg-head" }, [
        el("span", { class: "who" }, "Ranti"),
        el("span", { class: "tag" }, "command, no model call")
      ]),
      el("div", { class: "bubble" }, text)
    ]);
  }

  function renderChip(memory) {
    var blobId = memory && memory.blob_id ? String(memory.blob_id) : "";
    return el("div", { class: "chip" }, [
      el("div", { class: "chip-text" }, (memory && memory.text) || "(empty memory text)"),
      el("div", { class: "chip-meta" }, [
        el("span", {}, "salience " + fixed(memory ? memory.salience : 0, 2)),
        el("span", {}, "origin " + ((memory && memory.origin_surface) || "unknown")),
        el("span", { title: blobId }, "blob " + truncate(blobId, 16))
      ])
    ]);
  }

  function renderStoredFact(fact) {
    var pending = Boolean(fact && fact.pending);
    var blobId = fact && fact.blob_id ? String(fact.blob_id) : "";
    var persistence = pending
      ? "accepted, persisting (no blob id yet)"
      : "stored blob " + truncate(blobId, 16);
    return el("div", { class: "chip" + (pending ? " chip-pending" : "") }, [
      el("div", { class: "chip-text" }, (fact && fact.text) || "(empty fact text)"),
      el("div", { class: "chip-meta" }, [
        el("span", {}, "verdict " + ((fact && fact.verdict) || "new")),
        el("span", { title: blobId }, persistence)
      ])
    ]);
  }

  function renderCfColumn(title, body, cls) {
    return el("div", { class: "cf-col " + cls }, [
      el("h4", {}, title),
      el("div", { class: "cf-body" }, body || "(no reply)")
    ]);
  }

  function runCounterfactual(turnId, button, zone) {
    button.disabled = true;
    button.textContent = "Replaying without memory...";
    var previous = zone.querySelector(".cf-result");
    if (previous) {
      zone.removeChild(previous);
    }
    api("/chat/counterfactual/" + encodeURIComponent(turnId), {
      method: "POST",
      timeoutMs: CHAT_TIMEOUT_MS
    })
      .then(function (data) {
        zone.appendChild(
          el("div", { class: "cf-result" }, [
            el("div", { class: "cf-title" }, "Same question, with memory and without"),
            el("div", { class: "cf-grid" }, [
              renderCfColumn("With memory", data.with_memory, "cf-with"),
              renderCfColumn("Without memory", data.without_memory, "cf-without")
            ]),
            el("div", { class: "cf-summary" }, data.summary),
            el(
              "div",
              { class: "meta-line" },
              "recalled memories: " +
                num(data.recalled_count) +
                " | reply changed: " +
                (data.reply_changed ? "yes" : "no")
            )
          ])
        );
        button.textContent = "Refresh without-memory replay";
        button.disabled = false;
        scrollToEnd();
      })
      .catch(function (err) {
        zone.appendChild(
          el("div", { class: "notice notice-fail" }, "[FAIL] Counterfactual failed: " + err.message)
        );
        button.textContent = "Show it without memory";
        button.disabled = false;
      });
  }

  function assistantNode(turn, usedMemory) {
    var recalled = asArray(turn.recalled);
    var wrap = el("div", { class: "msg assistant" + (usedMemory ? "" : " no-memory") }, [
      el("div", { class: "msg-head" }, [
        el("span", { class: "who" }, "Ranti"),
        usedMemory
          ? null
          : el("span", { class: "tag tag-warn" }, "[NO MEMORY] answered without memory")
      ]),
      el("div", { class: "bubble" }, turn.reply || "(empty reply)")
    ]);

    if (turn.memory_degraded) {
      var note = turn.memory_note ? " Detail: " + turn.memory_note : "";
      wrap.appendChild(
        el(
          "div",
          { class: "notice notice-warn" },
          "[WARN] Walrus Memory was unreachable for this turn, so memories could not " +
            "be recalled or written. This does not mean you have no memories." +
            note
        )
      );
    }

    if (recalled.length) {
      wrap.appendChild(
        el(
          "div",
          { class: "chips-label" },
          "Recalled memories for this turn (" + recalled.length + ")"
        )
      );
      wrap.appendChild(el("div", { class: "chips" }, recalled.map(renderChip)));
    } else if (!turn.memory_degraded) {
      wrap.appendChild(
        el(
          "div",
          { class: "muted" },
          usedMemory
            ? "No memories were recalled for this turn."
            : "Memory was switched off for this turn, so nothing was recalled and nothing was stored."
        )
      );
    }

    var stored = asArray(turn.stored_facts);
    var persisting = stored.filter(function (fact) {
      return fact && fact.pending;
    });
    if (persisting.length) {
      wrap.appendChild(
        el(
          "div",
          { class: "chips-label" },
          "Facts accepted this turn (" + persisting.length + " persisting)"
        )
      );
      wrap.appendChild(el("div", { class: "chips" }, persisting.map(renderStoredFact)));
    }

    var bits = [];
    var settled = stored.filter(function (fact) {
      return fact && fact.blob_id && !fact.pending;
    });
    if (settled.length) {
      bits.push(settled.length + " new memory stored");
    }
    if (persisting.length) {
      bits.push(persisting.length + " accepted, persisting");
    }
    if (turn.skipped_duplicates) {
      bits.push(num(turn.skipped_duplicates) + " duplicate skipped");
    }
    if (turn.contradiction_count) {
      bits.push(num(turn.contradiction_count) + " contradiction flagged");
    }
    if (bits.length) {
      wrap.appendChild(el("div", { class: "meta-line" }, "memory: " + bits.join(", ")));
    }

    var zone = el("div", { class: "cf-zone" });
    var button = el("button", { type: "button", class: "ghost" }, "Show it without memory");
    button.addEventListener("click", function () {
      runCounterfactual(turn.turn_id, button, zone);
    });
    zone.appendChild(button);
    wrap.appendChild(zone);

    return wrap;
  }

  function appendNode(node) {
    var transcript = $("transcript");
    if (!transcript) {
      return;
    }
    transcript.appendChild(node);
    hideEmptyHint();
    scrollToEnd();
  }

  function record(entry) {
    state.history.push(entry);
    while (state.history.length > HISTORY_LIMIT) {
      state.history.shift();
    }
    persist(KEYS.history, state.history);
  }

  function renderHistory() {
    var transcript = $("transcript");
    if (!transcript) {
      return;
    }
    clear(transcript);
    if (!state.history.length) {
      transcript.appendChild(
        el(
          "div",
          { id: "empty-hint", class: "muted" },
          "Say something durable, for example a preference, a constraint, or a fact " +
            "about your work. Each reply shows the exact memories that were recalled " +
            "for it. Type /help for commands."
        )
      );
      return;
    }
    state.history.forEach(function (entry) {
      if (!entry || !entry.role) {
        return;
      }
      if (entry.role === "user") {
        transcript.appendChild(userNode(entry.text || ""));
      } else if (entry.role === "assistant" && entry.turn) {
        transcript.appendChild(assistantNode(entry.turn, entry.usedMemory !== false));
      } else if (entry.role === "command") {
        transcript.appendChild(commandNode(entry.text || ""));
      } else if (entry.role === "error") {
        transcript.appendChild(errorNode(entry.text || ""));
      }
    });
    scrollToEnd();
  }

  /* ---------------------------------------------------------- commands */

  function commandHead(text) {
    var parts = String(text || "").trim().split(/\s+/);
    return (parts[0] || "").toLowerCase();
  }

  function isCommand(text) {
    var head = commandHead(text);
    return head === "/start" || head === "/help" || head === "/memories";
  }

  function sortByImportance(rows) {
    return asArray(rows).slice().sort(function (a, b) {
      return (Number(b && b.importance) || 0) - (Number(a && a.importance) || 0);
    });
  }

  function loadActiveMemories() {
    if (!state.userId) {
      return Promise.resolve([]);
    }
    return api(
      "/memories/" + encodeURIComponent(state.userId) + "?include_inactive=false",
      { timeoutMs: READ_TIMEOUT_MS }
    ).then(sortByImportance);
  }

  function memoriesReply() {
    if (!state.userId) {
      return Promise.resolve(
        "I have nothing stored about you yet. Tell me a few things about yourself " +
          "and I will remember them for next time.\n\n" +
          HELP_TEXT
      );
    }
    return loadActiveMemories()
      .then(function (records) {
        if (!records.length) {
          return (
            "I have nothing stored about you yet. Tell me a few things about yourself " +
            "and I will remember them for next time.\n\n" +
            HELP_TEXT
          );
        }
        var lines = ["Here is what I have stored about you (" + records.length + " notes):", ""];
        records.slice(0, 10).forEach(function (record, index) {
          var marker = record.status && record.status !== "active" ? " [" + record.status + "]" : "";
          lines.push(index + 1 + ". " + (record.text || "(empty memory text)") + marker);
        });
        if (records.length > 10) {
          lines.push("...and " + (records.length - 10) + " more.");
        }
        lines.push("", "Tell me if any of that is wrong and I will correct it.");
        return lines.join("\n");
      })
      .catch(function (err) {
        return "[FAIL] Could not read stored memories: " + err.message;
      });
  }

  function welcomeReply() {
    if (!state.userId) {
      return Promise.resolve(GENERIC_INTRO + "\n\n" + HELP_TEXT);
    }
    return loadActiveMemories()
      .then(function (records) {
        if (!records.length) {
          return GENERIC_INTRO + "\n\n" + HELP_TEXT;
        }
        var sample = records
          .slice(0, 3)
          .map(function (record) {
            return record.text || "(empty memory text)";
          })
          .join("; ");
        return (
          "Welcome back. I remember " +
          records.length +
          " things about you, including: " +
          sample +
          ".\n\n" +
          HELP_TEXT
        );
      })
      .catch(function (err) {
        return (
          GENERIC_INTRO +
          "\n\n[WARN] I could not read your stored memories just now (" +
          err.message +
          "), which is not the same as having none.\n\n" +
          HELP_TEXT
        );
      });
  }

  function runCommand(text) {
    var head = commandHead(text);
    return (head === "/memories" ? memoriesReply() : welcomeReply()).then(function (reply) {
      appendNode(commandNode(reply));
      record({ role: "command", text: reply });
    });
  }

  /* --------------------------------------------------------- memory panel */

  function renderMemoryList(rows) {
    var list = $("memory-list");
    if (!list) {
      return;
    }
    clear(list);
    if (!rows.length) {
      list.appendChild(
        el("div", { class: "muted" }, "No memories match the current filter for this identity.")
      );
      return;
    }
    rows.forEach(function (memory) {
      var status = memory.status || "unknown";
      var blobId = memory.blob_id ? String(memory.blob_id) : "";
      list.appendChild(
        el("div", { class: "memory-card status-" + safeClass(status) }, [
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
          ])
        ])
      );
    });
  }

  function loadMemories() {
    var status = $("memory-status");
    var includeInactive = $("mem-include-inactive") && $("mem-include-inactive").checked;
    if (!state.userId) {
      renderMemoryList([]);
      setText(
        status,
        "No turns on this extension yet, so no user exists to list memories for."
      );
      return;
    }
    setText(status, "Loading memories for user " + state.userId + " ...");
    api(
      "/memories/" +
        encodeURIComponent(state.userId) +
        "?include_inactive=" +
        (includeInactive ? "true" : "false"),
      { timeoutMs: READ_TIMEOUT_MS }
    )
      .then(function (rows) {
        var all = asArray(rows);
        renderMemoryList(all);
        setText(
          status,
          all.length +
            " record(s) returned (" +
            (includeInactive ? "including inactive" : "active only") +
            ")."
        );
      })
      .catch(function (err) {
        setText(status, "[FAIL] Could not load memories: " + err.message);
      });
  }

  /* ------------------------------------------------------------- status */

  function setConn(message, cls) {
    var node = $("conn-status");
    if (!node) {
      return;
    }
    node.textContent = message;
    node.className = "conn" + (cls ? " " + cls : "");
  }

  function setDegraded(degraded) {
    var banner = $("degraded-banner");
    if (!banner) {
      return;
    }
    if (degraded) {
      banner.textContent =
        "[WARN] Walrus Memory did not answer for the most recent turn. Memory was " +
        "unreachable, which is not the same as having no memories. Recalls and " +
        "writes may be incomplete until it recovers.";
      banner.classList.remove("hidden");
    } else {
      banner.classList.add("hidden");
    }
  }

  function checkHealth() {
    setConn("[OK] Checking " + baseUrl() + "/health ...", "");
    return api("/health", { timeoutMs: HEALTH_TIMEOUT_MS })
      .then(function (health) {
        var memory = (health && health.memory) || {};
        if (memory.degraded) {
          setConn(
            "[WARN] API reachable at " + baseUrl() + "; Walrus Memory is degraded.",
            "conn-warn"
          );
        } else {
          setConn("[OK] API reachable at " + baseUrl() + ".", "conn-ok");
        }
      })
      .catch(function (err) {
        setConn("[FAIL] Could not reach " + baseUrl() + ": " + err.message, "conn-fail");
      });
  }

  /* -------------------------------------------------------------- chat */

  function setBusy(busy) {
    state.busy = busy;
    var button = $("send-button");
    if (button) {
      button.disabled = busy;
      button.textContent = busy ? "Thinking..." : "Send";
    }
  }

  function syncToggleLabel() {
    var toggle = $("memory-toggle");
    var label = $("memory-toggle-state");
    if (!toggle || !label) {
      return;
    }
    label.textContent = toggle.checked ? "Memory ON" : "Memory OFF for this turn";
    label.className = toggle.checked ? "tag tag-ok" : "tag tag-warn";
  }

  function sendTurn(event) {
    if (event) {
      event.preventDefault();
    }
    if (state.busy) {
      return;
    }
    var input = $("chat-input");
    var value = input ? input.value.trim() : "";
    if (!value) {
      return;
    }

    if (isCommand(value)) {
      appendNode(userNode(value));
      record({ role: "user", text: value });
      input.value = "";
      setBusy(true);
      runCommand(value).then(function () {
        setBusy(false);
      });
      return;
    }

    var nameInput = $("display-name");
    var name = nameInput ? nameInput.value.trim() : "";
    if (!name) {
      name = DEFAULT_DISPLAY_NAME;
      if (nameInput) {
        nameInput.value = name;
      }
    }
    state.displayName = name;
    persist(KEYS.displayName, name);

    var toggle = $("memory-toggle");
    var usedMemory = toggle ? toggle.checked : true;
    state.memoryEnabled = usedMemory;
    persist(KEYS.memoryEnabled, usedMemory);

    appendNode(userNode(value));
    record({ role: "user", text: value });
    input.value = "";
    setBusy(true);

    api("/chat/turn", {
      method: "POST",
      timeoutMs: CHAT_TIMEOUT_MS,
      body: {
        surface: SURFACE,
        surface_user_id: state.surfaceUserId,
        display_name: name,
        text: value.slice(0, MAX_TEXT),
        memory_enabled: usedMemory
      }
    })
      .then(function (turn) {
        state.userId = turn.user_id;
        persist(KEYS.userId, turn.user_id);
        appendNode(assistantNode(turn, usedMemory));
        record({ role: "assistant", turn: turn, usedMemory: usedMemory });
        setDegraded(Boolean(turn.memory_degraded));
        var panel = $("memory-panel");
        if (panel && !panel.classList.contains("hidden")) {
          loadMemories();
        }
      })
      .catch(function (err) {
        var message = "Could not complete the turn: " + err.message;
        appendNode(errorNode(message));
        record({ role: "error", text: message });
      })
      .then(function () {
        setBusy(false);
      });
  }

  /* ---------------------------------------------------------- settings */

  function saveSettings() {
    var status = $("settings-status");
    var raw = $("base-url") ? $("base-url").value : "";
    var normalized = normalizeBaseUrl(raw);
    if (!normalized) {
      setText(status, "[FAIL] Base URL must start with http:// or https://.");
      return;
    }
    state.baseUrl = normalized;
    var name = $("display-name") ? $("display-name").value.trim() : "";
    state.displayName = name;
    var values = {};
    values[KEYS.baseUrl] = normalized;
    values[KEYS.displayName] = name;
    storageSet(values).then(function () {
      var input = $("base-url");
      if (input) {
        input.value = normalized;
      }
      setText(status, "[OK] Saved. Base URL is " + normalized + ".");
      checkHealth();
    });
  }

  function resetBaseUrl() {
    state.baseUrl = DEFAULT_BASE_URL;
    var input = $("base-url");
    if (input) {
      input.value = DEFAULT_BASE_URL;
    }
    persist(KEYS.baseUrl, DEFAULT_BASE_URL).then(function () {
      setText($("settings-status"), "[OK] Base URL reset to " + DEFAULT_BASE_URL + ".");
      checkHealth();
    });
  }

  /* -------------------------------------------------------------- boot */

  async function init() {
    var items = await storageGet(Object.keys(KEYS).map(function (key) {
      return KEYS[key];
    }));

    state.baseUrl = normalizeBaseUrl(items[KEYS.baseUrl] || DEFAULT_BASE_URL) || DEFAULT_BASE_URL;
    state.displayName = items[KEYS.displayName] || "";
    state.userId = items[KEYS.userId] || "";
    state.memoryEnabled = items[KEYS.memoryEnabled] !== false;
    state.history = Array.isArray(items[KEYS.history]) ? items[KEYS.history] : [];

    state.surfaceUserId = items[KEYS.surfaceUserId] || "";
    if (!state.surfaceUserId) {
      state.surfaceUserId = "extension-" + newId();
      await persist(KEYS.surfaceUserId, state.surfaceUserId);
    }

    var baseInput = $("base-url");
    if (baseInput) {
      baseInput.value = state.baseUrl;
    }
    var nameInput = $("display-name");
    if (nameInput) {
      nameInput.value = state.displayName;
    }
    var idInput = $("surface-user-id");
    if (idInput) {
      idInput.value = state.surfaceUserId;
    }
    var toggle = $("memory-toggle");
    if (toggle) {
      toggle.checked = state.memoryEnabled;
    }
    syncToggleLabel();

    renderHistory();
    checkHealth();
  }

  function wire() {
    var form = $("composer");
    if (form) {
      form.addEventListener("submit", sendTurn);
    }

    var input = $("chat-input");
    if (input) {
      input.addEventListener("keydown", function (event) {
        if (event.key === "Enter" && !event.shiftKey) {
          event.preventDefault();
          sendTurn(event);
        }
      });
    }

    var toggle = $("memory-toggle");
    if (toggle) {
      toggle.addEventListener("change", function () {
        state.memoryEnabled = toggle.checked;
        persist(KEYS.memoryEnabled, toggle.checked);
        syncToggleLabel();
      });
    }

    var panelButton = $("toggle-memory-panel");
    var panel = $("memory-panel");
    if (panelButton && panel) {
      panelButton.addEventListener("click", function () {
        var open = panel.classList.contains("hidden");
        if (open) {
          panel.classList.remove("hidden");
          loadMemories();
        } else {
          panel.classList.add("hidden");
        }
        panelButton.setAttribute("aria-expanded", open ? "true" : "false");
      });
    }

    var refresh = $("refresh-memories");
    if (refresh) {
      refresh.addEventListener("click", loadMemories);
    }

    var includeInactive = $("mem-include-inactive");
    if (includeInactive) {
      includeInactive.addEventListener("change", loadMemories);
    }

    var save = $("save-settings");
    if (save) {
      save.addEventListener("click", saveSettings);
    }

    var reset = $("reset-base-url");
    if (reset) {
      reset.addEventListener("click", resetBaseUrl);
    }
  }

  function boot() {
    wire();
    init().catch(function (err) {
      setConn("[FAIL] Could not load extension settings: " + err.message, "conn-fail");
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
