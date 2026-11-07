// The details a refused Import Mod row opens from its status badge: the row's reason, then what the check read of each
// file (build target, Unity version, from the entry's `bundles`) and, for missing files, the dependencies nothing held
// (`missing.cabs`) and the files of the same slot in its folders (`missing.found`).

// An error text with the file it names left out (the details list each file), when the name opens or ends the text:
// "{file}, build target ..." or "Not a valid bundle: {file}". A name inside a sentence stays.
export function withoutFile(T, key, params = {}) {
  const marker = "\u0000", text = T(key, { ...params, file: marker });
  if (!text.includes(marker)) return text;
  const separators = "[\\s,，、:：-]*";
  const trimmed = text.replace(new RegExp(`^${separators}${marker}${separators}|${separators}${marker}${separators}$`), "");
  return trimmed.includes(marker) ? T(key, params) : trimmed;
}

export const hasDetails = (entry) => entry?.state === "blocked" || entry?.state === "incomplete";

// One paragraph for the reason, one for each file (its name, build target, Unity version) and one each for the unresolved
// dependencies and the files found: lines of a paragraph sit at the text's line pitch, paragraphs at the popup's gap.
export function detailLines(entry, { reason = "", T, separator = ", " }) {
  const lines = reason ? [reason] : [];
  for (const bundle of entry.bundles || []) {
    lines.push([bundle.file,
      ...bundle.platforms?.length ? [T("import.detail.platform", { platforms: bundle.platforms.join(separator) })] : [],
      ...bundle.unity?.length ? [T("import.detail.unity", { versions: bundle.unity.join(separator) })] : []].join("\n"));
  }
  const missing = entry.missing || {};
  if (missing.unresolved) lines.push([T("import.detail.unresolved", { n: missing.unresolved }), ...missing.cabs || []].join("\n"));
  if (missing.found?.length) {
    lines.push([T("import.missing.promptHead"), ...missing.found.map((file) => T("import.missing.promptFile", { name: file.name }))].join("\n"));
  }
  return lines;
}
