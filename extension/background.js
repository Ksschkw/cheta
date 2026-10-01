/* Ranti extension background service worker.
 *
 * Its only job is to make the toolbar button open the side panel, which is how
 * a person reaches the extension surface. No network access happens here, so
 * the panel still works if the service worker is asleep and gets woken.
 *
 * ASCII only by policy: no emojis, no smart punctuation.
 */

"use strict";

/* openPanelOnActionClick is the supported MV3 path in Chrome and Edge. It is
 * unavailable on very old builds, so it is feature-detected and the click
 * fallback below covers the rest. */
function enableActionOpensPanel() {
  if (!chrome.sidePanel || typeof chrome.sidePanel.setPanelBehavior !== "function") {
    return;
  }
  var result = chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true });
  if (result && typeof result.catch === "function") {
    result.catch(function () {
      /* The toolbar button keeps its native behavior; the fallback handles it. */
    });
  }
}

chrome.runtime.onInstalled.addListener(enableActionOpensPanel);
chrome.runtime.onStartup.addListener(enableActionOpensPanel);
enableActionOpensPanel();

/* Fallback for builds without setPanelBehavior: open the panel for the window
 * that owns the clicked tab. When setPanelBehavior succeeded this listener is
 * never invoked. */
if (chrome.action && chrome.action.onClicked) {
  chrome.action.onClicked.addListener(function (tab) {
    if (!chrome.sidePanel || typeof chrome.sidePanel.open !== "function") {
      return;
    }
    if (!tab || typeof tab.windowId !== "number") {
      return;
    }
    var opened = chrome.sidePanel.open({ windowId: tab.windowId });
    if (opened && typeof opened.catch === "function") {
      opened.catch(function () {
        /* Nothing else to try; the platform refused to open the panel. */
      });
    }
  });
}
