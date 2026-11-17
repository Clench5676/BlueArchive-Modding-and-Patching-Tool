/**
 * Group the import entries into rows: student variants share a row only when requested (merged); generic and
 * unidentified assets keep their own rows.
 * @param {object[]} entries The entries.
 * @param {boolean} merged Group the variants of one student.
 * @param {(entry: object) => object} identify Tells who an entry is for.
 */
export function importGroups(entries, merged, identify) {
  const groups = new Map();
  for (const entry of entries) {
    const variant = identify(entry).variant;
    const key = merged && variant && entry.kind !== "other" ? `variant:${variant.key}` : `entry:${entry.id}`;
    if (!groups.has(key)) groups.set(key, { key, entries: [], counts: { spr: 0, lobby: 0, model: 0, other: 0 } });
    const group = groups.get(key);
    group.entries.push(entry);
    group.counts[entry.kind in group.counts ? entry.kind : "other"]++;
  }
  return [...groups.values()];
}
