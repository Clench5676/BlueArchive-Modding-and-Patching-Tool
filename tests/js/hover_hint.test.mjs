// The hover hint (web/js/hover-hint.js): the game's description tooltip (UIDescriptionTooltip/Default) beside a greyed
// control, in every shipped language. The prefab's numbers are the Global client's (2026-04-06 tooltip bundle).
import assert from "node:assert/strict";
import { test } from "node:test";
import { LOCALES, installFitPage, text } from "./helpers/fit_canvas.mjs";

installFitPage();
const { S } = await import("../../src/bmpt/web/js/util.js");
const { hintLayout, hintStyle, hintTree } = await import("../../src/bmpt/web/js/hover-hint.js");

const sprite = (name, w, h, extra = {}) => ({ cls: "UISprite", sprite: name, atlas: "Common", w_: w, h_: h, pivot: 4, depth: 5, color: "#ffffff/1.00", flip: 0, ...extra });
const PREFAB = { name: "UIDescriptionTooltip", pos: [0, 0], children: [
  { name: "Default", pos: [0, 0], children: [
    { name: "BG", pos: [22, 28], widgets: [sprite("Common_Tooltip_Bg", 834, 255, { type: 1, anchors: { bottomAnchor: [0, -60], topAnchor: [1, 46] } })], children: [
      { name: "Arrow_L", pos: [-402, 9], rotZ: 0, widgets: [sprite("Common_Tooltip_Arrow", 46, 62, { type: 0, depth: 7, color: "#2be5fa/1.00", anchors: { leftAnchor: [0, -8], rightAnchor: [0, 38] } })] },
    ] },
  ] },
  { name: "Label_Info", pos: [-333, 13], widgets: [{ cls: "UILabel", text: "info", fontSize: 42, w_: 86, h_: 52, pivot: 0, depth: 22, color: "#6a7b8f/1.00" }] },
] };

test("the hint takes the description tooltip's numbers", () => {
  const style = hintStyle(PREFAB);
  assert.deepEqual([style.padX, style.maxW, style.padTop, style.padBottom, style.tipOut, style.tipIn, style.fs, style.color],
    [62, 710, 46, 60, 8, 38, 42, "#6a7b8f/1.00"]);
});

for (const locale of LOCALES) test(`Model not adapted fits the tooltip beside either control (${locale})`, () => {
  globalThis.document.documentElement.lang = locale;
  const style = hintStyle(PREFAB), words = text(locale, "model.unfit");
  // the details panel's Apply (left of the screen) and the screen's Apply card (its right end)
  for (const [button, side] of [[[120, 900, 200, 60], "right"], [[1700, 980, 200, 70], "left"]]) {
    const hint = hintLayout(words, button, 1920, 1080, style);
    assert.equal(hint.side, side);
    assert.ok(hint.fs <= 42 && hint.fs >= 30, `${locale}: ${hint.fs}`);
    assert.ok(hint.textW <= style.maxW, `${locale}: ${hint.textW}`);
    const [cx, cy] = hint.origin, half = (hint.w * S) / 2;
    assert.ok(cx - half >= 0 && cx + half <= 1920, `${locale}: inside the host`);
    // the arrow's tip, 8 units past the box, stops 12 units short of the control
    const tip = cx + (hint.tipX + (side === "right" ? -23 : 23)) * S;
    const edge = side === "right" ? button[0] + button[2] : button[0];
    assert.ok(Math.abs(Math.abs(tip - edge) - 12 * S) < 1, `${locale}: ${tip} vs ${edge}`);
    assert.ok(Math.abs(cy - (button[1] + button[3] / 2)) < 1 && hint.tipY === 0);
    const tree = hintTree(hint, style);
    const [bg, arrow, label] = tree.children;
    assert.deepEqual([bg.widgets[0].sprite, bg.widgets[0].w_, bg.widgets[0].h_], ["Common_Tooltip_Bg", hint.w, hint.h]);
    assert.deepEqual([arrow.widgets[0].sprite, arrow.widgets[0].flip], ["Common_Tooltip_Arrow", side === "right" ? 0 : 1]);
    assert.equal(label.widgets[0].text, hint.text);
    assert.ok(label.widgets[0].w_ >= hint.textW);
  }
});

test("a box with no room on either side stays inside the host, its arrow on the control's level", () => {
  const style = hintStyle(PREFAB);
  const hint = hintLayout("A reason long enough to need most of the tooltip's room on two lines", [900, 1050, 120, 30], 1100, 1080, style);
  const half = (hint.w * S) / 2, halfH = (hint.h * S) / 2;
  assert.ok(hint.origin[0] - half >= 0 && hint.origin[0] + half <= 1100);
  assert.ok(hint.origin[1] + halfH <= 1080 && Math.abs(hint.tipY) <= hint.h / 2);
});
