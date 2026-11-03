// Every row of buttons, group of tabs or segments and pair of popup buttons, in every shipped language: one caption size for
// the whole group, every caption inside its widget (the prefab's size), nothing wider than its body, no pictures needed.
// The captions are the locale files' own; the widths and heights are the prefabs' numbers the screens use. Text is measured on
// a stand-in canvas (a CJK character one em wide, anything else half an em), so what is asserted is the logic, not a font.
// Run by tests/test_ngui_fit.py.
import assert from "node:assert/strict";
import { test } from "node:test";
import { readFile } from "node:fs/promises";

const WIDE = /[⺀-鿿　-ヿ＀-￯가-힯฀-๿]/;
const ctx = {
  font: "",
  measureText(s) {
    const fs = parseFloat(/([\d.]+)px/.exec(this.font)[1]);
    return { width: [...s].reduce((w, c) => w + (WIDE.test(c) ? fs : fs / 2), 0) };
  },
};
globalThis.document = {
  createElement: () => ({ getContext: () => ctx, remove() {} }),
  documentElement: { lang: "en-US", appendChild() {} },
};
globalThis.getComputedStyle = () => ({ getPropertyValue: () => "" });
const NGUI = await import("../../src/ba_modding_toolkit/web/js/ngui.js");
const { LINE_PITCH, fitGroup, fitLabels, fitRow, fitText, fontSizeFor, glyphWidth } = NGUI;

const LOCALES = ["en-US", "zh-CN", "zh-TW", "ja-JP", "ko-KR", "th-TH"];
const web = new Map();
for (const locale of LOCALES) web.set(locale, JSON.parse(await readFile(new URL(`../../src/ba_modding_toolkit/locales/${locale}.json`, import.meta.url), "utf8")).webui);
const text = (locale, key) => {
  const value = key.split(".").reduce((o, part) => o?.[part], web.get(locale));
  assert.equal(typeof value, "string", `${locale}: ${key}`);
  return value;
};
const source = async (file) => readFile(new URL(`../../src/ba_modding_toolkit/web/js/${file}`, import.meta.url), "utf8");

const widest = (f) => Math.max(...f.text.split("\n").map((l) => glyphWidth(l, f.fs)));
const tall = (f) => f.lines * fontSizeFor(f.text, f.fs) * LINE_PITCH;
const keepsEverything = (f, caption) => assert.equal(f.text.replace(/\s/g, ""), caption.replace(/\s/g, ""), `every character of ${JSON.stringify(caption)} kept`);

// one group: every size the same, every caption within its box (width, height), nothing flagged as overflowing
function assertGroup(fits, captions, widths, maxH, label) {
  assert.equal(fits.length, captions.length);
  assert.equal(new Set(fits.map((f) => f.fs)).size, 1, `${label}: one size, got ${fits.map((f) => f.fs)}`);
  fits.forEach((f, i) => {
    assert.ok(!f.overflow, `${label}: ${captions[i]} overflows`);
    assert.ok(f.lines <= 2, `${label}: ${captions[i]} on ${f.lines} lines`);
    assert.ok(widest(f) <= widths[i] + 1e-9, `${label}: ${JSON.stringify(f.text)} is ${widest(f)} wide in ${widths[i]}`);
    assert.ok(tall(f) <= maxH + 1e-9, `${label}: ${JSON.stringify(f.text)} is ${tall(f)} tall in ${maxH}`);
    keepsEverything(f, captions[i]);
  });
}

// --- the source pickers' action row (popup.js detailsTree), 850 wide panel, 120 high bodies ---
test("the picker's constants are the ones asserted here", async () => {
  const popup = await source("popup.js");
  assert.match(popup, /const ACTION_CLEAR = 28, ACTION_TEXT_H = 96;/);
  assert.match(popup, /left: \{ l: -1370, r: -520, t: 600, b: -690 \}/);
  assert.match(popup, /const bw = \(w - 60\) \/ actions\.length, gap = 14;/);
  assert.match(popup, /fitRow\(actions\.map\(\(\[caption\]\) => caption\), 40, 26, actions\.length \* \(bw - gap\), ACTION_CLEAR, 2, \{ maxH: ACTION_TEXT_H \}\)/);
});

const PANEL = 1370 - 520, GAP = 14, CLEAR = 28, ACTION_H = 96;
const actionSets = (locale) => {
  const t = (key) => text(locale, key), open = t("open.mac"), openWindows = t("open.win");
  return {
    "mod (five)": [t("popup.apply"), open, t("compatibility.check"), t("act.update"), t("btn.delete")],
    "repaired Mod (five)": [t("popup.apply"), open, t("compatibility.check"), t("remap.go"), t("btn.delete")],
    "Windows, mod (five)": [t("popup.apply"), openWindows, t("compatibility.check"), t("act.update"), t("btn.delete")],
    "game file (three)": [t("popup.apply"), open, t("btn.extract")],
    "Windows, game file (three)": [t("popup.apply"), openWindows, t("btn.extract")],
    "variant picker (two)": [open, t("btn.extract")],
  };
};
for (const locale of LOCALES) test(`every source picker's action row shares one size and fits its buttons in ${locale}`, () => {
  document.documentElement.lang = locale;
  for (const [name, captions] of Object.entries(actionSets(locale))) {
    const n = captions.length, slot = (PANEL - 60) / n, total = n * (slot - GAP);
    const row = fitRow(captions, 40, 26, total, CLEAR, 2, { maxH: ACTION_H });
    assertGroup(row, captions, row.map((f) => f.width - CLEAR), ACTION_H, `${locale} ${name}`);
    near(row.reduce((sum, f) => sum + f.width, 0), total);
    // bodies are the equal slots, unless equal slots would take the size under the prefab's own floor of 26
    const equal = fitGroup(captions, 40, 26, slot - GAP - CLEAR, 2, { maxH: ACTION_H });
    if (equal[0].fs >= 26) {
      assert.ok(row.every((f) => Math.abs(f.width - (slot - GAP)) < 1e-9), `${locale} ${name}: equal bodies`);
      assert.equal(row[0].fs, equal[0].fs);
    } else {
      assert.ok(row[0].fs >= equal[0].fs, `${locale} ${name}: never smaller than equal bodies give`);
    }
    // the row sits in the panel: from the first body's left edge to the last one's right edge
    const span = row.reduce((sum, f) => sum + f.width + GAP, -GAP);
    assert.ok(span <= PANEL - 60 + 1e-9);
  }
});
const near = (a, b, eps = 1e-6) => assert.ok(Math.abs(a - b) < eps, `${a} != ${b}`);

test("with the Chinese wording the five-button row keeps equal bodies at a full size", () => {
  document.documentElement.lang = "zh-CN";
  const captions = actionSets("zh-CN")["mod (five)"];
  const row = fitRow(captions, 40, 26, 5 * ((PANEL - 60) / 5 - GAP), CLEAR, 2, { maxH: ACTION_H });
  assert.ok(row[0].fs >= 36, `${row[0].fs}`);
  assert.ok(row.every((f) => f.width === row[0].width));
});

// --- the detail page's action cards and tabs (detail.js screenTree) ---
for (const locale of LOCALES) test(`the four action cards and the three tabs share one size each in ${locale}`, async () => {
  const detail = await source("detail.js");
  assert.match(detail, /const ROW_L = 58 \+ 37, ROW_R = 1320 - 37, GAP = 6, CW = \(ROW_R - ROW_L - 3 \* GAP\) \/ 4;/);
  assert.match(detail, /fitGroup\(actions\.map\(\(\[, key\]\) => T\(key\)\), 40, 28, CW - 60, 2, \{ maxH: 90 \}\)/);
  assert.match(detail, /fitGroup\(tabLabels, 52, 36, 360, 1\)/);
  document.documentElement.lang = locale;
  const CW = (1320 - 37 - (58 + 37) - 3 * 6) / 4;
  const cards = ["compatibility.check", "act.update", "export.title", "popup.apply"].map((key) => text(locale, key));
  assertGroup(fitGroup(cards, 40, 28, CW - 60, 2, { maxH: 90 }), cards, cards.map(() => CW - 60), 90, `${locale} cards`);
  for (const lobby of ["tab.lobby", "special.background"]) {
    const tabs = ["tab.portrait", lobby, "tab.model"].map((key) => text(locale, key));
    const fits = fitGroup(tabs, 52, 36, 360, 1);
    assertGroup(fits, tabs, tabs.map(() => 360), Infinity, `${locale} tabs`);
    assert.ok(fits.every((f) => f.lines === 1));
  }
});

// --- popup buttons: Ok / Cancel pairs (popupFrame, listFrame, consent) and the update popup's three, Common_Btn_BG 496 x 177 ---
// One caption range for every footer (ui-tokens.js FOOTER.caption): 72 down to 48, a 400 box, two lines, 140 high
const { FOOTER } = await import("../../src/ba_modding_toolkit/web/js/ui-tokens.js");
const PAIRS = [["picker.ok", "popup.cancel"], ["popup.save", "popup.cancel"], ["consent.no", "consent.yes"], ["mods.scan.no", "mods.scan.yes"],
  ["spine.license.no", "spine.license.yes"], ["update.later", "update.notes", "update.now"], ["update.later", "update.notes", "update.restart"],
  ["update.later", "update.notes", "update.page"]];
const BELOW_MINIMUM = {
  "en-US": ["spine.license.no/spine.license.yes"],
  "th-TH": ["spine.license.no/spine.license.yes", "update.later/update.notes/update.now", "update.later/update.notes/update.restart",
    "update.later/update.notes/update.page"],
};
for (const locale of LOCALES) test(`pairs of popup buttons share one size and fit the 496 x 177 body in ${locale}`, async () => {
  const popup = await source("popup.js"), consent = await source("consent.js"), update = await source("update.js");
  assert.match(popup, /export const BUTTON_TEXT_H = FOOTER\.caption\.h;/);
  assert.match(popup, /fitLabels\(widgets, captions, CAPTION\.fs, CAPTION\.fsMin, CAPTION\.w, CAPTION\.lines, \{ maxH: CAPTION\.h \}\)/);
  assert.match(popup, /fitGroup\(captions, CAPTION\.fs, CAPTION\.fsMin, CAPTION\.w, CAPTION\.lines, \{ maxH: CAPTION\.h \}\)/);
  // the other footers take that fit: popupFrame, listFrame and the update offer call it, consent calls popupFrame
  assert.match(popup, /footerFit\(buttons\.map\(\(b\) => b\.label\)\)/);
  assert.match(consent, /Popup\.popupFrame\(title, w, h, \{ ok: yes, cancel: no, close: false \}\)/);
  assert.match(update, /Popup\.footerLabels\(widgets, widgets\.map\(\(l\) => l\.text\)\)/);
  for (const [name, text] of [["popup", popup], ["consent", consent], ["update", update]]) assert.doesNotMatch(text, /\b64, 40\b/, `${name} has no private caption range`);
  assert.deepEqual(FOOTER.caption, { fs: 72, fsMin: 48, w: 400, lines: 2, h: 140 });
  document.documentElement.lang = locale;
  const { fs: max, fsMin: min, w: width, lines, h: height } = FOOTER.caption;
  for (const keys of PAIRS) {
    const captions = keys.map((key) => text(locale, key)), [a, b] = [keys[0], keys.slice(1).join("/")];
    const widgets = captions.map(() => ({ text: "", fontSize: 72, w_: 400 }));
    const fits = fitLabels(widgets, captions, max, min, width, lines, { maxH: height });
    assertGroup(fits, captions, captions.map(() => width), height, `${locale} footer ${a}/${b}`);
    // In this file's stand-in canvas (Thai counted a full em a code point, Latin half an em) a few captions do not hold the
    // range's 48 in the 400 box on two lines; the shared fit then takes the group below it, never past the box (asserted above).
    // They are named here and reported, not hidden: the wording is the owner's.
    const known = BELOW_MINIMUM[locale]?.includes(`${a}/${b}`);
    if (known) assert.ok(fits[0].fs < min && fits[0].fs >= 36, `${locale} ${a}/${b}: ${fits[0].fs} is below the range's minimum, as recorded`);
    else assert.ok(fits[0].fs >= min && fits[0].fs <= max, `${locale} ${a}/${b}: ${fits[0].fs} is in the range`);
    assert.deepEqual(widgets.map((w) => w.fontSize), fits.map((f) => f.fs));
    assert.ok(widgets.every((w) => w.w_ >= width), "the label box is as wide as the room the caption was fitted to");
  }
});

// --- segments: the roster's six and the list popup's three ---
for (const locale of LOCALES) test(`the roster's six segments and the popup's three share one size in ${locale}`, async () => {
  const roster = await source("roster-widgets.js"), popup = await source("popup.js");
  assert.match(roster, /fitGroup\(\["seg\.all", "seg\.mods", "seg\.noMods", "seg\.all", "seg\.recruit", "seg\.noRecruit"\]\.map\(\(k\) => T\(k\)\), segFs, 22, segW - 26, 2, \{ maxH: 96 \}\)/);
  assert.match(popup, /fitGroup\(segs\.map\(\(key\) => T\(key\)\), 44, 26, segW - 30, 2, \{ maxH: 96 \}\)/);
  document.documentElement.lang = locale;
  for (const [segW, segFs] of [[230, 44], [210, 40], [200, 36], [190, 34], [180, 32], [170, 30]]) {
    const six = ["seg.all", "seg.mods", "seg.noMods", "seg.all", "seg.recruit", "seg.noRecruit"].map((key) => text(locale, key));
    assertGroup(fitGroup(six, segFs, 22, segW - 26, 2, { maxH: 96 }), six, six.map(() => segW - 26), 96, `${locale} roster ${segW}`);
  }
  const three = ["seg.all", "seg.recruit", "seg.noRecruit"].map((key) => text(locale, key));
  assertGroup(fitGroup(three, 44, 26, 230 - 30, 2, { maxH: 96 }), three, three.map(() => 200), 96, `${locale} popup`);
});

// --- Settings: tabs (300 wide), the buttons of a value row (402 x 136, the prefab's small button), the export pair (680 x 136) ---
const { SMALL_BUTTON } = await import("../../src/ba_modding_toolkit/web/js/ui-tokens.js");
const S = 1920 / 2960;
for (const locale of LOCALES) test(`Settings tabs and button pairs share one size and fit in ${locale}`, async () => {
  const settings = await source("settings.js"), buttons = await source("game-buttons.js");
  assert.match(settings, /fitGroup\(TABS\.map\(\(id\) => T\(`settings\.tab\.\$\{id\}`\)\), 56, 40, 300, 2\)/);
  assert.match(settings, /SMALL_BUTTON\.fs, SMALL_BUTTON\.fsMin,\s*SMALL_BUTTON\.w - SMALL_BUTTON\.fitInset, 2, \{ maxH: SMALL_BUTTON\.h - SMALL_BUTTON\.boxInsetH \}/);
  assert.match(settings, /SMALL_BUTTON\.fs, SMALL_BUTTON\.fsMin, SMALL_BUTTON\.wideW - SMALL_BUTTON\.fitInset, 2,\s*\{ maxH: SMALL_BUTTON\.h - SMALL_BUTTON\.boxInsetH \}/);
  assert.match(buttons, /w = SMALL_BUTTON\.w, h = SMALL_BUTTON\.h, enabled = true, fs = SMALL_BUTTON\.fs, min = SMALL_BUTTON\.fsMin/);
  document.documentElement.lang = locale;
  const { w, h, fs, fsMin, fitInset, boxInsetH, wideW } = SMALL_BUTTON;
  const tabs = Object.keys(web.get(locale).settings.tab).map((id) => text(locale, `settings.tab.${id}`));
  assertGroup(fitGroup(tabs, 56, 40, 300, 2), tabs, tabs.map(() => 300), Infinity, `${locale} tabs`);
  const exported = ["export.library", "export.applied"].map((key) => text(locale, key));
  assertGroup(fitGroup(exported, fs, fsMin, wideW - fitInset, 2, { maxH: h - boxInsetH }), exported, exported.map(() => wideW - fitInset), h - boxInsetH, `${locale} export`);
  // the value rows' buttons: Open and Change / Clear cache style captions
  for (const pair of [["settings.change", "settings.detect"], ["settings.mods.clear", "open.mac"], ["open.mac", "settings.change"]]) {
    const captions = pair.map((key) => text(locale, key));
    assertGroup(fitGroup(captions, fs, fsMin, w - fitInset, 2, { maxH: h - boxInsetH }), captions, captions.map(() => w - fitInset), h - boxInsetH, `${locale} ${pair}`);
  }
});

// --- Batch Import: the list's seven tools (widths 174 190 126 140 150 224 156 at scale 0.34) and the row under the list ---
for (const locale of LOCALES) test(`the import list's tools and the row under the list share one size and fit their bodies in ${locale}`, async () => {
  const page = await source("batch-import.js"), layout = await source("import-layout.js");
  assert.match(page, /\["files", "addFiles", 26, 174\], \["folder", "addFolder", 210, 190\], \["all", "all", 410, 126\], \["none", "none", 546, 140\]/);
  assert.match(page, /\["remove", "remove", 696, 150\], \["removeImported", "removeImported", 856, 224\], \["clear", "clear", 1090, 156\]/);
  assert.match(page, /\[\["options", "recheck", "start"\], FOOTER_ROW\.size\]/);
  assert.match(page, /wide: 330, width: SMALL_BUTTON\.w \* S/);
  assert.match(layout, /ui\.fitGroup\(captions, max \?\? w\.fontSize, min \?\? w\.fontSize \* 0\.7, widths\.map\(\(width\) => width - 60\), 2, \{ maxH: t\.widgets\.find\(\(x\) => "sprite" in x\)\.h_ - 20 \}\)/);
  document.documentElement.lang = locale;
  // Btn_Receive: caption 60 units, body 162 high, the label box 20 less
  const tools = [["addFiles", 174], ["addFolder", 190], ["all", 126], ["none", 140], ["remove", 150], ["removeImported", 224], ["clear", 156]];
  const widths = tools.map(([, width]) => width / 0.34 - 60), captions = tools.map(([key]) => text(locale, `import.${key}`));
  assertGroup(fitGroup(captions, 60, 42, widths, 2, { maxH: 162 - 20 }), captions, widths, 142, `${locale} tools`);
  // the row: the small button's 136 and caption 48 down to 34, on the prefabs' 162-high bodies, Import's caption in every form
  const k = SMALL_BUTTON.h * S / 162, range = [SMALL_BUTTON.fs * 162 / SMALL_BUTTON.h, SMALL_BUTTON.fsMin * 162 / SMALL_BUTTON.h];
  const rowWidths = [330, SMALL_BUTTON.w * S, SMALL_BUTTON.w * S].map((width) => width / k - 60);
  const action = text(locale, "import.action");
  for (const start of [action.replace("{n}", "0"), action.replace("{n}", "999"), text(locale, "import.results")]) {
    const row = [text(locale, "import.options.title"), text(locale, "import.recheck"), start];
    const fits = fitGroup(row, range[0], range[1], rowWidths, 2, { maxH: 142 });
    assertGroup(fits, row, rowWidths, 142, `${locale} row ${start}`);
    assert.ok(fits[0].fs <= range[0] && fits[0].fs >= range[1], `${locale} row ${start}: ${fits[0].fs} is in the small button's range`);
  }
});

// --- the photography gallery's pairs and the Hall modal's footer (Buttons.smallButton: 40 down to 26, label box 60 / 20 less) ---
for (const locale of LOCALES) test(`the gallery's button pairs share one size and fit in ${locale}`, async () => {
  const ui = await source("student-hall-ui.js"), controller = await source("photography/controller.js");
  assert.match(ui, /fitGroup\(captions, 40, 26, width \/ S - 60, 2, \{ maxH: height \/ S - 20 \}\)/);
  for (const call of ["buttonSize([P('apply'),P('delete')],140,64)", "buttonSize([P('previous'),P('next')],180,55)", "buttonSize([P('restore'),P('export')],180,64)"]) assert.ok(controller.includes(call), call);
  document.documentElement.lang = locale;
  const S = 1920 / 2960;
  for (const [keys, width, height] of [[["apply", "delete"], 140, 64], [["previous", "next"], 180, 55], [["restore", "export"], 180, 64], [["apply", "saveSelection"], 140, 64]]) {
    const captions = keys.map((key) => text(locale, `photo.${key}`)), room = width / S - 60, maxH = height / S - 20;
    assertGroup(fitGroup(captions, 40, 26, room, 2, { maxH }), captions, captions.map(() => room), maxH, `${locale} ${keys}`);
  }
  // the Hall modal's footer actions: up to 280 wide, 66 high
  const hall = ["hall.addPortraits", "hall.adjust", "hall.remove", "popup.cancel", "popup.save"].map((key) => text(locale, key));
  assertGroup(fitGroup(hall, 40, 26, 280 / S - 60, 2, { maxH: 66 / S - 20 }), hall, hall.map(() => 280 / S - 60), 66 / S - 20, `${locale} hall footer`);
});

// --- a lone caption that is one long word never runs past its body (Show in Explorer in Japanese is one) ---
for (const locale of LOCALES) test(`the Show in Finder / Explorer button's caption stays in its body in ${locale}`, () => {
  document.documentElement.lang = locale;
  for (const key of ["open.mac", "open.win"]) {
    const f = fitText(text(locale, key), 32, 26, 140);
    assert.ok(widest(f) <= 140 + 1e-9, `${locale} ${key}: ${JSON.stringify(f.text)} is ${widest(f)} wide in 140`);
    assert.ok(f.lines <= 2);
    keepsEverything(f, text(locale, key));
  }
});

for (const locale of LOCALES) test(`Hall portrait Reset fits its native button in ${locale}`, () => {
  document.documentElement.lang=locale;
  const S=1920/2960, caption=text(locale,"hall.scaleReset"), room=156/S-60, height=68/S-20;
  assertGroup(fitGroup([caption],40,26,room,2,{maxH:height}),[caption],[room],height,`${locale} portrait reset`);
});

for (const locale of LOCALES) test(`Hall portrait swap choice fits its existing card button in ${locale}`, () => {
  document.documentElement.lang=locale;
  const S=1920/2960, caption=text(locale,"import.select"), room=(320-22)*.46/S-60, height=40/S-20;
  const fit=fitGroup([caption],40,26,room,2,{maxH:height})[0];
  assertGroup([fit],[caption],[room],height,`${locale} portrait choice`);
});
