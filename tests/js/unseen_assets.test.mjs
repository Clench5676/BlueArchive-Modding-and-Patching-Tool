import assert from "node:assert/strict";
import { test } from "node:test";
import { clearUnseen, hasUnseen, shownUnseen, switchUnseen } from "../../src/bmpt/web/js/detail/unseen-assets.js";
import { DEFAULT_VIEW, normalizeView, order } from "../../src/bmpt/web/js/student-list/student-list-order.js";

const mod = (id, target, kind) => ({ id, targetAlt: target, resourceType: kind, fresh: true });
const alt = (key, mods = []) => ({
  key,
  portrait: `${key}_spr`,
  memorialLobby: `${key}_home`,
  chibi: `${key}_chibi`,
  names: { en: { full: key } },
  fresh: mods.length > 0,
  modList: mods,
  state: { files: [] },
});
const student = (...alts) => ({ key: "student", studentNames: { en: "Student" }, alts });
const shownIds = (...args) => shownUnseen(...args).map((entry) => entry.id);

for (const kind of ["portrait", "memorialLobby", "chibi"])
  for (const enabled of [true, false]) {
    test(`${kind} marks the ${enabled ? "applied" : "configured"} asset on its tab, and offers the rest on the switch`, () => {
      const v = alt("a", [mod("current", "a", kind), mod("other", "a", kind)]),
        f = student(v);
      v.state.files = [{ resourceType: kind, applied: enabled ? "mod:current" : "official", official: !enabled }];
      v.configured = { [kind]: "mod:current" };
      assert.ok(hasUnseen(v, kind));
      assert.deepEqual(shownIds(f, v, kind, enabled), ["current"]);
      assert.equal(switchUnseen(f, v, kind, enabled), true);
      clearUnseen(f, ["other"]);
      assert.equal(switchUnseen(f, v, kind, enabled), false, "the configured asset has no switch-card dot");
      assert.ok(hasUnseen(v, kind), "its tab stays marked until shown");
      clearUnseen(f, shownIds(f, v, kind, enabled));
      assert.equal(hasUnseen(v, kind), false);
      assert.equal(v.fresh, false);
    });
  }

test("showing a portrait worn across alts clears the asset's owning alt", () => {
  const a = alt("a"),
    b = alt("b", [mod("dress", "b", "portrait")]),
    f = student(a, b);
  a.state.files = [{ resourceType: "portrait", applied: "mod:dress", official: false }];
  assert.deepEqual(shownIds(f, a, "portrait"), ["dress"]);
  assert.equal(switchUnseen(f, a, "portrait"), false);
  clearUnseen(f, shownIds(f, a, "portrait"));
  assert.equal(b.fresh, false);
});

test("showing a repaired import clears its original asset's dot", () => {
  const original = { ...mod("original", "a", "memorialLobby"), repairedCopyId: "fixed" };
  const repaired = { ...mod("fixed", "a", "memorialLobby"), repairSourceId: "original", fresh: false };
  const a = alt("a", [original, repaired]),
    f = student(a);
  a.state.files = [{ resourceType: "memorialLobby", applied: "mod:fixed", official: false }];
  assert.deepEqual(shownIds(f, a, "memorialLobby"), ["original"]);
  assert.equal(switchUnseen(f, a, "memorialLobby"), false);
  clearUnseen(f, shownIds(f, a, "memorialLobby"));
  assert.equal(a.fresh, false);
});

test("a configured alternate memorial lobby core is seen even when it is not offered by the switch", () => {
  const a = alt("a", [{ ...mod("global", "a", "memorialLobby"), core: "a_home_gl" }]),
    f = student(a);
  a.configured = { memorialLobby: "mod:global" };
  assert.deepEqual(shownIds(f, a, "memorialLobby", false), ["global"]);
  clearUnseen(f, shownIds(f, a, "memorialLobby", false));
  assert.equal(a.fresh, false);
});

test("viewing the only imported memorial lobby clears both separate and merged student list marks", () => {
  const a = alt("a"),
    b = alt("b", [mod("memorialLobby", "b", "memorialLobby")]),
    f = student(a, b);
  b.state.files = [{ resourceType: "memorialLobby", applied: "mod:memorialLobby", official: false }];
  const marks = (merged) =>
    order({ students: [f] }, normalizeView({ ...DEFAULT_VIEW, groupAlts: merged }), {
      label: (names) => names?.en,
    }).map((card) => !!card.fresh);
  assert.deepEqual(marks(false), [false, true]);
  assert.deepEqual(marks(true), [true]);
  clearUnseen(f, shownIds(f, b, "memorialLobby"));
  assert.deepEqual(marks(false), [false, false]);
  assert.deepEqual(marks(true), [false]);
});

test("clearing one kind preserves the other tab and the merged student list dot", () => {
  const a = alt("a", [mod("portrait", "a", "portrait"), mod("memorialLobby", "a", "memorialLobby")]),
    f = student(a);
  clearUnseen(f, ["memorialLobby"]);
  assert.equal(hasUnseen(a, "memorialLobby"), false);
  assert.equal(hasUnseen(a, "portrait"), true);
  assert.equal(a.fresh, true);
});

test("unapplied assets stay unseen on tab visits and clear only when their switch popup offers them", () => {
  const a = alt("a", [mod("portrait", "a", "portrait"), mod("memorialLobby", "a", "memorialLobby")]),
    f = student(a);
  assert.deepEqual(shownIds(f, a, "portrait"), []);
  assert.deepEqual(shownIds(f, a, "memorialLobby"), []);
  assert.equal(switchUnseen(f, a, "portrait"), true);
  assert.equal(switchUnseen(f, a, "memorialLobby"), true);
  clearUnseen(f, ["memorialLobby"]);
  assert.equal(switchUnseen(f, a, "memorialLobby"), false);
  assert.equal(a.fresh, true);
});
