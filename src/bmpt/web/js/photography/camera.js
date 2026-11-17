// Camera limits constrain the capture square, rather than the entire display viewport.
// The framing guide matches UICollectCard/Char_Portrait, so application never stretches a square into the roster.
const HEIGHT = 360,
  WIDTH = (HEIGHT * 404) / 456;
export const FRAME = Object.freeze({ x: (1920 - WIDTH) / 2, y: 360, width: WIDTH, height: HEIGHT, size: HEIGHT });
const bounded = (value, low, high) => (low <= high ? Math.max(low, Math.min(high, value)) : (low + high) / 2);
/**
 * The capture square on the stage: its left and top edge and its size, in stage pixels. `size` is the width
 * and the height of a square frame; `width` and `height` are set for the portrait-shaped one.
 * @typedef {{x: number, y: number, width?: number, height?: number, size: number}} CaptureFrame
 */

/**
 * The smallest zoom of a portrait: the one at which the portrait's box (left, top, right, bottom in its own
 * units) still fills the frame's height, but never above the home scale.
 * @param {number[]} box The portrait's box [left, top, right, bottom].
 * @param {number} homeScale The zoom the portrait starts at.
 * @param {CaptureFrame} [frame] The capture frame.
 */
export const portraitMinimum = (box, homeScale, frame = FRAME) =>
  Math.min(homeScale, (frame.height || frame.size) / Math.max(1, box[3] - box[1]));
/**
 * The widest view of a lobby the camera allows: wide enough that the art's bounds fill the frame's height,
 * never narrower than the normal view.
 * @param {{y0: number, y1: number}} bounds The art's vertical bounds in lobby units.
 * @param {number} normalWidth The width of the lobby's normal view.
 * @param {CaptureFrame} [frame] The capture frame.
 */
export const lobbyMaximumWidth = (bounds, normalWidth, frame = FRAME) =>
  Math.max(normalWidth, ((bounds.y1 - bounds.y0) * 1920) / (frame.height || frame.size));
/**
 * The box of the choice list at the bottom right (portrait or lobby choices) with its grid: columns, the
 * visible rows (at most three) and the height of all rows for scrolling.
 * @param {"portrait"|"lobby"} kind Which choices are listed; portraits are drawn in up to four columns, lobbies
 *     in up to three.
 * @param {number} count The number of choices.
 */
export function choiceLayout(kind, count) {
  const columns = kind === "portrait" ? Math.min(4, Math.max(1, count)) : Math.min(3, Math.max(1, count));
  const rowHeight = kind === "portrait" ? 128 : 61,
    totalRows = Math.max(1, Math.ceil(count / columns)),
    rows = Math.min(3, totalRows);
  const width = kind === "portrait" ? columns * 146 + 24 : columns * 196 + 24;
  const height = rows * rowHeight + 24;
  return {
    x: 1890 - width,
    y: 920 - height,
    width,
    height,
    columns,
    rowHeight,
    rows,
    contentHeight: totalRows * rowHeight,
  };
}
/**
 * Keep a portrait's view inside what the frame may show of its bounds.
 * @param {{cx: number, cy: number, k: number}} view The view: the centre and the zoom.
 * @param {number[]} bounds The portrait's bounds [x0, y0, x1, y1].
 * @param {CaptureFrame} [frame] The capture frame.
 */
export function portraitCaptureView(view, bounds, frame = FRAME) {
  const [x0, y0, x1, y1] = bounds,
    k = view.k;
  return {
    ...view,
    cx: bounded(view.cx, x0 - (frame.x - 960) / k, x1 - (frame.x + (frame.width || frame.size) - 960) / k),
    cy: bounded(view.cy, y0 - (540 - frame.y - (frame.height || frame.size)) / k, y1 - (540 - frame.y) / k),
  };
}
/**
 * Keep a lobby's view inside what the frame may show of the art's bounds.
 * @param {{x: number, y: number, w: number, h: number}} view The view in lobby units.
 * @param {{x0: number, y0: number, x1: number, y1: number}} bounds The art's bounds in lobby units.
 * @param {CaptureFrame} [frame] The capture frame.
 */
export function lobbyCaptureView(view, bounds, frame = FRAME) {
  const left = frame.x / 1920,
    right = (frame.x + (frame.width || frame.size)) / 1920;
  const bottom = 1 - (frame.y + (frame.height || frame.size)) / 1080,
    top = 1 - frame.y / 1080;
  return {
    ...view,
    x: bounded(view.x, bounds.x0 - left * view.w, bounds.x1 - right * view.w),
    y: bounded(view.y, bounds.y0 - bottom * view.h, bounds.y1 - top * view.h),
  };
}

/**
 * The mode the mode button switches to: the one that is not shown, when the student has it.
 * @param {"portrait"|"lobby"} mode The shown mode.
 * @param {{hasPortrait: boolean, hasLobby: boolean}} info What the student has (bridge.info()).
 * @returns {"portrait"|"lobby"|null} The other mode; null when the student has no other, so no button shows.
 */
export const otherMode = (mode, info) => {
  const next = mode === "portrait" ? "lobby" : "portrait";
  return (next === "lobby" ? info?.hasLobby : info?.hasPortrait) ? next : null;
};
/**
 * The caption of an entry in the Dialogue and Actions lists: a dialogue (it carries its number n) is named as
 * the detail screen names it; any other clip (an idle pose) keeps its clip name until it has a label of its
 * own.
 * @param {{anim: string, n?: number}} entry The list entry.
 * @param {(key: string, params?: object) => string} T The translate function.
 */
export const actionCaption = (entry, T) =>
  Number.isFinite(entry?.n) ? T("lobby.dialogue", { n: entry.n }) : entry?.anim;
// The right edge and the neighbouring grid and mode controls stay where they are.
export const OCCLUSION_BUTTON = Object.freeze([1352, 25, 196, 76]);
// The toolbar's text buttons (Occlusion Guide, Grid) share one caption size: the largest up to 38 units at which every caption
// fits its 196 x 76 body (18 clear of each side, 8 above and below) on up to two lines. Stage px.
export const TOOLBAR_CAPTION = Object.freeze({ size: 38, min: 26, width: 196 - 36, height: 76 - 16 });
