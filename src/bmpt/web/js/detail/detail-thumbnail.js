// Thumbnail capture on the detail screen: preloading the shown art and the bridge the thumbnail capture controller
// drives.
import * as ThumbnailCapture from "../thumbnail-capture/controller.js";
import { specialFraming } from "./special-view.js";
import * as NGUI from "../ui/ngui.js";
import { nextFrame } from "../core/async.js";
import * as MemorialLobby from "../memorial-lobby/memorial-lobby.js";
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
  setMemorialLobbyFrom,
  setMemorialLobbyStale,
  setFullscreenState,
  setTab,
  setShownLeft,
  setShownKey,
} from "./detail.js";
import { stripCard } from "./detail-strip.js";
import { loadSpine, detailViewport, applyExpression, disposePortrait } from "./detail-portrait.js";
import { modsOn } from "./detail-switchers.js";
import { setFullscreen, slidOut, setStage, showExit } from "./detail-fullscreen.js";

// Thumbnail capture retains both real renderers during in-place camera mode changes.
export let thumbnailLastAction = "",
  thumbnailPrepared = null,
  thumbnailSession = false,
  thumbnailOrigin = null;
export const thumbnailFaces = new Map();
async function thumbnailPreload() {
  if (thumbnailPrepared) return thumbnailPrepared;
  thumbnailOrigin = altId;
  thumbnailSession = true;
  thumbnailPrepared = (async () => {
    const v = alt(),
      portraitAvailable = previewAvailable(v, "portrait"),
      memorialLobbyAvailable = previewAvailable(v, "memorialLobby"),
      portrait = $("portrait"),
      memorialLobby = $("memorial-lobby");
    if (!mainPlayer) {
      portrait.hidden = false;
      portrait.classList.add("thumbnail-preloading");
    }
    if (tab !== "memorialLobby") {
      memorialLobby.hidden = false;
      memorialLobby.classList.add("thumbnail-preloading");
    }
    await Promise.all([
      (async () => {
        if (portraitAvailable && !mainPlayer) await loadSpine(currentBase());
        if (portraitAvailable && !mainPlayer?.skeleton) throw new Error("Portrait renderer is not ready");
      })(),
      (async () => {
        if (!memorialLobbyAvailable) return;
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
        if (!(await MemorialLobby.show(v.memorialLobby, memorialLobby, v.key, options)))
          throw new Error("Memorial renderer is not ready");
        setMemorialLobbyFrom(shownStudent.version);
        setMemorialLobbyStale(false);
        MemorialLobby.suspend();
        MemorialLobby.frame("full");
        MemorialLobby.thumbnailCapture(ThumbnailCapture.FRAME);
        MemorialLobby.skipIntro();
        await MemorialLobby.prepareFrame();
      })(),
    ]);
    if (thumbnailOrigin !== altId || !open) throw new DOMException("Thumbnail capture source changed", "AbortError");
    if (portraitAvailable && !Visual.isOpen()) {
      const layout = await (layouts.visual ||= NGUI.layout("UICharacterVisualMode"));
      const parts = await Visual.prepare({
        layout,
        bg: v.background,
        strings: { reset: T("visual.reset"), back: T("memorialLobby.exit") },
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
        alive: () => open && thumbnailOrigin === altId,
      });
    }
    if (portraitAvailable) {
      Visual.thumbnailCapture(true, ThumbnailCapture.FRAME);
      Visual.captureReady();
      mainPlayer.drawFrame?.(false);
    }
  })().catch((error) => {
    thumbnailPrepared = null;
    throw error;
  });
  return thumbnailPrepared;
}
function thumbnailPortraitClock(on) {
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
function thumbnailVisibility(mode) {
  const portrait = mode === "portrait";
  for (const id of ["portrait", "visual-bg", "visual-ui"]) $(id).style.visibility = portrait ? "visible" : "hidden";
  $("portrait").hidden = !portrait;
  $("memorial-lobby").hidden = portrait;
  $("memorial-lobby").style.visibility = portrait ? "hidden" : "visible";
  for (const id of ["portrait", "memorial-lobby"]) $(id).classList.remove("thumbnail-preloading", "pending", "leaving");
  document.documentElement.classList.toggle("thumbnail-memorial-lobby", !portrait);
}
export const thumbnailBridge = {
  info: () => ({
    alt: altId,
    mode: tab,
    source: tab === "portrait" ? currentBase() : shownSource(alt(), "memorialLobby"),
    hasPortrait: previewAvailable(alt(), "portrait"),
    hasMemorialLobby: previewAvailable(alt(), "memorialLobby"),
  }),
  async full(mode) {
    await thumbnailPreload();
    if (mode === "portrait") {
      Visual.thumbnailCapture(true, ThumbnailCapture.FRAME);
      mainPlayer.paused = false;
      mainPlayer.drawFrame?.(false);
    } else await MemorialLobby.prepareFrame();
    await nextFrame();
    setTab(mode);
    setFullscreenState(true);
    stage().classList.add("fullscreen");
    Visual.uiOut(slidOut());
    if (mode === "portrait") {
      MemorialLobby.suspend();
      thumbnailVisibility(mode);
      thumbnailPortraitClock(true);
    } else {
      thumbnailPortraitClock(false);
      thumbnailVisibility(mode);
      MemorialLobby.resume();
      MemorialLobby.interactive(false);
    }
  },
  async leave() {
    MemorialLobby.suspend();
    setShownLeft(tab);
    setShownKey(leftKey(tab, alt()));
    await setFullscreen(false);
    await renderAll();
    await settleImages();
    if (tab === "memorialLobby") await MemorialLobby.prepareFrame();
    await nextFrame();
  },
  async release() {
    if (!thumbnailSession) return;
    thumbnailSession = false;
    thumbnailPrepared = null;
    thumbnailOrigin = null;
    Visual.close({ instant: true });
    MemorialLobby.thumbnailCapture(null);
    for (const id of ["portrait", "memorial-lobby", "visual-bg", "visual-ui"]) {
      $(id).style.visibility = "";
      $(id).classList.remove("thumbnail-preloading");
    }
    if (tab === "portrait") {
      MemorialLobby.hide($("memorial-lobby"));
      $("memorial-lobby").hidden = true;
      thumbnailPortraitClock(true);
    } else {
      disposePortrait();
      $("portrait").hidden = true;
      MemorialLobby.resume();
    }
    thumbnailFaces.clear();
    document.documentElement.classList.remove("thumbnail-memorial-lobby");
  },
  cancel() {
    const owned = thumbnailSession;
    void thumbnailBridge.release();
    if (owned && fullscreen) {
      setStage(false);
      showExit(false);
      MemorialLobby.frame("left");
      MemorialLobby.interactive(false);
    }
  },
  generatedThumbnail: async () =>
    alt().portrait ? await thumbnailWork.thumb(currentBase(), "studentList") : alt().officialThumbnail,
  defaultThumbnail: async () =>
    alt().officialThumbnail ||
    (alt().portrait ? await thumb(spineBase("official-portrait", alt().portrait), "studentList") : null),
  thumbnailChanged() {
    ctx?.invalidateStudentList();
  },
  expressions: () =>
    (mainPlayer?.skeleton?.data.animations || []).map((a) => a.name).filter((name) => /^\d{2}$/.test(name)),
  expressionPreview(key) {
    if (!thumbnailFaces.has(key)) thumbnailFaces.set(key, thumbnailWork.thumb(currentBase(), "face", key));
    return thumbnailFaces.get(key);
  },
  async prepareExpressions() {
    await Promise.all(thumbnailBridge.expressions().map((key) => thumbnailBridge.expressionPreview(key)));
  },
  expressionCard(key, preview) {
    const tree = stripCard(`Expression${key}`, 0, { thumb: preview, selected: key === expression });
    tree.pos = [0, 0];
    return tree;
  },
  expression: () => expression,
  action: () => MemorialLobby.playing() || thumbnailLastAction,
  setExpression(value) {
    setExpressionState(value);
    applyExpression();
  },
  actions: () =>
    [
      ...(MemorialLobby.meta()?.sequences || []),
      ...[...new Set(Object.values(MemorialLobby.meta()?.idles || {}).flat())].map((anim) => ({ anim })),
    ].filter((item, index, list) => list.findIndex((other) => other.anim === item.anim) === index),
  play(value) {
    thumbnailLastAction = value;
    MemorialLobby.resume();
    if ((MemorialLobby.meta()?.sequences || []).some((seq) => seq.anim === value)) MemorialLobby.play(value);
    else {
      MemorialLobby.skipIntro();
      MemorialLobby.stop();
      for (const p of MemorialLobby.layerPlayers())
        if (p.skeleton?.data.findAnimation(value)) p.animationState.setAnimation(0, value, true);
    }
  },
  pause(on) {
    if (tab === "memorialLobby") {
      if (on) MemorialLobby.suspend();
      else {
        MemorialLobby.resume();
        MemorialLobby.interactive(false);
      }
    } else if (mainPlayer) mainPlayer.paused = on;
  },
  async redraw() {
    if (tab === "memorialLobby") {
      if (await MemorialLobby.prepareFrame()) return;
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
    else MemorialLobby.panBy(dx, dy);
  },
  zoom(f, x, y) {
    if (tab === "portrait") Visual.zoomCapture(f, x, y);
    else MemorialLobby.zoomAt(f, x, y);
  },
  paintRoots: () => (tab === "portrait" ? [$("visual-bg"), $("portrait")] : [$("memorial-lobby")]),
};
