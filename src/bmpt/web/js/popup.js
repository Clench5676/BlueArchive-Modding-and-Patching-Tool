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
import { installScrollFades } from "./scroll-fade.js";
import { switchGrid, placeSwitchCards, cardInkBounds } from "./switch-layout.js";
import { scrollbar, scrollbarTrack } from "./scrollbar.js";
import { horizontalScroll } from "./horizontal-scroll.js";
import * as Buttons from "./game-buttons.js";
import { searchTree, pickerItems, toolbarFit, toolbarTree, TOOLBAR_CENTRE_Y } from "./roster-widgets.js";
import { resizeButton } from "./button-layout.js";
import { fitBadgeCaptions, selectedFrame, selectionArtWindow, SELECTION_PREFAB, SELECTION_SLOT } from "./selected-frames.js";
import { frameOutline } from "./row-thumbnail.js";
import { T } from "./i18n.js";
import { codeOf, noticeNames, withCode } from "./error-codes.js";
import { withTimeout } from "./async.js";
import { TEXT_VIEWER, frameClass, frameGeometry, popupContent } from "./popup-frames.js";
import { STATS_BOX_S, boxHeight, statsBox, valueRoom } from "./stats-box.js";
import { BACKDROP, CHIP, CHIP_TIERS, COLOUR, CONTROL, POPUP_FOOTER, POPUP_FRAME, POPUP_TYPE, SPACE, TYPE, SMALL_BUTTON, XS_BUTTON, STATS_BOX, captionMinimum } from "./ui-tokens.js";
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
  // every scrolling area fades at an edge with more past it (scroll-fade.js, BL-176); once, from the first popup on
  installScrollFades();
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
// a footer's bodies above the frame's bottom: the prefab's 102 under its buttonL bodies, scaled to the bodies' height (60 for
// the XS footer every popup has, POPUP_FOOTER.bottom)
export const footerBottom = (control = POPUP_FOOTER.control) => Math.round(POPUP_FRAME.footer.bottom * control.h / CONTROL.buttonL.h);
export const footerY = (h, control = POPUP_FOOTER.control) => FRAME_Y - h / 2 + footerBottom(control) + control.h / 2;
export const footerTop = (h, n = 1) => footerY(h) + POPUP_FOOTER.control.h / 2;
export function footerScale(n, w = Infinity, control = footerSize().control) {
  return n < 3 ? 1 : Math.min(1, (w - 2 * POPUP_FRAME.footer.side - POPUP_FRAME.footer.gap * (n - 1)) / n / control.minW);
}
export const footerGap = (n) => POPUP_FRAME.footer.gap;
export function footerSlots(n, w, control = footerSize().control) {
  if (!n) return { scale: 1, xs: [] };
  const scale = footerScale(n, w, control);
  w = Number.isFinite(w) ? w : n * control.minW * scale + (n - 1) * footerGap(n);
  const boxes = row(Array.from({ length: n }, () => ({ w: control.minW * scale, h: control.h })),
    { w, gap: footerGap(n), justify: "center" }).boxes;
  return { scale, xs: boxes.map(b => toNode(b, 4, { w, h: control.h, cx: 0, cy: 0 })[0]) };
}
const CAPTION_L = { fs: TYPE.display.fs, fsMin: TYPE.display.min, w: CONTROL.buttonL.minW - 2 * CONTROL.buttonL.pad,
  lines: 2, h: BUTTON_TEXT_H };
// The footer's sizes: "XS", the app's small button (CONTROL.buttonXS), every popup's footer (owner, 2026-11-11, BL-176:
// "弹窗的按钮弄成 XS", every popup of the App); "L", the buttonL control with display captions (UIPopup_RaidEnterCheck's footer); "M", one size smaller
// (the buttonM control, its caption 60 down to TYPE.body's floor); "S", two sizes smaller (the Settings value row's small
// button, SMALL_BUTTON, 402 x 136, its caption 48 down to TYPE.body's floor). Every button went one size down (owner,
// 2026-11-11, BL-173): a popup's footer is "M" unless its call site says otherwise, and the compatibility result ("S") and
// the repair review ("M") had gone down once already (BL-168).
const FOOTER_S = Object.freeze({ ...CONTROL.buttonS, minW: SMALL_BUTTON.w });
export const FOOTER = Object.freeze({
  L: Object.freeze({ control: CONTROL.buttonL, caption: CAPTION_L }),
  M: Object.freeze({ control: CONTROL.buttonM, caption: Object.freeze({ fs: CONTROL.buttonM.fs, fsMin: TYPE.body.min,
    w: CONTROL.buttonM.minW - 2 * CONTROL.buttonM.pad, lines: 2, h: CONTROL.buttonM.h - SPACE.m }) }),
  S: Object.freeze({ control: FOOTER_S, caption: Object.freeze({ fs: FOOTER_S.fs, fsMin: TYPE.body.min,
    w: FOOTER_S.minW - 2 * FOOTER_S.pad, lines: 2, h: FOOTER_S.h - SPACE.m }) }),
  XS: Object.freeze({ control: CONTROL.buttonXS, caption: Object.freeze({ fs: CONTROL.buttonXS.fs, fsMin: TYPE.badge.min,
    w: CONTROL.buttonXS.minW - 2 * CONTROL.buttonXS.pad, lines: 2, h: CONTROL.buttonXS.h - SPACE.s }) }),
});
export const FOOTER_DEFAULT = "XS";
// A footer's body width, units: the control's own minimum, wider where the longest caption needs it (its widest line, at the
// size the group fits the widest body, buttonL's, with the control's padding each side), the same for every button of the
// row. A tier is a height and a caption size; the width follows the captions, as the small button's does (glyphPad), so a
// caption that fitted the old body still fits at its size.
export function footerWidth(captions, kind = footerSize()) {
  const { control, caption } = kind, room = CONTROL.buttonL.minW - 2 * CONTROL.buttonL.pad;
  const labels = (captions || []).filter((text) => text);
  if (!labels.length) return control.minW;
  const fits = NGUI.fitGroup(labels, caption.fs, caption.fsMin, room, caption.lines, { maxH: caption.h });
  const need = Math.max(...fits.map((fit) => Math.max(...fit.text.split("\n").map((line) => NGUI.glyphWidth(line, fit.fs)))));
  return Math.min(CONTROL.buttonL.minW, Math.max(control.minW, Math.ceil(need + 2 * control.pad)));
}
// A footer row of these captions in a frame w wide: its size, the bodies' width and how far the row scales them down, their x
// centres and the room a caption has inside a body. footer: the size a call site names, or null for the default's (the bodies
// follow the captions, footerWidth).
export function footerLayout(labels, w, footer = null) {
  const kind = footerSize(footer), control = kind.control;
  const baseW = footer == null || footer === FOOTER_DEFAULT ? footerWidth(labels, kind) : control.minW;
  const { scale, xs } = footerSlots(labels.length, w, { ...control, minW: baseW }), bodyW = baseW * scale;
  return { kind, control, scale, xs, bodyW, room: bodyW - 2 * control.pad };
}
const CAPTION = FOOTER[FOOTER_DEFAULT].caption;
function footerSize(size) { return FOOTER[size] || FOOTER[FOOTER_DEFAULT]; }
function checkFooterFits(fits, { site = null, locale = globalThis.document?.documentElement?.lang, caption = CAPTION } = {}) {
  if (site && fits.some((fit) => fit.overflow || fit.fs < captionMinimum(site, locale, caption.fsMin)))
    throw new RangeError(`Popup footer cannot fit: ${site} (${locale})`);
  return fits;
}
export const footerFit = (captions, options = {}) => {
  const caption = options.caption || CAPTION;
  return checkFooterFits(NGUI.fitGroup(captions, caption.fs, caption.fsMin, options.width ?? caption.w, caption.lines,
    { maxH: caption.h }), { ...options, caption });
};
export function footerLabels(widgets, captions, options = {}) {
  const caption = options.caption || CAPTION, width = options.width ?? caption.w;
  widgets.forEach((widget, i) => Object.assign(widget, { w_: Array.isArray(width) ? width[i] : width, h_: caption.h }));
  return checkFooterFits(NGUI.fitLabels(widgets, captions, caption.fs, caption.fsMin,
    width, caption.lines, { maxH: caption.h }), { ...options, caption });
}

// the height of a title's line at the game's size (TYPE.display, one line): where a popup's smaller title is centred
const titleHeight = (title) => textItem(NGUI, title, TYPE.display, { lines: 1 }).h;
// the game's popup frame at a size: backdrop, title, close button and, when given, its Cancel and OK buttons with
// these labels on centred bodies of the footer's size (FOOTER_DEFAULT unless `footer` names one), with the shared gap and bottom inset.
//   buttons: [{label, primary, name}] instead of ok / cancel: named choices in a row (one centred, two as OK and Cancel,
//            three or more share the available width); a node is Choice_<index> unless it names itself
//   close: false for a popup that must be answered with a button (no close button)
//   titleCentre: the title's ink centre this far under the frame's top, in units, in place of the shared title top (the
//                header band's centre puts the title where UIPopup_Sort has it)
//   footer: the buttons' size, "XS", "S", "M" or "L" (FOOTER); without it the default size, XS, its bodies as wide as the captions need
//   align: "right" for a footer at the frame's bottom right, its last body's right edge SPACE.xl in from the frame's (the
//          panels' edge; owner, 2026-11-11, BL-176: the list popups and the Hall's background picker); centred otherwise
//   a button's tone: "bright" for the Settings small button's bright blue (BRIGHT_BUTTON) in place of the pale secondary
export function popupFrame(title, w, h, { ok = null, cancel = null, close = true, buttons = null, captionSite = null,
  titleCentre = null, footer = null, align = "center" } = {}) {
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
  // the title one size under the game's (POPUP_TYPE), centred where the game's 72 would stand
  const tf = textItem(NGUI, title, POPUP_TYPE.title, { maxW: titleBox.w, lines: 1 }), lift = (titleHeight(title) - tf.h) / 2;
  titleNode.pos = toNode({ ...titleBox, y: titleCentre == null ? titleBox.y + lift : titleCentre - (tf.mid ?? tf.h / 2), h: tf.h }, 4, local);
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
  // the default footer's bodies follow their captions (footerWidth); a size a call site names keeps its own width
  const { kind: footerKind, control, xs: centred, bodyW, room } = footerLayout(actions.map((action) => action.label), w, footer);
  const shift = align === "right" && centred.length ? w / 2 - SPACE.xl - (centred.at(-1) + bodyW / 2) : 0;
  const xs = centred.map((x) => x + shift);
  btn.active = !!actions.length;
  btn.pos = [0, footerY(h, control)];
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
    resizeButton(body, bodyW, control.h);
    find(child, "Label_Locale").pos = [0, 0];
    if (action.tone === "bright" && !action.primary) brighten(child);
    return child;
  }) : [];
  footerLabels(btn.children.map(child => find(child, "Label_Locale").widgets[0]), actions.map(action => action.label),
    { site: captionSite, width: room, caption: footerKind.caption });
  if (!buttons?.length) for (const [name, source] of [["Btn_Ok", primary], ["Btn_Cancel", secondary]]) {
    if (!find(btn, name)) { source.active = false; btn.children.push(source); }
  }
  find(t, "BlackBg/BlackBgClickArea").widgets.find((x) => "sprite" in x).color = BACKDROP;
  return t;
}

// The Settings small button's bright blue (UIPopup_Option_PC/.../ETCSetting/ButtonBg: Common_Btn_BG #8de4ff, its
// Common_Btn_Normal_B_S_Pt patterns, caption #314665), on a footer's secondary body (owner, 2026-11-11: the list popups'
// Cancel, BL-176)
export const BRIGHT_BUTTON = Object.freeze({ body: "#8de4ff/1.00", pattern: "Common_Btn_Normal_B_S_Pt", ink: "#314665/1.00" });
function brighten(button) {
  const body = find(button, "BG (1)"), bg = body.widgets.find((x) => "sprite" in x);
  bg.color = BRIGHT_BUTTON.body;
  for (const child of body.children || []) for (const x of child.widgets || []) if (/_Pt$/.test(x.sprite || "")) x.sprite = BRIGHT_BUTTON.pattern;
  find(button, "Label_Locale").widgets[0].color = BRIGHT_BUTTON.ink;
  button.actionSemantic = "cancel";
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
// the details' columns of the L class's twelve (the list has the rest), and the list's paper width: its columns less the
// panel's padding (listCardBox's)
const LIST_SPLIT = 4.5;
const LIST_PAPER_W = grid(container({ w: POPUP_FRAME.L.w, h: 0, padding: [0, SPACE.xl] }).inner).span(12 - LIST_SPLIT, LIST_SPLIT).w - 2 * SPACE.m;
export const LIST_CARD_NAME = Object.freeze({ fs: 34, fsMin: 24, lines: 2, padY: 22, pitch: 1.2 });
export function listLayout(check = false, kind = null) {
  // the card as the list's paper holds it (the L class's eight columns less the paper's padding)
  const native = switchGrid(kind, LIST_PAPER_W).card;
  // One complete native card, its name band and the toolbar determine the minimum useful viewport. Additional cards scroll.
  const cardHeight = native.h + Math.max(native.bar, LIST_CARD_NAME.padY + LIST_CARD_NAME.lines * LIST_CARD_NAME.fs * LIST_CARD_NAME.pitch);
  const need = POPUP_FRAME.contentTop + CONTROL.buttonS.h + 5 * SPACE.m + cardHeight + POPUP_FOOTER.clearance;
  // every list popup one height, the Hall's editors' (owner, 2026-11-11, BL-176: the three were as tall as their cards)
  const frame = frameGeometry("XL", Math.max(need, POPUP_FRAME.panelH));
  const content = popupContent(frame.w, frame.h);
  const panels = grid(content);
  // the details four and a half columns, the list seven and a half (owner, 2026-11-11, BL-176: the details' share smaller
  // than five; four leaves their actions and chips no room)
  const detailBox = { ...panels.span(LIST_SPLIT), h: content.h - (check ? CHECK_H + SPACE.m : 0) };
  const bounds = box => {
    const [l, t] = toNode(box, 0, frame);
    return { l, r: l + box.w, t, b: t - box.h };
  };
  return { frame, content, left: bounds(detailBox), list: bounds(panels.span(12 - LIST_SPLIT, LIST_SPLIT)),
    // Card artwork and the native card-placement helper retain their prefab envelope and scroll pitch.
    card: { w: 250, h: 226, bar: 68 }, pitch: [284, 350] };
}
// the band over a list's paper: the toolbar's height, or a slimmer one (``slim``) that only holds the footer's count
// The list popups' toolbar: the roster's, one size down (BL-176: "太高了，弄矮一些，再弄小一档"), its 116-unit tools 93 high
export const LIST_TOOLBAR = Object.freeze({ scale: .8 });
export const LIST_BAND = Object.freeze({ tools: Math.round(116 * LIST_TOOLBAR.scale), slim: 60 });
export function listCardBox(layout, { searching = false, horizontal = false, band = LIST_BAND.tools } = {}) {
  const b = layout.list, f = layout.frame;
  const panel = { x: b.l + f.w / 2, y: f.cy + f.h / 2 - b.t, w: b.r - b.l, h: b.t - b.b };
  // the drawn paper SPACE.m under the toolbar's band (SPACE.m above it too, BL-176), the cards' box SPACE.m inside it
  const paper = container({ ...panel, padding: [band + 3 * SPACE.m
    + (searching && !horizontal ? CONTROL.buttonS.h + SPACE.m : 0), SPACE.m, SPACE.m, SPACE.m] }).inner;
  // The grid owns the card viewport; native card envelopes and their un-stretched pitch are placed inside it.
  return grid(paper).span(12);
}
const LP = listLayout();
// a segment's caption: a string key, or {text} for a caption the caller wrote (a count in it)
const segText = (seg) => (typeof seg === "string" ? T(seg) : seg?.text ?? "");
// the student list's tools at the top right of the list: order, sort, search and, with segs, the segment filter; check:
// the roster's checkbox before them. One toolbar, the roster's, fitted by its own steps to the list's width (BL-176: the
// list popups are the L class, too narrow for the prefab's 230-wide segments and 420-wide sort button).
function toolbar(right, y, { segs = null, sortKey = "sort.default", active = 0, ascending = true, filtered = false, toggle = null, sortKeys = [sortKey] } = {}) {
  return fittedToolbar(right, y, { segs, sortKey, sortKeys, active, ascending, filtered, toggle });
}
// the icon of a list's toggle in the toolbar (Show Other Students, BL-176; the owner's pick of the game's icons)
export const TOGGLE_ICON = Object.freeze({ sprite: "Common_Charlist_Icon_ViewSmall", atlas: "Common", size: [57, 57] });

// the fit of that toolbar: the checkbox's ``label``, the ``segs`` captions' keys and the sort button's ``sortKeys`` (``sortKey``:
// the one shown), between the list's left edge and ``right``: the segments at the left, the icons at the right (BL-176)
export function checkedToolbarFit(right, { segs, sortKey, sortKeys, label = null, left = LP.list.l + SPACE.m, icons = 0 }) {
  return toolbarFit({ left, right, filters: segs ? [segs.map(segText)] : [], sorts: sortKeys.map((key) => T(key)),
    sortAt: sortKeys.indexOf(sortKey), check: label, icons, spread: true });
}
// The toolbar with a checkbox (Switch 3D Model's "Show other students", BL-131) is the student roster's: the checkbox
// first, the three segments, search, sort and order, fitted by the roster's own steps (roster-widgets.js toolbarFit; the
// sort button as wide as its longest key). Returns the tree, where the segments and the checkbox stand for the hit areas.
// The toolbar is drawn at LIST_TOOLBAR.scale, its right edge at `right`: it is fitted to the list's width over that scale.
// toggle: {label, checked, onChange} for an icon toggle before search (TOGGLE_ICON).
function fittedToolbar(right, y, { segs, sortKey, sortKeys, active, ascending, filtered, toggle }) {
  const k = LIST_TOOLBAR.scale, px = right * (1 - k), at = (x) => px + k * x;
  const lay = checkedToolbarFit(right, { segs, sortKey, sortKeys, left: (LP.list.l + SPACE.m - px) / k, icons: toggle ? 1 : 0 });
  const tree = toolbarTree(layouts.collection, layouts.checkbox || checkboxPrefab(), {}, false, lay,
    { filters: segs ? [{ name: "ModFilter", active }] : [], filtered, ascending,
      toggles: toggle ? [{ name: "ToggleBtn", ...TOGGLE_ICON, pressed: !!toggle.checked }] : [] });
  tree.name = "Toolbar";
  tree.scale = [k, k];
  tree.pos = [px, y - k * TOOLBAR_CENTRE_Y];
  tree.children = tree.children.filter((child) => ["ModFilter", "SearchBtn", "SortBtn", "OrderBtn", "ToggleBtn"].includes(child.name));
  // the segments span the filter's right edge back three widths (stage units, at the toolbar's scale)
  const icon = lay.icons?.[0];
  return { tree, lay, segBox: segs ? { right: at(lay.seg1 + 6), segW: k * lay.segW, y, scale: k } : null,
    toggle: toggle && icon ? { x: at(icon.x + icon.w / 2), w: k * icon.w, h: k * icon.h, y } : null };
}

// The list and details panels occupy the XL content grid; its footer uses the shared centred group.
// The list popups' footer is the XS every popup has (owner, 2026-11-11, BL-176; it was S, one size under the other popups'
// M, earlier the same day); footer: another size for one list.
export const LIST_FOOTER = FOOTER_DEFAULT;
export function listFrame(title, buttons, closeButton, check = false, kind = null, band = LIST_BAND.tools, footer = LIST_FOOTER) {
  const layout = listLayout(check, kind);
  // the footer at the frame's bottom right (owner, 2026-11-11, BL-176)
  const t = popupFrame(title, layout.frame.w, layout.frame.h, { close: closeButton, footer, align: "right",
    buttons: buttons.map((b, i) => ({ ...b, name: `Btn_${i}` })) });
  const popup = find(t, "Popup"), btn = find(popup, "Btn");
  btn.children = btn.children.map((child, i) => buttons[i].greyed ? Buttons.disable(child) : child);
  const panel = (name, b, tone) => node(name, [(b.l + b.r) / 2, (b.t + b.b) / 2],
    [sprite("Common_Bg_Raius10px", b.r - b.l, b.t - b.b, { depth: 3, color: tone })]);
  const b = layout.list;
  const paper = container({ x: b.l, y: -b.t, w: b.r - b.l, h: b.t - b.b,
    padding: [band + 2 * SPACE.m, SPACE.m, SPACE.m, SPACE.m] }).inner;  // SPACE.m above and below the toolbar (BL-176)
  popup.children.push(panel("LeftPanel", layout.left, "#ffffff/0.00"), panel("ListPanel", b, "#d7eaf4/1.00"),
    node("ListPaper", [paper.x + paper.w / 2, -paper.y - paper.h / 2],
      [sprite("Common_Bg_Raius10px", paper.w, paper.h, { depth: 4, color: "#f4fcfe/1.00" })]));
  return t;
}

// one card of the list: the game's card body, the picture clipped to its window, a name bar under it. A card that
// cannot be chosen (dim) is darkened like the roster's not-owned cards, its picture without colour; its badge (In Use,
// or the caller's) stays bright.
// tick: null, or whether the card is ticked, for a list whose cards are ticked apart from being shown (listPopup
// tick): a check box at the card's top right corner, the selection frame then without its check (it marks the card
// the details show)
export const TICK_BOX = Object.freeze({ w: 60, h: 58, icon: 48, inset: 42 });
export const tickPlace = (card) => [card.w / 2 - TICK_BOX.inset + (card.h / 2 - TICK_BOX.inset) * TAN10, card.h / 2 - TICK_BOX.inset];
export function listCard(it, x, y, selected, card = LP.card, kind = null, tick = null) {
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
    tick === null ? [node("Check", [w / 2 - 28 + (h / 2 - 30) * TAN10, h / 2 - 30], [sprite("Card_Ability_Select_Line_Icon", 66, 65, { type: 0, depth: 21 })])] : []));
  // the tick box, as the checkbox prefab draws one (Btn_Checkbox: plate, line, check), in the corner
  if (tick !== null) kids.push(node("Tick", tickPlace(card), [sprite("Common_Bg_Raius10px", TICK_BOX.w, TICK_BOX.h, { incline: 10, depth: 25, color: "#e4f3f8/1.00" })], [
    node("Line", [0, 0], [sprite("Common_Line_Raius10px", TICK_BOX.w, TICK_BOX.h, { incline: 10, depth: 26, color: "#96b6c2/1.00" })]),
    ...(tick ? [node("Icon", [0, -1], [sprite("Common_Icon_Check_B", TICK_BOX.icon, TICK_BOX.icon - 1, { depth: 27 })])] : [])]));
  const tree = node(`Card_${it.key}`, [x, y], [sprite("Common_Btn_BG", w, h, { incline: 10, depth: 7, color: "#ffffff/1.00" })], kids);
  if (typeof it.applied === "boolean") {
    // Keep the existing list's measured envelope, hit boxes and scroll pitch when replacing its decoration.
    tree.layoutChildren = [...kids];
    tree.children = kids.filter(n => n.name !== "Select");
    // frameCaption false: the frame alone, no Applied word in it (the Hall's chosen background, BL-176)
    const caption = it.frameCaption !== false && (it.applied || selected) ? selectionCaptions(card)?.[it.applied ? "applied" : "preview"] : null;
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

// The detail actions: the XS body, their captions 34 down to 24 (owner, 2026-11-11, BL-176: smaller, with more padding),
// the button's padding each side of the caption (the body's slant inside it). All of them on one row (owner, BL-176): the
// XXS body, its captions 30 down to 22, where the row of XS bodies at their minimum width does not fit.
const ACTION_TIERS = Object.freeze([
  Object.freeze({ control: CONTROL.buttonXS, fs: 34, min: 24 }),
  Object.freeze({ control: CONTROL.buttonXXS, fs: 30, min: 22 }),
]);
// The small button body's soft drop shadow under its solid art: 14 of its 176 rows (Btn_Cancel/BG (1) as drawn).
const ACTION_SHADOW = 14 / 176;
// The details panel one size down (owner, 2026-11-11, BL-173): its title and the name TYPE.body (the size under
// TYPE.heading), its chips the S chip (CHIP_TIERS), as wide as their captions, its light box STATS_BOX_S; its actions keep
// the XS button, the smallest. The chips stand on the name's line where they fit beside it, SPACE.s apart.
export const DETAILS = Object.freeze({ title: TYPE.body, name: TYPE.body, chip: CHIP_TIERS.S, box: STATS_BOX_S, chipGap: SPACE.s });
const CHIP_MIN_GAP = SPACE.xs;
// a sized chip (CHIP_TIERS) for a caption: its width (the caption, the recruit mark before it, the pad on each side; minW at
// least, maxW at most) and the caption's fit
export function chipFit(caption, { chip = DETAILS.chip, mark = false, maxW = Infinity } = {}) {
  const iconW = mark ? chip.fs * 1.35 / RECRUIT.ratio + 6 : 0;
  const need = Math.ceil(iconW + glyphsOf(caption, chip.fs) + 2 * chip.pad);
  return { w: Math.min(maxW, Math.max(chip.minW, need)), h: chip.h };
}
// Chips side by side in rows within panelW: each as wide as `widths` says, `gap` apart, a row centred, a new row where the
// next one would not fit (its incline counted). Gives each chip's row and its centre's x from the panel's centre;
// wrap: false keeps them on one row (the details panel's, whose widths the caller fitted to the panel).
export function chipPlaces(widths, panelW, { h = DETAILS.chip.h, gap = DETAILS.chipGap, wrap = true } = {}) {
  if (typeof widths === "number") widths = Array.from({ length: widths }, () => CHIP.w);
  const slant = h * TAN10, rows = [[]];
  for (const [i, w] of widths.entries()) {
    const cur = rows.at(-1), used = cur.reduce((n, j) => n + widths[j] + gap, 0);
    if (wrap && cur.length && used + w + slant > panelW) rows.push([i]); else cur.push(i);
  }
  const boxes = [];
  rows.forEach((indices, r) => {
    const placed = row(indices.map((i) => ({ w: widths[i], h })), { w: panelW, gap: indices.length > 1 ? Math.max(CHIP_MIN_GAP, gap) : 0, justify: "center" });
    placed.boxes.forEach((b, k) => boxes[indices[k]] = { row: r, x: b.x + b.w / 2 - panelW / 2 });
  });
  return boxes;
}
// the "…" at the end of a value cut to one line (owner, 2026-11-11: the panel does not scroll, a value shows whole in a
// popup): the line button (LINE_BUTTON) of the light box's line
export function moreButton(fs = DETAILS.box.value.fs) {
  const h = Math.round(fs * NGUI.LINE_PITCH * LINE_BUTTON.height) + XS_BUTTON.boxInsetH;
  const cap = Math.max(LINE_BUTTON.fsMin, Math.round(fs * LINE_BUTTON.share));
  return { h, fs: cap, w: Math.ceil(glyphsOf(MORE, cap)) + LINE_BUTTON.pad, gap: SPACE.s };
}
const MORE = "…";
// the details panel: title bar, the picture, name and chips, fields, actions (exported for the tests, like chipPlaces).
// options.truncate: each field's value on one line, a "…" button opening it whole where it is cut (tree.more lists them).
// The picture takes what the rest leaves of the panel, so the panel does not scroll; the actions stand on its bottom.
export function detailsTree(it, title, fields, actions, portraitTexture = null, kind = null, panel = LP.left, options = {}) {
  const w = panel.r - panel.l, cx = (panel.l + panel.r) / 2;
  const frame = { w, h: 0, cx, cy: panel.t }, content = container({ w, h: panel.t - panel.b }).inner;
  const kids = [], bands = [], more = [];
  const titleText = textItem(NGUI, title, DETAILS.title, { maxW: w - 2 * SPACE.xxl, lines: 1 });
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
  const infoW = infoColumn.w, edit = it.nameEdit ? CONTROL.icon.h + SPACE.m : 0, nameRoom = infoW - 2 * SPACE.m - edit;
  const nameFit = messageText(it.full, DETAILS.name.fs, DETAILS.name.min, nameRoom, 2, true);
  const name = textItem(NGUI, nameFit.text, { fs: nameFit.fs, min: nameFit.fs }, { maxW: nameRoom, lines: Infinity });
  const nameW = Math.min(nameRoom, Math.ceil(Math.max(...nameFit.text.split("\n").map((line) => glyphsOf(line, nameFit.fs)))));
  const using = it.inUseText || (it.inUse ? T("popup.inUse") : null);
  const chips = it.chips || [it.tag?.text || (it.recruit ? T("type.recruit") : it.kind === "mod" ? T("type.added") : T("type.noRecruit")), ...(using ? [using] : [])];
  const dark = !it.chips && it.recruit, chipRoom = infoW - 2 * SPACE.m;
  // the chips stay on one row (owner, 2026-11-11: never wrapped one under another): where their captions need more than the
  // column at the S chip, the XS chip (one size down, BL-176: the L class's narrower panel); where they still do, the widest
  // give up width first (one cap for all), and a squeezed caption may shrink to the XS chip's floor
  const widthsAt = (tier) => chips.map((caption, i) => chipFit(caption, { chip: tier, mark: i === 0 && dark, maxW: chipRoom }).w);
  const gapsAt = (tier) => Math.max(0, chips.length - 1) * DETAILS.chipGap + tier.h * TAN10;
  const sum = (ws) => ws.reduce((n, cw) => n + cw, 0);
  const chip = sum(widthsAt(DETAILS.chip)) + gapsAt(DETAILS.chip) <= chipRoom ? DETAILS.chip : CHIP_TIERS.XS;
  const chipGaps = gapsAt(chip);
  let chipW = widthsAt(chip);
  const squeezed = chipW.reduce((n, cw) => n + cw, 0) + chipGaps > chipRoom;
  if (squeezed) {
    const room = chipRoom - chipGaps, sorted = [...chipW].sort((a, b) => a - b);
    let cap = room / chips.length;
    for (let k = 0, used = 0; k < sorted.length; used += sorted[k], k += 1) {
      const c = (room - used) / (sorted.length - k);
      if (sorted[k] >= c) { cap = c; break; }
    }
    chipW = chipW.map((cw) => Math.min(cw, Math.floor(cap)));
  }
  const chipFloor = squeezed ? CHIP_TIERS.XS.fsMin : chip.fsMin;
  const chipsW = chipW.reduce((n, cw) => n + cw, 0) + chipGaps;
  // the chips beside a one-line name when the line holds both, else on their own row under it; a 3D model's beside its
  // picture always under the name (owner, 2026-11-11)
  // a portrait's name always centred on its own line, its chips under it (owner, 2026-11-11, BL-176)
  const beside = !side && !portraitTexture && chips.length > 0 && !nameFit.text.includes("\n") && nameW + edit + SPACE.l + chipsW <= chipRoom;
  const places = beside ? null : chipPlaces(chipW, chipRoom, { wrap: false, h: chip.h }), rows = !beside && chips.length ? Math.max(...places.map((p) => p.row)) + 1 : 0;
  const headH = beside ? Math.max(name.h, chip.h) : name.h;
  const info = [{ name: "Head", w: infoW, h: headH }];
  kids.push(node("Name", [0, 0], [label(name.text, name.fs, "#2d4664/1.00", beside ? nameW + 2 : nameRoom, name.h, { depth: 8, ellipsis: !!it.original, ariaLabel: it.full })]));
  if (it.nameEdit) kids.push(node("EditName", [0, 0], [sprite("Cafe_Icon_NameEdit", CONTROL.icon.h, CONTROL.icon.h, { type: 0, depth: 9, color: "#2d4664/1.00" })]));
  chips.forEach((caption, i) => kids.push(node(`Chip_${i}`, [0, 0],
    [sprite("Common_Bg_Raius10px", chipW[i], chip.h, { incline: 10, depth: 6, tier: "chip", color: i === 0 && dark ? "#2c4564/1.00" : "#ffffff/1.00" })], [
      node("Line", [0, 0], [sprite("Common_Line_Raius10px", chipW[i], chip.h, { incline: 10, depth: 7, color: "#c0c9d4/1.00" })]),
      node("Text", [0, 0], [], markedLabel(caption, { fs: chip.fs, fsMin: chipFloor,
        color: i === 0 && dark ? "#ffffff/1.00" : "#2c4564/1.00", maxW: chipW[i] - 2 * chip.pad, mark: i === 0 && dark, depth: 8, maxLines: 1 }).nodes),
    ])));
  if (rows) info.push({ name: "Chips", w: infoW, h: rows * chip.h + (rows - 1) * SPACE.s });
  if (it.original) {
    const original = textItem(NGUI, it.original, TYPE.micro, { maxW: infoW - 2 * SPACE.m, lines: 2 });
    kids.push(node("OriginalName", [0, 0], [label(original.text, original.fs, "#2d4664/0.65", infoW - 2 * SPACE.m, original.h, { depth: 8, ellipsis: true, ariaLabel: it.original })]));
    info.splice(1, 0, { name: "OriginalName", w: infoW, h: original.h });
  }
  // The light stats box keeps its own padding, row sizes and decoration, one size down. A portrait's narrow column stacks
  // each caption and value as successive full-width rows; other sources retain the paired columns. A field without a caption
  // (null) is its value alone. With options.truncate a value keeps one line, and a "…" at its end where it is cut.
  const button = moreButton();
  const cutRow = (field, caption, value) => options.truncate ? {
    more: button.w + button.gap,
    slot: ({ i, right, valueY, cut }) => {
      if (!cut) return [];
      more.push({ field, row: i, caption: caption ?? "", value: String(value) });
      const b = Buttons.smallButton(`More_${field}`, right - button.w / 2, valueY, MORE,
        { w: button.w, h: button.h, fs: button.fs, min: button.fs, depth: 9 });
      const bg = b.widgets.find((x) => "sprite" in x);
      if (bg) bg.tier = "button";
      return [b];
    },
  } : {};
  // the captions bold (owner, 2026-11-11, BL-176). A portrait's column, or a box whose captions leave a value no room for
  // its lines, stacks each caption over its value: the two rows joined, no divider between.
  const stackedRows = () => fields.flatMap(([caption, value, colour], field) => [...(caption == null ? [] : [{ value: caption, bold: true, joined: true }]),
    { value: String(value), colour, ariaLabel: String(value), ...cutRow(field, caption, value) }]);
  const pairedRows = () => fields.map(([caption, value, colour], i) => ({ caption: caption ?? "", value: String(value), colour, ariaLabel: String(value),
    cut: options.truncate ? 0 : i === 2 ? 2 : 0, ...cutRow(i, caption, value) }));
  const fieldW = side ? content.w : infoW;
  const boxOf = (rows) => statsBox({ holder: "panel", width: fieldW, at: [0, 0], name: "Fields", rows, type: DETAILS.box, boldCaptions: true });
  let fieldBox = { nodes: [], height: 0 }, stacked = !!portraitTexture;
  if (fields.length) {
    fieldBox = boxOf(stacked ? stackedRows() : pairedRows());
    if (!stacked && fieldBox.rows.some((r) => r.text.lines > DETAILS.box.value.lines)) { stacked = true; fieldBox = boxOf(stackedRows()); }
  }
  if (stacked) for (const part of fieldBox.nodes) {
    const match = /^V_(\d+)$/.exec(part.name);
    if (match) { const i = Number(match[1]); part.name = `${i % 2 ? "V" : "F"}_${Math.floor(i / 2)}`; }
  }
  kids.push(...fieldBox.nodes);
  const fieldBand = fields.length ? { name: "Fields", w: fieldW, h: fieldBox.height } : null;
  if (fieldBand && !side) info.push(fieldBand);
  const infoStack = layoutStack(info, { w: infoW, gap: SPACE.m });

  // The picture takes the room the title, the actions and the rest leave (the 3D model's square is one size for every
  // candidate: what fields with the file name on three lines and the others on one leave, its 10 degree incline within its
  // column; only longer fields shrink it).
  // the actions in rows of as many XS buttons as the panel holds at their minimum width (BL-176: the narrower panel, the
  // captions' padding kept), the rows SPACE.m apart
  const actionGap = SPACE.m, rowFits = (tier) => actions.length * (tier.control.minW + actionGap) - actionGap <= w - 2 * SPACE.m;
  // more than even the XXS row holds (a Mod's five): rows of XS bodies, as many a row as the panel holds at their minimum
  const oneRow = ACTION_TIERS.find(rowFits), actionTier = oneRow ?? ACTION_TIERS[0], BTN = actionTier.control;
  const ACTION_CLEAR = 2 * BTN.pad, ACTION_TEXT_H = BTN.h - 8;
  const perRow = oneRow ? Math.max(1, actions.length) : Math.max(1, Math.floor((w - 2 * SPACE.m + actionGap) / (BTN.minW + actionGap)));
  const actionRows = actions.length ? Math.ceil(actions.length / perRow) : 0;
  const actionsH = actionRows ? actionRows * BTN.h + (actionRows - 1) * actionGap : 0;
  const roomFor = (otherH, others) => content.h - titleH - otherH - actionsH - SPACE.m * (1 + others + (actions.length ? 1 : 0));
  const reference = side && fields.length ? boxHeight(fields.map((_, i) => (i === 0 && !options.truncate ? 3 : 1)), DETAILS.box) : 0;
  const texture = portraitTexture && clone(portraitTexture), native = texture?.widgets.find(widget => "texture" in widget);
  const portraitCard = texture && switchGrid("spr").card; // Keep the portrait card's native aspect in its preview column.
  const aspect = portraitCard ? portraitCard.w / portraitCard.h : kind === "model" ? 1 : 16 / 9;
  let pictureW;
  if (portraitTexture) pictureW = Math.max(0, Math.min(pictureColumn.w, roomFor(0, 0) * aspect));
  else if (side) pictureW = Math.max(0, Math.min(pictureColumn.w / (1 + TAN10), roomFor(reference, fields.length ? 1 : 0),
    roomFor(fieldBand?.h || 0, fields.length ? 1 : 0)));
  else pictureW = Math.max(0, Math.min(w - 2 * SPACE.m, roomFor(infoStack.h, 1) * aspect));
  const pictureH = pictureW / aspect;
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

  // every row's bodies fitted together (one caption size for all the actions), each row centred
  const gap = actionGap, inRow = Math.min(perRow, Math.max(1, actions.length)), bw = (w - 2 * SPACE.m + gap) / inRow;
  const groups = Array.from({ length: actionRows }, (_, r) => actions.slice(r * perRow, (r + 1) * perRow).map(([caption]) => caption));
  const groupFits = groups.map((captions) => NGUI.fitRow(captions, actionTier.fs, actionTier.min, captions.length * (bw - gap), ACTION_CLEAR, 2, { maxH: ACTION_TEXT_H }));
  const shared = Math.min(actionTier.fs, ...groupFits.flat().map((f) => f.fs));
  const fits = groups.flatMap((captions, r) => groupFits[r].some((f) => f.fs !== shared)
    ? NGUI.fitRow(captions, shared, shared, captions.length * (bw - gap), ACTION_CLEAR, 2, { maxH: ACTION_TEXT_H }) : groupFits[r]);
  const actionRow = { boxes: groups.flatMap((captions, r) => {
    const start = groups.slice(0, r).reduce((n, g) => n + g.length, 0);
    return row(fits.slice(start, start + captions.length).map((f) => ({ w: f.width, h: BTN.h })),
      { x: SPACE.m, w: w - 2 * SPACE.m, gap, justify: "center" }).boxes.map((b) => ({ ...b, row: r }));
  }) };
  actions.forEach(([caption, , disabled, semantic = "secondary"], i) => {
    let body = clone(find(layouts.sortPopup, "Center/Popup/Btn/Btn_Cancel/BG (1)")), ink = "#2d5164/1.00";
    if (disabled) {
      const grey = Buttons.disable(node("Action", [0, 0], [], [body]));
      body = clone(find(grey, "BG (1)")); ink = find(grey, "Label_Locale").widgets[0].color;
    }
    const bg = body.widgets.find(widget => "sprite" in widget), k = BTN.h / bg.h_;
    body.name = "Body"; body.pos = [0, 0]; body.scale = [k, k]; resizeButton(body, fits[i].width / k);
    // a button however narrow the row makes it: the pre-release size tag marks it (owner, 2026-11-11)
    Object.assign(bg, { depth: 6, tier: "button", ...(!disabled ? { color: "#dbeef5/1.00" } : {}) });
    for (const child of body.children || []) for (const widget of child.widgets) if ("w_" in widget) widget.depth = 7;
    const action = node(`Act_${i}`, [0, 0], [], [body, node("Label", [0, 0],
      [label(fits[i].text, fits[i].fs, ink, fits[i].width - ACTION_CLEAR, ACTION_TEXT_H, { depth: 8 })])]);
    action.actionSemantic = disabled ? "secondary" : semantic; kids.push(action);
  });
  if (actions.length) bands.push({ name: "Actions", w, h: actionsH });
  const placed = layoutStack(bands, { x: content.x, y: content.y, w: content.w, gap: SPACE.m });
  // The actions stand on the panel's bottom, their solid art level with the list panel's (BL-133; every panel, owner
  // 2026-11-11).
  const pinned = actions.length && placed.h <= content.h;
  if (pinned) placed.boxes.at(-1).y = content.y + content.h - actionsH + BTN.h * ACTION_SHADOW;
  const tree = node("Details", [0, 0], [], kids);
  const placeInfo = (box, x = 0) => {
    const target = { ...box, x: x + box.x };
    if (box.name === "Fields") {
      const [px, py] = toNode(target, 1, frame);
      for (const part of fieldBox.nodes) { part.pos[0] += px; part.pos[1] += py; }
    } else if (box.name === "Chips") chips.forEach((_, i) => {
      find(tree, `Chip_${i}`).pos = toNode({ x: target.x + infoW / 2 + places[i].x - chipW[i] / 2,
        y: target.y + places[i].row * (chip.h + SPACE.s), w: chipW[i], h: chip.h }, 4, frame);
    });
    else if (box.name === "Head") {
      // the name, its pencil and (beside) the chips as one centred group
      const parts = [{ w: beside ? nameW : nameRoom, h: name.h }, ...(it.nameEdit ? [{ w: CONTROL.icon.h, h: CONTROL.icon.h }] : []),
        ...(beside ? chipW.map((cw) => ({ w: cw, h: chip.h })) : [])];
      const gaps = parts.map((_, i) => (i === 0 ? 0 : beside && i === (it.nameEdit ? 2 : 1) ? SPACE.l : i === 1 && it.nameEdit ? SPACE.m : DETAILS.chipGap));
      const total = parts.reduce((n, p, i) => n + p.w + gaps[i], 0) + (beside ? chip.h * TAN10 : 0);
      let left = target.x + (infoW - total) / 2;
      parts.forEach((p, i) => {
        left += gaps[i];
        const at = toNode({ x: left, y: target.y + (target.h - p.h) / 2, w: p.w, h: p.h }, 4, frame);
        if (i === 0) find(tree, "Name").pos = at;
        else if (i === 1 && it.nameEdit) find(tree, "EditName").pos = at;
        else find(tree, `Chip_${i - 1 - (it.nameEdit ? 1 : 0)}`).pos = at;
        left += p.w;
      });
    } else find(tree, box.name).pos = toNode(target, 4, frame);
  };
  for (const box of placed.boxes) {
    if (box.name === "Portrait") {
      const portraitRow = row([{ w: side ? pictureW : pictureColumn.w, h: pictureH }, { w: infoW, h: infoStack.h }],
        { x: side ? pictureColumn.x + (pictureColumn.w - pictureW) / 2 : 0, y: box.y, w, gap: SPACE.xl, align: "center" }).boxes;
      picture.pos = toNode(portraitRow[0], 4, frame);
      // the name, chips and fields centred down the picture's height (owner, 2026-11-11, BL-176)
      const top = portraitRow[1].y - box.y;
      for (const part of infoStack.boxes) placeInfo({ ...part, y: part.y + box.y + top }, infoColumn.x);
    } else if (box.name === "Actions") actionRow.boxes.forEach((action, i) => {
      find(tree, `Act_${i}`).pos = toNode({ ...action, y: box.y + action.row * (BTN.h + gap) }, 4, frame);
    });
    else if (["Title_Bg", "Picture"].includes(box.name)) find(tree, box.name).pos = toNode(box, 4, frame);
    else placeInfo(box);
  }
  tree.contentHeight = pinned ? content.h : placed.h;
  tree.contentLayout = placed;
  tree.more = more;
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
//   tools: {segs, seg, onSeg, sortKey, more, onView} for the student list's tools over the cards, or null; without
//          them, hint (left) and footer (right) show in their place. more(query): cards added after the matching ones
//          (Switch 3D Model's other students' models, BL-131; asked on every drawing, with the search's words, which may be
//          empty), ordered and turned like them. onView(keys): the keys of the cards in view, after every drawing and
//          scroll (the same signal that brings their thumbnails in). toggle: {label, checked, onChange(checked)}, an icon
//          toggle (TOGGLE_ICON) before search, its label the hover hint; a click flips toggle.checked and draws again.
//   items' hint: the card's hover hint
//   check: {label, checked, onChange} for a checkbox below the details; a click flips check.checked, so the same
//          object is passed to every drawing
//   details(it): {title, fields: [[caption, value, colour]], actions: [[caption, fn]], truncate} for the panel on the left;
//                truncate: each value on one line, a "…" button showing a cut one whole
//   footer: the line over the paper's right (the ticked cards, their size); the footer buttons are LIST_FOOTER's size
//   keepScroll: the list's offset on its scrolling axis (horizontal for portraits)
//   tick: {keys, onTick(key, scroll)}: cards ticked apart from being shown (owner, 2026-11-11): a click on a card shows
//         it in the details (onSelect, selected), a click on its corner's tick box ticks or unticks it (onTick)
//   empty: what an empty list says, in the middle of its paper (hall.noCandidates by default)
//   detailKind(it): the kind whose details panel a card shows when it differs from layoutKind's ("spr": the portrait
//                   panel, an upright picture beside the stacked fields), for lists of several kinds
//   alignStart: the generic grid's cards from the paper's left edge, as the kinds' grids place them
//   slimBand: without tools and hint, the band over the paper only as high as the footer's count
const toolStates = new WeakMap();
const tokens = new Map(); // host -> the latest drawing's number: an older one that ends later is dropped
export async function listPopup(options) {
  let {
  host: hostArg, hostId, title, items, selected = null, multi = false, focus = null, onSelect, buttons = null, okText, onOk,
  onClose = null, tools = null, hint = "", footer = "", check = null, details, keepScroll = 0, tick = null, empty = null,
} = options;
  const host = hostOf(hostArg ?? hostId);
  const my = (tokens.get(host) || 0) + 1;
  tokens.set(host, my);
  const reopening = host.hidden;
  host.hidden = false;
  await ready();
  let portraitTexture = null;
  if (items.some(it => typeof it.applied === "boolean")) await selectionReady();
  const detailKind = options.detailKind || (() => options.layoutKind);
  if (options.layoutKind === "spr") {
    const inventory = await NGUI.layout("UINonEquipmentInventory");
    portraitTexture = find(inventory, "Inventory_ListControl/EquipPos/Texture");
  } else if (options.detailKind && items.some((it) => options.detailKind(it) === "spr")) {
    // a list of several kinds: without the prefab its portraits' details fall back to the generic panel
    const inventory = await NGUI.layout("UINonEquipmentInventory").catch(() => null);
    portraitTexture = inventory ? find(inventory, "Inventory_ListControl/EquipPos/Texture") : null;
  }
  const LP = listLayout(!!check, options.layoutKind);
  // the cards' columns follow the list's paper (BL-176: the L class's narrower list holds fewer)
  const cardGrid = { ...switchGrid(options.layoutKind, listCardBox(LP).w), ...(options.alignStart ? { leftAligned: true } : {}) };
  const { card, columns, pitch, horizontal } = cardGrid;
  const band0 = options.slimBand && !tools && !hint ? LIST_BAND.slim : LIST_BAND.tools;
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
  // cancelText: a bright blue Cancel before the primary button (owner, 2026-11-11, BL-176: the switch popups)
  const btns = (buttons || [...(options.cancelText && onClose ? [{ label: options.cancelText, fn: () => onClose("cancel"), tone: "bright", action: "cancel" }] : []),
    { label: okText, fn: onOk, primary: true, semantic: options.okSemantic || "confirm", action: "ok" }])
    .map((b) => (b.primary && filteredOut ? { ...b, greyed: true, blocked: true } : b));
  const next = document.createElement("div");
  const view = await NGUI.render(listFrame(title, btns, !!onClose, !!check, options.layoutKind, band0), next, { scale: S, origin: [960, 540] });
  btns.forEach((b, i) => {
    const button = hit(next, view, `Center/Popup/Btn/Btn_${i}/BG (1)`, b.blocked ? () => {} : b.fn, { name: b.label, action: b.action || `button${i}` });
    if (button && b.blocked) { button.disabled = true; button.setAttribute("aria-disabled", "true"); }
  });
  if (onClose) hit(next, view, "Center/Popup/Base/BG/Btn_Close", () => onClose("close"), { name: T("display.close"), action: "close" });
  closable(view, host, onClose);
  const band = [];
  if (tools && layouts.collection && !(horizontal && toolState.searching)) {
    const sortKeys = [...new Set([tools.sortKey ?? "sort.default", "sort.name"])];
    // the toolbar centred in its band: SPACE.m above and below it (BL-176)
    const tb = toolbar(LP.list.r - SPACE.m, LP.list.t - SPACE.m - LIST_BAND.tools / 2, { segs: tools.segs, sortKey: toolState.sort === "name" ? "sort.name" : tools.sortKey, sortKeys,
      active: tools.seg ?? 0, ascending: toolState.ascending, filtered: !!toolState.query, toggle: tools.toggle });
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
    if (tb.segBox && tools.onSeg) {
      const { right, segW, y, scale: k } = tb.segBox, h = CONTROL.toolbar.h * k;
      for (let i = 0; i < 3; i++) {
        const ux = right - (3 - i) * segW + segW / 2 - 6 * k;
        hitButton(next, [960 + (ux - segW / 2) * S, 540 - (y + h / 2) * S, segW * S, h * S], () => tools.onSeg(i), segText(tools.segs[i]), 80);
      }
    }
    if (tb.toggle) {
      const { x, w, h, y } = tb.toggle, state = tools.toggle;
      const box = hitButton(next, [960 + (x - w / 2) * S, 540 - (y + h / 2) * S, w * S, h * S], () => {
        state.checked = !state.checked;
        state.onChange?.(state.checked);
        redraw();
      }, state.label, 80);
      box.dataset.action = "toggle";
      box.setAttribute("aria-pressed", String(!!state.checked));
      attachHint(box, state.label);
    }
  } else {
    // the band over the cards: a hint at the left, the footer (the ticked cards, their size) at the right
    const room = LP.list.r - LP.list.l - 80;
    const ft = footer ? fitText(footer, 40, 28, room / 2, 1) : null;
    const ht = hint ? fitText(hint, 40, 28, room - (ft ? glyphsOf(ft.text, ft.fs) + 40 : 0), 2) : null;
    if (ht) band.push(node("Hint", [LP.list.l + 40, LP.list.t - SPACE.m - band0 / 2], [label(ht.text, ht.fs, "#2d4664/1.00", room, 50 * ht.lines, { pivot: 3, depth: 8 })]));
    if (ft) band.push(node("Footer", [LP.list.r - 40, LP.list.t - SPACE.m - band0 / 2], [label(ft.text, ft.fs, "#2d4664/1.00", room / 2, 50, { pivot: 5, depth: 8 })]));
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
    search.pos = [(LP.list.l + LP.list.r) / 2, LP.list.t - LIST_BAND.tools - SPACE.m - SPACE.xs];
    find(search, "SearchName").pos = [0, 0];
    if (horizontal) {
      const area = find(search, "SearchName/SearchNameArea"), w = area.widgets.find((w) => "sprite" in w);
      search.pos[1] = LP.list.t - SPACE.m - LIST_BAND.tools / 2 - area.pos[1] - (Math.floor((w.pivot ?? 4) / 3) / 2 - .5) * w.h_;
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
  const cardBox = listCardBox(LP, { searching: tools && toolState.searching, horizontal, band: band0 });
  const [cardLeft, top] = toNode(cardBox, 0, LP.frame), bottom = top - cardBox.h;
  if (!items.length) {
    // nothing to show: the list paper says so, as the Hall's pickers do
    const room = LP.list.r - LP.list.l - 160, f = fitText(empty ?? T("hall.noCandidates"), 48, 32, room, 2);
    await NGUI.render(node("Empty", [(LP.list.l + LP.list.r) / 2, (top + bottom) / 2],
      [label(f.text, f.fs, "#2d4664/1.00", room, f.fs * 1.3 * f.lines + 8, { depth: 8 })]), next, { scale: S, origin: [960, 540], z: 20 });
  }
  const [aL, aT] = [960 + cardLeft * S, 540 - top * S];
  // Measure the actual prefab widgets, including the name band's asymmetric incline and selection marks.
  const tickKeys = tick ? new Set(tick.keys || []) : null;
  const trees = items.map((it) => listCard(it, 0, 0, isSelected(it.key), card, options.layoutKind, tickKeys ? tickKeys.has(it.key) : null));
  const paper = { left: cardLeft, right: cardLeft + cardBox.w, top: top + SPACE.m };
  // a list whose cards are ticked: placed as if every card were selected, so ticking never moves the cards (owner,
  // 2026-11-11: the list's padding is fixed whatever is ticked); a list of one choice keeps its measured insets
  const locked = multi || !!tickKeys;
  const shapes = locked ? items.map((it) => listCard(it, 0, 0, true, card, options.layoutKind, tickKeys ? true : null)) : trees;
  placeSwitchCards(shapes, cardGrid, paper);
  // the cards' own ink where they stand, for the scroll extent
  const bounds = trees.map((tree, i) => {
    tree.pos = shapes[i].pos;
    return cardInkBounds(tree);  // the root's pos included
  });
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
    if (tickKeys) {
      const [tx, ty] = tickPlace(card), box = { w: TICK_BOX.w + 2 * SPACE.s, h: TICK_BOX.h + 2 * SPACE.s };
      const t = hitButton(inner, [sx + (tx - box.w / 2) * S, sy - (ty + box.h / 2) * S, box.w * S, box.h * S], () => tick.onTick(it.key, scroll()), it.full, 61);
      t.dataset.tick = it.key;
      t.setAttribute("role", "checkbox"); t.setAttribute("aria-checked", String(tickKeys.has(it.key)));
    }
  });
  const shown = multi ? focus ?? items.find((it) => ticked.has(it.key))?.key : selected;
  const sel = items.find((it) => it.key === shown) || items[0];
  if (sel && details) {
    const d = details(sel);
    const kind = detailKind(sel);
    const tree = detailsTree(sel, d.title, d.fields, d.actions, kind === "spr" ? portraitTexture : null, kind, LP.left, { truncate: !!d.truncate });
    const panel = LP.left, panelH = panel.t - panel.b;
    const detailsHost = tree.contentHeight > panelH ? scrollArea(next,
      [960 + panel.l * S, 540 - panel.t * S, (panel.r - panel.l) * S, panelH * S], tree.contentHeight * S) : next;
    await detailsHost.ready;
    const origin = detailsHost === next ? [960, 540] : [-panel.l * S, panel.t * S];
    const dv = await NGUI.render(tree, detailsHost, { scale: S, origin, z: 20 });
    if (sel.nameEdit) hit(detailsHost, dv, "Details/EditName", () => sel.nameEdit.fn(scroll()), { name: sel.nameEdit.label, action: "alias:details" });
    // a value cut to one line shows whole in a message over the list, titled with its caption
    for (const cut of tree.more) {
      const button = hit(detailsHost, dv, `Details/More_${cut.field}`, () => message({ title: cut.caption || d.title, lines: [cut.value],
        ok: T("picker.ok") }), { name: cut.caption || d.title, action: `more:${cut.field}` });
      button?.setAttribute("aria-description", cut.value);
    }
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
export const MSG = { title: POPUP_FRAME.contentTop, gap: SPACE.l, rowButton: XS_BUTTON.h + SPACE.m,
  check: CHECK_H + SPACE.m, buttons: POPUP_FOOTER.clearance,
  none: SPACE.xl, cap: POPUP_FRAME.maxH };

// message({title, lines, ok, cancel, check, host, width, close, confirmKeys, scroll}): a message on the popup frame, in host
// (#dialog by default, over everything but the message popup). Resolves once it is closed, however it is closed, with
// {button, checked}: button "ok" or "cancel", or null for the close button, the backdrop or Escape; checked, the
// checkbox's state then (null without one).
//   lines: text, or {text, button: {label, fn, inline}} for a line with a small button at its end (fn({close}), where
//          close(button) closes the popup; inline, with plain: only as high as the line's text, right after it; more:
//          [{label, fn}] further inline buttons after it, the same size); with such a line every line shows as a row of the game's light box
//          fontSize/minFontSize optionally keep a row's existing type size (the tour counter and body).
//          With plain: indent (units) moves a line right, marker hangs a bullet left of its first line (markerGap wide,
//          its font size by default), gap adds space above it (release notes' lists and sections), italic slants it.
//   fitTogether: line indices fitted at one shared size, using the smallest fit within the popup's size floor
//   plain: left-aligned lines without a box or separators, including lines with a button at the right; a line
//          {rule: true} is the game's dotted divider (Common_Line_Dot, as the light stats box draws it) across the text
//   ok, cancel: the buttons' labels (either may be left out); check: {label, checked}
//   buttons: optional named footer choices [{label, value, primary}], instead of ok/cancel
//   footer: the footer buttons' size, "XS", "S", "M" or "L" (FOOTER); without it the default size, XS, its bodies as wide as the captions need
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
  scroll = false, plain = false, width, height, fitTogether = [], captionSite = null, footer = null }, finish) {
  const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };
  const button = (caption, fn, action, cls = "message-button") => {
    const b = el("button", cls, caption); b.dataset.action = action; b.setAttribute("aria-label", caption);
    b.addEventListener("click", fn); return b;
  };
  const layout = messageLayout({ lines, ok, cancel, buttons, check: state, scroll, plain, width, height, fitTogether, assets: false, footer });
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
  const heading = textItem(NGUI, title, POPUP_TYPE.title, { maxW: frame.w - 2 * SPACE.xxxl, lines: 1 });
  box.appendChild(styleBox(el("h2", "", heading.text), { x: SPACE.xxxl, y: POPUP_FRAME.titleTop + (titleHeight(title) - heading.h) / 2,
    w: frame.w - 2 * SPACE.xxxl, h: heading.h }, NGUI.fontSizeFor(heading.text, heading.fs)));
  if (dismissable) box.appendChild(styleBox(button("×", () => finish(null), "close"),
    { x: frame.w - POPUP_FRAME.close.right - CONTROL.icon.h, y: POPUP_FRAME.close.top, w: CONTROL.icon.h, h: CONTROL.icon.h }, CONTROL.icon.h));
  if (dismissable) box.children.at(-1).setAttribute("aria-label", T("display.close"));
  const content = styleBox(el("div", "plain-message-lines"), { x: layout.content.x, y: FRAME_Y + frame.h / 2 - layout.y0,
    w: layout.content.w, h: layout.visible });
  Object.assign(content.style, { overflow: "auto", textAlign: plain ? "left" : "center" });
  const paragraphItems = layout.body.fitted.map(({ r, f, bf, bw, more = [], item: text }) => {
    return { r, f, bf, bw, more, text, w: layout.content.w,
      h: r.rule ? RULE.h : Math.max(r.button ? XS_BUTTON.h : 0, text.h) + (plain ? r.gap || 0 : 0) };
  });
  const paragraphs = layoutStack(paragraphItems, { w: layout.content.w, gap: SPACE.l });
  paragraphs.boxes.forEach(({ r, f, bf, bw, more, text, x, y, w }, i) => {
    if (r.rule) {
      const line = styleBox(el("div", "plain-message-rule"), { x: x + SPACE.xxxl, y, w: w - 2 * SPACE.xxxl, h: RULE.h });
      line.style.borderTop = `${Math.max(1, RULE.h * S)}px dotted ${STATS_BOX.divider}`;
      content.appendChild(line);
      return;
    }
    const indent = plain ? r.indent || 0 : 0;
    const taken = (r.button ? bw + SPACE.m : 0) + more.reduce((n, m) => n + m.bw + SPACE.m, 0);
    const parts = row([{ ...text, w: w - indent - taken }, ...(r.button ? [{ w: bw, h: XS_BUTTON.h }] : []),
      ...more.map((m) => ({ w: m.bw, h: XS_BUTTON.h }))],
      { x: x + indent, y: y + (plain ? r.gap || 0 : 0), w: w - indent, gap: SPACE.m, justify: "center" }).boxes;
    const p = styleBox(el("p", "", f.text), parts[0], NGUI.fontSizeFor(f.text, f.fs));
    content.appendChild(p);
    if (plain && r.marker) content.appendChild(styleBox(el("span", "", r.marker),
      { x: parts[0].x - (r.markerGap ?? f.fs), y: parts[0].y, w: r.markerGap ?? f.fs, h: NGUI.fontSizeFor(f.text, f.fs) * NGUI.LINE_PITCH }, NGUI.fontSizeFor(f.text, f.fs)));
    if (r.button) {
      const b = styleBox(button(bf.text, () => r.button.fn?.({ close: finish }), `line${i}`, "plain-line-button"), parts[1], NGUI.fontSizeFor(bf.text, bf.fs));
      b.setAttribute("aria-label", r.button.label); content.appendChild(b);
    }
    more.forEach((m, k) => {
      const b = styleBox(button(m.bf.text, () => m.button.fn?.({ close: finish }), `line${i}_${k + 1}`, "plain-line-button"), parts[2 + k], NGUI.fontSizeFor(m.bf.text, m.bf.fs));
      b.setAttribute("aria-label", m.button.label); content.appendChild(b);
    });
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
    const { kind: size, control, bodyW, room } = footerLayout(actions.map((action) => action.label), frame.w, footer);
    const fits = footerFit(actions.map(action => action.label), { site: captionSite, width: room, caption: size.caption });
    const actionRow = el("div", "message-actions");
    styleBox(actionRow, { x: 0, y: frame.h - footerBottom(control) - control.h, w: frame.w, h: control.h });
    const boxes = row(actions.map(() => ({ w: bodyW, h: control.h })), { w: frame.w, gap: POPUP_FRAME.footer.gap, justify: "center" }).boxes;
    actions.forEach((action, i) => {
      const b = styleBox(button(fits[i].text, () => finish(action.value), action.value), boxes[i], NGUI.fontSizeFor(fits[i].text, fits[i].fs));
      b.style.padding = `0 ${control.pad * S}px`; b.setAttribute("aria-label", action.label); actionRow.appendChild(b);
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

const placeholderButton = (name, x, y, caption, { w, h = XS_BUTTON.h }) => node(name, [x, y], [sprite("Common_Btn_BG", w, h)]);
// the inline buttons went one size down with the small button (owner, 2026-11-11, BL-173): the XS body's height over the S one's
const XS_RATIO = XS_BUTTON.h / SMALL_BUTTON.h;
// a plain message's divider row ({rule: true}): the light stats box's dotted line, its height
export const RULE = Object.freeze({ h: 4 });
// a plain line's inline button (button.inline): the small button, shadow included, within its line's text from top to
// bottom (``height`` of the type size: the ink of a line, not its pitch), its caption at most ``share`` of the type size
// pad: the small button fits its caption again within its width less XS_BUTTON.fitInset, so the pad is at least that
export const INLINE_BUTTON = Object.freeze({ height: 0.9 * XS_RATIO, share: 0.55 * XS_RATIO, fsMin: 20, pad: XS_BUTTON.fitInset });
// button.inline "line": the button's face as high as its line of text (the line's pitch, the small button's shadow
// inset added around it), its caption ``share`` of the type size, ``fsMin`` at least (the compatibility result's Details,
// owner 2026-11-11)
export const LINE_BUTTON = Object.freeze({ share: 0.6 * XS_RATIO, height: XS_RATIO, fsMin: TYPE.badge.min, pad: XS_BUTTON.fitInset });
function inlineButton(r, fs, w) {
  const first = oneInlineButton(r, r.button, fs);
  // more inline buttons after the first (r.more: [{label, fn}]), the same size, SPACE.m apart
  const more = (r.more || []).map((button) => ({ button, ...oneInlineButton(r, { ...button, inline: r.button.inline }, fs) }));
  const taken = [first, ...more].reduce((n, b) => n + b.bw + SPACE.m, 0);
  return { bf: first.bf, bw: first.bw, bh: first.bh, more, room: w - 2 * SPACE.xxxl - taken - (r.indent || 0) };
}
function oneInlineButton(r, button, fs) {
  const line = button.inline === "line";
  const bh = line ? Math.round(fs * NGUI.LINE_PITCH * LINE_BUTTON.height) + XS_BUTTON.boxInsetH : Math.round(fs * INLINE_BUTTON.height);
  const cap = line ? Math.max(LINE_BUTTON.fsMin, Math.round(fs * LINE_BUTTON.share))
    : Math.min(Math.round(fs * INLINE_BUTTON.share), bh - XS_BUTTON.boxInsetH);
  // one line: as high as the text beside it, a two-word caption (Go to) never folds
  const ib = fitText(button.label, cap, Math.min(cap, line ? LINE_BUTTON.fsMin : INLINE_BUTTON.fsMin),
    XS_BUTTON.w - XS_BUTTON.fitInset, 1);
  const ibw = glyphsOf(ib.text, ib.fs) + (line ? LINE_BUTTON.pad : INLINE_BUTTON.pad);
  return { bf: ib, bw: ibw, bh };
}

function messageLines(lines, w, fitTogether, plain, assets = true) {
  const rows = lines.map((l) => (typeof l === "string" ? { text: l } : l));
  const boxed = !plain && rows.some((r) => r.button);
  // paragraphs at POPUP_TYPE.text (BL-176: one size under the 52 down to 44 they had), light stats rows their own range
  // (38 down to 26); the width selection allows six lines
  const min = boxed ? STATS_BOX.value.fsMin : POPUP_TYPE.text.min, max = boxed ? STATS_BOX.value.fs : POPUP_TYPE.text.fs, most = 6;
  const fitted = rows.map((r) => {
    if (r.rule) return { r, room: w - 2 * SPACE.xxxl, f: counted({ text: "", fs: min }) };
    // a line button is the small button: 136 high, as wide as its caption needs and 90 more (240 at least), the caption 48 down to 34
    const bf = r.button ? fitText(r.button.label, XS_BUTTON.fs, XS_BUTTON.fsMin, XS_BUTTON.w - XS_BUTTON.fitInset) : null;
    const bw = bf ? Math.max(XS_BUTTON.minW, glyphsOf(bf.text, bf.fs) + XS_BUTTON.glyphPad) : 0;
    const room = boxed ? valueRoom("frame", w, { reserve: bw ? bw + SPACE.m : 0 }) : w - 2 * SPACE.xxxl - (bw ? bw + SPACE.m : 0) - (plain ? r.indent || 0 : 0);
    const f = messageText(r.text, r.fontSize ?? max, r.minFontSize ?? min, room, most, boxed);
    if (plain && r.button?.inline) {
      // no higher than its line's text
      const inline = inlineButton(r, f.fs, w);
      return { r, ...inline, f: counted(messageText(r.text, f.fs, f.fs, inline.room, most, boxed)) };
    }
    return { r, bf, bw, room, f: counted(f) };
  });
  const group = fitTogether.map((i) => fitted[i]).filter(Boolean);
  if (group.length) {
    const fs = Math.max(min, Math.min(...group.map(({ f }) => f.fs)));
    // Rewrap at the shared size; extra lines keep the popup's minimum instead of shrinking below it. An inline button
    // follows its line's new size.
    for (const s of group) {
      if (s.bh) Object.assign(s, inlineButton(s.r, fs, w));
      s.f = boxed ? counted(wrapPath(s.r.text, fs, fs, s.room, Infinity)) : wrapPath(s.r.text, fs, fs, s.room, Infinity);
    }
  }
  // Text is already wrapped and fitted. textItem measures that fit without repeating the line-break search.
  for (const s of fitted) s.item = s.r.rule ? { w: s.room, h: RULE.h } : textItem({ ...NGUI, fitText: () => s.f }, s.f.text,
    { fs: s.f.fs, min: s.f.fs }, { maxW: s.room, lines: Infinity });
  const buttons = [];
  if (boxed) {
    // rows on the game's light stats box: text at the left, the line's button at the right; a row with a button holds it, in 156
    // at least (the button and 10 above and below)
    const box = statsBox({ holder: "frame", width: w, name: "Box", names: { value: "Row", divider: "Dot" }, rows: fitted.map(({ r, f, bf, bw }, i) => ({
      value: f, colour: `${r.color || "#2d4664"}/1.00`, minH: bf ? MSG.rowButton : STATS_BOX.rowMin, reserve: bw ? bw + SPACE.m : 0,
      slot: bf ? ({ right, y }) => {
        buttons.push([i, r.button]);
        const buttonRow = row([{ w: bw, h: XS_BUTTON.h }], { x: right - bw, w: bw, gap: SPACE.m, justify: "center" });
        return [(assets ? Buttons.smallButton : placeholderButton)(`Btn_${i}`, buttonRow.boxes[0].x + bw / 2, y, r.button.label, { w: bw, fs: XS_BUTTON.fs, min: XS_BUTTON.fsMin, depth: 8 })];
      } : undefined,
    })) });
    return { tree: node("Lines", [0, 0], [], box.nodes), height: box.height, buttons, fitted };
  }
  const out = [], textBox = container({ w, h: 0, padding: [0, SPACE.xxxl] }).inner;
  const sized = fitted.map(s => {
    if (s.r.rule) {
      const lineRow = row([{ w: textBox.w, h: RULE.h }], { x: textBox.x, w: textBox.w, justify: "start" });
      return { ...s, lineRow, w: textBox.w, h: lineRow.h };
    }
    const indent = plain ? s.r.indent || 0 : 0;
    // an inline button follows its text; another sits at the line's right end
    const inline = !!s.bh;
    const lineRow = row([{ ...s.item, w: inline ? s.item.w : s.room }, ...(s.bf ? [{ w: s.bw, h: s.bh ?? XS_BUTTON.h }] : []),
      ...(s.more || []).map((m) => ({ w: m.bw, h: s.bh }))],
      { x: textBox.x + indent, w: textBox.w - indent, gap: SPACE.m, justify: inline ? "start" : "center" });
    return { ...s, lineRow, w: textBox.w, h: lineRow.h + (plain ? s.r.gap || 0 : 0) };
  });
  const paragraphs = layoutStack(sized, { x: textBox.x, w: textBox.w, gap: SPACE.l });
  paragraphs.boxes.forEach(({ r, f, bf, bw, bh, more = [], room, y, lineRow: measured }, i) => {
    const lineRow = { ...measured, boxes: measured.boxes.map(b => ({ ...b, y: b.y + y + (plain ? r.gap || 0 : 0) })) };
    const local = { w, h: 0, cx: 0, cy: 0 }, colour = `${r.color || "#2d4664"}/1.00`, box = lineRow.boxes[0];
    if (r.rule) {
      out.push(node(`Rule_${i}`, toNode(box, 3, local), [sprite("Common_Line_Dot", box.w, RULE.h,
        { type: 2, depth: 7, color: `${STATS_BOX.divider}/1.00`, pivot: 3 })]));
      return;
    }
    out.push(node(`Line_${i}`, toNode(box, plain ? 3 : 4, local),
      [label(f.text, f.fs, colour, room, box.h, { pivot: plain ? 3 : 4, depth: 8, ...(plain && r.italic ? { italic: true } : {}) })]));
    if (plain && r.marker) {
      const marker = { x: box.x - (r.markerGap ?? f.fs), y: box.y, w: r.markerGap ?? f.fs, h: f.fs * NGUI.LINE_PITCH };
      out.push(node(`Mark_${i}`, toNode(marker, 3, local), [label(r.marker, f.fs, colour, marker.w, marker.h, { pivot: 3, depth: 8 })]));
    }
    if (bf) {
      const [bx, by] = toNode(lineRow.boxes[1], 4, local);
      out.push((assets ? Buttons.smallButton : placeholderButton)(`Btn_${i}`, bx, by, r.button.label, bh
        ? { w: bw, h: bh, fs: bf.fs, min: bf.fs, depth: 8 } : { w: bw, fs: XS_BUTTON.fs, min: XS_BUTTON.fsMin, depth: 8 }));
      buttons.push([i, r.button]);
    }
    more.forEach((m, k) => {
      const [mx, my] = toNode(lineRow.boxes[2 + k], 4, local);
      out.push((assets ? Buttons.smallButton : placeholderButton)(`Btn_${i}_${k + 1}`, mx, my, m.button.label,
        { w: m.bw, h: bh, fs: m.bf.fs, min: m.bf.fs, depth: 8 }));
      buttons.push([`${i}_${k + 1}`, m.button]);
    });
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
  // as high as the lines and the footer need (BL-176: no taller minimum frame, so no padding round a short message)
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

async function showMessage({ title = "", lines = [], ok = null, cancel = null, buttons = null, check = null, host: hostArg = "dialog", width, close: dismissable = true, confirmKeys = null, fitTogether = [], scroll = false, plain = false, captionSite = null, footer = null }, resolve) {
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
    plainMessage(layer, { title, lines, ok, cancel, buttons, state, dismissable, scroll, plain, width, fitTogether, captionSite, footer }, finish);
    layer.focus?.({ preventScroll: true });
    return;
  }
  try {
    const { body, width: w, height, visible, y0, content, checkBox, scroll: scrolls } = messageLayout({ lines, width, ok, cancel, buttons, check: state, fitTogether, scroll, plain });
    const t = popupFrame(title, w, height, { ok, cancel, buttons, close: dismissable, captionSite, footer });
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
