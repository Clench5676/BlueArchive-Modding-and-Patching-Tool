// The 3D tab's action bar (row 4): one card per action group, and above the pressed one its action list. The
// list is the game's anchored description tooltip, as the hover hints draw it (hover-hint.js:
// UIDescriptionTooltip's Default box, Common_Tooltip_Bg, without its arrow), holding the group's sections as
// UIShortcutTooltip's list title bars and its actions as UISortObjectListUnit radio keys, one to a line. Pure
// geometry and text fit in the detail screen's units (y up, the prefab's centre at the stage's [960, 515] px),
// for action-list.js, detail.js and the tests.
import * as NGUI from "./ngui.js";
import { BOX_INSET } from "./hover-hint.js";
import { BAR_HEIGHT } from "../window/top-bar-layout.js";
import { POPUP_TYPE } from "./ui-tokens.js";
import { S, TAN10 } from "../core/util.js";

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
/**
 * A key's label box width: left-aligned labelX right of the radio, ending at the content's right edge (a wider
 * box would make the list scroll sideways, and its scroll fade would wash out the title bars' right ends).
 */
export const keyLabelWidth = (cw) => Math.min(ROW.maxLabelW, cw - ROW.radioX - ROW.labelX);
export const KEY_TYPE = POPUP_TYPE.card.option; // 42 down to 30, as the student list's sort keys
// A group card's texts (detail.js stripCard: 242 wide, the visible body 196 high): the names are siblings with
// one size for every card, wrapped onto two lines before they shrink; the playing group's card shows the playing
// action's name alone instead, at the names' size, never larger (down to 20, three lines, only when it does not
// fit), and moves down only as far as it takes to clear the check.
export const CARD_TEXT = {
  w: 182,
  room: 196 - 2 * 12,
  name: { fs: KEY_TYPE.fs, min: 28 },
  playing: { lines: 3, min: 20 },
  check: { left: 242 / 2 - 30 + (98 - 26) * TAN10 - 56 / 2, bottom: 98 - 26 - 54 / 2, clear: 0 },
};

/**
 * The list's rows from the top of its content (y down is negative) and the content's height. A group of one
 * section has no title bar: its card names it.
 * @param {boolean} [titled] Whether sections get title bars.
 * @returns {{rows: {kind: "head"|"key", y: number, section?: object, action?: object}[], height: number}} The
 *     rows and the content's height.
 */
export function stackList(sections, titled = sections.length > 1) {
  const rows = [];
  let y = 0;
  sections.forEach((s, i) => {
    if (i) y -= HEAD.gap;
    if (!titled)
      return void s.actions.forEach((a) => {
        rows.push({ kind: "key", y: y - ROW.pitch / 2, action: a });
        y -= ROW.pitch;
      });
    rows.push({ kind: "head", y: y - HEAD.h / 2, section: s });
    y -= HEAD.h;
    for (const a of s.actions) {
      rows.push({ kind: "key", y: y - ROW.pitch / 2, action: a });
      y -= ROW.pitch;
    }
  });
  return { rows, height: -y };
}

/**
 * The box's content width for the fitted texts: the widest key (radio, gap, label) or title, within the box's
 * limits.
 */
export function contentWidth(keyWidths, headWidths) {
  const keys = Math.max(0, ...keyWidths) + ROW.radioX + ROW.labelX + ROW.end;
  const heads = Math.max(0, ...headWidths) + 2 * HEAD.inset + HEAD.slant;
  return Math.min(BOX.maxW - 2 * BOX.padX, Math.max(BOX.minW, Math.ceil(Math.max(keys, heads))));
}

/**
 * Where the list goes for a card: the box's visible bottom edge stands GAP over the card, centred on it,
 * shifted only as far as the stage (less EDGE) needs; as tall as the content, at most up to the stage's top
 * bar.
 * @param {{left: number, right: number, top: number}} button The card's visible body in units.
 * @returns {object} The box's sprite rect {left, top, w, h} in units, the content's visible height (view) and
 *     whether it scrolls.
 */
export function placeList(button, contentW, contentH) {
  const w = contentW + 2 * BOX.padX,
    visW = w - BOX_INSET.left - BOX_INSET.right;
  const centre = (button.left + button.right) / 2;
  const visLeft = Math.max(STAGE.left + EDGE, Math.min(STAGE.right - EDGE - visW, centre - visW / 2));
  const visBottom = button.top + GAP,
    room = STAGE.top - EDGE - visBottom;
  const chrome = BOX.padTop + BOX.padBottom - BOX_INSET.top - BOX_INSET.bottom; // the visible box around its content
  const view = Math.max(ROW.pitch, Math.min(contentH, room - chrome));
  const h = view + BOX.padTop + BOX.padBottom;
  return {
    left: visLeft - BOX_INSET.left,
    top: visBottom - BOX_INSET.bottom + h,
    w,
    h,
    view,
    scrolls: view < contentH,
    visible: { left: visLeft, right: visLeft + visW, top: visBottom + view + chrome, bottom: visBottom },
  };
}

/**
 * The list's texts: the titles one line each, the keys one size (two lines at most, within a line's height).
 * @param {object[]} heads The section titles.
 * @param {string[]} keys The action names.
 * @param {string|null} [lang] The language of the texts when it is not the page's.
 */
export function fitList(heads, keys, lang = null) {
  return {
    heads: heads.map((h) =>
      NGUI.fitText(h, HEAD.fs, HEAD.min, BOX.maxW - 2 * BOX.padX - 2 * HEAD.inset - HEAD.slant, 1, lang),
    ),
    keys: NGUI.fitGroup(keys, KEY_TYPE.fs, KEY_TYPE.min, ROW.maxLabelW, 2, { lang, maxH: ROW.pitch - 24 }),
  };
}

// the cards' names: one size for all (the cards are siblings), two lines at most in the card's room
export function fitCards(names, lang = null) {
  return NGUI.fitGroup(names, CARD_TEXT.name.fs, CARD_TEXT.name.min, CARD_TEXT.w, 2, { lang, maxH: CARD_TEXT.room });
}
// a fitted text's height, NGUI's line pitch
export const textHeight = (f) => NGUI.fontSizeFor(f.text, f.fs) * NGUI.LINE_PITCH * f.lines;
// its ink's height: the lines less the line pitch's spacing over the first and under the last line
export const inkHeight = (f) => textHeight(f) - (NGUI.LINE_PITCH - 1) * NGUI.fontSizeFor(f.text, f.fs);
// a card's text: { name, nameY } from the card's centre. A group's card: its name's fit (`fit`, shared with the
// siblings), centred. The playing group's card: the playing action's name (`playing`) alone, at the siblings' size
// or smaller, centred unless its first line would reach under the check.
export function cardText(fit, playing = null, lang = null) {
  if (!playing) return { name: fit, nameY: 0 };
  const { room, playing: P, check } = CARD_TEXT;
  // centred, or moved down just under the check when its first line reaches that far right; null when even that
  // leaves the body
  const place = (name) => {
    const h = textHeight(name),
      ink = inkHeight(name),
      first = NGUI.glyphWidth(String(name.text).split("\n")[0], name.fs);
    if (h > room + 1e-6) return null;
    if (ink / 2 <= check.bottom - check.clear || first / 2 <= check.left - check.clear) return 0;
    const y = check.bottom - check.clear - ink / 2;
    return y - h / 2 >= -room / 2 - 1e-6 ? y : null;
  };
  // the largest size from the siblings' down that both fits and clears the check
  let last = null;
  for (let fs = fit.fs; fs >= P.min; fs -= 2) {
    const name = NGUI.wrapText(playing, fs, CARD_TEXT.w, P.lines, lang);
    if (name.overflow) continue;
    const y = place(name);
    if (y !== null) return { name, nameY: y };
    last = name;
  }
  const name = last || NGUI.fitGroup([playing], fit.fs, P.min, CARD_TEXT.w, P.lines, { lang, maxH: room })[0];
  return { name, nameY: 0 };
}
