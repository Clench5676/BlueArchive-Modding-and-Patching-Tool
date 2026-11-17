# Controls (`CONTROL`)

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
`FOOTER_DEFAULT` in `popup-footer.js`), its bodies as wide as the longest caption needs (`footerWidth`, 348 at least, 551 at most). The
inline buttons of a plain popup line and the Batch Import tool row are the XS body scaled by its ratio to S. A new button
takes its size from these; it never states a height.

Developer previews (a pre-release version other than a packaged release candidate, `buttonSizeTags`) tag every
`Common_Btn_BG` pill with its preset by its height on the stage; release candidates and stable builds show no tag.
