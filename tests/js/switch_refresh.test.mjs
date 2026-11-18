import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { DETAIL_MODULES } from "./helpers/source-text.mjs";
import vm from "node:vm";
import { coreOf, previewChanged } from "../../src/bmpt/web/js/mods/switch-sources.js";

// Exercise the actual detail-screen apply callback without loading its renderers.
// the detail screen's modules read as one, their exports as plain declarations
const source = DETAIL_MODULES.map((file) =>
  readFileSync(new URL(`../../src/bmpt/web/js/detail/${file}`, import.meta.url), "utf8"),
)
  .join("\n")
  .replace(/^export /gm, "");
function functionSource(name) {
  const start = source.indexOf(`${name}(`);
  return source.slice(source.lastIndexOf("\n", start) + 1, source.indexOf("\n}\n", start) + 2);
}
const code = [
  "async function applyCandidate",
  "async function reload",
  "function resetPreview",
  // detail.js's setters of the state these write
  ...[
    "setFam",
    "setLobbyStale",
    "setLobbyShot",
    "setShownKey",
    "setModelShot",
    "setExpressionState",
    "setMainPlayer",
  ].map((name) => `function ${name}`),
]
  .map(functionSource)
  .join("\n");
const setFam = functionSource("function setFam");
async function apply(result, kind, { changedConfiguration = false } = {}) {
  const calls = [];
  const v = {
    key: "dress",
    portrait: "dress_spr",
    memorialLobby: "dress_home",
    chibi: "dress_model",
    configured: { [kind]: "mod:old" },
  };
  const next = structuredClone(v);
  if (changedConfiguration) next.configured[kind] = "mod:new";
  const enabled = !result.deferred;
  const ctx = {
    busy: false,
    isApplied: () => false,
    unfitCostume: () => false,
    switchPopupPage: null,
    pageToken: 1,
    coreOf,
    alt: () => ctx.shownStudent.alts[0],
    sourceOf: (key) => key,
    ctx: { invalidateRoster: () => calls.push("roster") },
    forget: () => calls.push("forget"),
    spinePrefix: (kind, core) => `${kind}/${core}`,
    call: (fn) => fn(),
    readStudent: async () => ({ key: "student", alts: [next] }),
    renderAll: async () => calls.push("cards"),
    renderLeft: async () => calls.push("left"),
    shownSource: () => "mod:old",
    selectionStatus: () => "switch.configured",
    appliedKey: () => "mod:old",
    previewChanged,
    modsOn: () => enabled,
    previewEnabled: enabled,
    tabKind: () => kind,
    changeInPlace: (fn) => fn(),
    clearTimeout() {},
    open: true,
    shownStudent: { key: "student", alts: [v] },
    altId: v.key,
    shownLeft: kind,
    expression: "00",
    blinkTimer: null,
    mainPlayer: {},
    modelGeneration: 0,
    modelShot: {},
    lobbyStale: false,
    lobbyShot: {},
    lobbySeq: {},
    lobbyAll: true,
    shownKey: "dress",
    job: async (_method, _params, opts) => {
      await opts.onDone(result);
      await opts.after(true, result);
    },
  };
  vm.createContext(ctx);
  await vm.runInContext(`${code}; applyCandidate("mod:test", "${kind}")`, ctx);
  return { calls, ctx };
}

test("unchanged applies and repeated saved choices keep thumbnails and the live portrait or lobby", async () => {
  for (const kind of ["portrait", "memorialLobby", "chibi"])
    for (const deferred of [false, true]) {
      const { calls, ctx } = await apply({ files: [], unchanged: true, deferred }, kind);
      assert.deepEqual(calls, ["roster", "cards"]);
      assert.equal(ctx.lobbyStale, false);
      assert.ok(ctx.mainPlayer);
    }
});

test("saving a different source while mods are off refreshes the configured preview without evicting game thumbnails", async () => {
  for (const kind of ["portrait", "memorialLobby", "chibi"]) {
    const { calls, ctx } = await apply({ files: [], unchanged: true, deferred: true }, kind, {
      changedConfiguration: true,
    });
    assert.deepEqual(calls, ["roster", "cards", "left"]);
    assert.equal(ctx.shownStudent.alts[0].configured[kind], "mod:new");
    if (kind === "memorialLobby") assert.equal(ctx.lobbyStale, true);
    else if (kind === "chibi") {
      assert.equal(ctx.modelShot, null);
      assert.ok(ctx.modelGeneration > 0);
    } else assert.equal(ctx.mainPlayer, null);
  }
});

test("changed game files still invalidate thumbnails and refresh the shown preview", async () => {
  for (const kind of ["portrait", "memorialLobby", "chibi"]) {
    const { calls, ctx } = await apply({ files: ["changed.bundle"], unchanged: false }, kind);
    assert.deepEqual(calls, ["forget", "forget", "roster", "cards", "left"]);
    if (kind === "memorialLobby") assert.equal(ctx.lobbyStale, true);
    else if (kind === "chibi") {
      assert.equal(ctx.modelShot, null);
      assert.ok(ctx.modelGeneration > 0);
    } else assert.equal(ctx.mainPlayer, null);
  }
});

test("a lobby source event reloads the manifest even if a repack kept every student field unchanged", async () => {
  const calls = [],
    v = { key: "shun", memorialLobby: "shun_home" };
  const ctx = {
    open: true,
    quiet: () => true,
    busy: false,
    heldChange: { cores: ["shun_home"] },
    shownStudent: { key: "shun", alts: [v] },
    altId: "shun",
    alt: () => v,
    readStudent: async () => ({ key: "shun", alts: [v] }),
    renderFailed: assert.fail,
    previewEnabled: true,
    modsOn: () => true,
    lower: (s) => String(s || "").toLowerCase(),
    filesSig: () => "unchanged",
    previewChanged: () => false,
    tab: "lobby",
    tabKind: () => "memorialLobby",
    ctx: { invalidateRoster: () => calls.push("roster") },
    resetPreview: (kind) => calls.push(kind),
    changeInPlace: (fn) => fn(),
    renderAll: async () => calls.push("panels"),
    renderLeft: async () => calls.push("preview"),
  };
  vm.createContext(ctx);
  await vm.runInContext(`${setFam}\n${functionSource("async function reloadChanged")}; reloadChanged();`, ctx);
  assert.deepEqual(calls, ["roster", "memorialLobby", "panels", "preview"]);
});

test("prefab and dependency source events refresh the model while foreign cores stay outside the student", async () => {
  for (const core of ["ch0258_02", "ch0258", "ch0258_01_enemy"]) {
    const calls = [],
      v = { key: "ch0258", chibi: "ch0258_02", chibiCores: ["ch0258_02", "ch0258"] };
    const ctx = {
      open: true,
      quiet: () => true,
      busy: false,
      heldChange: undefined,
      shownStudent: { key: "hoshino", alts: [v] },
      altId: v.key,
      alt: () => v,
      readStudent: async () => ({ key: "hoshino", alts: [v] }),
      renderFailed: assert.fail,
      previewEnabled: true,
      modsOn: () => true,
      lower: (s) => String(s || "").toLowerCase(),
      filesSig: () => "unchanged",
      previewChanged: () => false,
      tabKind: () => "chibi",
      ctx: { invalidateRoster: () => calls.push("roster") },
      resetPreview: (kind) => calls.push(kind),
      changeInPlace: (fn) => fn(),
      renderAll: async () => calls.push("panels"),
      renderLeft: async () => calls.push("preview"),
      mergeChange: (_a, b) => b,
      resumeChanged: () => calls.push("queued"),
    };
    vm.createContext(ctx);
    const gate = functionSource("function filesChanged");
    await vm.runInContext(
      `${setFam}\n${gate}; ${functionSource("async function reloadChanged")};
      filesChanged({cores: [${JSON.stringify(core)}]}); reloadChanged();`,
      ctx,
    );
    assert.deepEqual(calls, core === "ch0258_01_enemy" ? [] : ["queued", "roster", "chibi", "panels", "preview"]);
  }
});

// On the detail screen a student read waits for the job that holds the library lock, and an old read never
// replaces a newer student
test("the student read has no deadline of its own: it waits for a job that holds the library lock", async () => {
  const line = source.split("\n").find((l) => l.startsWith("const readStudent ="));
  assert.ok(line, "readStudent is defined");
  let release;
  const ctx = {
    rpc: (method, params) =>
      new Promise((resolve) => {
        release = () => resolve({ method, key: params.key });
      }),
    // a stand-in deadline that would have fired long before the job ends
    withTimeout: (promise, ms) =>
      Promise.race([promise, new Promise((_, reject) => setTimeout(() => reject(new Error("timeout")), ms))]),
    HARD_CAP: 5,
  };
  vm.createContext(ctx);
  const pending = vm.runInContext(`${line}; readStudent("shun")`, ctx);
  await new Promise((resolve) => setTimeout(resolve, 40));
  release();
  assert.deepEqual(await pending, { method: "student.get", key: "shun" });
});

function deferred() {
  let resolve;
  const promise = new Promise((r) => {
    resolve = r;
  });
  return { promise, resolve };
}

test("a student read that finishes after the screen was read anew does not replace the newer student", async () => {
  const calls = [],
    v = { key: "shun", memorialLobby: "shun_home" };
  const read = deferred(),
    newer = { key: "shun", alts: [v], newer: true };
  const ctx = {
    open: true,
    quiet: () => true,
    busy: false,
    heldChange: { cores: ["shun_home"] },
    shownStudent: { key: "shun", alts: [v] },
    altId: "shun",
    alt: () => v,
    readStudent: () => read.promise,
    renderFailed: assert.fail,
    previewEnabled: true,
    modsOn: () => true,
    lower: (s) => String(s || "").toLowerCase(),
    mergeChange: () => ({ cores: ["shun_home"] }),
    filesSig: () => "unchanged",
    previewChanged: () => false,
    tab: "lobby",
    tabKind: () => "memorialLobby",
    ctx: { invalidateRoster: () => calls.push("roster") },
    resetPreview: (kind) => calls.push(kind),
    changeInPlace: (fn) => fn(),
    renderAll: async () => calls.push("panels"),
    renderLeft: async () => calls.push("preview"),
  };
  vm.createContext(ctx);
  const running = vm.runInContext(
    `${setFam}\n${functionSource("async function reloadChanged")}; reloadChanged();`,
    ctx,
  );
  ctx.shownStudent = newer; // the screen was left and entered again, or reloaded by another action, while the read waited
  ctx.heldChange = undefined;
  read.resolve({ key: "shun", alts: [{ ...v, old: true }] });
  await running;
  assert.equal(ctx.shownStudent, newer, "the old read is dropped");
  assert.deepEqual(calls, []);
  assert.deepEqual(ctx.heldChange, { cores: ["shun_home"] }, "the change is read again after the newer student");
});

test("reload and a switch's reload drop a read that finishes after the screen was read anew", async () => {
  const calls = [],
    v = { key: "dress", portrait: "dress_spr", memorialLobby: "dress_home", chibi: "dress_model", configured: {} };
  for (const entry of ["reload()", 'applyCandidate("mod:test", "portrait")']) {
    const read = deferred(),
      newer = { key: "student", alts: [v], newer: true };
    const ctx = {
      busy: false,
      isApplied: () => false,
      unfitCostume: () => false,
      switchPopupPage: null,
      pageToken: 1,
      coreOf,
      alt: () => ctx.shownStudent.alts[0],
      sourceOf: (key) => key,
      ctx: { invalidateRoster: () => calls.push("roster") },
      forget() {},
      spinePrefix: (kind, core) => `${kind}/${core}`,
      call: (fn) => fn(),
      readStudent: () => read.promise,
      renderAll: async () => calls.push("cards"),
      renderLeft: async () => calls.push("left"),
      shownSource: () => "mod:old",
      selectionStatus: () => "switch.configured",
      appliedKey: () => "mod:old",
      previewChanged,
      modsOn: () => true,
      previewEnabled: true,
      tabKind: () => "portrait",
      previewKey: () => "k",
      changeInPlace: (fn) => fn(),
      clearTimeout() {},
      open: true,
      shownStudent: { key: "student", alts: [v] },
      altId: v.key,
      shownLeft: "portrait",
      expression: "00",
      blinkTimer: null,
      mainPlayer: {},
      modelGeneration: 0,
      modelShot: {},
      lobbyStale: false,
      lobbyShot: {},
      lobbySeq: {},
      lobbyAll: true,
      shownKey: "dress",
      previewEnabledNow: true,
      job: async (_method, _params, opts) => {
        await opts.after(true, { files: [], unchanged: true });
      },
    };
    vm.createContext(ctx);
    const running = vm.runInContext(`${code}; ${entry}`, ctx);
    ctx.shownStudent = newer;
    read.resolve({ key: "student", alts: [{ ...v, old: true }] });
    await running;
    assert.equal(ctx.shownStudent, newer, `${entry}: the old read is dropped`);
    assert.ok(!calls.includes("cards") && !calls.includes("left"), `${entry}: nothing is drawn from it`);
    calls.length = 0;
  }
});
