import assert from "node:assert/strict";
import { test } from "node:test";
import { previewLifecycle, previewTimer } from "../../src/bmpt/web/js/preview-lifecycle.js";
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
  const queue = latestLoad(), players = [], errors = []; let frames = 0, resets = 0;
  const show = () => queue.run(async (signal) => {
    const load = loadPlayer((config) => {
      const p = { config, canvas: new EventTarget(), stopRequestAnimationFrame: false,
        time: { delta: 99, update() { this.delta = 99; } },
        stopRendering() { this.stopRequestAnimationFrame = true; },
        startRendering() { this.stopRequestAnimationFrame = false; },
        dispose() { this.disposed = true; } };
      players.push(p); queueMicrotask(() => config.success(p)); return p;
    }, { skeleton: `/${kind}/revision.skel`, frame() { frames++; } },
    { signal, onResume: () => resets++, onRecover: show, onError: (err) => errors.push(err) });
    return load.ready;
  });
  const p = await show();
  env.focus(false); p.config.frame(p); assert.equal(frames, 0);
  env.focus(true); await tick();
  assert.equal(p.stopRequestAnimationFrame, false); assert.equal(p.time.delta, 0); assert.equal(resets, 1);
  p.config.frame(p); assert.equal(frames, 1);
  contextEvent(p.canvas, "lost"); p.config.error(p, "Context lost"); await tick();
  assert.deepEqual(errors, []); assert.equal(players.length, 1, "wait for restoration");
  contextEvent(p.canvas, "restored"); await tick();
  assert.equal(players.length, 2); assert.equal(p.disposed, true);
  assert.equal(players[1].config.skeleton, p.config.skeleton);
  p.config.frame(p); contextEvent(p.canvas, "restored"); await tick();
  assert.equal(frames, 1); assert.equal(players.length, 2);
  players[1].config.error(players[1], "Invalid GPU draw"); await tick();
  assert.equal(errors.length, 1, "a foreground render failure is still reported"); queue.cancel();
});

test("a Spine load that times out says how far it got (BL-129)", async () => {
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
