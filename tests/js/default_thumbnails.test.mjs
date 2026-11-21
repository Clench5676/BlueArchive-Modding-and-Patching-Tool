// The generated default thumbnails after an upgrade (web/js/app/default-thumbnails.js), under the start cover: which
// candidates are rendered, the two progress phases, what the server is told, and that a failing or slow picture is
// skipped rather than keeping the cover up.
import assert from "node:assert/strict";
import { test } from "node:test";

import { applyDefaultThumbnails, shownPortraits } from "../../src/bmpt/web/js/app/default-thumbnails.js";
import { spineBase } from "../../src/bmpt/web/js/core/api.js";

const alt = (key, extra = {}) => ({ key, student: "s", portrait: `${key}_spr`, configured: {}, ...extra });
const DATA = {
  students: [
    {
      key: "s",
      alts: [
        alt("a"),
        alt("b"),
        alt("lobby", { portrait: null, memorialLobby: "lobby_home" }),
        alt("chibi", { portrait: null, chibi: "chibi_model", storedResourceTypes: ["chibi"] }),
        alt("modded", { configured: { portrait: "mod:m1" } }),
      ],
    },
  ],
};

function harness({ candidates, due = true, render = async (p) => `blob:${p.base}`, jobs = {} } = {}) {
  const calls = [],
    progress = [],
    tasks = [];
  const rpc = async (method, params) => {
    calls.push([method, params]);
    if (method === "thumbnails.defaults") return { due, candidates };
    if (method === "thumbnails.applyDefaults") return params.alts.length ? "j1" : null;
    return { saved: "x" };
  };
  const waitJob = async (id, update) => {
    calls.push(["wait", id]);
    update({ label: "write", current: 1, total: 1 });
    return jobs[id] ?? { alts: [] };
  };
  return {
    calls,
    progress,
    tasks,
    run: (options = {}) =>
      applyDefaultThumbnails({
        rpc,
        waitJob,
        students: async () => DATA,
        render,
        png: async (url) => `data:image/png;base64,${url}`,
        spine: () => true,
        task: (run) => {
          tasks.push("task");
          return run((value) => progress.push(value));
        },
        ...options,
      }),
  };
}

test("only candidates whose card shows a generated portrait are rendered, with the card's picture", () => {
  const candidates = ["a", "lobby", "chibi", "modded", "unknown"].map((key) => ({ alt: key, builtin: null }));
  const shown = shownPortraits(candidates, DATA, true);
  assert.deepEqual(
    shown.map((item) => [item.alt, item.picture.base]),
    [
      ["a", spineBase("portrait", "a_spr")],
      ["modded", spineBase("mod", "m1")],
    ],
  );
  assert.deepEqual(shownPortraits(candidates, DATA, false), []);
});

test("pictures are rendered one after another with a count, stored, then written by the job", async () => {
  const order = [];
  const h = harness({
    candidates: [
      { alt: "a", builtin: null },
      { alt: "b", builtin: "k-b" },
      { alt: "modded", builtin: null },
      { alt: "lobby", builtin: null },
    ],
    render: async (p) => {
      order.push(p.base);
      await new Promise((resolve) => setTimeout(resolve, 1));
      order.push(`${p.base} done`);
      return `blob:${p.base}`;
    },
  });
  await h.run();
  const a = spineBase("portrait", "a_spr"),
    m = spineBase("mod", "m1");
  assert.deepEqual(order, [a, `${a} done`, m, `${m} done`], "one picture at a time, nothing waited for");
  assert.deepEqual(h.tasks, ["task"]);
  assert.deepEqual(
    h.progress.map((p) => [p.label, p.current, p.total]),
    [
      ["progress.defaultThumbnails", 1, 2],
      ["progress.defaultThumbnails", 2, 2],
      ["write", 1, 1],
    ],
    "the render phase counts the pictures, then the job's own progress",
  );
  assert.deepEqual(h.calls, [
    ["thumbnails.defaults", undefined],
    ["thumbnails.storeDefault", { alt: "a", png: `data:image/png;base64,blob:${a}` }],
    ["thumbnails.storeDefault", { alt: "modded", png: `data:image/png;base64,blob:${m}` }],
    ["thumbnails.applyDefaults", { alts: ["b", "a", "modded"], complete: true }],
    ["wait", "j1"],
  ]);
});

test("a failing, throwing or slow picture is skipped and leaves the run incomplete", async () => {
  const h = harness({
    candidates: [
      { alt: "a", builtin: null },
      { alt: "b", builtin: null },
      { alt: "modded", builtin: null },
    ],
    render: (p) =>
      p.base.includes("a_spr")
        ? Promise.resolve(null)
        : p.base.includes("b_spr")
          ? Promise.reject(new Error("render failed"))
          : new Promise(() => {}),
  });
  const warn = console.warn;
  console.warn = () => {};
  try {
    await h.run({ cap: 5 });
  } finally {
    console.warn = warn;
  }
  assert.deepEqual(h.calls.at(-1), ["thumbnails.applyDefaults", { alts: [], complete: false }]);
});

test("a run handed to a later job is waited for to its end", async () => {
  const h = harness({
    candidates: [{ alt: "b", builtin: "k-b" }],
    jobs: { j1: { alts: ["b"], next: "j2" }, j2: { alts: [] } },
  });
  assert.deepEqual(await h.run(), { alts: [] });
  assert.deepEqual(
    h.calls.filter(([method]) => method === "wait"),
    [
      ["wait", "j1"],
      ["wait", "j2"],
    ],
  );
});

test("nothing opens the cover's task when the server says nothing is due, or Spine is off", async () => {
  const off = harness({ due: false, candidates: [] });
  assert.equal(await off.run(), null);
  assert.deepEqual(
    off.calls.map(([method]) => method),
    ["thumbnails.defaults"],
  );
  assert.deepEqual(off.tasks, []);
  const spine = harness({ candidates: [{ alt: "a", builtin: null }] });
  assert.equal(await spine.run({ spine: () => false }), null);
  assert.deepEqual(spine.tasks, []);
});
