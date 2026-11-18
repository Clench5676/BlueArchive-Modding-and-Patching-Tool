// Tests for web/js/mods/switch-sources.js (what the detail screen's switch cards offer and what the game shows), run by
// tests/test_switch_sources.py with Node's test runner: node --test tests/js/switch_sources.test.mjs
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

import {
  modelSource,
  modelUrl,
  appliedKey,
  appliedOf,
  availableTab,
  candidates,
  configuredKey,
  coreOf,
  costumeCandidates,
  costumeHold,
  crossCandidates,
  crossExtras,
  crossModels,
  hasAsset,
  hasKind,
  overwrittenOf,
  partsOf,
  popupMode,
  primaryKind,
  previewBase,
  previewChanged,
  previewKey,
  setShowOthers,
  showOthers,
  sourceOf,
  switchKindOf,
} from "../../src/bmpt/web/js/mods/switch-sources.js";

const part = (kind, applied, extra = {}) => ({
  file: `/game/${kind}-${applied}.bundle`,
  resourceType: kind,
  applied,
  crc: "ok",
  ...extra,
});
const mod = (id, target, kind) => ({
  id,
  targetAlt: target,
  resourceType: kind,
  name: id,
  file: `/library/mods/${id}/${id}.bundle`,
  fresh: false,
});
// hoshino: a portrait and a lobby; swimsuit: a portrait and a lobby of its own; np: a portrait only; shared: the same
// lobby core as hoshino
const student = () => ({
  key: "hoshino",
  alts: [
    {
      key: "hoshino",
      portrait: "hoshino_spr",
      memorialLobby: "hoshino_home",
      state: { files: [part("portrait", "official"), part("memorialLobby", "mod:l1")] },
      modList: [mod("p1", "hoshino", "portrait"), mod("l1", "hoshino", "memorialLobby")],
    },
    {
      key: "hoshino_swimsuit",
      portrait: "hoshino_swimsuit_spr",
      memorialLobby: "hoshino_swimsuit_home",
      state: { files: [part("portrait", "alt:hoshino"), part("memorialLobby", "official")] },
      modList: [mod("p2", "hoshino_swimsuit", "portrait"), mod("l2", "hoshino_swimsuit", "memorialLobby")],
    },
    {
      key: "np0045",
      portrait: "np0045_spr",
      memorialLobby: null,
      state: { files: [part("portrait", "official")] },
      modList: [],
    },
    {
      key: "hoshino_shared",
      portrait: "hoshino_shared_spr",
      memorialLobby: "hoshino_home",
      state: { files: [] },
      modList: [],
    },
  ],
});

test("stored-only character sources are previewable without an official install target", () => {
  const student = {
    key: "future",
    portrait: null,
    memorialLobby: null,
    chibi: null,
    storedKinds: ["memorialLobby"],
    localResourceTypes: [],
    state: { files: [] },
    configured: {},
    previewSources: { memorialLobby: "mod:future-mod" },
    modList: [mod("future-mod", "future", "memorialLobby")],
  };
  const shownStudent = { alts: [student] };
  assert.ok(hasAsset(student));
  assert.equal(primaryKind(student), "memorialLobby");
  assert.equal(availableTab(student, "portrait"), "lobby");
  assert.equal(coreOf(student, "memorialLobby"), null);
  assert.equal(previewBase(student, "memorialLobby", false), "/asset/spine/mod/future-mod/skeleton");
  const choices = candidates(shownStudent, student, "memorialLobby");
  assert.equal(choices.find((choice) => choice.key === "self").greyed, true);
  assert.equal(choices.find((choice) => choice.kind === "mod").greyed, false);
  assert.equal(configuredKey(student, "memorialLobby"), null);
  const modelOnly = { ...student, storedKinds: ["chibi"], previewSources: { chibi: "mod:future-model" } };
  assert.ok(hasAsset(modelOnly));
  assert.equal(primaryKind(modelOnly), "chibi");
  assert.equal(availableTab(modelOnly, "portrait"), "model");
  assert.equal(modelSource(modelOnly), "mod:future-model");
});

test("stored-only portraits offer their own Mods without sibling adaptation sources", () => {
  const student = {
    key: "future",
    portrait: null,
    memorialLobby: null,
    storedKinds: ["portrait"],
    localResourceTypes: [],
    state: { files: [] },
    modList: [mod("own", "future", "portrait")],
  };
  const sibling = {
    key: "released",
    portrait: "released_spr",
    state: { files: [] },
    modList: [mod("sibling", "released", "portrait")],
  };
  const choices = candidates({ alts: [student, sibling] }, student, "portrait");
  assert.deepEqual(
    choices.map((choice) => choice.key),
    ["self", "m:own"],
  );
  assert.equal(choices[0].greyed, true);
  assert.equal(choices[1].greyed, false);
});

test("special slots open their lobby tab and switch only mods for their own core", () => {
  const day = {
    key: "special:arona_workpage_daytime",
    portrait: null,
    memorialLobby: "arona_workpage_daytime",
    spineCategory: "spinebackground",
    state: { files: [part("memorialLobby", "official")] },
    configured: { memorialLobby: "mod:day" },
  };
  const night = { ...day, key: "special:arona_workpage_night", memorialLobby: "arona_workpage_night" };
  day.modList = [{ ...mod("day", day.key, "memorialLobby"), core: day.memorialLobby }];
  night.modList = [{ ...mod("night", night.key, "memorialLobby"), core: night.memorialLobby }];
  const shownStudent = { alts: [day, night, ...student().alts] };
  assert.ok(hasAsset(day));
  assert.equal(primaryKind(day), "memorialLobby");
  for (const tab of ["portrait", "model", "lobby"]) assert.equal(availableTab(day, tab), "lobby");
  assert.equal(availableTab(shownStudent.alts[2], "portrait"), "portrait");
  assert.equal(availableTab(shownStudent.alts[4], "lobby"), "portrait");
  assert.deepEqual(
    candidates(shownStudent, day, "memorialLobby").map((c) => c.key),
    ["self", "m:day"],
  );
  assert.equal(previewBase(day, "memorialLobby", true), "/asset/spine/mod/day/skeleton");
  assert.equal(previewBase(day, "memorialLobby", false), "/asset/spine/mod/day/skeleton");
  assert.deepEqual(popupMode(shownStudent, day, "memorialLobby", false), {
    start: "m:day",
    button: "popup.save",
    set: "m:day",
  });
  day.state.files = [part("memorialLobby", "mod:day")];
  assert.equal(popupMode(shownStudent, day, "memorialLobby", true).start, "m:day");
  assert.equal(sourceOf("self"), "official");
});

test("candidates of the portrait: own, own mods, other alts, their mods", () => {
  const shownStudent = student();
  const list = candidates(shownStudent, shownStudent.alts[0], "portrait");
  assert.deepEqual(
    list.map((c) => c.key),
    ["self", "m:p1", "v:hoshino_swimsuit", "v:np0045", "v:hoshino_shared", "m:p2"],
  );
  assert.equal(list[0].core, "hoshino_spr");
  assert.equal(list[2].core, "hoshino_swimsuit_spr");
  assert.equal(list.at(-1).origin.key, "hoshino_swimsuit");
});

test("candidates of the memorial lobby: the alt's own lobby and its lobby mods only", () => {
  const shownStudent = student();
  const list = candidates(shownStudent, shownStudent.alts[0], "memorialLobby");
  // no portrait mods, and neither another alt's lobby nor its lobby mods (untested under this alt's prefab)
  assert.deepEqual(
    list.map((c) => c.key),
    ["self", "m:l1"],
  );
  assert.equal(list[0].core, "hoshino_home");
  assert.ok(list.every((c) => c.kind !== "mod" || c.mod.resourceType === "memorialLobby"));
  assert.deepEqual(
    candidates(shownStudent, shownStudent.alts[1], "memorialLobby").map((c) => c.key),
    ["self", "m:l2"],
  );
  assert.deepEqual(
    candidates(shownStudent, shownStudent.alts[2], "memorialLobby").map((c) => c.key),
    ["self"],
  );
});

test("what the game shows, per kind", () => {
  const [hoshino, swimsuit, np] = student().alts;
  assert.equal(appliedOf(hoshino, "portrait"), "official");
  assert.equal(appliedKey(hoshino, "portrait"), "self");
  assert.equal(appliedKey(hoshino, "memorialLobby"), "m:l1");
  assert.equal(appliedKey(swimsuit, "portrait"), "v:hoshino");
  assert.equal(appliedKey(swimsuit, "memorialLobby"), "self");
  assert.equal(appliedKey(np, "memorialLobby"), "self"); // no lobby files: nothing replaced
});

test("parts with several sources, or changed outside the library, are unknown", () => {
  const v = { state: { files: [part("portrait", "mod:a"), part("portrait", "mod:b")] } };
  assert.equal(appliedOf(v), "unknown");
  assert.equal(appliedKey(v), "unknown");
  const outside = { state: { files: [part("memorialLobby", "unknown"), part("memorialLobby", "official")] } };
  assert.equal(appliedKey(outside, "memorialLobby"), "unknown");
  // one modded part among official ones: the mod
  assert.equal(appliedKey({ state: { files: [part("portrait", "official"), part("portrait", "mod:a")] } }), "m:a");
});

test("sources for actions.apply", () => {
  assert.equal(sourceOf("self"), "official");
  assert.equal(sourceOf("m:abc"), "mod:abc");
  assert.equal(sourceOf("v:hoshino_swimsuit"), "alt:hoshino_swimsuit");
});

test("parts and cores of a kind", () => {
  const v = {
    portrait: "a_spr",
    memorialLobby: null,
    chibiFiles: [{ file: "m" }],
    state: { files: [part("portrait", "official"), part("memorialLobby", "official")] },
  };
  assert.equal(partsOf(v, "portrait").length, 1);
  assert.equal(partsOf(v, "chibi")[0].file, "m");
  assert.equal(coreOf(v, "portrait"), "a_spr");
  assert.equal(coreOf(v, "memorialLobby"), null);
});

test("the switch card uses the asset kind of every tab", () => {
  assert.equal(switchKindOf("portrait"), "portrait");
  assert.equal(switchKindOf("lobby"), "memorialLobby");
  assert.equal(switchKindOf("model"), "chibi");
});

test("what the game shows follows the library's source rule, part by part", () => {
  const url = new URL("../data/source_rule_cases.json", import.meta.url);
  const { cases } = JSON.parse(readFileSync(url, "utf-8"));
  assert.ok(cases.some((c) => c.parts.some((p) => p.applied === "unknown" && p.crc === "ok")));
  for (const c of cases) {
    const v = { state: { files: c.parts.map((p) => ({ ...p, resourceType: "portrait", file: "/game/x.bundle" })) } };
    assert.equal(appliedOf(v), c.carried, c.name);
  }
});

test("a part's official verdict wins over its applied source; without one, its applied decides", () => {
  // a mod's part it left official, recorded under the mod, and a file of another size than the official one
  const left = {
    state: { files: [part("portrait", "mod:a", { official: true }), part("portrait", "official", { official: true })] },
  };
  assert.equal(appliedOf(left), "official");
  const other = { state: { files: [part("portrait", "unknown", { crc: "ok", official: false })] } };
  assert.equal(appliedKey(other), "unknown");
  assert.equal(appliedOf({ state: { files: [part("portrait", "mod:a")] } }), "mod:a"); // an older server: no verdict
});

// --- overwritten kinds, alternate lobby cores and the popup while mods are off ---

// a part of another tool's mod with no official copy anywhere (student.get: restorable false)
const overwrittenPart = (kind) => part(kind, "unknown", { official: false, copy: null, restorable: false });

test("candidates keep Original unavailable while mods can replace an overwritten kind", () => {
  const shownStudent = student();
  for (const c of candidates(shownStudent, shownStudent.alts[0], "portrait")) {
    assert.equal(c.restorable, true, c.key);
    assert.equal(c.greyed, false, c.key);
  }
  // An outside overwrite disables Original while library mods remain selectable.
  shownStudent.alts[0].state.files = [overwrittenPart("portrait")];
  assert.equal(overwrittenOf(shownStudent.alts[0], "portrait"), true);
  assert.deepEqual(
    candidates(shownStudent, shownStudent.alts[0], "portrait")
      .filter((c) => c.greyed)
      .map((c) => c.key),
    ["self"],
  );
  assert.equal(candidates(shownStudent, shownStudent.alts[0], "portrait")[0].restorable, false);
  // Adoption does not change the availability of other library mods.
  shownStudent.alts[0].state.files = [part("portrait", "mod:p1", { official: false, copy: null, restorable: false })];
  const list = candidates(shownStudent, shownStudent.alts[0], "portrait");
  assert.deepEqual(
    list.filter((c) => c.greyed).map((c) => c.key),
    ["self"],
  );
  // Preview selection is independent of whether the asset can currently be applied.
  assert.equal(popupMode(shownStudent, shownStudent.alts[0], "portrait", true).start, "m:p1");
  assert.equal(popupMode(shownStudent, shownStudent.alts[0], "portrait", false).start, "self");
  // the lobby is not overwritten: its cards are not greyed
  assert.ok(candidates(shownStudent, shownStudent.alts[0], "memorialLobby").every((c) => !c.greyed));
});

test("another alt's card is greyed on every other popup when its files of that kind are overwritten", () => {
  const shownStudent = student();
  shownStudent.alts[2].state.files = [overwrittenPart("portrait")]; // np0045's portrait: no official copy to adapt from
  for (const v of [shownStudent.alts[0], shownStudent.alts[1]]) {
    const card = candidates(shownStudent, v, "portrait").find((c) => c.key === "v:np0045");
    assert.equal(card.greyed, true, v.key);
    assert.equal(card.restorable, false, v.key);
    assert.ok(
      candidates(shownStudent, v, "portrait")
        .filter((c) => c.key !== "v:np0045")
        .every((c) => !c.greyed),
      v.key,
    );
  }
  // a part that is not official but has an official copy (kept, a backup, another folder) greys nothing
  shownStudent.alts[2].state.files = [
    part("portrait", "unknown", { official: false, copy: "backup", restorable: true }),
  ];
  assert.equal(
    candidates(shownStudent, shownStudent.alts[0], "portrait").find((c) => c.key === "v:np0045").greyed,
    false,
  );
  // a part without restorable (an older server) is never taken for overwritten
  assert.equal(overwrittenOf({ state: { files: [part("portrait", "unknown")] } }, "portrait"), false);
});

test("a lobby popup lists only the lobby mods of its own core", () => {
  const shownStudent = student();
  shownStudent.alts[0].modList.push(
    { ...mod("gl", "hoshino", "memorialLobby"), core: "hoshino_home_gl" },
    { ...mod("own", "hoshino", "memorialLobby"), core: "hoshino_home" },
  );
  const keys = candidates(shownStudent, shownStudent.alts[0], "memorialLobby").map((c) => c.key);
  assert.deepEqual(
    keys,
    ["self", "m:l1", "m:own"],
    "a mod without core is matched by its target, an alternate core's is not offered",
  );
});

test("asset selection always saves the preview independently of the Mods switch", () => {
  const shownStudent = student();
  const v = shownStudent.alts[0];
  // mods on: Apply, from what the game shows
  assert.deepEqual(popupMode(shownStudent, v, "portrait", true), { start: "self", button: "popup.save", set: null });
  assert.deepEqual(popupMode(shownStudent, v, "memorialLobby", true), {
    start: "m:l1",
    button: "popup.save",
    set: null,
  });
  // off, with a configured mod: the files are official, the popup starts on the configured card and saves
  v.configured = { portrait: "mod:p1", memorialLobby: "alt:hoshino_swimsuit" };
  v.state.files = [part("portrait", "official"), part("memorialLobby", "official")];
  assert.equal(configuredKey(v, "portrait"), "m:p1");
  assert.deepEqual(popupMode(shownStudent, v, "portrait", false), { start: "m:p1", button: "popup.save", set: "m:p1" });
  // a configured source the popup does not offer (another alt's lobby): it starts on the own files
  assert.equal(configuredKey(v, "memorialLobby"), "v:hoshino_swimsuit");
  assert.equal(popupMode(shownStudent, v, "memorialLobby", false).start, "self");
  // nothing configured: the own files, and no Set chip
  v.configured = { portrait: null, memorialLobby: null };
  assert.deepEqual(popupMode(shownStudent, v, "portrait", false), { start: "self", button: "popup.save", set: null });
  assert.equal(configuredKey({ configured: { portrait: "official" } }, "portrait"), null);
  assert.equal(configuredKey({}, "portrait"), null);
});

test("previews keep both configured kinds while the game files are official", () => {
  const shownStudent = student(),
    v = shownStudent.alts[0];
  v.configured = { portrait: "mod:p1", memorialLobby: "mod:l1" };
  v.state.files = [part("portrait", "official"), part("memorialLobby", "official")];
  for (const [kind, id] of [
    ["portrait", "p1"],
    ["memorialLobby", "l1"],
  ]) {
    assert.equal(previewKey(v, kind, false), `m:${id}`);
    assert.equal(previewBase(v, kind, false, shownStudent.alts), `/asset/spine/mod/${id}/skeleton`);
    assert.equal(appliedKey(v, kind), "self", "file status remains independent of the preview");
    assert.equal(previewKey(v, kind, true), `m:${id}`, "the saved source stays visible while installation is pending");
    const marked = candidates(shownStudent, v, kind).filter((c) => c.key === previewKey(v, kind, false));
    assert.deepEqual(
      marked.map((c) => c.key),
      [`m:${id}`],
      "only the configured card matches the preview",
    );
  }
  v.configured.portrait = "alt:hoshino_swimsuit";
  assert.equal(previewKey(v, "portrait", false), "v:hoshino_swimsuit");
  assert.equal(
    previewBase(v, "portrait", false, shownStudent.alts),
    "/asset/spine/official-portrait/hoshino_swimsuit_spr/skeleton",
  );
  assert.equal(
    previewBase(v, "portrait", true, shownStudent.alts),
    "/asset/spine/official-portrait/hoshino_swimsuit_spr/skeleton",
  );
});

test("a configured alt previews its official copy even when its live files still carry a mod", () => {
  const shownStudent = student(),
    [v, source] = shownStudent.alts;
  v.configured = { portrait: `alt:${source.key}` };
  source.state.files = [part("portrait", "mod:leftover", { official: false, restorable: true })];
  assert.equal(appliedKey(source), "m:leftover");
  assert.equal(
    previewBase(v, "portrait", false, shownStudent.alts),
    "/asset/spine/official-portrait/hoshino_swimsuit_spr/skeleton",
  );
  assert.equal(
    previewBase(source, "portrait", true, shownStudent.alts),
    "/asset/spine/portrait/hoshino_swimsuit_spr/skeleton",
  );
});

test("off previews use the configuration even if an unswitched game file still carries a different mod", () => {
  const v = student().alts[0];
  v.configured = { portrait: "mod:configured" };
  v.state.files = [part("portrait", "mod:carried")];
  assert.equal(previewBase(v, "portrait", false), "/asset/spine/mod/configured/skeleton");
  assert.equal(previewKey(v, "portrait", true), "m:configured");
  for (const source of [null, "official", "unknown"]) {
    v.configured.portrait = source;
    assert.equal(previewKey(v, "portrait", false), "self");
  }
  delete v.configured;
  assert.equal(previewKey(v, "portrait", false), "self");
});

test("off previews of the own source use verified originals for both kinds despite leftover live mods", () => {
  const shownStudent = student(),
    v = shownStudent.alts[0];
  for (const [kind, core] of [
    ["portrait", v.portrait],
    ["memorialLobby", v.memorialLobby],
  ]) {
    v.state.files = [part(kind, "mod:leftover", { official: false })];
    for (const source of [null, "official", "unknown"]) {
      v.configured = { [kind]: source };
      assert.equal(previewBase(v, kind, false, shownStudent.alts), `/asset/spine/official-${kind}/${core}/skeleton`);
      assert.equal(previewBase(v, kind, true, shownStudent.alts), `/asset/spine/official-${kind}/${core}/skeleton`);
    }
  }
});

test("a saved or removed configuration refreshes the preview without any change to the game files", () => {
  const before = student().alts[0];
  before.configured = { portrait: "mod:p1", memorialLobby: "mod:l1" };
  const after = structuredClone(before);
  after.configured.portrait = "mod:p2";
  assert.equal(previewChanged(before, after, "portrait", false, false), true);
  assert.equal(previewChanged(before, after, "memorialLobby", false, false), false);
  after.configured.portrait = null;
  assert.equal(
    previewChanged(before, after, "portrait", false, false),
    true,
    "removing the configured mod reloads the player",
  );
  assert.equal(
    previewChanged(before, before, "portrait", true, false),
    true,
    "a toggle refreshes even an unswitched entry",
  );
  assert.equal(
    previewChanged(before, after, "portrait", true, true),
    true,
    "with mods on the saved choice still decides",
  );
});

test("model sources stay within the alt and use the shared configured/applied rules", () => {
  const shownStudent = student(),
    v = shownStudent.alts[0];
  v.chibi = "hoshino_original";
  v.state.files.push(part("chibi", "mod:model-a", { official: false, restorable: true }));
  v.modList.push({ ...mod("model-a", v.key, "chibi"), core: v.chibi });
  shownStudent.alts[1].modList.push(mod("foreign", shownStudent.alts[1].key, "chibi"));
  v.modList.push({ ...mod("wrong-core", v.key, "chibi"), core: "alternate" });
  assert.deepEqual(
    candidates(shownStudent, v, "chibi").map((c) => c.key),
    ["self", "m:model-a"],
  );
  assert.equal(coreOf(v, "chibi"), "hoshino_original");
  assert.equal(appliedKey(v, "chibi"), "m:model-a");
  assert.equal(modelSource(v, true), "live");
  assert.equal(previewBase(v, "chibi", true), "/asset/chibi/hoshino.glb");
  v.configured = { chibi: "mod:model-a" };
  v.state.files = [part("chibi", "official")];
  assert.equal(modelSource(v, false), "mod:model-a");
  assert.equal(previewBase(v, "chibi", false), "/asset/chibi/hoshino.glb?source=mod%3Amodel-a");
  assert.deepEqual(popupMode(shownStudent, v, "chibi", false), {
    start: "m:model-a",
    button: "popup.save",
    set: "m:model-a",
  });
  assert.equal(appliedKey(v, "chibi"), "self");
  assert.equal(modelUrl("a/b", "official"), "/asset/chibi/a%2Fb.glb?source=official");
  assert.equal(modelUrl("hihumi", "live", "Hihumi_Original_Exs"), "/asset/chibi/hihumi.glb?clip=Hihumi_Original_Exs");
  assert.equal(
    modelUrl("hihumi", "alt:ch0242", "CH0271_Formation Pickup"),
    "/asset/chibi/hihumi.glb?source=alt%3Ach0242&clip=CH0271_Formation%20Pickup",
  );
  v.configured = {};
  assert.equal(modelSource(v, false), "official");
});

test("the 3D model offers the student's other models as costumes, field-mode models included", () => {
  const shownStudent = student(),
    [v, swimsuit, np] = shownStudent.alts;
  v.chibi = "hoshino_original";
  swimsuit.chibi = "hoshino_swimsuit";
  swimsuit.modList.push(mod("swim-model", swimsuit.key, "chibi")); // another alt's mod: never a costume
  np.fieldChibi = "np0045"; // an event costume with only a field-mode model
  const ok = { hoshino: "ok", hoshino_swimsuit: "ok", np0045: "ok" };
  assert.deepEqual(
    costumeCandidates(shownStudent, v).map((o) => o.key),
    ["hoshino_swimsuit", "np0045"],
  );
  assert.deepEqual(
    candidates(shownStudent, v, "chibi", ok).map((c) => c.key),
    ["self", "v:hoshino_swimsuit", "v:np0045"],
  );
  assert.deepEqual(
    candidates(shownStudent, swimsuit, "chibi", ok).map((c) => c.key),
    ["self", "m:swim-model", "v:hoshino", "v:np0045"],
  );
  // Every pair is listed at once; one not checked yet or refused is listed for a preview only, and a refused
  // one cannot be chosen as the preview either
  const shape = (c) => [c.key, !!c.previewOnly, !!c.refused];
  assert.deepEqual(candidates(shownStudent, v, "chibi").map(shape), [
    ["self", false, false],
    ["v:hoshino_swimsuit", true, false],
    ["v:np0045", true, false],
  ]);
  assert.deepEqual(candidates(shownStudent, v, "chibi", { hoshino_swimsuit: "refused", np0045: null }).map(shape), [
    ["self", false, false],
    ["v:hoshino_swimsuit", true, true],
    ["v:np0045", true, false],
  ]);
  assert.deepEqual(candidates(shownStudent, v, "chibi", { hoshino_swimsuit: "refused", np0045: "ok" }).map(shape), [
    ["self", false, false],
    ["v:hoshino_swimsuit", true, true],
    ["v:np0045", false, false],
  ]);
  // a pair past the stretch limit is offered with a caution
  assert.deepEqual(candidates(shownStudent, v, "chibi", { hoshino_swimsuit: "preview", np0045: "ok" }).map(shape), [
    ["self", false, false],
    ["v:hoshino_swimsuit", false, false],
    ["v:np0045", false, false],
  ]);
  const [pastLimit, refused] = candidates(shownStudent, v, "chibi", {
    hoshino_swimsuit: "preview",
    np0045: "refused",
  }).slice(1);
  assert.equal(pastLimit.beyondFit, true);
  assert.equal(pastLimit.previewOnly, undefined);
  assert.equal(pastLimit.refused, undefined);
  assert.equal(refused.previewOnly, true);
  assert.equal(refused.refused, true);
  assert.equal(refused.beyondFit, undefined);
  // the popup starts on the saved preview before any verdict arrives
  v.previewSources = { chibi: "alt:np0045" };
  assert.equal(popupMode(shownStudent, v, "chibi").start, "v:np0045");
  delete v.previewSources;
  // the field-only alt shows a 3D tab, with no slot to switch
  assert.equal(hasKind(np, "chibi"), true);
  assert.equal(availableTab(np, "model"), "model");
  assert.deepEqual(
    candidates(shownStudent, np, "chibi").map((c) => [c.key, c.greyed]),
    [["self", true]],
  );
  // a costume previews and applies as alt:<key>
  v.previewSources = { chibi: "alt:np0045" };
  assert.equal(modelSource(v, true), "alt:np0045");
  assert.equal(previewBase(v, "chibi", true), "/asset/chibi/hoshino.glb?source=alt%3Anp0045");
  assert.equal(sourceOf("v:np0045"), "alt:np0045");
  // the portrait keeps its own rules: other alts' portrait mods, never models
  assert.ok(candidates(shownStudent, v, "portrait").every((c) => c.kind !== "alt" || c.alt.portrait));
});

test("other students' models stay out of the normal list and retain saved cross choices", () => {
  const shownStudent = student(),
    v = shownStudent.alts[0];
  shownStudent.alts.forEach((o) => {
    o.student = shownStudent.key;
  });
  v.chibi = "hoshino_original";
  shownStudent.alts[1].chibi = "hoshino_swimsuit";
  const npc = { key: "ch0171", student: "yukino", chibi: "ch0171_enemy", playable: false };
  const field = { key: "field", student: "yukino", fieldChibi: "ch0171_field" };
  const roster = [
    shownStudent,
    { key: "yukino", alts: [npc, field, { key: "portrait", student: "yukino", portrait: "portrait" }] },
  ];
  const verdicts = { hoshino_swimsuit: "ok", ch0171: "preview" };
  assert.deepEqual(
    candidates(shownStudent, v, "chibi", verdicts, roster),
    candidates(shownStudent, v, "chibi", verdicts),
  );
  assert.deepEqual(
    crossModels(roster, v).map((o) => o.key),
    ["ch0171", "field"],
  );
  assert.deepEqual(crossModels(roster, field), [], "field-only models have no destination slot");
  for (const property of ["configured", "previewSources"]) {
    v[property] = { chibi: "alt:ch0171" };
    const choices = candidates(shownStudent, v, "chibi", verdicts, roster);
    assert.deepEqual(
      choices.map((c) => c.key),
      ["self", "v:hoshino_swimsuit", "v:ch0171"],
    );
    assert.equal(choices.at(-1).cross, true);
    assert.equal(choices.at(-1).previewOnly, undefined);
    assert.equal(popupMode(shownStudent, v, "chibi", true, roster).start, "v:ch0171");
    assert.equal(modelSource(v), "alt:ch0171");
    assert.deepEqual(
      candidates(shownStudent, v, "portrait", verdicts, roster),
      candidates(shownStudent, v, "portrait", verdicts),
    );
    assert.deepEqual(
      candidates(shownStudent, v, "memorialLobby", verdicts, roster),
      candidates(shownStudent, v, "memorialLobby", verdicts),
    );
    delete v[property];
  }
});

test("cross model hits are ordered and capped, flag pending cards, and omit hard refusals", () => {
  const v = { key: "slot", student: "own", chibi: "slot" };
  const alts = Array.from({ length: 70 }, (_, i) => ({ key: `npc${i}`, student: "other", fieldChibi: `ch${i}` }));
  const roster = [{ alts }];
  const hits = crossCandidates(
    roster,
    v,
    { npc0: "refused", npc1: "preview", npc2: "ok" },
    {
      compare: (a, b) => Number(a.key.slice(3)) - Number(b.key.slice(3)),
      exclude: ["v:npc3"],
    },
  );
  assert.equal(hits.length, 64);
  assert.deepEqual(
    hits.slice(0, 3).map((c) => c.key),
    ["v:npc1", "v:npc2", "v:npc4"],
  );
  assert.equal(hits.at(-1).key, "v:npc65");
  assert.ok(hits.every((c) => c.cross && !c.greyed));
  assert.ok(hits.slice(0, 2).every((c) => !c.pending && !c.previewOnly));
  assert.ok(hits.slice(2).every((c) => c.pending));
  assert.deepEqual(
    crossCandidates(roster, v, null, { match: (o) => o.key === "npc69" }).map((c) => c.key),
    ["v:npc69"],
  );
});

test("every other student's model is listed without the 64 cap, in the same order, when asked for", () => {
  const v = { key: "slot", student: "own", chibi: "slot" };
  const alts = Array.from({ length: 200 }, (_, i) => ({ key: `npc${i}`, student: "other", fieldChibi: `ch${i}` }));
  const roster = [{ alts }],
    compare = (a, b) => Number(a.key.slice(3)) - Number(b.key.slice(3));
  const verdicts = { npc0: "refused", npc5: "preview" };
  const capped = crossCandidates(roster, v, verdicts, { compare });
  assert.equal(capped.length, 64, "a search's hits stop at what one RPC call takes");
  const all = crossCandidates(roster, v, verdicts, { compare, limit: Infinity });
  assert.equal(all.length, 199, "every other model but the refused one");
  assert.deepEqual(
    all.slice(0, 64).map((c) => c.key),
    capped.map((c) => c.key),
    "the first 64 are the capped list",
  );
  assert.deepEqual(
    all.map((c) => c.key),
    all.map((c) => c.key).sort((a, b) => Number(a.slice(5)) - Number(b.slice(5))),
  );
  assert.ok(all.every((c) => c.cross && !c.greyed) && all.find((c) => c.key === "v:npc5").pending === undefined);
  // the student's own alts and models of the same student are never among them
  assert.deepEqual(
    crossCandidates([{ alts: [{ key: "mine", student: "own", chibi: "mine" }, ...alts.slice(0, 2)] }], v, null, {
      limit: Infinity,
    }).map((c) => c.key),
    ["v:npc0", "v:npc1"],
  );
});

test("Show other students is off at every start and is kept only in memory", async () => {
  const fresh = await import("../../src/bmpt/web/js/mods/switch-sources.js?fresh-start");
  assert.equal(showOthers(), false, "off by default");
  assert.equal(fresh.showOthers(), false);
  setShowOthers(1);
  assert.equal(showOthers(), true, "kept across students and popup openings");
  assert.equal(fresh.showOthers(), false, "a page that starts again begins unchecked");
  setShowOthers(false);
  assert.equal(showOthers(), false);
  // nothing writes it down: no storage, no settings, no library call mentions it
  for (const file of ["mods/switch-sources.js", "detail/detail.js"]) {
    const lines = readFileSync(new URL(`../../src/bmpt/web/js/${file}`, import.meta.url), "utf8").split("\n");
    for (const line of lines.filter((l) => /showOthers|ShowOthers/.test(l)))
      assert.ok(!/localStorage|sessionStorage|settings|rpc\(|fetch\(/.test(line), `${file}: ${line}`);
  }
});

test("a card of another student's model carries the caution and its ids, from a search or from the checkbox", () => {
  const v = { key: "slot", student: "own", chibi: "slot" };
  const roster = [
    {
      alts: [
        { key: "a", student: "other", chibi: "ch1", fileId: "f1" },
        { key: "b", student: "other", fieldChibi: "np2" },
      ],
    },
  ];
  for (const cards of [
    crossCandidates(roster, v, null, { match: (o) => o.key === "a" }),
    crossCandidates(roster, v, null, { limit: Infinity }),
  ]) {
    assert.ok(cards.length > 0);
    for (const card of cards) {
      const extras = crossExtras(card, "careful");
      assert.equal(extras.hint, "careful");
      assert.deepEqual(extras.terms, [card.alt.fileId, card.alt.chibi, card.alt.fieldChibi]);
    }
  }
  assert.deepEqual(
    crossExtras({ key: "v:sibling", kind: "alt" }, "careful"),
    {},
    "the student's own costumes carry none",
  );
  assert.deepEqual(crossExtras({ key: "self", kind: "self" }, "careful"), {});
});

test("past-limit verdicts allow Apply for every pair while hard refusals stay blocked", () => {
  const v = { key: "slot", student: "own", chibi: "slot" };
  const shownStudent = { alts: [v, { key: "sibling", student: "own", chibi: "sibling" }] };
  for (const [verdict, ownHold, crossHold] of [
    ["ok", null, null],
    ["preview", null, null],
    ["refused", "unfit", "unfit"],
    ["unknown", "unfit", "unfit"],
    [null, null, "pending"],
    [undefined, null, "pending"],
  ]) {
    assert.equal(costumeHold(shownStudent, v, "v:sibling", { sibling: verdict }), ownHold);
    assert.equal(costumeHold(shownStudent, v, "v:cross", { cross: verdict }), crossHold);
  }
  assert.equal(costumeHold(shownStudent, v, "self", {}), null);
  assert.equal(costumeHold(shownStudent, v, "m:mod", {}), null);
});

test("split model slots offer dependency mods and judge every slot part", async () => {
  const { selectionStatus, candidateStatus } = await import("../../src/bmpt/web/js/mods/switch-sources.js");
  const dependency = { id: "dependency", resourceType: "chibi", core: "ch0258", targetAlt: "ch0258" };
  const v = {
    key: "ch0258",
    chibi: "ch0258_02",
    chibiCores: ["ch0258_02", "ch0258"],
    configured: { chibi: "mod:dependency" },
    state: {
      files: [
        part("chibi", "official", { core: "ch0258_02" }),
        part("chibi", "mod:dependency", { core: "ch0258", official: false, restorable: false }),
      ],
    },
    modList: [
      dependency,
      { ...dependency, id: "enemy", core: "ch0258_01_enemy" },
      { ...dependency, id: "shared", core: "droid_kaiserpmc_shield" },
      { ...dependency, id: "foreign", core: "hoshino_original" },
    ],
  };
  const shownStudent = {
    alts: [
      v,
      {
        key: "hoshino",
        chibi: "hoshino_original",
        modList: [{ ...dependency, id: "other", core: "hoshino_original", target: "hoshino" }],
      },
    ],
  };
  // other cores' mods stay out; the other alt's model is listed as a costume, applicable once checked
  assert.deepEqual(
    candidates(shownStudent, v, "chibi", { hoshino: "ok" }).map((c) => [c.key, !!c.previewOnly]),
    [
      ["self", false],
      ["m:dependency", false],
      ["v:hoshino", false],
    ],
  );
  assert.deepEqual(
    candidates(shownStudent, v, "chibi", { hoshino: "preview" }).map((c) => [c.key, !!c.previewOnly, !!c.beyondFit]),
    [
      ["self", false, false],
      ["m:dependency", false, false],
      ["v:hoshino", false, true],
    ],
  );
  assert.deepEqual(
    candidates(shownStudent, v, "chibi").map((c) => [c.key, !!c.previewOnly]),
    [
      ["self", false],
      ["m:dependency", false],
      ["v:hoshino", true],
    ],
  );
  assert.equal(candidates(shownStudent, v, "chibi")[0].greyed, true);
  assert.equal(appliedKey(v, "chibi"), "m:dependency");
  assert.equal(selectionStatus(v, "chibi"), "status.applied");
  dependency.incomplete = true;
  assert.equal(selectionStatus(v, "chibi"), "mods.incomplete");
  assert.equal(candidateStatus(v, "chibi", "m:dependency"), "mods.incomplete");
});

test("Applied follows game intent independently of physical writes and preview selection", async () => {
  const { selectionStatus, candidateStatus, detailStatus, selectionError, importStatus } = await import(
    "../../src/bmpt/web/js/mods/switch-sources.js"
  );
  for (const kind of ["portrait", "memorialLobby", "chibi"]) {
    const v = {
      key: "a",
      portrait: "a_spr",
      memorialLobby: "a_home",
      chibi: "a",
      configured: { [kind]: "mod:chosen" },
      state: { files: [part(kind, "mod:old")] },
      selection: {},
    };
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
    assert.equal(
      selectionStatus(v, kind),
      "status.applied",
      "physical installation verdicts do not change game intent",
    );
  }
});

test("saved previews and game intent remain independent for every asset kind", async () => {
  const { intendedKey, isApplied, selectionStatus } = await import("../../src/bmpt/web/js/mods/switch-sources.js");
  for (const kind of ["portrait", "memorialLobby", "chibi"]) {
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
