// The detail screen's actions: jobs on the shown variant (update, rebuild, compatibility, remap, export), the mod
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
  fam,
  variantKey,
  pageToken,
  open,
  fullName,
  variant,
  isFav,
  modLabel,
  repairedId,
  readFamily,
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
  const name = baseName(gameFile(variant(), tabKind()));
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
        ctx.invalidateRoster();
        if (!open || page !== pageToken) return;
        await reload();
        if (open && page === pageToken) await redraw?.();
      },
    }),
  );
}
export async function toggleFav() {
  const v = variant();
  await call(() => rpc("favorites.set", { variant: v.key, on: !isFav(v) }));
  v.favorite = !isFav(v);
  ctx.invalidateRoster();
  renderAll();
}
// Add Portrait, Add Memorial Lobby and Add 3D Model open Import Mod: every mod comes in through one import.
export function addMods() {
  location.hash = "#/import";
}

// a job with a sticky progress line; the family is re-read afterwards (after(ok, result) instead, when given).
// A switch the classic UI's open window refuses opens the classic modal, which can queue it (mod-switch.js).
// An apply while mods are off only saves the choice (deferred): a notice says so ("Saved, applied when mods are on").
export async function job(method, params, options = {}) {
  const { onDone, after = () => reload(), name = fullName(variant()) } = options;
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
      "model.optimize": "optimize",
    }[method] || "process";
  const mod = fam?.variants
    ?.flatMap((v) => v.modList || [])
    .find((m) => m.id === (params.mod || params.id || params.target?.replace(/^mod:/, "")));
  const targetKey = params.variant || (params.target?.startsWith("variant:") ? params.target.slice(8) : mod?.target);
  const target = fam?.variants?.find((v) => v.key === targetKey) || variant();
  const kind = params.kind || mod?.kind || switchKind() || "spr";
  // the progress line (job-progress.js modJob): the student, the kind, the mod's own name or the variant whose
  // files it takes, how far a build is (a 3D model's costume) and the step; one item, so no count
  const who = progressName(target, "", name, fam);
  const sourceKey =
    typeof params.source === "string" && params.source.startsWith("variant:") ? params.source.slice(8) : "";
  const sourceVariant = sourceKey ? fam?.variants?.find((v) => v.key === sourceKey) : null;
  const nameOf = (entry) =>
    entry?.variant === sourceKey && sourceKey ? progressName(sourceVariant, "", sourceKey, fam) : who;
  const progress = modJob(
    { variant: target?.key || targetKey || kind, kind, name: mod ? modLabel(mod) : "", source: sourceKey },
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
      if (page === pageToken && open && refusal.code === "original_unavailable" && QUEUEABLE.includes(method))
        await overwrittenMessage(variant(), params.kind || "spr");
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
    target = fam.variants.find((v) => v.key === mod.target) || variant();
  return {
    variant: target?.key || mod.target,
    kind: mod.kind || switchKind(),
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
// the student's model is configured with it, applied again
async function runRebuild(c) {
  const v = variant(),
    applied = isApplied(v, "model", c.key);
  const done = await job(
    "actions.update",
    { mod: c.id },
    { name: c.label, after: (ok) => reload(ok ? ["model"] : []) },
  );
  if (done && applied && open && variant() === v) await applyCandidate(c.key, "model", { again: true });
  return done;
}
export async function runCompatibility() {
  const c = shownMod();
  if (!c || busy) return;
  await call(() => Compatibility.check(c.id, c.label, remapped, c.mod, modProgressName(c)));
  if (open) await renderScreen();
}
export async function remapped() {
  ctx.invalidateRoster();
  await reload();
}
async function runRemap(c) {
  if (!c || busy || !Compatibility.eligible(c.mod)) return;
  await call(() => Compatibility.remap(c.id, remapped, c.label, modProgressName(c), c.mod?.kind));
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
// did not reach soon (a hall that kept the screen, another route) is dropped.
let wanted = null;
const WANTED_MS = 30000;
/**
 * Remember a Go To request from the update summary, for takeWanted().
 * @param {{variant: string, kind?: string, id: string}|null} request The mod to show: its variant, the kind of
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
  if (!w || Date.now() - w.at > WANTED_MS || !open || variantKey !== w.variant) return;
  const kind = ["lobby", "model"].includes(w.kind) ? w.kind : "spr";
  if (switchKind() !== kind) return;
  if (popupKind !== kind || $("popup").hidden) await openPopup(kind);
  if (!open || $("popup").hidden || popupKind !== kind || variantKey !== w.variant) return;
  const key = `m:${w.id}`;
  if (!candidates(variant(), kind).some((c) => c.key === key)) return;
  setPending(key);
  await renderPopup();
  [...$("popup").querySelectorAll("[data-key]")]
    .find((el) => el.dataset.key === key)
    ?.scrollIntoView?.({ block: "nearest", inline: "nearest" });
}
export function runExport() {
  const c = shownMod();
  if (busy) return;
  const target = c ? `mod:${c.id}` : `variant:${variant().key}`;
  ModExport.open({ scope: "single", target, kind: switchKind() || "spr" });
}
// switch: the chosen source is written into the variant's game files of that kind, so the game shows it; the library
// keeps what it replaces (an official file until it is official again, a mod in the library)
// again: the applied choice written again (a costume swap or Optimize Physics copy built under an older rule)
export async function applyCandidate(key, kind = "spr", { again = false } = {}) {
  const v = variant();
  if (!kind || !coreOf(v, kind) || busy || (!again && isApplied(v, kind, key)) || unfitCostume(v, kind, key)) return;
  await job(
    "actions.apply",
    { variant: v.key, source: sourceOf(key), kind },
    {
      onDone: (result) => {
        if (result?.files?.length) {
          const core = coreOf(v, kind);
          forget(spinePrefix(kind, core));
          forget(spinePrefix(`official-${kind}`, core));
        }
      },
      // the applied portrait or lobby swaps in with the rest of the screen; the old one shows until then
      after: async (ok, result) => {
        ctx.invalidateRoster();
        if (!ok) return reload();
        if (result?.unchanged) return reload();
        resetPreview(kind);
        if (!open) return null; // the screen was left meanwhile: the next show reads the family again
        return changeInPlace(async () => {
          const read = fam;
          const data = await call(() => readFamily(read.key));
          if (data && fam !== read) return; // the screen was read anew meanwhile
          if (data) setFam(data);
          await Promise.all([renderAll(), renderLeft()]);
        });
      },
    },
  );
  if (switchPopupPage === pageToken && !$("popup").hidden) await renderPopup();
}

export async function saveCandidate(key, kind) {
  const v = variant(),
    family = fam;
  await call(() =>
    Loading.hold(
      async () => {
        const sources = await rpc("ui.preview", { variant: v.key, kind, source: sourceOf(key) });
        ctx.invalidateRoster();
        if (!open || fam !== family || variant() !== v) return;
        v.previewSources = sources;
        if (kind === "model") judgeCross(v);
        resetPreview(kind);
        await changeInPlace(() => Promise.all([renderAll(), renderLeft()]));
      },
      { all: true },
    ),
  );
}
