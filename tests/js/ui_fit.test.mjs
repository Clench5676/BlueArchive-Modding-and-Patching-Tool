// Text fit of the Batch Import header and name plate, the option rows, the roster's cards and the Show in Explorer button, in
// every shipped language: the real locale strings through the fit logic of web/js/ngui.js against the prefabs' numbers (a
// stand-in canvas, see helpers/fit_canvas.mjs: what is asserted is the logic, not a font).
import assert from "node:assert/strict";
import { test } from "node:test";
import { LOCALES, installFitPage, text } from "./helpers/fit_canvas.mjs";
import { makeImportLayouts } from "./import_prefabs.mjs";

const page = installFitPage();
const NGUI = await import("../../src/ba_modding_toolkit/web/js/ngui.js");
const { importArtwork } = await import("../../src/ba_modding_toolkit/web/js/import-layout.js");
const { S } = await import("../../src/ba_modding_toolkit/web/js/util.js");

const { find, fontSizeFor, glyphWidth, LINE_PITCH } = NGUI;
const lines = (value) => String(value).split("\n");
const widest = (value, fs) => Math.max(...lines(value).map((line) => glyphWidth(line, fs)));
// the box the lines take, at NGUI's pitch
const stack = (value, fs) => lines(value).length * fontSizeFor(value, fs) * LINE_PITCH;
const artwork = () => importArtwork({ ...NGUI, translate: (key) => key }, makeImportLayouts());

// --- UI-9: the Import State filter's caption (Items SortBtn/Btn_Normal/Type_2/Text: 336 wide, 56, body 456 x 116) ---
const STATES = ["ready", "convert", "update", "library", "installed", "blocked", "done"];
const filterCaptions = (locale, n) => [text(locale, "seg.all"), ...STATES.map((state) => text(locale, `import.state.${state}`))]
  .map((state) => text(locale, "import.filterCount", { state, n }));

// the largest size (steps of 2 down from the prefab's 56) at which the caption fits on one line, or on two split at a space
function bestSize(caption, room, budget) {
  for (let fs = 56; fs >= 20; fs -= 2) {
    const words = caption.split(" "), splits = [caption, ...words.slice(1).map((_, k) => `${words.slice(0, k + 1).join(" ")}\n${words.slice(k + 1).join(" ")}`)];
    if (splits.some((split) => widest(split, fs) <= room && stack(split, fs) <= budget)) return fs;
  }
  return 20;
}

for (const locale of LOCALES) test(`the Import State caption wraps inside the 116-high body, is never ellipsized and keeps its size in ${locale}`, () => {
  page.lang = locale;
  const art = artwork();
  for (const n of [12, 999]) for (const [filtered, caption] of filterCaptions(locale, n).flatMap((c) => [[false, c], [true, c]])) {
    const tree = art.header(1272 / S, "List", caption, filtered, true, false);
    const type = find(tree, "SortBtn/Btn_Normal/Type_2"), node = find(type, "Text"), w = node.widgets.find((x) => "text" in x);
    const body = find(tree, "SortBtn/Btn_Normal").widgets.find((x) => "sprite" in x);
    assert.equal(body.h_, 116);
    assert.ok(!w.ellipsis, `${locale} ${caption}: ellipsized`);
    assert.ok(lines(w.text).length <= 2, `${locale} ${caption}: ${lines(w.text).length} lines`);
    assert.equal(w.text.replace(/\s/g, ""), caption.replace(/\s/g, ""), "every character kept");
    assert.ok(widest(w.text, w.fontSize) <= 336 + 1e-9, `${locale} ${JSON.stringify(w.text)} is ${widest(w.text, w.fontSize)} wide in 336`);
    // the lines' box, round the label's offset, stays inside the body
    const centre = type.pos[1] + node.pos[1], budget = body.h_ - 2 * Math.abs(centre);
    assert.ok(Math.abs(centre) + stack(w.text, w.fontSize) / 2 <= body.h_ / 2 + 1e-9, `${locale} ${JSON.stringify(w.text)}: ${stack(w.text, w.fontSize)} tall`);
    assert.ok(w.h_ >= stack(w.text, w.fontSize) - 1e-9);
    // the prefab's own range is 56 down to 70 %: 39.2. Under it only for the one caption that cannot fit above it, never far
    assert.ok(w.fontSize >= (n === 12 ? 39.2 : 36), `${locale} ${JSON.stringify(w.text)} shrinks to ${w.fontSize}`);
    // wrapping comes before shrinking: no step of the size is given away where the words allow a larger one
    if (locale !== "zh-CN" && locale !== "zh-TW" && locale !== "ja-JP") {
      assert.ok(w.fontSize >= bestSize(caption, 336, budget) - 4, `${locale} ${JSON.stringify(w.text)} at ${w.fontSize}, ${bestSize(caption, 336, budget)} fits`);
    }
  }
});

test("a caption that fits on one line keeps the prefab's size, a longer one wraps before it shrinks", () => {
  page.lang = "en-US";
  const art = artwork(), size = (caption) => art.header(1272 / S, "List", caption, false, true, false)
    .children.find((c) => c.name === "SortBtn").children[0].children.find((c) => c.name === "Type_2").children[0].widgets[0];
  assert.equal(size("Done (12)").fontSize, 56);
  const long = size("Pending Conversion (999)");
  assert.equal(lines(long.text).length, 2);
  assert.ok(long.fontSize > 36, `${long.fontSize}`);
});
