// A clip added on demand to the shown chibi (chibi.js addClips, actClips): fetched from the chibi route with the shown
// chibi's source, its tracks moved onto the shown chibi's nodes, its renderer switches found in the shown chibi or
// brought along from the clip's GLB, and played in turn. Only the GPU is a stand-in.
import assert from "node:assert/strict";
import { register } from "node:module";
import { test } from "node:test";
const VENDOR = new URL("../../src/bmpt/web/vendor/three-0.160.0/", import.meta.url).href;
register(
  `data:text/javascript,${encodeURIComponent(`export async function resolve(spec, ctx, next) {
  if (spec === "three") return { url: ${JSON.stringify(VENDOR + "build/three.module.min.js")}, shortCircuit: true };
  if (spec.startsWith("three/addons/")) return { url: ${JSON.stringify(VENDOR + "examples/jsm/")} + spec.slice(13), shortCircuit: true };
  return next(spec, ctx); }`)}`,
);
const THREE = await import("three");
const { createStillView } = await import("../../src/bmpt/web/js/chibi/chibi.js");

// one buffer of float32 runs, each a buffer view
function buffer(...runs) {
  const floats = runs.flat(),
    bytes = new Float32Array(floats);
  let offset = 0;
  const views = runs.map((run) => {
    const v = { buffer: 0, byteOffset: offset, byteLength: run.length * 4 };
    offset += run.length * 4;
    return v;
  });
  return {
    buffers: [
      {
        uri: `data:application/octet-stream;base64,${Buffer.from(bytes.buffer).toString("base64")}`,
        byteLength: bytes.byteLength,
      },
    ],
    bufferViews: views,
  };
}
const TRIANGLE = [-0.3, 0, 0, 0.3, 0, 0, 0, 1.1, 0];
const accessorsOf = (meshes, anims) => [
  ...meshes.map((v) => ({
    bufferView: v,
    componentType: 5126,
    count: 3,
    type: "VEC3",
    min: [-0.3, 0, 0],
    max: [0.3, 1.1, 0],
  })),
  ...anims.flatMap(([times, values]) => [
    { bufferView: times, componentType: 5126, count: 2, type: "SCALAR", min: [0], max: [1] },
    { bufferView: values, componentType: 5126, count: 2, type: "VEC4" },
  ]),
];
const glb = (doc) => new TextEncoder().encode(JSON.stringify({ asset: { version: "2.0" }, scene: 0, ...doc })).buffer;
const unlit = (name) => ({ name, extras: { kind: "unlit", colors: { _Color: [1, 1, 1, 1] } } });
const turn = (node, slot, name, extra = {}) => ({
  name,
  channels: [{ sampler: 0, target: { node, path: "rotation" } }],
  samplers: [{ input: 1, output: 2, interpolation: "LINEAR" }],
  extras: { slot, loop: true, duration: 1, events: [], ...extra },
});
// the hierarchy both GLBs share: the prefab's root, the hips, the body (drawn), the gag face (disabled in the prefab)
const HIERARCHY = [
  { name: "Echelon_X_Y", children: [1, 2, 3] },
  { name: "Bip001" },
  { name: "Body" },
  { name: "Face01" },
];
const base = glb({
  scenes: [{ nodes: [0] }],
  ...buffer(TRIANGLE, [0, 1], [0, 0, 0, 1, 0, 0.1, 0, 0.995]),
  nodes: HIERARCHY.map((n, i) => (i === 2 ? { ...n, mesh: 0 } : n)),
  meshes: [{ primitives: [{ attributes: { POSITION: 0 }, material: 0 }] }],
  materials: [unlit("body")],
  accessors: accessorsOf([0], [[1, 2]]),
  animations: [turn(1, "Formation_Idle", "X_Y_Formation_Idle")],
  extras: { prefab: "Echelon_X_Y", dev: "x_y", clips: ["X_Y_Formation_Idle", "X_Y_Exs", "X_Y_Normal_Attack_Start"] },
});
// the EX skill: turns the hips, the body off and the gag face (brought along, static) on
const exs = glb({
  scenes: [{ nodes: [0] }],
  ...buffer(TRIANGLE, [0, 1], [0, 0, 0, 1, 0, 0.5, 0, 0.866]),
  nodes: HIERARCHY.map((n, i) => (i === 3 ? { ...n, mesh: 0 } : n)),
  meshes: [{ primitives: [{ attributes: { POSITION: 0 }, material: 0 }] }],
  materials: [unlit("gag")],
  accessors: accessorsOf([0], [[1, 2]]),
  animations: [
    turn(1, "X_Y_Exs", "X_Y_Exs", {
      loop: false,
      toggles: [
        { node: 2, keys: [[0, 0]] },
        { node: 3, keys: [[0, 1]] },
      ],
    }),
  ],
  extras: { prefab: "Echelon_X_Y", dev: "x_y", clip: "X_Y_Exs", hierarchy: 4 },
});
// a clip of another hierarchy (another source answered)
const other = glb({
  scenes: [{ nodes: [0] }],
  ...buffer([0, 1], [0, 0, 0, 1, 0, 0.5, 0, 0.866]),
  nodes: [{ name: "Echelon_Z", children: [1] }, { name: "Bip001" }],
  accessors: [
    { bufferView: 0, componentType: 5126, count: 2, type: "SCALAR", min: [0], max: [1] },
    { bufferView: 1, componentType: 5126, count: 2, type: "VEC4" },
  ],
  animations: [
    { ...turn(1, "X_Y_Normal_Attack_Start", "X_Y_Normal_Attack_Start"), samplers: [{ input: 0, output: 1 }] },
  ],
  extras: { clip: "X_Y_Normal_Attack_Start", hierarchy: 2 },
});

const requests = [];
const nativeFetch = globalThis.fetch;
globalThis.fetch = (url, options) => {
  if (String(url.url || url).startsWith("data:")) return nativeFetch(url, options);
  requests.push([String(url), options]);
  const clip = new URL(String(url), "http://x").searchParams.get("clip");
  const body = !clip ? base : clip === "X_Y_Exs" ? exs : clip === "X_Y_Normal_Attack_Start" ? other : null;
  if (!body) return Promise.resolve({ ok: false, status: 404, headers: { get: () => null } });
  return Promise.resolve({ ok: true, arrayBuffer: async () => body.slice(0) });
};
globalThis.ProgressEvent = class {
  constructor(type, options) {
    Object.assign(this, { type }, options);
  }
};
const renderer = {
  domElement: { remove() {} },
  extensions: { has: () => false },
  setPixelRatio() {},
  setSize() {},
  compile: () => new Set(),
  render() {},
  getContext: () => ({}),
  setAnimationLoop() {},
  dispose() {},
  forceContextLoss() {},
};

async function shown() {
  const view = createStillView({ renderer });
  Object.assign(view, { key: "x_y", source: "alt:x_z" });
  await view.load("/asset/chibi/x_y.glb?source=alt%3Ax_z");
  return view;
}

test("an added clip is fetched from the shown chibi's source, plays on the shown nodes and brings its gag face", async () => {
  const view = await shown();
  try {
    assert.deepEqual(view.json.extras.clips, ["X_Y_Formation_Idle", "X_Y_Exs", "X_Y_Normal_Attack_Start"]);
    assert.equal(view.actClips("exs", ["X_Y_Exs"]), false, "not there before it is added");
    requests.length = 0;
    await view.addClips(["X_Y_Formation_Idle", "X_Y_Exs"]);
    assert.deepEqual(
      requests.map(([url]) => url),
      ["/asset/chibi/x_y.glb?source=alt%3Ax_z&clip=X_Y_Exs"],
      "the GLB's own clip is not fetched",
    );
    const entry = view.named.get("X_Y_Exs");
    const hips = view.root.getObjectByName("Bip001");
    assert.deepEqual(
      entry.clip.tracks.map((t) => t.name),
      [`${hips.uuid}.quaternion`],
    );
    // the body is the shown GLB's renderer; the gag face came along, hidden at rest
    const body = view.root.getObjectByName("Body"),
      added = view.root.getObjectByName("Face01_added");
    assert.ok(added && added.parent === view.root.getObjectByName("Face01") && !added.visible);
    assert.ok(added.children[0].material.isShaderMaterial, "the game's shader on it");
    assert.deepEqual(
      entry.toggles.map((t) => t.node),
      [2, "added:3"],
    );
    assert.ok(view.actClips("exs", ["X_Y_Exs"], {}));
    view.mixer.update(0.5);
    view.applySwitches();
    assert.equal(body.visible, false);
    assert.equal(added.visible, true);
    assert.ok(Math.abs(hips.quaternion.y) > 0.2, "the hips turned by the added clip");
    assert.equal(view.current, "exs");
    // back to the idle: the switches rest again
    view.act("idle", { quiet: true });
    view.mixer.update(0.1);
    view.applySwitches();
    assert.equal(body.visible, true);
    assert.equal(added.visible, false);
    // added once: asking again fetches nothing
    requests.length = 0;
    await view.addClips(["X_Y_Exs"]);
    assert.equal(requests.length, 0);
  } finally {
    view.dispose();
  }
});

test("a clip of another hierarchy or one the route refuses is not added", async () => {
  const view = await shown();
  try {
    await assert.rejects(view.addClips(["X_Y_Normal_Attack_Start"]), /does not fit/);
    await assert.rejects(view.addClips(["X_Y_Missing"]), /HTTP 404/);
    assert.ok(!view.named.has("X_Y_Normal_Attack_Start") && !view.named.has("X_Y_Missing"));
    // an aborted fetch ends the load
    const abort = new AbortController();
    abort.abort();
    globalThis.fetch = (
      (inner) => (url, options) =>
        options?.signal?.aborted ? Promise.reject(new DOMException("Aborted", "AbortError")) : inner(url, options)
    )(globalThis.fetch);
    await assert.rejects(view.addClips(["X_Y_Exs"], { signal: abort.signal }), /Abort/);
  } finally {
    view.dispose();
  }
});

test("a chain plays each clip once in turn and holds or loops the last by its own flag", async () => {
  const view = createStillView({ renderer });
  const root = new THREE.Object3D();
  const clip = (name, loop) => ({
    clip: new THREE.AnimationClip(name, 1, [new THREE.NumberKeyframeTrack(".position[x]", [0, 1], [0, 1])]),
    loop,
  });
  view.mixer = new THREE.AnimationMixer(root);
  view.mixer.addEventListener("finished", (e) => view.finished(e.action));
  view.clips = new Map();
  view.named = new Map([
    ["A_Start", clip("A_Start", false)],
    ["A_Ing", clip("A_Ing", true)],
    ["A_End", clip("A_End", false)],
    ["D02", clip("D02", false)],
    ["R02", clip("R02", true)],
  ]);
  const run = (seconds) => {
    for (let t = 0; t < seconds; t += 0.05) view.mixer.update(0.05);
  };
  assert.ok(view.actClips("attack", ["A_Start", "A_Ing", "A_End"]));
  assert.equal(view.slot.clip.name, "A_Start");
  run(1.2);
  assert.equal(view.slot.clip.name, "A_Ing");
  assert.equal(view.action.loop, THREE.LoopOnce, "a clip with one after it plays once, loop flag or not");
  run(1.2);
  assert.equal(view.slot.clip.name, "A_End");
  run(2);
  assert.equal(view.slot.clip.name, "A_End");
  assert.ok(view.action.clampWhenFinished, "the last clip holds");
  // a second battle form's retreat plays as the tab's retreat: a cut to its crying clip, looping
  const before = view.action;
  assert.ok(view.actClips("retreat@2", ["R02"], { like: "retreat" }));
  assert.equal(view.slot.clip.name, "R02");
  assert.ok(!before.isRunning(), "a cut, no cross-fade");
  run(3);
  assert.equal(view.slot.clip.name, "R02");
  assert.equal(view.action.loop, THREE.LoopRepeat);
  // its knockdown plays as the tab's knockdown: the fall once, held
  assert.ok(view.actClips("knockdown@2", ["D02"], { like: "knockdown" }));
  run(2);
  assert.equal(view.slot.clip.name, "D02");
  assert.ok(view.action.clampWhenFinished);
});
