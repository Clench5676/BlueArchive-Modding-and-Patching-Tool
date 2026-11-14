# Containers

## Popup (`POPUP_FRAME`)

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

## Panels and cards (`PANEL`)

A list panel pads its content by 20 (`SPACE.m`); a card (`Common_Bg_Raius5px`, `UIPopup_Option_PC`) by 58, its title 17
from its top; cards stack 12 apart. Corners follow the game's ladder (`Common_Bg_Raius5px`, `_10px`, `_20px`: 5, 10 and 22
units; `RADIUS_PX`), `Raius10px` being the most used (1153 nodes).

## List rows (`LIST_ROW`)

`UIMailBoxContent/List_01`, 2208 x 329: the icon's circle (304) 36 in, the text column from 342, the title (`TYPE.title`)
19 down, meta lines (`TYPE.body`) 66 apart from 163, the action (`buttonM`) 31 from the right and 26 from the bottom. A badge
sits on the centre of the line it qualifies.
