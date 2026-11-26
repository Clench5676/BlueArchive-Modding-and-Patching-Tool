import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const read = (path) => readFileSync(new URL(`../../src/bmpt/web/js/${path}`, import.meta.url), "utf8");
const slice = (source, from, to) => source.slice(source.indexOf(from), source.indexOf(to, source.indexOf(from)));

test("a card is marked while its entry waits for the reapply of the current install", () => {
  const code = slice(
    read("detail/detail-switchers.js"),
    "export function waitingReapply",
    "export function renderPopup",
  );
  const waitingReapply = new Function(`${code.replace("export ", "")} return waitingReapply;`)();
  const state = {
    current: "/game",
    installs: [
      { key: "/game", waiting: ["hoshino_spr"] },
      { key: "/other", waiting: ["shiroko_spr"] },
    ],
  };
  assert.equal(waitingReapply("Hoshino_spr", state), true);
  assert.equal(waitingReapply("shiroko_spr", state), false, "another install's entry");
  assert.equal(waitingReapply("hoshino_spr", { ...state, installs: [{ key: "/game", waiting: [] }] }), false);
  assert.equal(waitingReapply("", state), false);
  assert.equal(waitingReapply("hoshino_spr", null), false);
});

test("a quit while a start-up job runs covers the screen until its step ended", async () => {
  const code = slice(read("app/main.js"), "let finishing = null;", "// The first-run sequence");
  const tasks = [];
  const Loading = {
    task: (run, options) => {
      const done = run();
      tasks.push({ label: options.label, done });
      return done;
    },
  };
  const finishingStep = new Function("Loading", "T", `${code} return finishingStep;`)(Loading, (key) => key);
  finishingStep(true);
  finishingStep(true);
  assert.equal(tasks.length, 1, "one cover for one quit");
  assert.equal(tasks[0].label, "progress.finishingStep");
  let ended = false;
  tasks[0].done.then(() => {
    ended = true;
  });
  await Promise.resolve();
  assert.equal(ended, false);
  finishingStep(false);
  await tasks[0].done;
  assert.equal(ended, true);
  finishingStep(false);
  finishingStep(true);
  assert.equal(tasks.length, 2, "a later quit covers again");
});
