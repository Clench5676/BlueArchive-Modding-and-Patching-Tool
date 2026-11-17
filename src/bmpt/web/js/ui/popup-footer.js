// The popup frame's numbers and its footer: one set of sizes, places and caption fits for every popup frame
// (UIPopup_Sort/Center/Popup/Btn), shared by the frame (popup.js), the list popup and the message popup.
import * as NGUI from "./ngui.js";
import { CONTROL, POPUP_FOOTER, POPUP_FRAME, SPACE, TYPE, SMALL_BUTTON, captionMinimum } from "./ui-tokens.js";
import { row, toNode } from "./ui-grid.js";

// --- the frame ---
// The Sort branches are resized to the shared buttonL control. Captions keep two display lines inside its padding.
export const BUTTON_TEXT_H = CONTROL.buttonL.h - SPACE.m;
export const FRAME_Y = POPUP_FRAME.frameY;

// --- the footer: one set of numbers for every popup frame (UIPopup_Sort/Center/Popup/Btn) ---
/**
 * How far a footer's bodies stand above the frame's bottom: the prefab's 102 under its buttonL bodies, scaled
 * to the bodies' height (60 for the XS footer every popup has, POPUP_FOOTER.bottom).
 * @param {{h: number}} [control] The footer's button control.
 * @returns {number} The distance in units.
 */
export const footerBottom = (control = POPUP_FOOTER.control) =>
  Math.round((POPUP_FRAME.footer.bottom * control.h) / CONTROL.buttonL.h);
/**
 * The y of a footer's centre in a popup frame.
 * @param {{h: number}} [control] The footer's button control.
 * @returns {number} The y in NGUI units.
 */
export const footerY = (h, control = POPUP_FOOTER.control) => FRAME_Y - h / 2 + footerBottom(control) + control.h / 2;
/**
 * The y of the top of the default footer's bodies in a popup frame.
 * @param {number} [n] The number of buttons; unused.
 * @returns {number} The y in NGUI units.
 */
export const footerTop = (h, n = 1) => footerY(h) + POPUP_FOOTER.control.h / 2;
/**
 * How far a footer row is scaled down to fit a width: not at all for fewer than three buttons.
 * @param {{minW: number}} [control] The footer's button control.
 * @returns {number} The scale, at most 1.
 */
export function footerScale(n, w = Infinity, control = footerSize().control) {
  return n < 3
    ? 1
    : Math.min(1, (w - 2 * POPUP_FRAME.footer.side - POPUP_FRAME.footer.gap * (n - 1)) / n / control.minW);
}
/**
 * The gap between a footer's buttons.
 * @param {number} n The number of buttons; unused.
 * @returns {number} The gap in units.
 */
export const footerGap = (n) => POPUP_FRAME.footer.gap;
/**
 * Place a footer row's buttons side by side, centred.
 * @param {{minW: number, h: number}} [control] The footer's button control.
 * @returns {{scale: number, xs: number[]}} The scale applied to the bodies and the x of each centre.
 */
export function footerSlots(n, w, control = footerSize().control) {
  if (!n) return { scale: 1, xs: [] };
  const scale = footerScale(n, w, control);
  w = Number.isFinite(w) ? w : n * control.minW * scale + (n - 1) * footerGap(n);
  const boxes = row(
    Array.from({ length: n }, () => ({ w: control.minW * scale, h: control.h })),
    { w, gap: footerGap(n), justify: "center" },
  ).boxes;
  return { scale, xs: boxes.map((b) => toNode(b, 4, { w, h: control.h, cx: 0, cy: 0 })[0]) };
}
const CAPTION_L = {
  fs: TYPE.display.fs,
  fsMin: TYPE.display.min,
  w: CONTROL.buttonL.minW - 2 * CONTROL.buttonL.pad,
  lines: 2,
  h: BUTTON_TEXT_H,
};
// The footer's sizes: "XS", the app's small button (CONTROL.buttonXS), every popup's footer (// "弹窗的按钮弄成 XS", every
// popup of the App); "L", the buttonL control with display captions (UIPopup_RaidEnterCheck's footer); "M", one size
// smaller (the buttonM control, its caption 60 down to TYPE.body's floor); "S", two sizes smaller (the Settings value
// row's small button, SMALL_BUTTON, 402 x 136, its caption 48 down to TYPE.body's floor). A popup's footer is "XS"
// unless its call site names another size.
const FOOTER_S = Object.freeze({ ...CONTROL.buttonS, minW: SMALL_BUTTON.w });
export const FOOTER = Object.freeze({
  L: Object.freeze({ control: CONTROL.buttonL, caption: CAPTION_L }),
  M: Object.freeze({
    control: CONTROL.buttonM,
    caption: Object.freeze({
      fs: CONTROL.buttonM.fs,
      fsMin: TYPE.body.min,
      w: CONTROL.buttonM.minW - 2 * CONTROL.buttonM.pad,
      lines: 2,
      h: CONTROL.buttonM.h - SPACE.m,
    }),
  }),
  S: Object.freeze({
    control: FOOTER_S,
    caption: Object.freeze({
      fs: FOOTER_S.fs,
      fsMin: TYPE.body.min,
      w: FOOTER_S.minW - 2 * FOOTER_S.pad,
      lines: 2,
      h: FOOTER_S.h - SPACE.m,
    }),
  }),
  XS: Object.freeze({
    control: CONTROL.buttonXS,
    caption: Object.freeze({
      fs: CONTROL.buttonXS.fs,
      fsMin: TYPE.badge.min,
      w: CONTROL.buttonXS.minW - 2 * CONTROL.buttonXS.pad,
      lines: 2,
      h: CONTROL.buttonXS.h - SPACE.s,
    }),
  }),
});
export const FOOTER_DEFAULT = "XS";
/**
 * A footer's body width in units: the control's own minimum, wider where the longest caption needs it (its
 * widest line, at the size the group fits the widest body, buttonL's, with the control's padding each side),
 * the same for every button of the row.
 * A tier is a height and a caption size; the width follows the captions, as the small button's does (glyphPad).
 * @param {string[]} captions
 * @param {{control: object, caption: object}} [kind] The footer size, FOOTER[...].
 * @returns {number} The width of every body.
 */
export function footerWidth(captions, kind = footerSize()) {
  const { control, caption } = kind,
    room = CONTROL.buttonL.minW - 2 * CONTROL.buttonL.pad;
  const labels = (captions || []).filter((text) => text);
  if (!labels.length) return control.minW;
  const fits = NGUI.fitGroup(labels, caption.fs, caption.fsMin, room, caption.lines, { maxH: caption.h });
  const need = Math.max(
    ...fits.map((fit) => Math.max(...fit.text.split("\n").map((line) => NGUI.glyphWidth(line, fit.fs)))),
  );
  return Math.min(CONTROL.buttonL.minW, Math.max(control.minW, Math.ceil(need + 2 * control.pad)));
}
/**
 * Lay out a footer row of these captions in a frame.
 * @param {string[]} labels
 * @param {string|null} [footer] The size a call site names, or null for the default's (the bodies follow the
 *     captions, footerWidth).
 * @returns {{kind: object, control: object, scale: number, xs: number[], bodyW: number, room: number}} The
 *     size, how far the row scales the bodies down, their x centres, their width and the room a caption has
 *     inside a body.
 */
export function footerLayout(labels, w, footer = null) {
  const kind = footerSize(footer),
    control = kind.control;
  const baseW = footer == null || footer === FOOTER_DEFAULT ? footerWidth(labels, kind) : control.minW;
  const { scale, xs } = footerSlots(labels.length, w, { ...control, minW: baseW }),
    bodyW = baseW * scale;
  return { kind, control, scale, xs, bodyW, room: bodyW - 2 * control.pad };
}
const CAPTION = FOOTER[FOOTER_DEFAULT].caption;
function footerSize(size) {
  return FOOTER[size] || FOOTER[FOOTER_DEFAULT];
}
function checkFooterFits(
  fits,
  { site = null, locale = globalThis.document?.documentElement?.lang, caption = CAPTION } = {},
) {
  if (site && fits.some((fit) => fit.overflow || fit.fs < captionMinimum(site, locale, caption.fsMin)))
    throw new RangeError(`Popup footer cannot fit: ${site} (${locale})`);
  return fits;
}
/**
 * Fit footer captions as one group.
 * @param {{caption?: object, width?: number|number[], site?: string|null, locale?: string}} [options] caption:
 *     the footer's caption size; width: the room, or one per caption; site: the wording site, which makes a
 *     caption that cannot fit throw.
 * @returns {{text: string, fs: number, lines: number}[]} The fit of each caption.
 */
export const footerFit = (captions, options = {}) => {
  const caption = options.caption || CAPTION;
  return checkFooterFits(
    NGUI.fitGroup(captions, caption.fs, caption.fsMin, options.width ?? caption.w, caption.lines, { maxH: caption.h }),
    { ...options, caption },
  );
};
/**
 * Fit footer captions into their label widgets, one size for all.
 * @param {object[]} widgets The label widgets; updated in place.
 * @param {{caption?: object, width?: number|number[], site?: string|null, locale?: string}} [options]
 * @returns {{text: string, fs: number, lines: number}[]} The fit of each caption.
 */
export function footerLabels(widgets, captions, options = {}) {
  const caption = options.caption || CAPTION,
    width = options.width ?? caption.w;
  widgets.forEach((widget, i) => Object.assign(widget, { w_: Array.isArray(width) ? width[i] : width, h_: caption.h }));
  return checkFooterFits(
    NGUI.fitLabels(widgets, captions, caption.fs, caption.fsMin, width, caption.lines, { maxH: caption.h }),
    { ...options, caption },
  );
}
