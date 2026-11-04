// Release notes as the update popup shows them (update.js): the release's Markdown, read in the UI language.
// A release body holds one section per language, separated by a rule (---) and marked with <!-- lang: xx -->, which
// GitHub does not show; an unmarked section is told by its script. The section in the UI language is shown, else the
// same language's other variant (zh-TW reads zh-CN), else English, else the first. Markdown is drawn rather than shown:
// headings larger, list items with a bullet and a hanging indent; emphasis, code and link marks go, their text stays.
// Pure functions: no DOM, no imports.

const MARK = /^<!--\s*lang:\s*([A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*)\s*-->$/;
const RULE = /^ {0,3}([-*_])(?:[ \t]*\1){2,}[ \t]*$/;

// the language a section is written in, by its script: kana, hangul, Thai, Han, else Latin (English)
export function scriptOf(text) {
  const count = (re) => (String(text).match(re) || []).length;
  if (count(/[\u3040-\u30ff]/g) >= 3) return "ja";
  if (count(/[\uac00-\ud7af]/g) >= 3) return "ko";
  if (count(/[\u0e00-\u0e7f]/g) >= 3) return "th";
  if (count(/[\u4e00-\u9fff]/g) >= 3) return "zh-CN";
  return "en";
}

// [{lang, lines}] in the body's order; sections with nothing but blank lines are dropped
export function sections(markdown) {
  const out = [];
  let current = { lang: null, lines: [] };
  const close = () => {
    if (current.lines.some((line) => line.trim())) out.push(current);
    current = { lang: null, lines: [] };
  };
  for (const raw of String(markdown ?? "").split(/\r?\n/)) {
    const line = raw.replace(/\s+$/, "");
    const mark = MARK.exec(line.trim());
    if (RULE.test(line)) { close(); continue; }
    if (mark) {
      if (current.lines.some((l) => l.trim())) close();
      current.lang = mark[1];
      continue;
    }
    current.lines.push(line);
  }
  close();
  return out.map((s) => ({ lang: s.lang || scriptOf(s.lines.join("\n")), lines: s.lines }));
}

const base = (lang) => String(lang || "").toLowerCase().split("-")[0];
export function pick(list, lang) {
  if (!list.length) return null;
  const wanted = String(lang || "").toLowerCase();
  return list.find((s) => s.lang.toLowerCase() === wanted) || list.find((s) => base(s.lang) === base(lang))
    || list.find((s) => base(s.lang) === "en") || list[0];
}

// the text of one line, without Markdown's marks: images and links keep their text, emphasis, code and strike marks go,
// inline HTML goes, escapes keep their character
export function inline(text) {
  // escaped characters are set aside first, so an escaped mark never opens or closes an emphasis
  const kept = [];
  return String(text)
    .replace(/\\([\\`*_{}[\]()#+\-.!>~|])/g, (_, c) => `\u0000${kept.push(c) - 1}\u0000`)
    .replace(/<!--.*?-->/g, "")
    .replace(/!\[([^\]]*)\]\([^)]*\)/g, "$1")
    .replace(/\[([^\]]+)\]\([^)]*\)/g, "$1")
    .replace(/<(https?:\/\/[^>\s]+)>/g, "$1")
    .replace(/<\/?[A-Za-z][^>]*>/g, "")
    .replace(/`([^`]+)`/g, "$1")
    .replace(/(\*\*|__)(?=\S)(.+?)(?<=\S)\1/g, "$2")
    .replace(/~~(?=\S)(.+?)(?<=\S)~~/g, "$1")
    .replace(/(^|[^\w*])\*(?=\S)(.+?)(?<=\S)\*(?!\w)/g, "$1$2")
    .replace(/(^|[^\w])_(?=\S)(.+?)(?<=\S)_(?!\w)/g, "$1$2")
    .replace(/\u0000(\d+)\u0000/g, (_, i) => kept[Number(i)])
    .trim();
}

// [{kind: "heading" | "item" | "text", text, level?, depth?, marker?, gap}]: gap marks a blank line before. Every line stays a
// line, as GitHub shows a release's text; an indented line under a list item continues it at its indent (depth + 1, no
// bullet).
export function blocks(lines) {
  const out = [];
  let gap = false;
  for (const raw of lines) {
    const line = raw.replace(/\t/g, "    ");
    if (!line.trim()) { gap = out.length > 0; continue; }
    let m = /^ {0,3}(#{1,6})\s+(.*?)(?:\s+#+)?\s*$/.exec(line);
    if (m) { out.push({ kind: "heading", level: m[1].length, text: inline(m[2]), gap: out.length > 0 }); gap = false; continue; }
    m = /^(\s*)([-*+]|\d{1,3}[.)])\s+(.*)$/.exec(line);
    if (m) {
      const ordered = /\d/.test(m[2]);
      out.push({ kind: "item", depth: Math.min(3, Math.floor(m[1].length / 2)), marker: ordered ? `${parseInt(m[2], 10)}.` : "\u2022",
        text: inline(m[3]), gap });
      gap = false;
      continue;
    }
    const previous = out.at(-1), text = inline(line.trim());
    if (!text) continue;
    const under = !gap && /^\s{2,}/.test(line) && (previous?.kind === "item" || previous?.under != null);
    out.push(under ? { kind: "text", text, gap: false, under: previous.under ?? previous.depth } : { kind: "text", text, gap });
    gap = false;
  }
  return out.filter((b) => b.text);
}

// the blocks for the UI language, or [] for empty notes
export const notesFor = (markdown, lang) => {
  const section = pick(sections(markdown), lang);
  return section ? blocks(section.lines) : [];
};
