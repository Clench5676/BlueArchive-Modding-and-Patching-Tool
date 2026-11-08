// Design tokens: the numbers and colours the web UI shares, each taken from a node of the game's own prefabs (units are
// the prefab's, 2960 across, unless a name says px). Every value lives here once; call sites import it. This module
// imports nothing, so any UI module (and the tests) can read it. tests/js/ui_tokens.test.mjs pins it against the call
// sites, and tests/test_ui_tokens.py against the prefabs themselves.

// UIPopup_Sort, UIPopup_Option_PC, UIPopup_FilterSort: Center/BlackBg/BlackBgClickArea. The sprite says 0.94, but the
// node carries a TweenAlpha from 0 to 0.80 over 0.2 s, and 0.80 is where it rests.
export const BACKDROP = "#000000/0.80";

// Old anatomy, retained and pinned for callers awaiting migration: UIPopup_Sort/Center/Popup.
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

// Old anatomy, retained and pinned for callers awaiting migration: UIPopup_Sort/Center/Popup/Btn (y -499) with Btn_Ok (x +292) and Btn_Cancel (x -292).
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
  // how far the footer rose against the 122 the app's own popup frame kept before the footer followed the prefab: frames
  // that held content down to the buttons grow by this
  rise: 190.5 - 122,
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

// Owner-approved wording exceptions, scoped to a single Hall site and locale. The prefab minima above stay unchanged.
export const CAPTION_EXCEPTIONS = Object.freeze({
  "hall.doneAdding": Object.freeze({ "th-TH": 44 }),
  "hall.unsavedChanges": Object.freeze({ "th-TH": 36 }),
  "hall.scaleReset": Object.freeze({ "th-TH": 30 }),
});
export const captionMinimum = (site, locale, minimum) => CAPTION_EXCEPTIONS[site]?.[locale] ?? minimum;

// UICharacterDetail/Inventory_ListControl/LeftInfo/CharInfo/Char_Target/Type/MainType/BG: Common_Bg_Raius10px 272 x 72
// inclined 10 degrees, Label fs 40. Chips stand 290 apart, a second row 84 under the first (the nameplate's rows).
export const CHIP = Object.freeze({
  w: 272,
  h: 72,
  fs: 40,
  fsMin: 28,
  pitch: 290,
  gap: 290 - 272,
  rowPitch: 84,
  // the caption is fitted to w - 40
  inset: 40,
});

// The light stats box, UICharacterDetail/Inventory_ListControl/RightInfo/Target/Active/1_NormalInfo/Status: Bg
// (Common_Bg_Raius10px #ecf5f9), its Deco (Common_Line_Raius10px #c0c9d4) and Bar (Common_Line_Dot #c0c9d4, 4 high). The
// prefab's own rows are 82 high with no padding and its captions 42; the app's rows, captions and paddings below are its own.
export const STATS_BOX = Object.freeze({
  fill: "#ecf5f9",
  line: "#c0c9d4",
  divider: "#c0c9d4",
  // Deco is the fill's size plus 5 across and 5 down (Bg_1: 319 x 164 against 324 x 169, centred to within a unit)
  lineOutset: 5,
  // what a box leaves of the width of what holds it: a popup frame leaves 200 across, a panel 40, and a table leaves the line's
  // outset, so the box's outer edge is the edge of the table under it
  inset: Object.freeze({ frame: 200, panel: 40, table: 5 }),
  // caption 38; value 38 down to 26 on up to 3 lines, wrapped and never cut; a row is 64 high at least
  caption: 38,
  value: Object.freeze({ fs: 38, fsMin: 26, lines: 3 }),
  rowMin: 64,
  // a text row is its lines at 1.25 of the size and this much more, a value label 8 more than its lines
  rowPad: 16,
  linePitch: 1.25,
  labelPad: 8,
  // the box's padding above the first row and under the last, the captions' and the dividers' inset from the box's edges,
  // the gap between the caption column and the value, the slack kept between the text and the label's right edge
  padY: 12,
  padX: 30,
  dividerInset: 30,
  captionGap: 30,
  slack: 20,
  // a smaller line under a value (a mod's original name): size range, label height, the room it adds to the row, how far the
  // value rises and how far the line stands above the row's foot
  secondary: Object.freeze({ fs: 28, fsMin: 24, h: 36, extra: 42, rise: 21, foot: 26, colour: "#2d4664/0.65" }),
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
  // UIPopup_Option_PC SkillCutIn/Type_1/Label_Locale_Info: the red part of an option's note
  noteAlert: "#e06f6f",
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

// UIPopup_Mulligan/Center/Popup/01/UIMulliganSlot/SelectLine, the applied selection UISprite.
export const APPLIED_SELECTION_GOLD = "#ffe900";

// UILobby's legacy Animation clip UIAni_Lobby (on the prefab's root), played as the lobby opens: the top bar
// (Front/NUITop/Top/Right, at rest y 702) drops from 333.7 units above and the bottom menu (Front/Bottom/Menu, at rest
// y -680) rises from 1043 below. Unweighted Hermite keys, [time s, y offset from the rest position, slope].
export const LOBBY_ENTRY = Object.freeze({
  top: Object.freeze([Object.freeze([0, 333.7, 0]), Object.freeze([0.2, 28.55, -271.9047]), Object.freeze([0.42, 0, 0])]),
  bottom: Object.freeze([Object.freeze([0, -1043, 0]), Object.freeze([0.2, -29.52, 281.1431]), Object.freeze([0.42, 0, 0])]),
});

// --- The design system (docs/design-system.md): the scales every composed screen takes its sizes from. Values are the game's,
// from a survey of 41 page prefabs and all 247 popups (the most common value of each role) and named exemplar nodes; the
// layout primitives that use them are ui-grid.js. ---

// Spacing, in units. Where a prefab gives a composition its own spacing, that wins; these are for what the App composes.
export const SPACE = Object.freeze({
  hair: 4,    // an outline's outset, an ink nudge
  xs: 8,      // a label to its own decoration
  s: 12,      // stacked cards (UIPopup_Option_PC rows), a pair of small buttons in a card
  m: 20,      // a list panel's padding (UINonEquipmentInventory Inbox in PaperBG), buttons side by side (SMALL_BUTTON.gap)
  l: 28,      // a control to its label (UIPopup_Sort radio to label, 26), a column of a row to the next
  xl: 40,     // the grid's gutter; a popup's side padding around panels (survey median 41 to 43)
  xxl: 60,    // a card's padding (UIPopup_Option_PC card to its title, 58); the page margin (UICharacterCollection, 66)
  xxxl: 100,  // a popup's side padding around text (UIPopup_Sort 101, UIPopup_TicketUse 102)
});

// Type roles: the size (fs) and the floor a fit may shrink to (min), NGUI font sizes in units.
export const TYPE = Object.freeze({
  display: Object.freeze({ fs: 72, min: 48 }),  // popup titles (135 of 158 titled popups), footer captions
  title: Object.freeze({ fs: 62, min: 48 }),    // a list row's title (UIMailBoxContent/List_01/Label_System)
  heading: Object.freeze({ fs: 52, min: 40 }),  // option labels (UIPopup_Sort radio labels), card titles (50)
  body: Object.freeze({ fs: 48, min: 34 }),     // meta lines (List_01/Label_Locale_Received), small-button captions
  dense: Object.freeze({ fs: 42, min: 42 }),    // the App's dense list rows (import-layout.js ROW_TEXT_SIZE)
  chip: Object.freeze({ fs: 40, min: 28 }),     // chips (CHIP)
  caption: Object.freeze({ fs: 36, min: 28 }),  // descriptions (UIPopup_Option_PC Label_Option_Description)
  badge: Object.freeze({ fs: 34, min: 28 }),    // status badges (List_01/TimeLimit/Label_Locale_TimeLimit)
  micro: Object.freeze({ fs: 28, min: 24 }),    // a secondary line (STATS_BOX.secondary)
});
// Line pitch as a share of the size: NGUI's own (a label's lines) and a stack of separate lines (List_01's meta lines, 66
// apart at 48)
export const LINE = Object.freeze({ label: 1.15, stack: 1.375 });

// Control heights: everything in one row shares one of these, and a control's caption has its size. pad: the clear space a
// caption-sized control keeps on each side of its caption (a button's includes its 30-unit slice border).
export const CONTROL = Object.freeze({
  badge: Object.freeze({ h: 54, fs: 34, pad: 16, minW: 220 }),             // List_01/TimeLimit/Bg 220 x 54
  chip: Object.freeze({ h: 72, fs: 40, pad: 20, minW: 272 }),              // CHIP
  buttonS: Object.freeze({ h: 136, fs: 48, pad: 45, minW: 240 }),          // SMALL_BUTTON (glyphPad 90)
  buttonM: Object.freeze({ h: 162, fs: 60, pad: 45, minW: 348 }),          // List_01/Btn_Receive 348 x 162
  buttonL: Object.freeze({ h: 176, fs: 72, pad: 39.5, minW: 551 }),        // UIPopup_RaidEnterCheck footer, 551 x 176
  icon: Object.freeze({ h: 60 }),                                          // Common_Icon_Close, Common_Btn_Radio_BG
  toolbar: Object.freeze({ h: 104 }), // UILobby/Front/Right/Btn_RotateSpine/ConditionRoot/Able_Count/Bg
});

// The grid inside any container: 12 columns, a gutter of SPACE.xl. A page's container keeps SPACE.xxl from the stage's
// edges: 2840 across, so a column is 200 and its pitch 240.
export const GRID = Object.freeze({ columns: 12, gutter: 40, pageMargin: 60, stageW: 2960, stageH: 1665 });

// Popup frames (Common_Popup_Bg), by width class: S 1300 x 1050 (19 popups; UIPopup_RaidEnterCheck), M 1540 (UIPopup_
// ConquestBattleEnter_Sub), L 2098 x 1110 (UIPopup_Option_PC), XL 2841 x 1353 (UIPopup_GachaCharList). Their anatomy is
// the S class's, the survey's most common: the title's top 36 under the frame's (fs 72), the close icon 48 from the right and
// 39 from the top, content from 170 down, SPACE.xl (panels) or SPACE.xxxl (text) from the sides, the footer's buttonL bodies
// 33 apart and 102 above the frame's bottom, the pair centred (82.5 from each side; the exemplar's are 84 and 81).
export const POPUP_FRAME = Object.freeze({
  frameY: -12, // UIPopup_Sort Base -20 + BG 8
  minH: 786, // UIPopup_ClientVersion Center/Popup/Base/BG 1115 x 786, the smallest titled popup with a footer button
  maxH: 1560,
  S: Object.freeze({ w: 1300, h: 1050 }),
  M: Object.freeze({ w: 1540, h: 1073 }),
  L: Object.freeze({ w: 2098, h: 1110 }),
  XL: Object.freeze({ w: 2841, h: 1353 }),
  titleTop: 36,
  close: Object.freeze({ right: 48, top: 39, size: 60 }),
  contentTop: 170,
  footer: Object.freeze({ gap: 33, bottom: 102, side: 82.5 }),
});

// Panels and cards: a list panel's padding (SPACE.m), a card's (UIPopup_Option_PC: the card's title 58 in, from the card's
// left and 17 from its top), cards stacked SPACE.s apart.
export const PANEL = Object.freeze({ padList: 20, padCard: 58, cardTitleTop: 17, stackGap: 12 });

// A list row (UIMailBoxContent/List_01, 2208 x 329): the icon's circle 304 from 36 in, the text column from 342, the title's
// top 19 down, meta lines 66 apart from 163, the action (buttonM) 31 from the right and 26 from the bottom; a badge sits on
// the centre of the line it qualifies.
export const LIST_ROW = Object.freeze({ w: 2208, h: 329, iconX: 36, icon: 304, textX: 342, titleTop: 19, metaTop: 163,
  metaPitch: 66, actionRight: 31, actionBottom: 26 });
