// What a game update did under the library's mods (docs/webui/library.md, "Game update impact"). At the start, before
// the first screen (gate): once the install's resource version differs from the last one seen and the library has mods
// (mods.updateDue), the page asks whether to check them. Check runs under the loading screen (run): the library
// classifies every mod from the official parts it was built against (mods.impact), and with mods on and the file check
// allowed, applied entries the update overwrote with official files that need no repair are applied again
// (mods.restoreDrift). Once the first screen is in, the summary lists the held mods to update, only when there are any
// (check). Closing it records the version as seen (mods.impactSeen); Settings can reopen the list from the last check
// (view, mods.impactLast) or run the check again. Before the question, outdated costume builds are rebuilt (refresh) and
// entries whose last write failed are retried once per App build.
import * as Compatibility from "../mods/mod-compatibility.js";
import * as Loading from "./loading.js";
import { modJob } from "../core/job-progress.js";
import * as Popup from "../ui/popup.js";
import { notify } from "../ui/popup.js";
import { on, rpc, spineBase, waitJob } from "../core/api.js";
import { thumb } from "../detail/thumbs.js";
import { T, errorText } from "../core/i18n.js";
import * as ModSwitch from "../mods/mod-switch.js";
import { entryLabel } from "../mods/mod-switch.js";
import { COLOUR, SPACE, TYPE } from "../ui/ui-tokens.js";

// a held entry's line (the switch help's Details): Go To closes the message with "go:<mod id>", Update and Apply with
// "fix:<mod id>"
export const GO = "go:",
  FIX = "fix:";
export const ITEM = Object.freeze({ marker: "\u2022", markerGap: 44, indent: 52 });
export const WIDTH = 2098;
// what changed in a mod's official parts, under its line: the dense type, held close to the line it belongs to (a
// label to its own decoration, SPACE.xs, instead of the lines' SPACE.l)
export const DETAIL = Object.freeze({ fontSize: TYPE.dense.fs, minFontSize: TYPE.dense.fs, gap: SPACE.xs - SPACE.l });

const kindKey = (kind) => (kind === "model" ? "tab.model" : kind === "lobby" ? "tab.lobby" : "tab.portrait");

/**
 * What the update changed in a mod's official parts ("Official Portrait: atlas repacked, 1 new animation", from
 * mods.impact's changes), "" when nothing is known. A repack carries its texture sizes.
 */
export function changeText(mod, sep = T("mods.impact.sep")) {
  const c = mod?.changes || {};
  const parts = [];
  if (c.repacked) parts.push(T("mods.impact.change.repacked"));
  if (c.animations > 0)
    parts.push(
      c.animations === 1 ? T("mods.impact.change.animation") : T("mods.impact.change.animations", { n: c.animations }),
    );
  if (c.rig) parts.push(T("mods.impact.change.rig"));
  if (c.textures && !c.repacked) parts.push(T("mods.impact.change.textures"));
  if (c.removed) parts.push(T("mods.impact.change.removed"));
  if (c.added) parts.push(T("mods.impact.change.added"));
  if (!parts.length) return "";
  const changes = parts.join(sep);
  return mod.kind === "other"
    ? T("mods.impact.officialFiles", { changes })
    : T("mods.impact.official", { kind: T(kindKey(mod.kind)), changes });
}

/**
 * "student - kind - mod name" (the mod's own name left out where it only repeats the student's), then "Applied"
 * for a mod its student is configured with (mods.impact's configured: whatever the switch says).
 * @param {(entry: object) => string} [nameOf] Names the mod's variant.
 */
export function modName(mod, nameOf = (e) => e?.variant || e?.core || "") {
  const applied = mod?.configured ? ` - ${T("status.applied")}` : "";
  if (mod?.kind === "other") return `${mod.label || mod.core || ""} - ${T("settings.mods.other")}${applied}`;
  const entry = { variant: mod?.target, core: mod?.core, kind: mod?.kind };
  const named = entryLabel(entry, nameOf, " - ");
  return `${mod?.label && mod.label !== nameOf(entry) ? `${named} - ${mod.label}` : named}${applied}`;
}

/**
 * A mod's lines: its name with Go To (none for a generic mod: no student page) and, for a held mod (fix), Fix
 * after it; then what changed in its official parts.
 * @param {object} mod The mod as mods.impact lists it.
 * @param {(entry: object) => string} nameOf Names the mod's variant.
 * @param {object} [options]
 * @param {number} [options.indent] The indent of the change line.
 * @param {boolean} [options.marker] Hang a bullet before the line.
 * @param {boolean} [options.fix] Add the Fix button.
 */
export function modLines(mod, nameOf, { indent = ITEM.indent, marker = true, fix = false } = {}) {
  const page = mod?.id && mod.kind !== "other" && mod.target;
  const repair = fix && mod?.id ? [{ label: T("mods.impact.fix"), fn: ({ close }) => close(`${FIX}${mod.id}`) }] : [];
  const go = page
    ? { button: { label: T("add.go"), inline: true, fn: ({ close }) => close(`${GO}${mod.id}`) }, more: repair }
    : repair.length
      ? { button: { ...repair[0], inline: true } }
      : {};
  const line = {
    text: modName(mod, nameOf),
    indent,
    ...(marker ? { marker: ITEM.marker, markerGap: ITEM.markerGap } : {}),
    ...go,
  };
  const change = changeText(mod);
  return change ? [line, { text: change, indent, ...DETAIL }] : [line];
}

/**
 * The scan's entries a game update overwrote with official files (state updated, not dismissed) that need no
 * repair: not those whose configured mod's rig changed (held: listed with their mods, applied only once
 * repaired).
 */
export function reappliable(entries, result) {
  const updated = (entries || []).filter((e) => e && e.state === "updated" && !e.dismissed);
  const held = heldKeys(updated, result?.mods || []);
  return updated.filter((e) => !held.has(`${e.install}|${e.core}`));
}

/** The mods a drift prompt's entries are configured with ("mod:<id>"), for mods.impact {ids}. */
export const configuredMods = (entries) => [
  ...new Set((entries || []).map((e) => /^mod:(.+)$/.exec(e?.configured || "")?.[1]).filter(Boolean)),
];

/** The keys of the entries whose configured mod's rig an update changed (from mods.impact {ids}). */
export function heldKeys(entries, impacts) {
  const changed = new Set((impacts || []).filter((m) => m.impact === "rig_changed").map((m) => m.id));
  return new Set(
    (entries || [])
      .filter((e) => changed.has(/^mod:(.+)$/.exec(e?.configured || "")?.[1]))
      .map((e) => `${e.install}|${e.core}`),
  );
}

// --- the page ---

let names = async () => (e) => e?.variant || e?.core || "";
let goTo = async () => {}; // main.js: a mod's student page with its switch open on the mod
let busy = false;
let pending = null; // a check's outcome waiting for the first screen: {result, reapplied, failed}
/** Whether a check or a repair is running. */
export const isBusy = () => busy;

/** Follow game updates while the program runs: the game services are rebuilt for the new resource version. */
export function init(options = {}) {
  if (options.names) names = options.names;
  if (options.go) goTo = options.go;
  // a game update while the program runs: the game services were rebuilt for the new resource version
  on("config.changed", () => {
    Popup.whenIdle()
      .then(async () => {
        if (await gate()) await check();
      })
      .catch((err) => console.error("impact", err));
  });
}

/** Go To: the mod's student page with its switch open on the mod. */
export const open = (mod) =>
  Promise.resolve(goTo({ id: mod.id, variant: mod.target, kind: mod.kind })).catch((err) =>
    console.error("impact", err),
  );

// the progress of a check (Loading.task's update): its step, or the mod at hand while the mods are classified and
// applied again (job-progress.js modLine)
function stepper(update) {
  return (key) =>
    (p = {}) =>
      update({ ...p, label: T(key) });
}

// the scan the check reads its overwritten entries from: a new one, with mods on and the file check allowed; [] else
async function scanned(step) {
  const s = ModSwitch.current() || (await ModSwitch.refresh().catch(() => null));
  if (s?.scan !== true || s.enabled === false) return [];
  step("progress.check")();
  const done = new Promise((resolve) => {
    let off = null;
    const timer = setTimeout(() => {
      off?.();
      resolve(null);
    }, 60000);
    off = on("library.scanned", (data) => {
      clearTimeout(timer);
      off();
      resolve(data);
    });
  });
  await rpc("mods.scan", {});
  await done;
  return (await rpc("mods.entries").catch(() => [])) || [];
}

// run({force}): the check under the loading screen with its progress: classify, scan, apply again what needs no
// repair. force: whatever was seen (Check Update Impact). Resolves with {result, reapplied, failed}; throws what
// the jobs refuse.
async function run({ force = false, scan = true } = {}) {
  await names().catch(() => undefined); // the roster in, for the names on the progress line
  return Loading.task(
    async (update) => {
      const step = stepper(update);
      const result = await waitJob(
        await rpc("mods.impact", force ? { force: true } : {}),
        step("mods.impact.checking"),
      );
      const again = scan ? reappliable(await scanned(step), result) : [];
      let reapplied = [],
        failed = [];
      if (again.length) {
        const plain = again.map((e) => ({ install: e.install, core: e.core, state: e.state }));
        const done = await waitJob(await rpc("mods.restoreDrift", { entries: plain }), step("mods.impact.reapplying"));
        const ok = new Set((done?.done || []).map((d) => `${d.install}|${d.core}`));
        reapplied = again.filter((e) => ok.has(`${e.install}|${e.core}`));
        failed = done?.failed || [];
      }
      return { result, reapplied, failed };
    },
    { label: T("mods.impact.checking") },
  );
}

// refresh(): the costume builds of an older costume rule built again under the loading screen, with the plain progress
// of a writer task and nothing to answer: what cannot be built again is kept or left for the next start, never told
async function refresh() {
  await names().catch(() => undefined); // the roster in, for the names on the progress line
  await Loading.task(async (update) => waitJob(await rpc("mods.refreshCostumes"), update), {
    label: T("progress.modsProcess"),
  });
}

// retry(): the entries whose last write failed written again under the loading screen, nothing to answer: one that
// fails again keeps its fresh error on its row; a refusal of the moment leaves them for the next start
async function retry() {
  await names().catch(() => undefined); // the roster in, for the names on the progress line
  await Loading.task(async (update) => waitJob(await rpc("mods.retryFailed"), update), {
    label: T("progress.modsApply"),
  });
}

/**
 * At the start before the first screen (and after a game update while the program runs): the costume builds of
 * an older costume rule built again (refresh), the failed writes tried again (retry), then the question and the
 * check. Later asks again at the next start.
 * @returns {Promise<boolean>} True when a summary waits (check shows it).
 */
export async function gate() {
  if (busy) return false;
  const due = await rpc("mods.updateDue").catch(() => null);
  if (!due?.due && !due?.outdated && !due?.retry) return false;
  busy = true;
  // At the start, the boot watchdog holds from here: the answer is the user's, and the check, a job over every library
  // mod, shows its own progress (a first check after an upgrade fingerprints each mod and can take minutes; a costume
  // rebuild takes up to half a minute each)
  const release = globalThis.BAMTRecovery?.hold?.();
  try {
    if (due.outdated) await refresh().catch((err) => console.error("costume refresh", err));
    // Later at the game update question writes nothing (the check applies mods again): a retry waits for the check
    if (due.retry && !due.due) await retry().catch((err) => console.warn("failed writes retry", err));
    if (!due.due) return false;
    const answer = await Popup.message({
      title: T("mods.impact.gateTitle"),
      lines: [T("mods.impact.gateAsk")],
      close: false,
      buttons: [
        { label: T("update.later"), value: "later" },
        { label: T("mods.impact.gateCheck"), value: "check", primary: true },
      ],
    });
    if (answer.button !== "check") return false;
    const outcome = await run();
    if (due.retry) await retry().catch((err) => console.warn("failed writes retry", err));
    // nothing to update: done without a list (the version recorded as seen), only a failure told
    if (!heldOf(outcome.result).length) {
      await seen(outcome.result);
      if (!outcome.failed?.length) return false;
    }
    pending = outcome;
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

// --- the list (the drift prompt's popup) ---

/**
 * The mods the list shows, those to update: a mod whose rig changed, unless a repaired copy took its place (it
 * has one and its student is not configured with it).
 */
export const heldOf = (result) =>
  (result?.mods || []).filter((m) => m.impact === "rig_changed" && !(m.repaired_id && !m.configured));

/** A card's key: its mod, else its core. */
export const modKey = (mod) => mod?.id || `${mod?.core || ""}`;

/**
 * A mod's card: the student's name, its own picture (none for a 3D model), the kind and Applied as chips, the
 * Outdated badge.
 */
export function card(mod, nameOf = (e) => e?.variant || e?.core || "") {
  const entry = { variant: mod.target, core: mod.core, kind: mod.kind };
  const base = mod.kind === "model" || mod.kind === "other" || !mod.id ? null : spineBase("mod", mod.id);
  const lobby = mod.kind === "lobby";
  // an Official chip over the details' one line of the official changes
  const chips = [
    T(kindKey(mod.kind)),
    ...(mod.configured ? [T("status.applied")] : []),
    ...(changeText(mod) ? [T("mods.impact.chipOfficial")] : []),
  ];
  const badge = { text: T("mods.heldBadge"), color: COLOUR.warning, ink: "#ffffff" };
  // the details name the mod itself (the card names the student), its kind and Applied as chips
  // a generic mod (other) has no student: its own name, never its set's key
  const name =
    mod.kind === "other" ? mod.label || T("settings.mods.other") : nameOf(entry) || mod.label || mod.core || "";
  return {
    key: modKey(mod),
    label: name,
    full: mod.label || name,
    chips,
    badge,
    mod,
    thumb: base ? thumb(base, "card", null, lobby) : null,
    big: base ? () => thumb(base, mod.kind === "spr" ? "portrait" : "bust", null, lobby) : null,
  };
}

/**
 * The details panel of a card (its name is the mod's, card.full; the Official chip over it): what the update
 * changed, one line, no caption (the state is the card's badge); Go To, the update's preview and Update and
 * Apply.
 */
export function details(mod) {
  const changes = changeText(mod).replace(/^[^:：]*[:：]\s*/, "");
  const fields = changes ? [[null, changes]] : [];
  const page = !!(mod.id && mod.kind !== "other" && mod.target);
  const actions = [
    ...(page ? [["go", T("add.go")]] : []),
    ["preview", T("mods.impact.preview")],
    ["fix", T("mods.impact.fix"), "apply"],
  ];
  return {
    title: T(
      mod.kind === "lobby" ? "info.title.lobby" : mod.kind === "model" ? "info.title.model" : "info.title.portrait",
    ),
    fields,
    actions,
  };
}

/**
 * The list's footer buttons: Close, Check Update Impact and, with mods to update, Update and Apply for the
 * ticked ones (greyed with none ticked); the ids are the buttons' actions.
 * @param {number} count The number of mods to update.
 * @param {number} ticked The number ticked.
 */
export const footer = (count, ticked) => [
  ["close", T("display.close")],
  ["check", T("mods.impact.check")],
  ...(count ? [["fix", T("mods.impact.fix"), !ticked]] : []),
];

// recheck(): Check Update Impact: the check again, after a question when the install's resource version is the
// one last checked (no game update recorded: the check takes long). Resolves with its outcome, null when not asked for.
async function recheck() {
  const last = await rpc("mods.impactLast").catch(() => null);
  if (last && !last.update) {
    const answer = await Popup.message({
      title: T("mods.impact.check"),
      lines: [T("mods.impact.checkAsk")],
      cancel: T("popup.cancel"),
      ok: T("mods.impact.gateCheck"),
    });
    if (answer.button !== "ok") return null;
  }
  return run({ force: true, scan: false });
}

// presentList(outcome, nameOf): the list until Close or Go To answers it ({go: mod, checked} or {checked}: checked,
// the result of a check run from it). A click on a card shows it in the details; the tick box at its corner ticks it.
// Preview, Update and Apply and Check run over the list, which stays open and is drawn again as the mods stand then
// (never closed under the user).
function presentList(outcome, nameOf) {
  const host = document.getElementById("prompt");
  let current = outcome,
    mods = heldOf(current.result),
    checked = null;
  let selection = new Set(mods.map(modKey)),
    focus = null,
    scroll = 0,
    running = false;
  return new Promise((resolve) => {
    const done = (answer) => {
      if (running) return;
      host.hidden = true;
      host.innerHTML = "";
      Popup.closed(host);
      resolve({ ...answer, checked });
    };
    // the list as the mods stand: the ticks kept where they still apply, a mod new to it ticked
    const update = (result) => {
      const before = new Set(mods.map(modKey));
      current = { ...current, result };
      mods = heldOf(result);
      selection = new Set(mods.map(modKey).filter((key) => selection.has(key) || !before.has(key)));
    };
    const busy = async (task) => {
      if (running) return;
      running = true;
      try {
        await task();
      } catch (err) {
        notify(errorText(err.code, err.params), { kind: "error", error: err });
      } finally {
        running = false;
        draw(scroll);
      }
    };
    // an update over the list, then the list as the mods stand: the last check without the mods updated since
    const work = (picked, review) =>
      picked.length &&
      busy(async () => {
        const { partial } = await fix(picked, nameOf, { review });
        if (partial.length)
          await Popup.message({
            title: T("mods.impact.title"),
            ok: T("picker.ok"),
            plain: true,
            width: WIDTH,
            lines: [
              T("mods.impact.partial", { n: partial.length }),
              ...partial.map((m) => ({ text: modName(m, nameOf), ...ITEM })),
            ],
          });
        update(await rpc("mods.impactLast"));
      });
    const check = () =>
      busy(async () => {
        const again = await recheck();
        if (!again) return;
        checked = again.result;
        update(again.result);
      });
    const draw = (keep = 0) => {
      scroll = keep;
      const items = mods.map((mod) => card(mod, nameOf));
      if (!items.some((it) => it.key === focus)) focus = items[0]?.key ?? null;
      const ticked = mods.filter((m) => selection.has(modKey(m)));
      const act = { close: () => done({}), check, fix: () => work(ticked, false) };
      Popup.listPopup({
        host,
        title: T("mods.impact.title"),
        items,
        selected: focus,
        focus,
        keepScroll: keep,
        detailKind: (it) => it.mod?.kind,
        alignStart: true,
        slimBand: true,
        empty: T("mods.impact.none"),
        // portraits alone: the portrait switch's frame and tall cards, whose details keep their actions in view
        ...(items.length && items.every((it) => it.mod?.kind === "spr") ? { layoutKind: "spr" } : {}),
        ...(items.length
          ? {
              tick: {
                keys: [...selection],
                onTick: (key, s) => {
                  selection = selection.has(key)
                    ? new Set([...selection].filter((k) => k !== key))
                    : new Set([...selection, key]);
                  draw(s);
                },
              },
            }
          : {}),
        // Update and Apply without a count, so the S footer's caption keeps to one line; the tick boxes show what it
        // updates
        buttons: footer(items.length, ticked.length).map(([id, label, greyed]) => ({
          label,
          fn: act[id],
          action: id,
          ...(id === "fix" ? { primary: true, greyed } : {}),
        })),
        onClose: () => done({}),
        onSelect: (key, s) => {
          focus = key;
          draw(s);
        },
        details: (it) => {
          const d = details(it.mod);
          const go = {
            go: () => done({ go: it.mod }),
            preview: () => work([it.mod], true),
            fix: () => work([it.mod], false),
          };
          return {
            title: d.title,
            fields: d.fields,
            actions: d.actions.map(([id, caption, semantic]) => [caption, go[id], false, semantic]),
          };
        },
      }).catch((err) => {
        console.error("impact list", err);
        done({});
      });
    };
    draw();
  });
}

// the repaired copy takes a mod's place where its student is configured with it (actions.apply: only configured while
// mods are off)
async function applyRepaired(mod, id) {
  if (!mod.configured) return;
  // under the loading screen, with its line: the student, the kind, the mod and the write
  const line = modJob(
    { variant: mod.target, kind: mod.kind, core: mod.core || "", name: mod.label || "" },
    { nameOf: Loading.variantName, action: "progress.actionApply" },
  );
  await Loading.task(
    async (update) => {
      update(line());
      return waitJob(await rpc("actions.apply", { variant: mod.target, source: `mod:${id}`, kind: mod.kind }), (p) =>
        update(line(p)),
      );
    },
    { label: line().text },
  );
}

// fix(mods, nameOf, {review}): the student page's Update for each held mod, here: a repaired copy made before takes
// its place at once; a mod the repair can take is prepared with the default repair (review: the choice and the review,
// whose Accept is Update and Apply) and, unless partial, stored and put in its place; another gets the asset update.
// Resolves with {fixed, partial}: the mods fixed, and those whose repair was partial (not stored, not applied).
async function fix(mods, nameOf, { review = false } = {}) {
  const fixed = [],
    partial = [];
  for (const mod of mods) {
    const subject = modName({ ...mod, configured: false }, nameOf);
    // who the mod is on the progress line of its check, repair or update (job-progress.js modJob)
    const who = { variant: mod.target, kind: mod.kind, core: mod.core || "", name: mod.label || "" };
    try {
      if (mod.repaired_id) {
        await applyRepaired(mod, mod.repaired_id);
        fixed.push(mod);
        continue;
      }
      const report = await Loading.hold(() => Compatibility.forUpdate(mod.id, mod.label, who), { all: true });
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
          const answer = await Popup.message({
            title: T("mods.impact.preview"),
            lines: [T("mods.impact.noPreview")],
            cancel: T("photo.back"),
            ok: T("mods.impact.fix"),
          });
          if (answer.button !== "ok") continue;
        }
        const line = modJob(who, { nameOf: Loading.variantName, action: "progress.actionRepair" });
        await Loading.task(
          async (update) => {
            update(line());
            return waitJob(await rpc("actions.update", { mod: mod.id }), (p) => update(line(p)));
          },
          { label: line().text },
        );
        fixed.push(mod);
        continue;
      }
      const result = await Compatibility.prepare(mod.id, mod.label, who, mod.kind, review ? null : "default");
      if (!result || result.unchanged) continue;
      try {
        if (review) {
          if (
            !(await Compatibility.review(mod.id, result, {
              label: subject,
              kind: mod.kind,
              accept: T("mods.impact.fix"),
              discard: T("photo.back"),
            }))
          )
            continue;
        } else if (result.report?.partial) {
          partial.push(mod);
          continue;
        }
        const saved = await Compatibility.accept(mod.id, result, () => {}, who);
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

/**
 * The mods.state entries a game update's rig change holds ({install, core, variant, kind, source, label}) as
 * the summary's mods (applied by definition).
 */
export const heldMods = (entries) =>
  (entries || []).map((e) => ({
    id: /^mod:(.+)$/.exec(e?.source || "")?.[1] || "",
    target: e?.variant,
    core: e?.core,
    kind: e?.kind,
    label: e?.label || "",
    configured: true,
  }));

/**
 * The held entries, each as the summary names a held mod (Go To and Update and Apply where its mod is known);
 * no hint under them.
 */
export function heldLines(entries, nameOf) {
  return heldMods(entries).flatMap((m) => modLines(m, nameOf, { fix: true }));
}

/**
 * The switch help's Details of the held entries.
 * @returns {Promise<{go?: object, fix?: object[]}>} What a button asked: {go: mod} or {fix: [mod]} (the caller
 *     closes the help first, then opens or repairs it), else {}.
 */
export async function heldDetails(entries) {
  const nameOf = await names().catch(() => undefined);
  const answer = await Popup.message({
    title: T("mods.heldTitle"),
    ok: T("picker.ok"),
    plain: true,
    width: WIDTH,
    lines: heldLines(entries, nameOf),
  });
  const button = typeof answer.button === "string" ? answer.button : "";
  const find = (id) => heldMods(entries).find((m) => m.id === id) || null;
  if (button.startsWith(GO)) return { go: find(button.slice(GO.length)) };
  if (button.startsWith(FIX)) return { fix: [find(button.slice(FIX.length))].filter(Boolean) };
  return {};
}

/**
 * Fix from outside the summary (the switch help's Details).
 * @returns {Promise<object[]>}
 */
export async function repair(mods) {
  if (busy || !mods?.length) return [];
  busy = true;
  try {
    return (await fix(mods, await names().catch(() => undefined))).fixed;
  } finally {
    busy = false;
  }
}

// the version a check classified recorded as seen: its summary was shown or not needed
async function seen(result) {
  if (result?.version)
    await rpc("mods.impactSeen", { install: result.install, version: result.version }).catch((err) =>
      console.error("impact", err),
    );
}

/**
 * Once the first screen is in, show the mods to update a check left (gate), as the list, until it is closed
 * (its preview, updates and a new check run over it, presentList); Go To leaves for the mod's student page.
 */
export async function check() {
  if (busy || !pending) return null;
  busy = true;
  let chosen = null;
  const outcome = pending;
  pending = null;
  try {
    const nameOf = await names().catch(() => undefined);
    await Popup.whenIdle();
    if (heldOf(outcome.result).length) {
      const answer = await presentList(outcome, nameOf);
      chosen = answer.go || null;
      if (answer.checked) await seen(answer.checked);
    }
    await seen(outcome.result);
    if (outcome.failed?.length)
      await Popup.message({
        title: T("mods.impact.title"),
        lines: outcome.failed.map((f) => `${ModSwitch.entryLabel(f.entry, nameOf)} - ${errorText(f.code, f.params)}`),
        ok: T("picker.ok"),
      });
    return outcome.result;
  } catch (err) {
    console.error("impact", err);
    return null;
  } finally {
    busy = false;
    if (chosen) await open(chosen);
  }
}

/**
 * The mods to update from the last check, without checking (mods.impactLast), as the list.
 * @param {object} [options]
 * @param {boolean} [options.always] True (Settings' View Mods to Update): open it even with none; false (the
 *     tray's Back to Official, Settings' Check after a game update's entries): only with mods to update.
 * @returns {Promise<boolean>} True when a Go To left for a student page.
 */
export async function view({ always = true } = {}) {
  if (busy) return false;
  let chosen = null;
  try {
    busy = true;
    const last = await rpc("mods.impactLast");
    if (!always && !heldOf(last).length) return false;
    const nameOf = await names().catch(() => undefined);
    const answer = await presentList({ result: last }, nameOf);
    chosen = answer.go || null;
    if (answer.checked) await seen(answer.checked);
  } catch (err) {
    notify(errorText(err.code, err.params), { kind: "error", error: err });
    return false;
  } finally {
    busy = false;
  }
  if (!chosen) return false;
  const before = location.hash;
  await open(chosen);
  return location.hash !== before;
}

/** The list, only when the last check left mods to update (view). */
export const show = () => view({ always: false });

/**
 * The impacts of the mods the entries are configured with.
 * @returns {Promise<object[]>} The impacts; [] when none or on a failure.
 */
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
