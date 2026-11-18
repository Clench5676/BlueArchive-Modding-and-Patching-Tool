// Student photography on the detail screen: preloading the shown art and the bridge the photography controller
// drives.
import * as Photo from "../photography/controller.js";
import { specialFraming } from "./special-view.js";
import * as NGUI from "../ui/ngui.js";
import { nextFrame } from "../core/async.js";
import * as Lobby from "../lobby/lobby.js";
import * as Visual from "./visual-mode.js";
import { spineBase } from "../core/api.js";
import { T } from "../core/i18n.js";
import { $ } from "../core/util.js";
import {
  stage,
  altId,
  shownStudent,
  layouts,
  ctx,
  tab,
  fullscreen,
  expression,
  mainPlayer,
  open,
  thumbnailWork,
  thumb,
  settleImages,
  alt,
  currentBase,
  shownSource,
  previewAvailable,
  current,
  renderAll,
  leftKey,
  setExpressionState,
  setLobbyFrom,
  setLobbyStale,
  setFullscreenState,
  setTab,
  setShownLeft,
  setShownKey,
} from "./detail.js";
import { stripCard } from "./detail-strip.js";
import { loadSpine, detailViewport, applyExpression, disposePortrait } from "./detail-portrait.js";
import { modsOn } from "./detail-switchers.js";
import { setFullscreen, slidOut, setStage, showExit } from "./detail-fullscreen.js";

// Student photography retains both real renderers during in-place camera mode changes.
export let photoLastAction = "",
  photoPrepared = null,
  photoSession = false,
  photoOrigin = null;
export const photoFaces = new Map();
async function photoPreload() {
  if (photoPrepared) return photoPrepared;
  photoOrigin = altId;
  photoSession = true;
  photoPrepared = (async () => {
    const v = alt(),
      portraitAvailable = previewAvailable(v, "portrait"),
      lobbyAvailable = previewAvailable(v, "memorialLobby"),
      portrait = $("portrait"),
      lobby = $("lobby");
    if (!mainPlayer) {
      portrait.hidden = false;
      portrait.classList.add("photo-preloading");
    }
    if (tab !== "lobby") {
      lobby.hidden = false;
      lobby.classList.add("photo-preloading");
    }
    await Promise.all([
      (async () => {
        if (portraitAvailable && !mainPlayer) await loadSpine(currentBase());
        if (portraitAvailable && !mainPlayer?.skeleton) throw new Error("Portrait renderer is not ready");
      })(),
      (async () => {
        if (!lobbyAvailable) return;
        const c = current("memorialLobby"),
          options = {
            framing: specialFraming(v),
            deferPlayback: true,
            mod:
              c.kind === "mod" && (!!v.previewSources?.memorialLobby || !!v.configured || !modsOn() || !v.memorialLobby)
                ? c.id
                : null,
            camera: !!c.mod?.camera,
            official: c.kind === "self" && (!!v.configured || !!v.previewSources?.memorialLobby || !modsOn()),
          };
        if (!(await Lobby.show(v.memorialLobby, lobby, v.key, options)))
          throw new Error("Memorial renderer is not ready");
        setLobbyFrom(shownStudent.version);
        setLobbyStale(false);
        Lobby.suspend();
        Lobby.frame("full");
        Lobby.photography(Photo.FRAME);
        Lobby.skipIntro();
        await Lobby.prepareFrame();
      })(),
    ]);
    if (photoOrigin !== altId || !open) throw new DOMException("Photography source changed", "AbortError");
    if (portraitAvailable && !Visual.isOpen()) {
      const layout = await (layouts.visual ||= NGUI.layout("UICharacterVisualMode"));
      const parts = await Visual.prepare({
        layout,
        bg: v.background,
        strings: { reset: T("visual.reset"), back: T("lobby.exit") },
      });
      await Visual.open({
        parts,
        skeleton: mainPlayer.skeleton,
        detail: detailViewport(),
        host: stage(),
        bgHost: $("visual-bg"),
        uiHost: $("visual-ui"),
        players: () => [...portrait.querySelectorAll(".layer")].map((layer) => layer.player).filter(Boolean),
        onBack: () => setFullscreen(false),
        alive: () => open && photoOrigin === altId,
      });
    }
    if (portraitAvailable) {
      Visual.photography(true, Photo.FRAME);
      Visual.captureReady();
      mainPlayer.drawFrame?.(false);
    }
  })().catch((error) => {
    photoPrepared = null;
    throw error;
  });
  return photoPrepared;
}
function photoPortraitClock(on) {
  for (const layer of $("portrait").querySelectorAll(".layer")) {
    const p = layer.player;
    if (!p) continue;
    p.paused = !on;
    if (on) {
      p.drawFrame?.(false);
      if (p.stopRequestAnimationFrame) p.startRendering?.();
    } else p.stopRendering?.();
  }
}
function photoVisibility(mode) {
  const portrait = mode === "portrait";
  for (const id of ["portrait", "visual-bg", "visual-ui"]) $(id).style.visibility = portrait ? "visible" : "hidden";
  $("portrait").hidden = !portrait;
  $("lobby").hidden = portrait;
  $("lobby").style.visibility = portrait ? "hidden" : "visible";
  for (const id of ["portrait", "lobby"]) $(id).classList.remove("photo-preloading", "pending", "leaving");
  document.documentElement.classList.toggle("photo-lobby", !portrait);
}
export const photoBridge = {
  info: () => ({
    alt: altId,
    mode: tab,
    source: tab === "portrait" ? currentBase() : shownSource(alt(), "memorialLobby"),
    hasPortrait: previewAvailable(alt(), "portrait"),
    hasLobby: previewAvailable(alt(), "memorialLobby"),
  }),
  async full(mode) {
    await photoPreload();
    if (mode === "portrait") {
      Visual.photography(true, Photo.FRAME);
      mainPlayer.paused = false;
      mainPlayer.drawFrame?.(false);
    } else await Lobby.prepareFrame();
    await nextFrame();
    setTab(mode);
    setFullscreenState(true);
    stage().classList.add("fullscreen");
    Visual.uiOut(slidOut());
    if (mode === "portrait") {
      Lobby.suspend();
      photoVisibility(mode);
      photoPortraitClock(true);
    } else {
      photoPortraitClock(false);
      photoVisibility(mode);
      Lobby.resume();
      Lobby.interactive(false);
    }
  },
  async leave() {
    Lobby.suspend();
    setShownLeft(tab);
    setShownKey(leftKey(tab, alt()));
    await setFullscreen(false);
    await renderAll();
    await settleImages();
    if (tab === "lobby") await Lobby.prepareFrame();
    await nextFrame();
  },
  async release() {
    if (!photoSession) return;
    photoSession = false;
    photoPrepared = null;
    photoOrigin = null;
    Visual.close({ instant: true });
    Lobby.photography(null);
    for (const id of ["portrait", "lobby", "visual-bg", "visual-ui"]) {
      $(id).style.visibility = "";
      $(id).classList.remove("photo-preloading");
    }
    if (tab === "portrait") {
      Lobby.hide($("lobby"));
      $("lobby").hidden = true;
      photoPortraitClock(true);
    } else {
      disposePortrait();
      $("portrait").hidden = true;
      Lobby.resume();
    }
    photoFaces.clear();
    document.documentElement.classList.remove("photo-lobby");
  },
  cancel() {
    const owned = photoSession;
    void photoBridge.release();
    if (owned && fullscreen) {
      setStage(false);
      showExit(false);
      Lobby.frame("left");
      Lobby.interactive(false);
    }
  },
  generatedThumbnail: async () =>
    alt().portrait ? await thumbnailWork.thumb(currentBase(), "roster") : alt().officialThumbnail,
  defaultThumbnail: async () =>
    alt().officialThumbnail ||
    (alt().portrait ? await thumb(spineBase("official-portrait", alt().portrait), "roster") : null),
  thumbnailChanged() {
    ctx?.invalidateRoster();
  },
  expressions: () =>
    (mainPlayer?.skeleton?.data.animations || []).map((a) => a.name).filter((name) => /^\d{2}$/.test(name)),
  expressionPreview(key) {
    if (!photoFaces.has(key)) photoFaces.set(key, thumbnailWork.thumb(currentBase(), "face", key));
    return photoFaces.get(key);
  },
  async prepareExpressions() {
    await Promise.all(photoBridge.expressions().map((key) => photoBridge.expressionPreview(key)));
  },
  expressionCard(key, preview) {
    const tree = stripCard(`Expression${key}`, 0, { thumb: preview, selected: key === expression });
    tree.pos = [0, 0];
    return tree;
  },
  expression: () => expression,
  action: () => Lobby.playing() || photoLastAction,
  setExpression(value) {
    setExpressionState(value);
    applyExpression();
  },
  actions: () =>
    [
      ...(Lobby.meta()?.sequences || []),
      ...[...new Set(Object.values(Lobby.meta()?.idles || {}).flat())].map((anim) => ({ anim })),
    ].filter((item, index, list) => list.findIndex((other) => other.anim === item.anim) === index),
  play(value) {
    photoLastAction = value;
    Lobby.resume();
    if ((Lobby.meta()?.sequences || []).some((seq) => seq.anim === value)) Lobby.play(value);
    else {
      Lobby.skipIntro();
      Lobby.stop();
      for (const p of Lobby.layerPlayers())
        if (p.skeleton?.data.findAnimation(value)) p.animationState.setAnimation(0, value, true);
    }
  },
  pause(on) {
    if (tab === "lobby") {
      if (on) Lobby.suspend();
      else {
        Lobby.resume();
        Lobby.interactive(false);
      }
    } else if (mainPlayer) mainPlayer.paused = on;
  },
  async redraw() {
    if (tab === "lobby") {
      if (await Lobby.prepareFrame()) return;
    } else if (mainPlayer?.skeleton) {
      if (mainPlayer.error) throw new Error("Portrait renderer failed");
      if (!mainPlayer.disposed) {
        mainPlayer.paused = true;
        mainPlayer.drawFrame(false);
        return;
      }
    }
    throw Object.assign(new Error("Renderer is not ready"), { code: "capture_not_ready" });
  },
  pan(dx, dy) {
    if (tab === "portrait") Visual.panCapture(dx, dy);
    else Lobby.panBy(dx, dy);
  },
  zoom(f, x, y) {
    if (tab === "portrait") Visual.zoomCapture(f, x, y);
    else Lobby.zoomAt(f, x, y);
  },
  paintRoots: () => (tab === "portrait" ? [$("visual-bg"), $("portrait")] : [$("lobby")]),
};
