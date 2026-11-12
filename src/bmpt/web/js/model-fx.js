// The halo's particle effects in the 3D model view (model.js): Tsurugi's drops, Mika's stars and rings. The GLB's root
// extras carry them as gameassets/model.py exports them (haloFx: the lobby player's systems, materials, meshes and
// textures, each system on its glTF node, each texture a glTF texture). The lobbies' simulation (particles.js
// Effects) runs them in the model's Unity space, scaled to lobby units, each system placed by its node's world matrix
// every frame (the halo follower moves them); geometry3d() builds their triangles for the view's camera and three.js
// draws them with the lobbies' ports of the DSFX programs (particles-gl.js), after the model's transparents of a lower
// queue, depth tested but not written. The model view draws into the canvas in sRGB, not in linear light like the
// lobbies: each program's colour is encoded at its end, so glows blend in sRGB (a little brighter where they overlap).
import * as THREE from "three";
import { Effects, IDENTITY, STRIDE3, WORLD, geometry3d } from "./particles.js";
import { PROGRAMS, fragmentSource, vertexSource } from "./particles-gl.js";
import { toLinear } from "./touch-fx.js";

// glTF mirrors Unity on X (model.py)
const MIRROR = new THREE.Matrix4().makeScale(-1, 1, 1);
// UnityEngine.Rendering.BlendMode -> three.js factors
const FACTORS = [THREE.ZeroFactor, THREE.OneFactor, THREE.DstColorFactor, THREE.SrcColorFactor,
  THREE.OneMinusDstColorFactor, THREE.SrcAlphaFactor, THREE.OneMinusSrcColorFactor, THREE.DstAlphaFactor,
  THREE.OneMinusDstAlphaFactor, THREE.SrcAlphaSaturateFactor, THREE.OneMinusSrcAlphaFactor];
// Unity's built-in default textures, for a texture property a material leaves empty
const DEFAULT_TEXEL = { white: [255, 255, 255, 255], black: [0, 0, 0, 0], gray: [128, 128, 128, 128], grey: [128, 128, 128, 128],
  bump: [128, 128, 255, 128], red: [255, 0, 0, 0] };
// the vertex layout geometry3d() writes: [attribute, components, offset]
const LAYOUT = [["position", 3, 0], ["a_t0", 4, 3], ["a_t1", 4, 7], ["a_t2", 4, 11], ["a_col", 4, 15], ["a_n", 3, 19]];
const STILL_SECONDS = 2; // a still shows the effects this far in (after their prewarm)
const STILL_STEP = 1 / 30;

function swap(src, from, to) {
  if (!src.includes(from)) throw new Error(`model-fx: shader source lacks ${from}`);
  return src.replace(from, to);
}

// a lobby program's vertex GLSL for three.js: 3D positions through the object's model-view and the camera's projection
export function vertex3d(kind) {
  let src = swap(vertexSource(kind), "#version 300 es\n", "");
  src = swap(src, "in vec2 a_pos;", "in vec3 position;");
  src = swap(src, "uniform mat4 u_proj;", "uniform mat4 projectionMatrix;\nuniform mat4 modelViewMatrix;");
  return swap(src, "u_proj * vec4(a_pos, 0.0, 1.0)", "projectionMatrix * modelViewMatrix * vec4(position, 1.0)");
}

// a lobby program's fragment GLSL for three.js: the textures read with V flipped (glTF images are stored top row first,
// Unity's UV origin is the bottom-left corner), the colour encoded to sRGB at the end (premultiplied: u_premul 1, the
// colour divided by the alpha first)
export function fragment3d(kind) {
  let src = swap(fragmentSource(kind), "#version 300 es\n", "");
  src = swap(src, "out vec4 o;\n", `out vec4 o;
uniform float u_premul;
vec4 fxTexture(sampler2D t, vec2 uv) { return texture(t, vec2(uv.x, 1.0 - uv.y)); }
#define texture fxTexture
vec3 fxSrgb(vec3 c) {
  c = max(c, 0.0);
  return mix(c * 12.92, 1.055 * pow(c, vec3(1.0 / 2.4)) - 0.055, step(vec3(0.0031308), c));
}
`);
  src = swap(src, "void main() {", "void fxMain() {");
  return `${src}
void main() {
  fxMain();
  if (u_premul > 0.5 && o.a > 1e-4) o.rgb = fxSrgb(o.rgb / o.a) * o.a;
  else o.rgb = fxSrgb(o.rgb);
}`;
}

// the blend of a material (its BlendMode src and dst): the game's for colour; coverage only where the game's blend
// covers what is behind (additive light adds none, as the lobbies' player does)
export function blendOf(mat) {
  const src = FACTORS[mat.src] ?? THREE.OneFactor, dst = FACTORS[mat.dst] ?? THREE.ZeroFactor;
  const additive = dst === THREE.OneFactor;
  return { blendSrc: src, blendDst: dst, blendSrcAlpha: additive ? THREE.ZeroFactor : THREE.OneFactor,
    blendDstAlpha: additive ? THREE.OneFactor : THREE.OneMinusSrcAlphaFactor, premul: src === THREE.OneFactor && !additive };
}

// three.js matrix (column-major 4 x 4) <-> particles.js matrix (row-major 3 x 4)
export const rows = (m) => {
  const e = m.elements;
  return [e[0], e[4], e[8], e[12], e[1], e[5], e[9], e[13], e[2], e[6], e[10], e[14]];
};

// The camera in the simulation's space (particles.js geometry3d's view): toSim maps three.js world space there.
export function viewOf(camera, toSim) {
  camera.updateMatrixWorld();
  const e = camera.matrixWorld.elements;
  const dir = (x, y, z) => new THREE.Vector3(x, y, z).transformDirection(toSim).toArray();
  // transformDirection normalizes: the axes stay unit vectors in lobby units
  const eye = new THREE.Vector3(e[12], e[13], e[14]).applyMatrix4(toSim).toArray();
  const z = dir(-e[8], -e[9], -e[10]);
  const k = (2 * Math.tan(THREE.MathUtils.degToRad(camera.fov ?? 50) / 2)) / (camera.zoom || 1);
  return { x: dir(e[0], e[1], e[2]), y: dir(e[4], e[5], e[6]), z, eye, k };
}

// The effects of one model: fx is the GLB's haloFx, nodes its glTF nodes (three.js objects by index), textures its
// glTF textures (by index), root the glTF scene. group goes into the view's scene; update() advances and draws them,
// settle() puts them at a fixed moment for a still, dispose() frees what they made (the glTF textures are the view's).
export function createModelFx(fx, { nodes, textures, root }) {
  const defs = (fx?.systems || []).filter((d) => nodes[d.node]);
  if (!defs.length) return null;
  // placed by their nodes every frame: their static place is only where the simulation starts
  const data = { systems: defs.map((d) => ({ ...d, static: IDENTITY })), toggles: [] };
  const materials = fx.materials || {}, meshes = fx.meshes || {}, texmeta = fx.textures || {};
  const group = new THREE.Group();
  group.name = "halo_fx";
  const defaults = new Map();
  const defaultTexture = (name) => {
    if (!defaults.has(name)) {
      const t = new THREE.DataTexture(new Uint8Array(DEFAULT_TEXEL[name] || DEFAULT_TEXEL.white), 1, 1);
      t.needsUpdate = true;
      defaults.set(name, t);
    }
    return defaults.get(name);
  };

  // a system's mesh: its program, the material's uniforms and blend, room for its max particles
  function meshOf(def) {
    const mat = materials[def.material], program = mat && PROGRAMS[mat.program];
    if (!program) return null;
    const uniforms = { _TimeY: { value: 0 }, u_premul: { value: 0 } };
    for (const name of program.tex) {
      const meta = mat.tex?.[name] || { default: mat.defaults?.[name] || "white" };
      const tex = meta.id ? textures[texmeta[meta.id]?.texture] : defaultTexture(meta.default || "white");
      if (!tex) return null; // a texture that could not be read: the system is not drawn
      const st = meta.st || [1, 1, 0, 0];
      uniforms[name] = { value: tex };
      uniforms[`${name}_ST`] = { value: new THREE.Vector4(st[0], st[1], st[2], st[3]) };
    }
    for (const f of program.floats) uniforms[f] = { value: mat.floats?.[f] ?? 0 };
    // Color properties reach the shader in linear light (HDR above 1 by a 2.2 power), alpha as stored
    for (const c of program.colors) {
      const v = mat.colors?.[c] || [1, 1, 1, 1];
      uniforms[c] = { value: new THREE.Vector4(toLinear(v[0]), toLinear(v[1]), toLinear(v[2]), v[3]) };
    }
    for (const c of program.vectors || []) {
      const v = mat.colors?.[c] || [1, 1, 0, 0];
      uniforms[c] = { value: new THREE.Vector4(v[0], v[1], v[2], v[3]) };
    }
    const { premul, ...blend } = blendOf(mat);
    uniforms.u_premul.value = premul ? 1 : 0;
    const material = new THREE.RawShaderMaterial({
      name: `fx_${def.name}`, glslVersion: THREE.GLSL3, vertexShader: vertex3d(mat.program), fragmentShader: fragment3d(mat.program),
      uniforms, transparent: true, depthWrite: false, depthTest: true, side: THREE.DoubleSide,
      blending: THREE.CustomBlending, blendEquation: THREE.AddEquation, ...blend,
    });
    const r = def.renderer, perParticle = r.mode === 4
      ? Math.max(...(r.meshes || [r.mesh]).map((id) => meshes[id]?.i.length || 0)) : 6;
    const buffer = new THREE.InterleavedBuffer(new Float32Array(Math.max(1, (def.main.max || 1) * perParticle * STRIDE3)), STRIDE3);
    buffer.setUsage(THREE.DynamicDrawUsage);
    const geometry = new THREE.BufferGeometry();
    for (const [name, n, offset] of LAYOUT) geometry.setAttribute(name, new THREE.InterleavedBufferAttribute(buffer, n, offset));
    geometry.setDrawRange(0, 0);
    const mesh = new THREE.Mesh(geometry, material);
    mesh.name = def.name;
    mesh.frustumCulled = false;
    mesh.matrixAutoUpdate = false;
    // the game's render queue, then the order in layer (model.js puts each mesh at its material's queue)
    mesh.renderOrder = (def.queue ?? 3000) + (def.order || 0) * 1e-3;
    group.add(mesh);
    return { mesh, buffer, uniforms };
  }

  const draws = defs.map(meshOf);
  let effects = new Effects(data);
  const toThree = new THREE.Matrix4(), toSim = new THREE.Matrix4(), m = new THREE.Matrix4();

  // the simulation's space: the model's Unity space in lobby units, mirrored back to glTF and placed by the root
  function place() {
    root.updateMatrixWorld();
    toThree.copy(root.matrixWorld).multiply(MIRROR).multiply(m.makeScale(1 / WORLD, 1 / WORLD, 1 / WORLD));
    toSim.copy(toThree).invert();
    effects.systems.forEach((s) => {
      // a system's own Unity space (its node's, mirrored) in the simulation's space
      s.static = rows(m.copy(toSim).multiply(nodes[s.def.node].matrixWorld).multiply(MIRROR));
    });
  }

  function draw(camera) {
    const view = viewOf(camera, toSim);
    effects.systems.forEach((s, i) => {
      const d = draws[i];
      if (!d) return;
      d.mesh.matrix.copy(toThree);
      d.mesh.matrixWorld.copy(toThree);
      d.uniforms._TimeY.value = effects.time;
      const geo = geometry3d(effects, s, view, { meshes, linear: true });
      const count = Math.min(geo.count, d.buffer.count);
      d.buffer.array.set(geo.data.subarray(0, count * STRIDE3));
      d.buffer.needsUpdate = true;
      d.mesh.geometry.setDrawRange(0, count);
    });
  }

  return {
    group,
    effects: () => effects,
    // advance by dt seconds and build this frame's triangles for the camera (after the model's matrices are updated)
    update(dt, camera) {
      place();
      effects.update(dt);
      draw(camera);
    },
    // the effects at a fixed moment, the same every time: a still is the same image whenever it is taken
    settle(camera) {
      let seed = 1;
      effects = new Effects(data, { seed: 0x5eed, random: () => ((seed = Math.imul(seed, 48271) % 2147483647) / 2147483647) });
      place();
      effects.update(0);
      for (let t = 0; t < STILL_SECONDS - 1e-9; t += STILL_STEP) effects.update(STILL_STEP);
      draw(camera);
    },
    // for checks: live particles and drawn systems
    stats: () => ({ particles: effects.count(), systems: draws.filter(Boolean).length }),
    dispose() {
      for (const d of draws) if (d) { d.mesh.geometry.dispose(); d.mesh.material.dispose(); }
      for (const t of defaults.values()) t.dispose();
      group.removeFromParent();
    },
  };
}
