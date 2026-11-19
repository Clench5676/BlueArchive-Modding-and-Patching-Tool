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
| `students.list` | students with alts, mod counts, fresh flags, favourites, aliases, configured and preview sources; one library snapshot |
| `student.get {key}` | a student with its alts, Mods and each alt's state, `configured` by resource type, `chibiCores`, `chibiFiles`, `version` |
| `settings.get` / `settings.set {key, value}` | preferences, including `studentList`, `memorialLobbyZoomOut`, `memorialLobbyPostProcessing`, `memorialLobbyIntro`, and game folders |
| `settings.pickFolder {start?}` / `settings.clearCache` / `settings.detectGame {server}` | native folder picker / clear the asset cache / detect installs with scan consent |
| `memorialLobby.warm` / `memorialLobby.lines {alt, talks, lang}` | build the memorial lobby prefab index in the background / the dialogue lines per language (`lines_unavailable` when no source can be read) |
| `memorialLobby.camera.save {alt, memorialLobby, source, view, scale, offset}` | save memorial lobby framing as a Mod (job) |
| `favorites.set {alt, on}` / `aliases.set {student?, alt?, aliases}` / `ui.visit {alt}` | favourites / save user aliases by alt or student / record a recent visit |
| `mods.add {paths, background}` / `mods.pick {background}` | import files (identity is core plus SHA-1); with `background` a writer job |
| `mods.remove {id, confirm}` / `mods.removalInfo {id}` / `mods.setAlias {id, alias}` / `mods.seen {id\|ids}` | delete and restore affected entries / deletion confirmation data / set or clear an alias / mark seen |
| `mods.other` | the generic (`other`) mods for Settings' Other Mods |
| `ui.preview {alt, resourceType, source}` | save a preview choice in `webui.json` and return the preview choices keyed by resource type |
| `chibi.costumes {alt, others}` / `chibi.checkCostumes {...}` / `chibi.optimize {alt, source, name}` | costume verdicts / check pairs to their end (job) / Optimize Physics (writer job) |
| `chibi.costumeCautions {alt, others}` | what offered pairs may draw differently in the game: `ex_parts`, `carried_shader` |
| `mods.state` / `mods.sizes {kind}` | the switch snapshot / Mod Cache (`built`) or kept official files (`officials`) size on demand |
| `mods.methodNoticeSeen {install}` | the install's fallback to file replacement told, for its current resource version (see Switch method) |
| `mods.toggle {enabled, expect}` | the switch over every known install (writer job); also Retry |
| `mods.consent {granted}` / `mods.explained` / `mods.scan {dismissed}` / `mods.entries` | scan consent / first-toggle explanation shown / request a scan / the last scan's entries |
| `mods.adopt {entries}` / `mods.restoreDrift {entries}` / `mods.dismiss {kind, entries}` | Add to Library / Apply Mod Configuration / Keep as Is, Don't Ask Again |
| `mods.impact {ids?, force?}` / `mods.impactLast` / `mods.impactSeen {install, version}` | game update impact (job; a row whose target the update renamed or removed carries `targetRetired`) / the last check without checking / mark seen |
| `mods.updateDue` / `mods.refreshCostumes` / `mods.retryFailed` / `mods.confirmUntested {confirm}` | whether the start asks to check mods / prepare the game catalogs and rebuild outdated costume builds (writer job; its result's `notices` list `catalog_restored` by `install`) / write failed entries again once per App build (writer job) / confirm an `untested_build` refusal |
| `mods.clearBuilt` / `mods.forget {install}` | Clear Mod Cache / Remove from List (jobs) |
| `mods.troubleshootState` / `mods.applyOfficial` / `mods.restoreConfig` | Troubleshoot Mods (pre-release builds only) |
| `actions.apply {alt, source, resourceType}` / `actions.restore {alt, resourceType}` | switch what the current install shows (writer job, `deferred` while Mods are off) |
| `actions.update {mod}` / `actions.extract {target}` / `actions.compare {mod}` | rebuild against the current game file / extract / per-asset differences |
| `actions.useOfficialSkeleton {mod, geometry?}` / `actions.redrawn {mod, geometry}` / `actions.useRecordedRig {mod}` | atlas remap onto the current rig / redrawn-art check / apply a texture-only portrait with its recorded rig (see Atlas remap) |
| `actions.addAnimations {mod}` / `actions.addAnimationsBuild {token, geometry}` / `actions.addAnimationsCheck {token, geometry}` / `actions.discardAddAnimations {token}` | carry official new animations into a mod's skeleton; refused with `add_animations_spine_licence` until the Spine licence is confirmed |
| `actions.addAnimationsConvert {token, geometry?}` / `actions.addAnimationsConvertCheck {token, geometry?}` | build or check the converted animation result |
| `actions.converterStatus` / `actions.converterDownload {consent}` / `app.openConverterPage` | converter availability / download after consent / open the release page |
| `actions.compatibility {mod}` / `actions.compatibilityStamp {mod}` / `actions.acceptRepair {preview}` / `actions.discardRepair {preview}` | the compatibility report (job) / its cheap stamp / accept or discard a repair preview |
| `import.analyze {paths, recursive}` / `import.run {ids, options, revision}` / `import.pick` | Batch Import (see Import grouping); `run` requires the latest analysis's revision |
| `import.options` / `import.setOptions {...}` | the import options dialog's values |
| `import.catalog` / `import.assignAlt {id, alt}` / `import.setSource {id, server, platform}` | offline assignment catalog / confirm ownership / choose a source by hand |
| `export.options` / `export.start {scope, folder, server, platform, target?, resourceType?}` | ZIP export (see below) |
| `studentHall.get` / `studentHall.update {value, revision}` / `studentHall.options` / `studentHall.dialogueLayout` / `studentHall.lines {alt, lang?}` / `studentHall.voice {alt, clip}` / `studentHall.viewed {alt, source}` | the Student Hall's persisted state (stale revisions rejected with `student_hall_conflict`), sources, dialogue and voices |
| `thumbnails.*` | see [Student thumbnails](memorial-lobby-and-thumbnails.md#student-thumbnails) |
| `preview.cacheGet {key, renderer}` / `preview.cachePut {...}` / `preview.chibiRevision {alt, source}` / `preview.assetRevision {paths}` | the preview cache and the revisions persisted stills are keyed by |
| `jobs.get {id}` / `jobs.cancel {id}` | a job's state and its last log lines / cancel a job |
| `os.reveal {path}` | reveal in the file manager |
| `debug.trace {events}` / `preview.failed {url, step, error}` | input tracing with `--debug` / bounded preview diagnostics in the session log |
| `app.log` / `app.logFolder` / `app.report {context?, event?}` / `app.frontendError {event, context?}` | the session log and its folder / create a local error report / record a front-end error |
| `app.checkUpdate` / `app.update` / `app.installUpdate {file}` / `app.openReleasePage {page?}` | the update check, download (job) and install; open the release page (validated URL only) |
| `app.releaseNotes` / `app.upgradeNotes` / `app.upgradeNotesPresented {version}` | the running version's shipped notes / the once-per-upgrade offer / acknowledge it |
| `app.tours {launching?}` / `app.tourPresented {versions}` | pending feature tour pages / acknowledge them |
| `app.ready {timings?}` / `app.timings` | the page reports boot phases done / startup phase durations |
| `app.restartSafe` | restart in safe mode (refused where unavailable) |
| `window.chrome` / `window.layout {centre, height, drag, state}` / `window.motion {frames, period}` / `window.control {action}` | the native toolbar integration (macOS window buttons, minimize/maximize/close/fullscreen) |

`GET /api/classic/session`, `GET /api/classic/control` and `POST /api/classic/report` use the classic child's token.
`POST /api/instance/raise` uses its own raise token. The bootstrap, RPC and asset requests use the page's session.

### Switch method

Each `mods.state.installs[]` item carries how mods are switched in that game folder, read from the catalog store
in the snapshot, never through the library lock:

- `method`: `catalog` (the App swaps the game's catalog file for one that points the modded parts at placed files)
  or `files` (the App writes mod bytes over the official files);
- `methodReason`: why a folder uses `files`, null for `catalog` and for a folder not moved to the catalog yet: a refusal of the catalog checks (`catalog_unsupported`,
  `catalog_invalid`) or a layout reason (`unsupported_server`, `missing_catalog`, `missing_bundle_folder`,
  `outside_root`, `invalid_layout`);
- `methodNotice`: true while a fallback after a refusal of the catalog checks was not told for the folder's current
  resource version. The page tells it once and calls `mods.methodNoticeSeen`; a later game version that falls back
  again is told again.

### Resource fields

Student results use `students`, `alts`, `studentNames`, and `studentAliases` where applicable. An alt can include
`portrait`, `memorialLobby`, `chibi`, `fieldChibi`, `officialThumbnail`, the selected `thumbnail`, `background`,
`customAliases`, and `releasedOnServer`. `playable` describes availability on any server. A `resourceType` is
`portrait`, `memorialLobby`, `chibi`, or `other`. Alt sources use `alt:<key>` alongside `official` and `mod:<id>`.
`localResourceTypes` lists the installed resource types. `storedResourceTypes` lists the types with stored mods.

Mod results distinguish `targetAlt` from `genericTarget` (`other:…`) and may include `assignedAlt`, `name`,
`baseName`, `alias`, `repairSourceId`, and `repairedCopyId`. `hasKeptOfficial` describes the presence of a kept
copy, while `official` describes the live game bytes. Job `kind` identifies the operation. The `kind` selector in
`mods.dismiss` identifies the prompt type.

`settings.get` exposes the `studentList` view with `groupAlts` and `releaseStatus`. `memorialLobbyZoomOut` returns
`{mode, custom}`. `settings.set` accepts `fullArt`, `gameView`, `unlocked`, or `custom` for the mode and a number for
`memorialLobbyZoomOutCustomLimit`. The Student Hall uses `mode: "memorialLobby"`, `memorialLobbies`, and portrait
entries with `flipHorizontal`. Dialogue uses `animationName` and `animationCues`. Portrait `expression` stays the
name of the captured expression.

Compatibility results expose `addAnimations` with `resourceType` (`own` or `oldRig` when available). An Add
Animations review reports the carried animation names in `report.addedAnimations`.

For `settings.set` with `key: "libraryDir"`, a path selects a destination. An existing library requires a choice
with `value: {path, adopt: true}`. The backend requests a fresh choice when the destination inputs change.

### ZIP export

`mod-export.js` provides one flow for the student-detail Export action and Settings' Export Mod Library / Export
Applied. The exporter prepares selected sources against verified official files for the configured install. Each
source's `ba-mod.json` is its only metadata file. Single exports place it and the bundle files at the ZIP root.
Library and applied exports give each source a portable directory. The importer ignores the root `manifest.json`
of older exports. The archive is verified for ZIP integrity and member hashes before atomic publication. The
package's `kind` field and its enum values belong to the package format.

### Import grouping

Batch Import and the library's `mods.add` endpoint group files with one rule,
`judge.mod_versions`. Portrait and memorial lobby parts group by resource type and physical core. Chibi parts group
by the prefab core that owns the configuration entry. A repeated part splits the group by date when the installed
parts do not coexist. Batch folders separate alternatives.

Import Mod keeps incomplete rows in the session: a row is `incomplete` when its CAB dependencies cannot resolve
through its selected files and unreplaced install files, or its source folders contain unselected files of the same
alt and kind. The analysis searches source folders using UnityFS directories only, without loading unselected
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

`import.options`, `import.setOptions`, and the options passed to `import.run` use `outputFolder`, `crcCorrection`,
`createBackup`, `matchStrategy`, `threads`, and `assetTypes`, alongside compression, extra bytes, and preview and
apply options. Match strategies are `containerNameType`, `path_id`, and `name_type`. Saved options are in
`prefs.batch_import` in `webui.json`.

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
verified official baselines, so conversions and rebuilds can become `blocked` with `official_unavailable`; direct
replacements in current slots import even without an official copy. Output passes the normal library checks before
add/apply. A direct copy honours the CRC option (On, Off, or Auto from the official file) and appends the configured
Extra Bytes before the CRC fix. Spine import previews compose only the asset types the import writes
(`gameassets.spine.Composition`); chibis expose the import preview through
`/asset/chibi/<alt>.glb?source=import:<key>`, and every chibi source but `live` reads bundles outside the slot
through `ModLibrary.preview_reads` (so no source but `live` follows the Enable Mods switch). The reason keys exist
under `webui.import.reason.*` and `webui.error.*` with the same text in all locales.

### Assign an alt

`character-picker.js` uses the student list's search, filters and sorting over the offline assignment catalog (shipped
student metadata plus the character map, so ownership can name a student whose resources are absent from the selected
install). Confirming the owner persists `assigned_alt` with a self-contained, validated Mod. Re-analysis recognizes the
stored result. `assigned_catalog.py` adds saved assigned students to student list/detail results without inventing resource
handles: an assigned-only student can select its own Mod for preview, while Apply stays disabled without a local
target. A manual label does not bypass bundle-readability, dependency or compatibility checks.

### Atlas remap

`spine/atlas_remap.py` reconstructs current official atlas pages from matching mod regions. A physical page smaller than
its atlas declaration is enlarged to the declared dimensions (LANCZOS) before any region is placed; rotations, trim
offsets and premultiplied alpha remain part of placement. A portrait remap bakes pages through world space
(`bmpt.spine.rebake`) using per-pose geometry the page computes (`rig-geometry.js`); a portrait the game drew anew is
refused (`atlas_remap_redrawn`, judged by `actions.redrawn`'s silhouette overlap). Where no record keeps the rig a
texture-only portrait's pages were drawn for, the library's archive of official portrait rigs does
(`library/rig_archive.py`, `<library>/rigs`, kept from verified official copies with the scan consent); a mod's pages
are matched to the kept release whose alpha masks they follow. A repaired copy and its source Mod link to each
other using stored `repair_source_id` and `repaired_copy_id`.

## Data upgrade notice

The page calls `GET /upgrade/state` before ordinary RPC, events, or asset requests. The bootstrap uses the page's
session cookie, which the server sets from the initial launch URL, and accepts the same `X-BAMT-Token` header as RPC.
The response has `status`, `phase`, `reason`, `progress`, `size`, `affected`, `counts`, `logFolder`, and the localized
`upgrade.*` strings. `progress` is `{done, total}` or `null`. `affected` lists data directories for an old-data
change. A `ready` state also carries `notice`: `"upgraded"` while the one-time notice after the first upgrade is due,
otherwise `null`.

`ready` opens the App. `running` polls for progress, including during a pending write action. `changed` offers
Import Old Data or Keep Current Data, and `failed` gives a reason. Reasons are `busy`, `olderVersion`, `classic`, `unavailable`, `unsupportedStorage`, `recoveryPending`,
`newerFormat`, and `error`. For `newerFormat`, phase `data` or `library` identifies the affected manifest.

Each action posts to `/upgrade/<action>` and returns the current state:

| Action | Behavior |
|---|---|
| `retry` | Check and open the generation again. |
| `update` | Run the update check and open the release page for `newerFormat`. |
| `openLog` / `quit` | Open the upgrade log folder / close the App. |
| `keep` / `import` | Acknowledge the old-data changes or import them after backing up the current data. |
| `relocate` | Open the desktop folder picker and retry with the selected library. |
| `acknowledge` | Record the one-time notice as shown; `notice` is `null` from then on. |

While `notice` is `"upgraded"`, the started App shows the message popup (title `upgrade.title`, the line
`upgrade.upgradedWarning` in the warning ink, OK) ahead of the other start popups and posts `acknowledge` once OK is
pressed. Closing the popup any other way leaves the notice due for the next start. The upgrade itself never waits for
it and never asks for a choice.
The [generation layout](library.md#generation-2-layout) defines the committed stores and version rule.

## Asset routes

| Route | Class |
|---|---|
| `/asset/ui/atlases.json`, `/asset/ui/sprite/<atlas>/<name>.png` | light |
| `/asset/ui/textures.json`, `/asset/ui/texture/<name>.png` (also the pointer and touch effect textures) | light |
| `/asset/ui/layout/<root>.json?path=<subpath>` | light |
| `/asset/ui/selection-frame/<id>.png` | selection frame texture |
| `/asset/art/background/<name>`, `/asset/art/officialThumbnail/<name>.png` | heavy |
| `/asset/spine/portrait/<core>/<file>`, `/asset/spine/memorialLobby/<core>/<file>`, `/asset/spine/mod/<id>/<file>` (`skeleton.skel` and `skeleton.atlas` stand for the skeleton named like the core; a texture-only portrait mod with a recorded rig is drawn on that rig, `fingerprint.drawn_on`) | heavy |
| `/asset/spine/official-portrait/<core>/<file>`, `/asset/spine/official-memorialLobby/<core>/<file>`: verified official files, from kept originals when the game files are overwritten | heavy |
| `/asset/spine/<resourceType>/<key>/manifest.json`: the revision of the selected ordered bundle set; `/asset/spine/<resourceType>/<key>/<revision>/<file>` serves only that revision | heavy |
| `/asset/spine/portrait/<core>@<id>/<file>`, `/asset/spine/memorialLobby/<core>@<id>/<file>`: the core in another known install, `<id>` from `mods.state.installs` | heavy |
| `/asset/spine/memorialLobby/<core>/memorial-lobby.json` (prefab layout, touch boxes, clips, idle pools, sounds, post-processing, effects, materials, pins, intro; no skeleton is extracted) | heavy |
| `/asset/spine/memorialLobby/<core>/fx_<id>.png`, `/asset/spine/memorialLobby/<core>/sfx_<id>.wav` | heavy |
| `/asset/chibi/<alt>.glb` (`?source=` `live`, `official`, `mod:<id>`, `import:<key>`, `alt:<key>`; `&clip=<name>`: one clip of that chibi, [`chibi-actions.md`](chibi-actions.md#clips-on-demand)) | heavy |
| `/asset/voice/<alt>/<clip>` (a memorial lobby voice relayed from Kivo) | heavy |
| `/asset/office/<day|night>/layout.json` and its texture routes (the Student Hall's office background) | heavy |
| `/asset/thumbnails/...` | see [Student thumbnails](memorial-lobby-and-thumbnails.md#student-thumbnails) |

Layout widgets use `width`, `height`, `fillAmount`, `fillDirection`, `inclineAngle`, `effectStyle`, `alignment`,
`className`, and `spriteName`. Layout nodes use `localPosition` and `localRotationZDegrees`. Memorial lobby clips
use `finishMode`, `introMix`, `outroMix`, `outroStartOffset`, and `blockInteractionOnPlay`. Hit boxes carry
`rotation`.

Thumbnails (faces, busts, expression cards, alt cards) are rendered in the page from the skeletons, in one shared
offscreen WebGL context, framed on the face and kept as in-memory blob URLs (plus the Preview Cache's validated
lossless PNGs on disk). `spineAssetBase` resolves the manifest for every load, before the cache lookup; the revision
includes the ordered resolved paths, sizes and nanosecond times, and `Services.asset` checks it before and after
extraction, rejecting a source change during the request. Files of a core merge by name, the last bundle winning;
`ModLibrary.live_bundles(core)` gives the live sources the preview's order (official parts first, non-official last),
and official and preview parts are limited to the core's Spine category. Mod previews overlay the mod's parts on
omitted parts selected by the same replacement mapping as import and apply; missing official data fails the whole
preview with 404, leaving the plain card. Switch popups hold capture priority for their whole thumbnail scope; Spine
loading has a 15 s deadline after dequeueing and three retries on an idle renderer queue, and closing a scope removes
its pending work. The main portrait and memorial lobby resolve their sources the same way and retry once when a rejected load
has a newer manifest revision.

## Jobs and events

`Services.submit(kind, fn, writes=None, library=None, **params) -> job_id` runs `fn(log=..., progress=..., **params)`
on one worker thread (`JobRunner`). A **writer job** (`writes="game"`: it writes the game folders and the library;
`writes="library"`: the library only) holds back the window's close button, `app.quit`, a library move and the
services rebuild after a game folder change; a read-only job holds back nothing. Every writer job ends on the worker
with the library's judging of the `unfinished` list, a new `mods.state` snapshot, `library.changed {cores, resourceTypes}`
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
| `job.done` / `job.failed` / `job.cancelled` | `{id, kind, result}` / `{id, kind, code, ref?, params}` / `{id, kind}` |
| `drop` | `{paths: [str]}` |
| `mods.state` | as the `mods.state` method, on every change (toggle progress, writer job ends, game watch, classic window, scan consent, game folder change, another data directory's change) |
| `library.scanned` | `{counts: {install key: {state: n, configured: n}}}` after a scan |
| `library.changed` | `{cores, resourceTypes, mods?}` when affected resources are known, otherwise an empty payload to refresh the library |
| `library.waiting` / `preview.waiting` | `{busy}` while waiting for the library or preview gate |
| `thumbnails.changed` | `{alt}` after thumbnail selection, protected deletion, or a restore that changes the App selection |
| `classic.opened` / `classic.ready` / `classic.closed` | `{pid}` / `{pid, tab}` / `{code}` (see [Classic window](desktop.md#classic-window)) |
| `classic.queueDone` | `{submitted, dropped}` once the writes queued for the classic window ran |
| `tray.toggle` / `tray.open` | the tray submitted a toggle, or brought the window up on a view (see [Tray](desktop.md#tray)) |
| `config.changed`, `app.busy`, `server.closing` | `{}` |
