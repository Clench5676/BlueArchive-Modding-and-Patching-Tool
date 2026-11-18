// A mod stays new until its configured preview or its switch-popup card has been shown.
import { candidates, previewKey } from "../mods/switch-sources.js";

/** Whether an alt has a mod that is still new (`fresh`), of a kind or of any kind. */
export const hasUnseen = (alt, kind = null) =>
  (alt?.modList || []).some((mod) => mod.fresh && (!kind || mod.resourceType === kind));

const assetId = (mod) => mod.repairSourceId || mod.id;
function shownAsset(student, alt, kind, enabled) {
  const key = previewKey(alt, kind, enabled);
  return student.alts
    .flatMap((entry) => entry.modList || [])
    .find((mod) => mod.resourceType === kind && `m:${mod.id}` === key);
}

/**
 * The new mods that share the asset of the mod whose preview is shown now (a repaired copy counts as its
 * original); none when the preview is not a mod.
 */
export function shownUnseen(student, alt, kind, enabled = true) {
  const shown = shownAsset(student, alt, kind, enabled);
  return shown
    ? student.alts.flatMap((entry) => entry.modList || []).filter((mod) => mod.fresh && assetId(mod) === assetId(shown))
    : [];
}

/** Whether the switch popup lists a new mod whose asset is not the one shown now. */
export function switchUnseen(student, alt, kind, enabled = true) {
  const shown = shownAsset(student, alt, kind, enabled);
  return candidates(student, alt, kind).some(
    (source) => source.kind === "mod" && source.mod.fresh && assetId(source.mod) !== (shown && assetId(shown)),
  );
}

/**
 * Mark mods as seen: clear their new flag and the alts'.
 * @param {string[]} ids The mod ids.
 */
export function clearUnseen(student, ids) {
  const seen = new Set(ids);
  for (const alt of student?.alts || []) {
    for (const mod of alt.modList || []) if (seen.has(mod.id)) mod.fresh = false;
    alt.fresh = hasUnseen(alt);
  }
}
