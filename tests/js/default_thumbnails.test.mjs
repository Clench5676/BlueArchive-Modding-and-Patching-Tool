// The generated default thumbnails after an upgrade (web/js/app/default-thumbnails.js): which candidates are rendered,
// that no picture starts while a preview is in the foreground, and what the server is told.
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

function harness({ candidates, due = true, render = async (p) => `blob:${p.base}`, busy = () => false } = {}) {
  const calls = [];
  const rpc = async (method, params) => {
    calls.push([method, params]);
    if (method === "thumbnails.defaults") return { due, candidates };
    if (method === "thumbnails.applyDefaults") return params.alts.length ? "j1" : null;
    return { saved: "x" };
  };
  return {
    calls,
    run: (options = {}) =>
      applyDefaultThumbnails({
        rpc,
        students: async () => DATA,
        render,
        png: async (url) => `data:image/png;base64,${url}`,
        spine: () => true,
        busy,
        wait: async () => {},
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

test("rendered pictures are stored, stored ones reused, and the batch is told it is complete", async () => {
  const h = harness({
    candidates: [
      { alt: "a", builtin: null },
      { alt: "b", builtin: "k-b" },
      { alt: "lobby", builtin: null },
    ],
  });
  assert.equal(await h.run(), "j1");
  assert.deepEqual(h.calls, [
    ["thumbnails.defaults", undefined],
    ["thumbnails.storeDefault", { alt: "a", png: `data:image/png;base64,blob:${spineBase("portrait", "a_spr")}` }],
    ["thumbnails.applyDefaults", { alts: ["a", "b"], complete: true }],
  ]);
});

test("a picture that fails leaves the run incomplete, so the next start tries again", async () => {
  const h = harness({
    candidates: [
      { alt: "a", builtin: null },
      { alt: "b", builtin: null },
    ],
    render: async (p) => (p.base.includes("a_spr") ? null : `blob:${p.base}`),
  });
  await h.run();
  assert.deepEqual(h.calls.at(-1), ["thumbnails.applyDefaults", { alts: ["b"], complete: false }]);
});

test("nothing is rendered or applied when the server says nothing is due, or Spine is off", async () => {
  const off = harness({ due: false, candidates: [] });
  assert.equal(await off.run(), null);
  assert.deepEqual(
    off.calls.map(([method]) => method),
    ["thumbnails.defaults"],
  );
  const spine = harness({ candidates: [{ alt: "a", builtin: null }] });
  assert.equal(await spine.run({ spine: () => false }), null);
  assert.deepEqual(
    spine.calls.map(([method]) => method),
    ["thumbnails.defaults"],
  );
});

test("no picture starts while a preview is in the foreground", async () => {
  const log = [];
  let foreground = 3;
  const h = harness({
    candidates: [
      { alt: "a", builtin: null },
      { alt: "b", builtin: null },
    ],
    busy: () => {
      const busy = foreground > 0;
      log.push(busy ? "busy" : "free");
      return busy;
    },
    render: async (p) => {
      log.push(`render ${p.base.includes("a_spr") ? "a" : "b"}`);
      foreground = p.base.includes("a_spr") ? 1 : 0; // a preview opens while a renders
      return `blob:${p.base}`;
    },
  });
  await h.run({
    wait: async () => {
      foreground--;
      log.push("wait");
    },
  });
  assert.deepEqual(log, [
    "busy",
    "wait",
    "busy",
    "wait",
    "busy",
    "wait",
    "free",
    "render a",
    "busy",
    "wait",
    "free",
    "render b",
  ]);
});
