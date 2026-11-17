/**
 * Group the import entries into rows: student alts share a row only when requested (merged); generic and
 * unidentified assets keep their own rows.
 * @param {boolean} merged Group the alts of one student.
 * @param {(entry: object) => object} identify Tells who an entry is for.
 */
export function importGroups(entries, merged, identify) {
  const groups = new Map();
  for (const entry of entries) {
    const alt = identify(entry).alt;
    const key = merged && alt && entry.kind !== "other" ? `alt:${alt.key}` : `entry:${entry.id}`;
    if (!groups.has(key)) groups.set(key, { key, entries: [], counts: { spr: 0, lobby: 0, model: 0, other: 0 } });
    const group = groups.get(key);
    group.entries.push(entry);
    group.counts[entry.kind in group.counts ? entry.kind : "other"]++;
  }
  return [...groups.values()];
}
