# 3D tab: the action bar and its action lists

Row 4 of the 3D tab is the action bar: one card per action group the chibi has (Basic, Combat, Move & Cover, Skills,
Status, Random Alts, Other), in a row that scrolls sideways with the strip's edge fade; there is no "+N" pile.
Pressing a card opens its action list upward from it; picking an action plays it and closes the list. A card shows its
group's name centred; the playing action's group card is selected and shows that action's name alone instead.
A chibi without clips of its own (a story chibi, an NPC) shows the default row of actions (`chibi.js` `ACTIONS`, story
clips).

## Action list

The game has no drop-down list; the list is its anchored description tooltip, as the hover hints draw it:

- the box: `UIDescriptionTooltip` `Default/BG` (`Common_Tooltip_Bg`, 62 of room at the sides, 46 above and 60 under the
  content, at most the prefab's 834 wide; without its arrow, as the hover hints), its visible bottom edge 12 over the
  card (`hover-hint.js` `GAP`); the box at 0.95 alpha (the prefab's sprite colour is opaque white), its keys and
  titles opaque;
- the sections: `UIShortcutTooltip` `GetList/TitleBG` title bars (`Common_Bg_Raius5px`, `#334a66`, 60 high, 44 white),
  20 apart, drawn narrower than the content by their 10-degree lean so their tips stay inside the scroll area; a
  group of one section has none, its card names it;
- the actions: `UISortObjectListUnit` radio keys in their `Label_Text` form, one to a line (120 apart), each key's
  underline across the line; the playing one is on.

Placement (`action-list-layout.js` `placeList`): the box centred on the card, shifted only as far as the stage's sides
(less 12) need. As tall as its content, at most up to the top bar (less 12); a taller list scrolls inside the box with
the scroll fade, the playing key in view. The list lies over the bar's clip, so the bar's scroll and fade never cut it;
it follows its card when the bar scrolls and closes once the card leaves the row. A press outside the list closes it,
another group card switches to that card's list, Escape closes it.

Text: a list's keys one size (`fitGroup`, 42 down to 30, 590 wide, two lines within a line); its titles one line; the
box as wide as its widest key or title, each key's label box ending at the content's edge (so the list never scrolls
sideways and its scroll fade never washes out the title bars' ends); the content's height takes in half the last key's
underline, so a list that fits neither scrolls nor fades. The cards: the names are siblings, one size for every card,
the list keys' size (42 down to 28), 182 wide (30 clear at either side of the leaning card), wrapped onto two lines
before they shrink, centred on the card. The playing card: the action's name alone, at the names' size, never larger;
smaller (down to 20, three lines) only when it does not fit there (a second form's Tactical Challenge entry). It is
centred too, and moves down only as far as needed when its first line's ink would reach under the selection check. A
key's label leaves the battle form to its section's title; the card carries it (`Normal Attack (Form 2)`). Geometry and
fit are pure (`action-list-layout.js`) and tested in every language (`tests/js/action_list.test.mjs`). The prefabs are
read and the box, a title bar and a key drawn once off screen when the bar is first drawn, so the first press finds
their sprites ready.

## Groups

| Group card (`chibi.menu.group`) | Sections | Actions (`chibi.action`) and the slots they play |
|---|---|---|
| `basic` | one | `idle` (`Formation_Idle`, else `Cafe_Idle`, else `Idle`), `reaction`, `walk`, `victory` (Start, End), `pickup` |
| `combat` | one | `combatIdle` (`Normal_Idle`), `attack` (`Normal_Attack_` Start, Ing, Delay, End), `reload`, `callsign`, `tacticalEntry` (`Tactical_Start`) |
| `move` | `movement`, `kneel`, `stand` | `run` (`Move_Ing`), `stop` (`Move_End_Normal`), `jump`, `moveCallsign`; `kneelIn` (`Move_End_Kneel`), `kneelIdle`, `kneelAttack` (four clips), `kneelReload`, `kneelCallsign`; the same with `Stand_*` / `Move_End_Stand` |
| `skills` | one | `ex` (`Exs`; numbered `exN` when the chibi has several), `skill` (`Public`, `skillN`) |
| `status` | one | `panic`, `dying` (`Vital_Dying_Ing`), `retreat` (`Vital_Death`, `Vital_Retreat`), `knockdown` (`Vital_Death`, held) |
| `random` | one | the `*_Random` clips: `randomIdle`, `randomVictory`, `randomCallsign`, `randomReload`, `randomKneelReload`, `randomTacticalEntry`, `randomJump` |
| `other` | one | `rearrange` (`Rearrange_Start`, `Rearrange_Idle`), `rearrangeReaction`, `tss` (`TSS_Interaction<n>`, then its `_Idle`) |

A chibi with two or more battle forms gets a section per form after the shared one, titled `chibi.menu.form`
(`Combat (Form 1)`). A group without sections has one, named like the group. Clips outside this table (enemy and event
clips, rare ones such as `Normal02_ReadyStart`, `Formation_FormChange_*`, `Exs02_Vital_Panic`) are not shown, nor are
the actions whose clips the chibi lacks.

## Clip names

`clip-names.js` `parseClip(name, dev)` folds a controller clip name to `{slot, form, index}`:

- only names with the prefab's own prefix (`<dev>_`) count; the shared base controller's clips (`Kneel_Idle`,
  `Exs2`, `PublicSkill01`) are not the character's;
- the form's number may sit on any word: `Normal01_Idle`, `Normal_Idle01`, `Normal_Idle_01`, `Normal_Attack01_Start`,
  `Vital_Dying_ing02`, `Normal_Callsign1`; a number on `Exs`, `Ex`, `Public` or `Interaction` is the skill's;
- a leading `Normal` before a shared slot is dropped (`Normal01_Public01`, `Normal01_Victory_Start`);
- case and misspellings fold: `Move_ing`, `Rnadom`, `Fomation`, `Formation Pickup`, `Callsigh`, `STart`, `Strart`,
  `Nomal`, `Norma_`, `Publlic`;
- anything else (enemy and event clips: `Appear`, `Vital_Groggy`, `Helicopter_*`, `Rhythm_*`) is left out.

One form number alone is no second form (folded to the plain one). The first clip of a slot wins. `actionsOf(names,
dev)` gives the actions in menu order, each `{key, id, group, section, form, index, number, clips}`: `key` unique per
chibi (the id, the skill's number, `@form`: `attack@2`, `ex2`, `skill1@1`; the ids `idle`, `reaction`, `walk`,
`victory`, `pickup`, `retreat`, `knockdown` keep their names), `form` 0 unless the chibi has two or more battle forms,
`number` the skill's number shown (null when the chibi has one of that skill), `clips` the controller clip names it
plays, in order.

## Clips on demand

The chibi's GLB (`/asset/chibi/<alt>.glb?source=...`) carries the default clips (`chibi.py` `DEFAULT_CLIPS`, about
3 MB for Hifumi) and, in its root extras, `clips`: the name of every clip the chibi plays (its animator controllers'
clips, a field prefab's own list), from which the page groups the actions (`actionsOf(clips, extras.dev)`).

The default actions of the first battle form play from the GLB (`chibi.js` `ACTIONS`); any
other plays its `clips` in turn, each once, the last looping or held by its own loop flag (a second form's retreat waits
and fades as the first's). A clip the view does not hold yet is fetched first:

`GET /asset/chibi/<alt>.glb?source=<the shown source>&clip=<name>` -> one clip of that chibi as GLB
(`chibi.py` `export_clip`), read from the same files as the chibi (official copies, a mod, an import, a switched
costume's adapted bundles: whatever the shown chibi was built from), without meshes or textures. Its nodes are the
chibi's hierarchy in the chibi GLB's order (`extras.hierarchy` of them), so each channel's node is the shown chibi's
node of the same index; `extras.clip` names it. Its toggles name the node of the renderer's GameObject: the page
switches the shown chibi's renderer there, or, for a renderer the chibi's GLB leaves out (disabled, or shown only by an
EX skill, e.g. Miyu's gag face), the one the clip's GLB brings with its materials and textures, hidden at rest. A name
the chibi does not play is a 404; a name is only compared with the chibi's clip names (`[A-Za-z0-9_ ]{1,128}`). The
clip is cached by chibi, clip and the stamps of the files read (memory, and the preview cache with
consent). Measured on Hifumi: 0.08 to 0.33 MB, about 0.1 to 0.25 s to export once the chibi's bundles are read, then
from the cache.

The fetch is class 1 (the shown page's preview): the route counts as foreground (`preview_gate.foreground()`), one
export step of about 0.5 s under the chibi lock, never behind a job; the page's deadline (15 s) counts only its own time
in the foreground (`withPreviewTimeout`), not a wait for the library lock. While it loads, the action's group card shows
the game's loading ring in its name's place; leaving the 3D tab, the alt or the screen aborts it with the costume
checks (`detail.js` `stopCostumeChecks`); a step under way on the server finishes and stays cached. A failure leaves the
action that played before and shows "Preview unavailable" (`detail.failed`) for that action only.
