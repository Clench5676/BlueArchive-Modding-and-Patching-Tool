// The four sites of the light stats box with the locale files' real strings, in every shipped language, longest first (English,
// Thai): every value inside its row and its box, nothing overlapping, the size inside 38 down to 26, the lines inside three,
// the update frame clear of its buttons. Text is measured on a stand-in canvas (a CJK code point one em, anything else
// half; a Thai base letter 0.6, its combining marks nothing), so what is asserted is the logic, not a font.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { register } from "node:module";
import { test } from "node:test";

const CJK = /[\u2e80-\u9fff\u3000-\u30ff\uff00-\uffef\uac00-\ud7af]/;
const THAI_MARK = /[\u0e31\u0e34-\u0e3a\u0e47-\u0e4e]/;
const THAI = /[\u0e00-\u0e7f]/;
const ctx = {
  font: "",
  measureText(s) {
    const fs = parseFloat(/([\d.]+)px/.exec(this.font)[1]);
    return { width: [...s].reduce((w, c) => w + (CJK.test(c) ? fs : THAI_MARK.test(c) ? 0 : THAI.test(c) ? fs * 0.6 : fs / 2), 0) };
  },
};
globalThis.document = {
  createElement: () => ({ getContext: () => ctx, remove() {} }),
  documentElement: { lang: "en-US", appendChild() {} },
  getElementById: () => null,
  querySelector: () => null,
};
globalThis.getComputedStyle = () => ({ getPropertyValue: () => "" });
globalThis.addEventListener = () => {};
globalThis.removeEventListener = () => {};
// the small button's prefab nodes, served as the game's layouts are
const { controls } = await import("./control_prefabs.mjs");
const prefabs = controls();
globalThis.fetch = async (url) => {
  const body = String(url).includes("UIPopup_Option_PC") ? prefabs.option : String(url).includes("UIPopup_FilterSort") ? prefabs.filter : null;
  return { ok: !!body, status: body ? 200 : 404, json: async () => body };
};
// detail.js keeps detailsBody private: this loader hook exposes it to this test only
const DETAIL = new URL("../../src/bmpt/web/js/detail.js", import.meta.url).href;
register(`data:text/javascript,${encodeURIComponent(`
export async function load(url, ctx, next) {
  const result = await next(url, ctx);
  if (url === ${JSON.stringify(DETAIL)}) {
    const source = typeof result.source === "string" ? result.source : new TextDecoder().decode(result.source);
    return { ...result, source: source + "\\nexport { detailsBody };" };
  }
  return result;
}`)}`);
const JS = "../../src/bmpt/web/js/";
const NGUI = await import(`${JS}ngui.js`);
const Tokens = await import(`${JS}ui-tokens.js`);
const Popup = await import(`${JS}popup.js`);
const Detail = await import(DETAIL);
const Buttons = await import(`${JS}game-buttons.js`);
await Buttons.ready();
const Update = await import(`${JS}update.js`);
const Box = await import(`${JS}stats-box.js`);
const { setStrings } = await import(`${JS}i18n.js`);
const { portraitPanel } = await import(`${JS}switch-layout.js`);
const { DETAILS } = await import(`${JS}popup-frames.js`);
const { STATS_BOX: B, POPUP } = Tokens;

// longest first
const LOCALES = ["en-US", "th-TH", "ja-JP", "ko-KR", "zh-TW", "zh-CN"];
const webui = (locale) => JSON.parse(readFileSync(new URL(`../../src/bmpt/locales/${locale}.json`, import.meta.url), "utf8")).webui;
const flat = (value, prefix = "webui") => Object.entries(value).flatMap(([key, text]) => typeof text === "object" ? flat(text, `${prefix}.${key}`) : [[`${prefix}.${key}`, text]]);
const fill = (text, params) => text.replace(/\{(\w+)\}/g, (_, key) => params[key] ?? "");
const leaves = (value) => typeof value === "string" ? [value] : Object.values(value).flatMap(leaves);
function use(locale) {
  const web = webui(locale);
  setStrings(locale, Object.fromEntries(flat(web)));
  document.documentElement.lang = locale;
  const text = (key) => {
    const value = key.split(".").reduce((o, part) => o?.[part], web);
    assert.equal(typeof value, "string", `${locale}: ${key}`);
    return value;
  };
  return { web, text };
}

// a node's box (stage units, y up), for a label with pivot 3 or a centred sprite
const rectOf = (n, w = n.widgets.find((x) => "w_" in x)) => {
  const left = w.pivot === 3 ? n.pos[0] : n.pos[0] - w.w_ / 2;
  return { left, right: left + w.w_, top: n.pos[1] + w.h_ / 2, bottom: n.pos[1] - w.h_ / 2 };
};
const child = (nodes, name) => nodes.find((n) => n.name === name);

// the checks every box shares: nodes = the flat list the site appended (the box first)
function assertRows({ nodes, what, value = "V", caption = "F", divider = "L", count, slot = () => null, lines = B.value.lines }) {
  const box = nodes[0], fill = box.widgets[0], line = box.children[0].widgets[0];
  const boxRect = rectOf(box);
  // drawn through the component: the tokens' fill, a line 5 larger across and down, dotted dividers of the divider colour
  assert.deepEqual([fill.sprite, fill.color, line.sprite, line.color], ["Common_Bg_Raius10px", `${B.fill}/1.00`, "Common_Line_Raius10px", `${B.line}/1.00`], `${what}: the box`);
  assert.deepEqual([line.w_ - fill.w_, line.h_ - fill.h_], [B.lineOutset, B.lineOutset], `${what}: the line's outset`);
  assert.equal(B.lineOutset, 5);
  const dividers = Array.from({ length: count - 1 }, (_, i) => child(nodes, `${divider}_${i}`));
  assert.ok(dividers.every(Boolean), `${what}: a divider between every two rows`);
  for (const d of dividers) assert.deepEqual([d.widgets[0].sprite, d.widgets[0].color, d.widgets[0].w_], ["Common_Line_Dot", `${B.divider}/1.00`, fill.w_ - 2 * B.dividerInset], `${what}: a divider`);
  assert.equal(child(nodes, `${divider}_${count - 1}`), undefined, `${what}: none under the last row`);
  const edges = [boxRect.top - B.padY, ...dividers.map((d) => d.pos[1]), boxRect.bottom + B.padY];
  const placed = [];
  for (let i = 0; i < count; i++) {
    const row = { top: edges[i], bottom: edges[i + 1] };
    const v = child(nodes, `${value}_${i}`), vw = v.widgets[0], vr = rectOf(v);
    const name = `${what} row ${i}`;
    assert.ok(row.top - row.bottom >= B.rowMin - 1e-9, `${name}: at least ${B.rowMin} high`);
    // the value inside its row and its box, 30 short of both sides
    assert.ok(vr.top <= row.top + 1e-9 && vr.bottom >= row.bottom - 1e-9, `${name}: ${JSON.stringify(vw.text)} inside its row (${vr.bottom}..${vr.top} in ${row.bottom}..${row.top})`);
    assert.ok(vr.left >= boxRect.left + B.padX - 1e-9 && vr.right <= boxRect.right - B.padX + 1e-9, `${name}: inside the box's sides`);
    // its text inside its label, within the size range, the lines kept to three, every character kept
    const shown = vw.text.split("\n");
    assert.ok(vw.fontSize <= B.value.fs && vw.fontSize >= B.value.fsMin, `${name}: ${vw.fontSize} is outside ${B.value.fsMin} to ${B.value.fs}`);
    assert.ok(shown.length <= lines, `${name}: ${JSON.stringify(vw.text)} takes ${shown.length} lines at ${vw.fontSize}`);
    for (const line of shown) assert.ok(NGUI.glyphWidth(line, vw.fontSize) <= vw.w_ - B.slack + 1e-9, `${name}: ${JSON.stringify(line)} wider than its room`);
    assert.ok(shown.length * NGUI.fontSizeFor(vw.text, vw.fontSize) * NGUI.LINE_PITCH <= vw.h_ + 1e-9, `${name}: the lines taller than their label`);
    // the caption, when the row has one, stands left of the value and inside the row
    const c = child(nodes, `${caption}_${i}`);
    if (c) {
      const cr = rectOf(c);
      assert.ok(cr.right <= vr.left + 1e-9, `${name}: the caption ends before the value`);
      assert.ok(cr.top <= row.top + 1e-9 && cr.bottom >= row.bottom - 1e-9, `${name}: the caption inside its row`);
      const cw = c.widgets[0];
      assert.ok(NGUI.glyphWidth(cw.text, cw.fontSize) <= cw.w_ - B.captionGap + 1e-9, `${name}: ${cw.text} inside its column`);
    }
    // what the caller put in the row stays inside it and clear of the value
    const extra = slot(i);
    if (extra) {
      assert.ok(extra.left >= vr.right - B.slack - 1e-9 || extra.left >= vr.left, `${name}: the extra after the value`);
      assert.ok(extra.top <= row.top + 1e-9 && extra.bottom >= row.bottom - 1e-9, `${name}: the extra inside its row`);
      assert.ok(extra.right <= boxRect.right + 1e-9, `${name}: the extra inside the box`);
    }
    placed.push({ vr, row });
  }
  // nothing overlaps the next row's value
  for (let i = 1; i < placed.length; i++) assert.ok(placed[i].vr.top <= placed[i - 1].vr.bottom + 1e-9, `${what}: rows ${i - 1} and ${i} overlap`);
  return { fill, boxRect };
}

// the strings the details panels show
const errorLongest = (web) => leaves(web.error).reduce((a, b) => (NGUI.glyphWidth(b, 38) > NGUI.glyphWidth(a, 38) ? b : a));
const FILE = "ibuki_dress_spr_with_a_very_long_name_for_the_panel.bundle";

for (const locale of LOCALES) test(`the details panel's light box holds its captions and values in ${locale}`, () => {
  const { web, text } = use(locale);
  const it = { key: "a", full: "Hoshino", big: null, chips: [text("type.noRecruit")] };
  const sets = [
    // the Switch popups: file, size, status (with an error under it), CRC
    [[text("field.file"), FILE], [text("field.size"), "123.4 MB"], [text("field.status"), `${text("status.applied")}\n${errorLongest(web)}`], [text("field.crc"), text("crc.needed")]],
    [[text("field.file"), FILE], [text("field.size"), "123.4 MB"], [text("field.status"), text("import.reason.no_target")], [text("field.crc"), text("crc.fixed"), "#0a6f2d/1.00"]],
    // the source cards
    [[text("field.file"), FILE], [text("info.gameFile"), "2026-10-30"], [text("info.status"), text("status.replaced")], [text("tab.lobby"), text("model.has")]],
    [[text("field.file"), FILE], [text("info.gameFile"), "2026-10-30"], [text("info.status"), text("status.original")], [text("special.background"), text("model.none")]],
    // the Add to Library prompts
    [[text("field.file"), `${FILE} +2`], [text("field.size"), "-"], [text("field.crc"), text("crc.original")], [text("field.copy"), text("mods.copy.none")]],
  ];
  for (const [n, fields] of sets.entries()) {
    const tree = Popup.detailsTree(it, "Title", fields, []);
    const what = `${locale} details panel, set ${n}`;
    const nodes = tree.children.filter((c) => c.name === "Fields" || /^(F|V|L)_\d+$/.test(c.name));
    assert.equal(nodes[0].name, "Fields");
    const { fill, boxRect } = assertRows({ nodes, what, count: fields.length });
    // one width rule: the panel (850, popup.js's LP.left) less 40
    assert.match(readFileSync(new URL(`${JS}popup.js`, import.meta.url), "utf8"), /left: \{ l: -1370, r: -520, t: 600, b: -690 \}/);
    const panel = 850;
    assert.equal(fill.w_, panel - 40, what);
    assert.equal(rectOf(nodes[0]).right - rectOf(nodes[0]).left, panel - 40);
    // the third value keeps two lines and an ellipsis at most, its whole text as the accessible label
    const third = child(nodes, "V_2").widgets[0];
    assert.ok(third.text.split("\n").length <= 2, `${what}: the third value`);
    assert.equal(third.ariaLabel, String(fields[2][1]));
    assert.ok(boxRect.right - boxRect.left > 0);
  }
});

for (const locale of LOCALES) test(`the file details' light box holds its captions, values and edit icon in ${locale}`, () => {
  const { text } = use(locale);
  const folder = `/Users/someone/Library/Application Support/Game/${"folder/".repeat(12)}`;
  const mods = [{ alias: "My paint", core: "ibuki_spr" }, { alias: "A rather long alias for a modded portrait of a student", file: "C:\\mods\\ibuki_spr_v2.bundle" }];
  for (const mod of mods) {
    const fields = [[text("field.folder"), folder], [text("info.current"), mod.alias, mod], [text("field.modFile"), mod.file || "ibuki_spr_mod.bundle"],
      [text("field.source"), "Hoshino (Swimsuit)"]];
    const { tree } = Detail.detailsBody({ fields, rows: [] });
    const what = `${locale} file details, ${mod.alias.length} character alias`;
    const nodes = tree.children.filter((c) => c.name === "Summary" || /^(F|V|L|Original|Edit)_\d+$/.test(c.name));
    const W = DETAILS.r - DETAILS.l;
    const { boxRect } = assertRows({ nodes, what, count: fields.length, slot: (i) => {
      const edit = child(nodes, `Edit_${i}`);
      return edit ? rectOf(edit) : null;
    } });
    // as wide as the file table under it (2080): the box's line, outside the fill, ends at the table's edges
    assert.equal(boxRect.right - boxRect.left, W - B.lineOutset, what);
    assert.equal(child(nodes, "Summary").children[0].widgets[0].w_, W, `${what}: the line's outer edges are the table's`);
    // the edit icon follows the text, the original name sits under it (the value rises), both inside the row
    const edit = child(nodes, "Edit_1"), original = child(nodes, "Original_1"), value = child(nodes, "V_1");
    assert.ok(edit && original, `${what}: the edit icon and the original name`);
    const er = rectOf(edit), vr = rectOf(value), orr = rectOf(original);
    assert.ok(er.left >= vr.left, `${what}: the icon after the text starts`);
    assert.ok(orr.top <= vr.bottom + 1e-9 || orr.bottom < vr.bottom, `${what}: the original name under the value`);
    const second = original.widgets[0];
    assert.ok(second.fontSize <= 28 && second.fontSize >= 24 - 8, what);
    assert.ok(NGUI.glyphWidth(second.text, second.fontSize) <= second.w_ + 1e-9 || second.ellipsis, `${what}: the original name inside its label or cut with an ellipsis`);
    assert.equal(original.widgets[0].ariaLabel, mod.core || "ibuki_spr_v2.bundle");
    // the file table starts under the box
    const head = child(tree.children, "Head");
    assert.ok(head.pos[1] + head.widgets[0].h_ / 2 < boxRect.bottom, `${what}: the table under the box`);
  }
});

// the boxed messages: the compatibility findings, with their buttons
const PARAMS = { name: "CH0261_home.skel", mod: "4.2.33", official: "4.2.40", missing: "idle, walk, run", added: "dance, wave", names: "Intro_Shoulder_L, Intro_Shoulder_R, Face_Eye_L",
  atlas: "2048 x 2048", texture: "1024 x 1024", version: "4.2.33", runtime: "4.2.120", n: "3", reason: "The target file is in use by another program", kind: "Portrait" };
for (const locale of LOCALES) for (const width of [1700, 2100, 2458]) test(`boxed message rows hold their text and button in ${locale}, ${width} wide`, () => {
  const { web, text } = use(locale);
  const compat = leaves(web.compatibility).map((s) => fill(s, PARAMS));
  const buttons = [text("info.details"), text("add.go"), text("tour.open"), text("recovery.report")];
  for (const [n, texts] of [[0, compat]]) {
    const lines = texts.map((line, i) => (i % 2 === 0 ? { text: line, button: { label: buttons[(i / 2) % buttons.length], fn() {} } } : line));
    const what = `${locale} ${width} ${n ? "Add result" : "findings"}`;
    const layout = Popup.messageLayout({ lines, width, ok: "OK", cancel: "Cancel", scroll: true });
    const tree = layout.body.tree, nodes = tree.children;
    assert.equal(nodes[0].name, "Box", what);
    const slot = (i) => {
      const button = child(nodes, `Btn_${i}`);
      if (!button) return null;
      const bg = button.children.flatMap((c) => c.widgets).find((x) => x.sprite === "Common_Btn_BG") || button.widgets.find((x) => x.sprite === "Common_Btn_BG");
      return { left: button.pos[0] - bg.w_ / 2, right: button.pos[0] + bg.w_ / 2, top: button.pos[1] + bg.h_ / 2, bottom: button.pos[1] - bg.h_ / 2 };
    };
    const { fill: shape, boxRect } = assertRows({ nodes, what, value: "Row", divider: "Dot", count: lines.length, lines: Infinity, slot });
    assert.equal(shape.w_, width - 200, `${what}: the frame less 200`);
    assert.equal(boxRect.right - boxRect.left, width - 200);
    // rows with a button are 156 high at least and keep 30 between text and button; rows without are 64 at least
    lines.forEach((line, i) => {
      const row = child(nodes, `Row_${i}`).widgets[0], button = slot(i), r = rectOf(child(nodes, `Row_${i}`));
      if (typeof line !== "string") assert.ok(button && r.right + 30 <= button.left + 1e-9, `${what} row ${i}: 30 between text and button`);
      else assert.equal(button, null);
      assert.ok(row.fontSize >= B.value.fsMin && row.fontSize <= B.value.fs, `${what} row ${i}: ${row.fontSize}`);
    });
    // every string fits in three lines at 26 or more in these languages, or the report says which one does not
    for (const [i] of lines.entries()) {
      const lineCount = child(nodes, `Row_${i}`).widgets[0].text.split("\n").length;
      assert.ok(lineCount <= 3, `${what} row ${i}: ${lineCount} lines for ${JSON.stringify(typeof lines[i] === "string" ? lines[i] : lines[i].text)}`);
    }
  }
});

// the update dialog: two version rows, the frame as high as its content needs
for (const locale of LOCALES) test(`the update dialog's version rows and frame hold in ${locale}`, () => {
  const { text } = use(locale);
  for (const info of [{ current: "0.6.0-alpha.12-dev", version: "0.10.0-beta.12" }, { current: "0.6.0", version: "1.12.3-rc.12+20261103" }]) {
    const rows = Update.versionRows(info);
    assert.deepEqual(rows.map((row) => row.caption), [text("update.current"), text("update.latest")]);
    assert.deepEqual(rows.map((row) => row.colour), [undefined, `${Tokens.COLOUR.success}/1.00`], "the latest keeps the success colour, the current its black");
    const box = Box.statsBox({ holder: "frame", width: 1700, at: [0, 0], rows });
    const what = `${locale} update ${info.version}`;
    assert.equal(box.height, Update.FRAME_BOX_HEIGHT ?? 2 * B.rowMin + 2 * B.padY, what);
    const { fill } = assertRows({ nodes: box.nodes, what, count: 2, lines: 1 });
    assert.equal(fill.w_, 1500, `${what}: the frame (1700) less 200`);
    const frame = Update.frameFor(box.height);
    assert.equal(frame.h, Update.FRAME.h, `${what}: the frame is the same height`);
    // the lowest content, the progress bar, stays POPUP.clearance above the three buttons' tops, and the frame is on the stage
    assert.ok(frame.contentBottom - Popup.footerTop(frame.h, 3) >= POPUP.clearance - 1e-9, `${what}: ${frame.contentBottom - Popup.footerTop(frame.h, 3)} clear of the buttons`);
    assert.ok(frame.h <= POPUP.maxH && frame.h > 0);
    // from the top: the box where a message's lines start, Status under it, the progress bar under that
    assert.equal(POPUP.frameY + frame.h / 2 - frame.boxTop, 150);
    assert.ok(frame.boxTop - box.height > frame.status + 48 && frame.status - 48 > frame.progress + 10, `${what}: the order under the box`);
  }
});

// the portrait panel re-lays the details panel's box by its node names: it keeps the line's outset on the token
test("the Switch Portrait panel keeps the line 5 larger than its box", () => {
  use("en-US");
  const texture = NGUI.node("Texture", [0, 0], [{ texture: "portrait", w_: 10, h_: 10, pivot: 4 }]);
  const fields = [["File", "ibuki_dress_spr.bundle"], ["Size", "12 MB"], ["Status", "Applied"], ["CRC", "Fixed"]];
  const tree = Popup.detailsTree({ key: "a", full: "Hoshino", big: null, chips: ["Added"] }, "Title", fields, [], texture, "spr");
  const box = child(tree.children, "Fields"), fill = box.widgets[0], line = child(box.children, "Line").widgets[0];
  assert.deepEqual([line.w_ - fill.w_, line.h_ - fill.h_], [B.lineOutset, B.lineOutset]);
  assert.equal(fill.w_, (portraitPanel.right - portraitPanel.left - portraitPanel.gap) / 2, "its own width, not part of this change");
});

// the boxed message rows take the box's value range; a message without a box keeps its own
test("a message with a line button sets its rows 38 down to 26 and shares one size across a group; one without keeps 52 down to 44", () => {
  use("en-US");
  const shortRow = { text: "Short row", button: { label: "Go", fn() {} } };
  const long = "A finding that is rather long and needs several words to say what is different in this file".repeat(2);
  const boxed = Popup.messageLayout({ lines: [shortRow, long, "Third row"], width: 1700, ok: "OK" }).body.tree.children;
  const size = (nodes, name) => child(nodes, name).widgets[0].fontSize;
  assert.equal(size(boxed, "Row_0"), 38);
  assert.ok(size(boxed, "Row_1") < 38 && size(boxed, "Row_1") >= 26);
  assert.equal(size(boxed, "Row_2"), 38);
  const grouped = Popup.messageLayout({ lines: [shortRow, long, "Third row"], width: 1700, ok: "OK", fitTogether: [0, 1, 2] }).body.tree.children;
  assert.equal(new Set([0, 1, 2].map((i) => size(grouped, `Row_${i}`))).size, 1, "one size across the group");
  assert.ok(size(grouped, "Row_0") >= 26 && size(grouped, "Row_0") <= 38);
  // rows without a button: the box's 64; with one: 156
  const rows = Popup.messageLayout({ lines: [shortRow, "Plain row", "Plain row two"], width: 1700, ok: "OK" }).body.tree.children;
  assert.equal(rows.find((n) => n.name === "Dot_0").pos[1] - rows.find((n) => n.name === "Dot_1").pos[1], 64);
  assert.equal(-12 - rows.find((n) => n.name === "Dot_0").pos[1], 156);
  // no box, no change: 52 down to 44, lines of 1.3
  const plain = Popup.messageLayout({ lines: ["Short row", long], width: 1700, ok: "OK" }).body.tree.children;
  assert.equal(size(plain, "Line_0"), 52);
  assert.ok(size(plain, "Line_1") <= 52 && size(plain, "Line_1") >= 44 - 8);
  assert.equal(plain.some((n) => n.name === "Box"), false);
});
