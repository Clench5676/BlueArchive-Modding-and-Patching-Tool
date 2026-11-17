// Source text for tests that assert how a module is written: the layout the formatter chose is irrelevant, so line
// breaks and indentation collapse to single spaces, and a wrapped list closes up on its contents without the trailing
// comma the formatter adds.
export const flat = (text) =>
  text
    .replace(/\s+/g, " ")
    .replace(/,\s*\}/g, " }")
    .replace(/,\s*([)\]])/g, "$1")
    .replace(/([([])\s+/g, "$1")
    .replace(/\s+([)\]])/g, "$1");

// The end of the statement that starts at `at`: the first ";" outside brackets, strings, template literals, comments
// and regular expressions (a "/" after an operator or an opening bracket starts one), or the end of the text.
export function statementEnd(text, at) {
  let depth = 0;
  let last = "";
  for (let i = at; i < text.length; i++) {
    const c = text[i];
    if (c === '"' || c === "'" || c === "`") {
      i = stringEnd(text, i);
      last = c;
      continue;
    }
    if (c === "/" && text[i + 1] === "/") {
      i = text.indexOf("\n", i);
      if (i < 0) return text.length;
      continue;
    }
    if (c === "/" && text[i + 1] === "*") {
      i = text.indexOf("*/", i) + 1;
      continue;
    }
    if (c === "/" && (last === "" || "(,=:[!&|?{};+-*%<>~^".includes(last))) {
      i = regexEnd(text, i);
      last = "/";
      continue;
    }
    if ("([{".includes(c)) depth++;
    else if (")]}".includes(c)) depth--;
    else if (c === ";" && depth === 0) return i + 1;
    if (!/\s/.test(c)) last = c;
  }
  return text.length;
}

function stringEnd(text, start) {
  const quote = text[start];
  for (let i = start + 1; i < text.length; i++) {
    if (text[i] === "\\") i++;
    else if (text[i] === quote) return i;
    else if (quote === "`" && text[i] === "$" && text[i + 1] === "{") {
      let depth = 1;
      for (i += 2; i < text.length && depth > 0; i++) {
        if (text[i] === "{") depth++;
        else if (text[i] === "}") depth--;
        else if (text[i] === '"' || text[i] === "'" || text[i] === "`") i = stringEnd(text, i);
      }
      i--;
    }
  }
  return text.length;
}

function regexEnd(text, start) {
  let inClass = false;
  for (let i = start + 1; i < text.length; i++) {
    const c = text[i];
    if (c === "\\") i++;
    else if (c === "[") inClass = true;
    else if (c === "]") inClass = false;
    else if (c === "/" && !inClass) {
      while (/[a-z]/i.test(text[i + 1] ?? "")) i++;
      return i;
    }
  }
  return text.length;
}

// The whole statement that starts with `prefix` (its first occurrence).
export function statementOf(text, prefix) {
  const at = text.indexOf(prefix);
  if (at < 0) throw new Error(`not found: ${prefix}`);
  return text.slice(at, statementEnd(text, at));
}

// Source text with no layout at all, for assertions written against densely written code: whitespace only where it
// separates two words, double quotes, no trailing commas and no parentheses around a lone arrow parameter.
export const compact = (text) =>
  text
    .replace(/'/g, '"')
    .replace(/\s+/g, " ")
    .replace(/ ?([^\w$ ]) ?/g, "$1")
    .replace(/,([)\]}])/g, "$1")
    .replace(/(^|[^\w$.])\(([\w$]+)\)=>/g, "$1$2=>");
