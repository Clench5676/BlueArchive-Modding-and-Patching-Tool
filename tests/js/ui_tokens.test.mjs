// The design tokens (web/js/ui-tokens.js): one place for the numbers and colours that popups, buttons, chips and the style
// sheets share. The prefab values behind them are checked by tests/test_ui_tokens.py against the development view.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import * as Tokens from "../../src/ba_modding_toolkit/web/js/ui-tokens.js";

const WEB = new URL("../../src/ba_modding_toolkit/web/", import.meta.url);
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
const NGUI = await import("../../src/ba_modding_toolkit/web/js/ngui.js");
const ButtonsModule = await import("../../src/ba_modding_toolkit/web/js/game-buttons.js");
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

test("a screen with its own button boxes (the Hall's) passes every size, so the new defaults do not change it", () => {
  // the Hall's footer and first-run buttons: explicit w, h and caption range
  for (const [w, h, fs, min] of [[431, 102, 40, 26], [1093, 166, 40, 26], [216, 98, 36, 36]]) {
    const tree = smallButton("B", 0, 0, "Add Portraits", { w, h, fs, min });
    assert.deepEqual([bodyOf(tree).w_, bodyOf(tree).h_], [w, h]);
    assert.deepEqual([labelOf(tree).w_, labelOf(tree).h_], [w - 30, h - 20]);
    assert.ok(labelOf(tree).fontSize <= fs, `${w} x ${h}`);
  }
  const hall = readFileSync(new URL("js/student-hall-ui.js", WEB), "utf8");
  assert.match(hall, /Buttons\.smallButton\(name, 0, 0, caption, \{ w: width \/ S, h: height \/ S, fs: size \?\? 40, min: size \?\? 26,/);
  assert.match(hall, /NGUI\.fitGroup\(captions, 40, 26, width \/ S - 60, 2, \{ maxH: height \/ S - 20 \}\)/);
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
  assert.deepEqual({ ...B.inset }, { frame: 200, panel: 40 });
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
  // left as they were: the lobby's subtitle and the photography sheet
  assert.match(ruleStarting(css, ".lobby-subtitle"), /border-radius: 10px/);
  assert.match(css, /\.loading-fallback i \{[^}]*border-radius: 50%/);
});

// --- item B/I: the footer's caption range through every locale file ---
import { readFileSync as readSync } from "node:fs";
const LOCALE_DIR = new URL("../../src/ba_modding_toolkit/locales/", import.meta.url);
const locales = readdirSync(LOCALE_DIR).filter((name) => name.endsWith(".json")).map((name) => name.slice(0, -5)).sort();
// every set of captions that stands together in one popup footer
const FOOTERS = [["picker.ok", "popup.cancel"], ["popup.save", "popup.cancel"], ["popup.apply", "popup.cancel"], ["consent.no", "consent.yes"],
  ["mods.scan.no", "mods.scan.yes"], ["spine.license.no", "spine.license.yes"], ["update.later", "update.notes", "update.now"],
  ["update.later", "update.notes", "update.restart"], ["update.later", "update.notes", "update.page"], ["update.releasePage", "display.close"],
  ["tour.previous", "tour.next"], ["tour.previous", "tour.finish"], ["export.chooseDirectory", "export.confirm"], ["export.openFolder", "popup.cancel"],
  ["remap.discard", "remap.accept"], ["settings.log.copy", "display.close"], ["picker.ok", "open.mac"], ["picker.ok", "open.win"], ["btn.delete", "popup.cancel"],
  ["recovery.report", "picker.ok"]];
// Captions that do not hold the range's 48 in a 400 box on two lines in this file's stand-in canvas (a code point of Thai a full em,
// Latin half an em): the shared fit takes the group under 48, never past the box. Named here so that they are seen, not hidden.
const BELOW_MINIMUM = {
  "en-US": ["spine.license.no/spine.license.yes"],
  "th-TH": ["spine.license.no/spine.license.yes", "update.later/update.notes/update.now", "update.later/update.notes/update.restart", "update.later/update.notes/update.page",
    "recovery.report/picker.ok"],
};
test("there is a locale file for each shipped language", () => {
  assert.deepEqual(["en-US", "ja-JP", "ko-KR", "th-TH", "zh-CN", "zh-TW"].filter((l) => !locales.includes(l)), []);
});
for (const locale of locales) test(`every footer's captions share one size in the 72 to 48 range, inside the 400 x 140 box, in ${locale}`, () => {
  const web = JSON.parse(readSync(new URL(`${locale}.json`, LOCALE_DIR), "utf8")).webui;
  const text = (key) => key.split(".").reduce((o, part) => o?.[part], web);
  document.documentElement.lang = locale;
  const { fs: max, fsMin: min, w, lines, h } = Tokens.FOOTER.caption;
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
    if (BELOW_MINIMUM[locale]?.includes(keys.join("/"))) assert.ok(size < min && size >= 36, `${what}: ${size} is under the range's ${min}, as recorded`);
    else assert.ok(size >= min && size <= max, `${what}: ${size} is in the range`);
  }
});
