// What a game update did under the library's mods (docs/web-ui.md, "Game update impact"): once the current install's
// resource version differs from the last one told, the library classifies every mod from the official parts it was
// built against (mods.impact) and the page tells it in one message: rebuilt with the same rig, new official parts,
// rig changed (those entries are held, not re-applied by an On or its Retry), gone from the game, and how many were
// not affected or not determined. Closing it records the version as told (mods.impactSeen). The pure parts are
// tested in Node (tests/js/update_impact.test.mjs).
import * as Popup from "./popup.js";
import { on, rpc, waitJob } from "./api.js";
import { T } from "./i18n.js";
import { entryLabel } from "./mod-switch.js";

// the order the summary counts its kinds in, those that need the user first; unchanged and unknown close it
export const KINDS = ["rig_changed", "gone", "new_parts", "rebuilt", "unchanged", "unknown"];
const KEYS = { rig_changed: "rigChanged", gone: "gone", new_parts: "newParts", rebuilt: "rebuilt",
  unchanged: "unchanged", unknown: "unknown" };
const MAX_LISTED = 6; // held mods the summary names; the rest are counted

// summary(result, nameOf): the message of an update's impact, {title, lines}; null when nothing needs telling.
// Every kind with mods gets a count line; the mods held for a changed rig are named under theirs, with the hint that
// updating them on their page ends the hold.
export function summary(result, nameOf = (e) => e?.variant || e?.core || "") {
  if (!result?.due) return null;
  const counts = result.counts || {};
  const held = (result.mods || []).filter((m) => m.impact === "rig_changed");
  const lines = [];
  for (const kind of KINDS.filter((k) => counts[k] > 0)) {
    lines.push(T(`mods.impact.${KEYS[kind]}`, { n: counts[kind] }));
    if (kind !== "rig_changed" || !held.length) continue;
    const named = held.map((m) => entryLabel({ variant: m.target, core: m.core, kind: m.kind }, nameOf));
    lines.push(...named.slice(0, MAX_LISTED));
    if (named.length > MAX_LISTED) lines.push(`+${named.length - MAX_LISTED}`);
    lines.push(T("mods.impact.hint"));
  }
  return { title: T("mods.impact.title"), lines };
}

// the mods a drift prompt's entries are configured with ("mod:<id>"), for mods.impact {ids}
export const configuredMods = (entries) => [...new Set((entries || []).map((e) => /^mod:(.+)$/.exec(e?.configured || "")?.[1]).filter(Boolean))];

// the keys of the entries whose configured mod's rig an update changed (from mods.impact {ids})
export function heldKeys(entries, impacts) {
  const changed = new Set((impacts || []).filter((m) => m.impact === "rig_changed").map((m) => m.id));
  return new Set((entries || []).filter((e) => changed.has(/^mod:(.+)$/.exec(e?.configured || "")?.[1])).map((e) => `${e.install}|${e.core}`));
}

// --- the page ---

let names = async () => (e) => e?.variant || e?.core || "";
let busy = false;
const told = new Set(); // install|version told in this page
export const isBusy = () => busy;

export function init(options = {}) {
  if (options.names) names = options.names;
  // a game update while the program runs: the game services were rebuilt for the new resource version
  on("config.changed", () => { Popup.whenIdle().then(() => check()).catch((err) => console.error("impact", err)); });
}

// check(): the summary of the current install's update, if one is due; resolves once its message closed
export async function check() {
  if (busy) return null;
  busy = true;
  try {
    const result = await waitJob(await rpc("mods.impact"));
    if (!result?.due || told.has(`${result.install}|${result.version}`)) return null;
    const message = summary(result, await names().catch(() => undefined));
    if (!message) return null;
    told.add(`${result.install}|${result.version}`);
    await Popup.whenIdle();
    await Popup.message({ title: message.title, lines: message.lines, ok: T("picker.ok") });
    await rpc("mods.impactSeen", { install: result.install, version: result.version }).catch((err) => console.error("impact", err));
    return result;
  } catch (err) {
    console.error("impact", err);
    return null;
  } finally {
    busy = false;
  }
}

// impacts(entries): the impacts of the mods the entries are configured with, [] when none or on a failure
export async function impacts(entries) {
  const ids = configuredMods(entries);
  if (!ids.length) return [];
  try {
    return (await waitJob(await rpc("mods.impact", { ids })))?.mods || [];
  } catch (err) {
    console.error("impact", err);
    return [];
  }
}
