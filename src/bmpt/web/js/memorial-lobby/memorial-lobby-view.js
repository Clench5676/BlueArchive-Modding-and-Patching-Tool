// The view of one memorial lobby instance (memorial-lobby.js createMemorialLobby): the left view and fullscreen with
// their crossfade, the wheel zoom and pan inside the zoom-out limit, the camera framing, the thumbnail capture frame, the
// switch card's crop, and for art that does not cover the view the blurred edge extension and its feather. Views are
// {x, y, w, h} in NGUI units, y up. The functions take the instance's state S and functions L; this module keeps no
// state of its own.
import { memorialLobbyCaptureView, memorialLobbyMaximumWidth } from "../thumbnail-capture/camera.js";
import * as Linear from "../core/linear.js";
import * as Touch from "./memorial-lobby-touch.js";
import {
  ZOOM_IN,
  ZOOM_OUT_MAX,
  ZOOM_CUSTOM,
  clamp,
  framingView,
  cameraParams,
  ZOOM_MODES,
  zoomBounds,
  widest,
} from "./memorial-lobby-measure.js";

export const STAGE = { w: 1920, h: 1080 };
const GAME = { x: -1480, y: -832.5, w: 2960, h: 1665 }; // memorial-lobby.json "view": the game's 16:9 view in NGUI units
const ZOOM_TAU = 0.08; // s: a zoom or pan eases to its target
const FEATHER_MS = 100; // the fallback's feather and extension follow a zoom or pan at most this often
const FADE_MS = 450; // the switch crossfade, in step with the UI slide (app.css .45s)
// fallback only (art that does not cover the view): the size of the edge extension's canvas
export const EXT_W = 480,
  EXT_H = 270;
// a player's canvas on screen (not a layer the intro keeps hidden)
const shown = (p) => !!p.canvas && !p.parent?.classList.contains("memorial-lobby-off");
const inside = (v, P) => v.x >= P.x0 - 0.5 && v.x + v.w <= P.x1 + 0.5 && v.y >= P.y0 - 0.5 && v.y + v.h <= P.y1 + 0.5;

/**
 * The view functions of a memorial lobby instance.
 * @param {object} S The instance's state.
 * @param {object} L The instance's functions.
 * @returns {object} The functions this part adds to L.
 */
export function viewPart(S, L) {
  /** Tell the memorial lobby whether the dialogue is shown for the fullscreen view. */
  function setDialogueFullscreen(on) {
    S.dialogueFullscreen = on;
    S.current?.caption?.reposition();
  }
  /** Set the bounds the subtitle is kept inside, and place it again. */
  function setDialogueBounds(bounds) {
    S.dialogueViewBounds = bounds;
    S.current?.caption?.reposition();
  }
  const game = () => {
    const v = S.current?.specialGame || S.current?.info?.view || GAME;
    if (
      ![v.x, v.y, v.w, v.h].every(Number.isFinite) ||
      v.w <= 0 ||
      v.h <= 0 ||
      (!S.current?.framing && (v.w > GAME.w * 16 || v.h > GAME.h * 16))
    )
      throw new Error("Invalid memorial lobby camera view");
    return { ...v };
  };

  /**
   * Turn the camera framing on or off: on, the memorial lobby rests in fullscreen (no intro), the view starts at the
   * game's and measures how far it may go; off, the view goes back to the game's.
   */
  function camera(on) {
    S.cameraOn = !!on;
    if (!S.current?.meta) return;
    S.current.host?.classList.toggle("camera-framing", S.cameraOn);
    Touch.setPanOnly(S.cameraOn);
    if (S.cameraOn) {
      S.current.intro?.skip();
      L.measureCamera();
    }
    openEdge();
    resetZoom();
  }
  /**
   * The state for the camera's controls and checks: the view, the game's view, the limits and what the shutter
   * would keep.
   * @returns {object|null} The state; null before a memorial lobby is shown.
   */
  const cameraState = () => {
    const current = S.current;
    if (!current?.meta) return null;
    const G = game(),
      V = current.target || current.view,
      lim = current.camera?.lim;
    return {
      on: S.cameraOn,
      measuring: !!current.camera?.measuring,
      view: { ...V },
      game: G,
      prefab: !!current.info?.prefab,
      widest: lim?.sum ? lim.wMax : G.w,
      narrowest: G.w / ZOOM_IN,
      poses: lim?.poses || 0,
      ms: current.camera?.ms,
      params: cameraParams(V, G),
    };
  };

  const viewOf = (m, md = S.mode) => ({ ...(md === "full" ? m.full : m.left) });
  // the shared view of this frame, eased towards its target once per frame (every player asks in its frame hook; the
  // document timeline's time is the same for all of them within a frame)
  function viewNow() {
    const c = S.current;
    if (!c?.view) return null;
    const now = document.timeline?.currentTime ?? performance.now();
    if (now !== c.viewAt) {
      const dt = c.viewAt == null ? 0 : clamp((now - c.viewAt) / 1000, 0, 0.1);
      c.viewAt = now;
      const v = c.view,
        t = c.target;
      if (t && v !== t) {
        const k = 1 - Math.exp(-dt / ZOOM_TAU);
        const w = v.w + (t.w - v.w) * k;
        c.view = { x: v.x + (t.x - v.x) * k, y: v.y + (t.y - v.y) * k, w, h: (w * 9) / 16 };
        if (Math.abs(t.w - w) < 0.05 && Math.abs(t.x - c.view.x) < 0.05 && Math.abs(t.y - c.view.y) < 0.05) c.view = t;
        if (S.thumbnailFrame) thumbnailMatte(c.view);
        // sparse art (the fallback): the feather and the extension move with the art's border, while the view eases
        // (throttled) and once it settles
        if (c.meta?.sparse && (c.view === t || now - (c.featherAt ?? -Infinity) >= FEATHER_MS)) {
          c.featherAt = now;
          feather(c.view);
        }
      }
    }
    c.caption?.reposition();
    return c.view;
  }
  function setView(view, immediate) {
    S.current.target = view;
    if (immediate) {
      S.current.view = view;
      S.current.caption?.reposition();
    }
  }

  /**
   * Switch between the left view and fullscreen: the old framing fades out over the new one, in step with the UI
   * slide; the zoom resets.
   * @param {"left"|"full"} m
   */
  function frame(m) {
    const changed = m !== S.mode;
    S.mode = m;
    if (S.current && m === "left") S.current.userFramed = false;
    if (!S.current?.meta) return;
    if (changed && !L.paused()) crossfade();
    setView(viewOf(S.current.meta), true);
    feather(S.current.view);
    if (L.ownsInput() && m !== "full") Touch.detach();
    else if (L.ownsInput() && S.wantTouch && !L.paused()) Touch.attach(S.current.host);
  }
  function crossfade() {
    const fade = S.current.host?.querySelector(".memorial-lobby-fade");
    if (!fade) return;
    const g = fade.getContext("2d");
    g.clearRect(0, 0, fade.width, fade.height);
    const ext = S.current.host.querySelector(".memorial-lobby-extend");
    if (ext && !ext.hidden) g.drawImage(ext, 0, 0, fade.width, fade.height);
    for (const p of S.players) if (shown(p)) g.drawImage(p.canvas, 0, 0, fade.width, fade.height);
    fade.style.transition = "none";
    fade.style.opacity = "1";
    void getComputedStyle(fade).opacity; // start the fade from 1 in this frame, together with the UI slide
    fade.style.transition = `opacity ${FADE_MS}ms ease`;
    fade.style.opacity = "0";
  }
  const bounds = () => zoomBounds({ mode: S.zoomMode, custom: S.zoomCustom }, S.current.meta, game());
  /** Tell the memorial lobby the thumbnail capture frame, or none, so the view stays inside it. */
  function thumbnailCapture(frame) {
    S.thumbnailFrame = frame;
    const el = S.current?.host?.querySelector(".memorial-lobby-view");
    if (!frame && el) el.style.clipPath = "";
    reclamp();
    if (S.current?.view) feather(S.current.view);
  }
  function clampView(v) {
    if (S.cameraOn && S.current.camera?.lim) return framingView(v, S.current.camera.lim);
    const B = S.thumbnailFrame ? S.current.meta.edge : bounds();
    if (S.thumbnailFrame) return memorialLobbyCaptureView(v, B, S.thumbnailFrame);
    return { x: clamp(v.x, B.x0, B.x1 - v.w), y: clamp(v.y, B.y0, B.y1 - v.h), w: v.w, h: v.h };
  }
  /**
   * Zoom the fullscreen view at a point.
   * @param {number} factor The factor; above 1 zooms in.
   * @param {number} sx The pointer's x in stage pixels.
   * @param {number} sy The pointer's y in stage pixels.
   */
  function zoomAt(factor, sx, sy) {
    if (S.mode !== "full" || !S.current?.meta || !(factor > 0)) return;
    S.current.userFramed = true;
    const T = S.current.target,
      full = S.current.meta.full;
    const fx = sx / STAGE.w,
      fy = 1 - sy / STAGE.h,
      ux = T.x + fx * T.w,
      uy = T.y + fy * T.h;
    if (S.cameraOn && S.current.camera?.lim) {
      // centred across: only the height under the pointer stays put
      const w = T.w / factor,
        h = (w * 9) / 16;
      setView(clampView({ x: 0, y: uy - fy * h, w, h }), false);
      return;
    }
    const w = clamp(
        T.w / factor,
        full.w / ZOOM_IN,
        S.thumbnailFrame
          ? memorialLobbyMaximumWidth(S.current.meta.edge, full.w, S.thumbnailFrame)
          : Math.max(full.w, widest(bounds())),
      ),
      h = (w * 9) / 16;
    setView(clampView({ x: ux - fx * w, y: uy - fy * h, w, h }), false);
  }
  /**
   * Set the zoom-out setting: a view wider than the new limit comes back inside it, around the same centre; Full
   * Art and Unlocked measure the memorial lobby's clips (once).
   * @param {{mode: string, custom?: number}} zoom One of ZOOM_MODES, and for "custom" how many game views across
   *     the view may be.
   */
  function setZoomLimit(zoom) {
    S.zoomMode = ZOOM_MODES.includes(zoom?.mode) ? zoom.mode : "fullArt";
    S.zoomCustom = clamp(Number(zoom?.custom) || ZOOM_CUSTOM, 1, ZOOM_OUT_MAX);
    if (measuresClips()) L.measureClips();
    openEdge();
    reclamp();
  }
  const measuresClips = () => !S.current?.framing && (S.zoomMode === "fullArt" || S.zoomMode === "unlocked");
  // past the painted art (Unlocked, Custom) the memorial lobby shows what the game's memorial lobby camera clears to,
  // black (Persistent/Camera: solid colour clear), graded with PP on: the bottom layer draws it (linear.js setClear),
  // the page shows black behind it without WebGL2; sparse art keeps the edge extension
  function openEdge() {
    const open =
      (S.cameraOn || S.zoomMode === "unlocked" || S.zoomMode === "custom") &&
      !!S.current?.meta &&
      !S.current.meta.sparse;
    S.current?.host?.classList.toggle("open-edge", open);
  }
  /**
   * Set the game's PP option: off draws the memorial lobby without its post-processing volumes (the next frame shows
   * it).
   */
  function setPostProcessing(on) {
    Linear.setPostEnabled(on);
  }
  // a view wider than the limit now allows eases back inside it, around the same centre
  function reclamp() {
    if (!S.current?.meta || !S.current.target || S.mode !== "full") return;
    const T = S.current.target,
      full = S.current.meta.full;
    if (S.thumbnailFrame || S.cameraOn) {
      setView(clampView(T), false);
      return;
    }
    if (T.w <= full.w + 1e-6) {
      setView(clampView(T), false);
      return;
    }
    const cx = T.x + T.w / 2,
      cy = T.y + T.h / 2,
      w = Math.max(full.w, Math.min(T.w, widest(bounds()))),
      h = (w * 9) / 16;
    setView(clampView({ x: cx - w / 2, y: cy - h / 2, w, h }), false);
  }
  /** Pan by a stage-pixel drag (the content follows the pointer); it only moves while zoomed in. */
  function panBy(dx, dy) {
    if (S.mode !== "full" || !S.current?.meta) return;
    S.current.userFramed = true;
    const T = S.current.target;
    if (S.cameraOn) dx = 0; // the camera moves up and down only
    setView(clampView({ x: T.x - (dx / STAGE.w) * T.w, y: T.y + (dy / STAGE.h) * T.h, w: T.w, h: T.h }), false);
  }
  // the view a zoom or pan is easing to, and putting one back (memorial-lobby-touch.js: a scroll taken for a wheel that
  // turns out a trackpad swipe)
  const viewTarget = () => S.current?.target || null;
  function setViewTarget(v) {
    if (S.mode !== "full" || !S.current?.meta || !v) return;
    S.current.userFramed = true;
    setView(clampView(v), false);
  }
  /** Reset the zoom to the game's view. */
  function resetZoom() {
    if (!S.current?.meta) return;
    S.current.userFramed = false;
    setView(viewOf(S.current.meta), true);
    feather(S.current.view);
  }
  /**
   * For checks: the rects involved, the current view, the zoom relative to the game's view and the character's
   * tracks.
   * @returns {object|undefined} The measurements; undefined before a memorial lobby is shown.
   */
  const limits = () => {
    const current = S.current;
    return (
      current?.meta && {
        game: game(),
        painted: current.meta.painted,
        full: current.meta.full,
        left: current.meta.left,
        view: current.view,
        target: current.target,
        touch: current.meta.touch,
        zoom: current.meta.full.w / current.view.w,
        mode: S.mode,
        outer: current.meta.outer,
        whole: current.meta.whole,
        zoomLimit: { mode: S.zoomMode, custom: S.zoomCustom },
        bounds: bounds(),
        safe: current.meta.safe,
        measuring: !!current.measuring,
        tracks: L.mainPlayer()?.animationState?.tracks.map((t) => t?.animation?.name || null) || [],
      }
    );
  };

  /**
   * The switch card's still: a 450 x 253 crop around the face inside the left view (so fully painted), returned
   * in canvas pixels; the card's mask fades its edges, and its text covers the right third.
   * @returns {{x: number, y: number, w: number, h: number}|null} The crop; null before a memorial lobby is shown.
   */
  function stillRect(canvasW, canvasH) {
    const m = S.current?.meta;
    if (!m) return null;
    const v = m.left,
      T = m.touch,
      G = game();
    if (S.current.framing) return { x: 0, y: 0, w: canvasW, h: canvasH };
    const w = clamp(G.w * 0.5, v.w * 0.5, v.w),
      h = (w * 253) / 450;
    // the head-pat point (top of the head) at 32 % across and 20 % down the crop: the face in the card's clear part
    const x = clamp(T.x - 0.32 * w, v.x, v.x + v.w - w),
      top = clamp(T.y + 0.2 * h, v.y + h, v.y + v.h);
    return {
      x: ((x - v.x) / v.w) * canvasW,
      y: ((v.y + v.h - top) / v.h) * canvasH,
      w: (w / v.w) * canvasW,
      h: (h / v.h) * canvasH,
    };
  }
  // a stage point (px) -> NGUI units in the current view
  function toWorld(sx, sy) {
    const v = S.current?.view || game();
    return [v.x + (sx / STAGE.w) * v.w, v.y + (1 - sy / STAGE.h) * v.h];
  }

  // --- fallback only (art that does not cover the view): edge pixels stretched outwards and blurred ---
  function rectOnStage(view, w, h) {
    const P = S.current.meta.edge;
    return {
      l: ((P.x0 - view.x) / view.w) * w,
      r: ((P.x1 - view.x) / view.w) * w,
      t: ((view.y + view.h - P.y1) / view.h) * h,
      b: ((view.y + view.h - P.y0) / view.h) * h,
    };
  }
  function drawExtension(canvas, view) {
    const g = canvas.getContext("2d");
    const src = document.createElement("canvas");
    src.width = EXT_W;
    src.height = EXT_H;
    const sg = src.getContext("2d");
    for (const p of S.players) if (shown(p)) sg.drawImage(p.canvas, 0, 0, EXT_W, EXT_H);
    const r = rectOnStage(view, EXT_W, EXT_H);
    const L0 = Math.max(0, Math.ceil(r.l)),
      R0 = Math.min(EXT_W, Math.floor(r.r)),
      T0 = Math.max(0, Math.ceil(r.t)),
      B0 = Math.min(EXT_H, Math.floor(r.b));
    g.clearRect(0, 0, EXT_W, EXT_H);
    g.drawImage(src, 0, 0);
    // clamp: the last column or row inside the painted area is repeated out to the edge
    if (R0 < EXT_W && R0 > 2) g.drawImage(src, R0 - 2, 0, 2, EXT_H, R0, 0, EXT_W - R0, EXT_H);
    if (L0 > 0 && L0 < EXT_W - 2) g.drawImage(src, L0, 0, 2, EXT_H, 0, 0, L0, EXT_H);
    const mid = document.createElement("canvas");
    mid.width = EXT_W;
    mid.height = EXT_H;
    mid.getContext("2d").drawImage(canvas, 0, 0);
    if (B0 < EXT_H && B0 > 2) g.drawImage(mid, 0, B0 - 2, EXT_W, 2, 0, B0, EXT_W, EXT_H - B0);
    if (T0 > 0 && T0 < EXT_H - 2) g.drawImage(mid, 0, T0, EXT_W, 2, 0, 0, EXT_W, T0);
  }
  // a view inside the painted area needs nothing; otherwise the sharp memorial lobby fades out over the last stretch
  // before its painted border, into the blurred extension
  function feather(view) {
    if (S.thumbnailFrame) {
      thumbnailMatte(view);
      return;
    }
    const el = S.current?.host?.querySelector(".memorial-lobby-view"),
      ext = S.current?.host?.querySelector(".memorial-lobby-extend");
    if (!el || !S.current?.meta) return;
    clearInterval(S.extendTimer);
    const off = !!S.current.framing || inside(view, S.current.meta.edge);
    if (ext) ext.hidden = off;
    if (off) {
      el.style.webkitMaskImage = el.style.maskImage = "none";
      return;
    }
    const r = rectOnStage(view, STAGE.w, STAGE.h),
      f = 90,
      parts = [];
    if (r.r < STAGE.w) parts.push(`linear-gradient(90deg, #000 ${r.r - f}px, transparent ${r.r}px)`);
    if (r.l > 0) parts.push(`linear-gradient(270deg, #000 ${STAGE.w - r.l - f}px, transparent ${STAGE.w - r.l}px)`);
    if (r.b < STAGE.h) parts.push(`linear-gradient(180deg, #000 ${r.b - f}px, transparent ${r.b}px)`);
    if (r.t > 0) parts.push(`linear-gradient(0deg, #000 ${STAGE.h - r.t - f}px, transparent ${STAGE.h - r.t}px)`);
    const mask = parts.join(", ") || "none";
    el.style.webkitMaskImage = mask;
    el.style.maskImage = mask;
    el.style.webkitMaskComposite = parts.length > 1 ? "source-in" : "";
    el.style.maskComposite = parts.length > 1 ? "intersect" : "";
    // keep the extension in step with the animation (cheap: a 480 x 270 canvas a few times a second)
    const tick = () => S.current?.view && drawExtension(ext, S.current.view);
    tick();
    if (!L.paused()) S.extendTimer = setInterval(tick, 200);
  }
  // A camera may show beyond the art, but never synthesize stretched/blurred border pixels.
  function thumbnailMatte(view) {
    if (!S.current?.meta || !view) return;
    clearInterval(S.extendTimer);
    const el = S.current.host.querySelector(".memorial-lobby-view"),
      ext = S.current.host.querySelector(".memorial-lobby-extend");
    if (ext) ext.hidden = true;
    if (!el) return;
    const r = rectOnStage(view, STAGE.w, STAGE.h);
    el.style.webkitMaskImage = el.style.maskImage = "none";
    el.style.clipPath = `inset(${Math.max(0, r.t)}px ${Math.max(0, STAGE.w - r.r)}px ${Math.max(0, STAGE.h - r.b)}px ${Math.max(0, r.l)}px)`;
  }

  return {
    setDialogueFullscreen,
    setDialogueBounds,
    game,
    camera,
    cameraState,
    viewOf,
    viewNow,
    setView,
    frame,
    thumbnailCapture,
    zoomAt,
    setZoomLimit,
    measuresClips,
    openEdge,
    setPostProcessing,
    reclamp,
    panBy,
    viewTarget,
    setViewTarget,
    resetZoom,
    limits,
    stillRect,
    toWorld,
    feather,
    thumbnailMatte,
  };
}
