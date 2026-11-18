// The memorial lobbies' particle geometry: the vertex data particles-gl.js draws, built from the systems
// particles.js simulates (billboards, meshes, trails, sprites; flat for the memorial lobby view, 3D for the chibis).
import { toLinear } from "../app/touch-fx.js";
import {
  rnd,
  R,
  colLen,
  rotationOf,
  VIEW_H,
  apply,
  mul,
  IDENTITY,
  euler,
  applyDir,
  norm,
  colorAt,
  value,
} from "./particles.js";

// --- geometry -------------------------------------------------------------------------------------------------------

// floats per vertex: position (x, y in memorial lobby units), TEXCOORD0..2 (uv, uv2 and custom data where the
// renderer's streams put them), colour, normal (world axes: a mesh's own turned with the particle, a billboard's to the
// camera)
export const STRIDE = 2 + 12 + 4 + 3;

/**
 * The particle colour as the vertex stream carries it: clamped and 8-bit like Unity's Color32, then (in linear
 * light) decoded from sRGB; alpha as it is.
 * @param {number[]} c The colour [r, g, b, a].
 * @param {boolean} linear Whether the memorial lobby draws in linear light.
 */
export function vertexColor(c, linear) {
  const q = (x) => Math.round(Math.min(1, Math.max(0, x)) * 255) / 255;
  const out = [q(c[0]), q(c[1]), q(c[2]), q(c[3])];
  if (linear) for (let i = 0; i < 3; i++) out[i] = toLinear(out[i]);
  return out;
}

// TEXCOORD0..2 of a particle from the renderer's streams (custom data; the UV slots are filled per vertex)
const TC = new Float32Array(12);
function texcoords(streams, custom) {
  const t = TC.fill(0);
  const put = (at, vals) => {
    if (at) for (let k = 0; k < at[1] && at[0] + k < 12; k++) t[at[0] + k] = vals[k] ?? 0;
  };
  put(streams.c1, custom[0]);
  put(streams.c2, custom[1]);
  return t;
}

// a billboard's normal: towards the camera, which looks along +z
const FACING = [0, 0, -1];
// quad corners [x, y, u, v] in the triangle order 0 1 2, 2 3 0
const QUAD = [
  [-0.5, -0.5, 0, 0],
  [0.5, -0.5, 1, 0],
  [0.5, 0.5, 1, 1],
  [0.5, 0.5, 1, 1],
  [-0.5, 0.5, 0, 1],
  [-0.5, -0.5, 0, 0],
];

/**
 * The index of the mesh a particle draws among a mesh renderer's meshes (particles.py _mesh_choice): Unity
 * picks one per particle, uniformly at random or by the meshes' weights.
 */
export function meshIndex(rend, seed) {
  const n = rend.meshes?.length || 1;
  if (n === 1) return 0;
  const w = rend.weights || null,
    total = w ? w.reduce((a, b) => a + Math.max(0, b), 0) : n;
  if (!(total > 0)) return 0;
  let x = rnd(seed, R.mesh) * total;
  for (let i = 0; i < n - 1; i++) {
    x -= w ? Math.max(0, w[i]) : 1;
    if (x < 0) return i;
  }
  return n - 1;
}

// One system's triangles for this frame: {data: Float32Array (STRIDE floats per vertex), count: vertices}, in a
// buffer the next call reuses. meshes: memorial-lobby.json fx.meshes; linear: drawn in linear light, where Unity decodes the
// colours from sRGB unless the renderer's Apply Active Color Space is off (colorSpace 0: the colours as stored).
// Positions are memorial lobby units (z dropped: the camera is orthographic).
let scratch = new Float32Array(0);
/**
 * The triangles of one system's particles for this frame, as vertices of STRIDE floats: for a billboard 6 per
 * particle, for a mesh particle its mesh's indices.
 * @param {object} fxs The memorial lobby's effects.
 * @param {object} s The system's state.
 * @param {object} [options]
 * @param {Object<string, object>} [options.meshes] The loaded meshes by id.
 * @param {boolean} [options.linear] Whether the memorial lobby draws in linear light.
 * @returns {{data: Float32Array, count: number}} The vertices and their number.
 */
export function geometry(fxs, s, { meshes = {}, linear = true } = {}) {
  const def = s.def,
    rend = def.renderer,
    world = def.main.space === 1;
  const decode = linear && rend.colorSpace !== 0;
  const list = rend.mode === 4 ? (rend.meshes || [rend.mesh]).map((id) => meshes[id]) : null;
  if (!s.particles.length || (list && !list.every(Boolean))) return { data: new Float32Array(0), count: 0 };
  const M = s.M,
    streams = rend.streams || { uv: [0, 2] };
  const need = s.particles.length * (list ? Math.max(...list.map((m) => m.i.length)) : 6) * STRIDE;
  if (scratch.length < need) scratch = new Float32Array(Math.max(need, scratch.length * 2, 4096));
  const data = scratch;
  let o = 0;
  const sx = colLen(M, 0),
    sy = colLen(M, 1),
    sz = colLen(M, 2),
    Rm = rotationOf(M);
  const maxSize = (rend.maxSize ?? 0.5) * VIEW_H;
  // drawn in order, the last on top: oldest in front (2) draws the youngest first, youngest in front (3) the oldest;
  // by distance (1) and by depth (4) the farthest first, by z for the orthographic camera that looks along +z
  let order = s.particles;
  if (rend.sort === 2) order = order.slice().sort((a, b) => a.age - b.age);
  else if (rend.sort === 3) order = order.slice().sort((a, b) => b.age - a.age);
  else if (rend.sort === 1 || rend.sort === 4) {
    const z = (pt) => (world ? pt.pos[2] : M[8] * pt.pos[0] + M[9] * pt.pos[1] + M[10] * pt.pos[2]);
    order = order.slice().sort((a, b) => z(b) - z(a));
  }
  for (const pt of order) {
    const mesh = list && list[meshIndex(rend, pt.seed)];
    const cur = fxs.current(s, pt),
      c = vertexColor(cur.color, decode),
      tc = texcoords(streams, cur.custom);
    const centre = world ? pt.pos : apply(M, pt.pos[0], pt.pos[1], pt.pos[2]);
    const fu = rend.flip && rnd(pt.seed, R.flipU) < rend.flip[0],
      fv = rend.flip && rnd(pt.seed, R.flipV) < rend.flip[1];
    // the sheet tile's UV rectangle (frame 0 is the top-left tile; UV (0, 0) is the bottom-left corner)
    let u0 = 0,
      v0 = 0,
      du = 1,
      dv = 1;
    if (cur.tiles) {
      du = 1 / cur.tiles[0];
      dv = 1 / cur.tiles[1];
      u0 = (cur.frame % cur.tiles[0]) * du;
      v0 = 1 - (Math.floor(cur.frame / cur.tiles[0]) + 1) * dv;
    }
    const push = (x, y, u, v, n = FACING, col = c) => {
      const uu = u0 + (fu ? 1 - u : u) * du,
        vv = v0 + (fv ? 1 - v : v) * dv;
      data[o++] = x;
      data[o++] = y;
      for (let k = 0; k < 12; k++) data[o + k] = tc[k];
      if (streams.uv) {
        data[o + streams.uv[0]] = uu;
        data[o + streams.uv[0] + 1] = vv;
      }
      if (streams.uv2) {
        data[o + streams.uv2[0]] = uu;
        data[o + streams.uv2[0] + 1] = vv;
      }
      o += 12;
      data[o++] = col[0];
      data[o++] = col[1];
      data[o++] = col[2];
      data[o++] = col[3];
      data[o++] = n[0];
      data[o++] = n[1];
      data[o++] = n[2];
    };
    if (mesh) {
      // the mesh scaled by the particle's 3D size and the system's scale, turned like a billboard (z clockwise on
      // screen, then x and y), in the system's orientation with local alignment, else the camera's (world axes); the
      // particle's colour times the mesh's vertex colours where it has them (ch0188's sea meshes fade out by them)
      const deg = 180 / Math.PI,
        mc = mesh.c,
        pc = cur.color;
      const turn = mul(rend.align === 2 ? Rm : IDENTITY, euler(cur.rot[0] * deg, cur.rot[1] * deg, -cur.rot[2] * deg));
      const T = mul(turn, [cur.size[0] * sx, 0, 0, 0, 0, cur.size[1] * sy, 0, 0, 0, 0, cur.size[2] * sz, 0]);
      for (const idx of mesh.i) {
        const p = applyDir(T, mesh.v[idx * 3], mesh.v[idx * 3 + 1], mesh.v[idx * 3 + 2]);
        const n = mesh.n ? norm(applyDir(turn, mesh.n[idx * 3], mesh.n[idx * 3 + 1], mesh.n[idx * 3 + 2])) : FACING;
        const col = mc
          ? vertexColor(
              [pc[0] * mc[idx * 4], pc[1] * mc[idx * 4 + 1], pc[2] * mc[idx * 4 + 2], pc[3] * mc[idx * 4 + 3]],
              decode,
            )
          : c;
        push(
          centre[0] + p[0],
          centre[1] + p[1],
          mesh.uv ? mesh.uv[idx * 2] : 0,
          mesh.uv ? mesh.uv[idx * 2 + 1] : 0,
          n,
          col,
        );
      }
      continue;
    }
    let w = cur.size[0] * sx,
      h = cur.size[1] * sy;
    const big = Math.max(Math.abs(w), Math.abs(h));
    if (big > maxSize) {
      w *= maxSize / big;
      h *= maxSize / big;
    } // max particle size: a fraction of the view height
    if (rend.mode === 1) {
      // stretched along its velocity on screen, the particle at the head: length = size x length scale + speed x
      // velocity scale
      const v = world
        ? [pt.vel[0] + pt.anim[0], pt.vel[1] + pt.anim[1]]
        : applyDir(M, pt.vel[0] + pt.anim[0], pt.vel[1] + pt.anim[1], pt.vel[2] + pt.anim[2]);
      const sp = Math.hypot(v[0], v[1]) || 1e-6,
        dx = v[0] / sp,
        dy = v[1] / sp;
      const len = w * (rend.lengthScale ?? 2) + sp * (rend.velocityScale || 0);
      for (const q of QUAD) {
        const a = (q[0] - 0.5) * len,
          b = q[1] * h;
        push(centre[0] + dx * a - dy * b, centre[1] + dy * a + dx * b, q[2], q[3]);
      }
      continue;
    }
    // facing the camera (or in the system's plane with local alignment), turned clockwise by the rotation
    const rz = -cur.rot[2],
      cz = Math.cos(rz),
      sn = Math.sin(rz);
    const px = (rend.pivot?.[0] || 0) * w,
      py = (rend.pivot?.[1] || 0) * h;
    const X = rend.align === 2 ? [Rm[0], Rm[4]] : [1, 0],
      Y = rend.align === 2 ? [Rm[1], Rm[5]] : [0, 1];
    const fx3 = def.main.rotation.length === 3 ? Math.cos(cur.rot[1]) : 1,
      fy3 = def.main.rotation.length === 3 ? Math.cos(cur.rot[0]) : 1;
    for (const q of QUAD) {
      const lx = (q[0] * w + px) * fx3,
        ly = (q[1] * h + py) * fy3;
      const rx = lx * cz - ly * sn,
        ry = lx * sn + ly * cz;
      push(centre[0] + X[0] * rx + Y[0] * ry, centre[1] + X[1] * rx + Y[1] * ry, q[2], q[3]);
    }
  }
  return { data: data.subarray(0, o), count: o / STRIDE };
}

// floats per vertex of geometry3d(): position (x, y, z), then as geometry()
export const STRIDE3 = STRIDE + 1;

const cross3 = (a, b) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];

let scratch3 = new Float32Array(0);
/**
 * One system's triangles for a perspective camera (the chibi view's halo, chibi-fx.js), as geometry() builds
 * them for the memorial lobby's orthographic one, with z kept: positions in the simulation's units and space.
 * Billboards face the camera, stretched ones lie along the velocity, meshes turn with the camera, the world or the
 * system as their alignment says; the max particle size is a share of the view's height at the particle's depth.
 * Trails are not built (no halo has them).
 * @param {object} fxs The chibi's effects.
 * @param {object} s The system's state.
 * @param {{x: number[], y: number[], z: number[], eye: number[], k: number}} view The camera in the
 *     simulation's space: its right, up and forward unit vectors (Unity's handedness: x cross y is z), its
 *     position, and the visible height per unit of depth (2 tan(fov / 2)).
 * @param {object} [options] As for geometry().
 * @returns {{data: Float32Array, count: number}} The vertices (STRIDE3 floats each) and their number.
 */
export function geometry3d(fxs, s, view, { meshes = {}, linear = true } = {}) {
  const def = s.def,
    rend = def.renderer,
    world = def.main.space === 1;
  const decode = linear && rend.colorSpace !== 0;
  const list = rend.mode === 4 ? (rend.meshes || [rend.mesh]).map((id) => meshes[id]) : null;
  if (!s.particles.length || rend.mode === 5 || (list && !list.every(Boolean)))
    return { data: new Float32Array(0), count: 0 };
  const M = s.M,
    streams = rend.streams || { uv: [0, 2] };
  const need = s.particles.length * (list ? Math.max(...list.map((m) => m.i.length)) : 6) * STRIDE3;
  if (scratch3.length < need) scratch3 = new Float32Array(Math.max(need, scratch3.length * 2, 4096));
  const data = scratch3;
  let o = 0;
  const sx = colLen(M, 0),
    sy = colLen(M, 1),
    sz = colLen(M, 2),
    Rm = rotationOf(M);
  const facing = [-view.z[0], -view.z[1], -view.z[2]];
  const depth = (p) =>
    (p[0] - view.eye[0]) * view.z[0] + (p[1] - view.eye[1]) * view.z[1] + (p[2] - view.eye[2]) * view.z[2];
  const centreOf = (pt) => (world ? pt.pos : apply(M, pt.pos[0], pt.pos[1], pt.pos[2]));
  // drawn in order, the last on top (geometry()), by distance from the eye (1) or by depth (4) the farthest first
  let order = s.particles;
  if (rend.sort === 2) order = order.slice().sort((a, b) => a.age - b.age);
  else if (rend.sort === 3) order = order.slice().sort((a, b) => b.age - a.age);
  else if (rend.sort === 1 || rend.sort === 4) {
    const far = (pt) => {
      const c = centreOf(pt);
      return rend.sort === 4 ? depth(c) : Math.hypot(c[0] - view.eye[0], c[1] - view.eye[1], c[2] - view.eye[2]);
    };
    order = order.slice().sort((a, b) => far(b) - far(a));
  }
  // the camera's axes as a matrix's columns: a mesh's View alignment
  const camera = [
    view.x[0],
    view.y[0],
    view.z[0],
    0,
    view.x[1],
    view.y[1],
    view.z[1],
    0,
    view.x[2],
    view.y[2],
    view.z[2],
    0,
  ];
  for (const pt of order) {
    const mesh = list && list[meshIndex(rend, pt.seed)];
    const cur = fxs.current(s, pt),
      c = vertexColor(cur.color, decode),
      tc = texcoords(streams, cur.custom);
    const centre = centreOf(pt);
    const fu = rend.flip && rnd(pt.seed, R.flipU) < rend.flip[0],
      fv = rend.flip && rnd(pt.seed, R.flipV) < rend.flip[1];
    let u0 = 0,
      v0 = 0,
      du = 1,
      dv = 1;
    if (cur.tiles) {
      du = 1 / cur.tiles[0];
      dv = 1 / cur.tiles[1];
      u0 = (cur.frame % cur.tiles[0]) * du;
      v0 = 1 - (Math.floor(cur.frame / cur.tiles[0]) + 1) * dv;
    }
    const push = (p, u, v, n = facing, col = c) => {
      const uu = u0 + (fu ? 1 - u : u) * du,
        vv = v0 + (fv ? 1 - v : v) * dv;
      data[o++] = p[0];
      data[o++] = p[1];
      data[o++] = p[2];
      for (let k = 0; k < 12; k++) data[o + k] = tc[k];
      if (streams.uv) {
        data[o + streams.uv[0]] = uu;
        data[o + streams.uv[0] + 1] = vv;
      }
      if (streams.uv2) {
        data[o + streams.uv2[0]] = uu;
        data[o + streams.uv2[0] + 1] = vv;
      }
      o += 12;
      data[o++] = col[0];
      data[o++] = col[1];
      data[o++] = col[2];
      data[o++] = col[3];
      data[o++] = n[0];
      data[o++] = n[1];
      data[o++] = n[2];
    };
    if (mesh) {
      const deg = 180 / Math.PI,
        mc = mesh.c,
        pc = cur.color;
      const base = rend.align === 2 ? Rm : rend.align === 1 ? IDENTITY : camera;
      const turn = mul(base, euler(cur.rot[0] * deg, cur.rot[1] * deg, -cur.rot[2] * deg));
      const T = mul(turn, [cur.size[0] * sx, 0, 0, 0, 0, cur.size[1] * sy, 0, 0, 0, 0, cur.size[2] * sz, 0]);
      for (const idx of mesh.i) {
        const p = applyDir(T, mesh.v[idx * 3], mesh.v[idx * 3 + 1], mesh.v[idx * 3 + 2]);
        const n = mesh.n ? norm(applyDir(turn, mesh.n[idx * 3], mesh.n[idx * 3 + 1], mesh.n[idx * 3 + 2])) : facing;
        const col = mc
          ? vertexColor(
              [pc[0] * mc[idx * 4], pc[1] * mc[idx * 4 + 1], pc[2] * mc[idx * 4 + 2], pc[3] * mc[idx * 4 + 3]],
              decode,
            )
          : c;
        push(
          [centre[0] + p[0], centre[1] + p[1], centre[2] + p[2]],
          mesh.uv ? mesh.uv[idx * 2] : 0,
          mesh.uv ? mesh.uv[idx * 2 + 1] : 0,
          n,
          col,
        );
      }
      continue;
    }
    let w = cur.size[0] * sx,
      h = cur.size[1] * sy;
    const big = Math.max(Math.abs(w), Math.abs(h)),
      maxSize = (rend.maxSize ?? 0.5) * view.k * Math.max(depth(centre), 1e-6);
    if (big > maxSize) {
      w *= maxSize / big;
      h *= maxSize / big;
    }
    if (rend.mode === 1) {
      // along its velocity, the particle at the head, its width across the velocity and the line of sight
      const v = world
        ? [pt.vel[0] + pt.anim[0], pt.vel[1] + pt.anim[1], pt.vel[2] + pt.anim[2]]
        : applyDir(M, pt.vel[0] + pt.anim[0], pt.vel[1] + pt.anim[1], pt.vel[2] + pt.anim[2]);
      const sp = Math.hypot(v[0], v[1], v[2]) || 1e-6,
        d = [v[0] / sp, v[1] / sp, v[2] / sp];
      const toEye = norm([view.eye[0] - centre[0], view.eye[1] - centre[1], view.eye[2] - centre[2]]);
      let side = cross3(d, toEye);
      side = Math.hypot(side[0], side[1], side[2]) > 1e-6 ? norm(side) : view.y;
      const len = w * (rend.lengthScale ?? 2) + sp * (rend.velocityScale || 0);
      for (const q of QUAD) {
        const a = (q[0] - 0.5) * len,
          b = q[1] * h;
        push(
          [centre[0] + d[0] * a + side[0] * b, centre[1] + d[1] * a + side[1] * b, centre[2] + d[2] * a + side[2] * b],
          q[2],
          q[3],
        );
      }
      continue;
    }
    const rz = -cur.rot[2],
      cz = Math.cos(rz),
      sn = Math.sin(rz);
    const px = (rend.pivot?.[0] || 0) * w,
      py = (rend.pivot?.[1] || 0) * h;
    let X = view.x,
      Y = view.y;
    if (rend.mode === 2) {
      X = [1, 0, 0];
      Y = [0, 0, 1];
    } else if (rend.mode === 3) {
      const flat = [view.x[0], 0, view.x[2]];
      X = Math.hypot(flat[0], flat[2]) > 1e-6 ? norm(flat) : [1, 0, 0];
      Y = [0, 1, 0];
    } else if (rend.align === 2) {
      X = [Rm[0], Rm[4], Rm[8]];
      Y = [Rm[1], Rm[5], Rm[9]];
    }
    const fx3 = def.main.rotation.length === 3 ? Math.cos(cur.rot[1]) : 1,
      fy3 = def.main.rotation.length === 3 ? Math.cos(cur.rot[0]) : 1;
    for (const q of QUAD) {
      const lx = (q[0] * w + px) * fx3,
        ly = (q[1] * h + py) * fy3;
      const rx = lx * cz - ly * sn,
        ry = lx * sn + ly * cz;
      push(
        [centre[0] + X[0] * rx + Y[0] * ry, centre[1] + X[1] * rx + Y[1] * ry, centre[2] + X[2] * rx + Y[2] * ry],
        q[2],
        q[3],
      );
    }
  }
  return { data: data.subarray(0, o), count: o / STRIDE3 };
}

// One system's trails for this frame (the trail module; the renderer's second submesh, drawn with its trail material),
// in geometry()'s vertex layout: for each particle with a trail, a ribbon facing the camera from the particle back
// through the points it left. Along it (u: 0 at the particle, 1 at the trail's end, the texture stretched once over
// the length) the width is widthOverTrail times the particle's size (Size affects Width), the colour colorOverTrail
// times colorOverLifetime at the particle's age, times the particle's colour (Inherit Particle Color). The trail's UV
// is TEXCOORD0.xy, whatever the renderer's custom streams say.
let trailScratch = new Float32Array(0);
/**
 * One system's trails for this frame (the trail module; the renderer's second submesh, drawn with its trail
 * material), in geometry()'s vertex layout: for each particle with a trail, a ribbon facing the camera from the
 * particle back through the points it left, its width widthOverTrail times the particle's size and its colour
 * colorOverTrail times colorOverLifetime times the particle's colour.
 * @param {object} fxs The memorial lobby's effects.
 * @param {object} s The system's state.
 * @param {object} [options]
 * @param {boolean} [options.linear] Whether the memorial lobby draws in linear light.
 */
export function trailGeometry(fxs, s, { linear = true } = {}) {
  const def = s.def,
    tr = def.modules?.trail,
    world = def.main.space === 1;
  const empty = { data: new Float32Array(0), count: 0 };
  if (!tr || !s.particles.length) return empty;
  const decode = linear && def.renderer.colorSpace !== 0,
    M = s.M,
    sx = colLen(M, 0),
    memorialLobby = world || tr.world;
  let need = 0;
  for (const pt of s.particles) if (pt.trail) need += pt.trail.length * 6 * STRIDE;
  if (!need) return empty;
  if (trailScratch.length < need) trailScratch = new Float32Array(Math.max(need, trailScratch.length * 2, 4096));
  const data = trailScratch;
  let o = 0;
  const push = (p, u, v, col) => {
    data[o++] = p[0];
    data[o++] = p[1];
    data[o] = u;
    data[o + 1] = v;
    for (let k = 2; k < 12; k++) data[o + k] = 0;
    o += 12;
    data[o++] = col[0];
    data[o++] = col[1];
    data[o++] = col[2];
    data[o++] = col[3];
    data[o++] = FACING[0];
    data[o++] = FACING[1];
    data[o++] = FACING[2];
  };
  for (const pt of s.particles) {
    if (!pt.trail) continue;
    // the particle in memorial lobby units, then its points (in the simulation space or, World Space, memorial lobby
    // units: trailPoint)
    const pts = [world ? pt.pos : apply(M, pt.pos[0], pt.pos[1], pt.pos[2])];
    for (let i = pt.trail.length - 1; i >= 0; i--) {
      const at = pt.trail[i].p,
        p = memorialLobby ? at : apply(M, at[0], at[1], at[2]),
        q = pts[pts.length - 1];
      if (Math.hypot(p[0] - q[0], p[1] - q[1]) > 1e-6) pts.push(p);
    }
    if (pts.length < 2) continue;
    const len = [0];
    for (let i = 1; i < pts.length; i++)
      len.push(len[i - 1] + Math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]));
    const total = len[len.length - 1],
      cur = fxs.current(s, pt),
      r = (k) => rnd(pt.seed, k);
    const life = colorAt(tr.color, pt.age / pt.life, r(R.trailColor)),
      base = tr.inherit ? cur.color : [1, 1, 1, 1];
    const size = (tr.sizeWidth ? cur.size[0] : 1) * sx;
    const corners = pts.map((p, i) => {
      const a = pts[Math.max(0, i - 1)],
        b = pts[Math.min(pts.length - 1, i + 1)];
      const dx = a[0] - b[0],
        dy = a[1] - b[1],
        dl = Math.hypot(dx, dy) || 1,
        u = len[i] / total;
      const half = (value(tr.width, u, r(R.trailWidth)) * size) / 2,
        nx = (-dy / dl) * half,
        ny = (dx / dl) * half;
      const c = colorAt(tr.colorTrail, u, r(R.trailColorTrail));
      const col = vertexColor(
        [life[0] * c[0] * base[0], life[1] * c[1] * base[1], life[2] * c[2] * base[2], life[3] * c[3] * base[3]],
        decode,
      );
      return { l: [p[0] + nx, p[1] + ny], r: [p[0] - nx, p[1] - ny], u, col };
    });
    for (let i = 0; i + 1 < corners.length; i++) {
      const a = corners[i],
        b = corners[i + 1];
      push(a.l, a.u, 1, a.col);
      push(a.r, a.u, 0, a.col);
      push(b.r, b.u, 0, b.col);
      push(b.r, b.u, 0, b.col);
      push(b.l, b.u, 1, b.col);
      push(a.l, a.u, 1, a.col);
    }
  }
  return { data: data.subarray(0, o), count: o / STRIDE };
}

/**
 * A sprite's quad (memorial-lobby.json fx.sprites: a SpriteRenderer, memorial_lobby_intro.py sprite): its rect around the pivot,
 * in the GameObject's units, through its matrix M; the sprite's UVs (flipped as the renderer says) in TEXCOORD0 and
 * its colour as the renderer's vertex colour (decoded from sRGB in linear light, like a particle's).
 * @returns {{data: Float32Array, count: number}} The six vertices of the quad.
 */
export function spriteGeometry(def, M, color, { linear = true } = {}) {
  const [x0, y0, x1, y1] = def.quad,
    [u0, v0, u1, v1] = def.uv,
    c = vertexColor(color, linear);
  const fu = def.flip?.[0],
    fv = def.flip?.[1],
    data = new Float32Array(6 * STRIDE);
  let o = 0;
  for (const q of QUAD) {
    const lx = q[2] ? x1 : x0,
      ly = q[3] ? y1 : y0,
      p = apply(M, lx, ly, 0);
    data[o++] = p[0];
    data[o++] = p[1];
    data[o] = (fu ? !q[2] : q[2]) ? u1 : u0;
    data[o + 1] = (fv ? !q[3] : q[3]) ? v1 : v0;
    o += 12;
    data[o++] = c[0];
    data[o++] = c[1];
    data[o++] = c[2];
    data[o++] = c[3];
    data[o++] = FACING[0];
    data[o++] = FACING[1];
    data[o++] = FACING[2];
  }
  return { data, count: 6 };
}
