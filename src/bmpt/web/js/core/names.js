/**
 * The saved aliases of an alt: the first is the display name; all remain searchable and editable.
 */
export const customAliases = (alt) =>
  Array.isArray(alt?.custom) ? alt.custom.filter((a) => typeof a === "string" && a) : [];
/**
 * The display name of an alt: its first alias, else the original name.
 */
export const studentName = (alt, original) => customAliases(alt)[0] || original;
/**
 * Another student's costume: "Student (Costume)" in the language's brackets, the rule job progress names an
 * alt by (progress.subject). "Student" is the student's base name, or the user's alias of the alt; the
 * costume is always named, the original one too.
 */
export const crossName = (alt, student, costume, translate) =>
  translate("progress.subject", { name: studentName(alt, student), parts: costume });

/**
 * A mod's name. The backend's label is presentation text in the language of its last read, so the repair
 * suffixes are rebuilt from stable names and a locale change needs no reread.
 */
export function modName(mod, entries, translate, seen = new Set()) {
  if (!mod) return "";
  if (mod.alias) return mod.alias;
  if (mod.optimized_from) return translate("model.optimizedName", { name: mod.label_base || mod.label });
  // a memorial lobby camera: its source's name, then its own, "<source> - Camera <n>"
  if (mod.camera) {
    const own = translate("lobby.cameraName", { n: mod.camera.number });
    if (!mod.camera.source?.startsWith("mod:")) return `${translate("lobby.original")} - ${own}`;
    const from = entries.find((entry) => entry.id === mod.camera.source.slice(4));
    return from && !seen.has(from.id)
      ? `${modName(from, entries, translate, new Set([...seen, mod.id]))} - ${own}`
      : own;
  }
  if (mod.repaired_from && !seen.has(mod.repaired_from)) {
    const parent = entries.find((entry) => entry.id === mod.repaired_from);
    const name = parent ? modName(parent, entries, translate, new Set([...seen, mod.id])) : mod.label_base;
    if (name) return translate("remap.name", { name });
  }
  return mod.label_base || mod.label || mod.file?.split(/[\\/]/).pop() || mod.id;
}

/**
 * The name of the game resource a mod replaces: a mod's label may already be its alias, but its core and file
 * name still identify the original.
 */
export const modOriginalName = (mod) =>
  mod?.core || mod?.file?.split(/[\\/]/).pop() || (mod?.label !== mod?.alias ? mod?.label : "") || "";
