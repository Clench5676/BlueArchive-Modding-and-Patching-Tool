import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import vm from "node:vm";
import { coreOf, previewChanged } from "../../src/bmpt/web/js/switch-sources.js";

// Exercise the actual detail-screen apply callback without loading its renderers.
const source = readFileSync(new URL("../../src/bmpt/web/js/detail.js", import.meta.url), "utf8");
function functionSource(name) {
  const start = source.indexOf(`${name}(`);
  return source.slice(source.lastIndexOf("\n", start) + 1, source.indexOf("\n}\n", start) + 2);
}
const code = ["async function applyCandidate", "async function reload", "function resetPreview"].map(functionSource).join("\n");
async function apply(result, kind, { changedConfiguration = false } = {}) {
  const calls = [];
  const v = { key: "dress", spr: "dress_spr", lobby: "dress_home", model: "dress_model", configured: { [kind]: "mod:old" } };
  const next = structuredClone(v);
  if (changedConfiguration) next.configured[kind] = "mod:new";
  const enabled = !result.deferred;
  const ctx = {
    busy: false, isApplied: () => false, switchPopupPage: null, pageToken: 1,
    coreOf, variant: () => ctx.fam.variants[0], sourceOf: (key) => key, ctx: { invalidateRoster: () => calls.push("roster") },
    forget: () => calls.push("forget"), spinePrefix: (kind, core) => `${kind}/${core}`,
    call: (fn) => fn(), readFamily: async () => ({ key: "family", variants: [next] }),
    renderAll: async () => calls.push("cards"), renderLeft: async () => calls.push("left"),
    shownSource: () => "mod:old", selectionStatus: () => "switch.configured", appliedKey: () => "mod:old", previewChanged, modsOn: () => enabled, previewEnabled: enabled, tabKind: () => kind,
    changeInPlace: (fn) => fn(),
    clearTimeout() {}, open: true, fam: { key: "family", variants: [v] }, variantKey: v.key, shownLeft: kind,
    expression: "00", blinkTimer: null, mainPlayer: {},
    modelGeneration: 0, modelShot: {}, lobbyStale: false, lobbyShot: {}, lobbySeq: {}, lobbyAll: true, shownKey: "dress",
    job: async (_method, _params, opts) => { await opts.onDone(result); await opts.after(true, result); },
  };
  vm.createContext(ctx);
  await vm.runInContext(`${code}; applyCandidate("mod:test", "${kind}")`, ctx);
  return { calls, ctx };
}

test("unchanged applies and repeated saved choices keep thumbnails and the live portrait or lobby", async () => {
  for (const kind of ["spr", "lobby", "model"]) for (const deferred of [false, true]) {
    const { calls, ctx } = await apply({ files: [], unchanged: true, deferred }, kind);
    assert.deepEqual(calls, ["roster", "cards"]);
    assert.equal(ctx.lobbyStale, false); assert.ok(ctx.mainPlayer);
  }
});

test("saving a different source while mods are off refreshes the configured preview without evicting game thumbnails", async () => {
  for (const kind of ["spr", "lobby", "model"]) {
    const { calls, ctx } = await apply({ files: [], unchanged: true, deferred: true }, kind, { changedConfiguration: true });
    assert.deepEqual(calls, ["roster", "cards", "left"]);
    assert.equal(ctx.fam.variants[0].configured[kind], "mod:new");
    if (kind === "lobby") assert.equal(ctx.lobbyStale, true);
    else if (kind === "model") { assert.equal(ctx.modelShot, null); assert.ok(ctx.modelGeneration > 0); }
    else assert.equal(ctx.mainPlayer, null);
  }
});

test("changed game files still invalidate thumbnails and refresh the shown preview", async () => {
  for (const kind of ["spr", "lobby", "model"]) {
    const { calls, ctx } = await apply({ files: ["changed.bundle"], unchanged: false }, kind);
    assert.deepEqual(calls, ["forget", "forget", "roster", "cards", "left"]);
    if (kind === "lobby") assert.equal(ctx.lobbyStale, true);
    else if (kind === "model") { assert.equal(ctx.modelShot, null); assert.ok(ctx.modelGeneration > 0); }
    else assert.equal(ctx.mainPlayer, null);
  }
});

test("a lobby source event reloads the manifest even if a repack kept every family field unchanged", async () => {
  const calls = [], v = { key: "shun", lobby: "shun_home" };
  const ctx = {
    open: true, quiet: () => true, busy: false, heldChange: { cores: ["shun_home"] },
    fam: { key: "shun", variants: [v] }, variantKey: "shun", variant: () => v,
    readFamily: async () => ({ key: "shun", variants: [v] }), renderFailed: assert.fail,
    previewEnabled: true, modsOn: () => true, lower: (s) => String(s || "").toLowerCase(),
    filesSig: () => "unchanged", previewChanged: () => false, tab: "lobby", tabKind: () => "lobby",
    ctx: { invalidateRoster: () => calls.push("roster") }, resetPreview: (kind) => calls.push(kind),
    changeInPlace: (fn) => fn(), renderAll: async () => calls.push("panels"), renderLeft: async () => calls.push("preview"),
  };
  vm.createContext(ctx);
  await vm.runInContext(`${functionSource("async function reloadChanged")}; reloadChanged();`, ctx);
  assert.deepEqual(calls, ["roster", "lobby", "panels", "preview"]);
});

// FUN-26 for the detail screen: a family read waits for the job that holds the library lock, and an old read never
// replaces a newer family
test("the family read has no deadline of its own: it waits for a job that holds the library lock", async () => {
  const line = source.split("\n").find((l) => l.startsWith("const readFamily ="));
  assert.ok(line, "readFamily is defined");
  let release;
  const ctx = {
    rpc: (method, params) => new Promise((resolve) => { release = () => resolve({ method, key: params.key }); }),
    // a stand-in deadline that would have fired long before the job ends
    withTimeout: (promise, ms) => Promise.race([promise, new Promise((_, reject) => setTimeout(() => reject(new Error("timeout")), ms))]),
    HARD_CAP: 5,
  };
  vm.createContext(ctx);
  const pending = vm.runInContext(`${line}; readFamily("shun")`, ctx);
  await new Promise((resolve) => setTimeout(resolve, 40));
  release();
  assert.deepEqual(await pending, { method: "family.get", key: "shun" });
});

function deferred() {
  let resolve;
  const promise = new Promise((r) => { resolve = r; });
  return { promise, resolve };
}

test("a family read that finishes after the screen was read anew does not replace the newer family", async () => {
  const calls = [], v = { key: "shun", lobby: "shun_home" };
  const read = deferred(), newer = { key: "shun", variants: [v], newer: true };
  const ctx = {
    open: true, quiet: () => true, busy: false, heldChange: { cores: ["shun_home"] },
    fam: { key: "shun", variants: [v] }, variantKey: "shun", variant: () => v,
    readFamily: () => read.promise, renderFailed: assert.fail,
    previewEnabled: true, modsOn: () => true, lower: (s) => String(s || "").toLowerCase(), mergeChange: () => ({ cores: ["shun_home"] }),
    filesSig: () => "unchanged", previewChanged: () => false, tab: "lobby", tabKind: () => "lobby",
    ctx: { invalidateRoster: () => calls.push("roster") }, resetPreview: (kind) => calls.push(kind),
    changeInPlace: (fn) => fn(), renderAll: async () => calls.push("panels"), renderLeft: async () => calls.push("preview"),
  };
  vm.createContext(ctx);
  const running = vm.runInContext(`${functionSource("async function reloadChanged")}; reloadChanged();`, ctx);
  ctx.fam = newer; // the screen was left and entered again, or reloaded by another action, while the read waited
  ctx.heldChange = undefined;
  read.resolve({ key: "shun", variants: [{ ...v, old: true }] });
  await running;
  assert.equal(ctx.fam, newer, "the old read is dropped");
  assert.deepEqual(calls, []);
  assert.deepEqual(ctx.heldChange, { cores: ["shun_home"] }, "the change is read again after the newer family");
});

test("reload and a switch's reload drop a read that finishes after the screen was read anew", async () => {
  const calls = [], v = { key: "dress", spr: "dress_spr", lobby: "dress_home", model: "dress_model", configured: {} };
  for (const entry of ["reload()", 'applyCandidate("mod:test", "spr")']) {
    const read = deferred(), newer = { key: "family", variants: [v], newer: true };
    const ctx = {
      busy: false, isApplied: () => false, switchPopupPage: null, pageToken: 1,
      coreOf, variant: () => ctx.fam.variants[0], sourceOf: (key) => key, ctx: { invalidateRoster: () => calls.push("roster") },
      forget() {}, spinePrefix: (kind, core) => `${kind}/${core}`,
      call: (fn) => fn(), readFamily: () => read.promise,
      renderAll: async () => calls.push("cards"), renderLeft: async () => calls.push("left"),
      shownSource: () => "mod:old", selectionStatus: () => "switch.configured", appliedKey: () => "mod:old", previewChanged,
      modsOn: () => true, previewEnabled: true, tabKind: () => "spr", previewKey: () => "k", changeInPlace: (fn) => fn(),
      clearTimeout() {}, open: true, fam: { key: "family", variants: [v] }, variantKey: v.key, shownLeft: "spr",
      expression: "00", blinkTimer: null, mainPlayer: {}, modelGeneration: 0, modelShot: {}, lobbyStale: false, lobbyShot: {},
      lobbySeq: {}, lobbyAll: true, shownKey: "dress", previewEnabledNow: true,
      job: async (_method, _params, opts) => { await opts.after(true, { files: [], unchanged: true }); },
    };
    vm.createContext(ctx);
    const running = vm.runInContext(`${code}; ${entry}`, ctx);
    ctx.fam = newer;
    read.resolve({ key: "family", variants: [{ ...v, old: true }] });
    await running;
    assert.equal(ctx.fam, newer, `${entry}: the old read is dropped`);
    assert.ok(!calls.includes("cards") && !calls.includes("left"), `${entry}: nothing is drawn from it`);
    calls.length = 0;
  }
});
