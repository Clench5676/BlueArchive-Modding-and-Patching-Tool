// Verified LobbyCommon and LobbyCommonNight: identity ancestors, TweenController/CharPos at (-112, -886),
// scale 111. Ordinary portrait SkeletonDataAssets use .01. The game's 16:9 UI camera spans 2960 x 1665 units.
// These are a projection baseline, not a claim about every runtime camera/character composition.
export const LOBBY_CHARACTER_POS = Object.freeze({ pos: [-112, -886], scale: [111, 111] });
export const LOBBY_UI_WIDTH = 2960;
export const PORTRAIT_SKELETON_SCALE = .01;
export const PORTRAIT_BASE_SCALE = 111 * PORTRAIT_SKELETON_SCALE * 1920 / LOBBY_UI_WIDTH;
export const PORTRAIT_ANCHOR = Object.freeze({ x: 960 - 112 * 1920 / LOBBY_UI_WIDTH, y: 540 + 886 * 1920 / LOBBY_UI_WIDTH });
export const HALL_SCALE = Object.freeze({ min: .25, max: 1.5, default: 1 });
export const hallScale = (value) => Number.isFinite(value) ? Math.max(HALL_SCALE.min, Math.min(HALL_SCALE.max, value)) : HALL_SCALE.default;
export const portraitScale = (entry, globalScale = HALL_SCALE.default) => PORTRAIT_BASE_SCALE * entry.scale * hallScale(globalScale);

export function portraitRect(entry, bounds = null, globalScale = HALL_SCALE.default) {
  const source = bounds || { x: -195, y: 0, width: 390, height: 850 }, scale = portraitScale(entry, globalScale);
  return { x: entry.x + (entry.mirror ? -source.x - source.width : source.x) * scale, y: entry.y - (source.y + source.height) * scale,
    width: source.width * scale, height: source.height * scale };
}

// Query the runtime's posed attachment geometry; this maps one point to atlas alpha, never rasterizes a mesh.
export const VISIBLE_ALPHA = .1;
const QUAD = [0, 1, 2, 2, 3, 0];
const pages = new WeakMap(), vertices = new WeakMap();
// Picking needs alpha only coarsely: a page is read at most ALPHA_SIDE texels on its longer side, so a large Mod atlas
// costs milliseconds, not seconds, and its canvas is released at once. A page that reads as wholly transparent is a
// failed read (WebKit refuses canvases past its memory budget): it counts as opaque rather than hiding the portrait.
export const ALPHA_SIDE = 1024;
function alphaSize(image) {
  const fit = Math.min(1, ALPHA_SIDE / Math.max(image.width, image.height, 1));
  return [Math.max(1, Math.round(image.width * fit)), Math.max(1, Math.round(image.height * fit))];
}
function readAlpha(texture, source, width, height) {
  const canvas = document.createElement("canvas");
  canvas.width = width; canvas.height = height;
  const context = canvas.getContext("2d", { willReadFrequently: true });
  context.drawImage(source, 0, 0, width, height);
  const rgba = context.getImageData(0, 0, width, height).data;
  canvas.width = canvas.height = 0;
  const alpha = new Uint8Array(width * height);
  let any = 0;
  for (let i = 0; i < alpha.length; i++) any |= alpha[i] = rgba[i * 4 + 3];
  const page = any ? { width, height, alpha } : null; pages.set(texture, page); return page;
}
export function pageAlpha(texture) {
  if (pages.has(texture)) return pages.get(texture);
  const image = texture.getImage();
  return readAlpha(texture, image, ...alphaSize(image));
}
// The browser decodes and shrinks the page off the main thread; only the small read stays on it. That holds for the
// page's bytes: an <img> source would be decoded on the main thread again (120 ms for a 2048 page).
async function shrunkBitmap(image, width, height, load = fetch) {
  const options = { resizeWidth: width, resizeHeight: height, resizeQuality: "low" };
  if (typeof image?.src === "string" && image.src) {
    try { const response = await load(image.src); if (response.ok) return await createImageBitmap(await response.blob(), options); } catch {}
  }
  try { return await createImageBitmap(image, options); } catch { return null; }
}
async function loadPageAlpha(texture, load) {
  const image = texture.getImage(), [width, height] = alphaSize(image);
  const bitmap = await shrunkBitmap(image, width, height, load);
  if (pages.has(texture)) { bitmap?.close?.(); return pages.get(texture); }
  try { return readAlpha(texture, bitmap || image, width, height); } finally { bitmap?.close?.(); }
}
// Pages read so far; null means not read yet or unreadable, and picking then treats the attachment as opaque.
export const readyAlpha = (texture) => pages.get(texture) || null;
// Read the pages of skeleton data one per task, so entering layout editing never blocks on them.
export function preparePickPages(data, schedule = (task) => setTimeout(task), load = globalThis.fetch) {
  const textures = new Set();
  for (const skin of data.skins || []) for (const { attachment } of skin.getAttachments()) {
    if (attachment.region?.texture && !pages.has(attachment.region.texture)) textures.add(attachment.region.texture);
  }
  return [...textures].reduce((chain, texture) => chain.then(() => new Promise((resolve) => schedule(resolve))).then(async () => {
    try { if (!pages.has(texture)) await (typeof createImageBitmap === "function" ? loadPageAlpha(texture, load) : pageAlpha(texture)); }
    catch (error) { pages.set(texture, null); console.warn("Hall pick alpha unavailable", error); }
  }), Promise.resolve());
}
// Atlas UVs already include trim and rotation. Match the page's linear filtering and clamp at its edges.
export function sampleAlpha(page, u, v) {
  const x = u * page.width - .5, y = v * page.height - .5, ix = Math.floor(x), iy = Math.floor(y);
  const at = (px, py) => page.alpha[Math.max(0, Math.min(page.height - 1, py)) * page.width + Math.max(0, Math.min(page.width - 1, px))] / 255;
  const fx = x - ix, fy = y - iy;
  return (at(ix, iy) * (1 - fx) + at(ix + 1, iy) * fx) * (1 - fy)
    + (at(ix, iy + 1) * (1 - fx) + at(ix + 1, iy + 1) * fx) * fy;
}
export function visibleSlot(slot) { return slot.bone.active && slot.data.blendMode === 0; }
export function skeletonContains(skeleton, x, y, S, alphaOf = readyAlpha) {
  for (const slot of skeleton.drawOrder || []) {
    if (!visibleSlot(slot)) continue;
    const a = slot.getAttachment();
    if (!a) continue;
    const opacity = skeleton.color.a * slot.color.a * a.color?.a;
    if (!(opacity >= VISIBLE_ALPHA)) continue;
    let count, triangles;
    if (a instanceof S.RegionAttachment) { count = 8; triangles = QUAD; }
    else if (a instanceof S.MeshAttachment) { count = a.worldVerticesLength; triangles = a.triangles; }
    else continue;
    let world = vertices.get(a);
    if (!world || world.length !== count) vertices.set(a, (world = new Float32Array(count)));
    if (a instanceof S.RegionAttachment) a.computeWorldVertices(slot, world, 0, 2);
    else a.computeWorldVertices(slot, 0, count, world, 0, 2);
    if (trianglesAlpha(world, triangles, a.uvs, alphaOf(a.region.texture), opacity, x, y)) return true;
  }
  return false;
}
// The visible head of a posed skeleton, by the picker's own rule (Normal-blend active slots, effective alpha at least
// VISIBLE_ALPHA): the topmost visible point and the horizontal centre of the visible band below it (the top tenth of
// the visible height). Spine's bounds also count transparent and off-body parts, so they can sit to either side of
// the character. World units, y up; null when nothing is visible.
export function visibleHead(skeleton, S, alphaOf = readyAlpha, step = 8) {
  const parts = [];
  for (const slot of skeleton.drawOrder || []) {
    if (!visibleSlot(slot)) continue;
    const a = slot.getAttachment();
    if (!a) continue;
    const opacity = skeleton.color.a * slot.color.a * a.color?.a;
    if (!(opacity >= VISIBLE_ALPHA)) continue;
    let world, triangles;
    if (a instanceof S.RegionAttachment) { world = new Float32Array(8); a.computeWorldVertices(slot, world, 0, 2); triangles = QUAD; }
    else if (a instanceof S.MeshAttachment) { world = new Float32Array(a.worldVerticesLength); a.computeWorldVertices(slot, 0, world.length, world, 0, 2); triangles = a.triangles; }
    else continue;
    let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    for (let i = 0; i < world.length; i += 2) { minX = Math.min(minX, world[i]); maxX = Math.max(maxX, world[i]); minY = Math.min(minY, world[i + 1]); maxY = Math.max(maxY, world[i + 1]); }
    parts.push({ world, triangles, uv: a.uvs, page: alphaOf(a.region.texture), opacity, minX, minY, maxX, maxY });
  }
  if (!parts.length) return null;
  const hit = (x, y) => parts.some((part) => x >= part.minX && x <= part.maxX && y >= part.minY && y <= part.maxY
    && trianglesAlpha(part.world, part.triangles, part.uv, part.page, part.opacity, x, y));
  const left = Math.min(...parts.map((part) => part.minX)), right = Math.max(...parts.map((part) => part.maxX));
  const bottom = Math.min(...parts.map((part) => part.minY));
  let top = null;
  for (let y = Math.max(...parts.map((part) => part.maxY)); y >= bottom && top === null; y -= step)
    for (let x = left; x <= right; x += step) if (hit(x, y)) { top = y; break; }
  if (top === null) return null;
  let from = Infinity, to = -Infinity;
  // The band's width needs only every other row.
  for (let y = top; y >= top - Math.max(step, (top - bottom) / 10); y -= 2 * step)
    for (let x = left; x <= right; x += step) if (hit(x, y)) { from = Math.min(from, x); to = Math.max(to, x); }
  return { x: (from + to) / 2, y: top };
}
function trianglesAlpha(world, triangles, uv, page, opacity, x, y) {
  for (let i = 0; i < triangles.length; i += 3) {
    const p = triangles[i] * 2, q = triangles[i + 1] * 2, r = triangles[i + 2] * 2;
    const ax = world[p], ay = world[p + 1], bx = world[q] - ax, by = world[q + 1] - ay;
    const cx = world[r] - ax, cy = world[r + 1] - ay, det = bx * cy - by * cx;
    if (!det) continue;
    const dx = x - ax, dy = y - ay, b = (dx * cy - dy * cx) / det, c = (bx * dy - by * dx) / det, aWeight = 1 - b - c;
    if (b < 0 || c < 0 || aWeight < 0) continue;
    if (!page) return true;
    if (opacity * sampleAlpha(page, aWeight * uv[p] + b * uv[q] + c * uv[r], aWeight * uv[p + 1] + b * uv[q + 1] + c * uv[r + 1]) >= VISIBLE_ALPHA) return true;
  }
  return false;
}
export function pickPortrait(actors, x, stageY, S, alphaOf = readyAlpha) {
  const ordered = [...actors.values()];
  for (let i = ordered.length - 1; i >= 0; i--) {
    if (skeletonContains(ordered[i].player.skeleton, x, 1080 - stageY, S, alphaOf)) return ordered[i].entry.id;
  }
  return null;
}
// One surface owns pointer input; the named DOM buttons remain focusable for keyboard/AT activation.
// WebKit (the macOS app's WKWebView) may end a captured press with only a click, without pointerup or
// lostpointercapture. A press therefore ends on whichever comes first: pointerup, click, lost capture or a move with no
// button down, at the last known point. Only pointercancel ends it without a release point.
export function bindPortraitPointer(surface, { root, pick, hover, down, move, end, enabled = () => true }) {
  let held = null;
  const point = (event) => {
    const box = root.getBoundingClientRect(), scale = 1920 / box.width;
    return { x: (event.clientX - box.left) * scale, y: (event.clientY - box.top) * scale };
  };
  const ours = (event) => !!held && (event.pointerId === undefined || event.pointerId === held.pointerId);
  surface.addEventListener("pointermove", (event) => {
    if (!enabled() || held && !ours(event)) return;
    if (held !== null && event.buttons === 0) { finish(held.last); return; }
    const p = point(event);
    if (held !== null) { held.last = p; move(held, p); } else hover(pick(p.x, p.y), null, p);
  });
  surface.addEventListener("pointerdown", (event) => {
    if (!enabled() || held !== null || event.button !== 0) return;
    const p = point(event); held = { id: pick(p.x, p.y), start: p, last: p, pointerId: event.pointerId };
    down(held, p); surface.setPointerCapture?.(event.pointerId); event.preventDefault();
  });
  function finish(p) {
    if (!held) return;
    const previous = held; held = null;
    if (surface.hasPointerCapture?.(previous.pointerId)) surface.releasePointerCapture(previous.pointerId);
    if (enabled()) { if (p) move(previous, p); end(previous, p); }
  }
  surface.addEventListener("pointerup", (event) => { if (ours(event)) finish(point(event)); });
  surface.addEventListener("click", (event) => { if (held) finish(point(event)); });
  surface.addEventListener("lostpointercapture", (event) => { if (ours(event)) finish(held.last); });
  surface.addEventListener("pointercancel", (event) => { if (ours(event)) finish(null); });
  surface.addEventListener("pointerleave", (event) => { if (!held) hover(null, event.relatedTarget, null); });
}

export const PORTRAIT_SIZE = Object.freeze({ min: .5, max: 2, default: 1 });
// App-owned packing around native 62px icon buttons and the UILobby 104-unit slider body. The two rows never overlap.
// Centred directly above the portrait's visible head (`head`, stage units) and touching it, so the pointer can travel
// from the portrait into the toolbar; without a head, above the portrait's box. Where that would leave the stage or cover
// a control (the top bar, the Hall's top-right buttons and slider), it goes under the portrait's feet instead, or to the
// stage's bottom when they are off-stage. Horizontally it moves inwards only at the stage's edges.
export const PORTRAIT_TOOLBAR_MARGIN = 16;
// The second row is the slider body's height (UILobby Btn_RotateSpine/ConditionRoot/Able_Count/Bg, 156 x 104 units);
// Reset beside it takes the same height (owner, 2026-11-04).
export const PORTRAIT_TOOLBAR_ROW = 104 * 1920 / LOBBY_UI_WIDTH;
export const PORTRAIT_TOOLBAR_HEIGHT = 74 + PORTRAIT_TOOLBAR_ROW;
export function portraitToolbarLayout(rect, count, obstacles = [], head = null) {
  const icon = 62, gap = 6, width = Math.max(420, count * (icon + gap) - gap), height = PORTRAIT_TOOLBAR_HEIGHT, edge = 20, m = PORTRAIT_TOOLBAR_MARGIN;
  const centre = head ? head.x : rect.x + rect.width / 2, top = head ? head.y : rect.y;
  const x = Math.max(edge, Math.min(1920 - edge - width, centre - width / 2));
  const covered = (y) => obstacles.filter((o) => x < o.x + o.width + m && x + width > o.x - m && y < o.y + o.height + m && y + height > o.y - m);
  let y = top - height;
  if (y < edge || covered(y).length) {
    y = Math.min(rect.y + rect.height, 1080 - edge - height);
    for (let pass = 0, over = covered(y); over.length && pass <= obstacles.length; pass++, over = covered(y))
      y = Math.min(...over.map((o) => o.y - m - height));
  }
  // A head below the stage still keeps the toolbar on it.
  y = Math.max(edge, Math.min(1080 - edge - height, y));
  const row = PORTRAIT_TOOLBAR_ROW;
  return { x, y, width, height, icon, gap, slider: [0, 74 + row / 2, width - 168], reset: [width - 156, 74, 156, row] };
}
