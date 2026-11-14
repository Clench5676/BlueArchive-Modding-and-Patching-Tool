# Mod library

## library

```python
class ModLibrary:
    def __init__(self, root, catalog, game, settings, *, scan_allowed=..., data_id="local",
                 write_check=None): ...                # write_check: the caller's refusal right before game writes
    def mods(self) -> list[ModEntry]               # id, file, label, target (variant key), kind, added, fresh,
                                                   # size, date, crc, core
    def add(self, paths, ...) -> list[ModEntry]    # copies into the library; target from the file name's core;
                                                   # one mod per core and kind, or per model slot (Import grouping)
    def remove(self, mod_id, confirm=False)        # refused while a game folder shows it (below)
    def rename(self, mod_id, label); def mark_seen(self, mod_id)
    def favorites(self) -> set[str]; def set_favorite(self, variant_key, on)
    def state(self, variant_key, pin=None) -> VariantState   # game files, applied, crc, copy, restorable, official
    def configured(self, variant_key, pin=None)    # {"spr": source | None, "lobby": source | None}
    def modified(self, pin=None) -> dict[str, str] # the roster's Replaced flags
    def apply(self, variant_key, source, log, progress, kind="spr", pin=None) -> ApplyResult   # also deferred
    def restore(self, variant_key, log, kind="spr", pin=None) -> ApplyResult
    def preview_bundles(self, mod_id, pin=None); def preview_complements(self, core, names, pin=None)
    def preview_incomplete(self, mod_id, pin=None) -> bool
    def model_cores(self, variant_key, pin=None) -> list[str]  # prefab first, own installed dependency cores
    def official_bundles(self, core, pin=None, *, current_fallback=False) -> list[Path]  # verified originals
    def preview_reads(self, policy, pin=None) -> PreviewReads  # "official" | "configured"
    def prune(self) -> list[Path]                  # kept originals deleted (below)
    def update(self, mod_id, log, progress) -> UpdateResult    # rebuild against the current game file
    def extract(self, target, out_dir, log); def compare(self, mod_id) -> CompareResult
    # the Enable Mods switch and mods already in use, over every known game folder
    def installs(self) -> list[Pin]; def current_pin(self) -> Pin | None
    def toggle(self, enabled, expect, pins=None, log, progress, cancel) -> ToggleResult   # also Retry
    def restore_drift(self, entries, pins=None, ...) -> ToggleResult    # Apply Mod Configuration
    def scan(self, pins=None, cancel=None, include_dismissed=False) -> ScanResult   # needs the scan consent
    def entries(self, pins=None, include_dismissed=False) -> list[EntryState]
    def adopt(self, entries, pins=None, ...) -> AdoptResult             # Add to Library
    def dismiss(self, kind, entries); def forget(self, install); def clear_built(self) -> int
    def judge_unfinished(self) -> int; def intent(self) -> dict; def status(self) -> dict
```

**Switching** is what the detail screen's switch cards do: `apply` writes the chosen source into the variant's game
files of that kind. The sources: the official files (`official`, the same as `restore`), a mod (`mod:<id>`; one made
for another variant of the family is adapted), or another variant's official files (`variant:<key>`, adapted). A mod
replaces every part of its kind as a unit: parts it has no file for go back to official. The portrait and the lobby
are switched apart; a lobby mod belongs to one lobby core.

**Game folders.** Every call that reads or writes game files works on a `Pin` (an install key, its resource folders
and its server), taken from the settings at the call or given by the caller (the web UI's jobs pin what they saw at
submit and are refused with `folder_changed` when the folders changed meanwhile). The known installs are the Global
and Japan folders the settings name, then every install with a configuration record whose folder exists; without a
current install the switch still works over the others.

What a switch replaces is always kept:

- **An official file** is copied to `originals/<file name>` the first time it is replaced. The official copy is, in
  order: the original already kept, the game file itself, its `.backup` beside it, or a file of its name in another
  game folder — each checked at write time by the CRC32 of its whole content against the CRC in its name, so a modded
  file is never kept as official. Restoring requires a verified official copy; without one it leaves the Mod in place
  (`original_unavailable`).
- **A mod** stays in the library (`mods/<id>/`). While mods are on, `remove` refuses one a game folder still shows
  (`mod_in_use`, or `mod_in_use_elsewhere` with the folder). While mods are off removing is allowed: every
  configuration naming it is dropped, and when a game file still carries it with no official copy anywhere,
  `mod_last_copy` asks first; those files are marked removed so nothing adopts them again.
- **A file changed outside the library** (another tool, the classic GUI, a manual copy) is added to the library as a
  new mod of its core before it is overwritten, judged in full (SHA-1 against the library's record, then CRC32),
  so a mod installed without the CRC fix or with the date put back is kept too. A successful apply reports
  `kept_current`; a failed write rolls back to the outside bytes from its journal.

A partial Mod takes omitted parts from verified official copies when available, otherwise from the current game files
(reported `incomplete`; shown as Mod incomplete). Official previews and restore never use that fallback.

Every write goes through `_commit`: resource path checks, the game not running and Steam not busy, the files
lockable, atomic replacement, and a rollback of every written part when one fails. The records of `state.json` say
for each file what was written there: the source, its SHA-1, size and date, whether the bytes are official, the write
time, trailing bytes and the CRC state. Older builds keep these keys in records they do not rewrite.

**Kept originals and the prune.** After every switch, at the end of every bulk job, and once per library
(`library.prune`), `prune` decides about every kept original from names, file stats and records only; it reads no
game file's contents. An original stays while a configured entry may write non-official bytes over its file, while
any known install holds a file of its name that is not provably official since the library last wrote official bytes
there, or while a file of its name the library wrote to elsewhere may need it. Once its name is gone from every known
install and each of its records' folders holds a later-dated file of the same part, a game update superseded it and
it is deleted; when unsure, an original stays (the cost is disk space, counted in the switch's help).

**Configuration (`profiles.json`).** Older builds rewrite `state.json` with their own keys only, so the configuration
lives beside it: the Enable Mods switch intent (`enabled`, one for every known install; missing means on), its last
change, the entries not switched yet (`unfinished`: `pending`, `left`, `stuck` or `held`), and per install key its
server, the data directories naming or hiding it, the configured source per core, the `updated` markers, the built
copies, the dismissed prompts and the last resource version whose game update impact was told. Every change is
applied to the file as it is, under `profiles.json.lock`, unknown keys kept, the last good file kept as `.prev` (read
instead of an unreadable file, kept as `.bad`); with neither readable the switch is unavailable
(`profiles_unreadable`) and nothing is written. At each build the library finds each named install's record (its own,
a moved install's, else one migrated from what the library already wrote there).

**Entries.** One game core of a variant in one install (a portrait, or one memorial lobby core), keyed by the variant
key `Catalog.core_map` gives the core in its own install. Its state, first match wins: `damaged`, `missing`,
`untracked` / `changed` (files the library did not write, without / with a configuration), `source_missing`, a
take-over (a newer library write than the configuration, by another instance), `updated` / `reset` (a configured
entry a game update renamed or a Steam check put back; `updated` also marks entries configured with a mod whose build
changed), `ok`, else `pending`.

**The Enable Mods switch** (`toggle`). An Off adopts the mods it finds first (one mod per entry of its non-official
parts, the entry configured with it), keeps the built copies, then switches every entry carrying a library source to
official. An On switches every configured entry back to its source, except one whose files changed outside the
library: that one is skipped (`skipped`) and only Apply Mod Configuration writes over it. The intent and the entries
to reach are saved first, so a crash leaves them listed (the red dot and Retry). Each entry is judged again when it
is reached: a part the library can show unchanged by size and date (or the last bytes of a CRC-fixed file) carries
its record's source, every other part is read in full, and what the full read finds is kept in the records so the
cheap judgement agrees from then on. Refusals before anything is written: `folder_changed`, `state_changed`, the game
running, Steam busy, `not_enough_space` (what an Off copies, with 512 MiB to spare); an Off leaves overwritten
entries (no official copy anywhere) as they are (`stuck`).

**CRC repair** (`crc_repair.py`). Builds before 0.7.0 could write a mod file whole over a part whose name carries
another CRC without the CRC fix. Every whole-file write now compares the bytes with the CRC in the target's name
(`install.matches_target_crc`), and a built copy whose kept bytes lack the part's CRC is built again. Once per
library the start looks for what an older build left and repairs it silently once the first screen is revealed
(a game writer job); a library free of such entries records the check in `profiles.json` and never reads again.

**Game update impact** (`fingerprint.impact`). What a game update did under a mod is told from the fingerprint of the
official parts it was built against (`built-against.json`) held against the current ones, matched by the assets they
hold: `unchanged`, `rebuilt`, `new_parts`, `rig_changed`, `gone` or `unknown`. `update_impact` classifies every
library mod once the install's resource version differs from the recorded one; `last_impact` reads the kept outcome
back without checking. Each mod named carries `changes` (what the update changed: `repacked`, `animations`, `rig`,
`textures`, `removed`, `added`, `gone`). An On and its Retry hold an entry configured with a mod whose rig changed
(`held`) instead of writing it; updating or remapping the mod ends the hold.

**Untested game build** (`tested_build.json`, written by `scripts/build_tested_build.py`). The App ships, per server,
the resource versions it was tested on and a hash of every bundle name of the newest. On another resource version of
a server with that data, a write of any source but official onto a changed core is refused with `untested_build`
until the user confirms it (`mods.confirmUntested`); confirmed cores pass while the program runs and the install
keeps that resource version. Off, restores and rollbacks never ask.

**Built copies.** Before an Off reverts an entry that carries its configured source, the library keeps the exact
bytes it wrote there, whole: a file of the mod when one holds them, else a copy in `library/built/<sha1>.bundle`.
On, Retry and Apply Mod Configuration write them back byte for byte while they fit (built for that source, every
name the build read still in the install). A game update, a changed configuration, a mod's update or removal, a
Forget and Mod Cache cleanup drop them. Deletions run under `profiles.json.lock` against the configuration just
saved; bundle copies run outside that lock and are checked again under it. Settings shows the Mod Cache size and can
clear it; sizes are measured on demand.

**What reads Steam's records.** The scan consent covers Steam's records on this device: Detect, the game watch's
Steam poll, and Steam's file list of the installed build (`steam_records.py`: the depot manifest's size and SHA-1 per
file). With it, a bundle that list names is official by its size and SHA-1; without it, the CRC rule stays. The
write-time Steam-busy check needs no consent.

**What reads game files.** The scan consent (`scan_allowed`, asked at each use) covers every read the app makes on
its own of files the library did not write: the scan (headers and last bytes, never a whole file), an Off's own pass,
the roster's Replaced mark for mods outside the library, and a moved install's re-keying. Without it `scan` refuses
with `scan_off`, an Off reverts what the library wrote or configured and adopts the changed files it meets on the
way, and the Replaced mark shows only what the library wrote. Needing no consent: names and stats, the library's own
writes, the files a switch or the user's own action reaches (read in full), and the family the detail screen shows,
judged cheaply. Nothing hashes a game file outside switching.

**Several instances and data directories.** Two instances sharing a library merge `state.json` on every save and read
the other's writes before judging. A data directory removing an install from its list (`forget`) drops its record
only when no other data directory names it.

**State file safety.** `state.json` is read record by record: a record that cannot be used is skipped with a warning
and held — written back unchanged at every save, so one bad record costs that record at most. The last good file is
copied to `state.json.prev` before every write; an unreadable file is read from `.prev` and kept as `.bad`. When
neither can be read, or the file is a newer build's (`version` above 1, never read as corrupt), the library is
blocked: reads answer an empty library, every write raises `library_state_unreadable`, nothing of `profiles.json` is
written either, and `Services` takes no `seen_mods` baseline from such a library. A file that cannot be opened
(locked, scanned) is not corruption: reads answer empty, writes raise `library_io_error`, the next call reads again.
The replaces of `atomic_json`, `atomic_copy` and `atomic_clone` use `filelock.replace` with the same short Windows
retry as `webui.json`.

**Held records.** A valid record whose files are missing (a quarantined file, a partial restore) is held the same
way instead of dropped: not among the library's records, written back unchanged, back at the next load that finds its
files, with its alias, assignment and configuration. A held id still counts as a source the library has (its
configuration is not `source_missing`, its game files are not offered for adoption); the one exception is a game file
about to be replaced, whose bytes are kept as a mod then. A held photograph recipe still owns its photos
(`photo_in_use`).

**Library move** (`Services.move_library`, `library/relocate.py`; `LIBRARY_ITEMS` lists what travels). Each item is
copied into a staging folder inside the target, verified against the source, then published; only then is
`library_dir` switched and the source cleared item by item. Up to the switch nothing of the source changes; a failure
removes the copies and the same call can be made again. A failure clearing the source never loses anything.

**Per-Mod import publication.** `_store` stages and verifies the complete file group under
`import-transactions/<uuid>/files`, journals its expected hashes, publishes the directory under `mods/<uuid>`, then
saves the `state.json` record — the commit point. A failed operation rolls back its record and files without removing
earlier imports. Recovery runs before library operations and retains a pending journal it cannot complete.

**Per-Mod update.** `update` rebuilds into a temporary folder, then publishes under the mod writer: it refuses with
`entry_changed` when the mod's files changed since the rebuild read them, journals old and new files with hashes,
copies the old files to `mods/<id>/previous/<uuid>/`, then replaces the files. The record's new file list is the
commit point; a failure before it leaves the old mod whole.

**Pending journals.** A write, an import or an asset deletion that did not finish leaves its journal, and every write
recovers first (`_transaction_lock` raises the real failure of a journal it cannot settle, so a write never goes on
over one). Reads never fail on a pending journal: a read attempts recovery, logs a failure once, remembers it, and
goes on; it tries again when the set of journals changed or after 60 s. While a failure is remembered every change of
`state.json` and `profiles.json` is refused with it. Recovery rules: a game file whose bytes are neither the
journal's before nor after was changed outside the library — left as it is, its record goes back without a new
modification time, its kept original stays; a game file missing while a later-dated file of its part is in its folder
(a game update renamed it) is not restored; a write journal whose install is not known now waits, kept with its
files, and the next recovery after the install is back rolls it back.

This is stage-local atomicity; the game-write transaction separately retains and restores every replaced part. With
the master switch off, a valid application records desired state and defers the game-file write.

**3D model slots.** A model slot contains `variant.model` (its prefab core, first) and every other installed
model-kind core `Catalog.model_map` maps to that variant; the choice is keyed by the prefab core, and the slot's
files, Needs Fix, restore availability and incomplete judgement cover all its cores. A model Mod fits when every
file's core belongs to the slot; apply uses the existing replacement mapping, keeps originals and journals the whole
transaction. Switch Chibi offers Mods from any of the slot's cores and the student's other models as costumes
(`variant:<key>`, field-mode models included, `gameassets/model_adapt.py`): the slot keeps its skeleton, clips,
weapon, halo and props; the source's costume renderers are added as new objects to the slot's own bundles (existing
objects never change in place, since other bundles reference them). Roles, bone mapping and checks use data only,
never part names; a pair whose checks fail is refused with `source_unsupported`. A pair past the stretch limit
(`model_adapt.stretch_limit`) is built for a preview only (`previewOnly`; Apply greyed); another student's model is a
deliberate choice whose "preview" verdict is applied like "ok". A pair can be applied only once checked to its end
(`Services.costume_check`); verdicts are kept in `costume-verdicts.json`, keyed by the build signature with the
costume rule's version `gameassets/costume_rule.COSTUME_RULE` (bumped with any change to what the build writes,
refuses or measures; `tests/test_costume_rule.py` pins its sources). `model.checkCostumes` checks pairs in the
background as cards come into view. A costume build holds the model lock (taken after the library's lock) and closes
every bundle it opened when it ends. Optimize Physics builds a costume with the physics rules as well and keeps the
result as a model mod of the variant (`optimized_from`); it answers "No optimization needed" for the official model
and "No physics optimization available" when no rule beats the default build. A variant whose only model is a
field-mode player prefab gets `variant.field_model`; a variant with neither takes a field NPC prefab named by its id
or a model in its own dependency bundles, playing its story clips (`model.asset_root`, `_story_clips`).

## Game watch

### Checks before a write

`library/safety.py` holds the checks every game file write makes first (`writable_game_files`). None of them reads a
game file, so none needs the scan consent; the watch's own Steam poll runs only with it (`steam_allowed`).

- **Game running.** `check_game_not_running()` refuses with `game_running`, or `game_status_unavailable` when the
  process list cannot be read. `game_processes()` lists the game's processes (Windows: a Toolhelp snapshot, `tasklist`
  as fallback; macOS and Linux: `ps`, matching the game's executable names, Wine and CrossOver included), never this
  process or its parent.
- **Steam busy.** `check_steam_idle(game_folder)` refuses with `steam_busy` while the app manifest's `StateFlags`
  carries any updating/validating/downloading bit. `game_paths.steam_app_manifest()` finds the manifest by walking up
  to a `steamapps` ancestor; no manifest (outside a Steam library, the JP server, a dev sandbox) means no check.
- **Refusals by cause.** A destination the system refuses is named by `write_refusal(error, path)` from the OS error
  behind it: `game_file_locked`, `game_disk_full`, `game_file_not_found`, `game_app_denied` (macOS App Management),
  `game_file_denied`, else `game_file_write_failed`. On Windows the preflight's probe and every replace wait up to
  5 s (`filelock.IN_USE_WAIT`) while another program holds the file.

### The poller

`webui/gamewatch.py` greys the Enable Mods switch while a write would be refused. It polls every 3 s while a page is
connected to `/api/events` or the tray icon is shown, else every 15 s, and at once on `poke()` (the tray menu
opening). Each poll checks the game once, Steam for every known install, and the classic window `classic.json` names.
The write-time checks stay the authority; the poll only greys controls.

```python
GameWatch(installs, find_classic, profiles_seen, on_change, publish, request_scan, *, active, own_classic,
          on_classic_closed, check_game, check_steam).start()   # a daemon thread; stop() ends it
watch.state  # WatchState(game, steam_busy, classic) with .reason: game_running, game_status_unavailable,
             # steam_busy, classic_open, or None, in that order
```

| Callable | Given by | What for |
|---|---|---|
| `installs()` | the services | the known installs' folders, read at each poll |
| `find_classic()` | the classic hand-off | the classic window `classic.json` names, or `None` |
| `own_classic()` | the services | the pid of the classic window this instance started, so the poll ignores it |
| `profiles_seen()` | the services | whether another data directory changed `profiles.json` since the last poll |
| `on_change(state)` | the services | the first poll, every change, every `profiles_seen()` yes: publishes `mods.state` |
| `publish(event, data)` | the event bus | `classic.closed {code: null}` when a watched classic window ends |
| `request_scan()` | the scanner | the game stopping, Steam going idle, a classic window ending (scan consent only) |
| `on_steam_idle()` | the services | a game update found at Steam idle rebuilds game, catalog and library (`reload_game`) |

A failing callable is logged and never stops the thread.
