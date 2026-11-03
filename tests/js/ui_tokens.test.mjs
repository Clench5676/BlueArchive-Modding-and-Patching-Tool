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
