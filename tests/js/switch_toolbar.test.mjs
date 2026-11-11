// The list popups' toolbar (BL-176: every list popup the L class): the student roster's toolbar fitted by its own steps
// (roster-widgets.js toolbarFit), the three segments, search, sort and order, without a checkbox (Switch 3D Model's "Show
// other students", BL-131, stands under the details, the toolbar has no room for it at this width), in every shipped language, longest first (English, Thai), against the locale files' real strings. Text is measured on the fit
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
const { S } = await import(`${JS}util.js`);
const { setStrings } = await import(`${JS}i18n.js`);

const flat = (value, prefix = "webui") => Object.entries(value).flatMap(([key, item]) => typeof item === "object" ? flat(item, `${prefix}.${key}`) : [[`${prefix}.${key}`, item]]);
const SEGMENTS = ["seg.all", "seg.recruit", "seg.noRecruit"], SORTS = ["sort.default", "sort.name"];
const ORDER = [...LOCALES].sort((a, b) => (a === "en-US" ? -2 : a === "th-TH" ? -1 : 0) - (b === "en-US" ? -2 : b === "th-TH" ? -1 : 0));
const list = Popup.listLayout(false, "model").list, RIGHT = list.r - SPACE.m, LEFT = list.l + SPACE.m;
function layoutFor(locale, sortKey = "sort.default") {
  setStrings(locale, Object.fromEntries(flat(webui(locale))));
  document.documentElement.lang = locale;
  return Popup.checkedToolbarFit(RIGHT, { segs: SEGMENTS, sortKey, sortKeys: SORTS });
}

for (const locale of ORDER) test(`the list toolbar fits the list's width in ${locale}`, () => {
  for (const sortKey of SORTS) {
    const lay = layoutFor(locale, sortKey), { boxes } = lay;
    assert.equal(lay.fits, true, `${locale}: the segments stand after the list's left edge (steps down to the roster's last)`);
    // segments at the list's left padding, search, sort, order at its right (owner, 2026-11-11, BL-176): at least SPACE.l
    // between the groups and SPACE.m among the tools
    assert.equal(boxes.length, 4);
    assert.ok(Math.abs(lay.cbLeft - LEFT) < 1e-9, `${locale}: ${lay.cbLeft} against ${LEFT}`);
    for (let i = 1; i < boxes.length; i++) {
      const gap = boxes[i].x - boxes[i - 1].x - boxes[i - 1].w;
      if (i < 2) assert.ok(gap >= SPACE.l - 1e-9, `${locale}: gap ${i} is ${gap}`);
      else assert.ok(Math.abs(gap - SPACE.m) < 1e-9, `${locale}: gap ${i} is ${gap}`);
    }
    assert.ok(Math.abs(boxes.at(-1).x + boxes.at(-1).w - RIGHT) < 1e-9, "the order button ends at the list's right padding");
    assert.equal(boxes[0].x, lay.cbLeft);
    assert.equal(lay.boxes[0].w, 3 * lay.segW);
    assert.equal(lay.cbText, null, "no checkbox");
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

for (const locale of ORDER) test(`the list toolbar stays inside the list and the window at every size in ${locale}`, () => {
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
