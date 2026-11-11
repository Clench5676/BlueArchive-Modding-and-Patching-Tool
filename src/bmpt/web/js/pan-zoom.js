// Pan and zoom of a picture inside a fixed frame, as plain numbers: {zoom, x, y}, where the picture's box is `zoom`
// times the frame and its top left corner sits at (x, y) in frame sizes (0 at the frame's own corner, negative once
// the picture is larger). The frame never shows anything outside the picture's box.
export const MAX_ZOOM = 8;
export const REST = Object.freeze({ zoom: 1, x: 0, y: 0 });

export function clamp(view) {
  const zoom = Math.min(MAX_ZOOM, Math.max(1, Number.isFinite(view.zoom) ? view.zoom : 1));
  const within = (value) => Math.min(0, Math.max(1 - zoom, Number.isFinite(value) ? value : 0));
  return { zoom, x: within(view.x), y: within(view.y) };
}

// Zoom by `factor` keeping the point (px, py) of the frame (0..1 each way) on the same spot of the picture.
export function zoomAt(view, factor, px = 0.5, py = 0.5) {
  const current = clamp(view), zoom = Math.min(MAX_ZOOM, Math.max(1, current.zoom * factor)), k = zoom / current.zoom;
  return clamp({ zoom, x: px - (px - current.x) * k, y: py - (py - current.y) * k });
}

// Move the picture by (dx, dy) frame sizes.
export function panBy(view, dx, dy) {
  const current = clamp(view);
  return clamp({ zoom: current.zoom, x: current.x + dx, y: current.y + dy });
}

// The centred view at which a picture drawn whole inside the frame (contained) fills it instead: `frame` and
// `picture` are width / height.
export function cover(frame, picture) {
  if (!(frame > 0) || !(picture > 0)) return { ...REST };
  const zoom = Math.min(MAX_ZOOM, Math.max(frame / picture, picture / frame));
  return clamp({ zoom, x: (1 - zoom) / 2, y: (1 - zoom) / 2 });
}

// The ring drawn around a part of the picture: `box` is [left, top, right, bottom] in frame sizes of the picture's
// own box (0..1); the ring is its centre and half sizes, a little wider than the part and never a dot.
export function ring([left, top, right, bottom], { margin = 1.2, least = 0.03 } = {}) {
  const rx = Math.max(least, (right - left) / 2 * margin), ry = Math.max(least, (bottom - top) / 2 * margin);
  return { cx: (left + right) / 2, cy: (top + bottom) / 2, rx, ry };
}
export const ringStyle = ({ cx, cy, rx, ry }) => ({ left: `${(cx - rx) * 100}%`, top: `${(cy - ry) * 100}%`, width: `${rx * 200}%`, height: `${ry * 200}%` });

export const style = (view) => ({ width: `${view.zoom * 100}%`, height: `${view.zoom * 100}%`, left: `${view.x * 100}%`, top: `${view.y * 100}%` });
