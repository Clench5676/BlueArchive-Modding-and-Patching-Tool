// The game's light stats box (STATS_BOX in ui-tokens.js): the fill, its line, the dotted dividers between rows and the rows
// themselves, laid out as pure data. statsBox() takes what holds the box and its width and the rows, and gives the nodes
// (positions in the caller's frame) and the box's height; nothing here touches the page, so tests can run it.
//
// A row is {caption, value, colour, ariaLabel, cut, reserve, secondary, minH, slot}:
//   caption    the left column's text, one line (rows without one start the value at the box's padding)
//   value      the text, wrapped by wrapPath() into up to three lines from 38 down to 26 and never cut; or an already fitted
//              {text, fs, lines} (a caller that fits several rows together)
//   colour     the value's colour as "#rrggbb/a" (black)
//   ariaLabel  the value label's accessible text
//   cut        lines kept when the fit needs more: the rest becomes an ellipsis (0, the default: never)
//   reserve    width left free at the value's right (an icon, a button), the value's room is narrower by it
//   secondary  a smaller one-line text under the value, cut with an ellipsis
//   minH       the row's height at least (STATS_BOX.rowMin)
//   more       width a button at the value's end takes (its gap included) when the value is longer than one line: the value is
//              then cut to the one line it has at its full size, the button's width left free (a details panel that does not
//              scroll, owner 2026-11-11); a value that fits keeps the whole row
//   slot       (g) => nodes a caller adds in the row; g = {i, x, y, valueY, right, room, text, h, cut}: the row's index, the value's
//              left edge, the row's centre, the value's centre, the inner right edge, the value's room, the fitted value, the
//              row's height and whether `more` cut the value
// type: the box's sizes when it is one size down from the prefab's ({caption, value: {fs, fsMin, lines}, rowMin}; STATS_BOX's
// by default).
import * as NGUI from "./ngui.js";
import { STATS_BOX as B } from "./ui-tokens.js";
import { wrapPath } from "./util.js";

const { node, sprite, label } = NGUI;
const colourOf = (colour) => colour || "#000000/1.00";
const lineHeight = (fitted) => fitted.lines * fitted.fs * B.linePitch;

// the box's width in what holds it: "frame" is a popup frame's width, "panel" a panel's, "table" a table's the box stands over
export const boxWidth = (holder, width) => width - B.inset[holder];

// the width the captions take: the widest one and the gap to the values (none without captions)
export const captionWidth = (captions, size = B.caption) => {
  const shown = captions.filter(Boolean);
  return shown.length ? Math.max(...shown.map((caption) => NGUI.textWidth(caption, size))) + B.captionGap : 0;
};

// The details panel's box, one size under the prefab's (owner, 2026-11-11: the panel one size down): captions and values 34
// (TYPE.badge, the size under the box's 38), values down to 26 as before, rows 56 high at least.
export const STATS_BOX_S = Object.freeze({ caption: 34, value: Object.freeze({ fs: 34, fsMin: 26, lines: 3 }), rowMin: 56 });

const graphemes = (text) => globalThis.Intl?.Segmenter
  ? [...new Intl.Segmenter(undefined, { granularity: "grapheme" }).segment(text)].map((part) => part.segment) : Array.from(text);
// a value on one line at size fs within room: the whole of it when it fits (cut false), else as much of its first line as
// fits (whole graphemes)
export function oneLine(text, fs, room) {
  const first = String(text).split("\n")[0];
  if (first === String(text) && NGUI.glyphWidth(first, fs) <= room) return { text: first, fs, lines: 1, cut: false };
  const parts = graphemes(first);
  let lo = 0, hi = parts.length;
  while (lo < hi) {
    const mid = Math.ceil((lo + hi) / 2);
    if (NGUI.glyphWidth(parts.slice(0, mid).join(""), fs) <= room) lo = mid; else hi = mid - 1;
  }
  return { text: parts.slice(0, lo).join(""), fs, lines: 1, cut: true };
}

// the room a value's text may fill: the box less its padding on both sides, the caption column, what the row reserves and the slack
export const valueRoom = (holder, width, { capW = 0, reserve = 0 } = {}) => boxWidth(holder, width) - 2 * B.padX - capW - reserve - B.slack;

// the height of a box whose values take these numbers of lines at the full value size (no secondary lines, no minH); type: the
// box's sizes, as statsBox takes them
export const boxHeight = (lines, type = null) => {
  const rowMin = type?.rowMin ?? B.rowMin, fs = type?.value?.fs ?? B.value.fs;
  return lines.reduce((sum, n) => sum + Math.max(rowMin, n * fs * B.linePitch + B.rowPad), 0) + 2 * B.padY;
};

// the value cut to its first `cut` lines and an ellipsis, when it takes more (lines counted as drawn, a line break in the text
// included); the last line kept gives up graphemes until the ellipsis fits in room
function cutTo(fitted, cut, room = Infinity) {
  const lines = fitted.text.split("\n");
  if (!cut || lines.length <= cut) return { ...fitted, lines: Math.max(fitted.lines ?? 0, lines.length) };
  const kept = lines.slice(0, cut);
  let last = graphemes(kept[cut - 1]);
  while (last.length && NGUI.glyphWidth(`${last.join("")}\u2026`, fitted.fs) > room) last = last.slice(0, -1);
  kept[cut - 1] = last.join("");
  return { ...fitted, text: `${kept.join("\n")}\u2026`, lines: cut };
}

// holder: "frame", "panel" or "table", width: its width; at: the box's top centre in the caller's frame; name: the box node's name;
// names: the rows' node name prefixes ({caption: "F", value: "V", divider: "L"} by default).
// Gives {nodes, height, width, capW, rows: [{y, h, top, bottom, text, room}]}; the nodes are the box (fill and line) first, then
// each row's caption, value, secondary line, slot nodes and the divider under it (none under the last row).
export function statsBox({ holder, width, at = [0, 0], rows, name = "Box", names = {}, type = null }) {
  const prefix = { caption: "F", value: "V", divider: "L", secondary: "Original", ...names };
  const size = { caption: B.caption, value: B.value, rowMin: B.rowMin, ...(type || {}) };
  const boxW = boxWidth(holder, width);
  const capW = captionWidth(rows.map((row) => row.caption), size.caption);
  const left = at[0] - boxW / 2, right = at[0] + boxW / 2 - B.padX;
  const placed = rows.map((row) => {
    let reserve = row.reserve ?? 0, room = valueRoom(holder, width, { capW, reserve }), cut = false, fitted;
    if (typeof row.value === "object" && row.value) fitted = row.value;
    else if (row.more) {
      // one line at the full size: the whole value, or what fits beside the button
      fitted = oneLine(row.value ?? "", size.value.fs, room);
      if (fitted.cut) {
        reserve += row.more;
        room = valueRoom(holder, width, { capW, reserve });
        fitted = oneLine(row.value ?? "", size.value.fs, room);
        cut = true;
      }
    } else fitted = cutTo(wrapPath(String(row.value ?? ""), size.value.fs, size.value.fsMin, room, size.value.lines), row.cut, room);
    const h = Math.max(row.minH ?? size.rowMin, lineHeight(fitted) + B.rowPad + (row.secondary ? B.secondary.extra : 0));
    return { row, room, fitted, h, cut };
  });
  const height = placed.reduce((sum, { h }) => sum + h, 0) + 2 * B.padY;
  const nodes = [node(name, [at[0], at[1] - height / 2], [sprite("Common_Bg_Raius10px", boxW, height, { depth: 5, color: `${B.fill}/1.00` })], [
    node("Line", [0, 0], [sprite("Common_Line_Raius10px", boxW + B.lineOutset, height + B.lineOutset, { depth: 4, color: `${B.line}/1.00` })]),
  ])];
  const out = [];
  let top = at[1] - B.padY;
  placed.forEach(({ row, room, fitted, h, cut }, i) => {
    const y = top - h / 2, x = left + B.padX + capW, valueY = y + (row.secondary ? B.secondary.rise : 0);
    if (row.caption != null) nodes.push(node(`${prefix.caption}_${i}`, [left + B.padX, y], [label(row.caption, size.caption, "#2d4664/1.00", capW, 50, { pivot: 3, depth: 8 })]));
    nodes.push(node(`${prefix.value}_${i}`, [x, valueY], [label(fitted.text, fitted.fs, colourOf(row.colour), room + B.slack,
      fitted.lines * fitted.fs * B.linePitch + B.labelPad, { pivot: 3, depth: 8, ...(row.ariaLabel != null ? { ariaLabel: row.ariaLabel } : {}) })]));
    if (row.secondary) {
      const second = NGUI.fitText(row.secondary, B.secondary.fs, B.secondary.fsMin, room, 1);
      nodes.push(node(`${prefix.secondary}_${i}`, [x, top - h + B.secondary.foot],
        [label(second.text, second.fs, B.secondary.colour, room, B.secondary.h, { pivot: 3, depth: 8, ellipsis: true, ariaLabel: row.secondary })]));
    }
    if (row.slot) nodes.push(...row.slot({ i, x, y, valueY, right, room, text: fitted, h, cut }));
    if (i < placed.length - 1) nodes.push(node(`${prefix.divider}_${i}`, [at[0], top - h],
      [sprite("Common_Line_Dot", boxW - 2 * B.dividerInset, 4, { type: 2, depth: 7, color: `${B.divider}/1.00` })]));
    out.push({ y, h, top, bottom: top - h, text: fitted, room, cut });
    top -= h;
  });
  return { nodes, height, width: boxW, capW, rows: out };
}
