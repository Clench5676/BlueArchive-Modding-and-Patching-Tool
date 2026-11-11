// What a game update did under the library's mods (docs/web-ui.md, "Game update impact"): once the current install's
// resource version differs from the last one told, the library classifies every mod from the official parts it was
// built against (mods.impact) and the page tells it in one message: official rig changed (those entries are not
// applied by an On or its Retry until updated), gone from the game, new official parts, rebuilt with the same rig, and
// how many were not affected or not determined. Each mod is named with its student, kind and own name, what the update
// changed in its official parts, and a Go To button that opens its student's switch on it. Closing it records the
// version as told (mods.impactSeen); Settings' Game Update Impact shows it again (show). The pure parts are tested in
// Node (tests/js/mod_switch.test.mjs).
import * as Loading from "./loading.js";
import * as Popup from "./popup.js";
import { notify } from "./popup.js";
import { on, rpc, waitJob } from "./api.js";
import { T, errorText } from "./i18n.js";
import { entryLabel, listSep } from "./mod-switch.js";
import { SPACE, TYPE } from "./ui-tokens.js";

// the order the summary counts its kinds in, those that need the user first; unchanged and unknown close it
export const KINDS = ["rig_changed", "gone", "new_parts", "rebuilt", "unchanged", "unknown"];
const KEYS = { rig_changed: "rigChanged", gone: "gone", new_parts: "newParts", rebuilt: "rebuilt",
  unchanged: "unchanged", unknown: "unknown" };
// a mod line's Go To closes the message with "go:<mod id>"
export const GO = "go:";

// summary(result, nameOf, {expanded, always}): the message of an update's impact, {title, lines} for a plain
// (left-aligned) message; null when nothing needs telling (always: Settings' View, which tells the counts even then).
// The mods whose official rig changed come first: their count, each one as a list item ("student - kind - mod name",
// Go To), what changed in its official parts under it, and, in italics, how the hold ends. Then "Other affected mods"
// with an Expand / Collapse button (its line's button closes the message with "expand", see check) over a list item
// per other kind with its count and its mods, shown once expanded. With no mod held that list shows at once, without
// the button.
export const ITEM = Object.freeze({ marker: "\u2022", markerGap: 44, indent: 52 });
const GROUP_GAP = 16;
export const WIDTH = 2098;
// a mod listed under another kind's count
export const SUB = 104;
// how a hold ends: in italics, two steps under the lines' type (heading 52 > body 48 > dense 42)
export const HINT = Object.freeze({ italic: true, fontSize: TYPE.dense.fs, minFontSize: TYPE.dense.fs });
// what changed in a mod's official parts, under its line: the dense type, held close to the line it belongs to (a
// label to its own decoration, SPACE.xs, instead of the lines' SPACE.l)
export const DETAIL = Object.freeze({ fontSize: TYPE.dense.fs, minFontSize: TYPE.dense.fs, gap: SPACE.xs - SPACE.l });

const kindKey = (kind) => (kind === "model" ? "tab.model" : kind === "lobby" ? "tab.lobby" : "tab.portrait");

// changeText(mod): what the update changed in a mod's official parts ("Official Portrait: atlas repacked, 1 new
// animation", from mods.impact's changes), "" when nothing is known. A repack carries its texture sizes.
export function changeText(mod, sep = listSep()) {
  const c = mod?.changes || {};
  const parts = [];
  if (c.repacked) parts.push(T("mods.impact.change.repacked"));
  if (c.animations > 0) parts.push(c.animations === 1 ? T("mods.impact.change.animation") : T("mods.impact.change.animations", { n: c.animations }));
  if (c.rig) parts.push(T("mods.impact.change.rig"));
  if (c.textures && !c.repacked) parts.push(T("mods.impact.change.textures"));
  if (c.removed) parts.push(T("mods.impact.change.removed"));
  if (c.added) parts.push(T("mods.impact.change.added"));
  if (!parts.length) return "";
  const changes = parts.join(sep);
  return mod.kind === "other" ? T("mods.impact.officialFiles", { changes }) : T("mods.impact.official", { kind: T(kindKey(mod.kind)), changes });
}

// modName(mod, nameOf): "student - kind - mod name" (the mod's own name left out where it only repeats the student's)
export function modName(mod, nameOf = (e) => e?.variant || e?.core || "") {
  if (mod?.kind === "other") return `${mod.label || mod.core || ""} - ${T("settings.mods.other")}`;
  const entry = { variant: mod?.target, core: mod?.core, kind: mod?.kind };
  const named = entryLabel(entry, nameOf, " - ");
  return mod?.label && mod.label !== nameOf(entry) ? `${named} - ${mod.label}` : named;
}

// a mod's lines: its name with Go To (none for a generic mod: no student page), then what changed in its official parts
export function modLines(mod, nameOf, { indent = ITEM.indent, marker = true } = {}) {
  const go = mod?.id && mod.kind !== "other" && mod.target
    ? { button: { label: T("add.go"), inline: true, fn: ({ close }) => close(`${GO}${mod.id}`) } } : {};
  const line = { text: modName(mod, nameOf), indent, ...(marker ? { marker: ITEM.marker, markerGap: ITEM.markerGap } : {}), ...go };
  const change = changeText(mod);
  return change ? [line, { text: change, indent, ...DETAIL }] : [line];
}

export function summary(result, nameOf = (e) => e?.variant || e?.core || "", { expanded = false, always = false } = {}) {
  if (!result || (!result.due && !always)) return null;
  const counts = result.counts || {};
  const mods = result.mods || [];
  const held = mods.filter((m) => m.impact === "rig_changed");
  const lines = [];
  if (counts.rig_changed > 0) {
    lines.push({ text: T("mods.impact.rigChanged", { n: counts.rig_changed }) });
    lines.push(...held.flatMap((m) => modLines(m, nameOf)));
    if (held.length) lines.push({ text: T("mods.impact.hint", { go: T("add.go"), action: T("act.update") }), ...HINT });
  }
  const others = KINDS.filter((k) => k !== "rig_changed" && counts[k] > 0).flatMap((kind) => [
    { text: T(`mods.impact.${KEYS[kind]}`, { n: counts[kind] }), ...ITEM },
    ...mods.filter((m) => m.impact === kind).flatMap((m) => modLines(m, nameOf, { indent: SUB, marker: false })),
  ]);
  const title = T("mods.impact.title");
  if (!lines.length) return { title, lines: others.length ? others : [{ text: T("settings.mods.empty") }] };
  if (others.length) {
    lines.push({ text: T("mods.impact.others"), gap: GROUP_GAP,
      button: { label: T(expanded ? "mods.impact.collapse" : "mods.impact.expand"), inline: true, fn: ({ close }) => close("expand") } });
    if (expanded) lines.push(...others);
  }
  return { title, lines };
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
let goTo = async () => {}; // main.js: a mod's student page with its switch open on the mod
let busy = false;
const told = new Set(); // install|version told in this page
export const isBusy = () => busy;

export function init(options = {}) {
  if (options.names) names = options.names;
  if (options.go) goTo = options.go;
  // a game update while the program runs: the game services were rebuilt for the new resource version
  on("config.changed", () => { Popup.whenIdle().then(() => check()).catch((err) => console.error("impact", err)); });
}

// the summary's message until it is closed for good; resolves with the mod its Go To named, if one did
async function present(result, nameOf, options = {}) {
  // the Expand / Collapse button closes the message, which opens again with the list the other way
  for (let expanded = false; ;) {
    const message = summary(result, nameOf, { ...options, expanded });
    // the wide frame: the list unfolded keeps clear of the frame's height cap in every language
    const answer = await Popup.message({ title: message.title, lines: message.lines, ok: T("picker.ok"), plain: true, width: WIDTH });
    if (answer.button === "expand") { expanded = !expanded; continue; }
    const id = typeof answer.button === "string" && answer.button.startsWith(GO) ? answer.button.slice(GO.length) : null;
    return id ? (result.mods || []).find((m) => m.id === id) || null : null;
  }
}

// open(mod): Go To, the mod's student page with its switch open on the mod
export const open = (mod) => Promise.resolve(goTo({ id: mod.id, variant: mod.target, kind: mod.kind })).catch((err) => console.error("impact", err));

// the mods.state entries a game update's rig change holds ({install, core, variant, kind, source, label}) as the
// summary's mods
export const heldMods = (entries) => (entries || []).map((e) => ({ id: /^mod:(.+)$/.exec(e?.source || "")?.[1] || "",
  target: e?.variant, core: e?.core, kind: e?.kind, label: e?.label || "" }));

// heldLines(entries, nameOf): the held entries, each as the summary names a mod (Go To where its mod is known), then
// how the hold ends
export function heldLines(entries, nameOf) {
  const lines = heldMods(entries).flatMap((m) => modLines(m, nameOf));
  return [...lines, { text: T("mods.impact.hint", { go: T("add.go"), action: T("act.update") }), gap: 32, ...HINT }];
}

// the switch help's Details of the held entries; resolves with the mod a Go To named (the caller closes the help
// first, then opens it), else null
export async function heldDetails(entries) {
  const nameOf = await names().catch(() => undefined);
  const answer = await Popup.message({ title: T("mods.heldTitle"), ok: T("picker.ok"), plain: true, width: WIDTH, lines: heldLines(entries, nameOf) });
  const id = typeof answer.button === "string" && answer.button.startsWith(GO) ? answer.button.slice(GO.length) : null;
  return id ? heldMods(entries).find((m) => m.id === id) || null : null;
}

// check(): the summary of the current install's update, if one is due; resolves once its message closed
export async function check() {
  if (busy) return null;
  busy = true;
  let chosen = null;
  try {
    const result = await waitJob(await rpc("mods.impact"));
    if (!result?.due || told.has(`${result.install}|${result.version}`)) return null;
    const nameOf = await names().catch(() => undefined);
    if (!summary(result, nameOf)) return null;
    told.add(`${result.install}|${result.version}`);
    await Popup.whenIdle();
    chosen = await present(result, nameOf);
    await rpc("mods.impactSeen", { install: result.install, version: result.version }).catch((err) => console.error("impact", err));
    return result;
  } catch (err) {
    console.error("impact", err);
    return null;
  } finally {
    busy = false;
    if (chosen) await open(chosen);
  }
}

// show(): Settings' Game Update Impact, the summary of the mods as they stand now, whatever was told before; resolves
// with true when a Go To left for a student page
export async function show() {
  if (busy) return false;
  busy = true;
  let chosen = null;
  try {
    const id = await rpc("mods.impact", { force: true });
    const result = await Loading.task((update) => waitJob(id, update), { label: T("progress.compatibility", { name: T("progress.mods") }) });
    chosen = await present(result, await names().catch(() => undefined), { always: true });
  } catch (err) {
    notify(errorText(err.code, err.params), { kind: "error", error: err });
  } finally {
    busy = false;
  }
  if (chosen) await open(chosen);
  return !!chosen;
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
