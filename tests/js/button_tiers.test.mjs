// The button size tag of developer previews (BL-173): which preset (XS, S, M, L) a Common_Btn_BG pill is by its height in the
// app's units, the pills that are none of them, the bodies that are no buttons, and the dot that replaces a tag that would
// cover the caption. Run by tests/test_ngui_fit.py's runner with the other JS tests: node --test tests/js/button_tiers.test.mjs
import assert from "node:assert/strict";
import { test } from "node:test";
import { BUTTON_TIERS, CHIP_TIER_LIST, PILL, TAG, buttonTier, chipTier, inkBox, isPill, overlaps, tagBox, tagOf } from "../../src/bmpt/web/js/button-layout.js";
import { CHIP_TIERS, CONTROL, SMALL_BUTTON, XS_BUTTON } from "../../src/bmpt/web/js/ui-tokens.js";

test("the presets are the controls' heights, XS under S under M under L", () => {
  assert.deepEqual(BUTTON_TIERS.map(([tier, control]) => [tier, control.h]), [["XXS", 88], ["XS", 104], ["S", 136], ["M", 162], ["L", 176]]);
  assert.equal(XS_BUTTON.h, CONTROL.buttonXS.h);
  assert.equal(XS_BUTTON.h, CONTROL.toolbar.h, "the game's own toolbar and tab height (UILobby Able_Count/Bg, Common_Filter_Tab_Type1)");
  assert.equal(2 * CONTROL.buttonXS.pad, XS_BUTTON.glyphPad);
  assert.equal(XS_BUTTON.pitch - XS_BUTTON.w, XS_BUTTON.gap);
  assert.equal(SMALL_BUTTON.h, CONTROL.buttonS.h, "S is the prefab's small button");
  assert.ok(XS_BUTTON.h < SMALL_BUTTON.h && XS_BUTTON.w < SMALL_BUTTON.w && XS_BUTTON.minW < SMALL_BUTTON.minW);
  assert.ok(XS_BUTTON.fs < SMALL_BUTTON.fs && XS_BUTTON.fsMin < SMALL_BUTTON.fsMin);
});

test("a height is a tier within half a unit, anything else has none", () => {
  for (const [tier, control] of BUTTON_TIERS) {
    assert.equal(buttonTier(control.h), tier);
    assert.equal(buttonTier(control.h + 0.4), tier);
    assert.equal(buttonTier(control.h - 0.4), tier);
    assert.equal(buttonTier(control.h + 1), null);
  }
  for (const height of [52, 65, 85, 120, 140.8, 180]) assert.equal(buttonTier(height), null, String(height));
});

test("only pills are tagged: low, and wider than high", () => {
  assert.ok(isPill(402, 136) && isPill(308, 104) && isPill(348, 162) && isPill(551, 176));
  assert.ok(isPill(191, 85) && isPill(150, 52), "compact bodies are pills too");
  // the cards, thumbnails and square action buttons that draw the same sprite
  for (const [w, h] of [[293, 264], [200, 180], [205, 197], [242, 209], [250, 226], [444, 250], [780, 218], [320, 704], [854, 480]]) {
    assert.ok(!isPill(w, h), `${w} x ${h}`);
    assert.equal(tagOf(w, h), null);
  }
  assert.equal(PILL.maxH, 200);
});

test("a preset pill is tagged with its tier, any other pill with its measured height", () => {
  assert.deepEqual(tagOf(308, 104), { tier: "XS", text: "XS" });
  assert.deepEqual(tagOf(402, 136), { tier: "S", text: "S" });
  assert.deepEqual(tagOf(348, 162), { tier: "M", text: "M" });
  assert.deepEqual(tagOf(551, 176), { tier: "L", text: "L" });
  assert.deepEqual(tagOf(191, 85), { tier: null, text: "85" });
  assert.deepEqual(tagOf(300, 52.4), { tier: null, text: "52" });
});

test("a tag that overlaps the caption's ink is told so, one that clears it is not", () => {
  // a caption centred in a 300 x 100 body, 120 wide and 30 high: its ink starts at x 90
  const body = [0, 0, 300, 100], ink = inkBox(body, 120, 30, "center", "center");
  assert.deepEqual(ink, [90, 35, 120, 30]);
  const tag = tagBox("XS", 0, 0);
  assert.deepEqual(tag, [0, 0, TAG.padX + TAG.perChar * 2, TAG.h]);
  assert.ok(!overlaps(tag, ink), "a short caption leaves the corner free");
  // a caption that fills the body
  const wide = inkBox(body, 296, 70, "center", "center");
  assert.ok(overlaps(tag, wide));
  // left aligned and top aligned captions
  assert.deepEqual(inkBox(body, 100, 20, "left", "flex-start"), [0, 0, 100, 20]);
  assert.deepEqual(inkBox(body, 100, 20, "right", "flex-end"), [200, 80, 100, 20]);
  assert.ok(overlaps(tag, inkBox(body, 100, 20, "left", "flex-start")));
  assert.ok(!overlaps([0, 0, 10, 10], [10, 0, 10, 10]), "boxes that only touch do not overlap");
});

// --- the render: a body in a tree gets a tag in a developer preview, none in a stable build ---
class El {
  children = []; attrs = {}; style = {}; dataset = {}; className = ""; textContent = "";
  appendChild(el) { this.children.push(el); return el; }
  setAttribute(k, v) { this.attrs[k] = v; }
  addEventListener() {}
  getContext() { return { measureText: () => ({ width: 100 }) }; }
}
async function render(t, tree, { tags, scale } = {}) {
  t.mock.method(globalThis, "fetch", async () => ({ ok: true, json: async () => ({ Common: { Test: { width: 1, height: 1 } } }) }));
  t.mock.method(console, "error", () => {});
  const original = globalThis.document;
  globalThis.document = { createElement: () => new El(), documentElement: { lang: "en-US" } };
  try {
    const NGUI = await import("../../src/bmpt/web/js/ngui.js");
    NGUI.setTierTags(tags);
    const host = new El();
    await NGUI.render(tree, host, scale ? { scale } : {});
    return host.children.filter((el) => el.className.startsWith("ngui-tier"));
  } finally { globalThis.document = original; }
}
const pill = (w, h, caption = "OK", { sx = 1, sy = 1 } = {}) => ({ name: "Btn", active: true, pos: [0, 0], scale: [sx, sy], rotZ: 0,
  widgets: [{ sprite: "Common_Btn_BG", atlas: "Common", type: 1, w_: w, h_: h, pivot: 4, depth: 5, color: "#ffffff/1.00", incline: 0, flip: 0 }],
  children: [{ name: "Label", active: true, pos: [0, 0], scale: [1, 1], rotZ: 0, children: [],
    widgets: [{ cls: "UILabel", text: caption, fontSize: 40, color: "#000000/1.00", w_: w - 20, h_: h - 20, pivot: 4, depth: 9, align: 0 }] }] });

test("a developer preview tags each pill with its tier at the body's top left", async (t) => {
  const tags = await render(t, pill(308, 104), { tags: true });
  assert.equal(tags.length, 1);
  assert.equal(tags[0].textContent, "XS");
  assert.equal(tags[0].dataset.tier, "XS");
  assert.equal(tags[0].dataset.h, "104.0");
  assert.match(tags[0].className, /^ngui-tier$/);
  assert.equal(tags[0].style.pointerEvents, undefined, "the CSS makes it transparent to clicks");
});

test("the size is what the body measures on the stage: node scales and the render's scale count", async (t) => {
  assert.equal((await render(t, pill(402, 136), { tags: true }))[0].dataset.tier, "S");
  assert.equal((await render(t, pill(402, 272, "OK", { sy: 0.5 }), { tags: true }))[0].dataset.tier, "S", "node scale");
  // drawn at half the stage scale, the 162-high body is 81 high: no preset
  const small = await render(t, pill(348, 162), { tags: true, scale: 1920 / 2960 / 2 });
  assert.equal(small[0].dataset.tier, "");
  assert.equal(small[0].textContent, "81");
  assert.match(small[0].className, /off/);
});

test("a stable build tags nothing, nor does a body that is no button", async (t) => {
  assert.deepEqual(await render(t, pill(308, 104), { tags: false }), []);
  assert.deepEqual(await render(t, pill(293, 264), { tags: true }), []);
});

test("a caption that reaches the corner turns the tag into a dot that keeps the label", async (t) => {
  // the measuring stand-in gives every caption 100 units: it clears the corner of a wide, tall body and covers that of a small one
  const clear = await render(t, pill(308, 104, "OK"), { tags: true });
  assert.equal(clear[0].textContent, "XS");
  assert.doesNotMatch(clear[0].className, /dot/);
  const covered = await render(t, pill(120, 60, "A caption"), { tags: true });
  assert.match(covered[0].className, /dot/);
  assert.equal(covered[0].textContent, "", "a dot shows no text");
  assert.equal(covered[0].dataset.size, "60");
  const preset = await render(t, pill(160, 104, "A caption"), { tags: true });
  assert.equal(preset[0].dataset.size, "XS 104", "a preset's label names its tier and height");
});

// --- the details panel's narrow actions and the chips (owner, 2026-11-11: both are tagged by their size) ---
test("a body that says it is a button is tagged whatever its shape; a chip by the chips' four sizes", () => {
  // the details panel's five actions are 155 wide and 104 high: narrower than a pill, still buttons
  assert.equal(tagOf(155, 104), null);
  assert.deepEqual(tagOf(155, 104, "button"), { tier: "XS", text: "XS" });
  assert.deepEqual(CHIP_TIER_LIST.map(([tier, chip]) => [tier, chip.h]), [["XS", 42], ["S", 54], ["M", 62], ["L", 72]]);
  for (const [tier, chip] of CHIP_TIER_LIST) {
    assert.equal(chipTier(chip.h), tier);
    assert.deepEqual(tagOf(chip.minW, chip.h, "chip"), { tier, text: tier });
  }
  assert.deepEqual(tagOf(200, 50, "chip"), { tier: null, text: "50" }, "a chip of no size shows its height");
  assert.equal(CHIP_TIERS.S.h, CONTROL.badge.h);
});

const chipBody = (w, h) => ({ name: "Chip", active: true, pos: [0, 0], scale: [1, 1], rotZ: 0, children: [],
  widgets: [{ sprite: "Common_Bg_Raius10px", atlas: "Common", type: 1, w_: w, h_: h, pivot: 4, depth: 5, color: "#ffffff/1.00", incline: 10, flip: 0, tier: "chip" }] });
test("a developer preview tags a chip with its size, marked as a chip", async (t) => {
  const tags = await render(t, chipBody(108, 54), { tags: true });
  assert.equal(tags.length, 1);
  assert.equal(tags[0].textContent, "S");
  assert.match(tags[0].className, /\bchip\b/);
  const narrow = pill(155, 104);
  narrow.widgets[0].tier = "button";
  assert.equal((await render(t, narrow, { tags: true }))[0].dataset.tier, "XS");
  assert.deepEqual(await render(t, chipBody(108, 54), { tags: false }), []);
});
