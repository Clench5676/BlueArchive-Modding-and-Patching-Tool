// UICharacterCollection's search field and list tools, shared with Batch Import.
import * as NGUI from "./ngui.js";
import { S, clone, advanceOf, glyphsOf } from "./util.js";
import { emptyBlock } from "./empty-block.js";
import { CONTROL, SPACE } from "./ui-tokens.js";
import { row } from "./ui-grid.js";

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

// a list popup's cards for its search and sort: found by their name, full name, original name, key and any further
// terms of their own (an item's ``terms``: a model's dev ids, BL-131)
export function pickerItems(items, { query = "", sort = "default", ascending = true } = {}) {
  const terms = query.trim().toLocaleLowerCase().split(/\s+/).filter(Boolean);
  const list = items.filter((item) => terms.every((term) => [item.label, item.full, item.original, item.key, ...(item.terms || [])]
    .some((value) => String(value || "").toLocaleLowerCase().includes(term))));
  if (sort === "name") list.sort((a, b) => String(a.label).localeCompare(String(b.label)));
  return ascending ? list : list.reverse();
}

import { T } from "./i18n.js";
import { SORTS, filtering } from "./roster-order.js";
const { find, node, label, fitText } = NGUI;

// The student roster's toolbar: its Group Alts checkbox, the two segment filters, search, sort and order.
export function toolbarLayout(view, { title = T("roster.title"), left = -1270, right = 1412 } = {}) {
  return toolbarFit({ title, left, right,
    filters: [["seg.all", "seg.mods", "seg.noMods"], ["seg.all", "seg.recruit", "seg.noRecruit"]].map((keys) => keys.map((k) => T(k))),
    sorts: SORTS.map((k) => T(`sort.${k}`)), sortAt: SORTS.indexOf(view.sort), check: T("roster.merge") });
}

// A toolbar's fit, right to left from the order button, with the most generous sizes at which everything stands after
// the title (the first step that fits; the last one regardless, ``fits`` says): a checkbox, the segment filters (``filters``:
// the three captions of each), the search, sort and order tools. ``sorts``: every caption the sort button can show (it is as
// wide as its longest, so choosing another moves nothing), ``sortAt``: the shown one; ``check``: the checkbox's caption.
// The roster takes -1270 to 1412 after its title; a list popup its own width and no title (popup.js).
// icons: how many icon toggles (the search button's body, another icon) stand before search (a list popup's, BL-176)
export function toolbarFit({ title = "", left, right, filters, sorts, sortAt = 0, check, icons = 0 }) {
  // the last three steps only a list popup's narrower list reaches (BL-176: the L class)
  const steps = [[230, 44, 56, 40, 62], [210, 40, 48, 38, 62], [200, 36, 44, 34, 58], [190, 34, 42, 32, 54], [180, 32, 40, 30, 50], [170, 30, 36, 28, 46],
    [160, 28, 34, 26, 44], [150, 26, 32, 24, 42], [140, 24, 30, 22, 40]];
  for (const [segW, segFs, sortFs, cbFs, titleFs] of steps) {
    // after the title and SPACE.l; from the left edge itself without one (a list popup's toolbar)
    const titleEnd = title ? left + advanceOf(title, titleFs) + SPACE.l : left;
    // the segments of every filter share one size
    const segLabels = NGUI.fitGroup(filters.flat(), segFs, 22, segW - 26, 2, { maxH: 96 });
    // the sort button is as wide as its longest key, so choosing another one moves nothing
    const sortTexts = sorts.map((text) => fitText(text, sortFs, 28, 420));
    const sortText = sortTexts[Math.max(0, sortAt)];
    const sortW = Math.max(300, ...sortTexts.map((s) => glyphsOf(s.text, s.fs) + 170));
    // no checkbox (check null: a list popup's toolbar without one): no room kept for it
    const cbText = check == null ? null : fitText(check, cbFs, 24, 400);
    const cbW = cbText ? 131 + glyphsOf(cbText.text, cbText.fs) + 46 : 0;
    // The segmented prefab keeps its three adjoining slots; composed groups use SPACE.l. Search and order share
    // UICharacterCollection/SeletControl/TopMenu/SearchBtn/Btn_Normal's own 150 x 116-unit body. Sort and order
    // also keep their prefab's 116-unit height; the segmented filters keep 104. Caption fitting tiers stay as before.
    const h = CONTROL.toolbar.h, iconW = 150;
    const nativeH = 116;
    const tools = row([...Array.from({ length: icons }, () => ({ w: iconW, h: nativeH })), { w: iconW, h: nativeH }, { w: sortW, h: nativeH },
      { w: iconW, h: nativeH }], { gap: SPACE.m });
    const cb = cbText ? 1 : 0;
    const groups = row([...(cb ? [{ w: cbW, h }] : []), ...filters.map(() => ({ w: 3 * segW, h })), tools], { gap: SPACE.l });
    const cbLeft = right - groups.w, offset = (box) => ({ ...box, x: box.x + cbLeft });
    const lead = cb + filters.length;  // the checkbox and the filters come before the tools
    const boxes = groups.boxes.slice(0, lead).map(offset);
    boxes.push(...tools.boxes.map((box) => offset({ ...box, x: groups.boxes[lead].x + box.x })));
    const centre = (box) => box.x + box.w / 2;
    const fits = cbLeft >= titleEnd;
    if (fits || segW === steps.at(-1)[0]) {
      const ends = filters.map((_, i) => boxes[cb + i].x + boxes[cb + i].w);
      return { segW, segLabels, sortText, sortW, cbText, cbW,
        order: centre(boxes.at(-1)), sort: centre(boxes.at(-2)), search: centre(boxes.at(-3)),
        icons: boxes.slice(boxes.length - 3 - icons, boxes.length - 3),
        seg1: ends[0], seg2: ends[1], cbLeft, titleFs, boxes, fits,
        // per filter: where its right edge is and its three captions
        filters: ends.map((end, i) => ({ right: end, labels: segLabels.slice(3 * i, 3 * i + 3) })) };
    }
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
    { box: [lay.cbLeft + lay.cbW / 2, segY, lay.cbW, CONTROL.toolbar.h], name: T("roster.merge"), pressed: view.merged === true,
      apply: (target) => { target.merged = !target.merged; } },
  ];
}

// Where the toolbar's controls stand in SeletControl's units (y up from TopMenu's origin): their visual centre line.
export const TOOLBAR_CENTRE_Y = -219 - 50.2;  // UICharacterCollection/SeletControl/TopMenu/FilterBtn's authored centre line

// What a toolbar shows of its state: one entry per filter of the layout (its node's name and the active segment), the
// checkbox, the sort button's funnel (a display setting filters the list) and the order arrow. The roster's own, from its view.
export const rosterToolbarState = (view) => ({
  filters: [{ name: "ModFilter", active: { all: 0, mods: 1, none: 2 }[view.mods] },
    { name: "RecruitFilter", active: { all: 0, yes: 1, no: 2 }[view.recruit] }],
  checked: view.merged, filtered: filtering(view.filters), ascending: view.order !== "desc" });

// Shared complete UICharacterCollection toolbar; callers retain their own list and title. ``state``: what it shows
// (``rosterToolbarState``), for a list popup's own.
export function toolbarTree(collection, checkbox, view, searching, lay = toolbarLayout(view), state = rosterToolbarState(view)) {
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
  // (a stand-in prefab without a node leaves it out)
  const filterBtn = find(menu, "FilterBtn");
  if (filterBtn) filterBtn.active = false;
  menu.children.push(...state.filters.map(({ name, active }, i) => segFilter(name, lay.filters[i].labels, active, lay.filters[i].right)));
  // search: the game's toggle, pressed while the search box is open
  const search = find(menu, "SearchBtn");
  for (const [name, on] of [["Btn_Normal", !searching], ["Btn_Pressed", searching]]) { const n = find(search, name); if (n) n.active = on; }
  // sort: the current key, and the funnel in blue while a display setting filters the list
  const sort = find(menu, "SortBtn");
  sort.children = sort.children.map((c) => c.name === "Btn_Normal" ? sortBody(collection, lay.sortText, lay.sortW, state.filtered) : c);
  // order: one arrow, down for descending (the prefab holds both, Icon_Up flipped)
  const orderBtn = find(menu, "OrderBtn");
  orderBtn.children = orderBtn.children.map((c) => c.name === "Btn_Normal" ? orderBody(collection, state.ascending) : c);
  // The sprite's own pivot supplies its local centre; every state is centred on the same row, without hand offsets.
  const centreY = TOOLBAR_CENTRE_Y; // the prefab's authored centre line stays.
  for (const [control, box] of [[search, lay.boxes.at(-3)], [sort, lay.boxes.at(-2)], [orderBtn, lay.boxes.at(-1)]]) {
    control.pos = [box.x + box.w / 2, centreY];
    for (const state of control.children) {
      const body = state.widgets.find((widget) => "sprite" in widget);
      if (!body) continue;
      const pivot = body.pivot ?? 4;
      state.pos = [((pivot % 3) / 2 - .5) * box.w, (.5 - Math.floor(pivot / 3) / 2) * box.h];
      body.w_ = box.w; body.h_ = box.h;
    }
  }
  // icon toggles before search (a list popup's Show Other Students, BL-176): the search button's body and states with
  // another icon, pressed while on; state.toggles: [{ name, sprite, atlas, size: [w, h], pressed }]
  (state.toggles || []).forEach((toggle, i) => {
    const box = lay.icons?.[i];
    if (!box) return;
    const t = clone(search);
    t.name = toggle.name;
    t.pos = [box.x + box.w / 2, centreY];
    for (const st of t.children) {
      st.active = (st.name === "Btn_Pressed") === !!toggle.pressed;
      for (const icon of st.children || []) {
        const w = icon.widgets?.find((x) => "sprite" in x);
        if (!w) continue;
        icon.name = "Icon";
        Object.assign(w, { sprite: toggle.sprite, atlas: toggle.atlas ?? w.atlas, ...(toggle.size ? { w_: toggle.size[0], h_: toggle.size[1] } : {}) });
      }
    }
    menu.children.push(t);
  });
  // checkbox toggle after the title (game: "unowned students only" in the gacha character list); none without one
  if (!lay.cbText) return menu;
  const cb = clone(checkbox);
  cb.name = "Merge";
  const menubar = find(menu, "Menubar");
  cb.pos = [lay.cbLeft - menubar.pos[0], centreY - menubar.pos[1]];
  Object.assign(cb.widgets.find((w) => "sprite" in w), { w_: lay.cbW, h_: CONTROL.toolbar.h, pivot: 3 });
  cb.children = cb.children.filter((c) => c.name !== "Locale_Text");
  for (const c of cb.children) {
    c.children = (c.children || []).filter((k) => k.name === "Check");
    c.active = (c.name === "Type_ON") === state.checked;
  }
  cb.children.push(node("Label", [131, 0], [label(lay.cbText.text, lay.cbText.fs, "#484e52/1.00", lay.cbW - 131, 50 * lay.cbText.lines, { pivot: 3, depth: 31 })]));
  menubar.children.push(cb);
  return menu;
}
