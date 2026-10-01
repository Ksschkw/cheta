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
    var button = document.getElementById("copy-cli");
    var block = document.getElementById("cli-command");
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

  function start() {
    startHero();
    renderExtensionCta();
    setupCopy();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();
