// Where an empty list's message goes. Batch Import's list (Items ListEmpty) and the student list
// (UICharacterCollection LeftInfo_Empty) centre their block on the middle of the paper the eye sees; no DOM, so
// the tests can run it.
export const EMPTY_GAP = 85; // between the centres of Items' ListEmpty message and the drop hint under it

/**
 * The box centres (px, down) of the ListEmpty message and the drop hint under it (null: none) that put the
 * block's ink centre on `centre`, as the eye takes the middle of the list. A title may stand for a block above
 * its label too (the student list's picture): then `top` reaches up to the block's first ink.
 * @param {number} centre The centre to put the block's ink on.
 * @param {{top: number, bottom: number}} title The message's ink as offsets of its top and bottom from its box
 *     centre (NGUI.inkBand).
 * @param {{top: number, bottom: number}|null} [note] The hint's ink, the same way.
 * @param {number} [gap] The distance between the centres of the message and the hint.
 * @returns {{title: number, note: number|null}} The box centres.
 */
export function emptyBlock(centre, title, note = null, gap = EMPTY_GAP) {
  const bottom = note ? gap + note.bottom : title.bottom;
  const first = centre - (title.top + bottom) / 2;
  return { title: first, note: note ? first + gap : null };
}
