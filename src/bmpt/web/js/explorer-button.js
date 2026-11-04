// The detail screen's Show in Finder / Show in Explorer button (UICharacterDetail .../1_NormalInfo/Status/Btn_Up_Wrapper/Btn_Up):
// the prefab's inclined body, 200 wide and 180 high, with the folder icon over the caption. The caption takes up to three lines
// inside the body at the largest size from the prefab's range (32 down to 26) that fits, before it shrinks.
import { fitGroup, fontSizeFor, LINE_PITCH } from "./ngui.js";
import { TAN10 } from "./util.js";

// The body (BG (1)) in the button's own units, y up: centred at -6.3, 180 high.
export const BODY = { top: 83.7, bottom: -96.3, x: -1.4 };
export const CAPTION = { fsMax: 32, fsMin: 26, room: 140, maxLines: 3 };
export const ICON = { w: 56, h: 50 };
// Folder icon centres by lines of caption: one and two lines have their place; three lines move the icon up to the body's top
// edge and the caption down under it.
const FOLDER_Y = { 1: 26, 2: 38, 3: 54 };
const CAPTION_Y = { 1: -46, 2: -36, 3: -32 };
const GAP = 6, MARGIN = 10; // clear between the icon and the caption, and between the caption and the body's bottom edge
// the tallest caption: what the three-line layout leaves between the icon's bottom edge and the body's
const BAND = FOLDER_Y[3] - ICON.h / 2 - GAP - (BODY.bottom + MARGIN);

// The caption's text and size and where it and the icon go (units, x slanted with y like the body): { text, fs, lines,
// label: [x, y, h], folder: [x, y] }. h: the label box's height.
export function explorerLayout(caption) {
  const [fit] = fitGroup([caption], CAPTION.fsMax, CAPTION.fsMin, CAPTION.room, CAPTION.maxLines, { maxH: BAND });
  const lines = Math.min(3, Math.max(1, fit.lines)), slant = (y) => BODY.x + y * TAN10;
  const height = Math.max(40 * lines, lines * fontSizeFor(fit.text, fit.fs) * LINE_PITCH);
  return { text: fit.text, fs: fit.fs, lines, overflow: !!fit.overflow, label: [slant(CAPTION_Y[lines]), CAPTION_Y[lines], height],
    folder: [slant(FOLDER_Y[lines]), FOLDER_Y[lines]] };
}
