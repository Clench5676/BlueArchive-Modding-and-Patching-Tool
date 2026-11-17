// A mod stays new until its configured preview or its switch-popup card has been shown.
import { candidates, previewKey } from "./switch-sources.js";

/**
 * Whether a variant has a mod that is still new (`fresh`), of a kind or of any kind.
 */
export const hasUnseen = (variant, kind = null) =>
  (variant?.modList || []).some((mod) => mod.fresh && (!kind || mod.kind === kind));

const assetId = (mod) => mod.repaired_from || mod.id;
function shownAsset(family, variant, kind, enabled) {
  const key = previewKey(variant, kind, enabled);
  return family.variants
    .flatMap((entry) => entry.modList || [])
    .find((mod) => mod.kind === kind && `m:${mod.id}` === key);
}

/**
 * The new mods that share the asset of the mod whose preview is shown now (a repaired copy counts as its
 * original); none when the preview is not a mod.
 */
export function shownUnseen(family, variant, kind, enabled = true) {
  const shown = shownAsset(family, variant, kind, enabled);
  return shown
    ? family.variants
        .flatMap((entry) => entry.modList || [])
        .filter((mod) => mod.fresh && assetId(mod) === assetId(shown))
    : [];
}

/**
 * Whether the switch popup lists a new mod whose asset is not the one shown now.
 */
export function switchUnseen(family, variant, kind, enabled = true) {
  const shown = shownAsset(family, variant, kind, enabled);
  return candidates(family, variant, kind).some(
    (source) => source.kind === "mod" && source.mod.fresh && assetId(source.mod) !== (shown && assetId(shown)),
  );
}

/**
 * Mark mods as seen: clear their new flag and the variants'.
 * @param {object} family The family.
 * @param {string[]} ids The mod ids.
 */
export function clearUnseen(family, ids) {
  const seen = new Set(ids);
  for (const variant of family?.variants || []) {
    for (const mod of variant.modList || []) if (seen.has(mod.id)) mod.fresh = false;
    variant.fresh = hasUnseen(variant);
  }
}
