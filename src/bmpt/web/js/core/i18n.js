// UI strings come from the locale files (webui.* keys, via i18n.strings); names come with the data per language.

let strings = {};
let lang = "zh-CN";

// the app's language -> the key used in name data ({"zh-CN", "zh-TW", "en", "ja", "ko", "th"})
export const NAME_LANG = {
  "zh-CN": "zh-CN",
  "zh-TW": "zh-TW",
  "en-US": "en",
  "ja-JP": "ja",
  "ko-KR": "ko",
  "th-TH": "th",
};

/**
 * Set the UI language and its string table.
 * @param {string} appLang The language tag.
 * @param {Object<string, string>} table The strings by key.
 */
export function setStrings(appLang, table) {
  lang = appLang || "zh-CN";
  strings = table || {};
  document.documentElement.lang = lang;
}

/** The UI language tag. */
export const language = () => lang;

/**
 * Make the function that changes the language: locale tables are small and immutable for this page, concurrent
 * requests share a read, and a late reply cannot replace a newer choice or restart the media pipeline.
 * @param {object} options
 * @param {(lang: string) => Promise<object>} options.load Reads a locale's strings.
 * @param {(lang: string) => void} options.refresh Redraws the page in the language.
 */
export function languageRefresh({ load, refresh }) {
  const tables = new Map();
  let revision = 0;
  return async (next) => {
    const my = ++revision;
    if (!tables.has(next))
      tables.set(
        next,
        Promise.resolve()
          .then(() => load(next))
          .catch((error) => {
            tables.delete(next);
            throw error;
          }),
      );
    const table = await tables.get(next);
    if (my !== revision) return false;
    setStrings(next, table);
    await refresh(next);
    return my === revision;
  };
}

/** Translate a key, filling {name} placeholders from vars (a missing one becomes empty). */
export function T(key, vars = {}) {
  const s = strings[`webui.${key}`] ?? key;
  return s.replace(/\{(\w+)\}/g, (_, k) => vars[k] ?? "");
}

/**
 * An error's text: its code's string with its params, error.internal without a code. error.internal shows what
 * the error says of itself (its params' error, else its message); with nothing to say it ends where that detail
 * would start, so no raw key (error.undefined) and no empty detail is shown.
 * @param {typeof T} [t] A caller's own translate function (quit.js).
 */
export function errorText(err, t = T) {
  const code = err?.code || "internal",
    params = { ...(err?.params || {}) };
  if (code !== "internal" || params.error) return t(`error.${code}`, params);
  const said = typeof err === "string" ? err : err?.message && err.message !== err.code ? String(err) : "";
  return said
    ? t("error.internal", { ...params, error: said })
    : t("error.internal", params).replace(/[\s:\uFF1A]+$/u, "");
}

/**
 * The name keys to try for a UI language: its own, then zh-CN for Simplified Chinese and en for the others
 * (Traditional Chinese included: a Simplified name in a Traditional screen reads as wrong, like the UI strings,
 * i18n.fallback_language), then whatever there is.
 */
export function nameKeys(appLang) {
  const own = NAME_LANG[appLang] || appLang;
  const simplified = /^zh(-|$)/i.test(appLang) && !/^zh-(hant|tw|hk|mo)\b/i.test(appLang);
  return [...new Set([own, simplified ? "zh-CN" : "en", "en", "zh-CN"])];
}

/** The value of a {lang: value} object for the UI language (see nameKeys). */
export function L(value) {
  if (!value || typeof value !== "object") return value;
  for (const key of nameKeys(lang)) if (value[key] != null) return value[key];
  return Object.values(value)[0];
}

// the platform decides the file manager's name
export const platform = /Mac/i.test(navigator.platform) ? "mac" : "win";
