// Text fit of the Batch Import header and name plate, the option rows, the roster's cards and the Show in Explorer button, in
// every shipped language: the real locale strings through the fit logic of web/js/ngui.js against the prefabs' numbers (a
// stand-in canvas, see helpers/fit_canvas.mjs: what is asserted is the logic, not a font).
import assert from "node:assert/strict";
import { test } from "node:test";
import { LOCALES, installFitPage, text } from "./helpers/fit_canvas.mjs";
import { makeImportLayouts } from "./import_prefabs.mjs";

const page = installFitPage();
const NGUI = await import("../../src/bmpt/web/js/ngui.js");
const { importArtwork } = await import("../../src/bmpt/web/js/import-layout.js");
const { S } = await import("../../src/bmpt/web/js/util.js");

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
const { radioRow } = await import("../../src/bmpt/web/js/settings-rows.js");
// the language picker's order (i18n.py UI_LANGUAGES), each option named in its own language
const LANGUAGES = ["zh-CN", "zh-TW", "en-US", "ja-JP", "ko-KR", "th-TH"];
// the labels a radio row draws, in order: [widget, node]
const optionLabels = (row) => row.tree.children.filter((c) => /^Opt_\d+$/.test(c.name)).map((c) => find(c, "Label").widgets[0]);
const noop = () => {};
// the pitch and the room the row fits a label to (settings-rows.js): the option's pitch (508, or the content box's 1552 less
// 2 x 58 shared by the columns) less the radio (60), its gap to the label (19), the clear of the next option (12) and the
// fit's slack (20)
const roomOf = (columns) => Math.min(508, (1552 - 2 * 58) / columns) - (60 + 19 + 12 + 20);
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

for (const locale of LOCALES) test(`Match Strategy and the other option rows draw one size in ${locale}`, () => {
  page.lang = locale;
  const rows = {
    strategy: ["cont_name_type", "path_id", "name_type"].map((key) => text(locale, `import.strategy.${key}`)),
    crc: [text(locale, "import.auto"), text(locale, "settings.post.on"), text(locale, "settings.post.off")],
    compression: ["LZMA", "LZ4", text(locale, "import.original"), text(locale, "import.uncompressed")],
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

// --- UI-19: text that sank far under its floor ---
import { readFileSync } from "node:fs";
const catalog = JSON.parse(readFileSync(new URL("../../src/bmpt/catalog/data/catalog.json", import.meta.url), "utf8"));
const NAME_KEY = { "en-US": "en", "zh-CN": "zh-CN", "zh-TW": "zh-TW", "ja-JP": "ja", "ko-KR": "ko", "th-TH": "th" };
// every variant's full name in a locale's language (Thai falls back to the English names, as the app does)
const namesOf = (locale) => Object.values(catalog.variants).map((v) => v.names?.[NAME_KEY[locale]]?.full || v.names?.en?.full).filter(Boolean);
const separator = (locale) => (/^(zh|ja)/.test(locale) ? "、" : ", ");

// the Show in Finder / Explorer button: Btn_Up's body is 200 x 180 (inclined), its caption 32 down to 26 in 140
const { explorerLayout, BODY, ICON, CAPTION } = await import("../../src/bmpt/web/js/explorer-button.js");
const { TAN10 } = await import("../../src/bmpt/web/js/util.js");
for (const locale of LOCALES) test(`the Show in Finder / Explorer caption wraps onto up to three lines inside the button at 26 or more in ${locale}`, () => {
  page.lang = locale;
  for (const key of ["open.mac", "open.win"]) {
    const caption = text(locale, key), r = explorerLayout(caption), where = `${locale} ${key} ${JSON.stringify(r.text)}`;
    assert.ok(!r.overflow, `${where}: overflows`);
    assert.ok(r.lines <= 3 && lines(r.text).length === r.lines, where);
    assert.equal(r.text.replace(/\s/g, ""), caption.replace(/\s/g, ""), "every character kept");
    // the prefab's own range: 32 down to the floor of 26, never under it
    assert.ok(r.fs >= CAPTION.fsMin && r.fs <= CAPTION.fsMax, `${where}: size ${r.fs}`);
    assert.ok(widest(r.text, r.fs) <= CAPTION.room + 1e-9, `${where}: ${widest(r.text, r.fs)} wide in ${CAPTION.room}`);
    // the lines' box sits in the body under the folder icon, clear of both, and the icon in the body
    const [labelX, labelY, boxH] = r.label, [folderX, folderY] = r.folder, ink = stack(r.text, r.fs);
    assert.ok(boxH >= ink - 1e-9, `${where}: the label box (${boxH}) holds the lines (${ink})`);
    assert.ok(labelY + ink / 2 <= folderY - ICON.h / 2 + 1e-9, `${where}: the caption's top ${labelY + ink / 2} reaches the icon's bottom ${folderY - ICON.h / 2}`);
    assert.ok(labelY - ink / 2 >= BODY.bottom + 10 - 1e-9, `${where}: the caption's bottom ${labelY - ink / 2} is under the body's ${BODY.bottom}`);
    assert.ok(folderY + ICON.h / 2 <= BODY.top - 4, `${where}: the icon leaves the body's top`);
    // both follow the body's slant
    assert.ok(Math.abs(labelX - (BODY.x + labelY * TAN10)) < 1e-9 && Math.abs(folderX - (BODY.x + folderY * TAN10)) < 1e-9, where);
  }
});

test("the Show in Explorer caption no longer sinks under 26 where the old two-line fit did (every language)", () => {
  let worse = 0, better = 0;
  for (const locale of LOCALES) {
    page.lang = locale;
    for (const key of ["open.mac", "open.win"]) {
      const old = NGUI.fitText(text(locale, key), 32, 26, 140), now = explorerLayout(text(locale, key));
      if (now.fs < old.fs) worse++;
      if (old.fs < 26 && now.fs >= 26) better++;
    }
  }
  assert.equal(worse, 0, "never smaller than before");
  assert.ok(better >= 1, "at least the Japanese Explorer caption was under its floor before");
});

test("a Japanese word wider than the button breaks between characters rather than shrinking the caption", () => {
  page.lang = "ja-JP";
  const r = explorerLayout(text("ja-JP", "open.win"));
  assert.equal(r.lines, 3); assert.ok(r.fs >= 26);
  // the kinsoku rules hold: no line starts with the prolonged sound mark or small kana
  assert.ok(lines(r.text).every((line) => !/^[ーァィゥェォッャュョ]/.test(line)), JSON.stringify(r.text));
  // groups under fsMin elsewhere are unaffected: a Latin caption never breaks inside a word
  page.lang = "en-US";
  assert.ok(lines(explorerLayout("Show in Explorer").text).every((line) => /^[A-Za-z ]+$/.test(line) && !/\s$/.test(line)));
  assert.deepEqual(NGUI.fitGroup([], 32, 26, 140, 3), []);
});

// the prompt cards' labels (prompts.js card, popup.js listCard: 34 down to 24, two lines, 250 - 56 wide)
const { markedLabel } = await import("../../src/bmpt/web/js/util.js");
test("the prompt cards draw the pieces this test measures", () => {
  const popup = readFileSync(new URL("../../src/bmpt/web/js/popup.js", import.meta.url), "utf8");
  assert.match(popup, /LIST_CARD_NAME = Object\.freeze\(\{ fs: 34, fsMin: 24, lines: 2, padY: 22, pitch: 1\.2 \}\)/);
  assert.match(popup, /markedLabel\(it\.label, \{ fs: LIST_CARD_NAME.fs, fsMin: LIST_CARD_NAME.fsMin, color: "#ffffff\/1\.00", maxW: w - 56 - \(it\.nameEdit \? 60 : 0\), mark: false, maxLines: LIST_CARD_NAME.lines \}\)/);
  assert.match(popup, /const LP = \{[\s\S]*?card: \{ w: 250, h: 226, bar: 68 \}/);
  const prompts = readFileSync(new URL("../../src/bmpt/web/js/prompts.js", import.meta.url), "utf8");
  assert.doesNotMatch(prompts.slice(prompts.indexOf("export function card(")), /kindLabel\(entry\)\}`/, "the kind is not appended to the label");
});

for (const locale of LOCALES) test(`dropping the kind from a prompt card's label never makes its text smaller, over every catalog name in ${locale}`, () => {
  page.lang = locale;
  const kind = text(locale, "tab.lobby"), fit = (label) => markedLabel(label, { fs: 34, fsMin: 24, color: "#fff", maxW: 250 - 56, maxLines: 2 });
  let under = 0, wasUnder = 0, total = 0;
  for (const name of namesOf(locale)) {
    const now = fit(name), before = fit(`${name}${separator(locale)}${kind}`);
    assert.ok(now.fs >= before.fs, `${name}: ${now.fs} against ${before.fs}`);
    assert.ok(now.lines <= 2);
    total++; if (now.fs < 24) under++; if (before.fs < 24) wasUnder++;
  }
  assert.ok(total > 600);
  assert.ok(under < wasUnder, `${locale}: ${under} names under 24 now against ${wasUnder} with the kind`);
});

// the Hall's student cards (student-hall-ui.js formationCardTree: 36 down to 24, two lines, 190 wide)
const hall = await (async () => {
  let source = readFileSync(new URL("../../src/bmpt/web/js/student-hall-ui.js", import.meta.url), "utf8");
  const { CONTROL, TYPE, SPACE, SMALL_BUTTON } = await import("../../src/bmpt/web/js/ui-tokens.js");
  const { stack: layoutStack } = await import("../../src/bmpt/web/js/ui-grid.js");
  globalThis.hallHarness = { NGUI, Popup: {}, clone: structuredClone, Loading: {}, withTimeout: () => {}, T: (key) => key, CONTROL, TYPE, SPACE, SMALL_BUTTON, layoutStack };
  source = source.replace(/^import .*;$/gm, "");
  source = `const { Popup, clone, NGUI, Loading, withTimeout, T, CONTROL, TYPE, SPACE, SMALL_BUTTON, layoutStack } = globalThis.hallHarness; const S = 1920 / 2960;\n${source}\n//# sourceURL=hall-ui-fit.js`;
  return import(`data:text/javascript;base64,${Buffer.from(source).toString("base64")}`);
})();
const hallLabel = (name) => {
  const n = (nodeName, children = [], widgets = []) => ({ name: nodeName, children, widgets, pos: [0, 0], scale: [1, 1] });
  const normal = n("Normal", [n("Card_Char", [], [{ texture: "sample", w_: 252, h_: 204 }])]);
  const card = n("UIPopupFormationCharSlot", [n("Bg"), n("UICharacterCard", [normal]),
    n("Sprite", [n("Type_2 (1)", [n("Text", [], [{ text: "sample", fontSize: 36, w_: 190, h_: 77, pivot: 4 }])])]), n("Cover_Sell_Select")], [{ sprite: "x", w_: 256, h_: 347, pivot: 1 }]);
  let formation = card;
  for (const key of ["UIFormationCharSlotGroup", "Grid", "Scroll View", "Inbox", "RightBg", "Popup", "Center"]) formation = n(key, [formation]);
  const tree = hall.formationCardTree(n("Root", [formation]), { key: "a", name, thumb: "portrait" }, false);
  return find(tree, "Sprite/Type_2 (1)/Text").widgets[0];
};
for (const locale of LOCALES) test(`a Terror form's name wraps at its asterisk inside the Hall card at 24 or more in ${locale}`, () => {
  page.lang = locale;
  // the bare Terror names: character, asterisk, form, no brackets
  const bare = namesOf(locale).filter((name) => /^[^*＊（(]+[*＊][^（(]+$/.test(name));
  assert.ok(bare.length >= 1, "the catalog has a bare Terror name");
  for (const name of bare) {
    const w = hallLabel(name);
    assert.ok(w.fontSize >= 24, `${name}: ${w.fontSize}`);
    assert.ok(lines(w.text).length <= 2);
    assert.equal(w.text.replace(/\s/g, ""), name.replace(/\s/g, ""));
    assert.ok(widest(w.text, w.fontSize) <= 190 + 1e-9, `${name}: ${widest(w.text, w.fontSize)} wide in 190`);
    // wrapped, it breaks after the asterisk, never inside the character's or the form's name
    if (lines(w.text).length === 2) assert.match(lines(w.text)[0], /[*＊]$/, JSON.stringify(w.text));
  }
});

test("a break may follow an asterisk, in either width, and only there", () => {
  page.lang = "en-US";
  const wrapped = NGUI.fitText("Shiroko*Terror", 36, 24, 190, 2);
  assert.equal(wrapped.text, "Shiroko*\nTerror"); assert.equal(wrapped.fs, 24);
  const full = NGUI.fitText("Shiroko＊Terror", 36, 24, 190, 2);
  assert.equal(full.text, "Shiroko＊\nTerror");
  // a star at the very start or end (a footnote mark) is not a break, and ordinary words keep wrapping at spaces
  assert.equal(NGUI.fitText("*Shiroko", 36, 24, 60, 2).text.replace(/\n/g, ""), "*Shiroko");
  assert.ok(!lines(NGUI.fitText("*Shiroko", 36, 24, 60, 2).text).includes("*"));
  assert.equal(NGUI.fitText("Hoshino (Armed)", 36, 24, 190, 2).text, "Hoshino\n(Armed)");
});
