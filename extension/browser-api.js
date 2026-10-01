/* Cheta extension: the one place where Chromium and Firefox differences live.
 *
 * Load this before sidepanel.js in sidepanel.html. background.js pulls it in
 * too: Chromium with importScripts(), Firefox by listing it first in
 * background.scripts.
 *
 * It exposes exactly one global, globalThis.ChetaBrowserApi. Every extension
 * API call in this folder goes through that object, so no other file has to
 * know which browser it is running under:
 *
 *   - Firefox exposes browser.* (always promises) and chrome.* (callbacks).
 *     Chromium exposes chrome.*, callback-first, with promises on newer
 *     builds. The layer prefers browser.* and normalises both conventions.
 *   - Chromium side panel is chrome.sidePanel plus the "side_panel" manifest
 *     key. Firefox replaces both with browser.sidebarAction plus the
 *     "sidebar_action" manifest key. There is no Firefox equivalent of
 *     setPanelBehavior, so that difference is exposed as a capability here and
 *     handled in background.js.
 *   - storage.local get/set, tabs.query, tabs.create and
 *     scripting.executeScript are wrapped so call sites only ever see
 *     promises, whichever calling convention the browser uses underneath.
 *
 * ASCII only by policy: no emojis, no smart punctuation.
 */
(function (global) {
  "use strict";

  var browserNamespace =
    typeof global.browser !== "undefined" && global.browser ? global.browser : null;
  var chromeNamespace =
    typeof global.chrome !== "undefined" && global.chrome ? global.chrome : null;
  var namespace = browserNamespace || chromeNamespace || null;

  /* browser.* returns promises and ignores callbacks. chrome.* is callback
   * based (and calls chrome.runtime.lastError), so when only chrome is present
   * the callback form is used. Feature detection is on the namespace, never on
   * a user-agent string. */
  var prefersPromises = Boolean(browserNamespace);

  function runtimeLastError() {
    if (!chromeNamespace || !chromeNamespace.runtime) {
      return null;
    }
    var error = chromeNamespace.runtime.lastError;
    if (!error) {
      return null;
    }
    return new Error(error.message || "The browser reported an extension API error.");
  }

  /* Run one API call and settle a promise for it, accepting either the
   * promise-returning or the callback-and-lastError convention. */
  function invoke(fn, thisArg, args) {
    return new Promise(function (resolve, reject) {
      var settled = false;

      function succeed(value) {
        if (!settled) {
          settled = true;
          resolve(value);
        }
      }

      function fail(error) {
        if (!settled) {
          settled = true;
          reject(error);
        }
      }

      var returned;
      try {
        if (prefersPromises) {
          returned = fn.apply(thisArg, args);
        } else {
          returned = fn.apply(
            thisArg,
            args.concat([
              function (value) {
                var error = runtimeLastError();
                if (error) {
                  fail(error);
                } else {
                  succeed(value);
                }
              }
            ])
          );
        }
      } catch (error) {
        fail(error);
        return;
      }

      if (returned && typeof returned.then === "function") {
        returned.then(succeed, fail);
      } else if (prefersPromises) {
        /* The promise-returning namespace gave us nothing to wait on. */
        succeed(returned);
      }
    });
  }

  /* ------------------------------------------------------------- storage */

  var storageArea =
    namespace && namespace.storage && namespace.storage.local ? namespace.storage.local : null;

  function hasExtensionStorage() {
    return Boolean(storageArea && typeof storageArea.get === "function");
  }

  /* Falls back to window.localStorage when the file is opened outside an
   * extension context (development and screenshots), which is behaviour the
   * panel had before this layer existed. */
  function storageGet(keys) {
    if (hasExtensionStorage()) {
      return invoke(storageArea.get, storageArea, [keys]).then(function (items) {
        return items || {};
      });
    }
    var out = {};
    var list = Array.isArray(keys) ? keys : [keys];
    try {
      list.forEach(function (key) {
        var raw = global.localStorage.getItem(key);
        if (raw !== null) {
          try {
            out[key] = JSON.parse(raw);
          } catch (parseError) {
            out[key] = raw;
          }
        }
      });
    } catch (error) {
      /* Storage disabled: state stays in memory only. */
    }
    return Promise.resolve(out);
  }

  function storageSet(values) {
    if (hasExtensionStorage()) {
      return invoke(storageArea.set, storageArea, [values]).then(function () {
        return undefined;
      });
    }
    try {
      Object.keys(values).forEach(function (key) {
        global.localStorage.setItem(key, JSON.stringify(values[key]));
      });
    } catch (error) {
      /* Storage disabled: state stays in memory only. */
    }
    return Promise.resolve();
  }

  /* ---------------------------------------------------------------- tabs */

  var tabsApi = namespace && namespace.tabs ? namespace.tabs : null;

  function tabsQuery(queryInfo) {
    if (!tabsApi || typeof tabsApi.query !== "function") {
      return Promise.reject(
        new Error("This browser does not expose tabs to the extension.")
      );
    }
    return invoke(tabsApi.query, tabsApi, [queryInfo]);
  }

  function tabsCreate(createProperties) {
    if (!tabsApi || typeof tabsApi.create !== "function") {
      return Promise.reject(
        new Error("This browser does not expose tabs to the extension.")
      );
    }
    return invoke(tabsApi.create, tabsApi, [createProperties]);
  }

  /* ----------------------------------------------------------- scripting */

  var scriptingApi = namespace && namespace.scripting ? namespace.scripting : null;

  function executeScript(injection) {
    if (!scriptingApi || typeof scriptingApi.executeScript !== "function") {
      return Promise.reject(
        new Error("This browser build does not support reading page text.")
      );
    }
    return invoke(scriptingApi.executeScript, scriptingApi, [injection]);
  }

  /* ------------------------------------------------------------- sidebar */

  var sidePanelApi = namespace && namespace.sidePanel ? namespace.sidePanel : null;
  var sidebarActionApi =
    namespace && namespace.sidebarAction ? namespace.sidebarAction : null;

  function hasSidePanel() {
    return Boolean(sidePanelApi);
  }

  function canSetPanelBehavior() {
    return Boolean(
      sidePanelApi && typeof sidePanelApi.setPanelBehavior === "function"
    );
  }

  function canOpenSidePanel() {
    return Boolean(sidePanelApi && typeof sidePanelApi.open === "function");
  }

  function hasSidebarAction() {
    return Boolean(sidebarActionApi);
  }

  function setPanelBehavior(options) {
    if (!canSetPanelBehavior()) {
      return Promise.reject(
        new Error("setPanelBehavior is not available in this browser.")
      );
    }
    return invoke(sidePanelApi.setPanelBehavior, sidePanelApi, [options]);
  }

  function openSidePanel(options) {
    if (!canOpenSidePanel()) {
      return Promise.reject(
        new Error("The side panel API is not available in this browser.")
      );
    }
    return invoke(sidePanelApi.open, sidePanelApi, [options]);
  }

  function openSidebar() {
    if (!sidebarActionApi || typeof sidebarActionApi.open !== "function") {
      return Promise.reject(
        new Error("The sidebar API is not available in this browser.")
      );
    }
    return invoke(sidebarActionApi.open, sidebarActionApi, []);
  }

  /* sidebarAction.toggle() exists in recent Firefox; open() covers the rest.
   * Both require a user action, so call this from a click or command handler. */
  function toggleSidebar() {
    if (sidebarActionApi && typeof sidebarActionApi.toggle === "function") {
      return invoke(sidebarActionApi.toggle, sidebarActionApi, []);
    }
    return openSidebar();
  }

  global.ChetaBrowserApi = {
    /* The raw namespace, for events and anything not wrapped below. */
    namespace: namespace,
    prefersPromises: prefersPromises,
    hasExtensionStorage: hasExtensionStorage,
    storageGet: storageGet,
    storageSet: storageSet,
    tabsQuery: tabsQuery,
    tabsCreate: tabsCreate,
    executeScript: executeScript,
    hasSidePanel: hasSidePanel,
    canSetPanelBehavior: canSetPanelBehavior,
    canOpenSidePanel: canOpenSidePanel,
    hasSidebarAction: hasSidebarAction,
    setPanelBehavior: setPanelBehavior,
    openSidePanel: openSidePanel,
    openSidebar: openSidebar,
    toggleSidebar: toggleSidebar
  };
})(globalThis);
