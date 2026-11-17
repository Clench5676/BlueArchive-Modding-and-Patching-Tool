// The details panel of a list popup: title bar, picture, name and chips, fields and actions (detailsTree).
import * as NGUI from "./ngui.js";
import { switchGrid } from "../mods/switch-layout.js";
import * as Buttons from "./game-buttons.js";
import { resizeButton } from "./button-layout.js";
import { T } from "../core/i18n.js";
import { STATS_BOX_S, boxHeight, statsBox } from "./stats-box.js";
import { CHIP, CHIP_TIERS, CONTROL, SPACE, TYPE, XS_BUTTON } from "./ui-tokens.js";
import { RECRUIT, TAN10, clone, glyphsOf, markedLabel } from "../core/util.js";
import { container, grid, row, stack as layoutStack, textItem, toNode } from "./ui-grid.js";
import { node, sprite, label, find, layouts } from "./popup.js";
import { LP } from "./popup-list.js";
import { messageText, LINE_BUTTON } from "./popup-message.js";

// The detail actions: the XS body, their captions 34 down to 24 (smaller, with more padding),
// the button's padding each side of the caption (the body's slant inside it). All of them on one row: the
// XXS body, its captions 30 down to 22, where the row of XS bodies at their minimum width does not fit.
const ACTION_TIERS = Object.freeze([
  Object.freeze({ control: CONTROL.buttonXS, fs: 34, min: 24 }),
  Object.freeze({ control: CONTROL.buttonXXS, fs: 30, min: 22 }),
]);
// The small button body's soft drop shadow under its solid art: 14 of its 176 rows (Btn_Cancel/BG (1) as drawn).
const ACTION_SHADOW = 14 / 176;
// a loading action's ring against its body's height
const ACTION_RING = 0.55;
// The details panel one size down: its title and the name TYPE.body (the size under TYPE.heading), its chips the
// S chip (CHIP_TIERS), as wide as their captions, its light box STATS_BOX_S; its actions keep the XS button, the
// smallest. The chips stand on the name's line where they fit beside it, SPACE.s apart.
export const DETAILS = Object.freeze({
  title: TYPE.body,
  name: TYPE.body,
  chip: CHIP_TIERS.S,
  box: STATS_BOX_S,
  chipGap: SPACE.s,
});
const CHIP_MIN_GAP = SPACE.xs;
/**
 * Size a chip (CHIP_TIERS) for a caption: as wide as the caption, the recruit mark before it and the pad on
 * each side need, minW at least, maxW at most.
 * @param {{chip?: object, mark?: boolean, maxW?: number}} [options] chip: the chip size (DETAILS.chip); mark:
 *     the recruit mark leads the caption; maxW: the widest it may be.
 * @returns {{w: number, h: number}} The chip's size.
 */
export function chipFit(caption, { chip = DETAILS.chip, mark = false, maxW = Infinity } = {}) {
  const iconW = mark ? (chip.fs * 1.35) / RECRUIT.ratio + 6 : 0;
  const need = Math.ceil(iconW + glyphsOf(caption, chip.fs) + 2 * chip.pad);
  return { w: Math.min(maxW, Math.max(chip.minW, need)), h: chip.h };
}
/**
 * Place chips side by side in rows within a panel: each as wide as `widths` says, `gap` apart, a row centred, a
 * new row where the next one would not fit (its incline counted).
 * @param {number|number[]} widths The chips' widths, or the number of chips at CHIP.w.
 * @param {{h?: number, gap?: number, wrap?: boolean}} [options] h: the chip height; gap: the space between
 *     chips; wrap: false keeps them on one row (the details panel's, whose widths the caller fitted to the
 *     panel).
 * @returns {{row: number, x: number}[]} Each chip's row and its centre's x from the panel's centre.
 */
export function chipPlaces(widths, panelW, { h = DETAILS.chip.h, gap = DETAILS.chipGap, wrap = true } = {}) {
  if (typeof widths === "number") widths = Array.from({ length: widths }, () => CHIP.w);
  const slant = h * TAN10,
    rows = [[]];
  for (const [i, w] of widths.entries()) {
    const cur = rows.at(-1),
      used = cur.reduce((n, j) => n + widths[j] + gap, 0);
    if (wrap && cur.length && used + w + slant > panelW) rows.push([i]);
    else cur.push(i);
  }
  const boxes = [];
  rows.forEach((indices, r) => {
    const placed = row(
      indices.map((i) => ({ w: widths[i], h })),
      { w: panelW, gap: indices.length > 1 ? Math.max(CHIP_MIN_GAP, gap) : 0, justify: "center" },
    );
    placed.boxes.forEach((b, k) => (boxes[indices[k]] = { row: r, x: b.x + b.w / 2 - panelW / 2 }));
  });
  return boxes;
}
/**
 * Size the "..." button at the end of a value cut to one line (the panel does not scroll, a value shows whole
 * in a popup): the line button (LINE_BUTTON) of the light box's line.
 * @param {number} [fs] The value's font size.
 * @returns {{h: number, fs: number, w: number, gap: number}} The button's height, caption size, width and the
 *     gap before it.
 */
export function moreButton(fs = DETAILS.box.value.fs) {
  const h = Math.round(fs * NGUI.LINE_PITCH * LINE_BUTTON.height) + XS_BUTTON.boxInsetH;
  const cap = Math.max(LINE_BUTTON.fsMin, Math.round(fs * LINE_BUTTON.share));
  return { h, fs: cap, w: Math.ceil(glyphsOf(MORE, cap)) + LINE_BUTTON.pad, gap: SPACE.s };
}
const MORE = "…";
/**
 * Build the details panel of a list popup: title bar, the picture, name and chips, fields, actions (exported
 * for the tests, like chipPlaces).
 * The picture takes what the rest leaves of the panel, so the panel does not scroll; the actions stand on its
 * bottom.
 * @param {ListItem} it
 * @param {string} title The title bar's text.
 * @param {(string|null)[][]} fields The fields: [caption, value, colour]; a field without a caption (null) is
 *     its value alone.
 * @param {(string|boolean|Function|undefined)[][]} actions The actions: [caption, fn, disabled, semantic, hint,
 *     loading].
 * @param {NguiNode|null} [portraitTexture] The portrait picture node, for an upright picture beside the stacked
 *     fields.
 * @param {string|null} [kind] The kind of cards: "model" or "photo" give a square picture.
 * @param {{l: number, r: number, t: number, b: number}} [panel]
 * @param {{truncate?: boolean}} [options] truncate: each field's value on one line, a "..." button opening it
 *     whole where it is cut (tree.more lists them).
 * @returns {NguiNode} The panel's node tree, with `more`: the cut values.
 */
export function detailsTree(
  it,
  title,
  fields,
  actions,
  portraitTexture = null,
  kind = null,
  panel = LP.left,
  options = {},
) {
  const w = panel.r - panel.l,
    cx = (panel.l + panel.r) / 2;
  const frame = { w, h: 0, cx, cy: panel.t },
    content = container({ w, h: panel.t - panel.b }).inner;
  const kids = [],
    bands = [],
    more = [];
  const titleText = textItem(NGUI, title, DETAILS.title, { maxW: w - 2 * SPACE.xxl, lines: 1 });
  const titleH = Math.max(73, titleText.h); // The section bar and its decoration keep their prefab heights.
  const titleNode = node(
    "Title_Bg",
    [cx, 0],
    [sprite("Common_Bg_Raius10px", w - SPACE.m, titleH, { depth: 5, color: "#2d4c72/1.00", incline: 10 })],
    [
      node(
        "Deco",
        [-(w - SPACE.m) / 2 + SPACE.l, 0],
        [sprite("Common_BGDeco", 68, 72, { depth: 6, color: "#2a5886/1.00", type: 0 })],
      ),
      node(
        "Deco (1)",
        [(w - SPACE.m) / 2 - SPACE.l, 0],
        [sprite("Common_BGDeco", 68, 72, { depth: 6, color: "#2a5886/1.00", flip: 3, type: 0 })],
      ),
      node(
        "Label",
        [0, 0],
        [label(titleText.text, titleText.fs, "#ffffff/1.00", w - 2 * SPACE.xxl, titleText.h, { depth: 7 })],
      ),
    ],
  );
  kids.push(titleNode);
  bands.push({ name: "Title_Bg", w, h: titleH });
  // A 3D model's picture spans the panel, its name, chips and fields under it, one size for every candidate
  // (model); a photograph's square the same (photo), its picture filling the window (cover) where a model's still
  // is shown whole (contain).
  const model = !portraitTexture && (kind === "model" || kind === "photo");
  const columns = grid(content),
    pictureColumn = portraitTexture ? columns.span(5) : content;
  const infoColumn = portraitTexture ? columns.span(7, 5) : content;
  const infoW = infoColumn.w,
    edit = it.nameEdit ? CONTROL.icon.h + SPACE.m : 0,
    nameRoom = infoW - 2 * SPACE.m - edit;
  const nameFit = messageText(it.full, DETAILS.name.fs, DETAILS.name.min, nameRoom, 2, true);
  const name = textItem(NGUI, nameFit.text, { fs: nameFit.fs, min: nameFit.fs }, { maxW: nameRoom, lines: Infinity });
  const nameW = Math.min(
    nameRoom,
    Math.ceil(Math.max(...nameFit.text.split("\n").map((line) => glyphsOf(line, nameFit.fs)))),
  );
  const using = it.inUseText || (it.inUse ? T("popup.inUse") : null);
  const chips = it.chips || [
    it.tag?.text || (it.recruit ? T("type.recruit") : it.kind === "mod" ? T("type.added") : T("type.noRecruit")),
    ...(using ? [using] : []),
  ];
  const dark = !it.chips && it.recruit,
    chipRoom = infoW - 2 * SPACE.m;
  // the chips stay on one row (never wrapped one under another): where their captions need more than the
  // column at the S chip, the XS chip (one size down: the L class's narrower panel); where they still do, the widest
  // give up width first (one cap for all), and a squeezed caption may shrink to the XS chip's floor
  const widthsAt = (tier) =>
    chips.map((caption, i) => chipFit(caption, { chip: tier, mark: i === 0 && dark, maxW: chipRoom }).w);
  const gapsAt = (tier) => Math.max(0, chips.length - 1) * DETAILS.chipGap + tier.h * TAN10;
  const sum = (ws) => ws.reduce((n, cw) => n + cw, 0);
  const chip = sum(widthsAt(DETAILS.chip)) + gapsAt(DETAILS.chip) <= chipRoom ? DETAILS.chip : CHIP_TIERS.XS;
  const chipGaps = gapsAt(chip);
  let chipW = widthsAt(chip);
  const squeezed = chipW.reduce((n, cw) => n + cw, 0) + chipGaps > chipRoom;
  if (squeezed) {
    const room = chipRoom - chipGaps,
      sorted = [...chipW].sort((a, b) => a - b);
    let cap = room / chips.length;
    for (let k = 0, used = 0; k < sorted.length; used += sorted[k], k += 1) {
      const c = (room - used) / (sorted.length - k);
      if (sorted[k] >= c) {
        cap = c;
        break;
      }
    }
    chipW = chipW.map((cw) => Math.min(cw, Math.floor(cap)));
  }
  const chipFloor = squeezed ? CHIP_TIERS.XS.fsMin : chip.fsMin;
  const chipsW = chipW.reduce((n, cw) => n + cw, 0) + chipGaps;
  // the chips beside a one-line name when the line holds both, else on their own row under it; a 3D model's beside its
  // picture always under the name
  // a portrait's name always centred on its own line, its chips under it
  const beside =
    !model &&
    !portraitTexture &&
    chips.length > 0 &&
    !nameFit.text.includes("\n") &&
    nameW + edit + SPACE.l + chipsW <= chipRoom;
  const places = beside ? null : chipPlaces(chipW, chipRoom, { wrap: false, h: chip.h }),
    rows = !beside && chips.length ? Math.max(...places.map((p) => p.row)) + 1 : 0;
  const headH = beside ? Math.max(name.h, chip.h) : name.h;
  const info = [{ name: "Head", w: infoW, h: headH }];
  kids.push(
    node(
      "Name",
      [0, 0],
      [
        label(name.text, name.fs, "#2d4664/1.00", beside ? nameW + 2 : nameRoom, name.h, {
          depth: 8,
          ellipsis: !!it.original,
          ariaLabel: it.full,
        }),
      ],
    ),
  );
  if (it.nameEdit)
    kids.push(
      node(
        "EditName",
        [0, 0],
        [sprite("Cafe_Icon_NameEdit", CONTROL.icon.h, CONTROL.icon.h, { type: 0, depth: 9, color: "#2d4664/1.00" })],
      ),
    );
  chips.forEach((caption, i) =>
    kids.push(
      node(
        `Chip_${i}`,
        [0, 0],
        [
          sprite("Common_Bg_Raius10px", chipW[i], chip.h, {
            incline: 10,
            depth: 6,
            tier: "chip",
            color: i === 0 && dark ? "#2c4564/1.00" : "#ffffff/1.00",
          }),
        ],
        [
          node(
            "Line",
            [0, 0],
            [sprite("Common_Line_Raius10px", chipW[i], chip.h, { incline: 10, depth: 7, color: "#c0c9d4/1.00" })],
          ),
          node(
            "Text",
            [0, 0],
            [],
            markedLabel(caption, {
              fs: chip.fs,
              fsMin: chipFloor,
              color: i === 0 && dark ? "#ffffff/1.00" : "#2c4564/1.00",
              maxW: chipW[i] - 2 * chip.pad,
              mark: i === 0 && dark,
              depth: 8,
              maxLines: 1,
            }).nodes,
          ),
        ],
      ),
    ),
  );
  if (rows) info.push({ name: "Chips", w: infoW, h: rows * chip.h + (rows - 1) * SPACE.s });
  if (it.original) {
    const original = textItem(NGUI, it.original, TYPE.micro, { maxW: infoW - 2 * SPACE.m, lines: 2 });
    kids.push(
      node(
        "OriginalName",
        [0, 0],
        [
          label(original.text, original.fs, "#2d4664/0.65", infoW - 2 * SPACE.m, original.h, {
            depth: 8,
            ellipsis: true,
            ariaLabel: it.original,
          }),
        ],
      ),
    );
    info.splice(1, 0, { name: "OriginalName", w: infoW, h: original.h });
  }
  // The light stats box keeps its own padding, row sizes and decoration, one size down. A portrait's narrow column
  // stacks each caption and value as successive full-width rows; other sources retain the paired columns. A field
  // without a caption (null) is its value alone. With options.truncate a value keeps one line, and a "…" at its
  // end where it is cut.
  const button = moreButton();
  const cutRow = (field, caption, value) =>
    options.truncate
      ? {
          more: button.w + button.gap,
          slot: ({ i, right, valueY, cut }) => {
            if (!cut) return [];
            more.push({ field, row: i, caption: caption ?? "", value: String(value) });
            const b = Buttons.smallButton(`More_${field}`, right - button.w / 2, valueY, MORE, {
              w: button.w,
              h: button.h,
              fs: button.fs,
              min: button.fs,
              depth: 9,
            });
            const bg = b.widgets.find((x) => "sprite" in x);
            if (bg) bg.tier = "button";
            return [b];
          },
        }
      : {};
  // the captions bold. A portrait's column, or a box whose captions leave a value no room for
  // its lines, stacks each caption over its value: the two rows joined, no divider between.
  const stackedRows = () =>
    fields.flatMap(([caption, value, colour], field) => [
      ...(caption == null ? [] : [{ value: caption, bold: true, joined: true }]),
      { value: String(value), colour, ariaLabel: String(value), ...cutRow(field, caption, value) },
    ]);
  const pairedRows = () =>
    fields.map(([caption, value, colour], i) => ({
      caption: caption ?? "",
      value: String(value),
      colour,
      ariaLabel: String(value),
      cut: options.truncate ? 0 : i === 2 ? 2 : 0,
      ...cutRow(i, caption, value),
    }));
  const fieldW = infoW;
  const boxOf = (rows) =>
    statsBox({
      holder: "panel",
      width: fieldW,
      at: [0, 0],
      name: "Fields",
      rows,
      type: DETAILS.box,
      boldCaptions: true,
    });
  let fieldBox = { nodes: [], height: 0 },
    stacked = !!portraitTexture;
  if (fields.length) {
    fieldBox = boxOf(stacked ? stackedRows() : pairedRows());
    if (!stacked && fieldBox.rows.some((r) => r.text.lines > DETAILS.box.value.lines)) {
      stacked = true;
      fieldBox = boxOf(stackedRows());
    }
  }
  if (stacked)
    for (const part of fieldBox.nodes) {
      const match = /^V_(\d+)$/.exec(part.name);
      if (match) {
        const i = Number(match[1]);
        part.name = `${i % 2 ? "V" : "F"}_${Math.floor(i / 2)}`;
      }
    }
  kids.push(...fieldBox.nodes);
  const fieldBand = fields.length ? { name: "Fields", w: fieldW, h: fieldBox.height } : null;
  if (fieldBand) info.push(fieldBand);
  const infoStack = layoutStack(info, { w: infoW, gap: SPACE.m });

  // The picture takes the room the title, the actions and the rest leave (the 3D model's square is one size for
  // every candidate: what fields with the file name on three lines and the others on one leave, its 10 degree
  // incline within its column; only longer fields shrink it).
  // The actions go in rows of as many XS buttons as the panel holds at their minimum width (the narrower panel,
  // the captions' padding kept), the rows SPACE.m apart.
  const actionGap = SPACE.m,
    rowFits = (tier) => actions.length * (tier.control.minW + actionGap) - actionGap <= w - 2 * SPACE.m;
  // more than even the XXS row holds (a Mod's five): rows of XS bodies, as many a row as the panel holds at their
  // minimum
  const oneRow = ACTION_TIERS.find(rowFits),
    actionTier = oneRow ?? ACTION_TIERS[0],
    BTN = actionTier.control;
  const ACTION_CLEAR = 2 * BTN.pad,
    ACTION_TEXT_H = BTN.h - 8;
  const perRow = oneRow
    ? Math.max(1, actions.length)
    : Math.max(1, Math.floor((w - 2 * SPACE.m + actionGap) / (BTN.minW + actionGap)));
  const actionRows = actions.length ? Math.ceil(actions.length / perRow) : 0;
  const actionsH = actionRows ? actionRows * BTN.h + (actionRows - 1) * actionGap : 0;
  const roomFor = (otherH, others) =>
    content.h - titleH - otherH - actionsH - SPACE.m * (1 + others + (actions.length ? 1 : 0));
  // a model's picture as large as the most its name (two lines) and fields (the file name on three lines unless
  // truncated) leave, whatever this candidate's hold
  const reference = model
    ? infoStack.h -
      name.h +
      Math.max(
        name.h,
        textItem(NGUI, "X\nX", { fs: DETAILS.name.fs, min: DETAILS.name.fs }, { maxW: nameRoom, lines: 2 }).h,
      ) +
      (fields.length
        ? boxHeight(
            fields.map((_, i) => (i === 0 && !options.truncate ? 3 : 1)),
            DETAILS.box,
          ) - fieldBox.height
        : 0)
    : 0;
  const texture = portraitTexture && clone(portraitTexture),
    native = texture?.widgets.find((widget) => "texture" in widget);
  // the portrait card keeps its native aspect in its preview column
  const portraitCard = texture && switchGrid("spr").card;
  const aspect = portraitCard ? portraitCard.w / portraitCard.h : model ? 1 : 16 / 9;
  let pictureW;
  if (portraitTexture) pictureW = Math.max(0, Math.min(pictureColumn.w, roomFor(0, 0) * aspect));
  // a model's square beside the reference and the most room actions take (two rows of XS bodies): one size for all
  else if (model)
    pictureW = Math.max(
      0,
      Math.min(
        (w - 2 * SPACE.m) / (1 + TAN10),
        roomFor(reference, 1) + actionsH - Math.max(actionsH, 2 * CONTROL.buttonXS.h + actionGap),
      ),
    );
  else pictureW = Math.max(0, Math.min(w - 2 * SPACE.m, roomFor(infoStack.h, 1) * aspect));
  const pictureH = pictureW / aspect;
  const source = typeof it.big === "function" ? it.big() : it.big;
  const picture = node("Picture", [0, 0], [], []);
  if (texture) {
    texture.scale = [1, 1];
    Object.assign(native, {
      clipImage: source,
      placeholder: T("import.previewUnavailable"),
      w_: pictureW,
      h_: pictureH,
      imgSize: "cover",
      imgPosition: "center",
      imgLeft: "0",
      imgWidth: "100%",
      incline: 0,
      radius: 0,
    });
    texture.pos = toNode({ x: 0, y: 0, w: pictureW, h: pictureH }, native.pivot, {
      w: pictureW,
      h: pictureH,
      cx: 0,
      cy: 0,
    });
    picture.children = [texture];
  } else {
    picture.widgets = [sprite("Common_Btn_BG", pictureW, pictureH, { incline: 10, depth: 6, color: "#ffffff/1.00" })];
    picture.children = [
      node(
        "Thumb",
        [0, 0],
        [
          {
            clipImage: source,
            placeholder: T("import.previewUnavailable"),
            w_: pictureW - SPACE.m,
            h_: pictureH - SPACE.m,
            pivot: 4,
            depth: 7,
            incline: 10,
            radius: 22,
            color: "#ffffff/1.00",
            imgSize: kind === "model" ? "contain" : "cover",
            imgPosition: "center 18%",
          },
        ],
      ),
      node(
        "Line",
        [0, 0],
        [
          sprite("Common_Line_Raius20px", pictureW - SPACE.s, pictureH - SPACE.s, {
            incline: 10,
            depth: 8,
            color: "#ffffff/1.00",
          }),
        ],
      ),
    ];
  }
  kids.push(picture);
  if (portraitTexture) bands.push({ name: "Portrait", w, h: Math.max(pictureH, infoStack.h) });
  else bands.push({ name: "Picture", w, h: pictureH }, ...infoStack.boxes);

  // every row's bodies fitted together (one caption size for all the actions), each row centred
  const gap = actionGap,
    inRow = Math.min(perRow, Math.max(1, actions.length)),
    bw = (w - 2 * SPACE.m + gap) / inRow;
  const groups = Array.from({ length: actionRows }, (_, r) =>
    actions.slice(r * perRow, (r + 1) * perRow).map(([caption]) => caption),
  );
  const groupFits = groups.map((captions) =>
    NGUI.fitRow(captions, actionTier.fs, actionTier.min, captions.length * (bw - gap), ACTION_CLEAR, 2, {
      maxH: ACTION_TEXT_H,
    }),
  );
  const shared = Math.min(actionTier.fs, ...groupFits.flat().map((f) => f.fs));
  const fits = groups.flatMap((captions, r) =>
    groupFits[r].some((f) => f.fs !== shared)
      ? NGUI.fitRow(captions, shared, shared, captions.length * (bw - gap), ACTION_CLEAR, 2, { maxH: ACTION_TEXT_H })
      : groupFits[r],
  );
  const actionRow = {
    boxes: groups.flatMap((captions, r) => {
      const start = groups.slice(0, r).reduce((n, g) => n + g.length, 0);
      return row(
        fits.slice(start, start + captions.length).map((f) => ({ w: f.width, h: BTN.h })),
        { x: SPACE.m, w: w - 2 * SPACE.m, gap, justify: "center" },
      ).boxes.map((b) => ({ ...b, row: r }));
    }),
  };
  actions.forEach(([caption, , disabled, semantic = "secondary", , loading = false], i) => {
    let body = clone(find(layouts.sortPopup, "Center/Popup/Btn/Btn_Cancel/BG (1)")),
      ink = "#2d5164/1.00";
    if (disabled) {
      const grey = Buttons.disable(node("Action", [0, 0], [], [body]));
      body = clone(find(grey, "BG (1)"));
      ink = find(grey, "Label_Locale").widgets[0].color;
    }
    const bg = body.widgets.find((widget) => "sprite" in widget),
      k = BTN.h / bg.h_;
    body.name = "Body";
    body.pos = [0, 0];
    body.scale = [k, k];
    resizeButton(body, fits[i].width / k);
    // a button however narrow the row makes it: the size tag (developer previews) marks it
    Object.assign(bg, { depth: 6, tier: "button", ...(!disabled ? { color: "#dbeef5/1.00" } : {}) });
    for (const child of body.children || []) for (const widget of child.widgets) if ("w_" in widget) widget.depth = 7;
    // an action still loading (its check under way) shows the game's loading ring in its caption's place, in the
    // caption's ink; the body keeps the caption's width, so nothing moves when the caption comes back
    const caption_ = label(fits[i].text, fits[i].fs, ink, fits[i].width - ACTION_CLEAR, ACTION_TEXT_H, { depth: 8 });
    const ring = Math.min(ACTION_TEXT_H, Math.round(BTN.h * ACTION_RING));
    const action = node(
      `Act_${i}`,
      [0, 0],
      [],
      [
        body,
        node(
          "Label",
          [0, 0],
          [
            loading
              ? { loadingRing: true, w_: ring, h_: ring, pivot: 4, depth: 8, color: ink, fallback: caption_ }
              : caption_,
          ],
        ),
      ],
    );
    action.actionSemantic = disabled ? "secondary" : semantic;
    kids.push(action);
  });
  if (actions.length) bands.push({ name: "Actions", w, h: actionsH });
  const placed = layoutStack(bands, { x: content.x, y: content.y, w: content.w, gap: SPACE.m });
  // The actions stand on the panel's bottom, their solid art level with the list panel's (every
  // panel).
  const pinned = actions.length && placed.h <= content.h;
  if (pinned) placed.boxes.at(-1).y = content.y + content.h - actionsH + BTN.h * ACTION_SHADOW;
  const tree = node("Details", [0, 0], [], kids);
  const placeInfo = (box, x = 0) => {
    const target = { ...box, x: x + box.x };
    if (box.name === "Fields") {
      const [px, py] = toNode(target, 1, frame);
      for (const part of fieldBox.nodes) {
        part.pos[0] += px;
        part.pos[1] += py;
      }
    } else if (box.name === "Chips")
      chips.forEach((_, i) => {
        find(tree, `Chip_${i}`).pos = toNode(
          {
            x: target.x + infoW / 2 + places[i].x - chipW[i] / 2,
            y: target.y + places[i].row * (chip.h + SPACE.s),
            w: chipW[i],
            h: chip.h,
          },
          4,
          frame,
        );
      });
    else if (box.name === "Head") {
      // the name, its pencil and (beside) the chips as one centred group
      const parts = [
        { w: beside ? nameW : nameRoom, h: name.h },
        ...(it.nameEdit ? [{ w: CONTROL.icon.h, h: CONTROL.icon.h }] : []),
        ...(beside ? chipW.map((cw) => ({ w: cw, h: chip.h })) : []),
      ];
      const gaps = parts.map((_, i) =>
        i === 0
          ? 0
          : beside && i === (it.nameEdit ? 2 : 1)
            ? SPACE.l
            : i === 1 && it.nameEdit
              ? SPACE.m
              : DETAILS.chipGap,
      );
      const total = parts.reduce((n, p, i) => n + p.w + gaps[i], 0) + (beside ? chip.h * TAN10 : 0);
      let left = target.x + (infoW - total) / 2;
      parts.forEach((p, i) => {
        left += gaps[i];
        const at = toNode({ x: left, y: target.y + (target.h - p.h) / 2, w: p.w, h: p.h }, 4, frame);
        if (i === 0) find(tree, "Name").pos = at;
        else if (i === 1 && it.nameEdit) find(tree, "EditName").pos = at;
        else find(tree, `Chip_${i - 1 - (it.nameEdit ? 1 : 0)}`).pos = at;
        left += p.w;
      });
    } else find(tree, box.name).pos = toNode(target, 4, frame);
  };
  for (const box of placed.boxes) {
    if (box.name === "Portrait") {
      const portraitRow = row(
        [
          { w: pictureColumn.w, h: pictureH },
          { w: infoW, h: infoStack.h },
        ],
        { x: 0, y: box.y, w, gap: SPACE.xl, align: "center" },
      ).boxes;
      picture.pos = toNode(portraitRow[0], 4, frame);
      // the name, chips and fields centred down the picture's height
      const top = portraitRow[1].y - box.y;
      for (const part of infoStack.boxes) placeInfo({ ...part, y: part.y + box.y + top }, infoColumn.x);
    } else if (box.name === "Actions")
      actionRow.boxes.forEach((action, i) => {
        find(tree, `Act_${i}`).pos = toNode({ ...action, y: box.y + action.row * (BTN.h + gap) }, 4, frame);
      });
    else if (["Title_Bg", "Picture"].includes(box.name)) find(tree, box.name).pos = toNode(box, 4, frame);
    else placeInfo(box);
  }
  tree.contentHeight = pinned ? content.h : placed.h;
  tree.contentLayout = placed;
  tree.more = more;
  return tree;
}
