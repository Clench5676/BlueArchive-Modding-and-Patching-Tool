// Shared UIPopup_Option_PC / UIPopup_Option_Toggle rows, used by Settings, Batch Import and the Export popup.
// A row is a card laid out in a container (ui-grid.js): the card less its padding is the content box, and the title, the
// options and the value rows' text and buttons take their boxes from it with row and stack. The prefab's own values stay (the
// card's width 1552 and padding 58, its title 17 from the top, the radio groups 508 apart, the cards 12 apart, the small buttons
// 20 apart); the other numbers of the card's anatomy (240, 109, the dotted line's size and place, 100, 19, 5) are kept as the rows
// had them, and everything the App adds comes from the scales (ui-tokens.js).
import { node, sprite, label, fitText, fitGroup } from "./ngui.js";
import { PANEL, SPACE, CONTROL, XS_BUTTON, TYPE, COLOUR } from "./ui-tokens.js";
import { container, grid, row as layoutRow, stack as layoutStack, toNode } from "./ui-grid.js";

// UIPopup_Option_PC's card: 1552 wide (PANEL.padCard 58 each side), 240 high with one line of options, its dotted line (Common_Dotline,
// 1523 x 4) 109 under its top and 16.3 in from its left edge (the line is not centred on the card), the title 76 high 17 under the
// top (PANEL.cardTitleTop)
export const ROW_W = 1552;
export const CARD = Object.freeze({ w: ROW_W, h: 240, line: 109, lineX: 16.3, lineW: 1523, lineH: 4, titleH: 76 });
// UIPopup_Option_Toggle: the options of a radio group 508 apart (the prefab's pitch; a group too long for it shares the content box),
// a further line of options 100 under the last, the radio's label 19 clear of the radio (the label's node 49 from the radio's centre)
// and 5 under its centre
const OPTION = Object.freeze({ pitch: 508, rowPitch: 100, labelGap: 19, labelDrop: 5, size: CONTROL.icon.h });
// the lines of an option stay in the band under the row's dotted line: 123 high round the option's centre in a one-line row,
// and where the options wrap onto more rows (100 apart) no taller than the pitch less a margin
export const OPTION_TEXT_H = 96, OPTION_TEXT_H_ONE_ROW = 120;

// The card's frame in NGUI space (centre origin, y up): where a box in the card's own frame (x right, y down from its top left)
// goes. The card's content box is the card less its padding: PANEL.padCard each side, SPACE.m under the dotted line, SPACE.l above
// the foot (the value rows' gaps, see valueGeometry).
const frameOf = (h) => ({ cx: 0, cy: 0, w: ROW_W, h });
export const cardContent = (h) => container({ w: ROW_W, h, padding: [CARD.line + SPACE.m, PANEL.padCard, SPACE.l, PANEL.padCard] });
// UIPopup_Option_PC's Label_Locale_Info: an option's note under the dotted line (SkillCutIn, CVLabel), in the caption role
// (TYPE.caption, the prefab's 36), pivot top left, its top SPACE.l under the line (the prefab's 27.9), across the content box,
// up to two lines; what the card holds follows it SPACE.m below. An alert note takes the red part of SkillCutIn's note.
export const NOTE = Object.freeze({ top: SPACE.l, gap: SPACE.m, lines: 2 });
export const noteFit = (text) => fitText(text, TYPE.caption.fs, TYPE.caption.min, cardContent(0).inner.w, NOTE.lines);
// the note's label at `pos` (valueGeometry's note), `h` its lines' height
export function noteNode(fit, pos, w, h, { colour = COLOUR.noteAlert } = {}) {
  return node("Note", pos, [label(fit.text, fit.fs, `${colour}/1.00`, w, h + 10, { pivot: 0, depth: 8 })]);
}
// the title: the content box's width, from the card's top PANEL.cardTitleTop, left aligned
const titleBox = () => ({ x: PANEL.padCard, y: PANEL.cardTitleTop, w: ROW_W - 2 * PANEL.padCard, h: CARD.titleH });
// the card's title room: the box less a SPACE.xl slack at its right
const titleRoom = () => titleBox().w - SPACE.xl;

function titleNode(text, h) {
  const box = titleBox(), f = fitText(text, 50, 40, titleRoom(), 1);
  return node("Title", toNode(box, 3, frameOf(h)), [label(f.text, f.fs, "#506e8e/1.00", box.w, box.h, { pivot: 3, depth: 8 })], [
    node("TagDeco", [-13, 5.2], [sprite("Common_Square_Bg", 6, 45, { depth: 9, color: "#5cd3ff/1.00" })]),
  ]);
}

// Where something that follows the title on its line stands (the Enable Mods "?"): the box of a `width` x `height` item SPACE.xl
// after a title text of `textW`, centred on the title's line, in the card's frame, and its NGUI position (pivot centre).
export function afterTitle(h, textW, { width, height }) {
  const box = titleBox();
  const line = layoutRow([{ w: textW, h: box.h }, { w: width, h: height }], { x: box.x, y: box.y, h: box.h, gap: SPACE.xl });
  return { box: line.boxes[1], pos: toNode(line.boxes[1], 4, frameOf(h)) };
}

// a row: the white body with the title and its dotted line; returns [tree, height, hits]
export function rowBase(name, y, h, title) {
  return node(name, [0, y], [sprite("Common_Bg_Raius5px", ROW_W, h, { depth: 5, color: "#ffffff/1.00" })], [
    titleNode(title, h),
    node("DotLine", [CARD.lineX - ROW_W / 2, h / 2 - CARD.line], [sprite("Common_Dotline", CARD.lineW, CARD.lineH, { pivot: 3, type: 2, depth: 6, color: "#ffffff/1.00" })]),
  ]);
}

// the card's options: how many lines the options take, the row's height, and where each option's box stands (the card's frame)
export function optionLayout(count, columns = count) {
  const lines = Math.ceil(count / columns), h = CARD.h + (lines - 1) * OPTION.rowPitch;
  const content = cardContent(h).inner, pitch = Math.min(OPTION.pitch, content.w / columns);
  // the options of a line side by side, a line's centre line the middle of the band under the dotted line
  const centre = CARD.line + (CARD.h - CARD.line) / 2;
  const across = layoutRow(Array.from({ length: columns }, () => ({ w: pitch, h: OPTION.size })), { x: content.x }).boxes;
  const boxes = Array.from({ length: count }, (_, i) => ({ ...across[i % columns], y: centre + Math.floor(i / columns) * OPTION.rowPitch - OPTION.size / 2 }));
  // an option: the radio, then its label (the label's room is the option's less the radio, its gap, a SPACE.s clear of the next
  // option and a SPACE.m slack the fit keeps)
  const labelBox = pitch - OPTION.size - OPTION.labelGap - SPACE.s;
  return { h, pitch, boxes, labelBox, room: labelBox - SPACE.m };
}

// radio options in a row (the game's On / Off toggles, as many as needed): [value, text, the text's language if it is
// not the page's]; dim: the options drawn faded (a switch that is busy or unavailable; a click still answers why)
export function radioRow(name, y, title, options, selected, onPick, { dim = false, description = "", columns = options.length } = {}) {
  const lay = optionLayout(options.length, columns), h = lay.h, row = rowBase(name, y, h, title), hits = [];
  const { pitch } = lay;
  // the options are one kind of control side by side: one size for all of them (each text measured in its own language),
  // its lines inside the band under the row's dotted line
  const fits = fitGroup(options.map(([, text]) => text), 48, 34, lay.room, 2,
    { lang: options.map(([, , lang]) => lang || null), maxH: options.length > columns ? OPTION_TEXT_H : OPTION_TEXT_H_ONE_ROW });
  options.forEach(([value, text, lang], i) => {
    const on = selected instanceof Set ? selected.has(value) : value === selected;
    const f = fits[i], [x, y] = toNode({ ...lay.boxes[i], w: OPTION.size }, 4, frameOf(h));   // the radio's centre
    const alpha = dim ? (on ? "0.55" : "0.35") : on ? "1.00" : "0.71";
    row.children.push(node(`Opt_${i}`, [x, y], [], [
      node("BG", [0, 0], [sprite("Common_Btn_Radio_BG", OPTION.size, OPTION.size, { type: 0, depth: 25, ...(dim ? { color: "#ffffff/0.55" } : {}) })]),
      ...(on ? [node("On", [0, 0], [sprite("Common_Btn_Radio_On", OPTION.size, OPTION.size, { type: 0, depth: 26, ...(dim ? { color: "#ffffff/0.55" } : {}) })])] : []),
      node("Label", [OPTION.size / 2 + OPTION.labelGap, -OPTION.labelDrop], [label(f.text, f.fs, `#2c4663/${alpha}`, lay.labelBox, 50, { pivot: 3, depth: 26, lang })]),
    ]));
    hits.push({ path: `${name}/Opt_${i}/BG`, w: pitch - SPACE.m, fn: () => onPick(value), name: text, description });
  });
  return { tree: row, h, hits };
}

// --- a value with buttons on the right (folders, version, cache); long values wrap, never cut ---
// The value takes the content box's left; the light-blue buttons (XS_BUTTON: 402 x 136, SPACE.m apart) stand right-aligned
// on its right edge. Beside the value they leave it the content box less their pitches, and no fewer than four grid columns
// (the least a path is read in); buttons that do not fit wrap to a second line, right-aligned as the first. Under the value
// (below) the whole line is theirs. A value alone is at least TEXT_MIN high.
const TEXT_MIN = SPACE.xxxl;
const buttonPitch = XS_BUTTON.w + SPACE.m;
const valueMin = () => grid(cardContent(0).inner).span(4).w;

// how many buttons each line holds: [first line, second line, ...]
export function buttonLines(count, below = false) {
  const { w } = cardContent(0).inner;
  const perLine = Math.max(1, below ? Math.floor((w + SPACE.m) / buttonPitch) : Math.floor((w - valueMin()) / buttonPitch));
  const lines = [];
  for (let i = 0; i < count; i += perLine) lines.push(Math.min(perLine, count - i));
  return lines;
}
export const valueRoom = (count, below = false) => below ? cardContent(0).inner.w : cardContent(0).inner.w - (buttonLines(count)[0] || 0) * buttonPitch;

// the row's height and where its parts stand (NGUI space, y up from the card's centre): textH is the value's height. Beside the
// value the first line of buttons is centred on it; under it the lines of buttons follow the value, SPACE.l below. noteH: a note
// (NOTE) of that height under the dotted line, the value and the buttons NOTE.gap under it.
export function valueGeometry(count, textH, { below = false, noteH = 0 } = {}) {
  const inner = cardContent(0).inner, lines = buttonLines(count, below), { w: bw } = XS_BUTTON, bh = CONTROL.buttonXS.h;
  const noteBox = noteH ? { x: inner.x, y: CARD.line + NOTE.top, w: inner.w, h: noteH } : null;
  const start = noteBox ? noteBox.y + noteBox.h + NOTE.gap : inner.y;
  const blockH = Math.max(textH, below || !count ? TEXT_MIN : bh);
  const lineBoxes = (n, box) => layoutRow(Array.from({ length: n }, () => ({ w: bw, h: bh })), { x: box.x, y: box.y, w: box.w, h: box.h, gap: SPACE.m, justify: "end" }).boxes;
  const outer = inner.x, wide = inner.w;
  let valueBox, height;
  const buttons = [];
  if (below) {
    const group = layoutStack(lines.map(() => ({ w: wide, h: bh })), { x: outer, w: wide, gap: SPACE.m });
    const whole = layoutStack([{ w: wide, h: blockH }, ...lines.length ? [{ w: wide, h: group.h }] : []], { x: outer, y: start, w: wide, gap: SPACE.l });
    const [block, under] = whole.boxes;
    valueBox = layoutRow([{ grow: 1, h: textH }], { x: block.x, y: block.y, w: block.w, h: block.h }).boxes[0];
    if (under) layoutStack(lines.map(() => ({ w: wide, h: bh })), { x: outer, y: under.y, w: wide, gap: SPACE.m })
      .boxes.forEach((line, j) => buttons.push(...lineBoxes(lines[j], line)));
    height = start + whole.h + SPACE.l;
  } else {
    const first = lines[0] || 0, groupW = first ? first * bw + (first - 1) * SPACE.m : 0;
    const whole = layoutStack([{ w: wide, h: blockH }, ...lines.slice(1).map(() => ({ w: wide, h: bh }))], { x: outer, y: start, w: wide, gap: SPACE.m });
    const [block, ...more] = whole.boxes;
    const top = layoutRow([{ grow: 1, h: textH }, ...first ? [{ w: groupW, h: bh }] : []], { x: block.x, y: block.y, w: block.w, h: block.h, gap: first ? SPACE.m : 0 });
    valueBox = top.boxes[0];
    if (first) buttons.push(...lineBoxes(first, top.boxes[1]));
    more.forEach((line, j) => buttons.push(...lineBoxes(lines[j + 1], line)));
    height = start + whole.h + SPACE.l;
  }
  const frame = frameOf(height), up = (box) => toNode(box, 4, frame)[1], centreX = (box) => box.x + box.w / 2 - ROW_W / 2;
  let at = 0;
  const ys = lines.map((n) => { const y = up(buttons[at]); at += n; return y; });
  return { h: height, blockH, midY: up(valueBox), value: toNode(valueBox, 3, frame), room: valueBox.w, lines, ys, xs: buttons.map(centreX),
    boxes: buttons, valueBox, noteBox, note: noteBox && toNode(noteBox, 0, frame) };
}

// A row of `count` buttons `width` wide under the dotted line (the Export pair), SPACE.xxl apart and centred in the content box:
// the card's height, the buttons' centres across (xs, NGUI space) and their line (y).
export function buttonRowGeometry(count, width) {
  const inner = cardContent(0).inner, bh = CONTROL.buttonXS.h, h = inner.y + bh + SPACE.l;
  const { boxes } = layoutRow(Array.from({ length: count }, () => ({ w: width, h: bh })), { x: inner.x, y: inner.y, w: inner.w, h: bh, gap: SPACE.xxl, justify: "center" });
  const frame = frameOf(h);
  return { h, xs: boxes.map((box) => toNode(box, 4, frame)[0]), y: toNode(boxes[0], 4, frame)[1], boxes };
}

// The cards of a panel (the area w x h, padded by PANEL.padList), one under the other PANEL.stackGap apart and centred: each card's
// box, its centre below the area's top (negative, NGUI y) and the height the panel scrolls over.
export function stackCards(heights, area) {
  const panel = container({ ...area, padding: PANEL.padList });
  const laid = layoutStack(heights.map((h) => ({ w: ROW_W, h })), { x: panel.inner.x, y: panel.inner.y, w: panel.inner.w, gap: PANEL.stackGap, align: "center" });
  return { boxes: laid.boxes, centres: laid.boxes.map((box) => -(box.y + box.h / 2)), height: laid.h + 2 * PANEL.padList };
}
