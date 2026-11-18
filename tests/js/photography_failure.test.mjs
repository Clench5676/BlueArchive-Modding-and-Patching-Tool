import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
const source = readFileSync(new URL("../../src/bmpt/web/js/thumbnail-capture/controller.js", import.meta.url), "utf8");
const code = source.slice(source.indexOf("function errorText("), source.indexOf("function node("));
function setup() {
  const popups = [],
    forwarded = [];
  const env = {
    T: (key, params) => `T:${key}`,
    P: (key) => `P:${key}`,
    Popup: {
      message: async (options) => {
        popups.push(options);
      },
    },
  };
  const previous = globalThis.BAMTRecovery,
    error = console.error;
  globalThis.BAMTRecovery = { forward: (failure, kind) => forwarded.push([failure, kind]) };
  console.error = () => {};
  const safe = new Function("env", `const {${Object.keys(env)}}=env;${code};return safe;`)(env);
  return {
    safe,
    popups,
    forwarded,
    restore: () => {
      globalThis.BAMTRecovery = previous;
      console.error = error;
    },
  };
}
test("a thumbnail capture failure without a code reaches the diagnostics report as well as the popup", async () => {
  const h = setup();
  try {
    const failure = new Error("MemorialLobby image unavailable: /asset/spine/mod/x/fx_a.png");
    await h.safe(async () => {
      throw failure;
    })();
    assert.deepEqual(h.forwarded, [[failure, "thumbnailCapture"]]);
    assert.deepEqual(
      h.popups.map((popup) => popup.lines),
      [["P:captureFailed"]],
    );
  } finally {
    h.restore();
  }
});
test("coded and cancelled thumbnail capture failures stay out of the report", async () => {
  const h = setup();
  try {
    await h.safe(async () => {
      throw Object.assign(new Error("Renderer did not recover"), { code: "thumbnail_capture_unavailable" });
    })();
    await h.safe(async () => {
      throw new DOMException("Thumbnail capture ended", "AbortError");
    })();
    assert.deepEqual(h.forwarded, []);
    assert.deepEqual(
      h.popups.map((popup) => popup.lines),
      [["T:error.thumbnail_capture_unavailable"]],
    );
  } finally {
    h.restore();
  }
});
