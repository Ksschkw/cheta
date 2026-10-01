/* Cheta extension background context.
 *
 * Chromium loads this as an MV3 service worker, so there is no document and no
 * localStorage here. Firefox MV3 does not support service workers, so its
 * manifest loads browser-api.js and this file as an event page instead. The
 * importScripts guard below makes the Chromium case work without the Firefox
 * case loading the compat layer twice.
 *
 * The only job in Chromium is to make the toolbar button open the side panel.
 * Firefox has no equivalent: there is no setPanelBehavior and the sidebar is
 * chosen from the browser's own Sidebar UI, so nothing here pretends an action
 * click can behave the Chromium way. Firefox instead gets one explicit user
 * action, the keyboard command declared in manifest.firefox.json, which opens
 * or toggles the sidebar with sidebarAction.toggle(). Opening the sidebar that
 * way also grants activeTab for the tab in front, which is what the page agent
 * needs.
 *
 * ASCII only by policy: no emojis, no smart punctuation.
 */

"use strict";

if (!globalThis.ChetaBrowserApi && typeof importScripts === "function") {
  importScripts("browser-api.js");
}

(function () {
  var api = globalThis.ChetaBrowserApi;
  if (!api || !api.namespace) {
    return;
  }
  var ns = api.namespace;

  function addRuntimeListener(name, listener) {
    var event = ns.runtime ? ns.runtime[name] : null;
    if (event && typeof event.addListener === "function") {
      event.addListener(listener);
    }
  }

  /* Fallback click path for Chromium builds where setPanelBehavior is missing
   * or refused: open the panel for the window that owns the clicked tab. When
   * setPanelBehavior succeeded this listener is never invoked. */
  function bindActionFallback() {
    if (!ns.action || !ns.action.onClicked) {
      return;
    }
    ns.action.onClicked.addListener(function (tab) {
      if (!api.canOpenSidePanel() || !tab || typeof tab.windowId !== "number") {
        return;
      }
      api.openSidePanel({ windowId: tab.windowId }).catch(function () {
        /* Nothing else to try; the platform refused to open the panel. */
      });
    });
  }

  function enableActionOpensPanel() {
    api.setPanelBehavior({ openPanelOnActionClick: true }).catch(function () {
      /* Older builds: the toolbar button keeps native behavior and the
       * fallback click handler covers opening the panel. */
    });
  }

  /* Chromium behaviour, chosen because chrome.sidePanel.setPanelBehavior is
   * present. This is capability detection, not a user-agent check. */
  function setupChromiumSidePanel() {
    addRuntimeListener("onInstalled", enableActionOpensPanel);
    addRuntimeListener("onStartup", enableActionOpensPanel);
    enableActionOpensPanel();
    bindActionFallback();
  }

  /* Chromium build that has sidePanel.open but not setPanelBehavior. */
  function setupChromiumSidePanelFallback() {
    bindActionFallback();
  }

  /* Firefox behaviour, chosen because browser.sidebarAction is present and
   * setPanelBehavior is not. Deliberately no action-click handler: Firefox
   * cannot open the sidebar from a toolbar click the way Chromium can, and the
   * manifest declares the sidebar itself, so an action is not needed. */
  function setupFirefoxSidebar() {
    if (!ns.commands || !ns.commands.onCommand) {
      return;
    }
    ns.commands.onCommand.addListener(function (command) {
      if (command !== "open-cheta-sidebar") {
        return;
      }
      /* Called synchronously from the command handler so Firefox still counts
       * it as a user action, which sidebarAction.toggle() requires. */
      api.toggleSidebar().catch(function () {
        /* The browser refused to toggle the sidebar; the Sidebar menu still
         * lets the person open it by hand. */
      });
    });
  }

  if (api.canSetPanelBehavior()) {
    setupChromiumSidePanel();
  } else if (api.hasSidebarAction()) {
    setupFirefoxSidebar();
  } else if (api.canOpenSidePanel()) {
    setupChromiumSidePanelFallback();
  }
})();
