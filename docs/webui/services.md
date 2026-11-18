# Services

## Rules for every service

- **Threads.** Services are thread-safe: HTTP handlers run on many threads at once. UnityPy work on one bundle is
  serialised with a per-bundle lock. Long operations run on the single job worker (see [Jobs](api.md#jobs-and-events)).
- **No prose in results.** Services return data, not sentences. Errors are `ServiceError(code, **params)`
  (`bmpt/core/errors.py`); the front end shows the locale string `webui.error.<code>`. An error passes every parameter
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
- **Disk cache.** Generation 2 uses a sibling with the `-bmpt-g2` suffix beside the platform or configured cache
  directory. It keeps the three most recently used resource-version directories and stores persistent previews
  separately under `previews/`.
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
- **User data.** The upgrade coordinator resolves `data.json` and `library.json` before services open. Preferences,
  including `aliases`, are saved under the data epoch's `webui.json` lock. The last good file stays as a `.prev`
  copy and unreadable content can be retained as `.bad`. Unreadable input that cannot be recovered pauses the
  upgrade. The config poll applies successful reads of Classic saves from the committed `config.toml`.

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
`/asset/art/background/` serves only names starting with `BG_`, `/asset/art/officialThumbnail/` only `Student_Portrait_`.

`model_glb` reads prefab bundles and resolves their external CABs on demand. Chibi, memorial lobby and office graphs share
the revision-checked `gameassets/cabs.py` resolver; the optional preview cache persists bounded, checksummed CAB
metadata, confined to the installation and checked by device, inode, size, mtime and ctime. The shared
`unity/unityfs.py` parser reads only header/block info and uses the declared bundle length to ignore CRC-correction
tails. The GLB carries material, sampler, outline, blend and clip-toggle data. `chibi-materials.js` applies that
data when rendering a chibi. `model-camera.js` supplies the initial framing. The view accepts zoom, pan and orbit
with eased framing (`attachInput`).

`AssetNotFound` (a `KeyError`) when a name is unknown. Layout nodes use the schema
`{name, active, localPosition, scale, localRotationZDegrees, widgets, children?}`. Atlas and texture references resolve to
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

Cached layouts carry the export's format in their key (`_LAYOUT` in `service.py`). Generation-2 caches use a
separate namespace.

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
in every game folder: a portrait core's key is the core less `_spr`; a lobby core belongs to the installed alt
whose key or core alias plus `_home`, `_home_gl` or `_home_teen` names it, a direct file-id match first. The shipped
data and the character map are parsed once and kept; every build works on a copy, and `reload()` keeps the old parse
when the data is invalid.

Special Spine slots use `special:<lower-case core>` keys and the lobby workflow. Discovery accepts named
`spinebackground` bundles and nonstandard cores in `spinelobbies` and `spinecharacters`; shared `_mxcommon` bundles
are excluded. The category is retained on the alt (`spineCategory` in RPC data) and kept separate from student
portraits and memorial lobbies; import, apply, restore, configuration, the global toggle and entry scans classify
these files by category as well as core, while parts still match by prefix and resource type. Roster cards, chips,
the picker and search include lobby-only alts; container paths under `SpineBackground/<core>/` identify the
owning scene, and conflicting scene containers are refused. Known role tokens (workpage, daytime, night, task, cafe)
have locale labels; unknown names keep their exact core.

Sources: the character map CSV the app already downloads (`BA-Characters-Internal-ID.csv`), the curated data file
shipped in the package (names, backgrounds and portrait names, generated by `scripts/build_catalog_data.py`), and the
game scan (which skeletons exist, dates). Skeletons missing from the map become their own student, named by file id.
Playable students take SchaleDB's names per server; NPCs and story sprites the character map's columns and
`catalog/data/naming.json` (its name table and glossary give each language's word, from the game where it names it,
else a translation, and say which). zh-CN shows Kivo's community wording; the CN server's replaced wording stays
findable through `aliases` (never shown). Names follow the alt name format: base name plus the alt in
brackets, never a middle dot.

`releasedOnServer` follows the selected server. An installed Collection portrait establishes availability for a
playable alt. Otherwise the catalog's `released` flags apply. If there is no flag, the absence of a Collection
portrait index permits the alt. All playable alts remain in the student list.

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
  every alt in those languages. The lobby's lines follow the UI language (`LINE_LANGS`).
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
