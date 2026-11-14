# Design system

The web UI's screens are built from the game's own prefabs (NGUI layouts). Where the App composes a screen of its own
(Import Mod's rows, Settings, popups, the tools), it takes every size, gap and alignment from this system instead of
placing widgets by hand. The numbers live in `src/bmpt/web/js/ui-tokens.js`; the layout primitives that use them in
`src/bmpt/web/js/ui-grid.js`. `tests/test_ui_tokens.py` pins the numbers to the prefab nodes they come from in the official
game files; `tests/js/ui_grid.test.mjs` tests the primitives and how the scales hang together.

All values are prefab units: the stage is 2960 units across and 1665 high, drawn at `S = 1920 / 2960` CSS pixels per
unit at a 1920-pixel-wide window.

- [`spacing.md`](spacing.md): Spacing (`SPACE`), Grid (`GRID`), Alignment rules
- [`typography.md`](typography.md): Type (`TYPE`, `LINE`)
- [`controls.md`](controls.md): Controls (`CONTROL`)
- [`containers.md`](containers.md): Popup (`POPUP_FRAME`), Panels and cards (`PANEL`), List rows (`LIST_ROW`)
- [`layout.md`](layout.md): Layout primitives (`ui-grid.js`)
- [`tokens.md`](tokens.md): Other shared tokens (`ui-tokens.js`)
- [`screens/`](screens/README.md): one reference layout per screen

## How the values were found

A survey of 288 prefabs from the official files (41 page prefabs and all 247 popups), each exported with the App's
own reader (`GameAssets.layout`): label sizes, button bodies, popup frames, list grids and gaps between neighbours
counted and measured across all of them, and the anatomy of the 183 popups with a `Common_Popup_Bg` frame compared.
The game has no strict grid, but each role has one dominant value; the system takes those values, each traced to a
named exemplar node.

## Adoption

On the system: every popup (`popup.js`, `popup-frames.js`), Import Mod, Settings, the switch cards, the toolbars of
the Student Hall, its framing, the roster and photography, and the detail page's composed parts. Values that are a
prefab's own stay and say so in the code. Not yet: the stopped memorial candidate/queue/footer buttons, the
photography toolbar and the detail screen's Show in Finder / Explorer button (`retainedButton`, the old `POPUP` /
`FOOTER` tokens); the classic tool pages join the system when they move into the web UI.
