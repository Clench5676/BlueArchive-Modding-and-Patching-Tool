// The detail screen's actions: jobs on the shown alt (update, rebuild, compatibility, remap, export), the mod
// name and favourite edits, and applying a chosen candidate.
import { notify, message, notice } from "../ui/popup.js";
import { modJob, progressName } from "../core/job-progress.js";
import * as Loading from "../app/loading.js";
import * as ModAlias from "../mods/mod-alias.js";
import * as ModExport from "../mods/mod-export.js";
import * as Compatibility from "../mods/mod-compatibility.js";
import { QUEUEABLE, refused } from "../mods/mod-switch.js";
import { rpc, spinePrefix, waitJob } from "../core/api.js";
import { T, errorText } from "../core/i18n.js";
import { forget } from "./thumbs.js";
import { isApplied, coreOf, sourceOf } from "../mods/switch-sources.js";
import { $ } from "../core/util.js";
import {
  ctx,
  shownStudent,
  altId,
  pageToken,
  open,
  fullName,
  alt,
  isFav,
  modLabel,
  repairedId,
  readStudent,
  candidates,
  judgeCross,
  unfitCostume,
  tabKind,
  switchKind,
  current,
  gameFile,
  baseName,
  renderAll,
  changeInPlace,
  renderLeft,
  reload,
  resetPreview,
  setPending,
  setFam,
} from "./detail.js";
import { renderScreen } from "./detail-screen.js";
import { popupKind, switchPopupPage, renderPopup, openPopup, overwrittenMessage } from "./detail-switchers.js";
import { resumeChanged } from "./detail-changes.js";

export let busy = false;

// --- actions ---
export async function call(fn) {
  try {
    return await fn();
  } catch (err) {
    console.error("Detail action failed", err);
    notify(err.code ? errorText(err) : T("detail.failed"), { kind: "error", error: err });
    return null;
  }
}
export async function reveal(path) {
  if (path) await call(() => rpc("os.reveal", { path }));
}
export async function copyName() {
  const name = baseName(gameFile(alt(), tabKind()));
  try {
    await navigator.clipboard.writeText(name);
  } catch (err) {
    notify(T("detail.copyFailed"), { kind: "error", error: err });
  }
}
export function editModName(mod, redraw = null) {
  const page = pageToken;
  return call(() =>
    ModAlias.open({
      mod,
      onSaved: async () => {
        ctx.invalidateStudentList();
        if (!open || page !== pageToken) return;
        await reload();
        if (open && page === pageToken) await redraw?.();
      },
    }),
  );
}
export async function toggleFav() {
  const v = alt();
  await call(() => rpc("favorites.set", { alt: v.key, on: !isFav(v) }));
  v.favorite = !isFav(v);
  ctx.invalidateStudentList();
  renderAll();
}
// Add Portrait, Add Memorial Lobby and Add Chibi open Import Mod: every mod comes in through one import.
export function addMods() {
  location.hash = "#/import";
}

// a job with a sticky progress line; the student is re-read afterwards (after(ok, result) instead, when given).
// A switch the classic UI's open window refuses opens the classic modal, which can queue it (mod-switch.js).
// An apply while mods are off only saves the choice (deferred): a notice says so ("Saved, applied when mods are on").
export async function job(method, params, options = {}) {
  const { onDone, after = () => reload(), name = fullName(alt()) } = options;
  if (busy) {
    notify(T("error.busy"), { kind: "error", error: { code: "busy" } });
    return false;
  }
  busy = true;
  const page = pageToken;
  let ok = false,
    result = null,
    refusal = null;
  const action =
    {
      "actions.apply": params.source === "official" ? "restore" : "apply",
      "actions.restore": "restore",
      "actions.update": "repair",
      "actions.extract": "extract",
      "mods.remove": "delete",
      "chibi.optimize": "optimize",
    }[method] || "process";
  const mod = shownStudent?.alts
    ?.flatMap((v) => v.modList || [])
    .find((m) => m.id === (params.mod || params.id || params.target?.replace(/^mod:/, "")));
  const targetKey = params.alt || (params.target?.startsWith("alt:") ? params.target.slice(4) : mod?.targetAlt);
  const target = shownStudent?.alts?.find((v) => v.key === targetKey) || alt();
  const kind = params.resourceType || mod?.resourceType || switchKind() || "portrait";
  // the progress line (job-progress.js modJob): the student, the kind, the mod's own name or the alt whose
  // files it takes, how far a build is (a Chibi's costume) and the step; one item, so no count
  const who = progressName(target, "", name, shownStudent);
  const sourceKey = typeof params.source === "string" && params.source.startsWith("alt:") ? params.source.slice(4) : "";
  const sourceAlt = sourceKey ? shownStudent?.alts?.find((v) => v.key === sourceKey) : null;
  const nameOf = (entry) =>
    entry?.alt === sourceKey && sourceKey ? progressName(sourceAlt, "", sourceKey, shownStudent) : who;
  const progress = modJob(
    { alt: target?.key || targetKey || kind, resourceType: kind, name: mod ? modLabel(mod) : "", source: sourceKey },
    { nameOf, action: `progress.action${action[0].toUpperCase()}${action.slice(1)}` },
  );
  const running = progress();
  try {
    return await Loading.hold(
      async (update) => {
        update(running);
        try {
          try {
            await renderScreen();
            const id = await rpc(method, params, { timeout: 30000 });
            result = await waitJob(id, (p) => {
              const text = progress(p);
              update(text);
            });
            await onDone?.(result);
            ok = true;
          } catch (err) {
            refusal = err;
          }
          if (page === pageToken && open) await after(ok, result);
          return ok;
        } finally {
          busy = false;
          if (page === pageToken && open) {
            try {
              await renderScreen();
            } catch (err) {
              notify(errorText(err), { kind: "error", error: err });
            }
          }
        }
      },
      { all: true },
    );
  } catch (err) {
    notify(errorText(err), { kind: "error", error: err });
    return false;
  } finally {
    busy = false;
    // Refusals can require an answer. Release the strict job lock before opening their controls.
    if (refusal) {
      if (page === pageToken && open && refusal.code === "official_unavailable" && QUEUEABLE.includes(method))
        await overwrittenMessage(alt(), params.resourceType || "portrait");
      // an untested game build's confirmation makes the call again, from this same page only
      else if (QUEUEABLE.includes(method))
        await refused(refusal, method, params, page === pageToken && open ? () => job(method, params, options) : null);
      else notify(errorText(refusal), { kind: "error", error: refusal });
    } else if (ok) {
      // an apply while mods are off only saves the choice, without a notice, like any other saved choice
      const lines = [result?.kept_current && T("mods.keptCurrent"), result?.incomplete && T("mods.incomplete")].filter(
        Boolean,
      );
      if (lines.length) void notice(lines.join("\n"));
    }
    resumeChanged();
  }
}
// The mod previewed on the shown tab (row 2's update and compare act on it), or null.
export function shownMod() {
  const sk = switchKind(),
    c = sk && current(sk);
  return c?.kind === "mod" ? c : null;
}
// who a mod's repair or check is (mod-compatibility.js progressTask, job-progress.js modJob)
export function modProgressName(c) {
  const mod = c.mod || c,
    target = shownStudent.alts.find((v) => v.key === mod.targetAlt) || alt();
  return {
    alt: target?.key || mod.targetAlt,
    resourceType: mod.resourceType || switchKind(),
    core: mod.core || "",
    name: c.label || "",
  };
}
export async function runUpdate(c = shownMod()) {
  const kind = switchKind();
  if (!c || busy) return;
  if (c.mod?.optimized_from) return runRebuild(c);
  const page = pageToken;
  const report = await call(() =>
    Loading.hold(() => Compatibility.forUpdate(c.id, c.label, modProgressName(c)), { all: true }),
  );
  if (!report || page !== pageToken || !open) return false;
  if (Compatibility.eligible(c.mod) && Compatibility.canRemap(c.id, c.mod)) return runRemap(c);
  // drawn on a rig the App does not have: an update would change nothing, so the result says why instead
  if (Compatibility.rigUnknown(c.id)) {
    await call(() => Compatibility.show(report));
    return false;
  }
  if (!report.assessment.available || !report.assessment.issues.length) {
    const answer = await message({
      title: T("act.update"),
      lines: [T(report.assessment.available ? "compatibility.forceUpdate" : "compatibility.updateUnchecked")],
      cancel: T("popup.cancel"),
      ok: T("act.update"),
    });
    if (answer.button !== "ok" || page !== pageToken || !open) return false;
  }
  let notice = "";
  const done = await job(
    "actions.update",
    { mod: c.id },
    {
      name: c.label,
      onDone: (result) => {
        forget(spinePrefix("mod", c.id));
        notice = Compatibility.rigNotice(result);
      },
      after: (ok) => reload(ok ? [kind] : []),
    },
  );
  // An update that kept the Mod's own skeleton says what the official one has since gained.
  if (done && notice && page === pageToken && open)
    await message({ title: T("act.update"), lines: [notice], ok: T("picker.ok") });
  return done;
}
// Update of an Optimize Physics copy: its costume built again under the current costume rule, in place; where
// the student's Chibi is configured with it, applied again
async function runRebuild(c) {
  const v = alt(),
    applied = isApplied(v, "chibi", c.key);
  const done = await job(
    "actions.update",
    { mod: c.id },
    { name: c.label, after: (ok) => reload(ok ? ["chibi"] : []) },
  );
  if (done && applied && open && alt() === v) await applyCandidate(c.key, "chibi", { again: true });
  return done;
}
export async function runCompatibility() {
  const c = shownMod();
  if (!c || busy) return;
  await call(() => Compatibility.check(c.id, c.label, remapped, c.mod, modProgressName(c)));
  if (open) await renderScreen();
}
export async function remapped() {
  ctx.invalidateStudentList();
  await reload();
}
async function runRemap(c) {
  if (!c || busy || !Compatibility.eligible(c.mod)) return;
  await call(() => Compatibility.remap(c.id, remapped, c.label, modProgressName(c), c.mod?.resourceType));
}
export async function jumpRepaired(c) {
  const id = repairedId(c);
  if (!id) return;
  if (!popupKind || $("popup").hidden) await openPopup(switchKind());
  setPending(`m:${id}`);
  await renderPopup();
}
// Go To from the update summary (update-impact.js, main.js openMod): once the mod's student page is in on the tab of
// its kind, the switch opens with the mod's card selected (its details hold Update) and in view. A request the page
// did not reach soon (a Student Hall that kept the screen, another route) is dropped.
let wanted = null;
const WANTED_MS = 30000;
/**
 * Remember a Go To request from the update summary, for takeWanted().
 * @param {{alt: string, kind?: string, id: string}|null} request The mod to show: its alt, the kind of
 *     its page and its id; null forgets the request.
 */
export function want(request) {
  wanted = request ? { ...request, at: Date.now() } : null;
}
/**
 * Act on the remembered Go To request once the mod's student page is in: open the switch of the mod's kind with
 * its card selected and in view. A request the page did not reach in time is dropped.
 * @returns {Promise<void>} Settles when the switch is open or the request is dropped.
 */
export async function takeWanted() {
  const w = wanted;
  wanted = null;
  if (!w || Date.now() - w.at > WANTED_MS || !open || altId !== w.alt) return;
  const kind = ["memorialLobby", "chibi"].includes(w.resourceType) ? w.resourceType : "portrait";
  if (switchKind() !== kind) return;
  if (popupKind !== kind || $("popup").hidden) await openPopup(kind);
  if (!open || $("popup").hidden || popupKind !== kind || altId !== w.alt) return;
  const key = `m:${w.id}`;
  if (!candidates(alt(), kind).some((c) => c.key === key)) return;
  setPending(key);
  await renderPopup();
  [...$("popup").querySelectorAll("[data-key]")]
    .find((el) => el.dataset.key === key)
    ?.scrollIntoView?.({ block: "nearest", inline: "nearest" });
}
export function runExport() {
  const c = shownMod();
  if (busy) return;
  const target = c ? `mod:${c.id}` : `alt:${alt().key}`;
  ModExport.open({ scope: "single", target, resourceType: switchKind() || "portrait" });
}
// switch: the chosen source is written into the alt's game files of that kind, so the game shows it; the library
// keeps what it replaces (an official file until it is official again, a mod in the library)
// again: the applied choice written again (a costume swap or Optimize Physics copy built under an older rule)
export async function applyCandidate(key, kind = "portrait", { again = false } = {}) {
  const v = alt();
  if (!kind || !coreOf(v, kind) || busy || (!again && isApplied(v, kind, key)) || unfitCostume(v, kind, key)) return;
  await job(
    "actions.apply",
    { alt: v.key, source: sourceOf(key), resourceType: kind },
    {
      onDone: (result) => {
        if (result?.files?.length) {
          const core = coreOf(v, kind);
          forget(spinePrefix(kind, core));
          forget(spinePrefix(`official-${kind}`, core));
        }
      },
      // the applied portrait or memorial lobby swaps in with the rest of the screen; the old one shows until then
      after: async (ok, result) => {
        ctx.invalidateStudentList();
        if (!ok) return reload();
        if (result?.unchanged) return reload();
        resetPreview(kind);
        if (!open) return null; // the screen was left meanwhile: the next show reads the student again
        return changeInPlace(async () => {
          const read = shownStudent;
          const data = await call(() => readStudent(read.key));
          if (data && shownStudent !== read) return; // the screen was read anew meanwhile
          if (data) setFam(data);
          await Promise.all([renderAll(), renderLeft()]);
        });
      },
    },
  );
  if (switchPopupPage === pageToken && !$("popup").hidden) await renderPopup();
}

export async function saveCandidate(key, kind) {
  const v = alt(),
    student = shownStudent;
  await call(() =>
    Loading.hold(
      async () => {
        const sources = await rpc("ui.preview", { alt: v.key, resourceType: kind, source: sourceOf(key) });
        ctx.invalidateStudentList();
        if (!open || shownStudent !== student || alt() !== v) return;
        v.previewSources = sources;
        if (kind === "chibi") judgeCross(v);
        resetPreview(kind);
        await changeInPlace(() => Promise.all([renderAll(), renderLeft()]));
      },
      { all: true },
    ),
  );
}
