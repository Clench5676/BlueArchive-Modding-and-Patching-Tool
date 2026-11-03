// Design tokens: the numbers and colours the web UI shares, each taken from a node of the game's own prefabs (units are
// the prefab's, 2960 across, unless a name says px). Every value lives here once; call sites import it. This module
// imports nothing, so any UI module (and the tests) can read it. tests/js/ui_tokens.test.mjs pins it against the call
// sites, and tests/test_ui_tokens.py against the prefabs themselves.

// UIPopup_Sort, UIPopup_Option_PC, UIPopup_FilterSort: Center/BlackBg/BlackBgClickArea. The sprite says 0.94, but the
// node carries a TweenAlpha from 0 to 0.80 over 0.2 s, and 0.80 is where it rests.
export const BACKDROP = "#000000/0.80";

// The popup frame, UIPopup_Sort/Center/Popup.
export const POPUP = Object.freeze({
  // Base (y -20) and Base/BG (y +8, Common_Popup_Bg): the frame is centred 12 units under the stage's centre
  baseY: -20,
  bgY: 8,
  frameY: -20 + 8,
  // Base/BG at the prefab's own size
  frameW: 2458,
  frameH: 1355,
  // the tallest frame the app draws (the stage is 1665 units high; the frame's centre is 12 under its middle)
  maxH: 1560,
  // the clear space between the lowest content and the buttons' tops
  clearance: 16,
});

// The footer, UIPopup_Sort/Center/Popup/Btn (y -499) with Btn_Ok (x +292) and Btn_Cancel (x -292).
export const FOOTER = Object.freeze({
  btnY: -499,
  // Btn's centre above the BG's bottom edge: -499 - (-12 - 1355 / 2), in every popup frame whatever its height
  centre: 190.5,
  // Btn_Ok/BG (1) and Btn_Cancel/BG (1), Common_Btn_BG
  body: Object.freeze({ w: 496, h: 177 }),
  // Btn_Ok and Btn_Cancel at +/-292 leave 88 between the two bodies
  pairX: 292,
  pairGap: 88,
  // Label_Locale: fs 72, box 400 wide. A caption wraps to two lines and shrinks to 48; maxH keeps even two lines clear
  // of the body's top and bottom edges. Siblings share one size.
  caption: Object.freeze({ fs: 72, fsMin: 48, w: 400, lines: 2, h: 140 }),
  // three or more choices: 0.8 of the bodies, 24 apart, and the frame keeps 140 across for its margins
  many: Object.freeze({ scale: 0.8, gap: 24, margin: 140 }),
});

// UIPopup_Option_PC/Center/Popup/List/Sound_Setting/ETCSetting/ButtonBg: Common_Btn_BG 402 x 136, Label_Locale fs 48. Only
// the width varies, 240 at least.
export const SMALL_BUTTON = Object.freeze({
  w: 402,
  h: 136,
  minW: 240,
  fs: 48,
  fsMin: 34,
  // the caption is fitted to w - 60, its label box is w - 30 by h - 20
  fitInset: 60,
  boxInsetW: 30,
  boxInsetH: 20,
  // buttons side by side: 20 apart (pitch 422)
  gap: 20,
  pitch: 402 + 20,
  // a line button's width is its caption's width and this much
  glyphPad: 90,
  // the Export pair in Settings
  wideW: 680,
});

// UICharacterDetail/Inventory_ListControl/LeftInfo/CharInfo/Char_Target/Type/MainType/BG: Common_Bg_Raius10px 272 x 72
// inclined 10 degrees, Label fs 40. Chips stand 290 apart.
export const CHIP = Object.freeze({
  w: 272,
  h: 72,
  fs: 40,
  fsMin: 28,
  pitch: 290,
  gap: 290 - 272,
  // the caption is fitted to w - 40
  inset: 40,
});

// Colours, each from the game.
export const COLOUR = Object.freeze({
  // UIPopup_Formation_CharEdit Tag_1_Select/Label: success text, progress fill
  success: "#0a6f2d",
  // UIMailBoxContent Label_Locale_Limit/Label_Limit: warning
  warning: "#f14f00",
  // SkillData's type sprite: error
  error: "#bb3421",
  // Btn_Disable's label: a greyed badge, a greyed navigation entry
  greyed: "#828282",
  // the collection's top bar title: the page's ink, the loading fallback's shadow
  navy: "#2c4564",
  // UICollectCard/Active/BG and the tag ink: the In Use badge
  inUse: "#ffb700",
  inUseInk: "#4b2116",
  // prefab labels: a note's ink, and the consent popup's "never" section bar
  note: "#6a7b8f",
  neverBar: "#6a7b8f",
});

// Settings rows (UIPopup_Option_PC, Game tab): 12 apart
export const SETTINGS = Object.freeze({ rowGap: 12 });

// The game's corner ladder, Common_Bg_Raius5px, _Raius10px and _Raius20px (5, 10 and 22 units), in CSS px at the stage's
// scale (1920 / 2960). The style sheets repeat these, because CSS cannot import; the token tests keep them equal.
export const RADIUS_PX = Object.freeze({ r5: 3.24, r10: 6.49, r22: 14.27 });

// App-added, no game counterpart: the keyboard focus ring, on the radius ladder
export const FOCUS = Object.freeze({ colour: "#2fa6f0", width: 4, offset: 2, radiusPx: RADIUS_PX.r10 });

// The plain fallback button of the no-game screens (the light-blue button, Common_Btn_BG #8de4ff, Label #314665), flat:
// no skew and no shadow
export const FALLBACK_BUTTON = Object.freeze({ fill: "#8de4ff", ink: "#314665", radiusPx: RADIUS_PX.r10 });
