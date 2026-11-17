// Reuse a rendered prefab sprite's alpha in another flattened widget's local
// coordinates. The sprite supplies every corner; no replacement radius is used.
import { canvasFailed, context2d, encodedPicture, hasAlpha } from "./canvas-failure.js";

const masks = new Map();
const LIMIT = 96;
const rect = (style) => ["left", "top", "width", "height"].map((key) => parseFloat(style[key]));
const multiply = (a, b) => [
  a[0] * b[0] + a[2] * b[1],
  a[1] * b[0] + a[3] * b[1],
  a[0] * b[2] + a[2] * b[3],
  a[1] * b[2] + a[3] * b[3],
  a[0] * b[4] + a[2] * b[5] + a[4],
  a[1] * b[4] + a[3] * b[5] + a[5],
];
const translate = (x, y) => [1, 0, 0, 1, x, y];

// NGUI emits skew followed by flip, both around the widget's centre. Read the
// rendered dimensions, including rounding, rather than recalculating a prefab.
function localToStage(style) {
  const [x, y, w, h] = rect(style);
  let transform = [1, 0, 0, 1, 0, 0];
  for (const part of (style.transform || "").matchAll(/(skewX|scale)\(([^)]+)\)/g)) {
    const values = part[2].split(",").map(parseFloat);
    const next =
      part[1] === "skewX"
        ? [1, 0, Math.tan((values[0] * Math.PI) / 180), 1, 0, 0]
        : [values[0], 0, 0, values[1] ?? values[0], 0, 0];
    transform = multiply(transform, next);
  }
  return multiply(multiply(translate(x + w / 2, y + h / 2), transform), translate(-w / 2, -h / 2));
}

/**
 * The transform that maps the target's local space onto the source's, both read from their inline styles, for
 * placing a mask.
 */
export function spriteMaskTransform(source, target) {
  const a = localToStage(target),
    det = a[0] * a[3] - a[1] * a[2];
  const inverse = [
    a[3] / det,
    -a[1] / det,
    -a[2] / det,
    a[0] / det,
    (a[2] * a[5] - a[3] * a[4]) / det,
    (a[1] * a[4] - a[0] * a[5]) / det,
  ];
  return multiply(inverse, localToStage(source));
}

/**
 * The near-opaque painted surface of a body sprite: the sprites include their own bevel and drop shadow, but
 * decoration belongs to the surface, not those outer pixels. The result is normalised by the sprite's tint so
 * the same extraction works for blue, yellow and grey buttons.
 * @param {{data: Uint8ClampedArray, width: number, height: number}} image The sprite's RGBA pixels.
 */
export function spriteSurface({ data, width, height }) {
  const peak = [0, 0, 0, 0];
  for (let i = 0; i < data.length; i += 4) {
    if (data[i + 3] < 240) continue;
    for (let c = 0; c < 4; c++) peak[c] = Math.max(peak[c], data[i + c]);
  }
  if (!peak[3]) return { data, width, height };
  const result = new Uint8ClampedArray(data.length);
  for (let i = 0; i < data.length; i += 4) {
    let paint = data[i + 3] / peak[3];
    for (let c = 0; c < 3; c++) if (peak[c]) paint = Math.min(paint, data[i + c] / peak[c]);
    result[i] = result[i + 1] = result[i + 2] = 255;
    result[i + 3] = Math.round(Math.max(0, Math.min(1, (paint - 14 / 16) * 16)) * 255);
  }
  return { data: result, width, height };
}

/**
 * Mask a target element with a sprite's shape. A mask drawn on a canvas with no backing store comes out empty;
 * kept, it would hide every widget sharing it for the rest of the session, so it is never kept: the widget
 * shows unmasked and the next render draws the mask again.
 * @param {HTMLElement} source The element whose sprite gives the shape.
 * @param {HTMLElement} target The element to mask.
 * @param {(url: string) => Promise<HTMLImageElement>} load Loads the sprite's image.
 * @param {object} [options] Options.
 * @param {boolean} [options.surface] Mask by the painted surface (spriteSurface), without the bevel and shadow.
 * @param {Function} [options.report] Reports a canvas failure, for the tests.
 * @returns {Promise<boolean>} Whether the mask was applied.
 */
export async function maskWithSprite(source, target, load, { surface = false, report = canvasFailed } = {}) {
  const match = /^url\(["']?(.*?)["']?\)$/.exec(source.style.backgroundImage || "");
  if (!match || !load) return false;
  const [, , sw, sh] = rect(source.style),
    [, , tw, th] = rect(target.style);
  if (![sw, sh, tw, th].every((v) => Number.isFinite(v) && v > 0)) return false;
  const matrix = spriteMaskTransform(source.style, target.style);
  if (!matrix.every(Number.isFinite)) return false;
  // Match NGUI's 2x baked sprites; higher display densities retain their edge
  // coverage as well. This resamples only the mask, never the displayed image.
  const density = Math.max(2, globalThis.devicePixelRatio || 1);
  const width = Math.ceil(tw * density),
    height = Math.ceil(th * density);
  const key = JSON.stringify([match[1], surface, sw, sh, tw, th, width, height, matrix.map((v) => +v.toFixed(5))]);
  if (!masks.has(key)) {
    const pending = load(match[1])
      .then((image) => {
        if (surface) {
          const body = document.createElement("canvas");
          body.width = image.naturalWidth;
          body.height = image.naturalHeight;
          const pixels = context2d(body);
          pixels.drawImage(image, 0, 0);
          const original = pixels.getImageData(0, 0, body.width, body.height);
          if (!hasAlpha(original.data)) throw new Error("Mask sprite drew no pixels");
          original.data.set(spriteSurface(original).data);
          pixels.putImageData(original, 0, 0);
          image = body;
        }
        const canvas = document.createElement("canvas");
        canvas.width = width;
        canvas.height = height;
        const context = context2d(canvas);
        context.imageSmoothingQuality = "high";
        context.scale(width / tw, height / th);
        context.transform(...matrix);
        context.drawImage(image, 0, 0, sw, sh);
        if (!hasAlpha(context.getImageData(0, 0, width, height).data)) throw new Error("Mask drew no pixels");
        const data = canvas.toDataURL("image/png");
        if (!encodedPicture(data)) throw new Error(`Mask encoding failed (${String(data).slice(0, 16)})`);
        return data;
      })
      .catch((err) => {
        if (masks.get(key) === pending) masks.delete(key);
        throw err;
      });
    masks.set(key, pending);
    if (masks.size > LIMIT) masks.delete(masks.keys().next().value);
  }
  let data;
  try {
    data = await masks.get(key);
  } catch (error) {
    report(error, match[1], "ui-mask");
    return false;
  }
  const url = `url("${data}")`;
  Object.assign(target.style, {
    webkitMaskImage: url,
    maskImage: url,
    webkitMaskSize: "100% 100%",
    maskSize: "100% 100%",
    webkitMaskPosition: "0 0",
    maskPosition: "0 0",
    webkitMaskRepeat: "no-repeat",
    maskRepeat: "no-repeat",
  });
  return true;
}
