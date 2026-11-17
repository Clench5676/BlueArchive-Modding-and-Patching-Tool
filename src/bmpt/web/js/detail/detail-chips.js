// The variant chips in the nameplate's white panel, in the style of the game's type chips.
import * as NGUI from "../ui/ngui.js";
import { T } from "../core/i18n.js";
import { hasUnseen as hasFresh } from "./unseen-assets.js";
import { CHIP } from "../ui/ui-tokens.js";
import { row, stack, toNode } from "../ui/ui-grid.js";
import { hasAsset } from "../mods/switch-sources.js";
import { $, DOT, RECRUIT, S, TAN10, glyphsOf, hitButton, markedLabel } from "../core/util.js";
import {
  sprite,
  node,
  rect,
  ORIGIN,
  fam,
  variantKey,
  layouts,
  ctx,
  renderFailed,
  commit,
  applied,
  showable,
  showGen,
  picturesReady,
  fillPictures,
  shortName,
  variant,
  isFav,
  appliedKey,
  shownSource,
  setChipPics,
} from "./detail.js";
import { LOCAL_FRAME } from "./detail-strip.js";
import { openPicker } from "./detail-switchers.js";

let chipToken = 0;

// --- variant chips in the nameplate's white panel (style of the game's type chips) ---
// 3 columns x 2 rows; a long name takes two columns; with more variants than fit, the last cell opens the picker.
const CHIP_FS = CHIP.fs,
  CHIP_W = CHIP.w,
  CHIP_PITCH = CHIP.pitch; // the prefab chip (Char_Target/Type/MainType/BG): 272 x 72, 40 down to 28
const chipSpan = (item) => {
  const need =
    glyphsOf(shortName(item), 32) +
    (item.recruitable ? (32 * 1.35) / RECRUIT.ratio + 6 : 0) +
    (appliedKey(item) !== "self" ? 34 : 0);
  return need > CHIP_W - CHIP.inset ? 2 : 1;
};
export const byRank = (a, b) => isFav(b) - isFav(a) || b.recruitable - a.recruitable || a.order - b.order;
function placeChips(items) {
  const cells = [];
  let row = 0,
    col = 0;
  for (const item of items) {
    const span = chipSpan(item);
    if (col + span > 3) {
      row += 1;
      col = 0;
    }
    if (row > 1) return null;
    cells.push({ v: item, row, col, span });
    col += span;
  }
  return cells;
}
function chipLayout() {
  // favourites first, then recruitable variants, then the others in release order
  const list = fam.variants.filter(hasAsset).sort(byRank);
  const all = placeChips(list);
  if (all) return all;
  // six cells, the last one is "more": take variants in order, skipping ones too wide for the space left in a row
  const caps = [3, 2],
    fill = [0, 0],
    picked = [];
  const tryPlace = (v) => {
    const span = chipSpan(v);
    for (let r = 0; r < 2; r++)
      if (fill[r] + span <= caps[r]) {
        picked.push({ v, row: r, col: fill[r], span });
        fill[r] += span;
        return true;
      }
    return false;
  };
  for (const v of list) {
    tryPlace(v);
    if (fill[0] === caps[0] && fill[1] === caps[1]) break;
  }
  // the current variant is always shown
  if (!picked.some((p) => p.v.key === variantKey)) {
    const last = [...picked]
      .reverse()
      .find((p) => p.span === chipSpan(variant()) || (p.span === 2 && chipSpan(variant()) === 1));
    if (last) Object.assign(last, { v: variant(), span: chipSpan(variant()) });
  }
  return [...picked, { more: list.length - picked.length, row: 1, col: 2, span: 1 }];
}
function chipNode(name, pos, w, caption, active, swapped, mark, fresh = false) {
  const body = sprite("Common_Bg_Raius10px", w, CHIP.h, {
    incline: 10,
    depth: 5,
    color: active ? "#2c4564/1.00" : "#ffffff/1.00",
  });
  const color = active ? "#ffffff/1.00" : "#2c4564/1.00";
  const lbl = markedLabel(caption, {
    fs: CHIP_FS,
    fsMin: CHIP.fsMin,
    color,
    maxW: w - CHIP.inset - (swapped ? 30 : 0),
    mark,
    depth: 8,
  });
  const kids = [node("Label", [swapped ? -15 : 0, 0], [], lbl.nodes)];
  if (!active)
    kids.push(
      node(
        "Line",
        [0, 0],
        [sprite("Common_Line_Raius10px", w, CHIP.h, { incline: 10, depth: 6, color: "#c0c9d4/1.00" })],
      ),
    );
  // the variant wears another portrait: the game's swap icon at the right end
  if (swapped)
    kids.push(
      node(
        "Swapped",
        [w / 2 - 36, 0],
        [sprite("Common_Icon_Change", 32, 32, { type: 0, depth: 9, color: active ? "#e6b6ef/1.00" : "#9c44ac/1.00" })],
      ),
    );
  if (fresh) kids.push(DOT([w / 2 - 4 + 36 * TAN10, 34], 30));
  return node(name, pos, [body], kids);
}
function chipBoxes(chips) {
  // UICharacterDetail's 272 x 72 chip, 290 pitch and 84 row pitch are prefab geometry, not App spacing.
  const width = CHIP_W + 2 * CHIP_PITCH;
  const rows = stack([{ h: CHIP.h }, { h: CHIP.h }], {
    y: -(234 - 58) - CHIP.h / 2,
    w: width,
    gap: CHIP.rowPitch - CHIP.h,
  });
  const slots = rows.boxes.map(
    (b) =>
      row(
        Array.from({ length: 3 }, () => ({ w: CHIP_W, h: CHIP.h })),
        {
          x: 514 + (-b.y - CHIP.h / 2 - (234 - 101)) * TAN10 - width / 2,
          y: b.y,
          gap: CHIP.gap,
        },
      ).boxes,
  );
  return chips.map((chip) => ({
    ...chip,
    box: { ...slots[chip.row][chip.col], w: CHIP_W + (chip.span - 1) * CHIP_PITCH },
  }));
}
// Char_Target's frame in the screen: the prefab's offsets down to it, summed as NGUI.render does. The chips are
// drawn in their own layer and take the plate's frame from the layout, so they stay in its white panel wherever
// the game puts it.
export const PLATE_PATH = "LeftInfo/CharInfo/Char_Target";
function plateFrame(list) {
  let x = list.pos[0],
    y = list.pos[1],
    sx = list.scale?.[0] ?? 1,
    sy = list.scale?.[1] ?? 1,
    at = list;
  for (const name of PLATE_PATH.split("/")) {
    at = at.children.find((k) => k.name === name);
    x += at.pos[0] * sx;
    y += at.pos[1] * sy;
    sx *= at.scale?.[0] ?? 1;
    sy *= at.scale?.[1] ?? 1;
  }
  return { pos: [x, y], scale: [sx, sy] };
}
export function renderChips() {
  return drawChips().catch(renderFailed);
}
async function drawChips() {
  const my = ++chipToken,
    gen = showGen;
  const ok = () => showable("chips", my, chipToken, gen);
  const host = $("ngui-chips");
  // BG2 (886 x 202, top-centre at 514, 234 in Char_Target, sheared about its centre) holds the chips
  const chips = chipLayout(),
    plate = plateFrame(layouts.list);
  const tree = node(
    "Chips",
    plate.pos,
    [],
    chipBoxes(chips).map(({ v, more, box }) => {
      const pos = toNode(box, 4, LOCAL_FRAME),
        w = box.w;
      return v
        ? chipNode(
            `Chip_${v.key}`,
            pos,
            w,
            shortName(v),
            v.key === variantKey,
            shownSource(v) !== "self",
            v.recruitable,
            hasFresh(v),
          )
        : chipNode("Chip_more", pos, w, T("chips.more", { n: more }), false, false, false);
    }),
    plate.scale,
  );
  const next = document.createDocumentFragment();
  const view = await NGUI.render(tree, next, { scale: S, origin: ORIGIN, z: 20, deferImages: true });
  if (my === chipToken) {
    setChipPics([picturesReady(view.images, next)]);
    fillPictures(view.thumbnails, next);
  }
  if (!ok()) return; // a newer render took over
  for (const [path, entry] of view.nodes) {
    const m = /\/Chip_([^/]*)$/.exec(path);
    if (!m || !entry.els[0]) continue;
    // the chip's caption names it; the shown variant's chip is the pressed one
    const chip = m[1] === "more" ? chips.find((c) => !c.v) : chips.find((c) => c.v?.key === m[1]);
    const button = hitButton(
      next,
      rect(entry.els[0]),
      () => (m[1] === "more" ? openPicker() : ctx.go(fam.key, m[1])),
      chip?.v ? shortName(chip.v) : T("chips.more", { n: chip?.more ?? 0 }),
    );
    if (chip?.v) button.setAttribute("aria-pressed", String(chip.v.key === variantKey));
  }
  commit(() => {
    if (!ok()) return;
    applied.chips = my;
    host.replaceChildren(next);
  });
}
