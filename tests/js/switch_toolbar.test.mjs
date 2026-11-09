// BL-131: Switch 3D Model's toolbar with its "Show other students" checkbox, laid out the way the student roster's toolbar fits
// its Group Alts checkbox (roster-widgets.js toolbarFit): the checkbox first, the three segments, search, sort and order, in
// every shipped language, longest first (English, Thai), against the locale files' real strings. Text is measured on the fit
// tests' stand-in canvas (helpers/fit_canvas.mjs), so what is asserted is the logic, not a font.
import assert from "node:assert/strict";
import { test } from "node:test";
import { LOCALES, installFitPage, text, webui } from "./helpers/fit_canvas.mjs";

installFitPage();
Object.assign(globalThis.document, { getElementById: () => null, querySelector: () => null });
globalThis.addEventListener = () => {};
globalThis.removeEventListener = () => {};
globalThis.fetch = async () => ({ ok: false, status: 404, json: async () => null });
const JS = "../../src/bmpt/web/js/";
const NGUI = await import(`${JS}ngui.js`);
const Popup = await import(`${JS}popup.js`);
const { CONTROL, SPACE } = await import(`${JS}ui-tokens.js`);
const { S, glyphsOf } = await import(`${JS}util.js`);
const { setStrings } = await import(`${JS}i18n.js`);

const flat = (value, prefix = "webui") => Object.entries(value).flatMap(([key, item]) => typeof item === "object" ? flat(item, `${prefix}.${key}`) : [[`${prefix}.${key}`, item]]);
const SEGMENTS = ["seg.all", "seg.recruit", "seg.noRecruit"], SORTS = ["sort.default", "sort.name"];
const ORDER = [...LOCALES].sort((a, b) => (a === "en-US" ? -2 : a === "th-TH" ? -1 : 0) - (b === "en-US" ? -2 : b === "th-TH" ? -1 : 0));
const list = Popup.listLayout(false, "model").list, RIGHT = list.r - SPACE.m, LEFT = list.l + SPACE.m;
function layoutFor(locale, sortKey = "sort.default") {
  setStrings(locale, Object.fromEntries(flat(webui(locale))));
  document.documentElement.lang = locale;
  return Popup.checkedToolbarFit(RIGHT, { segs: SEGMENTS, sortKey, sortKeys: SORTS, label: text(locale, "model.showOthers") });
}

for (const locale of ORDER) test(`the checkbox toolbar fits the list's width in ${locale}`, () => {
  const label = text(locale, "model.showOthers");
  assert.ok(label && label !== "model.showOthers");
  for (const sortKey of SORTS) {
    const lay = layoutFor(locale, sortKey), { boxes } = lay;
    assert.equal(lay.fits, true, `${locale}: the checkbox stands after the list's left edge (steps down to the roster's last)`);
    // checkbox, segments, search, sort, order: left to right, SPACE.l between the groups and SPACE.m among the tools
    assert.equal(boxes.length, 5);
    assert.ok(lay.cbLeft >= LEFT + SPACE.l, `${locale}: ${lay.cbLeft} against ${LEFT + SPACE.l}`);
    for (let i = 1; i < boxes.length; i++) {
      const gap = boxes[i].x - boxes[i - 1].x - boxes[i - 1].w;
      assert.ok(Math.abs(gap - (i < 3 ? SPACE.l : SPACE.m)) < 1e-9, `${locale}: gap ${i} is ${gap}`);
    }
    assert.ok(Math.abs(boxes.at(-1).x + boxes.at(-1).w - RIGHT) < 1e-9, "the order button ends at the list's right padding");
    assert.equal(boxes[0].x, lay.cbLeft);
    assert.equal(lay.boxes[1].w, 3 * lay.segW);
    // the checkbox: the label inside its box, up to two lines, never past the room it was fitted to
    const { cbText, cbW } = lay;
    assert.ok(!cbText.overflow, `${locale}: ${cbText.text}`);
    assert.ok(cbText.lines <= 2 && cbText.fs >= 24);
    for (const line of cbText.text.split("\n")) assert.ok(NGUI.glyphWidth(line, cbText.fs) <= 400 + 1e-9);
    assert.ok(Math.abs(cbW - (131 + glyphsOf(cbText.text, cbText.fs) + 46)) < 1e-9, `${locale}: the box is as wide as its label needs`);
    // the three segments share one size, each caption inside its slot
    assert.equal(new Set(lay.segLabels.map((f) => f.fs)).size, 1, `${locale}: one segment size`);
    for (const f of lay.segLabels) {
      for (const line of f.text.split("\n")) assert.ok(NGUI.glyphWidth(line, f.fs) <= lay.segW - 26 + 1e-9, `${locale}: ${f.text}`);
      assert.ok(f.lines * NGUI.fontSizeFor(f.text, f.fs) * NGUI.LINE_PITCH <= 96 + 1e-9);
    }
    // the sort button is as wide as its longest key, whichever is shown, and holds its caption
    assert.ok(lay.sortW >= 300);
    assert.ok(!lay.sortText.overflow && NGUI.glyphWidth(lay.sortText.text, lay.sortText.fs) <= lay.sortW - 170 + 1e-9);
  }
  // choosing the other key moves nothing
  assert.deepEqual(layoutFor(locale, "sort.default").boxes, layoutFor(locale, "sort.name").boxes);
});

for (const locale of ORDER) test(`the checkbox toolbar stays inside the list and the window at every size in ${locale}`, () => {
  const { boxes } = layoutFor(locale);
  // the default window is 1600 x 1000 (the stage is 1920 x 1080 units scaled to the smaller side); also the smallest and a large one
  for (const [width, height] of [[960, 540], [1600, 1000], [2560, 1440]]) for (const density of [1, 2]) {
    const k = Math.min(width / 1920, height / 1080) * density;
    for (const box of boxes) {
      const left = (width * density) / 2 + box.x * S * k, right = left + box.w * S * k;
      assert.ok(left >= (width * density) / 2 + list.l * S * k - 1e-6, `${locale} ${width}x${height}@${density}: ${left}`);
      assert.ok(right <= (width * density) / 2 + list.r * S * k + 1e-6, `${locale} ${width}x${height}@${density}: ${right}`);
      assert.ok(left >= 0 && right <= width * density);
      // every control keeps its prefab height (116 for the icons and the sort button, 104 for the checkbox and the segments)
      assert.ok([CONTROL.toolbar.h, 116].includes(box.h));
    }
  }
});

// the owner's wording for the new caption (and the other languages in the same short style), the same key in every file
const WORDING = { "zh-CN": "显示其他角色", "zh-TW": "顯示其他角色", "en-US": "Show other students", "ja-JP": "他の生徒を表示",
  "ko-KR": "다른 학생 표시", "th-TH": "แสดงนักเรียนคนอื่น" };
for (const locale of LOCALES) test(`model.showOthers is in ${locale} with the agreed wording`, () => {
  assert.equal(text(locale, "model.showOthers"), WORDING[locale]);
  assert.equal(Object.keys(WORDING).length, LOCALES.length);
});
