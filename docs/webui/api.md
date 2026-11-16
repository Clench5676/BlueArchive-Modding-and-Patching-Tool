# RPC, events and asset routes

## RPC

`POST /api/rpc` with `{"method": "...", "params": {...}}`, answered with `{"result": ...}` or
`{"error": {"code", "params"}}`. Methods whose result is a job id run on the worker (see [Jobs](#jobs-and-events)); details of what each
one changes are in the other files ([`README.md`](README.md)).

| Method | What it does |
|---|---|
| `app.info` | version, language, platform, game found, resource version, consents (`consent`, `spineConsent`), `uiCached`, paths, `dataDirWritable`, `debug` |
| `app.consent {granted}` / `app.spineConsent {granted?}` | the asset consent (`false` closes the app) / the Spine licence acknowledgement |
| `app.quit` | closes the window; `busy` while a writer job runs; in browser mode stops the server; `native_unavailable` with `--serve` |
| `app.openClassic {tab}` / `app.classic` / `classic.front` | start or raise the classic window / its status / Bring to Front |
| `classic.queue {method, params}` / `classic.clearQueue` | queue a refused write for when the classic window closes / empty the queue |
| `i18n.strings {lang}` | `{key: text}` for `webui.*` |
| `roster.list` | families with variants, mod counts, fresh flags, favourites, aliases, configured and preview sources; one library snapshot |
| `family.get {key}` | a family with its variants, mods and each variant's state (judged cheaply), `configured` per slot, `modelCores`, `modelFiles`, `version` |
| `settings.get` / `settings.set {key, value}` | the settings popup's values (roster view, lobby zoom and intro, tray, classicPrompt, game folders — refused with `game_not_found` unless the folder is a game install) |
| `settings.pickFolder {start?}` / `settings.clearCache` / `settings.detectGame {region}` | native folder picker / clear the asset cache / detect the install (scan consent) |
| `lobby.warm` / `lobby.lines {variant, talks, lang}` | build the lobby prefab index in the background / the dialogue lines per language (`lines_unavailable` when no source can be read) |
| `favorites.set {variant, on}` / `aliases.set {family, aliases}` | favourites / the user's own aliases (validated, at most 8 of 24 characters) |
| `mods.add {paths, background}` / `mods.pick {background}` | import files (identity is core plus SHA-1); with `background` a writer job |
| `mods.remove {id, confirm}` / `mods.removalInfo {id}` / `mods.rename {id, label}` / `mods.seen {id|ids}` | delete (restores affected entries) / the deletion confirmation's data / rename / mark seen |
| `mods.other` | the generic (`other`) mods for Settings' Other Mods |
| `ui.preview {variant, kind, source}` | store a preview choice in `webui.json`; never touches the manifest or game files |
| `model.costumes {variant, others}` / `model.checkCostumes {...}` / `model.optimize {variant, source, name}` | costume verdicts / check pairs to their end (job) / Optimize Physics (writer job) |
| `model.costumeCautions {variant, others}` | what offered pairs may draw differently in the game: `ex_parts`, `carried_shader` |
| `mods.state` / `mods.sizes {kind}` | the switch snapshot / Mod Cache or originals size, measured on demand |
| `mods.toggle {enabled, expect}` | the switch over every known install (writer job); also Retry |
| `mods.consent {granted}` / `mods.explained` / `mods.scan {dismissed}` / `mods.entries` | scan consent / first-toggle explanation shown / request a scan / the last scan's entries |
| `mods.adopt {entries}` / `mods.restoreDrift {entries}` / `mods.dismiss {kind, entries}` | Add to Library / Apply Mod Configuration / Keep as Is, Don't Ask Again |
| `mods.impact {ids?, force?}` / `mods.impactLast` / `mods.impactSeen {install, version}` | game update impact (job) / the last check without checking / mark seen |
| `mods.updateDue` / `mods.refreshCostumes` / `mods.confirmUntested {confirm}` | whether the start asks to check mods / rebuild outdated costume builds (writer job) / confirm an `untested_build` refusal |
| `mods.clearBuilt` / `mods.forget {install}` | Clear Mod Cache / Remove from List (jobs) |
| `mods.troubleshootState` / `mods.applyOfficial` / `mods.restoreConfig` | Troubleshoot Mods (pre-release builds only) |
| `actions.apply {variant, source, kind}` / `actions.restore {variant, kind}` | switch what the current install shows (writer job; `deferred` while mods are off; refusals of the moment keep the earlier choice) |
| `actions.update {mod}` / `actions.extract {target}` / `actions.compare {mod}` | rebuild against the current game file / extract / per-asset differences |
| `actions.remap {mod, geometry?}` / `actions.redrawn {mod, geometry}` / `actions.oldRig {mod}` | atlas remap onto the current rig / redrawn-art check / apply a texture-only portrait with its recorded rig (see Atlas remap) |
| `actions.graft {mod}` / `actions.graftBuild {token, geometry}` / `actions.graftCheck {token, geometry}` / `actions.discardGraft {token}` | carry official new animations into a mod's skeleton; refused with `graft_spine_licence` until the Spine licence is confirmed |
| `actions.compatibility {mod}` / `actions.compatibilityStamp {mod}` / `actions.acceptRemap` / `actions.discardRemap` | the compatibility report (job) / its cheap stamp / accept or discard a repair preview |
| `import.analyze {paths, recursive}` / `import.run {ids, options, revision}` / `import.pick` | Batch Import (see Import grouping); `run` requires the latest analysis's revision |
| `import.options` / `import.setOptions {...}` | the import options dialog's values |
| `import.catalog` / `import.assignCharacter {id, variant}` / `import.setSource {id, server, platform}` | offline assignment catalog / confirm ownership / choose a source by hand |
| `export.options` / `export.start {scope, folder, server, platform, ...}` | ZIP export (see below) |
| `hall.get` / `hall.update {value, revision}` / `hall.options` / `hall.dialogueLayout` / `hall.lines` / `hall.voice` / `hall.viewed` | the Student Hall's persisted state (stale revisions rejected with `hall_conflict`), sources, dialogue and voices |
| `photography.*` | see [Student photography](lobby-and-photography.md#student-photography) |
| `preview.cacheGet {key, renderer}` / `preview.cachePut {...}` / `preview.modelRevision {variant, source}` / `preview.assetRevision {paths}` | the preview cache and the revisions persisted stills are keyed by |
| `jobs.get {id}` | a job's state and its last log lines |
| `os.reveal {path}` | reveal in the file manager |
| `debug.trace {events}` / `preview.failed {url, step, error}` | input tracing with `--debug` / bounded preview diagnostics in the session log |
| `app.log` / `app.logFolder` / `app.report {context?, event?}` / `app.frontendError {event, context?}` | the session log and its folder / create a local error report / record a front-end error |
| `app.checkUpdate` / `app.update` / `app.installUpdate {file}` / `app.openReleasePage {page?}` | the update check, download (job) and install; open the release page (validated URL only) |
| `app.releaseNotes` / `app.upgradeNotes` / `app.upgradeNotesPresented {version}` | the running version's shipped notes / the once-per-upgrade offer / acknowledge it |
| `app.tours {launching?}` / `app.tourPresented {versions}` | pending feature tour pages / acknowledge them |
| `app.ready {timings?}` / `app.timings` | the page reports boot phases done / startup phase durations |
| `app.restartSafe` | restart in safe mode (refused where unavailable) |
| `window.chrome` / `window.layout {centre, height, drag, state}` / `window.motion {frames, period}` / `window.control {action}` | the native toolbar integration (macOS window buttons, minimize/maximize/close/fullscreen) |

HTTP endpoints outside `/api/rpc`, each with its own token, never the session token: `GET /api/classic/control` and
`POST /api/classic/report` (the classic child's one-time token) and `POST /api/instance/raise` (the raise token).

### ZIP export

`mod-export.js` provides one flow for the student-detail Export action and Settings' Export Mod Library / Export
Applied. `webui/export_service.py` prepares selected sources against real originals from the chosen configured
install; missing or incompatible originals block the export, and server/platform labels are never substituted for
conversion. The ZIP has one stable, portable directory per source and a root `manifest.json` with the target, source
identities, filenames, sizes and SHA-256 digests. The archive is written to a temporary file in the destination,
verified for ZIP integrity and member hashes, and atomically published; a failure leaves no partial new ZIP. This
exports user-requested files at runtime; no game assets are included in application builds.

### Import grouping

The batch import (`import.analyze`) and Add (`mods.add`, drag and drop) group files with one rule,
`judge.mod_versions`. Portrait and lobby files use their physical core and kind as the set key; model files use their
variant's prefab slot key across every physical core in that slot. The set stays together whatever its parts' dates;
only a repeated part whose dates the install does not keep together splits the set by date, one row or Mod per date.
Library Mods stored before this rule keep their split. Batch folders separate alternatives.

Import Mod keeps incomplete rows in the session: a row is `incomplete` when its CAB dependencies cannot resolve
through its selected files and unreplaced install files, or its source folders contain unselected files of the same
variant and kind. The analysis searches source folders using UnityFS directories only, without loading unselected
bundles' assets. Each row's `missing` lists the unresolved CAB ids and found files; a blocked or incomplete row also
carries `bundles` (what the check read per file: platforms, Unity versions, CABs, references) and `resolution` (how
each file found its official parts, and for a guessed source every server and platform tried with its failure), which
the error report and analysis log carry too. A row's status badge opens Details with that evidence; rows with found
files offer to complete themselves, and files supplied later join the incomplete row by slot or missing CAB. Missing
rows are unticked and `import.run` skips them.

Add and Import Mod both use `ModLibrary.identify_import`: an installed name (CRC aside), or a verified kept original
of that name, owns the target and kind; other filenames use content identification. One resolver decides which
official parts a set of Mod files replaces or updates (`bmpt/library/targets.py`, `ModLibrary._resolve`); the
analysis, previews, dependency checks, Enable and Apply all ask it, and a test checks that call sites decide nothing
themselves. Per file: (1) a current release — the exact installed name, else the name without the CRC, else the
unique CAB match (remaining ambiguity is `mod_target_ambiguous`); (2) other files (older releases, pre-split bundles,
another build's names), per name prefix and core, by the parts holding their asset paths, else sharing an asset name
and type; only for unreadable files, their part at its newest date. The resolution gives the parts the files write
(`updated`) and the parts they stand in for (`replaced`), which previews and the compatibility check drop. Generic
slots go through `targets.generic`; a generic part several files of one folder share gives each file its own slot,
keyed by the file's CAB as well (`judge.learn_packed`). Dependency checks resolve external CAB references through one
revision-checked `CabIndex` per install, excluding replaced files, and refuse only missing references or Spine pairs
added relative to the official set.

### Import origins and conversion

Select for Preview and Apply to Game are independent checkboxes above Import; `previewAfterImport` defaults false,
`enableAfterImport` true. Preview selection writes only the saved preview source; application writes the desired-state
manifest and, while enabled, the game files. `import.analyze` returns a library writer job whose result is
`{entries, skipped, revision}`; `Entry.state` is `ready`, `convert`, `update`, `blocked`, `library`, `installed` or
`done` (imported in this session; completion survives re-analysis of unchanged inputs, and converted inputs are
recognized through persisted source identities or a temporary conversion compared against eligible old entries).
`import.run` checks the revision again before building or writing; a fresh blocked verdict stops the run. Verified
original inputs are snapshotted before any row applies, so two Mods for one slot cannot rebuild from each other's
writes; each apply keeps its own transaction. Results carry `configured` and `applied`; with the master switch off,
configuration succeeds and the deferred apply writes when it turns on.

Each entry adds `source: {server, platform, detected} | null` and `target: {server, platform}` (servers `global` and
`japan`; platforms `android`, `ios`, `windows`). Origin evidence, in order: every serialized Unity build target
(explicit internal path segments can resolve an unknown one); bundle/container names, dependencies and package
markers (`_home_gl` and the Global package name identify Global, the JP package name Japan); serialized CAB
identities matching recoverable official CABs. A CAB name has no region meaning by itself; shared resource suffixes,
a repacker's new CAB, compression and the outer filename prove nothing. When direct detection is incomplete,
preflight tries only the unknown dimensions, Global before Japan and Android before iOS before PC; the first valid
choice wins, and a reliable platform or region is a constraint manual selection cannot contradict. If no candidate
passes, the row keeps `source_unknown`, its preview and the manual source selector; the error report lists every
choice tried with its failure.

Conversion uses the classic `core.process_mod_update` (extract assets, re-encode textures, inject into official
bundles, preserving target Unity metadata, CABs and dependencies), with `name_type` matching because platform path
IDs need not agree. Across servers, files the target resolver places by name, CAB or asset path are accepted; the
others' filename slots must match destination slots (`conversion_names`). The execution reader must be able to
extract every comparable Mod asset, and every extracted key must have a matching target: a readable header or a
rendered preview alone is insufficient, and a partial conversion cannot be added or applied. Asset transfers need
verified official baselines, so conversions and rebuilds can become `blocked` with `original_unavailable`; direct
replacements in current slots import even without an official copy. Output passes the normal library checks before
add/apply. A direct copy honours the CRC option (On, Off, or Auto from the official file) and appends the configured
Extra Bytes before the CRC fix. Spine import previews compose only the asset types the import writes
(`gameassets.spine.Composition`); models expose the import preview through
`/asset/model/<variant>.glb?source=import:<key>`, and every model source but `live` reads bundles outside the slot
through `ModLibrary.preview_reads` (so no source but `live` follows the Enable Mods switch). The reason keys exist
under `webui.import.reason.*` and `webui.error.*` with the same text in all locales.

### Manual character assignment

`character-picker.js` uses the roster's search, filters and sorting over the offline assignment catalog (shipped
student metadata plus the character map, so ownership can name a student whose resources are absent from the selected
install). Confirming the owner persists `assignment` with a self-contained, validated Mod; re-analysis recognizes the
stored result. `assigned_catalog.py` adds saved assigned students to roster/detail results without inventing resource
handles: an assigned-only student can select its own Mod for preview, while Apply stays disabled without a local
target. A manual label does not bypass bundle-readability, dependency or compatibility checks.

### Atlas remap

`atlas_remap.py` reconstructs current official atlas pages from matching mod regions. A physical page smaller than
its atlas declaration is enlarged to the declared dimensions (LANCZOS) before any region is placed; rotations, trim
offsets and premultiplied alpha remain part of placement. A portrait remap bakes pages through world space
(`bmpt.rebake`) using per-pose geometry the page computes (`rig-geometry.js`); a portrait the game drew anew is
refused (`atlas_remap_redrawn`, judged by `actions.redrawn`'s silhouette overlap). Where no record keeps the rig a
texture-only portrait's pages were drawn for, the library's archive of official portrait rigs does
(`library/rig_archive.py`, `<library>/rigs`, kept from verified official copies with the scan consent); a mod's pages
are matched to the kept release whose alpha masks they follow. A repaired copy and its original link to each other
(`repaired_from`, `repaired_id`), and a linked mod is not repaired again.

## Asset routes

| Route | Class |
|---|---|
| `/asset/ui/atlases.json`, `/asset/ui/sprite/<atlas>/<name>.png` | light |
| `/asset/ui/textures.json`, `/asset/ui/texture/<name>.png` (also the pointer and touch effect textures) | light |
| `/asset/ui/layout/<root>.json?path=<subpath>` | light |
| `/asset/art/bg/<name>`, `/asset/art/portrait/<name>.png` | heavy |
| `/asset/spine/spr/<core>/<file>`, `/asset/spine/lobby/<core>/<file>`, `/asset/spine/mod/<id>/<file>` (`skeleton.skel` and `skeleton.atlas` stand for the skeleton named like the core; a texture-only portrait mod with a recorded rig is drawn on that rig, `fingerprint.drawn_on`) | heavy |
| `/asset/spine/official-spr/<core>/<file>`, `/asset/spine/official-lobby/<core>/<file>`: verified official files, from kept originals when the game files are overwritten | heavy |
| `/asset/spine/<kind>/<key>/manifest.json`: the revision of the selected ordered bundle set; `/asset/spine/<kind>/<key>/<revision>/<file>` serves only that revision | heavy |
| `/asset/spine/spr/<core>@<id>/<file>`, `/asset/spine/lobby/<core>@<id>/<file>`: the core in another known install, `<id>` from `mods.state.installs` | heavy |
| `/asset/spine/lobby/<core>/lobby.json` (prefab layout, touch boxes, clips, idle pools, sounds, post-processing, effects, materials, pins, intro; no skeleton is extracted) | heavy |
| `/asset/spine/lobby/<core>/fx_<id>.png`, `/asset/spine/lobby/<core>/sfx_<id>.wav` | heavy |
| `/asset/model/<variant>.glb` (`?source=` `live`, `official`, `mod:<id>`, `import:<key>`, `variant:<key>`; `&clip=<name>`: one clip of that model, [`model-actions.md`](model-actions.md#clips-on-demand)) | heavy |
| `/asset/voice/<variant>/<clip>` (a lobby voice relayed from Kivo) | heavy |
| `/asset/office/<day|night>/layout.json` and its texture routes (the Hall's office background) | heavy |
| `/asset/photography/...` | see [Student photography](lobby-and-photography.md#student-photography) |

Thumbnails (faces, busts, expression cards, variant cards) are rendered in the page from the skeletons, in one shared
offscreen WebGL context, framed on the face and kept as in-memory blob URLs (plus the Preview Cache's validated
lossless PNGs on disk). `spineAssetBase` resolves the manifest for every load, before the cache lookup; the revision
includes the ordered resolved paths, sizes and nanosecond times, and `Services.asset` checks it before and after
extraction, rejecting a source change during the request. Files of a core merge by name, the last bundle winning;
`ModLibrary.live_bundles(core)` gives the live sources the preview's order (official parts first, non-official last),
and official and preview parts are limited to the core's Spine category. Mod previews overlay the mod's parts on
omitted parts selected by the same replacement mapping as import and apply; missing official data fails the whole
preview with 404, leaving the plain card. Switch popups hold capture priority for their whole thumbnail scope; Spine
loading has a 15 s deadline after dequeueing and three retries on an idle renderer queue, and closing a scope removes
its pending work. The main portrait and lobby resolve their sources the same way and retry once when a rejected load
has a newer manifest revision.

## Jobs and events

`Services.submit(kind, fn, writes=None, library=None, **params) -> job_id` runs `fn(log=..., progress=..., **params)`
on one worker thread (`JobRunner`). A **writer job** (`writes="game"`: it writes the game folders and the library;
`writes="library"`: the library only) holds back the window's close button, `app.quit`, a library move and the
services rebuild after a game folder change; a read-only job holds back nothing. Every writer job ends on the worker
with the library's judging of the `unfinished` list, a new `mods.state` snapshot, `library.changed {cores, kinds}`
and a scan (with the scan consent).

| Writes | Jobs |
|---|---|
| game | `apply`, `restore`, `mods.toggle`, `mods.restore` (Apply Mod Configuration) |
| library | `library.adopt`, `library.prune`, `library.clearBuilt`, `library.forget`, `library.judge`, `update` |
| none | `extract`, `export`, `update.download` |

Every game folder write a job makes is pinned at submit, so a game folder changed meanwhile gives `folder_changed`
instead of writing another install. Shutdown drops queued read-only jobs and waits only while a writer job is queued
or running. Besides the front end's jobs, the web UI submits `library.prune` once per library it builds and
`library.judge` when another data directory sharing the library changed it.

Events on `/api/events`:

| Event | Data |
|---|---|
| `job.started` | `{id, kind}` |
| `job.log` | `{id, lines: [str]}` (batched) |
| `job.progress` | `{id, sequence, current, total, label}` (throttled); batch jobs also report `done`, `completed`, `remaining`, `index`, `phase`, `step` |
| `job.done` / `job.failed` | `{id, result}` / `{id, code, params}` |
| `drop` | `{paths: [str]}` |
| `mods.state` | as the `mods.state` method, on every change (toggle progress, writer job ends, game watch, classic window, scan consent, game folder change, another data directory's change) |
| `library.scanned` | `{counts: {install key: {state: n, configured: n}}}` after a scan |
| `library.changed` | `{cores, kinds}` after every writer job; without data (read everything again) after a failed writer job, a library move, a classic window closing, and the mod list's own changes |
| `classic.opened` / `classic.ready` / `classic.closed` | `{pid}` / `{pid, tab}` / `{code}` (see [Classic window](desktop.md#classic-window)) |
| `classic.queueDone` | `{submitted, dropped}` once the writes queued for the classic window ran |
| `tray.toggle` / `tray.open` | the tray submitted a toggle, or brought the window up on a view (see [Tray](desktop.md#tray)) |
| `config.changed`, `app.busy` | `{}` |
