import { maskWithSprite } from "./sprite-mask.js";

/**
 * Clip a roster card's band and portrait to the game's filled card surface. Its bevel and shadow are outside
 * the picture, including the diagonal top-left/bottom-right cuts.
 * @param {RenderView} view What render() drew.
 * @param {string} root The card's path.
 * @param {object} [options] Options.
 * @param {(url: string) => Promise<HTMLImageElement>} [options.load] The image loader.
 * @returns {Promise<void>} Settles when the masks are applied.
 */
export async function clipRosterBand(view, root, { load } = {}) {
  await Promise.all(view.images || []);
  const frame = view.nodes.get(`${root}/BG`)?.els[0];
  const band = view.nodes.get(`${root}/Sort/Sprite`)?.els[0];
  const portrait = view.nodes.get(`${root}/Char_Portrait`)?.els[0];
  if (!frame) return;
  await Promise.all(
    [band, portrait].filter(Boolean).map((element) => maskWithSprite(frame, element, load, { surface: true })),
  );
}
