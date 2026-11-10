// chrome-motion.js: the top bar and the bottom menu on UILobby's UIAni_Lobby curves (ui-tokens.js LOBBY_ENTRY), their
// entry after the first screen, their absence in immersive scenes and their return at the window's edge. A fake DOM,
// clock, frame queue and timers; viewport 960 x 540, so the stage's scale is 0.5.
import assert from "node:assert/strict";
import { test } from "node:test";
import { readFileSync } from "node:fs";
import { LOBBY_ENTRY } from "../../src/bmpt/web/js/ui-tokens.js";

const source = readFileSync(new URL("../../src/bmpt/web/js/chrome-motion.js", import.meta.url), "utf8")
  .replace('import { reflowCorners } from "./corner-layout.js";', "const reflowCorners = () => {};")
  .replace('import { rpc } from "./api.js";', "const rpc = (...call) => { globalThis.motionRpc.push(call); return Promise.resolve(); };")
  .replace('import { LOBBY_ENTRY } from "./ui-tokens.js";', `import { LOBBY_ENTRY } from "${new URL("../../src/bmpt/web/js/ui-tokens.js", import.meta.url).href}";`);
globalThis.motionRpc = [];
const M = await import(`data:text/javascript;base64,${Buffer.from(source).toString("base64")}`);
const S = 1920 / 2960, near = (a, b, e = 1e-6) => assert.ok(Math.abs(a - b) <= e, `${a} vs ${b}`);

class Classes extends Set { toggle(name, on) { on ? this.add(name) : this.delete(name); return on; } }
const shift = (el) => parseFloat((el?.style.translate || "0 0px").split(" ")[1]) || 0;
class El {
  constructor(box) { this.box = box; this.style = {}; this.inert = false; this.kids = []; }
  getBoundingClientRect() {
    const b = this.box(), r = { left: b[0], top: b[1], width: b[2], height: b[3] };
    return { ...r, right: r.left + r.width, bottom: r.top + r.height };
  }
  querySelectorAll() { return this.kids; }
}
function harness({ native = false, lights = true } = {}) {
  let clock = 0, timerId = 0;
  const frames = [], timers = new Map(), calls = [], listeners = {}, docListeners = {}, changes = [];
  const view = { innerWidth: 960, innerHeight: 540, addEventListener: (name, fn) => { listeners[name] = fn; } };
  const wrapper = native ? new El(() => [0, shift(wrapper), 960, 32]) : null;
  // the header moves with its own translate (stage px) or with the native wrapper's (viewport px)
  const lift = () => (wrapper ? shift(wrapper) : shift(header) * .5);
  const header = new El(() => [0, lift(), 960, 32]);
  const back = new El(() => [44, 2 + lift(), 27, 45]); header.kids = [back];
  const nav = new El(() => [0, 488 + shift(nav) * .5, 960, 52]);
  const stage = new El(() => [0, 0, 960, 540]);
  const root = { classList: new Classes() };
  const doc = { documentElement: root, getElementById: (id) => (id === "stage" ? stage : null),
    querySelector: (sel) => ({ ".topbar": header, ".nav": nav, ".window-header": wrapper })[sel] ?? null,
    addEventListener: (name, fn) => { docListeners[name] = fn; } };
  const motion = M.createChromeMotion({ document: doc, window: view, raf: (fn) => frames.push(fn), now: () => clock,
    setTimeout: (fn, ms) => { timers.set(++timerId, { fn, at: clock + ms }); return timerId; },
    clearTimeout: (id) => timers.delete(id), rpc: async (name, value) => { calls.push({ name, value }); } });
  motion.setup({ onChange: () => changes.push(motion.state()), lights: () => lights });
  const run = (ms) => {
    const end = clock + ms;
    while (clock < end) {
      clock = Math.min(end, clock + 1000 / 60);
      for (const [id, t] of [...timers]) if (t.at <= clock) { timers.delete(id); t.fn(); }
      for (const fn of frames.splice(0)) fn();
    }
  };
  const move = (y) => listeners.pointermove({ clientX: 480, clientY: y });
  const leave = () => docListeners.mouseout({ relatedTarget: null });
  return { motion, header, nav, wrapper, root, view, calls, changes, timers, run, move, leave, lift };
}
const motions = (h) => h.calls.filter((c) => c.name === "window.motion").map((c) => c.value);

test("the clip's Hermite keys: values at the keys, no overshoot, the share left falls from 1 to 0", () => {
  for (const keys of [LOBBY_ENTRY.top, LOBBY_ENTRY.bottom]) {
    for (const [t, v] of keys) near(M.hermite(keys, t), v);
    let last = 1;
    for (let t = 0; t <= M.duration(keys) + 1e-9; t += .005) {
      const share = M.remaining(keys, t);
      assert.ok(share <= last + 1e-12 && share >= -1e-12, `${t}: ${share}`); last = share;
    }
    near(M.remaining(keys, M.duration(keys)), 0);
  }
  // 0.2 s into the clip the top bar is 28.55 units from its rest, the menu 31.52
  near(M.hermite(LOBBY_ENTRY.top, .2), 28.55); near(M.hermite(LOBBY_ENTRY.bottom, .2), -31.52);
  const list = M.frames(LOBBY_ENTRY.top, 100, 0);
  assert.equal(list.length, 27); assert.equal(list[0], 100); assert.equal(list.at(-1), 0);
  near(M.entryDistance(LOBBY_ENTRY.top, .5), 333.7 * S * .5); near(M.entryDistance(LOBBY_ENTRY.bottom, .5), 1045 * S * .5);
});

test("before the first screen both bars wait where the clip starts, hidden and out of reach", () => {
  const h = harness();
  assert.equal(h.header.style.translate, `0 ${-333.7 * S}px`, "stage pixels, the bar in the stage");
  assert.equal(h.nav.style.translate, `0 ${1045 * S}px`);
  assert.ok(h.root.classList.has("chrome-hide-top") && h.root.classList.has("chrome-hide-bottom"));
  assert.equal(h.motion.hidden("top"), true); assert.equal(h.motion.hidden("bottom"), true);
  h.motion.syncLights();
  assert.deepEqual(motions(h).at(-1).frames, [Math.round(333.7 * S * .5 * 100) / 100], "the window buttons wait with the bar");
});

test("enter() plays UIAni_Lobby on both bars and the window buttons, once", () => {
  const h = harness();
  h.motion.enter();
  assert.equal(h.motion.hidden("top"), false, "usable from the start of the entry");
  assert.ok(!h.root.classList.has("chrome-hide-top"));
  const sent = motions(h).at(-1);
  assert.equal(sent.period, M.FRAME); assert.equal(sent.frames.length, 27); assert.equal(sent.frames.at(-1), 0);
  near(sent.frames[0], 333.7 * S * .5, .01);
  h.run(200);
  near(shift(h.header), -28.55 * S, .6);
  h.run(300);
  assert.equal(h.header.style.translate, ""); assert.equal(h.nav.style.translate, "");
  assert.ok(h.changes.length >= 1);
  const count = motions(h).length; h.motion.enter(); assert.equal(motions(h).length, count);
});

test("the entry comes on its own when the first screen never reports", () => {
  const h = harness();
  h.run(M.ENTRY_FALLBACK - 100); assert.equal(h.motion.state().entered, false);
  h.run(200); assert.equal(h.motion.state().entered, true);
});

test("an immersive scene sends both bars away, as far as their painted edges, and gives the scene's inert state back", () => {
  const h = harness();
  h.motion.enter(); h.run(500);
  h.header.inert = true; h.nav.inert = true; // the scene holds them, as Hall and detail fullscreen do
  h.motion.setImmersive(true);
  assert.equal(h.motion.hidden("top"), true);
  h.run(500);
  near(shift(h.header) * .5, -(47 + M.EXTENT_MARGIN), .01); near(shift(h.nav) * .5, 52 + M.EXTENT_MARGIN, .01);
  assert.ok(h.root.classList.has("chrome-hide-top") && h.root.classList.has("chrome-hide-bottom"));
  assert.equal(h.header.inert, true); assert.equal(h.nav.inert, true);
  assert.deepEqual([h.view.bamtChromeInsets.top, h.view.bamtChromeInsets.bottom], [0, 0]);
  assert.equal(motions(h).at(-1).frames.at(-1), 47 + M.EXTENT_MARGIN, "the window buttons leave with the bar");
});

test("the pointer at an edge brings that edge's bar back, usable, and the scene's controls make room", () => {
  const h = harness();
  h.motion.enter(); h.run(500); h.header.inert = h.nav.inert = true; h.motion.setImmersive(true); h.run(500);
  h.move(200); h.run(100); assert.equal(h.motion.hidden("top"), true, "the middle of the window brings nothing");
  h.move(M.EDGE_BAND);
  assert.equal(h.motion.hidden("top"), false); assert.equal(h.motion.hidden("bottom"), true, "each edge on its own");
  assert.equal(h.header.inert, false, "a revealed bar takes clicks");
  assert.ok(!h.root.classList.has("chrome-hide-top"));
  h.run(500);
  assert.equal(h.header.style.translate, ""); assert.equal(motions(h).at(-1).frames.at(-1), 0);
  assert.equal(h.view.bamtChromeInsets.top, 47 + M.EXTENT_MARGIN);
  h.move(530); assert.equal(h.motion.hidden("bottom"), false); assert.equal(h.nav.inert, false);
  h.run(500); assert.equal(h.view.bamtChromeInsets.bottom, 52 + M.EXTENT_MARGIN);
});

test("a revealed bar leaves HIDE_DELAY after the pointer left it; coming back in time keeps it", () => {
  const h = harness();
  h.motion.enter(); h.run(500); h.header.inert = true; h.motion.setImmersive(true); h.run(500);
  h.move(4); h.run(500);
  h.move(40); h.run(M.HIDE_DELAY + 100); assert.equal(h.motion.hidden("top"), false, "still on the bar, Back included");
  h.move(300); h.run(M.HIDE_DELAY - 100); assert.equal(h.motion.hidden("top"), false);
  h.move(20); h.run(400); assert.equal(h.motion.hidden("top"), false, "back on the bar in time");
  h.move(300); h.run(M.HIDE_DELAY + 50);
  assert.equal(h.motion.hidden("top"), true); assert.equal(h.header.inert, true);
  h.run(500); assert.ok(h.root.classList.has("chrome-hide-top"));
});

test("leaving the window over a bar keeps it; leaving elsewhere lets it go", () => {
  const h = harness();
  h.motion.enter(); h.run(500); h.motion.setImmersive(true); h.run(500);
  h.move(2); h.run(500); h.leave(); h.run(M.HIDE_DELAY * 3);
  assert.equal(h.motion.hidden("top"), false, "the window buttons or the menu bar above");
  h.move(300); h.leave(); h.run(M.HIDE_DELAY + 50); assert.equal(h.motion.hidden("top"), true);
});

test("leaving the scene brings both bars back and forgets the revealed state", () => {
  const h = harness();
  h.motion.enter(); h.run(500); h.motion.setImmersive(true); h.run(500); h.move(1); h.run(500);
  h.motion.setImmersive(false);
  assert.equal(h.motion.hidden("top"), false); assert.equal(h.motion.hidden("bottom"), false);
  h.run(500);
  assert.equal(h.header.style.translate, ""); assert.equal(h.nav.style.translate, "");
  assert.deepEqual([h.view.bamtChromeInsets.top, h.view.bamtChromeInsets.bottom], [0, 0]);
  h.motion.setImmersive(true); h.run(500); assert.equal(h.motion.hidden("top"), true, "a new scene starts with the bar away");
});

test("the native toolbar's wrapper takes the offset in viewport pixels; a resized window keeps a bar at rest away", () => {
  const h = harness({ native: true });
  assert.equal(h.wrapper.style.translate, `0 ${-333.7 * S * .5}px`); assert.equal(h.header.style.translate, "");
  h.motion.enter(); h.run(500); h.motion.setImmersive(true); h.run(500);
  const before = motions(h).length;
  h.header.kids[0].box = () => [44, 2 + h.lift(), 27, 60]; // the window grew: Back now hangs to 62
  h.motion.apply();
  assert.equal(h.wrapper.style.translate, `0 ${-(62 + M.EXTENT_MARGIN)}px`);
  assert.equal(motions(h).length, before + 1); assert.equal(motions(h).at(-1).frames.length, 1);
});

test("no native window buttons, no motion for them", () => {
  const h = harness({ lights: false });
  h.motion.enter(); h.run(500); h.motion.syncLights();
  assert.equal(motions(h).length, 0);
});

test("boot() lets the bars in after the first screen, and after a failed one too", async () => {
  const main = readFileSync(new URL("../../src/bmpt/web/js/main.js", import.meta.url), "utf8");
  const code = main.slice(main.indexOf("async function boot()"), main.indexOf("async function start()"));
  for (const fail of [false, true]) {
    const entered = [];
    const env = { fitStage() {}, addEventListener() {}, timed: (_phase, work) => work(),
      Loading: { scene: async () => { if (fail) throw new Error("boot"); }, timings: () => ({}) },
      chromeMotion: () => ({ enter: () => entered.push(true) }), start() {}, globalThis: { BAMTRecovery: { ready: () => false } } };
    const boot = new Function("env", `const { ${Object.keys(env).join(", ")} } = env; ${code.slice(0, code.indexOf("\n}\n") + 3)} return boot;`)(env);
    await boot().catch(() => {});
    assert.equal(entered.length, 1, fail ? "a recovery page needs the window buttons" : "after the first screen");
  }
});

test("without a stand-in the motion goes to the App's own RPC, so the native window buttons get it", () => {
  globalThis.motionRpc.length = 0;
  const box = (b) => ({ getBoundingClientRect: () => ({ left: b[0], top: b[1], width: b[2], height: b[3], right: b[0] + b[2], bottom: b[1] + b[3] }),
    querySelectorAll: () => [], style: {} });
  const header = box([0, 0, 960, 32]), stage = box([0, 0, 960, 540]);
  const doc = { documentElement: { classList: { toggle() {} } }, getElementById: () => stage,
    querySelector: (sel) => (sel === ".topbar" ? header : null), addEventListener() {} };
  const motion = M.createChromeMotion({ document: doc, window: { innerWidth: 960, innerHeight: 540, addEventListener() {} },
    raf() {}, now: () => 0, setTimeout: () => 0, clearTimeout() {} });
  motion.setup({ lights: () => true });
  motion.syncLights();
  assert.equal(globalThis.motionRpc.at(-1)[0], "window.motion");
  assert.ok(globalThis.motionRpc.at(-1)[1].frames[0] > 0, "the buttons wait away with the bar before the first screen");
});
