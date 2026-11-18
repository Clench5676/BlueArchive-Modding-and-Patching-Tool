import { maskWithSprite } from "../ui/sprite-mask.js";

/**
 * Clip a student list card's band and portrait to the game's filled card surface. Its bevel and shadow are outside
 * the picture, including the diagonal top-left/bottom-right cuts.
 * @param {RenderView} view What render() drew.
 * @param {object} [options]
 * @param {(url: string) => Promise<HTMLImageElement>} [options.load]
 * @returns {Promise<void>} Settles when the masks are applied.
 */
export async function clipStudentListBand(view, root, { load } = {}) {
  await Promise.all(view.images || []);
  const frame = view.nodes.get(`${root}/BG`)?.els[0];
  const band = view.nodes.get(`${root}/Sort/Sprite`)?.els[0];
  const portrait = view.nodes.get(`${root}/Char_Portrait`)?.els[0];
  if (!frame) return;
  await Promise.all(
    [band, portrait].filter(Boolean).map((element) => maskWithSprite(frame, element, load, { surface: true })),
  );
}
