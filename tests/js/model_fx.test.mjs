// Tests for web/js/chibi/chibi-fx.js (the halo's particles in the chibi view): the memorial lobby programs' GLSL
// rewritten for three.js, the blend, and each system placed by its glTF node (glTF mirrors Unity on X). Run by
// tests/test_particles_player.py with Node's test runner; "three" resolves to the bundled copy.
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
const { blendOf, createChibiFx, fragment3d, rows, vertex3d, viewOf } = await import(
  "../../src/bmpt/web/js/chibi/chibi-fx.js"
);
const { PROGRAMS } = await import("../../src/bmpt/web/js/memorial-lobby/particles-gl.js");
const { IDENTITY, WORLD } = await import("../../src/bmpt/web/js/memorial-lobby/particles.js");

const near = (a, b, eps = 1e-5, what = "") => assert.ok(Math.abs(a - b) <= eps, `${what} ${a} != ${b}`);

test("every memorial lobby program, rewritten for three.js: 3D positions, V flipped, sRGB at the end", () => {
  for (const kind of Object.keys(PROGRAMS)) {
    const v = vertex3d(kind),
      f = fragment3d(kind);
    assert.ok(!v.includes("#version") && !f.includes("#version"), kind); // three.js adds it (GLSL3)
    assert.ok(
      v.includes("in vec3 position;") && v.includes("projectionMatrix * modelViewMatrix * vec4(position, 1.0)"),
      kind,
    );
    assert.ok(!/\ba_pos\b|\bu_proj\b/.test(v), kind);
    assert.ok(
      f.includes("#define texture fxTexture") && f.includes("void fxMain() {") && f.includes("fxSrgb(o.rgb"),
      kind,
    );
    assert.equal(f.match(/void main\(\)/g).length, 1, kind);
  }
});

test("the blend: the game's for colour, coverage unless the light adds", () => {
  // Step (SrcAlpha OneMinusSrcAlpha), Mask (One OneMinusSrcAlpha: premultiplied), Additive (One One)
  assert.deepEqual(blendOf({ src: 5, dst: 10 }), {
    blendSrc: THREE.SrcAlphaFactor,
    blendDst: THREE.OneMinusSrcAlphaFactor,
    blendSrcAlpha: THREE.OneFactor,
    blendDstAlpha: THREE.OneMinusSrcAlphaFactor,
    premul: false,
  });
  assert.equal(blendOf({ src: 1, dst: 10 }).premul, true);
  assert.deepEqual(blendOf({ src: 1, dst: 1 }), {
    blendSrc: THREE.OneFactor,
    blendDst: THREE.OneFactor,
    blendSrcAlpha: THREE.ZeroFactor,
    blendDstAlpha: THREE.OneFactor,
    premul: false,
  });
});

test("rows: a three.js matrix as particles.js reads it", () => {
  const m = new THREE.Matrix4().makeTranslation(1, 2, 3).multiply(new THREE.Matrix4().makeScale(4, 5, 6));
  assert.deepEqual(rows(m), [4, 0, 0, 1, 0, 5, 0, 2, 0, 0, 6, 3]);
});

// one system as chibi.py exports it: one particle at its node, still, a quad of size 0.1 (Unity units)
function halo(over = {}) {
  return {
    systems: [
      {
        name: "dot",
        node: 2,
        material: "m",
        queue: 3000,
        order: 0,
        ls: [1, 1, 1],
        playOnAwake: 1,
        main: {
          duration: 1,
          loop: 1,
          prewarm: 0,
          delay: 0,
          speedScale: 1,
          space: 0,
          scaling: 0,
          life: 10,
          speed: 0,
          size: [0.1],
          rotation: [0],
          flipRotation: 0,
          color: { col: [1, 1, 1, 1] },
          gravity: 0,
          max: 4,
        },
        emission: { rate: 0, dist: 0, bursts: [{ t: 0, n: 1, cycles: 1, interval: 0.01, p: 1 }] },
        shape: null,
        modules: {},
        renderer: {
          mode: 0,
          align: 0,
          sort: 0,
          minSize: 0,
          maxSize: 10,
          lengthScale: 2,
          velocityScale: 0,
          pivot: [0, 0, 0],
          flip: [0, 0, 0],
          streams: { uv: [0, 2] },
        },
        ...over,
      },
    ],
    materials: {
      m: {
        shader: "DSFX/FX_SHADER_Step_0",
        program: "step",
        src: 5,
        dst: 10,
        queue: 3000,
        tex: { _Tex_Main: { default: "white", st: [1, 1, 0, 0] }, _Tex_Mask: { default: "white", st: [1, 1, 0, 0] } },
        floats: { _Multiply: 1 },
        colors: { _Color: [1, 1, 1, 1] },
      },
    },
    textures: {},
    meshes: {},
  };
}

function scene() {
  // the glTF scene, moved and turned as a view may place it, a halo root and the system's node below it
  const root = new THREE.Group();
  root.position.set(0.5, -0.2, 0.3);
  root.rotation.y = 0.7;
  const haloRoot = new THREE.Object3D(),
    node = new THREE.Object3D();
  haloRoot.position.set(0.1, 1.2, 0);
  node.position.set(0.05, 0.1, -0.02);
  node.rotation.z = 0.3;
  root.add(haloRoot);
  haloRoot.add(node);
  const camera = new THREE.PerspectiveCamera(10, 1, 0.01, 100);
  camera.position.set(0.5, 1, 15);
  camera.lookAt(0.5, 1, 0);
  return { root, nodes: [root, haloRoot, node], node, camera };
}

function drawn(fx) {
  const mesh = fx.group.children[0],
    attr = mesh.geometry.getAttribute("position");
  const corners = [];
  for (let i = 0; i < mesh.geometry.drawRange.count; i++)
    corners.push(new THREE.Vector3().fromBufferAttribute(attr, i).applyMatrix4(mesh.matrixWorld));
  return corners;
}

test("a system plays where its node is, the quad facing the camera at its Unity size", () => {
  const { root, nodes, node, camera } = scene();
  const fx = createChibiFx(halo(), { nodes, textures: [], root });
  fx.update(0.016, camera);
  const corners = drawn(fx);
  assert.equal(corners.length, 6);
  const centre = corners.reduce((a, c) => a.add(c), new THREE.Vector3()).divideScalar(6);
  const at = node.getWorldPosition(new THREE.Vector3());
  near(centre.x, at.x, 1e-5, "x");
  near(centre.y, at.y, 1e-5, "y");
  near(centre.z, at.z, 1e-5, "z");
  // facing the camera: every corner at the centre's depth; 0.1 across
  const forward = camera.getWorldDirection(new THREE.Vector3());
  for (const c of corners) near(c.clone().sub(centre).dot(forward), 0, 1e-6, "depth");
  const xs = corners.map((c) => c.x);
  near(Math.max(...xs) - Math.min(...xs), 0.1, 1e-4, "width");
  // the node moves (the halo follower): the system's place follows on the next frame, its particle with it in local
  // space
  node.position.x += 0.2;
  fx.update(0.016, camera);
  const moved = drawn(fx)
    .reduce((a, c) => a.add(c), new THREE.Vector3())
    .divideScalar(6);
  near(moved.x, node.getWorldPosition(new THREE.Vector3()).x, 1e-5, "followed");
  fx.dispose();
});

test("the simulation runs in Unity's space: a system's +x is glTF's -x, in memorial lobby units", () => {
  const { root, nodes, node, camera } = scene();
  const fx = createChibiFx(
    halo({
      main: { ...halo().systems[0].main, speed: 1 },
      shape: {
        type: 4,
        angle: 0,
        radius: 0,
        length: 0,
        thickness: 0,
        arc: 360,
        pos: [0, 0, 0],
        rot: [0, 90, 0],
        scale: [1, 1, 1],
        randomDir: 0,
        spherical: 0,
        randomPos: 0,
        align: 0,
      },
    }),
    { nodes, textures: [], root },
  );
  fx.update(0.016, camera);
  const s = fx.effects().systems[0];
  // the system's matrix: Unity units to memorial lobby units (WORLD), the node's place mirrored on X
  const unity = new THREE.Matrix4().makeScale(-1, 1, 1);
  const want = new THREE.Matrix4()
    .makeScale(WORLD, WORLD, WORLD)
    .multiply(unity)
    .multiply(root.matrixWorld.clone().invert())
    .multiply(node.matrixWorld)
    .multiply(unity);
  rows(want).forEach((v, i) => near(s.static[i], v, 1e-6, `static ${i}`));
  assert.notDeepEqual(s.static, IDENTITY);
  // a cone turned 90 degrees about Unity's y emits along Unity's +x: glTF's -x in the node's space
  for (let i = 0; i < 30; i++) fx.update(0.03, camera);
  const p = drawn(fx)
    .reduce((a, c) => a.add(c), new THREE.Vector3())
    .divideScalar(6);
  const local = node.worldToLocal(p.clone());
  assert.ok(local.x < -0.5 && Math.abs(local.y) < 1e-4 && Math.abs(local.z) < 1e-4, JSON.stringify(local));
  fx.dispose();
});

test("the camera in the simulation's space; a still's effects are the same every time", () => {
  const { root, nodes, camera } = scene();
  const fx = createChibiFx(
    halo({
      emission: { rate: 3, dist: 0, bursts: [] },
      shape: {
        type: 0,
        radius: 0.2,
        thickness: 1,
        arc: 360,
        pos: [0, 0, 0],
        rot: [0, 0, 0],
        scale: [1, 1, 1],
        randomDir: 0,
        spherical: 0,
        randomPos: 0,
        align: 0,
      },
    }),
    { nodes, textures: [], root },
  );
  root.updateMatrixWorld();
  const toSim = new THREE.Matrix4()
    .makeScale(WORLD, WORLD, WORLD)
    .multiply(new THREE.Matrix4().makeScale(-1, 1, 1))
    .multiply(root.matrixWorld.clone().invert());
  const view = viewOf(camera, toSim);
  // unit axes, Unity's handedness (x cross y is z), the eye where the camera is
  for (const a of [view.x, view.y, view.z]) near(Math.hypot(...a), 1, 1e-9);
  const c = new THREE.Vector3(...view.x).cross(new THREE.Vector3(...view.y));
  near(c.dot(new THREE.Vector3(...view.z)), 1, 1e-9, "handedness");
  const eye = camera.position.clone().applyMatrix4(toSim);
  view.eye.forEach((v, i) => near(v, eye.getComponent(i), 1e-6, "eye"));
  near(view.k, 2 * Math.tan((5 * Math.PI) / 180), 1e-12, "k");
  fx.settle(camera);
  const first = drawn(fx).map((v) => v.toArray());
  fx.update(0.5, camera);
  fx.settle(camera);
  assert.ok(first.length > 6);
  assert.deepEqual(
    drawn(fx).map((v) => v.toArray()),
    first,
  );
  fx.dispose();
});

test("no system with a node, or one whose texture could not be read: nothing, or that system left out", () => {
  const { root, nodes } = scene();
  assert.equal(createChibiFx(halo({ node: 9 }), { nodes, textures: [], root }), null);
  assert.equal(createChibiFx(null, { nodes, textures: [], root }), null);
  const fx = halo();
  fx.materials.m.tex._Tex_Main = { id: "missing", st: [1, 1, 0, 0] };
  const made = createChibiFx(fx, { nodes, textures: [], root });
  assert.equal(made.stats().systems, 0);
  made.dispose();
});
