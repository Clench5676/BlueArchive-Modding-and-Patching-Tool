// What the detail screen's switch cards offer and what the game shows now, for the portrait ("spr") and the memorial
// lobby ("lobby"): pure functions of the family that family.get returns, tested in Node
// (tests/js/switch_sources.test.mjs). Switching saves the chosen source and installs it while enabled; the
// library keeps what it replaces (docs/web-ui.md, library).
import { spineBase } from "./api.js";

// Every asset tab uses the same source selection and apply flow.
export const switchKindOf = (tab) => ({ portrait: "spr", lobby: "lobby", model: "model" }[tab] || null);

// the core of a variant's skeleton of a kind: its portrait or its memorial lobby
export const coreOf = (v, kind = "spr") => (kind === "model" ? v.model : kind === "lobby" ? v.lobby : v.spr) || null;
// a variant with only a field-mode model (fieldModel: an event or story costume) has a 3D tab, but no slot to switch
export const hasKind = (v, kind) => ["spr", "lobby", "model"].includes(kind)
  && (!!coreOf(v, kind) || (kind === "model" && !!v?.fieldModel) || !!v?.storedKinds?.includes(kind));
export const hasAsset = (v) => hasKind(v, "spr") || hasKind(v, "lobby") || hasKind(v, "model");
export const primaryKind = (v) => hasKind(v, "spr") ? "spr" : hasKind(v, "lobby") ? "lobby" : hasKind(v, "model") ? "model" : "lobby";
export function availableTab(v, tab) {
  if (hasKind(v, switchKindOf(tab))) return tab;
  return hasKind(v, "spr") ? "portrait" : hasKind(v, "lobby") ? "lobby" : hasKind(v, "model") ? "model" : "lobby";
}

// the game file parts of one kind ("spr" portrait, "lobby" memorial lobby, "model" the 3D chibi's whole slot,
// newest first)
export const partsOf = (v, kind = "spr") => {
  const parts = (v?.state?.files || []).filter((f) => f.kind === kind);
  return kind === "model" && !parts.length ? v?.modelFiles || [] : parts;
};

// whether a game file part holds official bytes: the server's verdict (family.get's official), else, from a server
// without it, its applied source
export const isOfficial = (f) => (typeof f.official === "boolean" ? f.official : f.applied === "official");

// what the variant's game files of a kind carry now, from their non-official parts only (the library records every
// part of a switched kind, the parts a mod left official included): the one source among them (a mod often replaces
// one part), "official", or "unknown" when they carry several (tests/data/source_rule_cases.json, shared with the
// library's own rule)
export function appliedOf(v, kind = "spr") {
  const sources = new Set(partsOf(v, kind).filter((f) => !isOfficial(f)).map((f) => f.applied));
  return !sources.size ? "official" : sources.size === 1 ? [...sources][0] : "unknown";
}

// the candidate key of what the game shows: "self" (the variant's own official files), "m:<mod id>", "v:<variant
// key>" (another variant's official files, adapted) or "unknown" (changed outside the library)
export function appliedKey(v, kind = "spr") {
  const a = appliedOf(v, kind);
  if (a === "official") return "self";
  if (a.startsWith("mod:")) return `m:${a.slice(4)}`;
  if (a.startsWith("variant:")) return `v:${a.slice(8)}`;
  return "unknown";
}

// a candidate key as an actions.apply source
export const sourceOf = (key) => (key === "self" ? "official" : key.startsWith("m:") ? `mod:${key.slice(2)}` : `variant:${key.slice(2)}`);

// whether a variant's files of a kind are overwritten: a part that is not official has no official copy anywhere
// (family.get's restorable false). Its official choice is unavailable; library mods can still replace it.
export const overwrittenOf = (v, kind = "spr") => partsOf(v, kind).some((f) => !isOfficial(f) && f.restorable === false);

// the sources a variant's portrait or lobby can be switched to, in the switch popup's order: its own official files,
// its own mods of that kind, then for the portrait the other variants' official files (those with a portrait of their
// own) and the other variants' portrait mods. Other variants' memorial lobbies and their lobby mods are not offered:
// another student's lobby put under this variant's lobby prefab is untested in the game, and a lobby popup lists only
// the lobby mods of its own core (a mod of an alternate lobby core, _home_gl, is switched by the toggle only; a mod
// without core is matched by its target). Each: { key, kind ("self" | "mod" | "variant"), core, variant, id, mod,
// origin (the variant a mod was made for), restorable (its files have an official copy), greyed (cannot be chosen:
// on an overwritten kind every card but the one in use, and another variant's card whose files of the kind are
// overwritten) }.
// The 3D model lists every other model of the student as a costume at once (BL-136); only a pair that was built and
// exported for these files can be applied (``costumes``: model.costumes' verdicts, "ok" | "preview" | "refused" |
// null): "ok" pairs are offered, the others (past the stretch limit, not checked yet, refused) for a preview only
// (``previewOnly``), and a refused pair cannot be chosen as the preview either (``refused``, BL-122).
// Another student's model (BL-131, ``families``: roster.list's) is offered through the popup's search only
// (crossCandidates), save the one already chosen, listed last (``cross``).
export function candidates(fam, v, kind = "spr", costumes = null, families = null) {
  const own = coreOf(v, kind);
  const cores = (kind === "model" && v.modelCores?.length ? v.modelCores : [own]).map((core) => String(core).toLowerCase());
  const mods = fam.variants.flatMap((o) => o.modList || []).filter((m) => m.kind === kind)
    .filter((m) => kind === "spr" || !m.core || !own || cores.includes(m.core));
  const across = (kind === "spr" || kind === "model") && !!own; // Cross-variant adaptation requires an actual local target.
  const others = !across ? [] : kind === "model"
    ? costumeCandidates(fam, v)
    : fam.variants.filter((o) => o.key !== v.key && !!coreOf(o, kind) && coreOf(o, kind) !== own);
  const chosen = kind === "model" ? new Set([configuredKey(v, kind), sourceKey(v?.previewSources?.model)]) : null;
  const chosenCross = kind === "model" && across ? crossModels(families, v).filter((o) => chosen.has(`v:${o.key}`)) : [];
  const mod = (m) => ({ key: `m:${m.id}`, kind: "mod", id: m.id, mod: m, core: null, origin: fam.variants.find((o) => o.key === m.target) || null });
  const list = [
    { key: "self", kind: "self", core: own, variant: v },
    ...mods.filter((m) => m.target === v.key).map(mod),
    // previewOnly: past the stretch limit ("preview"), not checked yet or refused: shown, never applied
    ...others.map((o) => ({ key: `v:${o.key}`, kind: "variant", core: coreOf(o, kind), variant: o,
      ...(kind === "model" && costumes?.[o.key] !== "ok" ? { previewOnly: true } : {}),
      ...(kind === "model" && costumes?.[o.key] === "refused" ? { refused: true } : {}) })),
    ...(across && kind === "spr" ? mods.filter((m) => m.target !== v.key).map(mod) : []),
    ...chosenCross.map((o) => crossCard(o, costumes)),
  ];
  const stuck = overwrittenOf(v, kind);
  return list.map((c) => {
    const restorable = c.cross || c.kind === "mod" ? true : !overwrittenOf(c.variant, kind);
    const greyed = (c.kind === "self" && (!own || stuck)) || (c.kind === "variant" && !restorable);
    return { ...c, restorable, greyed };
  });
}

// the other variants whose model a variant's 3D model may take as a costume, before their check: any other model of
// the student, field-mode models included (model.costumes / model.checkCostumes judge each pair)
export function costumeCandidates(fam, v) {
  const own = coreOf(v, "model");
  if (!own) return [];
  return fam.variants.filter((o) => o.key !== v.key && !!(coreOf(o, "model") || o.fieldModel) && coreOf(o, "model") !== own);
}

// --- another student's model (BL-131) ---
// The most cards a search adds to Switch 3D Model: the pairs one model.costumes or model.checkCostumes call takes
// (webui/api.py costume_keys).
export const CROSS_LIMIT = 64;
// the other students' variants (roster.list's families) whose model a variant's 3D model may take as a costume: any
// with a battle model or a field-mode or NPC one (BL-127)
export function crossModels(families, v) {
  if (!coreOf(v, "model")) return [];
  return (families || []).flatMap((f) => f.variants || [])
    .filter((o) => o.family !== v.family && o.key !== v.key && !!(coreOf(o, "model") || o.fieldModel));
}
// Another student's model is a deliberate choice: past the stretch limit ("preview") it is applied like "ok"; before its
// verdict lands it waits (``pending``), and a refused one is shown for a preview only.
export const crossCard = (o, costumes = null) => {
  const verdict = costumes?.[o.key];
  return { key: `v:${o.key}`, kind: "variant", core: coreOf(o, "model"), variant: o, cross: true, restorable: true, greyed: false,
    ...(verdict === "ok" || verdict === "preview" ? {} : verdict == null ? { pending: true } : { previewOnly: true, refused: true }) };
};
// the cards a search adds after the popup's own (``exclude``: their keys): the other students' models ``match`` finds,
// refused ones left out, in ``compare``'s order (the roster's name order), the first 64
export function crossCandidates(families, v, costumes = null, { match = () => true, compare = null, exclude = [] } = {}) {
  const skip = new Set(exclude);
  const hits = crossModels(families, v).filter((o) => !skip.has(`v:${o.key}`) && costumes?.[o.key] !== "refused" && match(o));
  if (compare) hits.sort(compare);
  return hits.slice(0, CROSS_LIMIT).map((o) => crossCard(o, costumes));
}
// what keeps a 3D candidate from being applied by its costume check: null, "pending" (another student's model before
// its verdict) or "unfit" (refused, or the student's own model past the stretch limit: shown, never applied, BL-122)
export function costumeHold(fam, v, key, costumes) {
  if (typeof key !== "string" || !key.startsWith("v:")) return null;
  const other = key.slice(2), verdict = costumes?.[other];
  if (fam.variants.some((o) => o.key === other)) return verdict && verdict !== "ok" ? "unfit" : null;
  return verdict === "ok" || verdict === "preview" ? null : verdict == null ? "pending" : "unfit";
}

const sourceKey = (source) => (typeof source === "string" && source.startsWith("variant:") ? `v:${source.slice(8)}` : null);

// a configured source (family.get's configured[kind]) as a candidate key; null when the kind is not configured
export function configuredKey(v, kind = "spr") {
  const source = v?.configured?.[kind];
  if (typeof source !== "string" || !source || source === "official") return null;
  if (source.startsWith("mod:")) return `m:${source.slice(4)}`;
  if (source.startsWith("variant:")) return `v:${source.slice(8)}`;
  return null;
}

// Manager previews always show the saved source. The file table and write guards
// still use appliedOf/appliedKey: those describe the bytes in the game folder.
export const previewKey = (v, kind = "spr", enabled = true) => {
  const source = v?.previewSources?.[kind];
  if (source === "official") return "self";
  if (source?.startsWith("mod:")) return `m:${source.slice(4)}`;
  if (source?.startsWith("variant:")) return `v:${source.slice(8)}`;
  return configuredKey(v, kind) || (v?.configured || !enabled ? "self" : appliedKey(v, kind));
};

// The manifest describes what the game should use, including while Mods are off.
export const intendedKey = (v, kind = "spr") => configuredKey(v, kind) || "self";
export const isApplied = (v, kind, key = previewKey(v, kind)) => key === intendedKey(v, kind);

// A switch being On is intent, never evidence that the chosen source reached the game.
export function selectionStatus(v, kind = "spr") {
  if ((previewKey(v, kind) === intendedKey(v, kind) && v?.selection?.[kind]?.incomplete)
    || (v?.modList || []).some((m) => m.incomplete && `m:${m.id}` === previewKey(v, kind)))
    return "mods.incomplete";
  return isApplied(v, kind) ? "status.applied" : null;
}
export function candidateStatus(v, kind, key, mod = (v?.modList || []).find((m) => `m:${m.id}` === key)) {
  if (mod?.incomplete) return "mods.incomplete";
  return isApplied(v, kind, key) ? "status.applied" : null;
}
export const detailStatus = selectionStatus;
export const selectionError = (v, kind) => v?.selection?.[kind]?.error || null;
export const importStatus = (item) => item?.incomplete ? "mods.incomplete" : item?.applied || item?.configured ? "status.applied" : "import.notApplied";
export const previewChanged = (before, after, kind, beforeEnabled, enabled) => !before || !after || beforeEnabled !== enabled
  || previewKey(before, kind, beforeEnabled) !== previewKey(after, kind, enabled);
export function modelSource(v, enabled = true) {
  if (enabled && !v?.configured && !v?.previewSources?.model) return "live";
  const key = previewKey(v, "model", enabled);
  return key.startsWith("m:") ? `mod:${key.slice(2)}` : key.startsWith("v:") ? `variant:${key.slice(2)}`
    : key === "self" ? "official" : "live";
}
export const modelUrl = (key, source = "live") => `/asset/model/${encodeURIComponent(key)}.glb${source === "live" ? "" : `?source=${encodeURIComponent(source)}`}`;

export function previewBase(v, kind = "spr", enabled = true, variants = []) {
  if (kind === "model") return modelUrl(v.key, modelSource(v, enabled));
  if (enabled && !v?.configured && !v?.previewSources?.[kind]) return spineBase(kind, coreOf(v, kind));
  const key = previewKey(v, kind, enabled);
  if (key.startsWith("m:")) return spineBase("mod", key.slice(2));
  const source = key.startsWith("v:") ? variants.find((o) => o.key === key.slice(2)) : v;
  return spineBase(`official-${kind}`, coreOf(source || v, kind));
}

// Start on the saved choice, including a pending or failed install. Keep Apply/Save labels.
export function popupMode(fam, v, kind = "spr", enabled = true, families = null) {
  const set = configuredKey(v, kind);
  const listed = candidates(fam, v, kind, null, families);
  const want = previewKey(v, kind, enabled);
  const start = listed.some((c) => c.key === want) ? want : "self";
  return { start, button: "popup.save", set };
}
