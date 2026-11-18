// NGUI prefab renderer: builds absolutely positioned DOM from a layout tree read from the game's UI prefabs
// (/asset/ui/layout/<Root>.json: nodes with local positions in NGUI units and UISprite / UITexture / UILabel
// widgets). Sprites are simple, sliced (9-slice, borders from the atlas) or tiled, tinted like NGUI (mColor
// multiplies the mostly white sprite), flipped and sheared by inclineAngle; textures are the game's UITexture
// images or per-node overrides; labels are plain text in the page font. Depth becomes z-index; NGUI's y points
// up, the stage's y down.
import { hasAffirmative, skinAffirmatives, goldTemplate } from "./affirmative-buttons.js";
import { labelEffect, labelClipPadding } from "./ngui-label.js";
import { clipButtonPatterns, tagOf, tagBox, overlaps, inkBox } from "./button-layout.js";
import { spriteUrl, uiExportUrl, setAssetContext } from "../core/api.js";
import { decodeAll, imageUrls, imageDecoder } from "../core/image-decode.js";
import { withTimeout } from "../core/async.js";
import { readSerialized } from "../core/serialized-fields.js";
import { canvasFailed, encodedPicture } from "../core/canvas-failure.js";
import { spriteBorders, sliceAxis } from "./sprite-slices.js";
import { renderFrameEffect } from "./ngui-frame-fx.js";
import {
  GLYPH_LIFT,
  LATIN_LIFT,
  isLatin,
  fontSizeFor,
  measureCtx,
  fontStack,
  glyphWidth,
  fitText,
  LINE_PITCH,
} from "./ngui-text.js";
// The text functions stay part of the renderer's API (NGUI.fitGroup and the like).
export {
  fontSizeFor,
  textWidth,
  glyphWidth,
  fitText,
  LINE_PITCH,
  wrapText,
  fitGroup,
  fitRow,
  inkReference,
  inkShift,
  inkBand,
  fitLabels,
  fitLabel,
} from "./ngui-text.js";

const PIVX = [0, 0.5, 1, 0, 0.5, 1, 0, 0.5, 1];
const PIVY = [0, 0, 0, 0.5, 0.5, 0.5, 1, 1, 1]; // fraction of the height above the pivot

let index = null,
  textures = null,
  initDone = null;
/** The atlas index: atlas name to sprite name to its metadata. Empty until init() has loaded it. */
export const atlasIndex = () => index || {};
/** The UI texture index: texture name to the URL of its image. Empty until init() has loaded it. */
export const textureIndex = () => textures || {};
let atlasDone = null,
  texturesDone = null;
const images = new Map(),
  tints = new Map();
let resourceContext = null,
  resourceRevision = 0;
const resourceListeners = new Set();
// Developer previews (pre-release builds and development runs) tag every button body with its size preset, XS,
// S, M or L; packaged release candidates and stable builds do not (settings.get `buttonSizeTags`). A body's size
// in the app's own units is what it measures on the stage: its prefab size, the nodes' scales and the render's
// scale against the stage's (a button drawn at a smaller render scale is a smaller button).
const STAGE_SCALE = 1920 / 2960;
let tierTags = false;
/**
 * Switch the size tags on button bodies on or off.
 * @param {boolean} on Whether render() adds the tags.
 */
export const setTierTags = (on) => {
  tierTags = !!on;
};
/**
 * Subscribe to a change of the resource context.
 * @param {() => void} fn Called after the caches are cleared.
 * @returns {() => boolean} Unsubscribes the listener.
 */
export function onResourceChange(fn) {
  resourceListeners.add(fn);
  return () => resourceListeners.delete(fn);
}
/**
 * Switch the game resources the UI is drawn from. A change clears every cache and informs the listeners.
 * @param {*} value The new context; compared with the current one by identity.
 * @returns {boolean} False when the context was already current.
 */
export function setResourceContext(value) {
  if (value === resourceContext) return false;
  resourceContext = value;
  resourceRevision++;
  setAssetContext(value);
  index = textures = initDone = atlasDone = texturesDone = null;
  for (const cache of [images, tints, gradients, baked, scaled, layouts]) cache.clear();
  for (const fn of resourceListeners) fn();
  return true;
}

/**
 * Fetch the atlas and texture indexes once, however many callers wait for them (the loading screen asks first).
 * @returns {Promise<void[]>} Settles when both indexes are in; a failed fetch is tried again on the next call.
 */
export function init() {
  const revision = resourceRevision;
  initDone ||= Promise.all([
    (atlasDone = assetIndex("/asset/ui/atlases.json").then((a) => {
      const object = (v) => v && typeof v === "object" && !Array.isArray(v);
      if (
        !object(a) ||
        !Object.keys(a).length ||
        !Object.values(a).every(
          (sprites) =>
            object(sprites) &&
            Object.values(sprites).every((s) => object(s) && Number.isFinite(s.width) && Number.isFinite(s.height)),
        ) ||
        !Object.values(a).some((sprites) => Object.keys(sprites).length)
      ) {
        throw new Error("/asset/ui/atlases.json: empty or invalid atlas index");
      }
      if (revision === resourceRevision) index = a;
    })),
    (texturesDone = assetIndex("/asset/ui/textures.json").then((t) => {
      if (revision === resourceRevision)
        textures = Object.fromEntries(Object.entries(t).map(([name, url]) => [name, uiExportUrl(url)]));
    })),
  ]).catch((err) => {
    if (revision === resourceRevision) initDone = null;
    throw err;
  });
  return initDone;
}

function assetIndex(url) {
  return withTimeout(assetJson(url, "no-store"), 15000, url);
}

async function assetJson(url, cache, label = url) {
  const endRequest = globalThis.BAMTRecovery?.request?.();
  try {
    const response = await fetch(uiExportUrl(url), { cache });
    if (!response.ok) throw new Error(`${label}: HTTP ${response.status}`);
    return readSerialized(await response.json());
  } finally {
    endRequest?.();
  }
}

/**
 * Load an image, shared by URL unless a signal is given.
 * @param {{signal?: AbortSignal}} [options] With a signal the load is private and is dropped when it aborts.
 * @returns {Promise<HTMLImageElement>} The decoded-on-demand image; rejects after 15 s.
 */
export function load(url, { signal } = {}) {
  url = uiExportUrl(url);
  if (signal) {
    const img = new Image();
    const pending = new Promise((resolve, reject) => {
      img.onload = () => resolve(img);
      img.onerror = () => reject(new Error(url));
      if (!signal.aborted) img.src = url;
    });
    return withTimeout(pending, 15000, url, { signal }).finally(() => {
      img.onload = img.onerror = null;
      if (signal.aborted) img.removeAttribute?.("src");
    });
  }
  if (!images.has(url)) {
    images.set(
      url,
      withTimeout(
        new Promise((resolve, reject) => {
          const img = new Image();
          img.onload = () => resolve(img);
          img.onerror = () => reject(new Error(url));
          img.src = url;
        }),
        15000,
        url,
      ).catch((err) => {
        images.delete(url);
        throw err;
      }),
    );
  }
  return images.get(url);
}

/**
 * Read the pixels of a sprite's atlas image.
 * @param {{atlas?: string, sprite: string}} widget The sprite widget; the atlas defaults to Common.
 * @returns {Promise<ImageData>} The whole atlas image's pixels.
 */
export async function spritePixels(widget) {
  const img = await load(spriteUrl(widget.atlas || "Common", widget.sprite));
  const canvas = document.createElement("canvas");
  canvas.width = img.naturalWidth;
  canvas.height = img.naturalHeight;
  const context = canvas.getContext("2d");
  context.drawImage(img, 0, 0);
  return context.getImageData(0, 0, canvas.width, canvas.height);
}

/**
 * Turn a canvas into an in-memory blob URL (never written anywhere).
 * A canvas with no backing store gives no blob and encodes to "data:,": that is a failure, so the caches below
 * drop it and the next render draws the picture again.
 * @returns {Promise<string>} The blob URL; rejects when the canvas cannot be encoded.
 */
export const canvasUrl = (c) =>
  withTimeout(
    new Promise((resolve, reject) =>
      c.toBlob((b) => {
        if (b) {
          resolve(URL.createObjectURL(b));
          return;
        }
        const data = c.toDataURL();
        if (encodedPicture(data)) {
          resolve(data);
          return;
        }
        const error = new Error(`UI image encoding failed (${c.width} x ${c.height})`);
        canvasFailed(error, "", "ui-image");
        reject(error);
      }),
    ),
    15000,
    "UI image encoding",
  );

/**
 * Multiply an image by a tint colour, keeping its alpha (the tint's alpha is applied as opacity).
 * @param {string} hex The tint as "#rrggbb"; white returns the URL unchanged.
 * @returns {Promise<string>} The URL of the tinted image.
 */
export async function tinted(url, hex) {
  if (hex === "#ffffff") return url;
  const key = `${url}|${hex}`;
  if (!tints.has(key)) {
    tints.set(
      key,
      load(url)
        .then((img) => {
          const c = document.createElement("canvas");
          c.width = img.naturalWidth;
          c.height = img.naturalHeight;
          const g = c.getContext("2d");
          g.drawImage(img, 0, 0);
          g.globalCompositeOperation = "multiply";
          g.fillStyle = hex;
          g.fillRect(0, 0, c.width, c.height);
          g.globalCompositeOperation = "destination-in";
          g.drawImage(img, 0, 0);
          return canvasUrl(c);
        })
        .catch((err) => {
          tints.delete(key);
          throw err;
        }),
    );
  }
  return tints.get(key);
}

const gradients = new Map();
async function gradientTint(url, colors) {
  const key = `${url}|${colors.join("|")}`;
  if (!gradients.has(key))
    gradients.set(
      key,
      load(url)
        .then(async (img) => {
          const c = document.createElement("canvas");
          c.width = img.naturalWidth;
          c.height = img.naturalHeight;
          const g = c.getContext("2d"),
            gradient = g.createLinearGradient(0, 0, 0, c.height);
          colors.forEach((value, i) => {
            const { hex, a } = colorOf(value);
            gradient.addColorStop(
              i,
              `${hex}${Math.round(a * 255)
                .toString(16)
                .padStart(2, "0")}`,
            );
          });
          g.drawImage(img, 0, 0);
          g.globalCompositeOperation = "multiply";
          g.fillStyle = gradient;
          g.fillRect(0, 0, c.width, c.height);
          g.globalCompositeOperation = "destination-in";
          g.drawImage(img, 0, 0);
          return canvasUrl(c);
        })
        .catch((err) => {
          gradients.delete(key);
          throw err;
        }),
    );
  return gradients.get(key);
}

const baked = new Map();
/**
 * Draw a sliced sprite into one image, at least 2x with a higher effective DPI when requested.
 * @param {number[]} b The borders [top, right, bottom, left] in source pixels.
 * @param {number} wpx The drawn width in stage pixels.
 * @param {number} hpx The drawn height in stage pixels.
 * @param {number|number[]} bs The border scale, or one per border (top, right, bottom, left).
 * @param {number} [density=2] Pixels per stage pixel.
 * @returns {Promise<string>} The URL of the drawn image.
 */
export function slicedImage(url, b, wpx, hpx, bs, density = 2) {
  const scales = Array.isArray(bs) ? bs : [bs, bs, bs, bs];
  const key = `${url}|${b.join(",")}|${wpx}x${hpx}|${scales.join(",")}|${density}`;
  if (!baked.has(key)) {
    baked.set(
      key,
      load(url)
        .then((img) => {
          const [t, r, bo, l] = b,
            sw = img.naturalWidth,
            sh = img.naturalHeight;
          const horizontal = sliceAxis(sw, l, r, wpx, [scales[3], scales[1]], density),
            vertical = sliceAxis(sh, t, bo, hpx, [scales[0], scales[2]], density);
          const W = horizontal.size,
            H = vertical.size;
          const c = document.createElement("canvas");
          c.width = W;
          c.height = H;
          const g = c.getContext("2d");
          g.imageSmoothingQuality = "high";
          const xs = horizontal.spans,
            ys = vertical.spans;
          for (const [sx0, sx1, dx0, dx1] of xs) {
            for (const [sy0, sy1, dy0, dy1] of ys) {
              if (sx1 <= sx0 || sy1 <= sy0 || dx1 <= dx0 || dy1 <= dy0) continue;
              // Clamp each source tile before interpolation: neighbouring atlas pixels otherwise bleed into seams.
              const tile = document.createElement("canvas");
              tile.width = sx1 - sx0;
              tile.height = sy1 - sy0;
              const source = tile.getContext("2d");
              source.imageSmoothingEnabled = false;
              source.drawImage(img, sx0, sy0, sx1 - sx0, sy1 - sy0, 0, 0, tile.width, tile.height);
              g.drawImage(tile, 0, 0, tile.width, tile.height, dx0, dy0, dx1 - dx0, dy1 - dy0);
            }
          }
          return canvasUrl(c);
        })
        .catch((err) => {
          baked.delete(key);
          throw err;
        }),
    );
  }
  return baked.get(key);
}

// halve repeatedly with smoothing until the image is at most twice its drawn size (x2 for HiDPI)
const scaled = new Map();
function downscaled(url, wpx, hpx) {
  const key = `${url}|${Math.round(wpx)}x${Math.round(hpx)}`;
  if (!scaled.has(key)) {
    scaled.set(
      key,
      load(url)
        .then((img) => {
          let w = img.naturalWidth,
            h = img.naturalHeight;
          // small icons get 3x so their edges stay smooth on HiDPI screens
          const k = Math.max(wpx, hpx) < 120 ? 3 : 2;
          const tw = Math.max(1, Math.round(wpx * k)),
            th = Math.max(1, Math.round(hpx * k));
          if (w <= tw * 1.5 && h <= th * 1.5) return url;
          let src = img;
          while (w / 2 >= tw && h / 2 >= th) {
            const c = document.createElement("canvas");
            c.width = Math.round(w / 2);
            c.height = Math.round(h / 2);
            const g = c.getContext("2d");
            g.imageSmoothingQuality = "high";
            g.drawImage(src, 0, 0, c.width, c.height);
            src = c;
            w = c.width;
            h = c.height;
          }
          const c = document.createElement("canvas");
          c.width = tw;
          c.height = th;
          const g = c.getContext("2d");
          g.imageSmoothingQuality = "high";
          g.drawImage(src, 0, 0, tw, th);
          return canvasUrl(c);
        })
        .catch((err) => {
          scaled.delete(key);
          throw err;
        }),
    );
  }
  return scaled.get(key);
}

const colorOf = (c) => {
  const [hex, a] = (c || "#ffffff/1.00").split("/");
  return { hex: hex.toLowerCase(), a: Number(a ?? 1) };
};

/**
 * Options of render().
 * @typedef {object} RenderOptions
 * @property {number} [scale] Stage pixels per NGUI unit.
 * @property {number[]} [origin] [x, y] in stage pixels of the root's origin.
 * @property {string[]} [on] Node names or paths to force on.
 * @property {string[]} [off] Node names or paths to force off.
 * @property {Object<string, number[]>} [shift] Node name or path to a [dx, dy] shift in units.
 * @property {Object<string, string>} [texts] Node name or path to the text it shows.
 * @property {Object<string, string>} [textures] Node name or path to a texture URL.
 * @property {Object<string, string>} [textureNames] Texture name to URL.
 * @property {number} [rasterDensity] Pixels per stage pixel of sliced sprites; at least 2.
 * @property {Object<string, string>} [styles] Node name or path to CSS applied after the images are in.
 * @property {string} [rootPath] Prefix of the paths.
 * @property {number} [z] Offset added to every z-index.
 * @property {boolean} [deferImages] Fill the pictures in later instead of waiting for them.
 * @property {AbortSignal} [signal] Drops the pictures still loading when it aborts.
 */

/**
 * What render() drew.
 * @typedef {object} RenderView
 * @property {Map<string, HTMLElement>} nodes The elements by node name and path.
 * @property {number} scale Stage pixels per NGUI unit.
 * @property {number[]} origin [x, y] in stage pixels of the root's origin.
 * @property {Promise<void>[]} pictures All the picture work, deferred work included.
 * @property {Promise<void>[]} images The pictures other than thumbnails.
 * @property {Promise<void>[]} thumbnails The optional thumbnails, so a screen can give them a shorter wait.
 */

/**
 * Draw a layout tree into a host element.
 * @param {RenderOptions} [opts]
 * @returns {Promise<RenderView>} The drawn elements and the picture work.
 */
export async function render(tree, host, opts = {}) {
  if (hasAffirmative(tree)) tree = skinAffirmatives(tree, goldTemplate);
  // Popup branches publish together, including decoded textures, even when the caller renders into a live layer.
  const target = host,
    popup = !!findNamed(tree, "Popup");
  if (popup) host = document.createElement("div");
  const ready = init();
  const spritesReady = atlasDone,
    textureReady = texturesDone;
  if (opts.deferImages) ready.catch((err) => console.error("NGUI index failed", err));
  else await ready;
  const needsPlaceholder = (n) => n.widgets?.some((w) => w.placeholder) || n.children?.some(needsPlaceholder);
  const placeholderLabel = needsPlaceholder(tree)
    ? find(await layout("UIPopup_Sort"), "Center/Popup/Btn/Btn_Cancel/Label_Locale").widgets.find((w) => "text" in w)
    : null;
  // A still being made holds its place with the game's loading ring (UIRatioNoticePopup's LoadingIcon and its
  // TweenRotation); without that prefab the placeholder text stands in. A widget may stand for work under way with
  // the same ring (loadingRing: a button whose action is still loading).
  const needsRing = (n) => n.widgets?.some((w) => w.loadingRing) || n.children?.some(needsRing);
  const loadingIcon =
    needsPlaceholder(tree) || needsRing(tree)
      ? await layout("UIRatioNoticePopup").then(
          (t) => find(t, "Center/Popup/Base/LoadingIcon") || null,
          () => null,
        )
      : null;
  const S = opts.scale ?? 1920 / 2960;
  const [ox, oy] = opts.origin ?? [960, 540];
  const on = new Set(opts.on || []),
    off = new Set(opts.off || []);
  const pick = (map, name, path) => (map ? (map[path] ?? map[name]) : undefined);
  const nodes = new Map();
  const jobs = [],
    post = [],
    pictures = [],
    thumbnails = [];
  const frameFailures = [];
  const bodies = [],
    inks = [];
  const failed = (p, err) => {
    console.error("NGUI image failed", p, err);
    if (popup && /(?:\/Popup\/Base\/BG|\/BlackBg\/BlackBgClickArea)$/.test(p)) frameFailures.push(err);
  };

  function walk(n, px, py, sx, sy, alpha, path) {
    const p = path ? `${path}/${n.name}` : n.name;
    const active = (n.active || on.has(n.name) || on.has(p)) && !off.has(n.name) && !off.has(p);
    if (!active) return;
    const shift = pick(opts.shift, n.name, p) || [0, 0];
    const x = px + (n.pos[0] + shift[0]) * sx,
      y = py + (n.pos[1] + shift[1]) * sy;
    const nsx = sx * n.scale[0],
      nsy = sy * n.scale[1];
    const entry = { node: n, x, y, els: [], stage: [ox + x * S, oy - y * S] };
    nodes.set(p, entry);
    if (n.frameEffect)
      jobs.push(
        renderFrameEffect(n.frameEffect, host, {
          x: ox + x * S,
          y: oy - y * S,
          sx: nsx * S,
          sy: nsy * S,
          z: opts.z || 0,
          load,
        }).catch((error) => failed(p, error)),
      );
    let a = alpha;
    for (const w of n.widgets) {
      if (!("w_" in w)) continue;
      const col = colorOf(w.color);
      if (w.loadingRing) {
        // the ring fitted into the widget's box, in its colour (the white sprite tinted, as NGUI tints); without the
        // prefab the widget's fallback (its caption) shows instead
        const el = loadingIcon
          ? spinner(n, loadingIcon, p, x, y, nsx, nsy, alpha, w.depth || 0, w)
          : w.fallback
            ? widget(n, { ...w.fallback, depth: w.depth }, p, x, y, nsx, nsy, colorOf(w.fallback.color), alpha)
            : null;
        if (el) {
          el.dataset.loading = "true";
          entry.els.push(el);
        }
      } else if ("sprite" in w || "texture" in w || "text" in w || "clipImage" in w) {
        const el = widget(n, w, p, x, y, nsx, nsy, col, alpha);
        if (el) entry.els.push(el);
        // a widget may say what it is (tier: "button" or "chip"); any other Common_Btn_BG body is judged by its shape
        if (el && tierTags && (w.tier || w.sprite === "Common_Btn_BG"))
          bodies.push({
            el,
            path: p,
            kind: w.tier || null,
            w: (w.w_ * Math.abs(nsx) * S) / STAGE_SCALE,
            h: (w.h_ * Math.abs(nsy) * S) / STAGE_SCALE,
          });
        else if (el && tierTags && "text" in w && el.textContent)
          inks.push({ el, w, sx: Math.abs(nsx), sy: Math.abs(nsy) });
      }
      a = alpha * col.a; // NGUI widgets pass their alpha on to child widgets
    }
    for (const c of n.children || []) walk(c, x, y, nsx, nsy, a, p);
  }

  // The game's loading ring at a placeholder's centre, turned as its TweenRotation turns it (from and to about z,
  // in degrees, over its duration; a loop or ping-pong). It shows after a moment, so a still that comes at once
  // never flashes. box: a loadingRing widget whose box the ring fits into, centred, and whose colour tints it.
  function spinner(n, icon, p, x, y, sx, sy, alpha, depth, box = null) {
    const sprite = icon.widgets?.find((w) => "sprite" in w);
    if (!sprite) return null;
    const k = box ? Math.min(box.w_ / sprite.w_, box.h_ / sprite.h_) : 1;
    const color = box?.color ?? sprite.color;
    const el = widget(
      n,
      { ...sprite, depth, ...(box ? { w_: sprite.w_ * k, h_: sprite.h_ * k, pivot: 4 } : {}), color },
      p,
      x,
      y,
      sx,
      sy,
      colorOf(color),
      alpha,
    );
    if (!el) return null;
    const tween = icon.widgets.find((w) => w.cls === "TweenRotation")?.tween;
    const from = tween?.from?.[2] ?? 180,
      to = tween?.to?.[2] ?? -180;
    el.classList.add("ngui-loading");
    // NGUI turns z anticlockwise, CSS clockwise
    const turn = {
      "--turn-from": `${-from}deg`,
      "--turn-to": `${-to}deg`,
      "--turn-time": `${tween?.duration || 3}s`,
      "--turn-loop": tween?.style === 2 ? "alternate" : "normal",
    };
    for (const [key, value] of Object.entries(turn)) {
      if (el.style.setProperty) el.style.setProperty(key, value);
      else el.style[key] = value;
    }
    return el;
  }
  function widget(n, w, p, x, y, sx, sy, col, alpha) {
    const ww = w.w_ * sx,
      hh = w.h_ * sy;
    const left = x - PIVX[w.pivot ?? 4] * ww,
      top = y + PIVY[w.pivot ?? 4] * hh;
    const el = document.createElement("div");
    el.className = `ngui ${n.name.replace(/\W+/g, "_")}`;
    el.dataset.path = p;
    Object.assign(el.style, {
      position: "absolute",
      left: `${(ox + left * S).toFixed(2)}px`,
      top: `${(oy - top * S).toFixed(2)}px`,
      width: `${(ww * S).toFixed(2)}px`,
      height: `${(hh * S).toFixed(2)}px`,
      zIndex: String((opts.z || 0) + (w.depth || 0)),
      opacity: String(alpha * col.a),
      pointerEvents: "none",
    });
    const transforms = [];
    if (w.incline && Math.abs(w.incline) > 0.01) transforms.push(`skewX(${-w.incline}deg)`);
    if (w.flip === 1 || w.flip === 3 || w.flip === 2)
      transforms.push(`scale(${w.flip === 1 || w.flip === 3 ? -1 : 1}, ${w.flip >= 2 ? -1 : 1})`);
    if (transforms.length) el.style.transform = transforms.join(" ");

    if ("clipImage" in w) {
      // an upright picture in a sheared, rounded window (stands in for NGUI panel clipping)
      const r = (w.radius || 0) * S * Math.abs(sy);
      Object.assign(el.style, { overflow: "hidden", borderRadius: `${r.toFixed(1)}px` });
      if (w.clipPolygon)
        el.style.clipPath = `polygon(${w.clipPolygon.map(([x, y]) => `${x * 100}% ${y * 100}%`).join(", ")})`;
      const pic = document.createElement("div");
      const inc = w.incline || 0;
      Object.assign(pic.style, {
        position: "absolute",
        left: w.imgLeft || "-15%",
        top: w.imgTop || "0",
        width: w.imgWidth || "130%",
        height: w.imgHeight || "100%",
        backgroundSize: w.imgSize || "cover",
        backgroundPosition: w.imgPosition || "center 20%",
        backgroundRepeat: "no-repeat",
        transform: inc ? `skewX(${inc}deg)` : "",
        ...(w.imgMask ? { webkitMaskImage: w.imgMask, maskImage: w.imgMask } : {}),
      });
      // Reuse the popup's UILabel treatment (UIPopup_Sort/Btn/Btn_Cancel/Label_Locale).
      // Kept until a still decodes, including null, rejected and cancelled requests.
      const placeholderRect = w.placeholderRect || { x: 0, y: 0, w: w.w_, h: w.h_ };
      const fitted = w.placeholder && fitText(w.placeholder, 34, 24, placeholderRect.w - 32, 3);
      const note = fitted
        ? widget(
            n,
            {
              ...placeholderLabel,
              text: fitted.text,
              fontSize: fitted.fs,
              w_: placeholderRect.w - 32,
              h_: fitted.fs * fitted.lines * 1.3,
              pivot: 4,
              depth: (w.depth || 0) + 1,
            },
            `${p}/Placeholder`,
            x + placeholderRect.x * sx,
            y + placeholderRect.y * sy,
            sx,
            sy,
            colorOf(placeholderLabel.color),
            alpha,
          )
        : null;
      const src = w.clipImage;
      const spin =
        src && fitted && loadingIcon
          ? spinner(
              n,
              loadingIcon,
              `${p}/Loading`,
              x + placeholderRect.x * sx,
              y + placeholderRect.y * sy,
              sx,
              sy,
              alpha,
              (w.depth || 0) + 1,
            )
          : null;
      const failureNote = (visible) => {
        if (visible) spin?.remove();
        if (note && (w.placeholderOnFailure || spin)) {
          note.hidden = !visible;
          note.style.display = visible ? "flex" : "none";
        }
      };
      failureNote(!src);
      if (src) {
        if (src.thumbnail) pic.dataset.thumbnail = "true";
        // a URL, or a promise of one (a thumbnail being rendered): thumbnails fill in when ready, render() does not
        // wait. One that comes in after the first frames fades in instead of popping.
        const t0 = performance.now();
        Object.assign(pic.style, { opacity: "0", transition: "opacity .25s ease" });
        const done = withTimeout(
          Promise.resolve(src).then((u) => (u && w.tint ? tinted(u, w.tint) : u)),
          src.thumbnail ? Infinity : 15000,
          `UI picture ${p}`,
          { signal: src.signal || opts.signal },
        )
          .then(async (u) => {
            if (src.signal?.aborted || opts.signal?.aborted) return;
            if (!u) {
              failureNote(true);
              return;
            }
            if (note) {
              await load(u, { signal: src.signal || opts.signal });
              if (src.signal?.aborted || opts.signal?.aborted) return;
              note.remove();
              spin?.remove();
            }
            pic.style.backgroundImage = `url("${u}")`;
            if (performance.now() - t0 < 50) pic.style.transition = "none";
            pic.style.opacity = "1";
          })
          .catch((err) => {
            if (!src.signal?.aborted && !opts.signal?.aborted) {
              failureNote(true);
              failed(p, err);
            }
          });
        if (src.thumbnail) thumbnails.push(done);
        if (typeof src === "string") jobs.push(done);
        else pictures.push(done);
      }
      el.appendChild(pic);
    } else if ("text" in w) {
      const text = pick(opts.texts, n.name, p) ?? w.text;
      if (!text) return null;
      const align =
        // biome-ignore lint/suspicious/noSparseArray: NGUI's alignment enum starts at 1
        [, "left", "center", "right", "justify"][w.align] || ["left", "center", "right"][(w.pivot ?? 4) % 3];
      Object.assign(el.style, {
        display: "flex",
        alignItems: ["flex-start", "center", "flex-end"][Math.floor((w.pivot ?? 4) / 3)],
        justifyContent: { left: "flex-start", center: "center", right: "flex-end", justify: "center" }[align],
        color: col.hex,
        fontSize: `${(fontSizeFor(text, w.fontSize || 40) * sy * S).toFixed(2)}px`,
        lineHeight: "1.15",
        whiteSpace: "pre",
        textAlign: align,
        fontStyle: w.italic ? "italic" : "",
        fontWeight: w.bold ? "700" : "",
        textShadow: labelEffect(w, sy * S),
        paddingBottom: `${(fontSizeFor(text, w.fontSize || 40) * sy * S * (isLatin(text) ? LATIN_LIFT : GLYPH_LIFT) * 2).toFixed(2)}px`,
      });
      if (w.lang) el.lang = w.lang; // text in another language than the page's draws with its fonts (app.css)
      if (w.ariaLabel) el.setAttribute("aria-label", w.ariaLabel);
      if (w.ellipsis) {
        const span = document.createElement("span");
        const fs = fontSizeFor(text, w.fontSize || 40) * sy * S;
        measureCtx.font = `500 ${fs}px ${fontStack()}`;
        const distance = (w.effectDistance || [0, 0]).map((v) => v * sy * S);
        const pad = labelClipPadding({ ...w, effectDistance: distance }, fs, measureCtx.measureText(text));
        Object.assign(span.style, {
          overflow: "hidden",
          textOverflow: "ellipsis",
          whiteSpace: "nowrap",
          maxWidth: "100%",
          padding: `${pad.top}px ${pad.right}px ${pad.bottom}px ${pad.left}px`,
          marginTop: `${-pad.top}px`,
          marginBottom: `${-pad.bottom}px`,
          marginLeft: `${-pad.left}px`,
          marginRight: `${-pad.right}px`,
        });
        span.textContent = text;
        el.appendChild(span);
      } else {
        el.textContent = text;
      }
    } else if ("sprite" in w) {
      if (w.sprite === "Common_Empty") return null;
      const atlas = w.atlas || "Common";
      // Keep the geometry for hit targets even when the atlas is missing or a sprite cannot load.
      if (opts.deferImages) el.style.backgroundColor = col.hex;
      jobs.push(
        spritesReady
          .then(async () => {
            const meta = index?.[atlas]?.[w.sprite];
            if (!meta) {
              // An incomplete index must be fetched again on the next render, like a failed request.
              if (atlasDone === spritesReady) initDone = null;
              throw new Error(`Missing sprite ${atlas}/${w.sprite} in /asset/ui/atlases.json`);
            }
            const tintedUrl = await tinted(spriteUrl(atlas, w.sprite), col.hex);
            const url = w.gradient ? await gradientTint(tintedUrl, w.gradient) : tintedUrl;
            // Runtime cuts use the same single-image raster path as atlas-authored sliced sprites.
            const b = spriteBorders({ ...meta, ...w.sliceBorders });
            if (w.type === 1 && b.some(Boolean)) {
              const sliceX = w.sliceScale && w.sliceScale[0] * S * Math.abs(sx),
                sliceY = w.sliceScale && w.sliceScale[1] * S * Math.abs(sy);
              // slicedImage takes per-border scales: top, right, bottom, left.
              const borderScale = w.sliceScale
                ? [sliceY, sliceX, sliceY, sliceX]
                : S * Math.min(Math.abs(sx), Math.abs(sy));
              return slicedImage(
                url,
                b,
                ww * S,
                hh * S,
                borderScale,
                Math.max(2, Number(opts.rasterDensity) || 2),
              ).then((img) => {
                Object.assign(el.style, {
                  backgroundImage: `url("${img}")`,
                  backgroundSize: "100% 100%",
                  backgroundRepeat: "no-repeat",
                });
              });
            } else if (w.type === 2) {
              Object.assign(el.style, {
                backgroundImage: `url("${url}")`,
                backgroundRepeat: "repeat",
                backgroundSize: `${meta.width * S}px ${meta.height * S}px`,
              });
            } else {
              // simple sprites keep their own aspect unless told to stretch (NGUI stretches; game widgets are sized to
              // match)
              const fit = Math.min((ww * S) / meta.width, (hh * S) / meta.height);
              return downscaled(
                url,
                w.keepAspect ? meta.width * fit : ww * S,
                w.keepAspect ? meta.height * fit : hh * S,
              ).then((small) => {
                Object.assign(el.style, {
                  backgroundImage: `url("${small}")`,
                  backgroundSize: w.keepAspect ? "contain" : "100% 100%",
                  backgroundPosition: "center",
                  backgroundRepeat: "no-repeat",
                });
              });
            }
            return null;
          })
          .then(() => {
            el.style.backgroundColor = "";
          })
          .catch((err) => failed(p, err)),
      );
    } else {
      el.dataset.tex = w.texture;
      const override = pick(opts.textures, n.name, p) ?? opts.textureNames?.[w.texture];
      const src =
        override || (opts.deferImages ? textureReady.then(() => textures?.[w.texture]) : textures?.[w.texture]) || null;
      if (!src) return null;
      if (src.thumbnail) el.dataset.thumbnail = "true";
      const done = withTimeout(
        Promise.resolve(src)
          .then((u) => (!u || override ? u : tinted(u, col.hex)))
          .then((url) =>
            !url || src.thumbnail || url.startsWith("data:image/svg") ? url : downscaled(url, ww * S, hh * S),
          ),
        src.thumbnail ? Infinity : 15000,
        `UI picture ${p}`,
        { signal: src.signal || opts.signal },
      )
        .then((url) => {
          if (url && !src.signal?.aborted && !opts.signal?.aborted)
            Object.assign(el.style, {
              backgroundImage: `url("${url}")`,
              backgroundSize: "100% 100%",
              backgroundRepeat: "no-repeat",
            });
        })
        .catch((err) => {
          if (!src.signal?.aborted && !opts.signal?.aborted) failed(p, err);
        });
      if (src.thumbnail) thumbnails.push(done);
      if (typeof src === "string")
        jobs.push(done); // a pending thumbnail fills in later
      else pictures.push(done);
    }
    const css = pick(opts.styles, n.name, p);
    if (css) post.push([el, css]); // applied after the images are in, so it can override them
    host.appendChild(el);
    return el;
  }

  walk(tree, 0, 0, 1, 1, 1, opts.rootPath || "");
  if (opts.deferImages) pictures.push(...jobs);
  else await Promise.all(jobs);
  for (const [el, css] of post) el.style.cssText += css;
  for (const body of bodies) {
    const tag = tierTag(body, opts.z || 0, inks, S);
    if (tag) host.appendChild(tag);
  }
  const view = {
    nodes,
    scale: S,
    origin: [ox, oy],
    pictures,
    images: pictures.filter((p) => !thumbnails.includes(p)),
    thumbnails,
  };
  await clipButtonPatterns(view, { load });
  if (popup) await Promise.all(view.images);
  if (frameFailures.length) throw new AggregateError(frameFailures, "Popup frame unavailable");
  if (popup || !opts.deferImages) {
    await decoded(
      [...(host.children || [])].filter((el) => !el.dataset?.thumbnail),
      Infinity,
    );
  }
  if (popup) {
    for (const child of [...host.children]) target.appendChild(child);
  }
  return view;
}

// The size tag at a button body's top left corner: the preset's letters, or the height in units when the pill is
// none of them. Where it would cover the caption (the inks of the text widgets inside the body) it is a dot
// there, the same label on hover.
function tierTag({ el, path, w, h, kind }, z, inks, S) {
  const info = tagOf(w, h, kind);
  if (!info) return null;
  const px = (name) => parseFloat(el.style[name]),
    body = [px("left"), px("top"), px("width"), px("height")];
  const covered = inks.some(({ el: textEl, w: tw, sx, sy }) => {
    const box = ["left", "top", "width", "height"].map((name) => parseFloat(textEl.style[name]));
    if (
      box[0] + box[2] / 2 < body[0] ||
      box[0] + box[2] / 2 > body[0] + body[2] ||
      box[1] + box[3] / 2 < body[1] ||
      box[1] + box[3] / 2 > body[1] + body[3]
    )
      return false;
    const lines = textEl.textContent.split("\n"),
      fs = tw.fontSize || 40;
    const inkW = glyphWidth(textEl.textContent, fs) * sx * S,
      inkH = lines.length * fontSizeFor(textEl.textContent, fs) * LINE_PITCH * sy * S;
    const align =
      // biome-ignore lint/suspicious/noSparseArray: NGUI's alignment enum starts at 1
      [, "left", "center", "right", "justify"][tw.align] || ["left", "center", "right"][(tw.pivot ?? 4) % 3];
    const vertical = ["flex-start", "center", "flex-end"][Math.floor((tw.pivot ?? 4) / 3)];
    return overlaps(tagBox(info.text, body[0], body[1]), inkBox(box, inkW, inkH, align, vertical));
  });
  const tag = document.createElement("div");
  tag.className = `ngui-tier${info.tier ? "" : " off"}${covered ? " dot" : ""}${kind === "chip" ? " chip" : ""}`;
  Object.assign(tag.dataset, {
    tier: info.tier ?? "",
    w: w.toFixed(1),
    h: h.toFixed(1),
    path,
    size: `${info.tier ? `${info.tier} ` : ""}${Math.round(h)}`,
  });
  if (!covered) tag.textContent = info.text;
  Object.assign(tag.style, { left: el.style.left, top: el.style.top, zIndex: String(z + 1000) });
  return tag;
}

/**
 * Find a subtree by path.
 * @param {string} path Names joined by "/", for example "A/B/C".
 * @returns {NguiNode|null} The node, or null when a part is missing.
 */
export function find(tree, path) {
  let n = tree;
  for (const part of path.split("/").filter(Boolean)) {
    n = (n.children || []).find((c) => c.name === part);
    if (!n) return null;
  }
  return n;
}

/**
 * Find the first node with a name, depth first.
 * @param {NguiNode} tree The node to search from, itself included.
 * @returns {NguiNode|null} The node, or null when there is none.
 */
export function findNamed(tree, name) {
  if (tree.name === name) return tree;
  for (const c of tree.children || []) {
    const hit = findNamed(c, name);
    if (hit) return hit;
  }
  return null;
}

const layouts = new Map();
/**
 * Load a prefab layout, shared by name.
 * Cold exports scan prefab bundles and can wait for a lobby's bundle locks. The scene owns the slow-load
 * indicator and navigation cancellation; elapsed time alone does not make a valid server response fail.
 * @param {string} name "Root" for a prefab root, or "Root.Node" for the first node of that name inside it.
 * @returns {Promise<NguiNode>}
 */
export function layout(name) {
  if (!layouts.has(name)) {
    const revision = resourceRevision;
    const [root, sub] = name.split(".");
    // Cold exports scan prefab bundles and can wait for a lobby's bundle locks. The scene owns the slow-load
    // indicator and navigation cancellation; elapsed time alone does not make a valid server response fail.
    const request = sub
      ? layout(root).then((tree) => findNamed(tree, sub))
      : assetJson(`/asset/ui/layout/${encodeURIComponent(root)}.json`, "no-cache", `layout ${root}`);
    const pending = request
      .then((tree) => {
        if (revision !== resourceRevision) throw new DOMException("Resource context changed", "AbortError");
        return tree;
      })
      .catch((err) => {
        if (layouts.get(name) === pending) layouts.delete(name);
        throw err;
      });
    layouts.set(name, pending);
  }
  return layouts.get(name);
}

/**
 * The stage rectangle of an element made by render().
 * @returns {number[]} [left, top, width, height] in stage pixels.
 */
export const rect = (el) => ["left", "top", "width", "height"].map((k) => parseFloat(el.style[k]));

const decodeImage = imageDecoder(() => new Image());
/**
 * Wait until every picture drawn under an element is loaded and decoded, for a screen about to show (see
 * image-decode.js).
 * A picture still being made (a thumbnail) has no background yet and fills in later.
 * @param {HTMLElement|HTMLElement[]} root An element whose descendants are checked, or a list of elements.
 * @param {number} cap The longest wait in milliseconds.
 * @param {HTMLElement|null} [shown] An element whose pictures are on screen already, so decoded; they are left
 *     out (a Chromium decode() takes a frame or two even then).
 * @param {{signal?: AbortSignal}} [options]
 * @returns {Promise<void>} Settles when the pictures are decoded or the cap has passed.
 */
export function decoded(root, cap, shown = null, { signal } = {}) {
  const onScreen = shown?.isConnected && shown.getClientRects().length ? imageUrls(shown.querySelectorAll("*")) : [];
  const skip = new Set(onScreen);
  const elements = Array.isArray(root) ? root : [root, ...root.querySelectorAll("*")];
  const urls = imageUrls(elements).filter((url) => !skip.has(url));
  return decodeAll(urls, decodeImage, cap, { signal });
}
/**
 * A node of an NGUI layout tree, as read from a prefab or made by node().
 * @typedef {object} NguiNode
 * @property {string} name The node's name; paths join names with "/".
 * @property {boolean} active Whether the node is drawn.
 * @property {number[]} pos Local position [x, y] in NGUI units, y up.
 * @property {number[]} scale Local scale [x, y].
 * @property {number} rotZ Rotation about z in degrees.
 * @property {object[]} widgets The UISprite, UITexture and UILabel widgets on the node.
 * @property {NguiNode[]} children The child nodes.
 */

/**
 * Build a plain node by hand.
 * @param {number[]} pos Local position [x, y] in NGUI units.
 * @param {NguiNode[]} [children]
 * @param {number[]} [scale] Local scale [x, y].
 */
export const node = (name, pos, widgets = [], children = [], scale = [1, 1]) => ({
  name,
  active: true,
  pos,
  scale,
  rotZ: 0,
  widgets,
  children,
});
/**
 * Build a sprite widget.
 * @param {string} name The sprite's name in its atlas.
 * @param {number} w The widget width in NGUI units.
 * @param {number} h The widget height in NGUI units.
 * @param {object} [opts] Fields that replace the defaults (atlas Common, type 1, pivot 4, depth 5, white).
 */
export const sprite = (name, w, h, opts = {}) => ({
  sprite: name,
  atlas: "Common",
  type: 1,
  w_: w,
  h_: h,
  pivot: 4,
  depth: 5,
  color: "#ffffff/1.00",
  incline: 0,
  flip: 0,
  ...opts,
});
/**
 * Build a label widget.
 * @param {string} color The colour as "#rrggbb/alpha".
 * @param {number} w The widget width in NGUI units.
 * @param {number} h The widget height in NGUI units.
 * @param {object} [opts] Fields that replace the defaults (pivot 4, depth 9, align 0).
 */
export const label = (text, fontSize, color, w, h, opts = {}) => ({
  cls: "UILabel",
  text,
  fontSize,
  color,
  w_: w,
  h_: h,
  pivot: 4,
  depth: 9,
  align: 0,
  ...opts,
});
