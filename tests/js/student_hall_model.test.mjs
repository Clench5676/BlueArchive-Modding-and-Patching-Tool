import assert from "node:assert/strict";
import { test } from "node:test";
import {
  createDraft,
  sourceKey,
  portraitRows,
  studentChoices,
  memorialChoices,
  contentBounds,
} from "../../src/bmpt/web/js/student-hall/student-hall-chibi.js";
import { normalizeView } from "../../src/bmpt/web/js/student-list/student-list-order.js";
const saved = () => ({
  revision: 0,
  mode: "portraits",
  background: "office-day",
  portraits: [],
  memorialLobbies: [
    { id: "a", alt: "hero", source: "official" },
    { id: "b", alt: "hero", source: "mod:alternate" },
  ],
  active: "b",
});
const source = (alt, kind = "portrait", extra = {}) => ({
  alt,
  student: "hero",
  studentNames: { en: "Hero" },
  names: { en: { full: alt } },
  source: "mod:" + alt,
  resourceType: kind,
  favorite: false,
  releasedOnServer: true,
  ...extra,
});
const label = (names) => names?.en;

test("the same portrait source cannot be added twice, but different assets of one student remain independent", () => {
  const state = createDraft(saved());
  state.begin("layout");
  const a = state.add(source("hero"), () => "one");
  assert.equal(
    state.add(source("hero"), () => "duplicate"),
    null,
  );
  const b = state.add(source("hero", "portrait", { source: "mod:alternate" }), () => "two");
  a.x = 400;
  assert.notEqual(a.x, b.x);
  assert.equal(state.current().portraits.length, 2);
  assert.equal(state.saved().portraits.length, 0);
  assert.ok(state.dirty());
  state.cancel();
  assert.equal(state.current().portraits.length, 0);
  assert.equal(state.dirty(), false);
});
test("legacy duplicate portrait reads retain the first UUID and every intentional position or relative scale", () => {
  const value = saved();
  value.portraits = [
    { id: "first", alt: "hero", source: "mod:one", x: 410, y: 905, scale: 0.6 },
    { id: "second", alt: "hero", source: "mod:one", x: 1000, y: 1120, scale: 1 },
    { id: "other", alt: "hero", source: "mod:two", x: 1310, y: 1055, scale: 1.3 },
  ];
  const state = createDraft(value);
  assert.deepEqual(state.current().portraits, [value.portraits[0], value.portraits[2]]);
  assert.equal(value.portraits.length, 3, "reading never mutates the supplied saved snapshot");
  assert.equal(state.current().memorialLobbies.length, 2);
});

test("playlist reorder retains active UUID and the last entry cannot be removed", () => {
  const state = createDraft(saved());
  state.begin("memorialLobby");
  state.move("a", "b");
  assert.equal(state.current().active, "b");
  assert.equal(state.remove("a"), true);
  assert.equal(state.remove("b"), false);
  state.cancel();
  assert.deepEqual(
    state.current().memorialLobbies.map((entry) => entry.id),
    ["a", "b"],
  );
});
test("portrait rows include official portraits and library mods, merge skins without leaking other students, and match saved aliases", () => {
  const choices = [
    source("hero", "portrait", { source: "official" }),
    source("hero", "portrait", { aliases: ["Alias Two"] }),
    source("hero_swimsuit", "portrait", { customAliases: ["Summer Nickname"] }),
    source("other", "portrait", { student: "other", studentNames: { en: "Other" } }),
  ];
  const view = normalizeView({ groupAlts: true, sort: "name" });
  const all = portraitRows(choices, view, "", label, "en");
  assert.equal(all.find((row) => row.key === "hero").choices.length, 3);
  assert.ok(all.flatMap((row) => row.choices).some((choice) => choice.source === "official"));
  assert.deepEqual(
    portraitRows(choices, view, "Alias Two", label, "en")
      .flatMap((row) => row.choices)
      .map((choice) => choice.alt),
    ["hero"],
  );
  assert.deepEqual(
    portraitRows(choices, view, "Summer Nickname", label, "en")
      .flatMap((row) => row.choices)
      .map((choice) => choice.alt),
    ["hero_swimsuit"],
  );
});
test("memorial availability filters students, selected student narrows candidates, and recent order uses source history", () => {
  const choices = [
    source("hero", "memorialLobby", { source: "official", viewed: 3 }),
    source("hero", "memorialLobby", { viewed: 0 }),
    source("new", "memorialLobby", { student: "new", releasedOnServer: false, viewed: 1 }),
  ];
  const view = normalizeView({ sort: "recent", order: "desc" });
  const students = studentChoices(choices, view, "", label, "en");
  assert.deepEqual(memorialChoices(choices, students, null, view, "").map(sourceKey), [
    "hero:mod:hero",
    "new:mod:new",
    "hero:official",
  ]);
  assert.equal(memorialChoices(choices, students, "hero", view, "").length, 2);
  const unreleased = studentChoices(choices, { ...view, releaseStatus: "no" }, "", label, "en");
  assert.deepEqual(
    unreleased.map((s) => s.key),
    ["new"],
  );
});
test("memorial sort controls respect direction, localized names, mod counts and aliases", () => {
  const a = source("raw_z", "memorialLobby", {
    student: "a",
    names: { en: { full: "Alpha" } },
    aliases: ["Secret"],
    mods: 5,
    order: 0,
  });
  const b = source("raw_a", "memorialLobby", { student: "b", names: { en: { full: "Zulu" } }, mods: 1, order: 1 });
  const choices = [a, b],
    students = studentChoices(choices, normalizeView({}), "", label, "en");
  assert.deepEqual(
    memorialChoices(choices, students, null, { sort: "name", order: "asc" }, "", {}, label).map((c) => c.alt),
    ["raw_z", "raw_a"],
  );
  assert.equal(memorialChoices(choices, students, null, { sort: "name", order: "desc" }, "", {}, label)[0], b);
  assert.equal(memorialChoices(choices, students, null, { sort: "default", order: "asc" }, "")[0], a);
  assert.equal(memorialChoices(choices, students, null, { sort: "mods", order: "desc" }, "")[0], a);
  assert.equal(memorialChoices(choices, students, null, { sort: "default", order: "desc" }, "")[0], b);
  assert.deepEqual(memorialChoices(choices, students, null, { sort: "recent", order: "desc" }, "Secret"), [a]);
});

test("dialogue bounds clear the actual local toolbar and bottom navigation at any stage scale", () => {
  const scaled = contentBounds({ top: 0, width: 1280 }, [{ bottom: 120, height: 40 }], {
    top: 650.6666666667,
    height: 69.333333,
  });
  assert.equal(scaled.top, 196);
  assert.ok(Math.abs(scaled.bottom - 960) < 0.001);
  const bounds = contentBounds({ top: 20, width: 1920 }, [{ bottom: 210, height: 70 }], { top: 996, height: 104 });
  assert.equal(bounds.top, 206);
  assert.equal(bounds.bottom, 960);
});

test("portrait layers and mirror are draft-only, preserve identity and survive save or restore on cancel", () => {
  const value = saved();
  value.portraits = ["a", "b", "c"].map((id) => ({ id, alt: id, source: `mod:${id}`, x: 500, y: 1000, scale: 1 }));
  const state = createDraft(value);
  state.begin("layout");
  assert.equal(state.layer("a", "back"), false);
  assert.equal(state.layer("c", "front"), false);
  state.layer("a", 1);
  assert.deepEqual(
    state.current().portraits.map((entry) => entry.id),
    ["b", "a", "c"],
  );
  state.layer("b", "front");
  state.layer("c", "back");
  state.flip("a");
  assert.deepEqual(
    state.current().portraits.map((entry) => entry.id),
    ["c", "a", "b"],
  );
  assert.equal(state.current().portraits[1].flipHorizontal, true);
  state.cancel();
  assert.deepEqual(state.current().portraits, value.portraits);
  state.begin("layout");
  state.layer("a", "front");
  state.flip("a");
  state.accept({ ...state.current(), revision: 1 });
  assert.deepEqual(
    state.saved().portraits.map((entry) => entry.id),
    ["b", "c", "a"],
  );
  assert.equal(state.saved().portraits[2].flipHorizontal, true);
});
test("merged memorial students expose every alt and source in the selected student", () => {
  const choices = [
    source("hero", "memorialLobby", { source: "official" }),
    source("hero_swimsuit", "memorialLobby"),
    source("other", "memorialLobby", { student: "other" }),
  ];
  const view = normalizeView({ groupAlts: true, sort: "name", order: "asc" }),
    students = studentChoices(choices, view, "", label, "en");
  assert.equal(students.length, 2);
  const hero = students.find((student) => student.student === "hero");
  assert.deepEqual(
    memorialChoices(choices, students, hero.key, view, "").map((choice) => choice.alt),
    ["hero", "hero_swimsuit"],
  );
});

test("memorial resources are unique while distinct sources of the same student remain selectable", () => {
  const state = createDraft(saved());
  state.begin("memorialLobby");
  assert.equal(
    state.add(source("hero", "memorialLobby", { source: "official" }), () => "dup"),
    null,
  );
  assert.equal(
    state.add(source("hero", "memorialLobby", { source: "mod:alternate" }), () => "dup2"),
    null,
  );
  assert.equal(state.add(source("hero", "memorialLobby", { source: "mod:another" }), () => "other").id, "other");
  assert.equal(state.add(source("hero_swimsuit", "memorialLobby", { source: "official" }), () => "skin").id, "skin");
  assert.equal(state.current().memorialLobbies.length, 4);
});

test("legacy memorial repeats keep first position and preserve an active duplicate UUID without mutation", () => {
  const value = saved();
  value.memorialLobbies.push({ id: "active-duplicate", alt: "hero", source: "official" });
  value.active = "active-duplicate";
  value.portraits = [{ id: "portrait-id", alt: "hero", source: "official" }];
  const before = structuredClone(value),
    state = createDraft(value);
  assert.deepEqual(
    state.current().memorialLobbies.map((entry) => entry.id),
    ["active-duplicate", "b"],
  );
  assert.equal(state.current().active, "active-duplicate");
  assert.equal(state.dirty(), false);
  assert.deepEqual(value, before);
  const ids = [...state.current().portraits, ...state.current().memorialLobbies].map((entry) => entry.id);
  assert.equal(new Set(ids).size, ids.length);
  state.accept(value);
  assert.deepEqual(
    state.current().memorialLobbies.map((entry) => entry.id),
    ["active-duplicate", "b"],
  );
});

test("picker Has Mod, No Mod and mod-count sorting are scoped to the selected asset kind", () => {
  const choices = [];
  for (const alt of ["spr_only", "memorial_lobby_only", "both", "none"]) {
    for (const kind of ["portrait", "memorialLobby"])
      choices.push(source(alt, kind, { student: alt, source: "official", mods: 99 }));
  }
  choices.push(
    source("spr_only", "portrait", { student: "spr_only", mods: 99 }),
    source("memorial_lobby_only", "memorialLobby", { student: "memorial_lobby_only", mods: 99 }),
    source("both", "portrait", { student: "both", mods: 99 }),
    source("both", "memorialLobby", { student: "both", mods: 99 }),
    source("both", "memorialLobby", { student: "both", source: "mod:second", mods: 99 }),
  );
  const pick = (kind, mods) =>
    studentChoices(choices, normalizeView({ mods, sort: "mods", order: "desc" }), "", label, "en", kind);
  assert.deepEqual(
    pick("portrait", "mods")
      .map((s) => s.key)
      .sort(),
    ["both", "spr_only"],
  );
  assert.deepEqual(
    pick("memorialLobby", "mods").map((s) => s.key),
    ["both", "memorial_lobby_only"],
  );
  assert.deepEqual(
    pick("portrait", "none")
      .map((s) => s.key)
      .sort(),
    ["memorial_lobby_only", "none"],
  );
  assert.deepEqual(
    pick("memorialLobby", "none")
      .map((s) => s.key)
      .sort(),
    ["none", "spr_only"],
  );
  assert.equal(pick("portrait", "mods").find((s) => s.key === "both").mods, 1);
  assert.equal(pick("memorialLobby", "mods")[0].mods, 2);
  assert.equal(
    studentChoices(choices, normalizeView({ mods: "mods" }), "", label, "en").length,
    4,
    "unscoped student list semantics retain reported totals",
  );
});

test("merged picker students aggregate only the current resource kind", () => {
  const choices = [
    source("hero", "portrait", { source: "official", mods: 100 }),
    source("hero", "memorialLobby", { source: "official", mods: 100 }),
    source("hero", "portrait", { mods: 100 }),
    source("hero_swimsuit", "memorialLobby", { source: "official", mods: 100 }),
    source("hero_swimsuit", "memorialLobby", { mods: 100 }),
  ];
  for (const kind of ["portrait", "memorialLobby"]) {
    const [student] = studentChoices(choices, normalizeView({ groupAlts: true, mods: "mods" }), "", label, "en", kind);
    assert.equal(student.mods, 1);
  }
  assert.equal(
    studentChoices(
      choices.filter((choice) => choice.resourceType !== "memorialLobby" || choice.source === "official"),
      normalizeView({ groupAlts: true, mods: "mods" }),
      "",
      label,
      "en",
      "memorialLobby",
    ).length,
    0,
  );
});

import { portraitReplacements } from "../../src/bmpt/web/js/student-hall/student-hall-chibi.js";
const swapChoices = [
  { alt: "base", student: "hero", resourceType: "portrait", source: "mod:current" },
  { alt: "base", student: "hero", resourceType: "portrait", source: "official" },
  { alt: "other", student: "hero", resourceType: "portrait", source: "official" },
  { alt: "base", student: "hero", resourceType: "portrait", source: "mod:another" },
  { alt: "other", student: "hero", resourceType: "portrait", source: "mod:placed" },
  { alt: "other", student: "hero", resourceType: "memorialLobby", source: "mod:memorialLobby" },
  { alt: "other", student: "stranger", resourceType: "portrait", source: "mod:wrong-student" },
];
test("portrait replacements include the student's official art and same-student mods, excluding self, placed and memorial lobby art", () => {
  const entry = { id: "a", alt: "base", source: "mod:current" },
    placed = [entry, { alt: "other", source: "mod:placed" }];
  assert.deepEqual(
    portraitReplacements(entry, swapChoices, placed).map((c) => `${c.alt}:${c.source}`),
    ["base:official", "other:official", "base:mod:another"],
  );
  // An official portrait never lists itself, only the student's other portraits.
  assert.deepEqual(
    portraitReplacements({ id: "b", alt: "base", source: "official" }, swapChoices, []).map(
      (c) => `${c.alt}:${c.source}`,
    ),
    ["base:mod:current", "other:official", "base:mod:another", "other:mod:placed"],
  );
  assert.deepEqual(portraitReplacements({ ...entry, source: "mod:missing" }, swapChoices, placed), []);
  assert.deepEqual(portraitReplacements(entry, swapChoices, [...placed, ...swapChoices]), []);
});
test("replacing a portrait keeps its identity, position, mirror, size and layer; Cancel restores its source", () => {
  const entry = { id: "a", alt: "base", source: "mod:current", x: 812, y: 1240, scale: 1.73, flipHorizontal: true };
  const state = {
    revision: 0,
    mode: "portraits",
    portraits: [
      { id: "below", alt: "below", source: "official", x: 0, y: 1100, scale: 1 },
      entry,
      { id: "above", alt: "above", source: "official", x: 100, y: 1100, scale: 1 },
    ],
    memorialLobbies: [],
    active: null,
  };
  const draft = createDraft(state);
  assert.equal(draft.replacePortrait("a", swapChoices[2], swapChoices), false);
  draft.begin("layout");
  assert.equal(draft.replacePortrait("a", swapChoices[5], swapChoices), false);
  assert.ok(draft.replacePortrait("a", swapChoices[2], swapChoices));
  assert.deepEqual(draft.current().portraits[1], { ...entry, alt: "other", source: "official" });
  assert.deepEqual(
    draft.current().portraits.map((e) => e.id),
    ["below", "a", "above"],
  );
  assert.ok(draft.dirty());
  draft.cancel();
  assert.deepEqual(draft.current().portraits[1], entry);
  draft.begin("layout");
  draft.replacePortrait("a", swapChoices[3], swapChoices);
  draft.accept({ ...draft.current(), revision: 1 });
  assert.equal(draft.saved().portraits[1].source, "mod:another");
});
