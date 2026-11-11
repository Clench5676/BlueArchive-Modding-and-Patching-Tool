// What a game update did under the library's mods (docs/web-ui.md, "Game update impact"). At the start, before the
// first screen (gate): once the current install's resource version differs from the last one seen and the library has
// mods (mods.updateDue), the page asks whether to check them; Check runs under the loading screen with its progress
// (run): the library classifies every mod from the official parts it was built against (mods.impact), with mods on
// and the file check allowed the game files are scanned and the applied entries the update overwrote with official
// files, which need no repair, are applied again (mods.restoreDrift). Once the first screen is in, the summary shows
// it as a list (check; owner, 2026-11-11: the drift prompt's popup): three segments, the held mods (ticked, with the
// Files Changed badge; Fix and Apply for the ticked ones), the entries applied again, the other affected mods; a
// card's details hold Go To and, for a held mod, the repair's preview (the review, whose Accept is Fix and Apply) and
// Fix and Apply. Closing it records the version as seen (mods.impactSeen); Settings' Game Update Impact runs it again
// (show). The pure parts are tested in Node (tests/js/mod_switch.test.mjs).
import * as Compatibility from "./mod-compatibility.js";
import * as Loading from "./loading.js";
import * as Popup from "./popup.js";
import { notify } from "./popup.js";
import { on, rpc, spineBase, waitJob } from "./api.js";
import { thumb } from "./thumbs.js";
import { T, errorText } from "./i18n.js";
import * as ModSwitch from "./mod-switch.js";
import { entryLabel } from "./mod-switch.js";
import { COLOUR, SPACE, TYPE } from "./ui-tokens.js";

// a held entry's line (the switch help's Details): Go To closes the message with "go:<mod id>", Fix and Apply with
// "fix:<mod id>"
export const GO = "go:", FIX = "fix:";
export const ITEM = Object.freeze({ marker: "\u2022", markerGap: 44, indent: 52 });
export const WIDTH = 2098;
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
async function run({ force = false, scan = true } = {}) {
  const nameOf = await names().catch(() => undefined);
  return Loading.task(async (update) => {
    const step = stepper(update, nameOf);
    const result = await waitJob(await rpc("mods.impact", force ? { force: true } : {}), step("mods.impact.checking"));
    const again = scan ? reappliable(await scanned(step), result) : [];
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
  // At the start, the boot watchdog holds from here: the answer is the user's, and the check, a job over every library
  // mod, shows its own progress (a first check after an upgrade fingerprints each mod and can take minutes)
  const release = globalThis.BAMTRecovery?.hold?.();
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
    release?.();
  }
}

// --- the list (owner, 2026-11-11: the drift prompt's popup; the wireframe of the thread) ---

// the segments over the cards: the held mods, the entries the check applied again, the other affected mods
export const TABS = ["held", "reapplied", "others"];
const OTHER_KINDS = { rebuilt: "kindRebuilt", new_parts: "kindNewParts", gone: "kindGone" };

// heldOf(result): the held mods the list shows: a mod whose rig changed, unless a repaired copy took its place (it has
// one and its student is not configured with it)
export const heldOf = (result) => (result?.mods || []).filter((m) => m.impact === "rig_changed" && !(m.repaired_id && !m.configured));

// tabMods(outcome): the mods each segment lists ({held, reapplied, others})
export function tabMods({ result, reapplied = [] } = {}) {
  const mods = result?.mods || [];
  return { held: heldOf(result), reapplied: reapplied.map(entryMod),
    others: mods.filter((m) => m.impact in OTHER_KINDS) };
}

// the segments' captions (no counts, owner 2026-11-11), as the list's toolbar takes them
export const tabCaptions = () => ["mods.impact.tabHeld", "mods.impact.tabReapplied", "mods.impact.tabOthers"];

// card(mod, tab, nameOf): a mod's card: the student's name, its own picture (none for a 3D model), the kind and Applied
// as chips; a held mod with the Files Changed badge, another kind with what happened to it as its badge
export function card(mod, tab, nameOf = (e) => e?.variant || e?.core || "") {
  const entry = { variant: mod.target, core: mod.core, kind: mod.kind };
  const base = mod.kind === "model" || mod.kind === "other" || !mod.id ? null : spineBase("mod", mod.id);
  const lobby = mod.kind === "lobby";
  // an Official chip over the details' one line of the official changes (owner, 2026-11-11)
  const chips = [T(kindKey(mod.kind)), ...(mod.configured ? [T("status.applied")] : []), ...(changeText(mod) ? [T("mods.impact.chipOfficial")] : [])];
  const badge = tab === "held" ? { text: T("mods.heldBadge"), color: COLOUR.warning, ink: "#ffffff" }
    : tab === "others" ? { text: T(`mods.impact.${OTHER_KINDS[mod.impact]}`), color: COLOUR.greyed, ink: "#ffffff" } : null;
  // the details name the mod itself (the card names the student), its kind and Applied as chips
  // a generic mod (other) has no student: its own name, never its set's key (owner, 2026-11-11)
  const name = mod.kind === "other" ? mod.label || T("settings.mods.other") : nameOf(entry) || mod.label || mod.core || "";
  return { key: mod.id || `${mod.core}`, label: name, full: mod.label || name, chips,
    ...(badge ? { badge } : {}), mod,
    thumb: base ? thumb(base, "card", null, lobby) : null,
    big: base ? () => thumb(base, mod.kind === "spr" ? "portrait" : "bust", null, lobby) : null };
}

// details(mod, tab): the details panel of a card (its name is the mod's, card.full; the Official chip over it):
// what the update changed, one line, no caption (owner, 2026-11-11: the state is the card's badge and the segment's);
// Go To, and for a held mod the repair's preview and Fix and Apply.
export function details(mod, tab) {
  const changes = changeText(mod).replace(/^[^:：]*[:：]\s*/, "");
  const fields = changes ? [[null, changes]] : [];
  const page = !!(mod.id && mod.kind !== "other" && mod.target);
  const actions = [...(page ? [["go", T("add.go")]] : []),
    ...(tab === "held" ? [["preview", T("mods.impact.preview")], ["fix", T("mods.impact.fix"), "apply"]] : [])];
  return { title: T(mod.kind === "lobby" ? "info.title.lobby" : mod.kind === "model" ? "info.title.model" : "info.title.portrait"),
    fields, actions };
}

// presentList(outcome, nameOf): the list until Close or Go To answers it ({go: mod} or {}). A click on a card shows it
// in the details; on Needs Fix the tick box at its corner ticks it (owner, 2026-11-11). Preview Fix and Fix and Apply
// run over the list, which stays open and is drawn again as the mods stand then (owner, 2026-11-11: never closed under
// the user).
function presentList(outcome, nameOf) {
  const host = document.getElementById("prompt");
  let current = outcome, tabs = tabMods(current);
  let tab = TABS.findIndex((name) => tabs[name].length);
  if (tab < 0) tab = 0;
  let selection = new Set(tabs.held.map((m) => m.id)), focus = null, scroll = 0, running = false;
  return new Promise((resolve) => {
    const done = (answer) => {
      if (running) return;
      host.hidden = true;
      host.innerHTML = "";
      Popup.closed(host);
      resolve(answer);
    };
    // a repair over the list, then the list as the mods stand: the result read again, the ticks kept where they still apply
    const work = async (mods, review) => {
      if (running || !mods.length) return;
      running = true;
      try {
        const { partial } = await fix(mods, nameOf, { review });
        if (partial.length) await Popup.message({ title: T("mods.impact.title"), ok: T("picker.ok"), plain: true, width: WIDTH,
          lines: [T("mods.impact.partial", { n: partial.length }), ...partial.map((m) => ({ text: modName(m, nameOf), ...ITEM }))] });
        const id = await rpc("mods.impact", { force: true });
        current = { ...current, result: await Loading.task((update) => waitJob(id, update), { label: T("mods.impact.checking") }) };
        tabs = tabMods(current);
        selection = new Set(tabs.held.map((m) => m.id).filter((id2) => selection.has(id2)));
      } catch (err) {
        notify(errorText(err.code, err.params), { kind: "error", error: err });
      } finally {
        running = false;
        draw(scroll);
      }
    };
    const draw = (keep = 0) => {
      scroll = keep;
      const name = TABS[tab], held = name === "held";
      const items = tabs[name].map((mod) => card(mod, name, nameOf));
      if (!items.some((it) => it.key === focus)) focus = items[0]?.key ?? null;
      const ticked = tabs.held.filter((m) => selection.has(m.id));
      Popup.listPopup({
        host, title: T("mods.impact.title"), items, selected: focus, focus, keepScroll: keep,
        detailKind: (it) => it.mod?.kind, alignStart: true, empty: T("mods.impact.none"),
        // portraits alone: the portrait switch's frame and tall cards, whose details keep their actions in view
        ...(items.length && items.every((it) => it.mod?.kind === "spr") ? { layoutKind: "spr" } : {}),
        ...(held ? { tick: { keys: [...selection], onTick: (key, s) => {
          selection = selection.has(key) ? new Set([...selection].filter((k) => k !== key)) : new Set([...selection, key]);
          draw(s);
        } } } : {}),
        tools: { segs: tabCaptions(tabs), seg: tab, onSeg: (i) => { tab = i; focus = null; draw(); }, sortKey: "sort.default" },
        // Fix and Apply without a count, so the S footer's caption keeps to one line; the tick boxes show what it fixes
        buttons: [{ label: T("display.close"), fn: () => done({}) },
          ...(held ? [{ label: T("mods.impact.fix"), primary: true, greyed: !ticked.length,
            fn: () => work(ticked, false) }] : [])],
        onClose: () => done({}),
        onSelect: (key, s) => { focus = key; draw(s); },
        details: (it) => {
          const d = details(it.mod, name);
          const act = { go: () => done({ go: it.mod }), preview: () => work([it.mod], true), fix: () => work([it.mod], false) };
          return { title: d.title, fields: d.fields, actions: d.actions.map(([id, caption, semantic]) => [caption, act[id], false, semantic]) };
        },
      }).catch((err) => { console.error("impact list", err); done({}); });
    };
    draw();
  });
}

// the repaired copy takes a mod's place where its student is configured with it (actions.apply: only configured while
// mods are off)
async function applyRepaired(mod, id) {
  if (mod.configured) await waitJob(await rpc("actions.apply", { variant: mod.target, source: `mod:${id}`, kind: mod.kind }));
}

// fix(mods, nameOf, {review}): the student page's Update for each held mod, here: a repaired copy made before takes
// its place at once; a mod the repair can take is prepared with the default repair (review: the choice and the review,
// whose Accept is Fix and Apply) and, unless partial, stored and put in its place; another gets the asset update.
// Resolves with {fixed, partial}: the mods fixed, and those whose repair was partial (not stored, not applied).
async function fix(mods, nameOf, { review = false } = {}) {
  const fixed = [], partial = [];
  for (const mod of mods) {
    const subject = modName({ ...mod, configured: false }, nameOf);
    try {
      if (mod.repaired_id) {
        await applyRepaired(mod, mod.repaired_id);
        fixed.push(mod);
        continue;
      }
      const report = await Loading.hold(() => Compatibility.forUpdate(mod.id, mod.label, subject), { all: true });
      if (!report) continue;
      if (!(Compatibility.eligible(mod) && Compatibility.canRemap(mod.id, mod))) {
        // drawn on a rig the App does not have: an update would change nothing, so it stays held; the preview shows
        // the compatibility result that says why, as the student page's Update does
        if (Compatibility.rigUnknown(mod.id)) {
          if (review) await Compatibility.show(report);
          continue;
        }
        // the asset update moves the mod's assets onto the new parts as they are: it looks the same after, said so
        if (review) {
          const answer = await Popup.message({ title: T("mods.impact.preview"), lines: [T("mods.impact.noPreview")],
            cancel: T("photo.back"), ok: T("mods.impact.fix"), footer: "XS", compact: true });
          if (answer.button !== "ok") continue;
        }
        await Loading.task(async (update) => waitJob(await rpc("actions.update", { mod: mod.id }), update),
          { label: T("progress.process", { name: subject }) });
        fixed.push(mod);
        continue;
      }
      const result = await Compatibility.prepare(mod.id, mod.label, subject, mod.kind, review ? null : "default");
      if (!result || result.unchanged) continue;
      try {
        if (review) {
          if (!await Compatibility.review(mod.id, result, { label: subject, kind: mod.kind, accept: T("mods.impact.fix"), discard: T("photo.back") })) continue;
        } else if (result.report?.partial) {
          partial.push(mod);
          continue;
        }
        const saved = await Compatibility.accept(mod.id, result, () => {}, subject);
        if (saved?.mod?.id) await applyRepaired(mod, saved.mod.id);
        fixed.push(mod);
      } finally {
        await Compatibility.discard(result);
      }
    } catch (err) {
      notify(errorText(err.code, err.params), { kind: "error", error: err });
    }
  }
  return { fixed, partial };
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
  try { return (await fix(mods, await names().catch(() => undefined))).fixed; } finally { busy = false; }
}

// check(): once the first screen is in, the summary a check left (gate), as the list, until it is closed (its preview
// and fixes run over it, presentList); Go To leaves for the mod's student page
export async function check() {
  if (busy || !pending) return null;
  busy = true;
  let chosen = null;
  const outcome = pending;
  pending = null;
  try {
    const nameOf = await names().catch(() => undefined);
    await Popup.whenIdle();
    chosen = (await presentList(outcome, nameOf)).go || null;
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
    // the summary as it stands: the library answers at once when nothing changed since its last check; no new scan
    // (a game update's drift is reconciled by the scans themselves, prompts.js reconcile)
    pending = await run({ force: true, scan: false });
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
