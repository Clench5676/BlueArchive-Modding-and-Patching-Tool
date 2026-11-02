// Shared UIPopup_Option_PC / UIPopup_Option_Toggle rows, used by Settings and Batch Import.
import { node, sprite, label, fitText, fitGroup } from "./ngui.js";

export const ROW_W = 1552;
// the lines of an option stay in the band under the row's dotted line: 123 high round the option's centre in a one-line row,
// and where the options wrap onto more rows (100 apart) no taller than the pitch less a margin
export const OPTION_TEXT_H = 96, OPTION_TEXT_H_ONE_ROW = 120;

function titleNode(text, y = 65) {
  const f = fitText(text, 50, 40, 1400, 1);
  return node("Title", [-718.5, y], [label(f.text, f.fs, "#506e8e/1.00", 1440, 76, { pivot: 3, depth: 8 })], [
    node("TagDeco", [-13, 5.2], [sprite("Common_Square_Bg", 6, 45, { depth: 9, color: "#5cd3ff/1.00" })]),
  ]);
}

// a row: the white body with the title and its dotted line; returns [tree, height, hits]
export function rowBase(name, y, h, title) {
  return node(name, [0, y], [sprite("Common_Bg_Raius5px", ROW_W, h, { depth: 5, color: "#ffffff/1.00" })], [
    titleNode(title, h / 2 - 55),
    node("DotLine", [-759.7, h / 2 - 109], [sprite("Common_Dotline", 1523, 4, { pivot: 3, type: 2, depth: 6, color: "#ffffff/1.00" })]),
  ]);
}

// radio options in a row (the game's On / Off toggles, as many as needed): [value, text, the text's language if it is
// not the page's]; dim: the options drawn faded (a switch that is busy or unavailable; a click still answers why)
export function radioRow(name, y, title, options, selected, onPick, { dim = false, description = "", columns = options.length } = {}) {
  const h = 240 + (Math.ceil(options.length / columns) - 1) * 100, row = rowBase(name, y, h, title), hits = [];
  const pitch = Math.min(508, 1400 / columns);
  // the options are one kind of control side by side: one size for all of them (each text measured in its own language),
  // its lines inside the band under the row's dotted line
  const fits = fitGroup(options.map(([, text]) => text), 48, 34, pitch - 110, 2,
    { lang: options.map(([, , lang]) => lang || null), maxH: options.length > columns ? OPTION_TEXT_H : OPTION_TEXT_H_ONE_ROW });
  options.forEach(([value, text, lang], i) => {
    const x = -689 + i % columns * pitch, on = selected instanceof Set ? selected.has(value) : value === selected;
    const f = fits[i];
    const alpha = dim ? (on ? "0.55" : "0.35") : on ? "1.00" : "0.71";
    row.children.push(node(`Opt_${i}`, [x, h / 2 - 174.5 - Math.floor(i / columns) * 100], [], [
      node("BG", [0, 0], [sprite("Common_Btn_Radio_BG", 60, 60, { type: 0, depth: 25, ...(dim ? { color: "#ffffff/0.55" } : {}) })]),
      ...(on ? [node("On", [0, 0], [sprite("Common_Btn_Radio_On", 60, 60, { type: 0, depth: 26, ...(dim ? { color: "#ffffff/0.55" } : {}) })])] : []),
      node("Label", [49, -5], [label(f.text, f.fs, `#2c4663/${alpha}`, pitch - 90, 50, { pivot: 3, depth: 26, lang })]),
    ]));
    hits.push({ path: `${name}/Opt_${i}/BG`, w: pitch - 20, fn: () => onPick(value), name: text, description });
  });
  return { tree: row, h, hits };
}

