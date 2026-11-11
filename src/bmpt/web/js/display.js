// Display settings: the student list's filter and sort popup, the game's own (UIPopup_FilterSort). Filter and Sort
// tabs on the left; on the right the filter groups (UIFilterObjectList: title with its tag, dotted line, check boxes)
// under the list header with Reset All, or the sort keys as the game's radio buttons (UISortObjectListUnit); Cancel
// and Confirm below. It edits a draft of the view; Confirm hands the draft back.
import { opened, closed, FOOTER as FOOTERS, FOOTER_DEFAULT, footerWidth } from "./popup.js";
import { resizeButton } from "./button-layout.js";
import { scrollbar } from "./scrollbar.js";
import * as NGUI from "./ngui.js";
import { T } from "./i18n.js";
import { FILTERS, NATURAL, SORTS } from "./roster-order.js";
import { BACKDROP, POPUP_FRAME, POPUP_TYPE } from "./ui-tokens.js";
import { $, S, clone, glyphsOf, hitButton } from "./util.js";

const { node, find } = NGUI;
const Z = 3000;
// The frame one class narrower than the prefab's 2810 (owner, 2026-11-11, BL-176: "窗口过宽了，往回调窄一到两号"): the L
// class, 2098. Everything at the frame's sides moves in by HALF, and what spans the panels is CUT narrower.
const PREFAB_W = 2810, CUT = PREFAB_W - POPUP_FRAME.L.w, HALF = CUT / 2;
// the filter list's visible area under its header (Filter/BG_List below Filter/BG_Tag), popup units
const LIST = { cx: 212, top: 377, bottom: -404, w: 2244 - CUT };
// one row of check boxes per group: the bottom line 36 under the row (as under the prefab's second row), the
// list's 12 of padding between groups
const GROUP = { x: -1109 + HALF, top: -150, pitch: 308, unitX: [165, 712, 1259, 1806], unitY: -76, line: -176 };
// the sort keys' grid (Sort_Normal/Option_Normal/Grid), relative to the Sort panel: three to a line in the narrower panel
const SORT = { titleY: 446.5, x: -1015 + HALF, y: 330, cell: [480, 174], perLine: 3 };
// where the prefab's controls stand once the frame is narrower: the close button, the tabs, Reset All
const CLOSE = [1323 - HALF, 639], TAB_X = -1143 + HALF, RESET = [212 + 874 - HALF, 516 - 66];
// the text one size under the game's (POPUP_TYPE, BL-176): the title, the tabs, the count, the groups' titles and options
const TEXT = { title: POPUP_TYPE.title, tab: { fs: 48, min: 36 }, count: { fs: 48, min: 36 }, reset: { fs: 42, min: 30 },
  heading: POPUP_TYPE.card.title, option: { fs: 40, min: 30 }, sortOption: POPUP_TYPE.card.option };
// (a node a prefab build lacks is left alone)
const moveX = (n, dx) => { if (n?.pos) n.pos = [n.pos[0] + dx, n.pos[1]]; };
const widen = (n, dw) => { for (const w of n?.widgets || []) if (w.w_ != null) w.w_ += dw; };
// the prefab's frame CUT narrower: the frame, its sides' decorations and controls, the panels and their headers (exported
// for the tests)
export function narrow(popup) {
  const at = (n, path) => (n ? find(n, path) : null);
  const bg = at(popup, "Base/BG");
  widen(bg, -CUT);
  moveX(at(bg, "Texture (1)"), -HALF); moveX(at(bg, "Texture_Top"), HALF); moveX(at(bg, "Btn_Close"), -HALF);
  // the tabs' outline (pivot top left) and its white panel keep their left margin
  const tabBg = at(popup, "TabBg");
  moveX(tabBg, HALF); widen(tabBg, -CUT); widen(at(tabBg, "Bg_W"), -CUT);
  moveX(at(popup, "Tab"), HALF);
  widen(at(popup, "BG"), -CUT);
  const tag = at(popup, "Filter/BG_Tag");
  widen(tag, -CUT); moveX(at(tag, "Label_Locale_ListAll"), HALF); moveX(at(tag, "Btn_Reset"), -HALF);
  // the header's triangle pattern (Img_Deco_Weapon, its rounded corners on the left, fading to the right) from the header's
  // left end: the prefab stands it from the right end outwards, past the header and the frame (owner, 2026-11-11, BL-176)
  const deco = at(tag, "DecoTex_Normal"), tagW = tag?.widgets.find((w) => w.w_ != null)?.w_;
  if (deco?.pos && tagW) deco.pos = [-tagW / 2, deco.pos[1]];
  widen(at(popup, "Filter/BG_List"), -CUT); widen(at(popup, "Sort/BG_List"), -CUT);
  const bar = at(popup, "Sort/Scroll View/Sort_Normal/Title_Normal");
  widen(bar, -CUT); moveX(at(bar, "Deco_R"), -HALF); moveX(at(bar, "Deco_L"), HALF);
}

let layout = null, draft = null, tab = "filter", count = null, done = null, scroll = 0, token = 0;
let sortKeys = SORTS, sortOnly = false;

const optionLabel = {
  favorite: { yes: () => T("fav.on"), no: () => T("display.fav.off") },
  portrait: { official: () => T("status.original"), replaced: () => T("status.replaced") },
  lobby: { yes: () => T("model.has"), no: () => T("model.none") },
  model: { yes: () => T("model.has"), no: () => T("model.none") },
};
const groupTitle = { favorite: () => T("fav.label"), portrait: () => T("tab.portrait"), lobby: () => T("tab.lobby"),
  model: () => T("tab.model") };

const setText = (n, text, fsMax, fsMin, w) => NGUI.fitLabel(n.widgets.find((x) => "text" in x), text, fsMax, fsMin, w);
// Options side by side share one size (NGUI.fitGroup): the largest from fsMax at which every text fits w after wrapping.
// widgets: for each text, the label widgets that show it.
function shareSize(widgets, texts, fsMax, fsMin, w) {
  const fits = NGUI.fitGroup(texts, fsMax, fsMin, w);
  widgets.forEach((shown, i) => shown.forEach((x) => Object.assign(x, { text: fits[i].text, fontSize: fits[i].fs, w_: Math.max(x.w_ || 0, w) })));
}

// --- the popup frame: backdrop, title, tabs, the shown panel, the buttons ---
function frameTree() {
  const t = clone(find(layout.fs, "Center"));
  find(t, "BlackBg/BlackBgClickArea").widgets.find((w) => "sprite" in w).color = BACKDROP;
  const popup = find(t, "Popup");
  narrow(popup);
  const title = find(popup, "Base/BG/Label_Title_Locale");
  const tf = setText(title, T("display.title"), TEXT.title.fs, TEXT.title.min, 1100);
  find(title, "Line").widgets[0].w_ = glyphsOf(tf.text, tf.fs) + 14;
  const tabLabels = [];
  for (const [widget, id, key] of [["FilterWidget/Filter", "filter", "display.tab.filter"], ["SortWidget/Sort", "sort", "display.tab.sort"]]) {
    const n = find(popup, `Tab/${widget}`);
    n.active = !sortOnly || id === "sort";
    for (const state of ["Tab_On", "Tab_Off"]) {
      tabLabels.push([find(n, `${state}/Label_Locale`).widgets.find((x) => "text" in x), T(key)]);
      find(n, state).active = (state === "Tab_On") === (id === tab);
    }
  }
  // the tabs' captions share one size
  NGUI.fitLabels(tabLabels.map(([widget]) => widget), tabLabels.map(([, text]) => text), TEXT.tab.fs, TEXT.tab.min, 360);
  const filter = find(popup, "Filter"), sort = find(popup, "Sort");
  filter.active = tab === "filter";
  sort.active = tab === "sort";
  // the filter panel: header with the count and Reset All; the groups scroll in a list drawn beside the tree
  setText(find(filter, "BG_Tag/Label_Locale_ListAll"), T("display.count", { n: count(draft) }), TEXT.count.fs, TEXT.count.min, 1400 - CUT);
  setText(find(filter, "BG_Tag/Btn_Reset/LabelLocale"), T("display.reset"), TEXT.reset.fs, TEXT.reset.min, 300);
  filter.children = filter.children.filter((c) => ["BG_List", "BG_Tag"].includes(c.name));
  // the sort panel: the title bar and one radio button per key
  const normal = find(sort, "Scroll View/Sort_Normal");
  const bar = clone(find(normal, "Title_Normal"));
  bar.pos = [1, SORT.titleY];
  // the scroll view's own panel draws above the list's white body (BG_List, depth 16)
  const lift = (n) => { for (const w of n.widgets) if ("depth" in w) w.depth += 12; (n.children || []).forEach(lift); };
  lift(bar);
  setText(find(bar, "Label_Locale_UnitList"), T("display.sortBy"), TEXT.heading.fs, TEXT.heading.min, 1600 - CUT);
  const unit = find(normal, "Option_Normal/Grid/UISortObjectListUnit");
  const options = sortKeys.map((key, i) => sortUnit(unit, key, i));
  // the sort keys are options side by side: one size for all of them (each key's two labels, dark and dimmed, alike)
  shareSize(options.map((u) => ["On/Label_On", "Label_On"].map((path) => find(find(u, "Label_Text"), path).widgets.find((x) => "text" in x))),
    sortKeys.map((key) => T(`sort.${key}`)), TEXT.sortOption.fs, TEXT.sortOption.min, 360);
  sort.children = [find(sort, "BG_List"), bar, ...options];
  find(popup, "Btn_Section/Btn_Disable").active = false;
  // the footer buttons on the prefab's Btn_Section line, at the size every popup's footer has (XS, BL-176; the prefab's
  // are 521 x 180), the bodies as wide as the captions need and the shared footer gap apart (footerPlaces)
  const footer = FOOTERS[FOOTER_DEFAULT], { control, caption } = footer, captions = [T("picker.ok"), T("popup.cancel")];
  const bodyW = footerWidth(captions, footer), places = footerPlaces(bodyW);
  const labels = [];
  for (const name of ["Btn_Ok", "Btn_Cancel"]) {
    const button = find(popup, `Btn_Section/${name}`), label = find(button, "Label_Locale").widgets.find((x) => "text" in x);
    button.pos = [places[name], button.pos[1]];
    resizeButton(find(button, "BG (1)"), bodyW, control.h);
    Object.assign(label, { w_: bodyW - 2 * control.pad, h_: caption.h });
    labels.push(label);
  }
  // side by side, one caption size
  NGUI.fitLabels(labels, captions, caption.fs, caption.fsMin, bodyW - 2 * control.pad, caption.lines, { maxH: caption.h });
  return t;
}

// the footer's two bodies bodyW wide, the shared gap apart, centred: Cancel on the left, Confirm on the right
const footerPlaces = (bodyW) => ({ Btn_Cancel: -(bodyW + POPUP_FRAME.footer.gap) / 2, Btn_Ok: (bodyW + POPUP_FRAME.footer.gap) / 2 });
const footerBodyW = () => footerWidth([T("picker.ok"), T("popup.cancel")], FOOTERS[FOOTER_DEFAULT]);
// a sort key: the game's radio button with its label (the Label_Text form of UISortObjectListUnit)
function sortUnit(template, key, i) {
  const u = clone(template);
  u.name = `Sort_${key}`;
  u.pos = [SORT.x + (i % SORT.perLine) * SORT.cell[0], SORT.y - Math.floor(i / SORT.perLine) * SORT.cell[1]];
  for (const c of u.children) c.active = c.name === "Label_Text";
  const opt = find(u, "Label_Text");
  // the key's underline (504 at the prefab's 553 pitch) keeps the same 49 clear of the next key at this pitch
  const line = find(opt, "Line")?.widgets[0];
  if (line) line.w_ = SORT.cell[0] - 49;
  const on = draft.sort === key;
  find(opt, "On").active = on;
  find(opt, "Label_On").active = !on; // the dimmed label under the button when off, the dark one with it when on
  return u;
}

// the filter groups: UIFilterObjectList with one check box per option (UIFilterObjectListUnit)
function groupsTree() {
  const out = node("Groups", [0, 0]), labels = [], texts = [];
  Object.keys(FILTERS).forEach((group, gi) => {
    const g = clone(layout.group);
    g.name = `Group_${group}`;
    g.pos = [GROUP.x, GROUP.top - gi * GROUP.pitch];
    const title = find(g, "Label_Title");
    widen(title, -CUT); widen(find(title, "LineDeco"), -CUT); widen(find(g, "Table_Align/Line_Deco_Bottom"), -CUT);
    setText(title, groupTitle[group](), TEXT.heading.fs, TEXT.heading.min, 1800 - CUT);
    find(g, "Table_Align/Line_Deco_Bottom").pos[1] = GROUP.line;
    const units = find(g, "Table_Align/Table_ListUnit");
    const template = units.children[0];
    units.children = FILTERS[group].map((value, i) => {
      const u = clone(template);
      u.name = `Unit_${value}`;
      u.pos = [GROUP.unitX[i], GROUP.unitY];
      const on = (draft.filters[group] || []).includes(value);
      find(u, "UIFilter_CheckBox/CheckBoxIcon").active = on;
      find(u, "UIFilter_CheckBox/CheckBoxIcon_Disable").active = !on;
      const sw = find(u, "SwitchObject_Columns_4");
      for (const c of sw.children) c.active = c.name === "Label_Text";
      labels.push([find(sw, "Label_Text").widgets.find((x) => "text" in x)]);
      texts.push(optionLabel[group][value]());
      return u;
    });
    out.children.push(g);
  });
  // every option of every group is one kind of control: one size for all of them
  shareSize(labels, texts, TEXT.option.fs, TEXT.option.min, 380);
  return out;
}

function toggle(group, value) {
  const on = new Set(draft.filters[group] || []);
  if (on.has(value)) on.delete(value); else on.add(value);
  draft.filters = { ...draft.filters, [group]: FILTERS[group].filter((v) => on.has(v)) };
  if (!draft.filters[group].length) delete draft.filters[group];
  render();
}

// the list header's decoration (BG_Tag/DecoTex_Normal, pivot top left at the header's right end) runs on past the
// popup; the game's panel clips it at the popup's frame, so only the part over the frame's right margin shows
function clipToFrame(view, frame) {
  const deco = view.nodes.get("Center/Popup/Filter/BG_Tag/DecoTex_Normal")?.els[0];
  if (!deco || !frame) return;
  const [l, , w] = NGUI.rect(deco), [fl, , fw] = NGUI.rect(frame);
  const over = l + w - (fl + fw);
  if (over > 0) deco.style.clipPath = `inset(0 ${over.toFixed(2)}px 0 0)`;
}

const stagePx = ([x, y]) => [960 + x * S, 540 - y * S];
function hitAt(host, [x, y], [w, h], fn, name) {
  const [l, t] = stagePx([x - w / 2, y + h / 2]);
  return hitButton(host, [l, t, w * S, h * S], fn, name, Z + 200);
}

async function render() {
  const my = ++token;
  const next = document.createElement("div");
  const view = await NGUI.render(frameTree(), next, { scale: S, origin: [960, 540], z: Z });
  // a click on the backdrop cancels; the popup's body takes the clicks that miss its controls (UIEventTrigger)
  const back = view.nodes.get("Center/BlackBg/BlackBgClickArea")?.els[0];
  if (back) { back.style.pointerEvents = "auto"; back.addEventListener("click", () => finish(null)); }
  const body = view.nodes.get("Center/Popup/Base/BG")?.els[0];
  if (body) body.style.pointerEvents = "auto";
  clipToFrame(view, body);
  hitAt(next, CLOSE, [120, 120], () => finish(null), T("display.close"));
  [["filter", 474.5], ["sort", 337.7]].forEach(([id, y]) => {
    if (id !== tab && !sortOnly) hitAt(next, [TAB_X, y], [420, 136], () => { tab = id; scroll = 0; render(); }, T(`display.tab.${id}`));
  });
  // the footer's bodies (Btn_Section at -547, each body 10 under its button's centre)
  const bodyW = footerBodyW(), places = footerPlaces(bodyW), bodyH = FOOTERS[FOOTER_DEFAULT].control.h;
  hitAt(next, [places.Btn_Ok, -557], [bodyW, bodyH], () => finish(draft), T("picker.ok"));
  hitAt(next, [places.Btn_Cancel, -557], [bodyW, bodyH], () => finish(null), T("popup.cancel"));
  if (tab === "filter") {
    hitAt(next, RESET, [454, 106], () => { draft.filters = {}; render(); }, T("display.reset"));
    await renderGroups(next);
  } else {
    sortKeys.forEach((key, i) => {
      const x = 211 + SORT.x + (i % SORT.perLine) * SORT.cell[0], y = -2.5 + SORT.y - Math.floor(i / SORT.perLine) * SORT.cell[1];
      hitAt(next, [x + 200, y], [SORT.cell[0] - 20, 150], () => {
        if (draft.sort !== key) draft = { ...draft, sort: key, order: NATURAL[key] };
        render();
      }, T(`sort.${key}`));
    });
  }
  if (my !== token || $("display").hidden) return;
  $("display").replaceChildren(...next.childNodes);
  const list = $("display").querySelector(".display-rows");
  if (list) {
    list.scrollTop = scroll;
    list.addEventListener("scroll", () => { scroll = list.scrollTop; syncBar(list); });
    syncBar(list);
  }
}

// the groups scroll inside the list area (wheel, trackpad, scrollbar drag of the OS is hidden: the game's bar shows)
async function renderGroups(host) {
  const [l, t] = stagePx([LIST.cx - LIST.w / 2, LIST.top]);
  const w = LIST.w * S, h = (LIST.top - LIST.bottom) * S;
  const area = document.createElement("div");
  area.className = "settings-rows display-rows";
  Object.assign(area.style, { left: `${l}px`, top: `${t}px`, width: `${w}px`, height: `${h}px`, zIndex: Z + 20 });
  const inner = document.createElement("div");
  inner.className = "settings-rows-inner";
  const groups = Object.keys(FILTERS).length;
  const contentH = (-GROUP.top + (groups - 1) * GROUP.pitch - GROUP.line + 40) * S;
  inner.style.height = `${Math.max(h, contentH)}px`;
  area.appendChild(inner);
  host.appendChild(area);
  const origin = [w / 2, 0];
  await NGUI.render(groupsTree(), inner, { scale: S, origin, z: Z + 30 });
  Object.keys(FILTERS).forEach((group, gi) => {
    FILTERS[group].forEach((value, i) => {
      const x = GROUP.x + GROUP.unitX[i], y = GROUP.top - gi * GROUP.pitch + GROUP.unitY;
      // the check box and its label (the unit's AlignTableSpace: from the box to the end of the label)
      hitButton(inner, [origin[0] + (x - 130) * S, -(y + 64) * S, 520 * S, 128 * S], () => toggle(group, value), optionLabel[group][value](), Z + 60);
    });
  });
  const bar = await scrollbar(host, [l + w - 13 - 63 * S, t + 10, 126 * S, h - 20], { z: Z + 80, className: "display-bar" });
  bar.sync(h, contentH, scroll);
}

function syncBar(list) {
  const bar = $("display").querySelector(".display-bar");
  bar?.sync(list.clientHeight, list.scrollHeight, list.scrollTop);
}

function onKey(ev) {
  if (!ev.defaultPrevented && ev.key === "Escape") { ev.preventDefault(); finish(null); }
}

function finish(result) {
  const host = $("display");
  host.hidden = true;
  host.classList.remove("over");
  closed(host);
  host.innerHTML = "";
  removeEventListener("keydown", onKey);
  const fn = done;
  done = null;
  fn?.(result);
}

// open({view, count, onDone}): view is the roster's view (sort, order, filters); count(draft) the number of cards the
// draft shows; onDone(draft) with the confirmed draft, or null when cancelled
export async function open({ view, count: counter, onDone, sorts = SORTS, sortOnly: onlySort = false }) {
  await load();
  sortKeys = sorts;
  sortOnly = onlySort;
  if (sortOnly) tab = "sort";
  draft = { sort: view.sort, order: view.order, filters: clone(view.filters || {}) };
  count = counter;
  done = onDone;
  scroll = 0;
  // opened from a dialog (#dialog, z-index 2600): drawn over it, and over it on the popup stack (opened() below)
  $("display").classList.toggle("over", !$("dialog")?.hidden);
  $("display").hidden = false;
  opened($("display"), () => finish(null));
  addEventListener("keydown", onKey);
  try { await render(); } catch (err) { finish(null); throw err; }
}

let loading = null;
const load = () => (loading ||= Promise.all([NGUI.layout("UIPopup_FilterSort"), NGUI.layout("UIFilterObjectList")])
  .then(([fs, group]) => { layout = { fs, group }; }, (err) => { loading = null; throw err; }));

// draw both tabs once off screen, so the first open finds the frame's images made (the popup's large panels take
// a while to slice the first time); once per session, started when the pointer comes to the sort button
let warming = null;
export function warm() {
  warming ||= drawAhead().catch((err) => { warming = null; console.warn("display warm-up", err); });
  return warming;
}
async function drawAhead() {
  await load();
  if (!$("display").hidden) return;
  const keep = [draft, tab, count, sortKeys, sortOnly];
  sortKeys = SORTS; sortOnly = false;
  draft = { sort: SORTS[0], order: NATURAL[SORTS[0]], filters: {} };
  count = () => 0;
  const trees = [];
  for (const id of ["filter", "sort"]) { tab = id; trees.push(frameTree()); }
  trees.push(groupsTree());
  [draft, tab, count, sortKeys, sortOnly] = keep;
  const scratch = document.createElement("div");
  await Promise.all(trees.map((t) => NGUI.render(t, scratch, { scale: S, origin: [960, 540] })));
}

export const isOpen = () => !$("display").hidden;

// Prefabs and derived UI values belong to the selected installation.
NGUI.onResourceChange?.(() => { layout = null; token++; });
