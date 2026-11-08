// The design system's layout primitives (docs/design-system.md). A composed screen describes its parts as containers, a
// 12-column grid, rows and stacks, and places its prefab clones into the boxes they return, instead of computing each
// position by hand. Everything is in prefab units, in a container's own frame: x to the right and y down from its top left
// corner. toNode turns a box into the NGUI position of a widget with a given pivot (y up, from the container's centre).
// Pure functions: no DOM and no NGUI import, so the layout of every screen can be tested in Node.
import { GRID } from "./ui-tokens.js";

export const box = (x, y, w, h) => ({ x, y, w, h });

// padding: one number, [vertical, horizontal] or [top, right, bottom, left], as in CSS
function sides(padding) {
  if (typeof padding === "number") return [padding, padding, padding, padding];
  if (padding.length === 2) return [padding[0], padding[1], padding[0], padding[1]];
  return padding;
}

// The box inside b once padding is taken off.
export function inset(b, padding) {
  const [top, right, bottom, left] = sides(padding);
  return box(b.x + left, b.y + top, b.w - left - right, b.h - top - bottom);
}

// A container: its outer box (w x h at x, y) and its content box inside the padding.
export function container({ x = 0, y = 0, w, h, padding = 0 }) {
  const outer = box(x, y, w, h);
  return { outer, inner: inset(outer, padding) };
}

// The 12-column grid of a box (the Bootstrap model): span(n, offset) covers n columns and the n - 1 gutters between them,
// offset columns in; at(n) is where column n (0-based) starts. Spans are as tall as the box.
export function grid(b, { columns = GRID.columns, gutter = GRID.gutter } = {}) {
  const pitch = (b.w + gutter) / columns;
  return {
    pitch,
    column: pitch - gutter,
    at: (n) => b.x + n * pitch,
    span: (n, offset = 0) => box(b.x + offset * pitch, b.y, n * pitch - gutter, b.h),
  };
}

// An item's visual centre from its top: mid where the item gives one (a text's: its script's reference glyph, so the lines
// of one language share a centre line whatever their letters), else its ink's middle (ink = [top, bottom] from the item's
// top), else its box's.
export const centreOf = (item) => item.mid ?? (item.ink ? (item.ink[0] + item.ink[1]) / 2 : item.h / 2);

// Items side by side. Each item is { w, h } (a fixed size), { grow } (a share of the width left over; its h is the row's) or
// both; an item may carry ink (text) and any other fields, which come back on its box. align, across the row: "start",
// "end" or "center" (default), where centre means the visual centre: a text's ink, a widget's box (the game's rule: every
// element's visual centre on the bar's centre line). justify, along it: "start", "end", "center" or "between". The row is
// as tall as h, or as the room its items need around the shared centre line. Returns { boxes, w, h, centre }.
export function row(items, { x = 0, y = 0, w = null, h = null, gap = 0, align = "center", justify = "start" } = {}) {
  const fixed = items.reduce((sum, item) => sum + (item.grow ? 0 : item.w), 0) + gap * Math.max(0, items.length - 1);
  const grows = items.reduce((sum, item) => sum + (item.grow || 0), 0);
  const width = w ?? fixed;
  const left = Math.max(0, width - fixed);
  const widths = items.map((item) => item.grow ? left * item.grow / grows : item.w);
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
    const itemW = widths[i], itemH = item.grow && !item.h ? height : item.h;
    const top = item.grow && !item.h ? y : align === "start" ? y : align === "end" ? y + height - itemH
      : y + centre - centreOf(item);
    const out = { ...item, ...box(cursor, top, itemW, itemH) };
    cursor += itemW + gap + between;
    return out;
  });
  return { boxes, w: width, h: height, centre: centre == null ? null : y + centre };
}

// Items one under the other, gap apart. align, across the stack: "start" (default), "center", "end" or "stretch" (each item
// as wide as the stack). justify, along it: "start" (default), "center" or "end" within h. Returns { boxes, w, h }.
export function stack(items, { x = 0, y = 0, w = null, h = null, gap = 0, align = "start", justify = "start" } = {}) {
  const width = w ?? Math.max(0, ...items.map((item) => item.w || 0));
  const used = items.reduce((sum, item) => sum + item.h, 0) + gap * Math.max(0, items.length - 1);
  const height = h ?? used, free = Math.max(0, height - used);
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

// A text item for row and stack: its fitted text and size, its box (the label's lines at NGUI's pitch, drawn from the box's
// top), its ink (the band its glyphs cover) and mid (the centre of its script's reference glyph, what centres it: the
// rule badges follow). ui supplies NGUI's measures (fitText, glyphWidth, fontSizeFor, inkBand, inkShift, LINE_PITCH); type
// is a TYPE role ({ fs, min }); maxW the width it may take; lines the most it may wrap to.
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

// A control whose width follows its caption (CONTROL entries: { h, fs, pad, minW }): the caption's width and pad on each side,
// at least minW. measure(caption, fs) is the caption's width at its size.
export const controlWidth = (control, caption, measure) => Math.max(control.minW ?? 0, measure(caption, control.fs) + 2 * control.pad);

// NGUI's pivots: the share of the width and height left of and above the pivot point (0 top left ... 8 bottom right).
const PIVOT_X = [0, 0.5, 1, 0, 0.5, 1, 0, 0.5, 1];
const PIVOT_Y = [0, 0, 0, 0.5, 0.5, 0.5, 1, 1, 1];

// The NGUI position (y up) of a widget with this pivot that fills box b of a container whose frame centre sits at
// [cx, cy] in the parent's NGUI space and is w x h.
export function toNode(b, pivot, frame) {
  const left = frame.cx - frame.w / 2 + b.x, top = frame.cy + frame.h / 2 - b.y;
  return [left + b.w * PIVOT_X[pivot ?? 4], top - b.h * PIVOT_Y[pivot ?? 4]];
}
