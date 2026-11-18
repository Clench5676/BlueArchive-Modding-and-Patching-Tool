// A size in bytes as the pages show it; shared by the mod screens and the data upgrade notice, which loads before
// the rest of the page.

/**
 * A size as the help shows it.
 * @param {number} bytes The size in bytes.
 * @returns {string} "0 MB", whole KB under a megabyte, one decimal in MB, two in GB.
 */
export function sizeText(bytes) {
  const b = Math.max(0, Number(bytes) || 0);
  if (!b) return "0 MB";
  if (b < 1048576) return `${Math.max(1, Math.round(b / 1024))} KB`;
  if (b < 1073741824) return `${(b / 1048576).toFixed(1)} MB`;
  return `${(b / 1073741824).toFixed(2)} GB`;
}
