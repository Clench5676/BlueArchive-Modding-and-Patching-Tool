# Design system

The web UI's screens are built from the game's own prefabs (NGUI layouts). Where the App composes a screen of its own
(Import Mod's rows, Settings, popups, the tools), it takes every size, gap and alignment from this system instead of
placing widgets by hand. The numbers live in `src/bmpt/web/js/ui-tokens.js`; the layout primitives that use them in
`src/bmpt/web/js/ui-grid.js`. `tests/test_ui_tokens.py` pins the numbers to the prefab nodes they come from in the official
game files; `tests/js/ui_grid.test.mjs` tests the primitives and how the scales hang together.

All values are prefab units: the stage is 2960 units across and 1665 high, drawn at `S = 1920 / 2960` CSS pixels per
unit at a 1920-pixel-wide window.

## How the values were found

A survey of 288 prefabs from the official files (41 page prefabs and all 247 popups), each exported with the App's
own reader (`GameAssets.layout`): label sizes, button bodies, popup frames, list grids and gaps between neighbours
counted and measured across all of them, and the anatomy of the 183 popups with a `Common_Popup_Bg` frame compared.
The game has no strict grid, but each role has one dominant value; the system takes those values, each traced to a
named exemplar node.

## Scales

### Spacing (`SPACE`)

| Token | Units | From |
|---|---|---|
| `hair` | 4 | an outline's outset, an ink nudge |
| `xs` | 8 | a label to its own decoration |
| `s` | 12 | stacked cards (`UIPopup_Option_PC` rows), a pair of small buttons in a card |
| `m` | 20 | a list panel's padding (`UINonEquipmentInventory` Inbox in PaperBG), buttons side by side |
| `l` | 28 | a control to its label (`UIPopup_Sort` radio to label, 26), one column of a row to the next |
| `xl` | 40 | the grid's gutter; a popup's side padding round panels (survey median 41 to 43) |
| `xxl` | 60 | a card's padding (`UIPopup_Option_PC`, 58); the page margin (`UICharacterCollection`, 66) |
| `xxxl` | 100 | a popup's side padding round text (`UIPopup_Sort` 101, `UIPopup_TicketUse` 102) |

A prefab's own spacing wins over the scale; the scale is for what the App composes.

### Type (`TYPE`, `LINE`)

| Role | Size | Floor | From |
|---|---|---|---|
| `display` | 72 | 48 | popup titles, popup footer captions |
| `title` | 62 | 48 | a list row's title (`UIMailBoxContent/List_01/Label_System`) |
| `heading` | 52 | 40 | option labels (`UIPopup_Sort`), card titles (50) |
| `body` | 48 | 34 | meta lines (`List_01/Label_Locale_Received`), small-button captions |
| `dense` | 42 | 42 | the App's dense list rows (Import Mod) |
| `chip` | 40 | 28 | chips (`UICharacterDetail` type chip) |
| `caption` | 36 | 28 | descriptions (`UIPopup_Option_PC` `Label_Locale_Info`) |
| `badge` | 34 | 28 | status badges (`List_01/TimeLimit`) |
| `micro` | 28 | 24 | secondary lines |

A text shrinks from its size towards its floor only after wrapping. Lines of one label
are NGUI's 1.15 apart (`LINE.label`); separate lines stacked as a list 1.375 (`LINE.stack`, `List_01`'s meta lines: 66 at 48).

### Controls (`CONTROL`)

Everything in one row shares one control height, and a control's caption has the control's size.

| Control | Height | Caption | Padding each side | Minimum width | From |
|---|---|---|---|---|---|
| `badge` | 54 | 34 | 16 | 220 | `List_01/TimeLimit/Bg` |
| `chip` | 72 | 40 | 20 | 272 | `UICharacterDetail` type chip |
| `buttonXXS` | 88 | 34 | 26 | 140 | `buttonXS` at 0.85: a details panel's actions when their row of XS buttons would not fit |
| `buttonXS` | 104 | 40 | 34 | 200 | the app's small button: the S button at 0.765 of its height, the game's toolbar and tab height |
| `buttonS` | 136 | 48 | 45 | 240 | `UIPopup_Option_PC` small button |
| `buttonM` | 162 | 60 | 45 | 348 | `List_01/Btn_Receive` |
| `buttonL` | 176 | 72 | 39.5 | 551 | `UIPopup_RaidEnterCheck` footer |
| `icon` | 60 | | | | close icon, radio |

A button's padding includes its body's 30-unit slice border (the rim and corner). Buttons of one group share one width
(the widest caption's) and one caption size.

Every button went one size down. The app's small buttons (Settings rows, the import page's row, a details panel's
actions, the Hall's and the photography page's) are `buttonXS` (`XS_BUTTON`); `SMALL_BUTTON` (402 x 136) stays for the
popups whose footers are set to S. A popup's footer is M unless its call site names a size (`footer: "S"`, `"M"`, `"L"`;
`FOOTER_DEFAULT` in `popup.js`), its bodies as wide as the longest caption needs (`footerWidth`, 348 at least, 551 at most). The
inline buttons of a plain popup line and the Batch Import tool row are the XS body scaled by its ratio to S. A new button
takes its size from these; it never states a height.

Developer previews (a pre-release version other than a packaged release candidate, `buttonSizeTags`) tag every
`Common_Btn_BG` pill with its preset by its height on the stage; release candidates and stable builds show no tag.

### Grid (`GRID`)

Any container's content box holds 12 columns with a 40-unit gutter (`SPACE.xl`): a span of n columns covers the n - 1
gutters between them. A page keeps 60 units (`SPACE.xxl`) from the stage's sides, which makes its content 2840 across: a
column of 200 at a pitch of 240, half a page 1400.

## Containers

### Popup (`POPUP_FRAME`)

`Common_Popup_Bg` frames come in four width classes:

| Class | Exemplar size | Exemplar | Survey |
|---|---|---|---|
| S | 1300 x 1050 | `UIPopup_RaidEnterCheck` | 19 popups at 1300 |
| M | 1540 x 1073 | `UIPopup_ConquestBattleEnter_Sub` | 22 popups between 1500 and 1620 |
| L | 2098 x 1110 | `UIPopup_Option_PC` | 8 popups at about 2100 |
| XL | 2841 x 1353 | `UIPopup_GachaCharList` | 9 popups at about 2840 |

Their anatomy is the S class's, the survey's most common: the title (`TYPE.display`) 36 under the frame's top, the close
icon (60) 48 from the right and 39 from the top, content from 170 down and 40 (panels) or 100 (text) from the sides, the
footer's `buttonL` bodies 33 apart and 102 above the frame's bottom, the pair centred. The class sets the width; explicit
heights fit the content above `minH` 786 (`UIPopup_ClientVersion`, `Center/Popup/Base/BG`, 1115 x 786), capped at 1560.
The exemplar heights remain defaults for scrolling viewers that omit a height; scrolling lists explicitly keep at least
their class height. A body that fits is centred between the title clearance and the footer (or bottom padding
without buttons), including a message's check row as one block, even where scrolling is allowed; only an overflowing
body fills the content box and scrolls. Frames stay centred 12 below the stage centre.

### Panels and cards (`PANEL`)

A list panel pads its content by 20 (`SPACE.m`); a card (`Common_Bg_Raius5px`, `UIPopup_Option_PC`) by 58, its title 17
from its top; cards stack 12 apart. Corners follow the game's ladder (`Common_Bg_Raius5px`, `_10px`, `_20px`: 5, 10 and 22
units; `RADIUS_PX`), `Raius10px` being the most used (1153 nodes).

### List rows (`LIST_ROW`)

`UIMailBoxContent/List_01`, 2208 x 329: the icon's circle (304) 36 in, the text column from 342, the title (`TYPE.title`)
19 down, meta lines (`TYPE.body`) 66 apart from 163, the action (`buttonM`) 31 from the right and 26 from the bottom. A badge
sits on the centre of the line it qualifies.

## Alignment rules

1. In a row, every element's visual centre lies on the row's centre line: a widget's box centre, a text's reference glyph
   centre (one centre line per language, whatever the letters; the rule the status badges follow).
2. Elements of one row share a control height; a control's caption has its size.
3. Stacked texts share their left edge, the column's start.
4. The same role has the same size on every screen.
5. A container's content keeps the container's padding on every side; nothing is placed by an offset from a neighbour that
   is not one of the scales.

## Layout primitives (`ui-grid.js`)

Boxes are `{ x, y, w, h }` in a container's own frame (x right, y down from its top left).

- `container({ x, y, w, h, padding })`: the outer box and the content box inside the padding (`inset` for any box).
- `grid(box)`: `span(n, offset)` and `at(n)` of the 12-column grid.
- `row(items, { x, y, w, h, gap, align, justify })`: items side by side; `align` centre (default, rule 1), start or end;
  `justify` start, end, centre or between; items with `grow` share the width left over.
- `stack(items, { x, y, w, gap, align })`: items one under the other; `align` start, centre, end or stretch.
- `textItem(ui, text, TYPE.role, { maxW, lines })`: a fitted text with its box, ink band and reference centre.
- `controlWidth(CONTROL.kind, caption, measure)`: a caption-sized control's width.
- `toNode(box, pivot, frame)`: the NGUI position of a widget with that pivot filling the box, for a prefab clone.

A screen measures its parts (texts with `textItem`, controls from `CONTROL`), lays them out with `row`, `stack` and
`grid` inside a `container`, and moves each prefab clone to `toNode` of its box. Its tests then check the rules on the
boxes instead of pixel offsets.

## Other shared tokens (`ui-tokens.js`)

Beyond the scales, `ui-tokens.js` holds the remaining numbers and colours the web UI shares, each named after the
prefab node it comes from (`tests/test_ui_tokens.py` pins them to the development view):

- **`BACKDROP`**: every popup's backdrop, black at 0.80 — the value `BlackBgClickArea`'s TweenAlpha rests at in the
  game's popups (shown at once, without the fade).
- **`LOBBY_ENTRY`**: the top bar's and bottom menu's entry motion, UILobby's `UIAni_Lobby` Hermite keys (see
  web-ui.md, Bar motion).
- **`XS_BUTTON`** / **`SMALL_BUTTON`**: the app's small button (308 x 104, caption 40 down to 28) and the S popup
  footers' 402 x 136 body; `game-buttons.js optionButton` and the Hall's `button()` take the XS values as defaults.
- **`CAPTION_EXCEPTIONS`** (checked by `captionMinimum`): the few recorded Thai-only caption floors; an allowance
  only lowers a floor, and no other group gets an exception.
- **`CHIP`** (`UICharacterDetail` type chip): 272 x 72 inclined 10 degrees, caption 40 down to 28, 290 apart; drawn
  by the nameplate's variant chips and the list popup's details panel.
- **`STATS_BOX`** (`UICharacterDetail`'s light stats box: `Bg` `#ecf5f9`, `Deco` line `#c0c9d4`): drawn by
  `statsBox()` in `web/js/stats-box.js` for details panels, file details, messages with a line button and the update
  dialog; captions 38, values 38 down to 26, rows 64 high at least, dotted dividers between rows only.
- **`COLOUR`**, each from the game: success `#0a6f2d`, warning `#f14f00`, error `#bb3421`, greyed `#828282`, the
  page's ink `#2c4564`, the In Use badge `#ffb700` on `#4b2116`, a note's ink `#6a7b8f`. The style sheets repeat the
  CSS ones, which the token tests keep equal.
- **`APPLIED_SELECTION_GOLD`**: the Hall's applied-frame yellow `#ffe900` (`UIPopup_Mulligan`'s `SelectLine`).
- **Corners and focus ring** (style sheets): the game's radius ladder (5, 10 and 22 units, `RADIUS_PX`); one focus
  rule in `app.css` draws the ring for every control, 4 px `#2fa6f0`.

## Adoption

On the system: every popup (`popup.js`, `popup-frames.js`), Import Mod, Settings, the switch cards, the toolbars of
the Student Hall, its framing, the roster and photography, and the detail page's composed parts. Values that are a
prefab's own stay and say so in the code. Not yet: the stopped memorial candidate/queue/footer buttons, the
photography toolbar and the detail screen's Show in Finder / Explorer button (`retainedButton`, the old `POPUP` /
`FOOTER` tokens); the classic tool pages join the system when they move into the web UI.
