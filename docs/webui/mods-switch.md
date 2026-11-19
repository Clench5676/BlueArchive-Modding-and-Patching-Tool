# Enable Mods switch and scans

## Mods state, scan and the classic queue

The services hold what the Enable Mods switch, the prompts and the classic hand-off read (`webui/services.py` and its mixins):

- **The snapshot** (`ModsSnapshot`). `mods.state` answers from it, never through the library lock: the library's
  `status()` (names and `profiles.json` only, without walking storage), the last scan's entries and a running
  toggle's progress; its lock is a leaf lock. `reason`, first match: `game_not_found`, `mod_configuration_unreadable`, the
  game watch's answer, this instance's classic window. With the scan consent, `pending` and `stuck` count the last
  scan's entries; without it, the `unfinished` list by `why`. `held` always counts the held entries.
- **The library** is built without a current install too, with `scan_allowed` read at each use and `write_check`,
  which refuses every game file write with `classic_open` while a classic window is open.
- **The data directory's id.** `UiState.data_id` keeps `data_id` with the resolved `data_path` in `webui.json`; a
  recorded path that is another folder means a copy when that folder still exists with the same id, a move otherwise.
- **The scanner** (`webui/scanner.py`) runs the scan on a daemon thread of its own, only with the scan consent: one
  scan at a time, requests meanwhile served by one scan after it, `cancel()` on a game folder change or a library
  move. A stale result (the library wrote while the files were read) is scanned again once while no writer job runs.
  Startup maintenance waits for the first screen. The scan requires consent. With consent,
  scans also run after a game folder change, after every writer job, when the game stops or Steam turns idle, after a
  classic window closed, when consent is granted, on Settings' Check (which lists dismissed entries again) and when
  the page asks (the window back in front after 30 s).
- **Shared libraries.** Data directories use the same committed library under its session lock. The UI refreshes
  its snapshot when the profile signature changes.
- **The classic queue.** While a classic window is open, `actions.apply`, `actions.restore` (when it would write),
  `mods.toggle` and `mods.restoreDrift` are refused at submit with `classic_open`. The classic modal queues the
  refused call (`classic.queue`) with its pins and the size and date of its entries' files. Once the classic window
  closed, the page is told to read everything again, a scan runs, then `ClassicQueue.run` submits each call that
  still holds and `classic.queueDone` lists the dropped ones.

## Enable Mods switch

One kill switch for every game folder (`mod-switch.js`). It never changes the
configuration: Off puts every entry of every known install back to official, On writes the configured sources again
(see [library](library.md#library)). It is in the top bar of the student list and the detail screen, and in Settings' Mods tab.

- **The top bar**: the switch (two filter segments from the game's sprites, since the atlases have no two-state
  switch), its label and the square "?" (the game's info button). It is a `button` with `role="switch"`.
- **States** (`view(mods.state)`): on; off; busy (dimmed) while a toggle runs or writes wait for the classic window;
  unavailable, greyed with the reason as its accessible description; the red dot while entries were not switched.
- **A click** (`click(from, want)`): on the current state nothing; busy, a notice or the classic modal; unavailable,
  the reason as a notice; the first toggle ever, the explanation (shown once, `mods.explained`); otherwise the
  confirmation, then `mods.toggle {enabled, expect}` with the corner indicator and "Working 12/40". The end is
  silent, or a result popup when entries were adopted first, failed or were left as is. `state_changed` is a notice.
- **The "?" help**: what On and Off do; one line per game folder with its switch method ("Switch method: Catalog" or
  "Switch method: File Replacement", the folder's label first when more than one is known); "Not switched: n" with
  Retry; "Left as is, no official copy: n" with Details (the Overwritten message and repair lines); held entries with
  Details and Go To; "Gone from the library: n".
- **The scan consent popup** (`consent.js scan`, built like the asset consent): what the file check reads and will
  not do; must be answered; declining does not quit. A failed answer shows its error with the popup still up.
- **Settings' Mods tab**: Enable Mods (the same switch), Mod Cache (size on demand, Clear), Tray Icon, the startup
  check radio row (On opens the consent first; Off withdraws consent; `scan_consent` is the sole stored preference)
  and Configuration Drift Check with its Check button (without consent it opens the consent popup first).
- **The classic modal** (`classicModal`): a write the open classic window refuses opens "Classic UI Open" with the
  box "Apply automatically once it closes" (queued through `classic.queue`), Bring to Front and Confirm. When the
  queue has run, one popup lists the dropped calls.
- **What a writer job switched** (`library.changed`): the student list is read again, thumbnails under the changed cores
  are forgotten, and the detail screen reloads the current resource type under a still of the scene, waiting while a popup or
  fullscreen view is open. The screen's reads of `student.get` have no client deadline (a writer job can hold the
  library lock); a read that finishes after the screen was read anew is dropped. The Student Hall shows library
  mods and official copies, never the game files as they are: a change of game files or of the switch leaves it
  standing and only reads its list of choices again; it is drawn again when the mod list changed.
- **While mods are off** an apply only saves the choice (the apply job's `deferred`); the switch popup reads Save.
- **The tray's events** are followed like the page's own toggle (see [Tray](desktop.md#tray)).
- **Catalog notices** (`ModSwitch.tellCatalog`, with the start popups and later whenever no popup is open): one
  notice popup lists "Game catalog restored to official" for each folder the start put back (the start job's
  `notices`), and "Catalog switching unavailable for this game version. File replacement in use." for each folder
  whose `methodNotice` is set, then records it (`mods.methodNoticeSeen`). Moving a folder to the catalog is silent:
  only the start cover's progress line shows it.

## Switch methods

Each game folder switches mods one of two ways (`mods.state.installs[].method`); the App picks it, there is no
setting.

- **Catalog** (`catalog`): a Global install with the PC layout (`PUB/Resource/Catalog/Windows/catalog_Windows.bytes`
  below the game folder and `PUB/Resource/GameData/Windows` among its resource folders). Mod files are placed beside
  the official ones in a `ModBundles` subfolder, each named after the SHA-1 of its bytes, and the App generates a
  catalog that points the modded parts at them. A switch is one swap of the live catalog: a verified copy beside it,
  then a rename. Off swaps the official catalog back and leaves the placed files, so the next On is one swap again.
  An entry whose placed files are there as recorded for its configured source, and whose official-named files are
  official, is switched by the swap alone: nothing of it is read again, and a placed file that is the mod's own file
  stays valid across program versions. Only entries that changed since (the mod's files, the configured source, a
  placed file, an official-named file) go through the per-entry steps. An Off makes no pass over the game files
  first: files that are neither official nor a library source are found by the scan that follows the toggle and
  offered by "Mods Not in the Library"; until then such a file stays as it is.
  A rebuilt output (a mod adapted to another character, a costume build) records a build key of two parts: a
  digest of what it was made from (the bytes of its inputs, the official bytes of its targets, the options) and
  what built it (a fingerprint of the builder code and of the packages that shape the bytes,
  `catalog/data/builder_fingerprints.json`, written by `scripts/build_builder_fingerprints.py`; the costume rule
  for a costume). A toggle reuses an output while the first part holds. An output of another builder stays in use
  and is built again by the start job behind the start cover (`refresh_builds`), never inside a toggle. That
  refresh takes no kept copy another builder made: it builds the output and drops the copy.
  Apply and restore of one entry place (or reuse) its file, generate a catalog and swap. Official files are never
  overwritten. A core whose files the catalog does not name, or that lie outside `GameData/Windows`, is switched by
  file replacement in the same folder.
- **File replacement** (`files`): Japan installs, other layouts, and Global installs whose catalog the checks
  refused. Mod bytes are written over the official files, which the library keeps as originals.

The swap runs inside the writer jobs (toggle, apply, restore, Apply Mod Configuration, Retry) with the same checks
as a file write (game closed, Steam idle, no classic window); the generation and the swap run outside the library
lock, which is taken only to save the store.

- **Moving to the catalog.** At the first start that finds a capable install with mods on, behind the start cover:
  the official catalog is kept, the bytes of every switched entry are placed, the generated catalog is swapped in,
  then the official files are put back entry by entry. An entry without an official copy keeps its bytes under the
  official name. An interruption repeats from the store: placing is idempotent and the swap is journaled. With mods
  off only the method is recorded. The bytes placed are the ones the game already loads, so their contents are not
  read again (hash and copy only). The kept official copies this makes unnecessary are released afterwards by the
  maintenance prune, never by a start job: a prune pass asks each mod for a missing fingerprint once.
- **Game updates.** Steam replaces the live catalog, so mods are off in game until the App acts. The new catalog is
  accepted as official (checked against Steam's file list with the scan consent, else it must be none of the App's
  generated catalogs and pass the format checks) and kept under the new resource version. A redirect whose official
  bundle is gone from the new catalog is dropped; its mod is listed in Mods to Update ("game file renamed or
  removed", `targetRetired`) and updated with the usual update action. Once the update check is answered and mods
  are on, a catalog for the new version is generated and swapped in. A Steam file check that restores the official
  catalog makes the redirected entries `reset`; Apply Mod Configuration is one swap.
- **Failures.** Nothing reaches the live path without passing the generator's checks and a SHA-1 check of the
  temporary copy. A failed swap leaves the previous catalog live and reports the job's error (`catalog_changed`
  when the live catalog is neither the recorded official nor the recorded generated one). If the generator's checks
  refuse a version (`catalog_unsupported`, `catalog_invalid`), the folder uses file replacement for that resource
  version and the user is told once. At start, a live catalog recorded as the App's that matches no recorded catalog
  or does not parse is swapped back to the official copy and the user is told (`catalog_restored`).

## Troubleshoot Mods (pre-release builds)

A Settings row shown and answered only where `prereleasePreviews` is true, to find which mod the game refuses one at
a time (`troubleshoot.js`, `library/troubleshoot.py`): `mods.applyOfficial` turns everything official while saving
the configuration as `profiles.json`'s `troubleshoot`, `mods.restoreConfig` puts it back, `mods.troubleshootState`
says whether one is saved. Both results carry `check`: every bundle Steam's file list names, verified by size and
SHA-1.

## Mods already in use

The prompts and messages around files already modded (`prompts.js`). The lists come
from the last scan, so the prompts exist only with the scan consent; the messages of an overwritten entry need none.

- **Mods Not in the Library** (`#prompt`): the `untracked` and configured `changed` entries of every known install,
  every card ticked, each with its own install's picture, the alt's name, kind and install chips and "No Official
  Copy" where it holds. Add to Library (`mods.adopt`), with mods off also Add and Turn On Mods, Later, and Don't Ask
  Again (`mods.dismiss untracked`). Damaged entries are listed apart, dim and never ticked.
- **Costume builds of an older rule** (`update-impact.js gate`, before the first screen): what the App built under an
  older `COSTUME_RULE` is built again silently (`mods.refreshCostumes`).
- **Failed writes after an App update** (`update-impact.js gate`, before the first screen): with mods on, the
  configured entries whose last write failed (the error kept on the choice) are written again once under each new build
  of the App, silently (`mods.updateDue`'s `retry`, `mods.retryFailed`, an On's Retry of those entries alone): one
  written, or already holding its source, loses its error; one that fails keeps a fresh one. While the Game Updated
  question is due they wait for its Check; a refusal of the moment leaves them for the next start.
- **Game Updated** (before the first screen; also after `config.changed`): once the resource version differs from the
  last seen and the library has mods (`mods.updateDue`), "Check your mods against this update?" with Later and Check.
  Check runs under the loading screen: `mods.impact` classifies the mods, a new scan finds entries the update
  overwrote, and those that need no repair are applied again (`mods.restoreDrift`).
- **Mods to Update** (the list popup): only the mods to update (rig changed, no repaired copy), ticked, with the
  Outdated badge. The details give what the update changed, with Go To, Preview Update (the repair review through
  `mod-compatibility.js`) and Update and Apply; footer Close, Check Update Impact (asks first when no game update is
  recorded) and Update and Apply for the ticked. Closing records the version as seen (`mods.impactSeen`). Settings'
  View Mods to Update opens it from the last check without checking.
- **New Game Version** (`confirmUntested`): a write refused with `untested_build` lists the affected entries, Apply
  Anyway (`mods.confirmUntested`, then the same call again) and Cancel.
- **A game update's drift** (`prompts.js reconcile`): with mods on and the file check allowed, `updated` entries that
  need no repair are applied again by themselves; those whose mod's rig changed stay held; while the update check is
  due, its entries wait for Check.
- **Mods Back to Official** (while mods are on): the `reset` entries (a Steam check or repair), ticked except those
  whose mod a game update changed (Outdated badge, unticked). Exactly two buttons and no other way out: Apply Mod
  Configuration (`mods.restoreDrift`) and Keep as Is (`mods.dismiss drift`). Entries still overwritten are listed
  apart with the repair lines.
- **When they show:** at the start; after a later scan once no other popup is open, when it found entries not shown
  in that state; after Settings' Check; from the tray's Back to Official. Never while the game runs, Steam is busy or
  the classic UI is open.
- **Add First** (an overwritten entry whose mod is not in the library): offers only Add to Library — a repair would
  replace the only copy of the mod. **Overwritten** (once its mod is in the library): "No official copy left" and the
  repair lines of its install (`repairs.js`: Steam's verify, then the game's own Restore Resources as another option,
  never promised). The app never starts a repair.
