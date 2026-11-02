// Ordinary refusals read as the game's neutral notice (title Notice, Confirm only); real failures keep Failed and the
// Error Report button (BL-045). web/js/popup.js runs for real on a stand-in DOM without a game folder (every prefab is
// missing), so the messages are drawn plain, which carries the same buttons.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { afterEach, test } from "node:test";

class El {
  constructor(tag = "div") {
    Object.assign(this, { tagName: tag.toUpperCase(), children: [], parentNode: null, style: {}, dataset: {}, attrs: {}, listeners: {} });
    Object.assign(this, { hidden: false, className: "", textContent: "", id: "", tabIndex: 0 });
  }
  get childNodes() { return [...this.children]; }
  get parentElement() { return this.parentNode; }
  appendChild(child) { child.remove(); child.parentNode = this; this.children.push(child); return child; }
  replaceChildren(...kids) { for (const c of [...this.children]) c.remove(); for (const k of kids) this.appendChild(k); }
  remove() {
    if (!this.parentNode) return;
    this.parentNode.children = this.parentNode.children.filter((c) => c !== this);
    this.parentNode = null;
  }
  contains(node) { for (let x = node; x; x = x.parentNode) if (x === this) return true; return false; }
  get isConnected() { return document.body.contains(this); }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  getAttribute(k) { return this.attrs[k] ?? null; }
  addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); }
  click() { for (const fn of this.listeners.click || []) fn({ type: "click" }); }
  focus() { document.activeElement = this; }
  querySelector() { return null; }
  getContext() { return { font: "", measureText(s) { return { width: s.length * 10 }; } }; }
}
const body = new El("body");
const walk = (x, out = []) => { out.push(x); for (const c of x.children) walk(c, out); return out; };
globalThis.document = {
  body, activeElement: body, documentElement: { lang: "en-US", appendChild() {} },
  createElement: (tag) => new El(tag),
  getElementById: (id) => walk(body).find((x) => x.id === id) || null,
  querySelector: () => null,
};
globalThis.addEventListener = () => {};
globalThis.removeEventListener = () => {};
const dialog = body.appendChild(new El());
dialog.id = "dialog";
dialog.hidden = true;
globalThis.fetch = async () => ({ ok: false, status: 404, json: async () => null });
const warn = console.warn;
console.warn = (...args) => { if (!/popup layout|message popup without/.test(String(args[0]))) warn(...args); };

const Popup = await import("../../src/ba_modding_toolkit/web/js/popup.js");
const { setStrings } = await import("../../src/ba_modding_toolkit/web/js/i18n.js");
const locales = ["en-US", "zh-CN", "zh-TW", "ja-JP", "ko-KR", "th-TH"];
const webui = (locale) => JSON.parse(readFileSync(new URL(`../../src/ba_modding_toolkit/locales/${locale}.json`, import.meta.url), "utf8")).webui;
const strings = (value, prefix = "webui") => Object.entries(value).flatMap(([key, text]) => typeof text === "object"
  ? strings(text, `${prefix}.${key}`) : [[`${prefix}.${key}`, text]]);
const english = webui("en-US");
setStrings("en-US", Object.fromEntries(strings(english)));

const tick = () => new Promise((resolve) => setTimeout(resolve, 0));
const action = (name) => walk(dialog).find((e) => e.dataset?.action === name);
async function drawn(name) {
  for (let i = 0; i < 400; i++) { const b = action(name); if (b) return b; await tick(); }
  throw new Error(`no ${name} button`);
}
const actions = () => walk(dialog).filter((e) => e.dataset?.action).map((e) => e.dataset.action).sort();
const title = () => walk(dialog).find((e) => e.tagName === "H2")?.textContent;
async function closeAll() {
  for (let i = 0; i < 20 && !dialog.hidden; i++) { (action("ok") || action("cancel"))?.click(); await tick(); }
}
afterEach(closeAll);

test("every refusal code is a code the page can say: a string in all six languages", () => {
  for (const locale of locales) {
    const ui = webui(locale);
    for (const code of Popup.REFUSALS) assert.ok(ui.error[code], `${locale}: error.${code}`);
    assert.ok(ui.quit.title && ui.picker.ok, `${locale}: the notice's title and Confirm`);
  }
  assert.equal(new Set(Popup.REFUSALS).size, Popup.REFUSALS.length, "no code twice");
});

test("the refusals are state and rule refusals; unreadable or unknown states stay failures", () => {
  for (const code of ["busy", "game_running", "steam_busy", "classic_open", "game_not_found", "game_directory_missing", "state_changed"]) {
    assert.ok(Popup.isRefusal({ code }), code);
  }
  for (const code of ["internal", "game_status_unavailable", "profiles_unreadable", "game_file_locked", "library_io_error", "mod_apply_failed",
    "bundle_invalid", "asset_load_failed", "update_check_failed", undefined]) assert.equal(Popup.isRefusal({ code }), false, String(code));
  assert.equal(Popup.isRefusal(null), false);
  assert.equal(Popup.isRefusal(undefined), false);
});

test("a refusal is the neutral notice: the notice caption, its line and Confirm only", async () => {
  const shown = Popup.notice("Wait for the current task to finish");
  const ok = await drawn("ok");
  assert.equal(title(), english.quit.title);
  assert.notEqual(title(), english.job.failed);
  assert.deepEqual(actions(), ["ok"], "Confirm is the only button");
  assert.equal(action("cancel"), undefined, "no Error Report");
  assert.equal(action("line0"), undefined, "no Error Report row either");
  assert.ok(walk(dialog).some((e) => e.textContent === "Wait for the current task to finish"));
  assert.equal(ok.getAttribute("aria-label"), english.picker.ok);
  ok.click();
  assert.deepEqual(await shown, { button: "ok", checked: null });
});

test("notify shows every refusal code as the notice, with the page's own text", async () => {
  for (const code of Popup.REFUSALS) {
    Popup.notify(`text of ${code}`, { kind: "error", error: { code, params: {} } });
    await drawn("ok");
    assert.equal(title(), english.quit.title, code);
    assert.equal(action("cancel"), undefined, `${code}: no Error Report`);
    assert.ok(walk(dialog).some((e) => e.textContent === `text of ${code}`), code);
    action("ok").click();
    await tick();
    assert.equal(dialog.hidden, true, code);
  }
});

test("notify keeps Failed and the Error Report for a real failure, with or without a code", async () => {
  const reports = [];
  globalThis.BAMTRecovery = { report: (event) => reports.push(event) };
  try {
    for (const error of [{ code: "library_io_error", params: { file: "x.bundle" } }, { code: "game_file_locked" }, { code: "profiles_unreadable" },
      { code: "game_status_unavailable" }, { code: "internal" }, null, {}]) {
      Popup.notify("It broke", { kind: "error", error });
      const report = await drawn("cancel");
      assert.equal(title(), english.job.failed, JSON.stringify(error));
      assert.equal(report.getAttribute("aria-label"), english.recovery.report);
      report.click();
      await tick();
      assert.equal(reports.at(-1).code, error?.code || "internal");
      assert.equal(dialog.hidden, true);
    }
  } finally { delete globalThis.BAMTRecovery; }
});

test("information is still silent, and an empty text shows nothing", async () => {
  Popup.notify("fine", { kind: "info" });
  Popup.notify("", { kind: "error", error: { code: "busy" } });
  await tick();
  assert.equal(dialog.hidden, true);
});

test("the classic modal and the licence prompt are not failures: neither passes an error", () => {
  const web = (file) => readFileSync(new URL(`../../src/ba_modding_toolkit/web/js/${file}`, import.meta.url), "utf8");
  const modal = web("mod-switch.js"), start = modal.indexOf("export async function classicModal(");
  const classic = modal.slice(start, modal.indexOf("\n}\n", start));
  assert.match(classic, /title: T\("classic\.opened"\)/);
  assert.doesNotMatch(classic.slice(0, classic.indexOf("});")), /error:/);
  const compat = web("mod-compatibility.js"), need = compat.indexOf("async function requireRuntime()");
  const licence = compat.slice(need, compat.indexOf("\n}\n", need));
  assert.match(licence, /spine\.license\.title/);
  assert.doesNotMatch(licence, /error:/);
});

test("a greyed Apply names its reason as the same refusal as the greyed switch", () => {
  const prompts = readFileSync(new URL("../../src/ba_modding_toolkit/web/js/prompts.js", import.meta.url), "utf8");
  assert.match(prompts, /notify\(b\.why \|\| "", \{ kind: "error", error: b\.whyError \}\)/);
  assert.match(prompts, /why: blocked, whyError: \{ code: s\.reason, params: s\.reasonParams \}/);
});
