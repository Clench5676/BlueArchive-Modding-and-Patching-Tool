# Backlog

Current version handoff: [Development status](project-status.md). Verify it against the actual branch, working tree
and `pyproject.toml` version before reporting progress.

BL-001 through BL-008 describe the implementation included in 0.5.0, following the 0.4.1 baseline (`d936decd`).
Later entries track unreleased local review work and planned or deferred requirements.
It includes the revised Hall workflow, dialogue, office backgrounds, global portrait scaling, immersive Hall view
and touch-friendly queue reordering. The owner approved the stable release after the local alpha review builds.

The 0.5.0 release regression run passed 1,324 JavaScript tests and 2,801 Python tests, with 24 Python tests skipped.
The preceding alpha.2 baseline completed native-window checks; alpha.3 was handed to the owner for visual acceptance.
Current gesture coverage includes touch holds, swipe-to-scroll, cancellation, pointer ownership, viewport scaling
and stationary-edge scrolling. These tests do not replace acceptance on a physical touchscreen.
No development data or runtime-exported game resources are included in the distributable files.

The owner's 2026-10-26 feedback is recorded as **BL-022 through BL-031**. See the
[complete requirement/evidence map](feedback-2026-10-26.md) for every sub-item, supplied screenshot/report,
acceptance boundary and proposed execution order. BL-022 through BL-029 and BL-031 were implemented for local 0.6.0-alpha.1 review. BL-030 remains planned. The preceding
Chinese README/CHANGELOG drafts and fork synchronization are complete; the initial BL-015 implementation has owner acceptance.

BL-032 through BL-039 (except the BL-037 design task) were implemented for local 0.6.0-alpha.2 review. The owner's
macOS review of alpha.2 on 2026-10-26 accepted BL-033 to BL-036, BL-038 and BL-039; its findings are recorded as
**BL-040 through BL-045**. Windows has not been reviewed yet.

## BL-001: Per-Mod atomic library import and game installation

- Status: Implemented in 0.5.0.
- Requested: 2026-10-23.
- Requirement: A partial file set for one Mod must not be reported as a successful library import or game install.

Library import now journals the full group under `import-transactions/<id>/`, copies and verifies every staged
bundle, publishes the complete Mod directory, then publishes its metadata. Metadata publication is the commit point.
Caught failures restore the prior metadata and remove the incomplete group. Recovery checks journal paths, file
hashes and directory boundaries; a blocked rollback retains its journal and reports `library_recovery_pending`.
Recovery also requires directory synchronization before deleting a journal, including retries whose metadata
already has the desired contents. Previously completed, independent Mods remain intact.

Game installation retains its existing journaled multi-file write, verification and rollback flow. Replacement
happens file by file; interrupted operations recover on the next operation or restart. A locked file or failed
recovery can remain pending rather than being reported as complete.

Atomicity is **per Mod within each stage**. Library storage and optional application remain separate stages: a
complete library Mod can remain after an application failure, together with a configured choice awaiting repair
or retry. This does not introduce a combined import-and-apply transaction. While Mods are off, application retains
the existing desired-state behavior and defers game-file writes.

Validation: `tests/test_library_import_atomic.py` covers each file transfer and verification, metadata publication,
interruption at commit boundaries, failed rollback, repair links, symlink confinement and failed directory-sync
retries. Existing library and game-install tests remain part of integration validation.

## BL-002: Student-detail export flow and ZIP output

- Status: Implemented in 0.5.0.
- Requested: 2026-10-23.

The primary Export action now uses the shared ZIP flow:

1. Show the actual configured default output directory, with Confirm on the right and Choose Directory on the left.
2. Show target server and platform choices from the available configured installations; default to the current
   install, preferring serialized platform metadata.
3. Choosing a directory returns to the confirmation dialog with its updated path. Cancelling the picker retains
   the previous path. Only Confirm starts an export.
4. Prepare bundles against the selected target's actual original resources, validate them, then write and verify
   a complete ZIP before publishing it by atomic replacement.
5. On success, offer to open the actual destination used for that export.

Conversion reuses import preparation and its compatibility checks; changing metadata labels is not conversion.
Missing or incompatible target originals prevent export. A ZIP contains one stable, portable subfolder per source
and a `manifest.json` with target, source identity, filenames, sizes and SHA-256 digests. Partial preparation,
archive writing or verification does not publish a new ZIP. The secondary asset-extraction actions remain available.

Validation: generated UnityFS fixtures cover Android/iOS-to-PC and cross-server preparation, desired-state selection,
source changes and publication failures in `tests/test_mod_export.py`. Frontend journey tests cover picker return,
cancellation and the successful destination; the shared confirmation layout has been inspected in a running UI.

Workflow clarification (2026-10-25): routine old-Mod upgrading and generation for distribution should reuse
Import -> Update/Repair when needed and offered -> Student Detail Export. Application to game files is optional;
repair is not a mandatory step for an already compatible Mod. Export prepares against the configured target's
available official resources. Arbitrary, unconfigured target Bundles remain an advanced classic-interface use
case rather than a separate near-term screen duplicating this journey.

## BL-003: Batch-import preview/apply checkboxes and removal of imported rows

- Status: Implemented in 0.5.0.
- Requested: 2026-10-23.

Select for Preview and Apply to Game are separate checkboxes immediately above Import. Apply After Import is no
longer in the options dialog. Their saved preferences remain independent: preview selection defaults off;
application retains its existing default on and any explicitly saved choice.

Successful imports can save a preview source without changing the game configuration. Application uses the existing
desired-state semantics, including deferred writes while Mods are off. Failed imports never select a preview.
A library-only manual assignment can be stored and selected for preview even when application has no local target.

Remove Imported removes rows classified as completed, already in the library or already installed from the import
list. It does not delete library Mods or uninstall game files. Pending, conflicted and failed rows remain available.
Returning to the page in the same session retains the list and its current view state.

Validation: batch-import Python and frontend tests cover independent preferences, preview/application outcomes,
completed-row removal and unchanged or failed entries.

## BL-004: Mod settings export section

- Status: Implemented in 0.5.0.
- Requested: 2026-10-23.

Settings > Mods contains an Export section with Export Mod Library and Export Applied. Both use the complete
BL-002 journey and ZIP format. Export Applied selects distinct Mod sources from the desired-state manifests across
the library's recorded installations and includes their assignments in the manifest. It does not infer selection
from the current on/off switch or from the bytes presently installed in the game.

Every selected Mod must prepare successfully for the chosen target before the archive is published; a failure in
one member leaves no partial bulk ZIP. Verification includes the master-switch-off case and the shared frontend flow.

## BL-005: Enable update checking by default

- Status: Existing default and regression coverage verified for 0.5.0.
- Requested: 2026-10-23.

Automatic startup update checking was already enabled for an unset preference. That default remains enabled;
an explicitly saved `startupUpdate: false` remains off. No preference migration resets an opt-out. The automatic
network check retains the existing released-build gate and starts after the first screen, without blocking navigation.

Validation covers missing, enabled and disabled preferences and the build gate.

## BL-006: Manual character assignment for unidentified Mods

- Status: Implemented in 0.5.0.
- Requested: 2026-10-23.

The import workflow offers a character selector for unidentified, self-contained Mods. It uses the shipped offline
student metadata and character map, including students and variants without resources in the selected installation.
Its roster-based search, filtering, sorting and grouping controls identify the owner, which is confirmed and stored
with the Mod. Confirmed ownership survives re-analysis and does not depend on a local original bundle.

Ownership and installation compatibility remain separate. Without a compatible local target, the Mod can be stored
and its own source selected for preview, but Apply is disabled and the detail page shows No Matching Game File.
`storedKinds` describes library preview availability; `localKinds` and the actual resource handles describe the
installation. No missing handle is invented, no sibling source is substituted, and declining automatic preview
selection does not silently choose a Mod. Later target availability still requires the normal compatibility checks.

Validation: manual-identity/catalog fixtures, import-preview tests and detail/roster rendering tests cover saved
ownership, missing resources, preview opt-out and the disabled application path. The actual supplied Mod is subject
to the same readability and dependency validation; assignment alone does not make an incomplete Mod usable.

## BL-007: Preserve navigation history across menus

- Status: Implemented in 0.5.0.
- Requested: 2026-10-23.

Back follows committed page entries within the current session. Batch Import -> student detail -> Back returns to
Batch Import, retaining its import list, selection and view controls. Student steps and tab changes replace the
current detail entry rather than adding every pose to history. Cancelling unfinished navigation returns to the last
committed page; failed navigation does not become a successful history entry.

Student Hall is the root fallback, including for a direct student link. History is not persisted across app restarts.
Navigation and rendering tests exercise page transitions, cancellation, source-page return and the detail Back callback.

## BL-008: Student Hall

- Status: Implemented in 0.5.0; local alpha review builds approved for release.
- Requested: 2026-10-23.

Student Hall is the startup/root page and has portrait-composition and memorial-lobby modes. The top-right Home
control returns to it; the bottom navigation contains no Hall entry. Home, Settings and their divider retain the
native top-bar prefab geometry. Local Hall actions sit immediately to the left of the mode switch.
Layout uses the selected Pencil icon, Background uses Photo, and portrait/playlist selection uses the native duty
student icon. All top-right action icons are aligned by their visible artwork and reduced to 75% of their previous size.
Save/Cancel have equal widths on the next row, aligned with the icon group. The mode switch retains the
enabled loop graphic without its separate count label. The far-right Fullscreen control uses UILobby's native
`Btn_Screen_On`; its owned exit uses `Btn_Screen_Off`. Both share the toolbar's visible icon size and centering.
Immersive view hides navigation and editing controls without rebuilding the scene; the exit control and Escape
restore them, preserving drafts and playback. Navigation cleanup restores focus and inert state independently of
the detail screen's visual mode.

Portrait entries have independent IDs. Different portrait sources for the same student may coexist, but the same
source cannot be added twice. Portraits share one rendering canvas and keep independent animation state, position,
scale and draw order. The editor adds, removes and positions entries. Hovering an actor in the editor exposes the
selected native trash icon; layouts with at least three actors also expose layer order and horizontal flip actions.
There is no selection frame or individual scaling interaction. The portrait picker admits only library portrait sources, groups rows by student with optional
skin merging, and gives each row an independent horizontal card strip. Search, filters and sorting follow the
roster. Cards remain still previews; clicks do not open another window or change the background scene. Explicit Add
buttons align with their inclined name strips; an existing source has a disabled Added action. Older duplicate
portrait entries are canonicalized when read without writing a migration or changing the saved revision.

The portrait projection uses the ordinary lobby's authored CharPos and skeleton scale as its baseline. A same-variant
comparison with the supplied Cycling Shiroko screenshot supports this corrected default composition. Layout editing
adds a compact global size slider at the upper right (25–150%, default 100%), spanning the icon group
inside the native inclined button body, without a caption, percentage input or Reset button. It multiplies each entry's
relative scale without moving its anchor or resizing the background. Changes update existing skeletons and hit bounds
without reloading assets or rebuilding the canvas; the percentage participates in the same Save/Cancel draft.
Entering the editor, saving and canceling retain the existing players and animation state. New sources load behind
a same-resolution snapshot and loading indicator, then all actors commit and draw together. Current and saved
entries retain their resources for cancellation; unused actors, source players and backgrounds are pruned.
The compact portrait modal uses native white inclined row plates with equal outer-edge margins; its scrollbar's
painted right edge aligns with the toolbar. Lazy thumbnails show failure text only after a real failure.

Layout and playlist editors use drafts with Save/Cancel. Their modal hosts block the underlying page; nested
selection/filter popups belong to the editor lifecycle. Leaving a dirty Hall prompts Save, Discard or Keep Editing.
Canceled navigation preserves the current scene and return stack, and obsolete async navigation cannot commit.

Memorial mode maintains an ordered playlist of unique official or Mod lobby sources. Different sources for the same
student remain allowed; read-only legacy deduplication preserves the active source without advancing the revision. Previous/Next
selects the active entry; the existing lobby renderer supplies its scene and interaction. The adjustment modal puts
six-column native formation cards on the left and landscape candidates on the right, with a draggable playlist
underneath. Selected students retain the native green outline without a check or sequence number. Both selectors
share the roster toolbar without its screen-title icon/caption. Playlist cards have no extra name strip or heading;
their Remove buttons align with the image. The queue has a native pale rounded background and compact gaps.
Pointer-based reordering keeps the source thumbnail at 50% opacity, carries its image with the cursor and marks
the insertion gap with a native blue gradient rather than highlighting another card. The entire area above each
Remove button is a grab target. Mouse/pen drag directly; a touch swipe pans the queue, while a 260 ms touch hold
starts reordering with a slightly lifted thumbnail. Card gestures claim the pointer stream so the surrounding
horizontal scroll handler cannot pan simultaneously. Holding a dragged card at an edge scrolls continuously;
release, cancellation, a second touch, loss of focus and disposal retire the gesture and its timers.
Unselected candidates default to recently viewed order. Mod availability, counts and sorting are scoped separately
to portrait and memorial sources; release filters describe students in the selected installation. The final playlist entry cannot be removed; the backend enforces that transition too.
Hall choices do not apply Mods to game files or change the detail page's saved preview. Mode, selected background,
portrait arrangement, playlist order and active memorial entry persist; revision checks reject conflicting saves.

Scene switches keep the previous complete frame until all target resources and the first frame are ready. Portrait
and current memorial sessions are retained separately, with inactive clocks/audio paused. Playlist edits that retain
the current source do not replay it. A bounded hidden memorial staging slot loads replacements without destroying
the active scene. Modal redraws wait for visible thumbnails and decoding while offscreen entries stay lazy.

Portrait clicks use the actual `LobbyCommon/DialogPanel/ChatDialog/Talk` balloon (`Lobby_balloon`) beside the clicked
instance. Text preserves the native font, wraps without truncation and centers in the visible bubble body excluding
the tail and shadow; edge placement can flip the tail. A Hall-wide turn lock ignores other portrait clicks during
preparation and vocal playback. Preparation is bounded to 25 seconds; text-only or failed-audio fallback remains
for five seconds. The preparation timeout is removed once audio starts and never cuts off a playing vocal.
Ordinary lobby voice lookup prefers a matching local AudioClip and otherwise uses the existing Kivo transport.
Localized Idle dialogue shares the existing Global table download. Voices stay in bounded memory caches, concurrent
loads coalesce, and each click has cancellation ownership. A pending first lookup retries within a bounded budget;
available text remains visible while the same clip's voice becomes ready. Missing audio does not erase readable text.
Missing dialogue is silent. Authored expression clips and timed continuation rows drive vocal expressions, including
Simplified Chinese; delayed metadata resumes from the active vocal time and the previous pose returns afterward.

The office-background sources have been identified as the layered NGUI prefabs `LobbyCommon` and `LobbyCommonNight`
(`UILobbyBackground_Day` and `UILobbyBackground_Night`). Integration reads their native texture layers losslessly at
original resolution and places them using the prefab geometry. With no saved background choice, the daytime office
is the default when its resources are available. A saved available choice takes precedence. Partial installations
without the office resources can use the available collection backgrounds; the inspected inventory contains 54,
which is an observed count rather than a fixed limit.

Environmental cloud and light motion driven by the office's Animation/Tween components remains static in this
implementation. Portrait animation and memorial-lobby animation retain their existing behavior. The native
additive light pass, daytime/nighttime scenes, picker previews and cancellation have been checked in a running UI;
the final integrated build passed the corresponding runtime checks.

Validation covers state validation and revision conflicts, portrait/memorial uniqueness, kind-scoped filters, shared rendering and
lifecycle behavior, navigation and runtime UI checks. The local validation run passed 2,801 Python tests (24 skipped)
and 1,313 JavaScript tests. Native checks include foreground activation after loading in the background, mode
round-trips, forward/reverse queue dragging and the final icon/control layout. Game assets, rendered previews and voice files are not included
in the program's distributable files.

## BL-009: Chinese Hall captions and non-editing scene zoom

- Status: Accepted by the owner (2026-10-27); Windows not reviewed.
- History: First integrated into local 0.5.1-alpha.1; retained in the current review build.
- Requested: 2026-10-24.

A Chinese Hall request gives its preferred caption source the existing bounded preparation budget instead of
assuming that an earlier Global response means Chinese text is absent. A late caption updates the current balloon
without replaying its voice or consuming another turn. Chinese interfaces use the other Chinese script as their
caption fallback, never Japanese or English. Language switches immediately reevaluate existing captions, and stale
requests or render completions cannot overwrite a newer language. Uncaptioned old recordings do not duplicate a
captioned current recording of the same numbered line. Flat ordinary voice numbers follow voiced rows rather than
dialogue-group IDs, including groups with two separate voices and missing recordings.

Outside layout editing, mouse wheels and touchpad pinches zoom the entire portrait scene, from 100% to 300%.
BL-010 raises the original 50% viewing minimum to the default view.
Touchpad swipes pan the magnified scene. The shared Spine camera redraws portraits at native rendering quality;
the office layers use the same transform, and hit areas and speech balloons follow. UI controls stay at their
normal size. Viewing zoom does not write layout positions, per-portrait scales or the saved overall size. Entering
layout editing restores the original editing view. Mode round-trips retain the temporary portrait viewing camera.

Validation includes translation readiness and stale-language races, source filtering and dialogue-group mapping,
shared-renderer identity, pointer-centered projection, WebKit pinch deduplication, scaled hit areas and input
blocking during editing or modals. A live-source check of eight implemented students returned Chinese captions
for all 42 ordinary lines. Local package visual acceptance remains with the owner.

## BL-010: Default-size minimum for Hall scene zoom

- Status: Accepted by the owner (2026-10-27); Windows not reviewed.
- History: First integrated into local 0.5.1-alpha.1; retained in the current review build.
- Requested: 2026-10-24.

The Student Hall's non-editing scene zoom must stop at its default viewing size (100%, camera scale 1.0).
Mouse-wheel and touchpad zoom-out gestures must not shrink the scene below that size. This supersedes the
50% viewing minimum originally implemented in BL-009.

## BL-011: Thin solid memorial-queue insertion line

- Status: Accepted by the owner (2026-10-27); Windows not reviewed.
- History: First integrated into local 0.5.1-alpha.1; retained in the current review build.
- Requested: 2026-10-24.

Use approved option A: a solid line from the native Common/Common_Line_Dot sprite, tinted with the game's
formation-selection blue. Reduce its visible width to about one fifth of the current line (10 to 2 stage pixels).
The insertion indicator remains in the target gap and uses no vertical opacity gradient or target-card outline.
The existing native line widget and formation-selection palette supply its artwork and color.

## BL-012: Integrate upstream CAB discovery improvements

- Status: Accepted by the owner on the lead's evidence (2026-10-27): positive change.
- History: First integrated into local 0.5.1-alpha.1; retained in the current review build.
- Requested: 2026-10-25.

Consolidate the App and upstream low-level CAB parsing where their behavior is compatible. Adopt demand-driven
dependency discovery: search bundles in the referring bundle's group first, then common/prolog resources, and
expand to the remaining resources only when necessary. Stop after all requested CABs are resolved. Do not inherit
a file-size cutoff that can silently exclude a required dependency.

Persist the reusable CAB-to-bundle index in the managed user cache, with bounded storage, schema and resource-version
keys, source-directory confinement and the App's existing stronger file-revision validation. Update or discard
stale mappings after game updates, Mod replacement, file removal or a cache-clear request. Publish cache files
atomically; corrupt or unavailable cache data must fall back to normal discovery.

Use the declared UnityFS bundle length when block info is stored at the end, so appended CRC-correction bytes do
not shift the index. Preserve supported compression, alignment and engine-version flag handling, including the
UnityFS LZMA correction made during the upstream sync.

Retain the App's bundle locks, explicit resource cleanup, Mod overrides, missing-dependency tracking and incomplete
export safeguards. Do not replace its renderer/export graph wholesale with the CLI-only graph. Measure cold and
warm dependency lookup with isolated official fixtures and verify invalidation and concurrent readers before
claiming improvements to any preview or loading workflow.

Validation covers same-group/common fallback, bundles larger than 64 MiB, persisted mappings, mtime-preserving
replacement, deletion, new dependencies, cache clearing during reads, source changes during parsing, corrupt-cache
confinement, symlink escape, concurrent readers, graph hints and cleanup. Repeated scan prefixes reuse batch metadata;
returned mappings still revalidate their files. Two real model exports and two memorial scene descriptions retained
identical output hashes before and after integration. Persistent metadata has a first-build cost; warm restart
measurements must be reported separately rather than presented as universal cold-start improvements.
The integrated alpha.1 regression passed 2,855 Python tests (24 skipped) and 1,340 JavaScript tests. The local macOS
package passed version/resource and compiled-server startup checks; owner visual acceptance remains pending.

Acceptance evidence (2026-10-27): the owner accepts if the change is positive. `tests/test_cab_discovery.py` and
`tests/test_cli_cab_cache.py` pass with the official development view (35 tests): the referring bundle's group is
searched first and the search stops once every CAB is found; prolog resources come before the remaining scan; CAB
metadata is reused across sessions without rereading headers; large bundles are no longer cut off, so a needed
dependency is not silently missed; the CRC-correction tail no longer shifts the index; replaced, removed and cleared
files invalidate mappings; nothing outside the game folder is read. The full suite shows no regression. No timing
benchmark was run.

## BL-013: Drag-and-drop asset packing screen

- Status: Planned. Not started; owner confirmed backlog-only scope.
- Requested: 2026-10-25.
- Priority: Near-term backlog.

Provide a new-interface packing workflow based on the batch-import screen. Accept dropped raw assets such as PNG,
SKEL and ATLAS files, group related resources, show validation and target-bundle matches, and generate packed Bundle
files. A target Bundle supplies the existing Unity asset structure; packaging is not merely creating a ZIP of the
input files. Permit target selection when automatic matching is ambiguous and explain missing companions before
starting work. Preserve the existing packing options where relevant.

Generation must finish and validate the complete output group before reporting success. Keep generation separate
from optional library import or game application, and reuse the existing protected write paths for those actions.
Follow the batch-import screen's native prefab layout, readiness and progress conventions. Do not introduce a new
rendering-quality reduction or write to game files just because files were dropped.

## BL-014: In-context bundle inspection and reference tracing

- Status: Candidate. Not scheduled.
- Requested: 2026-10-25.

Consider exposing the upstream read-only inspection and cross-bundle reference tracing through contextual actions
in import errors, compatibility details and the existing diagnostic-report flow. Show the source platform, Unity
version, asset identity, missing external CAB and its resolution evidence when useful to explain a failure.

Prefer enriching the current workflows over adding a mandatory standalone diagnostics screen. Avoid expensive
full scans during normal thumbnail rendering or list refreshes; cancellation, bounded work and on-demand detail
loading must preserve UI responsiveness. Keep user paths redacted in shareable reports and distinguish dependency
or format failures from guessed platform incompatibility.

## BL-015: Student thumbnail photography and per-character library

- Status: Implemented; owner UAT and visual acceptance approved on 2026-10-26. Not released.
- Requested: 2026-10-25.
- Follow-up: BL-022 and BL-023 record the owner's 2026-10-26 revisions. Shared galleries across skins, separate
  official/generated defaults, variant-scoped numbering, export and occlusion guides are included in alpha.1 for owner review.

Open photography from the A1 framed-camera icon beside Reset in the student's native fullscreen view. Reuse the
real full-quality portrait and memorial renderers. Both modes preload before interaction, switch in place with
one ready-frame publication, retain their players and pause hidden rendering/audio. Back uses the standard App
button, leaves fullscreen and restores the prepared detail page. Route changes cancel stale camera work and
release retained resources.

The centered guide matches the roster's 404:456 texture aspect. Its rule-of-thirds grid can be hidden while
framing corners remain. Pan at minimum zoom; zoom out until the complete authored portrait or memorial height
fits the guide. Outside-art regions use a clean matte without blurred edge extension. Capture reads native scene
pixels and excludes controls, guides, dialogue balloons, custom pointers, click effects and capture feedback.
Preserve the character's authored halo, scene effects, expression and playback state.

The A1 smile (`Common_Icon_Emoji`) opens the titleless expression strip, with cached face previews and the same
native difference cards as student details. B1 chat (`School_Icon_Chat`) opens a titleless dialogue/action strip;
the detail page's existing playback button sits beside it. Strips fit actual rows and scroll overflow. The mode
switch reuses the Hall's icon-only control. Shutter captures save directly without a review dialog or automatic
gallery opening. A transient shutter effect follows the snapshot; only a confirmed save animates the actual photo
into the bottom-left album. Animation failures cannot block persistence or leave playback frozen.

Captures are immutable, lossless PNGs in the shared Mod library's `photographs/`, outside disposable preview caches.
The manifest is serialized across instances, validates IDs/ownership/digests, and publishes images and metadata
atomically. Each character has an independent App selection. Save in the compact gallery changes the student
roster's image source; Apply is a distinct, actual game operation. Default always remains visible and protected;
Restore Default resets both selections without deleting captures. App/desired-game photographs are protected
from deletion. Apply and Delete share a short row, with Save centered beneath the pair.

The target is the student roster's Collection portrait:
`UICharacterCollectionPopulatorUnit/Grid/UICollectCard/Char_Portrait` (404 by 456 prefab units), resolved through the
character's `Student_Portrait_<variant>_Collection` mapping. Shiroko (Cycling)'s verified target is
`Student_Portrait_Shiroko_ridingsuit_Collection`. Formation and Tactical Challenge selection icons are separate
resources and remain untouched. Default images read verified originals, including kept backups, with install and
resource-revision-specific asset URLs.

Game application composes the chosen texture into its shared Collection bundle, validates every unrelated object's
raw bytes and every resource stream, retains native dimensions/mipmaps and uses lossless RGBA32/LZ4 output. Exact
original GPU-encoded pixels restore one default while other characters' choices remain. Removing the final custom
choice restores the entire original bundle byte-for-byte. Managed composite Mods reuse journaled atomic writes,
backups, rollback, protection against target changes during preparation, desired-state manifests and the master
switch. With Mods off, Apply changes configuration only; On installs the composed sources. Obsolete compiled
composites can be pruned while saved PNGs persist.

Dated Collection batches coexist and have separate slots. Legacy configuration keys migrate without game writes.
When a photo-only batch is replaced by a game update, its choices rebuild from the new originals before enabling;
unrelated objects from an old batch are never copied over new originals. Missing originals or mixed external Mods
remain explicit errors instead of successful application. Error states allow retry without a false Applied badge.

Automated coverage includes persistent storage, cross-instance saves, invalid inputs/digests, selected-photo
protection, failed metadata publication, camera geometry/native-density cropping, rendered expression reuse,
shutter/save ordering, resource retention, intro playback controls, Back navigation, shared-bundle preservation,
individual defaults, master-switch defer/reapply, updated batches, concurrent target changes and full rollback.
Game assets and development data are excluded from packages. UAT and visual review belong to the owner.

## BL-016: Advanced extraction and ADB interface migration

- Status: Deferred. Not started.
- Requested: 2026-10-25.
- Priority: Someday (backlog of the backlog).

Migrate arbitrary-Bundle asset extraction and its advanced options, including Atlas frame unpacking and scaling,
to the new interface only when higher-priority work is complete. The existing per-character extraction path does
not require this migration. Keep the classic extraction tool available meanwhile.

ADB file pushing and device-directory browsing are likewise deferred; preserve their classic-interface entry
points. This entry does not authorize development access to the owner's device or production game installation.

## BL-017: Directory-scan backups and bulk preview export interface migration

- Status: Deferred. Not started.
- Requested: 2026-10-25.
- Priority: Someday (backlog of the backlog).

Defer new-interface migration of scanning game directories to back up detected Mods and of bulk rendering previews
to output image files. Existing library/applied ZIP export and automatic thumbnail generation remain separate
workflows; retain the classic tools until migration becomes a priority.

## BL-018: Standalone manual CRC tool interface migration

- Status: Deferred. Not started.
- Requested: 2026-10-25.
- Priority: Someday (backlog of the backlog).

Keep manual CRC calculation, two-file comparison and explicit target-CRC correction available in the classic
interface for Mod authors and troubleshooting. Normal import/application should continue to handle required
CRC correction automatically. Defer a standalone new-interface page or advanced-tool migration until higher
priorities are complete; do not add this as a routine step in the normal import workflow.

## BL-019: Integrate file CRC checks and Mod inventory reports

- Status: Planned. Not started.
- Requested: 2026-10-25.

Extend Settings > Mods > Check Game Files and its current scan-result workflow instead of adding another mandatory
top-level tool. Reuse the installation snapshot and existing background scan infrastructure to provide explicit
CRC inspection results and export a Mod inventory. Share the resulting snapshot with the existing diagnostic
report when relevant; an inventory of detected Mods and a fault/log report remain distinct outputs.

The inventory should identify the installation, character/core, resource kind, filenames, managed or untracked
state and available checksum evidence. Keep long reads off the UI thread, support cancellation, report monotonic
progress and invalidate stale results when the install or source files change. Do not repeat a full checksum scan
on language changes, list redraws or report generation when a valid snapshot is already available.

CRC mismatch is evidence, not proof of corruption, and matching CRC is not proof that a bundle is official.
Check readability, completeness and target identity before offering correction; do not make an unknown or damaged
bundle appear valid merely by matching the filename's CRC. A read-only check or inventory export must never alter
game files. Explicit corrections must use the existing protected writer, backup/rollback and verification paths,
respect the master-switch and installation state, and report incomplete or failed work accurately.

Validate using generated and isolated official fixtures: matching, corrected, unknown, corrupt, missing and
externally changed files; stale snapshots and cancellation; export-only runs; and failed writes with rollback.

## BL-020: Release notes in update detection

- Status: Accepted by the owner (macOS review of 0.6.0-alpha.9, 2026-10-27) through the prerelease-only simulation; Windows not reviewed.
- Note: Prerelease Settings offers notes-present and notes-empty simulations through the real update dialog; update and release-page actions stay local without network or installation.
- Requested: 2026-10-25.

Add a localized Release Notes action to the update-detection dialog. Display the notes approved for that exact
detected release, with a link to its release page when notes are unavailable. Reading notes must never start a
download or installation. For future stable releases, the agent drafts release notes from final changes and
validation, and the owner reviews their wording before publication. Record this release requirement in the
repository's agent guidance; do not publish an empty stable-release description.

Owner decision (2026-10-27): the next prerelease includes a simulated update prompt so the owner can review it: the
three-button dialog, the Release Notes view and the link fallback when the notes are empty. It is clearly marked as a
simulation, cannot install anything and must not exist in a stable build.

## BL-021: First-launch new-version feature tour

- Status: Accepted by the owner on macOS (alpha.12, 2026-10-29); Windows not reviewed.
- Owner review of the 0.6.0 draft (2026-10-26): merge the photography and shared-gallery pages into one student
  thumbnail page (`学生详情全屏后增加大头照拍摄功能`), drop the release-notes page; publication awaits the owner's OK
  on the revised copy.
- Note: The two-page, six-locale 0.6.0 text-only tour is registered for prerelease Settings replay and upgrade presentation; stable publication remains gated.
- Requested: 2026-10-25.
- Flow: [New-version feature tour](feature-tour-flow.md).

On the first normal launch after an upgrade, present a short, dismissible tour of newly introduced features.
Each page covers one feature with concise text and an optional approved App UI illustration or text-only layout. Previous/Next, page count and Close
remain available; the last page finishes into the current App screen. Settings provides permanent access to
published tours, including earlier versions. Dismissal must not affect existing preferences or ongoing work.

The agent drafts the copy and optional App UI illustrations outside packaged sources; a human reviews them before shipping. Never package screenshots or images made from game files.
Wait for the first screen, required consents and higher-priority recovery/update dialogs to settle before showing
the tour. Do not introduce new startup scans, rendering work or network dependencies for this presentation.
Remember presentation per user-data profile/version, avoid duplicate prompts on restarts/reinstalls/downgrades,
and handle missing images or absent tour content without blocking launch. Release notes and CHANGELOG remain
separate, complementary views of the same actual release history.

Owner review of the 0.6.0 tour copy (2026-10-27): page 1 stays as it is. Page 2: title `缩放学生大厅`, body
`滚轮、捏合放大立绘场景，平移查看` (the clause about the saved layout is dropped); the other locales follow. Owner decision:
put the tour into the next prerelease (Settings tour entry and the first launch after updating) for review; a stable
build shows it only after the owner's final OK.

Owner review of 0.6.0-alpha.9 on macOS (2026-10-27): the tour popup ("新功能 - 0.6.0", page 1/2, Previous/Next)
fills almost the whole window; it is far too large for two short lines. Owner decision: text-only pages use the size of the App's
ordinary message popups (for example the delete confirmation), fitted to the text; title, page counter and
Previous/Next stay, with their button size unchanged.

Owner review of 0.6.0-alpha.10 on macOS (2026-10-27): Settings' New Features row shows "Not set" (`settings.notSet`)
as its value. Lead's diagnosis: the row uses `valueRow` (`settings.js`), which prints Not set for an empty value; the
row has no value to show.

## BL-022: Shared student galleries, generated defaults and photo export

- Status: Accepted by the owner (2026-10-27); Windows not reviewed.
- Requested: 2026-10-26.
- Details: [Gallery semantics and acceptance](feedback-2026-10-26.md#bl-022-detail-shared-gallery-and-default-sources).
- Later requirement: BL-036 supersedes the original blanket deletion protection for App-selected and generated
  photographs. Only official entries are inherently undeletable; applied custom entries require confirmation.

Share a student's photographs across skins while retaining source-variant identity, independent variant selections
and safe migration. Provide protected official and roster-generated defaults; the generated image is the fallback
unless an official/custom choice is explicit. Keep counters independent per originating skin and reuse existing
localized variant naming when displaying other skins' photos. Add lower-left gallery Export to the configured
output directory and open that directory after verified success.

The current game-photo writer already obeys the Mod master switch; App-roster selection is independent. Preserve
that distinction, deferred application, shared-bundle integrity and rollback for both default types and shared
photos. No automatic game write follows merely from browsing the gallery or resolving an App fallback.

## BL-023: Faithful photography occlusion guide and save readiness

- Status: Accepted by the owner (2026-10-27); Windows not reviewed.
- Requested: 2026-10-26.
- Details: [Camera requirements](feedback-2026-10-26.md#bl-023-detail-camera-guides-placement-and-save-readiness).
- Owner review: the occlusion button is too wide and the frame-not-ready modal still occurs. Follow-ups BL-032
  and BL-033 are planned; this part has not passed acceptance.

Add an Occlusion Guide toggle to the left of the grid control, matching the roster gray banner and Exclusive Weapon
indicator area from actual Collection prefabs at 1:1 relative geometry. Exclude the guide from saved photographs.
Remove portrait fullscreen Reset's text and move the memorial camera entry to the portrait Reset position.
Recover transient frame-not-ready capture/save races in the background without the reported modal; wait for valid
pixels and durable publication, prevent duplicate/stale work, and retain meaningful handling of permanent failures.

## BL-024: Frameless desktop shell and macOS menus

- Status: Accepted by the owner (2026-10-27); Windows not reviewed.
- Requested: 2026-10-26.
- Details: [Desktop chrome](feedback-2026-10-26.md#bl-024-detail-native-desktop-chrome).
- Follow-up: BL-037 requests a design for integrating genuine platform window controls into the top toolbar.
  The design itself is backlog work and has not started.

Remove Windows/macOS title bars and WebUI-related macOS menu entries. Preserve standard window drag, resize,
close/minimize/fullscreen and keyboard/accessibility behavior, plus the App's own navigation and camera controls.
Validate actual packaged host behavior rather than relying only on a browser development server.

## BL-025: Windows update location, shortcuts and companion files

- Status: Targeted changes implemented for local 0.6.0-alpha.1 review; packaged Windows acceptance pending. Windows UAT by the owner later (2026-10-27).
- Requested: 2026-10-26.
- Details: [Update continuity](feedback-2026-10-26.md#bl-025-detail-windows-update-continuity).

Trace the versioned-EXE download/restart flow and define a stable launch/update arrangement that preserves existing
desktop shortcuts. Inventory same-directory program dependencies versus user data and temporary files. Retain
settings/library/output, required companion files, verified replacement and failure recovery. Do not assume a
shortcut follows a newly named EXE or delete unrecognized neighboring files.

## BL-026: Desktop rendering seams, shadow corners and source-toolbar overflow

- Status: Accepted by the owner on Windows (alpha.12, 2026-10-29): no seams, corners or overflow seen.
- Requested: 2026-10-26.
- Evidence: Screenshots 3 through 7 in the [feedback record](feedback-2026-10-26.md#supplied-evidence-index).
- Details: [Cross-platform checks](feedback-2026-10-26.md#bl-026-detail-cross-platform-ui-defects).
- Owner review: a fine seam remains visible in the Mod switch's On segment. Residual defect tracked by BL-035.

Investigate Windows side bands, diagonal cuts in the inner shadow, thin heading/button/frame seams and overflowing
portrait/L2D source-action labels. Reuse correct prefab geometry and preserve artwork/font quality. Verify packaged
Windows WebView2 at representative display scaling and macOS Retina/non-Retina-equivalent sizes, with localized
labels and normal/fullscreen views. Record actual platform coverage; owner UAT/visual review remains required.

## BL-027: First Windows startup failure after a 0.3-to-0.5 upgrade

- Status: Targeted changes implemented for local 0.6.0-alpha.1 review; packaged Windows acceptance pending. Windows UAT by the owner later (2026-10-27).
- Requested: 2026-10-26.
- Evidence: `BAMT-report-0.5.0-20261025-1309.zip`; reviewed read-only; cold first-screen layout delay recorded in the report.
- Details: [Failure and migration investigation](feedback-2026-10-26.md#bl-027-detail-one-time-windows-upgrade-startup-failure).

Identify why the first post-upgrade launch failed while later launches succeeded. Preserve the report, diagnose the
earliest failure, reproduce with isolated old-profile fixtures and add a regression for the confirmed cause.
Make migration/recovery idempotent; a successful retry alone is not evidence that the defect has been fixed.

The report has no backend exception: two layout exports took 38.817 and 40.530 seconds before a 45-second
first-screen watchdog expired. Add the missing Formation prefab lookup hint and reset the boot idle watchdog on
completed layout work, with a bounded total grace period. This addresses a concrete cold-layout path; the report
does not establish a damaged migration, and exact Windows reproduction/acceptance remains pending.

## BL-028: Official Hall portraits, toolbar counts and Shiroko first-run defaults

- Status: Accepted by the owner (2026-10-27); Windows not reviewed.
- Requested: 2026-10-26.
- Details: [Hall behavior](feedback-2026-10-26.md#bl-028-detail-student-hall-sources-and-initial-state).
- Follow-up: BL-038 closes the Hall-only layout gap left by hiding Home: move Settings right and remove its divider.

Include original portraits alongside Mod sources. Hide Home while already at the Hall root, retaining it elsewhere.
Prevent removal of the final portrait and hide its trash control. Show all applicable layer/mirror/removal actions
with two portraits, including the reported incomplete-toolbar case. Preserve toolbar hover ownership and alignment.
Initialize an unconfigured first-time profile with Shiroko's official portrait and memorial lobby, prepare before
reveal and handle unavailable resources gracefully without replacing an existing user's layout or writing game files.

## BL-029: Native gold affirmative-action buttons

- Status: Accepted by the owner (2026-10-27); Windows not reviewed.
- Requested: 2026-10-26.
- Details: [Prefab inventory and semantics](feedback-2026-10-26.md#bl-029-detail-gold-affirmative-actions).
- Owner review: source-picker Save is missing gold styling, while Update and Settings' Export Mod Library are
  incorrectly gold. Correct the explicit action mapping under BL-034; current coverage is not accepted.

Find the official gold affirmative prefab, inventory Confirm/Save/Apply and equivalent affirmative actions across
shared dialogs and feature screens, then apply it consistently with native states, sizes and visual centering.
Preserve cancel/secondary meaning, loading/disabled states and localized text fit. Navigation/mode toggles are not
affirmative confirmations merely because they are currently blue.

Source: `UIMailBox/Bottom/ObjectsDisplayed_Unread/ReceiveAll/Btn`, with gold `Common_Btn_BG`,
`Common_Btn_Normal_Y_S_Pt` decoration and native ink. Only derived widget values ship; sprite pixels still come
from the user's game. Shared rendering preserves asynchronous thumbnail sources and secondary/disabled states.

## BL-030: ADB and Japanese-server validation with accurate support disclosures

- Status: Audit planned. Device/server validation and Settings changes not started; owner requested backlog only.
- Requested: 2026-10-26.
- Details: [Evidence matrix and requested notice](feedback-2026-10-26.md#bl-030-detail-adb-and-japanese-server-support-evidence).

Separate implemented paths, synthetic tests, isolated real-resource checks, actual device/server flows and owner
acceptance. Audit relevant detection, conversion, import/application/restore, export and ADB transfer/recovery.
Do not claim end-to-end support from code presence alone. Add the requested boxed experimental-information notice
for the Japanese-server setting if it remains unverified/unusable, and update README descriptions from evidence.
The Chinese draft now states the unverified boundary; testing details await the audit. Other languages still wait
for review. This is independent of the deferred ADB UI migration (BL-016) and does not authorize production access.

## BL-031: Light and dark Chinese README logo variants

- Status: Accepted by the owner (2026-10-27).
- Requested: 2026-10-26.
- Evidence: Screenshots 1 and 2 in the [feedback record](feedback-2026-10-26.md#supplied-evidence-index).

Place the supplied Chinese wordmark in the marked lower-right area under the BMPT logo. Prepare matching light-
and dark-background versions with legible colors and consistent alignment/proportions. Change Chinese README
branding only; keep other-language assets untouched and hand candidates to the owner for visual acceptance.

## BL-032: Compact photography occlusion control

- Status: Closed (2026-10-27): superseded by BL-040, which the owner accepted.
- Requested: 2026-10-26, following local alpha.1 review.
- Parent: BL-023.

Reduce the excessive width of the Occlusion Guide button. Keep its position to the left of the grid toggle,
visual centering, localized readability and usable pointer/touch target. Do not enlarge or displace surrounding
controls to accommodate it. Final visual acceptance belongs to the owner.

The native button is compacted with a fixed right edge and measured localized label fit; neighbouring controls retain their geometry.

## BL-033: Remaining frame-not-ready photography modal

- Status: Accepted by the owner (macOS review of 0.6.0-alpha.2, 2026-10-26); Windows not reviewed.
- Requested: 2026-10-26, following local alpha.1 review.
- Parent: BL-023.

The owner still sees the frame-not-ready modal, so the previous targeted handling did not cover every affected
path. Trace the actual failing capture/save/apply path and error propagation rather than treating the existing
retry or message suppression as proof of a fix. Recover transient readiness in the background while preserving
framing, pose, playback and session ownership; finish with a valid, durably saved image. Prevent duplicate or stale
publication. Keep genuine storage/format/permanent failures distinguishable and never display a false success.

Redraw readiness was outside the retry boundary, transparent frames passed the canvas check, and uncoded failures used the readiness caption. Capture now retries preparation and pixel readiness with session ownership, reports permanent failures separately, and preserves image/manifest consistency on publication failure.

## BL-034: Correct gold-button scope and applied-Mod export label

- Status: Accepted by the owner (macOS review of 0.6.0-alpha.2, 2026-10-26); Windows not reviewed.
- Requested: 2026-10-26, following local alpha.1 review.
- Parent: BL-029; export wording also relates to BL-004.

Use explicit action semantics to correct these owner-observed cases:

| Location/action | Required treatment |
| --- | --- |
| Portrait source selection > Save | Official gold affirmative prefab |
| Memorial source selection > Save | Official gold affirmative prefab |
| Portrait/memorial source selection > Update | Normal non-gold style |
| Settings > Mods > Export Mod Library | Normal non-gold style |
| Photo gallery > Export | Normal non-gold style; explicitly confirmed by the owner |
| Settings > Mods > Export Applied | Rename the Simplified Chinese label to `导出已应用的Mod` |

The requested Simplified Chinese Save label is `保存`; Export Mod Library is `导出Mod仓库`, and the previous
applied-export label is `导出已应用`. This wording change alone does not request a new color for applied export.
Keep secondary, disabled and loading states intact. A broad name-based rule must not color Update or the library
export merely because other affirmative actions use gold. Owner review remains the visual acceptance criterion.

Owner clarification confirmed that the photo gallery's Export must use the normal non-gold style. This is a
confirmed backlog requirement; it does not authorize implementation in this documentation-only update.

Source-picker primary Save now carries an explicit affirmative semantic. Update and both export entry points retain normal styling; the applied export label is localized in all six languages. Styling no longer infers meaning from names or captions.

## BL-035: Fine seam in the Mod switch's On segment

- Status: Accepted by the owner (macOS review of 0.6.0-alpha.2, 2026-10-26); Windows not reviewed.
- Requested: 2026-10-26, following local alpha.1 review.
- Parent: BL-026.

A fine line is still visible on close inspection of the top-left Enable Mods > On button. The earlier general
rendering corrections did not resolve this specific artifact. Inspect this control's actual rendering path,
including its scaled segment edges and active-state paint, before choosing a correction. Preserve the native
shape, colors, text and click geometry; do not hide the defect by reducing image quality. Owner visual acceptance
and accurately reported platform/DPI coverage are required when implementation is authorized.

The switch bypassed NGUI's contiguous raster slicing: its active CSS mask and inactive border image sampled fractional tile edges independently. Both states now use the shared high-quality NGUI raster path, retaining the sprite, inclination, colors and hit box. Geometry checks cover stage scales 0.5–2 and device pixel ratios 1–3 with a raster of at least 2× that increases to the effective display density; native WebView visual acceptance remains pending.

## BL-036: Consistent asset deletion for official, previewed and applied sources

- Status: Accepted by the owner (macOS review of 0.6.0-alpha.2, 2026-10-26); Windows not reviewed.
- Requested: 2026-10-26, following local alpha.1 review.
- Related: BL-015/BL-022 photograph protection, BL-001 atomic operations and existing library-source deletion.

Apply the owner's deletion rule consistently across user-managed assets, including photographs and portrait,
memorial and model sources. Official asset entries are the only inherently undeletable class: omit their Delete
control entirely rather than showing a disabled button. Determine official provenance from the actual source,
not its display label, matching CRC or whether the asset is selected.

| Asset state | Required behavior |
| --- | --- |
| Official | No Delete control; reject direct deletion requests as well |
| Non-official, selected only for App preview | Allow deletion; preview selection is not a lock |
| Non-official, applied/configured for game use | Keep Delete available; ask for confirmation before deletion |
| Other non-official managed assets | Allow normal deletion without an in-use restriction |

Required Simplified Chinese applied-asset confirmation: `该资产正在被应用中，是否删除？` Cancelling retains the
asset and all choices. Confirming removes it through the existing protected writer/rollback flow and reconciles
the affected game/configuration state. Include configured application deferred by the Mod master switch; with
Mods off, reconcile desired configuration without performing game writes. Preserve other choices in shared
Collection bundles and unrelated assets. Failed cleanup/write/metadata publication must not report a successful
deletion or leave dangling references.

Deleting an App-preview selection must clear or replace its stale reference with a valid available fallback.
Sharing photographs across skins means checking every affected preview/game reference, not only the open skin.
Generated/custom assets must not be silently reclassified as official merely to retain the previous lock. This
requirement supersedes the earlier default/App-selected/custom-applied blanket protections; only genuine official
assets keep permanent deletion protection. Runtime resources outside user-managed asset/library choices are not
targets for a bulk filesystem-delete operation.

Official provenance controls Delete visibility and direct-request rejection. Managed previews can be deleted with fallback across skins; applied/deferred choices require confirmation. Source, shared Collection recipes, protected game writes and metadata cleanup share a recoverable undo transaction; Mods-off deletion changes configuration without game writes.

## BL-037: Design native window controls integrated into the top toolbar

- Status: Accepted by the owner (macOS review of 0.6.0-alpha.7, 2026-10-27); Windows not reviewed.
- Note: Removed immersive drag strips and the Window menu/timer; window notifications restore Edit and guarded Quit while accepted bar dragging and double-click remain unchanged.
- Requested: 2026-10-26.
- Parent: BL-024.

Develop a more native integration of macOS's three top-left window buttons and Windows's three top-right window
buttons into the application's existing top toolbar. Keep the platform-appropriate placement and native window
behavior. The task includes conceiving the layout and interactions before implementation; do not treat the
current custom control group as the accepted final design.

The future design should account for native control space, alignment with App actions, draggable versus clickable
areas, resizing, maximize/fullscreen, keyboard/accessibility behavior and narrow-window layouts. Preserve the
existing unsaved-change and active-writer close handling. Avoid overlapping native controls with Back, Settings,
Home or the Mod switch. The owner's latest clarification explicitly defers the design itself into the backlog;
no mockup or platform feasibility investigation is part of this documentation-only update.

Owner decisions on `docs/design/window-controls.md` (2026-10-26): try M1 (real macOS traffic lights in the
toolbar row, moved vertically to the bar's centre line) and W1 (system caption buttons top right; their vertical
position follows the system). The owner gives feedback after the experiment. Narrow windows: keep one row and drop
the "Enable Mods" caption text, keeping the switch and its help badge. Windows 11 is the primary target; Windows 10
keeps working without dedicated optimisation. Close: every quit origin uses the existing unsaved-change prompt,
a clean close needs no prompt, active writers still refuse. Immersion: native controls stay with a small drag
background; add only the window/fullscreen keyboard commands needed.

Owner review of 0.6.0-alpha.5 on macOS (2026-10-26; Windows not reviewed yet):

1. Dragging the blank top bar does not move the window, and double-clicking it does nothing. Lead's diagnosis: the
   native row left pywebview's drag selector (`.topbar:not(.native-toolbar)`) and relies only on the
   `ToolbarDragView` overlay (`mouseDownCanMoveWindow` with `setMovableByWindowBackground_`); nothing handles a
   double-click, contrary to the comment in `window_macos.py`. Proposed: handle a left mouse-down inside the reported
   drag rectangles natively (`performWindowDragWithEvent:`) and a double-click with the system's title-bar
   double-click action.
2. Immersion: no small drag area beside the native controls (same cause).
3. The Window menu does not appear. The cause could not be determined from the code.
4. The fullscreen portrait, the memorial lobby fullscreen and the lobby camera still show the white top bar and its
   Back over the page's own Back (two Back buttons). Lead's diagnosis: the header moved from `#stage` into
   `.window-header`, so `.stage.fullscreen .topbar` and `.stage.hall-fullscreen .topbar` no longer match, and
   `.window-immersive .window-header .topbar > *` hides the bar's children but not its background or the Back.
   Owner (2026-10-26): audit all four scenes, the portrait fullscreen, the memorial lobby (L2D) fullscreen and the
   camera opened from each, plus Student Hall's fullscreen; nothing else is known to be wrong in the camera UI
   (the two-line Occlusion Guide label is BL-040's accepted wrap).
5. Page titles are cut off although the row has room: BL-049.

Owner review of 0.6.0-alpha.6 on macOS (2026-10-26):

1. Dragging the blank bar and the double-click action: accepted.
2. The small drag area beside the native controls in immersive scenes is not wanted: remove it (both platforms).
3. The Window menu appears but is not wanted: remove it, with its 0.25 s repair timer. Keep the Edit menu and the
   existing keyboard commands.

## BL-038: Reflow Settings when Home is hidden in Student Hall

- Status: Accepted by the owner (macOS review of 0.6.0-alpha.2, 2026-10-26); Windows not reviewed.
- Requested: 2026-10-26.
- Parent: BL-028; coordinate native-control safe space with BL-037.

When already in Student Hall, Home remains hidden, Settings moves right into the space Home previously occupied,
and the separator between those actions is removed. Keep the appropriate outer margin/native-control clearance,
visual centering and hit target. Other pages retain their Settings/Home arrangement and separator. Returning to
or leaving the Hall must update the layout consistently without an empty Home-sized slot or a stray divider.

The Hall centers the native Settings sprite in Home’s former box and hides the divider in the same layout update. Other pages restore the prefab sibling geometry; overlapping native controls retain clearance.

## BL-039: Missing Japanese directory causes server-switch error and blank Hall

- Status: Accepted by the owner (macOS review of 0.6.0-alpha.2, 2026-10-26); Windows not reviewed.
- Requested: 2026-10-26.
- Related: BL-030 support audit, but this is a separately reported settings/navigation failure.
- Evidence: `codex-clipboard-dd0d548d-05d4-46af-a634-31af2a05b43c.png`, supplied by the owner.

Observed sequence: in Settings, switch from the Global server to the Japanese server when no local Japanese game
directory is available. The App shows this internal error:

```text
AttributeError: 'types.SimpleNamespace' object has no attribute 'names'
```

The Student Hall then becomes blank. Restarting restores the display. This does not establish a root cause,
whether the server preference was retained, or whether any saved Hall configuration changed.

When implementation is authorized, reproduce with isolated configuration/resources, trace missing-directory
handling and the failed refresh, and identify the actual incomplete record reaching name display. Missing game
resources must produce an actionable missing-directory state rather than an AttributeError. A failed or cancelled
switch must preserve a usable screen and saved layout/selection, and recovery must work without restarting.
Avoid committing stale cross-server refresh results, silently clearing Hall data or writing game files as a side
effect of changing this setting. Owner UAT remains the acceptance step.

Stored model fallback records lacked display metadata when the selected catalog had no installed entry. Offline metadata and complete fallback records now cover name display. Server changes validate the directory before saving; missing resources show a folder-selection message, retain the current server and Hall data, and recover in-session. Refresh ownership rejects stale server results.

## BL-040: Wrap the English Occlusion Guide label

- Status: Accepted by the owner (macOS review of 0.6.0-alpha.3, 2026-10-26); Windows not reviewed.
- Requested: 2026-10-26.
- Parent: BL-032.
- Evidence: owner screenshot of the English photography toolbar.
- Note: Occlusion Guide and the grid toggle share one caption size fitted to the prefab body on up to two lines;
  English wraps as two lines inside the button.

The compact Occlusion Guide button from BL-032 overflows in English. Wrap the label inside the native button instead
of letting it spill out; keep BL-032's compact width, fixed right edge and the neighbouring controls' geometry. The
Chinese label already fits. Check every shipped language, not only English.

## BL-041: Text fit and one font size for source action buttons

- Status: Accepted by the owner (macOS review of 0.6.0-alpha.3, 2026-10-26); Windows not reviewed.
- Requested: 2026-10-26.
- Related: BL-034 and BL-036 (source-picker actions).
- Evidence: owner screenshot of the English L2D source picker (Apply, Show in Finder, Check Compatibility, Update,
  Delete).
- Note: one caption size per same-kind group (shared group fitting), wrapping on up to two lines; applied to the
  source pickers and the other button rows, tabs and segments. Long English, Thai and Korean rows give the widest
  caption a wider body. The owner accepted the wider body for the longest caption.

In the L2D source picker, Check Compatibility overflows its button, and the buttons in the same row use different
font sizes (Update is smaller than Delete). Long labels wrap inside the button; buttons in one row share one font
size that fits all of them. Chinese shows no problem; audit every other language on every source picker (portrait,
memorial, L2D, model, thumbnail) and the other action rows. The owner asked for this to become a standing UI design
and review rule, kept in a UI guideline file rather than in the general agent rules.

## BL-042: Restore Default on thumbnails creates an orphan generated default

- Status: Accepted by the owner (macOS review of 0.6.0-alpha.4, 2026-10-26); Windows not reviewed.
- Note: Restore reuses the variant’s stored generated default; gallery identity and applied usage now match deletion checks.
- Requested: 2026-10-26.
- Related: BL-022 (generated defaults) and BL-036 (deletion rules).

Observed: in the thumbnail picker, with a user-made thumbnail selected, Restore Default adds another generated
default thumbnail. The new entry shows no Applied marker, yet Delete answers that the asset is in use
(`该资产正在被应用中，是否删除？`). Restore Default must reuse the existing generated default and create one only
when none exists. The Applied/preview markers and the deletion check must read the same state, so the in-use
confirmation appears only for assets that are actually applied.

## BL-043: Top bar vertical centring and title size

- Status: Accepted by the owner (macOS review of 0.6.0-alpha.3, 2026-10-26); Windows not reviewed.
- Requested: 2026-10-26.
- Related: BL-038 and BL-037.
- Evidence: owner screenshot of the Students top bar with a red line through the bar's centre.
- Note: content centred on the fill of `Common_Top_Menu_Bg` by ink (text by its script's reference glyph, bitmaps
  by their drawn rows); the title uses the prefab's `Label_Title` size 66 in the App's per-script drawing size. The
  owner kept the larger Latin title and the fill centre (not the whole bar including its dark edge).

The top bar's content is not centred on its background: the right-hand actions (Settings, Home) sit a few pixels
high and the left-hand items low. The current page title (e.g. 学生) is larger than the bar's other text and not
consistent. Measure against the game's top bar prefab and name it; align each element's visual centre with the
bar's centre and use the prefab's title size on every page. Check all pages and languages at several window sizes
and display densities.

## BL-044: Applied and preview frames from the game's selected skill cards

- Status: Review 1 accepted by the owner (macOS, alpha.6); review 2 closed as won't fix by the owner; Windows not reviewed.
- Note: Both official badges have identical RGBA columns [14, 28), giving horizontal borders L14/R36 and no vertical cuts. Applied/Selected share a width and font size per locale/card, starting at Mulligan_IndexLabel's 42 units and shrinking only at the card body width limit; the check retains its original prefab scale. No image data ships.
- Requested: 2026-10-26.
- Related: BL-015, BL-022 and BL-036.
- Evidence: owner screenshots of the game's Formation Edit > starting skills > selected skill cards (animated yellow
  and blue frames, numbered top-right badges with a check).

The portrait, L2D and thumbnail pickers (and the other source pickers) tell the applied choice from the one being
previewed. Show both with the game's animated selected-card frames, built from the game's prefab (identify and name
it), not CSS. Move the Applied marker from the top-left corner to the top-right, in the numbered check-badge style.
Proposed mapping for owner review: applied = yellow frame with the check badge, previewed = blue frame.

Owner review of 0.6.0-alpha.4 (2026-10-26), two screenshots:

1. Every badge shows its check twice. In the yellow (applied) badge the content must be the text "已选择" (the game's
   own wording for a selected card in each language), not a grey check icon.
2. The animated frames, yellow and blue alike, show extra dark areas at the card's corners, outside the rounded frame.
   Lead's diagnosis to confirm: the game's `FX_SHADER_AlphaBlend_Add_Mask_0` (blend One / OneMinusSrcAlpha, linear
   colour space) mostly adds light; the App draws the effect into its own WebGL canvas with non-premultiplied alpha
   and lets the browser blend it in display (gamma) space over the card. Where the scrolling mask is dark but its alpha
   is not zero, which includes the mesh's corners outside the rounded frame, the effect dims the card behind instead of
   adding light.

Addressed, awaiting review: the badge sprite (`Common_Icon_Mulligan_1`) already draws a check, so the grey check cloned
from `SelectLine/Sprite` is gone and the prefab's number label (`Mulligan_IndexLabel`) reads "Selected" instead. The
ring mesh has no corner fill: it is a 9 px outline whose alpha is only the mask, while its HDR light saturates at mask
0.15 (yellow) and 0.03 (blue). The straight-alpha canvas weighted that light by the mask, up to 0.85 (yellow) and 0.97
(blue) darker than the game's One / OneMinusSrcAlpha result, and dimmed the card by (1 - alpha) in sRGB values, up to
0.29 over white. The canvas is now a premultiplied layer that is never darker than the game's blend; which of these
showed at the corners is for the owner to judge.

Owner decisions (2026-10-26): keep the new glow compositing for review. Badge text by state: the yellow (applied)
badge reads the existing "Applied" label (`webui.status.applied`, zh-CN `已应用`); the blue (previewed) frame gets the
prefab's blue badge (`Common_Icon_Mulligan_2`) reading "Selected" (`webui.status.selected`, zh-CN `已选择`).

Owner review of 0.6.0-alpha.5 on macOS (2026-10-26):

1. The badge captions overflow the badge ("Applied", `已应用`, `已选择`), in the other languages too. Owner request:
   widen the badge to the left until the caption fits.
2. A little uncovered base colour still shows at the frame's top-left and bottom-left corners.
3. Two thumbnails show Applied at once: BL-053.

The badge sprites (`Common_Icon_Mulligan_1`/`_2`, 64x69) have no slice borders, so NGUI could only stretch them whole.
Owner decision (2026-10-26): widen the badge by slicing the original sprite at runtime. The App keeps reading the
sprite from the game; it defines its own horizontal slice borders (the rounded left end, the right part with the
check, and between them a run of identical pixel columns) and repeats only that run leftward, so the check and the
corners keep their shape. Only the border numbers ship, verified numerically against the official sprite.

Owner review of 0.6.0-alpha.6 on macOS (2026-10-26): review 1 (badges widened by runtime slicing) accepted. Review 2 (base colour
at the frame corners) is not fixed by alpha.6; the owner stops work on it (won't fix). The owner keeps alpha.6's art
coverage change (no revert).

## BL-045: Separate Global and Japanese game directories

- Status: Accepted by the owner (macOS review of 0.6.0-alpha.3, 2026-10-26); Windows not reviewed.
- Requested: 2026-10-26.
- Related: BL-039 and BL-030.
- Evidence: owner screenshot of the dialog shown when switching to the Japanese server (title `失败`, message
  `请在设置中选择此服务器的游戏目录。`, buttons Generate Error Report and Confirm).

Settings has no place to choose a Japanese game directory, so that message cannot be acted on. Add separate Global
and Japanese game directory settings, using each language's official server names, and assign the existing single
directory to the server it belongs to. When the selected server's directory is not set, show a plain notice instead
of a failure: no `失败` title and no error-report button, a message such as `未设置日服游戏目录`, and Confirm only.
Keep BL-039's behaviour: the current server, Hall and saved choices stay as they are.

Settings exposes both server folders, migrates detected legacy Japanese installs and shows a Confirm-only notice for missing folders; refused switches retain the current server, and resource caches remain isolated by installation.


## BL-046: Managed CLI CAB cache and upstream introspection tests

- Status: Closed by the owner without UAT (2026-10-26): CLI-only; the App engine is unaffected apart from batched CAB index saves and preview-cache clear detection, covered by tests.
- Requested: 2026-10-26.
- Related: BL-012 and BL-014.
- Note: CLI discovery shares managed scoped/versioned CAB metadata, publishes interrupted scans before output,
  and tests local and external PPtrs separately.

The CLI `inspect`/`trace` cache still defaults to `Addons/cab_cache.json` beside the program: unbounded, not keyed by
the game's resource version, not cleared from Settings, and since the latest upstream sync rewritten in full on every
CAB hit. Move it onto the App's managed CAB index/preview cache (bounded, versioned, cleared in Settings) and retire
the file. The upstream test `tests/test_core/test_introspect.py` expects every non-null PPtr to resolve in local-only
mode without separating local (`m_FileID == 0`) from external references, which official bundles with external
references contradict; test the two cases separately and add tests for `--local-only`, cache isolation and
interrupted runs. Upstream's CLI options and the shared UnityFS reader are already in `main`.

## BL-047: Fullscreen button left on a rotated fullscreen portrait

- Status: Accepted by the owner (macOS review of 0.6.0-alpha.7, 2026-10-27); Windows not reviewed.
- Requested: 2026-10-26.
- Related: the portrait view's rotate and fullscreen controls.
- Note: The prefab's RightTop button switches portrait and memorial lobby fullscreen; skins without a lobby omit it.

In the portrait view, after rotating the screen and entering fullscreen, the fullscreen button is still shown in the
top-right corner. It must not be shown once the view is fullscreen; leaving fullscreen restores it.

Owner correction (2026-10-26): the top-right button stays; its function should be switching the fullscreen view
between the portrait and the memorial lobby. The removal of the button is reverted; the new function awaits the
owner's confirmation of the exact behaviour.

Owner confirmation (2026-10-26): in the fullscreen view the top-right button (double-arrow icon kept) switches
between this skin's portrait and its memorial lobby; the Hide UI function is dropped.

Owner review of 0.6.0-alpha.5 on macOS (2026-10-26):

1. Switching first moves the portrait back to the detail page's initial position and only then shows the memorial
   lobby. It must switch at once, without restoring the position.
2. The button touches the top edge, under the top bar that should be hidden there: BL-037 item 4 and BL-052.

Owner review of 0.6.0-alpha.6 on macOS (2026-10-26): the direct switch is accepted. The top-right switch must use the cycle-arrows
icon `Common_Icon_Change` (the Student Hall Flip action's icon, owner screenshot) instead of the double arrow.

## BL-048: Remove the CLI as a built and installed command

- Status: Accepted by the owner (2026-10-26).
- Requested: 2026-10-26.
- Related: BL-046.

The App does not need a dedicated command-line entry; scripts that can call the functions are enough. The classic
GUI stays for users who still use it. The `bamt-cli` command, the Windows CLI executable and CLI release uploads are
removed; `python -m ba_modding_toolkit.cli` keeps working from a source checkout, and the App and the classic GUI are
unchanged.

## BL-049: Page titles cut off; titles at the Enable Mods caption size

- Status: Accepted by the owner (macOS review of 0.6.0-alpha.7, 2026-10-27); Windows not reviewed.
- Note: Removed visible top-bar titles and title fitting; page names label the header, and Enable Mods occupies the former title/Back slot with native clearance and caption-only compacting.
- Requested: 2026-10-26.
- Related: BL-043 (title size) and BL-037 (single-row fitting).

Page titles are cut off with an ellipsis on every page and in every language although the row has free space
(`Student H…`, `学…`). Owner decision: draw page titles at the same size as the "Enable Mods" caption, replacing
BL-043's title size. A title is shortened only when the row really has no room left.

Owner review of 0.6.0-alpha.6 on macOS (2026-10-26): the caption-sized titles are no longer noticeable. Owner decision: remove
the page titles from the top bar on every page.

## BL-050: No Back button in Student Hall

- Status: Accepted by the owner (macOS review of 0.6.0-alpha.6, 2026-10-26); Windows not reviewed.
- Note: Hall hides Back and uses its title slot with native clearance; leaving restores the current route and BL-038 action geometry.
- Requested: 2026-10-26.
- Related: BL-038.

Student Hall is the App's home: remove the top bar's Back button there. Other pages keep Back.
Owner decision (2026-10-26): the title moves left into the Back's place, keeping the native-control clearance.

## BL-051: Hairlines at the top bar

- Status: Accepted by the owner (macOS review of 0.6.0-alpha.6, 2026-10-26); Windows not reviewed.
- Note: The bar and scrollbar use the shared contiguous NGUI raster; the switch already used it. Geometry checks retain the source cuts, caps, colours and shadow at scales 0.5–2 and DPR 1–3.
- Requested: 2026-10-26.
- Related: BL-026 and BL-035.

Thin lines show at the top bar: at its right end beside Settings and at its top left beside the title. The owner
asks what these recurring lines are. Lead's diagnosis to confirm: the bar's `Common_Top_Menu_Bg` is drawn with a CSS
`border-image` 9-slice (172 px left, 11 px right) under a fractional `transform: scale()`; WebKit draws each slice on
its own, so the slice edges fall between device pixels and blend into a faint line. BL-035 removed the same kind of
seam from the Mod switch with the shared NGUI raster path (`ngui.js` `slicedImage`, one baked image).
`mod-switch-skin.js` and `scrollbar.js` still use `border-image`.

## BL-052: One corner-button margin across the App

- Status: Accepted by the owner (macOS review of 0.6.0-alpha.8, 2026-10-27); Windows not reviewed.
- Note: Native-control clearance uses visible button bodies across fullscreen views and both cameras; prefab hit areas stay intact, and the remaining alpha.7 macOS acceptance stands. Windows not reviewed.
- Requested: 2026-10-26.
- Related: BL-037 and BL-047.

Corner buttons sit at different distances from the edges. Student Hall's three top-right buttons hang too low (owner:
move them up to just below the bar); the fullscreen view's top-right button touches the top edge (owner: move it
down). Use one margin from the edges for corner buttons throughout the App.

Owner decision (2026-10-26): the margin is the game prefab's distance of the student detail page's top-right
buttons from the screen edge. Measure it from the top bar's lower edge where the bar is shown and from the window's
top edge in fullscreen views; the same horizontal margin from the window's side edge.

Owner review of 0.6.0-alpha.6 on macOS (2026-10-26): the window-edge margins show no problem so far, including non-16:9 windows.
In the portrait and memorial lobby fullscreen views and in both cameras, the top-left Back must sit at the same height
as the top-right button; the lead's assumption of measuring below the native row is rejected. A real overlap with
native window controls is resolved along the edge, not downward. The camera's Back must use the same button as the
fullscreen views (`UICharacterVisualMode/Inventory_ListControl_PC/LeftTop/Btn_Back`), not the top bar's round Back.

Owner review of 0.6.0-alpha.7 on macOS (2026-10-27): the Back sits in different places in the portrait fullscreen
(pushed right of the traffic lights) and the memorial lobby fullscreen (at the left margin, below the traffic lights).
Owner decision: both use the memorial lobby's placement. Only the visible button body decides whether it would cover
the native controls; its larger prefab collider does not push it aside. Left and right top controls stay level.

## BL-053: Thumbnail picker marks other skins' applied photos

- Status: Accepted by the owner (macOS review of 0.6.0-alpha.7, 2026-10-27); Windows not reviewed.
- Note: Photo deletion reports target skins and distinguishes other-skin-only usage in all six locales; shown-skin usage keeps the existing warning.
- Requested: 2026-10-26.
- Related: BL-015, BL-022 and BL-044.

The thumbnail picker shows Applied on two photos at once: the one applied to the alternative skin and the one applied
to the original skin. Only the photo applied to the shown skin's Collection portrait is Applied. Lead's diagnosis:
`PhotographyService.list` sets `applied` from `photograph_removal_info(...)["used"]`, which is true when the photo is
applied to any target. The delete warning for photos in use elsewhere stays as it is.

Owner review of 0.6.0-alpha.6 on macOS (2026-10-26): Applied per skin accepted. The delete warning for a photo applied to another
skin must say so: zh-CN `正在被另一换皮使用` (owner wording), translated in the other locales; a photo applied to the shown
skin keeps the existing warning.

## BL-054: Mod switch outside the App

- Status: Deferred by the owner (2026-10-27) for further research; design proposal in `docs/design/mod-switch-shortcut.md`, not approved; implementation not started.
- Requested: 2026-10-27.
- Related: the top bar's Enable Mods switch, BL-037 close handling.

Settings gets a button to generate an out-of-App Mod switch ("生成APP外MOD开关"). It creates a "MOD开关" file in a
folder; the user can put a shortcut to it on the desktop. Opening that file has the same effect as the App's Enable
Mods switch and shows a short, plain UI notice of the result. Open design questions before implementation: where the
file goes on each platform (packaged macOS builds cannot write next to the app), the file type on macOS and Windows,
behaviour while the App is running or a game writer is active, and what the notice looks like.

Owner decision (2026-10-27): not in 0.6.0. The design (M1 generated applet on macOS, W1 `.lnk` on Windows, command
forwarding to a running App, a window-less transient mode with a small result window; estimated by the designer at
8-12 engineering days) awaits the owner's research; no feasibility build either.

## BL-055: First launch of 0.6.0-alpha.10 hangs on macOS

- Status: Accepted by the owner on macOS (0.6.0 pre-review, 2026-10-30); the early loop-exit trigger stays logged and unresolved.
- Requested: 2026-10-27.
- Evidence: macOS hang report from the owner (`error report.txt` in the repository root, git-ignored, never commit
  it); the App process section is extracted to the lead's private brief folder.

The first launch after installing alpha.10 froze (macOS "hang" event, about 33 s unresponsive, 32 s after start).
Lead's first reading: the main thread waits on a condition variable inside `main` while pywebview's `loaded` handler
thread is in `webui/main.py` `_run_window.on_loaded` -> `native.watch_drops` -> pywebview DOM event registration
(`webview.dom.event.__iadd__` -> `element._exists`), which needs the main thread; a possible deadlock. To confirm.

Investigation (2026-10-27): the local Python 3.12.11 library has the same Mach-O UUID as the report's Python
library. Symbolicating the main-thread addresses resolves `Py_Exit -> Py_FinalizeEx -> wait_for_thread_shutdown`,
then a Python thread-lock acquisition. This is interpreter shutdown waiting for a non-daemon thread, not the
normal native startup loop waiting for `loaded`. The remaining Python worker is pywebview's non-daemon loaded
event handler: `watch_drops` registers a DOM handler through `Element.on`, whose synchronous Cocoa `evaluate_js`
queues work with `AppHelper.callAfter` and waits on an unbounded result semaphore. With the native loop ended,
that result cannot arrive; shutdown waits for the worker and the worker waits for the stopped GUI thread. The
`_exists` frame is a decorator around `on`, not itself a DOM existence query. The lead's dependency is confirmed,
but its lifecycle interpretation needs this correction.

The extracted stacks do not establish why the GUI loop ended. The App's close coordinator, menus and chrome
were traced, but no sampled frame identifies that earlier trigger. Candidates and code evidence:

- Early close/quit overlapping registration: Cocoa's `windowWillClose_` stops the application loop after the last
  window closes; the App's asynchronous close coordinator can approve closing before the page loads. A logged
  closing/closed sequence before loop return would establish that route, though not which input requested it.
- Native loop return without the App's close callbacks: Cocoa's `first_show` runs the loop only if the application
  is not already running, and its `WebKitHost.keyDown_` has a direct Command-Q `app.stop_` route. The App installs
  an interceptor for ordinary Command-Q, so this is a candidate bypass, not an established cause. Return without
  closing/closed markers would separate this class from the guarded close route; native event evidence would
  still be needed to identify the particular bypass.
- Exception/exit unwinding the native loop: `_run` catches ordinary startup exceptions after `_run_window` and
  `_serve` cleanup; `SystemExit` also reaches interpreter shutdown. Finalization is compatible with these routes,
  but the stacks contain no earlier exception. A loop exception-type marker and the existing startup error log
  would distinguish this from a normal return.

The initial diagnostic change followed the brief's fallback, without a speculative lifecycle fix or pywebview patch. INFO logs
record initialized/before-show/shown/loaded/closing/closed, each drop registration stage, native loop return
versus exception type, cleanup state and completion before the session log closes. They contain only stage names,
booleans and renderer/exception types, with no paths, dropped names or exception contents. The deciding evidence
is the next session log's sequence: close callbacks before return, a loop exception, or return without close;
`drops=running` plus an unfinished registration stage identifies the shutdown overlap.

Registration is unconditional on every document load. Migration and library warming do not gate this handler;
consent and automatic tours run in the page after its startup/first-screen sequence. The alpha.9-to-alpha.10
source changes affect tour/popup sizing, not native event dispatch or shutdown. No first-launch-only cause is
proved; slower first-launch work can affect timing, and the shutdown overlap can recur on later launches or
reloads. Windows also registers from the non-daemon loaded handler; WebView2 synchronously invokes its UI thread
and waits for a result semaphore. A similar overlap is possible there, but the macOS stack does not prove a
Windows occurrence. The report's duration is not a registration timeout or proof of recovery.

Headless regression fakes stop the GUI loop during each of the four registrations and reproduce a shutdown
join blocked by the loaded worker, then supply the missing GUI result to release both threads. They verify the
diagnostic distinction, exception propagation, unchanged drop options/path delivery and close return values;
they do not claim to prove a hang fix. No prefab, geometry, startup ordering, tour or locale wording is changed.
Native launch/close reproduction and visual acceptance remain the owner's; Windows is not reviewed.

J2 fix (2026-10-27, owner authorized): the pywebview `loaded` callback now hands document/native setup to an
App-owned daemon worker and returns without waiting for JavaScript. The four drop registrations retain their
order, Python callbacks, prevent-default/stop-propagation options and dragover debounce on every live load,
including reloads and a vetoed close. There is no registration timeout that could discard a slow normal launch.
An approved close, `closed`, or native loop exit marks the window stopped: late load work and queued chrome
installation are skipped, remaining registrations are abandoned, and late drop callbacks no longer publish.
A registration already waiting inside Cocoa or WebView2 may remain blocked, but its daemon worker cannot hold
interpreter shutdown. No site-packages changes are needed; Windows still queues chrome installation with
`BeginInvoke`, never evaluating JavaScript on the GUI thread. All earlier lifecycle/drop diagnostics remain.

Other App window handlers were checked: shown/maximized/minimized/restored only queue native refresh work
(`callAfter`/`BeginInvoke`) or publish local state; initialized/before-show do not wait for JavaScript; closing
retains the synchronous approval result and its asynchronous close coordinator already uses daemon page-call
workers with bounded waits. Closed still signals that coordinator and handles prepared update helpers. These
handlers do not wait for a result from a stopped GUI loop. Native chrome, close approval and writer admission
are unchanged. The load worker owns no writer or instance lock: `_serve` still stops the server and calls
`Services.close` to drain writers, and `main` releases the instance lock in its existing `finally` path.

Headless regressions now cover a stalled GUI result at each of the four registration stages for Cocoa and
WebView2, including real subprocess shutdown without releasing that result, normal/reloaded/vetoed-close drop
registration and path delivery, late work suppression, GUI-dispatched chrome setup and the other asynchronous
window handlers. A lifecycle integration check drains a real test writer before cleanup completes and then
reacquires the released instance lock while the registration worker is still blocked. The shutdown deadlock
is fixed; why the native loop ended about one second after startup is still unknown. No prefab, geometry or
locale text changed. Native macOS/Windows launch, drag-and-drop, close/menu/chrome behaviour and visual acceptance
remain for owner UAT; Windows is not natively reviewed.

## BL-056: AI-assisted refinement of official portraits (funnel)

- Status: Funnel (2026-10-27): idea being refined with the owner; not designed, not scheduled.
- Experiment report ready for owner review: [report and local image inventory](research/bl056-experiment.md).
- Pass-through assembly implemented; test mods built for owner review (2026-10-27; K4 engine, no UI integration).
- Composite generation and projection implemented; R1 and S2 test mods built for owner review (2026-10-27; K5 engine,
  no UI integration).
- Owner review of the K5 test mods (2026-10-27): halos must be masked and never modified. Joins still show a
  boundary (the waist, between the jersey and the shorts). Lead's check: the generated composite has no seam there;
  cutting it back created one, because edited texels faded back to official colour near every part boundary and the
  lower part's texels under the upper part's soft edge kept their official colour.
- Owner direction (2026-10-27): join the parts first, let the model edit the joined character, then cut the result
  apart along the part boundaries. No dispatch until the revised method is settled ("shit in, shit out").
- Revised method (lead, 2026-10-27), each stage gated, a failed gate stops the run:
  1. pose as the game shows it (idle and expression animations, face deforms included), with masked parts removed:
     halos, non-Normal blend slots, effects;
  2. generate on the whole character, tiling in composite space when the model's output is smaller than the frame;
     reject moved silhouettes, lines or lettering before cutting;
  3. cut with cover fill: every drawn texel of an unmasked part, visible or covered, takes the edited colour at its
     composite position, with no fade to official colour at joins; internal joins keep official alpha, the outer
     silhouette takes the generated outline within each part's mesh, and face overlays (expressions, eyelids) solve
     their alpha, since their lines are drawn in it; bleed refilled;
  4. edit each other expression's face with the base pass as fixed context and cut only its slot;
  5. re-render the packed mod for every expression and idle frames: masked, untouched and alpha bytes identical, and no
     error spike in the join bands, before the owner sees it.
- R1b test mod built for owner review (lead, 2026-10-27): K5's R1 generations cut again with the revised method,
  no new generation. Join-band error against interior pixels of the same detail: waist 0.94 (K5 R1: 2.08), face edge
  0.59 (1.26), front hair edge 0.79 (0.86); silhouette IoU against the generation 0.992 (0.957). Halos byte-identical.
  Known residue: a faint light trace under the smile's mouth and above the right eye in the eye-close pose. Details in
  the [experiment report](research/bl056-experiment.md#10-r1b-re-cut-with-the-revised-method).
- Owner review of R1b (2026-10-27): good, no defect found. R1b is a local re-cut, not yet engine code; its
  structure is the simplest class (one page, one face slot, an idle that moves the whole body).
- Join, generate, cut implemented in the engine; R1b reproduced (2026-10-27; K6, no UI integration).
- Generation inputs, crop merge and prompt templates implemented; no generation yet (2026-10-27; K7).
- K9 seam stitching, outline keying, rigid crop alignment and calibrated gates implemented (2026-10-28). The lead
  kept join bands 3 px off the outer silhouette, where the waist band met thin outline structures (K5's seam still
  fails at 2.23, R1b passes at 0.93). R1c built from K8's returns for owner review: waist 0.972, face edge 0.699,
  front hair 0.978, silhouette IoU 0.996, import accepted. Head: K8's v1 retry (v2 still adds a hair outline).
- Owner review of R1c (2026-10-28): good, no defect found (the added hair outline included).
- Local seam gates tried and dropped (2026-10-28; K10 to K12, not merged). Band segments, character windows, an
  outline-colour strip and a visibility-calibrated line detector all missed most visible synthetic seams at a 0.8
  clean margin (best: 7 of 38). The render-against-target residual mixes seams with the cut's deliberate departures
  from the target, and masking those departures removed up to 23% of the base pose and nearly all expression areas.
  The enforced whole-band join and crop-boundary gates stay as they are; seam review stays visual.
- Finding from that work: in R1c's head the cut keeps only part of the generated sharpening (render-to-target
  difference about as large as render-to-official), while R1b keeps most of it. Likely cause: R1c reuses K5's
  expression targets, made from a different base head, in the head median vote. Next run: generate the expression
  crops from the new base head (the K7 expression inputs) before cutting.
  Owner (2026-10-28): not visible at normal size; no R1c rework. The rule applies to later runs.
- Spine artist route (2026-10-28 to 10-29; branch `codex/spine-artist`, not merged): images in (full body, one edit
  per expression, eye-close, from any image model), a new skeleton, atlas and page out; the official portrait serves
  only as reference (face position and size) and its halo is carried over unchanged. Utaha remake R1: Codex drew 10
  images (new pose and head angle), gave the face box, waifu2x enlarged the images 2x and the body was cut into two
  seamless regions; texture resolution 1.07 of official, import accepted.
- Owner review of the Utaha remake R1 (2026-10-29): accepted, very good. Finding: switching expressions visibly
  changes the whole head slightly, not only the features. Cause (lead): each expression patch covers the whole face
  and comes from its own redrawn picture, so bangs, skin shading and the face outline inside it differ per
  expression. Next: take only the features from each edit and the rest of the face from one base.
- Spine artist moved to a standalone repository, BA Spine Artist (owner decision 2026-10-29: not App-related yet;
  to be published by the owner later). Its build and mod reproduce the accepted R1 byte for byte; the branch
  `codex/spine-artist` is closed. Writing game bundles still uses this App's packer.
- R1c stopped after one retry (2026-10-27; K8): movement, overlap RMSE and crop-boundary gates still fail;
  no cut or mod was built. See experiment section 13 for the four-crop run and merge/key artifacts.
- Owner review of the K4 test mods (2026-10-27): D1 and D3 are acceptable as generated images, but the joins are
  the problem. D3 2x shows seams at the forearms and along the waist where the restored upper body meets the
  unprocessed lower body, rough edge pixels on the forearm, and a lower body visibly blurrier than the upper body.
  S1 is wrong (displaced halo, dark patches in the library thumbnail), so style repair cannot be judged yet.
- Requested: 2026-10-27.
- Related: the editor plan (portrait module before L2D; AI as an optional module with deferred dependencies), the
  portrait repair editor milestone, BL-016 through BL-018.

The owner is considering AI assistance on the official portraits the App already reads from the user's install:

1. Upscaling: a higher-resolution version of an official portrait. An earlier trial with Codex image generation
   improved the detail considerably.
2. Partial style repair: some later portraits are drawn less well than earlier ones (or the reverse). Give the model
   a base portrait plus reference portraits and let it bring the base's style in line with the references, for
   example through Codex (`codex:rescue`) or a similar image model.

Spine-related tooling or skills could help, because many portraits are Spine atlases (no Spine skill was found in the
available skill catalogue on 2026-10-27).

Questions to refine before a design:

- Which portraits: static textures only, or Spine atlases too (atlas regions, padding, premultiplied alpha, mesh
  seams and the game's texture size limits must survive the edit).
- Where the model runs: a cloud image service (account, key, cost, upload of the user's game art) or a local model
  installed as an optional module (download size, GPU/CPU time).
- Review and apply: before/after comparison, accepting parts of an edit, and applying the result as a Mod through the
  existing library and switch, so it can always be restored.
- Reference choice for style repair, and how far the model may change a portrait.
- Development: the dev store's official images must not be uploaded or shared, so experiments with cloud models on
  official art need an explicit owner decision (for example the owner runs them on their own install).

Owner decisions (2026-10-27):

- Scope: every portrait type, Spine atlases included. Only the final look matters to the owner; the App handles atlas
  regions, padding, alpha, seams and size limits transparently (pass-through processing).
- Model: GPT Image only for now. Lead's research (2026-10-27, to verify against OpenAI's own API reference before a
  design): OpenAI's Images API edit endpoint (`/v1/images/edits`) takes several input images as references;
  third-party documentation for `gpt-image-2` (released April 2026) gives 10 to 16 input images and outputs up to 2K
  or 4K, and they disagree. The API bills an OpenAI API key, separate from a ChatGPT plan. Codex CLI reportedly has
  built-in `gpt-image-2` generation that counts against the ChatGPT plan's Codex usage, or bills the API when an API
  key is configured; `codex --image` passes input images. Whether `codex exec` can generate images non-interactively
  is unverified.
- Review and apply: before/after comparison, partial acceptance, applied as a Mod through the library and switch.
- References for style repair: at first only other portraits from the game, of the same or another character.
- Development: official images from the dev store may be sent to AI services for development; they must never be
  published on UGC platforms, committed or shipped.

Owner decisions, continued (2026-10-27):

- Engine: prefer Codex CLI with the user's ChatGPT subscription (most users have one and can spend its usage) over an
  API key. Usage is not a concern for the experiment.
- Upscaling really means restoring detail: most portraits are already large but very blurry, as if enlarged.
- A previous Codex trial on Shiroko (Cycling) looked good; continue the experiment from there.

## BL-057: English calls alternate student versions "alts"

- Status: Accepted by the owner on macOS (alpha.12, 2026-10-29); Windows not reviewed.
- Requested: 2026-10-29.

The English interface called alternate versions of a student "variants" (the roster's Group Variants, the picker's
Select Variant, errors and the character name help). The owner checked the English-speaking community, which says
"alt". All English texts now say alt (Group Alts, Select Alt, Alt not found, In use by another alt); locale keys and
code keep "variant".

## BL-058: Batch import's empty list and after-import choices

- Status: Accepted by the owner on macOS (alpha.12, 2026-10-29); Windows not reviewed.
- Requested: 2026-10-29.

Owner findings on the Import screen:

- Select for Preview and Apply to Game were squeezed between the list and the footer buttons (shrunk, overlapping
  them). Owner decision: move them into Advanced Options, replacing BL-003's placement above Import. They lead the
  popup as On/Off rows like its other switches; saved values and defaults are unchanged.
- With no Mods added, the top-right "0 Mods" count is hidden.
- "No Mods" and the drop hint under it are centred as one block on the grey frame's diagonal centre (Items/ListBG's
  paper centre), by the ink of the text, also with search open.

## BL-059: Windows title bar: accent strip and native caption buttons

- Status: Accepted by the owner on Windows (alpha.14, 2026-10-30).
- Requested: 2026-10-29.

On Windows the native caption (BL-037's W1) shows a bar in the system accent colour along the top of the window, up
to the minimize, maximize and close buttons on the right, which look out of place against the game-styled top bar.
The owner asks whether the buttons can be redrawn or take the game's icons. Options: keep the native buttons and
give the caption the top bar's colours (Windows 11 DWM caption and text colour attributes), or draw the buttons in
the top bar from game sprites (the game has a close button, no minimize or maximize icons; Windows 11's snap layout
flyout on the maximize button would be lost).

Candidates in the official `Common` atlas (lead, 2026-10-29): `Common_Icon_Close` (60 × 60, white X),
`Common_Icon_Minus` (50 × 50, white bar, same stroke as the X), `Common_Square_Line` (34 × 34, thin outline) for
maximize. The game has no restore (two windows) icon.

## BL-060: Windows: blue lines at the stage edges during startup

- Status: Accepted by the owner on Windows (alpha.14, 2026-10-30).
- Requested: 2026-10-29.

While Now Loading shows, a thin blue vertical line runs down each side, about 28 px in from the window edges of a
1568-px-wide window: where a 16:9 stage would end in that window. Lead's reading: a sub-pixel seam where the stage's
own background shows past the loading cover at a fractional stage offset (Windows display scaling). Needs checking.

## BL-061: Windows: top bar background flickers while the window is resized

- Status: Accepted by the owner on Windows (alpha.14, 2026-10-30).
- Requested: 2026-10-29.

Dragging the window wider or narrower makes the whole top bar background (`Common_Top_Menu_Bg`, drawn as one
contiguous raster) flicker, as if it is cleared and redrawn on every resize step. It should keep the last drawn bar
until the new one is ready.

## BL-062: Windows: slow first launch of a new build crashes, then reloads itself

- Status: Accepted by the owner on Windows (alpha.14, 2026-10-30).
- Requested: 2026-10-29.

The first launch after building alpha.12 on Windows took a long time and crashed again; before the error report
could be opened, the App reloaded by itself and then ran normally. Related: BL-027 (first Windows startup failure)
and BL-055 (macOS first-launch hang). Evidence needed: `webui.log`, `webui.1.log` and `webui.2.log` from the data
folder beside the executable, copied before the next launches rotate them out.
On the same first launch no new-features tour (BL-021) appeared, although it should open on the first launch after
an update in prereleases.

Diagnosis from the owner's report (`BAMT-report-0.6.0-alpha.12-20261030-0938.zip`, lead, 2026-10-30): no crash and no
process restart. On the first launch one `ui.layouts` request took 55.4 s (cold layout export, the class of cause in
BL-027). The front end's 45-second boot watchdog fired during that single request (`Boot timeout: first-screen`,
21:08:37, showing the recovery panel); boot then finished at 21:08:44 and `BAMTRecovery.ready()` removed the panel,
which looked like a self-reload. The next launch took 5.4 s. The timing lines do not name the layout root.

The earlier `drag and drop is unavailable` TypeError and the double `loaded` come from W1's one initial reload,
removed in alpha.13 (BL-059).

Likely cause of the missing tour, to be verified: `tours.py` treats a profile without tour history as a new install
(`highest` unset, nothing pending), so an upgrade from 0.5.0, which recorded no history, gets no tour.

Fix plan: a pending boot request keeps the watchdog alive up to the existing 180-second bound; name the root in the
layout timing and add the missing `PREFAB_HINTS`; tell an upgrade from a new install for the tour.

Implementation (2026-10-30): `BAMTRecovery.request()` tracks boot RPC and UI index/layout requests through their
response bodies. The live watchdog grants pending requests grace, restores the 45-second idle check when they
settle, and caps every reset (including phase changes and `activity()`) at 180 seconds from page startup. Errors
still recover immediately; ready and Safe Mode still cancel the watchdog. `Services._ui_asset` now measures
`ui.layouts.<root>`, including failed exports.

Cold measurements used only the official development view `steam-3557621-7791673520712697115-1955d55449`, with
`bamt_dev.testing.isolate()` before importing the App. Each root had a new `GameAssets` instance and an empty,
consented temporary `AssetCache`; requests ran through `Services._ui_asset`, including serialization. These are
App-cache-cold macOS measurements, with OS file caching uncontrolled, not Windows boot times. The roots cover the
default hall's loading, navigation and controls plus the roster's templates. No native App or browser was launched.

| Root | Before (s) | After (s) |
| --- | ---: | ---: |
| UILoading | 0.7013 | 0.4602 |
| UIRepeater | 0.4551 | 0.4085 |
| UICharacterDetail | 0.5627 | 0.5109 |
| UILobby | 0.4913 | 0.4490 |
| UICharacterCollection | 0.4458 | 0.4228 |
| UIPopup_Sort | 0.4316 | 0.4207 |
| UIPopup_GachaCharList | 0.6177 | 0.5547 |
| UIPopup_Option_PC | 0.4938 | 0.4558 |
| UIPopup_FilterSort | 0.7059 | 0.6225 |
| UIPopup_Formation_CharEdit | 0.8541 | 0.8065 |
| UILobbyCharacterSelectPopup_Slot | 12.9323 | 0.4412 |
| UICharacterCollectionPopulatorUnit | 1.1228 | 1.0697 |

The hall's `UILobbyCharacterSelectPopup_Slot` was the only slow root: one full prefab scan took 12.5523 seconds.
Its verified bundle is `ui-_mxload-prefabs-2025-07-02_assets_all_1360207439.bundle`; adding that `PREFAB_HINTS` entry
removed the scan. The other roots scanned zero prefab indexes before and after. The Windows report omitted the
root, so its exact 55.4-second request cannot be identified retrospectively; the measured cold default-screen
scan is confirmed and future logs identify the root directly. Missing/renamed hints retain the existing fallback.

The tour diagnosis is confirmed: `tours.launch` previously recorded the current version and selected nothing when
`highest` was unset. Version 0.5.0 already persisted a boolean asset-consent answer. `UiState.__init__` snapshots
whether that answer existed before this launch; `tours.launch` uses it only when no valid high-water version is
available. Existing profiles queue available published tours and retry until presentation is acknowledged; a new
install stays quiet even after its startup dialogs save consents. Existing high-water marks, acknowledgements,
repeat/downgrade rules and publication gates retain priority. Profiles without a prior consent answer remain quiet
because no earlier use is established. No tour copy or locale text changed.

Regressions cover the reported long request, concurrent and body-pending requests, request success/failure/abort
cleanup, idle recovery, the absolute deadline under continuing activity, ready cleanup, UI index tracking, named
layout failure timings, valid/missing/renamed prefab hints, all twelve cold first-screen layouts without a prefab
scan, historyless upgrades with either consent answer, fresh profiles with startup writes, pending/repeated/downgraded
tours and RPC ready/Safe Mode gates. Native Windows cold launch, recovery presentation, tour presentation and visual
acceptance remain the owner's.

Validation: the prescribed isolated full pytest run passed (3,594 passed, 27 skipped, 3 existing locale deprecation
warnings); the full Node suite passed (1,941 passed). Added 38 Python cases and 7 JavaScript cases, and extended the
two existing layout-request tests. `git diff --check` passed. Git staging was refused by the sandbox when creating
the worktree index lock, so the implementation remains uncommitted in this worktree.

## BL-063: Windows: the system pointer often replaces the game's pointer

- Status: Accepted by the owner on Windows (alpha.14, 2026-10-30).
- Requested: 2026-10-30.

On Windows the mouse pointer often shows the system style instead of the game's `PCIcon_MousePoint` (`web/js/cursor.js`).
Owner detail (2026-10-30): in an affected launch it stays the system pointer for the whole run, while the touch effect
works, so the game textures load. Lead's reading: `initPointer` runs once and returns silently if the texture load,
the cuts or `CSS.supports` fail; add a log line and a retry first, then read the next report.

## BL-064: Windows follow-ups from alpha.14

- Status: Accepted by the owner on Windows (alpha.15, 2026-10-30).
- Requested: 2026-10-30.

1. The new-features tour opens only some time after the first screen; find why and shorten the wait.
2. Lists such as the student roster scroll without smooth scrolling on Windows.
3. English release notes: the Open Release Page button's text overflows.
4. The tour list joins page titles with " - "; use the language's comma.

Findings and implementation:

1. `main.boot` waited for the entire startup popup sequence before `Tours.start`. Its drift/untracked steps called
   `Prompts.scanned`, which waited up to 10 seconds for `library.scanned` even with no dialog to show yet.
   `mods.state.busy` does not include scans: it means an active Mod toggle or queued writes. `app.ready` starts
   maintenance on a background thread, and `Popup.whenIdle(200)` polls immediately rather than sleeping first.
   `Prompts.start` now uses completed scan results without waiting for an unfinished scan. Late results wait for
   open popups, including the tour, and a final catch-up handles results arriving between startup steps.
   `Prompts.isBusy` keeps prompt card preparation and actions ahead of the tour, alongside the existing loading,
   writer/queue, recovery and popup gates. Consent and already-ready startup prompts retain their order.
2. The roster is virtual: its wheel handler prevents native scrolling and updates `view.scroll` and the translated
   row host immediately. Display/settings and the other vertical lists use native overflow; there was no shared
   wheel easing. `horizontal-scroll.js` supplies a pure cubic `easeScroll` and a shared `wheelScroller` for Windows
   notches, using the existing WebView2 wheel classification. The roster writes each intermediate position through
   `applyScroll`, keeping rows and scrollbar synchronized. `main` installs delegated handling for every native
   vertical overflow container, including dynamically created dialogs, import lists and logs. Repeated notches
   accumulate; reversal, boundaries, stage scaling, cancellation and reduced motion are covered. Horizontal lists
   retain `horizontal-scroll.js`; macOS trackpad pixels and momentum retain their existing path.
3. `popupFrame` already fitted sibling captions together with `NGUI.fitLabels`, at most two lines within a 420 by
   140 text area on the unchanged `UIPopup_Sort` button body (496 by 177). The release-notes-only
   `sharedButtonSize` pass fitted that already-wrapped text again with `wrapPath`, counting embedded newlines
   incorrectly and increasing English to three lines. Removed the redundant pass and option from `popupFrame`,
   `showMessage` and `Update.releaseNotes`. The regression failed on English's three lines before the fix, then
   passed for all six locales, at minimum/default/large windows and 1x/2x density, preserving geometry and wording.
4. Only `webui.tour.separator` changes in `en-US`, `ko-KR` and `th-TH`, to `, `. The other three remain `、`.
   The locale audit confirms no other wording changes; the tour-list test checks the joined titles in all six.

New regressions: `startup_prompts.test.mjs` (5 cases), `vertical_scroll.test.mjs` (16 cases), and two cases in
`popup.test.mjs` for release-note fit and localized tour-list joining. Existing `screen_loading.test.mjs` now also
checks prompt preparation and recovery priority. No App, browser preview or screenshot was used. Windows wheel
feel, startup timing on the owner's machine and actual font/visual acceptance remain the owner's review.

Validation: the prescribed isolated full pytest run passed (3,594 passed, 27 skipped, 3 existing locale
deprecation warnings); the full Node suite passed (1,965 passed, including 23 new cases). `git diff --check` passed.
An initial test run refused to read a new, untracked scrolling source file under the development isolation
policy. The implementation now reuses the tracked `horizontal-scroll.js`; the formerly failing case and both
full suites pass without changing the isolation policy. Git staging was refused when creating the worktree's
index lock, so the changes remain uncommitted in this worktree.

## BL-065: Mod library state and recovery safety

- Status: Accepted by the owner on macOS (alpha.7, 2026-11-03): the owner's checks passed; what the owner could not trigger (state damage, recovery, engine and CI fixes) on the automated tests; Windows not reviewed.
- Requested: 2026-11-02, from a whole-app functional review (FUN-1 to FUN-3).

1. One bad record or an unreadable `state.json` erased the library at the next save.
2. A mod file that was briefly missing permanently deleted its library record.
3. A journal that could not roll back (a Steam repair after a crash, a moved game folder) blocked reads and writes for
   good.

Implementation (`library/service.py`, `library/asset_delete.py`, `library/profiles.py`):

1. `state.json` is read record by record; an unusable record is held and written back unchanged. The last good file is
   kept as `state.json.prev`, an unreadable one as `state.json.bad`, the same convention as `profiles.json`. A missing
   file also reads `.prev`. With neither readable, or a version above 1, the library is blocked: reads answer empty,
   every change of state or configuration is refused with `library_state_unreadable`, and side writes of reads skip. A
   file that cannot be opened is not corruption: `library_io_error`, read again at the next call.
2. A valid record with a missing file is held the same way and returns when its file does, with its alias, assignment,
   configuration and applied record. A held id still counts as a source, so nothing is settled away; a removal never
   comes back.
3. Reads attempt recovery but never fail on it; a read retries when the journals change or after 60 s. Writes always
   attempt recovery and refuse with the real error. A game file whose bytes match neither the journal's before nor its
   after is an outside change: left as it is, without a new date on its record, its kept original kept, the journal
   retired. Same install key with other resource folders proceeds if every target is still accepted. A write journal of
   an install not known now waits with its files while other installs are written. An asset deletion cannot wait,
   because its rollback restores a whole-library snapshot: reads work and writes refuse.

Known limits, for the owner's decision:

- Nothing in the UI shows a held mod or a blocked library. A held mod stays hidden until its file returns; a blocked
  library looks empty and the error appears only when a write is refused.
- `original_unavailable`, `library_recovery_pending` and an invalid journal still refuse writes with no way out in the
  UI.
- `_prune` deletes nothing while a journal waits, so an install that never returns keeps originals on disk.
- Held photograph composite records are invisible to `usage_targets` and `remove_photo`, and the one-time legacy remap
  does not see held originals.

Validation: `tests/test_library_state_safety.py` covers every case, the review's reproductions through the real library
and the RPC registry, and a sweep of the read RPCs with `profiles.json` byte for byte unchanged. Full suites on
`deddd173`: 3,729 passed, 26 skipped; JavaScript 1,965 passed. Not run natively; Windows from code only.

## BL-066: Review fixes for import grouping, game folder, photographs, popups and texture bounds

- Status: Accepted by the owner on macOS (alpha.7, 2026-11-03): the owner's checks passed; what the owner could not trigger (state damage, recovery, engine and CI fixes) on the automated tests; Windows not reviewed.
- Requested: 2026-11-02, from the whole-app review (FUN-4 to FUN-6, UI-2 to UI-4, SEC-1).

1. FUN-4: a mod whose parts carry different dates imported as several mods, and applying the last reverted the others.
   Now the files of one core and kind are one mod whatever their dates; only a part repeated under another date splits
   the set by date (`library/judge.py` `mod_versions`, used by Add and Batch Import). A mod is dated by its newest part.
   Mods already stored as halves are not merged.
2. FUN-5: any existing folder was accepted as the game folder, and the next launch stayed on the recovery panel. A game
   folder must now hold the UI atlas bundles (`searching.is_game_install`, by file names only); Settings refuses others
   with `game_not_found`, and an old bad setting starts on the no-game screen. An error without a code shows the generic
   failure text instead of `error.undefined`. Safe Mode has no folder control (owner's call).
3. FUN-6: one stale photograph row failed the whole manifest and with it the roster and detail. A bad record or selection
   now costs that entry only and is written back unchanged; a stale selection reads as the default thumbnail; a manifest
   that cannot be parsed refuses photograph writes and is never touched; the roster never fails on the photograph lookup.
4. UI-2: Display Settings opened from Assign Character now stacks over the dialog (`#display.over`).
5. UI-3: a failed Import Results row shows its short reason, then the localized error text, never the code or JSON;
   both stay on the result for the error report.
6. UI-4: a message popup taller than the scroll cap (1400) scrolls by itself, so its title and buttons stay on the stage.
7. SEC-1: every texture decode goes through `texture_guard`: sides 1 to 16384, a known format, and image data that covers
   the declared size, checked before any native decoder. Crunched formats are refused, because the native unpack itself
   is unsafe on bad data; no official Windows texture uses them. All 30,714 official Windows textures pass except 14
   empty font placeholders that could not be decoded before either.

Known limits, for the owner's decision: Safe Mode folder control; split mods from before FUN-4; crunched textures in
Android or iOS files, not checked; import does not refuse an out-of-bounds texture, only its decode does.

Validation: full suites on `60667b6c`: 4,063 passed, 26 skipped; JavaScript 1,982 passed. Not run natively; Windows from
code only. Visual acceptance of the popups is the owner's.

## BL-067: Review fixes, Medium findings

- Status: Accepted by the owner on macOS (alpha.7, 2026-11-03): the owner's checks passed; what the owner could not trigger (state damage, recovery, engine and CI fixes) on the automated tests; Windows not reviewed.
- Requested: 2026-11-02, from the whole-app review.

Library: FUN-7 (an update marks the entries configured with the mod, and Off then On never writes the old build back),
FUN-11 (a deleted repaired copy leaves its parent repairable; a dead link counts as none).

Settings files and data folder: FUN-9 (`webui.json` keeps `.prev` and `.bad`, flushed before replace), FUN-10 (a bad
`config.toml` starts on defaults, is kept as `.bad` before a save, and a failed read is never applied), FUN-13 (no
lock-order deadlock around the photograph store), FUN-14 (a read-only data folder gives one notice and
`data_dir_readonly`, with a new string in six locales, the non-English wording not owner-reviewed), FUN-17 (a library move
copies, verifies and publishes before it clears the source, and a failed move can be retried).

Engine: FUN-12 (the newest release with this platform's build is offered first), FUN-16 (shorter staging folders), SEC-2
(`bundle_guard` bounds bundle sizes before loading; all official bundles pass), UI-23 (errors name their file).

Popups and first run: UI-1, UI-5 (ordinary refusals are a neutral notice, `REFUSALS` in `popup.js`), UI-7, UI-8 (one shared
focus trap), UI-15, UI-16, UI-17 (dialogues only; idles keep clip names, no string exists), UI-22, FUN-15 (the first-run
sequence runs once a game folder is chosen).

Fit and accessibility: UI-9, UI-10, UI-11, UI-12, UI-13, UI-14, UI-18, UI-19. `ngui.js` `fitLines` and `fitGroup` changed for
every caller: text with no break point keeps the largest one-line size, and CJK may break between characters only when no word
wrap stays at the floor.

Known limits, for the owner's decision: FUN-8 (On overwrites an outside change, against the docs), UI-6 (actions with no
result since the toasts were removed), UI-20, UI-21, UI-24, UI-25, a third line for prompt cards (names still under 24),
labels for camera idles, Safe Mode folder control, the classic UI's truncating save of `config.toml`.

Validation: full suites on `949c26eb`: 4,197 passed, 26 skipped; JavaScript 2,116 passed. Not run natively; Windows from code
only. Visual acceptance is the owner's.

## BL-068: Review fixes, Low findings

- Status: Accepted by the owner on macOS (alpha.7, 2026-11-03): the owner's checks passed; what the owner could not trigger (state damage, recovery, engine and CI fixes) on the automated tests; Windows not reviewed.
- Requested: 2026-11-02, from the whole-app review.

Engine: FUN-18 (no restore over a game update's rename), FUN-19 (Windows replace retry), FUN-20 (a mod update is atomic and
journaled), FUN-21 (direct import honours CRC and Extra Bytes), FUN-22 (photo export without hard links), FUN-23 (the macOS
helper reopens the app when the image cannot mount), FUN-24 (three resource versions in the UI asset disk cache), FUN-25 (game
services refresh after a game update), FUN-26 (navigation to a student has no client timeout), FUN-27 (the library apply writes
no `.backup`). CI: SEC-4 (actions pinned to commits, uv 0.13.1; proven only by the next release's Windows build).

Web UI: UI-26 to UI-34, UI-36, UI-37. One new string, `countOne`, in six locales.

Known limits: `detail.js` `readFamily` keeps its 15 s cap; Hall has no game clear button; UI-37's other copies of the error
expression remain. For the owner's decision: FUN-8, UI-6, UI-20, UI-21, UI-24, UI-25, UI-35. SEC-3 closed: no signing (owner, 2026-11-02).
SEC-5 not done: reachable only with the session token.

Validation: full suites on `248d5adc`: 4,234 passed, 26 skipped; JavaScript 2,141 passed. Not run natively; Windows from code
only.

## BL-069: macOS: the pointer sometimes flickers to the system pointer

- Status: Accepted by the owner on macOS (alpha.7, 2026-11-03): no flicker seen in ten minutes of use; reopen if it returns.
- Requested: 2026-11-02 (owner, 0.6.1-alpha.3).

On macOS the game's pointer (`web/js/cursor.js`) occasionally flickers to the system pointer. Trigger not known yet.

A code audit proved no single cause. Fixed: the last 3 CSS px at the window's right and bottom edges had no cursor cut (the
list now goes down to 1 px); the Settings sliders showed `ew-resize`; a plain Hall card set `default`. A test now pins which
cursor values the page may use and what writes `--cursor`.

Candidates the code cannot prove, and what to watch for: the integrated title bar and the traffic lights (only when crossing the
top strip); AppKit's resize edges (only at the window border, not in fullscreen); cursor rects reset on window events (right
after Cmd-Tab, a resize or a fullscreen toggle); a busy web process (heavy screens, or scene changes under a still pointer).

For the owner's decision: `photography.css` `.photo-active` sets the system pointer for the whole camera mode.

Leftovers in the same job: UI-37's other copies use the shared `errorText`; `detail.js` `readFamily` has no 15 s cap and a stale
read does not commit; a photo a held record uses counts as used and cannot be deleted.

Validation: full suites on `f1ad2631`: 4236 passed, 26 skipped; JavaScript 2149 passed. Not run natively.

## BL-070: One design system for the web UI

- Status: Closed by the owner (2026-11-04). Steps 1 to 3 accepted on macOS (alpha.5, alpha.7, alpha.20); step 4 not started; Windows not reviewed.
- Requested: 2026-11-02 (owner): does the UI follow the game's design language (button sizes, spacing, fonts), and where it does not, does it have one system of its own?

Owner decisions (2026-11-03): follow the prefab's values as the one system; run the migration one step at a time; keep the top bar and the Enable Mods switch at their present size.

**Step 1, shared tokens (`web/js/ui-tokens.js`), pinned against the prefabs by `tests/test_ui_tokens.py`:**

| Role | Value | Prefab |
| --- | --- | --- |
| Popup footer | centre 190.5 above the frame's bottom, bodies 496 x 177, two 88 apart, three or more at 0.8 and 24 apart, caption 72 down to 48 in a 400 box | `UIPopup_Sort/Center/Popup/Btn` |
| Backdrop | `#000000/0.80`: the sprite says 0.94, but its `TweenAlpha` rests at 0.80 (read from the data, not seen in the game) | `BlackBg/BlackBgClickArea` |
| Small button | 402 x 136, caption 48 down to 34, only the width varies (240 at least) | `ETCSetting/ButtonBg` |
| Chip | 272 x 72, caption 40 down to 28, 290 apart; three in the list popup's panel about 283 apart | `Char_Target/Type/MainType/BG` |
| Status colours | success `#0a6f2d`, warning `#f14f00`, error `#bb3421`, greyed `#828282` | prefab labels and sprites |
| Settings row gap | 12 | `UIPopup_Option_PC`, Game tab |
| CSS radii | 3.24, 6.49, 14.27 px (5, 10, 22 units); one fallback button, no skew or shadow | `Common_Bg_Raius*` |

Consent and update popups now use `popupFrame`; their OK stays blue (BL-034). Popups have no fade.

Step 1 leaves these for later or for the owner: captions below the 48 minimum under the tests' measuring stand-in (en-US Spine licence 46; Thai Spine licence 36, update offer 40, recovery report 36), to be judged with the real font; 11 off-game colours nobody decided (stage gradient, Enable Mods OFF ink, plain popup backdrop 0.6, checked plain checkbox, lobby-unavailable text, repair preview, native-controls button, detail placeholder ink and strip card tones).

**Step 2, one light stats box (`web/js/stats-box.js`, `STATS_BOX`):** the list popup's details panel, the file details, messages with a line
button and the update dialog draw through one component. Caption 38, value 38 down to 26 on up to three lines, a row at least 64 high, the line
the fill plus 5 (the prefab's). Width: a popup frame less 200, a panel less 40, the file details the table under it. Owner's decision (2026-11-03):
boxed message rows take the same text range; a row with a button stays 156 high. The update dialog's frame goes from 940 to about 710 high. The
Switch Portrait panel still lays out its own half-width box.

**Remaining, one at a time:**

- Step 3: the lobby subtitle on the balloon (owner, 2026-11-03: as the lead proposed, `Talk` at the head box, `UITalk` in fullscreen); Hall modals and buttons on the shared footer and small button.
  Implemented (Codex, reviewed by the lead, 2026-11-04):
  - Memorial lobby dialogue (`4dea1366`): the self-made `.lobby-subtitle` box is gone. Normal views (detail screen, Hall
    memorial) draw LobbyCommon's `Talk` balloon through the Hall's balloon code, at the lobby prefab's own `ChatDialog` Talk
    position under the lobby camera (`ch0100_home`: (-320, 503); the same prefab-root space as the pat box, proven on the
    official view), clamped to the visible view (left of the detail panel); lobbies without one use the head-pat point.
    Fullscreen (detail fullscreen, Hall immersive) draws the screen-anchored `UITalk` (813 x 298, label 680 x 220, fs62
    `#29415a`) with NGUI ShrinkContent, no minimum size. Leave and enter motion kept.
  - Hall first-run buttons on the small button (`4fb031c3`): 460 px wide, 136 u high, captions 48 down to 34, same centre.
  Stopped, waiting for the owner (numbers in `f719f3f5` and `docs/web-ui.md`):
  - Hall modals on the shared footer: the content of all three modals would cross the footer's clearance (Add/Change
    Portrait short by 77 px, memorial editor 88 px, background chooser 13 px). Their own 66 px action row stays.
  - Hall small buttons in rows: card Add/Select, queue Remove and the portrait size reset would overlap their neighbours by
    16 to 18 px and leave their rows by 5 to 10 px. They keep their present size.
  - Thai captions under the minimum: `hall.doneAdding` 44 in the footer (48), the unsaved-changes popup's three buttons 36
    (48, already in use today), the portrait size reset 30 (34).
  Owner's decisions (2026-11-04), as the lead recommended: the Hall modals move up or shorten their content to make room for
  the shared footer; rows and cards grow so their buttons can take the small button's size; Thai may go below the minimum
  caption size at those three sites, wording unchanged.
  Second pass (Codex, reviewed by the lead, 2026-11-04): the Add/Change Portrait and background modals draw the shared footer
  (`abf289e6`; the portrait toolbar and search box rise to the title underline, the background list uses the gap above it);
  portrait Add/Select and the portrait size Reset take the small button, their rows (402 to 437 px) and the toolbar (142 to
  162 px) grow (`b9b7ad4e`); the three Thai exceptions are named per site and locale (`7f245853`). The memorial editor
  cannot fit the footer and a full row (18 px short, 77 px with search open): owner's decision (2026-11-04), it keeps its own
  66 px action row and its Add and Remove buttons as they are.
  Owner's review of alpha.17 (2026-11-04): the portrait size Reset must be as tall as the slider beside it, not taller;
  fullscreen must keep the `Talk` balloon (switching to `UITalk` broke the continuity). Lead's fix: the toolbar's second
  row and Reset take the slider body's 104 u (67.5 px; toolbar 141.5 px), the caption range unchanged; fullscreen draws
  the same Talk balloon by the head, bounded by the whole stage and following the art, and the UITalk path
  (`hall.dialogueLayout`'s `box`, `NGUI.shrinkContent`) is removed. The two buttons the lead had called "first-run" are
  the Hall's empty states (all portraits removed; no or an unavailable memorial): the first visit gets Shiroko's portrait
  and memorial, so they do not show then.
- Step 4: list popup cards and details panel cloned from prefabs (UI-21), and its details actions.
- Not scheduled, owner's eye first: the photography toolbar and colours; the lobby frost.

Audit by numbers, from the official prefabs and the code. Screens cloned from prefabs are restored: roster, detail, Display Settings, Settings (three deviations), Batch Import, navigation,
loading, visual mode, the Hall balloon. Inconsistent: message, notice and confirm popups, tours, Mod Alias, Compare, Export, the consents, the update dialog, Hall's chrome and modals, the lobby subtitle
and frost.

Main findings: the popup footer's button centre sits 122, 148, 172 or about 81 units above the frame bottom against the prefab's 190.5, with five caption ranges and gaps of 88, 54, 31 and 24 against 88; the game's one small
button (402 x 136, font 48) has seven body sizes in the app; about 28 CSS lines carry their own radius, blur, shadow or colour; 30 of 69 colour literals are not in the exported prefabs; four hand copies of the light stats box and two chips.

Decisions from the same review: FUN-8, UI-6 and UI-20's shadows are done on `main`. UI-21 migrates when those screens are next touched, and is part of this item. UI-24 closed: no selected state, the game's lobby menu has none.

## BL-071: Student Hall: portraits not fixed to the scene while panning

- Status: Accepted by the owner on macOS (alpha.7, 2026-11-03); Windows not reviewed.
- Requested: 2026-11-03 (owner): zoomed in, a pan makes the portrait feel thrown about rather than fixed to the canvas.

Two causes, measured in the development build with a read-only frame probe (a right-button drag of 720 stage px at 2.3 x):
a pan eased towards its goal over 0.08 s and glided on after release, and the portraits' renderer could draw before the camera
stepped, so the portraits trailed the background by a frame. Before: 64 of 70 frames off the background, up to 19.9 px, 713.7 of
720 px at the last move. After: 0 of 156 frames, 720 of 720 px, no movement after release. A pan now has no easing, and the
renderer steps the camera at the top of its frame. Zooming keeps its easing. A headless software renderer cannot judge the feel:
that is the owner's. A trackpad's own momentum after the fingers lift is the system's and stays.

## BL-072: Batch Import's design check

- Status: Accepted by the owner on macOS (alpha.7, 2026-11-03); Windows not reviewed. Item 2 stays as it is by the owner's choice.
- Requested: 2026-11-03 (owner): check the page by eye and in code; the bottom-right confirm button is too large.

Done:

- Import was the Items prefab's `UseBtn` at scale 0.6 (100 px high, caption 48 px) beside 81 px buttons with 29 px captions, against the
  rule that siblings share one size. The row is now one row of the small button (88 px, caption up to 34.9 px, 20 apart),
  right-aligned to the list's tools. Gold marks Import.
- Found on the way: every gold popup button had lost its two corner patterns (BL-029 names them): the template's nodes were not
  marked active, and the mirrored one used a negative scale the renderer does not draw.

Seen in the development build (English, Simplified Chinese, Thai), with what the owner chose (2026-11-03):

1. The list showed about 2.2 rows: rows are 188 px high, and a 100 px strip above them held nothing but "N Mods", which the filter
   button's "All (N)" already says. Done: the strip and the count are gone, about 2.7 rows. This replaces BL-058's hidden count.
   Rows keep their size.
2. The list's seven tools are 56 px high with 21 px captions, beside the 88 px row. The game's lower small buttons are 106 units
   (69 px: `Btn_Reset`, `Btn_Shortcuts`). Left as it is: at that height the row may not fit.
3. Grouped rows: the student icon at 20 px read as a dark blob, and the status was plain text where ungrouped rows use a tag. Done:
   kinds as text on the metadata line, states on the tag. Grouped results name the kinds in words too.
4. The left column ended at 765 px and the list at 826 px. Done: the name plate's foot meets the list's; the blank left column with
   no Mods stays.
5. Two golds: Import `#ffee00` (Items `UseBtn`), popups `#f6e94b` (Mailbox `ReceiveAll`). Done: Import uses Mailbox's button.
6. The filter popup was 2458 units wide for one short column. Done: Advanced Options' frame.

## BL-073: macOS: on a second display the window jumps between screens and loses its traffic lights

- Status: Accepted by the owner (2026-11-03) without a test: no window that wide in use; reopen if it returns.
- Requested: 2026-11-03 (owner, 0.6.1-alpha.7): after moving the window to the second display, dragging it there makes it jump back
  and forth between the two displays at a high rate, and the traffic lights are gone.

Owner's setup: built-in Retina display (2x, primary) and a 1920 x 1080 display at 1x.

Lead's findings (2026-11-03):

- The traffic lights belong to the native chrome (BL-037 M1). When `CocoaHost.measure` raises, `WindowChrome.fail` disposes it: the
  lights are hidden again and the top bar falls back to pywebview's JS drag (`DRAG_REGION_SELECTOR`), which moves the window by
  `screenX`/`screenY` flipped against the screen the window was created on. Across displays of different sizes and origins that
  conversion is wrong, and each move changes the next event's coordinates: the window oscillates. Both symptoms fit "the native
  chrome failed after the move".
- Not reproducible in development: the packaged app's executable is linked against the macOS 27.2 SDK, the development Python against
  15.5, and AppKit gives them different title bar metrics. In development the title bar container is 28 high and the chrome fails at
  start on both displays ("M1 button would be clipped by its AppKit container"); in the packaged app it works on the primary display.
- Reproduced the packaged title bar in development with a launcher linked against SDK 27.2: the chrome then works, and stays active
  when the window is moved between the displays programmatically (whole jumps, small steps across the boundary, sizes up to
  1900 wide on the second display). A real pointer drag could not be simulated without accessibility rights. On the second display
  the buttons sit 0.37 pt under the title bar container's top edge, close to the "clipped" check.
- Needed: the reason the packaged app logs (`Native window chrome inactive: ...`) after the move.
- Cause, confirmed by the owner's log ("M1 button would be clipped by its AppKit container", after maximizing on the second display)
  and reproduced with the SDK 27.2 launcher: the lights move down to the toolbar's centre line, which grows with the window
  (`window-layout.js` `headerGeometry`: 23.3 at 1512 wide, 28.9 at 1920 x 1055). The title bar container is 32 high, so beyond
  about 1560 wide, on any display, the move was refused and the chrome given up for good.
- Fix (`window_macos.py` `_within_container`): the buttons go as low as the container allows and the chrome stays. On such a window
  the lights sit up to about 5 points above the bar's centre line (owner's eye). Verified with real AppKit through the launcher:
  before, the chrome failed and stayed off; after, it stays on at 1920 x 1055 and back.
- Left: pywebview's JS drag still jumps across displays, but it now runs only when the native chrome fails for another reason.

## BL-074: Student Hall shows the official art for two students after updating to alpha.7

- Status: Closed by the owner (2026-11-03), working as intended. Cause confirmed: the owner had deleted the first of duplicate mods,
  and a deleted mod's Hall entries go back to the official art. The owner keeps that: the user notices it more easily.
- Requested: 2026-11-03 (owner, 0.6.1-alpha.7): in the Student Hall, the portrait and the memorial lobby of Ako (Dress) and Hina
  (Swimsuit) show the official art again; other students not checked.

The Hall's "official" source has drawn the verified official files since 0.6.0, and a Hall entry whose mod is gone shows "missing", not
the official art, so neither explains it alone. Owner (2026-11-03): the detail screen still shows the mods (renamed by the owner);
the game state cannot be checked; an update of those students is possible.

Lead's lead candidate: deleting a mod (`mods.remove` -> `state.reconcile_deleted_source`) rewrites the Hall entries that use it to the
official source whenever the student has one, even when another mod of the same student and kind remains (`api.py` `fallback`
prefers `official`). Deleting old versions after importing new ones would give exactly this. The only other path since 0.6.0 is
`webui.json` read from `.prev` (FUN-9), which would bring back an older Hall as a whole. The owner's library cannot be read from here. Questions for the owner: what the Hall's
layout editing names those two entries; whether the detail screen and the game show the mod; whether those mods were updated in the
app, or the game updated those students, since the last good state.

## BL-075: Settings: the volume sliders do not follow the arrow keys

- Status: Closed by the owner (2026-11-04).
- Requested: 2026-11-03 (owner, BL-067 review): the arrow keys do not change the volume. UI-14 (BL-067) meant to fix this.

## BL-076: Student Hall: select a portrait by its outline, a pinned toolbar, its own size and a skin swap

- Status: Implemented (`95959624`, Codex, reviewed and fixed by the lead). Owner on alpha.9 (2026-11-03): a click on a portrait did
  nothing, and the toolbar beside the portrait was harder to reach than one directly above it. Cause: the macOS WKWebView can end a
  captured press with only a click (no pointerup, no lost capture; seen in an offscreen WKWebView probe), and selection waited for the
  release. Fixed in `2dede1ed` (alpha.10): selection on press, any release signal ends a press, toolbar centred directly above the
  portrait and below the top bar and top-right controls, hover kept over the transparent parts of the box. Owner on alpha.10: clicks
  work; the toolbar still sat to the left or right of every student. Cause: Spine's bounds count transparent and off-body parts, so
  their centre is 30 to 130 stage px off the character for most students (measured on official portraits in the dev view). Fixed in
  `5b20484c` (alpha.11): the toolbar is centred on the visible head. Owner on alpha.11: above the head as wanted, but a portrait at
  the top right got it on its face (pushed below the top-right controls), and entering layout editing stalled for seconds. Owner
  decision: where the toolbar cannot go above the head, it goes under the feet. The stall was the main-thread re-decode of every
  atlas page for pick alpha (about 120 ms per 2048 page in Chrome). Fixed in `f2d9820d` (alpha.12): feet placement, pages read in
  the background (blob decode and shrink off the main thread, opaque until read). Accepted by the owner on macOS (alpha.13,
  2026-11-04); Windows not reviewed. Japanese, Korean and Thai "Change Portrait" wording unreviewed.
- Requested: 2026-11-03 (owner): with portraits close together, the toolbar of a placed portrait is hard to reach. Clicking a portrait
  should draw the gold "applied" outline along its real visible outline and pin its toolbar (hovering another portrait shows no
  toolbar then); clicking it again clears the selection. The toolbar should gain a size slider of its own and a button that swaps the
  portrait for another skin of the same student.

Today (lead, 2026-11-03): the toolbar exists only in layout editing (`student-hall.js` `renderHits`, `createHoverOwner`); it follows the
pointer over each portrait's bounding box (`portraitRect`), so overlapping boxes hide the portraits behind. Each Hall portrait already
stores its own size (`entry.scale`, 0.2 to 2.5, validated in `student_hall.py`), but no control sets it: only the global size
slider. All portraits draw into one shared Spine canvas. Icon candidates in the game's atlases: skin swap `Campaign_Icon_ChangeUnit`
(77 x 68), `CCG_Icon_ReplaceCards`, `Common_Icon_BeforeChange`; size `Common_Icon_ZoomChar` (100 x 69). The flip button already uses
`Common_Icon_Change`.

Owner's decisions (2026-11-03), as the lead proposed: layout editing only; a click selects, a click on the selected portrait clears,
another portrait switches, the empty scene or Escape clears, a drag selects and never clears; the portrait under the pointer is the
front-most one with visible pixels there (Normal-blend slots; halos and glows ignored), for hover, click and drag; a static gold
outline about 3 stage px wide in the applied frame's yellow, under the portrait, no animation; the portrait's own size from 50% to
200% in a second toolbar row with a reset, the global size kept; a change-portrait button (`Campaign_Icon_ChangeUnit`) listing the
same student's other portraits not yet in the Hall, keeping position, size, mirror and layer. Only the Spine runtime's public API.


## BL-077: Photography: blank space in the Expressions list pans the camera

- Status: Fixed in `9bef922d` (alpha.13); accepted by the owner on macOS (2026-11-04); Windows not reviewed.
- Requested: 2026-11-03 (owner, 0.6.1-alpha.12, macOS): with the portrait camera's Expressions list open, the pointer over the list's
  blank space turns into the hand, and the wheel there moves the frame.

Cause: `#photo-overlay` passes the pointer through (`pointer-events: none`) and only the `.hit` cards took it back; the list's scroll
area (`.popup-cards`) and the panel's frame are not under a `.layer-host`, so their blank space hit the pan surface (`.photo-pan`,
`cursor: grab`, wheel pan and zoom). The lobby camera's Actions list had the same gaps.

Fix (`controller.js` `choiceShield`, `photography.css`): a transparent shield under the cards covers the panel's whole box, and the
scroll area takes the pointer, so a wheel in the gaps scrolls the list and the game pointer stays.

## BL-078: macOS: dragging the top bar sometimes does not move the window

- Status: Accepted by the owner on macOS (alpha.19, 2026-11-04); Windows not reviewed. Fixes `1a2afd84`, `b186e93b`,
  `4841e30e`, `bb034e39`.
- Requested: 2026-11-03 (owner, 0.6.1-alpha.12, macOS): dragging the top bar did not move the window; happened once, not seen again.

Candidates (lead, from the code): the native drag regions (`window-chrome.js` `dragRegions`) are published empty while a popup or
consent host is open, a scene is immersive or the chrome is not yet valid, and again only after the next layout pass; the native
chrome itself failing (`Native window chrome inactive: ...` in the log) leaves pywebview's JS drag. Needed when it returns: the
screen, whether the traffic lights were shown, whether a double-click on the bar worked, and the log line if any.

Owner's alpha.13 log (2026-11-04, macOS, three times: 00:32, 08:28, 08:30): `Native window chrome inactive: 'NoneType' object
has no attribute 'isFlipped'` from `CocoaHost.measure` → `_rect`. Cause: pywebview's `windowWillClose_` removes the web view,
which is the window's content view, before AppKit posts the window's last notifications; the resign-key refresh measured the
buttons against a None view, and `WindowChrome.fail` gave the native chrome up (buttons restored, drag monitor removed). Fixed in
`1a2afd84`: `measure` returns nothing and `mouse_down` passes the event through while there is no content view; the chrome
stays active. Reproduced in `tests/test_window_chrome.py` (the old code raises at the same line). Only the close path is known
to clear the content view, so these entries are most likely close-time noise; a failure mid-session would also have replaced
the traffic lights with the fallback buttons. Still needed if the drag fails again: the log lines just before the failure.

Owner, 2026-11-04 (0.6.1-alpha.15, macOS): right after the newly installed build opened (Student Hall), the top bar did not
move the window. The report's logs (alpha.13 sessions and this one): the alpha.13 `isFlipped` warnings each follow
`Native lifecycle: closed complete`, so they were close-time noise, fixed by `1a2afd84`; the alpha.15 session has no window
chrome line at all, so the cause is not in the log. Three silent paths in the code could leave the top bar without drag
regions: the page remembered a toolbar layout before the native side took it, so a refused layout was never sent again; the
native side refused a layout without a log line; a missing content view left the chrome waiting until the window moved or
resized, with no notification to measure again. Fixed in `b186e93b` (lead): a layout is sent again until it is taken, a
failed layout pass goes to the diagnostic report, a missing content view is measured again (8 x 0.25 s), and the log now
says when the chrome becomes active and valid, when its drag regions appear or go, when it refuses a layout, and why a
press on the top bar did not drag. If it happens again, those lines show which path it was.

Owner, 2026-11-04 (0.6.1-alpha.17, macOS): the top bar did not move the window again. The log: chrome active and valid,
12 drag regions at start; 31 s later `drag regions: 0`, and 3 s after that a press on the top bar was ignored for
`no drag regions`. The page clears the regions only for an immersive scene (detail or Hall fullscreen, photography),
chrome that is not valid (logged as `waiting`, absent here), or any open popup, consent, settings or Hall modal. So the
press came while one of those was open. The popup rule was stricter than the design (`docs/design/window-controls.md`:
popups remove only the regions they cover): the stage's popups draw below the native bar (`.window-header`, z 1000, over
the transformed stage), so they cover none of it, yet the bar looked live and did not drag. Fixed by the lead: the bar
keeps its drag regions while a popup is open, its controls still wait for the popup; immersive scenes keep no drag
regions as BL-037 accepted. The log now names why the regions are empty (`immersive`, `waiting`, `covered`).

Owner, 2026-11-04 (0.6.1-alpha.18, macOS): after fullscreen the top bar no longer moved the window. The log: `drag regions:
0 (immersive)` on entering, then nothing until a press was ignored for `no drag regions`; no frontend error. The page
cleared the regions at once when a scene (or, before `4841e30e`, a popup) began, but published the new regions only after
`alignTopBar()` had finished within the same layout pass: an alignment still decoding, or any change under the stage
during it, ended the pass, and the bar kept the empty regions. The alpha.17 report fits the same pattern. Fixed by the
lead: each pass publishes the regions at once and again after the alignment; an alignment still pending after 2 s goes
to the report. Reproduced in `tests/js/window_chrome.test.mjs` (the old code fails it).

## BL-079: The bars enter as the game's lobby does; immersive scenes keep them away until the pointer comes

- Status: Accepted by the owner on macOS (alpha.19, 2026-11-04); Windows not reviewed.
- Requested: 2026-11-04 (owner): find the game's animation of the top and bottom bars sliding in when the game starts;
  play it when the App first loads; in fullscreen hide both bars and the window buttons, and slide them out when the
  pointer comes near.

Found: UILobby's root carries a legacy `Animation` with the clip `UIAni_Lobby` (in a dependency bundle), played as the
lobby opens. `Front/NUITop/Top/Right` drops from 333.7 units above its rest, `Front/Bottom/Menu` (the App's bottom menu)
rises from 1043 below; unweighted Hermite keys at 0, 0.2 and 0.42 s cover about 96 % of the way in the first 0.2 s. The
same clip slides the lobby's side buttons in, which the App does not have.

Owner's decisions (2026-11-04): the hiding covers detail and Hall fullscreen and photography, whose own top controls move
down with the bar; a revealed bar is usable (Back leaves the page as usual) and the window drags from it; the top and bottom
edges reveal their own bar; the window buttons wait hidden during loading and come in with the top bar.

Implementation (`chrome-motion.js`, tokens `LOBBY_ENTRY`, `corner-layout.js` insets and `bindEdge`, `window.motion` and
`CocoaHost.slide`): see `docs/web-ui.md` "Bar motion". The reveal and its leaving reuse the clip's curve over the bar's own
painted extent (the game has no such motion); the 16 px edge band and the 500 ms delay are the App's. Photography's bottom
controls rise with the menu as its top controls go down with the bar.

Owner's review of alpha.18 (2026-11-04): the bars are right; the window buttons stayed visible throughout, loading and
fullscreen included. Cause: the motion module's default sender was a stand-in, so `window.motion` never left the page;
its tests passed their own sender. Fixed by the lead: it sends through the App's RPC, with a test of the default.

## BL-080: Student Hall: the system pointer replaces the game pointer for good

- Status: Closed by the owner (2026-11-04): not seen on alpha.20. The alpha.19 flicker fixed (`5e6b3d56`); the stuck pointer's
  cause not identified, the pointer logging (`5e6b3d56`) stays for a report if it returns.
- Requested: 2026-11-04 (owner, 0.6.1-alpha.18, macOS): in the Student Hall, switched to the L2D memorial and in
  fullscreen, the pointer turns into the system pointer, mostly the open hand (sometimes the arrow), as if the Hall's
  "drag the scene" took it over; only a restart brings the game pointer back. Not known whether earlier builds did it.

Lead's notes: the game pointer is a CSS `cursor` on `body` and buttons (`cursor.js`, `--cursor`), which nothing else writes.
The open hand is CSS `grab`: `lobby-touch.js` gives the memorial's host `grab`/`grabbing` over its drag boxes, and the
photography, model and visual-mode surfaces have it too; the Hall camera's pan (middle or right button, pointer capture)
sets no cursor. A pointer that stays wrong everywhere until a restart points at WebKit's native cursor state rather than
the page's styles. Next: find a sequence that reproduces it.

Owner, 2026-11-04 (0.6.1-alpha.19, macOS): the system pointer and the game pointer now flicker in turns; the log had
nothing on pointers. Lead's finding: alpha.19 moves the window buttons with the top bar (BL-079), and every page layout
made the native side align them again, moving them from the offset to their place and back. Each real move of a window
button makes AppKit reset the pointer, which WebKit then set back. Fixed by the lead: buttons already where the alignment
and the offset put them are not moved, and a move or hiding that changes nothing is skipped. The log now says when the
pointer changes 8 times within 10 s, when a system pointer stays 5 s over the page (both from AppKit's current cursor
while the mouse moves), and when the page lays the toolbar out 30 times within 10 s.

