// Tests for web/js/popup.js (the shared popups: the stack Escape goes to, the message popup, the list popup with
// several cards ticked, the start sequence), web/js/update.js and detail.js's preview actions, on a small stand-in
// DOM. Run by tests/test_popup.py with Node's test runner: node --test tests/js/popup.test.mjs
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { register } from "node:module";
import { afterEach, test } from "node:test";
import vm from "node:vm";
import { switchPrefabs } from "./switch_prefabs.mjs";
import { selectionPrefab, selectionFramePixels, selectionSprite } from "./selection_prefabs.mjs";
import { controls, insert } from "./control_prefabs.mjs";
import { displayLayouts } from "./display_prefabs.mjs";

// --- a stand-in DOM: elements in a tree, ids, clicks, the focus and the window's key listeners ---
class El {
  constructor(tag = "div") {
    Object.assign(this, { tagName: tag.toUpperCase(), children: [], parentNode: null, style: {}, dataset: {}, attrs: {}, listeners: {} });
    Object.assign(this, { hidden: false, className: "", textContent: "", id: "", font: "", tabIndex: 0 });
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
  set innerHTML(html) {
    for (const c of [...this.children]) c.remove();
    const stack = [this];
    for (const match of html.matchAll(/<(\/?)(\w+)(?:\s+class="([^"]*)")?[^>]*>/g)) {
      if (match[1]) stack.pop();
      else { const el = new El(match[2]); el.className = match[3] || ""; stack.at(-1).appendChild(el); stack.push(el); }
    }
  }
  get classList() {
    return { replace: (a, b) => { this.classList.remove(a); this.classList.add(b); },
      toggle: (name, force) => { const on = force ?? !this.className.split(" ").includes(name); this.classList[on ? "add" : "remove"](name); return on; }, add: (...names) => { this.className = [...new Set([...this.className.split(" "), ...names])].join(" "); },
      remove: (...names) => { this.className = this.className.split(" ").filter((name) => !names.includes(name)).join(" "); } };
  }
  contains(node) { for (let n = node; n; n = n.parentNode) if (n === this) return true; return false; }
  // only the attribute-presence selector the tests need: [data-some-key]
  querySelectorAll(selector) {
    const key = /^\[data-([\w-]+)\]$/.exec(selector)?.[1].replace(/-(\w)/g, (_, c) => c.toUpperCase());
    if (!key) throw new Error(`stand-in querySelectorAll: ${selector}`);
    return walk(this).filter((e) => e !== this && key in e.dataset);
  }
  get isConnected() { return document.body.contains(this); }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  getAttribute(k) { return this.attrs[k] ?? null; }
  removeAttribute(k) { delete this.attrs[k]; }
  addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); }
  click() {
    let result;
    for (const fn of this.listeners.click || []) result = fn({ type: "click", target: this, currentTarget: this });
    return result;
  }
  focus() { document.activeElement = this; }
  querySelector(selector) { return walk(this).find((e) => selector.startsWith(".")
    ? e.className.split(" ").includes(selector.slice(1)) : e.tagName === selector.toUpperCase()) || null; }
  // a canvas: text is measured at 0.6 of its size per character
  getContext() { return { font: "", drawImage() {}, fillRect() {}, scale() {}, transform() {}, putImageData() {},
    getImageData(_x, _y, width, height) { return width === 30 && height === 30 ? selectionFramePixels()
      : { width, height, data: new Uint8ClampedArray(width * height * 4).fill(255) }; },
    measureText(s) { return { width: s.length * 0.6 * parseFloat(/([\d.]+)px/.exec(this.font)?.[1] || 10) }; } }; }
  toBlob(fn) { fn(new Blob()); }
  toDataURL() { return "data:image/png;base64,c3ludGhldGljLW1hc2s="; }
}
globalThis.Image = class {
  naturalWidth = 8;
  naturalHeight = 8;
  set src(url) {
    if (url.includes('Common_Bg_Raius10px_Select')) this.naturalWidth = this.naturalHeight = 30;
    if (url.includes('Common_Icon_Mulligan_')) { this.naturalWidth = 64; this.naturalHeight = 69; }
    queueMicrotask(() => this.onload?.());
  }
};
let events;
globalThis.EventSource = class {
  constructor() { events = this; this.listeners = {}; }
  addEventListener(type, fn) { this.listeners[type] = fn; }
  emit(type, data) { this.listeners[type]?.({ data: JSON.stringify(data) }); }
};
const body = new El("body");
const walk = (n, out = []) => { out.push(n); for (const c of n.children) walk(c, out); return out; };
globalThis.document = {
  body, activeElement: body, documentElement: { lang: "en-US", appendChild() {} },
  createElement: (tag) => new El(tag),
  getElementById: (id) => walk(body).find((n) => n.id === id) || null,
  querySelector: (selector) => selector === ".popup-host:not([hidden]), .consent-host:not([hidden])"
    ? walk(body).find((e) => !e.hidden && ["popup-host", "consent-host"].some((cls) => e.className.split(" ").includes(cls))) || null : null,
};
const keyListeners = [];
globalThis.addEventListener = (type, fn) => { if (type === "keydown") keyListeners.push(fn); };
globalThis.removeEventListener = (type, fn) => { const at = keyListeners.indexOf(fn); if (type === "keydown" && at >= 0) keyListeners.splice(at, 1); };
function press(key, extra = {}) {
  const ev = { key, defaultPrevented: false, preventDefault() { this.defaultPrevented = true; }, ...extra };
  for (const fn of keyListeners) fn(ev);
  return ev;
}
const host = (id) => { const h = body.appendChild(new El()); h.id = id; h.hidden = true; return h; };
const dialog = host("dialog"), prompt = host("prompt"), consent = host("consent");
const settings = host("settings");
settings.className = "popup-host";
consent.className = "consent-host";
const display = host("display");
display.className = "consent-host settings-host";
const stage = host("stage");
stage.getBoundingClientRect = () => ({ width: 1920 });
stage.hidden = false;

// --- the server: a small atlas index (the progress and button sprites only), the popup
// frame's prefab, the update check ---
const n = (name, pos, widgets = [], children = []) => ({ name, active: true, pos, scale: [1, 1], rotZ: 0, widgets, children });
const spr = (name, w, h, extra = {}) => ({ sprite: name, atlas: "Common", type: 1, w_: w, h_: h, pivot: 4, depth: 5, color: "#ffffff/1.00", ...extra });
const lbl = () => ({ cls: "UILabel", text: "text", fontSize: 72, color: "#2d4664/1.00", w_: 400, h_: 180, pivot: 4, depth: 9 });
const SORT = n("UIPopup_Sort", [0, 0], [], [n("Center", [0, 0], [], [
  n("BlackBg", [0, 0], [], [n("BlackBgClickArea", [0, 0], [spr("Common_Square_Bg", 2976, 2232, { color: "#000000/0.94" })])]),
  n("Popup", [0, 0], [], [
    n("Base", [0, -20], [], [n("BG", [0, 8], [spr("Common_Popup_Bg", 2458, 1355)], [
      n("Texture_Top", [-977, 609], [{ texture: "Top", w_: 500, h_: 100, pivot: 4, depth: 6, color: "#ffffff/1.00" }]),
      n("Label_Title_Locale", [0, 611], [{ ...lbl(), w_: 146, h_: 72 }], [n("Line", [0, -52], [spr("Common_Square_Bg", 160, 12)])]),
      n("Btn_Close", [1144, 616], [spr("Common_Icon_Close", 60, 60, { type: 0 })]),
    ])]),
    n("Btn", [0, -499], [], [
      n("Btn_Ok", [292, 0], [], [n("Label_Locale", [0, 2], [lbl()]), n("BG (1)", [0, -4], [spr("Common_Btn_BG", 496, 177)])]),
      n("Btn_Cancel", [-292, 0], [], [n("BG (1)", [0, -4], [spr("Common_Btn_BG", 496, 177)]), n("Label_Locale", [0, 2], [lbl()])]),
    ]),
    n("Normal", [0, 0]),
  ]),
])]);
// A synthetic prefab pattern with a top-right pivot and fixed-size parent anchors.
for (const button of SORT.children[0].children[1].children.find((c) => c.name === "Btn").children) {
  button.children.find((c) => c.name === "BG (1)").children.push(n("Corner", [-50, -20], [
    spr("Common_Btn_Normal_B_C_Pt", 126, 70, { type: 0, pivot: 2,
      anchors: { leftAnchor: [1, -130], rightAnchor: [1, -4], bottomAnchor: [1, -76], topAnchor: [1, -6] } }),
  ]));
}
// The settings test uses General only; the tabs and popup frame retain their real paths.
const OPTIONS = structuredClone(SORT);
OPTIONS.name = "UIPopup_Option_PC";
OPTIONS.children[0].children[1].children.push(
  n("TabBg", [0, 0], [], [n("Grid", [0, 0])]),
  n("Game", [0, 0], [], ["Tab_On", "Tab_Off"].map((name) => n(name, [0, 0], [], [n("Label_Locale", [0, 0], [lbl()])]))),
);
const controlLayouts = controls();
const filterGroup = displayLayouts(controlLayouts.filter);
const switchLayouts = switchPrefabs();
insert(OPTIONS, "Center/Popup/List/Sound_Setting/ETCSetting/ButtonBg", controlLayouts.option.children[0].children[0].children[0].children[0].children[0].children[0]);
// The sound row's slider (Master/Type_3): its title, the 689-unit bar with its thumb, the icons and the mute check.
insert(OPTIONS, "Center/Popup/List/Sound_Setting/Sound_Scroll View/Grid_2/Master/Type_3", n("Type_3", [0, 0], [], [
  n("Label_Locale_OptionTitle", [-600, 0], [{ ...lbl(), w_: 360, h_: 60, pivot: 3 }]),
  n("SlideBg", [300, 0], [spr("Common_Bg_Raius10px", 689, 40)], [n("Thumb", [-316, 0], [spr("Common_Bg_Raius10px", 60, 60)])]),
  n("Icon_Minimum", [-100, 0], [spr("Common_Bg_Raius10px", 40, 40)]), n("Icon_Maximum", [700, 0], [spr("Common_Bg_Raius10px", 40, 40)]),
  n("Label_Mute", [850, 0], [{ ...lbl(), w_: 180, h_: 60 }], [n("Bg", [0, 0], [spr("Common_Bg_Raius10px", 100, 60)]), n("Icon", [0, 0], [spr("Common_Bg_Raius10px", 40, 40)])]),
]));
let update = null; // what app.checkUpdate answers
const calls = [];
const requests = [], handlers = new Map();
const reply = (body, ok = true) => ({ ok, status: ok ? 200 : 404, json: async () => body });
globalThis.fetch = async (url, opts = {}) => {
  if (url === "/error-codes.json") return reply(JSON.parse(readFileSync(new URL("../../src/bmpt/web/error-codes.json", import.meta.url), "utf8")));
  if (url === "/api/rpc") {
    const { method, params } = JSON.parse(opts.body);
    calls.push(method);
    requests.push({ method, params });
    if (handlers.has(method)) return reply(await handlers.get(method)(params));
    return reply({ result: method === "app.checkUpdate" ? update : null, params });
  }
  if (url === "/asset/ui/atlases.json") return reply({ Common: Object.fromEntries(
    ["Common_Square_Bg", "Common_Bg_Raius10px", "Common_Btn_BG", "Common_Popup_Bg", "Common_Btn_Normal_B_C_Pt",
      "Common_Bg_Raius10px_Select", "Common_Icon_Check_Y_Off", "Common_Icon_Mulligan_1", "Common_Icon_Mulligan_2", "Common_Line_Raius20px", "Common_Icon_Reddot2", "Card_Ability_Select_Line", "Card_Ability_Select_Line_Icon", "Cafe_Icon_NameEdit"].map((name) =>
      [name, name === 'Common_Bg_Raius10px_Select' ? selectionSprite : name.startsWith('Common_Icon_Mulligan_') ? { width: 64, height: 69 } : { width: 8, height: 8 }])) });
  if (url === "/asset/ui/textures.json") return reply({ Scroll: "scroll.png" });
  if (url === "/asset/ui/layout/UICharacterCollectionPopulatorUnit.json") return reply(switchLayouts.unit);
  if (url === "/asset/ui/layout/UINonEquipmentInventory.json") return reply(switchLayouts.inventory);
  if (url === "/asset/ui/layout/UIPopup_Sort.json") return reply(SORT);
  if (url === "/asset/ui/layout/UIPopup_Mulligan.json") return reply(selectionPrefab());
  if (url === "/asset/ui/layout/UIPopup_FilterSort.json") return reply(controlLayouts.filter);
  if (url === "/asset/ui/layout/UIFilterObjectList.json") return reply(filterGroup);
  if (url === "/asset/ui/layout/UIPopup_Option_PC.json") return reply(OPTIONS);
  return reply(null, false); // the other prefabs are optional
};

// the prefabs this server lacks are optional: their warning is expected
const warn = console.warn;
console.warn = (...args) => { if (!String(args[0]).startsWith("popup layout")) warn(...args); };

const Popup = await import("../../src/bmpt/web/js/popup.js");
const Update = await import("../../src/bmpt/web/js/update.js");
const { S } = await import("../../src/bmpt/web/js/util.js");
const { setStrings } = await import("../../src/bmpt/web/js/i18n.js");
const { setSpineConsent } = await import("../../src/bmpt/web/js/spine-runtime.js");
setSpineConsent(true);
const runtime = vm.createContext({ console, TextDecoder, TextEncoder, Float32Array, Uint8Array, Uint16Array, Int16Array, Int32Array, ArrayBuffer, DataView });
vm.runInContext(readFileSync(new URL("../../src/bmpt/web/vendor/spine-player-4.2.120/spine-player.min.js", import.meta.url), "utf8"), runtime);
globalThis.spine = runtime.spine;
const english = JSON.parse(readFileSync(new URL("../../src/bmpt/locales/en-US.json", import.meta.url))).webui;
function strings(value, prefix = "webui") {
  return Object.entries(value).flatMap(([key, text]) => typeof text === "object" ? strings(text, `${prefix}.${key}`) : [[`${prefix}.${key}`, text]]);
}
setStrings("en-US", Object.fromEntries(strings(english)));

const tick = () => new Promise((resolve) => setTimeout(resolve, 0));
const actionsIn = (root) => walk(root).filter((e) => e.dataset?.action);
const action = (root, name) => actionsIn(root).find((e) => e.dataset.action === name);
// waits until a button is drawn in root
async function drawn(root, name) {
  for (let i = 0; i < 1000; i++) { const b = action(root, name); if (b) return b; await tick(); }
  throw new Error(`no ${name} button`);
}
// NGUI's stand-in rect reads local styles. Include the scroll viewport's stage offset when testing its children.
const stageRect = (el, NGUI) => {
  const rect = [...NGUI.rect(el)];
  for (let parent = el.parentElement; parent; parent = parent.parentElement) if (parent.className === "popup-cards") {
    rect[0] += parseFloat(parent.style.left) - (parent.scrollLeft || 0);
    rect[1] += parseFloat(parent.style.top) - (parent.scrollTop || 0);
  }
  return rect;
};
const state = (promise) => Promise.race([promise.then((v) => ({ done: v })), tick().then(() => "pending")]);
async function until(fn) {
  for (let i = 0; i < 1000; i++) { if (fn()) return; await tick(); }
  assert.fail("popup did not reach the expected state");
}
// the update offer's content (versions, Status, Progress) sits in its centred Content group
const part = (name) => walk(consent).find((e) => [`Center/Popup/${name}`, `Center/Popup/Content/${name}`].includes(e.dataset.path));

afterEach(async () => {
  await new Promise((resolve) => setImmediate(resolve));
  for (const layer of [...dialog.children].reverse()) action(layer, "close")?.click();
});

test("fitting message lines together keeps the size floor and all text when extra wrapping is needed", async () => {
  const NGUI = await import("../../src/bmpt/web/js/ngui.js");
  const text = "A long explanation ".repeat(60).trim();
  const opened = Popup.message({ title: "Help", lines: ["On", text], width: 700, fitTogether: [0, 1] });
  await drawn(dialog, "close");
  // this message is taller than the scroll cap (about 1800 units of lines): its lines are in the scrolling list
  assert.ok(walk(dialog).some((e) => e.className === "popup-cards"));
  const lines = [0, 1].map((i) => walk(dialog).find((e) => e.dataset.path === `Lines/Line_${i}`));
  const size = `${(NGUI.fontSizeFor(text, 44) * S).toFixed(2)}px`;
  assert.equal(lines[0].style.fontSize, size);
  assert.equal(lines[1].style.fontSize, size);
  assert.ok(lines[1].textContent.split("\n").length > 4);
  assert.equal(lines[1].textContent.replaceAll("\n", " "), text);
  action(dialog, "close").click();
  await opened;
});

test("plain message line buttons keep their actions and allow closing the popup", async () => {
  let details = 0;
  const pending = Popup.message({ title: "Help", plain: true, lines: ["On", "Off",
    { text: "Not switched", button: { label: "Retry", fn: ({ close }) => close("retry") } },
    { text: "Left as is", button: { label: "Details", fn: () => { details++; } } },
  ] });
  await drawn(dialog, "line3");
  action(dialog, "line3").click();
  assert.equal(details, 1);
  assert.equal(dialog.hidden, false);
  action(dialog, "line2").click();
  assert.deepEqual(await pending, { button: "retry", checked: null });
  assert.equal(dialog.hidden, true);
});
const available = { current: "0.1.0-alpha.3", version: "0.1.0-beta", installable: true, downloaded: false, file: null };

test("Escape goes to the topmost open popup only, and to none under one that must be answered", () => {
  const a = body.appendChild(new El()), b = body.appendChild(new El());
  const closed = [];
  Popup.opened(a, (how) => closed.push(["a", how]));
  Popup.opened(b, (how) => closed.push(["b", how]));
  // drawing the lower one again keeps its place
  Popup.opened(a, (how) => closed.push(["a", how]));
  assert.equal(Popup.top().host, b);
  assert.ok(press("Escape").defaultPrevented);
  assert.deepEqual(closed, [["b", "escape"]]);
  b.hidden = true; // closed: off the stack by itself
  press("Escape");
  assert.deepEqual(closed, [["b", "escape"], ["a", "escape"]]);
  a.hidden = true;
  assert.equal(Popup.top(), null);
  assert.equal(press("Escape").defaultPrevented, false, "nothing open: the key is the screen's");
  // a popup without a way out takes the key: the one under it stays
  a.hidden = false;
  b.hidden = false;
  Popup.opened(a, () => closed.push(["a", "again"]));
  Popup.opened(b, null);
  assert.ok(press("Escape").defaultPrevented);
  assert.equal(closed.length, 2);
  // an input method composing, or a control that took the key itself
  b.hidden = true;
  press("Escape", { isComposing: true });
  press("Escape", { keyCode: 229 });
  press("Escape", { defaultPrevented: true });
  assert.equal(closed.length, 2);
  press("Enter");
  assert.equal(closed.length, 2);
  a.remove();
  b.remove();
});

test("scrollable findings wrap long paths and unbroken names inside the frame, keeping actions outside", async () => {
  const lines = ["CH0261_home.skel - rig differs", "Intro_Shoulder_L/".repeat(90), "x".repeat(180),
    { text: "Region_".repeat(100), button: { label: "Details", fn: () => { details++; } } }];
  let details = 0;
  const pending = Popup.message({ title: "Compatibility", lines, ok: "Remap", cancel: "Cancel", scroll: true });
  const ok = await drawn(dialog, "ok");
  const area = walk(dialog).find((el) => el.className === "popup-cards");
  const inner = area.children[0];
  assert.ok(parseFloat(inner.style.height) >= parseFloat(area.style.height));
  const rows = walk(inner).filter((el) => /^Lines\/Row_\d+$/.test(el.dataset.path));
  assert.equal(rows.length, lines.length);
  for (const row of rows) {
    const width = parseFloat(row.style.width), fs = parseFloat(row.style.fontSize);
    assert.ok(row.textContent.split("\n").every((line) => line.length * fs * 0.6 <= width + 1), row.dataset.path);
  }
  assert.ok(rows[1].textContent.includes("\n") && rows[2].textContent.includes("\n"));
  assert.ok(parseFloat(ok.style.top) >= parseFloat(area.style.top) + parseFloat(area.style.height));
  action(dialog, "line3").click();
  assert.equal(details, 1);
  assert.ok(action(dialog, "cancel"));
  ok.click();
  assert.deepEqual(await pending, { button: "ok", checked: null });
});

test('photo deletion warnings fit the UIPopup_Sort frame in all six locales', async () => {
  const NGUI = await import('../../src/bmpt/web/js/ngui.js');
  const bounds = el => { const [left, top, width, height] = NGUI.rect(el); return {left, top, right:left+width, bottom:top+height}; };
  try {
    for(const locale of ['en-US','zh-CN','zh-TW','ja-JP','ko-KR','th-TH']){
      const ui=JSON.parse(readFileSync(new URL(`../../src/bmpt/locales/${locale}.json`,import.meta.url),'utf8')).webui;
      setStrings(locale,Object.fromEntries(strings(ui)));
      for(const warning of [ui.error.asset_delete_confirm,ui.error.photo_delete_other_variant]){
        const pending=Popup.message({title:ui.photo.delete,lines:[warning],ok:ui.photo.delete,cancel:ui.popup.cancel});
        const ok=await drawn(dialog,'ok');
        const part=path=>walk(dialog).find(el=>el.dataset.path===`Center/Popup/${path}`);
        const line=part('Lines/Line_0'),frame=bounds(part('Base/BG'));
        const text=bounds(line),buttons=['Btn_Ok','Btn_Cancel'].map(name=>bounds(part(`Btn/${name}/BG (1)`)));
        const fs=parseFloat(line.style.fontSize),width=parseFloat(line.style.width);
        for(const row of line.textContent.split('\n'))assert.ok(row.length*.6*fs<=width+.01,`${locale}: ${row}`);
        for(const viewportWidth of [960,1920,2560])for(const density of [1,2]){
          const scale=viewportWidth/1920*density;
          assert.ok(text.left*scale>=frame.left*scale&&text.right*scale<=frame.right*scale,locale);
          assert.ok(text.top*scale>=frame.top*scale&&text.bottom*scale<Math.min(...buttons.map(b=>b.top))*scale,locale);
        }
        ok.click();await pending;
      }
    }
  } finally { setStrings('en-US',Object.fromEntries(strings(english))); }
});

test('the untested game version confirmation fits the UIPopup_Sort frame in all six locales', async () => {
  const NGUI = await import('../../src/bmpt/web/js/ngui.js');
  const bounds = el => { const [left, top, width, height] = NGUI.rect(el); return {left, top, right:left+width, bottom:top+height}; };
  const item = { marker: "\u2022", markerGap: 44, indent: 52 };
  try {
    for(const locale of ['en-US','th-TH','ko-KR','ja-JP','zh-CN','zh-TW']){
      const ui=JSON.parse(readFileSync(new URL(`../../src/bmpt/locales/${locale}.json`,import.meta.url),'utf8')).webui;
      setStrings(locale,Object.fromEntries(strings(ui)));
      // mod-switch.js untestedLines: the count, then six entries at most and the rest counted
      const names=Array.from({length:6},(_,i)=>`Hoshino (First Year, School Swimsuit) ${i} - ${ui.tab.lobby}`);
      const lines=[{text:ui.mods.untested.affected.replace('{n}','8')},...[...names,'+2'].map(text=>({text,...item}))];
      const pending=Popup.message({title:ui.mods.untested.title,lines,ok:ui.mods.untested.apply,cancel:ui.popup.cancel,plain:true});
      const ok=await drawn(dialog,'ok');
      const part=path=>walk(dialog).find(el=>el.dataset.path===`Center/Popup/${path}`);
      const frame=bounds(part('Base/BG'));
      const buttons=['Btn_Ok','Btn_Cancel'].map(name=>bounds(part(`Btn/${name}/BG (1)`)));
      for(let i=0;i<lines.length;i++){
        const line=part(`Lines/Line_${i}`),text=bounds(line);
        assert.ok(line,`${locale}: line ${i}`);
        assert.ok(text.left>=frame.left&&text.right<=frame.right,`${locale}: line ${i} inside the frame`);
        assert.ok(text.bottom<Math.min(...buttons.map(b=>b.top)),`${locale}: line ${i} above the buttons`);
      }
      // the two captions: one size, at most two lines inside the slanted bodies' 472-unit room (0.6 em stand-in glyphs)
      const captions=['Btn_Ok','Btn_Cancel'].map(name=>part(`Btn/${name}/Label_Locale`));
      assert.equal(new Set(captions.map(el=>el.style.fontSize)).size,1,`${locale}: one caption size`);
      for(const caption of captions){
        const fs=parseFloat(caption.style.fontSize),rows=caption.textContent.split('\n');
        assert.ok(rows.length<=2&&rows.every(row=>row.length*.6*fs<=472+.01),`${locale}: ${caption.textContent} at ${fs}`);
      }
      ok.click();await pending;
    }
  } finally { setStrings('en-US',Object.fromEntries(strings(english))); }
});

test("shared message sizes and scrolling preserve complete paths with and without row buttons", async () => {
  const NGUI = await import("../../src/bmpt/web/js/ngui.js");
  // long enough to scroll at the light box's smaller type as well
  const path = "Intro_Shoulder_L/".repeat(180), name = "x".repeat(360);
  for (const boxed of [false, true]) {
    const lines = ["On", path, boxed ? { text: name, button: { label: "Details" } } : name];
    const pending = Popup.message({ title: "Findings", lines, fitTogether: [0, 1], scroll: true, ok: "OK" });
    const ok = await drawn(dialog, "ok");
    const area = walk(dialog).find((el) => el.className === "popup-cards");
    const prefix = boxed ? "Row" : "Line";
    const rows = [0, 1, 2].map((i) => walk(area).find((el) => el.dataset.path === `Lines/${prefix}_${i}`));
    const size = `${(NGUI.fontSizeFor(path, boxed ? 26 : 44) * S).toFixed(2)}px`;
    assert.equal(rows[0].style.fontSize, size);
    assert.equal(rows[1].style.fontSize, size);
    assert.equal(rows[1].textContent.replaceAll("\n", ""), path);
    assert.equal(rows[2].textContent.replaceAll("\n", ""), name);
    for (const row of rows) {
      const width = parseFloat(row.style.width), fs = parseFloat(row.style.fontSize);
      assert.ok(row.textContent.split("\n").every((line) => line.length * fs * 0.6 <= width + 1));
    }
    assert.ok(parseFloat(area.children[0].style.height) > parseFloat(area.style.height));
    assert.ok(parseFloat(ok.style.top) >= parseFloat(area.style.top) + parseFloat(area.style.height));
    ok.click();
    await pending;
  }
});

test("compatibility details scroll the complete list and return to the same Remap and Cancel actions", async () => {
  const Compatibility = await import("../../src/bmpt/web/js/mod-compatibility.js");
  const names = Array.from({ length: 80 }, (_, i) => `Intro_Shoulder_L/Intro_Shoulder_L_${i}`);
  const input = (paths) => ({ atlases: {}, textures: {}, skeletons: { "CH0261_home.skel": {
    version: "4.2.33", data: Buffer.from(JSON.stringify({ bones: [{ name: "root" }], slots: [{ name: "face", bone: "root" }],
      skins: { default: { face: Object.fromEntries(paths.map((name) => [name, { width: 2, height: 2 }])) } } })).toString("base64"),
  } } });
  let remapped = 0;
  const pending = Compatibility.show({ official: input([]), mod: input(names), remappable: true },
    { onRemap: () => { remapped++; } });
  const remap = await drawn(dialog, "ok"), cancel = action(dialog, "cancel");
  assert.equal(remap.getAttribute("aria-label"), english.remap.action);
  const detail = action(dialog, "line1");  // under the "Changes:" heading
  assert.equal(detail.getAttribute("aria-label"), english.info.details);
  const viewing = detail.click();
  await until(() => dialog.children.length === 2 && actionsIn(dialog).filter((el) => el.dataset.action === "ok").length === 2);
  const full = dialog.children.at(-1), area = walk(full).find((el) => el.className === "popup-cards");
  assert.ok(parseFloat(area.children[0].style.height) > parseFloat(area.style.height));
  const text = walk(full).map((el) => el.textContent).join("\n");
  for (const name of names) assert.ok(text.replaceAll("\n", "").includes(name));
  assert.equal(remapped, 0);
  action(full, "ok").click();
  await viewing;
  assert.ok(remap.isConnected && cancel.isConnected);
  remap.click();
  await pending;
  assert.equal(remapped, 1);
});

// The compatibility result (owner, 2026-11-11): plain lines, "Changes:" as list items with their Details button as high as
// the line, the game's dotted rule, then "Auto update:" (or the author hints), and footer buttons one size smaller.
test("the compatibility result's list, rule, inline Details and smaller footer fit in every locale", async () => {
  const Compatibility = await import("../../src/bmpt/web/js/mod-compatibility.js");
  const NGUI = await import("../../src/bmpt/web/js/ngui.js");
  const { glyphsOf } = await import("../../src/bmpt/web/js/util.js");
  const { CONTROL, POPUP_FRAME, SMALL_BUTTON, XS_BUTTON, STATS_BOX, TYPE } = await import("../../src/bmpt/web/js/ui-tokens.js");
  await Popup.ready();
  const results = [
    // a portrait drawn anew whose old rig Update keeps
    [{ issues: [{ key: "atlasLayout", params: { name: "ch0252_spr.atlas" } }, { key: "atlasLayout", params: { name: "other.atlas" } },
      { key: "redrawn", params: {} }], author: true, oldRig: true }, true],
    // drawn anew, official animations the old skeleton lacks: named, the whole list behind Details, nothing to update
    [{ issues: [{ key: "atlasLayout", params: { name: "ako_spr.atlas" } }, { key: "redrawn", params: {} },
      { key: "oldRigAnimations", params: { names: "17、18、19、+1" }, detail: { names: "17、18、19、20" } }], author: true }, false],
    // the remap
    [{ issues: [{ key: "regions", params: { names: "face, body" } }, { key: "atlasSize", params: { name: "page.png", atlas: "2048 × 2048", texture: "1024 × 512" } }],
      author: true, remap: true }, true],
  ];
  try {
    for (const lang of ["en-US", "zh-CN", "zh-TW", "ja-JP", "ko-KR", "th-TH"]) {
      const ui = JSON.parse(readFileSync(new URL(`../../src/bmpt/locales/${lang}.json`, import.meta.url))).webui;
      setStrings(lang, Object.fromEntries(strings(ui)));
      for (const [n, [result, repair]] of results.entries()) {
        const what = `${lang} result ${n}`, lines = Compatibility.resultLines(result, repair);
        const texts = lines.map((line) => (typeof line === "string" ? line : line.text));
        assert.equal(texts[0], ui.compatibility.changes, what);
        assert.equal(texts.filter((text) => text === ui.compatibility.atlasLayout).length <= 1, true, `${what}: one line per finding`);
        const rule = lines.findIndex((line) => line.rule);
        // the rule and what Update does only where it repairs; nothing a user does not act on
        if (repair) {
          assert.ok(rule > 1, what);
          assert.deepEqual(texts.slice(rule + 1), [ui.compatibility.autoUpdate, result.oldRig ? ui.compatibility.oldRig : ui.compatibility.remapUpdate], what);
        } else assert.equal(rule, -1, what);
        const ok = repair ? ui.remap.action : ui.picker.ok, cancel = repair ? ui.popup.cancel : null;
        const fitTogether = lines.flatMap((line, i) => (line.rule ? [] : [i]));
        const layout = Popup.messageLayout({ lines, plain: true, scroll: true, ok, cancel, fitTogether });
        assert.equal(new Set(layout.body.fitted.filter(({ r }) => !r.rule).map(({ f }) => f.fs)).size, 1, `${what}: one type size`);
        for (const { r, f, room, bf, bw, bh } of layout.body.fitted) {
          if (r.rule) continue;
          assert.ok(f.fs >= 44, `${what}: "${r.text}" keeps the plain message's floor`);
          for (const line of f.text.split("\n")) assert.ok(glyphsOf(line, f.fs) <= room + 1e-6, `${what}: "${line}" within its room`);
          if (r.button) {
            // its face as high as its line of text (the shadow inset around it), its caption readable and inside it
            assert.equal(r.button.inline, "line", what);
            assert.equal(bh - XS_BUTTON.boxInsetH, Math.round(f.fs * NGUI.LINE_PITCH * Popup.LINE_BUTTON.height), `${what}: Details one XS ratio under its line`);
            assert.ok(bf.fs >= Popup.LINE_BUTTON.fsMin && bf.fs < bh - XS_BUTTON.boxInsetH, `${what}: Details caption ${bf.fs}`);
            assert.ok(glyphsOf(bf.text, bf.fs) < bw, `${what}: Details caption inside`);
          }
        }
        if (rule >= 0) {
          const divider = NGUI.find(layout.body.tree, `Rule_${rule}`).widgets[0];
          assert.deepEqual([divider.sprite, divider.h_, divider.color, divider.pivot], ["Common_Line_Dot", Popup.RULE.h, `${STATS_BOX.divider}/1.00`, 3], what);
          // across the text: from the lines' left edge, as wide as their box
          const ruleNode = NGUI.find(layout.body.tree, `Rule_${rule}`), heading = NGUI.find(layout.body.tree, "Line_0");
          assert.equal(ruleNode.pos[0], heading.pos[0], `${what}: the rule starts where the lines start`);
          assert.equal(divider.w_, layout.width - 2 * 100, `${what}: the rule spans the text box`);
        }
        // the footer two sizes smaller (owner, 2026-11-11): the small button's 402 x 136 bodies, captions at its size range
        const frame = Popup.popupFrame(ui.compatibility.result, layout.width, layout.height, { ok, cancel, footer: "S" });
        const btn = NGUI.find(frame, "Popup/Btn");
        for (const child of btn.children.filter((c) => c.active)) {
          const bg = NGUI.find(child, "BG (1)").widgets.find((w) => "sprite" in w && "w_" in w);
          assert.deepEqual([bg.w_, bg.h_], [SMALL_BUTTON.w, SMALL_BUTTON.h], what);
          const caption = NGUI.find(child, "Label_Locale").widgets[0];
          assert.ok(caption.fontSize <= SMALL_BUTTON.fs && caption.fontSize >= TYPE.body.min, `${what}: ${caption.text}`);
        }
        assert.deepEqual(Popup.FOOTER.M.control, CONTROL.buttonM);
        assert.deepEqual([Popup.FOOTER.S.control.h, Popup.FOOTER.S.control.fs, Popup.FOOTER.S.control.minW],
          [SMALL_BUTTON.h, SMALL_BUTTON.fs, SMALL_BUTTON.w]);
        // the title, centred, keeps clear of the close button
        const title = NGUI.find(frame, "Popup/Base/BG/Label_Title_Locale").widgets[0];
        const clear = layout.width - 2 * (POPUP_FRAME.close.right + POPUP_FRAME.close.size);
        assert.ok(glyphsOf(title.text, title.fontSize) <= clear, `${what}: title "${title.text}" at ${title.fontSize} clear of the close button`);
      }
    }
  } finally { setStrings("en-US", Object.fromEntries(strings(english))); }
});

test("a message resolves with the button and the checkbox however it closes", async () => {
  const before = body.appendChild(new El("button"));
  before.focus();
  const m = Popup.message({ title: "Classic UI Open", lines: ["Close the classic UI first"], ok: "OK", cancel: "Bring to Front", check: { label: "Apply automatically once it closes" } });
  await drawn(dialog, "ok");
  assert.equal(dialog.hidden, false);
  assert.equal(document.activeElement, dialog.children[0], "the popup takes the focus");
  assert.equal(await state(m), "pending");
  const check = action(dialog, "check");
  assert.equal(check.getAttribute("role"), "checkbox");
  assert.equal(check.getAttribute("aria-checked"), "false");
  check.click();
  assert.equal(check.getAttribute("aria-checked"), "true");
  press("Escape");
  assert.deepEqual(await m, { button: null, checked: true });
  assert.equal(dialog.hidden, true);
  assert.equal(document.activeElement, before, "the focus goes back");
  before.remove();

  for (const [name, button] of [["ok", "ok"], ["cancel", "cancel"], ["close", null]]) {
    const p = Popup.message({ title: "Remove", lines: ["The game may not start"], ok: "Remove", cancel: "Cancel", check: { label: "Don't Ask Again", checked: true } });
    (await drawn(dialog, name)).click();
    assert.deepEqual(await p, { button, checked: true }, name);
  }
  // without a checkbox, checked is null; the backdrop closes like Escape
  const p = Popup.message({ title: "Notice", lines: ["Quit?"], ok: "Confirm", cancel: "Cancel" });
  await drawn(dialog, "ok");
  Popup.top().close("backdrop");
  assert.deepEqual(await p, { button: null, checked: null });
});

test("messages stack: Escape closes the one in front, the line buttons act", async () => {
  const lower = Popup.message({ title: "Enable Mods", lines: ["Mods stay in the mod library", { text: "Not switched: 2", button: { label: "Retry", fn: () => retried.push(1) } },
    { text: "Other mods not checked", button: { label: "Allow Check", fn: ({ close }) => close("ok") } }], ok: "OK" });
  const retried = [];
  await drawn(dialog, "line1");
  action(dialog, "line1").click();
  assert.deepEqual(retried, [1]);
  const upper = Popup.message({ title: "Official File Overwritten", lines: ["No official copy left"], ok: "OK" });
  await tick();
  assert.equal(dialog.children.length, 2);
  press("Escape");
  assert.deepEqual(await upper, { button: null, checked: null });
  assert.equal(await state(lower), "pending");
  assert.equal(dialog.hidden, false);
  // a line's button may close its popup
  action(dialog, "line2").click();
  assert.deepEqual(await lower, { button: "ok", checked: null });
  assert.equal(dialog.hidden, true);
});

test("confirm keys press OK while the popup is in front and holds the focus; none by default", async () => {
  const keyOn = (layer, target, key, extra = {}) => {
    const ev = { key, target, defaultPrevented: false, preventDefault() { this.defaultPrevented = true; }, ...extra };
    for (const fn of layer.listeners.keydown || []) fn(ev);
    return ev;
  };
  // the quit prompt: Enter and Space, as the PC client binds its popups' confirm
  const quit = Popup.message({ title: "Notice", lines: ["Quit?"], ok: "Confirm", cancel: "Cancel", confirmKeys: ["Enter", " "] });
  await drawn(dialog, "ok");
  const layer = dialog.children.at(-1);
  assert.equal(keyOn(layer, action(dialog, "cancel"), "Enter").defaultPrevented, false, "a focused button takes its own key");
  for (const extra of [{ repeat: true }, { isComposing: true }, { keyCode: 229 }, { ctrlKey: true }, { defaultPrevented: true }]) {
    keyOn(layer, layer, "Enter", extra);
  }
  keyOn(layer, layer, "a");
  assert.equal(await state(quit), "pending");
  // another message in front: the keys are its own
  const front = Popup.message({ title: "Remove", lines: ["The game may not start"], ok: "Remove" });
  await tick();
  assert.equal(keyOn(layer, layer, "Enter").defaultPrevented, false);
  press("Escape");
  await front;
  assert.ok(keyOn(layer, layer, " ").defaultPrevented);
  assert.deepEqual(await quit, { button: "ok", checked: null });
  // without confirmKeys, Enter presses nothing
  const plain = Popup.message({ title: "Remove", lines: ["The game may not start"], ok: "Remove", cancel: "Cancel" });
  await drawn(dialog, "ok");
  const l2 = dialog.children.at(-1);
  assert.equal(keyOn(l2, l2, "Enter").defaultPrevented, false);
  assert.equal(await state(plain), "pending");
  action(dialog, "cancel").click();
  await plain;
});

test("the hand-off prompt saves Don't Ask Again only with Open (decision 6)", async () => {
  const { ITEMS, activate, handOffPrompt } = await import("../../src/bmpt/web/js/nav-items.js");
  const T = (key) => key;
  const ask = (item) => Popup.message(handOffPrompt(item, T));
  const run = () => {
    const log = [];
    const done = activate(ITEMS.find((item) => item.classic === "crc_tool"), { prompt: true, ask, go: () => log.push("go"), open: async (tab) => log.push(`open ${tab}`), savePrompt: async (v) => log.push(`save ${v}`) });
    return { log, done };
  };
  for (const [ticked, button, expected] of [[true, "ok", ["save false", "open crc_tool"]], [false, "ok", ["open crc_tool"]], [true, "cancel", []], [true, "close", []]]) {
    const r = run();
    const box = await drawn(dialog, "check");
    if (ticked) box.click();
    action(dialog, button).click();
    await r.done;
    assert.deepEqual(r.log, expected, `${ticked ? "ticked" : "unticked"}, ${button}`);
  }
});

test("a message that must be answered has no close button and ignores Escape", async () => {
  const p = Popup.message({ title: "Mods Back to Official", lines: ["Game files verified, repaired or updated"], ok: "Apply Mod Configuration", cancel: "Keep as Is", close: false });
  await drawn(dialog, "ok");
  assert.equal(action(dialog, "close"), undefined);
  assert.ok(press("Escape").defaultPrevented);
  assert.equal(await state(p), "pending");
  action(dialog, "cancel").click();
  assert.deepEqual(await p, { button: "cancel", checked: null });
});

const items = [
  { key: "a", label: "A", full: "A", thumb: null, big: null, size: 100 },
  { key: "b", label: "B", full: "B", thumb: null, big: null, size: 250 },
  { key: "c", label: "C", full: "C", thumb: null, big: null, size: 4000, disabled: true, dim: true, badge: { text: "Overwritten", color: "#d9822b" } },
  { key: "d", label: "D", full: "D", thumb: null, big: null },
];

test("several cards ticked: all that can be, to start with, and their size added up", () => {
  const all = Popup.selectable(items);
  assert.deepEqual([...all], ["a", "b", "d"]);
  assert.equal(Popup.sizeOf(items, all), 350);
  const less = Popup.toggled(all, items, "b");
  assert.deepEqual([...less].sort(), ["a", "d"]);
  assert.deepEqual([...all], ["a", "b", "d"], "the selection given is left as it is");
  assert.equal(Popup.sizeOf(items, less), 100);
  assert.deepEqual([...Popup.toggled(less, items, "b")].sort(), ["a", "b", "d"]);
  assert.deepEqual([...Popup.toggled(less, items, "c")].sort(), ["a", "d"], "a card that cannot be ticked");
  assert.deepEqual([...Popup.toggled(less, items, "zz")].sort(), ["a", "d"]);
  assert.equal(Popup.sizeOf(items, new Set()), 0);
});

test("a list popup with several cards ticked, its buttons and no way out but them", async () => {
  const picked = [], pressed = [];
  let selected = Popup.selectable(items);
  const draw = () => Popup.listPopup({
    host: prompt, title: "Mods Not in the Library", items, multi: true, selected,
    onSelect: (key) => { picked.push(key); selected = Popup.toggled(selected, items, key); return draw(); },
    buttons: [{ label: "Later", fn: () => pressed.push("later") }, { label: "Add to Library", fn: () => pressed.push("add"), primary: true, action: "add" }],
    hint: "Lost to a game update or file check unless added", footer: "Selected: 3",
    check: { label: "Don't Ask Again", checked: false },
    details: (it) => ({ title: "Portrait", fields: [["File", `${it.key}.bundle`]], actions: [] }),
  });
  await draw();
  const cards = () => walk(prompt).filter((e) => e.dataset?.key);
  assert.deepEqual(cards().map((c) => c.dataset.key), ["a", "b", "c", "d"], "in the list's order");
  assert.deepEqual(cards().map((c) => c.getAttribute("aria-checked")), ["true", "true", "false", "true"]);
  assert.equal(action(prompt, "close"), undefined, "no close button");
  assert.ok(press("Escape").defaultPrevented);
  assert.equal(prompt.hidden, false);
  await cards().find((c) => c.dataset.key === "b").click();
  await tick();
  assert.deepEqual(picked, ["b"]);
  assert.deepEqual(cards().map((c) => c.getAttribute("aria-checked")), ["true", "false", "false", "true"]);
  action(prompt, "add").click();
  action(prompt, "button0").click();
  assert.deepEqual(pressed, ["add", "later"]);
  action(prompt, "check").click();
  prompt.hidden = true;
});

test("switch detail actions retain the prefab pattern at every button width and keep their hit areas", async () => {
  const near = (a, b) => assert.ok(Math.abs(a - b) < 0.02, `${a} != ${b}`);
  for (const count of [1, 2, 3, 4]) {
    let clicked = -1;
    await Popup.listPopup({ host: prompt, title: "Portrait", items: [items[0]], selected: items[0].key,
      details: () => ({ title: "Portrait", fields: [], actions: Array.from({ length: count }, (_, i) =>
        [i ? "Check compatibility" : "Show in Finder", () => { clicked = i; }]) }) });
    for (let i = 0; i < count; i++) {
      const path = `Details/Act_${i}/Body`, findPart = (p) => walk(prompt).find((e) => e.dataset.path === p);
      const body = findPart(path), corner = findPart(`${path}/Corner`), box = (el) =>
        ["left", "top", "width", "height"].map((key) => parseFloat(el.style[key]));
      const [x, y, w, h] = box(body), [dx, dy, dw, dh] = box(corner), k = 104 / 177 * S; // the Sort prefab's 177-high body drawn at the XS height
      near(x + w - dx - dw, 4 * k);
      near(dy - y, 6 * k);
      near(dw, 126 * k);
      near(dh, 70 * k);
      const button = walk(prompt).find((e) => e.tagName === "BUTTON" && e.style.left === `${x}px` && e.style.top === `${y}px`);
      assert.ok(button);
      assert.deepEqual(box(button), box(body));
      button.click();
      assert.equal(clicked, i);
    }
    prompt.hidden = true;
  }
});

test("Update.check resolves when its popup closes, not when it is drawn", async () => {
  update = null;
  assert.equal(await Update.check(consent), null, "no newer release: nothing to wait for");
  update = { current: "0.1.0-alpha.3", version: "0.1.0-beta", installable: true };
  const later = Update.check(consent);
  await drawn(consent, "later");
  assert.equal(await state(later), "pending");
  assert.equal(Popup.top().host, consent, "on the popup stack");
  action(consent, "later").click();
  assert.equal(await later, "later");
  assert.equal(consent.hidden, true);
  // Escape is Later
  const escaped = Update.check(consent);
  await drawn(consent, "later");
  press("Escape");
  assert.equal(await escaped, "later");
  // a build that cannot be installed here: the download page
  update = { ...update, installable: false };
  const page = Update.check(consent);
  (await drawn(consent, "page")).click();
  assert.equal(await page, "page");
  assert.ok(calls.includes("app.openReleasePage"));
});

test("a late background update waits for every popup and job before showing on the current screen", async () => {
  let answer, job = true;
  handlers.set("app.checkUpdate", () => new Promise((resolve) => { answer = resolve; }));
  const pending = Update.check(consent, undefined, { beforeShow: () => Popup.whenIdle(5, () => job) });
  await until(() => answer);
  const first = Popup.message({ title: "Current screen", lines: ["Details"] });
  await drawn(dialog, "close");
  answer({ result: available });
  await tick();
  assert.equal(consent.hidden, true, "a late result must not cover the current popup");
  action(dialog, "close").click();
  await first;
  await tick();
  assert.equal(consent.hidden, true, "the job still runs after that popup closes");
  const second = Popup.message({ title: "Another screen", lines: ["Result"] });
  await drawn(dialog, "close");
  job = false;
  await new Promise((resolve) => setTimeout(resolve, 20));
  assert.equal(consent.hidden, true, "recheck popups after the job finishes");
  action(dialog, "close").click();
  await second;
  (await drawn(consent, "later")).click();
  assert.equal(await pending, "later");
  handlers.clear();
});

test("a queued update never replaces a consent popup in its own host", async () => {
  consent.hidden = false;
  const current = consent.appendChild(new El());
  const pending = Update.check(consent, available, { beforeShow: () => Popup.whenIdle(5) });
  await tick();
  assert.equal(consent.children[0], current);
  assert.equal(action(consent, "later"), undefined);
  consent.hidden = true;
  consent.innerHTML = "";
  (await drawn(consent, "later")).click();
  assert.equal(await pending, "later");
});

test("automatic checks with no release or an offline error do not wait for popup readiness", async () => {
  for (const response of [{ result: null }, { error: { code: "internal" } }]) {
    handlers.set("app.checkUpdate", () => response);
    assert.equal(await Update.check(consent, undefined, { beforeShow: () => assert.fail("no offer to queue") }), null);
    assert.equal(consent.hidden, true);
  }
  handlers.clear();
});

test("update progress stays in NGUI, blocks buttons and Escape, and completion waits for Restart", async () => {
  update = { ...available };
  const start = calls.length;
  let release;
  handlers.set("app.update", () => new Promise((resolve) => { release = () => resolve({ result: "download" }); }));
  handlers.set("jobs.get", () => ({ result: { state: "running" } }));
  const done = Update.check(consent);
  const first = await drawn(consent, "update");
  first.click();
  first.click();
  await until(() => release);
  const later = action(consent, "later"), downloading = action(consent, "update");
  assert.ok(later.disabled && downloading.disabled);
  assert.equal(downloading.getAttribute("aria-disabled"), "true");
  assert.equal(part("Btn/Btn_Ok/Label_Locale").style.color, "#828282");
  later.click();
  downloading.click();
  assert.ok(press("Escape").defaultPrevented);
  assert.equal(await state(done), "pending");
  assert.equal(consent.hidden, false);
  release();
  await until(() => calls.slice(start).includes("jobs.get"));
  const progress = async (current, total, p) => {
    events.emit("job.progress", { id: "download", current, total, label: "build.dmg" });
    await until(() => part("Status")?.textContent === `Downloading ${p}%`);
    return part("Progress/Fill");
  };
  assert.equal(await progress(20, 0, 0), undefined, "unknown total: zero, without an invalid bar width");
  const fill = await progress(50, 100, 50);
  assert.equal(parseFloat(fill.style.width) / parseFloat(part("Progress").style.width), 0.5);
  assert.ok(fill.style.backgroundImage, "the bar uses a rendered, tinted sprite");
  const bounds = (el) => {
    const top = parseFloat(el.style.top), height = parseFloat(el.style.height);
    return { top, height, bottom: top + height };
  };
  const bar = bounds(part("Progress")), status = bounds(part("Status")), frame = bounds(part("Base/BG"));
  assert.ok(bar.top - status.bottom >= 6 * S, "the status has a gap above the bar");
  for (const name of ["Btn_Ok", "Btn_Cancel"]) {
    const button = bounds(part(`Btn/${name}/BG (1)`));
    assert.ok(Math.abs(button.height - CONTROL.buttonM.h * S) < 0.01, "check the prefab's button background height at the three-choice scale");
    assert.ok(button.top - bar.bottom >= 12 * S, "the full button background clears the progress bar");
    assert.ok(button.bottom < frame.bottom, "the buttons stay within the frame");
  }
  await progress(200, 100, 100);
  await progress(-10, 100, 0);
  // In-flight progress renders cannot replace the ready state after completion.
  events.emit("job.progress", { id: "download", current: 100, total: 100 });
  events.emit("job.done", { id: "download", result: { file: "/session/build.dmg" } });
  await drawn(consent, "restart");
  assert.equal(part("Status").textContent, "Download Complete");
  assert.equal(part("Progress"), undefined);
  assert.equal(action(consent, "restart").disabled, false);
  assert.equal(await state(done), "pending");
  assert.equal(calls.slice(start).filter((m) => m === "app.update").length, 1);
  first.click();
  await tick();
  assert.equal(calls.slice(start).includes("app.installUpdate"), false);
  action(consent, "later").click();
  assert.equal(await done, "later");
  assert.equal(calls.slice(start).includes("app.installUpdate"), false);

  const ready = { ...available, downloaded: true, file: "/session/build.dmg" };
  // Settings passes its result; startup and subsequent checks fetch it themselves.
  for (const supplied of [true, false]) {
    update = ready;
    const before = calls.length;
    const again = supplied ? Update.check(consent, ready) : Update.check(consent);
    await drawn(consent, "restart");
    assert.equal(part("Status").textContent, "Download Complete");
    assert.deepEqual(calls.slice(before), supplied ? [] : ["app.checkUpdate"]);
    press("Escape");
    assert.equal(await again, "later");
  }
  handlers.clear();
});

test("download failures stay inside the popup, allow retry, and resolve only when dismissed", async () => {
  update = { ...available };
  handlers.set("app.update", () => ({ result: "retry" }));
  handlers.set("jobs.get", () => ({ result: { state: "running" } }));
  const start = calls.length;
  const done = Update.check(consent);
  (await drawn(consent, "update")).click();
  await until(() => calls.slice(start).includes("jobs.get"));
  events.emit("job.failed", { id: "retry", code: "update_checksum_mismatch" });
  await until(() => part("Status")?.textContent === english.error.update_checksum_mismatch);
  assert.equal(await state(done), "pending");
  assert.equal(consent.hidden, false);
  assert.equal(action(consent, "update").disabled, false);
  assert.equal(action(consent, "later").disabled, false);
  assert.equal(part("Progress"), undefined);
  const beforeRetry = calls.length;
  action(consent, "update").click();
  await until(() => calls.slice(beforeRetry).includes("jobs.get"));
  events.emit("job.done", { id: "retry", result: { file: "/session/retry.dmg" } });
  await drawn(consent, "restart");
  assert.equal(part("Status").textContent, "Download Complete");
  assert.equal(calls.slice(start).filter((m) => m === "app.update").length, 2);
  action(consent, "later").click();
  assert.equal(await done, "later", "a successful retry clears the failure");

  for (const code of ["update_download_failed", "internal"]) {
    handlers.set("app.update", () => ({ error: code === "internal" ? {} : { code } }));
    const failed = Update.check(consent);
    (await drawn(consent, "update")).click();
    // an error with nothing to say: error.internal without its empty detail (UI-37)
    await until(() => part("Status")?.textContent === english.error[code].replace(/\{\w+\}/g, "").replace(/[\s:]+$/, ""));
    assert.equal(await state(failed), "pending");
    press("Escape");
    assert.equal(await failed, "update", "the failed-update result is deferred until the popup closes");
  }
  handlers.clear();
});

test("only Restart installs, and every install refusal stays ready for another attempt", async () => {
  update = { ...available, downloaded: true, file: "/session/verified.dmg" };
  const start = calls.length;
  let answer;
  handlers.set("app.installUpdate", () => new Promise((resolve) => { answer = resolve; }));
  const done = Update.check(consent);
  for (const code of ["classic_open", "busy", "update_unavailable", "update_unsupported", "update_restart_failed"]) {
    answer = null;
    (await drawn(consent, "restart")).click();
    await until(() => answer);
    assert.ok(action(consent, "restart").disabled);
    action(consent, "restart").click();
    action(consent, "later").click();
    press("Escape");
    assert.equal(await state(done), "pending");
    answer({ error: { code } });
    await until(() => part("Status")?.textContent === english.error[code]);
    assert.equal(action(consent, "restart").disabled, false);
    assert.equal(action(consent, "restart").getAttribute("aria-label"), "Restart");
    assert.equal(consent.hidden, false);
  }
  assert.equal(calls.slice(start).filter((m) => m === "app.installUpdate").length, 5);
  assert.equal(calls.slice(start).includes("app.update"), false);
  assert.ok(requests.filter((r) => r.method === "app.installUpdate").every((r) => r.params.file === update.file));
  action(consent, "later").click();
  assert.equal(await done, "update");
  handlers.clear();
});

test("Restart recovers to ready when installUpdate succeeds but the page is still alive", async (t) => {
  const timers = [], realTimeout = setTimeout;
  t.mock.method(globalThis, "setTimeout", (fn, ms, ...args) => {
    if (ms === 3000) { timers.push(fn); return 0; }
    return realTimeout(fn, ms, ...args);
  });
  update = { ...available, downloaded: true, file: "/session/verified.dmg" };
  handlers.set("app.installUpdate", () => ({ result: null }));
  const restarted = Update.check(consent);
  (await drawn(consent, "restart")).click();
  await until(() => timers.length);
  assert.equal(part("Status").textContent, english.update.restarting);
  assert.equal(await state(restarted), "pending", "the app closes on successful install; startup cannot advance under it");
  assert.ok(action(consent, "restart").disabled);
  timers.shift()();
  await until(() => part("Status")?.textContent === english.error.update_restart_failed);
  assert.equal(action(consent, "restart").disabled, false);
  assert.equal(action(consent, "later").disabled, false);
  assert.equal(await state(restarted), "pending");
  handlers.set("app.installUpdate", () => ({ error: { code: "busy" } }));
  action(consent, "restart").click();
  await until(() => part("Status")?.textContent === english.error.busy);
  assert.equal(action(consent, "restart").disabled, false);
  press("Escape");
  assert.equal(await restarted, "update");
  handlers.clear();
});

test("concurrent checks share one request and popup through download and dismissal, per host", async () => {
  let answer;
  const start = calls.length;
  handlers.set("app.checkUpdate", () => new Promise((resolve) => { answer = resolve; }));
  handlers.set("app.update", () => ({ result: "single-download" }));
  handlers.set("jobs.get", () => ({ result: { state: "running" } }));
  const first = Update.check(consent), second = Update.check(consent);
  assert.equal(second, first, "the host is reserved before the check finishes");
  await until(() => answer);
  assert.equal(calls.slice(start).filter((m) => m === "app.checkUpdate").length, 1);
  answer({ result: available });
  (await drawn(consent, "update")).click();
  await until(() => calls.slice(start).includes("jobs.get"));
  const locked = action(consent, "update");
  assert.equal(Update.check(consent, { ...available, version: "9.0.0" }), first);
  await tick();
  assert.equal(action(consent, "update"), locked, "a late result cannot replace the locked popup");
  press("Escape");
  assert.equal(consent.hidden, false);
  assert.equal(await state(first), "pending");
  // Another host has its own popup and promise.
  const other = host("other-update"), independent = Update.check(other, available);
  assert.notEqual(independent, first);
  (await drawn(other, "later")).click();
  assert.equal(await independent, "later");
  other.remove();
  events.emit("job.done", { id: "single-download", result: { file: "/session/single.dmg" } });
  await drawn(consent, "restart");
  assert.equal(Update.check(consent), first);
  action(consent, "later").click();
  assert.deepEqual(await Promise.all([first, second]), ["later", "later"]);
  const again = Update.check(consent, available);
  assert.notEqual(again, first, "a closed popup releases the host");
  (await drawn(consent, "later")).click();
  assert.equal(await again, "later");
  handlers.clear();
});

test("Settings disables Check for Updates while pending and uses the shared popup check", async () => {
  const Settings = await import("../../src/bmpt/web/js/settings.js");
  handlers.set("settings.get", () => ({ result: {
    version: "1.0.0", language: "en-US", languages: [["en-US", "English"]].map(([code, name]) => ({ code, name })),
    cacheDir: "/test/cache", native: true,
  } }));
  handlers.set("mods.state", () => ({ result: {} }));
  let answer;
  handlers.set("app.checkUpdate", () => new Promise((resolve) => { answer = resolve; }));
  const checkButton = () => walk(settings).find((e) => e.tagName === "BUTTON" && e.getAttribute("aria-label") === english.settings.update.check);
  await Settings.open();
  const start = calls.length, button = checkButton();
  assert.ok(button);
  button.click();
  assert.equal(button.disabled, true, "disabled synchronously, before any RPC or redraw");
  button.click(); // even a stale or programmatic second click cannot start another check
  await until(() => answer);
  assert.equal(checkButton(), undefined, "a pending redraw has no enabled check hit area");
  assert.equal(calls.slice(start).filter((m) => m === "app.checkUpdate").length, 1);
  answer({ result: null });
  await until(() => checkButton());
  // no newer release: the neutral notice says so, Settings stays open behind it
  await drawn(dialog, "ok");
  assert.ok(walk(dialog).some((e) => e.textContent === english.quit.title), "the notice caption");
  assert.ok(walk(dialog).some((e) => e.textContent === english.settings.update.none));
  assert.equal(action(dialog, "cancel"), undefined, "a notice, not a failure with an Error Report");
  assert.equal(settings.hidden, false);
  action(dialog, "ok").click();
  await tick();
  assert.equal(dialog.hidden, true);
  // A later check is allowed; its popup owns the same host and startup shares its promise.
  answer = null;
  checkButton().click();
  await until(() => answer);
  const shared = Update.check(consent);
  answer({ result: available });
  await drawn(consent, "update");
  assert.equal(settings.hidden, true);
  assert.equal(calls.slice(start).filter((m) => m === "app.checkUpdate").length, 2);
  assert.equal(Update.check(consent), shared);
  press("Escape");
  assert.equal(await shared, "later");
  handlers.clear();
});

test("Settings includes Thai as a sixth language with its native label and language tag", async () => {
  const Settings = await import("../../src/bmpt/web/js/settings.js");
  const languages = ["zh-CN", "zh-TW", "en-US", "ja-JP", "ko-KR", "th-TH"].map((code) => ({
    code, name: JSON.parse(readFileSync(new URL(`../../src/bmpt/locales/${code}.json`, import.meta.url))).webui.languageName,
  }));
  handlers.set("settings.get", () => ({ result: { version: "1.0.0", language: "en-US", languages } }));
  handlers.set("mods.state", () => ({ result: {} }));
  try {
    await Settings.open();
    const labels = walk(settings).filter((e) => /Rows\/Language\/Opt_\d+\/Label$/.test(e.dataset.path || ""));
    assert.equal(labels.length, 6);
    assert.equal(labels[5].textContent, "ไทย");
    assert.equal(labels[5].lang, "th-TH");
    assert.ok(walk(settings).some((e) => e.tagName === "BUTTON" && e.getAttribute("aria-label") === "ไทย"));
  } finally {
    press("Escape");
    handlers.clear();
  }
});

test("a manual Settings check joins an automatic offer already queued behind Settings", async () => {
  const Settings = await import("../../src/bmpt/web/js/settings.js");
  handlers.set("settings.get", () => ({ result: {
    version: "1.0.0", language: "en-US", languages: [{ code: "en-US", name: "English" }],
    cacheDir: "/test/cache", native: true,
  } }));
  handlers.set("mods.state", () => ({ result: {} }));
  let answer;
  handlers.set("app.checkUpdate", () => new Promise((resolve) => { answer = resolve; }));
  const start = calls.length;
  const automatic = Update.check(consent, undefined, { beforeShow: () => Popup.whenIdle(5) });
  await until(() => answer);
  await Settings.open();
  answer({ result: available });
  await tick();
  assert.equal(settings.hidden, false);
  assert.equal(consent.hidden, true);
  walk(settings).find((e) => e.tagName === "BUTTON" && e.getAttribute("aria-label") === english.settings.update.check).click();
  await drawn(consent, "update");
  assert.equal(settings.hidden, true, "the joining check's onCheck still closes Settings");
  assert.equal(calls.slice(start).filter((m) => m === "app.checkUpdate").length, 1);
  action(consent, "later").click();
  assert.equal(await automatic, "later");
  handlers.clear();
});

test("every update locale has short completion and restart labels", () => {
  for (const language of ["en-US", "zh-CN", "zh-TW", "ja-JP", "ko-KR", "th-TH"]) {
    const locale = JSON.parse(readFileSync(new URL(`../../src/bmpt/locales/${language}.json`, import.meta.url))).webui.update;
    assert.deepEqual(Object.keys(locale).sort(), Object.keys(english.update).sort());
    for (const key of ["complete", "restart"]) assert.ok(locale[key] && !/[.。!！·・]/u.test(locale[key]));
  }
});

test("the start popups come one at a time, in order; one that fails does not stop the others", async () => {
  const log = [];
  let release;
  const first = new Promise((resolve) => { release = resolve; });
  const done = Popup.sequence([
    ["consent", () => { log.push("consent"); return first; }],
    ["scan", () => false], // nothing to show
    ["update", () => { log.push("update"); throw new Error("offline"); }],
    ["drift", async () => { log.push("drift"); }],
  ]);
  const errors = console.error;
  console.error = () => {};
  try {
    await tick();
    assert.deepEqual(log, ["consent"], "the next one waits for the first to close");
    release();
    await done;
  } finally {
    console.error = errors;
  }
  assert.deepEqual(log, ["consent", "update", "drift"]);
});

test("whenIdle waits for the open popups to close", async () => {
  const h = body.appendChild(new El());
  Popup.opened(h, () => {});
  let idle = false;
  const p = Popup.whenIdle(5).then(() => { idle = true; });
  await new Promise((resolve) => setTimeout(resolve, 20));
  assert.equal(idle, false);
  h.hidden = true;
  await p;
  assert.equal(idle, true);
  assert.equal(Popup.anyOpen(), false);
  h.remove();
});

test("whenIdle waits for caller work even without a popup", async () => {
  let busy = true;
  const pending = Popup.whenIdle(5, () => busy);
  assert.equal(await state(pending), "pending");
  busy = false;
  await pending;
});

test("the log popup preserves literal text, opens at the bottom and Copy leaves it open", async () => {
  const original = Array.from({ length: 500 }, (_, i) => `${i}: <script>literal</script>`).join("\n");
  let copied = null;
  const done = Popup.textViewer({ title: "Log", text: original, copy: "Copy", onCopy: () => { copied = original; } });
  await drawn(dialog, "copy");
  const pre = walk(dialog).find((n) => n.tagName === "PRE");
  assert.equal(pre.textContent, original);
  assert.equal(pre.children.length, 0);
  assert.equal(pre.scrollTop, pre.scrollHeight);
  action(dialog, "copy").click();
  assert.equal(copied, original);
  assert.equal(dialog.hidden, false);
  press("Escape");
  await done;
  assert.equal(dialog.hidden, true);
});

// UI-31: the log popup's fallback (no game frame) draws with the plain popup's styles, sized to the log
test("the log popup's plain fallback uses only classes the style sheet defines", () => {
  const source = readFileSync(new URL("../../src/bmpt/web/js/popup.js", import.meta.url), "utf8");
  const viewer = source.slice(source.indexOf("export function textViewer("), source.indexOf("// --- ordinary refusals ---"));
  const fallback = viewer.slice(viewer.indexOf("} catch (err) {"));
  const css = readFileSync(new URL("../../src/bmpt/web/css/app.css", import.meta.url), "utf8");
  assert.match(fallback, /plainMessage\(layer, \{ title, width: TEXT_VIEWER.w, height: TEXT_VIEWER.h/);
  assert.match(fallback, /TEXT_VIEWER.box.width/);
  assert.match(fallback, /TEXT_VIEWER.box.height/);
  const shared = source.slice(source.indexOf("export function plainMessage("), source.indexOf("// a fixed size (max equals min)"));
  for (const name of ["plain-popup-back", "plain-popup", "plain-message-lines", "message-actions", "message-button"]) {
    assert.ok(shared.includes(`"${name}"`));
    assert.match(css, new RegExp(`\\.${name}[\\s,{.:]`), `.${name} has a rule`);
  }
});

test("new card dots hide for in-use and selected mods independently", async () => {
  const items = [
    { key: "current", label: "In use", inUse: true, isNew: true },
    { key: "pending", label: "Pending", isNew: true },
    { key: "other", label: "Other", isNew: true },
    { key: "seen", label: "Seen" },
  ];
  for (const selected of ["current", "pending", "other"]) {
    await Popup.listPopup({ host: prompt, title: "Portrait", items, selected });
    const hasDot = (key) => walk(prompt).some((el) => el.dataset.path?.endsWith(`Card_${key}/Dot`));
    assert.equal(hasDot("current"), false, "an auto-applied mod stays unmarked after another card is picked");
    assert.equal(hasDot("pending"), selected !== "pending");
    assert.equal(hasDot("other"), selected !== "other");
    assert.equal(hasDot("seen"), false);
    assert.ok(items[0].isNew, "rendering must not consume the tab or roster mark");
  }
  prompt.hidden = true;
});

test("portrait and memorial lobby mods share the localized Mod chip under the name", async () => {
  for (const lang of ["en-US", "zh-CN", "zh-TW", "ja-JP", "ko-KR", "th-TH"]) {
    const ui = JSON.parse(readFileSync(new URL(`../../src/bmpt/locales/${lang}.json`, import.meta.url))).webui;
    setStrings(lang, { "webui.type.added": ui.type.added, "webui.popup.inUse": ui.popup.inUse });
    for (const kind of ["spr", "lobby"]) {
      await Popup.listPopup({ host: prompt, title: kind, items: [{ key: "m", kind: "mod", label: "Library mod", full: "Library mod", inUse: true }],
        selected: "m", okText: "OK", details: () => ({ title: kind, fields: [], actions: [] }) });
      const captions = walk(prompt).map((el) => el.textContent);
      assert.ok(captions.includes(ui.type.added), `${lang} ${kind}`);
      assert.ok(captions.includes(ui.popup.inUse), `${lang} ${kind} In Use`);
      prompt.hidden = true;
    }
  }
});

// Exercise the detail actions with the real RPC/job and popup code. The loader exposes its private actions only
// to this test and replaces thumbnail rendering; panel/player drawing is observed through the setup callback.
const DETAIL = new URL("../../src/bmpt/web/js/detail.js", import.meta.url).href;
const THUMBS = new URL("../../src/bmpt/web/js/thumbs.js", import.meta.url).href;
const detailTest = `
export { baseOf, detailsBody, renderPopup, runUpdate, runCompatibility, copyName };
export function setupPreviewTest(data, kind, onLeft = async () => {}) {
  fam = data; variantKey = data.variants[0].key; ctx = { invalidateRoster() {} };
  open = true; busy = false; tab = kind === "lobby" ? "lobby" : "portrait";
  leftPage = new Promise(() => {});
  popupKind = kind; popupSeg = 0; pending = shownSource(variant(), kind); previewEnabled = modsOn();
  switchThumbnails?.cancel(); switchThumbnails = thumbnailScope(); switchPopupPage = pageToken;
  expression = "03"; mainPlayer = { cached: true }; lobbyShot = "cached";
  lobbySeq = "talk"; lobbyAll = true; lobbyStale = false; shownLeft = tab; shownKey = "cached";
  renderScreen = renderChips = renderExpressions = async () => {};
  renderLeft = onLeft;
}
export const previewTestState = () => ({ expression, mainPlayer, lobbyShot, lobbySeq, lobbyAll, lobbyStale, shownKey, pending });
`;
const thumbsTest = `
export const forgotten = [];
export const forget = (base) => forgotten.push(base);
export const thumb = async () => null;
export const withThumbnailFallback = async (picture, fallback) => await picture || fallback();
export function thumbnailScope() {
  const controller = new AbortController();
  return { signal: controller.signal, thumb, cancel: () => controller.abort() };
}
export const idleAnimation = () => null;
export const prioritise = () => {};
`;
register(`data:text/javascript,${encodeURIComponent(`
export async function load(url, ctx, next) {
  if (url === ${JSON.stringify(THUMBS)}) return { format: "module", shortCircuit: true, source: ${JSON.stringify(thumbsTest)} };
  const result = await next(url, ctx);
  if (url === ${JSON.stringify(DETAIL)}) {
    const source = typeof result.source === "string" ? result.source : new TextDecoder().decode(result.source);
    return { ...result, source: source + ${JSON.stringify(detailTest)} };
  }
  return result;
}`)}`);
const Detail = await import(DETAIL);
const Mods = await import("../../src/bmpt/web/js/mod-switch.js");
const Thumbs = await import(THUMBS);
const switchHost = host("popup");
const previewFamily = () => ({ key: "a", version: "rv1", variants: [
  { key: "a", spr: "a_spr", lobby: "a_home", configured: { spr: "mod:p", lobby: "mod:l" },
    state: { files: ["spr", "lobby"].map((kind) => ({ kind, file: `/game/${kind}.bundle`, applied: "official", official: true, crc: "ok" })) },
    modList: [{ id: "p", target: "a", kind: "spr", label: "Portrait mod" }, { id: "l", target: "a", kind: "lobby", label: "Lobby mod" }] },
  { key: "b", spr: "b_spr", state: { files: [{ kind: "spr", applied: "mod:leftover", official: false }] }, modList: [] },
] });
async function modsEnabled(enabled) {
  handlers.set("mods.state", () => ({ result: { enabled } }));
  await Mods.refresh();
}

test("detail variant candidates preview official assets independently of the source's live mod", () => {
  const fam = previewFamily(), source = fam.variants[1];
  assert.equal(Detail.baseOf({ kind: "variant", core: source.spr, variant: source }), "/asset/spine/official-spr/b_spr/skeleton");
  assert.equal(Detail.baseOf({ kind: "self", core: fam.variants[0].spr }), "/asset/spine/official-spr/a_spr/skeleton");
});

test("Update reloads a configured portrait or lobby rebuilt under the same mod id while mods are off", async () => {
  await modsEnabled(false);
  const inputs = { skeletons: {}, atlases: {}, textures: {} };
  handlers.set("actions.compatibility", () => ({ result: "compatibility" }));
  for (const [kind, id] of [["spr", "p"], ["lobby", "l"]]) {
    const fam = previewFamily(), draws = [];
    handlers.set("actions.compatibilityStamp", ({ mod }) => ({ result: `stamp-${mod}` }));
    Detail.setupPreviewTest(fam, kind, async () => draws.push(Detail.previewTestState()));
    handlers.set("actions.update", (params) => { assert.deepEqual(params, { mod: id }); return { result: "rebuild" }; });
    handlers.set("jobs.get", ({ id }) => ({ result: { state: "done", result: id === "compatibility" ? { official: inputs, mod: inputs } : {} } }));
    handlers.set("family.get", () => ({ result: structuredClone(fam) }));
    Thumbs.forgotten.length = 0;
    const updating = Detail.runUpdate();
    (await drawn(dialog, "ok")).click();
    assert.equal(await updating, true);
    await new Promise(setImmediate);
    for (const layer of [...dialog.children].reverse()) (await drawn(layer, "close")).click();
    assert.deepEqual(Thumbs.forgotten, [`/asset/spine/mod/${id}`]);
    assert.equal(draws.length, 1, `${kind} reloads even when family.get has no changed source key or game file`);
    if (kind === "spr") {
      assert.equal(draws[0].mainPlayer, null);
      assert.equal(draws[0].expression, "00");
      assert.equal(draws[0].lobbyShot, "cached", "the other preview stays cached");
    } else {
      assert.equal(draws[0].lobbyStale, true);
      assert.equal(draws[0].lobbyShot, null);
      assert.equal(draws[0].lobbySeq, null);
      assert.equal(draws[0].lobbyAll, false);
      assert.equal(draws[0].shownKey, null);
      assert.deepEqual(draws[0].mainPlayer, { cached: true }, "the portrait stays cached");
    }
    Detail.setupPreviewTest(fam, kind, async () => assert.fail("a failed update must keep the player"));
    handlers.set("actions.compatibilityStamp", () => ({ result: "failed-inputs" }));
    handlers.set("jobs.get", () => ({ result: { state: "failed", error: { code: "internal" } } }));
    Thumbs.forgotten.length = 0;
    const failed = Detail.runUpdate();
    assert.equal(await failed, false);
    await new Promise(setImmediate);
    for (const layer of [...dialog.children].reverse()) (await drawn(layer, "close")).click();
    assert.deepEqual(Thumbs.forgotten, []);
    assert.deepEqual(Detail.previewTestState().mainPlayer, { cached: true });
    assert.equal(Detail.previewTestState().lobbyShot, "cached");
  }
});

test("a compatibility check never updates an existing mod even when its bundle date differs", async () => {
  setStrings("en-US", Object.fromEntries(strings(english)));
  await modsEnabled(false);
  const fam = previewFamily();
  Detail.setupPreviewTest(fam, "spr");
  const inputs = { skeletons: {}, atlases: {}, textures: {} };
  handlers.set("actions.compatibility", () => ({ result: "compatibility" }));
  handlers.set("actions.update", () => assert.fail("compatibility cannot overwrite the mod"));
  handlers.set("jobs.get", () => ({ result: { state: "done", result: { official: inputs, mod: inputs, outdated: true } } }));
  const done = Detail.runCompatibility();
  (await drawn(dialog, "ok")).click();
  await done;
});

test("each mod card exposes a read-only compatibility check for its own mod", async () => {
  setStrings("en-US", Object.fromEntries(strings(english)));
  await modsEnabled(false);
  for (const [kind, id] of [["spr", "p"], ["lobby", "l"]]) {
    const fam = previewFamily();
    Detail.setupPreviewTest(fam, kind);
    let checks = 0;
    handlers.set("actions.compatibility", (params) => { assert.deepEqual(params, { mod: id }); checks++; return { result: "compatibility" }; });
    handlers.set("actions.update", () => assert.fail("a match requires no update"));
    const inputs = { skeletons: {}, atlases: {}, textures: {} };
    handlers.set("jobs.get", () => ({ result: { state: "done", result: { official: inputs, mod: inputs } } }));
    await Detail.renderPopup();
    const check = walk(switchHost).find((el) => el.getAttribute("aria-label") === english.compatibility.check);
    assert.ok(check);
    const checking = check.click();
    (await drawn(dialog, "ok")).click();
    await checking;
    await until(() => checks === 1 && dialog.hidden);
    switchHost.hidden = true;
  }
});

test("switch cards and details label manifest choices Applied with either switch state", async () => {
  for (const lang of ["en-US", "zh-CN", "zh-TW", "ja-JP", "ko-KR", "th-TH"]) {
    const ui = JSON.parse(readFileSync(new URL(`../../src/bmpt/locales/${lang}.json`, import.meta.url))).webui;
    setStrings(lang, Object.fromEntries(["type", "popup", "switch", "status"].flatMap((group) =>
      Object.entries(ui[group]).map(([key, value]) => [`webui.${group}.${key}`, value]))));
    for (const [kind, source] of [["spr", "mod:p"], ["lobby", "mod:l"], ["spr", "variant:b"]]) {
      for (const enabled of [false, true]) {
        await modsEnabled(enabled);
        const fam = previewFamily();
        fam.variants[0].configured[kind] = source;
        fam.variants[0].selection = { [kind]: { source, installed: enabled } };
        if (enabled) Object.assign(fam.variants[0].state.files.find((f) => f.kind === kind), { applied: source, official: false });
        Detail.setupPreviewTest(fam, kind);
        await Detail.renderPopup();
        const captions = walk(switchHost).map((el) => el.textContent);
        const caption = ui.status.applied;
        assert.equal(captions.filter((c) => c === caption).length, 3,
          `${lang} ${kind} ${source}: badge, chip and configured status`);
        const key = source.startsWith("mod:") ? `m:${source.slice(4)}` : `v:${source.slice(8)}`;
        assert.ok(walk(switchHost).some(el => el.dataset.path === `Card_${key}/Select/AppliedBadge`));
        assert.equal(walk(switchHost).find(el => el.dataset.path === `Card_${key}/Select/AppliedLabel`).textContent, caption);
        assert.ok(!walk(switchHost).some(el => el.dataset.path === `Card_${key}/Using`));
        if (source.startsWith("mod:")) assert.ok(captions.includes(ui.type.added));
        assert.ok(!captions.includes(ui.switch.configured) && !captions.includes(ui.status.installed));
        assert.ok(!captions.includes(ui.popup.inUse));
        switchHost.hidden = true;
      }
    }
  }
});

test("a saved source's detail chip keeps its caption independently of In Use", async () => {
  const ui = JSON.parse(readFileSync(new URL("../../src/bmpt/locales/en-US.json", import.meta.url))).webui;
  await Popup.listPopup({ host: prompt, title: "Portrait", items: [{ key: "m", kind: "mod", label: "Saved mod", full: "Saved mod",
    inUse: false, inUseText: ui.switch.configured, badge: { text: ui.switch.configured, color: "#8de4ff" } }],
    selected: "m", details: () => ({ title: "Portrait", fields: [], actions: [] }) });
  assert.equal(walk(prompt).filter((el) => el.textContent === ui.switch.configured).length, 2, "card badge and detail chip");
  prompt.hidden = true;
});

test("switch popup fields fit a long file independently in portrait and lobby details", async () => {
  const field = (prefix, i) => walk(prompt).find((el) => el.dataset.path === `Details/${prefix}_${i}`);
  try {
    for (const lang of ["en-US", "zh-CN", "zh-TW", "ja-JP", "ko-KR", "th-TH"]) {
      const ui = JSON.parse(readFileSync(new URL(`../../src/bmpt/locales/${lang}.json`, import.meta.url))).webui;
      setStrings(lang, Object.fromEntries(strings(ui)));
      for (const kind of ["portrait", "lobby"]) {
        const snapshots = [];
        for (const file of ["ibuki_spr.bundle", `${"azusa_swimsuit_spr".repeat(4)}.bundle`]) {
          const fields = [[ui.field.file, file], [ui.field.size, "20 MB"], [ui.field.status, ui.status.replaced], [ui.field.crc, ui.crc.fixed]];
          await Popup.listPopup({ host: prompt, title: ui.info.title[kind], items: [{ key: "m", kind: "mod", label: "Mod", full: "Mod" }],
            selected: "m", details: () => ({ title: ui.info.title[kind], fields, actions: [] }) });
          const cells = fields.map((_, i) => field("V", i));
          assert.equal(cells[0].getAttribute("aria-label"), file);
          assert.equal(cells[0].title, undefined);
          assert.equal(cells[0].style.pointerEvents, "none");
          if (file.length > 50) assert.ok(cells[0].textContent.includes("\n"), "a long file wraps inside its own row");
          snapshots.push({ values: cells.slice(1).map((el) => [el.textContent, el.style.fontSize]),
            labels: fields.map((_, i) => field("F", i).style.fontSize) });
        }
        assert.deepEqual(snapshots[1], snapshots[0], `${lang} ${kind}: the file does not resize the other values or labels`);
      }
    }
  } finally { prompt.hidden = true; setStrings("en-US", Object.fromEntries(strings(english))); }
});

test("long mod aliases and original file names share the switch detail name band in every locale", async () => {
  const NGUI = await import("../../src/bmpt/web/js/ngui.js");
  try {
    for (const lang of ["en-US", "zh-CN", "zh-TW", "ja-JP", "ko-KR", "th-TH"]) {
      const ui = JSON.parse(readFileSync(new URL(`../../src/bmpt/locales/${lang}.json`, import.meta.url))).webui;
      setStrings(lang, Object.fromEntries(strings(ui)));
      const alias = "Painted <summer> & colors ".repeat(8), original = "ibuki_dress_spr".repeat(16) + ".bundle";
      await Popup.listPopup({ host: prompt, title: ui.info.title.portrait,
        items: [{ key: "m", kind: "mod", label: alias, full: alias, original, nameEdit: { label: ui.modAlias.title, fn() {} } }],
        selected: "m", details: () => ({ title: ui.info.title.portrait, fields: [], actions: [] }) });
      const part = (name) => walk(prompt).find((el) => el.dataset.path === `Details/${name}`);
      const main = part("Name"), secondary = part("OriginalName");
      assert.equal(main.getAttribute("aria-label"), alias);
      assert.equal(secondary.getAttribute("aria-label"), original);
      assert.ok(parseFloat(main.style.fontSize) > parseFloat(secondary.style.fontSize));
      for (const el of [main, secondary]) assert.equal(el.children[0].style.textOverflow, "ellipsis");
      const a = stageRect(main, NGUI), b = stageRect(secondary, NGUI), chip = stageRect(part("Chip_0"), NGUI);
      assert.ok(a[1] + a[3] <= b[1], `${lang}: name lines do not overlap`);
      assert.ok(b[1] + b[3] <= chip[1], `${lang}: original name stays above chips`);
    }
  } finally { prompt.hidden = true; setStrings("en-US", Object.fromEntries(strings(english))); }
});

test("file details keep a smaller original name below the mod alias, including file-name fallback", () => {
  for (const mod of [{ alias: "My paint", core: "ibuki_spr" }, { alias: "My paint", file: "C:\\mods\\ibuki_spr.bundle" }]) {
    const { tree } = Detail.detailsBody({ fields: [[english.info.current, mod.alias, mod]], rows: [] });
    const value = tree.children.find((n) => n.name === "V_0"), original = tree.children.find((n) => n.name === "Original_0");
    assert.equal(value.widgets[0].ariaLabel, mod.alias);
    assert.equal(original.widgets[0].ariaLabel, mod.core || "ibuki_spr.bundle");
    assert.ok(value.widgets[0].fontSize > original.widgets[0].fontSize);
    assert.ok(value.pos[1] - value.widgets[0].h_ / 2 > original.pos[1] + original.widgets[0].h_ / 2);
    assert.equal(original.widgets[0].ellipsis, true);
  }
});

test("a long folder in the file-details summary does not resize the current value or labels", () => {
  const fields = [[english.field.folder, "/game"], [english.info.current, "ibuki_spr"]];
  const before = Detail.detailsBody({ fields, rows: [] }).tree;
  fields[0][1] = `/game/${"folder/".repeat(60)}`;
  const after = Detail.detailsBody({ fields, rows: [] }).tree;
  const widget = (tree, name) => tree.children.find((n) => n.name === name).widgets[0];
  for (const name of ["F_0", "F_1", "V_1"]) assert.deepEqual(widget(after, name), widget(before, name));
  assert.equal(widget(after, "V_0").ariaLabel, fields[0][1]);
  assert.equal(widget(after, "V_0").title, undefined);
  assert.ok(widget(after, "V_0").fontSize >= 26, "a wrapped path respects its size floor");
  assert.ok(widget(after, "V_0").text.includes("\n"));
});

test("repair review displays both pages and warnings; Accept alone stores the repaired mod", async () => {
  setStrings("en-US", Object.fromEntries(strings(english)));
  const Compatibility = await import("../../src/bmpt/web/js/mod-compatibility.js");
  globalThis.getComputedStyle = () => ({ opacity: "1" });
  globalThis.requestAnimationFrame = (fn) => setTimeout(fn, 0);
  const built = { preview: "review", pages: [{ name: "page", original: "data:image/png;base64,YQ==", repaired: "data:image/png;base64,Yg==" }],
    report: { moved: ["face"], added: ["new"], dropped: ["old"], warnings: [{ key: "rig", name: "test.skel" }], partial: true },
    compare: { assets: [{ type: "Texture2D", name: "page", status: "changed" }] } };
  let saved = 0, discarded = 0, refreshed = 0;
  handlers.set("actions.remap", () => ({ result: "remap-job" }));
  handlers.set("actions.acceptRemap", () => { saved++; return { result: "accept-job" }; });
  handlers.set("actions.discardRemap", () => { discarded++; return { result: null }; });
  handlers.set("jobs.get", ({ id }) => ({ result: { state: "done", result: id === "remap-job" ? built : { mod: { id: "fixed" } } } }));
  for (const accept of [false, true]) {
    const done = Compatibility.remap("original", () => { refreshed++; }, "Painted");
    const ok = await drawn(dialog, "ok");
    assert.equal(saved, 0);
    const images = walk(dialog).filter((el) => el.tagName === "IMG");
    assert.deepEqual(images.map((el) => el.src), [built.pages[0].original, built.pages[0].repaired]);
    assert.ok(walk(dialog).some((el) => el.textContent === english.remap.partial));
    assert.ok(walk(dialog).some((el) => el.textContent.includes("test.skel")));
    assert.equal(ok.getAttribute("aria-label"), english.remap.accept);
    assert.equal(action(dialog, "cancel").getAttribute("aria-label"), english.remap.discard);
    (accept ? ok : action(dialog, "cancel")).click();
    await done;
    assert.equal(saved, Number(accept));
    assert.equal(refreshed, Number(accept));
  }
  assert.equal(discarded, 2);
});

test("repaired names carry their localized tag and originals jump to their repaired card", async () => {
  setStrings("en-US", Object.fromEntries(strings(english)));
  await modsEnabled(false);
  const fam = previewFamily(), mods = fam.variants[0].modList;
  mods[0].repaired_id = "fixed";
  mods.push({ ...mods[0], id: "fixed", repaired_from: mods[0].id, repaired_id: "" });
  Detail.setupPreviewTest(fam, "spr");
  await Detail.renderPopup();
  assert.ok(walk(switchHost).some((el) => el.textContent.includes(english.remap.repaired)));
  const jump = walk(switchHost).find((el) => el.getAttribute("aria-label") === english.remap.go);
  assert.ok(jump);
  assert.ok(!walk(switchHost).some((el) => el.getAttribute("aria-label") === english.remap.action));
  await jump.click();
  assert.equal(Detail.previewTestState().pending, "m:fixed");
  assert.ok(walk(switchHost).some((el) => el.textContent.includes(english.remap.repaired)));
  switchHost.hidden = true;
});

test("Go To from the update summary opens the switch on its mod once the student page of its kind is in", async () => {
  setStrings("en-US", Object.fromEntries(strings(english)));
  await modsEnabled(false);
  const fam = previewFamily();
  fam.variants[0].modList.push({ id: "q", target: "a", kind: "spr", label: "Second portrait mod" });
  // another variant, another kind's tab, a request the page did not reach soon: the switch stays closed
  for (const [request, at] of [[{ id: "p", variant: "b", kind: "spr" }, 0], [{ id: "l", variant: "a", kind: "lobby" }, 0],
    [{ id: "p", variant: "a", kind: "spr" }, 31000]]) {
    Detail.setupPreviewTest(fam, "spr");
    switchHost.hidden = true;
    const now = Date.now;
    Detail.want(request);
    Date.now = () => now() + at;
    try { await Detail.takeWanted(); } finally { Date.now = now; }
    assert.equal(switchHost.hidden, true, JSON.stringify(request));
  }
  Detail.setupPreviewTest(fam, "spr");
  switchHost.hidden = true;
  Detail.want({ id: "q", variant: "a", kind: "spr" });
  await Detail.takeWanted();
  assert.equal(switchHost.hidden, false);
  assert.equal(Detail.previewTestState().pending, "m:q", "the mod's card selected, its details with Update");
  assert.ok(walk(switchHost).some((el) => el.getAttribute("aria-label") === english.act.update));
  // taken once
  Detail.setupPreviewTest(fam, "spr");
  await Detail.takeWanted();
  assert.notEqual(Detail.previewTestState().pending, "m:q");
  switchHost.hidden = true;
});

test("repair comparison uses separate in-memory players and disposes both on close", async () => {
  const Compare = await import("../../src/bmpt/web/js/mod-compare.js");
  const previous = globalThis.spine, created = [];
  globalThis.spine = {
    Downloader: class { rawDataUris = {}; },
    TextureAtlas: class { pages = [{ name: "page.png" }]; },
    SpinePlayer: class {
      constructor(host, config) {
        this.host = host; this.config = config; this.disposed = false;
        this.skeleton = { data: { animations: [{ name: "Idle_01" }] } };
        this.animationState = { setAnimation() {} };
        created.push(this);
        queueMicrotask(() => config.success(this));
      }
      dispose() { this.disposed = true; }
    },
  };
  const original = { name: "test.skel", json: true, skeleton: Buffer.from('\ufeff{"bones":[{"name":"root"}]}').toString("base64"), atlas: "original page data" };
  const repaired = { ...original, atlas: "repaired page data" };
  try {
    const pending = Compare.show({ pages: [{ name: "page", original: "original pixels", repaired: "repaired pixels" }],
      drawings: { original: [original], repaired: [repaired] } }, "Painted", "dialog",
    { lines: [], accept: english.remap.accept, discard: english.remap.discard });
    await drawn(dialog, "cancel");
    assert.equal(created.length, 2);
    assert.notEqual(created[0].host.parentNode, created[1].host.parentNode);
    for (const [i, player] of created.entries()) {
      let text, json;
      player.config.downloader.downloadText("skeleton.atlas", (value) => { text = value; });
      player.config.downloader.downloadJson("skeleton.json", (value) => { json = value; }, assert.fail);
      assert.equal(text, i ? repaired.atlas : original.atlas);
      assert.equal(json.bones[0].name, "root");
      assert.equal(player.config.rawDataURIs["page.png"], i ? "repaired pixels" : "original pixels");
    }
    press("Escape");
    assert.equal(await pending, false);
    assert.ok(created.every((player) => player.disposed));
  } finally { globalThis.spine = previous; }
});

test("the single Update action checks fresh inputs and routes a mixed page mismatch to review", async () => {
  setStrings("en-US", Object.fromEntries(strings(english)));
  await modsEnabled(false);
  const previous = globalThis.spine;
  globalThis.spine = { TextureAtlas: class {
    pages = [{ name: "page.png", width: 64, height: 64 }];
    regions = [];
  } };
  try {
    const fam = previewFamily();
    Detail.setupPreviewTest(fam, "spr");
    await Detail.renderPopup();
    assert.equal(walk(switchHost).filter((el) => el.getAttribute("aria-label") === english.act.update).length, 1);
    const inputs = { skeletons: {}, atlases: { "test.atlas": "page.png\nsize: 64,64\n" }, textures: { page: [64, 32] } };
    const built = { preview: "unified-review", pages: [], compare: { assets: [] },
      report: { moved: ["part"], added: [], dropped: [], warnings: [], partial: false } };
    const methods = [];
    handlers.set("actions.compatibilityStamp", () => ({ result: "mixed-review" }));
    handlers.set("actions.compatibility", () => { methods.push("check"); return { result: "unified-check" }; });
    handlers.set("actions.update", () => assert.fail("remap must not run the classic port"));
    handlers.set("actions.remap", () => { methods.push("remap"); return { result: "unified-remap" }; });
    handlers.set("actions.acceptRemap", () => assert.fail("Discard must not store a result"));
    handlers.set("actions.discardRemap", () => { methods.push("discard"); return { result: null }; });
    handlers.set("jobs.get", ({ id }) => ({ result: { state: "done", result: id === "unified-check"
      ? { official: inputs, mod: inputs, remappable: true } : built } }));
    const updating = Detail.runUpdate();
    await drawn(dialog, "ok");
    action(dialog, "cancel").click();
    await updating;
    assert.deepEqual(methods, ["check", "remap", "discard"]);
    switchHost.hidden = true;
  } finally { globalThis.spine = previous; }
});

test("errors use an answerable game popup, plain notices stay silent and the toast UI is absent", async () => {
  setStrings("en-US", Object.fromEntries(strings(english)));
  Popup.notify("Plain success"); assert.equal(dialog.hidden, true);
  for (const kind of ["error"]) {
    assert.equal(Popup.notify(`Review ${kind}`, { kind }), undefined, "notification delivery does not change caller return values");
    const layer = dialog.children.at(-1);
    const ok = await drawn(layer, "ok");
    assert.ok(walk(layer).some((el) => el.textContent === `Review ${kind}`));
    assert.ok(walk(layer).some((el) => el.dataset.path === "Center/Popup/Base/BG"), "uses the existing game popup frame");
    ok.click(); assert.equal(dialog.hidden, true);
  }
  const root = new URL("../../src/bmpt/web/", import.meta.url);
  assert.doesNotMatch(readFileSync(new URL("index.html", root), "utf8"), /id="toast"|class="toast"/);
  assert.doesNotMatch(readFileSync(new URL("css/app.css", root), "utf8"), /\.toast\b/);
  assert.doesNotMatch(readFileSync(new URL("js/util.js", root), "utf8"), /export function toast\b/);
});


test("clean Update asks Cancel / Update and reuses the checked report; problems update without that question", async () => {
  setStrings("en-US", Object.fromEntries(strings(english))); await modsEnabled(false);
  const inputs = { skeletons: {}, atlases: {}, textures: {} }, fam = previewFamily();
  let stamp = "force-current", checks = 0, updates = 0, problems = false;
  handlers.set("actions.compatibilityStamp", () => ({ result: stamp }));
  handlers.set("actions.compatibility", () => { checks++; return { result: "force-check" }; });
  handlers.set("actions.update", () => { updates++; return { result: "force-update" }; });
  handlers.set("family.get", () => ({ result: structuredClone(fam) }));
  handlers.set("jobs.get", ({ id }) => ({ result: { state: "done", result: id === "force-check"
    ? { official: problems ? { ...inputs, skeletons: { "required.skel": {} } } : inputs, mod: inputs } : {} } }));
  Detail.setupPreviewTest(fam, "spr");
  let work = Detail.runCompatibility(); (await drawn(dialog, "ok")).click(); await work;
  assert.equal(checks, 1); assert.equal(updates, 0);
  work = Detail.runUpdate(); await drawn(dialog, "cancel");
  assert.ok(walk(dialog).some((el) => el.textContent.replaceAll("\n", " ") === english.compatibility.forceUpdate));
  assert.equal(action(dialog, "ok").getAttribute("aria-label"), english.act.update);
  action(dialog, "cancel").click(); assert.equal(await work, false); assert.equal(updates, 0); assert.equal(checks, 1);
  work = Detail.runUpdate(); (await drawn(dialog, "ok")).click(); assert.equal(await work, true);
  assert.equal(updates, 1); assert.equal(checks, 1);
  assert.equal(dialog.hidden, true, "successful updates finish without a confirmation popup");
  problems = true; stamp = "new-game-inputs";
  work = Detail.runUpdate(); assert.equal(await work, true);
  assert.equal(checks, 2); assert.equal(updates, 2);
  assert.ok(!walk(dialog).some((el) => el.textContent.replaceAll("\n", " ") === english.compatibility.forceUpdate));
  const exact = JSON.parse(readFileSync(new URL("../../src/bmpt/locales/zh-CN.json", import.meta.url))).webui.compatibility.forceUpdate;
  assert.equal(exact, "\u76ee\u524d\u5b8c\u6574\u6027\u65e0\u95ee\u9898\uff0c\u662f\u5426\u786e\u5b9a\u4ecd\u8981\u5f3a\u5236\u66f4\u65b0\uff1f");
});

for (const locale of ["en-US", "zh-CN", "zh-TW", "ja-JP", "ko-KR", "th-TH"]) test(`unavailable compatibility asks before updating in ${locale}`, async () => {
  const ui = JSON.parse(readFileSync(new URL(`../../src/bmpt/locales/${locale}.json`, import.meta.url))).webui;
  setStrings(locale, Object.fromEntries(strings(ui))); await modsEnabled(false);
  const inputs = { skeletons: {}, atlases: {}, textures: {} }, fam = previewFamily();
  let updates = 0;
  handlers.set("actions.compatibilityStamp", () => ({ result: `unchecked-${locale}` }));
  handlers.set("actions.compatibility", () => ({ result: "unchecked-check" }));
  handlers.set("actions.update", () => { updates++; return { result: "unchecked-update" }; });
  handlers.set("family.get", () => ({ result: structuredClone(fam) }));
  handlers.set("jobs.get", ({ id }) => ({ result: { state: "done", result: id === "unchecked-check"
    ? { official: inputs, mod: inputs, unavailable: true } : {} } }));
  Detail.setupPreviewTest(fam, "spr");
  try {
    let work = Detail.runUpdate(); await drawn(dialog, "cancel");
    assert.ok(walk(dialog).some((el) => el.textContent.replaceAll("\n", " ") === ui.compatibility.updateUnchecked));
    assert.ok(walk(dialog).some((el) => el.dataset.path === "Center/Popup/Base/BG"));
    assert.equal(action(dialog, "ok").getAttribute("aria-label"), ui.act.update);
    assert.equal(action(dialog, "cancel").getAttribute("aria-label"), ui.popup.cancel);
    action(dialog, "cancel").click(); assert.equal(await work, false); assert.equal(updates, 0);
    work = Detail.runUpdate(); (await drawn(dialog, "ok")).click(); assert.equal(await work, true);
    assert.equal(updates, 1);
  } finally { setStrings("en-US", Object.fromEntries(strings(english))); }
});

for (const locale of ["en-US", "zh-CN", "zh-TW", "ja-JP", "ko-KR", "th-TH"]) test(`copy-name failures use an error popup while success stays silent in ${locale}`, async () => {
  const ui = JSON.parse(readFileSync(new URL(`../../src/bmpt/locales/${locale}.json`, import.meta.url))).webui;
  const previous = navigator.clipboard, copied = [];
  setStrings(locale, Object.fromEntries(strings(ui)));
  Detail.setupPreviewTest(previewFamily(), "spr");
  try {
    navigator.clipboard = { writeText: async (name) => { copied.push(name); } };
    await Detail.copyName();
    assert.equal(copied.length, 1); assert.ok(copied[0]);
    assert.equal(dialog.hidden, true, "successful copying stays silent");
    for (const clipboard of [undefined, { writeText: async () => { throw new Error("Clipboard rejected"); } }]) {
      navigator.clipboard = clipboard;
      await Detail.copyName();
      const layer = dialog.children.at(-1), ok = await drawn(layer, "ok");
      assert.ok(walk(layer).some((el) => el.textContent.replaceAll("\n", " ") === ui.detail.copyFailed));
      assert.ok(walk(layer).some((el) => el.dataset.path === "Center/Popup/Base/BG"));
      ok.click(); assert.equal(dialog.hidden, true);
    }
  } finally { navigator.clipboard = previous; setStrings("en-US", Object.fromEntries(strings(english))); }
});

test("failed-job notifications offer Error Report on the game frame", async () => {
  setStrings("en-US", Object.fromEntries(strings(english)));
  let reports = 0;
  globalThis.BAMTRecovery = { report: () => { reports++; } };
  try {
    Popup.notify("Failed job", { kind: "error" });
    const layer = dialog.children.at(-1);
    const report = await drawn(layer, "cancel");
    assert.ok(walk(layer).some((el) => el.textContent.replace(/\s+/g, " ") === "Error Report"));
    report.click();
    assert.equal(reports, 1);
    assert.equal(dialog.hidden, true);
    const refused = Popup.message({ title: "Refusal", lines: ["Busy"], error: true, ok: "OK", cancel: "Existing Action" });
    const next = dialog.children.at(-1);
    await drawn(next, "line1");
    action(next, "line1").click();
    assert.equal(reports, 2);
    assert.ok(action(next, "cancel"), "the original refusal action remains available");
    action(next, "ok").click();
    await refused;
  } finally { delete globalThis.BAMTRecovery; }
});


test("error popup report actions retain the triggering code and parameters", async () => {
  const events = [], error = { code: "library_io_error", params: { file: "Private Mod Name.bundle", operation: "read" } };
  globalThis.BAMTRecovery = { report: (event) => events.push(event) };
  try {
    Popup.notify("Cannot read", { kind: "error", error });
    const layer = dialog.children.at(-1);
    (await drawn(layer, "cancel")).click();
    assert.equal(events[0].code, error.code);
    assert.equal(events[0].ref, "E4007");
    assert.deepEqual(events[0].params, error.params);
    const pending = Popup.message({ title: "Failed", error, ok: "OK", cancel: "Retry" });
    const next = dialog.children.at(-1);
    (await drawn(next, "line0")).click();
    assert.equal(events[1].code, error.code);
    assert.equal(events[1].ref, "E4007");
    action(next, "ok").click();
    await pending;
  } finally { delete globalThis.BAMTRecovery; }
});

// Exercise the actual rendered popup at all supported text lengths and kinds, without localhost.
test("switch geometry: per-kind columns, aspects, card bounds, labels and portrait split", async () => {
  const NGUI = await import("../../src/bmpt/web/js/ngui.js");
  const box = (path) => stageRect(walk(prompt).find((el) => el.dataset.path === path), NGUI);
  const inside = (a, b, message) => {
    assert.ok(a[0] >= b[0] - 1 && a[1] >= b[1] - 1 && a[0] + a[2] <= b[0] + b[2] + 1 && a[1] + a[3] <= b[1] + b[3] + 1, message);
  };
  try {
    for (const lang of ["en-US", "zh-CN", "zh-TW", "ja-JP", "ko-KR", "th-TH"]) {
      const ui = JSON.parse(readFileSync(new URL(`../../src/bmpt/locales/${lang}.json`, import.meta.url))).webui;
      setStrings(lang, Object.fromEntries(strings(ui)));
      for (const [kind, columns] of [["spr", 4], ["lobby", 2], ["model", 4]]) {
        const items = Array.from({ length: 9 }, (_, i) => ({ key: String(i), kind: "mod", full: "ch0200_spr", label: "ch0200_spr",
          nameEdit: { label: ui.modAlias.title, fn() {} }, inUseText: ui.switch.configured,
          badge: { text: ui.switch.configured, color: "#8de4ff" } }));
        const fields = [[ui.field.file, `assets-_mx-spinecharacters-${"long_bundle_name_".repeat(5)}.bundle`],
          [ui.field.size, "2.5 MB"], [ui.field.status, `${ui.switch.configured}\n${ui.error.game_file_locked.replace("{file}", "/very/long/path".repeat(20))}`], [ui.field.crc, "—"]];
        await Popup.listPopup({ host: prompt, title: kind, layoutKind: kind, items, selected: "0", okText: ui.popup.apply,
          details: () => ({ title: ui.info.title.portrait, fields, actions: [[ui.open.mac], [ui.btn.extract]] }) });
        const cards = items.map((it) => box(`Card_${it.key}`));
        const ratio = cards[0][2] / cards[0][3];
        if (kind === "spr") {
          const still = box("Details/Picture/Texture");
          assert.ok(Math.abs(1 / ratio - 2.2) < .01, "portrait height follows the latest 2.2:1 sketch");
        }
        if (kind === "lobby") assert.ok(Math.abs(ratio - 16 / 9) < .02);
        if (kind === "model") { assert.equal(ratio, 1); assert.ok(cards[0][2] > 220); }
        const rowSize = kind === "spr" ? items.length : columns;
        for (let i = 0; i < cards.length; i++) {
          if (i % rowSize) assert.ok(cards[i][0] > cards[i - 1][0] + cards[i - 1][2] + 8, `${kind}: column gap`);
          if (i >= rowSize) assert.ok(cards[i][1] > cards[i - rowSize][1] + cards[i - rowSize][3] + 65, `${kind}: row gap includes names`);
          assert.equal(cards[i][1], cards[Math.floor(i / rowSize) * rowSize][1]);
          const label = walk(prompt).find((el) => el.dataset.path?.startsWith(`Card_${i}/Bar/Name/`) && el.textContent);
          const band = box(`Card_${i}/Bar`);
          inside(stageRect(label, NGUI), band, `${lang}: name in card bar`);
          inside(box(`Card_${i}/Bar/EditName`), band, `${lang}: edit pencil in card bar`);
          assert.ok(band[1] > cards[i][1] + cards[i][3], "the name band is below the artwork");
          const el = (suffix = "") => walk(prompt).find((e) => e.dataset.path === `Card_${i}${suffix}`);
          assert.ok(el().style.backgroundImage, "shared card body sprite renders");
          for (const suffix of ["", "/Thumb", "/Line", "/Bar", "/Using"]) {
            assert.equal(el(suffix).style.transform, "skewX(-10deg)", `${kind}: shared incline for ${suffix}`);
          }
          assert.equal(el("/Thumb").style.borderRadius, `${(18 * S).toFixed(1)}px`);
          const image = el("/Thumb").children[0], window = box(`Card_${i}/Thumb`);
          assert.equal(image.style.transform, "skewX(10deg)", "art stays upright inside the shared incline");
          const margin = -parseFloat(image.style.left) / 100 * window[2];
          assert.ok(margin + .01 >= window[3] * Math.tan(Math.PI / 18) / 2, "image covers the tall slanted corners");
          assert.ok(!walk(prompt).some((e) => e.dataset.path?.startsWith(`Card_${i}/Sort`)), "no collection Sort overlay across the art");
          if (i === 0) {
            assert.ok(el("/Select").style.backgroundImage, "shared selection outline renders");
            assert.equal(el("/Select").style.transform, "skewX(-10deg)");
            assert.ok(el("/Select/Check").style.backgroundImage, "shared check sprite renders");
          }
        }
        if (kind === "spr") {
          const picture = box("Details/Picture/Texture"), name = box("Details/Name"), buttons = box("Details/Act_0/Body");
          assert.ok(picture[3] > picture[2] * 2);
          assert.ok(picture[0] + picture[2] < name[0]);
          assert.ok(picture[1] + picture[3] < buttons[1]);
          let previous = box("Details/Chip_0");
          for (let i = 0; i < 4; i++) {
            const caption = box(`Details/F_${i}`), value = box(`Details/V_${i}`);
            assert.ok(caption[0] > picture[0] + picture[2]);
            assert.ok(caption[1] >= previous[1] + previous[3]);
            assert.ok(value[1] >= caption[1] + caption[3]);
            assert.ok(value[0] + value[2] <= 960 + Popup.listLayout().left.r * S);
            assert.ok(value[1] + value[3] < buttons[1], `${lang}: fields above actions`);
            previous = value;
          }
          assert.ok(walk(prompt).find((el) => el.dataset.path === "Details/V_0").textContent.includes("\n"));
        }
        prompt.hidden = true;
      }
    }
  } finally { prompt.hidden = true; setStrings("en-US", Object.fromEntries(strings(english))); }
});


test("portrait spacing fits four tall cards at the standard width and selects horizontal scrolling", async () => {
  const { switchGrid } = await import("../../src/bmpt/web/js/switch-layout.js");
  for (const width of [1300, 1818, 2200]) {
    const { card, columns, pitch, overhang } = switchGrid("spr", width);
    assert.equal(card.h / card.w, 2.2);
    assert.equal(pitch[0] - card.w, 40, "portrait gaps do not stretch to fill the viewport (SPACE.xl: no prefab gives the cards a gap)");
    assert.equal(pitch[1] - card.h - card.bar, 40);
    assert.ok(columns <= 4);
    assert.ok((columns - 1) * pitch[0] + card.w + overhang <= width - 80 + .001);
  }
  assert.equal(switchGrid("spr").columns, 4);
  assert.equal(switchGrid("spr").horizontal, true);
  for (const [kind, columns] of [["lobby", 2], ["model", 4]]) {
    assert.equal(switchGrid(kind).horizontal, false);
    assert.equal(switchGrid(kind).columns, columns);
  }
});

// Measure rendered CSS rectangles independently of switch-layout's calculations. Include each widget's shear.
test("every kind left-aligns outer corners, with compact portrait gaps and balanced vertical grids", async () => {
  const { rect } = await import("../../src/bmpt/web/js/ngui.js");
  const { switchGrid } = await import("../../src/bmpt/web/js/switch-layout.js");
  const outer = (el) => {
    const [x, y, w, h] = rect(el), angle = Number(/skewX\(([-\d.]+)deg\)/.exec(el.style.transform)?.[1] || 0);
    const overhang = Math.abs(Math.tan(angle * Math.PI / 180) * h / 2);
    return { left: x - overhang, right: x + w + overhang, top: y, bottom: y + h };
  };
  const headerInsets = [], leftInsets = [];
  for (const kind of ["spr", "lobby", "model"]) {
    const { columns } = switchGrid(kind);
    for (const count of new Set([0, 1, 2, columns, columns + 1, 2 * columns, 19])) for (const selected of new Set(["0", String(count - 1), null])) {
      const items = Array.from({ length: count }, (_, i) => ({ key: String(i), full: "Owner label", label: "Owner label", isNew: true }));
      await Popup.listPopup({ host: prompt, layoutKind: kind, items, selected, title: kind, okText: "Set" });
      const paper = outer(walk(prompt).find((e) => e.dataset.path === "Center/Popup/ListPaper"));
      const area = walk(prompt).find((e) => e.className === "popup-cards");
      const dx = parseFloat(area.style.left), dy = parseFloat(area.style.top);
      const rowSize = kind === "spr" ? Math.max(1, count) : columns;
      for (let start = 0; start < count; start += rowSize) {
        const names = items.slice(start, start + rowSize).map((it) => `Card_${it.key}`);
        const ink = walk(prompt).filter((e) => e.style.backgroundImage && names.some((n) => e.dataset.path === n || e.dataset.path?.startsWith(`${n}/`))).map(outer);
        const left = Math.min(...ink.map((b) => b.left)) + dx - paper.left;
        const right = paper.right - Math.max(...ink.map((b) => b.right)) - dx;
        leftInsets.push(left);
        assert.ok(Math.abs(left - 40 * S) <= 1, `${kind}, ${count} cards, row ${start}: fixed left inset, no centring offset`);
        if (kind !== "spr" && names.length === columns) assert.ok(Math.abs(left - right) <= 1, `${kind}: equal insets for a full row`);
        if (kind !== "spr" || count <= columns) assert.ok(right >= left - 1, "short rows leave spare space on the right");
        if (kind === "spr") {
          assert.ok(Math.min(...ink.map((b) => b.top)) >= 0, "the row starts inside the viewport");
          assert.ok(Math.max(...ink.map((b) => b.bottom)) <= parseFloat(area.style.height), `portrait row including names fits the paper: ${Math.max(...ink.map(b => b.bottom)) / S} <= ${parseFloat(area.style.height) / S}`);
          const inner = area.children[0], extent = parseFloat(inner.style.width), visible = parseFloat(area.style.width);
          assert.equal(inner.style.height, area.style.height, "no vertical overflow");
          assert.equal(area.style.overflowX, "auto"); assert.equal(area.style.overflowY, "hidden");
          assert.ok(extent >= visible);
          const lastRight = Math.max(...ink.map((b) => b.right));
          assert.ok(lastRight <= extent - 40 * S + 1, "scroll extent includes the last inclined corner and trailing inset");
          if (count > columns) assert.ok(Math.abs(extent - lastRight - left) <= 1, "last card reaches the matching right inset at the scroll end");
        }
        if (!start) headerInsets.push(Math.min(...ink.map((b) => b.top)) + dy - paper.top);
      }
      prompt.hidden = true;
    }
  }
  assert.ok(Math.max(...headerInsets) - Math.min(...headerInsets) < 1, "all kinds share the top inset");
  assert.ok(Math.max(...leftInsets) - Math.min(...leftInsets) < 1, "all kinds and incomplete rows share the left inset");
  assert.ok(Math.abs(headerInsets[0] - 40 * S) < 1);
});

const fire = (el, type, extra = {}) => {
  const event = { type, detail: 1, defaultPrevented: false, stopped: false,
    preventDefault() { this.defaultPrevented = true; }, stopImmediatePropagation() { this.stopped = true; }, ...extra };
  for (const listener of el.listeners[type] || []) { listener(event); if (event.stopped) break; }
  return event;
};

test("portrait horizontal input handles wheel units, scaled drags, touch and keyboard clicks", async () => {
  const { horizontalScroll } = await import("../../src/bmpt/web/js/horizontal-scroll.js");
  const area = new El(); area.scrollLeft = 0; area.scrollTop = 0;
  area.getBoundingClientRect = () => ({ width: 200 }); // stage at half scale
  let captured = null; area.setPointerCapture = (id) => { captured = id; };
  horizontalScroll(area, 400, 1600);
  assert.equal(fire(area, "wheel", { deltaX: 30, deltaY: 1 }).defaultPrevented, true);
  assert.equal(area.scrollLeft, 60, "horizontal trackpad input follows the scaled stage");
  fire(area, "wheel", { deltaX: 0, deltaY: 20 }); assert.equal(area.scrollLeft, 100, "vertical wheel maps to horizontal");
  fire(area, "wheel", { deltaY: 2, deltaMode: 1 }); assert.equal(area.scrollLeft, 180, "line units");
  fire(area, "wheel", { deltaY: 1, deltaMode: 2 }); assert.equal(area.scrollLeft, 580, "page units");
  assert.equal(fire(area, "wheel", { deltaY: 100, ctrlKey: true }).defaultPrevented, false);
  assert.equal(area.scrollLeft, 580, "pinch zoom is not swallowed");
  fire(area, "wheel", { deltaX: 10000 }); assert.equal(area.scrollLeft, 1200);
  fire(area, "wheel", { deltaY: -10000 }); assert.equal(area.scrollLeft, 0);
  fire(area, "pointerdown", { button: 0, pointerId: 7, clientX: 100 });
  fire(area, "pointermove", { pointerId: 7, clientX: 98 });
  assert.equal(area.scrollLeft, 0, "a click's small movement is not a drag");
  assert.equal(captured, null, "ordinary card and pencil clicks retain their target");
  fire(area, "pointermove", { pointerId: 8, clientX: 0 }); assert.equal(area.scrollLeft, 0);
  fire(area, "pointermove", { pointerId: 7, clientX: 50 }); assert.equal(area.scrollLeft, 100);
  assert.equal(captured, 7, "drag capture continues outside the paper");
  fire(area, "pointerup", { pointerId: 7 });
  assert.equal(fire(area, "click").stopped, true, "a drag cannot select or rename a card");
  assert.equal(fire(area, "click", { detail: 0 }).stopped, false, "keyboard activation is retained");
  fire(area, "pointerdown", { button: 0, pointerId: 8, clientX: 100 });
  fire(area, "pointerup", { pointerId: 8 });
  assert.equal(fire(area, "click").stopped, false, "ordinary clicks still select");
  fire(area, "pointerdown", { button: 0, pointerId: 9, pointerType: "touch", clientX: 100 });
  fire(area, "pointermove", { pointerId: 9, clientX: 0 }); assert.equal(area.scrollLeft, 100, "touch uses native pan-x");
  fire(area, "pointerdown", { button: 0, pointerId: 10, clientX: 100 });
  fire(area, "pointercancel", { pointerId: 10 });
  fire(area, "pointermove", { pointerId: 10, clientX: 0 }); assert.equal(area.scrollLeft, 100);
  assert.equal(area.scrollTop, 0);
  const short = new El(); short.scrollLeft = 0;
  horizontalScroll(short, 400, 400);
  assert.equal(fire(short, "wheel", { deltaY: 100 }).defaultPrevented, false);
});

test("switch captures begin on visibility and retain selection and rename offsets on either axis", async () => {
  const { lazyThumbnail } = await import("../../src/bmpt/web/js/lazy-thumbnail.js");
  const { rect } = await import("../../src/bmpt/web/js/ngui.js");
  for (const kind of ["spr", "lobby", "model"]) {
    const captured = [], selections = [], aliases = [];
    const items = Array.from({ length: 19 }, (_, i) => {
      const picture = lazyThumbnail(() => { captured.push(String(i)); return Promise.resolve(`blob:${kind}-${i}`); });
      return { key: String(i), label: "Owner label", full: `Owner label ${i}`, thumb: picture, big: () => picture.start(),
        nameEdit: { label: "Rename", fn: (offset) => aliases.push([String(i), offset]) },
        badge: i === 0 ? { text: "Set", color: "#8de4ff" } : null };
    });
    const options = { host: prompt, layoutKind: kind, items, selected: "0", title: kind, okText: "Apply",
      onSelect: (key, offset) => selections.push([key, offset]), details: () => ({ title: kind, fields: [], actions: [] }) };
    await Popup.listPopup(options);
    const area = walk(prompt).find((e) => e.className === "popup-cards"), horizontal = kind === "spr";
    const axis = horizontal ? "scrollLeft" : "scrollTop", index = horizontal ? 0 : 1;
    const visible = parseFloat(area.style[horizontal ? "width" : "height"]);
    const expected = (offset) => items.filter((it) => {
      const boxes = walk(area).filter((e) => e.style.backgroundImage && (e.dataset.path === `Card_${it.key}` || e.dataset.path?.startsWith(`Card_${it.key}/`)))
        .map((e) => {
          const r = rect(e), angle = Number(/skewX\(([-\d.]+)deg\)/.exec(e.style.transform)?.[1] || 0);
          const lean = horizontal ? Math.abs(Math.tan(angle * Math.PI / 180) * r[3] / 2) : 0;
          return [r[index] - lean, r[index] + r[index + 2] + lean];
        });
      return Math.max(...boxes.map((b) => b[1])) > offset && Math.min(...boxes.map((b) => b[0])) < offset + visible;
    }).map((it) => it.key);
    assert.deepEqual([...captured].sort(), expected(0).sort(), `${kind}: only visible cards capture at open`);
    assert.ok(captured.length < items.length);
    const offset = parseFloat(area.children[0].style[horizontal ? "width" : "height"]) - visible;
    const visibleAtEnd = expected(offset);
    area[axis] = offset; fire(area, "scroll");
    await new Promise((resolve) => setTimeout(resolve, 140));
    for (const key of visibleAtEnd) assert.ok(captured.includes(key), `${kind}: newly visible card ${key} captures`);
    assert.ok(!captured.includes("9"), "unvisited middle cards stay deferred");
    assert.ok(captured.includes("18"), "the last card can be reached");
    const hits = walk(area).filter((e) => e.dataset.key);
    assert.deepEqual(hits.map((e) => e.dataset.key), items.map((it) => it.key), "keyboard order follows the row or grid");
    hits.at(-1).click(); action(area, "alias:18").click(); action(prompt, "alias:details").click();
    assert.deepEqual(selections, [["18", offset]]);
    assert.deepEqual(aliases, [["18", offset], ["0", offset]]);
    await Popup.listPopup({ ...options, selected: "18", keepScroll: offset });
    const nextArea = walk(prompt).find((e) => e.className === "popup-cards");
    assert.equal(nextArea[axis], offset, "selection redraw retains the active axis offset");
    assert.ok(walk(prompt).some((e) => e.dataset.path === "Card_18/Select"));
    assert.ok(walk(prompt).some((e) => e.dataset.path === "Card_0/Using/Text" && e.textContent === "Set"));
    assert.equal(captured.filter((key) => key === "18").length, 1, "card and details reuse one capture");
    area[axis] = offset / 2; fire(area, "scroll");
    await new Promise((resolve) => setTimeout(resolve, 140));
    assert.ok(!captured.includes("9"), "a retired popup cannot start off-screen captures after redraw");
    prompt.hidden = true;
  }
});

test("deferred switch thumbnails cancel without starting off-screen work or publishing late results", async () => {
  const { lazyThumbnail } = await import("../../src/bmpt/web/js/lazy-thumbnail.js");
  const cancelled = new AbortController(); let starts = 0;
  const pending = lazyThumbnail(() => { starts++; return "blob:unexpected"; }, cancelled.signal);
  cancelled.abort(); pending.bump();
  assert.equal(await pending, null); assert.equal(starts, 0);
  const active = new AbortController(); let finish, bumps = 0;
  const picture = lazyThumbnail(() => Object.assign(new Promise((resolve) => { starts++; finish = resolve; }), { bump: () => bumps++ }), active.signal);
  assert.equal(picture.start(), picture); picture.bump(); picture.start();
  assert.equal(starts, 1); assert.equal(bumps, 1);
  active.abort(); finish("blob:late"); assert.equal(await picture, null);
});

test("NGUI preview placeholders remain for pending, missing, rejected and undecodable stills", async () => {
  const NGUI = await import("../../src/bmpt/web/js/ngui.js");
  let finish;
  const pending = Object.assign(new Promise((resolve) => { finish = resolve; }), { thumbnail: true });
  const target = new El();
  const view = await NGUI.render(n("Still", [0, 0], [{ clipImage: pending, placeholder: "Preview unavailable", w_: 320, h_: 704 }]), target);
  const note = () => walk(target).find((e) => e.dataset.path === "Still/Placeholder");
  assert.ok(note().textContent); assert.equal(note().hidden, false);
  finish(null); await Promise.all(view.thumbnails);
  assert.equal(note().hidden, false);
  for (const source of [null, Promise.reject(new Error("capture failed")), "missing:still", "blob:ready"]) {
    target.replaceChildren();
    const Original = Image;
    globalThis.Image = class { set src(url) { queueMicrotask(() => url === "missing:still" ? this.onerror?.() : this.onload?.()); } };
    try {
      const v = await NGUI.render(n("Still", [0, 0], [{ clipImage: source, placeholder: "Preview unavailable", w_: 320, h_: 704 }]), target);
      await Promise.all(v.pictures);
      if (source === "blob:ready") assert.equal(note(), undefined, "successful decoding removes the label from the DOM");
      else {
        assert.equal(note().hidden, false);
        assert.equal(note().style.color, "#2d4664", "UIPopup_Sort cancel label colour is retained");
      }
    } finally { globalThis.Image = Original; }
  }
});

// BL-135: a still being made holds its place with the game's loading ring (UIRatioNoticePopup's LoadingIcon, turned by
// its TweenRotation); the placeholder text shows only once it fails, and both go when it decodes
test("a still being made shows the game's loading ring, the placeholder text only when it fails", async (t) => {
  const NGUI = await import("../../src/bmpt/web/js/ngui.js");
  const ICON = n("UIRatioNoticePopup", [0, 0], [], [n("Center", [0, 0], [], [n("Popup", [0, 0], [], [n("Base", [0, 0], [], [
    n("LoadingIcon", [0, 0], [{ cls: "UISprite", sprite: "Common_Login_Loading", atlas: "Common", type: 1, flip: 1, w_: 75, h_: 75,
      pivot: 4, depth: 10, color: "#ffffff/1.00" }, { cls: "TweenRotation", tween: { from: [0, 0, 180], to: [0, 0, -180], duration: 3,
      delay: 0, style: 1, method: 0 } }])])])])]);
  const base = globalThis.fetch;
  t.mock.method(globalThis, "fetch", async (url, options) => url === "/asset/ui/layout/UIRatioNoticePopup.json" ? reply(ICON) : base(url, options));
  const Original = Image;
  globalThis.Image = class { set src(url) { queueMicrotask(() => this.onload?.()); } };
  try {
    for (const outcome of [null, "blob:ready"]) {
      let finish;
      const pending = Object.assign(new Promise((resolve) => { finish = resolve; }), { thumbnail: true });
      const target = new El();
      const view = await NGUI.render(n("Still", [0, 0], [{ clipImage: pending, placeholder: "Preview unavailable", w_: 320, h_: 704 }]), target);
      const find = (path) => walk(target).find((e) => e.dataset.path === path);
      const ring = find("Still/Loading");
      assert.ok(ring, "the ring holds the place");
      assert.ok(ring.className.split(" ").includes("ngui-loading"));
      assert.deepEqual(["--turn-from", "--turn-to", "--turn-time", "--turn-loop"].map((k) => ring.style[k]),
        ["-180deg", "180deg", "3s", "normal"], "the tween's turn, clockwise on screen, 3 s a loop");
      assert.equal(find("Still/Placeholder").hidden, true, "no placeholder text while the still is made");
      finish(outcome); await Promise.all(view.thumbnails);
      if (outcome) await until(() => !find("Still/Placeholder"), "the still decoded");
      assert.equal(find("Still/Loading"), undefined, "the ring goes when the still comes or fails");
      if (outcome === null) assert.equal(find("Still/Placeholder").hidden, false, "a failed still shows the placeholder text");
    }
  } finally { globalThis.Image = Original; }
});

for (const kind of ["spr", "model"]) test(`${kind} card and info panel remove placeholders after their shared still succeeds`, async () => {
  let finish;
  const picture = Object.assign(new Promise((resolve) => { finish = resolve; }), { thumbnail: true });
  const items = ["Original", "Dress", "hina_spr"].map((label) => ({ key: label, label, full: label, thumb: picture, big: () => picture }));
  await Popup.listPopup({ host: prompt, title: kind, layoutKind: kind, items, selected: "Original",
    details: () => ({ title: kind, fields: [], actions: [] }) });
  const paths = [...items.map((it) => `Card_${it.key}/Thumb`), kind === "spr" ? "Details/Picture/Texture" : "Details/Picture/Thumb"];
  const element = (path) => walk(prompt).find((el) => el.dataset.path === path);
  const geometry = paths.map((path) => { const s = element(path).style; return [s.left, s.top, s.width, s.height]; });
  for (const path of paths) assert.ok(element(`${path}/Placeholder`));
  finish(`blob:${kind}-success`);
  await until(() => paths.every((path) => element(path).children[0].style.backgroundImage), "all stills decoded");
  paths.forEach((path, i) => {
    assert.equal(element(`${path}/Placeholder`), undefined, path);
    const s = element(path).style;
    assert.deepEqual([s.left, s.top, s.width, s.height], geometry[i], "image publication preserves prefab geometry");
  });
  prompt.hidden = true;
});

test("portrait search uses the prefab header without reducing the row height or hiding the scrollbar", async (t) => {
  const NGUI = await import("../../src/bmpt/web/js/ngui.js");
  const collection = n("UICharacterCollection", [0, 0]);
  const base = "Inventory_ListControl/RightCanvas/ListBG";
  insert(collection, "SeletControl/TopMenu", structuredClone(NGUI.find(switchLayouts.inventory, `${base}/BG_Top_Widget`)));
  insert(collection, "SeletControl/CharacterSearch", structuredClone(NGUI.find(switchLayouts.inventory, `${base}/Inbox/SearchInfo`)));
  insert(collection, "SeletControl/List/Scroll Bar", n("Scroll Bar", [0, 0], [], ["Background", "Foreground"].map((name) =>
    n(name, [1391, 0], [{ texture: "Scroll", w_: 126, h_: 990, pivot: 4, color: "#92a6b7/1.00" }]))));
  const fetch = globalThis.fetch;
  t.mock.method(globalThis, "fetch", async (url, options) => url === "/asset/ui/layout/UICharacterCollection.json" ? reply(collection) : fetch(url, options));
  const Fresh = await import("../../src/bmpt/web/js/popup.js?portrait-search");
  const items = Array.from({ length: 8 }, (_, i) => ({ key: String(i), label: `Owner ${i}`, full: `Owner ${i}` }));
  try {
    await Fresh.listPopup({ host: prompt, layoutKind: "spr", title: "Portraits", items, selected: "7", tools: {}, okText: "Apply" });
    const area = () => walk(prompt).find((e) => e.className === "popup-cards");
    const before = { ...area().style };
    action(prompt, "search").click();
    await until(() => walk(prompt).some((e) => e.className === "roster-search"));
    assert.deepEqual(area().style, before, "search keeps the complete portrait viewport");
    assert.equal(action(prompt, "search"), undefined, "the search field occupies the toolbar's header slot");
    const input = walk(prompt).find((e) => e.className === "roster-search");
    assert.ok(NGUI.rect(input)[1] + NGUI.rect(input)[3] <= parseFloat(area().style.top), "search stays above the paper");
    const bands = walk(area()).filter((e) => /Card_\d+\/Bar$/.test(e.dataset.path || ""));
    assert.ok(bands.every((e) => { const r = NGUI.rect(e); return r[1] + r[3] <= parseFloat(area().style.height); }));
    assert.equal(new Set(bands.map((e) => e.style.top)).size, 1, "the selected last card stays in the same row");
    const bar = walk(prompt).find((e) => e.className.startsWith("scrollbar"));
    assert.equal(bar.style.transform, "rotate(-90deg)");
    const thumb = walk(bar).find((e) => e.dataset.path === "Scroll Bar/Foreground");
    area().scrollLeft = 200; fire(area(), "scroll");
    assert.ok(parseFloat(thumb.style.top) > 0, "native horizontal scrolling updates the prefab thumb");
    action(prompt, "search-close").click();
    await until(() => !!action(prompt, "search"));
  } finally { prompt.hidden = true; Fresh.closed(prompt); }
});

test("standard message choices expose all three actions within one native footer", async () => {
  const pending = Popup.message({ title: "Unsaved Changes", width: 1450, lines: ["Save changes?"], buttons: [
    { label: "Keep Editing", value: "stay" }, { label: "Discard", value: "discard" }, { label: "Save and Leave", value: "save", primary: true },
  ] });
  await until(() => action(dialog, "save"));
  const choices = ["stay", "discard", "save"].map((name) => action(dialog, name));
  assert.ok(choices.every(Boolean));
  for (let i = 1; i < choices.length; i++) assert.ok(parseFloat(choices[i].style.left) > parseFloat(choices[i-1].style.left) + parseFloat(choices[i-1].style.width));
  await choices[1].click(); assert.equal((await pending).button, "discard");
});

test("Hall's opt-in failure caption stays hidden until a lazy still actually fails", async () => {
  const NGUI = await import("../../src/bmpt/web/js/ngui.js");
  for (const outcome of ["null", "reject", "success"]) {
    let resolve, reject;
    const pending = Object.assign(new Promise((yes, no) => { resolve=yes; reject=no; }), { thumbnail:true });
    const target = new El();
    const view = await NGUI.render(n("LazyStill", [0,0], [{clipImage:pending,placeholder:"Preview unavailable",placeholderOnFailure:true,w_:320,h_:600}]),target);
    const note=()=>walk(target).find((e)=>e.dataset.path==="LazyStill/Placeholder");
    assert.equal(note().hidden,true);assert.equal(note().style.display,"none");
    if(outcome==="reject")reject(new Error("capture failed")); else resolve(outcome==="null"?null:"blob:hall-success");
    await Promise.all(view.thumbnails);
    if(outcome==="success")assert.equal(note(),undefined);else { assert.equal(note().hidden,false);assert.equal(note().style.display,"flex"); }
  }
});

test("release notes never run HTML or load images, scroll, retain the offer and never download or install", async () => {
  setStrings("en-US", Object.fromEntries(strings(english)));
  const start = calls.length;
  const notes = '<script>literal</script> ![remote](https://example.invalid/image)\n' + 'More changes\n'.repeat(100);
  const release = { ...available, version: '0.6.0', page: 'https://github.com/example/releases/tag/0.6.0', notes };
  const offered = Update.check(consent, release);
  (await drawn(consent, "notes")).click();
  await drawn(dialog, "page");
  const area = walk(dialog).find((el) => el.className === "popup-cards");
  assert.ok(area);
  assert.ok(parseFloat(area.children[0].style.height) > parseFloat(area.style.height));
  // Markdown is drawn as text: tags and image marks go, their text stays, nothing becomes an element
  const text = walk(dialog).find((el) => el.dataset.path === "Lines/Line_0");
  assert.equal(text.textContent, "literal remote");
  assert.equal(text.children.length, 0);
  action(dialog, "dismiss").click();
  assert.equal(consent.hidden, false);
  assert.equal(await state(offered), 'pending');
  assert.equal(calls.slice(start).some((m) => ['app.update', 'app.installUpdate'].includes(m)), false);
  action(consent, "later").click();
  await offered;
});

test("release notes draw their Markdown in the UI language: headings larger, items with a hanging bullet", async () => {
  const NGUI = await import("../../src/bmpt/web/js/ngui.js");
  const { scriptOf } = await import("../../src/bmpt/web/js/release-notes.js");
  const shipped = readFileSync(new URL("../../src/bmpt/assets/release-notes/0.7.0.md", import.meta.url), "utf8");
  const chineseUi = JSON.parse(readFileSync(new URL("../../src/bmpt/locales/zh-CN.json", import.meta.url))).webui;
  try {
    for (const [lang, table, script] of [["en-US", english, "en"], ["zh-CN", chineseUi, "zh-CN"]]) {
      setStrings(lang, Object.fromEntries(strings(table)));
      const offered = Update.check(consent, { ...available, version: "0.7.0", page: "https://example.invalid/release", notes: shipped });
      (await drawn(consent, "notes")).click();
      await drawn(dialog, "page");
      const lines = walk(dialog).filter((e) => /^Lines\/Line_\d+$/.test(e.dataset.path || ""));
      const marks = walk(dialog).filter((e) => /^Lines\/Mark_\d+$/.test(e.dataset.path || ""));
      const size = (e) => parseFloat(e.style.fontSize) / S / NGUI.fontSizeFor(e.textContent || "x", 1);
      assert.ok(lines.length > 10 && marks.length >= 8, `${lang}: lines and bullets drawn`);
      assert.equal(scriptOf(lines.map((e) => e.textContent).join("")), script, `${lang}: one language only`);
      for (const e of lines) assert.doesNotMatch(e.textContent, /^#|\*\*|<!--|^---/, `${lang}: no Markdown marks`);
      assert.ok(Math.abs(size(lines[0]) - 52) < .01, `${lang}: a section heading is larger`);
      assert.ok(lines.filter((e) => Math.abs(size(e) - 44) < .01).length >= 8, `${lang}: items at the notes' size`);
      for (const mark of marks) {
        const text = lines.find((e) => e.dataset.path === mark.dataset.path.replace("Mark_", "Line_"));
        assert.ok(parseFloat(mark.style.left) < parseFloat(text.style.left), `${lang}: the bullet hangs left of its text`);
      }
      action(dialog, "dismiss").click();
      action(consent, "later").click();
      await offered;
    }
  } finally { setStrings("en-US", Object.fromEntries(strings(english))); }
});

test("release notes use one type size on every line: short, wrapped, long and unbroken lines alike", async () => {
  const NGUI = await import("../../src/bmpt/web/js/ngui.js");
  setStrings("en-US", Object.fromEntries(strings(english)));
  const room = 2841 - 200; // the plain message's text room at the notes popup's width
  let over = "Fixed an issue";
  while (NGUI.glyphWidth(over, 44) <= room) over += " with the mod";
  const longest = "Words that wrap on and on ".repeat(320).trim();
  const lines = ["Short", "A mid-length line about the mod library and its switches", over,
    ...[8, 12, 14, 16, 18, 40, 80, 160].map((n) => "Words that wrap on and on ".repeat(n).trim()), longest,
    ...[6, 14, 30, 60].map((n) => "更新说明里的一行文字，会在需要时换行。".repeat(n)),
    ...[10, 40].map((n) => "การแก้ไขข้อผิดพลาดของมอด ".repeat(n).trim()),
    `Full changelog: https://example.invalid/${"segment".repeat(60)}`];
  const release = { ...available, version: "0.6.0", page: "https://example.invalid/release", notes: lines.join("\n") };
  const offered = Update.check(consent, release);
  (await drawn(consent, "notes")).click();
  await drawn(dialog, "page");
  const drawnLines = lines.map((text, i) => walk(dialog).find((e) => e.dataset.path === `Lines/Line_${i}`));
  const sizes = drawnLines.map((e, i) => parseFloat(e.style.fontSize) / S / NGUI.fontSizeFor(lines[i] || "x", 1));
  assert.ok(drawnLines[2].textContent.includes("\n"), "the line just over the width wraps");
  assert.ok(drawnLines[lines.indexOf(longest)].textContent.split("\n").length > 4, "a long paragraph wraps on as many lines as it needs");
  assert.ok(drawnLines.at(-1).textContent.split("\n").length > 1, "an unbroken line breaks between characters");
  for (const [i, size] of sizes.entries()) assert.ok(Math.abs(size - 44) < 0.01, `line ${i} is ${size}`);
  action(dialog, "dismiss").click();
  action(consent, "later").click();
  await offered;
});

test("release-note button captions fit the resized UIPopup_Sort branches in all locales and window sizes", async () => {
  const NGUI = await import("../../src/bmpt/web/js/ngui.js");
  const { CONTROL, POPUP_FRAME } = await import("../../src/bmpt/web/js/ui-tokens.js");
  await Popup.ready();
  try {
    for (const lang of ['en-US', 'th-TH', 'ko-KR', 'ja-JP', 'zh-CN', 'zh-TW']) {
      const ui = JSON.parse(readFileSync(new URL(`../../src/bmpt/locales/${lang}.json`, import.meta.url))).webui;
      setStrings(lang, Object.fromEntries(strings(ui)));
      const captions = [ui.update.releasePage, ui.display.close];
      const tree = Popup.popupFrame(ui.update.notes, 2458, 1355, {
        buttons: captions.map(label => ({ label })) });
      const controls = NGUI.find(tree, 'Popup/Btn').children;
      const labels = controls.map(child => NGUI.find(child, 'Label_Locale').widgets[0]);
      assert.equal(new Set(labels.map(w => w.fontSize)).size, 1, lang);
      // the default footer is the M size (one under the old L), its bodies as wide as the captions need
      const { control, caption } = Popup.FOOTER.M, bodyW = Popup.footerWidth(captions), room = bodyW - 2 * control.pad;
      assert.ok(bodyW >= control.minW && bodyW <= CONTROL.buttonL.minW, lang);
      for (const [i, child] of controls.entries()) {
        const w = labels[i], body = NGUI.find(child, 'BG (1)');
        assert.deepEqual(child.pos, [(i - .5) * (bodyW + POPUP_FRAME.footer.gap), 0]); // two bodies, the shared gap apart
        assert.deepEqual(child.scale, [1, 1]);
        assert.deepEqual(body.pos, [0, 0]);
        assert.deepEqual([body.widgets[0].w_, body.widgets[0].h_], [bodyW, CONTROL.buttonM.h]);
        assert.deepEqual(NGUI.find(child, 'Label_Locale').pos, [0, 0]);
        assert.equal(w.text.replace(/\s/g, ''), captions[i].replace(/\s/g, ''), lang);
        const lines = w.text.split('\n');
        assert.ok(lines.length <= 2, `${lang}: ${JSON.stringify(w.text)}`);
        assert.ok(w.fontSize >= caption.fsMin && w.fontSize <= caption.fs, lang);
        for (const [width, height] of [[960, 540], [1600, 900], [2560, 1440]]) for (const density of [1, 2]) {
          const scale = Math.min(width / 1920, height / 1080) * (1920 / 2960) * child.scale[0] * density;
          assert.ok(NGUI.glyphWidth(w.text, w.fontSize) * scale <= room * scale + 1e-9, `${lang}: width`);
          assert.ok(lines.length * NGUI.fontSizeFor(w.text, w.fontSize) * NGUI.LINE_PITCH * scale
            <= caption.h * scale + 1e-9, `${lang}: height`);
        }
      }
    }
  } finally { setStrings('en-US', Object.fromEntries(strings(english))); }
});

// UI-32: the offer's three captions wrap to two lines before they shrink, in the footer's 60 to 34 range, at one size
test("the update offer's captions wrap before they shrink, share one size and fit in every locale", async () => {
  const NGUI = await import("../../src/bmpt/web/js/ngui.js");
  await Popup.ready();
  const { caption: range } = Popup.FOOTER.M, H = range.h;
  let W = 0; // the caption room on the footer's bodies for the captions at hand (Popup.footerLayout)
  const inside = (w, scale = 1) => {
    const lines = w.text.split("\n");
    return lines.length <= 2 && lines.every((line) => NGUI.glyphWidth(line, w.fontSize) * scale <= W * scale + 1e-9)
      && lines.length * NGUI.fontSizeFor(w.text, w.fontSize) * NGUI.LINE_PITCH * scale <= H * scale + 1e-9;
  };
  try {
    for (const lang of ['en-US', 'th-TH', 'ko-KR', 'ja-JP', 'zh-CN', 'zh-TW']) {
      const ui = JSON.parse(readFileSync(new URL(`../../src/bmpt/locales/${lang}.json`, import.meta.url))).webui;
      setStrings(lang, Object.fromEntries(strings(ui)));
      for (const ok of [ui.update.now, ui.update.page, ui.update.restart]) {
        const texts = [ui.update.later, ui.update.notes, ok], widgets = texts.map((text) => ({ text }));
        W = Popup.footerLayout(texts, 2098).room; // the 1700 frame takes the L class
        Update.offerCaptions(widgets);
        const fs = widgets[0].fontSize, what = `${lang} ${ok}`;
        assert.equal(new Set(widgets.map((w) => w.fontSize)).size, 1, `${what}: one size`);
        // this file's stand-in canvas is 0.6 em a character, harsher than the real font: where a caption cannot hold its size
        // in it, the group takes the next sizes down (never under the footer's floor); the logic is the shared fit
        assert.ok(fs >= range.fsMin && fs <= range.fs, `${what}: ${fs}`);
        for (const [i, w] of widgets.entries()) {
          assert.equal(w.text.replace(/\s/g, ""), texts[i].replace(/\s/g, ""), what);
          for (const [width, height] of [[960, 540], [1600, 900], [2560, 1440]]) for (const density of [1, 2]) {
            assert.ok(inside(w, Math.min(width / 1920, height / 1080) * S * density), `${what}: ${JSON.stringify(w.text)} at ${fs}`);
          }
        }
        // wrapping comes first: the largest size of the range that holds all three on at most two lines, unless one line
        // loses no more than two steps (4) to it, the shared rule of every popup's buttons (NGUI.fitGroup)
        const holds = (size) => {
          const at = texts.map((text) => ({ text }));
          NGUI.fitLabels(at, texts, size, size, W, 2, { maxH: H });
          return at.every((w) => w.fontSize === size && inside(w));
        };
        let best = range.fs;
        while (best > range.fsMin && !holds(best)) best -= 2;
        if (widgets.some((w) => w.text.includes("\n"))) assert.equal(fs, best, what);
        else assert.ok(fs >= best - 4, `${what}: one line at ${fs}, two lines at ${best}`);
        // never smaller than the old single line
        assert.ok(fs >= NGUI.fitGroup(texts, range.fs, 34, W, 1)[0].fs, what);
      }
    }
    // the review's case: Open Download Page in English drew all three at 34 on one line
    setStrings('en-US', Object.fromEntries(strings(english)));
    const widgets = [english.update.later, english.update.notes, english.update.page].map((text) => ({ text }));
    Update.offerCaptions(widgets);
    assert.ok(widgets[0].fontSize > range.fsMin);
    // the drawn offer uses the same fit
    const offered = Update.check(consent, { ...available, installable: false });
    await drawn(consent, 'page');
    const labels = ['Btn_Cancel', 'Btn_Notes', 'Btn_Ok'].map((name) => walk(consent).find((el) => el.dataset.path === `Center/Popup/Btn/${name}/Label_Locale`));
    assert.deepEqual(labels.map((el) => el.textContent), widgets.map((w) => w.text));
    assert.equal(new Set(labels.map((el) => el.style.fontSize)).size, 1);
    action(consent, 'later').click();
    await offered;
  } finally { setStrings('en-US', Object.fromEntries(strings(english))); }
});

test("empty and unavailable notes offer the exact release page in every locale", async () => {
  for (const lang of ['en-US', 'zh-CN', 'zh-TW', 'ja-JP', 'ko-KR', 'th-TH']) {
    const ui = JSON.parse(readFileSync(new URL(`../../src/bmpt/locales/${lang}.json`, import.meta.url))).webui;
    setStrings(lang, Object.fromEntries(strings(ui)));
    const release = { ...available, version: '0.6.0', page: 'https://github.com/example/releases/tag/v0.6.0' };
    const offered = Update.check(consent, release);
    (await drawn(consent, 'notes')).click();
    await drawn(dialog, 'page');
    assert.ok(walk(dialog).some((el) => el.textContent === ui.update.notesEmpty));
    const noteButtons = [0, 1].map((i) => walk(dialog).find((el) => el.dataset.path === `Center/Popup/Btn/Choice_${i}/Label_Locale`));
    assert.equal(noteButtons[0].style.fontSize, noteButtons[1].style.fontSize);
    const buttons = ['Btn_Cancel', 'Btn_Notes', 'Btn_Ok'].map((name) => walk(consent).find((el) => el.dataset.path === `Center/Popup/Btn/${name}/Label_Locale`));
    assert.equal(new Set(buttons.map((el) => el.style.fontSize)).size, 1);
    const bounds = ['Btn_Cancel', 'Btn_Notes', 'Btn_Ok'].map((name) => walk(consent).find((el) => el.dataset.path === `Center/Popup/Btn/${name}/BG (1)`));
    for (let i = 1; i < bounds.length; i++) assert.ok(parseFloat(bounds[i - 1].style.left) + parseFloat(bounds[i - 1].style.width) < parseFloat(bounds[i].style.left));
    action(dialog, 'page').click();
    await until(() => requests.at(-1)?.method === 'app.openReleasePage');
    assert.deepEqual(requests.at(-1).params, { page: release.page });
    action(consent, 'later').click();
    await offered;
  }
  setStrings('en-US', Object.fromEntries(strings(english)));
});

const Tours = await import('../../src/bmpt/web/js/tours.js');
const tourPages = [
  { id: 'photos', version: '0.6.0', title: 'Your Student Photos', body: 'Frame a portrait, save a photo, make it yours.' },
  { id: 'hall', version: '0.6.0', title: 'Your Student Hall', body: 'Keep your favorite students close.' },
];
function tourKey(layer, key, extra = {}) {
  const event = { key, preventDefault() { this.defaultPrevented = true; }, ...extra };
  for (const handler of layer.listeners.keydown || []) handler(event);
  return event;
}

test('tour navigation, focus trap and dismissal acknowledge only a painted first page', async () => {
  setStrings('en-US', Object.fromEntries(strings(english)));
  const before = body.appendChild(new El('button')); before.focus();
  const recorded = [];
  handlers.set('app.tourPresented', ({ versions }) => { recorded.push(versions); return { result: null }; });
  const opened = Tours.show(tourPages, ['0.6.0']);
  assert.equal(Tours.show(tourPages, ['0.6.0']), opened, 'only one viewer at a time');
  await drawn(dialog, 'next');
  await until(() => recorded.length === 1);
  const layer = dialog.children.at(-1);
  assert.equal(action(layer, 'previous').disabled, true);
  assert.equal(layer.getAttribute('aria-modal'), 'true');
  assert.equal(document.activeElement, layer);
  tourKey(layer, 'Tab', { shiftKey: true }); assert.equal(document.activeElement, action(layer, 'close'));
  tourKey(layer, 'Tab'); assert.equal(document.activeElement, action(layer, 'next'));
  tourKey(layer, 'Tab', { shiftKey: true }); assert.equal(document.activeElement, action(layer, 'close'));
  tourKey(layer, 'Tab'); assert.equal(document.activeElement, action(layer, 'next'));
  tourKey(layer, 'ArrowRight', { isComposing: true });
  assert.equal(action(layer, 'previous').disabled, true);
  tourKey(layer, 'ArrowRight');
  await until(() => action(layer, 'previous')?.disabled === false);
  assert.equal(action(layer, 'next').getAttribute('aria-label'), english.tour.finish);
  assert.ok(walk(layer).some((el) => el.textContent === '2 / 2'));
  tourKey(layer, 'ArrowLeft');
  await until(() => action(layer, 'previous')?.disabled === true);
  tourKey(layer, 'ArrowRight');
  await until(() => action(layer, 'previous')?.disabled === false);
  action(layer, 'next').click();
  assert.equal(await opened, true);
  assert.equal(document.activeElement, before);
  assert.deepEqual(recorded, [['0.6.0']]);
  const replay = Tours.show(tourPages, ['0.6.0']);
  await drawn(dialog, 'next');
  await until(() => recorded.length === 2);
  tourKey(dialog.children.at(-1), 'Escape');
  await replay;
  assert.equal(dialog.hidden, true);
  before.remove(); handlers.clear();
});

test('empty content and closing a pre-render consume no presentation; missing images never delay controls', async () => {
  const start = calls.length;
  assert.equal(await Tours.show([], ['0.6.0']), false);
  assert.equal(await Tours.show([{ title: '', body: '' }], ['0.6.0']), false);
  const interrupted = Tours.show(tourPages, ['0.6.0']);
  press('Escape');
  assert.equal(await interrupted, false);
  await tick();
  assert.equal(calls.slice(start).includes('app.tourPresented'), false);
  const imagePage = { ...tourPages[0], image: '/tours/missing-ui.png', caption: 'Photo controls' };
  const visible = Tours.show([imagePage], ['0.6.0']);
  await drawn(dialog, 'next');
  await until(() => calls.slice(start).includes('app.tourPresented'));
  const image = walk(dialog).find((el) => el.tagName === 'IMG' && el.src === imagePage.image);
  assert.ok(image); image.onerror(); assert.equal(image.isConnected, false);
  action(dialog, 'close').click(); await visible;
});

test('tour pages and sibling controls fit all six locales inside the notice prefab', async () => {
  const NGUI = await import('../../src/bmpt/web/js/ngui.js');
  await Popup.ready();
  const examples = {
    'en-US': ['Your Student Photos', 'Frame a portrait, save a photo, make it yours.'],
    'zh-CN': ['学生照片，随心定格', '调整构图，拍下喜欢的瞬间，设为学生照片。'],
    'zh-TW': ['學生照片，自由定格', '調整構圖，拍下喜歡的瞬間，設為學生照片。'],
    'ja-JP': ['お気に入りの一枚を', '構図を整えて撮影。学生の写真に設定。'],
    'ko-KR': ['나만의 학생 사진', '구도를 맞추고 촬영，학생 사진으로 설정.'],
    'th-TH': ['ภาพนักเรียนในแบบคุณ', 'จัดองค์ประกอบ ถ่ายภาพ แล้วตั้งเป็นภาพนักเรียน'],
  };
  try {
    for (const [lang, [title, text]] of Object.entries(examples)) {
      const ui = JSON.parse(readFileSync(new URL(`../../src/bmpt/locales/${lang}.json`, import.meta.url))).webui;
      setStrings(lang, Object.fromEntries(strings(ui)));
      for (const image of [null, '/tours/app-ui.png']) for (const index of [0, 1]) {
        const tree = Tours.pageTree({ version: '0.6.0', title, body: text, image, caption: title }, index, 2);
        const popup = NGUI.find(tree, 'Popup');
        assert.equal(NGUI.find(popup, 'Btn/Btn_Ok').actionSemantic, index === 1 ? 'finish' : 'navigation');
        const titleWidget = NGUI.find(popup, 'FeatureTitle').widgets[0];
        const bodyWidget = NGUI.find(popup, 'FeatureBody').widgets[0];
        const count = NGUI.find(popup, 'Count');
        const heading = NGUI.find(popup, 'FeatureTitle'), copy = NGUI.find(popup, 'FeatureBody');
        assert.ok(count.pos[1] - 35 >= heading.pos[1] + titleWidget.h_ / 2);
        assert.ok(heading.pos[1] - titleWidget.h_ / 2 > copy.pos[1] + bodyWidget.h_ / 2);
        if (image) {
          const slot = NGUI.find(popup, 'ImageSlot'), caption = NGUI.find(popup, 'Caption');
          assert.ok(copy.pos[1] - bodyWidget.h_ / 2 >= slot.pos[1] + 250);
          // the caption's bottom edge keeps 16 clear of the footer's tops (the footer sits 190.5 above the frame's bottom)
          assert.ok(caption.pos[1] - 35 >= Popup.footerTop(NGUI.find(popup, "Base/BG").widgets[0].h_) + 20);
        }
        for (const widget of [titleWidget, bodyWidget]) for (const line of widget.text.split('\n')) {
          assert.ok(NGUI.glyphWidth(line, widget.fontSize) <= widget.w_, `${lang}: ${line}`);
        }
        const captions = ['Btn_Cancel', 'Btn_Ok'].map((name) => NGUI.find(popup, `Btn/${name}/Label_Locale`).widgets[0]);
        assert.equal(captions[0].fontSize, captions[1].fontSize);
        for (const widget of captions) {
          assert.ok(widget.fontSize >= 48 && widget.fontSize <= 72);
          assert.ok(widget.text.split('\n').length <= 2);
          for (const line of widget.text.split('\n')) assert.ok(NGUI.glyphWidth(line, widget.fontSize) <= 400);
        }
      }
    }
  } finally { setStrings('en-US', Object.fromEntries(strings(english))); }
});

for (const lang of ['en-US', 'zh-CN', 'zh-TW', 'ja-JP', 'ko-KR', 'th-TH']) {
  test(`Settings New Features has no value and empty paths retain Not set in ${lang}`, async () => {
    const Settings = await import('../../src/bmpt/web/js/settings.js');
    const NGUI = await import('../../src/bmpt/web/js/ngui.js');
    const ui = JSON.parse(readFileSync(new URL(`../../src/bmpt/locales/${lang}.json`, import.meta.url))).webui;
    const byPath = (path) => walk(settings).find((el) => el.dataset.path === `Rows/${path}`);
    const near = (actual, expected) => assert.ok(Math.abs(actual - expected) < 0.02, `${actual} != ${expected}`);
    handlers.set('settings.get', () => ({ result: { version: '0.6.0-alpha.10', language: lang,
      languages: [{ code: lang, name: ui.languageName }], native: true, logPath: '',
      gameDirs: { global: '', japan: '' }, libraryDir: '', outputDir: '' } }));
    handlers.set('mods.state', () => ({ result: {} }));
    handlers.set('app.tours', () => ({ result: { tours: [] } }));
    setStrings(lang, Object.fromEntries(strings(ui)));
    try {
      await Settings.open(null, 'general');
      assert.ok(!byPath('Tours/Value'), 'the action-only row has no value label');
      const title = byPath('Tours/Title'), caption = byPath('Tours/Btn_0/Label_Locale');
      assert.deepEqual(walk(settings).filter((el) => el.dataset.path?.startsWith('Rows/Tours/') && el.textContent)
        .map((el) => el.textContent.replace(/\n/g, '')), [ui.tour.title, ui.tour.open]);
      assert.equal(byPath('Log/Value').textContent, ui.settings.notSet);
      const row = NGUI.rect(byPath('Tours')), button = NGUI.rect(byPath('Tours/Btn_0'));
      // UIPopup_Option_PC: the card (1552 wide, padding 58, title 17 from its top, dotted line 109 down) and ETCSetting/ButtonBg
      // (308 x 104 now): the content box starts SPACE.m (20) under the dotted line, ends SPACE.l (28) above the foot and holds the
      // button against its right edge, 58 in from the card's.
      near(row[2], 1552 * S); near(row[3], (109 + 20 + 104 + 28) * S);
      const titleRect = NGUI.rect(title);
      near(titleRect[0] - row[0], 58 * S);
      near(titleRect[1] - row[1], 17 * S);
      near(titleRect[2], (1552 - 2 * 58) * S); near(titleRect[3], 76 * S);
      near(button[0] - row[0], (1552 - 58 - 308) * S);
      near(button[1] - row[1], (109 + 20) * S);
      near(button[2], 308 * S); near(button[3], 104 * S);
      const tag = NGUI.rect(byPath('Tours/Title/TagDeco'));
      near(tag[2], 6 * S); near(tag[3], 45 * S);
      for (const el of [title, caption]) {
        const fs = parseFloat(el.style.fontSize), rect = NGUI.rect(el);
        for (const [width, height] of [[960, 540], [1600, 900], [2560, 1440]]) for (const density of [1, 2]) {
          const scale = Math.min(width / 1920, height / 1080) * density;
          for (const line of el.textContent.split('\n')) assert.ok(line.length * 0.6 * fs * scale <= (rect[2] + 0.02) * scale);
          assert.ok(el.textContent.split('\n').length * fs * 1.15 * scale <= (rect[3] + 0.02) * scale);
        }
      }
      const open = walk(settings).find((el) => el.tagName === 'BUTTON' && el.getAttribute('aria-label') === ui.tour.open
        && NGUI.rect(el).every((value, i) => Math.abs(value - button[i]) < 0.02));
      assert.ok(open);
      const pending = open.click();
      await drawn(dialog, 'cancel');
      assert.ok(walk(dialog).some((el) => el.textContent === ui.tour.empty));
      action(dialog, 'cancel').click();
      await pending;
      await Settings.open(null, 'folders');
      for (const name of ['Game_global', 'Game_japan', 'Library', 'Output']) {
        assert.equal(byPath(`${name}/Value`).textContent, ui.settings.notSet, name);
      }
    } finally {
      action(dialog, 'cancel')?.click(); settings.hidden = true; handlers.clear();
      setStrings('en-US', Object.fromEntries(strings(english)));
    }
  });
}

test('Settings replay keeps the version list underneath and earlier tours remain selectable', async () => {
  handlers.set('app.tours', () => ({ result: { tours: [
    { version: '0.6.0', pages: tourPages }, { version: '0.5.0', pages: [{ ...tourPages[0], version: '0.5.0' }] },
  ] } }));
  const versions = Tours.browse();
  (await drawn(dialog, 'line1')).click();
  await drawn(dialog, 'next');
  const top = dialog.children.at(-1);
  assert.ok(walk(top).some((el) => el.textContent.includes('0.5.0')));
  action(top, 'close').click();
  await until(() => dialog.children.length === 1);
  assert.ok(action(dialog, 'line0'));
  action(dialog, 'cancel').click(); await versions;
  handlers.set('app.tours', () => ({ result: { tours: [] } }));
  const empty = Tours.browse(); await drawn(dialog, 'cancel');
  assert.ok(walk(dialog).some((el) => el.textContent === english.tour.empty));
  action(dialog, 'cancel').click(); await empty;
  handlers.clear();
});

test('the tour list joins titles with each shipped language\'s comma', async () => {
  try {
    for (const lang of ['en-US', 'ko-KR', 'th-TH', 'ja-JP', 'zh-CN', 'zh-TW']) {
      const ui = JSON.parse(readFileSync(new URL(`../../src/bmpt/locales/${lang}.json`, import.meta.url))).webui;
      const separator = ['en-US', 'ko-KR', 'th-TH'].includes(lang) ? ', ' : '、';
      assert.equal(ui.tour.separator, separator);
      setStrings(lang, Object.fromEntries(strings(ui)));
      handlers.set('app.tours', () => ({ result: { tours: [{ version: '0.6.0', pages: tourPages }] } }));
      const pending = Tours.browse();
      await drawn(dialog, 'cancel');
      const expected = `0.6.0 - ${tourPages.map(page => page.title).join(separator)}`;
      assert.ok(walk(dialog).some(el => el.textContent.replace(/\s/g, '') === expected.replace(/\s/g, '')), lang);
      action(dialog, 'cancel').click(); await pending;
    }
  } finally { handlers.clear(); setStrings('en-US', Object.fromEntries(strings(english))); }
});

test('automatic tours wait for consents, update dialogs and jobs without checking the network', async () => {
  let busy = true, planning = 0;
  handlers.set('app.tours', () => { planning++; return { result: { automatic: { versions: ['0.8.0'], pages: tourPages } } }; });
  const start = calls.length;
  const priority = Popup.message({ title: 'Update result', lines: ['Priority dialog'] });
  await drawn(dialog, 'close');
  const pending = Tours.start({ info: { gameFound: true }, ready: Promise.resolve(), blocked: () => busy });
  await until(() => planning > 0);
  assert.equal(action(dialog, 'next'), undefined);
  action(dialog, 'close').click(); await priority;
  await tick(); assert.equal(action(dialog, 'next'), undefined);
  busy = false;
  await drawn(dialog, 'next');
  action(dialog, 'close').click(); await pending;
  assert.equal(calls.slice(start).some((method) => ['app.checkUpdate', 'app.update', 'app.installUpdate'].includes(method)), false);
  const before = planning;
  await Tours.start({ info: { gameFound: true, safeMode: true } });
  await Tours.start({ info: { gameFound: false } });
  assert.equal(planning, before);
  handlers.clear();
});

test('source frame replacement preserves card envelopes, list placement and hit targets', async () => {
  const { cardInkBounds, switchGrid, placeSwitchCards } = await import('../../src/bmpt/web/js/switch-layout.js');
  await Popup.selectionReady();
  for (const kind of ['spr', 'lobby', 'model', null]) {
    const grid = switchGrid(kind), base = { key: 'choice', label: 'Source', full: 'Source' };
    for (const selected of [false, true]) for (const applied of [false, true]) {
      const oldTree = Popup.listCard(base, 0, 0, selected, grid.card, kind);
      const tree = Popup.listCard({ ...base, applied }, 0, 0, selected, grid.card, kind);
      assert.deepEqual(cardInkBounds(tree), cardInkBounds(oldTree));
      const paper = { left: 0, right: 1818, top: 500 };
      assert.deepEqual(placeSwitchCards([tree], structuredClone(grid), paper), placeSwitchCards([oldTree], structuredClone(grid), paper));
      assert.deepEqual(tree.pos, oldTree.pos);
    }
  }
  await Popup.listPopup({ host: prompt, title: 'Sources', layoutKind: 'spr', selected: 'preview',
    items: [{ key: 'applied', label: 'Applied source', full: 'Applied source', applied: true },
      { key: 'preview', label: 'Preview source', full: 'Preview source', applied: false }], onSelect() {} });
  const hit = walk(prompt).find(e => e.dataset.key === 'applied');
  assert.ok(hit.getAttribute('aria-label').includes(' - '));
  assert.ok(walk(prompt).some(e => e.dataset.path === 'Card_applied/Select/AppliedBadge'));
  const caption = walk(prompt).find(e => e.dataset.path === 'Card_applied/Select/AppliedLabel');
  assert.equal(caption.textContent, english.status.applied);
  assert.ok(!walk(prompt).some(e => e.dataset.path === 'Card_applied/Select/Check'), 'no second check beside the badge sprite');
  assert.ok(!walk(prompt).some(e => e.dataset.path === 'Card_preview/Select/AppliedLabel'));
  assert.ok(walk(prompt).some(e => e.dataset.path === 'Card_preview/Select'));
  assert.ok(!walk(prompt).some(e => e.dataset.path === 'Card_preview/Select/AppliedBadge'));
  const previewCaption = walk(prompt).find(e => e.dataset.path === 'Card_preview/Select/PreviewLabel');
  assert.equal(previewCaption.textContent, english.status.selected);
  assert.ok(walk(prompt).some(e => e.dataset.path === 'Card_preview/Select/PreviewBadge'));
  assert.ok(!walk(prompt).some(e => e.dataset.path === 'Card_preview/Select/Check'));
  prompt.hidden = true;
});

test('source cards use the state badge and fitted locale caption in every picker', async () => {
  const NGUI = await import('../../src/bmpt/web/js/ngui.js');
  const { switchGrid } = await import('../../src/bmpt/web/js/switch-layout.js');
  await Popup.selectionReady();
  const treeWalk = n => [n, ...(n.children || []).flatMap(treeWalk)];
  try {
    for (const lang of ['en-US', 'zh-CN', 'zh-TW', 'ja-JP', 'ko-KR', 'th-TH']) {
      const ui = JSON.parse(readFileSync(new URL(`../../src/bmpt/locales/${lang}.json`, import.meta.url))).webui;
      setStrings(lang, Object.fromEntries(strings(ui)));
      const captions = Popup.selectionCaptions();
      assert.equal(captions.applied.text, ui.status.applied);
      assert.equal(captions.preview.text, ui.status.selected);
      assert.equal(captions.applied.fontSize, captions.preview.fontSize);
      for (const kind of ['spr', 'lobby', 'model', null]) {
        const captions = Popup.selectionCaptions(switchGrid(kind).card);
        for (const applied of [false, true]) for (const previewed of [false, true]) {
          const tree = Popup.listCard({ key: 'source', label: 'Source', applied }, 0, 0, previewed, switchGrid(kind).card, kind);
          const frame = NGUI.find(tree, 'Select');
          if (!applied && !previewed) { assert.equal(frame, null); continue; }
          const prefix = applied ? 'Applied' : 'Preview', caption = captions[applied ? 'applied' : 'preview'];
          const art = NGUI.find(tree, 'Thumb'), image = art.widgets[0], border = frame.widgets[0];
          assert.deepEqual(art.pos, frame.pos, 'art follows the prefab frame centre in every locale');
          assert.ok(Math.abs(image.w_ - (border.w_ - 14)) < .01);
          assert.ok(Math.abs(image.h_ - (border.h_ - 14)) < .01, 'fractional heights follow the shared nine-slice raster cuts');
          assert.equal(image.radius, 0);
          assert.equal(NGUI.find(tree, 'Line'), null, 'the old 20-unit-radius outline cannot show through the opening');
          assert.ok(!caption.overflow);
          assert.deepEqual(treeWalk(frame).flatMap(n => n.widgets.filter(w => 'sprite' in w).map(w => w.sprite)),
            ['Common_Bg_Raius10px_Select', applied ? 'Common_Icon_Mulligan_1' : 'Common_Icon_Mulligan_2']);
          const labels = treeWalk(frame).flatMap(n => n.widgets.filter(w => 'text' in w));
          assert.equal(labels.length, 1, 'only the resolved state has a caption');
          const text = labels[0];
          assert.equal(NGUI.find(frame, `${prefix}Label`).widgets[0], text);
          assert.equal(text.text, applied ? ui.status.applied : ui.status.selected);
          assert.equal(text.fontSize, caption.fontSize);
          assert.deepEqual([text.w_, text.h_, text.color], [caption.labelWidth, 52, '#7a4800/1.00']);
          assert.ok(NGUI.glyphWidth(text.text, text.fontSize) <= text.w_);
          assert.ok(NGUI.fontSizeFor(text.text, text.fontSize) * NGUI.LINE_PITCH <= NGUI.find(frame, `${prefix}Badge`).widgets[0].h_);
          assert.ok(!treeWalk(tree).some(n => n.widgets.some(w => w.sprite?.includes('Check') || w.sprite === 'Card_Ability_Select_Line_Icon')),
            'the old selection check is absent from the rendered tree');
        }
      }
    }
  } finally { setStrings('en-US', Object.fromEntries(strings(english))); }
});


test('shipped text-only tours use one fitted message frame in every locale, window and density', async () => {
  const NGUI = await import('../../src/bmpt/web/js/ngui.js');
  const { CONTROL, POPUP_FRAME } = await import('../../src/bmpt/web/js/ui-tokens.js');
  await Popup.ready();
  try {
    for (const lang of ['en-US', 'zh-CN', 'zh-TW', 'ja-JP', 'ko-KR', 'th-TH']) {
      const ui = JSON.parse(readFileSync(new URL(`../../src/bmpt/locales/${lang}.json`, import.meta.url))).webui;
      setStrings(lang, Object.fromEntries(strings(ui)));
      const pages = Object.entries(ui.tours.v0_6_0).map(([id, copy]) => ({ id, version: '0.6.0', ...copy }));
      assert.deepEqual(pages.map(({ id }) => id), ['student-thumbnails', 'hall-viewing-zoom']);
      const size = Tours.pageSize(pages);
      const confirmation = Popup.messageLayout({ lines: [ui.error.asset_delete_confirm], ok: ui.photo.delete, cancel: ui.popup.cancel });
      assert.ok([1300,1540,2098].includes(size.width));
      assert.ok([1300,1540,2098].includes(confirmation.width));
      assert.ok(size.height >= confirmation.height && size.height < Tours.GEOMETRY.height);
      const individual = pages.map((page, i) => NGUI.find(Tours.pageTree(page, i, pages.length), 'Popup/Base/BG').widgets[0].h_);
      assert.equal(size.height, Math.max(...individual), 'the longest localized page determines the frame');
      for (const [index, page] of pages.entries()) {
        const { title, body: copy } = page;
        const tree = Tours.pageTree(page, index, pages.length, size);
        const popup = NGUI.find(tree, 'Popup');
        const frame = NGUI.find(popup, 'Base/BG').widgets[0], controls = NGUI.find(popup, 'Btn');
        assert.deepEqual([frame.w_, frame.h_], [size.width, size.height]);
        assert.equal(NGUI.find(popup, 'ImageSlot'), null);
        assert.equal(NGUI.find(popup, 'Caption'), null);
        const large = NGUI.find(Tours.pageTree({ ...page, image: '/tours/app-ui.png' }, index, pages.length), 'Popup');
        assert.deepEqual(controls.children, NGUI.find(large, 'Btn').children, 'footer widgets, captions and states are unchanged');
        const bodies = ['Btn_Cancel', 'Btn_Ok'].map((name) => NGUI.find(controls, `${name}/BG (1)`));
        // the footer is the default M size, its two bodies as wide as the captions need and the shared gap apart
        const bodyW = bodies[0].widgets[0].w_;
        assert.ok(bodyW >= CONTROL.buttonM.minW && bodyW <= CONTROL.buttonL.minW, `${lang}: ${bodyW}`);
        for (const [i, name] of ['Btn_Cancel', 'Btn_Ok'].entries()) {
          assert.deepEqual(NGUI.find(controls, name).pos, [(i - .5) * (bodyW + POPUP_FRAME.footer.gap), 0], 'footer spacing');
          assert.deepEqual(bodies[i].pos, [0, 0]);
          assert.deepEqual([bodies[i].widgets[0].w_, bodies[i].widgets[0].h_], [bodyW, CONTROL.buttonM.h], 'design-system buttonM size');
        }
        const close = NGUI.find(popup, 'Base/BG/Btn_Close');
        assert.equal(close.active, true);
        assert.deepEqual(close.widgets, NGUI.find(large, 'Base/BG/Btn_Close').widgets);
        assert.deepEqual(close.pos, [frame.w_ / 2 - 78, frame.h_ / 2 - 69]);
        const heading = NGUI.find(popup, 'Base/BG/Label_Title_Locale');
        assert.equal(heading.widgets[0].text.replace(/\s/g, ''), `${ui.tour.title} - 0.6.0`.replace(/\s/g, ''));
        const counter = ui.tour.count.replace('{page}', String(index + 1)).replace('{total}', '2');
        let previousBottom = Popup.FRAME_Y + heading.pos[1] - heading.widgets[0].h_ / 2;
        for (const [path, original] of [['Count', counter], ['FeatureTitle', title], ['FeatureBody', copy]]) {
          const node = NGUI.find(popup, path), w = node.widgets[0];
          assert.equal(w.text.replace(/\s/g, ''), original.replace(/\s/g, ''));
          assert.ok(w.fontSize >= (path === 'FeatureTitle' ? 44 : 40), `${lang}: ${path} font floor`);
          assert.ok(w.text.split('\n').length * NGUI.fontSizeFor(w.text, w.fontSize) * NGUI.LINE_PITCH <= w.h_, `${lang}: ${path} text height`);
          assert.ok(node.pos[1] + w.h_ / 2 < previousBottom, `${lang}: ${path} clears the preceding text`);
          previousBottom = node.pos[1] - w.h_ / 2;
          for (const [width, height] of [[960, 540], [1600, 900], [2560, 1440]]) for (const density of [1, 2]) {
            const scale = Math.min(width / 1920, height / 1080) * S * density;
            const left = width * density / 2 - frame.w_ / 2 * scale;
            const top = height * density / 2 - (Popup.FRAME_Y + frame.h_ / 2) * scale;
            assert.ok(left >= 0 && left + frame.w_ * scale <= width * density);
            assert.ok(top >= 0 && top + frame.h_ * scale <= height * density);
            for (const line of w.text.split('\n')) assert.ok(NGUI.glyphWidth(line, w.fontSize) * scale <= w.w_ * scale);
            assert.ok((Math.abs(node.pos[0]) + w.w_ / 2) * scale < frame.w_ / 2 * scale);
            assert.ok((Math.abs(node.pos[1] - Popup.FRAME_Y) + w.h_ / 2) * scale < frame.h_ / 2 * scale);
            assert.ok((node.pos[1] - w.h_ / 2) * scale > (controls.pos[1] + bodies[0].pos[1] + bodies[0].widgets[0].h_ / 2) * scale);
          }
        }
      }
      const opened = Tours.show(pages, ['0.6.0']);
      await drawn(dialog, 'next');
      const renderedFrame = () => walk(dialog).find((el) => el.dataset.path === 'Center/Popup/Base/BG');
      const firstBounds = NGUI.rect(renderedFrame());
      assert.equal(firstBounds[2], Number((size.width * S).toFixed(2)));
      assert.equal(firstBounds[3], Number((size.height * S).toFixed(2)));
      action(dialog, 'next').click();
      await until(() => action(dialog, 'previous')?.disabled === false);
      assert.deepEqual(NGUI.rect(renderedFrame()), firstBounds, 'actual navigation preserves the frame');
      action(dialog, 'close').click(); await opened;
    }
  } finally { setStrings('en-US', Object.fromEntries(strings(english))); }
});

test('illustrated and mixed tours retain the original geometry, including a missing image', async () => {
  const NGUI = await import('../../src/bmpt/web/js/ngui.js');
  await Popup.ready();
  const pages = [tourPages[0], { ...tourPages[1], image: '/tours/missing-ui.png', caption: 'App controls' }];
  const size = Tours.pageSize(pages);
  // the frame is 68.5 taller for the footer's rise and its content half of that higher: the original room under the caption
  const lift = 34.25;
  assert.deepEqual(size, { width: 2458, height: 1468.5, image: [-1000, -400 + lift, 2000, 500], bottom: -445 + lift - 35 });
  for (const [i, page] of pages.entries()) {
    const popup = NGUI.find(Tours.pageTree(page, i, pages.length, size), 'Popup');
    assert.deepEqual([NGUI.find(popup, 'Base/BG').widgets[0].w_, NGUI.find(popup, 'Base/BG').widgets[0].h_], [2841, 1468.5]);
    assert.deepEqual(NGUI.find(popup, 'Count').pos, [0, 520 + lift]);
    assert.deepEqual(NGUI.find(popup, 'FeatureTitle').pos, [0, (page.image ? 405 : 300) + lift]);
    assert.deepEqual(NGUI.find(popup, 'FeatureBody').pos, [0, (page.image ? 220 : -40) + lift]);
    assert.equal(NGUI.find(popup, 'FeatureTitle').widgets[0].w_, 2100);
    assert.deepEqual(NGUI.find(popup, 'Btn').pos, [0, Popup.footerY(1468.5, Popup.FOOTER.M.control)]);
    if (page.image) {
      const slot = NGUI.find(popup, 'ImageSlot');
      assert.deepEqual(slot.pos, [0, -150 + lift]);
      assert.deepEqual([slot.widgets[0].w_, slot.widgets[0].h_], [2000, 500]);
      assert.deepEqual(NGUI.find(popup, 'Caption').pos, [0, -445 + lift]);
    }
  }
  const opened = Tours.show(pages, ['0.6.0']);
  await drawn(dialog, 'next');
  const frame = () => NGUI.rect(walk(dialog).find((el) => el.dataset.path === 'Center/Popup/Base/BG'));
  const initial = frame();
  action(dialog, 'next').click();
  await until(() => action(dialog, 'previous')?.disabled === false);
  assert.deepEqual(frame(), initial);
  const image = walk(dialog).find((el) => el.tagName === 'IMG');
  assert.deepEqual([image.style.left, image.style.top, image.style.width, image.style.height],
    [`${960 - 1000 * S}px`, `${540 - (100 + lift) * S}px`, `${2000 * S}px`, `${500 * S}px`]);
  image.onerror();
  assert.deepEqual(frame(), initial);
  action(dialog, 'close').click(); await opened;
});

test('tour frame height grows for longer copy and is scoped to its version', async () => {
  const NGUI = await import('../../src/bmpt/web/js/ngui.js');
  await Popup.ready();
  const pages = [tourPages[0], { ...tourPages[1], body: 'A longer explanation of the feature. '.repeat(12).trim() }];
  const size = Tours.pageSize(pages);
  assert.ok(size.height > Tours.pageSize([pages[0]]).height);
  assert.equal(Tours.pageSize([...pages].reverse()).height, size.height);
  const earlier = { ...tourPages[0], version: '0.5.0', image: '/tours/app-ui.png' };
  const opened = Tours.show([...pages, earlier], ['0.6.0', '0.5.0']);
  await drawn(dialog, 'next');
  const frame = () => NGUI.rect(walk(dialog).find((el) => el.dataset.path === 'Center/Popup/Base/BG'));
  const initial = frame();
  assert.equal(initial[3], Number((size.height * S).toFixed(2)));
  action(dialog, 'next').click();
  await until(() => walk(dialog).some((el) => el.textContent.includes('A longer explanation')));
  assert.deepEqual(frame(), initial);
  action(dialog, 'next').click();
  await until(() => !!walk(dialog).find((el) => el.tagName === 'IMG'));
  assert.equal(frame()[3], Number((Tours.GEOMETRY.height * S).toFixed(2)));
  action(dialog, 'close').click(); await opened;
});


test('Settings exposes one prerelease simulation row with both cases and stable exposes none', async () => {
  const Settings = await import('../../src/bmpt/web/js/settings.js');
  try {
    for (const lang of ['en-US', 'zh-CN', 'zh-TW', 'ja-JP', 'ko-KR', 'th-TH']) {
      const ui = JSON.parse(readFileSync(new URL(`../../src/bmpt/locales/${lang}.json`, import.meta.url))).webui;
      setStrings(lang, Object.fromEntries(strings(ui)));
      for (const enabled of [false, true]) {
        const prefs = { version: enabled ? '0.6.0-alpha.9' : '0.6.0', prereleasePreviews: enabled,
          language: lang, languages: [{ code: lang, name: ui.languageName }] };
        handlers.set('settings.get', () => ({ result: prefs }));
        handlers.set('mods.state', () => ({ result: {} }));
        await Settings.open(null, 'general');
        const rowTitle = walk(settings).filter((el) => el.dataset.path === 'Rows/UpdateSimulation/Title');
        assert.equal(rowTitle.length, enabled ? 1 : 0);
        if (!enabled) {
          const start = calls.length;
          assert.equal(await Update.simulate(consent, prefs), null);
          assert.equal(consent.hidden, true);
          assert.equal(calls.length, start);
          continue;
        }
        assert.equal(rowTitle[0].textContent.replace(/\n/g, ' '), ui.update.simulation.entry);
        const labels = [0, 1].map((i) => walk(settings).find((el) => el.dataset.path === `Rows/UpdateSimulation/Btn_${i}/Label_Locale`));
        assert.equal(new Set(labels.map((el) => el.style.fontSize)).size, 1);
        for (const [i, original] of [ui.update.notes, ui.update.simulation.empty].entries()) {
          const el = labels[i], fs = parseFloat(el.style.fontSize);
          assert.equal(el.textContent.replace(/\s/g, ''), original.replace(/\s/g, ''));
          for (const line of el.textContent.split('\n')) assert.ok(line.length * 0.6 * fs <= 342 * S + 0.1, `${lang}: ${line} at ${fs}, width ${line.length * 0.6 * fs}`);
          assert.ok(el.textContent.split('\n').length * fs * 1.15 <= 116 * S + 0.01);
        }
        const value = walk(settings).find((el) => el.dataset.path === 'Rows/UpdateSimulation/Value');
        const bodies = [0, 1].map((i) => walk(settings).find((el) => el.dataset.path === `Rows/UpdateSimulation/Btn_${i}`));
        for (const [el, original] of [[rowTitle[0], ui.update.simulation.entry], [value, ui.update.simulation.choose]]) {
          assert.equal(el.textContent.replace(/\s/g, ''), original.replace(/\s/g, ''));
          for (const line of el.textContent.split('\n')) assert.ok(line.length * 0.6 * parseFloat(el.style.fontSize) <= parseFloat(el.style.width) + 0.1);
        }
        for (const [width, height] of [[960, 540], [1600, 900], [2560, 1440]]) for (const density of [1, 2]) {
          const scale = Math.min(width / 1920, height / 1080) * density;
          assert.ok((parseFloat(value.style.left) + parseFloat(value.style.width)) * scale < parseFloat(bodies[0].style.left) * scale);
          assert.ok((parseFloat(bodies[0].style.left) + parseFloat(bodies[0].style.width)) * scale < parseFloat(bodies[1].style.left) * scale);
          for (const body of bodies) {
            assert.ok(Math.abs(parseFloat(body.style.width) - 308 * S) < 0.01);
            assert.ok(Math.abs(parseFloat(body.style.height) - 104 * S) < 0.01);
            const centre = (el) => parseFloat(el.style.top) + parseFloat(el.style.height) / 2;
            assert.ok(Math.abs(centre(body) - centre(value)) * scale < 0.01 * scale);
          }
        }
        // Both Settings actions enter the same real update renderer.
        for (const [i, caption] of [ui.update.notes, ui.update.simulation.empty].entries()) {
          if (i) await Settings.open(null, 'general');
          const button = walk(settings).find((el) => el.tagName === 'BUTTON' && el.getAttribute('aria-label') === caption);
          const start = calls.length;
          const done = button.click();
          await drawn(consent, 'update');
          assert.equal(settings.hidden, true);
          assert.ok(part('V_1'));
          (await drawn(consent, 'notes')).click();
          await drawn(dialog, 'page');
          assert.ok(walk(dialog).some((el) => el.textContent === (i ? ui.update.notesEmpty : ui.update.simulation.sample)));
          action(dialog, 'dismiss').click();
          action(consent, 'later').click();
          await done;
          // only the build's own notes are read (a local file): no check, download, install or browser open
          assert.deepEqual(calls.slice(start).filter((m) => m !== 'app.releaseNotes'), [], 'simulation makes no other RPC');
        }
      }
    }
  } finally { action(consent, 'later')?.click(); settings.hidden = true; handlers.clear(); setStrings('en-US', Object.fromEntries(strings(english))); }
});

test('simulated offers use the real prefab and notes builder, intercept every action and fit every locale', async () => {
  const { CONTROL } = await import('../../src/bmpt/web/js/ui-tokens.js');
  const byPath = (root, path) => walk(root).find((el) => el.dataset.path === path);
  try {
    for (const lang of ['en-US', 'zh-CN', 'zh-TW', 'ja-JP', 'ko-KR', 'th-TH']) {
      const ui = JSON.parse(readFileSync(new URL(`../../src/bmpt/locales/${lang}.json`, import.meta.url))).webui;
      setStrings(lang, Object.fromEntries(strings(ui)));
      for (const notes of [true, false]) {
        const start = calls.length;
        const offered = Update.simulate(consent, { version: '0.6.0-alpha.9', prereleasePreviews: true }, notes);
        await drawn(consent, 'update');
        const title = byPath(consent, 'Center/Popup/Base/BG/Label_Title_Locale');
        assert.equal(title.textContent.replace(/\n/g, ''), ui.update.title + ui.update.simulation.suffix);
        const geometry = ['Btn_Cancel', 'Btn_Notes', 'Btn_Ok'].map((name) => byPath(consent, `Center/Popup/Btn/${name}/BG (1)`));
        assert.equal(actionsIn(consent).length, 3);
        const wanted = Popup.footerLayout([ui.update.later, ui.update.notes, ui.update.now], 2098);
        for (const el of geometry) {
          // Three choices keep equal M bodies, as wide as the captions need, at this L width.
          assert.ok(Math.abs(parseFloat(el.style.width) - wanted.bodyW * S) < 0.01);
          assert.ok(Math.abs(parseFloat(el.style.height) - CONTROL.buttonM.h * S) < 0.01);
        }
        for (let i = 1; i < geometry.length; i++) assert.ok(parseFloat(geometry[i - 1].style.left) + parseFloat(geometry[i - 1].style.width) < parseFloat(geometry[i].style.left));
        const labels = ['Btn_Cancel', 'Btn_Notes', 'Btn_Ok'].map((name) => byPath(consent, `Center/Popup/Btn/${name}/Label_Locale`));
        assert.equal(new Set(labels.map((el) => el.style.fontSize)).size, 1);
        for (const el of [title, ...labels]) {
          const fs = parseFloat(el.style.fontSize);
          // a body as wide as its caption needs holds the widest line exactly: the styles are rounded to 0.01 px
          for (const line of el.textContent.split('\n')) assert.ok(line.length * 0.6 * fs <= parseFloat(el.style.width) + 0.1, `${lang}: ${line}`);
        }
        (await drawn(consent, 'notes')).click();
        await drawn(dialog, 'page');
        assert.ok(walk(dialog).some((el) => el.className === 'popup-cards'), 'real scrollable release-notes view');
        const notesTitle = byPath(dialog, 'Center/Popup/Base/BG/Label_Title_Locale');
        assert.ok(notesTitle.textContent.includes(ui.update.simulation.suffix.trim()));
        action(dialog, 'page').click();
        await until(() => part('Status')?.textContent.replace(/\n/g, ' ') === ui.update.simulation.pageStatus);
        assert.equal(action(consent, 'restart'), undefined);
        action(consent, 'update').click();
        await until(() => part('Status')?.textContent.replace(/\n/g, ' ') === ui.update.simulation.status);
        // Retained/repeated clicks never enter a writer phase or offer Restart.
        action(consent, 'update').click(); await tick();
        assert.equal(action(consent, 'restart'), undefined);
        assert.equal(action(consent, 'later').disabled, false);
        const status = part('Status'), fs = parseFloat(status.style.fontSize);
        for (const [w, h] of [[960, 540], [1600, 900], [2560, 1440]]) for (const density of [1, 2]) {
          const scale = Math.min(w / 1920, h / 1080) * density;
          for (const line of status.textContent.split('\n')) assert.ok(line.length * 0.6 * fs * scale <= 1250 * S * scale + 0.01);
          assert.ok(status.textContent.split('\n').length * fs * 1.15 <= 96 * S + 0.01);
        }
        action(consent, 'later').click(); await offered;
        assert.deepEqual(calls.slice(start).filter((m) => m !== 'app.releaseNotes'), [],
          'no network RPC, updater job, staging, installation or external browser');
      }
    }
  } finally { action(consent, 'later')?.click(); setStrings('en-US', Object.fromEntries(strings(english))); }
});

test('simulation never joins or replaces an existing real update offer', async () => {
  const offered = Update.check(consent, { ...available, downloaded: true, file: '/verified/update.dmg' });
  await drawn(consent, 'restart');
  assert.equal(await Update.simulate(consent, { version: '0.6.0-alpha.9', prereleasePreviews: true }), null);
  assert.ok(action(consent, 'restart'));
  action(consent, 'later').click(); await offered;
});

// --- the display settings over a dialog (#display over #dialog) ---
const appCss = readFileSync(new URL("../../src/bmpt/web/css/app.css", import.meta.url), "utf8");
const zRule = (selector) => Number(new RegExp(`^${selector.replace(/[.#]/g, "\\$&")} \\{[^}]*?z-index: (\\d+)`, "m").exec(appCss)?.[1]);
// a host's z-index from the stylesheet: the id's own rule, the class rule of its kind, and `.over` where it is set
const stacking = (host) => {
  const over = host.className.split(" ").includes("over");
  return host.id === "display" ? zRule(over ? "#display.over" : ".settings-host") : zRule(`#${host.id}`);
};

test("the display settings open over a dialog: above it in the page and on the stack, Escape and the backdrop close them first", async (t) => {
  const collection = n("UICharacterCollection", [0, 0], [], [n("SeletControl", [0, 0], [], [n("List", [0, 0], [], [
    n("Scroll Bar", [0, 0], [], ["Background", "Foreground"].map((name) => n(name, [1391, 0], [{ texture: "Scroll", w_: 126, h_: 990, pivot: 4, color: "#92a6b7/1.00" }])))])])]);
  const fetch = globalThis.fetch;
  t.mock.method(globalThis, "fetch", async (url, options) => url === "/asset/ui/layout/UICharacterCollection.json" ? reply(collection) : fetch(url, options));
  const Display = await import("../../src/bmpt/web/js/display.js");
  const done = [];
  const open = () => Display.open({ view: { sort: "name", order: "asc", filters: {} }, count: () => 3, onDone: (draft) => done.push(draft) });
  const backdrop = () => walk(display).find((e) => e.dataset.path === "Center/BlackBg/BlackBgClickArea");

  // the stylesheet: plainly under the dialog, over it with `.over`
  assert.equal(stacking(display), 2500);
  display.classList.add("over");
  assert.ok(stacking(display) > stacking(dialog), "the display settings with .over draw above #dialog");
  display.classList.remove("over");
  assert.ok(stacking(display) < stacking(dialog), "without .over today's order stays");

  // opened with no dialog up (the student list): today's order
  await open();
  assert.equal(display.hidden, false);
  assert.ok(!display.className.split(" ").includes("over"));
  assert.ok(stacking(display) < stacking(dialog));
  press("Escape");
  assert.equal(display.hidden, true);
  assert.deepEqual(done, [null]);

  // opened from a dialog (Assign Character): over it
  const closes = [];
  const layer = dialog.appendChild(new El());
  dialog.hidden = false;
  Popup.opened(dialog, (how) => { closes.push(how); layer.remove(); dialog.hidden = true; }, { el: layer });
  await open();
  assert.ok(display.className.split(" ").includes("over"));
  assert.ok(stacking(display) > stacking(dialog), "the display settings draw above the dialog");
  assert.equal(Popup.top().host, display, "and are first on the popup stack");
  assert.ok(!dialog.contains(display) && !display.contains(dialog), "siblings: a click in one never reaches the other");

  // Escape: the display settings first, then the dialog
  assert.ok(press("Escape").defaultPrevented);
  assert.deepEqual([display.hidden, closes], [true, []], "the dialog is still open");
  assert.ok(!display.className.split(" ").includes("over"), "the layer class goes with the popup");
  assert.deepEqual(done, [null, null]);
  assert.equal(Popup.top().el, layer);
  press("Escape");
  assert.deepEqual(closes, ["escape"]);
  assert.equal(Popup.top(), null);

  // the backdrop of the display settings closes them alone
  dialog.hidden = false;
  dialog.appendChild(layer);
  Popup.opened(dialog, (how) => { closes.push(how); layer.remove(); dialog.hidden = true; }, { el: layer });
  await open();
  const back = backdrop();
  assert.ok(back, "the display settings' backdrop is drawn");
  back.click();
  assert.deepEqual([display.hidden, closes, done.length], [true, ["escape"], 3], "the backdrop click stays inside the display settings");
  assert.equal(dialog.hidden, false);
  assert.equal(Popup.top().el, layer);
  press("Escape");
  assert.deepEqual(closes, ["escape", "escape"]);
});

// Options side by side are one kind of control: every sort key, and every filter option of every group, draws at one size
test("the display settings' sort keys and filter options each share one font size in every language", async (t) => {
  const collection = n("UICharacterCollection", [0, 0], [], [n("SeletControl", [0, 0], [], [n("List", [0, 0], [], [
    n("Scroll Bar", [0, 0], [], ["Background", "Foreground"].map((name) => n(name, [1391, 0], [{ texture: "Scroll", w_: 126, h_: 990, pivot: 4, color: "#92a6b7/1.00" }])))])])]);
  const fetch = globalThis.fetch;
  t.mock.method(globalThis, "fetch", async (url, options) => url === "/asset/ui/layout/UICharacterCollection.json" ? reply(collection) : fetch(url, options));
  const Display = await import("../../src/bmpt/web/js/display.js");
  const label = (root, pattern) => walk(root).filter((e) => pattern.test(e.dataset.path || "") && e.textContent);
  try {
    // the filter tab is the module's tab until a sort-only popup switches it, so the filter passes come first
    for (const sortOnly of [false, true]) for (const locale of ["en-US", "zh-CN", "zh-TW", "ja-JP", "ko-KR", "th-TH"]) {
      const ui = JSON.parse(readFileSync(new URL(`../../src/bmpt/locales/${locale}.json`, import.meta.url), "utf8")).webui;
      setStrings(locale, Object.fromEntries(strings(ui)));
      document.documentElement.lang = locale;
      await Display.open({ view: { sort: "name", order: "asc", filters: {} }, count: () => 3, onDone() {}, sortOnly });
      if (sortOnly) {
        // the five sort keys, each with the one label that shows (dark with the button on, dimmed off)
        const sizes = label(display, /\/Sort_\w+\/Label_Text\/(On\/)?Label_On$/).map((e) => e.style.fontSize);
        assert.equal(sizes.length, 5, `${locale}: five sort keys`);
        assert.equal(new Set(sizes).size, 1, `${locale}: the sort keys at ${sizes}`);
      } else {
        // the eight options of the four groups
        await until(() => label(display, /Unit_\w+\/SwitchObject_Columns_4\/Label_Text$/).length === 8);
        const sizes = label(display, /Unit_\w+\/SwitchObject_Columns_4\/Label_Text$/).map((e) => e.style.fontSize);
        assert.equal(new Set(sizes).size, 1, `${locale}: the filter options at ${sizes}`);
      }
      press("Escape");
    }
  } finally { setStrings("en-US", Object.fromEntries(strings(english))); document.documentElement.lang = "en-US"; }
});

// --- long messages scroll by themselves (the Add result, the dropped queue list) ---
const messageShapes = {
  boxed: (n) => ({ lines: Array.from({ length: n }, (_, i) => ({ text: `Mod ${i} belongs to a student\nApplied`, button: { label: "Go To", fn() {} } })), ok: "Go To", cancel: "Stay Here" }),
  plain: (n) => ({ lines: Array.from({ length: n }, (_, i) => `Mod ${i} belongs to a student\nApplied`), ok: "Go To", cancel: "Stay Here" }),
  single: (n) => ({ lines: Array.from({ length: n }, (_, i) => `Entry ${i} - not applied`), ok: "OK" }),
  check: (n) => ({ lines: Array.from({ length: n }, (_, i) => `Line ${i}`), check: { label: "Recursive", checked: false }, ok: "OK", cancel: "Cancel" }),
  choices: (n) => ({ lines: Array.from({ length: n }, (_, i) => `Line ${i}`), buttons: [{ label: "A", value: "a", primary: true }, { label: "B", value: "b" }, { label: "C", value: "c" }] }),
  none: (n) => ({ lines: Array.from({ length: n }, (_, i) => `Line ${i}`) }),
  fit: (n) => ({ lines: Array.from({ length: n }, (_, i) => `Line ${i}`), fitTogether: [0, 1], plain: true, ok: "OK" }),
};

test("message stacks centre above the shared floor and grow to the stage cap, then scroll with 20-unit footer clearance", async () => {
  await Popup.ready();
  for (const [shape, make] of Object.entries(messageShapes)) for (let n = 0; n <= 40; n++) {
    const layout = Popup.messageLayout(make(n));
    assert.ok(layout.height >= 786 && layout.height <= 1560, `${shape} ${n}`);
    const usedHeight = layout.visible + (shape === "check" ? Popup.MSG.check : 0);
    const free = layout.scroll ? 0 : Math.max(0, layout.content.h - usedHeight);
    near2(layout.y0, Popup.FRAME_Y + layout.height / 2 - 170 - free / 2);
    assert.equal(layout.scroll, layout.body.height > layout.visible);
    const used = layout.visible + (shape === "check" ? Popup.MSG.check : 0);
    if (shape !== "none") assert.ok(layout.y0 - used >= Popup.footerTop(layout.height) + 20 - 1e-9);
    if (n === 40) assert.ok(layout.scroll, `${shape}: all forty paragraphs preserved in the scrolling content`);
  }
  const asked = Popup.messageLayout({ ...messageShapes.plain(2), scroll: true });
  assert.equal(asked.scroll, true);
  assert.equal(asked.height, 786);
  // A scroll that is asked for but not needed keeps the body's own height, centred in the content box.
  near2(asked.visible, asked.body.height);
  near2(Popup.FRAME_Y + asked.height / 2 - asked.y0 + asked.visible / 2, asked.content.y + asked.content.h / 2);
});

const ADD_LOCALES = ["en-US", "zh-CN", "zh-TW", "ja-JP", "ko-KR", "th-TH"];
const fill = (text, params) => text.replace(/\{(\w+)\}/g, (_, key) => params[key] ?? "");
const onStage = ([left, top, width, height]) => left >= -0.01 && top >= -0.01 && left + width <= 1920.01 && top + height <= 1080.01;

for (const lang of ADD_LOCALES) test(`the Add result and the dropped list keep their frame, title and buttons on the stage for 1 to 40 rows in ${lang}`, async () => {
  const NGUI = await import("../../src/bmpt/web/js/ngui.js");
  const ui = JSON.parse(readFileSync(new URL(`../../src/bmpt/locales/${lang}.json`, import.meta.url))).webui;
  setStrings(lang, Object.fromEntries(strings(ui)));
  try {
    // mod-switch.js queueDone: one "what - reason" line per dropped item, Confirm
    const dropped = (n) => Array.from({ length: n }, (_, i) => `${ui.mods.onTitle} ${i} - ${fill(ui.error.library_io_error, { file: `mod_${i}_spr.bundle` })}`);
    const cases = [
      // as called: with the error, so an Error Report button joins Confirm
      ["Not applied", (n) => ({ title: ui.classic.notApplied, error: { code: "internal", params: {} }, lines: dropped(n), ok: ui.picker.ok })]];
    for (const [name, make] of cases) for (let n = 1; n <= 40; n++) {
      const options = make(n), expected = Popup.messageLayout(options);
      const pending = Popup.message(options);
      await drawn(dialog, "ok");
      const path = (p) => walk(dialog).find((e) => e.dataset.path === `Center/Popup/${p}`);
      const label = `${name}, ${n} rows`;
      // the frame is the layout's, never taller than the cap, and so are the drawn title and buttons
      const frame = NGUI.rect(path("Base/BG"));
      assert.ok(Math.abs(frame[3] - expected.height * S) < 0.01 && expected.height <= 1560, label);
      for (const part of ["Base/BG", "Base/BG/Label_Title_Locale", "Base/BG/Btn_Close", "Btn/Btn_Ok/BG (1)", ...(options.cancel || options.error ? ["Btn/Btn_Cancel/BG (1)"] : [])]) {
        assert.ok(onStage(NGUI.rect(path(part))), `${label}: ${part} on the stage`);
      }
      // the buttons that answer it are on the stage too
      for (const key of ["ok", "close", ...(options.cancel || options.error ? ["cancel"] : [])]) {
        const button = action(dialog, key);
        assert.ok(button, `${label}: ${key}`);
        assert.ok(onStage(["left", "top", "width", "height"].map((k) => parseFloat(button.style[k]))), `${label}: ${key} button on the stage`);
      }
      // long ones scroll in the frame, short ones lay out as before
      const area = walk(dialog).find((e) => e.className === "popup-cards");
      assert.equal(!!area, expected.scroll, label);
      if (area) {
        assert.ok(parseFloat(area.style.top) + parseFloat(area.style.height) <= NGUI.rect(path("Btn/Btn_Ok/BG (1)"))[1], `${label}: the list ends above the buttons`);
        assert.ok(parseFloat(area.style.top) >= NGUI.rect(path("Base/BG/Label_Title_Locale"))[1] + NGUI.rect(path("Base/BG/Label_Title_Locale"))[3], `${label}: and starts under the title`);
        if (options.lines.some((line) => line.button)) assert.ok(action(dialog, `line${n - 1}`), `${label}: the last row's button is in the list`);
      }
      action(dialog, "ok").click();
      assert.deepEqual(await pending, { button: "ok", checked: null });
    }
  } finally { setStrings("en-US", Object.fromEntries(strings(english))); }
});

test("Settings is on the popup stack: Escape and the backdrop close it, and a popup over it takes the key first", async () => {
  const Settings = await import("../../src/bmpt/web/js/settings.js");
  handlers.set("settings.get", () => ({ result: { version: "1.0.0", language: "en-US", languages: [{ code: "en-US", name: "English" }], cacheDir: "/test/cache", native: true } }));
  handlers.set("mods.state", () => ({ result: {} }));
  try {
    await Settings.open();
    assert.equal(settings.hidden, false);
    assert.equal(Popup.top().host, settings, "registered with Popup.opened");
    // a message over it takes Escape first; Settings stays up
    const over = Popup.message({ title: "Over", lines: ["Line"], ok: "OK" });
    await drawn(dialog, "ok");
    assert.equal(Popup.top().host, dialog);
    assert.ok(press("Escape").defaultPrevented);
    assert.deepEqual(await over, { button: null, checked: null });
    assert.equal(settings.hidden, false);
    assert.equal(Popup.top().host, settings);
    // then Escape closes Settings itself
    assert.ok(press("Escape").defaultPrevented);
    assert.equal(settings.hidden, true);
    assert.equal(settings.children.length, 0);
    assert.equal(Popup.top(), null);
    assert.equal(press("Escape").defaultPrevented, false, "nothing open: the key is the screen's");
    // the backdrop closes it, a click on its body that misses a control does not
    await Settings.open();
    walk(settings).find((el) => el.dataset.path === "Center/Popup/Base/BG").click();
    assert.equal(settings.hidden, false);
    walk(settings).find((el) => el.dataset.path === "Center/BlackBg/BlackBgClickArea").click();
    assert.equal(settings.hidden, true);
    assert.equal(Popup.top(), null);
    // a redraw (a tab, a setting) keeps one place on the stack
    await Settings.open();
    const tab = walk(settings).find((el) => el.tagName === "BUTTON" && el.getAttribute("aria-label") === english.settings.tab.mods);
    assert.ok(tab);
    const stackBefore = Popup.top();
    tab.click();
    await until(() => walk(settings).some((el) => el.dataset.path?.includes("Rows/Switch")));
    assert.equal(Popup.top().host, settings);
    assert.ok(stackBefore.host === settings);
    press("Escape");
    assert.equal(settings.hidden, true);
    assert.equal(Popup.top(), null);
  } finally { handlers.clear(); settings.hidden = true; settings.innerHTML = ""; }
});

// UI-15: Mod Alias and the repair preview are layers of their own in #dialog, like the message popups
const layers = () => dialog.children.filter((el) => el.className === "popup-layer");
test("Mod Alias is a layer of its own: a failed save's message draws above it, and Escape closes the visible layer", async () => {
  const ModAlias = await import("../../src/bmpt/web/js/mod-alias.js");
  handlers.set("mods.rename", () => ({ error: { code: "library_io_error", params: { file: "x.bundle" } } }));
  try {
    const saved = [];
    const opening = ModAlias.open({ mod: { id: "m1", alias: "" }, onSaved: async (value) => saved.push(value) });
    const ok = await drawn(dialog, "ok");
    await opening;
    const [alias] = layers();
    assert.equal(layers().length, 1);
    assert.ok(alias.contains(ok), "its buttons are in its own layer, not loose in the host");
    assert.ok(walk(alias).some((el) => el.className.includes("mod-alias-input")));
    assert.equal(dialog.children.every((el) => el === alias), true, "nothing else sits in the host");
    assert.equal(Popup.top().el, alias);
    ok.click();
    await until(() => layers().length === 2);
    const [, message] = layers();
    assert.notEqual(message, alias);
    assert.equal(dialog.children.indexOf(message) > dialog.children.indexOf(alias), true, "the failed save's message is above Mod Alias");
    assert.equal(alias.isConnected, true, "the alias popup stays open for another try");
    assert.equal(walk(alias).find((el) => el.className.includes("mod-alias-input")).disabled, false);
    assert.equal(Popup.top().el, message, "Escape belongs to the message");
    assert.ok(press("Escape").defaultPrevented);
    await until(() => layers().length === 1);
    assert.equal(alias.isConnected, true, "closing the message leaves Mod Alias");
    assert.equal(dialog.hidden, false);
    assert.equal(Popup.top().el, alias);
    assert.ok(press("Escape").defaultPrevented);
    assert.equal(layers().length, 0);
    assert.equal(dialog.hidden, true);
    assert.deepEqual(saved, []);
  } finally { handlers.clear(); }
});

test("closing Mod Alias leaves the other layers of the host, and a saved name closes only its own layer", async () => {
  const ModAlias = await import("../../src/bmpt/web/js/mod-alias.js");
  handlers.set("mods.rename", () => ({ result: { id: "m1", label: "Name" } }));
  try {
    const saved = [];
    const opening = ModAlias.open({ mod: { id: "m1", alias: "Old" }, onSaved: async (value) => saved.push(value) });
    await drawn(dialog, "ok");
    await opening;
    const [alias] = layers();
    const other = Popup.message({ title: "Other", lines: ["Line"], ok: "OK" });
    await until(() => layers().length === 2 && action(layers()[1], "ok"));
    const [, message] = layers();
    action(alias, "cancel").click(); // Cancel on the layer under the message
    assert.equal(alias.isConnected, false);
    assert.equal(message.isConnected, true, "the other layer is not removed");
    assert.equal(dialog.hidden, false);
    action(message, "ok").click();
    await other;
    assert.equal(dialog.hidden, true);
    // a save that works closes Mod Alias and reports the result
    const again = ModAlias.open({ mod: { id: "m1", alias: "" }, onSaved: async (value) => saved.push(value) });
    const ok = await drawn(dialog, "ok");
    await again;
    ok.click();
    await until(() => saved.length === 1);
    assert.deepEqual(saved, [{ id: "m1", label: "Name" }]);
    assert.equal(layers().length, 0);
    assert.equal(dialog.hidden, true);
    // opening it again while one is open replaces that one only
    const first = ModAlias.open({ mod: { id: "m1", alias: "" }, onSaved: async () => {} });
    await drawn(dialog, "ok"); await first;
    const second = ModAlias.open({ mod: { id: "m2", alias: "" }, onSaved: async () => {} });
    await until(() => layers().length === 1 && action(dialog, "ok")); await second;
    assert.equal(layers().length, 1);
    action(dialog, "close").click();
    assert.equal(dialog.hidden, true);
  } finally { handlers.clear(); }
});

test("the repair preview is a layer of its own: a message over it, Escape on the visible layer, and a close that leaves the others", async () => {
  const Compare = await import("../../src/bmpt/web/js/mod-compare.js");
  const review = { lines: ["moved 1"], accept: english.remap.accept, discard: english.remap.discard };
  const pending = Compare.show({ assets: [], pages: [], drawings: {} }, "Painted", "dialog", review);
  await drawn(dialog, "cancel");
  const [preview] = layers();
  assert.equal(layers().length, 1);
  assert.ok(preview.contains(action(dialog, "ok")), "its buttons are in its own layer");
  assert.equal(Popup.top().el, preview);
  const over = Popup.message({ title: "Failed", lines: ["It failed"], ok: "OK" });
  await until(() => layers().length === 2 && action(layers()[1], "ok"));
  const [, message] = layers();
  assert.equal(dialog.children.indexOf(message) > dialog.children.indexOf(preview), true, "the message draws above the preview");
  assert.equal(Popup.top().el, message);
  assert.ok(press("Escape").defaultPrevented);
  assert.deepEqual(await over, { button: null, checked: null });
  assert.equal(preview.isConnected, true, "Escape closed only the message");
  assert.equal(Popup.top().el, preview);
  // Discard on the preview leaves a message opened over it
  const other = Popup.message({ title: "Other", lines: ["Line"], ok: "OK" });
  await until(() => layers().length === 2 && action(layers()[1], "ok"));
  action(preview, "cancel").click();
  assert.equal(await pending, false);
  assert.equal(preview.isConnected, false);
  assert.equal(layers().length, 1, "the other layer stays");
  assert.equal(dialog.hidden, false);
  action(layers()[0], "ok").click();
  await other;
  assert.equal(dialog.hidden, true);
  // Accept resolves true
  const accepted = Compare.show({ assets: [], pages: [], drawings: {} }, "Painted", "dialog", review);
  action(await drawn(dialog, "ok").then(() => dialog), "ok").click();
  assert.equal(await accepted, true);
  assert.equal(dialog.hidden, true);
});

// UI-22: a search or segment with no match says so in the list paper, and confirming a hidden choice is blocked
const noMatch = () => walk(prompt).filter((e) => e.textContent === english.hall.noCandidates && e.dataset.path === "Empty");
test("a list whose segment shows nothing says so in the paper, and its primary button answers nothing", async () => {
  const everything = [{ key: "a", label: "Alpha", full: "Alpha", recruit: true }, { key: "b", label: "Beta", full: "Beta", recruit: false }];
  const asked = [];
  const options = (items, selected) => ({ host: prompt, title: "Sources", items, selected, tools: { segs: ["seg.all", "seg.recruit", "seg.noRecruit"], seg: 0, onSeg() {} },
    okText: "Apply", onOk: () => asked.push("ok"), onClose: () => asked.push("close"),
    details: (it) => ({ title: "Details", fields: [["File", it.label]], actions: [] }) });
  try {
    // everything shown: the primary button works
    await Popup.listPopup(options(everything, "a"));
    assert.equal(noMatch().length, 0);
    const colour = (root) => walk(root).find((e) => e.dataset.path === "Center/Popup/Btn/Btn_0/Label_Locale")?.style.color;
    const normal = colour(prompt), plainOk = action(prompt, "ok");
    assert.equal(plainOk.disabled ?? false, false);
    plainOk.click();
    assert.deepEqual(asked, ["ok"]);
    // the chosen card filtered out (the caller's segment removed it): the paper still lists the others, Apply is blocked
    asked.length = 0;
    await Popup.listPopup(options(everything.slice(1), "a"));
    assert.equal(noMatch().length, 0, "other cards are shown: no empty message");
    const blocked = action(prompt, "ok");
    assert.equal(blocked.disabled, true);
    assert.equal(blocked.getAttribute("aria-disabled"), "true");
    blocked.click();
    assert.deepEqual(asked, [], "a disabled button does not answer");
    assert.notEqual(colour(prompt), normal, "drawn as the game's disabled button");
    // nothing shown at all
    await Popup.listPopup(options([], "a"));
    const [empty] = noMatch();
    assert.ok(empty, "hall.noCandidates is in the list paper");
    assert.equal(action(prompt, "ok").disabled, true);
    const paper = walk(prompt).find((e) => e.dataset.path === "Center/Popup/ListPaper");
    assert.ok(paper, "the list paper is drawn");
    const [pl, pt, pw, ph] = NGUI_rect(paper), [el, et, ew, eh] = NGUI_rect(empty);
    assert.ok(el >= pl && el + ew <= pl + pw && et >= pt && et + eh <= pt + ph, "the message lies inside the paper");
    assert.ok(Math.abs((el + ew / 2) - (pl + pw / 2)) < 1, "centred across the paper");
    // the paper's own text is the app's string
    assert.equal(empty.textContent, english.hall.noCandidates);
    // the segment is cleared by the caller: the card is back and Apply works again
    asked.length = 0;
    await Popup.listPopup(options(everything, "a"));
    assert.equal(noMatch().length, 0);
    assert.equal(action(prompt, "ok").disabled ?? false, false);
    action(prompt, "ok").click();
    assert.deepEqual(asked, ["ok"]);
    // a popup without tools (a prompt) is not blocked, whatever it lists
    asked.length = 0;
    await Popup.listPopup({ host: prompt, title: "Prompt", items: everything.slice(1), selected: "a", okText: "Apply", onOk: () => asked.push("ok") });
    action(prompt, "ok").click();
    assert.deepEqual(asked, ["ok"]);
  } finally { prompt.hidden = true; Popup.closed(prompt); }
});
const NGUI_rect = (el) => ["left", "top", "width", "height"].map((k) => parseFloat(el.style[k]));

// UI-34: Escape in a list popup's search box closes the search and the popup stays, as the roster's does
test("Escape in a list popup's search closes the search first; the next one closes the popup", async (t) => {
  const NGUI = await import("../../src/bmpt/web/js/ngui.js");
  const collection = n("UICharacterCollection", [0, 0]);
  const base = "Inventory_ListControl/RightCanvas/ListBG";
  insert(collection, "SeletControl/TopMenu", structuredClone(NGUI.find(switchLayouts.inventory, `${base}/BG_Top_Widget`)));
  insert(collection, "SeletControl/CharacterSearch", structuredClone(NGUI.find(switchLayouts.inventory, `${base}/Inbox/SearchInfo`)));
  const fetch = globalThis.fetch;
  t.mock.method(globalThis, "fetch", async (url, options) => url === "/asset/ui/layout/UICharacterCollection.json" ? reply(collection) : fetch(url, options));
  const Fresh = await import("../../src/bmpt/web/js/popup.js?escape-search");
  const items = Array.from({ length: 4 }, (_, i) => ({ key: String(i), label: `Owner ${i}`, full: `Owner ${i}` }));
  const closes = [];
  const options = { host: prompt, title: "Students", items, selected: "0", tools: {}, okText: "Apply",
    onClose: (how) => { closes.push(how); prompt.hidden = true; Fresh.closed(prompt); } };
  try {
    await Fresh.listPopup(options);
    action(prompt, "search").click();
    await until(() => walk(prompt).some((e) => e.className === "roster-search"));
    const input = () => walk(prompt).find((e) => e.className === "roster-search");
    input().value = "Owner 1";
    for (const fn of input().listeners.input || []) fn({ isComposing: false });
    await until(() => input()?.value === "Owner 1");
    await tick();
    // the key that ends an input method's conversion is not Escape for the search
    const ending = { key: "Escape", isComposing: false, keyCode: 229, defaultPrevented: false, preventDefault() { this.defaultPrevented = true; } };
    for (const fn of input().listeners.keydown || []) fn(ending);
    assert.equal(ending.defaultPrevented, false);
    // the box gets the key before the window's listeners (the popup stack's Escape)
    const ev = { key: "Escape", defaultPrevented: false, preventDefault() { this.defaultPrevented = true; } };
    for (const fn of input().listeners.keydown || []) fn(ev);
    for (const fn of [...keyListeners]) fn(ev);
    assert.ok(ev.defaultPrevented);
    assert.deepEqual(closes, [], "the popup stays");
    await until(() => !input());
    assert.ok(action(prompt, "search"), "the search is closed, its toolbar button is back");
    assert.equal(prompt.hidden, false);
    await tick();
    press("Escape");
    assert.deepEqual(closes, ["escape"]);
    // opened again, the search starts empty
    await Fresh.listPopup(options);
    action(prompt, "search").click();
    await until(() => !!input());
    assert.equal(input().value, "");
  } finally { prompt.hidden = true; Fresh.closed(prompt); }
});

test("a search that matches nothing blocks the primary button until the match is back", async (t) => {
  const NGUI = await import("../../src/bmpt/web/js/ngui.js");
  const collection = n("UICharacterCollection", [0, 0]);
  const base = "Inventory_ListControl/RightCanvas/ListBG";
  insert(collection, "SeletControl/TopMenu", structuredClone(NGUI.find(switchLayouts.inventory, `${base}/BG_Top_Widget`)));
  insert(collection, "SeletControl/CharacterSearch", structuredClone(NGUI.find(switchLayouts.inventory, `${base}/Inbox/SearchInfo`)));
  insert(collection, "SeletControl/List/Scroll Bar", n("Scroll Bar", [0, 0], [], ["Background", "Foreground"].map((name) =>
    n(name, [1391, 0], [{ texture: "Scroll", w_: 126, h_: 990, pivot: 4, color: "#92a6b7/1.00" }]))));
  const fetch = globalThis.fetch;
  t.mock.method(globalThis, "fetch", async (url, options) => url === "/asset/ui/layout/UICharacterCollection.json" ? reply(collection) : fetch(url, options));
  const Fresh = await import("../../src/bmpt/web/js/popup.js?empty-search");
  const items = Array.from({ length: 4 }, (_, i) => ({ key: String(i), label: `Owner ${i}`, full: `Owner ${i}` }));
  const asked = [];
  try {
    await Fresh.listPopup({ host: prompt, layoutKind: "spr", title: "Portraits", items, selected: "3", tools: {}, okText: "Apply", onOk: () => asked.push("ok") });
    action(prompt, "search").click();
    await until(() => walk(prompt).some((e) => e.className === "roster-search"));
    const type = async (value) => {
      const input = walk(prompt).find((e) => e.className === "roster-search");
      input.value = value;
      for (const fn of input.listeners.input || []) fn({ isComposing: false });
      await until(() => walk(prompt).find((e) => e.className === "roster-search")?.value === value);
      await tick();
    };
    await type("zzz");
    await until(() => walk(prompt).some((e) => e.dataset.path === "Empty"));
    const empty = walk(prompt).find((e) => e.dataset.path === "Empty");
    assert.equal(empty.textContent, english.hall.noCandidates);
    assert.equal(action(prompt, "ok").disabled, true);
    action(prompt, "ok").click();
    assert.deepEqual(asked, []);
    // the paper is lowered with the search field open: the message is centred on what shows of it
    const area = walk(prompt).find((e) => e.className === "popup-cards");
    assert.ok(area, "the scrolling area is still drawn");
    // a match that does not include the chosen card keeps Apply blocked; one that does frees it
    await type("Owner 0");
    await until(() => !walk(prompt).some((e) => e.dataset.path === "Empty"));
    assert.equal(action(prompt, "ok").disabled, true, "card 3 is still hidden by the search");
    await type("Owner 3");
    await until(() => action(prompt, "ok") && !action(prompt, "ok").disabled);
    action(prompt, "ok").click();
    assert.deepEqual(asked, ["ok"]);
  } finally { prompt.hidden = true; Fresh.closed(prompt); }
});

// --- UI-13: the asset consent's Agree and Decline are named by their captions ---
test("the asset consent names Agree and Decline with their captions in every language", async () => {
  const Consent = await import("../../src/bmpt/web/js/consent.js");
  try {
    for (const locale of ["en-US", "zh-CN", "zh-TW", "ja-JP", "ko-KR", "th-TH"]) {
      const ui = JSON.parse(readFileSync(new URL(`../../src/bmpt/locales/${locale}.json`, import.meta.url), "utf8")).webui;
      setStrings(locale, Object.fromEntries(strings(ui)));
      const asked = Consent.show(consent, "/cache", false);
      await until(() => walk(consent).some((e) => e.tagName === "BUTTON"));
      const buttons = walk(consent).filter((e) => e.tagName === "BUTTON");
      // the popup's order: Decline (Btn_Cancel) first, then Agree
      assert.deepEqual(buttons.map((b) => b.getAttribute("aria-label")), [ui.consent.no, ui.consent.yes], locale);
      buttons[1].click();
      assert.equal(await asked, true);
      assert.equal(consent.hidden, true);
    }
  } finally { setStrings("en-US", Object.fromEntries(strings(english))); consent.hidden = true; }
});

// --- UI-36: a failed asset consent shows its error and can be answered again; a repeat click sends nothing ---
test("a failed asset consent shows the error, keeps the popup to retry, and a repeat click sends nothing", async () => {
  const Consent = await import("../../src/bmpt/web/js/consent.js");
  setStrings("en-US", Object.fromEntries(strings(english)));
  const sent = [];
  let answer = null;
  handlers.set("app.consent", (params) => { sent.push(params); return new Promise((resolve) => { answer = resolve; }); });
  try {
    const asked = Consent.show(consent, "/cache", false);
    await until(() => walk(consent).some((e) => e.tagName === "BUTTON"));
    const agree = () => walk(consent).filter((e) => e.tagName === "BUTTON")[1];
    agree().click(); agree().click(); await tick();
    assert.deepEqual(sent, [{ granted: true }], "one request while the first is answered");
    answer({ error: { code: "data_dir_readonly", params: { path: "/cache" } } });
    const shown = english.error.data_dir_readonly.replace("{path}", "/cache");
    await until(() => walk(dialog).some((e) => e.textContent === shown));
    assert.equal(await state(asked), "pending");
    assert.equal(consent.hidden, false, "the consent stays up to answer again");
    (action(dialog, "ok") || action(dialog, "cancel")).click();
    await until(() => dialog.hidden);
    agree().click(); await tick();
    assert.equal(sent.length, 2, "after the failure another answer is sent");
    answer({ result: null });
    assert.equal(await asked, true);
    assert.equal(consent.hidden, true);
  } finally { handlers.delete("app.consent"); consent.hidden = true; Popup.closed(consent); }
});

// --- UI-14: the Settings sliders are range inputs over the game's slider ---
const sliderOf = (name) => walk(settings).find((e) => e.tagName === "INPUT" && e.attrs["aria-label"] === name);
async function openSound(prefs = {}, tabName = "sound") {
  const saved = [], state = { version: "1.0.0", language: "en-US", languages: [{ code: "en-US", name: "English" }], voiceVolume: 80, voiceMuted: false,
    lobbyZoom: { mode: "custom", custom: 2 }, ...prefs };
  handlers.set("settings.get", () => ({ result: state }));
  handlers.set("mods.state", () => ({ result: {} }));
  handlers.set("settings.set", ({ key, value }) => { saved.push([key, value]); Object.assign(state, { [key]: value }); return { result: state }; });
  const Settings = await import("../../src/bmpt/web/js/settings.js");
  await Settings.open(null, tabName);
  return { saved, state, Settings };
}

test("the Settings sound slider is a range input over the game's slider, named by its row, with the saved value", async () => {
  const { Settings } = await openSound();
  try {
    assert.equal(walk(settings).filter((e) => /slider-hit/.test(e.className)).length, 0, "no scripted pointer area is left");
    const range = sliderOf(english.settings.voice);
    assert.ok(range, "the Voice row's slider");
    assert.deepEqual([range.type, range.min, range.max, range.step, range.value], ["range", "0", "100", "1", "80"]);
    assert.match(range.className, /\bhit\b/); assert.match(range.className, /\bslider-range\b/);
    // geometry: its native thumb's centre travels the 632 units the drawn thumb does, round the bar's centre
    const bar = walk(settings).find((e) => e.dataset.path === "Rows/Voice/SlideBg"), [bl, bt, bw, bh] = NGUI_rect(bar);
    const left = parseFloat(range.style.left), width = parseFloat(range.style.width);
    slack(left + width / 2, bl + bw / 2); slack(width, 632 * S + Settings.SLIDER_THUMB);
    slack(parseFloat(range.style.top), bt - 20); slack(parseFloat(range.style.height), bh + 40);
    assert.ok(left <= bl + 1 && left + width >= bl + bw - 1, "and the hit area covers the whole bar");
    const thumb = walk(settings).find((e) => e.dataset.path === "Rows/Voice/SlideBg/Thumb");
    slack(parseFloat(thumb.style.left) + parseFloat(thumb.style.width) / 2, bl + bw / 2 + (-316 + 632 * 0.8) * S);
  } finally { press("Escape"); handlers.clear(); }
});

const slack = (a, b, eps = 0.02) => assert.ok(Math.abs(a - b) < eps, `${a} != ${b}`);

test("a slider moves the game's thumb while it changes and saves when it commits, by pointer or key", async () => {
  const { saved } = await openSound();
  try {
    const range = () => sliderOf(english.settings.voice), thumb = () => walk(settings).find((e) => e.dataset.path === "Rows/Voice/SlideBg/Thumb");
    const start = parseFloat(thumb().style.left);
    // a drag: the thumb follows, nothing is saved yet
    range().value = "50"; fire(range(), "input");
    slack(parseFloat(thumb().style.left), start + (632 * (0.5 - 0.8)) * S);
    range().value = "20"; fire(range(), "input");
    slack(parseFloat(thumb().style.left), start + (632 * (0.2 - 0.8)) * S); assert.deepEqual(saved, []);
    // the release commits
    fire(range(), "change"); await tick();
    assert.deepEqual(saved, [["voiceVolume", 20]]);
    // a key press: input and change together, one step
    range().value = "21"; fire(range(), "input"); fire(range(), "change"); await tick();
    assert.deepEqual(saved.at(-1), ["voiceVolume", 21]);
    // the page is drawn again with the saved value
    assert.equal(range().value, "21");
  } finally { press("Escape"); handlers.clear(); }
});

test("a cancelled drag puts the thumb and the input back and saves nothing", async () => {
  const { saved } = await openSound();
  try {
    const range = () => sliderOf(english.settings.voice), thumb = () => walk(settings).find((e) => e.dataset.path === "Rows/Voice/SlideBg/Thumb");
    const start = parseFloat(thumb().style.left);
    range().value = "5"; fire(range(), "input");
    assert.ok(parseFloat(thumb().style.left) < start);
    fire(range(), "pointercancel");
    assert.equal(range().value, "80"); slack(parseFloat(thumb().style.left), start);
    await tick(); assert.deepEqual(saved, [], "nothing was committed");
    // after a commit the cancelled drag goes back to that value
    range().value = "60"; fire(range(), "input"); fire(range(), "change"); await tick();
    range().value = "10"; fire(range(), "input"); fire(range(), "pointercancel");
    assert.equal(range().value, "60");
  } finally { press("Escape"); handlers.clear(); }
});

test("keyboard focus shows a ring on the game's thumb, the pointer's focus does not, and the focus survives the redraw a save causes", async () => {
  await openSound();
  try {
    const range = () => sliderOf(english.settings.voice), thumb = () => walk(settings).find((e) => e.dataset.path === "Rows/Voice/SlideBg/Thumb");
    assert.ok(!thumb().className.split(" ").includes("slider-thumb-focus"));
    range().matches = () => false; fire(range(), "focus");
    assert.ok(!thumb().className.split(" ").includes("slider-thumb-focus"), "a pointer's focus draws no ring");
    range().matches = (selector) => selector === ":focus-visible"; fire(range(), "focus");
    assert.ok(thumb().className.split(" ").includes("slider-thumb-focus"), "a key's focus draws the ring on the thumb");
    fire(range(), "blur"); assert.ok(!thumb().className.split(" ").includes("slider-thumb-focus"));
    // a key press saves and the page is drawn again: the new input has the focus
    range().focus(); const before = range();
    range().value = "81"; fire(range(), "input"); fire(range(), "change"); await tick();
    for (let i = 0; i < 20 && range() === before; i++) await tick();
    assert.notEqual(range(), before, "the page was drawn again");
    assert.equal(document.activeElement, range(), "and the slider kept the focus");
    // the ring is a class of the stylesheet, in the app's focus blue
    const css = readFileSync(new URL("../../src/bmpt/web/css/app.css", import.meta.url), "utf8");
    // one focus rule for every control (the slider's thumb among them): 4 px, 2 px off, on the Raius10px corner
    assert.match(css, /\.slider-thumb-focus,[^{]*\{[^}]*outline:\s*4px solid #2fa6f0;\s*outline-offset:\s*2px;\s*border-radius:\s*6\.49px/);
    assert.match(css, /\.hit\.slider-range\s*\{[^}]*opacity:\s*0/);
    assert.match(css, /\.slider-range::-webkit-slider-thumb\s*\{[^}]*width:\s*40px/);
  } finally { press("Escape"); handlers.clear(); }
});

test("the custom zoom-out limit's slider is a range input with the value as its text", async () => {
  const { saved } = await openSound({}, "l2d");
  try {
    const range = sliderOf(english.settings.zoomOut.limit);
    assert.ok(range, "the limit row's slider");
    assert.deepEqual([range.min, range.max, range.value], ["0", "30", "10"]);
    assert.equal(range.attrs["aria-valuetext"], english.settings.zoomOut.factor.replace("{v}", "2.0"));
    range.value = "25"; fire(range, "input");
    assert.equal(range.attrs["aria-valuetext"], english.settings.zoomOut.factor.replace("{v}", "3.5"));
    const label = walk(settings).find((e) => e.dataset.path === "Rows/ZoomCustom/Label_Mute");
    assert.equal(label.textContent, english.settings.zoomOut.factor.replace("{v}", "3.5"), "the value label follows too");
    fire(range, "change"); await tick();
    assert.deepEqual(saved, [["lobbyZoomCustom", 3.5]]);
  } finally { press("Escape"); handlers.clear(); }
});

// --- design-system popup anatomy, independent of the Sort prefab's original dimensions ---
const Tokens = await import("../../src/bmpt/web/js/ui-tokens.js");
const Frames = await import("../../src/bmpt/web/js/popup-frames.js");
const { FOOTER, POPUP, POPUP_FRAME, CONTROL, SPACE, TYPE } = Tokens;
const STAGE_HALF = 540 / S;
const unitsOf = (el) => {
  const [top, height, left, width] = ["top", "height", "left", "width"].map((key) => parseFloat(el.style[key]));
  return { cx: (left + width / 2 - 960) / S, cy: (540 - (top + height / 2)) / S, w: width / S, h: height / S,
    top: (540 - top) / S, bottom: (540 - top - height) / S };
};
const measure = (root, path) => unitsOf(walk(root).find((e) => e.dataset.path === path));
const near2 = (a, b, eps = 0.02) => assert.ok(Math.abs(a - b) < eps, `${a} != ${b}`);

for (const lang of ["en-US", "zh-CN"]) test(`each popup class positions its frame, title, close and centred footer in ${lang}`, async () => {
  const NGUI = await import("../../src/bmpt/web/js/ngui.js");
  await Popup.ready();
  const ui = JSON.parse(readFileSync(new URL(`../../src/bmpt/locales/${lang}.json`, import.meta.url))).webui;
  try {
    setStrings(lang, Object.fromEntries(strings(ui))); document.documentElement.lang = lang;
    for (const key of ["S", "M", "L", "XL"]) for (const requestedH of [0, 1400, 2000]) for (const n of [1, 2, 3]) {
      const f = Frames.frameGeometry(key, requestedH), actions = Array.from({ length: n }, (_, i) => ({ label: ui.picker.ok, name: `B${i}` }));
      const tree = Popup.popupFrame(ui.quit.title, key, requestedH, { buttons: actions });
      const bg = NGUI.find(tree, "Popup/Base/BG"), title = NGUI.find(bg, "Label_Title_Locale"), close = NGUI.find(bg, "Btn_Close");
      assert.deepEqual([bg.widgets[0].w_, bg.widgets[0].h_], [f.w, f.h]);
      assert.equal(bg.widgets[0].sprite, "Common_Popup_Bg");
      near2(f.h / 2 - title.pos[1] - title.widgets[0].h_ / 2, 36);
      assert.equal(title.pos[0], 0); assert.ok(title.widgets[0].fontSize >= 48 && title.widgets[0].fontSize <= 72);
      assert.ok(!title.widgets[0].text.includes("\n"));
      near2(f.w / 2 - close.pos[0] - 30, 48); near2(f.h / 2 - close.pos[1] - 30, 39);
      const btn = NGUI.find(tree, "Popup/Btn"), widths = btn.children.map(b => NGUI.find(b, "BG (1)").widgets[0].w_);
      assert.equal(new Set(widths).size, 1);
      assert.equal(new Set(btn.children.map(b => NGUI.find(b, "Label_Locale").widgets[0].fontSize)).size, 1);
      near2(btn.pos[1] - CONTROL.buttonM.h / 2 - (Popup.FRAME_Y - f.h / 2), 102);
      for (let i = 0; i < n; i++) {
        const body = NGUI.find(btn.children[i], "BG (1)");
        assert.deepEqual(body.pos, [0, 0]); assert.equal(body.widgets[0].h_, CONTROL.buttonM.h);
        const caption = NGUI.find(btn.children[i], "Label_Locale").widgets[0];
        assert.equal(caption.w_, widths[i] - 2 * CONTROL.buttonM.pad); assert.equal(caption.h_, Popup.FOOTER.M.caption.h);
        if (i) near2(btn.children[i].pos[0] - btn.children[i-1].pos[0] - widths[i], 33);
      }
      near2(btn.children[0].pos[0] + btn.children.at(-1).pos[0], 0);
      // the default footer's bodies are as wide as the captions need (Confirm in this language), a row of three is scaled to the frame
      assert.equal(widths[0], Popup.footerLayout(actions.map((a) => a.label), f.w).bodyW);
      assert.ok(widths[0] >= CONTROL.buttonM.minW && widths[0] <= CONTROL.buttonL.minW);
      if (n === 3) near2(btn.children[0].pos[0] - widths[0] / 2 + f.w / 2, Math.max(82.5, (f.w - n * widths[0] - 66) / 2));
      const box = Frames.popupContent(f.w, f.h);
      assert.deepEqual([box.x, box.y, box.w], [40, 170, f.w - 80]);
      near2(Popup.FRAME_Y + f.h / 2 - box.y - box.h, Popup.footerTop(f.h) + 20);
      const text = Frames.popupContent(f.w, f.h, { text: true }); assert.deepEqual([text.x, text.w], [100, f.w - 200]);
    }
  } finally { setStrings("en-US", Object.fromEntries(strings(english))); document.documentElement.lang = "en-US"; }
});

test("titleCentre puts the title's ink centre that far under the frame's top and leaves the shared title top without it", async () => {
  const NGUI = await import("../../src/bmpt/web/js/ngui.js"); await Popup.ready();
  const at = (options) => NGUI.find(Popup.popupFrame("Title", 2098, 1300, options), "Popup/Base/BG/Label_Title_Locale").pos[1];
  const shared = at({}), centred = at({ titleCentre: 66 }), lower = at({ titleCentre: 90 });
  assert.equal(at({ titleCentre: null }), shared);
  near2(centred - lower, 24);
  assert.ok(centred > shared, "the centred title stands above the shared one (y grows upwards)");
});

test("numeric caller widths round up and every fixed popup chooses its assigned class", async () => {
  const NGUI = await import("../../src/bmpt/web/js/ngui.js"); await Popup.ready();
  for (const [width, expected] of [[700,1300],[1301,1540],[1541,2098],[2100,2841],[2458,2841]])
    assert.equal(NGUI.find(Popup.popupFrame("T", width, 1), "Popup/Base/BG").widgets[0].w_, expected);
  assert.equal(Popup.messageLayout({ lines: ["Short"] }).width, 1300);
  assert.equal(Popup.messageLayout({ width: 2100 }).width, 2098);
  assert.equal(Popup.messageLayout({ width: 2101 }).width, 2841);
  const candidates = Array.from({ length: 100 }, (_, n) => "word ".repeat(n + 1));
  for (const width of [1300,1540,2098]) assert.ok(candidates.some(text => Popup.messageLayout({ lines: [text] }).width === width));
  assert.equal(Frames.TEXT_VIEWER.w, 2841); assert.equal(Popup.listLayout().frame.w, 2841);
  assert.equal(Frames.DETAILS.w, 2098); assert.equal(Frames.EXPORT.w, 2098);
  assert.equal(Frames.MOD_ALIAS.w, 1540); assert.equal(Frames.ALIAS.w, 1540);
  assert.equal(Frames.compareFrame(false).w, 2098); assert.equal(Frames.compareFrame(true).w, 2098);
  for (let n = 1; n <= Frames.ALIAS.max; n++) {
    const f = Frames.aliasFrame(n); assert.ok(f.h >= 786 && f.h <= 1560); assert.ok(f.bottom >= Popup.footerTop(f.h) + 20);
  }
  for (const body of [0, 300, 2000]) { const f = Frames.detailsFrame(body); assert.ok(f.bottom >= Popup.footerTop(f.h) + 20); }
  for (const f of [Frames.TEXT_VIEWER, Frames.MOD_ALIAS, Frames.EXPORT, Frames.compareFrame(true)]) assert.ok(f.bottom >= Popup.footerTop(f.h) + 20);
});

for (const lang of ["en-US", "zh-CN"]) test(`list panels, check row and detail bands remain inside their content box in ${lang}`, async () => {
  const NGUI = await import("../../src/bmpt/web/js/ngui.js"); await Popup.ready();
  const layout = Popup.listLayout(true), { content, frame } = layout;
  near2(layout.left.l, -frame.w / 2 + 40); near2(layout.list.r, frame.w / 2 - 40);
  near2(layout.list.l - layout.left.r, 40); near2(frame.cy + frame.h / 2 - layout.left.t, 170);
  near2(layout.left.b - (Popup.footerTop(frame.h) + 20), 124);
  const tree = Popup.detailsTree(items[0], "Details", [["File", "one.bundle"]], [["Open", () => {}]]);
  const picture = NGUI.find(tree, "Picture"), name = NGUI.find(tree, "Name"), chip = NGUI.find(tree, "Chip_0");
  assert.ok(picture.pos[1] - 230 - name.pos[1] - name.widgets[0].h_ / 2 >= 20 - 1e-9);
  assert.ok(name.pos[1] - name.widgets[0].h_ / 2 - chip.pos[1] - 36 >= 20 - 1e-9);
});

// BL-133: a 3D model's details fit the list popup's panel without scrolling: the picture beside the name and chips,
// inside its column with its incline, one size for every candidate, big enough to read with a long file name and six
// actions, and the actions on the panel's bottom
test("a 3D model's details fit the panel without scrolling", async () => {
  const NGUI = await import("../../src/bmpt/web/js/ngui.js"); await Popup.ready();
  const panel = Popup.listLayout().left, height = panel.t - panel.b, width = panel.r - panel.l;
  const file = "assets-_mx-characters-hina_original-_mxdependency-2024-11-18_000_assets_all_3164161564.bundle";
  const fields = [["File", file], ["Size", "7.2 MB"], ["Status", "Applied"], ["CRC", "Original"]];
  const actions = ["Apply", "Show in Finder", "Check Compatibility", "Update", "Optimize Skirt", "Delete"].map((c) => [c, () => {}]);
  const it = { ...items[0], full: "Hina (Pajamas) - Optimized", nameEdit: { label: "Alias", fn: () => {} } };
  const sizes = new Set(), bottoms = new Set();
  for (const n of [3, 4, 6]) for (const rows of [fields, fields.map(([c]) => [c, "-"])]) {
    const tree = Popup.detailsTree(it, "3D Model Info", rows, actions.slice(0, n), null, "model", panel);
    assert.ok(tree.contentHeight <= height + 1e-6, `${n} actions: ${tree.contentHeight} > ${height}`);
    sizes.add(NGUI.find(tree, "Picture").widgets[0].w_.toFixed(6));
    bottoms.add(NGUI.find(tree, "Act_0").pos[1].toFixed(6));
    const picture = NGUI.find(tree, "Picture"), size = picture.widgets[0].w_, name = NGUI.find(tree, "Name");
    assert.equal(picture.widgets[0].h_, size);
    assert.ok(size >= 150, `picture ${size}`);
    const TAN10 = Math.tan(Math.PI / 18), left = picture.pos[0] - size * (1 + TAN10) / 2, centre = (panel.l + panel.r) / 2;
    assert.ok(left >= centre - width / 2 - 1e-6 && picture.pos[0] + size * (1 + TAN10) / 2 <= name.pos[0], `picture ${left}`);
  }
  // one picture size for every candidate, the actions on the panel's bottom whatever the fields hold
  assert.equal(sizes.size, 1, [...sizes].join(", "));
  assert.equal(bottoms.size, 1, [...bottoms].join(", "));
});

// --- design tokens, item C: one backdrop, the prefab's resting 0.80 (BlackBgClickArea's TweenAlpha runs 0 to 0.80, not the sprite's 0.94) ---
test("every popup frame draws the one backdrop token", async () => {
  const NGUI = await import("../../src/bmpt/web/js/ngui.js");
  await Popup.ready();
  assert.equal(Tokens.BACKDROP, "#000000/0.80");
  const colour = (tree) => NGUI.find(tree, "BlackBg/BlackBgClickArea").widgets.find((w) => "sprite" in w).color;
  for (const [w, h] of [[1500, 680], [1900, 1180], [2458, 1400]]) assert.equal(colour(Popup.popupFrame("T", w, h, { ok: "OK" })), Tokens.BACKDROP);
  // the consent and update dialogs are popupFrame's now; the list popup, the display settings and Settings set the same token
  for (const file of ["popup.js", "display.js", "settings.js"]) {
    const source = readFileSync(new URL(`../../src/bmpt/web/js/${file}`, import.meta.url), "utf8");
    const sites = source.match(/BlackBgClickArea"\)\.widgets\.find\(\(\w+\) => "sprite" in \w+\)\.color = [^;]+;/g) || [];
    assert.ok(sites.length >= 1, file);
    assert.ok(sites.every((site) => site.endsWith("= BACKDROP;")), `${file}: ${sites}`);
  }
  for (const file of ["consent.js", "update.js"]) {
    const source = readFileSync(new URL(`../../src/bmpt/web/js/${file}`, import.meta.url), "utf8");
    assert.doesNotMatch(source, /BlackBgClickArea/, `${file} draws no frame of its own`);
  }
});

// --- design tokens, item D: one small button (XS: 308 x 104, caption 40 down to 28) ---
const LOCALE_LIST = ["en-US", "zh-CN", "zh-TW", "ja-JP", "ko-KR", "th-TH"];
const localeWebui = (lang) => JSON.parse(readFileSync(new URL(`../../src/bmpt/locales/${lang}.json`, import.meta.url))).webui;
const byDots = (ui, key) => key.split(".").reduce((o, part) => o[part], ui);

for (const lang of LOCALE_LIST) test(`message line buttons are 104 high, as wide as their caption and 68 (200 at least), and their rows hold them in ${lang}`, async () => {
  const NGUI = await import("../../src/bmpt/web/js/ngui.js");
  await Popup.ready();
  const ui = localeWebui(lang);
  try {
    setStrings(lang, Object.fromEntries(strings(ui)));
    document.documentElement.lang = lang;
    for (const key of ["tour.open", "recovery.report", "settings.mods.check", "popup.apply", "btn.delete", "update.releasePage", "settings.update.check"]) {
      const caption = byDots(ui, key);
      for (const plain of [false, true]) for (const width of [1700, 2100, 2458]) {
        const lines = [{ text: "A line of text without a button" }, { text: "Mods: item one, item two and item three", button: { label: caption, fn() {} } }, { text: "Another plain line" }];
        const { body } = Popup.messageLayout({ lines, width, ok: "OK", plain });
        const button = NGUI.find(body.tree, "Btn_1"), bg = button.widgets.find((w) => w.sprite === "Common_Btn_BG"), text = NGUI.find(body.tree, `${plain ? "Line" : "Row"}_1`);
        const label = NGUI.find(button, "Label_Locale").widgets[0];
        const fit = NGUI.fitText(caption, 40, 28, 308 - 46);
        assert.equal(bg.h_, 104, `${lang} ${key}`);
        assert.equal(bg.w_, Math.max(200, NGUI.glyphWidth(fit.text, fit.fs) + 68), `${lang} ${key}: the caption's width and 68, 200 at least`);
        assert.ok(label.fontSize <= 40 && label.fontSize >= 28 - 8, `${lang} ${key}: ${label.fontSize}`);
        for (const line of label.text.split("\n")) assert.ok(NGUI.glyphWidth(line, label.fontSize) <= label.w_, `${lang} ${key}: inside its label box`);
        assert.ok(label.text.split("\n").length * label.fontSize * 1.15 * NGUI.fontSizeFor(label.text, 1) <= label.h_, `${lang} ${key}: the lines fit the box's height`);
        // the text keeps clear of the button
        const t = text.widgets[0], textRight = text.pos[0] + t.w_, buttonLeft = button.pos[0] - bg.w_ / 2;
        assert.ok(textRight + 20 <= buttonLeft + 1e-9, `${lang} ${key} ${width}: text ends ${textRight}, the button starts ${buttonLeft}`);
        assert.ok(button.pos[0] + bg.w_ / 2 <= Popup.messageLayout({lines,width,plain}).width / 2 - 100, "inside the text content box");
        // a row with a button holds it (124 in the light box); rows without keep the box's minimum (64)
        const rows = (tree) => ["Row_0", "Row_1", "Row_2"].map((name) => NGUI.find(tree, name)).filter(Boolean);
        if (!plain) {
          const [r0, r1, r2] = rows(body.tree);
          assert.equal(r0.pos[1] - r1.pos[1], (64 + 124) / 2, `${lang}: row heights 64, 124`);
          assert.equal(r1.pos[1] - r2.pos[1], (124 + 64) / 2);
        }
      }
    }
  } finally { setStrings("en-US", Object.fromEntries(strings(english))); document.documentElement.lang = "en-US"; }
});

test("value rows lay their 308 x 104 buttons right-aligned 328 apart in the card's content box and wrap to a second line, never shrinking one", async () => {
  const Rows = await import("../../src/bmpt/web/js/settings-rows.js");
  const { XS_BUTTON: B, SPACE, PANEL } = Tokens;
  // the content box: the 1552-wide card less its padding (58 each side), SPACE.m under the dotted line (109 down), SPACE.l above the foot
  const left = -1552 / 2 + PANEL.padCard, right = 1552 / 2 - PANEL.padCard, top = 109 + SPACE.m;
  // beside the value: three buttons to a line (the value keeps its four grid columns, 452); under it: four (the line is 1436 wide)
  assert.deepEqual([0, 1, 2, 3, 4, 5, 6, 7].map((n) => Rows.buttonLines(n)), [[], [1], [2], [3], [3, 1], [3, 2], [3, 3], [3, 3, 1]]);
  assert.deepEqual([0, 1, 2, 3, 4, 5, 6, 7].map((n) => Rows.buttonLines(n, true)), [[], [1], [2], [3], [4], [4, 1], [4, 2], [4, 3]]);
  assert.equal(Rows.valueRoom(0), right - left, "a value alone takes the content box");
  for (const below of [false, true]) for (let n = 0; n <= 7; n++) for (const textH of [0, 49.4, 98.8, 148.2, 300]) {
    const layout = Rows.valueGeometry(n, textH, { below }), room = Rows.valueRoom(n, below);
    assert.equal(layout.xs.length, n);
    assert.equal(layout.room, room, "the value's room is what valueRoom says");
    // right-aligned to the content box's right edge, 328 apart on each line
    let at = 0;
    layout.lines.forEach((count, line) => {
      near2(layout.xs[at + count - 1] + B.w / 2, right);
      for (let i = 1; i < count; i++) near2(layout.xs[at + i] - layout.xs[at + i - 1], B.pitch);
      at += count;
      // a line sits beside the value only when the value keeps its four columns, SPACE.m clear of the buttons
      if (!below && line === 0) assert.ok(room >= 452 && left + room + SPACE.m <= layout.xs[0] - B.w / 2 + 1e-9, `${n}: the value keeps ${room}`);
      if (!below) assert.ok(layout.xs[at - count] - B.w / 2 >= layout.xs[0] - B.w / 2 - 1e-9, "a later line starts no further left than the first");
    });
    // every button inside the row, 104 high, in the content box: under its top and above the row's foot
    for (let line = 0; line < layout.lines.length; line++) {
      assert.ok(layout.ys[line] + B.h / 2 <= layout.h / 2 - top + 1e-9, `${n} buttons, below ${below}: under the dotted line`);
      assert.ok(layout.ys[line] - B.h / 2 >= -layout.h / 2 + SPACE.l - 1e-9, `${n} buttons, below ${below}: above the row's foot`);
      if (line) assert.ok(layout.ys[line - 1] - layout.ys[line] >= B.h + B.gap - 1e-9, "lines of buttons are SPACE.m apart at least");
    }
    // the value starts on the content box's left edge, its centre on the block's
    near2(layout.value[0], left); near2(layout.value[1], layout.midY);
    assert.ok(layout.midY + layout.blockH / 2 <= layout.h / 2 - top + 1e-9);
    if (!below && n) {
      // the first line is centred on the value, which sits under the dotted line
      near2(layout.ys[0], layout.midY);
      assert.ok(layout.blockH >= B.h);
    }
    if (below && n) near2(layout.midY - layout.blockH / 2 - (layout.ys[0] + B.h / 2), SPACE.l);
    // the card is exactly its content: SPACE.l under the last line
    const lowest = Math.min(layout.midY - layout.blockH / 2, ...layout.ys.slice(below ? 0 : 1).map((y) => y - B.h / 2));
    near2(lowest + layout.h / 2, SPACE.l);
  }
});

for (const lang of LOCALE_LIST) test(`Settings tabs keep every value row's buttons inside the row and clear of the value, and every row reachable in ${lang}`, async () => {
  const NGUI = await import("../../src/bmpt/web/js/ngui.js");
  const ui = localeWebui(lang);
  const prefs = (extra = {}) => ({ version: "0.6.0-alpha.10", language: lang, languages: [{ code: lang, name: ui.languageName }], native: true, traySupported: true,
    logPath: "/Users/someone/Library/Application Support/bluearchive-modding-patching-tool/logs/session.log",
    cacheDir: "/Users/someone/Library/Application Support/bluearchive-modding-patching-tool/cache", consent: true, previewCache: true, startupUpdate: true,
    prereleasePreviews: true, gameDirs: { global: "/Applications/Blue Archive/Game/Contents/Global", japan: "/Applications/Blue Archive/Game/Contents/Japan" },
    libraryDir: "/Users/someone/Library/Application Support/bluearchive-modding-patching-tool/library", outputDir: "/Users/someone/Documents/BA output", ...extra });
  handlers.set("settings.get", () => ({ result: prefs() }));
  handlers.set("mods.state", () => ({ result: {} }));
  handlers.set("mods.sizes", () => ({ result: { builtSize: 1048576 } }));
  handlers.set("mods.other", () => ({ result: [{ id: "x", label: "Other Pack", target: "t", files: ["a.bundle", "b.bundle"], state: "changed", selected: false }] }));
  handlers.set("app.tours", () => ({ result: { tours: [] } }));
  setStrings(lang, Object.fromEntries(strings(ui)));
  document.documentElement.lang = lang;
  const Settings = await import("../../src/bmpt/web/js/settings.js");
  const rectOf = (el) => NGUI.rect(el);
  const inside = (a, b, eps = 0.05) => a[0] >= b[0] - eps && a[1] >= b[1] - eps && a[0] + a[2] <= b[0] + b[2] + eps && a[1] + a[3] <= b[1] + b[3] + eps;
  const apart = (a, b) => a[0] + a[2] <= b[0] + 0.05 || b[0] + b[2] <= a[0] + 0.05 || a[1] + a[3] <= b[1] + 0.05 || b[1] + b[3] <= a[1] + 0.05;
  try {
    for (const tabName of ["general", "mods", "folders"]) {
      await Settings.open(null, tabName);
      for (let i = 0; i < 40 && !walk(settings).some((e) => /^Rows\/[^/]+\/Btn_0$/.test(e.dataset.path || "")); i++) await tick();
      const rowEls = walk(settings).filter((e) => /^Rows\/[^/]+$/.test(e.dataset.path || ""));
      assert.ok(rowEls.length >= 3, `${tabName}: rows drawn`);
      const inner = walk(settings).find((e) => e.className === "settings-rows-inner");
      const rows = rowEls.map((el) => ({ el, name: el.dataset.path.slice(5), box: rectOf(el) })).sort((a, b) => a.box[1] - b.box[1]);
      for (const [i, row] of rows.entries()) {
        // the rows stand 12 apart (the prefab's Game tab), none over another
        if (i) near2(row.box[1] - (rows[i - 1].box[1] + rows[i - 1].box[3]), Tokens.SETTINGS.rowGap * S);
        const buttons = walk(settings).filter((e) => new RegExp(`^Rows/${row.name}/Btn_\\d+$`).test(e.dataset.path || ""));
        const value = walk(settings).find((e) => e.dataset.path === `Rows/${row.name}/Value`);
        for (const [j, button] of buttons.entries()) {
          const box = rectOf(button);
          assert.ok(inside(box, row.box), `${tabName} ${row.name} button ${j} inside its row`);
          assert.ok(Math.abs(box[3] - 104 * S) < 0.02, `${tabName} ${row.name}: 104 high`);
          assert.ok(Math.abs(box[2] - (row.name === "Export" ? 520 : 308) * S) < 0.02, `${tabName} ${row.name}: ${box[2] / S} wide`);
          if (value) assert.ok(apart(box, rectOf(value)), `${tabName} ${row.name}: the buttons clear the value`);
          for (const other of buttons.slice(j + 1)) assert.ok(apart(box, rectOf(other)), `${tabName} ${row.name}: the buttons do not overlap`);
          const caption = walk(settings).find((e) => e.dataset.path === `${button.dataset.path}/Label_Locale`);
          const fs = parseFloat(caption.style.fontSize);
          for (const line of caption.textContent.split("\n")) assert.ok(line.length * 0.6 * fs <= box[2] - 24 * S + 0.1, `${tabName} ${row.name}: ${line} inside the button`);
          assert.ok(caption.textContent.split("\n").length * fs * 1.15 <= box[3] - 16 * S + 0.1);
        }
        if (buttons.length > 1) assert.equal(new Set(buttons.map((b) => walk(settings).find((e) => e.dataset.path === `${b.dataset.path}/Label_Locale`).style.fontSize)).size, 1, `${tabName} ${row.name}: one caption size`);
      }
      if (tabName === "general") {
        // the Log row's three buttons: all three beside the value on one line, right-aligned
        const [a, b, c] = [0, 1, 2].map((i) => rectOf(walk(settings).find((e) => e.dataset.path === `Rows/Log/Btn_${i}`)));
        assert.ok(a[1] === b[1] && b[1] === c[1], "three XS buttons fit on the value's line");
        assert.ok(a[0] + a[2] < b[0] && b[0] + b[2] < c[0], "left to right, apart");
      }
      // scrolling reaches the last row: the inner area is as tall as the rows' bottom
      const last = rows.at(-1).box, innerH = parseFloat(inner.style.height);
      assert.ok(last[1] + last[3] <= innerH + 0.05, `${tabName}: the last row ends at ${last[1] + last[3]}, the scrolling area is ${innerH} high`);
      settings.hidden = true; Popup.closed(settings);
    }
  } finally { settings.hidden = true; Popup.closed(settings); handlers.clear(); setStrings("en-US", Object.fromEntries(strings(english))); document.documentElement.lang = "en-US"; }
});

for (const lang of LOCALE_LIST) test(`the asset consent's Change button (104 high) clears the item and path text and the buttons below, in ${lang}`, async () => {
  const NGUI = await import("../../src/bmpt/web/js/ngui.js");
  const Consent = await import("../../src/bmpt/web/js/consent.js");
  const ui = localeWebui(lang);
  await Popup.ready();
  const apartBoxes = (a, b) => a[2] <= b[0] + 1e-9 || b[2] <= a[0] + 1e-9 || a[3] <= b[1] + 1e-9 || b[3] <= a[1] + 1e-9; // [x0, y0, x1, y1]
  try {
    setStrings(lang, Object.fromEntries(strings(ui)));
    document.documentElement.lang = lang;
    for (const path of ["/c", "/Users/someone/Library/Application Support/bluearchive-modding-patching-tool/cache", "/Users/someone/".repeat(30)]) {
      const L = Consent.assetLayout(path), { change } = L;
      const bf = NGUI.fitText(ui.consent.change, 40, 28, 308 - 46);
      assert.equal(change.w, Math.max(200, NGUI.glyphWidth(bf.text, bf.fs) + 68), "the caption's width and 68, 200 at least");
      // the section's own coordinates: x from its centre, y down from its top
      const button = [change.x - change.w / 2, L.rowY - 52, change.x + change.w / 2, L.rowY + 52];
      const item = [L.itemBox[0], L.itemBox[1], L.itemBox[0] + NGUI.glyphWidth(ui.consent.cached.items, 40), L.itemBox[2]];
      const text = [-L.sectionW / 2 + 20, L.pathY - L.pathH / 2, -L.sectionW / 2 + 20 + L.room, L.pathY + L.pathH / 2];
      assert.ok(apartBoxes(button, item), `${lang}: the button clears the item text`);
      assert.ok(apartBoxes(button, text), `${lang}: the button clears the path text`);
      assert.ok(button[2] <= L.sectionW / 2 && button[0] >= -L.sectionW / 2 && button[1] >= -L.cachedH, "the button stays in its section");
      for (const line of L.to.text.split("\n")) assert.ok(NGUI.glyphWidth(line, L.to.fs) <= L.room, `${lang}: the path's lines fit their room`);
      assert.ok(text[1] >= -L.cachedH - 1e-9, "the path text stays in the section");
      // The Note keeps the shared 20-unit footer clearance on the stage.
      assert.ok(L.noteBottom >= Popup.footerTop(L.h) + 20 - 1e-9, `${lang}: ${L.noteBottom} against ${Popup.footerTop(L.h)}`);
      assert.ok(L.h <= Tokens.POPUP.maxH && L.h >= 786);
      assert.ok(L.neverTop - L.neverH >= L.noteBottom + L.note.h + 40 - 1e-9, "the Never section clears the Note");
    }
    // as drawn
    const asked = Consent.show(consent, "/Users/someone/Library/Application Support/bluearchive-modding-patching-tool/cache", true);
    await until(() => walk(consent).some((e) => e.dataset.path === "Center/Popup/Cached/Change"));
    const get = (path) => walk(consent).find((e) => e.dataset.path === `Center/Popup/${path}`);
    const [change, item, path] = ["Cached/Change", "Cached/Item_0", "Cached/Path"].map((name) => NGUI.rect(get(name)));
    const box = ([x, y, w, h]) => [x, y, x + w, y + h];
    assert.ok(apartBoxes(box(change), box(item)) && apartBoxes(box(change), box(path)));
    assert.ok(Math.abs(change[3] - 104 * S) < 0.02);
    walk(consent).find((e) => e.tagName === "BUTTON").click();
    await asked;
  } finally { consent.hidden = true; Popup.closed(consent); setStrings("en-US", Object.fromEntries(strings(english))); document.documentElement.lang = "en-US"; }
});

// --- design tokens, item E: one chip (272 x 72, caption 40 down to 28, 290 apart) in the list popup's details panel ---
test("chipPlaces stands chips in centred rows that fit inside the 850-unit panel, 290 apart or closer", () => {
  const { CHIP } = Tokens;
  const slant = CHIP.h * Math.tan(10 * Math.PI / 180);
  for (const panel of [850, 790]) for (let n = 1; n <= 7; n++) {
    const places = Popup.chipPlaces(n, panel), rows = Math.max(...places.map((p) => p.row)) + 1;
    assert.equal(places.length, n);
    for (let row = 0; row < rows; row++) {
      const xs = places.filter((p) => p.row === row).map((p) => p.x);
      xs.slice(1).forEach((x, i) => {
        const pitch = x - xs[i];
        assert.ok(pitch <= CHIP.pitch + 1e-9 && pitch >= CHIP.w + 8 - 1e-9, `${n} chips in ${panel}: pitch ${pitch}`);
        assert.ok(Math.abs(pitch - (xs[1] - xs[0])) < 1e-9, "one pitch in a row");
      });
      assert.ok(Math.abs(xs.reduce((a, b) => a + b, 0)) < 1e-9, "centred");
      const left = Math.min(...xs) - CHIP.w / 2 - slant / 2, right = Math.max(...xs) + CHIP.w / 2 + slant / 2;
      assert.ok(left >= -panel / 2 - 1e-9 && right <= panel / 2 + 1e-9, `${n} chips in ${panel}: row ${row} is ${left} to ${right}`);
    }
    // rows are filled in order: no row is started before the one above is full
    const counts = Array.from({ length: rows }, (_, row) => places.filter((p) => p.row === row).length);
    assert.ok(counts.slice(0, -1).every((c) => c === counts[0]) && counts.at(-1) <= counts[0]);
  }
  assert.deepEqual(Popup.chipPlaces(3, 850).map((p) => p.row), [0, 0, 0], "the most a card has, three chips, stay on one row, so the fields box keeps its place");
  assert.deepEqual(Popup.chipPlaces(2, 850).map((p) => p.x), [-145, 145]);
});

for (const lang of LOCALE_LIST) test(`the details panel's chips are 272 x 72, inside the panel and clear of the fields box and the name, in ${lang}`, async () => {
  const NGUI = await import("../../src/bmpt/web/js/ngui.js");
  const { CHIP } = Tokens;
  const ui = localeWebui(lang);
  const lists = [[ui.type.noRecruit], [ui.type.added, ui.popup.inUse], [ui.type.recruit, ui.settings.server.global, ui.mods.copy.none],
    [ui.type.added, ui.settings.server.japan, ui.mods.copy.none, ui.popup.inUse]];
  const bounds = Popup.listLayout().left;
  const panel = [960 + bounds.l * S, 960 + bounds.r * S], slant = CHIP.h * Math.tan(10 * Math.PI / 180) * S;
  try {
    setStrings(lang, Object.fromEntries(strings(ui)));
    document.documentElement.lang = lang;
    for (const chips of lists) for (const original of [null, "original_name_spr.bundle"]) for (const full of ["Hoshino", "Hoshino (Swimsuit, Battle Ready, Special Edition)"]) {
      await Popup.listPopup({ host: prompt, title: ui.info.title.portrait, selected: "a",
        items: [{ key: "a", label: full, full, original, chips, thumb: null, big: null }],
        details: () => ({ title: ui.info.title.portrait, fields: [["File", "ibuki_dress_spr_very_long_name.bundle"], ["Size", "12 MB"], ["CRC", "Fixed"], ["Copy", "Official"]], actions: [[ui.picker.ok, () => {}]] }) });
      const part = (name) => walk(prompt).find((el) => el.dataset.path === `Details/${name}`);
      const fields = stageRect(part("Fields"), NGUI), name = stageRect(part("Name"), NGUI);
      const what = `${lang} ${chips.length} chips${original ? " with an original" : ""}, ${full.length > 10 ? "long" : "short"} name`;
      chips.forEach((_, i) => {
        const chip = stageRect(part(`Chip_${i}`), NGUI);
        assert.ok(Math.abs(chip[2] - CHIP.w * S) < 0.02 && Math.abs(chip[3] - CHIP.h * S) < 0.02, `${what}: chip ${i} is ${chip[2] / S} x ${chip[3] / S}`);
        assert.ok(chip[0] - slant / 2 >= panel[0] - 0.02 && chip[0] + chip[2] + slant / 2 <= panel[1] + 0.02, `${what}: chip ${i} inside the panel`);
        assert.ok(chip[1] + chip[3] + 20 * S <= fields[1] + 0.02, `${what}: chip ${i} keeps 20 above the fields box`);
        // its caption stays in the chip
        const text = walk(prompt).filter((el) => el.dataset.path?.startsWith(`Details/Chip_${i}/Text`) && el.textContent);
        assert.ok(text.length, `${what}: chip ${i} has its caption`);
        for (const el of text) {
          const r = stageRect(el, NGUI), fs = parseFloat(el.style.fontSize);
          assert.ok(r[0] >= chip[0] - 0.02 && r[0] + r[2] <= chip[0] + chip[2] + 0.02 && r[1] >= chip[1] - 0.02 && r[1] + r[3] <= chip[1] + chip[3] + 0.02, `${what}: chip ${i}'s caption inside it`);
          assert.ok(fs <= NGUI.fontSizeFor(el.textContent, CHIP.fs) * S + 0.02, `${what}: chip ${i} at ${fs}`);
        }
      });
      // the chips of one row are level and the name above does not reach into them (an original line sits between)
      const first = stageRect(part("Chip_0"), NGUI);
      if (!original && full.length < 10) assert.ok(name[1] + name[3] <= first[1] + 0.02, `${what}: the name clears the chips`);
      prompt.hidden = true;
    }
  } finally { prompt.hidden = true; setStrings("en-US", Object.fromEntries(strings(english))); document.documentElement.lang = "en-US"; }
});

test("the portrait panel's chips (72 high) stay in the right half and 20 above the fields box", async () => {
  const NGUI = await import("../../src/bmpt/web/js/ngui.js");
  const b = Popup.listLayout().left, w = b.r - b.l;
  const ui = localeWebui("en-US");
  const left = 960 + (b.l + (w + 40) / 12 * 5) * S, right = 960 + b.r * S;
  try {
    for (const chips of [[ui.type.noRecruit], [ui.type.added, ui.popup.inUse], [ui.type.recruit, ui.settings.server.global, ui.mods.copy.none]]) {
      await Popup.listPopup({ host: prompt, title: "Portrait", selected: "a", layoutKind: "spr",
        items: [{ key: "a", label: "Hoshino", full: "Hoshino", chips, thumb: null, big: null }],
        details: () => ({ title: "Portrait", fields: [["File", "ibuki_dress_spr.bundle"], ["Size", "12 MB"], ["CRC", "Fixed"], ["Copy", "Official"]], actions: [] }) });
      const part = (name) => walk(prompt).find((el) => el.dataset.path === `Details/${name}`);
      const fields = stageRect(part("Fields"), NGUI);
      chips.forEach((_, i) => {
        const chip = stageRect(part(`Chip_${i}`), NGUI);
        assert.ok(Math.abs(chip[3] - 72 * S) < 0.02, `${chips.length} chips: chip ${i} is 72 high`);
        assert.ok(chip[0] >= left - 0.02 && chip[0] + chip[2] <= right + 0.02, `${chips.length} chips: chip ${i} in the right half`);
        assert.ok(chip[1] + chip[3] + 20 * S <= fields[1] + 0.02, `${chips.length} chips: chip ${i} keeps 20 above the fields box`);
      });
      prompt.hidden = true;
    }
  } finally { prompt.hidden = true; }
});

test("the startup Spine notice wraps inside UIPopup_Sort at message sizes in every locale and window scale", async () => {
  const NGUI = await import("../../src/bmpt/web/js/ngui.js");
  const Consent = await import("../../src/bmpt/web/js/consent.js");
  await Popup.ready();
  try {
    for (const lang of ["en-US", "th-TH", "zh-CN", "zh-TW", "ja-JP", "ko-KR"]) {
      const ui = JSON.parse(readFileSync(new URL(`../../src/bmpt/locales/${lang}.json`, import.meta.url))).webui;
      setStrings(lang, Object.fromEntries(strings(ui)));
      const { title, notice } = ui.spine.license;
      const { tree, width, height } = Consent.spineFrame();
      const popup = NGUI.find(tree, "Popup");
      assert.equal(NGUI.find(popup, "Base/BG/Btn_Close").active, false);
      assert.equal(NGUI.find(popup, "Btn/Btn_Cancel").active, false);
      const ok = NGUI.find(popup, "Btn/Btn_Ok");
      assert.deepEqual([ok.active, ok.pos[0]], [true, 0]);
      const frame = NGUI.find(popup, "Base/BG").widgets[0];
      assert.deepEqual([frame.w_, frame.h_, frame.pivot], [width, height, 4]);
      assert.equal(width, 2098);
      const heading = NGUI.find(popup, "Base/BG/Label_Title_Locale").widgets[0];
      assert.equal(heading.text.replace(/\s/g, ""), title.replace(/\s/g, ""));
      const lineNode = NGUI.find(popup, "Lines/Line_0"), line = lineNode.widgets[0];
      assert.equal(line.text.replace(/\s/g, ""), notice.replace(/\s/g, ""));
      if (NGUI.glyphWidth(notice, 52) > line.w_) assert.ok(line.text.includes("\n"), `${lang} wraps when needed`);
      assert.ok(line.fontSize >= 44 && line.fontSize <= 52, `${lang} message size ${line.fontSize}`);
      assert.ok(line.text.split("\n").length * NGUI.fontSizeFor(line.text, line.fontSize) * NGUI.LINE_PITCH <= line.h_);
      const caption = NGUI.find(ok, "Label_Locale").widgets[0];
      assert.equal(caption.text.replace(/\s/g, ""), ui.picker.ok.replace(/\s/g, ""));
      assert.ok(caption.fontSize >= FOOTER.caption.fsMin);
      for (const [name, widget] of [["heading", heading], ["body", line], ["caption", caption]]) {
        for (const text of widget.text.split("\n")) assert.ok(NGUI.glyphWidth(text, widget.fontSize) <= widget.w_, `${lang} ${name}`);
      }
      const lineTop = NGUI.find(popup, "Lines").pos[1] + lineNode.pos[1] + line.h_ / 2;
      const lineBottom = lineTop - line.h_;
      assert.ok(lineTop <= POPUP.frameY + height / 2 - Popup.MSG.title + 1, `${lang} under the title`);
      assert.ok(lineBottom >= Popup.footerTop(height), `${lang} over the button`);
      assert.ok(height <= Popup.MSG.cap, `${lang} needs no scroll or clipped content`);
      // main.py's minimum and default windows, plus a large window; the 1920 x 1080 stage scales uniformly.
      for (const [windowW, windowH] of [[960, 540], [1600, 900], [2560, 1440]]) for (const density of [1, 2]) {
        const scale = Math.min(windowW / 1920, windowH / 1080) * density;
        const centre = [windowW * density / 2, windowH * density / 2 - POPUP.frameY * S * scale];
        assert.ok(centre[0] - width * S * scale / 2 >= 0);
        assert.ok(centre[0] + width * S * scale / 2 <= windowW * density);
        assert.ok(centre[1] - height * S * scale / 2 >= 0);
        assert.ok(centre[1] + height * S * scale / 2 <= windowH * density);
      }
    }
  } finally { setStrings("en-US", Object.fromEntries(strings(english))); }
});

// Every preview entry stays quiet while the startup acknowledgement is pending.
test("preview, comparison and compatibility modules contain no licence notice or confirmation button", () => {
  for (const name of ["lobby", "detail", "student-hall", "mod-compare", "mod-compatibility"]) {
    const source = readFileSync(new URL(`../../src/bmpt/web/js/${name}.js`, import.meta.url), "utf8");
    assert.doesNotMatch(source, /licenseRequired|framedNotice|noticeFrame|spine\.license|confirmLicence|Consent\.spine/);
  }
  assert.equal(Popup.noticeFrame, undefined);
  assert.doesNotMatch(readFileSync(new URL("../../src/bmpt/web/css/app.css", import.meta.url), "utf8"), /\.lobby-notice/);
});

test("missing-original apply results fit the message frame in every locale", async () => {
  const NGUI = await import("../../src/bmpt/web/js/ngui.js");
  await Popup.ready();
  for (const lang of ["en-US", "th-TH", "zh-CN", "zh-TW", "ja-JP", "ko-KR"]) {
    const ui = JSON.parse(readFileSync(new URL(`../../src/bmpt/locales/${lang}.json`, import.meta.url))).webui;
    setStrings(lang, Object.fromEntries(strings(ui)));
    const lines = [ui.mods.keptCurrent, ui.mods.incomplete];
    // the results show as an ordinary message (Popup.message), at its default width
    const { body, width, height } = Popup.messageLayout({ lines, ok: ui.picker.ok });
    assert.ok([1300,1540,2098].includes(width));
    assert.ok(height * S <= 1080, lang);
    for (const [index, text] of lines.entries()) {
      const widget = NGUI.find(body.tree, `Line_${index}`).widgets[0];
      assert.equal(widget.text.replace(/\s/g, ""), text.replace(/\s/g, ""));
      assert.ok(widget.fontSize >= 44 && widget.fontSize <= 52, lang);
      assert.ok(NGUI.glyphWidth(widget.text, widget.fontSize) <= widget.w_, lang);
      assert.ok(widget.h_ >= widget.text.split("\n").length * widget.fontSize, lang);
    }
  }
  setStrings("en-US", Object.fromEntries(strings(english)));
});

for (const lang of LOCALE_LIST) test(`the rendered footer labels keep every caption and its recorded floor in ${lang}`, async () => {
  const NGUI = await import("../../src/bmpt/web/js/ngui.js");
  await Popup.ready();
  const ui = localeWebui(lang);
  const groups = [["picker.ok", "popup.cancel"], ["popup.save", "popup.cancel"], ["popup.apply", "popup.cancel"],
    ["consent.no", "consent.yes"], ["mods.scan.no", "mods.scan.yes"], ["update.later", "update.notes", "update.now"],
    ["update.later", "update.notes", "update.restart"], ["update.later", "update.notes", "update.page"],
    ["update.releasePage", "display.close"], ["settings.log.copy", "display.close"], ["recovery.report", "picker.ok"]];
  try {
    setStrings(lang, Object.fromEntries(strings(ui))); document.documentElement.lang = lang;
    for (const keys of groups) {
      const captions = keys.map(key => byDots(ui, key));
      const before = NGUI.fitGroup(captions, 72, 48, 400, 2, { maxH: 140 })[0].fs;
      const known = lang === "th-TH" && (keys[0] === "update.later" || keys.join("/") === "recovery.report/picker.ok");
      const tree = Popup.popupFrame(ui.quit.title, keys.length > 2 ? "L" : "S", 0,
        { buttons: captions.map(label => ({ label })) });
      const labels = NGUI.find(tree, "Popup/Btn").children.map(child => NGUI.find(child, "Label_Locale").widgets[0]);
      assert.equal(new Set(labels.map(label => label.fontSize)).size, 1);
      labels.forEach((label, i) => {
        assert.ok(label.fontSize >= (known ? Math.max(36, before) : 48) && label.fontSize <= 72, `${lang} ${keys.join("/")}`);
        assert.equal(label.text.replace(/\s/g, ""), captions[i].replace(/\s/g, ""));
        assert.ok(label.text.split("\n").length <= 2);
        for (const line of label.text.split("\n")) assert.ok(NGUI.glyphWidth(line, label.fontSize) <= 472);
        assert.ok(label.text.split("\n").length * NGUI.fontSizeFor(label.text, label.fontSize) * NGUI.LINE_PITCH <= Popup.BUTTON_TEXT_H);
      });
    }
  } finally { setStrings("en-US", Object.fromEntries(strings(english))); document.documentElement.lang = "en-US"; }
});

test("portrait and ordinary detail scroll areas keep the complete last field and actions reachable", async () => {
  const NGUI = await import("../../src/bmpt/web/js/ngui.js");
  const fields = Array.from({ length: 12 }, (_, i) => [`Field ${i}`, `value_${i}_`.repeat(12)]);
  try {
    for (const kind of [null, "spr"]) {
      await Popup.listPopup({ host: prompt, title: "Sources", layoutKind: kind, items: [{ key: "a", label: "Source", full: "Source" }],
        selected: "a", details: () => ({ title: "Source", fields, actions: [["Open", () => {}]] }) });
      const last = walk(prompt).find(el => el.dataset.path === "Details/V_11");
      const action = walk(prompt).find(el => el.dataset.path === "Details/Act_0/Body");
      const area = last.parentElement.parentElement;
      assert.equal(area.className, "popup-cards");
      assert.ok(parseFloat(area.children[0].style.height) > parseFloat(area.style.height));
      const r = NGUI.rect(last), a = NGUI.rect(action);
      assert.ok(r[1] + r[3] < a[1]);
      assert.ok(a[1] + a[3] <= parseFloat(area.children[0].style.height) + 0.02);
      const layout = Popup.listLayout(false, kind);
      assert.ok(Math.abs(parseFloat(area.style.top) + parseFloat(area.style.height)
        - (540 - (Popup.footerTop(layout.frame.h) + 20) * S)) < 0.02);
    }
  } finally { prompt.hidden = true; Popup.closed(prompt); }
});

test("finding the game asks the Check Game Files consent first and searches only after an Allow", async () => {
  const Consent = await import("../../src/bmpt/web/js/consent.js");
  let allowed = false, searched = 0, asked = 0;
  handlers.set("settings.detectGame", () => { searched++; return allowed ? { result: ["/games/BlueArchive"] } : { error: { code: "scan_off" } }; });
  try {
    assert.equal(await Consent.detectGame("global", async () => { asked++; return false; }), null, "declined: nothing is searched");
    assert.equal(searched, 1);
    assert.deepEqual(await Consent.detectGame("global", async () => { asked++; allowed = true; return true; }), ["/games/BlueArchive"]);
    assert.equal(searched, 3);
    assert.deepEqual(await Consent.detectGame("global", async () => { asked++; return true; }), ["/games/BlueArchive"]);
    assert.equal(asked, 2, "an existing consent asks nothing");
  } finally { handlers.delete("settings.detectGame"); }
});

// BL-097: content-fitted heights and vertical centring, shared by prefab and fallback popups.
const contentCentre = f => f.cy + f.h / 2 - f.content.y - f.content.h / 2;
const messageTop = layout => Popup.FRAME_Y + layout.height / 2 - layout.y0;

test("explicit frame heights use 786 through 1560; omitted or zero heights retain the exemplar", () => {
  for (const key of ["S", "M", "L", "XL"]) {
    assert.equal(Frames.frameGeometry(key).h, POPUP_FRAME[key].h);
    assert.equal(Frames.frameGeometry(key, 0).h, POPUP_FRAME[key].h);
    for (const [height, expected] of [[1, 786], [700, 786], [786, 786], [900, 900], [1400, 1400], [2000, 1560]])
      assert.equal(Frames.frameGeometry(key, height).h, expected);
  }
  assert.ok(!("minH" in Popup.MSG) && !("minH" in Frames.DETAILS));
});

for (const lang of LOCALE_LIST) test(`messages fit and centre their body and check block in S, M and L in ${lang}`, async () => {
  const ui = localeWebui(lang);
  await Popup.ready();
  try {
    setStrings(lang, Object.fromEntries(strings(ui))); document.documentElement.lang = lang;
    for (const width of ["S", "M", "L"]) for (const footer of [false, true]) {
      const args = { lines: [ui.picker.ok], width, ok: footer ? ui.picker.ok : null };
      const short = Popup.messageLayout(args);
      assert.equal(short.height, 786); assert.equal(short.scroll, false);
      near2(messageTop(short) + short.visible / 2, short.content.y + short.content.h / 2, .5);
      const checked = Popup.messageLayout({ ...args, check: { label: ui.picker.ok, checked: false } });
      assert.equal(checked.height, 786); assert.equal(checked.scroll, false);
      near2(messageTop(checked) + (checked.visible + Popup.MSG.check) / 2, checked.content.y + checked.content.h / 2, .5);
      near2(checked.checkBox.y, messageTop(checked) + checked.visible + SPACE.m);
      // The same formula must hold when a tour reserves the tallest page's height.
      const page = Popup.messageLayout({ ...args, height: 1200 });
      assert.equal(page.height, 1200);
      near2(messageTop(page) + page.visible / 2, page.content.y + page.content.h / 2, .5);
      const tall = Popup.messageLayout({ ...args, lines: Array.from({ length: footer ? 6 : 8 }, () => ({ text: ui.picker.ok, fontSize: 52, minFontSize: 52 })) });
      const need = Popup.MSG.title + tall.body.height + (footer ? Popup.MSG.buttons : Popup.MSG.none);
      assert.ok(need > 786 && need < POPUP_FRAME.maxH);
      near2(tall.height, need); near2(messageTop(tall), POPUP_FRAME.contentTop);
      assert.equal(tall.scroll, false);
      const scrolling = Popup.messageLayout({ ...args, scroll: true, check: { label: ui.picker.ok } });
      near2(scrolling.visible, scrolling.body.height);
      near2(messageTop(scrolling) + (scrolling.visible + Popup.MSG.check) / 2, scrolling.content.y + scrolling.content.h / 2, .5);
      const layer = new El(), fallback = Popup.plainMessage(layer, { ...args, title: ui.picker.ok,
        state: { label: ui.picker.ok, checked: false } }, () => {});
      near2(parseFloat(fallback.content.style.top) / S, messageTop(fallback.layout));
      near2(parseFloat(layer.querySelector(".plain-check").style.top) / S, fallback.layout.checkBox.y);
      assert.equal(fallback.layout.height, checked.height);
      // The body centre stays on its content centre at minimum, default and large windows, at 1x and 2x.
      for (const [w, h] of [[960, 540], [1600, 900], [2560, 1440]]) for (const density of [1, 2]) {
        const scale = Math.min(w / 1920, h / 1080) * S * density;
        near2((short.y0 - short.visible / 2) * scale,
          (Popup.FRAME_Y + short.height / 2 - short.content.y - short.content.h / 2) * scale, .5);
      }
    }
  } finally { setStrings("en-US", Object.fromEntries(strings(english))); document.documentElement.lang = "en-US"; }
});

for (const lang of LOCALE_LIST) test(`both consent stacks fit and centre between title and footer in ${lang}`, async () => {
  const Consent = await import("../../src/bmpt/web/js/consent.js"), ui = localeWebui(lang);
  try {
    setStrings(lang, Object.fromEntries(strings(ui))); document.documentElement.lang = lang;
    const asset = Consent.assetLayout("/fixtures/cache");
    const assetHeight = asset.cachedH + asset.neverH + asset.note.h + 2 * SPACE.xl;
    near2(asset.h, Math.max(786, POPUP_FRAME.contentTop + assetHeight + Popup.MSG.buttons), 1);
    near2((asset.cachedTop + asset.noteBottom) / 2, contentCentre(asset), .5);
    const scan = Consent.scanLayout(), first = scan.boxes[0], last = scan.boxes.at(-1);
    const scanHeight = scan.boxes.reduce((sum, b) => sum + b.h, 0) + 2 * SPACE.xl;
    near2(scan.h, Math.max(786, POPUP_FRAME.contentTop + scanHeight + Popup.MSG.buttons), 1);
    near2((first.y + last.y + last.h) / 2, scan.content.y + scan.content.h / 2, .5);
    near2(first.y, POPUP_FRAME.contentTop);
    for (const f of [asset, scan]) assert.ok(f.h <= POPUP_FRAME.maxH);
  } finally { setStrings("en-US", Object.fromEntries(strings(english))); document.documentElement.lang = "en-US"; }
});

test("Mod Alias and Export fit their unchanged row envelopes and centre the whole body", () => {
  const mod = Frames.MOD_ALIAS, content = Frames.popupContent(mod.w, mod.h, { text: true });
  const modNeed = POPUP_FRAME.contentTop + 2 * SPACE.xxxl + SPACE.m + Popup.MSG.buttons;
  near2(mod.h, Math.max(786, modNeed), 1);
  assert.equal(mod.input.h, SPACE.xxxl); assert.equal(mod.hint.h, SPACE.xxxl);
  near2((mod.input.y + mod.input.h / 2 + mod.bottom) / 2, contentCentre({ ...mod, content }), .5);
  const exp = Frames.EXPORT, rows = Object.values(exp.rows), expContent = Frames.popupContent(exp.w, exp.h);
  assert.deepEqual(rows.map(r => r.h), [260, 240, 240]);
  near2(exp.h, Math.max(786, POPUP_FRAME.contentTop + 740 + 2 * SPACE.m + Popup.MSG.buttons), 1);
  near2((rows[0].y + rows[0].h / 2 + exp.bottom) / 2, contentCentre({ ...exp, content: expContent }), .5);
  for (let i = 1; i < rows.length; i++) near2(rows[i - 1].y - rows[i - 1].h / 2 - rows[i].y - rows[i].h / 2, SPACE.m);
});

test("scrolling text viewer and picker heights retain their pre-fit viewports", () => {
  assert.equal(Frames.TEXT_VIEWER.h, 1353);
  assert.equal(Frames.TEXT_VIEWER.box.height, 885);
  for (const [kind, height] of [[null, 1353], ["spr", 1512], ["lobby", 1353], ["model", 1353]])
    for (const check of [false, true]) assert.equal(Popup.listLayout(check, kind).frame.h, height, String(kind));
});

test("model search appends cross cards, drawn like any card, through the shared picker tools", async (t) => {
  const NGUI = await import("../../src/bmpt/web/js/ngui.js");
  const collection = n("UICharacterCollection", [0, 0]);
  const base = "Inventory_ListControl/RightCanvas/ListBG";
  insert(collection, "SeletControl/TopMenu", structuredClone(NGUI.find(switchLayouts.inventory, `${base}/BG_Top_Widget`)));
  insert(collection, "SeletControl/CharacterSearch", structuredClone(NGUI.find(switchLayouts.inventory, `${base}/Inbox/SearchInfo`)));
  const fetch = globalThis.fetch;
  t.mock.method(globalThis, "fetch", async (url, options) => url === "/asset/ui/layout/UICharacterCollection.json" ? reply(collection) : fetch(url, options));
  const Fresh = await import("../../src/bmpt/web/js/popup.js?cross-model-search");
  const own = [{ key: "self", label: "Original", full: "Original" },
    { key: "mod", label: "Model mod", full: "Model mod" },
    { key: "sibling", label: "Model sibling", full: "Model sibling", hint: english.model.beyondFit }];
  const extras = [{ key: "cross-z", label: "Model Zebra", full: "Model Zebra" }, { key: "cross-a", label: "Model Alpha", full: "Model Alpha" }]
    .map((it) => ({ ...it, hint: english.model.crossCharacter, thumb: "model.png", terms: ["ch0171_enemy"] }));
  const queries = [], chosen = [];
  const keys = () => walk(prompt).filter((e) => e.dataset.key).map((e) => e.dataset.key);
  try {
    await Fresh.listPopup({ host: prompt, layoutKind: "model", title: "Models", items: own, selected: "self", okText: "Save",
      tools: { more: (query) => { queries.push(query); return query.trim() ? extras : []; } },
      onSelect: (key) => chosen.push(key) });
    assert.deepEqual(keys(), ["self", "mod", "sibling"], "empty query keeps exactly the original cards");
    const sibling = walk(prompt).find((e) => e.dataset.key === "sibling");
    assert.equal(sibling.disabled ?? false, false);
    assert.equal(sibling.getAttribute("aria-description"), english.model.beyondFit);
    action(prompt, "search").click();
    const input = () => walk(prompt).find((e) => e.className === "roster-search");
    await until(() => !!input());
    assert.equal(input().placeholder, english.roster.searchHint);
    input().value = "Model";
    for (const fn of input().listeners.input || []) fn({ isComposing: true });
    await tick();
    assert.deepEqual(keys(), ["self", "mod", "sibling"], "IME composition does not run a new search");
    for (const fn of input().listeners.compositionend || []) fn({});
    await until(() => keys().length === 4 && keys()[0] === "mod");
    assert.deepEqual(keys(), ["mod", "sibling", "cross-z", "cross-a"]);
    assert.equal(queries.at(-1), "Model");
    const cross = walk(prompt).find((e) => e.dataset.key === "cross-z");
    assert.equal(cross.disabled ?? false, false);
    assert.equal(cross.getAttribute("aria-description"), english.model.crossCharacter);
    cross.click(); assert.deepEqual(chosen, ["cross-z"]);
    const thumb = walk(prompt).find((e) => e.dataset.path === "Card_cross-z/Thumb");
    const ownThumb = walk(prompt).find((e) => e.dataset.path === "Card_mod/Thumb");
    assert.ok(!thumb.style.cssText && !ownThumb.style.cssText, "no grey filter: a cross card's picture is drawn like an own card's");
    assert.equal(Fresh.SHADE, undefined, "the grey filter is gone");
    assert.ok(!walk(prompt).some((e) => ["Card_cross-z/Dim", "Card_cross-z/Using"].includes(e.dataset.path)), "no overwrite veil or badge");
    assert.deepEqual([thumb.style.width, thumb.style.height], [ownThumb.style.width, ownThumb.style.height], "cross cards keep the model card geometry");
    action(prompt, "order").click();
    await until(() => keys()[0] === "sibling");
    assert.deepEqual(keys(), ["sibling", "mod", "cross-a", "cross-z"], "direction applies within both groups");
    input().value = "CH0171_ENEMY";
    for (const fn of input().listeners.input || []) fn({ isComposing: false });
    await until(() => keys().length === 2);
    assert.deepEqual(keys(), ["cross-a", "cross-z"], "the shared matcher searches model dev ids");
    action(prompt, "search-close").click();
    await until(() => !input());
    assert.deepEqual(keys(), ["sibling", "mod", "self"], "clearing search removes appended cards and preserves the active sort direction");
  } finally { prompt.hidden = true; Fresh.closed(prompt); }
});

// --- the list popup's toolbar: with Switch 3D Model's checkbox (BL-131) and without ---
// A collection stand-in whose TopMenu also carries the segment filter and Menubar the roster's toolbar clones.
function toolbarCollection(NGUI) {
  const collection = n("UICharacterCollection", [0, 0]);
  const base = "Inventory_ListControl/RightCanvas/ListBG";
  insert(collection, "SeletControl/TopMenu", structuredClone(NGUI.find(switchLayouts.inventory, `${base}/BG_Top_Widget`)));
  insert(collection, "SeletControl/CharacterSearch", structuredClone(NGUI.find(switchLayouts.inventory, `${base}/Inbox/SearchInfo`)));
  const menu = NGUI.find(collection, "SeletControl/TopMenu");
  menu.children.push(n("FilterBtn", [0, -219], [], ["All", "Main", "Support"].map((name) => n(name, [0, 0], [],
    ["Btn_Normal", "Btn_Pressed"].map((state) => n(state, [0, 0], [], [n("Caption", [0, 0], [{ texture: "Caption", color: "#ffffff/1.00" }])]))))),
  n("Menubar", [0, 0]));
  return collection;
}
// serves it as the page's collection (NGUI keeps the first one it was served for the whole run: forget that one)
function serveToolbarCollection(t, NGUI) {
  NGUI.setResourceContext("toolbar-test");
  NGUI.setResourceContext(null);
  const collection = toolbarCollection(NGUI), fetch = globalThis.fetch;
  t.mock.method(globalThis, "fetch", async (url, options) => url === "/asset/ui/layout/UICharacterCollection.json" ? reply(collection) : fetch(url, options));
}
const SEGMENTS = ["seg.all", "seg.recruit", "seg.noRecruit"];
const rectOf = (el) => ["left", "top", "width", "height"].map((k) => parseFloat(el.style[k]));
// the hit areas of the list's tools, in the stand-in page's pixels, and a digest of everything drawn under Toolbar/
async function toolbarOf(Fresh, tools, items) {
  await Fresh.listPopup({ host: prompt, layoutKind: "model", title: "Models", items, selected: "a", okText: "Save", tools, onSelect() {} });
  const named = (label) => walk(prompt).find((e) => e.className === "hit" && e.getAttribute("aria-label") === label);
  const drawnTools = walk(prompt).filter((e) => (e.dataset.path || "").startsWith("Toolbar/"))
    .map((e) => [e.dataset.path, ...rectOf(e), e.style.visibility || ""]);
  return { order: rectOf(action(prompt, "order")), search: rectOf(action(prompt, "search")), sort: rectOf(action(prompt, "sort")),
    segments: SEGMENTS.map((key) => rectOf(named(english.seg[key.slice(4)]))), drawn: drawnTools };
}

test("a list popup toolbar without the checkbox keeps its layout", async (t) => {
  const NGUI = await import("../../src/bmpt/web/js/ngui.js");
  serveToolbarCollection(t, NGUI);
  const Fresh = await import("../../src/bmpt/web/js/popup.js?toolbar-plain");
  const items = [{ key: "a", label: "Alpha", full: "Alpha", thumb: "a.png" }];
  try {
    const plain = await toolbarOf(Fresh, { segs: SEGMENTS, seg: 1, onSeg() {}, sortKey: "sort.default" }, items);
    // measured on the layout before the checkbox existed (popup.js toolbar): not a pixel moves
    const near = (got, want) => got.forEach((v, i) => assert.ok(Math.abs(v - want[i]) < 1e-6, `${got} against ${want}`));
    near(plain.order, [1733.25, 238.7, 108.97, 75.24]);
    near(plain.search, [1239.89, 238.7, 80.43, 75.24]);
    near(plain.sort, [1453.16, 238.67, 272.7, 75.32]);
    near(plain.segments[0], [824.7567567567569, 242.46486486486486, 149.1891891891892, 67.45945945945947]);
    near(plain.segments[2], [1123.1351351351352, 242.46486486486486, 149.1891891891892, 67.45945945945947]);
    assert.equal(plain.drawn.length, 15);
    assert.equal(createHash("sha1").update(JSON.stringify(plain.drawn)).digest("hex"), "621aac4f9012a6e9104d3aed9f5b85da1b877c64");
    assert.equal(action(prompt, "check"), undefined, "no checkbox in the toolbar");
  } finally { prompt.hidden = true; Fresh.closed(prompt); }
});

test("Show other students is a checkbox of the model popup's toolbar: drawn from the roster's toggle, flipping on a click", async (t) => {
  const NGUI = await import("../../src/bmpt/web/js/ngui.js");
  serveToolbarCollection(t, NGUI);
  const Fresh = await import("../../src/bmpt/web/js/popup.js?toolbar-check");
  const items = [{ key: "a", label: "Alpha", full: "Alpha", thumb: "a.png" }, { key: "b", label: "Beta", full: "Beta", thumb: "b.png" }];
  const check = { label: english.model.showOthers, checked: false, onChange(on) { changes.push(on); } };
  const changes = [], views = [];
  const tools = { segs: SEGMENTS, seg: 0, onSeg() {}, sortKey: "sort.default", check, onView: (keys) => views.push(keys) };
  const box = () => action(prompt, "check");
  const paths = (name) => walk(prompt).filter((e) => (e.dataset.path || "").includes(name));
  try {
    await toolbarOf(Fresh, tools, items);
    assert.ok(box(), "the checkbox is in the toolbar");
    assert.equal(box().getAttribute("role"), "checkbox");
    assert.equal(box().getAttribute("aria-checked"), "false");
    assert.equal(box().getAttribute("aria-label"), english.model.showOthers);
    // it stands left of the three segments, on the toolbar's row, inside the list panel, and the tools keep their order
    const [cx, cy, cw, ch] = rectOf(box());
    const seg = ["All", "Playable", "NPC"].map((label) => rectOf(walk(prompt).find((e) => e.className === "hit" && e.getAttribute("aria-label") === label)));
    const search = rectOf(action(prompt, "search")), sort = rectOf(action(prompt, "sort")), order = rectOf(action(prompt, "order"));
    assert.ok(cx + cw <= seg[0][0], "the checkbox ends before the first segment");
    assert.ok(seg[0][0] < seg[1][0] && seg[1][0] < seg[2][0] && seg[2][0] + seg[2][2] <= search[0] + 1e-6, "segments, then search");
    assert.ok(search[0] < sort[0] && sort[0] < order[0]);
    const listPanel = Fresh.listLayout(false, "model").list, left = 960 + listPanel.l * S, right = 960 + listPanel.r * S;
    assert.ok(cx >= left && order[0] + order[2] <= right + 1e-6, "everything inside the list panel");
    const centre = (r) => r[1] + r[3] / 2;
    assert.ok(Math.abs(centre([cx, cy, cw, ch]) - centre(search)) < 1.5, "the checkbox is on the tools' centre line");
    // drawn from the roster's toggle: its plate, the box and its Type_OFF state; no tick while off
    assert.ok(paths("Toolbar/Menubar/Merge").length > 0);
    assert.ok(paths("Merge/Type_OFF/Check").length > 0 && paths("Merge/Type_ON").length === 0);
    assert.equal(paths("Toolbar/Menubar/Merge/Label").length > 0, true, "its caption");
    // a click flips the shared state, tells the caller and draws the popup again with the tick
    box().click();
    assert.equal(check.checked, true);
    assert.deepEqual(changes, [true]);
    await until(() => box()?.getAttribute("aria-checked") === "true");
    assert.ok(paths("Merge/Type_ON/Check/Icon").length > 0 && paths("Merge/Type_OFF").length === 0, "the tick shows while on");
    box().click();
    await until(() => box()?.getAttribute("aria-checked") === "false");
    assert.deepEqual(changes, [true, false]);
    // the cards in view are reported after every drawing
    assert.ok(views.length >= 1 && views.at(-1).includes("a") && views.at(-1).includes("b"));
    // other popups with tools, without the checkbox (portraits, lobbies), draw none and report nothing
    await Fresh.listPopup({ host: prompt, layoutKind: "model", title: "Models", items, selected: "a", okText: "Save",
      tools: { segs: SEGMENTS, seg: 0, onSeg() {}, sortKey: "sort.default" }, onSelect() {} });
    assert.equal(box(), undefined);
  } finally { prompt.hidden = true; Fresh.closed(prompt); }
});

test("the list reports the cards in view, and those a scroll brings in", async (t) => {
  const NGUI = await import("../../src/bmpt/web/js/ngui.js");
  serveToolbarCollection(t, NGUI);
  const Fresh = await import("../../src/bmpt/web/js/popup.js?toolbar-view");
  const items = Array.from({ length: 60 }, (_, i) => ({ key: `k${i}`, label: `Card ${i}`, full: `Card ${i}`, thumb: "c.png" }));
  const views = [];
  try {
    await Fresh.listPopup({ host: prompt, layoutKind: "model", title: "Models", items, selected: "k0", okText: "Save", onSelect() {},
      tools: { segs: SEGMENTS, seg: 0, onSeg() {}, sortKey: "sort.default", onView: (keys) => views.push(keys) } });
    const first = views.at(-1);
    assert.ok(first.length > 0 && first.length < items.length, `${first.length} of ${items.length} in view`);
    assert.deepEqual(first, items.slice(0, first.length).map((it) => it.key), "the first rows, in the list's order");
    const area = walk(prompt).find((e) => e.className === "popup-cards");
    area.scrollTop = 3000; fire(area, "scroll");
    await until(() => views.at(-1) !== first);
    const later = views.at(-1);
    assert.ok(later.length > 0 && !later.includes("k0") && later.length < items.length, "a scroll moves the window");
  } finally { prompt.hidden = true; Fresh.closed(prompt); }
});

test("a plain line's inline button is as high as the line's text and stays inside the frame, in every locale", async () => {
  await Popup.ready();
  const { glyphsOf } = await import("../../src/bmpt/web/js/ngui.js").then((m) => ({ glyphsOf: m.glyphsOf || null }));
  for (const lang of ["en-US", "zh-CN", "zh-TW", "ja-JP", "ko-KR", "th-TH"]) {
    const ui = JSON.parse(readFileSync(new URL(`../../src/bmpt/locales/${lang}.json`, import.meta.url))).webui;
    for (const caption of [ui.add.go, ui.mods.impact.fix]) {
      const layout = Popup.messageLayout({ plain: true, ok: "OK", lines: [
        { text: ui.mods.held.replace("{n}", "3") },
        { text: ui.mods.heldTitle, gap: 32, button: { label: caption, inline: true, fn: () => {} } }] });
      const line = layout.body.boxes[1], [text, button] = line.lineRow.boxes;
      assert.ok(button.h <= line.f.fs, `${lang} ${caption}: within the text's height`);
      assert.ok(line.bf.fs <= button.h - Tokens.XS_BUTTON.boxInsetH, `${lang} ${caption}: the caption inside the button`);
      assert.ok(button.h < 104, `${lang}: smaller than the XS small button`);
      assert.ok(line.bf.text === caption, `${lang} ${caption}: caption whole`);
      if (glyphsOf) assert.ok(glyphsOf(line.bf.text, line.bf.fs) < button.w, `${lang} ${caption}: caption inside`);
      assert.ok(text.x + text.w <= button.x && button.x + button.w <= layout.width, `${lang} ${caption}: inside the frame`);
      assert.ok(button.x - (text.x + text.w) <= 40, `${lang} ${caption}: right after the text`);
      assert.equal(text.x, layout.body.boxes[0].lineRow.boxes[0].x, `${lang} ${caption}: the text starts where the lines do`);
    }
  }
});

test("the held entries' Details fit their wide frame with four mods, Go To and Fix and Apply on one line each, in every locale", async () => {
  await Popup.ready();
  const I18n = await import("../../src/bmpt/web/js/i18n.js");
  const Impact = await import("../../src/bmpt/web/js/update-impact.js");
  const NGUI = await import("../../src/bmpt/web/js/ngui.js");
  const { XS_BUTTON } = await import("../../src/bmpt/web/js/ui-tokens.js");
  const flat = (o, p = "", out = {}) => { for (const [k, v] of Object.entries(o)) { if (v && typeof v === "object") flat(v, `${p}${k}.`, out); else out[`${p}${k}`] = v; } return out; };
  try {
    for (const lang of ["en-US", "zh-CN", "zh-TW", "ja-JP", "ko-KR", "th-TH"]) {
      I18n.setStrings(lang, flat(JSON.parse(readFileSync(new URL(`../../src/bmpt/locales/${lang}.json`, import.meta.url)))));
      const entries = [1, 2, 3, 4].map((i) => ({ install: "/g", core: "hoshino_swimsuit_spr", variant: "hoshino_swimsuit", kind: "spr",
        source: `mod:${i}`, label: `Swimsuit Hoshino v${i}` }));
      const layout = Popup.messageLayout({ lines: Impact.heldLines(entries, () => "Hoshino (Swimsuit)"), ok: "OK", plain: true, width: Impact.WIDTH });
      assert.equal(layout.scroll, false, `${lang} ${layout.height} ${layout.body.height} ${layout.visible}`);
      for (const line of layout.body.boxes.filter((b) => b.r.button)) {
        const [text, ...buttons] = line.lineRow.boxes;
        assert.equal(buttons.length, 2, `${lang}: Go To and Fix and Apply`);
        for (const [button, fit, label] of [[buttons[0], line.bf, line.r.button.label], [buttons[1], line.more[0].bf, line.more[0].button.label]]) {
          assert.ok(button.h <= line.f.fs, `${lang} ${label}: within the text's height`);
          assert.ok(fit.text === label, `${lang} ${label}: caption whole, on one line`);
          // the small button fits its caption again within its width less its inset: still one line
          assert.ok(!NGUI.fitText(label, fit.fs, fit.fs, button.w - XS_BUTTON.fitInset).text.includes("\n"), `${lang} ${label}: drawn on one line`);
          assert.ok(text.x + text.w <= button.x && button.x + button.w <= layout.width, `${lang} ${label}: inside the frame`);
        }
        assert.ok(buttons[0].x + buttons[0].w <= buttons[1].x, `${lang}: side by side`);
      }
    }
  } finally { setStrings("en-US", Object.fromEntries(strings(english))); }
});

test("a segment grows so its count never sits alone on a line, within the list's room; captions without one keep 230", async () => {
  await Popup.ready();
  const I18n = await import("../../src/bmpt/web/js/i18n.js");
  const NGUI = await import("../../src/bmpt/web/js/ngui.js");
  const flat = (o, p = "", out = {}) => { for (const [k, v] of Object.entries(o)) { if (v && typeof v === "object") flat(v, `${p}${k}.`, out); else out[`${p}${k}`] = v; } return out; };
  try {
    for (const lang of ["en-US", "zh-CN", "zh-TW", "ja-JP", "ko-KR", "th-TH"]) {
      const table = flat(JSON.parse(readFileSync(new URL(`../../src/bmpt/locales/${lang}.json`, import.meta.url))));
      I18n.setStrings(lang, table);
      const plain = ["seg.all", "seg.recruit", "seg.noRecruit"].map((key) => I18n.T(key));
      const kept = Popup.segmentWidth(plain);
      assert.equal(kept.w, Popup.SEG.w, `${lang}: no count, the prefab's width`);
      assert.deepEqual(kept.fits, NGUI.fitGroup(plain, 44, 26, 200, 2, { maxH: 96 }), `${lang}: the same fit as before`);
      for (const n of [0, 12, 104]) {
        const captions = ["tabHeld", "tabReapplied", "tabOthers"].map((k) => I18n.T(`mods.impact.${k}`, { n }));
        assert.ok(captions.every((c) => c.endsWith(String(n)) && !c.startsWith("mods.")), `${lang}: ${captions}`);
        const { w, fits } = Popup.segmentWidth(captions, 900);
        assert.ok(w >= Popup.SEG.w && w <= 300, `${lang} ${n}: ${w} within the room`);
        for (const f of fits) assert.ok(!/\n\s*\d+\s*$/.test(f.text), `${lang} ${n}: "${f.text}" keeps its count with its words`);
        assert.equal(new Set(fits.map((f) => f.fs)).size, 1, `${lang}: one size`);
      }
    }
  } finally { setStrings("en-US", Object.fromEntries(strings(english))); }
});
