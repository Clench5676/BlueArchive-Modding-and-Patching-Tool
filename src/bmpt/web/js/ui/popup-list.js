// The list popup: the list of cards with the student list's tools, its frame and paper, the cards, and drawing it
// (listPopup); the details panel beside the list is popup-details.js.
import * as NGUI from "./ngui.js";
import { attachHint } from "./hover-hint.js";
import { switchGrid, placeSwitchCards } from "../mods/switch-layout.js";
import * as Buttons from "./game-buttons.js";
import { searchTree, pickerItems, toolbarFit, toolbarTree, TOOLBAR_CENTRE_Y } from "../roster/roster-widgets.js";
import { selectedFrame, selectionArtWindow } from "./selected-frames.js";
import { T } from "../core/i18n.js";
import { frameGeometry, popupContent } from "./popup-frames.js";
import { COLOUR, CONTROL, POPUP_FOOTER, POPUP_FRAME, SPACE } from "./ui-tokens.js";
import { DOT, RECRUIT, S, TAN10, glyphsOf, hitButton, markedLabel } from "../core/util.js";
import { container, grid, row, toNode } from "./ui-grid.js";
import {
  find,
  node,
  sprite,
  label,
  fitText,
  tagRow,
  hostOf,
  layouts,
  selectionSlot,
  selectionSprite,
  selectionOutline,
  selectionReady,
  selectionCaptions,
  ready,
  nodeRect,
  hit,
  popupFrame,
  closable,
  scrollArea,
} from "./popup.js";
import { FOOTER_DEFAULT } from "./popup-footer.js";
import { CHECK_H, checkboxPrefab, checkbox, wireCheck } from "./popup-checkbox.js";
import { detailsTree } from "./popup-details.js";
import { message } from "./popup-message.js";

// --- the list popup --- the details' columns of the L class's twelve (the list has the rest), and the list's
// paper width: its columns less the panel's padding (listCardBox's)
const LIST_SPLIT = 4.5;
const LIST_PAPER_W =
  grid(container({ w: POPUP_FRAME.L.w, h: 0, padding: [0, SPACE.xl] }).inner).span(12 - LIST_SPLIT, LIST_SPLIT).w -
  2 * SPACE.m;
export const LIST_CARD_NAME = Object.freeze({ fs: 34, fsMin: 24, lines: 2, padY: 22, pitch: 1.2 });
/**
 * The geometry of a list popup: its frame and content box, the details panel at the left and the list panel at
 * the right, and the card size and pitch.
 * @param {boolean} [check] Whether a checkbox sits below the details.
 * @param {string|null} [kind] The kind of cards the list shows.
 * @returns
 *     {{frame: PopupFrame, content: Box, left: {l: number, r: number, t: number, b: number}, list: {l: number, r: number, t: number, b: number}, card: {w: number, h: number, bar: number}, pitch: number[]}}
 *     The frames, the panels' bounds and the cards' geometry.
 */
export function listLayout(check = false, kind = null) {
  // the card as the list's paper holds it (the L class's eight columns less the paper's padding)
  const native = switchGrid(kind, LIST_PAPER_W).card;
  // One complete native card, its name band and the toolbar determine the minimum useful viewport. Additional
  // cards scroll.
  const cardHeight =
    native.h +
    Math.max(native.bar, LIST_CARD_NAME.padY + LIST_CARD_NAME.lines * LIST_CARD_NAME.fs * LIST_CARD_NAME.pitch);
  const need = POPUP_FRAME.contentTop + CONTROL.buttonS.h + 5 * SPACE.m + cardHeight + POPUP_FOOTER.clearance;
  // every list popup has one height, the Hall's editors'
  const frame = frameGeometry("XL", Math.max(need, POPUP_FRAME.panelH));
  const content = popupContent(frame.w, frame.h);
  const panels = grid(content);
  // the details four and a half columns, the list seven and a half (the details' share smaller
  // than five; four leaves their actions and chips no room)
  const detailBox = { ...panels.span(LIST_SPLIT), h: content.h - (check ? CHECK_H + SPACE.m : 0) };
  const bounds = (box) => {
    const [l, t] = toNode(box, 0, frame);
    return { l, r: l + box.w, t, b: t - box.h };
  };
  return {
    frame,
    content,
    left: bounds(detailBox),
    list: bounds(panels.span(12 - LIST_SPLIT, LIST_SPLIT)),
    // Card artwork and the native card-placement helper retain their prefab envelope and scroll pitch.
    card: { w: 250, h: 226, bar: 68 },
    pitch: [284, 350],
  };
}
// the band over a list's paper: the toolbar's height, or a slimmer one (``slim``) that only holds the footer's count
// The list popups' toolbar: the roster's, one size down ("太高了，弄矮一些，再弄小一档"), its 116-unit tools 93 high
export const LIST_TOOLBAR = Object.freeze({ scale: 0.8 });
export const LIST_BAND = Object.freeze({ tools: Math.round(116 * LIST_TOOLBAR.scale), slim: 60 });
/**
 * The box the list's cards are placed in, inside the drawn paper.
 * @param {ReturnType<typeof listLayout>} layout
 * @param {{searching?: boolean, horizontal?: boolean, band?: number}} [options] The band over the paper
 *     (LIST_BAND) and the scroll direction.
 */
export function listCardBox(layout, { searching = false, horizontal = false, band = LIST_BAND.tools } = {}) {
  const b = layout.list,
    f = layout.frame;
  const panel = { x: b.l + f.w / 2, y: f.cy + f.h / 2 - b.t, w: b.r - b.l, h: b.t - b.b };
  // the drawn paper SPACE.m under the toolbar's band (SPACE.m above it too), the cards' box SPACE.m inside it
  const paper = container({
    ...panel,
    padding: [
      band + 3 * SPACE.m + (searching && !horizontal ? CONTROL.buttonS.h + SPACE.m : 0),
      SPACE.m,
      SPACE.m,
      SPACE.m,
    ],
  }).inner;
  // The grid owns the card viewport; native card envelopes and their un-stretched pitch are placed inside it.
  return grid(paper).span(12);
}
export const LP = listLayout();
// a segment's caption: a string key, or {text} for a caption the caller wrote (a count in it)
const segText = (seg) => (typeof seg === "string" ? T(seg) : (seg?.text ?? ""));
// the student list's tools at the top right of the list: order, sort, search and, with segs, the segment filter; check:
// the roster's checkbox before them. One toolbar, the roster's, fitted by its own steps to the list's width (the
// list popups are the L class, too narrow for the prefab's 230-wide segments and 420-wide sort button).
function toolbar(
  right,
  y,
  {
    segs = null,
    sortKey = "sort.default",
    active = 0,
    ascending = true,
    filtered = false,
    toggle = null,
    sortKeys = [sortKey],
  } = {},
) {
  return fittedToolbar(right, y, { segs, sortKey, sortKeys, active, ascending, filtered, toggle });
}
// the icon of a list's toggle in the toolbar (Show Other Students; one of the game's icons)
export const TOGGLE_ICON = Object.freeze({ sprite: "Common_Charlist_Icon_ViewSmall", atlas: "Common", size: [57, 57] });

/**
 * Fit the list toolbar with a checkbox: the checkbox's label, the captions of the segments and the sort
 * button's keys, between the list's left edge and a right edge: the segments at the left, the icons at the
 * right.
 * @param {number} right The toolbar's right edge.
 * @param
 *     {{segs?: (string|{text: string})[], sortKey: string, sortKeys: string[], label?: string|null, left?: number, icons?: number}}
 *     options segs: the segment captions' keys; sortKey: the one shown; sortKeys: all the sort button's keys.
 * @returns {object} The toolbar's fit, as roster-widgets.js toolbarFit gives it.
 */
export function checkedToolbarFit(
  right,
  { segs, sortKey, sortKeys, label = null, left = LP.list.l + SPACE.m, icons = 0 },
) {
  return toolbarFit({
    left,
    right,
    filters: segs ? [segs.map(segText)] : [],
    sorts: sortKeys.map((key) => T(key)),
    sortAt: sortKeys.indexOf(sortKey),
    check: label,
    icons,
    spread: true,
  });
}
// The toolbar with a checkbox (Switch 3D Model's "Show other students") is the student roster's: the checkbox,
// the three segments, search, sort and order, fitted by the roster's own steps (roster-widgets.js toolbarFit;
// the sort button as wide as its longest key). Returns the tree, where the segments and the checkbox stand for
// the hit areas. It is drawn at LIST_TOOLBAR.scale with its right edge at `right`, fitted to the list's width
// over that scale. toggle: {label, checked, onChange} for an icon toggle before search (TOGGLE_ICON).
function fittedToolbar(right, y, { segs, sortKey, sortKeys, active, ascending, filtered, toggle }) {
  const k = LIST_TOOLBAR.scale,
    px = right * (1 - k),
    at = (x) => px + k * x;
  const lay = checkedToolbarFit(right, {
    segs,
    sortKey,
    sortKeys,
    left: (LP.list.l + SPACE.m - px) / k,
    icons: toggle ? 1 : 0,
  });
  const tree = toolbarTree(layouts.collection, layouts.checkbox || checkboxPrefab(), {}, false, lay, {
    filters: segs ? [{ name: "ModFilter", active }] : [],
    filtered,
    ascending,
    toggles: toggle ? [{ name: "ToggleBtn", ...TOGGLE_ICON, pressed: !!toggle.checked }] : [],
  });
  tree.name = "Toolbar";
  tree.scale = [k, k];
  tree.pos = [px, y - k * TOOLBAR_CENTRE_Y];
  tree.children = tree.children.filter((child) =>
    ["ModFilter", "SearchBtn", "SortBtn", "OrderBtn", "ToggleBtn"].includes(child.name),
  );
  // the segments span the filter's right edge back three widths (stage units, at the toolbar's scale)
  const icon = lay.icons?.[0];
  return {
    tree,
    lay,
    segBox: segs ? { right: at(lay.seg1 + 6), segW: k * lay.segW, y, scale: k } : null,
    toggle: toggle && icon ? { x: at(icon.x + icon.w / 2), w: k * icon.w, h: k * icon.h, y } : null,
  };
}

// The list and details panels occupy the XL content grid; its footer uses the shared centred group. The list
// popups' footer is the XS every popup has; footer: another size for one list.
export const LIST_FOOTER = FOOTER_DEFAULT;
/**
 * Build the popup frame of a list popup: the title, the buttons at the footer's bottom right, the details
 * panel's and the list's panels and the paper.
 * @param {{label: string, fn?: Function, primary?: boolean, greyed?: boolean, action?: string}[]} buttons
 * @param {boolean} closeButton Whether the frame has a close button.
 * @param {boolean} [check] Whether a checkbox sits below the details.
 * @param {string|null} [kind] The kind of cards the list shows.
 * @param {number} [band] The band over the paper (LIST_BAND).
 * @returns {NguiNode} The popup's node tree.
 */
export function listFrame(
  title,
  buttons,
  closeButton,
  check = false,
  kind = null,
  band = LIST_BAND.tools,
  footer = LIST_FOOTER,
) {
  const layout = listLayout(check, kind);
  // the footer at the frame's bottom right
  const t = popupFrame(title, layout.frame.w, layout.frame.h, {
    close: closeButton,
    footer,
    align: "right",
    buttons: buttons.map((b, i) => ({ ...b, name: `Btn_${i}` })),
  });
  const popup = find(t, "Popup"),
    btn = find(popup, "Btn");
  btn.children = btn.children.map((child, i) => (buttons[i].greyed ? Buttons.disable(child) : child));
  const panel = (name, b, tone) =>
    node(
      name,
      [(b.l + b.r) / 2, (b.t + b.b) / 2],
      [sprite("Common_Bg_Raius10px", b.r - b.l, b.t - b.b, { depth: 3, color: tone })],
    );
  const b = layout.list;
  const paper = container({
    x: b.l,
    y: -b.t,
    w: b.r - b.l,
    h: b.t - b.b,
    padding: [band + 2 * SPACE.m, SPACE.m, SPACE.m, SPACE.m],
  }).inner; // SPACE.m above and below the toolbar
  popup.children.push(
    panel("LeftPanel", layout.left, "#ffffff/0.00"),
    panel("ListPanel", b, "#d7eaf4/1.00"),
    node(
      "ListPaper",
      [paper.x + paper.w / 2, -paper.y - paper.h / 2],
      [sprite("Common_Bg_Raius10px", paper.w, paper.h, { depth: 4, color: "#f4fcfe/1.00" })],
    ),
  );
  return t;
}

// The tick box at a list card's top right corner, in units; inset is its distance from the corner.
export const TICK_BOX = Object.freeze({ w: 60, h: 58, icon: 48, inset: 42 });
// The gap between a card's top badge and the tick box.
export const BADGE_GAP = 8;
/**
 * The widest a card's top badge may be beside the tick box, BADGE_GAP short of it (both slant alike: their
 * edges are compared without the slant, which corner and tickPlace add by height).
 * @param {{w: number, h: number}} card
 * @returns {number} The width in units.
 */
export const badgeRoom = (card) => {
  const [x, y] = tickPlace(card);
  return x - y * TAN10 - TICK_BOX.w / 2 - BADGE_GAP - (-card.w / 2 + 12);
};
/**
 * The centre of a card's tick box.
 * @param {{w: number, h: number}} card
 * @returns {number[]} [x, y] from the card's centre, y up.
 */
export const tickPlace = (card) => [
  card.w / 2 - TICK_BOX.inset + (card.h / 2 - TICK_BOX.inset) * TAN10,
  card.h / 2 - TICK_BOX.inset,
];
/**
 * Draw one card of a list: the game's card body, the picture clipped to its window, a name bar under it.
 * A card that cannot be chosen (dim) is darkened like the roster's not-owned cards, its picture without colour;
 * its badge (In Use, or the caller's) stays bright.
 * @param {ListItem} it
 * @param {{w: number, h: number, bar: number}} [card] The card's size and its name bar's height.
 * @param {string|null} [kind] The kind of cards the list shows.
 * @param {boolean|null} [tick] Null, or whether the card is ticked, for a list whose cards are ticked apart
 *     from being shown (listPopup tick): a check box at the card's top right corner, the selection frame then
 *     without its check (it marks the card the details show).
 * @returns {NguiNode}
 */
export function listCard(it, x, y, selected, card = LP.card, kind = null, tick = null) {
  const { w, h, bar } = card;
  // The same upright image must reach both inclined edges even in a tall portrait window.
  const overscan = Math.max(0.15, ((h - 26) * TAN10) / (2 * (w - 16)));
  const kids = [
    node(
      "Thumb",
      [1, 4],
      [
        {
          clipImage: it.thumb,
          placeholder: T("import.previewUnavailable"),
          w_: w - 16,
          h_: h - 26,
          pivot: 4,
          depth: 8,
          incline: 10,
          radius: 18,
          color: "#ffffff/1.00",
          imgSize: kind === "chibi" ? "contain" : "cover",
          imgPosition: "center 30%",
          imgLeft: `${-overscan * 100}%`,
          imgWidth: `${(1 + 2 * overscan) * 100}%`,
        },
      ],
    ),
    node(
      "Line",
      [1, 4],
      [sprite("Common_Line_Raius20px", w - 12, h - 18, { incline: 10, depth: 9, color: "#ffffff/1.00" })],
    ),
  ];
  const lbl = markedLabel(it.label, {
    fs: LIST_CARD_NAME.fs,
    fsMin: LIST_CARD_NAME.fsMin,
    color: "#ffffff/1.00",
    maxW: w - 56 - (it.nameEdit ? 60 : 0),
    mark: false,
    maxLines: LIST_CARD_NAME.lines,
  });
  const barH = Math.max(bar, LIST_CARD_NAME.padY + lbl.lines * lbl.fs * LIST_CARD_NAME.pitch),
    barY = -h / 2 - 10 - barH / 2;
  kids.push(
    node(
      "Bar",
      [barY * TAN10 + 4, barY],
      [sprite("Common_Bg_Raius10px", w - 22, barH, { incline: 10, depth: 8, color: "#2c4564/1.00" })],
      [
        node("Name", [it.nameEdit ? -30 : 0, 0], [], lbl.nodes),
        ...(it.nameEdit
          ? [node("EditName", [w / 2 - 50, 0], [sprite("Cafe_Icon_NameEdit", 42, 42, { type: 0, depth: 12 })])]
          : []),
      ],
    ),
  );
  const corner = (dx, dy) => [dx + dy * TAN10, dy]; // a point inside the slanted card
  if (it.dim)
    kids.push(
      node(
        "Dim",
        [1, 4],
        [sprite("Common_Bg_Raius10px", w - 12, h - 18, { incline: 10, depth: 24, color: "#000000/0.55" })],
      ),
    );
  const badge = it.badge || (it.inUse ? { text: T("popup.inUse"), color: COLOUR.inUse, ink: COLOUR.inUseInk } : null);
  const place = (name, mark, x, bf, bw, y) =>
    kids.push(
      node(
        name,
        corner(x + bw / 2, y),
        [sprite("Common_Bg_Raius10px", bw, 42, { depth: 26, color: `${mark.color}/1.00`, incline: 10 })],
        [
          node(
            "Text",
            [0, 0],
            [label(bf.text, bf.fs, `${mark.ink || COLOUR.inUseInk}/1.00`, bw - 4, 40, { depth: 27 })],
          ),
        ],
      ),
    );
  if (badge) {
    // as wide as its word needs, from the corner, up to most of the card; with a tick box in the corner, up to the box
    const room = tick === null ? w - 46 : badgeRoom(card);
    const bf = fitText(badge.text, 26, 20, room - 24, 1);
    place("Using", badge, -w / 2 + 12, bf, Math.min(room, Math.max(116, glyphsOf(bf.text, bf.fs) + 24)), h / 2 - 30);
  }
  // the bottom row: the tag, then the marks (Updatable), after the recruit icon and before the pin
  tagRow(it, w).forEach(({ mark, x, bf, bw }, i) => place(i ? `Mark${i}` : "Tag", mark, x, bf, bw, -h / 2 + 36));
  if (it.fav)
    kids.push(
      node("Pin", corner(w / 2 - 38, -h / 2 + 36), [sprite("Common_Btn_Pin_01", 54, 54, { type: 0, depth: 26 })]),
    );
  if (it.recruit)
    kids.push(
      node("Recruit", corner(-w / 2 + 34, -h / 2 + 40), [
        sprite(RECRUIT.sprite, 62 / RECRUIT.ratio, 62, { atlas: RECRUIT.atlas, type: 0, depth: 26 }),
      ]),
    );
  // In-use mods already have a badge; a picked card's check takes the corner.
  if (it.isNew && !it.inUse && !it.applied && !selected) kids.push(DOT(corner(w / 2 - 4, h / 2 - 6), 34));
  if (selected)
    kids.push(
      node(
        "Select",
        [1, 4],
        [sprite("Card_Ability_Select_Line", w + 4, h - 8, { incline: 10, depth: 20 })],
        tick === null
          ? [
              node(
                "Check",
                [w / 2 - 28 + (h / 2 - 30) * TAN10, h / 2 - 30],
                [sprite("Card_Ability_Select_Line_Icon", 66, 65, { type: 0, depth: 21 })],
              ),
            ]
          : [],
      ),
    );
  // the tick box, as the checkbox prefab draws one (Btn_Checkbox: plate, line, check), in the corner
  if (tick !== null)
    kids.push(
      node(
        "Tick",
        tickPlace(card),
        [sprite("Common_Bg_Raius10px", TICK_BOX.w, TICK_BOX.h, { incline: 10, depth: 25, color: "#e4f3f8/1.00" })],
        [
          node(
            "Line",
            [0, 0],
            [
              sprite("Common_Line_Raius10px", TICK_BOX.w, TICK_BOX.h, {
                incline: 10,
                depth: 26,
                color: "#96b6c2/1.00",
              }),
            ],
          ),
          ...(tick
            ? [node("Icon", [0, -1], [sprite("Common_Icon_Check_B", TICK_BOX.icon, TICK_BOX.icon - 1, { depth: 27 })])]
            : []),
        ],
      ),
    );
  const tree = node(
    `Card_${it.key}`,
    [x, y],
    [sprite("Common_Btn_BG", w, h, { incline: 10, depth: 7, color: "#ffffff/1.00" })],
    kids,
  );
  if (typeof it.applied === "boolean") {
    // layoutChildren keeps the complete set of children (the selection included) for the card's measured envelope,
    // hit boxes and scroll pitch.
    tree.layoutChildren = [...kids];
    tree.children = kids.filter((n) => n.name !== "Select");
    // frameCaption false: the frame alone, no Applied word in it (the Hall's chosen background)
    const caption =
      it.frameCaption !== false && (it.applied || selected)
        ? selectionCaptions(card)?.[it.applied ? "applied" : "preview"]
        : null;
    const frame = selectedFrame(selectionSlot, card, it.applied, selected, caption);
    if (frame) {
      const window = selectionArtWindow(frame, selectionSprite, selectionOutline);
      const source = find(tree, "Thumb"),
        image = { ...source.widgets[0] };
      const thumb = { ...source, pos: [window.x, window.y], widgets: [image] };
      // The layout envelope stays; only the art underneath the selected frame changes.
      Object.assign(image, { w_: window.width, h_: window.height, radius: 0 });
      const overscan = Math.max(0.15, (window.height * TAN10) / (2 * window.width));
      Object.assign(image, { imgLeft: `${-overscan * 100}%`, imgWidth: `${(1 + 2 * overscan) * 100}%` });
      tree.children = tree.children.filter((n) => n.name !== "Line").map((n) => (n === source ? thumb : n));
      tree.children.push(frame);
    }
  }
  return tree;
}

/**
 * A card of a list popup.
 * @typedef {object} ListItem
 * @property {string} key The card's key.
 * @property {string} label The name on the card.
 * @property {string} [full] The full name, shown in the details panel.
 * @property {string|Promise<string>} [thumb] The card's picture.
 * @property {string|(() => string)} [big] The details panel's picture, or a function that gives it.
 * @property {boolean} [recruit] The card is recruitable: shows the recruit icon.
 * @property {string} [kind] "mod" for an added mod.
 * @property {boolean} [inUse] The card is the one in use.
 * @property {string} [inUseText] A saved source's caption in the detail chip, independent of whether its files are in
 *     use.
 * @property {boolean} [fav] The card is a favourite: shows the pin.
 * @property {boolean} [isNew] Shows the new dot.
 * @property {number} [size] The size in bytes, for the footer's total of the ticked cards.
 * @property {boolean} [disabled] Cannot be ticked (with multi).
 * @property {boolean} [dim] The darkened look.
 * @property {boolean} [applied] Draws the selection frame; true for the applied card.
 * @property {boolean} [frameCaption] False: the frame alone, no Applied word in it (the Hall's chosen background).
 * @property {{text: string, color: string, ink: string}} [badge] The badge at the card's top.
 * @property {{text: string, color: string, ink: string}} [tag] The first tag of the card's bottom row.
 * @property {object[]} [marks] The marks after the tag (Updatable).
 * @property {object[]} [chips] The chips of the details panel.
 * @property {string} [original] A secondary name in the detail band.
 * @property {{label: string, fn: (scroll: number) => void}} [nameEdit] The name's edit button.
 * @property {string} [hint] The card's hover hint.
 */

/**
 * The student list's tools over the cards of a list popup.
 * @typedef {object} ListTools
 * @property {(string|{text: string})[]} [segs] The segment filter's captions: a string key, or {text} for a caption
 *     the caller wrote.
 * @property {number|string} [seg] The chosen segment.
 * @property {(seg: number|string) => void} [onSeg] A segment was chosen.
 * @property {string} [sortKey] The sort shown.
 * @property {(query: string) => ListItem[]} [more] Cards added after the matching ones (Switch 3D Model's other
 *     students' models), asked on every drawing with the search's words, which may be empty; ordered and turned like
 *     the others.
 * @property {(keys: string[]) => void} [onView] The keys of the cards in view, after every drawing and scroll (the
 *     same signal that brings their thumbnails in).
 * @property {{label: string, checked: boolean, onChange: (checked: boolean) => void}} [toggle] An icon toggle
 *     (TOGGLE_ICON) before search, its label the hover hint; a click flips toggle.checked and draws again.
 */

/**
 * A button under a list popup's cards.
 * @typedef {{label: string, fn: () => void, primary?: boolean, greyed?: boolean, action?: string}} ListButton
 */

/**
 * A checkbox below a list popup's details panel.
 * @typedef {{label: string, checked: boolean, onChange?: (checked: boolean) => void}} ListCheck
 */

/**
 * The details panel a list popup shows for one card.
 * @typedef {object} ListDetails
 * @property {string} title The panel's title.
 * @property {Array<Array<string|null>>} fields The rows: [caption, value, colour].
 * @property {Array<Array<unknown>>} actions The buttons: [caption, fn, disabled, semantic, hint, loading].
 * @property {boolean} [truncate] Each value on one line, a "..." button showing a cut one whole.
 */

/**
 * Options of listPopup().
 * @typedef {object} ListPopupOptions
 * @property {HTMLElement|string} [host] The element, or its id, the popup is drawn in.
 * @property {string} [hostId] The host's id, when host is not given.
 * @property {string} title The title.
 * @property {ListItem[]} items The cards.
 * @property {string|string[]|Set<string>|null} [selected] The chosen key; with multi, the ticked keys
 *     (selectable(items) ticks all).
 * @property {boolean} [multi] Several cards are ticked.
 * @property {string|null} [focus] With multi, the card whose details show (by default the first ticked one).
 * @property {(key: string, scroll: number) => void} onSelect A card was clicked (the caller ticks it, toggled(), or
 *     chooses it, and draws the popup again).
 * @property {ListButton[]|null} [buttons] The buttons under the list; or okText and onOk for the one confirm button.
 * @property {string} [okText] The confirm button's label.
 * @property {() => void} [onOk] The confirm button's handler.
 * @property {string} [cancelText] A bright blue Cancel before the primary button (the switch popups).
 * @property {string} [okSemantic] The confirm button's semantic, "confirm" by default.
 * @property {((how: string) => void)|null} [onClose] The close button, the backdrop and Escape; null for a popup
 *     without them (it must be answered).
 * @property {ListTools|null} [tools] The student list's tools over the cards; without them, hint (left) and footer
 *     (right) show in their place.
 * @property {string} [hint] The line over the paper's left.
 * @property {string} [footer] The line over the paper's right (the ticked cards, their size); the footer buttons are
 *     LIST_FOOTER's size.
 * @property {ListCheck|null} [check] A checkbox below the details; a click flips check.checked, so the same object is
 *     passed to every drawing.
 * @property {(it: ListItem) => ListDetails} details The panel on the left for a card: fields as [caption, value, colour],
 *     actions as [caption, fn, disabled, semantic, hint, loading]; a loading action (disabled while its check runs)
 *     shows the loading ring in its caption's place; truncate: each value on one line, a "..." button showing a cut
 *     one whole.
 * @property {number} [keepScroll] The list's offset on its scrolling axis (horizontal for portraits).
 * @property {{keys: string[], onTick: (key: string, scroll: number) => void}|null} [tick] Cards ticked apart from
 *     being shown: a click on a card shows it in the details (onSelect, selected), a click on its corner's tick box
 *     ticks or unticks it (onTick).
 * @property {string|null} [empty] What an empty list says, in the middle of its paper (hall.noCandidates by default).
 *     A list with no card to show puts it there; with tools, while the chosen card (selected) is not among the cards
 *     shown, the primary button is the game's disabled one and answers nothing.
 * @property {string} [layoutKind] The kind of cards the list lays out ("portrait", "lobby", "model", ...).
 * @property {(it: ListItem) => string} [detailKind] The kind whose details panel a card shows when it differs from
 *     layoutKind's ("portrait": the portrait panel, an upright picture beside the stacked fields), for lists of several
 *     kinds.
 * @property {boolean} [alignStart] The generic grid's cards start at the paper's left edge, as the kinds' grids place
 *     them.
 * @property {boolean} [slimBand] Without tools and hint, the band over the paper is only as high as the footer's count.
 */

const toolStates = new WeakMap();
const tokens = new Map(); // host -> the latest drawing's number: an older one that ends later is dropped
/**
 * Draw a list popup in a host. The caller draws it again on every change (a card picked, a segment).
 * @param {ListPopupOptions} options What the popup shows and how it answers.
 * @returns {Promise<void>} Settles when the popup is drawn.
 */
export async function listPopup(options) {
  let {
    host: hostArg,
    hostId,
    title,
    items,
    selected = null,
    multi = false,
    focus = null,
    onSelect,
    buttons = null,
    okText,
    onOk,
    onClose = null,
    tools = null,
    hint = "",
    footer = "",
    check = null,
    details,
    keepScroll = 0,
    tick = null,
    empty = null,
  } = options;
  const host = hostOf(hostArg ?? hostId);
  const my = (tokens.get(host) || 0) + 1;
  tokens.set(host, my);
  const reopening = host.hidden;
  host.hidden = false;
  await ready();
  let portraitTexture = null;
  if (items.some((it) => typeof it.applied === "boolean")) await selectionReady();
  const detailKind = options.detailKind || (() => options.layoutKind);
  if (options.layoutKind === "portrait") {
    const inventory = await NGUI.layout("UINonEquipmentInventory");
    portraitTexture = find(inventory, "Inventory_ListControl/EquipPos/Texture");
  } else if (options.detailKind && items.some((it) => options.detailKind(it) === "portrait")) {
    // a list of several kinds: without the prefab its portraits' details fall back to the generic panel
    const inventory = await NGUI.layout("UINonEquipmentInventory").catch(() => null);
    portraitTexture = inventory ? find(inventory, "Inventory_ListControl/EquipPos/Texture") : null;
  }
  const LP = listLayout(!!check, options.layoutKind);
  // the cards' columns follow the list's paper (the L class's narrower list holds fewer)
  const cardGrid = {
    ...switchGrid(options.layoutKind, listCardBox(LP).w),
    ...(options.alignStart ? { leftAligned: true } : {}),
  };
  const { card, columns, pitch, horizontal } = cardGrid;
  const band0 = options.slimBand && !tools && !hint ? LIST_BAND.slim : LIST_BAND.tools;
  const scrollKey = horizontal ? "scrollLeft" : "scrollTop";
  let toolState = toolStates.get(host);
  if (tools) {
    if (reopening || !toolState || host.dataset.pickerTitle !== title) {
      toolState = { query: "", sort: "default", ascending: true, searching: false };
      toolStates.set(host, toolState);
      host.dataset.pickerTitle = title;
    }
    items = pickerItems(items, toolState);
    if (tools.more) items = [...items, ...pickerItems(tools.more(toolState.query) || [], toolState)];
  }
  const redraw = () => listPopup({ ...options, keepScroll: 0 });
  const ticked = multi ? new Set(selected || []) : null;
  const isSelected = (key) => (multi ? ticked.has(key) : key === selected);
  // a search or segment that hides the chosen card: confirming would act on a card the user cannot see
  const filteredOut = !!tools && !multi && selected != null && !items.some((it) => it.key === selected);
  // cancelText: a bright blue Cancel before the primary button (the switch popups)
  const btns = (
    buttons || [
      ...(options.cancelText && onClose
        ? [{ label: options.cancelText, fn: () => onClose("cancel"), tone: "bright", action: "cancel" }]
        : []),
      { label: okText, fn: onOk, primary: true, semantic: options.okSemantic || "confirm", action: "ok" },
    ]
  ).map((b) => (b.primary && filteredOut ? { ...b, greyed: true, blocked: true } : b));
  const next = document.createElement("div");
  const view = await NGUI.render(listFrame(title, btns, !!onClose, !!check, options.layoutKind, band0), next, {
    scale: S,
    origin: [960, 540],
  });
  btns.forEach((b, i) => {
    const button = hit(next, view, `Center/Popup/Btn/Btn_${i}/BG (1)`, b.blocked ? () => {} : b.fn, {
      name: b.label,
      action: b.action || `button${i}`,
    });
    if (button && b.blocked) {
      button.disabled = true;
      button.setAttribute("aria-disabled", "true");
    }
  });
  if (onClose)
    hit(next, view, "Center/Popup/Base/BG/Btn_Close", () => onClose("close"), {
      name: T("display.close"),
      action: "close",
    });
  closable(view, host, onClose);
  const band = [];
  if (tools && layouts.collection && !(horizontal && toolState.searching)) {
    const sortKeys = [...new Set([tools.sortKey ?? "sort.default", "sort.name"])];
    // the toolbar centred in its band: SPACE.m above and below it
    const tb = toolbar(LP.list.r - SPACE.m, LP.list.t - SPACE.m - LIST_BAND.tools / 2, {
      segs: tools.segs,
      sortKey: toolState.sort === "name" ? "sort.name" : tools.sortKey,
      sortKeys,
      active: tools.seg ?? 0,
      ascending: toolState.ascending,
      filtered: !!toolState.query,
      toggle: tools.toggle,
    });
    const tv = await NGUI.render(tb.tree, next, { scale: S, origin: [960, 540], z: 20 });
    hit(
      next,
      tv,
      "Toolbar/OrderBtn/Btn_Normal",
      () => {
        toolState.ascending = !toolState.ascending;
        redraw();
      },
      { name: T(toolState.ascending ? "roster.asc" : "roster.desc"), action: "order" },
    );
    hit(
      next,
      tv,
      "Toolbar/SearchBtn/Btn_Normal",
      () => {
        toolState.searching = !toolState.searching;
        if (!toolState.searching) toolState.query = "";
        redraw();
      },
      { name: T("roster.search"), action: "search" },
    );
    hit(
      next,
      tv,
      "Toolbar/SortBtn/Btn_Normal",
      async () => {
        const Display = await import("../app/display.js");
        await Display.open({
          view: { sort: toolState.sort, order: toolState.ascending ? "asc" : "desc", filters: {} },
          count: () => items.length,
          sorts: ["default", "name"],
          sortOnly: true,
          onDone: (draft) => {
            if (!draft || host.hidden || tokens.get(host) !== my) return;
            toolState.sort = draft.sort;
            toolState.ascending = draft.order === "asc";
            redraw();
          },
        });
      },
      { name: T("display.tab.sort"), action: "sort" },
    );
    if (tb.segBox && tools.onSeg) {
      const { right, segW, y, scale: k } = tb.segBox,
        h = CONTROL.toolbar.h * k;
      for (let i = 0; i < 3; i++) {
        const ux = right - (3 - i) * segW + segW / 2 - 6 * k;
        hitButton(
          next,
          [960 + (ux - segW / 2) * S, 540 - (y + h / 2) * S, segW * S, h * S],
          () => tools.onSeg(i),
          segText(tools.segs[i]),
          80,
        );
      }
    }
    if (tb.toggle) {
      const { x, w, h, y } = tb.toggle,
        state = tools.toggle;
      const box = hitButton(
        next,
        [960 + (x - w / 2) * S, 540 - (y + h / 2) * S, w * S, h * S],
        () => {
          state.checked = !state.checked;
          state.onChange?.(state.checked);
          redraw();
        },
        state.label,
        80,
      );
      box.dataset.action = "toggle";
      box.setAttribute("aria-pressed", String(!!state.checked));
      attachHint(box, state.label);
    }
  } else {
    // the band over the cards: a hint at the left, the footer (the ticked cards, their size) at the right
    const room = LP.list.r - LP.list.l - 80;
    const ft = footer ? fitText(footer, 40, 28, room / 2, 1) : null;
    const ht = hint ? fitText(hint, 40, 28, room - (ft ? glyphsOf(ft.text, ft.fs) + 40 : 0), 2) : null;
    if (ht)
      band.push(
        node(
          "Hint",
          [LP.list.l + 40, LP.list.t - SPACE.m - band0 / 2],
          [label(ht.text, ht.fs, "#2d4664/1.00", room, 50 * ht.lines, { pivot: 3, depth: 8 })],
        ),
      );
    if (ft)
      band.push(
        node(
          "Footer",
          [LP.list.r - 40, LP.list.t - SPACE.m - band0 / 2],
          [label(ft.text, ft.fs, "#2d4664/1.00", room / 2, 50, { pivot: 5, depth: 8 })],
        ),
      );
  }
  let cb = null;
  if (check) {
    const checkW = LP.left.r - LP.left.l;
    cb = checkbox("Check", 0, 0, check.label, !!check.checked, checkW);
    const checkRow = row([{ w: cb.w, h: CHECK_H }], {
      x: LP.content.x,
      y: LP.content.y + LP.content.h - CHECK_H,
      w: checkW,
      gap: SPACE.m,
      justify: "center",
    });
    cb.tree.pos = toNode(checkRow.boxes[0], 3, LP.frame);
    band.push(cb.tree);
  }
  if (band.length) {
    const bv = await NGUI.render(node("Band", [0, 0], [], band), next, { scale: S, origin: [960, 540], z: 20 });
    if (cb) wireCheck(next, bv, "Band/Check", check, check.onChange);
  }
  let searchInput = null;
  if (tools && toolState.searching && layouts.collection) {
    const search = searchTree(layouts.collection);
    // Portraits need the paper's full height: search replaces their toolbar in the header.
    search.pos = [(LP.list.l + LP.list.r) / 2, LP.list.t - LIST_BAND.tools - SPACE.m - SPACE.xs];
    find(search, "SearchName").pos = [0, 0];
    if (horizontal) {
      const area = find(search, "SearchName/SearchNameArea"),
        w = area.widgets.find((w) => "sprite" in w);
      search.pos[1] =
        LP.list.t - SPACE.m - LIST_BAND.tools / 2 - area.pos[1] - (Math.floor((w.pivot ?? 4) / 3) / 2 - 0.5) * w.h_;
    }
    const sv = await NGUI.render(search, next, { scale: S, origin: [960, 540], z: 25 });
    const r = nodeRect(sv, `${search.name}/SearchName/SearchNameArea/InputField/Label_Search`);
    if (r) {
      searchInput = document.createElement("input");
      searchInput.className = "roster-search";
      Object.assign(searchInput.style, {
        left: `${r[0]}px`,
        top: `${r[1]}px`,
        width: `${r[2]}px`,
        height: `${r[3]}px`,
        fontSize: `${60 * S}px`,
        zIndex: 80,
      });
      searchInput.type = "text";
      searchInput.autocomplete = "off";
      searchInput.spellcheck = false;
      searchInput.placeholder = T("roster.searchHint");
      searchInput.value = toolState.query;
      searchInput.setAttribute("aria-label", T("roster.searchHint"));
      const apply = () => {
        toolState.query = searchInput.value;
        redraw();
      };
      searchInput.addEventListener("input", (ev) => {
        if (!ev.isComposing) apply();
      });
      searchInput.addEventListener("compositionend", apply);
      const closeSearch = () => {
        toolState.query = "";
        toolState.searching = false;
        redraw();
      };
      // Escape closes the search first, as the roster's does, and the popup stays; not the key that ends an input
      // method's conversion (keyCode 229 in WebKit)
      searchInput.addEventListener("keydown", (ev) => {
        if (ev.key !== "Escape" || ev.isComposing || ev.keyCode === 229) return;
        ev.preventDefault();
        closeSearch();
      });
      hit(next, sv, `${search.name}/SearchName/SearchNameArea/Btn_Close`, closeSearch, {
        name: T("display.close"),
        action: "search-close",
      });
      next.appendChild(searchInput);
    }
  }
  const cardBox = listCardBox(LP, { searching: tools && toolState.searching, horizontal, band: band0 });
  const [cardLeft, top] = toNode(cardBox, 0, LP.frame),
    bottom = top - cardBox.h;
  if (!items.length) {
    // nothing to show: the list paper says so, as the Hall's pickers do
    const room = LP.list.r - LP.list.l - 160,
      f = fitText(empty ?? T("hall.noCandidates"), 48, 32, room, 2);
    await NGUI.render(
      node(
        "Empty",
        [(LP.list.l + LP.list.r) / 2, (top + bottom) / 2],
        [label(f.text, f.fs, "#2d4664/1.00", room, f.fs * 1.3 * f.lines + 8, { depth: 8 })],
      ),
      next,
      { scale: S, origin: [960, 540], z: 20 },
    );
  }
  const [aL, aT] = [960 + cardLeft * S, 540 - top * S];
  // Measure the actual prefab widgets, including the name band's asymmetric incline and selection marks.
  const tickKeys = tick ? new Set(tick.keys || []) : null;
  const trees = items.map((it) =>
    listCard(it, 0, 0, isSelected(it.key), card, options.layoutKind, tickKeys ? tickKeys.has(it.key) : null),
  );
  const paper = { left: cardLeft, right: cardLeft + cardBox.w, top: top + SPACE.m };
  // every list is placed as if every card were selected, so choosing or ticking a card, or a segment that hides
  // the chosen one, never moves the cards (the list's padding is fixed whatever is ticked, and whatever is
  // chosen); a card's own marks (the selection's check, the new dot) stand over that envelope
  const shapes = items.map((it) => listCard(it, 0, 0, true, card, options.layoutKind, tickKeys ? true : null));
  // the envelopes where they stand, for the scroll extent and the cards in view: the same whatever is chosen
  const bounds = placeSwitchCards(shapes, cardGrid, paper);
  trees.forEach((tree, i) => {
    tree.pos = shapes[i].pos;
  });
  const rows = Math.ceil(items.length / columns),
    visH = (top - bottom) * S,
    visW = (paper.right - paper.left) * S;
  const contentH = horizontal ? visH : (rows * pitch[1] + 20) * S;
  const contentW = horizontal ? Math.max(visW, ...bounds.map((b) => (b.right - paper.left + 40) * S)) : visW;
  const inner = scrollArea(next, [aL, aT, visW, visH], contentH, { horizontal, contentW });
  await inner.ready;
  const origin = [960 - aL, 540 - aT];
  const scroll = () => inner.parentElement[scrollKey] || 0;
  const at = (i) => trees[i].pos;
  await Promise.all(
    items.map(async (it, i) => {
      const tree = trees[i];
      // a dim card's picture is wholly grey under a dark veil
      const styles = it.dim ? { [`Card_${it.key}/Thumb`]: "filter: grayscale(1)" } : undefined;
      const cv = await NGUI.render(tree, inner, { scale: S, origin, z: 30, styles });
      if (it.nameEdit)
        hit(inner, cv, `Card_${it.key}/Bar/EditName`, () => it.nameEdit.fn(scroll()), {
          name: it.nameEdit.label,
          action: `alias:${it.key}`,
        });
    }),
  );
  // the cards' buttons in the list's order, so the keyboard steps through them as they show
  items.forEach((it, i) => {
    const [x, y] = at(i),
      [sx, sy] = [origin[0] + x * S, origin[1] - y * S];
    const b = hitButton(
      inner,
      [sx - (card.w / 2) * S, sy - (card.h / 2) * S, card.w * S, (card.h + card.bar + 10) * S],
      () => onSelect(it.key, scroll()),
      it.full,
      60,
    );
    b.dataset.key = it.key;
    if (it.applied)
      b.setAttribute("aria-label", [it.full || it.label, T("status.applied")].filter(Boolean).join(" - "));
    if (it.hint) {
      attachHint(b, it.hint);
      b.setAttribute("aria-description", it.hint);
    }
    if (multi) {
      b.setAttribute("role", "checkbox");
      b.setAttribute("aria-checked", String(ticked.has(it.key)));
    }
    if (tickKeys) {
      const [tx, ty] = tickPlace(card),
        box = { w: TICK_BOX.w + 2 * SPACE.s, h: TICK_BOX.h + 2 * SPACE.s };
      const t = hitButton(
        inner,
        [sx + (tx - box.w / 2) * S, sy - (ty + box.h / 2) * S, box.w * S, box.h * S],
        () => tick.onTick(it.key, scroll()),
        it.full,
        61,
      );
      t.dataset.tick = it.key;
      t.setAttribute("role", "checkbox");
      t.setAttribute("aria-checked", String(tickKeys.has(it.key)));
    }
  });
  const shown = multi ? (focus ?? items.find((it) => ticked.has(it.key))?.key) : selected;
  const sel = items.find((it) => it.key === shown) || items[0];
  if (sel && details) {
    const d = details(sel);
    const kind = detailKind(sel);
    const tree = detailsTree(
      sel,
      d.title,
      d.fields,
      d.actions,
      kind === "portrait" ? portraitTexture : null,
      kind,
      LP.left,
      { truncate: !!d.truncate },
    );
    const panel = LP.left,
      panelH = panel.t - panel.b;
    const detailsHost =
      tree.contentHeight > panelH
        ? scrollArea(
            next,
            [960 + panel.l * S, 540 - panel.t * S, (panel.r - panel.l) * S, panelH * S],
            tree.contentHeight * S,
          )
        : next;
    await detailsHost.ready;
    const origin = detailsHost === next ? [960, 540] : [-panel.l * S, panel.t * S];
    const dv = await NGUI.render(tree, detailsHost, { scale: S, origin, z: 20 });
    if (sel.nameEdit)
      hit(detailsHost, dv, "Details/EditName", () => sel.nameEdit.fn(scroll()), {
        name: sel.nameEdit.label,
        action: "alias:details",
      });
    // a value cut to one line shows whole in a message over the list, titled with its caption
    for (const cut of tree.more) {
      const button = hit(
        detailsHost,
        dv,
        `Details/More_${cut.field}`,
        () => message({ title: cut.caption || d.title, lines: [cut.value], ok: T("picker.ok") }),
        { name: cut.caption || d.title, action: `more:${cut.field}` },
      );
      button?.setAttribute("aria-description", cut.value);
    }
    d.actions.forEach(([caption, fn, disabled, , hint, loading], i) => {
      if (!fn) return;
      const button = hit(detailsHost, dv, `Details/Act_${i}/Body`, disabled ? () => {} : fn, { name: caption });
      if (!button) return;
      if (loading) button.setAttribute("aria-busy", "true");
      // a greyed action with a reason keeps the pointer, so the reason shows on hover (the game's tooltip); an action's
      // own hint (what it covers) shows the same way
      if (disabled && hint) button.setAttribute("aria-disabled", "true");
      else button.disabled = !!disabled;
      if (hint) {
        attachHint(button, hint);
        button.setAttribute("aria-description", hint);
      }
    });
  }
  if (tokens.get(host) !== my || host.hidden) return;
  const area = inner.parentElement;
  host.replaceChildren(...next.childNodes);
  if (searchInput) {
    searchInput.focus?.();
    searchInput.setSelectionRange?.(searchInput.value.length, searchInput.value.length);
  }
  area[scrollKey] = keepScroll;
  // the thumbnails render in the order they were asked for: the cards in view go first, again as the list scrolls
  const bumpShown = () => {
    if (tokens.get(host) !== my || host.hidden) return;
    const offset = scroll(),
      inView = [];
    for (let i = items.length - 1; i >= 0; i--) {
      const b = bounds[i];
      const first = horizontal ? (b.left - paper.left) * S : (top - b.top) * S;
      const last = horizontal ? (b.right - paper.left) * S : (top - b.bottom) * S;
      if (last > offset && first < offset + (horizontal ? visW : visH)) {
        items[i].thumb?.bump?.();
        inView.unshift(items[i].key);
      }
    }
    tools?.onView?.(inView);
  };
  bumpShown();
  let waiting = false;
  area.addEventListener("scroll", () => {
    if (waiting) return;
    waiting = true;
    setTimeout(() => {
      waiting = false;
      bumpShown();
    }, 120);
  });
}
