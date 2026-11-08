// The design system's layout primitives (web/js/ui-grid.js) and how its scales hang together (web/js/ui-tokens.js).
import assert from "node:assert/strict";
import { test } from "node:test";
import { box, inset, container, grid, row, stack, textItem, controlWidth, centreOf, toNode } from "../../src/bmpt/web/js/ui-grid.js";
import { SPACE, TYPE, CONTROL, GRID, POPUP_FRAME, PANEL, LIST_ROW, SMALL_BUTTON, CHIP, SETTINGS } from "../../src/bmpt/web/js/ui-tokens.js";

const near = (a, b, eps = 1e-9) => Math.abs(a - b) <= eps;

test("padding takes one, two or four sides off a box, as in CSS", () => {
  assert.deepEqual(inset(box(0, 0, 100, 50), 10), box(10, 10, 80, 30));
  assert.deepEqual(inset(box(0, 0, 100, 50), [5, 20]), box(20, 5, 60, 40));
  assert.deepEqual(inset(box(0, 0, 100, 50), [1, 2, 3, 4]), box(4, 1, 94, 46));
  assert.deepEqual(container({ w: 1300, h: 1050, padding: [POPUP_FRAME.contentTop, SPACE.xl, 0, SPACE.xl] }).inner,
    box(40, 170, 1220, 880));
});

test("a page's grid: 12 columns of 200, 40 apart, inside the stage less its 60-unit margins", () => {
  const page = container({ w: GRID.stageW, h: GRID.stageH, padding: [0, GRID.pageMargin] }).inner;
  const g = grid(page);
  assert.equal(page.w, 2840);
  assert.equal(g.column, 200); assert.equal(g.pitch, 240);
  assert.deepEqual(g.span(1), box(60, 0, 200, 1665));
  assert.deepEqual(g.span(6), box(60, 0, 1400, 1665));
  assert.deepEqual(g.span(6, 6), box(1500, 0, 1400, 1665));
  assert.equal(g.span(12).w, 2840);
  assert.equal(g.at(3), 60 + 3 * 240);
  // any width: the spans of a row add up to it with the gutters between them
  const narrow = grid(box(0, 0, 1220, 10));
  assert.ok(near(narrow.span(4).w * 3 + 2 * GRID.gutter, 1220));
});

test("a row centres its items on one line: boxes by their middle, text by its script's reference glyph", () => {
  const badge = { w: 400, h: CONTROL.badge.h }, button = { w: 180, h: CONTROL.badge.h };
  // a text whose box is 50 high and whose reference glyph's centre is 28 down
  const label = { w: 300, h: 50, mid: 28 };
  const r = row([label, badge, button], { x: 10, y: 100, gap: SPACE.l });
  const [a, b, c] = r.boxes;
  assert.equal(b.y, c.y, "same-height controls share their top and bottom");
  assert.ok(near(a.y + 28, b.y + b.h / 2), "the text's reference centre on the controls' centre");
  assert.ok(near(r.centre, b.y + b.h / 2));
  assert.equal(b.x - (a.x + a.w), SPACE.l); assert.equal(c.x - (b.x + b.w), SPACE.l);
  // the row is as tall as the room its items need round the shared line
  assert.ok(near(r.h, Math.max(28, 27) + Math.max(22, 27)));
  assert.equal(r.boxes[0].x, 10);
});

test("a row given a height centres its line in it; start and end align edges", () => {
  const items = [{ w: 10, h: 20 }, { w: 10, h: 60 }];
  const centred = row(items, { h: 100 });
  assert.deepEqual(centred.boxes.map((b) => b.y + b.h / 2), [50, 50]);
  assert.deepEqual(row(items, { align: "start" }).boxes.map((b) => b.y), [0, 0]);
  assert.deepEqual(row(items, { align: "end", h: 100 }).boxes.map((b) => b.y + b.h), [100, 100]);
});

test("a row justifies what it does not fill, and growing items share the width left", () => {
  const items = [{ w: 100, h: 10 }, { w: 200, h: 10 }];
  assert.deepEqual(row(items, { w: 1000, justify: "end", gap: 20 }).boxes.map((b) => b.x), [680, 800]);
  assert.deepEqual(row(items, { w: 1000, justify: "center", gap: 20 }).boxes.map((b) => b.x), [340, 460]);
  assert.deepEqual(row(items, { w: 1000, justify: "between" }).boxes.map((b) => b.x), [0, 800]);
  const spaced = row([{ w: 100, h: 10 }, { grow: 1 }, { w: 200, h: 10 }], { w: 1000, gap: 20 });
  assert.deepEqual(spaced.boxes.map((b) => [b.x, b.w]), [[0, 100], [120, 660], [800, 200]]);
  assert.equal(spaced.boxes[1].y, 0); assert.equal(spaced.boxes[1].h, spaced.h);
  const two = row([{ grow: 1, h: 10 }, { grow: 3, h: 10 }], { w: 440, gap: 40 });
  assert.deepEqual(two.boxes.map((b) => b.w), [100, 300]);
});

test("a stack sets items gap apart and aligns them across its width", () => {
  const items = [{ w: 100, h: 50 }, { w: 300, h: 20 }];
  const s = stack(items, { x: 5, y: 10, gap: PANEL.stackGap });
  assert.deepEqual(s.boxes.map((b) => [b.x, b.y]), [[5, 10], [5, 72]]);
  assert.equal(s.h, 82); assert.equal(s.w, 300);
  assert.deepEqual(stack(items, { align: "center" }).boxes.map((b) => b.x), [100, 0]);
  assert.deepEqual(stack(items, { align: "end" }).boxes.map((b) => b.x), [200, 0]);
  assert.deepEqual(stack(items, { w: 500, align: "stretch" }).boxes.map((b) => b.w), [500, 500]);
});

test("a text item fits its role, measures its lines and places its ink and reference centre from its top", () => {
  const ui = {
    LINE_PITCH: 1.15,
    fitText: (text, fs, min, maxW, lines) => ({ text: text.length * fs * 0.5 > maxW && lines > 1 ? `${text.slice(0, 4)}\n${text.slice(4)}` : text, fs }),
    glyphWidth: (text, fs) => text.length * fs * 0.5,
    inkBand: (text, px) => ({ top: -0.4 * px, bottom: 0.35 * px }),
    inkShift: (text, px) => 0.05 * px,
  };
  const one = textItem(ui, "Ready", TYPE.badge);
  assert.equal(one.fs, 34); assert.ok(near(one.h, 34 * 1.15)); assert.equal(one.w, 5 * 17);
  assert.ok(near(one.mid, one.h / 2 - 0.05 * 34));
  assert.ok(near(one.ink[0], one.h / 2 - 0.4 * 34) && near(one.ink[1], one.h / 2 + 0.35 * 34));
  assert.equal(centreOf(one), one.mid);
  const two = textItem(ui, "Missing Files", TYPE.dense, { maxW: 100, lines: 2 });
  assert.equal(two.lines, 2); assert.ok(near(two.h, 2 * 42 * 1.15)); assert.ok(two.w <= 100);
});

test("a caption-sized control keeps its padding each side and its minimum width", () => {
  const measure = (text, fs) => text.length * fs * 0.5;
  assert.equal(controlWidth(CONTROL.buttonS, "OK", measure), CONTROL.buttonS.minW);
  assert.equal(controlWidth(CONTROL.buttonS, "Choose Files and More", measure), 21 * 24 + 2 * 45);
  assert.equal(controlWidth(CONTROL.badge, "Ready", measure), CONTROL.badge.minW);
});

test("a box becomes the NGUI position of a widget with any pivot", () => {
  const frame = { cx: 0, cy: -12, w: 1300, h: 1050 };
  // the close icon: 48 from the right, 39 from the top, 60 square, pivot centre
  const close = box(1300 - 48 - 60, 39, 60, 60);
  assert.deepEqual(toNode(close, 4, frame), [650 - 48 - 30, -12 + 525 - 39 - 30]);
  assert.deepEqual(toNode(box(0, 0, 10, 10), 0, frame), [-650, 513]);
  assert.deepEqual(toNode(box(0, 0, 10, 10), 8, frame), [-640, 503]);
});

test("the scales agree with the tokens they grew from", () => {
  assert.deepEqual([CONTROL.buttonS.h, CONTROL.buttonS.fs, CONTROL.buttonS.minW, 2 * CONTROL.buttonS.pad],
    [SMALL_BUTTON.h, SMALL_BUTTON.fs, SMALL_BUTTON.minW, SMALL_BUTTON.glyphPad]);
  assert.deepEqual([CONTROL.chip.h, CONTROL.chip.fs, CONTROL.chip.minW], [CHIP.h, CHIP.fs, CHIP.w]);
  assert.equal(TYPE.chip.fs, CHIP.fs); assert.equal(TYPE.chip.min, CHIP.fsMin);
  assert.equal(SPACE.m, SMALL_BUTTON.gap); assert.equal(SPACE.s, SETTINGS.rowGap); assert.equal(PANEL.stackGap, SETTINGS.rowGap);
  assert.equal(GRID.gutter, SPACE.xl); assert.equal(GRID.pageMargin, SPACE.xxl);
  // every role's floor is at most its size, and the ramp descends
  const sizes = Object.values(TYPE).map((t) => t.fs);
  assert.deepEqual(sizes, [...sizes].sort((a, b) => b - a));
  for (const t of Object.values(TYPE)) assert.ok(t.min <= t.fs);
  // controls climb, and a row's action fits the row (List_01: buttonM 27 above its bottom)
  const heights = ["badge", "chip", "buttonS", "buttonM", "buttonL"].map((k) => CONTROL[k].h);
  assert.deepEqual(heights, [...heights].sort((a, b) => a - b));
  assert.ok(LIST_ROW.h - LIST_ROW.actionBottom - CONTROL.buttonM.h > LIST_ROW.titleTop);
  // the S popup's footer: two buttonL bodies, the gap and the sides fill the frame's width
  assert.equal(2 * CONTROL.buttonL.minW + POPUP_FRAME.footer.gap + 2 * POPUP_FRAME.footer.side, POPUP_FRAME.S.w);
});


test("a stack justifies its items along a supplied height without compressing them", () => {
  const items = [{ w: 100, h: 50 }, { w: 300, h: 20 }];
  for (const [justify, offset] of [["start", 0], ["center", 50], ["end", 100]]) {
    const s = stack(items, { x: 5, y: 10, w: 400, h: 190, gap: 20, align: "center", justify });
    assert.deepEqual(s.boxes.map(b => [b.x, b.y]), [[155, 10 + offset], [55, 80 + offset]]);
    assert.equal(s.h, 190); assert.equal(s.w, 400);
    const tight = stack(items, { y: 10, h: 30, gap: 20, justify });
    assert.deepEqual(tight.boxes.map(b => b.y), [10, 80], "no negative free space");
    assert.equal(tight.h, 30);
    assert.equal(stack([], { h: 100, justify }).h, 100);
    assert.equal(stack([], { gap: 20, justify }).h, 0);
  }
});
