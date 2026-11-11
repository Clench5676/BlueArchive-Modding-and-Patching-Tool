// BL-176: scrolling areas fade at an edge only while there is more to scroll past it.
import assert from "node:assert/strict";
import { test } from "node:test";
const { fadeMask, FADE, SCROLLERS } = await import("../../src/bmpt/web/js/scroll-fade.js");

test("an area fades at the edges with more past them, and stays sharp where there is none", () => {
  assert.equal(fadeMask({ start: 0, max: 0 }), "", "nothing to scroll: no mask");
  const top = fadeMask({ start: 0, max: 300 });
  assert.match(top, /^linear-gradient\(to bottom, #000 0, #000 18px, #000 calc\(100% - 18px\), transparent 100%\)$/);
  const middle = fadeMask({ start: 120, max: 300 });
  assert.match(middle, /^linear-gradient\(to bottom, transparent 0, .*, transparent 100%\)$/);
  const end = fadeMask({ start: 300, max: 300 });
  assert.match(end, /^linear-gradient\(to bottom, transparent 0, .*, #000 100%\)$/);
  assert.match(fadeMask({ start: 10, max: 300, horizontal: true }), /^linear-gradient\(to right, /);
  assert.equal(FADE, 18);
});

test("every kind of scrolling area takes part", () => {
  for (const cls of [".popup-cards", ".settings-rows", ".import-list", ".import-dialog-content", ".plain-message-lines", ".log-text"])
    assert.ok(SCROLLERS.includes(cls), cls);
});
