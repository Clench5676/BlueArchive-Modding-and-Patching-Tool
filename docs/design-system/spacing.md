# Spacing, grid and alignment

## Spacing (`SPACE`)

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

## Grid (`GRID`)

Any container's content box holds 12 columns with a 40-unit gutter (`SPACE.xl`): a span of n columns covers the n - 1
gutters between them. A page keeps 60 units (`SPACE.xxl`) from the stage's sides, which makes its content 2840 across: a
column of 200 at a pitch of 240, half a page 1400.

## Alignment rules

1. In a row, every element's visual centre lies on the row's centre line: a widget's box centre, a text's reference glyph
   centre (one centre line per language, whatever the letters; the rule the status badges follow).
2. Elements of one row share a control height; a control's caption has its size.
3. Stacked texts share their left edge, the column's start.
4. The same role has the same size on every screen.
5. A container's content keeps the container's padding on every side; nothing is placed by an offset from a neighbour that
   is not one of the scales.
