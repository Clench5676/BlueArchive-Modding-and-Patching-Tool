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
    // the prefab's own range is 56 down to 70 %: 39.2. Under it only where the words allow no larger size on two lines, and
    // then by a step or two
    assert.ok(w.fontSize >= 36, `${locale} ${JSON.stringify(w.text)} shrinks to ${w.fontSize}`);
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
  assert.ok(long.fontSize >= 36 && long.fontSize < 56, `${long.fontSize}`);
});

// --- UI-10: the Batch Import name plate (Items Equip_Target: BG 1246 x 204 at y 98, Label_Name 104 at y 93, room 1160) ---
// a breakable name whose one-line width at the prefab's floor (72.8) is at least `target`, built from a variant's parts
function nameOfWidth(target, parts, from = 0) {
  let name = parts[from];
  for (let i = from + 1; glyphWidth(name, 72.8) < target; i++) name += `${parts === KANA ? "、" : ", "}${parts[i % parts.length]}`;
  return name;
}
const LATIN = ["Hoshino (Armed", "Gun", "Pistol", "Wounded", "Rifle", "Sniper", "Mace", "Bow", "Axe", "Club"];
const KANA = ["ホシノ（武装", "銃", "拳銃", "負傷", "ライフル", "水着", "私服", "冬服", "祭り", "盾"];
// the review's lengths: 1160 (one line at the floor), up to the longest catalog name, 1849 units
const WIDTHS = [600, 1100, 1160, 1161, 1300, 1500, 1700, 1849, 2000];

for (const [locale, parts] of [["en-US", LATIN], ["th-TH", LATIN], ["ja-JP", KANA], ["zh-CN", KANA], ["ko-KR", LATIN]]) {
  test(`a long name stays inside the Batch Import name plate on two lines in ${locale}`, () => {
    page.lang = locale;
    const art = artwork();
    for (const target of WIDTHS) {
      const name = nameOfWidth(target, parts), tree = art.banner(name, "Portrait");
      const plate = find(tree, "BG"), node = find(tree, "Label_Name"), w = node.widgets.find((x) => "text" in x);
      const body = plate.widgets.find((x) => "sprite" in x);
      assert.ok(!w.ellipsis, `${target}: ellipsized`);
      assert.ok(lines(w.text).length <= 2, `${target}: ${lines(w.text).length} lines`);
      assert.equal(w.text.replace(/\s/g, ""), name.replace(/\s/g, ""), "every character kept");
      assert.ok(widest(w.text, w.fontSize) <= 1160 + 1e-9, `${target}: ${widest(w.text, w.fontSize)} wide in 1160`);
      // the lines' box round the label's centre inside the plate's top and bottom edges
      const top = plate.pos[1] + body.h_ / 2, bottom = plate.pos[1] - body.h_ / 2, reach = stack(w.text, w.fontSize) / 2;
      assert.ok(node.pos[1] + reach <= top && node.pos[1] - reach >= bottom, `${target}: ${stack(w.text, w.fontSize)} tall on ${bottom}..${top}`);
      assert.ok(node.pos[1] + w.h_ / 2 <= top + 1e-9 && node.pos[1] - w.h_ / 2 >= bottom - 1e-9, `${target}: the label box leaves the plate`);
      // the label starts at the plate's left and its room ends inside the plate's right edge
      assert.ok(node.pos[0] >= plate.pos[0] - body.w_ / 2 && node.pos[0] + w.w_ <= plate.pos[0] + body.w_ / 2);
      // the prefab's own range is 104 down to 70 %
      assert.ok(w.fontSize >= 72.8 || target > 1849, `${target}: shrinks to ${w.fontSize}`);
    }
  });
}

test("a short name keeps the prefab's size on one line and a name that fits shrinks before it wraps", () => {
  page.lang = "en-US";
  const art = artwork(), plate = (name) => find(art.banner(name, "Portrait"), "Label_Name").widgets[0];
  assert.deepEqual([plate("Airi").fontSize, plate("Airi").text], [104, "Airi"]);
  // one line fits between 72.8 and the prefab's size: it shrinks on one line rather than wrapping
  const fits = nameOfWidth(1000, LATIN);
  assert.ok(glyphWidth(fits, 72.8) <= 1160, `${glyphWidth(fits, 72.8)}`);
  const middle = plate(fits);
  assert.equal(lines(middle.text).length, 1);
  assert.ok(middle.fontSize > 72.8 && middle.fontSize < 104, `${middle.fontSize}`);
});

test("a name with no word break to wrap at is not cut off by the plate", () => {
  page.lang = "en-US";
  const name = "Hoshino_Armed_Shotgun_Handgun_Wounded_Sniper_Rifle_Swimsuit_Casual_Winter_Festival_v3_final.bundle";
  const w = find(artwork().banner(name, "Portrait"), "Label_Name").widgets[0];
  assert.equal(lines(w.text).length, 1);
  assert.ok(glyphWidth(name, 72.8) > 1160 * 1.4, "longer than two lines of the room at the floor");
  assert.ok(w.ellipsis === true || widest(w.text, w.fontSize) <= 1160, `${widest(w.text, w.fontSize)} wide, ellipsis ${w.ellipsis}`);
});

// --- UI-11: options side by side share one size (settings-rows.js radioRow: Language, Match Strategy, Spine ...) ---
const { radioRow } = await import("../../src/ba_modding_toolkit/web/js/settings-rows.js");
// the language picker's order (i18n.py UI_LANGUAGES), each option named in its own language
const LANGUAGES = ["zh-CN", "zh-TW", "en-US", "ja-JP", "ko-KR", "th-TH"];
// the labels a radio row draws, in order: [widget, node]
const optionLabels = (row) => row.tree.children.filter((c) => /^Opt_\d+$/.test(c.name)).map((c) => find(c, "Label").widgets[0]);
const noop = () => {};
// the pitch and the room the row fits a label to (settings-rows.js)
const roomOf = (columns) => Math.min(508, 1400 / columns) - 110;
// a text fits at X when fitText holds it there with nothing smaller allowed, and its lines stay in the band
const fitsAt = (value, fs, room, lang = null, band = 96) => {
  const f = NGUI.fitText(value, fs, fs, room, 2, lang);
  return f.fs === fs && stack(f.text, fs) <= band;
};

function assertSharedRow(row, texts, columns, where, langs = []) {
  const labels = optionLabels(row), room = roomOf(columns), sizes = new Set(labels.map((w) => w.fontSize));
  assert.equal(labels.length, texts.length);
  assert.equal(sizes.size, 1, `${where}: one size, got ${[...sizes]}`);
  labels.forEach((w, i) => {
    assert.equal(w.lang, langs[i], `${where}: option ${i} keeps its own language`);
    assert.equal(w.text.replace(/\s/g, ""), texts[i].replace(/\s/g, ""), "every character kept");
    assert.ok(lines(w.text).length <= 2, `${where}: ${texts[i]} on ${lines(w.text).length} lines`);
    assert.ok(widest(w.text, w.fontSize) <= room + 1e-9, `${where}: ${JSON.stringify(w.text)} is ${widest(w.text, w.fontSize)} wide in ${room}`);
    // under the dotted line (109 below the row's top, 4 thick) and above the row's bottom (240) round the option's centre at
    // 174.5, and no taller than the 100 between option lines where the options take more than one row
    const band = texts.length > columns ? 96 : 120;
    assert.ok(stack(w.text, w.fontSize) <= band + 1e-9, `${where}: ${JSON.stringify(w.text)} is ${stack(w.text, w.fontSize)} tall`);
    assert.ok(174.5 - stack(w.text, w.fontSize) / 2 >= 113 && 174.5 + stack(w.text, w.fontSize) / 2 <= 240, `${where}: leaves the row`);
  });
  // the largest size at which every one fits, give or take the step or two that wrapping must win to be worth it (fitGroup)
  const fs = labels[0].fontSize;
  if (fs <= 42 && lines(labels[0].text).length === 1) assert.ok(texts.some((value, i) => !fitsAt(value, fs + 6, room, langs[i] || null, texts.length > columns ? 96 : 120)), `${where}: ${fs} could be larger`);
}

test("the Language row draws every option at one size, each measured in its own language", () => {
  for (const locale of LOCALES) {
    page.lang = locale;
    const names = LANGUAGES.map((code) => text(code, "languageName"));
    const row = radioRow("Language", 0, text(locale, "settings.language"), LANGUAGES.map((code, i) => [code, names[i], code]), "en-US", noop);
    assertSharedRow(row, names, LANGUAGES.length, `${locale} Language`, LANGUAGES);
  }
});

test("the language of each label decides how it is measured", () => {
  page.lang = "en-US";
  const wide = "Wide label of some length", short = "Short";
  const [own, other] = NGUI.fitGroup([wide, short], 48, 34, 150, 2, { lang: ["sv", null] });
  const alone = NGUI.fitGroup([wide], 48, 34, 150, 2, { lang: "sv" })[0], plain = NGUI.fitGroup([wide], 48, 34, 150, 2)[0];
  assert.ok(alone.fs < plain.fs, "the wide stack measures wider, so the same text takes a smaller size");
  assert.equal(own.fs, Math.min(alone.fs, NGUI.fitGroup([short], 48, 34, 150, 2)[0].fs), "the group's size is the wide label's");
  assert.equal(other.fs, own.fs);
  // the same list through fitLabels: each widget's own lang
  const widgets = [{ lang: "sv" }, {}];
  NGUI.fitLabels(widgets, [wide, short], 48, 34, 150);
  assert.equal(widgets[0].fontSize, own.fs);
  assert.equal(widgets[1].fontSize, own.fs);
  assert.equal(page.lang, "en-US");
});

for (const locale of LOCALES) test(`Match Strategy, Spine and the other option rows draw one size in ${locale}`, () => {
  page.lang = locale;
  const rows = {
    strategy: ["cont_name_type", "path_id", "name_type"].map((key) => text(locale, `import.strategy.${key}`)),
    crc: [text(locale, "import.auto"), text(locale, "settings.post.on"), text(locale, "settings.post.off")],
    compression: ["LZMA", "LZ4", text(locale, "import.original"), text(locale, "import.uncompressed")],
    spine: [text(locale, "spine.license.yes"), text(locale, "spine.license.no")],
    onOff: [text(locale, "settings.post.on"), text(locale, "settings.post.off")],
  };
  for (const [key, texts] of Object.entries(rows)) {
    const columns = key === "strategy" || key === "crc" || key === "compression" ? Math.min(texts.length, 3) : texts.length;
    const row = radioRow(`Option_${key}`, 0, "Title", texts.map((value, i) => [i, value]), 0, noop, { columns });
    assertSharedRow(row, texts, columns, `${locale} ${key}`);
  }
});

// the display settings: the five sort keys (UISortObjectListUnit labels, 360 wide) and the eight filter options
// (UIFilterObjectListUnit labels, 380 wide) are each one group; display.js fits them together (shareSize)
for (const locale of LOCALES) test(`the display settings' sort keys and filter options fit as one group each in ${locale}`, () => {
  page.lang = locale;
  const sorts = ["recent", "added", "name", "mods", "default"].map((key) => text(locale, `sort.${key}`));
  const filters = [text(locale, "fav.on"), text(locale, "display.fav.off"), text(locale, "status.original"), text(locale, "status.replaced"),
    text(locale, "model.has"), text(locale, "model.none"), text(locale, "model.has"), text(locale, "model.none")];
  for (const [texts, fsMax, fsMin, room] of [[sorts, 48, 34, 360], [filters, 44, 32, 380]]) {
    const fits = NGUI.fitGroup(texts, fsMax, fsMin, room);
    assert.equal(new Set(fits.map((f) => f.fs)).size, 1, `${locale}: ${fits.map((f) => f.fs)}`);
    fits.forEach((f, i) => {
      assert.ok(!f.overflow && f.lines <= 2, `${locale} ${texts[i]}`);
      assert.ok(widest(f.text, f.fs) <= room + 1e-9, `${locale} ${JSON.stringify(f.text)} is ${widest(f.text, f.fs)} wide in ${room}`);
    });
  }
});
