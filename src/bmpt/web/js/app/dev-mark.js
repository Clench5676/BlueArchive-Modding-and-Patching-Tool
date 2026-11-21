// The mark of a dev run: the server puts a DEV badge (.dev-mark) into the page only in dev mode, and the titles the
// page sets keep the "[DEV] " the server put before the first one. Fixed and untranslated, like the native window's.
export const DEV_PREFIX = "[DEV] ";

// whether the page was served by a dev run
export function isDevPage(doc = globalThis.document) {
  return !!doc?.querySelector?.(".dev-mark");
}

// a title as the page shows it: the prefix before it on a dev run's page, a single one
export function pageTitle(text, dev = isDevPage()) {
  return dev && !text.startsWith(DEV_PREFIX) ? DEV_PREFIX + text : text;
}
