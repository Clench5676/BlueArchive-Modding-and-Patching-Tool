// The running scene of one memorial lobby instance (memorial-lobby.js createMemorialLobby): its timers, which pause
// with it, its claim on the shared fullscreen interaction (memorial-lobby-touch.js), suspension (another Student Hall
// mode, Now Loading, a background window), the first complete frame, the draw watchdog, the intro's controls and the
// checks' clock. The functions take the instance's state S and functions L; this module keeps no state of its own.
import { withPreviewTimeout as withTimeout } from "../core/async.js";
import { artBounds, overlaps, worldBounds } from "./memorial-lobby-health.js";
import * as Linear from "../core/linear.js";
import * as Stage from "./memorial-lobby-stage.js";
import * as Touch from "./memorial-lobby-touch.js";
import { rectOf } from "./memorial-lobby-measure.js";

// Every memorial lobby instance shares memorial-lobby-touch.js. The shared token also protects against late disposal
// when a caller claims another instance before releasing this one (including the detail screen's default instance).
const touchOwners = (globalThis[Symbol.for("bamt.memorial-lobby.touch-owners")] ||= new WeakMap());
// Each instance owns its deadlines; suspension retains remaining time rather than clearing and recreating a dialogue
// or random-idle run. Extracted playback tests still use native timers.
const nativeTimeout = (...args) => globalThis.setTimeout(...args),
  nativeClearTimeout = (id) => globalThis.clearTimeout(id);

/**
 * The scene functions of a memorial lobby instance.
 * @param {object} S The instance's state.
 * @param {object} L The instance's functions.
 * @returns {object} The functions this part adds to L.
 */
export function scenePart(S, L) {
  const ownsInput = () => S.ownsTouch && touchOwners.get(Touch) === S.touchToken;
  const paused = () => !!(S.current?.suspended || S.current?.backgroundPaused || S.current?.loadingPaused);
  function armTimer(timer) {
    timer.started = performance.now();
    timer.id = nativeTimeout(
      () => {
        S.sceneTimers.delete(timer);
        timer.fn();
      },
      Math.max(0, timer.remaining),
    );
  }
  function setTimeout(fn, ms = 0) {
    const timer = { fn, remaining: ms, id: null, started: null };
    S.sceneTimers.set(timer, timer);
    if (!paused()) armTimer(timer);
    return timer;
  }
  function clearTimeout(timer) {
    if (!timer) return;
    nativeClearTimeout(timer.id);
    S.sceneTimers.delete(timer);
  }
  function pauseTimers() {
    for (const timer of S.sceneTimers.values())
      if (timer.id != null) {
        nativeClearTimeout(timer.id);
        timer.remaining -= Math.max(0, performance.now() - timer.started);
        timer.id = null;
      }
  }
  function resumeTimers() {
    for (const timer of S.sceneTimers.values()) if (timer.id == null) armTimer(timer);
  }
  function clearTimers() {
    for (const timer of S.sceneTimers.values()) nativeClearTimeout(timer.id);
    S.sceneTimers.clear();
  }

  function releaseTouch() {
    const owned = ownsInput();
    S.ownsTouch = false;
    if (!owned) return;
    touchOwners.delete(Touch);
    Touch.detach();
    Touch.setup(null);
  }
  function restoreTouch() {
    if (!S.current?.touch || paused()) return;
    touchOwners.set(Touch, S.touchToken);
    Touch.setup(S.current.touch);
    S.ownsTouch = true;
    if (S.mode === "full" && S.wantTouch) Touch.attach(S.current.host);
  }
  /** Switch the fullscreen interaction on or off (memorial-lobby-touch.js); it only runs in the fullscreen framing. */
  function interactive(on) {
    S.wantTouch = !!on;
    if (S.wantTouch && S.mode === "full" && S.current?.meta && !paused()) {
      if (!ownsInput()) restoreTouch();
      else Touch.attach(S.current.host);
    } else if (ownsInput()) Touch.detach();
  }
  /**
   * The input trace of the fullscreen interaction (memorial-lobby-touch.js).
   * @param {((events: object[]) => void)|null} sink Gets batches of events; null turns the trace off.
   */
  const setTrace = (sink) => Touch.setTrace(sink);

  function freezePlayer(player, c) {
    c.pauseFlags ||= new Map();
    // A native window can lose focus while the player's assets are loading. Its constructor pause is not a
    // playback preference: loadSkeleton will still call play() after success. The first loaded frame captures it.
    if (player.bamtLayer && player.animationState && !c.pauseFlags.has(player)) c.pauseFlags.set(player, player.paused);
    player.paused = true;
    player.stopRendering?.();
  }
  function freezeScene(c) {
    if (S.current !== c) return;
    c.pausedAt ??= performance.now();
    c.running = false;
    c.host.inert = true;
    pauseTimers();
    clearInterval(S.extendTimer);
    S.extendTimer = null;
    releaseTouch();
    for (const player of S.players) freezePlayer(player, c);
    for (const audio of [...S.audios, ...S.Sfx.playing])
      if (!audio.paused && !audio.ended) {
        c.resumeAudio.add(audio);
        audio.pause();
      }
  }
  function resumeScene(c) {
    if (S.current !== c || paused() || c.running || c.signal.aborted) return;
    c.running = true;
    c.host.inert = !!c.inputWasInert;
    if (S.talk && c.pausedAt != null) S.talk.started += Math.max(0, performance.now() - c.pausedAt);
    c.pausedAt = null;
    c.viewAt = null;
    c.introTime = undefined;
    c.drawnAt = performance.now();
    for (const player of S.players) {
      if (c.pauseFlags?.has(player)) player.paused = c.pauseFlags.get(player);
      freshClock(player);
    }
    c.pauseFlags?.clear();
    restoreTouch();
    resumeTimers();
    L.feather(c.view);
    for (const audio of c.resumeAudio)
      if (!S.muted && S.volume > 0 && !audio.ended && !audio.bamtOwnerSet?.closed) audio.play().catch(() => {});
    c.resumeAudio.clear();
    const sounds = c.pendingSounds.splice(0);
    for (const [id, set] of sounds) if (!set?.closed) S.Sfx.play(id, set);
    reveal(false);
    // Let a previously queued frame observe stopRendering before permitting a single replacement RAF loop.
    (globalThis.requestAnimationFrame || queueMicrotask)(() => {
      if (S.current !== c || paused() || !c.running || c.signal.aborted) return;
      for (const player of S.players)
        if (
          !Stage.hostOf(player) &&
          player.stopRequestAnimationFrame &&
          S.playerLoads.get(player)?.active?.() !== false
        ) {
          freshClock(player);
          player.startRendering?.();
        }
    });
  }
  /**
   * Pause the memorial lobby while Student Hall keeps an attached instance and another mode is visible. All clocks and
   * audio resume from this point.
   */
  function suspend() {
    const c = S.current;
    if (!c || c.suspended) return;
    c.suspended = true;
    freezeScene(c);
  }
  /** Resume the memorial lobby after suspend(). */
  function resume() {
    const c = S.current;
    if (!c) return;
    c.suspended = false;
    resumeScene(c);
  }
  /**
   * Draw the memorial lobby's first complete frame: it settles only after every optional FX/material image is in and an
   * actual complete composite has been drawn. Preparing the intro's frame zero queues its time-zero sounds for
   * activation instead of consuming them silently.
   * @returns {Promise<boolean>} True when it is drawn; false when the memorial lobby changed meanwhile.
   */
  async function prepareFrame() {
    const c = S.current;
    if (!c) return false;
    await c.ready;
    if (S.current !== c || c.signal.aborted) return false;
    await withTimeout(Promise.all(c.visualLoads.values()), 15000, "MemorialLobby visual assets", { signal: c.signal });
    if (S.current !== c || c.signal.aborted) return false;
    if (S.thumbnailFrame && c.target) {
      c.view = { ...c.target };
      c.viewAt = null;
      L.thumbnailMatte(c.view);
    }
    const flags = new Map(S.players.map((player) => [player, player.paused]));
    c.preparingFrame = true;
    try {
      for (const player of S.players) {
        player.paused = false;
        clocked(player);
        freshClock(player);
      }
      for (const player of S.players) if (!Stage.hostOf(player)) player.drawFrame?.(false);
      if (c.error) throw c.error;
      if (S.players.some((player) => player.error || player.disposed))
        throw new Error("MemorialLobby frame preparation failed");
      c.prepared = true;
      return true;
    } finally {
      c.preparingFrame = false;
      for (const [player, flag] of flags) player.paused = flag;
    }
  }

  // --- the intro (memorial-lobby-intro.js) ---
  /**
   * The memorial lobby is on screen (the detail screen has committed it): its intro starts with the next frame it is
   * visible.
   */
  function reveal(restart = true) {
    const c = S.current;
    if (!c || c.suspended || c.backgroundPaused || c.loadingPaused) return;
    c.revealed = true;
    // A player first laid out under a hidden tab must get a frame with the visible canvas's size.
    for (const p of S.players)
      if (
        restart &&
        !document.hidden &&
        (document.hasFocus?.() ?? true) &&
        S.playerLoads.get(p)?.active?.() !== false &&
        !Stage.hostOf(p) &&
        p.stopRequestAnimationFrame
      )
        p.startRendering?.();
    clearTimeout(c.watchdog);
    const check = () => {
      if (S.current !== c || c.signal.aborted) return;
      const visible =
        !document.hidden &&
        (document.hasFocus?.() ?? true) &&
        S.players.every((p) => S.playerLoads.get(p)?.active?.() !== false) &&
        (c.host.checkVisibility ? c.host.checkVisibility() : !c.host.hidden);
      if (visible) {
        const roots = S.players.filter((p) => !Stage.hostOf(p));
        if (roots.some((p) => p.context?.gl?.isContextLost())) {
          L.recoverView(c);
          return;
        }
        const err = roots.find((p) => p.error || p.disposed);
        const sized = roots.some((p) => p.canvas?.width > 0 && p.canvas?.height > 0);
        if (err || !sized || !c.drawnAt || performance.now() - c.drawnAt > 4000) {
          L.failView(
            new Error(err ? "MemorialLobby renderer stopped" : "MemorialLobby produced no visible frames"),
            c.host,
            `${c.artBase}files.json`,
            "draw watchdog",
          );
          return;
        }
        if (c.intro?.pending()) {
          const state = c.intro.state();
          if (!state.started || !Number.isFinite(state.t) || c.introTime === state.t) {
            L.failView(
              new Error("MemorialLobby intro is not advancing after reveal"),
              c.host,
              `${c.base}memorial-lobby.json`,
              "intro watchdog",
            );
            return;
          }
          c.introTime = state.t;
        }
        if (!c.intro?.pending() && !S.talk && !(S.mode === "full" && c.userFramed)) {
          try {
            const visibleArt = c.all.some(
              (l) =>
                Stage.shown(l.player) &&
                overlaps(worldBounds(artBounds(l.player.skeleton), l.player.bamtLayer), rectOf(c.view)),
            );
            if (!visibleArt) throw new Error("No memorial lobby art intersects the camera");
            if (!c.pixelsChecked) {
              if (!roots.some((p) => Linear.hasPixels(p))) throw new Error("MemorialLobby render target is empty");
              c.pixelsChecked = true;
            }
          } catch (err) {
            L.failView(err, c.host, `${c.artBase}files.json`, "draw bounds");
            return;
          }
        }
      }
      c.watchdog = setTimeout(check, 5000);
    };
    c.watchdog = setTimeout(check, 5000);
  }
  /**
   * The intro setting: on plays it when the memorial lobby is entered, off shows the memorial lobby as the intro leaves
   * it (Shiroko's holds its last frame); a memorial lobby shown now keeps what it does.
   */
  function setIntro(on) {
    S.introOn = on !== false;
  }
  /** Wait for the memorial lobby's intro to end, be skipped or be missing (the switch card's still waits for it). */
  function introDone() {
    const c = S.current;
    return c?.ready ? c.ready.then(() => c.intro?.done()) : Promise.resolve();
  }
  /** Skip the memorial lobby's intro. */
  const skipIntro = () => S.current?.intro?.skip();
  /** Whether the intro is waiting to start. */
  const introPending = () => !!S.current?.intro?.pending();
  /**
   * For checks: the intro's time and state.
   * @returns {object|null} The state; null without an intro.
   */
  const introState = () => S.current?.intro?.state() || null;

  // for checks: every player's frame advances by a fixed `dt` seconds (0 holds the memorial lobby still), or by the
  // real time again (null); advance(dt) gives each player one frame of dt
  /**
   * For checks: every player's frame advances by a fixed `dt` seconds (0 holds the memorial lobby still), or by the
   * real time again (null).
   * @param {number|null} dt The step in seconds.
   */
  function setClock(dt) {
    S.fixedDt = dt == null ? null : Math.max(0, Number(dt) || 0);
    for (const p of S.players) clocked(p);
  }
  // a player on the checks' clock (a memorial lobby shown after setClock too)
  function clocked(p) {
    if (!p.time || p.time.bamtUpdate) return;
    const update = p.time.update.bind(p.time);
    p.time.bamtUpdate = update;
    p.time.update = () => {
      const total = p.time.totalTime;
      update();
      if (paused() || S.current?.preparingFrame) {
        p.time.delta = 0;
        p.time.totalTime = total;
        return;
      }
      if (S.fixedDt == null) return;
      p.time.delta = S.stepDt.has(p) ? S.stepDt.get(p) : S.fixedDt;
      S.stepDt.delete(p);
    };
  }
  function freshClock(p) {
    if (!p.time) return;
    const total = p.time.totalTime;
    (p.time.bamtUpdate || p.time.update)?.call(p.time);
    p.time.delta = 0;
    p.time.totalTime = total;
  }
  /**
   * For checks: give each player one frame of dt.
   * @param {number} dt The step in seconds.
   */
  function advance(dt) {
    for (const p of S.players) S.stepDt.set(p, Math.max(0, Number(dt) || 0));
  }

  return {
    ownsInput,
    paused,
    setTimeout,
    clearTimeout,
    clearTimers,
    releaseTouch,
    restoreTouch,
    interactive,
    setTrace,
    freezePlayer,
    freezeScene,
    resumeScene,
    suspend,
    resume,
    prepareFrame,
    reveal,
    setIntro,
    introDone,
    skipIntro,
    introPending,
    introState,
    setClock,
    clocked,
    freshClock,
    advance,
  };
}
