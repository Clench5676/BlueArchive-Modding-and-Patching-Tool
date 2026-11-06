import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { setStrings } from "../../src/bmpt/web/js/i18n.js";

globalThis.document = { documentElement: {}, createElement: () => ({ getContext: () => ({ measureText: () => ({ width: 0 }) }) }),
  getElementById: () => null, querySelector: () => null, addEventListener() {} };
globalThis.window = new EventTarget();
globalThis.addEventListener = () => {};
const { kindLabel, entryBase, promptEntries } = await import("../../src/bmpt/web/js/prompts.js");

const flat = (obj, path = "", out = {}) => {
  for (const [key, value] of Object.entries(obj)) {
    if (value && typeof value === "object") flat(value, `${path}${key}.`, out);
    else out[`${path}${key}`] = value;
  }
  return out;
};
setStrings("en-US", flat(JSON.parse(readFileSync(new URL("../../src/bmpt/locales/en-US.json", import.meta.url), "utf8"))));

test("model entries retain their kind in untracked and drift prompts without requesting a Spine preview", () => {
  const model = { kind: "model", core: "hoshino_original", install: "game", state: "untracked" };
  assert.equal(kindLabel(model), "3D Model");
  assert.equal(entryBase(model, { current: "game" }), null);
  assert.deepEqual(promptEntries([model], "untracked").listed, [model]);
  const reset = { ...model, state: "reset" };
  assert.deepEqual(promptEntries([reset], "drift").listed, [reset]);
});

test("generic entries retain their kind without requesting a Spine preview", () => {
  const entry = { kind: "other", core: "other:ui", variant: "ui", install: "game", state: "untracked" };
  assert.equal(kindLabel(entry), "Other Mods");
  assert.equal(entryBase(entry, { current: "game" }), null);
  assert.deepEqual(promptEntries([entry], "untracked").listed, [entry]);
});

test("configuration apply results report kept current files and incomplete mods once", async () => {
  const { driftSummary } = await import("../../src/bmpt/web/js/prompts.js");
  const { resultSummary } = await import("../../src/bmpt/web/js/mod-switch.js");
  const done = Array.from({ length: 2 }, () => ({ core: "test_spr", kept_current: true, incomplete: true }));
  for (const summary of [driftSummary({ done }), resultSummary({ enabled: true, done })]) {
    assert.equal(summary.lines.filter((line) => line === "Current game files added to the library").length, 1);
    assert.equal(summary.lines.filter((line) => line === "Mod incomplete").length, 1);
  }
});
