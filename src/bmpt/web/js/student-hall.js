// Student Hall: UILobby controls, independent drafts, and modal resource editors built from native game prefabs.
import * as NGUI from "./ngui.js";
import { bindCorner } from "./corner-layout.js";
import * as Lobby from "./lobby.js";
import * as Loading from "./loading.js";
import * as Popup from "./popup.js";
import * as UI from "./student-hall-ui.js";
import * as Backgrounds from "./student-hall-backgrounds.js";
import { createDraft, sourceKey, contentBounds, portraitSelection, portraitReplacements } from "./student-hall-model.js";
import { openPicker } from "./student-hall-picker.js";
import { createDialogue } from "./student-hall-dialogue.js";
import { createPortraits } from "./student-hall-portraits.js";
import { alignIcon } from "./student-hall-icons.js";
import { createMemorialPool } from "./student-hall-memorial.js";
import { createFullscreen } from "./student-hall-fullscreen.js";
import { createSceneCamera, sceneRect, sceneViewport } from "./student-hall-camera.js";
import { dialogueText } from "./dialogue-text.js";
import { bindPortraitPointer, portraitRect, portraitToolbarLayout, PORTRAIT_SIZE } from "./student-hall-framing.js";
import { rpc } from "./api.js";
import { T, L, errorText, language } from "./i18n.js";
import { clone, S, hitButton, text } from "./util.js";
import { lazyThumbnail } from "./lazy-thumbnail.js";
import { thumbnailScope } from "./thumbs.js";
import { CONTROL, XS_BUTTON, SPACE } from "./ui-tokens.js";
import { row } from "./ui-grid.js";

let ctx, store, options, root, art, chrome, hits, subtitle, layouts, dialogue, fullscreen, sceneCamera;
let page = 0, scene = 0, toolbar = 0, hitGeneration = 0, abort, portraits, selected = null, editor = null, leavePrompt = null;
let saving = Promise.resolve(), leavePending = null, leaveResolve = null, pendingWrites = 0, needsSettlement = false;
let sceneMode = null, backgroundHost = null, portraitHost = null, backgroundName = null, actorHover = null, portraitSceneHost = null, memorials = null, sceneCover = null;
const backgroundCache = new Map(), revisions = new WeakMap();
const memorialCaptions = new WeakMap();
const lineLanguage = () => ({ "en-US": "en", "zh-CN": "zh-CN", "zh-TW": "zh-TW", "ja-JP": "ja", "ko-KR": "ko", "th-TH": "th" })[language()] || "en";
// User-selected Common atlas icons; dimensions are the authored sprite sizes.
const ACTION_ICONS = { Layout: ["Cafe_Icon_NameEdit", 50, 50], Background: ["Cafe_Icon_Photo", 120, 104], Adjust: null, Add: null };
// The stacked icons include translucent backplates; their full visible outline supplies the visual center.
const ACTION_OPTICS = {
  Layout: { center: "bounds", alphaCutoff: 32 },
  Background: { center: "bounds", alphaCutoff: 32, height: 48 * S * 1.08 },
  Add: { center: "bounds", alphaCutoff: 32 }, Adjust: { center: "bounds", alphaCutoff: 32 },
  Fullscreen: { center: "bounds", alphaCutoff: 32 },
};
// UILobby Able_Count/Bg keeps its own 156 x 104-unit body; ACTION_ICONS keeps the authored sprite sizes above.
const TOOLBAR = Object.freeze({ right: 1875, gap: SPACE.m * S, iconWidth: 156 * S, width: (4 * 156 + 2 * SPACE.m + SPACE.l) * S });
const current = () => store.current();
const editing = () => store?.kind() === "layout";
const arranging = () => editing() && !fullscreen?.active();
const choiceOf = (entry, kind = "spr") => options.choices.find((choice) => choice.kind === kind && sourceKey(choice) === sourceKey(entry));
const titleOf = (choice) => choice ? [L(choice.names)?.full || choice.variant, choice.label].filter(Boolean).join(" - ") : T("hall.missing");
const fail = (error) => { if (root && error?.name !== "AbortError") Popup.notify(errorText(error), { kind: "error", error }); };
function layer(parent, className = "layer-host", z = null) {
  const element = document.createElement("div"); element.className = className;
  Object.assign(element.style, { position: "absolute", inset: "0", ...(z === null ? {} : { zIndex: String(z) }) }); parent.append(element); return element;
}
function closeEditor() { editor?.close(); editor = null; }
function clearPortraitScene() {
  dialogue?.dispose(); dialogue = null; portraits?.dispose(); portraits = null;
  portraitSceneHost?.remove(); portraitSceneHost = null; backgroundHost = portraitHost = null; backgroundName = null; backgroundCache.clear();
}
function stopScene() {
  ++scene; abort?.abort(); abort = null;
  clearPortraitScene(); memorials?.dispose(); memorials = null;
  sceneCover?.remove(); sceneCover = null; sceneMode = null;
}
function freezeScene() {
  if (sceneCover || !sceneMode) return;
  const cover = layer(root, "", 3); cover.inert = true; cover.style.pointerEvents = "none";
  for (const source of [art, hits, subtitle]) {
    const copy = source.cloneNode(true), originals = source.querySelectorAll("canvas"), copies = copy.querySelectorAll("canvas");
    copies.forEach((canvas, index) => {
      canvas.width = originals[index].width; canvas.height = originals[index].height;
      if (canvas.width && canvas.height) canvas.getContext("2d").drawImage(originals[index], 0, 0);
    });
    copy.style.pointerEvents = "none"; cover.append(copy);
  }
  sceneCover = cover;
}

async function persist(value, owner = store) {
  const snapshot = clone(value), editorHost = editor?.modal?.host, wasInert = editorHost?.inert;
  pendingWrites++; if (editorHost) editorHost.inert = true; renderChrome().catch(fail);
  const pending = saving.catch(() => {}).then(async () => {
    const revision = revisions.get(owner) ?? owner.saved().revision;
    const result = await rpc("hall.update", { value: snapshot, revision }); revisions.set(owner, result.revision); return result;
  });
  saving = pending;
  try { return await pending; }
  finally { pendingWrites--; if (editorHost?.isConnected) editorHost.inert = wasInert; if (root) renderChrome().catch(fail); }
}
async function saveDraft({ render = true } = {}) {
  if (pendingWrites) return;
  const gen = page, owner = store;
  const result = await persist(owner.current(), owner);
  if (gen !== page || owner !== store || !root) return;
  owner.accept(result); selected = null; closeEditor();
  if (render) await renderScene();
}
async function cancelDraft() {
  if (!root || pendingWrites) return; store.cancel(); selected = null; closeEditor(); await renderScene();
}
function controls() {
  const portraitMode = current().mode === "portraits", actions = [];
  if (editing()) actions.push(["Add", "hall.addPortraits", 0, 0, () => addPortraits()], ["Background", "hall.background", 0, 0, () => chooseBackground()]);
  else if (portraitMode) actions.push(["Layout", "hall.changeLayout", 0, 0, async () => { store.begin("layout"); await renderScene(); }],
    ["Background", "hall.background", 0, 0, () => chooseBackground()]);
  else actions.push(["Adjust", "hall.adjust", 0, 0, () => adjustMemorials()]);
  const width = TOOLBAR.iconWidth, h = CONTROL.toolbar.h * S;
  const local = row(actions.map(() => ({ w: width, h })), { gap: TOOLBAR.gap });
  const navigation = row([{ w: width, h }, { w: width, h }], { gap: TOOLBAR.gap });
  const groups = row([local, navigation], { x: TOOLBAR.right - TOOLBAR.width, w: TOOLBAR.width, gap: SPACE.l * S, justify: "end" });
  local.boxes.forEach((box, i) => { actions[i][2] = groups.boxes[0].x + box.x; actions[i][3] = box.w; });
  const [modeBox, fullBox] = navigation.boxes.map((box) => ({ ...box, x: groups.boxes[1].x + box.x }));
  actions.push(["Mode", portraitMode ? "hall.memorial" : "hall.portraits", modeBox.x, width, () => setMode(portraitMode ? "memorial" : "portraits")]);
  actions.push(["Fullscreen", "lobby.fullscreen", fullBox.x, width, () => fullscreen?.enter()]);
  if (editing()) {
    const width = (TOOLBAR.width - TOOLBAR.gap) / 2, left = TOOLBAR.right - TOOLBAR.width;
    const pair = row([{ w: width, h: CONTROL.buttonXS.h * S }, { w: width, h: CONTROL.buttonXS.h * S }], { x: left, gap: TOOLBAR.gap });
    actions.push(["Cancel", "popup.cancel", pair.boxes[0].x, width, () => cancelDraft(), true, 230],
      ["Save", "popup.save", pair.boxes[1].x, width, () => saveDraft(), true, 230]);
  }
  return actions;
}

function lobbyControl(name, caption, x, width, enabled = true, y = 145, fit = null) {
  const tree = clone(NGUI.find(layouts.lobby, name === "Fullscreen" ? "Front/Right/Btn_Screen_On" : "Front/Right/Btn_RotateSpine/ConditionRoot/Able_Count"));
  const iconAction = Object.hasOwn(ACTION_ICONS, name), iconOnly = name === "Mode" || name === "Fullscreen" || iconAction;
  tree.name = name; tree.pos = [(x + width / 2 - 960) / S, (540 - y) / S];
  tree.children = tree.children.filter((node) => (iconOnly ? ["Bg", "Icon"] : ["Bg", "Label_SpineCount"]).includes(node.name));
  Object.assign(NGUI.find(tree, "Bg").widgets.find((w) => "sprite" in w), { w_: width / S, h_: iconOnly ? CONTROL.toolbar.h : CONTROL.buttonXS.h });
  const dutyIcon = NGUI.find(layouts.lobby, `Front/NUITop/Top/Right/Btn_LobbyCharacter/${enabled ? "Able" : "Disable"}/Icon`);
  const tint = dutyIcon.widgets.find((widget) => "sprite" in widget).color;
  if (iconAction) {
    const chosen = ACTION_ICONS[name];
    if (chosen) {
      const icon = NGUI.find(tree, "Icon").widgets.find((widget) => "sprite" in widget);
      [icon.sprite, icon.w_, icon.h_] = chosen; icon.color = tint;
    } else tree.children = tree.children.map((node) => node.name === "Icon" ? clone(dutyIcon) : node);
  }
  if (iconOnly) {
    const node = NGUI.find(tree, "Icon"), widget = node.widgets.find((item) => "sprite" in item);
    const size = 76 / Math.max(widget.w_, widget.h_);
    widget.w_ *= size; widget.h_ *= size; widget.color = tint;
    node.pos = [0, 0]; node.scale = [1, 1];
  } else {
    const fitted = fit || NGUI.fitGroup([caption], CONTROL.buttonXS.fs, XS_BUTTON.fsMin, width / S - 2 * CONTROL.buttonXS.pad, 2, { maxH: CONTROL.buttonXS.h - XS_BUTTON.boxInsetH })[0];
    const label = NGUI.find(tree, "Label_SpineCount"); label.pos = [0, 0]; label.scale = [1, 1];
    text(tree, "Label_SpineCount", fitted.text, { w_: width / S - XS_BUTTON.boxInsetW, h_: CONTROL.buttonXS.h - XS_BUTTON.boxInsetH, fontSize: fitted.fs, pivot: 4, align: 2 });
  }
  if (!enabled) for (const node of tree.children) if (node.name !== "Bg") for (const widget of node.widgets) if (widget.color) widget.color = tint;
  return tree;
}
async function renderChrome({ publish = true } = {}) {
  if (!root || !layouts) return;
  const gen = page, version = ++toolbar, mode = current().mode, destination = document.createElement("div");
  Object.assign(destination.style, { position: "absolute", inset: "0" });
  const actionHost = document.createElement("div"); Object.assign(actionHost.style, { position: "absolute", inset: "0" }); destination.append(actionHost);
  const actions = controls(), textActions = actions.filter((action) => action[6]);
  const fits = textActions.length ? NGUI.fitGroup(textActions.map(([, key]) => T(key)), CONTROL.buttonXS.fs, XS_BUTTON.fsMin,
    textActions[0][3] / S - 2 * CONTROL.buttonXS.pad, 2, { maxH: CONTROL.buttonXS.h - XS_BUTTON.boxInsetH }) : [];
  const nodes = actions.map(([name, key, x, width, , enabled, y]) => lobbyControl(name, T(key), x, width, enabled !== false && pendingWrites === 0, y,
    fits[textActions.findIndex((action) => action[0] === name)]));
  const view = await NGUI.render(NGUI.node("Hall", [0, 0], [], nodes), actionHost, { scale: S, origin: [960, 540] });
  bindCorner(actionHost, [TOOLBAR.right - TOOLBAR.width, 145 - CONTROL.toolbar.h * S / 2, TOOLBAR.width, CONTROL.toolbar.h * S], { corner: "top-right", immersive: false });
  await Promise.all(actions.filter(([name]) => name === "Mode" || name === "Fullscreen" || Object.hasOwn(ACTION_ICONS, name)).map(([name, , , , , enabled]) =>
    alignIcon(view, `Hall/${name}/Icon`, `Hall/${name}/Bg`, { height: 48 * S,
      maxWidth: 104 * S, enabled: enabled !== false && !pendingWrites, ...ACTION_OPTICS[name] })));
  const run = (action) => () => { if (gen === page && version === toolbar && root && pendingWrites === 0) Promise.resolve(action()).catch(fail); };
  for (const [name, key, , , action, enabled] of actions) {
    const button = Popup.hit(actionHost, view, `Hall/${name}/Bg`, run(action), { name: T(key), action: `hall-${name.toLowerCase()}` });
    if (button) button.disabled = enabled === false || pendingWrites > 0;
  }
  if (mode === "memorial" && current().memorials.length) {
    const arrows = await NGUI.render(NGUI.node("Hall", [0, 0], [], [clone(NGUI.find(layouts.lobby, "Front/Btn_Arrow"))]), destination, { scale: S, origin: [960, 540] });
    Popup.hit(destination, arrows, "Hall/Btn_Arrow/Pos_prev/Btn_prev/Btn_prev", run(() => step(-1)), { name: T("hall.previous"), action: "hall-previous" });
    Popup.hit(destination, arrows, "Hall/Btn_Arrow/Pos_Next/Btn_Next/Btn_Next", run(() => step(1)), { name: T("hall.next"), action: "hall-next" });
  }
  if (editing()) await UI.scaleControl(actionHost, current().portraitScale, (value) => {
    if (gen !== page || version !== toolbar || !root || !editing() || pendingWrites) return;
    current().portraitScale = value; portraits?.setScale?.(value); renderHits();
  }, { enabled: () => gen === page && version === toolbar && !!root && pendingWrites === 0, box: [TOOLBAR.right - TOOLBAR.width, 315, TOOLBAR.width] });
  if (gen === page && version === toolbar && root) { if (publish) chrome.replaceChildren(destination); return destination; }
  return null;
}
async function setMode(mode) {
  const gen = page;
  if (mode === current().mode || !(await canLeave()) || gen !== page || !root) return;
  const next = clone(store.saved()); next.mode = mode;
  const result = await persist(next); if (gen !== page || !root) return; store.accept(result); await renderScene();
}
async function step(delta) {
  if (!root || pendingWrites || store.kind()) return;
  const gen = page, next = clone(store.saved()), list = next.memorials;
  if (!list.length) return;
  const at = Math.max(0, list.findIndex((entry) => entry.id === next.active)); next.active = list[(at + delta + list.length) % list.length].id;
  const result = await persist(next); if (gen !== page || !root) return; store.accept(result); await renderScene();
}
function rectOf(entry) {
  const rect = portraitRect(entry, portraits?.actors.get(entry.id)?.bounds, current().portraitScale);
  return sceneCamera && current().mode === "portraits" && !editing() ? sceneRect(rect, sceneCamera.view()) : rect;
}
function paintSceneView(view, immediate = false) {
  portraits?.setView?.(sceneViewport(view), { draw: immediate });
  if (backgroundHost) Object.assign(backgroundHost.style, { transformOrigin: "0 0", transform: `translate(${view.x}px, ${view.y}px) scale(${view.scale})` });
  if (!root || !store || current().mode !== "portraits") return;
  for (const button of hits.querySelectorAll("[data-entry]")) {
    const entry = current().portraits.find((item) => item.id === button.dataset.entry);
    if (!entry) continue;
    const rect = rectOf(entry);
    Object.assign(button.style, { left: `${rect.x}px`, top: `${rect.y}px`, width: `${rect.width}px`, height: `${rect.height}px` });
  }
  dialogue?.reposition?.();
}
function selectPortrait(type, id = null) {
  selected = portraitSelection(selected, { type, id, ids: current().portraits.map((entry) => entry.id) });
  portraits?.setSelection?.(arranging() ? selected : null);
  actorHover?.pin(selected);
  for (const button of hits?.querySelectorAll?.("[data-entry]") || []) button.setAttribute("aria-pressed", String(arranging() && selected === button.dataset.entry));
}
// The top bar and the Hall's own controls, in stage units: the portrait toolbar never covers them.
function toolbarObstacles() {
  if (!root) return [];
  const box = root.getBoundingClientRect(), scale = box.width > 0 ? 1920 / box.width : 1;
  return [...(chrome?.querySelectorAll?.("button, input") || []), ...document.querySelectorAll("header.topbar")]
    .map((element) => element.getBoundingClientRect()).filter((b) => b.width > 0 && b.height > 0)
    .map((b) => ({ x: (b.left - box.left) * scale, y: (b.top - box.top) * scale, width: b.width * scale, height: b.height * scale }));
}
function toolbarBox(entry, rect = rectOf(entry)) {
  const offset = portraits?.head?.(entry.id), head = offset && { x: entry.x + offset.x, y: entry.y + offset.y };
  return portraitToolbarLayout(rect, portraitActions(entry).length, toolbarObstacles(), head);
}
function portraitActions(entry) {
  const list = current().portraits, index = list.findIndex((item) => item.id === entry.id);
  const actions = list.length > 1 ? [["Remove", "hall.remove", () => store.remove(entry.id), true]] : [];
  if (current().portraits.length >= 2) actions.push(
    ["Backward", "hall.layerBackward", () => store.layer(entry.id, -1), index > 0],
    ["Forward", "hall.layerForward", () => store.layer(entry.id, 1), index < list.length - 1],
    ["Front", "hall.layerFront", () => store.layer(entry.id, "front"), index < list.length - 1],
    ["Back", "hall.layerBack", () => store.layer(entry.id, "back"), index > 0],
    ["Flip", "hall.flipHorizontal", () => store.flip(entry.id), true]);
  actions.push(["Change", "hall.changePortrait", () => changePortrait(entry), portraitReplacements(entry, options.choices, list).length > 0]);
  return actions;
}
// Kept separate from behavior so the chosen game icons can be swapped without changing editor semantics.
const PORTRAIT_ICONS = {
  // UICardGame_MyDeck SelectDump, UIConquest MissionTab_Target, UICampaign ScenarioReplay, UIFormation PositionChange.
  Change: { atlas: "Common", sprite: "Campaign_Icon_ChangeUnit", width: 77, height: 68 },
  Remove: { atlas: "CardGame", sprite: "CCG_Icon_Dump", width: 64, height: 64 },
  Backward: { sprite: "Common_Icon_Top", width: 44, height: 26, rotZ: 180 },
  Forward: { sprite: "Common_Icon_Top", width: 44, height: 26 },
  Front: { sprite: "Common_Skip_Icon", width: 61, height: 36, rotZ: 90, type: 0 },
  Back: { sprite: "Common_Skip_Icon", width: 61, height: 36, rotZ: 270, type: 0 },
  Flip: { sprite: "Common_Icon_Change", width: 80, height: 80 },
};
async function renderPortraitActions(host, entry, rect, version, alive = () => true) {
  // The toolbar is placed by the portrait's visible head, known once its atlas alpha is read (a few milliseconds).
  await portraits?.pickReady?.(entry.id);
  if (version !== hitGeneration || !alive()) return;
  const actions = portraitActions(entry), box = toolbarBox(entry, rect), width = box.icon;
  Object.assign(host.style, { left: `${box.x}px`, top: `${box.y}px`, width: `${box.width}px`, height: `${box.height}px` });
  const destination = document.createElement("div");
  await Promise.all(actions.map(async ([name, key, action, enabled], index) => {
    const chosen = PORTRAIT_ICONS[name];
    if (!chosen) return;
    const tree = clone(NGUI.find(layouts.lobby, "Front/Right/Btn_RotateSpine/ConditionRoot/Able_Count"));
    tree.name = `Portrait${name}`; tree.pos = [0, 0]; tree.children = tree.children.filter((node) => ["Bg", "Icon"].includes(node.name));
    const bg = NGUI.find(tree, "Bg").widgets.find((widget) => "sprite" in widget); bg.w_ = bg.h_ = width / S;
    const node = NGUI.find(tree, "Icon"), widget = node.widgets.find((item) => "sprite" in item);
    node.pos = [0, 0]; node.scale = [1, 1];
    widget.color = NGUI.find(layouts.lobby, "Front/NUITop/Top/Right/Btn_LobbyCharacter/Able/Icon").widgets.find((item) => "sprite" in item).color;
    Object.assign(widget, { type: chosen.type ?? 1, atlas: chosen.atlas || "Common", sprite: chosen.sprite, w_: chosen.width, h_: chosen.height, flip: chosen.flip || 0 });
    const size = 56 / Math.max(widget.w_, widget.h_); widget.w_ *= size; widget.h_ *= size;
    const at = box.icons[index];
    const view = await NGUI.render(tree, destination, { scale: S, origin: [at.x + at.w / 2, at.y + at.h / 2] });
    await alignIcon(view, `${tree.name}/Icon`, `${tree.name}/Bg`, { maxSize: 56 * S, rotation: chosen.rotZ || 0, enabled: enabled && !pendingWrites });
    const button = Popup.hit(destination, view, `${tree.name}/Bg`, () => {
      if (version !== hitGeneration || !alive() || !arranging() || pendingWrites || !enabled) return;
      if (name === "Remove") selectPortrait("remove", entry.id);
      if (name === "Change") action(); else if (action()) renderScene().catch(fail);
    }, { name: T(key), action: `hall-portrait-${name.toLowerCase()}`, z: 90 });
    if (button) { button.dataset.entryAction = entry.id; button.disabled = pendingWrites > 0 || !enabled; }
  }));
  const enabled = () => version === hitGeneration && alive() && arranging() && !pendingWrites;
  // The toolbar moves only once a size is set: moving it under a dragged thumb would feed back into the slider.
  const place = () => {
    if (!enabled()) return;
    const next = toolbarBox(entry);
    Object.assign(host.style, { left: `${next.x}px`, top: `${next.y}px` });
  };
  let control;
  await UI.button(destination, "HallPortraitScaleReset", T("hall.scaleReset"), box.reset, () => { control?.reset(); place(); }, { enabled: enabled(), captionSite: "hall.scaleReset", height: box.reset[3] });
  control = await UI.scaleControl(destination, entry.scale, (value) => {
    if (!enabled()) return;
    entry.scale = value; portraits?.setScale?.(current().portraitScale);
    const rect = rectOf(entry), button = hits.querySelector(`[data-entry="${entry.id}"]`);
    if (button) Object.assign(button.style, { left: `${rect.x}px`, top: `${rect.y}px`, width: `${rect.width}px`, height: `${rect.height}px` });
  }, { enabled, box: box.slider, limits: PORTRAIT_SIZE, focusKey: `hall-portrait-scale:${entry.id}` });
  control?.range?.addEventListener("change", place);
  if (version === hitGeneration && alive() && root) host.replaceChildren(destination);
}
function createHoverOwner(change) {
  let owner = null, pointer = null, palette = false, focus = null, pinned = null;
  const publish = () => {
    const next = pinned || (palette ? owner : pointer || focus);
    if (next !== owner) { owner = next; change(owner); }
  };
  return {
    pin(id) { pinned = id; publish(); },
    actorEnter(id) { pointer = id; publish(); },
    actorLeave(id, intoPalette = false) { if (pointer === id) pointer = null; if (intoPalette) palette = true; publish(); },
    paletteEnter() { palette = true; },
    paletteLeave(next = null) { palette = false; pointer = next; publish(); },
    focus(id) { focus = id; publish(); },
    clearPointer() { pointer = null; palette = false; publish(); },
    snapshot: () => ({ owner, pointer, palette, focus }),
    restore(saved) { pointer = saved.pointer || (saved.palette ? saved.owner : null); focus = saved.focus; owner = saved.owner; palette = false; change(owner); },
  };
}
function renderHits(host = hits) {
  if (!editing()) selected = null;
  else selected = portraitSelection(selected, { type: "redraw", ids: current().portraits.map((entry) => entry.id) });
  portraits?.setSelection?.(arranging() ? selected : null);
  const previousHover = actorHover?.snapshot(), focused = document.activeElement;
  const focusedKey = focused?.dataset?.focusKey, focusedAction = focused?.dataset?.entryAction && focused.dataset.action, focusedActor = focused?.dataset?.entry;
  const hitVersion = ++hitGeneration; actorHover = null; host.replaceChildren(); if (current().mode !== "portraits") return Promise.resolve();
  let ready = Promise.resolve();
  const alive = () => hitVersion === hitGeneration && !!root;
  let palette = null, paletteVersion = 0, owner = null;
  const buttons = new Map();
  if (arranging()) {
    palette = document.createElement("div"); palette.dataset.actorToolbar = "";
    Object.assign(palette.style, { position: "absolute", visibility: "hidden", pointerEvents: "auto", zIndex: "1000" });
    const hover = actorHover = createHoverOwner((id) => {
      if (!alive()) return;
      const entry = current().portraits.find((item) => item.id === id), version = ++paletteVersion; owner = entry?.id || null;
      palette.style.visibility = entry ? "" : "hidden"; palette.dataset.actorToolbar = owner || "";
      if (!entry) return;
      renderPortraitActions(palette, entry, rectOf(entry), hitVersion, () => alive() && version === paletteVersion).then(() => {
        if (!alive() || version !== paletteVersion || (!focusedAction && !focusedKey) || previousHover?.owner !== id) return;
        palette.querySelector(focusedKey ? `[data-focus-key="${focusedKey}"]` : `[data-action="${focusedAction}"]`)?.focus({ preventScroll: true });
      }).catch(fail);
    });
    const targetActor = (target) => target?.closest?.("[data-entry]")?.dataset.entry || null;
    const updateFocus = () => {
      if (!alive()) return;
      const element = document.activeElement;
      hover.focus(palette.contains(element) ? owner : host.contains(element) ? targetActor(element) : null);
    };
    palette.addEventListener("pointerenter", () => hover.paletteEnter());
    // Capture precedes the focused actor's blur when a pointer clicks an action after keyboard selection.
    palette.addEventListener("pointerdown", () => hover.paletteEnter(), true);
    palette.addEventListener("pointerleave", (event) => { updateFocus(); hover.paletteLeave(targetActor(event.relatedTarget)); });
    palette.addEventListener("focusin", () => hover.focus(owner));
    palette.addEventListener("focusout", () => queueMicrotask(updateFocus));
  }
  if (!current().portraits.length) {
    ready = UI.button(host, "HallFirstPortrait", T("hall.addPortraits"), [730, 560, 460, 70], async () => {
      if (pendingWrites) return; const gen = page; store.begin("layout"); await renderScene(); if (gen === page && root) addPortraits();
    }).catch(fail);
  }
  for (const entry of current().portraits) {
    const rect = rectOf(entry), button = hitButton(host, [rect.x, rect.y, rect.width, rect.height], () => {
      if (arranging()) { if (!pendingWrites) selectPortrait("click", entry.id); }
      else dialogue?.play(entry).catch(fail);
    }, titleOf(choiceOf(entry)));
    buttons.set(entry.id, button); button.dataset.entry = entry.id; button.style.touchAction = "none";
    if (arranging()) button.style.pointerEvents = "none";
    button.setAttribute("aria-pressed", String(arranging() && selected === entry.id));
    const hover = actorHover;
    if (hover) {
      button.addEventListener("pointerenter", () => hover.actorEnter(entry.id));
      button.addEventListener("pointerleave", (event) => hover.actorLeave(entry.id, palette.contains(event.relatedTarget)));
      button.addEventListener("focusin", () => hover.focus(entry.id));
      button.addEventListener("focusout", () => queueMicrotask(() => {
        if (!alive()) return;
        const focused = document.activeElement;
        hover.focus(palette.contains(focused) ? owner : host.contains(focused) ? focused?.dataset?.entry || null : null);
      }));
    }
  }
  if (arranging()) {
    portraits?.preparePick?.();
    const surface = document.createElement("div");
    surface.dataset.portraitSurface = "";
    Object.assign(surface.style, { position: "absolute", inset: "0", pointerEvents: "auto", touchAction: "none" });
    host.append(surface);
    bindPortraitPointer(surface, { root, pick: (x, y) => portraits?.pick?.(x, y) || null,
      enabled: () => alive() && arranging() && !pendingWrites,
      hover: (id, target, point) => {
        if (id) { actorHover.actorEnter(id); return; }
        // Transparent parts of the hovered portrait's box, such as the way up to its toolbar, keep its toolbar.
        const entry = owner && current().portraits.find((item) => item.id === owner), rect = entry && point && rectOf(entry);
        if (rect && point.x >= rect.x && point.x <= rect.x + rect.width && point.y >= rect.y && point.y <= rect.y + rect.height) return;
        actorHover.actorLeave(owner, palette?.contains(target));
      },
      // Selection happens on press: the release may arrive late or only as a click (WebKit). Pressing the selected
      // portrait again clears it on release, unless the press becomes a drag.
      down: (held) => {
        const entry = current().portraits.find((item) => item.id === held.id);
        if (entry) { held.x = entry.x; held.y = entry.y; }
        if (held.id && held.id === selected) held.toggle = true; else selectPortrait("click", held.id);
      },
      move: (held, point) => {
        const entry = current().portraits.find((item) => item.id === held.id); if (!entry) return;
        if (!held.dragged && Math.hypot(point.x - held.start.x, point.y - held.start.y) <= 4) return;
        if (!held.dragged) { held.dragged = true; selectPortrait("drag", held.id); }
        entry.x = Math.max(0, Math.min(1920, held.x + point.x - held.start.x));
        entry.y = Math.max(200, Math.min(1800, held.y + point.y - held.start.y));
        portraits?.setScale?.(current().portraitScale);
        const rect = rectOf(entry), button = buttons.get(entry.id);
        Object.assign(button.style, { left: `${rect.x}px`, top: `${rect.y}px` });
        if (palette && owner === entry.id) {
          const box = toolbarBox(entry, rect);
          Object.assign(palette.style, { left: `${box.x}px`, top: `${box.y}px` });
        }
      },
      end: (held, point) => { if (held.toggle && !held.dragged && point) selectPortrait("click", held.id); if (alive()) renderHits(); },
    });
  }
  if (palette) {
    host.append(palette);
    if (previousHover?.owner && buttons.has(previousHover.owner)) actorHover.restore(previousHover);
    actorHover.pin(selected);
    if (focusedActor && buttons.has(focusedActor)) buttons.get(focusedActor).focus({ preventScroll: true });
  }
  return ready;
}

function viewed(choice) {
  if (!choice || choice.kind !== "lobby") return;
  rpc("hall.viewed", { variant: choice.variant, source: choice.source }).then((keys) => {
    if (!options || !Array.isArray(keys)) return;
    for (const source of options.choices) { const index = keys.indexOf(sourceKey(source)); source.viewed = index < 0 ? null : index; }
  }).catch((error) => console.warn("Hall view history unavailable", error));
}
function configureMemorial(player, prefs = ctx?.prefs) {
  player.setDialogueFullscreen(!!fullscreen?.active());
  player.setVolume((prefs?.voiceVolume ?? 80) / 100, ctx?.info?.mute || prefs?.voiceMuted);
  player.setZoomLimit(prefs?.lobbyZoom); player.setPostProcessing(prefs?.lobbyPost !== false); player.setIntro(prefs?.lobbyIntro !== false);
}
function refreshMemorialCaptions(player, source = memorialCaptions.get(player)) {
  if (!source || source.signal.aborted) return Promise.resolve();
  memorialCaptions.set(player, source);
  const request = source.request = (source.request || 0) + 1, lang = lineLanguage();
  const pick = (line) => dialogueText(line, lineLanguage())?.text || "";
  player.setLines(undefined, pick);
  // Chinese requests also load the official Chinese script while Kivo's preferred text is being retrieved.
  return rpc("lobby.lines", { variant: source.variant, talks: source.talks, lang: lang === "zh-CN" ? "zh-TW" : lang }, { signal: source.signal })
    .then((lines) => {
      if (!source.signal.aborted && request === source.request && memorialCaptions.get(player) === source)
        player.setLines(lines, pick);
    });
}
async function memorial(choice, host, signal, player) {
  configureMemorial(player);
  const meta = await player.show(choice.core, host, choice.variant, { mod: choice.source.startsWith("mod:") ? choice.source.slice(4) : null,
    official: choice.source === "official", deferPlayback: true });
  if (signal.aborted) throw new DOMException("Memorial load cancelled", "AbortError");
  if (!meta) throw new Error("Memorial source is unavailable");
  player.frame("full"); player.interactive(true); host.querySelector(".lobby-frost")?.remove();
  if (!(await player.prepareFrame())) throw new Error("Memorial frame is unavailable");
  if (signal.aborted) throw new DOMException("Memorial load cancelled", "AbortError");
  const talks = Object.fromEntries(meta.sequences.map((sequence) => [sequence.anim, sequence.cues.map((cue) => cue.clip)]));
  refreshMemorialCaptions(player, { variant: choice.variant, talks, signal }).catch(() => {});
  return true;
}
async function renderScene() {
  if (!root) return;
  abort ||= new AbortController(); memorials ||= createMemorialPool({ load: memorial });
  const version = ++scene, gen = page, signal = abort.signal, value = current(), mode = value.mode, previousMode = sceneMode;
  const entry = mode === "memorial" && value.memorials.find((item) => item.id === value.active), choice = entry && choiceOf(entry, "lobby");
  const background = Backgrounds.backgroundChoice(value.background, options.backgrounds), name = background?.name || "";
  const portraitCold = mode === "portraits" && !portraits?.allowed;
  const memoryChanged = mode === "memorial" && choice && memorials.current()?.key !== sourceKey(choice);
  const resourcesChanged = mode !== previousMode || portraitCold || memoryChanged
    || (mode === "portraits" && (portraits.needsLoad?.(value.portraits) || backgroundName !== name));
  const live = () => !signal.aborted && gen === page && version === scene && !!root;
  if (resourcesChanged) {
    freezeScene(); portraits?.setActive?.(false); memorials.suspend(); dialogue?.stop();
  }
  let nextMemory = null, nextBackground = null, temporaryHost = null;
  const prepare = async () => {
    if (mode === "portraits") {
      memorials.cancelPending();
      if (portraitCold) {
        clearPortraitScene();
        portraitSceneHost = layer(art, "lobby", 0); portraitSceneHost.style.visibility = "hidden";
        backgroundHost = layer(portraitSceneHost, "", 0); portraitHost = layer(portraitSceneHost, "", 1);
        nextBackground = document.createElement("div"); Object.assign(nextBackground.style, { position: "absolute", inset: "0" });
        const backgroundReady = Backgrounds.render(background, nextBackground, { signal, choices: options.backgrounds });
        const actorReady = createPortraits(portraitHost, value.portraits, choiceOf, { signal, globalScale: value.portraitScale, onError: fail,
          onFrame: () => sceneCamera?.flush(),
          onRecover: () => { if (!signal.aborted) { clearPortraitScene(); if (current().mode === "portraits") renderScene().catch(fail); } } });
        const [result] = await Promise.all([actorReady, backgroundReady]);
        if (!live()) { result.dispose(); return; }
        portraits = result; portraits.setActive?.(false); backgroundCache.set(name, nextBackground);
        dialogue = createDialogue({ host: subtitle, actorOf: (item) => ({ actor: portraits?.actors.get(item.id), rect: rectOf(item) }), choiceOf,
          info: () => ctx.info, prefs: () => ctx.prefs, onError: fail, signal,
          bounds: contentBounds(root.getBoundingClientRect(), [...chrome.querySelectorAll("button")].map((button) => button.getBoundingClientRect()), document.querySelector(".nav")?.getBoundingClientRect()) });
      } else {
        nextBackground = backgroundCache.get(name);
        if (backgroundName !== name && !nextBackground) {
          nextBackground = document.createElement("div"); Object.assign(nextBackground.style, { position: "absolute", inset: "0" });
          await Backgrounds.render(background, nextBackground, { signal, choices: options.backgrounds });
          if (!live()) return;
          backgroundCache.set(name, nextBackground);
        }
        await portraits.update(value.portraits, value.portraitScale, { retain: store.saved().portraits });
      }
    } else if (choice) {
      if (!memoryChanged && mode === previousMode) { memorials.cancelPending(); nextMemory = memorials.current(); }
      else {
        nextMemory = await memorials.prepare(choice, () => { const host = layer(art, "lobby", 1); host.style.visibility = "hidden"; return host; }, signal);
        if (nextMemory && !memoryChanged && !(await nextMemory.player.prepareFrame())) {
          memorials.invalidate(nextMemory); nextMemory = null; throw new Error("Memorial frame is unavailable");
        }
      }
    } else {
      memorials.cancelPending(); temporaryHost = layer(art, "", 1); temporaryHost.style.visibility = "hidden";
      await Backgrounds.render(background, temporaryHost, { signal, choices: options.backgrounds });
    }
    if (!live()) return;
    let nextChrome = null;
    while (live() && !nextChrome) nextChrome = await renderChrome({ publish: false });
    if (!live()) return;
    const nextHits = document.createElement("div"); nextHits.className = "layer-host";
    Object.assign(nextHits.style, { position: "absolute", inset: "0", zIndex: "2" });
    if (mode === "portraits") await renderHits(nextHits);
    else if (!choice) {
      await UI.caption(nextHits, T(entry ? "hall.missing" : "hall.empty"), [510, 500, 900]);
      await UI.button(nextHits, "HallFirstMemorial", T("hall.adjust"), [730, 560, 460, 70], () => adjustMemorials());
    }
    if (!live()) return;
    if (mode === "portraits") {
      if (nextBackground && backgroundName !== name) { backgroundHost.replaceChildren(nextBackground); backgroundName = name; }
      const savedBackground = Backgrounds.backgroundChoice(store.saved().background, options.backgrounds)?.name || "";
      for (const key of backgroundCache.keys()) if (key !== name && key !== savedBackground) backgroundCache.delete(key);
      if (editing()) dialogue?.stop();
      if (portraitSceneHost) portraitSceneHost.style.visibility = "";
      const memory = memorials.current(); if (memory) memory.host.style.visibility = "hidden";
      portraits?.setActive?.(true); subtitle.style.visibility = "";
    } else {
      if (portraitSceneHost) portraitSceneHost.style.visibility = "hidden";
      subtitle.style.visibility = "hidden";
      if (nextMemory) {
        const changed = memorials.current() !== nextMemory;
        memorials.commit(nextMemory); nextMemory.host.style.visibility = "";
        if (resourcesChanged) configureMemorial(nextMemory.player);
        nextMemory.player.setDialogueFullscreen(!!fullscreen?.active());
        nextMemory.player.resume();
        if (changed) viewed(choice);
      } else if (temporaryHost) {
        const memory = memorials.current(); if (memory) memory.host.style.visibility = "hidden";
        temporaryHost.style.visibility = "";
      }
    }
    for (const child of [...art.children]) if (child !== portraitSceneHost && child !== memorials.current()?.host && child !== temporaryHost) child.remove();
    chrome.replaceChildren(nextChrome); hits.replaceWith(nextHits); hits = nextHits; sceneMode = mode;
    sceneCamera?.sync({ reset: editing() });
    sceneCover?.remove(); sceneCover = null;
  };
  try {
    if (resourcesChanged) await Loading.hold(prepare, { until: new Promise((resolve) => signal.addEventListener("abort", resolve, { once: true })) });
    else await prepare();
  } catch (error) {
    memorials?.discard(nextMemory); temporaryHost?.remove();
    if (live()) {
      sceneCover?.remove(); sceneCover = null;
      if (previousMode === "portraits") { if (portraitSceneHost) portraitSceneHost.style.visibility = ""; portraits?.setActive?.(true); }
      else memorials?.resume();
    }
    throw error;
  } finally {
    if (!live()) { memorials?.discard(nextMemory); temporaryHost?.remove(); }
  }
}

export function applyPrefs(prefs) {
  if (ctx) ctx.prefs = prefs; dialogue?.preferencesChanged();
  const memory = memorials?.current(); if (memory) configureMemorial(memory.player, prefs);
}
function changePortrait(entry) {
  if (!arranging() || pendingWrites) return;
  const choices = portraitReplacements(entry, options.choices, current().portraits); if (!choices.length) return;
  closeEditor(); const gen = page;
  editor = openPicker({ kind: "spr", choices, draft: store, onError: fail, isSaving: () => pendingWrites > 0,
    onDone: () => closeEditor(), onReplace: async (choice) => {
      if (gen !== page || !arranging() || pendingWrites || !store.replacePortrait(entry.id, choice, options.choices)) return;
      closeEditor(); await renderScene().catch(fail);
    } });
}
function addPortraits() {
  if (!editing()) store.begin("layout"); closeEditor(); const gen = page;
  editor = openPicker({ kind: "spr", choices: options.choices, draft: store, onError: fail, isSaving: () => pendingWrites > 0,
    onDone: async () => { if (gen !== page) return; closeEditor(); await renderScene(); }, onChange: () => {} });
}
function adjustMemorials() {
  if (!root) return; store.begin("memorial"); closeEditor();
  editor = openPicker({ kind: "lobby", choices: options.choices, draft: store, onError: fail, isSaving: () => pendingWrites > 0,
    onSave: () => saveDraft(), onCancel: () => cancelDraft(), onChange: () => {} });
}

function chooseBackground() {
  closeEditor();
  const gen = page, work = thumbnailScope(), positions = new Map(); let selectedBackground = Backgrounds.backgroundChoice(current().background, options.backgrounds)?.name || "";
  const modal = UI.createModal(T("hall.background"), () => close());
  const close = () => { work.cancel(); modal.close(); if (editor?.modal === modal) editor = null; };
  editor = { modal, close };
  const items = options.backgrounds.map((background) => ({ key: background.name, label: Backgrounds.label(background), full: Backgrounds.label(background),
    thumb: lazyThumbnail(() => Backgrounds.preview(background, { signal: work.signal }), work.signal) }));
  const draw = () => modal.draw(async (target, alive, signal) => {
    const list = UI.virtualArea(target, UI.backgroundPickerBox(), Math.ceil(items.length / 2), 280,
      (cell, row) => Promise.all(items.slice(row * 2, row * 2 + 2).map((item, index) => UI.card(cell, item, {
        x: 190 + index * 370, y: 110, scale: .4, selected: item.key === selectedBackground,
        onPreview: () => { selectedBackground = item.key; draw().catch(fail); } }))),
      { key: "hall-backgrounds", signal, positions, onError: fail });
    await list.ready;
    if (!alive()) return;
    const panel = layer(target, "", 25); Object.assign(panel.style, { inset: "auto", left: "940px", top: "260px", width: "1920px", height: "1080px", transform: "scale(.43)", transformOrigin: "0 0" });
    await Backgrounds.render(Backgrounds.backgroundChoice(selectedBackground, options.backgrounds), panel, { signal: work.signal, choices: options.backgrounds });
    await UI.caption(target, items.find((item) => item.key === selectedBackground)?.label || "", [940, 770, 800]);
  }, [{ label: T("popup.cancel"), fn: close }, { label: T("hall.useBackground"), fn: async () => {
    if (gen !== page || !root) return;
    if (store.kind()) current().background = selectedBackground;
    else { const next = clone(store.saved()); next.background = selectedBackground; const result = await persist(next); if (gen !== page || !root) return; store.accept(result); }
    close(); await renderScene();
  } }]);
  draw().catch(fail);
}
async function fullscreenExit(close) {
  const source = clone(NGUI.find(layouts.lobby, "Front/Right/ScreenshotMode/ModeInformation/Btn_Screen_Off"));
  source.name = "HallFullscreenExit"; source.pos = [0, 0]; source.active = true;
  source.children = source.children.filter((node) => ["BG", "Icon"].includes(node.name));
  const body = NGUI.find(source, "BG"); body.name = "Bg"; body.pos = [0, 0];
  const host = document.createElement("div"); host.className = "layer-host hall-fullscreen-exit";
  const view = await NGUI.render(source, host, { scale: S, origin: [TOOLBAR.right - TOOLBAR.iconWidth / 2, 145] });
  await alignIcon(view, "HallFullscreenExit/Icon", "HallFullscreenExit/Bg", { height: 48 * S, maxWidth: 104 * S, center: "bounds", alphaCutoff: 32 });
  const button = Popup.hit(host, view, "HallFullscreenExit/Bg", close, { name: T("lobby.exit"), action: "hall-fullscreen-exit" });
  bindCorner(host, NGUI.rect(view.nodes.get("HallFullscreenExit/Bg").els[0]), { corner: "top-right" });
  return { host, button };
}
export const closeRevision = () => JSON.stringify([page, store?.current(), store?.kind(), pendingWrites]);

export function canLeave() {
  if (!root || !store) return Promise.resolve(true);
  if (leavePending) return leavePending;
  const gen = page; fullscreen?.leave({ refocus: false }); editor?.closeNested?.();
  leavePending = (async () => {
    await saving.catch(() => {}); if (gen !== page || !root) return true;
    if (!store.dirty()) { needsSettlement ||= store.kind() === "layout"; closeEditor(); store.cancel(); selectPortrait("end"); return true; }
    const answer = await new Promise((resolve) => {
      leaveResolve = resolve;
      const modal = UI.createModal(T("hall.unsaved"), () => { modal.close(); leavePrompt = null; leaveResolve = null; resolve("stay"); }, { level: 3 }); leavePrompt = modal;
      Popup.message({ host: modal.host, title: T("hall.unsaved"), width: 1450, lines: [T("hall.unsavedText")], captionSite: "hall.unsavedChanges",
        buttons: [{ label: T("hall.keepEditing"), value: "stay" }, { label: T("hall.discard"), value: "discard" }, { label: T("hall.saveLeave"), value: "save", primary: true }],
      }).then(({ button }) => { modal.close(); leavePrompt = null; leaveResolve = null; resolve(button || "stay"); })
        .catch((error) => { fail(error); modal.close(); leavePrompt = null; leaveResolve = null; resolve("stay"); });
    });
    if (answer === "stay") return false;
    if (gen !== page || !root) return true;
    needsSettlement = true;
    if (answer === "save") { try { await saveDraft({ render: false }); } catch (error) { fail(error); return false; } }
    else { store.cancel(); selectPortrait("end"); }
    closeEditor(); return true;
  })().finally(() => { leavePending = null; });
  return leavePending;
}
export async function settleView() {
  if (!root || !needsSettlement) return;
  needsSettlement = false; await renderScene();
}
export async function show(context, data = null) {
  hide(); ctx = context; const gen = page; await saving.catch(() => {}); if (gen !== page) return;
  root = document.getElementById("student-hall"); if (!root) throw new Error("Student Hall host unavailable"); root.replaceChildren();
  art = layer(root, "lobby", 1); hits = layer(root, "layer-host", 2); subtitle = layer(root, "layer-host", 4); chrome = layer(root, "layer-host hall-chrome", 5);
  const [saved, available, lobby, slot] = await Promise.all([data?.mode ? data : rpc("hall.get"), rpc("hall.options"), NGUI.layout("UILobby"), NGUI.layout("UILobbyCharacterSelectPopup_Slot"), UI.ready()]);
  if (gen !== page) return; store = createDraft(saved); options = available; layouts = { lobby, slot };
  fullscreen = createFullscreen({ root, stage: document.getElementById("stage"), chrome, prepareExit: fullscreenExit,
    popupOpen: () => Popup.anyOpen(), returnFocus: () => chrome?.querySelector('[data-action="hall-fullscreen"]'),
    onChange: (active) => { memorials?.current()?.player.setDialogueFullscreen(active); actorHover?.clearPointer(); renderHits(); } });
  sceneCamera = createSceneCamera({ host: root, enabled: () => !!store && current().mode === "portraits" && !editing(),
    blocked: () => !root || pendingWrites > 0 || Popup.anyOpen() || Loading.held() || Loading.covering(), onChange: paintSceneView });
  document.addEventListener("keydown", onKey); await renderScene();
}
function onKey(event) {
  if (root && arranging() && selected && event.key === "Escape" && !Popup.anyOpen() && !event.defaultPrevented && !event.isComposing && event.keyCode !== 229) {
    event.preventDefault(); event.stopImmediatePropagation(); selectPortrait("escape"); return;
  }
  if (!root || current().mode !== "memorial" || Popup.anyOpen() || !["ArrowLeft", "ArrowRight"].includes(event.key) || event.repeat) return;
  if (/INPUT|TEXTAREA|SELECT/.test(event.target?.tagName) || event.target?.isContentEditable) return;
  event.preventDefault(); step(event.key === "ArrowLeft" ? -1 : 1).catch(fail);
}
export function hide() {
  const owned = !!root; sceneCamera?.dispose(); sceneCamera = null; fullscreen?.dispose(); fullscreen = null; ++page; ++toolbar; document.removeEventListener("keydown", onKey);
  closeEditor(); leavePrompt?.close(); leavePrompt = null; leaveResolve?.("stay"); leaveResolve = null;
  if (owned) stopScene(); else { abort?.abort(); abort = null; }
  root?.replaceChildren(); root = art = chrome = hits = subtitle = null; store = null; selected = null; actorHover = null; needsSettlement = false;
}
export async function relabel() {
  const gen = page;
  if (root) {
    dialogue?.relabel();
    const memory = memorials?.current(); if (memory) refreshMemorialCaptions(memory.player).catch(() => {});
    fullscreen?.relabel(T("lobby.exit")); await renderChrome(); if (gen === page) await editor?.redraw?.();
  }
}
