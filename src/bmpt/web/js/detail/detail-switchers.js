// The detail screen's switch popups on the shared list popup (switch portrait, memorial lobby, chibi) and the
// alt picker.
import { notify, closed as popupClosed, listPopup, message } from "../ui/popup.js";
import { isSpecial, specialFraming } from "./special-view.js";
import * as Compatibility from "../mods/mod-compatibility.js";
import { current as modsState, entryLabel } from "../mods/mod-switch.js";
import * as Prompts from "../app/prompts.js";
import { rpc } from "../core/api.js";
import { T, errorText, platform } from "../core/i18n.js";
import { thumbnailScope } from "./thumbs.js";
import { lazyThumbnail } from "../ui/lazy-thumbnail.js";
import { clearUnseen, hasUnseen as hasFresh } from "./unseen-assets.js";
import { modOriginalName } from "../core/names.js";
import { COLOUR } from "../ui/ui-tokens.js";
import {
  cautionKey,
  crossExtras,
  setShowOthers,
  showOthers,
  selectionError,
  intendedKey,
  isApplied,
  coreOf,
  hasAsset,
  primaryKind,
  partsOf,
  popupMode,
  tabOfKind,
} from "../mods/switch-sources.js";
import { $ } from "../core/util.js";
import {
  shownStudent,
  ctx,
  altId,
  pending,
  renderFailed,
  pageToken,
  open,
  thumb,
  chibiPicture,
  originalName,
  fullName,
  shortName,
  alt,
  isFav,
  repairedId,
  optimizeSource,
  optimizedCopy,
  baseOf,
  candidates,
  otherStudents,
  startLoose,
  readOthers,
  popupCandidate,
  crossItems,
  viewCross,
  costumesReady,
  judgeCross,
  costumeChecking,
  appliedKey,
  altBase,
  candidateLabel,
  gameFile,
  baseName,
  sizeText,
  reload,
  setPending,
  setCrossShown,
  setStartLoose,
} from "./detail.js";
import {
  busy,
  call,
  reveal,
  editModName,
  job,
  modProgressName,
  runUpdate,
  remapped,
  jumpRepaired,
  applyCandidate,
  saveCandidate,
} from "./detail-actions.js";
import { renderScreen } from "./detail-screen.js";
import { byRank, renderChips } from "./detail-chips.js";
import { albumClosed, setAlbumClosed } from "./detail-fullscreen.js";

export let pickerSel = null;

// --- the detail screen's popups, on the shared popup module (popup.js): the list popups (switch portrait or memorial
// memorial lobby, the alt picker) and the frame (compare, aliases, details); Escape and the backdrop close them ---

// switch portrait or memorial lobby: the alt's own skeleton and added ones, then the other alts' portraits;
// OK writes the chosen one into the game files (applyCandidate)
// While mods are off the popup saves the choice instead of writing it (popupMode): it starts on the configured source,
// whose card carries "Set", and its button reads Save. On an overwritten kind its cards are greyed (switch-sources.js):
// a click on one opens the entry's Add First or Overwritten message instead of choosing it.
export let popupSeg = 0,
  popupKind = "portrait",
  popupStart = null,
  switchPopupPage = null;
export let switchThumbnails = null;
export const modsOn = () => modsState()?.enabled !== false;
/**
 * Whether an entry of the current install still waits for the reapply after a game update (mods.state's install
 * ``waiting``): the game has its official files until the reapply has run, so its card says so.
 * @param {string} core The entry's core.
 */
export function waitingReapply(core, state = modsState()) {
  const install = (state?.installs || []).find((item) => item.key === state?.current);
  return !!core && (install?.waiting || []).includes(String(core).toLowerCase());
}
const GREYED_BADGE = () => ({ text: T("mods.overwritten"), color: COLOUR.greyed, ink: "#ffffff" });
export function renderPopup(scroll = 0) {
  return drawPopup(scroll).catch(renderFailed);
}
async function drawPopup(scroll = 0) {
  if (!switchThumbnails || switchPopupPage !== pageToken) return;
  const v = alt(),
    kind = popupKind,
    applied = intendedKey(v, kind);
  const mode = popupMode(shownStudent, v, kind, modsOn(), otherStudents);
  const own = candidates(v, kind);
  const item = (c) => {
    const work = switchThumbnails;
    const picture = lazyThumbnail(
      () =>
        kind === "chibi"
          ? chibiPicture(c, v, work)
          : thumb(
              baseOf(c, kind),
              kind === "portrait" ? "portrait" : "landscape",
              null,
              kind === "memorialLobby",
              specialFraming(v),
              (c.origin || c.alt || v).background,
              work,
            ),
      work.signal,
    );
    return {
      key: c.key,
      kind: c.kind,
      recruit: c.kind !== "mod" && c.recruit,
      label: candidateLabel(c, v),
      full: c.kind === "mod" ? c.label : fullName(c.alt),
      original: c.cross ? originalName(c.alt) : c.mod?.alias ? modOriginalName(c.mod) : "",
      nameEdit:
        c.kind === "mod"
          ? {
              label: T("modAlias.title"),
              fn: (s = scroll) =>
                editModName(c.mod, () => (switchPopupPage === pageToken && !$("popup").hidden ? renderPopup(s) : null)),
            }
          : null,
      // The card and info panel consume one still, including its fallback, so they cannot disagree after a retry.
      thumb: picture,
      big: () => picture.start(),
      inUse: false,
      applied: !!coreOf(v, kind) && c.key === applied,
      fav: c.fav,
      isNew: c.kind === "mod" && c.mod.fresh && c.key !== applied,
      src: c,
      inUseText: c.mod?.incomplete
        ? T("mods.incomplete")
        : coreOf(v, kind) && c.key === applied
          ? T("status.applied")
          : null,
      dim: c.greyed,
      // past-limit own costumes and another student's chibi carry their caution on hover
      // what the game may draw differently (cautionKey); past the limit, another student's chibi say more
      ...(cautionKey(c) ? { hint: T(cautionKey(c)) } : {}),
      ...(c.beyondFit ? { hint: T("chibi.beyondFit") } : {}),
      ...crossExtras(c, T("chibi.crossCharacter")),
      badge: c.greyed
        ? {
            ...GREYED_BADGE(),
            ...(!coreOf(v, kind) && c.kind === "self" ? { text: T("import.reason.no_target") } : {}),
          }
        : c.mod?.incomplete
          ? { text: T("mods.incomplete"), color: COLOUR.warning, ink: "#ffffff" }
          : coreOf(v, kind) && c.key === applied && waitingReapply(coreOf(v, kind))
            ? { text: T("mods.waitingReapply"), color: COLOUR.warning, ink: "#ffffff" }
            : null,
      tag: c.mod?.repairSourceId
        ? { text: T("remap.repaired"), color: "#d7eaf4", ink: "#2d4664" }
        : c.mod?.optimized_from
          ? { text: T("chibi.optimized"), color: "#d7eaf4", ink: "#2d4664" }
          : null,
    };
  };
  const items = own
    .map(item)
    .filter((it) => popupSeg === 0 || (popupSeg === 1) === !!it.recruit)
    .sort((a, b) => (b.fav ? 1 : 0) - (a.fav ? 1 : 0));
  // a search adds the other students' chibis after the matching cards
  const more =
    kind === "chibi" && coreOf(v, kind)
      ? (query) =>
          crossItems(
            v,
            query,
            own.map((c) => c.key),
            item,
          )
      : null;
  await listPopup({
    hostId: "popup",
    layoutKind: kind,
    title: T(isSpecial(v) ? "special.switch" : kind === "portrait" ? "popup.switch" : `row.switch.${tabOfKind(kind)}`),
    okText: T(mode.button),
    okSemantic: "save",
    cancelText: T("popup.cancel"),
    items,
    selected: pending,
    keepScroll: scroll,
    tools: {
      segs: ["seg.all", "seg.recruit", "seg.noRecruit"],
      seg: popupSeg,
      onSeg: (i) => {
        popupSeg = i;
        renderPopup();
      },
      sortKey: "sort.default",
      more,
      // the icon toggle lists every other student's chibi (an icon in the toolbar); the cards in view are
      // the ones judged
      ...(more
        ? {
            toggle: {
              label: T("chibi.showOthers"),
              checked: showOthers(),
              onChange: (on) => {
                setShowOthers(on);
                setCrossShown([]);
              },
            },
            onView: (keys) => viewCross(v, keys),
          }
        : {}),
    },
    onSelect: (key, s) => {
      const c = candidates(v, kind).find((x) => x.key === key);
      if (c?.kind === "mod") markSeen([c.mod], s);
      setPending(key);
      renderPopup(s);
    },
    onOk: () => closePopup(true),
    onClose: () => closePopup(false),
    details: (it) => {
      const c = it.src;
      const file = c.kind === "mod" ? c.mod.file : gameFile(c.alt, kind);
      const size = c.kind === "mod" ? c.mod.size : partsOf(c.alt, kind).reduce((n, f) => n + (f.size || 0), 0);
      // File and Size; no State or CRC rows (the chips say what is applied): only a problem shows, a
      // row of its own in the warning colour (no file of this kind in the game, the configured choice's error)
      const error = c.key === mode.set ? selectionError(v, kind) : null;
      const problem = [
        !coreOf(v, kind) && T("import.reason.no_target"),
        error && T(`error.${error.code}`, error.params),
      ]
        .filter(Boolean)
        .join("\n");
      // its costume check under way: Apply and Optimize Physics show the loading ring until the verdict lands
      const checking = !!coreOf(v, kind) && !c.greyed && costumeChecking(v, kind, c.key);
      return {
        title: T(
          isSpecial(v)
            ? "special.background"
            : kind === "portrait"
              ? "info.title.portrait"
              : `info.title.${tabOfKind(kind)}`,
        ),
        fields: [
          [T("field.file"), baseName(file)],
          [T("field.size"), sizeText(size)],
          ...(problem ? [[null, problem, `${COLOUR.warning}/1.00`]] : []),
        ],
        truncate: true,
        actions: [
          [
            T("popup.apply"),
            () => applyCandidate(c.key, kind),
            !coreOf(v, kind) || isApplied(v, kind, c.key) || !!c.greyed || !!c.previewOnly || !!c.pending || busy,
            "apply",
            checking
              ? null
              : c.previewOnly
                ? T("chibi.unfit")
                : c.beyondFit
                  ? T("chibi.beyondFit")
                  : cautionKey(c)
                    ? T(cautionKey(c))
                    : null,
            checking && !isApplied(v, kind, c.key),
          ],
          [T(platform === "mac" ? "open.mac" : "open.win"), () => reveal(file)],
          ...(c.kind === "mod"
            ? [
                [
                  T("compatibility.check"),
                  async () => {
                    let rebuilt = false;
                    await call(() =>
                      Compatibility.check(
                        c.id,
                        c.label,
                        () => {
                          rebuilt = true;
                          return remapped();
                        },
                        c.mod,
                        modProgressName(c),
                      ),
                    );
                    if (open && !rebuilt) await renderPopup();
                  },
                ],
                ...(repairedId(c) ? [[T("remap.go"), () => jumpRepaired(c)]] : [[T("act.update"), () => runUpdate(c)]]),
              ]
            : []),
          ...(kind === "chibi" && optimizeSource(c)
            ? [
                optimizedCopy(c, v)
                  ? [
                      T("chibi.optimizedGo"),
                      () => {
                        setPending(`m:${optimizedCopy(c, v).id}`);
                        renderPopup();
                      },
                    ]
                  : [
                      T("chibi.optimize"),
                      () => runOptimize(c, v),
                      busy || !!c.greyed || !!c.previewOnly || !!c.pending || !coreOf(v, kind),
                      "secondary",
                      checking
                        ? null
                        : c.previewOnly
                          ? T("chibi.unfit")
                          : c.beyondFit
                            ? T("chibi.beyondFit")
                            : T("chibi.optimizeHint"),
                      checking,
                    ],
              ]
            : []),
          c.kind === "mod"
            ? [
                T("btn.delete"),
                async () => {
                  if (!(await removeMod(c.id))) return;
                  await reload();
                  ctx.invalidateStudentList();
                  if (pending === c.key)
                    setPending(popupMode(shownStudent, alt(), kind, modsOn(), otherStudents).start);
                  renderPopup();
                },
              ]
            : [
                T("btn.extract"),
                () => {
                  closePopup(false);
                  job(
                    "actions.extract",
                    { target: `alt:${c.alt.key}` },
                    { name: fullName(c.alt), onDone: (r) => reveal(r?.folder) },
                  );
                },
              ],
        ],
      };
    },
  });
}
// Optimize Physics on a 3D candidate (chibi.optimize): the copy is selected, or "No optimization needed" shows
async function runOptimize(c, v = alt()) {
  const source = optimizeSource(c);
  if (!source || busy) return;
  const shown = c.kind === "self" ? shortName(v) : candidateLabel(c, v);
  let result = null;
  const ok = await job(
    "chibi.optimize",
    { alt: v.key, source, name: shown },
    {
      name: shown,
      onDone: (r) => {
        result = r;
      },
    },
  );
  if (!ok || !open || switchPopupPage !== pageToken || $("popup").hidden) return;
  // nothing to build: the official chibi needs none; a costume that no physics rule improves says it has no gain
  if (result?.unneeded)
    await message({
      title: T("chibi.optimize"),
      lines: [T(result.reason === "no_gain" ? "chibi.optimizeNoGain" : "chibi.optimizeUnneeded")],
      ok: T("picker.ok"),
    });
  else if (result?.mod) setPending(`m:${result.mod}`);
  await renderPopup($("popup").querySelector(".popup-cards")?.scrollTop || 0);
}
export async function openPopup(kind = "portrait") {
  const student = shownStudent,
    v = alt();
  if (kind === "chibi") {
    void costumesReady(v).catch((err) => console.error("Costume check failed", err));
    // the switch opens at once with the student's own cards; another student's chibi already chosen
    // gets its card, and the popup its start, once the student list is in
    if (otherStudents) judgeCross(v);
    else
      void readOthers().then((students) => {
        if (
          !students ||
          !open ||
          shownStudent !== student ||
          alt() !== v ||
          popupKind !== kind ||
          switchPopupPage !== pageToken
        )
          return;
        const start = popupMode(shownStudent, v, kind, modsOn(), otherStudents).start;
        if (pending === popupStart) setPending(start);
        popupStart = start;
        setStartLoose(false);
        void renderPopup($("popup").querySelector(".popup-cards")?.scrollTop || 0);
      });
  }
  switchThumbnails?.cancel();
  switchThumbnails = thumbnailScope({ priority: "popup" });
  switchPopupPage = pageToken;
  popupKind = kind;
  popupStart = popupMode(shownStudent, alt(), kind, modsOn(), otherStudents).start;
  setStartLoose(kind === "chibi" && !otherStudents);
  setPending(popupStart);
  popupSeg = 0;
  setCrossShown([]);
  const mods = candidates(alt(), kind)
    .filter((c) => c.kind === "mod")
    .map((c) => c.mod);
  try {
    await drawPopup();
    if (open && shownStudent === student && alt() === v && popupKind === kind && !$("popup").hidden)
      await markSeen(mods);
  } catch (err) {
    closePopup(false);
    renderFailed(err);
  }
}

const seeing = new Set();
export async function markSeen(mods, scroll = 0) {
  const ids = [...new Set(mods.filter((m) => m.fresh && !seeing.has(m.id)).map((m) => m.id))];
  if (!ids.length) return;
  const student = shownStudent;
  ids.forEach((id) => seeing.add(id));
  try {
    await rpc("mods.seen", { ids });
    clearUnseen(student, ids);
    if (shownStudent !== student) clearUnseen(shownStudent, ids);
    ctx.invalidateStudentList();
    // The save may finish after the switch closed and another popup took its host.
    if (open)
      await Promise.all([
        renderScreen(),
        renderChips(),
        switchPopupPage === pageToken && !$("popup").hidden ? renderPopup(scroll) : null,
      ]);
  } catch (err) {
    console.error("Mark mods seen failed", err);
  } finally {
    ids.forEach((id) => seeing.delete(id));
  }
}
function closePopup(apply) {
  const album = albumClosed;
  setAlbumClosed(null);
  const saved = closeSwitch(apply);
  if (album) Promise.resolve(saved).finally(album);
  return saved;
}
function closeSwitch(apply) {
  switchThumbnails?.cancel();
  switchThumbnails = null;
  switchPopupPage = null;
  setCrossShown([]);
  $("popup").hidden = true;
  $("popup").innerHTML = "";
  popupClosed($("popup"));
  const chosen = popupCandidate(alt(), popupKind, pending);
  if (popupKind === "chibi") judgeCross(alt());
  // only a choice other than the start (what the game shows; while mods are off, the configured source)
  // a start taken before the student list was in may stand for another student's chibi not listed yet: kept as it is
  if (apply && startLoose && pending === popupStart) return;
  if (apply && chosen && !chosen.greyed && !chosen.refused) return saveCandidate(pending, popupKind);
}

// The confirmation is read again when opened, so configurations in other installs are included.
async function removeMod(id) {
  try {
    const info = await rpc("mods.removalInfo", { id });
    if (info.official) return false;
    const r = await message({
      title: T("btn.delete"),
      lines: info.used ? [T("error.asset_delete_confirm")] : [T("mods.deleteConfirm", { name: info.name })],
      ok: T("btn.delete"),
      cancel: T("popup.cancel"),
    });
    if (r.button !== "ok") return false;
    return await job(
      "mods.remove",
      { id, confirm: true },
      {
        name: info.name,
        onDone: (result) => {
          if (result?.failed?.length) throw Object.assign(new Error("Asset deletion failed"), result.failed[0]);
        },
      },
    );
  } catch (err) {
    notify(errorText(err), { kind: "error", error: err });
    return false;
  }
}

// the Add First or Overwritten message of an alt's files of a kind (prompts.js); resolves with true once its mod
// was added to the library
export async function overwrittenMessage(v, kind = "portrait") {
  const page = pageToken;
  const core = coreOf(v, kind);
  const added = await Prompts.overwritten({
    name: entryLabel({ core, kind }, () => fullName(v)),
    core,
    files: partsOf(v, kind),
  });
  if (added && open && page === pageToken) {
    ctx.invalidateStudentList();
    await reload();
  }
  return added;
}

// alt picker: every alt of the student in the same list layout
let pickerSeg = 0;
function closePicker() {
  $("picker").hidden = true;
  popupClosed($("picker"));
}
export async function openPicker(scroll = 0) {
  pickerSel = pickerSel || altId;
  const items = shownStudent.alts
    .filter(hasAsset)
    .sort(byRank)
    .map((o) => ({
      key: o.key,
      kind: o.releasedOnServer ? "playable" : "other",
      recruit: o.releasedOnServer,
      label: shortName(o),
      full: fullName(o),
      thumb:
        primaryKind(o) === "chibi"
          ? null
          : thumb(
              altBase(o, primaryKind(o)),
              "card",
              null,
              primaryKind(o) === "memorialLobby",
              specialFraming(o),
              o.background,
            ),
      big: () =>
        primaryKind(o) === "chibi"
          ? null
          : thumb(
              altBase(o, primaryKind(o)),
              "bust",
              null,
              primaryKind(o) === "memorialLobby",
              specialFraming(o),
              o.background,
            ),
      inUse: false,
      fav: isFav(o),
      isNew: hasFresh(o),
      src: o,
    }))
    .filter((it) => pickerSeg === 0 || (pickerSeg === 1) === it.recruit);
  await listPopup({
    hostId: "picker",
    title: T("picker.title"),
    okText: T("picker.ok"),
    items,
    selected: pickerSel,
    keepScroll: scroll,
    tools: {
      segs: ["seg.all", "seg.recruit", "seg.noRecruit"],
      seg: pickerSeg,
      onSeg: (i) => {
        pickerSeg = i;
        openPicker();
      },
      sortKey: "sort.release",
    },
    onSelect: (key, s) => {
      pickerSel = key;
      openPicker(s);
    },
    onOk: () => {
      closePicker();
      const k = pickerSel;
      pickerSel = null;
      ctx.go(shownStudent.key, k);
    },
    onClose: () => {
      closePicker();
      pickerSel = null;
    },
    details: (it) => {
      const o = it.src,
        kind = primaryKind(o),
        file = gameFile(o, kind);
      return {
        title: T(isSpecial(o) ? "special.background" : "info.title.portrait"),
        fields: [
          [T("field.file"), coreOf(o, kind)],
          [T("info.gameFile"), o.date || "—"],
          [T("info.status"), T(appliedKey(o, kind) === "self" ? "status.original" : "status.replaced")],
          [
            T(isSpecial(o) ? "special.background" : "tab.memorialLobby"),
            T(o.memorialLobby ? "chibi.has" : "chibi.none"),
          ],
        ],
        actions: [
          [T(platform === "mac" ? "open.mac" : "open.win"), () => reveal(file)],
          [
            T("btn.extract"),
            () => {
              closePicker();
              job(
                "actions.extract",
                { target: `alt:${o.key}` },
                { name: fullName(o), onDone: (r) => reveal(r?.folder) },
              );
            },
          ],
        ],
      };
    },
  });
}

// Setters of state of detail-switchers.js for the other detail modules: an imported binding is read-only.
export function setSwitchThumbnails(value) {
  switchThumbnails = value;
}
export function setSwitchPopupPage(value) {
  switchPopupPage = value;
}
