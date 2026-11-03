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
export function pageAlpha(texture) {
  if (pages.has(texture)) return pages.get(texture);
  const image = texture.getImage(), canvas = document.createElement("canvas");
  canvas.width = image.width; canvas.height = image.height;
  const context = canvas.getContext("2d", { willReadFrequently: true });
  context.drawImage(image, 0, 0);
  const rgba = context.getImageData(0, 0, canvas.width, canvas.height).data;
  const alpha = new Uint8Array(canvas.width * canvas.height);
  for (let i = 0; i < alpha.length; i++) alpha[i] = rgba[i * 4 + 3];
  const page = { width: canvas.width, height: canvas.height, alpha }; pages.set(texture, page); return page;
}
export function preparePickPages(data) {
  for (const skin of data.skins || []) for (const { attachment } of skin.getAttachments()) {
    if (attachment.region?.texture) pageAlpha(attachment.region.texture);
  }
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
export function skeletonContains(skeleton, x, y, S, alphaOf = pageAlpha) {
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
export function visibleHead(skeleton, S, alphaOf = pageAlpha, step = 8) {
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
    if (opacity * sampleAlpha(page, aWeight * uv[p] + b * uv[q] + c * uv[r], aWeight * uv[p + 1] + b * uv[q + 1] + c * uv[r + 1]) >= VISIBLE_ALPHA) return true;
  }
  return false;
}
export function pickPortrait(actors, x, stageY, S, alphaOf = pageAlpha) {
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
// from the portrait into the toolbar; without a head, above the portrait's box. Moved inwards only where it would not
// fit on the stage, and down below any control it would cover (the top bar, the Hall's top-right buttons and slider).
export const PORTRAIT_TOOLBAR_MARGIN = 16;
export function portraitToolbarLayout(rect, count, obstacles = [], head = null) {
  const icon = 62, gap = 6, width = Math.max(420, count * (icon + gap) - gap), height = 142, edge = 20;
  const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
  const centre = head ? head.x : rect.x + rect.width / 2, top = head ? head.y : rect.y;
  const x = clamp(centre - width / 2, edge, 1920 - edge - width);
  let y = clamp(top - height, edge, 1080 - edge - height);
  const m = PORTRAIT_TOOLBAR_MARGIN;
  for (let moved = true, pass = 0; moved && pass < obstacles.length + 1; pass++) {
    moved = false;
    for (const o of obstacles) {
      if (x < o.x + o.width + m && x + width > o.x - m && y < o.y + o.height + m && y + height > o.y - m) { y = o.y + o.height + m; moved = true; }
    }
  }
  return { x, y: Math.min(y, 1080 - edge - height), width, height, icon, gap,
    slider: [0, 108, width - 168], reset: [width - 156, 74, 156, 68] };
}
