// The design tokens (web/js/ui-tokens.js): one place for the numbers and colours that popups, buttons, chips and the style
// sheets share. The prefab values behind them are checked by tests/test_ui_tokens.py against the development view.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { row, toNode } from "../../src/bmpt/web/js/ui-grid.js";
import * as Tokens from "../../src/bmpt/web/js/ui-tokens.js";
import { stack as layoutStack } from "../../src/bmpt/web/js/ui-grid.js";
const near = (a, b, eps = 1e-9) => assert.ok(Math.abs(a - b) < eps, `${a} != ${b}`);

const WEB = new URL("../../src/bmpt/web/", import.meta.url);
const read = (path) => readFileSync(new URL(path, WEB), "utf8");

const frozenDeep = (value) => !value || typeof value !== "object" || (Object.isFrozen(value) && Object.values(value).every(frozenDeep));

test("every token is frozen and the module imports no other UI module", () => {
  for (const [name, value] of Object.entries(Tokens)) assert.ok(frozenDeep(value), `${name} is frozen`);
  assert.doesNotMatch(read("js/ui-tokens.js"), /^\s*import\b/m);
});

test("the footer's centre follows from the prefab's frame and button rows", () => {
  const { POPUP, FOOTER } = Tokens;
  assert.equal(POPUP.frameY, -12);
  // Btn at y -499, the frame's bottom edge at frameY - frameH / 2
  assert.equal(FOOTER.btnY - (POPUP.frameY - POPUP.frameH / 2), FOOTER.centre);
  assert.equal(FOOTER.centre, 190.5);
  // Btn_Ok at +292 and Btn_Cancel at -292, bodies 496 wide: 88 between them
  assert.equal(2 * FOOTER.pairX - FOOTER.body.w, FOOTER.pairGap);
  assert.equal(FOOTER.pairGap, 88);
});

test("the small button and the chip are the prefab nodes' sizes, their pitches follow", () => {
  const { SMALL_BUTTON, CHIP } = Tokens;
  assert.deepEqual([SMALL_BUTTON.w, SMALL_BUTTON.h, SMALL_BUTTON.fs, SMALL_BUTTON.fsMin, SMALL_BUTTON.minW], [402, 136, 48, 34, 240]);
  assert.equal(SMALL_BUTTON.pitch, 422);
  assert.equal(SMALL_BUTTON.pitch - SMALL_BUTTON.w, SMALL_BUTTON.gap);
  assert.deepEqual([CHIP.w, CHIP.h, CHIP.fs, CHIP.fsMin, CHIP.pitch], [272, 72, 40, 28, 290]);
  assert.equal(CHIP.pitch - CHIP.w, CHIP.gap);
});

// --- the measuring stand-in the fit tests share (button_row_fit.test.mjs): a CJK or Thai code point one em, anything else half ---
const WIDE = /[\u2e80-\u9fff\u3000-\u30ff\uff00-\uffef\uac00-\ud7af\u0e00-\u0e7f]/;
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
const NGUI = await import("../../src/bmpt/web/js/ngui.js");
const ButtonsModule = await import("../../src/bmpt/web/js/game-buttons.js");
const { controls } = await import("./control_prefabs.mjs");

const { option, filter } = controls();
const smallButton = (...args) => ButtonsModule.optionButton(option, filter, ...args);
const bodyOf = (tree) => tree.widgets.find((w) => w.sprite === "Common_Btn_BG");
const labelOf = (tree) => NGUI.find(tree, "Label_Locale").widgets[0];

// --- item D: one small button, the prefab's ETCSetting/ButtonBg ---
test("the small button defaults to the prefab's 402 x 136 at 48 down to 34, its label box 30 and 20 less", () => {
  const { SMALL_BUTTON: B } = Tokens;
  const tree = smallButton("B", 0, 0, "OK");
  assert.deepEqual([bodyOf(tree).w_, bodyOf(tree).h_], [402, 136]);
  const label = labelOf(tree);
  assert.deepEqual([label.w_, label.h_], [402 - 30, 136 - 20]);
  assert.equal(label.fontSize, 48);
  // a long caption wraps, then shrinks, inside the fit width (w - 60) and the box height, never under 34 while it can hold
  const long = labelOf(smallButton("B", 0, 0, "Clear the cache of this application"));
  const widest = Math.max(...long.text.split("\n").map((line) => NGUI.glyphWidth(line, long.fontSize)));
  assert.ok(widest <= 402 - B.fitInset && long.fontSize <= 48);
  // only the width varies, and the label follows it
  for (const w of [240, 300, 402, 680]) {
    const wide = smallButton("B", 0, 0, "Open", { w });
    assert.deepEqual([bodyOf(wide).w_, bodyOf(wide).h_, labelOf(wide).w_, labelOf(wide).h_], [w, 136, w - 30, 116]);
  }
});

test("stopped memorial sites explicitly retain their existing button boxes and caption range", () => {
  const S = 1920 / 2960;
  for (const [w, h, fs, min] of [[280 / S, 66 / S, 40, 26], [(720 - 22) * .39 / S, 40 / S, 40, 26],
    [(720 - 22) * .25 / S, 36 / S, 40, 26], [190 / S, 36 / S, 40, 26]]) {
    const tree = smallButton("B", 0, 0, "Add Portraits", { w, h, fs, min });
    assert.deepEqual([bodyOf(tree).w_, bodyOf(tree).h_], [w, h]);
    assert.deepEqual([labelOf(tree).w_, labelOf(tree).h_], [w - 30, h - 20]);
    assert.ok(labelOf(tree).fontSize <= fs, `${w} x ${h}`);
  }
  const hall = read("js/student-hall-ui.js");
  assert.match(hall, /retainedButtonSize = .*NGUI\.fitGroup\(captions, 40, 26, width \/ S - 60, 2, \{ maxH: height \/ S - 20 \}\)/);
  assert.match(hall, /actions.map\(\(action, index\) => retainedButton/);
  assert.match(hall, /kind === "spr" \? button : retainedButton/);
  assert.match(read("js/student-hall-picker.js"), /UI\.retainedButton\(box, `Remove/);
  assert.match(read("js/student-hall.js"), /UI\.button\(destination, "HallPortraitScaleReset"/);
});

// --- item F: colours from the game's own prefabs, each in one place ---
import { readdirSync } from "node:fs";
const sources = (dir, ext) => readdirSync(new URL(dir, WEB), { withFileTypes: true }).flatMap((entry) =>
  entry.isDirectory() ? sources(`${dir}${entry.name}/`, ext) : entry.name.endsWith(ext) ? [`${dir}${entry.name}`] : []);

test("the status colours are the game's: success, warning, error, greyed, navy, In Use and note", () => {
  assert.deepEqual({ ...Tokens.COLOUR }, { success: "#0a6f2d", warning: "#f14f00", error: "#bb3421", greyed: "#828282", navy: "#2c4564",
    inUse: "#ffb700", inUseInk: "#4b2116", note: "#6a7b8f", neverBar: "#6a7b8f" });
});

test("the stats box tokens: the prefab's colours and outset, the lead's caption, value and row numbers, the app's paddings", () => {
  const { STATS_BOX: B } = Tokens;
  assert.deepEqual([B.fill, B.line, B.divider, B.lineOutset], ["#ecf5f9", "#c0c9d4", "#c0c9d4", 5]);
  assert.deepEqual({ ...B.inset }, { frame: 200, panel: 40, table: 5 });
  assert.deepEqual([B.caption, B.value.fs, B.value.fsMin, B.value.lines, B.rowMin], [38, 38, 26, 3, 64]);
  assert.deepEqual([B.padY, B.padX, B.dividerInset, B.captionGap, B.slack, B.rowPad, B.linePitch, B.labelPad], [12, 30, 30, 30, 20, 16, 1.25, 8]);
  assert.deepEqual({ ...B.secondary }, { fs: 28, fsMin: 24, h: 36, extra: 42, rise: 21, foot: 26, colour: "#2d4664/0.65" });
});

test("no site outside ui-tokens.js carries a retired backdrop or colour literal", () => {
  const retired = [/#000000\/0\.60/i, /#000000\/0\.65/i, /#1f9a52/i, /#d9822b/i, /#c74646/i, /#c0392b/i, /#9aa3ad/i, /#2c445c/i, /#ffbc11/i, /#3a2a00/i,
    /#6b7d92/i, /#545454/i];
  const files = [...sources("js/", ".js"), ...sources("css/", ".css")].filter((file) => file !== "js/ui-tokens.js");
  assert.ok(files.length > 100 && files.includes("js/popup.js") && files.includes("css/app.css"));
  for (const file of files) {
    const text = read(file);
    for (const pattern of retired) assert.doesNotMatch(text, pattern, `${file} still holds ${pattern}`);
  }
});

test("the call sites take the status colours from the tokens, the style sheet repeats the same values", () => {
  const uses = { "js/update.js": ["COLOUR.success", "COLOUR.error"], "js/detail.js": ["COLOUR.success", "COLOUR.warning", "COLOUR.greyed"],
    "js/mod-compare.js": ["COLOUR.warning", "COLOUR.success", "COLOUR.error"], "js/prompts.js": ["COLOUR.greyed"],
    "js/popup.js": ["COLOUR.inUse", "COLOUR.inUseInk"], "js/consent.js": ["COLOUR.neverBar", "COLOUR.note"] };
  for (const [file, names] of Object.entries(uses)) for (const name of names) assert.ok(read(file).includes(name), `${file} uses ${name}`);
  const css = read("css/app.css");
  assert.match(css, new RegExp(`--navy-text: ${Tokens.COLOUR.navy};`));
  assert.match(css, new RegExp(`\\.nav-item\\.greyed \\{ color: ${Tokens.COLOUR.greyed}; \\}`));
  // the loading fallback's text shadow and dot take the same navy
  assert.match(css, new RegExp(`text-shadow: 0 3px 0 ${Tokens.COLOUR.navy}, 2px 0 0 ${Tokens.COLOUR.navy}, -2px 0 0 ${Tokens.COLOUR.navy};`));
  assert.match(css, new RegExp(`box-shadow: 0 0 0 1px #fff, 0 3px 0 2px ${Tokens.COLOUR.navy};`));
});

// --- item H: the game's corner ladder, one focus ring, one fallback button ---
const rule = (css, selector) => {
  const i = css.indexOf(selector);
  assert.ok(i >= 0, `${selector} in the style sheet`);
  return css.slice(css.indexOf("{", i) + 1, css.indexOf("}", i));
};
// the declarations of the rule whose selector (or first selector of a list) is `selector`
const ruleStarting = (css, selector) => {
  const bare = css.replace(/\/\*[\s\S]*?\*\//g, "");
  for (const [, head, body] of bare.matchAll(/([^{}]+)\{([^}]*)\}/g)) {
    if (head.split(",")[0].trim().replace(/\s+/g, " ") === selector) return body;
  }
  assert.fail(`${selector} rule`);
};

test("the radii are the game's ladder (Raius5px, Raius10px and Raius20px: 5, 10 and 22 units) in px at the stage's scale", () => {
  const S = 1920 / 2960;
  assert.deepEqual({ ...Tokens.RADIUS_PX }, { r5: 3.24, r10: 6.49, r22: 14.27 });
  for (const [px, units] of [[3.24, 5], [6.49, 10], [14.27, 22]]) assert.ok(Math.abs(px - units * S) < 0.005, `${units} units`);
});

test("one focus ring for every control: 4 px #2fa6f0, 2 px off, radius 6.49 px; cards keep a negative offset and drop the glow", () => {
  const css = read("css/app.css");
  const { FOCUS } = Tokens;
  assert.deepEqual([FOCUS.colour, FOCUS.width, FOCUS.offset, FOCUS.radiusPx], ["#2fa6f0", 4, 2, 6.49]);
  const outlines = css.match(/outline:[^;]*#2fa6f0[^;]*;/g);
  assert.equal(outlines.length, 1, "one declaration of the ring in the whole style sheet");
  const ring = ruleStarting(css, ".hit:focus-visible");
  assert.match(ring, new RegExp(`outline: ${FOCUS.width}px solid ${FOCUS.colour}; outline-offset: ${FOCUS.offset}px; border-radius: ${FOCUS.radiusPx}px;`));
  const selectors = css.slice(css.indexOf(".hit:focus-visible"), css.indexOf("{", css.indexOf(".hit:focus-visible")));
  for (const name of [".card-hit:focus-visible", ".nav-item:focus-visible", ".top-option:focus-visible", ".top-home:focus-visible", ".back:focus-visible",
    ".ms-toggle:focus-visible", ".ms-help:focus-visible", ".slider-thumb-focus", ".plain-popup .message-button:focus-visible", ".plain-check:focus-visible",
    ".plain-line-button:focus-visible"]) assert.ok(selectors.includes(name), name);
  const card = ruleStarting(css, ".card-row .card-hit:focus-visible");
  assert.match(card, /outline-offset: -3px;/);
  assert.doesNotMatch(card, /box-shadow|rgba\(47, ?166, ?240/);
  assert.doesNotMatch(css, /rgba\(47, ?166, ?240/, "no focus glow anywhere");
});

test("the plain popup and its parts sit on the ladder, and the three fallback buttons are one flat look", () => {
  const css = read("css/app.css"), fallback = read("css/fallback.css");
  const { RADIUS_PX: R, FALLBACK_BUTTON: B } = Tokens;
  assert.match(ruleStarting(css, ".plain-popup"), new RegExp(`border-radius: ${R.r22}px`));
  assert.match(ruleStarting(css, ".plain-check::before"), new RegExp(`border-radius: ${R.r10}px`));
  const look = [ruleStarting(css, ".plain-line-button"), rule(fallback, ".message-button, #bamt-recovery button")];
  for (const declarations of look) {
    assert.match(declarations, new RegExp(`border-radius: ${B.radiusPx}px`));
    assert.match(declarations, new RegExp(`background: ${B.fill}`));
    assert.match(declarations, new RegExp(`color: ${B.ink}`));
    assert.doesNotMatch(declarations, /skew|transform|box-shadow|text-shadow/);
  }
  assert.deepEqual([B.fill, B.ink, B.radiusPx], ["#8de4ff", "#314665", 6.49]);
  // The lobby now draws the prefab balloon; its CSS host has only geometry and motion.
  assert.doesNotMatch(ruleStarting(css, ".lobby-subtitle"), /border-radius|background|color|font|padding/);
  assert.match(css, /\.loading-fallback i \{[^}]*border-radius: 50%/);
});

// --- item B/I: the footer's caption range through every locale file ---
import { readFileSync as readSync } from "node:fs";
const LOCALE_DIR = new URL("../../src/bmpt/locales/", import.meta.url);
const locales = readdirSync(LOCALE_DIR).filter((name) => name.endsWith(".json")).map((name) => name.slice(0, -5)).sort();
// every set of captions that stands together in one popup footer
const FOOTERS = [["picker.ok", "popup.cancel"], ["popup.save", "popup.cancel"], ["popup.apply", "popup.cancel"], ["consent.no", "consent.yes"],
  ["mods.scan.no", "mods.scan.yes"], ["update.later", "update.notes", "update.now"],
  ["update.later", "update.notes", "update.restart"], ["update.later", "update.notes", "update.page"], ["update.releasePage", "display.close"],
  ["tour.previous", "tour.next"], ["tour.previous", "tour.finish"], ["export.chooseDirectory", "export.confirm"], ["export.openFolder", "popup.cancel"],
  ["remap.discard", "remap.accept"], ["settings.log.copy", "display.close"], ["picker.ok", "open.mac"], ["picker.ok", "open.win"], ["btn.delete", "popup.cancel"],
  ["recovery.report", "picker.ok"]];
// Captions that do not hold the range's 48 in a 400 box on two lines in this file's stand-in canvas (a code point of Thai a full em,
// Latin half an em): the shared fit takes the group under 48, never past the box. Named here so that they are seen, not hidden.
const BELOW_MINIMUM = {
  "th-TH": ["update.later/update.notes/update.now", "update.later/update.notes/update.restart", "update.later/update.notes/update.page",
    "recovery.report/picker.ok"],
};
test("there is a locale file for each shipped language", () => {
  assert.deepEqual(["en-US", "ja-JP", "ko-KR", "th-TH", "zh-CN", "zh-TW"].filter((l) => !locales.includes(l)), []);
});
for (const locale of locales) test(`every footer's captions share one size in the 72 to 48 range, inside the 472 x 156 box, in ${locale}`, () => {
  const web = JSON.parse(readSync(new URL(`${locale}.json`, LOCALE_DIR), "utf8")).webui;
  const text = (key) => key.split(".").reduce((o, part) => o?.[part], web);
  document.documentElement.lang = locale;
  const max = Tokens.TYPE.display.fs, min = Tokens.TYPE.display.min, w = 472, lines = 2, h = 156;
  for (const keys of FOOTERS) {
    const captions = keys.map(text);
    const what = `${locale} ${keys.join("/")}`;
    if (captions.some((c) => typeof c !== "string")) { assert.fail(`${what}: a caption is missing`); }
    const widgets = captions.map(() => ({ text: "", fontSize: max, w_: w }));
    const fits = NGUI.fitLabels(widgets, captions, max, min, w, lines, { maxH: h });
    assert.equal(new Set(fits.map((f) => f.fs)).size, 1, `${what}: one size`);
    fits.forEach((f, i) => {
      assert.ok(!f.overflow, `${what}: ${captions[i]} overflows`);
      assert.ok(f.lines <= lines, `${what}: ${captions[i]} on ${f.lines} lines`);
      for (const line of f.text.split("\n")) assert.ok(NGUI.glyphWidth(line, f.fs) <= w + 1e-9, `${what}: ${JSON.stringify(line)} wider than ${w}`);
      assert.ok(f.lines * NGUI.fontSizeFor(f.text, f.fs) * NGUI.LINE_PITCH <= h + 1e-9, `${what}: ${JSON.stringify(f.text)} taller than ${h}`);
      assert.equal(f.text.replace(/\s/g, ""), captions[i].replace(/\s/g, ""), `${what}: every character kept`);
    });
    const size = fits[0].fs;
    if (BELOW_MINIMUM[locale]?.includes(keys.join("/"))) {
      const before = NGUI.fitGroup(captions, max, min, 400, 2, { maxH: 140 })[0].fs;
      assert.ok(size >= 36 && size >= before, `${what}: no smaller than the recorded fit ${before}`);
    }
    else assert.ok(size >= min && size <= max, `${what}: ${size} is in the range`);
  }
});

// Hall modals use the shared footer; the memorial editor still hits its full-row stop rule.
const popupSource = read("js/popup.js");
const footerHelpers = new Function("CONTROL", "POPUP_FRAME", "SPACE", "TYPE", "BUTTON_TEXT_H", "row", "toNode", "FRAME_Y", "NGUI", "captionMinimum", `${popupSource.slice(popupSource.indexOf("export const footerY"), popupSource.indexOf("// the game's popup frame at a size"))
  .replaceAll("export ", "")}; return {footerY, footerTop, footerSlots, footerFit, footerLabels};`)(Tokens.CONTROL, Tokens.POPUP_FRAME, Tokens.SPACE, Tokens.TYPE, 156, row, toNode, Tokens.POPUP_FRAME.frameY, NGUI, Tokens.captionMinimum);
test("Hall modal footers use buttonL for one, two and three actions", () => {
  for (const n of [1, 2, 3]) {
    const { scale, xs } = footerHelpers.footerSlots(n, 2800);
    assert.equal(scale, 1);
    assert.equal(xs.reduce((sum, x) => sum + x, 0), 0);
    for (let i = 1; i < n; i++) assert.ok(Math.abs(xs[i] - xs[i-1] - Tokens.CONTROL.buttonL.minW * scale
      - Tokens.POPUP_FRAME.footer.gap) < 1e-9);
    assert.equal(footerHelpers.footerY(1450) - (Tokens.POPUP.frameY - 1450 / 2), Tokens.POPUP_FRAME.footer.bottom + Tokens.CONTROL.buttonL.h / 2);
  }
});
const { SORTS } = await import("../../src/bmpt/web/js/roster-order.js");
const toolbarSource = read("js/roster-widgets.js");
const toolbarFor = (web) => new Function("NGUI", "T", "advanceOf", "glyphsOf", "fitText", "SORTS",
  `${toolbarSource.slice(toolbarSource.indexOf("export function toolbarLayout"), toolbarSource.indexOf("// UICharacterCollection/SeletControl/LeftInfo_Empty"))
    .replaceAll("export ", "")}; return toolbarLayout;`)(NGUI, (key) => key.split(".").reduce((v, k) => v[k], web), NGUI.glyphWidth, NGUI.glyphWidth, NGUI.fitText, SORTS);
const modalLayoutFor = (web) => {
  const source = read("js/student-hall-ui.js");
  return new Function("Popup", "POPUP", "S", "toolbarLayout", "SPACE", "BUTTON", "layoutStack", `${source.slice(source.indexOf("export const modalContentBottom"), source.indexOf("// Keep a caller's centre"))
    .replaceAll("export ", "")}; return {modalContentBottom, portraitPickerLayout, backgroundPickerBox, portraitRowLayout};`)({ ...footerHelpers, LIST_CARD_NAME: { fs: 34, lines: 2, padY: 22, pitch: 1.2 } }, Tokens.POPUP, 1920 / 2960, toolbarFor(web), Tokens.SPACE, Tokens.CONTROL.buttonS, layoutStack);
};
for (const locale of ["en-US", "th-TH", "zh-CN", "zh-TW", "ja-JP", "ko-KR"]) test(`Hall modal content clears the shared footer, keeping full rows in both search states in ${locale}`, () => {
  document.documentElement.lang = locale;
  const web = JSON.parse(readSync(new URL(`${locale}.json`, LOCALE_DIR), "utf8")).webui;
  const layout = modalLayoutFor(web), S = 1920 / 2960;
  for (const searching of [false, true]) {
    const box = layout.portraitPickerLayout({ sort: "name" }, searching);
    const [x, y, w, h] = box.list;
    assert.ok(h >= layout.portraitRowLayout().height, "one full grown portrait row, including the tallest name strip");
    assert.ok(box.toolbar.y - 70 * box.scale >= box.titleBottom - 1e-9);
    assert.ok(y >= box.toolbar.y + 70 * box.scale - 1e-9);
    if (searching) assert.ok(y >= box.search[1] + 30 + 45 * S);
    assert.ok(y + h + Tokens.POPUP.clearance * S <= 540 - footerHelpers.footerTop(1300) * S + 1e-9);
    assert.deepEqual([x, w], [300, 1320]);
    for (const [width, height] of [[960, 540], [1600, 900], [2560, 1440]]) for (const dpr of [1, 2, 3]) {
      const k = Math.min(width / 1920, height / 1080) * dpr;
      assert.ok((y + h) * k <= height * dpr);
    }
  }
  const bg = layout.backgroundPickerBox();
  assert.deepEqual([bg[0], bg[2], bg[3]], [130, 760, 650]);
  assert.ok(bg[1] >= 540 - (Tokens.POPUP.frameY + 1450 / 2 - 132) * S);
  assert.ok(bg[3] >= 280);
  assert.ok(bg[1] + bg[3] + Tokens.POPUP.clearance * S <= 540 - footerHelpers.footerTop(1450, 2) * S + 1e-9);
  assert.match(read("js/student-hall-picker.js"), /UI\.portraitPickerLayout\(view, searching\)\.list/);
  assert.match(read("js/student-hall.js"), /UI\.backgroundPickerBox\(\)/);
});
test("the memorial modal remains stopped: even tightly stacked full rows cannot clear the shared footer", () => {
  const S = 1920 / 2960, limit = 540 - (footerHelpers.footerTop(1450, 2) + Tokens.POPUP.clearance) * S;
  const titleBottom = 540 - (Tokens.POPUP.frameY + 1450 / 2 - (64 + 52 + 6)) * S;
  const candidate = 93 + (202.5 + 10 + 88) * .39 + 8 + Tokens.SMALL_BUTTON.h * S;
  const queue = 119 + Tokens.SMALL_BUTTON.h * S;
  // A generous lower bound: no inter-panel gaps, shortest name strip, search only its native body's height.
  for (const searching of [false, true]) {
    const bottom = titleBottom + 140 * (1660 / 2840) + (searching ? 90 * S : 0) + 58 + candidate + 15 + 15 + queue + 5;
    assert.ok(bottom > limit, `${searching}: ${bottom} exceeds ${limit}`);
  }
  assert.match(read("js/student-hall-picker.js"), /memorial \? \{ retainedFooter: true \}/);
  assert.match(read("js/student-hall-picker.js"), /queuePanel\(layer, \[115, 720, 1685, 195\]/);
});
const HALL_FOOTERS = [["popup.cancel", "hall.savePlaylist"], ["hall.doneAdding"], ["popup.cancel"],
  ["popup.cancel", "hall.useBackground"], ["hall.keepEditing", "hall.discard", "hall.saveLeave"]];
for (const locale of ["en-US", "th-TH", "zh-CN", "zh-TW", "ja-JP", "ko-KR"]) test(`Hall footer captions use only named Thai exceptions and preserve every character in ${locale}`, () => {
  const web = JSON.parse(readSync(new URL(`${locale}.json`, LOCALE_DIR), "utf8")).webui;
  document.documentElement.lang = locale;
  for (const keys of [...HALL_FOOTERS].sort((a, b) => Math.max(...b.map((key) => key.split(".").reduce((v, k) => v[k], web).length)) - Math.max(...a.map((key) => key.split(".").reduce((v, k) => v[k], web).length)))) {
    const captions = keys.map((key) => key.split(".").reduce((value, part) => value[part], web));
    const site = keys[0] === "hall.doneAdding" ? "hall.doneAdding" : keys[0] === "hall.keepEditing" ? "hall.unsavedChanges" : "hall.modal";
    const widgets = captions.map(() => ({}));
    const fits = footerHelpers.footerLabels(widgets, captions, { site });
    assert.deepEqual(fits.map(f=>f.fs), footerHelpers.footerFit(captions, { site }).map(f=>f.fs));
    assert.equal(new Set(fits.map((f) => f.fs)).size, 1);
    for (const [i, fit] of fits.entries()) {
      const exceptions = locale === "th-TH" ? { "hall.doneAdding": 44, "hall.keepEditing/hall.discard/hall.saveLeave": 36 } : {};
      if (exceptions[keys.join("/")]) {
        const before = NGUI.fitGroup(captions, 72, 48, 400, 2, { maxH: 140 })[0].fs;
        assert.ok(fit.fs >= exceptions[keys.join("/")] && fit.fs >= before, "Named site and Thai floor; never smaller than before");
      }
      else assert.ok(fit.fs >= Tokens.FOOTER.caption.fsMin, `${locale} ${keys.join("/")} needs ${fit.fs}`);
      assert.ok(fit.fs <= Tokens.FOOTER.caption.fs && !fit.overflow);
      assert.ok(fit.lines * NGUI.fontSizeFor(fit.text, fit.fs) * NGUI.LINE_PITCH <= 156);
      assert.equal(fit.text.replace(/\s/g, ""), captions[i].replace(/\s/g, ""));
      for (const line of fit.text.split("\n")) assert.ok(NGUI.glyphWidth(line, fit.fs) <= 472);
    }
  }
});

const hallUiSource = read("js/student-hall-ui.js");
const hallControls = new Function("NGUI", "Buttons", "Popup", "ready", "S", "SMALL_BUTTON", "BUTTON", "BUTTON_FLOOR", "captionMinimum", `${hallUiSource.slice(
  hallUiSource.indexOf("export function buttonBox"), hallUiSource.indexOf("export async function caption"))
  .replaceAll("export ", "")}; return {buttonBox, buttonSize, button};`)(
    { ...NGUI, render: async (tree, host, options) => { host.tree = tree; host.origin = options.origin; return {}; } },
    { smallButton }, { hit: () => ({ dataset: {} }) }, async () => {}, 1920 / 2960, Tokens.SMALL_BUTTON, Tokens.CONTROL.buttonS, Tokens.TYPE.body.min, Tokens.captionMinimum);
test("Hall small buttons retain centres and widths, use the minimum width and token height", async () => {
  const S = 1920 / 2960, B = Tokens.SMALL_BUTTON;
  for (const box of [[730, 560, 460, 70], [0, 0, 100, 66], [100, 200, 300, 68]]) {
    const placed = hallControls.buttonBox(box);
    assert.equal(placed[0] + placed[2] / 2, box[0] + box[2] / 2);
    assert.equal(placed[1] + placed[3] / 2, box[1] + box[3] / 2);
    assert.equal(placed[2], Math.max(box[2], B.minW * S)); assert.equal(placed[3], B.h * S);
    const host = {}, hit = await hallControls.button(host, "FirstRun", "Add Portraits", box, () => {}, { enabled: false });
    assert.equal(hit.disabled, true);
    assert.equal(bodyOf(host.tree)?.h_ || NGUI.find(host.tree, "BG (1)").widgets[0].h_, B.h);
    assert.deepEqual([labelOf(host.tree).w_, labelOf(host.tree).h_], [placed[2] / S - B.boxInsetW, B.h - B.boxInsetH]);
    assert.deepEqual(host.origin, [box[0] + box[2] / 2, box[1] + box[3] / 2]);
    assert.ok(labelOf(host.tree).fontSize >= B.fsMin && labelOf(host.tree).fontSize <= B.fs);
  }
});
test("portrait rows and their virtual strips grow together for SMALL_BUTTON, keeping artwork and names intact", () => {
  const layout = modalLayoutFor(JSON.parse(readSync(new URL("en-US.json", LOCALE_DIR), "utf8")).webui);
  const row = layout.portraitRowLayout(), S = 1920 / 2960;
  const maxBar = 22 + 2 * 34 * 1.2;
  near(row.height, 138 + (300 + 10 + maxBar) * .46 + 8 + Tokens.CONTROL.buttonS.h * S + Tokens.SPACE.m * S);
  assert.equal(row.pitch, row.height + 4);
  assert.match(read("js/student-hall-picker.js"), /groups.length, rowLayout.pitch/);
  assert.match(read("js/student-hall-picker.js"), /rowPlate\(rowHost, \[1320, rowLayout.height\]/);
  assert.match(read("js/student-hall-picker.js"), /\[rowLayout.stripX, 0, rowLayout.stripW, rowLayout.height\]/);
});
test("portrait Add and Select keep horizontal centres and widths below both one- and two-line names", async () => {
  const source = read("js/student-hall-ui.js"), S = 1920 / 2960;
  let drawn;
  const draw = async (host, name, caption, box, action, opts) => { drawn = { box: hallControls.buttonBox(box), opts }; };
  for (const barH of [88, 22 + 2 * 34 * 1.2]) for (const fitAdd of [false, true]) {
    const y = -(300 + 10 + barH / 2), x = y * Math.tan(Math.PI / 18) + 4;
    const bar = { pos: [x, y], widgets: [{ sprite: "Common_Bg_Raius10px", w_: 298, h_: barH }] }, thumb = { widgets: [{ clipImage: "image" }] };
    const card = new Function("Popup", "NGUI", "document", "pictureReadiness", "hitButton", "observePicture", "button", "retainedButton", "buttonSize", "retainedButtonSize", "BUTTON", "S", "T",
      `${source.slice(source.indexOf("export async function card("), source.indexOf("export const MEMORIAL_QUEUE_PITCH")).replaceAll("export ", "")}; return card;`)(
      { listCard: () => ({ children: [bar] }) }, { find: (_, name) => name === "Bar" ? bar : thumb, render: async () => ({}) },
      { createElement: () => ({ style: {} }) }, () => {}, () => ({ dataset: {}, style: {} }), () => {}, draw,
      () => assert.fail("portrait still retained"), hallControls.buttonSize, () => {}, Tokens.CONTROL.buttonS, S, (key) => key);
    const row = modalLayoutFor({}).portraitRowLayout();
    await card({ append() {} }, { key: "x", full: "Name" }, { x: row.cardX, y: row.cardY, kind: "spr", scale: .46, onAdd() {}, fitAdd, addEnabled: false });
    const [left, top, w, h] = drawn.box;
    assert.equal(h, Tokens.SMALL_BUTTON.h * S);
    assert.ok(Math.abs(left + w / 2 - (row.cardX + x * .46)) < 1e-9);
    assert.equal(w, Tokens.SMALL_BUTTON.minW * S);
    assert.ok(Math.abs(top - (row.cardY + (300 + 10 + barH) * .46 + 8)) < 1e-9);
    assert.ok(top + h <= row.height - Tokens.SPACE.m * S + 1e-9, "button clears the painted scrollbar band");
    assert.ok(left >= 0 && left + w <= 220, "hit box stays in its virtual cell");
    assert.equal(row.cardX + row.stripX, 105 + 240, "stage horizontal centre unchanged");
    assert.equal(row.cardY - 600 * .46 / 2, 0, "artwork stays in the row");
    assert.equal(drawn.opts.enabled, false);
  }
});
for (const locale of ["en-US", "th-TH", "zh-CN", "zh-TW", "ja-JP", "ko-KR"]) test(`Hall button captions fit SMALL_BUTTON without cutting in ${locale}`, () => {
  const web = JSON.parse(readSync(new URL(`${locale}.json`, LOCALE_DIR), "utf8")).webui;
  document.documentElement.lang = locale;
  const B = Tokens.SMALL_BUTTON, S = 1920 / 2960;
  for (const [keys, width, bodyH = B.h] of [[["hall.addPortraits", "hall.adjust"], 460], [["hall.scaleReset"], 156, 104],
    [["hall.remove"], 190], [["hall.add", "hall.added", "import.select"], (320-22)*.46],
    [["hall.add", "hall.added"], (720-22)*.39]]) {
    const captions = keys.map((key) => key.split(".").reduce((value, part) => value[part], web));
    const fits = NGUI.fitGroup(captions, B.fs, B.fsMin, Math.max(B.minW, width / S) - B.fitInset, 2, { maxH: bodyH - B.boxInsetH });
    const size = bodyH === B.h ? hallControls.buttonSize(captions, width) : fits[0].fs;
    assert.equal(new Set(fits.map((f) => f.fs)).size, 1);
    for (const [i, f] of fits.entries()) {
      assert.equal(f.fs, size); if (locale === "th-TH" && keys[0] === "hall.scaleReset") assert.equal(f.fs, 30, "Named Thai Reset exception");
      else assert.ok(f.fs >= B.fsMin && f.fs <= B.fs, `${locale} ${keys.join("/")} needs ${f.fs}`);
      assert.ok(!f.overflow); assert.ok(f.lines * NGUI.fontSizeFor(f.text, f.fs) * NGUI.LINE_PITCH <= bodyH - B.boxInsetH);
      for (const line of f.text.split("\n")) assert.ok(NGUI.glyphWidth(line, f.fs) <= Math.max(B.minW, width / S) - B.fitInset);
      assert.equal(f.text.replace(/\s/g, ""), captions[i].replace(/\s/g, ""));
    }
  }
  // The first-run memorial button clears its existing empty/missing caption at the same centre.
  for (const key of ["empty", "missing"]) {
    const fit = NGUI.fitText(web.hall[key], 40, 26, 900 / S, 3);
    const captionBottom = 500 + fit.fs * 1.25 * fit.lines * S / 2;
    assert.ok(captionBottom <= hallControls.buttonBox([730, 560, 460, 70])[1]);
  }
});

test("Hall first-run body and caption bounds scale together at supported window sizes and densities", () => {
  const box = hallControls.buttonBox([730, 560, 460, 70]), S = 1920 / 2960, B = Tokens.SMALL_BUTTON;
  for (const [w, h] of [[960, 540], [1600, 900], [2560, 1440]]) for (const dpr of [1, 2, 3]) {
    const scale = Math.min(w / 1920, h / 1080) * dpr;
    assert.ok(box[0] * scale >= 0 && (box[0] + box[2]) * scale <= w * dpr);
    assert.ok(box[1] * scale >= 0 && (box[1] + box[3]) * scale <= h * dpr);
    assert.ok((B.h - B.boxInsetH) * S * scale < box[3] * scale);
    assert.ok((box[2] / S - B.boxInsetW) * S * scale < box[2] * scale);
  }
});


// Design system, the Hall's toolbars and selectors (student-hall-ui.js): a Hall button is a buttonS control, buttons of one row share
// one height and one caption size, and the portrait picker and its rows are stacks laid out flush.
for (const locale of ["en-US", "zh-CN"]) test(`Hall buttons are buttonS controls with one caption size per row, and the picker and its rows are stacks in ${locale}`, () => {
  const web = JSON.parse(readSync(new URL(`${locale}.json`, LOCALE_DIR), "utf8")).webui, S = 1920 / 2960, C = Tokens.CONTROL.buttonS;
  document.documentElement.lang = locale;
  for (const box of [[730, 560, 460, 70], [0, 0, 100, 66], [264, 74, 300, 100]]) {
    const placed = hallControls.buttonBox(box);
    assert.equal(placed[3], C.h * S, "a buttonS control's height");
    assert.ok(placed[2] >= C.minW * S, "its least width");
  }
  assert.equal(hallControls.buttonBox([0, 0, 300, 100], 104 * S)[3], 104 * S, "a site whose neighbour sets the height keeps it");
  // the buttons of one row share one size, within the body role's range
  for (const [keys, width] of [[["hall.addPortraits", "hall.adjust"], 460], [["hall.add", "hall.added", "import.select"], (320 - 22) * .46], [["hall.remove"], 190]]) {
    const captions = keys.map((key) => key.split(".").reduce((value, part) => value[part], web));
    const size = hallControls.buttonSize(captions, width), fits = NGUI.fitGroup(captions, C.fs, Tokens.TYPE.body.min, Math.max(C.minW, width / S) - Tokens.SMALL_BUTTON.fitInset, 2, { maxH: C.h - Tokens.SMALL_BUTTON.boxInsetH });
    assert.equal(new Set(fits.map((f) => f.fs)).size, 1, `${locale} ${keys}: one size`);
    assert.equal(size, fits[0].fs);
    assert.ok(size >= Tokens.TYPE.body.min && size <= C.fs);
  }
  // the picker: toolbar bar, search field (when open) and list are one stack, flush, from the title's foot
  const layout = modalLayoutFor(web);
  for (const searching of [false, true]) {
    const box = layout.portraitPickerLayout({ sort: "name" }, searching), barTop = box.toolbar.y - 70 * box.scale, barBottom = box.toolbar.y + 70 * box.scale;
    near(barTop, box.titleBottom);
    if (searching) { near(box.search[1], barBottom); near(box.list[1], box.search[1] + 60); } else near(box.list[1], barBottom);
    assert.deepEqual([box.toolbar.x, box.toolbar.width, box.search[0], box.search[2], box.list[0], box.list[2]], [300, 1320, 300, 1320, 300, 1320]);
  }
  // the portrait row: the artwork down to its name strip, 8 px, a buttonS body, then the scrollbar's 20-unit band (SPACE.m)
  const row = layout.portraitRowLayout(), strip = 22 + 2 * 34 * 1.2;
  near(row.height, row.cardY + (300 + 10 + strip) * .46 + 8 + C.h * S + Tokens.SPACE.m * S);
  assert.equal(row.pitch, row.height + 4);
});

test("caption exceptions are exactly the three approved sites and apply only in Thai", () => {
  assert.deepEqual({...Tokens.CAPTION_EXCEPTIONS}, {
    "hall.doneAdding": {"th-TH":44}, "hall.unsavedChanges": {"th-TH":36}, "hall.scaleReset": {"th-TH":30},
  });
  for (const locale of ["en-US","th-TH","zh-CN","zh-TW","ja-JP","ko-KR"]) {
    assert.equal(Tokens.captionMinimum("hall.other",locale,48),48);
    for (const [site, minimum] of [["hall.doneAdding",48],["hall.unsavedChanges",48],["hall.scaleReset",34]])
      if(locale!=="th-TH") assert.equal(Tokens.captionMinimum(site,locale,minimum),minimum);
  }
});
test("Thai footer exceptions cannot authorize another footer or another locale", () => {
  document.documentElement.lang="th-TH";
  const web=JSON.parse(readSync(new URL("th-TH.json",LOCALE_DIR),"utf8")).webui;
  for(const [site,captions] of [["hall.doneAdding",[web.hall.doneAdding]],["hall.unsavedChanges",[web.hall.keepEditing,web.hall.discard,web.hall.saveLeave]]]) {
    assert.throws(()=>footerHelpers.footerFit(captions,{site:"hall.other",width:400}),RangeError);
    assert.throws(()=>footerHelpers.footerFit(captions,{site,locale:"en-US",width:400}),RangeError);
    assert.doesNotThrow(()=>footerHelpers.footerFit(captions,{site,width:400}));
  }
  assert.match(read("js/student-hall-picker.js"), /captionSite: onReplace \? "hall.portraitReplacement" : "hall.doneAdding"/);
  assert.match(read("js/student-hall.js"), /captionSite: "hall.unsavedChanges"/);
  assert.match(popupSource,/popupFrame\(title, w, height, \{ ok, cancel, buttons, close: dismissable, captionSite \}\)/);
});
for (const locale of ["en-US","th-TH","zh-CN","zh-TW","ja-JP","ko-KR"]) test(`Reset renders with its named caption allowance in ${locale}`, async () => {
  document.documentElement.lang=locale;
  const web=JSON.parse(readSync(new URL(`${locale}.json`,LOCALE_DIR),"utf8")).webui,host={};
  const row=104*1920/2960;
  await hallControls.button(host,"HallPortraitScaleReset",web.hall.scaleReset,[264,74,156,row],()=>{}, {captionSite:"hall.scaleReset",height:row});
  const label=labelOf(host.tree);
  assert.ok(Math.abs((bodyOf(host.tree)?.h_ || NGUI.find(host.tree, "BG (1)").widgets[0].h_) - 104) < 1e-9, "the body is the slider body's height");
  assert.equal(host.origin[1], 74 + row / 2, "centred on the slider's row");
  assert.ok(label.fontSize >= (locale==="th-TH"?30:34));
  if(locale==="th-TH") {
    assert.equal(label.fontSize,30);
    await assert.rejects(hallControls.button({},"Other",web.hall.scaleReset,[264,74,156,88.216],()=>{}),RangeError);
  }
  assert.equal(label.text.replace(/\s/g,""),web.hall.scaleReset.replace(/\s/g,""));
  assert.match(read("js/student-hall.js"),/captionSite: "hall.scaleReset"/);
});
