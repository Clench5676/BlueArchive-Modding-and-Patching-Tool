# Blue Archive asset ecosystem (Global, Steam, Windows)

Research report, 2026-11-07. Section 7's proposals are in the backlog: P1 (for older releases), the in-place rebuild of
section 4.2 and the built-in resources of P7 are implemented as BL-098 to BL-100; P2 to P6 are BL-101 to BL-105.

Labels used throughout:

- **Verified**: measured on every relevant file of the official build named below.
- **Inferred**: consistent with all the data seen, not proven (for example, needs a second build or the catalog).
- **Guess**: plausible, little direct evidence.

## 1. Method and sources

- **Build.** Steam app 3557620, depot 3557621, manifest gid 7791673520712697115, build id 25905763. The App's resource
  version for it (SHA-1 of `catalog_Windows.hash`) starts `ae2145d0`.
- **Whole depot, names and sizes only.** Steam's depot manifest lists 34,308 files (25.8 GiB) with size and SHA-1.
  Sections 2.1 and 2.2 come from it.
- **Bundles, content metadata.** A verified, read-only development copy of the official files (every file's SHA-1
  checked against the manifest): all 26,301 bundles of `GameData/Windows` and `Preload/Windows` plus
  `catalog_Windows.hash`. Each bundle was read with UnityPy (the App's own reader library), in a test session that
  cannot open anything else, recording: the UnityFS header and nodes; each serialized file's version, platform and
  externals; the `AssetBundle` object (`m_Name`, `m_AssetBundleName`, `m_Dependencies`, `m_Container`, preload table
  size, scene flag); names, sizes and formats of textures, text assets, audio clips, materials, shaders and
  MonoScripts; the CRC32 of the whole file. 157 s, no errors. Only names, sizes, counts and hashes were written.
- **Not read.** `catalog_Windows.bytes`, the `BundleRevision` files, `MediaCatalog.bytes`, `TableCatalog.bytes`, the
  `TableBundles` and the `MediaResources` are not in the development copy: names and sizes only. Only one build was
  available, so nothing here is a diff between two game versions.
- **App code compared** (`main` at `a80a44d9`, 0.7.0-alpha.6): `ba_modding_toolkit/naming.py`, `bmpt/unityfs.py`,
  `bmpt/gameassets/cabs.py` and `discovery.py`, `bmpt/library/judge.py`, `integrity.py`, `storage.py` and `service.py`
  (`_check_group`, `_replacements`, `_other_targets`, `_official_for`, `_spine_parts`, `_core_map`),
  `bmpt/webui/batch_import.py`, `bmpt/webui/gamewatch.py`.

Terms: a **bundle** is one `.bundle` file; a **CAB** is a serialized file inside it (`CAB-<32 hex>`), possibly with a
`.resS` or `.resource` stream node; an **asset path** is a key of the bundle's `m_Container`
(`Assets/_MX/...`); a **group** is a bundle's file name without the CRC; a **part** is one bundle of a character core.

## 2. Inventory

### 2.1 The depot (Verified, manifest)

| Location | Files | Size | Content |
|---|---:|---:|---|
| game folder root | 7 | 252.5 MiB | `BlueArchive.exe`, `GameAssembly.dll` (IL2CPP, 232 MB), `UnityPlayer.dll`, crash handlers |
| `BlueArchive_Data/` (player data) | 20 | 193.5 MiB | `globalgamemanagers`, `level0`–`level2`, `resources.assets` (+ 157 MB `.resS`), `sharedassets0`–`2` |
| `BlueArchive_Data/Plugins/` | 95 | 393.1 MiB | native plugins |
| `BlueArchive_Data/Resources/` | 2 | 4.9 MiB | `unity default resources`, `unity_builtin_extra` |
| `BlueArchive_Data/il2cpp_data/` | 5 | 33.7 MiB | `global-metadata.dat` and four resource files |
| `StreamingAssets/aa/` | 2 | 0.2 MiB | `settings.json` (3,174 B), `AddressablesLink/link.xml` (216,080 B): Unity Addressables |
| `StreamingAssets/Audio`, `/Video` | 6 | 28.7 MiB | title and logo videos, theme music |
| `StreamingAssets/PUB/Resource/` | 34,171 | 24.9 GiB | the patchable resources, below |

### 2.2 `PUB/Resource` (Verified, manifest)

| Folder | Files | Size | Content |
|---|---:|---:|---|
| `Catalog/Windows/` | 2 | 65.9 MiB | `catalog_Windows.bytes` (69,083,513 B), `catalog_Windows.hash` (32 B) |
| `Catalog/MediaResources/` | 2 | 7.2 MiB | `MediaCatalog.bytes`, `MediaCatalog.hash` (10 B) |
| `Catalog/TableBundles/` | 2 | 1.2 MiB | `TableCatalog.bytes`, `TableCatalog.hash` (11 B) |
| `Catalog/`, `GameData/`, `Preload/` | 3 | 27 B | `BundleRevision`, 9 B each |
| `GameData/Windows/` | 26,073 | 15,901.6 MiB | Unity bundles (section 2.3) |
| `Preload/Windows/` | 228 | 198.5 MiB | Unity bundles: the prolog layer and shared base (section 3.4) |
| `GameData/TableBundles/` | 6,313 | 135.8 MiB | one `.zip` per stage and map, `<stage id>_<scene>.zip` |
| `Preload/TableBundles/` | 20 | 369.0 MiB | `ExcelDB.db` (314.4 MiB), `LevelSkillDataDBSchema.db`, `LogicEffectDataDBSchema.db`, `SkillVisualEffectDataDBSchema.db`, `Excel.zip` (16.5 MiB), `Battle.zip`, `Module.zip`, prolog stage zips |
| `GameData/MediaResources/` | 984 | 8,513.8 MiB | `Audio/` 599 `.molru` (`BGM`, `VOC_JP/JP_<Name>` 295, `VOC_KR/KR_<Name>` 291, `VOC_KR/KR_Main` 12); `Cafe/PV` 32 `.mp4` + 10 `.ogg`; `Scenario/Event` 224 `.mp4` + 62 `.ogg`, `Scenario/Main` 20 + 8; `UIs/03_Scenario` 3 `.molru` (742.5 MiB); `Video/` 26 `.mp4` |
| `Preload/MediaResources/` | 544 | 345.5 MiB | `Audio/` 531 `.molru`, `UIs/` 2 `.molru`, `Video/` 11 `.mp4` |

Videos come per language (`-kr`, `-th`, `-tw`, `_en` suffixes). The `.molru` container format was not inspected
(not in the development copy).

### 2.3 Bundle families (Verified, development copy)

Grouped by file-name prefix. Dates: distinct dates in the family's names, earliest and latest.

| Family | Bundles | MiB | Dates | Range |
|---|---:|---:|---:|---|
| UI textures and sprites (`uis-*`) | 1,643 | 5,503.8 | 81 | 2024-11-18 – 2026-10-13 |
| 3D student dependencies (`assets-_mx-characters-*`) | 2,114 | 2,072.8 | 10 | 2024-11-18 – 2026-07-02 |
| Memorial lobbies (`assets-_mx-spinelobbies-*`) | 1,405 | 1,946.8 | 11 | 2024-11-18 – 2026-07-02 |
| Scenes (streamed scene bundles, `_scenes_all`) | 341 | 1,781.5 | 8 | 2024-11-18 – 2026-07-02 |
| Scene dependencies (`assets-_mx-scenes-*`) | 1,133 | 983.7 | 12 | 2024-11-18 – 2026-07-02 |
| 3D NPC and enemy dependencies (`assets-_mx-npcs-*`) | 1,624 | 919.9 | 9 | 2024-11-18 – 2026-07-02 |
| Spine portraits (`assets-_mx-spinecharacters-*`) | 2,517 | 735.5 | 10 | 2024-11-18 – 2026-07-02 |
| Cafe furniture dependencies (`assets-_mx-cafe-*`) | 3,524 | 379.8 | 8 | 2025-02-11 – 2026-07-02 |
| Cafe prefabs and animations (`cafe-*`) | 1,339 | 353.3 | 16 | 2024-11-18 – 2026-07-02 |
| UI prefabs (`ui-*`) | 1,110 | 169.3 | 18 | 2024-11-18 – 2026-10-13 |
| Minigames | 212 | 159.2 | 7 | 2024-11-18 – 2026-02-10 |
| Spine backgrounds (`assets-_mx-spinebackground-*`) | 98 | 151.2 | 6 | 2024-11-18 – 2026-07-02 |
| Field dependencies | 112 | 150.7 | 5 | 2024-11-18 – 2026-07-02 |
| Obstacle dependencies | 1,606 | 91.9 | 8 | 2024-11-18 – 2026-07-02 |
| Cutscene resources (`prodresources`) | 84 | 82.6 | 3 | 2024-11-18 – 2026-04-06 |
| Effects (prefabs, `effect-*`) | 2,195 | 72.4 | 15 | 2024-11-18 – 2026-07-02 |
| 3D prefabs (`character-*`) | 2,276 | 68.2 | 10 | 2024-11-18 – 2026-07-02 |
| UI dependencies (atlases, fonts, `assets-_mx-uis-*`) | 78 | 48.2 | 11 | 2024-11-18 – 2026-10-13 |
| Field prefabs | 91 | 47.8 | 4 | 2024-11-18 – 2026-07-02 |
| Audio (`audio-*`) | 31 | 41.4 | 8 | 2024-11-18 – 2026-07-02 |
| Effect dependencies (`effectsfreezed`, `etceffects`) | 469 | 48.0 | 10 | 2024-11-18 – 2026-07-02 |
| Obstacle and projectile prefabs | 949 | 29.3 | 8 | 2025-07-02 – 2026-07-02 |
| Lobby and 3D shared (`_mxcommon`) | 57 | 16.6 | 8 | 2025-02-11 – 2026-07-02 |
| Timelines | 169 | 7.7 | 5 | 2025-07-02 – 2026-07-02 |
| Shaders | 13 | 0.9 | 8 | 2021-10-29 – 2026-07-02 |
| Other (per-asset groups, 713 families) | 896 | 55.9 | | |
| **Preload** (whole folder) | 228 | 198.5 | 26 | 2021-10-29 – 2026-07-22 |

Preload holds: prolog addressables (66 bundles, 66.0 MiB), the prolog audio mixer and common sound effects (23, 46.0
MiB), 3D, UI, effect and scene dependencies used by the prolog and by everything else, the 9 prolog portraits
(`arona_spr`, `yuuka_spr`, ...), shaders, URP and NGUI package resources, and the one built-in shader bundle
`6befbbb5c811cb1575f22145d4f0a954_unitybuiltinshaders_3925052273.bundle`.

The App's own classification (`judge.kind_of_file`) of the 26,301 names: 2,521 `spr` (630 cores), 1,506 `lobby` (289
cores), 6,023 `model` (897 cores by `core_of`), 16,250 `other`, 1 none (the built-in shader bundle).

### 2.4 Bundle format (Verified, all 26,301)

- Unity **2021.3.56f2** in every bundle and every serialized file, including groups dated 2021-10-29.
- UnityFS format 8, flags 0x243: LZ4HC blocks, block info combined with the directory, padding at the start.
- Platform StandaloneWindows64, serialized file version 22, type trees present.
- 33,708 nodes: 26,748 serialized files (CABs) and 6,960 stream nodes. 19,998 bundles hold one CAB only; 5,955 one
  CAB plus a stream; scene bundles up to 201 nodes.
- `m_ExplicitDataLayout = 1` and `m_PathFlags = 0` everywhere (Scriptable Build Pipeline output).
- `m_Dependencies` lists the referenced CAB names (lower case). It equals the CABs in the externals in 26,292 bundles;
  in 9 streamed scene bundles it lists fewer.
- The only non-CAB external is `Library/unity default resources`, in 3,779 serialized files (the player ships it).

### 2.5 Objects (Verified)

7,277,014 objects. Main types: GameObject 2,251,750, Transform 2,251,518, MonoBehaviour 1,015,406, ParticleSystem
266,524, MonoScript 66,221 (2,343 distinct classes; copies sit in 10,902 bundles), Mesh 32,673, Texture2D 30,714,
Material 27,655, AnimationClip 19,648, AudioClip 6,351, PlayableDirector 3,588, TextAsset 2,089, Shader 251 (in 36
bundles; 132 of them in one bundle).

- **Textures.** 30,696 of 30,714 store their pixels in the bundle's `.resS` stream node. Formats: portraits BC7 (622)
  and RGBA32 (3); `spinelobbies` bundles RGBA32 (699), DXT1 (25), DXT5 (15); 3D students DXT1 (4,030), DXT5 (1,589),
  BC7 (125); UI DXT5 (6,859), RGBA32 (6,006), RGB24 (2,686).
- **Audio.** All 6,351 clips keep their data in `.resource` stream nodes, 240.7 MiB in total. Voices and music are
  not in bundles: they are the `.molru` files of `MediaResources`.
- **Text.** Spine skeletons and atlases are the bulk of the TextAssets: portraits 626 `.skel` / 625 `.atlas`, lobbies
  288 / 287, backgrounds 49 / 49, prolog portraits 9 / 9.
- **Asset paths.** 200,432 container entries name 145,397 distinct asset paths. **No asset path occurs in two
  bundles.** 14,757 bundles hold exactly one asset path.
- **Object names are not unique.** 1,474 texture names occur in more than one bundle (for example `FX_TEX_Noise_02`
  in 12 folders). Each copy has its own asset path; only 28 textures have no matching asset path in their bundle.

## 3. Naming and the dependency graph

### 3.1 File names (Verified unless marked)

```
[prologgroup- | prologdepengroup-] <group path> -<marker> [-<type word>] -<YYYY-MM-DD> [_<NNN>] _<assets|scenes>_all_ <CRC32> .bundle
```

- **CRC32.** The number before `.bundle` is the CRC32 of the whole file, in decimal: 26,301 of 26,301.
- **Group path.** For `_mxdependency` groups it is the asset folder, lower case, `/` → `-`
  (`assets-_mx-spinecharacters-ch0100_spr` ↔ `Assets/_MX/SpineCharacters/ch0100_spr/`): 14,935 groups checked,
  16 exceptions (folders with `-` in their names, stray files). For `_mxload` groups it is the path below
  `Assets/_MX/AddressableAsset/` (`uis-01_common-14_charactercollect` ↔
  `Assets/_MX/AddressableAsset/UIs/01_Common/14_CharacterCollect/`): 9,970 of 10,673; the other 703 are one group
  per asset, named after the asset (timelines and animator controllers under `AddressableAsset/Character/`).
- **Markers.** `_mxload` (loadable entry points, 10,673), `_mxdependency` (dependency assets, 14,927), `_mxprolog`
  (Preload, 196), `_mxcommon` with `_mxdependency` or `_mxprolog` (category-wide shared assets, 170); in 326 names the
  marker is glued to a scene name instead (`sb_`, `rb_`, `db_`, `rd_` battle maps, for example
  `sb_02_riverside_p01_n_mxload-…_scenes_all_…`; 325 of them are scene bundles).
- **Date.** 102 distinct dates, 2021-10-29 to 2026-10-13. Section 4 shows it is the group's creation date, not the
  file's build date.
- **Split parts.** A dependency folder is split into one group per asset type. Groups dated up to 2025-06 number the
  split `_000`, `_001`, ... after the date; from 2025-07 a type word before the date appears
  (`-_mxdependency-textures-2026-04-06`, `-_mxload-prefabs-2025-07-02`), numbers continue beside it until 2026-02, and
  groups dated 2026-03 or later use type words only.
  Words seen: prefabs 6,643, textures 470, materials 318, meshes 244, timelines 236, animationclips 162, assets 134,
  animatorcontrollers 78, textassets 63, audio 51, lightingsettings 5, others 3, shaders 2, scenes 2, fonts 1.
- **The numbered split is positional, not a type code.** A portrait core is `.asset` (000), `.mat` (001),
  `.bytes`+`.txt` (002), `.png` (003) because it has those four types; a 3D core runs `.anim`, `.asset`, `.wav`,
  `.mat`, `.fbx`/`.mesh`, `.prefab`, `.psd`/`.tga` and numbers only the types it has. The same group path and number
  under two dates holds different asset types in **454 of 769** cases (402 labels in 294 model cores, 10 in 4 lobby
  cores, 42 others).
- **Pack-separately groups.** 74 groups emit one bundle per asset, and all bundles of such a group share the file
  name except the CRC: 6,476 files (the largest single group, `effect-_mxload-prefabs-2025-07-02`, has 1,543).
  By family over all dates: `effect-_mxload-prefabs` 1,864, `cafe-_mxload-prefabs` 1,114, `ui-_mxload-prefabs` 769,
  `uis-03_scenario-02_character-_mxload-prefabs` 681, `projectile-` 489, `obstacle-new-` 460,
  `ui-uilobbyelement-_mxload-prefabs` 289 (the memorial lobby prefabs), `uis-03_scenario-01_background-` 282.
- **Internal names.** Every bundle's `m_AssetBundleName` is unique: `<32 hex>_assets_all.bundle` (19,476),
  `<32 hex>_scenes_all.bundle` (341) or `<32 hex>_assets_<asset path>.bundle` (6,484, the pack-separately groups).
  19,898 distinct 32-hex prefixes: each numbered or typed split part has its own prefix, a pack-separately group
  shares one; the same group path under two dates never shares one (939 of 939).

### 3.2 Graph size and shape (Verified)

Nodes are bundles; an edge A → B means a CAB of A lists a CAB held by B in its externals.

- 103,652 edges between 26,301 bundles; no reference is left unresolved (every referenced CAB is in the build).
- 7,191 bundles reference nothing; 9,643 are referenced by nothing (entry points loaded by address).
- **Cycles exist:** 190 strongly connected components of 2 to 9 bundles (for example a 3D model's dependency parts
  and the effect prefabs that use them reference each other). Any closure or ordering code must allow cycles.
- Longest dependency chain (cycles condensed): 14 bundles; 50 % of bundles sit at depth 3 or less.
- Transitive closure per bundle: median 5 bundles, 90th percentile 108, 99th 408, maximum 1,083.
- **Preload is a base layer:** 29,609 edges go from GameData to Preload, 1 from Preload to GameData.

### 3.3 Hubs (Verified)

9 bundles have 1,000 or more direct dependents, 92 have 100 or more (33 of them in Preload, 130.8 MiB together), 1,169
have 10 or more.

| Dependents | Bundle | Size | Holds |
|---:|---|---:|---|
| 4,207 | `prologgroup-assets-_mx-addressableasset-audio-audiomixer-_mxprolog-2024-11-18_…_1078098862` | 7 KiB | the audio mixer (groups, snapshots) |
| 3,368 | `prologdepengroup-assets-_mx-effectsfreezed-material-_mxprolog-2024-11-18_…_2795506591` | 13 KiB | 114 effect materials |
| 3,332 | `prologdepengroup-assets-_mx-shaders-_mxprolog-2024-11-18_…_1690106043` | 365 KiB | 34 shaders (the portrait shaders among them) |
| 2,995 | `assets-_mx-effectsfreezed-material-_mxdependency-2024-11-18_000_…_3075775260` | 179 KiB | 1,567 effect materials |
| 2,499 | `packages-com.unity.render-pipelines.universal-runtime-materials-_mxdependency-2023-12-27_…_4212363245` | 4 KiB | URP default materials |
| 2,043 | `prologdepengroup-assets-_mx-effectsfreezed-mesh-_mxprolog-2024-11-18_…_1591984242` | 230 KiB | effect meshes |
| 1,646 | `assets-_mx-effectsfreezed-mesh-_mxdependency-2024-11-18_002_…_4133386000` | 5.4 MiB | 832 effect meshes |
| 1,444 | `prologdepengroup-assets-_mx-effectsfreezed-material-_mxprolog-2021-10-29_001_…_2669666289` | 9 KiB | 60 effect materials |
| 1,042 | `prologdepengroup-assets-_mx-uis-atlas-_mxprolog-2024-11-18_…_1735253868` | 4.8 MiB | 4 UI atlases (NGUI) |
| 807 / 806 | `prologdepengroup-assets-_mx-program-animators-_mxprolog-…` (two dates) | 6–10 KiB | shared animation clips |
| 806 | `prologdepengroup-assets-ngui-resources-shaders-_mxprolog-2024-11-18_…` | 20 KiB | NGUI shaders |
| 751 | `assets-_mx-spinelobbies-_mxcommon-.mat-_mxdependency-2025-02-11_000_…` | 35 KiB | 212 shared lobby materials |
| 735 | `assets-_mx-shaders-_mxdependency-2024-11-18_001_…` | 620 KiB | 132 shaders |
| 679 | the built-in shader bundle | 96 KiB | 15 Unity built-in shaders |

### 3.4 Chains per kind (Verified)

| Kind | Cores | Parts per core | Bundles outside the core it needs (closure) | Who references the core |
|---|---:|---|---|---|
| Portrait `*_spr` | 630 | 4 (603 cores), 5 (15), 8 (3), 1–2 (9 prolog cores) | 1 (median and 90th percentile; max 2): the shader prolog | scenario character prefabs `uis-03_scenario-02_character` (1,369 edges), `ui` prefabs (257) |
| Memorial lobby `*_home` | 289 | 5 (251), 6–9 (31), 1–4 (6), 35 (`spinebg`) | median 5, 90th 9, max 65 (median 7.7 MiB): `_mxcommon` sounds and materials, audio mixer, spine-unity runtime, sound effects, shaders | `ui-uilobbyelement` prefabs (937 edges), `uis-03_scenario-01_background` (907) |
| 3D model | 897 | 1–18, median 7 (prefab core `character-<core>` plus dependency core `assets-_mx-characters-<core>` or `-npcs-<core>`) | median 91 bundles (114.4 MiB), 90th 316, max 765: animators, audio mixer, effect materials and meshes, shaders, URP materials, character animator base | effect prefabs (5,143 edges), timelines (1,081), projectiles (717), obstacles, cutscenes, other NPCs |

- **Portrait parts:** `_000` the spine-unity `SkeletonDataAsset` and `SpineAtlasAsset` (`.asset`), `_001` the material
  (`.mat`), `_002` `.skel.bytes` and `.atlas.txt`, `_003` the page texture (`.png`); about 590 of 630 cores follow it.
- **Lobby parts:** the same four plus `.playable` timelines (`_004`), prefabs, clips and sounds in the larger cores.
  The lobby prefab itself is outside the core, in `ui-uilobbyelement-…` (UI prefab, pack-separately since 2025-07-02).
- **3D:** the prefab core `character-<core>-_mxload-…` holds prefabs (`Character/<CORE>/`, `/Cafe`, `/Echelon`),
  timelines and override controllers; the dependency core holds animations, audio, materials, meshes, effect prefabs
  and textures, split by type. Model parts are consumed widely: changing their objects can break effects and timelines
  elsewhere, which is why the App's consumer scan (`_check_model_references`) exists.

## 4. Releases and updates

### 4.1 Dates in this build (Verified)

102 dates. Large waves (GameData bundles carrying that date):

| Date | Bundles | MiB | Notes |
|---|---:|---:|---|
| 2024-11-18 | 10,989 (+101 Preload) | 10,243.4 | the base: 42 % of all bundles, 64 % of the bytes |
| 2025-02-11 | 2,440 | 315.8 | cafe furniture (2,380), the first `_mxcommon` groups |
| 2025-02-18 | 746 | 590.7 | |
| 2025-07-02 | 5,511 | 297.1 | the switch to type words and pack-separately prefab groups |
| 2025-07-10 | 643 | 522.4 | |
| 2025-07-25 | 1,601 | 610.2 | |
| 2025-11-07 | 837 | 501.2 | |
| 2026-02-10 | 265 | 216.7 | |
| 2026-04-06 | 1,412 | 815.9 | |
| 2026-07-02 | 1,100 | 794.3 | |

The other 92 dates carry 1 to 90 bundles each (many single UI and event bundles). Preload names go back to
2021-10-29.

### 4.2 What a date means (Verified, with one stated assumption)

- **Dates are group creation labels; old groups are rebuilt later.** 3,790 bundles reference a CAB of a group dated
  later than their own (7,389 edges; 854 within one core: `assets-_mx-characters-ch0058-_mxdependency-2024-11-18_004`
  references `…-2025-07-25_001`). A group's CAB name is tied to that group (every dated group has its own internal
  prefix, 939 of 939), so those bundles were built after the later group existed while keeping their own date.
  Every bundle, the 2021-10-29 ones included, carries Unity 2021.3.56f2.
- **One file per group, one bundle per asset.** No asset path is in two bundles, and every internal name is unique.
  This build holds no superseded copies: a dated group added later does not leave a stale duplicate in an older
  group's bundle.
- **CRC = content version.** The name's CRC is the file's CRC32, so a rebuilt group whose bytes change gets a new file
  name that differs only in the CRC; an unchanged group keeps its name (Inferred: deterministic builds; not observable
  in one build).

### 4.3 What a game update changes (Inferred)

1. Groups whose content changed are rebuilt in place: same group name and date, new CRC, so a new file name; the old
   file goes. Evidence: 4.2.
2. New assets of an existing folder go into a new dated group of that folder (for example Airi's 3D folder has groups
   dated 2024-11-18, 2025-07-25 and 2025-11-07, the later ones with one animation and one effect prefab; `ch0164_home`
   has a second material and page texture dated 2025-07-10). Whether a *changed* asset moves to the new dated group or
   stays in its rebuilt old group cannot be told from one build.
3. New content (students, events) adds new folders, so new groups with the release date.
4. Steam ships every current file; mobile and launcher clients presumably fetch changed files by the catalog
   (Guess: the catalog, not the file names, is the download list).

To verify: diff two depot manifests (names, sizes, SHA-1) across a game update, and read `catalog_Windows.bytes` and
`BundleRevision` (section 7, P8).

## 5. The developers' asset management model

| Inference | Evidence | Confidence |
|---|---|---|
| Unity **Addressables** with the packed build script on the Scriptable Build Pipeline | `StreamingAssets/aa/settings.json` and `AddressablesLink/link.xml`; internal names `<32 hex>_assets_all.bundle`, `_scenes_all.bundle` and `_assets_<asset path>.bundle` are the packed mode's pack-together, scene and pack-separately names with a group-based prefix; a `<32 hex>_unitybuiltinshaders` bundle; `m_ExplicitDataLayout = 1`; `m_Dependencies` holds CAB names | Verified that the files are Addressables/SBP output; the build script's settings are Inferred |
| MonoScript bundle off | 66,221 MonoScript copies spread over 10,902 bundles; no monoscripts bundle | Verified |
| Groups are generated by the developers' own tooling, one per asset folder and role | group names are folder paths with `_mxload` / `_mxdependency` / `_mxprolog` / `_mxcommon` markers; loadable assets sit under `Assets/_MX/AddressableAsset/`; dependencies are made explicit (only 28 of 30,714 textures lack an asset path in their bundle), so Addressables never duplicates implicit dependencies | Inferred (names and contents fit exactly; the tool itself is unseen) |
| Dependency groups are split per asset type into separate groups | each split part has its own internal prefix; positional `_NNN` until 2025-02, type words from 2025-07 | Verified for the output; the motive (smaller downloads per type) is a Guess |
| Each new content release creates dated groups; old groups keep their date and are rebuilt in place | 4.2 | Verified for "rebuilt later, date kept"; the moving rule of changed assets is open |
| File names are the developers' own: group, then `_assets_all_` or `_scenes_all_`, then the file's CRC32 | Addressables' hash naming appends an MD5-style hash; here a decimal CRC32 of the final compressed file is appended, also to pack-separately bundles whose internal names say `_assets_<path>` | Verified for the rule; that it is a post-build rename is Inferred |
| The client checks the whole-file CRC32 | Unity's own bundle CRC covers the uncompressed content, yet the community's appended-bytes CRC correction (which the App's engine uses) works only against a check of the whole file | Inferred |
| A separate Preload (prolog) layer ships with the client and is the shared base | `prologgroup-` / `prologdepengroup-` names; 29,609 edges into Preload, 1 out | Verified |
| Tables and media have their own catalogs and pipeline, outside Addressables | `TableCatalog.bytes`, `MediaCatalog.bytes` with short `.hash` files; `ExcelDB.db`, `Excel.zip`, per-stage zips; `.molru` media containers | Verified that they exist; formats not inspected |
| Rendering stack | URP package bundles, NGUI resources and shaders, spine-unity, Unity Timeline (MonoScript classes) | Verified |

## 6. The App today against the data

| Area | The App today | The data shows | Gap |
|---|---|---|---|
| File-name parsing (`naming.parse_filename`) | date, CRC, `res_type` from `_NNN` or a known type word, prefix | 6 type words unknown to it (`animatorcontrollers`, `lightingsettings`, `others`, `shaders`, `scenes`, `fonts`; 91 files): `res_type` is None, the word stays in the prefix | small; identity stays distinct |
| Part identity across dates (`judge.part_of`) | prefix (no date) + `res_type` | `_NNN` is positional: same label, different types in 454 of 769 cross-date cases | used where no exact or CRC-less match exists (`_check_group` older parts, `_replaced_parts`, `mod_versions`): can pair a mod file with a part of another type |
| Exact target (`integrity.replacements`) | exact name, then name less CRC, then equal CAB set, then `part_of` | 6,476 files share their name less CRC; CABs are unique per bundle | the CAB tier resolves them (checked on a lobby prefab named with another CRC); `part_of` would be ambiguous |
| Slots of generic files (`core_of` for `other`) | `other:` + hash of `part_of` | one slot covers 1,864 effect prefabs, 289 lobby prefabs, 681 scenario character prefabs; 308 `other` slots hold several current files (7,378 files) | two mods of different prefabs of one such group get the same slot and target (by the code; not exercised) |
| Asset identity | not indexed | asset paths are unique across the build (145,397) | the one exact, release-independent key is unused |
| CAB index (`gameassets/cabs.py`) | header-only, demand-driven, persisted, revision-checked | 33,708 nodes; all bundles read in 4.4 s warm; no dangling references; cycles | fits; no gap |
| Missing parts (`_check_group`, BL-090) | references less files, resolved by the CAB index | 3,779 serialized files reference `Library/unity default resources`, never a CAB | a mod adding such a reference that its official part lacks would be reported missing (low) |
| Officialness (`_is_official_as`) | CRC32 of the whole file equals the name's CRC, no trailing bytes | true for 26,301 of 26,301 official files; the App already computes SHA-1 per file version (`Fingerprints`); Steam's manifest gives official SHA-1 and size for every file | CRC is not proof of origin (BL-019); no official SHA-1 to compare with |
| Resource version | SHA-1 of `catalog_Windows.hash` | also `BundleRevision` (9 B) in three folders, media and table catalog hashes | fine for cache keys; no per-update change list |
| Game updates (`gamewatch`) | reads Steam app manifests, rebuilds services when the resource version changes; entries report `updated` when a game update overwrote a mod | an update rebuilds groups in place (new CRC) and adds dated groups | no record of which groups an update rebuilt; old-build mods of a rebuilt group are judged by layout only when an official copy exists |
| 3D and lobby previews (BL-092) | lazy cross-bundle loading by CAB | 3D closure median 91 bundles / 114 MiB, up to 765; lobbies up to 65 | closure recomputed per preview |

## 7. Proposals, by value

Each is a candidate backlog item with its evidence. Effort is a rough guess.

**P1. Identify parts by asset path.** Keep an index of asset path → bundle (and CAB) beside the CAB index, persisted
and revision-checked the same way. Resolve a mod file's targets by its asset paths first; fall back to today's tiers.
This fixes cross-date pairing by `_NNN` (454 of 769 labels), finds the current home of an asset that a newer release
moved to another dated group, and names exactly which official assets a mod replaces and which it adds.
Evidence: 3.1, 2.5 (asset paths unique). Cost: reading every `AssetBundle` object with UnityPy took 149 s for the
whole build (cold build of the index; afterwards only changed files). A reader that decompresses only the blocks up to
the `AssetBundle` object would read far less: that object ends within the first 128 KiB of its CAB in 67 % of bundles
and within 512 KiB in 89 % (sample of 2,024); not measured. Tests: official files offered under names of another
date, number or CRC map to their own targets; a portrait or 3D part pairs by asset path, never by number.

**P2. Slots for pack-separately groups.** Where several official files share a name less the CRC, key the `other`
slot by the bundle's CAB (stable per entry, already in the CAB index) or its single asset path, not by `part_of`.
Evidence: 74 groups, 6,476 files, including all 289 memorial lobby prefabs and 681 scenario character prefabs.
Tests: two official lobby prefab bundles added as two one-file mods give two slots and two targets.

**P3. Official by Steam's manifest on Steam installs.** The App already reads Steam's app manifests (game watch) and
already hashes files with SHA-1. Reading the installed depot manifest of the install's build gives the official size
and SHA-1 of every file (34,308 here), so "official" becomes SHA-1 equality instead of CRC equality; other installs
keep the CRC rule. It also lets Check Game Files (BL-019) list modified files exactly, and two manifests give the
exact list of files a game update changed. Evidence: 2.1, `storage.Fingerprints`, BL-019's own caveat. Effort: a
read-only parser of Steam's binary manifest, tests on synthetic manifests. The owner decides whether reading
Steam's depot cache is in scope.

**P4. Dependency closures cached for previews and exports.** Store each core's closure (bundle list, sizes) in the
CAB index, keyed by the revisions of its members, so 3D and lobby previews and exports load a known set without
rediscovering it, and so the UI can show the size before loading. Evidence: 3.4 (3D median 91 bundles / 114 MiB);
input for BL-092. Cost: small, on top of the existing index.

**P5. A release ledger per install.** On every resource-version change, record each group's file name (with CRC) and
size; diff with the previous record: groups rebuilt (same group, new CRC), groups added, groups removed. Use it to
report which library entries an update touched, to re-check only those, and to tell for a mod file whose group name
matches but whose CRC does not whether that CRC was ever this install's official build ("made for an earlier build")
or never seen. Evidence: 4.2–4.3. With P3 the ledger is exact (SHA-1).

**P6. Hubs in import and compatibility details.** Mark a target with many dependents (from the CAB index edges: 92
bundles with 100 or more, the shader prolog with 3,332) and say so when a mod replaces it; this belongs with BL-014's
contextual inspection. The strict layout check for `other` replacements already guards their format.

**P7. Small parser and check fixes.** Add the six missing type words to `RESOURCE_TYPES_TEXT` (91 files); treat
`Library/unity default resources` as present in the missing-part check. Low value, low risk.

**P8. Development data to settle the open questions.** In the development setup: keep the catalog files
(`catalog_Windows.bytes`, the three `BundleRevision`, `MediaCatalog`, `TableCatalog`) with the tables as research
files, and keep one depot manifest per game update. The next update then answers 4.3 by a diff, and the catalog shows
whether it lists each bundle's CRC, size and dependencies, which would let the App check a file against the game's own
expectation on every platform. Needs the owner's approval for the sync that reads the install.

Not proposed: re-parsing the whole build at start-up (the header-only CAB index is enough for discovery); trusting
object names across bundles (1,474 texture names repeat); using dates as release identity (dates are creation
labels).

## 8. Open questions

- Does a changed asset move to a new dated group or stay in its rebuilt group? (P8, a manifest diff.)
- What do `BundleRevision`, `MediaCatalog.hash` and `TableCatalog.hash` hold, and what does `catalog_Windows.bytes`
  list per bundle? (P8.)
- Is the CRC compared by the client against the catalog or against the file name? Only the file-name rule is verified.
- Do the JP and mobile builds use the same groups, asset paths and dates? (A second platform's files would show it.)
