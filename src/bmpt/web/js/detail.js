import * as Photo from "./photography/controller.js";
import { notify } from "./popup.js";
import { jobProgress, progressName } from "./job-progress.js";
// Detail screen, built from the game's own prefab (UICharacterDetail) by ngui.js. The prefab supplies every frame,
// sprite, tint, incline and position; this file swaps labels and textures, hides what the tool has no use for, and adds
// what the game has no screen for: variant chips, the expression strip and the portrait and lobby switchers. A family
// is one base character with all of its variant portraits; each variant's game file carries one portrait (its own
// skeleton, another variant's skeleton or an added portrait), and likewise one memorial lobby (its own or an added
// one). Added portraits and lobbies belong to the variant whose game file they replace (mod.target); added portraits
// can be worn by the other variants of the family, lobbies are not offered to them. Switching writes the chosen one
// into the game files (switch-sources.js, actions.apply).
// A change inside the screen (another variant, a tab, an applied portrait) prepares its parts behind the loading
// cover and swaps them in one frame, after the primary preview and required UI images settle.
import { isSpecial, specialFraming } from "./special-view.js";
import * as NGUI from "./ngui.js";
import { resizeButton } from "./button-layout.js";
import { horizontalScroll } from "./horizontal-scroll.js";
import { explorerLayout } from "./explorer-button.js";
import { latestLoad, loadPlayer } from "./spine-load.js";
import { ensureSpine, spineAllowed } from "./spine-runtime.js";
import { decodeAll } from "./image-decode.js";
import { withTimeout, nextFrame } from "./async.js";
import { previewError } from "./preview-error.js";
import { attachHint } from "./hover-hint.js";
import * as Loading from "./loading.js";
import * as ModAlias from "./mod-alias.js";
import * as ModExport from "./mod-export.js";
import * as Compatibility from "./mod-compatibility.js";
import { show as showCompare } from "./mod-compare.js";
import * as Lobby from "./lobby.js";
import * as Visual from "./visual-mode.js";
import { QUEUEABLE, current as modsState, entryLabel, listSep, refused } from "./mod-switch.js";
import * as Prompts from "./prompts.js";
import { anyOpen as popupOpen } from "./popup.js";
import { rpc, spineBase, spinePrefix, loadSpineAssets, waitJob } from "./api.js";
import { L, T, errorText, language, platform } from "./i18n.js";
import { forget, idleAnimation, prioritise, thumbnailScope } from "./thumbs.js";
import { modelThumbnail, modelStillVersion } from "./model-still.js";
import { lazyThumbnail } from "./lazy-thumbnail.js";
import { portraitViewport } from "./portrait-view.js";
import { colourSwitchCard } from "./switch-card.js";
import { clearUnseen, hasUnseen as hasFresh, shownUnseen, switchUnseen } from "./unseen-assets.js";
import { customAliases as aliasesOf, modName, modOriginalName, studentName } from "./names.js";
import { setTitle } from "./nav.js";
import { KEYS, deltaOf, pacer, typing } from "./step-keys.js";
import { closable, closed as popupClosed, frameReady, hit as hitNode, listPopup, message, notice, popupFrame, scrollArea } from "./popup.js";
import { ALIAS, DETAILS, aliasFrame, detailsFrame, popupContent } from "./popup-frames.js";
import { statsBox } from "./stats-box.js";
import { CHIP, COLOUR, CONTROL, SPACE, TYPE } from "./ui-tokens.js";
import { container, row, stack, textItem, toNode } from "./ui-grid.js";
import { candidateStatus, detailStatus, selectionStatus, selectionError, intendedKey, isApplied, availableTab, coreOf, costumeCandidates, hasAsset, hasKind, primaryKind, appliedKey as keyOf, appliedOf, candidates as listSources, partsOf, popupMode, modelSource, previewBase, previewChanged, previewKey, sourceOf, switchKindOf } from "./switch-sources.js";
import { $, DOT, RECRUIT, S, TAN10, advanceOf, at, clone, glyphsOf, hitButton, markedLabel, off, text, widget, wrapPath } from "./util.js";

const { node, sprite, label, find, rect } = NGUI;
const fitText = (...a) => NGUI.fitText(...a);
// fitText for a box: where nothing fits from fsMin up (fitText then gives up on one line at fsMin - 8, however wide),
// the same again from smaller sizes, down to 60 % of fsMin
function fitIn(str, fsMax, fsMin, maxW, maxLines = 2) {
  let f = fitText(str, fsMax, fsMin, maxW, maxLines), min = fsMin;
  while (f.lines === 1 && f.fs < min && min - 8 >= fsMin * 0.6) { min -= 8; f = fitText(str, min + 6, min, maxW, maxLines); }
  return f;
}
const ORIGIN = [960, 515]; // prefab centre; 25 px above the stage centre so the panels clear the nav bar
const toStage = (x, y) => [ORIGIN[0] + x * S, ORIGIN[1] - y * S];
const INFO = "RightInfo/Target/Active/1_NormalInfo";
const stage = () => document.getElementById("stage");
// the portrait's players cover the stage (.portrait in app.css), so nothing the game shows is cut at a box edge
const PORTRAIT_BOX = { left: 0, top: 0, width: 1920, height: 1080 };

let ctx = null, fam = null, variantKey = null, layouts = null;
// Prefabs and derived UI values belong to the selected installation.
NGUI.onResourceChange?.(() => { layouts = null; fam = null; photoFaces.clear(); });
let previewEnabled = true;
let tab = "portrait", fullscreen = false, expression = "00", expanded = false, exprScroll = 0;
let mainPlayer = null, blinkTimer = null, pending = null, pickerSel = null, lobbySeq = null, busy = false, lobbyShot = null;
let modelShot = null, modelActions = [], modelAction = null, modelGeneration = 0;
// three.js and the model viewer load with the first 3D model
let modelLib = null;
const ModelLib = () => (modelLib ||= import("./model.js"));
let renderToken = 0, exprToken = 0, chipToken = 0, spineToken = 0, leftToken = 0;

// --- readiness ---
const HARD_CAP = 15000;  // individual failed assets settle with an error before the screen is revealed
const LINES_CAP = 1500;  // the lobby's lines from Kivo (a network call) likewise
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const frames = async (n) => { while (n-- > 0) await nextFrame(); };
function renderFailed(err, source = null) {
  console.error("Detail render failed", ...(source ? [source] : []), err);
  previewError(err, source, "detail");
  if (open) notify(T("detail.failed"), { kind: "error", error: err });
}

// Every visible swap goes through commit(): at once, or, while an in-place change is prepared, queued and applied
// together with every other part of that change in one frame. A later re-render (a face card, play all) is at once.
let batch = null;
const commit = (fn) => (batch ? batch.push(fn) : fn());
// A render of a part is shown when it is the newest one, or, while a change is prepared, when it is newer than the
// one on screen and belongs to that change: a re-render still running at the commit (late lobby lines) then follows
// it instead of keeping the old part in the commit.
const applied = { screen: 0, chips: 0, expr: 0 };
const showable = (part, my, latest, gen) => open && (my === latest || (!!batch && gen === showGen && my > applied[part]));
// the screen is shown (from show() until hide()): what a show, a tab change or a job started for it starts nothing
// once it is hidden (the left side's players least of all)
let open = false, suspended = false, pageToken = 0;
let leftPage = Promise.resolve(), leavePage = () => {};
// a generation of the screen: a newer show, tab change or hide ends the wait of the one before
let showGen = 0, endWait = () => {};
let thumbnailWork = thumbnailScope();
const thumb = (base, frame = "bust", expr = null, lobby = false, framing = null, background = variant()?.bg, work = thumbnailWork) => {
  if (!base) return null;
  if (!spineAllowed()) return collectionPortrait(base);
  return work.thumb(base, frame, expr, lobby, framing, frame === "face" ? null : background);
};
function collectionPortrait(base) {
  for (const v of fam?.variants || []) {
    if (!v.spr) continue;
    if (spineBase("official-spr", v.spr) === base) return v.portrait || null;
  }
  return null;
}
// a model card's still: another variant's costume shows that variant's own model
const modelPicture = (c, v = variant(), work = thumbnailWork) => c.kind === "variant"
  ? modelThumbnail(c.variant.key, "official", { version: modelStillVersion(fam, c.variant, modelGeneration), signal: work.signal })
  : c.kind !== "mod" && !coreOf(v, "model") && !v.fieldModel ? null
    : modelThumbnail(v.key, c.kind === "mod" ? `mod:${c.id}` : "official",
      { version: modelStillVersion(fam, v, modelGeneration), signal: work.signal });
function begin() {
  endWait();
  thumbnailWork.cancel();
  thumbnailWork = thumbnailScope();
  screenPics = exprPics = chipPics = [];
  const gen = ++showGen;
  return { gen, ended: new Promise((resolve) => { endWait = resolve; }) };
}
// work() renders every part; resolves once they are ready (or given up on). In place, their swaps are queued
// meanwhile and applied in one animation frame; a generation that was taken over drops its queue (the newer one
// renders everything again).
async function prepare({ gen, ended }, inPlace, work) {
  const queued = inPlace ? [] : null;
  batch = queued;
  try {
    await Promise.race([Promise.resolve().then(work), ended]);
  } catch (err) {
    if (gen === showGen) renderFailed(err);
  }
  if (gen !== showGen) return false;
  if (queued) {
    await nextFrame();
    if (gen !== showGen) return false;
    for (const fn of queued) try { fn(); } catch (err) { console.error(err); }
    batch = null;
  }
  if (open && fam) markSeen(shownUnseen(fam, variant(), tabKind(), modsOn()));
  return true;
}
// Required UI pictures settle before publication; optional thumbnails keep their
// existing scopes and fill locally without holding the primary preview.
let screenPics = [], exprPics = [], chipPics = [];
const picturesReady = (pictures, ...roots) => {
  const { signal } = thumbnailWork;
  // roots may be missing (a part not drawn) and descendants may lack a style (e.g. text holders): skip both
  const elements = roots.filter(Boolean).flatMap((root) => [root, ...(root.querySelectorAll?.("*") || [])]);
  return Promise.allSettled(pictures).then(() => {
    if (signal.aborted) return;
    for (const el of elements) if (el?.style?.transition) el.style.transition = "none";
    return NGUI.decoded(elements, HARD_CAP, null, { signal });
  });
};
function fillPictures(pictures, ...roots) {
  const { signal } = thumbnailWork;
  picturesReady(pictures, ...roots).catch((error) => { if (!signal.aborted) renderFailed(error); });
}
async function settlePictures(read, signal) {
  let before;
  do {
    before = read();
    await Promise.allSettled(before.flat());
  } while (!signal.aborted && before.some((part, i) => part !== read()[i]));
}
async function settleImages() {
  const { signal } = thumbnailWork;
  await settlePictures(() => [screenPics, exprPics, chipPics], signal);
}

// --- names, candidates, state ---
const originalName = (v) => L(v.names)?.full ?? v.key;
const fullName = (v) => studentName(v, originalName(v));
const shortName = (v) => studentName(v, L(v.names)?.short ?? v.key);
// the current variant's own aliases (webui/state.py keeps the same limits): the roster search
// finds this skin by all of them; the first one is the nameplate's main name
const ALIAS_MAX = ALIAS.max, ALIAS_LENGTH = 24;
const customAliases = () => aliasesOf(variant());
const aliasList = () => customAliases().join(/^(zh|ja)/.test(language()) ? "\u3001" : ", ");
const variant = () => fam.variants.find((v) => v.key === variantKey) || fam.variants[0];
const familyMods = () => fam.variants.flatMap((v) => v.modList || []);
const isFav = (v) => !!v.favorite;
const hasLobby = (v) => hasKind(v, "lobby");
const hasModel = (v) => hasKind(v, "model");
const modLabel = (m) => modName(m, familyMods(), T);
const repairedId = (c) => c?.mod?.repaired_id && familyMods().some((m) => m.id === c.mod.repaired_id) ? c.mod.repaired_id : null;
const repairAction = (c) => repairedId(c) ? "remap.go" : "act.update";
// a candidate's skeleton: a mod's preview (its parts over the official ones), else the variant's own portrait or lobby
const baseOf = (c, kind = "spr") => (c.kind === "mod" ? spineBase("mod", c.id) : c.core ? spineBase(`official-${kind}`, c.core) : null);
// The installed portrait comes from the current game files; with mods off, preview the configured source.
const currentBase = () => variantBase(variant());
// no deadline: the read waits for a job that holds the library lock, as the route's does (main.js), and the screen stays as
// it is meanwhile. A caller drops the result when the screen was read anew in the meantime (left and entered again, or
// reloaded by another action), so that an old read never replaces a newer family.
const readFamily = (key) => rpc("family.get", { key });

// what the switch card of a kind offers (switch-sources.js), with the names and marks the cards show: the variant's
// own portrait or lobby and its added ones, then for the portrait the other variants' ones and their added ones
function candidates(v = variant(), kind = "spr") {
  return listSources(fam, v, kind, costumeState.get(v.key)).map((c) => (c.kind === "mod"
    ? { ...c, label: modLabel(c.mod) }
    : { ...c, label: shortName(c.variant), recruit: c.variant.recruitable, fav: isFav(c.variant) }));
}
// Switch 3D Model offers another variant's costume only once that pair was built and exported for these files
// (model.costumes, BL-122): the verdicts by variant, read when the 3D tab shows and checked to their end
// (model.checkCostumes) in the background, or, when the switch opens first, while it waits.
const costumeState = new Map(), costumeChecks = new Map();
function costumesReady(v, { wait = false } = {}) {
  const others = costumeCandidates(fam, v).map((o) => o.key);
  if (!others.length) return Promise.resolve(false);
  const key = `${v.key}|${others.join(",")}`;
  let check = costumeChecks.get(key);
  if (!check) {
    check = (async () => {
      const known = await rpc("model.costumes", { variant: v.key, others });
      costumeState.set(v.key, { ...(costumeState.get(v.key) || {}), ...known });
      // the Apply card follows the shown costume's verdict
      if (open && variant() === v && tab === "model") void renderScreen();
      const unknown = others.filter((o) => known[o] == null);
      if (!unknown.length) return false;
      const id = await rpc("model.checkCostumes", { variant: v.key, others: unknown });
      const result = await waitJob(id, (p) => check.report?.(p));
      costumeState.set(v.key, { ...(costumeState.get(v.key) || {}), ...result });
      return true;
    })().finally(() => costumeChecks.delete(key));
    costumeChecks.set(key, check);
  }
  if (!wait) return check;
  return Loading.task((report) => { check.report = report; return check; },
    { label: T("progress.compatibility", { name: T("tab.model") }) });
}
// a costume past the stretch limit (or refused) chosen as the 3D model's preview: shown, never applied
const unfitCostume = (v, kind, key) => kind === "model" && typeof key === "string" && key.startsWith("v:")
  && !!costumeState.get(v.key)?.[key.slice(2)] && costumeState.get(v.key)[key.slice(2)] !== "ok";
// After a background check: the open Switch 3D Model popup lists what it found.
function costumesChecked(v) {
  if (open && variant() === v && tab === "model") void renderScreen();
  if (open && variant() === v && popupKind === "model" && switchPopupPage === pageToken && !$("popup").hidden) void renderPopup();
}

// the kind of game file the shown tab is about, and the newest date of a kind's files
const tabKind = () => (tab === "lobby" || tab === "model" ? tab : "spr");
// The switch card uses the kind of the shown asset tab.
const switchKind = () => switchKindOf(tab);
const dateOf = (v, kind) => (kind === "spr" ? v.date : partsOf(v, kind).map((f) => f.date).filter(Boolean).sort().pop());
function crcOf(v, kind = "spr") {
  const states = partsOf(v, kind).map((f) => f.crc);
  return states.includes("needs_fix") ? "needs_fix" : states.includes("fixed") ? "fixed" : "ok";
}
const appliedKey = (v = variant(), kind = primaryKind(v)) => keyOf(v, kind);
const shownSource = (v = variant(), kind = primaryKind(v)) => previewKey(v, kind, modsOn());
const previewAvailable = (v, kind) => !!coreOf(v, kind) || shownSource(v, kind).startsWith("m:");
const variantBase = (v, kind = "spr") => previewAvailable(v, kind) ? previewBase(v, kind, modsOn(), fam.variants) : null;
// The configured portrait or lobby while mods are off; otherwise what the game files carry.
const current = (kind = "spr") => candidates(variant(), kind).find((c) => c.key === shownSource(variant(), kind)) || candidates(variant(), kind)[0];
// a candidate's name on the cards: another variant's mod with that variant's name first
const candidateLabel = (c, v = variant()) => (c.kind === "mod" && !c.mod.alias && c.origin && c.origin.key !== v.key ? `${shortName(c.origin)} - ${c.label}` : c.label);
// The lobby's preview source as a label: the original, a mod's or another variant's name, or "replaced".
const lobbyLabel = (v) => sourceLabel(v, "lobby");
function sourceLabel(v, kind) {
  const key = shownSource(v, kind);
  if (key === "self") return T(`${kind}.original`);
  const c = candidates(v, kind).find((x) => x.key === key);
  return c ? candidateLabel(c, v) : T("status.replaced");
}
const gameFile = (v = variant(), kind = "spr") => partsOf(v, kind)[0]?.file || null;
const baseName = (path) => (path ? path.split(/[\\/]/).pop() : "—");
const sizeText = (bytes) => (!bytes ? "—" : bytes < 1048576 ? `${Math.max(1, Math.round(bytes / 1024))} KB` : `${(bytes / 1048576).toFixed(1)} MB`);

const svg = (body) => `data:image/svg+xml,${encodeURIComponent(body)}`;
const TEXTURES = {
  "svg:stop": svg('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 48 48"><rect x="10" y="10" width="28" height="28" rx="4" fill="#2b496e"/></svg>'),
  "svg:folder": svg('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 48 48"><path fill="#2d5164" d="M5 12a3 3 0 0 1 3-3h11l4 5h17a3 3 0 0 1 3 3v19a3 3 0 0 1-3 3H8a3 3 0 0 1-3-3Z"/></svg>'),
};

// Keep the card readable; disabled colours come from FilterSort's disabled button.
export function actionState(card, enabled, disabledTemplate = null) {
  find(card, "Normal").active = true;
  const lock = find(card, "Lock_Cover");
  if (lock) lock.active = false;
  if (!enabled && disabledTemplate) {
    const color = find(disabledTemplate, "BG (1)")?.widgets.find((w) => "sprite" in w)?.color;
    const body = find(card, "Bg")?.widgets.find((w) => "sprite" in w);
    if (body && color) body.color = color;
  }
  const empty = find(card, "Empty");
  if (empty) empty.active = false;
}

// an upright texture inside a slanted body is clipped to the body (the game does this with panel clipping)
const clipped = (name, pos, url, win, img, opts = {}) => node(name, pos, [{ clipImage: url, placeholder: opts.placeholder, w_: win.w, h_: win.h, pivot: 4, depth: opts.depth ?? 9,
  placeholderRect: { x: img.dx, y: img.dy, w: img.w, h: img.h },
  incline: 10, radius: win.r ?? 20, color: opts.color ?? "#ffffff/1.00", tint: opts.tint, imgSize: "100% 100%", imgPosition: "0 0",
  imgLeft: `${(win.w / 2 + img.dx - img.w / 2) * S}px`, imgTop: `${(win.h / 2 - img.dy - img.h / 2) * S}px`, imgWidth: `${img.w * S}px`, imgHeight: `${img.h * S}px`,
  imgMask: opts.mask, imgEdge: opts.edge }]);

// --- row 4 geometry ---
const ROW4_Y = -552;                       // centre of row 4's visible part (Bg_W4: top -436, 234 tall, shadow below)
const BOX_RIGHT = 332;                     // the first skill card's right edge: the box ends there
// UICharacterDetail's Skill + SkillData + Bg supply these columns, pitch and bodies; keep the prefab's own values.
const COLS = [461.9, 707.5, 953.2];        // skill cards 2-4 (Skill at 202 + SkillData x + Bg x 24)
const BTN_COL = [1079.8, 1284.8];          // expression pile's column, derived from the skill/Add cards; keep its x edges
const CARD = { w: 242, h: 209 };           // Common_Btn_BG like the skill cards; the visible body is h - 13 (shadow below)
const PITCH = 245.7;
const LOCAL_FRAME = { cx: 0, cy: 0, w: 0, h: 0 };

// --- the static screen: background, arrows, tabs, right panel, nameplate frame ---
function screenTree() {
  const t = clone(layouts.list);
  off(t, "Top", "NUITop_Scenes", "LeftInfo/CharInfo_Disable", "LeftInfo/CharCombatStyle", "RightInfo/Target/Disable",
    "RightInfo/Target/Active/2_LevelUp", "RightInfo/Target/Active/3_Transcendence", "RightInfo/Target/Active/FullCollier", "RightInfo/Target/Active/Effect");
  const v = variant(), sk = switchKind(), c = current(sk || "spr"), kind = tabKind();
  // layer 1 of the panel: measured on the game screenshots it lets about 55 % of the background through
  widget(t, "RightInfo/Target/Active/Bg").color = "#0c284b/0.45";

  // tabs: portrait / memorial lobby / 3D model (disabled where unavailable)
  const tabs = [["Tab_1", "tab.portrait", "portrait", hasKind(v, "spr")], ["Tab_2", "tab.lobby", "lobby", hasLobby(v)], ["Tab_3", "tab.model", "model", hasModel(v)]];
  // the three captions share one size: the largest at which every one fits its tab on one line
  const tabLabels = tabs.map(([, key, id]) => T(isSpecial(v) && id === "lobby" ? "special.background" : key));
  const tabSize = NGUI.fitGroup(tabLabels, 52, 36, 360, 1)[0].fs;
  for (const [index, [node_, key, id, enabled]] of tabs.entries()) {
    const base = `RightInfo/Target/Active/CharacterTab/${node_}`;
    const label = tabLabels[index];
    for (const state of ["Tab_On", "Tab_Off"]) text(t, `${base}/${state}/Label`, label, { w_: 400, fontSize: tabSize });
    if (!enabled) widget(t, `${base}/Tab_Off/Label`).color = "#ffffff/0.35";
    at(t, `${base}/Tab_On`).active = id === tab;
    at(t, `${base}/Tab_Off`).active = id !== tab;
    const dot = find(t, `${base}/${id === "portrait" ? "Dot" : id === "lobby" ? "Dot (1)" : "Dot (2)"}`);
    if (dot) dot.active = hasFresh(v, id === "portrait" ? "spr" : id);
  }
  // the separator on a tab's right edge goes when either neighbour is active
  if (tab !== "portrait") { const l = find(t, "RightInfo/Target/Active/CharacterTab/Tab_1/Tab_Off/Line"); if (l) l.active = tab !== "lobby"; }
  { const l = find(t, "RightInfo/Target/Active/CharacterTab/Tab_2/Tab_Off/Line (1)"); if (l) l.active = tab === "portrait"; }
  // row 1: portrait information
  text(t, `${INFO}/Title_Bg/Label_Locale`, T(isSpecial(v) && tab === "lobby" ? "special.background" : `info.title.${tab}`), { w_: 600 });
  text(t, `${INFO}/Detail_Btn/Label_Locale`, T("info.details"));
  const crcState = crcOf(v, kind);
  const crc = crcState === "fixed" ? ["crc.fixed", COLOUR.success] : crcState === "needs_fix" ? ["crc.needed", COLOUR.warning] : ["crc.original", "#000000"];
  const now = tab === "portrait" ? c.label : sourceLabel(v, kind);
  const stats = [["Label_Life", "info.current", "Data_Widget/Data", now],
    ["Label_Attact", "info.status", "Data_Widget/Data", !coreOf(v, kind) ? T("import.reason.no_target") : detailStatus(v, kind) ? T(detailStatus(v, kind)) : "—"],
    ["Label_Defense", "info.gameFile", "Data", dateOf(v, kind) || "—"], ["Label_Healing", "info.crc", "Data_Widget/Data", T(crc[0]), crc[1]]];
  // the value column starts after the longest caption of its column
  const capW = (keys) => Math.max(...keys.map((k) => advanceOf(T(k), 42))) + 24;
  const leftCap = capW(["info.current", "info.gameFile"]), rightCap = capW(["info.status", "info.crc"]);
  const rooms = stats.map(([name_]) => {
    const right = name_ === "Label_Attact" || name_ === "Label_Healing";
    return (right ? 1240 - 773 - rightCap : 773 - 293 - leftCap) - 24;
  });
  const editingMod = shownMod();
  if (editingMod) rooms[0] -= 62;
  stats.forEach(([name_, caption, dataPath, value, color], i) => {
    text(t, `${INFO}/Status/${name_}`, T(caption), { w_: 300 });
    const right = name_ === "Label_Attact" || name_ === "Label_Healing";
    const dataNode = at(t, `${INFO}/Status/${name_}/${dataPath.split("/")[0]}`);
    dataNode.pos[0] = right ? rightCap : leftCap;
    // Fit each value independently; the compact rows use an ellipsis once this value reaches the size floor.
    const original = i === 0 && editingMod?.mod.alias ? modOriginalName(editingMod.mod) : "";
    const fit = fitText(String(value), original ? 34 : 42, 28, rooms[i], 1);
    text(t, `${INFO}/Status/${name_}/${dataPath}`, fit.text, { w_: rooms[i], fontSize: fit.fs, ellipsis: true, ariaLabel: String(value), ...(original ? { h_: 42 } : {}), ...(color ? { color: `${color}/1.00` } : {}) });
    if (original) {
      const main = at(t, `${INFO}/Status/${name_}/${dataPath}`);
      main.pos[1] += 15;
      const secondary = fitText(original, 24, 20, rooms[i], 1);
      dataNode.children.push(node("OriginalName", [main.pos[0], main.pos[1] - 38],
        [label(secondary.text, secondary.fs, "#2d4664/0.65", rooms[i], 28, { pivot: 3, depth: 22, ellipsis: true, ariaLabel: original })]));
    }
    if (i === 0 && editingMod) dataNode.children.push(node("ModAliasBtn", [Math.min(glyphsOf(String(value), fit.fs), rooms[i]) + 32, original ? 15 : 0],
      [sprite("Cafe_Icon_NameEdit", 46, 46, { type: 0, depth: 22, color: "#2d4664/1.00" })]));
    const pot = find(t, `${INFO}/Status/${name_}/Data_Widget/Potential_Widget`);
    if (pot) pot.active = false;
  });
  // the button beside the information: show the game file in Finder / Explorer (secondary style, like Cancel)
  const up = `${INFO}/Status/Btn_Up_Wrapper/Btn_Up`;
  widget(t, up).sprite = "Common_Empty";
  off(t, `${up}/Lock`, `${up}/Dot`);
  widget(t, `${up}/BG (1)`).color = "#dbeef5/1.00";
  for (const k of at(t, `${up}/BG (1)`).children) k.widgets[0].sprite = "Common_Btn_Normal_B_C_Pt";
  resizeButton(at(t, `${up}/BG (1)`));
  // the caption wraps onto up to three lines inside the 180-high body before it shrinks (explorer-button.js)
  const open_ = explorerLayout(T(platform === "mac" ? "open.mac" : "open.win"));
  text(t, `${up}/Label_Locale`, open_.text, { fontSize: open_.fs, w_: 160, h_: open_.label[2], color: "#2d5164/1.00" });
  at(t, `${up}/Label_Locale`).pos = [open_.label[0], open_.label[1]];
  at(t, up).children.push(node("Folder", open_.folder, [{ texture: "svg:folder", w_: 56, h_: 50, pivot: 4, depth: 12, color: "#ffffff/1.00" }]));

  // row 2: four actions on what the shown tab's game files carry. Title and icon centred as a group on the card's
  // slanted midline.
  // Keep these action cards and row 3's Add card at their existing prefab-based positions and sizes.
  const isMod = !!sk && c.kind === "mod";
  const actions = [["SkillData_EX", "compatibility.check", "compare", isMod && !busy], ["SkillData_EX (1)", repairAction(c), "update", isMod && !busy],
    ["SkillData_EX (2)", "export.title", "extract", !busy], ["SkillData_EX (3)", "popup.apply", "apply", !!sk && !!coreOf(v, sk) && !busy && !isApplied(v, sk, shownSource(v, sk)) && !unfitCostume(v, sk, shownSource(v, sk))]];
  // the row's white section spans x 58..1320 (Bg_W2 at 689, 1262 wide); cards keep the first card's 37-unit margins
  const ROW_L = 58 + 37, ROW_R = 1320 - 37, GAP = 6, CW = (ROW_R - ROW_L - 3 * GAP) / 4;
  actions.forEach(([card], i) => {
    const base = `${INFO}/Skill/${card}`, bg = at(t, `${base}/Bg`);
    at(t, base).pos[0] = ROW_L + CW / 2 + i * (CW + GAP) - 202 - bg.pos[0];
    resizeButton(bg, CW);
    find(t, `${base}/Bg/Bg_Line`).widgets[0].w_ = CW - 10;
  });
  off(t, `${INFO}/Skill/Btn_Up_Wrapper`); // the install button, removed for now
  // the four titles share one size: the largest at which every one fits its card after wrapping onto two lines
  const titles = NGUI.fitGroup(actions.map(([, key]) => T(key)), 40, 28, CW - 60, 2, { maxH: 90 });
  for (const [index, [card, key, glyph, enabled]] of actions.entries()) {
    const base = `${INFO}/Skill/${card}`;
    actionState(at(t, base), enabled, layouts.disabled);
    off(t, `${base}/Normal/Label_SkillType (1)`, `${base}/Normal/Line (1)`);
    const max = find(t, `${base}/Normal/Icon_Max`);
    if (max) max.active = false;
    const bgX = at(t, `${base}/Bg`).pos[0] + 1, midY = 158.3 - 126; // Bg_Line centre: the card's visible body
    const title = titles[index];
    const disabledInk = layouts.disabled && find(layouts.disabled, "Label_Locale")?.widgets.find((w) => "text" in w)?.color;
    text(t, `${base}/Normal/Label_SkillType`, title.text, { fontSize: title.fs, w_: CW - 56, h_: Math.max(46, title.fs * title.lines * 1.3),
      color: !enabled && disabledInk ? disabledInk : "#466399/1.00" });
    at(t, `${base}/Normal/Label_SkillType`).pos = [bgX + 66 * TAN10, midY + 66];
    const icon = at(t, `${base}/Normal/SkillIconBg`);
    icon.pos = [bgX - 30 * TAN10, midY - 30];
    icon.widgets = [];
    icon.children = icon.children.filter((child) => child.name === "Texture");
    const picture = widget(t, `${base}/Normal/SkillIconBg/Texture`);
    delete picture.texture;
    const [atlas, sprite] = { compare: ["Common", "School_Icon_Schedule"], update: ["Common", "Common_Icon_All_Download"],
      extract: ["Common", "Common_Icon_Inventory_Equip"], apply: ["Common", "Common_Icon_All_Download"] }[glyph];
    Object.assign(picture, { cls: "UISprite", sprite, atlas, type: 0, w_: 109, h_: 109, keepAspect: true });
    if (!enabled && disabledInk) picture.color = disabledInk;
  }
  // row 3: portrait box, the switch card (the game's light weapon card), add portrait. The box's body spans
  // -96..95 around its centre (its slanted right edge ends lower down a few units further left); the title sits at -8.
  const boxTitle = fitIn(T(isSpecial(v) && tab === "lobby" ? "special.background" : `row.box.${tab}`), 48, 42, 160);
  text(t, `${INFO}/Weapon/WeaponType/Label_WeaponType`, boxTitle.text, { fontSize: boxTitle.fs, w_: 200, h_: 60 * boxTitle.lines });
  at(t, `${INFO}/Weapon/WeaponType/Label_WeaponType`).pos = [-8, -2];
  off(t, `${INFO}/Weapon/WeaponType/Icon`);
  const card = `${INFO}/Weapon/Equip_1/UIEquipmentCard/Btn_Target`;
  if (!sk || !switchUnseen(fam, v, sk, modsOn())) off(t, `${INFO}/Weapon/Equip_1/UIEquipmentCard/Dot`);
  off(t, `${card}/Normal_NotEquip`, `${card}/Lock`, `${card}/Normal/Icon`, `${card}/Normal/Icon_Star`,
    `${card}/Normal/Card_Equipment`, `${card}/Normal/Card_Bg/Deco`);
  text(t, `${card}/Normal/Card_Level`, "");
  // window = the card's inner line (Common_Line_Raius20px 766 x 201 at (1, 6)); the decoration and the picture are clipped to it
  const win = { w: 766, h: 201, r: 20 };
  const deco = layouts.textures.Img_Deco_Weapon;
  at(t, `${card}/Normal`).children.push(
    ...(deco ? [clipped("DecoClip", [1, 6], deco, win, { dx: 30.8 - 1, dy: 7.5 - 6, w: 820, h: 198 }, { depth: 9 })] : []),
    tab === "portrait"
      ? clipped("Bust", [1, 6], thumb(currentBase(), "bust"), win, { dx: -383 + 202 - 40, dy: -67.5, w: 404, h: 404 },
        { depth: 10, mask: "linear-gradient(90deg, #000 70%, transparent 98%)" })
      // wider than the window's slanted left edge; the lobby fades out before the labels so their contrast is known
      : clipped("Bust", [1, 6], tab === "model" ? modelPicture(current("model")) : lobbyShot, win, { dx: -383 + 216, dy: 0, w: tab === "model" ? 253 : 450, h: 253 },
        { depth: 10, placeholder: T("import.previewUnavailable"), edge: 0.7, mask: `linear-gradient(90deg, transparent 0, #000 10%, #000 70%, transparent ${tab === "model" ? 96 : 82}%)` }));
  // the wrapper holds an enabled (cyan) and a disabled (grey) body and label: the enabled ones while the tab has
  // something to add and switch to (switchKind), else the disabled ones
  const keep = switchKind() ? 1 : 2;
  let bodies = 0, labels = 0;
  const add = fitIn(T(isSpecial(v) && tab === "lobby" ? "special.add" : `row.add.${tab}`), 52, 30, 160, 3); // the cyan body is 205 wide, slanted
  for (const k of at(t, `${INFO}/Weapon/Btn_Up_Wrapper/Btn_Up`).children) {
    if (k.name === "BG (1)") { resizeButton(k); bodies += 1; if (bodies !== keep) k.active = false; }
    if (k.name === "Label_Locale") { labels += 1; if (labels !== keep) k.active = false; else Object.assign(k.widgets[0], { text: add.text, fontSize: add.fs, w_: 200, h_: 160 }); }
    if (k.name === "Bg" || k.name === "Lock" || k.name === "Dot") k.active = false;
  }

  // row 4: a box like row 3's in the first column of the skill grid; the cards are rendered separately
  off(t, `${INFO}/Equip/Grid`, `${INFO}/Equip/Btn_Up_Wrapper`);
  const box = clone(find(t, `${INFO}/Weapon/WeaponType`));
  box.name = "ExprType";
  box.pos = [374.7, ROW4_Y];
  find(box, "BG/BG (1)").widgets[0].w_ = BOX_RIGHT - 126;   // ends where the first skill card of row 2 ends
  find(box, "Line/Line").widgets[0].w_ = BOX_RIGHT + 3 - 123.7;
  const boxLabel = find(box, "Label_WeaponType");
  const row4 = fitIn(T(`row.four.${tab}`), 48, 42, 200); // the box's body spans -96..148, the label sits at 20
  Object.assign(boxLabel.widgets[0], { text: row4.text, fontSize: row4.fs, w_: 240, h_: 60 * row4.lines });
  boxLabel.pos = [(87.5 + BOX_RIGHT) / 2 - 183.7 - 6, -2];
  at(t, `${INFO}/Equip`).children.push(box);

  // nameplate: favourite pin, name, the white panel for the variant chips; no stars, level, heart, squad or terrain
  const np = "LeftInfo/CharInfo/Char_Target";
  off(t, `${np}/Icon_Star`, `${np}/Text_Lv`, `${np}/FavorBG`, `${np}/SquadType`, `${np}/Type`, `${np}/Terrain`, `${np}/Char_ProfileBtn`);
  const fav = isFav(v);
  at(t, `${np}/Pin/Pin_On`).active = fav;
  at(t, `${np}/Pin/Pin_Off`).active = !fav;
  for (const state of ["Pin_On", "Pin_Off"]) {
    const base = `${np}/Pin/${state}`;
    at(t, base).pos[1] = 392;
    const lblNode = find(t, `${base}/Bg/Icon/Label_Locale`) || find(t, `${base}/Bg/Label_Locale`);
    const label = lblNode.widgets.find((w) => "text" in w), tw = advanceOf(T("fav.label"), label.fontSize);
    // Keep each state's sprite, baked shading, icon tint and local positions.
    // Only the translated caption and its right anchor expand the prefab.
    const oldWidth = label.w_;
    Object.assign(label, { text: T("fav.label"), w_: Math.max(oldWidth, tw + 4) });
    widget(t, `${base}/Bg`).w_ += label.w_ - oldWidth;
  }
  // Both names share the existing dark band, leaving room for the pencil after the main name.
  const original = originalName(v), shown = fullName(v), aliased = customAliases().length > 0;
  const { room } = aliasPlate(at(t, np), shown, aliased);
  if (aliased) {
    const secondary = fitText(original, 28, 24, room, 1);
    at(t, np).children.push(node("Label_OriginalName", [110, 258],
      [label(secondary.text, secondary.fs, "#ffffff/0.62", room, 36, { pivot: 3, depth: 22, ellipsis: true, ariaLabel: original })]));
  }
  // the dark plate keeps its bottom edge and loses the level row; the game's shard texture is clipped to it
  widget(t, `${np}/BG`).h_ = 120;
  off(t, `${np}/BG/Deco`);
  const poli = layouts.textures.Img_Deco_Poli;
  if (poli) {
    at(t, `${np}/BG`).children.push(node("DecoClip", [0, 0], [{ clipImage: poli, tint: "#0e62b2",
      w_: 906, h_: 120, pivot: 6, depth: 5, incline: 10, radius: 10, color: "#ffffff/0.90",
      imgLeft: `${-13.4 * S}px`, imgTop: "0", imgWidth: `${388 * S}px`, imgHeight: `${172 * S}px`, imgSize: "100% 100%", imgPosition: "0 0" }]));
  }
  // square buttons: fullscreen (the portrait's visual mode with the game's own icon, the lobby, the 3D model), copy the
  // file name, and on the lobby tab play all dialogue
  at(t, `${np}/Char_CVCollectionBtn`).pos[0] = 1208.8;
  if (tab === "lobby" || tab === "model") Object.assign(widget(t, `${np}/Char_ZoomBtn/Sprite`), { sprite: "Common_UIHide_Icon", w_: 74, h_: 66, color: "#2b496e/1.00" });
  Object.assign(widget(t, `${np}/Char_CVCollectionBtn/Sprite`), { sprite: "Common_Icon_Copy", w_: 64, h_: 76, color: "#2b496e/1.00" });
  if (tab === "lobby") {
    const play = clone(find(t, `${np}/Char_ZoomBtn`));
    play.name = "Char_PlayBtn";
    play.pos[0] = 1362;
    const w = play.children[0].widgets[0];
    if (Lobby.playingAll()) { Object.assign(w, { texture: "svg:stop", w_: 54, h_: 54, color: "#2b496e/1.00" }); delete w.sprite; }
    else Object.assign(w, { sprite: "Common_Icon_Play", w_: 64, h_: 64, color: "#2b496e/1.00" });
    at(t, np).children.push(play);
  }
  return t;
}

// The game's input-popup pencil opens the alias editor; its accessible name includes every saved alias.
const PENCIL = CONTROL.icon.h; // App-created sprite, not a cloned prefab icon
function nameplateRow(np, caption, aliased, ui = NGUI) {
  const y = aliased ? 310 : 288, bg = find(np, "BG"), body = bg.widgets.find(w => "sprite" in w);
  // The prefab band's right edge at the name's height, with the App's margin on the spacing scale.
  // Its retained body is 120 high; include its pivot and shear rather than a hand-set right limit.
  const [px, py] = toNode({ x: 0, y: 0, w: body.w_, h: 120 }, body.pivot, LOCAL_FRAME);
  const right = bg.pos[0] - px + body.w_ + (y - (bg.pos[1] - py - 60)) * Math.tan((body.incline || 0) * Math.PI / 180);
  const content = container({ x: 110, w: right - 110, h: 120, padding: [0, SPACE.xl, 0, 0] }).inner;
  const room = content.w - PENCIL - SPACE.xs;
  const type = { fs: aliased ? 54 : 64, min: 44 }, height = aliased ? 64 : 76;
  let name;
  do {
    name = textItem(ui, caption, type, { maxW: room });
    if (name.h <= height || type.fs === type.min) break;
    type.fs = Math.max(type.min, type.fs - 2);
  } while (true);
  const line = row([name, { w: PENCIL, h: PENCIL }], { x: content.x, gap: SPACE.xs });
  return { ...line, room, frame: { ...LOCAL_FRAME, cy: y + line.centre } };
}
function aliasPlate(np, caption, aliased) {
  const layout = nameplateRow(np, caption, aliased), [name, pencil] = layout.boxes;
  const main = find(np, "Label_Name");
  Object.assign(main.widgets.find(w => "text" in w), { text: name.text, w_: name.w, h_: name.h, fontSize: name.fs, pivot: 3, ellipsis: true, ariaLabel: caption });
  main.pos = toNode(name, 3, layout.frame);
  np.children.push(node("Char_AliasBtn", toNode(pencil, 4, layout.frame),
    [sprite("Cafe_Icon_NameEdit", PENCIL, PENCIL, { type: 0, depth: 22, color: "#ffffff/0.85" })]));
  return layout;
}

function renderScreen() { return drawScreen().catch(renderFailed); }
async function drawScreen() {
  const my = ++renderToken, gen = showGen, shown = tab;
  const ok = () => showable("screen", my, renderToken, gen);
  const host = $("ngui"), bgHost = $("ngui-bg");
  const v = variant();
  const t = screenTree();
  const bgNode = find(t, "BG");
  const RIGHT = ["RightInfo", "Arrow_R"];
  const left = { ...t, children: t.children.filter((k) => k !== bgNode && !RIGHT.includes(k.name)) };
  const right = { ...t, widgets: [], children: t.children.filter((k) => RIGHT.includes(k.name)) };
  // render off-screen, then swap in one go (no flicker while images load)
  const next = document.createElement("div");
  next.innerHTML = '<div class="side left"></div><div class="side right"></div>';
  const [leftHost, rightHost] = next.children;
  const [lv, rv] = await Promise.all([
    NGUI.render(left, leftHost, { scale: S, origin: ORIGIN, textureNames: TEXTURES, deferImages: true }),
    NGUI.render(right, rightHost, { scale: S, origin: ORIGIN, textureNames: TEXTURES, deferImages: true }),
  ]);
  const frag = document.createDocumentFragment();
  const bg = await NGUI.render(bgNode, frag, { scale: S, origin: ORIGIN, rootPath: "Inventory_ListControl", textures: { BG: v.bg || undefined }, deferImages: true });
  if (!ok()) return;
  const view = { nodes: new Map([...lv.nodes, ...rv.nodes]) };
  const panel = rv.nodes.get("Inventory_ListControl/RightInfo/Target/Active/Bg")?.els[0];
  if (panel) {
    const [left, , , height] = NGUI.rect(panel);
    const incline = rv.nodes.get("Inventory_ListControl/RightInfo/Target/Active/Bg").node.widgets.find((w) => "sprite" in w)?.incline || 0;
    Lobby.setDialogueBounds({ left: 0, top: 0, right: left - Math.abs(Math.tan(incline * Math.PI / 180)) * height / 2, bottom: 1080 });
  }
  const cardReady = addSwitchText(rightHost, view);
  if (my === renderToken) {
    screenPics = [picturesReady([...lv.images, ...rv.images, ...bg.images], leftHost, rightHost, frag)];
    fillPictures([...lv.thumbnails, ...rv.thumbnails, ...bg.thumbnails, cardReady], leftHost, rightHost, frag);
  }
  Photo.attach(photoBridge);
  hits(view, leftHost, rightHost);
  commit(() => {
    if (!ok()) return;
    applied.screen = my;
    // the control with the keyboard's focus keeps it in the new screen (an arrow stepped with Enter, a step key
    // pressed on a button), where the new screen has it
    const had = host.contains(document.activeElement) ? document.activeElement.dataset?.part : null;
    host.replaceChildren(...next.children);
    if (had) [...host.querySelectorAll(".hit")].find((b) => b.dataset.part === had)?.focus({ preventScroll: true });
    bgHost.replaceChildren(frag);
    bgHost.classList.toggle("dim", shown !== "portrait");
    bgHost.hidden = shown === "lobby"; // the lobby extends its own edges instead
  });
}

// the switch card has no label slot in the game; put the title and the portrait's name beside the picture
function switchTextLayout(w, h, title, sub, arrow) {
  // The prefab bust keeps the left half. The App composes the text and chevron inside the other half.
  const content = container({ x: w / 2, w: w / 2, h, padding: SPACE.s }).inner;
  const columns = row([{ grow: 1 }, ...(arrow ? [{ w: CONTROL.icon.h, h: CONTROL.icon.h }] : [])], { ...content, gap: SPACE.xs });
  const room = columns.boxes[0];
  let titleSize = TYPE.body.fs, subSize = TYPE.caption.fs, texts;
  do {
    texts = stack([
      textItem(NGUI, title, { ...TYPE.body, fs: titleSize }, { maxW: room.w, lines: 2 }),
      textItem(NGUI, sub, { ...TYPE.caption, fs: subSize }, { maxW: room.w, lines: 2 }),
    ], { x: room.x, gap: SPACE.xs });
    if (texts.h <= room.h || (titleSize === TYPE.body.min && subSize === TYPE.caption.min)) break;
    titleSize = Math.max(TYPE.body.min, titleSize - 2);
    subSize = Math.max(TYPE.caption.min, subSize - 2);
  } while (true);
  return { texts: texts.boxes.map(b => ({ ...b, y: b.y + room.y + (room.h - texts.h) / 2 })), arrow: columns.boxes[1], content };
}
function addSwitchText(host, view) {
  const cardEl = view.nodes.get(`Inventory_ListControl/${INFO}/Weapon/Equip_1/UIEquipmentCard/Btn_Target/Normal/Card_Bg`)?.els[0];
  if (!cardEl) return;
  const [l, t, w, h] = rect(cardEl);
  const box = document.createElement("div");
  box.className = "switch-text";
  Object.assign(box.style, { left: `${l}px`, top: `${t}px`, width: `${w}px`, height: `${h}px`, display: "block", padding: "0" });
  const selectedStatus = selectionStatus(variant(), tabKind());
  const sub = [tab === "portrait" ? candidateLabel(current()) : sourceLabel(variant(), tabKind()), selectedStatus && T(selectedStatus)].filter(Boolean).join(" - ");
  const title = T(isSpecial(variant()) && tab === "lobby" ? "special.switch" : `row.switch.${tab}`);
  const layout = switchTextLayout(w / S, h / S, title, sub, !!switchKind());
  const b = document.createElement("b"), span = document.createElement("span"), go = document.createElement("i");
  [b, span].forEach((el, i) => {
    const item = layout.texts[i];
    el.innerText = item.text;
    Object.assign(el.style, { position: "absolute", left: `${item.x * S}px`, top: `${item.y * S}px`, width: `${item.w * S}px`,
      height: `${item.h * S}px`, fontSize: `${item.px * S}px`, lineHeight: String(NGUI.LINE_PITCH), whiteSpace: "pre" });
  });
  go.className = "switch-go";
  if (layout.arrow) Object.assign(go.style, { left: `${layout.arrow.x * S}px`, top: `${layout.arrow.y * S}px`,
    width: `${layout.arrow.w * S}px`, height: `${layout.arrow.h * S}px`, right: "auto", marginTop: "0" });
  // The chevron opens the shared source popup.
  box.append(b, span, ...(switchKind() ? [go] : []));
  host.appendChild(box);
  const bust = view.nodes.get(`Inventory_ListControl/${INFO}/Weapon/Equip_1/UIEquipmentCard/Btn_Target/Normal/Bust`);
  const { signal } = thumbnailWork;
  const background = variant().bg || layouts.textures[widget(layouts.list, "BG").texture];
  if (switchKind() && bust?.els[0]) return colourSwitchCard(bust.els[0], box, bust.node.widgets[0], NGUI.load, { signal, kind: tab, background })
    .catch((err) => { if (!signal.aborted) console.warn("Switch card colours unavailable", err); });
}

function hits(view, leftHost, rightHost) {
  // each button names its part, so the focus finds it again in the next render
  const add = (host, path, fn, name) => {
    const el = view.nodes.get(`Inventory_ListControl/${path}`)?.els[0];
    if (!el) return null;
    const button = hitButton(host, rect(el), fn, name);
    button.dataset.part = path;
    return button;
  };
  // The switch card selects a source for the shown asset kind.
  const sk = switchKind();
  if (sk) {
    add(rightHost, `${INFO}/Weapon/Equip_1/UIEquipmentCard/Btn_Target/Normal/Card_Bg`, () => openPopup(sk), T(isSpecial(variant()) && sk === "lobby" ? "special.switch" : `row.switch.${tab}`));
    add(rightHost, `${INFO}/Weapon/Btn_Up_Wrapper/Btn_Up/BG (1)`, addMods, T(isSpecial(variant()) && sk === "lobby" ? "special.add" : `row.add.${tab}`));
  }
  add(rightHost, `${INFO}/Status/Btn_Up_Wrapper/Btn_Up/BG (1)`, () => reveal(gameFile(variant(), tabKind())), T(platform === "mac" ? "open.mac" : "open.win"));
  add(rightHost, `${INFO}/Detail_Btn`, showDetails, T("info.details"));
  if (shownMod()) add(rightHost, `${INFO}/Status/Label_Life/Data_Widget/ModAliasBtn`, () => editModName(shownMod().mod), T("modAlias.title"));
  const act = (card, fn, name) => add(rightHost, `${INFO}/Skill/${card}/Bg`, fn, name);
  act("SkillData_EX", () => runCompatibility(), T("compatibility.check"));
  act("SkillData_EX (1)", () => repairedId(shownMod()) ? jumpRepaired(shownMod()) : runUpdate(),
    T(repairAction(shownMod())));
  act("SkillData_EX (2)", () => runExport(), T("export.title"));
  const apply = act("SkillData_EX (3)", () => applyCandidate(shownSource(variant(), sk), sk), T("popup.apply"));
  if (apply) {
    const unfit = unfitCostume(variant(), sk, shownSource(variant(), sk));
    apply.disabled = !unfit && (!sk || !coreOf(variant(), sk) || busy || isApplied(variant(), sk, shownSource(variant(), sk)));
    if (!coreOf(variant(), sk)) apply.setAttribute("aria-description", T("import.reason.no_target"));
    // a costume shown for a preview only: greyed, the reason on hover (the click does nothing, applyCandidate)
    if (unfit) { apply.setAttribute("aria-disabled", "true"); apply.setAttribute("aria-description", T("model.unfit")); attachHint(apply, T("model.unfit")); }
  }
  add(leftHost, "LeftInfo/CharInfo/Char_Target/Pin/Pin_On/Bg", toggleFav, T("fav.label"));
  add(leftHost, "LeftInfo/CharInfo/Char_Target/Pin/Pin_Off/Bg", toggleFav, T("fav.label"));
  // Copy, then the file it copies
  add(leftHost, "LeftInfo/CharInfo/Char_Target/Char_CVCollectionBtn", copyName, `${T("window.copy")}${listSep()}${baseName(gameFile(variant(), tabKind()))}`);
  // The pencil opens the editor beside the displayed name.
  const aliasEls = ["Char_AliasBtn"].map((n) => view.nodes.get(`Inventory_ListControl/LeftInfo/CharInfo/Char_Target/${n}`)?.els[0]).filter(Boolean);
  if (aliasEls.length) {
    const boxes = aliasEls.map(rect), pad = 14;
    const l = Math.min(...boxes.map((b) => b[0])) - pad, tp = Math.min(...boxes.map((b) => b[1])) - pad;
    const r = Math.max(...boxes.map((b) => b[0] + b[2])) + pad, bt = Math.max(...boxes.map((b) => b[1] + b[3])) + pad;
    const list = aliasList();
    hitButton(leftHost, [l, tp, r - l, bt - tp], () => call(() => openAliases()), list ? `${T("alias.title")} - ${list}` : T("alias.title"));
  }
  // the previous or next card of the student list, within its filters and in its order
  add(leftHost, "Arrow_L/Left", () => ctx.step(-1, fam.key, variantKey), T("hall.previous"));
  add(rightHost, "Arrow_R/Right", () => ctx.step(1, fam.key, variantKey), T("hall.next"));
  add(leftHost, "LeftInfo/CharInfo/Char_Target/Char_ZoomBtn", () => setFullscreen(true), T("lobby.fullscreen"));
  if (tab === "lobby") add(leftHost, "LeftInfo/CharInfo/Char_Target/Char_PlayBtn", togglePlayAll, T(Lobby.playingAll() ? "lobby.stop" : "lobby.playAll"));
  const v = variant();
  [["Tab_1", "portrait", hasKind(v, "spr")], ["Tab_2", "lobby", hasLobby(v)], ["Tab_3", "model", hasModel(v)]].forEach(([n, id, ok]) => {
    if (ok && id !== tab) add(rightHost, `RightInfo/Target/Active/CharacterTab/${n}/Tab_Off/Label`, () => showTab(id), T(isSpecial(v) && id === "lobby" ? "special.background" : `tab.${id}`));
  });
}

// --- actions ---
async function call(fn) {
  try { return await fn(); } catch (err) {
    console.error("Detail action failed", err);
    notify(err.code ? errorText(err) : T("detail.failed"), { kind: "error", error: err });
    return null;
  }
}
async function reveal(path) { if (path) await call(() => rpc("os.reveal", { path })); }
async function copyName() {
  const name = baseName(gameFile(variant(), tabKind()));
  try { await navigator.clipboard.writeText(name); }
  catch (err) { notify(T("detail.copyFailed"), { kind: "error", error: err }); }
}
function editModName(mod, redraw = null) {
  const page = pageToken;
  return call(() => ModAlias.open({ mod, onSaved: async () => {
    ctx.invalidateRoster();
    if (!open || page !== pageToken) return;
    await reload();
    if (open && page === pageToken) await redraw?.();
  } }));
}
async function toggleFav() {
  const v = variant();
  await call(() => rpc("favorites.set", { variant: v.key, on: !isFav(v) }));
  v.favorite = !isFav(v);
  ctx.invalidateRoster();
  renderAll();
}
// Add Portrait, Add Memorial Lobby and Add 3D Model open Import Mod: every mod comes in through one import (BL-095).
function addMods() {
  location.hash = "#/import";
}

// a job with a sticky progress line; the family is re-read afterwards (after(ok, result) instead, when given).
// A switch the classic UI's open window refuses opens the classic modal, which can queue it (mod-switch.js).
// An apply while mods are off only saves the choice (deferred): a notice says so ("Saved, applied when mods are on").
async function job(method, params, { onDone, after = () => reload(), name = fullName(variant()) } = {}) {
  if (busy) { notify(T("error.busy"), { kind: "error", error: { code: "busy" } }); return false; }
  busy = true;
  const page = pageToken;
  let ok = false, result = null, refusal = null;
  const action = ({ "actions.apply": params.source === "official" ? "restore" : "apply", "actions.restore": "restore", "actions.update": "repair",
    "actions.extract": "extract", "mods.remove": "delete" })[method] || "process";
  const mod = fam?.variants?.flatMap((v) => v.modList || []).find((m) => m.id === (params.mod || params.id || params.target?.replace(/^mod:/, "")));
  const targetKey = params.variant || (params.target?.startsWith("variant:") ? params.target.slice(8) : mod?.target);
  const target = fam?.variants?.find((v) => v.key === targetKey) || variant();
  const kind = params.kind || mod?.kind || switchKind() || "spr";
  const subject = progressName(target, T(({ spr: "tab.portrait", lobby: "tab.lobby", model: "tab.model", other: "import.other" })[kind] || "import.other"), name, fam);
  const progress = (p = {}) => jobProgress({ action: `progress.action${action[0].toUpperCase()}${action.slice(1)}`, name: subject,
    current: p.current || 1, total: p.total || 1, step: p.step || "" });
  const running = progress();
  try {
    return await Loading.hold(async (update) => {
      update(running);
      try {
        try {
          await renderScreen();
          const id = await rpc(method, params, { timeout: 30000 });
          result = await waitJob(id, (p) => { const text = progress(p); update(text); });
          await onDone?.(result);
          ok = true;
        } catch (err) { refusal = err; }
        if (page === pageToken && open) await after(ok, result);
        return ok;
      } finally {
        busy = false;
        if (page === pageToken && open) {
          try { await renderScreen(); } catch (err) { notify(errorText(err), { kind: "error", error: err }); }
        }
      }
    }, { all: true });
  } catch (err) {
    notify(errorText(err), { kind: "error", error: err });
    return false;
  } finally {
    busy = false;
    // Refusals can require an answer. Release the strict job lock before opening their controls.
    if (refusal) {
      if (page === pageToken && open && refusal.code === "original_unavailable" && QUEUEABLE.includes(method))
        await overwrittenMessage(variant(), params.kind || "spr");
      else if (QUEUEABLE.includes(method)) await refused(refusal, method, params);
      else notify(errorText(refusal), { kind: "error", error: refusal });
    } else if (ok) {
      // an apply while mods are off only saves the choice, without a notice, like any other saved choice (owner)
      const lines = [result?.kept_current && T("mods.keptCurrent"), result?.incomplete && T("mods.incomplete")].filter(Boolean);
      if (lines.length) void notice(lines.join("\n"));
    }
    resumeChanged();
  }
}
// The mod previewed on the shown tab (row 2's update and compare act on it), or null.
function shownMod() {
  const sk = switchKind(), c = sk && current(sk);
  return c?.kind === "mod" ? c : null;
}
function modProgressName(c) {
  const mod = c.mod || c, target = fam.variants.find((v) => v.key === mod.target) || variant();
  const kind = mod.kind || switchKind();
  return progressName(target, T(({ spr: "tab.portrait", lobby: "tab.lobby", model: "tab.model" })[kind] || "import.other"), c.label, fam);
}
async function runUpdate(c = shownMod()) {
  const kind = switchKind();
  if (!c || busy) return;
  const page = pageToken;
  const report = await call(() => Loading.hold(() => Compatibility.forUpdate(c.id, c.label, modProgressName(c)), { all: true }));
  if (!report || page !== pageToken || !open) return false;
  if (Compatibility.eligible(c.mod) && Compatibility.canRemap(c.id, c.mod)) return runRemap(c);
  if (!report.assessment.available || !report.assessment.issues.length) {
    const answer = await message({ title: T("act.update"), lines: [T(report.assessment.available ? "compatibility.forceUpdate" : "compatibility.updateUnchecked")],
      cancel: T("popup.cancel"), ok: T("act.update") });
    if (answer.button !== "ok" || page !== pageToken || !open) return false;
  }
  return job("actions.update", { mod: c.id }, {
    name: c.label,
    onDone: () => { forget(spinePrefix("mod", c.id)); },
    after: (ok) => reload(ok ? [kind] : []),
  });
}
async function runCompatibility() {
  const c = shownMod();
  if (!c || busy) return;
  await call(() => Compatibility.check(c.id, c.label, remapped, c.mod, modProgressName(c)));
  if (open) await renderScreen();
}
async function remapped() {
  ctx.invalidateRoster();
  await reload();
}
async function runRemap(c) {
  if (!c || busy || !Compatibility.eligible(c.mod)) return;
  await call(() => Compatibility.remap(c.id, remapped, c.label, modProgressName(c)));
}
async function jumpRepaired(c) {
  const id = repairedId(c);
  if (!id) return;
  if (!popupKind || $("popup").hidden) await openPopup(switchKind());
  pending = `m:${id}`;
  await renderPopup();
}
function runExport() {
  const c = shownMod();
  if (busy) return;
  const target = c ? `mod:${c.id}` : `variant:${variant().key}`;
  ModExport.open({ scope: "single", target, kind: switchKind() || "spr" });
}
async function runCompare() {
  const c = shownMod();
  if (!c || busy) return;
  const result = await call(() => rpc("actions.compare", { mod: c.id }));
  if (result) showCompare(result);
}
// switch: the chosen source is written into the variant's game files of that kind, so the game shows it; the library
// keeps what it replaces (an official file until it is official again, a mod in the library)
async function applyCandidate(key, kind = "spr") {
  const v = variant();
  if (!kind || !coreOf(v, kind) || busy || isApplied(v, kind, key) || unfitCostume(v, kind, key)) return;
  await job("actions.apply", { variant: v.key, source: sourceOf(key), kind }, {
    onDone: (result) => {
      if (result?.files?.length) {
        const core = coreOf(v, kind);
        forget(spinePrefix(kind, core));
        forget(spinePrefix(`official-${kind}`, core));
      }
    },
    // the applied portrait or lobby swaps in with the rest of the screen; the old one shows until then
    after: async (ok, result) => {
      ctx.invalidateRoster();
      if (!ok) return reload();
      if (result?.unchanged) return reload();
      resetPreview(kind);
      if (!open) return null; // the screen was left meanwhile: the next show reads the family again
      return changeInPlace(async () => {
        const read = fam;
        const data = await call(() => readFamily(read.key));
        if (data && fam !== read) return; // the screen was read anew meanwhile
        if (data) fam = data;
        await Promise.all([renderAll(), renderLeft()]);
      });
    },
  });
  if (switchPopupPage === pageToken && !$("popup").hidden) await renderPopup();
}

async function saveCandidate(key, kind) {
  const v = variant(), family = fam;
  await call(() => Loading.hold(async () => {
    const sources = await rpc("ui.preview", { variant: v.key, kind, source: sourceOf(key) });
    ctx.invalidateRoster();
    if (!open || fam !== family || variant() !== v) return;
    v.previewSources = sources;
    resetPreview(kind);
    await changeInPlace(() => Promise.all([renderAll(), renderLeft()]));
  }, { all: true }));
}

// --- variant chips in the nameplate's white panel (style of the game's type chips) ---
// 3 columns x 2 rows; a long name takes two columns; with more variants than fit, the last cell opens the picker.
const CHIP_FS = CHIP.fs, CHIP_W = CHIP.w, CHIP_PITCH = CHIP.pitch; // the prefab chip (Char_Target/Type/MainType/BG): 272 x 72, 40 down to 28
const chipSpan = (item) => {
  const need = glyphsOf(shortName(item), 32) + (item.recruitable ? 32 * 1.35 / RECRUIT.ratio + 6 : 0) + (appliedKey(item) !== "self" ? 34 : 0);
  return need > CHIP_W - CHIP.inset ? 2 : 1;
};
const byRank = (a, b) => (isFav(b) - isFav(a)) || (b.recruitable - a.recruitable) || a.order - b.order;
function placeChips(items) {
  const cells = [];
  let row = 0, col = 0;
  for (const item of items) {
    const span = chipSpan(item);
    if (col + span > 3) { row += 1; col = 0; }
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
  const caps = [3, 2], fill = [0, 0], picked = [];
  const tryPlace = (v) => {
    const span = chipSpan(v);
    for (let r = 0; r < 2; r++) if (fill[r] + span <= caps[r]) { picked.push({ v, row: r, col: fill[r], span }); fill[r] += span; return true; }
    return false;
  };
  for (const v of list) { tryPlace(v); if (fill[0] === caps[0] && fill[1] === caps[1]) break; }
  // the current variant is always shown
  if (!picked.some((p) => p.v.key === variantKey)) {
    const last = [...picked].reverse().find((p) => p.span === chipSpan(variant()) || (p.span === 2 && chipSpan(variant()) === 1));
    if (last) Object.assign(last, { v: variant(), span: chipSpan(variant()) });
  }
  return [...picked, { more: list.length - picked.length, row: 1, col: 2, span: 1 }];
}
function chipNode(name, pos, w, caption, active, swapped, mark, fresh = false) {
  const body = sprite("Common_Bg_Raius10px", w, CHIP.h, { incline: 10, depth: 5, color: active ? "#2c4564/1.00" : "#ffffff/1.00" });
  const color = active ? "#ffffff/1.00" : "#2c4564/1.00";
  const lbl = markedLabel(caption, { fs: CHIP_FS, fsMin: CHIP.fsMin, color, maxW: w - CHIP.inset - (swapped ? 30 : 0), mark, depth: 8 });
  const kids = [node("Label", [swapped ? -15 : 0, 0], [], lbl.nodes)];
  if (!active) kids.push(node("Line", [0, 0], [sprite("Common_Line_Raius10px", w, CHIP.h, { incline: 10, depth: 6, color: "#c0c9d4/1.00" })]));
  // the variant wears another portrait: the game's swap icon at the right end
  if (swapped) kids.push(node("Swapped", [w / 2 - 36, 0], [sprite("Common_Icon_Change", 32, 32, { type: 0, depth: 9, color: active ? "#e6b6ef/1.00" : "#9c44ac/1.00" })]));
  if (fresh) kids.push(DOT([w / 2 - 4 + 36 * TAN10, 34], 30));
  return node(name, pos, [body], kids);
}
function chipBoxes(chips) {
  // UICharacterDetail's 272 x 72 chip, 290 pitch and 84 row pitch are prefab geometry, not App spacing.
  const width = CHIP_W + 2 * CHIP_PITCH;
  const rows = stack([{ h: CHIP.h }, { h: CHIP.h }], { y: -(234 - 58) - CHIP.h / 2, w: width, gap: CHIP.rowPitch - CHIP.h });
  const slots = rows.boxes.map(b => row(Array.from({ length: 3 }, () => ({ w: CHIP_W, h: CHIP.h })), {
    x: 514 + (-b.y - CHIP.h / 2 - (234 - 101)) * TAN10 - width / 2, y: b.y, gap: CHIP.gap,
  }).boxes);
  return chips.map(chip => ({ ...chip, box: { ...slots[chip.row][chip.col], w: CHIP_W + (chip.span - 1) * CHIP_PITCH } }));
}
function renderChips() { return drawChips().catch(renderFailed); }
async function drawChips() {
  const my = ++chipToken, gen = showGen;
  const ok = () => showable("chips", my, chipToken, gen);
  const host = $("ngui-chips");
  // Char_Target sits at (-1480, -720); BG2 (886 x 202, top-centre at 514, 234, sheared about its centre) holds the chips
  const chips = chipLayout();
  const tree = node("Chips", [-1480, -720], [], chipBoxes(chips).map(({ v, more, box }) => {
    const pos = toNode(box, 4, LOCAL_FRAME), w = box.w;
    return v
      ? chipNode(`Chip_${v.key}`, pos, w, shortName(v), v.key === variantKey, shownSource(v) !== "self", v.recruitable, hasFresh(v))
      : chipNode("Chip_more", pos, w, T("chips.more", { n: more }), false, false, false);
  }));
  const next = document.createDocumentFragment();
  const view = await NGUI.render(tree, next, { scale: S, origin: ORIGIN, z: 20, deferImages: true });
  if (my === chipToken) {
    chipPics = [picturesReady(view.images, next)];
    fillPictures(view.thumbnails, next);
  }
  if (!ok()) return; // a newer render took over
  for (const [path, entry] of view.nodes) {
    const m = /\/Chip_([^/]*)$/.exec(path);
    if (!m || !entry.els[0]) continue;
    // the chip's caption names it; the shown variant's chip is the pressed one
    const chip = m[1] === "more" ? chips.find((c) => !c.v) : chips.find((c) => c.v?.key === m[1]);
    const button = hitButton(next, rect(entry.els[0]), () => (m[1] === "more" ? openPicker() : ctx.go(fam.key, m[1])),
      chip?.v ? shortName(chip.v) : T("chips.more", { n: chip?.more ?? 0 }));
    if (chip?.v) button.setAttribute("aria-pressed", String(chip.v.key === variantKey));
  }
  commit(() => {
    if (!ok()) return;
    applied.chips = my;
    host.replaceChildren(next);
  });
}

// --- row 4: a box, then cards on the columns of row 2's skill cards, the pile in the button column ---
function expressionBoxes(count, nativeColumns = false) {
  // Slots retain the prefab's pitch (including its rounded column coordinates); the bodies remain 242 x 209.
  return row(Array.from({ length: count }, (_, i) => ({ w: nativeColumns && i + 1 < COLS.length ? COLS[i + 1] - COLS[i] : PITCH, h: CARD.h })), {
    x: COLS[0] - CARD.w / 2, y: -ROW4_Y + 6.5 - CARD.h / 2,
  }).boxes.map(b => ({ ...b, w: CARD.w }));
}
function pileBoxes() {
  const w = BTN_COL[1] - BTN_COL[0] - SPACE.l;
  // Overlapping row slots fan the pile inside its retained column; each step is SPACE.s across and in height.
  return row(Array.from({ length: 3 }, (_, i) => ({ w: SPACE.s, h: CARD.h - i * SPACE.s })), {
    x: BTN_COL[1] - w - 2 * SPACE.s, y: -ROW4_Y + 6.5 - CARD.h / 2,
  }).boxes.map(b => ({ ...b, w }));
}
function stripCard(name, x, { w = CARD.w, h = CARD.h, thumb: pic = null, selected = false, dim = false, caption = "", body = "", placeholder = false, tone = "#ffffff" } = {}) {
  const vis = h - 13, cy = 6.5; // the visible body's centre sits 6.5 units above the widget's centre
  const kids = [node("Line", [1, cy], [sprite("Common_Line_Raius20px", w - 10, vis, { incline: 10, depth: 9, color: "#bebebe/1.00" })])];
  if (pic) kids.push(node("Thumb", [1, cy], [{ clipImage: pic, w_: w - 18, h_: vis - 8, pivot: 4, depth: 8, incline: 10, radius: 16, color: "#ffffff/1.00", imgSize: "cover", imgPosition: "center 50%" }]));
  if (dim) kids.push(node("Dim", [1, cy], [sprite("Common_Bg_Raius20px", w - 12, vis - 4, { incline: 10, depth: 10, color: "#000000/0.50" })]));
  if (caption) {
    const f = fitText(caption, 56, 28, w - 50);
    kids.push(node("Caption", [0, cy], [label(f.text, f.fs, dim ? "#ffffff/1.00" : "#2c4564/1.00", w - 30, f.fs * 1.3 * f.lines, { depth: 12 })]));
  }
  if (body) {
    // a dialogue line: centred on the card, as large as fits in up to four lines; a longer line ends in an ellipsis
    // (the subtitle shows it in full). A placeholder (no line in the UI language) is grey and slanted.
    let f = fitText(body, 40, 32, w - 52, 4), cut = body;
    while (f.lines === 1 && glyphsOf(f.text, f.fs) > w - 52 && cut.length > 4) { cut = cut.slice(0, -2).trimEnd(); f = fitText(`${cut}…`, 40, 32, w - 52, 4); }
    const ink = placeholder ? "#8f9aa6/1.00" : "#2c4564/1.00";
    kids.push(node("Body", [0, cy], [label(f.text, f.fs, ink, w - 40, f.fs * 1.3 * f.lines, { depth: 12, italic: placeholder })]));
  }
  if (selected) kids.push(node("Select", [1, cy], [sprite("Card_Select_Line_Blue", w + 6, vis + 8, { incline: 10, depth: 20 })],
    [node("Check", [w / 2 - 30 + (vis / 2 - 26) * TAN10, vis / 2 - 26], [sprite("Common_Icon_Check_B", 56, 54, { type: 0, depth: 21 })])]));
  return node(name, [x, ROW4_Y - cy], [sprite("Common_Btn_BG", w, h, { incline: 10, depth: 7, color: `${tone}/1.00` })], kids);
}

// row 4 per tab: the portrait's faces, the memorial lobby's dialogues
function row4Items() {
  if (tab === "lobby") {
    // the dialogue's lines in the UI language, else a numbered placeholder
    return (Lobby.meta()?.sequences || []).map((sq) => {
      const text = Lobby.textOf(sq).join(" "), none = T("lobby.dialogue", { n: sq.n });
      return text ? { key: sq.anim, body: text } : { key: sq.anim, body: none, placeholder: true };
    });
  }
  if (tab === "model") return modelActions.map((a) => ({ key: a, caption: T(`model.action.${a}`) }));
  if (!spineAllowed()) return [];
  const base = currentBase();
  if (!base) return [];
  const names = mainPlayer?.skeleton ? mainPlayer.skeleton.data.animations.map((a) => a.name).filter((n) => /^\d{2}$/.test(n)).sort() : [];
  const request = thumbnailWork.thumb;
  return (names.length ? names : ["00"]).map((e) => ({ key: e, thumb: () => request(base, "face", e) }));
}
const row4Selected = () => (tab === "lobby" ? lobbySeq : tab === "model" ? modelAction : expression);
function row4Pick(key) {
  // the card follows the dialogue through Lobby.onTalk: selected while it plays, let go when it ends
  if (tab === "lobby") { if (Lobby.playing() === key) Lobby.stop(); else Lobby.play(key); }
  else if (tab === "model") {
    modelAction = key;
    modelLib?.then((M) => M.play(key));
  } else { expression = key; applyExpression(); }
}
// play every dialogue in order; the cards and the play-all button follow along (Lobby.onTalk)
function togglePlayAll() {
  if (Lobby.playingAll()) Lobby.stop();
  else Lobby.playAll(lobbySeq);
}
// a dialogue started (a card, play all, a tap on the character in fullscreen), ended or stopped. A lobby that is
// hidden stops without telling (and off the lobby tab nothing is followed): the tab change, the variant change and
// hide() reset what is followed here, so the next start redraws the cards and the play-all button.
let lobbyAll = false;
Lobby.onTalk((anim, all) => {
  if (tab !== "lobby" || (lobbySeq === anim && lobbyAll === all)) return;
  const button = lobbyAll !== all;
  lobbySeq = anim;
  lobbyAll = all;
  renderExpressions();
  if (button) renderScreen();
});

function renderExpressions() { return drawExpressions().catch(renderFailed); }
async function drawExpressions() {
  const my = ++exprToken, gen = showGen;
  const ok = () => showable("expr", my, exprToken, gen);
  const host = $("ngui-expr");
  const list = row4Items(), sel = row4Selected(), unfolded = expanded, pics = [], thumbs = [];
  const picture = (it) => typeof it.thumb === "function" ? it.thumb() : it.thumb;
  const placed = (name, box, options) => Object.assign(stripCard(name, 0, { w: box.w, h: box.h, ...options }), { pos: toNode(box, 4, LOCAL_FRAME) });
  const cardOf = (it, box) => placed(`Item_${it.key}`, box, { thumb: picture(it), body: it.body, placeholder: it.placeholder, caption: it.caption, selected: it.key === sel });
  const cards = [];
  if (unfolded || list.length <= 4) {
    const boxes = expressionBoxes(list.length + (unfolded ? 1 : 0));
    list.forEach((it, i) => cards.push({ tree: cardOf(it, boxes[i]), item: it }));
    if (unfolded) cards.push({ tree: placed("Fold", boxes.at(-1), { caption: T("fold.label"), tone: "#eef4f8" }), fold: true, name: T("fold.label") });
  } else {
    // three cards; a chosen item further along takes the third card so it stays in view
    const shown = list.slice(0, 3);
    const chosen = list.find((it) => it.key === sel);
    if (chosen && !shown.includes(chosen)) shown[2] = chosen;
    const boxes = expressionBoxes(shown.length, true);
    shown.forEach((it, i) => cards.push({ tree: cardOf(it, boxes[i]), item: it }));
    // the pile fills the button column: two plain cards behind, the top one darkened with the count
    const rest = list.filter((it) => !shown.includes(it));
    const pile = pileBoxes();
    cards.push({ tree: placed("Pile_2", pile[2], { tone: "#dfe8ef" }), pile: true, z: 100 });
    cards.push({ tree: placed("Pile_1", pile[1], { tone: "#eef4f8" }), pile: true, z: 140 });
    cards.push({ tree: placed("Pile_0", pile[0], { thumb: picture(rest[0]), dim: !!rest[0].thumb, caption: `+${rest.length}`, tone: rest[0].thumb ? "#ffffff" : "#d3dee8" }),
      pile: true, name: T("chips.more", { n: rest.length }), z: 180 });
  }
  // clip: the row's height, from the box's slanted right edge to the row's inner right edge
  const Lx = 300, Rx = 1316, TOPU = -436, BOTU = -670;
  const [cl, ct] = toStage(Lx, TOPU);
  const clip = document.createElement("div");
  clip.className = "expr-clip";
  Object.assign(clip.style, { left: `${cl}px`, top: `${ct}px`, width: `${(Rx - Lx) * S}px`, height: `${(TOPU - BOTU) * S}px` });
  const edgeTop = BOX_RIGHT + (TOPU - ROW4_Y) * TAN10 + 5 - Lx, edgeBottom = BOX_RIGHT + (BOTU - ROW4_Y) * TAN10 + 5 - Lx;
  clip.style.clipPath = `polygon(${edgeTop * S}px 0, 100% 0, 100% 100%, ${edgeBottom * S}px 100%)`;
  const track = document.createElement("div");
  track.className = "expr-track";
  clip.appendChild(track);
  const origin = [ORIGIN[0] - cl, ORIGIN[1] - ct];
  await Promise.all(cards.map(async (cd) => {
    const view = await NGUI.render(cd.tree, track, { scale: S, origin, z: cd.z || 30, textureNames: TEXTURES, deferImages: true });
    pics.push(...view.images);
    thumbs.push(...view.thumbnails);
    const body = view.nodes.get(cd.tree.name)?.els[0];
    if (!body || (cd.pile && cd.tree.name !== "Pile_0")) return;
    const strip = hitButton(track, rect(body), () => {
      if (cd.item) { row4Pick(cd.item.key); keepScroll(); renderExpressions(); }
      if (cd.pile) { expanded = true; exprScroll = 0; renderExpressions(); }
      if (cd.fold) { expanded = false; renderExpressions(); }
    }, cd.name || cd.item?.body || cd.item?.caption || (cd.item ? faceName(cd.item.key) : ""), 260);
    // the strip's cards pick one item: the picked one is pressed
    if (cd.item) strip.setAttribute("aria-pressed", String(cd.item.key === sel));
  }));
  if (!ok()) return; // a newer render took over (e.g. the tab changed while faces were rendering)
  if (my === exprToken) {
    exprPics = [picturesReady(pics, clip)];
    fillPictures(thumbs, clip);
  }
  if (unfolded) {
    clip.classList.add("expanded");
    const last = cards.at(-1).tree;
    const width = (Rx - Lx) * S, contentW = (last.pos[0] + CARD.w / 2 + SPACE.m - Lx) * S;
    track.style.width = `${contentW}px`;
    // the wheel, a mouse drag and touch scroll the strip as the popups' card rows do (Popup.scrollArea); a drag picks nothing
    horizontalScroll(clip, width, contentW);
  }
  commit(() => {
    if (!ok()) return;
    applied.expr = my;
    host.replaceChildren(clip);
    if (unfolded) clip.scrollLeft = exprScroll;
  });
}
// a portrait face's name, as the camera's expression list has it
const faceName = (key) => (key === "00" ? T("photo.natural") : `${T("photo.expressions")} ${key}`);
const keepScroll = () => { exprScroll = document.querySelector(".expr-clip")?.scrollLeft || 0; };

// --- main Spine portrait ---
// loadSpine(base) -> Promise, resolved once the portrait's first frame is drawn and the expression strip shows its
// faces (also on an error, after the message popup). The new layer stays transparent until committed; then it fades in over
// the old one, which goes. A portrait that cannot be shown takes the old one away too: the left side stays empty
// rather than showing another variant under this one's name.
const portraitLoads = latestLoad();
function loadSpine(base) {
  const my = ++spineToken;
  return portraitLoads.run((signal) => showSpine(base, my, signal));
}
async function showSpine(base, my, signal) {
  const allowed = await ensureSpine();
  if (signal.aborted || my !== spineToken) return;
  const host = $("portrait");
  const layer = document.createElement("div");
  layer.className = host.children.length || batch ? "layer fading" : "layer";
  host.appendChild(layer);
  const previous = [...host.querySelectorAll(".layer")].filter((l) => l !== layer);
  clearTimeout(blinkTimer);
  // layers fade out, then their players are freed (once each: a failed portrait may retire layers already going)
  const retire = (layers) => {
    for (const old of layers) {
      if (old.retired) continue;
      old.retired = true;
      if (batch || Loading.covering()) { old.load?.dispose(); old.remove(); continue; }
      old.classList.add("fading");
      // disposePortrait() may have freed it meanwhile (a quick second step)
      setTimeout(() => { if (old.isConnected) { old.load?.dispose(); old.remove(); } }, 400);
    }
  };
  if (!allowed) {
    mainPlayer = null;
    const portrait = variant().portrait;
    if (portrait) {
      const image = document.createElement("img");
      image.className = "portrait-static";
      image.alt = "";
      image.src = portrait;
      layer.appendChild(image);
      await decodeAll([portrait], () => image.decode?.(), HARD_CAP, { signal });
    }
    if (signal.aborted || my !== spineToken) { layer.remove(); return; }
    commit(() => {
      if (signal.aborted || my !== spineToken) return;
      layer.classList.remove("fading");
      retire(previous);
    });
    return;
  }
  prioritise(base);
  try {
    await loadSpineAssets(base, async (resolved) => {
      if (signal.aborted || my !== spineToken) { layer.remove(); return; }
      layer.load?.dispose();
      layer.replaceChildren();
      const load = layer.load = loadPlayer((config) => new spine.SpinePlayer(layer, config), {
        // the idle is set in success(): some NPC skeletons have no Idle_01, and spine-player fails on a missing one
        skeleton: `${resolved}.skel`, atlas: `${resolved}.atlas`, animation: undefined,
        premultipliedAlpha: false, alpha: true, backgroundColor: "#00000000",
        showControls: false, showLoading: false, preserveDrawingBuffer: true,
        // framed like the game (portrait-view.js), never on the skeleton's bounds; no viewport transition:
        // spine-player starts it from an undefined viewport and draws nothing meanwhile
        viewport: detailViewport(),
        // the visual mode (visual-mode.js) frames it while it is open
        frame: (p) => Visual.frame(p),
        success: async (player, live) => {
          // before the first await: once this returns, spine-player frames the view on track 0's animation and plays.
          // With nothing on track 0 it would pause (no face, no blink): a skeleton without an idle gets an empty
          // animation there that never ends, so it keeps its setup pose and the faces on tracks 1-2 still play.
          const idle = idleAnimation(player.skeleton.data);
          if (idle) player.animationState.setAnimation(0, idle, true);
          else player.animationState.setEmptyAnimation(0, 0).trackEnd = Number.MAX_VALUE;
          if (my !== spineToken || !live()) return; // a newer portrait took over; it retires this layer
          mainPlayer = player;
          applyExpression();
          await renderExpressions(); // the faces come from this skeleton
          await frames(2);           // its first frame is on the canvas
          commit(() => {
            if (my !== spineToken || !live()) return;
            if (batch || Loading.covering()) layer.style.transition = "none";
            layer.classList.remove("fading");
            retire(previous);
          });
        },
      }, { signal,
        onRecover: () => {
          if (open && !signal.aborted && my === spineToken) loadSpine(base).catch((err) => renderFailed(err, base));
        },
        onError: (err) => {
          if (signal.aborted || my !== spineToken) return;
          clearTimeout(blinkTimer); mainPlayer = null;
          layer.querySelector(".spine-player-error")?.remove();
          retire([layer]);
          renderFailed(err, base);
        },
      });
      layer.player = load.player;
      await load.ready;
    }, { signal });
    if (signal.aborted || my !== spineToken) layer.remove();
  } catch (err) {
    if (!signal.aborted && my === spineToken) renderFailed(err, base);
    // spine-player's own error box (black, with the message) never shows: the message popup says it
    layer.querySelector(".spine-player-error")?.remove();
    if (my === spineToken) {
      if (mainPlayer === layer.player) { clearTimeout(blinkTimer); mainPlayer = null; } // it broke after it was shown
      commit(() => { if (my === spineToken) retire([...previous, layer]); });
    }
  }
}
// the detail screen's portrait framing: the skeleton's origin on CharacterPos
const detailViewport = () => portraitViewport(layouts && find(layouts.list, "CharacterPos"), { origin: ORIGIN, s: S, box: PORTRAIT_BOX });
function scheduleBlink(player) {
  clearTimeout(blinkTimer);
  if (expression !== "00" || mainPlayer !== player) return;
  blinkTimer = setTimeout(() => {
    if (expression !== "00" || mainPlayer !== player) return;
    if (player.skeleton?.data.findAnimation("Eye_Close_01")) {
      player.animationState.setAnimation(2, "Eye_Close_01", false);
      player.animationState.addEmptyAnimation(2, 0.1, 0);
    }
    scheduleBlink(player);
  }, 2500 + Math.random() * 3500);
}
function applyExpression() {
  clearTimeout(blinkTimer);
  mainPlayer?.animationState.clearTrack?.(2);
  if (mainPlayer?.skeleton?.data.findAnimation(expression)) mainPlayer.animationState.setAnimation(1, expression, true);
  if (mainPlayer) scheduleBlink(mainPlayer);
}
function disposePortrait() {
  clearTimeout(blinkTimer);
  spineToken += 1;
  portraitLoads.cancel();
  for (const l of $("portrait").querySelectorAll(".layer")) { l.load?.dispose(); l.remove(); }
  mainPlayer = null;
}

// --- the detail screen's popups, on the shared popup module (popup.js): the list popups (switch portrait or memorial
// lobby, the variant picker) and the frame (compare, aliases, details); Escape and the backdrop close them ---

// switch portrait or memorial lobby: the variant's own skeleton and added ones, then the other variants' portraits;
// OK writes the chosen one into the game files (applyCandidate)
// While mods are off the popup saves the choice instead of writing it (popupMode): it starts on the configured source,
// whose card carries "Set", and its button reads Save. On an overwritten kind its cards are greyed (switch-sources.js):
// a click on one opens the entry's Add First or Overwritten message instead of choosing it.
let popupSeg = 0, popupKind = "spr", popupStart = null, switchPopupPage = null;
let switchThumbnails = null;
const modsOn = () => modsState()?.enabled !== false;
const GREYED_BADGE = () => ({ text: T("mods.overwritten"), color: COLOUR.greyed, ink: "#ffffff" });
function renderPopup(scroll = 0) { return drawPopup(scroll).catch(renderFailed); }
async function drawPopup(scroll = 0) {
  if (!switchThumbnails || switchPopupPage !== pageToken) return;
  const v = variant(), kind = popupKind, applied = intendedKey(v, kind);
  const mode = popupMode(fam, v, kind, modsOn());
  const items = candidates(v, kind).map((c) => {
    const work = switchThumbnails;
    const picture = lazyThumbnail(() => kind === "model" ? modelPicture(c, v, work)
      : thumb(baseOf(c, kind), kind === "spr" ? "portrait" : "landscape", null, kind === "lobby", specialFraming(v), (c.origin || c.variant || v).bg, work), work.signal);
    return {
      key: c.key, kind: c.kind, recruit: c.kind !== "mod" && c.recruit,
      label: candidateLabel(c, v),
      full: c.kind === "mod" ? c.label : fullName(c.variant),
      original: c.mod?.alias ? modOriginalName(c.mod) : "",
      nameEdit: c.kind === "mod" ? { label: T("modAlias.title"), fn: (s = scroll) => editModName(c.mod,
        () => switchPopupPage === pageToken && !$("popup").hidden ? renderPopup(s) : null) } : null,
      // The card and info panel consume one still, including its fallback, so they cannot disagree after a retry.
      thumb: picture, big: () => picture.start(),
      inUse: false, applied: !!coreOf(v, kind) && c.key === applied, fav: c.fav, isNew: c.kind === "mod" && c.mod.fresh && c.key !== applied, src: c,
      inUseText: c.mod?.incomplete ? T("mods.incomplete") : coreOf(v, kind) && c.key === applied ? T("status.applied") : null,
      dim: c.greyed,
      badge: c.greyed ? { ...GREYED_BADGE(), ...(!coreOf(v, kind) && c.kind === "self" ? { text: T("import.reason.no_target") } : {}) }
        : c.mod?.incomplete ? { text: T("mods.incomplete"), color: COLOUR.warning, ink: "#ffffff" } : null,
      tag: c.mod?.repaired_from ? { text: T("remap.repaired"), color: "#d7eaf4", ink: "#2d4664" } : null,
    };
  })
    .filter((it) => popupSeg === 0 || (popupSeg === 1) === !!it.recruit)
    .sort((a, b) => (b.fav ? 1 : 0) - (a.fav ? 1 : 0));
  await listPopup({
    hostId: "popup", layoutKind: kind, title: T(isSpecial(v) ? "special.switch" : kind === "spr" ? "popup.switch" : `row.switch.${kind}`), okText: T(mode.button), okSemantic: "save", items, selected: pending, keepScroll: scroll,
    tools: { segs: ["seg.all", "seg.recruit", "seg.noRecruit"], seg: popupSeg, onSeg: (i) => { popupSeg = i; renderPopup(); }, sortKey: "sort.default" },
    onSelect: (key, s) => {
      const c = candidates(v, kind).find((x) => x.key === key);
      if (c?.kind === "mod") markSeen([c.mod], s);
      pending = key;
      renderPopup(s);
    },
    onOk: () => closePopup(true), onClose: () => closePopup(false),
    details: (it) => {
      const c = it.src;
      const file = c.kind === "mod" ? c.mod.file : gameFile(c.variant, kind);
      const size = c.kind === "mod" ? c.mod.size : partsOf(c.variant, kind).reduce((n, f) => n + (f.size || 0), 0);
      const status = !coreOf(v, kind) ? "import.reason.no_target" : candidateStatus(v, kind, c.key, c.mod);
      const statusColor = status && coreOf(v, kind) ? `${COLOUR.success}/1.00` : null;
      const error = c.key === mode.set ? selectionError(v, kind) : null;
      const crcState = c.key === appliedKey(v, kind) ? crcOf(v, kind) : null;
      const crc = crcState === "fixed" ? ["crc.fixed", COLOUR.success] : crcState === "needs_fix" ? ["crc.needed", COLOUR.warning] : crcState === "ok" ? ["crc.original", ""] : null;
      return {
        title: T(isSpecial(v) ? "special.background" : kind === "spr" ? "info.title.portrait" : `info.title.${kind}`),
        fields: [[T("field.file"), baseName(file)], [T("field.size"), sizeText(size)], [T("field.status"), [status && T(status), error && T(`error.${error.code}`, error.params)].filter(Boolean).join("\n") || "—", statusColor],
          [T("field.crc"), crc ? T(crc[0]) : "—", crc?.[1] && `${crc[1]}/1.00`]],
        actions: [[T("popup.apply"), () => applyCandidate(c.key, kind), !coreOf(v, kind) || isApplied(v, kind, c.key) || !!c.greyed || !!c.previewOnly || busy, "apply", c.previewOnly ? T("model.unfit") : null],
          [T(platform === "mac" ? "open.mac" : "open.win"), () => reveal(file)],
          ...(c.kind === "mod" ? [[T("compatibility.check"), async () => {
            let rebuilt = false;
            await call(() => Compatibility.check(c.id, c.label, () => { rebuilt = true; return remapped(); }, c.mod, modProgressName(c)));
            if (open && !rebuilt) await renderPopup();
          }], ...(repairedId(c) ? [[T("remap.go"), () => jumpRepaired(c)]] : [[T("act.update"), () => runUpdate(c)]])] : []),
          c.kind === "mod"
            ? [T("btn.delete"), async () => {
              if (!(await removeMod(c.id))) return;
              await reload();
              ctx.invalidateRoster();
              if (pending === c.key) pending = popupMode(fam, variant(), kind, modsOn()).start;
              renderPopup();
            }]
            : [T("btn.extract"), () => { closePopup(false); job("actions.extract", { target: `variant:${c.variant.key}` }, { name: fullName(c.variant), onDone: (r) => reveal(r?.folder) }); }]],
      };
    },
  });
}
async function openPopup(kind = "spr") {
  const family = fam, v = variant();
  if (kind === "model") {
    try { await costumesReady(v, { wait: true }); } catch (err) { console.error("Costume check failed", err); }
    if (!open || fam !== family || variant() !== v) return;
  }
  switchThumbnails?.cancel();
  switchThumbnails = thumbnailScope({ priority: "popup" });
  switchPopupPage = pageToken;
  popupKind = kind;
  popupStart = popupMode(fam, variant(), kind, modsOn()).start;
  pending = popupStart;
  popupSeg = 0;
  const mods = candidates(variant(), kind).filter((c) => c.kind === "mod").map((c) => c.mod);
  try {
    await drawPopup();
    if (open && fam === family && variant() === v && popupKind === kind && !$("popup").hidden) await markSeen(mods);
  } catch (err) { closePopup(false); renderFailed(err); }
}

const seeing = new Set();
async function markSeen(mods, scroll = 0) {
  const ids = [...new Set(mods.filter((m) => m.fresh && !seeing.has(m.id)).map((m) => m.id))];
  if (!ids.length) return;
  const family = fam;
  ids.forEach((id) => seeing.add(id));
  try {
    await rpc("mods.seen", { ids });
    clearUnseen(family, ids);
    if (fam !== family) clearUnseen(fam, ids);
    ctx.invalidateRoster();
    // The save may finish after the switch closed and another popup took its host.
    if (open) await Promise.all([renderScreen(), renderChips(),
      switchPopupPage === pageToken && !$("popup").hidden ? renderPopup(scroll) : null]);
  } catch (err) { console.error("Mark mods seen failed", err); }
  finally { ids.forEach((id) => seeing.delete(id)); }
}
function closePopup(apply) {
  switchThumbnails?.cancel();
  switchThumbnails = null;
  switchPopupPage = null;
  $("popup").hidden = true;
  $("popup").innerHTML = "";
  popupClosed($("popup"));
  const chosen = candidates(variant(), popupKind).find((c) => c.key === pending);
  // only a choice other than the start (what the game shows; while mods are off, the configured source)
  if (apply && chosen && !chosen.greyed) return saveCandidate(pending, popupKind);
}

// The confirmation is read again when opened, so configurations in other installs are included.
async function removeMod(id) {
  try {
    const info = await rpc("mods.removalInfo", { id });
    if(info.official)return false;
    const r = await message({
      title: T("btn.delete"),
      lines: info.used?[T("error.asset_delete_confirm")]:[T("mods.deleteConfirm", { name: info.name })],
      ok: T("btn.delete"), cancel: T("popup.cancel"),
    });
    if (r.button !== "ok") return false;
    return await job("mods.remove", { id, confirm: true }, {
      name: info.name,
      onDone: (result) => { if (result?.failed?.length) throw Object.assign(new Error('Asset deletion failed'),result.failed[0]); },
    });
  } catch (err) {
    notify(errorText(err), { kind: "error", error: err });
    return false;
  }
}

// the Add First or Overwritten message of a variant's files of a kind (prompts.js); resolves with true once its mod
// was added to the library
async function overwrittenMessage(v, kind = "spr") {
  const page = pageToken;
  const core = coreOf(v, kind);
  const added = await Prompts.overwritten({ name: entryLabel({ core, kind }, () => fullName(v)), core, files: partsOf(v, kind) });
  if (added && open && page === pageToken) { ctx.invalidateRoster(); await reload(); }
  return added;
}

// variant picker: every variant of the family in the same list layout
let pickerSeg = 0;
function closePicker() {
  $("picker").hidden = true;
  popupClosed($("picker"));
}
async function openPicker(scroll = 0) {
  pickerSel = pickerSel || variantKey;
  const items = fam.variants.filter(hasAsset).sort(byRank).map((o) => ({
    key: o.key, kind: o.recruitable ? "playable" : "other", recruit: o.recruitable, label: shortName(o), full: fullName(o),
    thumb: primaryKind(o) === "model" ? null : thumb(variantBase(o, primaryKind(o)), "card", null, primaryKind(o) === "lobby", specialFraming(o), o.bg),
    big: () => primaryKind(o) === "model" ? null : thumb(variantBase(o, primaryKind(o)), "bust", null, primaryKind(o) === "lobby", specialFraming(o), o.bg),
    inUse: false, fav: isFav(o), isNew: hasFresh(o), src: o,
  })).filter((it) => pickerSeg === 0 || (pickerSeg === 1) === it.recruit);
  await listPopup({
    hostId: "picker", title: T("picker.title"), okText: T("picker.ok"), items, selected: pickerSel, keepScroll: scroll,
    tools: { segs: ["seg.all", "seg.recruit", "seg.noRecruit"], seg: pickerSeg, onSeg: (i) => { pickerSeg = i; openPicker(); }, sortKey: "sort.release" },
    onSelect: (key, s) => { pickerSel = key; openPicker(s); },
    onOk: () => { closePicker(); const k = pickerSel; pickerSel = null; ctx.go(fam.key, k); },
    onClose: () => { closePicker(); pickerSel = null; },
    details: (it) => {
      const o = it.src, kind = primaryKind(o), file = gameFile(o, kind);
      return {
        title: T(isSpecial(o) ? "special.background" : "info.title.portrait"),
        fields: [[T("field.file"), coreOf(o, kind)], [T("info.gameFile"), o.date || "—"], [T("info.status"), T(appliedKey(o, kind) === "self" ? "status.original" : "status.replaced")],
          [T(isSpecial(o) ? "special.background" : "tab.lobby"), T(o.lobby ? "model.has" : "model.none")]],
        actions: [[T(platform === "mac" ? "open.mac" : "open.win"), () => reveal(file)],
          [T("btn.extract"), () => { closePicker(); job("actions.extract", { target: `variant:${o.key}` }, { name: fullName(o), onDone: (r) => reveal(r?.folder) }); }]],
      };
    },
  });
}

// --- the alias editor (the nameplate's pencil): the user's own aliases of the variant on the popup frame, one input
// row each, drawn like the game's input row (UIPopup_Input's Input_NickName: a grey box with a line round it, a dark
// icon at its end), the cross removing it, then an empty row with the pencil to add one while there is room. OK keeps
// them (aliases.set); Cancel, the close button, the backdrop or Escape drop the changes. Enter in the empty row adds
// what it holds, in another row goes to the next one, in an empty one at the end keeps them; never while an input
// method is composing (isComposing, or keyCode 229, which WebKit sends for the key that ends a conversion). ---
const AL = ALIAS;
let aliasToken = 0;
// draft: the rows; focusAt: the row to focus (the empty one when null); carry: the empty row of the editor drawn before,
// which keeps the focus while this one is drawn: what is typed there meanwhile goes into the new empty row
async function openAliases(draft = customAliases(), focusAt = null, key = variantKey, carry = null) {
  const my = ++aliasToken;
  await Loading.wait(frameReady(), { immediate: true, cap: null });
  if (my !== aliasToken || !open || variantKey !== key) return;
  const host = $("popup");
  const names = draft.slice(0, ALIAS_MAX);
  const adding = names.length < ALIAS_MAX;
  const n = names.length + (adding ? 1 : 0);
  const { h, ys } = aliasFrame(n);
  const t = popupFrame(T("alias.title"), AL.w, h, { ok: T("picker.ok"), cancel: T("popup.cancel") });
  const popup = find(t, "Popup");
  ys.forEach((y, i) => {
    const add = adding && i === n - 1;
    const icon = add ? sprite("Cafe_Icon_NameEdit", 50, 50, { type: 0, depth: 9, color: "#2d4664/1.00" })
      : sprite("Common_Icon_Close", 42, 42, { type: 0, depth: 9, color: "#2d4664/1.00" });
    popup.children.push(node(`Row_${i}`, [0, y], [sprite("Common_Bg_Raius10px", AL.rowW, AL.rowH, { depth: 6, color: "#e6eaec/1.00" })], [
      node("BG_Line", [0, 0], [sprite("Common_line_10px", AL.rowW + 2, AL.rowH, { depth: 7, color: "#cfd4da/1.00" })]),
      node("Icon", [AL.rowW / 2 - 58, 0], [icon]),
    ]));
  });
  host.hidden = false;
  const next = document.createElement("div");
  const view = await NGUI.render(t, next, { scale: S, origin: [960, 540], z: 10 });
  if (my !== aliasToken) return;
  const close = () => { aliasToken += 1; host.hidden = true; host.innerHTML = ""; popupClosed(host); };
  // the screen was left or shows another variant meanwhile
  if (!open || variantKey !== key) { close(); return; }
  const inputs = [];
  // the rows' aliases as edited, and what the empty row holds
  const rows = () => inputs.slice(0, names.length).map((el) => el.value);
  const typed = () => (adding ? inputs[n - 1].value : "");
  const keep = async () => {
    const saved = await call(() => rpc("aliases.set", { variant: key, aliases: [...rows(), typed()] }));
    if (!saved || my !== aliasToken) return; // an error is shown and the editor stays
    close();
    ctx.invalidateRoster();
    if (variantKey !== key) return;
    variant().custom = saved;
    renderScreen();
  };
  ys.forEach((y, i) => {
    const add = adding && i === n - 1;
    const el = document.createElement("input");
    el.className = "alias-input";
    el.type = "text";
    el.maxLength = ALIAS_LENGTH;
    el.spellcheck = false;
    el.autocomplete = "off";
    el.enterKeyHint = add ? "done" : "next";
    el.value = add ? carry?.value || "" : names[i];
    el.placeholder = add ? T("alias.add") : "";
    el.setAttribute("aria-label", add ? T("alias.add") : T("alias.title"));
    Object.assign(el.style, {
      left: `${960 + (-AL.rowW / 2 + 36) * S}px`, top: `${540 - (y + AL.rowH / 2) * S}px`,
      width: `${(AL.rowW - 150) * S}px`, height: `${AL.rowH * S}px`, fontSize: `${50 * S}px`,
    });
    el.addEventListener("keydown", (ev) => {
      if (ev.isComposing || ev.keyCode === 229 || ev.key !== "Enter") return;
      ev.preventDefault();
      if (add && el.value.trim()) {
        const text = el.value;
        el.value = "";
        openAliases([...rows(), text], names.length + 1, key, el);
      }
      else if (i < n - 1) inputs[i + 1].focus();
      else keep();
    });
    next.appendChild(el);
    inputs.push(el);
    const [ix, iy] = [960 + (AL.rowW / 2 - 58) * S, 540 - y * S], size = 84 * S;
    const b = hitButton(next, [ix - size / 2, iy - size / 2, size, size],
      add ? () => el.focus() : () => openAliases(rows().filter((_, j) => j !== i), i, key, adding ? inputs[n - 1] : null),
      add ? T("alias.add") : T("alias.remove"), 86);
    if (add) b.tabIndex = -1;
  });
  const hit = (path, fn, name, action) => hitNode(next, view, path, fn, { name, action });
  hit("Center/Popup/Btn/Btn_Ok/BG (1)", keep, T("picker.ok"), "ok");
  hit("Center/Popup/Btn/Btn_Cancel/BG (1)", close, T("popup.cancel"), "cancel");
  hit("Center/Popup/Base/BG/Btn_Close", close, T("display.close"), "close");
  closable(view, host, close);
  host.replaceChildren(...next.childNodes);
  const target = inputs[focusAt ?? inputs.length - 1] || inputs[inputs.length - 1];
  target?.focus();
}

// --- details (the info bar's Details button): the shown tab's game files on the popup frame. A summary (the folder,
// what the files carry and, for a mod, its file and the variant it was made for), then one row per bundle: name,
// size, date, CRC and what it carries ---
const DT = DETAILS;
const DT_COLS = [["field.file", 1040], ["field.size", 220], ["field.date", 270], ["field.crc", 290], ["field.status", 260]];
const crcLabel = (state) => (state === "fixed" ? [T("crc.fixed"), COLOUR.success] : state === "needs_fix" ? [T("crc.needed"), COLOUR.warning] : [T("crc.original"), "#000000"]);
// what a game file carries, as a label: the original, a mod's name, another variant's name, or "replaced"
function carriedLabel(applied) {
  if (!applied || applied === "official") return T("status.original");
  if (applied.startsWith("mod:")) { const m = familyMods().find((x) => x.id === applied.slice(4)); if (m) return modLabel(m); }
  if (applied.startsWith("variant:")) { const o = fam.variants.find((x) => x.key === applied.slice(8)); if (o) return shortName(o); }
  return T("status.replaced");
}
function detailsInfo() {
  const v = variant(), kind = tabKind(), parts = partsOf(v, kind);
  const first = parts[0]?.file || null;
  const folder = first ? first.replace(/[\\/][^\\/]*$/, "") : "—";
  const applied = appliedOf(v, kind);
  const fields = [[T("field.folder"), folder], [T("info.current"), tab === "portrait" ? current().label : sourceLabel(v, kind), shownMod()?.mod]];
  const mod = applied.startsWith("mod:") ? familyMods().find((m) => m.id === applied.slice(4)) : null;
  const from = mod ? fam.variants.find((o) => o.key === mod.target) : applied.startsWith("variant:") ? fam.variants.find((o) => o.key === applied.slice(8)) : null;
  if (mod) fields.push([T("field.modFile"), mod.file || "—"]);
  if (from) fields.push([T("field.source"), fullName(from)]);
  const rows = parts.map((p) => {
    const crc = crcLabel(p.crc);
    return [baseName(p.file), sizeText(p.size), p.date || "—", [crc[0], crc[1]], carriedLabel(p.applied)];
  });
  return { title: T(isSpecial(v) && tab === "lobby" ? "special.background" : `info.title.${tab}`), fields, rows, reveal: first };
}
// the summary box and the file table, laid out from the top left of the scrolling area (units); returns the tree
// and its height
function detailsBody({ fields, rows }) {
  const W = popupContent(DT.w, DT.h).w, kids = [];
  // summary: captions, then values that wrap (paths), never cut; on the game's light stats box, as wide as the file table under
  // it. A mod's value leaves room at the right for its edit icon, and its original name stands under it.
  const TOP = SPACE.s;
  // The light stats box retains STATS_BOX's own geometry and typography (stats-box.js).
  const box = statsBox({ holder: "table", width: W, at: [W / 2, -TOP], name: "Summary", rows: fields.map(([k, v, mod]) => ({
    caption: k, value: String(v), ariaLabel: String(v), reserve: mod ? 70 : 0, secondary: mod?.alias ? modOriginalName(mod) : "",
    slot: mod ? ({ i, x, valueY, room, text }) => [node(`Edit_${i}`, [x + Math.min(glyphsOf(text.text, text.fs), room) + 32, valueY],
      [sprite("Cafe_Icon_NameEdit", 46, 46, { type: 0, depth: 9, color: "#2d4664/1.00" })])] : undefined,
  })) });
  kids.push(...box.nodes);
  const boxH = box.height;
  // the file table: the list header's light blue bar with the captions, then the bundles
  const cols = DT_COLS.filter((_, i) => i < 4 || rows.some((r) => r[4] !== null));
  const extra = DT_COLS.reduce((a, [, w]) => a + w, 0) - cols.reduce((a, [, w]) => a + w, 0);
  const columns = row(cols.map(([, w], i) => ({ grow: w + (i === 0 ? extra : 0) })), { w: W }).boxes;
  const widths = columns.map(b => b.w - 2 * SPACE.s);
  const head = cols.map(([key], i) => textItem(NGUI, T(key), TYPE.caption, { maxW: widths[i], lines: 2 }));
  const data = rows.map(r => cols.map((_, i) => {
    const [value, color] = i === 3 ? r[3] : [r[i], "#000000"];
    // Dense rows keep their 42-unit floor. Wrap paths at separators and other text at language-aware boundaries;
    // longer values grow the row, keeping every character rather than escaping the range or clipping.
    let fit = i === 0 ? wrapPath(value, TYPE.caption.fs, TYPE.caption.min, widths[i], Infinity)
      : NGUI.wrapText(String(value), TYPE.caption.fs, widths[i], Infinity);
    if (fit.overflow) fit = wrapPath(value, TYPE.caption.fs, TYPE.caption.min, widths[i], Infinity);
    return { ...textItem({ ...NGUI, fitText: () => fit }, value, TYPE.caption, { maxW: widths[i], lines: Infinity }), color };
  }));
  const lines = [head, ...data].map(items => row(items.map((item, i) => ({ ...item, w: widths[i] })), { x: SPACE.s, gap: 2 * SPACE.s }));
  const table = stack(lines.map(line => ({ w: W, h: line.h + 2 * SPACE.s })), { y: TOP + boxH + SPACE.xl, gap: SPACE.s });
  table.boxes.forEach((box, ri) => {
    const items = row(lines[ri].boxes, { x: SPACE.s, y: box.y + SPACE.s, gap: 2 * SPACE.s }).boxes;
    if (!ri) kids.push(node("Head", toNode(box, 4, LOCAL_FRAME), [sprite("Common_Bg_Raius10px", W, box.h, { depth: 5, color: "#d1edff/1.00" })]));
    items.forEach((item, i) => kids.push(node(ri ? `R_${ri - 1}_${i}` : `H_${i}`, toNode(item, 3, LOCAL_FRAME), [
      label(item.text, item.fs, ri ? `${item.color}/1.00` : "#294263/1.00", item.w, item.h, { pivot: 3, depth: 8 }),
    ])));
    if (ri) kids.push(node(`RL_${ri - 1}`, [W / 2, -box.y - box.h], [sprite("Common_Line_Dot", W - 2 * SPACE.m, SPACE.hair, { type: 2, depth: 7, color: "#c0c9d4/1.00" })]));
  });
  return { tree: node("Details", [0, 0], [], kids), height: table.boxes.at(-1).y + table.boxes.at(-1).h + SPACE.m };
}
async function showDetails() {
  const host = $("popup");
  const info = detailsInfo();
  const body = detailsBody(info);
  // popup.js supplies the L frame; popupContent keeps the table 40 from either side (popup-frames.js).
  const { h, top, bottom, content } = detailsFrame(body.height);
  const t = popupFrame(info.title, DT.w, h, { ok: T("picker.ok"), cancel: info.reveal ? T(platform === "mac" ? "open.mac" : "open.win") : null });
  host.hidden = false;
  const next = document.createElement("div");
  const view = await NGUI.render(t, next, { scale: S, origin: [960, 540], z: 10 });
  const done = () => { host.hidden = true; host.innerHTML = ""; popupClosed(host); };
  const hit = (path, fn, name, action) => hitNode(next, view, path, fn, { name, action });
  hit("Center/Popup/Base/BG/Btn_Close", done, T("display.close"), "close");
  hit("Center/Popup/Btn/Btn_Ok/BG (1)", done, T("picker.ok"), "ok");
  if (info.reveal) hit("Center/Popup/Btn/Btn_Cancel/BG (1)", () => reveal(info.reveal), T(platform === "mac" ? "open.mac" : "open.win"), "cancel");
  closable(view, host, done);
  const inner = scrollArea(next, [960 + (content.x - DT.w / 2) * S, 540 - top * S, content.w * S, (top - bottom) * S], body.height * S);
  await inner.ready;
  const bodyView = await NGUI.render(body.tree, inner, { scale: S, origin: [0, 0], z: 20 });
  info.fields.forEach(([, , mod], i) => {
    if (mod) hitNode(inner, bodyView, `Details/Edit_${i}`, () => editModName(mod, () => !host.hidden ? showDetails() : null), { name: T("modAlias.title") });
  });
  if (host.hidden) return;
  host.replaceChildren(...next.childNodes);
}

async function renderAll() {
  previewEnabled = modsOn();
  await Promise.all([renderScreen(), renderChips(), renderExpressions()]);
}

// A tab or applied preview prepares under the shared in-page shade, with the input hold retained.
function changeInPlace(work) {
  const g = begin();
  return Loading.page(async (covered) => {
    await covered;
    if (g.gen !== showGen) return false;
    return prepare(g, true, async () => {
      await work();
      await settleImages();
    });
  }, { until: g.ended }).then((ready) => {
    // it may have taken over a show (a tab clicked while a step loads): the screen is on that student now
    if (ready && fam) arrived(fam.key, askedKey);
    return ready;
  });
}

// tabs: the left side shows the portrait, the memorial lobby or the 3D model
function showTab(id) {
  if (busy || Loading.held()) return;
  tab = id;
  if (id === "model") {
    const v = variant();
    void costumesReady(v).then((checked) => { if (checked) costumesChecked(v); }, (err) => console.error("Costume check failed", err));
  }
  expanded = false;
  exprScroll = 0;
  lobbySeq = null;
  lobbyAll = false;
  return changeInPlace(() => Promise.all([renderAll(), renderLeft()]));
}

// renderLeft() resolves once the preview has drawn its first frame or failed. It loads laid out but
// transparent (.pending: a hidden canvas has no size) and is
// committed with the rest of the screen; the part it replaces fades out and is freed. The lobby and the model are
// single players: when the shown one is replaced by another variant's, a still of it holds the place meanwhile.
const LEFT = ["portrait", "lobby", "model"];
let shownLeft = null, shownKey = null;
// what the lobby or the model shows: the same variant of another install (game folder, file source) is another one
const leftKey = (part, v) => (part === "lobby" ? `${v.lobby}:${shownSource(v, "lobby")}@${fam.version}` : part === "model" ? `${v.key}:${shownSource(v, "model")}@${fam.version}` : null);
let lobbyFrom = null; // the install of the lobby in lobby.js, which keeps a lobby shown again by its core
let lobbyStale = false; // its game files were switched: lobby.js reads them again
function renderLeft() {
  if (!open) return Promise.resolve();
  const v = variant(), my = ++leftToken, want = tab, el = $(want), key = leftKey(want, v);
  const available = previewAvailable(v, switchKindOf(want));
  if (!available) {
    if (want === "portrait") disposePortrait();
    else if (want === "lobby") Lobby.hide(el);
    else hideModel();
  }
  const same = want === shownLeft;
  if (!same) {
    el.hidden = false;
    el.classList.remove("leaving");
    el.classList.add("pending");
  }
  const swap = same && key !== shownKey ? freezeLeft(want) : null;
  let expired = false;
  // a newer left side or hide() came first: nothing is started
  const ready = withTimeout(Promise.resolve(swap).then(() => (my !== leftToken || expired || !available ? null
    : want === "portrait" ? (mainPlayer ? null : loadSpine(currentBase()))
      : want === "lobby" ? showLobby(v, my, () => !expired) : showModel(v, my, () => !expired))), want === "lobby" ? HARD_CAP + 3000 : HARD_CAP, `Detail ${want}`).catch((err) => {
    expired = true;
    if (my !== leftToken || !open) return;
    if (want === "lobby") Lobby.unavailable(err, el, spineBase("lobby", v.lobby));
    else if (want === "portrait") disposePortrait();
    else hideModel();
    renderFailed(err);
  });
  const shown = ready.then(() => commit(() => {
    if (my !== leftToken) return;
    el.hidden = false;
    el.classList.remove("pending", "leaving");
    shownLeft = want;
    shownKey = key;
    unfreezeLeft();
    if (want === "lobby") Lobby.reveal(); // on screen: its intro starts
    for (const part of LEFT) if (part !== want) retireLeft(part, my);
  }));
  return shown;
}
// a part no longer shown: faded out, then freed (unless a newer change wants it again)
function retireLeft(part, my) {
  const el = $(part);
  const free = () => {
    el.hidden = true;
    el.classList.remove("pending", "leaving");
    if (part === "portrait") disposePortrait();
    else if (part === "lobby") Lobby.hide(el);
    else hideModel();
  };
  if (batch || Loading.covering() || el.hidden || el.classList.contains("pending")) { free(); return; }
  el.classList.add("leaving");
  setTimeout(() => { if (my === leftToken && el.classList.contains("leaving")) free(); }, 360);
}
// a still of the shown lobby or model over the left side, until its replacement is committed. The model's canvas
// keeps no drawing buffer: it is copied in an animation frame, after the model's own loop has drawn into it.
async function freezeLeft(part) {
  const host = $(part);
  const sources = [...host.querySelectorAll(part === "lobby" ? ".lobby-view .lobby-layer:not(.lobby-off) canvas" : "canvas.model-view")];
  if (!sources.length) return;
  if (part === "model") await nextFrame();
  const box = document.createElement("div");
  box.className = `left-freeze ${part}`;
  Object.assign(box.style, { left: `${host.offsetLeft}px`, top: `${host.offsetTop}px`, width: `${host.offsetWidth}px`, height: `${host.offsetHeight}px` });
  const c = document.createElement("canvas");
  c.width = sources[0].width;
  c.height = sources[0].height;
  const g = c.getContext("2d");
  for (const src of sources) if (src.width && src.height) g.drawImage(src, 0, 0, c.width, c.height);
  box.appendChild(c);
  if (part === "lobby") { const frost = document.createElement("div"); frost.className = "lobby-frost"; box.appendChild(frost); }
  unfreezeLeft(true);
  $("lobby").after(box);
}
function unfreezeLeft(now = false) {
  for (const box of document.querySelectorAll(".left-freeze:not(.leaving)")) {
    if (now || batch || Loading.covering()) { box.remove(); continue; }
    box.classList.add("leaving");
    setTimeout(() => box.remove(), 360);
  }
}

// the UI language -> its key in the lobby lines
const LINE_LANG = { "zh-CN": "zh-CN", "zh-TW": "zh-TW", "en-US": "en", "ja-JP": "ja", "ko-KR": "ko", "th-TH": "th" };
// the memorial lobby: every layer in and drawn, the still for the switch card, the dialogue strip; the lines when
// they come in time (later ones re-render the strip)
async function showLobby(v, my, alive = () => true) {
  const still = () => alive() && open && my === leftToken && tab === "lobby" && variantKey === v.key;
  if (!(await ensureSpine())) {
    if (still()) Lobby.hide($("lobby"));
    return;
  }
  if (!still()) return;
  if (lobbyFrom !== fam.version || lobbyStale) Lobby.hide($("lobby"));
  lobbyFrom = fam.version;
  lobbyStale = false;
  const c = current("lobby");
  const options = { framing: specialFraming(v),
    mod: c.kind === "mod" && (!!v.previewSources?.lobby || !!v.configured || !modsOn() || !v.lobby) ? c.id : null,
    official: c.kind === "self" && (!!v.configured || !!v.previewSources?.lobby || !modsOn()) };
  let m = await Lobby.show(v.lobby, $("lobby"), v.key, options);
  if (!still()) return;
  // A cancelled attempt is not a ready preview. The newest detail render owns one fresh attempt.
  if (!m) m = await Lobby.show(v.lobby, $("lobby"), v.key, options);
  if (!still()) return;
  if (!m) throw new Error(`Lobby load superseded without a replacement: ${v.lobby}`);
  if (fullscreen) { Lobby.frame("full"); Lobby.interactive(true); }
  const lines = refreshLobbyLines(v, m, still);
  // the still is taken in the left framing, once the players have drawn a few frames of it; while the intro plays (its
  // flash and scenes are not the lobby at rest), once it has ended
  await frames(4);
  if (!still()) return;
  if (Lobby.introPending()) {
    Lobby.introDone().then(() => frames(4)).then(() => {
      if (!still() || lobbyShot || fullscreen) return;
      lobbyShot = snapshotLobby();
      if (lobbyShot) return renderScreen();
    }).catch(renderFailed);
  } else if (!lobbyShot && !fullscreen) lobbyShot = snapshotLobby();
  await Promise.all([renderScreen(), renderExpressions(), Promise.race([lines, sleep(LINES_CAP)])]);
}

let linesRevision = 0;
function refreshLobbyLines(v, meta, alive = () => true) {
  const my = ++linesRevision, lang = language(), key = LINE_LANG[lang] || "en";
  const still = () => my === linesRevision && alive() && open && tab === "lobby"
    && variantKey === v.key && language() === lang && Lobby.meta() === meta;
  const talks = Object.fromEntries(meta.sequences.map((sq) => [sq.anim, sq.cues.map((cue) => cue.clip)]));
  // Change the subtitle picker immediately, retaining voice metadata while the
  // next language's table is fetched. Pending voice cues must keep playing.
  Lobby.setLines(undefined, (line) => line?.[key] || "");
  return (isSpecial(v) ? Promise.resolve({}) : rpc("lobby.lines", { variant: v.key, talks, lang: key })).then((table) => {
    if (!still()) return;
    Lobby.setLines(table, (line) => line?.[key] || "");
    return renderExpressions();
  }).catch((err) => { console.error("Lobby lines failed", err); });
}
// the 3D chibi: loaded, its shaders compiled and its first frame drawn; the actions fill row 4 and a still of it goes
// on the switch card
async function showModel(v, my, alive = () => true) {
  const still = () => alive() && open && my === leftToken && tab === "model" && variantKey === v.key;
  try {
    const M = await ModelLib();
    if (!still()) return;
    const list = await M.show(v.key, $("model"), {
      frame: fullscreen ? "full" : "left", version: `${fam.version}:${modelGeneration}`, source: modelSource(v, modsOn()),
      onAction: (a) => { if (tab === "model" && variantKey === v.key) { modelAction = a; renderExpressions(); } },
    });
    if (!list || !still()) return;
    modelActions = list;
    modelAction = M.current();
    if (!modelShot && !fullscreen) modelShot = M.snapshot();
    await Promise.all([renderScreen(), renderExpressions()]);
  } catch (err) {
    // a model that cannot be read (a broken or unreadable GLB): the tab stays, empty, and the rest of the screen works
    console.error(err);
    if (!still()) return;
    // the official model asked for has a part with no official copy left: the install's own repair message, not
    // awaited (the left side's load has a time limit)
    if (err?.code === "original_unavailable") void overwrittenMessage(v, "model");
    else if (err?.code === "source_unsupported" && v.previewSources?.model?.startsWith("variant:")
             && !costumeRefusals.has(`${v.key}|${v.previewSources.model}`)) {
      // a costume refused at its first build (saved before the check existed): read the family again, whose preview
      // sources leave it out now, instead of an error
      costumeRefusals.add(`${v.key}|${v.previewSources.model}`);
      costumeState.set(v.key, { ...(costumeState.get(v.key) || {}), [v.previewSources.model.slice(8)]: "refused" });
      void reload();
    } else notify(T("model.failed"), { kind: "error", error: err });
  }
}
const costumeRefusals = new Set();
function hideModel() {
  modelActions = [];
  modelAction = null;
  modelLib?.then((M) => M.hide($("model")));
}

// a still of the lobby for the switch card, framed on the face: lobby.js picks the crop inside the left view, which
// lies inside the lobby's painted area, so the still is opaque everywhere and the card's mask fades it out softly
function snapshotLobby() {
  const canvases = [...$("lobby").querySelectorAll(".lobby-view .lobby-layer:not(.lobby-off) canvas")];
  if (!canvases.length) return null;
  const W = canvases[0].width, H = canvases[0].height;
  const r = Lobby.stillRect(W, H) || { x: W * 0.1, y: H * 0.16, w: W * 0.5, h: W * 0.5 * (253 / 450) };
  const c = document.createElement("canvas");
  c.width = 900; c.height = Math.round(900 * (253 / 450));
  const g = c.getContext("2d");
  g.imageSmoothingQuality = "high";
  for (const src of canvases) g.drawImage(src, r.x, r.y, r.w, r.h, 0, 0, c.width, c.height);
  return new Promise((resolve) => c.toBlob((b) => resolve(b ? URL.createObjectURL(b) : null)));
}

// fullscreen memorial lobby: every UI part slides out its own way; the exit button hides when the pointer rests
let idleTimer = null;
const wake = () => {
  const exit = $("fs-exit");
  exit.classList.remove("idle");
  clearTimeout(idleTimer);
  idleTimer = setTimeout(() => exit.classList.add("idle"), 2500);
};
// in fullscreen the character can be touched and the view zoomed like in the game; leaving resets the zoom. The
// portrait's fullscreen is the game's visual mode (visual-mode.js), with the game's own buttons instead of fs-exit.
// Leaving puts every part back whatever the tab is by now (an in-place show may have changed it); setStage(false)
// closes the visual mode.
let fullscreenRequest = 0;
async function setFullscreen(on, { switching = false } = {}) {
  const request = ++fullscreenRequest, part = tab, key = variantKey;
  if (on && tab === "portrait") { return enterVisual({ switching }); }
  if (on) {
    const layout = await (layouts.visual ||= NGUI.layout("UICharacterVisualMode").catch(() => null));
    if (layout) await Visual.exitControl($("fs-exit"), layout);
    const control = tab === "lobby" && previewAvailable(variant(), "spr") ? await Visual.prepareSwitch({
      layout, label: T("row.switch.portrait"), onSwitch: switchFullscreenView,
    }) : null;
    if (request !== fullscreenRequest || !open || tab !== part || variantKey !== key) return;
    $("visual-ui").replaceChildren(...(control ? [control] : []));
    $("visual-ui").hidden = !control;
  } else if (part !== "portrait") {
    $("visual-ui").hidden = true;
    $("visual-ui").replaceChildren();
  }
  setStage(on);
  if (!on || tab === "lobby") { Lobby.frame(on ? "full" : "left"); Lobby.interactive(on); }
  if (!on || tab === "model") modelLib?.then((M) => M.frame(on ? "full" : "left"));
  showExit(on);
  await Photo.visualEntry(on);
  if (!on) { await photoBridge.release(); stillAfterFullscreen(); }
}
// the UI slides out (app.css .stage.fullscreen) and out of the keyboard's reach; back, the visual mode closes and the
// focus returns (visual-mode.js uiOut, uiBack)
const slidOut = () => [$("ngui"), $("ngui-chips"), $("ngui-expr"), document.querySelector(".topbar"), stage().querySelector(".nav")];
function setStage(on) {
  fullscreen = on;
  stage().classList.toggle("fullscreen", on);
  if (on) Visual.uiOut(slidOut());
  else Visual.uiBack(slidOut(), { refocus: open, fallback: () => [...$("ngui").querySelectorAll("button")]
    .find((button) => button.getAttribute("aria-label") === T("lobby.fullscreen")) });
}
function showExit(on) {
  $("fs-exit").hidden = !on;
  if (on) { wake(); stage().addEventListener("pointermove", wake); } else { stage().removeEventListener("pointermove", wake); clearTimeout(idleTimer); }
}
// the visual mode: its background and buttons are made off screen first, then the UI slides out as the portrait
// glides into the whole art and they come in. Without the prefab (another game version) the portrait still goes
// fullscreen, with the tool's exit button.
let visualToken = 0;
async function enterVisual({ switching = false } = {}) {
  if ((fullscreen && !switching) || !mainPlayer?.skeleton) return; // the portrait is not in yet
  const my = ++visualToken, key = variantKey, request = fullscreenRequest;
  const alive = () => my === visualToken && request === fullscreenRequest && open && fullscreen === switching
    && tab === "portrait" && variantKey === key && !!mainPlayer?.skeleton;
  const layout = await (layouts.visual ||= NGUI.layout("UICharacterVisualMode").catch(() => null));
  const strings = { reset: T("visual.reset"), back: T("lobby.exit"),
    switch: previewAvailable(variant(), "lobby") ? T("row.switch.lobby") : null };
  const parts = await Visual.prepare({ layout, bg: variant().bg, strings }).catch((err) => { console.error(err); return null; });
  if (!alive()) return;
  if (!(await Visual.open({
    instant: switching,
    parts, skeleton: mainPlayer.skeleton, detail: detailViewport(), host: stage(), bgHost: $("visual-bg"), uiHost: $("visual-ui"),
    players: () => [...$("portrait").querySelectorAll(".layer")].map((l) => l.player).filter(Boolean),
    onBack: () => setFullscreen(false),
    onSwitch: switchFullscreenView,
    alive,
  }))) return;
  if (!alive()) return;
  setStage(true);
  await Photo.visualEntry(true);
  showExit(!parts);
}

// Reuse the detail preview loaders and source selection while keeping the stage fullscreen. These paths only
// read previews; the selected skin, expression, mod toggle and configured sources remain owned by the detail page.
let fullscreenSwitching = false;
async function switchFullscreenView() {
  if (!fullscreen || fullscreenSwitching || busy || Loading.held() || photoSession || !["portrait", "lobby"].includes(tab)) return;
  const target = tab === "portrait" ? "lobby" : "portrait", key = variantKey;
  if (!previewAvailable(variant(), switchKindOf(target))) return;
  const request = ++fullscreenRequest;
  const alive = () => request === fullscreenRequest && open && fullscreen && variantKey === key;
  fullscreenSwitching = true;
  try {
    const ready = await changeInPlace(async () => {
      if (!alive()) return;
      // Retire the old renderer without writing the detail viewport during an immersive handoff.
      Visual.close({ instant: true, restore: false });
      $("visual-ui").hidden = true;
      $("visual-ui").replaceChildren();
      showExit(false);
      tab = target;
      await Promise.all([renderAll(), renderLeft()]);
      if (alive()) await setFullscreen(true, { switching: true });
    });
    if (ready && open && fullscreen && variantKey === key && tab === target) {
      [...$("visual-ui").querySelectorAll("button")].find((button) => button.dataset.visualAction === "Btn_HideUI")?.focus({ preventScroll: true });
    }
  } finally { fullscreenSwitching = false; }
}
// the switch card's still is taken in the left framing: when fullscreen was entered before the lobby or the model was
// in, the still is taken once the left framing is back
async function stillAfterFullscreen() {
  const part = tab, key = variantKey;
  const wanted = () => open && !fullscreen && tab === part && variantKey === key && (part === "lobby"
    ? !lobbyShot && !!Lobby.meta() : part === "model" && !modelShot && !!$("model").querySelector(".model-view.ready"));
  if (!wanted()) return;
  // the lobby draws the left view within a few frames (after its intro); the model glides there
  const M = part === "model" ? await ModelLib() : null;
  if (!M) await Lobby.introDone();
  await (M ? M.settled() : frames(4));
  if (!wanted()) return;
  if (M) modelShot = M.snapshot();
  else lobbyShot = snapshotLobby();
  if (M ? modelShot : lobbyShot) renderScreen();
}

async function reload(refresh = []) {
  const read = fam;
  const data = await call(() => readFamily(read.key));
  if (data && fam !== read) return; // the screen was read anew meanwhile
  const next = data?.variants.find((v) => v.key === variantKey) || data?.variants[0];
  const changed = new Set(refresh);
  if (data) for (const kind of ["spr", "lobby", "model"]) if (previewChanged(variant(), next, kind, previewEnabled, modsOn())) changed.add(kind);
  if (data) fam = data;
  for (const kind of changed) resetPreview(kind);
  await Promise.all([renderAll(), changed.has(tabKind()) ? renderLeft() : null]);
}

// A source changed or was rebuilt: discard the cached player or still, also when only a saved configuration changed.
function resetPreview(kind) {
  if (kind === "lobby") {
    lobbyStale = true;
    lobbyShot = null;
    lobbySeq = null;
    lobbyAll = false;
    if (shownLeft === "lobby") shownKey = null;
  } else if (kind === "model") {
    modelShot = null;
    modelGeneration++;
    if (shownLeft === "model") shownKey = null;
  } else {
    expression = "00";
    clearTimeout(blinkTimer);
    mainPlayer = null;
  }
}

// --- game files switched elsewhere: the Enable Mods switch, the tray, a queued write, the classic UI closing
// (library.changed, main.js). The family is read again, and the left side reloads the way an applied portrait or
// lobby does when the shown variant's files of that kind changed. It waits while a popup or a fullscreen view is
// open, while the screen changes and while this screen's own job runs (whose reload comes first). ---
let heldChange; // undefined: nothing waiting; null: anything may have changed; {cores}: those lower-case cores
let reloadingChanged = null;
const mergeChange = (a, b) => (a === null || b === null ? null : a === undefined ? b : { cores: [...new Set([...a.cores, ...b.cores])] });
const lower = (core) => String(core || "").toLowerCase();
// a variant's game files of a kind as the screen judged them: they changed when this differs
const filesSig = (v, kind) => JSON.stringify(v ? [v.configured?.[kind], v.selection?.[kind], partsOf(v, kind).map((f) => [f.file, f.applied, f.crc, f.size, f.date, f.mtime, f.official])] : null);
const quiet = () => !fullscreen && !Visual.isOpen() && !popupOpen() && !changing() && !Loading.covering();

// filesChanged({cores} | null): resolves after queued changes have refreshed and drawn, or the screen is left
export function filesChanged(change = null) {
  if (!open || !fam) return;
  if (change) {
    const cores = new Set(change.cores.map(lower));
    if (!fam.variants.some((v) => [v.spr, v.lobby, v.model, ...(v.modelCores || [])]
      .some((core) => cores.has(lower(core))))) return;
  }
  heldChange = mergeChange(heldChange, change && { cores: change.cores.map(lower) });
  return resumeChanged();
}
function resumeChanged() {
  if (reloadingChanged) return reloadingChanged;
  if (heldChange === undefined || !open) return;
  reloadingChanged = (async () => {
    while (open && heldChange !== undefined) {
      while (open && (busy || !quiet())) await sleep(250);
      if (!open) return;
      await reloadChanged();
    }
  })().catch((err) => console.error("reload after a switch", err)).finally(() => {
    reloadingChanged = null;
    resumeChanged();
  });
  return reloadingChanged;
}
async function reloadChanged() {
  while (open && !quiet()) await sleep(250);
  if (!open || busy || heldChange === undefined) return;
  const change = heldChange;
  heldChange = undefined;
  const read = fam;
  const data = await readFamily(read.key).catch(renderFailed);
  if (!data || !open || fam?.key !== read.key || busy) return;
  if (fam !== read) { heldChange = mergeChange(heldChange, change); return; } // read anew meanwhile: read again after it
  // A repacked file can retain its size, date and family fields. A source event still revalidates its manifest.
  const lobbyChanged = change === null || change.cores.includes(lower(variant().lobby));
  const modelChanged = change === null || [variant().model, ...(variant().modelCores || [])]
    .some((core) => change.cores.includes(lower(core)));
  if (JSON.stringify(data) === JSON.stringify(fam) && previewEnabled === modsOn() && !lobbyChanged && !modelChanged) return;
  // the thumbnails of every core of the family whose files or configured preview changed
  const changed = { spr: false, lobby: lobbyChanged, model: modelChanged };
  for (const v of data.variants) {
    const was = fam.variants.find((x) => x.key === v.key);
    for (const kind of ["spr", "lobby", "model"]) {
      if (filesSig(was, kind) === filesSig(v, kind) && !previewChanged(was, v, kind, previewEnabled, modsOn())) continue;
      const core = coreOf(v, kind);
      if (core) { forget(spinePrefix(kind, core)); forget(spinePrefix(`official-${kind}`, core)); }
      if (v.key === variantKey) changed[kind] = true;
    }
  }
  ctx.invalidateRoster();
  for (const kind of ["spr", "lobby", "model"]) if (changed[kind]) resetPreview(kind);
  const left = changed[tabKind()];
  await changeInPlace(async () => {
    fam = data;
    await Promise.all([renderAll(), left ? renderLeft() : null]);
  });
}

async function ensureLayouts() {
  if (layouts) return;
  const detail = await withTimeout(NGUI.layout("UICharacterDetail"), HARD_CAP, "Detail layout");
  const filter = await withTimeout(NGUI.layout("UIPopup_FilterSort"), HARD_CAP, "Disabled button layout");
  await NGUI.init().catch(renderFailed);
  layouts = { list: find(detail, "Inventory_ListControl"), textures: NGUI.textureIndex(), visual: null,
    disabled: find(filter, "Center/Popup/Btn_Section/Btn_Disable") };
}

// the voice volume and mute (development runs started with --mute stay silent), the zoom-out limit and the
// post-processing from the settings
export function applyPrefs(prefs) {
  Lobby.setVolume((prefs?.voiceVolume ?? 80) / 100, ctx?.info?.mute || prefs?.voiceMuted);
  Lobby.setZoomLimit(prefs?.lobbyZoom);
  Lobby.setPostProcessing(prefs?.lobbyPost !== false);
  Lobby.setIntro(prefs?.lobbyIntro !== false);
  // started with --debug: the fullscreen lobby's pointer, wheel and gesture events go to the app's log
  Lobby.setTrace(ctx?.info?.debug ? (events) => rpc("debug.trace", { events }).catch(() => {}) : null);
}

// Locale updates redraw controls around the retained scene. Do not call show,
// renderLeft or begin here: they reset expressions, playback or capture scopes.
export async function relabel() {
  if (!open || !fam || !layouts) return;
  const work = [renderScreen(), renderChips(), renderExpressions()];
  if (tab === "lobby" && Lobby.meta()) {
    // Network-backed dialogue is optional; it must not delay language selection.
    refreshLobbyLines(variant(), Lobby.meta());
  }
  if (switchThumbnails && switchPopupPage === pageToken && !$("popup").hidden && !$("popup").querySelector(".alias-input")) {
    const scroll = $("popup").querySelector(".popup-cards");
    work.push(renderPopup(popupKind === "spr" ? scroll?.scrollLeft || 0 : scroll?.scrollTop || 0));
  }
  if (pickerSel && !$("picker").hidden) work.push(openPicker($("picker").querySelector(".popup-cards")?.scrollTop || 0));
  await Promise.all(work);
}

// --- the previous and next student from the keyboard: the PC client's keys for the arrows (step-keys.js) ---
// A key steps exactly like its arrow (ctx.step: the roster's cards in its order), one step at a time: the next one
// waits until the screen shows the student the last one went to (arrived), so a held key goes at the pace the screen
// follows and starts no load it would drop. The keys are the screen's own: not while a text field or an input method
// has them, under a popup (the screen's, the settings, the consent or update dialog), in a fullscreen view (the lobby,
// the 3D model, the portrait's visual mode), during a job or another change of the screen, or behind the loading screen.
const blocked = () => !open || !fam || fullscreen || Visual.isOpen() || busy || Loading.held() || Loading.covering() || $("screen-detail").hidden
  || !!document.querySelector(".popup-host:not([hidden]), .consent-host:not([hidden])");
const changing = () => !!batch; // an in-place change is being prepared (a step, a chip, a tab, an applied portrait)
// the address, as main.js routes it: what the arrows asked for
function address() {
  try { return location.hash.replace(/^#\/?/, "").split("/").map(decodeURIComponent); } catch { return []; }
}
let arrivals = [], askedKey = null; // askedKey: the variant the last show() was asked for, as in the address
// the screen is ready on a student (or was left: no arguments): a step waiting for the address to be shown goes on
function arrived(family, key) {
  if (!arrivals.length) return;
  const [screen, f, k] = address();
  if (family !== undefined && (screen !== "student" || f !== family || k !== key)) return; // a newer address comes
  for (const resolve of arrivals.splice(0)) resolve();
}
const keySteps = pacer(async (delta) => {
  if (blocked() || changing()) return;
  const from = location.hash;
  let resolve;
  const shown = new Promise((r) => { resolve = r; arrivals.push(r); });
  try {
    await ctx.step(delta, fam.key, variantKey);
  } finally {
    // nowhere else to go (or the step failed): nothing to wait for
    if (location.hash === from) resolve();
  }
  let timer;
  await Promise.race([shown, new Promise((r) => { timer = setTimeout(r, HARD_CAP); })]); // a screen that never comes
  clearTimeout(timer);
  arrivals = arrivals.filter((r) => r !== resolve);
});
// Escape leaves fullscreen only when no popup took it or is open over the view (Escape belongs to the top layer)
addEventListener("keydown", (ev) => {
  if (ev.key === "Escape" && !ev.defaultPrevented && !popupOpen() && open && fullscreen && !Visual.isOpen() && !photoSession) {
    ev.preventDefault();
    void setFullscreen(false);
    return;
  }
  const delta = deltaOf(ev);
  if (!delta || typing(ev.target) || blocked() || (changing() && !keySteps.busy)) return;
  ev.preventDefault(); // the page's own use of the arrows (scrolling the unfolded strip) gives way to the step
  keySteps.press(delta, ev.repeat);
});
addEventListener("keyup", (ev) => { if (KEYS[ev.key]) keySteps.release(KEYS[ev.key]); });
addEventListener("blur", () => keySteps.release());

// show(context, familyData, variantKey, {inPlace}): open the detail screen on a variant; resolves once the screen is
// ready (the panels, primary preview and decoded UI images). In place (another
// variant or family while the screen is shown), the shown screen stays until then and every part swaps in one frame.
export async function show(context, familyData, key, { inPlace = false, kind = null } = {}) {
  Photo.destroy();
  ModAlias.close();
  leavePage();
  pageToken += 1;
  leftPage = new Promise((resolve) => { leavePage = resolve; });
  ctx = context;
  applyPrefs(ctx.prefs);
  open = true;
  suspended = false;
  askedKey = key;
  const g = begin();
  const sameFamily = fam?.key === familyData.key;
  // the alias editor of another variant goes (a history step while it was open)
  if ((!sameFamily || variantKey !== key) && $("popup").querySelector(".alias-input")) { aliasToken += 1; $("popup").hidden = true; $("popup").innerHTML = ""; }
  // the same variant of another install (another game folder or file source) is read again like another variant
  const sameVariant = sameFamily && variantKey === key && fam.version === familyData.version;
  fam = familyData;
  variantKey = fam.variants.some((v) => v.key === key && hasAsset(v)) ? key : (fam.variants.find(hasAsset) || fam.variants[0]).key;
  if (!sameVariant) {
    expression = "00";
    expanded = false;
    lobbyShot = null;
    lobbySeq = null;
    lobbyAll = false;
    modelShot = null;
    modelActions = [];
    modelAction = null;
    // a new portrait is loaded; the shown one stays until it is committed
    clearTimeout(blinkTimer);
    mainPlayer = null;
  }
  // the tabs the variant has, on every show: another install may lack a part under the same variant
  tab = availableTab(variant(), ({ spr: "portrait", portrait: "portrait", lobby: "lobby", model: "model" })[kind] || tab);
  previewEnabled = modsOn();
  rpc("ui.visit", { variant: variantKey }).catch(() => {});
  // The portrait uses its own assets and default framing while the UI layout is still loading or unavailable.
  const ready = await prepare(g, true, async () => {
    commit(() => setTitle(T("top.students"), ctx.back || (() => { location.hash = "#/roster"; })));
    await Promise.all([renderLeft(), ensureLayouts().then(() => {
      if (g.gen === showGen) return renderAll();
    }).catch(renderFailed)]);
    await settleImages();
  });
  if (ready) arrived(familyData.key, key);
  // the server's index of the lobbies' prefabs takes seconds to build the first time: built in the background once
  // the screen is in, so the lobby tab does not wait for it
  if (open && hasLobby(variant())) rpc("lobby.warm").catch(() => {});
}

// a route to a student failed (main.js): a key step waiting for that screen goes on at once
export function stepFailed() { arrived(); }

// The variant actually shown, including the fallback for a family-only or unavailable-variant route.
export const currentVariant = () => (open || suspended) && fam ? variant().key : null;

// Release page work before a destination waits on library locks, retaining the
// complete current frame until that destination can replace it.
export function suspend() {
  Photo.destroy();
  switchThumbnails?.cancel();
  switchThumbnails = null;
  ModAlias.close(); leavePage(); open = false; suspended = true;
  begin(); batch = null; leftToken += 1; spineToken += 1;
}

// leaving the screen: stop the players, drop any change being prepared
export function hide() {
  Photo.destroy();
  switchThumbnails?.cancel();
  switchThumbnails = null;
  ModAlias.close();
  leavePage();
  pageToken += 1;
  open = false;
  suspended = false;
  switchPopupPage = null;
  begin();
  batch = null;
  leftToken += 1;
  visualToken += 1;
  Visual.close({ instant: true });
  if (fullscreen) setFullscreen(false);
  disposePortrait();
  Lobby.hide($("lobby"));
  lobbySeq = null;
  lobbyAll = false;
  hideModel();
  unfreezeLeft(true);
  shownLeft = shownKey = null;
  for (const part of LEFT) { $(part).hidden = true; $(part).classList.remove("pending", "leaving"); }
  for (const id of ["popup", "picker"]) { $(id).hidden = true; $(id).innerHTML = ""; }
  keySteps.clear();
  heldChange = undefined; // the next show reads the family anyway
  arrived();
}

$("fs-exit")?.addEventListener("click", () => setFullscreen(false));

// Student photography retains both real renderers during in-place camera mode changes.
let photoLastAction="",photoPrepared=null,photoSession=false,photoOrigin=null;
const photoFaces=new Map();
async function photoPreload(){
  if(photoPrepared)return photoPrepared;
  photoOrigin=variantKey;photoSession=true;
  photoPrepared=(async()=>{
    const v=variant(),portraitAvailable=previewAvailable(v,'spr'),lobbyAvailable=previewAvailable(v,'lobby'),portrait=$("portrait"),lobby=$("lobby");
    if(!mainPlayer){portrait.hidden=false;portrait.classList.add('photo-preloading');}
    if(tab!=='lobby'){lobby.hidden=false;lobby.classList.add('photo-preloading');}
    await Promise.all([
      (async()=>{if(portraitAvailable&&!mainPlayer)await loadSpine(currentBase());if(portraitAvailable&&!mainPlayer?.skeleton)throw new Error('Portrait renderer is not ready');})(),
      (async()=>{
        if(!lobbyAvailable)return;
        const c=current('lobby'),options={framing:specialFraming(v),deferPlayback:true,
          mod:c.kind==='mod'&&(!!v.previewSources?.lobby||!!v.configured||!modsOn()||!v.lobby)?c.id:null,
          official:c.kind==='self'&&(!!v.configured||!!v.previewSources?.lobby||!modsOn())};
        if(!await Lobby.show(v.lobby,lobby,v.key,options))throw new Error('Memorial renderer is not ready');
        lobbyFrom=fam.version;lobbyStale=false;Lobby.suspend();Lobby.frame('full');Lobby.photography(Photo.FRAME);Lobby.skipIntro();
        await Lobby.prepareFrame();
      })(),
    ]);
    if(photoOrigin!==variantKey||!open)throw new DOMException('Photography source changed','AbortError');
    if(portraitAvailable&&!Visual.isOpen()){
      const layout=await(layouts.visual ||= NGUI.layout('UICharacterVisualMode'));
      const parts=await Visual.prepare({layout,bg:v.bg,strings:{reset:T('visual.reset'),back:T('lobby.exit')}});
      await Visual.open({parts,skeleton:mainPlayer.skeleton,detail:detailViewport(),host:stage(),bgHost:$("visual-bg"),uiHost:$("visual-ui"),
        players:()=>[...portrait.querySelectorAll('.layer')].map(layer=>layer.player).filter(Boolean),onBack:()=>setFullscreen(false),alive:()=>open&&photoOrigin===variantKey});
    }
    if(portraitAvailable){Visual.photography(true,Photo.FRAME);Visual.captureReady();mainPlayer.drawFrame?.(false);}
  })().catch(error=>{photoPrepared=null;throw error;});
  return photoPrepared;
}
function photoPortraitClock(on){
  for(const layer of $("portrait").querySelectorAll('.layer')){
    const p=layer.player;if(!p)continue;p.paused=!on;
    if(on){p.drawFrame?.(false);if(p.stopRequestAnimationFrame)p.startRendering?.();}
    else p.stopRendering?.();
  }
}
function photoVisibility(mode){
  const portrait=mode==='portrait';
  for(const id of ['portrait','visual-bg','visual-ui'])$(id).style.visibility=portrait?'visible':'hidden';
  $("portrait").hidden=!portrait;$("lobby").hidden=portrait;
  $("lobby").style.visibility=portrait?'hidden':'visible';
  for(const id of ['portrait','lobby'])$(id).classList.remove('photo-preloading','pending','leaving');
  document.documentElement.classList.toggle('photo-lobby',!portrait);
}
const photoBridge={
  info:()=>({variant:variantKey,mode:tab,source:tab==='portrait'?currentBase():shownSource(variant(),'lobby'),hasPortrait:previewAvailable(variant(),'spr'),hasLobby:previewAvailable(variant(),'lobby')}),
  async full(mode){
    await photoPreload();
    if(mode==='portrait'){
      Visual.photography(true,Photo.FRAME);mainPlayer.paused=false;mainPlayer.drawFrame?.(false);
    }else await Lobby.prepareFrame();
    await nextFrame();
    tab=mode;fullscreen=true;stage().classList.add('fullscreen');Visual.uiOut(slidOut());
    if(mode==='portrait'){Lobby.suspend();photoVisibility(mode);photoPortraitClock(true);}
    else{photoPortraitClock(false);photoVisibility(mode);Lobby.resume();Lobby.interactive(false);}
  },
  async leave(){
    Lobby.suspend();
    shownLeft=tab;shownKey=leftKey(tab,variant());
    await setFullscreen(false);
    await renderAll();await settleImages();
    if(tab==='lobby')await Lobby.prepareFrame();
    await nextFrame();
  },
  async release(){
    if(!photoSession)return;photoSession=false;photoPrepared=null;photoOrigin=null;
    Visual.close({instant:true});Lobby.photography(null);
    for(const id of ['portrait','lobby','visual-bg','visual-ui']){$(id).style.visibility='';$(id).classList.remove('photo-preloading');}
    if(tab==='portrait'){Lobby.hide($("lobby"));$("lobby").hidden=true;photoPortraitClock(true);}
    else{disposePortrait();$("portrait").hidden=true;Lobby.resume();}
    photoFaces.clear();document.documentElement.classList.remove('photo-lobby');
  },
  cancel(){
    const owned=photoSession;
    void photoBridge.release();
    if(owned&&fullscreen){setStage(false);showExit(false);Lobby.frame('left');Lobby.interactive(false);}
  },
  generatedThumbnail:async()=>variant().spr?await thumbnailWork.thumb(currentBase(),'roster'):variant().portrait,
  defaultThumbnail:async()=>variant().portrait||(variant().spr?await thumb(spineBase('official-spr',variant().spr),'roster'):null),
  thumbnailChanged(){ctx?.invalidateRoster();},
  playbackTree(paused){
    // layouts.list already starts at Inventory_ListControl.
    const source=find(layouts.list,'LeftInfo/CharInfo/Char_Target/Char_ZoomBtn');
    if(!source)throw new Error('Memorial playback control is unavailable');
    const tree=clone(source);
    tree.name='PhotoPlayback';tree.pos=[0,0];
    const icon=tree.children[0].widgets[0];
    if(paused){delete icon.texture;Object.assign(icon,{sprite:'Common_Icon_Play',w_:64,h_:64,color:'#2b496e/1.00'});}
    else{delete icon.sprite;Object.assign(icon,{texture:'svg:stop',w_:54,h_:54,color:'#2b496e/1.00'});}
    return {tree,textures:TEXTURES};
  },
  expressions:()=>(mainPlayer?.skeleton?.data.animations||[]).map(a=>a.name).filter(name=>/^\d{2}$/.test(name)),
  expressionPreview(key){
    if(!photoFaces.has(key))photoFaces.set(key,thumbnailWork.thumb(currentBase(),'face',key));
    return photoFaces.get(key);
  },
  async prepareExpressions(){await Promise.all(photoBridge.expressions().map(key=>photoBridge.expressionPreview(key)));},
  expressionCard(key,preview){const tree=stripCard(`Expression${key}`,0,{thumb:preview,selected:key===expression});tree.pos=[0,0];return tree;},
  expression:()=>expression,
  action:()=>Lobby.playing()||photoLastAction,
  setExpression(value){expression=value;applyExpression();},
  actions:()=>[...(Lobby.meta()?.sequences||[]),...[...new Set(Object.values(Lobby.meta()?.idles||{}).flat())].map(anim=>({anim}))].filter((item,index,list)=>list.findIndex(other=>other.anim===item.anim)===index),
  play(value){photoLastAction=value;Lobby.resume();if((Lobby.meta()?.sequences||[]).some(seq=>seq.anim===value))Lobby.play(value);
    else{Lobby.skipIntro();Lobby.stop();for(const p of Lobby.layerPlayers())if(p.skeleton?.data.findAnimation(value))p.animationState.setAnimation(0,value,true);}},
  pause(on){if(tab==='lobby'){if(on)Lobby.suspend();else{Lobby.resume();Lobby.interactive(false);}}else if(mainPlayer)mainPlayer.paused=on;},
  async redraw(){
    if(tab==='lobby'){if(await Lobby.prepareFrame())return;}
    else if(mainPlayer?.skeleton){
      if(mainPlayer.error)throw new Error('Portrait renderer failed');
      if(!mainPlayer.disposed){mainPlayer.paused=true;mainPlayer.drawFrame(false);return;}
    }
    throw Object.assign(new Error('Renderer is not ready'),{code:'capture_not_ready'});
  },
  pan(dx,dy){if(tab==='portrait')Visual.panCapture(dx,dy);else Lobby.panBy(dx,dy);},
  zoom(f,x,y){if(tab==='portrait')Visual.zoomCapture(f,x,y);else Lobby.zoomAt(f,x,y);},
  paintRoots:()=>tab==='portrait'?[$("visual-bg"),$("portrait")]:[$("lobby")],
};
