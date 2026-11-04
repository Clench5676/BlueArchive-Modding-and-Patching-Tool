// The fixed-size popup frames (popupFrame callers) and where each one's content ends, in one place so the room under the
// content can be proved by tests: the lowest content edge stays POPUP.clearance above the buttons' tops (popup.js
// footerTop), and the frame stays inside the stage. y is popup-local (the frame's centre is POPUP.frameY); a frame with no
// buttons has no footer to clear.
// The footer sits FOOTER.centre above the frame's bottom edge, 68.5 (FOOTER.rise) higher than the app's frames kept it, so a
// frame that held content down to its buttons grows by that, and its content, anchored to the frame's top, moves up by half.
import { FOOTER, POPUP } from "./ui-tokens.js";

const Y = POPUP.frameY, RISE = FOOTER.rise, LIFT = RISE / 2;

// the text viewer (the log): the pre's box in the frame
export const TEXT_VIEWER = { w: 2458, h: 1400, box: { top: Y + 530, height: 900, width: 2200 } };
TEXT_VIEWER.bottom = TEXT_VIEWER.box.top - TEXT_VIEWER.box.height;

// the alias editor (detail.js): one input row each, the frame grows with them. AL.bottom is the room under the last row's
// centre, which keeps the old 73.5 under its edge.
export const ALIAS = { w: 1500, rowW: 1180, rowH: 100, pitch: 124, top: 236, bottom: 334 + RISE, max: 8 };
// n rows: the frame's height, the rows' centres and the lowest content edge
export function aliasFrame(n) {
  const h = ALIAS.top + (n - 1) * ALIAS.pitch + ALIAS.bottom;
  const ys = Array.from({ length: n }, (_, i) => Y + h / 2 - ALIAS.top - i * ALIAS.pitch);
  return { w: ALIAS.w, h, ys, bottom: ys[n - 1] - ALIAS.rowH / 2 };
}

// the file details (detail.js): the scrolling table fills the frame between the title (140 under the top) and the buttons
export const DETAILS = { w: 2200, h: 1400 + RISE, l: -1040, r: 1040, title: 140, bottom: 228 + RISE, minH: 900 };
// the frame for a body bodyHeight tall: its height, the scrolling area's top and bottom (the lowest content edge)
export function detailsFrame(bodyHeight) {
  const h = Math.min(DETAILS.h, Math.max(DETAILS.minH, bodyHeight + DETAILS.title + DETAILS.bottom));
  return { w: DETAILS.w, h, top: Y + h / 2 - DETAILS.title, bottom: Y - h / 2 + DETAILS.bottom };
}

// Mod Alias: the input row and its hint under it
export const MOD_ALIAS = { w: 1500, h: 680 + RISE, input: { y: Y + 50 + LIFT, w: 1180, h: 100 }, hint: { dy: -110, h: 90 } };
MOD_ALIAS.bottom = MOD_ALIAS.input.y + MOD_ALIAS.hint.dy - MOD_ALIAS.hint.h / 2;

// Compare and the repair preview: the findings list without buttons, the repair review with Accept and Discard
export function compareFrame(review) {
  const lift = review ? LIFT : 0;
  const rows = { y: 400 + lift, pitch: 64, h: 50, max: 12 };
  const scroll = { top: 420 + lift, height: 760, width: 1700 };
  return { w: 1900, h: 1180 + (review ? RISE : 0), rows, scroll, lift,
    bottom: review ? scroll.top - scroll.height : rows.y - (rows.max - 1) * rows.pitch - rows.h / 2 };
}

// Export: the destination row and the two option rows (Settings' rows, 240 high in a line)
export const EXPORT = { w: 1800, h: 1400, rows: { Destination: { y: 385, h: 260 }, Server: { y: 85, h: 240 }, Platform: { y: -175, h: 240 } } };
EXPORT.bottom = Math.min(...Object.values(EXPORT.rows).map(({ y, h }) => y - h / 2));
