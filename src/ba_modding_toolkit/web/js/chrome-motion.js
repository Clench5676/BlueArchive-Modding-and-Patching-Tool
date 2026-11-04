// The top bar and the bottom menu, moved as the game's lobby moves them (UILobby's UIAni_Lobby, ui-tokens.js LOBBY_ENTRY).
// - Entry: both wait off the screen until the first screen shows (enter()), then come in on the clip's own curves.
// - Immersive scenes (window-layout.js isImmersive: detail and Hall fullscreen, photography) send both away. A pointer at
//   the window's top or bottom edge brings that edge's bar back, usable, until the pointer has been off it for HIDE_DELAY;
//   leaving the window over a bar keeps it. The scenes' own controls make room (corner-layout.js, bamtChromeInsets).
// - The macOS window buttons travel with the top bar: the same offsets go to the native side (window.motion).
// Offsets are viewport CSS pixels away from the screen: up for the top bar, down for the bottom menu.
import { LOBBY_ENTRY } from "./ui-tokens.js";
import { reflowCorners } from "./corner-layout.js";

const UNIT = 1920 / 2960;
export const FRAME = 1 / 60;
export const EDGE_BAND = 16;        // px at the window's edge that bring a hidden bar out
export const HIDE_DELAY = 500;      // ms off a revealed bar before it leaves
export const EXTENT_MARGIN = 8;     // px past a bar's lowest (highest) painted edge, hanging Back art included
export const ENTRY_FALLBACK = 30000; // ms: the bars come in even if the first screen never reports

// Unity's AnimationCurve between unweighted keys [time, value, slope]: a cubic Hermite segment.
export function hermite(keys, t) {
  if (t <= keys[0][0]) return keys[0][1];
  for (let i = 1; i < keys.length; i++) {
    const [t0, v0, m0] = keys[i - 1], [t1, v1, m1] = keys[i];
    if (t > t1) continue;
    const dt = t1 - t0, s = (t - t0) / dt, s2 = s * s, s3 = s2 * s;
    return (2 * s3 - 3 * s2 + 1) * v0 + (s3 - 2 * s2 + s) * dt * m0 + (3 * s2 - 2 * s3) * v1 + (s3 - s2) * dt * m1;
  }
  return keys.at(-1)[1];
}
export const duration = (keys) => keys.at(-1)[0];
// The share of the clip's travel still ahead at t: 1 at the start, 0 at the end.
export const remaining = (keys, t) => hermite(keys, t) / keys[0][1];
export const offsetAt = (keys, from, to, t) => t >= duration(keys) ? to : to + (from - to) * remaining(keys, Math.max(0, t));
// The offsets at FRAME steps, the last one exactly `to`: what the native window buttons play.
export function frames(keys, from, to) {
  const out = [];
  for (let i = 0; i * FRAME < duration(keys); i++) out.push(offsetAt(keys, from, to, i * FRAME));
  out.push(to);
  return out;
}
export const entryDistance = (keys, scale) => Math.abs(keys[0][1]) * UNIT * scale;

export function createChromeMotion(env = {}) {
  const doc = env.document ?? globalThis.document, view = env.window ?? globalThis;
  const raf = env.raf ?? ((fn) => view.requestAnimationFrame(fn));
  const now = env.now ?? (() => view.performance.now());
  const later = env.setTimeout ?? ((fn, ms) => view.setTimeout(fn, ms));
  const cancel = env.clearTimeout ?? ((id) => view.clearTimeout(id));
  const send = env.rpc ?? (() => Promise.resolve());
  const reflow = env.reflow ?? reflowCorners;
  let onChange = () => {}, lights = () => false;
  const edge = (name, keys) => ({ name, keys, offset: 0, from: 0, to: 0, start: 0, moving: false, away: true,
    revealed: false, timer: null, extent: 0, base: null });
  const top = edge("top", LOBBY_ENTRY.top), bottom = edge("bottom", LOBBY_ENTRY.bottom), edges = [top, bottom];
  let entered = false, immersive = false, pointer = null, outside = false, ticking = false, lightsAt = null, fallback = null;

  const scale = () => {
    const box = doc.getElementById("stage")?.getBoundingClientRect?.();
    return box?.width ? box.width / 1920 : Math.min(view.innerWidth / 1920, view.innerHeight / 1080) || 1;
  };
  const bar = () => doc.querySelector(".topbar"), nav = () => doc.querySelector(".nav");
  const parts = (e) => [e === top ? bar() : nav()].filter(Boolean);

  // Where the bar rests, whatever its offset now: the top bar's lowest painted edge, the menu's top.
  function measure(e) {
    if (e === top) {
      const header = bar();
      if (!header) return 0;
      const boxes = [header, ...header.querySelectorAll("*")].map((el) => el.getBoundingClientRect())
        .filter((box) => box.width || box.height);
      return boxes.length ? Math.max(0, Math.max(...boxes.map((box) => box.bottom)) + top.offset + EXTENT_MARGIN) : 0;
    }
    const box = nav()?.getBoundingClientRect();
    return box && (box.width || box.height) ? view.innerHeight - (box.top - bottom.offset) + EXTENT_MARGIN : 0;
  }
  const shown = (e) => entered && (!immersive || e.revealed);
  const rest = (e) => entered ? e.extent : Math.max(entryDistance(e.keys, scale()), e.extent);

  function paint() {
    const s = scale(), header = bar(), host = doc.querySelector(".window-header"), menu = nav();
    // the native toolbar's wrapper is fixed in the viewport; without it the bar sits in the stage
    if (host) { host.style.translate = top.offset ? `0 ${-top.offset}px` : ""; if (header) header.style.translate = ""; }
    else if (header) header.style.translate = top.offset ? `0 ${-top.offset / s}px` : "";
    if (menu) menu.style.translate = bottom.offset ? `0 ${bottom.offset / s}px` : "";
    const root = doc.documentElement.classList;
    root.toggle("chrome-hide-top", top.away && !top.moving);
    root.toggle("chrome-hide-bottom", bottom.away && !bottom.moving);
    view.bamtChromeInsets = { offset: top.offset, top: immersive ? Math.max(0, top.extent - top.offset) : 0,
      bottom: immersive ? Math.max(0, bottom.extent - bottom.offset) : 0 };
    reflow();
  }

  function sendLights(list) {
    if (!lights()) { lightsAt = null; return; }
    lightsAt = list.at(-1);
    Promise.resolve(send("window.motion", { frames: list.map((v) => Math.round(v * 100) / 100), period: FRAME }))
      .catch(() => {});
  }

  // Hall and detail fullscreen make the bars inert while they own the scene: a revealed bar is usable again, a leaving
  // one gets their state back. Outside immersion the bars are left alone.
  function inert(e, away) {
    if (!immersive) return;
    for (const el of parts(e)) el.inert = away ? !!e.base?.get(el) : false;
  }

  function step() {
    let more = false;
    for (const e of edges) if (e.moving) {
      const t = (now() - e.start) / 1000;
      e.offset = offsetAt(e.keys, e.from, e.to, t);
      if (t >= duration(e.keys)) { e.moving = false; e.offset = e.to; } else more = true;
    }
    paint();
    if (more) raf(step);
    else { ticking = false; onChange(); }
  }

  function update(e) {
    const away = !shown(e);
    e.extent = measure(e);
    const to = away ? rest(e) : 0, changed = away !== e.away;
    e.away = away;
    inert(e, away);
    if (e.to === to && (e.moving || e.offset === to)) { if (changed) onChange(); return; }
    e.from = e.offset; e.to = to; e.start = now(); e.moving = e.from !== to;
    if (e === top) sendLights(frames(e.keys, e.from, to));
    if (changed) onChange();
    paint();
    if (e.moving && !ticking) { ticking = true; raf(step); }
  }

  const clearTimer = (e) => { if (e.timer != null) cancel(e.timer); e.timer = null; };
  function evaluate() {
    if (!entered || !immersive || !pointer) return;
    for (const e of edges) {
      const fromEdge = e === top ? pointer.y : view.innerHeight - pointer.y;
      if (!e.revealed) {
        if (!outside && fromEdge <= EDGE_BAND) { e.revealed = true; clearTimer(e); update(e); }
      } else if (fromEdge <= e.extent) clearTimer(e);
      else if (e.timer == null) e.timer = later(() => { e.timer = null; e.revealed = false; update(e); }, HIDE_DELAY);
    }
  }

  const api = {
    setup({ onChange: change, lights: native } = {}) {
      if (change) onChange = change;
      if (native) lights = native;
      // before the first screen both bars wait where the clip starts
      for (const e of edges) { e.away = true; e.extent = measure(e); e.offset = e.to = rest(e); }
      paint();
      view.addEventListener("pointermove", (event) => { pointer = { x: event.clientX, y: event.clientY }; outside = false; evaluate(); },
        { capture: true, passive: true });
      // the pointer left the page: over a bar (the window buttons, the menu bar) that bar stays
      doc.addEventListener("mouseout", (event) => { if (!event.relatedTarget) { outside = true; evaluate(); } }, true);
      fallback = later(() => api.enter(), ENTRY_FALLBACK);
    },
    enter() {
      if (entered) return;
      entered = true; if (fallback != null) cancel(fallback); fallback = null;
      update(top); update(bottom);
    },
    setImmersive(on) {
      on = !!on;
      if (on === immersive) return;
      immersive = on;
      for (const e of edges) {
        clearTimer(e); e.revealed = false;
        e.base = on ? new Map(parts(e).map((el) => [el, el.inert])) : null;
      }
      if (entered) { update(top); update(bottom); } else paint();
    },
    // A layout pass: a new toolbar wrapper takes the offset; a resized window moves a bar at rest to its new rest.
    apply() {
      for (const e of edges) if (!e.moving) {
        e.extent = measure(e);
        const to = e.away ? rest(e) : 0;
        if (Math.abs(to - e.offset) > .5) { e.offset = e.to = to; if (e === top) sendLights([to]); }
      }
      paint();
    },
    // The native chrome came (back): its buttons take the top bar's offset.
    syncLights() { if (!top.moving && lights() && lightsAt !== top.offset) sendLights([top.offset]); },
    hidden: (name) => (name === "bottom" ? bottom : top).away,
    state: () => ({ entered, immersive, top: { ...top, base: undefined }, bottom: { ...bottom, base: undefined } }),
  };
  return api;
}

let shared = null;
export const chromeMotion = () => shared ||= createChromeMotion();
