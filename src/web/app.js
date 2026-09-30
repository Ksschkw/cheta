/* Ranti web surfaces: shared logic for the chat and the evidence dashboard.
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

  var KEYS = {
    displayName: "ranti.display_name",
    surfaceUserId: "ranti.surface_user_id",
    userId: "ranti.user_id",
    memoryEnabled: "ranti.memory_enabled"
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
    return fetch(path, init).then(function (response) {
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

  /* =============================================================== chat */

  function initChat() {
    var transcript = $("transcript");
    var nameInput = $("display-name");
    var surfaceHidden = $("surface-user-id");
    var toggle = $("memory-toggle");
    var toggleState = $("memory-toggle-state");
    var input = $("chat-input");
    var form = $("composer");
    var sendButton = $("send-button");
    var banner = $("degraded-banner");
    var countEl = $("memory-count");
    var countDetail = $("memory-count-detail");

    var state = {
      surfaceUserId: getSurfaceUserId(),
      userId: storeGet(KEYS.userId, ""),
      displayName: storeGet(KEYS.displayName, ""),
      busy: false
    };

    if (surfaceHidden) {
      surfaceHidden.value = state.surfaceUserId;
    }
    nameInput.value = state.displayName;
    toggle.checked = storeGet(KEYS.memoryEnabled, "1") !== "0";

    function syncToggleLabel() {
      toggleState.textContent = toggle.checked
        ? "ON: replies may use remembered facts"
        : "OFF: this turn is answered without memory";
      toggleState.className = toggle.checked ? "tag tag-ok" : "tag tag-warn";
    }
    syncToggleLabel();

    toggle.addEventListener("change", function () {
      storeSet(KEYS.memoryEnabled, toggle.checked ? "1" : "0");
      syncToggleLabel();
    });

    function setBusy(busy) {
      state.busy = busy;
      sendButton.disabled = busy;
      sendButton.textContent = busy ? "Thinking..." : "Send";
    }

    function scrollToEnd() {
      window.scrollTo({ top: document.body.scrollHeight, behavior: "smooth" });
    }

    function appendUser(value) {
      transcript.appendChild(
        el("div", { class: "msg user" }, [
          el("div", { class: "msg-head" }, [el("span", { class: "who" }, "You")]),
          el("div", { class: "bubble" }, value)
        ])
      );
      scrollToEnd();
    }

    function appendError(message) {
      transcript.appendChild(
        el("div", { class: "msg error" }, [
          el("div", { class: "msg-head" }, [el("span", { class: "who" }, "Error")]),
          el("div", { class: "bubble" }, message)
        ])
      );
      scrollToEnd();
    }

    function renderChip(memory) {
      var blobId = memory && memory.blob_id ? String(memory.blob_id) : "";
      var meta = el("div", { class: "chip-meta" }, [
        el("span", {}, "salience " + fixed(memory ? memory.salience : 0, 2)),
        el("span", {}, "origin " + ((memory && memory.origin_surface) || "unknown")),
        el("span", { title: blobId }, "blob " + truncate(blobId, 16))
      ]);
      return el("div", { class: "chip" }, [
        el("div", { class: "chip-text" }, (memory && memory.text) || "(empty memory text)"),
        meta
      ]);
    }

    function renderStoredFact(fact) {
      var pending = Boolean(fact && fact.pending);
      var blobId = fact && fact.blob_id ? String(fact.blob_id) : "";
      // A pending fact has only been accepted as a job. Its blob id does not
      // exist yet, so the chip must not present it as a stored blob.
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
      api("/chat/counterfactual/" + encodeURIComponent(turnId), { method: "POST" })
        .then(function (data) {
          var box = el("div", { class: "cf-result" }, [
            el("div", { class: "cf-title" }, "Counterfactual: the same question with memory off"),
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
          ]);
          zone.appendChild(box);
          button.textContent = "Refresh without-memory replay";
          button.disabled = false;
          scrollToEnd();
        })
        .catch(function (err) {
          zone.appendChild(
            el("div", { class: "notice notice-fail" }, "Counterfactual failed: " + err.message)
          );
          button.textContent = "Show it without memory";
          button.disabled = false;
        });
    }

    function appendAssistant(turn, usedMemory) {
      var recalled = asArray(turn.recalled);
      var head = el("div", { class: "msg-head" }, [
        el("span", { class: "who" }, "Ranti"),
        usedMemory
          ? null
          : el("span", { class: "tag tag-warn" }, "[NO MEMORY] answered without memory")
      ]);
      var wrap = el("div", { class: "msg assistant" + (usedMemory ? "" : " no-memory") }, [
        head,
        el("div", { class: "bubble" }, turn.reply || "(empty reply)")
      ]);

      if (turn.memory_degraded) {
        var note = turn.memory_note ? " Detail: " + turn.memory_note : "";
        wrap.appendChild(
          el(
            "div",
            { class: "notice notice-warn" },
            "[DEGRADED] Walrus Memory was unreachable for this turn, so memories could " +
              "not be recalled. This does not mean you have no memories." +
              note
          )
        );
      }

      if (recalled.length) {
        wrap.appendChild(
          el("div", { class: "chips-label" }, "Recalled memories for this turn (" + recalled.length + ")")
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
      var settled = stored.filter(function (fact) {
        return fact && fact.blob_id && !fact.pending;
      });
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

      transcript.appendChild(wrap);
      scrollToEnd();
    }

    function setDegraded(degraded) {
      if (degraded) {
        banner.textContent =
          "[DEGRADED] Walrus Memory did not answer for the most recent turn. Memory was " +
          "unreachable, which is not the same as having no memories. Replies and the " +
          "memory count may be incomplete until it recovers.";
        banner.classList.remove("hidden");
      } else {
        banner.classList.add("hidden");
      }
    }

    function refreshMemoryCount() {
      if (!state.userId) {
        setText(countEl, "0");
        setText(countDetail, "No turns yet on this browser, so no user exists to measure.");
        return;
      }
      setText(countDetail, "Refreshing from /memories/" + state.userId + "/stats ...");
      api("/memories/" + encodeURIComponent(state.userId) + "/stats")
        .then(function (stats) {
          setText(countEl, num(stats.active));
          if (stats.relayer_degraded) {
            setText(
              countDetail,
              "active memories in the local index; the Walrus Memory count is unreachable (degraded)."
            );
          } else {
            setText(
              countDetail,
              num(stats.relayer_memory_count) +
                " on Walrus Memory | " +
                num(stats.superseded) +
                " superseded | " +
                num(stats.turns) +
                " turns"
            );
          }
        })
        .catch(function (err) {
          setText(countDetail, "Memory count unavailable: " + err.message);
        });
    }

    function sendTurn(event) {
      if (event) {
        event.preventDefault();
      }
      if (state.busy) {
        return;
      }
      var value = input.value.trim();
      if (!value) {
        return;
      }
      var name = nameInput.value.trim();
      if (!name) {
        name = "Web visitor";
        nameInput.value = name;
      }
      state.displayName = name;
      storeSet(KEYS.displayName, name);

      var usedMemory = toggle.checked;
      storeSet(KEYS.memoryEnabled, usedMemory ? "1" : "0");

      appendUser(value);
      input.value = "";
      setBusy(true);

      api("/chat/turn", {
        method: "POST",
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
          storeSet(KEYS.userId, turn.user_id);
          appendAssistant(turn, usedMemory);
          setDegraded(Boolean(turn.memory_degraded));
          refreshMemoryCount();
        })
        .catch(function (err) {
          appendError("Could not complete the turn: " + err.message);
        })
        .then(function () {
          setBusy(false);
        });
    }

    form.addEventListener("submit", sendTurn);
    input.addEventListener("keydown", function (event) {
      if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        sendTurn(event);
      }
    });

    refreshMemoryCount();
  }

  /* ========================================================== dashboard */

  function initDashboard() {
    var state = {
      users: [],
      selectedId: ""
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
            meets
              ? el("span", { class: "tag tag-ok" }, "[" + THRESHOLD + "+ ACTIVE]")
              : null
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
        userSelect.appendChild(el("option", { value: "" }, "No users yet"));
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
      setText(evidenceStatus, "Loading /evidence/users ...");
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
        })
        .catch(function (err) {
          setText(evidenceStatus, "Could not load evidence: " + err.message);
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
            "No memories match the current filter for this user."
          )
        );
        return;
      }

      shown.forEach(function (memory) {
        var status = memory.status || "unknown";
        var blobId = memory.blob_id ? String(memory.blob_id) : "";
        memoryList.appendChild(
          el("div", { class: "memory-card status-" + safeFilename(status) }, [
            el("div", { class: "memory-text" }, memory.text || "(empty memory text)"),
            el("div", { class: "memory-meta" }, [
              statusTag(status),
              el("span", {}, "importance " + fixed(memory.importance, 2)),
              el("span", {}, "origin " + (memory.origin_surface || "unknown")),
              el("span", { title: "occurred_at " + (memory.occurred_at || "") }, "when " + truncate(memory.occurred_at || "unknown", 19)),
              el("span", { title: blobId }, "blob " + truncate(blobId, 24))
            ]),
            memory.superseded_by
              ? el(
                  "div",
                  { class: "memory-meta" },
                  el("span", { title: String(memory.superseded_by) }, "superseded_by " + truncate(String(memory.superseded_by), 24))
                )
              : null
          ])
        );
      });
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
        .catch(function (err) {
          setText(memoryStatus, "Could not load memories: " + err.message);
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
              el("div", { class: "muted" }, "No open contradictions for this user.")
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
                .catch(function (err) {
                  button.disabled = false;
                  button.textContent = "Resolve";
                  contradictionList.appendChild(
                    el("div", { class: "notice notice-fail" }, "Resolve failed: " + err.message)
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
        .catch(function (err) {
          setText(contradictionStatus, "Could not load contradictions: " + err.message);
        });
    }

    /* ----------------------------------------------------------- select */

    function selectUser(userId) {
      state.selectedId = userId;
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
          downloadJson("ranti-passport-" + safeFilename(name) + ".json", data);
          setText(
            passportStatus,
            "Exported " + memories.length + " memories for " + name + " (namespace " +
              ((data && data.namespace) || "unknown") + ")."
          );
        })
        .catch(function (err) {
          setText(passportStatus, "Export failed: " + err.message);
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
          .catch(function (err) {
            setText(passportStatus, "Import failed: " + err.message);
          });
      };
      reader.onerror = function () {
        setText(passportStatus, "Import failed: the file could not be read.");
      };
      reader.readAsText(file);
    }

    /* ----------------------------------------------------------- health */

    function loadHealth() {
      setText(healthStatus, "Loading /health ...");
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
            ["LLM providers", providers.length ? providers.join(", ") : "none configured", providers.length ? "value-ok" : "value-warn"],
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
        .catch(function (err) {
          setText(healthStatus, "Could not load health: " + err.message);
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

  window.Ranti = {
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
