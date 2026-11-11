// What a game update did under the library's mods (docs/web-ui.md, "Game update impact"). At the start, before the
// first screen (gate): once the current install's resource version differs from the last one seen and the library has
// mods (mods.updateDue), the page asks whether to check them; Check runs under the loading screen with its progress
// (run): the library classifies every mod from the official parts it was built against (mods.impact), with mods on
// and the file check allowed the game files are scanned and the applied entries the update overwrote with official
// files, which need no repair, are applied again (mods.restoreDrift). Once the first screen is in, the summary tells
// it in one message (check): official rig changed (those entries are not applied by an On or its Retry until
// repaired, with Fix All), each such mod with Go To and Fix; the entries applied again (folded); gone from the game,
// new official parts, rebuilt with the same rig, not affected, not determined (folded). Closing it records the
// version as seen (mods.impactSeen); Settings' Game Update Impact runs it again (show). The pure parts are tested in
// Node (tests/js/mod_switch.test.mjs).
import * as Compatibility from "./mod-compatibility.js";
import * as Loading from "./loading.js";
import * as Popup from "./popup.js";
import { notify } from "./popup.js";
import { on, rpc, waitJob } from "./api.js";
import { T, errorText } from "./i18n.js";
import * as ModSwitch from "./mod-switch.js";
import { entryLabel } from "./mod-switch.js";
import { SPACE, TYPE } from "./ui-tokens.js";

// the order the summary counts its kinds in, those that need the user first; unchanged and unknown close it
export const KINDS = ["rig_changed", "gone", "new_parts", "rebuilt", "unchanged", "unknown"];
const KEYS = { rig_changed: "rigChanged", gone: "gone", new_parts: "newParts", rebuilt: "rebuilt",
  unchanged: "unchanged", unknown: "unknown" };
// a mod line's Go To closes the message with "go:<mod id>", its Fix with "fix:<mod id>"; Fix All with "fixAll"
export const GO = "go:", FIX = "fix:", FIX_ALL = "fixAll";

// summary(result, nameOf, {expanded, always}): the message of an update's impact, {title, lines} for a plain
// (left-aligned) message; null when nothing needs telling (always: Settings' View, which tells the counts even then).
// The mods whose official rig changed come first: their count, each one as a list item ("student - kind - mod name",
// Go To), what changed in its official parts under it, and, in italics, how the hold ends. Then "Other affected mods"
// with an Expand / Collapse button (its line's button closes the message with "expand", see check) over a list item
// per other kind with its count and its mods, shown once expanded. With no mod held that list shows at once, without
// the button.
export const ITEM = Object.freeze({ marker: "\u2022", markerGap: 44, indent: 52 });
export const WIDTH = 2098;
// a mod listed under another kind's count
export const SUB = 104;
// what changed in a mod's official parts, under its line: the dense type, held close to the line it belongs to (a
// label to its own decoration, SPACE.xs, instead of the lines' SPACE.l)
export const DETAIL = Object.freeze({ fontSize: TYPE.dense.fs, minFontSize: TYPE.dense.fs, gap: SPACE.xs - SPACE.l });

const kindKey = (kind) => (kind === "model" ? "tab.model" : kind === "lobby" ? "tab.lobby" : "tab.portrait");

// changeText(mod): what the update changed in a mod's official parts ("Official Portrait: atlas repacked, 1 new
// animation", from mods.impact's changes), "" when nothing is known. A repack carries its texture sizes.
export function changeText(mod, sep = T("mods.impact.sep")) {
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

// modName(mod, nameOf): "student - kind - mod name" (the mod's own name left out where it only repeats the student's),
// then "Applied" for a mod its student is configured with (mods.impact's configured: whatever the switch says)
export function modName(mod, nameOf = (e) => e?.variant || e?.core || "") {
  const applied = mod?.configured ? ` - ${T("status.applied")}` : "";
  if (mod?.kind === "other") return `${mod.label || mod.core || ""} - ${T("settings.mods.other")}${applied}`;
  const entry = { variant: mod?.target, core: mod?.core, kind: mod?.kind };
  const named = entryLabel(entry, nameOf, " - ");
  return `${mod?.label && mod.label !== nameOf(entry) ? `${named} - ${mod.label}` : named}${applied}`;
}

// a mod's lines: its name with Go To (none for a generic mod: no student page) and, for a held mod (fix), Fix after
// it; then what changed in its official parts
export function modLines(mod, nameOf, { indent = ITEM.indent, marker = true, fix = false } = {}) {
  const page = mod?.id && mod.kind !== "other" && mod.target;
  const repair = fix && mod?.id ? [{ label: T("mods.impact.fix"), fn: ({ close }) => close(`${FIX}${mod.id}`) }] : [];
  const go = page ? { button: { label: T("add.go"), inline: true, fn: ({ close }) => close(`${GO}${mod.id}`) }, more: repair }
    : repair.length ? { button: { ...repair[0], inline: true } } : {};
  const line = { text: modName(mod, nameOf), indent, ...(marker ? { marker: ITEM.marker, markerGap: ITEM.markerGap } : {}), ...go };
  const change = changeText(mod);
  return change ? [line, { text: change, indent, ...DETAIL }] : [line];
}

// an entry of mods.entries as the summary names a mod: its configured mod (Go To), variant, kind; applied by definition
export const entryMod = (e) => ({ id: /^mod:(.+)$/.exec(e?.configured || "")?.[1] || "", target: e?.variant, core: e?.core,
  kind: e?.kind, label: "", configured: true });

// reappliable(entries, result): the scan's entries a game update overwrote with official files (state updated, not
// dismissed) that need no repair: not those whose configured mod's rig changed (held: listed with their mods, applied
// only once repaired)
export function reappliable(entries, result) {
  const updated = (entries || []).filter((e) => e && e.state === "updated" && !e.dismissed);
  const held = heldKeys(updated, result?.mods || []);
  return updated.filter((e) => !held.has(`${e.install}|${e.core}`));
}

// summary(result, nameOf, {expanded, reapplied, open, always}): the message of an update's impact, {title, lines} for a
// plain (left-aligned) message; null when nothing needs telling (always: Settings' View, which tells the counts even
// then). The held mods first: what holds them with Fix All, each one as a list item ("student - kind - mod name",
// Applied, Go To, Fix), what changed in its official parts under it, and, in italics, how the hold ends. Then, after
// the game's dotted rule, the entries the check applied again (reapplied), folded under their count with an Expand /
// Collapse button (open: "reapplied"). Then, after a rule, "Other affected mods" folded the same way (open:
// "others") over a list item per other kind with its count and its mods. With nothing above them the other kinds show
// at once, without the button. A fold's button closes the message with "expand:<fold>" (see present).
// heldOf(result): the held mods the summary lists: a mod whose rig changed, unless a repaired copy took its place (it
// has one and its student is not configured with it)
export const heldOf = (result) => (result?.mods || []).filter((m) => m.impact === "rig_changed" && !(m.repaired_id && !m.configured));

export function summary(result, nameOf = (e) => e?.variant || e?.core || "", { reapplied = [], open = [], always = false } = {}) {
  if (!result || (!result.due && !always && !reapplied.length)) return null;
  const counts = result.counts || {};
  const mods = result.mods || [];
  const held = heldOf(result);
  const lines = [];
  const fold = (name) => ({ label: T(open.includes(name) ? "mods.impact.collapse" : "mods.impact.expand"), inline: true,
    fn: ({ close }) => close(`expand:${name}`) });
  if (held.length) {
    lines.push({ text: T("mods.impact.rigChanged"),
      ...(held.length ? { button: { label: T("mods.impact.fixAll"), inline: true, fn: ({ close }) => close(FIX_ALL) } } : {}) });
    lines.push(...held.flatMap((m) => modLines(m, nameOf, { fix: true })));
  }
  if (reapplied.length) {
    if (lines.length) lines.push({ rule: true });
    lines.push({ text: T("mods.impact.reapplied", { n: reapplied.length }), button: fold("reapplied") });
    if (open.includes("reapplied")) lines.push(...reapplied.flatMap((e) => modLines(entryMod(e), nameOf)));
  }
  const others = KINDS.filter((k) => k !== "rig_changed" && counts[k] > 0).flatMap((kind) => [
    { text: T(`mods.impact.${KEYS[kind]}`, { n: counts[kind] }), ...ITEM },
    ...mods.filter((m) => m.impact === kind).flatMap((m) => modLines(m, nameOf, { indent: SUB, marker: false })),
  ]);
  const title = T("mods.impact.title");
  if (!lines.length) return { title, lines: others.length ? others : [{ text: T("settings.mods.empty") }] };
  if (others.length) {
    lines.push({ rule: true }, { text: T("mods.impact.others"), button: fold("others") });
    if (open.includes("others")) lines.push(...others);
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
let pending = null; // a check's outcome waiting for the first screen: {result, reapplied, failed}
export const isBusy = () => busy;

export function init(options = {}) {
  if (options.names) names = options.names;
  if (options.go) goTo = options.go;
  // a game update while the program runs: the game services were rebuilt for the new resource version
  on("config.changed", () => {
    Popup.whenIdle().then(async () => { if (await gate()) await check(); }).catch((err) => console.error("impact", err));
  });
}

// open(mod): Go To, the mod's student page with its switch open on the mod
export const open = (mod) => Promise.resolve(goTo({ id: mod.id, variant: mod.target, kind: mod.kind })).catch((err) => console.error("impact", err));

// the progress of a check (Loading.task's update): its step and, while the mods are classified, the mod at hand
function stepper(update, nameOf) {
  return (key) => (p = {}) => update({ ...p,
    label: key === "mods.impact.checking" && p.variant
      ? T("mods.impact.checkingMod", { name: modName({ target: p.variant, kind: p.kind, label: p.label }, nameOf) }) : T(key) });
}

// the scan the check reads its overwritten entries from: a new one, with mods on and the file check allowed; [] else
async function scanned(step) {
  const s = ModSwitch.current() || (await ModSwitch.refresh().catch(() => null));
  if (s?.scan !== true || s.enabled === false) return [];
  step("progress.check")();
  const done = new Promise((resolve) => {
    let off = null;
    const timer = setTimeout(() => { off?.(); resolve(null); }, 60000);
    off = on("library.scanned", (data) => { clearTimeout(timer); off(); resolve(data); });
  });
  await rpc("mods.scan", {});
  await done;
  return (await rpc("mods.entries").catch(() => [])) || [];
}

// run({force}): the check under the loading screen with its progress: classify, scan, apply again what needs no repair.
// force: Settings' View, whatever was seen. Resolves with {result, reapplied, failed}; throws what the jobs refuse.
async function run({ force = false } = {}) {
  const nameOf = await names().catch(() => undefined);
  return Loading.task(async (update) => {
    const step = stepper(update, nameOf);
    const result = await waitJob(await rpc("mods.impact", force ? { force: true } : {}), step("mods.impact.checking"));
    const again = reappliable(await scanned(step), result);
    let reapplied = [], failed = [];
    if (again.length) {
      const plain = again.map((e) => ({ install: e.install, core: e.core, state: e.state }));
      const done = await waitJob(await rpc("mods.restoreDrift", { entries: plain }), step("mods.impact.reapplying"));
      const ok = new Set((done?.done || []).map((d) => `${d.install}|${d.core}`));
      reapplied = again.filter((e) => ok.has(`${e.install}|${e.core}`));
      failed = done?.failed || [];
    }
    return { result, reapplied, failed };
  }, { label: T("mods.impact.checking") });
}

// gate(): at the start before the first screen (and after a game update while the program runs): the question, then the
// check. Resolves true when a summary waits (check shows it); Later asks again at the next start.
export async function gate() {
  if (busy) return false;
  const due = await rpc("mods.updateDue").catch(() => null);
  if (!due?.due) return false;
  busy = true;
  try {
    const answer = await Popup.message({ title: T("mods.impact.gateTitle"), lines: [T("mods.impact.gateAsk")], close: false,
      buttons: [{ label: T("update.later"), value: "later" }, { label: T("mods.impact.gateCheck"), value: "check", primary: true }] });
    if (answer.button !== "check") return false;
    pending = await run();
    return true;
  } catch (err) {
    console.error("impact", err);
    notify(errorText(err.code, err.params), { kind: "error", error: err });
    return false;
  } finally {
    busy = false;
  }
}

// the summary's message until it is closed for good; resolves with what its buttons asked: {go: mod} or {fix: [mods]}
async function present({ result, reapplied = [], failed = [] }, nameOf, options = {}) {
  let open = [];
  for (;;) {
    const message = summary(result, nameOf, { ...options, reapplied, open });
    if (!message) return {};
    // the wide frame: an unfolded list scrolls within the frame's height cap
    const answer = await Popup.message({ title: message.title, lines: message.lines, ok: T("picker.ok"), plain: true, width: WIDTH });
    const button = typeof answer.button === "string" ? answer.button : "";
    if (button.startsWith("expand:")) {
      const name = button.slice(7);
      open = open.includes(name) ? open.filter((n) => n !== name) : [...open, name];
      continue;
    }
    const held = heldOf(result);
    if (button === FIX_ALL) return { fix: held };
    if (button.startsWith(FIX)) return { fix: held.filter((m) => m.id === button.slice(FIX.length)) };
    if (button.startsWith(GO)) {
      const id = button.slice(GO.length);
      return { go: [...(result.mods || []), ...reapplied.map(entryMod)].find((m) => m.id === id) || null };
    }
    return {};
  }
}

// fix(mods): the student page's Update for each held mod, here: the compatibility check, the repair (the review its
// choice opens) or the asset update, then, for a mod its student is configured with, its repaired copy takes its place
// (actions.apply: with mods off it is only configured). Resolves with the mods it repaired.
async function fix(mods, nameOf) {
  const repaired = [];
  for (const mod of mods) {
    const subject = modName({ ...mod, configured: false }, nameOf);
    try {
      // a repaired copy made before (the student page's Update): it takes the mod's place
      if (mod.repaired_id) {
        if (mod.configured) await waitJob(await rpc("actions.apply", { variant: mod.target, source: `mod:${mod.repaired_id}`, kind: mod.kind }));
        repaired.push(mod);
        continue;
      }
      const report = await Loading.hold(() => Compatibility.forUpdate(mod.id, mod.label, subject), { all: true });
      if (!report) continue;
      if (Compatibility.eligible(mod) && Compatibility.canRemap(mod.id, mod)) {
        const saved = await Compatibility.remap(mod.id, () => {}, mod.label, subject, mod.kind);
        if (!saved?.mod?.id) continue;
        if (mod.configured) await waitJob(await rpc("actions.apply", { variant: mod.target, source: `mod:${saved.mod.id}`, kind: mod.kind }));
      } else {
        await Loading.task(async (update) => waitJob(await rpc("actions.update", { mod: mod.id }), update),
          { label: T("progress.process", { name: subject }) });
      }
      repaired.push(mod);
    } catch (err) {
      notify(errorText(err.code, err.params), { kind: "error", error: err });
    }
  }
  return repaired;
}

// the mods.state entries a game update's rig change holds ({install, core, variant, kind, source, label}) as the
// summary's mods (applied by definition)
export const heldMods = (entries) => (entries || []).map((e) => ({ id: /^mod:(.+)$/.exec(e?.source || "")?.[1] || "",
  target: e?.variant, core: e?.core, kind: e?.kind, label: e?.label || "", configured: true }));

// heldLines(entries, nameOf): the held entries, each as the summary names a held mod (Go To and Fix and Apply where its
// mod is known); no hint under them (owner, 2026-11-11)
export function heldLines(entries, nameOf) {
  return heldMods(entries).flatMap((m) => modLines(m, nameOf, { fix: true }));
}

// the switch help's Details of the held entries; resolves with what a button asked ({go: mod} or {fix: [mod]}, the
// caller closes the help first, then opens or repairs it), else {}
export async function heldDetails(entries) {
  const nameOf = await names().catch(() => undefined);
  const answer = await Popup.message({ title: T("mods.heldTitle"), ok: T("picker.ok"), plain: true, width: WIDTH, lines: heldLines(entries, nameOf) });
  const button = typeof answer.button === "string" ? answer.button : "";
  const find = (id) => heldMods(entries).find((m) => m.id === id) || null;
  if (button.startsWith(GO)) return { go: find(button.slice(GO.length)) };
  if (button.startsWith(FIX)) return { fix: [find(button.slice(FIX.length))].filter(Boolean) };
  return {};
}

// repair(mods): Fix from outside the summary (the switch help's Details)
export async function repair(mods) {
  if (busy || !mods?.length) return [];
  busy = true;
  try { return await fix(mods, await names().catch(() => undefined)); } finally { busy = false; }
}

// check(): once the first screen is in, the summary a check left (gate), until it is closed; its Fix and Fix All
// repair here and show it again as the mods stand then; Go To leaves for the mod's student page
export async function check() {
  if (busy || !pending) return null;
  busy = true;
  let chosen = null;
  const outcome = pending;
  pending = null;
  try {
    const nameOf = await names().catch(() => undefined);
    await Popup.whenIdle();
    let current = outcome;
    for (;;) {
      const answer = await present(current, nameOf, { always: true });
      if (answer.fix?.length) {
        await fix(answer.fix, nameOf);
        const id = await rpc("mods.impact", { force: true });
        current = { ...current, result: await Loading.task((update) => waitJob(id, update), { label: T("mods.impact.checking") }) };
        continue;
      }
      chosen = answer.go || null;
      break;
    }
    if (outcome.result?.version) await rpc("mods.impactSeen", { install: outcome.result.install, version: outcome.result.version }).catch((err) => console.error("impact", err));
    if (outcome.failed?.length) await Popup.message({ title: T("mods.impact.title"), lines: outcome.failed.map((f) => `${ModSwitch.entryLabel(f.entry, nameOf)} - ${errorText(f.code, f.params)}`), ok: T("picker.ok") });
    return outcome.result;
  } catch (err) {
    console.error("impact", err);
    return null;
  } finally {
    busy = false;
    if (chosen) await open(chosen);
  }
}

// show(): Settings' Game Update Impact: the check again, whatever was seen, then its summary; resolves with true when
// a Go To left for a student page
export async function show() {
  if (busy) return false;
  try {
    busy = true;
    pending = await run({ force: true });
  } catch (err) {
    notify(errorText(err.code, err.params), { kind: "error", error: err });
    return false;
  } finally {
    busy = false;
  }
  const before = location.hash;
  await check();
  return location.hash !== before;
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
