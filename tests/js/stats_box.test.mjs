// The light stats box component (web/js/stats-box.js): pure layout from a width and rows to nodes and a height. The
// sites that draw through it are checked in popup.test.mjs (the details panel, the file details, the messages, the update
// dialog); the tokens against the prefab in tests/test_ui_tokens.py.
import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import { test } from "node:test";
import { STATS_BOX as B } from "../../src/bmpt/web/js/ui-tokens.js";

// the measuring stand-in shared with the other fit tests: a CJK or Thai code point one em, anything else half
const WIDE = /[\u2e80-\u9fff\u3000-\u30ff\uff00-\uffef\uac00-\ud7af\u0e00-\u0e7f]/;
const ctx = {
  font: "",
  measureText(s) {
    const fs = parseFloat(/([\d.]+)px/.exec(this.font)[1]);
    return { width: [...s].reduce((w, c) => w + (WIDE.test(c) ? fs : fs / 2), 0) };
  },
};
globalThis.document = {
  createElement: () => ({ getContext: () => ctx, remove() {} }),
  documentElement: { lang: "en-US", appendChild() {} },
};
globalThis.getComputedStyle = () => ({ getPropertyValue: () => "" });
const NGUI = await import("../../src/bmpt/web/js/ngui.js");
const Box = await import("../../src/bmpt/web/js/stats-box.js");

const WEB = new URL("../../src/bmpt/web/", import.meta.url);
const read = (path) => readFileSync(new URL(path, WEB), "utf8");
const named = (nodes, name) => nodes.find((n) => n.name === name);
const widgetOf = (n) => n.widgets[0];

test("the box is the holder's width less 200 in a popup frame and 40 in a panel", () => {
  assert.deepEqual([B.inset.frame, B.inset.panel, B.inset.table], [200, 40, B.lineOutset]);
  for (const width of [850, 1700, 2080, 2458]) {
    assert.equal(Box.boxWidth("frame", width), width - 200);
    assert.equal(Box.boxWidth("panel", width), width - 40);
    for (const holder of ["frame", "panel"]) {
      const { nodes, width: boxW } = Box.statsBox({ holder, width, rows: [{ caption: "File", value: "a.bundle" }] });
      assert.equal(boxW, Box.boxWidth(holder, width));
      assert.equal(widgetOf(nodes[0]).w_, boxW);
    }
  }
});

test("the fill, its line 5 larger across and down, and the colours are the tokens'", () => {
  const { nodes } = Box.statsBox({ holder: "panel", width: 850, name: "Fields", rows: [{ caption: "File", value: "a.bundle" }, { caption: "Size", value: "12 MB" }] });
  const [fill, line] = [nodes[0], nodes[0].children[0]].map(widgetOf);
  assert.equal(nodes[0].name, "Fields");
  assert.deepEqual([fill.sprite, fill.color, fill.type, fill.depth], ["Common_Bg_Raius10px", "#ecf5f9/1.00", 1, 5]);
  assert.deepEqual([line.sprite, line.color, line.depth], ["Common_Line_Raius10px", "#c0c9d4/1.00", 4]);
  assert.deepEqual([line.w_ - fill.w_, line.h_ - fill.h_], [5, 5]);
  assert.deepEqual(nodes[0].children[0].pos, [0, 0], "the line is centred on the fill");
  const divider = widgetOf(named(nodes, "L_0"));
  assert.deepEqual([divider.sprite, divider.color, divider.type, divider.h_], ["Common_Line_Dot", "#c0c9d4/1.00", 2, 4]);
});

test("rows are 64 high at least, the box 12 above and below them, dividers between rows and none after the last", () => {
  for (const count of [1, 2, 3, 5]) {
    const rows = Array.from({ length: count }, (_, i) => ({ caption: `Caption ${i}`, value: `Value ${i}` }));
    const box = Box.statsBox({ holder: "frame", width: 1700, at: [0, 0], rows });
    assert.equal(box.height, count * 64 + 24);
    assert.equal(widgetOf(box.nodes[0]).h_, box.height);
    assert.equal(box.nodes[0].pos[1], -box.height / 2, "the box hangs from `at`");
    const dividers = box.nodes.filter((n) => /^L_\d+$/.test(n.name));
    assert.equal(dividers.length, count - 1);
    dividers.forEach((d, i) => {
      assert.equal(d.pos[1], -12 - 64 * (i + 1), `between row ${i} and ${i + 1}`);
      assert.equal(widgetOf(d).w_, Box.boxWidth("frame", 1700) - 60, "30 in from each edge");
    });
    box.rows.forEach((row, i) => {
      assert.equal(row.h, 64);
      assert.equal(row.top, -12 - 64 * i);
      assert.equal(named(box.nodes, `V_${i}`).pos[1], row.y);
      assert.equal(named(box.nodes, `F_${i}`).pos[1], row.y);
    });
  }
});

test("captions are 38, the value starts after the widest caption and 30, 30 in from the box's edge", () => {
  const rows = [{ caption: "Folder", value: "x" }, { caption: "Official Copy", value: "y" }];
  const box = Box.statsBox({ holder: "panel", width: 850, at: [100, 0], rows });
  const left = 100 - (850 - 40) / 2;
  assert.equal(B.caption, 38);
  const capW = NGUI.textWidth("Official Copy", 38) + 30;
  assert.equal(box.capW, capW);
  for (const [i, row] of rows.entries()) {
    const caption = named(box.nodes, `F_${i}`), value = named(box.nodes, `V_${i}`);
    assert.equal(widgetOf(caption).fontSize, 38);
    assert.deepEqual(caption.pos[0], left + 30);
    assert.equal(value.pos[0], left + 30 + capW);
    assert.equal(widgetOf(value).w_, 850 - 40 - 60 - capW, "the label reaches 30 short of the box's right edge");
    assert.equal(widgetOf(value).text, row.value);
  }
});

test("a value is 38 down to 26 on up to three lines, wrapped and never cut; a longer one keeps 26 and more lines", () => {
  const room = Box.valueRoom("panel", 850, { capW: 200 });
  assert.equal(room, 850 - 40 - 60 - 200 - 20);
  const rows = [{ caption: "File", value: "ibuki.bundle" }, { caption: "File", value: "ibuki_dress_spr_with_a_rather_long_name_for_this_panel.bundle" },
    { caption: "File", value: "folder/".repeat(60) }];
  const { nodes, rows: placed } = Box.statsBox({ holder: "panel", width: 850, rows });
  const sizes = [0, 1, 2].map((i) => widgetOf(named(nodes, `V_${i}`)).fontSize);
  assert.equal(sizes[0], 38);
  assert.ok(sizes[1] <= 38 && sizes[1] >= 26);
  assert.equal(sizes[2], 26, "the longest path keeps the floor");
  placed.forEach((row, i) => {
    const w = widgetOf(named(nodes, `V_${i}`));
    for (const line of w.text.split("\n")) assert.ok(NGUI.glyphWidth(line, w.fontSize) <= row.room, `row ${i}`);
    assert.equal(w.text.replaceAll("\n", ""), rows[i].value, "every character kept");
    assert.ok(row.h >= w.text.split("\n").length * w.fontSize * 1.25 + 16 - 1e-9, "the row holds its lines");
  });
  assert.ok(placed[2].text.lines > 3, "past three lines at 26 the lines grow instead of the text being cut");
  assert.ok(widgetOf(named(nodes, "V_1")).text.split("\n").length <= 3 || sizes[1] === 26);
});

test("a row can cut its value after some lines with an ellipsis, keeping the whole as its accessible label", () => {
  const value = "A very long status text that needs three lines in this narrow panel, ".repeat(2).trim();
  const [cut, whole] = [2, 0].map((keep) => Box.statsBox({ holder: "panel", width: 850, rows: [{ caption: "Status", value, ariaLabel: value, cut: keep }] }));
  const kept = widgetOf(named(cut.nodes, "V_0"));
  assert.ok(widgetOf(named(whole.nodes, "V_0")).text.split("\n").length > 2);
  assert.equal(kept.text.split("\n").length, 2);
  assert.ok(kept.text.endsWith("\u2026"));
  assert.equal(kept.ariaLabel, value);
  assert.equal(cut.rows[0].text.lines, 2);
});

test("a row's own colour, minimum height, reserve and slot; a value can arrive already fitted", () => {
  const seen = [];
  const box = Box.statsBox({ holder: "frame", width: 1700, names: { value: "Row", divider: "Dot" }, rows: [
    { value: { text: "Fitted\ntwo lines", fs: 30, lines: 2 }, colour: "#123456/1.00", minH: 156, reserve: 300, slot: (g) => { seen.push(g); return [NGUI.node("Btn_0", [g.right - 120, g.y])]; } },
    { value: "Plain", colour: undefined },
  ] });
  const first = widgetOf(named(box.nodes, "Row_0"));
  assert.deepEqual([first.color, first.text, first.fontSize, first.w_], ["#123456/1.00", "Fitted\ntwo lines", 30, 1500 - 60 - 300]);
  assert.equal(widgetOf(named(box.nodes, "Row_1")).color, "#000000/1.00");
  assert.deepEqual(box.rows.map((row) => row.h), [156, 64]);
  assert.ok(named(box.nodes, "Dot_0") && !named(box.nodes, "L_0"), "the divider takes the caller's name");
  assert.equal(seen.length, 1);
  assert.deepEqual([seen[0].i, seen[0].y, seen[0].right, seen[0].room, seen[0].h], [0, -12 - 78, 1500 / 2 - 30, 1500 - 80 - 300, 156]);
  assert.equal(named(box.nodes, "Btn_0").pos[0], 750 - 30 - 120);
  assert.equal(box.nodes.indexOf(named(box.nodes, "Btn_0")) + 1, box.nodes.indexOf(named(box.nodes, "Dot_0")), "slot nodes stand before the divider");
});

test("a secondary line stands under the value, smaller and fainter, and adds its room to the row", () => {
  const plain = Box.statsBox({ holder: "panel", width: 2080, rows: [{ caption: "Current", value: "My paint" }] });
  const both = Box.statsBox({ holder: "panel", width: 2080, rows: [{ caption: "Current", value: "My paint", secondary: "ibuki_spr.bundle", reserve: 70 }] });
  const second = widgetOf(named(both.nodes, "Original_0")), value = widgetOf(named(both.nodes, "V_0"));
  assert.equal(both.height, Math.max(64, 38 * 1.25 + 16 + 42) + 24);
  assert.ok(both.height > plain.height);
  assert.deepEqual([second.fontSize <= 28 && second.fontSize >= 24, second.color, second.ellipsis, second.ariaLabel], [true, "#2d4664/0.65", true, "ibuki_spr.bundle"]);
  assert.ok(value.fontSize > second.fontSize);
  const valueNode = named(both.nodes, "V_0"), secondNode = named(both.nodes, "Original_0");
  assert.ok(valueNode.pos[1] - value.h_ / 2 > secondNode.pos[1] + second.h_ / 2, "the value clears the secondary line");
  assert.equal(second.w_, value.w_ - 20, "both keep 70 for an icon");
});

test("the room a value gets is the box less 30 each side, the captions, what the row reserves and 20", () => {
  assert.equal(Box.valueRoom("frame", 1700), 1500 - 60 - 20);
  assert.equal(Box.valueRoom("frame", 1700, { reserve: 270 }), 1500 - 60 - 20 - 270);
  assert.equal(Box.valueRoom("panel", 2080, { capW: 140, reserve: 70 }), 2040 - 60 - 20 - 140 - 70);
  assert.equal(Box.captionWidth([]), 0);
  assert.equal(Box.captionWidth([null, ""]), 0);
  assert.equal(Box.captionWidth(["A", "Official Copy"]), NGUI.textWidth("Official Copy", 38) + 30);
});

test("the module touches no page: it uses the text measure, the path wrap and the tokens, nothing else", () => {
  const source = read("js/stats-box.js");
  assert.doesNotMatch(source, /document\.|window\.|innerWidth|getElementById/);
  assert.deepEqual([...source.matchAll(/^import .* from "(.+)";$/gm)].map((m) => m[1]), ["./ngui.js", "./ui-tokens.js", "./util.js"]);
});

// --- no site paints the light box by hand: the fill's colour is written in the tokens and nowhere else but the tour image slot ---
const sources = (dir, ext) => readdirSync(new URL(dir, WEB), { withFileTypes: true }).flatMap((entry) =>
  entry.isDirectory() ? sources(`${dir}${entry.name}/`, ext) : entry.name.endsWith(ext) ? [`${dir}${entry.name}`] : []);

test("the stats box's fill and line colours are written in ui-tokens.js only (and the tour's image slot reuses the fill)", () => {
  const files = sources("js/", ".js");
  assert.ok(files.length > 100 && files.includes("js/popup.js") && files.includes("js/update.js"));
  const holders = (literal) => files.filter((file) => read(file).toLowerCase().includes(literal)).sort();
  assert.deepEqual(holders("#ecf5f9"), ["js/tours.js", "js/ui-tokens.js"]);
  // the line's colour is also the progress bar's, the chips' outline and the dotted dividers of the file table
  for (const file of holders("#c0c9d4")) assert.ok(["js/detail.js", "js/popup.js", "js/ui-tokens.js", "js/update.js"].includes(file), file);
});

test("the four sites build their box with statsBox()", () => {
  for (const [file, calls] of [["js/popup.js", 2], ["js/detail.js", 1], ["js/update.js", 2]]) {
    const source = read(file);
    assert.match(source, /import \{[^}]*\bstatsBox\b[^}]*\} from "\.\/stats-box\.js";/, file);
    assert.equal(source.match(/\bstatsBox\(/g).length, calls, `${file}: its calls to statsBox()`);
  }
  assert.match(read("js/popup.js"), /name: "Fields"/);
  assert.match(read("js/popup.js"), /name: "Box", names: \{ value: "Row", divider: "Dot" \}/);
  assert.match(read("js/detail.js"), /name: "Summary"/);
});
