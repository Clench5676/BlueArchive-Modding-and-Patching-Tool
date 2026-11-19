import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { hasAsset } from "../../src/bmpt/web/js/mods/switch-sources.js";
import { visit } from "../../src/bmpt/web/js/student-list/student-list-order.js";

// Run the event coalescing and refresh with a detail page already on screen.
const source = readFileSync(new URL("../../src/bmpt/web/js/app/main.js", import.meta.url), "utf8");
const code = source.slice(source.indexOf("let heldChange,"), source.indexOf("// A completed toggle"));
function harness({ shown = "screen-detail", writing = () => false } = {}) {
  const refreshed = [],
    forgotten = [],
    timers = [],
    routes = [],
    options = [];
  let invalidated = 0;
  const env = {
    ctx: { invalidateStudentList: () => invalidated++ },
    BatchImport: { invalidate() {} },
    Detail: { filesChanged: (change) => refreshed.push(change) },
    $: (id) => ({ hidden: id !== shown }),
    Popup: {
      whenIdle: (_every, blocked = () => false) =>
        new Promise((resolve) => {
          const check = () => (blocked() ? setImmediate(check) : resolve());
          check();
        }),
    },
    Loading: { writing },
    StudentHall: { refreshOptions: () => options.push(env.routeToken) },
    route: async () => {
      routes.push(env.routeToken);
    },
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
  return { ...api, refreshed, forgotten, routes, options, invalidated: () => invalidated };
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
  const studentListData = { students: [shownStudent] };
  const code = source.slice(source.indexOf("function visited("), source.indexOf("function showScreen("));
  const visited = new Function("studentListData", "hasAsset", "visit", `${code}; return visited;`)(
    studentListData,
    hasAsset,
    visit,
  );
  visited(shownStudent, "special:arona_workpage");
  assert.equal(shownStudent.alts[1].recent, 0);
  assert.equal(shownStudent.alts[0].recent, undefined);
});

test("the Student Hall and the list wait for a watched write to end before they are drawn again; its own end draws at once", async () => {
  for (const shown of ["screen-student-hall", "screen-student-list"]) {
    let busy = true;
    const h = harness({ shown, writing: () => busy });
    // Enable Mods flips midway through its job: the shown screen stays under Now Loading
    h.libraryChanged(null);
    const waiting = h.applyChange();
    await new Promise((resolve) => setImmediate(resolve));
    await new Promise((resolve) => setImmediate(resolve));
    assert.deepEqual(h.routes, [], shown);
    busy = false;
    await waiting;
    assert.equal(h.routes.length, 1, shown);
    // the write's own end (refreshAfterToggle) is drawn while its hold is still up
    busy = true;
    h.libraryChanged(null);
    await h.applyChange({ now: true });
    assert.equal(h.routes.length, 2, shown);
  }
});

test("a write of game files or the switch leaves the Student Hall standing and draws the list again", async () => {
  const files = [
    (h) => h.libraryChanged(null, { files: true }),
    (h) => h.libraryChanged({ cores: ["dress_spr"], resourceTypes: ["portrait"] }),
    (h) => {
      h.libraryChanged(null, { files: true });
      h.libraryChanged({ cores: ["dress_spr"], resourceTypes: ["portrait"] });
    },
  ];
  for (const change of files) {
    const hall = harness({ shown: "screen-student-hall", writing: () => true });
    change(hall);
    await hall.applyChange();
    assert.deepEqual(hall.routes, []);
    assert.equal(hall.options.length, 1);
    assert.equal(hall.invalidated() > 0, true);
    const list = harness({ shown: "screen-student-list" });
    change(list);
    await list.applyChange();
    assert.equal(list.routes.length, 1);
  }
});

test("a mod-list change among file changes still draws the Student Hall again", async () => {
  for (const order of [0, 1]) {
    const h = harness({ shown: "screen-student-hall" });
    const changes = [() => h.libraryChanged(null, { files: true }), () => h.libraryChanged(null)];
    if (order) changes.reverse();
    for (const change of changes) change();
    await h.applyChange();
    assert.equal(h.routes.length, 1);
    assert.deepEqual(h.options, []);
    // the next change starts clean
    h.libraryChanged(null, { files: true });
    await h.applyChange();
    assert.equal(h.routes.length, 1);
  }
});
