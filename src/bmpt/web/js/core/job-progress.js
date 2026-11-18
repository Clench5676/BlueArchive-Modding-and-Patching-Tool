// Shared student identity and job wording for batch and detail actions.
import { L, T } from "./i18n.js";
import { customAliases, studentName } from "./names.js";

/**
 * The name a job's progress gives a student: the student's name (the user's alias, else the student's base
 * name), then in the language's brackets the costume's short name (omitted for the student's own alt or a
 * alt with aliases) and the kind of file.
 * @param {string} kind The kind of file, as the job names it.
 * @param {string} [fallback] The name when the data has none.
 * @param {object} [student] The alt's student.
 */
export function progressName(alt, kind, fallback = "", student = alt?.student) {
  const names = L(alt?.names),
    catalogBase = L(student?.studentNames || student?.names);
  const base = studentName(alt, catalogBase || names?.full || fallback);
  const altName = !customAliases(alt).length && catalogBase && alt?.key !== student?.key ? names?.short : "";
  const parts = [altName, kind].filter(Boolean).join(T("progress.comma"));
  return parts ? T("progress.subject", { name: base, parts }) : base;
}

/**
 * The share done of the item at hand, after the job's line: whole percents, below 100 while the item runs;
 * nothing for a job that reports no fraction. A job reports it as `fraction` (0..1) beside its `current` /
 * `total`.
 */
export function percentText(fraction) {
  if (typeof fraction !== "number" || !Number.isFinite(fraction) || fraction <= 0) return "";
  return T("progress.percent", { percent: Math.min(99, Math.floor(fraction * 100)) });
}
/** Add the percentage to a job's line. */
export const withPercent = (text, fraction) => {
  const percent = percentText(fraction);
  return percent ? `${text} ${percent}` : text;
};

/**
 * Make the filter of a job's fraction as shown: it only grows within one item (its current / total) and starts
 * again with the next. A snapshot without a fraction keeps the item's last one; null until the item reports
 * any.
 * @returns {(progress?: object) => (number|null)} Gives the fraction to show for a snapshot.
 */
export function steadyFraction() {
  let item = null,
    best = null;
  return (p = {}) => {
    const key = `${p?.current ?? ""}/${p?.total ?? ""}`;
    if (key !== item) {
      item = key;
      best = null;
    }
    const f = p?.fraction;
    if (typeof f === "number" && Number.isFinite(f) && (best === null || f > best)) best = Math.min(1, Math.max(0, f));
    return best;
  };
}

/**
 * The text of a job's progress line: the verb, the name, the count (current of total), the step (left out where
 * it repeats the verb) and the percentage.
 * @param {object} progress The job's progress.
 * @param {string} progress.action The verb's string key.
 * @param {string} progress.name Who it is for.
 * @param {number} [progress.current] The item at hand.
 * @param {number} [progress.total] The number of items.
 * @param {string} [progress.step] The step's string key.
 * @param {number|null} [progress.fraction] The share done of the item, 0..1.
 */
export function jobProgress({ action, name, current = 1, total = 1, step = "", fraction = null }) {
  const phase = T(action);
  const detail = step && T(step);
  // These phases and steps express the same work even where their grammatical forms differ.
  const repeated = {
    "import.phase.add": "import.step.add",
    "import.phase.apply": "import.step.apply",
    "progress.actionApply": "import.step.apply",
    "progress.actionExtract": "import.step.extract",
  };
  const suffix = detail && detail !== phase && repeated[action] !== step ? T("progress.step", { step: detail }) : "";
  return withPercent(
    T("progress.job", {
      action: phase
        .replace(/\{name\}/g, "")
        .replace(/[:：]\s*$/u, "")
        .trim(),
      name,
      current,
      total,
    }) + suffix,
    fraction,
  );
}

// --- a job over mods: one line per item at hand (library/mod_progress.py) ---
// "Processing Mods (3/13): Hoshino (Swimsuit) - Chibi - My Swim Edit - Saving Bundle - 42%": the job's verb and the
// item at hand of all, who it is, its kind, the mod's own name (or the alt whose costume it takes), how far it is
// and its step (0% before it reports one). A one-item job leaves the count out.
const KIND_KEYS = {
  portrait: "tab.portrait",
  memorialLobby: "tab.memorialLobby",
  chibi: "tab.chibi",
  other: "import.other",
  thumbnail: "thumbnail.modName",
};
const MOD_ACTIONS = {
  process: "progress.modsProcess",
  apply: "progress.modsApply",
  restore: "progress.modsRestore",
  check: "progress.modsCheck",
  add: "progress.modsAdd",
  export: "progress.modsExport",
};
export const SHRINK_NAME = 1,
  SHRINK_WHO = 2;
const plainName = (entry) => entry?.alt || entry?.core || "";

/**
 * A job's progress as a line over mods.
 * @param {object} [p] A job's progress: {current, total, action, alt, kind, core, name, source, step,
 *     fraction}.
 * @param {object} [options]
 * @param {(entry: {alt?: string, core?: string}) => string} [options.nameOf] Names an alt as the student list
 *     does.
 * @param {string} [options.action] A string key that overrides the job's verb.
 * @returns {{text: string, parts: Array<[string, number]>}|null} parts are [text, shrink], shrink 0 for text
 *     that always stays; null for progress of no mod item.
 */
export function modLine(p = {}, { nameOf = plainName, action = "" } = {}) {
  if (!p || (!p.alt && !p.core)) return null;
  const current = Number(p.current) || 0,
    total = Number(p.total) || 0;
  const verb = T(action || MOD_ACTIONS[p.action] || MOD_ACTIONS.process);
  const head =
    total > 1 ? T("progress.modCount", { action: verb, current, total }) : T("progress.modHead", { action: verb });
  const sep = T("progress.sep");
  const who = (p.resourceType === "other" ? p.core : nameOf({ alt: p.alt, core: p.core })) || p.core || p.alt || "";
  const parts = [
    [head, 0],
    [who, SHRINK_WHO],
    [sep + T(KIND_KEYS[p.resourceType] || KIND_KEYS.other), 0],
  ];
  const own =
    p.name && p.name !== who && p.name !== p.core
      ? p.name
      : p.source
        ? T("progress.from", { name: nameOf({ alt: p.source }) || p.source })
        : "";
  if (own) parts.push([sep, 0], [own, SHRINK_NAME]);
  const step = p.step ? T(p.step) : "";
  if (step && step !== p.step) parts.push([sep + step, 0]);
  // the item's share after its step, 0% until the item reports one
  parts.push([sep + (percentText(p.fraction) || T("progress.percent", { percent: 0 })), 0]);
  return { text: parts.map(([text]) => text).join(""), parts };
}

/**
 * Make the progress line of a one-mod job (a student page's, Settings' or a repair's action): who it is stays
 * as given, the step and share come from the job's progress as they arrive; a report of files written (no step,
 * their own count) keeps the step and share shown.
 * @param {{alt: string, kind: string, core?: string, name?: string, source?: string}} identity Who the job
 *     is for.
 * @param {object} [options]
 * @param {(entry: object) => string} [options.nameOf] Names an alt.
 * @param {string} [options.action] The verb's string key.
 * @returns {(progress?: object) => object|null} Gives the line for each progress snapshot.
 */
export function modJob(identity, { nameOf = plainName, action = "progress.actionProcess" } = {}) {
  const steady = steadyFraction();
  let step = "";
  return (p = {}) => {
    if (typeof p?.step === "string") step = p.step;
    return modLine(
      {
        ...identity,
        alt: identity?.alt || identity?.resourceType || "",
        current: 1,
        total: 1,
        step,
        fraction: steady({ ...p, current: 1, total: 1 }),
      },
      { nameOf, action },
    );
  };
}
/** Whether a progress subject is a mod's identity object, not a list. */
export const isIdentity = (subject) => !!subject && typeof subject === "object" && !Array.isArray(subject);

/**
 * Fit the line in at most `max` wide: the mod's own name shortened first, then who it is, each to the longest
 * start that fits followed by an ellipsis; the verb, count, kind, percentage and step always stay. A line that
 * still does not fit is returned with both shortened to the ellipsis alone.
 * @param {Array<[string, number]>} parts The line's parts, from modLine.
 * @param {(text: string) => number} width Measures a text.
 * @param {number} max The widest the line may be.
 */
export function fitLine(parts, width, max, ellipsis = "…") {
  const texts = parts.map(([text]) => text);
  const joined = () => texts.join("");
  if (width(joined()) <= max) return joined();
  for (const level of [SHRINK_NAME, SHRINK_WHO]) {
    const at = parts.findIndex(([, shrink]) => shrink === level);
    if (at < 0) continue;
    const chars = Array.from(parts[at][0]);
    let lo = 0,
      hi = chars.length - 1; // the longest start, lo characters, that fits
    texts[at] = ellipsis;
    if (width(joined()) > max) continue;
    while (lo < hi) {
      const mid = Math.ceil((lo + hi) / 2);
      texts[at] = chars.slice(0, mid).join("").trimEnd() + ellipsis;
      if (width(joined()) <= max) lo = mid;
      else hi = mid - 1;
    }
    texts[at] = chars.slice(0, lo).join("").trimEnd() + ellipsis;
    return joined();
  }
  return joined();
}
