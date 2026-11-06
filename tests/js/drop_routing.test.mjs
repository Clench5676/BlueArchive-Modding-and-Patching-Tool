// Only Import Mod takes files dropped on the window (BL-095): main.js routes drops there and refuses them elsewhere.
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const main = readFileSync(new URL("../../src/bmpt/web/js/main.js", import.meta.url), "utf8");
const block = main.slice(main.indexOf("  // Only Import Mod takes files dropped"), main.indexOf('  $("top-option").addEventListener'));

function harness(hash) {
  const env = { location: { hash }, dropped: [], notified: [], listeners: {}, handlers: {} };
  env.addEventListener = (type, fn) => { env.listeners[type] = fn; };
  env.on = (type, fn) => { env.handlers[type] = fn; };
  env.BatchImport = { drop: async (paths) => { env.dropped.push(paths); } };
  env.notify = (...args) => env.notified.push(args);
  env.errorText = (err) => String(err);
  new Function("env", `const { location, addEventListener, on, BatchImport, notify, errorText } = env; ${block}`)(env);
  return env;
}

const dragover = () => {
  const event = { prevented: false, dataTransfer: { dropEffect: "copy" } };
  event.preventDefault = () => { event.prevented = true; };
  return event;
};

test("files dropped on Import Mod reach the import list", async () => {
  for (const hash of ["#/import", "#/import/"]) {
    const env = harness(hash);
    await env.handlers.drop({ paths: ["a.bundle", "b.bundle"] });
    assert.deepEqual(env.dropped, [["a.bundle", "b.bundle"]]);
    const event = dragover();
    env.listeners.dragover(event);
    assert.equal(event.prevented, false);
    assert.equal(event.dataTransfer.dropEffect, "copy", "Import Mod sets its own drop effect");
  }
});

test("every other page refuses dropped files", async () => {
  for (const hash of ["#/roster", "#/student/hoshino/ch0258", "#/hall", ""]) {
    const env = harness(hash);
    await env.handlers.drop({ paths: ["a.bundle"] });
    assert.deepEqual(env.dropped, []);
    assert.deepEqual(env.notified, []);
    const event = dragover();
    env.listeners.dragover(event);
    assert.equal(event.prevented, true);
    assert.equal(event.dataTransfer.dropEffect, "none");
  }
});
