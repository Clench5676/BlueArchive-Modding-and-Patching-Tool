import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

// The hall screen's staging while a route loads it (main.js): overlapping hall reloads, as a mod toggle starts them.
const source = readFileSync(new URL("../../src/bmpt/web/js/main.js", import.meta.url), "utf8");
const code = source.slice(source.indexOf("let stagedHall"), source.indexOf("function goBack("));
const staging = () => new Function(`${code}; return { stageHall, unstageHall };`)();
const screen = (hidden) => ({ hidden, style: { visibility: "" } });

test("a hall reload that starts while another is loading leaves the hall visible", () => {
  const { stageHall, unstageHall } = staging();
  const hall = screen(false);
  stageHall(hall); // the toggle's direct refresh
  stageHall(hall); // its library event, before the first one finished; the first one is then dropped
  unstageHall(hall, true);
  assert.equal(hall.hidden, false);
  assert.equal(hall.style.visibility, "");
});

test("an unfinished hall load puts back the screen as it was before the first load", () => {
  const { stageHall, unstageHall } = staging();
  const hall = screen(true); // coming from the roster
  stageHall(hall);
  stageHall(hall);
  unstageHall(hall, false);
  assert.equal(hall.hidden, true);
  assert.equal(hall.style.visibility, "");
});

test("another page taking over a hall load clears the staging for the next hall visit", () => {
  const { stageHall, unstageHall } = staging();
  const hall = screen(true);
  stageHall(hall);
  unstageHall(hall, false); // a non-hall route started meanwhile
  assert.equal(hall.style.visibility, "");
  hall.hidden = true;
  stageHall(hall);
  unstageHall(hall, true);
  assert.equal(hall.style.visibility, "");
  unstageHall(hall, false); // nothing staged: no change
  assert.equal(hall.hidden, false);
});
