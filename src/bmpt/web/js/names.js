// The first saved variant alias is the display name; all saved aliases remain searchable and editable.
export const customAliases = (variant) => (Array.isArray(variant?.custom) ? variant.custom.filter((a) => typeof a === "string" && a) : []);
export const studentName = (variant, original) => customAliases(variant)[0] || original;

// The backend's label is presentation text in the language of its last read.
// Rebuild repair suffixes from stable names so a locale change needs no reread.
export function modName(mod, entries, translate, seen = new Set()) {
  if (!mod) return "";
  if (mod.alias) return mod.alias;
  if (mod.optimized_from) return translate("model.optimizedName", { name: mod.label_base || mod.label });
  // a memorial lobby camera (BL-137): its source's name, then its own, "<source> - Camera <n>"
  if (mod.camera) {
    const own = translate("lobby.cameraName", { n: mod.camera.number });
    if (!mod.camera.source?.startsWith("mod:")) return `${translate("lobby.original")} - ${own}`;
    const from = entries.find((entry) => entry.id === mod.camera.source.slice(4));
    return from && !seen.has(from.id) ? `${modName(from, entries, translate, new Set([...seen, mod.id]))} - ${own}` : own;
  }
  if (mod.repaired_from && !seen.has(mod.repaired_from)) {
    const parent = entries.find((entry) => entry.id === mod.repaired_from);
    const name = parent ? modName(parent, entries, translate, new Set([...seen, mod.id])) : mod.label_base;
    if (name) return translate("remap.name", { name });
  }
  return mod.label_base || mod.label || mod.file?.split(/[\\/]/).pop() || mod.id;
}

// A mod's label may already be its alias. Its core and file name still identify the original game resource.
export const modOriginalName = (mod) => mod?.core || mod?.file?.split(/[\\/]/).pop() || (mod?.label !== mod?.alias ? mod?.label : "") || "";
