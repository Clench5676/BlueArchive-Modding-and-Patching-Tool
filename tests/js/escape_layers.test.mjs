// Escape belongs to the top layer: a message over a fullscreen view (detail.js) or over the camera (photography/
// controller.js, a capture error) closes alone, and the view under it stays until the next Escape. The real popup
// stack (web/js/popup.js) on a stand-in DOM; the screens' key handlers are taken from their sources.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import { test } from "node:test";

class El {
  constructor(tag = "div") {
    Object.assign(this, { tagName: tag.toUpperCase(), children: [], parentNode: null, style: {}, dataset: {}, attrs: {}, listeners: {},
      hidden: false, inert: false, className: "", id: "", tabIndex: -1 });
  }
  appendChild(child) { child.parentNode = this; this.children.push(child); return child; }
  contains(node) { for (let x = node; x; x = x.parentNode) if (x === this) return true; return false; }
  get isConnected() { return body.contains(this); }
  hasAttribute(k) { return k in this.attrs; }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  getAttribute(k) { return this.attrs[k] ?? null; }
  addEventListener() {}
  focus() { document.activeElement = this; }
  querySelector() { return null; }
  getContext() { return { font: "", measureText: (s) => ({ width: s.length * 10 }) }; }
}
const body = new El("body");
globalThis.document = { body, activeElement: body, documentElement: { lang: "en-US", appendChild() {} },
  createElement: (tag) => new El(tag), getElementById: () => null, querySelector: () => null };
// the window's key listeners in the browser's order: capture ones first, then the bubbling ones, each in the order added
const listeners = [];
globalThis.addEventListener = (type, fn, options) => { if (type === "keydown") listeners.push({ fn, capture: options === true || !!options?.capture }); };
globalThis.removeEventListener = () => {};
function press(key) {
  let stopped = false;
  const ev = { key, defaultPrevented: false, preventDefault() { this.defaultPrevented = true; },
    stopPropagation() { stopped = true; }, stopImmediatePropagation() { stopped = true; } };
  for (const { fn } of [...listeners.filter((l) => l.capture), ...listeners.filter((l) => !l.capture)]) { if (stopped) break; fn(ev); }
  return ev;
}
globalThis.fetch = async () => ({ ok: false, status: 404, json: async () => null });
const Popup = await import("../../src/ba_modding_toolkit/web/js/popup.js");

// a message on the popup stack (the #dialog host); Escape closes it through the stack
function message() {
  const host = body.appendChild(new El());
  const closes = [];
  Popup.opened(host, (how) => { closes.push(how); host.hidden = true; Popup.closed(host); });
  return { host, closes };
}

const detail = readFileSync(new URL("../../src/ba_modding_toolkit/web/js/detail.js", import.meta.url), "utf8");
const detailKeys = detail.slice(detail.indexOf("// Escape leaves fullscreen only when"), detail.indexOf('addEventListener("keyup"'));
function detailFullscreen() {
  const calls = [];
  const env = { addEventListener: globalThis.addEventListener, popupOpen: Popup.anyOpen, open: true, fullscreen: true,
    Visual: { isOpen: () => false }, photoSession: false, setFullscreen: async (on) => { calls.push(on); },
    deltaOf: () => 0, typing: () => false, blocked: () => false, changing: () => false, keySteps: {} };
  const before = listeners.length;
  vm.runInNewContext(detailKeys, env);
  assert.equal(listeners.length, before + 1, "the slice adds the detail screen's key handler");
  const handler = listeners.at(-1);
  return { calls, handler, dispose: () => listeners.splice(listeners.indexOf(handler), 1) };
}

test("Escape on a message over a fullscreen view closes the message only; the next one leaves fullscreen", () => {
  const view = detailFullscreen();
  try {
    const shown = message();
    const first = press("Escape");
    assert.deepEqual(shown.closes, ["escape"]);
    assert.ok(first.defaultPrevented);
    assert.deepEqual(view.calls, [], "the fullscreen view stays under the message");
    press("Escape");
    assert.deepEqual(view.calls, [false]);
    assert.deepEqual(shown.closes, ["escape"]);
  } finally { view.dispose(); }
});

test("with a popup open the fullscreen handler does nothing, whatever order the listeners run in", () => {
  const view = detailFullscreen();
  try {
    const shown = message();
    view.handler.fn({ key: "Escape", defaultPrevented: false, preventDefault() {} });
    assert.deepEqual(view.calls, []);
    assert.deepEqual(shown.closes, []);
    press("Escape");
    assert.deepEqual(shown.closes, ["escape"]);
    assert.deepEqual(view.calls, []);
  } finally { view.dispose(); }
});

const controller = readFileSync(new URL("../../src/ba_modding_toolkit/web/js/photography/controller.js", import.meta.url), "utf8");
const cameraKeys = controller.slice(controller.indexOf("// Escape leaves the camera"));

test("Escape on a capture error over the camera closes the error only; the next one leaves the camera", () => {
  const left = [];
  const env = { Popup, active: true, modal: null, Loading: { covering: () => false }, safe: (fn) => fn, leave: () => { left.push(true); } };
  env.addEventListener = globalThis.addEventListener; vm.createContext(env);
  const before = listeners.length;
  vm.runInContext(cameraKeys, env);
  assert.equal(listeners.length, before + 1);
  assert.equal(listeners.at(-1).capture, true, "the camera's handler runs before the screen's");
  try {
    const error = message();
    press("Escape");
    assert.deepEqual(error.closes, ["escape"], "the popup stack still gets the key");
    assert.deepEqual(left, [], "the camera stays under the error");
    press("Escape");
    assert.deepEqual(left, [true]);
    // a key another handler already took leaves the camera alone
    env.active = true; left.length = 0;
    listeners.at(-1).fn({ key: "Escape", defaultPrevented: true, preventDefault() {}, stopImmediatePropagation() {} });
    assert.deepEqual(left, []);
  } finally { listeners.splice(listeners.length - 1, 1); }
});
