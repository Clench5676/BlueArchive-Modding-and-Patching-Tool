// Update popup on the game's popup frame (UIPopup_Sort): the current and the new version, Later / Update.
// Updating downloads and verifies the build; Restart installs it only after a separate confirmation.
// check() resolves once the popup has closed; automatic checks wait for beforeShow without holding a screen.
import * as Buttons from "./game-buttons.js";
import * as NGUI from "./ngui.js";
import * as Popup from "./popup.js";
import { frameGeometry } from "./popup-frames.js";
import { rpc, waitJob } from "./api.js";
import { T, errorText, language } from "./i18n.js";
import { notesFor } from "./release-notes.js";
import { statsBox } from "./stats-box.js";
import { COLOUR, FOOTER, POPUP, STATS_BOX } from "./ui-tokens.js";
import { S, clone } from "./util.js";

const { node, sprite, label, find } = NGUI;
const checks = new WeakMap();
const RESTART_WAIT = 3000;
const NOTES_FONT = 44;

// The release's Markdown on the same UIPopup_Sort frame used by notices, in the UI language (release-notes.js): headings
// larger, list items with a hanging bullet. No HTML, images or update jobs.
const dialogTitle = (key, simulation) => T(key) + (simulation ? T("update.simulation.suffix") : "");
const HEADING_FONT = [56, 52, 48];

// the popup's lines for the notes: one fixed type size per kind, as a changelog reads; a longer line wraps instead of
// shrinking
export function notesLines(markdown, lang = language()) {
  const blocks = notesFor(typeof markdown === "string" ? markdown : "", lang);
  if (!blocks.length) return [{ text: T("update.notesEmpty"), fontSize: NOTES_FONT, minFontSize: NOTES_FONT }];
  return blocks.map((b) => {
    if (b.kind === "heading") {
      const fontSize = HEADING_FONT[Math.min(b.level, 3) - 1];
      return { text: b.text, fontSize, minFontSize: fontSize, gap: b.gap ? 32 : 0 };
    }
    const line = { text: b.text, fontSize: NOTES_FONT, minFontSize: NOTES_FONT, gap: b.gap ? 12 : 0 };
    if (b.kind === "item") return { ...line, marker: b.marker, markerGap: 44, indent: 52 + b.depth * 52 };
    return b.under != null ? { ...line, indent: 52 + b.under * 52 } : line;
  });
}

export async function releaseNotes(info, { simulation = false, onSimulatedPage } = {}) {
  const lines = notesLines(info.notes);
  const result = await Popup.message({ title: `${dialogTitle("update.notes", simulation)} - ${info.version}`,
    lines, plain: true, scroll: true, width: 2458,
    buttons: [{ label: T("update.releasePage"), value: "page" },
      { label: T("display.close"), value: "dismiss" }] });
  if (result.button === "page") {
    if (simulation) await onSimulatedPage?.();
    else await rpc("app.openReleasePage", { page: info.page });
  }
}

// Settings supplies the server's shared prerelease gate. Synthetic data never enters the updater service. With notes,
// the build's own release notes (app.releaseNotes) stand in for a newer release's, so they can be read before publishing.
export async function simulate(host, prefs, notes = true) {
  if (prefs?.prereleasePreviews !== true || checks.has(host)) return null;
  const own = notes ? await rpc("app.releaseNotes").catch(() => null) : null;
  return check(host, { current: prefs.version, version: own?.notes ? own.version : "0.6.0", installable: true,
    notes: notes ? own?.notes || T("update.simulation.sample") : "" }, { simulation: true });
}

// resolves with what closed the popup: "later" (Later or Escape), "page" (the download page opened) or "update" (a
// failed update, once dismissed). A successful retry clears that failure; Restart quits the app on success.
// null without a newer release, offline or when the popup cannot be drawn. Settings may pass its check's result.
export function check(host, info, { onCheck, onError, beforeShow, simulation = false } = {}) {
  const observe = (checked) => {
    if (onCheck || onError) checked.then(onCheck, onError).catch((err) => console.error("update check", err));
  };
  if (checks.has(host)) {
    const current = checks.get(host);
    observe(current.checked);
    return current.done;
  }
  // Reserve the host before asking the server, including checks that have not drawn their popup yet.
  const checked = Promise.resolve().then(() => info === undefined ? rpc("app.checkUpdate") : info);
  observe(checked);
  const done = checked.then(async (release) => {
    if (!release) return null;
    await beforeShow?.();
    return show(host, release, { simulation });
  }, () => null).finally(() => checks.delete(host)); // automatic checks stay quiet when offline
  checks.set(host, { checked, done });
  return done;
}

// the offer's three captions (Label_Locale widgets) on the footer's bodies in the frame (FRAME_W wide, taken to the popup
// frames' class): the popups' footer fit (popupFrame applies the same to the three choices), a long one wraps to two lines
// before it shrinks, clear of the body's edges; all three share one size
export function offerCaptions(widgets) {
  const texts = widgets.map((l) => l.text), { kind, room } = Popup.footerLayout(texts, frameGeometry(FRAME_W).w);
  return Popup.footerLabels(widgets, texts, { width: room, caption: kind.caption });
}

// the frame, 1700 wide, as high as its content needs: the light box where a message's lines start under the title, then Status
// (96 high) and the Progress bar (20 high, the lowest content), and POPUP.clearance between that and the three buttons' tops
const STATUS = { gap: 6, h: 96 };
const PROGRESS = { gap: 8, h: 20 };
const TOP = Popup.MSG.title;
const FRAME_W = 1700;
// the frame for a box boxH high: its height, the box's top and the centres of Status and Progress (popup-local y), and the
// lowest content edge
export function frameFor(boxH) {
  const below = STATUS.gap + STATUS.h + PROGRESS.gap + PROGRESS.h;
  const footer = FOOTER.centre + FOOTER.body.h * Popup.footerScale(3, FRAME_W) / 2;
  const h = Math.ceil(TOP + boxH + below + POPUP.clearance + footer);
  const boxTop = POPUP.frameY + h / 2 - TOP, boxBottom = boxTop - boxH;
  const status = boxBottom - STATUS.gap - STATUS.h / 2, progress = boxBottom - STATUS.gap - STATUS.h - PROGRESS.gap - PROGRESS.h / 2;
  return { w: FRAME_W, h, boxTop, status, progress, contentBottom: progress - PROGRESS.h / 2 };
}
// The content shown is centred in the room the frame keeps under the title: the version box alone while the offer waits,
// with Status once there is a line, with the Progress bar too while downloading (owner, 2026-11-04: no empty band under
// the box). The shift moves the whole group down from its top place.
export function contentShift({ status = false, progress = false } = {}) {
  const full = STATUS.gap + STATUS.h + PROGRESS.gap + PROGRESS.h;
  const shown = status || progress ? STATUS.gap + STATUS.h + (progress ? PROGRESS.gap + PROGRESS.h : 0) : 0;
  return (full - shown) / 2;
}
// the rows of the box: the current version in black, the latest in the success colour
export const versionRows = (info) => [{ caption: T("update.current"), value: info.current.replace(/-dev$/, "") },
  { caption: T("update.latest"), value: info.version, colour: `${COLOUR.success}/1.00` }];
// the two versions are one line each: two rows of the box's minimum
export const FRAME = frameFor(2 * STATS_BOX.rowMin + 2 * STATS_BOX.padY);

function show(host, info, options) {
  return new Promise((resolve) => {
    draw(host, info, resolve, options).catch((err) => {
      console.error("update popup", err);
      host.hidden = true;
      host.innerHTML = "";
      resolve(null);
    });
  });
}

async function draw(host, info, resolve, { simulation = false } = {}) {
  host.hidden = false;
  let ended = false;
  let phase = !simulation && info.installable && info.downloaded ? "ready" : "offer";
  let file = info.file, error = "", status = "", percent = 0, revision = 0;
  let buttons = [];
  const blocked = () => phase === "downloading" || phase === "installing";
  const close = (how) => {
    if (ended || blocked()) return false;
    ended = true;
    host.hidden = true;
    host.innerHTML = "";
    Popup.closed(host);
    resolve(how === "later" && error ? "update" : how);
    return true;
  };
  // on the popup stack from the start: Escape is Later unless a job or restart is in progress
  Popup.opened(host, () => close("later"));
  await Buttons.ready();
  await Popup.frameReady();
  if (ended) return;
  const installable = simulation || info.installable;
  // the two versions on the game's light stats box; the frame takes the height the box and the lines under it need
  const rows = versionRows(info);
  const frame = frameFor(statsBox({ holder: "frame", width: FRAME_W, rows }).height);
  const content = [
    ...statsBox({ holder: "frame", width: FRAME_W, at: [0, frame.boxTop], rows }).nodes,
    node("Status", [0, frame.status], [label("", 40, "#2d4664/1.00", 1250, STATUS.h, { depth: 8 })]),
    node("Progress", [0, frame.progress], [sprite("Common_Bg_Raius10px", 1200, PROGRESS.h, { depth: 5, color: "#c0c9d4/1.00" })], [
      node("Fill", [-600, 0], [sprite("Common_Bg_Raius10px", 1200, PROGRESS.h, { pivot: 3, depth: 6, color: `${COLOUR.success}/1.00` })]),
    ]),
  ];

  async function render() {
    const rev = ++revision, locked = blocked();
    if (locked) for (const b of buttons) b.disabled = true;
    const action = !installable ? "page" : phase === "ready" || phase === "installing" ? "restart" : "update";
    const okText = T(action === "update" ? "update.now" : `update.${action}`);
    // the shared popup frame, no close button; Later, Release Notes and the action are its three choices
    const tree = Popup.popupFrame(dialogTitle("update.title", simulation), frame.w, frame.h, { close: false, buttons: [
      { label: T("update.later"), name: "Btn_Cancel" }, { label: T("update.notes"), name: "Btn_Notes" },
      { label: okText, name: "Btn_Ok", primary: true, semantic: "update" }] });
    const body = find(tree, "Popup"), controls = find(body, "Btn");
    const text = error || status || (phase === "downloading" ? T("update.downloading", { p: percent })
      : phase === "ready" ? T("update.complete") : phase === "installing" ? T("update.restarting") : "");
    const shift = contentShift({ status: !!text, progress: phase === "downloading" });
    body.children.push(node("Content", [0, -shift], [], clone(content)));
    for (const name of ["Btn_Cancel", "Btn_Notes", "Btn_Ok"]) {
      if (locked) {
        const index = controls.children.findIndex((child) => child.name === name);
        controls.children[index] = Buttons.disable(controls.children[index]);
      }
    }
    const f = NGUI.fitText(text, 40, 30, 1250, 2);
    Object.assign(find(body, "Content/Status").widgets[0], { text: f.text, fontSize: f.fs, color: error ? `${COLOUR.error}/1.00` : "#2d4664/1.00" });
    const progress = find(body, "Content/Progress");
    progress.active = phase === "downloading";
    find(progress, "Fill").active = percent > 0;
    find(progress, "Fill").widgets[0].w_ = 1200 * percent / 100;
    const next = document.createElement("div");
    const view = await NGUI.render(tree, next, { scale: S, origin: [960, 540], z: 2000 });
    if (ended || rev !== revision) return;
    const hit = (name, fn, caption, action) => Popup.hit(next, view, `Center/Popup/Btn/${name}/BG (1)`, fn, { name: caption, action, z: 2100 });
    buttons = [hit("Btn_Cancel", () => close("later"), T("update.later"), "later"),
      hit("Btn_Notes", () => releaseNotes(info, { simulation, onSimulatedPage: async () => {
        status = T("update.simulation.pageStatus"); await render();
      } }).catch((err) => console.error("release notes", err)), T("update.notes"), "notes"),
      hit("Btn_Ok", () => act(action), okText, action)];
    for (const b of buttons) { b.disabled = locked; b.setAttribute("aria-disabled", String(locked)); }
    host.replaceChildren(...next.childNodes);
  }

  async function act(action) {
    if (ended || blocked()) return;
    if ((action === "update" && phase !== "offer") || (action === "restart" && phase !== "ready")) return;
    // Intercept every simulated action before download, staged-file handling or native installation.
    if (simulation) {
      status = T("update.simulation.status");
      await render();
      return;
    }
    if (!installable) {
      if (close("page")) rpc("app.openReleasePage", { page: info.page }).catch(() => {});
      return;
    }
    const installing = phase === "ready";
    phase = installing ? "installing" : "downloading";
    error = "";
    percent = 0;
    try {
      await render();
      if (installing) {
        if (await rpc("app.installUpdate", { file }) === false) { phase = "ready"; await render(); return; }
        await new Promise((resolve) => setTimeout(resolve, RESTART_WAIT));
        // A successful quit destroys this page. If it is still here, allow dismissal and another Restart.
        phase = "ready";
        error = T("error.update_restart_failed");
        await render();
        return;
      }
      const result = await waitJob(await rpc("app.update"), (p) => {
        const next = p.total > 0 ? Math.max(0, Math.min(100, Math.floor(p.current / p.total * 100))) : 0;
        if (next === percent) return; // the popup is drawn again only when the number changes
        percent = next;
        render().catch((err) => console.error("update progress", err));
      });
      file = result.file;
      phase = "ready";
    } catch (err) {
      phase = installing ? "ready" : "offer";
      error = errorText(err);
    }
    await render();
  }
  await render();
}
