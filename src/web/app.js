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

  /* Plain words for a failed call. Never prints a host, port or path. */
  function plainError(err) {
    if (err && err.network) {
      return "Could not reach the server. Check your connection and try again.";
    }
    return "The request could not be completed. Please try again.";
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
    var toggle = $("memory-toggle");
    var input = $("chat-input");
    var form = $("composer");
    var sendButton = $("send-button");
    var banner = $("degraded-banner");

    var state = {
      surfaceUserId: getSurfaceUserId(),
      userId: storeGet(KEYS.userId, ""),
      displayName: storeGet(KEYS.displayName, ""),
      busy: false
    };

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

    function appendUser(value) {
      transcript.appendChild(
        el("div", { class: "turn user" }, [
          el("span", { class: "who" }, "You"),
          el("div", { class: "bubble" }, value)
        ])
      );
      scrollToEnd();
    }

    function appendError(message) {
      transcript.appendChild(
        el("div", { class: "turn error" }, [
          el("span", { class: "who" }, "Cheta"),
          el("div", { class: "bubble" }, message)
        ])
      );
      scrollToEnd();
    }

    function renderRecalled(memories) {
      var items = memories.map(function (memory) {
        var text = (memory && memory.text) || "";
        return el("li", {}, text);
      });
      return el("div", { class: "recalled" }, [
        el("p", { class: "recalled-label" }, "RECALLED"),
        el("ul", { class: "recalled-list" }, items)
      ]);
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

    function appendAssistant(turn, usedMemory) {
      var recalled = asArray(turn.recalled);
      var isCommand = Boolean(turn.command) || !turn.turn_id;
      var bubble = el("div", { class: "bubble" }, turn.reply || "(empty reply)");

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

    function submitText(rawValue) {
      if (state.busy) {
        return;
      }
      var value = String(rawValue === null || rawValue === undefined ? "" : rawValue).trim();
      if (!value) {
        return;
      }
      if (value.length > MAX_TEXT) {
        value = value.slice(0, MAX_TEXT);
      }

      var name = displayName();
      var usedMemory = toggle ? toggle.checked : true;
      storeSet(KEYS.memoryEnabled, usedMemory ? "1" : "0");

      appendUser(value);
      input.value = "";
      input.style.height = "";
      setBusy(true);

      api("/chat/turn", {
        method: "POST",
        body: {
          surface: SURFACE,
          surface_user_id: state.surfaceUserId,
          display_name: name,
          text: value,
          memory_enabled: usedMemory
        }
      })
        .then(function (turn) {
          state.userId = turn.user_id;
          storeSet(KEYS.userId, turn.user_id);
          appendAssistant(turn, usedMemory);
          setDegraded(Boolean(turn.memory_degraded));
        })
        .catch(function (err) {
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

    form.addEventListener("submit", sendTurn);
    input.addEventListener("input", autoGrow);
    input.addEventListener("keydown", function (event) {
      if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        sendTurn(event);
      }
    });

    transcript.appendChild(
      el("div", { class: "turn assistant is-intro" }, [
        el("span", { class: "who" }, "Cheta"),
        el(
          "div",
          { class: "bubble" },
          "Hi, I am Cheta. I keep what matters and bring it back on later turns. " +
            "Tell me something worth remembering, or type /help to see what I can do."
        )
      ])
    );
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
