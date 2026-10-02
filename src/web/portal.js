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
    var isEdge = /Edg\//i.test(ua) || /EdgA\//i.test(ua) || /EdgiOS\//i.test(ua);
    var isOpera = /OPR\//i.test(ua) || /OPiOS\//i.test(ua);
    var isFirefox = /Firefox\//i.test(ua) || /FxiOS\//i.test(ua);
    var isBrave = Boolean(navigator.brave && typeof navigator.brave.isBrave === "function");
    var isChrome = !isEdge && !isOpera && !isBrave && /Chrome\//i.test(ua);

    if (isFirefox) return "firefox";
    if (isEdge) return "edge";
    if (isBrave) return "brave";
    if (isOpera) return "opera";
    if (isChrome) return "chrome";
    if (/WebKit|Safari|Chromium/i.test(ua)) return "chromium";
    return "other";
  }

  function getBrowserMeta() {
    var b = detectBrowser();
    if (b === "firefox") {
      return {
        name: "Firefox",
        downloadUrl: "/app/downloads/cheta-firefox.xpi",
        filename: "cheta-firefox.xpi",
        instructionsUrl: "about:debugging#/runtime/this-firefox",
        urlName: "about:debugging"
      };
    }
    if (b === "edge") {
      return {
        name: "Edge",
        downloadUrl: "/app/downloads/cheta-chrome.zip",
        filename: "cheta-chrome.zip",
        instructionsUrl: "edge://extensions",
        urlName: "edge://extensions"
      };
    }
    if (b === "brave") {
      return {
        name: "Brave",
        downloadUrl: "/app/downloads/cheta-chrome.zip",
        filename: "cheta-chrome.zip",
        instructionsUrl: "brave://extensions",
        urlName: "brave://extensions"
      };
    }
    if (b === "opera") {
      return {
        name: "Opera",
        downloadUrl: "/app/downloads/cheta-chrome.zip",
        filename: "cheta-chrome.zip",
        instructionsUrl: "opera://extensions",
        urlName: "opera://extensions"
      };
    }
    return {
      name: "Chrome",
      downloadUrl: "/app/downloads/cheta-chrome.zip",
      filename: "cheta-chrome.zip",
      instructionsUrl: "chrome://extensions",
      urlName: "chrome://extensions"
    };
  }

  function makeDownloadButton(meta, customClass) {
    var btn = document.createElement("a");
    btn.className = "btn btn-accent " + (customClass || "");
    btn.href = meta.downloadUrl;
    btn.setAttribute("download", meta.filename);
    btn.innerHTML = "<span>Install for " + meta.name + "</span> <span class=\"ext-icon\">📥</span>";
    return btn;
  }

  function renderExtensionCta() {
    var meta = getBrowserMeta();

    // 1. Hero extension pill label
    var heroExtLabel = document.getElementById("hero-ext-label");
    if (heroExtLabel) {
      heroExtLabel.textContent = "Extension for " + meta.name;
    }
    var heroExtBtn = document.getElementById("hero-ext-btn");
    if (heroExtBtn) {
      heroExtBtn.addEventListener("click", function () {
        openModal();
        var extCard = document.querySelector(".surface-extension");
        if (extCard) {
          extCard.scrollIntoView({ behavior: "smooth" });
        }
      });
    }

    // 2. Section 04 card
    var host = document.getElementById("extension-cta");
    if (host) {
      host.textContent = "";
      var btn = makeDownloadButton(meta, "btn-oneclick");
      host.appendChild(btn);

      var guideLink = document.createElement("a");
      guideLink.className = "btn btn-quiet";
      guideLink.href = HELP_URL;
      guideLink.target = "_blank";
      guideLink.rel = "noopener noreferrer";
      guideLink.textContent = "Step-by-step Guide ↗";
      host.appendChild(guideLink);
    }

    // 3. Modal extension container
    var modalHost = document.getElementById("modal-extension-cta");
    if (modalHost) {
      modalHost.textContent = "";
      var mBtn = makeDownloadButton(meta, "btn-oneclick");
      mBtn.addEventListener("click", function () {
        var guide = document.getElementById("modal-extension-guide");
        if (guide) {
          guide.classList.remove("hidden");
        }
      });
      modalHost.appendChild(mBtn);

      var mHelp = document.createElement("a");
      mHelp.className = "btn btn-quiet";
      mHelp.href = HELP_URL;
      mHelp.target = "_blank";
      mHelp.rel = "noopener noreferrer";
      mHelp.textContent = "Guide ↗";
      modalHost.appendChild(mHelp);
    }

    var extUrlEl = document.getElementById("browser-ext-url");
    if (extUrlEl) {
      extUrlEl.textContent = meta.urlName;
    }
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
