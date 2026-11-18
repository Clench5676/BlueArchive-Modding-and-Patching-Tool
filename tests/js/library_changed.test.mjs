import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { hasAsset } from "../../src/bmpt/web/js/mods/switch-sources.js";
import { visit } from "../../src/bmpt/web/js/roster/roster-order.js";

// Run the event coalescing and refresh with a detail page already on screen.
const source = readFileSync(new URL("../../src/bmpt/web/js/app/main.js", import.meta.url), "utf8");
const code = source.slice(source.indexOf("let heldChange,"), source.indexOf("// A completed toggle"));
function harness() {
  const refreshed = [],
    forgotten = [],
    timers = [];
  let invalidated = 0;
  const env = {
    ctx: { invalidateRoster: () => invalidated++ },
    BatchImport: { invalidate() {} },
    Detail: { filesChanged: (change) => refreshed.push(change) },
    $: (id) => ({ hidden: id === "screen-hall" }),
    forget: (base) => forgotten.push(base),
    spinePrefix: (kind, core) => `${kind}/${core}`,
    clearTimeout() {},
    setTimeout: (fn) => {
      timers.push(fn);
      return timers.length;
    },
    routeToken: 1,
  };
  const api = new Function(
    "env",
    `const { ${Object.keys(env).join(", ")} } = env;
    ${code}
    return { libraryChanged, applyChange };`,
  )(env);
  return { ...api, refreshed, forgotten, invalidated: () => invalidated };
}

test("a library-only add refreshes the open detail page with no game thumbnail eviction", async () => {
  const h = harness();
  h.libraryChanged({ cores: [], resourceTypes: [], mods: true });
  await h.applyChange();
  assert.deepEqual(h.refreshed, [null]);
  assert.equal(h.invalidated(), 1);
  assert.deepEqual(h.forgotten, []);
});

test("coalescing an add with a switch still refreshes the mod list and evicts the changed core", async () => {
  for (const addFirst of [true, false]) {
    const h = harness();
    const add = { cores: [], resourceTypes: [], mods: true },
      apply = { cores: ["dress_spr"], resourceTypes: ["portrait"] };
    for (const change of addFirst ? [add, apply] : [apply, add]) h.libraryChanged(change);
    await h.applyChange();
    assert.deepEqual(h.refreshed, [null]);
    assert.deepEqual(h.forgotten, ["portrait/dress_spr", "official-portrait/dress_spr"]);
  }
});

test("an empty file change without a mod-list change still needs no refresh", async () => {
  const h = harness();
  h.libraryChanged({ cores: [], resourceTypes: [] });
  await h.applyChange();
  assert.equal(h.invalidated(), 0);
  assert.deepEqual(h.refreshed, []);
});

test("a special scene's category decides cache eviction even when its core ends in spr", async () => {
  const h = harness();
  h.libraryChanged({ cores: ["scene_spr"], resourceTypes: ["memorialLobby"] });
  await h.applyChange();
  assert.deepEqual(h.forgotten, ["memorialLobby/scene_spr", "official-memorialLobby/scene_spr"]);
  assert.deepEqual(h.refreshed, [{ cores: ["scene_spr"] }]);
});

test("a visit records the selected background alongside a portrait in its student", () => {
  const shownStudent = {
    alts: [
      { key: "arona", portrait: "arona_spr" },
      { key: "special:arona_workpage", portrait: null, memorialLobby: "arona_workpage" },
    ],
  };
  const rosterData = { students: [shownStudent] };
  const code = source.slice(source.indexOf("function visited("), source.indexOf("function showScreen("));
  const visited = new Function("rosterData", "hasAsset", "visit", `${code}; return visited;`)(
    rosterData,
    hasAsset,
    visit,
  );
  visited(shownStudent, "special:arona_workpage");
  assert.equal(shownStudent.alts[1].recent, 0);
  assert.equal(shownStudent.alts[0].recent, undefined);
});
