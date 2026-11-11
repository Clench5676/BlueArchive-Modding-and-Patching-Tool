// What a portrait rig draws, pose by pose, for the remap's world-space rebake (rebake.py): per drawn slot its
// region, page, blend, alpha, world vertices, page UVs and triangles. The caller supplies the shipped Spine runtime;
// all posing uses its public API.

const round = (value, scale) => Math.round(value * scale) / scale;
// as atlas_remap.pair_atlases pairs them: the file name without directory, case and extension
const stem = (name) => name.replaceAll("\\", "/").split("/").at(-1).toLowerCase().replace(/\.png$/, "")
  .replace(/\.[^.]*$/, "");

function bytesOf(input) {
  if (ArrayBuffer.isView(input) || typeof input === "string") return input;
  return Uint8Array.from(atob(input.data), (c) => c.charCodeAt(0));
}

function load(S, atlasText, skeletonData) {
  const atlas = new S.TextureAtlas(atlasText);
  atlas.setTextures({ get: () => ({ getImage: () => ({ width: 1, height: 1 }), setFilters() {}, setWraps() {} }) });
  // A region the atlas lacks draws nothing: such an attachment is kept out of the poses.
  const inner = new S.AtlasAttachmentLoader(atlas), fallback = atlas.regions[0], missing = new Set();
  const lenient = (make, Type) => (skin, name, path, sequence) => {
    try { return make(skin, name, path, sequence); } catch {
      const attachment = new Type(name, path || name);
      if (fallback) attachment.region = fallback;
      missing.add(attachment);
      return attachment;
    }
  };
  const loader = {
    newRegionAttachment: lenient(inner.newRegionAttachment.bind(inner), S.RegionAttachment),
    newMeshAttachment: lenient(inner.newMeshAttachment.bind(inner), S.MeshAttachment),
    newBoundingBoxAttachment: (skin, name) => inner.newBoundingBoxAttachment(skin, name),
    newPathAttachment: (skin, name) => inner.newPathAttachment(skin, name),
    newPointAttachment: (skin, name) => inner.newPointAttachment(skin, name),
    newClippingAttachment: (skin, name) => inner.newClippingAttachment(skin, name),
  };
  let data = bytesOf(skeletonData);
  if (ArrayBuffer.isView(data)) {
    const text = new TextDecoder().decode(data).trim();
    if (text.startsWith("{")) data = text;
  }
  data = typeof data === "string" ? new S.SkeletonJson(loader).readSkeletonData(data)
    : new S.SkeletonBinary(loader).readSkeletonData(data);
  return { data, missing };
}

function idleOf(data) {
  return data.findAnimation("Idle_01") ?? data.animations.find((a) => /^idle/i.test(a.name)) ?? null;
}

function pose(S, data, missing, idle, overlay) {
  const skeleton = new S.Skeleton(data), state = new S.AnimationState(new S.AnimationStateData(data));
  skeleton.setToSetupPose();
  if (idle) state.setAnimation(0, idle.name, true);
  if (overlay) state.setAnimation(1, overlay, true);
  state.apply(skeleton);
  skeleton.updateWorldTransform(S.Physics.update);
  const drawn = [];
  for (const slot of skeleton.drawOrder) {
    const a = slot.getAttachment();
    if (!a || !slot.bone.active || missing.has(a)) continue;
    let vertices, triangles;
    if (a instanceof S.RegionAttachment) {
      vertices = new Float32Array(8); a.computeWorldVertices(slot, vertices, 0, 2); triangles = [0, 1, 2, 2, 3, 0];
    } else if (a instanceof S.MeshAttachment) {
      vertices = new Float32Array(a.worldVerticesLength);
      a.computeWorldVertices(slot, 0, a.worldVerticesLength, vertices, 0, 2);
      triangles = Array.from(a.triangles);
    } else continue;
    const alpha = slot.color.a * (a.color ? a.color.a : 1);
    const numbers = [...vertices, ...a.uvs, alpha];
    if (!numbers.every(Number.isFinite)) throw new Error("Non-finite posed geometry");
    drawn.push({ slot: slot.data.name, region: a.region.name, page: a.region.page.name, blend: slot.data.blendMode,
      alpha: round(alpha, 1000), vertices: Array.from(vertices, (v) => round(v, 1000)),
      uvs: Array.from(a.uvs, (v) => round(v, 1e6)), triangles });
  }
  return drawn;
}

// The rest pose "" (the idle on track 0 and "00" on track 1 where the skeleton has them, track time 0) and one pose
// per other animation by its name (the idle on track 0, the animation on track 1).
export function poses(S, atlasText, skeletonData) {
  const { data, missing } = load(S, atlasText, skeletonData);
  const idle = idleOf(data), rest = data.findAnimation("00") ? "00" : null;
  const result = { "": pose(S, data, missing, idle, rest) };
  for (const animation of data.animations) {
    if (animation === idle || animation.name === rest) continue;
    result[animation.name] = pose(S, data, missing, idle, animation.name);
  }
  return { slots: Array.from(data.slots, (slot) => slot.name), poses: result };
}

const byStem = (items, name) => Object.entries(items || {}).find(([other]) => stem(other) === stem(name));

// The rig a portrait mod's textures were drawn for and the official rig, per official atlas that has a skeleton of
// the same stem. The source: the mod's own atlas and skeleton where its files carry them (report.mod is the mod's
// assets over the official ones: an asset that differs from the official one is the mod's), else the recorded ones
// (report.source), else the official ones. ModLibrary.remap makes the same choice from the files.
export function forRemap(report, S = globalThis.spine) {
  if (report?.kind !== "spr") return {};
  const official = report.official, mod = report.mod || {}, recorded = report.source || {};
  const result = {};
  for (const [name, atlas] of Object.entries(official.atlases)) {
    const skeleton = byStem(official.skeletons, name);
    if (!skeleton) continue;
    const own = (items, value) => {
      const found = byStem(items, name);
      return found && (typeof value === "string" ? found[1] !== value : found[1].data !== value.data) ? found[1] : null;
    };
    const ownAtlas = own(mod.atlases, atlas), ownSkeleton = own(mod.skeletons, skeleton[1]);
    const savedAtlas = byStem(recorded.atlases, name)?.[1], savedSkeleton = byStem(recorded.skeletons, name)?.[1];
    const sourceAtlas = ownAtlas ?? savedAtlas ?? atlas;
    const sourceSkeleton = ownSkeleton ?? (ownAtlas == null && savedAtlas != null ? savedSkeleton : null) ?? skeleton[1];
    result[name] = { source: poses(S, sourceAtlas, sourceSkeleton), target: poses(S, atlas, skeleton[1]) };
  }
  return result;
}

// Rest poses only: what the redrawn check needs.
export function restOnly(geometry) {
  return Object.fromEntries(Object.entries(geometry).map(([name, { source, target }]) => [name, {
    source: { slots: source.slots, poses: { "": source.poses[""] } },
    target: { slots: target.slots, poses: { "": target.poses[""] } } }]));
}

// The setup pose of a rig for the graft (graft.py setup): per bone its world matrix [a, b, c, d, x, y], per slot its
// bone, and per skin attachment that draws a region its page, world vertices, page UVs and triangles.
export function setupOf(S, atlasText, skeletonData) {
  const { data, missing } = load(S, atlasText, skeletonData);
  const skeleton = new S.Skeleton(data);
  skeleton.setToSetupPose();
  skeleton.updateWorldTransform(S.Physics.update);
  const finite = (values) => { if (!values.every(Number.isFinite)) throw new Error("Non-finite setup geometry"); return values; };
  const bones = Object.fromEntries(skeleton.bones.map((b) => [b.data.name,
    finite([b.a, b.b, b.c, b.d, b.worldX, b.worldY]).map((v) => round(v, 1e6) || 0)]));
  const slotBones = Object.fromEntries(Array.from(data.slots, (slot) => [slot.name, slot.boneData.name]));
  const skin = [];
  for (const sk of data.skins) for (const entry of sk.getAttachments()) {
    const a = entry.attachment, slot = skeleton.slots[entry.slotIndex];
    if (missing.has(a) || !a.region || !a.uvs) continue;
    let world, triangles;
    if (a instanceof S.RegionAttachment) {
      world = new Float32Array(8); a.computeWorldVertices(slot, world, 0, 2); triangles = [0, 1, 2, 2, 3, 0];
    } else if (a instanceof S.MeshAttachment) {
      // the attachment as set up: no deform of the slot's current one
      world = new Float32Array(a.worldVerticesLength);
      const deform = slot.deform;
      slot.deform = [];
      a.computeWorldVertices(slot, 0, a.worldVerticesLength, world, 0, 2);
      slot.deform = deform;
      triangles = Array.from(a.triangles);
    } else continue;
    skin.push({ slot: slot.data.name, name: entry.name, page: a.region.page.name,
      world: finite(Array.from(world)).map((v) => round(v, 1000)), uvs: finite(Array.from(a.uvs)).map((v) => round(v, 1e6)),
      tris: triangles });
  }
  return { bones, slotBones, skin };
}

// Poses for the graft's check (graft.py choose), in rebake.rig's shape: the rest pose left empty and, per animation of
// `check` (name -> slots) the skeleton has, what those slots draw with the idle on track 0 and the animation on track 1.
export function partPoses(S, atlasText, skeletonData, check) {
  const { data, missing } = load(S, atlasText, skeletonData);
  const idle = idleOf(data);
  const result = { "": [] };
  for (const [name, slots] of Object.entries(check || {})) {
    if (!data.findAnimation(name)) continue;
    const wanted = new Set(slots);
    result[name] = pose(S, data, missing, idle, name).filter((item) => wanted.has(item.slot));
  }
  return { slots: Array.from(data.slots, (slot) => slot.name), poses: result };
}
