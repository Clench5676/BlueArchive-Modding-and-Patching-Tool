// The 3D tab's action bar (row 4): one card per action group, and above the pressed one its action list. The list is
// the game's anchored description tooltip, as the hover hints draw it (hover-hint.js: UIDescriptionTooltip's
// Default box, Common_Tooltip_Bg, without its arrow), holding the group's sections as UIShortcutTooltip's list title
// bars (GetList/TitleBG: Common_Bg_Raius5px, #334a66, 60 high, 44 white) and its actions as UISortObjectListUnit radio
// keys in their Label_Text form, one to a line. Pure geometry and text fit in the detail screen's units (y up, the
// prefab's centre at the stage's [960, 515] px), for action-list.js, detail.js and the tests.
import * as NGUI from "./ngui.js";
import { BOX_INSET } from "./hover-hint.js";
import { BAR_HEIGHT } from "./top-bar-layout.js";
import { POPUP_TYPE } from "./ui-tokens.js";
import { S, TAN10 } from "./util.js";

// the stage in the detail screen's units: 1920 x 1080 px around the prefab's centre at [960, 515] px; the top bar's
// BAR_HEIGHT px over its top. The list keeps EDGE inside it.
export const STAGE = { left: -960 / S, right: 960 / S, top: (515 - BAR_HEIGHT) / S, bottom: -(1080 - 515) / S };
export const EDGE = 12;
// the list's box over its card: hover-hint.js's GAP between the box's visible edge and the control
export const GAP = 12;
// the box (UIDescriptionTooltip Default/BG, hover-hint.js hintStyle): 62 of room at the sides, 46 above, 60 under
// the content; at most the prefab's 834 wide; the content at least MIN_W wide
export const BOX = { padX: 62, padTop: 46, padBottom: 60, maxW: 834, minW: 420 };
// a section's title bar (UIShortcutTooltip GetList/TitleBG) and the room over it after a section's last key; the bar
// leans 10 degrees (its incline), so its tips reach slant / 2 past its width on either side: it is drawn that much
// (and 2 more) narrower than the content, whose scroll area clips at its edges
export const HEAD = { h: 60, gap: 20, inset: 30, fs: 44, min: 36, slant: 60 * TAN10 + 4 };
// a key: the radio's centre 30 inside the content, Label_On 61.5 right of it; a line every ROW, its underline
// (UISortObjectListUnit Line) under it
export const ROW = { pitch: 120, radioX: 30, labelX: 61.5, end: 20, maxLabelW: 590 };
// a key's label box: left-aligned labelX right of the radio, ending at the content's right edge (a wider box would
// make the list scroll sideways, and its scroll fade would wash out the title bars' right ends)
export const keyLabelWidth = (cw) => Math.min(ROW.maxLabelW, cw - ROW.radioX - ROW.labelX);
export const KEY_TYPE = POPUP_TYPE.card.option; // 42 down to 30, as the roster's sort keys
// a group card's texts (detail.js stripCard: 242 wide less 40, 20 clear at either side; the visible body 196 high, 12
// clear above and below): the group's name at the caption's size, wrapped onto two lines before it shrinks, centred.
// The playing group's card (selected: Card_Select_Line_Blue and its check, Common_Icon_Check_B 54 high, centred 26
// under the body's top, at its right) shows the playing action's name alone instead, as large as the names: one line
// centred on the card (it passes under the check), else up to three lines under the check (3 clear) and 6 over the
// body's bottom, down to 22 for the longest (a second battle form's random Tactical Challenge entry).
export const CARD_TEXT = { w: 242 - 40, room: 196 - 2 * 12, name: { fs: 56, min: 28 },
  playing: { top: 98 - 26 - 54 / 2 - 3, bottom: -98 + 6, lines: 3, min: 22 }, checkBottom: 98 - 26 - 54 / 2 };

// the list's rows from the top of its content (y down is negative): [{ kind: "head"|"key", y, section | action }]
// and the content's height. A group of one section has no title bar: its card names it.
export function stackList(sections, titled = sections.length > 1) {
  const rows = [];
  let y = 0;
  sections.forEach((s, i) => {
    if (i) y -= HEAD.gap;
    if (!titled) return void s.actions.forEach((a) => { rows.push({ kind: "key", y: y - ROW.pitch / 2, action: a }); y -= ROW.pitch; });
    rows.push({ kind: "head", y: y - HEAD.h / 2, section: s });
    y -= HEAD.h;
    for (const a of s.actions) {
      rows.push({ kind: "key", y: y - ROW.pitch / 2, action: a });
      y -= ROW.pitch;
    }
  });
  return { rows, height: -y };
}

// the box's content width for the fitted texts: the widest key (radio, gap, label) or title, within the box's limits
export function contentWidth(keyWidths, headWidths) {
  const keys = Math.max(0, ...keyWidths) + ROW.radioX + ROW.labelX + ROW.end;
  const heads = Math.max(0, ...headWidths) + 2 * HEAD.inset + HEAD.slant;
  return Math.min(BOX.maxW - 2 * BOX.padX, Math.max(BOX.minW, Math.ceil(Math.max(keys, heads))));
}

// Where the list goes for a card (button: { left, right, top } in units, its visible body): the box's sprite rect
// { left, top, w, h } in units, the content's visible height (view) and whether it scrolls. The box's visible bottom edge
// stands GAP over the card, centred on it, shifted only as far as the stage (less EDGE) needs; as tall as the content,
// at most up to the stage's top bar.
export function placeList(button, contentW, contentH) {
  const w = contentW + 2 * BOX.padX, visW = w - BOX_INSET.left - BOX_INSET.right;
  const centre = (button.left + button.right) / 2;
  const visLeft = Math.max(STAGE.left + EDGE, Math.min(STAGE.right - EDGE - visW, centre - visW / 2));
  const visBottom = button.top + GAP, room = STAGE.top - EDGE - visBottom;
  const chrome = BOX.padTop + BOX.padBottom - BOX_INSET.top - BOX_INSET.bottom; // the visible box around its content
  const view = Math.max(ROW.pitch, Math.min(contentH, room - chrome));
  const h = view + BOX.padTop + BOX.padBottom;
  return { left: visLeft - BOX_INSET.left, top: visBottom - BOX_INSET.bottom + h, w, h, view, scrolls: view < contentH,
    visible: { left: visLeft, right: visLeft + visW, top: visBottom + view + chrome, bottom: visBottom } };
}

// the list's texts: the titles one line each, the keys one size (two lines at most, within a line's height)
export function fitList(heads, keys, lang = null) {
  return {
    heads: heads.map((h) => NGUI.fitText(h, HEAD.fs, HEAD.min, BOX.maxW - 2 * BOX.padX - 2 * HEAD.inset - HEAD.slant, 1, lang)),
    keys: NGUI.fitGroup(keys, KEY_TYPE.fs, KEY_TYPE.min, ROW.maxLabelW, 2, { lang, maxH: ROW.pitch - 24 }),
  };
}

// the cards' names: one size for all (the cards are siblings), two lines at most in the card's room
export function fitCards(names, lang = null) {
  return NGUI.fitGroup(names, CARD_TEXT.name.fs, CARD_TEXT.name.min, CARD_TEXT.w, 2, { lang, maxH: CARD_TEXT.room });
}
// a fitted text's height, NGUI's line pitch
export const textHeight = (f) => NGUI.fontSizeFor(f.text, f.fs) * NGUI.LINE_PITCH * f.lines;
// a card's text: { name, nameY } from the card's centre. A group's card: its name's fit (`fit`, shared with the
// siblings), centred. The playing group's card: the playing action's name (`playing`) alone, fitted on its own.
export function cardText(fit, playing = null, lang = null) {
  if (!playing) return { name: fit, nameY: 0 };
  const { top, bottom } = CARD_TEXT.playing;
  const name = NGUI.fitGroup([playing], CARD_TEXT.name.fs, CARD_TEXT.playing.min, CARD_TEXT.w, CARD_TEXT.playing.lines, { lang, maxH: top - bottom })[0];
  return { name, nameY: name.lines > 1 ? (top + bottom) / 2 : 0 };
}
