// Button decoration patterns are separate NGUI widgets: their anchors, pivot and size follow the body, not its
// caption.
import { maskWithSprite, spriteMaskTransform } from "./sprite-mask.js";
import { CHIP_TIERS, CONTROL } from "./ui-tokens.js";
const PIVX = [0, 0.5, 1, 0, 0.5, 1, 0, 0.5, 1];
const PIVY = [1, 1, 1, 0.5, 0.5, 0.5, 0, 0, 0];

/**
 * Resize a button body in place, moving its decoration patterns with the body's edges.
 * @param {number|null} [width] The new body width in NGUI units; unchanged when null.
 * @param {number|null} [height] The new body height; unchanged when null.
 */
export function resizeButton(body, width = null, height = null) {
  const bg = body.widgets.find((w) => "sprite" in w && "w_" in w);
  if (!bg) return body;
  const oldW = bg.w_,
    oldH = bg.h_,
    pivot = bg.pivot ?? 4;
  bg.w_ = width ?? oldW;
  bg.h_ = height ?? oldH;
  for (const child of body.children || []) {
    for (const w of child.widgets) {
      if (!/^Common_Btn_.*_Pt$/.test(w.sprite || "")) continue;
      const anchors = w.anchors || {},
        p = w.pivot ?? 4;
      const axis = (i, size, old, bp, cp, lo, hi) => {
        const scale = child.scale[i],
          span = w[size] * scale;
        const start = -bp * bg[size],
          end = start + bg[size];
        const edge = (key) => (anchors[key] ? start + (end - start) * anchors[key][0] + anchors[key][1] : null);
        let a = edge(lo),
          b = edge(hi);
        if (a == null && b == null) {
          // Unanchored patterns retain their prefab edge offsets. Items/UseBtn's second pattern is mirrored
          // on the left/bottom; Mailbox/ReceiveAll uses a negative child scale for the same decoration.
          const flipped = i === 0 ? w.flip === 1 || w.flip === 3 : w.flip >= 2;
          const mirrored = flipped !== scale < 0;
          child.pos[i] += (bg[size] - old) * ((mirrored ? 0 : 1) - bp);
          return;
        }
        if (a == null) a = b - span;
        if (b == null) b = a + span;
        child.pos[i] = a + (b - a) * cp;
        w[size] = (b - a) / scale;
      };
      axis(0, "w_", oldW, PIVX[pivot], PIVX[p], "leftAnchor", "rightAnchor");
      axis(1, "h_", oldH, PIVY[pivot], PIVY[p], "bottomAnchor", "topAnchor");
    }
  }
  return body;
}

/**
 * Clip a button's decoration to its inclined body, as the game does.
 * NGUI's DOM renderer flattens the hierarchy, so the clipping is restored from the rendered prefab body, after
 * resizeButton has resolved the patterns' anchors.
 * @param {RenderView} view What render() drew.
 * @param {{load?: (url: string) => Promise<HTMLImageElement>}} [options] The image loader; without it only the
 *     polygon clip is set.
 * @returns {Promise<unknown[]>} Settles when the alpha masks are applied.
 */
export function clipButtonPatterns(view, { load } = {}) {
  const pending = [];
  for (const [path, entry] of view.nodes) {
    const w = entry.node.widgets.find((w) => /^Common_Btn_.*_Pt$/.test(w.sprite || ""));
    if (!w) continue;
    const parent = view.nodes.get(path.slice(0, path.lastIndexOf("/")));
    const body = parent?.node.widgets.find((w) => w.sprite === "Common_Btn_BG");
    const element = entry.els[0];
    if (!body || !element) continue;
    const rect = (el) => ["left", "top", "width", "height"].map((key) => parseFloat(el.style[key]));
    const source = parent.els[0],
      [, , bw, bh] = rect(source);
    // The cheap bounds are available before asynchronous sprite painting. The
    // alpha mask then adds the official body's rounded corners, which a four-
    // corner polygon cannot represent.
    const transform = (widget) =>
      `${widget.incline ? `skewX(${-widget.incline}deg)` : ""} scale(${widget.flip === 1 || widget.flip === 3 ? -1 : 1}, ${widget.flip >= 2 ? -1 : 1})`;
    const style = (el, widget) =>
      Object.fromEntries(
        ["left", "top", "width", "height", "transform"].map((key) => [
          key,
          key === "transform" ? el.style.transform || transform(widget) : el.style[key],
        ]),
      );
    const sourceStyle = style(source, body),
      targetStyle = style(element, w);
    const [a, b, c, d, e, f] = spriteMaskTransform(sourceStyle, targetStyle);
    const points = [
      [0, 0],
      [bw, 0],
      [bw, bh],
      [0, bh],
    ].map(([x, y]) => [a * x + c * y + e, b * x + d * y + f]);
    element.style.clipPath = `polygon(${points.map(([x, y]) => `${x}px ${y}px`).join(", ")})`;
    if (load)
      pending.push(Promise.all(view.images || []).then(() => maskWithSprite(source, element, load, { surface: true })));
  }
  return Promise.all(pending);
}

// The size tag of a button (developer previews only): which of the presets (XXS, XS, S, M, L) a button body is,
// by its height in the app's units (what it measures on the stage: its prefab size, the nodes' scales and the
// render's scale). A pill that is none of them (a compact body, a list's tool, a footer scaled to fit a row) is
// tagged with its measured height instead. A pill is a body at most PILL.maxH high and PILL.ratio times as wide
// as high: the cards, thumbnails and square action buttons that also draw Common_Btn_BG are not tagged.
export const PILL = Object.freeze({ maxH: 200, ratio: 1.5 });
export const BUTTON_TIERS = Object.freeze([
  ["XXS", CONTROL.buttonXXS],
  ["XS", CONTROL.buttonXS],
  ["S", CONTROL.buttonS],
  ["M", CONTROL.buttonM],
  ["L", CONTROL.buttonL],
]);
/**
 * The button preset a body height belongs to.
 * @param {number} height The body height in the app's units.
 * @param {number} [tolerance] How far the height may be from the preset's.
 * @returns {string|null} "XXS", "XS", "S", "M" or "L"; null when the height is none of them.
 */
export function buttonTier(height, tolerance = 0.5) {
  return BUTTON_TIERS.find(([, control]) => Math.abs(height - control.h) <= tolerance)?.[0] ?? null;
}
/**
 * Whether a body is shaped like a button.
 * @returns {boolean} True for a body at most PILL.maxH high and PILL.ratio times as wide as high.
 */
export const isPill = (width, height) => height <= PILL.maxH && width >= PILL.ratio * height;
// The chips' four sizes (CHIP_TIERS), tagged the same way as the buttons.
export const CHIP_TIER_LIST = Object.freeze(["XS", "S", "M", "L"].map((name) => [name, CHIP_TIERS[name]]));
/**
 * The chip preset a body height belongs to.
 * @param {number} height The body height in the app's units.
 * @param {number} [tolerance] How far the height may be from the preset's.
 * @returns {string|null} "XS", "S", "M" or "L"; null when the height is none of them.
 */
export function chipTier(height, tolerance = 0.5) {
  return CHIP_TIER_LIST.find(([, chip]) => Math.abs(height - chip.h) <= tolerance)?.[0] ?? null;
}
/**
 * The size tag of a body.
 * @param {string|null} [kind] What the widget says it is (its `tier`): "button" for a button whatever its shape
 *     (a narrow body in a row of actions), "chip" for a chip, measured against the chips' sizes; without it, a
 *     Common_Btn_BG pill is a button.
 * @returns {{tier: string|null, text: string}|null} The tier and the text of the tag (the height when
 *     off-preset); null for a body that is no button.
 */
export function tagOf(width, height, kind = null) {
  if (kind !== "button" && kind !== "chip" && !isPill(width, height)) return null;
  const tier = kind === "chip" ? chipTier(height) : buttonTier(height);
  return { tier, text: tier ?? String(Math.round(height)) };
}

// The tag is a small label at the body's top left corner. Where it would cover the caption's ink it becomes a
// dot there, the label shown on hover (the tag must never hide the pill's text). Boxes are [left, top, width,
// height] in px.
export const TAG = Object.freeze({ h: 16, padX: 10, perChar: 8 });
/**
 * The box of a size tag.
 * @param {number} left The body's left edge in px.
 * @param {number} top The body's top edge in px.
 * @returns {number[]} [left, top, width, height] in px.
 */
export const tagBox = (text, left, top) => [left, top, TAG.padX + TAG.perChar * String(text).length, TAG.h];
/**
 * Whether two boxes overlap.
 * @param {number[]} a [left, top, width, height].
 * @param {number[]} b [left, top, width, height].
 * @returns {boolean} True when they share area.
 */
export const overlaps = (a, b) => a[0] < b[0] + b[2] && b[0] < a[0] + a[2] && a[1] < b[1] + b[3] && b[1] < a[1] + a[3];
/**
 * The ink of a text widget inside its box: lines of the widest `inkW` and the height `inkH`, placed as the
 * widget aligns them.
 * @param {number[]} box [left, top, width, height] of the widget's box.
 * @param {number} inkW The widest line's width.
 * @param {"left"|"right"|"center"} align
 * @param {"flex-start"|"flex-end"|"center"} vertical
 * @returns {number[]} [left, top, width, height] of the ink.
 */
export function inkBox([left, top, width, height], inkW, inkH, align, vertical) {
  const x = align === "right" ? left + width - inkW : align === "left" ? left : left + (width - inkW) / 2;
  const y = vertical === "flex-start" ? top : vertical === "flex-end" ? top + height - inkH : top + (height - inkH) / 2;
  return [x, y, inkW, inkH];
}
