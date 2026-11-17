// A 2D canvas without a backing store (WebKit past its canvas memory limit) draws nothing, reads back as transparent
// and encodes to "data:," or to no blob at all. A picture made from it must never be cached: a mask or image cached
// once is reused for the rest of the session, so a passing shortage would hide that picture until the next launch.
import { previewError } from "../preview-error.js";

/** Whether data is a PNG or other image data URL with a body: "data:," (an empty canvas's encoding) is none. */
export const encodedPicture = (data) => typeof data === "string" && /^data:image\/[\w.+-]+(;[^,]*)?,./.test(data);

/** Whether any pixel of RGBA data is not fully transparent. */
export function hasAlpha(data) {
  for (let i = 3; i < data.length; i += 4) if (data[i]) return true;
  return false;
}

/**
 * The 2D context of a canvas, checked: a canvas without a backing store fails here.
 * @returns {CanvasRenderingContext2D} The context. Throws when the canvas is unusable.
 */
export function context2d(canvas) {
  const context = canvas.getContext("2d");
  if (!context) throw new Error(`2D canvas unavailable (${canvas.width} x ${canvas.height})`);
  return context;
}

// The session log hears of a failing step at most once every REPORT_EVERY ms: a shortage fails every picture asked
// for meanwhile.
export const REPORT_EVERY = 30000;
const reported = new Map();
/**
 * Record that a canvas step failed (a shortage fails every picture asked for meanwhile, so the session log
 * hears of a failing step at most once every REPORT_EVERY ms).
 * @param {string} url The picture's URL.
 * @param {string} step The step that failed.
 * @param {object} [options]
 * @param {number} [options.now] The time in milliseconds, for the tests.
 * @param {Function} [options.report] Reports it, for the tests.
 * @returns {boolean} Whether it was reported.
 */
export function canvasFailed(error, url, step, { now = Date.now(), report = previewError } = {}) {
  if (now - (reported.get(step) ?? -Infinity) < REPORT_EVERY) return false;
  reported.set(step, now);
  report(error, url, step);
  return true;
}
