import { lobbyCaptureView, lobbyMaximumWidth } from "./photography/camera.js";
// Memorial lobby for the detail screen's second tab, framed and played like the game. The lobby's UILobbyElement prefab
// (lobby.json, read by the server) places every skeleton in NGUI units around the screen centre; at 16:9 the game shows
// the 2960 x 1665 units G around it. Fullscreen shows exactly G; the left view puts the head-pat point (Touch_Point)
// left of the panel and stays inside the painted rect P, the largest rectangle around G that the lobby's opaque art
// covers, so neither view shows a blank edge; switching between them crossfades. Dialogues play the talk clips with the
// game's own tracks and mixes, their voices and one subtitle line per voice cue (lines and voices come from Kivo,
// setLines). The touch interaction and the wheel zoom are in lobby-touch.js, the drawing in lobby-stage.js, the intro in
// lobby-intro.js.
import { specialFramingOf, specialView } from "./detail/special-view.js";
import { latestLoad, loadPlayer } from "./spine/spine-load.js";
import { ensureSpine } from "./spine/spine-runtime.js";
import { forgetSpineAssets, spineBase, spineAssetBase } from "./core/api.js";
import { lobbyIdle } from "./spine/rest-pose.js";
// every wait of the lobby is a preview's: counted in the foreground, without the time a read waits for the library
import { withPreviewTimeout as withTimeout } from "./core/async.js";
import { atStep, previewError } from "./detail/preview-error.js";
import { T, language } from "./core/i18n.js";
const dialogueModule = () => import("./student-hall-dialogue.js");
import { artBounds, containingView, overlaps, resumeRest, settle, validRect, worldBounds } from "./lobby-health.js";
import * as Linear from "./core/linear.js";
import * as Materials from "./spine/skeleton-materials.js";
import * as Particles from "./particles-gl.js";
import * as SlotColours from "./spine/slot-colours.js";
import * as Stage from "./lobby-stage.js";
import * as Pins from "./spine/skeleton-pins.js";
import * as Touch from "./lobby-touch.js";
import * as Sync from "./lobby-sync.js";
import * as Intro from "./lobby-intro.js";
import { freezeWhileLoading, onScreen, previewLifecycle } from "./detail/preview-lifecycle.js";

const STAGE = { w: 1920, h: 1080 };
const GAME = { x: -1480, y: -832.5, w: 2960, h: 1665 }; // lobby.json "view": the game's 16:9 view in NGUI units
const ZOOM_IN = 2; // fullscreen zoom limit relative to the game's view (the art is about 0.5 texel per unit)
const ZOOM_OUT_MAX = 4; // no zoom-out limit goes past this many game views across (the custom limit's range too)
const ZOOM_CUSTOM = 2; // the custom limit until one is set
const ZOOM_TAU = 0.08; // s: a zoom or pan eases to its target
const FEATHER_MS = 100; // the fallback's feather and extension follow a zoom or pan at most this often
const FADE_MS = 450; // the switch crossfade, in step with the UI slide (app.css .45s)
const TALK_GUARD_MS = 500; // a tap does not skip a dialogue that started this recently

let players = [],
  current = null,
  mode = "left",
  extendTimer = null,
  chain = null,
  token = 0,
  wantTouch = false;
let zoomMode = "art",
  zoomCustom = ZOOM_CUSTOM; // the zoom-out setting (setZoomLimit)
let talk = null,
  talkIndex = -1,
  cueTimers = [],
  audios = [],
  subtitleTimer = null;
let lines = {},
  muted = false,
  volume = 0.8,
  lineOf = (l) => l?.["zh-CN"] || "";
let introOn = true; // the intro setting (setIntro): off shows the lobby as its intro leaves it
const listeners = new Set();
let ownsTouch = false;
let dialogueFullscreen = null;
/**
 * A view in NGUI units: a 16:9 window onto the lobby.
 * @typedef {{x: number, y: number, w: number, h: number}} ViewRect
 */

/**
 * A rectangle of cells of the painted-rect grid, edges inclusive.
 * @typedef {{i0: number, i1: number, j0: number, j1: number}} CellRect
 */

/** Tell the lobby whether the dialogue is shown for the fullscreen view. */
export function setDialogueFullscreen(on) {
  dialogueFullscreen = on;
  current?.caption?.reposition();
}
let dialogueViewBounds = { left: 0, top: 0, right: STAGE.w, bottom: STAGE.h };
/** Set the bounds the subtitle is kept inside, and place it again. */
export function setDialogueBounds(bounds) {
  dialogueViewBounds = bounds;
  current?.caption?.reposition();
}
// Query-keyed Lobby modules share lobby-touch.js. The shared token also protects against late disposal when a
// caller claims another instance before releasing this one (including the canonical detail-screen instance).
const touchOwners = (globalThis[Symbol.for("bamt.lobby.touch-owners")] ||= new WeakMap()),
  touchToken = {};
const ownsInput = () => ownsTouch && touchOwners.get(Touch) === touchToken;
const paused = () => !!(current?.suspended || current?.backgroundPaused || current?.loadingPaused);
// This module can have two Hall instances. Each owns its deadlines; suspension retains remaining time rather
// than clearing/recreating a dialogue or random-idle run. Extracted playback tests still use native timers.
const nativeTimeout = (...args) => globalThis.setTimeout(...args),
  nativeClearTimeout = (id) => globalThis.clearTimeout(id);
const sceneTimers = new Map();
function armTimer(timer) {
  timer.started = performance.now();
  timer.id = nativeTimeout(
    () => {
      sceneTimers.delete(timer);
      timer.fn();
    },
    Math.max(0, timer.remaining),
  );
}
function setTimeout(fn, ms = 0) {
  const timer = { fn, remaining: ms, id: null, started: null };
  sceneTimers.set(timer, timer);
  if (!paused()) armTimer(timer);
  return timer;
}
function clearTimeout(timer) {
  if (!timer) return;
  nativeClearTimeout(timer.id);
  sceneTimers.delete(timer);
}
function pauseTimers() {
  for (const timer of sceneTimers.values())
    if (timer.id != null) {
      nativeClearTimeout(timer.id);
      timer.remaining -= Math.max(0, performance.now() - timer.started);
      timer.id = null;
    }
}
function resumeTimers() {
  for (const timer of sceneTimers.values()) if (timer.id == null) armTimer(timer);
}
function clearTimers() {
  for (const timer of sceneTimers.values()) nativeClearTimeout(timer.id);
  sceneTimers.clear();
}

function releaseTouch() {
  const owned = ownsInput();
  ownsTouch = false;
  if (!owned) return;
  touchOwners.delete(Touch);
  Touch.detach();
  Touch.setup(null);
}
function restoreTouch() {
  if (!current?.touch || paused()) return;
  touchOwners.set(Touch, touchToken);
  Touch.setup(current.touch);
  ownsTouch = true;
  if (mode === "full" && wantTouch) Touch.attach(current.host);
}
function visualImage(url) {
  const c = current;
  if (!c) return Promise.reject(new Error("Lobby image has no owner"));
  if (!c.visualLoads.has(url)) {
    const image = new Image();
    const request = withTimeout(
      new Promise((resolve, reject) => {
        image.onload = () => resolve(image);
        image.onerror = () => reject(new Error(`Lobby image unavailable: ${url}`));
        image.src = url;
      }),
      15000,
      url,
      { signal: c.signal },
    ).finally(() => {
      image.onload = image.onerror = null;
    });
    request.catch(() => {}); // The complete-frame barrier reports failures, including those swallowed by a renderer.
    c.visualLoads.set(url, request);
  }
  return c.visualLoads.get(url);
}

const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
const isBg = (n) => /background|_bg\b|bg_/i.test(n);
const game = () => {
  const v = current?.specialGame || current?.info?.view || GAME;
  if (
    ![v.x, v.y, v.w, v.h].every(Number.isFinite) ||
    v.w <= 0 ||
    v.h <= 0 ||
    (!current?.framing && (v.w > GAME.w * 16 || v.h > GAME.h * 16))
  )
    throw new Error("Invalid lobby camera view");
  return { ...v };
};

// camera: the mod is a lobby camera, whose layout is its own prefab's (/asset/spine/mod/<id>/lobby.json)
async function sourcesOf(core, mod, refresh = false, signal = null, official = false, camera = false) {
  const artSource = spineBase(mod ? "mod" : official ? "official-lobby" : "lobby", mod || core);
  const layoutSource = mod && camera ? artSource : core ? spineBase("lobby", core) : null;
  // A shown tab must not inherit a manifest request that belonged to a cancelled or previous source.
  if (refresh)
    for (const source of new Set([artSource, layoutSource].filter(Boolean)))
      forgetSpineAssets(source.slice(0, source.lastIndexOf("/")));
  const art = spineAssetBase(artSource, { signal });
  const layout = !layoutSource
    ? Promise.resolve(null)
    : (mod && !camera) || official
      ? spineAssetBase(layoutSource, { signal })
      : art;
  const dir = (base) => base?.slice(0, base.lastIndexOf("/") + 1) || null;
  const [artBase, base] = await Promise.all([art, layout]);
  return { artBase: dir(artBase), base: dir(base), official };
}

/**
 * Read the layers of a lobby in the prefab's draw order with its offsets and scales; skeletons the prefab keeps
 * inactive (talk-only scenes) are left out. Without prefab data every skeleton sits at the standard place,
 * backgrounds first.
 * @param {object|null} [mod]
 * @param {object|null} [sources]
 * @param {object|null} [framing]
 * @param {object} [options]
 * @param {boolean} [options.camera] Read the camera's art.
 * @returns {Promise<object[]>} The layers: name, offset, scale, data and files.
 */
export async function layersOf(
  core,
  mod = null,
  sources = null,
  framing = null,
  { signal, official = false, camera = false } = {},
) {
  const { artBase, base } = sources || (await sourcesOf(core, mod, false, signal, official, camera));
  if (!artBase) throw new Error(`Lobby source unavailable: ${core}`);
  const read = (url) =>
    withTimeout(
      fetch(url, { signal }).then((r) => {
        if (!r.ok) throw new Error(`${url}: HTTP ${r.status}`);
        return r.json();
      }),
      15000,
      url,
      { signal },
    ).catch((err) => {
      throw atStep(err, url, "metadata");
    });
  const [files, info] = await Promise.all([read(`${artBase}files.json`), base ? read(`${base}lobby.json`) : null]);
  if (!Array.isArray(files) || files.some((f) => typeof f !== "string")) throw new Error("Invalid lobby file list");
  const atlasOf = pairAtlases(files),
    skels = [...atlasOf.keys()];
  const match = (n) => skels.find((s) => s.toLowerCase() === n.toLowerCase());
  const defaults = (name, index) => ({
    name,
    file: name,
    x: 0,
    y: framing ? 0 : -962,
    scale: 1,
    index,
    skin: null,
    animation: null,
  });
  const unpaired = files.filter((f) => f.endsWith(".skel") && !atlasOf.has(f.slice(0, -5)));
  if (unpaired.length) throw new Error(`No unambiguous lobby atlas: ${unpaired.join(", ")}`);
  // equal sorting orders (akari_home before akari_bg in the prefab): background-named skeletons first; the scenes only
  // the intro shows (Akari's, Aru's) are loaded too, hidden until it shows them (introOnly)
  let list = (info?.layers || [])
    .filter((l) => (l.active || l.intro) && match(l.name))
    .map((l) => ({ ...l, file: match(l.name), introOnly: !l.active }))
    .sort((a, b) => a.order - b.order || isBg(b.name) - isBg(a.name) || a.index - b.index);
  if (!list.length) {
    list = skels.sort((a, b) => isBg(b) - isBg(a) || a.localeCompare(b)).map(defaults);
  } else {
    // Repacked mods can rename or add their character while retaining the official background.
    const known = new Set((info?.layers || []).map((l) => match(l.name)));
    const added = skels.filter((s) => !known.has(s));
    const missing = (info?.layers || []).filter((l) => l.active && !match(l.name));
    if (missing.length && !added.length)
      throw new Error(`Missing lobby layers: ${missing.map((l) => l.name).join(", ")}`);
    list.push(...added.map(defaults));
  }
  for (const l of list)
    if (![l.x, l.y, l.scale].every(Number.isFinite) || l.scale <= 0)
      throw new Error(`Invalid lobby layer transform: ${l.name}`);
  return {
    info,
    layers: list.map((l) => ({
      ...l,
      spine: `${artBase}${encodeURIComponent(l.file)}`,
      atlas: `${artBase}${encodeURIComponent(atlasOf.get(l.file))}.atlas`,
    })),
  };
}
/**
 * Pair a lobby's skeleton files with their atlases: the atlas of the same name, else of the same name in
 * another case (Juri: juri_home.skel, Juri_home.atlas), else the one atlas left for the one skeleton left
 * (CH9996_home.skel with CH0996_home.atlas); the game pairs them through the SkeletonDataAsset, not by name.
 * Skeletons without one are left out.
 * @param {string[]} files
 * @returns {Map<string, string>} Skeleton name to atlas name.
 */
export function pairAtlases(files) {
  const skels = files.filter((f) => f.endsWith(".skel")).map((f) => f.slice(0, -5));
  const atlases = files.filter((f) => f.endsWith(".atlas")).map((f) => f.slice(0, -6));
  const out = new Map(),
    free = new Set(atlases);
  for (const s of skels)
    if (free.has(s)) {
      out.set(s, s);
      free.delete(s);
    }
  for (const s of skels) {
    if (out.has(s)) continue;
    const a = [...free].find((x) => x.toLowerCase() === s.toLowerCase());
    if (a) {
      out.set(s, a);
      free.delete(a);
    }
  }
  const left = skels.filter((s) => !out.has(s));
  if (left.length === 1 && free.size === 1) out.set(left[0], [...free][0]);
  return out;
}

// voice clip names as Kivo keys them: "Images/sound/Hoshino_MemorialLobby_3_1.ogg" -> "hoshino_memoriallobby_3_1"
const clipId = (name) =>
  name
    .toLowerCase()
    .replace(/\\/g, "/")
    .split("/")
    .pop()
    .replace(/\.(ogg|wav|mp3)$/, "")
    .replace(/_new$/, "");
/**
 * The looping idle of a layer (rest-pose.js, which the thumbnails choose with too).
 * @returns {string|null}
 */
export function idleOf(data, layer, info = current?.info) {
  if (layer.rest) return layer.rest.name;
  return lobbyIdle(data, { idle: info?.idle?.[layer.name], animation: layer.animation });
}
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
const CELL = 16,
  SAMPLES = 6;
const QUAD = [0, 1, 2, 2, 3, 0];
// Geometry only, never art or a live player: bound both the retained occupancy masks and revision keys.
function rectCache(maxBytes = 8 * 1024 * 1024, maxEntries = 24) {
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
const paintedRects = rectCache();
function geometryKey(loaded, G) {
  const { artBase, base, framing, info } = current || {};
  // Only immutable revision URLs identify parsed skeleton/atlas content; unversioned inputs are measured afresh.
  if (!/\/[a-f0-9]{64}\/$/.test(artBase || "") || (base && !/\/[a-f0-9]{64}\/$/.test(base))) return null;
  // Keep every authored layer field and the resolved rest pose, including skin, transforms and ignorePins.
  // The complete layout also covers pins, idle/clip selection and any future framing inputs.
  return JSON.stringify([artBase, base, framing, G, info, loaded.map(({ player, ...layer }) => layer)]);
}
function cachedPaintedRect(loaded, G) {
  const key = geometryKey(loaded, G),
    cached = paintedRects.get(key);
  if (cached) return cached;
  const value = paintedRect(loaded, G);
  paintedRects.set(key, value);
  return value;
}
// the grid: three game views wide and high around the game's view
function gridOf(G) {
  return { X0: G.x - G.w, Y0: G.y - G.h, gw: Math.ceil((G.w * 3) / CELL), gh: Math.ceil((G.h * 3) / CELL) };
}
// a reusable skeleton of a layer (its skin set), posed by tracks [track, animation, time, loop] with no mixing
function poser(l) {
  const data = l.player.skeleton.data,
    sk = new spine.Skeleton(data),
    st = new spine.AnimationState(new spine.AnimationStateData(data));
  if (l.skin)
    try {
      sk.setSkinByName(l.skin);
    } catch {
      /* the default skin */
    }
  return (tracks) => {
    st.clearTracks();
    sk.setToSetupPose();
    for (const [track, name, time, loop] of tracks) {
      const e = st.setAnimation(track, name, loop);
      e.mixDuration = 0;
      e.trackTime = time;
    }
    if (tracks.length) {
      st.update(0);
      st.apply(sk);
    }
    // the bones the prefab pins, as the players draw them
    Pins.pin(sk, l.name, l.ignorePins ? null : current?.info?.pins);
    sk.updateWorldTransform(spine.Physics.reset); // physics at rest, as a new skeleton's: no state from the last pose
    return sk;
  };
}
// sets the cells whose centre one of the posed layer's painted triangles covers, for each target [out, rects]: in
// `out`, within its cell rects ([i0, i1, j0, j1], inclusive); additive FX and see-through parts never count
function paint(grid, l, sk, targets) {
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
const unitsOf = (grid, r) => ({
  x0: grid.X0 + r.i0 * CELL,
  x1: grid.X0 + (r.i1 + 1) * CELL,
  y0: grid.Y0 + r.j0 * CELL,
  y1: grid.Y0 + (r.j1 + 1) * CELL,
});
function paintedRect(loaded, G) {
  const grid = gridOf(G),
    { X0, Y0, gw, gh } = grid,
    all = [[0, gw - 1, 0, gh - 1]];
  const acc = new Uint8Array(gw * gh).fill(1),
    cur = new Uint8Array(gw * gh),
    any = new Uint8Array(gw * gh);
  const posers = loaded.map((l) => [l, poser(l), idleOf(l.player.skeleton.data, l)]);
  for (let k = 0; k < SAMPLES; k++) {
    cur.fill(0);
    for (const [l, pose, anim] of posers) {
      const time = anim ? (l.rest?.time || 0) + (l.player.skeleton.data.findAnimation(anim).duration * k) / SAMPLES : 0;
      paint(grid, l, pose(anim ? [[0, anim, time, true]] : []), [[cur, all]]);
    }
    for (let i = 0; i < acc.length; i++) {
      acc[i] &= cur[i];
      any[i] |= cur[i];
    }
  }
  const full = (i0, i1, j0, j1) => {
    for (let j = j0; j <= j1; j++) for (let i = i0; i <= i1; i++) if (!acc[j * gw + i]) return false;
    return true;
  };
  const r = {
    i0: Math.floor((G.x + 1 - X0) / CELL),
    i1: Math.floor((G.x + G.w - 1 - X0) / CELL),
    j0: Math.floor((G.y + 1 - Y0) / CELL),
    j1: Math.floor((G.y + G.h - 1 - Y0) / CELL),
  };
  const covered = full(r.i0, r.i1, r.j0, r.j1);
  // art that does not cover the game view (a mod): start from the covered middle of it
  while (!full(r.i0, r.i1, r.j0, r.j1) && r.i1 - r.i0 > 8 && r.j1 - r.j0 > 8) {
    r.i0++;
    r.i1--;
    r.j0++;
    r.j1--;
  }
  // then grow each side while the next column or row is fully painted; parts parked off screen behind a transparent
  // gap (intro props, a second stage set) are never reached
  for (let moved = true; moved; ) {
    moved = false;
    if (r.i0 > 0 && full(r.i0 - 1, r.i0 - 1, r.j0, r.j1)) {
      r.i0--;
      moved = true;
    }
    if (r.i1 < gw - 1 && full(r.i1 + 1, r.i1 + 1, r.j0, r.j1)) {
      r.i1++;
      moved = true;
    }
    if (r.j0 > 0 && full(r.i0, r.i1, r.j0 - 1, r.j0 - 1)) {
      r.j0--;
      moved = true;
    }
    if (r.j1 < gh - 1 && full(r.i0, r.i1, r.j1 + 1, r.j1 + 1)) {
      r.j1++;
      moved = true;
    }
  }
  // and the box of the scene's art in the idle poses (the Unlocked limit, until the clips widen it)
  const box = sceneBox(any, gw, gh, r);
  return { ...unitsOf(grid, r), covered, cells: r, any, whole: { ...unitsOf(grid, box), cells: box } };
}

// --- the zoom-out limit, safe for every clip: P, measured on idle poses, shrunk until no clip the lobby plays uncovers
// a cell near its edge (Kurumi's last dialogue moves the scene and showed a blank strip at the widest view) ---
const BAND = 24; // cells inside each side of P that are checked: a scene moving up to 384 units
const CLIP_STEP = 0.1; // s between the poses sampled from a clip
const SLICE_MS = 8; // the measurement runs in slices of the main thread, after the lobby is shown
const DRAG_CLIP = /^(Pat|Look|Pinch|HandFollow|Touch)(End)?_\d+_M$/;
const safeRects = rectCache(2 * 1024 * 1024); // the same source and geometry identity as the initial painted rect

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
// one pose per step: the cells near P's sides that the pose leaves unpainted (Full Art), and the cells outside P that
// it paints (Unlocked); every layer is drawn, the others idle
function* measureSafe(loaded, meta, G) {
  const grid = gridOf(G),
    P = meta.painted.cells,
    K = BAND;
  const bands = [
    [P.i0, Math.min(P.i1, P.i0 + K - 1), P.j0, P.j1],
    [Math.max(P.i0, P.i1 - K + 1), P.i1, P.j0, P.j1],
    [P.i0, P.i1, P.j0, Math.min(P.j1, P.j0 + K - 1)],
    [P.i0, P.i1, Math.max(P.j0, P.j1 - K + 1), P.j1],
  ];
  const bad = new Uint8Array(grid.gw * grid.gh),
    cur = new Uint8Array(grid.gw * grid.gh),
    any = meta.any.slice();
  // `any` adds up over the idle and every pose, up to K cells past what the idle paints: a dialogue that moves or
  // scales the whole scene (CH0243, Airi's CH0251) does not open the limit to its whole path
  const W = meta.painted.whole.cells,
    reach = {
      i0: Math.max(0, W.i0 - K),
      i1: Math.min(grid.gw - 1, W.i1 + K),
      j0: Math.max(0, W.j0 - K),
      j1: Math.min(grid.gh - 1, W.j1 + K),
    };
  const targets = [
    [cur, bands],
    [any, around(P, reach)],
  ];
  const layers = loaded.map((l) => ({
    ...l,
    data: l.player.skeleton.data,
    idle: idleOf(l.player.skeleton.data, l),
    main: l.name === meta.main,
  }));
  const posers = layers.map(poser);
  const plan = clipPoses(
    layers,
    current?.info,
    meta.sequences.map((s) => s.anim),
    (l, name) => clipOf(l.player, name),
  );
  for (const [index, tracks] of plan) {
    cur.fill(0);
    const t = tracks[tracks.length - 1][2];
    layers.forEach((l, j) => {
      const idleDur = l.idle ? l.data.findAnimation(l.idle).duration : 0;
      const pose = j === index ? tracks : l.idle ? [[0, l.idle, idleDur ? t % idleDur : 0, true]] : [];
      paint(grid, l, posers[j](pose), targets);
    });
    for (const [i0, i1, j0, j1] of bands)
      for (let j = j0; j <= j1; j++) for (let i = i0; i <= i1; i++) if (!cur[j * grid.gw + i]) bad[j * grid.gw + i] = 1;
    yield;
  }
  const s = sceneBox(any, grid.gw, grid.gh, P),
    box = {
      i0: Math.max(s.i0, reach.i0),
      i1: Math.min(s.i1, reach.i1),
      j0: Math.max(s.j0, reach.j0),
      j1: Math.min(s.j1, reach.j1),
    };
  return {
    ...unitsOf(grid, shrinkRect(P, bad, grid.gw)),
    poses: plan.length,
    whole: { ...unitsOf(grid, box), cells: box },
  };
}
// the zoom-out limit (a setting): the art one cell in, and never less than the game's view; art that hardly covers the
// view keeps the game's view. The painted rect counts a 16-unit cell as painted by its centre, so the art can end a
// few units before its border.
function outerOf(edge, full, sparse) {
  if (sparse) return rectOf(full);
  return {
    x0: Math.min(edge.x0 + CELL, full.x),
    x1: Math.max(edge.x1 - CELL, full.x + full.w),
    y0: Math.min(edge.y0 + CELL, full.y),
    y1: Math.max(edge.y1 - CELL, full.y + full.h),
  };
}
// measure the limits over the clips in the background (once per lobby in a session), then narrow Full Art to what
// every clip keeps painted and widen Unlocked to what any of them paints
function measureClips() {
  const c = current,
    m = c?.meta;
  if (!m || m.sparse || m.safe || c.measuring || !c.loaded) return;
  const key = geometryKey(c.loaded, game());
  const done = (rect) => {
    if (current !== c) return;
    c.measuring = false;
    m.safe = rect;
    m.outer = outerOf(rect, m.full, false);
    m.whole = union(rect.whole, rectOf(m.full));
    reclamp();
  };
  const cached = safeRects.get(key);
  if (cached) {
    done(cached);
    return;
  }
  c.measuring = true;
  const job = measureSafe(c.loaded, m, game());
  let busy = 0;
  const slice = () => {
    if (current !== c) return;
    const from = performance.now(),
      until = from + SLICE_MS;
    let step;
    try {
      do step = job.next();
      while (!step.done && performance.now() < until);
    } catch (err) {
      console.warn("zoom-out limit over the clips", err); // keep the idle poses' limit
      c.measuring = false;
      return;
    }
    busy += performance.now() - from;
    if (!step.done) {
      setTimeout(slice, 0);
      return;
    }
    const rect = { ...step.value, ms: Math.round(busy) };
    safeRects.set(key, rect);
    done(rect);
  };
  setTimeout(slice, 0);
}
// the layers' setup-pose bounds, the painted area of the fallback for art that hardly covers the view
function boundsRect(loaded) {
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
// Every pose the lobby rests in is painted: the idle and every clip (clipPoses). The intro does not count: some
// intros move the whole scene (Mika (Swimsuit)'s lifts it off her feet for seconds) and the view the user frames
// is the lobby at rest, so such an intro may show the camera's clear colour past the art. A cell outside the
// game's view that some pose leaves unpainted is `bad`: a view may take in no bad cell. Cells inside the game's
// view never count: what the game itself shows there is the lobby as made.
function* measureFraming(all, loaded, meta, G, info) {
  const grid = gridOf(G),
    { gw, gh, X0, Y0 } = grid,
    n = gw * gh;
  const bad = new Uint8Array(n),
    cur = new Uint8Array(n),
    whole = [[cur, [[0, gw - 1, 0, gh - 1]]]];
  const outside = new Uint8Array(n);
  for (let j = 0; j < gh; j++)
    for (let i = 0; i < gw; i++) {
      const x = X0 + (i + 0.5) * CELL,
        y = Y0 + (j + 0.5) * CELL;
      outside[j * gw + i] = x < G.x || x > G.x + G.w || y < G.y || y > G.y + G.h ? 1 : 0;
    }
  const mark = () => {
    for (let k = 0; k < n; k++) if (outside[k] && !cur[k]) bad[k] = 1;
  };
  const layers = loaded.map((l) => ({
    ...l,
    data: l.player.skeleton.data,
    idle: idleOf(l.player.skeleton.data, l),
    main: l.name === meta.main,
  }));
  const posers = new Map(layers.map((l) => [l.name, poser(l)]));
  const idlePose = (l, t) => {
    const d = l.idle ? l.data.findAnimation(l.idle)?.duration || 0 : 0;
    return l.idle ? [[0, l.idle, d ? t % d : 0, true]] : [];
  };
  // the lobby at rest and through its clips
  const plan = [
    [-1, []],
    ...clipPoses(
      layers,
      info,
      meta.sequences.map((s) => s.anim),
      (l, name) => clipOf(l.player, name),
    ),
  ];
  let poses = 0;
  for (const [index, tracks] of plan) {
    cur.fill(0);
    const t = tracks.length ? tracks[tracks.length - 1][2] : 0;
    layers.forEach((l, j) => paint(grid, l, posers.get(l.name)(j === index ? tracks : idlePose(l, t)), whole));
    mark();
    poses++;
    yield;
  }
  return { ...framingLimits(G, grid, bad), poses };
}
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
let cameraOn = false;
const framingCache = new Map(); // geometry key -> measured limits, for the session
function measureCamera() {
  const c = current,
    m = c?.meta;
  if (!m || c.camera?.lim?.sum || c.camera?.measuring || !c.loaded) return;
  const G = game(),
    key = geometryKey(c.all || c.loaded, G);
  c.camera = { lim: { G, grid: null, sum: null, wMax: G.w }, measuring: true, started: performance.now() };
  const done = (lim) => {
    if (current !== c) return;
    c.camera = { lim, measuring: false, ms: lim.ms };
    reclamp();
  };
  const cached = key && framingCache.get(key);
  if (cached) {
    done(cached);
    return;
  }
  const job = measureFraming(c.all || c.loaded, c.loaded, m, G, c.info);
  let busy = 0;
  const slice = () => {
    if (current !== c) return;
    const from = performance.now(),
      until = from + SLICE_MS;
    let step;
    try {
      do step = job.next();
      while (!step.done && performance.now() < until);
    } catch (err) {
      console.warn("camera framing limits", err); // keep the game's view as the limit
      c.camera.measuring = false;
      return;
    }
    busy += performance.now() - from;
    if (!step.done) {
      setTimeout(slice, 0);
      return;
    }
    const lim = { ...step.value, ms: Math.round(busy) };
    if (key) framingCache.set(key, lim);
    done(lim);
  };
  setTimeout(slice, 0);
}
/**
 * Turn the camera framing on or off: on, the lobby rests in fullscreen (no intro), the view starts at the
 * game's and measures how far it may go; off, the view goes back to the game's.
 */
export function camera(on) {
  cameraOn = !!on;
  if (!current?.meta) return;
  current.host?.classList.toggle("camera-framing", cameraOn);
  Touch.setPanOnly(cameraOn);
  if (cameraOn) {
    current.intro?.skip();
    measureCamera();
  }
  openEdge();
  resetZoom();
}
/**
 * The state for the camera's controls and checks: the view, the game's view, the limits and what the shutter
 * would keep.
 * @returns {object|null} The state; null before a lobby is shown.
 */
export const cameraState = () => {
  if (!current?.meta) return null;
  const G = game(),
    V = current.target || current.view,
    lim = current.camera?.lim;
  return {
    on: cameraOn,
    measuring: !!current.camera?.measuring,
    view: { ...V },
    game: G,
    prefab: !!current.info?.prefab,
    widest: lim?.sum ? lim.wMax : G.w,
    narrowest: G.w / ZOOM_IN,
    poses: lim?.poses || 0,
    ms: current.camera?.ms,
    params: cameraParams(V, G),
  };
};

// --- views: {x, y, w, h} in NGUI units, y up ---
// the largest 16:9 view no wider than w that fits P, centred on (cx, cy) as far as P allows
function fit(P, w, cx, cy) {
  const W = Math.min(w, P.x1 - P.x0, ((P.y1 - P.y0) * 16) / 9),
    H = (W * 9) / 16;
  return { x: clamp(cx - W / 2, P.x0, P.x1 - W), y: clamp(cy - H / 2, P.y0, P.y1 - H), w: W, h: H };
}
// left view: the head-pat point T near 28 % across and 34 % down, as wide as possible without leaving P. A first pass
// keeps T within 16-34 % across (zoom up to 1.6 x the game's view), a second allows 44 % and 1.9 x; else 1.9 x as near
// as P allows.
function leftView(P, T, G) {
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
const viewOf = (m, md = mode) => ({ ...(md === "full" ? m.full : m.left) });
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
// the shared view of this frame, eased towards its target once per frame (every player asks in its frame hook; the
// document timeline's time is the same for all of them within a frame)
function viewNow() {
  const c = current;
  if (!c?.view) return null;
  const now = document.timeline?.currentTime ?? performance.now();
  if (now !== c.viewAt) {
    const dt = c.viewAt == null ? 0 : clamp((now - c.viewAt) / 1000, 0, 0.1);
    c.viewAt = now;
    const v = c.view,
      t = c.target;
    if (t && v !== t) {
      const k = 1 - Math.exp(-dt / ZOOM_TAU);
      const w = v.w + (t.w - v.w) * k;
      c.view = { x: v.x + (t.x - v.x) * k, y: v.y + (t.y - v.y) * k, w, h: (w * 9) / 16 };
      if (Math.abs(t.w - w) < 0.05 && Math.abs(t.x - c.view.x) < 0.05 && Math.abs(t.y - c.view.y) < 0.05) c.view = t;
      if (photoFrame) photoMatte(c.view);
      // sparse art (the fallback): the feather and the extension move with the art's border, while the view eases
      // (throttled) and once it settles
      if (c.meta?.sparse && (c.view === t || now - (c.featherAt ?? -Infinity) >= FEATHER_MS)) {
        c.featherAt = now;
        feather(c.view);
      }
    }
  }
  c.caption?.reposition();
  return c.view;
}
function setView(view, immediate) {
  current.target = view;
  if (immediate) {
    current.view = view;
    current.caption?.reposition();
  }
}

// the lobby's frame, head-pat point and talk sequences
function measure(loaded) {
  if (current.framing) return measureSpecial(loaded);
  const info = current.info,
    G = game();
  const main =
    loaded.find((l) => l.name === info?.main) ||
    loaded.find((l) => l.player.skeleton.data.findBone("Touch_Point")) ||
    loaded[loaded.length - 1];
  const data = main.player.skeleton.data;
  const { any, ...painted } = cachedPaintedRect(loaded, G); // any: the cells some idle pose paints
  // a lobby whose art covers less than half the view (a character-only mod): the game view with the old edge
  // extension and feather, measured on the skeletons' bounds
  const area = (r) => Math.max(0, r.x1 - r.x0) * Math.max(0, r.y1 - r.y0);
  const sparse = !painted.covered && area(painted) < 0.5 * G.w * G.h;
  const edge = sparse ? boundsRect(loaded) : painted;
  // the head-pat point in the idle pose (Touch_Point, else the prefab's pat box, else the top third of the view)
  const sk = new spine.Skeleton(data),
    idle = idleOf(data, main);
  sk.setToSetupPose();
  if (main.skin) {
    sk.setSkinByName(main.skin);
    sk.setSlotsToSetupPose();
  }
  if (idle) {
    const st = new spine.AnimationState(new spine.AnimationStateData(data));
    st.setAnimation(0, idle, true).trackTime = main.rest?.time || 0;
    st.update(0);
    st.apply(sk);
  }
  Pins.pin(sk, main.name, main.ignorePins ? null : info?.pins);
  sk.updateWorldTransform(spine.Physics.update);
  const bone = sk.findBone("Touch_Point") || sk.findBone("Touch_Eye");
  const pat = info?.drags?.find((d) => d.kind === "pat")?.rect;
  let touch = bone
    ? { x: main.x + bone.worldX * main.scale, y: main.y + bone.worldY * main.scale }
    : pat
      ? { x: (pat[0] + pat[2]) / 2, y: (pat[1] + pat[3]) / 2 }
      : { x: 0, y: G.y + G.h * 0.66 };
  const full = sparse
    ? overlaps(edge, rectOf(G))
      ? G
      : containingView(edge)
    : fit(painted, G.w, G.x + G.w / 2, G.y + G.h / 2);
  if (
    !Number.isFinite(touch.x) ||
    !Number.isFinite(touch.y) ||
    touch.x < edge.x0 ||
    touch.x > edge.x1 ||
    touch.y < edge.y0 ||
    touch.y > edge.y1
  )
    touch = { x: (edge.x0 + edge.x1) / 2, y: edge.y0 + (edge.y1 - edge.y0) * 0.66 };
  const sequences = data.animations
    .map((a) => /^Talk_(\d+)_M$/.exec(a.name))
    .filter(Boolean)
    .map((m) => {
      const anim = m[0],
        a = data.findAnimation(anim);
      // the voice cues: the "Talk" event whose string names the clip (every lobby), or an event named after the
      // clip (older lobbies only), with its time
      const cues = [];
      for (const e of a.timelines.filter((tl) => tl instanceof spine.EventTimeline).flatMap((tl) => tl.events)) {
        const s = e.stringValue ?? e.data.stringValue ?? "";
        const id =
          /^talk$/i.test(e.data.name) && /memoriallobby/i.test(s)
            ? clipId(s)
            : /memoriallobby/i.test(e.data.name)
              ? clipId(e.data.name)
              : null;
        if (id && !cues.some((c) => c.clip === id)) cues.push({ t: e.time, clip: id });
      }
      cues.sort((x, y) => x.t - y.t);
      return { anim, n: Number(m[1]), duration: a.duration, cues };
    })
    .sort((a, b) => a.n - b.n);
  // the painted rect counts a 16-unit cell as painted by its centre, so the art can end a few units before its
  // border: the left view keeps one cell inside it
  const inner = sparse
    ? edge
    : { ...edge, x0: edge.x0 + CELL, x1: edge.x1 - CELL, y0: edge.y0 + CELL, y1: edge.y1 - CELL };
  // the zoom-out limits, until measureClips has narrowed Full Art to what every clip keeps painted and widened Unlocked
  // to what any of them paints (sparse art: its skeletons' bounds)
  const outer = outerOf(edge, full, sparse);
  const whole = union(sparse ? edge : painted.whole, rectOf(full));
  return {
    main: main.name,
    painted,
    any,
    edge,
    sparse,
    touch,
    full,
    outer,
    whole,
    safe: null,
    left: leftView(inner, touch, G),
    sequences,
  };
}

// Special slots have no memorial camera: derive their frame from settled, drawable attachments.
function measureSpecial(loaded) {
  const edge = loaded
    .map((l) => worldBounds(artBounds(l.player.skeleton), l))
    .filter(validRect)
    .reduce((a, b) => (a ? union(a, b) : b), null);
  const framing = specialFramingOf(current.framing, edge, loaded.length);
  const full = specialView(edge, framing);
  const left = specialView(edge, framing, 16 / 9, framing === "character" ? 0.25 : 0.5);
  current.specialGame = full;
  return {
    main: loaded.at(-1).name,
    edge,
    painted: edge,
    sparse: framing === "character",
    touch: { x: (edge.x0 + edge.x1) / 2, y: edge.y0 + (edge.y1 - edge.y0) * 0.8 },
    full,
    left,
    outer: rectOf(full),
    whole: union(edge, rectOf(full)),
    safe: null,
    sequences: [],
  };
}

/**
 * Switch between the left view and fullscreen: the old framing fades out over the new one, in step with the UI
 * slide; the zoom resets.
 * @param {"left"|"full"} m
 */
export function frame(m) {
  const changed = m !== mode;
  mode = m;
  if (current && m === "left") current.userFramed = false;
  if (!current?.meta) return;
  if (changed && !paused()) crossfade();
  setView(viewOf(current.meta), true);
  feather(current.view);
  if (ownsInput() && m !== "full") Touch.detach();
  else if (ownsInput() && wantTouch && !paused()) Touch.attach(current.host);
}
// a player's canvas on screen (not a layer the intro keeps hidden)
const shown = (p) => !!p.canvas && !p.parent?.classList.contains("lobby-off");
function crossfade() {
  const fade = current.host?.querySelector(".lobby-fade");
  if (!fade) return;
  const g = fade.getContext("2d");
  g.clearRect(0, 0, fade.width, fade.height);
  const ext = current.host.querySelector(".lobby-extend");
  if (ext && !ext.hidden) g.drawImage(ext, 0, 0, fade.width, fade.height);
  for (const p of players) if (shown(p)) g.drawImage(p.canvas, 0, 0, fade.width, fade.height);
  fade.style.transition = "none";
  fade.style.opacity = "1";
  void getComputedStyle(fade).opacity; // start the fade from 1 in this frame, together with the UI slide
  fade.style.transition = `opacity ${FADE_MS}ms ease`;
  fade.style.opacity = "0";
}

// fullscreen zoom at a stage point: from ZOOM_IN x to the game's view (never wider, so nothing the game never shows
// appears), or with the zoom-out setting to the widest view its limit allows; the width is clamped first, so the
// point under the pointer holds at the limits too
const rectOf = (v) => ({ x0: v.x, x1: v.x + v.w, y0: v.y, y1: v.y + v.h });
const union = (a, b) => ({
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
const bounds = () => zoomBounds({ mode: zoomMode, custom: zoomCustom }, current.meta, game());
const widest = (B) => Math.min(B.x1 - B.x0, ((B.y1 - B.y0) * 16) / 9);
let photoFrame = null;
/** Tell the lobby the photography frame, or none, so the view stays inside it. */
export function photography(frame) {
  photoFrame = frame;
  const el = current?.host?.querySelector(".lobby-view");
  if (!frame && el) el.style.clipPath = "";
  reclamp();
  if (current?.view) feather(current.view);
}
function clampView(v) {
  if (cameraOn && current.camera?.lim) return framingView(v, current.camera.lim);
  const B = photoFrame ? current.meta.edge : bounds();
  if (photoFrame) return lobbyCaptureView(v, B, photoFrame);
  return { x: clamp(v.x, B.x0, B.x1 - v.w), y: clamp(v.y, B.y0, B.y1 - v.h), w: v.w, h: v.h };
}
/**
 * Zoom the fullscreen view at a point.
 * @param {number} factor The factor; above 1 zooms in.
 * @param {number} sx The pointer's x in stage pixels.
 * @param {number} sy The pointer's y in stage pixels.
 */
export function zoomAt(factor, sx, sy) {
  if (mode !== "full" || !current?.meta || !(factor > 0)) return;
  current.userFramed = true;
  const T = current.target,
    full = current.meta.full;
  const fx = sx / STAGE.w,
    fy = 1 - sy / STAGE.h,
    ux = T.x + fx * T.w,
    uy = T.y + fy * T.h;
  if (cameraOn && current.camera?.lim) {
    // centred across: only the height under the pointer stays put
    const w = T.w / factor,
      h = (w * 9) / 16;
    setView(clampView({ x: 0, y: uy - fy * h, w, h }), false);
    return;
  }
  const w = clamp(
      T.w / factor,
      full.w / ZOOM_IN,
      photoFrame ? lobbyMaximumWidth(current.meta.edge, full.w, photoFrame) : Math.max(full.w, widest(bounds())),
    ),
    h = (w * 9) / 16;
  setView(clampView({ x: ux - fx * w, y: uy - fy * h, w, h }), false);
}
/**
 * Set the zoom-out setting: a view wider than the new limit comes back inside it, around the same centre; Full
 * Art and Unlocked measure the lobby's clips (once).
 * @param {{mode: string, custom?: number}} zoom One of ZOOM_MODES, and for "custom" how many game views across
 *     the view may be.
 */
export function setZoomLimit(zoom) {
  zoomMode = ZOOM_MODES.includes(zoom?.mode) ? zoom.mode : "art";
  zoomCustom = clamp(Number(zoom?.custom) || ZOOM_CUSTOM, 1, ZOOM_OUT_MAX);
  if (measuresClips()) measureClips();
  openEdge();
  reclamp();
}
const measuresClips = () => !current?.framing && (zoomMode === "art" || zoomMode === "unlocked");
// past the painted art (Unlocked, Custom) the lobby shows what the game's lobby camera clears to, black
// (Persistent/Camera: solid colour clear), graded with PP on: the bottom layer draws it (linear.js setClear), the page
// shows black behind it without WebGL2; sparse art keeps the edge extension
function openEdge() {
  const open =
    (cameraOn || zoomMode === "unlocked" || zoomMode === "custom") && !!current?.meta && !current.meta.sparse;
  current?.host?.classList.toggle("open-edge", open);
}
/**
 * Set the game's PP option: off draws the lobby without its post-processing volumes (the next frame shows it).
 */
export function setPostProcessing(on) {
  Linear.setPostEnabled(on);
}
// a view wider than the limit now allows eases back inside it, around the same centre
function reclamp() {
  if (!current?.meta || !current.target || mode !== "full") return;
  const T = current.target,
    full = current.meta.full;
  if (photoFrame || cameraOn) {
    setView(clampView(T), false);
    return;
  }
  if (T.w <= full.w + 1e-6) {
    setView(clampView(T), false);
    return;
  }
  const cx = T.x + T.w / 2,
    cy = T.y + T.h / 2,
    w = Math.max(full.w, Math.min(T.w, widest(bounds()))),
    h = (w * 9) / 16;
  setView(clampView({ x: cx - w / 2, y: cy - h / 2, w, h }), false);
}
/** Pan by a stage-pixel drag (the content follows the pointer); it only moves while zoomed in. */
export function panBy(dx, dy) {
  if (mode !== "full" || !current?.meta) return;
  current.userFramed = true;
  const T = current.target;
  if (cameraOn) dx = 0; // the camera moves up and down only
  setView(clampView({ x: T.x - (dx / STAGE.w) * T.w, y: T.y + (dy / STAGE.h) * T.h, w: T.w, h: T.h }), false);
}
// the view a zoom or pan is easing to, and putting one back (lobby-touch.js: a scroll taken for a wheel that turns out
// a trackpad swipe)
const viewTarget = () => current?.target || null;
function setViewTarget(v) {
  if (mode !== "full" || !current?.meta || !v) return;
  current.userFramed = true;
  setView(clampView(v), false);
}
/** Reset the zoom to the game's view. */
export function resetZoom() {
  if (!current?.meta) return;
  current.userFramed = false;
  setView(viewOf(current.meta), true);
  feather(current.view);
}
/**
 * For checks: the rects involved, the current view, the zoom relative to the game's view and the character's
 * tracks.
 * @returns {object|undefined} The measurements; undefined before a lobby is shown.
 */
export const limits = () =>
  current?.meta && {
    game: game(),
    painted: current.meta.painted,
    full: current.meta.full,
    left: current.meta.left,
    view: current.view,
    target: current.target,
    touch: current.meta.touch,
    zoom: current.meta.full.w / current.view.w,
    mode,
    outer: current.meta.outer,
    whole: current.meta.whole,
    zoomLimit: { mode: zoomMode, custom: zoomCustom },
    bounds: bounds(),
    safe: current.meta.safe,
    measuring: !!current.measuring,
    tracks: mainPlayer()?.animationState?.tracks.map((t) => t?.animation?.name || null) || [],
  };

/**
 * The switch card's still: a 450 x 253 crop around the face inside the left view (so fully painted), returned
 * in canvas pixels; the card's mask fades its edges, and its text covers the right third.
 * @returns {{x: number, y: number, w: number, h: number}|null} The crop; null before a lobby is shown.
 */
export function stillRect(canvasW, canvasH) {
  const m = current?.meta;
  if (!m) return null;
  const v = m.left,
    T = m.touch,
    G = game();
  if (current.framing) return { x: 0, y: 0, w: canvasW, h: canvasH };
  const w = clamp(G.w * 0.5, v.w * 0.5, v.w),
    h = (w * 253) / 450;
  // the head-pat point (top of the head) at 32 % across and 20 % down the crop: the face in the card's clear part
  const x = clamp(T.x - 0.32 * w, v.x, v.x + v.w - w),
    top = clamp(T.y + 0.2 * h, v.y + h, v.y + v.h);
  return {
    x: ((x - v.x) / v.w) * canvasW,
    y: ((v.y + v.h - top) / v.h) * canvasH,
    w: (w / v.w) * canvasW,
    h: (h / v.h) * canvasH,
  };
}

// share: the lobby's bottom player, whose context this one draws with (lobby-stage.js), or null
function makePlayer(host, layer, fx, share, signal) {
  const div = document.createElement("div");
  div.className = "lobby-layer";
  if (layer.introOnly) {
    div.style.visibility = "hidden";
    div.classList.add("lobby-off");
  }
  host.appendChild(div);
  return loadPlayer(
    (config) => Stage.make(share, () => new spine.SpinePlayer(div, config)),
    {
      skeleton: `${layer.spine}.skel`,
      atlas: layer.atlas || `${layer.spine}.atlas`,
      animation: undefined,
      premultipliedAlpha: false,
      alpha: true,
      backgroundColor: "#00000000",
      showControls: false,
      showLoading: false,
      preserveDrawingBuffer: true,
      defaultMix: current?.info?.defaultMix?.[layer.name] ?? 0.2,
      viewport: layerViewport(game(), layer),
      // every frame: the frames of the players drawn with this one's context (lobby-stage.js), the random-timing clips
      // that are due (before the animation update, as the game's coroutines run before the next Update), the shared
      // view, no spine-player viewport transitions of its own
      frame: (p, delta) => {
        // SpinePlayer starts with paused=true, then calls play() AFTER success. Capture its settled playback
        // state here, not the constructor's temporary pause, so activation does not restore that loading state.
        if (paused() && !current?.preparingFrame) {
          freezePlayer(p, current);
          delta = 0;
        }
        if (!paused() || current?.preparingFrame) current?.intro?.frame(p, delta);
        // A timeline may finish by emptying track 0. Its idle must resume once that mix has ended.
        if (!current?.intro?.pending() && !talk) resumeRest(p, layer);
        Stage.frame(p);
        if (!paused()) p.bamtRuns?.tick(delta);
        const v = viewNow();
        if (v) {
          p.currentViewport = layerViewport(v, layer);
          p.previousViewport = null;
        }
        Linear.begin(p);
      },
      // in linear light like the game (linear.js): the slot colours as its shader sees them, then the frame drawn into
      // its framebuffer goes onto the canvas; the particle effects that sort before the skeleton draw after the clear,
      // the others after it
      update: (p, delta) => {
        Linear.tint(p);
        fx?.below(p, paused() || current?.preparingFrame ? 0 : delta);
      },
      draw: (p) => {
        fx?.above(p);
        Linear.end(p);
        if (current && !Stage.hostOf(p)) current.drawnAt = performance.now();
      },
      // after the animation, before the world transform: the sound keys its track entries have reached, and the
      // dragged bones follow the pointer
      updateWorldTransform: (p, delta) => {
        if (!paused()) p.bamtSounds?.tick();
        Pins.apply(p, layer.ignorePins ? null : current?.info?.pins);
        if (ownsInput() && !paused()) Touch.apply(p, delta);
        p.skeleton.updateWorldTransform(spine.Physics.update);
      },
      success: (player) => {
        try {
          player.bamtLayer = layer;
          clocked(player);
          Stage.ready(player);
          if (layer.introOnly) Stage.setShown(player, false); // a scene only the intro shows
          // linear light; a player drawn with another's context once every player is in (show)
          if (!Stage.hostOf(player)) lightUp(player);
          if (layer.skin)
            try {
              player.skeleton.setSkinByName(layer.skin);
              player.skeleton.setSlotsToSetupPose();
            } catch {
              /* the default skin */
            }
          // the idle, and with it the clips it starts (its sync clips, the rest of its pool), as the game's OnEnable
          player.bamtSounds = new Sync.SoundKeys(sfx);
          player.bamtRuns = new Sync.Runs(player.animationState, {
            clip: (name) => clipOf(player, name),
            duration: (name) => player.skeleton.data.findAnimation(name)?.duration || 0,
            onEntry: (entry, c) => soundsOf(player, entry, c),
          });
          // its Spine events reach the particle effects, which some switch on and off (SpineEventToggleObjects)
          if (fx)
            player.animationState.addListener({
              event: (_entry, e) => fx.spineEvent(layer.name, e.data.name, e.intValue),
            });
          const idle = settle(player, layer, current?.info, Pins.pin);
          if (idle) soundsOf(player, player.animationState.getCurrent(0), clipOf(player, idle));
          for (const name of idleClips(current?.info, layer.name, idle, (n) => clipOf(player, n)))
            if (clipOf(player, name).track !== 0) playClip(player, name);
          if (paused()) player.stopRendering?.();
        } catch (err) {
          console.error("Lobby player setup failed", layer.name, err);
          throw err;
        }
      },
    },
    {
      signal,
      manual: !!current?.deferPlayback,
      onResume: () => {
        const c = current;
        if (!c || c.signal !== signal || paused()) return false;
        c.drawnAt = performance.now();
        c.introTime = undefined;
        c.pixelsChecked = false;
        c.viewAt = null;
        if (c.revealed) reveal(false);
        return !players.some((p) => p.bamtLayer === layer && Stage.hostOf(p));
      },
      onRecover: () => {
        if (current?.signal === signal) recoverView(current);
      },
      onError: (err) => {
        if (!current || signal.aborted) return;
        current.error = err;
        if (current.meta) failView(err, current.host, `${layer.spine}.skel`, "render");
      },
    },
  );
}

// A loaded player in linear light like the game (linear.js), with the game camera's post-processing (the prefab's
// volumes) placed in the camera's view whatever the player shows, the slot colours as each page's shader takes them
// and the game's page materials. A player drawn with another's context (lobby-stage.js) gets it only with that one in
// linear light, whose pass it shares.
function lightUp(player) {
  const layer = player.bamtLayer;
  if (!Linear.attach(player, Stage.hostOf(player))) return;
  Linear.setPost(player, current?.info?.post);
  Linear.setScreen(player, layerViewport(game(), layer));
  Linear.setCurves(player, SlotColours.curvesOf(layer));
  // Keep Aoi's authored glow blends out of the memorial lobby's fallback to Normal.
  Materials.attach(player, layer, {
    screen: () => layerViewport(game(), layer),
    base: current.base,
    load: current.deferPlayback ? visualImage : undefined,
    slotBlends: typeof current.core === "string" && current.core.toLowerCase() === "np0032_npc",
  });
}

// --- fallback only (art that does not cover the view): edge pixels stretched outwards and blurred ---
const EXT_W = 480,
  EXT_H = 270;
const inside = (v, P) => v.x >= P.x0 - 0.5 && v.x + v.w <= P.x1 + 0.5 && v.y >= P.y0 - 0.5 && v.y + v.h <= P.y1 + 0.5;
function rectOnStage(view, w, h) {
  const P = current.meta.edge;
  return {
    l: ((P.x0 - view.x) / view.w) * w,
    r: ((P.x1 - view.x) / view.w) * w,
    t: ((view.y + view.h - P.y1) / view.h) * h,
    b: ((view.y + view.h - P.y0) / view.h) * h,
  };
}
function drawExtension(canvas, view) {
  const g = canvas.getContext("2d");
  const src = document.createElement("canvas");
  src.width = EXT_W;
  src.height = EXT_H;
  const sg = src.getContext("2d");
  for (const p of players) if (shown(p)) sg.drawImage(p.canvas, 0, 0, EXT_W, EXT_H);
  const r = rectOnStage(view, EXT_W, EXT_H);
  const L0 = Math.max(0, Math.ceil(r.l)),
    R0 = Math.min(EXT_W, Math.floor(r.r)),
    T0 = Math.max(0, Math.ceil(r.t)),
    B0 = Math.min(EXT_H, Math.floor(r.b));
  g.clearRect(0, 0, EXT_W, EXT_H);
  g.drawImage(src, 0, 0);
  // clamp: the last column or row inside the painted area is repeated out to the edge
  if (R0 < EXT_W && R0 > 2) g.drawImage(src, R0 - 2, 0, 2, EXT_H, R0, 0, EXT_W - R0, EXT_H);
  if (L0 > 0 && L0 < EXT_W - 2) g.drawImage(src, L0, 0, 2, EXT_H, 0, 0, L0, EXT_H);
  const mid = document.createElement("canvas");
  mid.width = EXT_W;
  mid.height = EXT_H;
  mid.getContext("2d").drawImage(canvas, 0, 0);
  if (B0 < EXT_H && B0 > 2) g.drawImage(mid, 0, B0 - 2, EXT_W, 2, 0, B0, EXT_W, EXT_H - B0);
  if (T0 > 0 && T0 < EXT_H - 2) g.drawImage(mid, 0, T0, EXT_W, 2, 0, 0, EXT_W, T0);
}
// a view inside the painted area needs nothing; otherwise the sharp lobby fades out over the last stretch before its
// painted border, into the blurred extension
function feather(view) {
  if (photoFrame) {
    photoMatte(view);
    return;
  }
  const el = current?.host?.querySelector(".lobby-view"),
    ext = current?.host?.querySelector(".lobby-extend");
  if (!el || !current?.meta) return;
  clearInterval(extendTimer);
  const off = !!current.framing || inside(view, current.meta.edge);
  if (ext) ext.hidden = off;
  if (off) {
    el.style.webkitMaskImage = el.style.maskImage = "none";
    return;
  }
  const r = rectOnStage(view, STAGE.w, STAGE.h),
    f = 90,
    parts = [];
  if (r.r < STAGE.w) parts.push(`linear-gradient(90deg, #000 ${r.r - f}px, transparent ${r.r}px)`);
  if (r.l > 0) parts.push(`linear-gradient(270deg, #000 ${STAGE.w - r.l - f}px, transparent ${STAGE.w - r.l}px)`);
  if (r.b < STAGE.h) parts.push(`linear-gradient(180deg, #000 ${r.b - f}px, transparent ${r.b}px)`);
  if (r.t > 0) parts.push(`linear-gradient(0deg, #000 ${STAGE.h - r.t - f}px, transparent ${STAGE.h - r.t}px)`);
  const mask = parts.join(", ") || "none";
  el.style.webkitMaskImage = mask;
  el.style.maskImage = mask;
  el.style.webkitMaskComposite = parts.length > 1 ? "source-in" : "";
  el.style.maskComposite = parts.length > 1 ? "intersect" : "";
  // keep the extension in step with the animation (cheap: a 480 x 270 canvas a few times a second)
  const tick = () => current?.view && drawExtension(ext, current.view);
  tick();
  if (!paused()) extendTimer = setInterval(tick, 200);
}

// show(core, host) -> Promise<meta> (frame, sequences); resolves once every layer is in and framed
const lobbyLoads = latestLoad(),
  sourceLoads = latestLoad(),
  playerLoads = new WeakMap();
/**
 * Show a lobby in a host: load every layer, frame it and start it.
 * @param {object} [options]
 * @param {object|null} [options.mod]
 * @param {object|null} [options.framing]
 * @param {boolean} [options.camera] Show the camera's art.
 * @param {boolean} [options.deferPlayback] Do not start playback until reveal().
 * @returns {Promise<object>} The lobby's meta (frames, sequences); settles once every layer is in and framed.
 */
export function show(
  core,
  host,
  variant,
  { mod = null, framing = null, official = false, camera = false, deferPlayback = false } = {},
) {
  if (
    current &&
    (current.core !== core ||
      current.mod !== mod ||
      !!current.official !== !!official ||
      current.framing !== framing ||
      !current.meta)
  ) {
    lobbyLoads.cancel();
    clearView(host);
  }
  return sourceLoads.run(async (sourceSignal) => {
    if (!(await ensureSpine())) {
      if (!sourceSignal.aborted) clearView(host);
      return null;
    }
    if (sourceSignal.aborted) return null;
    let stopped = false;
    const url = spineBase(mod ? "mod" : official ? "official-lobby" : "lobby", mod || core);
    const abort = () => lobbyLoads.cancel();
    sourceSignal.addEventListener("abort", abort, { once: true });
    const run = async () => {
      let sources = await sourcesOf(core, mod, true, sourceSignal, official, camera);
      for (let attempt = 0; ; attempt++) {
        if (stopped || sourceSignal.aborted) return null;
        if (!sources.artBase)
          throw atStep(new Error("Lobby source unavailable"), url.replace(/skeleton$/, "manifest.json"), "manifest");
        if (
          current?.meta &&
          current.artBase === sources.artBase &&
          current.base === sources.base &&
          current.framing === framing &&
          current.host === host &&
          !current.signal.aborted &&
          players.length &&
          !current.error
        )
          return current.ready;
        try {
          return await lobbyLoads.run((signal) =>
            showNow(core, host, variant, mod, sources, signal, framing, deferPlayback),
          );
        } catch (err) {
          if (sourceSignal.aborted || attempt) throw err;
          const fresh = await sourcesOf(core, mod, true, sourceSignal, official, camera);
          if (stopped || sourceSignal.aborted) return null;
          if (fresh.artBase === sources.artBase && fresh.base === sources.base) throw err;
          console.warn("Lobby source changed during load", url, err);
          sources = fresh;
        }
      }
    };
    try {
      return await withTimeout(run(), 15000, "Detail lobby", { signal: sourceSignal });
    } catch (err) {
      stopped = true;
      if (!sourceSignal.aborted) {
        lobbyLoads.cancel();
        failView(err, host, url, "load");
      }
      throw err;
    } finally {
      sourceSignal.removeEventListener("abort", abort);
    }
  });
}
async function showNow(core, host, variant, mod, sources, signal, framing, deferPlayback = false) {
  clearView(host);
  if (!sources.artBase) throw new Error("Lobby source unavailable");
  const { base } = sources;
  const my = ++token;
  host.innerHTML =
    '<canvas class="lobby-extend" hidden></canvas><div class="lobby-view"></div><canvas class="lobby-fade"></canvas><div class="lobby-frost"></div><div class="lobby-subtitle" hidden></div>';
  const ext = host.querySelector(".lobby-extend"),
    viewHost = host.querySelector(".lobby-view"),
    fade = host.querySelector(".lobby-fade");
  ext.width = EXT_W;
  ext.height = EXT_H;
  fade.width = STAGE.w;
  fade.height = STAGE.h;
  current = {
    core,
    mod,
    ...sources,
    variant,
    host,
    signal,
    framing,
    deferPlayback,
    suspended: deferPlayback,
    inputWasInert: !!host.inert,
    info: null,
    meta: null,
    view: null,
    target: null,
    viewAt: null,
    visualLoads: new Map(),
    resumeAudio: new Set(),
    pendingSounds: [],
  };
  if (deferPlayback) host.inert = true;
  if (deferPlayback) {
    const c = current;
    c.lifecycle = previewLifecycle({
      suspend: () => {
        if (current === c) {
          c.backgroundPaused = true;
          freezeScene(c);
        }
      },
      resume: () => {
        if (current === c) {
          c.backgroundPaused = false;
          if (!c.suspended) resumeScene(c);
        }
      },
    });
    c.backgroundPaused = !c.lifecycle.active();
  }
  {
    // a lobby on screen stops under Now Loading (its voices, timers and intro included) and goes on afterwards
    const c = current;
    c.loadingFreeze = freezeWhileLoading({
      shown: () => current === c && !!c.revealed && !c.signal.aborted && onScreen(c.host),
      freeze: () => {
        if (current === c) {
          c.loadingPaused = true;
          freezeScene(c);
        }
      },
      thaw: () => {
        if (current === c) {
          c.loadingPaused = false;
          resumeScene(c);
        }
      },
    });
  }
  // Cancel the composition before individual load listeners, releasing shared-context guests first.
  current.cancel = () => {
    if (current?.signal === signal) clearView(host);
  };
  signal.addEventListener("abort", current.cancel, { once: true });
  talkIndex = -1;
  current.ready = (async () => {
    const owner = current;
    void dialogueModule()
      .then(({ createLobbyDialogue }) => {
        if (signal.aborted || my !== token) return;
        owner.caption = createLobbyDialogue({
          host: host.querySelector(".lobby-subtitle"),
          later: setTimeout,
          cancel: clearTimeout,
          geometry: () => ({
            info: owner.info,
            meta: owner.meta,
            view: owner.view,
            framing: owner.framing,
            full: dialogueFullscreen ?? mode === "full",
            bounds: dialogueViewBounds,
          }),
        });
        if (owner.captionLine) owner.caption.show(...owner.captionLine);
      })
      .catch((error) => {
        if (!signal.aborted && my === token) console.warn("Lobby dialogue unavailable", error);
      });
    const { info, layers } = await layersOf(core, mod, sources, framing, { signal });
    if (signal.aborted || my !== token) return null;
    if (!layers.length) throw new Error("No lobby skeletons found");
    current.info = info;
    setView(game(), true); // the first frames are already in the game's framing
    const fx = (current.fx =
      info?.fx?.systems?.length || info?.fx?.renderers?.length || info?.fx?.sprites?.length
        ? Particles.create(info.fx, { base, bone: boneOf, load: deferPlayback ? visualImage : undefined })
        : null);
    // every layer drawn with the bottom one's context (share), or each with its own
    const build = async (share) =>
      (
        await Promise.all(
          layers.map(async (layer) => {
            const load = makePlayer(viewHost, layer, fx, share ? players[0] : null, signal);
            if (load.player) {
              players.push(load.player);
              playerLoads.set(load.player, load);
            }
            try {
              return (await load.ready) ? { ...layer, player: load.player } : null;
            } catch (err) {
              throw atStep(err, `${layer.spine}.skel`, "player");
            }
          }),
        )
      ).filter(Boolean);
    const all = await build(layers.length > 1);
    if (signal.aborted || my !== token) return null;
    // the lobby as it rests: without the scenes only its intro shows
    const loaded = all.filter((l) => !l.introOnly);
    if (!loaded.length) throw new Error("No lobby skeletons loaded");
    if (current.error) throw current.error;
    Stage.install(
      all.map((l) => l.player),
      fx,
    );
    for (const l of all) if (Stage.hostOf(l.player)) lightUp(l.player); // the guests, with their host loaded
    current.meta = measure(loaded);
    for (const v of [current.meta.left, current.meta.full])
      if (!validRect(rectOf(v))) throw new Error("Invalid lobby framing");
    current.loaded = loaded;
    current.all = all;
    // the bottom layer over the camera's clear colour (graded with PP on); sparse art keeps the page behind it, for
    // the edge extension
    Linear.setClear(Stage.hostOf(loaded[0].player) || loaded[0].player, !current.meta.sparse);
    setView(viewOf(current.meta), true);
    feather(current.view);
    const main = loaded.find((l) => l.name === current.meta.main)?.player;
    current.touch = {
      players: loaded.map((l) => l.player),
      main,
      info,
      toWorld,
      zoomAt,
      panBy,
      playClip,
      clearClip,
      next,
      viewTarget,
      setViewTarget,
      talking: () => !!talk,
      stopTalk: stop,
      blocked: () => Idle.blocking > 0,
      pause: () => Idle.pause(),
      resume: () => Idle.resume(),
      interrupt: () => Idle.interrupt(),
    };
    if (!deferPlayback) restoreTouch();
    Idle.start(loaded.map((l) => l.player));
    openEdge();
    // the intro, from the first frame the lobby is revealed on screen (reveal); with the setting off, its end at once
    if (info?.intro) {
      if (!Number.isFinite(info.intro.duration) || info.intro.duration < 0 || info.intro.duration > 300)
        throw new Error("Invalid lobby intro duration");
      const c = current;
      c.intro = Intro.create(info.intro, {
        layers: all,
        fx,
        post: info.post,
        host,
        bone: boneOf,
        sfx: (id, set) => Sfx.play(id, set),
        screen: (p) => Linear.setScreen(p, layerViewport(game(), p.bamtLayer)),
        // one target for the skeletons drawn with the bottom player's context: its clear stays
        clear: (p, on) => Linear.setClear(Stage.hostOf(p) || p, on && !c.meta.sparse),
        show: (p, on) => Stage.setShown(p, on),
        visible: () =>
          !!c.preparingFrame ||
          (!paused() &&
            !!c.revealed &&
            (host.checkVisibility ? host.checkVisibility({ opacityProperty: true }) : !host.hidden)),
      });
      if (!introOn) c.intro.skip();
      else if (!muted && volume > 0) for (const s of info.intro.sounds || []) Sfx.prefetch(s.id); // in time for it
    }
    // with Full Art or Unlocked: the limits over every clip, once the screen is up
    setTimeout(() => {
      if (measuresClips() && my === token) measureClips();
    }, 400);
    return current.meta;
  })();
  try {
    return await current.ready;
  } catch (err) {
    if (my === token) clearView(host);
    throw atStep(err, `${sources.artBase}files.json`, "compose");
  }
}

function recoverView(c) {
  if (current !== c || c.recovering || c.signal.aborted) return;
  c.recovering = true;
  const revealed = c.revealed;
  lobbyLoads
    .run(async (signal) => {
      try {
        const meta = await showNow(
          c.core,
          c.host,
          c.variant,
          c.mod,
          { base: c.base, artBase: c.artBase, official: c.official },
          signal,
          c.framing,
          c.deferPlayback,
        );
        if (meta && !signal.aborted && revealed) {
          if (c.deferPlayback && !c.suspended) resume();
          else if (!c.deferPlayback) reveal();
        }
        return meta;
      } catch (err) {
        if (!signal.aborted) failView(err, c.host, `${c.artBase}files.json`, "context recovery");
      }
    })
    .catch((err) => console.error("Lobby recovery failed", err));
}

function failView(err, host, url, step) {
  previewError(err, url, step);
  clearView(host);
  const message = document.createElement("div");
  message.className = "lobby-unavailable";
  message.setAttribute("role", "status");
  message.textContent = T("detail.failed");
  host.appendChild(message);
}
/** Show that the lobby cannot be shown. */
export function unavailable(err, host, url, step = "detail") {
  sourceLoads.cancel();
  lobbyLoads.cancel();
  failView(err, host, url, step);
}

// the lobby's players and what each holds on the GPU, gone
function dropPlayers() {
  for (const p of players) p.stopRendering?.();
  // Guests release their resources before the shared context's owner.
  for (const p of [...players].reverse()) {
    try {
      current?.fx?.detach(p);
      Materials.detach(p);
      Linear.detach(p);
    } catch (err) {
      console.error(err);
    } finally {
      try {
        playerLoads.get(p)?.dispose();
      } catch (err) {
        console.error(err);
      }
      p.parent?.remove();
    }
  }
  players = [];
}

/** Hide the lobby in a host and free its players. */
export function hide(host) {
  sourceLoads.cancel();
  lobbyLoads.cancel();
  clearView(host);
}
function clearView(host) {
  token += 1;
  clearTimeout(current?.watchdog);
  if (current?.cancel) current.signal.removeEventListener("abort", current.cancel);
  releaseTouch();
  current?.lifecycle?.dispose();
  current?.loadingFreeze?.dispose();
  if (current?.host) current.host.inert = !!current.inputWasInert;
  Idle.stop();
  current?.intro?.dispose(); // its sounds, its depth of field's targets
  dropPlayers();
  current?.fx?.dispose();
  current?.caption?.dispose();
  current = null;
  clearInterval(extendTimer);
  chain = null;
  endTalk(false);
  Sfx.clear();
  clearTimeout(subtitleTimer);
  clearTimers();
  if (host) {
    host.innerHTML = "";
    host.classList.remove("open-edge");
  }
}

/**
 * The input trace of the fullscreen interaction (lobby-touch.js).
 * @param {((events: object[]) => void)|null} sink Gets batches of events; null turns the trace off.
 */
export const setTrace = (sink) => Touch.setTrace(sink);

function freezePlayer(player, c) {
  c.pauseFlags ||= new Map();
  // A native window can lose focus while the player's assets are loading. Its constructor pause is not a
  // playback preference: loadSkeleton will still call play() after success. The first loaded frame captures it.
  if (player.bamtLayer && player.animationState && !c.pauseFlags.has(player)) c.pauseFlags.set(player, player.paused);
  player.paused = true;
  player.stopRendering?.();
}
function freezeScene(c) {
  if (current !== c) return;
  c.pausedAt ??= performance.now();
  c.running = false;
  c.host.inert = true;
  pauseTimers();
  clearInterval(extendTimer);
  extendTimer = null;
  releaseTouch();
  for (const player of players) freezePlayer(player, c);
  for (const audio of [...audios, ...Sfx.playing])
    if (!audio.paused && !audio.ended) {
      c.resumeAudio.add(audio);
      audio.pause();
    }
}
function resumeScene(c) {
  if (current !== c || paused() || c.running || c.signal.aborted) return;
  c.running = true;
  c.host.inert = !!c.inputWasInert;
  if (talk && c.pausedAt != null) talk.started += Math.max(0, performance.now() - c.pausedAt);
  c.pausedAt = null;
  c.viewAt = null;
  c.introTime = undefined;
  c.drawnAt = performance.now();
  for (const player of players) {
    if (c.pauseFlags?.has(player)) player.paused = c.pauseFlags.get(player);
    freshClock(player);
  }
  c.pauseFlags?.clear();
  restoreTouch();
  resumeTimers();
  feather(c.view);
  for (const audio of c.resumeAudio)
    if (!muted && volume > 0 && !audio.ended && !audio.bamtOwnerSet?.closed) audio.play().catch(() => {});
  c.resumeAudio.clear();
  const sounds = c.pendingSounds.splice(0);
  for (const [id, set] of sounds) if (!set?.closed) Sfx.play(id, set);
  reveal(false);
  // Let a previously queued frame observe stopRendering before permitting a single replacement RAF loop.
  (globalThis.requestAnimationFrame || queueMicrotask)(() => {
    if (current !== c || paused() || !c.running || c.signal.aborted) return;
    for (const player of players)
      if (!Stage.hostOf(player) && player.stopRequestAnimationFrame && playerLoads.get(player)?.active?.() !== false) {
        freshClock(player);
        player.startRendering?.();
      }
  });
}
/**
 * Pause the lobby while Hall keeps an attached instance and another mode is visible. All clocks and audio
 * resume from this point.
 */
export function suspend() {
  const c = current;
  if (!c || c.suspended) return;
  c.suspended = true;
  freezeScene(c);
}
/** Resume the lobby after suspend(). */
export function resume() {
  const c = current;
  if (!c) return;
  c.suspended = false;
  resumeScene(c);
}
/**
 * Draw the lobby's first complete frame: it settles only after every optional FX/material image is in and an
 * actual complete composite has been drawn. Preparing the intro's frame zero queues its time-zero sounds for
 * activation instead of consuming them silently.
 * @returns {Promise<boolean>} True when it is drawn; false when the lobby changed meanwhile.
 */
export async function prepareFrame() {
  const c = current;
  if (!c) return false;
  await c.ready;
  if (current !== c || c.signal.aborted) return false;
  await withTimeout(Promise.all(c.visualLoads.values()), 15000, "Lobby visual assets", { signal: c.signal });
  if (current !== c || c.signal.aborted) return false;
  if (photoFrame && c.target) {
    c.view = { ...c.target };
    c.viewAt = null;
    photoMatte(c.view);
  }
  const flags = new Map(players.map((player) => [player, player.paused]));
  c.preparingFrame = true;
  try {
    for (const player of players) {
      player.paused = false;
      clocked(player);
      freshClock(player);
    }
    for (const player of players) if (!Stage.hostOf(player)) player.drawFrame?.(false);
    if (c.error) throw c.error;
    if (players.some((player) => player.error || player.disposed)) throw new Error("Lobby frame preparation failed");
    c.prepared = true;
    return true;
  } finally {
    c.preparingFrame = false;
    for (const [player, flag] of flags) player.paused = flag;
  }
}

// --- the intro (lobby-intro.js) ---
/**
 * The lobby is on screen (the detail screen has committed it): its intro starts with the next frame it is
 * visible.
 */
export function reveal(restart = true) {
  const c = current;
  if (!c || c.suspended || c.backgroundPaused || c.loadingPaused) return;
  c.revealed = true;
  // A player first laid out under a hidden tab must get a frame with the visible canvas's size.
  for (const p of players)
    if (
      restart &&
      !document.hidden &&
      (document.hasFocus?.() ?? true) &&
      playerLoads.get(p)?.active?.() !== false &&
      !Stage.hostOf(p) &&
      p.stopRequestAnimationFrame
    )
      p.startRendering?.();
  clearTimeout(c.watchdog);
  const check = () => {
    if (current !== c || c.signal.aborted) return;
    const visible =
      !document.hidden &&
      (document.hasFocus?.() ?? true) &&
      players.every((p) => playerLoads.get(p)?.active?.() !== false) &&
      (c.host.checkVisibility ? c.host.checkVisibility() : !c.host.hidden);
    if (visible) {
      const roots = players.filter((p) => !Stage.hostOf(p));
      if (roots.some((p) => p.context?.gl?.isContextLost())) {
        recoverView(c);
        return;
      }
      const err = roots.find((p) => p.error || p.disposed);
      const sized = roots.some((p) => p.canvas?.width > 0 && p.canvas?.height > 0);
      if (err || !sized || !c.drawnAt || performance.now() - c.drawnAt > 4000) {
        failView(
          new Error(err ? "Lobby renderer stopped" : "Lobby produced no visible frames"),
          c.host,
          `${c.artBase}files.json`,
          "draw watchdog",
        );
        return;
      }
      if (c.intro?.pending()) {
        const state = c.intro.state();
        if (!state.started || !Number.isFinite(state.t) || c.introTime === state.t) {
          failView(
            new Error("Lobby intro is not advancing after reveal"),
            c.host,
            `${c.base}lobby.json`,
            "intro watchdog",
          );
          return;
        }
        c.introTime = state.t;
      }
      if (!c.intro?.pending() && !talk && !(mode === "full" && c.userFramed)) {
        try {
          const visibleArt = c.all.some(
            (l) =>
              Stage.shown(l.player) &&
              overlaps(worldBounds(artBounds(l.player.skeleton), l.player.bamtLayer), rectOf(c.view)),
          );
          if (!visibleArt) throw new Error("No lobby art intersects the camera");
          if (!c.pixelsChecked) {
            if (!roots.some((p) => Linear.hasPixels(p))) throw new Error("Lobby render target is empty");
            c.pixelsChecked = true;
          }
        } catch (err) {
          failView(err, c.host, `${c.artBase}files.json`, "draw bounds");
          return;
        }
      }
    }
    c.watchdog = setTimeout(check, 5000);
  };
  c.watchdog = setTimeout(check, 5000);
}
/**
 * The intro setting: on plays it when the lobby is entered, off shows the lobby as the intro leaves it
 * (Shiroko's holds its last frame); a lobby shown now keeps what it does.
 */
export function setIntro(on) {
  introOn = on !== false;
}
/** Wait for the lobby's intro to end, be skipped or be missing (the switch card's still waits for it). */
export function introDone() {
  const c = current;
  return c?.ready ? c.ready.then(() => c.intro?.done()) : Promise.resolve();
}
/** Skip the lobby's intro. */
export const skipIntro = () => current?.intro?.skip();
/** Whether the intro is waiting to start. */
export const introPending = () => !!current?.intro?.pending();
/**
 * For checks: the intro's time and state.
 * @returns {object|null} The state; null without an intro.
 */
export const introState = () => current?.intro?.state() || null;
// for checks: every player's frame advances by a fixed `dt` seconds (0 holds the lobby still), or by the real time
// again (null); advance(dt) gives each player one frame of dt
let fixedDt = null;
const stepDt = new Map();
/**
 * For checks: every player's frame advances by a fixed `dt` seconds (0 holds the lobby still), or by the real
 * time again (null).
 * @param {number|null} dt The step in seconds.
 */
export function setClock(dt) {
  fixedDt = dt == null ? null : Math.max(0, Number(dt) || 0);
  for (const p of players) clocked(p);
}
// a player on the checks' clock (a lobby shown after setClock too)
function clocked(p) {
  if (!p.time || p.time.bamtUpdate) return;
  const update = p.time.update.bind(p.time);
  p.time.bamtUpdate = update;
  p.time.update = () => {
    const total = p.time.totalTime;
    update();
    if (paused() || current?.preparingFrame) {
      p.time.delta = 0;
      p.time.totalTime = total;
      return;
    }
    if (fixedDt == null) return;
    p.time.delta = stepDt.has(p) ? stepDt.get(p) : fixedDt;
    stepDt.delete(p);
  };
}
function freshClock(p) {
  if (!p.time) return;
  const total = p.time.totalTime;
  (p.time.bamtUpdate || p.time.update)?.call(p.time);
  p.time.delta = 0;
  p.time.totalTime = total;
}
/**
 * For checks: give each player one frame of dt.
 * @param {number} dt The step in seconds.
 */
export function advance(dt) {
  for (const p of players) stepDt.set(p, Math.max(0, Number(dt) || 0));
}

/** Switch the fullscreen interaction on or off (lobby-touch.js); it only runs in the fullscreen framing. */
export function interactive(on) {
  wantTouch = !!on;
  if (wantTouch && mode === "full" && current?.meta && !paused()) {
    if (!ownsInput()) restoreTouch();
    else Touch.attach(current.host);
  } else if (ownsInput()) Touch.detach();
}
// a stage point (px) -> NGUI units in the current view
function toWorld(sx, sy) {
  const v = current?.view || game();
  return [v.x + (sx / STAGE.w) * v.w, v.y + (1 - sy / STAGE.h) * v.h];
}

// --- clips: an animation played the way the lobby's clip asset says (track, loop, mixes, chain, sync clips) ---
function clipOf(p, name) {
  const c = current?.info?.clips?.[p.bamtLayer?.name]?.[name];
  if (c) return c;
  // without the clip asset (a mod, or no prefab data): the usual layout of the lobby clips
  const sync = /_M$/.test(name) ? name.replace(/_M$/, "_A") : null,
    isA = /_A$/.test(name),
    talky = /^Talk/.test(name);
  const mix = talky ? 0.3 : p.animationState.data.defaultMix;
  return {
    track: isA ? 2 : 1,
    loop: /^(Pat|Look|Pinch|HandFollow|Touch)_\d+_[MA]$/.test(name),
    finish: 2,
    intro: isA && !talky ? 0 : mix,
    outro: mix,
    next: null,
    sync: sync && p.skeleton.data.findAnimation(sync) ? [sync] : [],
  };
}
// As the game plays a clip: a clip of mode 2 waits for its random turn (lobby-sync.js), a random idle clip (mode 3)
// plays on its own schedule (Idle), the others play at once; then the clip's sync clips.
function playClip(p, name, depth = 0) {
  const st = p?.animationState,
    data = p?.skeleton?.data;
  if (!st || !data.findAnimation(name) || depth > 4) return null;
  const c = clipOf(p, name),
    t = c.track,
    cur = st.tracks[t];
  let entry = null;
  if (c.mode === 2) p.bamtRuns?.start(name);
  else if (c.mode !== 3) {
    if (!cur || (cur.animation.name === "<empty>" && !cur.mixingFrom)) {
      // an idle track: fade in over the lower tracks (setAnimation on an empty track would ignore the mix)
      st.setEmptyAnimation(t, 0);
      entry = st.addAnimation(t, name, c.loop, 0);
    } else entry = st.setAnimation(t, name, c.loop);
    entry.mixDuration = c.intro;
    soundsOf(p, entry, c);
    if (!c.loop) {
      // the outro or the next clip: so that it ends with the clip (outroAt null), else from that long after its end
      const at = c.outroAt == null ? 0 : data.findAnimation(name).duration + c.outroAt;
      if ((c.finish === 1 || c.finish === 3) && c.next && data.findAnimation(c.next)) enqueue(p, t, c.next, at, entry);
      else if (c.finish === 2) st.addEmptyAnimation(t, c.outro, at);
    }
  }
  for (const s of c.sync || []) playClip(p, s, depth + 1);
  return entry;
}
// the clip after another on its track, delay as for addAnimation; a random-timing one starts its
// wait once the clip before it completes (Hoshino's WhaleMove_01_R after Start_WhaleMove_01_R)
function enqueue(p, t, name, delay, after) {
  const st = p.animationState,
    n = clipOf(p, name);
  if (n.mode === 2) {
    Sync.listen(after, {
      complete: () => {
        if (p.animationState) playClip(p, name);
      },
    });
    return;
  }
  const e = st.addAnimation(t, name, n.loop, delay);
  if (delay <= 0) e.delay = Math.max(0, e.delay + e.mixDuration - n.intro); // its mix ends with the clip before
  e.mixDuration = n.intro;
  soundsOf(p, e, n);
}
function clearClip(p, name, depth = 0) {
  if (!p?.animationState || depth > 4) return;
  const c = clipOf(p, name);
  if (c.mode === 2) p.bamtRuns?.stop(name);
  p.animationState.setEmptyAnimation(c.track, c.outro || 0.2);
  for (const s of c.sync || []) clearClip(p, s, depth + 1);
}
// a dialogue starts or ends: the game returns the character to its idle keeping the idle's clips,
// and a random-timing one that has not played yet starts its wait again
function returnToIdle(p) {
  if (!p?.bamtRuns || !p.skeleton) return;
  const idle = idleOf(p.skeleton.data, p.bamtLayer);
  p.bamtRuns.returnToIdle(idleClips(current?.info, p.bamtLayer.name, idle, (n) => clipOf(p, n)));
}
const mainPlayer = () => players.find((p) => p.bamtLayer?.name === current?.meta?.main) || players[players.length - 1];
// a bone of a layer's skeleton in its current pose, for the particle systems that follow it (SkeletonUtilityBone)
function boneOf(skeleton, name) {
  const bone = players.find((p) => p.bamtLayer?.name === skeleton)?.skeleton?.findBone(name);
  return bone && bone.active !== false ? bone : null;
}
/**
 * For checks: the particle effects' live particles, systems, loaded textures, GL contexts and time per frame.
 * @returns {object|null} The statistics; null without effects.
 */
export const effects = (opts) => current?.fx?.stats(opts) || null;
/**
 * For checks: the layers' players, bottom first.
 * @returns {object[]}
 */
export const layerPlayers = () => players.filter((p) => p.bamtLayer);

// --- random idle clips (SpineCharacter.idleBaseRandomClipPool): blinks, ear twitches, expression changes, each
// after a random wait; held back while a dialogue or a drag runs. Without them the newer lobbies never blink. ---
const Idle = {
  timers: new Set(),
  paused: 0,
  blocking: 0,
  playing: [],
  start(list) {
    this.stop();
    for (const p of list)
      for (const name of current?.info?.randomIdle?.[p.bamtLayer.name] || []) this.schedule(p, name);
  },
  later(fn, ms) {
    const id = setTimeout(() => {
      this.timers.delete(id);
      fn();
    }, ms);
    this.timers.add(id);
  },
  schedule(p, name) {
    if (!p.skeleton?.data.findAnimation(name)) return;
    const r = clipOf(p, name).random || { min: 8, max: 15 };
    this.later(() => this.fire(p, name), Math.max(1, r.min + Math.random() * Math.max(0, r.max - r.min)) * 1000);
  },
  fire(p, name) {
    if (!current || !p.animationState) return;
    if (this.paused || talk) {
      this.schedule(p, name);
      return;
    }
    const c = clipOf(p, name),
      r = c.random || { loops: [0, 0] },
      st = p.animationState;
    const [lo, hi] = r.loops || [0, 0],
      times = 1 + lo + Math.floor(Math.random() * (Math.max(lo, hi) - lo + 1));
    st.setEmptyAnimation(c.track, 0);
    st.addAnimation(c.track, name, false, 0).mixDuration = c.intro;
    for (let i = 1; i < times; i++) st.addAnimation(c.track, name, false, 0).mixDuration = 0;
    // the last frame must not stay on a track above the face tracks
    st.addEmptyAnimation(c.track, c.outro || 0, 0);
    const ms = p.skeleton.data.findAnimation(name).duration * times * 1000,
      entry = { p, track: c.track, outro: c.outro };
    this.playing.push(entry);
    if (c.block) this.blocking += 1;
    this.later(() => {
      this.playing = this.playing.filter((x) => x !== entry);
      if (c.block) this.blocking = Math.max(0, this.blocking - 1);
      this.schedule(p, name);
    }, ms);
  },
  // a dialogue or a drag takes over: running random clips fade out
  interrupt() {
    for (const { p, track, outro } of this.playing) p.animationState?.setEmptyAnimation(track, outro || 0.2);
  },
  pause() {
    this.paused += 1;
  },
  resume() {
    this.paused = Math.max(0, this.paused - 1);
  },
  stop() {
    for (const id of this.timers) clearTimeout(id);
    this.timers.clear();
    this.paused = 0;
    this.blocking = 0;
    this.playing = [];
  },
};

// --- dialogues: the talk clip (Talk_NN_M, its sync Talk_NN_A) with the game's mixes, the voices and one subtitle line
/**
 * Play a dialogue: the talk clip (Talk_NN_M, its sync Talk_NN_A) with the game's mixes, the voices and one
 * subtitle line per voice cue, then back to idle. A dialogue starts at once, cutting off whatever was playing.
 * @param {string} anim The talk animation's name.
 * @param {object} [options]
 * @param {boolean} [options.fromChain] The dialogue follows another in play all.
 * @returns {object|null} The dialogue's sequence; null when it cannot play (no lobby, or suspended).
 */
export function play(anim, { fromChain = false } = {}) {
  const m = current?.meta;
  if (!m || current?.suspended || current?.backgroundPaused || current?.loadingPaused) return null;
  current.intro?.skip(); // a dialogue comes after the intro, as a tap that skips it
  if (!fromChain) chain = null;
  const i = m.sequences.findIndex((s) => s.anim === anim),
    seq = m.sequences[i],
    p = mainPlayer();
  if (!seq || !p?.animationState) return null;
  endTalk(false); // the new talk takes over tracks 1 and 2
  Idle.interrupt();
  returnToIdle(p);
  talkIndex = i;
  const my = (talk = { seq, started: performance.now(), cues: [] });
  for (const cue of seq.cues) {
    cueTimers.push(
      setTimeout(() => {
        if (talk !== my) return;
        my.cues.push(cue.clip);
        const text = lineOf(lines[cue.clip]);
        if (text) subtitle(text);
        if (!muted && lines[cue.clip]?.voice) voice(cue.clip);
      }, cue.t * 1000),
    );
  }
  cueTimers.push(setTimeout(() => finishTalk(my), seq.duration * 1000 + 300));
  playClip(p, seq.anim);
  emit();
  return seq;
}
function voice(clip) {
  if (current?.suspended || current?.backgroundPaused || current?.loadingPaused) return;
  const a = new Audio(`/asset/voice/${encodeURIComponent(current.variant)}/${encodeURIComponent(clip)}.ogg`);
  a.volume = volume;
  audios.push(a);
  a.play().catch(() => {});
}
function finishTalk(my) {
  if (talk !== my) return;
  endTalk(false);
  returnToIdle(mainPlayer());
  const run = chain;
  if (run && current?.meta) {
    const seq = current.meta.sequences[++run.i];
    if (seq) {
      setTimeout(() => {
        if (chain === run) play(seq.anim, { fromChain: true });
      }, 200);
      return;
    }
    chain = null;
  }
  emit();
}
function endTalk(fade) {
  for (const t of cueTimers) clearTimeout(t);
  cueTimers = [];
  silence();
  subtitle("");
  if (talk && fade) clearClip(mainPlayer(), talk.seq.anim);
  talk = null;
}

/** Stop at once: the talk tracks fade back to idle, voices and subtitle go. */
export function stop() {
  const was = !!talk || !!chain; // play all may sit between two dialogues
  chain = null;
  const talking = !!talk;
  endTalk(true);
  if (talking) returnToIdle(mainPlayer());
  if (was) emit();
}
/**
 * Play the next dialogue after the last one played (a tap on the body); a dialogue that has only just started
 * is not cut.
 */
export function next() {
  const seqs = current?.meta?.sequences;
  if (
    !seqs?.length ||
    current?.suspended ||
    current?.backgroundPaused ||
    current?.loadingPaused ||
    (talk && performance.now() - talk.started < TALK_GUARD_MS)
  )
    return;
  chain = null;
  play(seqs[(talkIndex + 1) % seqs.length].anim);
}
/**
 * Play every dialogue in order, each starting when the previous one ends.
 * @param {string|null} [selected]
 */
export function playAll(selected = null) {
  const seqs = current?.meta?.sequences;
  if (!seqs?.length || current?.suspended || current?.backgroundPaused || current?.loadingPaused) return;
  const run = {
    i: Math.max(
      0,
      seqs.findIndex((seq) => seq.anim === selected),
    ),
  };
  chain = run;
  play(seqs[run.i].anim, { fromChain: true });
}
/** Whether play all is running. */
export const playingAll = () => !!chain;
/**
 * The dialogue in play.
 * @returns {string|null} Its talk animation, or null.
 */
export const playing = () => talk?.seq.anim || null;
/**
 * The shown lobby's meta (frames, sequences).
 * @returns {object|null} The meta, or null.
 */
export const meta = () => current?.meta || null;
/**
 * Subscribe to dialogues: cb(anim or null, playingAll) is called whenever a dialogue starts, ends or stops.
 * @returns {() => void} Unsubscribes the listener.
 */
export function onTalk(cb) {
  listeners.add(cb);
  return () => listeners.delete(cb);
}
function emit() {
  const anim = talk?.seq.anim || null,
    all = !!chain;
  for (const cb of listeners)
    try {
      cb(anim, all);
    } catch (e) {
      console.error(e);
    }
}

function silence() {
  for (const a of audios) {
    a.pause();
    current?.resumeAudio?.delete(a);
  }
  audios = [];
}

/**
 * Set the lines by clip id ({clip: {lang: text, voice}}: Kivo's Chinese and Japanese, the Global server's
 * English and others) and the picker of the UI language's line ("" when it has none: a cue without one leaves
 * the subtitle as it is, since one row of the game's text can cover two clips).
 * @param {Object<string, object>} table
 * @param {(line: object) => string} pick Picks the UI language's text.
 */
export function setLines(table, pick) {
  // Undefined changes only the picker while translated rows load; keep voice availability for upcoming cues.
  if (table !== undefined) lines = table || {};
  if (pick) lineOf = pick;
  // Re-evaluate only cues already reached. A text row may cover several voice cues, so a blank later cue keeps
  // the latest translated row. Playback, voice audio and the remaining cue timers continue unchanged.
  let text = "";
  for (const clip of talk?.cues || []) text = lineOf(lines[clip]) || text;
  subtitle(text, true);
}
/**
 * Set the voice volume, which the clips' sounds follow too; muted never plays voices or sounds.
 * @param {number} value The volume, 0 to 1.
 */
export function setVolume(value, mute) {
  volume = Math.max(0, Math.min(1, value));
  muted = !!mute;
  for (const a of audios) a.volume = volume;
  Sfx.follow();
  if (muted) {
    silence();
    Sfx.stop();
  }
}

// --- the clips' sounds (SpineClip.SoundKeys, played by lobby-sync.js): an AnimationAudioEvent plays one of its
// clips at random, at its volume and at its pitch, a playback rate as Unity's pitch is.
// The clips come from the game files (lobby.json sounds, /asset/spine/lobby/<core>/sfx_<clip>.wav), fetched once a
// clip with sound keys starts and kept as blobs for the lobby; the game's SFX volume is the app's voice volume. ---
const Sfx = {
  urls: new Map(),
  playing: new Set(),
  log: [],
  load(clip) {
    if (!this.urls.has(clip) && current) {
      this.urls.set(
        clip,
        fetch(`${current.base}sfx_${encodeURIComponent(clip)}.wav`)
          .then((r) => (r.ok ? r.blob() : null))
          .then((b) => (b ? URL.createObjectURL(b) : null))
          .catch(() => null),
      );
    }
    return this.urls.get(clip) || Promise.resolve(null);
  },
  prefetch(id) {
    for (const clip of current?.info?.sounds?.[id]?.clips || []) this.load(clip);
  },
  // also: the set the audio joins while it plays (the intro's, which a skip stops)
  play(id, also = null) {
    if (paused()) {
      current?.pendingSounds.push([id, also]);
      return;
    }
    const ev = current?.info?.sounds?.[id];
    this.log.push({ event: id, name: ev?.name || null, at: Math.round(performance.now()) });
    if (this.log.length > 40) this.log.shift();
    if (!ev?.clips?.length || muted || !(volume > 0)) return;
    const clip = ev.clips[Math.floor(Math.random() * ev.clips.length)],
      my = token;
    this.load(clip).then((url) => {
      if (!url || my !== token || muted || also?.closed) return; // a skipped intro's sound still loading never plays
      const a = new Audio(url);
      a.bamtOwnerSet = also;
      a.bamtVolume = ev.volume ?? 1;
      a.volume = clamp(a.bamtVolume * volume, 0, 1);
      a.preservesPitch = a.webkitPreservesPitch = a.mozPreservesPitch = false;
      a.defaultPlaybackRate = a.playbackRate = ev.pitch || 1;
      this.playing.add(a);
      also?.add(a);
      a.onended = () => {
        this.playing.delete(a);
        also?.delete(a);
      };
      if (paused()) current.resumeAudio.add(a);
      else
        a.play().catch(() => {
          if (!current?.resumeAudio?.has(a)) {
            this.playing.delete(a);
            also?.delete(a);
          }
        });
    });
  },
  follow() {
    for (const a of this.playing) a.volume = clamp(a.bamtVolume * volume, 0, 1);
  }, // a new voice volume
  stop() {
    for (const a of this.playing) {
      a.pause();
      current?.resumeAudio?.delete(a);
    }
    this.playing.clear();
  },
  clear() {
    this.stop();
    for (const url of this.urls.values()) url.then((u) => u && URL.revokeObjectURL(u));
    this.urls.clear();
    this.log = [];
  },
};
const sfx = (id) => Sfx.play(id);
// a track entry's sound keys (on every entry a clip sets, as in the game), their clips fetched ahead
function soundsOf(p, entry, c) {
  if (!entry || !c?.sounds?.length || !p?.bamtSounds) return;
  p.bamtSounds.watch(entry, c.sounds);
  if (!muted && volume > 0) for (const [, id] of c.sounds) Sfx.prefetch(id);
}
/**
 * For checks: each layer's random-timing clips (seconds until each plays next) and tracks, and the sound keys
 * played lately (also while muted).
 * @returns {object[]}
 */
export const clipRuns = () =>
  players
    .filter((p) => p.bamtLayer)
    .map((p) => ({
      layer: p.bamtLayer.name,
      time: +(p.bamtRuns?.time || 0).toFixed(3),
      runs: p.bamtRuns?.list() || [],
      tracks: p.animationState?.tracks.map((t) => t?.animation?.name || null) || [],
    }));
/**
 * For checks: the sound keys played lately.
 * @returns {object[]}
 */
export const soundLog = () => Sfx.log.slice();
/**
 * A sequence's lines, one per voice clip.
 * @param {{cues: {clip: string}[]}} seq
 * @returns {string[]}
 */
export const textOf = (seq) => seq.cues.map((c) => lineOf(lines[c.clip])).filter(Boolean);

// one subtitle line at a time: the old one leaves upwards and the next rises in from below
function subtitle(text, immediate = false) {
  if (!current) return;
  if (!text) {
    current.captionLine = ["", immediate];
    current.caption?.show(...current.captionLine);
    return;
  }
  // A Hall fallback may use another language than the page. Preserve the selected row's actual language.
  const preferred = { "en-US": "en", "ja-JP": "ja", "ko-KR": "ko", "th-TH": "th" }[language()] || language();
  const rows = (talk?.cues || []).map((clip) => lines[clip]).reverse();
  const row = rows.find((value) => value && Object.values(value).includes(text));
  const lang =
    row?.[preferred] === text ? preferred : Object.keys(row || {}).find((key) => row[key] === text) || preferred;
  current.captionLine = [text, immediate, lang];
  current.caption?.show(...current.captionLine);
}

// A camera may show beyond the art, but never synthesize stretched/blurred border pixels.
function photoMatte(view) {
  if (!current?.meta || !view) return;
  clearInterval(extendTimer);
  const el = current.host.querySelector(".lobby-view"),
    ext = current.host.querySelector(".lobby-extend");
  if (ext) ext.hidden = true;
  if (!el) return;
  const r = rectOnStage(view, STAGE.w, STAGE.h);
  el.style.webkitMaskImage = el.style.maskImage = "none";
  el.style.clipPath = `inset(${Math.max(0, r.t)}px ${Math.max(0, STAGE.w - r.r)}px ${Math.max(0, STAGE.h - r.b)}px ${Math.max(0, r.l)}px)`;
}
