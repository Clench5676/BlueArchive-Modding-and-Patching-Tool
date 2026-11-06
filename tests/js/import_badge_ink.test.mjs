// Import Mod's status badges: the ink of the text (the extent the eye sees, one baseline per language: NGUI.inkShift with the
// reference glyph) is centred on the badge background (UIMailBoxContent TimeLimit/Bg), for every state text of the page, one
// line or wrapped, in every shipped language. Measured on the shared stand-in canvas (helpers/fit_canvas.mjs): what is
// asserted is the geometry, not a font.
import assert from "node:assert/strict";
import { test } from "node:test";
import { installFitPage, text, LOCALES } from "./helpers/fit_canvas.mjs";
import { makeImportLayouts, FRAME_META, framePixels } from "./import_prefabs.mjs";

const page = installFitPage();
Object.defineProperty(globalThis, "navigator", { value: { platform: "Mac" }, configurable: true });
globalThis.addEventListener = () => {};
const NGUI = await import("../../src/bmpt/web/js/ngui.js");
const { importArtwork, loadImportLayouts, ROW_SCALE } = await import("../../src/bmpt/web/js/import-layout.js");
const { STATES } = await import("../../src/bmpt/web/js/import-state.js");

const { find, fontSizeFor, inkBand, inkReference, LINE_PITCH } = NGUI;
const prefabs = makeImportLayouts();
const layouts = await loadImportLayouts({ layout: async (name) => prefabs[name], find, spritePixels: async () => framePixels() });
const ui = { ...NGUI, translate: (key) => key, atlasIndex: () => ({ Common: { Card_Select_Line: FRAME_META, Common_Line_Raius20px: FRAME_META } }) };
const art = () => importArtwork(ui, layouts);
const textOf = (node) => node.widgets.find((w) => "text" in w);

// the ink centre of the badge's text minus the badge background's centre, in stage px (the row drawn at ROW_SCALE). The label's
// lines fill the top of its box at NGUI's pitch (pivot 1); the ink is that of the reference glyph on every line, else the text's own.
function inkOffset(tree) {
  const status = find(tree, "TimeLimit"), node = find(status, "Label_Locale_TimeLimit"), label = textOf(node), bg = find(status, "Bg");
  const lines = String(label.text).split("\n"), px = fontSizeFor(label.text, label.fontSize), reference = inkReference(label.text);
  const band = inkBand(reference ? lines.map(() => reference).join("\n") : label.text, px);
  const inkDown = lines.length * px * LINE_PITCH / 2 + (band.top + band.bottom) / 2;     // below the label's top
  const inkY = status.pos[1] + node.pos[1] - inkDown, bgY = status.pos[1] + bg.pos[1] - bg.widgets[0].h_ / 2;
  return (inkY - bgY) * ROW_SCALE;
}

const badge = (state, stateKey = "ready") => art().row({ name: "Student", kind: "Kind", state, stateKey, meta: "ch0163 - 3 files, 4.0 MB", textSize: 42 });

// every text the badge shows on the shipped page: the states, library-only and the unknown source
const shippedTexts = (locale) => [...STATES.map((state) => text(locale, `import.state.${state}`)), text(locale, "import.libraryOnly"),
  text(locale, "import.reason.source_unknown")];

for (const locale of LOCALES) test(`the ink of every status badge text is centred on its background within half a pixel in ${locale}`, () => {
  page.lang = locale;
  const states = shippedTexts(locale);
  assert.equal(new Set(states).size, states.length);
  for (const state of states) {
    const tree = badge(state);
    assert.ok(Math.abs(inkOffset(tree)) <= 0.5, `${locale} ${JSON.stringify(state)}: ${inkOffset(tree).toFixed(2)} px off`);
  }
});

for (const locale of LOCALES) test(`a badge that wraps onto two lines is centred too in ${locale}`, () => {
  page.lang = locale;
  const long = Array(24).fill(text(locale, "import.state.convert")).join(/^(en|th)/.test(locale) ? " " : "");
  const tree = badge(long, "convert"), label = textOf(find(find(tree, "TimeLimit"), "Label_Locale_TimeLimit"));
  assert.ok(label.text.split("\n").length >= 2, JSON.stringify(label.text));
  assert.ok(Math.abs(inkOffset(tree)) <= 0.5, `${locale}: ${inkOffset(tree).toFixed(2)} px off`);
});

test("the correction is the distance between the ink's centre and the background's, and moves only the badge's label", () => {
  page.lang = "en-US";
  const tree = badge(text("en-US", "import.state.ready")), status = find(tree, "TimeLimit");
  const label = find(status, "Label_Locale_TimeLimit"), bg = find(status, "Bg");
  assert.equal(bg.pos[1], 4); assert.equal(bg.widgets[0].pivot, 1);
  assert.notEqual(label.pos[1], 0, "the label is not left at the line box's centre");
  // the other labels of the row keep their places
  assert.deepEqual(find(tree, "Label_Locale_Received").pos, [find(tree, "Label_Locale_Received").pos[0], 20]);
});
