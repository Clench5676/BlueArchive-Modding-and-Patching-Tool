// Dragging the roster list (web/js/roster.js): the window listeners run in the capture phase, a cancelled pointer ends
// the drag, and so does a move with no button held (the button was released outside the window), as Hall's drag does.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import { test } from "node:test";

const source = readFileSync(new URL("../../src/bmpt/web/js/roster.js", import.meta.url), "utf8");
const code = source.slice(source.indexOf("// dragging the list:"), source.indexOf("// --- toolbar layout"));

function harness() {
  const listeners = {}, timers = [];
  const env = {
    addEventListener: (type, fn, options) => { listeners[type] = { fn, capture: options === true || !!options?.capture }; },
    document: { getElementById: () => ({ getBoundingClientRect: () => ({ width: 960 }) }) }, // the stage at half size
    setTimeout: (fn) => timers.push(fn), ctx: { dragged: false }, view: { scroll: 100 }, scrolls: 0,
  };
  env.applyScroll = () => { env.scrolls++; };
  vm.runInNewContext(`${code}\nglobalThis.press = (y) => { drag = { y, scroll: view.scroll }; ctx.dragged = false; };`, env);
  const flush = () => { for (const fn of timers.splice(0)) fn(); };
  return { env, listeners, flush };
}

test("the list's drag listens in the capture phase and ends on a cancelled pointer", () => {
  const { env, listeners, flush } = harness();
  for (const type of ["pointermove", "pointerup", "pointercancel"]) assert.equal(listeners[type]?.capture, true, type);
  env.press(500);
  listeners.pointermove.fn({ clientY: 480, buttons: 1 });
  assert.equal(env.view.scroll, 140, "20 window px on a half-size stage are 40 stage units");
  assert.equal(env.ctx.dragged, true);
  listeners.pointercancel.fn({});
  flush();
  assert.equal(env.ctx.dragged, false);
  listeners.pointermove.fn({ clientY: 400, buttons: 1 });
  assert.equal(env.view.scroll, 140, "the list no longer follows the pointer");
});

test("a move with no button held ends the drag: the list does not stick to the pointer after a release outside", () => {
  const { env, listeners, flush } = harness();
  env.press(500);
  listeners.pointermove.fn({ clientY: 490, buttons: 1 });
  assert.equal(env.view.scroll, 120);
  // the pointer comes back without the button (its pointerup went elsewhere)
  listeners.pointermove.fn({ clientY: 300, buttons: 0 });
  assert.equal(env.view.scroll, 120);
  listeners.pointermove.fn({ clientY: 200, buttons: 1 });
  assert.equal(env.view.scroll, 120);
  flush();
  assert.equal(env.ctx.dragged, false);
  // an ordinary release still ends it
  env.press(500);
  listeners.pointermove.fn({ clientY: 520, buttons: 1 });
  assert.equal(env.view.scroll, 80);
  listeners.pointerup.fn({});
  listeners.pointermove.fn({ clientY: 600, buttons: 1 });
  assert.equal(env.view.scroll, 80);
});
