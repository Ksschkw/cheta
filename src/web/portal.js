/* Cheta portal behaviour: the Three.js hero, browser detection and copy.
   No network calls. ASCII only. */

(function () {
  "use strict";

  /* The portal is served at the root of the domain, so every internal link is
     an absolute path under /app/ rather than a relative one. */
  var HELP_URL = "/app/extension-help.html";

  function startHero() {
    var canvas = document.getElementById("hero-canvas");
    if (!canvas || !window.ChetaHero) {
      return;
    }
    window.ChetaHero.mount(canvas, { settleAt: 7600 });
  }

  function detectBrowser() {
    var ua = navigator.userAgent || "";
    var isEdge = /Edg\//.test(ua) || /EdgA\//.test(ua) || /EdgiOS\//.test(ua);
    var isOpera = /OPR\//.test(ua) || /OPiOS\//.test(ua);
    var isFirefox = /Firefox\//.test(ua) || /FxiOS\//.test(ua);
    var isChrome = !isEdge && !isOpera && /Chrome\//.test(ua);

    if (isFirefox) {
      return "firefox";
    }
    if (isEdge || isChrome) {
      return "chromium";
    }
    return "other";
  }

  function makeButton(label) {
    var link = document.createElement("a");
    link.className = "btn";
    link.href = HELP_URL;
    link.textContent = label;
    return link;
  }

  function renderExtensionCta() {
    var host = document.getElementById("extension-cta");
    if (!host) {
      return;
    }

    var browser = detectBrowser();
    host.textContent = "";

    if (browser === "chromium") {
      host.appendChild(makeButton("Add to Chrome"));
      return;
    }

    if (browser === "firefox") {
      host.appendChild(makeButton("Add to Firefox"));
      return;
    }

    var note = document.createElement("p");
    note.className = "alt-note";
    note.textContent =
      "Other browser. The extension ships as a Chromium side panel and a " +
      "Firefox temporary add-on, so this browser is not supported yet.";
    host.appendChild(note);

    var link = document.createElement("a");
    link.href = HELP_URL;
    link.textContent = "Read the install notes";
    host.appendChild(link);
  }

  function legacyCopy(text) {
    var area = document.createElement("textarea");
    area.value = text;
    area.setAttribute("readonly", "");
    area.style.position = "fixed";
    area.style.top = "-1000px";
    document.body.appendChild(area);
    area.select();
    var ok = false;
    try {
      ok = document.execCommand("copy");
    } catch (err) {
      ok = false;
    }
    document.body.removeChild(area);
    return ok;
  }

  function setupCopy() {
    bindCopy("copy-cli", "cli-command");
    bindCopy("copy-modal-cli", "modal-cli-command");
  }

  function bindCopy(buttonId, blockId) {
    var button = document.getElementById(buttonId);
    var block = document.getElementById(blockId);
    if (!button || !block) {
      return;
    }

    button.addEventListener("click", function () {
      var text = (block.innerText || block.textContent || "").replace(/\s+$/, "");

      function report(ok) {
        button.textContent = ok ? "Copied" : "Select and copy";
        window.setTimeout(function () {
          button.textContent = "Copy";
        }, 2000);
      }

      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(
          function () {
            report(true);
          },
          function () {
            report(legacyCopy(text));
          }
        );
        return;
      }

      report(legacyCopy(text));
    });
  }

  /* ------------------------------------------------------------- try modal
     A single modal opened from the hero and the index nav. It is a plain
     dialog: focus moves in, Escape and the scrim close it, Tab is trapped
     inside, and focus returns to whichever button opened it. */

  var FOCUSABLE =
    'a[href], button:not([disabled]), input:not([disabled]), ' +
    'select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

  var lastTrigger = null;

  function focusableIn(panel) {
    var nodes = panel.querySelectorAll(FOCUSABLE);
    var out = [];
    for (var i = 0; i < nodes.length; i += 1) {
      if (nodes[i].offsetParent !== null || nodes[i] === document.activeElement) {
        out.push(nodes[i]);
      }
    }
    return out;
  }

  function openModal(trigger) {
    var modal = document.getElementById("try-modal");
    var panel = document.getElementById("try-modal-panel");
    if (!modal || !panel) {
      return;
    }

    lastTrigger = trigger || null;
    modal.hidden = false;
    modal.classList.add("is-open");
    document.body.classList.add("modal-open");

    var target = focusableIn(panel)[0] || panel;
    target.focus();
  }

  function closeModal() {
    var modal = document.getElementById("try-modal");
    if (!modal || modal.hidden) {
      return;
    }

    modal.classList.remove("is-open");
    modal.hidden = true;
    document.body.classList.remove("modal-open");

    if (lastTrigger && typeof lastTrigger.focus === "function") {
      lastTrigger.focus();
    }
    lastTrigger = null;
  }

  function setupModal() {
    var modal = document.getElementById("try-modal");
    var panel = document.getElementById("try-modal-panel");
    var close = document.getElementById("try-modal-close");
    if (!modal || !panel) {
      return;
    }

    var openers = document.querySelectorAll(".try-btn");
    for (var i = 0; i < openers.length; i += 1) {
      openers[i].addEventListener("click", function (event) {
        openModal(event.currentTarget);
      });
    }

    if (close) {
      close.addEventListener("click", closeModal);
    }

    modal.addEventListener("click", function (event) {
      if (event.target === modal || event.target.getAttribute("data-modal-dismiss")) {
        closeModal();
      }
    });

    document.addEventListener("keydown", function (event) {
      if (modal.hidden) {
        return;
      }

      if (event.key === "Escape") {
        event.preventDefault();
        closeModal();
        return;
      }

      if (event.key !== "Tab") {
        return;
      }

      var nodes = focusableIn(panel);
      if (nodes.length === 0) {
        event.preventDefault();
        panel.focus();
        return;
      }

      var first = nodes[0];
      var last = nodes[nodes.length - 1];
      var active = document.activeElement;

      if (event.shiftKey) {
        if (active === first || !panel.contains(active)) {
          event.preventDefault();
          last.focus();
        }
        return;
      }

      if (active === last || !panel.contains(active)) {
        event.preventDefault();
        first.focus();
      }
    });
  }

  function start() {
    startHero();
    renderExtensionCta();
    setupCopy();
    setupModal();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();
