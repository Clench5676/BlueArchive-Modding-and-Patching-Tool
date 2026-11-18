// Shared popups on the game's popup frame (UIPopup_Sort): the frame (popupFrame) with its backdrop, title, close
// button and Cancel / OK; the list popup, laid out like the game's quick formation edit (a card's details on the
// left, a framed list with the student list's tools on the right, one card chosen or several ticked); and the
// message popup (title, lines, an optional checkbox, a footer group; it resolves when it closes).
// Popups draw into a host: the detail screen's own (#popup, #picker) or the stage-level ones (#prompt for lists,
// #dialog for messages), so they show on any screen. Every open popup is on one stack and Escape goes to the
// topmost only, as the game's PopupClose closes the popup in front; a popup that must be answered with a button
// takes Escape and does nothing with it.
import * as NGUI from "./ngui.js";
import { installScrollFades } from "./scroll-fade.js";
import { scrollbar, scrollbarTrack } from "./scrollbar.js";
import { horizontalScroll } from "./horizontal-scroll.js";
import * as Buttons from "./game-buttons.js";
import { resizeButton } from "./button-layout.js";
import { fitBadgeCaptions, SELECTION_PREFAB, SELECTION_SLOT } from "./selected-frames.js";
import { frameOutline } from "./row-thumbnail.js";
import { T } from "../core/i18n.js";
import { withTimeout } from "../core/async.js";
import { frameGeometry } from "./popup-frames.js";
import { BACKDROP, POPUP_FRAME, POPUP_TYPE, SPACE, TYPE } from "./ui-tokens.js";
import { RECRUIT, S, clone, glyphsOf, hitButton } from "../core/util.js";

import { container, textItem, toNode } from "./ui-grid.js";
import { footerY, footerLayout, footerLabels } from "./popup-footer.js";
// The popups split into the modules below stay reachable as Popup.<name>.
export {
  BUTTON_TEXT_H,
  FRAME_Y,
  footerBottom,
  footerY,
  footerTop,
  footerScale,
  footerGap,
  footerSlots,
  FOOTER,
  FOOTER_DEFAULT,
  footerWidth,
  footerLayout,
  footerFit,
  footerLabels,
} from "./popup-footer.js";
export { checkbox, wireCheck } from "./popup-checkbox.js";
export {
  LIST_CARD_NAME,
  listLayout,
  LIST_TOOLBAR,
  LIST_BAND,
  listCardBox,
  TOGGLE_ICON,
  checkedToolbarFit,
  LIST_FOOTER,
  listFrame,
  TICK_BOX,
  BADGE_GAP,
  badgeRoom,
  tickPlace,
  listCard,
  listPopup,
} from "./popup-list.js";
export { DETAILS, chipFit, chipPlaces, moreButton, detailsTree } from "./popup-details.js";
export {
  MSG,
  message,
  confirmKey,
  plainMessage,
  RULE,
  INLINE_BUTTON,
  LINE_BUTTON,
  messageLayout,
  textViewer,
  REFUSALS,
  isRefusal,
  notice,
  deferWhile,
  notify,
} from "./popup-message.js";

export const { node, sprite, label, find, rect } = NGUI;
export const fitText = (...a) => NGUI.fitText(...a);
// The gap between the tags and marks of a list card.
export const TAG_GAP = 8;
/**
 * Lay out a list card's bottom row of tags, left to right: its tag, then its marks (Updatable), on one line,
 * TAG_GAP apart, from the left corner or after the recruit icon, never under the pin (a favourite) or past the
 * card's right edge.
 * One tag keeps its badge width (its word, at least 116); several share one size, the largest at which the row
 * fits (26 down to 18, the inset narrowing last). A row that fits at none leaves out the tag (the marks tell
 * what needs doing; the card's name still says Optimized), then whatever does not fit.
 * @returns {{mark: object, x: number, bf: {text: string, fs: number}, bw: number}[]} Each placed tag: its mark,
 *     its left edge, its fitted text and its badge width.
 */
export function tagRow(it, w) {
  const start = it?.recruit ? -w / 2 + 34 + 31 / RECRUIT.ratio + TAG_GAP : -w / 2 + 12;
  const end = it?.fav ? w / 2 - 38 - 27 - TAG_GAP : w / 2 - 12;
  const room = end - start;
  const span = (row) => row.reduce((n, t) => n + t.bw, 0) + TAG_GAP * Math.max(0, row.length - 1);
  const fit = (marks) => {
    if (marks.length === 1) {
      // the badge's own form where it fits, else the row's steps below
      const bf = fitText(marks[0].text, 26, 20, Math.min(w - 70, room - 24), 1);
      const row = [{ mark: marks[0], bf, bw: Math.max(Math.min(116, room), glyphsOf(bf.text, bf.fs) + 24) }];
      if (span(row) <= room + 1e-9) return row;
    }
    for (const inset of [24, 16, 12])
      for (let fs = 26; fs >= 18; fs--) {
        const row = marks.map((mark) => {
          const bf = fitText(mark.text, fs, fs, room, 1);
          return { mark, bf, bw: glyphsOf(bf.text, bf.fs) + inset };
        });
        if (span(row) <= room + 1e-9) return row;
      }
    return null;
  };
  const marks = [it?.tag, ...(it?.marks || [])].filter(Boolean);
  let row = marks.length ? fit(marks) || (it?.tag && it?.marks?.length ? fit(it.marks) : null) : [];
  for (let n = marks.length - 1; !row && n > 0; n--) row = fit(marks.slice(0, n));
  let x = start;
  return (row || []).map((t) => {
    const placed = { ...t, x };
    x += t.bw + TAG_GAP;
    return placed;
  });
}

export const hostOf = (h) => (typeof h === "string" ? document.getElementById(h) : h);

// --- the prefabs, read once: the frame (required), the student list's tools and the checkbox (each optional: a
// popup without them still works) ---
export let layouts = null,
  loading = null,
  frameLoading = null;
export let selectionLoading = null,
  selectionSlot = null,
  selectionSprite = null,
  selectionOutline = null;
/**
 * Load what the Applied and Selected frames of a card are drawn from (the starting-skill selection slot).
 * @returns {Promise<void>} Settles when the prefab and the frame's outline are in.
 */
export function selectionReady() {
  if (selectionLoading) return selectionLoading;
  const pending = Promise.all([NGUI.layout(SELECTION_PREFAB), NGUI.init()])
    .then(async ([tree]) => {
      const slot = find(tree, SELECTION_SLOT);
      if (!slot) throw new Error("Starting-skill selection slot unavailable");
      const frame = find(slot, "SelectLine").widgets.find((w) => "sprite" in w);
      const outline = frameOutline(await NGUI.spritePixels(frame));
      if (selectionLoading !== pending) throw new DOMException("Resource context changed", "AbortError");
      selectionSprite = NGUI.atlasIndex()[frame.atlas || "Common"][frame.sprite];
      selectionOutline = outline;
      selectionSlot = slot;
    })
    .catch((error) => {
      if (selectionLoading === pending) selectionLoading = null;
      throw error;
    });
  return (selectionLoading = pending);
}
/**
 * The slot's number label holds Applied (yellow) or Selected (blue), fitted together to keep one size across
 * source cards.
 * @param {{w: number, h: number}|null} [card]
 * @param {object|null} [slot] The selection slot node; the loaded one by default.
 * @returns {{applied: object, preview: object}|null} The fitted captions, or null before the slot is loaded.
 */
export function selectionCaptions(card = null, slot = selectionSlot) {
  const captions =
    slot && fitBadgeCaptions(slot, [T("status.applied"), T("status.selected")], NGUI.fitGroup, NGUI.glyphWidth, card);
  return captions && { applied: captions[0], preview: captions[1] };
}
/**
 * Load the popup frame prefab, which every popup needs.
 * @returns {Promise<void>} Settles when the frame is in; a failed load is tried again on the next call.
 */
export function frameReady() {
  return (frameLoading ||= withTimeout(NGUI.layout("UIPopup_Sort"), 15000, "Popup frame")
    .then((sortPopup) => {
      layouts = { ...layouts, sortPopup };
    })
    .catch((err) => {
      frameLoading = null;
      throw err;
    }));
}
const optional = (name) =>
  NGUI.layout(name).catch((err) => {
    console.warn("popup layout", name, err);
    return null;
  });
/**
 * Load everything popups draw from: the frame (required), the student list's tools and the checkbox (each
 * optional: a popup without them still works). Also starts the scroll fades, once, from the first popup on.
 * @returns {Promise<void>} Settles when the prefabs are in.
 */
export function ready() {
  // every scrolling area fades at an edge with more past it (scroll-fade.js); once, from the first popup on
  installScrollFades();
  loading ||= Promise.all([
    frameReady(),
    optional("UICharacterCollection"),
    optional("UIPopup_GachaCharList.Btn_Checkbox"),
    Buttons.ready(),
  ]).then(
    ([, collection, checkbox]) => {
      layouts = { ...layouts, collection, checkbox };
    },
    (err) => {
      loading = null;
      throw err;
    },
  );
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
/**
 * The elements Tab can stop at inside a box, in order: enabled, drawn and not inert; a hidden or inert part is
 * skipped whole.
 * @returns {HTMLElement[]}
 */
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
  const box = el || host,
    active = doc()?.activeElement;
  if (active && active !== doc().body && !box.contains?.(active)) return; // the user moved on
  const above = stack.findLast(live); // another popup still open: only into it
  if (above && !(above.el || above.host).contains?.(before)) return;
  before.focus?.({ preventScroll: true });
}
/**
 * Keep Tab inside the topmost popup: it wraps at the ends and brings the focus back in when it is outside (on
 * the opener, or lost when a redraw replaced the focused control). A control that took the key itself keeps it
 * (defaultPrevented).
 */
export function trapKey(ev) {
  if (
    ev.key !== "Tab" ||
    ev.defaultPrevented ||
    ev.isComposing ||
    ev.keyCode === 229 ||
    ev.altKey ||
    ev.ctrlKey ||
    ev.metaKey
  )
    return;
  const t = top();
  if (!t) return;
  const box = t.el || t.host,
    stops = tabStops(box),
    active = doc()?.activeElement;
  if (!stops.length) {
    ev.preventDefault();
    focusIn(box);
    return;
  }
  const at = stops.indexOf(active),
    last = stops.length - 1;
  if (at >= 0 && (ev.shiftKey ? at > 0 : at < last)) return; // inside: the browser steps to the next control
  ev.preventDefault();
  (ev.shiftKey ? stops[last] : stops[0]).focus?.();
}
addEventListener("keydown", trapKey);

/**
 * An open popup on the stack.
 * @typedef {object} PopupEntry
 * @property {HTMLElement} host The element the popup is drawn into.
 * @property {HTMLElement|null} el The popup's own layer, when the host holds several popups.
 * @property {((how: string) => void)|null} close Closes the popup; null for a popup that must be answered with one of
 *     its buttons.
 * @property {HTMLElement|null} before What had the focus when the popup opened, to give it back.
 */

/**
 * Put a popup on the stack, or keep its place when it is drawn again.
 * @param {HTMLElement} host The element the popup is drawn into.
 * @param {((how: "escape"|"backdrop"|"close") => void)|null} close Closes the popup; null for a popup that must
 *     be answered with one of its buttons.
 * @param {{el?: HTMLElement|null, loading?: boolean}} [options] el: the popup's own layer, when the host holds
 *     several; loading: keep the layer hidden and inert until reveal().
 * @returns {PopupEntry}
 */
export function opened(host, close, { el = null, loading = false } = {}) {
  if (loading && el) {
    el.style.visibility = "hidden";
    el.inert = true;
  }
  prune();
  const at = stack.findIndex((e) => e.host === host && e.el === el);
  // a popup drawn again keeps its place and what to give the focus back to; a new one takes the focus in
  const entry = { host, el, close, before: at >= 0 ? stack[at].before : opener(el || host) };
  if (at >= 0) stack[at] = entry;
  else {
    stack.push(entry);
    if (!loading) focusIn(el || host);
  }
  return entry;
}
/**
 * Reveal a popup layer once all its work and decoded pictures are ready. A closed layer stays closed.
 * @param {Promise<unknown>[]} [pending]
 * @returns {Promise<boolean>} False when the layer was closed meanwhile.
 */
export async function reveal(layer, pending = []) {
  await Promise.all(pending);
  await NGUI.decoded([layer, ...(layer.querySelectorAll?.("*") || [])], Infinity);
  if (!layer.isConnected) return false;
  layer.style.visibility = "";
  layer.inert = false;
  if (top()?.el === layer) focusIn(layer);
  return true;
}
/**
 * Take a popup off the stack, giving the focus back.
 * @param {HTMLElement|null} [el] The popup's own layer.
 */
export function closed(host, el = null) {
  const at = stack.findIndex((e) => e.host === host && e.el === el);
  if (at >= 0) release(stack.splice(at, 1)[0]);
}
/**
 * The topmost open popup.
 * @returns {PopupEntry|null} The entry, or null when none is open.
 */
export function top() {
  prune();
  return stack.at(-1) || null;
}
/**
 * Whether any popup is open: on the stack, or in a host of the other popups (the consent and update popups, the
 * settings, the display settings).
 */
export const anyOpen = () =>
  !!top() || !!document.querySelector(".popup-host:not([hidden]), .consent-host:not([hidden])");
/**
 * Whether an input event may go on while holds are on: they leave every open popup usable, including stack
 * entries with their own hosts and the consent layers. Escape belongs to the popup even when focus is still on
 * the control that opened it.
 * @returns {boolean} True when the event belongs to an open popup.
 */
export function allowsInput(ev) {
  prune();
  return (
    (ev.key === "Escape" && anyOpen()) ||
    stack.some((e) => (e.el || e.host).contains(ev.target)) ||
    !!ev.target.closest?.(".popup-host:not([hidden]), .consent-host:not([hidden])")
  );
}
/**
 * Wait until no popup is open and the caller's work has finished (late prompts and update offers).
 * @param {number} [every] The polling interval in milliseconds.
 * @param {() => boolean} [blocked] True while the caller's work is not finished.
 * @returns {Promise<void>} Settles when the screen is idle.
 */
export function whenIdle(every = 200, blocked = () => false) {
  return new Promise((resolve) => {
    const check = () => (anyOpen() || blocked() ? setTimeout(check, every) : resolve());
    check();
  });
}

/**
 * Close the topmost popup on Escape, not while an input method composes (keyCode 229 is the key that ends a
 * conversion in WebKit), nor when a control in the popup took the key itself.
 */
export function onKey(ev) {
  if (ev.key !== "Escape" || ev.defaultPrevented || ev.isComposing || ev.keyCode === 229) return;
  const t = top();
  if (!t) return;
  ev.preventDefault();
  t.close?.("escape");
}
addEventListener("keydown", onKey);

// --- the start sequence: popups one at a time, in order ---
// Each step resolves once its popup has closed, at once when it has nothing to show; one that fails is logged
// and the next one comes.
/**
 * Run the start popups one at a time, in order.
 * @param {[string, () => Promise<unknown>][]} steps The steps: a name for the log and a function that resolves
 *     once its popup has closed.
 * @returns {Promise<void>} Settles when every step has run.
 */
export async function sequence(steps) {
  for (const [name, run] of steps) {
    try {
      await run();
    } catch (err) {
      console.error("start popup", name, err);
    }
  }
}

// --- the selection of a list popup with several cards ticked: every card that can be ticked is, to start with ---
/**
 * The selection of a list popup with several cards ticked, to start with: every card that can be ticked.
 * @param {{key: string, disabled?: boolean}[]} items
 * @returns {Set<string>}
 */
export const selectable = (items) => new Set(items.filter((it) => !it.disabled).map((it) => it.key));
/**
 * The selection with a key ticked or unticked; a card that cannot be ticked leaves it as it is.
 * @param {Set<string>} selection
 * @param {{key: string, disabled?: boolean}[]} items
 * @returns {Set<string>}
 */
export function toggled(selection, items, key) {
  const it = items.find((x) => x.key === key);
  const next = new Set(selection);
  if (!it || it.disabled) return next;
  if (next.has(key)) next.delete(key);
  else next.add(key);
  return next;
}
/**
 * The sizes of the ticked cards, added up.
 * @param {{key: string, size?: number}[]} items
 * @param {Set<string>} selection
 * @returns {number} The total in bytes.
 */
export const sizeOf = (items, selection) => items.reduce((n, it) => n + (selection.has(it.key) ? it.size || 0 : 0), 0);

// --- hit areas ---
const PIVX = [0, 0.5, 1, 0, 0.5, 1, 0, 0.5, 1],
  PIVY = [0, 0, 0, 0.5, 0.5, 0.5, 1, 1, 1];
/**
 * A rendered node's stage rectangle: its widget's element, or, for a sprite the atlases lack, worked out from
 * the node, so its button still works.
 * @param {RenderView} view What render() drew.
 * @returns {number[]|null} [left, top, width, height] in stage pixels; null when the node is not in the view.
 */
export function nodeRect(view, path) {
  const e = view.nodes.get(path);
  if (!e) return null;
  if (e.els[0]) return rect(e.els[0]);
  const w = e.node.widgets.find((x) => "w_" in x);
  if (!w) return null;
  const ww = w.w_ * view.scale,
    hh = w.h_ * view.scale,
    p = w.pivot ?? 4;
  return [e.stage[0] - PIVX[p] * ww, e.stage[1] - PIVY[p] * hh, ww, hh];
}
/**
 * Put an invisible button over a rendered node.
 * @param {RenderView} view What render() drew.
 * @param {{name?: string, action?: string, z?: number}} [options] name: the accessible name (the caption it
 *     covers); action: what it does (data-action, for checks); z: the z-index.
 * @returns {HTMLButtonElement|null} The button, or null when the node is not in the view.
 */
export function hit(host, view, path, fn, { name = "", action = "", z = 80 } = {}) {
  const r = nodeRect(view, path);
  if (!r) return null;
  const b = hitButton(host, r, fn, name, z);
  if (action) b.dataset.action = action;
  return b;
}

// --- the frame ---
// the height of a title's line at the game's size (TYPE.display, one line): where a popup's smaller title is centred
export const titleHeight = (title) => textItem(NGUI, title, TYPE.display, { lines: 1 }).h;
/**
 * Options of popupFrame().
 * @typedef {object} PopupFrameOptions
 * @property {string|null} [ok] The OK button's label.
 * @property {string|null} [cancel] The Cancel button's label.
 * @property {boolean} [close] False for a popup that must be answered with a button (no close button).
 * @property {{label: string, primary?: boolean, name?: string, semantic?: string, tone?: string}[]|null} [buttons]
 *     Named choices in a row instead of ok / cancel: one centred, two as OK and Cancel, three or more sharing the
 *     available width. A node is Choice_<index> unless it names itself; tone "bright" gives a secondary button the
 *     Settings small button's bright blue (BRIGHT_BUTTON).
 * @property {string|null} [captionSite] The wording site, which makes a caption that cannot fit throw.
 * @property {number|null} [titleCentre] The title's ink centre this far under the frame's top, in units, in place of
 *     the shared title top (the header band's centre puts the title where UIPopup_Sort has it).
 * @property {string|null} [footer] The buttons' size, "XS", "S", "M" or "L" (FOOTER); without it the default size,
 *     XS, its bodies as wide as the captions need.
 * @property {"center"|"right"} [align] "right" for a footer at the frame's bottom right, its last body's right edge
 *     SPACE.xl in from the frame's (the panels' edge; the list popups and the Student Hall's background picker).
 */

/**
 * Build the game's popup frame at a size: backdrop, title, close button and, when given, its Cancel and OK
 * buttons with these labels on centred bodies of the footer's size, with the shared gap and bottom inset.
 * @param {number} w The frame's width in units; a class name's width or the width the content needs.
 * @param {number} h The frame's height in units.
 * @param {PopupFrameOptions} [options]
 * @returns {NguiNode} The popup's node tree.
 */
export function popupFrame(
  title,
  w,
  h,
  {
    ok = null,
    cancel = null,
    close = true,
    buttons = null,
    captionSite = null,
    titleCentre = null,
    footer = null,
    align = "center",
  } = {},
) {
  ({ w, h } = frameGeometry(w, h));
  const t = clone(find(layouts.sortPopup, "Center"));
  const popup = find(t, "Popup");
  popup.children = popup.children.filter((c) => ["Base", "Btn"].includes(c.name));
  const bg = find(popup, "Base/BG"),
    local = { w, h, cx: 0, cy: 0 };
  Object.assign(bg.widgets[0], { w_: w, h_: h });
  // The corner texture keeps its own prefab inset and dimensions.
  find(bg, "Texture_Top").pos = [-w / 2 + 248, h / 2 - 66];
  const titleNode = find(bg, "Label_Title_Locale");
  const titleBox = container({ w, h, padding: [POPUP_FRAME.titleTop, SPACE.xxxl, 0, SPACE.xxxl] }).inner;
  // the title one size under the game's (POPUP_TYPE), centred where the game's 72 would stand
  const tf = textItem(NGUI, title, POPUP_TYPE.title, { maxW: titleBox.w, lines: 1 }),
    lift = (titleHeight(title) - tf.h) / 2;
  titleNode.pos = toNode(
    { ...titleBox, y: titleCentre == null ? titleBox.y + lift : titleCentre - (tf.mid ?? tf.h / 2), h: tf.h },
    4,
    local,
  );
  Object.assign(titleNode.widgets[0], { text: tf.text, fontSize: tf.fs, w_: titleBox.w, h_: tf.h, pivot: 4 });
  find(titleNode, "Line").widgets[0].w_ = glyphsOf(tf.text, tf.fs) + SPACE.s;
  const closeBtn = find(bg, "Btn_Close"),
    size = POPUP_FRAME.close.size;
  closeBtn.pos = toNode(
    { x: w - POPUP_FRAME.close.right - size, y: POPUP_FRAME.close.top, w: size, h: size },
    4,
    local,
  );
  Object.assign(closeBtn.widgets[0], { w_: size, h_: size, pivot: 4 });
  closeBtn.active = close;
  const btn = find(popup, "Btn"),
    primary = clone(find(btn, "Btn_Ok")),
    secondary = clone(find(btn, "Btn_Cancel"));
  const actions = buttons?.length
    ? buttons
    : [
        ...(cancel ? [{ label: cancel, name: "Btn_Cancel" }] : []),
        ...(ok ? [{ label: ok, name: "Btn_Ok", primary: true }] : []),
      ];
  // the default footer's bodies follow their captions (footerWidth); a size a call site names keeps its own width
  const {
    kind: footerKind,
    control,
    xs: centred,
    bodyW,
    room,
  } = footerLayout(
    actions.map((action) => action.label),
    w,
    footer,
  );
  const shift = align === "right" && centred.length ? w / 2 - SPACE.xl - (centred.at(-1) + bodyW / 2) : 0;
  const xs = centred.map((x) => x + shift);
  btn.active = !!actions.length;
  btn.pos = [0, footerY(h, control)];
  // Keep both named branches for callers that overlay a disabled confirm button.
  btn.children = actions.length
    ? actions.map((action, index) => {
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
      })
    : [];
  footerLabels(
    btn.children.map((child) => find(child, "Label_Locale").widgets[0]),
    actions.map((action) => action.label),
    { site: captionSite, width: room, caption: footerKind.caption },
  );
  if (!buttons?.length)
    for (const [name, source] of [
      ["Btn_Ok", primary],
      ["Btn_Cancel", secondary],
    ]) {
      if (!find(btn, name)) {
        source.active = false;
        btn.children.push(source);
      }
    }
  find(t, "BlackBg/BlackBgClickArea").widgets.find((x) => "sprite" in x).color = BACKDROP;
  return t;
}

// The Settings small button's bright blue (UIPopup_Option_PC/.../ETCSetting/ButtonBg: Common_Btn_BG #8de4ff, its
// Common_Btn_Normal_B_S_Pt patterns, caption #314665), on a footer's secondary body (the list popups'
// Cancel)
export const BRIGHT_BUTTON = Object.freeze({
  body: "#8de4ff/1.00",
  pattern: "Common_Btn_Normal_B_S_Pt",
  ink: "#314665/1.00",
});
function brighten(button) {
  const body = find(button, "BG (1)"),
    bg = body.widgets.find((x) => "sprite" in x);
  bg.color = BRIGHT_BUTTON.body;
  for (const child of body.children || [])
    for (const x of child.widgets || []) if (/_Pt$/.test(x.sprite || "")) x.sprite = BRIGHT_BUTTON.pattern;
  find(button, "Label_Locale").widgets[0].color = BRIGHT_BUTTON.ink;
  button.actionSemantic = "cancel";
}
/**
 * Draw popupFrame's confirm button as the game's disabled one, alone at the frame's place: a popup that can
 * block its confirm lays this over the live one while it is blocked (the list popup draws its blocked primary
 * button the same way).
 * @returns {NguiNode} The tree with only the disabled confirm button.
 */
export function disabledConfirm(frame) {
  const t = clone(frame),
    popup = find(t, "Popup"),
    btn = find(popup, "Btn");
  btn.children = [Buttons.disable(find(btn, "Btn_Ok"))];
  popup.children = [btn];
  t.children = t.children.filter((c) => c.name === "Popup");
  return t;
}

/**
 * Close a popup on a click on its backdrop or on Escape, as the game's PopupClose does; its body takes the
 * clicks that miss its controls. Puts the popup on the stack.
 * @param {RenderView} view What render() drew.
 * @param {((how: string) => void)|null} close Closes the popup; null for a popup that must be answered with a
 *     button (the backdrop and Escape do nothing).
 * @param {{el?: HTMLElement|null}} [options] el: the popup's own layer, when the host holds several.
 * @returns {PopupEntry}
 */
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

/**
 * Add a scrolling card area with the game list's scrollbar colours.
 * @param {number[]} rect [left, top, width, height] of the area in stage pixels.
 * @param {{horizontal?: boolean, contentW?: number}} [options] horizontal: scroll along x, over contentW.
 * @returns {HTMLElement} The inner element to draw the content into; its `ready` promise settles when the
 *     scrollbar is in.
 */
export function scrollArea(host, [l, t, w, h], contentH, { horizontal = false, contentW = w } = {}) {
  const area = document.createElement("div");
  area.className = "popup-cards";
  const viewport = container({ x: l, y: t, w, h }).inner;
  Object.assign(area.style, {
    left: `${viewport.x}px`,
    top: `${viewport.y}px`,
    width: `${viewport.w}px`,
    height: `${viewport.h}px`,
  });
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
    const pending = scrollbar(host, track, { horizontal })
      .then((bar) => {
        const sync = () =>
          horizontal ? bar.sync(w, contentW, area.scrollLeft || 0) : bar.sync(h, contentH, area.scrollTop || 0);
        area.addEventListener("scroll", sync);
        sync();
      })
      .catch((err) => console.warn("popup scrollbar unavailable", err));
    inner.ready = pending;
  }
  return inner;
}

// Prefabs and derived UI values belong to the selected installation.
NGUI.onResourceChange?.(() => {
  layouts = loading = frameLoading = selectionLoading = selectionSlot = selectionSprite = selectionOutline = null;
});
