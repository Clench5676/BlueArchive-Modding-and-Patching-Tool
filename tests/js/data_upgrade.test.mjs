import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import vm from "node:vm";
import {
  ACTIONS,
  REASONS,
  bootstrapRequest,
  noticeView,
  startGate,
  upgradeGate,
  upgradedNoticeFor,
} from "../../src/bmpt/web/js/app/data-upgrade.js";

const web = new URL("../../src/bmpt/web/js/", import.meta.url);
const localeFiles = new URL("../../src/bmpt/locales/", import.meta.url);
const recoverySource = readFileSync(new URL("app/recovery.js", web), "utf8");
const locales = ["en-US", "zh-CN", "zh-TW", "ja-JP", "ko-KR", "th-TH"];
const keys = [
  "title",
  "paused",
  "retry",
  "update",
  "openLog",
  "quit",
  "import",
  "keep",
  "relocate",
  "changed",
  "affected",
  "backup",
  "upgradedWarning",
  "reason.newerFormatData",
  ...REASONS.map((r) => `reason.${r}`),
];

function localeStrings(locale) {
  const raw = JSON.parse(readFileSync(new URL(`${locale}.json`, localeFiles), "utf8")).webui.upgrade;
  const flatten = (object, prefix) =>
    Object.fromEntries(
      Object.entries(object).flatMap(([key, value]) => {
        const name = `${prefix}.${key}`;
        return typeof value === "string" ? [[name, value]] : Object.entries(flatten(value, name));
      }),
    );
  return flatten(raw, "upgrade");
}
const strings = localeStrings("en-US");
const state = (status, details = {}) => ({ status, phase: "", reason: null, progress: null, strings, ...details });

class El {
  children = [];
  dataset = {};
  textContent = "";
  constructor(tag) {
    this.tag = tag;
  }
  append(...children) {
    children.forEach((child) => this.appendChild(child));
  }
  appendChild(child) {
    this.children.push(child);
    child.parent = this;
  }
  insertBefore(child, anchor) {
    this.children.splice(this.children.indexOf(anchor), 0, child);
    child.parent = this;
  }
  remove() {
    if (this.parent) this.parent.children = this.parent.children.filter((child) => child !== this);
  }
  setAttribute() {}
  querySelectorAll(tag) {
    return this.children.filter((child) => child.tag === tag);
  }
}

function harness() {
  const body = new El("body"),
    stage = new El("main");
  body.appendChild(stage);
  const ctx = {
    BAMT_CONFIG: { language: "en-US", strings },
    document: {
      body,
      createElement: (tag) => new El(tag),
      getElementById: () => stage,
      addEventListener() {},
    },
    addEventListener() {},
    setTimeout: () => 1,
    clearTimeout() {},
  };
  ctx.window = ctx;
  vm.runInNewContext(recoverySource, ctx);
  return { api: ctx.BAMTRecovery, stage, panel: () => body.children.find((child) => child.id === "bamt-recovery") };
}

test("all six locales supply the upgrade captions and no consent text", () => {
  for (const locale of locales) {
    const translated = localeStrings(locale);
    assert.deepEqual(
      Object.keys(translated).sort(),
      keys.toSorted().map((key) => `upgrade.${key}`),
      locale,
    );
    assert.ok(
      Object.values(translated).every((value) => value.trim()),
      locale,
    );
    const ui = JSON.parse(readFileSync(new URL(`${locale}.json`, localeFiles), "utf8")).webui;
    assert.equal(ui.error.protective_copies_needed, undefined, `${locale}: no protective-copy refusal text`);
  }
  assert.equal(localeStrings("zh-CN")["upgrade.upgradedWarning"], "数据格式已升级，请勿再打开旧版本");
  assert.equal(localeStrings("en-US")["upgrade.upgradedWarning"], "Data format upgraded. Do not open older versions");
});

test("the recovery overlay draws the running and changed notices", () => {
  const h = harness();
  for (const [snapshot, title, hint, actions] of [
    [state("running", { progress: { done: 2, total: 7 } }), "Data Upgrade", "2 / 7", []],
    [state("changed", { affected: ["/data/a", "/data/b"] }), "Old Data Changed", "", ["import", "keep", "quit"]],
  ]) {
    h.api.notice(noticeView(snapshot), () => {});
    assert.equal(h.panel().children[0].textContent, title);
    assert.equal(h.panel().children[1].textContent, hint);
    assert.deepEqual(
      h
        .panel()
        .querySelectorAll("button")
        .map((item) => item.dataset.action),
      actions,
    );
    assert.equal(h.stage.inert, true);
    if (snapshot.status === "changed") {
      assert.deepEqual(
        h
          .panel()
          .children.slice(2, 6)
          .map((entry) => entry.textContent),
        ["Affected Data Folders", "/data/a", "/data/b", "Current Data Backup"],
      );
    }
  }
  let chosen;
  h.api.notice(noticeView(state("changed", { affected: [] })), (id) => {
    chosen = id;
  });
  const buttons = h.panel().querySelectorAll("button");
  buttons[1].onclick();
  assert.equal(chosen, "keep");
  assert.ok(buttons.every((button) => button.disabled));
  h.api.closeNotice();
  assert.equal(h.panel(), undefined);
  assert.equal(h.stage.inert, false);
});

test("failed reasons render their localized captions and only their available actions", () => {
  const h = harness();
  for (const [reason, phase, hint, actions] of [
    ["busy", "", "Mod Library in Use", ["retry", "relocate", "openLog", "quit"]],
    ["olderVersion", "", "Close Older Version", ["retry", "openLog", "quit"]],
    ["classic", "", "Close Classic UI", ["retry", "openLog", "quit"]],
    ["unavailable", "", "Mod Library Unavailable", ["retry", "relocate", "openLog", "quit"]],
    ["unsupportedStorage", "", "Unsupported Storage", ["retry", "relocate", "openLog", "quit"]],
    ["recoveryPending", "", "Recovery Pending", ["retry", "openLog", "quit"]],
    ["newerFormat", "library", "Mod Library Upgraded by a Newer Version", ["update", "openLog", "quit"]],
    ["newerFormat", "data", "Data Upgraded by a Newer Version", ["update", "openLog", "quit"]],
    ["error", "", "", ["retry", "openLog", "quit"]],
  ]) {
    h.api.notice(noticeView(state("failed", { reason, phase })), () => {});
    assert.equal(h.panel().children[0].textContent, strings["upgrade.paused"]);
    assert.equal(h.panel().children[1].textContent, hint);
    assert.deepEqual(
      h
        .panel()
        .querySelectorAll("button")
        .map((item) => item.dataset.action),
      actions,
    );
  }
  assert.ok(!noticeView(state("failed", { reason: "unavailable" }), false).actions.some(({ id }) => id === "relocate"));
});

test("requests use the RPC session cookie and carry no body", async () => {
  const requests = [];
  const request = bootstrapRequest(async (url, options) => {
    requests.push({ url, ...options });
    return { ok: true, json: async () => state("ready") };
  });
  await request("state");
  for (const { path, body } of Object.values(ACTIONS)) await request(path, body);
  assert.deepEqual(
    requests.map(({ url, method }) => [url, method]),
    [["/upgrade/state", "GET"], ...Object.values(ACTIONS).map(({ path }) => [`/upgrade/${path}`, "POST"])],
  );
  for (const { credentials, cache } of requests) {
    assert.equal(credentials, "same-origin");
    assert.equal(cache, "no-store");
  }
  assert.deepEqual(Object.keys(ACTIONS).sort(), ["import", "keep", "openLog", "quit", "relocate", "retry", "update"]);
  assert.ok(requests.every(({ body }) => body === undefined));
  for (const status of [403, 404]) {
    await assert.rejects(bootstrapRequest(async () => ({ ok: false, status }))("state"), new RegExp(`HTTP ${status}`));
  }
});

test("each notice action uses its endpoint, and Quit blocks App startup even if the reply is ready", async () => {
  const h = harness();
  for (const [choice, snapshot] of [
    ["retry", state("failed", { reason: "busy" })],
    ["update", state("failed", { reason: "newerFormat" })],
    ["openLog", state("failed", { reason: "recoveryPending" })],
    ["quit", state("failed", { reason: "error" })],
    ["keep", state("changed", { affected: ["/one"] })],
    ["import", state("changed", { affected: ["/one"] })],
    ["relocate", state("failed", { reason: "unavailable" })],
  ]) {
    const calls = [];
    let active = 0;
    const ready = await upgradeGate({
      request: async (path, body) => {
        calls.push([path, body]);
        return path === "state" ? snapshot : state("ready");
      },
      show: (view) =>
        new Promise((resolve) => {
          h.api.notice(view, resolve);
          if (view.actions.length) {
            const button = h
              .panel()
              .querySelectorAll("button")
              .find((item) => item.dataset.action === choice);
            assert.ok(button, choice);
            button.onclick();
          }
        }),
      hide: h.api.closeNotice,
      hold: () => {
        active++;
        return () => active--;
      },
    });
    assert.deepEqual(calls, [
      ["state", undefined],
      [ACTIONS[choice].path, undefined],
    ]);
    assert.equal(ready?.status, choice === "quit" ? undefined : "ready");
    assert.equal(active, choice === "quit" ? 1 : 0);
  }
});

test("the gate shows progress during a pending write action, then releases startup", async () => {
  let finish;
  const pending = new Promise((resolve) => {
    finish = resolve;
  });
  const hints = [];
  let reads = 0,
    released = false;
  const ready = await upgradeGate({
    request: (path) =>
      path === "state"
        ? Promise.resolve(
            reads++ === 0
              ? state("changed", { affected: ["/one"] })
              : state("running", { progress: { done: 5, total: 8 } }),
          )
        : pending,
    show: async (view) => {
      hints.push(view.hint);
      if (view.actions.length) return "keep";
      if (view.hint) finish(state("ready"));
      return null;
    },
    hide() {},
    hold: () => () => {
      released = true;
    },
  });
  assert.equal(ready.status, "ready");
  assert.ok(hints.includes("5 / 8"));
  assert.equal(released, true);
});

test("the page's startup gate releases App startup only after the ready response", async (t) => {
  const h = harness();
  t.mock.method(globalThis, "fetch", async (url) => ({
    ok: true,
    json: async () => (url === "/upgrade/state" ? state("changed", { affected: ["/one"] }) : state("ready")),
  }));
  let started = false;
  const boot = startGate(h.api, {}).then(() => {
    started = true;
  });
  await new Promise(setImmediate);
  assert.equal(started, false);
  h.panel()
    .querySelectorAll("button")
    .find((item) => item.dataset.action === "keep")
    .onclick();
  await boot;
  assert.equal(started, true);
  assert.equal(h.panel(), undefined);
  assert.equal(h.stage.inert, false);
});

test("a failed first read or action stays paused until the server returns ready", async () => {
  const requests = [],
    views = [];
  await upgradeGate({
    strings,
    request: async (path) => {
      requests.push(path);
      if (requests.length < 3) throw new Error("HTTP 404");
      return state("ready");
    },
    show: async (view) => {
      if (!view.actions.length) return new Promise(() => {});
      views.push(view.title);
      return "retry";
    },
    hide() {},
  });
  assert.deepEqual(requests, ["state", "retry", "retry"]);
  assert.deepEqual(views, ["Data Upgrade Paused", "Data Upgrade Paused"]);
});

function settingsMove() {
  const source = readFileSync(new URL("settings/settings.js", web), "utf8");
  const slice = source.slice(
    source.indexOf("export async function setSetting"),
    source.indexOf("async function saveSetting"),
  );
  return new Function("T", "errorText", "rpc", "Popup", `${slice.replace(/^export /gm, "")}\nreturn setSetting;`)(
    (key) => strings[key] ?? key,
    (error) => error.code,
    () => assert.fail("tests pass their own request"),
    { message: () => assert.fail("tests pass their own question") },
  );
}

test("Settings asks once to adopt an existing library and sends no protection choice", async () => {
  const move = settingsMove(),
    calls = [],
    questions = [];
  const result = await move(
    "libraryDir",
    "/chosen",
    async (method, params) => {
      calls.push([method, params]);
      if (calls.length === 1) throw { code: "library_target_not_empty", params: { adoptable: true } };
      return { libraryDir: "/chosen" };
    },
    async (options) => {
      questions.push(options);
      return { button: "ok" };
    },
  );
  assert.deepEqual(result, { libraryDir: "/chosen" });
  assert.deepEqual(calls, [
    ["settings.set", { key: "libraryDir", value: "/chosen" }],
    ["settings.set", { key: "libraryDir", value: { path: "/chosen", adopt: true } }],
  ]);
  assert.equal(questions.length, 1);
});

test("declining the adoption leaves the move pending, and any other refusal is thrown as it is", async () => {
  const move = settingsMove();
  let sends = 0;
  assert.equal(
    await move(
      "libraryDir",
      "/chosen",
      async () => {
        sends++;
        throw { code: "library_target_not_empty", params: { adoptable: true } };
      },
      async () => ({ button: null }),
    ),
    null,
  );
  assert.equal(sends, 1);
  for (const code of ["library_target_not_empty", "busy"]) {
    await assert.rejects(
      move(
        "libraryDir",
        "/chosen",
        async () => {
          throw { code, params: { adoptable: false } };
        },
        async () => assert.fail("no question"),
      ),
      { code },
    );
  }
});

const upgradedHarness = (notice) => {
  const shown = [],
    requests = [];
  let answer = { button: "ok" };
  const T = (key) => strings[key] ?? key;
  const open = upgradedNoticeFor(state("ready", { notice }), {
    message: async (options) => {
      shown.push(options);
      return answer;
    },
    T,
    warning: "#f14f00",
    request: async (path, body) => {
      requests.push([path, body]);
      return state("ready");
    },
  });
  return {
    open,
    shown,
    requests,
    answer: (value) => {
      answer = value;
    },
  };
};

test("the upgraded notice shows once for notice upgraded: title, the line in the warning ink, OK only", async () => {
  const h = upgradedHarness("upgraded");
  assert.equal(typeof h.open, "function");
  assert.equal(await h.open(), true);
  assert.equal(h.shown.length, 1);
  assert.deepEqual(h.shown[0], {
    title: "Data Upgrade",
    lines: [{ text: "Data format upgraded. Do not open older versions", color: "#f14f00" }],
    ok: "picker.ok",
  });
  assert.equal(h.shown[0].cancel, undefined);
  assert.equal(h.shown[0].buttons, undefined);
  assert.deepEqual(h.requests, [["acknowledge", undefined]]);
  assert.equal(await h.open(), true, "the start sequence and the no-game start share one popup");
  assert.equal(h.shown.length, 1);
  assert.equal(h.requests.length, 1);
});

test("the upgraded notice acknowledges only when confirmed, and is absent when none is due", async () => {
  for (const notice of [null, undefined, "other"]) {
    assert.equal(upgradedHarness(notice).open, null, String(notice));
  }
  assert.equal(upgradedNoticeFor(null, { message: () => assert.fail("no notice") }), null);
  const closed = upgradedHarness("upgraded");
  closed.answer({ button: null });
  assert.equal(await closed.open(), false);
  assert.deepEqual(closed.requests, [], "closing without OK leaves the notice due");
  const failing = upgradedNoticeFor(state("ready", { notice: "upgraded" }), {
    message: async () => ({ button: "ok" }),
    T: (key) => key,
    warning: "#f14f00",
    request: async () => {
      throw new Error("HTTP 500");
    },
  });
  await assert.rejects(failing(), /HTTP 500/);
});

test("the gate hands the ready state with its notice to the page", async () => {
  const ready = await upgradeGate({
    strings,
    request: async () => state("ready", { notice: "upgraded" }),
    show: async () => assert.fail("a ready state shows no overlay"),
    hide() {},
  });
  assert.equal(ready.notice, "upgraded");
  assert.equal(typeof upgradedNoticeFor(ready, { message() {}, T: (key) => key, warning: "w" }), "function");
});

test("no consent path remains in the page's upgrade code", () => {
  const source = readFileSync(new URL("app/data-upgrade.js", web), "utf8");
  assert.doesNotMatch(source, /consent|protect|extraSpace/i);
  assert.equal(
    noticeView(state("consent")).actions.some(({ id }) => id === "continue" || id === "protect"),
    false,
  );
});
