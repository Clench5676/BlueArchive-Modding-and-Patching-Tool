import * as ThumbnailCapture from "../thumbnail-capture/controller.js";
import * as Camera from "../memorial-lobby/memorial-lobby-camera.js";
import { notify } from "../ui/popup.js";
// Detail screen, built from the game's own prefab (UICharacterDetail) by ngui.js: the prefab supplies every frame,
// sprite, tint, incline and position; this file swaps labels and textures, hides what the tool has no use for, and adds
// what the game has no screen for (alt chips, the expression strip, the portrait and memorial lobby switchers). A
// student is one base character with all of its alt portraits. Each alt's game file carries one portrait and one
// memorial memorial lobby (its own, another alt's or an added one). Added ones belong to the alt whose game file they
// replace (mod.targetAlt); added portraits can be worn by the other alts of the student, memorial lobbies are not
// offered to them. Switching writes the chosen one into the game files (switch-sources.js, actions.apply). A change
// inside the screen prepares its parts behind the loading cover and swaps them in one frame.
import * as NGUI from "../ui/ngui.js";
import { spineAllowed } from "../spine/spine-runtime.js";
import { withPreviewTimeout, withTimeout, nextFrame } from "../core/async.js";
import { previewError } from "./preview-error.js";
import * as Loading from "../app/loading.js";
import * as ModAlias from "../mods/mod-alias.js";
import * as ActionList from "../ui/action-list.js";
import * as MemorialLobby from "../memorial-lobby/memorial-lobby.js";
import * as Visual from "./visual-mode.js";
import { anyOpen as popupOpen } from "../ui/popup.js";
import { rpc, spineBase, waitJob } from "../core/api.js";
import { L, T, language } from "../core/i18n.js";
import { thumbnailScope } from "./thumbs.js";
import { chibiThumbnail, chibiStillVersion } from "../chibi/chibi-still.js";
import { shownUnseen } from "./unseen-assets.js";
import { crossName, customAliases as aliasesOf, modName, studentName } from "../core/names.js";
import { setTitle } from "../app/nav.js";
import { KEYS, deltaOf, pacer, typing } from "../core/step-keys.js";
import { ALIAS } from "../ui/popup-frames.js";
import {
  CAUTIONS,
  CROSS_LIMIT,
  showOthers,
  availableTab,
  coreOf,
  costumeCandidates,
  costumeHold,
  crossCandidates,
  crossCard,
  crossChibis,
  hasAsset,
  hasKind,
  primaryKind,
  appliedKey as keyOf,
  candidates as listSources,
  partsOf,
  previewBase,
  previewChanged,
  previewKey,
  switchKindOf,
  tabOfKind,
} from "../mods/switch-sources.js";
import { pickerItems } from "../student-list/student-list-widgets.js";
import { $, S } from "../core/util.js";
import { blinkTimer, spineToken, loadSpine, disposePortrait, setSpineToken } from "./detail-portrait.js";
import {
  pickerSel,
  popupSeg,
  popupKind,
  switchPopupPage,
  switchThumbnails,
  modsOn,
  renderPopup,
  markSeen,
  openPicker,
  setSwitchThumbnails,
  setSwitchPopupPage,
} from "./detail-switchers.js";
import { busy, call, takeWanted } from "./detail-actions.js";
import { renderScreen } from "./detail-screen.js";
import { renderChips } from "./detail-chips.js";
import { stopActionLoad, renderExpressions } from "./detail-strip.js";
import { aliasToken, setAliasToken } from "./detail-aliases.js";
import { showMemorialLobby, refreshMemorialLobbyLines } from "./detail-memorial-lobby-tab.js";
import { showChibi, hideChibi } from "./detail-chibi-tab.js";
import { setFullscreen, visualToken, setVisualToken } from "./detail-fullscreen.js";
import { thumbnailSession, thumbnailFaces } from "./detail-thumbnail.js";
import { setHeldChange } from "./detail-changes.js";
// The parts of the screen split into the modules below stay reachable through this module.
export { want, takeWanted } from "./detail-actions.js";
export { filesChanged } from "./detail-changes.js";

export const { node, sprite, label, find, rect } = NGUI;
export const fitText = (...a) => NGUI.fitText(...a);
// fitText for a box: where nothing fits from fsMin up (fitText then gives up on one line at fsMin - 8, however wide),
// the same again from smaller sizes, down to 60 % of fsMin
export function fitIn(str, fsMax, fsMin, maxW, maxLines = 2) {
  let f = fitText(str, fsMax, fsMin, maxW, maxLines),
    min = fsMin;
  while (f.lines === 1 && f.fs < min && min - 8 >= fsMin * 0.6) {
    min -= 8;
    f = fitText(str, min + 6, min, maxW, maxLines);
  }
  return f;
}
export const ORIGIN = [960, 515]; // prefab centre; 25 px above the stage centre so the panels clear the nav bar
export const toStage = (x, y) => [ORIGIN[0] + x * S, ORIGIN[1] - y * S];
export const INFO = "RightInfo/Target/Active/1_NormalInfo";
export const stage = () => document.getElementById("stage");
// the portrait's players cover the stage (.portrait in app.css), so nothing the game shows is cut at a box edge
export const PORTRAIT_BOX = { left: 0, top: 0, width: 1920, height: 1080 };

export let ctx = null,
  shownStudent = null,
  altId = null,
  layouts = null;
// Prefabs and derived UI values belong to the selected installation.
NGUI.onResourceChange?.(() => {
  layouts = null;
  shownStudent = null;
  resetOthers();
  costumeState.clear();
  thumbnailFaces.clear();
});
export let previewEnabled = true;
export let tab = "portrait",
  fullscreen = false,
  expression = "00",
  expanded = false,
  exprScroll = 0;
export let mainPlayer = null,
  pending = null,
  memorialLobbySeq = null,
  memorialLobbyShot = null;
export let chibiShot = null,
  chibiActions = [],
  chibiAction = null,
  chibiGeneration = 0;
// every action of the chibi (clip-names.js actionsOf: its own clips by group), which the action bar shows; null: the
// chibi has none of its own (a story or an NPC chibi), and row 4 lists today's actions (chibiActions)
export let chibiMenu = null;
// three.js and the chibi viewer load with the first 3D chibi
export let chibiLib = null;
export const ChibiLib = () => (chibiLib ||= import("../chibi/chibi.js"));
export let leftToken = 0;

// --- readiness ---
export const HARD_CAP = 15000; // individual failed assets settle with an error before the screen is revealed
export const LINES_CAP = 1500; // the memorial lobby's lines from Kivo (a network call) likewise
export const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
export const frames = async (n) => {
  while (n-- > 0) await nextFrame();
};
export function renderFailed(err, source = null) {
  console.error("Detail render failed", ...(source ? [source] : []), err);
  previewError(err, source, "detail");
  if (open) notify(T("detail.failed"), { kind: "error", error: err });
}

// Every visible swap goes through commit(): at once, or, while an in-place change is prepared, queued and applied
// together with every other part of that change in one frame. A later re-render (a face card, play all) is at once.
export let batch = null;
export const commit = (fn) => (batch ? batch.push(fn) : fn());
// A render of a part is shown when it is the newest one, or, while a change is prepared, when it is newer than the
// one on screen and belongs to that change: a re-render still running at the commit (late memorial lobby lines) then
// follows it instead of keeping the old part in the commit.
export const applied = { screen: 0, chips: 0, expr: 0 };
export const showable = (part, my, latest, gen) =>
  open && (my === latest || (!!batch && gen === showGen && my > applied[part]));
// the screen is shown (from show() until hide()): what a show, a tab change or a job started for it starts nothing
// once it is hidden (the left side's players least of all)
export let open = false,
  suspended = false,
  pageToken = 0;
let leavePage = () => {};
// a generation of the screen: a newer show, tab change or hide ends the wait of the one before
export let showGen = 0,
  endWait = () => {};
export let thumbnailWork = thumbnailScope();
export const thumb = (
  base,
  frame = "bust",
  expr = null,
  memorialLobby = false,
  framing = null,
  background = alt()?.background,
  work = thumbnailWork,
) => {
  if (!base) return null;
  if (!spineAllowed()) return collectionPortrait(base);
  return work.thumb(base, frame, expr, memorialLobby, framing, frame === "face" ? null : background);
};
function collectionPortrait(base) {
  for (const v of shownStudent?.alts || []) {
    if (!v.portrait) continue;
    if (spineBase("official-portrait", v.portrait) === base) return v.officialThumbnail || null;
  }
  return null;
}
// a chibi card's still: another alt's costume shows that alt's own chibi
export const chibiPicture = (c, v = alt(), work = thumbnailWork) =>
  c.kind === "alt"
    ? chibiThumbnail(c.alt.key, "official", {
        version: chibiStillVersion(shownStudent, c.alt, chibiGeneration),
        signal: work.signal,
      })
    : c.kind !== "mod" && !coreOf(v, "chibi") && !v.fieldChibi
      ? null
      : chibiThumbnail(v.key, c.kind === "mod" ? `mod:${c.id}` : "official", {
          version: chibiStillVersion(shownStudent, v, chibiGeneration),
          signal: work.signal,
        });
function begin() {
  endWait();
  thumbnailWork.cancel();
  thumbnailWork = thumbnailScope();
  screenPics = exprPics = chipPics = [];
  const gen = ++showGen;
  return {
    gen,
    ended: new Promise((resolve) => {
      endWait = resolve;
    }),
  };
}
// work() renders every part; resolves once they are ready (or given up on). In place, their swaps are queued
// meanwhile and applied in one animation frame; a generation that was taken over drops its queue (the newer one
// renders everything again).
async function prepare({ gen, ended }, inPlace, work) {
  const queued = inPlace ? [] : null;
  batch = queued;
  try {
    await Promise.race([Promise.resolve().then(work), ended]);
  } catch (err) {
    if (gen === showGen) renderFailed(err);
  }
  if (gen !== showGen) return false;
  if (queued) {
    await nextFrame();
    if (gen !== showGen) return false;
    for (const fn of queued)
      try {
        fn();
      } catch (err) {
        console.error(err);
      }
    batch = null;
  }
  if (open && shownStudent) markSeen(shownUnseen(shownStudent, alt(), tabKind(), modsOn()));
  return true;
}
// Required UI pictures settle before publication; optional thumbnails keep their
// existing scopes and fill locally without holding the primary preview.
let screenPics = [],
  exprPics = [],
  chipPics = [];
export const picturesReady = (pictures, ...roots) => {
  const { signal } = thumbnailWork;
  // roots may be missing (a part not drawn) and descendants may lack a style (e.g. text holders): skip both
  const elements = roots.filter(Boolean).flatMap((root) => [root, ...(root.querySelectorAll?.("*") || [])]);
  return Promise.allSettled(pictures).then(() => {
    if (signal.aborted) return;
    for (const el of elements) if (el?.style?.transition) el.style.transition = "none";
    return NGUI.decoded(elements, HARD_CAP, null, { signal });
  });
};
export function fillPictures(pictures, ...roots) {
  const { signal } = thumbnailWork;
  picturesReady(pictures, ...roots).catch((error) => {
    if (!signal.aborted) renderFailed(error);
  });
}
async function settlePictures(read, signal) {
  let before;
  do {
    before = read();
    await Promise.allSettled(before.flat());
  } while (!signal.aborted && before.some((part, i) => part !== read()[i]));
}
export async function settleImages() {
  const { signal } = thumbnailWork;
  await settlePictures(() => [screenPics, exprPics, chipPics], signal);
}

// --- names, candidates, state ---
export const originalName = (v) => L(v.names)?.full ?? v.key;
export const fullName = (v) => studentName(v, originalName(v));
export const shortName = (v) => studentName(v, L(v.names)?.short ?? v.key);
// the current alt's own aliases (webui/state.py keeps the same limits): the student list search
// finds this skin by all of them; the first one is the nameplate's main name
export const ALIAS_MAX = ALIAS.max,
  ALIAS_LENGTH = 24;
export const customAliases = () => aliasesOf(alt());
export const aliasList = () => customAliases().join(/^(zh|ja)/.test(language()) ? "\u3001" : ", ");
export const alt = () => shownStudent.alts.find((v) => v.key === altId) || shownStudent.alts[0];
export const studentMods = () => shownStudent.alts.flatMap((v) => v.modList || []);
export const isFav = (v) => !!v.favorite;
export const hasMemorialLobby = (v) => hasKind(v, "memorialLobby");
export const hasChibi = (v) => hasKind(v, "chibi");
export const modLabel = (m) => modName(m, studentMods(), T);
export const repairedId = (c) =>
  c?.mod?.repairedCopyId && studentMods().some((m) => m.id === c.mod.repairedCopyId) ? c.mod.repairedCopyId : null;
export const repairAction = (c) => (repairedId(c) ? "remap.go" : "act.update");
// Optimize Physics runs on the official chibi (which needs none: its own clips move its skirt) and on another
// alt's costume; the copy it made from a costume, a chibi mod of the alt, takes its place afterwards
export const optimizeSource = (c) => (c.kind === "self" ? "official" : c.kind === "alt" ? `alt:${c.alt.key}` : null);
export const optimizedCopy = (c, v = alt()) =>
  c.kind !== "alt"
    ? null
    : studentMods().find(
        (m) => m.resourceType === "chibi" && m.targetAlt === v.key && m.optimized_from === `alt:${c.alt.key}`,
      ) || null;
// a candidate's skeleton: a mod's preview (its parts over the official ones), else the alt's own portrait or
// memorialLobby
export const baseOf = (c, kind = "portrait") =>
  c.kind === "mod" ? spineBase("mod", c.id) : c.core ? spineBase(`official-${kind}`, c.core) : null;
// The installed portrait comes from the current game files; with mods off, preview the configured source.
export const currentBase = () => altBase(alt());
// no deadline: the read waits for a job that holds the library lock, as the route's does (main.js), and the
// screen stays as it is meanwhile. A caller drops the result when the screen was read anew in the meantime (left
// and entered again, or reloaded by another action), so that an old read never replaces a newer student.
export const readStudent = (key) => rpc("student.get", { key });

// what the switch card of a kind offers (switch-sources.js), with the names and marks the cards show: the alt's
// own portrait or memorial lobby and its added ones, then for the portrait the other alts' ones and their added ones
export function candidates(v = alt(), kind = "portrait") {
  return listSources(shownStudent, v, kind, costumeState.get(v.key), otherStudents).map(named);
}
// a memorial lobby camera framed on the official art takes the name of the alt's own card, as the cards show it
const cameraLabel = (c) =>
  c.mod.camera && !c.mod.alias && !c.mod.camera.source?.startsWith("mod:") && c.origin
    ? `${shortName(c.origin)} - ${T("memorialLobby.cameraName", { n: c.mod.camera.number })}`
    : modLabel(c.mod);
const named = (c) =>
  c.kind === "mod"
    ? { ...c, label: cameraLabel(c) }
    : { ...c, label: shortName(c.alt), recruit: c.alt.releasedOnServer, fav: isFav(c.alt) };
// Switch Chibi takes any student's chibi, offered through its search only: students.list's students, read
// with the 3D tab or the popup (the one already chosen keeps its card), for the life of the screen
export let otherStudents = null,
  othersLoad = null,
  othersEpoch = 0,
  startLoose = false;
function resetOthers() {
  othersEpoch++;
  otherStudents = null;
  othersLoad = null;
  crossWanted = { alt: null, keys: [] };
  crossShown = [];
}
export function readOthers() {
  if (otherStudents || !ctx?.studentList) return Promise.resolve(otherStudents);
  const epoch = othersEpoch;
  return (othersLoad ||= Promise.resolve()
    .then(() => ctx.studentList({ stale: true }))
    .then((data) => (epoch === othersEpoch ? (otherStudents = data?.students || null) : null))
    .catch((err) => {
      console.error("StudentList read failed", err);
      return null;
    })
    .finally(() => {
      if (epoch === othersEpoch) othersLoad = null;
    }));
}
// a 3D candidate key as a card, another student's chibi found by the search included
export function popupCandidate(v, kind, key) {
  const c = candidates(v, kind).find((x) => x.key === key);
  if (c || kind !== "chibi" || typeof key !== "string" || !key.startsWith("v:")) return c;
  const o = crossChibis(otherStudents, v).find((x) => x.key === key.slice(2));
  return o ? named(crossCard(o, costumeState.get(v.key))) : undefined;
}
// The other students' chibis after the popup's own cards: the search's hits, or with "Show other students" all
// of them (and the search narrows them). By the popup's own matching (their names, keys and chibi ids) and segment, in
// the student list's name order (favourites first); a search's hits at most CROSS_LIMIT, all of them without. A
// search's hits are judged at once; all the students' chibis only as they come into view (viewCross).
export function crossItems(v, query, own, item) {
  const all = showOthers();
  if (!otherStudents || (!all && !query.trim())) {
    // today's cards only, and only the chosen pair is checked
    judgeCross(v);
    return [];
  }
  let collator = null;
  try {
    collator = new Intl.Collator(language(), { numeric: true, sensitivity: "base" });
  } catch {
    collator = null;
  }
  const name = (a, b) =>
    collator
      ? collator.compare(fullName(a), fullName(b))
      : fullName(a) < fullName(b)
        ? -1
        : fullName(a) > fullName(b)
          ? 1
          : 0;
  const hits = crossCandidates(otherStudents, v, costumeState.get(v.key), {
    match: (o) =>
      (popupSeg === 0 || (popupSeg === 1) === !!o.releasedOnServer) &&
      pickerItems(
        [
          {
            label: crossLabel(o),
            full: fullName(o),
            original: originalName(o),
            key: `v:${o.key}`,
            terms: [o.fileId, o.chibi, o.fieldChibi],
          },
        ],
        { query },
      ).length > 0,
    compare: (a, b) => isFav(b) - isFav(a) || name(a, b),
    exclude: own,
    limit: all ? Infinity : CROSS_LIMIT,
  });
  const keys = hits.map((c) => c.alt.key);
  const inView = new Set(crossShown); // the cards in view, as the list last reported them (popup.js onView)
  judgeCross(v, all ? keys.filter((key) => inView.has(key)) : keys);
  return hits.map((c) => item(named(c)));
}
// the list's cards in view (popup.js onView): with "Show other students" the chibis of other students among them
// are the ones to judge, the others dropped from the queue; a scroll brings the next ones in
let crossShown = [];
export function viewCross(v, keys) {
  const shown = keys
    .filter((key) => typeof key === "string" && key.startsWith("v:"))
    .map((key) => key.slice(2))
    .filter((key) => !shownStudent.alts.some((o) => o.key === key));
  if (shown.length === crossShown.length && shown.every((key, i) => key === crossShown[i])) return;
  crossShown = shown;
  if (showOthers() && popupKind === "chibi" && alt() === v) judgeCross(v, shown);
}
// Switch Chibi lists every costume of the student at once and lets one be applied only once that pair was built
// and exported for these files (chibi.costumes): the verdicts by alt, read when the 3D tab shows
// and checked to their end (chibi.checkCostumes) in the background, one pair at a time; each card turns applicable
// as its check ends.
export const costumeState = new Map(),
  costumeChecks = new Map();
export function costumesReady(v) {
  const run = costumeRun;
  void readOthers().then((students) => {
    if (!costumesWanted(v, run)) return;
    // a student list that cannot be read leaves another student's chibi unjudged: its loading ring stops
    if (!students) {
      costumesChecked(v);
      return;
    }
    // Loading the student list must not replace the popup's current hits with just the saved choices.
    if (switchPopupPage !== pageToken || $("popup").hidden) judgeCross(v);
    costumesChecked(v);
  });
  const others = costumeCandidates(shownStudent, v).map((o) => o.key);
  if (!others.length) return Promise.resolve(false);
  const key = `${v.key}|${others.join(",")}`;
  let check = costumeChecks.get(key);
  if (!check) {
    check = (async () => {
      const known = await rpc("chibi.costumes", { alt: v.key, others });
      costumeState.set(v.key, { ...(costumeState.get(v.key) || {}), ...known });
      void readCautions(
        v,
        others.filter((o) => known[o] === "ok" || known[o] === "preview"),
      );
      // the Apply card follows the shown costume's verdict, and an open switch its cards' verdicts
      costumesChecked(v);
      const unknown = others.filter((o) => known[o] == null);
      for (const other of unknown) {
        // the 3D tab left: no further pair (stopCostumeChecks cancelled the one under way)
        if (!costumesWanted(v, run)) return false;
        const result = await checkPair(v, other, run);
        if (!result) return false;
        costumeState.set(v.key, { ...(costumeState.get(v.key) || {}), ...result });
        void readCautions(v, [other]);
        costumesChecked(v);
      }
      return unknown.length > 0;
    })().finally(() => {
      // a run given up when the tab was left is dropped already; coming back starts another
      if (costumeChecks.get(key) === check) costumeChecks.delete(key);
      // a pair left without a verdict (its check failed) stops showing the loading ring
      if (unjudged(v, others)) costumesChecked(v);
    });
    costumeChecks.set(key, check);
  }
  return check;
}
// Another student's chibi is judged only once it shows: the chosen one, and the search's hits while they are
// hits. Their saved verdicts are read at once (chibi.costumes), the rest checked in the background one pair at a time;
// a pair no longer wanted (a new query, another alt) is not checked. Each card waits until its verdict lands.
let crossWanted = { alt: null, keys: [] },
  crossRunning = null;
const chosenCross = (v) =>
  otherStudents
    ? candidates(v, "chibi")
        .filter((c) => c.cross)
        .map((c) => c.alt.key)
    : [];
export function judgeCross(v, hits = []) {
  const keys = [...new Set([...chosenCross(v), ...hits])];
  crossWanted = { alt: v, keys };
  runCross();
}
function runCross() {
  if (crossRunning || !crossWanted.alt || !crossWanted.keys.length) return;
  const tried = new Set();
  let handled = null;
  const run = costumeRun;
  crossRunning = (async () => {
    for (;;) {
      handled = crossWanted;
      const { alt: v, keys } = crossWanted;
      if (!v || !keys.length || !costumesWanted(v, run)) return;
      const epoch = othersEpoch;
      const state = costumeState.get(v.key) || {};
      // Saved choices can add to the 64 hits. Each read still respects costume_keys' limit.
      const fresh = keys.filter((k) => !(k in state)).slice(0, CROSS_LIMIT);
      if (fresh.length) {
        const known = await rpc("chibi.costumes", { alt: v.key, others: fresh });
        if (epoch !== othersEpoch) continue;
        costumeState.set(v.key, { ...(costumeState.get(v.key) || {}), ...known });
        void readCautions(
          v,
          fresh.filter((o) => known[o] === "ok" || known[o] === "preview"),
        );
        costumesChecked(v);
        continue;
      }
      const next = keys.find((k) => !tried.has(`${v.key}|${k}`) && state[k] == null);
      if (!next) return;
      tried.add(`${v.key}|${next}`);
      const result = await checkPair(v, next, run);
      if (!result || epoch !== othersEpoch) continue;
      costumeState.set(v.key, { ...(costumeState.get(v.key) || {}), ...result });
      void readCautions(v, [next]);
      costumesChecked(v);
    }
  })()
    .catch((err) => console.error("Costume check failed", err))
    .finally(() => {
      crossRunning = null;
      if (handled !== crossWanted) runCross();
      // a wanted pair left without a verdict stops showing the loading ring
      else if (handled?.alt && unjudged(handled.alt, handled.keys)) costumesChecked(handled.alt);
    });
}
// Costume checks run only while the screen is open on the same alt with its 3D tab shown (the Switch 3D
// Chibi popup opens from that tab). Leaving it (another tab, another alt, the screen closed) stops them
// (stopCostumeChecks): no further pair is submitted, the submitted checks are cancelled (jobs.cancel: a queued
// one never runs, a running one stops between its steps and saves no verdict) and the loading ring stops. Coming
// back restarts the pairs still without a verdict.
export let costumeRun = 0;
const costumeJobs = new Set();
export const costumesWanted = (v, run) => run === costumeRun && open && !!v && alt().key === v.key && tab === "chibi";
function stopCostumeChecks() {
  costumeRun += 1;
  // an action's clips being fetched for the 3D tab are given up the same way (the request is aborted)
  stopActionLoad();
  costumeChecks.clear();
  crossWanted = { alt: null, keys: [] };
  for (const id of costumeJobs) rpc("jobs.cancel", { id }).catch(() => {});
  costumeJobs.clear();
}
// one pair checked to its end (chibi.checkCostumes): its verdicts, or null when it was cancelled because the 3D tab
// was left meanwhile
async function checkPair(v, other, run) {
  const id = await rpc("chibi.checkCostumes", { alt: v.key, others: [other] });
  if (!costumesWanted(v, run)) {
    rpc("jobs.cancel", { id }).catch(() => {});
    return null;
  }
  costumeJobs.add(id);
  try {
    return await waitJob(id);
  } catch (err) {
    if (err?.code === "cancelled") return null;
    throw err;
  } finally {
    costumeJobs.delete(id);
  }
}
// whether any of an alt's pairs (``others``: alt keys) still has no verdict
const unjudged = (v, others) => others.some((o) => (costumeState.get(v.key) || {})[o] == null);
// A 3D candidate whose costume check is still under way (no verdict yet, its check running or queued): its Apply and
// Optimize Physics wait for it and show the game's loading ring in their caption's place instead of only greying.
// ``key``: the candidate's key ("v:<alt>"); another student's chibi also waits for the student list read that queues
// it.
export function costumeChecking(v, kind, key) {
  if (kind !== "chibi" || typeof key !== "string" || !key.startsWith("v:")) return false;
  const other = key.slice(2);
  if ((costumeState.get(v.key) || {})[other] != null) return false;
  if (shownStudent.alts.some((o) => o.key === other))
    return [...costumeChecks.keys()].some((k) => k.startsWith(`${v.key}|`));
  if (!otherStudents) return !!othersLoad;
  return !!crossRunning && crossWanted.alt === v && crossWanted.keys.includes(other);
}
// the 3D tab's Apply card while the shown costume (another student's chibi) waits for its check
export const applyChecking = (v, kind) =>
  !!kind &&
  !!coreOf(v, kind) &&
  !busy &&
  costumeBlock(v, kind, shownSource(v, kind)) === "pending" &&
  costumeChecking(v, kind, shownSource(v, kind));
// The cautions of the offered pairs (chibi.costumeCautions), beside their verdicts (switch-sources.js CAUTIONS), read
// aside from the check queue; a failed read leaves them unknown, which shows no caution.
async function readCautions(v, others) {
  if (!others.length) return;
  let found = {};
  try {
    found = await rpc("chibi.costumeCautions", { alt: v.key, others });
  } catch (err) {
    console.error("Costume cautions failed", err);
  }
  if (!found || !Object.keys(found).length) return;
  const state = costumeState.get(v.key) || {};
  costumeState.set(v.key, { ...state, [CAUTIONS]: { ...(state[CAUTIONS] || {}), ...found } });
  costumesChecked(v);
}
// what keeps a 3D source from being applied by its check (switch-sources.js costumeHold): a refused costume
// ("unfit": shown, never applied), another student's chibi before its verdict ("pending")
export const costumeBlock = (v, kind, key) =>
  kind === "chibi" ? costumeHold(shownStudent, v, key, costumeState.get(v.key)) : null;
export const unfitCostume = (v, kind, key) => !!costumeBlock(v, kind, key);
// After a background check: the open Switch Chibi popup lists what it found, where it was scrolled to.
function costumesChecked(v) {
  if (open && alt() === v && tab === "chibi") void renderScreen();
  if (open && alt() === v && popupKind === "chibi" && switchPopupPage === pageToken && !$("popup").hidden)
    void renderPopup($("popup").querySelector(".popup-cards")?.scrollTop || 0);
}

// the kind of game file the shown tab is about, and the newest date of a kind's files
export const tabKind = () => switchKindOf(tab) || "portrait";
// The switch card uses the kind of the shown asset tab.
export const switchKind = () => switchKindOf(tab);
export const dateOf = (v, kind) =>
  kind === "portrait"
    ? v.date
    : partsOf(v, kind)
        .map((f) => f.date)
        .filter(Boolean)
        .sort()
        .pop();
export function crcOf(v, kind = "portrait") {
  const states = partsOf(v, kind).map((f) => f.crc);
  return states.includes("needs_fix") ? "needs_fix" : states.includes("fixed") ? "fixed" : "ok";
}
export const appliedKey = (v = alt(), kind = primaryKind(v)) => keyOf(v, kind);
export const shownSource = (v = alt(), kind = primaryKind(v)) => previewKey(v, kind, modsOn());
// the left side draws a kind it can preview: an alt's own files, a field or NPC chibi without a slot (//
// Shown, never switched), or an added mod
export const previewAvailable = (v, kind) =>
  !!coreOf(v, kind) || (kind === "chibi" && !!v?.fieldChibi) || shownSource(v, kind).startsWith("m:");
export const altBase = (v, kind = "portrait") =>
  previewAvailable(v, kind) ? previewBase(v, kind, modsOn(), shownStudent.alts) : null;
// The configured portrait or memorial lobby while mods are off; otherwise what the game files carry.
export const current = (kind = "portrait") =>
  candidates(alt(), kind).find((c) => c.key === shownSource(alt(), kind)) || candidates(alt(), kind)[0];
// another student's costume, named after the student: "Yukino (Original)" (the student's base name from the student
// list read)
const crossLabel = (o) => {
  const base = L(otherStudents?.find((f) => f.key === o.student)?.studentNames);
  // no student read: the alt's full name
  return base ? crossName(o, base, L(o.names)?.short ?? o.key, T) : fullName(o);
};
// a candidate's name on the cards: another alt's mod with that alt's name first, another student's chibi with
// the student's name first
export const candidateLabel = (c, v = alt()) =>
  c.cross
    ? crossLabel(c.alt)
    : c.kind === "mod" && !c.mod.alias && c.origin && c.origin.key !== v.key
      ? `${shortName(c.origin)} - ${c.label}`
      : c.label;
export function sourceLabel(v, kind) {
  const key = shownSource(v, kind);
  if (key === "self") return T(`${tabOfKind(kind)}.original`);
  const c = candidates(v, kind).find((x) => x.key === key);
  return c ? candidateLabel(c, v) : T("status.replaced");
}
export const gameFile = (v = alt(), kind = "portrait") => partsOf(v, kind)[0]?.file || null;
export const baseName = (path) => (path ? path.split(/[\\/]/).pop() : "—");
export const sizeText = (bytes) =>
  !bytes ? "—" : bytes < 1048576 ? `${Math.max(1, Math.round(bytes / 1024))} KB` : `${(bytes / 1048576).toFixed(1)} MB`;

const svg = (body) => `data:image/svg+xml,${encodeURIComponent(body)}`;
export const TEXTURES = {
  "svg:stop": svg(
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 48 48"><rect x="10" y="10" width="28" height="28" rx="4" fill="#2b496e"/></svg>',
  ),
  "svg:folder": svg(
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 48 48"><path fill="#2d5164" d="M5 12a3 3 0 0 1 3-3h11l4 5h17a3 3 0 0 1 3 3v19a3 3 0 0 1-3 3H8a3 3 0 0 1-3-3Z"/></svg>',
  ),
};

/**
 * Show an action card in its normal state; a disabled card takes the colour of FilterSort's disabled button.
 * @param {NguiNode|null} [disabledTemplate] FilterSort's disabled button.
 */
export function actionState(card, enabled, disabledTemplate = null) {
  find(card, "Normal").active = true;
  const lock = find(card, "Lock_Cover");
  if (lock) lock.active = false;
  if (!enabled && disabledTemplate) {
    const color = find(disabledTemplate, "BG (1)")?.widgets.find((w) => "sprite" in w)?.color;
    const body = find(card, "Bg")?.widgets.find((w) => "sprite" in w);
    if (body && color) body.color = color;
  }
  const empty = find(card, "Empty");
  if (empty) empty.active = false;
}

// an upright texture inside a slanted body is clipped to the body (the game does this with panel clipping)
export const clipped = (name, pos, url, win, img, opts = {}) =>
  node(name, pos, [
    {
      clipImage: url,
      placeholder: opts.placeholder,
      w_: win.w,
      h_: win.h,
      pivot: 4,
      depth: opts.depth ?? 9,
      placeholderRect: { x: img.dx, y: img.dy, w: img.w, h: img.h },
      incline: 10,
      radius: win.r ?? 20,
      color: opts.color ?? "#ffffff/1.00",
      tint: opts.tint,
      imgSize: "100% 100%",
      imgPosition: "0 0",
      imgLeft: `${(win.w / 2 + img.dx - img.w / 2) * S}px`,
      imgTop: `${(win.h / 2 - img.dy - img.h / 2) * S}px`,
      imgWidth: `${img.w * S}px`,
      imgHeight: `${img.h * S}px`,
      imgMask: opts.mask,
      imgEdge: opts.edge,
    },
  ]);
// a dialogue started (a card, play all, a tap on the character in fullscreen), ended or stopped. A memorial lobby that
// is hidden stops without telling (and off the memorial lobby tab nothing is followed): the tab change, the alt change
// and hide() reset what is followed here, so the next start redraws the cards and the play-all button.
let memorialLobbyAll = false;
MemorialLobby.onTalk((anim, all) => {
  if (tab !== "memorialLobby" || (memorialLobbySeq === anim && memorialLobbyAll === all)) return;
  const button = memorialLobbyAll !== all;
  memorialLobbySeq = anim;
  memorialLobbyAll = all;
  renderExpressions();
  if (button) renderScreen();
});

export async function renderAll() {
  previewEnabled = modsOn();
  await Promise.all([renderScreen(), renderChips(), renderExpressions()]);
}

// A tab or applied preview prepares under the shared in-page shade, with the input hold retained.
export function changeInPlace(work) {
  const g = begin();
  return Loading.page(
    async (covered) => {
      await covered;
      if (g.gen !== showGen) return false;
      return prepare(g, true, async () => {
        await work();
        await settleImages();
      });
    },
    { until: g.ended },
  ).then((ready) => {
    // it may have taken over a show (a tab clicked while a step loads): the screen is on that student now
    if (ready && shownStudent) arrived(shownStudent.key, askedKey);
    return ready;
  });
}

// tabs: the left side shows the portrait, the memorial lobby or the 3D chibi
export function showTab(id) {
  if (busy || Loading.held()) return;
  if (id !== "chibi") stopCostumeChecks();
  tab = id;
  if (id === "chibi") {
    const v = alt();
    void costumesReady(v).catch((err) => console.error("Costume check failed", err));
  }
  expanded = false;
  exprScroll = 0;
  memorialLobbySeq = null;
  memorialLobbyAll = false;
  return changeInPlace(() => Promise.all([renderAll(), renderLeft()]));
}

// renderLeft() resolves once the preview has drawn its first frame or failed. It loads laid out but
// transparent (.pending: a hidden canvas has no size) and is
// committed with the rest of the screen; the part it replaces fades out and is freed. The memorial lobby and the chibi
// are single players: when the shown one is replaced by another alt's, a still of it holds the place meanwhile.
const LEFT = ["portrait", "memorialLobby", "chibi"];
// the element that shows a part: its id is the part in kebab case
const partHost = (part) => $(part === "memorialLobby" ? "memorial-lobby" : part);
let shownLeft = null,
  shownKey = null;
// what the memorial lobby or the chibi shows: the same alt of another install (game folder, file source) is another one
export const leftKey = (part, v) =>
  part === "memorialLobby"
    ? `${v.memorialLobby}:${shownSource(v, "memorialLobby")}@${shownStudent.version}`
    : part === "chibi"
      ? `${v.key}:${shownSource(v, "chibi")}@${shownStudent.version}`
      : null;
export let memorialLobbyFrom = null; // the install of the memorial lobby in memorial-lobby.js, which keeps a memorial lobby shown again by its core
export let memorialLobbyStale = false; // its game files were switched: memorial-lobby.js reads them again
export function renderLeft() {
  if (!open) return Promise.resolve();
  const v = alt(),
    my = ++leftToken,
    want = tab,
    el = partHost(want),
    key = leftKey(want, v);
  const available = previewAvailable(v, switchKindOf(want));
  if (!available) {
    if (want === "portrait") disposePortrait();
    else if (want === "memorialLobby") MemorialLobby.hide(el);
    else hideChibi();
  }
  const same = want === shownLeft;
  if (!same) {
    el.hidden = false;
    el.classList.remove("leaving");
    el.classList.add("pending");
  }
  const swap = same && key !== shownKey ? freezeLeft(want) : null;
  let expired = false;
  // a newer left side or hide() came first: nothing is started
  // the preview's own work: foreground time only, without waits for the library
  const ready = withPreviewTimeout(
    Promise.resolve(swap).then(() =>
      my !== leftToken || expired || !available
        ? null
        : want === "portrait"
          ? mainPlayer
            ? null
            : loadSpine(currentBase())
          : want === "memorialLobby"
            ? showMemorialLobby(v, my, () => !expired)
            : showChibi(v, my, () => !expired),
    ),
    want === "memorialLobby" ? HARD_CAP + 3000 : HARD_CAP,
    `Detail ${want}`,
  ).catch((err) => {
    expired = true;
    if (my !== leftToken || !open) return;
    if (want === "memorialLobby") MemorialLobby.unavailable(err, el, spineBase("memorialLobby", v.memorialLobby));
    else if (want === "portrait") disposePortrait();
    else hideChibi();
    renderFailed(err);
  });
  const shown = ready.then(() =>
    commit(() => {
      if (my !== leftToken) return;
      el.hidden = false;
      el.classList.remove("pending", "leaving");
      shownLeft = want;
      shownKey = key;
      unfreezeLeft();
      if (want === "memorialLobby") MemorialLobby.reveal(); // on screen: its intro starts
      for (const part of LEFT) if (part !== want) retireLeft(part, my);
    }),
  );
  return shown;
}
// a part no longer shown: faded out, then freed (unless a newer change wants it again)
function retireLeft(part, my) {
  const el = partHost(part);
  const free = () => {
    el.hidden = true;
    el.classList.remove("pending", "leaving");
    if (part === "portrait") disposePortrait();
    else if (part === "memorialLobby") MemorialLobby.hide(el);
    else hideChibi();
  };
  if (batch || Loading.covering() || el.hidden || el.classList.contains("pending")) {
    free();
    return;
  }
  el.classList.add("leaving");
  setTimeout(() => {
    if (my === leftToken && el.classList.contains("leaving")) free();
  }, 360);
}
// a still of the shown memorial lobby or chibi over the left side, until its replacement is committed. The chibi's
// canvas keeps no drawing buffer: it is copied in an animation frame, after the chibi's own loop has drawn into it.
async function freezeLeft(part) {
  const host = partHost(part);
  const sources = [
    ...host.querySelectorAll(
      part === "memorialLobby"
        ? ".memorial-lobby-view .memorial-lobby-layer:not(.memorial-lobby-off) canvas"
        : "canvas.chibi-view",
    ),
  ];
  if (!sources.length) return;
  if (part === "chibi") await nextFrame();
  const box = document.createElement("div");
  box.className = `left-freeze ${part}`;
  Object.assign(box.style, {
    left: `${host.offsetLeft}px`,
    top: `${host.offsetTop}px`,
    width: `${host.offsetWidth}px`,
    height: `${host.offsetHeight}px`,
  });
  const c = document.createElement("canvas");
  c.width = sources[0].width;
  c.height = sources[0].height;
  const g = c.getContext("2d");
  for (const src of sources) if (src.width && src.height) g.drawImage(src, 0, 0, c.width, c.height);
  box.appendChild(c);
  if (part === "memorialLobby") {
    const frost = document.createElement("div");
    frost.className = "memorial-lobby-frost";
    box.appendChild(frost);
  }
  unfreezeLeft(true);
  $("memorial-lobby").after(box);
}
function unfreezeLeft(now = false) {
  for (const box of document.querySelectorAll(".left-freeze:not(.leaving)")) {
    if (now || batch || Loading.covering()) {
      box.remove();
      continue;
    }
    box.classList.add("leaving");
    setTimeout(() => box.remove(), 360);
  }
}

export async function reload(refresh = []) {
  const read = shownStudent;
  const data = await call(() => readStudent(read.key));
  if (data && shownStudent !== read) return; // the screen was read anew meanwhile
  const next = data?.alts.find((v) => v.key === altId) || data?.alts[0];
  const changed = new Set(refresh);
  if (data)
    for (const kind of ["portrait", "memorialLobby", "chibi"])
      if (previewChanged(alt(), next, kind, previewEnabled, modsOn())) changed.add(kind);
  if (data) shownStudent = data;
  for (const kind of changed) resetPreview(kind);
  await Promise.all([renderAll(), changed.has(tabKind()) ? renderLeft() : null]);
}

// A source changed or was rebuilt: discard the cached player or still, also when only a saved configuration changed.
export function resetPreview(kind) {
  if (kind === "memorialLobby") {
    memorialLobbyStale = true;
    memorialLobbyShot = null;
    memorialLobbySeq = null;
    memorialLobbyAll = false;
    if (shownLeft === "memorialLobby") shownKey = null;
  } else if (kind === "chibi") {
    chibiShot = null;
    chibiGeneration++;
    if (shownLeft === "chibi") shownKey = null;
  } else {
    expression = "00";
    clearTimeout(blinkTimer);
    mainPlayer = null;
  }
}

async function ensureLayouts() {
  if (layouts) return;
  const detail = await withTimeout(NGUI.layout("UICharacterDetail"), HARD_CAP, "Detail layout");
  const filter = await withTimeout(NGUI.layout("UIPopup_FilterSort"), HARD_CAP, "Disabled button layout");
  await NGUI.init().catch(renderFailed);
  layouts = {
    list: find(detail, "Inventory_ListControl"),
    textures: NGUI.textureIndex(),
    visual: null,
    disabled: find(filter, "Center/Popup/Btn_Section/Btn_Disable"),
  };
}

/**
 * Apply the settings the memorial lobby and the voices take: the voice volume and mute (development runs started with
 * --mute stay silent), the zoom-out limit and the post-processing.
 * @param {object|undefined} prefs
 */
export function applyPrefs(prefs) {
  MemorialLobby.setVolume((prefs?.voiceVolume ?? 80) / 100, ctx?.info?.mute || prefs?.voiceMuted);
  MemorialLobby.setZoomLimit(prefs?.memorialLobbyZoomOut);
  MemorialLobby.setPostProcessing(prefs?.memorialLobbyPostProcessing !== false);
  MemorialLobby.setIntro(prefs?.memorialLobbyIntro !== false);
  // started with --debug: the fullscreen memorial lobby's pointer, wheel and gesture events go to the app's log
  MemorialLobby.setTrace(ctx?.info?.debug ? (events) => rpc("debug.trace", { events }).catch(() => {}) : null);
}

/**
 * Redraw the screen's controls after a locale change, around the retained scene. It does not call show,
 * renderLeft or begin: they reset expressions, playback and capture scopes.
 * @returns {Promise<void>} Settles when the controls are redrawn.
 */
export async function relabel() {
  if (!open || !shownStudent || !layouts) return;
  const work = [renderScreen(), renderChips(), renderExpressions()];
  if (tab === "memorialLobby" && MemorialLobby.meta()) {
    // Network-backed dialogue is optional; it must not delay language selection.
    refreshMemorialLobbyLines(alt(), MemorialLobby.meta());
  }
  if (
    switchThumbnails &&
    switchPopupPage === pageToken &&
    !$("popup").hidden &&
    !$("popup").querySelector(".alias-input")
  ) {
    const scroll = $("popup").querySelector(".popup-cards");
    work.push(renderPopup(popupKind === "portrait" ? scroll?.scrollLeft || 0 : scroll?.scrollTop || 0));
  }
  if (pickerSel && !$("picker").hidden)
    work.push(openPicker($("picker").querySelector(".popup-cards")?.scrollTop || 0));
  await Promise.all(work);
}

// --- the previous and next student from the keyboard: the PC client's keys for the arrows (step-keys.js) --- A
// key steps exactly like its arrow (ctx.step: the student list's cards in its order), one step at a time: the next one
// waits until the screen shows the student the last one went to (arrived), so a held key goes at the pace the
// screen follows. The keys are the screen's own: not while a text field or an input method has them, under a
// popup, in a fullscreen view, during a job or another change of the screen, or behind the loading screen.
const blocked = () =>
  !open ||
  !shownStudent ||
  fullscreen ||
  Visual.isOpen() ||
  busy ||
  Loading.held() ||
  Loading.covering() ||
  $("screen-detail").hidden ||
  !!document.querySelector(".popup-host:not([hidden]), .consent-host:not([hidden])");
export const changing = () => !!batch; // an in-place change is being prepared (a step, a chip, a tab, an applied portrait)
// the address, as main.js routes it: what the arrows asked for
function address() {
  try {
    return location.hash.replace(/^#\/?/, "").split("/").map(decodeURIComponent);
  } catch {
    return [];
  }
}
let arrivals = [],
  askedKey = null; // askedKey: the alt the last show() was asked for, as in the address
// the screen is ready on a student (or was left: no arguments): a step waiting for the address to be shown goes on
function arrived(student, key) {
  if (!arrivals.length) return;
  const [screen, f, k] = address();
  if (student !== undefined && (screen !== "student" || f !== student || k !== key)) return; // a newer address comes
  for (const resolve of arrivals.splice(0)) resolve();
}
const keySteps = pacer(async (delta) => {
  if (blocked() || changing()) return;
  const from = location.hash;
  let resolve;
  const shown = new Promise((r) => {
    resolve = r;
    arrivals.push(r);
  });
  try {
    await ctx.step(delta, shownStudent.key, altId);
  } finally {
    // nowhere else to go (or the step failed): nothing to wait for
    if (location.hash === from) resolve();
  }
  let timer;
  await Promise.race([
    shown,
    new Promise((r) => {
      timer = setTimeout(r, HARD_CAP);
    }),
  ]); // a screen that never comes
  clearTimeout(timer);
  arrivals = arrivals.filter((r) => r !== resolve);
});
// Escape leaves fullscreen only when no popup took it or is open over the view (Escape belongs to the top layer)
addEventListener("keydown", (ev) => {
  if (
    ev.key === "Escape" &&
    !ev.defaultPrevented &&
    !popupOpen() &&
    open &&
    fullscreen &&
    !Visual.isOpen() &&
    !thumbnailSession
  ) {
    ev.preventDefault();
    void setFullscreen(false);
    return;
  }
  const delta = deltaOf(ev);
  if (!delta || typing(ev.target) || blocked() || (changing() && !keySteps.busy)) return;
  ev.preventDefault(); // the page's own use of the arrows (scrolling the unfolded strip) gives way to the step
  keySteps.press(delta, ev.repeat);
});
addEventListener("keyup", (ev) => {
  if (KEYS[ev.key]) keySteps.release(KEYS[ev.key]);
});
addEventListener("blur", () => keySteps.release());

/**
 * Open the detail screen on an alt. In place (another alt or student while the screen is shown), the
 * shown screen stays until the new one is ready and every part swaps in one frame.
 * @param {object} studentData The student, as student.get gives it.
 * @param {object} [options]
 * @param {boolean} [options.inPlace] Swap the parts in place of the shown screen.
 * @param {string|null} [options.kind] The tab to open on: "portrait", "memorialLobby" or "chibi".
 * @returns {Promise<void>} Settles once the screen is ready (the panels, primary preview and decoded UI
 *     images).
 */
export async function show(context, studentData, key, { inPlace = false, kind = null } = {}) {
  ThumbnailCapture.destroy();
  Camera.close(false);
  document.getElementById("camera-launcher")?.remove();
  ModAlias.close();
  leavePage();
  pageToken += 1;
  new Promise((resolve) => {
    leavePage = resolve;
  });
  ctx = context;
  applyPrefs(ctx.prefs);
  open = true;
  suspended = false;
  askedKey = key;
  const g = begin();
  const sameStudent = shownStudent?.key === studentData.key;
  // the alias editor of another alt goes (a history step while it was open)
  if ((!sameStudent || altId !== key) && $("popup").querySelector(".alias-input")) {
    setAliasToken(aliasToken + 1);
    $("popup").hidden = true;
    $("popup").innerHTML = "";
  }
  // the same alt of another install (another game folder or file source) is read again like another alt
  const sameAlt = sameStudent && altId === key && shownStudent.version === studentData.version;
  shownStudent = studentData;
  altId = shownStudent.alts.some((v) => v.key === key && hasAsset(v))
    ? key
    : (shownStudent.alts.find(hasAsset) || shownStudent.alts[0]).key;
  if (!sameAlt) {
    expression = "00";
    expanded = false;
    memorialLobbyShot = null;
    memorialLobbySeq = null;
    memorialLobbyAll = false;
    chibiShot = null;
    chibiActions = [];
    chibiMenu = null;
    exprScroll = 0;
    ActionList.close();
    chibiAction = null;
    // a new portrait is loaded; the shown one stays until it is committed
    clearTimeout(blinkTimer);
    mainPlayer = null;
  }
  // the tabs the alt has, on every show: another install may lack a part under the same alt
  tab = availableTab(
    alt(),
    {
      portrait: "portrait",
      memorialLobby: "memorialLobby",
      chibi: "chibi",
    }[kind] || tab,
  );
  // the costume checks of another alt, or of a 3D tab no longer shown, stop; the same 3D tab's go on
  if (!sameAlt || tab !== "chibi") stopCostumeChecks();
  previewEnabled = modsOn();
  rpc("ui.visit", { alt: altId }).catch(() => {});
  // The portrait uses its own assets and default framing while the UI layout is still loading or unavailable.
  const ready = await prepare(g, true, async () => {
    commit(() =>
      setTitle(
        T("top.students"),
        ctx.back ||
          (() => {
            location.hash = "#/student-list";
          }),
      ),
    );
    await Promise.all([
      renderLeft(),
      ensureLayouts()
        .then(() => {
          if (g.gen === showGen) return renderAll();
        })
        .catch(renderFailed),
    ]);
    await settleImages();
  });
  if (ready) arrived(studentData.key, key);
  if (ready && open) void takeWanted().catch(renderFailed);
  // a 3D tab entered directly (a link, a reload) reads and checks the costume verdicts as a click on the tab does,
  // once the screen is drawn (their arrival redraws it)
  if (ready && open && tab === "chibi")
    void costumesReady(alt()).catch((err) => console.error("Costume check failed", err));
  // the server's index of the memorial lobbies' prefabs takes seconds to build the first time: built in the background
  // once the screen is in, so the memorial lobby tab does not wait for it
  if (open && hasMemorialLobby(alt())) rpc("memorialLobby.warm").catch(() => {});
}

/** Tell the screen a route to a student failed: a key step waiting for that screen goes on at once. */
export function stepFailed() {
  arrived();
}

/**
 * The alt actually shown, including the fallback for a student-only or unavailable-alt route.
 * @returns {string|null} The alt's key, or null when the screen is not shown.
 */
export const currentAlt = () => ((open || suspended) && shownStudent ? alt().key : null);

/**
 * Release page work before a destination waits on library locks, keeping the complete current frame until that
 * destination can replace it.
 */
export function suspend() {
  ThumbnailCapture.destroy();
  Camera.close(false);
  document.getElementById("camera-launcher")?.remove();
  switchThumbnails?.cancel();
  setSwitchThumbnails(null);
  ModAlias.close();
  leavePage();
  open = false;
  suspended = true;
  stopCostumeChecks();
  begin();
  batch = null;
  leftToken += 1;
  setSpineToken(spineToken + 1);
}

/** Leave the screen: stop the players and drop any change being prepared. */
export function hide() {
  ThumbnailCapture.destroy();
  Camera.close(false);
  document.getElementById("camera-launcher")?.remove();
  switchThumbnails?.cancel();
  setSwitchThumbnails(null);
  ModAlias.close();
  ActionList.close();
  leavePage();
  pageToken += 1;
  open = false;
  suspended = false;
  setSwitchPopupPage(null);
  stopCostumeChecks();
  resetOthers();
  begin();
  batch = null;
  leftToken += 1;
  setVisualToken(visualToken + 1);
  Visual.close({ instant: true });
  if (fullscreen) setFullscreen(false);
  disposePortrait();
  MemorialLobby.hide($("memorial-lobby"));
  memorialLobbySeq = null;
  memorialLobbyAll = false;
  hideChibi();
  unfreezeLeft(true);
  shownLeft = shownKey = null;
  for (const part of LEFT) {
    partHost(part).hidden = true;
    partHost(part).classList.remove("pending", "leaving");
  }
  for (const id of ["popup", "picker"]) {
    $(id).hidden = true;
    $(id).innerHTML = "";
  }
  keySteps.clear();
  setHeldChange(undefined); // the next show reads the student anyway
  arrived();
}

$("fs-exit")?.addEventListener("click", () => setFullscreen(false));

// Setters of the screen's state for the other detail modules: an imported binding is read-only.
export function setScreenPics(value) {
  screenPics = value;
}
export function setPending(value) {
  pending = value;
}
export function setFam(value) {
  shownStudent = value;
}
export function setChipPics(value) {
  chipPics = value;
}
export function setChibiAction(value) {
  chibiAction = value;
}
export function setExpressionState(value) {
  expression = value;
}
export function setExpanded(value) {
  expanded = value;
}
export function setExprScroll(value) {
  exprScroll = value;
}
export function setExprPics(value) {
  exprPics = value;
}
export function setMainPlayer(value) {
  mainPlayer = value;
}
export function setCrossShown(value) {
  crossShown = value;
}
export function setStartLoose(value) {
  startLoose = value;
}
export function setMemorialLobbyFrom(value) {
  memorialLobbyFrom = value;
}
export function setMemorialLobbyStale(value) {
  memorialLobbyStale = value;
}
export function setMemorialLobbyShot(value) {
  memorialLobbyShot = value;
}
export function setChibiActions(value) {
  chibiActions = value;
}
export function setChibiMenu(value) {
  chibiMenu = value;
}
export function setChibiShot(value) {
  chibiShot = value;
}
export function setFullscreenState(value) {
  fullscreen = value;
}
export function setTab(value) {
  tab = value;
}
export function setShownLeft(value) {
  shownLeft = value;
}
export function setShownKey(value) {
  shownKey = value;
}
