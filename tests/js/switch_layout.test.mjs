// Decision 3 of the design system's screens job: the switch cards' grid (switch-layout.js) is laid out in a container (the paper
// less SPACE.xl on every side), a row of columns and a stack of rows; the least gap between cards, across and down, is SPACE.xl.
// The cards carry no text, so the checks hold in every language; they run at the standard width (1818) and at a narrow and a wide one.
import assert from "node:assert/strict";
import { test } from "node:test";
import { SPACE } from "../../src/bmpt/web/js/ui-tokens.js";
import { cardInkBounds, switchGrid, placeSwitchCards, portraitPanel, splitPortraitPanel } from "../../src/bmpt/web/js/switch-layout.js";

const near = (a, b, eps = 1e-6) => assert.ok(Math.abs(a - b) < eps, `${a} != ${b}`);
// a card's tree as popup.js builds it, reduced to what the layout reads: the body (card.w x card.h) and the name band under it,
// both inclined 10 degrees as the prefab's are
const cardTree = ({ w, h, bar }) => ({ name: "Card", pos: [0, 0], scale: [1, 1], active: true,
  widgets: [{ sprite: "Body", w_: w, h_: h, pivot: 4, incline: 10 }],
  children: [{ name: "Bar", pos: [0, -(h + bar) / 2], scale: [1, 1], active: true, widgets: [{ sprite: "Bar", w_: w, h_: bar, pivot: 4, incline: 10 }], children: [] }] });
const KINDS = ["spr", "lobby", "model", "other"];
const PAPER = { left: 0, right: 1818, top: 600 };

test("the gap between cards, across and down, is the grid gutter (SPACE.xl) for every kind", () => {
  for (const width of [1300, 1818, 2200]) for (const kind of KINDS) {
    const { card, pitch, rowH, columns } = switchGrid(kind, width);
    assert.equal(rowH, card.h + card.bar, `${kind}: a row is a card and its name band`);
    assert.equal(pitch[1] - rowH, SPACE.xl, `${kind}: rows ${SPACE.xl} apart`);
    if (kind === "spr") assert.equal(pitch[0] - card.w, SPACE.xl, "portraits: bodies SPACE.xl apart, however wide the viewport");
    else if (columns > 1) assert.ok(pitch[0] - card.w >= SPACE.xl, `${kind} ${width}: columns at least SPACE.xl apart (${pitch[0] - card.w})`);
  }
  assert.equal(switchGrid("spr").columns, 4);
  assert.equal(switchGrid("lobby").columns, 2);
  assert.equal(switchGrid("model").columns, 4);
});

test("the column count is the most cards that fit the content box with at least the gutter between them", () => {
  for (const width of [1300, 1818, 2200]) for (const kind of ["lobby", "model", "other"]) {
    const { card, columns, overhang } = switchGrid(kind, width), room = width - 2 * SPACE.xl - overhang, cap = { lobby: 2, model: 4, other: 6 }[kind];
    assert.ok(columns * card.w + (columns - 1) * SPACE.xl <= room + 1e-9 || columns === 1, `${kind} ${width}: ${columns} fit`);
    if (columns < cap) assert.ok((columns + 1) * card.w + columns * SPACE.xl > room, `${kind} ${width}: one more would not fit`);
  }
});

test("full rows span the content box edge to edge and later rows stand a row and the gutter below", () => {
  for (const kind of ["lobby", "model", "other"]) {
    const grid = switchGrid(kind), trees = Array.from({ length: grid.columns * 2 + 1 }, () => cardTree(grid.card));
    const bounds = placeSwitchCards(trees, structuredClone(grid), PAPER);
    const first = bounds.slice(0, grid.columns), second = bounds.slice(grid.columns, 2 * grid.columns);
    near(Math.min(...first.map((b) => b.left)), PAPER.left + SPACE.xl);
    near(Math.max(...first.map((b) => b.right)), PAPER.right - SPACE.xl);
    // the same columns in the next row, one row and the gutter lower
    first.forEach((b, i) => { near(second[i].left, b.left); near(second[i].top, b.top - (grid.rowH + SPACE.xl)); });
    // the first row starts the inset under the paper's top; a short row keeps the left inset and the columns
    near(Math.max(...first.map((b) => b.top)), PAPER.top - SPACE.xl);
    near(bounds[2 * grid.columns].left, first[0].left);
  }
});

test("portraits stay one strip, their bodies SPACE.xl apart from the left inset", () => {
  const grid = switchGrid("spr"), trees = Array.from({ length: 7 }, () => cardTree(grid.card));
  const bounds = placeSwitchCards(trees, structuredClone(grid), PAPER);
  near(bounds[0].left, PAPER.left + SPACE.xl);
  near(bounds[0].top, PAPER.top - SPACE.xl);
  bounds.slice(1).forEach((b, i) => { near(b.left - bounds[i].left, grid.card.w + SPACE.xl); near(b.top, bounds[0].top); });
});

test("the cards are placed the same with and without the grid's row height (older callers)", () => {
  const grid = switchGrid("model"), { rowH, ...legacy } = structuredClone(grid);
  const a = Array.from({ length: 9 }, () => cardTree(grid.card)), b = Array.from({ length: 9 }, () => cardTree(grid.card));
  placeSwitchCards(a, structuredClone(grid), PAPER); placeSwitchCards(b, legacy, PAPER);
  assert.deepEqual(a.map((t) => t.pos), b.map((t) => t.pos));
  assert.ok(cardInkBounds(a[0]).right > cardInkBounds(a[0]).left);
});

test("the generic list keeps six centred columns inside the paper's inset at the standard width", () => {
  const grid = switchGrid(null), trees = Array.from({ length: 6 }, () => cardTree(grid.card));
  assert.equal(grid.columns, 6);
  assert.equal(grid.pitch[0] - grid.card.w, SPACE.l);
  assert.equal(grid.pitch[1] - grid.rowH, SPACE.xxl);
  const bounds = placeSwitchCards(trees, structuredClone(grid), PAPER);
  assert.ok(Math.min(...bounds.map((b) => b.left)) >= PAPER.left + SPACE.xl && Math.max(...bounds.map((b) => b.right)) <= PAPER.right - SPACE.xl);
});

test("the portrait detail panel's gaps are the scale's: halves SPACE.l, chips SPACE.s, fields SPACE.m", () => {
  assert.equal(portraitPanel.gap, SPACE.l);
  const node = (name, extra = {}) => ({ name, pos: [0, 0], scale: [1, 1], active: true, widgets: [{ w_: 100, h_: 40, text: name }], children: [], ...extra });
  const tree = node("Root", { widgets: [], children: [node("Picture", { widgets: [], children: [node("Texture", { widgets: [{ texture: "t" }] })] }),
    node("Name"), node("OriginalName"), node("Chip_0", { children: [node("Line"), node("Text")] }), node("Chip_1", { children: [node("Line"), node("Text")] }),
    node("F_0"), node("V_0"), node("L_0"), node("Fields", { children: [node("Line")] })] });
  const find = (t, path) => path.split("/").reduce((n, key) => n.children.find((c) => c.name === key), t);
  splitPortraitPanel(tree, find, (text, fs) => ({ text, fs: Math.min(fs, 30), lines: 1 }));
  const width = (portraitPanel.right - portraitPanel.left - portraitPanel.gap) / 2, [c0, c1] = [find(tree, "Chip_0"), find(tree, "Chip_1")];
  near(c0.widgets[0].w_, (width - SPACE.s) / 2);
  near(c1.pos[0] - c0.pos[0] - c0.widgets[0].w_, SPACE.s);
});
