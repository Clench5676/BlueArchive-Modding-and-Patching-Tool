import { readFileSync, readdirSync } from "node:fs";
import assert from "node:assert/strict";
import { test } from "node:test";

class El {
  children = [];
  style = {};
  listeners = {};
  className = "";
  isConnected = true;
  hidden = false;
  parentNode = null;
  classList = {
    add: (...names) => {
      this.className = [...new Set([...this.className.split(" "), ...names])].join(" ").trim();
    },
    remove: (...names) => {
      this.className = this.className
        .split(" ")
        .filter((n) => !names.includes(n))
        .join(" ");
    },
    replace: (a, b) => {
      this.classList.remove(a);
      this.classList.add(b);
    },
    contains: (name) => this.className.split(" ").includes(name),
    toggle: (name, on) => {
      if (on) this.classList.add(name);
      else this.classList.remove(name);
    },
  };
  addEventListener(type, fn) {
    this.listeners[type] = fn;
  }
  setAttribute() {}
  removeAttribute() {}
  remove() {
    this.isConnected = false;
  }
  appendChild(el) {
    el.parentNode = this;
    this.children.push(el);
    return el;
  }
  contains(node) {
    for (let n = node; n; n = n.parentNode) if (n === this) return true;
    return false;
  }
  closest(selector) {
    for (let n = this; n; n = n.parentNode) {
      if (selector === ".topbar .back" && n.back) return n;
      if (selector.includes(":not([hidden])") && !n.hidden && /popup-host|consent-host/.test(n.className)) return n;
    }
    return null;
  }
  replaceChildren(...els) {
    this.children = els;
  }
  get firstChild() {
    return this.children[0];
  }
  set innerHTML(text) {
    if (text.includes("loading-back")) this.parts = { ".loading-back": new El(), ".loading-ind": new El() };
    else this.parts = { span: new El() };
    this.children = [new El()];
  }
  querySelector(key) {
    return this.parts?.[key] || null;
  }
  getContext() {
    return {};
  }
}
const stage = new El();
const root = new El();
globalThis.document = {
  createElement: () => new El(),
  getElementById: (id) => (id === "stage" ? stage : null),
  querySelector: () => stage.children.find((el) => !el.hidden && /popup-host|consent-host/.test(el.className)) || null,
  documentElement: root,
};
globalThis.getComputedStyle = () => ({ opacity: "1" });
globalThis.addEventListener = () => {};
globalThis.requestAnimationFrame = (fn) => setTimeout(fn, 0);
globalThis.fetch = async () => {
  throw new Error("no game assets in this test");
};
const Loading = await import("../../src/bmpt/web/js/loading.js");
const Popup = await import("../../src/bmpt/web/js/popup.js");
const { freezeWhileLoading } = await import("../../src/bmpt/web/js/preview-lifecycle.js");
const event = (target = new El(), key) => ({
  target,
  key,
  prevented: false,
  stopped: false,
  preventDefault() {
    this.prevented = true;
  },
  stopImmediatePropagation() {
    this.stopped = true;
  },
});

function deferred() {
  let resolve;
  const promise = new Promise((r) => {
    resolve = r;
  });
  return { promise, resolve };
}

test("input is held immediately through a slow apply, Back included", async () => {
  const job = deferred();
  const done = Loading.hold(() => job.promise);
  assert.equal(Loading.held(), true);
  const click = event();
  stage.listeners.click(click);
  assert.ok(click.prevented && click.stopped);
  const backButton = new El();
  backButton.back = true;
  const back = event(backButton);
  stage.listeners.click(back);
  assert.ok(back.prevented && back.stopped, "Now Loading locks the top bar too");
  await new Promise((r) => setTimeout(r, 350));
  assert.ok(Loading.active(), "the existing corner indicator shows during the hold");
  assert.equal(Loading.held(), true);
  job.resolve(7);
  assert.equal(await done, 7);
  assert.equal(Loading.held(), false);
});

test("every open popup layer and the consent and prompt controls pass capture while page input stays held", async () => {
  const work = deferred();
  const done = Loading.hold(() => work.promise);
  try {
    const host = stage.appendChild(new El());
    const lower = host.appendChild(new El()),
      upper = host.appendChild(new El());
    Popup.opened(host, () => {}, { el: lower });
    Popup.opened(host, null, { el: upper });
    const consent = stage.appendChild(new El());
    consent.className = "consent-host";
    const prompt = stage.appendChild(new El());
    prompt.className = "popup-host";
    for (const layer of [lower, upper, consent, prompt]) {
      const button = layer.appendChild(new El());
      for (const type of ["pointerdown", "click", "keydown"]) {
        const ev = event(button, type === "keydown" ? "Enter" : undefined);
        stage.listeners[type](ev);
        assert.equal(ev.prevented || ev.stopped, false, `${type} in ${layer.className || "a stacked popup"}`);
      }
    }
    for (const type of ["pointerdown", "click", "keydown"]) {
      const ev = event(stage, type === "keydown" ? "Enter" : undefined);
      stage.listeners[type](ev);
      assert.ok(ev.prevented && ev.stopped, `${type} on the underlying page`);
    }
    for (const layer of [host, consent, prompt]) layer.hidden = true;
    const hidden = event(lower);
    stage.listeners.click(hidden);
    assert.ok(hidden.prevented && hidden.stopped, "closed layers grant no exemption");
  } finally {
    work.resolve();
    await done;
  }
});

test("Escape reaches the top popup while focus remains on the held page", async () => {
  const work = deferred();
  const done = Loading.hold(() => work.promise);
  const host = stage.appendChild(new El());
  let closed = false;
  Popup.opened(host, () => {
    closed = true;
    host.hidden = true;
  });
  try {
    const escape = event(stage, "Escape");
    stage.listeners.keydown(escape);
    assert.equal(escape.prevented || escape.stopped, false);
    Popup.onKey({ ...escape, defaultPrevented: escape.prevented });
    assert.ok(closed, "the popup's own Escape handler runs");
    assert.equal(Loading.held(), true);
    const background = event();
    stage.listeners.click(background);
    assert.ok(background.prevented && background.stopped);
  } finally {
    work.resolve();
    await done;
  }
});

test("leaving releases input but automatic popups still wait for the job after navigation", async () => {
  const job = deferred(),
    left = deferred();
  let completed = false;
  const done = Loading.hold(
    async () => {
      await job.promise;
      completed = true;
      return "reported";
    },
    { until: left.promise },
  );
  left.resolve();
  await new Promise(setImmediate);
  assert.equal(Loading.held(), false);
  assert.equal(completed, false);
  globalThis.getComputedStyle = () => ({ opacity: "1" });
  await Loading.scene(async () => {});
  assert.equal(Loading.active(), false);
  assert.equal(Loading.busy(), true, "the released hold's job still blocks an update offer");
  job.resolve();
  assert.equal(await done, "reported");
  assert.equal(Loading.busy(), false);
});

test("overlapping holds and thrown work release exactly their own input hold", async () => {
  const job = deferred();
  const done = Loading.hold(() => job.promise);
  await assert.rejects(
    Loading.hold(() => {
      throw new Error("after job failed");
    }),
    /after job/,
  );
  assert.equal(Loading.held(), true);
  job.resolve();
  await done;
  assert.equal(Loading.held(), false);
});

test("a repair cover blocks Back, popup clicks and Escape until success or failure", async () => {
  globalThis.getComputedStyle = () => ({ opacity: "1" });
  for (const fail of [false, true]) {
    const job = deferred();
    const back = new El();
    back.back = true;
    const popup = new El();
    popup.className = "popup-host";
    const done = Loading.hold(
      () =>
        Loading.scene(async () => {
          await job.promise;
          if (fail) throw new Error("repair failed");
        }),
      { all: true },
    );
    await new Promise((resolve) => setTimeout(resolve, 0));
    assert.equal(Loading.covering(), false);
    assert.equal(stage.classList.contains("input-held"), true);
    assert.equal(Loading.busy(), true);
    for (const [target, key] of [
      [back, undefined],
      [popup, undefined],
      [popup, "Escape"],
    ]) {
      const ev = event(target, key);
      stage.listeners[key ? "keydown" : "click"](ev);
      assert.ok(ev.prevented && ev.stopped);
    }
    job.resolve();
    if (fail) await assert.rejects(done, /repair failed/);
    else await done;
    assert.equal(Loading.held(), false);
    assert.equal(Loading.covering(), false);
    assert.equal(stage.classList.contains("input-held"), false);
    assert.equal(Loading.busy(), false);
  }
});

const detailSource = readFileSync(new URL("../../src/bmpt/web/js/detail.js", import.meta.url), "utf8");
const detailJob = detailSource.slice(
  detailSource.indexOf("async function job("),
  detailSource.indexOf("function shownMod()"),
);
function detailHarness(overrides = {}) {
  const env = {
    Loading,
    notify() {},
    T: (key) => key,
    variant: () => ({}),
    fullName: () => "Student",
    fam: {},
    switchKind: () => "spr",
    progressName: () => "Student",
    modJob: () => () => ({ text: "Repairing: Student" }),
    modLabel: () => "",
    steadyFraction: () => () => null,
    renderScreen: async () => {},
    rpc: async () => "job",
    waitJob: async () => ({}),
    reload: async () => {},
    QUEUEABLE: [],
    errorText: String,
    refused() {},
    overwrittenMessage() {},
    resumeChanged() {},
    ...overrides,
  };
  return new Function(
    "env",
    `const { ${Object.keys(env).join(", ")} } = env;
    let busy = false, pageToken = 1, open = true;
    ${detailJob}
    return job;`,
  )(env);
}

test("detail jobs ignore Back, nav and every other interaction until the final redraw, then restore them", async () => {
  const work = deferred(),
    redraw = deferred();
  let renders = 0;
  const job = detailHarness({
    waitJob: () => work.promise,
    renderScreen: async () => {
      if (++renders === 2) await redraw.promise;
    },
  });
  const targets = ["back", "nav", "student", "variant", "tab", "mods switch", "action card", "popup trigger"].map(
    (name) => {
      const target = new El();
      target.back = name === "back";
      return target;
    },
  );
  const actions = targets.map(() => 0);
  const click = (i) => {
    const ev = event(targets[i]);
    stage.listeners.click(ev);
    if (!ev.stopped) actions[i]++;
    return ev;
  };
  const done = job("actions.update", { mod: "m1" });
  await new Promise(setImmediate);
  assert.equal(Loading.held(), true);
  targets.forEach((_, i) => assert.ok(click(i).stopped));
  assert.deepEqual(
    actions,
    targets.map(() => 0),
  );
  for (const type of ["pointerdown", "pointerup", "pointermove", "keydown", "wheel", "touchstart", "touchmove"]) {
    const ev = event(targets[0]);
    stage.listeners[type](ev);
    assert.ok(ev.stopped, type);
  }
  work.resolve({});
  await new Promise(setImmediate);
  assert.equal(Loading.held(), true, "the final redraw is still protected");
  assert.ok(click(0).stopped);
  assert.ok(click(1).stopped);
  redraw.resolve();
  assert.equal(await done, true);
  assert.equal(Loading.held(), false);
  targets.forEach((_, i) => assert.equal(click(i).stopped, false));
  assert.deepEqual(
    actions,
    targets.map(() => 1),
  );
});

test("a detail write refusal opens its answerable popup only after releasing the strict hold", async () => {
  let shown = 0;
  const job = detailHarness({
    QUEUEABLE: ["actions.apply"],
    waitJob: async () => {
      throw { code: "original_unavailable" };
    },
    overwrittenMessage: async () => {
      assert.equal(Loading.held(), false);
      shown++;
    },
  });
  assert.equal(await job("actions.apply", { kind: "spr" }), false);
  assert.equal(shown, 1);
  assert.equal(Loading.held(), false);
});

for (const entry of ["Enable Mods"])
  test(`${entry} blocks all detail input through submission and job completion`, async () => {
    const submitted = deferred(),
      work = deferred(),
      redraw = deferred();
    const targets = ["back", "nav", "student", "variant", "tab", "mods switch", "action", "popup"].map((name) => {
      const target = new El();
      target.back = name === "back";
      if (name === "popup") target.className = "popup-host";
      return target;
    });
    const assertInput = (blocked) => {
      for (const target of targets)
        for (const type of ["click", "keydown", "pointerdown", "wheel"]) {
          const ev = event(target, "Enter");
          stage.listeners[type](ev);
          assert.equal(ev.stopped, blocked, `${entry}: ${type}`);
        }
    };
    let start;
    const source = readFileSync(new URL("../../src/bmpt/web/js/mod-switch.js", import.meta.url), "utf8");
    const run = source.slice(source.indexOf("async function run(params)"), source.indexOf("// a toggle job followed"));
    const env = {
      Loading,
      T: (key) => key,
      notify: assert.fail,
      rpc: () => submitted.promise,
      toggleJob: () => work.promise,
      refreshPage: () => redraw.promise,
      REFRESH_CAP: 1000,
      finished: () => assertInput(false),
      refused: assert.fail,
    };
    start = new Function(
      "env",
      `const { ${Object.keys(env).join(", ")} } = env; let inFlight = false; ${run}; return run;`,
    )(env);
    const done = start();
    assertInput(true);
    submitted.resolve("job");
    await new Promise(setImmediate);
    assertInput(true);
    work.resolve({});
    await new Promise(setImmediate);
    if (entry === "Enable Mods") assertInput(true);
    redraw.resolve();
    await done;
    assert.equal(Loading.held(), false);
    assertInput(false);
  });

test("a navigation cannot leave an existing strict hold blocking pencils without a visible overlay", async () => {
  globalThis.getComputedStyle = () => ({ opacity: "1" });
  const work = deferred();
  const strict = Loading.hold(() => work.promise, { all: true });
  await new Promise(setImmediate);
  await Loading.scene(async () => {});
  await new Promise((r) => setTimeout(r, 320));
  try {
    assert.equal(Loading.held(), true);
    assert.equal(Loading.active(), true, "a live input lock always owns its visible shaded overlay");
    const click = event();
    stage.listeners.click(click);
    assert.equal(click.stopped, true);
  } finally {
    work.resolve();
    await strict;
  }
  const click = event();
  stage.listeners.click(click);
  assert.equal(click.stopped, false);
});

test("a read-only preview keeps popups available while guarding Back and other inputs", async () => {
  const work = deferred(),
    back = new El();
  back.back = true;
  const popup = new El();
  popup.className = "popup-host";
  const changing = Loading.page(async () => work.promise);
  await new Promise(setImmediate);
  try {
    const open = event(popup);
    stage.listeners.click(open);
    assert.equal(open.stopped, false);
    for (const target of [back, new El()]) {
      const click = event(target);
      stage.listeners.click(click);
      assert.equal(click.stopped, true);
    }
  } finally {
    work.resolve();
    await changing;
  }
});

// Now Loading locks every control beneath it, the top bar and the bottom menu included
test("writer progress locks the top bar, the bottom menu and the page until it settles", async () => {
  const job = deferred();
  const running = Loading.task(() => job.promise, { label: "Applying" });
  await new Promise(setImmediate);
  try {
    assert.equal(Loading.locked(), true);
    const toggle = new El(),
      nav = new El(),
      back = new El();
    back.back = true;
    for (const target of [toggle, nav, back])
      for (const type of ["pointerdown", "click", "keydown"]) {
        const ev = event(target, type === "keydown" ? " " : undefined);
        stage.listeners[type](ev);
        assert.ok(ev.prevented && ev.stopped, `${type} beneath writer progress`);
      }
    const popup = new El();
    popup.className = "popup-host";
    const open = event(popup);
    stage.listeners.click(open);
    assert.equal(open.stopped, false, "a popup over the progress stays answerable");
  } finally {
    job.resolve();
    await running;
  }
  assert.equal(Loading.locked(), false);
  const after = event();
  stage.listeners.click(after);
  assert.equal(after.stopped, false);
});

test("an in-page wait locks input once its indicator is up", async () => {
  const job = deferred();
  const waiting = Loading.wait(job.promise, { immediate: true });
  await new Promise(setImmediate);
  try {
    assert.equal(Loading.active(), true);
    const click = event();
    stage.listeners.click(click);
    assert.ok(click.stopped);
  } finally {
    job.resolve();
    await waiting;
  }
});

test("Now Loading holds pinches and touch ends too, and freezes the previews shown beneath it", async () => {
  const calls = [];
  const shown = freezeWhileLoading({ freeze: () => calls.push("freeze"), thaw: () => calls.push("thaw") });
  const preparing = freezeWhileLoading({ shown: () => false, freeze: () => calls.push("preparing freeze") });
  const job = deferred();
  const done = Loading.hold(() => job.promise);
  for (const type of ["gesturestart", "gesturechange", "gestureend", "touchend"]) {
    const ev = event();
    stage.listeners[type](ev);
    assert.ok(ev.prevented && ev.stopped, `${type} is held`);
  }
  assert.deepEqual(calls, ["freeze"]);
  job.resolve();
  await done;
  assert.equal(Loading.locked(), false);
  assert.deepEqual(calls, ["freeze", "thaw"]);
  const ev = event();
  stage.listeners.gesturechange(ev);
  assert.equal(ev.prevented, false, "a pinch reaches the preview once Now Loading is gone");
  shown.dispose();
  preparing.dispose();
});

// Packaged builds move the top bar out of the stage into the native window's toolbar row (window-chrome.js).
function windowHeader() {
  const header = new El();
  header.className = "window-header";
  const bar = header.appendChild(new El()),
    button = bar.appendChild(new El());
  button.blur = () => {
    button.blurred = true;
  };
  const release = Loading.lockOutside(header);
  return { header, button, release };
}
const HEADER_EVENTS = [
  "pointerdown",
  "pointerup",
  "click",
  "dblclick",
  "contextmenu",
  "wheel",
  "touchstart",
  "touchend",
  "gesturestart",
  "gesturechange",
];
const headerBlocked = ({ header, button }) =>
  HEADER_EVENTS.map((type) => {
    const ev = event(button);
    header.listeners[type](ev);
    return [type, ev.prevented && ev.stopped];
  });

const ENTRIES = {
  hold: (work) => Loading.hold(() => work),
  strict: (work) => Loading.hold(() => work, { all: true }),
  page: (work) => Loading.page(() => work),
  task: (work) => Loading.task(() => work, { label: "Mods" }),
  wait: (work) => Loading.wait(work, { immediate: true, cap: null }),
  scene: (work) => Loading.scene(() => work),
  cover: (work) => Loading.scene(() => work, { opaque: true }),
};
for (const [name, start] of Object.entries(ENTRIES))
  test(`the native toolbar takes no input under Now Loading (${name})`, async () => {
    const row = windowHeader();
    const job = deferred();
    const running = start(job.promise);
    await new Promise(setImmediate);
    try {
      assert.equal(Loading.locked(), true);
      for (const [type, blocked] of headerBlocked(row)) assert.ok(blocked, `${type} on the toolbar`);
      row.button.blurred = false;
      row.header.listeners.focusin(event(row.button));
      assert.equal(row.button.blurred, true, "a toolbar control that takes focus gives it up");
    } finally {
      job.resolve();
      await running;
    }
    await new Promise((r) => setTimeout(r, 350));
    assert.equal(Loading.locked(), false);
    for (const [type, blocked] of headerBlocked(row)) assert.equal(blocked, false, `${type} on the toolbar afterwards`);
    row.release();
  });

test("a toolbar row made while Now Loading is up is locked at once, and its focus given up", async () => {
  const job = deferred();
  const running = Loading.hold(() => job.promise);
  const header = new El(),
    button = header.appendChild(new El());
  button.blur = () => {
    button.blurred = true;
  };
  document.activeElement = button;
  try {
    const release = Loading.lockOutside(header);
    assert.equal(button.blurred, true, "the focused toolbar control gives up its focus");
    const ev = event(button);
    header.listeners.click(ev);
    assert.ok(ev.prevented && ev.stopped);
    release();
    const free = event(button);
    header.listeners.click(free);
    assert.equal(free.stopped, false, "a released row (back in the stage) is left to the stage's lock");
  } finally {
    document.activeElement = null;
    job.resolve();
    await running;
  }
});

test("a full cover hides the native toolbar with the screen; an indicator leaves it shown but locked", async () => {
  const job = deferred(),
    writer = deferred();
  const cover = Loading.scene(() => job.promise, { opaque: true });
  await new Promise(setImmediate);
  const progress = Loading.task(() => writer.promise, { label: "Mods" });
  await new Promise(setImmediate);
  try {
    assert.equal(root.classList.contains("now-loading-cover"), true, "the start cover hides the toolbar");
    job.resolve();
    await cover;
    assert.equal(root.classList.contains("now-loading-cover"), false, "the writer alone is an indicator");
    assert.equal(Loading.locked(), true);
    const row = windowHeader();
    for (const [type, blocked] of headerBlocked(row)) assert.ok(blocked, `${type} on the toolbar under the writer`);
    row.release();
  } finally {
    job.resolve();
    writer.resolve();
    await progress;
  }
  assert.equal(root.classList.contains("now-loading-cover"), false);
});

test("the toolbar row the lock reaches is the one the window chrome builds, and the cover hides it", () => {
  const chrome = readFileSync(new URL("../../src/bmpt/web/js/window-chrome.js", import.meta.url), "utf8");
  const loading = readFileSync(new URL("../../src/bmpt/web/js/loading.js", import.meta.url), "utf8");
  const css = readFileSync(new URL("../../src/bmpt/web/css/app.css", import.meta.url), "utf8");
  assert.match(chrome, /wrapper\.className = ["']window-header["'][\s\S]*unlockHeader = lockOutside\(wrapper\)/);
  assert.match(loading, /export function lockOutside/);
  assert.match(css, /\.now-loading-cover \.window-header \{[^}]*visibility: hidden/);
});

// Every element put outside the stage escapes the stage's input lock and paints above its Now Loading. Each one is
// either the toolbar (locked above) or takes no input of its own; a new one must be covered before it is listed.
const OUTSIDE_STAGE = {
  "window-chrome.js": ["controls", "wrapper"], // the window's own buttons (a system control); the toolbar row
  "recovery.js": ["panel"], // the crash recovery report, shown when the page itself failed
  "cursor.js": ["c"], // touch effects, pointer-events: none
  "thumbs.js": ["host"], // off-screen thumbnail renderer, pointer-events: none
  "settings.js": ["input"], // a clipboard fallback, removed in the same task
  "ngui.js": ["probe"], // a font measuring probe, removed in the same call
};
test("nothing else is attached outside the stage", () => {
  const dir = new URL("../../src/bmpt/web/js/", import.meta.url);
  const found = {};
  const walk = (url) => {
    for (const entry of readdirSync(url, { withFileTypes: true })) {
      if (entry.isDirectory()) {
        walk(new URL(`${entry.name}/`, url));
        continue;
      }
      if (!entry.name.endsWith(".js")) continue;
      const text = readFileSync(new URL(entry.name, url), "utf8");
      for (const m of text.matchAll(
        /document\.(?:body|documentElement)\.(?:append|appendChild|prepend|insertBefore)\(\s*(\w+)/g,
      )) {
        (found[entry.name] ||= []).push(m[1]);
      }
    }
  };
  walk(dir);
  assert.deepEqual(found, OUTSIDE_STAGE);
});
