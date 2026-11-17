import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import vm from "node:vm";
import { isSpecial, specialFraming, specialFramingOf, specialView } from "../../src/bmpt/web/js/detail/special-view.js";
import { artBounds, settle, validRect, worldBounds } from "../../src/bmpt/web/js/lobby-health.js";
import { layersOf } from "../../src/bmpt/web/js/lobby.js";

const read = (path) => readFileSync(new URL(path, import.meta.url), "utf8");
const context = vm.createContext({
  console,
  TextDecoder,
  TextEncoder,
  Float32Array,
  Uint8Array,
  Uint16Array,
  Int16Array,
  Int32Array,
  ArrayBuffer,
  DataView,
});
vm.runInContext(read("../../src/bmpt/web/vendor/spine-player-4.2.120/spine-player.min.js"), context);
globalThis.spine = context.spine;
const union = (a, b) => ({
  x0: Math.min(a.x0, b.x0),
  y0: Math.min(a.y0, b.y0),
  x1: Math.max(a.x1, b.x1),
  y1: Math.max(a.y1, b.y1),
});
function player() {
  const atlas = new spine.TextureAtlas(read("../fixtures/lobby/official.atlas"));
  atlas.setTextures({ get: () => ({ getImage: () => ({ width: 64, height: 64 }), setFilters() {}, setWraps() {} }) });
  const json = JSON.parse(read("../fixtures/lobby/official.json"));
  Object.assign(json.skeleton, { x: -1e9, y: -1e9, width: 2e9, height: 2e9 });
  const data = new spine.SkeletonJson(new spine.AtlasAttachmentLoader(atlas)).readSkeletonData(json);
  const p = {
    skeleton: new spine.Skeleton(data),
    animationState: new spine.AnimationState(new spine.AnimationStateData(data)),
  };
  settle(p, { name: "np0032_npc" }, null, () => {});
  return p;
}
const rectOf = (v) => ({ x0: v.x, y0: v.y, x1: v.x + v.w, y1: v.y + v.h });
const contains = (a, b) => b.x0 >= a.x0 - 1e-6 && b.y0 >= a.y0 - 1e-6 && b.x1 <= a.x1 + 1e-6 && b.y1 <= a.y1 + 1e-6;
const near = (a, b) => assert.ok(Math.abs(a - b) < 1e-6, `${a} != ${b}`);

test("special NPCs use character framing and work pages, tasks and cafes use scene framing", () => {
  for (const [core, category, expected] of [
    ["np0032_npc", "spinebackground", "character"],
    ["arona", "spinecharacters", "character"],
    ["np0032_npc", null, "character"],
    ["arona_workpage_daytime", "spinebackground", "scene"],
    ["plana_workpage", "spinecharacters", "scene"],
    ["task", "spinelobbies", "scene"],
    ["cafe", "spinebackground", "scene"],
  ]) {
    const v = { key: `special:${core}`, lobby: core, spineCategory: category };
    assert.equal(isSpecial(v), true);
    assert.equal(specialFraming(v), expected);
  }
  assert.equal(specialFraming({ key: "ibuki", lobby: "ibuki_home" }), null);
});

test("NPCs fit wholly in the left pane and fullscreen, at any scale or origin", () => {
  for (const r of [
    { x0: -100, y0: 0, x1: 100, y1: 1000 },
    { x0: -1000, y0: 5, x1: 1000, y1: 500 },
  ]) {
    for (const scale of [0.01, 1, 100]) {
      const bounds = worldBounds(r, { x: 1e6, y: -1e6, scale });
      const left = specialView(bounds, "character", 16 / 9, 0.25),
        full = specialView(bounds, "character");
      assert.ok(contains(rectOf({ ...left, w: left.w / 2 }), bounds), "the whole character clears the right panel");
      assert.ok(contains(rectOf(full), bounds));
      near((bounds.x0 + bounds.x1) / 2, left.x + left.w / 4);
    }
  }
});

test("scene framing fills every supported view aspect from its own bounds", () => {
  const bounds = { x0: 5000, y0: -4000, x1: 9000, y1: -2000 };
  for (const aspect of [16 / 9, 1, 404 / 456, 384 / 352]) {
    const v = specialView(bounds, "scene", aspect);
    assert.ok(contains(bounds, rectOf(v)));
    near(v.w / v.h, aspect);
    assert.ok(Math.abs(v.w - 4000) < 1e-6 || Math.abs(v.h - 2000) < 1e-6, "one axis reaches the art edge");
  }
  for (const bounds of [null, { x0: 0, y0: 0, x1: 0, y1: 1 }])
    assert.throws(() => specialView(bounds, "character"), /bounds/);
});

test("detail and thumbnails frame real posed vertices, ignoring stale export bounds", () => {
  const lobby = read("../../src/bmpt/web/js/lobby.js");
  const code = lobby.slice(lobby.indexOf("function measureSpecial("), lobby.indexOf("export function frame("));
  const thumbs = read("../../src/bmpt/web/js/detail/thumbs.js");
  const thumbnail = new Function(
    "spine",
    "specialView",
    "specialFramingOf",
    "artBounds",
    `${thumbs.slice(thumbs.indexOf("function viewFor("), thumbs.indexOf("function loadPlayer("))}; return viewFor;`,
  )(spine, specialView, specialFramingOf, artBounds);
  const p = player(),
    layer = { name: "np0032_npc", x: 40000, y: -30000, scale: 0.01, player: p };
  const edge = worldBounds(artBounds(p.skeleton), layer);
  for (const framing of ["character", "scene"]) {
    const current = { framing };
    const measure = new Function(
      "env",
      `const { current, worldBounds, artBounds, validRect, union, specialFramingOf, specialView, rectOf } = env; ${code}; return measureSpecial;`,
    )({ current, worldBounds, artBounds, validRect, union, specialFramingOf, specialView, rectOf });
    const m = measure([layer]),
      shaped = specialFramingOf(framing, edge);
    assert.deepEqual(m.full, specialView(edge, shaped));
    const thumb = thumbnail(p, "roster", 404 / 456, framing),
      expected = specialView(artBounds(p.skeleton), shaped, 404 / 456);
    assert.deepEqual([thumb.x, thumb.y, thumb.width, thumb.height], [expected.x, expected.y, expected.w, expected.h]);
    assert.ok(m.full.w < 100, "the huge export header never sets the zoom");
  }
});

test("art taller than wide is framed as a whole character, whatever the slot's name says", () => {
  // operator and guide NPCs filed as backgrounds (momoka_weekdungeon, rin_raiddungeon, np0013_quest, np0281_skill):
  // one standing figure, about half as wide as tall
  const tall = { x0: -418, y0: -873, x1: 667, y1: 1341 },
    wide = { x0: -1600, y0: -1100, x1: 1600, y1: 1150 };
  assert.equal(specialFramingOf("scene", tall), "character");
  assert.equal(specialFramingOf("scene", wide), "scene", "a work page or shop scene still fills the view");
  assert.equal(specialFramingOf("character", wide), "character", "a named NPC keeps its framing");
  assert.equal(specialFramingOf(null, tall), null, "students' lobbies are not special slots");
  assert.equal(specialFramingOf("scene", null), "scene");
  assert.equal(specialFramingOf("scene", tall, 23), "scene", "a set of skeletons (ui_set) keeps its framing");
  for (const aspect of [16 / 9, 404 / 456]) {
    const v = specialView(tall, specialFramingOf("scene", tall), aspect);
    assert.ok(contains(rectOf(v), tall), "head and feet both in frame");
  }
});

test("the detail view keeps scene framing for a tall set of several skeletons", () => {
  const lobby = read("../../src/bmpt/web/js/lobby.js");
  const code = lobby.slice(lobby.indexOf("function measureSpecial("), lobby.indexOf("export function frame("));
  const tall = { x0: -970, y0: -1000, x1: 975, y1: 1151 },
    art = () => tall,
    world = (r) => r;
  const m = new Function(
    "env",
    `const { current, worldBounds, artBounds, validRect, union, specialFramingOf, specialView, rectOf } = env; ${code}; return measureSpecial;`,
  )({
    current: { framing: "scene" },
    worldBounds: world,
    artBounds: art,
    validRect,
    union,
    specialFramingOf,
    specialView,
    rectOf,
  })([
    { name: "Ui_RaidBoss_Binah", player: {} },
    { name: "Ui_RaidBoss_Hod", player: {} },
  ]);
  assert.equal(m.sparse, false);
  assert.deepEqual(m.full, specialView(tall, "scene"));
});

test("the detail view and its thumbnail frame a tall scene-named NPC alike", () => {
  const lobby = read("../../src/bmpt/web/js/lobby.js");
  const code = lobby.slice(lobby.indexOf("function measureSpecial("), lobby.indexOf("export function frame("));
  const thumbs = read("../../src/bmpt/web/js/detail/thumbs.js");
  const tall = { x0: -418, y0: -873, x1: 667, y1: 1341 };
  const art = () => tall,
    world = (r) => r;
  const current = { framing: "scene" };
  const m = new Function(
    "env",
    `const { current, worldBounds, artBounds, validRect, union, specialFramingOf, specialView, rectOf } = env; ${code}; return measureSpecial;`,
  )({ current, worldBounds: world, artBounds: art, validRect, union, specialFramingOf, specialView, rectOf })([
    { name: "momoka_weekdungeon", player: {} },
  ]);
  assert.equal(m.sparse, true);
  assert.ok(
    contains(rectOf(m.full), tall) && contains(rectOf({ ...m.left, w: m.left.w / 2 }), tall),
    "whole in fullscreen and the left pane",
  );
  const thumbnail = new Function(
    "spine",
    "specialView",
    "specialFramingOf",
    "artBounds",
    `${thumbs.slice(thumbs.indexOf("function viewFor("), thumbs.indexOf("function loadPlayer("))}; return viewFor;`,
  )(spine, specialView, specialFramingOf, art);
  const t = thumbnail({ skeleton: {} }, "landscape", 16 / 9, "scene");
  assert.deepEqual([t.x, t.y, t.width, t.height], Object.values(specialView(tall, "character", 16 / 9)));
  assert.deepEqual(m.full, specialView(tall, "character"), "the same rule as the thumbnail");
});

test("special layers have no memorial lobby fallback offset", async (t) => {
  t.mock.method(globalThis, "fetch", async (url) => ({
    ok: true,
    json: async () => (url.endsWith("files.json") ? ["np0032_npc.skel", "np0032_npc.atlas"] : null),
  }));
  const { layers } = await layersOf("np0032_npc", null, { artBase: "/art/", base: "/layout/" }, "character");
  assert.deepEqual([layers[0].x, layers[0].y, layers[0].scale], [0, 0, 1]);
});
