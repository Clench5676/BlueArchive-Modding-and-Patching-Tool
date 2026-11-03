// The design tokens (web/js/ui-tokens.js): one place for the numbers and colours that popups, buttons, chips and the style
// sheets share. The prefab values behind them are checked by tests/test_ui_tokens.py against the development view.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import * as Tokens from "../../src/ba_modding_toolkit/web/js/ui-tokens.js";

const WEB = new URL("../../src/ba_modding_toolkit/web/", import.meta.url);
const read = (path) => readFileSync(new URL(path, WEB), "utf8");

const frozenDeep = (value) => !value || typeof value !== "object" || (Object.isFrozen(value) && Object.values(value).every(frozenDeep));

test("every token is frozen and the module imports no other UI module", () => {
  for (const [name, value] of Object.entries(Tokens)) assert.ok(frozenDeep(value), `${name} is frozen`);
  assert.doesNotMatch(read("js/ui-tokens.js"), /^\s*import\b/m);
});

test("the footer's centre follows from the prefab's frame and button rows", () => {
  const { POPUP, FOOTER } = Tokens;
  assert.equal(POPUP.frameY, -12);
  // Btn at y -499, the frame's bottom edge at frameY - frameH / 2
  assert.equal(FOOTER.btnY - (POPUP.frameY - POPUP.frameH / 2), FOOTER.centre);
  assert.equal(FOOTER.centre, 190.5);
  // Btn_Ok at +292 and Btn_Cancel at -292, bodies 496 wide: 88 between them
  assert.equal(2 * FOOTER.pairX - FOOTER.body.w, FOOTER.pairGap);
  assert.equal(FOOTER.pairGap, 88);
});

test("the small button and the chip are the prefab nodes' sizes, their pitches follow", () => {
  const { SMALL_BUTTON, CHIP } = Tokens;
  assert.deepEqual([SMALL_BUTTON.w, SMALL_BUTTON.h, SMALL_BUTTON.fs, SMALL_BUTTON.fsMin, SMALL_BUTTON.minW], [402, 136, 48, 34, 240]);
  assert.equal(SMALL_BUTTON.pitch, 422);
  assert.equal(SMALL_BUTTON.pitch - SMALL_BUTTON.w, SMALL_BUTTON.gap);
  assert.deepEqual([CHIP.w, CHIP.h, CHIP.fs, CHIP.fsMin, CHIP.pitch], [272, 72, 40, 28, 290]);
  assert.equal(CHIP.pitch - CHIP.w, CHIP.gap);
});
