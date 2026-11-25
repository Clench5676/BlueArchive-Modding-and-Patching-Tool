import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const main = readFileSync(new URL("../../src/bmpt/web/js/app/main.js", import.meta.url), "utf8");
const notice = main.slice(main.indexOf("function dataDirNotice()"), main.indexOf("// The start popups, one at a time"));

function harness(info) {
  const shown = [];
  const env = {
    info,
    T: (key, params) => `${key}${params ? JSON.stringify(params) : ""}`,
    Popup: {
      message: async (options) => {
        shown.push(options);
        return { button: "ok" };
      },
    },
  };
  const run = new Function(
    "env",
    `const { ${Object.keys(env).join(", ")} } = env; let dataDirNoticed = false; ${notice} return dataDirNotice;`,
  )(env);
  return { run, shown };
}

test("a writable or unreported data folder shows nothing", async () => {
  for (const info of [null, {}, { dataDirWritable: true, dataDir: "/data" }]) {
    const { run, shown } = harness(info);
    await run();
    assert.deepEqual(shown, []);
  }
});

test("a data folder that cannot be written shows one notice: the quit title, the path, Confirm only", async () => {
  const { run, shown } = harness({ dataDirWritable: false, dataDir: "/app/data" });
  await run();
  assert.equal(shown.length, 1);
  const [options] = shown;
  assert.equal(options.title, "quit.title");
  assert.deepEqual(options.lines, ['error.data_dir_readonly{"path":"/app/data"}']);
  assert.equal(options.ok, "picker.ok");
  assert.equal(options.cancel, undefined);
  assert.equal(options.buttons, undefined);
  assert.equal(options.error, undefined, "a notice, not a failure report");
});

function bootHarness(info, upgradedNotice = null) {
  const messages = [],
    sequences = [];
  const env = {
    info,
    upgradedNotice,
    startup: {},
    fitStage() {},
    addEventListener() {},
    timed: (_phase, work) => work(),
    start: async () => {},
    chromeMotion: () => ({ enter() {} }),
    rpc: async () => {},
    Cursor: { init() {} },
    $: () => ({}),
    console,
    setSpineConsent() {},
    T: (key, params) => `${key}${params ? JSON.stringify(params) : ""}`,
    Loading: { scene: (work) => work(), timings: () => ({}), busy: () => false, init() {} },
    Popup: {
      message: async (options) => {
        messages.push(options);
        return { button: "ok" };
      },
      sequence: async (steps) => {
        sequences.push(steps.map(([name]) => name));
        for (const [, run] of steps) await run();
      },
      whenIdle: async () => {},
    },
    ModSwitch: { current: () => ({}), startDue: () => false, tellCatalog: async () => {} },
    Tours: { start: async () => {} },
    document: { getElementById: () => null },
    Consent: { init() {}, show: async () => true, spine: async () => true, scan: async () => {} },
    Prompts: { start: async () => {}, isBusy: () => false },
    Impact: { ask: async () => null, isBusy: () => false },
    Update: { check: async () => null, afterUpgrade: async () => {} },
    route: async () => {},
  };
  const code = main.slice(main.indexOf("let sessionReady = null"), main.indexOf("async function start()"));
  const { boot, firstRun } = new Function(
    "env",
    `const { ${Object.keys(env).join(", ")} } = env; ${code} return { boot, firstRun };`,
  )(env);
  return { boot, firstRun, info, messages, sequences };
}

test("the start shows the notice once, ahead of the other start popups", async () => {
  const h = bootHarness({
    gameFound: true,
    dataDirWritable: false,
    dataDir: "/app/data",
    consent: true,
    spineConsent: true,
  });
  await h.boot();
  assert.equal(h.sequences.length, 1);
  assert.deepEqual(h.sequences[0], ["data-dir", "consent", "spine", "scan", "impact", "catalog", "drift", "untracked"]);
  assert.equal(h.messages.length, 1);
  assert.equal(h.messages[0].title, "quit.title");
});

test("a writable data folder leaves the start popups as they were", async () => {
  for (const dataDirWritable of [true, undefined]) {
    const h = bootHarness({ gameFound: true, dataDirWritable, dataDir: "/app/data" });
    await h.boot();
    assert.deepEqual(h.sequences, [["consent", "spine", "scan", "impact", "catalog", "drift", "untracked"]]);
    assert.deepEqual(h.messages, []);
  }
});

test("the no-game screen shows the notice too, and only when the folder cannot be written", async () => {
  const bad = bootHarness({ gameFound: false, dataDirWritable: false, dataDir: "/app/data" });
  await bad.boot();
  assert.equal(bad.messages.length, 1);
  assert.deepEqual(bad.sequences, [], "no other start popup without a game");
  const good = bootHarness({ gameFound: false, dataDirWritable: true, dataDir: "/app/data" });
  await good.boot();
  assert.deepEqual(good.messages, []);
});

test("a game folder chosen after the no-game notice does not show it again", async () => {
  const h = bootHarness({ gameFound: false, dataDirWritable: false, dataDir: "/app/data" });
  await h.boot();
  assert.equal(h.messages.length, 1);
  h.info.gameFound = true;
  await h.firstRun();
  assert.equal(h.sequences.length, 1, "the first-run sequence ran");
  assert.equal(h.messages.length, 1, "one notice per session");
});

test("an upgrade notice runs first in the start sequence, and once per session across the no-game screen", async () => {
  const order = [];
  let calls = 0;
  const notice = () => {
    calls++;
    order.push("upgraded");
    return Promise.resolve(true);
  };
  const withGame = bootHarness({ gameFound: true, dataDirWritable: false, dataDir: "/app/data" }, notice);
  await withGame.boot();
  assert.deepEqual(withGame.sequences, [
    ["upgraded", "data-dir", "consent", "spine", "scan", "impact", "catalog", "drift", "untracked"],
  ]);
  assert.equal(calls, 1);

  calls = 0;
  const noGame = bootHarness({ gameFound: false, dataDirWritable: false, dataDir: "/app/data" }, notice);
  await noGame.boot();
  assert.equal(calls, 1, "the no-game screen shows the notice before the data folder notice");
  assert.deepEqual(noGame.sequences, []);
  assert.equal(noGame.messages.length, 1);
});

test("a failing upgrade notice never stops the data folder notice or the start popups", async () => {
  const failing = () => Promise.reject(new Error("HTTP 500"));
  const noGame = bootHarness({ gameFound: false, dataDirWritable: false, dataDir: "/app/data" }, failing);
  await noGame.boot();
  assert.equal(noGame.messages.length, 1, "the data folder notice still shows");
  const noNotice = bootHarness({ gameFound: true, dataDirWritable: true }, null);
  await noNotice.boot();
  assert.deepEqual(noNotice.sequences, [["consent", "spine", "scan", "impact", "catalog", "drift", "untracked"]]);
});
