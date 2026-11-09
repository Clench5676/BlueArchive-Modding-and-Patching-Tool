// The memorial lobby's camera framing (BL-137): in the fullscreen lobby a video camera button (the game's
// Cafe_Icon_Video) enters a framing mode: the lobby rests (no intro), the view zooms at the pointer and moves up and
// down, kept where no pose of the lobby (its intro, idle and every clip) shows past the art (lobby.js framingView);
// the outline marks the game's own view. The shutter keeps the view as the lobby's camera: the lobby scaled about the
// screen centre and moved (lobby.js cameraParams), saved as a new Memorial Lobby choice of the student (lobby.camera.save,
// a job; docs/web-ui.md), its art the source the lobby shows.
import { rpc, waitJob } from "./api.js";
import { T } from "./i18n.js";
import * as NGUI from "./ngui.js";
import * as Lobby from "./lobby.js";
import * as Popup from "./popup.js";
import { S, hitButton } from "./util.js";
import { visualButton as whiteButton } from "./photography/controller.js";
import { cameraFeedback } from "./photography/feedback.js";
import { prepareBack } from "./visual-mode.js";
import { anchoredCentre } from "./visual-view.js";
import { buttonCenter } from "./student-hall-icons.js";

const STAGE = { w: 1920, h: 1080 };

let hooks = null, overlay = null, active = false, saving = false, feedback = null, frame = 0, generation = 0;
const stage = () => document.getElementById("stage");

// the entry: beside the photography button, in the fullscreen lobby only, and not in a lobby without a prefab (nothing
// to keep the camera in)
export async function entry(on, bridge) {
  document.getElementById("camera-launcher")?.remove();
  hooks = bridge || hooks;
  if (!on || active || !hooks || !hooks.lobby() || Lobby.cameraState()?.prefab === false) return;
  const my = ++generation;
  const layout = await NGUI.layout("UICharacterVisualMode"), corner = NGUI.find(layout, "Inventory_ListControl_PC/RightBottom");
  const reset = NGUI.find(corner, "Btn_RefreshPos"), body = NGUI.find(reset, "Btn_RefreshPos"), widget = body.widgets.find((w) => "sprite" in w);
  const at = anchoredCentre(corner.widgets.find((w) => w.anchors)) || corner.pos;
  const width = widget.w_ * S, height = widget.h_ * S;
  const x = 960 + (at[0] + reset.pos[0] + body.pos[0]) * S - width / 2 - (width + 26);
  const y = 540 - (at[1] + reset.pos[1] + body.pos[1]) * S - height / 2;
  if (my !== generation || active) return;
  const host = document.createElement("div");
  host.id = "camera-launcher";
  stage().append(host);
  await whiteButton(host, "CameraEntry", T("lobby.cameraOpen"), [x, y, width, height], open,
    { icon: "Cafe_Icon_Video", atlas: "Common", corner: "bottom-right", anchorBox: [x + width + 26, y, width, height] });
}

async function open() {
  if (active || !hooks) return;
  active = true;
  saving = false;
  document.getElementById("camera-launcher")?.remove();
  document.documentElement.classList.add("lobby-camera");
  Lobby.camera(true);
  overlay = document.createElement("div");
  overlay.id = "camera-overlay";
  stage().append(overlay);
  const guide = document.createElement("div");
  guide.className = "camera-guide";
  overlay.append(guide);
  await Promise.all([back(), resetButton(), shutter()]);
  feedback = cameraFeedback(stage(), { x: 0, y: 0, width: STAGE.w, height: STAGE.h }, () => null);
  const follow = () => {
    if (!active) return;
    place(guide, Lobby.cameraState());
    frame = requestAnimationFrame(follow);
  };
  follow();
}
// the game's view, where the current view shows it (stage px)
export function guideBox(state) {
  const V = state.view, G = state.game;
  return { x: ((G.x - V.x) / V.w) * STAGE.w, y: (1 - (G.y + G.h - V.y) / V.h) * STAGE.h, w: (G.w / V.w) * STAGE.w, h: (G.h / V.h) * STAGE.h };
}
function place(el, state) {
  if (!state) return;
  const b = guideBox(state);
  Object.assign(el.style, { left: `${b.x}px`, top: `${b.y}px`, width: `${b.w}px`, height: `${b.h}px` });
}

async function back() {
  const wrapper = await prepareBack({ layout: await NGUI.layout("UICharacterVisualMode"), label: T("photo.back"), onBack: () => close() });
  if (!wrapper || !overlay) return;
  wrapper.querySelector("button").dataset.cameraAction = "Back";
  overlay.append(wrapper);
}
async function resetButton() {
  const host = document.createElement("div");
  overlay.append(host);
  const hit = await whiteButton(host, "CameraReset", T("visual.reset"), [1890 - 196, 25, 196, 76], () => Lobby.resetZoom(), { corner: "top-right" });
  hit.dataset.cameraAction = "Reset";
}
async function shutter() {
  const host = document.createElement("div");
  overlay.append(host);
  const tree = { name: "Shutter", active: true, pos: [0, 0], scale: [1, 1, 1], rotZ: 0, children: [],
    widgets: [{ sprite: "Common_Btn_Circle_W", atlas: "Common", type: 0, w_: 146 / S, h_: 146 / S, pivot: 4, depth: 60, color: "#ffffff/1.00" }] };
  const view = await NGUI.render(tree, host, { scale: S, origin: [960, 980] });
  const center = await buttonCenter(view.nodes.get("Shutter").els[0]);
  const ring = document.createElement("span");
  ring.className = "photo-shutter-ring";
  Object.assign(ring.style, { left: `${center.x - 49}px`, top: `${center.y - 49}px` });
  host.append(ring);
  const hit = hitButton(host, [887, 907, 146, 146], () => save().catch((err) => {
    console.error("Camera save failed", err);
    saving = false;
  }), T("lobby.cameraSave"), 90);
  hit.dataset.cameraAction = "Save";
}

async function save() {
  if (saving || !active) return;
  const state = Lobby.cameraState();
  if (!state || state.measuring) return;
  saving = true;
  feedback?.flash();
  const { scale, offset } = state.params;
  let result;
  try {
    const id = await rpc("lobby.camera.save", { variant: hooks.variant(), lobby: hooks.lobby(), source: hooks.source(),
      view: state.view, scale, offset });
    result = await waitJob(id);
  } finally {
    saving = false;
  }
  // the student's lobby choices again, with the new one
  await hooks.saved?.(result);
  if (!active) return;
  await Popup.message({ title: T("lobby.cameraSaved"),
    lines: [T("lobby.cameraDetail", { scale: result.scale.toFixed(3), offset: result.offset.map((v) => Math.round(v)).join(", ") })],
    ok: T("photo.confirm") });
}

// leaves the framing (Back, leaving fullscreen or the page): the view goes back to the game's
export function close(reenter = true) {
  if (!active) return;
  active = false;
  cancelAnimationFrame(frame);
  feedback?.dispose();
  feedback = null;
  overlay?.remove();
  overlay = null;
  document.documentElement.classList.remove("lobby-camera");
  Lobby.camera(false);
  if (reenter) void entry(true);
}
export const isOpen = () => active;
