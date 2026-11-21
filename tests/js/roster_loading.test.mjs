import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { hasAsset } from "../../src/bmpt/web/js/mods/switch-sources.js";
import { listPicture } from "../../src/bmpt/web/js/student-list/list-picture.js";
import { spineBase } from "../../src/bmpt/web/js/core/api.js";

const source = readFileSync(new URL("../../src/bmpt/web/js/student-list/student-list.js", import.meta.url), "utf8");
const rowsCode = source.slice(source.indexOf("async function renderRow("), source.indexOf("function maxScroll("));
const deferred = () => {
  let resolve;
  const promise = new Promise((r) => {
    resolve = r;
  });
  return { promise, resolve };
};
const flush = async () => {
  for (let i = 0; i < 20; i++) await Promise.resolve();
};

test("declined licence keeps collection portraits with a configured source and mods off", () => {
  const code = source.slice(source.indexOf("const portraitOf ="), source.indexOf("// --- rows near the view"));
  const portraitOf = new Function("spineAllowed", "listPicture", `${code}\nreturn portraitOf;`)(
    () => false,
    listPicture,
  );
  assert.equal(
    portraitOf({ officialThumbnail: "/asset/collection/portrait.png", configured: { portrait: "mod:a" } }),
    "/asset/collection/portrait.png",
  );
  assert.equal(portraitOf({ portrait: null, configured: { memorialLobby: "mod:a" } }), null);
});

class El {
  children = [];
  dataset = {};
  style = {};
  attrs = {};
  setAttribute(k, v) {
    this.attrs[k] = v;
  }
  addEventListener() {}
  appendChild(el) {
    this.children.push(el);
  }
  insertBefore(el, before) {
    this.children.splice(before ? this.children.indexOf(before) : this.children.length, 0, el);
  }
  remove() {}
}

function harness(kind = "portrait", generated = false) {
  const requested = [],
    host = new El(),
    pictures = new Map(),
    renders = new Map();
  const rows = new Map(
    [0, 70, 150].map((top, key) => {
      pictures.set(key, deferred());
      renders.set(key, deferred());
      return [
        key,
        {
          top,
          items: [
            {
              x: 0,
              y: 0,
              e: {
                key,
                name: `Student ${key}`,
                mods: 0,
                portrait: kind === "portrait" ? "portrait" : null,
                memorialLobby: kind === "memorialLobby" ? "arona_workpage_daytime" : null,
              },
            },
          ],
        },
      ];
    }),
  );
  const env = {
    thumbnailWork: { signal: { aborted: false } },
    document: { createElement: () => new El() },
    rows,
    rendered: new Map(),
    dom: { scrollHost: host },
    hasAsset,
    CLIP_BOTTOM: 100,
    PITCH: 50,
    S: 1,
    ORIGIN: [0, 0],
    view: { scroll: 0 },
    clipTop: () => 0,
    ctx: {},
    clipStudentListBand: async () => {},
    cardTree: (e) => e,
    portraitOf: (e) => `portrait-${e.key}`,
    countText: () => "Portraits: 1, Memorial lobbies: 1",
    T: (key) => key,
    NGUI: {
      render: async (tree) => {
        requested.push(tree.key);
        await renders.get(tree.key).promise;
        return {
          images: generated ? [] : [pictures.get(tree.key).promise],
          pictures: [pictures.get(tree.key).promise],
        };
      },
    },
  };
  const api = new Function(
    "env",
    `const { ${Object.keys(env).join(", ")} } = env;
    ${rowsCode}
    return { syncRows };`,
  )(env);
  return { ...api, requested, pictures, renders, host, rows, signal: env.thumbnailWork.signal };
}

test("studentList cards keep their full accessible name and counts without a hover title", async () => {
  const h = harness();
  h.rows.get(0).items[0].e.mods = 2;
  h.rows.get(0).items[0].e.modCounts = { portrait: 1, memorialLobby: 1 };
  const pending = h.syncRows();
  const card = h.host.children[0].children[0];
  assert.equal(card.attrs["aria-label"], "Student 0 - Portraits: 1, Memorial lobbies: 1");
  assert.equal(card.title, undefined);
  assert.equal(card.attrs.title, undefined);
  for (const key of [0, 1]) {
    h.renders.get(key).resolve();
    h.pictures.get(key).resolve();
  }
  await pending;
});

for (const kind of ["portrait", "memorialLobby"])
  test(`visible generated ${kind} thumbnails complete before the student list is revealed`, async () => {
    const h = harness(kind, true);
    let ready = false;
    const showing = h.syncRows().then(() => {
      ready = true;
    });
    await flush();
    assert.equal(ready, false, "prefab construction still precedes reveal");
    for (const key of [0, 1]) h.renders.get(key).resolve();
    await flush();
    assert.equal(ready, false, "card frames cannot release the portrait barrier");
    h.pictures.get(0).resolve();
    await flush();
    assert.equal(ready, false);
    h.pictures.get(1).resolve();
    await showing;
    assert.equal(ready, true, "only offscreen thumbnails may remain unresolved");
    assert.deepEqual(h.requested, [0, 1, 2]);
  });

test("leaving the student list cancels overscan queued behind the visible rows", async () => {
  const h = harness();
  const pending = h.syncRows();
  h.signal.aborted = true;
  for (const key of [0, 1]) {
    h.renders.get(key).resolve();
    h.pictures.get(key).resolve();
  }
  await pending;
  await flush();
  assert.deepEqual(h.requested, [0, 1]);
});

for (const kind of ["portrait", "memorialLobby"])
  test(`the first screenful waits for every ${kind} picture before overscan is requested`, async () => {
    const h = harness(kind);
    let ready = false;
    const first = h.syncRows().then(() => {
      ready = true;
    });
    assert.deepEqual(h.requested, [0, 1]);
    h.renders.get(0).resolve();
    h.renders.get(1).resolve();
    await flush();
    assert.equal(ready, false, "rendered card geometry is insufficient");
    h.pictures.get(0).resolve();
    await flush();
    assert.equal(ready, false, "one finished portrait cannot reveal the row");
    h.pictures.get(1).resolve();
    await first;
    assert.equal(ready, true);
    assert.deepEqual(h.requested, [0, 1, 2]);
    // The off-screen portrait may stay pending without delaying the first frame.
  });

for (const kind of ["portrait", "memorialLobby"])
  test(`a repeated row sync still waits for rows whose ${kind} pictures are being prepared`, async () => {
    const h = harness(kind);
    const first = h.syncRows();
    let ready = false;
    const second = h.syncRows().then(() => {
      ready = true;
    });
    await flush();
    assert.equal(ready, false);
    assert.deepEqual(h.requested, [0, 1], "existing rows are reused");
    for (const key of [0, 1]) {
      h.renders.get(key).resolve();
      h.pictures.get(key).resolve();
    }
    await Promise.all([first, second]);
    assert.equal(ready, true);
  });

test("studentList fallback and configured-source stills receive the alt's default background", () => {
  const code = source.slice(source.indexOf("const portraitOf ="), source.indexOf("// --- rows near the view"));
  const requested = [],
    student = {
      key: "np0189",
      student: "student",
      portrait: "np0189_spr",
      background: "/bg/practice-room",
      officialThumbnail: null,
    };
  const env = {
    spineAllowed: () => true,
    thumbnailWork: { thumb: (...args) => requested.push(args) },
    data: { students: [{ key: "student", alts: [student] }] },
    listPicture,
  };
  const portraitOf = new Function("env", `const { ${Object.keys(env).join(", ")} } = env; ${code} return portraitOf;`)(
    env,
  );
  portraitOf(student);
  student.configured = { portrait: "mod:incoming" };
  portraitOf(student);
  assert.deepEqual(
    requested.map((args) => [args[0], args[1], args[5]]),
    [
      [spineBase("portrait", "np0189_spr"), "studentList", "/bg/practice-room"],
      [spineBase("mod", "incoming"), "studentList", "/bg/practice-room"],
    ],
  );
});

test("library-only student list cards use saved Mod previews without inventing local cores", () => {
  const code = source.slice(source.indexOf("const portraitOf ="), source.indexOf("// --- rows near the view"));
  const requested = [],
    alts = [];
  const env = {
    spineAllowed: () => true,
    thumbnailWork: {
      thumb: (...args) => {
        requested.push(args);
        return args[0];
      },
    },
    hasAsset,
    data: { students: [{ key: "assigned", alts }] },
    listPicture,
  };
  const portraitOf = new Function("env", `const { ${Object.keys(env).join(", ")} } = env; ${code} return portraitOf;`)(
    env,
  );
  for (const kind of ["portrait", "memorialLobby", "chibi"]) {
    const student = {
      key: kind,
      student: "assigned",
      portrait: null,
      memorialLobby: null,
      chibi: null,
      storedResourceTypes: [kind],
      configured: {},
    };
    alts.push(student);
    assert.equal(hasAsset(student), true);
    assert.equal(portraitOf(student), null, "preview opt-out stays unselected");
    student.previewSources = { [kind]: `mod:assigned-${kind}` };
    const result = portraitOf(student);
    if (kind === "chibi") assert.equal(result, null, "chibi cards never request Spine assets");
    else assert.ok(result.includes(`/mod/assigned-${kind}/`));
  }
  assert.equal(requested.length, 2);
});

test("the empty list is placed by emptyListY on the middle of the visible paper, whether or not the search box is open", () => {
  const code = source.slice(
    source.indexOf("function emptyTree() {"),
    source.indexOf("// the empty list's picture for the current items"),
  );
  assert.match(code, /emptyListY\(\(listTop\(\) \+ PAPER_BOTTOM\) \/ 2, message\)/);
  assert.doesNotMatch(code, /PAPER_TOP/, "the old shift by the search box's height is gone");
  const root = () => ({
    name: "LeftInfo_Empty",
    active: false,
    pos: [0, 53],
    widgets: [],
    children: [
      {
        name: "Label_Locale",
        pos: [0, -589],
        widgets: [{ text: "empty", fontSize: 48, w_: 146, h_: 56 }],
        children: [],
      },
    ],
  });
  const find = (tree, path) => path.split("/").reduce((n, part) => n?.children.find((c) => c.name === part), tree);
  const collection = { children: [{ name: "SeletControl", children: [root()] }] };
  for (const [top, centre] of [
    [334.5, -190],
    [182, -266.25],
  ]) {
    const calls = [];
    const emptyTree = new Function(
      "env",
      `const { layouts, clone, find, T, listTop, PAPER_BOTTOM, emptyListY } = env;\n${code}\nreturn emptyTree;`,
    )({
      layouts: { collection },
      clone: structuredClone,
      find: (tree, path) =>
        path === "SeletControl/LeftInfo_Empty" ? collection.children[0].children[0] : find(tree, path),
      T: (key) => `[${key}]`,
      listTop: () => top,
      PAPER_BOTTOM: -714.5,
      emptyListY: (...args) => {
        calls.push(args);
        return 1234;
      },
    });
    const tree = emptyTree();
    assert.equal(tree.active, true);
    assert.equal(tree.pos[1], 1234);
    assert.equal(calls.length, 1);
    assert.equal(calls[0][0], centre);
    assert.deepEqual([calls[0][1].text, calls[0][1].w_], ["[studentList.empty]", 1200]);
    assert.equal(collection.children[0].children[0].pos[1], 53, "the layout is cloned, never moved");
  }
});
