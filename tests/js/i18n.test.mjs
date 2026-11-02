// Tests for web/js/i18n.js: the name language of each UI language and what stands in for a missing name. Run by
// tests/test_i18n.py with Node's test runner: node --test tests/js/i18n.test.mjs
import assert from "node:assert/strict";
import { test } from "node:test";

globalThis.document = { documentElement: { lang: "" } };
const { L, NAME_LANG, T, language, nameKeys, setStrings, languageRefresh } = await import("../../src/ba_modding_toolkit/web/js/i18n.js");

const names = { "zh-CN": "星野", "zh-TW": "星野", en: "Hoshino", ja: "ホシノ", ko: "호시노" };

test("every UI language has its name language", () => {
  assert.deepEqual(NAME_LANG, { "zh-CN": "zh-CN", "zh-TW": "zh-TW", "en-US": "en", "ja-JP": "ja", "ko-KR": "ko", "th-TH": "th" });
  for (const [ui, key] of Object.entries(NAME_LANG)) {
    setStrings(ui, {});
    assert.equal(language(), ui);
    assert.equal(document.documentElement.lang, ui); // the page's lang picks the font stack (app.css :lang)
    assert.equal(L(names), names[key] ?? names.en, ui);
  }
});

test("a missing name falls back to English, Simplified Chinese only for Simplified Chinese", () => {
  const noOwn = (drop) => Object.fromEntries(Object.entries(names).filter(([k]) => k !== drop));
  setStrings("ko-KR", {});
  assert.equal(L(noOwn("ko")), "Hoshino");
  setStrings("zh-TW", {});
  assert.equal(L(noOwn("zh-TW")), "Hoshino"); // never the Simplified name in a Traditional screen
  setStrings("ja-JP", {});
  assert.equal(L(noOwn("ja")), "Hoshino");
  setStrings("zh-CN", {});
  assert.equal(L(noOwn("zh-CN")), "Hoshino");
  assert.equal(L({ ko: "호시노" }), "호시노"); // whatever there is, last
  assert.equal(L("plain"), "plain");
  assert.deepEqual(nameKeys("zh-SG"), ["zh-SG", "zh-CN", "en"]);
  assert.deepEqual(nameKeys("zh-Hant-HK"), ["zh-Hant-HK", "en", "zh-CN"]);
  assert.deepEqual(nameKeys("ko-KR"), ["ko", "en", "zh-CN"]);
});

test("Thai student and variant names use English until Thai source values exist", () => {
  setStrings("th-TH", {});
  assert.deepEqual(nameKeys("th-TH"), ["th", "en", "zh-CN"]);
  assert.equal(L(names), "Hoshino");
  assert.equal(L({ en: "Swimsuit", "zh-CN": "\u6cf3\u88c5" }), "Swimsuit");
  assert.equal(L({ ...names, th: "โฮชิโนะ" }), "โฮชิโนะ");
});

test("strings are looked up with their variables", () => {
  setStrings("ko-KR", { "webui.hello": "{name} 안녕" });
  assert.equal(T("hello", { name: "호시노" }), "호시노 안녕");
  assert.equal(T("missing"), "missing");
});

test("repeated language selection reuses tables and only refreshes retained UI", async () => {
  const loaded = [], refreshed = [];
  const player = { frame: 42 }, route = "#/student/ako/ako/lobby";
  const change = languageRefresh({ load: async (lang) => { loaded.push(lang); return { "webui.caption": lang }; },
    refresh: async (lang) => { refreshed.push([lang, T("caption"), player, route]); } });
  for (const lang of ["en-US", "ja-JP", "en-US", "ja-JP"]) assert.equal(await change(lang), true);
  assert.deepEqual(loaded, ["en-US", "ja-JP"]);
  assert.deepEqual(refreshed.map(([lang, text]) => [lang, text]), [["en-US", "en-US"], ["ja-JP", "ja-JP"], ["en-US", "en-US"], ["ja-JP", "ja-JP"]]);
  assert.ok(refreshed.every(([, , kept, hash]) => kept === player && hash === route));
  assert.equal(player.frame, 42);
});

test("a late locale response cannot revert a newer selected language", async () => {
  const replies = new Map(), refreshed = [];
  const change = languageRefresh({ load: (lang) => new Promise((resolve) => replies.set(lang, resolve)), refresh: (lang) => refreshed.push(lang) });
  const first = change("en-US"), second = change("ja-JP");
  await Promise.resolve();
  replies.get("ja-JP")({ "webui.caption": "new" });
  assert.equal(await second, true);
  replies.get("en-US")({ "webui.caption": "old" });
  assert.equal(await first, false);
  assert.equal(language(), "ja-JP");
  assert.equal(T("caption"), "new");
  assert.deepEqual(refreshed, ["ja-JP"]);
});

test("a failed locale read preserves the active table and can be retried", async () => {
  setStrings("en-US", { "webui.caption": "retained" });
  let attempts = 0;
  const change = languageRefresh({ load: async () => { if (++attempts === 1) throw new Error("offline"); return { "webui.caption": "retried" }; }, refresh() {} });
  await assert.rejects(change("ja-JP"), /offline/);
  assert.equal(T("caption"), "retained");
  await change("ja-JP");
  assert.equal(T("caption"), "retried");
});

// UI-37: one error text for the whole UI: never a raw key (error.undefined) or an empty detail ("Something went wrong: ")
test("errorText gives a coded error its string and an uncoded one error.internal with a detail or none at all", async () => {
  const { readFileSync } = await import("node:fs");
  const { errorText } = await import("../../src/ba_modding_toolkit/web/js/i18n.js");
  const flat = (o, p = "webui") => Object.entries(o).flatMap(([k, v]) => typeof v === "object" ? flat(v, `${p}.${k}`) : [[`${p}.${k}`, v]]);
  class RpcError extends Error { constructor(code, params = {}) { super(code); this.code = code; this.params = params; } } // as api.js
  try {
    for (const locale of ["en-US", "zh-CN", "zh-TW", "ja-JP", "ko-KR", "th-TH"]) {
      const ui = JSON.parse(readFileSync(new URL(`../../src/ba_modding_toolkit/locales/${locale}.json`, import.meta.url), "utf8")).webui;
      setStrings(locale, Object.fromEntries(flat(ui)));
      const internal = ui.error.internal, bare = internal.slice(0, internal.indexOf("{error}")).replace(/[\s:\uFF1A]+$/u, "");
      assert.ok(bare && !/[:\uFF1A]\s*$/u.test(bare), locale);
      // a coded error: its own string with its params
      assert.equal(errorText(new RpcError("busy")), ui.error.busy, locale);
      assert.equal(errorText({ code: "data_dir_readonly", params: { path: "/x" } }), ui.error.data_dir_readonly.replace("{path}", "/x"));
      // error.internal with what the error says of itself
      assert.equal(errorText(new RpcError("internal", { error: "HTTP 500" })), internal.replace("{error}", "HTTP 500"), locale);
      assert.equal(errorText(new TypeError("x is not a function")), internal.replace("{error}", "TypeError: x is not a function"), locale);
      assert.equal(errorText("offline"), internal.replace("{error}", "offline"));
      // nothing to say (a job that failed without a code or params, no error at all): error.internal without its detail
      for (const err of [new RpcError("internal", {}), new RpcError(undefined, undefined), { code: undefined }, {}, null, undefined, new Error("")]) {
        const text = errorText(err);
        assert.equal(text, bare, `${locale}: ${JSON.stringify(err)}`);
        assert.ok(!text.includes("error."), `${locale}: no raw key`);
      }
      // a caller's own translator (quit.js passes the page's T)
      assert.equal(errorText(new RpcError("busy"), (key) => `<${key}>`), "<error.busy>");
    }
  } finally { setStrings("zh-CN", {}); }
});

test("the sites the review lists draw their error text through the shared errorText", async () => {
  const { readFileSync } = await import("node:fs");
  const read = (name) => readFileSync(new URL(`../../src/ba_modding_toolkit/web/js/${name}`, import.meta.url), "utf8");
  for (const name of ["consent.js", "update.js", "quit.js", "mod-add.js", "settings.js"]) {
    const source = read(name);
    assert.doesNotMatch(source, /`error\.\$\{/, `${name} builds no error key of its own`);
    assert.doesNotMatch(source, /const errorText =/, `${name} has no errorText of its own`);
    assert.match(source, /errorText\(/, name);
  }
  assert.equal(read("consent.js").match(/errorText\(err\)/g).length, 3, "the folder change, the Spine consent and the scan consent");
  const main = read("main.js");
  assert.match(main, /catch \(err\) \{ notify\(errorText\(err\), \{ kind: "error", error: err \}\); \}/, "the no-game screen's folder");
  assert.match(main, /\} catch \(err\) \{\n {6}notify\(errorText\(err\), \{ kind: "error", error: err \}\);/, "files dropped on the window");
});
