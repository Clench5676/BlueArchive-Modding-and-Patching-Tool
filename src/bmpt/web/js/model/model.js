// 3D chibi for the detail screen's third tab. The GLB is exported from the user's game files in memory
// (gameassets/model.py): the game's material constants are in the material extras, the halo follower
// (FxFollower) in the root extras, and each clip's slot, loop flag, SetMouthTile events and renderer switches in
// the animation extras; the halo's particle effects (haloFx) play as model-fx.js draws them. The materials are
// ports of the game's character shaders (chibi-materials.js); the camera starts at the formation screen's view
// of one character (FORMATION_VIEW) and orbits freely; the wheel, pinches and swipes are read as the fullscreen
// lobby reads them (attachInput). One view at a time; hide() frees its WebGL context at once.
import * as THREE from "three";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { straightTextureLoader } from "../core/png-decode.js";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { chibiMaterial, disposeDefaults, outlineMaterial, setOutlineScale } from "./chibi-materials.js";
import { createModelFx } from "./model-fx.js";
import { modelUrl } from "../mods/switch-sources.js";
import { freezeWhileLoading, onScreen } from "../detail/preview-lifecycle.js";
import {
  clamp,
  ZOOM_TAU,
  FORMATION_VIEW,
  FRAMES,
  TWEEN,
  frameDistance,
  bustDistance,
  zoomShare,
  liftShare,
  carryZoom,
  clampPan,
  saveZoom,
  restoreZoom,
  attachInput,
  HOME,
  visibleStillFrame,
  stillCamera,
  stillCrop,
} from "./model-camera.js";

const ease = (t) => 1 - Math.pow(1 - t, 3);

// the tab's actions: the clip slots they play, in order of preference; "next" follows a clip that plays once, "then"
// is the action after it; "fade" the cross-fade into the action in seconds (0: a cut), else the caller's; "face":
// the view turns to the character's front once the action's first frame is posed
export const ACTIONS = {
  idle: { slots: ["Formation_Idle", "Cafe_Idle", "Idle"] },
  // biome-ignore lint/suspicious/noThenProperty: "then" names the action that follows, the object is never awaited
  reaction: { slots: ["Cafe_Reaction"], then: "idle" },
  walk: { slots: ["Cafe_Walk"] },
  victory: { slots: ["Victory_Start"], next: "Victory_End" },
  pickup: { slots: ["Formation_Pickup"] },
  // the battle's vital clips: the retreat cuts straight to the crying in place the battle controller's retreat state
  // plays (looping), turned to the battle camera, so the view turns to the face; the knockdown, an enemy's, falls and
  // stays down
  retreat: { slots: ["Vital_Retreat"], fade: 0, face: true },
  knockdown: { slots: ["Vital_Death"], hold: true },
};

// a story model's own cutscene clips (slots Story_1, Story_2, ...): actions story1, story2, ..., each played
// once and held on its last frame
const STORY = /^Story_(\d+)$/;
/**
 * The actions of a story model's own cutscene clips (slots Story_1, Story_2, ...), each played once and held on
 * its last frame.
 * @param {Map<string, *>|undefined} clips The model's clips by slot.
 * @returns {string[]} The action names story1, story2, ... in order.
 */
export const storyActions = (clips) =>
  [...(clips?.keys() || [])]
    .map((slot) => STORY.exec(slot)?.[1])
    .filter(Boolean)
    .map(Number)
    .sort((a, b) => a - b)
    .map((n) => `story${n}`);
/**
 * The definition of a story action.
 * @returns {{slots: string[], hold: boolean}|null} The slots to play; null when the name is not a story action.
 */
export const storyAction = (name) =>
  /^story\d+$/.test(name) ? { slots: [`Story_${name.slice(5)}`], hold: true } : null;

// The skeleton's paired bones and trunk the facing is read from: a biped's (3ds Max's "Bip001 L Thigh", any rig's
// left/right thigh and clavicle or shoulder, its pelvis or hips and its head); null without a pair and the trunk.
const SIDE = /(?:^|[\s_.:-])(l|r|left|right)[\s_.:-]*(thigh|upleg|upperleg|clavicle|shoulder)$/i;
/**
 * The skeleton's paired bones and trunk the facing is read from: a biped's (3ds Max's "Bip001 L Thigh", any
 * rig's left and right thigh or arm) and the head and hips.
 * @param {THREE.Object3D|undefined} root
 * @returns {{head: THREE.Object3D, hips: THREE.Object3D, pairs: THREE.Object3D[][]}|null} The bones; null when
 *     the rig lacks them.
 */
export function facingBones(root) {
  const found = {};
  root?.traverse((o) => {
    const side = SIDE.exec(o.name || "");
    if (side) {
      const key = `${side[1][0].toLowerCase()}${/thigh|leg/i.test(side[2]) ? "Leg" : "Arm"}`;
      found[key] ||= o;
    } else if (!found.head && /(?:^|[\s_.:-])head$/i.test(o.name || "")) found.head = o;
  });
  const hips = anchorNode(root);
  const pairs = [
    ["lLeg", "rLeg"],
    ["lArm", "rArm"],
  ].filter(([l, r]) => found[l] && found[r]);
  return hips && found.head && pairs.length
    ? { hips, head: found.head, pairs: pairs.map(([l, r]) => [found[l], found[r]]) }
    : null;
}

/**
 * The head's own forward, in its local space: `front` (the front view's direction) as the head is turned now,
 * read while the formation idle looks into the formation camera; null without the bones.
 * @param {object|null} bones The bones, as facingBones gives them.
 * @param {THREE.Vector3} [front] The front view's direction.
 * @returns {THREE.Vector3|null}
 */
export function headFront(bones, front = HOME) {
  if (!bones) return null;
  return front.clone().applyQuaternion(bones.head.getWorldQuaternion(new THREE.Quaternion()).invert());
}

/**
 * The way a posed character faces, level (unit, y 0): where the face looks, with the head's forward
 * (headFront), else the body's: the trunk (hips to head) crossed with its left-to-right line (each pair's right
 * bone less its left, summed); null when the pose gives none (lying flat along that line, or the face straight
 * up or down).
 * @param {object|null} bones The bones, as facingBones gives them.
 * @param {THREE.Vector3} [out]
 * @param {THREE.Vector3|null} [front] The head's forward (headFront).
 * @returns {THREE.Vector3|null}
 */
export function facingOf(bones, out = new THREE.Vector3(), front = null) {
  if (!bones) return null;
  if (front) {
    out.copy(front).applyQuaternion(bones.head.getWorldQuaternion(new THREE.Quaternion())).setY(0);
    if (out.lengthSq() > 1e-4) return out.normalize();
  }
  const at = (o) => o.getWorldPosition(new THREE.Vector3());
  const up = at(bones.head).sub(at(bones.hips));
  const right = new THREE.Vector3();
  for (const [l, r] of bones.pairs) {
    const d = at(r).sub(at(l));
    if (d.lengthSq() > 0) right.add(d.normalize());
  }
  out.crossVectors(up, right).setY(0);
  return out.lengthSq() > 1e-8 ? out.normalize() : null;
}

/**
 * The camera's direction from the orbit target that sees the character's front: its facing, at the camera's
 * height angle now (the orbit's elevation kept, so only the yaw turns).
 * @param {THREE.Vector3} facing
 * @param {THREE.Vector3} offset The camera's offset from the orbit target.
 * @returns {THREE.Vector3} The direction, a unit vector.
 */
export function frontView(facing, offset) {
  const length = offset.length();
  const sin = length > 0 ? THREE.MathUtils.clamp(offset.y / length, -1, 1) : 0;
  return facing
    .clone()
    .multiplyScalar(Math.sqrt(1 - sin * sin))
    .setY(sin)
    .normalize();
}

/**
 * The node the view follows: the hips (3ds Max's Bip001, else a pelvis or hips bone), whose horizontal travel a
 * clip's walk, run or cutscene path makes; null without one (the view then stays put).
 * @param {THREE.Object3D|undefined} root
 */
export function anchorNode(root) {
  let found = root?.getObjectByName("Bip001") || null;
  root?.traverse((o) => {
    if (!found && /(^|[\s_])(pelvis|hips)$/i.test(o.name)) found = o;
  });
  return found;
}

/**
 * The orbit target's horizontal shift that keeps the anchor where it stood when the view was measured: the
 * camera moves with the character, which then acts in place whatever its clip does (height is the clip's own:
 * jumps and crouches still show).
 * @param {THREE.Vector3} position
 * @param {THREE.Vector3} rest The anchor when the view was measured.
 * @param {THREE.Vector3} [out]
 * @returns {THREE.Vector3}
 */
export function followOffset(position, rest, out = new THREE.Vector3()) {
  return out.set(position.x - rest.x, 0, position.z - rest.z);
}

/**
 * Where the orbit target stands across the floor: over the prefab's root, as on the formation screen, unless
 * the hips stand further than `far` from it (a cutscene's model, placed off its own root, e.g. Arona's): then
 * over the hips, so the character is in the frame's point rather than beside it.
 * @param {{x: number, z: number}} origin
 * @param {number} [far] The distance beyond which the hips are used.
 */
export function standPoint(origin, hips, far = 0.2) {
  const off = hips ? Math.hypot(hips.x - origin.x, hips.z - origin.z) : 0;
  return off > far ? { x: hips.x, z: hips.z } : { x: origin.x, z: origin.z };
}

const stageScale = () => (document.getElementById("stage")?.getBoundingClientRect().width || 1920) / 1920;

// a GLB of the model route, parsed; the server's refusal code (X-BAMT-Error, e.g. original_unavailable: no official
// copy of a part is left) on the error
async function readGlb(url, signal) {
  const loader = new GLTFLoader();
  // textures read straight, never premultiplied by the browser: alpha is data beside colours (png-decode.js)
  loader.register((parser) => {
    parser.textureLoader = straightTextureLoader;
    return { name: "BAMT_straight_textures" };
  });
  const response = await fetch(url, { signal, cache: "no-store" });
  if (!response.ok) {
    const error = new Error(`${url}: HTTP ${response.status}`);
    const code = response.headers.get("X-BAMT-Error");
    if (code) error.code = code;
    throw error;
  }
  const bytes = await response.arrayBuffer();
  signal?.throwIfAborted();
  return loader.parseAsync(bytes, "");
}
// a GLB's textures: colour textures decoded from sRGB, masks and direction maps as data (the game's linear
// colour space)
async function gltfTextures(gltf) {
  const json = gltf.parser.json;
  const textures = await Promise.all((json.textures || []).map((_, i) => gltf.parser.getDependency("texture", i)));
  textures.forEach((t, i) => {
    t.colorSpace = json.textures[i].extras?.linear ? THREE.NoColorSpace : THREE.SRGBColorSpace;
    t.flipY = false;
  });
  return textures;
}
function discardScene(scene) {
  scene.traverse((o) => {
    o.geometry?.dispose();
    for (const m of [].concat(o.material || [])) {
      for (const v of Object.values(m)) if (v?.isTexture) v.dispose();
      m.dispose();
    }
  });
}

/**
 * Make a clip added on demand (the model route's ?clip=, model.py export_clip) play on the shown model: the
 * clip's GLB holds the model's hierarchy in the shown GLB's node order, so each track's node is the shown
 * model's node of the same index (renamed to its uuid, which the mixer finds whatever the names).
 * @param {object} clipJson The clip GLB's JSON.
 * @param {THREE.Object3D[]} clipNodes The clip GLB's nodes.
 * @param {THREE.Object3D[]} nodes The shown model's nodes.
 * @param {object} json The shown GLB's JSON.
 * @returns {THREE.AnimationClip|null} The clip on the shown model; null when the hierarchy is not the shown
 *     one's (another source answered).
 */
export function retarget(clip, clipJson, clipNodes, nodes, json) {
  const count = clipJson.extras?.hierarchy;
  if (
    !Number.isInteger(count) ||
    count > (json.nodes || []).length ||
    clipJson.nodes.slice(0, count).some((n, i) => n.name !== json.nodes[i].name)
  )
    return null;
  const names = new Map();
  for (const ch of clipJson.animations?.[0]?.channels || []) {
    const i = ch.target?.node,
      from = clipNodes[i];
    if (from && i < count && nodes[i]) names.set(from.name || from.uuid, nodes[i].uuid);
  }
  const tracks = [];
  for (const track of clip.tracks) {
    const dot = track.name.lastIndexOf(".");
    const to = names.get(track.name.slice(0, dot));
    if (!to) continue;
    const t = track.clone();
    t.name = `${to}${track.name.slice(dot)}`;
    tracks.push(t);
  }
  return new THREE.AnimationClip(clip.name, clip.duration, tracks);
}

let viewCount = 0;
class ChibiView {
  constructor(host, key, { still = false, renderer = null } = {}) {
    this.host = host;
    this.key = key;
    this.onAction = null;
    this.renderer = renderer || new THREE.WebGLRenderer({ antialias: true, alpha: true });
    viewCount++;
    this.still = still;
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;
    this.canvas = this.renderer.domElement;
    this.canvas.className = "model-view";
    host?.appendChild(this.canvas);
    this.scene = new THREE.Scene();
    this.camera = new THREE.PerspectiveCamera(FORMATION_VIEW.fov, 1, 0.01, 100);
    this.controls = still
      ? { target: new THREE.Vector3(), update() {}, dispose() {} }
      : new OrbitControls(this.camera, this.canvas);
    // the controls orbit, at any angle (straight from above to straight from below), with one finger or the left
    // button; zoom and pan are attachInput's, between the bust and a step back (frameTo, bustDistance). The controls'
    // own zoom took 58 Chromium notches or about 1430 slow macOS ones (on a 2x screen) to the bust, and WebKit's
    // pinch never reached them.
    Object.assign(this.controls, {
      enableDamping: true,
      dampingFactor: 0.12,
      enablePan: false,
      enableZoom: false,
      minPolarAngle: 0,
      maxPolarAngle: Math.PI,
    });
    this.controls.touches = { ONE: THREE.TOUCH.ROTATE, TWO: null }; // a second finger pinches (attachInput)
    this.clock = new THREE.Clock();
    this.mode = "left";
    this.view = { ...FRAMES.left };
    this.tween = null;
    this.goal = null; // the camera distance a zoom eases to
    this.pan = new THREE.Vector3(); // the orbit target's offset from the lifted target, and the one it eases to
    this.panGoal = new THREE.Vector3();
    this.follow = new THREE.Vector3(); // followOffset of the anchor (the hips) this frame
    if (still) {
      this.mode = "still";
      this.view = { ...FRAMES.still };
      this.renderer.setPixelRatio(1);
      this.renderer.setSize(640, 640, false);
      return;
    }
    this.detachInput = attachInput(this.canvas, {
      zoom: (f) => this.zoomBy(f),
      zoomSave: () => saveZoom(this),
      zoomRestore: (saved) => restoreZoom(this, saved),
      span: () => this.span(),
      pan: (dx, dy) => this.panBy(dx, dy),
    });
    this.observer = new ResizeObserver(() => this.resize());
    this.observer.observe(host);
    // a window resize only rescales the stage (the host keeps its layout size), a move to another screen or a
    // browser zoom changes the pixel ratio: both change the canvas resolution
    this.onWindow = () => this.resize();
    addEventListener("resize", this.onWindow);
    this.watchPixelRatio();
    this.canvas.addEventListener("dblclick", () => this.home());
    this.enableTap();
    this.resize();
  }

  // the canvas at the stage's on-screen size: the stage is a 1920 x 1080 box scaled by CSS
  resize() {
    const w = this.host.clientWidth || 1,
      h = this.host.clientHeight || 1,
      scale = stageScale();
    this.renderer.setPixelRatio(Math.min(2, (window.devicePixelRatio || 1) * scale));
    this.renderer.setSize(w, h, false);
    setOutlineScale(w, h); // the game's outline is 0.1 % of its screen width: here the stage's
    this.controls.rotateSpeed = 0.7 / Math.max(0.25, scale); // pointer moves are in screen pixels
    this.applyView();
  }

  // a media query matches the current pixel ratio only, so it is renewed on every change
  watchPixelRatio() {
    this.dpr?.removeEventListener("change", this.onPixelRatio);
    this.dpr = matchMedia(`(resolution: ${window.devicePixelRatio || 1}dppx)`);
    this.onPixelRatio = () => {
      this.watchPixelRatio();
      this.resize();
    };
    this.dpr.addEventListener("change", this.onPixelRatio);
  }

  // the orbit target projects to the host's centre; shift the view so it lands on the frame's point instead
  applyView() {
    const w = this.still ? 640 : this.host.clientWidth || 1,
      h = this.still ? 640 : this.host.clientHeight || 1;
    this.camera.setViewOffset(w, h, w / 2 - this.view.x, h / 2 - this.view.y, w, h);
    this.camera.updateProjectionMatrix();
  }

  async load(url, { signal } = {}) {
    const gltf = await readGlb(url, signal);
    if (this.disposed) {
      discardScene(gltf.scene);
      return;
    }
    const json = gltf.parser.json,
      parser = gltf.parser;
    const ex = json.extras || {};
    const textures = await gltfTextures(gltf);
    const nodes = await Promise.all((json.nodes || []).map((_, i) => parser.getDependency("node", i)));
    if (this.disposed) {
      discardScene(gltf.scene);
      for (const t of textures) t.dispose();
      return;
    }
    this.textures = textures; // they are only in shader uniforms: disposed by hand
    // the mouth atlas comes as a texture of the EyeMouth material (_MouthTileTex); SetMouthTile moves its offset
    const mats = json.materials || [];
    const eye = mats.find((m) => m.extras?.kind === "eyemouth" && m.extras.textures?._MouthTileTex);
    if (eye) {
      const t = eye.extras.textures._MouthTileTex;
      this.mouth = {
        st: { value: new THREE.Vector4(t.scale[0], t.scale[1], t.offset[0], t.offset[1]) },
        cols: eye.extras.floats?._MouthTileCols || 8,
        rows: eye.extras.floats?._MouthTileRows || 8,
      };
    }
    const root = gltf.scene;
    const meshes = [];
    root.traverse((o) => {
      const node = json.nodes[parser.associations.get(o)?.nodes];
      if (node?.extras?.hidden) o.visible = false;
      if (o.isMesh) meshes.push(o);
    });
    this.spaces = []; // [material, the object whose space its shader reads directions in]
    this.dress(meshes, gltf, textures, nodes);
    this.scene.add(root);
    this.root = root;
    this.headFront = undefined; // read again for this model (readHeadFront)
    this.gltf = gltf;
    this.json = json;
    this.nodes = nodes;
    this.mixer = new THREE.AnimationMixer(root);
    this.mixer.addEventListener("finished", (e) => this.finished(e.action));
    this.clips = new Map();
    // every clip by its name (the base GLB's and the ones added on demand, addClips)
    this.named = new Map();
    // renderers a clip switches (an expression's alternative face), each with its state at rest
    this.switches = new Map();
    gltf.animations.forEach((clip, i) => {
      const extras = json.animations[i]?.extras || {};
      for (const t of extras.toggles || []) {
        const object = nodes[t.node];
        if (object && !this.switches.has(t.node)) this.switches.set(t.node, { object, rest: object.visible });
      }
      const entry = { clip, ...extras };
      if (extras.slot && !this.clips.has(extras.slot)) this.clips.set(extras.slot, entry);
      if (clip.name && !this.named.has(clip.name)) this.named.set(clip.name, entry);
    });
    // without a clip (NPC placeholders) the rest pose shows the props and the weapon where their bones rest, beside
    // the character; the game's clips scale them away, so fold them here
    if (!this.clips.size)
      root.traverse((o) => {
        if (/^(prop_root|Bip001_Weapon)$/i.test(o.name)) o.scale.setScalar(0);
      });
    await this.setupHalo(ex.haloFollower);
    if (this.disposed) return;
    // the halo's particles (Tsurugi's drops, Mika's stars and rings), placed by their nodes as the halo moves
    try {
      this.fx = createModelFx(ex.haloFx, { nodes, textures, root });
    } catch (err) {
      console.warn("halo effects", err);
      this.fx = null;
    }
    if (this.fx) this.scene.add(this.fx.group);
    // a story model has no idle: it stands in its first story clip's pose, never its bind pose
    if (!this.act("idle", { fade: 0, quiet: true })) this.act("story1", { fade: 0, quiet: true });
    this.measure();
  }

  // the game's shaders on a GLB's meshes (chibiMaterial) and their outlines. `nodes`: the shown model's nodes, in
  // whose space a skinned renderer's shader reads directions (its root bone; the hierarchy's indices are the same in
  // an added clip's GLB)
  dress(meshes, gltf, textures, nodes) {
    const json = gltf.parser.json,
      parser = gltf.parser,
      mats = json.materials || [];
    // the node of a mesh object: a node of one primitive is the mesh, of several a group around them
    const nodeOf = (o) => {
      for (let q = o; q; q = q.parent) {
        const i = parser.associations.get(q)?.nodes;
        if (i !== undefined) return i;
      }
      return undefined;
    };
    for (const o of meshes) {
      o.frustumCulled = false;
      const gltfMaterial = o.material;
      const mex = mats[parser.associations.get(gltfMaterial)?.materials]?.extras || {};
      const texs = {};
      for (const [k, v] of Object.entries(mex.textures || {})) texs[k] = textures[v.index];
      const { material, objectSpace, outline, queue } = chibiMaterial(mex, texs, this.mouth);
      material.name = gltfMaterial.name;
      o.material = material;
      o.renderOrder = queue; // the game's render queue: geometry, then the eyes over the face, then transparents
      gltfMaterial.dispose();
      if (objectSpace) {
        // a skinned renderer draws in its root bone's space (Unity's unity_ObjectToWorld), a static one in its own
        const def = json.nodes[nodeOf(o)];
        const skeleton = def?.skin !== undefined ? json.skins[def.skin].skeleton : undefined;
        this.spaces.push([material, o.isSkinnedMesh && skeleton !== undefined ? nodes[skeleton] : o]);
      }
      if (outline && o.geometry.attributes._outline) {
        const ol = o.isSkinnedMesh
          ? new THREE.SkinnedMesh(o.geometry, outlineMaterial(mex, texs._MainTex))
          : new THREE.Mesh(o.geometry, outlineMaterial(mex, texs._MainTex));
        if (o.isSkinnedMesh) ol.bind(o.skeleton, o.bindMatrix);
        ol.frustumCulled = false;
        ol.name = `${o.name}_outline`;
        ol.renderOrder = queue;
        o.add(ol); // shown and hidden with its mesh
      }
    }
  }

  // Clips by name (the GLB's extras.clips) the shown GLB does not carry, fetched one at a time from the model route
  // (?clip=, the same source as the model) and added: their tracks moved onto the shown model's nodes (retarget), a
  // renderer a clip switches found in the shown model, or, when the GLB left it out (an EX skill's alternative face),
  // taken from the clip's GLB onto the shown skeleton, hidden at rest. Rejects when one cannot be read or does not
  // fit; the clips added before stay.
  async addClips(names, { signal } = {}) {
    for (const name of names) {
      if (this.named.has(name)) continue;
      const gltf = await readGlb(modelUrl(this.key, this.source, name), signal);
      if (this.disposed) {
        discardScene(gltf.scene);
        return;
      }
      const cj = gltf.parser.json,
        cp = gltf.parser;
      const cnodes = await Promise.all((cj.nodes || []).map((_, i) => cp.getDependency("node", i)));
      const clip = gltf.animations[0] && retarget(gltf.animations[0], cj, cnodes, this.nodes, this.json);
      if (!clip) {
        discardScene(gltf.scene);
        throw new Error(`${name}: the clip does not fit the shown model`);
      }
      const textures = await gltfTextures(gltf);
      if (this.disposed || this.named.has(name)) {
        discardScene(gltf.scene);
        for (const t of textures) t.dispose();
        return;
      }
      const extras = cj.animations[0].extras || {};
      const toggles = [];
      for (const t of extras.toggles || []) {
        const key = this.switchOf(t.node, cj, cp, cnodes, gltf, textures);
        if (key != null) toggles.push({ ...t, node: key });
      }
      this.textures.push(...textures);
      this.named.set(name, { clip, ...extras, toggles });
    }
  }

  // The switch (this.switches' key) of the renderer at a hierarchy node an added clip toggles: the shown GLB's
  // renderer of that node, else the one the clip's GLB brought along, moved onto the shown model (hidden at rest)
  switchOf(h, cj, cp, cnodes, gltf, textures) {
    const own = this.json.nodes[h];
    if (!own) return null;
    const at = own.extras?.renderer ?? (own.mesh !== undefined ? h : undefined);
    if (at !== undefined) {
      const object = this.nodes[at];
      if (object && !this.switches.has(at)) this.switches.set(at, { object, rest: object.visible });
      return object ? at : null;
    }
    const key = `added:${h}`;
    if (this.switches.has(key)) return key;
    const src = cj.nodes[h]?.extras?.renderer ?? (cj.nodes[h]?.mesh !== undefined ? h : undefined);
    const holder = src === undefined ? null : cnodes[src];
    if (!holder) return null;
    // the primitives: the node's mesh, or the meshes of a node of several (not its child nodes)
    const parts = holder.isMesh
      ? [holder]
      : holder.children.filter((c) => c.isMesh && cp.associations.get(c)?.nodes === undefined);
    if (!parts.length) return null;
    const bone = (b) => this.nodes[cp.associations.get(b)?.nodes] || null;
    for (const p of parts) {
      if (p.isSkinnedMesh) {
        const bones = p.skeleton.bones.map(bone);
        if (bones.some((b) => !b)) return null;
        p.bind(new THREE.Skeleton(bones, p.skeleton.boneInverses), p.bindMatrix);
      }
    }
    this.dress(parts, gltf, textures, this.nodes);
    // a skinned renderer's node sits under the model's root, untransformed; a static one is the GameObject's own node,
    // whose transform the shown model's node of the same index has
    const skinned = parts[0].isSkinnedMesh;
    const group = new THREE.Group();
    group.name = `${cj.nodes[h].name}_added`;
    for (const p of parts) {
      if (p === holder && !skinned) {
        // the node itself drew it: a copy without the node's transform and its child nodes, with its outline
        const m = new THREE.Mesh(p.geometry, p.material);
        Object.assign(m, { name: p.name, renderOrder: p.renderOrder, frustumCulled: false });
        for (const c of [...p.children]) if (c.name.endsWith("_outline")) m.add(c);
        for (const s of this.spaces) if (s[1] === p) s[1] = m;
        group.add(m);
      } else group.add(p);
    }
    group.visible = false;
    (skinned ? this.nodes[0] : this.nodes[h]).add(group);
    this.switches.set(key, { object: group, rest: false });
    return key;
  }

  // the renderers the playing clip switches on or off at its time (step keys); the others as at rest, as the
  // game's animator writes the defaults of what a state does not animate
  applySwitches() {
    if (!this.switches?.size) return;
    const states = new Map();
    const t = this.action?.time ?? 0;
    for (const s of this.slot?.toggles || []) {
      let on = !!s.keys[0][1];
      for (const [time, state] of s.keys) if (time <= t + 1e-4) on = !!state;
      states.set(s.node, on);
    }
    for (const [node, s] of this.switches) s.object.visible = states.has(node) ? states.get(node) : s.rest;
  }

  // the face and hair shaders read directions in their renderer's space (Unity's unity_ObjectToWorld)
  updateSpaces() {
    for (const [material, object] of this.spaces || [])
      material.uniforms.objectToWorld.value.setFromMatrix4(object.matrixWorld);
  }

  // FxFollower: the halo eases toward a point and rotation fixed to the head bone, clamped in head space
  async setupHalo(f) {
    if (!f || f.node == null || f.target == null) return;
    const p = this.gltf.parser;
    const [halo, head] = await Promise.all([p.getDependency("node", f.node), p.getDependency("node", f.target)]);
    this.halo = {
      halo,
      head,
      f,
      pos: null,
      rot: null,
      rel: new THREE.Vector3(...f.relativePosition),
      relQ: new THREE.Quaternion(...f.relativeRotation),
      min: new THREE.Vector3(...f.clampMin),
      max: new THREE.Vector3(...f.clampMax),
    };
  }

  followHalo(dt, snap = false) {
    const h = this.halo;
    if (!h) return;
    h.head.updateWorldMatrix(true, false);
    const want = h.rel.clone().applyMatrix4(h.head.matrixWorld);
    const wantQ = h.head.getWorldQuaternion(new THREE.Quaternion()).multiply(h.relQ);
    if (!h.pos || snap) {
      h.pos = want.clone();
      h.rot = wantQ.clone();
    }
    const k = (p) => 1 - Math.pow(1 - p, dt * 60);
    h.pos.lerp(want, k(h.f.positionPower));
    h.rot.slerp(wantQ, k(h.f.rotationPower));
    const local = h.head.worldToLocal(h.pos.clone()).clamp(h.min, h.max);
    h.pos.copy(h.head.localToWorld(local));
    const parent = h.halo.parent;
    parent.updateWorldMatrix(true, false);
    h.halo.position.copy(parent.worldToLocal(h.pos.clone()));
    h.halo.quaternion.copy(parent.getWorldQuaternion(new THREE.Quaternion()).invert().multiply(h.rot));
  }

  // the size of the chibi in its first idle frame (halo included). The orbit target stands over the prefab's root
  // (where the character stands, as on the formation screen; weapons and props would pull a box centre aside) at the
  // formation camera's height, and rises to the face as the camera closes in.
  measure() {
    this.mixer.update(0);
    this.applySwitches();
    this.followHalo(0, true);
    this.root.updateMatrixWorld(true);
    const all = new THREE.Box3(),
      body = new THREE.Box3();
    this.root.traverse((o) => {
      if (!o.isMesh || o.name.endsWith("_outline")) return;
      for (let p = o; p; p = p.parent) if (!p.visible) return;
      let box;
      if (o.isSkinnedMesh) {
        o.computeBoundingBox();
        box = o.boundingBox.clone().applyMatrix4(o.matrixWorld);
      } else {
        o.geometry.computeBoundingBox();
        box = o.geometry.boundingBox.clone().applyMatrix4(o.matrixWorld);
      }
      all.union(box);
      if (o.isSkinnedMesh) body.union(box);
    });
    if (body.isEmpty()) body.copy(all);
    if (all.isEmpty()) all.set(new THREE.Vector3(-0.3, 0, -0.3), new THREE.Vector3(0.3, 1, 0.3));
    const origin = this.root.getWorldPosition(new THREE.Vector3());
    this.anchor = anchorNode(this.root);
    this.anchorRest = this.anchor?.getWorldPosition(new THREE.Vector3()) || null;
    const stand = standPoint(origin, this.anchorRest);
    this.size = all.getSize(new THREE.Vector3());
    this.target = new THREE.Vector3(stand.x, origin.y + FORMATION_VIEW.eye, stand.z);
    this.box = { min: all.min.clone().sub(this.target), max: all.max.clone().sub(this.target) };
    this.faceY = body.min.y + (body.max.y - body.min.y) * 0.78; // a chibi's face sits in the top quarter
    this.controls.target.copy(this.target);
    this.follow.set(0, 0, 0);
  }

  // the anchor's travel since the view was measured (followOffset)
  updateFollow() {
    if (!this.anchor || !this.anchorRest) return;
    followOffset(this.anchor.getWorldPosition(new THREE.Vector3()), this.anchorRest, this.follow);
  }

  // the front view, level, as on the formation screen
  homeDirection() {
    return HOME.clone();
  }

  // switch between the framing beside the panel and fullscreen; the orbit angle and the zoom (its place between
  // the bust and the fit, or beyond the fit) stay as they were
  frameTo(mode, snap = false) {
    this.mode = FRAMES[mode] ? mode : "left";
    const f = FRAMES[this.mode];
    if (!this.size) {
      this.view = { x: f.x, y: f.y };
      this.applyView();
      return;
    }
    const fit = frameDistance(this.box, f);
    const range = { fit, min: bustDistance(this.box, this.faceY - this.target.y, f, fit) };
    const offset = this.camera.position.clone().sub(this.controls.target);
    const keep = !snap && !!this.fit && offset.lengthSq() > 0;
    const from = this.range(); // gliding: where it is now
    // a zoom under way: where it heads
    const distance = keep ? carryZoom(this.goal ?? offset.length(), from, range) : fit;
    this.fit = fit;
    this.limits = { min: range.min, max: fit * 1.3 }; // the bust, a step back
    this.camera.near = fit / 50;
    this.camera.far = fit * 20;
    this.moveTo(keep ? offset.normalize() : this.homeDirection(), distance, f, snap, keep ? from : null);
  }

  // an action that asks for the face (ACTIONS "face"): the camera glides round to the character's front as posed now,
  // keeping its distance, height angle and pan; an orbit during the glide takes over (stepTween), and any later one
  // stays, as the view stays on other actions
  turnToFace() {
    this.facing = false;
    if (!this.limits || !this.root) return false;
    this.root.updateMatrixWorld(true);
    const facing = facingOf(facingBones(this.root), new THREE.Vector3(), this.headFront || null);
    if (!facing) return false;
    const offset = this.camera.position.clone().sub(this.controls.target);
    this.moveTo(frontView(facing, offset), this.zoomGoal(), FRAMES[this.mode], false, this.range());
    return true;
  }

  // the head's forward (headFront), from the formation idle as posed now: its face looks into the front view
  readHeadFront() {
    if (!this.root || this.slot?.clip !== this.clips?.get("Formation_Idle")?.clip) {
      this.headFront = false;
      return;
    }
    this.root.updateMatrixWorld(true);
    this.headFront = headFront(facingBones(this.root)) || false;
  }

  // double-click: back to the front view at the fit distance, unpanned (from where a change of framing has brought
  // the zoom range so far)
  home() {
    if (!this.limits) return;
    this.panGoal.set(0, 0, 0);
    this.moveTo(this.homeDirection(), this.fit, FRAMES[this.mode], false, this.range());
  }

  // the zoom range the target's lift spans, {fit, min}; while the camera glides to another framing it goes from the
  // old range to the new one with the camera, so the lift (and the target) does not jump
  range() {
    const r = { fit: this.fit, min: this.limits?.min ?? this.fit },
      tw = this.tween;
    if (!tw?.range0) return r;
    const k = ease(THREE.MathUtils.clamp((performance.now() - tw.t0) / TWEEN, 0, 1));
    return { fit: tw.range0.fit + (r.fit - tw.range0.fit) * k, min: tw.range0.min + (r.min - tw.range0.min) * k };
  }

  // the camera as a direction and distance from the orbit target, so the target can rise while it moves; range0:
  // the zoom range it leaves (a change of framing). The glide takes the distance over from a zoom under way; the
  // limits hold as the zoom's goal is clamped (setZoomGoal) and both ends of a glide lie inside them.
  moveTo(direction, distance, view, snap, range0 = null) {
    const offset = this.camera.position.clone().sub(this.controls.target);
    this.goal = null;
    const tw = (this.tween = {
      t0: performance.now(),
      dir0: offset.lengthSq() ? offset.clone().normalize() : direction.clone(),
      dist0: offset.length() || distance,
      dir1: direction.clone(),
      dist1: distance,
      view0: { ...this.view },
      view1: { x: view.x, y: view.y },
      range0,
    });
    if (snap) tw.t0 = -Infinity;
    this.stepTween();
    if (snap) {
      this.pan.copy(this.panGoal);
      this.liftTarget();
      this.controls.update();
    }
  }

  // the glide's step; an orbit (turned) or a zoom (zoomed) under way since it began keeps its part, and the framing
  // glides on
  stepTween() {
    const tw = this.tween;
    if (!tw) return;
    const t = Math.min(1, (performance.now() - tw.t0) / TWEEN),
      k = ease(t);
    const offset = this.camera.position.clone().sub(this.controls.target);
    const dir = tw.turned ? offset.clone() : new THREE.Vector3().lerpVectors(tw.dir0, tw.dir1, k);
    if (dir.lengthSq() < 1e-8) dir.copy(tw.dir1);
    const distance = tw.zoomed ? offset.length() : tw.dist0 + (tw.dist1 - tw.dist0) * k;
    this.camera.position.copy(this.controls.target).add(dir.normalize().multiplyScalar(distance));
    this.view = { x: tw.view0.x + (tw.view1.x - tw.view0.x) * k, y: tw.view0.y + (tw.view1.y - tw.view0.y) * k };
    this.applyView();
    if (t >= 1) this.tween = null;
  }

  // closing in moves the orbit target from the formation camera's height up to the face (liftShare), so the closest
  // view is the bust, plus the pan; the camera keeps its offset from the target
  liftTarget() {
    if (!this.fit) return;
    const o = this.controls,
      offset = this.camera.position.clone().sub(o.target);
    o.target
      .set(this.target.x, this.target.y + this.lift(offset.length()), this.target.z)
      .add(this.pan)
      .add(this.follow);
    this.camera.position.copy(o.target).add(offset);
  }

  // the target's rise above the formation target at a camera distance
  lift(distance, range = this.range()) {
    return (this.faceY - this.target.y) * liftShare(distance, range);
  }

  // --- zoom and pan (attachInput) ---
  distance() {
    return this.camera.position.distanceTo(this.controls.target);
  }

  // the distance a zoom goes on from: where a zoom under way heads, or where the camera is
  zoomGoal() {
    return this.goal ?? this.distance();
  }

  zoomBy(factor) {
    if (factor > 0 && factor !== 1) this.setZoomGoal(this.zoomGoal() * factor);
  }

  // a zoom during a glide takes the distance over from it (the direction and the framing glide on)
  setZoomGoal(distance) {
    if (!this.limits || !(distance > 0)) return;
    if (this.tween) this.tween.zoomed = true;
    this.goal = clamp(distance, this.limits.min, this.limits.max);
  }

  // ln(fit / bust): the zoom's span from the front view to the bust (wheelZoom)
  span() {
    const { fit, min } = this.range();
    return fit > min ? Math.log(fit / min) : 0;
  }

  // the chibi moves by dx, dy CSS px across the view (the target the other way, at its distance), as far as the zoom
  // lets it (clampPan)
  panBy(dx, dy) {
    if (!this.limits || (!dx && !dy)) return;
    const r = this.canvas.getBoundingClientRect(),
      d = this.distance();
    const unit = (2 * d * Math.tan(THREE.MathUtils.degToRad(this.camera.fov) / 2)) / Math.max(r.height, 1);
    this.camera.updateMatrixWorld();
    const e = this.camera.matrixWorld.elements; // the camera's right (x) and up (y) axes
    this.panGoal.x += (-dx * e[0] + dy * e[4]) * unit;
    this.panGoal.y += (-dx * e[1] + dy * e[5]) * unit;
    this.panGoal.z += (-dx * e[2] + dy * e[6]) * unit;
    clampPan(this.panGoal, zoomShare(d, this.range()), this.box, this.lift(d));
  }

  // every frame: the distance eases to the zoom's goal (in steps of the zoom, as a notch or a pinch makes them) and the
  // pan to its own, which zooming out draws in
  stepZoom(dt) {
    if (!this.limits) return;
    const k = 1 - Math.exp(-dt / ZOOM_TAU);
    if (this.goal != null) {
      const offset = this.camera.position.clone().sub(this.controls.target),
        d = offset.length(),
        g = this.goal;
      const next = Math.abs(Math.log(g / d)) < 1e-4 ? g : d * Math.pow(g / d, k);
      if (next === g) this.goal = null;
      this.camera.position.copy(this.controls.target).addScaledVector(offset.normalize(), next);
    }
    const range = this.range(),
      d = this.distance(),
      s = zoomShare(d, range),
      lift = this.lift(d, range);
    clampPan(this.panGoal, s, this.box, lift);
    this.pan.lerp(this.panGoal, k);
    clampPan(this.pan, s, this.box, lift);
  }

  // --- actions ---
  actions() {
    return [
      ...Object.keys(ACTIONS).filter((name) => ACTIONS[name].slots.some((s) => this.clips?.has(s))),
      ...storyActions(this.clips),
    ];
  }

  act(name, { fade = 0.3, quiet = false } = {}) {
    const def = ACTIONS[name] || storyAction(name);
    const slot = def?.slots.find((s) => this.clips?.has(s));
    if (!slot) return false;
    const next = def.next && this.clips.has(def.next) ? [this.clips.get(def.next)] : [];
    this.begin(name, [this.clips.get(slot), ...next], def, def.fade ?? fade);
    if (!quiet) this.onAction?.(name);
    return true;
  }

  // An action by the names of the clips it plays (the action bar's: clip-names.js actionsOf), the shown GLB's or
  // added ones (addClips); false when one is missing. `like`: the tab's action of ACTIONS it plays as (a second
  // battle form's knockdown holds as the first's), else its clips play in turn, each once, and the last loops or
  // holds by its own loop flag.
  actClips(key, names, { like = null, fade = 0.3 } = {}) {
    const entries = (names || []).map((n) => this.named?.get(n));
    if (!entries.length || entries.some((e) => !e)) return false;
    this.begin(key, entries, ACTIONS[like] || null, ACTIONS[like]?.fade ?? fade);
    return true;
  }

  begin(name, entries, def, fade) {
    this.current = name;
    this.facing = !!def?.face; // turned once the clip has posed the character (tick)
    // the head's forward is read once per model, on the formation idle's first posed frame (tick)
    if (name === "idle" && this.headFront === undefined) this.headFront = null;
    this.chain = { entries, at: 0, def };
    this.playEntry(entries[0], { fade, once: this.onceAt(0) });
  }

  // whether the chain's clip at `at` plays once: an action of ACTIONS by its flags (its follow-up loops by its own
  // flag); else every clip but the last
  onceAt(at) {
    const { entries, def } = this.chain;
    if (def) return at === 0 && !!(def.then || def.next || def.hold);
    return at < entries.length - 1;
  }

  playEntry(entry, { fade = 0.3, once = false } = {}) {
    const a = this.mixer.clipAction(entry.clip);
    const looping = !once && entry.loop !== false;
    a.reset().setLoop(looping ? THREE.LoopRepeat : THREE.LoopOnce, looping ? Infinity : 1);
    a.clampWhenFinished = !looping;
    if (this.action && this.action !== a && fade > 0) {
      a.play();
      a.crossFadeFrom(this.action, fade, false);
    } else {
      // a cut stops every clip, one still fading out of an earlier cross-fade too
      if (fade <= 0) this.mixer.stopAllAction();
      else if (this.action && this.action !== a) this.action.stop();
      a.play();
    }
    this.action = a;
    this.slot = entry;
  }

  // a clip that plays once has ended: the chain's next clip, or the action after it
  finished(action) {
    if (action !== this.action || !this.chain) return;
    const { entries, at, def } = this.chain;
    if (at + 1 < entries.length) {
      this.chain.at = at + 1;
      this.playEntry(entries[at + 1], { fade: def ? 0.2 : 0.15, once: this.onceAt(at + 1) });
    } else if (def?.then) this.act(def.then, { fade: 0.35 });
  }

  updateMouth() {
    if (!this.mouth || !this.action || !this.slot) return;
    let tile = this.mouthTile;
    const t = this.action.time;
    for (const e of this.slot.events || []) if (e.function === "SetMouthTile" && e.time <= t + 1e-4) tile = e.int;
    if (tile == null || tile === this.mouthTile) return;
    this.mouthTile = tile; // kept between clips, like the game
    // int = row * 100 + col; offsets in Unity uv (rows from the bottom)
    const st = this.mouth.st.value;
    st.set(st.x, st.y, (tile % 100) / this.mouth.cols, Math.floor(tile / 100) / this.mouth.rows);
  }

  // a tap on the character plays the cafe touch reaction, then the idle again; a drag still orbits, and during a glide
  // takes the direction over from it. Only a lone press of the left button or one finger taps or orbits.
  enableTap() {
    let down = null;
    const pressed = new Set();
    const moved = (e) => Math.hypot(e.clientX - down.x, e.clientY - down.y) > 6;
    this.canvas.addEventListener("pointerdown", (e) => {
      if (e.isPrimary) pressed.clear(); // the first finger, or a mouse: nothing else is down (a lost release)
      pressed.add(e.pointerId);
      down =
        pressed.size === 1 && e.button === 0
          ? { id: e.pointerId, x: e.clientX, y: e.clientY, t: performance.now() }
          : null;
    });
    this.canvas.addEventListener("pointermove", (e) => {
      if (down?.id === e.pointerId && this.tween && moved(e)) this.tween.turned = true;
    });
    const lift = (e) => pressed.delete(e.pointerId);
    this.canvas.addEventListener("pointercancel", (e) => {
      lift(e);
      down = null;
    });
    this.canvas.addEventListener("pointerup", (e) => {
      lift(e);
      if (down?.id !== e.pointerId || moved(e) || performance.now() - down.t > 400) {
        down = null;
        return;
      }
      down = null;
      if (this.root && this.current !== "reaction" && this.hit(e)) this.act("reaction", { fade: 0.2 });
    });
  }

  hit(e) {
    const r = this.canvas.getBoundingClientRect(); // includes the stage's CSS scale
    const p = new THREE.Vector2(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1);
    const ray = new THREE.Raycaster();
    ray.setFromCamera(p, this.camera);
    const visible = (o) => {
      for (let q = o; q; q = q.parent) if (!q.visible) return false;
      return true;
    };
    return ray
      .intersectObjects(this.root.children, true)
      .some((h) => !h.object.name.endsWith("_outline") && visible(h.object));
  }

  // the render loop; resolves once the first frame is on the canvas (or the view is disposed first)
  start() {
    this.clock.getDelta();
    return new Promise((drawn) => {
      this.drawn = drawn;
      this.renderer.setAnimationLoop(() => {
        // under Now Loading the shown model holds its pose: drawn again (a resize clears the canvas), not advanced
        if (this.frozen) {
          this.renderer.render(this.scene, this.camera);
          return;
        }
        this.tick();
        drawn();
      });
    });
  }

  // shaders before the first frame, in the background where the GPU driver can. Polled here rather than with
  // renderer.compileAsync, whose timer throws (and never settles) once the view is disposed meanwhile.
  async compile() {
    const r = this.renderer;
    let pending;
    try {
      pending = r.compile(this.scene, this.camera);
    } catch {
      return;
    } // compiled on the first frame instead
    if (!r.extensions.has("KHR_parallel_shader_compile")) return;
    // isReady() is false while the driver compiles (null once the context is lost: nothing to wait for)
    while (pending.size && !this.disposed) {
      for (const m of pending) if (r.properties.get(m).currentProgram?.isReady() !== false) pending.delete(m);
      if (pending.size) await new Promise((resolve) => setTimeout(resolve, 10));
    }
  }

  tick() {
    const dt = Math.min(0.1, this.clock.getDelta());
    this.mixer?.update(dt);
    if (this.headFront === null) this.readHeadFront();
    if (this.facing) this.turnToFace();
    this.applySwitches();
    this.updateMouth();
    this.followHalo(dt);
    this.updateFollow();
    this.stepTween();
    this.stepZoom(dt);
    this.liftTarget();
    this.controls.update();
    this.root?.updateMatrixWorld();
    this.updateSpaces();
    this.fx?.update(dt, this.camera);
    setOutlineScale(this.host.clientWidth || 1, this.host.clientHeight || 1);
    this.renderer.render(this.scene, this.camera);
  }

  // The live tab's shaders, idle, halo and formation camera with an explicit off-screen clock.
  // Freeze and validate before encoding: no RAF, blank cached frames or disk writes.
  async renderStill(signal) {
    this.frameTo("still", true);
    this.camera.lookAt(this.controls.target);
    await this.compile();
    signal.throwIfAborted();
    this.mixer.update(0);
    this.applySwitches();
    this.updateMouth();
    this.followHalo(0, true);
    this.root.updateMatrixWorld(true);
    this.updateSpaces();
    this.fx?.settle(this.camera); // the halo's particles at a fixed moment: the same still every time
    setOutlineScale(640, 640, 640);
    this.renderer.render(this.scene, this.camera);
    const gl = this.renderer.getContext(),
      pixels = new Uint8Array(640 * 640 * 4);
    if (gl.isContextLost()) throw new Error("3D still: WebGL context lost");
    gl.readPixels(0, 0, 640, 640, gl.RGBA, gl.UNSIGNED_BYTE, pixels);
    const frame = visibleStillFrame(pixels, 640, 640);
    const camera = this.camera.clone();
    camera.setViewOffset(640, 640, frame.x, frame.y, frame.w, frame.h);
    this.renderer.render(this.scene, camera);
    if (gl.isContextLost()) throw new Error("3D still: WebGL context lost");
    pixels.fill(0);
    gl.readPixels(0, 0, 640, 640, gl.RGBA, gl.UNSIGNED_BYTE, pixels);
    if (!pixels.some((value, i) => i % 4 === 3 && value > 0)) throw new Error("3D still: no visible pixels");
    const frozen = document.createElement("canvas");
    frozen.width = frozen.height = 640;
    frozen.getContext("2d").drawImage(this.canvas, 0, 0);
    return new Promise((resolve, reject) =>
      frozen.toBlob((blob) => {
        if (signal.aborted) resolve(null);
        else if (!blob) reject(new Error("3D still: PNG encoding failed"));
        else resolve(URL.createObjectURL(blob));
      }, "image/png"),
    );
  }

  // a still of the upper body for the switch card, from the front view at the fit distance in the current framing
  // whatever the orbit and the zoom (stillCamera), cropped from the box as rendered (stillCrop). Rendered and read
  // in one go (the drawing buffer is not kept), then the live view is drawn again before the canvas is shown.
  snapshot(aspect = 450 / 253) {
    if (!this.root || !this.fit) return null;
    const w = this.host.clientWidth || 1,
      h = this.host.clientHeight || 1;
    const target = this.target.clone().add(this.follow); // where the character is now (followOffset)
    const camera = stillCamera(this.camera, target, this.fit, FRAMES[this.mode], w, h);
    this.renderer.render(this.scene, camera);
    const { x, y, w: cw, h: ch } = stillCrop(camera, target, this.box, this.canvas.width, this.canvas.height, aspect);
    let still = null;
    if (ch >= 1) {
      const c = document.createElement("canvas");
      c.width = 900;
      c.height = Math.round(900 / aspect);
      c.getContext("2d").drawImage(this.canvas, x, y, cw, ch, 0, 0, c.width, c.height);
      still = c.toDataURL("image/png");
    }
    this.renderer.render(this.scene, this.camera);
    return still;
  }

  reveal() {
    this.canvas.classList.add("ready");
    this.loadingFreeze ||= freezeWhileLoading({
      shown: () => !this.disposed && onScreen(this.canvas),
      freeze: () => {
        this.frozen = true;
      },
      thaw: () => {
        this.frozen = false;
        this.clock.getDelta();
      },
    });
  }

  dispose() {
    if (this.disposed) return;
    this.disposed = true;
    this.loadingFreeze?.dispose();
    this.renderer.setAnimationLoop(null);
    this.drawn?.();
    this.observer?.disconnect();
    if (this.onWindow) removeEventListener("resize", this.onWindow);
    this.dpr?.removeEventListener("change", this.onPixelRatio);
    this.detachInput?.();
    this.controls.dispose();
    this.mixer?.stopAllAction();
    this.fx?.dispose();
    this.scene.traverse((o) => {
      o.geometry?.dispose();
      for (const m of [].concat(o.material || [])) {
        for (const v of Object.values(m)) if (v?.isTexture) v.dispose();
        m.dispose();
      }
    });
    for (const t of this.textures || []) t.dispose();
    if (--viewCount === 0) disposeDefaults();
    this.renderer.dispose();
    this.renderer.forceContextLoss();
    this.canvas.remove();
  }
}

/**
 * Make a view for stills of the model, not shown on screen.
 * @param {object} [options]
 */
export const createStillView = (options = {}) => new ChibiView(null, null, { ...options, still: true });

let view = null,
  token = 0;

/**
 * Load and show an alt's chibi. The shown view is kept for the same alt of the same install (version:
 * the game's resource version; another game folder or file source loads it again).
 * @param {object} [options]
 * @param {string} [options.frame] The framing, "left" or "full".
 * @param {((name: string) => void)|null} [options.onAction] Follows the current action when it changes by
 *     itself (a tap, a reaction ending).
 * @param {string|null} [options.version] The game's resource version.
 * @returns {Promise<string[]|null>} The action names once the first frame is drawn; null when another show or a
 *     hide came first.
 */
export async function show(key, host, { frame: mode = "left", onAction = null, version = null, source = "live" } = {}) {
  if (view?.key === key && view.host === host && view.version === version && view.source === source) {
    view.onAction = onAction;
    if (view.mode !== mode) view.frameTo(mode);
    return view.ready;
  }
  hide(host);
  const my = ++token;
  const v = (view = new ChibiView(host, key));
  v.version = version;
  v.source = source;
  v.onAction = onAction;
  v.frameTo(mode); // before the load this only records the framing
  v.ready = (async () => {
    try {
      await v.load(modelUrl(key, source));
    } catch (err) {
      if (my !== token) return null;
      hide(host);
      throw err;
    }
    if (my !== token) return null;
    // the framing asked for last: fullscreen may have been entered or left while the model loaded
    v.frameTo(v.mode, true);
    await v.compile();
    if (my !== token) return null;
    await v.start();
    if (my !== token) return null;
    v.reveal();
    return v.actions();
  })();
  return v.ready;
}

/** Free the view and its WebGL context. */
export function hide(host) {
  token += 1;
  if (view) {
    view.dispose();
    view = null;
  }
  if (host) host.replaceChildren();
}

/**
 * Change the framing: the camera glides to it.
 * @param {"left"|"full"} mode
 */
export function frame(mode) {
  view?.frameTo(mode);
}
/** Wait until the camera has arrived at its framing. */
export function settled() {
  return new Promise((resolve) => {
    const check = () => (view?.tween ? requestAnimationFrame(check) : resolve());
    check();
  });
}
/**
 * The shown model's actions.
 * @returns {object[]} The actions; empty before a model is shown.
 */
export const actions = () => view?.actions() || [];
/**
 * The action in play.
 * @returns {string|null}
 */
export const current = () => view?.current || null;
/**
 * Play an action by its name.
 * @returns {boolean} False when it cannot play.
 */
export function play(name) {
  return view?.act(name, { quiet: true }) || false;
}
/**
 * The shown model's clips by name (the GLB's extras.clips), for the action bar (clip-names.js actionsOf); empty
 * before a model is shown.
 * @returns {string[]}
 */
export const clipNames = () => view?.json?.extras?.clips || [];
/**
 * The shown model's prefab dev name.
 * @returns {string} The name; empty before a model is shown.
 */
export const devName = () => view?.json?.extras?.dev || "";
/**
 * Whether every clip named is there to play (the GLB's, or added).
 * @param {string[]} names
 * @returns {boolean} True when they are.
 */
export const hasClips = (names) => !!view?.named && (names || []).every((n) => view.named.has(n));
/**
 * Add the clips named that the GLB does not carry to the shown view (ChibiView.addClips).
 * @param {string[]} names
 * @returns {Promise<boolean>} Settles false when the view changed meanwhile.
 */
export async function loadClips(names, { signal } = {}) {
  const v = view;
  if (!v?.named) return false;
  await v.addClips(names, { signal });
  return v === view && !v.disposed;
}
/**
 * Play an action of the bar: today's actions (ACTIONS) of the first battle form play by their slots; any other
 * by its clips (actClips), which must be there (hasClips).
 * @param {{key: string, id: string, form?: number, clips?: string[]}} a
 * @returns {boolean} False when it cannot play.
 */
export function playAction(a) {
  if (!view || !a) return false;
  if (ACTIONS[a.id] && (a.form || 0) <= 1 && view.act(a.id, { quiet: true })) {
    view.current = a.key;
    return true;
  }
  return view.actClips(a.key, a.clips, { like: ACTIONS[a.id] ? a.id : null });
}
/**
 * The shown view's state, for checks.
 * @returns {object|null} The state; null without a view.
 */
export const snapshot = () => view?.snapshot() || null;
/**
 * For checks: the camera's distance, the zoom's goal, range and place, the target's lift, the pan and the
 * follow, and where the anchor (the hips) projects on the canvas (-1..1 across and up).
 * @returns {object|null}
 */
export function zoomState() {
  if (!view?.limits) return null;
  const d = view.distance(),
    range = view.range();
  return {
    distance: d,
    goal: view.goal,
    fit: view.fit,
    min: view.limits.min,
    max: view.limits.max,
    share: zoomShare(d, range),
    lift: view.lift(d, range),
    pan: view.pan.toArray(),
    panGoal: view.panGoal.toArray(),
    gliding: !!view.tween,
    follow: view.follow.toArray(),
    anchor: view.anchor?.name || null,
    anchorView: view.anchor
      ? view.anchor.getWorldPosition(new THREE.Vector3()).project(view.camera).toArray().slice(0, 2)
      : null,
  };
}
