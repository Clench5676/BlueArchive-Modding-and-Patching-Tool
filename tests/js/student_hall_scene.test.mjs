import assert from "node:assert/strict";
import { flat, joinedModules } from "./helpers/source-text.mjs";
import { test } from "node:test";
import { readFile } from "node:fs/promises";
import {
  createDraft,
  sourceKey,
  portraitSelection,
  portraitReplacements,
} from "../../src/bmpt/web/js/student-hall/student-hall-chibi.js";
import { createMemorialPool } from "../../src/bmpt/web/js/student-hall/student-hall-memorial.js";
import { dialogueText } from "../../src/bmpt/web/js/detail/dialogue-text.js";
import { sceneRect, sceneViewport } from "../../src/bmpt/web/js/student-hall/student-hall-camera.js";
import { portraitRect } from "../../src/bmpt/web/js/student-hall/student-hall-framing.js";

class Element {
  constructor() {
    this.style = {};
    this.dataset = {};
    this.children = [];
    this.listeners = {};
    this.isConnected = true;
  }
  append(...children) {
    for (const child of children) {
      child.parentElement = this;
      this.children.push(child);
    }
  }
  replaceChildren(...children) {
    this.children = [];
    this.append(...children);
  }
  replaceWith(next) {
    const parent = this.parentElement;
    if (parent) {
      const at = parent.children.indexOf(this);
      parent.children[at] = next;
      next.parentElement = parent;
    }
    this.isConnected = false;
  }
  remove() {
    if (this.parentElement) this.parentElement.children = this.parentElement.children.filter((child) => child !== this);
    this.isConnected = false;
  }
  cloneNode(deep) {
    const copy = new Element();
    copy.style = { ...this.style };
    copy.dataset = { ...this.dataset };
    if (deep) copy.append(...this.children.map((child) => child.cloneNode(true)));
    return copy;
  }
  querySelectorAll() {
    return [];
  }
  querySelector() {
    return null;
  }
  addEventListener(name, fn) {
    (this.listeners[name] ||= []).push(fn);
  }
  setAttribute(name, value) {
    this[name] = value;
  }
  setPointerCapture() {}
  contains(node) {
    return this === node || this.children.some((child) => child.contains?.(node));
  }
  closest() {
    return this.dataset.entry ? this : this.parentElement?.closest?.() || null;
  }
  getBoundingClientRect() {
    return { width: 1920, top: 0, left: 0 };
  }
  emit(name, event = {}) {
    for (const fn of this.listeners[name] || []) fn(event);
  }
}
const saved = () => ({
  revision: 0,
  mode: "portraits",
  background: "office-day",
  portraitScale: 1,
  portraits: ["a", "b", "c"].map((id) => ({ id, alt: id, source: `mod:${id}`, x: 700, y: 1100, scale: 1 })),
  memorialLobbies: [],
  active: null,
});
let loads = 0,
  disposals = 0,
  holds = 0,
  updates = 0,
  persisted = [],
  buttons = [],
  chromeDelay = null,
  testLanguage = "en-US";
const renderer = {
  actors: new Map(),
  allowed: true,
  needsLoad: () => false,
  dispose() {
    disposals++;
  },
  setScale() {},
  update(entries) {
    updates++;
    const previous = new Map(this.actors);
    this.actors.clear();
    for (const entry of entries) this.actors.set(entry.id, { ...previous.get(entry.id), entry });
    return Promise.resolve(true);
  },
};
globalThis.sceneHarness = {
  createDraft,
  createMemorialPool,
  sourceKey,
  portraitSelection,
  portraitReplacements,
  portraitRect,
  sceneRect,
  sceneViewport,
  dialogueText,
  language: () => testLanguage,
  clone: structuredClone,
  S: 1920 / 2960,
  T: (key) => key,
  L: (names) => names?.en,
  document: {
    createElement: () => new Element(),
    activeElement: null,
    removeEventListener() {},
    querySelectorAll: () => [],
  },
  Backgrounds: {
    backgroundChoice: () => ({ name: "office-day" }),
    render: async () => {
      loads++;
    },
  },
  Loading: {
    hold: async (fn) => {
      holds++;
      return fn();
    },
  },
  MemorialLobby: { hide() {} },
  Popup: { notify() {}, anyOpen: () => false },
  UI: { button: async () => {}, caption: async () => {} },
  rpc: async (method, params) => {
    if (method === "memorialLobby.lines") return globalThis.sceneHarness.lineResponse(params);
    const { value, revision } = params;
    persisted.push(method);
    return { ...structuredClone(value), revision: revision + 1 };
  },
  hitButton(parent, rect, click, label) {
    const button = new Element();
    button.label = label;
    button.rect = rect;
    button.addEventListener("click", click);
    parent.append(button);
    buttons.push(button);
    return button;
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
source = `import { bindPortraitPointer, portraitToolbarLayout } from "${new URL("../../src/bmpt/web/js/student-hall/student-hall-framing.js", import.meta.url).href}";\nconst {createDraft,createMemorialPool,sourceKey,portraitSelection,portraitReplacements,portraitRect,sceneRect,sceneViewport,dialogueText,language,clone,S,T,L,document,Backgrounds,Loading,MemorialLobby,Popup,UI,rpc,hitButton}=globalThis.sceneHarness;\n${source}
const originalHits=renderHits;
export function fixture(value,renderer,memory=null,shownMode=value.mode){
 store=createDraft(value);root=document.createElement('div');art=document.createElement('div');hits=document.createElement('div');chrome=document.createElement('div');subtitle=document.createElement('div');root.append(art,hits,chrome,subtitle);
 portraits=renderer;abort=new AbortController();sceneMode=shownMode;backgroundName='office-day';backgroundHost=document.createElement('div');portraitSceneHost=document.createElement('div');art.append(portraitSceneHost);ctx={prefs:{},info:{}};actorHover=null;selected=null;sceneCover=null;fullscreen=null;sceneCamera=null;dialogue=null;
 memorials=memory||{current:()=>null,suspend(){},cancelPending(){},dispose(){}};
 options={choices:[...value.portraits.map(e=>({...e,resourceType:'portrait',names:{en:{full:e.id}}})),...value.memorialLobbies.map(e=>({...e,resourceType:'memorialLobby',names:{en:{full:e.id}}}))],backgrounds:[]};
 renderChrome=async()=>{await globalThis.sceneHarness.chromeReady?.();return document.createElement('div');};renderHits=()=>Promise.resolve();renderPortraitActions=async()=>{};
}
export function elements(){return {root,art,hits,chrome,portraitSceneHost,sceneCover,sceneMode};}
export function immersiveFixture(active,onPlay){fullscreen={active:()=>active,dispose(){}};dialogue={play:onPlay};}

export function selection(){return selected;} export {selectPortrait,onKey};
export function draft(){return store;} export {controls,saveDraft,cancelDraft,renderScene,portraitActions,createHoverOwner,memorial,step,refreshMemorialCaptions,configureMemorial};
export function cameraFixture(view){sceneCamera={view:()=>view,sync(){},dispose(){}};}
export function hitsNow(){originalHits();return hits;}`;
// The fixture is intentionally DOM-only; actual Spine identities and GPU resource reuse are covered by
// student_hall.test.
source += "\n//# sourceURL=student-hall-scene-fixture.js";
const StudentHall = await import(`data:text/javascript;base64,${Buffer.from(source).toString("base64")}`);
function setup() {
  loads = disposals = holds = updates = 0;
  persisted = [];
  buttons = [];
  chromeDelay = null;
  globalThis.sceneHarness.chromeReady = () => chromeDelay;
  const value = saved();
  renderer.actors = new Map(
    value.portraits.map((entry) => [entry.id, { entry, player: { animationState: { trackTime: 18 } } }]),
  );
  StudentHall.fixture(value, renderer);
  return value;
}

test("Change Layout Save and Cancel reconcile the existing scene without loading, holds or disposal", async () => {
  setup();
  const before = [...renderer.actors.values()].map((actor) => actor.player);
  await StudentHall.controls().find((action) => action[0] === "Layout")[4]();
  StudentHall.draft().current().portraits[0].x = 900;
  await StudentHall.saveDraft();
  assert.equal(StudentHall.draft().saved().portraits[0].x, 900);
  assert.deepEqual(persisted, ["studentHall.update"]);
  await StudentHall.controls().find((action) => action[0] === "Layout")[4]();
  StudentHall.draft().current().portraits[0].x = 1200;
  await StudentHall.cancelDraft();
  assert.equal(renderer.actors.get("a").entry.x, 900);
  assert.deepEqual(
    [...renderer.actors.values()].map((actor) => actor.player),
    before,
  );
  assert.ok(before.every((player) => player.animationState.trackTime === 18));
  assert.equal(updates, 4);
  assert.equal(loads, 0);
  assert.equal(disposals, 0);
  assert.equal(holds, 0);
});
test("portrait hit order follows saved layer order and wheel or keys cannot mutate relative size", async () => {
  const value = setup();
  StudentHall.draft().begin("layout");
  StudentHall.draft().layer("a", "front");
  StudentHall.hitsNow();
  assert.deepEqual(
    buttons.map((button) => button.dataset.entry),
    ["b", "c", "a"],
  );
  for (const button of buttons) {
    button.emit("wheel", { deltaY: -120, preventDefault() {} });
    button.emit("keydown", { key: "+", preventDefault() {} });
  }
  assert.deepEqual(
    StudentHall.draft()
      .current()
      .portraits.map((entry) => entry.scale),
    [1, 1, 1],
  );
  assert.ok(buttons.every((button) => !button.listeners.wheel && !button.listeners.keydown));
  assert.equal(StudentHall.draft().current().portraitScale, 1);
  assert.equal(value.portraits[0].scale, 1);
});
test("two-or-more portrait layouts offer layer and flip actions, with boundary moves disabled", () => {
  setup();
  StudentHall.draft().begin("layout");
  const list = StudentHall.draft().current().portraits;
  assert.equal(StudentHall.portraitActions(list[0]).length, 7);
  assert.equal(StudentHall.portraitActions(list[0]).find((action) => action[0] === "Backward")[3], false);
  assert.equal(StudentHall.portraitActions(list[2]).find((action) => action[0] === "Front")[3], false);
  StudentHall.draft().remove("c");
  assert.equal(StudentHall.portraitActions(list[0]).length, 7);
  StudentHall.draft().remove("b");
  assert.deepEqual(
    StudentHall.portraitActions(list[0]).map((a) => [a[0], a[3]]),
    [["Change", false]],
  );
  assert.equal(StudentHall.draft().remove("a"), false);
});

test("one hover owner keeps its palette during pointer entry, focus loss and overlapping actor entry", () => {
  const shown = [],
    hover = StudentHall.createHoverOwner((id) => shown.push(id));
  hover.focus("a");
  hover.paletteEnter();
  hover.focus(null);
  hover.actorEnter("b");
  assert.equal(hover.snapshot().owner, "a");
  assert.deepEqual(shown, ["a"]);
  hover.paletteLeave("b");
  assert.equal(hover.snapshot().owner, "b");
  hover.actorLeave("b");
  assert.equal(hover.snapshot().owner, null);
});
test("actor to palette transitions never hide the palette before its pointerenter or click", () => {
  const shown = [],
    hover = StudentHall.createHoverOwner((id) => shown.push(id));
  hover.actorEnter("a");
  hover.actorLeave("a", true);
  hover.focus(null);
  hover.paletteEnter();
  assert.deepEqual(shown, ["a"]);
  hover.paletteLeave();
  assert.deepEqual(shown, ["a", null]);
  hover.focus("b");
  hover.actorEnter("b");
  hover.focus(null);
  assert.equal(hover.snapshot().owner, "b");
  hover.actorLeave("b");
  assert.equal(hover.snapshot().owner, null);
});

test("the shared action palette stays above every hit rectangle through real focusout and pointer events", async () => {
  setup();
  StudentHall.draft().begin("layout");
  const hits = StudentHall.hitsNow(),
    palette = hits.children.at(-1);
  assert.equal(palette.style.zIndex, "1000");
  assert.equal(palette.style.pointerEvents, "auto");
  buttons[0].emit("focusin");
  assert.equal(palette.dataset.actorToolbar, "a");
  palette.emit("pointerdown");
  globalThis.sceneHarness.document.activeElement = null;
  buttons[0].emit("focusout");
  await Promise.resolve();
  buttons[1].emit("pointerenter");
  assert.equal(palette.dataset.actorToolbar, "a");
  assert.equal(palette.style.visibility, "");
  palette.emit("pointerleave", { relatedTarget: buttons[1] });
  assert.equal(palette.dataset.actorToolbar, "b");
});

test("pixel pointer surface replaces overlapping rectangle dragging while named buttons stay keyboard reachable", () => {
  setup();
  StudentHall.draft().begin("layout");
  const hits = StudentHall.hitsNow(),
    surface = hits.children.find((child) => "portraitSurface" in child.dataset);
  assert.ok(surface);
  assert.ok(buttons.every((button) => button.style.pointerEvents === "none"));
  assert.ok(buttons.every((button) => button.label && button.listeners.click && !button.listeners.pointerdown));
});

test("atlas alpha is prepared for picking only in layout editing", () => {
  let prepared = 0;
  renderer.preparePick = () => prepared++;
  try {
    setup();
    StudentHall.hitsNow();
    assert.equal(prepared, 0, "viewing never decodes atlas alpha");
    StudentHall.draft().begin("layout");
    StudentHall.hitsNow();
    assert.equal(prepared, 1);
  } finally {
    delete renderer.preparePick;
  }
});

const tick = () => new Promise(setImmediate);
function memoryFixture() {
  const events = [],
    players = [0, 1].map((index) => ({
      index,
      suspended: true,
      suspend() {
        if (!this.suspended) events.push(["suspend", index]);
        this.suspended = true;
      },
      resume() {
        if (this.suspended) events.push(["resume", index]);
        this.suspended = false;
      },
      async prepareFrame() {
        events.push(["frame", index]);
        return true;
      },
      hide() {
        events.push(["hide", index]);
      },
      setVolume() {},
      setZoomLimit() {},
      setPostProcessing() {},
      setIntro() {},
      setDialogueFullscreen(on) {
        this.dialogueFullscreen = on;
      },
    }));
  let loadDelay = null;
  const pool = createMemorialPool({
    moduleFor: async (index) => players[index],
    load: async (choice, host, signal, player) => {
      events.push(["load", player.index, choice.source]);
      await loadDelay;
      return true;
    },
  });
  return {
    pool,
    players,
    events,
    delay(value) {
      loadDelay = value;
    },
  };
}
test("a mode transition retains old complete art and controls until target resources and chrome are both ready", async () => {
  const value = setup();
  value.memorialLobbies = [{ id: "m1", alt: "memory", source: "mod:memory" }];
  value.active = "m1";
  const memory = memoryFixture();
  StudentHall.fixture(value, renderer, memory.pool);
  const before = StudentHall.elements(),
    oldChrome = new Element();
  before.chrome.append(oldChrome);
  let resourcesReady, chromeReady;
  memory.delay(
    new Promise((resolve) => {
      resourcesReady = resolve;
    }),
  );
  chromeDelay = new Promise((resolve) => {
    chromeReady = resolve;
  });
  StudentHall.draft().current().mode = "memorialLobby";
  const pending = StudentHall.renderScene();
  await tick();
  assert.equal(before.chrome.children[0], oldChrome);
  assert.equal(before.hits.isConnected, true);
  assert.equal(
    memory.events.some((event) => event[0] === "resume"),
    false,
  );
  resourcesReady();
  await tick();
  assert.equal(before.chrome.children[0], oldChrome);
  assert.equal(before.hits.isConnected, true);
  chromeReady();
  await pending;
  assert.notEqual(before.chrome.children[0], oldChrome);
  assert.equal(before.hits.isConnected, false);
  assert.equal(StudentHall.elements().sceneMode, "memorialLobby");
  assert.equal(before.portraitSceneHost.style.visibility, "hidden");
  assert.equal(memory.pool.current().host.style.visibility, "");
});
test("playlist edits and source-identical active UUID changes never restart the retained memorial", async () => {
  const value = setup();
  value.mode = "memorialLobby";
  value.memorialLobbies = [
    { id: "m1", alt: "memory", source: "mod:memory" },
    { id: "m2", alt: "other", source: "mod:other" },
  ];
  value.active = "m1";
  const memory = memoryFixture(),
    session = await memory.pool.prepare(value.memorialLobbies[0], () => new Element(), new AbortController().signal);
  memory.pool.commit(session);
  memory.pool.resume();
  StudentHall.fixture(value, renderer, memory.pool);
  memory.events.length = 0;
  StudentHall.draft().begin("memorialLobby");
  StudentHall.draft().current().memorialLobbies.reverse();
  await StudentHall.renderScene();
  await StudentHall.saveDraft();
  StudentHall.draft().begin("memorialLobby");
  StudentHall.draft().current().memorialLobbies[1].id = "replacement-id";
  StudentHall.draft().current().active = "replacement-id";
  await StudentHall.renderScene();
  await StudentHall.cancelDraft();
  assert.deepEqual(memory.events, []);
  assert.equal(memory.pool.current(), session);
});
test("portraits and the last memorial survive round trips with one memory load and stable player identity", async () => {
  const value = setup();
  value.memorialLobbies = [{ id: "m1", alt: "memory", source: "mod:memory" }];
  value.active = "m1";
  const memory = memoryFixture();
  StudentHall.fixture(value, renderer, memory.pool);
  StudentHall.draft().current().mode = "memorialLobby";
  await StudentHall.renderScene();
  const retained = memory.pool.current();
  StudentHall.draft().current().mode = "portraits";
  await StudentHall.renderScene();
  StudentHall.draft().current().mode = "memorialLobby";
  await StudentHall.renderScene();
  assert.equal(memory.pool.current(), retained);
  assert.equal(memory.events.filter((event) => event[0] === "load").length, 1);
  assert.equal(memory.events.filter((event) => event[0] === "hide").length, 0);
  assert.equal(memory.events.filter((event) => event[0] === "resume").length, 2);
  assert.equal(disposals, 0);
});

test("the real memorial wrapper rejects null metadata or a false frame result before its ready contract", async () => {
  setup();
  const host = new Element(),
    signal = new AbortController().signal,
    choice = { alt: "m", core: "m", source: "official" };
  const player = {
    setVolume() {},
    setZoomLimit() {},
    setPostProcessing() {},
    setIntro() {},
    setDialogueFullscreen(on) {
      this.dialogueFullscreen = on;
    },
    frame() {},
    interactive() {},
    show: async () => null,
    prepareFrame: async () => false,
  };
  await assert.rejects(StudentHall.memorial(choice, host, signal, player), /source is unavailable/);
  player.show = async () => ({ sequences: [] });
  await assert.rejects(StudentHall.memorial(choice, host, signal, player), /frame is unavailable/);
});
test("Next then Previous during staging cancels the pending source and resumes the retained current memory", async () => {
  const value = setup();
  value.mode = "memorialLobby";
  value.memorialLobbies = [
    { id: "m1", alt: "a", source: "mod:a" },
    { id: "m2", alt: "b", source: "mod:b" },
  ];
  value.active = "m1";
  const memory = memoryFixture(),
    session = await memory.pool.prepare(value.memorialLobbies[0], () => new Element(), new AbortController().signal);
  memory.pool.commit(session);
  memory.pool.resume();
  StudentHall.fixture(value, renderer, memory.pool);
  memory.events.length = 0;
  let finish;
  memory.delay(
    new Promise((resolve) => {
      finish = resolve;
    }),
  );
  const next = StudentHall.step(1);
  await tick();
  assert.equal(session.player.suspended, true);
  await StudentHall.step(-1);
  assert.equal(memory.pool.current(), session);
  assert.equal(session.player.suspended, false);
  assert.equal(StudentHall.draft().saved().active, "m1");
  finish();
  await next;
  assert.equal(memory.pool.current(), session);
  assert.equal(session.player.suspended, false);
  assert.deepEqual(
    memory.events.filter((event) => event[0] === "resume"),
    [["resume", 0]],
  );
});

test("immersive portrait interaction preserves its draft and renderer while disabling editing gestures", async () => {
  setup();
  StudentHall.draft().begin("layout");
  const entry = StudentHall.draft().current().portraits[0],
    actor = renderer.actors.get(entry.id),
    before = structuredClone(entry);
  let spoken = null;
  StudentHall.immersiveFixture(true, async (value) => {
    spoken = value.id;
  });
  StudentHall.hitsNow();
  const target = buttons[0];
  target.emit("pointerdown", { clientX: 100, clientY: 100, pointerId: 1, preventDefault() {} });
  target.emit("pointermove", { clientX: 300, clientY: 400, buttons: 1 });
  target.emit("click");
  await Promise.resolve();
  assert.deepEqual(entry, before);
  assert.equal(spoken, entry.id);
  assert.equal(renderer.actors.get(entry.id), actor);
  assert.equal(StudentHall.draft().kind(), "layout");
  assert.equal(updates, 0);
  assert.equal(disposals, 0);
  StudentHall.immersiveFixture(false, async () => {});
});

test("viewing zoom projects every portrait hit with the artwork, and editing keeps original layout coordinates", () => {
  setup();
  StudentHall.hitsNow();
  const before = buttons.slice(-3).map((button) => button.rect);
  StudentHall.cameraFixture({ scale: 2, x: -500, y: -200 });
  StudentHall.hitsNow();
  const after = buttons.slice(-3).map((button) => button.rect);
  for (let i = 0; i < before.length; i++)
    assert.deepEqual(after[i], [before[i][0] * 2 - 500, before[i][1] * 2 - 200, before[i][2] * 2, before[i][3] * 2]);
  StudentHall.draft().begin("layout");
  StudentHall.hitsNow();
  assert.deepEqual(
    buttons.slice(-3).map((button) => button.rect),
    before,
  );
});

test("a retained memorial changes caption language without replaying and rejects stale language requests", async () => {
  setup();
  const controller = new AbortController(),
    source = { alt: "hero", talks: { Talk_01_M: ["voice"] }, signal: controller.signal };
  const requests = [],
    published = [];
  let oldReady;
  globalThis.sceneHarness.lineResponse = (params) => {
    requests.push(params);
    return params.lang === "ja"
      ? new Promise((resolve) => {
          oldReady = resolve;
        })
      : Promise.resolve({ voice: { "zh-CN": "Current Chinese", ja: "Original" } });
  };
  const player = {
    setLines: (rows, text) => published.push({ rows, text }),
    show() {
      assert.fail("Localization cannot restart the scene");
    },
  };
  testLanguage = "ja-JP";
  const old = StudentHall.refreshMemorialCaptions(player, source);
  testLanguage = "zh-CN";
  await StudentHall.refreshMemorialCaptions(player);
  assert.equal(requests[1].lang, "zh-TW");
  const tables = published.filter((value) => value.rows !== undefined);
  assert.equal(tables[0].text(tables[0].rows.voice), "Current Chinese");
  assert.equal(published[1].text({ ja: "Old foreign line" }), "");
  oldReady({ voice: { ja: "Obsolete" } });
  await old;
  assert.equal(published.filter((value) => value.rows !== undefined).length, 1);
  controller.abort();
  await StudentHall.refreshMemorialCaptions(player);
  assert.equal(requests.length, 2);
});

test("selection toggles, switches, pins against hover, and survives layer/flip redraws", () => {
  setup();
  StudentHall.draft().begin("layout");
  let hits = StudentHall.hitsNow();
  buttons[0].emit("click", { detail: 0 });
  assert.equal(StudentHall.selection(), "a");
  const palette = hits.children.at(-1);
  buttons[1].emit("pointerenter");
  assert.equal(palette.dataset.actorToolbar, "a");
  buttons[0].emit("click", { detail: 0 });
  assert.equal(StudentHall.selection(), null);
  buttons[1].emit("click", { detail: 0 });
  assert.equal(StudentHall.selection(), "b");
  buttons[2].emit("click", { detail: 0 });
  assert.equal(StudentHall.selection(), "c");
  StudentHall.draft().layer("c", "back");
  StudentHall.draft().flip("c");
  hits = StudentHall.hitsNow();
  assert.equal(StudentHall.selection(), "c");
  assert.equal(hits.children.at(-1).dataset.actorToolbar, "c");
  assert.equal(buttons.at(-3)["aria-pressed"], "true");
});
test("four-stage-pixel click tolerance, empty clicks, Escape and drag never toggle off", () => {
  setup();
  StudentHall.draft().begin("layout");
  let hits = StudentHall.hitsNow();
  renderer.pick = () => "a";
  const surface = () => hits.children.find((e) => "portraitSurface" in e.dataset);
  const press = (x, y) =>
    surface().emit("pointerdown", { button: 0, clientX: x, clientY: y, pointerId: 1, preventDefault() {} });
  const release = (x, y) => {
    surface().emit("pointerup", { clientX: x, clientY: y, pointerId: 1 });
    hits = StudentHall.elements().hits;
  };
  const before = StudentHall.draft().current().portraits[0].x;
  press(100, 100);
  release(104, 100);
  assert.equal(StudentHall.selection(), "a");
  assert.equal(StudentHall.draft().current().portraits[0].x, before);
  press(100, 100);
  release(105, 100);
  assert.equal(StudentHall.selection(), "a");
  assert.equal(StudentHall.draft().current().portraits[0].x, before + 5);
  let prevented = false;
  StudentHall.onKey({
    key: "Escape",
    preventDefault() {
      prevented = true;
    },
    stopImmediatePropagation() {},
  });
  assert.ok(prevented);
  assert.equal(StudentHall.selection(), null);
  prevented = false;
  StudentHall.onKey({
    key: "Escape",
    preventDefault() {
      prevented = true;
    },
  });
  assert.equal(prevented, false);
  StudentHall.selectPortrait("click", "a");
  renderer.pick = () => null;
  press(100, 100);
  release(100, 100);
  assert.equal(StudentHall.selection(), null);
  delete renderer.pick;
});
test("Save, Cancel, removal and leaving end editor selection", async () => {
  setup();
  StudentHall.draft().begin("layout");
  StudentHall.selectPortrait("click", "a");
  await StudentHall.saveDraft();
  assert.equal(StudentHall.selection(), null);
  StudentHall.draft().begin("layout");
  StudentHall.selectPortrait("click", "b");
  await StudentHall.cancelDraft();
  assert.equal(StudentHall.selection(), null);
  StudentHall.draft().begin("layout");
  StudentHall.selectPortrait("click", "c");
  StudentHall.draft().remove("c");
  StudentHall.hitsNow();
  assert.equal(StudentHall.selection(), null);
  StudentHall.selectPortrait("click", "a");
  StudentHall.selectPortrait("end");
  assert.equal(StudentHall.selection(), null);
});
test("selection reducer covers redraws and drag cancellation without persisting session state", () => {
  let selected = null;
  const send = (type, id = null, ids = []) => (selected = portraitSelection(selected, { type, id, ids }));
  assert.equal(send("click", "a"), "a");
  assert.equal(send("click", "a"), null);
  assert.equal(send("click", "b"), "b");
  assert.equal(send("drag", "b"), "b");
  assert.equal(send("drag", "a"), "a");
  assert.equal(send("redraw", null, ["a", "b"]), "a");
  assert.equal(send("remove", "b"), "a");
  assert.equal(send("remove", "a"), null);
  assert.equal(send("click", null), null);
  send("click", "a");
  assert.equal(send("escape"), null);
  send("click", "a");
  assert.equal(send("end"), null);
});

test("pixel surface ends a press on lost capture or released buttons; the press has already selected", () => {
  for (const event of ["lostpointercapture", "released"]) {
    setup();
    StudentHall.draft().begin("layout");
    const host = StudentHall.hitsNow(),
      surface = host.children.find((e) => "portraitSurface" in e.dataset);
    renderer.pick = () => "a";
    const entry = StudentHall.draft().current().portraits[0],
      initial = entry.x;
    surface.emit("pointerdown", { button: 0, clientX: 100, clientY: 100, pointerId: 1, preventDefault() {} });
    assert.equal(StudentHall.selection(), "a");
    if (event === "lostpointercapture") surface.emit(event, { pointerId: 1 });
    else surface.emit("pointermove", { clientX: 200, clientY: 100, buttons: 0 });
    surface.emit("pointermove", { clientX: 300, clientY: 100, buttons: 1 });
    assert.equal(entry.x, initial);
    assert.equal(StudentHall.selection(), "a");
    delete renderer.pick;
  }
});
test("hover keeps a portrait's toolbar over the transparent parts of its box, the way up to the toolbar", () => {
  setup();
  StudentHall.draft().begin("layout");
  const hits = StudentHall.hitsNow(),
    surface = hits.children.find((e) => "portraitSurface" in e.dataset),
    palette = hits.children.at(-1);
  const [left, top, width] = hits.children.find((e) => e.dataset.entry === "a").rect;
  renderer.pick = () => "a";
  surface.emit("pointermove", { clientX: left + width / 2, clientY: top + 300, buttons: 0 });
  assert.equal(palette.dataset.actorToolbar, "a");
  renderer.pick = () => null;
  surface.emit("pointermove", { clientX: left + width / 2, clientY: top + 2, buttons: 0 });
  assert.equal(palette.dataset.actorToolbar, "a", "inside the box");
  surface.emit("pointermove", { clientX: left - 40, clientY: top + 300, buttons: 0 });
  assert.equal(palette.dataset.actorToolbar, "", "outside the box");
  delete renderer.pick;
});
test("WebKit's click-only release selects, toggles off and never drags", () => {
  setup();
  StudentHall.draft().begin("layout");
  let hits = StudentHall.hitsNow();
  renderer.pick = () => "a";
  const surface = () => hits.children.find((e) => "portraitSurface" in e.dataset),
    entry = StudentHall.draft().current().portraits[0],
    initial = entry.x;
  // The sequence the macOS WKWebView sends: pointerdown, a move that still reports a button, then only click.
  const tap = () => {
    surface().emit("pointerdown", { button: 0, clientX: 100, clientY: 100, pointerId: 1, preventDefault() {} });
    surface().emit("pointermove", { clientX: 100, clientY: 100, buttons: 4, pointerId: 1 });
    surface().emit("click", { clientX: 100, clientY: 100 });
    hits = StudentHall.elements().hits;
  };
  tap();
  assert.equal(StudentHall.selection(), "a");
  assert.equal(entry.x, initial);
  assert.equal(hits.children.at(-1).dataset.actorToolbar, "a", "the toolbar is pinned");
  tap();
  assert.equal(StudentHall.selection(), null, "a second click clears it");
  tap();
  assert.equal(StudentHall.selection(), "a");
  renderer.pick = () => null;
  tap();
  assert.equal(StudentHall.selection(), null, "an empty click clears it");
  delete renderer.pick;
});

test("ordinary Student Hall memorials keep their view bounds with the full camera; immersion alone frees the whole stage", () => {
  setup();
  const changes = [],
    player = {
      setDialogueFullscreen: (on) => changes.push(on),
      setVolume() {},
      setZoomLimit() {},
      setPostProcessing() {},
      setIntro() {},
    };
  StudentHall.configureMemorial(player);
  StudentHall.immersiveFixture(true);
  StudentHall.configureMemorial(player);
  StudentHall.immersiveFixture(false);
  StudentHall.configureMemorial(player);
  assert.deepEqual(changes, [false, true, false]);
  assert.match(
    flat(source),
    /onChange: \(active\) => \{ memorials\?\.current\(\)\?\.player.setDialogueFullscreen\(active\)/,
  );
});
