import { rpc, waitJob } from "../core/api.js";
import { T, L } from "../core/i18n.js";
import * as NGUI from "../ui/ngui.js";
import * as UI from "../hall/student-hall-ui.js";
import * as Popup from "../ui/popup.js";
import * as Loading from "../app/loading.js";
import { modJob } from "../core/job-progress.js";
import { untestedRetry } from "../mods/mod-switch.js";
import { clone, S, hitButton } from "../core/util.js";
import { captureScene } from "./capture.js";
import { photograph } from "./shoot.js";
import { cameraFeedback } from "./feedback.js";
import { FRAME, choiceLayout, otherMode, actionCaption } from "./camera.js";
import { CONTROL, XS_BUTTON, SPACE, FULLSCREEN_BUTTON } from "../ui/ui-tokens.js";
import { row } from "../ui/ui-grid.js";
import { switchGrid } from "../mods/switch-layout.js";
import { alignIcon, buttonCenter } from "../hall/student-hall-icons.js";
import { anchoredCentre } from "../detail/visual-view.js";
import { wheelKind, wheelStep } from "../lobby/lobby-touch.js";
import { bindCorner, bindEdge } from "../window/corner-layout.js";
import { prepareBack } from "../detail/visual-mode.js";
export { FRAME };
/**
 * Make the white button of the visual mode's toolbar, for the lobby's camera framing (lobby-camera.js).
 */
export const visualButton = (...args) => whiteButton(...args);
/**
 * The gallery button's box at the bottom left (the lobby camera's album takes the same).
 */
export const galleryBox = () => photographyToolbarLayout().gallery;
let sessionAlt = null,
  generation = 0,
  repository,
  bridge,
  overlay,
  active = false,
  mode = "portrait",
  grid = true,
  expanded = false,
  paused = false,
  selected = null,
  modal = null;
// The gallery is the Switch pickers' list popup (popup.js listPopup) in their host: the chosen photo, its name and its
// actions at the left, the photos' cards scrolling at the right, Restore Default, Cancel and Save in the footer. Its
// cards and picture are Switch 3D Model's squares (layoutKind "photo"), each photo filling its inclined window.
const GALLERY_HOST = "popup",
  GALLERY_KIND = "photo";
const galleryHost = () => document.getElementById(GALLERY_HOST);
let controlsRevision = 0,
  saving = false,
  leaving = false,
  defaultThumbnail = null,
  generatedThumbnail = null,
  occlusion = false,
  feedback = null;
const CHOICE_ICONS = {
  portrait: { icon: "Common_Icon_Emoji", atlas: "Common" },
  lobby: { icon: "School_Icon_Chat", atlas: "Common" },
};
const P = (key) => T(`photo.${key}`);
const call = async (name, params = {}) => {
  const scope = generation,
    key = sessionAlt;
  const value = await rpc(`photography.${name}`, { alt: key, ...params });
  if (scope !== generation || key !== sessionAlt) throw new DOMException("Photography ended", "AbortError");
  return value;
};
const normalized = (value) => ({
  ...value,
  photos: value.photos.map((entry) => ({
    ...entry,
    name: entry.builtin
      ? P("generatedThumbnail")
      : [entry.alt !== sessionAlt ? L(entry.names)?.full || entry.alt : "", T("photo.captureName", { n: entry.number })]
          .filter(Boolean)
          .join(" - "),
  })),
});
const DEFAULT = "generated",
  OFFICIAL = "official";
const galleryPhotos = () =>
  [
    ...((repository?.photos || []).some((entry) => entry.alt === sessionAlt && entry.builtin)
      ? []
      : [
          {
            id: DEFAULT,
            name: P("generatedThumbnail"),
            url: generatedThumbnail,
            provenance: "runtime",
            managed: false,
          },
        ]),
    { id: OFFICIAL, name: P("officialThumbnail"), url: defaultThumbnail, provenance: "official", managed: false },
    ...(repository?.photos || []),
  ].filter((entry) => entry.url);
const photo = (id) => galleryPhotos().find((item) => item.id === id);
const selectedKey = (key) => (key === DEFAULT ? null : key);
async function generatedPNG() {
  const img = await NGUI.load(generatedThumbnail),
    canvas = document.createElement("canvas");
  canvas.width = img.naturalWidth || img.width;
  canvas.height = img.naturalHeight || img.height;
  canvas.getContext("2d").drawImage(img, 0, 0);
  return canvas.toDataURL("image/png");
}
const stage = () => document.getElementById("stage");
const tick = () => new Promise((resolve) => requestAnimationFrame(resolve));
const waitFrame = async (delay = 0) => {
  if (delay) await new Promise((resolve) => setTimeout(resolve, delay));
  await tick();
  await tick();
};
async function repositoryRead() {
  const key = sessionAlt,
    value = await call("list");
  if (key !== sessionAlt) throw new DOMException("Photography ended", "AbortError");
  repository = normalized(value);
  return repository;
}
function errorText(error) {
  return error.code ? T(`error.${error.code}`, error.params || {}) : P("captureFailed");
}
// A failure without an error code shows only the generic message: its message and stack go to the diagnostics
// report, which the popup alone would leave without a trace.
function safe(fn) {
  return (...args) =>
    Promise.resolve()
      .then(() => fn(...args))
      .catch((error) => {
        if (error.name === "AbortError") return;
        console.error("Photography action failed", error);
        if (!error.code) globalThis.BAMTRecovery?.forward?.(error, "photography");
        return Popup.message({ title: P("failed"), lines: [errorText(error)], ok: P("confirm") });
      });
}
function node(name, widgets, children = []) {
  return { name, active: true, pos: [0, 0], scale: [1, 1, 1], rotZ: 0, widgets, children };
}
let whiteTemplate;
// Back, the text and the icon buttons share the fullscreen views' one button size (ui-tokens.js FULLSCREEN_BUTTON),
// the text buttons wide enough for their captions; the separate shutter keeps its existing 146px circle.
function photographyToolbarLayout() {
  const textH = FULLSCREEN_BUTTON.h * S,
    icon = { w: FULLSCREEN_BUTTON.w * S, h: FULLSCREEN_BUTTON.h * S };
  const texts = row(
    [
      { w: 196, h: textH },
      { w: 196, h: textH },
    ],
    { gap: SPACE.m * S },
  );
  const top = row([texts, icon], { gap: SPACE.m * S }); // one gap between every two buttons
  const x = 1890 - top.w,
    y = 25,
    asBox = (b) => [b.x, b.y, b.w, b.h];
  const textBoxes = texts.boxes.map((b) => asBox({ ...b, x: x + top.boxes[0].x + b.x, y: y + top.boxes[0].y + b.y }));
  const mode = asBox({ ...top.boxes[1], x: x + top.boxes[1].x, y: y + top.boxes[1].y });
  const bottom = row([icon, icon], { x: 1890 - 2 * icon.w - SPACE.m * S, y: 950, gap: SPACE.m * S });
  const gallery = row([icon], { x: 25, y: 950 }).boxes[0];
  return {
    occlusion: textBoxes[0],
    grid: textBoxes[1],
    mode,
    top: [x, y, top.w, top.h],
    playback: asBox(bottom.boxes[0]),
    choices: asBox(bottom.boxes[1]),
    gallery: asBox(gallery),
    caption: {
      size: CONTROL.buttonXS.fs,
      min: XS_BUTTON.fsMin,
      width: 196 / S - 2 * CONTROL.buttonXS.pad,
      height: FULLSCREEN_BUTTON.h - XS_BUTTON.boxInsetH,
    },
  };
}
// the scroll offset (stage pixels) that shows the row of photo index: none while the row is in the first view, else the
// row at the list's top (the browser holds the offset to the list's end)
function galleryReveal(index) {
  const LP = Popup.listLayout(false, GALLERY_KIND),
    cards = Popup.listCardBox(LP, { band: Popup.LIST_BAND.slim });
  const { columns, pitch } = switchGrid(GALLERY_KIND, Popup.listCardBox(LP).w),
    top = Math.floor(Math.max(0, index) / columns) * pitch[1];
  return top + pitch[1] <= cards.h ? 0 : top * S;
}
// The captions of the two text buttons, one size for both states of the grid button too, so nothing changes size
// on a click.
const toolbarCaptions = () => {
  const { caption } = photographyToolbarLayout();
  return NGUI.fitGroup([P("occlusion"), P("gridOn"), P("gridOff")], caption.size, caption.min, caption.width, 2, {
    maxH: caption.height,
  });
};
async function whiteButton(
  host,
  name,
  caption,
  box,
  action,
  {
    icon = null,
    atlas = "Common",
    glyph = null,
    pressed = false,
    enabled = true,
    fit = null,
    corner = null,
    anchorBox = box,
  } = {},
) {
  whiteTemplate ||= NGUI.layout("UICharacterVisualMode").then((layout) =>
    clone(NGUI.find(layout, "Inventory_ListControl_PC/LeftTop/Btn_Back")),
  );
  const tree = clone(await whiteTemplate),
    [x, y, w, h] = box;
  tree.name = name;
  tree.pos = [0, 0];
  tree.children = [];
  const body = tree.widgets.find((widget) => "sprite" in widget);
  body.w_ = w / S;
  body.h_ = h / S;
  body.depth = 60;
  if (pressed) body.color = "#b9eeff/1.00";
  if (icon)
    tree.children.push(
      node("Icon", [
        {
          sprite: icon,
          atlas,
          w_: Math.min(w - 30, h - 26) / S,
          h_: Math.min(w - 30, h - 26) / S,
          pivot: 4,
          depth: 65,
          color: "#2b496e/1.00",
          type: 0,
        },
      ]),
    );
  else if (glyph)
    tree.children.push(
      node("Icon", [
        { texture: "glyph", w_: glyph.size / S, h_: glyph.size / S, pivot: 4, depth: 65, color: "#ffffff/1.00" },
      ]),
    );
  else if (!glyph)
    tree.children.push(
      node("Label", [
        {
          text: fit?.text ?? caption,
          fontSize: fit?.fs ?? CONTROL.buttonXS.fs,
          w_: w / S - XS_BUTTON.boxInsetW,
          h_: h / S - XS_BUTTON.boxInsetH,
          pivot: 4,
          depth: 65,
          color: "#2b496e/1.00",
          align: 2,
        },
      ]),
    );
  const wrapper = document.createElement("div");
  wrapper.dataset.photoControl = name;
  host.append(wrapper);
  const view = await NGUI.render(tree, wrapper, {
    scale: S,
    origin: [x + w / 2, y + h / 2],
    textureNames: glyph ? { glyph: glyph.url } : undefined,
  });
  if (icon)
    await alignIcon(view, `${name}/Icon`, name, {
      height: h * 0.52,
      maxWidth: w * 0.64,
      center: "bounds",
      color: "#2b496e",
    });
  else if (glyph) {
    // a texture glyph that fills its image, centred on the white body as alignIcon centres a sprite's ink
    const center = await buttonCenter(view.nodes.get(name).els[0]),
      el = view.nodes.get(`${name}/Icon`).els[0];
    Object.assign(el.style, {
      left: `${center.x - glyph.size / 2}px`,
      top: `${center.y - glyph.size / 2}px`,
      width: `${glyph.size}px`,
      height: `${glyph.size}px`,
    });
  } else if (!glyph) {
    let opticalBody = view.nodes.get(name).els[0];
    if (pressed) {
      // A cyan tint falls below the white-body detector's threshold. Measure the identical white stencil.
      const stencil = clone(tree);
      stencil.children = [];
      stencil.widgets.find((w) => "sprite" in w).color = "#ffffff/1.00";
      const probe = await NGUI.render(stencil, document.createElement("div"), {
        scale: S,
        origin: [x + w / 2, y + h / 2],
      });
      opticalBody = probe.nodes.get(name).els[0];
    }
    const center = await buttonCenter(opticalBody),
      label = view.nodes.get(`${name}/Label`).els[0];
    Object.assign(label.style, {
      left: `${center.x - (w - XS_BUTTON.boxInsetW * S) / 2}px`,
      top: `${center.y - (h - XS_BUTTON.boxInsetH * S) / 2}px`,
      paddingBottom: "0px",
      justifyContent: "center",
    });
    // The layer is not in the document yet, where computed style is empty: the size is the one the label was drawn
    // with.
    label.style.translate = `0 ${NGUI.inkShift(fit?.text ?? caption, parseFloat(label.style.fontSize))}px`;
  }
  const hit = hitButton(wrapper, box, safe(action), caption, 90);
  hit.disabled = !enabled;
  hit.dataset.photoAction = name;
  if (corner) bindCorner(wrapper, anchorBox, { corner });
  if (pressed) hit.setAttribute("aria-pressed", "true");
  return hit;
}
/**
 * Give the controller the detail screen's hooks (the bridge to the current student, its preview and its
 * captures).
 */
export function attach(hooks) {
  bridge = hooks;
}
/**
 * Close the photography mode and free everything it made.
 */
export function destroy() {
  generation++;
  controlsRevision++;
  active = false;
  leaving = false;
  modal?.close();
  modal = null;
  feedback?.dispose();
  feedback = null;
  overlay?.remove();
  overlay = null;
  document.getElementById("photo-launcher")?.remove();
  document.documentElement.classList.remove("photo-active", "photo-lobby");
  if (bridge?.cancel) bridge.cancel();
  else void bridge?.release();
  sessionAlt = null;
  repository = null;
}
/**
 * Show the photography button in the visual mode, when the shown mode is the portrait or the lobby.
 * @param {boolean} on Whether the visual mode is shown.
 */
export async function visualEntry(on) {
  document.getElementById("photo-launcher")?.remove();
  if (!on || active || !bridge || !["portrait", "lobby"].includes(bridge.info().mode)) return;
  const layout = await NGUI.layout("UICharacterVisualMode"),
    corner = NGUI.find(layout, "Inventory_ListControl_PC/RightBottom");
  const reset = NGUI.find(corner, "Btn_RefreshPos"),
    body = NGUI.find(reset, "Btn_RefreshPos");
  const at = anchoredCentre(corner.widgets.find((w) => w.anchors)) || corner.pos;
  const width = FULLSCREEN_BUTTON.w * S,
    height = FULLSCREEN_BUTTON.h * S;
  const gap = SPACE.m * S,
    x = 960 + (at[0] + reset.pos[0] + body.pos[0]) * S - width / 2 - (bridge.info().mode === "lobby" ? 0 : width + gap);
  const y = 540 - (at[1] + reset.pos[1] + body.pos[1]) * S - height / 2;
  const host = document.createElement("div");
  host.id = "photo-launcher";
  stage().append(host);
  await whiteButton(host, "Entry", P("open"), [x, y, width, height], () => open(bridge), {
    icon: "Combat_Icon_Camera",
    atlas: "Combat",
    corner: "bottom-right",
    anchorBox: [x + (bridge.info().mode === "lobby" ? 0 : width + gap), y, width, height],
  });
}
async function open(hooks) {
  if (active) return;
  bridge = hooks;
  const scope = ++generation;
  sessionAlt = hooks.info().alt;
  active = true;
  saving = false;
  mode = hooks.info().mode === "lobby" ? "lobby" : "portrait";
  paused = false;
  expanded = false;
  document.documentElement.classList.add("photo-active");
  try {
    await Loading.task(
      async () => {
        await repositoryRead();
        await Promise.all([
          hooks.full(mode),
          Promise.resolve(repository.default || hooks.defaultThumbnail()).then(async (url) => {
            defaultThumbnail = await url;
            if (defaultThumbnail) await NGUI.load(defaultThumbnail);
          }),
        ]);
        await Promise.all([
          hooks.prepareFaces(),
          hooks.generatedThumbnail().then(async (url) => {
            generatedThumbnail = url;
            if (url) await NGUI.load(url);
          }),
        ]);
      },
      { label: false },
    );
    if (scope !== generation) return;
    overlay = document.createElement("div");
    overlay.id = "photo-overlay";
    stage().append(overlay);
    await drawControls();
    feedback = cameraFeedback(stage(), FRAME, () => overlay?.querySelector('[data-path="Gallery/Icon"]'));
  } catch (error) {
    if (scope !== generation || error.name === "AbortError") return;
    active = false;
    document.documentElement.classList.remove("photo-active");
    overlay?.remove();
    throw error;
  }
}
async function leave() {
  if (!active || leaving) return;
  leaving = true;
  generation++;
  try {
    modal?.close();
    modal = null;
    await Loading.task(
      async () => {
        await bridge.leave();
        active = false;
        controlsRevision++;
        feedback?.dispose();
        feedback = null;
        overlay?.remove();
        overlay = null;
        document.documentElement.classList.remove("photo-active");
        await visualEntry(false);
      },
      { label: false },
    );
  } finally {
    leaving = false;
  }
}
async function switchMode() {
  if (saving) return;
  const next = otherMode(mode, bridge.info());
  if (!next) throw new Error(P("noAssets"));
  bridge.pause(false);
  paused = false;
  expanded = false;
  saving = true;
  try {
    await bridge.full(next);
    mode = next;
  } finally {
    saving = false;
  }
  await drawControls();
}
function panSurface(host) {
  const surface = document.createElement("div");
  surface.className = "photo-pan";
  host.append(surface);
  const pointers = new Map();
  let pinch = null,
    queued = false,
    gesture = 1,
    pinchUntil = 0,
    scroll = { kind: null, at: -Infinity };
  const redraw = () => {
    if (!paused || queued) return;
    queued = true;
    requestAnimationFrame(() => {
      queued = false;
      bridge.redraw().catch(console.error);
    });
  };
  const anchor = (x, y) => {
    const rect = stage().getBoundingClientRect(),
      k = 1920 / rect.width;
    return [(x - rect.left) * k, (y - rect.top) * k, k];
  };
  const pair = () => {
    const [a, b] = [...pointers.values()];
    return { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2, d: Math.hypot(a.x - b.x, a.y - b.y) };
  };
  surface.addEventListener("pointerdown", (event) => {
    if (event.button !== 0) return;
    pointers.set(event.pointerId, { x: event.clientX, y: event.clientY });
    surface.setPointerCapture(event.pointerId);
    if (pointers.size === 2) pinch = pair();
    event.preventDefault();
  });
  surface.addEventListener("pointermove", (event) => {
    const old = pointers.get(event.pointerId);
    if (!old || saving) return;
    pointers.set(event.pointerId, { x: event.clientX, y: event.clientY });
    if (pointers.size === 2) {
      const next = pair();
      if (pinch) {
        const [x, y, k] = anchor(next.x, next.y);
        if (pinch.d > 0) bridge.zoom(next.d / pinch.d, x, y);
        bridge.pan((next.x - pinch.x) * k, (next.y - pinch.y) * k);
      }
      pinch = next;
    } else if (pointers.size === 1) {
      const k = 1920 / stage().getBoundingClientRect().width;
      bridge.pan((event.clientX - old.x) * k, (event.clientY - old.y) * k);
    }
    redraw();
  });
  for (const type of ["pointerup", "pointercancel", "lostpointercapture"])
    surface.addEventListener(type, (event) => {
      pointers.delete(event.pointerId);
      pinch = null;
    });
  surface.addEventListener(
    "wheel",
    (event) => {
      event.preventDefault();
      if (saving) return;
      const [x, y, k] = anchor(event.clientX, event.clientY),
        unit = event.deltaMode === 1 ? 40 : event.deltaMode === 2 ? 800 : 1,
        now = performance.now();
      if (event.ctrlKey) {
        if (now < pinchUntil) return;
        bridge.zoom(Math.exp(-event.deltaY * unit * 0.01), x, y);
      } else {
        const kind = wheelKind(event, now, scroll);
        scroll = { kind, at: now };
        if (kind === "mouse" && Math.abs(event.deltaY) >= Math.abs(event.deltaX))
          bridge.zoom(
            Math.exp(
              -Math.sign(event.deltaY) *
                Math.max(Math.abs(event.deltaY * unit), wheelStep(event) === "notch" ? 40 : 0) *
                0.0015,
            ),
            x,
            y,
          );
        else bridge.pan(-event.deltaX * unit * k, -event.deltaY * unit * k);
      }
      redraw();
    },
    { passive: false },
  );
  surface.addEventListener("gesturestart", (event) => {
    event.preventDefault();
    gesture = 1;
    pinchUntil = performance.now() + 300;
  });
  surface.addEventListener("gesturechange", (event) => {
    event.preventDefault();
    if (saving) return;
    pinchUntil = performance.now() + 300;
    const [x, y] = anchor(event.clientX, event.clientY);
    bridge.zoom(event.scale / gesture, x, y);
    gesture = event.scale;
    redraw();
  });
  surface.addEventListener("gestureend", (event) => {
    event.preventDefault();
    pinchUntil = performance.now() + 300;
  });
  surface.addEventListener("contextmenu", (event) => event.preventDefault());
}

async function shutter(layer) {
  // it rises with the bottom menu the pointer brings back, as the corner controls do (corner-layout.js bindEdge)
  const host = document.createElement("div");
  layer.append(host);
  bindEdge(host, "bottom");
  const tree = node("Shutter", [
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
  ]);
  const view = await NGUI.render(tree, host, { scale: S, origin: [960, 980] });
  const center = await buttonCenter(view.nodes.get("Shutter").els[0]);
  const ring = document.createElement("span");
  ring.className = "photo-shutter-ring";
  Object.assign(ring.style, { left: `${center.x - 49}px`, top: `${center.y - 49}px` });
  host.append(ring);
  const hit = hitButton(host, [887, 907, 146, 146], safe(capture), P("capture"), 90);
  hit.disabled = saving;
  hit.dataset.photoAction = "Capture";
}

async function modeButton(host) {
  // a skin without the other mode has nothing to switch to: no button, rather than one that can only fail
  if (!otherMode(mode, bridge.info())) return;
  const boxes = photographyToolbarLayout(),
    [x, y, w, h] = boxes.mode;
  const wrapper = document.createElement("div");
  host.append(wrapper);
  const layout = await NGUI.layout("UILobby"),
    tree = clone(NGUI.find(layout, "Front/Right/Btn_RotateSpine/ConditionRoot/Able_Count"));
  tree.name = "CameraMode";
  tree.pos = [0, 0];
  tree.children = tree.children.filter((child) => ["Bg", "Icon"].includes(child.name));
  const body = NGUI.find(tree, "Bg").widgets.find((w) => "sprite" in w);
  body.w_ = w / S;
  body.h_ = h / S;
  body.depth = 60;
  const icon = NGUI.find(tree, "Icon");
  icon.pos = [0, 0];
  icon.scale = [1, 1];
  icon.widgets.find((w) => "sprite" in w).depth = 65;
  const view = await NGUI.render(tree, wrapper, { scale: S, origin: [x + w / 2, y + h / 2] });
  await alignIcon(view, "CameraMode/Icon", "CameraMode/Bg", {
    height: 48 * S,
    maxWidth: 104 * S,
    center: "bounds",
    color: "#2b496e",
  });
  const hit = hitButton(wrapper, boxes.mode, safe(switchMode), P(mode === "portrait" ? "lobby" : "portrait"), 90);
  hit.dataset.photoAction = "Mode";
  bindCorner(wrapper, boxes.top, { corner: "top-right" });
}

async function backButton(host) {
  const wrapper = await prepareBack({
    layout: await NGUI.layout("UICharacterVisualMode"),
    label: P("back"),
    onBack: safe(leave),
  });
  if (!wrapper) return;
  wrapper.querySelector("button").dataset.photoAction = "Back";
  host.append(wrapper);
}

async function playbackButton(host) {
  // the same white button as its neighbours: the game's play icon, or the stop square at the play icon's ink height
  const { playback, choices } = photographyToolbarLayout(),
    label = P(paused ? "play" : "pause");
  const stop = `data:image/svg+xml,${encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 28 28"><rect width="28" height="28" rx="4" fill="#2b496e"/></svg>')}`;
  const hit = await whiteButton(
    host,
    "Playback",
    label,
    playback,
    async () => {
      if (saving) return;
      paused = !paused;
      bridge.pause(paused);
      await drawControls();
    },
    paused
      ? { icon: "Common_Icon_Play", atlas: "Common", corner: "bottom-right", anchorBox: choices }
      : { glyph: { url: stop, size: Math.round(playback[3] * 0.4) }, corner: "bottom-right", anchorBox: choices },
  );
  hit.setAttribute("aria-pressed", String(paused));
}

// The list's whole box takes the pointer, blank space and frame included: the pan surface below never sees a
// press, a wheel or its grab cursor there, and a wheel over the cards' gaps scrolls the list.
function choiceShield(host, box) {
  const shield = document.createElement("div");
  shield.className = "photo-choice-shield";
  Object.assign(shield.style, {
    left: `${box.x}px`,
    top: `${box.y}px`,
    width: `${box.width}px`,
    height: `${box.height}px`,
  });
  host.prepend(shield);
  return shield;
}
async function choicePanel(host, box) {
  const { formation } = await UI.ready(),
    tree = clone(NGUI.find(formation, "Center/Popup/RightBg"));
  tree.name = "PhotoChoices";
  tree.pos = [0, 0];
  tree.children = tree.children.filter((child) => child.name === "Bg_W");
  Object.assign(
    tree.widgets.find((w) => "sprite" in w),
    { w_: box.width / S, h_: box.height / S, depth: 15 },
  );
  const paper = NGUI.find(tree, "Bg_W");
  paper.pos = [0, 0];
  Object.assign(
    paper.widgets.find((w) => "sprite" in w),
    { w_: box.width / S - 20, h_: box.height / S - 20, depth: 16 },
  );
  await NGUI.render(tree, host, { scale: S, origin: [box.x + box.width / 2, box.y + box.height / 2] });
}

async function occlusionGuide(layer) {
  const layout = await NGUI.layout("UICharacterCollectionPopulatorUnit");
  const tree = clone(NGUI.find(layout, "Grid/UICollectCard"));
  tree.name = "Occlusion";
  tree.pos = [0, 0];
  tree.scale = [1, 1];
  tree.active = true;
  tree.children = tree.children.filter((child) => ["Sort", "CharacterWeapon"].includes(child.name));
  const sort = NGUI.find(tree, "Sort");
  sort.active = true;
  sort.children = sort.children.filter((child) => child.name === "Sprite");
  const weapon = NGUI.find(tree, "CharacterWeapon");
  weapon.active = true;
  const host = document.createElement("div");
  Object.assign(host.style, {
    position: "absolute",
    left: FRAME.x + "px",
    top: FRAME.y + "px",
    width: FRAME.width + "px",
    height: FRAME.height + "px",
    overflow: "hidden",
    pointerEvents: "none",
    zIndex: "5",
  });
  layer.append(host);
  const scale = FRAME.width / 404;
  await NGUI.render(tree, host, { scale, origin: [FRAME.width / 2, FRAME.height / 2 + 2.8 * scale] });
}
async function drawControls() {
  if (!active || !overlay) return;
  const revision = ++controlsRevision;
  const layer = document.createElement("div");
  panSurface(layer);
  const guide = document.createElement("div");
  guide.className = "photo-guide" + (grid ? "" : " no-grid");
  guide.innerHTML = "<i></i><i></i><i></i><i></i>";
  layer.append(guide);
  Object.assign(guide.style, {
    left: `${FRAME.x}px`,
    top: `${FRAME.y}px`,
    width: `${FRAME.width}px`,
    height: `${FRAME.height}px`,
  });
  const captions = toolbarCaptions(),
    boxes = photographyToolbarLayout();
  const buttons = [
    backButton(layer),
    whiteButton(
      layer,
      "Occlusion",
      P("occlusion"),
      boxes.occlusion,
      async () => {
        occlusion = !occlusion;
        await drawControls();
      },
      { pressed: occlusion, fit: captions[0], corner: "top-right", anchorBox: boxes.top },
    ),
    whiteButton(
      layer,
      "Grid",
      grid ? P("gridOn") : P("gridOff"),
      boxes.grid,
      async () => {
        grid = !grid;
        await drawControls();
      },
      { pressed: grid, fit: captions[grid ? 1 : 2], corner: "top-right", anchorBox: boxes.top },
    ),
    modeButton(layer),
    whiteButton(layer, "Gallery", P("gallery"), boxes.gallery, openGallery, {
      icon: "Cafe_Icon_Photo",
      corner: "bottom-left",
    }),
    shutter(layer),
    whiteButton(
      layer,
      "Choices",
      P(mode === "portrait" ? "expressions" : "actions"),
      boxes.choices,
      async () => {
        expanded = !expanded;
        await drawControls();
      },
      { ...CHOICE_ICONS[mode], corner: "bottom-right" },
    ),
  ];
  if (occlusion) buttons.push(occlusionGuide(layer));
  if (mode === "lobby") buttons.push(playbackButton(layer));
  if (expanded) {
    const panelHost = document.createElement("div");
    layer.append(panelHost);
    bindCorner(panelHost, boxes.choices, { corner: "bottom-right" });
    const entries = mode === "portrait" ? bridge.faces() : bridge.actions(),
      box = choiceLayout(mode, entries.length);
    choiceShield(panelHost, box);
    await choicePanel(panelHost, box);
    if (mode === "portrait") {
      const items = entries,
        area = UI.area(
          panelHost,
          [box.x + 12, box.y + 12, box.width - 24, box.rows * box.rowHeight],
          box.contentHeight,
          "photo-faces",
        );
      for (let index = 0; index < items.length; index++)
        buttons.push(
          (async () => {
            const key = items[index],
              preview = await bridge.facePreview(key),
              x = 6 + (index % box.columns) * 146,
              y = Math.floor(index / box.columns) * box.rowHeight;
            const tree = bridge.faceCard(key, preview),
              scale = 128 / 242,
              height = 209 * scale;
            await NGUI.render(tree, area, { scale, origin: [x + 64, y + height / 2] });
            const hit = hitButton(
              area,
              [x, y, 128, height],
              safe(async () => {
                if (saving) return;
                bridge.setFace(key);
                await drawControls();
              }),
              key === "00" ? P("natural") : `${P("expressions")} ${key}`,
              90,
            );
            hit.setAttribute("aria-pressed", String(key === bridge.face()));
          })(),
        );
    } else {
      const sequences = entries;
      const actionArea = UI.area(
        panelHost,
        [box.x + 12, box.y + 12, box.columns * 196, box.rows * 61],
        box.contentHeight,
        "photo-actions",
      );
      for (let index = 0; index < sequences.length; index++)
        buttons.push(
          UI.button(
            actionArea,
            "Sequence" + index,
            actionCaption(sequences[index], T),
            [6 + (index % box.columns) * 196, Math.floor(index / box.columns) * 61, 185, 53],
            safe(async () => {
              if (saving) return;
              paused = false;
              bridge.play(sequences[index].anim);
              await drawControls();
            }),
          ),
        );
    }
  }
  await Promise.all(buttons);
  if (!active || revision !== controlsRevision) return;
  overlay.replaceChildren(...layer.childNodes);
}
async function capture() {
  if (!active || saving) return;
  saving = true;
  const scope = generation,
    key = sessionAlt,
    current = bridge,
    effects = feedback;
  const scoped = {
    ...current,
    pause: (on) => {
      if (scope === generation) current.pause(on);
    },
  };
  try {
    const result = await photograph({
      bridge: scoped,
      mode,
      paused,
      alive: () => scope === generation && active && !leaving,
      wait: waitFrame,
      capture: async () => {
        if (scope !== generation) throw new DOMException("Photography ended", "AbortError");
        const shot = await captureScene(current.paintRoots(), stage());
        if (scope !== generation) throw new DOMException("Photography ended", "AbortError");
        return shot;
      },
      save: (payload) => rpc("photography.save", { alt: key, ...payload }),
      onCaptured: () => effects?.flash(),
      onSaved: (_, shot) => effects?.saved(shot.png),
    });
    if (scope !== generation) return;
    repository = normalized({ ...repository, ...result });
    selected = result.saved;
    overlay?.querySelector(".photo-shutter-ring")?.animate([{ scale: ".9" }, { scale: "1" }], { duration: 140 });
  } finally {
    if (scope === generation) saving = false;
  }
}
async function openGallery(keepSelection = false) {
  if (saving) return;
  bridge.pause(true);
  await repositoryRead();
  if (!keepSelection)
    selected =
      repository.app || repository.photos.find((entry) => entry.alt === sessionAlt && entry.builtin)?.id || DEFAULT;
  const host = galleryHost(),
    handle = {
      close: () => {
        if (modal !== handle) return;
        modal = null;
        host.hidden = true;
        host.innerHTML = "";
        Popup.closed(host);
        bridge.pause(paused);
      },
    };
  modal?.close();
  modal = handle;
  await galleryDraw(galleryReveal(galleryPhotos().findIndex((item) => item.id === selected)));
}
// the list's offset now, kept by a redraw (an Apply, a Delete, another card chosen)
const galleryOffset = () => galleryHost()?.querySelector(".popup-cards")?.scrollTop || 0;
async function galleryDraw(keepScroll = galleryOffset()) {
  if (!modal) return;
  const handle = modal,
    photos = galleryPhotos();
  if (!photo(selected)) selected = photos[0]?.id ?? null;
  const items = photos.map((entry) => {
    const applied = !!(entry.applied || (entry.id === OFFICIAL && repository.game?.id === OFFICIAL));
    return {
      key: entry.id,
      label: entry.name,
      full: entry.name,
      thumb: entry.url,
      big: entry.url,
      applied,
      chips: applied ? [P("applied")] : [],
    };
  });
  const restorable = repository.app !== repository.game?.id || !photo(repository.game?.id)?.builtin;
  const applicable =
    repository.applicable &&
    (!repository.game?.known ||
      selected !== repository.game?.id ||
      !!repository.game?.error ||
      repository.game?.pending);
  await Popup.listPopup({
    hostId: GALLERY_HOST,
    layoutKind: GALLERY_KIND,
    title: P("gallery"),
    items,
    selected,
    keepScroll,
    slimBand: true,
    empty: P("empty"),
    buttons: [
      {
        label: P("restore"),
        fn: safe(restoreGame),
        action: "restore",
        ...(restorable ? {} : { greyed: true, blocked: true }),
      },
      { label: P("cancel"), fn: () => handle.close(), tone: "bright", action: "cancel" },
      { label: P("saveSelection"), fn: safe(saveSelection), primary: true, semantic: "save", action: "ok" },
    ],
    onSelect: (key, scroll) => {
      if (modal !== handle) return;
      selected = key;
      void safe(galleryDraw)(scroll);
    },
    onClose: () => handle.close(),
    details: (it) => {
      const entry = photo(it.key),
        deletable = entry?.managed !== false && entry?.provenance !== "official";
      return {
        title: P("photoLabel"),
        fields: [],
        actions: [
          [P("apply"), safe(applyGame), !applicable, "apply"],
          [P("export"), safe(exportPhoto), false, "export"],
          ...(deletable ? [[P("delete"), safe(deletePhoto)]] : []),
        ],
      };
    },
  });
}
// a photo's job under the loading screen with its line: the verb, the student and Student Thumbnail (job-progress.js)
function photoTask(action, work) {
  const line = modJob({ alt: sessionAlt, kind: "photo" }, { nameOf: Loading.altName, action });
  return Loading.task(
    async (update) => {
      update(line());
      return work((p) => update(line(p)));
    },
    { label: line().text },
  );
}
async function exportPhoto() {
  if (!photo(selected)) return;
  await photoTask("progress.actionExport", async () =>
    call("export", { id: selected, png: selected === DEFAULT ? await generatedPNG() : null }),
  );
}
async function saveSelection() {
  if (!selected) return;
  repository = normalized({ ...repository, ...(await call("select", { id: selectedKey(selected) })) });
  bridge.thumbnailChanged();
  modal?.close();
}
async function deletePhoto() {
  const key = selected,
    info = await call("removalInfo", { id: key });
  if (info.official) return;
  if (info.used) {
    const warning =
      info.used_elsewhere && !info.used_here ? "error.photo_delete_other_alt" : "error.asset_delete_confirm";
    const answer = await Popup.message({
      title: P("delete"),
      lines: [T(warning)],
      ok: P("delete"),
      cancel: T("popup.cancel"),
    });
    if (answer.button !== "ok") return;
  }
  const scope = generation;
  await photoTask("progress.actionDelete", async (report) => {
    const result = await waitJob(await call("delete", { id: key, confirm: !!info.used }), report);
    if (scope !== generation) throw new DOMException("Photography ended", "AbortError");
    repository = normalized(result);
  });
  bridge.thumbnailChanged();
  selected = repository.photos.at(-1)?.id || DEFAULT;
  await galleryDraw();
}

// an untested game build's confirmation (mod-switch.js untestedRetry) makes the write again
async function restoreGame() {
  const scope = generation;
  try {
    await photoTask("progress.actionRestore", async (report) => {
      await waitJob(await call("restore", { png: await generatedPNG() }), report);
      if (scope !== generation) throw new DOMException("Photography ended", "AbortError");
      await repositoryRead();
    });
  } catch (error) {
    if (scope === generation && (await untestedRetry(error, restoreGame))) return;
    throw error;
  }
  bridge.thumbnailChanged();
  selected = repository.app || DEFAULT;
  await galleryDraw(0);
}
async function applyGame() {
  if (!photo(selected) || !repository.applicable) return;
  const scope = generation;
  try {
    await photoTask("progress.actionApply", async (report) => {
      await waitJob(
        await call("apply", { id: selected, png: selected === DEFAULT ? await generatedPNG() : null }),
        report,
      );
      if (scope !== generation) throw new DOMException("Photography ended", "AbortError");
      await repositoryRead();
    });
  } catch (error) {
    if (scope === generation && (await untestedRetry(error, applyGame))) return;
    throw error;
  }
  await galleryDraw();
}
// Escape leaves the camera; a popup over it (a capture error) takes it first, and closes alone
globalThis.addEventListener?.(
  "keydown",
  (event) => {
    if (
      active &&
      !modal &&
      !Loading.covering() &&
      event.key === "Escape" &&
      !event.defaultPrevented &&
      !Popup.anyOpen()
    ) {
      event.preventDefault();
      event.stopImmediatePropagation();
      safe(leave)();
    }
  },
  true,
);
