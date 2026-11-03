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
//   slot       (g) => nodes a caller adds in the row; g = {i, x, y, valueY, right, room, text, h}: the row's index, the value's left
//              edge, the row's centre, the value's centre, the inner right edge, the value's room, the fitted value and the row's height
import * as NGUI from "./ngui.js";
import { STATS_BOX as B } from "./ui-tokens.js";
import { wrapPath } from "./util.js";

const { node, sprite, label } = NGUI;
const colourOf = (colour) => colour || "#000000/1.00";
const lineHeight = (fitted) => fitted.lines * fitted.fs * B.linePitch;

// the box's width in what holds it: "frame" is a popup frame's width, "panel" a panel's, "table" a table's the box stands over
export const boxWidth = (holder, width) => width - B.inset[holder];

// the width the captions take: the widest one and the gap to the values (none without captions)
export const captionWidth = (captions) => {
  const shown = captions.filter(Boolean);
  return shown.length ? Math.max(...shown.map((caption) => NGUI.textWidth(caption, B.caption))) + B.captionGap : 0;
};

// the room a value's text may fill: the box less its padding on both sides, the caption column, what the row reserves and the slack
export const valueRoom = (holder, width, { capW = 0, reserve = 0 } = {}) => boxWidth(holder, width) - 2 * B.padX - capW - reserve - B.slack;

// the value cut to its first `cut` lines and an ellipsis, when it takes more
function cutTo(fitted, cut) {
  if (!cut || fitted.lines <= cut) return fitted;
  return { ...fitted, text: `${fitted.text.split("\n").slice(0, cut).join("\n")}\u2026`, lines: cut };
}

// holder: "frame", "panel" or "table", width: its width; at: the box's top centre in the caller's frame; name: the box node's name;
// names: the rows' node name prefixes ({caption: "F", value: "V", divider: "L"} by default).
// Gives {nodes, height, width, capW, rows: [{y, h, top, bottom, text, room}]}; the nodes are the box (fill and line) first, then
// each row's caption, value, secondary line, slot nodes and the divider under it (none under the last row).
export function statsBox({ holder, width, at = [0, 0], rows, name = "Box", names = {} }) {
  const prefix = { caption: "F", value: "V", divider: "L", secondary: "Original", ...names };
  const boxW = boxWidth(holder, width);
  const capW = captionWidth(rows.map((row) => row.caption));
  const left = at[0] - boxW / 2, right = at[0] + boxW / 2 - B.padX;
  const placed = rows.map((row) => {
    const reserve = row.reserve ?? 0, room = valueRoom(holder, width, { capW, reserve });
    const fitted = typeof row.value === "object" && row.value
      ? row.value : cutTo(wrapPath(String(row.value ?? ""), B.value.fs, B.value.fsMin, room, B.value.lines), row.cut);
    const h = Math.max(row.minH ?? B.rowMin, lineHeight(fitted) + B.rowPad + (row.secondary ? B.secondary.extra : 0));
    return { row, room, fitted, h };
  });
  const height = placed.reduce((sum, { h }) => sum + h, 0) + 2 * B.padY;
  const nodes = [node(name, [at[0], at[1] - height / 2], [sprite("Common_Bg_Raius10px", boxW, height, { depth: 5, color: `${B.fill}/1.00` })], [
    node("Line", [0, 0], [sprite("Common_Line_Raius10px", boxW + B.lineOutset, height + B.lineOutset, { depth: 4, color: `${B.line}/1.00` })]),
  ])];
  const out = [];
  let top = at[1] - B.padY;
  placed.forEach(({ row, room, fitted, h }, i) => {
    const y = top - h / 2, x = left + B.padX + capW, valueY = y + (row.secondary ? B.secondary.rise : 0);
    if (row.caption != null) nodes.push(node(`${prefix.caption}_${i}`, [left + B.padX, y], [label(row.caption, B.caption, "#2d4664/1.00", capW, 50, { pivot: 3, depth: 8 })]));
    nodes.push(node(`${prefix.value}_${i}`, [x, valueY], [label(fitted.text, fitted.fs, colourOf(row.colour), room + B.slack,
      fitted.lines * fitted.fs * B.linePitch + B.labelPad, { pivot: 3, depth: 8, ...(row.ariaLabel != null ? { ariaLabel: row.ariaLabel } : {}) })]));
    if (row.secondary) {
      const second = NGUI.fitText(row.secondary, B.secondary.fs, B.secondary.fsMin, room, 1);
      nodes.push(node(`${prefix.secondary}_${i}`, [x, top - h + B.secondary.foot],
        [label(second.text, second.fs, B.secondary.colour, room, B.secondary.h, { pivot: 3, depth: 8, ellipsis: true, ariaLabel: row.secondary })]));
    }
    if (row.slot) nodes.push(...row.slot({ i, x, y, valueY, right, room, text: fitted, h }));
    if (i < placed.length - 1) nodes.push(node(`${prefix.divider}_${i}`, [at[0], top - h],
      [sprite("Common_Line_Dot", boxW - 2 * B.dividerInset, 4, { type: 2, depth: 7, color: `${B.divider}/1.00` })]));
    out.push({ y, h, top, bottom: top - h, text: fitted, room });
    top -= h;
  });
  return { nodes, height, width: boxW, capW, rows: out };
}
