// Shared student identity and job wording for batch and detail actions.
import { L, T } from "./i18n.js";
import { customAliases, studentName } from "./names.js";

export function progressName(variant, kind, fallback = "", family = variant?.family) {
  const names = L(variant?.names), catalogBase = L(family?.base || family?.names);
  const base = studentName(variant, catalogBase || names?.full || fallback);
  const variantName = !customAliases(variant).length && catalogBase && variant?.key !== family?.key ? names?.short : "";
  const parts = [variantName, kind].filter(Boolean).join(T("progress.comma"));
  return parts ? T("progress.subject", { name: base, parts }) : base;
}

// The share done of the item at hand, after the job's line: whole percents, below 100 while the item runs; nothing
// for a job that reports no fraction. A job reports it as `fraction` (0..1) beside its `current` / `total`.
export function percentText(fraction) {
  if (typeof fraction !== "number" || !Number.isFinite(fraction) || fraction <= 0) return "";
  return T("progress.percent", { percent: Math.min(99, Math.floor(fraction * 100)) });
}
export const withPercent = (text, fraction) => {
  const percent = percentText(fraction);
  return percent ? `${text} ${percent}` : text;
};

// A job's fraction as shown: it only grows within one item (its current / total) and starts again with the next. A
// snapshot without a fraction keeps the item's last one; null until the item reports any.
export function steadyFraction() {
  let item = null, best = null;
  return (p = {}) => {
    const key = `${p?.current ?? ""}/${p?.total ?? ""}`;
    if (key !== item) { item = key; best = null; }
    const f = p?.fraction;
    if (typeof f === "number" && Number.isFinite(f) && (best === null || f > best)) best = Math.min(1, Math.max(0, f));
    return best;
  };
}

export function jobProgress({ action, name, current = 1, total = 1, step = "", fraction = null }) {
  const phase = T(action);
  const detail = step && T(step);
  // These phases and steps express the same work even where their grammatical forms differ.
  const repeated = { "import.phase.add": "import.step.add", "import.phase.apply": "import.step.apply",
    "progress.actionApply": "import.step.apply", "progress.actionExtract": "import.step.extract" };
  const suffix = detail && detail !== phase && repeated[action] !== step ? T("progress.step", { step: detail }) : "";
  return withPercent(T("progress.job", { action: phase.replace(/\{name\}/g, "").replace(/[:：]\s*$/u, "").trim(), name, current, total }) + suffix, fraction);
}

// --- a job over mods: one line per item at hand (library/mod_progress.py) ---
// "Processing Mods (3/13): Hoshino (Swimsuit) - Chibi - My Swim Edit - 42% - Saving Bundle": the job's verb and the
// item at hand of all, who it is, its kind, the mod's own name (or the variant whose costume it takes), how far it is
// and its step. A one-item job leaves the count out.
const KIND_KEYS = { spr: "tab.portrait", lobby: "tab.lobby", model: "tab.model", other: "import.other",
  photo: "photo.modName" };
const MOD_ACTIONS = { process: "progress.modsProcess", apply: "progress.modsApply", restore: "progress.modsRestore",
  check: "progress.modsCheck", add: "progress.modsAdd", export: "progress.modsExport" };
export const SHRINK_NAME = 1, SHRINK_WHO = 2;
const plainName = (entry) => entry?.variant || entry?.core || "";

// p: a job's progress ({current, total, action, variant, kind, core, name, source, step, fraction}); nameOf names a
// variant ({variant, core}) as the roster does; action: a string key that overrides the job's verb. Returns
// {text, parts} (parts: [text, shrink], shrink 0 for text that always stays), or null for progress of no mod item.
export function modLine(p = {}, { nameOf = plainName, action = "" } = {}) {
  if (!p || (!p.variant && !p.core)) return null;
  const current = Number(p.current) || 0, total = Number(p.total) || 0;
  const verb = T(action || MOD_ACTIONS[p.action] || MOD_ACTIONS.process);
  const head = total > 1 ? T("progress.modCount", { action: verb, current, total }) : T("progress.modHead", { action: verb });
  const sep = T("progress.sep");
  const who = (p.kind === "other" ? p.core : nameOf({ variant: p.variant, core: p.core })) || p.core || p.variant || "";
  const parts = [[head, 0], [who, SHRINK_WHO], [sep + T(KIND_KEYS[p.kind] || KIND_KEYS.other), 0]];
  const own = p.name && p.name !== who && p.name !== p.core ? p.name
    : p.source ? T("progress.from", { name: nameOf({ variant: p.source }) || p.source }) : "";
  if (own) parts.push([sep, 0], [own, SHRINK_NAME]);
  const percent = percentText(p.fraction);
  if (percent) parts.push([sep + percent, 0]);
  const step = p.step ? T(p.step) : "";
  if (step && step !== p.step) parts.push([sep + step, 0]);
  return { text: parts.map(([text]) => text).join(""), parts };
}

// A one-mod job's line (a student page's, Settings' or a repair's action): who it is stays as given (identity:
// {variant, kind, core, name, source}), the step and share come from the job's progress as they arrive; a report of
// files written (no step, their own count) keeps the step and share shown. action: the verb's string key.
export function modJob(identity, { nameOf = plainName, action = "progress.actionProcess" } = {}) {
  const steady = steadyFraction();
  let step = "";
  return (p = {}) => {
    if (typeof p?.step === "string") step = p.step;
    return modLine({ ...identity, variant: identity?.variant || identity?.kind || "", current: 1, total: 1, step,
      fraction: steady({ ...p, current: 1, total: 1 }) }, { nameOf, action });
  };
}
export const isIdentity = (subject) => !!subject && typeof subject === "object" && !Array.isArray(subject);

// The line in at most max wide (width(text) measures it): the mod's own name shortened first, then who it is, each
// to the longest start that fits followed by an ellipsis; the verb, count, kind, percentage and step always stay. A
// line that still does not fit is returned with both shortened to the ellipsis alone.
export function fitLine(parts, width, max, ellipsis = "…") {
  const texts = parts.map(([text]) => text);
  const joined = () => texts.join("");
  if (width(joined()) <= max) return joined();
  for (const level of [SHRINK_NAME, SHRINK_WHO]) {
    const at = parts.findIndex(([, shrink]) => shrink === level);
    if (at < 0) continue;
    const chars = Array.from(parts[at][0]);
    let lo = 0, hi = chars.length - 1;  // the longest start, lo characters, that fits
    texts[at] = ellipsis;
    if (width(joined()) > max) continue;
    while (lo < hi) {
      const mid = Math.ceil((lo + hi) / 2);
      texts[at] = chars.slice(0, mid).join("").trimEnd() + ellipsis;
      if (width(joined()) <= max) lo = mid; else hi = mid - 1;
    }
    texts[at] = chars.slice(0, lo).join("").trimEnd() + ellipsis;
    return joined();
  }
  return joined();
}
