// A memorial lobby drawn the way the game's camera draws it: every skeleton and particle effect of the lobby into one
// target, in the order Unity sorts them (sorting order, render queue, then back to front), so the skeletons of one lobby
// interleave, which one canvas per skeleton cannot do. A lobby of several skeletons therefore draws them all in the
// bottom player's WebGL context (the host): the other players (guests) are made with that context and their canvases
// stay empty; the host draws every skeleton part (skeleton-parts.js) and the effects between them (particles-gl.js) into
// its linear framebuffer, post-processed once (linear.js). A lobby whose bottom skeleton fails to load keeps one context
// per skeleton, stacked (lobby.js).
import * as Linear from "./core/linear.js";
import * as Parts from "./spine/skeleton-parts.js";
import { compareKeys, queuesOf, sortKey } from "./particles.js";

const hosts = new WeakMap(); // guest player -> the player whose context it draws with
const own = new WeakMap(); // player -> its renderer's own drawSkeleton
const plans = new WeakMap(); // player -> what it draws: {steps, fx, guests}, and for checks the players
const hidden = new WeakSet(); // players not drawn now: the scenes only a lobby's intro shows (lobby-intro.js)
let interleave = true,
  latest = null; // for checks: the order mode, and the host installed last (a WeakRef)

/**
 * The order of a lobby's skeleton parts: one part per render queue of each layer's pages, lowest first.
 * Merged (one target) sorts them all as Unity sorts transparent submeshes, by sorting layer, sorting order,
 * render queue, then back to front (particles.js sortKey), equal keys in stacking order; otherwise it goes
 * layer after layer, as stacked canvases draw them.
 * @param {{sortingLayer?: number, order?: number, queue?: number, pages?: object, z?: number}[]} layers The
 *     players' layers in stacking order.
 * @param {boolean} [merged] Whether the parts share one target.
 * @returns {{layer: number, part: number, sortingLayer: number, order: number, queue: number, z: number}[]} The
 *     parts in drawing order.
 */
export function order(layers, merged = true) {
  const out = layers.flatMap((l, layer) =>
    queuesOf(l).map((queue, part) => ({
      layer,
      part,
      sortingLayer: l?.sortingLayer || 0,
      order: l?.order || 0,
      queue,
      z: l?.z || 0,
    })),
  );
  if (!merged) return out;
  return out.sort((a, b) => compareKeys(sortKey(a), sortKey(b)) || a.layer - b.layer || a.part - b.part);
}

/**
 * Make a spine-player; with a host (the lobby's bottom player), one that draws with the host's WebGL context:
 * the canvas it makes asks for a context while the player is made, and gets the host's. Losing and restoring
 * the host's context reaches the guest's textures and shaders too.
 * @param {() => object} create
 */
export function make(host, create) {
  const gl = host?.context?.gl;
  if (!gl || typeof HTMLCanvasElement === "undefined") return create();
  const proto = HTMLCanvasElement.prototype,
    getContext = proto.getContext;
  proto.getContext = function (type, ...rest) {
    return this !== gl.canvas && /webgl/i.test(type) ? gl : getContext.call(this, type, ...rest);
  };
  let player;
  try {
    player = create();
  } finally {
    proto.getContext = getContext;
  }
  if (player?.context?.gl !== gl) return player;
  hosts.set(player, host);
  for (const type of ["webglcontextlost", "webglcontextrestored"]) {
    player.addEventListener?.(gl.canvas, type, () => player.canvas?.dispatchEvent(new Event(type)));
  }
  return player;
}

/**
 * The player whose context a player draws with.
 * @returns {object|null} The host, or null.
 */
export const hostOf = (player) => hosts.get(player) || null;

// a player's element on the page: hidden, and left out of the stills and fades taken of the lobby's canvases
// (lobby-off), or shown again
function showElement(player, on) {
  const el = player?.parent || player?.dom?.parentElement;
  if (!el) return;
  el.style.visibility = on ? "" : "hidden";
  el.classList.toggle("lobby-off", !on);
}
/**
 * Show or hide a player (a scene only the intro shows). Drawn by a host, its parts are left out of the host's
 * frame, where the rest of the lobby and the effects keep their order; on a canvas of its own, its element is
 * hidden. The host's element always shows: it carries the whole lobby.
 */
export function setShown(player, on) {
  if (!player) return;
  if (on) hidden.delete(player);
  else hidden.add(player);
  if (!plans.get(player)?.guests.length) showElement(player, on);
}
/**
 * Whether a player is shown.
 * @returns {boolean} True unless setShown hid it.
 */
export const shown = (player) => !hidden.has(player);

/**
 * A player is loaded (its success hook, before its first frame). A guest stops drawing and running on its own:
 * its renderer draws nothing when its own frame asks, its frame loop stops (the host runs its frames) and a
 * window resize runs none. Any other player draws its skeleton's parts (install() adds what goes between them).
 */
export function ready(player) {
  const renderer = player?.sceneRenderer;
  if (!renderer || own.has(player)) return;
  own.set(player, renderer.drawSkeleton.bind(renderer));
  if (hosts.has(player)) {
    renderer.drawSkeleton = () => {};
    player.stopRequestAnimationFrame = true;
    if (Array.isArray(player.eventListeners)) {
      player.eventListeners = player.eventListeners.filter((e) => {
        if (e.target !== globalThis.window || e.event !== "resize") return true;
        e.target.removeEventListener(e.event, e.func);
        return false;
      });
    }
    return;
  }
  const layer = player.bamtLayer || {};
  const parts = Parts.partsOf(layer);
  plans.set(player, { steps: order([layer]).map((s) => ({ ...s, player, parts })), fx: null, guests: [] });
  renderer.drawSkeleton = (skeleton, ...args) => draw(player, skeleton, args);
}

/**
 * Every player of a lobby is in: fix the order every part and effect draws in. Guests of the host go into its
 * plan, the effects into the slots of the parts they sort after.
 * @param {object[]} players The players in stacking order, the host first.
 * @param {object|null} fx The lobby's effects (particles-gl.js).
 */
export function install(players, fx) {
  const host = players[0];
  if (!host) return;
  const merged = players.length > 1 && players.slice(1).every((p) => hosts.get(p) === host);
  const layers = players.map((p) => p.bamtLayer || {});
  const parts = layers.map((l) => Parts.partsOf(l));
  const steps = order(layers, merged && interleave).map((s) => ({
    ...s,
    player: players[s.layer],
    owner: merged ? host : players[s.layer],
    parts: parts[s.layer],
  }));
  // the effects go after the parts they sort after. In one target a hidden scene's parts draw nothing and the rest
  // keep their order; on canvases of their own no effect goes after a scene the intro keeps hidden (its canvas hides
  // with it): the effects on at rest keep the places they have without the scenes
  fx?.layers(players, merged ? steps : steps.filter((s) => !hidden.has(s.player)));
  for (const p of players) {
    if (hosts.has(p) && merged) continue;
    ready(p);
    const guests = merged && p === host ? players.slice(1) : [];
    plans.set(p, { steps: steps.filter((s) => s.owner === p), fx, guests, players });
  }
  if (merged) showElement(host, true);
  latest = typeof WeakRef === "undefined" ? null : new WeakRef(host);
}

/**
 * For checks: draw the skeletons of the lobby installed last interleaved by render queue in their one context
 * (true, as the game draws them) or one after another in stacking order (false, as one canvas per skeleton drew
 * them).
 */
export function setInterleaved(on) {
  interleave = !!on;
  const plan = plans.get(latest?.deref());
  if (plan?.players) install(plan.players, plan.fx);
}

/**
 * For checks: what the lobby installed last draws, in order.
 * @returns {Array<[string|undefined, number, boolean]>} [skeleton, render queue, whether effects draw next] per
 *     part.
 */
export function drawn() {
  const players = plans.get(latest?.deref())?.players || [];
  return players.flatMap((p) => {
    const plan = plans.get(p);
    return (plan?.steps || []).map((s, k) => [s.player.bamtLayer?.name, s.queue, !!plan.fx?.drawsAfter(p, k)]);
  });
}

/**
 * In the host's frame hook, before anything else: run its guests' frames (time, clips, animation, drags,
 * camera), so it draws them posed for this frame; the viewport each of them set goes back to the host's.
 */
export function frame(player) {
  const guests = plans.get(player)?.guests;
  if (!guests?.length) return;
  for (const g of guests) if (g.skeleton && !g.disposed && !g.error) g.drawFrame(false);
  const gl = player.context?.gl,
    canvas = player.canvas;
  if (gl && canvas) gl.viewport(0, 0, canvas.width, canvas.height);
}

// In place of spine-player's one drawSkeleton call (its renderer begun): every step of the player's plan, each with
// its own player's renderer and camera, the slots of the step's part only, and after a step the effects that sort
// after it (the batch flushed first). The guests' slot colours are tinted for linear light (linear.js) meanwhile.
function draw(player, skeleton, args) {
  const plan = plans.get(player),
    steps = plan?.steps || [];
  const base = player.sceneRenderer;
  if (steps.length <= 1 && !plan?.guests.length) {
    if (!hidden.has(player)) own.get(player)(skeleton, ...args);
    return;
  }
  for (const g of plan.guests) Linear.tint(g);
  let open = base;
  try {
    steps.forEach((step, i) => {
      const p = step.player,
        renderer = p.sceneRenderer,
        drawSkeleton = own.get(p);
      if (p.skeleton && renderer && drawSkeleton && !p.disposed && !p.error && !hidden.has(p)) {
        if (open !== renderer) {
          open?.end();
          renderer.begin();
          open = renderer;
        }
        Parts.drawPart(p.skeleton, step.parts, step.part, () => drawSkeleton(p.skeleton, ...args));
      }
      if (i < steps.length - 1 && plan.fx?.drawsAfter(player, i)) {
        open?.end();
        open = null;
        plan.fx.between(player, i);
      }
    });
  } finally {
    if (open !== base) {
      open?.end();
      base.begin();
    }
    for (const g of plan.guests) Linear.untint(g);
  }
}
