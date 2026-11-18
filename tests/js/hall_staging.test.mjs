import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

// The Student Hall screen's staging while a route loads it (main.js): overlapping Student Hall reloads, as a mod toggle
// starts them.
const source = readFileSync(new URL("../../src/bmpt/web/js/app/main.js", import.meta.url), "utf8");
const code = source.slice(source.indexOf("let stagedStudentHall"), source.indexOf("function goBack("));
const staging = () => new Function(`${code}; return { stageStudentHall, unstageStudentHall };`)();
const screen = (hidden) => ({ hidden, style: { visibility: "" } });

test("a Student Hall reload that starts while another is loading leaves the Student Hall visible", () => {
  const { stageStudentHall, unstageStudentHall } = staging();
  const studentHall = screen(false);
  stageStudentHall(studentHall); // the toggle's direct refresh
  stageStudentHall(studentHall); // its library event, before the first one finished; the first one is then dropped
  unstageStudentHall(studentHall, true);
  assert.equal(studentHall.hidden, false);
  assert.equal(studentHall.style.visibility, "");
});

test("an unfinished Student Hall load puts back the screen as it was before the first load", () => {
  const { stageStudentHall, unstageStudentHall } = staging();
  const studentHall = screen(true); // coming from the studentList
  stageStudentHall(studentHall);
  stageStudentHall(studentHall);
  unstageStudentHall(studentHall, false);
  assert.equal(studentHall.hidden, true);
  assert.equal(studentHall.style.visibility, "");
});

test("another page taking over a Student Hall load clears the staging for the next Student Hall visit", () => {
  const { stageStudentHall, unstageStudentHall } = staging();
  const studentHall = screen(true);
  stageStudentHall(studentHall);
  unstageStudentHall(studentHall, false); // a non-student-hall route started meanwhile
  assert.equal(studentHall.style.visibility, "");
  studentHall.hidden = true;
  stageStudentHall(studentHall);
  unstageStudentHall(studentHall, true);
  assert.equal(studentHall.style.visibility, "");
  unstageStudentHall(studentHall, false); // nothing staged: no change
  assert.equal(studentHall.hidden, false);
});
