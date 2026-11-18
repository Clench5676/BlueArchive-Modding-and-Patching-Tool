// What the detail screen's switch cards offer and what the game shows now, for the portrait ("portrait") and the
// memorial lobby ("memorialLobby"), and the chibi: pure functions of the student that student.get returns, tested in
// Node (tests/js/switch_sources.test.mjs). Switching saves the chosen source and installs it while enabled; the
// library keeps what it replaces (docs/webui/library.md, library).
import { spineBase } from "../core/api.js";

/**
 * The kind of source an asset tab switches; every asset tab uses the same source selection and apply flow.
 * @returns {"portrait"|"memorialLobby"|"chibi"|null} The resource type; null for another tab.
 */
export const switchKindOf = (tab) =>
  ({ portrait: "portrait", memorialLobby: "memorialLobby", chibi: "chibi" })[tab] || null;
/** The detail tab that shows a resource type: "portrait", "memorialLobby" or "chibi"; null for another type. */
export const tabOfKind = (kind) =>
  ({ portrait: "portrait", memorialLobby: "memorialLobby", chibi: "chibi" })[kind] || null;

/** The core of an alt's skeleton of a kind: its portrait or its memorial lobby (or its chibi's). */
export const coreOf = (v, kind = "portrait") =>
  (kind === "chibi" ? v.chibi : kind === "memorialLobby" ? v.memorialLobby : v.portrait) || null;
/**
 * Whether an alt has a kind to show: an alt with only a field-mode chibi (fieldChibi: an event or story
 * costume) has a 3D tab, but no slot to switch.
 */
export const hasKind = (v, kind) =>
  ["portrait", "memorialLobby", "chibi"].includes(kind) &&
  (!!coreOf(v, kind) || (kind === "chibi" && !!v?.fieldChibi) || !!v?.storedResourceTypes?.includes(kind));
/** Whether an alt has any kind to show. */
export const hasAsset = (v) => hasKind(v, "portrait") || hasKind(v, "memorialLobby") || hasKind(v, "chibi");
/** The first kind an alt has: the portrait, else the memorial lobby, else the chibi. */
export const primaryKind = (v) =>
  hasKind(v, "portrait")
    ? "portrait"
    : hasKind(v, "memorialLobby")
      ? "memorialLobby"
      : hasKind(v, "chibi")
        ? "chibi"
        : "memorialLobby";
/** The tab to show for an alt: the wanted one when the alt has its kind, else the first it has. */
export function availableTab(v, tab) {
  if (hasKind(v, switchKindOf(tab))) return tab;
  return hasKind(v, "portrait")
    ? "portrait"
    : hasKind(v, "memorialLobby")
      ? "memorialLobby"
      : hasKind(v, "chibi")
        ? "chibi"
        : "memorialLobby";
}

/**
 * The game file parts of one kind ("portrait" portrait, "memorialLobby" memorial lobby, "chibi" the 3D chibi's whole
 * slot), newest first.
 */
export const partsOf = (v, kind = "portrait") => {
  const parts = (v?.state?.files || []).filter((f) => f.resourceType === kind);
  return kind === "chibi" && !parts.length ? v?.chibiFiles || [] : parts;
};

/**
 * Whether a game file part holds official bytes: the server's verdict (student.get's official), else, from a
 * server without it, its applied source.
 */
export const isOfficial = (f) => (typeof f.official === "boolean" ? f.official : f.applied === "official");

/**
 * What the alt's game files of a kind carry now, from their non-official parts only (the library records
 * every part of a switched kind, the parts a mod left official included): the one source among them (a mod
 * often replaces one part), "official", or "unknown" when they carry several
 * (tests/data/source_rule_cases.json, shared with the library's own rule).
 */
export function appliedOf(v, kind = "portrait") {
  const sources = new Set(
    partsOf(v, kind)
      .filter((f) => !isOfficial(f))
      .map((f) => f.applied),
  );
  return !sources.size ? "official" : sources.size === 1 ? [...sources][0] : "unknown";
}

/**
 * The candidate key of what the game shows: "self" (the alt's own official files), "m:<mod id>",
 * "v:<alt key>" (another alt's official files, adapted) or "unknown" (changed outside the library).
 */
export function appliedKey(v, kind = "portrait") {
  const a = appliedOf(v, kind);
  if (a === "official") return "self";
  if (a.startsWith("mod:")) return `m:${a.slice(4)}`;
  if (a.startsWith("alt:")) return `v:${a.slice(4)}`;
  return "unknown";
}

/** A candidate key as an actions.apply source. */
export const sourceOf = (key) =>
  key === "self" ? "official" : key.startsWith("m:") ? `mod:${key.slice(2)}` : `alt:${key.slice(2)}`;

/**
 * Whether an alt's files of a kind are overwritten: a part that is not official has no official copy
 * anywhere (student.get's restorable false). Its official choice is unavailable; library mods can still replace
 * it.
 */
export const overwrittenOf = (v, kind = "portrait") =>
  partsOf(v, kind).some((f) => !isOfficial(f) && f.restorable === false);

// The cautions of the chibi's costume pairs (chibi.costumeCautions) are kept beside the verdicts under this
// key: { other: ["ex_parts" | "carried_shader", ...] }. It is a symbol, so it never reads as an alt.
export const CAUTIONS = Symbol("cautions");
const CAUTION_TEXT = { ex_parts: "chibi.exParts", carried_shader: "chibi.carriedShader" };
const cautionsOf = (costumes, key) => {
  const found = costumes?.[CAUTIONS]?.[key];
  return Array.isArray(found) && found.some((c) => c in CAUTION_TEXT) ? { cautions: found } : {};
};
/** The locale key of a card's caution on hover: the first it has, in CAUTION_TEXT's order; null for none. */
export const cautionKey = (c) => {
  const found = Object.keys(CAUTION_TEXT).find((code) => c?.cautions?.includes(code));
  return found ? CAUTION_TEXT[found] : null;
};
/**
 * The sources an alt's portrait or memorial lobby can be switched to, in the switch popup's order: its own official
 * files, its own mods of that kind, then for the portrait the other alts' official files (those with a
 * portrait of their own) and the other alts' portrait mods. Other alts' memorial lobbies and their
 * memorial lobby mods are not offered: another student's memorial lobby under this alt's memorial lobby prefab is
 * untested in the game. Each is { key, kind ("self" | "mod" | "alt"), core, alt, id, mod, origin (the alt a mod was
 * made for), restorable (its files have an official copy), greyed (cannot be chosen) }. The chibi lists
 * every other chibi of the student as a costume at once; only a pair that was built and exported for these
 * files can be applied (`costumes`: chibi.costumes' verdicts, "ok" | "preview" | "refused" | null): "ok" and
 * "preview" pairs are offered, the latter with a caution (`beyondFit`); unchecked or refused pairs are for a
 * preview only (`previewOnly`), and a refused pair cannot be chosen as the preview either (`refused`). Another
 * student's chibi (`students`: students.list's) is offered through the popup's search, or all at once with "Show
 * other students" (crossCandidates), save the one already chosen, listed last (`cross`).
 * @param {object} shownStudent The student, as student.get gives it.
 * @param {"portrait"|"memorialLobby"|"chibi"} [kind] The kind to switch.
 * @param {object|null} [costumes] The chibi's verdicts by alt.
 * @param {object[]|null} [students] students.list's students, for other students' chibis.
 */
export function candidates(shownStudent, v, kind = "portrait", costumes = null, students = null) {
  const own = coreOf(v, kind);
  const cores = (kind === "chibi" && v.chibiCores?.length ? v.chibiCores : [own]).map((core) =>
    String(core).toLowerCase(),
  );
  const mods = shownStudent.alts
    .flatMap((o) => o.modList || [])
    .filter((m) => m.resourceType === kind)
    .filter((m) => kind === "portrait" || !m.core || !own || cores.includes(m.core));
  // cross-alt adaptation requires an actual local target
  const across = (kind === "portrait" || kind === "chibi") && !!own;
  const others = !across
    ? []
    : kind === "chibi"
      ? costumeCandidates(shownStudent, v)
      : shownStudent.alts.filter((o) => o.key !== v.key && !!coreOf(o, kind) && coreOf(o, kind) !== own);
  const chosen = kind === "chibi" ? new Set([configuredKey(v, kind), sourceKey(v?.previewSources?.chibi)]) : null;
  const chosenCross =
    kind === "chibi" && across ? crossChibis(students, v).filter((o) => chosen.has(`v:${o.key}`)) : [];
  const mod = (m) => ({
    key: `m:${m.id}`,
    kind: "mod",
    id: m.id,
    mod: m,
    core: null,
    origin: shownStudent.alts.find((o) => o.key === m.targetAlt) || null,
  });
  const list = [
    { key: "self", kind: "self", core: own, alt: v },
    ...mods.filter((m) => m.targetAlt === v.key).map(mod),
    // beyondFit: past the stretch limit; previewOnly: not checked yet or refused, shown but never applied
    ...others.map((o) => ({
      key: `v:${o.key}`,
      kind: "alt",
      core: coreOf(o, kind),
      alt: o,
      ...(kind === "chibi" && costumes?.[o.key] === "preview"
        ? { beyondFit: true }
        : kind === "chibi" && costumes?.[o.key] !== "ok"
          ? { previewOnly: true }
          : {}),
      ...(kind === "chibi" && costumes?.[o.key] === "refused" ? { refused: true } : {}),
      ...(kind === "chibi" ? cautionsOf(costumes, o.key) : {}),
    })),
    ...(across && kind === "portrait" ? mods.filter((m) => m.targetAlt !== v.key).map(mod) : []),
    ...chosenCross.map((o) => crossCard(o, costumes)),
  ];
  const stuck = overwrittenOf(v, kind);
  return list.map((c) => {
    const restorable = c.cross || c.kind === "mod" ? true : !overwrittenOf(c.alt, kind);
    const greyed = (c.kind === "self" && (!own || stuck)) || (c.kind === "alt" && !restorable);
    return { ...c, restorable, greyed };
  });
}

/**
 * The other alts whose chibi an alt's chibi may take as a costume, before their check: any other
 * chibi of the student, field-mode chibis included (chibi.costumes / chibi.checkCostumes judge each pair).
 */
export function costumeCandidates(shownStudent, v) {
  const own = coreOf(v, "chibi");
  if (!own) return [];
  return shownStudent.alts.filter(
    (o) => o.key !== v.key && !!(coreOf(o, "chibi") || o.fieldChibi) && coreOf(o, "chibi") !== own,
  );
}

// --- another student's chibi ---
// The most cards a search adds to Switch Chibi: the pairs one chibi.costumes or chibi.checkCostumes call takes
// (webui/api_students.py costume_keys).
export const CROSS_LIMIT = 64;
// "Show other students" (Switch Chibi): every other student's chibi listed after the student's own, not only
// the search's hits. Off at every App start and never saved: kept for as long as the page lives, across students
// and popups.
const others = { shown: false };
/** Whether the 3D popup lists all other students' chibis ("Show other students"). */
export const showOthers = () => others.shown;
/** Set "Show other students". */
export const setShowOthers = (on) => {
  others.shown = !!on;
};
/**
 * The other students' alts (students.list's students) whose chibi an alt's chibi may take as a costume:
 * any with a battle chibi or a field-mode or NPC one.
 */
export function crossChibis(students, v) {
  if (!coreOf(v, "chibi")) return [];
  return (students || [])
    .flatMap((f) => f.alts || [])
    .filter((o) => o.student !== v.student && o.key !== v.key && !!(coreOf(o, "chibi") || o.fieldChibi));
}
/**
 * The card of another student's chibi. It is a deliberate choice: past the stretch limit ("preview") it is
 * applied like "ok"; before its verdict lands it waits (`pending`), and a refused one is shown for a preview
 * only.
 */
export const crossCard = (o, costumes = null) => {
  const verdict = costumes?.[o.key];
  return {
    key: `v:${o.key}`,
    kind: "alt",
    core: coreOf(o, "chibi"),
    alt: o,
    cross: true,
    restorable: true,
    greyed: false,
    ...(verdict === "ok" || verdict === "preview"
      ? {}
      : verdict == null
        ? { pending: true }
        : { previewOnly: true, refused: true }),
    ...cautionsOf(costumes, o.key),
  };
};
/**
 * The cards a search adds after the popup's own: the other students' chibis `match` finds, refused ones left
 * out, in `compare`'s order (the student list's name order), the first `limit` (CROSS_LIMIT: what one RPC call takes;
 * "Show other students" lists them all).
 * @param {object[]} students students.list's students.
 * @param {object|null} [costumes] The chibi's verdicts.
 * @param {object} [options]
 * @param {(chibi: object) => boolean} [options.match] Whether a chibi matches the search.
 * @param {((a: object, b: object) => number)|null} [options.compare] The order of the hits.
 * @param {string[]} [options.exclude] The keys of the popup's own cards.
 * @param {number} [options.limit] The most cards.
 */
export function crossCandidates(
  students,
  v,
  costumes = null,
  { match = () => true, compare = null, exclude = [], limit = CROSS_LIMIT } = {},
) {
  const skip = new Set(exclude);
  const hits = crossChibis(students, v).filter(
    (o) => !skip.has(`v:${o.key}`) && costumes?.[o.key] !== "refused" && match(o),
  );
  if (compare) hits.sort(compare);
  return hits.slice(0, limit).map((o) => crossCard(o, costumes));
}
/**
 * What a card of another student's chibi carries beyond any card's, from the search or from "Show other
 * students" alike: its word of caution on hover (`caution`), and the dev ids the popup's search also finds it
 * by.
 */
export const crossExtras = (c, caution) =>
  c.cross ? { hint: caution, terms: [c.alt.fileId, c.alt.chibi, c.alt.fieldChibi] } : {};
/**
 * What keeps a 3D candidate from being applied by its costume check.
 * @returns {"pending"|"unfit"|null} "pending" (another student's chibi before its verdict), "unfit" (refused:
 *     shown, never applied) or null.
 */
export function costumeHold(shownStudent, v, key, costumes) {
  if (typeof key !== "string" || !key.startsWith("v:")) return null;
  const other = key.slice(2),
    verdict = costumes?.[other];
  if (shownStudent.alts.some((o) => o.key === other))
    return verdict != null && verdict !== "ok" && verdict !== "preview" ? "unfit" : null;
  return verdict === "ok" || verdict === "preview" ? null : verdict == null ? "pending" : "unfit";
}

const sourceKey = (source) => (typeof source === "string" && source.startsWith("alt:") ? `v:${source.slice(4)}` : null);

/**
 * A configured source (student.get's configured[kind]) as a candidate key; null when the kind is not configured.
 */
export function configuredKey(v, kind = "portrait") {
  const source = v?.configured?.[kind];
  if (typeof source !== "string" || !source || source === "official") return null;
  if (source.startsWith("mod:")) return `m:${source.slice(4)}`;
  if (source.startsWith("alt:")) return `v:${source.slice(4)}`;
  return null;
}

/**
 * The candidate key the previews show: always the saved source. The file table and write guards still use
 * appliedOf/appliedKey, which describe the bytes in the game folder.
 */
export const previewKey = (v, kind = "portrait", enabled = true) => {
  const source = v?.previewSources?.[kind];
  if (source === "official") return "self";
  if (source?.startsWith("mod:")) return `m:${source.slice(4)}`;
  if (source?.startsWith("alt:")) return `v:${source.slice(4)}`;
  return configuredKey(v, kind) || (v?.configured || !enabled ? "self" : appliedKey(v, kind));
};

/** The candidate key the game should use, including while Mods are off: the manifest's. */
export const intendedKey = (v, kind = "portrait") => configuredKey(v, kind) || "self";
export const isApplied = (v, kind, key = previewKey(v, kind)) => key === intendedKey(v, kind);

/**
 * The status of an alt's selection of a kind. A switch being On is intent, never evidence that the chosen
 * source reached the game.
 * @returns {"mods.incomplete"|"status.applied"|null} The locale key of the status.
 */
export function selectionStatus(v, kind = "portrait") {
  if (
    (previewKey(v, kind) === intendedKey(v, kind) && v?.selection?.[kind]?.incomplete) ||
    (v?.modList || []).some((m) => m.incomplete && `m:${m.id}` === previewKey(v, kind))
  )
    return "mods.incomplete";
  return isApplied(v, kind) ? "status.applied" : null;
}
export function candidateStatus(v, kind, key, mod = (v?.modList || []).find((m) => `m:${m.id}` === key)) {
  if (mod?.incomplete) return "mods.incomplete";
  return isApplied(v, kind, key) ? "status.applied" : null;
}
export const detailStatus = selectionStatus;
export const selectionError = (v, kind) => v?.selection?.[kind]?.error || null;
export const importStatus = (item) =>
  item?.incomplete ? "mods.incomplete" : item?.applied || item?.configured ? "status.applied" : "import.notApplied";
export const previewChanged = (before, after, kind, beforeEnabled, enabled) =>
  !before ||
  !after ||
  beforeEnabled !== enabled ||
  previewKey(before, kind, beforeEnabled) !== previewKey(after, kind, enabled);
export function chibiSource(v, enabled = true) {
  if (enabled && !v?.configured && !v?.previewSources?.chibi) return "live";
  const key = previewKey(v, "chibi", enabled);
  return key.startsWith("m:")
    ? `mod:${key.slice(2)}`
    : key.startsWith("v:")
      ? `alt:${key.slice(2)}`
      : key === "self"
        ? "official"
        : "live";
}
// the chibi route's address of an alt's chibi from a source; with `clip`, of that clip of it alone (chibi.js
// addClips)
export function chibiUrl(key, source = "live", clip = null) {
  const query = [
    source === "live" ? "" : `source=${encodeURIComponent(source)}`,
    clip ? `clip=${encodeURIComponent(clip)}` : "",
  ];
  const q = query.filter(Boolean).join("&");
  return `/asset/chibi/${encodeURIComponent(key)}.glb${q ? `?${q}` : ""}`;
}

export function previewBase(v, kind = "portrait", enabled = true, alts = []) {
  if (kind === "chibi") return chibiUrl(v.key, chibiSource(v, enabled));
  if (enabled && !v?.configured && !v?.previewSources?.[kind]) return spineBase(kind, coreOf(v, kind));
  const key = previewKey(v, kind, enabled);
  if (key.startsWith("m:")) return spineBase("mod", key.slice(2));
  const source = key.startsWith("v:") ? alts.find((o) => o.key === key.slice(2)) : v;
  return spineBase(`official-${kind}`, coreOf(source || v, kind));
}

/**
 * How the switch popup starts: on the saved choice, including a pending or failed install; the buttons read
 * Apply or Save.
 * @returns {{start: string, button: string, set: boolean}} The card to start on, the primary button's locale
 *     key and whether it saves.
 */
export function popupMode(shownStudent, v, kind = "portrait", enabled = true, students = null) {
  const set = configuredKey(v, kind);
  const listed = candidates(shownStudent, v, kind, null, students);
  const want = previewKey(v, kind, enabled);
  const start = listed.some((c) => c.key === want) ? want : "self";
  return { start, button: "popup.save", set };
}
