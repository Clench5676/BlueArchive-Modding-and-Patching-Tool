// Exercise detail.js's search and check queue without starting the app or its renderers.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import vm from "node:vm";
import * as Sources from "../../src/bmpt/web/js/switch-sources.js";
import { LOCALES, installFitPage } from "./helpers/fit_canvas.mjs";

installFitPage();
const { pickerItems } = await import("../../src/bmpt/web/js/roster-widgets.js");
const source = readFileSync(new URL("../../src/bmpt/web/js/detail.js", import.meta.url), "utf8");
const functionSource = (name) => {
  const at = source.indexOf(`function ${name}(`);
  return source.slice(source.lastIndexOf("\n", at) + 1, source.indexOf("\n}\n", at) + 2);
};
const flush = async () => { for (let i = 0; i < 100; i++) await Promise.resolve(); };

for (const locale of LOCALES) test(`the model search shares picker matching and appends roster-ordered hits (${locale})`, () => {
  const ui = JSON.parse(readFileSync(new URL(`../../src/bmpt/locales/${locale}.json`, import.meta.url), "utf8")).webui;
  const v = { key: "slot", family: "own", model: "slot" };
  const npc = { key: "ch0171", family: "other", model: "ch0171_enemy", label: ui.model.unfit, full: ui.model.unfit, recruitable: false };
  const field = { key: "field", family: "other", fieldModel: "field_npc", label: "A field NPC", full: "A field NPC", recruitable: false };
  const recruit = { key: "recruit", family: "other", model: "recruit", label: "Z recruit", full: "Z recruit", recruitable: true };
  const calls = [];
  const ctx = vm.createContext({ ...Sources, pickerItems, Intl, otherStudents: [{ variants: [recruit, npc, field] }], popupSeg: 0,
    costumeState: new Map(), crossShown: [], language: () => locale, shortName: (o) => o.label, fullName: (o) => o.full,
    originalName: (o) => o.original || o.full, isFav: (o) => !!o.favorite, named: (c) => c,
    judgeCross: (_v, keys = []) => calls.push([...keys]), v,
  });
  vm.runInContext(functionSource("crossItems"), ctx);
  const hits = (query, own = []) => [...ctx.crossItems(v, query, own, (c) => ({ ...c, label: c.variant.label }))];
  assert.deepEqual(hits(""), []);
  assert.deepEqual(calls.at(-1), [], "empty search checks only saved choices");
  assert.deepEqual(hits(ui.model.unfit).map((c) => c.key), ["v:ch0171"], "localized label and full name");
  assert.deepEqual(hits("CH0171_ENEMY").map((c) => c.key), ["v:ch0171"], "dev id matching is case insensitive");
  assert.deepEqual(hits("v:ch0171").map((c) => c.key), ["v:ch0171"], "variant key");
  assert.deepEqual(hits("field_npc").map((c) => c.key), ["v:field"], "field-only NPCs are reachable");
  const ordered = hits(" ").map((c) => c.key);
  assert.deepEqual(ordered, [], "whitespace stays off");
  npc.label = "B NPC"; npc.full = "B NPC";
  field.label = "A NPC"; field.full = "A NPC"; recruit.label = "Z NPC"; recruit.full = "Z NPC";
  assert.deepEqual(hits("NPC").map((c) => c.key), ["v:field", "v:ch0171", "v:recruit"]);
  recruit.favorite = true;
  assert.deepEqual(hits("NPC").map((c) => c.key), ["v:recruit", "v:field", "v:ch0171"], "the roster puts favourites first");
  recruit.favorite = false;
  assert.deepEqual(hits("NPC", ["v:ch0171"]).map((c) => c.key), ["v:field", "v:recruit"], "saved cards are not duplicated");
  ctx.popupSeg = 1;
  assert.deepEqual(hits("NPC").map((c) => c.key), ["v:recruit"]);
  ctx.popupSeg = 2;
  assert.deepEqual(hits("NPC").map((c) => c.key), ["v:field", "v:ch0171"]);
  npc.original = npc.full; npc.label = "User alias"; npc.full = "User alias";
  assert.deepEqual(hits("B NPC").map((c) => c.key), ["v:ch0171"], "original name remains searchable with an alias");
});

function queue({ saved = [], read, check } = {}) {
  const v = { key: "slot", family: "own", model: "slot" }, calls = [], changed = [], failures = [];
  const ctx = vm.createContext({ ...Sources, otherStudents: [], othersEpoch: 0, costumeState: new Map(), open: true, current: v,
    variant: () => ctx.current, candidates: () => saved.map((key) => ({ cross: true, variant: { key } })),
    costumesChecked: (v) => changed.push(v.key), console: { error: (...args) => failures.push(args) },
    rpc: async (method, params) => {
      calls.push({ method, ...params });
      if (method === "model.costumes") return read ? read(params) : Object.fromEntries(params.others.map((key) => [key, null]));
      return check ? check(params) : Object.fromEntries(params.others.map((key) => [key, "preview"]));
    }, waitJob: async (result) => result,
  });
  const start = source.indexOf("let crossWanted ="), end = source.indexOf("// what keeps a 3D source", start);
  vm.runInContext(source.slice(start, end), ctx);
  return { ctx, v, calls, changed, failures, want: (keys, target = ctx.current) => ctx.judgeCross(target, keys) };
}

test("the cross queue checks only current hits and stops old query work after the in-flight pair", async () => {
  let release;
  const q = queue({ check: ({ others }) => others[0] === "first"
    ? new Promise((resolve) => { release = resolve; }) : { [others[0]]: "preview" } });
  q.want(["first", "old-hit"]); await flush();
  assert.ok(release);
  q.want(["new-hit"]);
  release({ first: "ok" }); await flush();
  assert.deepEqual(q.calls.filter((c) => c.method === "model.checkCostumes").map((c) => [...c.others]), [["first"], ["new-hit"]]);
  assert.equal(q.ctx.costumeState.get(q.v.key)["new-hit"], "preview");
  assert.equal(q.ctx.costumeState.get(q.v.key)["old-hit"], null);
  q.want([]); await flush();
  assert.equal(q.calls.filter((c) => c.method === "model.checkCostumes").length, 2);
  assert.deepEqual(q.failures, []);
});

test("64 search hits plus saved choices respect the RPC key limit and reuse saved verdicts", async () => {
  const q = queue({ saved: ["saved"], read: ({ others }) => Object.fromEntries(others.map((key) => [key, "ok"])) });
  const hits = Array.from({ length: 64 }, (_, i) => `hit${i}`);
  q.want(hits); await flush();
  assert.deepEqual(q.calls.map((c) => c.others.length), [64, 1]);
  assert.ok(q.calls.every((c) => c.method === "model.costumes"));
  q.want(hits); await flush();
  assert.equal(q.calls.length, 2, "known hits are not checked again");
});

test("a different destination takes over the queue and leaving the screen stops queued checks", async () => {
  let release;
  const q = queue({ check: ({ variant, others }) => variant === "slot"
    ? new Promise((resolve) => { release = resolve; }) : { [others[0]]: "ok" } });
  q.want(["first", "old-hit"]); await flush();
  q.ctx.current = { key: "next-slot", model: "next-slot" };
  q.want(["next-hit"]); release({ first: "ok" }); await flush();
  assert.deepEqual(q.calls.filter((c) => c.method === "model.checkCostumes").map((c) => [c.variant, ...c.others]),
    [["slot", "first"], ["next-slot", "next-hit"]]);
  const start = q.calls.length;
  q.ctx.open = false; q.want(["unseen"]); await flush();
  assert.equal(q.calls.length, start);
});

test("a failed saved-verdict read can be retried on the next search", async () => {
  let fail = true;
  const q = queue({ read: ({ others }) => {
    if (fail) throw new Error("read failed");
    return Object.fromEntries(others.map((key) => [key, "preview"]));
  } });
  q.want(["hit"]); await flush();
  assert.equal(q.failures.length, 1);
  fail = false; q.want(["hit"]); await flush();
  assert.equal(q.ctx.costumeState.get(q.v.key).hit, "preview");
});

test("Apply uses the verdict gate for cross previews, same-student previews and refusals", async () => {
  for (const key of ["sibling", "cross"]) for (const verdict of ["ok", "preview", "refused", null]) {
    const v = { key: "slot", family: "own", model: "slot" }, calls = [];
    const ctx = vm.createContext({ ...Sources, variant: () => v, fam: { variants: [v, { key: "sibling" }] },
      costumeState: new Map([[v.key, { [key]: verdict }]]), busy: false, isApplied: () => false,
      job: async (method, params) => calls.push({ method, ...params }), switchPopupPage: null, pageToken: 1,
    });
    const at = source.indexOf("const costumeBlock ="), end = source.indexOf("// After a background check", at);
    vm.runInContext(source.slice(at, end) + functionSource("applyCandidate"), ctx);
    await ctx.applyCandidate(`v:${key}`, "model");
    const allowed = verdict === "ok" || (key === "cross" && verdict === "preview") || (key === "sibling" && verdict == null);
    assert.equal(calls.length, Number(allowed), `${key}: ${verdict}`);
    if (allowed) assert.deepEqual(calls[0], { method: "actions.apply", variant: "slot", kind: "model", source: `variant:${key}` });
  }
});

test("a stale cross verdict cannot populate the cache after game resources change", async () => {
  let release;
  const q = queue({ check: () => new Promise((resolve) => { release = resolve; }) });
  q.want(["hit"]); await flush();
  q.ctx.othersEpoch++;
  q.ctx.open = false;
  q.ctx.costumeState.clear();
  release({ hit: "ok" }); await flush();
  assert.equal(q.ctx.costumeState.size, 0);
});

// --- Show other students (BL-131): every other student's model listed, judged as it comes into view ---
const makeSearch = (others, { seg = 0, shown = [] } = {}) => {
  const v = { key: "slot", family: "own", model: "slot" }, calls = [];
  const roster = [{ variants: others }];
  const ctx = vm.createContext({ ...Sources, pickerItems, Intl, otherStudents: roster, popupSeg: seg, crossShown: shown,
    costumeState: new Map(), language: () => "en-US", shortName: (o) => o.label, fullName: (o) => o.label,
    originalName: (o) => o.label, isFav: (o) => !!o.favorite, named: (c) => c, judgeCross: (_v, keys = []) => calls.push([...keys]), v });
  vm.runInContext(functionSource("crossItems"), ctx);
  return { ctx, v, calls, hits: (query = "", own = []) => [...ctx.crossItems(v, query, own, (c) => ({ ...c, label: c.variant.label }))] };
};
const student = (key, label, extra = {}) => ({ key, family: `family-${key}`, model: key, label, recruitable: true, ...extra });

test("with Show other students on, every other student's model follows the own cards, in the roster's order", () => {
  const others = [student("c", "Charlie"), student("a", "Alpha", { favorite: true }), student("b", "Bravo", { recruitable: false }),
    student("f", "Field NPC", { model: undefined, fieldModel: "np1", recruitable: false }), { ...student("own", "Mine"), family: "own" }];
  const q = makeSearch(others);
  try {
    assert.deepEqual(q.hits(), [], "off: an empty query lists the own cards only");
    assert.deepEqual(q.calls.at(-1), []);
    Sources.setShowOthers(true);
    assert.deepEqual(q.hits().map((c) => c.key), ["v:a", "v:b", "v:c", "v:f"], "favourites first, then the roster's name order; the student's own family is not among them");
    assert.deepEqual(q.hits("", ["v:b"]).map((c) => c.key), ["v:a", "v:c", "v:f"], "a card the popup lists already is not repeated");
    assert.ok(q.hits().every((c) => c.cross && c.variant.label === c.label));
    q.ctx.popupSeg = 1;
    assert.deepEqual(q.hits().map((c) => c.key), ["v:a", "v:c"], "the segment applies to them");
    q.ctx.popupSeg = 2;
    assert.deepEqual(q.hits().map((c) => c.key), ["v:b", "v:f"]);
    q.ctx.popupSeg = 0;
    assert.deepEqual(q.hits("NPC").map((c) => c.key), ["v:f"], "so does the search");
    assert.deepEqual(q.hits("zzz"), []);
    q.ctx.costumeState.set("slot", { a: "refused" });
    assert.deepEqual(q.hits().map((c) => c.key), ["v:b", "v:c", "v:f"], "a refused pair is left out");
  } finally { Sources.setShowOthers(false); }
});

test("with Show other students on there is no 64 cap, and only the cards in view are judged", () => {
  const others = Array.from({ length: 150 }, (_, i) => student(`s${String(i).padStart(3, "0")}`, `Student ${String(i).padStart(3, "0")}`));
  const q = makeSearch(others);
  try {
    // a search's hits: at most 64, all judged at once (the round-one behaviour)
    assert.equal(q.hits("Student").length, 64);
    assert.equal(q.calls.at(-1).length, 64);
    assert.deepEqual(q.hits("").length, 0);
    Sources.setShowOthers(true);
    assert.equal(q.hits().length, 150, "all of them are listed");
    assert.deepEqual(q.calls.at(-1), [], "none is in view yet: only the saved choice is judged");
    q.ctx.crossShown = ["s003", "s004", "gone"];
    q.hits();
    assert.deepEqual(q.calls.at(-1), ["s003", "s004"], "the cards in view that are still listed");
    assert.equal(q.hits("Student 14").length, 12, "the search narrows the list (014, 114 and 140 to 149)");
    assert.deepEqual(q.calls.at(-1), [], "cards no longer listed are dropped");
    Sources.setShowOthers(false);
    assert.deepEqual(q.hits(), []);
    assert.deepEqual(q.calls.at(-1), [], "unchecked again: today's cards only");
  } finally { Sources.setShowOthers(false); }
});

function viewing({ variants = [{ key: "sibling" }], on = true, kind = "model" } = {}) {
  const v = { key: "slot", model: "slot" }, calls = [];
  const ctx = vm.createContext({ ...Sources, fam: { variants: [v, ...variants] }, crossShown: [], popupKind: kind, variant: () => ctx.current,
    current: v, judgeCross: (_v, keys) => calls.push([...keys]) });
  vm.runInContext(functionSource("viewCross"), ctx);
  Sources.setShowOthers(on);
  return { ctx, v, calls, view: (keys) => ctx.viewCross(v, keys) };
}

test("the cards in view are what the verdict queue is given, and a scroll replaces them", () => {
  const w = viewing();
  try {
    w.view(["self", "m:mod", "v:sibling", "v:x1", "v:x2"]);
    assert.deepEqual(w.calls, [["x1", "x2"]], "only other students' models: not the own card, a mod, or the student's sibling");
    w.view(["self", "v:sibling", "v:x1", "v:x2"]);
    assert.equal(w.calls.length, 1, "the same cards again: nothing new to ask");
    w.view(["v:x2", "v:x3"]);
    assert.deepEqual(w.calls.at(-1), ["x2", "x3"], "scrolled on: x1 is dropped, x3 queued");
    w.view([]);
    assert.deepEqual(w.calls.at(-1), [], "nothing in view");
  } finally { Sources.setShowOthers(false); }
  const off = viewing({ on: false });
  off.view(["v:x1"]);
  assert.deepEqual(off.calls, [], "unchecked: a search's hits are judged by the search, not by the view");
  assert.deepEqual(off.ctx.crossShown, ["x1"], "the view is still remembered");
  const away = viewing({ kind: "spr" });
  away.view(["v:x1"]);
  assert.deepEqual(away.calls, [], "only for Switch 3D Model");
  Sources.setShowOthers(false);
});

test("in-view cards are read in calls of at most 64 keys, then checked one pair at a time; scrolling drops the rest", async () => {
  let release;
  const q = queue({ check: ({ others }) => others[0] === "v000" ? new Promise((resolve) => { release = resolve; }) : { [others[0]]: "preview" } });
  const first = Array.from({ length: 150 }, (_, i) => `v${String(i).padStart(3, "0")}`);
  q.want(first); await flush();
  assert.deepEqual(q.calls.filter((c) => c.method === "model.costumes").map((c) => c.others.length), [64, 64, 22]);
  assert.ok(release, "the first pair is being checked");
  const second = ["w0", "w1"];
  q.want(second);
  release({ v000: "ok" }); await flush();
  const checked = q.calls.filter((c) => c.method === "model.checkCostumes").map((c) => [...c.others]);
  assert.deepEqual(checked, [["v000"], ["w0"], ["w1"]], "the cards scrolled out are not checked; the new ones are, one at a time");
  assert.ok(checked.every((others) => others.length === 1));
});

test("another student's card is drawn like any card: no grey filter anywhere in the switch or the list", () => {
  const read = (name) => readFileSync(new URL(`../../src/bmpt/web/js/${name}`, import.meta.url), "utf8");
  assert.ok(!/shade:\s*true|\bSHADE\b|\.shade\b/.test(read("detail.js")), "detail.js");
  assert.ok(!/shade:\s*true|\bSHADE\b|\.shade\b/.test(read("popup.js")), "popup.js");
});

// --- Optimize Physics answers (BL-131): the official model needs none; a costume no physics rule improves has no gain ---
const LOCALE_FILES = ["en-US", "zh-CN", "zh-TW", "ja-JP", "ko-KR", "th-TH"];
const ui = (locale) => JSON.parse(readFileSync(new URL(`../../src/bmpt/locales/${locale}.json`, import.meta.url), "utf8")).webui;
async function optimizeAnswer(result, locale = "en-US") {
  const web = ui(locale), messages = [];
  const ctx = vm.createContext({ busy: false, open: true, switchPopupPage: 1, pageToken: 1, pending: "v:x", variant: () => ({ key: "slot" }),
    shortName: () => "Slot", candidateLabel: () => "Costume", $: () => ({ hidden: false, querySelector: () => null }),
    T: (key) => key.split(".").reduce((o, part) => o?.[part], web), renderPopup: async () => {},
    message: async (opts) => { messages.push(opts); }, job: async (_method, _params, { onDone }) => { onDone(result); return true; } });
  const start = source.indexOf("const optimizeSource ="), end = source.indexOf("\n", start);
  vm.runInContext(`${source.slice(start, end)}\n${functionSource("runOptimize")}`, ctx);
  await ctx.runOptimize({ kind: "variant", variant: { key: "costume" }, key: "v:costume" });
  return { messages, pending: ctx.pending, web };
}

for (const locale of LOCALE_FILES) test(`Optimize Physics says why there is nothing to build, in ${locale}`, async () => {
  const official = await optimizeAnswer({ unneeded: true, reason: "official" }, locale);
  assert.deepEqual(official.messages.map((m) => [...m.lines]), [[official.web.model.optimizeUnneeded]]);
  const noGain = await optimizeAnswer({ unneeded: true, reason: "no_gain" }, locale);
  assert.deepEqual(noGain.messages.map((m) => [...m.lines]), [[noGain.web.model.optimizeNoGain]]);
  assert.notEqual(noGain.web.model.optimizeNoGain, noGain.web.model.optimizeUnneeded);
  const older = await optimizeAnswer({ unneeded: true }, locale);
  assert.deepEqual(older.messages.map((m) => [...m.lines]), [[older.web.model.optimizeUnneeded]], "no reason: the old answer");
  const made = await optimizeAnswer({ mod: "copy" }, locale);
  assert.deepEqual([made.messages.length, made.pending], [0, "m:copy"]);
});

test("the new Optimize Physics answer reads as agreed in zh-CN and en-US", () => {
  assert.equal(ui("zh-CN").model.optimizeNoGain, "暂无可用的物理优化");
  assert.equal(ui("en-US").model.optimizeNoGain, "No physics optimization available");
  assert.equal(ui("zh-CN").model.optimizeUnneeded, "无需优化", "the official model's answer is unchanged");
  assert.equal(ui("en-US").model.optimizeUnneeded, "No optimization needed");
});
