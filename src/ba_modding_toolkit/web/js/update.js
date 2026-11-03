// Update popup on the game's popup frame (UIPopup_Sort): the current and the new version, Later / Update.
// Updating downloads and verifies the build; Restart installs it only after a separate confirmation.
// check() resolves once the popup has closed; automatic checks wait for beforeShow without holding a screen.
import * as Buttons from "./game-buttons.js";
import * as NGUI from "./ngui.js";
import * as Popup from "./popup.js";
import { rpc, waitJob } from "./api.js";
import { T, errorText } from "./i18n.js";
import { COLOUR } from "./ui-tokens.js";
import { S, clone } from "./util.js";

const { node, sprite, label, find } = NGUI;
const checks = new WeakMap();
const RESTART_WAIT = 3000;
const NOTES_FONT = 44;

// Literal text on the same UIPopup_Sort frame used by notices. No HTML, Markdown images or update jobs.
const dialogTitle = (key, simulation) => T(key) + (simulation ? T("update.simulation.suffix") : "");

export async function releaseNotes(info, { simulation = false, onSimulatedPage } = {}) {
  const notes = typeof info.notes === "string" ? info.notes.trim() : "";
  // one fixed type size on every line, as a changelog reads; a longer line wraps instead of shrinking
  const lines = (notes ? notes.split(/\r?\n/) : [T("update.notesEmpty")])
    .map((text) => ({ text, fontSize: NOTES_FONT, minFontSize: NOTES_FONT }));
  const result = await Popup.message({ title: `${dialogTitle("update.notes", simulation)} - ${info.version}`,
    lines, plain: true, scroll: true, width: 2458,
    buttons: [{ label: T("update.releasePage"), value: "page" },
      { label: T("display.close"), value: "dismiss" }] });
  if (result.button === "page") {
    if (simulation) await onSimulatedPage?.();
    else await rpc("app.openReleasePage", { page: info.page });
  }
}

// Settings supplies the server's shared prerelease gate. Synthetic data never enters the updater service.
export function simulate(host, prefs, notes = true) {
  if (prefs?.prereleasePreviews !== true || checks.has(host)) return Promise.resolve(null);
  return check(host, { current: prefs.version, version: "0.6.0", installable: true,
    notes: notes ? T("update.simulation.sample") : "" }, { simulation: true });
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

// the offer's three captions (Label_Locale widgets) on the slanted 496-wide bodies: the popups' footer fit (popupFrame
// applies the same to the three choices), a long one wraps to two lines before it shrinks, clear of the body's edges; all
// three share one size
export const offerCaptions = (widgets) => Popup.footerLabels(widgets, widgets.map((l) => l.text));

// the frame, 1700 wide; 940 high leaves the Progress bar's bottom (the lowest content) 33 above the three buttons' tops
const PROGRESS = { y: -160, h: 20 };
export const FRAME = { w: 1700, h: 940, contentBottom: PROGRESS.y - PROGRESS.h / 2 };

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
  // the two versions on the game's light stats box
  const rows = [[T("update.current"), info.current.replace(/-dev$/, "")], [T("update.latest"), info.version]];
  const capW = Math.max(...rows.map(([k]) => NGUI.textWidth(k, 44))) + 60;
  const content = [
    node("Box", [0, 90], [sprite("Common_Bg_Raius10px", 1300, 260, { depth: 5, color: "#ecf5f9/1.00" })], [
      node("Line", [0, 0], [sprite("Common_Line_Raius10px", 1306, 266, { depth: 4, color: "#c0c9d4/1.00" })]),
      ...rows.flatMap(([k, v], i) => [
        node(`K_${i}`, [-600, 62 - i * 124], [label(k, 44, "#2d4664/1.00", capW, 60, { pivot: 3, depth: 8 })]),
        node(`V_${i}`, [-600 + capW, 62 - i * 124], [label(v, 48, i ? `${COLOUR.success}/1.00` : "#000000/1.00", 1100 - capW, 60, { pivot: 3, depth: 8 })]),
      ]),
      node("Dots", [0, 0], [sprite("Common_Line_Dot", 1200, 4, { type: 2, depth: 7, color: "#c0c9d4/1.00" })]),
    ]),
    node("Status", [0, -94], [label("", 40, "#2d4664/1.00", 1250, 96, { depth: 8 })]),
    node("Progress", [0, PROGRESS.y], [sprite("Common_Bg_Raius10px", 1200, PROGRESS.h, { depth: 5, color: "#c0c9d4/1.00" })], [
      node("Fill", [-600, 0], [sprite("Common_Bg_Raius10px", 1200, PROGRESS.h, { pivot: 3, depth: 6, color: `${COLOUR.success}/1.00` })]),
    ]),
  ];

  async function render() {
    const rev = ++revision, locked = blocked();
    if (locked) for (const b of buttons) b.disabled = true;
    const action = !installable ? "page" : phase === "ready" || phase === "installing" ? "restart" : "update";
    const okText = T(action === "update" ? "update.now" : `update.${action}`);
    // the shared popup frame, no close button; Later, Release Notes and the action are its three choices
    const tree = Popup.popupFrame(dialogTitle("update.title", simulation), FRAME.w, FRAME.h, { close: false, buttons: [
      { label: T("update.later"), name: "Btn_Cancel" }, { label: T("update.notes"), name: "Btn_Notes" },
      { label: okText, name: "Btn_Ok", primary: true, semantic: "update" }] });
    const body = find(tree, "Popup"), controls = find(body, "Btn");
    body.children.push(...clone(content));
    for (const name of ["Btn_Cancel", "Btn_Notes", "Btn_Ok"]) {
      if (locked) {
        const index = controls.children.findIndex((child) => child.name === name);
        controls.children[index] = Buttons.disable(controls.children[index]);
      }
    }
    const text = error || status || (phase === "downloading" ? T("update.downloading", { p: percent })
      : phase === "ready" ? T("update.complete") : phase === "installing" ? T("update.restarting") : "");
    const f = NGUI.fitText(text, 40, 30, 1250, 2);
    Object.assign(find(body, "Status").widgets[0], { text: f.text, fontSize: f.fs, color: error ? `${COLOUR.error}/1.00` : "#2d4664/1.00" });
    const progress = find(body, "Progress");
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
