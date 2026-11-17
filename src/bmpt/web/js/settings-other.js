// Generic library sources in Settings, using the same rows and writer jobs as the other controls.
import { notify } from "./ui/popup.js";
import * as Loading from "./app/loading.js";
import { modJob } from "./core/job-progress.js";
import * as ModSwitch from "./mod-switch.js";
import * as Popup from "./ui/popup.js";
import { rpc, waitJob } from "./core/api.js";
import { T } from "./core/i18n.js";
import { overwrittenLines } from "./app/repairs.js";

let busy = false;

// write(method, params, mod, name, changed): a generic mod's job under the loading screen, its line naming the file
// and the mod (job-progress.js modJob)
async function write(method, params, mod, name, changed) {
  if (busy) return;
  busy = true;
  let refusal = null;
  const action =
    method === "mods.remove"
      ? "progress.actionDelete"
      : method === "actions.restore"
        ? "progress.actionRestore"
        : "progress.actionApply";
  const line = modJob(
    { variant: mod?.target || "", kind: "other", core: mod?.core || mod?.file?.split(/[\\/]/).pop() || name, name },
    { nameOf: Loading.variantName, action },
  );
  try {
    await Loading.task(
      async (update) => {
        update(line());
        return waitJob(await rpc(method, params), (p) => update(line(p)));
      },
      { label: line().text },
    ).then(async (result) => {
      if (result?.failed?.length)
        notify(T("mods.deletePending"), {
          kind: "error",
          error: { code: result.failed[0]?.code || "internal", params: { failed: result.failed } },
        });
      if (result?.kept_current)
        await Popup.message({ title: T("popup.apply"), lines: [T("mods.keptCurrent")], ok: T("picker.ok") });
    });
    await ModSwitch.refresh();
  } catch (err) {
    if (err.code === "original_unavailable") {
      const state = ModSwitch.current?.() || {};
      await Popup.message({
        title: T("mods.overwrittenTitle"),
        lines: overwrittenLines(
          {
            installs: (state.installs || []).filter((item) => item.key === state.current),
          },
          T,
        ),
        ok: T("picker.ok"),
      });
    } else refusal = err;
  } finally {
    busy = false;
    await changed();
  }
  // an untested game build's confirmation makes the write again
  if (refusal) await ModSwitch.refused(refusal, method, params, () => write(method, params, mod, name, changed));
}

/**
 * Apply a mod of the other kinds (game files that are not a portrait, lobby or model) or put the official files
 * back.
 * @param {object} mod The mod, as mods.other lists it.
 * @param {() => Promise<void>} changed Called after the write, whatever came of it, to refresh the page.
 * @param {boolean} [useMod] True applies the mod, false restores the official files; the opposite of what is
 *     applied by default.
 */
export async function select(mod, changed, useMod = !mod.selected) {
  const method = useMod ? "actions.apply" : "actions.restore";
  const params = { variant: mod.target, kind: "other", ...(useMod ? { source: `mod:${mod.id}` } : {}) };
  return write(method, params, mod, mod.label, changed);
}

/**
 * Delete a mod of the other kinds after asking, which also restores the official files it was applied over.
 * @param {object} mod The mod, as mods.other lists it.
 * @param {() => Promise<void>} changed Called after the write, to refresh the page.
 */
export async function remove(mod, changed) {
  if (busy) return;
  try {
    const info = await rpc("mods.removalInfo", { id: mod.id });
    const answer = await Popup.message({
      title: T("btn.delete"),
      lines: [T("mods.deleteConfirm", { name: info.name }), ...(info.used ? [T("mods.deleteRestore")] : [])],
      ok: T("btn.delete"),
      cancel: T("popup.cancel"),
    });
    if (answer.button === "ok") await write("mods.remove", { id: mod.id, confirm: true }, mod, info.name, changed);
  } catch (err) {
    ModSwitch.refused(err);
  }
}

/**
 * The state of a mod, then its file names in the language's list separator.
 */
export const valueText = (mod) =>
  [
    T(["active", "set"].includes(mod.state) ? "status.applied" : `settings.mods.otherState.${mod.state}`),
    mod.overwritten && T("mods.overwrittenNone"),
    mod.files.join(ModSwitch.listSep()),
  ]
    .filter(Boolean)
    .join(" - ");

/**
 * The Settings rows of the mods of the other kinds, each with its Use Mod, Official and Delete buttons.
 * @param {object[]} mods The mods, as mods.other lists them.
 * @param {Function} valueRow Makes a value row (settings-rows.js).
 * @param {() => Promise<void>} changed Called after a write, to refresh the page.
 */
export function rows(mods, valueRow, changed) {
  return mods.map((mod, i) =>
    valueRow(
      `Other_${i}`,
      0,
      mod.label,
      valueText(mod),
      [
        [
          T("settings.mods.useMod"),
          () => select(mod, changed, true),
          mod.state !== "missing" && (!mod.selected || mod.state === "changed"),
        ],
        [T("settings.mods.official"), () => select(mod, changed, false), mod.state !== "missing" && !mod.overwritten],
        [T("btn.delete"), () => remove(mod, changed)],
      ],
      { below: true },
    ),
  );
}
