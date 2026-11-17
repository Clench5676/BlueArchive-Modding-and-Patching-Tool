// The detail screen's fullscreen and visual modes: the stage, the exit button, the camera bridge, and switching the
// fullscreen view.
import * as Photo from "../photography/controller.js";
import * as Camera from "../lobby/lobby-camera.js";
import { specialFraming } from "./special-view.js";
import * as NGUI from "../ui/ngui.js";
import * as Loading from "../app/loading.js";
import * as Lobby from "../lobby/lobby.js";
import * as Visual from "./visual-mode.js";
import { spineBase } from "../core/api.js";
import { T } from "../core/i18n.js";
import { switchKindOf } from "../mods/switch-sources.js";
import { $ } from "../core/util.js";
import {
  stage,
  variantKey,
  layouts,
  tab,
  fullscreen,
  mainPlayer,
  lobbyShot,
  modelShot,
  modelLib,
  ModelLib,
  frames,
  open,
  thumb,
  variant,
  previewAvailable,
  current,
  renderAll,
  changeInPlace,
  renderLeft,
  reload,
  setLobbyShot,
  setModelShot,
  setFullscreenState,
  setTab,
} from "./detail.js";
import { busy } from "./detail-actions.js";
import { renderScreen } from "./detail-screen.js";
import { detailViewport } from "./detail-portrait.js";
import { openPopup } from "./detail-switchers.js";
import { snapshotLobby } from "./detail-lobby-tab.js";
import { photoSession, photoBridge } from "./detail-photo.js";

// fullscreen memorial lobby: every UI part slides out its own way; the exit button hides when the pointer rests
let idleTimer = null;
const wake = () => {
  const exit = $("fs-exit");
  exit.classList.remove("idle");
  clearTimeout(idleTimer);
  idleTimer = setTimeout(() => exit.classList.add("idle"), 2500);
};
// in fullscreen the character can be touched and the view zoomed like in the game; leaving resets the zoom. The
// portrait's fullscreen is the game's visual mode (visual-mode.js), with the game's own buttons instead of fs-exit.
// Leaving puts every part back whatever the tab is by now (an in-place show may have changed it); setStage(false)
// closes the visual mode.
let fullscreenRequest = 0;
export async function setFullscreen(on, { switching = false } = {}) {
  const request = ++fullscreenRequest,
    part = tab,
    key = variantKey;
  if (on && tab === "portrait") {
    return enterVisual({ switching });
  }
  if (on) {
    const layout = await (layouts.visual ||= NGUI.layout("UICharacterVisualMode").catch(() => null));
    if (layout) await Visual.exitControl($("fs-exit"), layout);
    const control =
      tab === "lobby" && previewAvailable(variant(), "spr")
        ? await Visual.prepareSwitch({
            layout,
            label: T("row.switch.portrait"),
            onSwitch: switchFullscreenView,
          })
        : null;
    if (request !== fullscreenRequest || !open || tab !== part || variantKey !== key) return;
    $("visual-ui").replaceChildren(...(control ? [control] : []));
    $("visual-ui").hidden = !control;
  } else if (part !== "portrait") {
    $("visual-ui").hidden = true;
    $("visual-ui").replaceChildren();
  }
  setStage(on);
  if (!on || tab === "lobby") {
    Lobby.frame(on ? "full" : "left");
    Lobby.interactive(on);
  }
  if (!on || tab === "model") modelLib?.then((M) => M.frame(on ? "full" : "left"));
  showExit(on);
  if (!on) Camera.close(false);
  await Photo.visualEntry(on);
  await Camera.entry(on && tab === "lobby", cameraBridge);
  if (!on) {
    await photoBridge.release();
    stillAfterFullscreen();
  }
}
// the camera framing's view of the detail page (lobby-camera.js)
// source: what the lobby shows (the camera's art); saved: the student's lobby choices read again with the new one;
// still: a saved camera's still, as its Switch Memorial Lobby card draws it; album: Switch Memorial Lobby itself,
// resolved once it closed and what was chosen there is shown
export let albumClosed = null;
const cameraBridge = {
  variant: () => variantKey,
  lobby: () => variant()?.lobby,
  source: () => {
    const c = current("lobby");
    return c?.kind === "mod" ? `mod:${c.id}` : "official";
  },
  saved: () => reload(),
  still: (id) => {
    const v = variant();
    return thumb(spineBase("mod", id), "landscape", null, true, specialFraming(v), v.bg);
  },
  album: () =>
    new Promise((resolve) => {
      albumClosed?.();
      albumClosed = resolve;
      const ended = () => {
        if ($("popup").hidden && albumClosed === resolve) {
          albumClosed = null;
          resolve();
        }
      };
      openPopup("lobby").then(ended, ended);
    }),
};
// the UI slides out (app.css .stage.fullscreen) and out of the keyboard's reach; back, the visual mode closes and the
// focus returns (visual-mode.js uiOut, uiBack)
export const slidOut = () => [
  $("ngui"),
  $("ngui-chips"),
  $("ngui-expr"),
  document.querySelector(".topbar"),
  stage().querySelector(".nav"),
];
export function setStage(on) {
  setFullscreenState(on);
  stage().classList.toggle("fullscreen", on);
  if (on) Visual.uiOut(slidOut());
  else
    Visual.uiBack(slidOut(), {
      refocus: open,
      fallback: () =>
        [...$("ngui").querySelectorAll("button")].find(
          (button) => button.getAttribute("aria-label") === T("lobby.fullscreen"),
        ),
    });
}
export function showExit(on) {
  $("fs-exit").hidden = !on;
  if (on) {
    wake();
    stage().addEventListener("pointermove", wake);
  } else {
    stage().removeEventListener("pointermove", wake);
    clearTimeout(idleTimer);
  }
}
// the visual mode: its background and buttons are made off screen first, then the UI slides out as the portrait
// glides into the whole art and they come in. Without the prefab (another game version) the portrait still goes
// fullscreen, with the tool's exit button.
export let visualToken = 0;
async function enterVisual({ switching = false } = {}) {
  if ((fullscreen && !switching) || !mainPlayer?.skeleton) return; // the portrait is not in yet
  const my = ++visualToken,
    key = variantKey,
    request = fullscreenRequest;
  const alive = () =>
    my === visualToken &&
    request === fullscreenRequest &&
    open &&
    fullscreen === switching &&
    tab === "portrait" &&
    variantKey === key &&
    !!mainPlayer?.skeleton;
  const layout = await (layouts.visual ||= NGUI.layout("UICharacterVisualMode").catch(() => null));
  const strings = {
    reset: T("visual.reset"),
    back: T("lobby.exit"),
    switch: previewAvailable(variant(), "lobby") ? T("row.switch.lobby") : null,
  };
  const parts = await Visual.prepare({ layout, bg: variant().bg, strings }).catch((err) => {
    console.error(err);
    return null;
  });
  if (!alive()) return;
  if (
    !(await Visual.open({
      instant: switching,
      parts,
      skeleton: mainPlayer.skeleton,
      detail: detailViewport(),
      host: stage(),
      bgHost: $("visual-bg"),
      uiHost: $("visual-ui"),
      players: () => [...$("portrait").querySelectorAll(".layer")].map((l) => l.player).filter(Boolean),
      onBack: () => setFullscreen(false),
      onSwitch: switchFullscreenView,
      alive,
    }))
  )
    return;
  if (!alive()) return;
  setStage(true);
  await Photo.visualEntry(true);
  showExit(!parts);
}

// Reuse the detail preview loaders and source selection while keeping the stage fullscreen. These paths only
// read previews; the selected skin, expression, mod toggle and configured sources remain owned by the detail page.
let fullscreenSwitching = false;
async function switchFullscreenView() {
  if (
    !fullscreen ||
    fullscreenSwitching ||
    busy ||
    Loading.held() ||
    photoSession ||
    !["portrait", "lobby"].includes(tab)
  )
    return;
  const target = tab === "portrait" ? "lobby" : "portrait",
    key = variantKey;
  if (!previewAvailable(variant(), switchKindOf(target))) return;
  const request = ++fullscreenRequest;
  const alive = () => request === fullscreenRequest && open && fullscreen && variantKey === key;
  fullscreenSwitching = true;
  try {
    const ready = await changeInPlace(async () => {
      if (!alive()) return;
      // Retire the old renderer without writing the detail viewport during an immersive handoff.
      Visual.close({ instant: true, restore: false });
      $("visual-ui").hidden = true;
      $("visual-ui").replaceChildren();
      showExit(false);
      setTab(target);
      await Promise.all([renderAll(), renderLeft()]);
      if (alive()) await setFullscreen(true, { switching: true });
    });
    if (ready && open && fullscreen && variantKey === key && tab === target) {
      [...$("visual-ui").querySelectorAll("button")]
        .find((button) => button.dataset.visualAction === "Btn_HideUI")
        ?.focus({ preventScroll: true });
    }
  } finally {
    fullscreenSwitching = false;
  }
}
// the switch card's still is taken in the left framing: when fullscreen was entered before the lobby or the model was
// in, the still is taken once the left framing is back
async function stillAfterFullscreen() {
  const part = tab,
    key = variantKey;
  const wanted = () =>
    open &&
    !fullscreen &&
    tab === part &&
    variantKey === key &&
    (part === "lobby"
      ? !lobbyShot && !!Lobby.meta()
      : part === "model" && !modelShot && !!$("model").querySelector(".model-view.ready"));
  if (!wanted()) return;
  // the lobby draws the left view within a few frames (after its intro); the model glides there
  const M = part === "model" ? await ModelLib() : null;
  if (!M) await Lobby.introDone();
  await (M ? M.settled() : frames(4));
  if (!wanted()) return;
  if (M) setModelShot(M.snapshot());
  else setLobbyShot(snapshotLobby());
  if (M ? modelShot : lobbyShot) renderScreen();
}

// Setters of state of detail-fullscreen.js for the other detail modules: an imported binding is read-only.
export function setAlbumClosed(value) {
  albumClosed = value;
}
export function setVisualToken(value) {
  visualToken = value;
}
