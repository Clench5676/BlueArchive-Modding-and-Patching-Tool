// Batch Import's artwork is cloned from runtime game layouts. Only container dimensions and data slots adapt
// to the approved two-column screen; sprite slicing, inclines, decoration, colours and typography stay in NGUI.
import { countNodes } from "./mod-counts.js";
import { resizeButton } from "./button-layout.js";
import { clone } from "./util.js";
import { frameOutline, innerFrame } from "./row-thumbnail.js";
import { SPACE, TYPE, LINE, CONTROL, LIST_ROW, XS_BUTTON } from "./ui-tokens.js";
import { row as layoutRow, stack as layoutStack, textItem, controlWidth, toNode } from "./ui-grid.js";

const INVENTORY = "Inventory_ListControl";
const LIST = `${INVENTORY}/RightCanvas/ListBG`;
const DETAIL = `${INVENTORY}/Left_Info/EquipInfo/Equip_Target`;
const FILTER = "Center/Popup/Filter/ScrollView/Filter_List/Table_List/UIFilterObjectList_1";
const RADIO = "Center/Popup/Sort/Scroll View/Sort_Normal/Option_Normal/Grid/UISortObjectListUnit/Label_Text";
// One row of the list is the card (UIMailBoxContent/List_01, LIST_ROW: 2208 x 329 units) and ROW_PITCH (348, the Mailbox list's
// cell) down to the next, drawn at ROW_SCALE.
const ROW_PITCH = 348;
export const ROW_SCALE = 0.54, ROW_WIDTH = LIST_ROW.w * ROW_SCALE, ROW_HEIGHT = ROW_PITCH * ROW_SCALE;
// The row's one text size (TYPE.dense: the App's floor for dense list rows) and the top of its first text line, in units down
// from the card's centre (the App's own: it sets the prefab's gap under the divider, see row()).
export const ROW_TEXT_SIZE = TYPE.dense.fs, ROW_TEXT_TOP = 20;
export const ROW_CHECK_TOP = LIST_ROW.h * ROW_SCALE / 2 - ROW_TEXT_TOP * ROW_SCALE;
export const ROW_SELECTION = { left: 19, top: 8, width: 164, height: 160, scale: 0.68, origin: [82, 80] };
// Items/ListBG: its PaperBG and Inbox sit 60 units below the panel's centre, so that is the middle of the grey frame
// (header strip and toolbar included), in panel px.
export const PAPER_CENTRE = (S) => [636, 352 + 60 * S];
// The height of every action button here: Mailbox's Btn_Receive and ReceiveAll/Btn.
export const BUTTON_BODY_H = 162;
export const NAME_ROOM = 1160;    // the Batch Import name plate's room for a name (units)
export { EMPTY_GAP, emptyBlock } from "./empty-block.js";

// The columns of a row's text, in units from the card's centre line. The App's own (a dense two-column list; the prefab's text column
// is wider), except titleRight, which is the prefab's: where Label_System and the divider (Line) end.
const COLUMNS = Object.freeze({
  floor: -700,       // the text column starts here at the earliest
  titleRight: 926,
  right: 910,        // the right column: a badge or a control ends on it
  kindRight: 550,    // where a row without a badge (a result row) ends its metadata, clear of the row's action control
  kind: Object.freeze({ min: 200, max: 400 }),    // the kind column's width
  badgeMin: 330,     // a badge is this wide at least (1.5 x the prefab's 220)
  metaMin: 360,      // the metadata keeps this much of the text column beside the badge (owner, 2026-11-07: the badge never leaves the line);
                     // only a badge that would leave it less goes to its own line (no shipped status in any language does)
  counts: Object.freeze({ left: 120, list: 780, result: 500 }),    // a merged row's counts block: where it starts, and how wide
});
// NGUI's label box: a line of text takes this much of its size; a badge's label sits in a background SPACE.hair beyond it above and below
const LABEL_BOX = 1.3;
// a badge's label is this much narrower than its background (the sprite's slice border)
const BADGE_LABEL_INSET = 30;
// the gap under the divider, when no font metrics give the ink's
const FALLBACK_GAP = 2 * SPACE.xs;
const rowGap = (ui, layouts) => ROW_PITCH - ui.find(layouts.UIMailBoxContent, "List_01/BG").widgets.find((w) => "sprite" in w).h_;

// Include child widgets, their pivots and inclined overhangs in the selection frame's drawn edge.
function rightEdge(node, x = 0, sx = 1, sy = 1) {
  if (node.active === false) return -Infinity;
  // exported prefab nodes may omit empty widget and child lists
  const [px] = node.pos || [0, 0], [kx, ky] = node.scale || [1, 1];
  x += px * sx; sx *= kx; sy *= ky;
  return Math.max(...(node.widgets || []).filter((w) => "w_" in w).map((w) =>
    x + (1 - [0, 0.5, 1][(w.pivot ?? 4) % 3]) * w.w_ * sx
      + Math.abs(Math.tan((w.incline || 0) * Math.PI / 180) * w.h_ * sy / 2)),
  ...(node.children || []).map((child) => rightEdge(child, x, sx, sy)));
}

function selectionWidget(tree) {
  function visit(n, x = 0, y = 0, sx = 1, sy = 1) {
    if (n.active === false) return null;
    x += (n.pos?.[0] || 0) * sx; y += (n.pos?.[1] || 0) * sy;
    sx *= n.scale?.[0] ?? 1; sy *= n.scale?.[1] ?? 1;
    const w = n.widgets?.find((w) => w.sprite && w.w_ > 0 && w.h_ > 0);
    if (w) {
      return { w, x, y, sx, sy };
    }
    for (const child of n.children || []) { const found = visit(child, x, y, sx, sy); if (found) return found; }
    return null;
  }
  const result = visit(tree);
  if (!result) throw new Error("Import selection frame has no drawable widget");
  return result;
}

// The selected widget supplies size and position. The standard rounded popup
// line supplies the picture's small corners, independently of the selection glow.
export function selectionWindow(tree, atlas = {}, outline, pictureOutline = null) {
  const frame = selectionWidget(tree), ratio = ROW_SELECTION.scale / ROW_SCALE;
  const window = innerFrame(frame.w, atlas[frame.w.atlas || "Common"]?.[frame.w.sprite] || {}, outline,
    { ...frame, scale: ROW_SELECTION.scale });
  if (pictureOutline) {
    const rounded = innerFrame({ sprite: "Common_Line_Raius20px", type: 1, pivot: 4,
      w_: window.width, h_: window.height }, atlas.Common?.Common_Line_Raius20px || {}, pictureOutline,
    { scale: ROW_SELECTION.scale });
    window.clipPolygon = rounded.clipPolygon;
  }
  return { ...window,
    x: (ROW_SELECTION.left + ROW_SELECTION.origin[0] - ROW_WIDTH / 2) / ROW_SCALE + window.x * ratio,
    y: (LIST_ROW.h * ROW_SCALE / 2 - ROW_SELECTION.top - ROW_SELECTION.origin[1]) / ROW_SCALE + window.y * ratio,
    width: window.width * ratio, height: window.height * ratio };
}

export async function loadImportLayouts(ui) {
  // UICharacterCollection holds the game list's scrollbar (SeletControl/List/Scroll Bar) that the scrolling areas draw
  const names = ["UINonEquipmentInventory", "UIMailBox", "UIMailBoxContent", "UIPopup_FilterSort", "UINonEquipmentInventoryListItem",
    "UIPopup_GachaCharList.Btn_Checkbox", "UICharacterCollection"];
  const layouts = Object.fromEntries(await Promise.all(names.map(async (name) => [name, await ui.layout(name)])));
  const item = layouts.UINonEquipmentInventoryListItem;
  const frame = clone(ui.find(item, `${item.children[0].name}/Normal/Cover_Select`));
  frame.active = true; frame.pos = [0, 0];
  layouts.selectionOutline = frameOutline(await ui.spritePixels(selectionWidget(frame).w));
  layouts.pictureOutline = frameOutline(await ui.spritePixels({ sprite: "Common_Line_Raius20px", atlas: "Common" }));
  return layouts;
}

export function importArtwork(ui, layouts) {
  const get = (prefab, path) => {
    const n = path ? ui.find(layouts[prefab], path) : layouts[prefab];
    if (!n) throw new Error(`Import prefab node missing: ${prefab}/${path}`);
    const t = clone(n); t.active = true; return t;
  };
  const at = (n, x = 0, y = 0) => { n.pos = [x, y]; return n; };
  const label = (n, caption, width = null, lines = 1) => {
    const w = n.widgets.find((w) => "text" in w);
    const f = ui.fitText(caption, w.fontSize, w.fontSize * 0.7, width ?? w.w_, lines);
    Object.assign(w, { text: f.text, fontSize: f.fs, w_: width ?? w.w_, h_: Math.max(w.h_, f.fs * 1.3 * String(f.text).split("\n").length) });
    return n;
  };
  // the box of lines at NGUI's pitch, as drawn (the page font draws Latin larger, see ngui.js fontSizeFor)
  const lineBox = (text, fs) => String(text).split("\n").length * (ui.fontSizeFor?.(text, fs) ?? fs) * (ui.LINE_PITCH ?? 1.15);
  // How far (units, down) a label whose lines sit at the top of its box (pivot 1) has to move for the centre of its ink to be the
  // centre of the box. The lines fill the top of the box at NGUI's pitch; the ink of the script's reference glyph lies
  // NGUI.inkShift off the centre of those lines (one baseline for all text of a language). 0 without NGUI's metrics.
  const inkDrop = (w) => {
    if (typeof ui.inkShift !== "function") return 0;
    const px = ui.fontSizeFor?.(w.text, w.fontSize) ?? w.fontSize, lines = String(w.text).split("\n").length;
    return w.h_ / 2 - lines * px * (ui.LINE_PITCH ?? 1.15) / 2 + ui.inkShift(w.text, px, { reference: true });
  };
  // The Import State filter's caption (SortBtn/Btn_Normal/Type_2/Text, 336 wide, 56 down to 70 %): wrapped onto two lines
  // inside the button's body (456 x 116) before it shrinks, never ellipsized. The lines keep clear of the body's edges
  // round the label's own offset.
  const filterCaption = (sort, caption) => {
    const type = ui.find(sort, "Type_2"), text = ui.find(type, "Text"), w = text.widgets.find((x) => "text" in x);
    const body = sort.widgets.find((x) => "sprite" in x), offset = Math.abs((type.pos?.[1] || 0) + (text.pos?.[1] || 0));
    const [f] = ui.fitGroup([caption], w.fontSize, w.fontSize * 0.7, w.w_, 2, { maxH: body.h_ - 2 * offset });
    Object.assign(w, { text: f.text, fontSize: f.fs, h_: Math.max(w.h_, lineBox(f.text, f.fs)) });
  };
  // Items/Equip_Target/Label_Name (104, down to 70 %) on its dark plate (BG): the name takes up to two lines, at the largest
  // size that keeps their box inside the plate round the label's own centre (the plate is 204 high, the label sits 5 below
  // its middle), 1160 wide (the plate's width less the right margin the removed quantity tag left). A name no wrapping can
  // fit (a long file name has no word breaks) stays on one line at the floor and ends in an ellipsis.
  const namePlate = (t, name) => {
    const plate = ui.find(t, "BG"), node = ui.find(t, "Label_Name"), w = node.widgets.find((x) => "text" in x);
    const body = plate.widgets.find((x) => "sprite" in x), half = body.h_ * (plate.scale?.[1] ?? 1) / 2;
    const room = 2 * Math.min(node.pos[1] - (plate.pos[1] - half), plate.pos[1] + half - node.pos[1]);
    const [f] = ui.fitGroup([name], w.fontSize, w.fontSize * 0.7, NAME_ROOM, 2, { maxH: room });
    if (f.overflow) { Object.assign(w, { text: String(name), fontSize: w.fontSize * 0.7, w_: NAME_ROOM, ellipsis: true }); return; }
    Object.assign(w, { text: f.text, fontSize: f.fs, w_: NAME_ROOM, h_: Math.max(w.h_, lineBox(f.text, f.fs)) });
  };
  const inventory = (path) => get("UINonEquipmentInventory", path);
  const mail = (path) => get("UIMailBox", path);
  const content = (path) => get("UIMailBoxContent", path);
  const filter = (path) => get("UIPopup_FilterSort", path);
  const selection = () => at(get("UINonEquipmentInventoryListItem",
    `${layouts.UINonEquipmentInventoryListItem.children[0].name}/Normal/Cover_Select`));

  return {
    label,
    // the game list's prefab, for scrollbar.js
    collection: () => layouts.UICharacterCollection,
    rowGap: () => rowGap(ui, layouts) * ROW_SCALE,
    // UINonEquipmentInventory/Inventory_ListControl/BG, including the game's two background textures.
    background: () => at(inventory(`${INVENTORY}/BG`)),
    // Items/ListBG: retain its paper and inset; replace its grid with the scrolling mail cards.
    panel(width, height) {
      const t = at(inventory(LIST));
      t.children = t.children.filter((c) => ["PaperBG", "Inbox"].includes(c.name));
      Object.assign(t.widgets.find((w) => "sprite" in w), { w_: width, h_: height });
      for (const c of t.children) {
        const w = c.widgets.find((w) => "sprite" in w);
        const inset = c.name === "PaperBG" ? 9 : 45;
        Object.assign(w, { w_: width - inset, h_: height - (c.name === "PaperBG" ? 152 : 182) });
        c.pos[1] = -60; c.children = [];
      }
      return t;
    },
    // Anchor Items' complete header to the panel's top/right; only its title follows the left edge.
    header(width, title, caption, filtered, ascending, searching) {
      const t = inventory(`${LIST}/BG_Top_Widget`);
      const panel = ui.find(layouts.UINonEquipmentInventory, LIST).widgets.find((w) => "sprite" in w);
      t.pos[0] += width - panel.w_ / 2; t.pos[1] -= panel.h_ / 2;
      const heading = ui.find(t, "Label_Locale_Title");
      heading.pos[0] -= width - panel.w_;
      label(heading, title, 660).widgets.find((w) => "text" in w).ellipsis = true;
      const sort = ui.find(t, "SortBtn/Btn_Normal");
      for (const name of ["FilterMarker_Off", "FilterMarker_On"]) ui.find(sort, name).active = (name === "FilterMarker_On") === filtered;
      ui.find(sort, "Type_1").active = false; ui.find(sort, "Type_2").active = true;
      filterCaption(sort, caption);
      const order = ui.find(t, "OrderBtn/Btn_Normal");
      ui.find(order, "Icon_down").active = !ascending; ui.find(order, "Icon_Up").active = ascending;
      ui.find(t, "SearchBtn/Btn_Normal").active = !searching;
      ui.find(t, "SearchBtn/Btn_Pressed").active = searching;
      return t;
    },
    // Mailbox's top/right count stays in the paper header, outside the scrolling list.
    count: (caption) => {
      const t = at(mail("Right/Label_Amount")), w = t.widgets.find((w) => "text" in w), height = w.h_;
      label(t, caption, 500); Object.assign(w, { h_: height, pivot: 5, ellipsis: true }); return t;
    },
    // Items' white search field, outline, search icon and clear icon. Resize actual sprite widgets:
    // SearchNameArea starts with a nonvisual component in exported layouts, not its background sprite.
    search(width, searching = true) {
      const t = at(inventory(`${LIST}/Inbox/SearchInfo/SearchName/SearchNameArea`));
      const body = t.widgets.find((w) => "sprite" in w), delta = width - body.w_;
      body.w_ = width;
      ui.find(t, "BG_Line").widgets.find((w) => "sprite" in w).w_ = width;
      ui.find(t, "Icon_Search").pos[0] -= delta / 2;
      ui.find(t, "Btn_Close").pos[0] += delta / 2;
      ui.find(t, "Icon_Search").active = searching;
      ui.find(t, "Btn_Close").active = searching;
      for (const c of ui.find(t, "InputField").children) {
        const w = c.widgets.find((w) => "text" in w);
        w.text = "";
        w.w_ = searching ? w.w_ + delta : width - 60;
      }
      return t;
    },
    inputStyle(scale) {
      const w = ui.find(layouts.UINonEquipmentInventory, `${LIST}/Inbox/SearchInfo/SearchName/SearchNameArea/InputField/Label_Search`).widgets.find((w) => "text" in w);
      return { color: w.color.split("/")[0], caretColor: w.color.split("/")[0], fontSize: `${w.fontSize * scale}px` };
    },
    // MailboxContent/List_01/Btn_Receive supplies every blue action; Mailbox/ReceiveAll/Btn, the popups' gold, supplies Import.
    // Clone the entire disabled state, including its dark dotted B_C patterns, not just its body/label colours.
    // The caption size buttons side by side share (secondary ones, MailboxContent/List_01/Btn_Receive): the largest from the
    // prefab's size down to 70 % of it (or the range given) at which every caption fits its button (the widths, units) after
    // wrapping.
    buttonSize(captions, widths, { max = null, min = null } = {}) {
      const t = content("List_01/Btn_Receive"), w = ui.find(t, "Label_Locale").widgets.find((w) => "text" in w);
      return ui.fitGroup(captions, max ?? w.fontSize, min ?? w.fontSize * 0.7, widths.map((width) => width - XS_BUTTON.fitInset), 2,
        { maxH: t.widgets.find((x) => "sprite" in x).h_ - XS_BUTTON.boxInsetH })[0]?.fs;
    },
    // A row control's caption matches the badge's screen size. Preserve the prefab's transforms. The width is a buttonS control's
    // (controlWidth: the caption and CONTROL.buttonXS.pad each side, the body's slice border included; 200 at least), in the
    // button's own units like the caption, then drawn at the prefab's scale and `scale`.
    controlSize(caption, scale) {
      const t = content("List_01/Btn_Receive"), node = ui.find(t, "Label_Locale"), body = t.widgets.find((w) => "sprite" in w);
      const sx = t.scale?.[0] ?? 1, sy = t.scale?.[1] ?? 1, lx = node.scale?.[0] ?? 1, ly = node.scale?.[1] ?? 1;
      const size = ROW_TEXT_SIZE * ROW_SCALE / (scale * sy * ly);
      const meta = ui.atlasIndex?.()[body.atlas || "Common"]?.[body.sprite];
      return { width: controlWidth({ ...CONTROL.buttonXS, fs: size }, caption, (text, fs) => ui.glyphWidth(text, fs) * lx) * sx * scale,
        height: BUTTON_BODY_H * scale, size, minHeight: ((meta?.borderTop || 0) + (meta?.borderBottom || 0)) * sy * scale };
    },
    // size: the row's shared caption size (buttonSize)
    // bodyHeight: optional rendered height in units at the caller's scale; only row controls use it.
    button(caption, width, primary = false, enabled = true, size = null, bodyHeight = null) {
      const t = !enabled ? mail("Bottom/ObjectsDisplayed_Unread/ReceiveAll/Btn_Disable")
        : primary ? mail("Bottom/ObjectsDisplayed_Unread/ReceiveAll/Btn") : content("List_01/Btn_Receive");
      at(t);
      const body = t;
      resizeButton(body, bodyHeight == null ? width : width / (t.scale?.[0] ?? 1),
        bodyHeight == null ? BUTTON_BODY_H : bodyHeight / (t.scale?.[1] ?? 1));
      // NGUI's DOM renderer uses sprite flips for mirroring; negative prefab scales would produce negative CSS sizes.
      for (const c of body.children) for (const w of c.widgets) if (/^Common_Btn_.*_Pt$/.test(w.sprite || "")) {
        w.flip = (w.flip || 0) ^ (c.scale[0] < 0 ? 1 : 0) ^ (c.scale[1] < 0 ? 2 : 0);
        c.scale = c.scale.map(Math.abs);
      }
      const captionNode = ui.find(t, "Label_Locale");
      const w = captionNode.widgets.find((w) => "text" in w), height = body.widgets.find((w) => "sprite" in w).h_ - XS_BUTTON.boxInsetH;
      if (bodyHeight != null) {
        const lx = captionNode.scale?.[0] ?? 1, ly = captionNode.scale?.[1] ?? 1, bg = body.widgets.find((w) => "sprite" in w);
        const meta = ui.atlasIndex?.()[bg.atlas || "Common"]?.[bg.sprite];
        // the caption keeps CONTROL.buttonXS.pad clear on each side (the body's slice border is part of it)
        const pad = CONTROL.buttonXS.pad, room = (bg.w_ - 2 * pad) / lx;
        const f = ui.fitText(caption, size ?? w.fontSize, size ?? w.fontSize, room, 1);
        Object.assign(w, { text: f.text, fontSize: f.fs, w_: room, h_: bg.h_ / ly, pivot: 1 });
        captionNode.pos = [0, bg.h_ / 2 - inkDrop(w) * ly + ((meta?.borderBottom || 0) - (meta?.borderTop || 0)) / 2];
        return t;
      }
      const fitRoom = width - XS_BUTTON.fitInset;
      if (size) {
        // the row's size: wrapped within the button at that size, whatever the prefab's own size is
        const f = ui.wrapText?.(caption, size, fitRoom, 2) ?? ui.fitText(caption, size, size, fitRoom, 2);
        Object.assign(w, { text: f.text, fontSize: f.fs, w_: Math.max(w.w_ || 0, fitRoom), h_: height });
        return t;
      }
      label(captionNode, caption, fitRoom, 2);
      if (w.h_ > height) { w.fontSize *= height / w.h_; w.h_ = height; }
      return t;
    },
    // UIPopup_GachaCharList.Btn_Checkbox, as the roster's variant grouping toggle: its own caption, the body sized to it.
    toggle(caption, on) {
      const t = at(get("UIPopup_GachaCharList.Btn_Checkbox")), text = label(ui.find(t, "Locale_Text"), caption, 460);
      const w = text.widgets.find((w) => "text" in w);
      t.widgets.find((w) => "sprite" in w).w_ = text.pos[0] + ui.glyphWidth(w.text, w.fontSize) + 46;
      for (const c of t.children) if (c.name.startsWith("Type_")) {
        c.children = c.children.filter((k) => k.name === "Check");
        c.active = (c.name === "Type_ON") === on;
      }
      return t;
    },
    // UIPopup_FilterSort's actual UIFilter_CheckBox, also used by the roster's display settings.
    checkbox(checked, disabled = false) {
      const t = at(filter(`${FILTER}/Table_Align/Table_ListUnit/UIFilterObjectListUnit_1/UIFilter_CheckBox`));
      ui.find(t, "CheckBoxIcon").active = checked && !disabled;
      ui.find(t, "CheckBoxIcon_Disable").active = checked && disabled;
      return t;
    },
    // UIPopup_FilterSort/UISortObjectListUnit/Label_Text, matching display.js's sortUnit.
    radio(caption, checked, width = 1500) {
      const t = at(filter(RADIO));
      ui.find(t, "On").active = checked;
      ui.find(t, "Label_On").active = !checked;
      for (const p of ["On/Label_On", "Label_On"]) label(ui.find(t, p), caption, width - 100, 2);
      ui.find(t, "Line").widgets[0].w_ = width;
      return t;
    },
    // Items/EquipPos: bind comparison art to its UITexture without the decorative backing circle.
    preview(caption, { width, height, headerHeight }) {
      const t = at(inventory(`${INVENTORY}/EquipPos`));
      const picture = ui.find(t, "Texture");
      Object.assign(picture.widgets[0], { w_: width, h_: height, pivot: 1 }); picture.pos = [0, -headerHeight];
      t.children = t.children.filter((child) => child.name !== "BGCilcle");
      const tag = inventory(`${DETAIL}/Text_Type`);
      const bg = ui.find(tag, "TypeBG"), body = bg.widgets[0];
      const tagScale = Math.min(1, headerHeight / (body.h_ * tag.scale[1]));
      tag.scale = tag.scale.map((value) => value * tagScale);
      at(tag, -width / 2 - bg.pos[0] * tag.scale[0], -(bg.pos[1] + body.h_ / 2) * tag.scale[1]);
      label(tag, caption, width / tag.scale[0] - 40);
      body.w_ = width / tag.scale[0];
      t.children.push(tag); return t;
    },
    // Items/Equip_Target: keep the dark name plate, texture and category tag. Quantity/expiry are irrelevant to Mods.
    banner(name, kind) {
      const t = at(inventory(DETAIL));
      t.children = t.children.filter((c) => ["BG", "Label_Name", "Text_Type"].includes(c.name));
      ui.find(t, "BG").children = ui.find(t, "BG").children.filter((c) => c.name !== "HaveBG");
      namePlate(t, name);
      const tag = ui.find(t, "Text_Type"); label(tag, kind, 850);
      ui.find(tag, "TypeBG").widgets[0].w_ = 910;
      return t;
    },
    // MailboxContent/List_01 (unread) and List_02 (muted/read): the real card, title, divider and meta slots.
    // The sender becomes the aligned kind column; TimeLimit becomes status; receipt/expiry become metadata/reason.
    // The beta type scale is a floor. Content changes geometry, never the
    // type size of other fields or rows (including hidden result states).
    rowTextSize() { return ROW_TEXT_SIZE; },
    // control: {width, height, minHeight?} in px. Share the badge line when it fits; otherwise reserve room under it.
    row({ name, kind, state, stateKey = "ready", meta, route = "", note = "", muted = false, counts = null, badge = true, result = false, unidentified = false, textSize = ROW_TEXT_SIZE, control = null }) {
      const t = at(content(muted ? "List_02" : "List_01"));
      t.children = t.children.filter((c) => ["BG", "Deco", "Deco (1)", "Sprite", "Line", "BG_Icon", "Label_System", "Label_From",
        "Label_Locale_Received", "Label_Locale_Limit", "TimeLimit"].includes(c.name));
      ui.find(t, "BG_Icon").children = ui.find(t, "BG_Icon").children.filter((c) => c.name === "Icon_Arona");
      const picture = ui.find(t, "BG_Icon/Icon_Arona"), slot = picture.widgets.find((w) => "texture" in w);
      picture.active = true;
      ui.find(t, "BG_Icon").widgets = [];
      const window = selectionWindow(selection(), ui.atlasIndex?.(), layouts.selectionOutline, layouts.pictureOutline);
      const bleed = Math.abs(Math.tan(window.incline * Math.PI / 180)) * window.height / window.width * 100;
      Object.assign(slot, { w_: window.width, h_: window.height, pivot: 4, clipImage: null,
        incline: window.incline, radius: 0, clipPolygon: window.clipPolygon, imgSize: "cover", imgPosition: "center",
        imgLeft: `${-bleed / 2}%`, imgWidth: `${100 + bleed}%`, imgTop: "0", imgHeight: "100%" });
      picture.pos = [0, 0]; picture.scale = [1, 1];
      ui.find(t, "BG_Icon").pos = [window.x, window.y]; ui.find(t, "BG_Icon").scale = [1, 1];
      // Reserve 32 stage pixels beyond the entire selected slot, including the real prefab frame.
      // Metadata gets half the text band; kind and state keep separate one-line cells.
      const frameRight = ROW_SELECTION.left + ROW_SELECTION.origin[0] + rightEdge(selection()) * ROW_SELECTION.scale;
      const textLeft = Math.max(COLUMNS.floor, rightEdge(ui.find(t, "BG_Icon")) + 32 / ROW_SCALE,
        (frameRight + 32 - ROW_WIDTH / 2) / ROW_SCALE);
      // Stretch the complete mail highlight so its transparent end survives the wider card.
      for (const name of ["BG", "Sprite"]) {
        const n = ui.find(t, name);
        for (const w of n?.widgets || []) if ("sprite" in w || "texture" in w) {
          const pivot = w.pivot ?? 4;
          n.pos[0] = 0; w.pivot = Math.floor(pivot / 3) * 3 + 1; w.w_ = ROW_WIDTH / ROW_SCALE / Math.abs(n.scale?.[0] || 1);
        }
      }
      const divider = ui.find(t, "Line");
      divider.pos[0] = textLeft;
      Object.assign(divider.widgets[0], { pivot: 3, w_: COLUMNS.titleRight - textLeft });
      // The row's one text type (every text at TYPE.dense: wrapped, never smaller) and the pitch of separate lines under each
      // other (List_01's meta lines: 66 at 48, LINE.stack of the size).
      const dense = { fs: textSize, min: textSize }, pitch = LINE.stack * textSize, room = COLUMNS.titleRight - textLeft;
      // Layout boxes ({ x, y, w, h }) are in units from the card's centre line (x) and down from the first text line's top (y);
      // toNode turns a box into the NGUI position of the widget that fills it. The name plate (Label_System, Line) keeps the
      // prefab's place; the layout starts under the divider.
      const frame = { cx: 0, cy: ROW_TEXT_TOP, w: 0, h: 0 }, up = (y) => ROW_TEXT_TOP - y;
      // (sums of boxes leave dust of 1e-15 units in y; it is kept to a billionth)
      const put = (n, b, pivot) => { const [x, y] = toNode(b, pivot, frame); n.pos = [x, Math.round(y * 1e9) / 1e9]; return n; };
      const wrap = (value, width) => {
        const fit = ui.wrapText?.(String(value ?? ""), textSize, width, Infinity)
          ?? ui.fitGroup?.([String(value ?? "")], textSize, textSize, width, Infinity)?.[0];
        if (fit?.fs === textSize && !fit.overflow) return fit.text;
        const rows = []; let line = "";
        for (const ch of String(value ?? "")) {
          if (line && ui.glyphWidth(line + ch, textSize) > width) {
            const space = line.lastIndexOf(" ") + 1;
            rows.push(space ? line.slice(0, space) : line); line = space ? line.slice(space) : "";
          }
          line += ch;
        }
        rows.push(line); return rows.join("\n");
      };
      // A row label's text at the row's one size: wrapped to the width, or one line with an ellipsis; item is its measured text
      // (textItem: ink band, reference centre) and null for no text.
      const shape = (value, width, ellipsis = false) => {
        const caption = String(value ?? ""), text = ellipsis ? caption.replace(/\s*\n\s*/g, " ") : wrap(caption, width);
        return { caption, text, width, ellipsis, lines: text ? text.split("\n").length : 0, item: text ? textItem(ui, text, dense) : null };
      };
      const fill = (n, shaped, pivot = 0) => {
        const w = n.widgets.find((w) => "text" in w);
        Object.assign(w, { text: shaped.text, fontSize: textSize, w_: shaped.width, h_: textSize * LABEL_BOX * shaped.text.split("\n").length,
          pivot, ellipsis: shaped.ellipsis, ariaLabel: shaped.caption });
        return shaped.lines;
      };
      // The kind (Portrait, Memorial Lobby, 3D) sits on the name's line at the divider's right end, above the divider; the
      // name keeps the rest of the line.
      const kindWidth = Math.max(COLUMNS.kind.min, Math.min(COLUMNS.kind.max, ui.glyphWidth(kind, textSize) + 2 * SPACE.xs));
      const kindOnTitle = Boolean(kind) && !counts;
      const title = ui.find(t, "Label_System"); title.pos[0] = textLeft;
      label(title, name, kindOnTitle ? room - kindWidth - SPACE.l : room, 1);

      // The status badge: one line, as wide as its text and the caption padding (CONTROL.badge.pad each side), at least the
      // App's minimum. It ends the metadata's line; the metadata takes what is left and wraps (owner, 2026-11-07: a badge never
      // leaves the line). A Choose Files control goes to the next line, its right edge on the badge's (owner, 2026-11-07). Only
      // a badge that would leave the metadata less than COLUMNS.metaMin goes to a line of its own.
      const fullBadgeWidth = Math.max(COLUMNS.badgeMin, Math.min(room, ui.glyphWidth(state, textSize) + 2 * CONTROL.badge.pad));
      const controlW = control ? control.width / ROW_SCALE : 0;
      const controlRoom = control ? COLUMNS.right - textLeft - controlW - SPACE.l : 0;
      const besideWidth = Math.max(COLUMNS.badgeMin, Math.min(fullBadgeWidth, controlRoom));
      const beside = Boolean(control && badge && controlRoom >= COLUMNS.badgeMin && wrap(state, besideWidth - BADGE_LABEL_INSET).split("\n").length === 1
        && (control.minHeight || 0) <= (textSize * LABEL_BOX + 2 * SPACE.hair) * ROW_SCALE);
      const trailing = badge ? fullBadgeWidth : 0;
      const inline = badge && trailing + SPACE.l <= COLUMNS.right - textLeft - COLUMNS.metaMin;
      const badgeWidth = inline ? fullBadgeWidth : beside ? besideWidth : fullBadgeWidth;
      const badgeShape = shape(state, badgeWidth - BADGE_LABEL_INSET);
      // the label's box (NGUI: LABEL_BOX of the size a line) and the background round it, SPACE.hair beyond it above and below
      const labelBox = textSize * LABEL_BOX * badgeShape.text.split("\n").length, badgeH = labelBox + 2 * SPACE.hair;
      const secondBadge = badge && !inline;
      const own = secondBadge && badge;

      // The first line: the metadata and the kind column (or, in a merged row, the counts block), top aligned. The kind column
      // ends on the aligned column (or SPACE.l before the badge when that is further left); the counts block starts at COLUMNS.counts.left.
      const countsW = result ? COLUMNS.counts.result : COLUMNS.counts.list;
      const columns = counts ? { w: COLUMNS.counts.left + countsW - textLeft, gap: 0, kindW: countsW }
        // without a badge (a result row) the metadata keeps clear of the row's action control, on the aligned column
        : { w: (inline ? COLUMNS.right - trailing - SPACE.l : badge ? COLUMNS.titleRight : COLUMNS.kindRight) - textLeft, gap: 0, kindW: 0 };
      const metaShape = shape(meta, layoutRow([{ grow: 1, h: 0 }, { w: columns.kindW, h: 0 }], { x: textLeft, w: columns.w, gap: columns.gap }).boxes[0].w);
      const sender = ui.find(t, "Label_From");
      let kindShape = null, kindLines;
      if (counts) {
        const kinds = ["spr", "lobby", "model", "other"].filter((kind) => counts[kind] > 0);
        sender.widgets = [];
        sender.children = countNodes(counts, {}, ui.translate, { kinds, width: countsW,
          color: ui.find(t, "Label_System").widgets[0].color, fontSize: textSize, top: true });
        kindLines = 1;
        for (const child of sender.children) {
          const count = ui.find(child, "Count"), w = count.widgets[0];
          kindLines = Math.max(kindLines, fill(count, shape(w.text, w.w_), w.pivot));
        }
      } else { kindShape = shape(kind, kindWidth); kindLines = 0; }
      // A line's centre line is the reference centre of its first text line (one per language, whatever the letters); a badge on
      // the line sits on it. Tops of the texts of one line stay level.
      // (without font metrics a text has no reference centre: the middle of its label box stands in)
      const lineFactor = ui.LINE_PITCH ?? 1.15, boxMid = textSize * LABEL_BOX / 2;
      const firstMid = (item) => item && (item.mid == null ? boxMid : item.mid - (item.lines - 1) * item.px * lineFactor / 2);
      const badgeMid = badgeShape.item?.mid ?? labelBox / 2;
      const lineMid = firstMid(metaShape.item) ?? firstMid(badgeShape.item) ?? boxMid;
      const onLine = (items, options) => layoutRow(items, { ...options, y: lineMid - layoutRow(items, options).centre });
      const lines1 = Math.max(metaShape.lines, kindLines);
      const first = onLine([{ grow: 1, h: metaShape.lines * pitch, mid: lineMid }, { w: columns.kindW, h: kindLines * pitch, mid: lineMid }],
        { x: textLeft, w: columns.w, gap: columns.gap });
      const received = ui.find(t, "Label_Locale_Received"); received.children = [];
      fill(received, metaShape);
      put(received, first.boxes[0], 0);
      if (kindShape) {
        // right aligned on the divider's end, its label box centred on the name's
        const titleText = title.widgets.find((w) => "text" in w);
        const titleMid = title.pos[1] + [-1, 0, 1][Math.floor((titleText.pivot ?? 4) / 3)] * titleText.h_ / 2;
        fill(sender, kindShape, 5);
        sender.pos = [COLUMNS.titleRight, titleMid];
        sender.active = kindOnTitle;
      } else put(sender, first.boxes[1], 1);

      // The prefab's vertical rhythm: the gap from the divider (Line) to the ink of the meta line. What hangs under the meta line
      // (a badge on its own line, then a control) keeps that gap from the ink or background above it.
      const metaInk = metaShape.item?.ink ?? null, dividerBottom = divider.pos[1] - divider.widgets[0].h_ / 2;
      const gap = metaInk ? dividerBottom - up(metaInk[0]) : FALLBACK_GAP;
      // The lines under the first, one pitch a line (a badge on its own line counts one). A badge on its own line
      // hangs one gap under the ink of the meta (and kind) lines, not one pitch under their boxes; the lines after it move with it.
      const hangs = own && metaInk;
      const inkBottom = metaInk?.[1] ?? 0;
      const routeShape = route ? shape(route, room) : null;
      const under = layoutStack([...own ? [{ w: room, h: pitch }] : [], ...routeShape ? [{ w: room, h: routeShape.lines * pitch }] : [],
        { w: room, h: pitch }], { x: textLeft, y: hangs ? inkBottom + gap + SPACE.hair : lines1 * pitch, w: room });
      let next = 0;
      const cell = own ? under.boxes[next++] : null;
      let platform = null;
      if (routeShape) {
        platform = clone(received); platform.name = "Platform";
        fill(platform, routeShape); put(platform, under.boxes[next++], 0); t.children.push(platform);
      }
      let limit = ui.find(t, "Label_Locale_Limit");
      if (!limit) { limit = content("List_01/Label_Locale_Limit"); t.children.push(limit); }
      limit.children = [];
      fill(limit, shape(note, room, true)); put(limit, under.boxes[next], 0);

      // TimeLimit is only present on the unread card; clone that same node for read cards as well.
      let status = ui.find(t, "TimeLimit");
      if (!status) { status = content("List_01/TimeLimit"); t.children.push(status); }
      status.active = badge;
      // the badge, and beside it the control: on its own line they share the line, the badge at the column's start and the control
      // on the right column, level with its background; on the first line it ends on the right column
      const badgeLine = own
        ? layoutRow(beside ? [{ w: badgeWidth, h: badgeH }, { w: controlW, h: badgeH }] : [{ w: badgeWidth, h: badgeH }],
          { x: textLeft, y: cell.y - SPACE.hair, w: COLUMNS.right - textLeft, gap: SPACE.l, justify: beside ? "between" : "start" })
        : onLine([{ w: badgeWidth, h: badgeH }], { x: textLeft, w: COLUMNS.right - textLeft, justify: "end" });
      const badgeBox = badgeLine.boxes[0];
      // the badge's box, for the hit area that opens a refused row's details (badgeRect)
      t.badge = badge ? { right: badgeBox.x + badgeBox.w, width: badgeBox.w, height: badgeBox.h, top: up(badgeBox.y) } : null;
      // the status node sits at the label's top, SPACE.hair under the background's
      put(status, { ...badgeBox, y: badgeBox.y + SPACE.hair }, 1);
      ui.find(status, "Bg").widgets[0].w_ = badgeWidth;
      const tones = {
        ready: inventory(`${DETAIL}/Text_Amount`).widgets[0].color,
        convert: mail("Bottom/ObjectsDisplayed_Unread/ReceiveAll/Btn").widgets[0].color,
        update: mail("Bottom/ObjectsDisplayed_Unread/ReceiveAll/Btn").widgets[0].color,
        library: content("List_02/BG").widgets[0].color,
        installed: content("List_02/Label_System").widgets[0].color,
        done: content("List_02/Label_System").widgets[0].color,
        blocked: inventory(`${DETAIL}/Label_ExpirationDateTime/TypeBG`).widgets[0].color,
      };
      ui.find(status, "Bg").widgets[0].color = tones[stateKey] || tones.blocked;
      const statusLabel = ui.find(status, "Label_Locale_TimeLimit");
      if (["ready", "convert", "update", "library"].includes(stateKey)) statusLabel.widgets[0].color = inventory(`${DETAIL}/Text_Type`).widgets[0].color;
      fill(statusLabel, badgeShape, 1);
      const statusBG = ui.find(status, "Bg"); at(statusBG, 0, SPACE.hair);
      Object.assign(statusBG.widgets[0], { pivot: 1, h_: badgeH });
      // the ink of the badge's text, not its line box, is centred on the badge (every state, one baseline per language)
      statusLabel.pos = [0, badgeMid - labelBox / 2];
      // The control shares the badge background's vertical box and ends at the right column. A wrapped badge or a control
      // that cannot fit keeps the old layout: one gap below the badge, its right edge on the badge's, with a gap under the
      // control inside the grown card.
      let reserved = null;
      if (control && badge) {
        if (inline) {
          const below = layoutStack([{ w: badgeWidth, h: badgeH }, { w: controlW, h: control.height / ROW_SCALE }],
            { x: badgeBox.x, y: badgeBox.y, w: badgeWidth, gap, align: "end" }).boxes[1];
          reserved = { right: badgeBox.x + badgeBox.w, width: controlW, height: below.h, top: up(below.y), beside: false };
        } else if (beside) {
          const box = badgeLine.boxes[1];
          reserved = { right: box.x + box.w, width: controlW, height: box.h, top: up(box.y), beside };
        } else {
          const below = layoutStack([{ w: badgeWidth, h: badgeH }, { w: controlW, h: control.height / ROW_SCALE }],
            { x: textLeft, y: badgeBox.y, w: badgeWidth, gap, align: "end" }).boxes[1];
          reserved = { right: status.pos[0] + badgeWidth / 2, width: controlW, height: below.h, top: up(below.y), beside };
        }
      }
      // Longer metadata wraps in full. Grow the prefab body downwards rather
      // than clipping a route or shrinking the whole list. Notes remain one line.
      let bottom = LIST_ROW.h / 2;
      const include = (n, y = 0) => {
        if (n.active === false) return;
        y += n.pos?.[1] || 0;
        for (const w of n.widgets || []) if (w.text) bottom = Math.max(bottom, -y + w.h_ + 2 * SPACE.xs);
        for (const child of n.children || []) include(child, y);
      };
      for (const path of ["Label_Locale_Received", "Label_From", "Platform", "Label_Locale_Limit", "TimeLimit"]) {
        const n = ui.find(t, path); if (n) include(n);
      }
      if (reserved && !reserved.beside) bottom = Math.max(bottom, gap - (reserved.top - reserved.height));
      const extra = bottom - LIST_ROW.h / 2;
      if (extra > 0) for (const name of ["BG", "Sprite"]) {
        const n = ui.find(t, name); if (!n) continue;
        n.pos[1] -= extra / 2;
        for (const w of n.widgets) if (w.h_) w.h_ += extra;
      }
      t.rowHeight = ROW_PITCH + extra;
      t.control = reserved;
      return t;
    },
    // Items list card's Cover_Select provides the selected preview marker, independently of import selection.
    selection,
    // Items/ListEmpty label and Mailbox's footer note supply dialog copy.
    empty: (caption) => at(label(inventory(`${LIST}/Inbox/ListEmpty/Label_Locale_EmptyMessage`), caption, 1700, 2)),
    note: (caption) => at(label(mail("Bottom/ObjectsDisplayed_Unread/Label_Locale"), caption, 1678, 2)),
    // Items' expiry tag supplies the error tone; read mail's title supplies the muted note tone.
    error(caption) {
      const t = at(label(inventory(`${LIST}/Inbox/ListEmpty/Label_Locale_EmptyMessage`), caption, 1700, 2));
      t.widgets[0].color = inventory(`${DETAIL}/Label_ExpirationDateTime/TypeBG`).widgets[0].color;
      return t;
    },
  };
}

// The box [x, y, width, height] (px, from the row's top left) of the control a row reserved room for (row({ control })), or null.
export function controlRect(tree) {
  const c = tree?.control;
  return c ? [ROW_WIDTH / 2 + (c.right - c.width) * ROW_SCALE, LIST_ROW.h * ROW_SCALE / 2 - c.top * ROW_SCALE, c.width * ROW_SCALE, c.height * ROW_SCALE] : null;
}

// The status badge's box [x, y, width, height] (px, from the row's top left), as controlRect, or null without a badge.
export function badgeRect(tree) {
  const b = tree?.badge;
  return b ? [ROW_WIDTH / 2 + (b.right - b.width) * ROW_SCALE, LIST_ROW.h * ROW_SCALE / 2 - b.top * ROW_SCALE, b.width * ROW_SCALE, b.height * ROW_SCALE] : null;
}

export { clipButtonPatterns } from "./button-layout.js";
