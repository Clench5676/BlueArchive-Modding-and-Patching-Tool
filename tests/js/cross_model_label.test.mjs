// BL-131: another student's model is named after the student ("Yukino (Original)") on its card, in the 3D tab's subtitle
// and in the name Optimize Physics gives its copy. detail.js's own lines run here without starting the app.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import vm from "node:vm";
import { LOCALES, installFitPage, text } from "./helpers/fit_canvas.mjs";

const page = installFitPage();
const { crossName, studentName } = await import("../../src/bmpt/web/js/names.js");
const { glyphsOf, markedLabel } = await import("../../src/bmpt/web/js/util.js");
const source = readFileSync(new URL("../../src/bmpt/web/js/detail.js", import.meta.url), "utf8");
const lineOf = (prefix) => {
  const at = source.indexOf(prefix);
  assert.ok(at >= 0, prefix);
  return source.slice(at, source.indexOf("\n", at));
};
const blockOf = (prefix) => {
  const at = source.indexOf(prefix);
  assert.ok(at >= 0, prefix);
  return source.slice(at, source.indexOf("\n};\n", at) + 4);
};
const functionSource = (name) => {
  const at = source.indexOf(`function ${name}(`);
  return source.slice(source.lastIndexOf("\n", at) + 1, source.indexOf("\n}\n", at) + 2);
};

const NAME_KEY = { "en-US": "en", "zh-CN": "zh-CN", "zh-TW": "zh-TW", "ja-JP": "ja", "ko-KR": "ko", "th-TH": "th" };
const yukino = { key: "yukino", family: "yukino", custom: [], names: {
  en: { full: "Yukino", short: "Original" }, "zh-CN": { full: "雪乃", short: "原皮" }, "zh-TW": { full: "雪乃", short: "原皮" },
  ja: { full: "ユキノ", short: "通常" }, ko: { full: "유키노", short: "기본" }, th: { full: "ยูกิโนะ", short: "ต้นฉบับ" } } };
const swimsuit = { key: "yukino_swimsuit", family: "yukino", custom: [], names: {
  en: { full: "Yukino (Swimsuit)", short: "Swimsuit" }, "zh-CN": { full: "雪乃（泳装）", short: "泳装" } } };
const families = [{ key: "yukino", base: { en: "Yukino", "zh-CN": "雪乃", "zh-TW": "雪乃", ja: "ユキノ", ko: "유키노", th: "ยูกิโนะ" },
  variants: [yukino, swimsuit] }, { key: "azusa", base: { en: "Azusa" }, variants: [] }];
const own = { key: "azusa", family: "azusa", custom: [], names: { en: { full: "Azusa", short: "Original" }, "zh-CN": { full: "梓", short: "原皮" } } };

// detail.js's label lines with the page's pieces around them
function page_(locale, extra = {}) {
  const key = NAME_KEY[locale];
  const lang = (value) => (value && typeof value === "object" ? value[key] ?? value.en : value);
  const ctx = vm.createContext({ variant: () => own, crossName, studentName, otherStudents: families, L: lang, T: (k, params) => text(locale, k, params),
    shortName: (v) => studentName(v, lang(v.names)?.short ?? v.key), shownSource: () => "v:yukino", ...extra });
  vm.runInContext([lineOf("const originalName ="), lineOf("const fullName ="), blockOf("const crossLabel ="), lineOf("const candidateLabel ="), functionSource("sourceLabel"),
    "Object.assign(globalThis, { candidateLabel, crossLabel });"].join("\n"), ctx);
  return ctx;
}
const card = (variant) => ({ key: `v:${variant.key}`, kind: "variant", variant, cross: true });

const EXPECTED = { "en-US": "Yukino (Original)", "zh-CN": "雪乃（原皮）", "zh-TW": "雪乃（原皮）", "ja-JP": "ユキノ（通常）",
  "ko-KR": "유키노 (기본)", "th-TH": "ยูกิโนะ (ต้นฉบับ)" };
for (const locale of LOCALES) test(`another student's card reads "Student (Costume)" in the language's brackets in ${locale}`, () => {
  const ctx = page_(locale);
  assert.equal(ctx.candidateLabel(card(yukino), own), EXPECTED[locale], "the original costume is named too");
  assert.equal(ctx.candidateLabel(card(yukino)), EXPECTED[locale], "with the default student argument as well");
});

test("another student's costume other than the original, and the student's alias", () => {
  const ctx = page_("zh-CN");
  assert.equal(ctx.candidateLabel(card(swimsuit), own), "雪乃（泳装）");
  assert.equal(page_("en-US").candidateLabel(card(swimsuit), own), "Yukino (Swimsuit)");
  // the user's alias of the variant stands for the student; the costume keeps its name
  const aliased = { ...yukino, custom: ["Yuki"] };
  assert.equal(page_("en-US").candidateLabel(card(aliased), own), "Yuki (Original)");
  assert.equal(page_("zh-CN").candidateLabel(card(aliased), own), "Yuki（原皮）");
  // no family read for the card: the variant's own full name stands in for the student's
  assert.equal(page_("en-US", { otherStudents: [] }).candidateLabel(card(swimsuit), own), "Yukino (Swimsuit)");
});

test("the student's own cards, mods and the original keep their labels", () => {
  const ctx = page_("en-US", { variant: () => own });
  const self = { key: "self", kind: "self", variant: own, label: "Original" };
  const sibling = { key: "v:azusa_swimsuit", kind: "variant", variant: own, label: "Swimsuit" };
  const mod = { key: "m:1", kind: "mod", mod: {}, label: "My mod", origin: own };
  const other = { key: "m:2", kind: "mod", mod: {}, label: "Their mod", origin: { ...own, key: "azusa_swimsuit", names: { en: { short: "Swimsuit" } } } };
  assert.equal(ctx.candidateLabel(self, own), "Original");
  assert.equal(ctx.candidateLabel(sibling, own), "Swimsuit");
  assert.equal(ctx.candidateLabel(mod, own), "My mod");
  assert.equal(ctx.candidateLabel(other, own), "Swimsuit - Their mod");
});

for (const locale of LOCALES) test(`the 3D tab's subtitle names the chosen model of another student after the student in ${locale}`, () => {
  const ctx = page_(locale, { candidates: () => [card(yukino)] });
  assert.equal(ctx.sourceLabel(own, "model"), EXPECTED[locale]);
});

// Optimize Physics names its copy after what the card shows; the stored name then reads "{name} - Optimized"
async function optimizeParams(c, locale) {
  const calls = [];
  const ctx = page_(locale, { busy: false, open: true, switchPopupPage: 1, pageToken: 1, pending: null, variant: () => own,
    $: () => ({ hidden: false, querySelector: () => null }), renderPopup: async () => {}, message: async () => {},
    job: async (method, params, options) => { calls.push({ method, params, options }); options.onDone({ mod: "copy" }); return true; } });
  vm.runInContext(`${lineOf("const optimizeSource =")}\n${functionSource("runOptimize")}`, ctx);
  await ctx.runOptimize(c, own);
  return calls;
}
for (const locale of LOCALES) test(`Optimize Physics on another student's card sends the student's name in ${locale}`, async () => {
  const [call] = await optimizeParams(card(yukino), locale);
  assert.equal(call.method, "model.optimize");
  assert.deepEqual([call.params.variant, call.params.source, call.params.name], ["azusa", "variant:yukino", EXPECTED[locale]]);
  assert.equal(call.options.name, EXPECTED[locale]);
  assert.equal(text(locale, "model.optimizedName", { name: call.params.name }), `${EXPECTED[locale]}${text(locale, "model.optimizedName", { name: "" })}`,
    "the stored copy keeps the locale's own pattern");
});

test("Optimize Physics on the student's own cards keeps its names", async () => {
  const [self] = await optimizeParams({ key: "self", kind: "self", variant: own }, "en-US");
  assert.equal(self.params.name, "Original");
  const [sibling] = await optimizeParams({ key: "v:azusa_swimsuit", kind: "variant", variant: { key: "azusa_swimsuit" }, label: "Swimsuit" }, "en-US");
  assert.equal(sibling.params.name, "Swimsuit");
});

// --- the card bar: 34 down to 24, two lines, 250 - 56 wide (popup.js listCard) ---
const catalog = JSON.parse(readFileSync(new URL("../../src/bmpt/catalog/data/catalog.json", import.meta.url), "utf8"));
test("the card label fitting is the one these tests measure", () => {
  const popup = readFileSync(new URL("../../src/bmpt/web/js/popup.js", import.meta.url), "utf8");
  assert.match(popup, /LIST_CARD_NAME = Object\.freeze\(\{ fs: 34, fsMin: 24, lines: 2, padY: 22, pitch: 1\.2 \}\)/);
  assert.match(popup, /markedLabel\(it\.label, \{ fs: LIST_CARD_NAME.fs, fsMin: LIST_CARD_NAME.fsMin, color: "#ffffff\/1\.00", maxW: w - 56 - \(it\.nameEdit \? 60 : 0\), mark: false, maxLines: LIST_CARD_NAME.lines \}\)/);
  assert.match(popup, /export function listLayout[\s\S]*?card: \{ w: 250, h: 226, bar: 68 \}/);
});

// The card fitting shrinks a long name below its 24 floor, never past the bar: two lines at most, every line inside the
// bar's room. The playable students' costumes (the ones looked for) all stay at 24 or more; the long NPC and enemy names shrink most.
const MAX_W = 250 - 56;
for (const locale of LOCALES) test(`every real student and costume fits the card bar on two lines in ${locale}`, () => {
  page.lang = locale;
  const key = NAME_KEY[locale], T = (k, params) => text(locale, k, params);
  const pick = (names) => names?.[key] ?? names?.en;
  const smallest = { playable: 99, other: 99 };
  let total = 0;
  for (const variant of Object.values(catalog.variants)) {
    const student = catalog.students[variant.student_id], costume = pick(variant.names)?.short;
    if (!student || !costume) continue;
    const label = crossName({ custom: [] }, student.names[key] ?? student.names.en, costume, T);
    const fit = markedLabel(label, { fs: 34, fsMin: 24, color: "#fff", maxW: MAX_W, maxLines: 2 });
    const lines = fit.nodes[0].widgets[0].text.split("\n");
    assert.ok(fit.lines <= 2 && lines.length === fit.lines, `${locale} ${label}: ${fit.lines} lines`);
    for (const line of lines) assert.ok(glyphsOf(line, fit.fs) <= MAX_W + 1e-9, `${locale} ${label}: "${line}" is wider than the bar's ${MAX_W} at ${fit.fs}`);
    const group = variant.kind === "playable" ? "playable" : "other";
    smallest[group] = Math.min(smallest[group], fit.fs);
    total++;
  }
  assert.ok(total > 600, `${total} costumes measured`);
  assert.ok(smallest.playable >= 24, `${locale}: the playable students' smallest size is ${smallest.playable}`);
  assert.ok(smallest.other >= 12, `${locale}: the smallest size is ${smallest.other}`);
});
