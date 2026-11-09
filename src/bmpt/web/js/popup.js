// Shared popups on the game's popup frame (UIPopup_Sort), for every screen:
//   - the frame itself (popupFrame), with its backdrop, title, close button and Cancel / OK;
//   - the list popup, laid out like the game's quick formation edit: a card's details on the left, a framed list with
//     the student list's tools on the right (optional), the buttons under it; one card chosen, or several ticked;
//   - the message popup: a title, lines (a line may carry a small button), an optional checkbox (the gacha list's
//     Btn_Checkbox) and a footer group; it resolves when it closes, however it closes.
// Popups draw into a host: the detail screen's own (#popup, #picker) or the stage-level ones (#prompt for lists,
// #dialog for messages, both over the settings), so they show on any screen. Every open popup is on one stack:
// Escape goes to the topmost one only, as the game's PopupClose closes the popup in front. A popup that must be
// answered with a button takes Escape and does nothing with it, so nothing under it acts either.
// The pure parts (the stack, the selection, the start sequence) are tested in Node (tests/js/popup.test.mjs).
import * as NGUI from "./ngui.js";
import { attachHint } from "./hover-hint.js";
import { switchGrid, placeSwitchCards } from "./switch-layout.js";
import { scrollbar, scrollbarTrack } from "./scrollbar.js";
import { horizontalScroll } from "./horizontal-scroll.js";
import * as Buttons from "./game-buttons.js";
import { searchTree, sortBody, orderBody, pickerItems, toolbarFit, toolbarTree, TOOLBAR_CENTRE_Y } from "./roster-widgets.js";
import { resizeButton } from "./button-layout.js";
import { fitBadgeCaptions, selectedFrame, selectionArtWindow, SELECTION_PREFAB, SELECTION_SLOT } from "./selected-frames.js";
import { frameOutline } from "./row-thumbnail.js";
import { T } from "./i18n.js";
import { codeOf, noticeNames, withCode } from "./error-codes.js";
import { withTimeout } from "./async.js";
import { TEXT_VIEWER, frameClass, frameGeometry, popupContent } from "./popup-frames.js";
import { boxHeight, statsBox, valueRoom } from "./stats-box.js";
import { BACKDROP, CHIP, COLOUR, CONTROL, POPUP_FRAME, SPACE, TYPE, SMALL_BUTTON, STATS_BOX, captionMinimum } from "./ui-tokens.js";
import { DOT, RECRUIT, S, TAN10, clone, glyphsOf, hitButton, markedLabel, wrapPath } from "./util.js";

import { container, grid, row, stack as layoutStack, textItem, toNode } from "./ui-grid.js";

const { node, sprite, label, find, rect } = NGUI;
const fitText = (...a) => NGUI.fitText(...a);
const hostOf = (h) => (typeof h === "string" ? document.getElementById(h) : h);

// --- the prefabs, read once: the frame (required), the student list's tools and the checkbox (each optional: a
// popup without them still works) ---
let layouts = null, loading = null, frameLoading = null;
let selectionLoading = null, selectionSlot = null, selectionSprite = null, selectionOutline = null;
export function selectionReady() {
  if (selectionLoading) return selectionLoading;
  const pending = Promise.all([NGUI.layout(SELECTION_PREFAB), NGUI.init()]).then(async ([tree]) => {
    const slot = find(tree, SELECTION_SLOT);
    if (!slot) throw new Error("Starting-skill selection slot unavailable");
    const frame = find(slot, "SelectLine").widgets.find(w => "sprite" in w);
    const outline = frameOutline(await NGUI.spritePixels(frame));
    if (selectionLoading !== pending) throw new DOMException("Resource context changed", "AbortError");
    selectionSprite = NGUI.atlasIndex()[frame.atlas || "Common"][frame.sprite];
    selectionOutline = outline;
    selectionSlot = slot;
  }).catch(error => { if (selectionLoading === pending) selectionLoading = null; throw error; });
  return selectionLoading = pending;
}
// The slot's number label holds Applied (yellow) or Selected (blue), fitted together to keep one size across source cards.
export function selectionCaptions(card = null, slot = selectionSlot) {
  const captions = slot && fitBadgeCaptions(slot, [T("status.applied"), T("status.selected")], NGUI.fitGroup, NGUI.glyphWidth, card);
  return captions && { applied: captions[0], preview: captions[1] };
}
export function frameReady() {
  return frameLoading ||= withTimeout(NGUI.layout("UIPopup_Sort"), 15000, "Popup frame")
    .then((sortPopup) => { layouts = { ...layouts, sortPopup }; })
    .catch((err) => { frameLoading = null; throw err; });
}
const optional = (name) => NGUI.layout(name).catch((err) => { console.warn("popup layout", name, err); return null; });
export function ready() {
  loading ||= Promise.all([frameReady(), optional("UICharacterCollection"), optional("UIPopup_GachaCharList.Btn_Checkbox"), Buttons.ready()])
    .then(([, collection, checkbox]) => { layouts = { ...layouts, collection, checkbox }; }, (err) => { loading = null; throw err; });
  return loading;
}

// --- the stack of open popups ---
// An entry is open while its host is in the page and shows, and its own layer (a host holding several popups) is in
// the page, so a popup closed by hiding its host leaves the stack by itself.
const stack = [];
const live = (e) => e.host.isConnected && !e.host.hidden && (!e.el || e.el.isConnected);
function prune() {
  for (let i = stack.length - 1; i >= 0; i -= 1) if (!live(stack[i])) release(stack.splice(i, 1)[0]);
}

// --- keyboard focus: one trap for every popup on the stack ---
// A popup takes the focus when it opens (the container, or what the caller focuses itself), Tab and Shift+Tab cycle
// inside the topmost one, and once it closes the focus goes back to what had it before, unless the user moved on.
const doc = () => globalThis.document;
// an element Tab can stop at: enabled, drawn, not inert; a hidden or inert part is skipped whole
function shown(el) {
  if (typeof el.getClientRects !== "function") return true;
  if (!el.getClientRects().length) return false;
  return typeof getComputedStyle !== "function" || getComputedStyle(el).visibility !== "hidden";
}
export function tabStops(box) {
  const out = [];
  const visit = (parent) => {
    for (const el of parent.children || []) {
      if (el.hidden || el.inert || el.style?.display === "none") continue;
      if (!el.disabled && el.tabIndex >= 0 && el.style?.visibility !== "hidden" && shown(el)) out.push(el);
      visit(el);
    }
  };
  visit(box);
  return out;
}
function focusIn(box) {
  const active = doc()?.activeElement;
  if (!box || box.hidden || box.inert || box.isConnected === false || (active && box.contains?.(active))) return;
  if (box.tabIndex < 0 && !box.hasAttribute?.("tabindex")) box.setAttribute?.("tabindex", "-1");
  box.focus?.({ preventScroll: true });
}
// what had the focus when the popup opened, unless it is the popup itself or nothing in particular
function opener(box) {
  const active = doc()?.activeElement;
  return active && active !== doc().body && !box.contains?.(active) ? active : null;
}
function release({ host, el, before }) {
  if (!before || before.isConnected === false) return;
  const box = el || host, active = doc()?.activeElement;
  if (active && active !== doc().body && !box.contains?.(active)) return; // the user moved on
  const above = stack.findLast(live); // another popup still open: only into it
  if (above && !(above.el || above.host).contains?.(before)) return;
  before.focus?.({ preventScroll: true });
}
// Tab inside the topmost popup: wraps at the ends, and brings the focus back in when it is outside (on the opener,
// or lost when a redraw replaced the focused control). A control that took the key itself keeps it (defaultPrevented).
export function trapKey(ev) {
  if (ev.key !== "Tab" || ev.defaultPrevented || ev.isComposing || ev.keyCode === 229 || ev.altKey || ev.ctrlKey || ev.metaKey) return;
  const t = top();
  if (!t) return;
  const box = t.el || t.host, stops = tabStops(box), active = doc()?.activeElement;
  if (!stops.length) { ev.preventDefault(); focusIn(box); return; }
  const at = stops.indexOf(active), last = stops.length - 1;
  if (at >= 0 && (ev.shiftKey ? at > 0 : at < last)) return; // inside: the browser steps to the next control
  ev.preventDefault();
  (ev.shiftKey ? stops[last] : stops[0]).focus?.();
}
addEventListener("keydown", trapKey);

// a popup opened in host (el: its own layer, when the host holds several); close(how) closes it, how being "escape",
// "backdrop" or "close"; null for a popup that must be answered with one of its buttons. Drawing the same popup again
// keeps its place on the stack.
export function opened(host, close, { el = null, loading = false } = {}) {
  if (loading && el) { el.style.visibility = "hidden"; el.inert = true; }
  prune();
  const at = stack.findIndex((e) => e.host === host && e.el === el);
  // a popup drawn again keeps its place and what to give the focus back to; a new one takes the focus in
  const entry = { host, el, close, before: at >= 0 ? stack[at].before : opener(el || host) };
  if (at >= 0) stack[at] = entry; else { stack.push(entry); if (!loading) focusIn(el || host); }
  return entry;
}
// Reveal only after the entire layer's work and decoded pictures are ready. A closed layer stays closed.
export async function reveal(layer, pending = []) {
  await Promise.all(pending);
  await NGUI.decoded([layer, ...(layer.querySelectorAll?.("*") || [])], Infinity);
  if (!layer.isConnected) return false;
  layer.style.visibility = "";
  layer.inert = false;
  if (top()?.el === layer) focusIn(layer);
  return true;
}
export function closed(host, el = null) {
  const at = stack.findIndex((e) => e.host === host && e.el === el);
  if (at >= 0) release(stack.splice(at, 1)[0]);
}
export function top() { prune(); return stack.at(-1) || null; }
// any popup open: the stack, or a host of the other popups (the consent and update popups, the settings, the display
// settings)
export const anyOpen = () => !!top() || !!document.querySelector(".popup-host:not([hidden]), .consent-host:not([hidden])");
// Holds leave every open popup usable, including stack entries with their own hosts and the consent layers.
// Escape belongs to the popup even when focus is still on the control that opened it.
export function allowsInput(ev) {
  prune();
  return (ev.key === "Escape" && anyOpen())
    || stack.some((e) => (e.el || e.host).contains(ev.target))
    || !!ev.target.closest?.(".popup-host:not([hidden]), .consent-host:not([hidden])");
}
// resolves once no popup is open and the caller's work has finished (late prompts and update offers)
export function whenIdle(every = 200, blocked = () => false) {
  return new Promise((resolve) => { const check = () => (anyOpen() || blocked() ? setTimeout(check, every) : resolve()); check(); });
}

// Escape goes to the topmost popup; not while an input method composes (keyCode 229 is the key that ends a conversion
// in WebKit), nor when a control in the popup took the key itself
export function onKey(ev) {
  if (ev.key !== "Escape" || ev.defaultPrevented || ev.isComposing || ev.keyCode === 229) return;
  const t = top();
  if (!t) return;
  ev.preventDefault();
  t.close?.("escape");
}
addEventListener("keydown", onKey);

// --- the start sequence: popups one at a time, in order. Each step resolves once its popup has closed, at once when
// it has nothing to show; one that fails is logged and the next one comes ---
export async function sequence(steps) {
  for (const [name, run] of steps) {
    try { await run(); } catch (err) { console.error("start popup", name, err); }
  }
}

// --- the selection of a list popup with several cards ticked: every card that can be ticked is, to start with ---
export const selectable = (items) => new Set(items.filter((it) => !it.disabled).map((it) => it.key));
// the selection with key ticked or unticked; a card that cannot be ticked leaves it as it is
export function toggled(selection, items, key) {
  const it = items.find((x) => x.key === key);
  const next = new Set(selection);
  if (!it || it.disabled) return next;
  if (next.has(key)) next.delete(key); else next.add(key);
  return next;
}
// the sizes of the ticked cards, added up (bytes)
export const sizeOf = (items, selection) => items.reduce((n, it) => n + (selection.has(it.key) ? it.size || 0 : 0), 0);

// --- hit areas ---
const PIVX = [0, 0.5, 1, 0, 0.5, 1, 0, 0.5, 1], PIVY = [0, 0, 0, 0.5, 0.5, 0.5, 1, 1, 1];
// a rendered node's stage rectangle: its widget's element, or, for a sprite the atlases lack, worked out from the
// node, so its button still works
export function nodeRect(view, path) {
  const e = view.nodes.get(path);
  if (!e) return null;
  if (e.els[0]) return rect(e.els[0]);
  const w = e.node.widgets.find((x) => "w_" in x);
  if (!w) return null;
  const ww = w.w_ * view.scale, hh = w.h_ * view.scale, p = w.pivot ?? 4;
  return [e.stage[0] - PIVX[p] * ww, e.stage[1] - PIVY[p] * hh, ww, hh];
}
// an invisible button over a rendered node; name: its accessible name (the caption it covers), action: what it does
// (data-action, for checks)
export function hit(host, view, path, fn, { name = "", action = "", z = 80 } = {}) {
  const r = nodeRect(view, path);
  if (!r) return null;
  const b = hitButton(host, r, fn, name, z);
  if (action) b.dataset.action = action;
  return b;
}

// --- the frame ---
// The Sort branches are resized to the shared buttonL control. Captions keep two display lines inside its padding.
export const BUTTON_TEXT_H = CONTROL.buttonL.h - SPACE.m;
export const FRAME_Y = POPUP_FRAME.frameY;

// --- the footer: one set of numbers for every popup frame (UIPopup_Sort/Center/Popup/Btn) ---
export const footerY = (h) => FRAME_Y - h / 2 + POPUP_FRAME.footer.bottom + CONTROL.buttonL.h / 2;
export const footerTop = (h, n = 1) => footerY(h) + CONTROL.buttonL.h / 2;
export function footerScale(n, w = Infinity) {
  return n < 3 ? 1 : Math.min(1, (w - 2 * POPUP_FRAME.footer.side - POPUP_FRAME.footer.gap * (n - 1)) / n / CONTROL.buttonL.minW);
}
export const footerGap = (n) => POPUP_FRAME.footer.gap;
export function footerSlots(n, w) {
  if (!n) return { scale: 1, xs: [] };
  const scale = footerScale(n, w);
  w = Number.isFinite(w) ? w : n * CONTROL.buttonL.minW * scale + (n - 1) * footerGap(n);
  const boxes = row(Array.from({ length: n }, () => ({ w: CONTROL.buttonL.minW * scale, h: CONTROL.buttonL.h })),
    { w, gap: footerGap(n), justify: "center" }).boxes;
  return { scale, xs: boxes.map(b => toNode(b, 4, { w, h: CONTROL.buttonL.h, cx: 0, cy: 0 })[0]) };
}
const CAPTION = { fs: TYPE.display.fs, fsMin: TYPE.display.min, w: CONTROL.buttonL.minW - 2 * CONTROL.buttonL.pad,
  lines: 2, h: BUTTON_TEXT_H };
function checkFooterFits(fits, { site = null, locale = globalThis.document?.documentElement?.lang } = {}) {
  if (site && fits.some((fit) => fit.overflow || fit.fs < captionMinimum(site, locale, CAPTION.fsMin)))
    throw new RangeError(`Popup footer cannot fit: ${site} (${locale})`);
  return fits;
}
export const footerFit = (captions, options = {}) => checkFooterFits(NGUI.fitGroup(captions, CAPTION.fs, CAPTION.fsMin,
  options.width ?? CAPTION.w, CAPTION.lines, { maxH: CAPTION.h }), options);
export function footerLabels(widgets, captions, options = {}) {
  const width = options.width ?? CAPTION.w;
  widgets.forEach((widget, i) => Object.assign(widget, { w_: Array.isArray(width) ? width[i] : width, h_: CAPTION.h }));
  return checkFooterFits(NGUI.fitLabels(widgets, captions, CAPTION.fs, CAPTION.fsMin,
    options.width ?? CAPTION.w, CAPTION.lines, { maxH: CAPTION.h }), options);
}

// the game's popup frame at a size: backdrop, title, close button and, when given, its Cancel and OK buttons with
// these labels on centred buttonL bodies, with the shared gap and bottom inset.
//   buttons: [{label, primary, name}] instead of ok / cancel: named choices in a row (one centred, two as OK and Cancel,
//            three or more share the available width); a node is Choice_<index> unless it names itself
//   close: false for a popup that must be answered with a button (no close button)
export function popupFrame(title, w, h, { ok = null, cancel = null, close = true, buttons = null, captionSite = null } = {}) {
  ({ w, h } = frameGeometry(w, h));
  const t = clone(find(layouts.sortPopup, "Center"));
  const popup = find(t, "Popup");
  popup.children = popup.children.filter((c) => ["Base", "Btn"].includes(c.name));
  const bg = find(popup, "Base/BG"), local = { w, h, cx: 0, cy: 0 };
  Object.assign(bg.widgets[0], { w_: w, h_: h });
  // The corner texture keeps its own prefab inset and dimensions.
  find(bg, "Texture_Top").pos = [-w / 2 + 248, h / 2 - 66];
  const titleNode = find(bg, "Label_Title_Locale");
  const titleBox = container({ w, h, padding: [POPUP_FRAME.titleTop, SPACE.xxxl, 0, SPACE.xxxl] }).inner;
  const tf = textItem(NGUI, title, TYPE.display, { maxW: titleBox.w, lines: 1 });
  titleNode.pos = toNode({ ...titleBox, h: tf.h }, 4, local);
  Object.assign(titleNode.widgets[0], { text: tf.text, fontSize: tf.fs, w_: titleBox.w, h_: tf.h, pivot: 4 });
  find(titleNode, "Line").widgets[0].w_ = glyphsOf(tf.text, tf.fs) + SPACE.s;
  const closeBtn = find(bg, "Btn_Close"), size = POPUP_FRAME.close.size;
  closeBtn.pos = toNode({ x: w - POPUP_FRAME.close.right - size, y: POPUP_FRAME.close.top, w: size, h: size }, 4, local);
  Object.assign(closeBtn.widgets[0], { w_: size, h_: size, pivot: 4 });
  closeBtn.active = close;
  const btn = find(popup, "Btn"), primary = clone(find(btn, "Btn_Ok")), secondary = clone(find(btn, "Btn_Cancel"));
  const actions = buttons?.length ? buttons : [
    ...(cancel ? [{ label: cancel, name: "Btn_Cancel" }] : []), ...(ok ? [{ label: ok, name: "Btn_Ok", primary: true }] : []),
  ];
  const { scale, xs } = footerSlots(actions.length, w), bodyW = CONTROL.buttonL.minW * scale;
  btn.active = !!actions.length;
  btn.pos = [0, footerY(h)];
  // Keep both named branches for callers that overlay a disabled confirm button.
  btn.children = actions.length ? actions.map((action, index) => {
    const child = clone(action.primary ? primary : secondary);
    child.name = action.name || `Choice_${index}`;
    child.active = true;
    child.pos = [xs[index], 0];
    child.scale = [1, 1];
    child.affirmative = !!action.primary && !action.semantic;
    child.actionSemantic = action.semantic || (action.primary ? "confirm" : "secondary");
    const body = find(child, "BG (1)");
    body.pos = [0, 0];
    resizeButton(body, bodyW, CONTROL.buttonL.h);
    find(child, "Label_Locale").pos = [0, 0];
    return child;
  }) : [];
  footerLabels(btn.children.map(child => find(child, "Label_Locale").widgets[0]), actions.map(action => action.label),
    { site: captionSite, width: bodyW - 2 * CONTROL.buttonL.pad });
  if (!buttons?.length) for (const [name, source] of [["Btn_Ok", primary], ["Btn_Cancel", secondary]]) {
    if (!find(btn, name)) { source.active = false; btn.children.push(source); }
  }
  find(t, "BlackBg/BlackBgClickArea").widgets.find((x) => "sprite" in x).color = BACKDROP;
  return t;
}

// popupFrame's confirm button as the game's disabled one, alone at the frame's place: a popup that can block its confirm
// lays this over the live one while it is blocked (the list popup draws its blocked primary button the same way)
export function disabledConfirm(frame) {
  const t = clone(frame), popup = find(t, "Popup"), btn = find(popup, "Btn");
  btn.children = [Buttons.disable(find(btn, "Btn_Ok"))];
  popup.children = [btn];
  t.children = t.children.filter((c) => c.name === "Popup");
  return t;
}

// a click on a popup's backdrop or Escape closes it, as the game's PopupClose does; its body takes the clicks that
// miss its controls. close null: the popup must be answered with a button (the backdrop and Escape do nothing).
// Puts the popup on the stack.
export function closable(view, host, close, { el = null } = {}) {
  const back = view.nodes.get("Center/BlackBg/BlackBgClickArea")?.els[0];
  if (back) {
    back.style.pointerEvents = "auto";
    if (close) back.addEventListener("click", () => close("backdrop"));
  }
  const body = view.nodes.get("Center/Popup/Base/BG")?.els[0];
  if (body) body.style.pointerEvents = "auto";
  return opened(host, close, { el });
}

// a scrolling card area with the game list's scrollbar colours
export function scrollArea(host, [l, t, w, h], contentH, { horizontal = false, contentW = w } = {}) {
  const area = document.createElement("div");
  area.className = "popup-cards";
  const viewport = container({ x: l, y: t, w, h }).inner;
  Object.assign(area.style, { left: `${viewport.x}px`, top: `${viewport.y}px`, width: `${viewport.w}px`, height: `${viewport.h}px` });
  const inner = document.createElement("div");
  inner.className = "popup-cards-inner";
  inner.style.height = `${contentH}px`;
  if (horizontal) {
    Object.assign(area.style, { overflowX: "auto", overflowY: "hidden" });
    inner.style.width = `${contentW}px`;
    horizontalScroll(area, w, contentW);
  }
  area.appendChild(inner);
  host.appendChild(area);
  if (horizontal ? contentW > w + 1 : contentH > h + 1) {
    const track = scrollbarTrack([l, t, w, h], S, horizontal);
    const pending = scrollbar(host, track, { horizontal }).then((bar) => {
      const sync = () => horizontal ? bar.sync(w, contentW, area.scrollLeft || 0) : bar.sync(h, contentH, area.scrollTop || 0);
      area.addEventListener("scroll", sync);
      sync();
    }).catch((err) => console.warn("popup scrollbar unavailable", err));
    inner.ready = pending;
  }
  return inner;
}

// the gacha list's checkbox (Btn_Checkbox: its white plate, the box and its tick, a label after it), at x (its left
// edge), y; the tick shows when on. Without the prefab, the same pieces drawn from its numbers.
const CHECK_H = 104;
// Btn_Checkbox drawn from its numbers (without the prefab): the plate, and the box with its tick in Type_ON, without in Type_OFF
function checkboxPrefab() {
  const box = (ticked) => node("Check", [-120, 8], [sprite("Common_Bg_Raius10px", 74, 72, { incline: 10, depth: 25, color: "#e4f3f8/1.00" })], [
    node("Line", [0, 0], [sprite("Common_Line_Raius10px", 74, 72, { incline: 10, depth: 30, color: "#96b6c2/1.00" })]),
    ...(ticked ? [node("Icon", [0.4, -1.8], [sprite("Common_Icon_Check_B", 59, 58, { depth: 35 })])] : []),
  ]);
  return node("Btn_Checkbox", [0, 0], [sprite("Resource_Bg_Radius10px", 798, 110, { incline: 10, pivot: 3, depth: 20 })],
    [node("Type_ON", [178, -3.2], [], [box(true)]), node("Type_OFF", [178, -3.2], [], [box(false)])]);
}
export function checkbox(name, x, y, caption, on, maxW) {
  const fit = NGUI.fitText(caption, TYPE.body.fs, TYPE.body.min, maxW - 131 - SPACE.xl, 2, { maxH: CHECK_H });
  const f = textItem(NGUI, fit.text, { fs: fit.fs, min: fit.fs }, { maxW: maxW - 131 - SPACE.xl, lines: 2 });
  const parts = row([{ w: 131, h: CHECK_H }, { w: f.w + SPACE.m, h: f.h }], { gap: SPACE.m });
  const w = parts.w; // The checkbox branch keeps its prefab check plate and its 131-unit label origin.
  const cb = layouts?.checkbox ? clone(layouts.checkbox) : checkboxPrefab();
  cb.name = name;
  cb.pos = [x, y];
  Object.assign(cb.widgets.find((x2) => "sprite" in x2), { w_: w, h_: CHECK_H });
  // one state drawn (Type_ON's box with its tick); the tick is shown or hidden in place
  cb.children = cb.children.filter((c) => c.name === "Type_ON");
  for (const c of cb.children) {
    c.active = true;
    c.children = (c.children || []).filter((k) => k.name === "Check");
    const icon = find(c, "Check/Icon");
    if (icon) icon.active = true;
  }
  cb.children.push(node("Label", [131, 0], [label(f.text, f.fs, "#484e52/1.00", w - 131, f.h, { pivot: 3, depth: 31 })]));
  return { tree: cb, w, on, caption: f };
}
// wires a drawn checkbox: a click flips it (state.checked) and shows or hides the tick
export function wireCheck(host, view, path, state, onChange) {
  const tick = view.nodes.get(`${path}/Type_ON/Check/Icon`)?.els[0];
  const show = () => { if (tick) tick.style.visibility = state.checked ? "" : "hidden"; };
  show();
  const b = hit(host, view, path, () => {
    state.checked = !state.checked;
    show();
    b?.setAttribute("aria-checked", String(state.checked));
    onChange?.(state.checked);
  }, { name: state.label, action: "check" });
  if (b) { b.setAttribute("role", "checkbox"); b.setAttribute("aria-checked", String(state.checked)); }
  return b;
}

// --- the list popup ---
export const LIST_CARD_NAME = Object.freeze({ fs: 34, fsMin: 24, lines: 2, padY: 22, pitch: 1.2 });
export function listLayout(check = false, kind = null) {
  const native = switchGrid(kind).card;
  // One complete native card, its name band and the toolbar determine the minimum useful viewport. Additional cards scroll.
  const cardHeight = native.h + Math.max(native.bar, LIST_CARD_NAME.padY + LIST_CARD_NAME.lines * LIST_CARD_NAME.fs * LIST_CARD_NAME.pitch);
  const need = POPUP_FRAME.contentTop + CONTROL.buttonS.h + 5 * SPACE.m + cardHeight
    + POPUP_FRAME.footer.bottom + CONTROL.buttonL.h + SPACE.m;
  const frame = frameGeometry("XL", Math.max(need, POPUP_FRAME.XL.h)), content = popupContent(frame.w, frame.h);
  const panels = grid(content);
  const detailBox = { ...panels.span(4), h: content.h - (check ? CHECK_H + SPACE.m : 0) };
  const bounds = box => {
    const [l, t] = toNode(box, 0, frame);
    return { l, r: l + box.w, t, b: t - box.h };
  };
  return { frame, content, left: bounds(detailBox), list: bounds(panels.span(8, 4)),
    // Card artwork and the native card-placement helper retain their prefab envelope and scroll pitch.
    card: { w: 250, h: 226, bar: 68 }, pitch: [284, 350] };
}
export function listCardBox(layout, { searching = false, horizontal = false } = {}) {
  const b = layout.list, f = layout.frame;
  const panel = { x: b.l + f.w / 2, y: f.cy + f.h / 2 - b.t, w: b.r - b.l, h: b.t - b.b };
  const paper = container({ ...panel, padding: [CONTROL.buttonS.h + 2 * SPACE.m
    + (searching && !horizontal ? CONTROL.buttonS.h + SPACE.m : 0), SPACE.m, SPACE.m, SPACE.m] }).inner;
  // The grid owns the card viewport; native card envelopes and their un-stretched pitch are placed inside it.
  return grid(paper).span(12);
}
const LP = listLayout();
// the student list's tools at the top right of the list: order, sort, search and, with segs, the segment filter
function toolbar(right, y, { segs = null, sortKey = "sort.default", active = 0, ascending = true, filtered = false, check = null, sortKeys = [sortKey] } = {}) {
  if (check) return checkedToolbar(right, y, { segs, sortKey, sortKeys, active, ascending, filtered, check });
  const menu = find(layouts.collection, "SeletControl/TopMenu");
  const kids = [];
  const toolRow = row([{ w: 150, h: CONTROL.buttonS.h }, { w: 420, h: CONTROL.buttonS.h }, { w: 243, h: CONTROL.buttonS.h }],
    { x: LP.list.l + SPACE.m, y: -y - CONTROL.buttonS.h / 2, w: right - LP.list.l - SPACE.m,
      gap: SPACE.m, justify: "end" });
  const local = { w: 0, h: 0, cx: 0, cy: 0 };
  const centers = toolRow.boxes.map(box => toNode(box, 4, local));
  // Corrections from each complete collection tool's origin to its visual body centre are prefab-owned.
  const x = toolRow.boxes[0].x - SPACE.m;
  const order = clone(find(menu, "OrderBtn"));
  order.pos = [centers[2][0] + 45.1, centers[2][1]];
  const sort = clone(find(menu, "SortBtn"));
  const sortW = 420;
  order.children = order.children.map((child) => child.name === "Btn_Normal" ? orderBody(layouts.collection, ascending) : child);
  const st = fitText(T(sortKey), 56, 36, sortW - 150);
  sort.children = sort.children.map((child) => child.name === "Btn_Normal" ? sortBody(layouts.collection, st, sortW, filtered) : child);
  sort.pos = [centers[1][0] + 83, centers[1][1]];
  const search = clone(find(menu, "SearchBtn"));
  search.pos = [centers[0][0] - 89, centers[0][1]];
  kids.push(order, sort, search);
  let segBox = null;
  if (segs) {
    const filter = clone(find(menu, "FilterBtn"));
    const segW = 230, sx = segW / 328;
    filter.scale = [sx, 1];
    // the three segments share one size
    const segFits = NGUI.fitGroup(segs.map((key) => T(key)), 44, 26, segW - 30, 2, { maxH: 96 });
    [["All", segs[0]], ["Main", segs[1]], ["Support", segs[2]]].forEach(([k, key], i) => {
      const seg = find(filter, k);
      for (const state of seg.children) {
        for (const c of state.children || []) {
          const tex = c.widgets.find((w) => "texture" in w);
          if (tex) {
            const f = segFits[i];
            c.widgets = [label(f.text, f.fs, tex.color, (segW - 20) / sx, Math.max(60, f.fs * f.lines * 1.3), { depth: 9 })];
          }
        }
        state.active = (state.name === "Btn_Pressed") === (i === active);
      }
    });
    filter.pos = [x - 3 * segW + 123 * sx + 164 * sx - 6, y + 50.2];
    segBox = { right: x, segW, y };
    kids.push(filter);
  }
  return { tree: node("Toolbar", [0, 0], [], kids), segBox };
}

// the fit of that toolbar: the checkbox's ``label``, the ``segs`` captions' keys and the sort button's ``sortKeys`` (``sortKey``:
// the one shown), between the list's left edge and ``right``
export function checkedToolbarFit(right, { segs, sortKey, sortKeys, label }) {
  return toolbarFit({ left: LP.list.l + SPACE.m, right, filters: [segs.map((key) => T(key))], sorts: sortKeys.map((key) => T(key)),
    sortAt: sortKeys.indexOf(sortKey), check: label });
}
// The toolbar with a checkbox (Switch 3D Model's "Show other students", BL-131) is the student roster's: the checkbox
// first, the three segments, search, sort and order, fitted by the roster's own steps (roster-widgets.js toolbarFit; the
// sort button as wide as its longest key). Returns the tree, where the segments and the checkbox stand for the hit areas.
function checkedToolbar(right, y, { segs, sortKey, sortKeys, active, ascending, filtered, check }) {
  const lay = checkedToolbarFit(right, { segs, sortKey, sortKeys, label: check.label });
  const tree = toolbarTree(layouts.collection, layouts.checkbox || checkboxPrefab(), {}, false, lay,
    { filters: [{ name: "ModFilter", active }], checked: !!check.checked, filtered, ascending });
  tree.name = "Toolbar";
  tree.pos = [0, y - TOOLBAR_CENTRE_Y];
  tree.children = tree.children.filter((child) => ["Menubar", "ModFilter", "SearchBtn", "SortBtn", "OrderBtn"].includes(child.name));
  const bar = find(tree, "Menubar");
  bar.children = bar.children.filter((child) => child.name === "Merge");
  // the segments span the filter's right edge back three widths, the same slots the plain toolbar's hit areas count from
  return { tree, lay, segBox: { right: lay.seg1 + 6, segW: lay.segW, y }, check: { x: lay.cbLeft + lay.cbW / 2, w: lay.cbW, y } };
}

// The list and details panels occupy the XL content grid; its footer uses the shared centred group.
export function listFrame(title, buttons, closeButton, check = false, kind = null) {
  const layout = listLayout(check, kind);
  const t = popupFrame(title, layout.frame.w, layout.frame.h, { close: closeButton,
    buttons: buttons.map((b, i) => ({ ...b, name: `Btn_${i}` })) });
  const popup = find(t, "Popup"), btn = find(popup, "Btn");
  btn.children = btn.children.map((child, i) => buttons[i].greyed ? Buttons.disable(child) : child);
  const panel = (name, b, tone) => node(name, [(b.l + b.r) / 2, (b.t + b.b) / 2],
    [sprite("Common_Bg_Raius10px", b.r - b.l, b.t - b.b, { depth: 3, color: tone })]);
  const b = layout.list;
  const paper = container({ x: b.l, y: -b.t, w: b.r - b.l, h: b.t - b.b,
    padding: [CONTROL.buttonS.h + SPACE.m, SPACE.m, SPACE.m, SPACE.m] }).inner;
  popup.children.push(panel("LeftPanel", layout.left, "#ffffff/0.00"), panel("ListPanel", b, "#d7eaf4/1.00"),
    node("ListPaper", [paper.x + paper.w / 2, -paper.y - paper.h / 2],
      [sprite("Common_Bg_Raius10px", paper.w, paper.h, { depth: 4, color: "#f4fcfe/1.00" })]));
  return t;
}

// one card of the list: the game's card body, the picture clipped to its window, a name bar under it. A card that
// cannot be chosen (dim) is darkened like the roster's not-owned cards, its picture without colour; its badge (In Use,
// or the caller's) stays bright.
export function listCard(it, x, y, selected, card = LP.card, kind = null) {
  const { w, h, bar } = card;
  // The same upright image must reach both inclined edges even in a tall portrait window.
  const overscan = Math.max(.15, (h - 26) * TAN10 / (2 * (w - 16)));
  const kids = [
    node("Thumb", [1, 4], [{ clipImage: it.thumb, placeholder: T("import.previewUnavailable"), w_: w - 16, h_: h - 26, pivot: 4, depth: 8, incline: 10, radius: 18, color: "#ffffff/1.00", imgSize: kind === "model" ? "contain" : "cover", imgPosition: "center 30%",
      imgLeft: `${-overscan * 100}%`, imgWidth: `${(1 + 2 * overscan) * 100}%` }]),
    node("Line", [1, 4], [sprite("Common_Line_Raius20px", w - 12, h - 18, { incline: 10, depth: 9, color: "#ffffff/1.00" })]),
  ];
  const lbl = markedLabel(it.label, { fs: LIST_CARD_NAME.fs, fsMin: LIST_CARD_NAME.fsMin, color: "#ffffff/1.00", maxW: w - 56 - (it.nameEdit ? 60 : 0), mark: false, maxLines: LIST_CARD_NAME.lines });
  const barH = Math.max(bar, LIST_CARD_NAME.padY + lbl.lines * lbl.fs * LIST_CARD_NAME.pitch), barY = -h / 2 - 10 - barH / 2;
  kids.push(node("Bar", [barY * TAN10 + 4, barY], [sprite("Common_Bg_Raius10px", w - 22, barH, { incline: 10, depth: 8, color: "#2c4564/1.00" })], [
    node("Name", [it.nameEdit ? -30 : 0, 0], [], lbl.nodes),
    ...(it.nameEdit ? [node("EditName", [w / 2 - 50, 0], [sprite("Cafe_Icon_NameEdit", 42, 42, { type: 0, depth: 12 })])] : []),
  ]));
  const corner = (dx, dy) => [dx + dy * TAN10, dy]; // a point inside the slanted card
  if (it.dim) kids.push(node("Dim", [1, 4], [sprite("Common_Bg_Raius10px", w - 12, h - 18, { incline: 10, depth: 24, color: "#000000/0.55" })]));
  const badge = it.badge || (it.inUse ? { text: T("popup.inUse"), color: COLOUR.inUse, ink: COLOUR.inUseInk } : null);
  for (const [name, mark, y] of [["Using", badge, h / 2 - 30], ["Tag", it.tag, -h / 2 + 36]]) {
    if (!mark) continue;
    // as wide as its word needs, from the corner, up to most of the card
    const bf = fitText(mark.text, 26, 20, w - 70, 1), bw = Math.max(116, glyphsOf(bf.text, bf.fs) + 24);
    kids.push(node(name, corner(-w / 2 + 12 + bw / 2, y), [sprite("Common_Bg_Raius10px", bw, 42, { depth: 26, color: `${mark.color}/1.00`, incline: 10 })],
      [node("Text", [0, 0], [label(bf.text, bf.fs, `${mark.ink || COLOUR.inUseInk}/1.00`, bw - 4, 40, { depth: 27 })])]));
  }
  if (it.fav) kids.push(node("Pin", corner(w / 2 - 38, -h / 2 + 36), [sprite("Common_Btn_Pin_01", 54, 54, { type: 0, depth: 26 })]));
  if (it.recruit) kids.push(node("Recruit", corner(-w / 2 + 34, -h / 2 + 40), [sprite(RECRUIT.sprite, 62 / RECRUIT.ratio, 62, { atlas: RECRUIT.atlas, type: 0, depth: 26 })]));
  // In-use mods already have a badge; a picked card's check takes the corner.
  if (it.isNew && !it.inUse && !it.applied && !selected) kids.push(DOT(corner(w / 2 - 4, h / 2 - 6), 34));
  if (selected) kids.push(node("Select", [1, 4], [sprite("Card_Ability_Select_Line", w + 4, h - 8, { incline: 10, depth: 20 })],
    [node("Check", [w / 2 - 28 + (h / 2 - 30) * TAN10, h / 2 - 30], [sprite("Card_Ability_Select_Line_Icon", 66, 65, { type: 0, depth: 21 })])]));
  const tree = node(`Card_${it.key}`, [x, y], [sprite("Common_Btn_BG", w, h, { incline: 10, depth: 7, color: "#ffffff/1.00" })], kids);
  if (typeof it.applied === "boolean") {
    // Keep the existing list's measured envelope, hit boxes and scroll pitch when replacing its decoration.
    tree.layoutChildren = [...kids];
    tree.children = kids.filter(n => n.name !== "Select");
    const caption = (it.applied || selected) ? selectionCaptions(card)?.[it.applied ? "applied" : "preview"] : null;
    const frame = selectedFrame(selectionSlot, card, it.applied, selected, caption);
    if (frame) {
      const window = selectionArtWindow(frame, selectionSprite, selectionOutline);
      const source = find(tree, "Thumb"), image = { ...source.widgets[0] };
      const thumb = { ...source, pos: [window.x, window.y], widgets: [image] };
      // Retain the pre-existing layout envelope; only the art underneath the selected frame changes.
      Object.assign(image, { w_: window.width, h_: window.height, radius: 0 });
      const overscan = Math.max(.15, window.height * TAN10 / (2 * window.width));
      Object.assign(image, { imgLeft: `${-overscan * 100}%`, imgWidth: `${(1 + 2 * overscan) * 100}%` });
      tree.children = tree.children.filter(n => n.name !== "Line").map(n => n === source ? thumb : n);
      tree.children.push(frame);
    }
  }
  return tree;
}

// The detail actions retain their existing 40–26 caption range and slanted-body inset. Their shared body height is buttonS.
const ACTION_CLEAR = 28, ACTION_TEXT_H = 96;
// The small button body's soft drop shadow under its solid art: 14 of its 176 rows (Btn_Cancel/BG (1) as drawn).
const ACTION_SHADOW = 14 / 176;
// Chip art and pitch remain UICharacterDetail's own; row/stack place complete chips inside the content column.
const CHIP_MIN_GAP = SPACE.xs;
export function chipPlaces(count, panelW) {
  const slant = CHIP.h * TAN10;
  let perRow = 1;
  while ((perRow + 1) * CHIP.w + perRow * CHIP_MIN_GAP + slant <= panelW) perRow += 1;
  const boxes = [];
  for (let first = 0; first < count; first += perRow) {
    const inRow = Math.min(perRow, count - first);
    const gap = inRow > 1 ? Math.min(CHIP.gap, (panelW - slant - inRow * CHIP.w) / (inRow - 1)) : 0;
    const chips = row(Array.from({ length: inRow }, () => ({ w: CHIP.w, h: CHIP.h })), { w: panelW, gap, justify: "center" });
    boxes.push(...chips.boxes.map(b => ({ row: first / perRow, x: b.x + b.w / 2 - panelW / 2 })));
  }
  return boxes;
}
// the details panel: title bar, the picture, name, kind chips, fields, actions (exported for the tests, like chipPlaces)
export function detailsTree(it, title, fields, actions, portraitTexture = null, kind = null, panel = LP.left) {
  const w = panel.r - panel.l, cx = (panel.l + panel.r) / 2;
  const frame = { w, h: 0, cx, cy: panel.t }, content = container({ w, h: panel.t - panel.b }).inner;
  const kids = [], bands = [];
  const titleText = textItem(NGUI, title, TYPE.heading, { maxW: w - 2 * SPACE.xxl, lines: 1 });
  const titleH = Math.max(73, titleText.h); // The section bar and its decoration keep their prefab heights.
  const titleNode = node("Title_Bg", [cx, 0], [sprite("Common_Bg_Raius10px", w - SPACE.m, titleH,
    { depth: 5, color: "#2d4c72/1.00", incline: 10 })], [
    node("Deco", [-(w - SPACE.m) / 2 + SPACE.l, 0], [sprite("Common_BGDeco", 68, 72, { depth: 6, color: "#2a5886/1.00", type: 0 })]),
    node("Deco (1)", [(w - SPACE.m) / 2 - SPACE.l, 0], [sprite("Common_BGDeco", 68, 72, { depth: 6, color: "#2a5886/1.00", flip: 3, type: 0 })]),
    node("Label", [0, 0], [label(titleText.text, titleText.fs, "#ffffff/1.00", w - 2 * SPACE.xxl, titleText.h, { depth: 7 })]),
  ]);
  kids.push(titleNode); bands.push({ name: "Title_Bg", w, h: titleH });
  // A 3D model's picture sits beside its name and chips, its fields full width below (BL-133), so the panel fits
  // without scrolling and the picture keeps a usable size.
  const side = !portraitTexture && kind === "model";
  const columns = grid(content), pictureColumn = portraitTexture || side ? columns.span(5) : content;
  const infoColumn = portraitTexture || side ? columns.span(7, 5) : content;
  const infoW = infoColumn.w, nameRoom = infoW - 2 * SPACE.m - (it.nameEdit ? CONTROL.icon.h + SPACE.m : 0);
  const nameFit = messageText(it.full, TYPE.heading.fs, TYPE.heading.min, nameRoom, 2, true);
  const name = textItem(NGUI, nameFit.text, { fs: nameFit.fs, min: nameFit.fs }, { maxW: nameRoom, lines: Infinity });
  const info = [{ name: "Name", w: infoW, h: name.h }];
  kids.push(node("Name", [0, 0], [label(name.text, name.fs, "#2d4664/1.00", nameRoom, name.h, { depth: 8, ellipsis: !!it.original, ariaLabel: it.full })]));
  if (it.nameEdit) kids.push(node("EditName", [0, 0], [sprite("Cafe_Icon_NameEdit", CONTROL.icon.h, CONTROL.icon.h, { type: 0, depth: 9, color: "#2d4664/1.00" })]));
  if (it.original) {
    const original = textItem(NGUI, it.original, TYPE.micro, { maxW: infoW - 2 * SPACE.m, lines: 2 });
    kids.push(node("OriginalName", [0, 0], [label(original.text, original.fs, "#2d4664/0.65", infoW - 2 * SPACE.m, original.h, { depth: 8, ellipsis: true, ariaLabel: it.original })]));
    info.push({ name: "OriginalName", w: infoW, h: original.h });
  }
  const using = it.inUseText || (it.inUse ? T("popup.inUse") : null);
  const chips = it.chips || [it.tag?.text || (it.recruit ? T("type.recruit") : it.kind === "mod" ? T("type.added") : T("type.noRecruit")), ...(using ? [using] : [])];
  const places = chipPlaces(chips.length, infoW), rows = chips.length ? Math.max(...places.map(p => p.row)) + 1 : 0;
  const dark = !it.chips && it.recruit;
  chips.forEach((caption, i) => kids.push(node(`Chip_${i}`, [0, 0],
    [sprite("Common_Bg_Raius10px", CHIP.w, CHIP.h, { incline: 10, depth: 6, color: i === 0 && dark ? "#2c4564/1.00" : "#ffffff/1.00" })], [
      node("Line", [0, 0], [sprite("Common_Line_Raius10px", CHIP.w, CHIP.h, { incline: 10, depth: 7, color: "#c0c9d4/1.00" })]),
      node("Text", [0, 0], [], markedLabel(caption, { fs: CHIP.fs, fsMin: CHIP.fsMin,
        color: i === 0 && dark ? "#ffffff/1.00" : "#2c4564/1.00", maxW: CHIP.w - CHIP.inset, mark: i === 0 && dark, depth: 8 }).nodes),
    ])));
  if (rows) info.push({ name: "Chips", w: infoW, h: rows * CHIP.h + (rows - 1) * SPACE.s });
  // The light stats box keeps its own padding, row sizes, caption/value types and decoration. A portrait's narrow column
  // stacks each caption and value as successive full-width rows; other sources retain the paired columns.
  const fieldRows = portraitTexture ? fields.flatMap(([caption, value, colour]) => [{ value: caption }, { value: String(value), colour }])
    : fields.map(([caption, value, colour], i) => ({ caption, value: String(value), colour, ariaLabel: String(value), cut: i === 2 ? 2 : 0 }));
  const fieldW = side ? content.w : infoW;
  const fieldBox = fields.length ? statsBox({ holder: "panel", width: fieldW, at: [0, 0], name: "Fields", rows: fieldRows }) : { nodes: [], height: 0 };
  if (portraitTexture) for (const part of fieldBox.nodes) {
    const match = /^V_(\d+)$/.exec(part.name);
    if (match) { const i = Number(match[1]); part.name = `${i % 2 ? "V" : "F"}_${Math.floor(i / 2)}`; }
  }
  kids.push(...fieldBox.nodes);
  const fieldBand = fields.length ? { name: "Fields", w: fieldW, h: fieldBox.height } : null;
  if (fieldBand && !side) info.push(fieldBand);
  const infoStack = layoutStack(info, { w: infoW, gap: SPACE.m });

  // The model's square is one size for every candidate: what the title, the actions and fields with the file name on
  // three lines (the others on one) leave, its 10 degree incline within its column. Only longer fields shrink it.
  const roomFor = (fieldsH) => content.h - titleH - fieldsH - (actions.length ? CONTROL.buttonS.h : 0)
    - SPACE.m * (1 + (fields.length ? 1 : 0) + (actions.length ? 1 : 0));
  const reference = side && fields.length ? boxHeight(fields.map((_, i) => (i === 0 ? 3 : 1))) : 0;
  const pictureW = portraitTexture ? pictureColumn.w
    : side ? Math.max(0, Math.min(pictureColumn.w / (1 + TAN10), roomFor(reference), roomFor(fieldBand?.h || 0))) : w - 2 * SPACE.m;
  const texture = portraitTexture && clone(portraitTexture), native = texture?.widgets.find(widget => "texture" in widget);
  const portraitCard = texture && switchGrid("spr").card; // Keep the portrait card's native aspect in its preview column.
  const aspect = portraitCard ? portraitCard.w / portraitCard.h : kind === "model" ? 1 : 16 / 9, pictureH = pictureW / aspect;
  const source = typeof it.big === "function" ? it.big() : it.big;
  const picture = node("Picture", [0, 0], [], []);
  if (texture) {
    texture.scale = [1, 1];
    Object.assign(native, { clipImage: source, placeholder: T("import.previewUnavailable"), w_: pictureW, h_: pictureH,
      imgSize: "cover", imgPosition: "center", imgLeft: "0", imgWidth: "100%", incline: 0, radius: 0 });
    texture.pos = toNode({ x: 0, y: 0, w: pictureW, h: pictureH }, native.pivot, { w: pictureW, h: pictureH, cx: 0, cy: 0 });
    picture.children = [texture];
  } else {
    picture.widgets = [sprite("Common_Btn_BG", pictureW, pictureH, { incline: 10, depth: 6, color: "#ffffff/1.00" })];
    picture.children = [node("Thumb", [0, 0], [{ clipImage: source, placeholder: T("import.previewUnavailable"),
      w_: pictureW - SPACE.m, h_: pictureH - SPACE.m, pivot: 4, depth: 7, incline: 10, radius: 22,
      color: "#ffffff/1.00", imgSize: kind === "model" ? "contain" : "cover", imgPosition: "center 18%" }]),
      node("Line", [0, 0], [sprite("Common_Line_Raius20px", pictureW - SPACE.s, pictureH - SPACE.s,
        { incline: 10, depth: 8, color: "#ffffff/1.00" })])];
  }
  kids.push(picture);
  if (portraitTexture) bands.push({ name: "Portrait", w, h: Math.max(pictureH, infoStack.h) });
  else if (side) bands.push({ name: "Portrait", w, h: Math.max(pictureH, infoStack.h) }, ...(fieldBand ? [fieldBand] : []));
  else bands.push({ name: "Picture", w, h: pictureH }, ...infoStack.boxes);

  const gap = SPACE.m, bw = (w - 2 * SPACE.m + gap) / Math.max(1, actions.length);
  const fits = NGUI.fitRow(actions.map(([caption]) => caption), 40, 26, actions.length * (bw - gap), ACTION_CLEAR, 2, { maxH: ACTION_TEXT_H });
  const actionRow = row(fits.map(f => ({ w: f.width, h: CONTROL.buttonS.h })), { x: SPACE.m, w: w - 2 * SPACE.m, gap, justify: "center" });
  actions.forEach(([caption, , disabled, semantic = "secondary"], i) => {
    let body = clone(find(layouts.sortPopup, "Center/Popup/Btn/Btn_Cancel/BG (1)")), ink = "#2d5164/1.00";
    if (disabled) {
      const grey = Buttons.disable(node("Action", [0, 0], [], [body]));
      body = clone(find(grey, "BG (1)")); ink = find(grey, "Label_Locale").widgets[0].color;
    }
    const bg = body.widgets.find(widget => "sprite" in widget), k = CONTROL.buttonS.h / bg.h_;
    body.name = "Body"; body.pos = [0, 0]; body.scale = [k, k]; resizeButton(body, fits[i].width / k);
    Object.assign(bg, { depth: 6, ...(!disabled ? { color: "#dbeef5/1.00" } : {}) });
    for (const child of body.children || []) for (const widget of child.widgets) if ("w_" in widget) widget.depth = 7;
    const action = node(`Act_${i}`, [0, 0], [], [body, node("Label", [0, 0],
      [label(fits[i].text, fits[i].fs, ink, fits[i].width - ACTION_CLEAR, ACTION_TEXT_H, { depth: 8 })])]);
    action.actionSemantic = disabled ? "secondary" : semantic; kids.push(action);
  });
  if (actions.length) bands.push({ name: "Actions", w, h: CONTROL.buttonS.h });
  const placed = layoutStack(bands, { x: content.x, y: content.y, w: content.w, gap: SPACE.m });
  // The model's actions stand on the panel's bottom, their solid art level with the list panel's (BL-133).
  const pinned = side && actions.length && placed.h <= content.h;
  if (pinned) placed.boxes.at(-1).y = content.y + content.h - CONTROL.buttonS.h * (1 - ACTION_SHADOW);
  const tree = node("Details", [0, 0], [], kids);
  const placeInfo = (box, x = 0) => {
    const target = { ...box, x: x + box.x };
    if (box.name === "Fields") {
      const [px, py] = toNode(target, 1, frame);
      for (const part of fieldBox.nodes) { part.pos[0] += px; part.pos[1] += py; }
    } else if (box.name === "Chips") chips.forEach((_, i) => {
      find(tree, `Chip_${i}`).pos = toNode({ x: target.x + infoW / 2 + places[i].x - CHIP.w / 2,
        y: target.y + places[i].row * (CHIP.h + SPACE.s), w: CHIP.w, h: CHIP.h }, 4, frame);
    });
    else {
      find(tree, box.name).pos = toNode(target, 4, frame);
      if (box.name === "Name" && it.nameEdit) find(tree, "EditName").pos = toNode({ x: target.x + infoW - SPACE.m - CONTROL.icon.h,
        y: target.y + (target.h - CONTROL.icon.h) / 2, w: CONTROL.icon.h, h: CONTROL.icon.h }, 4, frame);
    }
  };
  for (const box of placed.boxes) {
    if (box.name === "Portrait") {
      const portraitRow = row([{ w: side ? pictureW : pictureColumn.w, h: pictureH }, { w: infoW, h: infoStack.h }],
        { x: side ? pictureColumn.x + (pictureColumn.w - pictureW) / 2 : 0, y: box.y, w, gap: SPACE.xl, align: side ? "center" : "start" }).boxes;
      picture.pos = toNode(portraitRow[0], 4, frame);
      const top = side ? portraitRow[1].y - box.y : 0;
      for (const part of infoStack.boxes) placeInfo({ ...part, y: part.y + box.y + top }, infoColumn.x);
    } else if (box.name === "Actions") actionRow.boxes.forEach((action, i) => {
      find(tree, `Act_${i}`).pos = toNode({ ...action, y: box.y }, 4, frame);
    });
    else if (["Title_Bg", "Picture"].includes(box.name)) find(tree, box.name).pos = toNode(box, 4, frame);
    else placeInfo(box);
  }
  tree.contentHeight = pinned ? content.h : placed.h;
  tree.contentLayout = placed;
  return tree;
}

// a list popup in host (an element or its id). Redrawn by the caller on every change (a card picked, a segment):
//   items: [{key, label, full, thumb, big, recruit, kind, inUse, inUseText, fav, isNew, size, disabled, dim, badge: {text, color,
//           ink}, tag: {text, color, ink}, chips, original, nameEdit: {label, fn(scroll)}}]; original: secondary name in the detail band;
//           disabled: cannot be ticked (with multi); dim: the darkened look
//   inUseText: a saved source's caption in the detail chip, independent of whether its files are in use
//   selected: the chosen key; with multi, the ticked keys (selectable(items) ticks all), focus: the card whose details
//             show (by default the first ticked one)
//   onSelect(key, scroll): a card was clicked (the caller ticks it, toggled(), or chooses it, and draws the popup again)
//   buttons: [{label, fn, primary, greyed, action}] under the list, or okText and onOk for the one confirm button
//   onClose(how): the close button, the backdrop and Escape; null for a popup without them (it must be answered)
//   A list with no card to show puts hall.noCandidates in the list paper; with tools, while the chosen card (selected)
//   is not among the cards shown, the primary button is the game's disabled one and answers nothing.
//   tools: {segs, seg, onSeg, sortKey, more, check, onView} for the student list's tools over the cards, or null; without
//          them, hint (left) and footer (right) show in their place. more(query): cards added after the matching ones
//          (Switch 3D Model's other students' models, BL-131; asked on every drawing, with the search's words, which may be
//          empty), ordered and turned like them. check: {label, checked, onChange(checked)}, a checkbox before the
//          segments in the same toolbar (the roster's fit, see checkedToolbar); a click flips check.checked and draws the
//          popup again, so the same object is passed to every drawing. onView(keys): the keys of the cards in view, after
//          every drawing and scroll (the same signal that brings their thumbnails in)
//   items' hint: the card's hover hint
//   check: {label, checked, onChange} for a checkbox below the details; a click flips check.checked, so the same
//          object is passed to every drawing
//   details(it): {title, fields: [[caption, value, colour]], actions: [[caption, fn]]} for the panel on the left
//   keepScroll: the list's offset on its scrolling axis (horizontal for portraits)
const toolStates = new WeakMap();
const tokens = new Map(); // host -> the latest drawing's number: an older one that ends later is dropped
export async function listPopup(options) {
  let {
  host: hostArg, hostId, title, items, selected = null, multi = false, focus = null, onSelect, buttons = null, okText, onOk,
  onClose = null, tools = null, hint = "", footer = "", check = null, details, keepScroll = 0,
} = options;
  const host = hostOf(hostArg ?? hostId);
  const my = (tokens.get(host) || 0) + 1;
  tokens.set(host, my);
  const reopening = host.hidden;
  host.hidden = false;
  await ready();
  let portraitTexture = null;
  if (items.some(it => typeof it.applied === "boolean")) await selectionReady();
  if (options.layoutKind === "spr") {
    const inventory = await NGUI.layout("UINonEquipmentInventory");
    portraitTexture = find(inventory, "Inventory_ListControl/EquipPos/Texture");
  }
  const LP = listLayout(!!check, options.layoutKind);
  const cardGrid = switchGrid(options.layoutKind), { card, columns, pitch, horizontal } = cardGrid;
  const scrollKey = horizontal ? "scrollLeft" : "scrollTop";
  let toolState = toolStates.get(host);
  if (tools) {
    if (reopening || !toolState || host.dataset.pickerTitle !== title) {
      toolState = { query: "", sort: "default", ascending: true, searching: false };
      toolStates.set(host, toolState);
      host.dataset.pickerTitle = title;
    }
    items = pickerItems(items, toolState);
    if (tools.more) items = [...items, ...pickerItems(tools.more(toolState.query) || [], toolState)];
  }
  const redraw = () => listPopup({ ...options, keepScroll: 0 });
  const ticked = multi ? new Set(selected || []) : null;
  const isSelected = (key) => (multi ? ticked.has(key) : key === selected);
  // a search or segment that hides the chosen card: confirming would act on a card the user cannot see
  const filteredOut = !!tools && !multi && selected != null && !items.some((it) => it.key === selected);
  const btns = (buttons || [{ label: okText, fn: onOk, primary: true, semantic: options.okSemantic || "confirm", action: "ok" }])
    .map((b) => (b.primary && filteredOut ? { ...b, greyed: true, blocked: true } : b));
  const next = document.createElement("div");
  const view = await NGUI.render(listFrame(title, btns, !!onClose, !!check, options.layoutKind), next, { scale: S, origin: [960, 540] });
  btns.forEach((b, i) => {
    const button = hit(next, view, `Center/Popup/Btn/Btn_${i}/BG (1)`, b.blocked ? () => {} : b.fn, { name: b.label, action: b.action || `button${i}` });
    if (button && b.blocked) { button.disabled = true; button.setAttribute("aria-disabled", "true"); }
  });
  if (onClose) hit(next, view, "Center/Popup/Base/BG/Btn_Close", () => onClose("close"), { name: T("display.close"), action: "close" });
  closable(view, host, onClose);
  const band = [];
  if (tools && layouts.collection && !(horizontal && toolState.searching)) {
    const sortKeys = [...new Set([tools.sortKey ?? "sort.default", "sort.name"])];
    const tb = toolbar(LP.list.r - SPACE.m, LP.list.t - SPACE.m - CONTROL.buttonS.h / 2, { segs: tools.segs, sortKey: toolState.sort === "name" ? "sort.name" : tools.sortKey, sortKeys,
      active: tools.seg ?? 0, ascending: toolState.ascending, filtered: !!toolState.query, check: tools.segs ? tools.check : null });
    const tv = await NGUI.render(tb.tree, next, { scale: S, origin: [960, 540], z: 20 });
    hit(next, tv, "Toolbar/OrderBtn/Btn_Normal", () => { toolState.ascending = !toolState.ascending; redraw(); },
      { name: T(toolState.ascending ? "roster.asc" : "roster.desc"), action: "order" });
    hit(next, tv, "Toolbar/SearchBtn/Btn_Normal", () => { toolState.searching = !toolState.searching; if (!toolState.searching) toolState.query = ""; redraw(); },
      { name: T("roster.search"), action: "search" });
    hit(next, tv, "Toolbar/SortBtn/Btn_Normal", async () => {
      const Display = await import("./display.js");
      await Display.open({ view: { sort: toolState.sort, order: toolState.ascending ? "asc" : "desc", filters: {} },
        count: () => items.length, sorts: ["default", "name"], sortOnly: true,
        onDone: (draft) => {
          if (!draft || host.hidden || tokens.get(host) !== my) return;
          toolState.sort = draft.sort; toolState.ascending = draft.order === "asc"; redraw();
        } });
    }, { name: T("display.tab.sort"), action: "sort" });
    if (tb.check) {
      const { x, w, y } = tb.check, state = tools.check;
      const box = hitButton(next, [960 + (x - w / 2) * S, 540 - (y + CONTROL.toolbar.h / 2) * S, w * S, CONTROL.toolbar.h * S], () => {
        state.checked = !state.checked;
        state.onChange?.(state.checked);
        redraw();
      }, state.label, 80);
      box.dataset.action = "check";
      box.setAttribute("role", "checkbox");
      box.setAttribute("aria-checked", String(!!state.checked));
    }
    if (tb.segBox && tools.onSeg) {
      const { right, segW, y } = tb.segBox;
      for (let i = 0; i < 3; i++) {
        const ux = right - (3 - i) * segW + segW / 2 - 6, uy = y + 50.2 - 50;
        hitButton(next, [960 + (ux - segW / 2) * S, 540 - (uy + 52) * S, segW * S, 104 * S], () => tools.onSeg(i), T(tools.segs[i]), 80);
      }
    }
  } else {
    // the band over the cards: a hint at the left, the footer (the ticked cards, their size) at the right
    const room = LP.list.r - LP.list.l - 80;
    const ft = footer ? fitText(footer, 40, 28, room / 2, 1) : null;
    const ht = hint ? fitText(hint, 40, 28, room - (ft ? glyphsOf(ft.text, ft.fs) + 40 : 0), 2) : null;
    if (ht) band.push(node("Hint", [LP.list.l + 40, LP.list.t - SPACE.m - CONTROL.buttonS.h / 2], [label(ht.text, ht.fs, "#2d4664/1.00", room, 50 * ht.lines, { pivot: 3, depth: 8 })]));
    if (ft) band.push(node("Footer", [LP.list.r - 40, LP.list.t - SPACE.m - CONTROL.buttonS.h / 2], [label(ft.text, ft.fs, "#2d4664/1.00", room / 2, 50, { pivot: 5, depth: 8 })]));
  }
  let cb = null;
  if (check) {
    const checkW = LP.left.r - LP.left.l;
    cb = checkbox("Check", 0, 0, check.label, !!check.checked, checkW);
    const checkRow = row([{ w: cb.w, h: CHECK_H }], { x: LP.content.x, y: LP.content.y + LP.content.h - CHECK_H,
      w: checkW, gap: SPACE.m, justify: "center" });
    cb.tree.pos = toNode(checkRow.boxes[0], 3, LP.frame);
    band.push(cb.tree);
  }
  if (band.length) {
    const bv = await NGUI.render(node("Band", [0, 0], [], band), next, { scale: S, origin: [960, 540], z: 20 });
    if (cb) wireCheck(next, bv, "Band/Check", check, check.onChange);
  }
  let searchInput = null;
  if (tools && toolState.searching && layouts.collection) {
    const search = searchTree(layouts.collection);
    // Portraits need the paper's full height: search replaces their toolbar in the header.
    search.pos = [(LP.list.l + LP.list.r) / 2, LP.list.t - CONTROL.buttonS.h - SPACE.m - SPACE.xs];
    find(search, "SearchName").pos = [0, 0];
    if (horizontal) {
      const area = find(search, "SearchName/SearchNameArea"), w = area.widgets.find((w) => "sprite" in w);
      search.pos[1] = LP.list.t - SPACE.m - CONTROL.buttonS.h / 2 - area.pos[1] - (Math.floor((w.pivot ?? 4) / 3) / 2 - .5) * w.h_;
    }
    const sv = await NGUI.render(search, next, { scale: S, origin: [960, 540], z: 25 });
    const r = nodeRect(sv, `${search.name}/SearchName/SearchNameArea/InputField/Label_Search`);
    if (r) {
      searchInput = document.createElement("input");
      searchInput.className = "roster-search";
      Object.assign(searchInput.style, { left: `${r[0]}px`, top: `${r[1]}px`, width: `${r[2]}px`, height: `${r[3]}px`, fontSize: `${60 * S}px`, zIndex: 80 });
      searchInput.type = "text"; searchInput.autocomplete = "off"; searchInput.spellcheck = false;
      searchInput.placeholder = T("roster.searchHint");
      searchInput.value = toolState.query;
      searchInput.setAttribute("aria-label", T("roster.searchHint"));
      const apply = () => { toolState.query = searchInput.value; redraw(); };
      searchInput.addEventListener("input", (ev) => { if (!ev.isComposing) apply(); });
      searchInput.addEventListener("compositionend", apply);
      const closeSearch = () => { toolState.query = ""; toolState.searching = false; redraw(); };
      // Escape closes the search first, as the roster's does, and the popup stays; not the key that ends an input
      // method's conversion (keyCode 229 in WebKit)
      searchInput.addEventListener("keydown", (ev) => {
        if (ev.key !== "Escape" || ev.isComposing || ev.keyCode === 229) return;
        ev.preventDefault(); closeSearch();
      });
      hit(next, sv, `${search.name}/SearchName/SearchNameArea/Btn_Close`, closeSearch, { name: T("display.close"), action: "search-close" });
      next.appendChild(searchInput);
    }
  }
  const cardBox = listCardBox(LP, { searching: tools && toolState.searching, horizontal });
  const [cardLeft, top] = toNode(cardBox, 0, LP.frame), bottom = top - cardBox.h;
  if (!items.length) {
    // nothing to show: the list paper says so, as the Hall's pickers do
    const room = LP.list.r - LP.list.l - 160, f = fitText(T("hall.noCandidates"), 48, 32, room, 2);
    await NGUI.render(node("Empty", [(LP.list.l + LP.list.r) / 2, (top + bottom) / 2],
      [label(f.text, f.fs, "#2d4664/1.00", room, f.fs * 1.3 * f.lines + 8, { depth: 8 })]), next, { scale: S, origin: [960, 540], z: 20 });
  }
  const [aL, aT] = [960 + cardLeft * S, 540 - top * S];
  // Measure the actual prefab widgets, including the name band's asymmetric incline and selection marks.
  const trees = items.map((it) => listCard(it, 0, 0, isSelected(it.key), card, options.layoutKind));
  const paper = { left: cardLeft, right: cardLeft + cardBox.w, top: top + SPACE.m };
  const bounds = placeSwitchCards(trees, cardGrid, paper);
  const rows = Math.ceil(items.length / columns), visH = (top - bottom) * S, visW = (paper.right - paper.left) * S;
  const contentH = horizontal ? visH : (rows * pitch[1] + 20) * S;
  const contentW = horizontal ? Math.max(visW, ...bounds.map((b) => (b.right - paper.left + 40) * S)) : visW;
  const inner = scrollArea(next, [aL, aT, visW, visH], contentH, { horizontal, contentW });
  await inner.ready;
  const origin = [960 - aL, 540 - aT];
  const scroll = () => inner.parentElement[scrollKey] || 0;
  const at = (i) => trees[i].pos;
  await Promise.all(items.map(async (it, i) => {
    const tree = trees[i];
    // a dim card's picture is wholly grey under a dark veil
    const styles = it.dim ? { [`Card_${it.key}/Thumb`]: "filter: grayscale(1)" } : undefined;
    const cv = await NGUI.render(tree, inner, { scale: S, origin, z: 30, styles });
    if (it.nameEdit) hit(inner, cv, `Card_${it.key}/Bar/EditName`, () => it.nameEdit.fn(scroll()), { name: it.nameEdit.label, action: `alias:${it.key}` });
  }));
  // the cards' buttons in the list's order, so the keyboard steps through them as they show
  items.forEach((it, i) => {
    const [x, y] = at(i), [sx, sy] = [origin[0] + x * S, origin[1] - y * S];
    const b = hitButton(inner, [sx - card.w / 2 * S, sy - card.h / 2 * S, card.w * S, (card.h + card.bar + 10) * S], () => onSelect(it.key, scroll()), it.full, 60);
    b.dataset.key = it.key;
    if (it.applied) b.setAttribute("aria-label", [it.full || it.label, T("status.applied")].filter(Boolean).join(" - "));
    if (it.hint) { attachHint(b, it.hint); b.setAttribute("aria-description", it.hint); }
    if (multi) { b.setAttribute("role", "checkbox"); b.setAttribute("aria-checked", String(ticked.has(it.key))); }
  });
  const shown = multi ? focus ?? items.find((it) => ticked.has(it.key))?.key : selected;
  const sel = items.find((it) => it.key === shown) || items[0];
  if (sel && details) {
    const d = details(sel);
    const tree = detailsTree(sel, d.title, d.fields, d.actions, portraitTexture, options.layoutKind, LP.left);
    const panel = LP.left, panelH = panel.t - panel.b;
    const detailsHost = tree.contentHeight > panelH ? scrollArea(next,
      [960 + panel.l * S, 540 - panel.t * S, (panel.r - panel.l) * S, panelH * S], tree.contentHeight * S) : next;
    await detailsHost.ready;
    const origin = detailsHost === next ? [960, 540] : [-panel.l * S, panel.t * S];
    const dv = await NGUI.render(tree, detailsHost, { scale: S, origin, z: 20 });
    if (sel.nameEdit) hit(detailsHost, dv, "Details/EditName", () => sel.nameEdit.fn(scroll()), { name: sel.nameEdit.label, action: "alias:details" });
    d.actions.forEach(([caption, fn, disabled, , hint], i) => {
      if (!fn) return;
      const button = hit(detailsHost, dv, `Details/Act_${i}/Body`, disabled ? () => {} : fn, { name: caption });
      if (!button) return;
      // a greyed action with a reason keeps the pointer, so the reason shows on hover (the game's tooltip); an action's
      // own hint (what it covers) shows the same way
      if (disabled && hint) button.setAttribute("aria-disabled", "true");
      else button.disabled = !!disabled;
      if (hint) { attachHint(button, hint); button.setAttribute("aria-description", hint); }
    });
  }
  if (tokens.get(host) !== my || host.hidden) return;
  const area = inner.parentElement;
  host.replaceChildren(...next.childNodes);
  if (searchInput) { searchInput.focus?.(); searchInput.setSelectionRange?.(searchInput.value.length, searchInput.value.length); }
  area[scrollKey] = keepScroll;
  // the thumbnails render in the order they were asked for: the cards in view go first, again as the list scrolls
  const bumpShown = () => {
    if (tokens.get(host) !== my || host.hidden) return;
    const offset = scroll(), inView = [];
    for (let i = items.length - 1; i >= 0; i--) {
      const b = bounds[i];
      const first = horizontal ? (b.left - paper.left) * S : (top - b.top) * S;
      const last = horizontal ? (b.right - paper.left) * S : (top - b.bottom) * S;
      if (last > offset && first < offset + (horizontal ? visW : visH)) { items[i].thumb?.bump?.(); inView.unshift(items[i].key); }
    }
    tools?.onView?.(inView);
  };
  bumpShown();
  let waiting = false;
  area.addEventListener("scroll", () => {
    if (waiting) return;
    waiting = true;
    setTimeout(() => { waiting = false; bumpShown(); }, 120);
  });
}

// --- the message popup ---
export const MSG = { title: POPUP_FRAME.contentTop, gap: SPACE.l, rowButton: SMALL_BUTTON.h + SPACE.m,
  check: CHECK_H + SPACE.m, buttons: POPUP_FRAME.footer.bottom + CONTROL.buttonL.h + SPACE.m,
  none: SPACE.xl, cap: POPUP_FRAME.maxH };

// message({title, lines, ok, cancel, check, host, width, close, confirmKeys, scroll}): a message on the popup frame, in host
// (#dialog by default, over everything but the message popup). Resolves once it is closed, however it is closed, with
// {button, checked}: button "ok" or "cancel", or null for the close button, the backdrop or Escape; checked, the
// checkbox's state then (null without one).
//   lines: text, or {text, button: {label, fn}} for a line with a small button at its end (fn({close}), where
//          close(button) closes the popup); with such a line every line shows as a row of the game's light box
//          fontSize/minFontSize optionally keep a row's existing type size (the tour counter and body).
//          With plain: indent (units) moves a line right, marker hangs a bullet left of its first line (markerGap wide,
//          its font size by default), gap adds space above it (release notes' lists and sections).
//   fitTogether: line indices fitted at one shared size, using the smallest fit within the popup's size floor
//   plain: left-aligned lines without a box or separators, including lines with a button at the right
//   ok, cancel: the buttons' labels (either may be left out); check: {label, checked}
//   buttons: optional named footer choices [{label, value, primary}], instead of ok/cancel
//   close: false for a popup that must be answered with a button (no close button; the backdrop and Escape do nothing)
//   confirmKeys: keys that press OK while the popup is in front and holds the focus itself (a focused button takes its
//                own key), as the PC client binds its popups' confirm (["Enter", " "]); none by default
//   scroll: constrain the findings to the frame; title and action buttons stay visible. A message whose frame would
//           be taller than that frame (MSG.cap) scrolls by itself, so its title and buttons stay on the stage
// The popup takes the keyboard focus while it is open (Enter presses nothing under it) and gives it back after.
// Without the frame's prefab (no game folder) the same message is drawn plain.
export function message(opts) {
  if (opts.error || opts.title === T("job.failed")) {
    const error = typeof opts.error === "object" && opts.error ? opts.error : {};
    const event = { kind: "popup", code: error.code || "internal", params: error.params || {},
      message: error.message || (opts.lines || []).filter((line) => typeof line === "string").join("\n") };
    const ref = error.ref || codeOf(event.code);
    if (ref) event.ref = ref;
    const report = () => globalThis.BAMTRecovery?.report(event);
    if (!opts.cancel) {
      opts = { ...opts, cancel: T("recovery.report") };
      return new Promise((resolve) => showMessage(opts, (result) => {
        if (result.button === "cancel") report();
        resolve(result);
      }));
    }
    opts = { ...opts, lines: [...(opts.lines || []), { text: "", button: { label: T("recovery.report"),
      fn: report } }] };
  }
  return new Promise((resolve) => { showMessage(opts, resolve); });
}

// a key that presses OK (confirmKeys): alone, not a held key's repeat, not while an input method composes
export function confirmKey(ev, keys) {
  if (!keys?.length || !ev || ev.defaultPrevented || ev.isComposing || ev.keyCode === 229 || ev.repeat) return false;
  if (ev.altKey || ev.ctrlKey || ev.metaKey || ev.shiftKey) return false;
  return keys.includes(ev.key);
}

// the message without the game's prefab: a plain box on a dark backdrop, the same buttons, marked the same way
export function plainMessage(layer, { title, lines = [], ok, cancel, buttons, state = null, dismissable = true,
  scroll = false, plain = false, width, height, fitTogether = [], captionSite = null }, finish) {
  const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };
  const button = (caption, fn, action, cls = "message-button") => {
    const b = el("button", cls, caption); b.dataset.action = action; b.setAttribute("aria-label", caption);
    b.addEventListener("click", fn); return b;
  };
  const layout = messageLayout({ lines, ok, cancel, buttons, check: state, scroll, plain, width, height, fitTogether, assets: false });
  const frame = { w: layout.width, h: layout.height, cx: 0, cy: FRAME_Y };
  const styleBox = (element, box, fs) => {
    Object.assign(element.style, { position: "absolute", boxSizing: "border-box", margin: "0", padding: "0",
      left: `${box.x * S}px`, top: `${box.y * S}px`, width: `${box.w * S}px`, height: `${box.h * S}px`,
      ...(fs != null ? { fontSize: `${fs * S}px`, lineHeight: String(NGUI.LINE_PITCH), whiteSpace: "pre-wrap" } : {}) });
    return element;
  };
  const back = el("div", "plain-popup-back");
  if (dismissable) back.addEventListener("click", () => finish(null));
  const box = el("div", "plain-popup");
  Object.assign(box.style, { position: "absolute", boxSizing: "border-box", transform: "none", margin: "0", padding: "0",
    left: `${960 - frame.w * S / 2}px`, top: `${540 - (FRAME_Y + frame.h / 2) * S}px`,
    width: `${frame.w * S}px`, height: `${frame.h * S}px`, maxWidth: "none", maxHeight: "none" });
  const heading = textItem(NGUI, title, TYPE.display, { maxW: frame.w - 2 * SPACE.xxxl, lines: 1 });
  box.appendChild(styleBox(el("h2", "", heading.text), { x: SPACE.xxxl, y: POPUP_FRAME.titleTop,
    w: frame.w - 2 * SPACE.xxxl, h: heading.h }, NGUI.fontSizeFor(heading.text, heading.fs)));
  if (dismissable) box.appendChild(styleBox(button("×", () => finish(null), "close"),
    { x: frame.w - POPUP_FRAME.close.right - CONTROL.icon.h, y: POPUP_FRAME.close.top, w: CONTROL.icon.h, h: CONTROL.icon.h }, CONTROL.icon.h));
  if (dismissable) box.children.at(-1).setAttribute("aria-label", T("display.close"));
  const content = styleBox(el("div", "plain-message-lines"), { x: layout.content.x, y: FRAME_Y + frame.h / 2 - layout.y0,
    w: layout.content.w, h: layout.visible });
  Object.assign(content.style, { overflow: "auto", textAlign: plain ? "left" : "center" });
  const paragraphItems = layout.body.fitted.map(({ r, f, bf, bw, item: text }) => {
    return { r, f, bf, bw, text, w: layout.content.w,
      h: Math.max(r.button ? SMALL_BUTTON.h : 0, text.h) + (plain ? r.gap || 0 : 0) };
  });
  const paragraphs = layoutStack(paragraphItems, { w: layout.content.w, gap: SPACE.l });
  paragraphs.boxes.forEach(({ r, f, bf, bw, text, x, y, w }, i) => {
    const indent = plain ? r.indent || 0 : 0;
    const parts = row([{ ...text, w: w - indent - (r.button ? bw + SPACE.m : 0) }, ...(r.button ? [{ w: bw, h: SMALL_BUTTON.h }] : [])],
      { x: x + indent, y: y + (plain ? r.gap || 0 : 0), w: w - indent, gap: SPACE.m, justify: "center" }).boxes;
    const p = styleBox(el("p", "", f.text), parts[0], NGUI.fontSizeFor(f.text, f.fs));
    content.appendChild(p);
    if (plain && r.marker) content.appendChild(styleBox(el("span", "", r.marker),
      { x: parts[0].x - (r.markerGap ?? f.fs), y: parts[0].y, w: r.markerGap ?? f.fs, h: NGUI.fontSizeFor(f.text, f.fs) * NGUI.LINE_PITCH }, NGUI.fontSizeFor(f.text, f.fs)));
    if (r.button) {
      const b = styleBox(button(bf.text, () => r.button.fn?.({ close: finish }), `line${i}`, "plain-line-button"), parts[1], NGUI.fontSizeFor(bf.text, bf.fs));
      b.setAttribute("aria-label", r.button.label); content.appendChild(b);
    }
  });
  // Use the measured body height for the scroll viewport, including the native stats-box row envelope.
  if (paragraphs.h > layout.visible) content.style.overflowY = "auto";
  box.appendChild(content);
  if (state) {
    const check = button(layout.checkCaption.text, () => { state.checked = !state.checked; check.setAttribute("aria-checked", String(state.checked)); }, "check", "plain-check");
    check.setAttribute("role", "checkbox"); check.setAttribute("aria-checked", String(state.checked));
    check.setAttribute("aria-label", state.label);
    box.appendChild(styleBox(check, layout.checkBox, NGUI.fontSizeFor(layout.checkCaption.text, layout.checkCaption.fs)));
  }
  const actions = buttons?.length ? buttons : [...(cancel ? [{ label: cancel, value: "cancel" }] : []), ...(ok ? [{ label: ok, value: "ok" }] : [])];
  if (actions.length) {
    const slots = footerSlots(actions.length, frame.w), bodyW = CONTROL.buttonL.minW * slots.scale;
    const fits = footerFit(actions.map(action => action.label), { site: captionSite, width: bodyW - 2 * CONTROL.buttonL.pad });
    const actionRow = el("div", "message-actions");
    styleBox(actionRow, { x: 0, y: frame.h - POPUP_FRAME.footer.bottom - CONTROL.buttonL.h, w: frame.w, h: CONTROL.buttonL.h });
    const boxes = row(actions.map(() => ({ w: bodyW, h: CONTROL.buttonL.h })), { w: frame.w, gap: POPUP_FRAME.footer.gap, justify: "center" }).boxes;
    actions.forEach((action, i) => {
      const b = styleBox(button(fits[i].text, () => finish(action.value), action.value), boxes[i], NGUI.fontSizeFor(fits[i].text, fits[i].fs));
      b.style.padding = `0 ${CONTROL.buttonL.pad * S}px`; b.setAttribute("aria-label", action.label); actionRow.appendChild(b);
    });
    box.appendChild(actionRow);
  }
  layer.replaceChildren(back, box);
  return { box, content, layout };
}

// a fixed size (max equals min) only wraps, on as many lines as it needs, and never shrinks below that size. strict: no size
// under min either way (the light box's rows): text that cannot keep it within `lines` lines breaks at separators and between
// characters, down to min, on as many lines as it needs
function messageText(text, max, min, room, lines = 4, strict = false) {
  const fixed = max === min;
  const fitted = fitText(text, max, min, room, fixed ? Infinity : lines);
  if (fitted.fs >= min && fitted.text.split("\n").every((line) => glyphsOf(line, fitted.fs) <= room)) return fitted;
  return strict ? wrapPath(text, max, min, room, lines) : wrapPath(text, min, min, room, Infinity);
}
// a fitted text with its lines counted as drawn (a line break in the text itself counts)
const counted = (fitted) => ({ ...fitted, lines: fitted.text.split("\n").length });

const placeholderButton = (name, x, y, caption, { w }) => node(name, [x, y], [sprite("Common_Btn_BG", w, SMALL_BUTTON.h)]);

function messageLines(lines, w, fitTogether, plain, assets = true) {
  const rows = lines.map((l) => (typeof l === "string" ? { text: l } : l));
  const boxed = !plain && rows.some((r) => r.button);
  // Light stats rows retain their value range (38–26); unboxed paragraphs retain 52–44. Width selection allows six lines.
  const min = boxed ? STATS_BOX.value.fsMin : 44, max = boxed ? STATS_BOX.value.fs : TYPE.heading.fs, most = 6; // Existing message floor and stats-box type range.
  const fitted = rows.map((r) => {
    // a line button is the small button: 136 high, as wide as its caption needs and 90 more (240 at least), the caption 48 down to 34
    const bf = r.button ? fitText(r.button.label, SMALL_BUTTON.fs, SMALL_BUTTON.fsMin, SMALL_BUTTON.w - SMALL_BUTTON.fitInset) : null;
    const bw = bf ? Math.max(SMALL_BUTTON.minW, glyphsOf(bf.text, bf.fs) + SMALL_BUTTON.glyphPad) : 0;
    const room = boxed ? valueRoom("frame", w, { reserve: bw ? bw + SPACE.m : 0 }) : w - 2 * SPACE.xxxl - (bw ? bw + SPACE.m : 0) - (plain ? r.indent || 0 : 0);
    const f = messageText(r.text, r.fontSize ?? max, r.minFontSize ?? min, room, most, boxed);
    return { r, bf, bw, room, f: counted(f) };
  });
  const group = fitTogether.map((i) => fitted[i]).filter(Boolean);
  if (group.length) {
    const fs = Math.max(min, Math.min(...group.map(({ f }) => f.fs)));
    // Rewrap at the shared size; extra lines keep the popup's minimum instead of shrinking below it.
    for (const s of group) s.f = boxed ? counted(wrapPath(s.r.text, fs, fs, s.room, Infinity)) : wrapPath(s.r.text, fs, fs, s.room, Infinity);
  }
  // Text is already wrapped and fitted. textItem measures that fit without repeating the line-break search.
  for (const s of fitted) s.item = textItem({ ...NGUI, fitText: () => s.f }, s.f.text,
    { fs: s.f.fs, min: s.f.fs }, { maxW: s.room, lines: Infinity });
  const buttons = [];
  if (boxed) {
    // rows on the game's light stats box: text at the left, the line's button at the right; a row with a button holds it, in 156
    // at least (the button and 10 above and below)
    const box = statsBox({ holder: "frame", width: w, name: "Box", names: { value: "Row", divider: "Dot" }, rows: fitted.map(({ r, f, bf, bw }, i) => ({
      value: f, colour: `${r.color || "#2d4664"}/1.00`, minH: bf ? MSG.rowButton : STATS_BOX.rowMin, reserve: bw ? bw + SPACE.m : 0,
      slot: bf ? ({ right, y }) => {
        buttons.push([i, r.button]);
        const buttonRow = row([{ w: bw, h: SMALL_BUTTON.h }], { x: right - bw, w: bw, gap: SPACE.m, justify: "center" });
        return [(assets ? Buttons.smallButton : placeholderButton)(`Btn_${i}`, buttonRow.boxes[0].x + bw / 2, y, r.button.label, { w: bw, fs: SMALL_BUTTON.fs, min: SMALL_BUTTON.fsMin, depth: 8 })];
      } : undefined,
    })) });
    return { tree: node("Lines", [0, 0], [], box.nodes), height: box.height, buttons, fitted };
  }
  const out = [], textBox = container({ w, h: 0, padding: [0, SPACE.xxxl] }).inner;
  const sized = fitted.map(s => {
    const indent = plain ? s.r.indent || 0 : 0;
    const lineRow = row([{ ...s.item, w: s.room }, ...(s.bf ? [{ w: s.bw, h: SMALL_BUTTON.h }] : [])],
      { x: textBox.x + indent, w: textBox.w - indent, gap: SPACE.m, justify: "center" });
    return { ...s, lineRow, w: textBox.w, h: lineRow.h + (plain ? s.r.gap || 0 : 0) };
  });
  const paragraphs = layoutStack(sized, { x: textBox.x, w: textBox.w, gap: SPACE.l });
  paragraphs.boxes.forEach(({ r, f, bf, bw, room, y, lineRow: measured }, i) => {
    const lineRow = { ...measured, boxes: measured.boxes.map(b => ({ ...b, y: b.y + y + (plain ? r.gap || 0 : 0) })) };
    const local = { w, h: 0, cx: 0, cy: 0 }, colour = `${r.color || "#2d4664"}/1.00`, box = lineRow.boxes[0];
    out.push(node(`Line_${i}`, toNode(box, plain ? 3 : 4, local),
      [label(f.text, f.fs, colour, room, box.h, { pivot: plain ? 3 : 4, depth: 8 })]));
    if (plain && r.marker) {
      const marker = { x: box.x - (r.markerGap ?? f.fs), y: box.y, w: r.markerGap ?? f.fs, h: f.fs * NGUI.LINE_PITCH };
      out.push(node(`Mark_${i}`, toNode(marker, 3, local), [label(r.marker, f.fs, colour, marker.w, marker.h, { pivot: 3, depth: 8 })]));
    }
    if (bf) {
      const [bx, by] = toNode(lineRow.boxes[1], 4, local);
      out.push((assets ? Buttons.smallButton : placeholderButton)(`Btn_${i}`, bx, by, r.button.label, { w: bw, fs: SMALL_BUTTON.fs, min: SMALL_BUTTON.fsMin, depth: 8 }));
      buttons.push([i, r.button]);
    }
  });
  return { tree: node("Lines", [0, 0], [], out), height: paragraphs.h, buttons, fitted, boxes: paragraphs.boxes };
}

// Shared by ordinary messages and text-only tours, including row fitting, frame padding and footer clearance.
// `scroll` in the result tells whether the lines scroll in the frame: asked for, or forced because the frame would
// be taller than MSG.cap. Non-scrolling bodies, including their check row, centre in the content box.
export function messageLayout({ lines = [], width, ok = null, cancel = null, buttons = null, check = null,
  fitTogether = [], scroll = false, plain = false, assets = true, height: minimumHeight = 0 } = {}) {
  let size;
  if (width != null) size = frameClass(width === 2100 ? "L" : width);
  else size = ["S", "M", "L"].map(key => frameClass(key)).find(candidate =>
    messageLines(lines, candidate.w, fitTogether, plain, false).fitted.every(({ f }) => f.lines <= 6)) || POPUP_FRAME.L;
  const body = messageLines(lines, size.w, fitTogether, plain, assets), hasButtons = !!(ok || cancel || buttons?.length);
  const need = MSG.title + body.height + (check ? MSG.check : 0) + (hasButtons ? MSG.buttons : MSG.none);
  const { w, h: height } = frameGeometry(size.w, Math.max(need, minimumHeight)),
    content = popupContent(w, height, { text: true, footer: hasButtons });
  const available = Math.max(0, content.h - (check ? MSG.check : 0));
  // A body that fits keeps its own height and centres, scrolling or not; only an overflowing one fills the content box.
  const overflow = body.height > available + 1e-9, scrolls = scroll || overflow;
  const visible = overflow ? available : body.height;
  const bodyTop = content.y + Math.max(0, available - visible) / 2;
  const y0 = toNode({ ...content, y: bodyTop }, 1, { w, h: height, cx: 0, cy: FRAME_Y })[1];
  let checkBox = null, checkCaption = null;
  if (check) {
    const cb = checkbox("Check", 0, 0, check.label, !!check.checked, content.w);
    checkCaption = cb.caption;
    checkBox = row([{ w: cb.w, h: CHECK_H }], { x: content.x, y: bodyTop + visible + SPACE.m,
      w: content.w, gap: SPACE.m, justify: "center" }).boxes[0];
  }
  return { body, width: w, height, visible, y0, content, checkBox, checkCaption, scroll: scrolls };
}

async function showMessage({ title = "", lines = [], ok = null, cancel = null, buttons = null, check = null, host: hostArg = "dialog", width, close: dismissable = true, confirmKeys = null, fitTogether = [], scroll = false, plain = false, captionSite = null }, resolve) {
  const host = hostOf(hostArg);
  const layer = document.createElement("div");
  layer.className = "popup-layer";
  layer.tabIndex = -1;
  host.appendChild(layer);
  host.hidden = false;
  const before = document.activeElement;
  const state = check ? { label: check.label, checked: !!check.checked } : null;
  let ended = false;
  const finish = (button) => {
    if (ended) return;
    ended = true;
    const focused = layer.contains(document.activeElement);
    layer.remove();
    closed(host, layer);
    if (!host.children.length) host.hidden = true;
    if (focused && before?.isConnected) before.focus?.({ preventScroll: true });
    resolve({ button, checked: state ? state.checked : null });
  };
  opened(host, dismissable ? () => finish(null) : null, { el: layer });
  // the confirm keys, while this popup is in front and the focus is on the popup itself
  if (ok && confirmKeys) {
    layer.addEventListener("keydown", (ev) => {
      if (ev.target !== layer || top()?.el !== layer || !confirmKey(ev, confirmKeys)) return;
      ev.preventDefault();
      finish("ok");
    });
  }
  try {
    await ready();
  } catch (err) {
    console.warn("message popup without the game's frame", err);
    if (ended) return;
    plainMessage(layer, { title, lines, ok, cancel, buttons, state, dismissable, scroll, plain, width, fitTogether, captionSite }, finish);
    layer.focus?.({ preventScroll: true });
    return;
  }
  try {
    const { body, width: w, height, visible, y0, content, checkBox, scroll: scrolls } = messageLayout({ lines, width, ok, cancel, buttons, check: state, fitTogether, scroll, plain });
    const t = popupFrame(title, w, height, { ok, cancel, buttons, close: dismissable, captionSite });
    const popup = find(t, "Popup");
    // The body and check row centre together; a scrolling body fills the available content box.
    if (!scrolls) {
      body.tree.pos = [0, y0];
      popup.children.push(body.tree);
    }
    if (state) {
      const cb = checkbox("Check", 0, 0, state.label, state.checked, content.w);
      cb.tree.pos = toNode(checkBox, 3, { w, h: height, cx: 0, cy: FRAME_Y });
      popup.children.push(cb.tree);
    }
    const next = document.createElement("div");
    const view = await NGUI.render(t, next, { scale: S, origin: [960, 540], z: 10 });
    const bodyHost = scrolls ? scrollArea(next, [960 - w * S / 2, 540 - y0 * S,
      w * S, visible * S], body.height * S) : next;
    await bodyHost.ready;
    const bodyView = scrolls ? await NGUI.render(body.tree, bodyHost,
      { scale: S, origin: [w * S / 2, 0], z: 10 }) : view;
    if (ended) return;
    for (const [index, action] of (buttons || []).entries()) hit(next, view, `Center/Popup/Btn/Choice_${index}/BG (1)`, () => finish(action.value), { name: action.label, action: action.value });
    if (ok) hit(next, view, "Center/Popup/Btn/Btn_Ok/BG (1)", () => finish("ok"), { name: ok, action: "ok" });
    if (cancel) hit(next, view, "Center/Popup/Btn/Btn_Cancel/BG (1)", () => finish("cancel"), { name: cancel, action: "cancel" });
    if (dismissable) hit(next, view, "Center/Popup/Base/BG/Btn_Close", () => finish(null), { name: T("display.close"), action: "close" });
    for (const [i, b] of body.buttons) {
      hit(bodyHost, bodyView, `${scrolls ? "Lines" : "Center/Popup/Lines"}/Btn_${i}`, () => b.fn?.({ close: finish }), { name: b.label, action: `line${i}` });
    }
    if (state) wireCheck(next, view, "Center/Popup/Check", state);
    closable(view, host, dismissable ? () => finish(null) : null, { el: layer });
    layer.replaceChildren(...next.childNodes);
    layer.focus?.({ preventScroll: true });
  } catch (err) {
    console.error("message popup", err);
    finish(null);
  }
}

// A scrollable text document on the shared popup frame. Text stays literal, selectable and in its original order.
export function textViewer({ title, text, copy, close = T("display.close"), onCopy }) {
  return new Promise((resolve) => {
    const host = hostOf("dialog"), layer = document.createElement("div"), before = document.activeElement;
    layer.className = "popup-layer";
    layer.tabIndex = -1;
    host.hidden = false;
    host.appendChild(layer);
    let ended = false;
    const finish = () => {
      if (ended) return;
      ended = true;
      layer.remove();
      closed(host, layer);
      if (!host.children.length) host.hidden = true;
      if (before?.isConnected) before.focus?.({ preventScroll: true });
      resolve();
    };
    opened(host, finish, { el: layer });
    const pre = document.createElement("pre");
    pre.className = "log-text";
    pre.textContent = text;
    pre.tabIndex = 0;
    pre.setAttribute("aria-label", title);
    const draw = async () => {
      try {
        await ready();
        if (ended) return;
        const next = document.createElement("div");
        const view = await NGUI.render(popupFrame(title, TEXT_VIEWER.w, TEXT_VIEWER.h, { ok: copy, cancel: close }), next,
          { scale: S, origin: [960, 540], z: 10 });
        if (ended) return;
        hit(next, view, "Center/Popup/Btn/Btn_Ok/BG (1)", onCopy, { name: copy, action: "copy" });
        hit(next, view, "Center/Popup/Btn/Btn_Cancel/BG (1)", finish, { name: close, action: "cancel" });
        hit(next, view, "Center/Popup/Base/BG/Btn_Close", finish, { name: close, action: "close" });
        closable(view, host, finish, { el: layer });
        const box = TEXT_VIEWER.box;
        Object.assign(pre.style, { position: "absolute", boxSizing: "border-box", margin: "0", left: `${960 - box.width / 2 * S}px`, top: `${540 - box.top * S}px`,
          width: `${box.width * S}px`, height: `${box.height * S}px`, zIndex: 40 });
        next.appendChild(pre);
        layer.replaceChildren(...next.childNodes);
      } catch (err) {
        if (ended) return;
        console.warn("text popup without the game's frame", err);
        const fallback = plainMessage(layer, { title, width: TEXT_VIEWER.w, height: TEXT_VIEWER.h, scroll: true,
          buttons: [{ label: copy, value: "copy" }, { label: close, value: "close" }] }, action => action === "copy" ? onCopy() : finish());
        // The document box keeps the viewer's own top and height, whatever the empty message body measures.
        Object.assign(fallback.content.style, { top: `${(FRAME_Y + TEXT_VIEWER.h / 2 - TEXT_VIEWER.box.top) * S}px`,
          height: `${TEXT_VIEWER.box.height * S}px` });
        Object.assign(pre.style, { position: "absolute", boxSizing: "border-box", margin: "0", left: "0px", top: "0px",
          width: `${TEXT_VIEWER.box.width * S}px`, height: `${TEXT_VIEWER.box.height * S}px` });
        fallback.content.replaceChildren(pre);
      }
      pre.scrollTop = pre.scrollHeight;
      layer.focus?.({ preventScroll: true });
    };
    draw();
  });
}

// --- ordinary refusals ---
// A refusal declines an action because of a state or a rule (a task still running, the game open, no game folder, a
// Hall rule): nothing broke, so it reads as the game's neutral notice (title quit.title, Confirm only), without Failed
// and without an Error Report button (BL-045). Every other code is a real failure and keeps both. Codes that report
// something unreadable or unknown (game_status_unavailable, profiles_unreadable) stay failures.
export const REFUSALS = Object.freeze(noticeNames());
export const isRefusal = (error) => REFUSALS.includes(error?.code);

// the neutral notice: the game's caption title (as the quit prompt), one line, Confirm only; resolves when closed
export function notice(text) {
  return message({ title: T("quit.title"), lines: [text], ok: T("picker.ok") });
}

// Only errors/refusals use a notification popup: a refusal as a notice, anything else as Failed with the Error Report.
// Rich results call message() explicitly.
export function notify(text, { kind = "info", error = null } = {}) {
  if (!text || kind !== "error") return;
  if (isRefusal(error)) { void notice(text); return; }
  void message({ title: T("job.failed"), lines: [withCode(text, error, T)], ok: T("picker.ok"), error });
}

// Prefabs and derived UI values belong to the selected installation.
NGUI.onResourceChange?.(() => { layouts = loading = frameLoading = selectionLoading = selectionSlot = selectionSprite = selectionOutline = null; });
