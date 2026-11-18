// Memorial lobby for the detail screen's second tab and the Student Hall, framed and played like the game. The
// memorial lobby's UILobbyElement prefab (memorial-lobby.json, read by the server) places every skeleton in NGUI units around
// the screen centre; at 16:9 the game shows the 2960 x 1665 units G around it. Fullscreen shows exactly G; the left
// view puts the head-pat point (Touch_Point) left of the panel and stays inside the painted rect P, the largest
// rectangle around G that the memorial lobby's opaque art covers, so neither view shows a blank edge; switching between
// them crossfades. Dialogues play the talk clips with the game's own tracks and mixes, their voices and one subtitle
// line per voice cue (lines and voices come from Kivo, setLines).
//
// createMemorialLobby() builds one memorial lobby instance: its state S (the memorial lobby shown, its players,
// dialogue, timers and caches) is created per call, and its functions L come from the parts below, each of which takes
// S and L and keeps no state of its own: memorial-lobby-scene.js (timers, touch ownership, suspension, the first frame,
// the intro's controls), memorial-lobby-view.js (views, zoom, camera, edge extension), memorial-lobby-geometry.js
// (painted rect and limits), memorial-lobby-players.js (show, hide), memorial-lobby-clips.js (clips, random idle clips)
// and memorial-lobby-talk.js (dialogues, sounds). This module's own exports are a default instance, which the detail
// screen uses; the Student Hall makes one instance per slot. The touch interaction and the wheel zoom are in
// memorial-lobby-touch.js, the drawing in memorial-lobby-stage.js, the intro in memorial-lobby-intro.js.
import { latestLoad } from "../spine/spine-load.js";
import { layersOf, pairAtlases } from "./memorial-lobby-layers.js";
import { scenePart } from "./memorial-lobby-scene.js";
import { STAGE, viewPart } from "./memorial-lobby-view.js";
import { geometryPart } from "./memorial-lobby-geometry.js";
import { playersPart } from "./memorial-lobby-players.js";
import { clipsPart, createIdle } from "./memorial-lobby-clips.js";
import { createSfx, talkPart } from "./memorial-lobby-talk.js";
import {
  ZOOM_CUSTOM,
  rectCache,
  idleClips,
  sceneBox,
  around,
  clipPoses,
  shrinkRect,
  framingLimits,
  FRAMING_CELL,
  framingFits,
  framingView,
  cameraParams,
  layerViewport,
  ZOOM_MODES,
  zoomBounds,
} from "./memorial-lobby-measure.js";
// The layer reading and the measuring helpers stay part of the memorial lobby's API (MemorialLobby.framingView and the
// like).
export { layersOf, pairAtlases } from "./memorial-lobby-layers.js";
export {
  idleClips,
  sceneBox,
  around,
  clipPoses,
  shrinkRect,
  framingLimits,
  FRAMING_CELL,
  framingFits,
  framingView,
  cameraParams,
  layerViewport,
  ZOOM_MODES,
  zoomBounds,
} from "./memorial-lobby-measure.js";

/**
 * A view in NGUI units: a 16:9 window onto the memorial lobby.
 * @typedef {{x: number, y: number, w: number, h: number}} ViewRect
 */

/**
 * A rectangle of cells of the painted-rect grid, edges inclusive.
 * @typedef {{i0: number, i1: number, j0: number, j1: number}} CellRect
 */

// Everything of one instance that changes, caches included: two instances never share any of it.
function createState() {
  return {
    players: [],
    current: null,
    mode: "left",
    extendTimer: null,
    chain: null,
    token: 0,
    wantTouch: false,
    zoomMode: "fullArt",
    zoomCustom: ZOOM_CUSTOM, // the zoom-out setting (setZoomLimit)
    talk: null,
    talkIndex: -1,
    cueTimers: [],
    audios: [],
    lines: {},
    muted: false,
    volume: 0.8,
    lineOf: (l) => l?.["zh-CN"] || "",
    introOn: true, // the intro setting (setIntro): off shows the memorial lobby as its intro leaves it
    listeners: new Set(),
    ownsTouch: false,
    touchToken: {}, // this instance's claim in the shared touch-owner registry (memorial-lobby-scene.js)
    dialogueFullscreen: null,
    dialogueViewBounds: { left: 0, top: 0, right: STAGE.w, bottom: STAGE.h },
    sceneTimers: new Map(),
    paintedRects: rectCache(),
    safeRects: rectCache(2 * 1024 * 1024), // the same source and geometry identity as the initial painted rect
    cameraOn: false,
    framingCache: new Map(), // geometry key -> measured camera limits, for the session
    thumbnailFrame: null,
    memorialLobbyLoads: latestLoad(),
    sourceLoads: latestLoad(),
    playerLoads: new WeakMap(),
    fixedDt: null, // the checks' clock (setClock)
    stepDt: new Map(),
    Idle: null,
    Sfx: null,
  };
}

// One instance's state S and functions L. The parts reach each other's functions through L only, so each can be
// read (and replaced, in tests) on its own.
function createContext() {
  const S = createState(),
    L = {};
  Object.assign(
    L,
    scenePart(S, L),
    viewPart(S, L),
    geometryPart(S, L),
    playersPart(S, L),
    clipsPart(S, L),
    talkPart(S, L),
  );
  S.Idle = createIdle(S, L);
  S.Sfx = createSfx(S, L);
  return { S, L };
}

/**
 * Make a memorial lobby instance with its own state: the memorial lobby it shows, its players, dialogue, timers,
 * settings and caches. Its members are this module's exports, with the same names and behaviour.
 * @returns {object} The instance's API.
 */
export function createMemorialLobby() {
  const { L } = createContext();
  return {
    advance: L.advance,
    camera: L.camera,
    cameraState: L.cameraState,
    clipRuns: L.clipRuns,
    effects: L.effects,
    frame: L.frame,
    hide: L.hide,
    idleOf: L.idleOf,
    interactive: L.interactive,
    introDone: L.introDone,
    introPending: L.introPending,
    introState: L.introState,
    layerPlayers: L.layerPlayers,
    limits: L.limits,
    meta: L.meta,
    next: L.next,
    onTalk: L.onTalk,
    panBy: L.panBy,
    thumbnailCapture: L.thumbnailCapture,
    play: L.play,
    playAll: L.playAll,
    playing: L.playing,
    playingAll: L.playingAll,
    prepareFrame: L.prepareFrame,
    resetZoom: L.resetZoom,
    resume: L.resume,
    reveal: L.reveal,
    setClock: L.setClock,
    setDialogueBounds: L.setDialogueBounds,
    setDialogueFullscreen: L.setDialogueFullscreen,
    setIntro: L.setIntro,
    setLines: L.setLines,
    setPostProcessing: L.setPostProcessing,
    setTrace: L.setTrace,
    setVolume: L.setVolume,
    setZoomLimit: L.setZoomLimit,
    show: L.show,
    skipIntro: L.skipIntro,
    soundLog: L.soundLog,
    stillRect: L.stillRect,
    stop: L.stop,
    suspend: L.suspend,
    textOf: L.textOf,
    unavailable: L.unavailable,
    zoomAt: L.zoomAt,
    // stateless: the same functions in every instance
    layersOf,
    pairAtlases,
    idleClips,
    sceneBox,
    around,
    clipPoses,
    shrinkRect,
    framingLimits,
    FRAMING_CELL,
    framingFits,
    framingView,
    cameraParams,
    layerViewport,
    ZOOM_MODES,
    zoomBounds,
  };
}

// The default instance, shown by the detail screen. Its members are plain constants: no caller relies on a live
// binding, and none may.
const memorialLobby = createMemorialLobby();
export const {
  advance,
  camera,
  cameraState,
  clipRuns,
  effects,
  frame,
  hide,
  idleOf,
  interactive,
  introDone,
  introPending,
  introState,
  layerPlayers,
  limits,
  meta,
  next,
  onTalk,
  panBy,
  thumbnailCapture,
  play,
  playAll,
  playing,
  playingAll,
  prepareFrame,
  resetZoom,
  resume,
  reveal,
  setClock,
  setDialogueBounds,
  setDialogueFullscreen,
  setIntro,
  setLines,
  setPostProcessing,
  setTrace,
  setVolume,
  setZoomLimit,
  show,
  skipIntro,
  soundLog,
  stillRect,
  stop,
  suspend,
  textOf,
  unavailable,
  zoomAt,
} = memorialLobby;
