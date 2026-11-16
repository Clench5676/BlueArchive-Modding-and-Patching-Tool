import assert from "node:assert/strict";
import { test } from "node:test";
import { freezeShown, freezeWhileLoading, previewLifecycle, previewTimer } from "../../src/bmpt/web/js/preview-lifecycle.js";
import { latestLoad, loadPlayer } from "../../src/bmpt/web/js/spine-load.js";
import { nextFrame } from "../../src/bmpt/web/js/async.js";

const tick = () => new Promise(setImmediate);
function globals(t, env) {
  for (const key of ["document", "window"]) {
    const before = Object.getOwnPropertyDescriptor(globalThis, key);
    globalThis[key] = env[key];
    t.after(() => { if (before) Object.defineProperty(globalThis, key, before); else delete globalThis[key]; });
  }
}

function surface() {
  const document = new EventTarget(), window = new EventTarget();
  document.hidden = false; document.focused = true; document.hasFocus = () => document.focused;
  return { document, window,
    hidden(value) { document.hidden = value; document.dispatchEvent(new Event("visibilitychange")); },
    focus(value) { document.focused = value; window.dispatchEvent(new Event(value ? "focus" : "blur")); },
  };
}
const contextEvent = (canvas, type) => {
  const event = new Event(`webglcontext${type}`, { cancelable: true }); canvas.dispatchEvent(event); return event;
};

test("visibility and focus transitions suspend once, and resume only when both are active", () => {
  const env = surface(), calls = [];
  const life = previewLifecycle({ suspend: () => calls.push("pause"), resume: () => calls.push("resume") }, env);
  env.focus(false); env.hidden(true); env.focus(true);
  assert.equal(life.active(), false); assert.deepEqual(calls, ["pause"]);
  env.hidden(false); env.focus(true);
  assert.equal(life.active(), true); assert.deepEqual(calls, ["pause", "resume"]);
  life.dispose(); env.focus(false); env.focus(true);
  assert.deepEqual(calls, ["pause", "resume"], "retired views have no listeners");
});

test("context loss during a hidden interval waits for restoration and foreground before rebuilding once", async () => {
  const env = surface(), canvas = new EventTarget(), calls = [];
  const life = previewLifecycle({ recover: () => calls.push("rebuild") }, env); life.watch(canvas);
  assert.equal(contextEvent(canvas, "lost").defaultPrevented, true);
  env.hidden(true); contextEvent(canvas, "restored"); await tick();
  assert.equal(life.active(), false); assert.deepEqual(calls, []);
  env.hidden(false); env.focus(true); await tick();
  assert.deepEqual(calls, ["rebuild"]); assert.equal(life.active(), true);
  life.dispose(); contextEvent(canvas, "lost"); contextEvent(canvas, "restored"); await tick();
  assert.deepEqual(calls, ["rebuild"]);
});

test("preview deadlines consume foreground time and retain their remaining budget", (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const env = surface(); let now = 0, expired = 0;
  const cancel = previewTimer(() => expired++, 1000, { ...env, now: () => now });
  now = 400; t.mock.timers.tick(400); env.focus(false);
  now += 60000; t.mock.timers.tick(60000); assert.equal(expired, 0);
  env.hidden(true); env.focus(true); t.mock.timers.tick(60000); assert.equal(expired, 0);
  env.hidden(false); t.mock.timers.tick(599); assert.equal(expired, 0);
  t.mock.timers.tick(1); assert.equal(expired, 1); cancel(); t.mock.timers.reset();
});

test("player loading and first-frame deadlines pause through a hidden window", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const env = surface(); globals(t, env);
  let p;
  const load = loadPlayer((config) => (p = { config, canvas: new EventTarget(), dispose() {} }), {}, { timeout: 1000 });
  env.hidden(true); t.mock.timers.tick(60000); await tick();
  assert.equal(load.active(), false);
  env.hidden(false); p.config.success(p);
  assert.equal(await load.ready, p); load.dispose(); t.mock.timers.reset();
});

test("an unfocused player initializes without starting background animation", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const env = surface(); env.focus(false); globals(t, env);
  let draws = 0, starts = 0;
  const load = loadPlayer((config) => ({ config, canvas: new EventTarget(),
    stopRendering() { this.stopped = true; }, startRendering() { starts++; }, dispose() {},
    drawFrame() { if (++draws === 2) config.success(this); },
  }), {});
  await tick(); assert.equal(draws, 1);
  t.mock.timers.tick(32); await tick();
  assert.equal(await load.ready, load.player);
  assert.equal(load.player.stopped, true);
  t.mock.timers.tick(1000); await tick();
  assert.equal(draws, 2); assert.equal(starts, 0);
  load.dispose(); t.mock.timers.reset();
});

test("frame readiness uses foreground frames but can finish after focus leaves", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const env = surface(); globals(t, env);
  const old = globalThis.requestAnimationFrame;
  let callback;
  globalThis.requestAnimationFrame = (fn) => { callback = fn; return 1; };
  t.after(() => { if (old) globalThis.requestAnimationFrame = old; else delete globalThis.requestAnimationFrame; });
  let ready = false;
  const frame = nextFrame().then(() => { ready = true; });
  t.mock.timers.tick(100); await tick(); assert.equal(ready, false);
  callback(); await frame; assert.equal(ready, true);
  const unfocused = nextFrame(); env.focus(false); t.mock.timers.tick(100); await unfocused;
  const controller = new AbortController();
  const cancelled = nextFrame({ signal: controller.signal }); controller.abort(); await cancelled;
  t.mock.timers.reset();
});

for (const kind of ["portrait", "lobby", "special L2D"]) test(`${kind} restarts without stale frames, rebuilds its context and ignores disposed events`, async (t) => {
  const env = surface(); globals(t, env);
  const queue = latestLoad(), players = [], errors = []; let frames = 0, draws = 0, resets = 0;
  const show = () => queue.run(async (signal) => {
    const load = loadPlayer((config) => {
      const p = { config, canvas: new EventTarget(), stopRequestAnimationFrame: false,
        time: { delta: 99, update() { this.delta = 99; } },
        stopRendering() { this.stopRequestAnimationFrame = true; },
        startRendering() { this.stopRequestAnimationFrame = false; },
        drawFrame() { this.config.frame(this); this.config.draw(this); },
        dispose() { this.disposed = true; } };
      players.push(p); queueMicrotask(() => config.success(p)); return p;
    }, { skeleton: `/${kind}/revision.skel`, frame() { frames++; }, draw() { draws++; } },
    { signal, onResume: () => resets++, onRecover: show, onError: (err) => errors.push(err) });
    return load.ready;
  });
  const p = await show();
  env.focus(false); p.drawFrame();
  assert.deepEqual([frames, draws], [0, 0], "the loop frame queued before the pause is skipped whole");
  p.drawFrame(false);
  assert.deepEqual([frames, draws], [1, 1], "a frame asked for directly runs every hook, never half of them");
  env.focus(true); await tick();
  assert.equal(p.stopRequestAnimationFrame, false); assert.equal(p.time.delta, 0); assert.equal(resets, 1);
  p.drawFrame(); assert.deepEqual([frames, draws], [2, 2]);
  contextEvent(p.canvas, "lost"); p.config.error(p, "Context lost"); await tick();
  assert.deepEqual(errors, []); assert.equal(players.length, 1, "wait for restoration");
  contextEvent(p.canvas, "restored"); await tick();
  assert.equal(players.length, 2); assert.equal(p.disposed, true);
  assert.equal(players[1].config.skeleton, p.config.skeleton);
  p.config.frame(p); contextEvent(p.canvas, "restored"); await tick();
  assert.equal(frames, 2); assert.equal(players.length, 2);
  players[1].config.error(players[1], "Invalid GPU draw"); await tick();
  assert.equal(errors.length, 1, "a foreground render failure is still reported"); queue.cancel();
});

test("a Spine load that times out says how far it got", async () => {
  const { loadPlayer } = await import("../../src/bmpt/web/js/spine-load.js");
  const assetManager = { getLoaded: () => 2, getToLoad: () => 1, hasErrors: () => false, getErrors: () => ({}) };
  const player = { assetManager, canvas: { width: 640, height: 360 }, context: { gl: { isContextLost: () => false } },
    stopRendering() {}, drawFrame() {}, dispose() {} };
  const { ready } = loadPlayer(() => player, { skeleton: "x.skel" }, { timeout: 30, manual: true });
  const error = await ready.then(() => null, (err) => err);
  assert.match(error.message, /^Spine load timed out \{/);
  const state = JSON.parse(error.message.slice("Spine load timed out ".length));
  assert.deepEqual([state.player, state.assetsReady, state.loaded, state.toLoad, state.errors, state.contextLost, state.canvas],
    [true, false, 2, 1, [], false, "640x360"]);
  assert.ok(state.frames >= 1 && state.ms >= 30);
  assert.equal(error.step, "load timeout");
});

test("Now Loading freezes the previews shown beneath it; one started behind it keeps preparing", () => {
  const env = surface(), calls = [];
  let shown = true;
  const life = previewLifecycle({ suspend: () => calls.push("pause"), resume: () => calls.push("resume"), shown: () => shown }, env);
  const hidden = previewLifecycle({ suspend: () => calls.push("hidden pause"), shown: () => false }, env);
  const timer = previewLifecycle({ suspend: () => calls.push("timer pause") }, env);
  freezeShown(true);
  assert.equal(life.active(), false); assert.equal(hidden.active(), true); assert.equal(timer.active(), true);
  const behind = previewLifecycle({ suspend: () => calls.push("behind pause"), shown: () => true }, env);
  assert.equal(behind.active(), true, "prepared behind Now Loading");
  env.focus(false); env.focus(true);
  assert.equal(life.active(), false, "focus alone does not thaw it");
  freezeShown(false);
  assert.equal(life.active(), true);
  assert.deepEqual(calls, ["pause", "hidden pause", "timer pause", "behind pause", "resume"], "a blur pauses the others as before");
  freezeShown(true);
  assert.equal(behind.active(), false, "the next Now Loading freezes it");
  freezeShown(false);
  for (const item of [life, hidden, timer, behind]) item.dispose();
  freezeShown(true); freezeShown(false);
  assert.equal(calls.filter((call) => call === "pause").length, 2, "retired previews are not frozen");
});

test("a member that throws while freezing does not keep the others running", () => {
  const calls = [];
  const bad = freezeWhileLoading({ freeze: () => { throw new Error("broken"); } });
  const good = freezeWhileLoading({ freeze: () => calls.push("freeze"), thaw: () => calls.push("thaw") });
  const error = console.error; console.error = () => {};
  try { freezeShown(true); freezeShown(false); } finally { console.error = error; }
  assert.deepEqual(calls, ["freeze", "thaw"]);
  bad.dispose(); good.dispose();
});

test("a loaded Spine player on screen stops under Now Loading and restarts after it", async (t) => {
  const env = surface(); globals(t, env);
  const old = globalThis.requestAnimationFrame;
  globalThis.requestAnimationFrame = (fn) => queueMicrotask(fn);
  t.after(() => { if (old) globalThis.requestAnimationFrame = old; else delete globalThis.requestAnimationFrame; });
  let frames = 0;
  const load = loadPlayer((config) => {
    const canvas = new EventTarget(); canvas.isConnected = true;
    const p = { config, canvas, stopRequestAnimationFrame: false, time: { delta: 5, update() {} },
      stopRendering() { this.stopRequestAnimationFrame = true; }, startRendering() { this.stopRequestAnimationFrame = false; },
      drawFrame() { frames++; }, dispose() {} };
    queueMicrotask(() => config.success(p)); return p;
  }, {});
  const p = await load.ready;
  freezeShown(true);
  assert.equal(p.stopRequestAnimationFrame, true); assert.equal(load.active(), false);
  const before = frames; p.drawFrame(); assert.equal(frames, before, "loop frames are skipped while frozen");
  p.drawFrame(false); assert.equal(frames, before + 1, "a frame asked for directly still draws");
  freezeShown(false); await tick();
  assert.equal(p.stopRequestAnimationFrame, false); assert.equal(p.time.delta, 0);
  load.dispose();
});

test("preview deadlines leave out the time a read waits for the library", async (t) => {
  const { setLibraryWaiting, LIBRARY_WAIT_CAP } = await import("../../src/bmpt/web/js/preview-lifecycle.js");
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const env = surface(); let now = 0, expired = 0;
  const advance = (ms) => { now += ms; t.mock.timers.tick(ms); };
  const cancel = previewTimer(() => expired++, 1000, { ...env, now: () => now });
  advance(400);
  setLibraryWaiting(true); advance(60000); assert.equal(expired, 0, "waiting for the library is not the preview's work");
  env.focus(false); env.focus(true); advance(5000); assert.equal(expired, 0, "focus does not restart it while waiting");
  setLibraryWaiting(false); advance(599); assert.equal(expired, 0);
  advance(1); assert.equal(expired, 1); cancel();
  // a wait whose end is never heard counts again after the cap
  let late = 0;
  const other = previewTimer(() => late++, 1000, { ...env, now: () => now });
  setLibraryWaiting(true); advance(LIBRARY_WAIT_CAP); advance(999); assert.equal(late, 0);
  advance(1); assert.equal(late, 1); other(); setLibraryWaiting(false);
  t.mock.timers.reset();
});

test("a Spine player restarted under Now Loading draws again once it ends", async (t) => {
  const env = surface(); globals(t, env);
  const old = globalThis.requestAnimationFrame, queued = [];
  globalThis.requestAnimationFrame = (fn) => { queued.push(fn); };
  t.after(() => { if (old) globalThis.requestAnimationFrame = old; else delete globalThis.requestAnimationFrame; });
  const flush = () => { for (const fn of queued.splice(0)) fn(); };
  let frames = 0;
  const load = loadPlayer((config) => {
    const canvas = new EventTarget(); canvas.isConnected = true;
    const p = { config, canvas, stopRequestAnimationFrame: false, time: { delta: 5, update() {} },
      stopRendering() { this.stopRequestAnimationFrame = true; },
      // the runtime's own loop: each frame asks for the next while it runs
      startRendering() { this.stopRequestAnimationFrame = false; requestAnimationFrame(() => this.drawFrame()); },
      drawFrame(loop = true) { if (loop && !this.stopRequestAnimationFrame) requestAnimationFrame(() => this.drawFrame()); frames++; },
      dispose() {} };
    queueMicrotask(() => config.success(p)); return p;
  }, {});
  const p = await load.ready;
  freezeShown(true);
  p.startRendering(); // an owner restarting its clock behind Now Loading (photography opening)
  flush();
  assert.equal(p.stopRequestAnimationFrame, true, "the skipped frame ended the loop");
  freezeShown(false); await tick(); flush(); flush();
  const before = frames; flush();
  assert.equal(frames, before + 1, "the loop runs again");
  assert.equal(p.stopRequestAnimationFrame, false);
  load.dispose(); freezeShown(false);
});

test("a clip fetch waiting for a job on the server does not reach its 15 s cap", async (t) => {
  const { setLibraryWaiting } = await import("../../src/bmpt/web/js/preview-lifecycle.js");
  const { withPreviewTimeout } = await import("../../src/bmpt/web/js/async.js");
  t.mock.timers.enable({ apis: ["setTimeout"] });
  let now = 0;
  const realNow = performance.now;
  performance.now = () => now;
  const advance = (ms) => { now += ms; t.mock.timers.tick(ms); };
  try {
    let answer;
    const fetched = new Promise((resolve) => { answer = resolve; });
    let outcome = null;
    withPreviewTimeout(fetched, 15000, "model clips").then((v) => { outcome = v; }, (err) => { outcome = err; });
    advance(1000);
    setLibraryWaiting(true, "job"); // preview.waiting: the clip waits for the model lock a job holds
    setLibraryWaiting(true); setLibraryWaiting(false); // a library wait ending meanwhile ends nothing else
    advance(30000);
    await Promise.resolve();
    assert.equal(outcome, null, "the wait for the job is not the clip's own time");
    setLibraryWaiting(false, "job");
    advance(13000); await Promise.resolve();
    assert.equal(outcome, null);
    answer("clip"); await Promise.resolve(); await Promise.resolve();
    assert.equal(outcome, "clip");
    // without the signal, the cap still holds for the clip's own time
    let late = null;
    withPreviewTimeout(new Promise(() => {}), 15000, "model clips").catch((err) => { late = err; });
    advance(15000); await Promise.resolve(); await Promise.resolve();
    assert.match(String(late), /timed out/);
  } finally {
    performance.now = realNow;
    t.mock.timers.reset();
  }
});
