// Switch geometry in NGUI units. Preserve popup.js's Common_Btn_BG body,
// Common_Line_Raius20px frame and Common_Bg_Raius10px name band.
// The cards are laid out in a container (the paper, SPACE.xl in from each side), a row of equal columns and a stack of rows
// (ui-grid.js). The cards are the App's own sizes (250 to 790 wide, their incline adds up to 144), not spans of the 12-column
// grid, so the columns are spread between the content box's edges instead of snapped to it.
import { CHIP, POPUP, SPACE, STATS_BOX } from "./ui-tokens.js";
import { container, row as layoutRow, stack as layoutStack } from "./ui-grid.js";
const GRID_WIDTH = 1818;
// the paper's inset on every side: SPACE.xl, as it always was
const INSET = SPACE.xl;
// The gap between neighbouring cards, across (the least one) and down. No prefab gives the switch cards a gap (none of the
// prefab numbers pinned in tests/test_ui_tokens.py is 48), so it is the nearest scale value to the 48 it was: the grid's gutter.
const GAP = SPACE.xl;
const lean = Math.tan(10 * Math.PI / 180);

// The pitch of `count` equal boxes of width `w` spread edge to edge over `span` (the first box's left edge to the last one's right).
function spreadPitch(count, w, span) {
  if (count < 2) return span - w;
  const { boxes } = layoutRow(Array.from({ length: count }, () => ({ w, h: 0 })), { w: span, justify: "between" });
  return boxes[1].x - boxes[0].x;
}

// The list popups' cards in the L class (owner, 2026-11-11, BL-176): at least MIN_COLUMNS of a kind a row, the card scaled
// down (its aspect and name band kept) until they fit the paper less its inset, the vertical scrollbar's band (SCROLL_BAND)
// and the inclined edges' overhang; a portrait strip (horizontal) at PORTRAIT_SCALE, its names clear of the scrollbar under it.
export const MIN_COLUMNS = Object.freeze({ lobby: 2, model: 3 });
export const PORTRAIT_SCALE = .875; // 280 x 616: whole units
export const SCROLL_BAND = SPACE.m;
export function switchGrid(kind, width = GRID_WIDTH) {
  // The generic list (no kind): six columns of 250 x 226 cards, centred, at the card's width and SPACE.l across (34 before; the
  // gap that keeps six columns inside the paper's inset at the standard width) and SPACE.xxl down (56 before).
  if (!kind) return { card: { w: 250, h: 226, bar: 68 }, columns: 6, pitch: [250 + SPACE.l, 226 + 68 + SPACE.xxl], rowH: 226 + 68, overhang: 304 * lean };
  let card = kind === "spr" ? { w: 320 * PORTRAIT_SCALE, h: 704 * PORTRAIT_SCALE, bar: 104 }
    : kind === "lobby" ? { w: 790, h: 444.375, bar: 104 }
    : kind === "model" ? { w: 360, h: 360, bar: 104 }
    : { w: 250, h: 226, bar: 68 };
  const max = kind === "lobby" ? 2 : kind === "spr" || kind === "model" ? 4 : 6;
  // the paper less its inset and, for a list that scrolls down, the scrollbar's band at its right
  const area = container({ w: width - (kind === "spr" ? 0 : SCROLL_BAND), h: 0, padding: [0, INSET] }).inner;
  const least = MIN_COLUMNS[kind];
  if (least) {
    // m cards, GAP apart, and the overhang of the last one's incline: m w + (m - 1) GAP + (w r + bar + 10) lean <= area
    const r = card.h / card.w, w = (area.w - (least - 1) * GAP - (card.bar + 10) * lean) / (least + r * lean);
    // whole units, the width a multiple of 16 so a 16:9 card's height is whole too
    const whole = Math.floor(w / 16) * 16;
    if (whole < card.w) card = { ...card, w: whole, h: whole * r };
  }
  const overhang = (card.h + card.bar + 10) * lean;
  const columns = Math.max(1, Math.min(max, Math.floor((area.w - overhang + GAP) / (card.w + GAP))));
  const rowH = card.h + card.bar;
  const pitch = [kind === "spr" ? card.w + GAP : spreadPitch(columns, card.w, area.w - overhang), rowH + GAP];
  return { card, columns, pitch, rowH, overhang, horizontal: kind === "spr", leftAligned: true };
}

// NGUI inclines each widget around its own centre; children are positioned separately.
// Measure the outer corners, rather than splitting the total overhang equally about the body centre.
export function cardInkBounds(tree) {
  const bounds = { left: Infinity, right: -Infinity, top: -Infinity, bottom: Infinity };
  function visit(n, px, py, sx, sy) {
    if (n.active === false) return;
    const x = px + n.pos[0] * sx, y = py + n.pos[1] * sy;
    sx *= n.scale?.[0] ?? 1; sy *= n.scale?.[1] ?? 1;
    for (const w of n.widgets || []) {
      if (!("sprite" in w)) continue;
      const width = w.w_ * sx, height = w.h_ * sy, pivot = w.pivot ?? 4;
      const cx = x + (.5 - (pivot % 3) / 2) * width;
      const cy = y + (Math.floor(pivot / 3) / 2 - .5) * height;
      const half = width / 2 + Math.abs(Math.tan((w.incline || 0) * Math.PI / 180) * height / 2);
      bounds.left = Math.min(bounds.left, cx - half); bounds.right = Math.max(bounds.right, cx + half);
      bounds.top = Math.max(bounds.top, cy + height / 2); bounds.bottom = Math.min(bounds.bottom, cy - height / 2);
    }
    for (const child of n.layoutChildren || n.children || []) visit(child, x, y, sx, sy);
  }
  visit(tree, 0, 0, 1, 1);
  return bounds;
}

export function placeSwitchCards(trees, { columns, pitch, rowH, horizontal, leftAligned }, paper) {
  const boxes = trees.map(cardInkBounds);
  const size = horizontal ? Math.max(1, trees.length) : columns;
  // the content box: the paper less the inset; the rows stack down it, GAP apart
  const area = container({ x: paper.left, y: 0, w: paper.right - paper.left, h: 0, padding: [0, INSET] }).inner;
  const cell = rowH ?? pitch[1] - GAP;
  const lines = layoutStack(Array.from({ length: Math.ceil(trees.length / size) }, () => ({ w: area.w, h: cell })), { gap: pitch[1] - cell });
  for (let start = 0; start < trees.length; start += size) {
    const row = boxes.slice(start, start + size);
    // A full row sets the spacing; short rows retain its left inset and column positions.
    // Portraits retain a compact fixed gap as one strip, including past the paper.
    if (leftAligned && !horizontal && row.length >= columns && columns > 1) {
      // (the cards' ink boxes may overlap, their inclined edges interlocking: a negative gap, which a row of boxes does not take)
      pitch[0] = (area.w + row[0].left - row[columns - 1].right) / (columns - 1);
    }
    const left = Math.min(...row.map((b, i) => b.left + i * pitch[0]));
    const right = Math.max(...row.map((b, i) => b.right + i * pitch[0]));
    const x = leftAligned ? area.x - left : (paper.left + paper.right - left - right) / 2;
    const y = paper.top - INSET - Math.max(...row.map((b) => b.top)) - lines.boxes[Math.floor(start / size)].y;
    row.forEach((_, i) => { trees[start + i].pos = [x + i * pitch[0], y]; });
  }
  return boxes.map((b, i) => ({ left: b.left + trees[i].pos[0], right: b.right + trees[i].pos[0],
    top: b.top + trees[i].pos[1], bottom: b.bottom + trees[i].pos[1] }));
}

// Portrait body: image on the left, name/chips/stacked fields on the right;
// the existing full-width action row remains below both halves.
// (the two halves are SPACE.l apart, the chips SPACE.s, a field to the next SPACE.m, a value to its line SPACE.xs: the scale's own values)
export const portraitPanel = { left: -1340, right: -550, top: 490, bottom: -530, gap: SPACE.l };

export function splitPortraitPanel(tree, find, wrap) {
  const b = portraitPanel, middle = (b.left + b.right) / 2;
  const width = (b.right - b.left - b.gap) / 2, cx = middle + b.gap / 2 + width / 2;
  const picture = find(tree, "Picture");
  picture.pos = [b.left + width / 2, (b.top + b.bottom) / 2];
  // UINonEquipmentInventory/Inventory_ListControl/EquipPos/Texture is supplied by the caller.
  const texture = find(picture, "Texture");
  texture.pos = [0, 0];
  Object.assign(texture.widgets.find((w) => "texture" in w), { w_: width, h_: b.top - b.bottom, pivot: 4 });
  const name = find(tree, "Name"), edit = find(tree, "EditName"), original = find(tree, "OriginalName");
  name.pos = [cx - (edit ? 28 : 0), b.top - 54];
  const nw = name.widgets[0];
  Object.assign(nw, { w_: width - (edit ? 60 : 0), h_: 100, ellipsis: true });
  if (edit) edit.pos = [b.right - 24, name.pos[1]];
  if (original) { original.pos = [cx, b.top - 126]; original.widgets[0].w_ = width; }
  const chips = tree.children.filter((n) => n.name.startsWith("Chip_"));
  const chipWidth = (width - SPACE.s * (chips.length - 1)) / Math.max(1, chips.length);
  // the fields box starts 234 under the panel's top; the chips (72 high) stand POPUP.clearance above it
  const boxTop = b.top - 234, chipY = boxTop + POPUP.clearance + CHIP.h / 2;
  chips.forEach((chip, i) => {
    chip.pos = [middle + b.gap / 2 + chipWidth / 2 + i * (chipWidth + SPACE.s), chipY];
    chip.widgets[0].w_ = chipWidth;
    find(chip, "Line").widgets[0].w_ = chipWidth;
    const text = find(chip, "Text");
    for (const n of text.children || []) for (const w of n.widgets || []) if ("text" in w) {
      w.w_ = chipWidth - 20; w.ellipsis = true;
    }
  });
  const fields = tree.children.filter((n) => /^F_\d+$/.test(n.name));
  let top = b.top - 246;
  fields.forEach((caption, i) => {
    const value = find(tree, `V_${i}`), w = value.widgets[0];
    const fitted = wrap(w.ariaLabel ?? w.text, w.fontSize, 26, width - 32, i === 0 ? 5 : 3);
    // A persisted error may include a very long path. Keep its full accessible value,
    // but bound its visible status row so CRC and the action row stay reachable.
    const clipped = i === 2 && fitted.lines > 3;
    if (clipped) { fitted.text = fitted.text.split("\n").slice(0, 3).join("\n") + "…"; fitted.lines = 3; }
    const h = fitted.lines * fitted.fs * 1.25 + SPACE.xs;
    caption.pos = [middle + b.gap / 2 + 16, top];
    Object.assign(caption.widgets[0], { w_: width - 32, h_: 48, pivot: 0 });
    value.pos = [caption.pos[0], top - 50];
    Object.assign(w, { text: fitted.text, fontSize: fitted.fs, w_: width - 32, h_: h, pivot: 0, ellipsis: clipped });
    top -= 50 + h + SPACE.m;
    const line = find(tree, `L_${i}`);
    if (line) { line.pos = [cx, top + 10]; line.widgets[0].w_ = width - 24; }
  });
  const box = find(tree, "Fields");
  box.pos = [cx, (boxTop + top) / 2];
  Object.assign(box.widgets[0], { w_: width, h_: boxTop - top });
  Object.assign(find(box, "Line").widgets[0], { w_: width + STATS_BOX.lineOutset, h_: boxTop - top + STATS_BOX.lineOutset });
  return tree;
}
