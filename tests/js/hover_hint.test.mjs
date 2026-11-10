// The hover hint (web/js/hover-hint.js): the game's description tooltip box (UIDescriptionTooltip/Default) above a
// control, in every shipped language. The prefab's numbers are the Global client's (2026-04-06 tooltip bundle).
import assert from "node:assert/strict";
import { test } from "node:test";
import { LOCALES, installFitPage, text } from "./helpers/fit_canvas.mjs";

installFitPage();
const { S } = await import("../../src/bmpt/web/js/util.js");
const { BOX_INSET, hintHost, hintLayout, hintStyle, hintTree } = await import("../../src/bmpt/web/js/hover-hint.js");

const sprite = (name, w, h, extra = {}) => ({ cls: "UISprite", sprite: name, atlas: "Common", w_: w, h_: h, pivot: 4, depth: 5, color: "#ffffff/1.00", flip: 0, ...extra });
const PREFAB = { name: "UIDescriptionTooltip", pos: [0, 0], children: [
  { name: "Default", pos: [0, 0], children: [
    { name: "BG", pos: [22, 28], widgets: [sprite("Common_Tooltip_Bg", 834, 255, { type: 1, anchors: { bottomAnchor: [0, -60], topAnchor: [1, 46] } })], children: [] },
  ] },
  { name: "Label_Info", pos: [-333, 13], widgets: [{ cls: "UILabel", text: "info", fontSize: 42, w_: 86, h_: 52, pivot: 0, depth: 22, color: "#6a7b8f/1.00" }] },
] };

test("the hint takes the description tooltip's numbers", () => {
  const style = hintStyle(PREFAB);
  assert.deepEqual([style.padX, style.maxW, style.padTop, style.padBottom, style.fs, style.color], [62, 710, 46, 60, 42, "#6a7b8f/1.00"]);
});

for (const locale of LOCALES) for (const key of ["model.unfit", "model.crossCharacter", "model.beyondFit"]) test(`${key} fits the tooltip above the control (${locale})`, () => {
  globalThis.document.documentElement.lang = locale;
  const style = hintStyle(PREFAB), words = text(locale, key);
  // the details panel's Apply (a row near the bottom) and the screen's Apply card (the row's right end)
  for (const button of [[100, 637, 147, 74], [1700, 980, 200, 70]]) {
    const hint = hintLayout(words, button, 1920, 1080, style);
    assert.equal(hint.side, "top");
    assert.ok(hint.fs <= 42 && hint.fs >= 30, `${locale}: ${hint.fs}`);
    assert.ok(hint.textW <= style.maxW, `${locale}: ${hint.textW}`);
    const [cx, cy] = hint.origin, half = (hint.w * S) / 2;
    assert.ok(cx - half >= 0 && cx + half <= 1920, `${locale}: inside the host`);
    // the box's visible bottom edge stands 12 units above the control, centred on it unless held inside the host
    const visibleBottom = cy + (hint.h / 2 - BOX_INSET.bottom) * S;
    assert.ok(Math.abs(button[1] - visibleBottom - 12 * S) < 0.5, `${locale}: ${visibleBottom}`);
    const centre = button[0] + button[2] / 2;
    assert.ok(Math.abs(cx - Math.min(Math.max(centre, half), 1920 - half)) < 0.5);
    const [bg, label] = hintTree(hint, style).children;
    assert.deepEqual([bg.widgets[0].sprite, bg.widgets[0].w_, bg.widgets[0].h_], ["Common_Tooltip_Bg", hint.w, hint.h]);
    assert.equal(label.widgets[0].text, hint.text);
    assert.equal(hint.text.replace(/\s/g, ""), words.replace(/\s/g, ""), "the complete hint fits");
    assert.ok(label.widgets[0].w_ >= hint.textW);
    assert.ok(label.widgets[0].h_ >= hint.textH);
    assert.equal(hintTree(hint, style).children.length, 2, "no arrow");
    // main.py's minimum and default windows, plus a large window; the fixed stage scales with the window.
    for (const [windowW, windowH] of [[960, 540], [1600, 900], [2560, 1440]]) for (const density of [1, 2]) {
      const scale = Math.min(windowW / 1920, windowH / 1080) * density;
      assert.ok((cx - half) * scale >= 0 && (cx + half) * scale <= windowW * density);
      assert.ok((cy - hint.h * S / 2) * scale >= 0 && (cy + hint.h * S / 2) * scale <= windowH * density);
      assert.ok(hint.textW * S * scale <= label.widgets[0].w_ * S * scale);
      assert.ok(hint.textH * S * scale <= label.widgets[0].h_ * S * scale);
    }
  }
});

test("a control near the top gets the hint below it, inside the host", () => {
  const style = hintStyle(PREFAB);
  const hint = hintLayout("A reason long enough to need most of the tooltip's room on two lines", [1800, 20, 100, 40], 1920, 1080, style);
  const half = (hint.w * S) / 2;
  assert.equal(hint.side, "bottom");
  assert.ok(hint.origin[0] - half >= 0 && hint.origin[0] + half <= 1920);
  const visibleTop = hint.origin[1] - (hint.h / 2 - BOX_INSET.top) * S;
  assert.ok(Math.abs(visibleTop - (20 + 40) - 12 * S) < 0.5);
});

test("a card's hint is drawn around its scrolling list, not inside it, where the list's edges would cut it off", () => {
  const el = (cls, style = {}, extra = {}) => ({ classList: { contains: (c) => c === cls }, style, ...extra });
  const host = el("popup-host");
  const area = el("popup-cards", { left: "300px", top: "160px" }, { scrollLeft: 0, scrollTop: 250, parentElement: host });
  const inner = el("popup-cards-inner", {}, { parentElement: area });
  const card = { parentElement: inner };
  // the list's offset in the popup, less what it is scrolled by
  assert.deepEqual(hintHost(card), { host, dx: 300, dy: -90 });
  // a first-row card's hint above it then stays inside the popup, above the list's top edge
  const style = hintStyle(PREFAB), [l, t, w, h] = [20, 270, 200, 300];
  const hint = hintLayout("Cross-character model, may break", [l + 300, t - 90, w, h], 1920, 1080, style);
  assert.equal(hint.side, "top");
  assert.ok(hint.origin[1] - (hint.h / 2 - BOX_INSET.top) * S >= 0);
  // any other control keeps its own layer
  const layer = el("side");
  assert.deepEqual(hintHost({ parentElement: layer }), { host: layer, dx: 0, dy: 0 });
});
