import assert from "node:assert/strict";
import { test } from "node:test";
import * as MemorialLobby from "../../src/bmpt/web/js/memorial-lobby/memorial-lobby.js";

test("official Student Hall memorial uses original art with full game metadata", async () => {
  const before = globalThis.fetch,
    requests = [],
    revision = "c".repeat(64);
  const info = { layers: [{ name: "student_hall_home", active: true, x: 12, y: -24, scale: 1, index: 0 }] };
  globalThis.fetch = async (url) => {
    requests.push(url);
    return {
      ok: true,
      json: async () =>
        url.endsWith("manifest.json")
          ? { revision }
          : url.endsWith("memorial-lobby.json")
            ? info
            : ["student_hall_home.skel", "student_hall_home.atlas"],
    };
  };
  try {
    const result = await MemorialLobby.layersOf("student_hall_home", null, null, null, { official: true });
    assert.ok(result.layers[0].spine.startsWith("/asset/spine/official-memorialLobby/student_hall_home/"));
    assert.equal(result.info, info);
    assert.ok(requests.includes(`/asset/spine/memorialLobby/student_hall_home/${revision}/memorial-lobby.json`));
  } finally {
    globalThis.fetch = before;
  }
});

test("library-only memorial has no request for a nonexistent local core", async () => {
  const before = globalThis.fetch,
    requests = [],
    revision = "d".repeat(64);
  globalThis.fetch = async (url) => {
    requests.push(url);
    return {
      ok: true,
      json: async () =>
        url.endsWith("manifest.json") ? { revision } : ["unreleased_home.skel", "unreleased_home.atlas"],
    };
  };
  try {
    const result = await MemorialLobby.layersOf(null, "unreleased");
    assert.equal(result.layers.length, 1);
    assert.ok(requests.every((url) => url.startsWith("/asset/spine/mod/unreleased/")));
  } finally {
    globalThis.fetch = before;
  }
});
