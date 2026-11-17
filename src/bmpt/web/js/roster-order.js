// The student list's cards and their order for a view (merge, segment filters, the display settings' filters and
// sort, the search), shared by the roster and the detail screen's arrows. No DOM: the tests run it in Node.
// arrange gives the cards as the roster shows them, order the same cards in one list, neighbour the card before
// or after the shown variant (or family, merged), visit the recently viewed order after a visit.
import { studentName } from "./names.js";

export const SORTS = ["recent", "added", "name", "mods", "default"];
// the direction each sort reads in by default; the order button flips it
export const NATURAL = { recent: "desc", added: "desc", name: "asc", mods: "desc", default: "asc" };
// the display settings' filter groups and their options; inside a group options add up, groups narrow
export const FILTERS = {
  favorite: ["yes", "no"],
  portrait: ["official", "replaced"],
  lobby: ["yes", "no"],
  model: ["yes", "no"],
};
export const DEFAULT_VIEW = Object.freeze({
  merged: false,
  mods: "all",
  recruit: "all",
  sort: "recent",
  order: "desc",
  filters: {},
});

const pick = (value, allowed, fallback) => (allowed.includes(value) ? value : fallback);

/**
 * What the roster list shows and in what order.
 * @typedef {object} RosterView
 * @property {boolean} merged Group the variants of one character.
 * @property {"all"|"mods"|"none"} mods The mods segment filter.
 * @property {"all"|"yes"|"no"} recruit The recruitment segment filter.
 * @property {string} sort The sort key, one of SORTS.
 * @property {"asc"|"desc"} order The direction.
 * @property {Object<string, string[]>} filters The options that are on in each of the display settings' FILTERS groups.
 */

/**
 * A stored or edited view with every field valid (the server keeps the same rules: webui/state.py roster_view).
 * @param {*} v The stored or edited view; anything that is not an object gives the defaults.
 * @returns {RosterView}
 */
export function normalizeView(v) {
  const src = v && typeof v === "object" ? v : {};
  const filters = {};
  const given = src.filters && typeof src.filters === "object" ? src.filters : {};
  for (const [group, options] of Object.entries(FILTERS)) {
    const on = Array.isArray(given[group]) ? options.filter((o) => given[group].includes(o)) : [];
    if (on.length) filters[group] = on;
  }
  return {
    merged: src.merged === true,
    mods: pick(src.mods, ["all", "mods", "none"], "all"),
    recruit: pick(src.recruit, ["all", "yes", "no"], "all"),
    sort: pick(src.sort, SORTS, DEFAULT_VIEW.sort),
    order: pick(src.order, ["asc", "desc"], NATURAL[pick(src.sort, SORTS, DEFAULT_VIEW.sort)]),
    filters,
  };
}

/**
 * Whether the display settings' filters narrow the list: a group narrows only when some but not all of its
 * options are on.
 * @param {Object<string, string[]>|undefined} filters
 * @returns {boolean} True when some group narrows.
 */
export const filtering = (filters) =>
  Object.entries(FILTERS).some(([g, options]) => {
    const on = filters?.[g] || [];
    return on.length > 0 && on.length < options.length;
  });

// --- search: every name language, the aliases and the file id, case, width, kana and separators folded ---
/**
 * Fold a text for searching: case, width, kana and separators (brackets, dots, commas, dashes, quotes and the
 * marks inside official names such as Shiroko*Terror, Golconde&Decalcomania or a "?" title), so a name typed
 * without them is found.
 */
export function fold(str) {
  return String(str ?? "")
    .normalize("NFKC")
    .toLowerCase()
    .replace(/[\u3041-\u3096]/g, (c) => String.fromCharCode(c.charCodeAt(0) + 0x60)) // hiragana as katakana
    .replace(/[\s()[\]{}\u00b7\u30fb\u3001\uff0c,.\-_'"*&!?]/g, "");
}
/**
 * Split a search text into its folded words.
 * @returns {string[]} The words.
 */
export const terms = (query) =>
  String(query ?? "")
    .split(/\s+/)
    .map(fold)
    .filter(Boolean);

// what a variant is found by: its names, its family's base names, the aliases the UI never shows (the variant's and
// the family's, the CN server's wording) and the user's own aliases of the variant (v.custom changes after an edit)
const haystacks = new WeakMap();
function haystack(v, f) {
  let h = haystacks.get(v);
  if (h === undefined || h.custom !== v.custom || h.modNames !== v.modNames) {
    const parts = [
      v.key,
      v.fileId,
      ...(v.aliases || []),
      ...(f?.aliases || []),
      ...(v.custom || []),
      ...(v.modNames || []),
    ];
    for (const n of Object.values(v.names || {})) parts.push(n?.full);
    for (const n of Object.values(f?.base || {})) parts.push(n);
    h = {
      custom: v.custom,
      modNames: v.modNames,
      text: parts
        .filter((p) => typeof p === "string" && p)
        .map(fold)
        .join("\u0000"),
    };
    haystacks.set(v, h);
  }
  return h.text;
}
const matches = (v, f, words) => !words.length || words.every((w) => haystack(v, f).includes(w));

// --- filters on one card's attributes ---
function passes(card, filters) {
  for (const [group, options] of Object.entries(FILTERS)) {
    const on = filters[group] || [];
    if (!on.length || on.length === options.length) continue;
    const value = {
      favorite: card.favorite ? "yes" : "no",
      portrait: card.replaced ? "replaced" : "official",
      lobby: card.lobby ? "yes" : "no",
      model: card.model ? "yes" : "no",
    }[group];
    if (!on.includes(value)) return false;
  }
  return true;
}

const time = (iso) => {
  const t = Date.parse(iso || "");
  return Number.isFinite(t) ? t : -Infinity;
};

// cards: one per variant, or one per family when merged (its attributes are any of its variants'; the card shows the
// first variant, or the first one the search found)
function cards(data, view, words, label) {
  const list = [];
  let n = 0;
  for (const f of data.families || []) {
    const vs = f.variants || [];
    if (!vs.length) continue;
    if (view.merged) {
      const hit = words.length ? vs.find((v) => matches(v, f, words)) : vs[0];
      const recent = Math.min(...vs.map((v) => v.recent ?? Infinity));
      const card = {
        ...vs[0],
        ...(hit
          ? {
              key: hit.key,
              spr: hit.spr,
              portrait: hit.portrait,
              names: hit.names,
              configured: hit.configured,
              previewSources: hit.previewSources,
              selection: hit.selection,
              state: hit.state,
              spineCategory: hit.spineCategory,
            }
          : {}),
        name: studentName(
          hit || vs[0],
          words.length ? (label((hit || vs[0]).names)?.full ?? f.key) : (label(f.base) ?? f.key),
        ),
        family: f.key,
        n: n++,
        matched: !!hit,
        recruit: vs.some((v) => v.recruitable),
        mods: vs.reduce((s, v) => s + (v.mods || 0), 0),
        modCounts: Object.fromEntries(
          ["spr", "lobby"].map((kind) => [kind, vs.reduce((s, v) => s + (v.modCounts?.[kind] || 0), 0)]),
        ),
        favorite: vs.some((v) => v.favorite),
        fresh: vs.some((v) => v.fresh),
        replaced: vs.find((v) => v.replaced)?.replaced || null,
        lobby: hit?.spineCategory ? hit.lobby : vs.find((v) => v.lobby)?.lobby || null,
        model: vs.find((v) => v.model)?.model || null,
        storedKinds: [...new Set(vs.flatMap((v) => v.storedKinds || []))],
        localKinds: [...new Set(vs.flatMap((v) => v.localKinds || []))],
        recent,
        added: Math.max(...vs.map((v) => time(v.added))),
      };
      list.push(card);
    } else {
      for (const v of vs) {
        list.push({
          ...v,
          name: studentName(v, label(v.names)?.full ?? v.key),
          family: f.key,
          n: n++,
          matched: matches(v, f, words),
          recruit: !!v.recruitable,
          recent: v.recent ?? Infinity,
          added: time(v.added),
        });
      }
    }
  }
  return list;
}

function comparator(view, collator) {
  const by = {
    recent: (a, b) => b.recent - a.recent, // ascending: least recently viewed first
    added: (a, b) => a.added - b.added,
    name: (a, b) => (collator ? collator.compare(a.name, b.name) : a.name < b.name ? -1 : a.name > b.name ? 1 : 0),
    mods: (a, b) => (a.mods || 0) - (b.mods || 0),
    default: (a, b) => a.n - b.n,
  }[view.sort];
  const sign = view.order === "desc" ? -1 : 1;
  const safe = (x) => (Number.isNaN(x) ? 0 : x);
  // favourites (the game's pins) first whatever the sort; the catalog order breaks ties
  return (a, b) => b.favorite - a.favorite || sign * safe(by(a, b)) || a.n - b.n;
}

/**
 * Arrange the roster's cards for a view.
 * @param {{families?: object[]}} data The roster data of roster.list.
 * @param {object} [options]
 * @param {(names: object) => *} [options.label] Gives the UI language's value of a {lang: value} object.
 * @param {Intl.Collator|null} [options.collator]
 * @param {boolean} [options.unfiltered] True: the whole roster in the view's order, for stepping from a card
 *     the view hides.
 * @returns {{groups: {id: "mods"|"none", items: object[]}[], total: number}} The cards in their groups and the
 *     number of cards before filtering.
 */
export function arrange(
  data,
  view,
  { query = "", label = (v) => v?.["zh-CN"] ?? v?.en, collator = null, unfiltered = false } = {},
) {
  const words = unfiltered ? [] : terms(query);
  const all = cards(data, view, words, label);
  const shown = unfiltered
    ? all
    : all.filter(
        (c) =>
          c.matched &&
          (view.recruit === "all" || (view.recruit === "yes") === c.recruit) &&
          passes(c, view.filters || {}),
      );
  shown.sort(comparator(view, collator));
  const groups = [];
  const mods = unfiltered ? "all" : view.mods;
  if (mods !== "none") groups.push({ id: "mods", items: shown.filter((c) => c.mods) });
  if (mods !== "mods") groups.push({ id: "none", items: shown.filter((c) => !c.mods) });
  return { groups, total: all.length };
}

/**
 * The cards in the roster's order, in one list.
 * @param {{families?: object[]}} data The roster data of roster.list.
 * @returns {object[]}
 */
export const order = (data, view, opts) => arrange(data, view, opts).groups.flatMap((g) => g.items);

// a visit to a variant in roster.list's data, as the server records it (webui/state.py UiState.visit: first in the
// recently viewed, the ones before it one down, at most RECENT_MAX kept), so the list has it without a new roster.list
export const RECENT_MAX = 50;
/**
 * Record a visit to a variant in roster.list's data, as the server does (webui/state.py UiState.visit: first in
 * the recently viewed, the ones before it one down, at most RECENT_MAX kept), so the list has it without a new
 * roster.list.
 * @param {{families?: object[]}} data The roster data; updated in place.
 * @param {string} key The visited variant's key.
 */
export function visit(data, key) {
  const vs = (data?.families || []).flatMap((f) => f.variants || []);
  const was = vs.find((v) => v.key === key)?.recent ?? Infinity;
  for (const v of vs) {
    if (v.key === key) v.recent = 0;
    else if (v.recent != null && v.recent < was) v.recent = v.recent + 1 < RECENT_MAX ? v.recent + 1 : null;
  }
}

/**
 * Find the card a step away from the shown one in a list, wrapping round.
 * A variant the list hides (another chip of the family, a deep link) steps to the nearest listed card after or
 * before its place in full.
 * @param {object[]} list
 * @param {object[]|(() => object[])|null} full The unfiltered order, or a function making it.
 * @param {{family: string, variant: string, merged: boolean}} at
 * @param {number} delta The step, +1 or -1.
 * @returns {object|null} The card, or null when there is nowhere else to go.
 */
export function neighbour(list, full, at, delta) {
  const usable = list.filter((c) => c.spr || c.lobby || c.model || c.storedKinds?.length);
  if (!usable.length) return null;
  const id = (c) => (at.merged ? c.family : c.key);
  const here = at.merged ? at.family : at.variant;
  const step = (i, len) => (((i + delta) % len) + len) % len;
  let next = null;
  const i = usable.findIndex((c) => id(c) === here);
  if (i >= 0) {
    next = usable[step(i, usable.length)];
  } else {
    const all = typeof full === "function" ? full() : full || [];
    const byId = new Map(usable.map((c) => [id(c), c]));
    const j = all.findIndex((c) => id(c) === here);
    if (j >= 0) {
      for (let k = 1, p = j; k < all.length && !next; k++) {
        p = step(p, all.length);
        next = byId.get(id(all[p])) || null;
      }
    }
    next ||= usable[delta > 0 ? 0 : usable.length - 1];
  }
  return next && id(next) !== here ? next : null;
}
