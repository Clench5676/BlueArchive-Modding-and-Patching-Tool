// The memorial lobby's measuring, the pure part: the painted-rect grid, the poses the zoom-out limit checks and how
// it shrinks, the camera framings and the view rectangles. The module state (the lobby shown, its caches and timers)
// stays in lobby.js, which loads once per query-keyed instance; this module holds none, so the instances share it.
import { artBounds, validRect, worldBounds } from "./lobby-health.js";

export const ZOOM_IN = 2; // fullscreen zoom limit relative to the game's view (the art is about 0.5 texel per unit)
export const ZOOM_OUT_MAX = 4; // no zoom-out limit goes past this many game views across (the custom limit's range too)
export const ZOOM_CUSTOM = 2; // the custom limit until one is set

export const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
/**
 * The clips a skeleton's idle starts besides itself (the game plays every clip of the
 * SpineCharacter's idle pool, each with its sync clips): the idle's sync clips and the rest of its pool, when
 * the idle is the pool's first clip. Examples: Shigure's Train_00_R, Hoshino's whale, the snow, clouds, petals,
 * stars and sweat of other lobbies.
 * @param {string|null} idle
 * @param {(name: string) => object|undefined} clip Gives a clip (lobby.json clips).
 * @returns {string[]}
 */
export function idleClips(info, name, idle, clip) {
  const pool = info?.idles?.[name] || [];
  if (!idle || pool[0] !== idle) return [];
  return [...new Set([...(clip(idle)?.sync || []), ...pool.slice(1)])];
}

// --- painted rect P: opaque, normal-blend triangles of every layer, over a few idle poses, on a 16-unit grid ---
export const CELL = 16,
  SAMPLES = 6;
const QUAD = [0, 1, 2, 2, 3, 0];
// Geometry only, never art or a live player: bound both the retained occupancy masks and revision keys.
export function rectCache(maxBytes = 8 * 1024 * 1024, maxEntries = 24) {
  const entries = new Map();
  let bytes = 0;
  const remove = (key) => {
    const old = entries.get(key);
    if (old) {
      bytes -= old.bytes;
      entries.delete(key);
    }
  };
  return {
    get(key) {
      const entry = key && entries.get(key);
      if (!entry) return null;
      entries.delete(key);
      entries.set(key, entry);
      return structuredClone(entry.value);
    },
    set(key, value) {
      if (!key) return;
      remove(key);
      const size = key.length * 2 + (value.any?.byteLength || 0) + 1024;
      if (size > maxBytes) return;
      while (entries.size && (entries.size >= maxEntries || bytes + size > maxBytes))
        remove(entries.keys().next().value);
      entries.set(key, { value: structuredClone(value), bytes: size });
      bytes += size;
    },
  };
}
// the grid: three game views wide and high around the game's view
export function gridOf(G) {
  return { X0: G.x - G.w, Y0: G.y - G.h, gw: Math.ceil((G.w * 3) / CELL), gh: Math.ceil((G.h * 3) / CELL) };
}
// sets the cells whose centre one of the posed layer's painted triangles covers, for each target [out, rects]: in
// `out`, within its cell rects ([i0, i1, j0, j1], inclusive); additive FX and see-through parts never count
export function paint(grid, l, sk, targets) {
  const { X0, Y0, gw } = grid;
  const tri = (ax, ay, bx, by, cx, cy) => {
    const area = (bx - ax) * (cy - ay) - (by - ay) * (cx - ax);
    if (Math.abs(area) < 1e-6) return;
    const I0 = Math.floor((Math.min(ax, bx, cx) - X0) / CELL),
      I1 = Math.floor((Math.max(ax, bx, cx) - X0) / CELL);
    const J0 = Math.floor((Math.min(ay, by, cy) - Y0) / CELL),
      J1 = Math.floor((Math.max(ay, by, cy) - Y0) / CELL);
    for (const [out, rects] of targets)
      for (const r of rects) {
        const i0 = Math.max(I0, r[0]),
          i1 = Math.min(I1, r[1]),
          j0 = Math.max(J0, r[2]),
          j1 = Math.min(J1, r[3]);
        for (let j = j0; j <= j1; j++)
          for (let i = i0; i <= i1; i++) {
            if (out[j * gw + i]) continue; // painted already
            const px = X0 + (i + 0.5) * CELL,
              py = Y0 + (j + 0.5) * CELL;
            const w0 = ((bx - px) * (cy - py) - (by - py) * (cx - px)) / area,
              w1 = ((cx - px) * (ay - py) - (cy - py) * (ax - px)) / area;
            if (w0 >= -1e-6 && w1 >= -1e-6 && 1 - w0 - w1 >= -1e-6) out[j * gw + i] = 1;
          }
      }
  };
  for (const slot of sk.drawOrder) {
    const a = slot.getAttachment();
    if (!a || !slot.bone.active || slot.data.blendMode !== spine.BlendMode.Normal) continue;
    if (slot.color.a * (a.color ? a.color.a : 1) < 0.9) continue;
    let v, tris;
    if (a instanceof spine.RegionAttachment) {
      v = new Float32Array(8);
      a.computeWorldVertices(slot, v, 0, 2);
      tris = QUAD;
    } else if (a instanceof spine.MeshAttachment) {
      v = new Float32Array(a.worldVerticesLength);
      a.computeWorldVertices(slot, 0, a.worldVerticesLength, v, 0, 2);
      tris = a.triangles;
    } else continue;
    const X = (n) => l.x + v[n * 2] * l.scale,
      Y = (n) => l.y + v[n * 2 + 1] * l.scale;
    for (let t = 0; t < tris.length; t += 3)
      tri(X(tris[t]), Y(tris[t]), X(tris[t + 1]), Y(tris[t + 1]), X(tris[t + 2]), Y(tris[t + 2]));
  }
}
// the bounding box (cells, inclusive) of the cell rect P and the set cells of `mask` joined to it across gaps of at
// most `gap` cells: the scene's art, without the parts parked away from it (a talk's close-up, a second stage set)
const GAP = 2;
/**
 * The box of the scene's art over its poses, grown from the painted rect through cells the poses paint.
 * @param {Uint8Array} mask
 * @param {number} gw The grid's width in cells.
 * @param {number} gh The grid's height in cells.
 * @param {number} [gap] The gap in cells that may be bridged.
 */
export function sceneBox(mask, gw, gh, P, gap = GAP) {
  const seen = new Uint8Array(gw * gh),
    queue = new Int32Array(gw * gh),
    b = { i0: P.i0, i1: P.i1, j0: P.j0, j1: P.j1 };
  let head = 0,
    tail = 0;
  for (let j = P.j0; j <= P.j1; j++)
    for (let i = P.i0; i <= P.i1; i++) {
      seen[j * gw + i] = 1;
      queue[tail++] = j * gw + i;
    }
  const reach = gap + 1;
  while (head < tail) {
    const k = queue[head++],
      i = k % gw,
      j = (k - i) / gw;
    for (let y = Math.max(0, j - reach); y <= Math.min(gh - 1, j + reach); y++) {
      for (let x = Math.max(0, i - reach), row = y * gw; x <= Math.min(gw - 1, i + reach); x++) {
        if (seen[row + x] || !mask[row + x]) continue;
        seen[row + x] = 1;
        queue[tail++] = row + x;
        if (x < b.i0) b.i0 = x;
        if (x > b.i1) b.i1 = x;
        if (y < b.j0) b.j0 = y;
        if (y > b.j1) b.j1 = y;
      }
    }
  }
  return b;
}
/**
 * The cells of the cell rect R outside the cell rect P inside it, as up to four cell rects (below, above, left
 * and right of P).
 * @returns {number[][]} Each as [i0, i1, j0, j1].
 */
export function around(P, R) {
  return [
    [R.i0, R.i1, R.j0, P.j0 - 1],
    [R.i0, R.i1, P.j1 + 1, R.j1],
    [R.i0, P.i0 - 1, P.j0, P.j1],
    [P.i1 + 1, R.i1, P.j0, P.j1],
  ].filter(([i0, i1, j0, j1]) => i0 <= i1 && j0 <= j1);
}
// a cell rect in NGUI units
export const unitsOf = (grid, r) => ({
  x0: grid.X0 + r.i0 * CELL,
  x1: grid.X0 + (r.i1 + 1) * CELL,
  y0: grid.Y0 + r.j0 * CELL,
  y1: grid.Y0 + (r.j1 + 1) * CELL,
});

// --- the zoom-out limit, safe for every clip: P, measured on idle poses, shrunk until no clip the lobby plays uncovers
// a cell near its edge (Kurumi's last dialogue moves the scene and showed a blank strip at the widest view) ---
export const BAND = 24; // cells inside each side of P that are checked: a scene moving up to 384 units
const CLIP_STEP = 0.1; // s between the poses sampled from a clip
export const SLICE_MS = 8; // the measurement runs in slices of the main thread, after the lobby is shown
const DRAG_CLIP = /^(Pat|Look|Pinch|HandFollow|Touch)(End)?_\d+_M$/;

/**
 * The poses to check, [layer index, tracks]: every clip the lobby plays (its dialogues, the drag clips and
 * their ends, the clips the idle starts, the random idle clips and the clips they chain to) every CLIP_STEP, on
 * its own track over the idle and with its sync clips, as playClip plays them.
 * @param {string[]} talks
 * @param {(layer: object, name: string) => {track: number, next?: string, sync?: string[]}} clip Gives the
 *     clip's track, next and sync.
 * @returns {Array<[number, object]>}
 */
export function clipPoses(layers, info, talks, clip) {
  const out = [];
  layers.forEach((l, index) => {
    const data = l.data,
      idleDur = l.idle ? data.findAnimation(l.idle)?.duration || 0 : 0;
    const names = [...idleClips(info, l.name, l.idle, (name) => clip(l, name)), ...(info?.randomIdle?.[l.name] || [])];
    if (l.main) {
      names.push(...talks);
      const drags = (info?.drags || []).filter((d) => d.skeleton === l.name);
      if (drags.length) for (const d of drags) names.push(d.ing, d.end);
      else names.push(...data.animations.map((a) => a.name).filter((n) => DRAG_CLIP.test(n)));
    }
    const seen = new Set();
    while (names.length) {
      const name = names.shift(),
        anim = name && !seen.has(name) ? data.findAnimation(name) : null;
      if (!anim) continue;
      seen.add(name);
      const c = clip(l, name);
      if (c.next) names.push(c.next);
      const syncs = (c.sync || [])
        .filter((s) => data.findAnimation(s))
        .map((s) => [clip(l, s).track, s])
        .filter(([t]) => t !== c.track);
      const n = Math.max(1, Math.ceil(anim.duration / CLIP_STEP - 1e-6));
      for (let k = 0; k <= n; k++) {
        const t = Math.min(anim.duration, k * CLIP_STEP),
          tracks = [];
        if (l.idle && c.track !== 0) tracks.push([0, l.idle, idleDur ? t % idleDur : 0, true]);
        tracks.push([c.track, name, t, false]);
        for (const [track, s] of syncs) tracks.push([track, s, t, false]);
        out.push([index, tracks]);
      }
    }
  });
  return out;
}
/**
 * Shrink P (in cells, inclusive) while the outer column or row of a side has a cell some pose left unpainted
 * (`bad`, set within `band` cells of P's sides only).
 * @param {Uint8Array} bad
 * @param {number} gw The grid's width in cells.
 * @param {number} [band] How far from P's sides the cells are checked.
 * @returns {CellRect}
 */
export function shrinkRect(P, bad, gw, band = BAND) {
  const r = { ...P };
  const clear = (i0, i1, j0, j1) => {
    for (let j = j0; j <= j1; j++) for (let i = i0; i <= i1; i++) if (bad[j * gw + i]) return false;
    return true;
  };
  for (let moved = true; moved; ) {
    moved = false;
    if (r.i0 - P.i0 < band && r.i0 < r.i1 && !clear(r.i0, r.i0, r.j0, r.j1)) {
      r.i0++;
      moved = true;
    }
    if (P.i1 - r.i1 < band && r.i1 > r.i0 && !clear(r.i1, r.i1, r.j0, r.j1)) {
      r.i1--;
      moved = true;
    }
    if (r.j0 - P.j0 < band && r.j0 < r.j1 && !clear(r.i0, r.i1, r.j0, r.j0)) {
      r.j0++;
      moved = true;
    }
    if (P.j1 - r.j1 < band && r.j1 > r.j0 && !clear(r.i0, r.i1, r.j1, r.j1)) {
      r.j1--;
      moved = true;
    }
  }
  return r;
}
// the zoom-out limit (a setting): the art one cell in, and never less than the game's view; art that hardly covers the
// view keeps the game's view. The painted rect counts a 16-unit cell as painted by its centre, so the art can end a
// few units before its border.
export function outerOf(edge, full, sparse) {
  if (sparse) return rectOf(full);
  return {
    x0: Math.min(edge.x0 + CELL, full.x),
    x1: Math.max(edge.x1 - CELL, full.x + full.w),
    y0: Math.min(edge.y0 + CELL, full.y),
    y1: Math.max(edge.y1 - CELL, full.y + full.h),
  };
}
// the layers' setup-pose bounds, the painted area of the fallback for art that hardly covers the view
export function boundsRect(loaded) {
  const out = { x0: Infinity, x1: -Infinity, y0: Infinity, y1: -Infinity };
  for (const l of loaded) {
    const r = worldBounds(l.rest?.bounds || artBounds(l.player.skeleton), l);
    if (!r) continue;
    out.x0 = Math.min(out.x0, r.x0);
    out.x1 = Math.max(out.x1, r.x1);
    out.y0 = Math.min(out.y0, r.y0);
    out.y1 = Math.max(out.y1, r.y1);
  }
  if (!validRect(out)) throw new Error("No drawable lobby bounds");
  return out;
}

// --- the camera framing: a 16:9 view, centred across, zoomed and moved up or down, that the shutter keeps as
// the lobby's resting view; it may go wider than the game's view as far as no pose shows past the art ---
/**
 * The camera's limits from the bad cells of a grid: a summed-area table of them for the view checks, and the
 * widest view that fits.
 * @param {{X0: number, Y0: number, gw: number, gh: number}} grid The grid, in cells of CELL units.
 * @param {Uint8Array} bad
 * @returns {object} The limits: G, grid, sum and wMax.
 */
export function framingLimits(G, grid, bad) {
  const { gw, gh } = grid,
    sum = new Int32Array((gw + 1) * (gh + 1));
  for (let j = 0; j < gh; j++)
    for (let i = 0, row = 0; i < gw; i++) {
      row += bad[j * gw + i];
      sum[(j + 1) * (gw + 1) + i + 1] = sum[j * (gw + 1) + i + 1] + row;
    }
  const lim = { G: { ...G }, grid, sum };
  lim.wMax = widestFraming(lim);
  return lim;
}
export const FRAMING_CELL = CELL;
// the bad cells whose centres a rect takes in; a rect past the grid counts as bad
function badIn(lim, x0, y0, x1, y1) {
  const { X0, Y0, gw, gh } = lim.grid;
  const i0 = Math.ceil((x0 - X0) / CELL - 0.5 - 1e-9),
    i1 = Math.floor((x1 - X0) / CELL - 0.5 + 1e-9);
  const j0 = Math.ceil((y0 - Y0) / CELL - 0.5 - 1e-9),
    j1 = Math.floor((y1 - Y0) / CELL - 0.5 + 1e-9);
  if (i1 < i0 || j1 < j0) return 0;
  if (i0 < 0 || j0 < 0 || i1 >= gw || j1 >= gh) return Infinity;
  const W = gw + 1,
    S = lim.sum;
  return S[(j1 + 1) * W + i1 + 1] - S[j0 * W + i1 + 1] - S[(j1 + 1) * W + i0] + S[j0 * W + i0];
}
/**
 * Whether a view of a width, centred across on the game's view, with its bottom at y, shows nothing past the
 * art; before the measurement is done only views inside the game's view do.
 * @param {object} lim The limits, as framingLimits gives them.
 * @returns {boolean} True when it fits.
 */
export function framingFits(lim, w, y) {
  const G = lim.G,
    h = (w * 9) / 16,
    x0 = G.x + G.w / 2 - w / 2;
  if (!lim.sum) return x0 >= G.x - 1e-6 && x0 + w <= G.x + G.w + 1e-6 && y >= G.y - 1e-6 && y + h <= G.y + G.h + 1e-6;
  return badIn(lim, x0, y, x0 + w, y + h) === 0;
}
const FRAME_STEP = CELL / 4;
// the nearest bottom to y at which a view of width w fits, or null
function framingBottom(lim, w, y) {
  if (framingFits(lim, w, y)) return y;
  const span = lim.G.h * 3;
  for (let d = FRAME_STEP; d <= span; d += FRAME_STEP) {
    if (framingFits(lim, w, y + d)) return y + d;
    if (framingFits(lim, w, y - d)) return y - d;
  }
  return null;
}
// the widest view that fits somewhere up or down
function widestFraming(lim) {
  const G = lim.G;
  for (let w = Math.min(G.w * 3, G.w * ZOOM_OUT_MAX); w > G.w; w -= CELL / 2) {
    if (framingBottom(lim, w, G.y + G.h / 2 - (w * 9) / 32) != null) return w;
  }
  return G.w;
}
/**
 * Bring a view inside the camera's limits: centred across, ZOOM_IN x the game's view at most, as wide as fits
 * at most, then the nearest height at which it shows nothing past the art (narrower if none).
 */
export function framingView(v, lim) {
  const G = lim.G,
    cx = G.x + G.w / 2;
  let w = clamp(v.w, G.w / ZOOM_IN, lim.sum ? lim.wMax : G.w);
  for (;;) {
    const h = (w * 9) / 16,
      y = framingBottom(lim, w, v.y + (v.h - h) / 2);
    if (y != null || w <= G.w / ZOOM_IN + 1e-6) return { x: cx - w / 2, y: y ?? G.y + (G.h - h) / 2, w, h };
    w = Math.max(G.w / ZOOM_IN, w - CELL / 2);
  }
}
/**
 * What the shutter keeps for a view V: the lobby scaled by k about the screen centre and moved by `offset`
 * (NGUI units), so that V fills the screen as the game's view did: the root's new place r1 = k (r0 - c) with c
 * V's centre.
 * @returns {{scale: number, offset: number[]}} The scale and the offset.
 */
export function cameraParams(V, G) {
  const k = G.w / V.w,
    cx = V.x + V.w / 2 - (G.x + G.w / 2),
    cy = V.y + V.h / 2 - (G.y + G.h / 2);
  return { scale: k, offset: [-k * cx, -k * cy] };
}

// --- views: {x, y, w, h} in NGUI units, y up ---
// the largest 16:9 view no wider than w that fits P, centred on (cx, cy) as far as P allows
export function fit(P, w, cx, cy) {
  const W = Math.min(w, P.x1 - P.x0, ((P.y1 - P.y0) * 16) / 9),
    H = (W * 9) / 16;
  return { x: clamp(cx - W / 2, P.x0, P.x1 - W), y: clamp(cy - H / 2, P.y0, P.y1 - H), w: W, h: H };
}
// left view: the head-pat point T near 28 % across and 34 % down, as wide as possible without leaving P. A first pass
// keeps T within 16-34 % across (zoom up to 1.6 x the game's view), a second allows 44 % and 1.9 x; else 1.9 x as near
// as P allows.
export function leftView(P, T, G) {
  const pass = (fxMax, zoomIn) => {
    const maxW = Math.min(G.w, P.x1 - P.x0, ((P.y1 - P.y0) * 16) / 9),
      minW = Math.min(maxW, G.w / zoomIn);
    for (let W = maxW; W >= minW - 1e-6; W -= G.w / 200) {
      const H = (W * 9) / 16;
      const xa = Math.max(P.x0, T.x - fxMax * W),
        xb = Math.min(P.x1 - W, T.x - 0.16 * W);
      const ya = Math.max(P.y0, T.y - 0.78 * H),
        yb = Math.min(P.y1 - H, T.y - 0.54 * H);
      if (xa <= xb && ya <= yb)
        return { x: clamp(T.x - 0.28 * W, xa, xb), y: clamp(T.y - 0.66 * H, ya, yb), w: W, h: H };
    }
    return null;
  };
  const found = pass(0.34, 1.6) || pass(0.44, 1.9);
  if (found) return found;
  const W = Math.min(G.w / 1.9, P.x1 - P.x0, ((P.y1 - P.y0) * 16) / 9),
    H = (W * 9) / 16;
  return { x: clamp(T.x - 0.28 * W, P.x0, P.x1 - W), y: clamp(T.y - 0.66 * H, P.y0, P.y1 - H), w: W, h: H };
}
/**
 * A layer's spine-player viewport for a view: the view in that skeleton's own units (also the game's view, for
 * the post-processing's screen).
 * @param {{x: number, y: number, scale: number}} l
 */
export const layerViewport = (v, l) => ({
  x: (v.x - l.x) / l.scale,
  y: (v.y - l.y) / l.scale,
  width: v.w / l.scale,
  height: v.h / l.scale,
  padLeft: 0,
  padRight: 0,
  padTop: 0,
  padBottom: 0,
  transitionTime: 0,
  animations: {},
});

// fullscreen zoom at a stage point: from ZOOM_IN x to the game's view (never wider, so nothing the game never shows
// appears), or with the zoom-out setting to the widest view its limit allows; the width is clamped first, so the
// point under the pointer holds at the limits too
export const rectOf = (v) => ({ x0: v.x, x1: v.x + v.w, y0: v.y, y1: v.y + v.h });
export const union = (a, b) => ({
  x0: Math.min(a.x0, b.x0),
  x1: Math.max(a.x1, b.x1),
  y0: Math.min(a.y0, b.y0),
  y1: Math.max(a.y1, b.y1),
});
const meet = (a, b) => ({
  x0: Math.max(a.x0, b.x0),
  x1: Math.min(a.x1, b.x1),
  y0: Math.max(a.y0, b.y0),
  y1: Math.min(a.y1, b.y1),
});
// a 16:9 rect w units wide around the middle of R
function centred(R, w) {
  const cx = (R.x0 + R.x1) / 2,
    cy = (R.y0 + R.y1) / 2,
    h = (w * 9) / 16;
  return { x0: cx - w / 2, x1: cx + w / 2, y0: cy - h / 2, y1: cy + h / 2 };
}
export const ZOOM_MODES = ["game", "art", "unlocked", "custom"];
/**
 * The rect a fullscreen view stays inside for the zoom-out setting: the game's view ("game"), the art every
 * clip keeps painted ("art"), the box of the scene's art over its poses ("unlocked"), or `custom` game views
 * across around the game's view ("custom", 1 to ZOOM_OUT_MAX); never less than the game's view, never more than
 * ZOOM_OUT_MAX game views across.
 * @param {{mode: string, custom?: number}} zoom
 * @param {object} m The lobby's rects (measure).
 * @returns {ViewRect}
 */
export function zoomBounds(zoom, m, G) {
  const full = rectOf(m.full),
    f = clamp(Number(zoom?.custom) || ZOOM_CUSTOM, 1, ZOOM_OUT_MAX);
  const B =
    zoom?.mode === "art"
      ? m.outer
      : zoom?.mode === "unlocked"
        ? m.whole
        : zoom?.mode === "custom"
          ? centred(full, f * G.w)
          : null;
  return B ? union(meet(B, centred(full, ZOOM_OUT_MAX * G.w)), full) : full;
}
export const widest = (B) => Math.min(B.x1 - B.x0, ((B.y1 - B.y0) * 16) / 9);
