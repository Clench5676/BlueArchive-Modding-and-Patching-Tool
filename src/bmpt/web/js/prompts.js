// The prompts of the mods already in use (docs/webui/mods-switch.md, "Mods already in use"): the mods the last
// scan found outside the library ("Mods Not in the Library"), the configured entries a Steam check, a repair or
// a game update put back to official ("Mods Back to Official"), and the messages of an overwritten entry (no
// official copy anywhere): Add First while its mod is not in the library, the Overwritten message with the
// repair lines once it is. Every list comes from the scan (mods.entries), so the prompts exist only with the
// scan consent; the messages need none (the detail screen judges its files cheaply). The pure parts (which
// entries a prompt lists, its cards and details, what its buttons do, the drift result) are tested in Node
// (tests/js/prompts.test.mjs).
import { notify } from "./popup.js";
import * as Loading from "./loading.js";
import * as ModSwitch from "./mod-switch.js";
import * as Popup from "./popup.js";
import { COLOUR } from "./ui-tokens.js";
import { on, rpc, spineBase, waitJob } from "./api.js";
import { T } from "./i18n.js";
import { addFirstLines, overwrittenLines } from "./repairs.js";
import { thumb } from "./thumbs.js";
import * as Impact from "./update-impact.js";

// --- pure parts ---

/** The key of a scan entry: its install and its core. */
export const entryKey = (e) => `${e?.install}|${e?.core}`;

/**
 * An alternate lobby core's suffix ("gl", "teen"), shown after the kind; none for the primary core and
 * portraits.
 */
export function lobbySuffix(core) {
  const m = /_home_([a-z0-9]+)$/i.exec(String(core || ""));
  return m ? m[1].toLowerCase() : "";
}

/** The kind chip: Portrait, Memorial Lobby, or the memorial lobby of an alternate core with its suffix. */
export function kindLabel(entry) {
  const kind = entry?.kind || (/_spr$/i.test(entry?.core || "") ? "spr" : "lobby");
  if (kind === "other") return T("settings.mods.other");
  if (kind !== "lobby") return T(kind === "model" ? "tab.model" : "tab.portrait");
  const suffix = lobbySuffix(entry?.core);
  return suffix ? `${T("tab.lobby")} ${suffix}` : T("tab.lobby");
}

/** The install an entry is in, from mods.state.installs. */
export const installOf = (state, key) => (state?.installs || []).find((i) => i.key === key) || null;

/**
 * The id that names another install's files in a skeleton URL (<core>@<id>); null for the current install's.
 */
export function installId(state, key) {
  if (!key || key === state?.current) return null;
  return installOf(state, key)?.id || null;
}

/**
 * A card's skeleton: the entry's core in its own install, so a card never shows the current install's file of
 * the same name; null for kinds without a skeleton.
 */
export const entryBase = (entry, state) =>
  ["model", "other"].includes(entry?.kind)
    ? null
    : spineBase(entry?.kind === "lobby" ? "lobby" : "spr", entry?.core, installId(state, entry?.install));

/**
 * The entries a prompt lists, from the last scan (mods.entries), leaving out the dismissed ones unless asked
 * (Settings' Check lists them again). For "drift": the reset entries ("listed", while mods are on; a game
 * update's updated ones are the update summary's, update-impact.js) and the stuck ones (pending, overwritten),
 * which no repair brought back, apart ("apart"). For "untracked": the untracked entries and the configured
 * changed ones ("listed"), the damaged ones apart.
 * @param {object[]} entries The scan's entries.
 * @param {object} [options]
 * @param {boolean} [options.dismissed] Include the dismissed entries.
 * @param {boolean} [options.enabled] Whether mods are on.
 * @returns {{listed: object[], apart: object[]}}
 */
export function promptEntries(entries, which, { dismissed = false, enabled = true } = {}) {
  const open = (e) => dismissed || !e.dismissed;
  const list = (entries || []).filter((e) => e && typeof e === "object");
  if (which === "drift") {
    return {
      listed: enabled ? list.filter((e) => open(e) && e.state === "reset") : [],
      apart: list.filter((e) => e.state === "pending" && e.overwritten),
    };
  }
  return {
    listed: list.filter((e) => open(e) && ["untracked", "changed"].includes(e.state)),
    apart: list.filter((e) => e.state === "damaged"),
  };
}

/**
 * Whether a scan's entries give a prompt something new to show: an entry it lists that it has not shown in this
 * state.
 * @param {object[]} listed The entries the prompt lists.
 * @param {Set<string>} shown The `key|state` pairs shown already.
 */
export function fresh(listed, shown) {
  return listed.some((e) => !shown.has(`${entryKey(e)}|${e.state}`));
}

/**
 * The official copy of an entry's non-official files, as its details name it: Kept, Backup, Other Folder, or No
 * Official Copy.
 */
export function copyText(entry) {
  const files = (entry?.files || []).filter((f) => !f.official);
  if (!files.length) return T("mods.copy.kept");
  if (files.some((f) => !f.copy)) return T("mods.copy.none");
  const order = ["kept", "backup", "elsewhere", "game"];
  const best =
    order.find((c) => files.every((f) => f.copy === c)) ||
    files.map((f) => f.copy).sort((a, b) => order.indexOf(b) - order.indexOf(a))[0];
  return T(best === "game" ? "mods.copy.kept" : `mods.copy.${best}`);
}

/**
 * An entry's CRC state as the details name it: Fixed (trailing bytes), Needs Fix (only from a switch's record),
 * else Not Needed.
 */
export function crcText(entry) {
  const states = (entry?.files || []).filter((f) => !f.official).map((f) => f.crc);
  if (states.includes("needs_fix")) return T("crc.needed");
  if (states.includes("fixed") || entry?.fixed) return T("crc.fixed");
  return T("crc.original");
}

/** A size as the prompts show it. */
export const sizeText = (bytes) => ModSwitch.sizeText(bytes);

/**
 * One card of a prompt, for popup.js listPopup (its thumbnail is added by the page): the variant's name (the
 * kind is a chip of the details panel, not part of the label), the kind and install chips (the install only
 * when more than one is known), "No Official Copy" on an overwritten entry; damaged and stuck entries cannot be
 * ticked and are drawn dim with their state.
 * @param {object} entry The scan entry.
 * @param {object} [options]
 * @param {object|null} [options.state] mods.state.
 * @param {(entry: object) => string} [options.nameOf] Names the entry's variant.
 * @param {string} [options.which] The prompt, "untracked" or "drift".
 */
export function card(entry, { state = null, nameOf = (e) => e?.variant || e?.core || "", which = "untracked" } = {}) {
  const name = nameOf(entry) || entry?.variant || entry?.core || "";
  // the card's label is the name alone, at the most room the card has; the kind is the details panel's first chip
  const label = name;
  const installs = state?.installs || [];
  const chips = [kindLabel(entry)];
  if (installs.length > 1) chips.push(installOf(state, entry.install)?.label || entry.install);
  if (entry.overwritten) chips.push(T("mods.copy.none"));
  const apart = which === "untracked" ? entry.state === "damaged" : entry.state === "pending" && entry.overwritten;
  const badge =
    entry.state === "damaged"
      ? { text: T("mods.damaged"), color: COLOUR.greyed, ink: "#ffffff" }
      : entry.overwritten
        ? {
            text: which === "drift" ? T("mods.overwritten") : T("mods.copy.none"),
            color: COLOUR.greyed,
            ink: "#ffffff",
          }
        : null;
  return {
    key: entryKey(entry),
    label,
    full: name,
    kind: entry.kind,
    size: apart ? 0 : entry.size || 0,
    chips,
    disabled: apart,
    dim: apart,
    badge,
    entry,
  };
}

/**
 * The panel on the left of a prompt: File, Size, CRC state and Official Copy; a damaged or stuck entry's
 * Details button opens its repair lines.
 * @param {object} entry The scan entry.
 * @param {object} [options]
 * @param {string} [options.which] The prompt, "untracked" or "drift".
 */
export function details(entry, { which = "untracked" } = {}) {
  const files = entry?.files || [];
  // the files not official (an entry back to official: all of them)
  const modded = files.filter((f) => !f.official);
  const shown = modded.length ? modded : files;
  const name = (shown[0] || files[0])?.file?.split(/[\\/]/).pop() || entry?.core || "-";
  const more = shown.length > 1 ? ` +${shown.length - 1}` : "";
  const size = shown.reduce((n, f) => n + (f.size || 0), 0);
  const fields = [
    [T("field.file"), `${name}${more}`],
    [T("field.size"), size ? sizeText(size) : "-"],
  ];
  if (entry?.state !== "damaged") fields.push([T("field.crc"), crcText(entry)], [T("field.copy"), copyText(entry)]);
  const repair =
    entry?.state === "damaged" || (which === "drift" ? entry?.state === "pending" && entry?.overwritten : false);
  return { title: kindLabel(entry), fields, repair };
}

/**
 * The footer of a prompt: the ticked cards and, for what Add to Library copies, their total size.
 * @param {Set<string>} selection The ticked keys.
 * @param {object} [options]
 * @param {boolean} [options.size] Show the total size (Add to Library copies; the drift prompt does not).
 */
export function footer(items, selection, { sep = ModSwitch.listSep(), size = true } = {}) {
  const ticked = items.filter((it) => selection.has(it.key));
  const count = T("mods.selected", { n: ticked.length });
  return size ? `${count}${sep}${T("mods.size", { size: sizeText(Popup.sizeOf(items, selection)) })}` : count;
}

/**
 * What the untracked prompt's buttons do: add the ticked cards (and, with Add and Turn On Mods, turn mods on
 * after); with Don't Ask Again ticked, the cards not added are dismissed (by their files' names, sizes and
 * dates). Later adds nothing, and with the box ticked dismisses every card.
 * @param {Set<string>} selection The ticked keys.
 * @param {boolean} [dontAsk] Whether Don't Ask Again is ticked.
 * @returns {{adopt: object[], dismiss: object[], turnOn: boolean}} What to do.
 */
export function untrackedOutcome(button, items, selection, dontAsk = false) {
  const entries = (pick) => items.filter(pick).map((it) => ({ install: it.entry.install, core: it.entry.core }));
  const adding = button === "add" || button === "addTurnOn";
  const adopt = adding ? entries((it) => !it.disabled && selection.has(it.key)) : [];
  const dismiss = dontAsk ? entries((it) => !it.disabled && !(adding && selection.has(it.key))) : [];
  return { adopt, dismiss, turnOn: button === "addTurnOn" };
}

/**
 * Apply Mod Configuration's result: no popup when everything was done (null); otherwise the failed entries with
 * their error strings, and the ones skipped because their files changed since the scan (another tool, the
 * classic UI) with "Game files changed, check again".
 */
export function driftSummary(result, nameOf) {
  const list = (k) => result?.[k] || [];
  const skipped = list("skipped").filter((s) => ["changed", "untracked", "entry_changed"].includes(s.why));
  const kept = list("done").some((item) => item.kept_current),
    incomplete = list("done").some((item) => item.incomplete);
  if (!list("failed").length && !skipped.length && !list("stuck").length && !kept && !incomplete) return null;
  const label = (e) => ModSwitch.entryLabel(e, nameOf);
  const lines = [T("mods.result", { done: list("done").length, failed: list("failed").length + skipped.length })];
  if (kept) lines.push(T("mods.keptCurrent"));
  if (incomplete) lines.push(T("mods.incomplete"));
  if (list("stuck").length) lines.push(T("mods.stuck", { n: list("stuck").length }));
  const failures = [
    ...list("failed").map((f) => `${label(f.entry)} - ${ModSwitch.errorText(f.code, f.params)}`),
    ...skipped.map((s) => `${label(s)} - ${T("error.entry_changed")}`),
  ];
  lines.push(...failures.slice(0, 6));
  if (failures.length > 6) lines.push(`+${failures.length - 6}`);
  return { title: T("mods.driftTitle"), lines };
}

/**
 * The message for an overwritten entry's files: Add First while the mod in the game is not in the library (no
 * library source carries its non-official parts), else the Overwritten message with the repair lines.
 */
export function overwrittenKind(files) {
  const modded = (files || []).filter(
    (f) => !(typeof f.official === "boolean" ? f.official : f.applied === "official"),
  );
  return modded.length && modded.every((f) => /^(mod|variant):/.test(f.applied || "")) ? "overwritten" : "addFirst";
}

// --- the page ---

let names = async () => (e) => e?.variant || e?.core || "";
const shown = { drift: new Set(), untracked: new Set() }; // entry|state shown by each prompt in this page
let busy = false; // a prompt is open (or its action runs)
let started = false; // the start sequence reached its prompts: later scans may show them
/** Whether a prompt's job is running. */
export const isBusy = () => busy; // includes async card preparation before the popup joins the stack

/**
 * Follow the scan: a scan after the start (the window back in front, the classic window closed, a game folder
 * change) shows the prompts that have something new once no other popup is open.
 */
export function init(options = {}) {
  if (options.names) names = options.names;
  // the tray's Back to Official: the drift prompt, or the update summary when only a game update put entries back
  ModSwitch.setDriftPrompt(async () => {
    const entries = await rpc("mods.entries").catch(() => []);
    if ((await show("drift", { entries, force: true })) == null && !busy && entries.some((e) => e?.state === "updated"))
      await Impact.show();
  });
  // a scan after the start (the window back in front, the classic window closed, a game folder change): the prompts
  // that have something new show once no other popup is open
  on("library.scanned", () => {
    later().catch((err) => console.error("prompts", err));
  });
}

// Ready scan results keep startup priority. An unfinished background scan never holds the usable first screen's
// tour. While the install's game update check is due (Later at the start question, or the question not answered
// yet), its entries wait: only the check applies them again, never a scan before it.
let reconciling = false;
/**
 * Apply again, by themselves, the game update's overwritten entries (state updated) that need no repair while
 * mods are on, with no prompt: the configuration says they are applied, and once the switch is turned on again
 * the drift is reconciled. Those whose configured mod's rig changed stay held. A refusal (the game running,
 * Steam busy) leaves them for the next scan.
 * @param {object[]} entries The scan's entries.
 * @param {object} s mods.state.
 * @returns {Promise<boolean>} True when it wrote.
 */
export async function reconcile(entries, s) {
  if (reconciling || s?.enabled === false || s?.scan !== true || s?.available === false) return false;
  let updated = (entries || []).filter((e) => e?.state === "updated" && !e.dismissed);
  if (!updated.length) return false;
  const due = await rpc("mods.updateDue").catch(() => null);
  if (!due) return false; // unknown whether the check is due: nothing written
  if (due.due) updated = updated.filter((e) => e.install !== due.install);
  if (!updated.length) return false;
  const held = Impact.heldKeys(updated, await Impact.impacts(updated));
  const again = updated.filter((e) => !held.has(entryKey(e)));
  if (!again.length) return false;
  reconciling = true;
  try {
    await waitJob(
      await rpc("mods.restoreDrift", {
        entries: again.map((e) => ({ install: e.install, core: e.core, state: e.state })),
      }),
    );
    return true;
  } catch (err) {
    console.warn("drift reconcile", err);
    return false;
  } finally {
    reconciling = false;
  }
}

/** Show the start prompt a name asks for, if it has something to show. */
export async function start(which, info) {
  try {
    const s = await ModSwitch.refresh();
    if (s?.scan === true && !s.scanned) return;
    const entries = s?.scan === true ? await rpc("mods.entries").catch(() => []) : [];
    if (which === "drift") await reconcile(entries, s);
    if (ModSwitch.startDue(which, { info, state: s, entries })) await show(which, { entries });
  } finally {
    if (which === "untracked") {
      started = true;
      // Catch a scan published between the startup steps, before later() was enabled.
      later().catch((err) => console.error("prompts", err));
    }
  }
}

async function later() {
  if (busy || !started) return;
  await Popup.whenIdle();
  if (busy) return;
  const s = await ModSwitch.refresh();
  if (s?.scan !== true || !s.scanned) return;
  const entries = await rpc("mods.entries").catch(() => []);
  // the writes publish a scan of their own, which comes back here
  if (await reconcile(entries, s)) return;
  for (const which of ["drift", "untracked"]) {
    if (!ModSwitch.startDue(which, { info: {}, state: s, entries })) continue;
    await Popup.whenIdle();
    if (busy) return;
    if (!fresh(promptEntries(entries, which, { enabled: s.enabled !== false }).listed, shown[which])) continue;
    await show(which, { entries });
  }
}

/**
 * Show a prompt over the current screen. An empty list needs no popup.
 * @param {object} [options]
 * @param {object[]|null} [options.entries] The entries; the last scan's by default.
 * @param {boolean} [options.dismissed] Include the dismissed entries.
 * @param {boolean} [options.force] From Settings' Check or the tray: show it whatever was shown before.
 */
export async function show(which, { entries = null, dismissed = false, force = false } = {}) {
  if (busy) return null;
  const s = ModSwitch.current() || (await ModSwitch.refresh()) || {};
  const all = entries || (await rpc("mods.entries").catch(() => []));
  const lists = promptEntries(all, which, { dismissed, enabled: s.enabled !== false });
  if (!lists.listed.length) {
    return null;
  }
  for (const e of lists.listed) shown[which].add(`${entryKey(e)}|${e.state}`);
  busy = true;
  try {
    return which === "drift" ? await driftPrompt(lists, s) : await untrackedPrompt(lists, s);
  } finally {
    busy = false;
  }
}

async function cardsOf(entries, state, which) {
  const nameOf = await names().catch(() => undefined);
  return entries.map((e) => {
    const base = entryBase(e, state),
      lobby = e?.kind === "lobby";
    // a portrait's details show it upright, as the portrait switch popup does
    const big = e?.kind === "spr" ? () => thumb(base, "portrait") : () => thumb(base, "bust", null, lobby);
    return { ...card(e, { state, nameOf, which }), thumb: thumb(base, "card", null, lobby), big };
  });
}

// a prompt as a list popup in #prompt, every card that can be ticked ticked; resolves with {button, selection,
// checked} once one of its buttons (or, where it has one, its close) answered it
function listPrompt({ title, items, buttons, hint, check, closable, which, unticked = new Set() }) {
  const host = document.getElementById("prompt");
  let selection = new Set([...Popup.selectable(items)].filter((key) => !unticked.has(key)));
  let focus = null;
  return new Promise((resolve) => {
    const done = (button) => {
      host.hidden = true;
      host.innerHTML = "";
      Popup.closed(host);
      resolve({ button, selection, checked: !!check?.checked });
    };
    const draw = (scroll = 0) =>
      Popup.listPopup({
        host,
        title,
        items,
        selected: [...selection],
        multi: true,
        focus,
        keepScroll: scroll,
        hint,
        detailKind: (it) => it.entry?.kind,
        alignStart: true,
        slimBand: true,
        footer: footer(items, selection, { size: which === "untracked" }),
        check,
        buttons: buttons.map((b) => ({
          ...b,
          fn: () => (b.greyed ? notify(b.why || "", { kind: "error", error: b.whyError }) : done(b.action)),
        })),
        onClose: closable ? () => done("later") : null,
        onSelect: (key, s) => {
          focus = key;
          selection = Popup.toggled(selection, items, key);
          draw(s);
        },
        details: (it) => {
          const d = details(it.entry, { which });
          const actions = d.repair ? [[T("mods.details"), () => repairMessage([it], currentState())]] : [];
          return { title: d.title, fields: d.fields, actions };
        },
      }).catch((err) => {
        console.error("prompt", err);
        done(null);
      });
    draw();
  });
}
const currentState = () => ModSwitch.current() || {};

// the repair lines of damaged or stuck entries (Details): their names and what the user runs
function repairMessage(items, s) {
  const installs = (s.installs || []).filter((i) => items.some((it) => it.entry.install === i.key));
  const named = items.map((it) => ModSwitch.entryLabel(it.entry, () => it.full));
  const title = items.some((it) => it.entry.state === "damaged") ? T("mods.damaged") : T("mods.overwrittenTitle");
  return Popup.message({ title, lines: overwrittenLines({ names: named, installs }, T), ok: T("picker.ok") });
}

/**
 * Mark the cards of entries whose configured mod's rig a game update changed (`held`: their keys): they carry
 * the Rig Changed badge and start unticked, so Apply Mod Configuration writes them only when ticked.
 */
export function heldCards(items, held) {
  return items.map((it) =>
    held.has(it.key) && !it.disabled
      ? { ...it, badge: { text: T("mods.heldBadge"), color: COLOUR.warning, ink: "#ffffff" } }
      : it,
  );
}

// "Mods Back to Official": exactly two buttons, no Later and no close; Escape and the backdrop do
// nothing. Apply Mod Configuration sends the ticked cards (the others are kept as they are); Keep as Is keeps them all.
async function driftPrompt(lists, s) {
  const held = Impact.heldKeys(lists.listed, await Impact.impacts(lists.listed));
  const items = heldCards(
    [...(await cardsOf(lists.listed, s, "drift")), ...(await cardsOf(lists.apart, s, "drift"))],
    held,
  );
  const blocked = s.available === false ? ModSwitch.reasonText(s) : "";
  const hint = lists.apart.length ? T("mods.stillOverwritten", { n: lists.apart.length }) : "";
  const answer = await listPrompt({
    title: T("mods.driftTitle"),
    items,
    hint,
    closable: false,
    which: "drift",
    unticked: held,
    buttons: [
      { label: T("mods.driftKeep"), action: "keep" },
      {
        label: T("mods.driftApply"),
        action: "apply",
        primary: true,
        greyed: !!blocked && s.reason !== "classic_open",
        why: blocked,
        whyError: { code: s.reason, params: s.reasonParams },
      },
    ],
  });
  if (!answer.button) return null;
  const listed = items.filter((it) => !it.disabled).map((it) => it.entry);
  const choice = ModSwitch.driftChoice(answer.button, listed, [...answer.selection]);
  const plain = (e) => ({ install: e.install, core: e.core, state: e.state });
  if (choice.dismiss.length) {
    await rpc("mods.dismiss", { kind: "drift", entries: choice.dismiss.map(plain) }).catch((err) =>
      notify(ModSwitch.errorText(err.code, err.params), { kind: "error", error: err }),
    );
  }
  if (choice.restore.length) await applyDrift(choice.restore.map(plain));
  return answer.button;
}

/**
 * Apply Mod Configuration: the job, its progress, and its result; refused while the classic UI is open, the
 * classic modal (which can keep it for when the window closes). The update summary's Apply Mod Configuration
 * too.
 * @param {object[]} entries The entries to restore.
 */
export async function applyDrift(entries) {
  const params = { entries };
  let id;
  try {
    id = await rpc("mods.restoreDrift", params);
  } catch (err) {
    ModSwitch.refused(err, "mods.restoreDrift", params);
    return;
  }
  let refusal = null;
  const running = T("progress.apply", { name: T("progress.mods") });
  try {
    const result = await Loading.task((update) => waitJob(id, update), { label: running });
    const summary = driftSummary(result, await names().catch(() => undefined));
    if (!summary) return;
    await Popup.message({
      title: summary.title,
      lines: summary.lines,
      ok: T("picker.ok"),
      error: { code: result.failed?.[0]?.code || "internal", params: { failed: result.failed, left: result.left } },
    });
  } catch (err) {
    refusal = err;
  }
  // an untested game build's confirmation applies the same entries again, once the loading screen is gone
  if (refusal) await ModSwitch.refused(refusal, "mods.restoreDrift", params, () => applyDrift(entries));
}

// "Mods Not in the Library": every card ticked; Add to Library, with mods off also Add and Turn On Mods, and Later;
// Don't Ask Again dismisses the cards not added. The damaged entries are listed apart and cannot be ticked.
async function untrackedPrompt(lists, s) {
  const items = [...(await cardsOf(lists.listed, s, "untracked")), ...(await cardsOf(lists.apart, s, "untracked"))];
  const check = { label: T("mods.dontAsk"), checked: false, onChange: () => {} };
  const labels = { add: T("mods.untrackedAdd"), addTurnOn: T("mods.addTurnOn"), later: T("update.later") };
  const answer = await listPrompt({
    title: T("mods.untrackedTitle"),
    items,
    hint: T("mods.untrackedHint"),
    check,
    closable: true,
    which: "untracked",
    buttons: ModSwitch.untrackedButtons(s)
      .reverse()
      .map((id) => ({ label: labels[id], action: id, primary: id === "add" })),
  });
  if (!answer.button) return null;
  const outcome = untrackedOutcome(answer.button, items, answer.selection, answer.checked);
  if (outcome.dismiss.length) {
    await rpc("mods.dismiss", { kind: "untracked", entries: outcome.dismiss }).catch((err) =>
      notify(ModSwitch.errorText(err.code, err.params), { kind: "error", error: err }),
    );
  }
  if (outcome.adopt.length) {
    const added = await adopt(outcome.adopt);
    if (added && outcome.turnOn) await ModSwitch.turn({ enabled: true, expect: false });
  }
  return answer.button;
}

/**
 * Add to Library: the library.adopt job (allowed while the game runs), with a result popup for failures.
 * @param {object[]} entries The entries to add.
 */
export async function adopt(entries) {
  let id;
  try {
    id = await rpc("mods.adopt", { entries });
  } catch (err) {
    notify(ModSwitch.errorText(err.code, err.params), { kind: "error", error: err });
    return false;
  }
  const running = T("progress.add", { name: T("progress.mods") });
  try {
    const result = await Loading.task((update) => waitJob(id, update), { label: running });
    const failed = [
      ...(result?.failed || []),
      ...(result?.skipped || []).map((s) => ({ entry: s, code: "entry_changed" })),
    ];
    if (!failed.length) return true;
    const nameOf = await names().catch(() => undefined);
    await Popup.message({
      title: T("mods.untrackedAdd"),
      error: { code: failed[0].code, params: { failed } },
      lines: [
        T("mods.result", { done: (result?.added || []).length, failed: failed.length }),
        ...failed
          .slice(0, 6)
          .map((f) => `${ModSwitch.entryLabel(f.entry, nameOf)} - ${ModSwitch.errorText(f.code, f.params)}`),
      ],
      ok: T("picker.ok"),
    });
    return (result?.added || []).length > 0;
  } catch (err) {
    notify(ModSwitch.errorText(err.code, err.params), { kind: "error", error: err });
    return false;
  }
}

/**
 * The message for an overwritten entry of the current install (the switch popup's greyed cards, an
 * original_unavailable refusal): Add First, whose Add to Library adopts this entry, or the Overwritten message
 * with the repair lines of its install.
 * @returns {Promise<boolean>} Resolves once it closes: true when the entry was added.
 */
export async function overwritten({ name, core, files }) {
  const s = currentState();
  const install = s.current;
  const installs = (s.installs || []).filter((i) => i.key === install);
  if (overwrittenKind(files) === "overwritten") {
    await Popup.message({
      title: T("mods.overwrittenTitle"),
      lines: overwrittenLines({ names: name ? [name] : [], installs }, T),
      ok: T("picker.ok"),
    });
    return false;
  }
  const r = await Popup.message({
    title: T("mods.overwrittenTitle"),
    lines: addFirstLines({ names: name ? [name] : [] }, T),
    ok: T("mods.untrackedAdd"),
    cancel: T("popup.cancel"),
  });
  if (r.button !== "ok" || !install || !core) return false;
  return adopt([{ install, core: String(core).toLowerCase() }]);
}
