// Loading screen, from the game's own prefabs: UILoading's black backdrop (BlackBG; the game also shows an
// illustration there, the UITexture LoadingImage, which the tool leaves empty) and UIRepeater, the "Now Loading"
// letters, three hopping dots and Arona turning round, in the bottom-right corner (app.css animates it like the
// game's clip UIAni_Repeat 1; its UIAudioPlay is never played).
// Boot keeps its opaque cover. scene(fn) and page(fn) shade the current screen while a complete destination is
// prepared; wait(promise) shades an in-page wait. There is one page overlay at a time: a newer call takes over
// the one on screen instead of flashing a new one. Writer progress has its own indicator and lifetime,
// independent of page changes.
import { loadingLabel, loadingLetterSprites, loadingInkLine, alphaBounds } from "../ui/ngui-label.js";
import { alignBaseline, domBaseline, publishAligned } from "./loading-baseline.js";
import { spriteUrl } from "../core/api.js";
import * as NGUI from "../ui/ngui.js";
import * as Popup from "../ui/popup.js";
import { T } from "../core/i18n.js";
import { fitLine, modLine } from "../core/job-progress.js";
import { S } from "../core/util.js";
import { freezeShown } from "../detail/preview-lifecycle.js";

const STAGE_UNITS_H = 1080 / S; // 1665 units: the stage is taller than the prefabs' 2960 x 1440 frame
const FADE_BLACK = 150; // a scene first fades to black; work done by then fades straight back
const FADE_OUT = 300;
const WAIT_AFTER = 300; // indicator mode: a shorter wait shows nothing
const WAIT_MIN = 500; // once shown, the indicator stays at least this long
const HARD_CAP = 15000; // warn about a slow scene; only its work or cancellation can reveal it
const EDGE_HORIZONTAL = 260,
  EDGE_SIDE = 150,
  EDGE_ALPHA = 0.6;

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const stage = () => document.getElementById("stage");

let overlay = null,
  gen = 0,
  closeSeq = 0;
let writerOverlay = null,
  writerGen = 0;
const pending = new Set();
function track(promise) {
  const work = Promise.resolve(promise);
  pending.add(work);
  const finish = () => pending.delete(work);
  work.then(finish, finish);
  return work;
}
let repeater = null,
  loadingTree = null,
  shade = null,
  geometry = null;
const letterInk = new Map();
const initialCover = document.getElementById("loading-boot");
let styled = !!initialCover && !initialCover.classList.contains("neutral"),
  assetsAllowed = styled;
let firstLoading = null;
/** The timings of the first loading screen, for the start-up report. */
export const timings = () => firstLoading || {};

// --- setup ---

/**
 * Set the loading screen up from the app's information. Before asset consent, covers use only system text and a
 * neutral backdrop.
 */
export function init(info) {
  assetsAllowed = info?.consent === true && !!info?.gameFound;
  styled = assetsAllowed;
  for (const o of [overlay, writerOverlay]) if (o && !o.done) decorate(o);
  if (!assetsAllowed || !info?.gameFound) return;
  NGUI.layout("UILoading")
    .then((tree) => {
      loadingTree = tree;
      for (const o of [overlay, writerOverlay]) if (o) queueProgress(o);
      const back = NGUI.find(tree, "BlackBG")?.widgets.find((w) => "color" in w)?.color;
      geometry = { back: back ? back.split("/")[0] : "#000000" };
      if (overlay) overlay.el.querySelector(".loading-back").style.background = geometry.back;
    })
    .catch(() => {});
}

/** Redraw the overlay's label and the fallback indicator's text once the strings are in. */
export function relabel() {
  for (const o of [overlay, writerOverlay]) {
    if (!o) continue;
    label(o.el);
    const text = o.ind.querySelector(".loading-fallback span") || o.ind.querySelector(".loading-plain span");
    if (text) text.textContent = indicatorText(o);
    o.relabelProgress?.();
  }
}

/** Whether a page overlay is up. */
export const active = () => !!overlay && !overlay.closing;
/** Whether a full cover is up. */
export const covering = () => active() && overlay.mode === "full";
/** Whether automatic popups must wait: for page work, input holds and independent writer progress. */
export const busy = () => pending.size > 0 || active() || held() || (!!writerOverlay && !writerOverlay.closing);

// How a job over mods names an alt ({alt, core}) in its progress line (job-progress.js modLine): the roster's
// names once the App has them (setNames), the key until then.
let nameOf = (entry) => entry?.alt || entry?.core || "";
/**
 * Give a job's progress line the roster's names for alts; until then it shows the key.
 * @param {(entry: {alt?: string, core?: string}) => string} fn Names an alt or core.
 */
export function setNames(fn) {
  if (typeof fn === "function") nameOf = fn;
}
/** How a job over mods names an alt ({alt, core}) in its progress line (job-progress.js modLine). */
export const altName = (entry) => nameOf(entry);

/**
 * Make a progress line: {text}, or {text, parts} for a job over mods, whose parts fitLine shortens to the
 * line's width.
 * @param {string|{text: string, parts?: object[]}|object} value A job's progress, a line, or a text.
 * @param {object} [fallback] The progress to show when the job has none.
 */
export function progressLine(value, fallback) {
  if (typeof value === "string") return { text: value };
  if (value && typeof value.text === "string") return value;
  return modLine(value, { nameOf }) || { text: progressText(value || {}, fallback) };
}

/**
 * The text of a job's progress line.
 * @param {object} [progress] The job's progress; `fraction` is the share done of the item at hand, after the
 *     line as job-progress.js's percentText writes it (whole percents, below 100 while the item runs); nothing
 *     without one.
 * @param {string} [fallback] The text when the job reports no label.
 */
export function progressText(
  { label = "", current = 0, total = 0, fraction = null } = {},
  fallback = T("progress.process", { name: T("progress.mods") }),
) {
  const text = label || fallback;
  const line = total > 0 ? T("progress.count", { label: text, current, total }) : text;
  return typeof fraction === "number" && Number.isFinite(fraction) && fraction > 0
    ? `${line} ${T("progress.percent", { percent: Math.min(99, Math.floor(fraction * 100)) })}`
    : line;
}

/**
 * Show a writer task's progress until it settles. Progress survives the sprite upgrade and belongs to this task
 * only, so a late callback cannot change a newer task's indicator.
 * @param {(update: (progress: object) => void) => Promise<*>} run The task; it reports its progress through
 *     update.
 * @param {object} [options]
 * @param {string|false|null} [options.label] The line to show while the job has not reported one; false for
 *     none.
 * @returns {Promise<*>} What the task returned.
 */
export async function task(run, { label = null } = {}) {
  const my = ++writerGen;
  const o = writerOverlay && !writerOverlay.closing ? writerOverlay : open("indicator", false, true);
  o.el.classList.add("with-progress");
  const detail = document.createElement("div");
  detail.className = "loading-progress";
  detail.style.visibility = "hidden";
  o.progress?.remove();
  o.progress = detail;
  o.progressTree = null;
  o.progressContent = null;
  o.el.appendChild(detail);
  let latest = {},
    item = null,
    best = null;
  const update = (value = latest) => {
    if (my !== writerGen || o !== writerOverlay) return;
    latest = value;
    // the fraction only grows within one item (its current / total) and starts again with the next
    const key = `${value?.current ?? ""}/${value?.total ?? ""}`,
      f = value?.fraction;
    if (key !== item) {
      item = key;
      best = null;
    }
    if (typeof f === "number" && Number.isFinite(f) && (best === null || f > best)) best = f;
    // label false: work that is no job over mods (a screen being prepared) shows no line until it reports one
    const quiet = label === false && !value?.text && !value?.label && !value?.alt && !value?.core;
    setProgress(o, quiet ? "" : progressLine(best === null ? value : { ...value, fraction: best }, label || undefined));
  };
  o.relabelProgress = () => update();
  update();
  refresh();
  decorate(o);
  try {
    return await track(run(update));
  } finally {
    detail.remove();
    if (my === writerGen && o === writerOverlay && !o.closing) {
      o.relabelProgress = null;
      await close(o);
    }
  }
}

// --- the overlay ---

const nowLoading = () => {
  const s = T("loading.now");
  return s === "loading.now" ? "" : s;
};
const indicatorText = (o) =>
  o.plain ? (T("loading.plain") === "loading.plain" ? "" : T("loading.plain")) : nowLoading();
function label(el) {
  const s = T("app.loading");
  if (s !== "app.loading") el.setAttribute("aria-label", s);
}

// a new overlay; the boot one in index.html is taken when the page starts covered
function open(mode, opaque = false, writer = false) {
  const boot = writer ? null : document.getElementById("loading-boot");
  const el = opaque && boot ? boot : document.createElement("div");
  el.removeAttribute("id");
  el.className = `loading ${mode}${styled ? "" : " neutral"}`;
  el.setAttribute("role", "status");
  label(el);
  if (!el.querySelector(".loading-ind"))
    el.innerHTML = '<div class="loading-back"></div><div class="loading-ind" aria-hidden="true"></div>';
  if (geometry) el.querySelector(".loading-back").style.background = geometry.back;
  if (!el.isConnected) stage().appendChild(el);
  if (boot && el !== boot) boot.remove();
  const o = {
    el,
    mode,
    ind: el.querySelector(".loading-ind"),
    back: el.querySelector(".loading-back"),
    shownAt: performance.now(),
    closing: false,
    done: false,
    sprites: false,
    plain: !styled,
    boot: opaque && !writer,
  };
  if (opaque) el.classList.add("on");
  else {
    void el.offsetWidth;
    el.classList.add("on");
  } // fade in from the first frame
  if (writer) writerOverlay = o;
  else overlay = o;
  if (mode === "indicator" && !writer) {
    const detail = document.createElement("div");
    detail.className = "loading-progress";
    detail.style.visibility = "hidden";
    o.el.appendChild(detail);
    o.progress = detail;
    o.relabelProgress = () => {
      setProgress(
        o,
        [...holds]
          .reverse()
          .find((hold) => hold.progress)
          ?.progress() ||
          o.waitLabel ||
          T("app.loading"),
      );
    };
    o.relabelProgress();
  }
  refresh();
  return o;
}

/** Draw the overlays again, after the screen changed under them. */
export function refresh() {
  for (const o of [overlay, writerOverlay]) o?.relabelProgress?.();
  if (overlay)
    overlay.el.style.visibility =
      overlay.mode === "indicator" && writerOverlay && !writerOverlay.closing ? "hidden" : "";
  // over a full cover (the start's), the writer shows only its progress: the cover keeps the one Now Loading
  if (writerOverlay) {
    if (covering() && !writerOverlay.closing) writerOverlay.el.classList.add("over-cover");
    else writerOverlay.el.classList.remove("over-cover");
  }
  syncLock();
}

// The progress line's width on the stage, the prefab label's (ngui-label.js) and the plain one's (app.css), less room
// for the label's outline; the plain line's font size (app.css .loading-progress).
const PROGRESS_WIDTH = 1000 - 8,
  PLAIN_FONT = 26;
// The line shortened to its width at a font size (CSS px): a job over mods shortens its names (fitLine), any other
// line is left to the label's ellipsis.
function fittedProgress(o, fontSize) {
  const text = o.progressText || "";
  if (!o.progressParts) return text;
  try {
    return fitLine(o.progressParts, (str) => NGUI.textWidth(str, fontSize), PROGRESS_WIDTH);
  } catch {
    return text;
  }
}

function setProgress(o, value) {
  const line = typeof value === "string" ? { text: value } : value || { text: "" };
  const text = line.text || "";
  if (o.progressText === text && o.progressTarget === o.progress) return;
  o.progressTarget = o.progress;
  o.progressText = text;
  o.progressParts = line.parts || null;
  o.el.setAttribute("aria-label", text);
  if (!o.progressContent) o.progress.textContent = fittedProgress(o, PLAIN_FONT);
  correctFallback(o);
  queueProgress(o);
}
function queueProgress(o) {
  o.progressDirty = true;
  o.progressVersion = (o.progressVersion || 0) + 1;
  if (o.progressWork) return o.progressWork;
  // Concurrent imports can advance several phases before one prefab label has
  // decoded. Finish that draw, then render only the latest pending snapshot.
  const work = Promise.resolve()
    .then(async () => {
      do {
        o.progressDirty = false;
        try {
          await renderProgress(o);
        } catch {
          if (o.closing || o.done || !o.progress || o.progressDirty) continue;
          o.progressContent = null;
          o.progressTree = null;
          o.progress.classList.remove("prefab-label");
          o.progress.textContent = fittedProgress(o, PLAIN_FONT);
          o.geometryFailed = true;
          correctFallback(o);
          if (!o.progressContent) o.progress.style.visibility = "";
        }
      } while (o.progressDirty && !o.closing && !o.done);
    })
    .finally(() => {
      if (o.progressWork === work) o.progressWork = null;
    });
  o.progressWork = work;
  return work;
}
async function renderProgress(o) {
  if (!assetsAllowed || !o.progress || !o.repeaterTree || o.closing) return;
  const token = (o.progressVersion = (o.progressVersion || 0) + 1);
  const text = o.progressText || "";
  const template = await NGUI.layout("UICharacterDetail");
  const style = NGUI.find(template, "Inventory_ListControl/LeftInfo/CharInfo/Char_Target/Text_Lv/Text_Lv_Exp");
  const bounds = await Promise.all(
    loadingLetterSprites(o.repeaterTree).map(async ({ w }) => {
      if (!letterInk.has(w.sprite))
        letterInk.set(
          w.sprite,
          NGUI.load(spriteUrl(w.atlas || "Common", w.sprite))
            .then((img) => {
              const c = document.createElement("canvas");
              c.width = img.naturalWidth;
              c.height = img.naturalHeight;
              const ctx = c.getContext("2d");
              ctx.drawImage(img, 0, 0);
              return alphaBounds(ctx.getImageData(0, 0, c.width, c.height));
            })
            .catch((err) => {
              letterInk.delete(w.sprite);
              throw err;
            }),
        );
      return [w.sprite, await letterInk.get(w.sprite)];
    }),
  );
  const ink = Object.fromEntries(bounds);
  const tree = loadingLabel(o.repeaterTree, style || loadingTree, text, S, ink);
  // a job over mods: its names shortened to the label's width at the label's size
  const label = tree?.widgets?.[0];
  if (label && o.progressParts) label.text = fittedProgress(o, label.fontSize * S);
  const line = tree?.inkLine ?? loadingInkLine(o.repeaterTree, S, ink);
  if (line == null) throw new Error("Loading label baseline unavailable");
  const next = document.createElement("div");
  Object.assign(next.style, { position: "absolute", inset: "0", visibility: "hidden" });
  o.el.appendChild(next); // connected and measurable, never painted before correction
  try {
    if (tree) await NGUI.render(tree, next, { scale: S, origin: [0, 0] });
    else {
      const css = getComputedStyle(o.progress);
      Object.assign(next.style, {
        inset: "auto",
        left: "84px",
        bottom: "44px",
        font: css.font,
        color: css.color,
        whiteSpace: "nowrap",
      });
      next.textContent = fittedProgress(o, PLAIN_FONT);
    }
    await document.fonts?.ready;
    if (token !== o.progressVersion || o.closing || o.done) {
      next.remove();
      return;
    }
    o.progress.classList.add("prefab-label");
    o.progress.style.transform = "";
    if (!publishAligned(o.progress, next, line, stage())) throw new Error("Loading label alignment unavailable");
  } catch (err) {
    next.remove();
    throw err;
  }
  o.progressTree = tree;
  o.inkLine = line;
  o.progressContent = next;
  o.progress.style.visibility = "";
}

function correctFallback(o) {
  if (!o.progress || o.progressContent) return;
  if (assetsAllowed && o.inkLine == null && !o.geometryFailed) return;
  try {
    const indicator = o.ind.querySelector("span"),
      rect = stage().getBoundingClientRect();
    if (!indicator || !rect.width) return;
    const line = o.inkLine ?? (domBaseline(indicator) - rect.top) / (rect.width / 1920);
    if (alignBaseline(o.progress, line, stage())) o.progress.style.visibility = "";
  } catch {
    /* a detached boot surface waits for its first measurable frame */
  }
}

function realignProgress() {
  for (const o of [overlay, writerOverlay]) {
    if (!o || o.closing) continue;
    if (o.progressContent) alignBaseline(o.progressContent, o.inkLine, stage());
    else correctFallback(o);
  }
}
globalThis.addEventListener?.("resize", realignProgress);
document.fonts?.addEventListener?.("loadingdone", realignProgress);
if (globalThis.ResizeObserver && stage()) new ResizeObserver(realignProgress).observe(stage());
if (globalThis.MutationObserver && stage())
  new MutationObserver(realignProgress).observe(stage(), { attributes: true, attributeFilter: ["style"] });

// resolves in the first frame the overlay is fully black (the fade may start late on a busy page, so it is watched,
// not timed); a page in the background, which draws no frames, gets a time limit instead
function whenOpaque(o) {
  return new Promise((resolve) => {
    const limit = setTimeout(resolve, FADE_BLACK + 1500);
    const check = () => {
      const op = Number(getComputedStyle(o.el).opacity) * Number(getComputedStyle(o.back).opacity);
      if (op >= 0.999 || o !== overlay || o.closing) {
        clearTimeout(limit);
        resolve();
      } else requestAnimationFrame(check);
    };
    check();
  });
}

// the full overlay, for a scene: the one on screen is taken over (an indicator turns black, a closing one comes back)
function cover(opaque) {
  const o = overlay;
  if (o && (!o.closing || o.mode === "full")) {
    if (o.closing) {
      o.closing = false;
      o.el.classList.remove("out");
    }
    if (o.mode !== "full") {
      o.mode = "full";
      o.el.classList.replace("indicator", "full");
    }
    o.done = false;
    refresh();
    return o;
  }
  return open("full", opaque);
}

function close(o) {
  // A scene can finish while an independent detail job still owns input. Hand
  // its cover back to the holds instead of leaving an invisible capture lock.
  if (o === overlay && held()) {
    o.mode = "indicator";
    o.holdOwned = true;
    o.done = false;
    o.closing = false;
    o.el.classList.replace("full", "indicator");
    o.el.classList.remove("out");
    decorate(o);
    refresh();
    return Promise.resolve();
  }
  o.closing = true;
  o.done = true;
  const id = ++closeSeq;
  o.closeId = id;
  o.el.classList.add("out");
  refresh();
  return sleep(FADE_OUT).then(() => {
    if (!o.closing || o.closeId !== id) return; // taken over while fading out
    if (o.boot && !firstLoading) {
      const end = performance.now(),
        sprites = o.spritesAt ?? end;
      firstLoading = { "fallback-loading": sprites - o.shownAt, "sprite-loading": end - sprites };
    }
    o.el.remove();
    if (overlay === o) overlay = null;
    if (writerOverlay === o) writerOverlay = null;
  });
}

// --- the indicator: neutral until consent and a decoded UIRepeater, stylized on subsequent covers ---

function fallback(o) {
  if (assetsAllowed) {
    // Authorization selects the real prefab, including while it is still decoding.
    // A slow or failed export must never replace it with the drawn imitation.
    o.ind.replaceChildren();
    return;
  }
  o.ind.innerHTML = '<div class="loading-plain"><span></span></div>';
  o.ind.querySelector("span").textContent = indicatorText(o);
  correctFallback(o);
}

function repeaterTree() {
  repeater ||= NGUI.init()
    .then(() => NGUI.layout("UIRepeater"))
    .catch(() => {
      repeater = null;
      return null;
    });
  return repeater;
}

// The detail prefab's translucent gradient supplies the four window edges. CSS only positions/rotates its artwork.
async function shadeEdges(o) {
  if (!assetsAllowed || o.mode !== "indicator" || o.shade) return;
  o.shade = true;
  shade ||= NGUI.init()
    .then(() => NGUI.layout("UICharacterDetail"))
    .then((tree) => NGUI.find(tree, "Inventory_ListControl/LeftInfo/CharCombatStyle/CharCombatStyleAniwidget/Bg"))
    .catch(() => {
      shade = null;
      return null;
    });
  const template = await shade;
  if (!template || o.closing || o.done || (o !== overlay && o !== writerOverlay)) {
    o.shade = false;
    return;
  }
  for (const [name, width, angle, x, y, reach, _inset] of [
    ["Bottom", 1920, 0, 960, 1080, EDGE_HORIZONTAL, EDGE_SIDE],
    ["Top", 1920, 180, 960, 0, EDGE_HORIZONTAL, EDGE_SIDE],
    ["Left", 1080, 90, 0, 540, EDGE_SIDE, EDGE_HORIZONTAL],
    ["Right", 1080, -90, 1920, 540, EDGE_SIDE, EDGE_HORIZONTAL],
  ]) {
    const host = document.createElement("div");
    host.className = "loading-edge";
    Object.assign(host.style, { left: `${x}px`, top: `${y}px`, transform: `rotate(${angle}deg)` });
    const node = structuredClone(template);
    node.name = name;
    node.pos = [0, 0];
    node.active = true;
    node.children = [];
    // flipped: the gradient's dark side sits on the window edge and fades inwards; shorter and lighter than the prefab
    const [hex, alpha = "1"] = node.widgets[0].color.split("/");
    Object.assign(node.widgets[0], {
      w_: width / S,
      h_: reach,
      pivot: 7,
      incline: 0,
      flip: (node.widgets[0].flip || 0) ^ 2,
      color: `${hex}/${(Number(alpha) * EDGE_ALPHA).toFixed(2)}`,
    });
    await NGUI.render(node, host, { scale: S, origin: [0, 0] });
    // Full native gradients overlap smoothly; polygon joins exposed diagonal cuts on scaled windows.
    if (o.closing || (o !== overlay && o !== writerOverlay)) return;
    o.el.appendChild(host);
  }
}

async function upgrade(o) {
  if (!assetsAllowed || o.sprites) return;
  o.sprites = true;
  const tree = await repeaterTree();
  if (!tree || (o !== overlay && o !== writerOverlay)) {
    o.sprites = false;
    o.geometryFailed = !tree;
    correctFallback(o);
    return;
  }
  const next = document.createElement("div");
  next.className = "loading-repeater";
  // the corner widget is anchored to the screen: moved down to the stage's bottom edge
  await NGUI.render(tree, next, {
    scale: S,
    origin: [960, 540],
    shift: { "UIRepeater/Widget": [0, -(STAGE_UNITS_H - 1440) / 2] },
  });
  const decoded = await NGUI.decoded(next, HARD_CAP);
  if (!assetsAllowed || (o !== overlay && o !== writerOverlay)) {
    o.sprites = false;
    return;
  }
  const widgets = [...next.querySelectorAll(".ngui")];
  // NGUI keeps hit geometry after image failures; it must not replace the fallback with empty widgets.
  if (!decoded || !widgets.length || widgets.some((el) => !el.style.backgroundImage && !el.textContent)) {
    o.sprites = false;
    o.geometryFailed = true;
    correctFallback(o);
    return;
  }
  o.repeaterTree = tree;
  let progress = queueProgress(o);
  do {
    await progress;
    if (progress === o.progressWork) break;
    progress = o.progressWork;
  } while (progress);
  if (o.closing || o.done || (o !== overlay && o !== writerOverlay)) {
    o.sprites = false;
    return;
  }
  o.ind.replaceChildren(next);
  styled = true;
  o.plain = false;
  o.el.classList.remove("neutral");
  o.spritesAt ??= performance.now();
}

// once the screen is black and the work goes on: the indicator in the corner, nothing in the middle
function decorate(o) {
  shadeEdges(o).catch(() => {
    o.shade = false;
  });
  if (o.plain === styled) {
    o.plain = !styled;
    o.sprites = false;
    if (o.plain) o.el.classList.add("neutral");
    else o.el.classList.remove("neutral");
    fallback(o);
  } else if (!o.ind.firstChild) fallback(o);
  else {
    const text = o.ind.querySelector("span");
    if (text) text.textContent = indicatorText(o);
  }
  upgrade(o).catch(() => {
    o.sprites = false;
    o.geometryFailed = true;
    correctFallback(o);
  });
}

// --- modes ---

/**
 * Run the work that prepares a complete destination under the loading screen: only an opaque boot waits for
 * black; other scenes keep the current frame while callers build hidden content and publish it after their
 * readiness barrier.
 * @param {object} [options]
 * @param {boolean} [options.opaque] Wait for black first (the boot).
 * @param {Promise<*>|null} [options.until] Ends the scene early when it settles.
 */
export async function scene(fn, { opaque = false, until = null } = {}) {
  if (!opaque)
    return hold(
      () => {
        const work = Promise.resolve().then(() => fn(Promise.resolve()));
        return until ? Promise.race([work, until]) : work;
      },
      { until },
    );
  const my = ++gen;
  const o = cover(opaque);
  const covered = whenOpaque(o);
  let failed = false,
    error = null,
    result;
  covered.then(() => {
    if (my === gen && !o.done) decorate(o);
  });
  try {
    const work = Promise.resolve().then(() => fn(covered));
    result = await (until ? Promise.race([work, until]) : work);
  } catch (err) {
    failed = true;
    error = err;
  }
  if (my === gen) {
    o.done = true;
    if (o === overlay && !o.closing) await close(o);
  }
  if (failed) throw error;
  return result;
}

/**
 * Show the indicator over the current screen once the wait passes WAIT_AFTER, and resolve with the result of
 * the promise.
 * @param {object} [options]
 * @param {string|null} [options.label] What the indicator says to assistive technology (the page's "loading" by
 *     default).
 * @param {number} [options.cap] How long the indicator stays at most in milliseconds (a wait known to take
 *     longer than HARD_CAP, such as the classic UI's start).
 * @param {boolean} [options.immediate] Show it at once, not after WAIT_AFTER.
 */
export async function wait(promise, { label: text = null, cap = HARD_CAP, immediate = false } = {}) {
  promise = track(promise);
  if (covering()) return promise; // a scene covers it already
  const my = ++gen;
  let settled = false;
  // opened in an animation frame: a late timer (a busy page) must not show the indicator over a change that is
  // being committed in that same frame
  const reveal = () => {
    if (my !== gen || settled) return;
    if (active()) {
      if (immediate) overlay.holdOwned = true;
      refresh();
      return;
    }
    const o = open("indicator");
    o.holdOwned = immediate;
    o.waitLabel = text;
    o.relabelProgress?.();
    decorate(o);
  };
  const timer = immediate
    ? (Promise.resolve().then(reveal), null)
    : setTimeout(() => requestAnimationFrame(reveal), WAIT_AFTER);
  let failed = false,
    error = null,
    result;
  try {
    result = cap === null ? await promise : await Promise.race([promise, sleep(cap)]);
  } catch (err) {
    failed = true;
    error = err;
  }
  settled = true;
  clearTimeout(timer);
  const o = overlay;
  if (my === gen && o && !o.closing && o.mode === "indicator" && !(o.holdOwned && held())) {
    const left = immediate ? 0 : WAIT_MIN - (performance.now() - o.shownAt);
    const finish = () => {
      if (my === gen && o === overlay && !o.closing) close(o);
    };
    if (left > 0) sleep(left).then(finish);
    else finish();
  }
  if (failed) throw error;
  return result;
}

// A write holds input from submission through the final reload. Default holds leave popups available; all blocks
// them too. Leaving the page releases its hold while the job keeps reporting its end.
const holds = new Set();
/** Whether an input hold is on. */
export const held = () => holds.size > 0;
/**
 * Run work while the screen's input is held under Now Loading.
 * @param {object} [options]
 * @param {Promise<*>|null} [options.until] Ends the hold early when it settles.
 * @param {boolean} [options.all] A strict hold: open popups are locked too, and automatic popups wait for it to
 *     end.
 * @param {Function|null} [options.progress] Receives the work's progress.
 */
export async function hold(work, { until = null, all = false, progress = null } = {}) {
  let latest = null;
  const el = stage(),
    key = { all, progress: () => (latest === null ? progress?.() || T("app.loading") : progressLine(latest)) };
  const update = (value) => {
    latest = value;
    refresh();
  };
  holds.add(key);
  if (all) el.classList.add("input-held");
  refresh();
  const release = () => {
    holds.delete(key);
    if (![...holds].some((hold) => hold.all)) el.classList.remove("input-held");
    refresh();
    if (!holds.size && overlay?.holdOwned && overlay.mode === "indicator" && !overlay.closing) close(overlay);
  };
  const task = track(Promise.resolve().then(() => work(update)));
  const visible = until ? Promise.race([task, until.then(release)]) : task;
  wait(visible, { cap: null, immediate: true }).catch(() => {});
  try {
    return await task;
  } finally {
    release();
  }
}

/**
 * Hold the screen for a read-only preview: it keeps its frame-swap guard but allows existing popups to cancel
 * or supersede the wait. Writers keep their explicit strict hold.
 */
export function page(fn, options = {}) {
  return hold(() => fn(Promise.resolve()), options);
}

/**
 * Whether Now Loading locks every control beneath it, the top bar and the bottom menu included: while a hold, a
 * page overlay or a writer's progress is up, no click, key, wheel, touch or focus reaches the screen. Open
 * popups stay usable unless a strict hold (all) covers them too; automatic popups wait for a strict hold to
 * end, so a popup is never shown where it cannot be closed.
 */
export const locked = () => held() || active() || (!!writerOverlay && !writerOverlay.closing);
const strict = () => [...holds].some((hold) => hold.all);
const exempt = (ev) => !strict() && Popup.allowsInput(ev);
// WebKit's trackpad pinch comes as gesture events, not as wheel events.
export const LOCKED_EVENTS = [
  "pointerdown",
  "pointerup",
  "pointermove",
  "mousedown",
  "mouseup",
  "click",
  "dblclick",
  "auxclick",
  "contextmenu",
  "keydown",
  "keyup",
  "wheel",
  "touchstart",
  "touchmove",
  "touchend",
  "gesturestart",
  "gesturechange",
  "gestureend",
  "dragstart",
  "drop",
];
const wired = new WeakSet();
function wire(el = stage()) {
  if (!el?.addEventListener || wired.has(el)) return;
  const block = (ev) => {
    if (!locked() || exempt(ev)) return;
    ev.preventDefault();
    ev.stopImmediatePropagation();
  };
  for (const type of LOCKED_EVENTS) el.addEventListener(type, block, { capture: true, passive: false });
  // keys with the focus outside the stage (the page's own shortcuts: arrows, Escape); system shortcuts pass
  const keys = (ev) => {
    if (ev.target && !ev.defaultPrevented && !ev.metaKey && !ev.ctrlKey) block(ev);
  };
  for (const type of ["keydown", "keyup"]) globalThis.addEventListener?.(type, keys, true);
  // focus cannot be cancelled: a control beneath that takes it while locked gives it up at once
  el.addEventListener(
    "focusin",
    (ev) => {
      if (locked() && !exempt(ev)) ev.target?.blur?.();
    },
    true,
  );
  wired.add(el);
}
const outside = new Set();
/**
 * Lock a control outside the stage the same way, whenever it was made: such controls miss the stage's listeners
 * and draw above its overlays (the native window's toolbar row, window-chrome.js, holds the top bar there). A
 * full cover hides the row (the root class, app.css), so the top bar is neither shown nor pressed over Now
 * Loading.
 * @param {HTMLElement} el The control.
 * @returns {() => void} Stops locking it.
 */
export function lockOutside(el) {
  if (!el?.addEventListener) return () => {};
  outside.add(el);
  if (!wired.has(el)) {
    const block = (ev) => {
      if (!locked() || !outside.has(el)) return;
      ev.preventDefault();
      ev.stopImmediatePropagation();
    };
    for (const type of LOCKED_EVENTS) el.addEventListener(type, block, { capture: true, passive: false });
    el.addEventListener(
      "focusin",
      (ev) => {
        if (locked() && outside.has(el)) ev.target?.blur?.();
      },
      true,
    );
    wired.add(el);
  }
  syncLock();
  return () => {
    outside.delete(el);
  };
}
// on locking: the focused control beneath gives up its focus, so Enter or Space cannot press it, and the previews
// shown beneath freeze until the lock ends
function syncLock() {
  const el = stage();
  wire(el);
  freezeShown(locked());
  document.documentElement?.classList?.toggle?.("now-loading-cover", covering());
  const focused = document.activeElement;
  if (!locked() || !focused) return;
  if (focused !== el && el?.contains?.(focused) && !exempt({ target: focused })) focused.blur?.();
  for (const row of outside) if (row.contains?.(focused)) focused.blur?.();
}
Popup.deferWhile?.(strict);

// Prefabs and derived UI values belong to the selected installation.
NGUI.onResourceChange?.(() => {
  repeater = loadingTree = shade = geometry = null;
  letterInk.clear();
});
