// The slot colours of a lobby skeleton as the shaders of its atlas pages take them (lobby.json layer "shaders", each
// page's shader by the page's name). spine-unity hands every shader the slot colour as a premultiplied vertex colour in
// gamma space (an additive slot with alpha 0). Spine/Skeleton and the blend-mode shaders decode it with a cubic fit of
// the sRGB curve (divided by the alpha, decoded, multiplied by the alpha again; alpha 0 decoded as a whole);
// Spine/Skeleton MX Portrait with the exact sRGB curve; the MX Standard pages (97 lobbies) and NoiseFlowAnimation pass
// it on as it is; MX Portrait Noise reads only the colour's alpha. Without the page's shader a slot is decoded with the
// exact curve.
import { pageOf } from "./skeleton-parts.js";

const exact = (c) => (c <= 0.04045 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4));
const cubic = (c) => c * (c * (c * 0.305306011 + 0.682171111) + 0.012522878);
const asIs = (c) => c;
const unread = () => 1;
export const CURVES = Object.freeze({ exact, cubic, asIs, unread });

const BY_SHADER = Object.freeze({
  "Spine/Skeleton": cubic,
  "Spine/Blend Modes/Skeleton PMA Multiply": cubic,
  "Spine/Skeleton MX Portrait": exact,
  "Spine/Skeleton MX Standard": asIs,
  "Spine/Skeleton MX Standard - NoiseFlowAnimation": asIs,
  "Spine/Skeleton MX Portrait Noise": unread,
});

/**
 * The curve a shader decodes the vertex colours with: its own, or the exact sRGB curve when the shader is not
 * known.
 */
export const curveOf = (shader) => BY_SHADER[shader] || exact;

export let byShader = true; // for checks: false decodes every slot, whatever its page's shader
/**
 * For checks: decode by each page's shader (true) or every slot with the exact curve (false).
 */
export const setByShader = (on) => {
  byShader = !!on;
};

/**
 * A layer's curve for an attachment, by the page it lies on (linear.js setCurves).
 * @param {object} layer The lobby's layer.
 * @returns {((attachment: object) => Function)|null} Gives an attachment's curve; null when the layer has no
 *     page shaders, which keeps every slot decoded.
 */
export function curvesOf(layer) {
  const byPage = new Map(Object.entries(layer?.shaders || {}).map(([page, shader]) => [page, curveOf(shader)]));
  if (!byPage.size) return null;
  return (attachment) => (byShader && byPage.get(pageOf(attachment))) || exact;
}
