// NGUI text: measuring text in the page font, breaking it into lines by the CJK and Thai rules, and fitting labels
// and groups of labels into their boxes; also the ink band titles are centred on.

// measured for the page font (Resource Han Rounded): ascent 1.16, descent 0.29, CJK ink 0.84 above to 0.09 below the
// baseline, so in a 1.15 line box the ink centre is 0.06 em low
export const GLYPH_LIFT = 0.06;
// Latin glyphs of this font are small next to its CJK glyphs and read low (lowercase mass sits on the baseline):
// text without CJK or Thai is drawn 12 % larger and lifted a little more. Measuring goes through textWidth, so
// layout agrees.
export const LATIN_SCALE = 1.12,
  LATIN_LIFT = 0.085;
// CJK here: Han, kana, CJK punctuation and full-width forms, and Hangul (syllables, jamo, compatibility jamo).
// Thai uses system fonts too, so the RHR Latin size and baseline adjustment do not apply.
export const isLatin = (str) =>
  !/[\u0e00-\u0e7f\u2e80-\u9fff\u3000-\u303f\uff00-\uffef\u3040-\u30ff\uac00-\ud7af\u1100-\u11ff\u3130-\u318f]/.test(
    str,
  );
/**
 * The font size to draw a text at: Latin-only text is drawn larger than the nominal size, so it matches CJK
 * text.
 * @returns {number} The size to draw with.
 */
export const fontSizeFor = (str, fs) => fs * (isLatin(str) ? LATIN_SCALE : 1);
export const measureCtx = document.createElement("canvas").getContext("2d");
// the font stack text in a language draws with (app.css --font): the UI language's (the html lang), or another one's
// for text tagged with its own lang (fitText's lang); read from a probe element once per language
const stacks = new Map();
let measureLang = null; // the language fitText measures in, when not the page's
const textLang = () => measureLang || document.documentElement.lang || "";
export function fontStack() {
  const lang = textLang();
  if (stacks.has(lang)) return stacks.get(lang);
  let stack = "";
  try {
    const probe = document.createElement("span");
    probe.lang = lang;
    document.documentElement.appendChild(probe);
    stack = getComputedStyle(probe).getPropertyValue("--font").trim();
    probe.remove();
  } catch {
    /* no page to read it from */
  }
  if (stack) stacks.set(lang, stack); // not before the style sheet is in
  return stack || "RHR, sans-serif";
}
/**
 * Measure the widest line of a text in the page font.
 * @param {string} str The text; lines are split at newlines.
 * @param {number} fs The nominal font size, as given to fontSizeFor.
 * @returns {number} The width of the widest line in pixels.
 */
export function textWidth(str, fs) {
  measureCtx.font = `500 ${fontSizeFor(str, fs)}px ${fontStack()}`;
  return Math.max(
    ...String(str)
      .split("\n")
      .map((l) => measureCtx.measureText(l).width),
  );
}

/**
 * The ink width of the widest line of a text. A closing full-width bracket or corner bracket is mostly blank on
 * the right.
 * @returns {number} The width in pixels.
 */
export const glyphWidth = (str, fs) =>
  Math.max(
    ...String(str)
      .split("\n")
      .map((l) => textWidth(l, fs) - (/[\uff09\u300d]$/.test(l) ? fs * 0.45 : 0)),
  );
// Text breaks go at separators (the full-width comma and enumeration comma, ", ", " - ", spaces, after an
// asterisk, before brackets) first, then between CJK or Thai words; the CJK line-breaking rules hold (NO_START,
// NO_END).
const CJK = /[\u3040-\u30ff\u3400-\u9fff\uff00-\uffef]/;
const THAI = /[\u0e00-\u0e7f]/;
// never at the start of a line (UAX #14 CL, CP, EX, IS, NS, IN and the small kana of CJ, as CSS line-break: strict
// keeps them): closing brackets and quotes, commas, full stops, colons, exclamation and question marks, ellipses, the
// prolonged sound mark, wave dashes, iteration marks and small kana
const NO_START = new RegExp(
  "^[" +
    "\\u3001\\u3002\\uff0c\\uff0e\\uff1a\\uff1b\\uff01\\uff1f\\uff09\\uff3d\\uff5d\\uff60\\u3009\\u300b\\u300d\\u300f" +
    "\\u3011\\u3015\\u3017\\u3019\\u301b\\u301e\\u301f\\u2019\\u201d\\u2026\\u2025\\u30fc\\u301c\\uff5e\\u30fb\\u3005" +
    "\\u309d\\u309e\\u30fd\\u30fe\\u3041\\u3043\\u3045\\u3047\\u3049\\u3063\\u3083\\u3085\\u3087\\u308e\\u3095\\u3096" +
    "\\u30a1\\u30a3\\u30a5\\u30a7\\u30a9\\u30c3\\u30e3\\u30e5\\u30e7\\u30ee\\u30f5\\u30f6,.:;!?)\\]}%]",
);
// never at the end of a line (UAX #14 OP): opening brackets and quotes
const NO_END = /[\uff08\uff3b\uff5b\uff5f\u3008\u300a\u300c\u300e\u3010\u3014\u3016\u3018\u301a\u301d\u2018\u201c([{]$/;
function tokensOf(str, cjkBreaks) {
  // A Terror form joins the character and the form with an asterisk (Shiroko*Terror, full-width in CJK): a break
  // may follow it.
  let tokens = String(str)
    .split(/(?<=[\uff0c\u3001]|, | - |\s)|(?=[\uff08(])|(?<=\S[*\uff0a])(?=\S)/)
    .filter(Boolean);
  // CJK and Thai runs break between words (Intl.Segmenter), keeping Thai vowels and tone marks with their words.
  // Without a segmenter, Thai runs stay whole rather than splitting combining marks from their base letters.
  const seg =
    typeof Intl !== "undefined" && Intl.Segmenter
      ? new Intl.Segmenter(textLang() || "zh", { granularity: "word" })
      : null;
  // "chars": CJK may break between any two characters (the kinsoku rules below still hold), for a word wider than
  // its line
  const perChar = (tok) =>
    tok.match(/[\u3040-\u30ff\u3400-\u9fff\uff00-\uffef]|[^\u3040-\u30ff\u3400-\u9fff\uff00-\uffef]+/g);
  // the segmenter cuts a Latin run at its punctuation (".skel" into "." and "skel"): printable ASCII runs stay one word
  const ascii = /^[\x21-\x7e]+$/;
  const words = (tok) =>
    [...seg.segment(tok)]
      .map((x) => x.segment)
      .reduce((out, part) => {
        if (out.length && ascii.test(part) && ascii.test(out.at(-1))) out[out.length - 1] += part;
        else out.push(part);
        return out;
      }, []);
  if (cjkBreaks)
    tokens = tokens.flatMap((tok) =>
      !(CJK.test(tok) || THAI.test(tok))
        ? [tok]
        : cjkBreaks === "chars" && CJK.test(tok)
          ? perChar(tok)
          : seg
            ? words(tok)
            : perChar(tok),
    );
  // a token that may not start a line joins the one before it, one after an opening bracket joins that bracket
  const merged = [];
  for (const tok of tokens) {
    const last = merged.length - 1;
    if (last >= 0 && (NO_START.test(tok) || NO_END.test(merged[last]))) merged[last] += tok;
    else merged.push(tok);
  }
  return merged;
}
function breakLines(tokens, fs, maxW) {
  const lines = [];
  let cur = "";
  for (const tok of tokens) {
    const next = cur + tok;
    if (cur && glyphWidth(next.trimEnd(), fs) > maxW) {
      lines.push(cur.trimEnd());
      cur = tok.trimStart();
    } else cur = next;
  }
  if (cur) lines.push(cur.trimEnd());
  return lines;
}
/**
 * Fit text into a width: shrink it from fsMax to fsMin on one line; if it still does not fit, break it into up
 * to maxLines lines at the smallest size that fits. Wrapped text is never larger than fsMin.
 * @param {number} fsMin The smallest font size before wrapping.
 * @param {string|null} [lang] The text's language when it is not the page's; it is drawn in that language's
 *     fonts (app.css).
 * @returns {{text: string, fs: number, lines: number, overflow?: boolean}} The text with its line breaks, the
 *     size and the number of lines.
 */
export function fitText(str, fsMax, fsMin, maxW, maxLines = 2, lang = null) {
  const outer = measureLang;
  measureLang = lang || outer;
  try {
    return fitLines(String(str ?? ""), fsMax, fsMin, maxW, maxLines);
  } finally {
    measureLang = outer;
  }
}
// the narrowest width that still gives the same number of lines (no lone character on the last line)
function balanced(tokens, fs, maxW, lines) {
  let lo = 0,
    hi = maxW;
  while (hi - lo > 2) {
    const mid = (lo + hi) / 2,
      tryLines = breakLines(tokens, fs, mid);
    if (tryLines.length === lines.length && tryLines.every((l) => glyphWidth(l, fs) <= mid)) hi = mid;
    else lo = mid;
  }
  return breakLines(tokens, fs, hi);
}
function fitLines(str, fsMax, fsMin, maxW, maxLines) {
  const fit = fitWrapped(str, fsMax, fsMin, maxW, maxLines);
  // Text with no break to wrap at (one long word) keeps the largest size under fsMin at which it fits on one line;
  // a wrap that wins a larger size is still used.
  if (maxLines > 1 && fit.fs < fsMin) {
    for (let fs = fsMin - 2; fs > fit.fs && fs >= fsMin - 8; fs -= 2)
      if (glyphWidth(str, fs) <= maxW) return { text: str, fs, lines: 1 };
  }
  return fit;
}
function fitWrapped(str, fsMax, fsMin, maxW, maxLines) {
  for (let fs = fsMax; fs >= fsMin; fs -= 2) if (glyphWidth(str, fs) <= maxW) return { text: str, fs, lines: 1 };
  // A single-line cell keeps its size floor; the caller clips or ellipsizes any remaining overflow.
  if (maxLines === 1) return { text: str, fs: fsMin, lines: 1 };
  for (const cjkBreaks of [false, true]) {
    const tokens = tokensOf(str, cjkBreaks);
    for (let fs = fsMin; fs >= fsMin - 8; fs -= 2) {
      const lines = breakLines(tokens, fs, maxW);
      if (lines.length > 1 && lines.length <= maxLines && lines.every((l) => glyphWidth(l, fs) <= maxW)) {
        const split = balanced(tokens, fs, maxW, lines);
        return { text: split.join("\n"), fs, lines: split.length };
      }
    }
  }
  // Nothing fits above fsMin - 8 (one long word): that size and smaller, down to 40 % of fsMin, so the text stays
  // in its box.
  for (let fs = fsMin - 8; fs >= sizeFloor(fsMin); fs -= 2) {
    const lines = wrapAt(str, fs, maxW, maxLines);
    if (lines) return { text: lines.join("\n"), fs, lines: lines.length };
  }
  return { text: str, fs: fsMin - 8, lines: 1 };
}

// the lines str takes at size fs within maxW (one when it fits), at most maxLines; null when it does not fit
// charBreaks: also try breaks between any two CJK characters, after words and separators have failed (fitGroup sets it)
let charBreaks = false;
function wrapAt(str, fs, maxW, maxLines) {
  if (glyphWidth(str, fs) <= maxW) return [str];
  if (maxLines < 2) return null;
  for (const cjkBreaks of charBreaks ? [false, true, "chars"] : [false, true]) {
    const tokens = tokensOf(str, cjkBreaks);
    const lines = breakLines(tokens, fs, maxW);
    if (lines.length > 1 && lines.length <= maxLines && lines.every((l) => glyphWidth(l, fs) <= maxW))
      return balanced(tokens, fs, maxW, lines);
  }
  return null;
}

// NGUI's line pitch (render() lineHeight 1.15) of text drawn at fs
export const LINE_PITCH = 1.15;
/**
 * Draw a caption at the size already chosen for its sibling group, trying every permitted word and CJK break at
 * that size. fitText can otherwise choose a smaller size before trying Thai word breaks, undoing the group's
 * shared size.
 * @param {string|null} [lang] The text's language when it is not the page's.
 * @returns {{text: string, fs: number, lines: number, overflow: boolean}} The wrapped text; overflow is true
 *     when it does not fit.
 */
export function wrapText(str, fs, maxW, maxLines = 2, lang = null) {
  const outer = measureLang,
    previous = charBreaks;
  measureLang = lang || outer;
  charBreaks = true;
  try {
    const rows = wrapAt(String(str), fs, maxW, maxLines);
    return { text: rows ? rows.join("\n") : String(str), fs, lines: rows?.length || 1, overflow: !rows };
  } finally {
    measureLang = outer;
    charBreaks = previous;
  }
}
// the smallest size text is shrunk to so that it stays in its box, 40 % of the size range's floor
const sizeFloor = (fsMin) => Math.max(8, Math.floor(fsMin * 0.4));
/**
 * Fit the labels of widgets of one kind side by side (an action row, tabs, segments, the buttons of a popup) to
 * one shared size.
 * The size is the largest from fsMax down at which every label fits its box after wrapping onto at most
 * maxLines lines (breaks as in fitText: words in Latin scripts, words in Thai, CJK rules) and, with maxH,
 * within that height. A wrap that wins only a step or two of size over every label on one line is not used. A
 * size under fsMin is used only where some label cannot fit at fsMin, in steps of 2 down to 40 % of it: text
 * never runs past its box.
 * @param {string[]} strs
 * @param {number|number[]} maxW The width of the box, or one per label.
 * @param {number} [maxLines=2] The most lines per label.
 * @param {{lang?: string|string[]|null, maxH?: number}} [options] One language for every label or a list with
 *     each label's own (null: the page's), and the height of the box.
 * @returns {{text: string, fs: number, lines: number, overflow?: boolean}[]} One result per label, in order,
 *     with the same fs; overflow is true on each when not even the smallest size fits (the caller shortens the
 *     text or enlarges the box).
 */
export function fitGroup(strs, fsMax, fsMin, maxW, maxLines = 2, { lang = null, maxH = Infinity } = {}) {
  const outer = measureLang;
  // lang: one language for every label, or a list with each label's own (null: the page's), as the Language row has
  const perLabel = Array.isArray(lang);
  measureLang = perLabel ? outer : lang || outer;
  try {
    const labels = strs.map((s) => String(s ?? "")),
      widths = labels.map((_, i) => (Array.isArray(maxW) ? maxW[i] : maxW));
    const floor = sizeFloor(fsMin);
    const wrap = (i, fs, lines) => {
      if (!perLabel) return wrapAt(labels[i], fs, widths[i], lines);
      measureLang = lang[i] || outer;
      try {
        return wrapAt(labels[i], fs, widths[i], lines);
      } finally {
        measureLang = outer;
      }
    };
    const solve = (lines) => {
      for (let fs = fsMax; fs >= floor; fs -= 2) {
        const rows = labels.map((_, i) => wrap(i, fs, lines));
        if (rows.every((r, i) => r && r.length * fontSizeFor(labels[i], fs) * LINE_PITCH <= maxH)) {
          return rows.map((r) => ({ text: r.join("\n"), fs, lines: r.length }));
        }
      }
      return null;
    };
    if (!labels.length) return [];
    let wrapped = solve(maxLines);
    // A CJK word wider than its line (the katakana of "Explorer") keeps the labels under fsMin: break inside it
    // instead, as the guidelines allow for CJK, when that keeps the size within the range.
    if (maxLines > 1 && (!wrapped || wrapped[0].fs < fsMin)) {
      charBreaks = true;
      try {
        const chars = solve(maxLines);
        if (chars && (!wrapped || chars[0].fs > wrapped[0].fs)) wrapped = chars;
      } finally {
        charBreaks = false;
      }
    }
    if (wrapped) {
      const single = maxLines > 1 && wrapped.some((f) => f.lines > 1) ? solve(1) : null;
      return single && single[0].fs >= wrapped[0].fs - 4 && single[0].fs >= wrapped[0].fs * 0.92 ? single : wrapped;
    }
    return labels.map((s, i) => {
      const lines = wrap(i, floor, maxLines) || [s];
      return { text: lines.join("\n"), fs: floor, lines: lines.length, overflow: true };
    });
  } finally {
    measureLang = outer;
  }
}

// the ways str can be set at fs on one line or, with maxLines 2, on the two lines that make it narrowest, within
// maxH, each { lines, width }, narrowest first
function layoutOptions(str, fs, maxLines, maxH) {
  const options = [{ lines: [str], width: glyphWidth(str, fs) }];
  if (maxLines > 1) {
    let best = null;
    for (const cjkBreaks of [false, true]) {
      const tokens = tokensOf(str, cjkBreaks);
      for (let k = 1; k < tokens.length; k++) {
        const a = tokens.slice(0, k).join("").trimEnd(),
          b = tokens.slice(k).join("").trimStart();
        const width = Math.max(glyphWidth(a, fs), glyphWidth(b, fs));
        if (a && b && (!best || width < best.width - 0.01)) best = { lines: [a, b], width };
      }
    }
    if (best && best.width < options[0].width) options.push(best);
  }
  return options
    .filter((o) => o.lines.length * fontSizeFor(str, fs) * LINE_PITCH <= maxH)
    .sort((x, y) => x.width - y.width);
}

/**
 * Fit the captions of a row of buttons that share one width.
 * All captions get one size, the largest at which each fits after wrapping onto at most two lines, with `inset`
 * of clear space across each body (both sides together). Bodies are equal, except where equal bodies would
 * force the size below fsMin, the bottom of the prefab's range: then a body is at least as wide as its caption
 * needs and the rest are as equal as the width allows. A long word takes width from its neighbours, who keep
 * the same size, so one word costs the row a little evenness instead of the size of every caption.
 * @param {string[]} strs
 * @param {number} fsMin The bottom of the prefab's size range.
 * @param {number} total The width of the bodies together, without the gaps between them.
 * @param {number} inset The clear space across each body, both sides together.
 * @param {number} [maxLines=2] The most lines per caption.
 * @param {{lang?: string|string[]|null, maxH?: number}} [options] The language or languages, and the height of
 *     the box.
 * @returns {{text: string, fs: number, lines: number, width: number}[]} One result per caption: the wrapped
 *     text, the shared size, the line count and the body width.
 */
export function fitRow(strs, fsMax, fsMin, total, inset, maxLines = 2, { lang = null, maxH = Infinity } = {}) {
  const outer = measureLang;
  measureLang = lang || outer;
  try {
    const labels = strs.map((s) => String(s ?? "")),
      n = labels.length,
      share = n ? total / n : 0;
    const equal = fitGroup(labels, fsMax, fsMin, share - inset, maxLines, { lang, maxH });
    const even = equal.map((f) => ({ ...f, width: share }));
    if (n < 2) return even;
    if (equal[0].fs >= fsMin) return even;
    for (let fs = fsMax; fs >= equal[0].fs + 2; fs -= 2) {
      const options = labels.map((s) => layoutOptions(s, fs, maxLines, maxH));
      if (options.some((o) => !o.length)) continue;
      const need = options.map((o) => o[0].width + inset);
      if (need.reduce((a, b) => a + b, 0) > total) continue;
      // the level every body reaches that needs less: what is left after the wide ones, shared by the rest
      const order = need.map((_, i) => i).sort((x, y) => need[y] - need[x]);
      let rest = total,
        level = rest / n;
      for (let k = 0, left = n; k < n; k++, left--) {
        level = rest / left;
        if (need[order[k]] <= level) break;
        rest -= need[order[k]];
      }
      return labels.map((s, i) => {
        const width = Math.max(need[i], level),
          lines = wrapAt(s, fs, width - inset, maxLines) || options[i][0].lines;
        return { text: lines.join("\n"), fs, lines: lines.length, width };
      });
    }
    return even;
  } finally {
    measureLang = outer;
  }
}

/**
 * A glyph that stands for the vertical band Latin and Thai text occupies, so a title does not move with the
 * descenders and ascenders of its own letters: capital H for Latin, a consonant for Thai.
 * @returns {string|null} The glyph, or null for Han, kana and Hangul, which fill the same band whatever the
 *     characters are, so their own ink counts.
 */
export function inkReference(str) {
  const text = String(str);
  if (/[\u2e80-\u9fff\uff00-\uffef\uac00-\ud7af\u1100-\u11ff\u3130-\u318f]/.test(text)) return null;
  return /[\u0e00-\u0e7f]/.test(text) ? "\u0e01" : "H";
}
/**
 * How far text drawn in a label box has to move down for the centre of its ink to sit on the box's centre.
 * The box centres its lines (NGUI's 1.15 pitch) by font metrics, while the eye centres the ink of the first
 * line's top and the last line's bottom. It is measured with the font stack the text draws with, so it works on
 * a tree that is not in the document yet (computed style is empty there).
 * @param {number} px The font size in CSS pixels.
 * @param {{reference?: boolean}} [options] reference: measure the script's reference glyph (inkReference)
 *     instead of these letters, giving one baseline for every title of a language.
 * @returns {number} The shift in CSS pixels, down.
 */
export function inkShift(str, px, { reference = false } = {}) {
  const lines = String(str).split("\n");
  measureCtx.font = `500 ${px}px ${fontStack()}`;
  const glyph = reference ? inkReference(str) : null;
  const first = measureCtx.measureText(glyph ?? lines[0]),
    last = measureCtx.measureText(glyph ?? lines.at(-1));
  const [ascent, descent, fontAscent, fontDescent] = [
    first.actualBoundingBoxAscent,
    last.actualBoundingBoxDescent,
    first.fontBoundingBoxAscent,
    first.fontBoundingBoxDescent,
  ];
  return [ascent, descent, fontAscent, fontDescent].every(Number.isFinite)
    ? (ascent - descent - fontAscent + fontDescent) / 2
    : 0;
}

/**
 * The ink of text drawn in a label box: its top and bottom as offsets from the box's centre.
 * It is the extent the eye sees (first line's ascent to last line's descent, lines at NGUI's 1.15 pitch,
 * centred as inkShift describes); without font metrics, the em box of the lines.
 * @param {number} px The font size in CSS pixels.
 * @returns {{top: number, bottom: number}} The offsets in CSS pixels, down from the centre.
 */
export function inkBand(str, px) {
  const lines = String(str).split("\n");
  measureCtx.font = `500 ${px}px ${fontStack()}`;
  const ascent = measureCtx.measureText(lines[0]).actualBoundingBoxAscent;
  const descent = measureCtx.measureText(lines.at(-1)).actualBoundingBoxDescent;
  const span = (lines.length - 1) * px * 1.15;
  if (![ascent, descent].every(Number.isFinite)) return { top: -(px + span) / 2, bottom: (px + span) / 2 };
  const centre = -inkShift(str, px),
    half = (ascent + descent + span) / 2;
  return { top: centre - half, bottom: centre + half };
}

/**
 * Fit the label widgets of one group with fitGroup: each one's text and size (the same for all), and its box at
 * least maxW wide.
 * @param {object[]} widgets The label widgets; each is updated in place.
 * @param {string[]} strs The texts, one per widget.
 * @param {number} [maxLines=2] The most lines per label.
 * @param {{maxH?: number}} [opts]
 * @returns {{text: string, fs: number, lines: number}[]} The fit of each widget, as fitGroup gives it.
 */
export function fitLabels(widgets, strs, fsMax, fsMin, maxW, maxLines = 2, opts = {}) {
  // each widget's own language, as the Language row's options have
  const fits = fitGroup(strs, fsMax, fsMin, maxW, maxLines, {
    lang: widgets.some((w) => w.lang) ? widgets.map((w) => w.lang) : null,
    ...opts,
  });
  widgets.forEach((widget, i) =>
    Object.assign(widget, { text: fits[i].text, fontSize: fits[i].fs, w_: Math.max(widget.w_ || 0, maxW) }),
  );
  return fits;
}

/**
 * Fit text into a label widget: its text and size, and its box at least maxW wide, so text fitted to maxW never
 * spills out of a narrower box (a prefab label is often a little narrower than the button drawn round it).
 * @param {object} widget The label widget; updated in place.
 * @param {number} fsMin The smallest size before wrapping.
 * @returns {{text: string, fs: number, lines: number}} The fit, as fitText gives it.
 */
export function fitLabel(widget, str, fsMax, fsMin, maxW, maxLines = 2) {
  const f = fitText(str, fsMax, fsMin, maxW, maxLines, widget.lang);
  Object.assign(widget, { text: f.text, fontSize: f.fs, w_: Math.max(widget.w_ || 0, maxW) });
  return f;
}
