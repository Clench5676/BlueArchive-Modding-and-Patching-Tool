# Enable Mods switch and scans

## Mods state, scan and the classic queue

The services hold what the Enable Mods switch, the prompts and the classic hand-off read (`webui/services.py`):

- **The snapshot** (`ModsSnapshot`). `mods.state` answers from it, never through the library lock: the library's
  `status()` (names and `profiles.json` only, without walking storage), the last scan's entries and a running
  toggle's progress; its lock is a leaf lock. `reason`, first match: `game_not_found`, `profiles_unreadable`, the
  game watch's answer, this instance's classic window. With the scan consent, `pending` and `stuck` count the last
  scan's entries; without it, the `unfinished` list by `why`. `held` always counts the held entries.
- **The library** is built without a current install too, with `scan_allowed` read at each use and `write_check`,
  which refuses every game file write with `classic_open` while a classic window is open.
- **The data directory's id.** `UiState.data_id` keeps `data_id` with the resolved `data_path` in `webui.json`; a
  recorded path that is another folder means a copy when that folder still exists with the same id, a move otherwise.
- **The scanner** (`webui/scanner.py`) runs the scan on a daemon thread of its own, only with the scan consent: one
  scan at a time, requests meanwhile served by one scan after it, `cancel()` on a game folder change or a library
  move. A stale result (the library wrote while the files were read) is scanned again once while no writer job runs.
  Startup prune and scan begin after the first screen reveals, while the startup-check setting is On. With consent,
  scans also run after a game folder change, after every writer job, when the game stops or Steam turns idle, after a
  classic window closed, when consent is granted, on Settings' Check (which lists dismissed entries again) and when
  the page asks (the window back in front after 30 s).
- **Another data directory** sharing the library: the game watch asks `profiles_seen()`, which reads `profiles.json`
  again only when its size or date changed; a change publishes `mods.state`, requests a scan and judges the
  `unfinished` list, so the red dot and Retry follow with or without scan consent.
- **The classic queue.** While a classic window is open, `actions.apply`, `actions.restore` (when it would write),
  `mods.toggle` and `mods.restoreDrift` are refused at submit with `classic_open`. The classic modal queues the
  refused call (`classic.queue`) with its pins and the size and date of its entries' files. Once the classic window
  closed, the page is told to read everything again, a scan runs, then `ClassicQueue.run` submits each call that
  still holds and `classic.queueDone` lists the dropped ones.

## Enable Mods switch

One kill switch for every game folder (`mod-switch.js`; the pure parts tested in Node). It never changes the
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
- **The "?" help**: what On and Off do; the install labels when more than one folder is known; "Not switched: n" with
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
- **What a writer job switched** (`library.changed`): the roster is read again, thumbnails under the changed cores
  are forgotten, and the detail screen reloads the shown kind under a still of the old one, waiting while a popup or
  fullscreen view is open. The screen's reads of `family.get` have no client deadline (a writer job can hold the
  library lock); a read that finishes after the screen was read anew is dropped.
- **While mods are off** an apply only saves the choice (the apply job's `deferred`); the switch popup reads Save.
- **The tray's events** are followed like the page's own toggle (see [Tray](desktop.md#tray)).

## Troubleshoot Mods (pre-release builds)

A Settings row shown and answered only where `prereleasePreviews` is true, to find which mod the game refuses one at
a time (`troubleshoot.js`, `library/troubleshoot.py`): `mods.applyOfficial` turns everything official while saving
the configuration as `profiles.json`'s `troubleshoot`, `mods.restoreConfig` puts it back, `mods.troubleshootState`
says whether one is saved. Both results carry `check`: every bundle Steam's file list names, verified by size and
SHA-1.

## Mods already in use

The prompts and messages around files already modded (`prompts.js`; the pure parts tested in Node). The lists come
from the last scan, so the prompts exist only with the scan consent; the messages of an overwritten entry need none.

- **Mods Not in the Library** (`#prompt`): the `untracked` and configured `changed` entries of every known install,
  every card ticked, each with its own install's picture, the variant's name, kind and install chips and "No Official
  Copy" where it holds. Add to Library (`mods.adopt`), with mods off also Add and Turn On Mods, Later, and Don't Ask
  Again (`mods.dismiss untracked`). Damaged entries are listed apart, dim and never ticked.
- **Costume builds of an older rule** (`update-impact.js gate`, before the first screen): what the App built under an
  older `COSTUME_RULE` is built again silently (`mods.refreshCostumes`).
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
