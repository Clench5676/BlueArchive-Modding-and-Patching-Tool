# Web UI

The new GUI: a pywebview window showing a local web front end that reproduces the in-game Blue Archive UI, built
from the approved prototype (`prototype/main-screen-v4`, git-ignored). Game art is read from the user's own install
at runtime and never shipped with the program.

## Screens

Composed screens take their sizes, gaps and alignment from the design system ([`design-system.md`](design-system.md):
the scales in `ui-tokens.js`, the layout primitives in `ui-grid.js`).

The Student Hall, import/assignment, ZIP export and navigation changes are included in 0.5.0, along with office
backgrounds, global portrait scaling,
immersive Hall view and touch-friendly queue reordering. Local alpha review builds preceded the owner's stable
release approval. Runtime checks cover Hall toolbars/selectors, portrait resource reuse and complete batch reveal,
saved layering/mirroring, exclusive dialogue, and pointer-driven playlist reordering with save/reopen. Automated
gesture checks cover touch holds, swipe-to-scroll, cancellation, viewport scaling and stationary-edge scrolling.
Game assets and development data are excluded from packages.

- Screens: consent popup, Student Hall, student roster, Import Mod (Batch Import), student detail (Portrait, Memorial Lobby and 3D tabs), the
  switch popups (portrait, memorial lobby) and the variant picker. The lobby is framed and touched like the game (its prefab data,
  `lobby.json`), plays its intro timeline when it is entered (a tap skips it; the settings' L2D tab turns it off) and
  plays its dialogue with Kivo's voices and the lines in the UI language (Kivo's Chinese and
  Japanese, the Global server's official English and the others); the 3D tab shows the variant's chibi, exported
  from the install or a selected library Mod. A tab can remain available for a library-only Mod even when the
  installation has no corresponding original resource; application remains unavailable in that case.
- Nav entries without a new screen yet open the classic Tk GUI on that tab (`bamt --tab <name>`).
- `bamt` (and the compiled app) starts the web UI; `bamt --classic` starts the classic Tk GUI.
- Released builds look for a newer GitHub release at start and can update themselves (`updater.py`). The offer is
  the newest release above the running version that has this platform's build and its checksum, so a pre-release
  without a build here does not hide an older installable one; when none has them, it is the newest release, without
  an install button. The existing unset-preference default is on; an explicitly saved startup-update opt-out remains off.
- Out of scope for now: the other tool screens.

## Student photography

Local 0.6.0-alpha adds BL-015 photography to student details. The native fullscreen camera entry opens the actual
portrait and memorial renderers; it never enlarges a thumbnail as its capture source. The centered 404:456 guide
matches the student roster, optional thirds stay outside output, and the shutter writes a lossless native-pixel
PNG directly to the library. Expression cards reuse the detail renderer's face cache. Mode switches retain ready
resources, pause hidden clocks/audio and publish a complete frame in place. Route/variant changes invalidate
pending work. Playback, Back and mode buttons reuse the approved native prefabs. The mode button is drawn only when
the student has the other mode (`bridge.info()` tells `hasPortrait` and `hasLobby`); a skin without a memorial lobby or
without a portrait gets none. In the memorial camera's Dialogue / Actions list a dialogue is captioned as the detail
screen names it (`lobby.dialogue`, "Dialogue 3"), never by its Spine clip name; the idle poses in that list have no label
of their own yet and keep their clip names. The owner handles UAT/visual review.

The composed camera controls use centred `ui-grid.js` rows: neighbours are `SPACE.m` (20 units times `S`) apart,
groups `SPACE.l` (28 units times `S`). Occlusion Guide and Grid share 136-unit `CONTROL.buttonS` bodies and one
48-to-34 caption size, including both grid states. Mode, Gallery, Playback and Choices share 102 x 82 px bodies;
the mode control centres beside the taller text pair. Back retains its native 171 x 116-unit body and the shutter
its separate 146 px circle. Gallery action, paging and tool pairs use buttonS bodies, one caption size per pair and
20-unit gaps; the paging row leaves 28 units above the tool row.

`photographs/` belongs to the movable/shared Mod library, not Preview Cache. Its atomic manifest retains per-student
captures and App choices, IDs and SHA-256 digests; image publication rolls back if metadata cannot commit. Chosen
App photographs and configured/applied game photographs cannot be deleted. Preview-cache clearing never removes
captures. Save and App selection avoid unrelated game-writer status reads and do not hold the services lock (the
manifest has its own lock; only a library move is kept out, through `Services.photograph_writes`), and library moves
include photographs even when no ordinary Mods exist. The store's family lookup is bound to the catalog it was built
with, so no code path holds the manifest lock while it waits for the services lock. The roster/family payload's optional `thumbnail` field selects an App capture or an explicit verified
default. `photography.changed {variant}` invalidates the roster without reloading an active camera.

The manifest is read per entry. A photo record or a selection that cannot be used (a bad digest, an id that differs
from its key, a selection of a photo that is gone or of another family after the catalog changed) costs that entry
only: it is logged once, left out of every use and written back unchanged by every write, and a stale selection
reads as the default thumbnail. A new choice for the same student replaces its kept selection. Deleting a photo a
kept row names is refused (`photo_in_use`). A manifest that cannot be read as a whole (not JSON, another version,
wrong top-level types, a symlink) is never rewritten, copied or replaced: the photograph RPCs answer
`photo_library_invalid`, while `roster.list` and `family.get` log it once and use no photograph.

| RPC | Parameters | Result |
| --- | --- | --- |
| `photography.list` | `variant` | Captures, App choice, verified default URL, game intent/availability/error |
| `photography.save` | `variant, png, mode, expression?, action?, source?` | Captures/App choice and saved ID; game status unchanged |
| `photography.select` | `variant, id` (nullable) | Captures and updated App choice; game status unchanged |
| `photography.delete` | `variant, id` | Updated gallery after protected deletion |
| `photography.apply` | `variant, id` (nullable) | Actual writer job ID |
| `photography.restore` | `variant` | Default restore job ID; clears App choice after success |

Capture uploads are bounded to 48 MiB encoded PNG data and 4096 pixels per dimension. Only the save RPC receives the
larger HTTP body allowance; other RPCs retain the 4 MiB bound. Assets use ID routes
`/asset/photography/<variant>/<id>.png`. Defaults use
`/asset/photography/default/<install-id>/<variant>.png?revision=<resource-version>` and verified original bundles;
a changed current install cannot silently serve another install's default.

Game Apply targets `UICharacterCollectionPopulatorUnit/Grid/UICollectCard/Char_Portrait`, not formation or Tactical
Challenge icons. Collection bundles can contain hundreds of images; the composer changes only selected named
textures and checks unchanged object bytes/resource streams. New captures use native dimensions/mipmaps and
lossless RGBA32; restoring one texture inlines its exact original GPU payload. All-default compositions restore
original bundle bytes. Generated composite sources are internal managed `other` Mods with per-texture photo
recipes; the existing protected writer, rollback journal, official backups, pin checks and master-switch manifests
apply. Mods off configures only; On installs. Game errors retain retryable intent instead of a false success.
Dated Collection batches have distinct slots; old configuration keys migrate safely, and photo-only recipes can
rebuild against changed batches. App-only photograph choices remain independent of game intent.

## Languages

- The UI languages are the localization roadmap's M1 (`i18n.UI_LANGUAGES`: zh-CN, zh-TW, en-US, ja-JP, ko-KR), all
  shipped since 0.1.0-alpha.3: `locales/<code>.json` has every en-US key of the web UI and the classic GUI
  (`tests/test_locales.py`). A language is offered (the settings popup, `settings.set language`) once its locale
  file exists (`i18n.offered_languages`), each named in itself and drawn in its own fonts. The classic GUI lists
  every locale file.
- Wording: game concepts take the official server's words (the Global client's Traditional Chinese and Korean, the JP
  server's Japanese, the Global English); Simplified Chinese follows Kivo's community wording. Words the game never
  uses follow each language's community and the UI text separators (Korean: half-width brackets and ", ").
- The Enable Mods switch, the scan consent, the mod prompts, the classic hand-off, the quit prompt, the tray menu and
  Settings' Mods tab take their strings from `webui.mods.*`, `switch.*`, `classic.*`, `quit.*`, `tray.*`,
  `settings.mods.*` and `error.*`, in every language. Install labels (`mods.installNamed`) keep the folder name as it
  is; `mods.helpFolders` joins them with the language's list separator (`、` in zh-CN, zh-TW and ja, `, ` in en and
  ko). Japanese and Korean name the mod library as `settings.library` does (MODライブラリ, 모드 라이브러리). Japanese
  errors that ask for an action keep the polite form (`error.game_running`, `error.classic_open`); other Japanese
  strings are fragments. Repair advice is text only: `error.original_unavailable` says that no official copy is
  left, and `mods.repairSteam` gives Steam's menu path in each Steam client's words (the settled repair); then
  `mods.repairGame` names the game's own Restore Resources in the game's words as another option, never promised
  ("may not replace mods": whether it replaces a CRC-fixed mod is not verified on either server), followed by
  `mods.repairThen`.
- The first start takes the system language (`i18n.match_language`): Traditional Chinese (zh-Hant, zh-TW, zh-HK,
  zh-MO) zh-TW, other Chinese zh-CN, Japanese ja-JP, Korean ko-KR, the rest en-US. Without the language's file (a
  build that leaves it out), Traditional Chinese gets the whole zh-CN screen, the others en-US. `BAMT_LANG`
  overrides it.
- A string a locale file lacks falls back key by key (`i18n.fallback_language`): Simplified Chinese to zh-CN, every
  other language, Traditional Chinese included, to en-US. A Simplified (and Kivo-worded) string in a Traditional
  screen reads as a wrong translation, an English one as an untranslated one, which also shows the translator what
  is missing; CLDR, too, gives zh-Hant the root locale as its parent, not zh.
- Names come with the data per language (`i18n.js` `NAME_LANG`: zh-CN, zh-TW, en, ja, ko); the catalog names every
  variant in all five (the game's own names where it has them, SchaleDB's, curated ones, or translated in the game's
  vocabulary); a missing name would fall back to en, Simplified Chinese ones to zh-CN first. The lobby's lines follow
  the UI language (`LINE_LANGS`: Kivo's zh-CN and ja, the Global client's zh-TW, en and ko).
- Text fitting (`ngui.fitText`) shrinks, then wraps at separators and between words (`Intl.Segmenter` in the text's
  language), keeping the CJK line-breaking rules: no closing bracket, comma, full stop, ellipsis, small kana or
  prolonged sound mark starts a line, no opening bracket ends one. `ngui.fitLabel` also widens the label to the
  width it was fitted to. A control's caption is never ellipsized: Batch Import's Import State caption wraps onto
  two lines inside its button's 116-high body before it shrinks (`import-layout.js` `filterCaption`), and a long name
  takes up to two lines on the Batch Import name plate, inside the plate's height round the label (`namePlate`).
  Options side by side share one size (`ngui.fitGroup`): the option rows (`settings-rows.js` `radioRow`, each option
  measured in its own `lang`, as the Language row names every language in itself) and the Display Settings sort keys
  and filter options (`display.js` `shareSize`).
  A break may follow an asterisk (`Shiroko*Terror`, `＊` in CJK), so a Terror form's name wraps between the character and
  the form (the Hall's student cards). Text with no break to wrap at keeps the largest size under its floor at which it
  fits on one line, not the lowest. `fitGroup` breaks inside a CJK word between characters (the kinsoku rules hold) only
  when no word-level wrap keeps the size at its floor: the Japanese Show in Explorer caption. The detail screen's Show in
  Finder / Explorer caption (`explorer-button.js`) takes up to three lines inside the 180-high body, the folder icon moving
  up to the body's top edge for the third; the prompt cards (`prompts.js` `card`) label a variant by its name alone, the kind
  being the details panel's first chip.
- Fonts: the page draws with Resource Han Rounded CN (`web/fonts`, OFL, shipped). It covers every Han and kana
  character of the zh-TW and ja names and of the games' UI strings, but in Simplified Chinese glyph forms and
  punctuation positions, and it has no Hangul. So the html `lang` (set from the UI language) picks a stack in
  `app.css`: zh-CN and en-US keep the font for everything; zh-TW, ja and ko keep it for Latin letters and digits
  only (`RHR Latin`, the same file with a `unicode-range`) and draw CJK with the system font of the language
  (PingFang TC / Microsoft JhengHei, Hiragino Sans / Yu Gothic UI, Apple SD Gothic Neo / Malgun Gothic, Noto Sans CJK
  on Linux), with the full font last for a glyph none of them has. No font is added. Text in another language than
  the page's carries its own `lang` (a label widget's `lang`, as each language's name in the settings) and draws with
  that language's stack, so a name never mixes two fonts. `ngui.textWidth` measures with the same stack as the text
  draws with, and Hangul counts as CJK for the label sizing. The classic GUI switches the font of its own text
  widgets (drop zones, inputs, status bar, tooltips) to the language's system font the same way
  (`Theme.use_language`); its ttk widgets keep the system's default font.
- The Global client's own fonts (in the install: Noto Sans TC Medium for zh-TW, GyeonggiTitle Medium as the Korean
  main font, Noto Sans and RSU for en and th) are not used yet.

## Process layout

The App lives in `src/bmpt/`; the upstream engine and classic GUI/CLI remain in `src/ba_modding_toolkit/`.
The App imports the engine explicitly. `bmpt/__init__.py` is empty so the engine's fork-added hooks can import
small App modules without initializing the web UI. The launcher (`bmpt.launcher`), `python -m bmpt` and
`python -m bmpt.webui` register `bmpt/locales` with the engine before dispatch. The page reads those App strings
directly; the engine merges registered locale dictionaries with its own values taking precedence. Classic strings
remain in `ba_modding_toolkit/locales`. The legacy `python -m ba_modding_toolkit` command still dispatches the App.
A standalone `python -m ba_modding_toolkit.cli` run does not register App strings and may show the few App-only
error keys raw. Data/settings folders, update assets and user-facing strings are unchanged by the package split.

```
pywebview window (main thread) ── HTTP ──> WebServer (threaded WSGI, 127.0.0.1, random port)
                                              /              static front end (bmpt/web)
                                              /api/rpc       calls, JSON in and out
                                              /api/events    Server-Sent Events
                                              /asset/...     game assets read from the install
                                              /api/classic/* the classic window's channel (its own token)
                                              /api/instance/raise  a second start (the raise token)
```

The front end only speaks HTTP and SSE, never the pywebview `js_api`. So it runs in any browser during development
(`bamt --browser`, or `--serve` for tools), and tests drive the WSGI app without a window. Native features (file dialogs, drag-and-drop
paths, reveal in the file manager) run in Python; dropped paths reach the page as an event.

Every request must carry the session token (cookie `bamt`, set when the page is first opened with `/?t=<token>`, or
header `X-BAMT-Token`), and the `Host` header must be `127.0.0.1:<port>` or `localhost:<port>`.

## Packages

```
src/bmpt/
  config.py      Tk-free settings: same config.toml groups and keys as ba_modding_toolkit/gui/configs.py
  gameassets/    UI atlases, UI textures, prefab layouts, collection art and Spine skeletons from the install
  catalog/       students (families) and their variants, names in zh-CN, zh-TW, en, ja and ko
  library/       portrait, lobby, model and other mods: add, remove, apply, restore, update, extract, compare
  webui/         server, RPC, events, jobs, pywebview window, tray icon, entry point
  web/           static front end, no build step, plain ES modules
```

App module dependencies follow `webui` → `library` → `catalog` → `gameassets` → the existing core modules (`bundle`,
`searching`, `naming`, `utils`, `core`, `mod_install`). `gameassets`, `catalog` and `library` never import `webui`,
`gui`, `tkinter` or `webview`.

## Rules for every service

- **Threads.** Services are thread-safe: HTTP handlers run on many threads at once. UnityPy work on one bundle is
  serialised with a per-bundle lock. Long operations run on the single job worker (see Jobs).
- **No prose in results.** Services return data, not sentences. Errors are `ServiceError(code, **params)`
  (`bmpt/errors.py`); the front end shows the locale string `webui.error.<code>`. An error passes every
  parameter its text names, above all `file`; where there is no file to name, it uses a code whose text has no
  placeholder (`source_unsupported` beside `mod_unsupported`). `tests/test_error_params.py` scans for the omissions.
  Diagnostics go to `logging.getLogger(__name__)`, never `print`.
- **JSON-ready results.** Dataclasses with `to_json()` returning plain dicts; paths as absolute strings, dates as ISO
  strings.
- **Texture decoding.** Every decode of a `Texture2D` read from a bundle goes through `texture_guard.texture_image`
  (or `check_texture` before a decode of its own). A texture is refused unless its width and height are positive and at
  most 16384, its format is one UnityPy decodes (the crunched formats are not) and its image data, stream data
  included, covers the declared size. A refusal raises `ServiceError("bundle_invalid", file=...)` where the bundle file
  is known, else the `ValueError` subclass `TextureRefused`; callers treat it like any other unreadable texture.
- **Bundle sizes.** `Bundle.load` is the only place the App hands a bundle file to UnityPy, and it first runs
  `bundle_guard.check_bundle`. That reads the UnityFS header and block table (`unityfs.read_layout`, which decompresses
  no data block and bounds the block table itself) and refuses a bundle whose file size, single block, total declared
  uncompressed size or block table is above a ceiling. A refusal is `ServiceError("bundle_invalid", file=...)`, and
  `Bundle.load` reports it as any other unreadable bundle (`None`). A file that is not UnityFS goes on as before. Each
  ceiling is at least four times the largest value among the official game files; `tests/test_bundle_guard.py` measures
  them on the verified development view (`BAMT_TEST_GAME_DIR`).
- **Game assets.** Two classes:
  - *Light*: atlas sprites, atlas metadata, UI textures, prefab layouts and the bundle indexes. Kept in memory; written
    to the disk cache only after the user agreed (`consent = true`).
  - *Heavy*: collection backgrounds and portraits, Spine skeletons, lobbies and their data, and 3D models use memory
    and the default-on **Preview Cache** setting permits lossless exports and generated PNG thumbnails in
    the user cache directory. Browser responses remain `Cache-Control: no-store`; voices remain memory-only.
- **Disk cache.** `user_cache_dir()/<resource_version>/...`, where `user_cache_dir()` is
  `%LOCALAPPDATA%\bluearchive-modding-patching-tool\cache` on Windows,
  `~/Library/Application Support/bluearchive-modding-patching-tool/cache` on macOS and
  `$XDG_CACHE_HOME/bluearchive-modding-patching-tool` (or `~/.cache/...`) elsewhere. It can be cleared from the UI.
  It keeps the three most recently used resource-version folders (Global and Japan can both be in use; a read or write
  marks a folder used): a new version's first successful write deletes the other version folders, only those directly
  inside the cache root, never links or the `previews/` store. A folder that cannot be removed is logged and skipped.
  The old `BA-Modding-Toolkit` user folder is renamed automatically on first start; obsolete caches are rebuilt.
  Close older app windows first, then use the new version: an old build started afterward can recreate its old folder.
- **Preview cache.** On by default, preserving an explicitly saved off setting and independent of the initial official
  UI asset permission and light UI caching. Stored under the configured cache directory's
  `previews/` folder, bounded to 2 GiB with least-recently-used eviction. Entries include installation/resource/export
  identity, source revisions and model dependencies. Still keys additionally include renderer parameters and current
  background/framing content, and the renderer's own code: every still names its renderer (`renderer` "model" or
  "spine" in `preview.cacheGet` / `preview.cachePut`) and the backend keys it by the content hash of that renderer's
  pixel files (`webui/stills.py`), so a release that draws a still differently never serves the old one;
  `tests/test_still_renderers.py` holds every import of those files to a pixel or non-pixel list. Corrupt entries rebuild. A PNG above 2 MiB remains memory-only; it is not downscaled.
  **Clear Cache** deletes stored previews and backend memory without interrupting the active frontend scene or import
  selections. Clear, opt-out and location changes invalidate pending writes. Turning caching off keeps stored files
  until Clear; new renders after Clear may create new entries while caching remains enabled. Optional preview cache
  RPCs have a 15-second timeout and fall back to live rendering when storage or revision checks cannot finish.
- **Resource version.** SHA-1 of `Catalog/Windows/catalog_Windows.hash` when present, else of the sorted
  `(name, size)` listing of the resource folders. The cache key; a game update starts a new cache folder.
  The UI atlas index additionally keys on the current atlas bundle inventory (resolved paths, sizes, modification
  and change times). Discovery is reused while resource directory generations remain unchanged; known atlas files
  are still revalidated on each request, so adding bundles to a partial install or
  sandbox invalidates the index and cropped sprites even with the same catalog hash. Empty indexes, unresolved
  atlas replacements or textures, failed reads and changes during a scan are logged and never cached. Old atlas
  entries use a different key. The atlas JSON uses `no-store` in both the request and response; consent still
  permits the validated index and sprite PNGs in the app's disk cache.
  NGUI rejects empty or malformed atlas metadata and retries on a later render; a requested sprite missing from
  an otherwise valid index also invalidates it. Failures keep the existing placeholder geometry and log the cause.
- **Stills being made.** A picture with a placeholder (`clipImage` with `placeholder`: switch cards, details panels,
  import previews) holds its place with the game's loading ring while its still is made (BL-135):
  `UIRatioNoticePopup/Center/Popup/Base/LoadingIcon` (`Common_Login_Loading`, 75 x 75), turned as its `TweenRotation`
  turns it (the layout exports a tweener's `tween`: from, to, duration, delay, style, method; 180 to -180 degrees in
  3 s, a loop) and shown after 0.2 s, so a still that comes at once never flashes it. The placeholder text
  (`import.previewUnavailable`) shows only when the still fails; without the prefab the text stands in as before.
- **Read-only data folder.** The services test the data folder at start by writing and removing a real temporary
  file (`folder_writable`; `app.info.dataDirWritable`). No fallback to another folder is made. A write that the
  permissions refuse, to a file in the data folder while that folder cannot be written, answers `data_dir_readonly`
  (RPC calls and jobs alike, through `Services.translate_error`) instead of `internal`; a single busy file in a folder
  that takes new files stays an ordinary failure.
- **User data.** The mod library and UI state live in `utils.EXE_DIR` (the portable data directory the app already
  uses for `config.toml` and `output/`). Several instances may share it: a change to the UI state (`webui.json`) is
  applied to the file as it is at that moment, read again under a lock the instances share (`webui.json.lock`), so
  no instance erases another's keys, and reads follow the other instances' writes. Those reads take the same lock, as
  Windows cannot replace a file another process has open; a replace that meets another program's handle (a virus
  scanner, the search indexer) is tried again for up to a second there. The file is flushed to disk before the
  replace, and the last good file stays as `webui.json.prev` before every write. An unreadable `webui.json` is read
  from that copy and kept as `webui.json.bad` at the next write; with neither readable, the state starts from the
  defaults, an error is logged and the bad bytes are kept as `webui.json.bad` before the first write. The strict
  read behind the seen mods does not use the copy: unreadable state answers unknown, never every mod as new. The
  copy and the bad file move with the data directory (their stored paths are rewritten like `webui.json`'s).
  `config.toml` never stops the start: a group that is not a table or a value of another type reads as missing
  (its default; the rest of the file is still read), and a file that cannot be parsed as a whole (non-UTF-8 bytes, a
  syntax error) starts on defaults. Before any save replaces such a file it is copied to `config.toml.bad`, and the
  2-second poll for the classic GUI's saves never applies a read that failed or found no content (a save in
  progress): it reads again once the file changes. The user's own aliases of the students are there too (`aliases`:
  `{family key: [alias]}`, one student's entry changed at a time), never in the program folder or the repository.

## gameassets

```python
class GameAssets:
    def __init__(self, resource_dirs: list[Path], cache: AssetCache): ...
    resource_version: str

    # light
    def atlases(self) -> dict[str, dict[str, dict]]           # {atlas: {sprite: {x, y, width, height,
                                                               #   borderLeft, borderRight, borderTop, borderBottom,
                                                               #   paddingLeft, paddingRight, paddingTop, paddingBottom}}}
    def sprite_png(self, atlas: str, sprite: str) -> bytes
    def textures(self) -> list[str]                            # UITexture names
    def texture_png(self, name: str) -> bytes                  # also the pointer and touch effect textures
    def layout(self, root: str, subpath: str = "") -> dict     # prefab subtree, schema below

    # heavy
    def collection_bg(self, name: str) -> tuple[bytes, str]    # (data, mime)
    def collection_portrait(self, name: str) -> bytes          # PNG
    def collection_names(self) -> dict[str, list[str]]         # {"bg": [...], "portrait": [...]}
    def spine_cores(self, kind: str) -> dict[str, list[Path]]  # kind "spr" | "lobby": core -> bundle parts
    def spine_files(self, kind: str, core: str) -> dict[str, bytes]        # file name -> bytes (.skel, .atlas, pages)
    def spine_files_from(self, bundles: list[Path]) -> dict[str, bytes]    # the same, for a mod's bundle files
    def lobby_info(self, core: str) -> dict                    # a lobby's layers, touch boxes, clips, idle pools,
                                                               # sound events, post-processing (lobby.py), particle
                                                               # and mesh effects (particles.py, meshfx.py), page
                                                               # materials (spine_materials.py), pins (pins.py) and
                                                               # intro (intro.py)
    def lobby_fx_texture(self, core: str, tex_id: str) -> bytes # a texture lobby_info's "fx" lists, as PNG
    def lobby_sound(self, core: str, clip_id: str) -> bytes   # a clip lobby_info's "sounds" lists, decoded to WAV
    def model_cores(self) -> set[str]                          # dev names with a chibi (character-<dev>-_mxload-*)
    def model_glb(self, core: str) -> bytes                    # the chibi as one GLB (model.py)
```

The collection art is read where the game loads it: CostumeExcel's `CollectionBGTexturePath` and
`CollectionTexturePath` point into `UIs/01_Common/14_CharacterCollect` (Addressables keys
`uis/01_common/14_charactercollect/<name>`), the `uis-01_common-14_charactercollect-*` bundles. The index takes every
`BG_*` texture there (most are `BG_<place>_Collection`, a few are named after the scenario background they reuse:
`BG_NewYearFestival2` for the New Year variants, `BG_WaterFall`) and the `Student_Portrait_*_Collection` portraits;
it holds names and bundle locations only, in memory. `/asset/art/bg/` serves only names starting with `BG_`,
`/asset/art/portrait/` only `Student_Portrait_`.

`model_glb` reads prefab bundles and resolves their external CABs on demand. Model, memorial and office graphs
share the revision-checked `gameassets/cabs.py` resolver: same-character/group bundles, then prolog/common bundles,
then the remaining directory, stopping once all requested names are found. Graph traversal batches metadata writes.
The optional preview cache persists bounded, checksummed CAB metadata across launches, confined to the installation
and checked by device, inode, size, mtime and ctime. Source changes, new dependencies and cache clears invalidate
stale mappings. The shared `unityfs.py` parser reads only header/block info and uses the declared bundle length to
ignore CRC-correction tails. Export locks, cleanup, overrides and missing-dependency safeguards remain in place.
Complete GLBs can use the separately consented lossless preview cache; partial exports stay memory-only. Every cache
hit revalidates the dependencies recorded by its export.

The GLB carries what the game's shaders need: per material its shader, shading kind, constants (`colors` as stored,
`vectors` naming the Vector properties and `hdr` the HDR Color properties the game passes unconverted; it converts
every other Color property from sRGB), `keywords`, whether it draws an outline (its
shader's Outline pass, unless the material turns that pass off) and its colour pass `state` (cull, depth write, blend, queue, resolved against the material); per texture its sampler (filter,
wrap, mipmaps as in the game: the characters' textures have none) and `extras.linear` for masks; per clip the renderer
switches it animates (`toggles`: alternative faces), so a renderer the prefab disables is exported when a clip turns
it on. Euler rotation curves are turned into quaternions in the rotation order their binding carries (`customType`:
Unity's own ZXY for curves made in Unity, XYZ where an FBX kept Euler curves, as NP0186's battle idle). Renderers
only an EX skill shows are kept aside the same way, though the prefab enables them: the alternative expression faces
(of the face renderers, the ones without eyes beside one with them; the gag face lies in front of the eyes, and the
skill's timeline, not the Animator, switches the faces) and a cut-in copy of the body.
`web/js/chibi-materials.js` ports the MX/C-* shaders from their compiled D3D11 programs (the game is in linear
colour space; the light is the formation screen's, below), and for props
DSFX/FX_SHADER_Matcap (a riot shield, soup), MX/E-Water (the surface colour: the game deepens it by the camera depth
behind it, which a lone chibi lacks) and the unlit and effect shaders, each with its own texture slot and colour. An empty
texture slot samples the shader's default texture, as in the game: MX/C-Halo/UnlitTextured without a texture (ch0109's,
EN0009's halos) draws white x its HDR `_Tint` plus the glow; only MX/C-Halo/Simple draws a flat colour. Terms the
game's vertex programs compute stay per vertex: the outline's colour (the texture sampled at the vertex), MX/C-Weapon's
N.V and N.H. The outline is 0.1 % of the stage width times the vertex colour alpha, as in the game. There is no ground
shadow: the formation screen draws none.

The camera opens at the formation screen's view of one character (`FORMATION_VIEW` in `model.js`). GameMain's camera
(vertical field of view 10, at the origin, level; `CameraFovScaler` only widens it on screens narrower than 4:3) draws
the NGUI root 11.43 ahead, 2 units high, so 2 / 1665 of a unit per NGUI unit at 16:9 (UIRoot: constrained, 2960 x 1440,
fit width and height). UIFormation parents each character to its slot (`TeamPosition.SetCharacter`: local position,
rotation and scale reset), UnitPositions/Position1-4: 630 NGUI units to the character's unit, 316.4 toward the camera,
222.6 below Position_Set, whose anchors (BottomMenus to EchelonTab + 720) put it 56.25 below the screen's centre at
16:9, turned 180 degrees to face the camera. In the character's units the camera stands 14.6 away and 0.44 above the
feet, looking level, and the screen's centre lies at that height: fullscreen shows exactly that (the chibi at the
game's scale, about 42 % of the screen's height), beside the panel the same view moved left and up so the feet stay
above the nameplate. A chibi whose box would leave the host is framed from further back (`frameDistance`). The camera
orbits freely around the point at the formation camera's height, which rises to the face as it closes in; the zoom
stays between the bust (`bustDistance`: the face on the framing's point, the halo's top at the host's top edge, about
0.3 of the distance) and a step back (1.3). Fullscreen and back keep the zoom's place between the two (`carryZoom`),
and a double click returns to the front view. The switch card's still is rendered from that front view at the
framing's distance (`stillCamera`), whatever the orbit, the zoom and the pan.

Input (`attachInput`) is read as in the fullscreen lobby (`lobby-touch.js` `wheelStep` / `wheelKind`); the orbit
controls only orbit (the left button, one finger):

- *Mouse wheel.* Zooms in steps of the zoom (each multiplies the distance; `zoomShare` is the place between the fit
  and the bust in those steps): five notches from the front view to the bust (`wheelZoom`). A notch counts at least
  100 px (a Chromium / WebView2 notch; Firefox's 3 lines are 120 px; a slow macOS notch, a tenth of a line, is only
  4 px), a faster spin's bigger steps zoom further, and one notch out is about the step back.
- *Pinch.* Zooms half as far again as the fingers spread (`pinchZoom`: the distance times scale^-1.5), so one pinch of
  about 2.2 x goes from the front view to the bust: ctrl + wheel in Chromium and WebView2 (the scale is
  exp(-deltaY / 100); ctrl + a mouse wheel zooms by notches), gesture events in WebKit (the macOS app's WKWebView; the
  ctrl + wheel events some versions send beside them are left to the gesture), two fingers on a touch screen.
- *Pan.* A two-finger trackpad swipe, a Magic Mouse scroll or a horizontal wheel, and a right- or middle-button drag,
  move the chibi as far as the fingers or the pointer moved, while zoomed in: the orbit target stays inside the
  chibi's box scaled by the zoom's place (`clampPan`: nothing at the fit, the whole box at the bust), so zooming out
  draws the pan back in and the front view has none. A double click also unpans.
- *Easing.* The distance and the pan ease to their goals (0.08 s, as the lobby's view); a notch or a pinch adds to
  the goal, which is clamped to the limits. A zoom or an orbit during a glide (a double click, fullscreen and back)
  takes that part over from it while the framing glides on.
- *Lift.* The target rises with the zoom's place so that the face comes to the framing's point evenly, notch by notch
  (`liftShare`: about 28 px per notch for Koharu beside the panel).

The OrbitControls zoom it replaces took 58 Chromium notches from the front view to the bust, about 1430 slow macOS
notches and 26 trackpad swipes of 220 px on a 2x screen (it divides by `devicePixelRatio | 0`, so below a ratio of 1
one event jumped to a limit), ignored `deltaMode` (Firefox: 0.06 % a notch), never saw WebKit's gesture events (no
pinch in the macOS app) and needed some 80 Chromium pinches of 2 x; a zoom during a glide was overwritten until the
glide ended, and the lift, eased by distance, did two thirds of its rise in the first fifth of the zoom, dropping the
chibi before it grew.

The light is the formation screen's, read from the game rather than fitted to renders:

- *Light colour.* The game's modified URP (`ForwardLights.SetupMainLightConstants`) derives `_MxCharLightTone`,
  `_MxCharShadowTone` and `_MxCharLightData` from the main directional light (or a `CharacterLightColor` override):
  hue and brightness apart, data.x = 0.85 max + 0.15 (`characterLight`). UIFormation's light is white, intensity 1:
  tone 1, data (1, 1).
- *Direction.* `_MxCharLightDir` comes from the topmost `CharacterLight`; on the formation screen that is GameMain's
  CharacterLightFallback, from above, 6 degrees toward the screen's right and away from the camera. Light directions
  are world space in the game (`_MxCharLightDir`, MX/C-Weapon's `_FakeLightDir`), and the formation screen turns every
  character 180 degrees to face the camera (its slot's rotation, an axis 0.4 degrees off the vertical): `slotSpace`
  takes them into the character's space, so the light falls from the side it does in the game (weapons are lit from
  the screen's right).
- *Ambient.* Layer4, Single, Hair, Face and Weapon add the ambient probe (`unity_SH*`), evaluated per vertex at the
  world normal, clamped at 0 and scaled to a channel sum of at most 0.3. Outside battle the active scene is EmptyScene
  (`Persistent.GameStart` loads it alone; only battles, minigames and Undercover set another scene active), Trilight
  ambient with the colours SceneLightingLoader also uses as its defaults. `trilightProbe` turns them into the probe as
  Unity 2021.3 does (UnityPlayer: a cube map of 2 x 2 texels a face, +Y sky, -Y ground, the sides equator, projected
  onto L2 SH, then the cosine lobe): about +0.1 in every channel, bluer on surfaces that face up.

Other screens differ: the cafe (Cafe3D) sets its own Trilight colours (a neutral +0.1), and each battle stage has its
own main light, ambient and baked light probes. Against the 30 EnemyInfo renders, the 21 newer ones (the `CH0xxx` and
`*_Original` names) match with the ambient (median game / tool ratio in linear light: lit hair and body 1.00, 1.10
without it; shadowed hair 1.02 and body 1.07, 1.18 and 1.25 without it), and the 8 older ones (`Aru_SR`,
`Hoshino_SG` and the like) match without it: they were rendered in a scene without ambient, about 10 % darker than
the formation screen.

Checked against four screenshots of the formation screen (Global client, 2000 x 1125 game area): 14 students rendered
at the formation camera and their slots line up with the screenshots to the pixel (silhouettes, feet at 757 of 1125),
the Formation_Idle time picked per student by the closest match. Per region, the median game / tool ratio in linear
light over the students: face 1.01, lit skin 1.01, shadowed skin 1.01, lit hair 1.01, shadowed hair 1.00, lit clothes
0.99, shadowed clothes 1.00, lit weapons 0.99, shadowed weapons 1.05 (1.15 with the light in world space), halos 1.05.
Taking the light into the slot's space lowers the mean difference on the same pixels for every student: hair 4.8 to
3.7 sRGB levels (median), bodies 6.7 to 5.6, weapons 10.0 to 7.9; the faces, lit by their own direction, are unchanged.
What remains: the screenshots are softer (a Gaussian of about 0.6 px brings the renders closest; compression or the
game's resampling), fine dark lines come out lighter in them (the outline's dip about 0.75 of the tool's at the
silhouette and 0.82 inside, lines painted in the textures 0.87: partly the screenshot's own loss of fine detail),
halo sparkles (the halos' particle effects, not exported) and the formation screen's occasional
Formation_Idle_Random (`CharacterEchelon` plays it instead of the idle at random where the prefab has one, as Mika's
swimsuit does in the screenshots; the tool plays Formation_Idle only).

`AssetNotFound` (a `KeyError`) when a name is unknown. Layout nodes use the schema of
`prototype/research-v4/tools/layout.py`: `{name, active, pos[x, y], scale[x, y], rotZ, widgets[], children?}` with
the widget fields documented there; atlas and texture references are resolved to names.

**Button states.** The game's buttons (`MXButton`) carry no NGUI button colours: no prefab has a `UIButton` or
`UIButtonColor`, so there is no `disabledColor`, `hover` or `pressed` to read. A disabled or locked button is a node of
its own that a controller shows instead of the normal one, and its look is in that node's widgets. The layout gives
these controllers a `states` object, each field's node as a path below the controller's node (what `subtree` and
`?path=` take; `""` is the node itself, `null` an unset field):

| Controller | Fields |
|---|---|
| `OpenConditionButtonController` | `Enable`, `Disable`, `Prohibited` |
| `UIButtonEnableSwitch` | `enableButton`, `disableButton` |
| `MXDualStateButton` | `NormalButton`, `DisabledButton` |
| `ButtonActivator` | `activeObject`, `inactiveObject`, `button`, `contentsLockObject` |

The lobby's bottom menu (`UILobby`, `Front/Bottom/Menu/BG/Grid/<entry>`, a prefab hint) holds the game's disabled look
for the greyed navigation. An entry with an open condition (Cafe, Academy, Craft, Shop, Gacha) shows `Able`, the icon
sprite untinted (`#ffffff`), or `Disable`, the same sprite at `#797979` with `Common_Icon_Lock` over it; Student,
Formation and Social have the same `Disable` node without a controller. The label is the entry's own child, outside
both, and the prefab gives it no disabled colour (`#2d4664` or `#2c4564` in both states). Checked on every prefab of
the 2026-10 resource version: 421 controllers and 1124 references, each inside its controller's node and unambiguous
by name.

Cached layouts carry the export's format in their key (`layout-v2/<root>`, `_LAYOUT` in `service.py`). A change to
the export raises it, so layouts cached before are exported again and no other cached asset is dropped.

## catalog

```python
@dataclass
class Variant:
    key: str                  # file_id lower-cased: "hoshino", "hoshino_swimsuit", "ch0241", "np0045"
    file_id: str              # as in the character map: "CH0241"
    family: str               # Family.key
    names: dict[str, dict]    # {lang: {"full": "星野（一年级）", "short": "一年级"}}, langs zh-CN zh-TW en ja ko
    playable: bool            # a playable student (on some server)
    order: int                # release order inside the family
    spr: str | None           # Spine portrait core, e.g. "ch0241_spr", when the install has it
    lobby: str | None         # memorial lobby core, e.g. "ch0241_home", when the install has it
    bg: str | None            # collection background texture name
    portrait: str | None      # collection portrait texture name (None: the front end renders a bust)
    date: str | None          # date of the portrait bundle
    page_id: str | None       # the student page on Kivo, for lobby lines
    recruitable: bool         # on the selected server: its collection portrait is installed, else the shipped flag
    model: str | None         # 3D chibi dev name, e.g. "hoshino_original", "ch0242", when the install has it
    schale_id: int | None     # the game's character id (SchaleDB's), for the Global server's lobby lines
    aliases: list[str]        # the CN server's zh-CN names, e.g. "桃（武装）": the search matches them, the UI never
                              # shows them

@dataclass
class Family:
    key: str                  # the base variant's key, e.g. "hoshino"
    names: dict[str, str]     # base name per language
    school: str | None
    variants: list[Variant]
    aliases: list[str]        # the CN server's base names, e.g. "桃" for Momoi, "康娜" for Kanna

class Catalog:
    def __init__(self, game: GameAssets | None, character_map: Path | None, data: Path = <package data>,
                 server: str = "global"): ...           # "global" | "japan", Settings.server_region()
    def families(self) -> list[Family]
    def family(self, key: str) -> Family
    def variant(self, key: str) -> Variant
    def variant_for_core(self, core: str) -> Variant | None    # "ch0241_spr" or "ch0241_home" -> Variant
    def core_map(self, spr_cores, lobby_cores, special_cores=()) -> CoreMap  # any install's cores -> variant keys
    def reload(self) -> None                                    # parse the data again, rebuild the snapshot
```

`core_map` runs the roster's own core rules on another install's core names, so the library keys a core the same way
in every game folder: a portrait core's key is the core less `_spr`; a lobby core belongs to the installed variant
whose key or core alias plus `_home`, `_home_gl` or `_home_teen` names it, a direct file-id match first; a lobby core
whose variant has no portrait in that install takes the variant the data names for it the same way, else the core less
its suffix. `CoreMap` also gives each key's portrait core and primary lobby core there, and the shipped names of the
keys the data has (the cards of a core the current catalog does not map). The shipped data and the character map are
parsed once and kept; every build and every map works on a copy, so one install's unknown portrait keys never enter
another's, and `reload()` parses both again, keeping the old parse and snapshot when the data is invalid.

Special Spine slots use `special:<lower-case core>` keys and the lobby workflow. Discovery accepts named
`spinebackground` bundles and nonstandard cores in `spinelobbies` and `spinecharacters`; shared `_mxcommon` bundles
are excluded. The category is retained on the variant (`spineCategory` in RPC data), including backgrounds whose
cores happen to end in `_home` or `_spr`. They stay separate from student portraits and memorial lobbies. Import,
apply, restore, configuration, the global toggle and entry scans all classify these files by category as well as
core. Parts still match by prefix and resource type, so an older mod keeps its target category.

Repository naming evidence: `naming.py` already lists `assets-_mx-spinebackground-` among common mod prefixes;
the reported `arona_workpage_daytime` filename uses that category and the existing dated, numbered-part syntax.
The shipped catalog and character-map-derived metadata identify `arona` as Arona, `np0035` as Plana, and `np0032`
as Aoi (the Traditional Chinese name in the report refers to Aoi). Background prefixes `arona`, `plana` and `aoi`
join those families even when the install has no portrait for them. Character file IDs and aliases use the same
metadata. Other scenes share the Special family. Known role tokens (workpage, daytime, night, task, cafe) have
locale labels; unknown names retain their exact core. The repository ships student metadata, not a complete
game bundle-name inventory, so background discovery uses the category instead of a fixed scene-name allowlist.

Roster cards, chips, the picker, search results and arrow navigation include these lobby-only variants. Their
thumbnails and layered previews use the existing revisioned Spine routes. Container paths under
`SpineBackground/<core>/` identify the owning scene even when its skeleton layers have unrelated student or
background names; conflicting scene containers are refused. Texture-only or stripped parts can use the declared
installed slot. Missing skeletons, ambiguous atlases or unsupported scene layouts leave a plain card and the
apply/restore controls available. Student dialogue lookup is skipped for special slots. Actual scene framing,
animation and game rendering still require checking an isolated game install.

Sources: the character map CSV the app already downloads (`BA-Characters-Internal-ID.csv`, one row per file id, with
student id, names and skins in several languages), the curated data file shipped in the package (names,
backgrounds and portrait names, generated by `scripts/build_catalog_data.py`), and the game scan (which skeletons
exist, dates). Skeletons missing from the map become their own family, named by file id. Names follow the variant name
format: base name plus the variant in brackets, never `·` or `・`.

Languages: zh-CN follows Kivo (below); zh-TW, en and ko are the Global server's official names, ja the JP server's.
Playable students take SchaleDB's names per server (`students.{cn,tw,en,jp,kr}.json`); NPCs and story sprites the
character map's columns (`name_kr`, `skin_kr` for ko) and `catalog/data/naming.json`: its name table and glossary
give each language's word, from the Global client's ScenarioCharacterName (the scenario name of the sprite's own
`CharacterSpine_<file id>`; the JP server's for ja) where the game names it, else a translation, and say which
(`source`, `basis`). Korean writes names as the game does: half-width brackets without a space before them and the
Terror asterisk half width (호시노(수영복), 시로코*테러), parts joined by ", ", the base variant 기본; a ko skin of the
map that the game words otherwise yields to the glossary (the map's 임전, 정월, 캠프: the game's 무장, 새해, 캠핑).
`naming.json`'s `story_names` hold the story variants whose research names (prototype `names.json`, wiki names)
lack a language or use a community word (ko for the Hoshino and Kisaki story sprites; zh-TW's SchoolYear and Kiki
words).

Aliases: zh-CN shows Kivo's community wording, and the CN server's wording that it replaced stays findable. The
builder writes `aliases` (only when not empty) per student and per variant: a student's are the server's given names
(the map's `name_cn` and SchaleDB's cn `PersonalName` of the variants named by the base alone, so a Terror form stays
apart, and the name table's `cn` wording for NPC names such as 崔尼蒂学生A); a variant's are its zh-CN name with
those names, its own row's `name_cn` and the server's costume words (the map's `skin_kivo` / `skin_cn` pairs, e.g.
临战 / 武装, 兔女郎 / 邦妮, and the glossary's `cn` for words only Kivo's remarks use, e.g. 运动服 / 体操服), and
SchaleDB's cn name of a playable variant. zh-TW keeps the Global server's official names; these are the CN server's.
Rows the map adds after the shipped data get theirs from their own `name_cn` and `skin_cn`.

Recruitable follows the server of the selected game folder. An install ships story portraits of students its server
has not released yet (Global had Tsumugi's before her banner), but each server builds its collection bundles with
the students it has released, so a playable variant is recruitable when its collection portrait is installed. Without
one, the data file's `released` flags (`{"japan": bool, "global": bool}`, from SchaleDB `IsReleased`) decide: true
covers a portrait-name gap, false a student not on this server yet. With no flag either, the variant is recruitable
only when the install has no collection portraits at all. Nothing new is read or cached: the portrait index is the
memory-only one the catalog already uses. A portrait that arrives in a patch before the banner opens shows the student
as recruitable a few days early. Not-recruitable playables stay in the roster and sort after the recruitable variants
of their family.

## library

```python
class ModLibrary:
    def __init__(self, root: Path, catalog: Catalog, game: GameAssets | None, settings: Settings, *,
                 scan_allowed: Callable[[], bool] = lambda: False, data_id: str = "local",
                 write_check: Callable[[Pin], None] | None = None): ...   # write_check: the caller's own refusal
                                                   # right before game files are written (the web UI: classic_open)
    def mods(self) -> list[ModEntry]               # id, file, label, target (variant key), kind ("spr" | "lobby"),
                                                   # added, fresh, size, date (its newest part's), crc, core (lower case)
    def add(self, paths: list[Path]) -> list[ModEntry]   # copies into the library; target from the file name's core
                                                         # (portrait "_spr", lobby "_home", "_home_gl", "_home_teen");
                                                         # one mod per core and kind, or per model slot;
                                                         # see "Import grouping"
    def remove(self, mod_id: str, confirm=False) -> None # refused while a game folder shows it (see below)
    def rename(self, mod_id: str, label: str) -> None
    def mark_seen(self, mod_id: str) -> None
    def favorites(self) -> set[str]; def set_favorite(self, variant_key: str, on: bool) -> None
    def state(self, variant_key: str, pin=None) -> VariantState   # game files, applied ("official" | "mod:<id>" |
                                                         # "variant:<key>" | "removed" | "unknown"), crc ("ok" |
                                                         # "fixed" | "needs_fix"), copy, restorable, official
    def configured(self, variant_key: str, pin=None) -> {"spr": source | None, "lobby": source | None}
    def modified(self, pin=None) -> dict[str, str]       # the roster's Replaced flags (portraits)
    def apply(self, variant_key, source, log, progress, kind="spr", pin=None) -> ApplyResult   # also deferred
    def restore(self, variant_key, log, kind="spr", pin=None) -> ApplyResult
    def would_write(self, variant_key, source, kind="spr", pin=None) -> bool   # for the classic window's rule
    def preview_bundles(self, mod_id: str, pin=None) -> list[Path]
    def preview_complements(self, core: str, names: list[str], pin=None) -> list[Path]
    def preview_incomplete(self, mod_id: str, pin=None) -> bool
    def model_cores(self, variant_key: str, pin=None) -> list[str]  # prefab first, own installed dependency cores
    def official_bundles(self, core: str, pin=None, *, current_fallback=False) -> list[Path]  # whole slot;
                                                        # verified originals; model previews may allow current files
    def preview_reads(self, policy: str, pin=None) -> PreviewReads  # "official" | "configured": the bytes a model
                                                        # preview reads for the files its source does not supply
    def prune(self) -> list[Path]                        # kept originals deleted (see below)
    def update(self, mod_id: str, log, progress) -> UpdateResult   # rebuild against the current game file
    def extract(self, target: str, out_dir: Path, log) -> Path     # target "variant:<key>" or "mod:<id>"
    def compare(self, mod_id: str) -> CompareResult                # per-asset differences vs the game file
    # the Enable Mods switch and mods already in use, over every known game folder
    def installs(self) -> list[Pin]; def current_pin(self) -> Pin | None
    def toggle(self, enabled, expect, pins=None, log, progress, cancel) -> ToggleResult   # also Retry
    def restore_drift(self, entries, pins=None, log, progress, cancel) -> ToggleResult    # Apply Mod Configuration
    def scan(self, pins=None, cancel=None, include_dismissed=False) -> ScanResult         # needs the scan consent
    def entries(self, pins=None, include_dismissed=False) -> list[EntryState]
    def adopt(self, entries, pins=None, log, progress, cancel) -> AdoptResult             # Add to Library
    def dismiss(self, kind: "drift" | "untracked", entries) -> None                        # Keep as Is, Don't Ask Again
    def forget(self, install: str) -> None                                                 # Remove from List
    def clear_built(self) -> int                                                           # Clear Mod Cache
    def judge_unfinished(self) -> int; def intent(self) -> dict; def status(self) -> dict
```

**Switching** is what the detail screen's switch cards do (Switch Portrait, Switch Memorial Lobby): `apply` writes
the chosen source into the variant's game files of that kind, so the game itself shows it. The sources: the official
files (`official`, the same as `restore`), a mod (`mod:<id>`; one made for another variant of the family is adapted
to this variant's files), or another variant's official files (`variant:<key>`, adapted). A mod replaces every part
of its kind as a unit: parts it has no file for go back to official. The portrait and the lobby are switched apart.
A mod is installed directly when its core is the target's core; a lobby mod belongs to one lobby core. The variant a
mod belongs to is the one the current catalog gives its core, else the target stored when it was added.

**Game folders.** Every call that reads or writes game files works on a `Pin` (an install key, its resource folders
and its server), taken from the settings at the call, or given by the caller (the web UI's jobs pin what they saw at
submit and are refused with `folder_changed` when the folders changed meanwhile). An install key is the install root
of the setting (a setting naming a resource folder maps to the folder above it). The known installs, the ones the
switch covers, are the Global and Japan folders the settings name, whatever the file source (ADB mode included),
then every install with a configuration record whose folder exists. Without a current install (ADB mode, the folder
missing) the switch still works over the others.

What a switch replaces is always kept:

- **An official file** is copied to `originals/<file name>` the first time it is replaced. The official copy is, in
  this order: the original already kept, the game file itself, its `.backup` beside it (the classic GUI's, judged by
  its name without `.backup`; the library never writes or deletes `.backup` files, also when an import's backup
  option is on: the original it keeps first is the baseline), or a file of its name in another
  game folder; at write time each is checked by the CRC32 of its whole content against the CRC in its name, so a
  modded file is never kept as official. Missing official copies do not prevent a direct Mod import or apply.
  Restoring requires a verified official copy; without one it leaves the Mod in place (`original_unavailable`).
- **A mod** stays in the library (`mods/<id>/`). While mods are on, `remove` refuses one a game folder still shows
  (unchanged since the library put it there): `mod_in_use` in the current game folder, else `mod_in_use_elsewhere`
  with that folder; in a folder the switch does not cover, in any state. While mods are off removing is allowed: every
  configuration naming it is dropped, and when a game file still carries it with no official copy anywhere,
  `mod_last_copy` asks first (`confirm`); the records of those files are marked removed, so nothing adopts them again
  and the next Off reverts them.
- **Another variant's files** are only read, and can be adapted again.
- **A file changed outside the library** (another tool, the classic GUI, a manual copy), or the bytes of a source the
  library no longer has, is added to the library as a new mod of its core before it is overwritten; a part set
  already in the library is not stored twice. Each file a write reaches is judged in full here (its SHA-1 against the
  library's record, never its size and date, then its CRC32; the write hashes every target anyway, so this reads
  nothing more), so a mod another tool installed without the CRC fix, or with the file's date put back, is kept too.
  This also works when no official copy remains: all unkept non-official parts of the core and kind are stored
  before the first game write. A successful apply reports `kept_current`, shown once as Current game files added
  to the library. A failed write rolls back to the outside bytes from its journal, without calling them official.

A partial Mod takes omitted parts from verified official copies when available, otherwise from the current game
files. Preview and apply use the same order. Such a fallback reports `incomplete` and shows Mod incomplete in the
existing switch status and apply result; ordinary partial Mods with official omitted parts have no such status.
Official previews and restore never use that fallback. Repairing the game files clears the missing-copy state
automatically, and the next apply keeps those verified files as originals.

Every write goes through `_commit`: resource path checks, the game not running and Steam not updating or verifying
the install, the files lockable, atomic replacement, and a rollback of every written part when one fails (the mods it
had just kept are dropped again once every part is back, and the originals it had just kept from the game files are
released). The records of `state.json` say for each file what was written there: the source, its SHA-1, size and
date, whether the bytes are official (`official`, compared with the official copy), the write time (`at`), the last 8
bytes when there are trailing bytes (`tail`) and the CRC state (`crc`). Older builds keep these keys in records they
do not rewrite.

**Kept originals and the prune.** After every switch, at the end of every bulk job, and once per library in the web
UI (a `library.prune` job), `prune` decides about every kept original from names, file stats and records only, over
every known install; it reads no game file's contents. An original stays:

- while a configured entry may write non-official bytes over its file (so turning mods off and on copies nothing);
- while any known install holds a file of its name that is not unchanged since the library wrote official bytes there.
  An official file the library did not write (verified in Steam or by the game, restored by another tool) keeps it
  until the library next writes or records official bytes there: a restore that finds the file official writes
  nothing, records it and releases the original. A file of its name the library never wrote keeps it too: it may be
  another tool's mod with this original as its only official copy;
- while a file of its name the library wrote to elsewhere may need it, judged by stat; a folder that cannot be seen
  (an unmounted drive) is judged by its record.

These rules cover the installs this data directory removed from its list too: in a library shared with a data
directory that still names such an install, both prune alike, so removing an install from the list never changes what
the prune keeps.

Once its name is gone from every known install, a game update superseded it when each of its records' folders holds
a later-dated file of the same part (prefix and resource type): it is deleted. An original no record places, or one
with only an older file of its part (a downgrade), stays. The records of superseded files are dropped once no
original of their name is kept, leaving the entry's `updated` marker when the library had written non-official bytes
there. When unsure, an original stays: the cost is disk space, counted in the switch's help ("Official copies for
Off").

**Configuration (`profiles.json`).** Older builds rewrite `state.json` with their own keys only, so the configuration
lives beside it in a file of its own: the Enable Mods switch (`enabled`, one intent for every known install; missing
means on), its last change (`toggled`), the entries not switched yet (`unfinished`, each `pending`, `left` or
`stuck`), and per install key its server, the data directories naming it or hiding it (`named_by`, `hidden`), the
configured source per core (`configured`), the `updated` markers, the built copies (`built`) and the dismissed prompts
(`dismissed`, files by name, size and date). The user's switch popup sets and drops configured sources; the switch
never changes them. Every change is applied to the file as it is, under `profiles.json.lock`, unknown keys kept, the
last good file kept as `profiles.json.prev` (read instead of an unreadable file, which is kept as
`profiles.json.bad`); with neither readable the switch is unavailable (`profiles_unreadable`) and nothing is written.
At each build the library finds each named install's record: its own, a moved install's (the one record of that
server whose folder is gone while its parent exists; its files' records follow by size and date, or with scan consent
by size and last bytes), else one migrated from what the library already wrote there, so existing users keep their
mods through the first Off.

**Entries.** One game core of a variant in one install (a portrait, or one memorial lobby core: `_home`, `_home_gl`
and `_home_teen` are entries of their own), keyed by the variant key `Catalog.core_map` gives the core in its own
install. What its files carry comes from their non-official parts only (`tests/data/source_rule_cases.json`, shared
with the detail screen). Its state, first match wins: `damaged` (a header that cannot be read), `missing` (configured,
no files), `untracked` / `changed` (files the library did not write, without / with a configuration), `source_missing`
(a configured source gone from the library: listed once, then dropped), a take-over (a newer library write than the
configuration, written by another instance: configured with it), `updated` / `reset` (mods on, a configured entry a
game update renamed or a Steam check or repair put back, in whole or in part; `updated` also marks every entry
configured with a mod whose build `update` changed, whose game files still hold the old build), `ok`, else `pending`.

**The Enable Mods switch** (`toggle`). An Off adopts the mods it finds first (Add to Library: one mod per entry of its
non-official parts, every part recorded, the entry configured with it), keeps the built copies, then switches every
entry carrying a library source to official. An On switches every configured entry that does not carry its source
back to it, except an entry whose files changed outside the library: that one is skipped (`skipped`, `why` `changed`),
leaves the unfinished list and keeps its files. Mods Not in the Library offers it, and only Apply Mod Configuration,
given the entry as `changed`, writes over it (the outside change kept as a mod first). The intent and the entries to
reach are saved first, so a crash leaves them listed (the red dot and Retry, without a scan). Each entry is judged
again when it is reached, with its configuration read again: a part the
library can show unchanged by size and date (or the last bytes of a CRC-fixed file) carries its record's source, and
every other part is read in full there. An outside change is adopted by an Off and never overwritten by an On. What
the full read finds is kept in the records, so the cheap judgement agrees from then on: a file whose bytes are the
record's under a new date (a backup tool, `touch`) has its date stamped again, and a file the cheap rule takes for
official (no trailing bytes, the official size) whose bytes were changed outside the library is remembered as such
by its size and date (`outside`, dropped by the next write there). Without that, the next scan would list the first
in the untracked prompt again, and the second in the drift prompt at every start for Apply Mod Configuration to skip.
Refusals before anything is written: `folder_changed`, `state_changed` (another place switched first), the game
running, Steam busy, and `not_enough_space` (what an Off copies, with 512 MiB to spare); a running game, Steam busy or
changed folders stop a job midway, the rest stays listed. An Off leaves overwritten entries (no official copy
anywhere) as they are (`stuck`); the app names the existing Steam verify and Restore Resources repair instructions.

**Built copies.** Before an Off reverts an entry that carries its configured source, the library keeps the exact
bytes it wrote there, whole: a file of the mod in `mods/<id>/` when one holds them (every adopted part, every direct
install without a CRC fix), else a copy in `library/built/<sha1>.bundle`. On, Retry and Apply Mod Configuration write
them back with one copy per part (no installer, no UnityPy), byte for byte, while they fit: built for that source,
every part's name and every file name the build read (an adapted mod's official parts, another variant's files) still
in the install. A game update, a changed or dropped configuration, a mod's update or removal, a Forget and Mod Cache cleanup
drop them; a built file goes once no install's key references it. An update that changed the mod's build also sets the
`updated` marker on every entry configured with it (an earlier Keep as Is of its drift prompt is forgotten), so the drift
prompt offers Apply Mod Configuration; one that changed nothing sets none. An Off keeps no key for an entry with a
modded part that was not written from the mod's current files (the part's bytes are not a file of the mod and its
`build` is not the one a write from them signs now): a key naming only some parts would put official bytes over the
others, and the old build must not come back; the On builds the entry from the mod. Deletions, including Mod Cache cleanup,
run under `profiles.json.lock` against the configuration just saved. Bundle copies run outside that lock: after a
new key is saved and the lock released, any missing files are copied again from the still-modded game files. Later
deletions respect the saved key; recovery copies are checked again under the lock and deleted if no key references
them. Clear deletes `built/*.bundle` and `.library-` temporaries at least an hour old, counts both in the bytes freed,
leaves fresher temporaries alone, and keeps `built/` in place.
Built copies and kept originals hold game portraits and lobbies inside the library: restore copies of what the user's
own switch wrote, never read to draw the UI, and their disk use can grow large. Settings shows the Mod Cache size
and can clear it; kept originals remain available for Off. Sizes are measured on demand.

**What reads Steam's records.** The same consent covers Steam's records on this device: Detect (`settings.detectGame`,
`scan_off` without it; the page asks the consent first, in the plain style on the no-game screen), the game watch's
Steam poll, and Steam's file list of the installed build (`steam_records.py`: the app manifest's `InstalledDepots`
names the depot manifest kept in `depotcache`; its size and SHA-1 per file). With it, a bundle that list names is
official by its size and SHA-1 (`ModLibrary._steam_official`, `_is_official_as`) and the cheap rule takes its size from
there; without it, or for names the list lacks, the CRC rule stays. The write-time Steam check below needs no consent.
An Allow saved before the consent covered Steam's records (`scan_consent_scope` below 2) counts as unanswered and is
asked again; a refusal stands.

**What reads game files.** The scan consent (`scan_allowed`, asked at each use) covers every read the app makes on
its own of files the library did not write: the scan (the start and the end of each portrait and memorial lobby file;
headers and the last bytes of recorded files, never a whole file), an Off's own pass, the roster's Replaced mark for
mods outside the library, and a moved install's re-keying by last bytes. Without it `scan` refuses with `scan_off`, an
Off reverts what the library wrote or configured and adopts the changed files it meets on the way (other mods are not
checked), and the roster's Replaced mark shows only what the library wrote. Needing no consent: names and stats, the
library's own writes and folders, the files a switch or the user's own action reaches (read in full), and the
family the detail screen shows, judged cheaply (size, date, header; Needs Fix only from the record of the switch that
wrote the file, so it survives a restart). Nothing hashes a game file outside switching.

**Several instances and data directories.** Two instances sharing a library merge `state.json` on every save (each
one's records added, changed or removed, game file records written or dropped, favourites toggled) and read the
other's writes before judging. An alpha.3 instance still rewrites the whole file: its save replaces what an alpha.4
instance wrote since the alpha.3 one read it, and the records it writes itself lose the newer keys (they then count as
an older build's, which the library handles). It never opens `profiles.json`. A data directory removing an
install from its list (Remove from List, `forget`) drops its record only when no other data directory names it;
otherwise it only hides the install there, and a toggle there lists that install's entries as not switched for the
data directory that names it.

**State file safety.** `state.json` is read record by record. A record that cannot be used (not an object, a key
missing or of another type, a bad id) is skipped with a warning and held: its raw JSON is written back unchanged at
every save, so one bad record costs that record at most and never the file. An invalid `applied` entry is dropped. The
last good file is copied to `state.json.prev` before every write, the same way as `profiles.json`. An unreadable file
is read from `.prev` instead (logged) and kept as `state.json.bad` at the next write; `.prev` is never replaced by bad
bytes. A missing file with a `.prev` (a partial restore; nothing in the app deletes `state.json`, and its writes are
atomic) reads the `.prev` too, a running instance keeping its own records; with neither file the library is empty. A
running instance that holds state from a good read keeps its own state when the file breaks under it (the previous copy
is older), adds what `.prev` holds and replaces the file, keeping the old one as `.bad`. When neither the file nor
`.prev` can be read, or the file is a newer build's (`version` above 1, which is never read as corrupt, never touches
`.prev` or `.bad` and stays byte for byte), the library is blocked: reads answer an empty library, every write raises
`library_state_unreadable` before it starts, and nothing is written until the user fixes or removes the file (the next
call that finds the file changed reads it again). Nothing of `profiles.json` is written either: an empty library judges
every configured source gone, so the gate of the configuration (`Profiles.gate`) refuses with the same error, the reads
that settle something on the side (the scan's take-overs and dropped choices, a judgement's dates, the unfinished list)
skip it, and the kept originals are not pruned. `Services` takes no `seen_mods` baseline from such a library
(`state_unreadable()`), so its empty read cannot mark every mod as new later. A file that cannot be opened (locked,
scanned) is not corruption: reads answer an empty library, writes raise `library_io_error`, and the next call reads
again. A library move and a data directory move take `state.json.prev` and `.bad` along, as they do for
`profiles.json`. The replaces of `atomic_json`, `atomic_copy` and `atomic_clone` (`state.json`, the journals, game
files and kept originals) use `filelock.replace`, the same short retry as `webui.json` and `profiles.json`: on Windows a
`PermissionError` from another program's handle is tried again for up to a second, then raised unchanged; any other
error is raised at once, and no temporary file stays behind.

**Library move** (`Services.move_library`, `library/relocate.py`; `LIBRARY_ITEMS` lists what travels: the mod, original
and built folders, the three journal folders `transactions/`, `import-transactions/` and `asset-deletions/`,
`photographs/`, and `state.json` and `profiles.json` with their `.prev` and `.bad`). Each item is copied into a staging
folder inside the target (`.library-move-<token>`; files are flushed to disk, modification times kept), the copies are
compared with the source (names, kinds, sizes, link targets, and the bytes of files up to 1 MiB), and they are then
published under their final names. Only then is `library_dir` switched, and the source cleared item by item: each is
renamed aside in one step and the renamed folder deleted. Up to the switch nothing of the source changes: a failure
(`library_io_error`; a target inside an item is refused the same way) removes the copies, the staging folder and the
folders the move made, and the same call can be made again at once; a failed switch takes the published copies back. A
failure clearing the source never loses anything: the library is complete at the target and in use there, the old folder
keeps whole any item it could not rename, and the failure is logged. A staging folder left by a move that was cut short
is removed by the next move into that folder.

**Held records.** A valid record whose files are missing (a quarantined file, a partial restore) is held the same
way, with a warning, instead of being dropped at load: it is not among the library's records, so the engine and the
UI do not see it, and it is written back unchanged at every save. It returns to the records at the next load, refresh
of a changed file or save that finds its files, with its alias, assignment, import sources and configuration. At a
save the held records are the disk copy's, less the ids among the merged records and the ids this instance removed
since it last read the file (in its base, not in its records); a record with no usable id is always kept, so a
deletion or a rolled back import never comes back. A held id still counts as a source the library has: the
configuration naming it is not dropped as `source_missing`, and the game files carrying it are not offered for
adoption. The one exception is a game file about to be replaced: its bytes are kept as a mod then, since the held
record has no file to show them. Every removal path (`remove`, `_drop_mods`, an import rollback, an asset deletion)
saves before it deletes the folder, and an asset deletion's rollback leaves alone a record that returned during it. A
held photograph recipe (a record with `roster_photography` choices) still owns its photos: they count as used for
`photography.removalInfo` and the deletion confirmation, and deleting one is refused (`photo_in_use`) until the record's
files are back.

**Per-Mod import publication.** `_store` stages and verifies the complete file group under
`import-transactions/<uuid>/files`, journals its expected hashes, publishes the directory under `mods/<uuid>`, then
saves the corresponding `state.json` record. Metadata publication is the commit point. A failed operation rolls
back its record, any repair link and its files without removing earlier independent imports. Recovery runs before
library operations, validates journal and Mod paths, verifies a committed group's bytes and retains a pending
journal if recovery cannot complete. Metadata and directory synchronization must succeed before recovery discards
its journal, even when a retry finds that an earlier metadata replacement already took effect.

**Per-Mod update.** `update` rebuilds into a temporary folder, then publishes under the mod writer
(`_publish_update`): it refuses with `entry_changed` when the mod's files changed since the rebuild read them, journals
the old and new files with their hashes in `import-transactions/<mod id>/journal.json` (`operation` `update`), copies
the old files to `mods/<id>/previous/<uuid>/` and verifies them, then replaces the files. The record's new file list in
`state.json` is the commit point; the old files the rebuild does not name, older `previous/` copies (only the newest
is kept) and the old build's built copies go after it. A failure, or an interruption found by recovery, before the
commit leaves the old mod whole: its files come back from the previous copy (verified), new files it does not name go,
and its file list is the old one again; its id, alias, assignment, configuration and repair links never change.
Recovery finishes an update whose record names the new files that all hold their new bytes, and rolls back any other.

**Pending journals.** A write, an import or an asset deletion that did not finish leaves its journal (`transactions/`,
`import-transactions/`, `asset-deletions/`), and every write recovers first. `_transaction_lock` always attempts
the recovery and raises the real failure of a journal it cannot settle (`library_recovery_pending`,
`original_unavailable`, `library_io_error` on an invalid journal, `game_running`, `steam_busy`, `classic_open` and the
other refusals of a write), so a write never goes on over a journal. Reads never fail on a pending journal: a read
attempts recovery, logs a failure once, remembers it with the pending journal paths and the time, and goes on. It
tries again only when the set of journals changed or `RECOVERY_RETRY` (60 s) has passed, so a closed game or a
repaired file is picked up and reads stay fast; a retry that finds other journals still pending but raises nothing
follows the same limit. While a failure is remembered every change of `state.json` and `profiles.json` is refused with
that failure (the central gates are `_save` and `Profiles.update`; they attempt recovery again first, so the refusal is
the current one), which covers the favourites, aliases and configured choices that do not take the transaction lock;
a successful recovery clears it. A read that settles something on the side (the scan's take-overs, a judgement's
dates, the legacy migration, the unfinished list) skips it instead of failing. Recovery rules:

- A game file whose bytes are neither the journal's before nor its after was changed outside the library (a Steam
  repair, another tool), and so is one that is gone. Its bytes are left as they are, a warning is logged and the
  other files are restored; the rollback finishes and retires the journal. Its record goes back as it was, without a
  new modification time (a stamp from the file would let the stat check read the outside bytes as the recorded ones),
  its kept original stays (the only official copy of a file left as it is) and so do the mods kept from it. An asset
  deletion's rollback does the same and does not recreate a gone file.
- A game file that is missing while a later-dated file of its part is in its folder (a game update renamed it, as
  `_View.successor` judges) is not restored: that would write the old name beside the new one. The rollback skips it,
  drops its record in `applied` (only the journal's own key) and keeps its original and the mods kept from it. A
  missing file with no such successor is restored as before.
- A write journal whose install has the same key but other resource folders proceeds if every target still passes
  `check_resource_path`; otherwise it stays `folder_changed`. One whose install is not known now (its folder moved, its
  key differs) waits: it is logged, kept with its files and skipped, writes to other installs go on (a rollback
  restores only its own keys, and the originals' prune refuses while a journal exists), and the next recovery after the
  install is back rolls it back. An asset deletion cannot wait, since its rollback restores a snapshot of all records
  and applied entries: it stays a remembered failure, reads work and writes refuse.

This is stage-local atomicity. The existing game-write transaction separately retains and restores every replaced
part. A complete library import is not undone merely because later optional application fails; its configured
choice can remain pending. With the master switch off, a valid application records desired state and defers the
game-file write. Fixture fault injection covers transfer, verification, metadata, interruption and rollback
boundaries; it does not establish power-loss behavior for every filesystem or storage device.

The library also supports `model` Mods and the detail page's model source selector. Their prefab, mesh, texture and
animation dependencies are validated together. Model preview uses the chosen source independently of game
application; a library-only assigned model exposes its tab without claiming a local installation target.

A model slot contains `variant.model` (its prefab core, first) and every other installed model-kind core that
`Catalog.model_map` maps to that same variant. Unmapped shared NPC/enemy cores and other variants' cores stay separate.
`ModLibrary._core_map` keeps the choice keyed by the prefab core; dependency cores do not get independent configuration
or state entries. The slot's files, file table, Needs Fix, restore availability, selection verification and incomplete
judgement cover all its cores. Hoshino (Armed)'s slot is `ch0258_02` plus `ch0258`.

A model Mod fits when every file's core belongs to the slot. Apply uses the existing replacement mapping across all
slot files, keeps originals, and journals the whole transaction. Omitted parts prefer official copies, then current
files when no official copy remains; switching choices returns the previous choice's omitted parts to their official
copies where available. Enable Mods, drift repair, adoption, removal and restore operate on the same single choice.
Switch 3D Model offers Mods from any of the slot's cores. Filenames and the library identity retain the physical core.
A source-change event for any slot core refreshes the detail page and its model preview.

Switch 3D Model also offers the student's other models as costumes (`variant:<key>`), field-mode models included
(`gameassets/model_adapt.py`). The slot keeps its skeleton, clips, weapon, halo and props; the source's costume
renderers (their meshes with bindposes rebound onto the slot's skeleton, their materials and textures) are added as
new objects to the slot's own bundles, and the slot's prefabs draw them: the primary part in the slot's primary renderer,
further parts in renderers added beside it, the slot's other costume parts and alternates with no mesh. Existing
objects never change in place, since field, cutscene and other variants' bundles reference them. Roles, bone mapping
and checks use data only (shading, rig, visibility, the slot's clips), never part names: Biped bones by name, other
bones by structure, and each secondary group follows the slot's bones only while it stretches no more than the slot's
own costume under the slot's clips (else it stays rigid). The whole costume may stretch under the slot's clips up to
1.1 times `max(1.5 x own, own + 1)` (`model_adapt.stretch_limit`; owner, 2026-11-08). A pair whose checks fail is refused with `source_unsupported`
(`source` names the failed check, further params its detail). What the game loads with the slot's prefab counts as
available: the slot's files, every CAB the prefab's files reference (followed through the files they reference: the
catalog's dependencies), and the always-loaded prolog bundles. A shader outside them is carried with the costume: a copy
goes into the materials' file (its serialized type copied from the shader's file, both of the same build), its
dependencies on the player's own resources (`Library/unity default resources`) kept as external entries, other
dependencies carried the same way; a shader with fixed textures is refused. A slot whose primary mesh, materials or textures live in
another core's files (Izumi (Swimsuit) on Izumi's) gets the new objects in one of its own files loaded with the prefab
that holds that type; another core's files are never written.
Apply builds through the library's build cache like any adaptation; a preview builds once per build signature into
`built/previews/<signature>` (the newest four kept; Clear Built Mods clears them).

A costume build holds the model lock (`gameassets.service.model_lock`, taken after the library's lock) and closes
every bundle it opened when it ends: like an export it keeps the path lock of each bundle it reads until then, so two
at a time could wait on each other (0.7.0-alpha.22 hung there).

A pair past the stretch limit is built for a preview only (`adapt(strict=False)`, `plan.unfit`): its verdict is
"preview" (kept with the build in `parts.json`), its card is listed with `previewOnly`, it can be shown and saved as the
3D preview, and its Apply (the details panel's and the screen's) is greyed with `model.unfit` on hover; `apply` refuses
a pair whose verdict is not ok (`source_unsupported`) before saving any choice, mods on or off.

A pair can be applied only once it was checked to its end (BL-122): its preview built and exported
(`Services.costume_check`). The verdict is kept in the library's `costume-verdicts.json`, keyed by the build signature
(the costume rule's version `gameassets/costume_rule.COSTUME_RULE` in place of the program's, so a new program build
keeps them; the official bytes of both models, the save options; the newest 4000 kept; BL-134): the rule's refusals,
program errors and export failures are kept, so the pair is not offered again for these files; a refusal of the moment
(`original_unavailable`, a locked file) is not kept. The preview builds in `built/previews/<signature>` share the key.
`COSTUME_RULE` is bumped with any change to what the build writes, refuses or measures; `tests/test_costume_rule.py`
pins the rule's sources to it. `model.costumes` reads the verdicts; `model.checkCostumes` checks the others in the
background when the 3D tab shows or the switch opens, one pair per job. The switch opens at once with every costume
of the student (BL-136); a card whose pair is not "ok" (not checked yet, past the stretch limit or refused) is listed
for a preview only, Apply greyed with `model.unfit` on hover, and turns applicable as its check ends (the open switch
re-renders when the saved verdicts arrive and after each check, keeping its scroll). A refused pair is no preview
source (`family.get` drops it; the switch does not save it) and its `/asset/model` route answers 404 without building
it again.

Another student's model (BL-131) is a deliberate choice, built past the stretch limit as well and with its halo
(`cross`); its "preview" verdict is applied like "ok". The switch's search finds those models (at most 64, the keys one
`model.costumes` call takes), and the checkbox "Show other students" (`model.showOthers`) in the toolbar's left end lists
all of them after the student's own cards instead: in the roster's name order (favourites first), the segment tabs, the
search, the sort and the order applying to them as to the own cards, no cap. It is off at every App start and kept only
in memory (switch-sources.js `showOthers`), across students and popups. The toolbar with the checkbox is the roster's
(`roster-widgets.js` `toolbarFit`: the checkbox first, the sort button as wide as its longest key, the segments and type
sizes stepping down until everything stands after the list's left edge); the portrait and lobby switches and every other
list popup keep their toolbar as it was. A search's hits are judged at once; the models of the checkbox only as they come
into view (the list's `tools.onView`, the signal that brings the thumbnails in): `model.costumes` in calls of at most 64
keys, then `model.checkCostumes` one pair at a time, the cards scrolled out of view dropped from the queue. Every such
card shows `model.crossCharacter` on hover and is drawn like any other card.

Optimize Physics (BL-132) is an action of the switch's details panel on the official model and on another variant's
costume (not on 3D mods). The switch itself builds a costume with Rule A only (`adapt(physics=False)`); the action
builds it with the physics rules as well (`physics=True`: Rule B, the skirt chains as new bones of the slot) and keeps
the result as a model mod of the variant (`optimized_from: "variant:<key>"`, named `model.optimizedName` after the
costume, tagged `model.optimized` on its card). It answers "No optimization needed" (`model.optimizeUnneeded`) for the
official model, whose own clips move its skirt, and when the build's measured choice is not Rule B (no rigid skirt
chains, or Rule B past the stretch limit). A costume with a copy offers `model.optimizedGo` instead, which selects the
copy; the copy offers no action. The button's hover hint (`model.optimizeHint`) names what it covers.

A variant whose only model is a field-mode player prefab (`Field_Player_<dev>[_NonWeapon]` in the
`field-character-_mxload-*` bundles: event and story costumes such as Hina (Pajamas) and Niko (FOX EATS)) gets
`variant.field_model` (`fieldModel` in `family.get`): its 3D tab shows it with the clips its `FieldAnimancerController`
lists, and it is offered as a costume; it has no slot, so it switches nothing itself. Two more sources fill the same
field for a variant with neither a battle nor a field player model:

- a field NPC prefab named by the character's id in the same bundles (`Field_NPC_<id>[_...]`, e.g. Yukino's
  `Field_NPC_CH0171_NonWeapon`; of several, the one with a halo, then the first by name: NP0143's `_AR_Weapon`);
- a model in the character's own dependency bundles (`assets-_mx-npcs|characters-<dev>-_mxdependency-*`) for one the
  game shows only in a story cutscene or a UI scene (Ayame (Corrupted) and Ayame (Twilight): `NP0206_Mesh`,
  `NP0229_Mesh`; Arona: `Arona_Original_Gacha`): the root of a container prefab, else of an imported model, with
  renderers below it (`model.asset_root`). It plays the idle among those bundles' clips that moves it (Arona's), else
  its own clips of the story cutscenes (BL-127): the clips named after it (`<dev>_...`, e.g. `NP0206_Cutin_A_01`)
  in the cutscenes' dependency bundles (`assets-_mx-prodresources-sccutscene_*`, indexed once by clip name,
  `_story_clips`), at most 12 (`STORY_CLIPS`), as slots `Story_1`, `Story_2`, ... in the order of their names; their
  root (`bone_root`, `Bip001`) moves about where the model rests on the ground, its height kept, so the view keeps it
  framed. The 3D tab lists them as actions `story1`, `story2`, ... (`model.action.story`: 剧情 1, Story 1), each
  played once and held on its last frame; with no idle the first plays on opening, so the model never shows its bind
  pose. Without any clip it rests in its stored pose. The catalog asks for these (`asset_model_cores`) only for the variants left without a
  model, once per name. An unskinned one (Kei (Decagrammaton Body), a static mesh) is no costume: the rule refuses it
  as `static model`.

The GLB's root extras name the prefab (`prefab`) and, for these two, the source (`source`: `npc` or `asset`). The 3D
tab's left side draws a field or NPC model as it draws a slot's (`previewAvailable` counts `fieldModel`).

## Game watch

### Checks before a write

`library/safety.py` holds the checks every game file write makes first (`writable_game_files`). None of them reads a
game file: the process list and Steam's app manifest are not game files, so none needs the scan consent (owner,
2026-11-07: the write-time Steam check stays outside it; reading one state value per write costs about 12 µs). The
watch's own Steam poll runs only with the consent (`steam_allowed`).

- **Game running.** `check_game_not_running()` refuses with `game_running`, or `game_status_unavailable` when the
  process list cannot be read. `game_processes()` lists the game's processes, never this process or its parent:
  - Windows: the exe names of a Toolhelp snapshot (ctypes, no subprocess). `tasklist` is the fallback, run with
    `CREATE_NO_WINDOW` so the windowed build never flashes a console window.
  - macOS and Linux: `ps -A -ww -o pid=,comm=`, matched when the executable's file name is `BlueArchive` or
    `Blue Archive` (`.exe` allowed), so a native build's `/Applications/Blue Archive.app/Contents/MacOS/Blue Archive`
    matches with its spaces. Only without such a match, `ps -A -ww -o pid=,args=` for Wine and CrossOver, where the
    process is the preloader and `BlueArchive.exe` or `Blue Archive.exe` is in its arguments as a path's last part.
    `BlueArchiveTool.exe` and this program's own names never match.
  - The JP server's launcher, which may update or repair files on its own, is not matched yet: its process name is
    still to be found.
- **Steam busy.** `check_steam_idle(game_folder)` refuses with `steam_busy` while the app manifest's `StateFlags` has
  any of the bits 256 (update running), 1024 (update started), 65536 (reconfiguring), 131072 (validating), 262144
  (adding files), 524288 (preallocating), 1048576 (downloading), 2097152 (staging), 4194304 (committing) or 8388608
  (update stopping). `game_paths.steam_app_manifest()` finds the manifest by walking up from the folder, the install
  root or a resource folder below it, to an ancestor `common` whose parent is `steamapps`, along the path as given and
  then resolved, and takes `appmanifest_3557620.acf` there. No manifest (a folder outside a Steam library, the JP
  server, a dev sandbox) means no check. The app running bit (64) is not used.
- **The call.** `writable_game_files(paths, resource_dirs, *, game_folder=None)`: with `game_folder` (the install the
  files belong to), Steam is checked for it after the game.

### The poller

`webui/gamewatch.py` greys the Enable Mods switch while a write would be refused. It polls every 3 s while
`active()` says a page is connected to `/api/events` or the tray icon is shown, else every 15 s, and at once on
`poke()` (the tray menu opening). Each poll checks the game once, Steam for every known install, and the classic
window `classic.json` names. The write-time checks stay the authority; the poll only greys controls.

```python
GameWatch(installs, find_classic, profiles_seen, on_change, publish, request_scan, *, active, own_classic,
          on_classic_closed, check_game, check_steam).start()   # a daemon thread; stop() ends it
watch.state  # WatchState(game, steam_busy, classic) with .reason: game_running, game_status_unavailable,
             # steam_busy (any known install), classic_open, or None, in that order
```

| Callable | Given by | What for |
|---|---|---|
| `installs()` | the services | the known installs' folders, read at each poll |
| `find_classic()` | the classic hand-off (`webui/classic.py`) | the classic window `classic.json` names, or `None`; it owns the file, the identity check and the removal of a stale file |
| `own_classic()` | the services | the pid of the classic window this instance started, which its own watcher thread reports, so the poll ignores it |
| `profiles_seen()` | the services | whether another data directory changed `profiles.json`'s shared intent since the last poll |
| `on_change(state)` | the services | the first poll, every change of the answer and every `profiles_seen()` yes: updates the `mods.state` snapshot and publishes `mods.state` |
| `publish(event, data)` | the event bus | `classic.closed {code: null}` when a watched classic window ends, after `on_change`; then `on_classic_closed()` |
| `request_scan()` | the scanner | the game stopping (from `game_running`), an install's Steam going idle, a watched classic window ending; the scanner runs it only with scan consent |
| `on_steam_idle()` | the services | an install's Steam going idle, before `request_scan()`: when a built game's resource version changed (a game update), the game, catalog and library are rebuilt through the settings-change path (`reload_game`), at once or, while a job runs, at the next idle; an unchanged version does nothing |

A failing callable is logged and never stops the thread: an unreadable process list is `game_status_unavailable`,
a failed Steam check is idle, and a failed classic lookup keeps the last answer.

## Jobs and events

`Services.submit(kind, fn, writes=None, library=None, **params) -> job_id` runs `fn(log=..., progress=..., **params)`
on one worker thread (`JobRunner`). A **writer job** (`writes="game"`: it writes the game folders and the library;
`writes="library"`: the library only) holds back the window's close button, `app.quit`, a library move and the
services rebuild after a game folder change; a read-only job (an extract, an update download) holds back nothing.
Every writer job ends on the worker with the library's judging of the `unfinished` list (`judge_unfinished`), a new
`mods.state` snapshot, `library.changed {cores, kinds}` and a scan (with the scan consent).

| Writes | Jobs |
|---|---|
| game | `apply`, `restore`, `mods.toggle`, `mods.restore` (Apply Mod Configuration) |
| library | `library.adopt`, `library.prune`, `library.clearBuilt`, `library.forget`, `library.judge`, `update` |
| none | `extract`, `export`, `update.download` |

Every game folder write a job makes is pinned at submit: the RPC captures the library and the pins (`installs()` for
the jobs over every known install, `current_pin()` for one entry) and passes them to the library, so a game folder
changed meanwhile (in Settings, or the classic GUI's `config.toml`) gives `folder_changed` instead of writing another
install. Shutdown drops the queued read-only jobs and waits only while a writer job is queued or running.

Besides the front end's jobs, the web UI submits its own: `library.prune` once per library it builds (see library),
whose last step judges the `unfinished` list, and `library.judge` when another data directory sharing the library
changed it (below).

Events on `/api/events`:

| Event | Data |
|---|---|
| `job.started` | `{id, kind}` |
| `job.log` | `{id, lines: [str]}` (batched) |
| `job.progress` | `{id, sequence, current, total, label}` (throttled); batch jobs also report `done`, `completed`, `remaining`, `index`, `phase`, and `step` |
| `job.done` | `{id, result}` |
| `job.failed` | `{id, code, params}` |
| `drop` | `{paths: [str]}` |
| `mods.state` | as the `mods.state` method, on every change: a toggle's start, progress (at most every 0.25 s) and end, the end of every writer job, the game watch's answer, the classic window opened, busy and closed, the classic queue, the scan consent, the switch's settings, a game folder change, another data directory's change to `profiles.json` |
| `library.scanned` | `{counts: {install key: {state: n, configured: n}}}` after a scan (`stuck`: a `pending` entry with no official copy) |
| `library.changed` | `{cores, kinds}` after every writer job (empty lists for a job that switched nothing the page shows); without data (read everything again) after a failed writer job, a library move, a classic window closing, and the mod list's own changes |
| `classic.opened` / `classic.ready` / `classic.closed` | `{pid}` / `{pid, tab}` / `{code}` (see Classic window); `classic.closed {code: null}` for a window an earlier instance left |
| `classic.queueDone` | `{submitted: [{method, params, entries, job}], dropped: [{method, params, entries, error}]}` once the writes queued for the classic window ran |
| `tray.toggle` / `tray.open` | `{job, enabled, expect}` / `{view, ...}`: the tray submitted a toggle, or brought the window up on a view (see Tray) |
| `config.changed`, `app.busy` | `{}` |

## Mods state, scan and the classic queue

The services hold what the Enable Mods switch, the prompts and the classic hand-off read (`webui/services.py`):

- **The snapshot** (`ModsSnapshot`). `mods.state` answers from it, never through the library lock: the library's
  `status()` (the known installs, the current one, the shared intent and the configured count; names and
  `profiles.json` only, without walking storage folders), the last scan's entries and a running toggle's
  progress. The jobs, the scanner and the game watch update it; its lock is a leaf lock. Install labels are made in
  the UI language from `settings.server.*` and, for two installs of one server, `mods.installNamed` with the
  folder that tells them apart. `reason`, first match: `game_not_found` (no known install at all),
  `profiles_unreadable`, the game watch's answer (`game_running`, `game_status_unavailable`, `steam_busy`, a classic
  window an earlier instance left), this instance's classic window (`classic_open`). With the scan consent, `pending`
  and `stuck` count the last scan's entries; without it, they and `left` count the `unfinished` list by `why`.
- **The library** is built without a current install too (ADB mode, the selected folder missing), from the library
  folder, the catalog and the settings, with `scan_allowed` (the pref `scan_consent`, read at each use, so a change
  needs no rebuild), `data_id` and `write_check`, which refuses every game file write with `classic_open` while a
  classic window is open. That covers a restore while mods are off that the cheap judgement took for writing nothing
  but whose full judgement finds a file changed (the classic GUI writes in place): the write itself is refused.
- **The data directory's id.** `UiState.data_id(data_dir)` keeps `data_id` (12 hex digits) with `data_path` (the
  data directory, resolved, case-folded on Windows) in `webui.json`, made when the services first build the library.
  A recorded path that is another folder means a copy when that folder still exists with the same id (a new id), and
  a move otherwise (the id kept, the new path recorded).
- **The scanner** (`webui/scanner.py`) runs the scan on a daemon thread of its own, never on the job worker, and
  only with the scan consent: one scan at a time, requests made meanwhile served by one scan after it, `cancel()` on a
  game folder change or a library move (the result is dropped). A stale result (the library wrote while the files
  were read) is scanned again once while no writer job runs, else dropped: the scan after that job replaces it. The
  start scan waits for the `library.prune` job. Startup prune and scan begin after the first screen reveals,
  while Check for Configuration Drift and Untracked Mods at Start is On. Off withdraws consent, cancels outstanding
  scans, releases the classic queue and empties the entries. With consent, scans also run after a game folder change,
  after every writer job, when the game stops or Steam turns idle (the game watch), after a classic window closed,
  when consent is granted, on Settings' Check (`mods.scan {dismissed: true}`, which lists dismissed entries again)
  and whenever the page asks (`mods.scan`, the window back in front after 30 s).
- **Another data directory** sharing the library: at each poll the game watch asks `profiles_seen()`, which reads
  `profiles.json` again only when its size or date changed (under its file lock, never the library lock). When the
  shared intent or the `unfinished` counts changed, the snapshot follows, `mods.state` is published, an automatic
  scan is requested with scan consent and `library.judge` judges the `unfinished` list, so the red dot and Retry follow with or without scan
  consent.
- **The classic queue.** While a classic window is open, `actions.apply`, `actions.restore` (when it would write),
  `mods.toggle` and `mods.restoreDrift` are refused at submit with `classic_open`; the toggle and Apply Mod
  Configuration check again when their job starts, before anything is saved. The classic modal queues the refused
  call (`classic.queue`) with its pins and the size and date of its entries' files. Once the classic window closed (its
  watcher thread, or the game watch for one an earlier instance left), the page is told to read everything again,
  a scan runs (with scan consent), then `ClassicQueue.run` submits each call that still holds as its normal job and
  `classic.queueDone` lists the dropped ones. The queue waits for the scan it asked for by its number; when the
  consent is withdrawn meanwhile, that scan is dropped with the others and the queue runs at once.

## One instance per data directory

`main()` takes an OS lock on `<data dir>/instance.lock` for the process lifetime (`webui/instance.py`; `flock` or
`msvcrt.locking`, released by the system however the process ends) and names itself in `<data dir>/instance.json`
(`{pid, port, raise_token}`). A second start finds the lock held, posts `/api/instance/raise` with the raise token
(header `X-BAMT-Raise`, never the session token) and exits: the first instance shows and raises its window, or in
browser mode opens its page again. With `--serve` a second start refuses with a message. A classic window outlives
the instance that started it and is found again through `classic.json`.

**Quitting.** `app.quit` (the roster's quit prompt) is refused with `busy` while a writer job is queued or running,
the same rule as the window's close button; otherwise it closes the window, or in browser mode answers first and then
stops the server (the page shows Closed); with `--serve` it gives `native_unavailable`.

## RPC

`POST /api/rpc` with `{"method": "...", "params": {...}}`, answered with `{"result": ...}` or
`{"error": {"code", "params"}}`.

| Method | Result |
|---|---|
| `app.info` | version, language, platform, game found (the selected folder is a game install, as for `settings.set`; a folder saved earlier that holds no game starts on the no-game screen), resource version, consent (`null`, `true`, `false`), `spineConsent` (`null` until acknowledged; earlier Yes or No answers are true at startup), `uiCached` (consented light assets on disk for the current resource version), paths, `dataDirWritable` (a real temporary file was written in the data folder and removed again at start; `false`: see Start sequence), `debug` (started with `--debug`) |
| `app.consent {granted}` | records the choice; `false` closes the app |
| `app.spineConsent {granted?}` | reads the saved answer; `{granted: true}` records the notice acknowledgement and returns true, without a notice version |
| `app.quit` | closes the window; `busy` while a writer job runs; in browser mode answers, then stops the server; `native_unavailable` with `--serve` |
| `app.openClassic {tab}` | `{started}`: starts the classic window on a tab (`settings`: its settings) and waits for it (30 s at most), or brings the open one to the front, with the tab unless a classic task runs; a window an earlier instance left is only raised; `classic_failed {exit_code}` when the child exits or stays silent first; `busy` instead of a new window while a job writing the game folder is queued or running |
| `app.classic` | `{open, ready, busy, pid, orphan, queued}` (`orphan`: found through `classic.json`) |
| `classic.front` | `{raised}`: the classic modal's Bring to Front (never starts a window) |
| `classic.queue {method, params}` | `{queued}`: keeps one of `actions.apply`, `actions.restore`, `mods.toggle`, `mods.restoreDrift` for when the classic window closes |
| `classic.clearQueue` | `{}` |
| `i18n.strings {lang}` | `{key: text}` for `webui.*` |
| `roster.list` | families with variants, mod counts, fresh flags, favourites, recent order, `added` (when the newest own mod was added), `aliases` per family and per variant (matched by the search, never shown), `custom` per family (the user's own aliases), `configured` manifest sources and independent `previewSources`; reads one library snapshot, without physical `selection` verification (available through `family.get`); without scan consent Replaced comes from the library's records only |
| `settings.get` / `settings.set {key, value}` | the settings popup's values; key `roster` keeps the student list's view (merge, segment filters, sort, order, filter groups; `webui/state.py roster_view`); `lobbyZoom` is `{mode, custom}` in `settings.get`, set by keys `lobbyZoom` (`game`, `art`, `unlocked`, `custom`) and `lobbyZoomCustom` (1.0 to 4.0 game views across, kept in 0.1 steps); `lobbyPost` and `lobbyIntro` (the lobby's post-processing and its intro, on by default); the booleans `tray` (the tray or menu bar icon) and `classicPrompt` (the hand-off prompt); `settings.get` adds `traySupported`; the game folder keys `gameDir.global` and `gameDir.japan` are refused with `game_not_found` unless the folder is a game install (`searching.is_game_install`: one of its search directories holds a UI atlas bundle, `uis-atlas-*.bundle`, decided by file names only, so the resource folder itself stays valid), and `fileSource` with `game_directory_missing` for such a folder |
| `family.get {key}` | a family with its variants (and `aliases` and `custom`, as in `roster.list`), mods and each variant's state, judged cheaply (`copy`, `restorable` and `official` per file; Needs Fix only from the record of the switch that wrote the file); `configured {spr, lobby, model}` per variant (one source per slot); `modelCores` (prefab core first, then its own dependency cores), `modelFiles` (every model slot file, newest first, with the state's CRC and restore judgements) and `version` (the game's resource version, so a switched install reloads the screen); `modList` by the variant the current catalog gives each mod's core |
| `lobby.warm` | builds the memorial lobby prefab index in the background once per game; dependency CABs resolve on demand instead of triggering an eager full-directory index |
| `lobby.lines {variant, talks, lang}` | `{clip: {lang: text, voice}}`: zh-CN from Kivo, ja from Kivo else the Global server, en, zh-TW, ko and th from the Global server (read only for a `lang` other than zh-CN, left out when its table is not in within 3 s); `talks` (`{animation: [clip ids]}` from the skeleton) places the Global rows on the clips; `lines_unavailable` when neither source can be read |
| `debug.trace {events}` | the fullscreen lobby's input events, logged (logger `bmpt.webui.trace`, and `input-trace.log` in the data directory) only when the app was started with `--debug`; ignored otherwise |
| `preview.failed {url, step, error}` | bounded browser preview diagnostics in the session log, including release builds |
| `favorites.set {variant, on}` | |
| `aliases.set {family, aliases}` | the user's own aliases of a student as kept (`webui/state.py`: NFC, no control or format characters, whitespace runs one space, empty and repeated ones, ignoring case and width, dropped); `alias_too_long` past 24 characters, `alias_too_many` past 8, and nothing is kept then; an empty list forgets them |
| `mods.add {paths, background = false}` / `mods.pick {background = false}` | `{added, duplicates, installed}`; with `background`, a library writer job id (`mods.pick` opens the native file dialog); identity is core plus SHA-1, independent of filenames; grouping as in "Import grouping" |
| `mods.removalInfo {id}` | `{name, used}` for the deletion confirmation, including configured and applied entries in every install |
| `mods.remove {id, confirm = false}` | library writer job id; result `{done, failed, dropped, cores, kinds}`; deletes the source, stored files and references, then restores game entries to Official or their saved source; refused writes keep the new configuration and an unfinished entry |
| `mods.rename {id, label}` | |
| `mods.seen {id}` or `{ids}` | stores seen mod IDs in `webui.json`, without writing the library; roster and family fresh flags come from this state |
| `ui.preview {variant, kind, source}` | stores a preview choice in `webui.json`, scoped to the install and variant; returns `previewSources`; never changes the application manifest or game files |
| `model.costumes {variant, others}` | the verdicts of other variants' costumes on a variant's 3D model: `{other: "ok" \| "preview" \| "refused" \| null}` ("preview": past the stretch limit, shown, never applied; null: not checked yet); a pair outside the family, the variant itself or one with no model is "refused"; at most 64 `others` |
| `model.checkCostumes {variant, others}` | job id (`costume-check`, not a writer): checks each pair to its end (`Services.costume_check`) and saves its verdict; result `{other: "ok" \| "preview" \| "refused"}`, a refusal of the moment "refused" and not saved |
| `model.optimize {variant, source, name}` | job id (`model.optimize`, a library writer): Optimize Physics (BL-132) on `source`, `"official"` or `"variant:<key>"`; result `{mod}` (the copy, a model mod of the variant with `optimized_from`; an existing copy is returned) or `{unneeded: true}`; `name` (at most 200 characters) is the source as the screen shows it, the copy's name; a refused or preview-only pair is `source_unsupported` |
| `mods.state` | `{installs: [{id, key, server, folder, label, distinct, named}], current, enabled, available, reason, reasonParams, scan, explained, configured, pending, stuck, left, unfinished: {pending, left, stuck}, drift, untracked, damaged, sourceMissing, queued, scanned, tray, traySupported, busy, progress}` from the snapshot (above); `id` is the first 12 hex digits of the install key's SHA-1 (used in URLs), `named` tells a folder the settings name from a recorded one; `busy` while a toggle is queued or running or writes are queued for the classic window; `progress` is `{done, total}` of a running toggle |
| `mods.sizes {kind}` | `{builtSize}` for `kind: "built"` or `{originalsSize}` for `kind: "originals"`, bytes measured on demand; Settings requests only the Mod Cache size; no size walks in `mods.state` |
| `mods.toggle {enabled, expect}` | job id (`mods.toggle`): the switch over every known install, also Retry; `busy` while another toggle is queued or running, `classic_open` at submit; the job's refusals `state_changed`, `folder_changed`, `game_running`, `game_status_unavailable`, `steam_busy`, `classic_open`, `not_enough_space` |
| `mods.consent {granted}` | `{}`; saves `scan_consent`; granting enables startup checks and requests a scan immediately; withdrawing stops the scans and empties the entries |
| `mods.explained` | `{}`; sets `mods_explained` (the first toggle's explanation was shown) |
| `mods.scan {dismissed = false}` | `{requested: bool}`; the result comes as `library.scanned`; `scan_off` without scan consent; `dismissed: true` is Settings' explicit Check |
| `mods.entries` | the last scan's entries (`{install, core, variant, names, kind, source, configured, state, overwritten, fixed, size, files, dismissed}`), updated by every writer job's result until the next scan; empty without scan consent |
| `mods.adopt {entries}` | job id (`library.adopt`): Add to Library for `[{install, core}]`, allowed while the game runs and while a classic window is open; `not_enough_space` |
| `mods.restoreDrift {entries}` | job id (`mods.restore`): Apply Mod Configuration; `classic_open` at submit |
| `mods.dismiss {kind, entries}` | `{}`; `kind` `drift` (Keep as Is) or `untracked` (Don't Ask Again) |
| `mods.clearBuilt` | job id (`library.clearBuilt`); its result `{freed}` |
| `mods.forget {install}` | job id (`library.forget`): Remove from List; `bad_params` for an install the settings name |
| `actions.apply {variant, source, kind}` / `actions.restore {variant, kind}` | job id; switches what the current install shows, `kind` `spr` (default) or `lobby` (see library); the result has `deferred` while mods are off (configured, not written); `classic_open` at submit only when a game file would be written, `folder_changed` when the current install changed before the job ran, `steam_busy` from the write, `game_not_found` without a current install; a refusal of the moment (`game_running`, `game_status_unavailable`, `steam_busy`, `classic_open`, `folder_changed`) is checked before the choice is saved or anything built, and one that still comes at the write puts the earlier choice and its built copy back, so the choice stays as it was with no error on it (BL-123); a failed build keeps the new choice with its error |
| `actions.update {mod}` / `actions.extract {target}` | job id |
| `export.options` | actual default output folder, current target server/platform and configured target choices; serialized platform metadata is preferred |
| `export.start {scope, folder, server, platform, target?, kind?}` | job id; `scope` is `single`, `library` or `applied`; result `{folder, archive, count}` after complete ZIP verification and publication |
| `import.catalog` | offline assignment families, including students without local resources |
| `import.assignCharacter {id, variant}` | job id; confirms ownership for the analyzed entry and returns its updated verdict |
| `hall.get` / `hall.update {value, revision}` | persistent Hall state; updates validate the complete snapshot and reject stale revisions with `hall_conflict` |
| `hall.options` | available official/Mod portrait and memorial sources, plus installed day/night office-layout choices and collection-background URLs |
| `hall.dialogueLayout` | the ordinary `LobbyCommon/DialogPanel/ChatDialog/Talk` NGUI speech balloon |
| `hall.lines {variant, lang}` / `hall.voice {variant, clip}` | localized portrait dialogue metadata, including pending availability / memory-only voice audio |
| `hall.viewed {variant, source}` | records a validated memorial source in bounded recently viewed history |
| `actions.compare {mod}` | per-asset differences |
| `actions.remap {mod}` | writer job; creates a new mod against current official bundles, preserving the source; result `{mod, report, compare}`; `report.upscaled` lists `{atlas, page, w, h}` for pages enlarged before remapping |
| `os.reveal {path}` | |
| `app.checkUpdate` | the newest release above this version, or `null`; `notes` is the literal GitHub body for this exact tag and `page` its canonical release page; `downloaded` and `file` (path or `null`) report a verified file still present for that version in this session |
| `app.tours {launching?}` | approved local tours through this version, newest first; after normal `app.ready`, `launching: true` records the profile baseline and returns pending upgrade pages, deduplicated by feature ID |
| `app.tourPresented {versions}` | acknowledge the covered published versions only after a first page has painted; merges under the profile lock, without changing preferences/library/preview state |
| `app.update` / `app.installUpdate {file}` | job id (download and MD5 check, result `{file}`; reuses the verified file for the same version) / swap in the build and quit after Restart (a macOS disk image that does not mount starts the installed app again); `classic_open` while any classic window is open (never queued) |

HTTP endpoints outside `/api/rpc`, each with its own token, never the session token: `GET /api/classic/control` and
`POST /api/classic/report` (the classic child's one-time token, see Classic window) and `POST /api/instance/raise`
(the raise token of `instance.json`).

### ZIP export

`mod-export.js` provides one flow for the student-detail Export action and Settings > Mods > Export Mod Library /
Export Applied. The confirmation uses `UIPopup_Sort` and the shared option rows. It shows the actual default output
path, target server and platform, Choose Directory on the left and Confirm on the right. The picker returns to this
confirmation with the chosen path; cancelling it retains the previous path. It never starts an export by itself.
The successful-export prompt opens the actual result folder only when the user chooses Open Folder.

`webui/export_service.py` prepares selected sources against real originals from the chosen configured install.
It uses the import preparation and compatibility checks without the duplicate-import shortcut or loose-file
output. Missing or incompatible originals block the export; server/platform labels are never substituted for
conversion. Existing secondary asset-extraction actions continue to use `actions.extract`.

The ZIP has one stable, portable directory per source and a root `manifest.json` containing the target, source
identities, filenames, sizes and SHA-256 digests. Bulk Applied selects distinct Mod IDs from desired-state manifests
across recorded installations and records those assignments, including while the master switch is off. All members
must prepare successfully. The archive is written to a temporary file in the destination, verified for ZIP integrity
and member hashes, flushed and atomically published. Failed preparation or publication does not leave a partial
new ZIP. This exports user-requested files at runtime; no game assets are included in application builds.

### Import grouping

The batch import (`import.analyze`, with kind and variant; shown as Import Mod, and opened by a
student page's Add Portrait, Add Memorial Lobby and Add 3D Model) and Add (`mods.add`, drag and drop, one
upload) group files with one rule, `judge.mod_versions`. Portrait and lobby files use their physical core and kind as
the set key. Model files use their variant's prefab slot key across every physical core in that slot: a complete set
of prefab and dependency files becomes one row, one library Mod and one selected source. The filenames keep their
physical cores. The set stays together whatever its parts' dates (Spine textures and text, a model's prefab and
dependencies), and an apply writes every selected part. When the install keeps multiple dates of one
part side by side, those files also stay in one Mod. Only a repeated part whose dates the install does not keep together
splits the whole set by date, one row or Mod per date. Collection batches keep their separate dated slots.
`other` bundles keep their own handling. A Mod's `date` is the newest date of its parts. Library Mods stored before this
rule keep the split they were stored with; nothing is merged or migrated. Batch folders still separate alternatives.

Import Mod keeps incomplete rows in the session. A row is `incomplete` when its CAB dependencies cannot resolve through
its selected files and unreplaced install files, or its source folders contain unselected files of the same student
variant and kind (including every physical core of a 3D slot). Other slots in a mixed folder are silent. The analysis
job searches source folders and folders added during the session using UnityFS directories only, without loading the
unselected bundles' assets. It reuses the existing dependency inspection's unresolved references; UnityFS directories
themselves contain the supplied CAB names, not serialized-file external references. Directory entries supply paths;
embedded Mod names never supply a path. Each row's `missing` contains `unresolved`, the first 40 unresolved CAB ids
(`cabs`, sorted) and ordered, deduplicated `found` files (`path`, `name`, `size`). Missing rows are unticked and
`import.run` skips them as non-importable.

A blocked or incomplete row also carries `bundles`: for each of its files (at most 20) what the check read, from
`Inspection.summary` (the analysis's own cache; only the serialized-file headers of a bundle it already loaded are read
again): `file`, the build targets (`platforms`), the Unity versions (`unity`, major.minor), its own CABs and stream files
(`cabs`, at most 10) and how many other CABs or stream files it references (`references`, `null` when only headers were
read). A refusal from the files' format can so be told from one guessed from their names or targets. A blocked row
carries the resolver's evidence as well, `resolution`: per file (at most 20) its `method` (`name`, `name_without_crc`,
`cab`, `asset_path`, `asset_key`, `part`, `none`, or `null` when the analysis stopped before the asset stage) and the
official parts (`targets`) and their `dates` it found; and, for a guessed source, `attempts`: every server and platform
tried with its failure `code`. The error report's import rows and the analysis log carry `bundles`, `missing`,
`resolution` and `attempts`, and the report's rows `previewed`: whether the import preview composed the row (a refused
row that previewed shows the preview and the analysis disagreeing). CAB ids and the game's own file names stay, other
file names are redacted.

A blocked or incomplete row's status badge opens Details (`import-details.js`, the game's message popup, whose frame fits its content above the shared 786-unit minimum): the row's reason (its short label with the error text, a file name that opens or ends the text left out, or the missing-file status), then a paragraph a file (its name, `import.detail.platform`, `import.detail.unity`), the unresolved dependencies (`import.detail.unresolved`, then one CAB id a line, from `missing.cabs`) and the files of the same slot found in its folders (`missing.found`). Its hit area is the badge background's box (`badgeRect`), above the row's own; opening it does not select the row.

The status shows the number of found files, or the unresolved count and the folder note when none were found. The
Import State filter includes Missing Files before Imported; the footer counts incomplete groups. After an analysis,
rows with found files that were not asked about yet open their confirmation automatically: one row with the
filenames, roster variant name and tab name, several rows with Complete All's confirmation listing those groups.
Complete All is available with one or more fillable rows. Declining suppresses the same prompt until Recheck. Confirming adds the found paths and
analyses again. Files supplied later through Add Files, Add Folder or drop join the incomplete row by slot or missing
CAB; an ambiguous owner or a conflicting part from another folder is refused with the candidate paths recorded in
the row's error parameters. Initial selections from separate folders can join when a unique CAB dependency proves
their relationship. Folder separation otherwise keeps alternative Mods apart.

A row with nothing found offers Choose Files, opening the native file picker in its source folder (`import.pick`
accepts `directory`; pywebview supports it on macOS and Windows). An analysis that completes a row selects it and
scrolls it into view, clearing a filter that would hide it. Import runs only the ticked complete rows; missing rows
retain their state and can receive files later in the session.

Import Mod's rows are laid out with the design system (`docs/design-system.md`; `import-layout.js` `row()` over the
primitives of `ui-grid.js`). Every text of a row but the name is `TYPE.dense` (42, wrapped, never smaller). The name plate
(title and divider) keeps the prefab's place; the kind (Portrait, Memorial Lobby, 3D) is on the name's line, right aligned
on the divider's end (926) above it, its box centred on the name's, `SPACE.l` after the name's box (owner, 2026-11-07). The
first line (the metadata, or the counts block of a merged row) has its tops level, and the badge ends it on column 910 on
the same centre line (the reference glyph's centre, `textItem`'s `mid`): one line, its caption `CONTROL.badge.pad` (16)
each side, `SPACE.l` after the metadata, which takes the rest and wraps. Only a badge that would leave the metadata less
than `COLUMNS.metaMin` (360) takes a line of its own (no shipped status in any language does). A row without a badge (a
result row) ends its metadata on column 550, clear of its action control. The lines under the first (a badge on its own
line, the route, the note) are `LINE.stack` of the size apart (57.75); a badge on its own line keeps the divider-to-meta
ink gap below the metadata. A refused row's badge reads Cannot Import - <reason> (`import.reason.*`, else the error's own
text when it needs no parameters, else Import Check Failed); the note under the metadata keeps only a missing official
copy's repair guidance. Choose Files goes to the line under the badge, one gap below it, its right edge on the badge's,
and the row grows for it (owner, 2026-11-07). Its caption matches the badge's screen size. It is a `buttonS` control
(`controlWidth`): its caption and `CONTROL.buttonS.pad` (45, the body's slice border included) each side, in the button's
own units, 240 at least; reference-glyph ink is centred on the face
between the top and bottom slice borders. Native button decoration still uses `clipButtonPatterns`. Footer buttons
wrap at their group's shared size (`NGUI.wrapText`), preserving Thai word breaks instead of independently shrinking.

Add and Import Mod both use `ModLibrary.identify_import`: an installed name (CRC aside), or a verified kept
original of that name, owns the target and kind. Other filenames use content identification. Portrait and lobby aliases
canonicalize to the install's slot core (`variant.spr` / `variant.lobby`). A model keeps the core its assets name; a
dependency under `characters/<model>/` never becomes the variant's prefab core. A separate model dependency core can
be stored and applied to the variant's slot when it is one of that slot's cores. Add and Import Mod keep the same
filenames, target and library identity.

One resolver decides which official parts a set of Mod files replaces or updates (`bmpt/library/targets.py`,
`ModLibrary._resolve`, BL-113): Import Mod's analysis, the import preview's complements, Add's dependency check, the
compatibility check, Enable and Apply (`_plan`), library previews, adapted builds' inputs and generic slots all ask it,
and a test checks that these call sites decide nothing themselves. Per file:

1. a current release: the exact installed name, else the name without the CRC, else the unique CAB match, with a
   unique part (prefix and resource type) breaking an otherwise undecidable match; remaining ambiguous current matches
   give `mod_target_ambiguous`. Such a file replaces its part whole and is never outdated;
2. the other files (older releases, pre-split bundles, another build's names such as Global mobile's resource-type
   parts), per name prefix and core: the parts holding their asset paths over every date of the core (a build keeps an
   asset path in exactly one bundle; a core keeps parts under several dates), else the parts sharing an asset name and
   type; only for files whose assets cannot be read, their part (prefix and resource type or number) at its newest
   date. A file whose readable assets no part holds finds nothing.

The resolution gives the parts the files write (`updated`: the targets of the analysis, the dependency check's
exclusions, the install groups of Enable and Apply) and the parts they stand in for (`replaced`: those replaced whole,
those whose every asset path they carry, and, for unreadable files, their parts), which the preview and the
compatibility check drop; a part updated in part keeps its other assets under the Mod's files. `replaced` is always
within `updated`. A conversion or rebuild writes the parts of `updated` sharing an asset name and type of the import's
asset types with the files. Generic slots go through `targets.generic`: a collection batch's exact name, a current
release, the bundles of the prefix holding the file's asset paths over every date, else the newest file of each slot of
the prefix with its exact name or sharing a container, name and type; a generic record's slots hold their newest file
(`targets.slot_files`). Older releases are not refused as ambiguous replacement targets.

A generic part (prefix and part number) that several files of one folder share (a group packed one asset per bundle,
whose names differ only in the CRC, or a part kept under several dates) gives each of its files its own slot, keyed by
the file's CAB as well (BL-101; `judge.learn_packed`, learned from the headers of the install's files of the parts the
library's generic mods and the files being imported use). A mod file takes the slot of the install file with its CAB,
else of the one install file it updates. Configurations and records of the shared slot an earlier build used move to
the files' own slots at the library's start.

Dependency checks retain every install file the Mod does not replace, including dated siblings. External CAB references
resolve through one revision-checked `CabIndex` per install and resource folders, excluding replaced files. Missing
originals do not remove those dependencies. The check refuses only missing references or Spine pairs added relative to
the official set, using current parts as the comparison baseline when originals are unavailable. That fallback never
becomes an official rebuild or restore source. `mod_parts_missing` names a supplying file in the upload's folder when
available, else the replaced install file that held the missing CAB; references held nowhere retain their CAB IDs.

### Batch import origins and conversion

Select for Preview and Apply to Game are independent checkboxes directly above Import. The options dialog no longer
contains Apply After Import. `previewAfterImport` defaults false; `enableAfterImport` retains its existing true
default, and saved explicit choices remain unchanged. Preview selection writes only the saved preview source;
application writes the desired-state manifest and, while enabled, the game files. Failed imports never select a
preview. A library-only assignment remains storable/previewable when no compatible application target exists.

Remove Imported removes `done`, `library` and `installed` rows from the current list only. It does not delete stored
Mods or change installed files; pending, conflicted and failed rows remain. The import list, selection and view
controls remain in memory when navigation leaves and returns during the same session.

The scrolling areas of Batch Import hide the native bars (`app.css`) and draw the complete
`UICharacterCollection/SeletControl/List/Scroll Bar` prefab over their right edge, as `Popup.scrollArea` does
(`scrollbar.js`, bound by `bindBar` in `batch-import.js`): the import list, and the dialogs' area of Advanced Options,
the Import State filter, Import Results and Assign Character. The bar follows the area's own scroll position and shows
only while the content overflows; the list's bar is drawn again when the search box changes its length. Assign Character
has no scroller of its own inside that area: the 24 students of a page lie in it, and paging scrolls it back to the top.

A failed row in Import Results shows only localized text: the row's short reason, then ` - ` and the `error.<code>`
string with the failure's parameters when the locale has one that says more. The code and parameters are not shown; they stay on the result and,
for a blocked row, on its list entry, which the error report reads (`BAMTRecovery.importList`, `import_row`), and the
backend logs each failure with them.

Direct replacements in current slots import even if the install has no official copy. Asset transfers still need
verified official baselines: outdated updates, cross-platform or cross-server conversions, format/release rebuilds
and Spine-version updates can become `blocked` with `original_unavailable` during analysis or Recheck. If a baseline
disappears before preparation, the execution result can be `failed` with the same reason. Both use Official File
Overwritten, No official copy left and the existing repair lines. No live Mod is used as an official conversion base.

`import.analyze {paths, recursive}` returns a library writer job ID; its result is `{entries, skipped, revision}`.
`Entry.state` is `ready`, `convert`, `update`, `blocked`, `library`, `installed` or `done`. `done` means
the entry imported successfully in this session; it is unchecked and cannot be selected.
Completion survives leaving the page and re-analyzing the same unchanged inputs, including
direct inputs. Rebuilt and converted inputs are recognized as `library` through persisted source
identities, including after restarting or moving the source files. Completion is scoped to the library and pinned install and
requires the imported library Mod to still exist. Pre-existing library/installed entries,
skipped updates with no output and failed imports do not become `done`.
Older conversions without source identities are compared with a temporary conversion against the verified
official targets. All output parts must match an existing entry after normalizing compression and CRC tails.
Successful matches persist provenance; misses are cached against source, target, options and library revisions.
This check neither adds a Mod nor applies one; a failed optional comparison leaves the import verdict unchanged.
Model list and comparison pictures use the shared 3D still queue with live, imported or library sources;
NPC cores without student-list entries resolve through the current game's model index.

`import.run {ids, options, revision}` requires the revision returned by the latest analysis.
Queued work checks that revision again before building or writing. Only `ready`, `convert`,
and `update` rows can execute; a fresh blocked verdict stops the build, library addition and
apply, and requires Recheck before a retry. Merged selection filters each member separately.

Parallel preparation reports completed entries separately from the active entry's input ordinal. The UI shows
completed/total and remaining, followed by the current phase and student. Per-job sequence numbers prevent a delayed
poll response from overwriting newer SSE progress. Verified original inputs are snapshotted before any row applies,
so two Mods for the same slot cannot rebuild from each other's writes. Each apply keeps its own transaction and
rollback; library maintenance runs once at the end of the batch. Exact current-name, CRC-ready inputs skip redundant
planning and staging, while the final resource checks, backup, content verification and rollback remain in place.
A direct copy honours the CRC option (On, Off, or Auto from the official file) and appends the configured Extra Bytes
before the CRC fix, as a rebuild does; a file whose CRC already matches its name is copied unchanged.
Staging keeps the bundle file names, which carry the part, date and CRC, and names the folders around them by the first
16 hex digits of the entry id or the original's SHA-1 (`batch_import.staging_name`), so the longest official bundle
names still fit the Windows path limit under an ordinary library folder.

Job waits have no default elapsed-time deadline. Each status RPC has a 15-second limit; transient failures keep the
original job ID and progress while the UI shows Reconnecting. Polling retries with bounded backoff. Window focus,
page restoration, visibility restoration and an SSE reconnection immediately refresh that same job, cancelling
obsolete reads. A confirmed backend completion or failure settles the wait; a suspended window does not fail or
resubmit the batch. Explicit timeout/error limits remain available to callers that require a bounded wait.

The import list prepares and decodes visible row thumbnails before committing their DOM together; offscreen rows
load when scrolled into view. The roster similarly waits for its visible portraits before ending the loading hold.
Completed import rows and the result dialog reuse their immutable analyzed input previews. Only a successful game
apply invalidates the current-game comparison; saving a deferred choice does not reload unchanged game pictures.

Results use `configured: true` when that import successfully configured its Mod as
the variant's chosen source, and `applied: true` when the apply was not deferred. With `enableAfterImport`, each newly imported Mod selects its
source. Existing library Mods and unchanged outputs are skipped without applying. This applies to
`spr`, `lobby`, `model` and `other`. The master switch can remain off: configuration succeeds
immediately and the existing deferred apply writes game files when it turns on. `applied` remains false until then.
For several imports targeting the same variant
and kind (or overlapping `other` slots), the last successful import in the submitted ID order
wins. Completed rows submitted again are unchanged and do not select their sources again.

Each entry adds `source: {server, platform, detected} | null` and `target: {server, platform}`.
Server values are `global` and `japan`, the servers supported by Settings; platforms are
`android`, `ios` and `windows`. A target platform can be `null` when its metadata is conflicting
or unsupported. `source: null` means that a complete source could not be established.

`import.setSource {id, server, platform}` returns a library writer job ID whose result is
one replacement entry with the same ID, a new `revision`, and `source.detected: false`. It checks the upload's
fingerprint and pinned folder again. Choices survive rechecks of unchanged bytes in this session.
A new upload or library/folder context invalidates stale analysis as usual. The page keeps the
choice UI in `batch-import.js`'s `sourceLabel`, `sourceControl` and `openSource` functions; only
`renderPreview` hooks them into the detail panel. `import-layout.js` is unchanged.

Origin evidence, in order:

- Every serialized Unity build target is read. Android, iOS/iPhone and Windows/Windows64 map
  to their platforms; mixed or unsupported targets have distinct blocked reasons. Explicit
  internal Android/iOS/Windows path segments can resolve an otherwise unknown build target.
- Bundle/container names, AssetBundle dependencies, external references, Spine and texture
  names are inspected. `_home_gl` and `com.nexon.bluearchive` identify Global;
  `com.Yostar JP.BlueArchive` identifies Japan (the package convention in `adb/paths.py`).
- A CAB name has no region meaning by itself. A source's serialized CAB identities matching
  recoverable official CABs can identify the selected install's server. Numeric resource types,
  `textures` and a plain `_home` do not prove a server. Otherwise the user chooses the source.
- The target uses official serialized metadata, resource folder platform names and Settings'
  pinned install/server. Explicit official region evidence takes precedence over the setting.
  No install discovery or user-folder search is started by origin detection.

Platform/server differences produce `update`, including identical filenames. The classic
`core.process_mod_update` extracts assets, decodes/re-encodes textures and injects into official
bundles, preserving target Unity metadata, CABs and dependencies. Conversion uses `name_type`
matching because platform path IDs and containers need not agree. Android and iOS are accepted
as mobile sources/targets. Across servers, files the target resolver places by name, CAB or asset
path are accepted; the others' filename slots (prefix and resource type, ignoring release date/CRC)
must match destination slots (`conversion_names`). Output passes the normal
library platform/dependency checks before add/apply; the permissive conversion preflight is
never used to store or apply a source directly.

`Entry.preview` is registered independently of eligibility. Spine import URLs still use the
existing memory-only routes. A portrait or lobby row that transfers assets (`update`, `convert`)
previews what its import writes: when the import's asset types leave out `TextAsset` or `Texture2D`
(the types a Spine preview reads), the composition keeps every official part and leaves those types
of the source files out (`gameassets.spine.Composition`, part of the preview's revision); whole-file
rows (`ready`) and models compose as before. Models also expose the import preview bundle set through
`/asset/model/<variant>.glb?source=import:<key>`. Missing official parts do not prevent a
self-contained source preview. Changed bytes, a new analysis or a changed folder expire handles.
Model `mod:` and `import:` sources must contain only model files of the variant's slot cores; the exporter always
uses that variant's prefab. The `official` model preview overrides every slot file with its verified official copy,
using the current file only when no official copy remains. This preview fallback does not supply an official baseline
for rebuilding a Mod or make a file restorable. The default `official_bundles` call still requires verified originals.
A model export also loads bundles outside the slot: shared shaders, and textures stored under another core (ch0263's
materials take theirs from the NPC en0002's bundle, which another Mod may replace). Every model source but `live` reads
those through `ModLibrary.preview_reads` (`GameAssets.model_glb(..., reads=...)`, applied to the prefab bundles and to
every bundle the CAB index finds): `official` their verified official bytes, the current file only when no official
copy remains; `mod:` and `import:` the bytes the configured source of each file's slot writes (its kept built copy,
else the Mod's own file of that name, else the game file while it carries that source), else as `official`. So no
source but `live` follows the Enable Mods switch or the game folder's current files. The export's cache key includes
the rule (`PreviewReads.key`: the policy, and for `configured` the folder's configured and built entries); a persisted
export that read library files is reused only for locations the rule admits (`PreviewReads.trusted`).

New locale keys are present in all six locales. The reason keys also exist under `webui.error`
with the same text, so both entry reasons and RPC/job failures translate consistently:

| `webui.import` key | zh-CN | en-US |
|---|---|---|
| `source.choose` | 选择原始版本 | Choose Source |
| `source.unknown` | 原始版本未知 | Unknown Source |
| `source.server` | 原始区服 | Source Server |
| `source.platform` | 原始平台 | Source Platform |
| `source.android` | 安卓 | Android |
| `source.ios` | iOS | iOS |
| `source.windows` | 电脑 | PC |
| `source.route` | `{source} → {target}` | `{source} → {target}` |
| `reason.source_unknown` | 原始版本未知 | Unknown Source |
| `reason.source_conflict` | 原始版本信息冲突 | Conflicting Source Metadata |
| `reason.platform_unsupported` | 不支持的平台 | Unsupported Platform |
| `reason.target_unknown` | 游戏目录平台未知 | Unknown Game Platform |
| `reason.conversion_names` | 无对应文件名 | No Matching Filename |
| `reason.no_matching_assets` | 无匹配资源 | No Matching Assets |
| `reason.conversion_failed` | 转换失败 | Conversion Failed |

### Manual character assignment

`character-picker.js` uses the roster's search, filters, sorting and variant-grouping controls over the offline
assignment catalog. That catalog combines shipped student metadata and the character map, so ownership selection
can include a Japanese-server student whose resources are absent from the selected Global installation. Confirming
the owner persists `assignment` with a self-contained, validated Mod; re-analysis recognizes the stored result. A
search or filter that shows no student puts `hall.noCandidates` in the list, and while the chosen student is not among
those the view offers, Confirm is the game's disabled button and answers nothing (`dialog` of `batch-import.js`
passes the content `primary(enabled)`; `Popup.disabledConfirm` draws the disabled button over the live one).

`assigned_catalog.py` adds saved assigned students to roster/detail results without inventing resource handles.
`storedKinds` describes kinds present in the student's library Mods, while `localKinds` and `spr`/`lobby`/`model`
describe actual local resources. An assigned-only student can select its own Mod for preview. Without a local
target, Apply stays disabled with No Matching Game File, sibling sources are not offered as substitutes, and an
unset preview stays unset. A manual label does not bypass bundle-readability, dependency or compatibility checks.

### Atlas remap

`atlas_remap.py` reconstructs current official atlas pages from matching mod regions. When either physical page
axis is smaller than its atlas declaration, the whole page is enlarged to at least the declared dimensions with
Pillow's LANCZOS filter before any region is cropped or placed. Larger pages retain their resolution; no dependency
is added. This also normalizes undersized official destination pages, so the result uses the official declared
size. Rotations, trim offsets and premultiplied alpha remain part of region placement. `mod-compatibility.js` adds
"Textures upscaled to {w} × {h}" to the result for each distinct enlarged size (`remap.upscaled` in all five locales).

A repaired copy and its original link to each other (`repaired_from`, `repaired_id`), and a linked mod is not repaired
again (`remap` and `accept_remap` refuse it with `entry_changed`). `remove` clears the original's `repaired_id` in the
same state write that removes the copy, and an asset deletion's rollback restores the link with the snapshot of records.
A `repaired_id` that points at no record counts as none (`ModEntry.repaired_id`, the compatibility report and its stamp,
the repair gate), so a library that deleted the copy before this recovers without a migration. A held record (its folder
is missing for now) is there: its link stays and the original stays not repairable while it is held.

## Asset routes

| Route | Class |
|---|---|
| `/asset/ui/atlases.json`, `/asset/ui/sprite/<atlas>/<name>.png` | light |
| `/asset/ui/textures.json`, `/asset/ui/texture/<name>.png` (also the PC pointer and the touch effect's textures, an index of their own outside the listing) | light |
| `/asset/ui/layout/<root>.json?path=<subpath>` | light |
| `/asset/art/bg/<name>`, `/asset/art/portrait/<name>.png` | heavy |
| `/asset/spine/spr/<core>/<file>`, `/asset/spine/lobby/<core>/<file>`, `/asset/spine/mod/<id>/<file>` (`skeleton.skel` and `skeleton.atlas` stand for the skeleton named like the core, a mod's own core for a mod, else the only one) | heavy |
| `/asset/spine/official-spr/<core>/<file>`, `/asset/spine/official-lobby/<core>/<file>`: verified official skeletons, atlases and pages, from kept originals when the game files are overwritten | heavy |
| `/asset/spine/<kind>/<key>/manifest.json`: the revision of the selected ordered bundle set; `/asset/spine/<kind>/<key>/<revision>/<file>` serves only that revision, including relative atlas page paths | heavy |
| `/asset/spine/spr/<core>@<id>/<file>`, `/asset/spine/lobby/<core>@<id>/<file>`: the core in another known install, `<id>` from `mods.state.installs` (in the path, so the skeleton's own relative files follow it), read through a GameAssets over that install's folders, made on first use and dropped with the services rebuild; an unknown id is `404` | heavy |
| `/asset/spine/lobby/<core>/lobby.json` (prefab layout, touch boxes, clips, idle pools, sound events, post-processing, particle and mesh effects and sprites, page materials, pinned bones, the intro timeline (`gameassets/intro.py`); no skeleton is extracted) | heavy |
| `/asset/spine/lobby/<core>/fx_<id>.png` (a particle, mesh, sprite or page shader texture the lobby's `fx.textures` lists) | heavy |
| `/asset/spine/lobby/<core>/sfx_<id>.wav` (a sound clip the lobby's `sounds` list, decoded in memory) | heavy |
| `/asset/model/<variant>.glb` (the variant's chibi, `model/gltf-binary`; `?source=` `live`, `official`, `mod:<id>`, `import:<key>`, or `variant:<key>`: the slot carrying that variant's costume) | heavy |
| `/asset/voice/<variant>/<clip>` (a lobby voice relayed from Kivo) | heavy |

Thumbnails (faces, busts, expression cards, variant cards) are rendered in the page from the skeletons, in one
shared offscreen WebGL context, and kept as in-memory blob URLs. They are framed on the face (the expression and eye
slots, halos left out; without a part named like one, the face-sized parts the expression animations switch, as
many NPC portraits name their face slot `0`, `00` or `defalt`), sized by its width held between 170 and 400
skeleton units, never by the skeleton's bounds; the bounds stand in only where no face is found.

`detail.js` resolves the `official` card and `variant:<key>` cards through `official-spr` / `official-lobby` for the
candidate's core; `mod:<id>` uses `mod` and the mod's own core. `popup.js` receives these thumbnail promises and adds
no file selection. `ModLibrary.official_bundles` resolves every part through `_official_for`: verified kept original,
verified game file, verified `.backup`, or a verified copy elsewhere. Missing official data fails the whole preview
with 404 and `thumbs.js` returns null, leaving the existing plain card. Mod previews overlay the mod's parts on
omitted parts selected by the same replacement mapping as import and apply. Omitted parts prefer verified originals,
falling back to current files when no official copy remains; `preview_incomplete` marks that fallback.

`spineAssetBase` resolves the manifest for every load, before the thumbnail cache lookup. The revision includes the
ordered resolved paths, sizes, nanosecond modification and change times, covering an atomic replacement that
preserves size and modification time. It sits in the directory shared by the skeleton, atlas and every texture page.
`Services.asset` checks the revision before and after extraction and rejects a source change during the request;
`GameAssets.spine_files_from` uses the same file versions and rejects changes during extraction. Thumbnails are
cached by resolved base, frame and expression, so correctness does not depend on change events or `forget`.

Files of a core merge by name, the last bundle winning. A mod preview lists the official parts first and the mod's
last; the game folder lists parts by file name, so an official part could come after an applied mod part and win a
texture both carry (the mod's atlas drawn over the official page). `ModLibrary.live_bundles(core)` gives the live
`spr` and `lobby` sources the preview's order, the parts that are not official last (cheap judgement); a core the
library does not know keeps the game's order. Official and preview parts are limited to the core's Spine category
(`spinecharacters`, `spinelobbies`, `spinebackground`).

`thumbs.js` rests a still in the pose its skeleton settles into: a portrait in `idleAnimation`, a memorial lobby
(`thumb(base, frame, expr, true)`) in `lobbyIdle` (`rest-pose.js`), which `lobby.js` `idleOf` uses too. The tracks are
cleared before the idle is set, as the player starts the skeleton's first animation (a lobby's intro) and a new one would
mix in from it. A load also takes the other frame of a card (`card` and `bust`), so the big picture of a card is not a
second load; the promise of a thumbnail has `bump()`, which `popup.js` calls for the cards in view (again on scroll),
and the roster asks for its rows in view first. Pictures stay in memory, keyed by the resolved base (the revision
the files carry), frame and expression, with the default-on Preview Cache retaining validated lossless PNGs on disk;
`spineAssetBase` shares one answer between overlapping requests.

Switch popups hold capture priority for their entire thumbnail scope, including gaps between lazy card requests.
Opening one cancels any active off-screen background attempt and suspends queued background work; surviving
subscribers resume after the last popup closes. Popup and background consumers of the same still share the result.
Both Spine and model queues respect this scope. Spine loading has a 15 s deadline after dequeueing, and each
rendered image has its own 15 s budget; neither queue waiting nor earlier faces consume that image's deadline.
Timed-out captures keep their subscribers for three retries, after 2 s, 5 s and 15 s of an idle renderer queue.
New work restarts the idle interval without spending a retry. Closing a scope removes its pending work and timers;
late callbacks cannot publish cancelled images. Speculative companion captures wait until no popup is open.

The main portrait resolves its current candidate in the same way. The main memorial lobby uses live `lobby` files,
or the configured `mod` art while mods are off. Its metadata and effect/audio requests keep the live lobby's
revision; its art layers and pages use the art source's revision. Player reuse and measured art limits require both
revisions to match. Pending manifest waits cancel on a newer request or leaving the view, and serialized player
loads keep their cancellation and timeout handling. Lobby effect/audio caches include their source bundle versions. The roster uses
collection art when present and live `spr` thumbnails otherwise; entry cards use live files of their named install.
These thumbnail paths also resolve a manifest before caching.

The main portrait retries once when a rejected load has a newer manifest revision, like thumbnails. A missing or
invalid manifest reports its URL and status or validation error in the console; the detail error also names the
logical portrait source. Asset-route failures log the request and cause at warning level. Live routes without a
revision use the same mod-last order as the manifest, and only an unknown library core falls back to discovery;
read failures and dev-mode refusals propagate. Clearing the asset cache evicts extracted Spine files, while a
reload resolves and extracts the same live composition without needing a mod preview or kept originals.
The portrait starts independently of the detail layout, using the game's default framing while the layout loads.
A cold layout export reads the atlas index too, so its failure must never prevent portrait requests from starting.
Manifest network and JSON failures retain the URL and original cause; JSON errors include the response status.

## Front end

Invisible buttons over drawn controls carry their accessible names from the locale files, never a painted glyph: the
detail screen's student arrows (`hall.previous`, `hall.next`), Copy with the file it copies (`window.copy`), the variant
chips and the expression strip's cards (the chip's or card's caption; a face is `photo.natural` or `photo.expressions`
and its number, as the camera names it; the pile is `chips.more`, Collapse `fold.label`), the roster's six segments and
Group Alts, the asset consent's Agree and Decline, and Assign Character's page arrows (`photo.previous`, `photo.next`).
A control that picks one of several or switches a setting carries `aria-pressed`: the shown variant's chip, the picked
face and the segments and Group Alts that are on. The chip that opens the picker, the pile and Collapse are not toggles.

A port of `prototype/main-screen-v4`: `ngui.js` (prefab renderer), the roster, the detail screen, the popups, the
consent popup and the lobby player, as ES modules under `web/js/`, one page with a hash router (`#/roster`,
`#/student/<family>/<variant>`). Its data comes from the RPC methods above instead of the generated `data.js`, its
art from `/asset/...`, and its strings from the locale files. Prototype-only URL parameters and render hacks are not
ported.

### Student list

`roster-order.js` decides which cards the list shows and in what order (pure functions, tested in Node from
`tests/test_roster_order.py`); `roster.js` draws them and `display.js` is the game's display settings popup
(UIPopup_FilterSort) behind the sort button.

- One card per variant, or per family with "Group Variants" (its attributes are any of its variants'; the card shows
  the first variant, or the first one the search found). Cards with mods first, the rest after the "No Mod" bar;
  favourites lead each group whatever the sort.
- Segment filters (mods, recruitment), the display settings' filter groups (favourite, portrait original or replaced,
  memorial lobby, 3D model, student or NPC; options in a group add up, groups narrow) and the sort (recently viewed,
  recently added, name with the UI language's collation, mod count, default); the order button flips the sort. The
  funnel on the sort button turns blue while a filter group narrows the list. All of it is saved (`settings.set
  roster`); the search lasts the session.
- The search (the game's CharacterSearch box over the top of the list) matches every name language, the family's base
  names, the aliases (the variant's and the family's: the CN server's zh-CN names, never shown; see catalog), the
  user's own aliases of the family (the detail screen's alias editor) and the file id, ignoring case, width, kana and
  separators (with the marks inside names, as in Shiroko*Terror); every word must match. Its input opens focused at once, before the box is drawn round it, and stays in the page across
  rebuilds, so nothing typed meanwhile is lost. The detail screen's arrows follow the same search.
- With no match the game's empty list shows (`LeftInfo_Empty`: Arona's picture over the faint disc, the message under
  them). `roster-widgets.js` `emptyListY` puts its ink, from the top of the picture to the bottom of the message, on the
  middle of the paper the eye sees (`empty-block.js` `emptyBlock`, as Batch Import's empty list): the whole paper, or what
  the open search box leaves under it.
- Keyboard focus moves the list through its own scroll position (the clip never scrolls by itself, so the virtual
  rows and the scrollbar stay in step); the cards are in the page in the list's order, and the focused one shows a
  ring in the game's selection blue.
- A visit to a variant (`ui.visit`) is mirrored in the page's roster data, so "Recently Viewed" has it on the way
  back without a new `roster.list`.
- The detail screen's arrows step through the cards as the list showed them when it was left, in its order, wrapping
  round; a variant the list hides (another chip, a deep link) steps from its place in the whole list.
- Back from the detail screen, the list is built behind the loading cover. The visible rows finish their portraits,
  frames and panels before reveal; rows outside the viewport are queued after the visible ones. The first build
  waits the same way. Pictures are decoded too (`NGUI.decoded`, a 15 s failed-decode deadline; the URL and waiting logic is
  `image-decode.js`, tested in Node from `tests/test_image_decode.py`). WebKit lets go of the
  blob images a hidden screen draws (the sliced and tinted sprites `ngui.js` bakes): without the wait it painted the
  list's first frames (about 100 to 200 ms) with the portraits only, without the frame, the card frames and the
  dimming of the cards without mods. A rebuild inside the list (filters, sort, search) waits the same way for the
  pictures the list does not show yet.
- The left and right arrow keys step like the arrows, as in the PC client: its key mapping
  (`Assets/_MX/AddressableAsset/KeyMapping/PCKeyMappingSO.asset`) binds UICharacterDetail's `Arrow_L/Left` to
  `UI_KeyMapping_Common_ArrowLeft_isUsed` (LeftArrow) and `Arrow_R/Right` to `UI_KeyMapping_Common_ArrowRight_isUsed`
  (RightArrow), with no second key and no key icon on the arrows (`step-keys.js`, tested in Node from
  `tests/test_step_keys.py`). One step at a time: the next one waits until the screen shows the student the last one
  went to, so a held key steps at the pace the screen follows and starts no load it would drop; a press meanwhile
  waits (the latest one only), a held key's repeat only while the key is held. The keys do nothing with a modifier
  held, while a text field or an input method has them, under a popup (the screen's, the settings, the consent or
  update dialog), in a fullscreen view (the lobby, the 3D model, the portrait's visual mode), during a job or another
  change of the screen, or behind the loading screen. A focused control of the screen (an arrow stepped with Enter)
  keeps the focus in the new screen.

### Quit

The student list quits the app as the PC client's back key does in the lobby (`quit.js`, its rules tested in Node from
`tests/test_quit.py`).

- **Where.** The top bar's round back button (`Common_Circle_Back_Btn` with `Common_Icon_Back`, as on the detail
  screen) shows on the student list too, and Escape on the list does the same (decision 13). The detail screen's back
  button still goes to the list.
- **When Escape is left alone.** When another handler took the key (`defaultPrevented`: the search box, the display
  settings), while an input method composes (`isComposing`, `keyCode` 229), for a held key's repeat or with a
  modifier, while a popup host is open (`#consent`: the asset consent, scan consent and update popups; `#display`,
  `#settings`, `#dialog`, `#prompt`, and the detail screen's `#popup` and `#picker`), behind the loading screen, and
  on any screen but the list.
- **The prompt.** The game's quit popup: title Notice (`quit.title`), line Quit? (`quit.ask`), Cancel and Confirm, on
  the shared message popup. The PC client's own message popup (`UIPopup_System`) is not among the bundles the tool
  reads (the prefab index of the 2026-10 resource version has no common message, notice or quit popup), so the
  prompt is drawn on UIPopup_Sort's frame like the others. Escape, the backdrop and the close button cancel. Enter
  and Space confirm while the prompt holds the focus: the PC client's key mapping (`PCKeyMappingSO`, its JSON backup
  in the `keymapping` bundle) binds the confirm of `UIPopup_System` and `UIPopup_Sort` (`Btn_Ok`,
  `UI_KeyMapping_Common_BtnOK01`) to Return with Space as its second key; their cancel's Escape binding is unused.
  One prompt at a time.
- **Confirm** calls `app.quit`. While a writer job runs it is refused with `busy` (a notice), as the window's close
  button is. In the app's window the window closes. In browser mode (`--browser`) the server stops once it has
  answered, and the page shows Closed (`app.closed`) alone, without the top bar and the navigation. With `--serve`
  it is refused with `native_unavailable` (a notice).

### Bottom navigation

`nav-items.js` (tested in Node from `tests/test_nav_items.py`) lists the bottom bar's entries and decides what a
click does; `nav.js` draws them.

Student Hall (`#/hall`) is the root page, followed by Students and Batch Import. `route-history.js` records committed
page entries in memory. Back follows the previous entry, including Batch Import -> student detail -> Batch Import.
Student arrows and tab changes replace the current detail entry. Cancelling unfinished navigation returns to the
last committed screen, and failed transitions do not become successful history entries. Direct-entry routes fall
back to Student Hall; navigation history is not restored across app restarts. Source-page state, including the
import list and its view controls, stays available within the session.

- **Greyed entries** (decisions 18 and 19). An entry is greyed exactly when it has a classic tab and no web screen
  (`classic` and no `route`): the remaining classic tool entries after Batch Import. A later web screen is drawn normally once its
  entry gets a route. A greyed entry keeps the game's disabled tint and nothing else: no lock (these entries are not
  locked, they open elsewhere), no hover change, never the active look, and no tooltip. It stays a button:
  `aria-disabled="true"` (not `disabled`, which would swallow the click), focusable with the focus ring, and named
  "{label} - Classic UI" (`nav.classic`) for assistive technology.
- **The tint.** The game's buttons carry no NGUI button colours (see gameassets, "Button states"); a locked lobby entry
  (UILobby, `Front/Bottom/Menu/BG/Grid/<entry>/Disable`) shows the same icon sprite at `#797979` under a lock, and
  its label in its usual colour. The page reads that tint once (`/asset/ui/layout/UILobby.json?path=...`) and puts it
  on the icon as a brightness factor (0.4745; `data-tint="game"`; the icon at half until it is read). Without that
  prefab it takes NGUI's default disabled colour (`Color.grey`): the icon and the label at half brightness (`ngui`).
  Without a game folder the no-game screen shows the navigation too, with the icon in grey at half brightness and the
  label in the settings' disabled grey (`#9aa3ad`, `plain`). Its hint is `app.noGame.hint` (Detect it, or choose the
  game folder), with plain Detect and Choose buttons and no language or server controls. Settings is drawn from the
  game's UI and cannot open without a game folder: the top bar's Settings button then says the same hint in the plain
  notice, and any other failure of opening it is reported as a failure, so the click never fails silently.
- **The hand-off prompt** (decision 6). A click, Enter or Space on a greyed entry opens the prompt on the shared
  message popup: the entry's label as its title, "Not migrated yet, opens in the classic UI" (`classic.hint`), the
  checkbox Don't Ask Again (`mods.dontAsk`, unticked), Cancel and Open. It shows on every click. Open with the box
  ticked turns the pref `classicPrompt` off (`settings.set`) before the hand-off; Cancel, Escape, the backdrop and
  the close button save nothing. With the pref off a click hands over at once. Settings' General tab turns it back
  on: the row Confirm Before Opening Classic UI (On / Off) under More Settings (the General tab's rows scroll, so it has room).
  More Settings itself opens the classic UI without a prompt. Without a game folder the classic UI still works, and
  the prompt is drawn plain (Popups).
- **The hand-off.** `app.openClassic {tab}`, with the corner indicator labelled "Opening the classic UI" until the
  classic window is up (Loading screen); `classic_failed` and other refusals (`busy` while a job writes the game
  folder) show the Failed popup (`classic_failed`) or, for a refusal, a notice. One hand-off at a time: a click while one is under way does nothing.

### Student Hall

The working tree after 0.5.0 adds the 0.5.1-alpha caption and scene-camera fixes described in BL-009.
`dialogue-text.js` keeps Chinese subtitle fallback within Chinese scripts. Preferred-source readiness and late
caption enrichment preserve the current voice and expression timeline; language changes also update a retained
memorial scene without replaying it. Ordinary flat voice indices count voiced rows, not dialogue groups.

`student-hall-camera.js` owns a temporary non-editing scene view (50–300%). Mouse wheels and touchpad pinches zoom;
two-finger swipes pan. It reuses the detail viewer's wheel/trackpad classification, including WebKit gesture and
ctrl-wheel deduplication. The portrait renderer changes its shared viewport rather than scaling a captured canvas;
background layers, actor hits and balloons use the same projection. Layout state and resource identities are
preserved. Editing and modal controls retain their own input, and disposal removes camera listeners and frames.

`student-hall.js` renders portrait-composition and memorial-lobby modes with the game's `UILobby` controls and
`UILobbyCharacterSelectPopup_Slot` editor cards. Local actions precede the mode switch at the upper right. The global
Home control comes from `UICharacterCollection/NUITop_Scenes/Top/Right/Lobby`; no bottom-nav Hall entry is added.
The action and navigation groups use centred `ui-grid.js` rows, with 20-unit gaps between neighbours and 28 units
between groups, converted to CSS pixels with `S`. Their cloned `Able_Count/Bg` bodies keep 156 x 104 units
(`CONTROL.toolbar.h`); the selected action sprites retain their authored dimensions and existing optical sizing.
Cancel/Save shares 136-unit buttonS bodies, one fitted 48-to-34 caption size and a 20-unit gap.
Hall choices are separate from game application and detail preview selections. Every entry has its own UUID.
The far-right Fullscreen action clones `UILobby/Front/Right/Btn_Screen_On`; the exit clones
`Front/Right/ScreenshotMode/ModeInformation/Btn_Screen_Off`. Their icons use the same visible-size and native-body
centering as the adjacent controls. `student-hall-fullscreen.js` owns the immersive state, exit control, Escape
handling and restoration of hidden UI focus/inert state. It preserves the scene and any layout draft and does not
share the detail screen's exit control. Only the exit and scene interaction remain active while editing controls
and global navigation are hidden.

Portrait mode uses one shared canvas/context and camera. Each instance has independent skeleton animation state,
blink/talk tracks, position and individual relative scale. Layout editing queries posed public Spine attachment
vertices and UVs against cached atlas-page alpha, in reverse saved layer order. Normal-blend active slots with effective alpha at least 0.1 count; transparent areas pass through, including
when mirrored or scaled. One pointer surface uses that query for hover, press and drag; named DOM buttons retain Tab
and keyboard/assistive activation. Atlas alpha is read only in layout editing, in the background, one page per task: the page's bytes are decoded and
shrunk off the main thread (at most 1024 texels on the longer side), so entering layout editing never waits on them.
Until a page is read, and for a page that cannot be read (WebKit refuses canvases past its memory budget), picking
treats its attachments as opaque; a portrait's toolbar waits for its pages. Dragging changes position;
individual wheel/gesture scaling is disabled. Hover controls in the editor remove an actor; with two or more actors
they also change layer order and horizontal mirroring. Pressing an actor selects it at once and pressing another
switches it; pressing the selected actor again clears selection on release, unless the press moves more than four stage
pixels and becomes a drag. Pressing the empty scene and Escape clear it; a drag never toggles. A press ends on pointerup,
click, lost capture or a move with no button down, whichever comes first: the macOS WKWebView may send only a click.
Keyboard/assistive button activation uses the same toggle. Selection pins the toolbar against hover changes, survives
hit redraws, and ends on Save, Cancel, removal or leaving the page. With no selection the toolbar follows hover/focus;
transparent parts of the hovered actor's box keep its toolbar, so the pointer can reach it. A static three-stage-pixel gold outline uses
`UIPopup_Mulligan/Center/Popup/01/UIMulliganSlot/SelectLine` (`#ffe900`), held in `APPLIED_SELECTION_GOLD`.
A flat alpha-threshold shader through the public Spine batcher and skeleton renderer draws sixteen radius-three
offsets just before that actor's normal draw; non-Normal/inactive slots are excluded and front actors cover it.
Different portrait sources for one student may coexist; the same source cannot be added twice. The Common `Campaign_Icon_ChangeUnit` toolbar action opens the Hall portrait
picker restricted to the selected choice's family: the student's official and Mod portraits, excluding itself and
placed sources (a Mod portrait may go back to its own variant's official art). With no eligible source it is disabled. Choosing one replaces only the draft entry's variant
and source; UUID, position, individual size, mirror and layer index stay intact. Only the new art loads; saved source
actors remain cached through Cancel, without restarting other actors or the background.
Existing duplicate portrait rows are canonicalized on read without writing a migration or advancing the revision. Spine previews
wait for licence notice acknowledgement (see Spine licence); earlier Yes and No answers already count as acknowledged. The Add Portraits modal lists student names beside independently scrolling portrait-card
rows. Optional skin grouping, search, filters and sorting use roster semantics; only library portraits can be added.
Cards are still previews with explicit Add buttons; clicking them neither opens another preview window nor plays
in the background. The compact modal uses inclined native white row plates, equal outer-edge margins and a
scrollbar aligned with the toolbar's painted right edge. Layout changes remain drafts until Save; Cancel restores
the saved arrangement without recreating existing actors, textures or animation tracks.
The native `LobbyCommon` CharPos and SkeletonDataAsset scale define the projection baseline. Layout editing exposes
a compact global portrait-size slider from 25% to 150%, with 100% as the default.
It sits below the two equal-width Save/Cancel buttons at the upper right, aligned with the entire icon group,
in the native inclined button body. It has no visible caption, percentage field or Reset button.
Each portrait toolbar has a second row with the same native slider for its own size (50–200%, one-percent steps)
and a Reset button for 100%. The two rows occupy separate boxes. The toolbar is centred directly above the portrait's
visible head and touches it: the topmost visible point by the picker's rule and the centre of the visible band below it
(the top tenth of the visible height), cached per actor relative to its position and recomputed on a size or mirror
change. Spine's bounds also count transparent and off-body parts, so they can sit to either side of the character; they
are used only when nothing is visible. Where the toolbar would leave the stage above the head or cover a control (the
top bar, the Hall's top-right buttons and global size slider, measured live), it goes under the portrait's feet, or to
the stage's bottom when the feet are off-stage. Horizontally it moves inwards only at the stage's edges. It moves to a new size only on release or Reset, never under a dragged
thumb. Both size controls edit the draft; Save keeps them and Cancel restores them.
The global multiplier preserves entry anchors and individual scales and leaves the background unchanged. Slider changes
update the shared renderer and interaction bounds directly, including changes made while assets are still loading;
they do not recreate a canvas, renderer or asset request. New source batches retain a lossless last-frame cover
while shared-context guest players initialize; the complete new actor map is drawn before the cover is removed.
Only current/saved actor resources and the required context owner are retained, and background caching is bounded
to the current/saved choices.

Memorial mode has an ordered playlist and an active entry, with Previous/Next and entry editing. The same
variant/source pair can be added only once; different sources for the same student remain distinct. Legacy duplicates
are canonicalized on read without writing a migration, preserving the active source. Its modal places
six-column native formation cards on the left, landscape candidates on the right and the draggable draft playlist
below, on a pale native rounded panel. Green selection outlines have no check or sequence number. Picker toolbars
reuse the roster row without its screen title; playlist cards omit name strips and headings. Pointer capture implements
reordering in WebKit: the source remains at 50% opacity, a thumbnail follows the pointer, and a 2-stage-pixel solid native formation-blue line
marks the insertion gap without highlighting another card. Grab targets include the entire area above Remove.
Mouse and pen drag directly. Touch swipes pan the queue; a 260 ms hold starts reordering, tolerating small finger
jitter and lifting the ghost clear of the finger. Reordering owns its pointer stream, excluding simultaneous list
panning. Time-based edge scrolling continues with a stationary pointer. A second touch, loss of focus, cancellation
or disposal aborts the drag and removes capture, feedback, hold timers and animation callbacks. No selected student means recently viewed candidates by
default. Mod availability, counts and sorting are scoped to portraits in the portrait picker and memorial lobbies in
the memorial picker; current-server release filters retain their roster meaning. Removing the last entry is disabled and rejected by the persistence API; initial empty state remains
valid until the first playlist is saved. Each selected
official or Mod source runs through the existing lobby renderer, including its scene interaction and available
dialogue. Missing sources remain distinguishable from an empty playlist. A library-only Mod can render without a
local scene-layout resource, using the renderer's existing layout fallback.

Hall `createModal().draw` sends its actions through `popupFrame` and its shared `FOOTER` layout, preserving each
semantic and enabled state. Frame sizes stay unchanged. The 2150 x 1300 u portrait/replacement picker packs its toolbar
below the native title underline, then its optional search box and list, using the free header space before reducing
the viewport. Both search states end at 778.05 CSS px; the footer centre is 845.84 px (formerly 916.78). The background
picker moves its [130, 190, 760, 650] list up to y 176.70, retaining its viewport height and full rows; it ends at 826.70,
with footer centre 894.49 px (formerly 965.43). These content limits include `POPUP.clearance` (16 u).

The memorial editor remains stopped, with its original panels, candidate/queue controls and 66 px footer. Growing the
candidate Add and queue Remove bodies while retaining a full row cannot fit above the shared footer. Even a generous
lower bound with no inter-panel gaps ends at 845.11 px without search, or 903.49 with search, beyond
the 826.70 limit. Its current queue panel stays [115, 720, 1685, 195], ending at 915; footer centre stays 965.43 px.
Tests cover the migrated layouts in all six locales and both search states, and pin this remaining stop numerically.

`CAPTION_EXCEPTIONS` names exactly three Thai-only allowances: `hall.doneAdding` (44 u in its shared footer),
`hall.unsavedChanges` (36 u shared by Keep Editing / Discard / Save and Leave), and `hall.scaleReset` (30 u).
Call sites explicitly pass `captionSite`; the popup and small-button renderers validate the resulting size against that
site and the page locale. The token minima remain 48 and 34. Other Hall sites and all other locales retain those minima;
wording stays unchanged and every caption retains every character.

Hall `student-hall-ui.js` `button()` uses `SMALL_BUTTON`: height 136 u, caption 48 down to 34, fit width w-60,
label box w-30 by 116. `buttonBox` preserves each caller's centre and width, raising only widths below 240 u;
`buttonSize` gives sibling groups one size. The two 460 x 70 CSS px first-run buttons now draw 460 x 88.216 at
centre (960, 595). `updateAction` preserves the original control's migrated/retained role when refreshing it.

The portrait picker Add/Select bodies now sit 8 CSS px below the unchanged name strip and draw through `button()`.
Their minimum width is 155.676 px (240 u), keeping the former horizontal centre. Portrait row plates and nested strips
grow from 402 to 437.445 px, with vertical pitch 406 to 441.445: this reserves the existing name strip's largest
103.6 u two-line case, the 88.216 px button and the horizontal scrollbar's 12.973 px painted band. The artwork moves
up by the row's 16 px of free top space; its size and the name captions stay unchanged. The strip extends 12 px left
by its existing hit outset (x 240 to 228, width 960 to 972), while card/action stage horizontal centres stay unchanged.
Even the tightest searched viewport (Japanese toolbar) holds a full row. Horizontal card pitch stays 220 px.

The portrait toolbar uses centred rows: its equal 62 px icons stand 20 units times `S` apart, and its second row
starts 28 units times `S` below the icons, at y 80.162 px. The second row is the slider body's height (`UILobby`
`Btn_RotateSpine/ConditionRoot/Able_Count/Bg`, 104 u, 67.459 CSS px), total 147.622 px
(`PORTRAIT_TOOLBAR_ROW`, `PORTRAIT_TOOLBAR_HEIGHT`). Slider and Reset stand 20 units times `S` apart. Reset draws through
`button()` with that body height (`height` option) at its horizontal centre and width (156 px), so it is exactly as tall
as the slider beside it (owner, 2026-11-04); its caption keeps the 48 to 34 range, its label box 84 u high, and every
locale's caption is one line. Placement above the visible head, below the feet when blocked, inside the stage and clear
of the top bar and right controls uses this total height.
The memorial candidate Add and queue Remove still use `retainedButton` because their combined containing layout hits
the full-row stop above; their row/card/panel sizes, drag pitch, marker and hit boxes remain unchanged.

The shared roster toolbar lays out Group Alts, its two native segmented filters and its search/sort/order group as
centred rows. Groups stand 28 units apart; search/sort/order neighbours stand 20 units apart. Search and order share
150 x 116-unit bodies (the search prefab's own size) on the filters' native centre line. Sort keeps its own 116-unit
height. The segmented prefab keeps its adjoining slots, 104-unit height and its
existing caption fitting tiers. Group Alts' hit area follows the same 104-unit row and centre line.

Hall mode changes retain the previous complete frame until the target background, actors, material/FX textures and
first composite frame are ready. Portrait and current memorial sessions are retained separately; inactive sessions
pause animation, intro clocks, timers and audio. Returning resumes those sessions without restarting the intro.
Two bounded memorial renderer instances support the current scene and one hidden replacement. Editing or reordering
the playlist does not recreate an unchanged active source. Failed or obsolete staging cannot replace the current
scene or steal its input ownership. Modal redraws retain their committed content until visible thumbnails decode;
offscreen library entries remain lazy.

`student_hall.py` validates state stored under `student_hall` in UI preferences. Mode, background, portrait positions
and individual relative scales, boolean `mirror`, global `portraitScale`, entry order, memorial playlist and active entry persist. Missing global scale
defaults to 1; the API rejects non-finite or out-of-range values. Revision checks prevent a stale window from
silently overwriting another window's saved arrangement. Leaving the screen disposes its players, pending loads and
audio; returning loads the saved state. Dirty exits prompt before changing page or return history. Home resets that
history only once the root scene is committed. Async reveal, save and navigation guards reject stale owners.

`student-hall-dialogue.js` uses the ordinary `LobbyCommon/DialogPanel/ChatDialog/Talk` branch and its `Lobby_balloon`
sprite and original font. Text centers in the visible bubble body, excluding its tail and shadow, through the same
nine-slice geometry as the renderer. The bubble follows the selected actor instance, wraps complete text, and can
flip its tail near a stage edge. A Hall-wide turn lock ignores other portrait clicks throughout preparation and
vocal playback. Preparation has a 25-second cap, removed when playback starts; text-only or failed-audio fallback
lasts five seconds. `globallines.py` retains ordinary UILobby Idle groups alongside memorial dialogue
from the same downloaded table; entry greetings are excluded. Matching installed AudioClips take priority, with
Kivo as the existing fallback when local ordinary vocals are absent. No voice is written to disk. Bounded memory
caches coalesce concurrent fetches. Per-click aborts stop obsolete text/audio requests and prevent stale playback;
volume and mute changes also affect the active speaker. Pending first-click metadata retries without advancing the
selected dialogue turn again, and voice failure leaves available text readable. Missing dialogue is silent.
Authored expression clips use the ordinary lobby track metadata and timed dialogue continuation rows, including
Simplified Chinese text. Late metadata enriches an active vocal at its current time without restarting the voice
or balloon; its prior expression is restored when the turn ends.

The office sources are the game's layered NGUI prefabs `LobbyCommon` and `LobbyCommonNight`, using their
`UILobbyBackground_Day` and `UILobbyBackground_Night` branches. `gameassets/office.py` exposes the background branch,
its ancestor transforms and the installed texture layers. The integration uses the prefab's geometry and serves
native texture layers losslessly at their original resolution. It does not replace the office with a flattened
collection image. `/asset/office/<day|night>/layout.json` supplies the layer tree and texture routes.

The picker retains collection backgrounds alongside the available office choices. A saved available choice wins;
otherwise the daytime office is the default when present. Partial installations missing office resources fall back
to an available collection background. The inspected collection inventory contains 54 backgrounds, an observed
count rather than a fixed limit. Native material/shader pass metadata selects normal or additive composition;
night light uses the actual SrcAlpha/One pass in both the scene and picker. Texture URLs and cache keys track source,
material and shader revisions. Environmental cloud/light motion driven by office Animation/Tween components stays
static in this release; portrait and memorial-lobby animations retain their existing behavior. Day/night scenes,
previews and cancellation have been checked in the running source UI; the final local build also renders the
office background. No background, sprite,
preview or voice is shipped as part of this feature.

### Portrait

The detail screen frames the portrait like the game (`portrait-view.js`): the skeleton's origin sits on
UICharacterDetail's `CharacterPos` (`Inventory_ListControl/CharacterPos`, scale 101; a skeleton unit is 1/100 of a
Unity unit in every SkeletonDataAsset), never on the skeleton's bounds, so a long weapon, wings or a prop beside the
character neither shrink nor shift her. The player covers the stage under every panel and keeps one scale across and
down (its viewport has the canvas's own aspect).

**Visual mode** (`visual-mode.js`, framing in `visual-view.js`). The nameplate's square button with the rotate-phone
icon (`Char_ZoomBtn`, UICharacterDetail's `ZoomButton`; its click is wired in code, the prefab's `onClick` is empty)
opens the game's UICharacterVisualMode, a UI of its own. The identifiers in `global-metadata.dat` name what its code
does (`OnPressScreen`, `OnMoveSpine`, `PlayZoomInOut`, `co_ReturnToDefaultPosition`, `CheckResetButtonState`,
`OnClickHide`, `OnClickShow`, `UpdateGyro`, `SetScreenAutoRotation`); its prefab has a mobile group, drawn turned by
90 degrees for a phone held upright (hence the icon), and a PC group, which the page draws:

- The detail screen's UI slides out as in the lobby's fullscreen, and the portrait's own player (its face, its blink)
  glides from the detail framing into the whole art: the body in the middle at `Character/CharacterPos`'s scale (50,
  half the detail screen's 101; the tallest portrait, 3277 skeleton units, still fits the screen). The game reads the
  body's centre and the default, least and largest scales of each character from its CharacterIllustCoordinate table
  (`CharacterBodyCenterX/Y`, `DefaultScale`, `MinScale`, `MaxScale`), which the install keeps in the encrypted
  `ExcelDB.db`: here the centre is the skeleton's origin across and the middle of its bounds down, and the zoom goes
  from the whole art to 4 times closer (about twice the detail screen's size).
- `BG`: the collection background, 4533 x 3400 units, under two light patterns (`UITex_BGPoliLight_1` and `_4`)
  whose TweenAlpha ping-pongs between 0.2 and 0.5 every 1.25 s in opposite phase; it fades in over the detail screen's.
- `TouchArea`: press and drag (any button, one finger) moves the art, the stage's centre staying on it; the mouse
  wheel zooms at the pointer, a trackpad pinches and swipes (as in the lobby), two fingers pinch.
- The buttons, their corners anchored to the screen's edges, slide in with `UIAni_CharacterVisualMode_PC` (its
  Hermite keys sampled at 60 fps): `Btn_Back` (top left) and Escape leave, `Btn_HideUI` (top right) slides them out
  and `Btn_ShowUI` fades in in its place to bring them back, `Btn_RefreshPos` (bottom right, `UI_BUTTON_RESET_POS`)
  glides back to the whole art and is greyed out (`Btn_RefreshPos_Disable`) while it is shown. The background and the
  buttons are rendered off screen first, so every part starts in the same frame.
- Leaving glides back to the detail screen's framing exactly. Not reproduced: the phone's gyroscope (mobile only) and
  the button sounds. Without the prefab the portrait still goes fullscreen, with the tool's exit button.
- While any fullscreen view is open (this one, the lobby's, the 3D model's), the slid-out UI, the top bar and the
  navigation are inert (`uiOut`): Tab reaches only the view's own buttons (the tool's exit button in the others), and no
  hidden control acts. Leaving (`uiBack`) gives the focus back to the control that had it and always closes the
  visual mode, whatever tab the screen shows by then.

### Popups

The detail screen's popups (switch portrait, switch memorial lobby, the variant picker, compare, details, aliases) are
drawn on the game's popup frame (UIPopup_Sort) and cover the whole stage with its dark backdrop, top bar and
navigation included, like the display settings and the settings; a click on the backdrop or Escape closes them
(PopupClose). Every popup draws the same backdrop, `BACKDROP` in `ui-tokens.js`: black at 0.80, the value
`BlackBgClickArea`'s TweenAlpha (0 to 0.80 in 0.2 s) rests at in UIPopup_Sort, UIPopup_Option_PC and UIPopup_FilterSort
(the sprite's own 0.94 is not where it ends). The app shows it at once, without that fade.

**The shared popup module** (`popup.js`, tested in Node from `tests/test_popup.py` on a stand-in DOM). Every screen
draws its popups with it, and it reads its own prefabs once (`ready()`): UIPopup_Sort (required), the student list's
tools (UICharacterCollection) and the gacha list's checkbox (UIPopup_GachaCharList.Btn_Checkbox), the last two
optional, so a popup still works without them.

- **Hosts.** The detail screen's own, `#popup` and `#picker`, inside the screen; and two on the stage, so a popup
  shows on any screen: `#prompt` (z-index 2550) for list prompts and `#dialog` (z-index 2600) for message popups, both
  over the settings (2500). Each message popup is a layer of its own in `#dialog`, so one
  opened from another shows over it. Mod Alias (`mod-alias.js`) and the repair preview (`mod-compare.js`) are layers of
  their own in `#dialog` too (`.popup-layer`, registered on the stack with `el`): a failed save's message draws above
  Mod Alias, Escape closes the visible (topmost) layer, and closing one removes only its own layer, never the others in
  the host. The display settings (`#display`, 2500) opened while `#dialog` is up (Assign
  Character's Display Settings) get the `over` class (2700, like the scan consent's), so they draw over the dialog,
  take its clicks and are first on the stack; the class goes when they close.
- **The stack.** Every open popup is on one stack (`opened(host, close)`), and Escape goes to the topmost one only.
  A popup that must be answered with a button (no close button) takes Escape and does nothing with it, so nothing
  under it acts. A popup leaves the stack by itself when its host is hidden or its layer is gone. Escape is left
  alone while an input method composes (`isComposing`, `keyCode` 229) or when a control took the key itself
  (`defaultPrevented`). `anyOpen()` tells whether any popup is up (the stack, or the consent, update, settings and
  display hosts), and `whenIdle(every, blocked)` resolves once none is and the optional work predicate is false.
  The views under the popups leave Escape alone while `anyOpen()` or once a popup took it (`defaultPrevented`): the
  detail screen's fullscreen, the portrait's visual mode and the camera (whose capture-phase handler runs first) stay
  under a message such as a capture error until the next Escape. The settings (`#settings`) register with `opened` as they open and leave with `closed`, and wire the backdrop with
  `closable`: Escape and a click on the backdrop close them like every other popup, and a popup opened over them takes
  the key first.
- **Focus.** One shared trap for every popup on the stack (`opened` / `closed`, `trapKey` on the window). A popup takes
  the keyboard focus when it opens (its layer or host; a layer hidden while it loads, `loading`, when it is revealed), Tab
  and Shift+Tab stay inside the topmost popup and wrap at its first and last control (disabled, hidden, inert and
  undrawn controls are skipped), the focus is pulled back in when it is outside or lost to a redraw, and it returns to
  what had it before once the popup closes, unless the user has moved on or another popup is still open over it. A
  popup that must be answered with a button traps just the same. A control that took the key itself (`defaultPrevented`,
  the tour's own Tab), an input method composing and the modifier keys are left alone. The Hall modals and the import
  dialogs use it; the tour keeps its own three-button cycle.
- **Refusals and failures** (`notify`, `notice`; BL-045). An ordinary refusal, where the app declined an action because
  of a state or a rule and nothing broke, is the neutral notice: title Notice (`quit.title`), the line, Confirm only,
  no Failed and no Error Report. `REFUSALS` comes from the shared error registry's `level: "notice"` entries (27 names).
  Any other code,
  or none, is a real failure: title Failed and the Error Report button (`game_status_unavailable` and
  `profiles_unreadable` report something unreadable, so they stay failures). A greyed switch or Apply names its reason
  by its code, so the same rule decides. Failed notifications append the display reference to the message's last line
  using the locale's `popup.withCode` form; notices and badges keep their existing text. A refused import row's Details
  reason line uses the same form, including `mod_parts_missing` for incomplete rows. `errorText` remains unchanged.
  The classic modal and the Spine licence notice pass no `error`.
- **Buttons.** Every button of a popup is an invisible button over the drawn one, named for assistive technology by
  the caption it covers (`aria-label`) and marked with what it does (`data-action`). When a sprite is missing from
  the atlases its button is placed from the prefab node, so the popup can still be answered.
- **`popupFrame(title, w, h, {ok, cancel, close, buttons})`**: the shared frame builder, including lists and
  consents. It keeps `UIPopup_Sort`'s `Common_Popup_Bg`, title, close and footer branches, resizing and positioning them
  with `POPUP_FRAME`, `CONTROL.buttonL`, `TYPE` and `SPACE`. Width classes are S 1300 x 1050, M 1540 x 1073,
  L 2098 x 1110 and XL 2841 x 1353 (the sizes of their exemplars). A numeric width rounds up to the smallest class
  that holds it. The class sets the width; explicit heights fit content above `minH` 786, from `UIPopup_ClientVersion`
  `Center/Popup/Base/BG` (1115 x 786), capped at 1560. Scrolling viewers that omit height retain the exemplar height;
  scrolling lists explicitly keep at least XL's 1353. The frame remains centred 12 units below the stage centre.
  The title is one display line, 72 down to 48, with its top 36 below the frame top. The 60-unit close icon keeps
  48 from the right and 39 from the top. Content starts 170 below the top, with 40-unit panel/list side padding or
  100-unit text side padding. A body that fits centres vertically in that content box, above the footer clearance
  (or bottom padding without buttons), even where scrolling is allowed; a message body and check row centre as one
  block; only an overflowing body fills the content box and scrolls. Footer bodies are 551 x 176,
  33 apart, 102 above the bottom, centred as one group;
  three or more share a reduced width when necessary to keep at least 82.5 at either side. Captions share one size,
  72 down to 48, up to two lines in the body's width less 2 x 39.5. The recorded Thai footer exceptions retain their
  existing floors (36 for the recorded footer groups; named Hall allowances 44 and 36), never fitting smaller than
  their former 400-unit-box result. No other group gets an exception. The lowest content stays 20 above the footer.
  `footerY`, `footerTop`, `footerSlots`, `footerScale`, `footerGap`, `footerFit` and `footerLabels` expose the same
  geometry to callers; `footerScale` describes the equal width reduction while the bodies keep their control height.
  `popup-frames.js` holds the shared content boxes: text viewer XL, aliases and Mod Alias M, file details and Export L,
  and comparison/repair L. `POPUP` and `FOOTER` remain pinned to the old Sort anatomy for callers awaiting migration.
- **`message({title, lines, ok, cancel, check, close})`** in `#dialog`: lines of text, centred; when a line carries a
  small button (`{text, button: {label, fn}}`, the consent popup's light blue Change button), every line is a row of
  the game's light stats box with its button at the right. An optional checkbox (the gacha list's) under the lines.
  `plain: true` uses left-aligned lines without a box or separators; line buttons stay at the right, and adding one
  preserves the text's font size and fitting rules. `scroll: true` keeps the lines in a scrolling list between the
  title and the buttons; a message whose frame would pass 1560 units (the Add result of many Mods, the dropped queue
  list) scrolls automatically so its title, checkbox and buttons stay on the stage (`messageLayout().scroll`).
  Its default is S, or the smallest of S/M/L that fits each paragraph on at most six lines; longer copy uses L and
  scrolls as needed. Explicit message width 2100 selects L. `textItem` and `stack` measure and place paragraphs with
  a 28-unit gap; line buttons and the checkbox use centred rows with a 20-unit gap. Stats-box rows retain their own
  internal padding and row metrics.
  It resolves once it closes, however it closes, with `{button, checked}`: `button` is `ok`, `cancel`, or `null` for
  the close button, the backdrop and Escape; `checked` is the checkbox's state at that moment. `close: false` draws no
  close button, and the backdrop and Escape do nothing. The popup takes the keyboard focus while it is open (Enter
  presses nothing under it) and gives it back. `confirmKeys` (none by default; the quit prompt's Enter and Space)
  press OK while the popup is in front and holds the focus itself; a focused button takes its own key, and a held
  key's repeat, a modifier or an input method composing press nothing. Without the frame's prefab (no game folder)
  the same message is drawn plain: a light box on the dark backdrop with the same class, title, close, content,
  checkbox and footer boxes, marked and answered the same way. The log viewer and Spine consent reuse this fallback.
- **`listPopup({...})`**: the frame laid out like the game's quick formation edit, the selection's details on the
  left, the cards in a framed list on the right, the buttons under it. The student list's tools over the cards are
  optional (`tools`); without them a hint and a footer show in their place. One card chosen (`selected`), or several
  ticked (`multi`, with `selectable(items)` ticking all that can be, `toggled()` and `sizeOf()` for the ticked cards'
  total size). A card that cannot be ticked (`disabled`) can still be clicked; `dim` darkens it like the roster's
  not-owned cards with its picture in grey, and `badge` replaces In Use with the caller's word. Its XL content box
  uses a 4/8-column details/list grid and a centred shared footer. An optional checkbox occupies its own row below the details column,
  above the footer. Native card envelopes, aspects and scroll pitch are retained inside the card grid's viewport;
  chips and stats-box rows retain their prefab internals. Details use one measured stack, or a portrait/info row
  with stacked fields; content taller than its panel scrolls independently so the final field and actions remain
  reachable. There is no close button when `onClose` is `null`. Card buttons follow the list order in the tab sequence.
  A list with no card to show (a search or segment that matches nothing) puts `hall.noCandidates` in the list paper,
  centred on the visible paper. With `tools`, while the chosen card (`selected`) is not among the cards shown, the
  primary button is the game's disabled one (`aria-disabled`, answering nothing); a prompt without `tools` is never
  blocked. Escape in the search box closes and clears the search, as the roster's does, and the popup stays; Hall's
  pickers do the same (`student-hall-ui.js` `search(..., { onEscape })`). Hall's box is a search field without the
  browser's own clear button (hidden in `app.css`) and without the prefab's `Btn_Close`.

**Start sequence** (`main.js`, `Popup.sequence`). The whole first-run sequence (the cursor, the start popups, the
update check and the feature tour) is one function that runs once per session, when a game folder is known: at the
start, or, after a start without one, as soon as the folder is chosen on the no-game screen
(`refreshConfiguration` calls it after the first screen is shown; the consent and the loading screen are then given the
data of the game found), without a restart. A later refresh never starts it again. Once the first screen is revealed
the start popups come one at a time, in order, each only when it has something to show (`mod-switch.js startDue`): first,
when `app.info.dataDirWritable` is `false` (the data folder cannot be written), one notice with the quit dialog's title and
Confirm only (`webui.error.data_dir_readonly`, with the folder's path; the no-game screen shows it too, once per session);
then the asset consent, the Spine licence notice (only while no answer was ever stored; see Spine licence),
then the scan consent (while it is unanswered and a game folder is known; see Enable Mods switch). File checks require scan
consent: On checks at every start, Off withdraws consent and stops scans.
The Configuration Drift Check row's Check remains available while Off too: missing consent opens the scan consent
popup first, then Allow continues the manual check; declining cancels it. Check for Updates at Startup already
defaults to on for an unset preference; an explicitly saved false value remains off, without a migration resetting
it. In released builds it starts one background network check after the first screen is revealed, independently of
the popup sequence; turning it off skips that check. A slow
or failed check never holds startup, scan prompts or navigation. A newer release waits for the start popups to
finish, then for any open popup (including Settings), loading work, input hold, writer progress or queued mod toggle
to clear. Its offer can appear on any screen, including when the result arrives after startup. Manual Settings
update checks remain available and share an in-flight automatic check and its popup; their result callbacks still
run, so Settings closes for a newer release, and with none a notice says "Up to date" (`settings.update.none`; an
offline check or a failure is its error instead). `Update.check` resolves when its popup closes: `later` (Later or Escape), `page` (the download page
opened), `update` when a failed update is dismissed, `null` with no newer release, offline or if drawing fails.
Download progress and errors stay in the popup; its buttons and Escape are blocked during download. A verified
download shows Download Complete with Later / Restart. Later keeps it for this session; the next check for that
version opens ready to restart. Only Restart installs, and a refusal stays ready with an error and a retry. A
successful retry clears the failure, so Later / Escape then resolve as `later`. A step that fails is logged and the
next one comes. The prompts of mods back to official and of mods outside
the library come last if the start scan is already in. An unfinished scan stays in the background; its results
show once no other popup is open, including a tour. Each prompt appears when `startDue` says it is due:
only with the scan consent, never while the game runs, Steam is busy
or the classic UI is open, no drift while mods are off, nothing dismissed (see Mods already in use).

**Spine licence**

The start sequence's `spine` step follows asset consent. It shows `spine.license.title`, the legal wording in
`spine.license.notice`, and one centred Confirm (`picker.ok`) on `UIPopup_Sort`, built through `popup.js` by
`consent.js spineFrame`. It uses class L for width; the body wraps at the message font size (52 units), and the frame
fits its content above the shared 786-unit minimum or scrolls at the cap to keep the complete notice clear of the button.
Non-scrolling bodies centre between the title and footer; the asset and scan consents centre their section stacks in
the same content box. There is no close button; Escape and the backdrop do nothing. Closing the app window keeps its existing
quit behaviour. Without asset consent the plain popup uses exactly the same title, body and Confirm.

The notice says that continuing to use the software confirms that the user holds a Spine Editor licence.
Confirm sends one `app.spineConsent {granted: true}` RPC, saving `prefs.spine_consent = true` in `webui.json`.
Repeated clicks while saving send nothing; a failed save leaves the notice open to retry. No notice version is
stored or returned. `mod-switch.js startDue` requires this step only while `app.info.spineConsent` is null.
Earlier Yes and No answers never show the notice. On the first start, `UiState` migrates a stored No to true under
the shared state lock before `app.info` or the first screen reads it; an earlier Yes remains true. Unanswered
state stays null. Confirm updates `info.spineConsent` and calls `route()` to render the current screen again.

`spine-runtime.js ensureSpine` loads the runtime only after acknowledgement, including an earlier answer.
The first screen, built before the start popups, uses a static portrait where one exists, otherwise an empty preview. Detail, Hall,
comparison and compatibility paths show no licence notice or Confirm License action. Settings' General tab keeps
Spine License (`settings.spine.label`) as a value row with one View (`settings.spine.view`) button. View opens the
same notice with one Confirm and changes no state, including when its acknowledgement is still pending. There is
no withdrawal control.

- **Switch Portrait, Switch Memorial Lobby** (the switch card of the Portrait and Memorial Lobby tabs; the add button
  beside it adds mods of either kind): what the game can show for that kind (`switch-sources.js`, tested in Node):
  the variant's own files and its added portraits or lobbies; for the portrait also the other variants' own (those
  with one of their own), then the other variants' added ones. Other variants' lobbies and their added ones are not
  offered: another student's lobby under this variant's lobby prefab is untested in the game (the library itself
  still takes `variant:<key>` and other variants' mods for the lobby). Cards are framed on the face of each skeleton
  (a lobby's main skeleton, named like its core). Save always changes only the preview, independently of the
  application manifest. Preview sources persist per install and variant in `webui.json`; family and roster data
  expose them as `previewSources`. Apply, in the detail page's fourth action and the popup's lower-left actions,
  updates the manifest through `actions.apply`. With Mods on it also installs; with Mods off it only updates the
  intended source for the next enable. Applied marks this manifest choice, independently of the physical files.
  Set and In Use are not shown. Student-list cards have no application-status banners; their band shows Mod counts only.
  Applying the manifest's current choice is disabled using the game's grey button
  treatment, without a dark cover over the card. A lobby popup offers only the lobby mods of its own core
  (a mod of an alternate core, `_home_gl`, is switched by Enable Mods only).
  Candidate rows start 40 NGUI units inside the list paper, measured from their inclined outer corners; short rows
  stay left-aligned. Portraits retain their tall cards in one horizontal row, with drag, horizontal trackpad input
  and vertical wheel input mapped to horizontal scrolling. Search occupies the header so the row keeps its full
  height, with a fixed 40-unit gap (`SPACE.xl`) between portrait bodies. Memorial lobbies use larger 790 by 444.375 cards in
  two columns and models keep four, scrolling vertically. The horizontal track and thumb
  turn the complete `UICharacterCollection/SeletControl/List/Scroll Bar` prefab; the existing `UIPopup_Sort` frame,
  card sprites and `UINonEquipmentInventory/Inventory_ListControl/EquipPos/Texture` detail image stay intact.
  Selection and rename redraws retain the active axis offset. Captures start when a card enters the viewport or
  its details are shown, and both share one still; closing the popup cancels deferred work.
  On an overwritten kind (a file that is not official with no official copy anywhere, `restorable` false), Original
  is greyed with the existing Overwritten treatment. Library Mods remain selectable and applicable; another
  variant's official card is greyed when that source has no official copy. The greyed cards can still be selected
  for preview; Apply is disabled. `original_unavailable` opens the Add First or Overwritten message
  from an apply or a restore. Delete, in a mod's details, always confirms with the mod's name. If configured or
  applied anywhere, it explains that those entries return to Official or their saved source. The library writer
  deletes stored files and references, retaining built files referenced by other keys, then restores affected game
  entries through the normal write checks. Refused restores leave the configuration changed and appear in Retry.
  The picker and file drops compare canonical destination keys with the selected variant when the add started.
  Plain additions there refresh with no message unless one was installed, failed, kept outside files or is
  incomplete; additions elsewhere name their destination and offer Go to.
  Settings' Other Mods uses the same preservation notice; its Official action is disabled while the slot shows
  No official copy left. A refused restore there uses the existing overwritten message and repair lines.
  Duplicates show Already in the Mod Library with the stored mod's name, with Go to only for another variant.
  Exact outside game content is adopted without reapplying it and shows Recognized as an installed Mod, including
  when it matches an existing library entry. A batch combines these messages in one popup, with navigation only
  for other variants; local results alone have a confirmation button. The source already in use has Apply greyed
  and shows no message, and no job runs. Row 2's
  update and compare act on the mod the shown tab's files carry. The 3D tab's switch lists Mods from all of its own
  model slot cores, with one saved choice for the complete slot.

- **New mod marks**: unseen mods mark their own variant chip, their kind's detail tab and the roster card (any
  variant when merged). A visit or a tab change keeps the marks. Opening a switch popup marks all mods it offers
  as seen, including other variants' portrait mods; opening a mod card also marks that mod. Other kinds keep their
  marks. Seen IDs live in `webui.json`, through `mods.seen`, independently of the library's legacy fresh flags.
  Mods without a seen ID start new; adding an existing duplicate keeps its seen state.

- **Aliases** (the pencil after the name on the nameplate, the game's name edit icon `Cafe_Icon_NameEdit`): the
  user's own aliases of the student, which the roster search matches (several, at most 8 of up to 24 characters;
  kept with `aliases.set` in `webui.json`). One row each, drawn like the game's input row (UIPopup_Input's
  Input_NickName: a grey box with a line round it, the text and placeholder colours), its cross removing it, then an
  empty row with the pencil to add one. Enter in the empty row adds what it holds, in another row goes to the next
  one; OK keeps the rows as edited, Cancel, the close button, the backdrop or Escape drop the changes. Nothing
  happens on Enter or Escape while an input method is composing (`isComposing`, or `keyCode` 229, which WebKit sends
  for the key that ends a conversion). The nameplate shows them after the name, small and dim, where the name leaves
  room; the pencil's accessible name includes them. They are text everywhere, never markup.

- **Details** (the Portrait, Memorial Lobby and 3D Model info bars' button): the shown tab's game files. The folder,
  what they carry and, for a mod, its file and the variant it was made for; then one row per bundle with its size,
  date, CRC state and what it carries. The frame fits its content and scrolls past the stage's height.

### Bar motion

`chrome-motion.js` moves the top bar and the bottom menu as the game's lobby moves them: UILobby's legacy Animation clip
`UIAni_Lobby` on the prefab's root, `Front/NUITop/Top/Right` and `Front/Bottom/Menu` (`LOBBY_ENTRY` below). Offsets are
viewport pixels away from the screen, set as the CSS `translate` property (it composes with the native toolbar's `scale`
transform and the corner controls' own `translate`): on `.window-header` with the native toolbar, else on `.topbar` and
`.nav` in stage pixels. A bar that has left gets `chrome-hide-top` / `chrome-hide-bottom` on the root (hidden, no pointer).

- **Entry.** Both bars wait where the clip starts (333.7 and 1043 units away) until `main.js` `boot()` has shown the first
  screen, a recovery page included, then come in on the clip's Hermite curves (0.42 s). A first screen that never reports
  lets them in after 30 s.
- **Immersive scenes** (`window-layout.js` `isImmersive`: detail and Hall fullscreen, photography) send both bars away, as
  far as their painted edges (the hanging Back art included) plus 8 px. A pointer within 16 px of the window's top or bottom
  edge brings that edge's bar back on the same curve, usable (Hall and detail fullscreen hold the bars `inert`; a revealed
  bar is not, a leaving one gets their state back). It leaves 500 ms after the pointer was last off it; leaving the window
  over a bar keeps it. The scenes' own controls make room: `corner-layout.js` places top corners under the revealed bar and
  bottom corners above the revealed menu (`bamtChromeInsets`), and `bindEdge` moves a centred edge control (the
  photography shutter) as far.
- **macOS window buttons.** With the native toolbar outside macOS fullscreen, each top bar motion goes to `window.motion` as
  offsets at 60 Hz; `CocoaHost.slide` plays them on the buttons and hides them once wholly above the window. A layout pass
  aligns them again and keeps the offset; in macOS fullscreen AppKit owns them, and they get back only what the motion
  changed. While the top bar is away for its entry the whole row drags the window; an immersive scene's away bar has no
  drag regions, a revealed one drags and clicks as anywhere else.

### Design tokens

`web/js/ui-tokens.js` holds the numbers and colours the web UI shares, each named after the prefab node it comes from; a
call site imports it, nothing else repeats them (`tests/js/ui_tokens.test.mjs` pins the sites, `tests/test_ui_tokens.py`
the prefab values, from the development view). It imports no other UI module.

- **Popup frame, footer, backdrop**: see Popups above (`POPUP_FRAME`, `CONTROL.buttonL`, `TYPE`, `SPACE`, `BACKDROP`);
  `POPUP` and `FOOTER` retain the old anatomy for unmigrated callers.
- **Lobby entry** (`LOBBY_ENTRY`; UILobby's `UIAni_Lobby`): the top bar's and the bottom menu's unweighted Hermite keys,
  `[time, offset from rest, slope]`: 333.7 / 28.55 / 0 and -1043 / -29.52 / 0 units at 0, 0.2 and 0.42 s. See Bar motion.
- **Small button** (`SMALL_BUTTON`; UIPopup_Option_PC `Center/Popup/List/Sound_Setting/ETCSetting/ButtonBg`): 402 x 136, caption
  48 down to 34 fitted to the width less 60, its label box 30 less across and 20 less down; only the width varies, 240 at
  least. `game-buttons.js` `optionButton` takes these as its defaults; Hall `button()` also uses them, retaining each
  caller's centre and width (raised to 240 u if necessary). The stopped memorial sites use `retainedButton`. Settings'
  value rows
  put the buttons right-aligned to the card's content box (the 1552-wide card less its 58 padding, so to x 718), 422 apart,
  beside the value while it keeps at least four grid columns (452; two buttons to a line) and under it three to a line; the
  rest wrap to a second line, right-aligned like the first (`settings-rows.js` `valueGeometry`, laid out with the design
  system's `container`, `row` and `stack`). The Export pair is 680 x 136, 60 apart and centred. A message's line button is 136 high and as wide as its caption plus
  90 (240 at least) in a row at least 156 high; the asset consent's Change button the same, on a line of its own under
  the item (`consent.js` `assetLayout`). Not migrated yet: the stopped memorial candidate, queue and footer buttons,
  the photography toolbar and the detail screen's Show in Finder / Explorer button. Popup detail actions use the
  136-unit control height and retain their existing 40–26 caption range for the narrow shared action row.
- **Caption exceptions** (`CAPTION_EXCEPTIONS`, checked by `captionMinimum`): only Thai at Hall's Done Adding (44),
  unsaved-changes footer group (36) and portrait Reset (30). The footer/small-button token minima stay 48/34.
- **Batch Import's row under the list** is one row of `buttonS` controls (136 high, caption 48 down to 34, `SPACE.m` apart, laid
  out with `row`): Advanced Options, Recheck and Import share one body height and one caption size, on the prefabs' own bodies
  (Mailbox `Btn_Receive` and `ReceiveAll/Btn`, the popups' gold). Import stands out by its gold, not by its size. The row's right
  edge is the list tools'. The tools over it fill the list's width in one row `SPACE.m` apart, each in the share of it its caption
  needs, `SPACE.m` under the list. Advanced Options is the L popup frame (`POPUP_FRAME.L`, 2098 wide) with its option groups in
  a container padded `SPACE.xl`, `SPACE.l` apart (`stack`); the gaps are prefab units drawn at the stage scale, as the buttons.
- **Batch Import's list** starts 12 under the grey paper's top edge, about 2.7 rows at a time. The count once above it is gone: the
  filter button says it. A grouped row reads like an ungrouped one: its kinds as text on the metadata line, its states on the
  status tag. The left column's name plate ends where the list does; the filter popup keeps the frame it had.
- **Chip** (`CHIP`; UICharacterDetail `Char_Target/Type/MainType/BG`): 272 x 72 inclined 10 degrees, caption 40 down to 28,
  290 apart. The nameplate's variant chips (`detail.js`) and the list popup's details panel (`popup.js`) draw it. In the
  panel the chips stand in one centred row (three, the most a card has, about 283 apart so they stay inside it:
  `chipPlaces`), 16 above the fields box; the portrait panel keeps its one row of
  chips sized to its half (`switch-layout.js`), 16 above its box.
- **Light stats box** (`STATS_BOX`; UICharacterDetail `Inventory_ListControl/RightInfo/Target/Active/1_NormalInfo/Status`: `Bg`
  `#ecf5f9`, its `Deco` line `#c0c9d4` and the `Bar` divider): drawn by `statsBox()` in `web/js/stats-box.js` (pure
  layout: nodes and height), for the list popup's details panel, the file details, the messages with a line button and the
  update dialog. The line is the fill plus 5 across and down (the prefab's Deco). The box is the holder's width less 200 in
  a popup frame (messages, update), less 40 in a panel (the list popup's details) and less the line's 5 over a table (the file
  details, so its outer edge is the table's); captions 38, values 38 down to 26 on up to three
  lines, wrapped and never cut; a row 64 high at least, 12 of padding above and below, 30 in from each side, dotted
  dividers between rows only. The prefab's own rows are 82 high with no padding and its captions 42, so those are the
  app's. A message's rows take the same text range (a row with a button stays 156 high). The update dialog's box starts where a message's lines do (150 under the frame's top), and its frame is as
  high as its box, Status and the progress bar need, `POPUP.clearance` above the buttons (`update.js` `frameFor()`,
  `FRAME.h`). The portrait panel (`switch-layout.js` `splitPortraitPanel`) still lays out its own half-width box and takes
  only the line's outset from the token.
- **Colours** (`COLOUR`), each from the game: success `#0a6f2d` (the Formation popup's selected tag), warning `#f14f00`
  (the mailbox limit label), error `#bb3421` (the skill type sprite), greyed `#828282` (Btn_Disable's label), the page's ink
  `#2c4564` (the collection's top bar title; `--navy-text` repeats it), the In Use badge `#ffb700` on `#4b2116`, a note's
  ink and the consent's "never" bar `#6a7b8f`. The style sheets repeat the CSS ones, which the token tests keep equal.
- **Hall selection gold** (`APPLIED_SELECTION_GOLD`): `UIPopup_Mulligan/Center/Popup/01/UIMulliganSlot/SelectLine`,
  the applied frame's single UISprite yellow, `#ffe900`.
- **Corners, focus ring, fallback button** (style sheets): the game's ladder, Raius5px, Raius10px and Raius20px (5, 10 and
  22 units) is 3.24, 6.49 and 14.27 px at the stage's scale (`RADIUS_PX`). One focus rule in `app.css` draws the ring for
  every control, 4 px `#2fa6f0`, 2 px off, radius 6.49 px (a roster card's keeps its -3 px offset because the list clips; no
  glow). `.plain-popup` is 14.27 px, `.plain-line-button` and the plain checkbox 6.49 px. The no-game screens have one flat
  button look, `#8de4ff` with `#314665` ink on a 6.49 px corner, no skew and no shadow (`.message-button`, the recovery
  panel's buttons and `.plain-line-button`). The photography sheet is unchanged; the lobby dialogue uses ChatDialog balloons.
- **Settings rows** (`UIPopup_Option_PC` cards) are laid out in the card's container (`settings-rows.js` `cardContent`): 1552
  wide, padding 58 at the sides, the title 17 under the top, the dotted line 109 down, the content from 20 under the line to 28
  above the foot. Radio groups keep the prefab's 508 pitch and share the content box when longer. The cards stand 12 units apart
  (`PANEL.stackGap`, the prefab's Game tab) in the panel's list area padded by 20 (`stackCards`); the scrolling area ends at
  the last card's foot plus that padding. A value is a caption (`TYPE.caption`).

### Enable Mods switch

One kill switch for every game folder (`mod-switch.js`; the pure parts tested in Node from `tests/test_mod_switch.py`).
It never changes the configuration: Off puts every portrait and memorial lobby of every known install back to
official, On writes the configured sources again (see library). It is in the top bar of the student list and the
detail screen, and in Settings' Mods tab.

- **The top bar** (`#mod-switch`, after the title): the switch, its label and the square "?". All CSS on the game's
  sprites, like the rest of the top bar. The atlases have no two-state switch sprite (`toggle|switch|onoff` find
  none), so the switch is two of the student list filter's segments (UICharacterCollection `FilterBtn`:
  `Common_Filter_Tab_Type1` and its flipped twin, 9-sliced and inclined 10 degrees as NGUI draws them, at 40/104 of
  their size): ON pressed in the Support segment's blue (`#0664eb`), OFF pressed in the All segment's navy
  (`#153757`), on the pressed sprite `Common_Filter_Tab_Type1_Pressed`. The words are `settings.post.on` and `.off`
  (the game's On / Off). The "?" is the student list's own square info button (`Btn_Information`:
  `Common_Btn_Information` in `#2c4564` over a white square), its accessible name the game's "Guide". The label is
  fitted from 26 down to 20 stage px. It slides out with the top bar in fullscreen views, and is not on the no-game
  screen.
- **States** (`view(mods.state)`): on; off; busy while a toggle is queued or running or writes wait for the classic
  window (dimmed at the loading indicator's pace); unavailable, greyed with the reason as its accessible description (the game
  running or its state unknown, Steam busy, the classic window open, no game folder known, unreadable mod settings);
  the red dot (`Common_Icon_Reddot2`) while entries were not switched or were left as is. It is a `button` with
  `role="switch"` and `aria-checked`; Space and Enter ask for the other state. It always appears on the main
  screens with a current install (Settings keeps it without one).
- **A click** (`click(from, want)`, the same rules from the top bar and Settings; `want` is the segment or option
  clicked, `clickedState` in the top bar, none for Space and Enter): on the current state, nothing, in every state;
  otherwise busy, a notice (`error.busy`), or the
  classic modal with its box ticked when writes wait for the classic window; unavailable, the reason as a notice (the
  Failed popup when it is no refusal); the
  first toggle ever, the explanation; the classic window open, the confirmation, then the classic modal; otherwise the
  confirmation, then `mods.toggle {enabled: !current, expect: current}`, with the corner indicator and "Working
  12/40". The confirmation (`confirmation(state)`, message popup: title `quit.title` Notice, the line `mods.offAsk`
  "Turn off mods?" or `mods.onAsk` "Turn on mods?", Cancel and Turn Off Mods or Turn On Mods) comes before every
  toggle from the switch; only its button toggles. The tray's Enable Mods and the untracked prompt's Add and Turn On
  Mods toggle without it. The explanation (title and
  button Turn Off Mods or Turn On Mods, the help's opening text and "Not shown again", and Cancel) shows once, in
  place of the confirmation: closing it in any way calls `mods.explained`, and only its button toggles. The end
  is silent (the switch shows the state), or, when entries were added to the library first, failed, were left as is or
  their adoption failed, a result popup: "Added to the library first: n", "Done: x, failed: y", "Left as is, no
  official copy: n", then each failed entry ("Airi, Portrait - ..."), the first six named and the rest counted.
  `state_changed` (switched elsewhere first) is a notice, and the switch redraws from `mods.state`.
- **The "?" help** (message popup, title Enable Mods): two opening lines (`mods.helpOn`, `mods.helpOff`): On applies
  configured mods to all portraits and memorial lobbies; Off restores official files where available and keeps mods
  in the library; close the game before toggling. Both opening lines use the smaller of their fitted font sizes,
  at least the popup's minimum (44 units), and wrap together at that size; the first toggle's explanation uses the
  same fitting. Both use plain, left-aligned lines with no box or separators, with or without scan consent or action
  rows. No storage explanation or size request appears in the help. "Game folders: ..." shows the install labels
  when more than one game folder is known; "Not switched: n" with Retry (the
  same toggle again) for pending and left entries; "Left as is, no official copy: n" with Details, the Overwritten
  message (the entries, known from the last scan with the scan consent, "No official copy left" and the repair lines
  of `repairs.js`, see Mods already in use; never a repair button); "Gone from the library: n". The counts are the
  services': the last scan's with the scan consent, the unfinished list's by why without it. Retry and Details stay
  at their line's right end. There is no scan-off row or Allow Check button; file-check consent stays in Settings.
- **The scan consent popup** (`consent.js scan`, built like the asset consent): Check Game Files; This app will:
  read Steam's install records, update status and file lists on this device, to find the game folder and identify
  changed game files; check whether each file applied in the game matches the configuration, find mods outside the
  mod library and files with no official backup; This app will not: read whole game files, change Steam's files, or
  send anything off this device (`mods.scan.will.{label,steam,items}`, `mods.scan.willNot.{label,items}`); the note
  that declining keeps these off, the game folder is chosen by hand and Settings changes it; Don't Allow and Allow (`mods.consent`). It must be
  answered (Escape and the backdrop do nothing), declining does not quit, and opened from Settings it shows over it
  (`#consent.over`). A failed answer (`mods.consent`, and the asset consent's `app.consent`) shows its error with the
  popup still up to answer again; the asset consent sends one answer at a time, a repeat click while the server
  answers sends nothing.
- **Settings' Mods tab** (second, after General; `UIPopup_Option_PC`'s tab column has five separators): Enable Mods
  (On / Off, the same switch with the same rules, its square "?" and red dot after the title; faded while busy or
  unavailable); Mod Cache (mods rebuilt for the current game version and
  kept for instant re-enabling; size requested with `mods.sizes {kind: "built"}` when the row is drawn, loading text
  while waiting, Clear enabled for a nonzero size; `mods.clearBuilt`, then "Mod cache cleared" and the size measured
  again); Tray Icon or Menu Bar Icon (`tray`, only in the app window where the system has one); Check for Configuration
  Drift and Untracked Mods at Start (the standard two-option On / Off radio row), directly above Configuration Drift
  Check. On opens the consent popup before enabling checks, then scans immediately, at every start and on the automatic
  triggers described above; declining leaves Off selected. Off withdraws consent and stops scans. `scan_consent` is the
  sole stored preference. Legacy `startup_checks` is removed on load: At Startup stays On, Manual becomes Off because it
  did not authorize automatic checks, and unanswered consent stays unanswered. Configuration Drift Check shows what the
  last check found, "No drift found", or "File check off". Its Check button stays available with automatic checks Off:
  without consent it opens the scan consent popup first, then Allow continues the manual check (and enables automatic
  checks); declining cancels it. Check scans with the dismissed entries listed again; its square "?" gives one
  definition: configuration drift means the game files do not match the mod configuration. There is no Game Folders
  row in this tab. The tab follows `mods.state` while it is open. Ask Before Opening Unmigrated Features (`classicPrompt`)
  is a General row, under More Settings. The Folders tab's game folder, Mod Library and Classic Tools Output Folder rows use Change
  for their folder pickers.
- **The classic modal** (`classicModal`, decision 5): a write the open classic window refuses (`classic_open` from
  the switch, Retry, or the detail screen's apply and restore, at submit or from the job) opens "Classic UI Open",
  "Close the classic UI first", the box "Apply automatically once it closes" (unticked), Bring to Front and Confirm.
  Any way of closing follows the box: ticked, the call is queued (`classic.queue`, "Applies once the classic UI
  closes"); unticked, nothing. Opened on writes already queued (a click on the busy switch), the box starts ticked,
  and unticked the queue is emptied (`classic.clearQueue`). Bring to Front raises the classic window
  (`classic.front`). When the queue has run (`classic.queueDone`), one popup ("Not Applied") lists the dropped calls
  with their error strings, and a queued toggle shows its result as above.
- **What a writer job switched** (`library.changed {cores, kinds}`, `main.js`): the roster is read again (rebuilt at
  once when it shows, once no popup is open), the thumbnails under the live and official `spinePrefix(kind, core)`
  paths are forgotten (including the `<core>@<id>` bases of other installs; `thumbs.js forget` matches any case,
  the cores coming in lower case), and the detail screen
  reloads (`Detail.filesChanged`). It reads the family again and, when the shown variant's files of the shown kind
  changed, reloads the portrait or the lobby the way an applied one is (under a still of the old one). It waits
  while a popup or a fullscreen view is open, while the screen changes, and while its own job runs (whose reload
  comes first, so the same change is not read twice). Without cores (the classic window closed, a job stopped
  midway, the mod list changed) the screen compares what it shows with the family read again, and the roster forgets
  the portraits whose Replaced flag changed. A job that switched nothing (a prune) changes nothing on the page.
  The screen's own reads of `family.get` (after a switch, a reload, a change) have no client deadline either: they wait
  for a job that holds the library lock while the screen stays as it is, and a read that finishes after the screen was
  read anew (left and entered again, or reloaded by another action) is dropped; a change read that way is read again.
- **The window back in front** after 30 s or more asks for a scan (`mods.scan`, only with scan consent), so a
  Steam check or a repair made meanwhile is found.
- **While mods are off** an apply only saves the choice (the apply job's `deferred`): the switch popup reads Save and
  starts on the configured source; no notice follows, as for any saved choice (owner, 2026-11-08).
- **The tray's events** (see Tray): `tray.toggle` is followed like the page's own toggle, `tray.open` shows the first
  toggle's explanation (its button submits the tray's toggle), the tray's refusal as a notice (the tray never queues
  for the classic window) or the drift prompt. The page listens for them on the no-game screen too.

### Mods already in use

Item 1's prompts and messages (`prompts.js`; the pure parts tested in Node from `tests/test_mod_switch.py`). The
lists come from the last scan (`mods.entries`), so the prompts exist only with the scan consent; the messages of an
overwritten entry need none (the detail screen judges its files cheaply).

- **Mods Not in the Library** (`#prompt`): the `untracked` entries and the configured `changed` ones of every known
  install, every card ticked. A card: the entry's own install's picture (`spineBase(kind, core, id)`, so a card of
  another install never shows the current install's file of the same name), the variant's name, the kind chip (an
  alternate lobby core adds its suffix, "Memorial Lobby gl"), the install chip when more than one install is known,
  and "No Official Copy" on an overwritten entry; its details: File, Size, CRC state and Official Copy (Kept, Backup,
  Other Folder, No Official Copy). The footer counts the ticked cards and their size, the hint says they are lost to a
  game update or file check unless added. Add to Library (`mods.adopt`), with mods off also Add and Turn On Mods (then
  the toggle, with the first toggle's explanation), and Later (also the close button, the backdrop and Escape); Don't
  Ask Again dismisses the cards not added (`mods.dismiss untracked`). Damaged entries are listed apart, dim and never
  ticked, their Details giving the repair lines.
- **Mods Back to Official** (while mods are on): the `reset` and `updated` entries, every card ticked, with the install
  chip; entries still overwritten are listed apart ("Still overwritten: n", their Details the repair lines). Exactly
  two buttons (decision 16) and no way out but them (no close button; Escape and the backdrop do nothing): Apply Mod
  Configuration sends the ticked cards (`mods.restoreDrift`) and keeps the others as they are, Keep as Is keeps them
  all (`mods.dismiss drift`). Apply is greyed with the reason while the game runs or Steam is busy; while the classic
  UI is open it opens the classic modal. Its result is silent, or a popup naming the failed entries and those skipped
  because their files changed since the scan ("Game files changed, check again").
- **When they show.** At the start (see Popups, "Start sequence"); after a later scan (the window back in front, the
  classic window closed, a game folder change) once no other popup is open, when the scan found entries the prompt
  has not shown in that state; after Settings' Check, with the dismissed entries again; and the drift prompt from the
  tray's Back to Official. Never while the game runs, Steam is busy or the classic UI is open.
- **Add First** (an overwritten entry whose mod is not in the library: no library source carries its non-official
  parts): Official File Overwritten, "No official copy left", "Add before any repair", Add to Library (adopts that
  entry) and Cancel. It names no repair: a repair would replace the only copy of the mod.
- **Overwritten** (once its mod is in the library): Official File Overwritten, "No official copy left" and the repair
  lines of its install (`repairs.js`): for an install in a Steam library Steam's verify (Properties - Installed Files -
  Verify integrity of game files), the settled repair; then, as another option and never promised, the game's own
  Restore Resources ("may not replace mods": whether it replaces a CRC-fixed mod is not verified); then "Then start
  this app again". The app never starts a repair (decision 9).

### Loading screen

`loading.js` draws the game's own loading screen: UILoading's black backdrop with nothing in the middle (the game
shows an illustration there; the tool does not) and UIRepeater in the bottom-right corner ("Now Loading", the
hopping dots and Arona turning round, animated in `app.css` from the keyframes of the clip `UIAni_Repeat 1`; its
UIAudioPlay is never played). Before asset consent, covers use neutral system text, a grey
background and no game layouts, sprites or fonts. The server selects the initial document's style from consent and
the current resource version's disk cache, so a cached launch is stylized before modules or RPC replies arrive.
`app.info.uiCached` reports the same cache state. A cold cache upgrades after consent and successful
UIRepeater decode; authorized covers use only that actual prefab. Waiting for sprites or a decode failure never
revives a generated text-and-dots substitute. All web controls use accessible names and descriptions;
none creates a hover tooltip.

- `scene(fn)` covers startup, roster/detail navigation, student steps and detail tabs: a fade to black (150 ms),
  then, while the work goes on, the indicator. It warns after 15 s and stays covered until preparation settles or
  navigation supersedes it; individual asset loaders have failure deadlines. The reveal fades out over 300 ms.
  Opening a student waits for `family.get` without a client deadline, as the list waits for `roster.list`: a writer
  job (an apply, an export) can hold the library lock for its whole run. A later navigation supersedes the wait, and
  the late answer is dropped without a reveal.
  `wait(promise)` shows the indicator alone over the screen after 300 ms (at least 500 ms) for other waits; its default
  cap is 15 s. One page overlay at a time: a newer call takes over the one on screen.
  `wait(promise, {label, cap})` names the wait for assistive technology (the classic UI's start: "Opening the classic
  UI") and stays up to `cap` instead (32 s for that start, which `app.openClassic` waits up to 30 s for).
- The detail screen resolves `show()` only when it is ready: the panels, the left side's first frame (the portrait,
  or the lobby with its still, or the 3D model with its shaders compiled), visible thumbnails and decoded UI images;
  the lobby's lines from Kivo get 1.5 s. A change inside the screen (a variant chip, the arrows, a tab, an
  applied portrait) renders every part off screen and swaps them in one frame. The left side loads laid out but
  transparent; old layers and stills are retired while covered, without a second fade during reveal.
- Writer tasks own a separate indicator and lifetime. Loading stays bottom-right in both styles; only the action
  label and x/n progress use the bottom-left corner.
- The start popups (the consent popup first) come after the first screen is revealed (Popups, "Start sequence").
- Startup phase durations are recorded in the session log and `app.timings`.

### Pointer and touch effect

`cursor.js` gives the page the PC client's own pointer and the touch effect it shows on every press; `touch-fx.js`
simulates the effect (pure functions, tested in Node from `tests/test_touch_fx.py`). Both read their textures through
the light texture route (`gameassets` indexes the GLPC_Cursor, shared UI and EffectsFreezed texture bundles for them,
in `pointer-textures`); without a game the system cursor stays and there is no effect.

- **Pointer.** `ControllerMouse.SetPCCursorStyle` sets `PCIcon_MousePoint` (64 x 64; its MousePoint option chooses
  this or the system arrow) with `Cursor.SetCursor`, hotspot (0, 0) and `CursorMode.Auto`, which Windows shows at the
  system cursor size: here a 32 CSS px cursor with the texture as its 2x image (`image-set`, `-webkit-image-set` or a
  plain `url` by `CSS.supports`), set as `--cursor` and `--cursor-press` on the root. The game has no hover or pressed
  pointer, so buttons show it too; drag handles keep `grab` and `grabbing`, text fields the text cursor. `Cursor_1`
  (the halo crosshair, hotspot in its middle, a software cursor) is the controller's pointer and is not used.
- **Pointer in the window.** pywebview's `text_select=False` (its default) appends `body { user-select: none; cursor:
  default }` to the page after `app.css`, which left the game's pointer on the buttons only and the system arrow on
  everything else (the portrait, the lobby, the panels): the window is created with `text_select=True` and `app.css`
  turns off selection on `body` itself.
- **Window edges.** WebKit (WKWebView) drops a cursor image whose box would cross the window's right or bottom edge
  and shows the system arrow (`Style::Cursor::selectImageCursor`); Chromium (WebView2) takes any cursor up to 32 px.
  The image is cut to the texture's opaque part (23 x 31 CSS px), and smaller cuts follow in the list, 4 px apart and
  then 1 px apart below 4 px: narrower for the right edge, shorter for the bottom, squares for the corner. WebKit shows
  the first that fits, so the pointer is cut at the edge and no strip of the window is left to the arrow. The images are
  object URLs, which keep the value short (every button resolves it).
- **Touch effect.** `Resources/UI/FX_Touch`, spawned by TouchEffectCreater at the pointer through the UI camera
  (orthographic size 1: the stage is 2 units high), dragged while the button is held and stopped 60 frames after the
  release. Its parts and numbers come from the prefab's ParticleSystem and TrailRenderer typetrees and its materials'
  compiled shaders: two spinning rings cut by a dissolve threshold over `FX_TEX_Grad_Ring3`, a flash disc
  (`FX_TEX_Circle_01`, its colour added, its alpha hiding what is behind), twinkling triangles
  (`FX_TEX_Triangle_02_1`) leaving the press and 5 per unit of drag, and a 0.3 s trail 0.005 units wide
  (`FX_TEX_Trail_03`). Colours are worked out in linear light as in the game and saturate where it does.
- **Drawing.** Two canvases over the whole window (`pointer-events: none`, device pixel ratio aware, hidden with no
  frame loop and no bitmap while nothing animates): the light on one screened over the page, what the flash hides on a
  multiplied one under it. The page blends in sRGB values, so the flash's blend is fitted to be exact over black and
  over white.
- **Reduced motion** (`prefers-reduced-motion: reduce`): a press keeps its burst, a drag leaves no trail and no
  triangles.

### Memorial lobby

`lobby.js` frames and plays the lobby, `lobby-touch.js` adds the game's touch interaction in fullscreen.

- **Linear light** (`linear.js`). The game renders in Unity's linear colour space (PlayerSettings
  `m_ActiveColorSpace` 1) with sRGB atlas textures, so its blending happens in linear light: an additive glow over a
  bright scene adds little, a soft veil over a dark one adds more. Each lobby player samples its atlas pages as sRGB
  and blends into an sRGB framebuffer (a lobby's skeletons all into the bottom player's, below), which is copied to
  the canvas, encoded again; slot colours reach each atlas page's shader (lobby.json layer `shaders`) as that shader
  takes them (`slot-colours.js`, read from the compiled vertex programs): spine-unity's `Spine/Skeleton` decodes them
  to linear with UnityCG's cubic fit of the sRGB curve (an additive slot at alpha 0.42 adds 0.15 of its texture), the
  game's own `Spine/Skeleton MX Standard`, on the pages of 97 lobbies, uses them as they are (0.42), and `Spine/Skeleton
  MX Portrait Noise` (CH0310's game screen) reads only their alpha.
  Without WebGL2 the player draws in plain sRGB values as before. Drawn in sRGB values, glows such as Kurumi's photo
  booth light or Niko's window washed the scene out to white.
- **Draw order** (`lobby-stage.js`, `skeleton-parts.js`). spine-unity gives each submesh of a skeleton the material
  of its atlas page, and Unity sorts every submesh and particle renderer by sorting order, render queue, then back to
  front, whichever renderer it belongs to. A skeleton whose pages sit in several queues (the scenery at 2900, the
  character at 3050) draws in one part per queue, and the parts of all of a lobby's skeletons and its effects draw in
  that one order: the other skeletons' players are made with the bottom player's WebGL context, which runs their
  frames and draws everything into its framebuffer. Three lobbies need it: their second skeleton, the intro's scene
  parked beside the lobby, has pages below and above the character's, and their effects sort between them (Wakamo's
  fire at 3000 under her at 3050, Yuuka (Track)'s dust at 2950 and Shigure's bokeh at 3002 under them). With one
  canvas per skeleton those effects drew over the character with the parked scene. Equal keys keep the prefab's
  stacking order (Unity breaks them by the distance from the camera to each renderer's bounds). A lobby whose bottom
  skeleton does not load draws each skeleton on its own canvas.
- **Post-processing.** The lobby is drawn by the PC client's main Camera (level1 `Persistent/Camera`: perspective,
  10-degree field of view, HDR), whose culling mask has the lobby's layer 12 and whose URP volume mask has layers 17
  and 30. Every lobby prefab carries global URP volumes on layer 30 (`PPV` at weight 1; `PPV_C`, a flash, and
  `PPV_D`, a depth of field, which talk timelines drive and which mostly sit at 0); four (Erika, Yoshimi, Shimiko,
  CH0356) keep them on the UI layer 5, which that camera never blends, so they have none. `lobby.py` follows the
  volumes that count to their profiles (in the core's bundles, a few in another core's) and blends them the way URP's
  VolumeManager does (`lobby.json` `post`). Of the 265 lobbies: chromatic aberration in 259, lift / gamma / gain in
  166, Panini projection in 190 (on the 10-degree view the middle is enlarged by 0.6 %) and, from a flash volume left
  a little on, colour adjustments in 17 (post exposure up to +0.53 EV for Aru, colour filter, contrast, hue). No
  lobby has bloom, vignette or tonemapping, and no depth of field or lens distortion is on at a weight above 0. The
  linear pass's copy to the canvas runs them as URP 12 does (Unity 2021.3; the URP asset grades in LDR with a
  32-step LUT; checked against PostProcessPass, UberPost, LutBuilderLdr and ColorUtils.PrepareLiftGammaGain): Panini,
  the three-sample chromatic aberration, then post exposure, the clamp to 0..1 and the LUT's steps (contrast in LogC,
  colour filter, lift / gamma / gain, hue, saturation), in linear light; the LUT's own 32-step interpolation is not
  modelled. A lobby's skeletons are post-processed together, once, as the camera's target is (7 lobbies have more
  than one).
  Panini and the aberration are placed in the game's screen (the camera's 16:9 view), not in the canvas: the left
  view and a zoomed or panned fullscreen view show them where the game draws them on the art, magnified with it.
  Placed in the canvas, Michiru's left view had a 7.7 px fringe at its left edge, near the middle of the game's
  screen, where the game has none, and a 2x zoom drew 7.7 px at its edge for the game's 1.9 px magnified. Past the
  game's view (zoomed out beyond it) the game shows nothing to match: both shifts keep the values of the nearest
  point of its edge instead of growing with the distance. Every lobby's Panini crops to fit, so neither reads past
  the game's view from inside it, as in the game.
- **PP setting.** The Settings L2D tab's `lobbyPost` (on by default) is the PC client's PP option
  (`AllowPostProcessing`, graphics options): off draws the lobby without its volumes, at once. Of the 19 in-game
  captures among the references, only Michiru's (CH0113) shows chromatic aberration, and it matches the lobby with
  PP on: at the survey's 400 px crop the colour edges are offset +1.0 / -1.0 / +0.8 / -1.1 px (top, bottom, left,
  right) in the game and +1.0 / -0.9 / +0.9 / -1.0 here, and her face is Y 157.7 in the game, 157.3 with PP, 145.0
  without. The other 18 show no offset at all, so they were taken with PP off, and they match the lobby with it off:
  mean |dY| 1.8 off, 3.6 on (CH0326 Y 198.4: 199.4 off, 205.8 on; CH0228 209.0: 209.0 and 213.2).
- **Particle effects** (`gameassets/particles.py`, `particles.js`, `particles-gl.js`). Nearly every lobby prefab
  carries Unity ParticleSystems: bokeh and dust over the scene, stars, petals, Michiru's sparkler. `lobby.json` lists
  them under `fx` with their modules, renderer, material and place, and the page simulates and draws them:
  - Place: a system under a SkeletonUtilityBone (Follow) moves with that bone of its skeleton, through the transforms
    below the bone; any other keeps its place in the prefab. Local simulation space moves the particles with it, world
    space leaves them behind. Unity world units are the UI root's (the 1665-unit view is 2 units high).
  - Simulated: duration, looping, prewarm (exactly one duration, in steps of 1/30 s), start delay, lifetime, speed,
    size and rotation (1D or 3D), colour, gravity, max particles, scaling mode, simulation speed, the ring buffer
    (pause until replaced holds a particle at the end of its life, loop until replaced plays its loop range again,
    both until max particles needs its place: ch0334's boosters, one particle a second, and the sea meshes of ch0188
    and ch0356, 35 systems in 8 lobbies, vanished after their lifetime without it); emission over time and bursts
    (cycles, intervals, probability; every loop fires its bursts, the first one right after a prewarm, also where
    frames of 1/60 s add up to a hair under the loop's end: missed before in 400 of the 1419 looping systems with
    bursts, Tsurugi's smoke and Izuna's flare empty for 5 and 7.5 s after opening); a frame in Unity's order: the
    particles whose life ends in it die first, then it emits into the places they freed, and a particle emitted within
    it moves only for the part after its emission time (the rate's at the times its count passes a whole number), so a
    particle lives its whole lifetime and a system at max particles replaces one in the frame it dies in, at any frame
    rate. A frame used to emit before its deaths and draw a new particle a frame old: hibiki_home's river surface and
    background glows (one particle each, 60 a second, living 1 and 5 s; ch0177 and ch0356 have the same river) went
    missing for a frame at every replacement, its bottom-left corner blinking dark once a second, and Utaha's star (one
    particle, two a second, living 1 s) was off a third of the time; 63 systems in 39 lobbies had such gaps (60 fps,
    4 x 120 s); emission over distance (particles per Unity world unit the system's origin moves: CH0292's
    headlights, 9 systems); the shapes sphere, hemisphere,
    cone, box (volume, shell, edge), circle, edge, rectangle and donut
    with arc, thickness and their own transform, the arc's and an edge's mode (an edge takes the radius's): loop and
    ping-pong over the system's time, snapped to the spread, and burst spread, the particles emitted together evenly
    (Hibiki's ten bokeh edges; CH0069's and CH0294's halos have a spread too, 0.12, and how burst spread takes it is
    neither in the game's data nor in its compiled code, so they are placed at random as before); velocity over
    lifetime (linear, orbital, radial, speed modifier),
    limit velocity (dampen), force, colour, size and rotation over lifetime, colour by speed (CH0318's dust), texture
    sheet animation (start frame and
    frame over time as fractions of the sheet), custom data, birth and death sub-emitters and trails in particle mode
    (CH0172's oil drops: a point every minimum vertex distance, living the lifetime's share of the particle's life, a
    particle that stays put until all of them have gone starting again where it is). Systems below GameObjects a
    SpineEventToggleObjects lists (CH0217's fish and bubbles, 13 systems) play while the skeleton's EnableObject and
    DisableObject events have them on, from their start as GameObject.SetActive starts them. Curves are evaluated as
    Unity's are, a segment with a Constant (infinite) tangent holding its left key's value: the water drops of
    misaki_home and ch0095_home keep still, then jump between speeds. Billboards (view or
    local alignment, max size a fraction of the game's view height), stretched billboards and meshes (the particle
    colour times the mesh's vertex colours: ch0188's sea meshes fade out at their ends by them; each particle draws
    one of its renderer's meshes, up to four, picked uniformly at random or by their weights: the four rain streaks
    of misaki_home and ch0095_home and the two confetti triangles of five *_multi lobbies, 17 renderers in 7 lobbies).
  - Drawn: the game's DSFX shaders ported from their compiled programs (Additive, AlphaBlend, AlphaBlend_Add, the
    Mask, Distort, Step, Step_MXChar and Step_Distort variants, AlphaBlend_Add_Distort_1a and
    AlphaBlend_Add_Double_Distort_1a, AlphaBlend_2_Roz, AlwaysView_Add, Matcap with the meshes' normals, General
    Unlit VertexColor) with each material's blend, in linear
    light: textures sampled as sRGB where the game imports them so, vertex colours as 8-bit sRGB decoded (as stored
    where the renderer's Apply Active Color Space is off: 116 systems in 30 lobbies, Nodoka's star and meteors,
    Michiru's fireworks), Color properties decoded (above 1 by a 2.2 power), Vector properties as stored. Particles
    sorted by distance or by depth (CH0069, CH0294, CH0172) draw the farthest first. Each system draws after the
    skeleton part it sorts after (sorting layer, its value: the game's one layer besides Default stores 2, 7 systems in
    5 lobbies over every skeleton; then sorting order, render queue and back to front with its sorting fudge; see Draw
    order), or before the first one; its trails are a second submesh with the renderer's second material, sorted by
    that one's queue (CH0172's dirt trails at 2950 under her page, the drops at 3100 over it). The textures come from
    the game files on request and stay in memory.
  - The two *_1a shaders (ch0188's and ch0356's water splashes, ch0334's boosters) are UV distortions like Distort:
    their one pass samples only their own main, mask and distortion textures (no grab pass, no camera opaque
    texture), so nothing behind them is read. They also port their vertex programs, which route the particle's
    custom data into each UV through the material's CustomData_* selector vectors (which components of custom data
    1 and 2 add to a UV's tiling and offset, and to the distortion power); the Double variant takes two distortions
    from the red and green channels of one texture, each with its own tiling, scroll and power, and its keywords
    (_Tex_Main_Distort, _Tex_Mask_Distort: Off, R, G, RG) say which shift the main and the mask UV.
  - Not reproduced: noise, collision, lights, inherit velocity, size and rotation by speed (no system a lobby draws
    uses them); limit velocity's drag (CH0318's dust, too slow for it to tell); Animators and scripts that move or
    switch systems during a talk (a system plays as the prefab leaves it; the mesh renderers' Animators, the Spine
    event toggles and the intro's timeline are played); mesh and sprite emitter
    shapes (emitted from the shape's origin); the materials' culling (most DSFX materials cull back faces: in Unity a
    locally aligned billboard or a mesh turned away from the camera is not drawn; here every face is, as the
    mirrored transforms of ch0058's splashes and ch0334's stretched sparks would need Unity's own handling first). The
    framebuffer holds 8 bits per channel, so light that adds up past white is clipped at once (the game clips the sum
    at the end; the same result for purely additive light).
  - Cost: about 0.2 ms of main-thread time a frame for a typical lobby, 0.6 ms for the busiest (about 300 particles);
    the *_1a systems add 0.1 to 0.3 ms (ch0188's eleven splashes, ch0334's eight booster meshes);
    the first frame compiles each shader once. Everything is freed with the lobby (`particles-gl.js` detach and
    dispose), and made again on the next frame once a lost WebGL context is restored (a restorable of each player's
    context, as linear.js has). Without WebGL2 nothing is drawn.
- **Mesh effects** (`gameassets/meshfx.py`, `mesh-fx.js`). Nine prefabs carry plain MeshRenderers with effect shaders:
  lens dirt and light quads (FX/FX_SHADER_Lobby_Order, One One: the texture times `_Color0` and the vertex colour,
  ported from its compiled program; DSFX/FX_SHADER_Additive_0) in Akane's, Eimi's, Karin's, Shiroko's, Sumire's and
  Shun's lobbies, Hanako's water surface, Maki's drifting dust and Utaha's window glow
  (DSFX/FX_SHADER_AlphaBlend_Add_Distort_0), 20 renderers. All but Hanako's mesh are Unity's built-in Quad (unity
  default resources, path id 10210; its vertices, UVs and triangles are built in), white where a mesh has no vertex
  colours and as stored where it has them (Unity converts no mesh colours to linear light, and the three programs pass
  COLOR0 on unchanged). They are placed like the particle systems (on the bone of a SkeletonUtilityBone in Follow mode,
  through the transforms below it, or on the bone itself for one on the bone's own GameObject; else from the prefab
  root), sorted with them and the skeletons' atlas pages (Akane's lens dirt at 2950 over her page at 2800, the light
  shafts at 3100), and drawn by `particles-gl.js` with the particle programs. Back faces are culled as the material
  says, front faces being clockwise on screen and a mirrored transform turning that round, as Unity does. Four lobbies
  loop them with an Animator: the controller's default state plays one clip (`fx.clips`, sampled at its own rate and
  looped), which scales, moves and turns the quads (Euler curves in their rotation order) and animates their materials'
  colours: a Color channel (`.r`, bit 30 of the binding's attribute set) is converted to linear light like the
  material's colours, a Vector channel (`.x`, as Shiroko's lens dirt animates its `_Color0`) is not.
- **Skeleton materials** (`gameassets/spine_materials.py`, `skeleton-materials.js`). spine-unity draws each submesh
  with its atlas page's material, so a page's shader decides how its slots draw, whatever their blend mode:
  - Aru's third page uses Spine/Blend Modes/Skeleton PMA Multiply (Blend DstColor OneMinusSrcAlpha): its Normal
    slots multiply. Hina's multiply light takes the same shader from her blend mode materials.
  - CH0319's, CH0326's and CH0355's water and river pages use Spine/Skeleton MX Standard - NoiseFlowAnimation: the
    page's UV shifts by (2 noise - 1) x `_PixelNoisePower` x 0.001 x the vertex alpha, the noise scrolling at
    `_PixelNoiseSpeed`, inside a square of a plane taken from the vertex position (less `_CenterX` / `Y` in Unity
    units, times 0.01, turned by `_Rotate`, skewed and bent toward a vanishing line by `_SlopeForward` and
    `_SlopeSide`), the vertex colour as stored (no conversion to linear light).
  - CH0310's game screen (its intro only) uses Spine/Skeleton MX Portrait Noise: two passes draw alternate horizontal
    bands of the game's screen (sin(v x 900 + time) at or above 0, at or below), each shifted by `_GlitchOffset`
    of the camera's clip space on the frames a hash of the time passes `_GlitchFreq` (about one in fifty), the page
    times 0.72 x a scrolling scan line texture plus `_PortraitNoiseTint`.
  - Slot blend modes reach the screen only through materials: with `requiresBlendModeMaterials` set,
    BlendModeMaterials gives Multiply and Screen slots (Additive ones with `applyAdditiveMaterial`) the replacement
    material listed for their page; without it (all but 4 of the lobbies' 288 skeleton data assets) they keep their page's material and
    draw as Normal. ch0163's light_01, Hiyori's R_eye_ligth_02 and airi0's FX_Light_01 (airi0_home's LobbyAiri
    prefab names airi_home's skeleton data, which has none) drew as Screen; spine-player's Screen on straight alpha
    textures also ignored their alpha (a hard-edged patch over Hiyori's iris). A page that a mode's list does not
    name gets no replacement material (the list is matched by page name), as an entry left empty does; both draw as
    spine-player draws. An Additive slot always adds (its premultiplied vertex colour has alpha 0).
  - The dark colour (two-colour tint) is never drawn: `tintBlack` is off in every lobby's SkeletonAnimation and no
    lobby page shader reads it (their vertex programs take position, UV and one colour). CH0205's wing and Neru's
    halo key one, black.
  The ported programs are taken from their D3D11 bytecode and run in spine-player's batch: a draw switches to the
  page's program and blend (flushing the batch), feeding it the game's vertex colour (premultiplied, alpha 0 for an
  additive slot) from linear.js's tinted one.
- **Pinned bones** (`gameassets/pins.py`, `skeleton-pins.js`). A SkeletonUtilityBone in Override mode sets its bone
  from its GameObject after the animation, every frame. The drag targets (lobby-touch.js) are such bones; two others
  nothing moves, so the bone stays where the prefab put it: Hihumi's Hip at (218.8, 650.9), 12.7 units below the
  skeleton's own (its driver, SpineCharacterGyro, is switched off and rests there too), and with it her whole scene;
  CH0242's cheek_02, whose PinchEnd_01_M keys only what the pin leaves alone. The zoom-out limits and the head-pat
  point are measured with the pins.
- **Intro scenes.** Three prefabs keep four skeletons of their intro timeline (a PlayableDirector with Spine Animation
  State tracks) parked beside the scene: Shigure's Shigure_00 and Shigure_01 at x -4500 and 4500, CH0184_00 and
  Wakamo_Scene_0. Their clip assets are on track -1 (only the timeline plays them). Shigure's timeline (an Animation
  track on `Shigure`) puts Shigure_00 at x 0 until 5 s, Shigure_01 until 9.17 s, then Shigure_home, each scene back
  at its place after, and spine-unity sets a Spine track empty when its clip ends (`dontEndWithClip` off), so the
  scenes rest in their setup pose, out of the game's view. The lobby never loops an intro (`idleOf`): a skeleton
  with intros only rests in its setup pose, one with a Dummy loops that. Looped, Shigure_00's Start_Idle_00 drew its
  train (`train Sample`) across the bottom of the view, over Shigure, every 6 s.
- **Clip endings** (SpineBase `PlayMasked`, `QueueTrackClose`, `EnqueueSpineClip`, `EnqueueMasked`). A clip that
  does not loop and has FinishMode 2 fades its track out: with UseDefaultOutroMix over the skeleton's default mix,
  ending with the clip; otherwise over its OutroMix, starting OutroStartOffset after the clip's end (`clips.*.outroAt`),
  so a talk's last frame holds while it fades (0.3 to 0.6 s for most). FinishMode 3 queues the next clip the same way
  (its intro mix ending with the clip, or starting at its end).
- **Idle clips** (`lobby-sync.js`; the rules read from GameAssembly.dll: SpineBase `PlayBaseClip`, `PlaySpineClip`,
  `CoPlayMaskedRandomTiming`, `ResetRandomizedDelayWithDuration`, `AddRandomCountLoop`, `EnqueueRandomTiming`,
  `ReturnToIdleWithSpineClips`, `PlaySyncClipsIfNotPlaying`, `StopAllMasks`; the enums' values from
  `global-metadata.dat`). A SpineCharacter starts every clip of its idle pool, each on its own track, and each with
  its sync clips (`lobby.json` `idles`, `clips.*.sync`). A clip's PlayMode says how: 0 the base track and 1 masked
  play at once, 3 is a random idle clip (played by the random idle schedule only), 2 masked at random times: it waits
  (RandomTimingIntroDelayMode 0: not at all, 1: RandomDelayMin to Max, 2: IntroDelayDuration plus that), plays once
  on its track, at full weight on an empty track and mixing in from its own last frame after that (its intro mix),
  plus AddRandomLoopMin to Max extra plays queued one duration apart; a looping one then waits again, from that start,
  its playing time plus RandomDelayMin to Max; between plays the track holds the last frame, nothing fades it out.
  The clock is the skeleton's own animation time. A dialogue keeps them: the game returns the character to its idle
  at the start and the end of one keeping the idle's clips, and starts again only the wait of a random-timing clip
  that has not played yet (its track still empty); a drag stops every other track (`StopAllMasks(false)`) but keeps
  them too. They are part of the clips the zoom-out limits are measured over (on their track over the idle, on every
  layer). 19 lobbies: Shigure's Train_00_R (track 3, 3 s plus 10 to 15 s, then 6.67 s plus 10 to 15 s; the
  `train` slot, draw index 141, passes behind the bridge and Shigure from the top right, and a light flashes over the
  right side), Hoshino's background (Start_WhaleMove_01_R at once, 30.6 s, then WhaleMove_01_R, 56.7 s, every 3 to 4 s
  after the last), Seia's butterflies (both lobbies, 5 to 10 s apart), Kayoko (New Year) snow, Serina (Christmas) and
  Saki (Swimsuit) backgrounds, Nagusa's petals and clouds, Takane's aurora and breath (2 s plus 8 to 10 s), Hare
  (Camp)'s three shooting stars (6 s plus 20 to 30, 8 to 15 and 15 to 22 s), Pina (Guide)'s petals, Sumire
  (Part-Timer)'s and Hasumi (Swimsuit)'s sweat (10 s plus 25 to 30 and 15 to 20 s), Michiru (Dress)'s petals, Reisa
  (Magical)'s leaves, Yuzu (Armed)'s background and clouds (0 to 3 s apart), Kisaki (Swimsuit)'s fog, Erika's and
  Juri's clouds; the others without a wait loop back to back. Not reproduced: a random-timing clip's next clip
  (FinishMode 3 on a one-shot, which no lobby has).
- **Clip sounds.** A clip's SoundKeys (`clips.*.sounds`: time, event) play when its track entry's animation time
  reaches them, every loop on a looping entry, and no more once the entry is interrupted, ends or completes
  (`EnqueueSoundKeys`, `CoPlaySoundKeys`); 40 clips have them, 37 talks of 15 lobbies (cloth, leather, swings),
  Shigure's train (`SFX_Spine_Subway` at 3.1 s) and Hoshino's whale. Every event is an AnimationAudioEvent of the
  lobbies' shared bundle (`lobby.json` `sounds`): one of its AudioClips at random, at its volume, and at its pitch
  when that is above 0 (as a playback rate, the pitch going with it; 8 of the 32 events have a negative one, which
  the game never sets on the source). The clip is decoded from the game's audio bundle in memory (UnityPy's FMOD, `sfx_<id>.wav`), fetched
  once a clip with sound keys starts and kept as a blob for the lobby. They follow the voice volume and mute (the
  game's SFX slider has no counterpart yet), and `--mute` silences them. Not reproduced: a lobby's ambient loop
  (SpineBase `ambientEvent`, AmbientAudioPlayer).
- **Zoom.** Fullscreen opens on the game's 16:9 view and zooms in to 2x. How far it zooms out is the Settings L2D
  tab's zoom-out row (`settings.set lobbyZoom` and `lobbyZoomCustom`; `lobby_zoom` and `lobby_zoom_custom` in
  `webui.json`, where the boolean `lobby_zoom_out` it replaces still reads as Full Art or Game View; Full Art when
  nothing is stored); panning stays inside the same limit, and no limit goes past 4 game views across:
  - *Game View*: the game's view, never wider.
  - *Full Art* (the default): the widest 16:9 view inside the painted art (one 16-unit cell inside the painted rect). The painted
    rect is found on idle poses; the poses of every clip the lobby plays (dialogues with their sync clips, the drag
    clips and their ends, random idle clips, every 0.1 s over the idle) are checked in the background, in 8 ms
    slices, within 24 cells of its sides, and the rect gives up what one uncovers: a dialogue that moves the scene
    never shows a blank edge. Over the 265 lobbies (headless SwiftShader) it takes 780 poses and 0.55 s of slices at
    the median (2.2 s at most); 34 lobbies give up cells, most one, Kurumi three (Talk_07_M scales her scene down),
    Serika's CH0189 up to six (the end of every dialogue) and CH0303 up to four (a random idle clip).
  - *Unlocked*: the box of the art the idle poses paint (the same opaque art) joined to the scene across gaps of at
    most two cells, widened by what the clips paint (the same pass) up to 24 cells past it; parts parked away from
    the scene are left out (Toki's close-up head to the right, Miku's second stage set to the left), and a dialogue
    that moves or scales the whole scene (CH0243, Airi's CH0251) does not open the limit to its whole path. Kurumi
    1.41 game views across (Full Art 1.23), Toki 1.58 (1.27), Miku 1.44 (1.17); over the 265 lobbies 1.20 at the
    median (Full Art 1.16), 1.59 at the 90th percentile and 2.41 at most (CH0356's waterfall), 63 within 0.02 of Full
    Art. The art is looked for within three game views around the game's view (14 lobbies reach that edge). The pass
    costs 10 to 30 % more with it (Kurumi 0.78 to 0.90 s, Toki 0.82 to 1.06 s).
  - *Custom*: 1.0 to 4.0 game views across (0.1 steps, a slider row under the option), around the game's view.
    Settings' sliders (this one and the Sound tab's Voice) are a native range input laid unseen over the game's slider
    (`settings.js` `wireSlider`, its native thumb `SLIDER_THUMB` wide so its centre travels the drawn thumb's range): the
    pointer, the arrow keys, Home, End and Page Up / Down move it, the game's thumb and the value label follow while it
    changes, and the value is saved when it commits (the pointer's release, each key press; the page is drawn again
    with the focus put back on the slider). Keyboard focus shows a ring on the thumb. A drag the system cancels
    (`pointercancel`) goes back to the saved value and saves nothing.

  Past the painted art (Unlocked, Custom) the lobby shows black: what the game's lobby camera clears to (level1
  `Persistent/Camera`, solid colour, black). With PP on, the grade runs over that black as over the art: the bottom
  layer is drawn over it before grading, so the art's alpha edges blend into it in linear light too. 7 lobbies lift
  it above black (ch0082 60/255 grey, ch0259 45, ch0058 40, ch0222 a dark blue). Without WebGL2 the page shows black
  there. Sparse art (a mod) keeps the blurred edge extension. The measurements are kept for the session.
- **Input.** Press and drag: head pat, eye follow and the other drag boxes. Mouse wheel (a notch zooms at least a
  line's worth) and pinch (ctrl + wheel in Chromium / WebView2, gesture events in WebKit) zoom at the pointer; a
  two-finger trackpad swipe pans in any direction. A wheel is told by its steps: whole lines, `wheelDeltaY` in steps
  of 120 (Chromium), or in WebKit, whose `wheelDeltaY` is 3 x `deltaY` for every device, tenths of a line kept in
  16.16 fixed point (4.000244 px) against a trackpad's whole pixels. One scroll keeps the kind of its first event; one
  taken for a wheel that then sends a trackpad step goes back to the view it started from and pans (a WebKit whole
  line, or a Chromium notch: a Windows touchpad's first step can be exactly 100 px, wheelDelta 120). A Magic Mouse
  scrolls like a trackpad (ctrl + scroll zooms). A right- or middle-button drag pans. Native drags and the Force
  Touch look-up are turned off on the lobby.
- **Input trace.** Started with `--debug`, the page sends the fullscreen lobby's pointer, wheel and gesture events
  (moves and scrolls at most every 50 ms) and every press anywhere in the document, with its target, through
  `debug.trace`, so a trackpad problem can be reproduced once and the log sent.

## Classic window

The bottom-bar entries without a web screen (greyed, through the hand-off prompt: Front end, "Bottom navigation"), and
Settings' More Settings, hand over to the classic Tk GUI (`webui/classic.py`, its child side in `gui/control.py`). It
runs in a process of its own on both systems: Tk (Aqua) and pywebview (Cocoa) both need the main thread on macOS, and a
crash in a classic tab never takes the web UI with it.

- **One window per data directory.** `ClassicWindow.open(tab, may_start)` (`app.openClassic`) starts the program again
  (`system.classic_command(tab, control=True)`: `--classic --control`, then `--tab <name>` or `--settings`) and waits
  up to 30 s for its `ready`; an exit or silence before that is `classic_failed {exit_code}` (`null` when it stayed
  silent; a late `ready` is still taken, and the window then shows itself). With a window open, a click sends the tab
  and a raise; while a classic task runs (`busy`), only the raise, so a running task's tab is never switched. A window
  left by an earlier instance (below) is only raised. No window starts while a job writing the game folder is queued
  or running (`busy`): that job would stop at its next write with `classic_open`, a toggle half done. The busy check
  (`may_start`) and child registration share `ClassicWindow.starting()` with each game writer's classic check and
  job submission: either the writer is pending first and the new window is refused with `busy`, or the child is
  registered first and the writer is checked against the open window before submission. The lock is released before
  waiting for `ready`; raising this instance's window or an orphan never runs `may_start`.
- **The channel** is the web UI's own HTTP server, not stdio (the Windows build is onefile without a console):
  `BAMT_CLASSIC_URL` (the server's origin) and `BAMT_CLASSIC_TOKEN` (one-time, random) go to the child, which takes
  them out of its environment at once. It long-polls `GET /api/classic/control` (25 s) for its lines (`place x y w h`,
  `raise`, `tab <name>`, `settings`; anything else is ignored) and posts `POST /api/classic/report`
  (`{"type": "ready", pid, hwnd, exe}`, `{"type": "shown", tab}`, `{"type": "busy", n}`). Both take the child's token
  in `X-BAMT-Classic` and never the session token, and the WSGI app routes `/api/classic/*` to `ClassicWindow.wsgi`
  after its Host check. A daemon thread polls; the Tk thread drains the lines with `root.after(100)`. When the web UI
  quits, the polls are refused, and the classic window stays open on its own.
- **Placement.** The child starts transparent (`-alpha 0`). After `ready` the parent places it, hands the foreground
  over, then sends `raise` (always in that order): centred on the BAMT window, inside the work area of the screen that
  holds BAMT's centre, and shrunk to fit (the classic window is a fixed 800 x 1000). On macOS the parent reads the
  `NSWindow` frame and the screens' visible frames on the main thread, flips them against the primary screen and sends
  `place` in Tk's coordinates (the title bar fits too); then `yieldActivationToApplication:` (macOS 14) or an
  activation ignoring other apps. On Windows it scales the logical size by the monitor's DPI and moves the child's
  window itself (`SetWindowPos` on the `hwnd` from `ready`; `AllowSetForegroundWindow(ASFW_ANY)` before the raise).
  The raise shows the window, keeps it on top for 300 ms and focuses it; the user can bring BAMT back over it. With
  no raise 5 s after `ready`, the child shows itself.
- **Busy.** `TabFrame.run_in_thread` and `StoppableDialog.set_task_running` count the running tasks
  (`gui/control.py busy`); each new count is reported.
- **Output.** The child's stdout and stderr go to `classic.log` in the data directory (rewritten at each start,
  unbuffered, UTF-8), never to an unread pipe.
- **Exit.** A watcher thread waits for the process and publishes `classic.closed {code}`. Should the Windows onefile
  launcher return before the window it unpacked, the pid the child reported is followed until it ends or stops
  polling for 60 s.
- **`classic.json`** (`<data dir>/classic.json`, `{pid, exe, hwnd, created}`) names the open window, so an instance
  started later still refuses game-folder writes and can raise it. It is written at `ready` and removed with
  `classic.closed`, and by the child at its exit. `created` is the creation time the system reports, as an integer:
  `GetProcessTimes` on Windows (FILETIME, 100 ns), `proc_pidinfo`'s `pbi_start_tvsec` on macOS (seconds), else
  `ps -o lstart=` under `LC_ALL=C TZ=UTC0`, parsed with a fixed English table, which gives the same number whatever
  the caller's locale and time zone; `/proc/<pid>/stat` on Linux. `find_classic()` takes the pid only while it is
  alive with that creation time (a reused pid has a later one), runs this program's classic GUI (the exe name on
  Windows, `--classic` in its arguments elsewhere) and is neither this process nor its parent; any other file is
  stale and removed, so it never blocks writes. `ClassicWindow.orphan()` is that reader for a window this instance
  did not start (GameWatch's `find_classic`).
- **Events.** `classic.opened {pid}` (process started), `classic.ready {pid, tab}` (window up, or a tab shown),
  `classic.closed {code}`. `ClassicWindow.status()` gives `{open, ready, busy, pid, orphan}`.
- **The queue** (`ClassicQueue`, decision 5). While a classic window is open the web UI does not write the game
  folder; the classic modal can queue the refused write for when it closes. In memory only: quitting drops it. At
  most one toggle (a later one replaces it, one back to the stored intent removes it) and one action per entry (the
  latest wins; a `mods.restoreDrift` over several entries is narrowed to the others). Each item keeps its call, the
  pins and the size and modification time of its entries' files. `run(host)`, after `classic.closed` and the scan
  that follows it, submits each item as its normal job, in order, and lists the dropped ones: a toggle whose `expect`
  is no longer the stored intent (`state_changed`) or whose pins are not the known installs (`folder_changed`); an
  entry whose own install changed, or is no longer the current one for `actions.apply` and `actions.restore`
  (`folder_changed`), or whose files changed (`entry_changed`); a refused submit with its own error; an item that
  cannot be checked (a toggle's stored intent under unreadable mod settings) with that error (`profiles_unreadable`).

## Tray

The tray icon (Windows) or menu bar icon (macOS) of the app window (`webui/tray.py`), shown while the preference
`tray` is on (Settings' Mods tab, `settings.set tray`; off by default). `settings.get` and `mods.state` say
`traySupported` only in the app window on those two systems. Closing the window quits the app, and the icon goes with
it (decision 7); browser and serve mode have none.

- **The menu**, rebuilt from the last `mods.state` each time it opens (`tray_menu(snapshot, strings)`, pure):
  - **Enable Mods**, one item for every known install, with no install name (decision 1), checked while mods are on.
    Greyed, it has a second, disabled line: the `reason` (`error.game_running`, `error.game_status_unavailable`,
    `error.steam_busy`, `error.classic_open`, `error.game_not_found`, `error.profiles_unreadable`), else, while a
    toggle runs or writes wait for the classic window, `tray.working` (Working 12/40) or `job.running`. The tray never
    queues for the classic window.
  - **Back to Official: n** (`tray.drift`) while the last scan found drift (with scan consent).
  - A separator, **Open** (`settings.classic.open`) and **Quit** (`tray.quit`).
- **Clicks.** macOS: either click opens the menu. Windows: a left click (or double click) opens the window, a right
  click the menu; Open is in bold.
- **Tooltip**: the app title, then Mods on or Mods off (`mods.onState`, `mods.offState`), or Working 12/40 while a
  toggle runs.
- **Enable Mods** submits `mods.toggle {enabled: !enabled, expect: enabled}` at once, an Off adopting first like every
  Off (decision 4). The window comes to the front instead on the first toggle ever (`explained` false: the
  explanation, whose button toggles), and afterwards on a refusal (`not_enough_space` included) or a result with
  failed, stuck or left entries. **Back to Official** opens the window on the drift prompt. **Quit** follows
  `app.quit`; while a writer job runs, the window comes to the front with the busy notice (`app.busy`).
- **What the page is told:**

  | Event | Data | The page |
  |---|---|---|
  | `tray.toggle` | `{job, enabled, expect}` | follows the job as its own toggle (`waitJob`): progress, result popup |
  | `tray.open` | `{view: "explain", enabled, expect}` | shows the first toggle's explanation; its button submits `mods.toggle {enabled, expect}` |
  | `tray.open` | `{view: "error", code, params}` | shows the refusal as for its own toggle |
  | `tray.open` | `{view: "drift"}` | opens the drift prompt |

- **Never blocking the UI thread.** The menu and the tooltip read only the last `mods.state` the tray kept: it
  observes the event bus (`EventBus.observe`, called on the publishing thread and not counted as a page, so the game
  watch's fast polling still follows pages and the shown icon only). Opening the menu pokes the game watch, whose
  answer the next opening shows. Every click runs on a thread of its own: submitting the toggle may build the library.
- **macOS** (`CocoaTray`). An `NSStatusItem` on pywebview's own `NSApplication` loop (PyObjC comes with pywebview),
  made, changed and removed on the main thread only (`AppHelper.callAfter`); `menuNeedsUpdate:` rebuilds the `NSMenu`.
  The image is `assets/tray/menubar.pdf`, a vector template image of 18 pt that the menu bar tints for light, dark and
  highlighted bars, with `menubar@2x.png` (36 px) as the fallback. It is removed on the main thread once
  `webview.start` returns. rumps and pystray are not used: each runs a loop of its own.
- **Windows** (`WinFormsTray`). A `NotifyIcon` and its `ContextMenuStrip` made on pywebview's WinForms thread
  (`window.native.Invoke`), else on an STA thread with its own `ApplicationContext`; the strip's `Opening` rebuilds
  the items. `tray.ico` is loaded at the small icon size (`SystemInformation.SmallIconSize`), so Windows takes the
  frame drawn for the display scale. The icon is hidden and disposed of when the form closes, so Windows keeps no dead
  icon. Windows 11 puts a new icon in the overflow until the user pins it.
- **The icons** (`assets/tray`, the project's own art from `assets/eligma.png`, never a game asset). `menubar.svg`
  (18 x 18 pt, black on transparent: the gem's hexagon as a 1.6 pt band and its four-point star 1.5 pt clear of it,
  nothing thinner than 1.5 pt) and `tray.svg` (the colour icon: the hexagon in the gem's pink and the white star, 2
  px clear of the edge at 16 px) are the sources. `scripts/tray_icons.py` builds `menubar.pdf`, `menubar@2x.png` and
  `tray.ico` (32-bit frames rendered for 16, 20, 24 and 32 px) with Pillow only; `--check` reports built files that
  no longer match their SVGs (also a test), and `--preview sheet.png` renders the menu bar icon at 16, 18 and 22 pt,
  1x and 2x, on a light and a dark menu bar (through AppKit on macOS) and the tray frames on a light and a dark
  taskbar. The build scripts need no change: `assets/` is already included.

## Tests

- `config`, `catalog` and `library` with fixtures and temporary folders (`library`: a fake game folder of
  header-only bundles; its end-to-end checks copy a few real bundles into a temporary folder, never writing into the
  install).
- `gameassets` and end-to-end checks against a real install; skipped unless one is found (`BAMT_TEST_GAME_DIR`, or
  `game_paths.find_game_dirs`).
- `webui` through its WSGI app, without a window; the tray's menu and controller with fake services, never with a
  status item or a `NotifyIcon`.
- The front end's pure parts in Node (`tests/js/*.test.mjs`, each run from its `tests/test_*.py`), the shared popups,
  the Enable Mods switch, item 1's prompts and the tray's events on a stand-in DOM with the en-US strings
  (`mod_switch.test.mjs`, `repairs.test.mjs`, `spine_base.test.mjs` among them).
- The design tokens: `tests/js/ui_tokens.test.mjs` (the tokens, no retired literal in any web source, the caption range of
  every footer through every locale file, the small button, the style sheet rules) and the footer, frame-room, Settings
  row, message line button, consent and chip tests in `popup.test.mjs`; `tests/test_ui_tokens.py` compares the tokens with
  UIPopup_Sort, UIPopup_Option_PC, UICharacterDetail and the three BlackBgClickArea tweens from the development view
  (numbers only; skipped without `BAMT_TEST_GAME_DIR`).

### Lobby camera framing (BL-137)

- **Entry.** In the fullscreen memorial lobby a white visual-mode button with the game's `Common/Cafe_Icon_Video`
  sits left of the photography button (`lobby-camera.js`). It enters the framing: the lobby rests (the intro skipped),
  Back (UICharacterVisualMode `Btn_Back`), Reset and the photography shutter (`Common_Btn_Circle_W`) replace the
  fullscreen controls, and a dashed outline marks the game's own view.
- **Moves.** The wheel zooms at the pointer's height, any drag moves the view up or down (`lobby-touch.js` pan only);
  the view stays 16:9 and centred across (`lobby.js framingView`).
- **Limits.** `measureFraming` paints every pose the lobby goes through into 16-unit cells three game views across:
  the idle, every clip (`clipPoses`) and the intro every 0.1 s with its skeletons where the timeline has them
  (`lobby-intro.js sampler`: GameObject activity and transform curves, intro-only scenes, the Spine clips on their
  tracks, no mixes). A cell outside the game's view that some pose leaves unpainted may not be shown; inside the game's
  view everything may, as the game shows it. Zoom-in stops at 2x the game's view.
- **Kept.** `cameraParams(V, G)`: the lobby scaled by `k = G.w / V.w` about the screen centre and moved by
  `-k (V's centre - G's centre)` NGUI units, so that V fills the screen as G did.
- **Entry shown** in every fullscreen lobby, except one whose lobby.json has no prefab.
- **RPC `lobby.camera.save`** `{variant, lobby, source, view: {x, y, w, h}, scale, offset: [x, y]}` starts a job
  (`writes="library"`) whose result is `{variant, lobby, number, scale, offset, mod}`. `source` is what the lobby
  shows: `official`, `mod:<id>` of one of the variant's lobby mods, or one of its cameras (the new camera composes:
  `k = k2 k1`, `offset = k2 o1 + o2`, on that camera's source). After it the detail reads the family again; the
  popup says `lobby.cameraSaved` / `lobby.cameraDetail`.
- **The prefab** (`library/lobby_camera.py`). In the student's own `ui-uilobbyelement-...` bundle (one prefab per
  bundle), the root's `MX.Utils.UIManualScaleByAspect`: every `scaleTransforms` Transform's local scale times k, every
  `scaleTransforms` and `offsetSettings` Transform moved to `k p + offset` in the root's frame (z kept), every
  `MostWideAdd` / `MostNarrowAdd` times k; `mostWideScale` / `mostNarrowScale` kept. A similarity about the screen
  centre at every aspect. Every other object and stream stays byte for byte; saved with the CRC fix (lz4) and checked
  after. A prefab without the component on its root, or whose targets are not plain Transforms under unscaled,
  unturned parents, is refused (`lobby_camera_unsupported`).
- **The choice.** A camera is a generated generic mod of that prefab bundle (record key `lobby_camera`: variant, lobby
  core, source, scale, offset, root, prefab file, slot, base, number). Its slot is the prefab's own, keyed by its CAB
  (a group packed one asset per bundle); no lobby slot holds the prefab, so other lobby mods and students are as before.
  The App lists it as a lobby mod of its variant (`kind` "lobby", `camera: {source, number, scale, offset}`), named
  `<source's name> - lobby.cameraName` (`lobby.original` for the official art), in Switch Memorial Lobby with the
  lobby mods; rename and delete as for mods; not in Settings' generic sources nor the Student Hall.
- **Apply.** `actions.apply(variant, "mod:<camera>", "lobby")`: the source's art through the lobby's own apply, then
  the prefab through the generic one (only saved while mods are off). Any other lobby choice of the variant and a
  restore of its lobby put the prefab back to the camera's base (the official prefab, or the generic prefab mod it was
  framed on). The master switch, Apply Mod Configuration, integrity and delete treat the prefab slot like any generic
  slot. `configured` / `selection` give the camera as the lobby's source while the prefab slot holds it and the lobby
  slot holds its source.
- **Game update.** Before a master-switch plan and a camera's apply, a camera framed on the official prefab whose
  prefab file the game replaced is made again on the new one with its scale and offset, in place (same id).
- **Preview.** `/asset/spine/mod/<camera>/` serves the source's art and, as `lobby.json`, the camera's own prefab
  (framed on the fly from the official one after a game update it was not made again for); `Lobby.show({camera})`
  reads the layout there. `/asset/spine/lobby/<core>/lobby.json` reads the official prefab while a camera holds the
  game's, so no other choice's preview shows a camera's framing.

## Lobby lines

Online data, fetched on demand and kept in memory only (never written to disk):

- Kivo (`kivo.py`): one student page per variant (zh-CN, ja and the voices, by voice clip name).
- The Global server's CharacterDialog table (`globallines.py`), as ba-data publishes it
  (github.com/electricgoat/ba-data, `global` branch, a jsDelivr mirror second): one download per session (about
  2.4 MB compressed; 60 s and 64 MB received or unpacked at most), only when the UI language is not zh-CN; after a
  failure it is asked for again after 5 minutes. `lobby.lines` waits up to 3 s for it, then answers with Kivo's lines
  alone (the download goes on for the next lobby).
  SchaleDB's voice data has no memorial lobby lines. A dialogue's rows (`UILobbySpecial`, by `GroupId`, played by the
  talk animation the first row names) go on that animation's voice clips in order; with fewer rows than clips the last
  clips get none (the row before stays on screen), with more rows the rest join the last clip's line.

The page shows only the UI language's lines: a dialogue card without one shows a grey, slanted "Dialogue N", and the
subtitle shows nothing for it.

The memorial lobby dialogue host (`.lobby-subtitle`) draws game widgets rather than a CSS text box. In the detail
screen's left view it uses the Hall's `student-hall-dialogue.js` `prepareBalloon`, `balloonPosition` and `wrapDialogue`:
`LobbyCommon/DialogPanel/ChatDialog/Talk`, `Lobby_balloon`, native fs52 and #3e444a. The lobby parser's `dialog.Talk`
is in the same prefab-root coordinate space as the pat and drag rectangles (both include ancestor transforms).
The top-left pivot maps through the current lobby view, then clamps to the visible area before the detail panel's
rendered background. Missing/nonfinite Talk positions, special framing and sparse art use `touch` as a zero-size
anchor; `balloonPosition` puts it to the right or flips it left. Camera/frame changes recompute placement.

Fullscreen keeps the same Talk balloon by the head (owner, 2026-11-04: switching to `UITalk` broke the continuity), in
the detail screen and the immersive Hall; only its bounds widen to the whole stage, as the detail panel has gone. It
follows the art while the fullscreen view zooms or pans. The Hall's ordinary memorial view keeps its view bounds even
though its camera calls `frame("full")`: `setDialogueFullscreen` separates immersive UI state from camera framing.
`hall.dialogueLayout` returns the Talk box; the backend reads its geometry and styling from the prefab.
The existing 0.26 s upward leave/rising entry sequence and textless-cue retention remain. The pointer-transparent
host exposes the current text through `role="status"` and `aria-label`; the Hall portrait dialogue stays unchanged.

### Local error reports and startup recovery

The shared registry, `src/bmpt/web/error-codes.json` (served at `/error-codes.json`), gives error and import-reason names
permanent display references and a `failed` or `notice` level. Names remain the identity and locale message keys;
references are `E` plus four digits, grouped by their first digit: 1 App/RPC/jobs, 2 game installation/official files,
3 mod import, 4 mod library/switch/apply, 5 assets/Spine/atlas remap, 6 Hall/photography, and 7 updates. New names take the
next free number in their block; references are never reused. Service error JSON keeps `code` as the name and adds
`ref` for registered names, preserving all existing IDs. Python loads the packaged registry once; the front end loads
it once and falls back to an empty registry if that load fails. RPC and job boundaries log service errors once
as `E3006 mod_parts_missing {params}` with compact JSON parameters, at INFO for notices and WARNING for failures;
an unregistered name is logged alone.

**Error Report** in Settings' log row and failure popups creates one local
`BAMT-report-<version>-<YYYYMMDD-HHMM>.zip`. The same action is available on the built-in startup failure page.
The log row's card states that reports are anonymized (`settings.log.reportNote`), as `UIPopup_Option_PC`'s option note
`Label_Locale_Info`: the caption role, pivot top left, SPACE.l under the dotted line, in SkillCutIn's red (`COLOUR.noteAlert`,
`#e06f6f`); the path and buttons follow it SPACE.m below (`settings-rows.js` `NOTE`, `valueGeometry`'s `noteH`).
The destination order is Desktop, Downloads, the app data directory, then the system temporary directory; Windows uses redirected shell folders.
Repeated reports in one minute receive a numeric suffix. Finder/Explorer selects the archive and the clipboard
receives its short summary. No upload is performed. A failed clipboard/reveal action does not discard the report.

The archive contains `report.txt`, `report.json`, and `webui.log`, `webui.1.log`, `webui.2.log`. Each log keeps at
most its last 512 KiB, dropping a partial first line. Structured data is limited to 256 KiB. Home paths (including JSON and URL encodings), user names, hostnames,
credentials (including the existing session-secret registry), and asset filenames are redacted; extensions and
counts remain. An apostrophe inside a word is part of a name, so a whole name is redacted. The game's own names stay:
official bundle names (`*_assets_all_<n>.bundle`, `*_scenes_all_<n>.bundle`, Unity's built-in shaders
`<32 hex>_unitybuiltinshaders_<n>.bundle`, an apostrophe allowed inside), CAB ids and the App's model URLs. Game/library summaries contain no entry names or install paths. Unavailable observations are `null`.
Game metadata is captured during initialization and `app.info`; report generation never initializes game assets
or acquires the library lock. Persisted library metadata supplies counts only. Consent uses the in-memory cache.
Optional reads have a 100 ms limit and share a one-second collection deadline; unavailable fields are `null`.
A stuck read keeps at most one daemon worker per field. Known secrets are substituted only as whole tokens of
at least eight characters. Structured credential fields are always hidden; keys and build identity are preserved.

Reports include app/build and OS details, system/UI locale, WebView2 or WebKit version, browser user agent,
front-end page/phase/errors (including popup codes and sanitized parameters), screen size/scale, WebGL support/renderer when available, consent states, game metadata,
library counts, boot timings, and the last failed job's error code/parameters. Recorded error names in front-end events,
popups, `triggeringError`, `lastFailedJob`, import rows and their source attempts carry the display reference in `ref`
alongside `code` when registered; import row `id` values remain row IDs. Backend exceptions create a report
in the data directory, with the same destination fallback chain on failure, at most once per minute, retaining three automatic archives with an `-auto` suffix. User-created reports are retained. A bounded redacted snapshot of
the latest exception and its environment is embedded in later reports; archives are never nested.

With the Check Game Files consent (read from the in-memory state, like the other consents, and reported as
`consent.scan`), `gameFiles` lists each known install that has Steam's file list (`steam_records.OfficialFiles`,
the cache the library's judgements share): the install's `id` and `server`, the bundle counts `listed` (Steam's list)
and `present`, and three groups compared by name and size against Steam's list over `GameData/Windows` and
`Preload/Windows`: `sizeChanged`, `missing` (listed but not there) and `notListed`. Each group has its full `count` and
at most 100 sorted `names`; Steam's names are the game's own, and of the bundles Steam does not list only names in the
game's own form appear. No paths, no hashing (stat only), never the library lock: the installs come from the switch's
snapshot. The read starts first and is bounded at 0.4 s within the collection deadline; without the consent, before
the installs are known or when the read runs out of time, `gameFiles` is `null`, and an install without Steam's list is
left out.

Recovery code, its six-locale strings and the shared built-in fallback stylesheet are embedded before external CSS and the module graph. HTML delivery does not
wait for game UI cache detection; the normal loading code adopts cached/game styling after `app.info`. During boot,
uncaught errors, rejected promises and failed scripts/styles show a built-in recovery page immediately. Media
failures are logged and retain their existing placeholders. The watchdog resets on every phase start and end,
failing after 45 seconds without progress. A later `ready()` removes the failure page and reveals the app;
the recorded failure remains in diagnostics. Error forwarding is deduplicated for 60 seconds and capped at
ten distinct errors per minute on both sides.

`--safe-mode` is a launch-only option. The safe restart action releases the single-instance lock before spawning
the replacement. Source, development and serve-only launches refuse `app.restartSafe` with
`safe_restart_unavailable` and hide its button. Safe mode omits the normal module graph, refuses game-asset requests, skips background maintenance,
never creates Spine/WebGL renderers, and opens built-in Settings/report/log controls instead of restoring a page.
The screen identifies Safe Mode and instructs a normal launch to leave it. Saved consent and preferences remain intact.

Windows checks the Evergreen runtime before importing pywebview or creating a window, using Microsoft's
[per-user/per-machine registry detection](https://learn.microsoft.com/microsoft-edge/webview2/concepts/distribution#detect-if-a-webview2-runtime-is-already-installed)
in both registry views. The supported runtime floor is Chromium/WebView2 105; older/missing runtimes get a native
localized message with the download URL, even if report generation fails. The report path appears only when the file exists. Other native startup failures use the same report and
message-box path without depending on a web view. macOS uses a native alert; Windows uses MessageBoxW.

Each launch rotates the current log through `.1` and `.2` before service construction. Its first line records the
app/build, OS, web view, home-redacted data directory and safe-mode state. Exception hooks are installed before
opening the log. Failed creation, rotation or opening falls back to a temporary log, then stderr. The report and
Settings log viewer follow the active log path.


Outside layout editing, `student-hall-camera.js` projects the entire portrait composition and background through a
shared temporary camera, from 100% (the default view) to 300%. Wheel, pinch and animated zoom-out all stop at the
default size; the scene can pan only within its magnified bounds. UI controls retain their normal size. Camera
updates do not reload actors or mutate saved positions, individual scales or the layout-editing size slider.

A zoom eases to its goal (0.08 s); a pan has no easing, so the scene stays under the pointer or the fingers and stops when
they stop. The portraits' renderer steps the camera at the top of its frame (`flush`), so the portraits, the background and
the hit areas show one view in every frame; the portraits never trail the background while the scene moves.


## Release notes and feature tours

`update.js` retains the detected release snapshot. Release Notes uses the same `UIPopup_Sort` notice frame as
messages, with UILabel text, a scrolling body and an explicit release-page action. `release-notes.js` reads the body's
Markdown in the UI language: language sections are separated by a rule (`---`) and marked `<!-- lang: en -->`,
`<!-- lang: zh-CN -->` (GitHub hides the marks); an unmarked section is told by its script (0.6.0's body: English, a
rule, Chinese). The UI language's section shows, else its other variant (zh-TW reads zh-CN), else English, else the
first. Headings draw at 56 / 52 / 48, list items at 44 with a hanging bullet (plain message rows' `marker`, `indent`,
`gap`), other lines at 44; every line of the body stays its own line, as GitHub shows it, and a long one wraps rather than
shrinks. Emphasis, code, link and image marks go and their text stays; HTML is dropped, never interpreted, and nothing is
downloaded or installed. Missing or empty bodies show the empty-notes line.
A build ships its own notes as `assets/release-notes/<X.Y.Z>.md`, the text its GitHub release carries; `app.releaseNotes`
reads the running version's file, and Settings' prerelease update preview ("Release Notes" case) shows it in place of the
sample, so the notes can be read in the App before publishing.
The explicit validated page argument remains attached to that offer even if a later check changes backend state.

`tours.js` and `webui/tours.py` implement the local presentation framework for new-version
feature tours. The shipped registry is empty. Text-only pages are the default;
optional bundled PNGs may illustrate only the App's own UI, without game assets. The framework adds no startup
capture, asset scan or network request. Settings' General/version area replays all available versions. Navigation,
modal focus, disabled Previous, Close and Finish use the existing NGUI popup/control prefabs. The empty-manifest
path records launch history but performs no tour rendering. Drafts stay under `docs/tours/`, outside the package.
