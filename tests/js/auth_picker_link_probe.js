// The sign-in screen's "Choose a different company →", on a fake page where
// the launcher's bridge (window.pywebview) arrives when the probe says so and
// announces itself with pywebviewready, as pywebview does: on macOS after
// the page has loaded, later than the screen is drawn.
//
//   node tests/js/auth_picker_link_probe.js
//
// Prints one JSON line per scenario:
//   {"scenario", "link_at_first_paint", "picker_calls_before_the_bridge",
//    "link_after_ready", "picker_calls_after_ready", "message"}
const fs = require('fs'), vm = require('vm');

const SRC = fs.readFileSync('app/static/js/auth.js', 'utf8');

// An element whose innerHTML is a string; the elements inside it are found by
// id, and each keeps its listeners across later insertions.
function fakeEl(tag) {
  const el = {
    tagName: tag, id: '', children: [], removed: false, textContent: '', value: '',
    disabled: false, listeners: {}, _html: '', _byId: {},
    get innerHTML() { return this._html; },
    set innerHTML(html) { this._html = html; this._index(); },
    _index() {
      for (const m of this._html.matchAll(/id="([^"]+)"/g)) {
        if (!this._byId[m[1]]) { const c = fakeEl('child'); c.id = m[1]; c.root = this; this._byId[m[1]] = c; }
      }
    },
    setAttribute(k, v) { if (k === 'id') this.id = v; },
    appendChild(c) { this.children.push(c); return c; },
    remove() { this.removed = true; },
    querySelector(sel) { return sel.startsWith('#') ? (this._byId[sel.slice(1)] || null) : null; },
    insertAdjacentHTML(_where, html) { const root = this.root || this; root._html += html; root._index(); },
    addEventListener(type, fn) { (this.listeners[type] = this.listeners[type] || []).push(fn); },
    click() { (this.listeners.click || []).forEach((fn) => fn({ preventDefault() {} })); },
    focus() {},
  };
  return el;
}

async function scenario(name, status, bridgeAtStart, bridgeArrives) {
  const body = fakeEl('body');
  const calls = [];
  const bridge = { api: { show_picker: () => { calls.push('show_picker'); return {}; } } };
  const winListeners = {};
  const timers = [];
  const win = {
    addEventListener(type, fn, opts) {
      (winListeners[type] = winListeners[type] || []).push({ fn, once: !!(opts && opts.once) });
    },
    dispatchEvent(type) {
      const list = winListeners[type] || [];
      winListeners[type] = list.filter((l) => !l.once);
      list.forEach((l) => l.fn());
    },
  };
  if (bridgeAtStart) win.pywebview = bridge;
  const ctx = {
    console, JSON, Promise,
    window: win,
    setTimeout: (fn, ms) => timers.push({ fn, ms }), // time moves when the probe says
    fetch: async () => ({ ok: true, json: async () => status }),
    document: {
      body,
      createElement: fakeEl,
      addEventListener() {},
      getElementById(id) {
        if (id === 'status-text' || id === 'status-company') return fakeEl('span');
        return body.children.find((c) => c.id === id && !c.removed) || null;
      },
    },
  };
  vm.createContext(ctx);
  vm.runInContext(SRC, ctx);

  await win.SlowbooksAuth.promptAuth();
  const overlay = () => body.children.find((c) => !c.removed);
  const link = () => overlay() && overlay().querySelector('#auth-switch-company');
  const hasLink = () => !!(link() && overlay().innerHTML.includes('Choose a different company →'));
  const out = { scenario: name, link_at_first_paint: hasLink() };
  if (link()) link().click(); // clicked before the bridge is there
  out.picker_calls_before_the_bridge = calls.length;

  if (bridgeArrives) {
    win.pywebview = bridge; // the bridge arrives, and says so
    win.dispatchEvent('pywebviewready');
  }
  out.link_after_ready = hasLink();
  if (!out.link_at_first_paint && link()) link().click();
  out.picker_calls_after_ready = calls.length;
  timers.forEach((t) => t.fn()); // five seconds later
  const err = overlay() && overlay().querySelector('#auth-error');
  out.message = err ? err.textContent : '';
  console.log(JSON.stringify(out));
}

const signIn = { authenticated: false, setup_needed: false, multi_user: false, company_name: 'Harbor Light Bakery Two' };
(async () => {
  // macOS: the app's own window, the bridge injected after the page loaded
  await scenario('mac-window', Object.assign({ desktop: true }, signIn), false, true);
  // a server that did not say (no flag), in a window whose bridge comes late
  await scenario('late-bridge-no-flag', signIn, false, true);
  // Windows: the bridge is there before the screen is drawn
  await scenario('bridge-first', Object.assign({ desktop: true }, signIn), true, true);
  // a browser on Server Edition: no window, no bridge, no picker
  await scenario('browser', Object.assign({ desktop: false }, signIn), false, false);
  // a window whose bridge never comes (a server started without one)
  await scenario('no-bridge-ever', Object.assign({ desktop: true }, signIn), false, false);
})();
