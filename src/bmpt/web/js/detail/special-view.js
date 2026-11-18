import { validRect } from "../memorial-lobby/memorial-lobby-health.js";

/**
 * Whether an alt is a special slot (a spine category, or a key starting with "special:").
 */
export const isSpecial = (v) => !!(v?.spineCategory || v?.key?.startsWith("special:"));
/**
 * The framing of a special alt's own memorial lobby art: null for an ordinary alt.
 * @returns {string|null} "character" or "scene" by its name, or null.
 */
export function specialFraming(v) {
  if (!isSpecial(v)) return null;
  const core = v.memorialLobby || v.key.slice("special:".length),
    category = v.spineCategory;
  if (/_(?:npc|spr)$/i.test(core) || (category === "spinecharacters" && !/workpage|background|cafe|task/i.test(core)))
    return "character";
  return "scene";
}

/**
 * The framing a special slot's art gets: its name's framing, except that one skeleton taller than wide is a
 * standing character whatever its bundle category (an operator or guide NPC filed as a background:
 * momoka_weekdungeon), never a scene a 16:9 view can fill. A set of several skeletons (ui_set, spinebg) keeps
 * its name's framing. Every view of a special slot (detail, fullscreen, thumbnails) decides through here.
 * @param {string|null} framing The framing its name gives.
 * @param {{x0: number, x1: number, y0: number, y1: number}} bounds The art's bounds.
 * @param {number} [skeletons] How many skeletons the bounds cover.
 */
export function specialFramingOf(framing, bounds, skeletons = 1) {
  if (framing !== "scene" || skeletons !== 1 || !validRect(bounds)) return framing;
  return bounds.x1 - bounds.x0 < bounds.y1 - bounds.y0 ? "character" : "scene";
}

/**
 * The view of a special slot's art: fit the whole NPC, or fill the canvas with a scene. Both use drawable
 * skeleton bounds, independent of export scale.
 * @param {{x0: number, x1: number, y0: number, y1: number}} bounds The art's bounds.
 * @param {string|null} framing "character" or "scene".
 * @param {number} [aspect] The view's width over its height.
 * @param {number} [anchor] Where along the width the art is centred, 0 to 1.
 * @returns {{x: number, y: number, w: number, h: number}} The view in skeleton units.
 */
export function specialView(bounds, framing, aspect = 16 / 9, anchor = 0.5) {
  if (!validRect(bounds) || !(aspect > 0)) throw new Error("No drawable L2D bounds");
  const bw = bounds.x1 - bounds.x0,
    bh = bounds.y1 - bounds.y0;
  const w =
      framing === "character"
        ? Math.max(bw / (2 * Math.min(anchor, 1 - anchor)), bh * aspect)
        : Math.min(bw, bh * aspect),
    h = w / aspect;
  return { x: bounds.x0 + bw / 2 - w * anchor, y: bounds.y0 + bh / 2 - h / 2, w, h };
}
