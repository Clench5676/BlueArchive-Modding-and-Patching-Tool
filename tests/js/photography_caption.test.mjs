// The photography toolbar's text buttons (Occlusion Guide, Grid) share one caption size and wrap inside their bodies in
// every shipped language; the ink centring needs no computed style. Run by tests/test_photography_js.py.
import assert from "node:assert/strict";
import { test } from "node:test";
import { readFile } from "node:fs/promises";

const WIDE = /[\u2e80-\u9fff\u3000-\u30ff\uff00-\uffef\uac00-\ud7af\u0e00-\u0e7f]/;
const em = (c) => (WIDE.test(c) ? 1 : 0.55);
const ctx = {
  font: "",
  measureText(s) {
    const fs = parseFloat(/([\d.]+)px/.exec(this.font)[1]);
    return { width: [...s].reduce((w, c) => w + em(c) * fs, 0), actualBoundingBoxAscent: 0.75 * fs, actualBoundingBoxDescent: /[gjpqy]/.test(s) ? 0.2 * fs : 0,
      fontBoundingBoxAscent: 1.16 * fs, fontBoundingBoxDescent: 0.29 * fs };
  },
};
globalThis.document = {
  createElement: () => ({ getContext: () => ctx, remove() {} }),
  documentElement: { lang: "en-US", appendChild() {} },
};
globalThis.getComputedStyle = () => ({ getPropertyValue: () => "" });
const NGUI = await import("../../src/bmpt/web/js/ngui.js");
const { CONTROL, XS_BUTTON, SPACE, FULLSCREEN_BUTTON } = await import("../../src/bmpt/web/js/ui-tokens.js");
const { row } = await import("../../src/bmpt/web/js/ui-grid.js");
const controller = await readFile(new URL("../../src/bmpt/web/js/photography/controller.js", import.meta.url), "utf8");
const layoutCode = controller.slice(controller.indexOf("function photographyToolbarLayout("), controller.indexOf("async function whiteButton("));
const S = 1920 / 2960;
const { photographyToolbarLayout, galleryPair, toolbarCaptions } = new Function("CONTROL", "XS_BUTTON", "SPACE", "FULLSCREEN_BUTTON", "row", "S", "NGUI", "P", `${layoutCode}; return {photographyToolbarLayout,galleryPair,toolbarCaptions};`)(CONTROL, XS_BUTTON, SPACE, FULLSCREEN_BUTTON, row, S, NGUI, key=>strings[key]);
let strings;
const geometry = photographyToolbarLayout(), OCCLUSION_BUTTON=geometry.occlusion;
const TOOLBAR_CAPTION={...geometry.caption,width:geometry.caption.width*S,height:geometry.caption.height*S};
const LOCALES = ["en-US", "zh-CN", "zh-TW", "ja-JP", "ko-KR", "th-TH"];

for (const locale of LOCALES) test(`the toolbar captions share one size and fit the 196px by 136-unit bodies in ${locale}`, async () => {
  const ui = JSON.parse(await readFile(new URL(`../../src/bmpt/locales/${locale}.json`, import.meta.url), "utf8")).webui.photo;
  document.documentElement.lang = locale;
  strings=ui;
  const captions = [ui.occlusion, ui.gridOn, ui.gridOff];
  assert.ok(captions.every(Boolean));
  const fits = toolbarCaptions();
  assert.equal(new Set(fits.map((f) => f.fs)).size, 1, "one size for every caption and both grid states");
  assert.ok(fits[0].fs <= TOOLBAR_CAPTION.size);
  for (const f of fits) {
    assert.ok(!f.overflow, `${locale} fits`);
    assert.ok(f.lines <= 2);
    for (const line of f.text.split("\n")) assert.ok(NGUI.glyphWidth(line, f.fs) * S <= TOOLBAR_CAPTION.width + 1e-9, `${JSON.stringify(f.text)} wide`);
    assert.ok(f.lines * NGUI.fontSizeFor(f.text, f.fs) * NGUI.LINE_PITCH * S <= TOOLBAR_CAPTION.height + 1e-9, `${JSON.stringify(f.text)} tall`);
    assert.equal(f.text.replace(/\s/g, ""), captions[fits.indexOf(f)].replace(/\s/g, ""), "every character kept");
  }
});

test("a caption wider than the body wraps at its space instead of shrinking or spilling", () => {
  document.documentElement.lang = "en-US";
  // the widest Latin caption of the toolbar on the mock font: wider than one line
  const wide = "Occlusion Guide";
  assert.ok(NGUI.glyphWidth(wide, TOOLBAR_CAPTION.size) * S > TOOLBAR_CAPTION.width, "does not fit one line at full size");
  const [fit] = NGUI.fitGroup([wide, "Hide Grid", "Show Grid"], TOOLBAR_CAPTION.size, TOOLBAR_CAPTION.min, TOOLBAR_CAPTION.width / S, 2, { maxH: TOOLBAR_CAPTION.height / S });
  assert.deepEqual(fit.text.split("\n"), ["Occlusion", "Guide"]);
  assert.ok(fit.fs >= TOOLBAR_CAPTION.min, "within the font range");
});

test("the text button uses a buttonS body and caption insets", () => {
  assert.equal(OCCLUSION_BUTTON[2],196);assert.equal(OCCLUSION_BUTTON[3],FULLSCREEN_BUTTON.h*S); // BL-188: the fullscreen buttons' one height
  assert.equal(TOOLBAR_CAPTION.width,196-2*CONTROL.buttonXS.pad*S);
  assert.equal(TOOLBAR_CAPTION.height,(FULLSCREEN_BUTTON.h-XS_BUTTON.boxInsetH)*S);
});

test("the toolbar button code no longer depends on computed style", async () => {
  const source = await readFile(new URL("../../src/bmpt/web/js/photography/controller.js", import.meta.url), "utf8");
  assert.doesNotMatch(source, /getComputedStyle/);
  assert.match(source, /inkShift/);
  assert.match(source, /fitGroup/);
});

for (const locale of LOCALES) test(`photography toolbar and gallery pairs use shared bodies and scaled gaps in ${locale}`, async () => {
  const ui=JSON.parse(await readFile(new URL(`../../src/bmpt/locales/${locale}.json`,import.meta.url),"utf8")).webui.photo;
  document.documentElement.lang=locale;
  const near=(a,b)=>assert.ok(Math.abs(a-b)<1e-9,`${a} != ${b}`),g=photographyToolbarLayout();
  near(g.grid[0]-g.occlusion[0]-g.occlusion[2],SPACE.m*S);
  near(g.mode[0]-g.grid[0]-g.grid[2],SPACE.m*S);
  near(g.choices[0]-g.playback[0]-g.playback[2],SPACE.m*S);
  for(const box of [g.grid,g.mode])near(box[1]+box[3]/2,g.occlusion[1]+g.occlusion[3]/2);
  for(const box of [g.mode,g.gallery,g.playback])assert.deepEqual(box.slice(2),g.choices.slice(2));
  for(const [keys,width,x,y] of [[["apply","delete"],140,1200,670],[["previous","next"],180,365,835-(SPACE.l+CONTROL.buttonXS.h)*S],[["restore","export"],180,365,835]]){
    const pair=galleryPair(width,x,y),captions=keys.map(key=>ui[key]);
    assert.deepEqual(pair[0].slice(2),pair[1].slice(2));near(pair[0][3],CONTROL.buttonXS.h*S);near(pair[1][0]-pair[0][0]-pair[0][2],SPACE.m*S);
    const fits=NGUI.fitGroup(captions,CONTROL.buttonXS.fs,XS_BUTTON.fsMin,pair[0][2]/S-XS_BUTTON.fitInset,2,{maxH:CONTROL.buttonXS.h-XS_BUTTON.boxInsetH});
    assert.equal(new Set(fits.map(f=>f.fs)).size,1);
    for(const fit of fits)assert.ok(!fit.overflow,`${locale}: ${fit.text}`);
  }
});
