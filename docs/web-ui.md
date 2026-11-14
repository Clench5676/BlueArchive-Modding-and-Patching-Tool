# Web UI

The GUI: a pywebview window showing a local web front end that reproduces the in-game Blue Archive UI. Game art is
read from the user's own install at runtime and never shipped with the program.

## Screens

Composed screens take their sizes, gaps and alignment from the design system ([`design-system.md`](design-system.md):
the scales in `ui-tokens.js`, the layout primitives in `ui-grid.js`).

- Screens: consent popup, Student Hall, student roster, Import Mod (Batch Import), student detail (Portrait, Memorial
  Lobby and 3D tabs), the switch popups (portrait, memorial lobby, 3D model) and the variant picker. The lobby is
  framed and touched like the game, plays its intro timeline when entered (a tap skips it; the settings' L2D tab
  turns it off) and plays its dialogue with voices and the lines in the UI language; the 3D tab shows the variant's
  chibi, exported from the install or a selected library Mod. A tab can remain available for a library-only Mod even
  when the installation has no corresponding original resource; application remains unavailable in that case.
- Nav entries without a web screen yet open the classic Tk GUI on that tab (`bamt --tab <name>`).
- `bamt` (and the compiled app) starts the web UI; `bamt --classic` starts the classic Tk GUI.
- Released builds look for a newer GitHub release at start and can update themselves (`updater.py`). The offer is
  the newest release above the running version that has this platform's build and its checksum; when none has them,
  it is the newest release, without an install button. The unset-preference default is on; an explicitly saved
  startup-update opt-out remains off.

## Student photography

The native fullscreen camera entry on the student detail opens the actual portrait and memorial renderers; it never
enlarges a thumbnail as its capture source. The centered 404:456 guide matches the student roster, optional thirds
stay outside output, and the shutter writes a lossless native-pixel PNG directly to the library. The mode button is
drawn only when the student has the other mode (`bridge.info()` tells `hasPortrait` and `hasLobby`). The camera's
controls reuse the game's prefabs and are laid out with `ui-grid.js`.

`photographs/` belongs to the movable/shared Mod library, not Preview Cache. Its atomic manifest keeps per-student
captures and App choices, IDs and SHA-256 digests; image publication rolls back if metadata cannot commit. Chosen
App photographs and configured/applied game photographs cannot be deleted (`photo_in_use`). The manifest is read per
entry: a record that cannot be used costs that entry only and is written back unchanged; a manifest that cannot be
read as a whole is never rewritten and the photograph RPCs answer `photo_library_invalid`. Save and App selection
hold only the manifest's own lock, never the services lock.

| RPC | Parameters | Result |
| --- | --- | --- |
| `photography.list` | `variant` | Captures, App choice, verified default URL, game intent/availability/error |
| `photography.removalInfo` | `variant, id` | What a deletion would affect |
| `photography.save` | `variant, png, mode, expression?, action?, source?` | Captures/App choice and saved ID |
| `photography.select` | `variant, id` (nullable) | Captures and updated App choice |
| `photography.delete` | `variant, id` | Updated gallery after protected deletion |
| `photography.export` | `variant, id, png?` | Lossless export of a capture |
| `photography.apply` | `variant, id` (nullable) | Actual writer job ID |
| `photography.restore` | `variant` | Default restore job ID; clears App choice after success |

Capture uploads are bounded to 48 MiB encoded PNG data and 4096 pixels per dimension; only the save RPC gets the
larger HTTP body allowance. Assets use `/asset/photography/<variant>/<id>.png` and
`/asset/photography/default/<install-id>/<variant>.png?revision=<resource-version>` (verified original bundles, so a
changed install cannot serve another install's default). Game Apply replaces only the selected named textures of the
Collection card portrait (`UICharacterCollectionPopulatorUnit/Grid/UICollectCard/Char_Portrait`), with lossless
RGBA32 at native dimensions; restoring one texture inlines its exact original GPU payload, and all-default
compositions restore original bundle bytes. Generated composites are internal managed `other` Mods under the
existing protected writer, rollback journal, official backups and master-switch manifests.

## Languages

- The UI languages are `i18n.UI_LANGUAGES`: zh-CN, zh-TW, en-US, ja-JP, ko-KR and th-TH, each shipped as
  `locales/<code>.json` with every en-US key of the web UI and the classic GUI (`tests/test_locales.py`). A language
  is offered once its locale file exists (`i18n.offered_languages`), each named in itself and drawn in its own fonts.
- Wording: game concepts take the official server's words (the Global client's Traditional Chinese and Korean, the JP
  server's Japanese, the Global English); Simplified Chinese follows Kivo's community wording.
- The first start takes the system language (`i18n.match_language`): Traditional Chinese regions get zh-TW, other
  Chinese zh-CN, Japanese ja-JP, Korean ko-KR, Thai th-TH, the rest en-US. `BAMT_LANG` overrides it.
- A string a locale file lacks falls back key by key (`i18n.fallback_language`): Simplified Chinese to zh-CN, every
  other language, Traditional Chinese included, to en-US (a Simplified string in a Traditional screen reads as a
  wrong translation, an English one as an untranslated one).
- Names come with the data per language (`i18n.js` `NAME_LANG`: zh-CN, zh-TW, en, ja, ko, th); the catalog names
  every variant in those languages. The lobby's lines follow the UI language (`LINE_LANGS`).
- Text fitting (`ngui.fitText`) shrinks, then wraps at separators and between words (`Intl.Segmenter` in the text's
  language), keeping the CJK line-breaking rules; `ngui.fitLabel` also widens the label to the fitted width. A
  control's caption is never ellipsized. Options side by side share one size (`ngui.fitGroup`), each option measured
  in its own `lang`. Text with no break to wrap at keeps the largest size under its floor at which it fits on one
  line; `fitGroup` breaks inside a CJK word only when no word-level wrap keeps the size at its floor.
- Fonts: the page ships Resource Han Rounded CN (`web/fonts`, OFL). It has Simplified glyph forms and no Hangul or
  Thai, so the html `lang` (set from the UI language) picks a stack in `app.css`: zh-CN and en-US keep the font for
  everything; the other languages keep it for Latin letters and digits only and draw the rest with the language's
  system font, the full font last. Text in another language than the page's carries its own `lang` and draws with
  that language's stack. `ngui.textWidth` measures with the same stack the text draws with. The classic GUI switches
  its own text widgets the same way (`Theme.use_language`).

## Process layout

The App lives in `src/bmpt/`; the upstream engine and classic GUI/CLI remain in `src/ba_modding_toolkit/`.
The App imports the engine explicitly. `bmpt/__init__.py` is empty so the engine's fork-added hooks can import
small App modules without initializing the web UI. The launcher (`bmpt.launcher`), `python -m bmpt` and
`python -m bmpt.webui` register `bmpt/locales` with the engine before dispatch; classic strings remain in
`ba_modding_toolkit/locales`. The legacy `python -m ba_modding_toolkit` command still dispatches the App.

```
pywebview window (main thread) ── HTTP ──> WebServer (threaded WSGI, 127.0.0.1, random port)
                                              /              static front end (bmpt/web)
                                              /api/rpc       calls, JSON in and out
                                              /api/events    Server-Sent Events
                                              /asset/...     game assets read from the install
                                              /api/classic/* the classic window's channel (its own token)
                                              /api/instance/raise  a second start (the raise token)
```

The front end only speaks HTTP and SSE, never the pywebview `js_api`, so it runs in any browser during development
(`bamt --browser`, or `--serve` for tools) and tests drive the WSGI app without a window. Native features (file
dialogs, drag-and-drop paths, reveal in the file manager) run in Python; dropped paths reach the page as an event.

Every request must carry the session token (cookie `bamt`, set when the page is first opened with `/?t=<token>`, or
header `X-BAMT-Token`), and the `Host` header must be `127.0.0.1:<port>` or `localhost:<port>`.

## Packages

```
src/bmpt/
  config.py      Tk-free settings: same config.toml groups and keys as ba_modding_toolkit/gui/configs.py
  gameassets/    UI atlases, UI textures, prefab layouts, collection art and Spine skeletons from the install
  catalog/       students (families) and their variants, names per language
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
  (`bmpt/errors.py`); the front end shows the locale string `webui.error.<code>`. An error passes every parameter
  its text names, above all `file` (`tests/test_error_params.py` scans for omissions). Diagnostics go to
  `logging.getLogger(__name__)`, never `print`.
- **JSON-ready results.** Dataclasses with `to_json()` returning plain dicts; paths as absolute strings, dates as
  ISO strings.
- **Texture decoding.** Every decode of a `Texture2D` read from a bundle goes through `texture_guard.texture_image`
  (or `check_texture` first). A texture is refused unless its dimensions are positive and at most 16384, its format
  is one UnityPy decodes and its image data covers the declared size. A refusal is
  `ServiceError("bundle_invalid", file=...)` where the bundle file is known, else `TextureRefused` (a `ValueError`);
  callers treat it like any other unreadable texture.
- **Bundle sizes.** `Bundle.load` is the only place the App hands a bundle file to UnityPy, and it first runs
  `bundle_guard.check_bundle`: it reads the UnityFS header and block table (`unityfs.read_layout`, which decompresses
  no data block) and refuses a bundle whose file size, single block, total declared uncompressed size or block table
  is above a ceiling. A refusal is `ServiceError("bundle_invalid", file=...)`, reported like any other unreadable
  bundle. Each ceiling is at least four times the largest value among the official game files
  (`tests/test_bundle_guard.py` measures them on the verified view).
- **Game assets.** Two classes. *Light* (atlas sprites, atlas metadata, UI textures, prefab layouts, bundle indexes):
  kept in memory; written to the disk cache only after the user agreed (`consent = true`). *Heavy* (collection art,
  Spine skeletons, lobbies, 3D models): memory, plus the default-on **Preview Cache** setting, which permits lossless
  exports and generated PNG thumbnails in the user cache directory. Browser responses remain
  `Cache-Control: no-store`; voices remain memory-only.
- **Disk cache.** `user_cache_dir()/<resource_version>/...` (the platform's user cache directory for
  `bluearchive-modding-patching-tool`), clearable from the UI. It keeps the three most recently used resource-version
  folders; a new version's first successful write deletes the other version folders, never links or the `previews/`
  store. The old `BA-Modding-Toolkit` user folder is renamed automatically on first start.
- **Preview cache.** On by default, preserving an explicitly saved off setting, independent of the light-asset
  consent. Stored under the cache directory's `previews/`, bounded to 2 GiB with least-recently-used eviction.
  Entries are keyed by installation/resource/export identity, source revisions and model dependencies; stills
  additionally by renderer parameters, background/framing content and the renderer's own code (every still names its
  renderer in `preview.cacheGet` / `preview.cachePut`, and the backend keys it by the content hash of that renderer's
  pixel files, `webui/stills.py`; `tests/test_still_renderers.py` holds every import of those files to a pixel or
  non-pixel list). Corrupt entries rebuild; a PNG above 2 MiB stays memory-only. **Clear Cache** deletes stored
  previews and backend memory without interrupting the active scene; turning caching off keeps stored files until
  Clear. Preview cache RPCs have a 15-second timeout and fall back to live rendering.
- **Resource version.** SHA-1 of `Catalog/Windows/catalog_Windows.hash` when present, else of the sorted
  `(name, size)` listing of the resource folders. The cache key; a game update starts a new cache folder. The UI
  atlas index additionally keys on the current atlas bundle inventory (resolved paths, sizes, modification and
  change times) and revalidates known atlas files on each request, so adding bundles to a partial install
  invalidates the index even with the same catalog hash. Empty indexes, failed reads and changes during a scan are
  logged and never cached; NGUI rejects malformed atlas metadata and retries on a later render.
- **Stills being made.** A picture with a placeholder (`clipImage` with `placeholder`) holds its place with the
  game's loading ring, shown after 0.2 s so a still that comes at once never flashes it; the placeholder text shows
  only when the still fails.
- **Read-only data folder.** The services test the data folder at start by writing and removing a real temporary
  file (`app.info.dataDirWritable`); no fallback folder. A write refused while that folder cannot be written answers
  `data_dir_readonly` (through `Services.translate_error`) instead of `internal`.
- **User data.** The mod library and UI state live in `utils.EXE_DIR` (the portable data directory). Several
  instances may share it: a change to `webui.json` is applied to the file as it is at that moment, read again under
  the shared `webui.json.lock`, so no instance erases another's keys. On Windows a replace that meets another
  program's handle is retried for up to a second. The last good file stays as `webui.json.prev` before every write;
  an unreadable file is read from that copy and kept as `webui.json.bad`. `config.toml` never stops the start: a
  group of the wrong type reads as missing, an unparsable file starts on defaults (copied to `config.toml.bad`
  first), and the 2-second poll for the classic GUI's saves never applies a read that failed. The user's own aliases
  of the students are kept there too (`aliases`), never in the program folder.

## gameassets

```python
class GameAssets:
    def __init__(self, resource_dirs: list[Path], cache: AssetCache): ...
    resource_version: str

    # light
    def atlases(self) -> dict[str, dict[str, dict]]           # {atlas: {sprite: {x, y, width, height, borders, padding}}}
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
                                                               # sound events, post-processing, particle and mesh
                                                               # effects, page materials, pins and intro
    def lobby_fx_texture(self, core: str, tex_id: str) -> bytes # a texture lobby_info's "fx" lists, as PNG
    def lobby_sound(self, core: str, clip_id: str) -> bytes   # a clip lobby_info's "sounds" lists, decoded to WAV
    def model_cores(self) -> set[str]                          # dev names with a chibi (character-<dev>-_mxload-*)
    def model_glb(self, core: str) -> bytes                    # the chibi as one GLB (model.py)
```

The collection art is read where the game loads it (CostumeExcel's texture paths, the
`uis-01_common-14_charactercollect-*` bundles); the index holds names and bundle locations only, in memory.
`/asset/art/bg/` serves only names starting with `BG_`, `/asset/art/portrait/` only `Student_Portrait_`.

`model_glb` reads prefab bundles and resolves their external CABs on demand. Model, memorial and office graphs share
the revision-checked `gameassets/cabs.py` resolver; the optional preview cache persists bounded, checksummed CAB
metadata, confined to the installation and checked by device, inode, size, mtime and ctime. The shared `unityfs.py`
parser reads only header/block info and uses the declared bundle length to ignore CRC-correction tails. The GLB
carries what the game's shaders need (materials, constants, keywords, outline, blend state, samplers, clip toggles);
`web/js/chibi-materials.js` ports the game's shaders from their compiled programs, and `model.js` opens at the
formation screen's view with the formation screen's light, both read from the game's own data and checked against
in-game screenshots. Input is wheel/pinch zoom, pan and orbit with eased framing (`attachInput`).

`AssetNotFound` (a `KeyError`) when a name is unknown. Layout nodes use the schema
`{name, active, pos[x, y], scale[x, y], rotZ, widgets[], children?}`; atlas and texture references are resolved to
names.

**Button states.** The game's buttons carry no NGUI button colours: a disabled or locked button is a node of its own
that a controller shows instead of the normal one. The layout gives these controllers a `states` object, each field's
node as a path below the controller's node (`""` is the node itself, `null` an unset field):

| Controller | Fields |
|---|---|
| `OpenConditionButtonController` | `Enable`, `Disable`, `Prohibited` |
| `UIButtonEnableSwitch` | `enableButton`, `disableButton` |
| `MXDualStateButton` | `NormalButton`, `DisabledButton` |
| `ButtonActivator` | `activeObject`, `inactiveObject`, `button`, `contentsLockObject` |

The lobby's bottom menu (`UILobby`, `Front/Bottom/Menu/BG/Grid/<entry>`) holds the game's disabled look for the
greyed navigation: `Disable` shows the icon sprite at `#797979` with `Common_Icon_Lock` over it; the label is outside
both states.

Cached layouts carry the export's format in their key (`layout-v2/<root>`, `_LAYOUT` in `service.py`); a change to
the export raises it, so older cached layouts are exported again and no other cached asset is dropped.

## catalog

```python
@dataclass
class Variant:
    key: str                  # file_id lower-cased: "hoshino", "hoshino_swimsuit", "ch0241", "np0045"
    file_id: str              # as in the character map: "CH0241"
    family: str               # Family.key
    names: dict[str, dict]    # {lang: {"full", "short"}}
    playable: bool            # a playable student (on some server)
    order: int                # release order inside the family
    spr: str | None           # Spine portrait core, when the install has it
    lobby: str | None         # memorial lobby core, when the install has it
    bg: str | None            # collection background texture name
    portrait: str | None      # collection portrait texture name (None: the front end renders a bust)
    date: str | None          # date of the portrait bundle
    page_id: str | None       # the student page on Kivo, for lobby lines
    recruitable: bool         # on the selected server (see below)
    model: str | None         # 3D chibi dev name, when the install has it
    schale_id: int | None     # the game's character id (SchaleDB's), for the Global server's lobby lines
    aliases: list[str]        # the CN server's zh-CN names: the search matches them, the UI never shows them

@dataclass
class Family:
    key: str                  # the base variant's key
    names: dict[str, str]     # base name per language
    school: str | None
    variants: list[Variant]
    aliases: list[str]        # the CN server's base names

class Catalog:
    def __init__(self, game, character_map, data=<package data>, server="global"): ...  # "global" | "japan"
    def families(self) -> list[Family]
    def family(self, key: str) -> Family
    def variant(self, key: str) -> Variant
    def variant_for_core(self, core: str) -> Variant | None    # "ch0241_spr" or "ch0241_home" -> Variant
    def core_map(self, spr_cores, lobby_cores, special_cores=()) -> CoreMap  # any install's cores -> variant keys
    def reload(self) -> None                                    # parse the data again, rebuild the snapshot
```

`core_map` runs the roster's own core rules on another install's core names, so the library keys a core the same way
in every game folder: a portrait core's key is the core less `_spr`; a lobby core belongs to the installed variant
whose key or core alias plus `_home`, `_home_gl` or `_home_teen` names it, a direct file-id match first. The shipped
data and the character map are parsed once and kept; every build works on a copy, and `reload()` keeps the old parse
when the data is invalid.

Special Spine slots use `special:<lower-case core>` keys and the lobby workflow. Discovery accepts named
`spinebackground` bundles and nonstandard cores in `spinelobbies` and `spinecharacters`; shared `_mxcommon` bundles
are excluded. The category is retained on the variant (`spineCategory` in RPC data) and kept separate from student
portraits and memorial lobbies; import, apply, restore, configuration, the global toggle and entry scans classify
these files by category as well as core, while parts still match by prefix and resource type. Roster cards, chips,
the picker and search include lobby-only variants; container paths under `SpineBackground/<core>/` identify the
owning scene, and conflicting scene containers are refused. Known role tokens (workpage, daytime, night, task, cafe)
have locale labels; unknown names keep their exact core.

Sources: the character map CSV the app already downloads (`BA-Characters-Internal-ID.csv`), the curated data file
shipped in the package (names, backgrounds and portrait names, generated by `scripts/build_catalog_data.py`), and the
game scan (which skeletons exist, dates). Skeletons missing from the map become their own family, named by file id.
Playable students take SchaleDB's names per server; NPCs and story sprites the character map's columns and
`catalog/data/naming.json` (its name table and glossary give each language's word, from the game where it names it,
else a translation, and say which). zh-CN shows Kivo's community wording; the CN server's replaced wording stays
findable through `aliases` (never shown). Names follow the variant name format: base name plus the variant in
brackets, never a middle dot.

Recruitable follows the server of the selected game folder: a playable variant is recruitable when its collection
portrait is installed; without one, the data file's `released` flags (from SchaleDB `IsReleased`) decide; with no
flag either, the variant is recruitable only when the install has no collection portraits at all. Not-recruitable
playables stay in the roster and sort after the recruitable variants of their family.

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
transaction. Switch 3D Model offers Mods from any of the slot's cores and the student's other models as costumes
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
| `classic.opened` / `classic.ready` / `classic.closed` | `{pid}` / `{pid, tab}` / `{code}` (see Classic window) |
| `classic.queueDone` | `{submitted, dropped}` once the writes queued for the classic window ran |
| `tray.toggle` / `tray.open` | the tray submitted a toggle, or brought the window up on a view (see Tray) |
| `config.changed`, `app.busy` | `{}` |

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

## One instance per data directory

`main()` takes an OS lock on `<data dir>/instance.lock` for the process lifetime (`webui/instance.py`) and names
itself in `instance.json` (`{pid, port, raise_token}`). A second start finds the lock held, posts
`/api/instance/raise` with the raise token (header `X-BAMT-Raise`, never the session token) and exits: the first
instance shows and raises its window. With `--serve` a second start refuses with a message. `app.quit` is refused
with `busy` while a writer job is queued or running, the same rule as the window's close button.

## RPC

`POST /api/rpc` with `{"method": "...", "params": {...}}`, answered with `{"result": ...}` or
`{"error": {"code", "params"}}`. Methods whose result is a job id run on the worker (see Jobs); details of what each
one changes are in the sections above.

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
| `photography.*` | see Student photography |
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
| `/asset/model/<variant>.glb` (`?source=` `live`, `official`, `mod:<id>`, `import:<key>`, `variant:<key>`) | heavy |
| `/asset/voice/<variant>/<clip>` (a lobby voice relayed from Kivo) | heavy |
| `/asset/office/<day|night>/layout.json` and its texture routes (the Hall's office background) | heavy |
| `/asset/photography/...` | see Student photography |

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

## Front end

`ngui.js` (prefab renderer), the roster, the detail screen, the popups, the consent popup and the lobby player, as ES
modules under `web/js/`, one page with a hash router (`#/roster`, `#/student/<family>/<variant>`). Data comes from the
RPC methods, art from `/asset/...`, strings from the locale files. Invisible buttons over drawn controls carry their
accessible names from the locale files; a control that picks one of several carries `aria-pressed`.

### Student list

`roster-order.js` decides which cards the list shows and in what order (pure functions, tested in Node from
`tests/test_roster_order.py`); `roster.js` draws them and `display.js` is the game's display settings popup.

- One card per variant, or per family with "Group Variants". Cards with mods first, the rest after the "No Mod" bar;
  favourites lead each group whatever the sort.
- Segment filters, the display settings' filter groups (options in a group add up, groups narrow) and the sort
  (recently viewed, recently added, name with the UI language's collation, mod count, default). All of it is saved
  (`settings.set roster`); the search lasts the session.
- The search matches every name language, the base names, the aliases (the CN server's and the user's own) and the
  file id, ignoring case, width, kana and separators; every word must match.
- Keyboard focus moves the list through its own scroll position; the left and right arrow keys step through students
  as in the PC client (`step-keys.js`, tested in Node), one step at a time, paced by the screen, inert with a modifier
  held, in a text field, under a popup, in a fullscreen view or during a job.
- Back from the detail screen, the list is built behind the loading cover: the visible rows finish their portraits and
  frames before reveal, and pictures are decoded first (`NGUI.decoded`, `image-decode.js`, tested in Node) — WebKit
  lets go of blob images a hidden screen draws, so without the wait the first frames painted bare.
- A visit to a variant (`ui.visit`) is mirrored in the page's roster data, so "Recently Viewed" updates without a new
  `roster.list`.

### Quit

The student list quits the app as the PC client's back key does in the lobby (`quit.js`, tested in Node): the top
bar's round back button and Escape on the list open the game's quit prompt (Cancel / Confirm; Enter and Space confirm
while the prompt holds the focus, per the PC client's key mapping). Escape is left alone when another handler took the
key, while an input method composes, for a held key's repeat, with a modifier, while any popup is open, behind the
loading screen, and on any screen but the list. Confirm calls `app.quit` (refused with `busy` while a writer job
runs); in browser mode the page shows Closed after the server stops.

### Bottom navigation

`nav-items.js` (tested in Node) lists the bottom bar's entries and decides what a click does; `nav.js` draws them.
Student Hall (`#/hall`) is the root page, followed by Students and Batch Import; `route-history.js` records committed
page entries in memory (failed transitions do not become history; direct-entry routes fall back to Student Hall). An
entry is greyed exactly when it has a classic tab and no web screen: it keeps the game's disabled tint (read from
UILobby's locked entry, else NGUI's default grey), stays a focusable button (`aria-disabled`), and a click opens the
hand-off prompt ("Not migrated yet, opens in the classic UI", with Don't Ask Again; the pref `classicPrompt` skips
it). The hand-off is `app.openClassic {tab}` with the corner indicator until the window is up; refusals show as a
notice, one hand-off at a time. Without a game folder the no-game screen shows the navigation too, with plain hints
and Detect / Choose buttons; Settings cannot open without a game folder and says so instead of failing silently.

### Student Hall

`student-hall.js` renders portrait-composition and memorial-lobby modes with the game's `UILobby` controls and
`UILobbyCharacterSelectPopup_Slot` editor cards; `student-hall-fullscreen.js` owns the immersive state. Portrait mode
uses one shared canvas and camera; each instance has independent animation state, position and scale. Layout editing
picks actors by posed attachment vertices against cached atlas-page alpha (decoded off the main thread; until a page
is read its attachments count as opaque); dragging moves, hover controls remove, reorder and mirror, and a selection
pins the toolbar (a gold outline from the game's `SelectLine`). Each portrait toolbar has its own size slider and
Reset; a global size slider (25–150 %) edits the draft. The Add Portraits modal lists library portraits with roster
semantics. Layout changes stay drafts until Save. Memorial mode has an ordered playlist with pointer-capture
reordering (touch holds, edge scrolling, cancellation) and runs each source through the existing lobby renderer.
`student_hall.py` validates state stored under `student_hall` in UI preferences; revision checks prevent a stale
window from overwriting another's arrangement, and dirty exits prompt. `student-hall-dialogue.js` draws the game's
`ChatDialog/Talk` balloon for portrait dialogue, with a Hall-wide turn lock, bounded caches and per-click aborts; no
voice is written to disk. `student-hall-camera.js` owns a temporary non-editing scene view (100–300 %; wheel and
pinch zoom, two-finger swipes pan, reusing the detail viewer's wheel classification); camera updates do not reload
actors or mutate saved state, and the renderer steps the camera at the top of its frame so portraits, background and
hit areas show one view. The office background comes from the game's layered `LobbyCommon` / `LobbyCommonNight`
prefabs (`gameassets/office.py`, served losslessly at native resolution); a saved available choice wins, else the
daytime office, else a collection background.

### Portrait

The detail screen frames the portrait like the game (`portrait-view.js`): the skeleton's origin sits on
UICharacterDetail's `CharacterPos`, never on the skeleton's bounds, so a long weapon neither shrinks nor shifts the
character. **Visual mode** (`visual-mode.js`, framing in `visual-view.js`): the nameplate's rotate-phone button opens
the game's UICharacterVisualMode — the UI slides out, the portrait glides into the whole art over the collection
background with the game's light patterns, press-drag moves, wheel and pinch zoom, and the game's own buttons
(sampled from its animation clip) slide in. Leaving glides back exactly; without the prefab the portrait still goes
fullscreen with the tool's exit button. While any fullscreen view is open the slid-out UI is inert and the focus is
given back on leave.

### Popups

The detail screen's popups are drawn on the game's popup frame (UIPopup_Sort) with its dark backdrop (`BACKDROP` in
`ui-tokens.js`); a click on the backdrop or Escape closes them. The shared module (`popup.js`, tested in Node on a
stand-in DOM) reads its prefabs once and provides:

- **Hosts.** `#popup` and `#picker` inside the detail screen; `#prompt` and `#dialog` on the stage, so a popup shows
  on any screen. Each message popup is a layer of its own in `#dialog`, so one opened from another shows over it.
- **The stack.** Every open popup is on one stack; Escape goes to the topmost only, and a popup that must be answered
  with a button takes Escape and does nothing. `anyOpen()` and `whenIdle()` let views wait. The settings register
  like every other popup.
- **Focus.** One shared trap: a popup takes the keyboard focus when it opens, Tab stays inside the topmost and wraps,
  the focus is pulled back when lost, and it returns to what had it once the popup closes.
- **Refusals and failures** (`notify`, `notice`). An ordinary refusal (the shared error registry's `level: "notice"`)
  is the neutral notice with Confirm only; any other code is a real failure: title Failed and the Error Report
  button. Failed notifications append the display reference (`popup.withCode`).
- **`popupFrame(title, w, h, {...})`**: the shared frame builder; width classes S/M/L/XL and their anatomy are the
  design system's ([`design-system.md`](design-system.md)). **`message({...})`**: lines of text, optional line
  buttons, checkbox, `plain`, `scroll`; resolves with `{button, checked}` however it closes; without the frame's
  prefab (no game folder) the same message draws plain and answers the same way. **`listPopup({...})`**: details on
  the left, cards in a framed list on the right, single or multi selection, optional roster tools.

**Start sequence** (`main.js`, `Popup.sequence`), run once per session when a game folder is known: the start popups
come one at a time after the first screen is revealed, each only when due — the read-only data folder notice, the
asset consent, the Spine licence notice, the scan consent; then the release update check (its own background check,
never holding startup; the offer waits for open popups and loading work; download and install stay in its popup);
then the game update impact gate (see Mods already in use); then the prompts of mods back to official and mods
outside the library; then a newer version's release notes once, then the feature tour.

**Spine licence.** The notice says that continuing confirms the user holds a Spine Editor licence; one Confirm, no
close button. Confirm saves `prefs.spine_consent = true` once (`app.spineConsent`); an earlier Yes never shows it, a
stored No stays false and shows the notice until Confirm. `spine-runtime.js ensureSpine` loads the runtime only after
acknowledgement; the first screen uses a static portrait where one exists. Settings' General tab keeps a View button
that opens the same notice without changing state.

**Switch popups** (Switch Portrait / Memorial Lobby / 3D Model). The sources (`switch-sources.js`, tested in Node):
the variant's own files and its added mods; for the portrait also the other variants' own and added ones (other
variants' lobbies are not offered — untested in the game). Save changes only the preview (persisted per install and
variant in `webui.json`, exposed as `previewSources`); Apply updates the manifest through `actions.apply` (with Mods
off it only updates the intended source). On an overwritten kind, Original is greyed with the Overwritten treatment;
greyed cards can still be previewed. Delete always confirms with the mod's name and explains affected entries.
Duplicates show Already in the Mod Library; exact outside game content is adopted without reapplying. Unseen mods
mark their variant chip, tab and roster card; opening a switch popup marks what it offers as seen (`mods.seen`,
independent of the library's legacy flags). **Aliases** (the pencil on the nameplate): the user's own search aliases,
edited in the game's input rows, IME-safe. **Details** (the info bars' button): the shown tab's game files, one row
per bundle with size, date, CRC state and contents.

### Bar motion

`chrome-motion.js` moves the top bar and the bottom menu as the game's lobby moves them (UILobby's `UIAni_Lobby`
clip, kept as `LOBBY_ENTRY` in `ui-tokens.js`): both bars come in on the clip's curves once the first screen shows;
immersive scenes (detail and Hall fullscreen, photography) send them away, a pointer near the window's edge brings
that edge's bar back, and `corner-layout.js` places corner controls around the revealed bars. With the native macOS
toolbar the motion also drives the window buttons (`window.motion`, `CocoaHost.slide`).

### Design tokens

The shared numbers and colours live in `web/js/ui-tokens.js`, each named after the prefab node it comes from; the
scales, controls, popup anatomy and alignment rules are documented in [`design-system.md`](design-system.md)
(`tests/js/ui_tokens.test.mjs` pins the call sites, `tests/test_ui_tokens.py` the prefab values).

### Enable Mods switch

One kill switch for every game folder (`mod-switch.js`; the pure parts tested in Node). It never changes the
configuration: Off puts every entry of every known install back to official, On writes the configured sources again
(see library). It is in the top bar of the student list and the detail screen, and in Settings' Mods tab.

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
- **The tray's events** are followed like the page's own toggle (see Tray).

### Troubleshoot Mods (pre-release builds)

A Settings row shown and answered only where `prereleasePreviews` is true, to find which mod the game refuses one at
a time (`troubleshoot.js`, `library/troubleshoot.py`): `mods.applyOfficial` turns everything official while saving
the configuration as `profiles.json`'s `troubleshoot`, `mods.restoreConfig` puts it back, `mods.troubleshootState`
says whether one is saved. Both results carry `check`: every bundle Steam's file list names, verified by size and
SHA-1.

### Mods already in use

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

### Loading screen

`loading.js` draws the game's own loading screen: UILoading's black backdrop and UIRepeater in the bottom-right
corner, animated in `app.css` from the game's clip. Before asset consent, covers use neutral system text and no game
assets; the server selects the initial document's style from consent and the disk cache, so a cached launch is
stylized before modules arrive. `scene(fn)` covers startup, navigation, student steps and detail tabs (fade to black,
indicator, reveal; it warns after 15 s and waits without a client deadline for `family.get` and `roster.list`, since
a writer job can hold the library lock; a later navigation supersedes the wait). `wait(promise, {label, cap})` shows
the indicator alone for other waits. The detail screen resolves `show()` only when every part is ready and swaps
changes in one frame. Startup phase durations are recorded in the session log and `app.timings`.

### Pointer and touch effect

`cursor.js` gives the page the PC client's own pointer (`PCIcon_MousePoint`, a 32 CSS px cursor with the texture as
its 2x image) and `touch-fx.js` simulates the touch effect the game spawns on every press (two spinning rings, a
flash, triangles and a trail, drawn on two canvases over the window in linear light; pure functions tested in Node).
Both read their textures through the light texture route; without a game the system cursor stays. WebKit drops a
cursor whose box would cross the window's edge, so smaller cuts of the image follow in the list and the pointer is
cut at the edge instead of reverting to the arrow. With `prefers-reduced-motion`, a drag leaves no trail.

### Memorial lobby

`lobby.js` frames and plays the lobby, `lobby-touch.js` adds the game's touch interaction in fullscreen. The player
reproduces the game's rendering, read from the game's own data rather than fitted by eye; the subsystems, each in its
own module:

- **Linear light** (`linear.js`): the game blends in linear colour space, so each lobby player samples its atlas
  pages as sRGB and blends into an sRGB framebuffer, slot colours decoded per page shader (`slot-colours.js`).
  Without WebGL2 the player draws in plain sRGB as before.
- **Draw order** (`lobby-stage.js`, `skeleton-parts.js`): all of a lobby's skeletons and effects draw in one sorted
  order (sorting order, render queue, back to front) into the bottom player's context, as Unity sorts them.
- **Post-processing** (`lobby.py` reads the prefabs' URP volumes; the linear pass applies Panini projection,
  chromatic aberration and the colour grade as URP 12 does, placed in the game's screen). The Settings L2D tab's
  `lobbyPost` (on by default) is the PC client's PP option.
- **Particle effects** (`gameassets/particles.py`, `particles.js`, `particles-gl.js`): the prefabs' ParticleSystems
  simulated and drawn with the game's DSFX shaders ported from their compiled programs, in linear light, sorted with
  the skeleton parts; emission, shapes, curves, sub-emitters, trails and ring buffers follow Unity's rules. Cost is
  about 0.2–0.6 ms a frame; everything is freed with the lobby. Without WebGL2 nothing is drawn.
- **Mesh effects** (`gameassets/meshfx.py`, `mesh-fx.js`): the few prefabs with plain MeshRenderers (lens dirt,
  light quads, water), drawn with the particle programs, Animator loops included.
- **Skeleton materials** (`gameassets/spine_materials.py`, `skeleton-materials.js`): a page's shader decides how its
  slots draw (the game's multiply, noise-flow and portrait-noise page shaders ported); slot blend modes reach the
  screen only through BlendModeMaterials replacement materials, as in the game.
- **Pinned bones** (`gameassets/pins.py`, `skeleton-pins.js`): SkeletonUtilityBone overrides applied after the
  animation; the drag targets are such bones.
- **Intro scenes, clip endings, idle clips, clip sounds** (`gameassets/intro.py`, `lobby-sync.js`): the intro
  timeline is played as the game's PlayableDirector does; a clip's ending fades per its finish mode; idle pools play
  each clip on its own track with the game's random-timing waits; a clip's sound keys play through the lobby's shared
  audio events (decoded in memory, following the voice volume). Not reproduced: a lobby's ambient loop.
- **Zoom**: fullscreen opens on the game's 16:9 view and zooms in to 2x. The zoom-out limit is the Settings L2D tab's
  row: *Game View*; *Full Art* (the default: the widest 16:9 view inside the painted art, measured in background
  slices over every pose the lobby rests in, so a dialogue that moves the scene never shows a blank edge);
  *Unlocked* (the box of the painted art, parked parts left out); *Custom* (1.0–4.0 game views across, a slider).
  Past the painted art the lobby shows what the game's camera clears to. The measurements are kept for the session.
- **Input**: press and drag (head pat, eye follow, drag boxes); wheel and pinch zoom at the pointer; a two-finger
  swipe pans; a right- or middle-button drag pans. A wheel is told from a trackpad by its step sizes, per browser
  engine; one scroll keeps the kind of its first event. With `--debug`, the page sends the fullscreen lobby's input
  events through `debug.trace` so a trackpad problem can be reproduced from the log.

## Classic window

The bottom-bar entries without a web screen, and Settings' More Settings, hand over to the classic Tk GUI
(`webui/classic.py`, its child side in `gui/control.py`). It runs in a process of its own on both systems: Tk and
pywebview both need the main thread on macOS, and a crash in a classic tab never takes the web UI with it.

- **One window per data directory.** `ClassicWindow.open(tab, may_start)` starts the program again
  (`--classic --control`, then `--tab <name>` or `--settings`) and waits up to 30 s for its `ready`; an exit or
  silence first is `classic_failed {exit_code}`. With a window open, a click sends the tab and a raise; while a
  classic task runs, only the raise. No window starts while a job writing the game folder is queued or running
  (`busy`); the busy check and child registration share `ClassicWindow.starting()` with each game writer, so either
  the writer is pending first and the window is refused, or the child is registered first and the writer checked
  against it.
- **The channel** is the web UI's own HTTP server, not stdio: `BAMT_CLASSIC_URL` and a one-time `BAMT_CLASSIC_TOKEN`
  go to the child, which long-polls `GET /api/classic/control` for its lines (`place`, `raise`, `tab`, `settings`)
  and posts `POST /api/classic/report` (`ready`, `shown`, `busy`), both with the child's token in `X-BAMT-Classic`.
- **Placement.** The child starts transparent; after `ready` the parent places it centred on the BAMT window inside
  the work area, hands the foreground over, then raises. With no raise 5 s after `ready`, the child shows itself.
- **Exit.** A watcher thread waits for the process and publishes `classic.closed {code}`. The child's stdout and
  stderr go to `classic.log` in the data directory.
- **`classic.json`** (`{pid, exe, hwnd, created}`) names the open window, so an instance started later still refuses
  game-folder writes and can raise it. `find_classic()` takes the pid only while it is alive with that creation time,
  runs this program's classic GUI and is neither this process nor its parent; any other file is stale and removed.
- **The queue** (`ClassicQueue`): in memory only; at most one toggle and one action per entry (the latest wins). Each
  item keeps its call, the pins and the size and date of its entries' files; `run(host)` submits each item that still
  holds after the window closed and the scan ran, and lists the dropped ones with their errors.

## Tray

The tray icon (Windows) or menu bar icon (macOS) of the app window (`webui/tray.py`), shown while the preference
`tray` is on; `traySupported` only in the app window on those two systems. Closing the window quits the app and the
icon goes with it.

- **The menu**, rebuilt from the last `mods.state` each time it opens (`tray_menu(snapshot, strings)`, pure): Enable
  Mods (checked while on; greyed with a second disabled line naming the reason or the running work), Back to
  Official: n while the last scan found drift, a separator, Open and Quit.
- **Enable Mods** submits `mods.toggle` at once; the window comes to the front instead on the first toggle ever, and
  afterwards on a refusal or a result with failed entries. The page follows `tray.toggle` as its own toggle and
  `tray.open` for the explanation, an error or the drift prompt.
- **Never blocking the UI thread**: the menu reads only the last `mods.state` the tray kept (it observes the event
  bus, not counted as a page); every click runs on a thread of its own.
- **macOS** (`CocoaTray`): an `NSStatusItem` on pywebview's own loop, touched on the main thread only; the image is a
  vector template the menu bar tints. **Windows** (`WinFormsTray`): a `NotifyIcon` on pywebview's WinForms thread;
  the icon is loaded at the small icon size and disposed when the form closes.
- **The icons** (`assets/tray`, the project's own art, never a game asset) are built by `scripts/tray_icons.py` from
  their SVGs (`--check` reports drift, also a test).

## Tests

- `config`, `catalog` and `library` with fixtures and temporary folders (`library`: a fake game folder of header-only
  bundles; its end-to-end checks copy a few real bundles into a temporary folder, never writing into the install).
- `gameassets` and end-to-end checks against a real install; skipped unless one is found (`BAMT_TEST_GAME_DIR`, or
  `game_paths.find_game_dirs`).
- `webui` through its WSGI app, without a window; the tray's menu and controller with fake services.
- The front end's pure parts in Node (`tests/js/*.test.mjs`, each run from its `tests/test_*.py`) on a stand-in DOM
  with the en-US strings.
- The design tokens: `tests/js/ui_tokens.test.mjs` (the tokens, no retired literal in any web source, caption ranges
  through every locale) and `tests/test_ui_tokens.py` (the tokens against the prefabs of the development view;
  skipped without `BAMT_TEST_GAME_DIR`).

### Lobby camera framing

- **Entry.** In the fullscreen memorial lobby a visual-mode button (`lobby-camera.js`) enters the framing: the lobby
  rests, and Back, Reset and the photography shutter replace the fullscreen controls. The wheel zooms at the
  pointer's height, any drag moves the view up or down; the view stays 16:9 and centred across.
- **Limits.** `measureFraming` paints every pose the lobby rests in; a cell outside the game's view that some pose
  leaves unpainted may not be shown. Zoom-in stops at 2x the game's view.
- **RPC `lobby.camera.save`** `{variant, lobby, source, view, scale, offset}` starts a library writer job. The saved
  camera is a generated generic mod of the lobby's own UI prefab bundle (`library/lobby_camera.py`): the prefab's
  scale targets are reframed through a camera node, every other object byte for byte, saved with the CRC fix and
  checked after; a prefab whose structure does not fit is refused (`lobby_camera_unsupported`). The intro is held at
  the official framing by a track of the prefab's own copy of the intro timeline (`library/lobby_camera_intro.py`);
  the few prefabs with an Animator on the root keep the older framing (targets scaled directly).
- **The choice.** The App lists a camera as a lobby mod of its variant (`camera: {source, number, scale, offset}`),
  named after its source, in Switch Memorial Lobby; rename and delete as for mods. Apply writes the source's art
  through the lobby's own apply, then the prefab through the generic one; any other lobby choice puts the prefab back
  to the camera's base. Before a master-switch plan and a camera's apply, a camera framed on an official prefab the
  game replaced is made again on the new ones in place.
- **Preview.** `/asset/spine/mod/<camera>/` serves the source's art and the camera's own prefab as `lobby.json`; the
  official route reads the official prefab while a camera holds the game's, so no other choice's preview shows a
  camera's framing.

## Lobby lines

Online data, fetched on demand and kept in memory only (never written to disk):

- Kivo (`kivo.py`): one student page per variant (zh-CN, ja and the voices, by voice clip name).
- The Global server's CharacterDialog table (`globallines.py`), as ba-data publishes it (one bounded download per
  session, only when the UI language is not zh-CN; `lobby.lines` waits up to 3 s for it, then answers with Kivo's
  lines alone). A dialogue's rows go on the talk animation's voice clips in order.

The page shows only the UI language's lines: a dialogue card without one shows a grey, slanted "Dialogue N". The
dialogue host draws the game's `ChatDialog/Talk` balloon (shared with the Hall's `student-hall-dialogue.js`),
anchored by the lobby's own Talk position mapped through the current view and clamped to the visible area; fullscreen
keeps the same balloon with widened bounds. The pointer-transparent host exposes the current text through
`role="status"`.

### Local error reports and startup recovery

The shared registry, `src/bmpt/web/error-codes.json` (served at `/error-codes.json`), gives error and import-reason
names permanent display references (`E` plus four digits, grouped by area; references are never reused) and a
`failed` or `notice` level. Service error JSON keeps `code` as the name and adds `ref`; RPC and job boundaries log
service errors once with compact JSON parameters.

**Error Report** in Settings' log row and failure popups creates one local
`BAMT-report-<version>-<YYYYMMDD-HHMM>.zip` (Desktop, Downloads, the data directory, then the temporary directory;
no upload). The archive contains `report.txt`, `report.json` and the rotated logs, each bounded. Home paths, user
names, hostnames, credentials and asset filenames are redacted; the game's own bundle names, CAB ids and the App's
model URLs stay. Reports include app/build and OS details, locale, web view version, front-end page/phase/errors,
consent states, game metadata, library counts, boot timings and the last failed job; report generation never
initializes game assets or takes the library lock, and optional reads share a one-second deadline. With the Check
Game Files consent, `gameFiles` compares each install against Steam's file list by name and size (`sizeChanged`,
`missing`, `notListed`, bounded; stat only, no hashing). Backend exceptions create a report in the data directory at
most once per minute, retaining three automatic archives.

Recovery code, its locale strings and a built-in stylesheet are embedded before the module graph. During boot,
uncaught errors and failed scripts show a built-in recovery page immediately; the watchdog fails after 45 seconds
without phase progress (pending requests keep it waiting up to 180 s, and `hold()` stops its clock while the user's
part runs). A later `ready()` removes the failure page. `--safe-mode` is a launch-only option: it omits the normal
module graph, refuses game-asset requests, never creates Spine/WebGL renderers, and opens built-in Settings, report
and log controls; the safe restart action (`app.restartSafe`) releases the single-instance lock first and is refused
where unavailable. Windows checks the WebView2 Evergreen runtime (floor: 105) before importing pywebview and shows a
native localized message with the download URL; macOS uses a native alert. Each launch rotates the log through `.1`
and `.2`; failed rotation falls back to a temporary log, then stderr.

## Release notes and feature tours

Release Notes uses the `UIPopup_Sort` notice frame with a scrolling body and an explicit release-page action.
`release-notes.js` reads the body's Markdown in the UI language: language sections are separated by a rule and marked
`<!-- lang: ... -->`; an unmarked section is told by its script. Emphasis and link marks go and their text stays;
HTML is dropped, never interpreted. A build ships its own notes as `assets/release-notes/<X.Y.Z>.md` (the text its
GitHub release carries), read by `app.releaseNotes`. After an upgrade, pre-releases included, `Update.afterUpgrade`
shows the running version's notes once (`app.upgradeNotes`), when no popup is open, before the feature tour.

`tours.js` and `webui/tours.py` present new-version feature tours; the registry (`PUBLISHED_TOURS`) ships the
published tours, text-first pages whose optional bundled PNGs illustrate only the App's own UI, without game assets.
The framework adds no startup capture, asset scan or network request; Settings' version area replays all available
versions. Navigation and modal focus use the existing popup prefabs.
