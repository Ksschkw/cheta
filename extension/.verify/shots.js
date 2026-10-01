'use strict';
const { spawn } = require('child_process');
const fs = require('fs');
const path = require('path');
const CHROME = '/home/ksschkw/.cache/ms-playwright/chromium_headless_shell-1234/chrome-headless-shell-linux64/chrome-headless-shell';
const PORT = 9412;
const V = __dirname;
const PAGE = 'file:///home/ksschkw/kss/IDK/extension/sidepanel.html';
const profile = path.join(V, 'profile-shots');
fs.rmSync(profile, { recursive: true, force: true });
fs.mkdirSync(path.join(V, 'home'), { recursive: true });

const proc = spawn(CHROME, [
  '--no-sandbox', '--disable-gpu', '--disable-dev-shm-usage',
  '--remote-debugging-port=' + PORT,
  '--user-data-dir=' + profile,
  '--allow-file-access-from-files',
  '--window-size=320,720',
  'about:blank'
], {
  stdio: ['ignore', 'pipe', 'pipe'],
  env: Object.assign({}, process.env, {
    HOME: path.join(V, 'home'),
    XDG_CONFIG_HOME: path.join(V, 'home', '.config'),
    XDG_CACHE_HOME: path.join(V, 'home', '.cache'),
    XDG_RUNTIME_DIR: path.join(V, 'home', 'run')
  })
});
proc.stderr.on('data', () => {});
const wait = (ms) => new Promise((r) => setTimeout(r, ms));

let id = 0;
const pending = new Map();
function makeSend(ws) {
  ws.onmessage = (ev) => {
    const m = JSON.parse(ev.data);
    if (m.id && pending.has(m.id)) {
      const p = pending.get(m.id);
      pending.delete(m.id);
      if (m.error) p.rej(new Error(JSON.stringify(m.error)));
      else p.res(m.result);
    }
  };
  return function send(method, params, sessionId) {
    return new Promise((res, rej) => {
      const i = ++id;
      pending.set(i, { res, rej });
      const o = { id: i, method, params: params || {} };
      if (sessionId) o.sessionId = sessionId;
      ws.send(JSON.stringify(o));
    });
  };
}

function injected(params) {
  try {
    try { localStorage.clear(); } catch (e) { window.__lsError = String(e); }
    if (params.storage) {
      Object.keys(params.storage).forEach(function (k) {
        localStorage.setItem(k, JSON.stringify(params.storage[k]));
      });
    }
    window.__lsOk = (function () { try { localStorage.setItem('t', '1'); return localStorage.getItem('t'); } catch (e) { return 'fail:' + e.message; } })();
  } catch (e) { window.__lsError = String(e); }
  window.__requests = [];
  function resp(obj, status) {
    var body = typeof obj === 'string' ? obj : JSON.stringify(obj);
    var code = status || 200;
    return {
      ok: code >= 200 && code < 300,
      status: code,
      type: 'basic',
      headers: { get: function () { return 'application/json'; } },
      text: function () { return Promise.resolve(body); },
      json: function () { return Promise.resolve(JSON.parse(body)); }
    };
  }
  window.fetch = function (url, init) {
    var u = String(url);
    var method = (init && init.method) || 'GET';
    window.__requests.push({ url: u, method: method, body: init && init.body });
    if (u.indexOf('/health') >= 0) {
      return Promise.resolve(resp({ status: 'ok', memory: { degraded: false } }));
    }
    if (u.indexOf('/chat/turn') >= 0) {
      var body = {};
      try { body = JSON.parse(init.body); } catch (e) {}
      var text = body.text || '';
      var first = text.trim().split(/\s+/)[0] || '';
      if (first.charAt(0) === '/' && text.trim().indexOf(' ') < 0) {
        return Promise.resolve(resp({
          turn_id: '', user_id: 'user-1',
          reply: 'Your pairing code is ABCD2345\n\nEnter this on the other client: /pair ABCD2345\n\nIt works once and expires in 15 minutes.',
          recalled: [], stored_facts: [], memory_degraded: false,
          provider: 'command', command: first
        }));
      }
      return Promise.resolve(resp({
        turn_id: 'turn-1', user_id: 'user-1',
        reply: 'The page explains that a river delta forms where a river meets standing water and drops its sediment. The shape depends on sediment load, wave energy, and tide range.',
        recalled: [{ text: 'You are a software engineering student at FUTO', importance: 0.9 }],
        stored_facts: [], memory_degraded: false, provider: 'stub'
      }));
    }
    if (u.indexOf('/chat/counterfactual/') >= 0) {
      return Promise.resolve(resp({ with_memory: 'with', without_memory: 'without', summary: 'diff' }));
    }
    if (u.indexOf('/memories/') >= 0) {
      return Promise.resolve(resp([]));
    }
    if (u.indexOf('/.well-known/mcp.json') >= 0 || /\/mcp$/.test(u)) {
      if (params.mcp === 'none') { return Promise.resolve(resp('', 404)); }
      if (method === 'POST') {
        var b = {};
        try { b = JSON.parse(init.body); } catch (e) {}
        if (b.method === 'tools/list') {
          return Promise.resolve(resp({ jsonrpc: '2.0', id: 1, result: { tools: params.tools || [] } }));
        }
        if (b.method === 'tools/call') {
          return Promise.resolve(resp({ jsonrpc: '2.0', id: 2, result: { content: [{ type: 'text', text: 'Tool output: the delta covers about 1200 square kilometres.' }] } }));
        }
      }
      return Promise.resolve(resp('', 405));
    }
    return Promise.resolve(resp('', 404));
  };
}

const PAGE_TEXT = 'A river delta forms where a river enters standing water and drops its sediment. ' +
  'The delta grows seaward as new sediment arrives. Its shape depends on the balance between ' +
  'sediment supply, wave energy and tidal range. ' .repeat(8);

const lsKeys = {
  baseUrl: 'ranti.extension.base_url',
  surfaceUserId: 'ranti.extension.surface_user_id',
  displayName: 'ranti.extension.display_name',
  userId: 'ranti.extension.user_id',
  memoryEnabled: 'ranti.extension.memory_enabled',
  history: 'ranti.extension.history',
  onboarded: 'ranti.extension.onboarded',
  pageHintSeen: 'ranti.extension.page_hint_seen'
};

const baseStorage = {
  [lsKeys.onboarded]: true,
  [lsKeys.surfaceUserId]: 'extension-verify',
  [lsKeys.userId]: 'user-1',
  [lsKeys.displayName]: 'Verify'
};

const tools = [
  { name: 'get_delta_area', description: 'Return the mapped area of the delta.', inputSchema: { type: 'object', properties: { units: { type: 'string' } } } },
  { name: 'list_gauges', description: 'List river gauges near this page.', inputSchema: { type: 'object', properties: {} } },
  { name: 'get_flow', description: 'Current river flow at a gauge.', inputSchema: { type: 'object', properties: { gauge: { type: 'string' } } } }
];

(async () => {
  let wsUrl = null;
  for (let i = 0; i < 80; i++) {
    try {
      const r = await fetch('http://127.0.0.1:' + PORT + '/json/version');
      const j = await r.json();
      wsUrl = j.webSocketDebuggerUrl;
      break;
    } catch (e) { await wait(200); }
  }
  if (!wsUrl) throw new Error('no ws');
  const ws = new WebSocket(wsUrl);
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
  const send = makeSend(ws);

  const list = await send('Target.getTargets');
  let pageTarget = list.targetInfos.find((t) => t.type === 'page');
  if (!pageTarget) {
    const c = await send('Target.createTarget', { url: 'about:blank' });
    pageTarget = { targetId: c.targetId };
  }
  const attach = await send('Target.attachToTarget', { targetId: pageTarget.targetId, flatten: true });
  const sid = attach.sessionId;
  await send('Page.enable', {}, sid);
  await send('Runtime.enable', {}, sid);
  await send('Emulation.setDeviceMetricsOverride', { width: 320, height: 720, deviceScaleFactor: 2, mobile: false }, sid);

  let injectedId = null;
  async function setScenario(params) {
    if (injectedId) { await send('Page.removeScriptToEvaluateOnNewDocument', { identifier: injectedId }, sid); injectedId = null; }
    const r = await send('Page.addScriptToEvaluateOnNewDocument', { source: '(' + injected.toString() + ')(' + JSON.stringify(params) + ');' }, sid);
    injectedId = r.identifier;
  }
  async function nav(tag) {
    await send('Page.navigate', { url: PAGE + '?s=' + tag }, sid);
    await wait(1100);
  }
  async function ev(expr) {
    const r = await send('Runtime.evaluate', { expression: expr, returnByValue: true, awaitPromise: true }, sid);
    if (r.exceptionDetails) throw new Error('eval: ' + JSON.stringify(r.exceptionDetails));
    return r.result.value;
  }
  async function shot(name) {
    const r = await send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: false }, sid);
    fs.writeFileSync(path.join(V, name), Buffer.from(r.data, 'base64'));
  }
  async function stubTab() {
    await ev("ChetaBrowserApi.tabsQuery=function(){return Promise.resolve([{id:1,url:'https://example.com/delta'}]);};" +
      "ChetaBrowserApi.executeScript=function(){return Promise.resolve([{result:{title:'How river deltas form',hostname:'example.com',origin:'https://example.com',text:" + JSON.stringify(PAGE_TEXT) + ",truncated:false,totalLength:" + PAGE_TEXT.length + "}}]);};'stubbed'");
  }
  const results = {};

  // Scenario 1: before page load, hint visible, no settings.
  await setScenario({ storage: baseStorage, mcp: 'none' });
  await nav('before');
  await ev("document.getElementById('chat-input').blur()");
  results.before = await ev("JSON.stringify({" +
    "lsOk: window.__lsOk," +
    "settings: !!document.getElementById('settings')," +
    "baseUrlText: document.body.innerText.indexOf('base URL') >= 0," +
    "hintShown: !document.getElementById('page-hint').classList.contains('hidden')," +
    "pageSummaryHidden: document.getElementById('page-summary').classList.contains('hidden')," +
    "actionsHidden: document.getElementById('page-actions').classList.contains('hidden')," +
    "transcriptH: document.getElementById('transcript').getBoundingClientRect().height," +
    "pageH: document.getElementById('page-context').getBoundingClientRect().height," +
    "docScrollW: document.documentElement.scrollWidth," +
    "bodyScrollW: document.body.scrollWidth," +
    "innerW: window.innerWidth" +
  "})");
  await shot('shot1-before.png');

  // Scenario 2: after page load, collapsed summary + no-tools line.
  await stubTab();
  await ev("document.getElementById('use-page').click(); 'clicked'");
  await wait(900);
  results.pageCollapsed = await ev("JSON.stringify({" +
    "summary: document.getElementById('page-summary').textContent," +
    "summaryHidden: document.getElementById('page-summary').classList.contains('hidden')," +
    "hintShown: !document.getElementById('page-hint').classList.contains('hidden')," +
    "useHidden: document.getElementById('use-page').classList.contains('hidden')," +
    "actionsHidden: document.getElementById('page-actions').classList.contains('hidden')," +
    "mcpSummary: document.getElementById('mcp-summary').textContent," +
    "pageH: document.getElementById('page-context').getBoundingClientRect().height," +
    "transcriptH: document.getElementById('transcript').getBoundingClientRect().height," +
    "docScrollW: document.documentElement.scrollWidth," +
    "bodyScrollW: document.body.scrollWidth" +
  "})");
  await shot('shot2-page-collapsed.png');

  // Scenario 3: action reply visible, page controls collapsed, transcript at bottom.
  await ev("document.getElementById('page-actions-toggle').click(); 'opened'");
  await wait(120);
  results.actionsOpen = await ev("JSON.stringify({open: !document.getElementById('page-actions').classList.contains('hidden'), pageH: document.getElementById('page-context').getBoundingClientRect().height})");
  await ev("document.getElementById('page-action-summarise').click(); 'run'");
  await wait(1200);
  results.actionReply = await ev("JSON.stringify({" +
    "actionsHidden: document.getElementById('page-actions').classList.contains('hidden')," +
    "ariaExpanded: document.getElementById('page-actions-toggle').getAttribute('aria-expanded')," +
    "lastMsg: (function(){var n=document.querySelector('#transcript .msg.assistant .bubble');return n?n.textContent.slice(0,60):'NONE';})()," +
    "scrollTop: document.getElementById('transcript').scrollTop," +
    "scrollMax: document.getElementById('transcript').scrollHeight - document.getElementById('transcript').clientHeight," +
    "atBottom: Math.abs(document.getElementById('transcript').scrollTop - (document.getElementById('transcript').scrollHeight - document.getElementById('transcript').clientHeight)) < 4," +
    "pageH: document.getElementById('page-context').getBoundingClientRect().height," +
    "transcriptH: document.getElementById('transcript').getBoundingClientRect().height," +
    "docScrollW: document.documentElement.scrollWidth," +
    "bodyScrollW: document.body.scrollWidth" +
  "})");
  await shot('shot3-action-reply.png');

  // Scenario 4: slash suggestions open.
  await setScenario({ storage: baseStorage, mcp: 'none' });
  await nav('suggest');
  await ev("(function(){var i=document.getElementById('chat-input');i.focus();i.value='/';i.dispatchEvent(new Event('input',{bubbles:true}));return 'ok';})()");
  await wait(200);
  results.suggestions = await ev("JSON.stringify({" +
    "hidden: document.getElementById('suggestions').classList.contains('hidden')," +
    "count: document.querySelectorAll('#suggestions .suggestion').length," +
    "cmds: Array.prototype.map.call(document.querySelectorAll('#suggestions .suggestion-cmd'),function(n){return n.textContent;}).join(' | ')," +
    "descs: Array.prototype.map.call(document.querySelectorAll('#suggestions .suggestion-desc'),function(n){return n.textContent;}).join(' | ')," +
    "composerTop: document.getElementById('composer').getBoundingClientRect().top," +
    "suggTop: document.getElementById('suggestions').getBoundingClientRect().top," +
    "suggBottom: document.getElementById('suggestions').getBoundingClientRect().bottom," +
    "inputTop: document.getElementById('chat-input').getBoundingClientRect().top," +
    "docScrollW: document.documentElement.scrollWidth," +
    "bodyScrollW: document.body.scrollWidth" +
  "})");
  await shot('shot4-suggestions.png');
  // Arrow down then Enter accepts selection into the composer.
  await ev("(function(){var i=document.getElementById('chat-input');i.dispatchEvent(new KeyboardEvent('keydown',{key:'ArrowDown',bubbles:true}));return 'down';})()");
  await wait(120);
  await ev("(function(){var i=document.getElementById('chat-input');i.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true}));return 'enter';})()");
  await wait(150);
  results.suggestionAccept = await ev("JSON.stringify({value: document.getElementById('chat-input').value, suggHidden: document.getElementById('suggestions').classList.contains('hidden'), turns: document.querySelectorAll('#transcript .msg.user').length})");

  // Scenario 5: MCP tools discovered and expanded.
  await setScenario({ storage: baseStorage, mcp: 'tools', tools: tools });
  await nav('mcp');
  await stubTab();
  await ev("document.getElementById('use-page').click(); 'clicked'");
  await wait(1100);
  await ev("document.getElementById('mcp-tools-toggle').click(); 'open'");
  await wait(150);
  results.mcp = await ev("JSON.stringify({" +
    "mcpSummary: document.getElementById('mcp-summary').textContent," +
    "toolsHidden: document.getElementById('mcp-tools').classList.contains('hidden')," +
    "toolCount: document.querySelectorAll('#mcp-tools .mcp-tool').length," +
    "names: Array.prototype.map.call(document.querySelectorAll('#mcp-tools .mcp-tool-name'),function(n){return n.textContent;}).join(' | ')," +
    "pageH: document.getElementById('page-context').getBoundingClientRect().height," +
    "docScrollW: document.documentElement.scrollWidth," +
    "bodyScrollW: document.body.scrollWidth" +
  "})");
  await shot('shot5-mcp-tools.png');
  // Run a tool.
  await ev("document.querySelector('#mcp-tools .mcp-tool').click(); 'tool-open'");
  await wait(200);
  await ev("document.getElementById('mcp-run').click(); 'run'");
  await wait(1200);
  results.mcpRun = await ev("JSON.stringify({" +
    "contextNodes: document.querySelectorAll('#transcript .msg.context').length," +
    "toolCallSeen: window.__requests.filter(function(r){var b=r.body||'';return b.indexOf('tools/call')>=0;}).length," +
    "mcpArgsHidden: document.getElementById('mcp-args').classList.contains('hidden')," +
    "lastReply: (function(){var n=document.querySelector('#transcript .msg.assistant .bubble');return n?n.textContent.slice(0,40):'NONE';})()" +
  "})");
  await shot('shot6-mcp-result.png');

  // Scenario 6: a full seeded conversation at 320px.
  const hist = [];
  for (let i = 0; i < 4; i++) {
    hist.push({ role: 'user', text: 'Question number ' + (i + 1) + ' about deltas and sediment.' });
    hist.push({ role: 'assistant', usedMemory: true, turn: { turn_id: 't' + i, reply: 'Answer ' + (i + 1) + ': a delta forms where the river slows and drops sediment. '.repeat(4), recalled: [{ text: 'You live in Lagos and study at FUTO' }], memory_degraded: false } });
  }
  await setScenario({ storage: Object.assign({}, baseStorage, { [lsKeys.history]: hist }), mcp: 'none' });
  await nav('convo');
  await stubTab();
  await ev("document.getElementById('use-page').click(); 'clicked'");
  await wait(900);
  results.fullConvo = await ev("JSON.stringify({" +
    "transcriptH: document.getElementById('transcript').getBoundingClientRect().height," +
    "composerH: document.getElementById('composer').getBoundingClientRect().height," +
    "pageH: document.getElementById('page-context').getBoundingClientRect().height," +
    "topbarH: document.querySelector('.topbar').getBoundingClientRect().height," +
    "viewportH: window.innerHeight," +
    "docScrollW: document.documentElement.scrollWidth," +
    "bodyScrollW: document.body.scrollWidth," +
    "atBottom: Math.abs(document.getElementById('transcript').scrollTop - (document.getElementById('transcript').scrollHeight - document.getElementById('transcript').clientHeight)) < 4," +
    "msgs: document.querySelectorAll('#transcript .msg').length" +
  "})");
  await shot('shot7-full-convo-320.png');

  // Scenario 7: long conversation, then an action reply; newest reply must be visible.
  await setScenario({ storage: Object.assign({}, baseStorage, { [lsKeys.history]: hist }), mcp: 'tools', tools: tools });
  await nav('actionlong');
  await stubTab();
  await ev("document.getElementById('use-page').click(); 'clicked'");
  await wait(900);
  await ev("document.getElementById('page-actions-toggle').click(); 'open'");
  await wait(100);
  await ev("document.getElementById('page-action-summarise').click(); 'run'");
  await wait(1500);
  results.actionLong = await ev("JSON.stringify((function(){var t=document.getElementById('transcript');var nodes=document.querySelectorAll('#transcript .msg.assistant');var last=nodes[nodes.length-1];var r=last.getBoundingClientRect();var tr=t.getBoundingClientRect();return {scrollTop:Math.round(t.scrollTop),scrollMax:Math.round(t.scrollHeight-t.clientHeight),atBottom:Math.abs(t.scrollTop-(t.scrollHeight-t.clientHeight))<4,lastReplyTop:Math.round(r.top),lastReplyBottom:Math.round(r.bottom),transcriptTop:Math.round(tr.top),transcriptBottom:Math.round(tr.bottom),replyFullyVisible:r.bottom<=tr.bottom+1&&r.top>=tr.top-1,actionsHidden:document.getElementById('page-actions').classList.contains('hidden'),pageH:Math.round(document.getElementById('page-context').getBoundingClientRect().height),composerH:Math.round(document.getElementById('composer').getBoundingClientRect().height)};})())");
  await shot('shot8-action-scroll-long.png');

  console.log(JSON.stringify(results, null, 2));
  ws.close();
  proc.kill('SIGKILL');
})().catch((e) => { console.error('ERR', e); proc.kill('SIGKILL'); process.exit(1); });
