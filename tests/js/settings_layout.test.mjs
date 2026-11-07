// Decision 1 of the design system's screens job: Settings (UIPopup_Option_PC) lays its cards out in the card's container
// (settings-rows.js): the content box is the 1552-wide card less its padding, and the title, the options, the values and the
// buttons take their boxes from it with row and stack. The prefab's own values stay (the card, the title 17 from the top, the dotted
// line 109 down, radio groups 508 apart). Checked on the real rows of every tab in en-US and zh-CN, in prefab units: the default
// 1920 x 1080 window draws them at S = 1920 / 2960 whatever the language.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { controls } from "./control_prefabs.mjs";

const web = new URL("../../src/bmpt/web/js/", import.meta.url);
const source = readFileSync(new URL("settings.js", web), "utf8");
const strip = (s) => s.replace(/^import .*;\n/gm, "").replace(/^export /gm, "");
class El {
  style = {}; children = []; lang = "";
  appendChild(el) { this.children.push(el); }
  remove() {}
  getContext() { return { font: "", measureText(s) {
    const fs = Number(this.font.match(/([\d.]+)px/)?.[1] || 40);
    return { width: [...s].reduce((w, c) => w + (/\s/.test(c) ? .3 : c.charCodeAt(0) < 128 ? .58 : 1) * fs, 0) };
  } }; }
}
globalThis.document = { documentElement: new El(), createElement: () => new El() };
const NGUI = await import(new URL("ngui.js", web));
const { wrapPath, S, glyphsOf } = await import(new URL("util.js", web));
const Rows = await import(new URL("settings-rows.js", web));
const ButtonsModule = await import(new URL("game-buttons.js", web));
const { errorText } = await import(new URL("i18n.js", web));
const { BACKDROP, SMALL_BUTTON, TYPE, SPACE, PANEL, CONTROL } = await import(new URL("ui-tokens.js", web));
const { option, filter } = controls();
const Buttons = { smallButton: (...args) => ButtonsModule.optionButton(option, filter, ...args) };

const LOCALES = ["en-US", "zh-CN"];
// the card's content box in NGUI x (centre origin): the card 1552 wide less 58 each side
const LEFT = -1552 / 2 + PANEL.padCard, RIGHT = 1552 / 2 - PANEL.padCard, TOP = 109 + SPACE.m;
const near = (a, b, message = "", eps = 1e-6) => assert.ok(Math.abs(a - b) < eps, `${message} ${a} != ${b}`);
const bodyOf = (button) => button.widgets.find((w) => w.sprite === "Common_Btn_BG") ?? NGUI.find(button, "BG (1)")?.widgets[0] ?? NGUI.find(button, "Body").widgets[0];

function harness(locale, tab) {
  const strings = JSON.parse(readFileSync(new URL(`../../src/bmpt/locales/${locale}.json`, import.meta.url), "utf8")).webui;
  const T = (key, values = {}) => key.split(".").reduce((v, k) => v[k], strings).replace(/\{(\w+)\}/g, (_, k) => values[k]);
  globalThis.document.documentElement.lang = locale;
  const prefs = { language: locale, languages: [{ code: locale, name: strings.languageName }, { code: "en-US", name: "English" }, { code: "th-TH", name: "ภาษาไทย" }],
    native: true, traySupported: true, version: "0.6.0-alpha.10", consent: true, previewCache: true, startupUpdate: true, prereleasePreviews: true,
    cacheDir: "/Users/someone/Library/Application Support/bluearchive-modding-patching-tool/cache", logPath: "/Users/someone/Library/Application Support/bluearchive-modding-patching-tool/logs/session.log",
    fileSource: "windows_global", gameDirs: { global: "/Applications/Blue Archive/Game/Contents/Global", japan: "" }, libraryDir: "/Users/someone/library", outputDir: "/Users/someone/output",
    lobbyZoom: { mode: "custom", custom: 2 }, voiceVolume: 80 };
  const ModSwitch = { current: () => ({ scan: true }), view: () => ({ on: true, busy: false, unavailable: false, dot: false }), listSep: () => ", ",
    sizeText: (n) => `${n} B`, reasonText: () => "", openHelp() {}, click() {} };
  const deps = { NGUI, T, wrapPath, S, glyphsOf, ...Rows, Buttons, BACKDROP, SMALL_BUTTON, TYPE, platform: "mac", errorText: (err) => errorText(err, T),
    ModSwitch, DOT: () => ({ name: "Dot", pos: [0, 0], widgets: [], children: [] }), rpc: async () => prefs };
  const api = new Function("deps", "initial", `const {${Object.keys(deps)}} = deps; ${strip(source)};
    prefs = initial; tab = ${JSON.stringify(tab)}; return { rows };`)(deps, prefs);
  return { rows: api.rows({ builtSize: 1048576 }), T };
}

const line = (row) => row.tree.name;
const buttonsOf = (tree) => tree.children.filter((c) => /^Btn_\d+$/.test(c.name));

for (const locale of LOCALES) for (const tab of ["general", "mods", "folders"]) test(`${locale} ${tab}: cards stack in the panel's padded area, 12 apart, each as wide as the card`, () => {
  const { rows, height } = harness(locale, tab).rows;
  assert.ok(rows.length >= 2);
  let bottom = null;
  for (const row of rows) {
    const top = -row.tree.pos[1] - row.h / 2;
    near(top - (bottom ?? 0), bottom == null ? PANEL.padList : PANEL.stackGap, `${line(row)}: ${bottom == null ? "20 under the panel's top" : "12 under the card above"}`);
    assert.equal(row.tree.pos[0], 0, `${line(row)}: centred in the panel`);
    assert.equal(row.tree.widgets.find((w) => "sprite" in w)?.w_ ?? 1552, 1552, `${line(row)}: the card's 1552`);
    bottom = top + row.h;
  }
  near(height, bottom + PANEL.padList, "the panel scrolls to the last card's foot and the padding");
});

for (const locale of LOCALES) for (const tab of ["general", "mods", "folders"]) test(`${locale} ${tab}: titles stand on the content box's left, 17 under the card's top, at its width`, () => {
  for (const row of harness(locale, tab).rows.rows) {
    const title = NGUI.find(row.tree, "Title"), widget = title.widgets[0];
    near(title.pos[0], LEFT, `${line(row)}: x`);
    near(title.pos[1], row.h / 2 - PANEL.cardTitleTop - widget.h_ / 2, `${line(row)}: 17 from the top`);
    assert.equal(widget.w_, RIGHT - LEFT);
    assert.ok(NGUI.glyphWidth(widget.text, widget.fontSize) <= RIGHT - LEFT - SPACE.xl, `${line(row)}: the title fits the content box less its slack`);
    assert.ok(widget.fontSize >= TYPE.heading.min && widget.fontSize <= 50);
    const dots = NGUI.find(row.tree, "DotLine");
    near(dots.pos[1], row.h / 2 - 109, `${line(row)}: the dotted line 109 down`);
  }
});

for (const locale of LOCALES) for (const tab of ["general", "mods", "folders"]) test(`${locale} ${tab}: value rows put their buttons on the content box's right edge, SPACE.m apart, one height and one size`, () => {
  for (const row of harness(locale, tab).rows.rows) {
    const buttons = buttonsOf(row.tree);
    if (!buttons.length || row.tree.name === "Export") continue;   // the Export pair is centred (below)
    const boxes = buttons.map((b) => { const body = bodyOf(b), w = body.w_ ?? SMALL_BUTTON.w; return { x: b.pos[0] - w / 2, y: b.pos[1], w, h: body.h_ }; });
    // every button of a row has the control height and a row's captions share one size
    for (const box of boxes) near(box.h, CONTROL.buttonS.h, `${line(row)}: control height`);
    const sizes = new Set(buttons.map((b) => NGUI.find(b, "Label_Locale").widgets[0].fontSize));
    if (buttons.length > 1) assert.equal(sizes.size, 1, `${line(row)}: one caption size`);
    // lines of buttons: the right end of each line is the content box's right edge, neighbours SPACE.m apart
    const lines = new Map();
    for (const box of boxes) lines.set(Math.round(box.y), [...lines.get(Math.round(box.y)) ?? [], box]);
    for (const [y, line_] of lines) {
      line_.sort((a, b) => a.x - b.x);
      near(line_.at(-1).x + line_.at(-1).w, RIGHT, `${line(row)}: line at ${y} ends on the content box`);
      for (let i = 1; i < line_.length; i++) near(line_[i].x - (line_[i - 1].x + line_[i - 1].w), SPACE.m, `${line(row)}: SPACE.m apart`);
    }
    // inside the content box vertically: under the dotted line by SPACE.m, SPACE.l above the foot
    for (const box of boxes) {
      assert.ok(box.y + box.h / 2 <= row.h / 2 - TOP + 1e-9, `${line(row)}: under the dotted line`);
      assert.ok(box.y - box.h / 2 >= -row.h / 2 + SPACE.l - 1e-9, `${line(row)}: above the foot`);
    }
    // the value starts on the content box's left, clear of the buttons on its line, in the caption role
    const value = NGUI.find(row.tree, "Value");
    if (value) {
      const widget = value.widgets[0];
      near(value.pos[0], LEFT, `${line(row)}: the value's left`);
      assert.ok(widget.fontSize <= TYPE.caption.fs && widget.fontSize >= TYPE.caption.min, `${line(row)}: ${widget.fontSize} is a caption`);
      const beside = boxes.filter((box) => Math.abs(box.y - value.pos[1]) < box.h / 2);
      for (const box of beside) assert.ok(value.pos[0] + widget.w_ + SPACE.m <= box.x + 1e-9, `${line(row)}: the value keeps SPACE.m clear of its buttons`);
      for (const text of widget.text.split("\n")) assert.ok(NGUI.glyphWidth(text, widget.fontSize) <= widget.w_ + 1e-9, `${line(row)}: the value fits its room`);
    }
  }
});

for (const locale of LOCALES) test(`${locale} general: radio groups keep the prefab's 508 pitch inside the content box, one size, one control height`, () => {
  const { rows } = harness(locale, "general").rows;
  const radios = rows.filter((row) => row.tree.children.some((c) => /^Opt_\d+$/.test(c.name)));
  assert.ok(radios.length >= 3);
  for (const row of radios) {
    const options = row.tree.children.filter((c) => /^Opt_\d+$/.test(c.name));
    const pitch = Math.min(508, (RIGHT - LEFT) / options.length);
    options.forEach((opt, i) => {
      near(opt.pos[0] - CONTROL.icon.h / 2, LEFT + i * pitch, `${line(row)} option ${i}: the radio's left edge`);
      near(opt.pos[1], row.h / 2 - 174.5, `${line(row)}: the options' band`);
      const bg = NGUI.find(opt, "BG").widgets[0];
      assert.equal(bg.h_, CONTROL.icon.h);
      const label = NGUI.find(opt, "Label"), widget = label.widgets[0];
      const right = opt.pos[0] + label.pos[0] + widget.w_;
      assert.ok(right <= RIGHT + 1e-9, `${line(row)} option ${i}: the label stays in the content box`);
      assert.ok(right <= opt.pos[0] - CONTROL.icon.h / 2 + (i + 1) * pitch - i * pitch - SPACE.s + 1e-9, `${line(row)}: SPACE.s clear of the next option`);
      for (const text of widget.text.split("\n")) assert.ok(NGUI.glyphWidth(text, widget.fontSize) <= widget.w_ - SPACE.m + 1e-9, `${line(row)}: fits`);
    });
    assert.equal(new Set(options.map((o) => NGUI.find(o, "Label").widgets[0].fontSize)).size, 1, `${line(row)}: one size`);
  }
});

test("the Export pair is two SMALL_BUTTON.wideW buttons SPACE.xxl apart, centred in the content box, one size", () => {
  for (const locale of LOCALES) {
    const row = harness(locale, "mods").rows.rows.find((r) => r.tree.name === "Export"), buttons = buttonsOf(row.tree);
    assert.equal(buttons.length, 2);
    const [a, b] = buttons.map((button) => button.pos[0]);
    near(b - a - SMALL_BUTTON.wideW, SPACE.xxl);
    near((a + b) / 2, (LEFT + RIGHT) / 2, "centred");
    assert.equal(buttons[0].pos[1], buttons[1].pos[1]);
    near(buttons[0].pos[1] + CONTROL.buttonS.h / 2, row.h / 2 - TOP, "the content's top");
    near(row.h, 109 + SPACE.m + CONTROL.buttonS.h + SPACE.l);
    assert.equal(new Set(buttons.map((x) => NGUI.find(x, "Label_Locale").widgets[0].fontSize)).size, 1);
  }
});

test("the Enable Mods ? stands SPACE.xl after the title text, on the title's line", () => {
  for (const locale of LOCALES) {
    const row = harness(locale, "mods").rows.rows.find((r) => r.tree.name === "Switch"), title = NGUI.find(row.tree, "Title"), help = NGUI.find(row.tree, "Help");
    const widget = title.widgets[0], textRight = LEFT + glyphsOf(widget.text, widget.fontSize);
    near(help.pos[0] - 25 - textRight, SPACE.xl);
    near(help.pos[1], title.pos[1]);
  }
});

test("Settings keeps no hand-placed value geometry of its own", () => {
  assert.doesNotMatch(source, /VALUE_ROOM|VALUE_ROOM_MIN|BUTTONS_X|BELOW_ROOM|BUTTON_STEP|SETTINGS\.rowGap/);
  const rowsSource = readFileSync(new URL("settings-rows.js", web), "utf8");
  assert.doesNotMatch(rowsSource, /\b1400\b|\b1440\b|-718|-689|\b700\b/);
});
