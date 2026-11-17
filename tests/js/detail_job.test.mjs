// Exercise the detail job's orchestration with stand-ins for the rendered screen and RPC transport.
import { modJob, progressName, steadyFraction } from "../../src/bmpt/web/js/core/job-progress.js";
import { setStrings, T as translate } from "../../src/bmpt/web/js/core/i18n.js";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
// the module's exports as plain declarations
const source = readFileSync(new URL("../../src/bmpt/web/js/detail/detail-actions.js", import.meta.url), "utf8").replace(
  /^export /gm,
  "",
);
const jobSource = source.slice(source.indexOf("async function job("), source.indexOf("function shownMod()"));
const loadingSource = readFileSync(new URL("../../src/bmpt/web/js/app/loading.js", import.meta.url), "utf8");
const progressSource = loadingSource.slice(
  loadingSource.indexOf("export function progressText("),
  loadingSource.indexOf("export async function task("),
);
function harness(overrides = {}) {
  const messages = [],
    notices = [],
    calls = [],
    progress = [];
  let released = false,
    end;
  const env = {
    notify: (s) => messages.push(s),
    notice: (s) => {
      notices.push(s);
      return Promise.resolve();
    },
    modJob,
    modLabel: (m) => m?.label || "",
    progressName,
    steadyFraction,
    fam: {},
    switchKind: () => "spr",
    T: (s) => s,
    renderScreen: async () => {},
    rpc: async () => {
      calls.push("submit");
      return "j1";
    },
    waitJob: async () => ({}),
    reload: async () => {},
    errorText: String,
    QUEUEABLE: ["actions.apply", "actions.restore"],
    refused: (err) => messages.push(String(err)),
    overwrittenMessage: async () => {},
    alt: () => ({}),
    fullName: () => "Example",
    resumeChanged: () => {},
    leftPage: new Promise((resolve) => {
      end = resolve;
    }),
    Loading: {
      hold: async (work) => {
        try {
          return await work((value) => progress.push(value));
        } finally {
          released = true;
        }
      },
    },
    ...overrides,
  };
  const state = new Function(
    "env",
    `let { ${Object.keys(env).join(", ")} } = env;
    let busy = false, open = true, pageToken = 1;
    ${jobSource}
    return { job, busy: () => busy, leave() { open = false; pageToken++; } };`,
  )(env);
  return { ...state, messages, notices, calls, progress, released: () => released, end };
}

test("submission, job, onDone and final reload errors release busy and allow the next switch", async () => {
  for (const where of ["render", "submit", "wait", "done", "reload"]) {
    let fail = true;
    const throws = async () => {
      if (fail) throw new Error(where);
      return "j1";
    };
    const h = harness({
      ...(where === "render" ? { renderScreen: throws } : {}),
      ...(where === "submit" ? { rpc: throws } : {}),
      ...(where === "wait" ? { waitJob: throws } : {}),
    });
    await h.job(
      "actions.apply",
      {},
      { onDone: where === "done" ? throws : undefined, after: where === "reload" ? throws : undefined },
    );
    assert.equal(h.busy(), false, where);
    assert.ok(h.released(), where);
    assert.ok(
      h.messages.some((s) => s.includes(where)),
      where,
    );
    fail = false;
    assert.equal(await h.job("actions.restore", {}), true, where);
    assert.equal(h.busy(), false);
  }
});

test("a second switch is refused; leaving during the job still reports its end without rendering the old page", async () => {
  let finish,
    after = 0;
  const h = harness({
    waitJob: () =>
      new Promise((resolve) => {
        finish = resolve;
      }),
  });
  const job = h.job(
    "actions.apply",
    {},
    {
      after: () => {
        after++;
      },
    },
  );
  await new Promise(setImmediate);
  assert.equal(await h.job("actions.restore", {}), false);
  h.leave();
  h.end();
  finish({});
  assert.equal(await job, true);
  assert.equal(after, 0);
  assert.equal(h.busy(), false);
  assert.ok(!h.messages.includes("job.done"));
});

test("the default job reload does not pass its success flag as a list of preview kinds", async () => {
  const reloads = [];
  const h = harness({
    reload: (...args) => {
      reloads.push(args);
    },
  });
  assert.equal(await h.job("actions.apply", {}), true);
  assert.deepEqual(reloads, [[]]);
});

test("the apply result reaches the refresh while input is still held", async () => {
  const result = { files: [], unchanged: true, deferred: true };
  const seen = [];
  const h = harness({ waitJob: async () => result });
  assert.equal(
    await h.job(
      "actions.apply",
      {},
      {
        onDone: (value) => seen.push(value),
        after: (ok, value) => {
          assert.equal(ok, true);
          assert.equal(h.released(), false);
          seen.push(value);
        },
      },
    ),
    true,
  );
  assert.deepEqual(seen, [result, result]);
  assert.ok(h.released());
  assert.equal(h.busy(), false);
});

test("detail actions keep the verb, target, kind and step from progress in all locales", async () => {
  for (const locale of ["en-US", "zh-CN", "zh-TW", "ja-JP", "ko-KR", "th-TH"]) {
    const strings = JSON.parse(
      readFileSync(new URL(`../../src/bmpt/locales/${locale}.json`, import.meta.url), "utf8"),
    ).webui;
    const T = (key, vars = {}) =>
      key
        .split(".")
        .reduce((o, k) => o[k], strings)
        .replace(/\{(\w+)\}/g, (_, k) => vars[k] ?? "");
    const flat = (o, prefix = "", result = {}) => {
      for (const [key, value] of Object.entries(o)) {
        if (value && typeof value === "object") flat(value, prefix + key + ".", result);
        else result["webui." + prefix + key] = value;
      }
      return result;
    };
    globalThis.document ||= { documentElement: {} };
    globalThis.document.documentElement ||= {};
    setStrings(locale, flat(strings));
    const progressText = new Function("T", `${progressSource.replace("export ", "")}; return progressText;`)(T);
    for (const [method, action, params = {}] of [
      ["actions.apply", "apply"],
      ["actions.apply", "restore", { source: "official" }],
      ["actions.restore", "restore"],
      ["actions.update", "repair"],
      ["actions.extract", "extract"],
      ["mods.remove", "delete"],
    ]) {
      const label = T(`progress.${action}`, { name: "portrait.bundle" });
      const progress = [];
      const h = harness({
        T,
        Loading: { hold: (fn) => fn((value) => progress.push(value)), progressText },
        waitJob: async (id, update) => {
          update({ label, current: 1, total: 3, step: "import.step.crc" });
          return {};
        },
      });
      assert.equal(await h.job(method, params, { name: "Example" }), true);
      const actionKey = `progress.action${action[0].toUpperCase()}${action.slice(1)}`;
      // one item: the verb, who and the kind, then the step; the files' own count is not the line's
      const head = `${T("progress.modHead", { action: T(actionKey) })}Example${T("progress.sep")}${T("tab.portrait")}`;
      assert.deepEqual(
        progress.map((line) => line.text),
        [head, `${head}${T("progress.sep")}${T("import.step.crc")}`],
        `${locale} ${method}`,
      );
      assert.deepEqual(h.messages, []);
      assert.ok(progress.every(({ text }) => !text.includes("portrait.bundle") && !text.includes("3")));
    }
  }
});

test("a 3D model's costume build shows its percentage and step, only growing while the action runs", async () => {
  for (const locale of ["en-US", "zh-CN", "zh-TW", "ja-JP", "ko-KR", "th-TH"]) {
    const strings = JSON.parse(
      readFileSync(new URL(`../../src/bmpt/locales/${locale}.json`, import.meta.url), "utf8"),
    ).webui;
    const flat = (o, prefix = "", result = {}) => {
      for (const [key, value] of Object.entries(o)) {
        if (value && typeof value === "object") flat(value, prefix + key + ".", result);
        else result["webui." + prefix + key] = value;
      }
      return result;
    };
    globalThis.document ||= { documentElement: {} };
    globalThis.document.documentElement ||= {};
    setStrings(locale, flat(strings));
    const progress = [];
    const h = harness({
      T: translate,
      Loading: { hold: (fn) => fn((value) => progress.push(value)) },
      waitJob: async (id, update) => {
        update({ current: 1, total: 1, step: "" });
        update({ current: 1, total: 1, step: "import.step.fitCostume", fraction: 0.42 });
        update({ current: 1, total: 1, fraction: 0.3 }); // an older reading: the shown percentage stays
        update({ current: 1, total: 1, step: "import.step.write", fraction: 1 }); // never 100 % while it runs
        update({ current: 1, total: 4 }); // the files written, counted: the line stays as it is
        update({ current: 2, total: 4, fraction: 0.1 });
        return {};
      },
    });
    assert.equal(await h.job("model.optimize", { alt: "hina", source: "alt:aoba" }, { name: "Example" }), true);
    const sep = translate("progress.sep");
    const head = `${translate("progress.modHead", { action: translate("progress.actionOptimize") })}Example${sep}${translate("tab.portrait")}${sep}${translate("progress.from", { name: "aoba" })}`;
    const percent = (n) => `${sep}${translate("progress.percent", { percent: n })}`;
    const fit = `${sep}${translate("import.step.fitCostume")}`,
      write = `${sep}${translate("import.step.write")}`;
    assert.deepEqual(
      progress.map((line) => line.text),
      [
        head,
        head,
        `${head}${percent(42)}${fit}`,
        `${head}${percent(42)}${fit}`,
        `${head}${percent(99)}${write}`,
        `${head}${percent(99)}${write}`,
        `${head}${percent(99)}${write}`,
      ],
      locale,
    );
  }
});

test("adding from a student's page opens Import Mod instead of a picker of its own", () => {
  const start = source.indexOf("function addMods(");
  const addSource = source.slice(start, source.indexOf("\n}\n", start) + 2);
  const location = { hash: "#/student/base/base" };
  const h = new Function("env", `const { location } = env; ${addSource}; return { addMods };`)({ location });
  h.addMods();
  assert.equal(location.hash, "#/import");
  assert.ok(!source.includes("ModAdd"), "the student page no longer adds through mods.add");
});

test("detail progress identifies the target Mod's student alt and kind instead of the active tab or bundle", async () => {
  const strings = JSON.parse(readFileSync(new URL("../../src/bmpt/locales/en-US.json", import.meta.url), "utf8"));
  const flat = (o, prefix = "", out = {}) => {
    for (const [key, value] of Object.entries(o)) {
      if (value && typeof value === "object") flat(value, `${prefix}${key}.`, out);
      else out[prefix + key] = value;
    }
    return out;
  };
  setStrings("en-US", flat(strings));
  const student = {
    names: { en: "Yoshimi" },
    alts: [
      {
        key: "band",
        names: { en: { full: "Yoshimi (Band)", short: "Band" } },
        modList: [{ id: "m1", target: "band", kind: "lobby" }],
      },
    ],
  };
  const h = harness({
    T: translate,
    fam: student,
    alt: () => ({ key: "base" }),
    waitJob: async (_id, update) => {
      update({ label: "Repairing: assets-_mx-test.bundle", current: 2, total: 2 });
      return {};
    },
  });
  await h.job("actions.update", { mod: "m1" });
  assert.equal(h.progress.at(-1).text, "Repairing: Yoshimi (Band) - Memorial Lobby");
});

test("an apply adds no notice, while mods are off (only saved) or on", async () => {
  for (const result of [
    { deferred: true, unchanged: true, files: [] },
    {},
    { deferred: false, files: ["a"] },
    { unchanged: true },
    null,
  ]) {
    const h = harness({ waitJob: async () => result });
    assert.equal(await h.job("actions.apply", { alt: "v", source: "mod:a" }), true);
    assert.deepEqual(h.notices, [], JSON.stringify(result));
  }
});

test("an apply that failed shows its error and no saved notice; a failing reload after a saved choice still reports both", async () => {
  const failed = harness({
    waitJob: async () => {
      throw new Error("wait");
    },
  });
  assert.equal(await failed.job("actions.apply", {}), false);
  assert.deepEqual(failed.notices, []);
  assert.ok(failed.messages.some((s) => s.includes("wait")));
  const reload = harness({ waitJob: async () => ({ deferred: true }) });
  assert.equal(
    await reload.job(
      "actions.apply",
      {},
      {
        after: async () => {
          throw new Error("reload");
        },
      },
    ),
    false,
  );
  assert.ok(
    reload.messages.some((s) => s.includes("reload")),
    "the error is reported",
  );
  assert.deepEqual(reload.notices, [], "a saved choice adds no notice");
});

test("a notice does not wait for the user: the job returns and releases busy first", async () => {
  let close;
  const h = harness({
    waitJob: async () => ({ kept_current: true }),
    notice: () =>
      new Promise((resolve) => {
        close = resolve;
      }),
  });
  assert.equal(await h.job("actions.apply", {}), true);
  assert.equal(h.busy(), false);
  close();
});
