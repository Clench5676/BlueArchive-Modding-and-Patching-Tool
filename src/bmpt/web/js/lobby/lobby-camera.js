// The memorial lobby's camera framing: in the fullscreen lobby a video camera button (the game's
// Cafe_Icon_Video) enters a framing mode: the lobby rests (no intro), the view zooms at the pointer and moves up
// and down, kept where no pose of the lobby (its intro, idle and every clip) shows past the art (lobby.js
// framingView). The shutter keeps the view as the lobby's camera: the lobby scaled about the screen centre and
// moved (lobby.js cameraParams), saved as a new Memorial Lobby choice of the student (lobby.camera.save, a job;
// docs/webui/lobby-and-photography.md), its art the source the lobby shows; its still flies into the album at
// the bottom left, which opens the student's Switch Memorial Lobby (detail.js) over the framing.
import { rpc, waitJob } from "../core/api.js";
import { T } from "../core/i18n.js";
import * as NGUI from "../ui/ngui.js";
import * as Lobby from "./lobby.js";
import { S, hitButton } from "../core/util.js";
import { visualButton as whiteButton, galleryBox } from "../photography/controller.js";
import { cameraFeedback } from "../photography/feedback.js";
import * as Loading from "../app/loading.js";
import { modJob } from "../core/job-progress.js";
import { prepareBack } from "../detail/visual-mode.js";
import { anchoredCentre } from "../detail/visual-view.js";
import { buttonCenter } from "../hall/student-hall-icons.js";
import { FULLSCREEN_BUTTON, SPACE } from "../ui/ui-tokens.js";

const STAGE = { w: 1920, h: 1080 };
// how long the flight waits for the saved choice's still before it takes the view on screen instead
const STILL_WAIT = 1500;

let hooks = null,
  overlay = null,
  active = false,
  saving = false,
  albumOpen = false,
  feedback = null,
  generation = 0;
const stage = () => document.getElementById("stage");

/**
 * Show the framing button beside the photography button: in the fullscreen lobby only, and not in a lobby
 * without a prefab (nothing to keep the camera in).
 * @param {object} [bridge] The detail screen's hooks (lobby(), the shown alt and the actions the camera
 *     needs); the last given ones are kept.
 * @returns {Promise<void>} Settles when the button is drawn.
 */
export async function entry(on, bridge) {
  document.getElementById("camera-launcher")?.remove();
  hooks = bridge || hooks;
  if (!on || active || !hooks || !hooks.lobby() || Lobby.cameraState()?.prefab === false) return;
  const my = ++generation;
  const layout = await NGUI.layout("UICharacterVisualMode"),
    corner = NGUI.find(layout, "Inventory_ListControl_PC/RightBottom");
  const reset = NGUI.find(corner, "Btn_RefreshPos"),
    body = NGUI.find(reset, "Btn_RefreshPos");
  const at = anchoredCentre(corner.widgets.find((w) => w.anchors)) || corner.pos;
  // the fullscreen views' one button size (ui-tokens.js FULLSCREEN_BUTTON)
  const width = FULLSCREEN_BUTTON.w * S,
    height = FULLSCREEN_BUTTON.h * S;
  // the gap between every two fullscreen buttons (SPACE.m, as the photography toolbar's)
  const gap = SPACE.m * S,
    x = 960 + (at[0] + reset.pos[0] + body.pos[0]) * S - width / 2 - (width + gap);
  const y = 540 - (at[1] + reset.pos[1] + body.pos[1]) * S - height / 2;
  if (my !== generation || active) return;
  const host = document.createElement("div");
  host.id = "camera-launcher";
  stage().append(host);
  await whiteButton(host, "CameraEntry", T("lobby.cameraOpen"), [x, y, width, height], open, {
    icon: "Cafe_Icon_Video",
    atlas: "Common",
    corner: "bottom-right",
    anchorBox: [x + width + gap, y, width, height],
  });
}

async function open() {
  if (active || !hooks) return;
  active = true;
  saving = false;
  albumOpen = false;
  document.getElementById("camera-launcher")?.remove();
  document.documentElement.classList.add("lobby-camera");
  Lobby.camera(true);
  overlay = document.createElement("div");
  overlay.id = "camera-overlay";
  stage().append(overlay);
  await Promise.all([back(), resetButton(), shutter(), album()]);
  feedback = cameraFeedback(stage(), { x: 0, y: 0, width: STAGE.w, height: STAGE.h }, () =>
    overlay?.querySelector('[data-path="Album/Icon"]'),
  );
}

async function back() {
  const wrapper = await prepareBack({
    layout: await NGUI.layout("UICharacterVisualMode"),
    label: T("photo.back"),
    onBack: () => close(),
  });
  if (!wrapper || !overlay) return;
  wrapper.querySelector("button").dataset.cameraAction = "Back";
  overlay.append(wrapper);
}
async function resetButton() {
  const host = document.createElement("div");
  overlay.append(host);
  const hit = await whiteButton(
    host,
    "CameraReset",
    T("visual.reset"),
    [1890 - 196, 25, 196, FULLSCREEN_BUTTON.h * S],
    () => Lobby.resetZoom(),
    { corner: "top-right" },
  );
  hit.dataset.cameraAction = "Reset";
}
// the album: the student photography's gallery button (photography/controller.js), its icon, size and place
async function album() {
  const host = document.createElement("div");
  overlay.append(host);
  const hit = await whiteButton(host, "Album", T("lobby.cameraAlbum"), galleryBox(), openAlbum, {
    icon: "Cafe_Icon_Photo",
    corner: "bottom-left",
  });
  hit.dataset.cameraAction = "Album";
}
// Switch Memorial Lobby over the framing, whose controls take no input meanwhile; a lobby changed there (a choice
// saved or applied) is framed anew from the game's view, at rest, as on entering
async function openAlbum() {
  if (!active || saving || albumOpen || !hooks?.album) return;
  const shown = overlay,
    before = Lobby.meta();
  albumOpen = true;
  shown.inert = true;
  try {
    await hooks.album();
  } finally {
    shown.inert = false;
    if (overlay === shown) albumOpen = false;
  }
  if (active && overlay === shown && Lobby.meta() !== before) Lobby.camera(true);
}
async function shutter() {
  const host = document.createElement("div");
  overlay.append(host);
  const tree = {
    name: "Shutter",
    active: true,
    pos: [0, 0],
    scale: [1, 1, 1],
    rotZ: 0,
    children: [],
    widgets: [
      {
        sprite: "Common_Btn_Circle_W",
        atlas: "Common",
        type: 0,
        w_: 146 / S,
        h_: 146 / S,
        pivot: 4,
        depth: 60,
        color: "#ffffff/1.00",
      },
    ],
  };
  const view = await NGUI.render(tree, host, { scale: S, origin: [960, 980] });
  const center = await buttonCenter(view.nodes.get("Shutter").els[0]);
  const ring = document.createElement("span");
  ring.className = "photo-shutter-ring";
  Object.assign(ring.style, { left: `${center.x - 49}px`, top: `${center.y - 49}px` });
  host.append(ring);
  const hit = hitButton(
    host,
    [887, 907, 146, 146],
    () =>
      save().catch((err) => {
        console.error("Camera save failed", err);
        saving = false;
      }),
    T("lobby.cameraSave"),
    90,
  );
  hit.dataset.cameraAction = "Save";
}

async function save() {
  if (saving || !active || albumOpen) return;
  const state = Lobby.cameraState();
  if (!state || state.measuring) return;
  saving = true;
  feedback?.flash();
  const { scale, offset } = state.params;
  let still = null;
  try {
    // Now Loading locks the screen until the saved choice is in and the student's choices are refreshed; the new
    // choice's still (its Switch Memorial Lobby card, framed by the camera) starts drawing behind it. Its line:
    // Saving, the student and the memorial lobby (job-progress.js modJob).
    const line = modJob(
      { alt: hooks.alt(), kind: "lobby" },
      { nameOf: Loading.altName, action: "progress.actionSave" },
    );
    await Loading.task(
      async (update) => {
        update(line());
        const saved = await waitJob(
          await rpc("lobby.camera.save", {
            alt: hooks.alt(),
            lobby: hooks.lobby(),
            source: hooks.source(),
            view: state.view,
            scale,
            offset,
          }),
          (p) => update(line(p)),
        );
        await hooks.saved?.(saved);
        still = Promise.resolve(hooks.still?.(saved.mod)).catch(() => null);
      },
      { label: line().text },
    );
  } finally {
    saving = false;
  }
  if (!active) return;
  const picture = await Promise.race([still, new Promise((resolve) => setTimeout(resolve, STILL_WAIT, null))]);
  const png = active ? picture || viewPicture() : null;
  if (png) await feedback?.saved(png).catch((err) => console.warn("Camera save flight failed", err));
}
// the view on screen, the lobby's layers drawn in order
function viewPicture() {
  const layers = [...document.querySelectorAll("#lobby .lobby-view .lobby-layer:not(.lobby-off) canvas")];
  if (!layers.length) return null;
  const canvas = document.createElement("canvas");
  canvas.width = 640;
  canvas.height = 360;
  const g = canvas.getContext("2d");
  for (const layer of layers) g.drawImage(layer, 0, 0, canvas.width, canvas.height);
  return canvas.toDataURL("image/png");
}

/**
 * Leave the framing (Back, leaving fullscreen or the page): the view goes back to the game's.
 * @param {boolean} [reenter] Whether the framing button comes back.
 */
export function close(reenter = true) {
  if (!active) return;
  active = false;
  albumOpen = false;
  feedback?.dispose();
  feedback = null;
  overlay?.remove();
  overlay = null;
  document.documentElement.classList.remove("lobby-camera");
  Lobby.camera(false);
  if (reenter) void entry(true);
}
/** Whether the framing mode is on. */
export const isOpen = () => active;
