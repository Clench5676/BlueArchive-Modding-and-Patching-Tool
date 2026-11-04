// Where an empty list's message goes. Batch Import's list (Items ListEmpty) and the student roster (UICharacterCollection
// LeftInfo_Empty) centre their block on the middle of the paper the eye sees; no DOM, so the tests can run it.
export const EMPTY_GAP = 85;      // between the centres of Items' ListEmpty message and the drop hint under it

// The box centres (px, down) of the ListEmpty message and the drop hint under it (null: none) that put the block's ink
// centre on `centre`, as the eye takes the middle of the list. title, note: each label's ink as offsets of its top and
// bottom from its box centre (NGUI.inkBand). A title may stand for a block above its label too (the roster's picture):
// then `top` reaches up to the block's first ink.
export function emptyBlock(centre, title, note = null, gap = EMPTY_GAP) {
  const bottom = note ? gap + note.bottom : title.bottom;
  const first = centre - (title.top + bottom) / 2;
  return { title: first, note: note ? first + gap : null };
}
