// The roster's prefabs are asked for once the first screen of a start with a game is up (main.js start()), never
// awaited, and Roster.show() takes up that same request. start() and roster.js's prefab code run for real on stub
// collaborators.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const main = readFileSync(new URL("../../src/bmpt/web/js/main.js", import.meta.url), "utf8");
const roster = readFileSync(new URL("../../src/bmpt/web/js/roster.js", import.meta.url), "utf8");
const startCode = main.slice(main.indexOf("async function start()"), main.indexOf("\nboot()"));
const prefabCode = roster.slice(roster.indexOf("const PREFABS ="), roster.indexOf("// Show the roster from its saved data."));
const flush = async () => { for (let i = 0; i < 20; i++) await Promise.resolve(); };

const deferred = () => { let resolve; const promise = new Promise((r) => { resolve = r; }); return { promise, resolve }; };

function runStart(gameFound) {
  const log = [], screen = deferred();
  const el = () => ({ setAttribute() {}, addEventListener() {}, hidden: false });
  const env = {
    ctx: {}, ROOT_ROUTE: "#/", variantNames: {}, routeToken: 0,
    timed: (_phase, work) => work(),
    rpc: async (method) => (method === "app.info" ? { gameFound, resourceContext: "rev-2" } : {}),
    NGUI: { setResourceContext() {}, init: async () => {}, setTierTags() {} },
    Consent: { init() {} }, setSpineConsent() {}, Loading: { init() {}, relabel() {} },
    previewCache: { configure() {} }, setStrings() {}, T: (key) => key, notify() {}, errorText: String,
    document: { title: "", querySelector: el, fonts: { ready: Promise.resolve() } }, $: el,
    on() {}, addEventListener() {}, location: { hash: "#/" }, history: { replaceState() {} },
    renderNav() {}, ModSwitch: { init: async () => {}, current: () => null, onChange() {} },
    Prompts: { init() {} }, Impact: { init() {}, gate: async () => { log.push(["update-check"]); } },
    Detail: {}, StudentHall: {}, Settings: {}, Popup: {}, BatchImport: {},
    refreshAfterToggle() {}, libraryChanged() {}, openMod() {}, refreshConfiguration() {}, watchFront() {},
    openSettings() {}, goHome() {},
    noGame: () => log.push(["no-game"]),
    route: () => { log.push(["route"]); return screen.promise; },
    Roster: { init() {}, preload: () => log.push(["preload"]) },
  };
  const run = new Function("env", `const { ${Object.keys(env).join(", ")} } = env;
    let info, startWithGame;
    ${startCode}
    return start();`);
  return { log, screen, done: run(env) };
}

test("a start with a game asks for the roster's prefabs once its first screen is up, without waiting for them", async () => {
  const { log, screen, done } = runStart(true);
  await flush();
  assert.deepEqual(log, [["update-check"], ["route"]], "the first screen's exports go first");
  screen.resolve();
  await done;
  assert.deepEqual(log, [["update-check"], ["route"], ["preload"]]);
});

test("a start without a game asks for no prefabs", async () => {
  const { log, done } = runStart(false);
  await done;
  assert.deepEqual(log, [["no-game"]]);
});

function prefabs(layout) {
  return new Function("NGUI", `${prefabCode.replace("export function", "function")}\nreturn { PREFABS, prefabs, preload };`)({ layout });
}

test("show() and preload ask for the same prefabs, through the shared layout cache", async () => {
  const requested = [], cache = new Map();
  const { PREFABS, prefabs: load, preload } = prefabs((name) => {
    if (!cache.has(name)) { requested.push(name); cache.set(name, Promise.resolve({ name })); }
    return cache.get(name);
  });
  assert.deepEqual(PREFABS, ["UICharacterCollection", "UICharacterCollectionPopulatorUnit", "UIPopup_GachaCharList.Btn_Checkbox"]);
  preload();
  const trees = await load();
  assert.deepEqual(trees.map((tree) => tree.name), PREFABS);
  assert.deepEqual(requested, PREFABS, "each prefab is requested once");
  assert.match(roster, /const \[collection, unit, checkbox\] = await prefabs\(\);/, "show() takes the shared request");
});

test("a failed preload is swallowed and asked for again by show()", async () => {
  let calls = 0, unhandled = 0;
  const onUnhandled = () => { unhandled++; };
  process.on("unhandledRejection", onUnhandled);
  try {
    const { prefabs: load, preload } = prefabs((name) => {
      calls++;
      return calls <= 3 ? Promise.reject(new Error(`cold ${name}`)) : Promise.resolve({ name });
    });
    preload();
    await flush();
    await new Promise((resolve) => setImmediate(resolve));
    assert.equal(unhandled, 0);
    const trees = await load();
    assert.equal(trees.length, 3);
    assert.equal(calls, 6);
  } finally {
    process.off("unhandledRejection", onUnhandled);
  }
});
