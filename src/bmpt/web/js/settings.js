// Settings, on the game's PC options popup (UIPopup_Option_PC): tabs on the left, the white panel on the right with
// the game's option rows: radio rows (UIPopup_Option_Toggle), the sound slider row (UIPopup_Option_SoundToggle), and
// folder rows built from the same pieces (title with its tag, dotted line, the value, the popup's light-blue buttons).
// The Mods tab (second, after General) holds the Enable Mods switch with its square "?", where the switch shows, the
// startup file checks, the tray icon, Settings' Check of the game files, plus the size and cleanup of the mod cache.
// Whether the classic UI asks before it opens is a General row,
// under More Settings.
import { notify } from "./popup.js";
import * as ModSwitch from "./mod-switch.js";
import * as NGUI from "./ngui.js";
import * as Popup from "./popup.js";
import * as Prompts from "./prompts.js";
import * as Impact from "./update-impact.js";
import * as Consent from "./consent.js";
import * as OtherMods from "./settings-other.js";
import * as ModExport from "./mod-export.js";
import * as Tours from "./tours.js";
import * as Update from "./update.js";
import * as Buttons from "./game-buttons.js";
import { BACKDROP, SMALL_BUTTON, TYPE } from "./ui-tokens.js";
import { rowBase, radioRow, valueGeometry, valueRoom, afterTitle, buttonRowGeometry, stackCards, noteFit, noteNode } from "./settings-rows.js";
import { on, rpc } from "./api.js";
import { T, errorText, platform } from "./i18n.js";
import { $, DOT, S, clone, glyphsOf, hitButton, wrapPath } from "./util.js";

const { node, sprite, label, find, rect } = NGUI;
const fitText = (...a) => NGUI.fitText(...a);
// UIPopup_Option_PC's tab column has five separators (TabBg/Grid Line_1 to Line_5)
const TABS = ["general", "mods", "sound", "l2d", "folders"];
const CONTENT = { x: 209, top: 375, w: 1576, h: 854 }; // the panel's inner list area (List/BG), units

let layout = null, prefs = null, tab = "general", scroll = 0, onChange = null, token = 0;
let checkingUpdate = false;
let otherMods = null, otherRequest = 0;

async function call(fn) {
  try { return await fn(); } catch (err) {
    if (err.code === "game_directory_missing" && ["global", "japan"].includes(err.params?.region)) {
      // The game's notice caption, also used by Quit; UIPopup_Sort supplies the frame and lone Confirm button.
      await Popup.notice(T(`settings.gameDirMissing.${err.params.region}`));
    } else notify(errorText(err), { kind: "error", error: err });
    return null;
  }
}

// --- rows ---

// the popup's light-blue button (ETCSetting/ButtonBg), smaller
const buttonNode = (...args) => Buttons.smallButton(...args);

// the On / Off options of the Mods tab's rows (the game's words, settings.post.*)
const ON_OFF = () => [[true, T("settings.post.on")], [false, T("settings.post.off")]];

// the square "?" after a row's title (UICharacterCollection's Btn_Information: the game's Common_Btn_Information in
// the title's navy over a white square), with the red dot when entries were not switched; it opens the Enable Mods
// help unless ``open`` names another
function helpAfterTitle(row, title, dot, open = () => ModSwitch.openHelp()) {
  const t = fitText(title, 50, 40, 900);
  const { pos } = afterTitle(row.h, glyphsOf(t.text, t.fs), { width: 50, height: 50 });
  row.tree.children.push(node("Help", pos, [sprite("Common_Square_Bg", 50, 50, { depth: 22, color: "#ffffff/1.00" })], [
    node("Sprite", [2, -7], [sprite("Common_Btn_Information", 70, 72, { type: 0, depth: 23, color: "#2c4564/1.00" })]),
    ...(dot ? [DOT([40, 30], 30)] : []),
  ]));
  row.hits.push({ path: `${row.tree.name}/Help`, fn: open, name: T("mods.guide"), pad: 14 });
  return row;
}

// the Configuration Drift Check row's value: the file check off, or what the last check found
function filesValue(m) {
  if (m.scan !== true) return T("error.scan_off");
  const parts = [];
  if (m.drift) parts.push(T("tray.drift", { n: m.drift }));
  if (m.untracked) parts.push(T("mods.untrackedTitle"));
  if (m.damaged) parts.push(T("mods.damaged"));
  return parts.length ? parts.join(ModSwitch.listSep()) : T("settings.mods.clean");
}

// the Configuration Drift Check row's "?": what configuration drift is
export function driftLines() {
  return [T("settings.mods.driftHelp.what")];
}

function driftHelp() {
  return Popup.message({ title: T("settings.mods.driftHelp.title"), lines: driftLines(), ok: T("picker.ok"), width: 2100 });
}

// Settings' Check: ask for missing consent, then scan with the dismissed entries listed again and show the drift
// and untracked prompts over the settings
async function checkFiles() {
  if (ModSwitch.current()?.scan !== true && !await ModSwitch.askScan()) return;
  const result = await ModSwitch.check();
  if (!result) return;
  const m = ModSwitch.current() || {};
  render();
  if (!m.drift && !m.untracked && !m.damaged) return;
  const entries = await rpc("mods.entries").catch(() => []);
  await Prompts.show("drift", { entries, dismissed: true });
  // entries a game update put back: the update summary's
  if (m.enabled !== false && entries.some((e) => e?.state === "updated")) await Impact.show();
  await Prompts.show("untracked", { entries, dismissed: true });
  render();
}

// --- a value with buttons on the right (folders, version, cache); long values wrap, never cut ---
// The geometry (the content box, the buttons right-aligned SPACE.m apart, the value's room, the wrapping of a second line of
// buttons) is settings-rows.js valueGeometry. The value is a description: TYPE.caption, in the label box NGUI gives a line.
// note: a line under the dotted line, above the value and the buttons (settings-rows.js NOTE, the option's red note).
const LABEL_BOX = 1.3;
function valueRow(name, y, title, value, buttons, { below = false, showValue = true, note = "" } = {}) {
  const f = wrapPath(value || T("settings.notSet"), TYPE.caption.fs, TYPE.caption.min, valueRoom(buttons.length, below), 3);
  const n = note ? noteFit(note) : null, noteH = n ? n.fs * LABEL_BOX * n.lines : 0;
  const lineH = f.fs * LABEL_BOX, layout = valueGeometry(buttons.length, lineH * f.lines, { below, noteH });
  const { h } = layout, row = rowBase(name, y, h, title), hits = [];
  if (n) row.children.push(noteNode(n, layout.note, layout.noteBox.w, noteH));
  // Action-only rows keep the same spacing without a value label.
  if (showValue) row.children.push(node("Value", layout.value, [label(f.text, f.fs, "#2d4664/1.00", layout.room, lineH * f.lines + 10, { pivot: 3, depth: 8 })]));
  // buttons side by side share one caption size (the small button's range, 48 down to 34)
  const size = buttons.length > 1 ? NGUI.fitGroup(buttons.map(([caption]) => caption), SMALL_BUTTON.fs, SMALL_BUTTON.fsMin,
    SMALL_BUTTON.w - SMALL_BUTTON.fitInset, 2, { maxH: SMALL_BUTTON.h - SMALL_BUTTON.boxInsetH })[0].fs : null;
  let at = 0;
  layout.lines.forEach((count, line) => {
    for (let i = 0; i < count; i++, at++) {
      const [caption, fn, enabled = true] = buttons[at];
      row.children.push(buttonNode(`Btn_${at}`, layout.xs[at], layout.ys[line], caption, { enabled, ...(size ? { fs: size, min: size } : {}) }));
      if (enabled) hits.push({ path: `${name}/Btn_${at}`, fn, name: caption });
    }
  });
  return { tree: row, h, hits };
}

// the game's slider row (Master/Type_3) with its title (fitted to titleW) and the thumb at f (0 to 1)
function sliderTree(name, y, title, f, titleW = 360) {
  const t = clone(layout.slider);
  t.name = name;
  t.pos = [0, y];
  Object.assign(find(t, "Label_Locale_OptionTitle").widgets[0], { text: title, fontSize: fitText(title, 50, 34, titleW).fs, w_: titleW + 20 });
  find(t, "SlideBg/Thumb").pos[0] = -316 + 632 * f;
  return t;
}

// the sound slider row: mute icon, slider, full icon, the mute check
function sliderRow(name, y, title, value, muted) {
  const t = sliderTree(name, y, title, value / 100);
  const mute = find(t, "Label_Mute");
  Object.assign(mute.widgets[0], { text: T("settings.mute"), fontSize: fitText(T("settings.mute"), 50, 34, 180).fs });
  find(mute, "Icon").active = !!muted;
  return { tree: t, h: 172, hits: [{ path: `${name}/Label_Mute/Bg`, fn: () => set("voiceMuted", !muted), name: T("settings.mute") }],
    slider: { path: `${name}/SlideBg`, name: title, value: value / 100, steps: 100, set: (f) => set("voiceVolume", Math.round(f * 100)) } };
}

// the custom zoom-out limit: the slider row without the sound icons, the value where the mute check is
const ZOOM_CUSTOM = [1, 4]; // game views across (webui/state.py LOBBY_ZOOM_CUSTOM), in 0.1 steps
const factorText = (v) => T("settings.zoomOut.factor", { v: v.toFixed(1) });
function factorRow(name, y, title, value) {
  const [lo, hi] = ZOOM_CUSTOM, f = (value - lo) / (hi - lo), at = (x) => Math.round((lo + x * (hi - lo)) * 10) / 10;
  const t = sliderTree(name, y - 34, title, f, 340); // its body (BG, 34 up) in the middle of the row
  for (const path of ["Icon_Minimum", "Icon_Maximum", "Label_Mute/Bg", "Label_Mute/Icon"]) find(t, path).active = false;
  find(t, "SlideBg").pos[0] += 60; // into the mute icon's place: room for the title
  Object.assign(find(t, "Label_Mute").widgets[0], { text: factorText(value), fontSize: 50 });
  const slider = { path: `${name}/SlideBg`, name: title, label: `${name}/Label_Mute`, value: f, steps: Math.round((hi - lo) * 10),
    text: (x) => factorText(at(x)), set: (x) => set("lobbyZoomCustom", at(x)) };
  return { tree: t, h: 172, hits: [], slider };
}

// --- tabs and frame ---

function frameTree() {
  const t = clone(find(layout.popup, "Center"));
  const popup = find(t, "Popup");
  const keep = new Set(["Base", "TabBg"]);
  const template = find(popup, "Game");
  popup.children = popup.children.filter((c) => keep.has(c.name));
  const titleNode_ = find(popup, "Base/BG/Label_Title_Locale");
  const tf = fitText(T("settings.title"), 72, 52, 1000);
  Object.assign(titleNode_.widgets[0], { text: tf.text, fontSize: tf.fs, w_: 1100 });
  find(titleNode_, "Line").widgets[0].w_ = glyphsOf(tf.text, tf.fs) + 14;
  // the tabs' captions share one size
  const tabFits = NGUI.fitGroup(TABS.map((id) => T(`settings.tab.${id}`)), 56, 40, 300, 2);
  TABS.forEach((id, i) => {
    const n = clone(template);
    n.name = `Tab_${id}`;
    n.pos = [-680, 316 - i * 136.5];
    for (const state of ["Tab_On", "Tab_Off"]) {
      const lbl = find(n, `${state}/Label_Locale`);
      const f = tabFits[i];
      Object.assign(lbl.widgets[0], { text: f.text, fontSize: f.fs, w_: 320 });
      find(n, state).active = (state === "Tab_On") === (id === tab);
    }
    popup.children.push(n);
  });
  // the left column keeps only as many separators as there are tabs
  const grid = find(popup, "TabBg/Grid");
  if (grid) grid.children = grid.children.slice(0, TABS.length);
  find(t, "BlackBg/BlackBgClickArea").widgets.find((w) => "sprite" in w).color = BACKDROP;
  return t;
}

function rows({ builtSize = null, sizeError = null } = {}) {
  const p = prefs, out = [];
  const add = (r) => { out.push(r); };
  const reveal = (path) => () => call(() => rpc("os.reveal", { path }));
  const browse = (key, start) => async () => {
    const path = await call(() => rpc("settings.pickFolder", { start }));
    if (path) return set(key, path);
  };
  const open = T(platform === "mac" ? "open.mac" : "open.win");
  if (tab === "general") {
    // each language named in itself, in its own fonts
    add(radioRow("Language", 0, T("settings.language"), p.languages.map((l) => [l.code, l.name, l.code]), p.language, (v) => set("language", v)));
    add(valueRow("Cache", 0, T("settings.cache.label"), p.consent || p.previewCache ? T("cacheTo", { path: p.cacheDir }) : `${T("settings.cache.off")} - ${p.cacheDir}`, [
      [T("settings.cache.change"), browse("cacheDir", p.cacheDir), p.native],
      [T("settings.cache.clear"), async () => {
        const next = await call(() => rpc("settings.clearCache"));
        if (next) { prefs = next; await onChange?.("cacheCleared", next); await render(); }
      }, true]]));
    add(radioRow("PreviewCache", 0, T("settings.previewCache"), ON_OFF(), p.previewCache === true, (value) => set("previewCache", value)));
    add(valueRow("Spine", 0, T("settings.spine.label"), "", [
      [T("settings.spine.view"), () => call(() => Consent.spine($("consent"), { over: true, readOnly: true }))],
    ], { showValue: false }));
    add(valueRow("Version", 0, T("settings.version"), (p.version || "").replace(/-dev$/, ""), [[T("settings.update.check"), checkUpdate, !checkingUpdate]]));
    add(valueRow("Tours", 0, T("tour.title"), "", [[T("tour.open"), () => call(Tours.browse)]], { showValue: false }));
    if (p.prereleasePreviews === true) add(valueRow("UpdateSimulation", 0, T("update.simulation.entry"), T("update.simulation.choose"),
      [[T("update.notes"), () => simulateUpdate(true)], [T("update.simulation.empty"), () => simulateUpdate(false)]]));
    add(radioRow("StartupUpdate", 0, T("settings.update.startup"), ON_OFF(), p.startupUpdate !== false, (v) => set("startupUpdate", v)));
    add(valueRow("Log", 0, T("settings.log.label"), p.logPath, [
      [T("settings.log.open"), openLog], [open, reveal(p.logPath)],
      [T("recovery.report"), () => globalThis.BAMTRecovery?.report()]], { note: T("settings.log.reportNote") }));
    add(valueRow("Classic", 0, T("settings.classic.label"), T("settings.classic.hint"), [[T("settings.classic.open"), () => call(() => rpc("app.openClassic", { tab: "settings" }))]]));
    // the bottom bar's hand-off prompt (decision 6): Don't Ask Again turns it off, this turns it back on
    add(radioRow("ClassicAsk", 0, T("settings.classic.ask"), [[true, T("settings.post.on")], [false, T("settings.post.off")]],
      p.classicPrompt !== false, (v) => set("classicPrompt", v)));
  } else if (tab === "mods") {
    const m = ModSwitch.current() || {};
    const v = ModSwitch.view(m, { place: "settings" });
    // the same kill switch as the top bar's, with the same rules (the current option does nothing, the confirmation,
    // the first toggle's explanation, the reasons, the classic modal)
    const sw = radioRow("Switch", 0, T("mods.switch"), ON_OFF(), v.on, (value) => ModSwitch.click("settings", value),
      { dim: v.busy || v.unavailable, description: v.unavailable ? ModSwitch.reasonText(m) : "" });
    add(helpAfterTitle(sw, T("mods.switch"), v.dot));
    // the pair of 680 x 136 buttons under the dotted line
    const pair = buttonRowGeometry(2, SMALL_BUTTON.wideW), exportH = pair.h;
    const exportRow = rowBase("Export", 0, exportH, T("export.title"));
    const exportHits = [];
    const exportSize = NGUI.fitGroup([T("export.library"), T("export.applied")], SMALL_BUTTON.fs, SMALL_BUTTON.fsMin, SMALL_BUTTON.wideW - SMALL_BUTTON.fitInset, 2,
      { maxH: SMALL_BUTTON.h - SMALL_BUTTON.boxInsetH })[0].fs;
    [["library", "export.library"], ["applied", "export.applied"]].forEach(([scope, key], i) => {
      exportRow.children.push(buttonNode(`Btn_${i}`, pair.xs[i], pair.y, T(key), { w: SMALL_BUTTON.wideW, fs: exportSize, min: exportSize }));
      exportHits.push({ path: `Export/Btn_${i}`, fn: () => ModExport.open({ scope }), name: T(key) });
    });
    add({ tree: exportRow, h: exportH, hits: exportHits });
    add(valueRow("Built", 0, T("mods.cache"), sizeError || (builtSize == null ? T("job.running") :
      ModSwitch.sizeText(builtSize)), [
      [T("settings.mods.clear"), async () => { if (await ModSwitch.clearBuilt()) render(); }, builtSize > 0]]));
    // the tray or menu bar icon, only in the app window where the system has one
    if (p.native && p.traySupported) {
      add(radioRow("Tray", 0, T(platform === "mac" ? "settings.mods.tray.mac" : "settings.mods.tray.win"), ON_OFF(), !!p.tray, (value) => set("tray", value)));
    }
    // On requires consent and checks at every start; Off withdraws consent.
    add(radioRow("Scan", 0, T("settings.mods.scan"), ON_OFF(), m.scan === true, async (value) => {
      if (value === (m.scan === true)) return;
      if (value) await ModSwitch.askScan();
      else await call(() => rpc("mods.consent", { granted: false }));
      await ModSwitch.refresh();
      render();
    }));
    add(helpAfterTitle(valueRow("Files", 0, T("settings.mods.files"), filesValue(m), [[T("settings.mods.check"), checkFiles]]),
      T("settings.mods.files"), false, driftHelp));
    // the game update summary again, as the mods stand now (its Go To leaves Settings for the mod's student page)
    add(valueRow("Impact", 0, T("settings.mods.impact"), "", [[T("settings.mods.view"), () => Impact.show()]], { showValue: false }));
    if (otherMods?.length) {
      add({ tree: rowBase("OtherMods", 0, 130, T("settings.mods.other")), h: 130, hits: [] });
      OtherMods.rows(otherMods, valueRow, refreshOtherMods).forEach(add);
    } else {
      add(valueRow("OtherMods", 0, T("settings.mods.other"), T(otherMods ? "settings.mods.empty" : "job.running"), []));
    }
  } else if (tab === "sound") {
    add(sliderRow("Voice", 0, T("settings.voice"), p.voiceVolume, p.voiceMuted));
  } else if (tab === "l2d") {
    // the memorial lobby's fullscreen zoom-out limit: the game's view, the art every clip keeps painted, everything
    // painted, or a custom number of game views across (its slider shows with Custom)
    const zoom = p.lobbyZoom || { mode: "art", custom: 2 };
    const modes = ["game", "art", "unlocked", "custom"].map((m) => [m, T(`settings.zoomOut.${m}`)]);
    add(radioRow("ZoomOut", 0, T("settings.zoomOut.label"), modes, zoom.mode, (v) => set("lobbyZoom", v)));
    if (zoom.mode === "custom") add(factorRow("ZoomCustom", 0, T("settings.zoomOut.limit"), zoom.custom));
    // the game's PP option: the lobby's post-processing volumes (colour grading, chromatic aberration, Panini)
    add(radioRow("Post", 0, T("settings.post.label"), [[true, T("settings.post.on")], [false, T("settings.post.off")]],
      p.lobbyPost !== false, (v) => set("lobbyPost", v)));
    // the memorial lobby's intro when it is entered (off: the lobby as the intro leaves it)
    add(radioRow("Intro", 0, T("settings.intro.label"), [[true, T("settings.intro.on")], [false, T("settings.intro.off")]],
      p.lobbyIntro !== false, (v) => set("lobbyIntro", v)));
  } else {
    add(radioRow("Server", 0, T("settings.server.label"), [["windows_global", T("settings.server.global")], ["windows_japan", T("settings.server.japan")]],
      p.fileSource, (v) => set("fileSource", v)));
    for (const region of ["global", "japan"]) {
      const dir = p.gameDirs[region];
      add(valueRow(`Game_${region}`, 0, T(`settings.gameDirLabel.${region}`), dir, [
        [T("settings.detect"), async () => {
          const found = await call(() => Consent.detectGame(region, () => ModSwitch.askScan()));
          if (found?.length) return set(`gameDir.${region}`, found[0]);
          if (found) notify(T("app.noGame.title"), { kind: "error", error: { code: "game_not_found" } });
        }],
        [T("settings.change"), browse(`gameDir.${region}`, dir), p.native]]));
    }
    add(valueRow("Library", 0, T("settings.library"), p.libraryDir, [[open, reveal(p.libraryDir)], [T("settings.change"), browse("libraryDir", p.libraryDir), p.native]]));
    add(valueRow("Output", 0, T("settings.output"), p.outputDir, [[open, reveal(p.outputDir)], [T("settings.change"), browse("outputDir", p.outputDir), p.native]]));
  }
  // the cards stack down the panel's list area (the area less PANEL.padList, the cards PANEL.stackGap apart)
  const placed = stackCards(out.map((r) => r.h), { w: CONTENT.w, h: CONTENT.h });
  out.forEach((r, i) => { r.tree.pos[1] = placed.centres[i] + (r.tree.pos[1] || 0); });
  return { rows: out, height: placed.height };
}

async function openLog() {
  const log = await call(() => rpc("app.log"));
  if (!log) return;
  Popup.textViewer({ title: T("settings.log.label"), text: log.text, copy: T("settings.log.copy"), onCopy: () => call(async () => {
    if (navigator.clipboard?.writeText) await navigator.clipboard.writeText(log.text);
    else {
      const input = document.createElement("textarea");
      input.value = log.text;
      document.body.appendChild(input);
      try {
        input.select();
        if (!document.execCommand("copy")) throw new Error("Clipboard unavailable");
      } finally { input.remove(); }
    }
  }) });
}

function simulateUpdate(notes) {
  close();
  return call(() => Update.simulate($("consent"), prefs, notes));
}

async function checkUpdate(event) {
  if (checkingUpdate) return;
  checkingUpdate = true;
  event.currentTarget.disabled = true;
  try {
    await render();
    const { check } = await import("./update.js");
    await check($("consent"), undefined, {
      // no newer release: the check's only answer (offline and failures go to onError)
      onCheck: (info) => { if (info) close(); else void Popup.notice(T("settings.update.none")); },
      onError: (err) => notify(errorText(err), { kind: "error", error: err }),
    });
  } catch (err) {
    notify(errorText(err), { kind: "error", error: err });
  } finally {
    checkingUpdate = false;
    if (!$("settings").hidden) render();
  }
}

let languageWrite = Promise.resolve(),resourceWrite=Promise.resolve();
function set(key, value) {
  if(key==='fileSource'||key.startsWith('gameDir')){
    const saved=resourceWrite.then(()=>saveSetting(key,value));resourceWrite=saved.catch(()=>{});return saved;
  }
  if (key !== "language") return saveSetting(key, value);
  // Keep persisted language and visible strings in the same order when several
  // radio choices are clicked before the preceding request finishes.
  const saved = languageWrite.then(() => saveSetting(key, value));
  languageWrite = saved.catch(() => {});
  return saved;
}
async function saveSetting(key, value) {
  const next = await call(() => rpc("settings.set", { key, value }));
  if (!next) return;
  prefs = next;
  await call(() => onChange?.(key, next));
  await render();
  return next;
}

// --- the sliders: a native range input over the game's slider, as the Hall's size slider has it. The pointer, the arrow keys,
// Home, End and Page Up and Down move it, a screen reader reads it (name, value); the game's thumb follows it (and the value
// label, if any), and the value is saved when the input commits (the pointer's release, each key press). The input is
// unseen: its focus shows as a ring on the thumb. A drag the system cancels (pointercancel) is undone: the thumb and the
// input go back to the saved value and nothing is saved.
// s: {path, name, value (0 to 1), steps, set(f), label?, text?(f)} ---
export const SLIDER_THUMB = 40; // px: the input's native thumb (app.css .slider-range), so its centre travels the drawn thumb's 632 units
function wireSlider(host, view, s) {
  const bar = view.nodes.get(s.path)?.els[0], thumbEntry = view.nodes.get(`${s.path}/Thumb`);
  if (!bar || !thumbEntry) return;
  const thumb = thumbEntry.els[0], valueLabel = s.label ? view.nodes.get(s.label)?.els[0] : null;
  const [l, t, w, h] = rect(bar), travel = 632 * S; // the thumb travels 632 units inside the 689-unit bar
  const range = document.createElement("input");
  range.type = "range"; range.className = "hit slider-range";
  range.min = "0"; range.max = String(s.steps); range.step = "1";
  range.setAttribute("aria-label", s.name);
  range.dataset.focusKey = `slider:${s.path}`;
  Object.assign(range.style, { left: `${l + w / 2 - travel / 2 - SLIDER_THUMB / 2}px`, top: `${t - 20}px`,
    width: `${travel + SLIDER_THUMB}px`, height: `${h + 40}px`, zIndex: 80 });
  const stepOf = (f) => Math.round(f * s.steps), fractionOf = () => Number(range.value) / s.steps;
  const base = parseFloat(thumb.style.left) - (-316 + 632 * s.value) * S;
  let saved = s.value;
  const move = (f) => {
    thumb.style.left = `${base + (-316 + 632 * f) * S}px`;
    const text = s.text ? s.text(f) : null;
    if (valueLabel && text != null) valueLabel.textContent = text;
    if (text != null) range.setAttribute("aria-valuetext", text); else range.removeAttribute("aria-valuetext");
  };
  range.value = String(stepOf(s.value)); move(s.value);
  range.addEventListener("input", () => move(fractionOf()));
  range.addEventListener("change", () => { saved = fractionOf(); s.set(saved); });
  range.addEventListener("pointercancel", () => { range.value = String(stepOf(saved)); move(saved); });
  // keyboard focus only (the pointer's focus shows nothing, as everywhere in the app)
  range.addEventListener("focus", () => { if (range.matches?.(":focus-visible") !== false) thumb.classList.add("slider-thumb-focus"); });
  range.addEventListener("blur", () => thumb.classList.remove("slider-thumb-focus"));
  host.appendChild(range);
}

async function readyLayout() {
  if (layout) return;
  await Buttons.ready();
  const popup = await NGUI.layout("UIPopup_Option_PC");
  layout = { popup, slider: find(popup, "Center/Popup/List/Sound_Setting/Sound_Scroll View/Grid_2/Master/Type_3") };
}

async function render({ builtSize = null, sizeError = null } = {}) {
  const my = ++token;
  await readyLayout();
  if (my !== token || !layout) return;
  const host = $("settings");
  const next = document.createElement("div");
  const view = await NGUI.render(frameTree(), next, { scale: S, origin: [960, 540], z: 3000 });
  const close_ = view.nodes.get("Center/Popup/Base/BG/Btn_Close")?.els[0];
  if (close_) hitButton(next, rect(close_), close, T("display.close"), 3100);
  TABS.forEach((id) => {
    const el = view.nodes.get(`Center/Popup/Tab_${id}/Tab_Off/Label_Locale`)?.els[0];
    if (el && id !== tab) {
      const [l, t, w, h] = rect(el);
      hitButton(next, [l - 60, t - 30, w + 120, h + 60], () => {
        tab = id; scroll = 0;
        if (id === "mods") refreshOtherMods();
        else render();
      }, T(`settings.tab.${id}`), 3100);
    }
  });
  // the rows scroll inside the panel's list area
  const { rows: list, height } = rows({ builtSize, sizeError });
  const [aL, aT] = [960 + (CONTENT.x - CONTENT.w / 2) * S, 540 - CONTENT.top * S];
  const area = document.createElement("div");
  area.className = "settings-rows";
  Object.assign(area.style, { left: `${aL}px`, top: `${aT}px`, width: `${CONTENT.w * S}px`, height: `${CONTENT.h * S}px` });
  const inner = document.createElement("div");
  inner.className = "settings-rows-inner";
  inner.style.height = `${Math.max(CONTENT.h, height) * S}px`;
  area.appendChild(inner);
  next.appendChild(area);
  const origin = [CONTENT.w / 2 * S, 0];
  const tree = node("Rows", [0, 0], [], list.map((r) => r.tree));
  const rv = await NGUI.render(tree, inner, { scale: S, origin, z: 3010 });
  for (const r of list) {
    for (const hit of r.hits) {
      const el = rv.nodes.get(`Rows/${hit.path}`)?.els[0];
      if (!el) continue;
      const [l, t, w, h] = rect(el), pad = (hit.pad || 0) * S;
      const b = hitButton(inner, [l - pad, t - pad, (hit.w ? hit.w * S : w) + 2 * pad, h + 2 * pad], hit.fn, hit.name, 3050);
      if (hit.description) b.setAttribute("aria-description", hit.description);
    }
    if (r.slider) wireSlider(inner, rv, { ...r.slider, path: `Rows/${r.slider.path}`, label: r.slider.label && `Rows/${r.slider.label}` });
  }
  if (my !== token || $("settings").hidden) return;
  // a click on the backdrop closes it, as Escape does (the shared stack); a redraw keeps its place on the stack
  Popup.closable(view, host, close);
  // a slider saves on every key press and the page is drawn again: the focus goes back to the same control
  const focused = document.activeElement?.dataset?.focusKey;
  host.replaceChildren(...next.childNodes);
  if (focused) [...host.querySelectorAll("[data-focus-key]")].find((el) => el.dataset.focusKey === focused)?.focus();
  host.querySelector(".settings-rows").scrollTop = scroll;
  host.querySelector(".settings-rows").addEventListener("scroll", (e) => { scroll = e.target.scrollTop; });
  if (tab === "mods" && builtSize == null && !sizeError) {
    // Fetch after drawing the loading row; an old reply must not redraw a closed or newer tab.
    try {
      const sizes = await rpc("mods.sizes", { kind: "built" });
      if (my === token && !host.hidden) await render({ builtSize: sizes.builtSize });
    } catch (err) {
      if (my === token && !host.hidden) await render({ sizeError: errorText(err) });
    }
  }
}

export function close() {
  const host = $("settings");
  host.hidden = true;
  host.innerHTML = "";
  Popup.closed(host);
}

// the Mods tab follows the switch's state (mods.state: a toggle elsewhere, the game starting, the file check)
let following = false;
async function refreshOtherMods() {
  const request = ++otherRequest;
  const next = await call(() => rpc("mods.other"));
  if (request !== otherRequest) return;
  if (next) otherMods = next;
  if (!$("settings").hidden && tab === "mods") await render();
}
function follow() {
  if (following) return;
  following = true;
  ModSwitch.onChange(() => { if (!$("settings").hidden && tab === "mods") refreshOtherMods(); });
  on("library.changed", () => { if (!$("settings").hidden && tab === "mods") refreshOtherMods(); });
}

// open(onChange, initialTab): onChange(key, prefs) after a setting was saved
export async function open(changed, initialTab = null) {
  onChange = changed;
  if (TABS.includes(initialTab)) { tab = initialTab; scroll = 0; }
  follow();
  if (!layout) await readyLayout();
  prefs = await call(() => rpc("settings.get"));
  if (!prefs) return;
  if (!ModSwitch.current()) await ModSwitch.refresh();
  otherRequest++;
  otherMods = tab === "mods" ? await call(() => rpc("mods.other")) : null;
  $("settings").hidden = false;
  // on the popup stack from the start: Escape closes it, and a popup opened over it takes the key first
  Popup.opened($("settings"), close);
  await render();
}

export const current = () => prefs;

// Prefabs and derived UI values belong to the selected installation.
NGUI.onResourceChange?.(() => { layout = null; token++; });
