# Other shared tokens (`ui-tokens.js`)

Beyond the scales, `ui-tokens.js` holds the remaining numbers and colours the web UI shares, each named after the
prefab node it comes from (`tests/test_ui_tokens.py` pins them to the development view):

- **`BACKDROP`**: every popup's backdrop, black at 0.80 — the value `BlackBgClickArea`'s TweenAlpha rests at in the
  game's popups (shown at once, without the fade).
- **`MEMORIAL_LOBBY_ENTRY`**: the top bar's and bottom menu's entry motion, UILobby's `UIAni_Lobby` Hermite keys (see
  [`webui/front-end.md`](../webui/front-end.md#bar-motion), Bar motion).
- **`XS_BUTTON`** / **`SMALL_BUTTON`**: the app's small button (308 x 104, caption 40 down to 28) and the S popup
  footers' 402 x 136 body; `game-buttons.js optionButton` and the Student Hall's `button()` take the XS values as defaults.
- **`CAPTION_EXCEPTIONS`** (checked by `captionMinimum`): the few recorded Thai-only caption floors; an allowance
  only lowers a floor, and no other group gets an exception.
- **`CHIP`** (`UICharacterDetail` type chip): 272 x 72 inclined 10 degrees, caption 40 down to 28, 290 apart; drawn
  by the nameplate's alt chips and the list popup's details panel.
- **`STATS_BOX`** (`UICharacterDetail`'s light stats box: `Bg` `#ecf5f9`, `Deco` line `#c0c9d4`): drawn by
  `statsBox()` in `web/js/ui/stats-box.js` for details panels, file details, messages with a line button and the update
  dialog; captions 38, values 38 down to 26, rows 64 high at least, dotted dividers between rows only.
- **`COLOUR`**, each from the game: success `#0a6f2d`, warning `#f14f00`, error `#bb3421`, greyed `#828282`, the
  page's ink `#2c4564`, the In Use badge `#ffb700` on `#4b2116`, a note's ink `#6a7b8f`. The style sheets repeat the
  CSS ones, which the token tests keep equal.
- **`APPLIED_SELECTION_GOLD`**: the Student Hall's applied-frame yellow `#ffe900` (`UIPopup_Mulligan`'s `SelectLine`).
- **Corners and focus ring** (style sheets): the game's radius ladder (5, 10 and 22 units, `RADIUS_PX`); one focus
  rule in `app.css` draws the ring for every control, 4 px `#2fa6f0`.
