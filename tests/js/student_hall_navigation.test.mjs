import assert from "node:assert/strict";
import { test } from "node:test";
import { readFile } from "node:fs/promises";
import { joinedModules } from "./helpers/source-text.mjs";
import { createDraft } from "../../src/bmpt/web/js/student-hall/student-hall-chibi.js";
const tick = () => new Promise(setImmediate);
let requests = [],
  deferred = [],
  prompts = [],
  currentScene = "studentHall",
  renders = 0,
  auto = false;
const node = () => ({ style: {}, dataset: {}, inert: false, isConnected: true, replaceChildren() {}, append() {} });
globalThis.document = { removeEventListener() {}, addEventListener() {}, getElementById: node, createElement: node };
globalThis.studentHallHarness = {
  createDraft,
  S: 1920 / 2960,
  clone: structuredClone,
  T: (key) => key,
  rpc: (method, params) => {
    requests.push({ method, params });
    if (method !== "studentHall.update")
      return Promise.resolve(method === "studentHall.get" ? saved() : { choices: [], backgrounds: [] });
    if (auto) return Promise.resolve({ ...params.value, revision: params.revision + 1 });
    return new Promise((resolve, reject) => deferred.push({ resolve, reject }));
  },
  MemorialLobby: {
    hide() {
      currentScene = null;
    },
  },
  Popup: {
    notify() {},
    message({ buttons, width }) {
      assert.equal(width, 1450);
      return new Promise((resolve) =>
        prompts.push({ actions: buttons.map((action) => ({ fn: () => resolve({ button: action.value }) })) }),
      );
    },
  },
  NGUI: { layout: async () => ({}) },
  UI: {
    ready: async () => {},
    caption: async () => {},
    createModal() {
      const modal = {
        host: node(),
        close() {},
        async draw(build, actions) {
          prompts.push({ modal, actions });
        },
      };
      return modal;
    },
  },
};
const studentHallFile = (name) =>
  readFile(new URL(`../../src/bmpt/web/js/student-hall/${name}`, import.meta.url), "utf8");
let source = joinedModules(
  await studentHallFile("student-hall.js"),
  await studentHallFile("student-hall-portrait-toolbar.js"),
);
source =
  `import { CONTROL, XS_BUTTON, SPACE } from "${new URL("../../src/bmpt/web/js/ui/ui-tokens.js", import.meta.url).href}";\nimport { row } from "${new URL("../../src/bmpt/web/js/ui/ui-grid.js", import.meta.url).href}";\n` +
  source;
source = `import { portraitSelection } from "${new URL("../../src/bmpt/web/js/student-hall/student-hall-chibi.js", import.meta.url).href}";\nconst {createDraft,S,clone,T,rpc,MemorialLobby,Popup,NGUI,UI}=globalThis.studentHallHarness;\n${source}
export function fixture(value, render) { store=createDraft(value);root=art=nodeFake(); renderScene=render;renderChrome=async()=>{}; }
function nodeFake(){return {replaceChildren(){}};}
export function initializePending(){store=null;root=art=nodeFake();}
export function draft(){return store;} export {step,setMode,persist,saveDraft,cancelDraft};`;
const StudentHall = await import(`data:text/javascript;base64,${Buffer.from(source).toString("base64")}`);
function saved() {
  return {
    revision: 0,
    mode: "memorialLobby",
    portraits: [],
    memorialLobbies: [{ id: "one" }, { id: "two" }],
    active: "one",
  };
}
function setup() {
  requests = [];
  deferred = [];
  prompts = [];
  renders = 0;
  auto = false;
  StudentHall.fixture(saved(), async () => {
    renders++;
    currentScene = "studentHall";
  });
}

test("held save cannot replace a detail scene after leaving StudentHall", async () => {
  setup();
  const pending = StudentHall.step(1);
  await tick();
  StudentHall.hide();
  currentScene = "detail";
  deferred[0].resolve({ ...requests[0].params.value, revision: 1 });
  await pending;
  assert.equal(currentScene, "detail");
  assert.equal(renders, 0);
});
test("inactive Student Hall cleanup leaves successive detail owners intact", () => {
  setup();
  StudentHall.hide();
  currentScene = "detail-one";
  StudentHall.hide();
  assert.equal(currentScene, "detail-one");
  currentScene = "detail-two";
  StudentHall.hide();
  assert.equal(currentScene, "detail-two");
});
test("navigation before initial Student Hall metadata arrives is safe", async () => {
  setup();
  StudentHall.initializePending();
  assert.equal(await StudentHall.canLeave(), true);
  StudentHall.hide();
});
test("three queued saves use monotonic revisions even when accepted snapshots replace the base", async () => {
  setup();
  const a = StudentHall.persist(saved()),
    b = StudentHall.persist(saved());
  await tick();
  deferred[0].resolve({ ...saved(), revision: 1 });
  await a;
  StudentHall.draft().accept({ ...saved(), revision: 1 });
  const c = StudentHall.persist(saved());
  await tick();
  deferred[1].resolve({ ...saved(), revision: 2 });
  await b;
  await tick();
  assert.equal(requests[2].params.revision, 2);
  deferred[2].resolve({ ...saved(), revision: 3 });
  await c;
  StudentHall.hide();
});
test("canLeave coalesces prompts and Keep Editing preserves the draft", async () => {
  setup();
  StudentHall.draft().begin("layout").background = "changed";
  const first = StudentHall.canLeave(),
    second = StudentHall.canLeave();
  assert.equal(first, second);
  await tick();
  assert.equal(prompts.length, 1);
  prompts[0].actions[0].fn();
  assert.equal(await first, false);
  assert.ok(StudentHall.draft().dirty());
  StudentHall.hide();
});
test("Discard leaves without writing, Save persists the full draft once", async () => {
  setup();
  StudentHall.draft().begin("layout").background = "discard";
  let leaving = StudentHall.canLeave();
  await tick();
  prompts[0].actions[1].fn();
  assert.equal(await leaving, true);
  assert.equal(requests.length, 0);
  setup();
  auto = true;
  StudentHall.draft().begin("layout").background = "saved";
  leaving = StudentHall.canLeave();
  await tick();
  prompts[0].actions[2].fn();
  assert.equal(await leaving, true);
  assert.equal(requests.filter((r) => r.method === "studentHall.update").length, 1);
  assert.equal(StudentHall.draft().saved().background, "saved");
  StudentHall.hide();
});
test("Cancel cannot discard a draft while its save is already in flight", async () => {
  setup();
  StudentHall.draft().begin("layout").background = "saved";
  const pending = StudentHall.saveDraft({ render: false });
  await tick();
  await StudentHall.cancelDraft();
  assert.equal(StudentHall.draft().current().background, "saved");
  deferred[0].resolve({ ...requests[0].params.value, revision: 1 });
  await pending;
  assert.equal(StudentHall.draft().saved().background, "saved");
  StudentHall.hide();
});

test("settleView restores canceled draft visuals once when navigation stays on StudentHall", async () => {
  setup();
  StudentHall.draft().begin("layout").background = "discard";
  const leaving = StudentHall.canLeave();
  await tick();
  prompts[0].actions[1].fn();
  assert.equal(await leaving, true);
  assert.equal(renders, 0);
  await StudentHall.settleView();
  assert.equal(renders, 1);
  await StudentHall.settleView();
  assert.equal(renders, 1);
  StudentHall.hide();
});
test("clean Home settlement does not restart a memorial scene", async () => {
  setup();
  assert.equal(await StudentHall.canLeave(), true);
  await StudentHall.settleView();
  assert.equal(renders, 0);
  StudentHall.hide();
});
