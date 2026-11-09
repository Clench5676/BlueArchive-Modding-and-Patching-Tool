// Tests for the memorial lobby's camera framing (BL-137): the views it allows (web/js/lobby.js framingView: centred
// across, no cell past the art that some pose leaves bare), what the shutter keeps (cameraParams) and where the intro
// has its skeletons at a time (web/js/lobby-intro.js sampler). Run with Node's test runner.
import assert from "node:assert/strict";
import { test } from "node:test";

const Lobby = await import("../../src/bmpt/web/js/lobby.js");
const Intro = await import("../../src/bmpt/web/js/lobby-intro.js");

const G = { x: -1480, y: -832.5, w: 2960, h: 1665 };
const C = Lobby.FRAMING_CELL;
const near = (a, b, eps = 1e-6, what = "") => assert.ok(Math.abs(a - b) <= eps, `${what} ${a} != ${b}`);
// a grid three game views across, every cell bad except those whose centre is inside `art` (and the game's view)
function limitsWith(art) {
  const grid = { X0: G.x - G.w, Y0: G.y - G.h, gw: Math.ceil((G.w * 3) / C), gh: Math.ceil((G.h * 3) / C) };
  const bad = new Uint8Array(grid.gw * grid.gh);
  for (let j = 0; j < grid.gh; j++) for (let i = 0; i < grid.gw; i++) {
    const x = grid.X0 + (i + 0.5) * C, y = grid.Y0 + (j + 0.5) * C;
    const inG = x >= G.x && x <= G.x + G.w && y >= G.y && y <= G.y + G.h;
    const inArt = x >= art.x0 && x <= art.x1 && y >= art.y0 && y <= art.y1;
    bad[j * grid.gw + i] = inG || inArt ? 0 : 1;
  }
  return Lobby.framingLimits(G, grid, bad);
}

test("art no wider than the game's view keeps the camera inside it", () => {
  const lim = limitsWith({ x0: G.x, x1: G.x + G.w, y0: G.y, y1: G.y + G.h });
  assert.ok(lim.wMax >= G.w && lim.wMax < G.w + C, `widest ${lim.wMax}`);
  const v = Lobby.framingView({ x: -3000, y: -2000, w: 6000, h: 3375 }, lim);
  near(v.w, lim.wMax); near(v.x + v.w / 2, 0); near(v.y + v.h / 2, G.y + G.h / 2, C, "y");
  // zoomed in, the view moves up and down inside the game's view, and stays centred across
  const z = Lobby.framingView({ x: 400, y: 300, w: G.w / 2, h: G.h / 2 }, lim);
  near(z.x, -G.w / 4); assert.ok(z.y + z.h <= G.y + G.h + C / 2 && z.y >= G.y - C / 2, `${z.y} ${z.h}`);
});

test("wider art lets the camera zoom out as far as it reaches on its narrower side", () => {
  // art 400 units past the game's view on the left, 200 on the right, 600 above and below
  const art = { x0: G.x - 400, x1: G.x + G.w + 200, y0: G.y - 600, y1: G.y + G.h + 600 };
  const lim = limitsWith(art);
  // centred across: half a view may reach 1480 + 200 units at most (to a cell)
  assert.ok(Math.abs(lim.wMax - 2 * (1480 + 200)) <= 2 * C, `widest ${lim.wMax}`);
  const v = Lobby.framingView({ x: -5000, y: -3000, w: 10000, h: 5625 }, lim);
  near(v.w, lim.wMax);
  near(v.x + v.w / 2, 0);
  assert.ok(v.y >= art.y0 - C && v.y + v.h <= art.y1 + C);
});

test("a view that would show past the art moves to the nearest height that does not", () => {
  const art = { x0: G.x - 300, x1: G.x + G.w + 300, y0: G.y, y1: G.y + G.h + 500 }; // nothing below the game's view
  const lim = limitsWith(art);
  const w = G.w + 400, h = (w * 9) / 16;
  const v = Lobby.framingView({ x: -w / 2, y: G.y - 200, w, h }, lim); // asked lower than the art goes
  near(v.w, w);
  assert.ok(v.y >= G.y - C / 2, `bottom ${v.y}`);
  assert.ok(v.y + v.h <= art.y1 + C, `top ${v.y + v.h}`);
});

test("the shutter keeps the lobby scaled about the screen centre and moved so the view fills the screen", () => {
  near(Lobby.cameraParams(G, G).scale, 1);
  assert.deepEqual(Lobby.cameraParams(G, G).offset.map((v) => Math.abs(v)), [0, 0]);
  const w = 3552, h = (w * 9) / 16, V = { x: -w / 2, y: -h / 2 + 150, w, h };
  const p = Lobby.cameraParams(V, G);
  near(p.scale, G.w / w);
  near(p.offset[0], 0);
  near(p.offset[1], -p.scale * 150);
  // a point of the lobby at V's top edge lands on the screen's top edge
  const top = V.y + V.h;
  near(p.scale * top + p.offset[1], G.y + G.h, 1e-6, "top edge");
});

test("the intro sampler places a moved skeleton by its timeline and plays its clips on their tracks", () => {
  // node 0: the skeleton's GameObject, sliding from x -500 to 0 over 1 s; node 1: a scene switched on from 0.5 s
  const slide = { d: [0, 1, [-500, 0]] };
  const intro = {
    duration: 2,
    nodes: [{ own: true, trs: [0, 0, 0, 0, 0, 0, 1, 1, 1, 1] }, { own: false }],
    props: [{ on: "node", i: 0, attr: "position", k: 0, c: slide }, { on: "node", i: 1, attr: "active", c: { s: [[-1e9, 0, 0, 0, 0], [0.5, 0, 0, 0, 1]] } }],
    spine: [{ skeleton: "hero", track: 1, clips: [{ t: 0.2, d: 1, anim: "Start_Idle_01", loop: false }] }],
  };
  const layers = [
    { def: { name: "hero", moves: { m: [[1, 0, 0, 0, 0, 1, 0, -962, 0, 0, 1, 0], [100, 0, 0, 0, 0, 100, 0, 0, 0, 0, 100, 0]], nodes: [0] } }, introOnly: false, x: 0, y: -962, scale: 1 },
    { def: { name: "scene", gate: [1] }, introOnly: true, x: 0, y: 0, scale: 1 },
  ];
  const sample = Intro.sampler(intro, layers);
  const a = sample(0);
  near(a[0].x, -500 * 1); assert.equal(a[1].shown, false); assert.deepEqual(a[0].clips, []);
  const b = sample(0.7);
  near(b[0].x, -150, 1e-6, "slid"); near(b[0].y, -962); near(b[0].scale, 1);
  assert.equal(b[1].shown, true);
  assert.deepEqual(b[0].clips.map((c) => [c[0], c[1], +c[2].toFixed(6), c[3]]), [[1, "Start_Idle_01", 0.5, false]]);
  const c = sample(2);
  near(c[0].x, 0); assert.deepEqual(c[0].clips, []);
});
