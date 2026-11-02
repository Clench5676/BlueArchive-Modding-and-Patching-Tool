// UICharacterCollection's search field and list tools, shared with Batch Import.
import * as NGUI from "./ngui.js";
import { S, clone, advanceOf, glyphsOf } from "./util.js";
import { emptyBlock } from "./empty-block.js";

export function searchTree(collection) {
  const box = clone(find(collection, "SeletControl/CharacterSearch"));
  const input = find(box, "SearchName/SearchNameArea/InputField");
  for (const c of input.children) c.widgets.find((w) => "text" in w).text = "";
  return box;
}

// Keep the roster's funnel line, expand marker, caption and inclined shadow together when the width changes.
export function sortBody(collection, fitted, width, filtered) {
  const body = clone(find(collection, "SeletControl/TopMenu/SortBtn/Btn_Normal"));
  const shrink = 622 - width;
  body.widgets.find((w) => "sprite" in w).w_ = width;
  for (const name of ["FilterMarker_Off", "FilterMarker_On"]) {
    const n = find(body, name);
    n.pos[0] += shrink / 2;
    find(n, "Icon").pos[0] -= shrink;
    find(n, "Filter_02").widgets[0].w_ -= shrink;
    n.active = (name === "FilterMarker_On") === filtered;
  }
  find(body, "Icon").pos[0] -= shrink / 2;
  find(body, "Type_1").active = false;
  const type2 = find(body, "Type_2");
  type2.active = true;
  Object.assign(find(type2, "Text").widgets[0], { text: fitted.text, fontSize: fitted.fs, w_: width - 120 });
  find(type2, "Text").pos[0] = 30;
  return body;
}

export function orderBody(collection, ascending) {
  const body = clone(find(collection, "SeletControl/TopMenu/OrderBtn/Btn_Normal"));
  find(body, "Icon_down").active = !ascending;
  find(body, "Icon_Up").active = ascending;
  return body;
}

export function pickerItems(items, { query = "", sort = "default", ascending = true } = {}) {
  const terms = query.trim().toLocaleLowerCase().split(/\s+/).filter(Boolean);
  const list = items.filter((item) => terms.every((term) =>
    [item.label, item.full, item.original, item.key].some((value) => String(value || "").toLocaleLowerCase().includes(term))));
  if (sort === "name") list.sort((a, b) => String(a.label).localeCompare(String(b.label)));
  return ascending ? list : list.reverse();
}

import { T } from "./i18n.js";
import { SORTS, filtering } from "./roster-order.js";
const { find, node, label, fitText } = NGUI;

export function toolbarLayout(view, { title = T("roster.title"), left = -1270, right = 1412 } = {}) {
  const steps = [[230, 44, 56, 40, 62], [210, 40, 48, 38, 62], [200, 36, 44, 34, 58], [190, 34, 42, 32, 54], [180, 32, 40, 30, 50], [170, 30, 36, 28, 46]];
  for (const [segW, segFs, sortFs, cbFs, titleFs] of steps) {
    const titleEnd = left + advanceOf(title, titleFs) + 40;
    // the six segments of both filters share one size
    const segLabels = NGUI.fitGroup(["seg.all", "seg.mods", "seg.noMods", "seg.all", "seg.recruit", "seg.noRecruit"].map((k) => T(k)), segFs, 22, segW - 26, 2, { maxH: 96 });
    // the sort button is as wide as its longest key, so choosing another one moves nothing
    const sortTexts = SORTS.map((k) => fitText(T(`sort.${k}`), sortFs, 28, 420));
    const sortText = sortTexts[Math.max(0, SORTS.indexOf(view.sort))];
    const sortW = Math.max(300, ...sortTexts.map((s) => glyphsOf(s.text, s.fs) + 170));
    const cbText = fitText(T("roster.merge"), cbFs, 24, 400);
    const cbW = 131 + glyphsOf(cbText.text, cbText.fs) + 46;
    let x = right;
    const order = x - 243 / 2; x -= 243 + 16;
    const sort = x - sortW / 2; x -= sortW + 16;
    const search = x - 75; x -= 150 + 16;
    const seg2 = x; x -= 3 * segW + 16;
    const seg1 = x; x -= 3 * segW + 16;
    const cbLeft = x - cbW;
    if (cbLeft >= titleEnd || segW === 170) return { segW, segLabels, sortText, sortW, cbText, cbW, order, sort, search, seg1, seg2, cbLeft, titleFs };
  }
  return null;
}


// UICharacterCollection/SeletControl/LeftInfo_Empty, in its own units (y up) from its root: Arona's picture (Image_Inven_Empty,
// a 943 box at y -318) over the faint disc, and the message under them (Label_Locale at y -589). Measured on the official
// texture, 1024 square: its ink (alpha above 16) lies in rows 93 to 620.
export const EMPTY_LIST = { label: -589, picture: { y: -318, size: 943, inkTop: 93 / 1024 } };

// Where LeftInfo_Empty's root goes (units, y up) so that its ink, from the top of Arona's picture to the bottom of the message,
// is centred on `centre` (units): the middle of the paper the eye sees (the whole of it, or what the open search box leaves).
// label: the message's widget as drawn (its text and font size).
export function emptyListY(centre, label) {
  const { picture } = EMPTY_LIST;
  const pictureTop = picture.y + picture.size / 2 - picture.inkTop * picture.size;
  const ink = NGUI.inkBand(label.text, NGUI.fontSizeFor(label.text, label.fontSize) * S);
  // the message's box centre (px, down) that puts the block's centre on the paper's; the picture's ink reaches up to
  // `pictureTop`, which is (pictureTop - label) units above the message's centre
  const at = emptyBlock(-centre * S, { top: -(pictureTop - EMPTY_LIST.label) * S, bottom: ink.bottom });
  return -at.title / S - EMPTY_LIST.label;
}

// The toolbar's three toggles as hit areas, in SeletControl's units: the six segments of the two filters and Group Alts.
// Each has its box [x, y, width, height] (centre), its name (the caption it draws, "All" in both filters), whether its
// setting is on (aria-pressed) and apply(view), which sets it.
export function toggleHits(view, lay, segY = 720 - 219 - 50.2) {
  const segments = (key, values, labels, centre) => values.map((value, i) => ({
    box: [centre - (2.5 - i) * lay.segW, segY, lay.segW, 104], name: T(labels[i]), pressed: view[key] === value,
    apply: (target) => { target[key] = value; } }));
  return [
    ...segments("mods", ["all", "mods", "none"], ["seg.all", "seg.mods", "seg.noMods"], lay.seg1),
    ...segments("recruit", ["all", "yes", "no"], ["seg.all", "seg.recruit", "seg.noRecruit"], lay.seg2),
    { box: [lay.cbLeft + lay.cbW / 2, 720 - 273, lay.cbW, 110], name: T("roster.merge"), pressed: view.merged === true,
      apply: (target) => { target.merged = !target.merged; } },
  ];
}

// Shared complete UICharacterCollection toolbar; callers retain their own list and title.
export function toolbarTree(collection, checkbox, view, searching, lay = toolbarLayout(view)) {
  const menu = clone(find(collection, "SeletControl/TopMenu"));
  // segment filters: the game's All / STRIKER / SPECIAL control with our words in the same colours
  const segFilter = (name, labels, active, right) => {
    const f = clone(find(menu, "FilterBtn"));
    f.name = name;
    f.active = true;
    const sx = lay.segW / 328;
    f.scale = [sx, 1];
    f.pos = [right - (529.7 + 164) * sx, -219];
    [["All", 0], ["Main", 1], ["Support", 2]].forEach(([k, i]) => {
      const seg = find(f, k);
      for (const state of seg.children) {
        for (const c of state.children || []) {
          const tex = c.widgets.find((w) => "texture" in w);
          if (tex) c.widgets = [label(labels[i].text, labels[i].fs, tex.color, (lay.segW - 16) / sx, 60 * labels[i].lines, { depth: 9 })];
        }
        state.active = (state.name === "Btn_Pressed") === (i === active);
      }
    });
    return f;
  };
  find(menu, "FilterBtn").active = false;
  menu.children.push(
    segFilter("ModFilter", lay.segLabels.slice(0, 3), { all: 0, mods: 1, none: 2 }[view.mods], lay.seg1),
    segFilter("RecruitFilter", lay.segLabels.slice(3), { all: 0, yes: 1, no: 2 }[view.recruit], lay.seg2));
  // search: the game's toggle, pressed while the search box is open
  const search = find(menu, "SearchBtn");
  search.pos[0] = lay.search - 89;
  find(search, "Btn_Normal").active = !searching;
  find(search, "Btn_Pressed").active = searching;
  // sort: the current key, and the funnel in blue while a display setting filters the list
  const sort = find(menu, "SortBtn");
  sort.pos[0] = lay.sort + 83;
  sort.children = sort.children.map((c) => c.name === "Btn_Normal" ? sortBody(collection, lay.sortText, lay.sortW, filtering(view.filters)) : c);
  // order: one arrow, down for descending (the prefab holds both, Icon_Up flipped)
  const orderBtn = find(menu, "OrderBtn");
  orderBtn.pos[0] = lay.order + 45.1;
  orderBtn.children = orderBtn.children.map((c) => c.name === "Btn_Normal" ? orderBody(collection, view.order !== "desc") : c);
  // checkbox toggle after the title (game: "unowned students only" in the gacha character list)
  const cb = clone(checkbox);
  cb.name = "Merge";
  cb.pos = [lay.cbLeft, -266];
  Object.assign(cb.widgets.find((w) => "sprite" in w), { w_: lay.cbW, h_: 104 });
  cb.children = cb.children.filter((c) => c.name !== "Locale_Text");
  for (const c of cb.children) {
    c.children = (c.children || []).filter((k) => k.name === "Check");
    c.active = (c.name === "Type_ON") === view.merged;
  }
  cb.children.push(node("Label", [131, 0], [label(lay.cbText.text, lay.cbText.fs, "#484e52/1.00", lay.cbW - 131, 50 * lay.cbText.lines, { pivot: 3, depth: 31 })]));
  find(menu, "Menubar").children.push(cb);
  return menu;
}
