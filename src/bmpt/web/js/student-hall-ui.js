// Modal shells, controls and cards reuse UIPopup_Sort, UICharacterCollection and the existing switch-card renderer.
import * as NGUI from "./ngui.js";
import * as Popup from "./popup.js";
import * as Loading from "./loading.js";
import { withTimeout } from "./async.js";
import * as Buttons from "./game-buttons.js";
import { searchTree, orderBody, toolbarLayout, toolbarTree } from "./roster-widgets.js";
import { clone, S, hitButton } from "./util.js";
import { T } from "./i18n.js";
import { bindScale } from "./student-hall-scale.js";
import { POPUP, SMALL_BUTTON, CONTROL, TYPE, SPACE, captionMinimum } from "./ui-tokens.js";
import { stack as layoutStack } from "./ui-grid.js";
// A Hall small button is a buttonS control (docs/design-system.md): its height, minimum width and caption size are the system's, the
// caption's floor the body role's. Its fit insets are the Common_Btn_BG body's (SMALL_BUTTON), not a scale value.
const BUTTON = CONTROL.buttonS, BUTTON_FLOOR = TYPE.body.min;
let assets;
const visibleReadiness = new WeakMap(), virtualControls = new WeakMap();
function pictureReadiness(host, picture, view) {
  visibleReadiness.set(host, async (signal) => {
    picture?.bump?.(); picture?.start?.();
    await withTimeout(Promise.all(view.thumbnails || []), Infinity, "Hall thumbnail", { signal });
    const decoded = await NGUI.decoded(host, Infinity, null, { signal });
    if (!decoded && !signal?.aborted) throw new Error("Hall thumbnail decode failed");
  });
}
export async function prepareVisible(host, signal) {
  if (signal?.aborted) return;
  const own = visibleReadiness.get(host);
  if (own) await own(signal);
  else await Promise.all([...host.children].map((child) => prepareVisible(child, signal)));
}
function settleDrag(host, signal) {
  const pointerOwned = () => host.querySelector('[data-reordering="true"]') || host.querySelector('[data-pointer-owned="true"]');
  if (!pointerOwned()) return Promise.resolve();
  return new Promise((resolve) => {
    const surface = host.ownerDocument || host, window = surface.defaultView || globalThis;
    const listeners = [[surface, "pointerup"], [surface, "pointercancel"], [surface, "lostpointercapture"], [surface, "visibilitychange"], [window, "blur"]];
    const done = () => { for (const [target, event] of listeners) target.removeEventListener?.(event, check, true); signal.removeEventListener("abort", done); resolve(); };
    const check = () => queueMicrotask(() => { if (!pointerOwned()) done(); });
    for (const [target, event] of listeners) target.addEventListener?.(event, check, true);
    signal.addEventListener("abort", done, { once: true });
    if (signal.aborted) done();
  });
}

export async function ready() {
  if (!assets) {
    await Promise.all([Popup.ready(), Buttons.ready()]);
    const [sort, collection, checkbox, formation] = await Promise.all([NGUI.layout("UIPopup_Sort"), NGUI.layout("UICharacterCollection"), NGUI.layout("UIPopup_GachaCharList.Btn_Checkbox"), NGUI.layout("UIPopup_Formation_CharEdit")]);
    assets = { sort, collection, checkbox, formation };
  }
  return assets;
}
export function createModal(title, onClose, { level = 0 } = {}) {
  const host = document.createElement("div"), stage = document.getElementById("stage"), previous = document.activeElement;
  host.className = "popup-host stage-host"; host.style.zIndex = String(1250 + level * 50);
  host.tabIndex = -1; host.setAttribute("role", "dialog"); host.setAttribute("aria-modal", "true"); host.setAttribute("aria-label", title);
  const excluded = new Set(["prompt", "dialog", "display", "consent", "settings", "loading-boot"]);
  const held = [...stage.children].filter((el) => !excluded.has(el.id)).map((el) => [el, el.inert]);
  held.forEach(([el]) => { el.inert = true; }); stage.append(host); host.focus();
  // Tab stays in the modal through the shared trap of the popup stack (popup.js), which Popup.opened below joins
  const controller = new AbortController(); let revision = 0, closed = false, drawing = null, visibleDrawing = null;
  const api = { host, signal: controller.signal, live: () => !closed,
    close() {
      if (closed) return; closed = true; revision++; controller.abort(); drawing?.abort(); visibleDrawing?.abort(); Popup.closed(host); host.remove();
      held.forEach(([el, inert]) => { if (el.isConnected) el.inert = inert; });
      if (previous?.isConnected && !previous.inert) previous.focus?.();
    },
    async draw(build, actions = [], { width = 2800, height = 1450, retainedFooter = false, captionSite = "hall.modal" } = {}) {
      drawing?.abort(); const nextDrawing = new AbortController(); drawing = nextDrawing;
      const drawSignal = nextDrawing.signal, token = ++revision;
      const cancelled = new Promise((resolve) => drawSignal.addEventListener("abort", resolve, { once: true }));
      const live = () => !closed && token === revision && !drawSignal.aborted;
      try { await Loading.hold(async () => {
        await ready(); if (!live()) return;
        const layer = document.createElement("div"); layer.className = "popup-layer";
        const tree = modalFrame(title, width, height, retainedFooter ? [] : actions, captionSite);
        const frame = await NGUI.render(tree, layer, { scale: S, origin: [960, 540], signal: drawSignal });
        if (!live()) return;
        Popup.closable(frame, host, onClose);
        Popup.hit(layer, frame, "Center/Popup/Base/BG/Btn_Close", onClose, { name: T("display.close") });
        await build(layer, live, drawSignal);
        if (retainedFooter) {
          // The memorial editor cannot retain a full candidate row above the taller queue and shared footer.
          const actionWidth = Math.min(280, (width * S - 180) / Math.max(1, actions.length) - 20);
          const right = 960 + width * S / 2 - 70, bottom = 540 + (height / 2 - 120) * S;
          const footerSize = retainedButtonSize(actions.map((action) => action.label), actionWidth, 66);
          await Promise.all(actions.map((action, index) => retainedButton(layer, `Footer${index}`, action.label,
            [right - (actions.length - index) * (actionWidth + 20), bottom, actionWidth, 66], action.fn, { enabled: action.enabled !== false, semantic: action.semantic, size: footerSize })));
        } else actions.forEach((action, index) => {
          const hit = Popup.hit(layer, frame, `Center/Popup/Btn/Footer${index}/BG (1)`, action.fn, { name: action.label, action: `Footer${index}` });
          if (hit) { hit.disabled = action.enabled === false; hit.dataset.focusKey = `Footer${index}`; }
        });
        await document.fonts?.ready;
        const scrollSnapshot = () => new Map([...host.querySelectorAll('[data-scroll]')].map((el) => [el.dataset.scroll, [el.scrollLeft, el.scrollTop]]));
        let oldScroll;
        do {
          oldScroll = scrollSnapshot();
          for (const el of layer.querySelectorAll('[data-scroll]')) {
            virtualControls.get(el)?.sync();
          }
          // Detached IntersectionObservers cannot start capture. Only visible cells (including nested strips)
          // enter this barrier; the overscan neighbours retain their lazy captures.
          await prepareVisible(layer, drawSignal);
          const decoded = await NGUI.decoded(layer, Infinity, host, { signal: drawSignal });
          if (!live()) return;
          if (!decoded) throw new Error("Hall modal decode failed");
          await settleDrag(host, drawSignal); if (!live()) return;
        } while (JSON.stringify([...oldScroll]) !== JSON.stringify([...scrollSnapshot()]));
        const focusedElement = document.activeElement, focused = focusedElement?.dataset?.focusKey;
        const cursor = focusedElement?.selectionStart, value = focusedElement?.value;
        visibleDrawing?.abort(); visibleDrawing = nextDrawing; if (drawing === nextDrawing) drawing = null;
        host.replaceChildren(layer);
        for (const el of host.querySelectorAll('[data-scroll]')) {
          virtualControls.get(el)?.commit();
        }
        const nextFocus = focused && [...host.querySelectorAll('[data-focus-key]')].find((el) => el.dataset.focusKey === focused);
        if (nextFocus) {
          if (value != null && "value" in nextFocus) nextFocus.value = value;
          nextFocus.focus(); if (typeof cursor === "number") nextFocus.setSelectionRange?.(cursor, cursor);
        }
        Popup.opened(host, onClose);
      }, { until: cancelled }); }
      catch (error) { if (!drawSignal.aborted) throw error; }
      finally { if (visibleDrawing !== nextDrawing) { nextDrawing.abort(); if (drawing === nextDrawing) drawing = null; } }
    },
  };
  Popup.opened(host, onClose); return api;
}
export function modalFrame(title, width, height, actions, captionSite = "hall.modal") {
  const tree = Popup.popupFrame(title, width, height, { buttons: actions.map((action, index) => ({ ...action,
    name: `Footer${index}`, semantic: action.semantic || (action.primary ? "confirm" : "secondary") })), captionSite });
  if (actions.length) {
    const row = NGUI.find(tree, "Popup/Btn");
    row.children = row.children.map((child, index) => actions[index].enabled === false ? Buttons.disable(child) : child);
  }
  return tree;
}
export const modalContentBottom = (height, actions = 1) => 540 - (Popup.footerTop(height, actions) + POPUP.clearance) * S;
// UIPopup_Sort's title is 64 below the top, its underline another 52 down, with half its 12-unit height below that.
// Under the title the picker is a stack: the toolbar's bar (140 units at the toolbar's scale), the search field when it is open (60)
// and the list, flush against one another (the bar and the field are one panel with the list, no gap between).
export function portraitPickerLayout(view, searching) {
  const width = 1320, lay = toolbarLayout(view, { title: "" });
  const scale = Math.min(1660 / 2840, width / (1412 - lay.cbLeft + SPACE.l));
  const titleBottom = 540 - (POPUP.frameY + 1300 / 2 - (64 + 52 + 6)) * S;
  const head = layoutStack([{ w: width, h: 140 * scale }, ...searching ? [{ w: width, h: 60 }] : []], { x: 300, y: titleBottom, w: width });
  const [bar, field] = head.boxes, top = titleBottom + head.h;
  return { titleBottom, scale, toolbar: { x: 300, y: bar.y + bar.h / 2, width }, search: field ? [field.x, field.y, field.w] : [300, top, width],
    list: [300, top, width, modalContentBottom(1300) - top] };
}
export const backgroundPickerBox = () => [130, modalContentBottom(1450, 2) - 650, 760, 650];
export function portraitRowLayout() {
  const name = Popup.LIST_CARD_NAME, bar = Math.max(88, name.padY + name.lines * name.fs * name.pitch);
  // Use the row's 16 free pixels above the artwork before growing it; reserve the native horizontal scrollbar's
  // painted 20-unit band (SPACE.m, scrollbarTrack) below the button. Its viewport gains the card's existing 12px hit outset
  // on the left, keeping the artwork and action's stage x unchanged while admitting the wider body.
  // The row is a stack: the artwork down to the foot of its name strip, 8 px (the card's gap) and the action's body (a buttonS
  // control), then the scrollbar's band.
  const cardY = 600 * .46 / 2;
  const column = layoutStack([{ w: 0, h: cardY + (600 / 2 + 10 + bar) * .46 }, { w: 0, h: BUTTON.h * S }], { gap: 8 });
  const height = column.h + SPACE.m * S;
  return { height, pitch: height + 4, cardX: 105 + 12, cardY, stripX: 240 - 12, stripW: 960 + 12 };
}
// Keep a caller's centre and width; only the minimum width and the shared small-button height constrain its body.
// bodyHeight: a site whose neighbour sets the height (the portrait toolbar's Reset beside its slider).
export function buttonBox([x, y, width, height], bodyHeight = BUTTON.h * S) {
  const w = Math.max(BUTTON.minW * S, width), h = bodyHeight;
  return [x + (width - w) / 2, y + (height - h) / 2, w, h];
}
export const buttonSize = (captions, width) => NGUI.fitGroup(captions, BUTTON.fs, BUTTON_FLOOR,
  Math.max(BUTTON.minW, width / S) - SMALL_BUTTON.fitInset, 2,
  { maxH: BUTTON.h - SMALL_BUTTON.boxInsetH })[0]?.fs;

// The memorial editor's footer, candidate Add and queue Remove still hit the full-row stop rule.
// Their existing bodies/captions stay until their containing layout can change.
export const retainedButtonSize = (captions, width, height) => NGUI.fitGroup(captions, 40, 26, width / S - 60, 2, { maxH: height / S - 20 })[0]?.fs;
export const retainedButton = (host, name, caption, box, action, opts = {}) => drawButton(host, name, caption, box, action,
  { ...opts, fs: opts.size ?? 40, min: opts.size ?? 26, retained: true });

// size: a row's shared caption size (buttonSize). Fit first with the body's height as well as its width.
// height: the body height in stage pixels where the site sets it (buttonBox); the token's otherwise.
export async function button(host, name, caption, box, action, opts = {}) {
  await ready();
  const placed = buttonBox(box, opts.height), fit = NGUI.fitGroup([caption], opts.size ?? BUTTON.fs, opts.size ?? BUTTON_FLOOR,
    placed[2] / S - SMALL_BUTTON.fitInset, 2, { maxH: placed[3] / S - SMALL_BUTTON.boxInsetH })[0];
  if (fit.overflow || fit.fs < captionMinimum(opts.captionSite, document.documentElement.lang, BUTTON_FLOOR)) throw new RangeError(`Hall small button cannot fit: ${name}`);
  return drawButton(host, name, caption, placed, action, { ...opts, fs: fit.fs, min: fit.fs, fit });
}
async function drawButton(host, name, caption, [x, y, width, height], action,
  { enabled = true, semantic = "secondary", fs, min, fit = null, retained = false } = {}) {
  await ready();
  const tree = Buttons.smallButton(name, 0, 0, caption, { w: width / S, h: height / S, fs, min, enabled, semantic, depth: 65 });
  if (fit) Object.assign(NGUI.find(tree, "Label_Locale").widgets[0], { text: fit.text, fontSize: fit.fs });
  const view = await NGUI.render(tree, host, { scale: S, origin: [x + width / 2, y + height / 2] });
  const path = NGUI.find(tree, "BG (1)") ? `${name}/BG (1)` : name;
  const hit = Popup.hit(host, view, path, action, { name: caption, action: name, z: 90 });
  if (hit) { hit.disabled = !enabled; hit.dataset.focusKey = name; hit.dataset.retainedButton = String(retained); }
  return hit;
}
export async function caption(host, value, [x, y, width], { size = 40, align = 1 } = {}) {
  await ready();
  const template = clone(NGUI.find(assets.sort, "Center/Popup/Base/BG/Label_Title_Locale"));
  template.children = []; template.pos = [0, 0]; template.name = "Caption";
  const widget = template.widgets.find((w) => "text" in w);
  const fit = NGUI.fitText(value, size, 26, width / S, 3);
  Object.assign(widget, { text: fit.text, fontSize: fit.fs, w_: width / S, h_: fit.fs * 1.25 * fit.lines, pivot: 3, align, depth: 40 });
  return NGUI.render(template, host, { scale: S, origin: [x, y] });
}
export function area(host, box, height, key, { width = box[2], horizontal = false } = {}) {
  const inner = Popup.scrollArea(host, box, height, { horizontal, contentW: width });
  inner.parentElement.dataset.scroll = key; return inner;
}
// onEscape: Escape in the box closes the search first, as the roster's does (not the key that ends a conversion)
export async function search(host, query, onChange, [x, y, width] = [130, 172, 560], { onEscape = null } = {}) {
  await ready(); const tree = searchTree(assets.collection); tree.name = "HallSearch"; tree.pos = [0, 0];
  const name = NGUI.find(tree, "SearchName"); name.pos = [0, 0];
  const area = NGUI.find(name, "SearchNameArea"); area.pos = [0, 0];
  const body = area.widgets.find((w) => "sprite" in w); if (body) { body.w_ = width / S; body.h_ = 90; body.pivot = 3; }
  area.children = area.children.filter((child) => child.name === "InputField");
  const inputTree = NGUI.find(area, "InputField"); if (inputTree) inputTree.children = [];
  await NGUI.render(tree, host, { scale: S, origin: [x, y + 30] });
  const input = document.createElement("input"); input.className = "roster-search"; input.type = "search";
  Object.assign(input.style, { left: `${x + 16}px`, top: `${y + 7}px`, width: `${width - 32}px`, height: "48px", fontSize: "26px", zIndex: "85" });
  input.value = query; input.placeholder = T("roster.searchHint"); input.setAttribute("aria-label", T("roster.searchHint")); input.dataset.focusKey = "hall-search";
  input.addEventListener("input", (e) => { if (!e.isComposing) onChange(input.value); }); input.addEventListener("compositionend", () => onChange(input.value));
  if (onEscape) input.addEventListener("keydown", (e) => { if (e.key === "Escape" && !e.isComposing && e.keyCode !== 229) { e.preventDefault(); onEscape(); } });
  host.append(input); return input;
}
export async function checkbox(host, caption, checked, change, [x, y, width]) {
  await ready(); const item = Popup.checkbox("Merged", 0, 0, caption, checked, width / S);
  const view = await NGUI.render(item.tree, host, { scale: S, origin: [x, y + 30] });
  Popup.wireCheck(host, view, "Merged", { checked, label: caption }, change);
}
// UILobby's inclined rounded button contains UIPopup_Option_PC's native sound slider.
export async function scaleControl(host, value, change, { enabled = () => true, box = [1875 - (3 * 156 * S + 40), 315, 3 * 156 * S + 40], limits, focusKey = "hall-scale-range" } = {}) {
  const [popup, lobby, { buttonCenter }] = await Promise.all([NGUI.layout("UIPopup_Option_PC"), NGUI.layout("UILobby"), import("./student-hall-icons.js")]);
  const [x, y, width] = box, middle = x + width / 2, scale = (width - 44) / (689 * S);
  const tree = clone(NGUI.find(lobby, "Front/Right/Btn_RotateSpine/ConditionRoot/Able_Count"));
  tree.name = "HallScale"; tree.pos = [0, 0]; tree.children = tree.children.filter((node) => node.name === "Bg");
  NGUI.find(tree, "Bg").widgets.find((widget) => "sprite" in widget).w_ = width / S;
  const slide = clone(NGUI.find(popup, "Center/Popup/List/Sound_Setting/Sound_Scroll View/Grid_2/Master/Type_3/SlideBg"));
  slide.pos = [0, 0]; slide.scale = [scale, scale];
  NGUI.find(slide, "Thumb").pos = [-316, 0]; tree.children.push(slide);
  const view = await NGUI.render(tree, host, { scale: S, origin: [middle, y] });
  const center = await buttonCenter(view.nodes.get("HallScale/Bg").els[0]);
  for (const [path, node] of view.nodes) if (path.startsWith("HallScale/SlideBg")) for (const element of node.els) {
    element.style.left = `${parseFloat(element.style.left) + center.x - middle}px`;
    element.style.top = `${parseFloat(element.style.top) + center.y - y}px`;
  }
  const thumb = view.nodes.get("HallScale/SlideBg/Thumb").els[0], start = parseFloat(thumb.style.left);
  const range = document.createElement("input"); range.type = "range"; range.className = "hit";
  range.setAttribute("aria-label", T(limits ? "hall.portraitScalePercent" : "hall.portraitScale")); range.dataset.focusKey = focusKey;
  Object.assign(range.style, { position: "absolute", left: `${center.x - 346 * S * scale}px`, top: `${center.y - 24}px`,
    width: `${692 * S * scale}px`, height: "48px", opacity: "0", margin: "0", zIndex: "90" });
  host.append(range); range.disabled = !enabled();
  return { range, ...bindScale(range, null, null, { value, change, enabled, limits, paint: (fraction) => { thumb.style.left = `${start + 632 * S * scale * fraction}px`; } }) };
}

function observePicture(element, picture) {
  if (!picture?.start) return;
  if (typeof IntersectionObserver === "undefined") { picture.start(); return; }
  const observer = new IntersectionObserver((entries) => {
    if (entries.some((entry) => entry.isIntersecting)) { picture.bump?.(); picture.start(); observer.disconnect(); }
  }, { rootMargin: "80px" });
  observer.observe(element); picture.signal?.addEventListener("abort", () => observer.disconnect(), { once: true });
}
export async function card(host, item, { x, y, kind = "lobby", scale = .48, selected = false, onPreview, onAdd, addLabel = T("hall.add"), addEnabled = true, addSize, fitAdd = false, label = true, interactive = !!onPreview }) {
  const geometry = kind === "spr" ? { w: 320, h: 600, bar: 88 } : { w: 720, h: 405, bar: 88 };
  const tree = Popup.listCard(item, 0, 0, selected, geometry, kind);
  NGUI.find(tree, "Thumb").widgets.find((widget) => "clipImage" in widget).placeholderOnFailure = true;
  const bar = NGUI.find(tree, "Bar"), barWidget = bar.widgets.find((w) => "sprite" in w);
  if (!label) tree.children = tree.children.filter((child) => child !== bar);
  const visual = document.createElement("div"); Object.assign(visual.style, { position: "absolute", inset: "0", pointerEvents: "none" }); host.append(visual);
  const view = await NGUI.render(tree, visual, { scale, origin: [x, y] });
  pictureReadiness(visual, item.thumb, view);
  const width = geometry.w * scale, height = geometry.h * scale;
  const hit = hitButton(host, [x - width / 2 - 12, y - height / 2, width + 24, height], onPreview || (() => {}), item.full, 70);
  hit.dataset.action = interactive ? "hall-preview" : "hall-card"; hit.dataset.focusKey = `card:${item.key}`;
  if (!interactive) { hit.tabIndex = -1; hit.style.cursor = "var(--cursor)"; }
  observePicture(hit, item.thumb);
  // The name strip is inclined: its visual centre is left of the artwork centre.
  const actionWidth = barWidget.w_ * scale, actionX = x + bar.pos[0] * scale - actionWidth / 2;
  if (onAdd) {
    const draw = kind === "spr" ? button : retainedButton, height = kind === "spr" ? BUTTON.h * S : 40;
    const size = kind === "spr" ? buttonSize : (captions, width) => retainedButtonSize(captions, width, height);
    await draw(host, `Add${item.key}`, addLabel,
      [actionX, y - bar.pos[1] * scale + barWidget.h_ * scale / 2 + 8, actionWidth, height], onAdd,
      { enabled: addEnabled, ...(addSize != null ? { size: addSize } : fitAdd ? { size: size([addLabel], actionWidth) } : {}) });
  }
  return { view, visual, hit, width, height, actionBox: [actionX, y + height / 2 + 8, actionWidth, 36] };
}
export const MEMORIAL_QUEUE_PITCH = (290 + (720 + 405 * Math.tan(Math.PI / 18)) * .25) / 2;
export async function queuePanel(host, [x, y, width, height]) {
  await ready(); const tree = clone(NGUI.find(assets.collection, "ListBG/PaperBG"));
  tree.name = "MemorialQueuePanel"; tree.pos = [0, 0]; tree.children = [];
  Object.assign(tree.widgets.find((widget) => "sprite" in widget), { w_: width / S, h_: height / S, depth: 15 });
  return NGUI.render(tree, host, { scale: S, origin: [x + width / 2, y + height / 2] });
}
export async function queueMarker(host, pitch) {
  await ready();
  const tree = clone(NGUI.find(assets.formation, "Center/Popup/BG/Label_Title/DecoLine"));
  const blue = NGUI.find(assets.formation, "Center/Popup/RightBg/Inbox/Scroll View/Grid/UIFormationCharSlotGroup/UIPopupFormationCharSlot/UICharacterCard/Normal/Mulligan_Sub").widgets.find((widget) => "sprite" in widget).color;
  tree.name = "MemorialInsertionLine"; tree.active = true; tree.pos = [0, 0]; tree.children = [];
  // Retain the game's solid line artwork and formation-selection blue palette.
  Object.assign(tree.widgets.find((widget) => "sprite" in widget), { sprite: "Common_Line_Dot", type: 0,
    w_: 2 / S, h_: 155 / S, incline: 0, color: blue });
  const marker = document.createElement("div"); marker.hidden = true; marker.setAttribute("aria-hidden", "true");
  Object.assign(marker.style, { position: "absolute", left: "0", top: "0", width: "2px", height: "175px", pointerEvents: "none", zIndex: "100" });
  host.append(marker); await NGUI.render(tree, marker, { scale: S, origin: [1, 82.5] }); return marker;
}
export function formationCardTree(formation, item, selected) {
  const tree = clone(NGUI.find(formation, "Center/Popup/RightBg/Inbox/Scroll View/Grid/UIFormationCharSlotGroup/UIPopupFormationCharSlot"));
  tree.name = `Student_${item.key}`; tree.pos = [0, 0];
  tree.children = tree.children.filter((n) => ["Bg", "UICharacterCard", "Sprite", "Cover_Sell_Select"].includes(n.name));
  const card = NGUI.find(tree, "UICharacterCard"); card.children = card.children.filter((n) => n.name === "Normal");
  const normal = NGUI.find(card, "Normal"); normal.active = true;
  normal.children = normal.children.filter((n) => ["Card_Char", "Active", "Card_Bg_Line", "Card_Bg_Line (1)"].includes(n.name));
  const image = NGUI.find(normal, "Card_Char").widgets.find((w) => "texture" in w);
  delete image.texture;
  Object.assign(image, { clipImage: item.thumb || item.portrait, imgSize: "cover", w_: 220, h_: 202, incline: 10, radius: 18, imgLeft: "-9%", imgWidth: "118%" });
  const bar = NGUI.find(tree, "Sprite"); bar.children = bar.children.filter((n) => n.name === "Type_2 (1)");
  const type = NGUI.find(bar, "Type_2 (1)"); type.active = true;
  const label = NGUI.find(type, "Text").widgets.find((w) => "text" in w); NGUI.fitLabel(label, item.name, 36, 24, 190, 2); label.h_ = 77;
  const mark = NGUI.find(tree, "Cover_Sell_Select"); mark.active = selected; mark.children = [];
  return tree;
}
export async function studentCard(host, item, [x, y], selected, click) {
  await ready(); const tree = formationCardTree(assets.formation, item, selected);
  const visual = document.createElement("div"); Object.assign(visual.style, { position: "absolute", inset: "0", pointerEvents: "none" }); host.append(visual);
  const view = await NGUI.render(tree, visual, { scale: .52, origin: [x, y] }); pictureReadiness(visual, item.thumb, view);
  const hit = hitButton(host, [x - 67, y, 134, 181], click, item.name, 75);
  hit.dataset.focusKey = `student:${item.key}`; hit.setAttribute("aria-pressed", String(selected)); observePicture(hit, item.thumb); return hit;
}

// UIPopup_Formation_CharEdit's panel and its own tab label retain the header's bounds.
export async function panel(host, name, [x, y, width, height], title) {
  await ready(); const tree = clone(NGUI.find(assets.formation, "Center/Popup/RightBg"));
  tree.name = name; tree.pos = [0, 0]; tree.children = tree.children.filter((node) => node.name === "Bg_W");
  const body = tree.widgets.find((w) => "sprite" in w); Object.assign(body, { w_: width / S, h_: height / S, depth: 15 });
  const inset = NGUI.find(tree, "Bg_W"); inset.pos = [0, -36];
  Object.assign(inset.widgets.find((w) => "sprite" in w), { w_: width / S - 20, h_: height / S - 92, depth: 16 });
  const label = clone(NGUI.find(assets.formation, "Center/Popup/RightBg/AssistTab/Tab_My/Tab_OFF/Label_Locale"));
  label.name = "Title"; label.pos = [0, height / S / 2 - 37];
  const text = label.widgets.find((w) => "text" in w); NGUI.fitLabel(text, title, 42, 28, width / S - 64, 1);
  Object.assign(text, { h_: 56, depth: 40, pivot: 4, align: 0 }); tree.children.push(label);
  await NGUI.render(tree, host, { scale: S, origin: [x + width / 2, y + height / 2] });
}

export async function rosterToolbar(host, view, searching, actions, { x = 115, y = 206, width = 1685 } = {}) {
  await ready();
  const lay = toolbarLayout(view, { title: "" }), tree = toolbarTree(assets.collection, assets.checkbox, view, searching, lay);
  tree.pos = [0, 0]; tree.children = tree.children.filter((n) => ["Menubar", "ModFilter", "RecruitFilter", "SearchBtn", "SortBtn", "OrderBtn"].includes(n.name));
  const menu = NGUI.find(tree, "Menubar"); menu.children = menu.children.filter((child) => child.name === "Merge");
  const span = 1412 - lay.cbLeft, scale = Math.min(1660 / 2840, width / (span + 28));
  const origin = [x + (width - span * scale) / 2 - lay.cbLeft * scale, y - 273.5 * scale];
  const frame = clone(NGUI.find(assets.collection, "ListBG")); frame.name = "HallToolbarBG"; frame.pos = [0, 0]; frame.children = [];
  Object.assign(frame.widgets.find((w) => "sprite" in w), { w_: width / scale, h_: 140 });
  await NGUI.render(frame, host, { scale, origin: [x + width / 2, y], z: 55 });
  await NGUI.render(tree, host, { scale, origin, z: 65 });
  const hit = (x, width, action, name, key, pressed) => {
    const button = hitButton(host, [origin[0] + (x - width / 2) * scale, y - 58 * scale, width * scale, 116 * scale], action, name, 90);
    button.dataset.focusKey = key; if (pressed != null) button.setAttribute("aria-pressed", String(pressed)); return button;
  };
  ["all", "mods", "none"].forEach((value, i) => hit(lay.seg1 - (2.5 - i) * lay.segW, lay.segW, () => actions.change({ mods: value }), T(["seg.all", "seg.mods", "seg.noMods"][i]), `mods:${value}`, view.mods === value));
  ["all", "yes", "no"].forEach((value, i) => hit(lay.seg2 - (2.5 - i) * lay.segW, lay.segW, () => actions.change({ recruit: value }), T(["seg.all", "seg.recruit", "seg.noRecruit"][i]), `recruit:${value}`, view.recruit === value));
  hit(lay.cbLeft + lay.cbW / 2, lay.cbW, () => actions.change({ merged: !view.merged }), T("roster.merge"), "hall-merge", view.merged);
  hit(lay.search, 150, actions.search, T("roster.search"), "hall-search-toggle", searching);
  hit(lay.sort, lay.sortW, actions.display, T("display.title"), "hall-display");
  hit(lay.order, 243, () => actions.change({ order: view.order === "desc" ? "asc" : "desc" }), T(view.order === "desc" ? "roster.desc" : "roster.asc"), "hall-order");
}

// The collection sort plate supplies its white sliced body, native incline and shadow.
export function rowPlateTree(collection, [width, height], margin = 32) {
  const tree = clone(NGUI.find(collection, "SeletControl/TopMenu/SortBtn/Btn_Normal"));
  tree.name = "PortraitRow"; tree.pos = [0, 0]; tree.children = [];
  const body = tree.widgets.find((widget) => "sprite" in widget);
  const overhang = height * Math.abs(Math.tan((body.incline || 0) * Math.PI / 180));
  Object.assign(body, { w_: (width - margin * 2 - overhang) / S, h_: height / S, depth: 1 });
  return tree;
}
export async function rowPlate(host, [width, height]) {
  await ready();
  return NGUI.render(rowPlateTree(assets.collection, [width, height]), host, { scale: S, origin: [width / 2, height / 2] });
}

// Construct only visible rows/columns and one overscan neighbour. Nested portrait strips keep their own offsets.
export function virtualArea(host, box, count, pitch, render, { key, horizontal = false, signal, positions = new Map(), onError = console.error } = {}) {
  const inner = area(host, box, horizontal ? box[3] : Math.max(box[3], count * pitch), key,
    { horizontal, width: horizontal ? Math.max(box[2], count * pitch) : box[2] });
  const scroll = inner.parentElement, mounted = new Map(); let timer, disposed = false;
  const clamp = (value) => horizontal ? [Math.max(0, Math.min(Math.max(0, count * pitch - box[2]), value[0])), 0]
    : [0, Math.max(0, Math.min(Math.max(0, count * pitch - box[3]), value[1]))];
  let logical = clamp(positions.get(key) || [0, 0]);
  if (scroll.isConnected) [scroll.scrollLeft, scroll.scrollTop] = logical;
  const remember = () => { if (scroll.isConnected) logical = [scroll.scrollLeft, scroll.scrollTop]; positions.set(key, [...logical]); };
  const offsetOf = () => scroll.isConnected ? horizontal ? scroll.scrollLeft : scroll.scrollTop : logical[horizontal ? 0 : 1];
  virtualControls.set(scroll, {
    sync() { logical = clamp(positions.get(key) || logical); if (scroll.isConnected) [scroll.scrollLeft, scroll.scrollTop] = logical; },
    commit() { positions.set(key, [...logical]); [scroll.scrollLeft, scroll.scrollTop] = logical; },
  });
  const update = () => {
    if (disposed) return [];
    const offset = offsetOf(), extent = horizontal ? box[2] : box[3];
    const first = Math.max(0, Math.floor(offset / pitch) - 1), last = Math.min(count, Math.ceil((offset + extent) / pitch) + 1);
    for (const [index, cell] of mounted) if ((index < first || index >= last)
        && !cell.host.querySelector?.('[data-reordering="true"]') && !cell.host.querySelector?.('[data-pointer-owned="true"]')) { cell.controller.abort(); cell.host.remove(); mounted.delete(index); }
    const pending = [];
    for (let index = first; index < last; index++) if (!mounted.has(index)) {
      const cell = document.createElement("div"), controller = new AbortController();
      Object.assign(cell.style, { position: "absolute", left: `${horizontal ? index * pitch : 0}px`, top: `${horizontal ? 0 : index * pitch}px`,
        width: `${horizontal ? pitch : box[2]}px`, height: `${horizontal ? box[3] : pitch}px` });
      inner.append(cell);
      const item = { host: cell, controller, pending: null }; mounted.set(index, item);
      item.pending = Promise.resolve(render(cell, index, controller.signal)).catch((error) => { if (!controller.signal.aborted) { onError(error); throw error; } });
      pending.push(item.pending);
    }
    return pending;
  };
  visibleReadiness.set(scroll, async (prepareSignal) => {
    if (disposed) return;
    await Promise.all([inner.ready, ...update()]);
    const offset = offsetOf(), extent = horizontal ? box[2] : box[3];
    const visible = [...mounted].filter(([index]) => index * pitch < offset + extent && (index + 1) * pitch > offset);
    await Promise.all(visible.map(async ([, cell]) => { await cell.pending; await prepareVisible(cell.host, prepareSignal); }));
  });
  const onScroll = () => { remember(); clearTimeout(timer); timer = setTimeout(() => prepareVisible(scroll, signal).catch((error) => { if (!signal?.aborted) onError(error); }), 24); };
  scroll.addEventListener("scroll", onScroll);
  const dispose = () => { if (disposed) return; remember(); disposed = true; clearTimeout(timer); scroll.removeEventListener("scroll", onScroll);
    for (const cell of mounted.values()) cell.controller.abort(); mounted.clear(); signal?.removeEventListener("abort", dispose); };
  signal?.addEventListener("abort", dispose, { once: true });
  if (signal?.aborted) dispose();
  return { inner, ready: Promise.all([inner.ready, ...update()]), dispose };
}

export async function orderButton(host, ascending, action, [x, y, width, height]) {
  await ready(); const tree = orderBody(assets.collection, ascending); tree.name = "HallOrder"; tree.pos = [0, 0];
  const body = tree.widgets.find((widget) => "sprite" in widget);
  const ratio = Math.min(width / (body.w_ * S), height / (body.h_ * S)); tree.scale = [ratio, ratio];
  const view = await NGUI.render(tree, host, { scale: S, origin: [x + width / 2, y + height / 2], z: 65 });
  return Popup.hit(host, view, "HallOrder", action, { name: T(ascending ? "roster.asc" : "roster.desc"), action: "hall-order", z: 90 });
}

export async function updateAction(host, name, label, action, enabled) {
  const hit = [...host.querySelectorAll("button[data-action]")].find((button) => button.dataset.action === name);
  if (!hit) return;
  const box = [hit.style.left, hit.style.top, hit.style.width, hit.style.height].map(parseFloat), owner = hit.parentElement;
  const next = document.createElement("div");
  const draw = hit.dataset.retainedButton === "true" ? retainedButton : button;
  await draw(next, name, label, box, action, { enabled });
  if (!hit.isConnected) return;
  for (const element of owner.querySelectorAll("[data-path]")) if (element.dataset.path === name || element.dataset.path.startsWith(name + "/")) element.remove();
  hit.remove(); owner.append(...next.childNodes);
}

// Prefabs and derived UI values belong to the selected installation.
NGUI.onResourceChange?.(() => { assets = null; });
