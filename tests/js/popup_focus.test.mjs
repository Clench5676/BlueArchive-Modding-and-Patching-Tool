// The shared focus trap of the popup stack (web/js/popup.js): focus moves in when a popup opens, Tab and Shift+Tab stay
// inside the topmost one, and the focus returns to what had it before once it closes. A stand-in DOM that follows the
// browser's rules for tabIndex (buttons and inputs are tab stops, plain elements are not) and for focus.
import assert from "node:assert/strict";
import { afterEach, test } from "node:test";

const STOPS = new Set(["BUTTON", "INPUT", "SELECT", "TEXTAREA"]);
class El {
  constructor(tag = "div") {
    Object.assign(this, { tagName: tag.toUpperCase(), children: [], parentNode: null, style: {}, dataset: {}, attrs: {}, listeners: {},
      hidden: false, inert: false, disabled: false, className: "", id: "" });
    this.tabIndex = STOPS.has(this.tagName) ? 0 : -1;
  }
  get childNodes() { return [...this.children]; }
  get parentElement() { return this.parentNode; }
  appendChild(child) { child.remove(); child.parentNode = this; this.children.push(child); return child; }
  replaceChildren(...kids) { for (const c of [...this.children]) c.remove(); for (const k of kids) this.appendChild(k); }
  remove() {
    if (!this.parentNode) return;
    this.parentNode.children = this.parentNode.children.filter((c) => c !== this);
    this.parentNode = null;
    // a removed focused element drops the focus to the body, as the browser does
    if (document.activeElement && this.contains(document.activeElement)) document.activeElement = body;
  }
  contains(node) { for (let x = node; x; x = x.parentNode) if (x === this) return true; return false; }
  get isConnected() { return body.contains(this); }
  hasAttribute(k) { return k in this.attrs; }
  setAttribute(k, v) { this.attrs[k] = String(v); if (k === "tabindex") this.tabIndex = Number(v); }
  getAttribute(k) { return this.attrs[k] ?? null; }
  addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); }
  click() { for (const fn of this.listeners.click || []) fn({ type: "click" }); }
  // focus() works on a tab stop or on an element with a tabindex (even -1); a hidden, inert or disabled one keeps it away
  focus() {
    if (this.hidden || this.inert || this.disabled || !this.isConnected || this.tabIndex === undefined) return;
    if (!STOPS.has(this.tagName) && !this.hasAttribute("tabindex") && this.tabIndex < 0) return;
    for (let x = this.parentNode; x; x = x.parentNode) if (x.hidden || x.inert) return;
    document.activeElement = this;
  }
  querySelector() { return null; }
  getContext() { return { font: "", measureText(s) { return { width: s.length * 10 }; } }; }
}
const body = new El("body");
const walk = (x, out = []) => { out.push(x); for (const c of x.children) walk(c, out); return out; };
globalThis.document = { body, activeElement: body, documentElement: { lang: "en-US", appendChild() {} },
  createElement: (tag) => new El(tag), getElementById: (id) => walk(body).find((x) => x.id === id) || null, querySelector: () => null };
const keyListeners = [];
globalThis.addEventListener = (type, fn) => { if (type === "keydown") keyListeners.push(fn); };
globalThis.removeEventListener = () => {};
function press(key, extra = {}) {
  const ev = { key, defaultPrevented: false, preventDefault() { this.defaultPrevented = true; }, ...extra };
  for (const fn of keyListeners) fn(ev);
  return ev;
}
const dialog = body.appendChild(new El());
dialog.id = "dialog";
dialog.hidden = true;
globalThis.fetch = async () => ({ ok: false, status: 404, json: async () => null });
const warn = console.warn;
console.warn = (...args) => { if (!/popup layout|message popup without/.test(String(args[0]))) warn(...args); };
const Popup = await import("../../src/bmpt/web/js/popup.js");

const mount = (tag = "div") => body.appendChild(new El(tag));
const buttons = (host, n) => Array.from({ length: n }, () => host.appendChild(new El("button")));
const tick = () => new Promise((resolve) => setTimeout(resolve, 0));
let hosts = [];
const popupHost = () => { const host = mount(); host.className = "popup-host"; hosts.push(host); return host; };
afterEach(() => {
  for (const host of hosts) { host.hidden = true; host.remove(); }
  hosts = [];
  Popup.top(); // prune what is left
  document.activeElement = body;
});

test("a popup takes the focus in when it opens and gives it back to the opener when it closes", () => {
  const opener = mount("button");
  opener.focus();
  const host = popupHost();
  const [a, b] = buttons(host, 2);
  Popup.opened(host, () => {});
  assert.equal(document.activeElement, host, "the container holds the focus until a control takes it");
  assert.equal(host.getAttribute("tabindex"), "-1", "a plain container can be focused");
  a.focus();
  assert.equal(document.activeElement, a);
  // drawn again (a redraw): same place, the focus is left where it is, and the opener is not lost
  Popup.opened(host, () => {});
  assert.equal(document.activeElement, a);
  b.focus();
  host.hidden = true;
  Popup.closed(host);
  assert.equal(document.activeElement, opener, "back to what had it");
  opener.remove();
});

test("the focus is not taken from the popup's own control, and not given back over a user who moved on", () => {
  const opener = mount("button"), elsewhere = mount("button");
  opener.focus();
  const host = popupHost();
  const [a] = buttons(host, 1);
  a.focus();
  Popup.opened(host, () => {});
  assert.equal(document.activeElement, a, "a control that already holds the focus keeps it");
  elsewhere.focus();
  host.hidden = true;
  Popup.closed(host);
  assert.equal(document.activeElement, elsewhere, "focus moved to another control meanwhile");
  opener.remove(); elsewhere.remove();
});

test("Tab and Shift+Tab wrap at the ends and step by the browser inside", () => {
  const host = popupHost();
  const [a, b, c] = buttons(host, 3);
  Popup.opened(host, () => {});
  c.focus();
  let ev = press("Tab");
  assert.ok(ev.defaultPrevented);
  assert.equal(document.activeElement, a, "past the last control: the first");
  ev = press("Tab", { shiftKey: true });
  assert.ok(ev.defaultPrevented);
  assert.equal(document.activeElement, c, "before the first control: the last");
  b.focus();
  assert.equal(press("Tab").defaultPrevented, false, "in the middle the browser steps on");
  assert.equal(press("Tab", { shiftKey: true }).defaultPrevented, false);
  assert.equal(document.activeElement, b);
});

test("with the focus outside or on the container, Tab brings it in at the matching end", () => {
  const under = mount("button");
  const host = popupHost();
  const [a, , c] = buttons(host, 3);
  Popup.opened(host, () => {});
  under.focus();
  assert.ok(press("Tab").defaultPrevented);
  assert.equal(document.activeElement, a, "Tab from outside enters at the first control");
  under.focus();
  assert.ok(press("Tab", { shiftKey: true }).defaultPrevented);
  assert.equal(document.activeElement, c, "Shift+Tab from outside enters at the last");
  host.focus();
  assert.ok(press("Shift", { shiftKey: true }) && press("Tab", { shiftKey: true }).defaultPrevented);
  assert.equal(document.activeElement, c, "from the container, backwards wraps to the last");
  // the focus lost to the body (a redraw replaced the focused control)
  document.activeElement = body;
  assert.ok(press("Tab").defaultPrevented);
  assert.equal(document.activeElement, a);
  under.remove();
});

test("disabled, hidden, inert and undrawn controls are no tab stops", () => {
  const host = popupHost();
  const [first, off, gone, last] = buttons(host, 4);
  off.disabled = true;
  gone.style.display = "none";
  const part = host.appendChild(new El());
  part.inert = true;
  buttons(part, 2);
  const shut = host.appendChild(new El());
  shut.hidden = true;
  buttons(shut, 2);
  const seen = host.appendChild(new El("button"));
  seen.style.visibility = "hidden";
  Popup.opened(host, () => {});
  assert.deepEqual(Popup.tabStops(host), [first, last]);
  last.focus();
  assert.ok(press("Tab").defaultPrevented);
  assert.equal(document.activeElement, first);
});

test("a popup with no control keeps the focus on itself", () => {
  const host = popupHost();
  Popup.opened(host, () => {});
  const ev = press("Tab");
  assert.ok(ev.defaultPrevented);
  assert.equal(document.activeElement, host);
});

test("the topmost popup holds the trap, answered or not, and the one under it takes over when it closes", () => {
  const lower = popupHost(), upper = popupHost();
  const [l1, l2] = buttons(lower, 2), [u1, u2] = buttons(upper, 2);
  Popup.opened(lower, () => {});
  Popup.opened(upper, null); // must be answered with a button: it traps just the same
  assert.equal(document.activeElement, upper);
  l2.focus();
  assert.ok(press("Tab").defaultPrevented);
  assert.equal(document.activeElement, u1, "focus under the topmost popup is pulled into it");
  u2.focus();
  press("Tab");
  assert.equal(document.activeElement, u1);
  upper.hidden = true;
  Popup.closed(upper);
  assert.equal(Popup.top().host, lower);
  l2.focus();
  press("Tab");
  assert.equal(document.activeElement, l1);
});

test("a popup layer in a shared host traps by its own layer, not the host", () => {
  const host = popupHost();
  const lowerLayer = host.appendChild(new El()), upperLayer = host.appendChild(new El());
  lowerLayer.tabIndex = upperLayer.tabIndex = -1;
  const [l] = buttons(lowerLayer, 1), [u1, u2] = buttons(upperLayer, 2);
  Popup.opened(host, () => {}, { el: lowerLayer });
  Popup.opened(host, () => {}, { el: upperLayer });
  l.focus();
  press("Tab");
  assert.equal(document.activeElement, u1);
  u2.focus();
  press("Tab");
  assert.equal(document.activeElement, u1, "wraps inside the upper layer only");
});

test("keys the trap leaves alone: another handler's, composing, modified, and every other key", () => {
  const host = popupHost();
  const [a, b] = buttons(host, 2);
  Popup.opened(host, () => {});
  b.focus();
  for (const extra of [{ defaultPrevented: true }, { isComposing: true }, { keyCode: 229 }, { ctrlKey: true }, { altKey: true }, { metaKey: true }]) {
    const ev = press("Tab", extra);
    assert.equal(document.activeElement, b);
    assert.ok(!ev.defaultPrevented || extra.defaultPrevented, JSON.stringify(extra));
  }
  assert.equal(press("Enter").defaultPrevented, false);
  assert.equal(press("a").defaultPrevented, false);
  assert.equal(document.activeElement, b);
  assert.ok(a);
});

test("with no popup open Tab is the page's", () => {
  const page = mount("button");
  page.focus();
  assert.equal(Popup.top(), null);
  assert.equal(press("Tab").defaultPrevented, false);
  assert.equal(document.activeElement, page);
  page.remove();
});

test("a popup closed by hiding its host gives the focus back when the stack is next read", () => {
  const opener = mount("button");
  opener.focus();
  const host = popupHost();
  const [a] = buttons(host, 1);
  Popup.opened(host, () => {});
  a.focus();
  host.hidden = true; // no closed() call: the page only hides the host
  document.activeElement = body; // the browser drops the focus from a hidden subtree
  assert.equal(Popup.top(), null);
  assert.equal(document.activeElement, opener);
  opener.remove();
});

test("a popup under another one is not given the focus back over it", () => {
  const opener = mount("button");
  opener.focus();
  const lower = popupHost(), upper = popupHost();
  const [l] = buttons(lower, 1), [u] = buttons(upper, 1);
  Popup.opened(lower, () => {});
  l.focus();
  Popup.opened(upper, () => {});
  u.focus();
  lower.hidden = true;
  Popup.closed(lower);
  assert.equal(document.activeElement, u, "the opener of the lower popup is not under the upper one");
  opener.remove();
});

test("a message popup takes the focus, traps Tab between its buttons and returns the focus on close", async () => {
  const opener = mount("button");
  opener.focus();
  const shown = Popup.message({ title: "Title", lines: ["Line"], ok: "OK", cancel: "Cancel" });
  for (let i = 0; i < 200 && !walk(dialog).some((e) => e.dataset?.action === "ok"); i++) await tick();
  const layer = dialog.children.at(-1);
  assert.equal(document.activeElement, layer, "the popup holds the focus");
  const cancel = walk(layer).find((e) => e.dataset?.action === "cancel"), ok = walk(layer).find((e) => e.dataset?.action === "ok");
  assert.ok(press("Tab").defaultPrevented);
  assert.equal(document.activeElement, cancel, "the first control");
  ok.focus();
  assert.ok(press("Tab").defaultPrevented);
  assert.equal(document.activeElement, cancel, "past the last control: the first");
  opener.focus();
  press("Tab", { shiftKey: true });
  assert.equal(document.activeElement, ok, "from the page below: pulled back in");
  ok.click();
  assert.deepEqual(await shown, { button: "ok", checked: null });
  assert.equal(dialog.hidden, true);
  assert.equal(document.activeElement, opener);
  opener.remove();
});

test("a Hall-style modal that focuses its own host before joining the stack keeps that focus and traps Tab", () => {
  const nav = mount("button");
  nav.focus();
  const host = popupHost();
  host.setAttribute("tabindex", "-1");
  host.focus(); // createModal focuses its host at once
  const [a, b] = buttons(host, 2);
  Popup.opened(host, () => {});
  assert.equal(document.activeElement, host);
  b.focus();
  assert.ok(press("Tab").defaultPrevented);
  assert.equal(document.activeElement, a);
  nav.focus();
  assert.ok(press("Tab", { shiftKey: true }).defaultPrevented);
  assert.equal(document.activeElement, b, "pulled back in from the page below");
  nav.remove();
});

test("an asset-consent style popup that must be answered is trapped too, and Escape does nothing", () => {
  const behind = mount("button");
  behind.focus();
  const host = mount();
  host.className = "consent-host";
  hosts.push(host);
  const [no, yes] = buttons(host, 2);
  Popup.opened(host, null);
  assert.equal(document.activeElement, host);
  yes.focus();
  press("Tab");
  assert.equal(document.activeElement, no);
  assert.ok(press("Escape").defaultPrevented, "the key is taken, nothing under it acts");
  assert.equal(host.hidden, false);
  host.hidden = true;
  Popup.closed(host);
  assert.equal(document.activeElement, behind);
  behind.remove();
});

test("popup.js keeps one trap: the Hall modal and the import dialogs no longer carry their own", async () => {
  const { readFileSync } = await import("node:fs");
  const web = (file) => readFileSync(new URL(`../../src/bmpt/web/js/${file}`, import.meta.url), "utf8");
  assert.doesNotMatch(web("student-hall-ui.js"), /event\.key !== "Tab"/);
  assert.doesNotMatch(web("batch-import.js"), /event\.key !== "Tab"/);
  assert.match(web("popup.js"), /addEventListener\("keydown", trapKey\)/);
});
