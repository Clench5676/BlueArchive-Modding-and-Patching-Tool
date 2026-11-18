// The detail screen's main Spine portrait: loading it, its blink and expressions, and disposing of it.
import { latestLoad, loadPlayer } from "../spine/spine-load.js";
import { ensureSpine } from "../spine/spine-runtime.js";
import { decodeAll } from "../core/image-decode.js";
import * as Loading from "../app/loading.js";
import * as Visual from "./visual-mode.js";
import { loadSpineAssets } from "../core/api.js";
import { idleAnimation, prioritise } from "./thumbs.js";
import { portraitViewport } from "./portrait-view.js";
import { $, S } from "../core/util.js";
import {
  find,
  ORIGIN,
  PORTRAIT_BOX,
  layouts,
  expression,
  mainPlayer,
  HARD_CAP,
  frames,
  renderFailed,
  batch,
  commit,
  open,
  alt,
  setMainPlayer,
} from "./detail.js";
import { renderExpressions } from "./detail-strip.js";

export let blinkTimer = null;
export let spineToken = 0;

// --- main Spine portrait ---
// loadSpine(base) resolves once the portrait's first frame is drawn and the expression strip shows its faces
// (also on an error, after the message popup). The new layer stays transparent until committed, then fades in
// over the old one, which goes. A portrait that cannot be shown takes the old one away too: the left side stays
// empty rather than showing another alt under this one's name.
const portraitLoads = latestLoad();
export function loadSpine(base) {
  const my = ++spineToken;
  return portraitLoads.run((signal) => showSpine(base, my, signal));
}
async function showSpine(base, my, signal) {
  const allowed = await ensureSpine();
  if (signal.aborted || my !== spineToken) return;
  const host = $("portrait");
  const layer = document.createElement("div");
  layer.className = host.children.length || batch ? "layer fading" : "layer";
  host.appendChild(layer);
  const previous = [...host.querySelectorAll(".layer")].filter((l) => l !== layer);
  clearTimeout(blinkTimer);
  // layers fade out, then their players are freed (once each: a failed portrait may retire layers already going)
  const retire = (layers) => {
    for (const old of layers) {
      if (old.retired) continue;
      old.retired = true;
      if (batch || Loading.covering()) {
        old.load?.dispose();
        old.remove();
        continue;
      }
      old.classList.add("fading");
      // disposePortrait() may have freed it meanwhile (a quick second step)
      setTimeout(() => {
        if (old.isConnected) {
          old.load?.dispose();
          old.remove();
        }
      }, 400);
    }
  };
  if (!allowed) {
    setMainPlayer(null);
    const portrait = alt().officialThumbnail;
    if (portrait) {
      const image = document.createElement("img");
      image.className = "portrait-static";
      image.alt = "";
      image.src = portrait;
      layer.appendChild(image);
      await decodeAll([portrait], () => image.decode?.(), HARD_CAP, { signal });
    }
    if (signal.aborted || my !== spineToken) {
      layer.remove();
      return;
    }
    commit(() => {
      if (signal.aborted || my !== spineToken) return;
      layer.classList.remove("fading");
      retire(previous);
    });
    return;
  }
  prioritise(base);
  try {
    await loadSpineAssets(
      base,
      async (resolved) => {
        if (signal.aborted || my !== spineToken) {
          layer.remove();
          return;
        }
        layer.load?.dispose();
        layer.replaceChildren();
        const load = (layer.load = loadPlayer(
          (config) => new spine.SpinePlayer(layer, config),
          {
            // the idle is set in success(): some NPC skeletons have no Idle_01, and spine-player fails on a missing one
            skeleton: `${resolved}.skel`,
            atlas: `${resolved}.atlas`,
            animation: undefined,
            premultipliedAlpha: false,
            alpha: true,
            backgroundColor: "#00000000",
            showControls: false,
            showLoading: false,
            preserveDrawingBuffer: true,
            // framed like the game (portrait-view.js), never on the skeleton's bounds; no viewport transition:
            // spine-player starts it from an undefined viewport and draws nothing meanwhile
            viewport: detailViewport(),
            // the visual mode (visual-mode.js) frames it while it is open
            frame: (p) => Visual.frame(p),
            success: async (player, live) => {
              // before the first await: once this returns, spine-player frames the view on track 0's animation and
              // plays. With nothing on track 0 it would pause (no face, no blink): a skeleton without an idle gets an
              // empty animation there that never ends, so it keeps its setup pose and the faces on tracks 1-2 still
              // play.
              const idle = idleAnimation(player.skeleton.data);
              if (idle) player.animationState.setAnimation(0, idle, true);
              else player.animationState.setEmptyAnimation(0, 0).trackEnd = Number.MAX_VALUE;
              if (my !== spineToken || !live()) return; // a newer portrait took over; it retires this layer
              setMainPlayer(player);
              applyExpression();
              await renderExpressions(); // the faces come from this skeleton
              await frames(2); // its first frame is on the canvas
              commit(() => {
                if (my !== spineToken || !live()) return;
                if (batch || Loading.covering()) layer.style.transition = "none";
                layer.classList.remove("fading");
                retire(previous);
              });
            },
          },
          {
            signal,
            onRecover: () => {
              if (open && !signal.aborted && my === spineToken) loadSpine(base).catch((err) => renderFailed(err, base));
            },
            onError: (err) => {
              if (signal.aborted || my !== spineToken) return;
              clearTimeout(blinkTimer);
              setMainPlayer(null);
              layer.querySelector(".spine-player-error")?.remove();
              retire([layer]);
              renderFailed(err, base);
            },
          },
        ));
        layer.player = load.player;
        await load.ready;
      },
      { signal },
    );
    if (signal.aborted || my !== spineToken) layer.remove();
  } catch (err) {
    if (!signal.aborted && my === spineToken) renderFailed(err, base);
    // spine-player's own error box (black, with the message) never shows: the message popup says it
    layer.querySelector(".spine-player-error")?.remove();
    if (my === spineToken) {
      if (mainPlayer === layer.player) {
        clearTimeout(blinkTimer);
        setMainPlayer(null);
      } // it broke after it was shown
      commit(() => {
        if (my === spineToken) retire([...previous, layer]);
      });
    }
  }
}
// the detail screen's portrait framing: the skeleton's origin on CharacterPos
export const detailViewport = () =>
  portraitViewport(layouts && find(layouts.list, "CharacterPos"), { origin: ORIGIN, s: S, box: PORTRAIT_BOX });
function scheduleBlink(player) {
  clearTimeout(blinkTimer);
  if (expression !== "00" || mainPlayer !== player) return;
  blinkTimer = setTimeout(
    () => {
      if (expression !== "00" || mainPlayer !== player) return;
      if (player.skeleton?.data.findAnimation("Eye_Close_01")) {
        player.animationState.setAnimation(2, "Eye_Close_01", false);
        player.animationState.addEmptyAnimation(2, 0.1, 0);
      }
      scheduleBlink(player);
    },
    2500 + Math.random() * 3500,
  );
}
export function applyExpression() {
  clearTimeout(blinkTimer);
  mainPlayer?.animationState.clearTrack?.(2);
  if (mainPlayer?.skeleton?.data.findAnimation(expression)) mainPlayer.animationState.setAnimation(1, expression, true);
  if (mainPlayer) scheduleBlink(mainPlayer);
}
export function disposePortrait() {
  clearTimeout(blinkTimer);
  spineToken += 1;
  portraitLoads.cancel();
  for (const l of $("portrait").querySelectorAll(".layer")) {
    l.load?.dispose();
    l.remove();
  }
  setMainPlayer(null);
}

// Setters of state of detail-portrait.js for the other detail modules: an imported binding is read-only.
export function setSpineToken(value) {
  spineToken = value;
}
