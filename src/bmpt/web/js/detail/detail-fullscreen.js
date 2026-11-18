// The detail screen's fullscreen and visual modes: the stage, the exit button, the camera bridge, and switching the
// fullscreen view.
import * as ThumbnailCapture from "../thumbnail-capture/controller.js";
import * as Camera from "../memorial-lobby/memorial-lobby-camera.js";
import { specialFraming } from "./special-view.js";
import * as NGUI from "../ui/ngui.js";
import * as Loading from "../app/loading.js";
import * as MemorialLobby from "../memorial-lobby/memorial-lobby.js";
import * as Visual from "./visual-mode.js";
import { spineBase } from "../core/api.js";
import { T } from "../core/i18n.js";
import { switchKindOf } from "../mods/switch-sources.js";
import { $ } from "../core/util.js";
import {
  stage,
  altId,
  layouts,
  tab,
  fullscreen,
  mainPlayer,
  memorialLobbyShot,
  chibiShot,
  chibiLib,
  ChibiLib,
  frames,
  open,
  thumb,
  alt,
  previewAvailable,
  current,
  renderAll,
  changeInPlace,
  renderLeft,
  reload,
  setMemorialLobbyShot,
  setChibiShot,
  setFullscreenState,
  setTab,
} from "./detail.js";
import { busy } from "./detail-actions.js";
import { renderScreen } from "./detail-screen.js";
import { detailViewport } from "./detail-portrait.js";
import { openPopup } from "./detail-switchers.js";
import { snapshotMemorialLobby } from "./detail-memorial-lobby-tab.js";
import { thumbnailSession, thumbnailBridge } from "./detail-thumbnail.js";

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
    key = altId;
  if (on && tab === "portrait") {
    return enterVisual({ switching });
  }
  if (on) {
    const layout = await (layouts.visual ||= NGUI.layout("UICharacterVisualMode").catch(() => null));
    if (layout) await Visual.exitControl($("fs-exit"), layout);
    const control =
      tab === "memorialLobby" && previewAvailable(alt(), "portrait")
        ? await Visual.prepareSwitch({
            layout,
            label: T("row.switch.portrait"),
            onSwitch: switchFullscreenView,
          })
        : null;
    if (request !== fullscreenRequest || !open || tab !== part || altId !== key) return;
    $("visual-ui").replaceChildren(...(control ? [control] : []));
    $("visual-ui").hidden = !control;
  } else if (part !== "portrait") {
    $("visual-ui").hidden = true;
    $("visual-ui").replaceChildren();
  }
  setStage(on);
  if (!on || tab === "memorialLobby") {
    MemorialLobby.frame(on ? "full" : "left");
    MemorialLobby.interactive(on);
  }
  if (!on || tab === "chibi") chibiLib?.then((M) => M.frame(on ? "full" : "left"));
  showExit(on);
  if (!on) Camera.close(false);
  await ThumbnailCapture.visualEntry(on);
  await Camera.entry(on && tab === "memorialLobby", cameraBridge);
  if (!on) {
    await thumbnailBridge.release();
    stillAfterFullscreen();
  }
}
// the camera framing's view of the detail page (memorial-lobby-camera.js)
// source: what the memorial lobby shows (the camera's art); saved: the student's memorial lobby choices read again with
// the new one; still: a saved camera's still, as its Switch Memorial Lobby card draws it; album: Switch Memorial Lobby
// itself, resolved once it closed and what was chosen there is shown
export let albumClosed = null;
const cameraBridge = {
  alt: () => altId,
  memorialLobby: () => alt()?.memorialLobby,
  source: () => {
    const c = current("memorialLobby");
    return c?.kind === "mod" ? `mod:${c.id}` : "official";
  },
  saved: () => reload(),
  still: (id) => {
    const v = alt();
    return thumb(spineBase("mod", id), "landscape", null, true, specialFraming(v), v.background);
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
      openPopup("memorialLobby").then(ended, ended);
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
          (button) => button.getAttribute("aria-label") === T("memorialLobby.fullscreen"),
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
    key = altId,
    request = fullscreenRequest;
  const alive = () =>
    my === visualToken &&
    request === fullscreenRequest &&
    open &&
    fullscreen === switching &&
    tab === "portrait" &&
    altId === key &&
    !!mainPlayer?.skeleton;
  const layout = await (layouts.visual ||= NGUI.layout("UICharacterVisualMode").catch(() => null));
  const strings = {
    reset: T("visual.reset"),
    back: T("memorialLobby.exit"),
    switch: previewAvailable(alt(), "memorialLobby") ? T("row.switch.memorialLobby") : null,
  };
  const parts = await Visual.prepare({ layout, bg: alt().background, strings }).catch((err) => {
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
  await ThumbnailCapture.visualEntry(true);
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
    thumbnailSession ||
    !["portrait", "memorialLobby"].includes(tab)
  )
    return;
  const target = tab === "portrait" ? "memorialLobby" : "portrait",
    key = altId;
  if (!previewAvailable(alt(), switchKindOf(target))) return;
  const request = ++fullscreenRequest;
  const alive = () => request === fullscreenRequest && open && fullscreen && altId === key;
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
    if (ready && open && fullscreen && altId === key && tab === target) {
      [...$("visual-ui").querySelectorAll("button")]
        .find((button) => button.dataset.visualAction === "Btn_HideUI")
        ?.focus({ preventScroll: true });
    }
  } finally {
    fullscreenSwitching = false;
  }
}
// the switch card's still is taken in the left framing: when fullscreen was entered before the memorial lobby or the
// chibi was in, the still is taken once the left framing is back
async function stillAfterFullscreen() {
  const part = tab,
    key = altId;
  const wanted = () =>
    open &&
    !fullscreen &&
    tab === part &&
    altId === key &&
    (part === "memorialLobby"
      ? !memorialLobbyShot && !!MemorialLobby.meta()
      : part === "chibi" && !chibiShot && !!$("chibi").querySelector(".chibi-view.ready"));
  if (!wanted()) return;
  // the memorial lobby draws the left view within a few frames (after its intro); the chibi glides there
  const M = part === "chibi" ? await ChibiLib() : null;
  if (!M) await MemorialLobby.introDone();
  await (M ? M.settled() : frames(4));
  if (!wanted()) return;
  if (M) setChibiShot(M.snapshot());
  else setMemorialLobbyShot(snapshotMemorialLobby());
  if (M ? chibiShot : memorialLobbyShot) renderScreen();
}

// Setters of state of detail-fullscreen.js for the other detail modules: an imported binding is read-only.
export function setAlbumClosed(value) {
  albumClosed = value;
}
export function setVisualToken(value) {
  visualToken = value;
}
