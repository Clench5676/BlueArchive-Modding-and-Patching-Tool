// The top bar's Tasks button and its panel (task-panel-layout.js): the queued and running jobs, the running one
// with its items. The button carries the game's loading ring while any job is queued or running; a refused quit
// (app.busy) opens the panel, so the job that holds the quit is in view.
import * as NGUI from "../ui/ngui.js";
import { on, rpc } from "../core/api.js";
import { T } from "../core/i18n.js";
import { S } from "../core/util.js";
import { PANEL, panelTree, panelView } from "./task-panel-layout.js";

// jobs.list at most this often while the panel is open; job progress arrives up to ten times a second
const REFRESH_MS = 250;
const RING_SCALE = 0.38; // the button's running mark: LoadingIcon (75 units) at about a third of the button
const QUEUE_EVENTS = [
  "job.queued",
  "job.started",
  "job.done",
  "job.failed",
  "job.cancelled",
  "job.stopped",
  "job.dropped",
];

let options = { nameOf: undefined };
let jobs = [];
let open = false;
let timer = null;
let fetching = null;
let host = null;
let revision = 0;
const prefabs = {};

const button = () => document.getElementById("top-tasks");

async function loadPrefabs() {
  if (prefabs.row) return prefabs;
  const [mission, popup] = await Promise.all([NGUI.layout("UIMission"), NGUI.layout("UIRatioNoticePopup")]);
  Object.assign(prefabs, {
    row: NGUI.find(mission, "Right/ScrollViewWidget/ScrollView/Grid/NUIMissionContent"),
    frame: NGUI.find(popup, "Center/Popup/Base/BG"),
    title: NGUI.find(popup, "Center/Popup/Base/Label_Title"),
    ring: NGUI.find(popup, "Center/Popup/Base/LoadingIcon"),
  });
  return prefabs;
}

/** One full cycle of the ring's TweenRotation in seconds (a ping-pong turns there and back). */
function turnPeriod(ring) {
  const tween = ring?.widgets?.find((w) => w.cls === "TweenRotation")?.tween;
  const duration = tween?.duration || 3;
  return tween?.style === 2 ? 2 * duration : duration;
}

async function markRunning() {
  const b = button();
  if (!b) return;
  const busy = jobs.length > 0;
  let mark = b.querySelector(":scope > .top-tasks-ring");
  if (!busy) {
    mark?.remove();
    return;
  }
  if (mark) return;
  mark = document.createElement("span");
  mark.className = "top-tasks-ring";
  b.append(mark);
  try {
    const { ring } = await loadPrefabs();
    if (!ring || !mark.isConnected) return;
    // a loadingRing widget: NGUI.render turns it as the prefab's TweenRotation does
    const side = 75 * RING_SCALE;
    const colour = ring.widgets?.find((w) => "sprite" in w)?.color;
    const tree = NGUI.node("Ring", [0, 0], [{ loadingRing: true, w_: side, h_: side, pivot: 4, color: colour }]);
    await NGUI.render(tree, mark, { scale: S, origin: [(side * S) / 2, (side * S) / 2] });
  } catch (err) {
    console.error("tasks ring", err);
  }
}

async function draw() {
  if (!open) return;
  const mine = ++revision;
  const { row, frame, title, ring } = await loadPrefabs();
  if (!row || !frame || !title) return;
  const { tree, width, height } = panelTree(
    panelView(jobs, options.nameOf),
    { row, frame, title, ring },
    NGUI.textWidth,
  );
  const k = S * PANEL.SCALE;
  const next = document.createElement("div");
  await NGUI.render(tree, next, { scale: k, origin: [0, 0] });
  if (mine !== revision || !open || !host) return;
  host.replaceChildren(...next.childNodes);
  // Each redraw makes new rings; they start where the clock says, so they keep turning instead of starting over.
  host.style.setProperty("--turn-delay", `${-((performance.now() / 1000) % turnPeriod(ring))}s`);
  Object.assign(host.style, { width: `${width * k}px`, height: `${height * k}px` });
  place();
}

/** Put the panel under the button, its right edge by the button's. */
function place() {
  const b = button();
  if (!host || !b) return;
  const right = parseFloat(b.style.right) || 0,
    top = (parseFloat(b.style.top) || 0) + (parseFloat(b.style.height) || 0) + 8;
  Object.assign(host.style, { right: `${Math.max(0, right - 30)}px`, top: `${top}px` });
}

async function refresh() {
  if (fetching) return fetching;
  fetching = rpc("jobs.list")
    .then((list) => {
      jobs = Array.isArray(list) ? list : [];
      markRunning();
      return draw();
    })
    .catch((err) => console.error("jobs.list", err))
    .finally(() => {
      fetching = null;
    });
  return fetching;
}

function soon() {
  if (timer) return;
  timer = setTimeout(() => {
    timer = null;
    refresh();
  }, REFRESH_MS);
}

function outside(event) {
  if (!open) return;
  if (host?.contains(event.target) || button()?.contains(event.target)) return;
  hide();
}
function onEscape(event) {
  if (open && event.key === "Escape") {
    event.stopPropagation();
    hide();
  }
}

/** Open the panel under the Tasks button. */
export function show() {
  if (open) return refresh();
  open = true;
  host = document.createElement("div");
  host.className = "task-panel";
  host.setAttribute("role", "dialog");
  host.setAttribute("aria-label", T("tasks.title"));
  document.getElementById("stage")?.append(host);
  button()?.setAttribute("aria-expanded", "true");
  addEventListener("pointerdown", outside, true);
  addEventListener("keydown", onEscape, true);
  return refresh();
}

/** Close the panel. */
export function hide() {
  open = false;
  revision++;
  host?.remove();
  host = null;
  button()?.setAttribute("aria-expanded", "false");
  removeEventListener("pointerdown", outside, true);
  removeEventListener("keydown", onEscape, true);
}

/** Whether the panel is open. */
export const isOpen = () => open;

/**
 * Wire the Tasks button: its label, its running mark, and the panel it opens.
 * @param {object} [opts]
 * @param {(entry: {alt?: string, core?: string}) => string} [opts.nameOf] Names an alt as the student list does.
 */
export function init(opts = {}) {
  options = { ...options, ...opts };
  const b = button();
  if (!b) return;
  b.setAttribute("aria-label", T("tasks.title"));
  b.setAttribute("aria-expanded", "false");
  if (!b.dataset.wired) {
    b.dataset.wired = "1";
    b.addEventListener("click", () => (open ? hide() : show()));
    for (const event of QUEUE_EVENTS) on(event, soon);
    on("job.progress", () => {
      if (open) soon();
    });
    on("app.busy", () => show());
  }
  refresh();
}

/** Relabel after a language change and redraw an open panel. */
export function relabel() {
  button()?.setAttribute("aria-label", T("tasks.title"));
  host?.setAttribute("aria-label", T("tasks.title"));
  draw();
}
