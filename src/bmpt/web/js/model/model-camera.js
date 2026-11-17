// The 3D chibi's camera: the formation screen's view and the frames it is placed in, the distances of the fit and
// the bust, the zoom and pan the wheel, pinches and swipes drive (attachInput), and the square still's framing.
import * as THREE from "three";
import { wheelKind, wheelStep } from "../lobby/lobby-touch.js";

export const clamp = THREE.MathUtils.clamp;

export const ZOOM_TAU = 0.08; // s: a zoom or pan eases to its goal, as in the lobby
const NOTCH = 100; // px: a wheel notch zooms at least as far as one of Chromium's (a slow notch on macOS is 4 px)
const NOTCHES = 5; // notches from the front view to the bust
const PINCH = 1.5; // a pinch zooms half as far again as the fingers spread: one pinch from the front view to the bust
const CTRL_PINCH = 0.01; // Chromium's pinch as ctrl + wheel: the fingers' scale is exp(-deltaY / 100)
const SCROLL_MS = 120; // wheel events this close together belong to one scroll
const PINCH_HOLD = 300; // ms: WebKit's ctrl + wheel events beside its gesture events are left to the gesture

// The formation screen's view of one character (UIFormation under GameMain's camera): the camera's vertical
// field of view is 10 and it stands 14.6 character units away and 0.44 above the character's feet, looking
// level, so a character takes the same share of the screen's height whatever its size.
export const FORMATION_VIEW = (() => {
  const s = 2 / 1665,
    k = 630 * s;
  return { fov: 10, distance: (11.43 - 316.4 * s) / k, eye: ((222.6 + 56.25) * s) / k };
})();

// where the formation view's centre (the point 0.44 above the feet) lands on the host, in stage pixels, for a host of
// w x h: beside the panel the feet stay above the nameplate (floor); fullscreen it is the formation screen's centre.
// Pixels, not fractions, so the chibi does not jump when the host widens for fullscreen.
export const FRAMES = {
  left: { x: 490, y: 500, w: 1000, h: 1080, floor: 700 },
  full: { x: 960, y: 540, w: 1920, h: 1080, floor: 1056 },
  still: { x: 320, y: 320, w: 640, h: 640, floor: 616 },
};
export const TWEEN = 450;

/**
 * The camera distance (character units) for a frame: the formation screen's, or further back where the chibi's
 * box (halo and props included; relative to the orbit target, x across and y up) would leave the host around
 * the frame point (stage px, a margin kept) or reach below its floor.
 * @param {THREE.Box3} box The chibi's box around the orbit target.
 * @param {{x: number, y: number, w: number, h: number, floor: number}} frame The host's size and the frame
 *     point and floor in stage px.
 * @param {number} [fov] The vertical field of view in degrees.
 * @param {number} [base] The formation screen's distance.
 * @param {number} [margin] The margin kept in stage px.
 */
export function frameDistance(box, frame, fov = FORMATION_VIEW.fov, base = FORMATION_VIEW.distance, margin = 24) {
  const t = 2 * Math.tan(THREE.MathUtils.degToRad(fov) / 2); // the visible height per unit of distance
  const at = (units, px) => (units > 0 ? (units * frame.h) / (t * Math.max(px, 1)) : 0); // `units` over `px`
  const side = Math.min(frame.x, frame.w - frame.x) - margin;
  return Math.max(
    base,
    at(box.max.y, frame.y - margin),
    at(-box.min.y, Math.min(frame.floor, frame.h - margin) - frame.y),
    at(Math.max(box.max.x, -box.min.x), side),
  );
}

/**
 * The closest camera distance for a frame: the bust. Closing in lifts the orbit target to the face (`face`: its
 * height above the target), which then lies on the frame point; the bust is where the box's top (the halo)
 * reaches the host's top edge, a margin kept. Between a tenth and 0.45 of the frame's distance (`fit`), so a
 * box whose top lies at or below the face still closes in.
 * @param {THREE.Box3} box The chibi's box around the orbit target.
 * @param {number} face The face's height above the target.
 * @param {{y: number, h: number}} frame The frame point and the host's height in stage px.
 * @param {number} [fov] The vertical field of view in degrees.
 * @param {number} [margin] The margin kept in stage px.
 */
export function bustDistance(box, face, frame, fit, fov = FORMATION_VIEW.fov, margin = 24) {
  const t = 2 * Math.tan(THREE.MathUtils.degToRad(fov) / 2);
  const d = ((box.max.y - face) * frame.h) / (t * Math.max(frame.y - margin, 1));
  return THREE.MathUtils.clamp(d, fit * 0.1, fit * 0.45);
}

/**
 * The zoom's place between the fit (0) and the bust (1) of a zoom range, in the zoom's own steps (a notch or a
 * pinch multiplies the distance); below 0 further back than the fit.
 * @param {object} options
 */
export function zoomShare(distance, { fit, min }) {
  return fit > min && distance > 0 ? Math.log(fit / distance) / Math.log(fit / min) : 0;
}

/**
 * How far the orbit target has risen to the face (0 at the fit and further back, 1 at the bust): the face comes
 * to the frame point evenly, a notch or a pinch at a time, so closing in zooms towards the face instead of
 * first dropping the chibi.
 * @param {{fit: number, min: number}} range
 * @returns {number} The share, 0 to 1.
 */
export function liftShare(distance, range) {
  const s = clamp(zoomShare(distance, range), 0, 1);
  return clamp(1 - ((1 - s) * distance) / range.fit, 0, 1);
}

/**
 * A distance carried over to another framing: closer than the fit, as the same share of the way to the bust
 * (the target's lift goes on from where it was); further back, as the same multiple of the fit (up to 1.3).
 * @param {{fit: number, min: number}} from
 * @param {{fit: number, min: number}} to
 */
export function carryZoom(distance, from, to) {
  if (distance < from.fit) return to.fit * Math.pow(to.min / to.fit, Math.min(1, zoomShare(distance, from)));
  return to.fit * Math.min(distance / from.fit, 1.3);
}

/**
 * The camera distance's factor for a wheel step (> 0 backs away) in a zoom range: a notch is at least NOTCH px,
 * NOTCHES of them from the fit to the bust; a faster spin's bigger steps zoom further.
 * @param {number} px The wheel step in pixels.
 * @param {number} span ln(fit / bust) of the zoom range.
 */
export function wheelZoom(px, span) {
  return px ? Math.exp((Math.sign(px) * Math.max(Math.abs(px), NOTCH) * span) / (NOTCH * NOTCHES)) : 1;
}

/** The camera distance's factor for a pinch whose fingers spread by a scale (> 1 closes in). */
export const pinchZoom = (scale) => (scale > 0 ? Math.pow(scale, -PINCH) : 1);

/**
 * Keep the pan (the orbit target's offset from the lifted target, world units) inside the chibi's box as far as
 * the zoom allows: none at the fit and further back, the whole box at the bust. Clamps `pan` in place.
 * @param {number} share The zoom's place (zoomShare).
 * @param {THREE.Box3} box The chibi's box around the formation target.
 * @param {number} lift The target's rise above the formation target.
 * @returns {THREE.Vector3}
 */
export function clampPan(pan, share, box, lift) {
  const s = clamp(share, 0, 1);
  pan.x = clamp(pan.x, s * box.min.x, s * box.max.x);
  pan.y = clamp(pan.y, s * (box.min.y - lift), s * (box.max.y - lift));
  pan.z = clamp(pan.z, s * box.min.z, s * box.max.z);
  return pan;
}

/**
 * Take the zoom's state before a scroll that may turn out a swipe (attachInput).
 * @param {{goal: number|null, tween: object|null, distance: () => number}} view The view: goal (the distance a
 *     zoom eases to), tween (the glide) and distance().
 * @returns {{goal: number|null, tween: object|null, zoomed: boolean, distance: number}}
 */
export function saveZoom(view) {
  return { goal: view.goal, tween: view.tween, zoomed: !!view.tween?.zoomed, distance: view.distance() };
}
/**
 * Put the zoom's state back: during a glide its own easing of the distance resumes (a glide the swipe outlived
 * ends where it headed); a glide begun since owns the distance.
 * @param {{goal: number|null, tween: object|null, zoomed: boolean, distance: number}} saved
 */
export function restoreZoom(view, saved) {
  if (view.tween && view.tween === saved.tween) {
    view.goal = saved.goal;
    view.tween.zoomed = saved.zoomed;
  } else if (!view.tween) view.setZoomGoal(saved.goal ?? saved.tween?.dist1 ?? saved.distance);
}

/**
 * Read the view's wheel, pinch and pan input on an element, as the fullscreen lobby reads it (lobby-touch.js).
 * A mouse wheel zooms (wheelZoom); a trackpad swipe, a Magic Mouse scroll or a horizontal wheel pans while
 * zoomed in (as far as the fingers moved), and a scroll that starts like a notch and turns out a swipe takes
 * its zoom back (saveZoom, restoreZoom). A pinch zooms (pinchZoom): ctrl + wheel in Chromium and WebView2,
 * gesture events in WebKit, two fingers on a touch screen. A right- or middle-button drag pans.
 * @param {object} sink What the input drives: zoom(factor) multiplies the distance the zoom heads for,
 *     zoomSave() and zoomRestore(saved) take and put back the zoom's state, span() is the zoom range's
 *     (wheelZoom), pan(dx, dy) moves the chibi by CSS px.
 * @returns {() => void} Detaches the input.
 */
export function attachInput(el, sink) {
  let scroll = { kind: null, at: -Infinity },
    pinchUntil = -Infinity,
    gesture = 1,
    gesturing = false,
    drag = null;
  const touches = new Map();
  const wheel = (e) => {
    e.preventDefault();
    const unit = e.deltaMode === 1 ? 40 : e.deltaMode === 2 ? 800 : 1,
      now = performance.now(),
      step = wheelStep(e);
    if (e.ctrlKey) {
      scroll = { kind: null, at: -Infinity };
      if (now < pinchUntil) return;
      sink.zoom(
        step === "smooth"
          ? pinchZoom(Math.exp(-e.deltaY * unit * CTRL_PINCH))
          : wheelZoom(e.deltaY * unit, sink.span()),
      );
      return;
    }
    const fresh = !(now - scroll.at < SCROLL_MS && scroll.kind),
      kind = wheelKind(e, now, scroll);
    if (fresh) {
      // a scroll that starts like a wheel zooming in pixels keeps the zoom it started from, to go back to if it turns
      // out a trackpad (lobby-touch.js); a second notch confirms the wheel
      const zooms = kind === "mouse" && !e.deltaMode && Math.abs(e.deltaY) >= Math.abs(e.deltaX);
      scroll = { kind, at: now, from: zooms ? sink.zoomSave() : null };
    } else {
      if (scroll.kind === "mouse" && kind === "trackpad" && scroll.from != null) sink.zoomRestore(scroll.from);
      if (step === "notch" || kind === "trackpad") scroll.from = null;
      scroll.kind = kind;
      scroll.at = now;
    }
    if (kind === "mouse" && Math.abs(e.deltaY) >= Math.abs(e.deltaX))
      sink.zoom(wheelZoom(e.deltaY * unit, sink.span()));
    else sink.pan(-e.deltaX * unit, kind === "mouse" ? 0 : -e.deltaY * unit);
  };
  // WebKit's trackpad pinch (and its touch pinch, which also comes as pointers)
  const gestureStart = (e) => {
    e.preventDefault();
    gesture = 1;
    gesturing = true;
    pinchUntil = performance.now() + PINCH_HOLD;
  };
  const gestureChange = (e) => {
    e.preventDefault();
    pinchUntil = performance.now() + PINCH_HOLD;
    if (!(e.scale > 0)) return;
    sink.zoom(pinchZoom(e.scale / gesture));
    gesture = e.scale;
  };
  const gestureEnd = (e) => {
    e.preventDefault();
    gesturing = false;
    pinchUntil = performance.now() + PINCH_HOLD;
  };
  const spread = () => {
    const [a, b] = [...touches.values()];
    return Math.hypot(a[0] - b[0], a[1] - b[1]);
  };
  const down = (e) => {
    if (e.pointerType === "touch") touches.set(e.pointerId, [e.clientX, e.clientY]);
    else if ((e.button === 1 || e.button === 2) && !drag) {
      e.preventDefault();
      drag = { id: e.pointerId, at: [e.clientX, e.clientY] };
    }
  };
  const move = (e) => {
    if (drag?.id === e.pointerId) {
      sink.pan(e.clientX - drag.at[0], e.clientY - drag.at[1]);
      drag.at = [e.clientX, e.clientY];
      return;
    }
    if (!touches.has(e.pointerId)) return;
    const before = touches.size === 2 ? spread() : 0;
    touches.set(e.pointerId, [e.clientX, e.clientY]);
    if (before > 0 && !gesturing) {
      const after = spread();
      if (after > 0) sink.zoom(pinchZoom(after / before));
    }
  };
  const up = (e) => {
    touches.delete(e.pointerId);
    if (drag?.id === e.pointerId) drag = null;
  };
  const handlers = [
    ["wheel", wheel, { passive: false }],
    ["gesturestart", gestureStart],
    ["gesturechange", gestureChange],
    ["gestureend", gestureEnd],
    ["pointerdown", down],
    ["pointermove", move],
    ["pointerup", up],
    ["pointercancel", up],
  ];
  for (const [type, fn, opts] of handlers) el.addEventListener(type, fn, opts);
  return () => {
    for (const [type, fn, opts] of handlers) el.removeEventListener(type, fn, opts);
  };
}

// the front view, level, as on the formation screen
export const HOME = new THREE.Vector3(0, 0, 1);

/**
 * Frame the rendered silhouette, including the halo and props but excluding transparent mesh margins. WebGL
 * rows start at the bottom; camera view offsets start at the top.
 * @param {Uint8Array} pixels The RGBA pixels.
 * @param {number} width The width in pixels.
 * @param {number} height The height in pixels.
 * @param {number} [margin] The margin kept in pixels.
 * @returns {{x: number, y: number, w: number, h: number}|null} The frame; null when nothing is drawn.
 */
export function visibleStillFrame(pixels, width, height, margin = 24) {
  let left = width,
    right = -1,
    bottom = height,
    top = -1;
  for (let y = 0; y < height; y++)
    for (let x = 0; x < width; x++) {
      if (!pixels[(y * width + x) * 4 + 3]) continue;
      left = Math.min(left, x);
      right = Math.max(right, x);
      bottom = Math.min(bottom, y);
      top = Math.max(top, y);
    }
  if (right < left) throw new Error("3D still: no visible pixels");
  const scale = Math.max((right - left + 1) / (width - 2 * margin), (top - bottom + 1) / (height - 2 * margin));
  const w = width * scale,
    h = height * scale;
  return { x: (left + right + 1 - w) / 2, y: height - (bottom + top + 1 + h) / 2, w, h };
}

/**
 * The camera of the switch card's still: the live one's lens, at the front view at the fit distance from the
 * orbit target, the target on the frame's point of a w x h host. The live camera's orbit and zoom do not matter
 * (seen from straight above, the box's height shrinks to nothing on the canvas).
 * @param {THREE.PerspectiveCamera} camera
 * @param {THREE.Vector3} target
 * @param {{x: number, y: number}} frame The frame point in px.
 * @returns {THREE.PerspectiveCamera}
 */
export function stillCamera(camera, target, fit, frame, w, h) {
  const c = camera.clone();
  c.position.copy(target).addScaledVector(HOME, fit);
  c.up.set(0, 1, 0);
  c.lookAt(target);
  c.setViewOffset(w, h, w / 2 - frame.x, h / 2 - frame.y, w, h);
  c.updateMatrixWorld();
  return c;
}

/**
 * The still's crop of a W x H canvas rendered by a camera: the upper body, from the chibi's box (around the
 * target) as projected: its height, its centre over the root.
 * @param {THREE.Vector3} target
 * @param {THREE.Box3} box The chibi's box around the target.
 * @param {number} aspect The crop's width over its height.
 * @returns {{x: number, y: number, w: number, h: number}} The crop in canvas pixels.
 */
export function stillCrop(camera, target, box, W, H, aspect) {
  const at = (y) => new THREE.Vector3(target.x, target.y + y, target.z).project(camera);
  const top = at(box.max.y),
    bottom = at(box.min.y);
  const h = ((top.y - bottom.y) / 2) * H,
    x = ((top.x + 1) / 2) * W,
    y = ((1 - (top.y + bottom.y) / 2) / 2) * H;
  const ch = h * 0.5,
    cw = ch * aspect,
    cy = y - h * 0.18;
  return { x: Math.max(0, Math.min(W - cw, x - cw / 2)), y: Math.max(0, Math.min(H - ch, cy - ch / 2)), w: cw, h: ch };
}
