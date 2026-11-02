// A stand-in canvas and page for the fit tests (the layout and fit logic of web/js/ngui.js against the locale files' own
// strings): proportional glyph advances instead of a flat half em, so a wrapped label measures about as it does in the page
// font. What the tests assert is the logic, not a font: CJK, kana, Hangul and full-width forms are 1 em wide, a Thai base
// letter half an em and its vowel and tone marks none, Latin by class. ngui.js draws Latin 12 % larger (fontSizeFor), which
// reaches this measure as a larger px in the font string.
import { readFileSync } from "node:fs";

export const LOCALES = ["en-US", "zh-CN", "zh-TW", "ja-JP", "ko-KR", "th-TH"];
const WIDE = /[⺀-鿿　-ヿ＀-￯가-힯ᄀ-ᇿ㄰-㆏]/;
const THAI_MARK = /[ัิ-ฺ็-๎]/;
const THAI = /[฀-๿]/;

export function advance(char) {
  if (WIDE.test(char)) return 1;
  if (THAI_MARK.test(char)) return 0;
  if (THAI.test(char)) return 0.5;
  if (char === " ") return 0.3;
  if ("iljtfI.,:;'!|".includes(char)) return 0.33;
  if ("()[]*-/".includes(char)) return 0.4;
  if ("mwMW@".includes(char)) return 0.92;
  if (/[0-9]/.test(char)) return 0.58;
  if (/[A-Z]/.test(char)) return 0.68;
  return 0.56;
}

export const context = {
  font: "",
  measureText(text) {
    const px = parseFloat(/([\d.]+)px/.exec(this.font)[1]);
    return { width: [...String(text)].reduce((sum, char) => sum + advance(char) * px, 0),
      // ink above and below the baseline, and the font's box (inkShift, inkBand)
      actualBoundingBoxAscent: 0.72 * px, actualBoundingBoxDescent: /[gjpqy]/.test(text) ? 0.2 * px : 0,
      fontBoundingBoxAscent: 1.16 * px, fontBoundingBoxDescent: 0.29 * px };
  },
};

// Call before ngui.js is imported: it reads the canvas when it loads.
export function installFitPage() {
  const root = { lang: "en-US", appendChild() {} };
  globalThis.document = { createElement: () => ({ getContext: () => context, remove() {} }), documentElement: root };
  globalThis.getComputedStyle = () => ({ getPropertyValue: () => "" });
  return root;
}

const read = (locale) => JSON.parse(readFileSync(new URL(`../../../src/ba_modding_toolkit/locales/${locale}.json`, import.meta.url), "utf8")).webui;
const cache = new Map();
// the locale's web UI strings, flat ("import.filterCount") or nested
export function webui(locale) {
  if (!cache.has(locale)) cache.set(locale, read(locale));
  return cache.get(locale);
}
export function text(locale, key, params = {}) {
  const value = key.split(".").reduce((o, part) => o?.[part], webui(locale));
  if (typeof value !== "string") throw new Error(`${locale}: no string ${key}`);
  return value.replace(/\{(\w+)\}/g, (_, name) => params[name] ?? "");
}
