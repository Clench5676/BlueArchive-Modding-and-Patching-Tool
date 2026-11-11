// Troubleshoot Mods (BL-185), a Settings row of pre-release builds (Mods tab): Apply Official to All sets every choice
// to official with mods on, so one mod at a time writes again, and saves the configuration; Restore Configuration
// brings it back. Both are game writer jobs (mods.applyOfficial, mods.restoreConfig) whose end shows the check of the
// game files against Steam's file list. The pure parts are tested in Node (tests/js/troubleshoot.test.mjs).
import * as Loading from "./loading.js";
import * as Popup from "./popup.js";
import * as ModSwitch from "./mod-switch.js";
import { rpc, waitJob } from "./api.js";
import { T } from "./i18n.js";

const MAX_NAMES = 10; // file names a result lists; the rest are counted

// statusText(state): the row's value from mods.troubleshootState {active, count}
export const statusText = (state) => (state?.active
  ? T("settings.troubleshoot.active", { n: state.count || 0 }) : T("settings.troubleshoot.inactive"));

// checkLines(check): the check against Steam's file list, then the first names of the files that differ
export function checkLines(check) {
  if (!check) return [];
  if (!check.verified) return [T("settings.troubleshoot.unverified")];
  const count = (check.changedCount || 0) + (check.missingCount || 0);
  if (!count) return [T("settings.troubleshoot.match")];
  const names = [...(check.changed || []), ...(check.missing || [])];
  const lines = [T("settings.troubleshoot.differ", { n: count }), ...names.slice(0, MAX_NAMES)];
  if (count > MAX_NAMES) lines.push(`+${count - MAX_NAMES}`);
  return lines;
}

// resultLines(result, nameOf): the check, then the switch's own summary of entries not switched
export function resultLines(result, nameOf) {
  return [...checkLines(result?.check), ...(ModSwitch.resultSummary(result, nameOf)?.lines || [])];
}

async function submit(method, title) {
  let result;
  try {
    result = await Loading.hold(async (update) => {
      const id = await rpc(method, {}, { timeout: 30000 });
      update(title);
      return waitJob(id, (p) => update(Loading.progressText(p, title)));
    }, { all: true });
  } catch (err) {
    // the untested build's confirmation, then the same call again; else the error's string
    await ModSwitch.refused(err, null, null, () => submit(method, title));
    return true;
  }
  // a restore with nothing to report stays silent, as the switch does
  const lines = result?.cancelled ? [] : resultLines(result);
  if (lines.length) await Popup.message({ title, lines, ok: T("picker.ok") });
  return true;
}

async function run(method, key, confirmKey) {
  const title = T(`settings.troubleshoot.${key}`);
  const answer = await Popup.message({ title, lines: [T(`settings.troubleshoot.${confirmKey}`)], ok: title,
    cancel: T("popup.cancel") });
  if (answer.button !== "ok") return false;
  return submit(method, title);
}

export const state = () => rpc("mods.troubleshootState");
export const applyOfficial = () => run("mods.applyOfficial", "apply", "applyConfirm");
export const restore = () => run("mods.restoreConfig", "restore", "restoreConfirm");
