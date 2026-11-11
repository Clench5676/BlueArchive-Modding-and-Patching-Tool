// Import Mod's status badges: the ink of the text (the extent the eye sees, one baseline per language: NGUI.inkShift with the
// reference glyph) is centred on the badge background (UIMailBoxContent TimeLimit/Bg), for every state text of the page, one
// line or wrapped, in every shipped language. Measured on the shared stand-in canvas (helpers/fit_canvas.mjs): what is
// asserted is the geometry, not a font.
import assert from "node:assert/strict";
import { test } from "node:test";
import { installFitPage, text, LOCALES, context, webui } from "./helpers/fit_canvas.mjs";
import { makeImportLayouts, renderImportTree, FRAME_META, framePixels } from "./import_prefabs.mjs";

const page = installFitPage();
const { readFileSync } = await import("node:fs");
const proof = process.env.BAMT_IMPORT_FIT_PROOF ? JSON.parse(readFileSync(process.env.BAMT_IMPORT_FIT_PROOF, "utf8")) : null;
if (proof) context.measureText = function (value) {
  const px = parseFloat(/([\d.]+)px/.exec(this.font)[1]), chars = [...String(value)], font = proof.font;
  const glyphs = chars.map((ch) => { assert.ok(font.glyphs[ch], `Missing font metric ${ch}`); return font.glyphs[ch]; });
  const width = glyphs.reduce((sum, g) => sum + g[0], 0)
    + chars.slice(1).reduce((sum, ch, i) => sum + (font.kern[chars[i] + ch] || 0), 0);
  return { width: width * px / 1000, actualBoundingBoxAscent: Math.max(0, ...glyphs.map((g) => g[1])) * px / 1000,
    actualBoundingBoxDescent: Math.max(0, ...glyphs.map((g) => g[2])) * px / 1000,
    fontBoundingBoxAscent: font.ascent * px / 1000, fontBoundingBoxDescent: font.descent * px / 1000 };
};
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
const shippedTexts = (locale) => [...STATES.map((state) => text(locale, state === "incomplete" ? "import.missing.state" : `import.state.${state}`)), text(locale, "import.libraryOnly"),
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

// The accepted missing-file layout and text-fit proof, extended to every shipped locale.
{
const Popup = await import("../../src/bmpt/web/js/popup.js");
const { controlRect, badgeRect, ROW_WIDTH, ROW_TEXT_SIZE, BUTTON_BODY_H, clipButtonPatterns } = await import("../../src/bmpt/web/js/import-layout.js");
const { CHOOSE, noteRoom, NOTE_LEFT } = await import("../../src/bmpt/web/js/import-state.js");
const { XS_BUTTON, CONTROL, SPACE, TYPE, LINE } = await import("../../src/bmpt/web/js/ui-tokens.js");
const { S } = await import("../../src/bmpt/web/js/util.js");
const { setStrings } = await import("../../src/bmpt/web/js/i18n.js");
const missingLabel = (key, params = {}, locale) => text(locale, `import.missing.${({missing: "count", missingNotFound: "notFound"})[key] || key}`, params);
const statusText = (missing, locale) => missingLabel(missing.found.length ? "count" : "notFound", {n: missing.found.length || missing.unresolved}, locale);
const groupsText = (n, locale) => missingLabel(n === 1 ? "groupsOne" : "groups", {n}, locale);
const chooseSize = (art) => art.controlSize(missingLabel("choose", {}, document.documentElement.lang), CHOOSE.scale);
const MISSING_STRINGS = Object.fromEntries(LOCALES.map((locale) => [locale, webui(locale).import.missing]));
const { find, fontSizeFor, glyphWidth, LINE_PITCH } = NGUI;

const lines = (value) => String(value).split("\n");
const widest = (value, fs) => Math.max(...lines(value).map((line) => glyphWidth(line, fs)));
const stack = (value, fs) => lines(value).length * fontSizeFor(value, fs) * LINE_PITCH;
// the artwork on the stand-in prefabs, with the selection outlines a row needs (as loadImportLayouts reads them)
const prefabs = proof?.prefabs || makeImportLayouts();
const layouts = await loadImportLayouts({ layout: async (name) => prefabs[name], find, spritePixels: async () => framePixels() });
const ui = { ...NGUI, translate: (key) => key, atlasIndex: () => proof?.atlases || ({ Common: { Card_Select_Line: FRAME_META, Common_Line_Raius20px: FRAME_META, Common_Btn_BG: { borderLeft: 30, borderRight: 30, borderTop: 30, borderBottom: 35 } } }) };
const art = () => importArtwork(ui, layouts);
const textOf = (node) => node.widgets.find((w) => "text" in w);
const Ns = [1, 9, 25, 99];
const STATE_TEXTS = ["ready", "convert", "update", "library", "installed", "blocked", "done"];

const statuses = (locale) => Ns.flatMap((n) => [statusText({ found: [{}], unresolved: 0 }, locale).replace("1", n),
  statusText({ found: [], unresolved: n }, locale)]);

test("all six locales ship the same missing-file keys and placeholders", () => {
  assert.deepEqual(Object.keys(MISSING_STRINGS).sort(), LOCALES.slice().sort());
  const placeholders = (value) => [...value.matchAll(/\{(\w+)\}/g)].map((m) => m[1]).sort();
  assert.deepEqual(Object.keys(MISSING_STRINGS["zh-CN"]).sort(), Object.keys(MISSING_STRINGS["en-US"]).sort());
  for (const key of Object.keys(MISSING_STRINGS["en-US"])) assert.deepEqual(placeholders(MISSING_STRINGS["zh-CN"][key]), placeholders(MISSING_STRINGS["en-US"][key]), key);
  for (const locale of LOCALES) for (const value of Object.values(MISSING_STRINGS[locale])) assert.ok(!/[·・]/.test(value), "no middle dot separators");
});

for (const locale of LOCALES) test(`the status badge holds both forms of "missing files" inside its widget, whole, in ${locale}`, () => {
  setStrings(locale, {});
  const rowArt = art();
  for (const state of statuses(locale)) {
    const tree = rowArt.row({ name: "Student", kind: text(locale, "tab.lobby"), state, stateKey: "incomplete", meta: "ch0230 - 2 files, 4.1 MB", muted: true, textSize: 42 });
    const status = find(tree, "TimeLimit"), label = textOf(find(status, "Label_Locale_TimeLimit")), bg = find(status, "Bg").widgets[0];
    assert.ok(!label.ellipsis, `${state} ellipsized`);
    assert.equal(label.text.replace(/\n/g, ""), state, "every character kept");
    assert.ok(widest(label.text, label.fontSize) <= label.w_ + 1e-9, `${state}: ${widest(label.text, label.fontSize)} wide in ${label.w_}`);
    assert.ok(label.w_ <= bg.w_ - 30 + 1e-9, "the label sits inside the badge");
    assert.ok(bg.h_ >= label.h_, "the badge holds every line");
    assert.equal(label.fontSize, 42, "the row's one text size");
    // the badge ends at the row's text right edge (910), or starts at the text's left edge on its own line
    assert.ok(status.pos[0] + bg.w_ / 2 <= 910 + 1e-9 || status.pos[0] - bg.w_ / 2 >= -700 - 1e-9, "inside the card");
  }
});

// every text and badge rectangle of a row in px of the row, from the fixture's renderer (on plain elements, while it draws)
async function draw(tree, scale = ROW_SCALE, origin = [ROW_WIDTH / 2, 329 * ROW_SCALE / 2]) {
  const original = document.createElement, host = { children: [], appendChild(child) { host.children.push(child); return child; } };
  document.createElement = () => ({ dataset: {}, style: {}, children: [], textContent: "", appendChild(child) { this.children.push(child); return child; } });
  try {
    return { host, view: await renderImportTree(tree, host, { scale, origin }) };
  } finally { document.createElement = original; }
}
async function occupied(tree) {
  const { host } = await draw(tree);
  return host.children.filter((e) => e.textContent || /\/TimeLimit\/Bg$/.test(e.dataset.path)).map((e) => ({
    path: e.dataset.path, left: parseFloat(e.style.left), top: parseFloat(e.style.top), width: parseFloat(e.style.width), height: parseFloat(e.style.height) }));
}
const overlaps = (a, [x, y, w, h]) => a.left < x + w && a.left + a.width > x && a.top < y + h && a.top + a.height > y;

// the ink of the badge's text, centred on its background like every badge of the page (import_badge_ink.test.mjs has the shipped
// states in every language): the new texts, found and not found, one line or on their own line, in all shipped languages
for (const locale of LOCALES) test(`the ink of the new status texts is centred on the badge background within half a pixel in ${locale}`, () => {
  setStrings(locale, {});
  const rowArt = art();
  for (const state of [...statuses(locale), ...longest(locale)]) for (const unidentified of [false, true]) {
    const tree = rowArt.row({ name: "Student", kind: text(locale, "tab.lobby"), state, stateKey: "incomplete", meta: METAS[0], muted: true, textSize: 42, unidentified });
    const status = find(tree, "TimeLimit"), node = find(status, "Label_Locale_TimeLimit"), label = textOf(node), bg = find(status, "Bg");
    const n = lines(label.text).length, px = fontSizeFor(label.text, label.fontSize), reference = NGUI.inkReference(label.text);
    const band = NGUI.inkBand(reference ? Array(n).fill(reference).join("\n") : label.text, px);
    const inkY = status.pos[1] + node.pos[1] - (n * px * LINE_PITCH / 2 + (band.top + band.bottom) / 2);
    const bgY = status.pos[1] + bg.pos[1] - bg.widgets[0].h_ / 2;
    assert.ok(Math.abs((inkY - bgY) * ROW_SCALE) <= 0.5, `${locale} ${state}: ${((inkY - bgY) * ROW_SCALE).toFixed(2)} px off`);
  }
});

// --- the spacing under the meta line (the divider-to-meta gap is the prefab's rhythm) and the control under the badge ---
// a label whose lines fill the top of its box (pivots 0 to 2): the y (units, up) of its ink's top and bottom
const inkOf = (node) => {
  const w = textOf(node), px = fontSizeFor(w.text, w.fontSize), half = lines(w.text).length * px * LINE_PITCH / 2, band = NGUI.inkBand(w.text, px);
  return { top: node.pos[1] - (half + band.top), bottom: node.pos[1] - (half + band.bottom) };
};
const stagePx = (units) => units * ROW_SCALE;
const METAS = ["ch0163 - 3 files, 4.0 MB", "ch0230 - 12 files, 123.4 MB"];
// the longest status texts of the accepted flow, found and not found, and the form that fits a line
const longest = (locale) => [missingLabel("missing", { n: 99 }, locale), missingLabel("missingNotFound", { n: 99 }, locale)];

function measure(tree) {
  const status = find(tree, "TimeLimit"), bg = find(status, "Bg"), bgSprite = bg.widgets[0];
  const card = find(tree, "BG"), cardSprite = card.widgets.find((w) => "sprite" in w), line = find(tree, "Line");
  const metaInk = inkOf(find(tree, "Label_Locale_Received"));
  const bgTop = status.pos[1] + bg.pos[1], bgBottom = bgTop - bgSprite.h_;
  const cardBottom = card.pos[1] - [0, 0.5, 1][Math.floor((cardSprite.pivot ?? 4) / 3)] * cardSprite.h_;
  return { status, bgTop, bgBottom, right: status.pos[0] + bgSprite.w_ / 2, left: status.pos[0] - bgSprite.w_ / 2, above: metaInk.bottom, metaInk,
    dividerBottom: line.pos[1] - line.widgets[0].h_ / 2, cardBottom,
    // the badge is on its own line when its background starts under the ink of the first line
    second: bgTop < metaInk.bottom };
}
// the y (up) of the centre of a label's box, from its pivot
const boxCentre = (node) => { const w = textOf(node); return node.pos[1] + [-1, 0, 1][Math.floor((w.pivot ?? 4) / 3)] * w.h_ / 2; };
// the Choose Files control under a badge: one gap (the divider-to-meta rhythm) below the badge, its right edge on the badge's,
// at its full height, and one gap above the grown card's bottom
function controlUnder(tree, m, room, plain, label) {
  const c = tree.control, gap = m.dividerBottom - m.metaInk.top, rect = controlRect(tree);
  assert.equal(c.beside, false, label);
  assert.ok(Math.abs(stagePx(m.bgBottom - c.top - gap)) <= .01, `${label}: one gap below the badge`);
  assert.ok(Math.abs(c.right - m.right) <= 1e-6, `${label}: right edge on the badge's`);
  assert.ok(Math.abs(rect[3] - room.height) <= .01, `${label}: full control height`);
  assert.ok(tree.rowHeight > plain.rowHeight, `${label}: the row grows for the control's line`);
  assert.ok(Math.abs(stagePx(c.top - c.height - m.cardBottom - gap)) <= .01, `${label}: one gap under the control`);
  assert.ok(rect[0] >= 0 && rect[0] + rect[2] <= ROW_WIDTH - 66 && rect[1] + rect[3] <= tree.rowHeight * ROW_SCALE, `${label}: inside the row`);
}

for (const locale of LOCALES) test(`badge-line: every Choose Files state keeps its badge on the metadata's line, the control under it, in ${locale}`, () => {
  setStrings(locale, {});
  const rowArt = art(), control = chooseSize(rowArt);
  for (const state of [...statuses(locale), ...longest(locale)]) for (const meta of METAS) for (const unidentified of [false, true]) {
    const data = { name: "Student", kind: text(locale, "tab.lobby"), state, stateKey: "incomplete", meta, muted: true, unidentified };
    const plain = rowArt.row(data), tree = rowArt.row({ ...data, control }), m = measure(tree);
    const label = textOf(find(m.status, "Label_Locale_TimeLimit")), metaNode = find(tree, "Label_Locale_Received");
    assert.ok(!m.second, `${locale} ${state}: the badge stays on the metadata's line`);
    assert.equal(lines(label.text).length, 1, `${locale} ${state}: the badge never wraps`);
    assert.equal(label.text, state); assert.equal(label.fontSize, ROW_TEXT_SIZE);
    assert.ok(Math.abs(m.right - 910) <= 1e-6, "the badge ends on the right column");
    assert.ok(metaNode.pos[0] + textOf(metaNode).w_ <= m.left - SPACE.l + 1e-6, "the metadata ends SPACE.l before the badge");
    controlUnder(tree, m, control, plain, `${locale} ${state}`);
  }
});

for (const locale of LOCALES) test(`fallback: a badge too wide for the metadata's line takes a line of its own, the control under it, in ${locale}`, () => {
  setStrings(locale, {});
  const rowArt = art(), control = chooseSize(rowArt), state = Array(20).fill(missingLabel("missingNotFound", { n: 99 }, locale)).join(" ");
  const data = { name: "Student", kind: text(locale, "tab.lobby"), stateKey: "incomplete", meta: METAS[0], muted: true };
  // a badge too wide for the line, a control wider than the row and one taller than the badge: only the first leaves the line
  for (const [value, room, own] of [[state, control, true], [missingLabel("missingNotFound", { n: 1 }, locale), { ...control, width: ROW_WIDTH / 2 }, false],
    [missingLabel("missingNotFound", { n: 1 }, locale), { ...control, minHeight: 1000 }, false]]) {
    const tree = rowArt.row({ ...data, state: value, control: room }), plain = rowArt.row({ ...data, state: value });
    const m = measure(tree);
    assert.equal(m.second, own, `${locale}: ${own ? "its own line" : "the metadata's line"}`);
    controlUnder(tree, m, room, plain, locale);
    assert.equal(find(tree, "TimeLimit/Bg").widgets[0].w_, find(plain, "TimeLimit/Bg").widgets[0].w_, "the badge's width is its own, control or not");
    if (value === state) assert.ok(lines(textOf(find(tree, "TimeLimit/Label_Locale_TimeLimit")).text).length > 1);
  }
});

// --- the design system (docs/design-system.md) in Import Mod's rows: the scales and rules of decisions 1 to 3, in en-US and zh-CN ---
// the scales the row takes, and the rules as assertions on its boxes
const PITCH = LINE.stack * TYPE.dense.fs, DESIGN_LOCALES = ["en-US", "zh-CN"];
const metaFor = (locale, n = 3) => text(locale, n === 1 ? "import.metaOne" : "import.meta", { id: "ch0163", n, size: "4.0 MB" });
// y (up) of the reference centre of a label's first line, whose lines fill the top of its box (pivots 0 to 2)
const firstLineCentre = (node) => {
  const w = textOf(node), px = fontSizeFor(w.text, w.fontSize);
  return node.pos[1] - (px * LINE_PITCH / 2 - NGUI.inkShift(lines(w.text)[0], px, { reference: true }));
};
const backgroundCentre = (status) => { const bg = find(status, "Bg"); return status.pos[1] + bg.pos[1] - bg.widgets[0].h_ / 2; };
// a status text of about this width in units at the row's size, from the locale's own words
const stateOf = (locale, width) => {
  const word = text(locale, "import.state.convert"), join = /^(en|th)/.test(locale) ? " " : "";
  let state = word;
  while (NGUI.glyphWidth(state, ROW_TEXT_SIZE) < width) state += join + word;
  return state;
};

test("badge-line: the scales the row is built from are the design system's", () => {
  assert.equal(ROW_TEXT_SIZE, TYPE.dense.fs);
  assert.equal(TYPE.dense.min, TYPE.dense.fs, "the row's text only wraps, it never shrinks");
  assert.equal(PITCH, 57.75);
  assert.equal(SPACE.l, 28); assert.equal(CONTROL.badge.pad, 16); assert.equal(CONTROL.buttonXS.pad, 34);
});

for (const locale of DESIGN_LOCALES) test(`badge-line: every text of a row is TYPE.dense and sits on the card's first line in ${locale}`, () => {
  setStrings(locale, {});
  const rowArt = art(), kind = text(locale, "tab.lobby");
  const tree = rowArt.row({ name: "Student", kind, state: text(locale, "import.state.ready"), stateKey: "ready", meta: metaFor(locale), route: "Global Android", note: "Note" });
  for (const path of ["Label_Locale_Received", "Label_From", "Platform", "Label_Locale_Limit", "TimeLimit/Label_Locale_TimeLimit"]) {
    assert.equal(textOf(find(tree, path)).fontSize, TYPE.dense.fs, path);
  }
  // the name plate (title and divider) keeps the prefab's place; the first line starts ROW_TEXT_TOP under the card's centre
  const card = find(prefabs.UIMailBoxContent, "List_01");
  assert.equal(find(tree, "Label_System").pos[1], find(card, "Label_System").pos[1]);
  assert.equal(find(tree, "Line").pos[1], find(card, "Line").pos[1]);
  assert.equal(find(tree, "Label_Locale_Received").pos[1], 20);
  // the kind is on the name's line (owner, 2026-11-07): right aligned on the divider's end, its box centred on the name's
  const from = find(tree, "Label_From");
  assert.equal(from.pos[0], 926); assert.equal(textOf(from).pivot, 5);
  assert.ok(Math.abs(boxCentre(from) - boxCentre(find(tree, "Label_System"))) <= 1e-9);
  assert.ok(boxCentre(from) - textOf(from).h_ / 2 >= find(tree, "Line").pos[1], "above the divider");
});

for (const locale of DESIGN_LOCALES) test(`badge-line: the first line's texts and a badge on it share one centre line, tops level, in ${locale}`, () => {
  setStrings(locale, {});
  const rowArt = art(), kind = text(locale, "tab.lobby");
  for (const state of [text(locale, "import.state.ready"), text(locale, "import.state.update"), text(locale, "import.state.convert")]) {
    const tree = rowArt.row({ name: "Student", kind, state, stateKey: "update", meta: metaFor(locale), muted: true });
    const m = measure(tree), meta = find(tree, "Label_Locale_Received");
    assert.ok(!m.second, `${locale} ${state}: the badge is on the first line`);
    // rule 1: the visual centre of every element of the row on its centre line (a text's reference glyph, a widget's box)
    assert.ok(Math.abs(firstLineCentre(meta) - backgroundCentre(m.status)) <= .01, `${locale} ${state}: meta ${firstLineCentre(meta)} against badge ${backgroundCentre(m.status)}`);
    assert.equal(tree.rowHeight, 348, "a first-line badge leaves the row at its height");
  }
});

for (const locale of DESIGN_LOCALES) test(`badge-line: separate lines stack LINE.stack x the row's size (57.75) apart in ${locale}`, () => {
  setStrings(locale, {});
  const rowArt = art(), kind = text(locale, "tab.lobby");
  // a badge on its own line (too wide for the metadata's), a route and a note: the badge's cell, then the route, then the note,
  // a pitch a line
  const tree = rowArt.row({ name: "Student", kind, state: stateOf(locale, 1300), stateKey: "blocked", meta: metaFor(locale), route: "Global Android",
    note: "Note", muted: true });
  const m = measure(tree), route = find(tree, "Platform"), note = find(tree, "Label_Locale_Limit");
  assert.ok(m.second);
  assert.equal(lines(textOf(route).text).length, 1);
  assert.ok(Math.abs(m.status.pos[1] - route.pos[1] - PITCH) <= 1e-9, "the line after the badge");
  assert.equal(route.pos[1] - note.pos[1], PITCH, "the line after the route");
  // without a badge line the route follows the metadata's lines, a pitch each (the kind is on the name's line)
  for (const meta of [metaFor(locale), Array(60).fill(metaFor(locale)).join(/^en/.test(locale) ? " " : "")]) {
    const plain = rowArt.row({ name: "Student", kind, state: text(locale, "import.state.ready"), stateKey: "ready", meta, route: "Global Android", note: "Note" });
    const rows = lines(textOf(find(plain, "Label_Locale_Received")).text).length;
    assert.equal(find(plain, "Platform").pos[1], 20 - rows * PITCH);
    assert.equal(find(plain, "Label_Locale_Limit").pos[1], 20 - (rows + 1) * PITCH);
  }
});

for (const locale of DESIGN_LOCALES) test(`badge-line: the kind ends the name's line, the metadata ends SPACE.l before the badge, whose caption keeps CONTROL.badge.pad each side, in ${locale}`, () => {
  setStrings(locale, {});
  const rowArt = art(), kind = text(locale, "tab.lobby");
  for (const state of [stateOf(locale, 360), text(locale, "import.state.ready")]) {
    const tree = rowArt.row({ name: "Student", kind, state, stateKey: "convert", meta: metaFor(locale) });
    const m = measure(tree), bg = find(m.status, "Bg").widgets[0], meta = find(tree, "Label_Locale_Received"), title = find(tree, "Label_System");
    assert.ok(!m.second, "the badge shares the first line");
    assert.ok(Math.abs(m.left - (meta.pos[0] + textOf(meta).w_) - SPACE.l) <= 1e-6, `${m.left - (meta.pos[0] + textOf(meta).w_)} between the metadata and the badge`);
    assert.ok(bg.w_ >= 330);
    if (bg.w_ > 330) assert.ok(Math.abs(bg.w_ - NGUI.glyphWidth(state, ROW_TEXT_SIZE) - 2 * CONTROL.badge.pad) <= 1e-6, "the caption's padding is CONTROL.badge.pad each side");
    assert.ok(Math.abs(m.right - 910) <= 1e-6, "the badge ends on the right column");
    // the name's text box ends SPACE.l before the kind's, which ends on the divider's end
    const from = find(tree, "Label_From");
    assert.ok(Math.abs(from.pos[0] - textOf(from).w_ - (title.pos[0] + textOf(title).w_) - SPACE.l) <= 1e-6, "SPACE.l between the name and the kind");
  }
});

for (const locale of DESIGN_LOCALES) test(`badge-line: the Choose Files control is a buttonS control on the line under the badge, its right edge on the badge's, in ${locale}`, () => {
  setStrings(locale, {});
  const rowArt = art(), control = chooseSize(rowArt), caption = missingLabel("choose", {}, locale);
  // width: the caption and CONTROL.buttonXS.pad each side (the body's slice border included), 240 at least, in the button's own units
  const button = find(prefabs.UIMailBoxContent, "List_01/Btn_Receive"), sx = button.scale?.[0] ?? 1, lx = find(button, "Label_Locale").scale?.[0] ?? 1;
  const expected = Math.max(CONTROL.buttonXS.minW, NGUI.glyphWidth(caption, control.size) * lx + 2 * CONTROL.buttonXS.pad) * sx * CHOOSE.scale;
  assert.ok(Math.abs(control.width - expected) <= 1e-6, `${control.width} against ${expected}`);
  for (const state of statuses(locale)) {
    const data = { name: "Student", kind: text(locale, "tab.lobby"), state, stateKey: "incomplete", meta: metaFor(locale), muted: true };
    const tree = rowArt.row({ ...data, control }), m = measure(tree);
    assert.ok(!m.second, `${locale} ${state}`);
    assert.ok(Math.abs(tree.control.right - 910) <= 1e-6, "the control ends on the right column, under the badge's end");
    assert.ok(Math.abs(tree.control.width - control.width / ROW_SCALE) <= 1e-6);
    controlUnder(tree, m, control, rowArt.row(data), `${locale} ${state}`);
  }
});

for (const locale of LOCALES) test(`rows without a control keep their height, a badge on the first line included, in ${locale}`, () => {
  setStrings(locale, {});
  const rowArt = art(), kind = text(locale, "tab.lobby");
  const states = [...STATE_TEXTS.map((state) => text(locale, `import.state.${state}`)), missingLabel("missing", { n: 3 }, locale)];
  for (const state of states) for (const meta of METAS) {
    const tree = rowArt.row({ name: "Student", kind, state, stateKey: "update", meta, muted: true, textSize: 42 });
    const m = measure(tree);
    assert.equal(tree.control, null);
    if (!m.second) assert.equal(tree.rowHeight, 348, `${locale} ${state}`);
    // a badge on its own line still keeps the gap, with or without the control
    else assert.ok(Math.abs(stagePx(m.above - m.bgTop - (m.dividerBottom - m.metaInk.top))) <= 1);
  }
  // the second badge's extra height is only what the gap needs: the same row with the badge on the first line is 348
  assert.equal(rowArt.row({ name: "Student", kind, state: states[0], stateKey: "ready", meta: METAS[0], textSize: 42 }).rowHeight, 348);
});

for (const locale of LOCALES) test(`the Choose Files control keeps clear of the row's text and badge in ${locale}`, async () => {
  setStrings(locale, {});
  const rowArt = art(), kind = text(locale, "tab.lobby");
  for (const state of [...longest(locale), ...statuses(locale)]) for (const meta of METAS) {
    const tree = rowArt.row({ name: "Student", kind, state, stateKey: "incomplete", meta, muted: true, textSize: 42, control: chooseSize(rowArt) });
    const rect = controlRect(tree);
    const crowded = (await occupied(tree)).filter((box) => overlaps(box, rect));
    assert.deepEqual(crowded.map((box) => box.path), [], `${locale} ${state}`);
  }
});

for (const locale of LOCALES) test(`badge-line: the one-line Choose Files caption matches the badge size and centres its reference ink in ${locale}`, () => {
  setStrings(locale, {});
  const rowArt = art(), caption = missingLabel("choose", {}, locale), control = chooseSize(rowArt), height = (ROW_TEXT_SIZE * 1.3 + 8) * ROW_SCALE;
  const tree = rowArt.button(caption, control.width / CHOOSE.scale, false, true, control.size, height / CHOOSE.scale);
  const node = find(tree, "Label_Locale"), label = textOf(node), body = tree.widgets.find((w) => "sprite" in w);
  const sx = tree.scale[0] * CHOOSE.scale, sy = tree.scale[1] * CHOOSE.scale, ly = node.scale[1];
  assert.equal(label.text, caption); assert.equal(lines(label.text).length, 1); assert.ok(!label.ellipsis);
  assert.ok(Math.abs(fontSizeFor(caption, label.fontSize) * sy * ly - fontSizeFor(caption, ROW_TEXT_SIZE) * ROW_SCALE) <= .01);
  assert.ok(widest(label.text, label.fontSize) * node.scale[0] <= body.w_ - 2 * CONTROL.buttonXS.pad + 1e-9, "CONTROL.buttonXS.pad each side");
  const leftPadding = body.w_ / 2 + node.pos[0] - label.w_ * node.scale[0] / 2;
  const rightPadding = body.w_ / 2 - node.pos[0] - label.w_ * node.scale[0] / 2;
  assert.ok(Math.abs(leftPadding - CONTROL.buttonXS.pad) <= .01, "left padding is CONTROL.buttonXS.pad (the slice border is part of it)");
  assert.ok(Math.abs(rightPadding - CONTROL.buttonXS.pad) <= .01, "right padding is CONTROL.buttonXS.pad");
  assert.ok(Math.abs(body.h_ * sy - height) <= .01); assert.ok(Math.abs(body.w_ * sx - control.width) <= .01);
  const reference = NGUI.inkReference(caption), band = NGUI.inkBand(reference || caption, fontSizeFor(caption, label.fontSize));
  const ink = node.pos[1] - (fontSizeFor(caption, label.fontSize) * LINE_PITCH / 2 + (band.top + band.bottom) / 2) * ly;
  const borders = ui.atlasIndex().Common.Common_Btn_BG;
  const faceCentre = (borders.borderBottom - borders.borderTop) / 2;
  assert.ok(Math.abs((ink - faceCentre) * sy) <= .5, `${locale}: ${ink * sy} px off centre`);
  assert.ok(control.minHeight <= height, "native body corner slices fit");
});

const { spriteMaskTransform } = await import("../../src/bmpt/web/js/sprite-mask.js");
for (const locale of LOCALES) test(`clipping: Choose Files keeps native decoration inside the badge-height body in ${locale}`, async () => {
  setStrings(locale, {});
  const rowArt = art(), control = chooseSize(rowArt), caption = missingLabel("choose", {}, locale), height = (ROW_TEXT_SIZE * 1.3 + 8) * ROW_SCALE;
  const original = find(prefabs.UIMailBoxContent, "List_01/Btn_Receive"), originalBody = original.widgets.find((w) => w.sprite === "Common_Btn_BG");
  for (const [width, tall] of [[1024, 768], [1920, 1080], [3840, 2160]]) for (const density of [1, 2, 3]) {
    const scale = CHOOSE.scale * Math.min(width / 1920, tall / 1080);
    const tree = rowArt.button(caption, control.width / CHOOSE.scale, false, true, control.size, height / CHOOSE.scale);
    const { view } = await draw(tree, scale, [0, 0]);
    globalThis.devicePixelRatio = density;
    for (const entry of view.nodes.values()) for (const widget of entry.node.widgets) if (widget.sprite && entry.els[0]) {
      entry.els[0].style.transform = `skewX(${-widget.incline || 0}deg) scale(${widget.flip === 1 || widget.flip === 3 ? -1 : 1}, ${widget.flip >= 2 ? -1 : 1})`;
    }
    await clipButtonPatterns(view);
    let patterns = 0;
    for (const [path, entry] of view.nodes) {
      const widget = entry.node.widgets.find((w) => /^Common_Btn_.*_Pt$/.test(w.sprite || "")); if (!widget) continue;
      patterns++;
      const native = original.children.find((n) => n.name === entry.node.name), nativeWidget = native.widgets.find((w) => w.sprite === widget.sprite);
      assert.equal(widget.h_, nativeWidget.h_); assert.deepEqual(entry.node.scale, native.scale, "native pattern size and scale");
      const parent = view.nodes.get(path.slice(0, path.lastIndexOf("/"))), body = parent.node.widgets.find((w) => w.sprite === "Common_Btn_BG");
      assert.ok(Math.abs(entry.node.pos[1] - body.h_ / 2 - (native.pos[1] - originalBody.h_ / 2)) <= 1e-9, "native top-edge offset");
      const pattern = entry.els[0], bg = parent.els[0], points = pattern.style.clipPath.match(/-?[\d.]+(?=px)/g).map(Number);
      assert.equal(points.length, 8);
      // Map the polygon out of the pattern's local coordinates into the body's rectangle, undoing both sprite transforms.
      const [a, b, c, d, e, f] = spriteMaskTransform(pattern.style, bg.style);
      for (let i = 0; i < points.length; i += 2) {
        const x = a * points[i] + c * points[i + 1] + e, y = b * points[i] + d * points[i + 1] + f;
        assert.ok(x >= -.01 && x <= parseFloat(bg.style.width) + .01);
        assert.ok(y >= -.01 && y <= parseFloat(bg.style.height) + .01);
      }
    }
    assert.ok(patterns > 0);
  }
  delete globalThis.devicePixelRatio;
});

// the footer's buttons, as batch-import.js sizes them: the three of the row and the list action, one caption size
const FOOTER_SCALE = XS_BUTTON.h * S / BUTTON_BODY_H, WIDE_PX = 330, NARROW_PX = XS_BUTTON.w * S;
const FOOTER_RANGE = { max: XS_BUTTON.fs * BUTTON_BODY_H / XS_BUTTON.h, min: XS_BUTTON.fsMin * BUTTON_BODY_H / XS_BUTTON.h };

for (const locale of LOCALES) test(`the list action shares the footer row's caption size and fits its 330 px button in ${locale}`, () => {
  setStrings(locale, {});
  const rowArt = art();
  for (const n of [1, 2, 3, 25, 999]) {
    const captions = [text(locale, "import.options.title"), missingLabel("fillAll", { n }, locale), text(locale, "import.recheck"),
      text(locale, "import.action", { n: 12 })];
    const widths = [WIDE_PX, WIDE_PX, NARROW_PX, NARROW_PX].map((px) => px / FOOTER_SCALE);
    const size = rowArt.buttonSize(captions, widths, FOOTER_RANGE);
    assert.ok(size >= FOOTER_RANGE.min - 1e-9 && size <= FOOTER_RANGE.max + 1e-9, `${locale} ${n}: ${size}`);
    captions.forEach((caption, i) => {
      const tree = rowArt.button(caption, widths[i], i === 3, true, size), label = textOf(find(tree, "Label_Locale_Use") || find(tree, "Label_Locale"));
      assert.equal(label.fontSize, size, "one size across the row");
      assert.ok(lines(label.text).length <= 2, `${caption}: ${lines(label.text).length} lines`);
      assert.ok(widest(label.text, label.fontSize) <= widths[i] - XS_BUTTON.fitInset + 1e-9, `${caption}: ${widest(label.text, label.fontSize)} wide in ${widths[i] - XS_BUTTON.fitInset}`);
      assert.equal(label.text.replace(/\s/g, ""), caption.replace(/\s/g, ""));
    });
  }
});

test("the footer's count and the list action leave the left edge room: the note's room is positive and ends before the action", () => {
  const footer = { left: 957, gap: XS_BUTTON.gap, wide: WIDE_PX };
  assert.ok(noteRoom(footer) > 400, `${noteRoom(footer)} px`);
  assert.equal(NOTE_LEFT + noteRoom(footer) + 24, footer.left - footer.gap - footer.wide);
});

for (const locale of LOCALES) test(`the footer count fits its room on at most two lines at the prefab's size in ${locale}`, () => {
  setStrings(locale, {});
  const room = noteRoom({ left: 957, gap: XS_BUTTON.gap, wide: WIDE_PX }) / S, tree = art().note("x"), label = textOf(tree);
  for (const n of [1, 2, 9, 25, 99, 999]) {
    const caption = groupsText(n, locale), fit = NGUI.fitText(caption, label.fontSize, label.fontSize * 0.7, room, 2);
    assert.ok(!fit.overflow, `${caption} overflows`);
    assert.ok(lines(fit.text).length <= 2 && widest(fit.text, fit.fs) <= room + 1e-9, `${caption}: ${widest(fit.text, fit.fs)} in ${room}`);
    assert.equal(fit.text.replace(/\s/g, ""), caption.replace(/\s/g, ""));
    assert.ok(fit.fs >= label.fontSize * 0.7 - 1e-9);
  }
});

for (const locale of LOCALES) test(`the filter's "missing files" caption wraps inside the Import State button's body and is never ellipsized in ${locale}`, () => {
  setStrings(locale, {});
  const rowArt = art();
  for (const n of [0, 5, 99, 999]) for (const filtered of [false, true]) {
    const caption = text(locale, "import.filterCount", { state: missingLabel("state", {}, locale), n });
    const tree = rowArt.header(1272 / S, "List", caption, filtered, true, false);
    const type = find(tree, "SortBtn/Btn_Normal/Type_2"), node = find(type, "Text"), w = textOf(node);
    const body = find(tree, "SortBtn/Btn_Normal").widgets.find((x) => "sprite" in x);
    assert.ok(!w.ellipsis && lines(w.text).length <= 2);
    assert.equal(w.text.replace(/\s/g, ""), caption.replace(/\s/g, ""));
    assert.ok(widest(w.text, w.fontSize) <= 336 + 1e-9);
    assert.ok(Math.abs(type.pos[1] + node.pos[1]) + stack(w.text, w.fontSize) / 2 <= body.h_ / 2 + 1e-9);
    assert.ok(w.fontSize >= 36, `${caption} shrinks to ${w.fontSize}`);
  }
});

// the prompt on the popup frame: plain left-aligned lines (popup.js), the files by their official names
const labelsOf = (layout) => layout.body.tree.children.flatMap((child) => child.widgets.filter((w) => "text" in w));
const OFFICIAL = Array.from({ length: 12 }, (_, i) => `assets-_mx-characters-ch0258-_mxdependency-2026-10-25_${String(i).padStart(3, "0")}_assets_all_1234567890.bundle`);
const LONG_NAME = "Hoshino (Armed, School Swimsuit, Sky Blue)";

for (const locale of LOCALES) test(`the prompt fits the popup frame in ${locale}: official file names wrap whole, the sentence stays, the frame never outgrows the stage`, () => {
  setStrings(locale, {});
  const kind = text(locale, "tab.model");
  for (const count of [1, 3, 5, 9]) {
    const files = OFFICIAL.slice(0, count), promptLines = [missingLabel("promptHead", {}, locale), ...files.map((name) => missingLabel("promptFile", { name }, locale)),
      missingLabel("promptTail", { student: LONG_NAME, kind }, locale)];
    const layout = Popup.messageLayout({ lines: promptLines, plain: true, fitTogether: promptLines.map((_, i) => i), ok: missingLabel("addTogether", {}, locale),
      cancel: text(locale, "popup.cancel") });
    const room = layout.content.w;
    const labels = labelsOf(layout);
    assert.equal(labels.length, promptLines.length);
    labels.forEach((label, i) => {
      assert.equal(label.text.replace(/\s/g, ""), promptLines[i].replace(/\s/g, ""), "nothing dropped or ellipsized");
      assert.ok(widest(label.text, label.fontSize) <= room + 1e-9, `${promptLines[i].slice(0, 30)}: ${widest(label.text, label.fontSize)} in ${room}`);
      assert.ok(label.fontSize >= 40 - 1e-9, `${label.fontSize}`);  // POPUP_TYPE.text's floor (BL-176)
    });
    assert.ok(layout.height <= Popup.MSG.cap + 1e-9);
    if (count >= 9) assert.equal(layout.scroll, true, "a long list scrolls inside the frame");
  }
});

for (const locale of LOCALES) test(`the list action's confirmation lists 25 rows inside the frame and its buttons share one size in ${locale}`, () => {
  setStrings(locale, {});
  const rows = Array.from({ length: 25 }, (_, i) => missingLabel("fillLine", { student: `${LONG_NAME} ${i}`, kind: text(locale, "tab.lobby"), n: 5 }, locale));
  const layout = Popup.messageLayout({ lines: rows, plain: true, fitTogether: rows.map((_, i) => i), ok: missingLabel("addTogether", {}, locale),
    cancel: text(locale, "popup.cancel") });
  assert.equal(layout.scroll, true); assert.ok(layout.height <= Popup.MSG.cap + 1e-9);
  const room = layout.content.w;
  for (const label of labelsOf(layout)) assert.ok(widest(label.text, label.fontSize) <= room + 1e-9);
  // the footer's pair: Cancel and the owner's Add label at one size within the default footer's range (XS, BL-176)
  const caption = Popup.FOOTER[Popup.FOOTER_DEFAULT].caption, pair = [text(locale, "popup.cancel"), missingLabel("addTogether", {}, locale)];
  const fits = Popup.footerFit(pair);
  assert.equal(new Set(fits.map((f) => f.fs)).size, 1);
  fits.forEach((f, i) => {
    assert.ok(!f.overflow && f.fs >= caption.fsMin, `${pair[i]} at ${f.fs}`);
    assert.ok(widest(f.text, f.fs) <= caption.w + 1e-9);
  });
});

// N-002: a refused row's badge opens its details; the hit area is the badge background's box, wrapped or not, every language.
for (const locale of LOCALES) test(`badge-hit: the details hit area covers the badge background in ${locale}`, () => {
  setStrings(locale, {});
  const rowArt = art();
  for (const state of statuses(locale)) for (const meta of METAS) for (const stateKey of ["blocked", "incomplete"]) {
    const tree = rowArt.row({ name: "Student", kind: text(locale, "tab.lobby"), state, stateKey, meta, muted: true });
    const m = measure(tree), bgWidth = find(m.status, "Bg").widgets[0].w_, [x, y, w, h] = badgeRect(tree);
    const left = ROW_WIDTH / 2 + (m.right - bgWidth) * ROW_SCALE, right = ROW_WIDTH / 2 + m.right * ROW_SCALE;
    assert.ok(Math.abs(x - left) <= .01 && Math.abs(x + w - right) <= .01, `${locale} ${state}: same left and right as the background`);
    assert.ok(Math.abs(y - (329 / 2 - m.bgTop) * ROW_SCALE) <= .01, `${locale} ${state}: same top as the background`);
    assert.ok(Math.abs(y + h - (329 / 2 - m.bgBottom) * ROW_SCALE) <= .01, `${locale} ${state}: same bottom as the background`);
  }
  assert.equal(badgeRect(rowArt.row({ name: "Student", kind: "", state: "x", stateKey: "blocked", meta: "", badge: false })), null);
});

}

