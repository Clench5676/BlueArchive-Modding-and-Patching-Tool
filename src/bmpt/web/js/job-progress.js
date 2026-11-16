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
