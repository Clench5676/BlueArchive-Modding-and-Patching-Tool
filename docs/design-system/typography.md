# Type (`TYPE`, `LINE`)

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
