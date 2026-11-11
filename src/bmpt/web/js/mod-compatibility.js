// Read structural data with the same bundled Spine runtime as the preview, without creating textures or a player.
import { T, language } from "./i18n.js";
import { rpc, waitJob } from "./api.js";
import * as Loading from "./loading.js";
import * as Popup from "./popup.js";
import * as Compare from "./mod-compare.js";
import { ensureSpine } from "./spine-runtime.js";
import { jobProgress } from "./job-progress.js";
import * as RigGeometry from "./rig-geometry.js";

const key = (name) => name.replaceAll("\\", "/").split("/").at(-1).toLowerCase().replace(/\.png$/i, "");
const joined = (items) => items.join(/^(zh|ja)/i.test(language()) ? "、" : ", ") || "—";
const shortList = (items) => items.length > 3 ? T("compatibility.more", { names: joined(items.slice(0, 3)), n: items.length - 3 }) : joined(items);
const reports = new Map(), stamps = new Map();
function progressTask(action, name, run) {
  const text = (p = {}) => jobProgress({ action: `progress.action${action}`, name: name || T("progress.mods"),
    current: p.current || 1, total: p.total || 1, step: p.step || "" });
  return Loading.task((update) => run((p) => update({ label: text(p) })), { label: text() });
}
export const eligible = (mod) => mod?.kind !== "model" && !mod?.repaired_from && !mod?.repaired_id;
// The remap, for a portrait the game drew anew its old rig as a whole (assessment.oldRig), and the graft of the
// official rig's new animations into the mod's own rig (assessment.graft): all are Update.
// A portrait whose textures were drawn on a rig the App no longer has: nothing repairs it, and a plain update would
// leave it as it is (report.rig.unknown).
export const rigUnknown = (id) => reports.get(id)?.assessment?.issues?.some(({ key }) => key === "rigUnknown") === true;
export const canRemap = (id, mod = null) => eligible(mod) && eligible(reports.get(id))
  && ((reports.get(id)?.remappable === true
    && (reports.get(id)?.assessment?.remap === true || reports.get(id)?.assessment?.oldRig === true))
    || reports.get(id)?.assessment?.graft === true);

export function readSkeleton(input, runtime = globalThis.spine) {
  const bytes = Uint8Array.from(atob(input.data), (c) => c.charCodeAt(0));
  const text = new TextDecoder().decode(bytes).trim();
  const regions = new Set();
  const use = (path, sequence) => {
    if (sequence) for (let i = 0; i < sequence.count; i++) regions.add(`${path}${String((sequence.start ?? 1) + i).padStart(sequence.digits ?? 0, "0")}`);
    else regions.add(path);
  };
  let json = null;
  if (text.startsWith("{")) {
    const data = JSON.parse(text);
    if (!Array.isArray(data.bones) || !data.bones.length) throw new Error("Invalid skeleton data");
    json = data;
    if (!Array.isArray(json.skins)) json.skins = Object.entries(json.skins || {}).map(([name, attachments]) => ({ name, attachments }));
    if (!runtime) {
      const skins = data.skins.map((skin) => skin.attachments);
      for (const skin of skins) for (const entries of Object.values(skin || {})) for (const [name, attachment] of Object.entries(entries)) {
        if (!["boundingbox", "path", "point", "clipping"].includes(attachment.type)) use(attachment.path || name, attachment.sequence);
      }
      return { regions: [...regions], animations: Object.keys(data.animations || {}) };
    }
  }
  if (!runtime || (!json && !input.version?.startsWith("4.2."))) throw new Error("Unsupported skeleton version");
  // Read attachment paths without allocating textures.
  const create = (type, name) => {
    const attachment = new runtime[type](name);
    if (attachment.updateRegion) attachment.updateRegion = () => {};
    return attachment;
  };
  const loader = Object.fromEntries(["Region", "Mesh", "BoundingBox", "Path", "Point", "Clipping"].map((type) =>
    [`new${type}Attachment`, (_skin, name, path, sequence) => {
      if (type === "Region" || type === "Mesh") use(path || name, sequence);
      return create(`${type}Attachment`, name);
    }]));
  const data = json ? new runtime.SkeletonJson(loader).readSkeletonData(json)
    : new runtime.SkeletonBinary(loader).readSkeletonData(bytes);
  return { regions: [...regions], animations: data.animations.map((animation) => animation.name) };
}

export function compare(report, runtime = globalThis.spine, summarize = false) {
  if (!runtime && report.kind !== "model") return { available: false, issues: [{ key: "unavailable", params: {} }], author: false, remap: false };
  const issues = [], add = (key, params = {}, detail = null) => issues.push({ key, params, ...(detail ? { detail } : {}) });
  const list = summarize ? shortList : joined;
  const official = report.official, mod = report.mod;
  if (report.unavailable) add("unavailable");
  const stem = (name) => key(name).replace(/\.(skel|atlas)$/i, "");
  const uses = [];
  for (const [name, source] of Object.entries(mod.skeletons)) {
    const target = official.skeletons[name] || Object.entries(official.skeletons).find(([other]) => stem(other) === stem(name))?.[1]
      || (Object.keys(mod.skeletons).length === 1 && Object.keys(official.skeletons).length === 1 ? Object.values(official.skeletons)[0] : null);
    if (target && source.version?.split(".").slice(0, 2).join(".") !== target.version?.split(".").slice(0, 2).join("."))
      add("runtimeVersion", { name, version: source.version || "?", runtime: target.version || "?" });
    try {
      const data = readSkeleton(source, runtime);
      uses.push({ name, regions: data.regions });
      if (target) {
        let required;
        try { required = readSkeleton(target, runtime).animations; } catch { add("unavailable"); }
        const missing = required?.filter((animation) => !data.animations.includes(animation)) || [];
        if (missing.length) add("missingAnimations", { name, names: list(missing) }, summarize ? { name, names: joined(missing) } : null);
      }
    } catch { add("unreadable", { name }); }
  }
  if (!Object.keys(mod.skeletons).length && Object.keys(official.skeletons).length)
    add("skeletons", { name: joined(Object.keys(official.skeletons)) });
  const atlases = (input) => Object.entries(input.atlases).map(([name, text]) => {
    try {
      const atlas = new runtime.TextureAtlas(text);
      if (!atlas.pages.length || text.includes("\ufffd")) throw new Error("Invalid atlas data");
      return { name, ...atlas };
    } catch { add("atlasUnreadable", { name }); return { name, pages: [], regions: [] }; }
  });
  const left = atlases(mod);
  const unpaired = left.filter((a) => !uses.some((s) => stem(s.name) === stem(a.name)));
  const unnamed = uses.filter((s) => !left.some((a) => stem(s.name) === stem(a.name)));
  const missing = [];
  for (const skeleton of uses) {
    const atlas = left.find((a) => stem(a.name) === stem(skeleton.name))
      || (left.length === 1 ? left[0] : unpaired.length === 1 && unnamed.length === 1 ? unpaired[0] : null);
    for (const path of skeleton.regions) {
      if (!atlas?.regions.some((r) => r.name === path)) missing.push(path);
    }
  }
  if (missing.length) add("regions", { names: list(missing) }, summarize ? { names: joined(missing) } : null);
  for (const atlas of left) for (const page of atlas.pages) {
    const size = mod.textures[key(page.name)];
    if (!size || !page.width || !page.height || size[0] * page.height !== size[1] * page.width)
      add("atlasSize", { name: page.name, atlas: `${page.width} × ${page.height}`, texture: size?.join(" × ") || "—" });
  }
  // Atlases the game repacked since the Mod's textures were drawn on them (the library's record of the Mod, or the
  // earlier release it keeps that the textures follow), and those of them no kept rig repairs (owner, 2026-11-11).
  for (const name of report.rig?.layout || []) add("atlasLayout", { name: key(name) });
  if (report.rig?.unknown?.length) add("rigUnknown");
  // The game drew the portrait anew (actions.redrawn): nothing can be moved, so no remap is offered.
  const redrawn = report.redrawn?.redrawn === true;
  if (redrawn) add("redrawn");
  // Such a portrait can keep the rig its textures were drawn for, as a whole (actions.oldRig), unless the official
  // skeleton has animations the old one lacks: the game calls them by name.
  const gained = redrawn ? [...new Set(Object.values(report.oldRig?.animations || {}).flat())] : [];
  // The graft (actions.graft) keeps the mod's own rig, or that old rig, and adds what the official one gained.
  const graftable = eligible(report) && report.graft?.available === true
    && (report.graft.kind === "own" || (report.graft.kind === "oldRig" && redrawn && gained.length > 0));
  if (gained.length && graftable) {
    for (const [name, names] of Object.entries(report.oldRig.animations))
      add("missingAnimations", { name, names: list(names) }, summarize ? { name, names: joined(names) } : null);
  } else if (gained.length) add("oldRigAnimations", { names: list(gained) }, summarize ? { names: joined(gained) } : null);
  const available = !issues.some(({ key }) => key === "unavailable");
  return { available, issues, author: issues.some(({ key }) => key !== "unavailable"),
    remap: available && eligible(report) && report.remappable === true && !redrawn
      && issues.some(({ key }) => ["regions", "atlasSize", "atlasLayout", "missingAnimations"].includes(key)),
    oldRig: available && eligible(report) && report.remappable === true && redrawn && !gained.length
      && report.oldRig?.available === true,
    graft: available && graftable, graftOldRig: report.graft?.kind === "oldRig" };
}

async function assess(report) {
  let available = report.kind === "model";
  if (!available) {
    try { available = await ensureSpine(); } catch { available = false; }
  }
  return compare(report, available ? globalThis.spine : null);
}

// The result popup (owner, 2026-11-11), plain and left-aligned: what changed, under "Changes:", one list item each (a
// Details button as high as its line), then, where Update repairs it, the game's dotted rule and what Update does, under
// "Auto update:". Nothing a user does not act on (owner, 2026-11-11: no "Mod adapted to the current version required",
// no "CRC fix changes checksums only"). Footer buttons two sizes smaller (Popup.FOOTER.S, owner 2026-11-11); every line at
// one type size.
const ITEM = Object.freeze({ marker: "\u2022", markerGap: 44, indent: 52 });
export function resultLines(result, repair) {
  const item = (line) => ({ ...(typeof line === "string" ? { text: line } : line), ...ITEM });
  const changes = new Map();
  for (const { key, params, detail } of result.issues) {
    const text = T(`compatibility.${key}`, params), full = detail ? T(`compatibility.${key}`, detail) : text;
    if (changes.has(text)) continue;  // one line per finding: several atlases can tell the same
    changes.set(text, text === full ? text : { text, button: { label: T("info.details"), inline: "line", fn: () =>
      Popup.message({ title: T("info.details"), lines: [full], scroll: true, ok: T("picker.ok") }) } });
  }
  const lines = changes.size ? [T("compatibility.changes"), ...[...changes.values()].map(item)] : [T("compatibility.matches")];
  if (repair) lines.push({ rule: true }, T("compatibility.autoUpdate"), ...repairs(result).map(({ text }) => item(text)));
  return lines;
}

// What Update can do, first the default: the graft keeps the mod's own rig (owner, 2026-11-11), the remap puts the
// mod's pixels on the official rig, the old rig keeps a redrawn portrait's whole rig.
export function repairs(result) {
  const out = [];
  if (result.graft) out.push({ kind: "graft", text: T(result.graftOldRig ? "compatibility.graftOldRig" : "compatibility.graft"),
    label: T("compatibility.graftAction") });
  if (result.remap) out.push({ kind: "remap", text: T("compatibility.remapUpdate"), label: T("compatibility.remapAction") });
  if (result.oldRig) out.push({ kind: "oldRig", text: T("compatibility.oldRig"), label: T("remap.action") });
  return out;
}

// The footer of a popup that offers the repairs: Update for one, a named button each for more (the default last).
function repairButtons(choices) {
  if (choices.length < 2) return { ok: T("remap.action"), cancel: T("popup.cancel") };
  return { buttons: [{ label: T("popup.cancel"), value: "cancel" },
    ...choices.slice(1).map(({ kind, label }) => ({ label, value: kind })),
    { label: choices[0].label, value: choices[0].kind, primary: true }] };
}
const chosen = (response, choices) => response.button === "ok" ? choices[0]?.kind
  : choices.some(({ kind }) => kind === response.button) ? response.button : null;

export async function show(report, { onRemap } = {}) {
  if (report.kind !== "model" && !await ensureSpine()) return;
  const result = compare(report, globalThis.spine, true);
  const choices = onRemap ? repairs(result) : [];
  const repair = choices.length > 0;
  const lines = resultLines(result, repair);
  // the lines share one type size, as siblings do (docs/ui-guidelines.md)
  const response = await Popup.message({ title: T("compatibility.result"), lines, scroll: true, plain: true,
    fitTogether: lines.flatMap((line, i) => (line.rule ? [] : [i])),
    ...(repair ? repairButtons(choices) : { ok: T("picker.ok") }) });
  const kind = repair ? chosen(response, choices) : null;
  if (kind) await onRemap(kind);
}

// The repair review's lines: one per kind of region with its count, the names behind a Details button. Regions are
// named "<atlas> - <region>"; the atlas is left out when every region of the report is in the same one.
export function reviewLines(report) {
  // an old rig set up as a whole (actions.oldRig) moves nothing: its line names the atlases kept
  if (report.rigs?.length) return [T("remap.oldRig", { names: joined(report.rigs) }),
    ...report.warnings.map(({ key, name }) => T(`remap.${key}`, { name }))];
  // a graft (actions.graft) counts the expressions it added, corrected and could not match, the names behind Details
  if (report.grafted) {
    const named = (key, names) => !names.length ? [] : [{ text: T(`remap.${key}`, { n: names.length }), button: {
      label: T("info.details"), fn: () => Popup.message({ title: T("info.details"), lines: [joined(names)], scroll: true,
        ok: T("picker.ok") }) } }];
    return [...named("grafted", report.grafted), ...named("graftCorrected", report.corrected || []),
      ...named("graftUnfixed", report.unfixed || []), ...named("graftRefused", Object.keys(report.refused || {})),
      ...(report.grown ? [T("remap.graftGrown", { n: report.grown })] : []),
      ...(report.unanchored ? [T("remap.graftUnanchored")] : [])];
  }
  const kinds = { unpainted: report.added || [], official: report.official || [], dropped: report.dropped || [] };
  const atlases = new Set(Object.values(kinds).flat().map((name) => name.split(" - ")[0]));
  const bare = (names) => atlases.size === 1 ? names.map((name) => name.slice(name.indexOf(" - ") + 3)) : names;
  const details = (key, names) => ({ label: T("info.details"), fn: () => Popup.message({ title: T("info.details"),
    lines: [T(`remap.${key}`, { names: joined(bare(names)) })], scroll: true, ok: T("picker.ok") }) });
  const counted = (key, names) => !names.length ? [] : [{ text: T(`remap.${key}`, { names: names.length }), button: details(key, names) }];
  // the first line counts the moved and the unused regions: the unused ones' names are behind its own button
  const counts = T("remap.counts", { moved: report.moved.length, dropped: report.dropped.length });
  return [kinds.dropped.length ? { text: counts, button: details("dropped", kinds.dropped) } : counts,
    ...new Set((report.upscaled || []).map(({ w, h }) => T("remap.upscaled", { w, h }))),
    ...counted("official", kinds.official), ...counted("unpainted", kinds.unpainted),
    ...report.warnings.map(({ key, name }) => T(`remap.${key}`, { name }))];
}

// The graft (actions.graft, graftBuild, graftCheck): the page poses the rigs the library names, the library splices and
// packs and hands back a test rig with every candidate correction, the page poses that, and the library checks and
// prepares the review. A graft left between its steps is dropped.
async function grafted(id, subject) {
  if (!await ensureSpine()) return null;
  const S = globalThis.spine;
  let token = null;
  try {
    return await Loading.hold(() => Loading.page(() => progressTask("Remap", subject, async (update) => {
      const first = await waitJob(await rpc("actions.graft", { mod: id }), update);
      token = first.token;
      const skeleton = (rig) => ({ data: rig.skeleton });
      const setup = Object.fromEntries(Object.entries(first.rigs).map(([name, rig]) => [name, {
        mod: RigGeometry.setupOf(S, rig.mod.atlas, skeleton(rig.mod)),
        official: RigGeometry.setupOf(S, rig.official.atlas, skeleton(rig.official)),
        base: rig.base ? RigGeometry.setupOf(S, rig.base.atlas, skeleton(rig.base)) : null }]));
      const built = await waitJob(await rpc("actions.graftBuild", { token, geometry: setup }), update);
      const posed = Object.fromEntries(Object.entries(built.rigs).map(([name, rig]) => [name, {
        test: RigGeometry.partPoses(S, rig.atlas, skeleton(rig), rig.check),
        official: RigGeometry.partPoses(S, first.rigs[name].official.atlas, skeleton(first.rigs[name].official), rig.official) }]));
      const result = await waitJob(await rpc("actions.graftCheck", { token, geometry: posed }), update);
      token = null;  // the check takes it
      return result;
    })), { all: true });
  } finally {
    if (token) await rpc("actions.discardGraft", { token }).catch(() => {});
  }
}

// prepare(id, label, subject, kind, choice): the repair's preview, no review: the compatibility check if none is in
// hand, the repair (the choice given; "default": the first of repairs(), no question; null: asked when there are more),
// its job under the loading screen. Resolves with the job's result ({preview, report, compare, pages, drawings}, or
// {unchanged}), or null when nothing was made (no repair chosen). The caller accepts (accept) or discards it.
export async function prepare(id, label = "", subject = label, kind = "", choice = null) {
  // A portrait is baked through world space: the page poses both rigs with the runtime and posts what they draw.
  let checked = reports.get(id);
  if (!checked && kind === "spr") checked = await inspect(id, label, null, subject);
  if (checked?.assessment && (!choice || choice === "default")) {
    const choices = repairs(checked.assessment);
    // More than one repair (Update without the result popup): the same choice as there.
    if (choices.length > 1 && choice !== "default") {
      const lines = [T("compatibility.autoUpdate"), ...choices.map(({ text }) => ({ text, ...ITEM }))];
      choice = chosen(await Popup.message({ title: T("act.update"), lines, plain: true,
        fitTogether: lines.map((_line, i) => i), ...repairButtons(choices) }), choices);
      if (!choice) return null;
    } else choice = choices[0]?.kind ?? null;
  }
  if (choice === "default") choice = null;
  // A portrait the game drew anew keeps its old rig as a whole (actions.oldRig): no geometry to post.
  const old = choice === "oldRig" || (!choice && checked?.assessment?.oldRig === true);
  return choice === "graft" ? grafted(id, subject) : (async () => {
    const geometry = !old && checked?.kind === "spr" && await ensureSpine() ? RigGeometry.forRemap(checked, globalThis.spine) : undefined;
    return Loading.hold(() => Loading.page(() => progressTask("Remap", subject,
      async (update) => waitJob(await rpc(old ? "actions.oldRig" : "actions.remap",
        old || !geometry ? { mod: id } : { mod: id, geometry }), update),
    )), { all: true });
  })();
}

// accept(id, result, onRemapped, subject): store a prepared repair (actions.acceptRemap); resolves with the saved
// copy ({mod: {id}}). The preview is the caller's to discard (discard) either way.
export async function accept(id, result, onRemapped = () => {}, subject = "") {
  return Loading.hold(() => Loading.page(() => progressTask("Repair", subject, async (update) => {
    const saved = await waitJob(await rpc("actions.acceptRemap", { preview: result.preview }), update);
    reports.set(id, { ...reports.get(id), remappable: false, repaired_id: saved.mod.id });
    stamps.delete(id); stamps.delete(saved.mod.id);
    reports.set(saved.mod.id, { remappable: false, repaired_from: id });
    await onRemapped(saved);
    return saved;
  })), { all: true });
}

export const discard = (result) => (result?.preview ? rpc("actions.discardRemap", { preview: result.preview }).catch(() => {}) : Promise.resolve());

// review(id, result, options): the repair review (Compare.show): both pictures, the report's lines and marks; resolves
// true when accepted. options: {label, kind, accept, discard}: the review's title, kind and button labels.
export function review(id, result, { label = "", kind = "", accept: acceptLabel = T("remap.accept"), discard: discardLabel = T("remap.discard") } = {}) {
  const report = result.report;
  return Compare.show({ ...result.compare, pages: result.pages, drawings: result.drawings }, label || T("remap.action"), "dialog", {
    lines: reviewLines(report), partial: report.partial, accept: acceptLabel, discard: discardLabel, kind: kind || reports.get(id)?.kind || "spr",
    // the regions whose pixels are not the mod's own, or a graft's parts that match no candidate: what the
    // pictures can ring
    marks: report.grafted ? report.marks || []
      : [...new Set((report.official || []).map((name) => name.slice(name.indexOf(" - ") + 3)))],
    // a graft's review steps through the expressions it added, under a verdict of its own
    expressions: report.grafted || [],
    verdict: report.grafted ? (report.partial ? "remap.graftPartial" : "remap.graftDone") : null,
  });
}

export async function remap(id, onRemapped = () => {}, label = "", subject = label, kind = "", choice = null) {
  const result = await prepare(id, label, subject, kind, choice);
  if (!result) return null;
  if (result.unchanged) {
    reports.set(id, { ...reports.get(id), remappable: false });
    await Popup.message({ title: T("remap.action"), lines: [T("remap.unneeded")], ok: T("picker.ok") });
    return result;
  }
  try {
    if (!await review(id, result, { label, kind })) return null;
    return await accept(id, result, onRemapped, subject);
  } finally {
    await discard(result);
  }
}

// The official animations a Mod lacks after an update that kept its own skeleton (UpdateResult.rig), as a line.
export function rigNotice(result) {
  const names = new Set(Object.values(result?.rig?.animations || {}).flat());
  return names.size ? T("compatibility.rigChanged", { n: names.size }) : "";
}

export async function inspect(id, label = "", stamp = null, subject = label) {
  stamp ??= await rpc("actions.compatibilityStamp", { mod: id });
  const report = await progressTask("Compatibility", subject,
    async (update) => waitJob(await rpc("actions.compatibility", { mod: id }), update));
  // A portrait the remap could repair: whether the game drew it anew, from the two rigs' rest poses.
  if (report.kind === "spr" && report.remappable === true && await ensureSpine().catch(() => false)) {
    let geometry = null;
    try { geometry = RigGeometry.restOnly(RigGeometry.forRemap(report, globalThis.spine)); } catch { geometry = null; }
    if (geometry && Object.keys(geometry).length) report.redrawn = await progressTask("Compatibility", subject,
      async (update) => waitJob(await rpc("actions.redrawn", { mod: id, geometry }), update));
  }
  const current = await rpc("actions.compatibilityStamp", { mod: id });
  if (current !== stamp) {
    stamps.delete(id); reports.delete(id);
    throw Object.assign(new Error("Compatibility inputs changed"), { code: "entry_changed" });
  }
  stamps.set(id, stamp);
  const checked = { ...report, assessment: await assess(report) };
  reports.set(id, checked);
  return checked;
}

// Reuse a report only while the exact Mod, install and candidate official source files still match.
export async function forUpdate(id, label = "", subject = label) {
  const stamp = await rpc("actions.compatibilityStamp", { mod: id });
  if (stamps.get(id) !== stamp || !reports.has(id)) return inspect(id, label, stamp, subject);
  const report = reports.get(id), checked = { ...report, assessment: await assess(report) };
  reports.set(id, checked);
  return checked;
}

export async function check(id, label = "", onRemapped = () => {}, mod = null, subject = label) {
  if (mod?.kind !== "model" && !await ensureSpine()) return;
  const report = await Loading.hold(() => inspect(id, label, null, subject), { all: true });
  return show(report, { onRemap: (choice) => remap(id, onRemapped, label, subject, mod?.kind || report.kind, choice) });
}
