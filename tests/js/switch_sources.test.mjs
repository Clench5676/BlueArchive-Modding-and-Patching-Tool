// Tests for web/js/switch-sources.js (what the detail screen's switch cards offer and what the game shows), run by
// tests/test_switch_sources.py with Node's test runner: node --test tests/js/switch_sources.test.mjs
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

import {
  modelSource, modelUrl, appliedKey, appliedOf, availableTab, candidates, configuredKey, coreOf, costumeCandidates, costumeHold, crossCandidates, crossExtras, crossModels, hasAsset, hasKind, overwrittenOf, partsOf, popupMode, primaryKind, previewBase, previewChanged, previewKey, setShowOthers, showOthers, sourceOf, switchKindOf,
} from "../../src/bmpt/web/js/switch-sources.js";

const part = (kind, applied, extra = {}) => ({ file: `/game/${kind}-${applied}.bundle`, kind, applied, crc: "ok", ...extra });
const mod = (id, target, kind) => ({ id, target, kind, label: id, file: `/library/mods/${id}/${id}.bundle`, fresh: false });
// hoshino: a portrait and a lobby; swimsuit: a portrait and a lobby of its own; np: a portrait only; shared: the same
// lobby core as hoshino
const family = () => ({
  key: "hoshino",
  variants: [
    { key: "hoshino", spr: "hoshino_spr", lobby: "hoshino_home",
      state: { files: [part("spr", "official"), part("lobby", "mod:l1")] },
      modList: [mod("p1", "hoshino", "spr"), mod("l1", "hoshino", "lobby")] },
    { key: "hoshino_swimsuit", spr: "hoshino_swimsuit_spr", lobby: "hoshino_swimsuit_home",
      state: { files: [part("spr", "variant:hoshino"), part("lobby", "official")] },
      modList: [mod("p2", "hoshino_swimsuit", "spr"), mod("l2", "hoshino_swimsuit", "lobby")] },
    { key: "np0045", spr: "np0045_spr", lobby: null, state: { files: [part("spr", "official")] }, modList: [] },
    { key: "hoshino_shared", spr: "hoshino_shared_spr", lobby: "hoshino_home", state: { files: [] }, modList: [] },
  ],
});

test("stored-only character sources are previewable without an official install target", () => {
  const student = { key: "future", spr: null, lobby: null, model: null, storedKinds: ["lobby"], localKinds: [],
    state: { files: [] }, configured: {}, previewSources: { lobby: "mod:future-mod" },
    modList: [mod("future-mod", "future", "lobby")] };
  const fam = { variants: [student] };
  assert.ok(hasAsset(student));
  assert.equal(primaryKind(student), "lobby");
  assert.equal(availableTab(student, "portrait"), "lobby");
  assert.equal(coreOf(student, "lobby"), null);
  assert.equal(previewBase(student, "lobby", false), "/asset/spine/mod/future-mod/skeleton");
  const choices = candidates(fam, student, "lobby");
  assert.equal(choices.find((choice) => choice.key === "self").greyed, true);
  assert.equal(choices.find((choice) => choice.kind === "mod").greyed, false);
  assert.equal(configuredKey(student, "lobby"), null);
  const modelOnly = { ...student, storedKinds: ["model"], previewSources: { model: "mod:future-model" } };
  assert.ok(hasAsset(modelOnly));
  assert.equal(primaryKind(modelOnly), "model");
  assert.equal(availableTab(modelOnly, "portrait"), "model");
  assert.equal(modelSource(modelOnly), "mod:future-model");
});

test("stored-only portraits offer their own Mods without sibling adaptation sources", () => {
  const student = { key: "future", spr: null, lobby: null, storedKinds: ["spr"], localKinds: [],
    state: { files: [] }, modList: [mod("own", "future", "spr")] };
  const sibling = { key: "released", spr: "released_spr", state: { files: [] },
    modList: [mod("sibling", "released", "spr")] };
  const choices = candidates({ variants: [student, sibling] }, student, "spr");
  assert.deepEqual(choices.map((choice) => choice.key), ["self", "m:own"]);
  assert.equal(choices[0].greyed, true);
  assert.equal(choices[1].greyed, false);
});

test("special slots open their lobby tab and switch only mods for their own core", () => {
  const day = { key: "special:arona_workpage_daytime", spr: null, lobby: "arona_workpage_daytime", spineCategory: "spinebackground",
    state: { files: [part("lobby", "official")] }, configured: { lobby: "mod:day" } };
  const night = { ...day, key: "special:arona_workpage_night", lobby: "arona_workpage_night" };
  day.modList = [{ ...mod("day", day.key, "lobby"), core: day.lobby }];
  night.modList = [{ ...mod("night", night.key, "lobby"), core: night.lobby }];
  const fam = { variants: [day, night, ...family().variants] };
  assert.ok(hasAsset(day));
  assert.equal(primaryKind(day), "lobby");
  for (const tab of ["portrait", "model", "lobby"]) assert.equal(availableTab(day, tab), "lobby");
  assert.equal(availableTab(fam.variants[2], "portrait"), "portrait");
  assert.equal(availableTab(fam.variants[4], "lobby"), "portrait");
  assert.deepEqual(candidates(fam, day, "lobby").map((c) => c.key), ["self", "m:day"]);
  assert.equal(previewBase(day, "lobby", true), "/asset/spine/mod/day/skeleton");
  assert.equal(previewBase(day, "lobby", false), "/asset/spine/mod/day/skeleton");
  assert.deepEqual(popupMode(fam, day, "lobby", false), { start: "m:day", button: "popup.save", set: "m:day" });
  day.state.files = [part("lobby", "mod:day")];
  assert.equal(popupMode(fam, day, "lobby", true).start, "m:day");
  assert.equal(sourceOf("self"), "official");
});

test("candidates of the portrait: own, own mods, other variants, their mods", () => {
  const fam = family();
  const list = candidates(fam, fam.variants[0], "spr");
  assert.deepEqual(list.map((c) => c.key), ["self", "m:p1", "v:hoshino_swimsuit", "v:np0045", "v:hoshino_shared", "m:p2"]);
  assert.equal(list[0].core, "hoshino_spr");
  assert.equal(list[2].core, "hoshino_swimsuit_spr");
  assert.equal(list.at(-1).origin.key, "hoshino_swimsuit");
});

test("candidates of the memorial lobby: the variant's own lobby and its lobby mods only", () => {
  const fam = family();
  const list = candidates(fam, fam.variants[0], "lobby");
  // no portrait mods, and neither another variant's lobby nor its lobby mods (untested under this variant's prefab)
  assert.deepEqual(list.map((c) => c.key), ["self", "m:l1"]);
  assert.equal(list[0].core, "hoshino_home");
  assert.ok(list.every((c) => c.kind !== "mod" || c.mod.kind === "lobby"));
  assert.deepEqual(candidates(fam, fam.variants[1], "lobby").map((c) => c.key), ["self", "m:l2"]);
  assert.deepEqual(candidates(fam, fam.variants[2], "lobby").map((c) => c.key), ["self"]);
});

test("what the game shows, per kind", () => {
  const [hoshino, swimsuit, np] = family().variants;
  assert.equal(appliedOf(hoshino, "spr"), "official");
  assert.equal(appliedKey(hoshino, "spr"), "self");
  assert.equal(appliedKey(hoshino, "lobby"), "m:l1");
  assert.equal(appliedKey(swimsuit, "spr"), "v:hoshino");
  assert.equal(appliedKey(swimsuit, "lobby"), "self");
  assert.equal(appliedKey(np, "lobby"), "self"); // no lobby files: nothing replaced
});

test("parts with several sources, or changed outside the library, are unknown", () => {
  const v = { state: { files: [part("spr", "mod:a"), part("spr", "mod:b")] } };
  assert.equal(appliedOf(v), "unknown");
  assert.equal(appliedKey(v), "unknown");
  const outside = { state: { files: [part("lobby", "unknown"), part("lobby", "official")] } };
  assert.equal(appliedKey(outside, "lobby"), "unknown");
  // one modded part among official ones: the mod
  assert.equal(appliedKey({ state: { files: [part("spr", "official"), part("spr", "mod:a")] } }), "m:a");
});

test("sources for actions.apply", () => {
  assert.equal(sourceOf("self"), "official");
  assert.equal(sourceOf("m:abc"), "mod:abc");
  assert.equal(sourceOf("v:hoshino_swimsuit"), "variant:hoshino_swimsuit");
});

test("parts and cores of a kind", () => {
  const v = { spr: "a_spr", lobby: null, modelFiles: [{ file: "m" }], state: { files: [part("spr", "official"), part("lobby", "official")] } };
  assert.equal(partsOf(v, "spr").length, 1);
  assert.equal(partsOf(v, "model")[0].file, "m");
  assert.equal(coreOf(v, "spr"), "a_spr");
  assert.equal(coreOf(v, "lobby"), null);
});

test("the switch card uses the asset kind of every tab", () => {
  assert.equal(switchKindOf("portrait"), "spr");
  assert.equal(switchKindOf("lobby"), "lobby");
  assert.equal(switchKindOf("model"), "model");
});

test("what the game shows follows the library's source rule, part by part", () => {
  const url = new URL("../data/source_rule_cases.json", import.meta.url);
  const { cases } = JSON.parse(readFileSync(url, "utf-8"));
  assert.ok(cases.some((c) => c.parts.some((p) => p.applied === "unknown" && p.crc === "ok")));
  for (const c of cases) {
    const v = { state: { files: c.parts.map((p) => ({ ...p, kind: "spr", file: "/game/x.bundle" })) } };
    assert.equal(appliedOf(v), c.carried, c.name);
  }
});

test("a part's official verdict wins over its applied source; without one, its applied decides", () => {
  // a mod's part it left official, recorded under the mod, and a file of another size than the official one
  const left = { state: { files: [part("spr", "mod:a", { official: true }), part("spr", "official", { official: true })] } };
  assert.equal(appliedOf(left), "official");
  const other = { state: { files: [part("spr", "unknown", { crc: "ok", official: false })] } };
  assert.equal(appliedKey(other), "unknown");
  assert.equal(appliedOf({ state: { files: [part("spr", "mod:a")] } }), "mod:a"); // an older server: no verdict
});

// --- overwritten kinds, alternate lobby cores and the popup while mods are off ---

// a part of another tool's mod with no official copy anywhere (family.get: restorable false)
const overwrittenPart = (kind) => part(kind, "unknown", { official: false, copy: null, restorable: false });

test("candidates keep Original unavailable while mods can replace an overwritten kind", () => {
  const fam = family();
  for (const c of candidates(fam, fam.variants[0], "spr")) {
    assert.equal(c.restorable, true, c.key);
    assert.equal(c.greyed, false, c.key);
  }
  // An outside overwrite disables Original while library mods remain selectable.
  fam.variants[0].state.files = [overwrittenPart("spr")];
  assert.equal(overwrittenOf(fam.variants[0], "spr"), true);
  assert.deepEqual(candidates(fam, fam.variants[0], "spr").filter((c) => c.greyed).map((c) => c.key), ["self"]);
  assert.equal(candidates(fam, fam.variants[0], "spr")[0].restorable, false);
  // Adoption does not change the availability of other library mods.
  fam.variants[0].state.files = [part("spr", "mod:p1", { official: false, copy: null, restorable: false })];
  const list = candidates(fam, fam.variants[0], "spr");
  assert.deepEqual(list.filter((c) => c.greyed).map((c) => c.key), ["self"]);
  // Preview selection is independent of whether the asset can currently be applied.
  assert.equal(popupMode(fam, fam.variants[0], "spr", true).start, "m:p1");
  assert.equal(popupMode(fam, fam.variants[0], "spr", false).start, "self");
  // the lobby is not overwritten: its cards are not greyed
  assert.ok(candidates(fam, fam.variants[0], "lobby").every((c) => !c.greyed));
});

test("another variant's card is greyed on every other popup when its files of that kind are overwritten", () => {
  const fam = family();
  fam.variants[2].state.files = [overwrittenPart("spr")]; // np0045's portrait: no official copy to adapt from
  for (const v of [fam.variants[0], fam.variants[1]]) {
    const card = candidates(fam, v, "spr").find((c) => c.key === "v:np0045");
    assert.equal(card.greyed, true, v.key);
    assert.equal(card.restorable, false, v.key);
    assert.ok(candidates(fam, v, "spr").filter((c) => c.key !== "v:np0045").every((c) => !c.greyed), v.key);
  }
  // a part that is not official but has an official copy (kept, a backup, another folder) greys nothing
  fam.variants[2].state.files = [part("spr", "unknown", { official: false, copy: "backup", restorable: true })];
  assert.equal(candidates(fam, fam.variants[0], "spr").find((c) => c.key === "v:np0045").greyed, false);
  // a part without restorable (an older server) is never taken for overwritten
  assert.equal(overwrittenOf({ state: { files: [part("spr", "unknown")] } }, "spr"), false);
});

test("a lobby popup lists only the lobby mods of its own core", () => {
  const fam = family();
  fam.variants[0].modList.push(
    { ...mod("gl", "hoshino", "lobby"), core: "hoshino_home_gl" },
    { ...mod("own", "hoshino", "lobby"), core: "hoshino_home" });
  const keys = candidates(fam, fam.variants[0], "lobby").map((c) => c.key);
  assert.deepEqual(keys, ["self", "m:l1", "m:own"], "a mod without core is matched by its target, an alternate core's is not offered");
});

test("asset selection always saves the preview independently of the Mods switch", () => {
  const fam = family();
  const v = fam.variants[0];
  // mods on: Apply, from what the game shows
  assert.deepEqual(popupMode(fam, v, "spr", true), { start: "self", button: "popup.save", set: null });
  assert.deepEqual(popupMode(fam, v, "lobby", true), { start: "m:l1", button: "popup.save", set: null });
  // off, with a configured mod: the files are official, the popup starts on the configured card and saves
  v.configured = { spr: "mod:p1", lobby: "variant:hoshino_swimsuit" };
  v.state.files = [part("spr", "official"), part("lobby", "official")];
  assert.equal(configuredKey(v, "spr"), "m:p1");
  assert.deepEqual(popupMode(fam, v, "spr", false), { start: "m:p1", button: "popup.save", set: "m:p1" });
  // a configured source the popup does not offer (another variant's lobby): it starts on the own files
  assert.equal(configuredKey(v, "lobby"), "v:hoshino_swimsuit");
  assert.equal(popupMode(fam, v, "lobby", false).start, "self");
  // nothing configured: the own files, and no Set chip
  v.configured = { spr: null, lobby: null };
  assert.deepEqual(popupMode(fam, v, "spr", false), { start: "self", button: "popup.save", set: null });
  assert.equal(configuredKey({ configured: { spr: "official" } }, "spr"), null);
  assert.equal(configuredKey({}, "spr"), null);
});

test("previews keep both configured kinds while the game files are official", () => {
  const fam = family(), v = fam.variants[0];
  v.configured = { spr: "mod:p1", lobby: "mod:l1" };
  v.state.files = [part("spr", "official"), part("lobby", "official")];
  for (const [kind, id] of [["spr", "p1"], ["lobby", "l1"]]) {
    assert.equal(previewKey(v, kind, false), `m:${id}`);
    assert.equal(previewBase(v, kind, false, fam.variants), `/asset/spine/mod/${id}/skeleton`);
    assert.equal(appliedKey(v, kind), "self", "file status remains independent of the preview");
    assert.equal(previewKey(v, kind, true), `m:${id}`, "the saved source stays visible while installation is pending");
    const marked = candidates(fam, v, kind).filter((c) => c.key === previewKey(v, kind, false));
    assert.deepEqual(marked.map((c) => c.key), [`m:${id}`], "only the configured card matches the preview");
  }
  v.configured.spr = "variant:hoshino_swimsuit";
  assert.equal(previewKey(v, "spr", false), "v:hoshino_swimsuit");
  assert.equal(previewBase(v, "spr", false, fam.variants), "/asset/spine/official-spr/hoshino_swimsuit_spr/skeleton");
  assert.equal(previewBase(v, "spr", true, fam.variants), "/asset/spine/official-spr/hoshino_swimsuit_spr/skeleton");
});

test("a configured variant previews its official copy even when its live files still carry a mod", () => {
  const fam = family(), [v, source] = fam.variants;
  v.configured = { spr: `variant:${source.key}` };
  source.state.files = [part("spr", "mod:leftover", { official: false, restorable: true })];
  assert.equal(appliedKey(source), "m:leftover");
  assert.equal(previewBase(v, "spr", false, fam.variants), "/asset/spine/official-spr/hoshino_swimsuit_spr/skeleton");
  assert.equal(previewBase(source, "spr", true, fam.variants), "/asset/spine/spr/hoshino_swimsuit_spr/skeleton");
});

test("off previews use the configuration even if an unswitched game file still carries a different mod", () => {
  const v = family().variants[0];
  v.configured = { spr: "mod:configured" };
  v.state.files = [part("spr", "mod:carried")];
  assert.equal(previewBase(v, "spr", false), "/asset/spine/mod/configured/skeleton");
  assert.equal(previewKey(v, "spr", true), "m:configured");
  for (const source of [null, "official", "unknown"]) {
    v.configured.spr = source;
    assert.equal(previewKey(v, "spr", false), "self");
  }
  delete v.configured;
  assert.equal(previewKey(v, "spr", false), "self");
});

test("off previews of the own source use verified originals for both kinds despite leftover live mods", () => {
  const fam = family(), v = fam.variants[0];
  for (const [kind, core] of [["spr", v.spr], ["lobby", v.lobby]]) {
    v.state.files = [part(kind, "mod:leftover", { official: false })];
    for (const source of [null, "official", "unknown"]) {
      v.configured = { [kind]: source };
      assert.equal(previewBase(v, kind, false, fam.variants), `/asset/spine/official-${kind}/${core}/skeleton`);
      assert.equal(previewBase(v, kind, true, fam.variants), `/asset/spine/official-${kind}/${core}/skeleton`);
    }
  }
});

test("a saved or removed configuration refreshes the preview without any change to the game files", () => {
  const before = family().variants[0];
  before.configured = { spr: "mod:p1", lobby: "mod:l1" };
  const after = structuredClone(before);
  after.configured.spr = "mod:p2";
  assert.equal(previewChanged(before, after, "spr", false, false), true);
  assert.equal(previewChanged(before, after, "lobby", false, false), false);
  after.configured.spr = null;
  assert.equal(previewChanged(before, after, "spr", false, false), true, "removing the configured mod reloads the player");
  assert.equal(previewChanged(before, before, "spr", true, false), true, "a toggle refreshes even an unswitched entry");
  assert.equal(previewChanged(before, after, "spr", true, true), true, "with mods on the saved choice still decides");
});


test("model sources stay within the variant and use the shared configured/applied rules", () => {
  const fam = family(), v = fam.variants[0];
  v.model = "hoshino_original";
  v.state.files.push(part("model", "mod:model-a", { official: false, restorable: true }));
  v.modList.push({ ...mod("model-a", v.key, "model"), core: v.model });
  fam.variants[1].modList.push(mod("foreign", fam.variants[1].key, "model"));
  v.modList.push({ ...mod("wrong-core", v.key, "model"), core: "alternate" });
  assert.deepEqual(candidates(fam, v, "model").map((c) => c.key), ["self", "m:model-a"]);
  assert.equal(coreOf(v, "model"), "hoshino_original");
  assert.equal(appliedKey(v, "model"), "m:model-a");
  assert.equal(modelSource(v, true), "live");
  assert.equal(previewBase(v, "model", true), "/asset/model/hoshino.glb");
  v.configured = { model: "mod:model-a" };
  v.state.files = [part("model", "official")];
  assert.equal(modelSource(v, false), "mod:model-a");
  assert.equal(previewBase(v, "model", false), "/asset/model/hoshino.glb?source=mod%3Amodel-a");
  assert.deepEqual(popupMode(fam, v, "model", false), { start: "m:model-a", button: "popup.save", set: "m:model-a" });
  assert.equal(appliedKey(v, "model"), "self");
  assert.equal(modelUrl("a/b", "official"), "/asset/model/a%2Fb.glb?source=official");
  v.configured = {};
  assert.equal(modelSource(v, false), "official");
});

test("the 3D model offers the student's other models as costumes, field-mode models included", () => {
  const fam = family(), [v, swimsuit, np] = fam.variants;
  v.model = "hoshino_original";
  swimsuit.model = "hoshino_swimsuit";
  swimsuit.modList.push(mod("swim-model", swimsuit.key, "model"));  // another variant's mod: never a costume
  np.fieldModel = "np0045";  // an event costume with only a field-mode model
  const ok = { hoshino: "ok", hoshino_swimsuit: "ok", np0045: "ok" };
  assert.deepEqual(costumeCandidates(fam, v).map((o) => o.key), ["hoshino_swimsuit", "np0045"]);
  assert.deepEqual(candidates(fam, v, "model", ok).map((c) => c.key), ["self", "v:hoshino_swimsuit", "v:np0045"]);
  assert.deepEqual(candidates(fam, swimsuit, "model", ok).map((c) => c.key), ["self", "m:swim-model", "v:hoshino", "v:np0045"]);
  // BL-136: every pair is listed at once; one not checked yet or refused is listed for a preview only, and a refused
  // one cannot be chosen as the preview either
  const shape = (c) => [c.key, !!c.previewOnly, !!c.refused];
  assert.deepEqual(candidates(fam, v, "model").map(shape),
    [["self", false, false], ["v:hoshino_swimsuit", true, false], ["v:np0045", true, false]]);
  assert.deepEqual(candidates(fam, v, "model", { hoshino_swimsuit: "refused", np0045: null }).map(shape),
    [["self", false, false], ["v:hoshino_swimsuit", true, true], ["v:np0045", true, false]]);
  assert.deepEqual(candidates(fam, v, "model", { hoshino_swimsuit: "refused", np0045: "ok" }).map(shape),
    [["self", false, false], ["v:hoshino_swimsuit", true, true], ["v:np0045", false, false]]);
  // a pair past the stretch limit is listed for a preview only
  assert.deepEqual(candidates(fam, v, "model", { hoshino_swimsuit: "preview", np0045: "ok" }).map(shape),
    [["self", false, false], ["v:hoshino_swimsuit", true, false], ["v:np0045", false, false]]);
  // the popup starts on the saved preview before any verdict arrives
  v.previewSources = { model: "variant:np0045" };
  assert.equal(popupMode(fam, v, "model").start, "v:np0045");
  delete v.previewSources;
  // the field-only variant shows a 3D tab, with no slot to switch
  assert.equal(hasKind(np, "model"), true);
  assert.equal(availableTab(np, "model"), "model");
  assert.deepEqual(candidates(fam, np, "model").map((c) => [c.key, c.greyed]), [["self", true]]);
  // a costume previews and applies as variant:<key>
  v.previewSources = { model: "variant:np0045" };
  assert.equal(modelSource(v, true), "variant:np0045");
  assert.equal(previewBase(v, "model", true), "/asset/model/hoshino.glb?source=variant%3Anp0045");
  assert.equal(sourceOf("v:np0045"), "variant:np0045");
  // the portrait keeps its own rules: other variants' portrait mods, never models
  assert.ok(candidates(fam, v, "spr").every((c) => c.kind !== "variant" || c.variant.spr));
});

test("other students' models stay out of the normal list and retain saved cross choices", () => {
  const fam = family(), v = fam.variants[0];
  fam.variants.forEach((o) => { o.family = fam.key; }); v.model = "hoshino_original";
  fam.variants[1].model = "hoshino_swimsuit";
  const npc = { key: "ch0171", family: "yukino", model: "ch0171_enemy", playable: false };
  const field = { key: "field", family: "yukino", fieldModel: "ch0171_field" };
  const roster = [fam, { key: "yukino", variants: [npc, field, { key: "portrait", family: "yukino", spr: "portrait" }] }];
  const verdicts = { hoshino_swimsuit: "ok", ch0171: "preview" };
  assert.deepEqual(candidates(fam, v, "model", verdicts, roster), candidates(fam, v, "model", verdicts));
  assert.deepEqual(crossModels(roster, v).map((o) => o.key), ["ch0171", "field"]);
  assert.deepEqual(crossModels(roster, field), [], "field-only models have no destination slot");
  for (const property of ["configured", "previewSources"]) {
    v[property] = { model: "variant:ch0171" };
    const choices = candidates(fam, v, "model", verdicts, roster);
    assert.deepEqual(choices.map((c) => c.key), ["self", "v:hoshino_swimsuit", "v:ch0171"]);
    assert.equal(choices.at(-1).cross, true);
    assert.equal(choices.at(-1).previewOnly, undefined);
    assert.equal(popupMode(fam, v, "model", true, roster).start, "v:ch0171");
    assert.equal(modelSource(v), "variant:ch0171");
    assert.deepEqual(candidates(fam, v, "spr", verdicts, roster), candidates(fam, v, "spr", verdicts));
    assert.deepEqual(candidates(fam, v, "lobby", verdicts, roster), candidates(fam, v, "lobby", verdicts));
    delete v[property];
  }
});

test("cross model hits are ordered and capped, flag pending cards, and omit hard refusals", () => {
  const v = { key: "slot", family: "own", model: "slot" };
  const variants = Array.from({ length: 70 }, (_, i) => ({ key: `npc${i}`, family: "other", fieldModel: `ch${i}` }));
  const roster = [{ variants }];
  const hits = crossCandidates(roster, v, { npc0: "refused", npc1: "preview", npc2: "ok" }, {
    compare: (a, b) => Number(a.key.slice(3)) - Number(b.key.slice(3)), exclude: ["v:npc3"],
  });
  assert.equal(hits.length, 64);
  assert.deepEqual(hits.slice(0, 3).map((c) => c.key), ["v:npc1", "v:npc2", "v:npc4"]);
  assert.equal(hits.at(-1).key, "v:npc65");
  assert.ok(hits.every((c) => c.cross && !c.greyed));
  assert.ok(hits.slice(0, 2).every((c) => !c.pending && !c.previewOnly));
  assert.ok(hits.slice(2).every((c) => c.pending));
  assert.deepEqual(crossCandidates(roster, v, null, { match: (o) => o.key === "npc69" }).map((c) => c.key), ["v:npc69"]);
});

test("every other student's model is listed without the 64 cap, in the same order, when asked for", () => {
  const v = { key: "slot", family: "own", model: "slot" };
  const variants = Array.from({ length: 200 }, (_, i) => ({ key: `npc${i}`, family: "other", fieldModel: `ch${i}` }));
  const roster = [{ variants }], compare = (a, b) => Number(a.key.slice(3)) - Number(b.key.slice(3));
  const verdicts = { npc0: "refused", npc5: "preview" };
  const capped = crossCandidates(roster, v, verdicts, { compare });
  assert.equal(capped.length, 64, "a search's hits stop at what one RPC call takes");
  const all = crossCandidates(roster, v, verdicts, { compare, limit: Infinity });
  assert.equal(all.length, 199, "every other model but the refused one");
  assert.deepEqual(all.slice(0, 64).map((c) => c.key), capped.map((c) => c.key), "the first 64 are the capped list");
  assert.deepEqual(all.map((c) => c.key), all.map((c) => c.key).sort((a, b) => Number(a.slice(5)) - Number(b.slice(5))));
  assert.ok(all.every((c) => c.cross && !c.greyed) && all.find((c) => c.key === "v:npc5").pending === undefined);
  // the student's own variants and models of the same student are never among them
  assert.deepEqual(crossCandidates([{ variants: [{ key: "mine", family: "own", model: "mine" }, ...variants.slice(0, 2)] }], v,
    null, { limit: Infinity }).map((c) => c.key), ["v:npc0", "v:npc1"]);
});

test("Show other students is off at every start and is kept only in memory", async () => {
  const fresh = await import("../../src/bmpt/web/js/switch-sources.js?fresh-start");
  assert.equal(showOthers(), false, "off by default");
  assert.equal(fresh.showOthers(), false);
  setShowOthers(1);
  assert.equal(showOthers(), true, "kept across students and popup openings");
  assert.equal(fresh.showOthers(), false, "a page that starts again begins unchecked");
  setShowOthers(false);
  assert.equal(showOthers(), false);
  // nothing writes it down: no storage, no settings, no library call mentions it
  for (const file of ["switch-sources.js", "detail.js"]) {
    const lines = readFileSync(new URL(`../../src/bmpt/web/js/${file}`, import.meta.url), "utf8").split("\n");
    for (const line of lines.filter((l) => /showOthers|ShowOthers/.test(l))) assert.ok(!/localStorage|sessionStorage|settings|rpc\(|fetch\(/.test(line), `${file}: ${line}`);
  }
});

test("a card of another student's model carries the caution and its ids, from a search or from the checkbox", () => {
  const v = { key: "slot", family: "own", model: "slot" };
  const roster = [{ variants: [{ key: "a", family: "other", model: "ch1", fileId: "f1" }, { key: "b", family: "other", fieldModel: "np2" }] }];
  for (const cards of [crossCandidates(roster, v, null, { match: (o) => o.key === "a" }), crossCandidates(roster, v, null, { limit: Infinity })]) {
    assert.ok(cards.length > 0);
    for (const card of cards) {
      const extras = crossExtras(card, "careful");
      assert.equal(extras.hint, "careful");
      assert.deepEqual(extras.terms, [card.variant.fileId, card.variant.model, card.variant.fieldModel]);
    }
  }
  assert.deepEqual(crossExtras({ key: "v:sibling", kind: "variant" }, "careful"), {}, "the student's own costumes carry none");
  assert.deepEqual(crossExtras({ key: "self", kind: "self" }, "careful"), {});
});

test("cross preview verdicts allow Apply while same-student previews and hard refusals stay blocked", () => {
  const v = { key: "slot", family: "own", model: "slot" };
  const fam = { variants: [v, { key: "sibling", family: "own", model: "sibling" }] };
  for (const [verdict, ownHold, crossHold] of [["ok", null, null], ["preview", "unfit", null],
    ["refused", "unfit", "unfit"], [null, null, "pending"]]) {
    assert.equal(costumeHold(fam, v, "v:sibling", { sibling: verdict }), ownHold);
    assert.equal(costumeHold(fam, v, "v:cross", { cross: verdict }), crossHold);
  }
  assert.equal(costumeHold(fam, v, "self", {}), null);
  assert.equal(costumeHold(fam, v, "m:mod", {}), null);
});

test("split model slots offer dependency mods and judge every slot part", async () => {
  const { selectionStatus, candidateStatus } = await import("../../src/bmpt/web/js/switch-sources.js");
  const dependency = { id: "dependency", kind: "model", core: "ch0258", target: "ch0258" };
  const v = { key: "ch0258", model: "ch0258_02", modelCores: ["ch0258_02", "ch0258"],
    configured: { model: "mod:dependency" },
    state: { files: [part("model", "official", { core: "ch0258_02" }),
      part("model", "mod:dependency", { core: "ch0258", official: false, restorable: false })] },
    modList: [dependency, { ...dependency, id: "enemy", core: "ch0258_01_enemy" },
      { ...dependency, id: "shared", core: "droid_kaiserpmc_shield" },
      { ...dependency, id: "foreign", core: "hoshino_original" }] };
  const fam = { variants: [v, { key: "hoshino", model: "hoshino_original", modList: [
    { ...dependency, id: "other", core: "hoshino_original", target: "hoshino" }] }] };
  // other cores' mods stay out; the other variant's model is listed as a costume, applicable once checked
  assert.deepEqual(candidates(fam, v, "model", { hoshino: "ok" }).map((c) => [c.key, !!c.previewOnly]),
    [["self", false], ["m:dependency", false], ["v:hoshino", false]]);
  assert.deepEqual(candidates(fam, v, "model").map((c) => [c.key, !!c.previewOnly]),
    [["self", false], ["m:dependency", false], ["v:hoshino", true]]);
  assert.equal(candidates(fam, v, "model")[0].greyed, true);
  assert.equal(appliedKey(v, "model"), "m:dependency");
  assert.equal(selectionStatus(v, "model"), "status.applied");
  dependency.incomplete = true;
  assert.equal(selectionStatus(v, "model"), "mods.incomplete");
  assert.equal(candidateStatus(v, "model", "m:dependency"), "mods.incomplete");
});

test("Applied follows game intent independently of physical writes and preview selection", async () => {
  const { selectionStatus, candidateStatus, detailStatus, selectionError, importStatus } = await import("../../src/bmpt/web/js/switch-sources.js");
  for (const kind of ["spr", "lobby", "model"]) {
    const v = { key: "a", spr: "a_spr", lobby: "a_home", model: "a", configured: { [kind]: "mod:chosen" },
      state: { files: [part(kind, "mod:old")] }, selection: {} };
    for (const installed of [false, true]) {
      const error = installed ? null : { code: "game_file_locked", params: { file: "bundle" } };
      v.selection[kind] = { source: "mod:chosen", installed, error };
      const expected = "status.applied";
      assert.equal(selectionStatus(v, kind), expected, "roster and switch card");
      assert.equal(detailStatus(v, kind), expected, "detail Info");
      assert.equal(candidateStatus(v, kind, "m:chosen"), expected, "popup Status and chip");
      assert.equal(candidateStatus(v, kind, "m:old"), null);
      assert.equal(candidateStatus(v, kind, "self"), null);
      assert.deepEqual(selectionError(v, kind), error);
      assert.equal(importStatus({ applied: installed, configured: true }), expected, "import note");
      for (const enabled of [false, true]) assert.equal(previewKey(v, kind, enabled), "m:chosen");
    }
    v.selection[kind].source = "mod:old";
    assert.equal(selectionStatus(v, kind), "status.applied", "physical installation verdicts do not change game intent");
  }
});

test("saved previews and game intent remain independent for every asset kind", async () => {
  const { intendedKey, isApplied, selectionStatus } = await import("../../src/bmpt/web/js/switch-sources.js");
  for (const kind of ["spr", "lobby", "model"]) {
    const v = { key: "a", configured: { [kind]: "mod:game" }, previewSources: { [kind]: "mod:preview" } };
    for (const enabled of [false, true]) {
      assert.equal(previewKey(v, kind, enabled), "m:preview");
      assert.equal(intendedKey(v, kind), "m:game");
      assert.equal(selectionStatus(v, kind), null);
      assert.equal(isApplied(v, kind, "m:game"), true);
      assert.equal(isApplied(v, kind, "m:preview"), false);
    }
    v.previewSources[kind] = "official";
    assert.equal(previewKey(v, kind), "self", "explicit original preview does not fall back to an applied Mod");
    v.configured[kind] = null;
    assert.equal(selectionStatus(v, kind), "status.applied");
  }
});
