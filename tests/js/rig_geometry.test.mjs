// The remap's pose geometry, from synthetic JSON skeletons through the bundled Spine runtime under Node.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import vm from "node:vm";
import { forRemap, poses, restOnly } from "../../src/bmpt/web/js/rig-geometry.js";

const context = vm.createContext({ console, TextDecoder, TextEncoder, Float32Array, Float64Array, Uint8Array, Uint16Array,
  Int8Array, Int16Array, Int32Array, Uint32Array, ArrayBuffer, DataView, Math, Map, Set, JSON });
vm.runInContext(readFileSync(new URL("../../src/bmpt/web/vendor/spine-player-4.2.120/spine-player.min.js", import.meta.url), "utf8"), context);
const S = context.spine;

const ATLAS = "page.png\nsize: 64,64\nfilter: Linear,Linear\npma: false\nbody\n bounds: 0,0,8,8\nface_a\n bounds: 10,0,4,4\n"
  + "face_b\n bounds: 20,0,4,4\nglow\n bounds: 30,0,2,2\n";

function skeleton({ idle = "Idle_01", rest = true, extra = {} } = {}) {
  const animations = { "01": { slots: { face: { attachment: [{ name: "face_b" }] } } }, ...extra };
  if (idle) animations[idle] = { bones: { root: { translate: [{ x: 10, y: 0 }] } } };
  if (rest) animations["00"] = { slots: { face: { attachment: [{ name: "face_a" }] } } };
  return JSON.stringify({
    skeleton: { spine: "4.2.33" },
    bones: [{ name: "root" }],
    slots: [
      { name: "body", bone: "root", attachment: "body" },
      { name: "face", bone: "root", attachment: "face_a" },
      { name: "glow", bone: "root", attachment: "glow", blend: "additive", color: "ffffff80" },
    ],
    skins: [{ name: "default", attachments: {
      body: { body: { width: 8, height: 8 } },
      face: { face_a: { width: 4, height: 4 }, face_b: { width: 4, height: 4 } },
      glow: { glow: { width: 2, height: 2 } },
    } }],
    animations,
  });
}
const encoded = (text) => ({ version: "4.2.33", data: Buffer.from(text).toString("base64") });
const xs = (item) => item.vertices.filter((_, i) => i % 2 === 0);

test("the rest pose draws the idle at time 0 with every slot in draw order", () => {
  const rig = poses(S, ATLAS, skeleton());
  assert.deepEqual(rig.slots, ["body", "face", "glow"]);
  const rest = rig.poses[""];
  assert.deepEqual(rest.map((d) => [d.slot, d.region, d.page, d.blend]),
    [["body", "body", "page.png", 0], ["face", "face_a", "page.png", 0], ["glow", "glow", "page.png", 1]]);
  assert.deepEqual([Math.min(...xs(rest[0])), Math.max(...xs(rest[0]))], [6, 14]);  // the idle moved the root by 10
  assert.equal(rest[2].alpha, 0.502);
  for (const item of rest) {
    assert.equal(item.vertices.length, 8);
    assert.deepEqual(item.triangles, [0, 1, 2, 2, 3, 0]);
    assert.ok(item.uvs.every((v) => v >= 0 && v <= 1));
  }
});

test("each other animation is a pose of its own over the idle, and a slot that switches shows it", () => {
  const rig = poses(S, ATLAS, skeleton({ extra: { Eye_Close_01: {} } }));
  assert.deepEqual(Object.keys(rig.poses).sort(), ["", "01", "Eye_Close_01"]);
  assert.equal(rig.poses["01"][1].region, "face_b");
  assert.deepEqual([Math.min(...xs(rig.poses["01"][0])), Math.max(...xs(rig.poses["01"][0]))], [6, 14]);
  const uvs = rig.poses["01"][1].uvs;
  assert.ok(Math.min(...uvs.filter((_, i) => i % 2 === 0)) >= 20 / 64 - 1e-6);
});

test("a skeleton without an idle rests in its setup pose, and one without 00 rests without it", () => {
  const still = poses(S, ATLAS, skeleton({ idle: null }));
  assert.deepEqual([Math.min(...xs(still.poses[""][0])), Math.max(...xs(still.poses[""][0]))], [-4, 4]);
  assert.deepEqual(Object.keys(still.poses).sort(), ["", "01"]);
  const other = poses(S, ATLAS, skeleton({ idle: "idle_02", rest: false }));
  assert.deepEqual(Object.keys(other.poses).sort(), ["", "01"]);
  assert.equal(other.poses[""][1].region, "face_a");
  assert.equal(Math.min(...xs(other.poses[""][0])), 6);
});

test("an attachment whose region the atlas lacks draws nothing", () => {
  const rig = poses(S, ATLAS.replace("glow\n bounds: 30,0,2,2\n", ""), skeleton());
  assert.deepEqual(rig.poses[""].map((d) => d.slot), ["body", "face"]);
});

test("the remap pairs each official atlas with its skeleton and takes the mod's rig, else the recorded one", () => {
  const official = { skeletons: { "a.skel": encoded(skeleton()) }, atlases: { "a.atlas": ATLAS }, textures: {} };
  const moved = ATLAS.replace("body\n bounds: 0,0,8,8", "body\n bounds: 40,40,8,8");
  const report = { kind: "spr", official, mod: official, source: { skeletons: { "a.skel": encoded(skeleton()) }, atlases: { "a.atlas": moved } } };
  const u = (geometry, side) => Math.min(...geometry["a.atlas"][side].poses[""][0].uvs);
  let geometry = forRemap(report, S);
  assert.deepEqual(Object.keys(geometry), ["a.atlas"]);
  assert.equal(u(geometry, "source"), 40 / 64);  // the layout the textures were drawn on
  assert.equal(u(geometry, "target"), 0);
  geometry = forRemap({ ...report, mod: { ...official, atlases: { "A.atlas": moved.replace("40,40", "48,48") } } }, S);
  assert.equal(u(geometry, "source"), 48 / 64);  // the mod's own atlas comes first
  geometry = forRemap({ ...report, source: undefined }, S);
  assert.equal(u(geometry, "source"), 0);
  assert.deepEqual(forRemap({ ...report, kind: "lobby" }, S), {});
  const rest = restOnly(forRemap(report, S));
  assert.deepEqual(Object.keys(rest["a.atlas"].source.poses), [""]);
});
