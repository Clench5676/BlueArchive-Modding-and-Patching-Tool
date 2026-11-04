// release-notes.js: a release's Markdown, read in the UI language and drawn rather than shown (update.js notesLines).
import assert from "node:assert/strict";
import { test } from "node:test";
import { readFileSync } from "node:fs";
import { sections, pick, scriptOf, inline, blocks, notesFor } from "../../src/bmpt/web/js/release-notes.js";

const shipped = readFileSync(new URL("../../src/bmpt/assets/release-notes/0.7.0.md", import.meta.url), "utf8");
const HAN = "新功能与修复"; // a short Chinese heading
const KANA = "あたらしい", HANGUL = "새로운 기능", THAI = "คุณสมบัติ";

test("sections split at rules and language marks; an unmarked one is told by its script", () => {
  const body = `## What's New\n- one\n\n---\n\n## ${HAN}\n- ${HAN}`;
  assert.deepEqual(sections(body).map((s) => s.lang), ["en", "zh-CN"], "0.6.0's body: English, a rule, Chinese");
  const marked = `<!-- lang: en -->\n## A\n---\n<!-- lang: zh-TW -->\n## ${HAN}\n***\n<!-- lang: ja -->\n## B`;
  assert.deepEqual(sections(marked).map((s) => s.lang), ["en", "zh-TW", "ja"]);
  assert.deepEqual(sections("\n\n---\n\n").length, 0);
  assert.deepEqual([KANA + HAN, HANGUL, THAI, HAN, "Plain text"].map(scriptOf), ["ja", "ko", "th", "zh-CN", "en"]);
});

test("the UI language's section, else its other variant, else English, else the first", () => {
  const list = [{ lang: "en", lines: ["E"] }, { lang: "zh-CN", lines: ["C"] }];
  const chosen = (lang, from = list) => pick(from, lang).lines[0];
  assert.equal(chosen("zh-CN"), "C"); assert.equal(chosen("zh-TW"), "C"); assert.equal(chosen("en-US"), "E");
  for (const lang of ["ja-JP", "ko-KR", "th-TH", ""]) assert.equal(chosen(lang), "E");
  assert.equal(chosen("ja-JP", [{ lang: "ko", lines: ["K"] }, { lang: "zh-CN", lines: ["C"] }]), "K");
  assert.equal(pick([], "en-US"), null);
});

test("Markdown marks go and their text stays; nothing is read as HTML", () => {
  assert.equal(inline("**Student Hall:** select a `portrait`"), "Student Hall: select a portrait");
  assert.equal(inline("see [the release](https://example.invalid) and ![art](x.png)"), "see the release and art");
  assert.equal(inline("*one* _two_ ~~three~~ snake_case_name 2 * 3"), "one two three snake_case_name 2 * 3");
  assert.equal(inline("<b>bold</b> <script>x</script> <https://example.invalid/a>"), "bold x https://example.invalid/a");
  assert.equal(inline("\\*kept\\* <!-- note -->"), "*kept*");
});

test("headings, nested and numbered items, continued items and plain lines keep their own lines", () => {
  const got = blocks(["# Title", "", "## What's New", "", "- **One:** first", "  continued", "  - nested", "1. numbered",
    "", "Plain", "Next line", "### Small"]);
  assert.deepEqual(got.map(({ kind, text, level, depth, marker, gap, under }) => ({ kind, text, level, depth, marker, gap, under })), [
    { kind: "heading", text: "Title", level: 1, depth: undefined, marker: undefined, gap: false, under: undefined },
    { kind: "heading", text: "What's New", level: 2, depth: undefined, marker: undefined, gap: true, under: undefined },
    { kind: "item", text: "One: first", level: undefined, depth: 0, marker: "•", gap: true, under: undefined },
    { kind: "text", text: "continued", level: undefined, depth: undefined, marker: undefined, gap: false, under: 0 },
    { kind: "item", text: "nested", level: undefined, depth: 1, marker: "•", gap: false, under: undefined },
    { kind: "item", text: "numbered", level: undefined, depth: 0, marker: "1.", gap: false, under: undefined },
    { kind: "text", text: "Plain", level: undefined, depth: undefined, marker: undefined, gap: true, under: undefined },
    { kind: "text", text: "Next line", level: undefined, depth: undefined, marker: undefined, gap: false, under: undefined },
    { kind: "heading", text: "Small", level: 3, depth: undefined, marker: undefined, gap: true, under: undefined },
  ]);
});

test("the shipped notes read in English and in Chinese, without a mark of Markdown left", () => {
  const english = notesFor(shipped, "en-US"), chinese = notesFor(shipped, "zh-CN");
  assert.equal(english[0].text, "What's New");
  assert.equal(scriptOf(chinese.map((b) => b.text).join("")), "zh-CN");
  assert.deepEqual(notesFor(shipped, "zh-TW"), chinese); assert.deepEqual(notesFor(shipped, "ja-JP"), english);
  for (const list of [english, chinese]) {
    assert.ok(list.filter((b) => b.kind === "heading").length >= 3 && list.filter((b) => b.kind === "item").length >= 8);
    for (const b of list) assert.doesNotMatch(b.text, /^#|\*\*|`|<!--|^-{3}|\]\(/);
    assert.equal(list.some((b) => /[A-Za-z]{4,} [A-Za-z]{4,}/.test(b.text) && scriptOf(b.text) === "zh-CN" && list === english), false);
  }
  assert.equal(english.some((b) => scriptOf(b.text) !== "en"), false, "no Chinese in the English section");
});
