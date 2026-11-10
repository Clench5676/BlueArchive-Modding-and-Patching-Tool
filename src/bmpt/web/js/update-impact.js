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

// summary(result, nameOf): the message of an update's impact, {title, lines} for a plain (left-aligned) message;
// null when nothing needs telling. The mods held for a changed rig come first: their count, each one as a list item
// ("name - kind"), and how the hold ends; then, after a gap, a list item per other kind with mods.
export const ITEM = Object.freeze({ marker: "\u2022", markerGap: 44, indent: 52 });
const GROUP_GAP = 32;
export function summary(result, nameOf = (e) => e?.variant || e?.core || "") {
  if (!result?.due) return null;
  const counts = result.counts || {};
  const held = (result.mods || []).filter((m) => m.impact === "rig_changed");
  const lines = [];
  if (counts.rig_changed > 0) {
    lines.push({ text: T("mods.impact.rigChanged", { n: counts.rig_changed }) });
    const named = held.map((m) => entryLabel({ variant: m.target, core: m.core, kind: m.kind }, nameOf, " - "));
    lines.push(...named.slice(0, MAX_LISTED).map((text) => ({ text, ...ITEM })));
    if (named.length > MAX_LISTED) lines.push({ text: `+${named.length - MAX_LISTED}`, ...ITEM });
    if (named.length) lines.push({ text: T("mods.impact.hint", { action: T("act.update") }) });
  }
  KINDS.filter((k) => k !== "rig_changed" && counts[k] > 0).forEach((kind, i) => {
    lines.push({ text: T(`mods.impact.${KEYS[kind]}`, { n: counts[kind] }), ...ITEM, gap: i === 0 && lines.length ? GROUP_GAP : 0 });
  });
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
    await Popup.message({ title: message.title, lines: message.lines, ok: T("picker.ok"), plain: true });
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
