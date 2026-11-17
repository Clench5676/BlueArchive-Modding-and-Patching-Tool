// The design system's layout primitives (docs/design-system/layout.md): a composed screen describes its parts as
// containers, a 12-column grid, rows and stacks and places its prefab clones into the boxes they return.
// Everything is in prefab units, in a container's own frame (x to the right, y down from its top left corner);
// toNode turns a box into the NGUI position of a widget. Pure functions with no DOM and no NGUI import, so the
// layout of every screen can be tested in Node.
import { GRID } from "./ui-tokens.js";

/**
 * A rectangle in a container's frame, in prefab units.
 * @typedef {{x: number, y: number, w: number, h: number}} Box
 */

/**
 * Padding as in CSS: one number, [vertical, horizontal] or [top, right, bottom, left].
 * @typedef {number | number[]} Padding
 */

/**
 * An item of a row or a stack: a fixed size, or in a row a share of the width left over (grow). Any other fields come
 * back on the item's box.
 * @typedef {object} LayoutItem
 * @property {number} [w] The width.
 * @property {number} [h] The height.
 * @property {number} [grow] In a row, the item's share of the width left over; its height is the row's unless it has one.
 * @property {number[]} [ink] [top, bottom] of the item's ink from its top, for a text.
 * @property {number} [mid] The item's visual centre from its top, where it gives one.
 */

/** Make a box. */
export const box = (x, y, w, h) => ({ x, y, w, h });

// padding: one number, [vertical, horizontal] or [top, right, bottom, left], as in CSS
function sides(padding) {
  if (typeof padding === "number") return [padding, padding, padding, padding];
  if (padding.length === 2) return [padding[0], padding[1], padding[0], padding[1]];
  return padding;
}

/**
 * The box inside another once padding is taken off.
 * @returns {Box} The inner box.
 */
export function inset(b, padding) {
  const [top, right, bottom, left] = sides(padding);
  return box(b.x + left, b.y + top, b.w - left - right, b.h - top - bottom);
}

/**
 * Make a container: its outer box and its content box inside the padding.
 * @param {{x?: number, y?: number, w: number, h: number, padding?: Padding}} options The outer box (w x h at x,
 *     y) and the padding.
 * @returns {{outer: Box, inner: Box}} The two boxes.
 */
export function container({ x = 0, y = 0, w, h, padding = 0 }) {
  const outer = box(x, y, w, h);
  return { outer, inner: inset(outer, padding) };
}

/**
 * The 12-column grid of a box (the Bootstrap model).
 * @param {{columns?: number, gutter?: number}} [options] The column count and the gutter between columns.
 * @returns {{pitch: number, column: number, at: (n: number) => number, span: (n: number, offset?: number) =>
 *     Box}} The
 * column pitch and width; at(n) is where column n (0-based) starts; span(n, offset) covers n columns and the n
 * - 1 gutters between them, offset columns in, as tall as the box.
 */
export function grid(b, { columns = GRID.columns, gutter = GRID.gutter } = {}) {
  const pitch = (b.w + gutter) / columns;
  return {
    pitch,
    column: pitch - gutter,
    at: (n) => b.x + n * pitch,
    span: (n, offset = 0) => box(b.x + offset * pitch, b.y, n * pitch - gutter, b.h),
  };
}

/**
 * An item's visual centre from its top: mid where the item gives one (a text's: its script's reference glyph,
 * so the lines of one language share a centre line whatever their letters), else the middle of its ink, else
 * its box's.
 * @returns {number} The distance from the item's top.
 */
export const centreOf = (item) => item.mid ?? (item.ink ? (item.ink[0] + item.ink[1]) / 2 : item.h / 2);

/**
 * Place items side by side.
 * Each item is a fixed size, a share of the width left over (grow), or both; any other field of an item comes
 * back on its box. The row is as tall as h, or as the room its items need around the shared centre line.
 * @param {object} [options] The row's frame and layout.
 * @param {number|null} [options.w] The width, or the items' own.
 * @param {number|null} [options.h] The height, or what the items need.
 * @param {"start"|"end"|"center"} [options.align] Across the row; centre means the visual centre: a text's ink,
 *     a widget's box (the game's rule: every element's visual centre on the bar's centre line).
 * @param {"start"|"end"|"center"|"between"} [options.justify]
 * @returns {{boxes: (LayoutItem & Box)[], w: number, h: number, centre: number|null}} The items' boxes, the
 *     row's size and the centre line (null unless centred).
 */
export function row(items, { x = 0, y = 0, w = null, h = null, gap = 0, align = "center", justify = "start" } = {}) {
  const fixed = items.reduce((sum, item) => sum + (item.grow ? 0 : item.w), 0) + gap * Math.max(0, items.length - 1);
  const grows = items.reduce((sum, item) => sum + (item.grow || 0), 0);
  const width = w ?? fixed;
  const left = Math.max(0, width - fixed);
  const widths = items.map((item) => (item.grow ? (left * item.grow) / grows : item.w));
  // the room above and under the centre line every item needs, and so the row's height
  const above = Math.max(0, ...items.filter((item) => !item.grow || item.h).map(centreOf));
  const below = Math.max(0, ...items.filter((item) => !item.grow || item.h).map((item) => item.h - centreOf(item)));
  const height = h ?? (align === "center" ? above + below : Math.max(0, ...items.map((item) => item.h || 0)));
  const centre = align === "center" ? (h == null ? above : height / 2) : null;
  const used = widths.reduce((sum, value) => sum + value, 0) + gap * Math.max(0, items.length - 1);
  const free = grows ? 0 : Math.max(0, width - used);
  const between = justify === "between" && items.length > 1 ? free / (items.length - 1) : 0;
  let cursor = x + (justify === "end" ? free : justify === "center" ? free / 2 : 0);
  const boxes = items.map((item, i) => {
    const itemW = widths[i],
      itemH = item.grow && !item.h ? height : item.h;
    const top =
      item.grow && !item.h
        ? y
        : align === "start"
          ? y
          : align === "end"
            ? y + height - itemH
            : y + centre - centreOf(item);
    const out = { ...item, ...box(cursor, top, itemW, itemH) };
    cursor += itemW + gap + between;
    return out;
  });
  return { boxes, w: width, h: height, centre: centre == null ? null : y + centre };
}

/**
 * Place items one under the other.
 * @param {object} [options] The stack's frame and layout.
 * @param {number|null} [options.w] The width, or the widest item's.
 * @param {number|null} [options.h] The height, or the items' together.
 * @param {"start"|"center"|"end"|"stretch"} [options.align] Across the stack; stretch makes each item as wide
 *     as the stack.
 * @param {"start"|"center"|"end"} [options.justify] Along the stack, within h.
 * @returns {{boxes: (LayoutItem & Box)[], w: number, h: number}} The items' boxes and the stack's size.
 */
export function stack(items, { x = 0, y = 0, w = null, h = null, gap = 0, align = "start", justify = "start" } = {}) {
  const width = w ?? Math.max(0, ...items.map((item) => item.w || 0));
  const used = items.reduce((sum, item) => sum + item.h, 0) + gap * Math.max(0, items.length - 1);
  const height = h ?? used,
    free = Math.max(0, height - used);
  let cursor = y + (justify === "end" ? free : justify === "center" ? free / 2 : 0);
  const boxes = items.map((item) => {
    const itemW = align === "stretch" ? width : item.w;
    const left = align === "center" ? x + (width - itemW) / 2 : align === "end" ? x + width - itemW : x;
    const out = { ...item, ...box(left, cursor, itemW, item.h) };
    cursor += item.h + gap;
    return out;
  });
  return { boxes, w: width, h: height };
}

/**
 * Make a text item for a row or a stack: its fitted text and size, its box (the label's lines at NGUI's pitch,
 * drawn from the box's top), its ink (the band its glyphs cover) and mid (the centre of its script's reference
 * glyph, what centres it: the rule badges follow).
 * @param {object} ui NGUI's measures: fitText, glyphWidth, fontSizeFor, inkBand, inkShift and LINE_PITCH.
 * @param {{fs: number, min: number}} type
 * @param {{maxW?: number, lines?: number}} [options] The width the text may take and the most lines it may wrap
 *     to.
 * @returns {{text: string, fs: number, px: number, w: number, h: number, ink: number[]|null, mid: number|null,
 *     lines: number}} The item.
 */
export function textItem(ui, text, type, { maxW = Infinity, lines = 1 } = {}) {
  const fit = ui.fitText(String(text ?? ""), type.fs, type.min, maxW, lines);
  const rows = String(fit.text).split("\n");
  const px = ui.fontSizeFor?.(fit.text, fit.fs) ?? fit.fs;
  const h = rows.length * px * (ui.LINE_PITCH ?? 1.15);
  const w = Math.min(maxW, Math.max(0, ...rows.map((line) => ui.glyphWidth(line, fit.fs))));
  // inkBand and inkShift measure from the centre of the lines' box (down)
  const band = typeof ui.inkBand === "function" ? ui.inkBand(fit.text, px) : null;
  const ink = band ? [h / 2 + band.top, h / 2 + band.bottom] : null;
  const mid = typeof ui.inkShift === "function" ? h / 2 - ui.inkShift(fit.text, px, { reference: true }) : null;
  return { text: fit.text, fs: fit.fs, px, w, h, ink, mid, lines: rows.length };
}

/**
 * The width of a control that follows its caption: the caption's width and the pad on each side, at least minW.
 * @param {{fs: number, pad: number, minW?: number}} control
 * @param {(caption: string, fs: number) => number} measure The caption's width at a size.
 * @returns {number} The control's width.
 */
export const controlWidth = (control, caption, measure) =>
  Math.max(control.minW ?? 0, measure(caption, control.fs) + 2 * control.pad);

// NGUI's pivots: the share of the width and height left of and above the pivot point (0 top left ... 8 bottom right).
const PIVOT_X = [0, 0.5, 1, 0, 0.5, 1, 0, 0.5, 1];
const PIVOT_Y = [0, 0, 0, 0.5, 0.5, 0.5, 1, 1, 1];

/**
 * The NGUI position of a widget with a pivot that fills a box of a container.
 * @param {Box} b The box in the container's frame.
 * @param {number|undefined} pivot The NGUI pivot, 0 (top left) to 8 (bottom right); 4 (centre) by default.
 * @param {{cx: number, cy: number, w: number, h: number}} frame The container: its centre in the parent's NGUI
 *     space and its size.
 * @returns {number[]} The position [x, y], y up.
 */
export function toNode(b, pivot, frame) {
  const left = frame.cx - frame.w / 2 + b.x,
    top = frame.cy + frame.h / 2 - b.y;
  return [left + b.w * PIVOT_X[pivot ?? 4], top - b.h * PIVOT_Y[pivot ?? 4]];
}
