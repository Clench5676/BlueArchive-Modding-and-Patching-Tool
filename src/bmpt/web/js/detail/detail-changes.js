// Game files switched elsewhere (the Enable Mods switch, the tray, a queued write, the classic UI closing): the
// detail screen holds the change while busy and reloads the shown student when it is quiet.
import * as Loading from "../app/loading.js";
import * as Visual from "./visual-mode.js";
import { anyOpen as popupOpen } from "../ui/popup.js";
import { spinePrefix } from "../core/api.js";
import { forget } from "./thumbs.js";
import { coreOf, partsOf, previewChanged } from "../mods/switch-sources.js";
import {
  shownStudent,
  altId,
  ctx,
  previewEnabled,
  fullscreen,
  sleep,
  renderFailed,
  open,
  alt,
  readStudent,
  tabKind,
  renderAll,
  changeInPlace,
  renderLeft,
  resetPreview,
  changing,
  setFam,
} from "./detail.js";
import { busy } from "./detail-actions.js";
import { modsOn } from "./detail-switchers.js";

// --- game files switched elsewhere: the Enable Mods switch, the tray, a queued write, the classic UI closing
// (library.changed, main.js). The student is read again, and the left side reloads the way an applied portrait or
// memorial lobby does when the shown alt's files of that kind changed. It waits while a popup or a fullscreen view is
// open, while the screen changes and while this screen's own job runs (whose reload comes first). ---
let heldChange; // undefined: nothing waiting; null: anything may have changed; {cores}: those lower-case cores
let reloadingChanged = null;
const mergeChange = (a, b) =>
  a === null || b === null ? null : a === undefined ? b : { cores: [...new Set([...a.cores, ...b.cores])] };
const lower = (core) => String(core || "").toLowerCase();
// an alt's game files of a kind as the screen judged them: they changed when this differs
const filesSig = (v, kind) =>
  JSON.stringify(
    v
      ? [
          v.configured?.[kind],
          v.selection?.[kind],
          partsOf(v, kind).map((f) => [f.file, f.applied, f.crc, f.size, f.date, f.mtime, f.official]),
        ]
      : null,
  );
const quiet = () => !fullscreen && !Visual.isOpen() && !popupOpen() && !changing() && !Loading.covering();

/**
 * Take a change of the game files made elsewhere (the Enable Mods switch, the tray, a queued write, the classic
 * UI closing) into the screen. The student is read again and the left side reloads the way an applied portrait
 * or memorial lobby does.
 * @param {{cores: string[]}|null} [change] The cores that changed; null: anything may have changed.
 * @returns {Promise<void>|undefined} Settles after queued changes have refreshed and drawn, or the screen is
 *     left; undefined when the screen is not open.
 */
export function filesChanged(change = null) {
  if (!open || !shownStudent) return;
  if (change) {
    const cores = new Set(change.cores.map(lower));
    if (
      !shownStudent.alts.some((v) =>
        [v.portrait, v.memorialLobby, v.chibi, ...(v.chibiCores || [])].some((core) => cores.has(lower(core))),
      )
    )
      return;
  }
  heldChange = mergeChange(heldChange, change && { cores: change.cores.map(lower) });
  return resumeChanged();
}
export function resumeChanged() {
  if (reloadingChanged) return reloadingChanged;
  if (heldChange === undefined || !open) return;
  reloadingChanged = (async () => {
    while (open && heldChange !== undefined) {
      while (open && (busy || !quiet())) await sleep(250);
      if (!open) return;
      await reloadChanged();
    }
  })()
    .catch((err) => console.error("reload after a switch", err))
    .finally(() => {
      reloadingChanged = null;
      resumeChanged();
    });
  return reloadingChanged;
}
async function reloadChanged() {
  while (open && !quiet()) await sleep(250);
  if (!open || busy || heldChange === undefined) return;
  const change = heldChange;
  heldChange = undefined;
  const read = shownStudent;
  const data = await readStudent(read.key).catch(renderFailed);
  if (!data || !open || shownStudent?.key !== read.key || busy) return;
  if (shownStudent !== read) {
    heldChange = mergeChange(heldChange, change);
    return;
  } // read anew meanwhile: read again after it
  // A repacked file can retain its size, date and student fields. A source event still revalidates its manifest.
  const memorialLobbyChanged = change === null || change.cores.includes(lower(alt().memorialLobby));
  const chibiChanged =
    change === null || [alt().chibi, ...(alt().chibiCores || [])].some((core) => change.cores.includes(lower(core)));
  if (
    JSON.stringify(data) === JSON.stringify(shownStudent) &&
    previewEnabled === modsOn() &&
    !memorialLobbyChanged &&
    !chibiChanged
  )
    return;
  // the thumbnails of every core of the student whose files or configured preview changed
  const changed = { portrait: false, memorialLobby: memorialLobbyChanged, chibi: chibiChanged };
  for (const v of data.alts) {
    const was = shownStudent.alts.find((x) => x.key === v.key);
    for (const kind of ["portrait", "memorialLobby", "chibi"]) {
      if (filesSig(was, kind) === filesSig(v, kind) && !previewChanged(was, v, kind, previewEnabled, modsOn()))
        continue;
      const core = coreOf(v, kind);
      if (core) {
        forget(spinePrefix(kind, core));
        forget(spinePrefix(`official-${kind}`, core));
      }
      if (v.key === altId) changed[kind] = true;
    }
  }
  ctx.invalidateStudentList();
  for (const kind of ["portrait", "memorialLobby", "chibi"]) if (changed[kind]) resetPreview(kind);
  const left = changed[tabKind()];
  await changeInPlace(async () => {
    setFam(data);
    await Promise.all([renderAll(), left ? renderLeft() : null]);
  });
}

// Setters of state of detail-changes.js for the other detail modules: an imported binding is read-only.
export function setHeldChange(value) {
  heldChange = value;
}
