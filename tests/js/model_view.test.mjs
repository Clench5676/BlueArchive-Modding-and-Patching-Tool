// Tests for web/js/model.js's camera (the 3D tab opens at the formation screen's view of one character). Run by
// tests/test_chibi_materials.py with Node's test runner; "three" and "three/addons/" resolve to the bundled copy.
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
const { FORMATION_VIEW, bustDistance, carryZoom, frameDistance, stillCamera, stillCrop, storyAction, storyActions } =
  await import("../../src/bmpt/web/js/model.js");

const near = (a, b, eps = 1e-4) => assert.ok(Math.abs(a - b) <= eps, `${a} != ${b}`);
const TAN5 = Math.tan((5 * Math.PI) / 180);

test("the formation screen's camera, in the character's units", () => {
  // UIFormation: slot 630 NGUI units to the character's unit, 316.4 toward the camera, 222.6 + 56.25 below the
  // screen's centre; NGUI root 11.43 ahead at 2 / 1665 of a unit per NGUI unit; vertical field of view 10
  assert.equal(FORMATION_VIEW.fov, 10);
  near(FORMATION_VIEW.distance, 14.60164);
  near(FORMATION_VIEW.eye, 0.442619);
  // on a 1080-pixel screen the feet stand 187 px below its centre, where the screenshots show them (757 of 1125)
  const pxPerUnit = 1080 / (2 * FORMATION_VIEW.distance * TAN5);
  near(540 + FORMATION_VIEW.eye * pxPerUnit, (757 / 1125) * 1080, 1);
});

test("a chibi is framed at the formation screen's scale, a larger one from further back", () => {
  const frame = { x: 960, y: 540, w: 1920, h: 1080, floor: 1056 };
  // Koharu's box with its halo, around the orbit target (0.44 above the feet)
  const chibi = { min: { x: -0.5, y: -0.4426 }, max: { x: 0.45, y: 0.68 } };
  assert.equal(frameDistance(chibi, frame), FORMATION_VIEW.distance);
  // a box 3 units above the target: its top must stay 24 px below the host's top edge
  const tall = { min: { x: -0.5, y: -0.4426 }, max: { x: 0.5, y: 3 } };
  const d = frameDistance(tall, frame);
  near(d, (3 * 1080) / (2 * TAN5 * (540 - 24)));
  // beside the panel the feet stay above the nameplate (the frame's floor)
  const left = { x: 490, y: 500, w: 1000, h: 1080, floor: 700 };
  assert.equal(frameDistance(chibi, left), FORMATION_VIEW.distance);
  const low = { min: { x: -0.4, y: -1 }, max: { x: 0.4, y: 0.6 } };
  near(frameDistance(low, left), 1080 / (2 * TAN5 * 200));
  // and its sides inside the host's width
  const wide = { min: { x: -2, y: -0.4 }, max: { x: 0.5, y: 0.6 } };
  near(frameDistance(wide, left), (2 * 1080) / (2 * TAN5 * (490 - 24)));
});

const FULL = { x: 960, y: 540, w: 1920, h: 1080, floor: 1056 };
const LEFT = { x: 490, y: 500, w: 1000, h: 1080, floor: 700 };
// Koharu's box with its halo around the orbit target (0.44 above the feet), her face 0.34 above the target
const KOHARU = { min: { x: -0.5, y: -0.4426 }, max: { x: 0.45, y: 0.68 } };
const FACE = 0.34;
const pxPerUnit = (distance, frame) => frame.h / (2 * distance * TAN5);

test("the closest zoom is the bust, beside the panel and fullscreen", () => {
  for (const frame of [FULL, LEFT]) {
    const fit = frameDistance(KOHARU, frame);
    const bust = bustDistance(KOHARU, FACE, frame, fit);
    // the face on the frame point (the lifted target), the halo's top 24 px below the host's top edge
    near((KOHARU.max.y - FACE) * pxPerUnit(bust, frame), frame.y - 24, 1e-6);
    // the box then runs well past the host's height: head and shoulders, not the whole chibi
    assert.ok((KOHARU.max.y - KOHARU.min.y) * pxPerUnit(bust, frame) > 1.4 * frame.h);
    // 0.45 of the formation distance (the old limit) still showed her whole, shorter than the host
    assert.ok((KOHARU.max.y - KOHARU.min.y) * pxPerUnit(0.45 * fit, frame) < frame.h);
    assert.ok(bust < 0.35 * fit);
  }
  // a box whose top lies at or below the face still closes in; a very tall one does not stop further out than before
  const fit = FORMATION_VIEW.distance;
  near(bustDistance({ min: { y: -0.4 }, max: { y: 0.3 } }, FACE, FULL, fit), fit * 0.1);
  near(bustDistance({ min: { y: -0.4 }, max: { y: 5 } }, FACE, FULL, fit), fit * 0.45);
});

test("a change of framing keeps the zoom's place between the bust and the fit", () => {
  const range = (frame) => {
    const fit = frameDistance(KOHARU, frame);
    return { fit, min: bustDistance(KOHARU, FACE, frame, fit) };
  };
  const left = range(LEFT),
    full = range(FULL);
  assert.notEqual(left.min, full.min);
  near(carryZoom(left.min, left, full), full.min); // the bust stays the bust
  near(carryZoom(left.fit, left, full), full.fit);
  // halfway in the zoom's steps (each multiplies the distance): the same number of notches from the fit
  near(carryZoom(Math.sqrt(left.fit * left.min), left, full), Math.sqrt(full.fit * full.min));
  near(carryZoom(left.fit * 1.2, left, full), full.fit * 1.2); // a step back, as a multiple of the fit
  near(carryZoom(left.fit * 2, left, full), full.fit * 1.3);
  // a framing further back (a tall chibi): the same share of the way
  const far = { fit: 30, min: 6 };
  near(carryZoom(left.min, left, far), far.min);
  near(carryZoom(carryZoom(9, left, far), far, left), 9);
});

test("the switch card's still is the front view whatever the orbit and the zoom", () => {
  const target = new THREE.Vector3(0, FORMATION_VIEW.eye, 0),
    fit = FORMATION_VIEW.distance,
    aspect = 450 / 253;
  const live = (direction, distance) => {
    const c = new THREE.PerspectiveCamera(FORMATION_VIEW.fov, 1, fit / 50, fit * 20);
    c.position.copy(target).addScaledVector(direction.normalize(), distance);
    c.lookAt(target);
    c.setViewOffset(1000, 1080, 500 - LEFT.x, 540 - LEFT.y, 1000, 1080);
    c.updateMatrixWorld();
    return c;
  };
  const crop = (camera) =>
    stillCrop(stillCamera(camera, target, fit, LEFT, 1000, 1080), target, KOHARU, 2000, 2160, aspect);
  const home = crop(live(new THREE.Vector3(0, 0, 1), fit));
  // the upper body: half the box's height, at the formation scale (the box 1.12 units tall at 845 px per unit here)
  near(home.h, ((KOHARU.max.y - KOHARU.min.y) * pxPerUnit(fit, LEFT) * 2) / 2, 1e-3);
  near(home.w / home.h, aspect);
  // seen from straight above (and zoomed in, or from below and further back) the still is the same
  for (const [direction, distance] of [
    [new THREE.Vector3(0, 1, 1e-4), fit * 0.3],
    [new THREE.Vector3(0.3, -1, 0.2), fit * 1.3],
  ]) {
    const c = live(direction, distance);
    const r = crop(c);
    for (const k of ["x", "y", "w", "h"]) near(r[k], home[k], 1e-3);
    // cropped by the live camera itself, the box's height collapses (from above: to nothing)
    assert.ok(stillCrop(c, target, KOHARU, 2000, 2160, aspect).h < home.h / 2);
  }
});

test("a story model's cutscene clips are its actions, in order, each held on its last frame", () => {
  // Ayame's story forms have no idle; their slots Story_1.. come from their cutscenes
  const clips = new Map([
    ["Story_10", {}],
    ["Story_2", {}],
    ["Story_1", {}],
    ["Formation_Idle", {}],
  ]);
  assert.deepEqual(storyActions(clips), ["story1", "story2", "story10"]);
  assert.deepEqual(storyAction("story10"), { slots: ["Story_10"], hold: true });
  assert.equal(storyAction("idle"), null);
  assert.deepEqual(storyActions(new Map()), []);
});

test("the retreat cuts straight to the crying loop; the knockdown falls and stays down", async () => {
  const { ACTIONS, createStillView } = await import("../../src/bmpt/web/js/model.js");
  assert.deepEqual(Object.keys(ACTIONS), ["idle", "reaction", "walk", "victory", "pickup", "retreat", "knockdown"]);
  assert.deepEqual(ACTIONS.retreat, { slots: ["Vital_Retreat"], fade: 0, face: true });
  assert.deepEqual(ACTIONS.knockdown, { slots: ["Vital_Death"], hold: true });
  // a still view on a stand-in renderer, with one-track clips of the slots
  const renderer = { domElement: {}, setPixelRatio() {}, setSize() {} };
  const view = createStillView({ renderer });
  const root = new THREE.Object3D();
  root.name = "root";
  const clip = (name, length, loop) => ({
    clip: new THREE.AnimationClip(name, length, [new THREE.NumberKeyframeTrack(".position[x]", [0, length], [0, 1])]),
    loop,
  });
  view.mixer = new THREE.AnimationMixer(root);
  view.mixer.addEventListener("finished", (e) => view.finished(e.action));
  const run = (seconds) => {
    for (let t = 0; t < seconds; t += 0.05) view.mixer.update(0.05);
  };
  // the retreat needs its own clip alone, the knockdown the fall
  view.clips = new Map([
    ["Formation_Idle", clip("Formation_Idle", 2, true)],
    ["Vital_Death", clip("Vital_Death", 2, false)],
  ]);
  assert.deepEqual(view.actions(), ["idle", "knockdown"]);
  view.clips.set("Vital_Retreat", clip("Vital_Retreat", 1.6, true));
  assert.deepEqual(view.actions(), ["idle", "retreat", "knockdown"]);
  view.clips.delete("Vital_Death");
  assert.deepEqual(view.actions(), ["idle", "retreat"]);
  view.clips.set("Vital_Death", clip("Vital_Death", 2, false));

  // from the idle, a cut: the crying clip alone at full weight at once, no fall before it, looping
  assert.ok(view.act("idle", { quiet: true }));
  run(0.5);
  const idle = view.action;
  assert.ok(view.act("retreat", { quiet: true }));
  assert.equal(view.slot.clip.name, "Vital_Retreat");
  assert.ok(!idle.isRunning(), "the idle stops at the cut");
  assert.equal(view.action.getEffectiveWeight(), 1);
  assert.equal(view.action.loop, THREE.LoopRepeat);
  run(5);
  assert.equal(view.slot.clip.name, "Vital_Retreat");
  assert.ok(view.action.isRunning());
  // the knockdown plays the fall once and holds it
  view.act("knockdown", { quiet: true });
  run(5);
  assert.equal(view.slot.clip.name, "Vital_Death");
  assert.equal(view.action.loop, THREE.LoopOnce);
  assert.ok(view.action.clampWhenFinished);
});

// a stand-in biped facing +z (the front view's side), its left at +x, under a root turned by `yaw` radians
function biped(yaw = 0, names = {}) {
  const root = new THREE.Object3D();
  root.rotation.y = yaw;
  const hips = new THREE.Object3D();
  hips.name = "Bip001";
  hips.position.set(0, 0.34, 0);
  root.add(hips);
  const bone = (name, x, y) => {
    const o = new THREE.Object3D();
    o.name = names[name] ?? name;
    o.position.set(x, y, 0);
    hips.add(o);
    return o;
  };
  bone("Bip001 Head", 0, 0.29);
  bone("Bip001 L Thigh", 0.05, -0.03);
  bone("Bip001 R Thigh", -0.05, -0.03);
  bone("Bip001 L Clavicle", 0.02, 0.2);
  bone("Bip001 R Clavicle", -0.02, 0.2);
  root.updateMatrixWorld(true);
  return root;
}

test("the facing is read from the skeleton: trunk across the left-to-right line, whatever the student", async () => {
  const { facingBones, facingOf } = await import("../../src/bmpt/web/js/model.js");
  const facing = (root) =>
    facingOf(facingBones(root))
      ?.toArray()
      .map((v) => Math.round(v * 1000) / 1000 + 0);
  assert.deepEqual(facing(biped()), [0, 0, 1]);
  assert.deepEqual(facing(biped(Math.PI)), [0, 0, -1]);
  assert.deepEqual(facing(biped(Math.PI / 2)), [1, 0, 0]);
  // a turn of the trunk forward (a bow) leaves the facing level
  const bowed = biped();
  bowed.children[0].rotation.x = 0.6;
  bowed.updateMatrixWorld(true);
  assert.deepEqual(facing(bowed), [0, 0, 1]);
  // another rig's names: left/right shoulders and up-legs, a pelvis and a head
  const other = biped(Math.PI, {
    Bip001: "pelvis",
    "Bip001 Head": "head",
    "Bip001 L Thigh": "LeftUpLeg",
    "Bip001 R Thigh": "RightUpLeg",
    "Bip001 L Clavicle": "Left_Shoulder",
    "Bip001 R Clavicle": "Right_Shoulder",
  });
  assert.deepEqual(facing(other), [0, 0, -1]);
  // one pair is enough; none, or no head, gives no facing
  assert.deepEqual(facing(biped(0, { "Bip001 L Clavicle": "x", "Bip001 R Clavicle": "y" })), [0, 0, 1]);
  assert.equal(facingBones(biped(0, { "Bip001 L Thigh": "x", "Bip001 L Clavicle": "y" })), null);
  assert.equal(facingBones(biped(0, { "Bip001 Head": "x" })), null);
  assert.equal(facingOf(null), null);
});

test("the face's facing: the head's forward read on the formation idle, carried by the head's turn", async () => {
  const { facingBones, facingOf, headFront } = await import("../../src/bmpt/web/js/model.js");
  const round = (v) => v?.toArray().map((x) => Math.round(x * 1000) / 1000 + 0);
  // the idle: the body turned 0.5 rad off the front view, the head turned back to look into it
  const root = biped(0.5);
  const head = root.getObjectByName("Bip001 Head");
  head.rotation.y = -0.5;
  root.updateMatrixWorld(true);
  const bones = facingBones(root),
    front = headFront(bones);
  assert.deepEqual(round(facingOf(bones, new THREE.Vector3(), front)), [0, 0, 1]);
  assert.notDeepEqual(round(facingOf(bones)), [0, 0, 1], "the body alone looks aside");
  // the retreat turns the whole character round: the face's facing follows the head
  root.rotation.y = 0.5 + Math.PI;
  root.updateMatrixWorld(true);
  assert.deepEqual(round(facingOf(bones, new THREE.Vector3(), front)), [0, 0, -1]);
  // a head looking straight down gives no level facing: the body's
  head.rotation.set(Math.PI / 2, 0, 0);
  root.rotation.y = 0;
  root.updateMatrixWorld(true);
  assert.deepEqual(round(facingOf(bones, new THREE.Vector3(), new THREE.Vector3(0, 0, 1))), round(facingOf(bones)));
  assert.equal(headFront(null), null);
});

test("the front view keeps the camera's height angle and turns the yaw to the facing", async () => {
  const { frontView } = await import("../../src/bmpt/web/js/model.js");
  const round = (v) => v.toArray().map((x) => Math.round(x * 1000) / 1000 + 0);
  // biome-ignore lint/suspicious/noApproximativeNumericConstant: the expected value is rounded to three places
  assert.deepEqual(round(frontView(new THREE.Vector3(1, 0, 0), new THREE.Vector3(0, 2, 2))), [0.707, 0.707, 0]);
  assert.deepEqual(round(frontView(new THREE.Vector3(0, 0, -1), new THREE.Vector3(3, 0, 0))), [0, 0, -1]);
});

test("only the retreat turns the view to the face, from its first posed frame, and keeps the zoom", async () => {
  const { ACTIONS, createStillView } = await import("../../src/bmpt/web/js/model.js");
  assert.deepEqual(
    Object.keys(ACTIONS).filter((name) => ACTIONS[name].face),
    ["retreat"],
  );
  const renderer = { domElement: {}, setPixelRatio() {}, setSize() {} };
  const view = createStillView({ renderer });
  const clip = (name, loop) => ({
    clip: new THREE.AnimationClip(name, 1, [new THREE.NumberKeyframeTrack(".position[x]", [0, 1], [0, 0])]),
    loop,
  });
  view.root = biped(Math.PI); // the retreat's pose: turned away from the front view
  view.mixer = new THREE.AnimationMixer(view.root);
  view.clips = new Map([
    ["Formation_Idle", clip("Formation_Idle", true)],
    ["Vital_Retreat", clip("Vital_Retreat", true)],
    ["Vital_Death", clip("Vital_Death", false)],
  ]);
  view.named = new Map([["R02", clip("R02", true)]]);
  view.fit = 3;
  view.limits = { min: 1, max: 4 };
  // the head's forward is read on the formation idle's posed frame, once per model
  view.act("idle", { quiet: true });
  assert.equal(view.headFront, null);
  view.readHeadFront();
  assert.ok(view.headFront?.isVector3);
  view.headFront = undefined;
  view.camera.position.set(0, 0, 2.5);
  for (const name of ["idle", "knockdown"]) {
    view.act(name, { quiet: true });
    assert.equal(view.facing, false, name);
  }
  view.act("retreat", { quiet: true });
  assert.equal(view.facing, true, "turned on the next frame, once the clip has posed the skeleton");
  assert.ok(view.turnToFace());
  assert.equal(view.facing, false);
  assert.deepEqual(
    view.tween.dir1.toArray().map((v) => Math.round(v * 1000) / 1000 + 0),
    [0, 0, -1],
  );
  near(view.tween.dist1, 2.5);
  // a second battle form's retreat plays as the tab's retreat, and turns too; a pick of another action cancels a turn
  // not yet made
  view.actClips("retreat@2", ["R02"], { like: "retreat" });
  assert.equal(view.facing, true);
  view.act("idle", { quiet: true });
  assert.equal(view.facing, false);
  // with the head's forward read: the face's facing
  view.headFront = new THREE.Vector3(0, 0, 1);
  view.act("retreat", { quiet: true });
  assert.ok(view.turnToFace());
  assert.deepEqual(
    view.tween.dir1.toArray().map((v) => Math.round(v * 1000) / 1000 + 0),
    [0, 0, -1],
  );
  // a model without the formation idle (a cafe idle) has none: the body's facing
  view.clips.delete("Formation_Idle");
  view.clips.set("Cafe_Idle", clip("Cafe_Idle", true));
  view.headFront = undefined;
  view.act("idle", { quiet: true });
  view.readHeadFront();
  assert.equal(view.headFront, false);
  // a skeleton without the bones it reads: no turn
  view.root = new THREE.Object3D();
  view.act("retreat", { quiet: true });
  assert.equal(view.turnToFace(), false);
});

test("the view follows the hips across the floor, so any clip plays in place", async () => {
  const { anchorNode, followOffset } = await import("../../src/bmpt/web/js/model.js");
  const root = new THREE.Group();
  const boneRoot = new THREE.Bone();
  boneRoot.name = "bone_root";
  const hips = new THREE.Bone();
  hips.name = "Bip001";
  const pelvis = new THREE.Bone();
  pelvis.name = "Bip001 Pelvis";
  root.add(boneRoot);
  boneRoot.add(hips);
  hips.add(pelvis);
  assert.equal(anchorNode(root), hips);
  // without Bip001, a pelvis or hips bone; without either, none
  const other = new THREE.Group(),
    p = new THREE.Bone();
  p.name = "Character_Hips";
  other.add(p);
  assert.equal(anchorNode(other), p);
  assert.equal(anchorNode(new THREE.Group()), null);
  assert.equal(anchorNode(null), null);
  // a clip that walks the root 2 units forward and 0.5 aside, and bobs 0.1 up: the target moves along the floor only
  const rest = hips.getWorldPosition(new THREE.Vector3());
  boneRoot.position.set(0.5, 0.1, 2);
  root.updateMatrixWorld(true);
  const off = followOffset(hips.getWorldPosition(new THREE.Vector3()), rest);
  near(off.x, 0.5);
  near(off.y, 0);
  near(off.z, 2);
});

test("the target stands over the prefab's root, or over the hips of a model placed off its root", async () => {
  const { standPoint } = await import("../../src/bmpt/web/js/model.js");
  const origin = { x: 0, y: 0, z: 0 };
  // a chibi's idle hips sway a few centimetres from its root: the formation screen's place is kept
  assert.deepEqual(standPoint(origin, { x: -0.03, y: 0.38, z: 0.05 }), { x: 0, z: 0 });
  assert.deepEqual(standPoint(origin, null), { x: 0, z: 0 });
  // Arona's cutscene model stands 1.18 across and 0.84 deep from its root
  assert.deepEqual(standPoint(origin, { x: 1.18, y: 0.96, z: 0.84 }), { x: 1.18, z: 0.84 });
});
