# Memorial lobby and thumbnails

## Memorial lobby

`memorial-lobby.js` frames and plays the memorial lobby, `memorial-lobby-touch.js` adds the game's touch interaction in fullscreen. Each memorial lobby is
an instance of its own (`createMemorialLobby()`): its state (the memorial lobby shown, players, dialogue, timers, settings, caches) is
made per instance, and its functions come from parts that take that state and keep none: `memorial-lobby-players.js` (show,
hide, context recovery), `memorial-lobby-view.js` (views, zoom, camera framing, edge extension), `memorial-lobby-geometry.js` (painted
rect and limits), `memorial-lobby-scene.js` (timers, touch ownership, suspension, the first frame, the intro's controls),
`memorial-lobby-clips.js` and `memorial-lobby-talk.js` (clips, dialogues, sounds); `memorial-lobby-layers.js` and `memorial-lobby-measure.js` are
stateless. The detail screen uses `memorial-lobby.js`'s instance. The Student Hall makes an instance per scene.

- **Linear light** (`linear.js`): the game blends in linear colour space, so each memorial lobby player samples its atlas
  pages as sRGB and blends into an sRGB framebuffer, slot colours decoded per page shader (`slot-colours.js`).
  Without WebGL2 the player draws in plain sRGB.
- **Draw order** (`memorial-lobby-stage.js`, `skeleton-parts.js`): all of a memorial lobby's skeletons and effects draw in one sorted
  order (sorting order, render queue, back to front) into the bottom player's context, as Unity sorts them.
- **Post-processing** (`memorial_lobby.py` reads the prefabs' URP volumes; the linear pass applies Panini projection,
  chromatic aberration and the colour grade). The Settings L2D tab's
  `memorialLobbyPostProcessing` (on by default) is the PC client's PP option.
- **Particle effects** (`gameassets/particles.py`, `particles.js`, `particles-geometry.js`, `particles-gl.js`):
  the player reads ParticleSystem metadata from the prefab and draws the effects in linear light in the same
  draw list as the skeleton parts. The player releases those resources when the scene closes.
- **Mesh effects** (`gameassets/meshfx.py`, `mesh-fx.js`): the few prefabs with plain MeshRenderers (lens dirt,
  light quads, water), drawn with the particle programs, Animator loops included.
- **Skeleton materials** (`gameassets/spine_materials.py`, `skeleton-materials.js`): the player chooses the drawing
  behavior from each page's material metadata, including replacement blend-mode materials.
- **Pinned bones** (`gameassets/pins.py`, `skeleton-pins.js`): SkeletonUtilityBone overrides applied after the
  animation; the drag targets are such bones.
- **Intro scenes, clip endings, idle clips, clip sounds** (`gameassets/memorial_lobby_intro.py`, `memorial-lobby-sync.js`): the intro
  timeline is played as the game's PlayableDirector does; a clip's ending fades per its finish mode; idle pools play
  each clip on its own track with the game's random-timing waits; a clip's sound keys play through the memorial lobby's shared
  audio events (decoded in memory, following the voice volume). Not reproduced: a memorial lobby's ambient loop.
- **Zoom**: fullscreen opens on the game's 16:9 view and zooms in to 2x. The zoom-out limit is the Settings L2D tab's
  row: *Game View*; *Full Art* (the default: the widest 16:9 view inside the painted art, measured in background
  slices over every pose the memorial lobby rests in, so a dialogue that moves the scene never shows a blank edge);
  *Unlocked* (the box of the painted art, parked parts left out); *Custom* (1.0–4.0 game views across, a slider).
  Past the painted art the memorial lobby shows what the game's camera clears to. The measurements are kept for the session.
- **Input**: press and drag (head pat, eye follow, drag boxes); wheel and pinch zoom at the pointer; a two-finger
  swipe pans; a right- or middle-button drag pans. A wheel is told from a trackpad by its step sizes, per browser
  engine; one scroll keeps the kind of its first event. With `--debug`, the page sends the fullscreen memorial lobby's input
  events through `debug.trace` so a trackpad problem can be reproduced from the log.

## Memorial Lobby camera framing

- **Entry.** In the fullscreen memorial lobby a visual-mode button (`memorial-lobby-camera.js`) enters the framing: the memorial lobby
  rests, and Back, Reset, the thumbnail capture shutter and an album at the bottom left (the thumbnail capture gallery's button)
  replace the fullscreen controls. The wheel zooms at the pointer's height, any drag moves the view up or down; the
  view stays 16:9 and centred across.
- **Limits.** `measureFraming` paints every pose the memorial lobby rests in; a cell outside the game's view that some pose
  leaves unpainted may not be shown. Zoom-in stops at 2x the game's view.
- **RPC `memorialLobby.camera.save`** `{alt, memorialLobby, source, view, scale, offset}` starts a library writer job. The saved
  camera is a generated generic mod of the memorial lobby's own UI prefab bundle (`library/memorial_lobby_camera.py`): the prefab's
  scale targets are reframed through a camera node, every other object byte for byte, saved with the CRC fix and
  checked after; a prefab whose structure does not fit is refused (`memorial_lobby_camera_unsupported`). The intro is held at
  the official framing by a track of the prefab's own copy of the intro timeline (`library/memorial_lobby_camera_intro.py`);
  prefabs with an Animator on the root scale their targets directly.
- **Shutter and album.** Each press saves a new choice under Now Loading; then the new choice's still (else, after
  1.5 s, the view on screen) flies into the album. The album opens Switch Memorial Lobby itself (`detail.js`, through
  the camera bridge) over the framing, whose controls take no input meanwhile; a memorial lobby changed there is framed anew
  from the game's view.
- **The choice.** The App lists a camera as a memorial lobby mod of its alt (`camera: {source, number, scale, offset}`),
  named after its source, in Switch Memorial Lobby; rename and delete as for mods. Apply writes the source's art
  through the memorial lobby's own apply, then the prefab through the generic one; any other memorial lobby choice puts the prefab back
  to the camera's base. Before a master-switch plan and a camera's apply, a camera framed on an official prefab the
  game replaced is made again on the new ones in place.
- **Preview.** `/asset/spine/mod/<camera>/` serves the source's art and the camera's own prefab as `memorial-lobby.json`; the
  official route reads the official prefab while a camera holds the game's, so no other choice's preview shows a
  camera's framing. The camera's `framing.json` answers its own prefab's view and main layer, so its stills (Switch
  Memorial Lobby cards, the detail page's memorial lobby card) show what its memorial lobby shows at rest.

## Memorial lobby lines

Online data, fetched on demand and kept in memory only (never written to disk):

- Kivo (`lines/kivo.py`): one student page per alt (zh-CN, ja and the voices, by voice clip name).
- The Global server's CharacterDialog table (`lines/global_lines.py`), as ba-data publishes it (one bounded download per
  session, only when the UI language is not zh-CN; `memorialLobby.lines` waits up to 3 s for it, then answers with Kivo's
  lines alone). A dialogue's rows go on the talk animation's voice clips in order. The exported cue fields are
  `animationName` and `animationCues`.

The page shows only the UI language's lines: a dialogue card without one shows a grey, slanted "Dialogue N". The
dialogue host draws the game's `ChatDialog/Talk` balloon (shared with the Student Hall's `student-hall-dialogue.js`),
anchored by the memorial lobby's own Talk position mapped through the current view and clamped to the visible area; fullscreen
keeps the same balloon with widened bounds. The pointer-transparent host exposes the current text through
`role="status"`.

## Student thumbnails

The native fullscreen camera entry on the student detail opens the actual portrait and memorial renderers; it never
enlarges a thumbnail as its capture source. The centered 404:456 guide matches the student list, optional thirds
stay outside output, and the shutter writes a lossless native-pixel PNG directly to the library. The mode button is
drawn only when the student has the other mode (`bridge.info()` tells `hasPortrait` and `hasMemorialLobby`). The camera's
controls reuse the game's prefabs and are laid out with `ui-grid.js`. The gallery (the button at the bottom left) is
the Switch pickers' list popup (`popup-list.js` `listPopup`, with Switch Chibi's square cards). It shows the chosen
thumbnail with Apply, Export and Delete in the details at the left, a scrolling thumbnail list at the right, and
Restore Default, Cancel and Save in the footer.

`thumbnails/index.json` and `thumbnails/images/` belong to the active library epoch. The manifest keeps captures,
App choices, IDs, and SHA-256 digests. Image publication rolls back when metadata cannot commit. A chosen app
thumbnail or one configured or applied in the game cannot be deleted without the protected flow (`thumbnail_in_use`).
An unusable record is held. An unreadable manifest gives `thumbnail_library_invalid`. Save and App selection
use the thumbnail store's lock.

| RPC | Parameters | Result |
| --- | --- | --- |
| `thumbnails.list` | `alt` | Captures, App choice, verified default URL, game intent/availability/error |
| `thumbnails.removalInfo` | `alt, id` | What a deletion would affect |
| `thumbnails.save` | `alt, png, mode, expression?, action?, source?` | Captures/App choice and saved ID |
| `thumbnails.select` | `alt, id` (nullable) | Captures and updated App choice |
| `thumbnails.delete` | `alt, id, confirm?` | Protected deletion job ID |
| `thumbnails.export` | `alt, id, png?` | Lossless export of a capture |
| `thumbnails.apply` | `alt, id, png?` (`id` nullable) | Writer job ID |
| `thumbnails.restore` | `alt, png?` | Default restore job ID; clears App choice after success |
| `thumbnails.defaults` | | `due`, `candidates` (`alt`, `builtin`) of the generated defaults after an upgrade |
| `thumbnails.storeDefault` | `alt, png` | `saved`: the alt's generated default, stored without choosing or applying it |
| `thumbnails.applyDefaults` | `alts, complete?` | Start-up writer job ID, or null when nothing is written |

Generated defaults after an upgrade (`webui/thumbnail_defaults.py`, `app/default-thumbnails.js`): on a profile's
first start after an upgrade (fresh data only records the marker), each alt whose App thumbnail is still its default
and whose Collection texture shows the official art gets the student list card's picture as its App thumbnail and in
the game. It runs once the first screen is up (`boot()` in `main.js`, after `app.ready`), with nothing covering the
screen; its job shows in Tasks (`progress.defaultThumbnailsApply`): the page renders
one card picture at a time and stores it (a picture that fails or takes over 20 s is skipped), then one class 3
job (`thumbnails.applyDefaults`) writes each shared Collection bundle once, composing outside the library lock, and
chooses the written defaults in the App. `thumbnails.defaults` decides first whether the run is due, from the
launch history before the release notes record this launch. A write that fails leaves its bundle, the configuration and the
manifest as they were. The marker (`thumbnailDefaults` in the profile's tour history) is set once a run has nothing
left to try: a refusal of the moment (`LATER` in `library/service/base.py`) or an interrupted run is tried again at
the next start, a refusal that stays (such as `official_unavailable`) is not, and pictures that failed to render are
tried at most `RENDER_TRIES` starts. It waits for a later start without a game, without the Spine licence, or while a
classic window is open with mods on.

On data upgraded from the old format, the legacy fence (`game_write` in `host/data_upgrade.py`) asks for an official
copy an older build can restore from (its kept original or the file's `.backup`) only for files an older build
manages: a path its state applied, or a core its profiles configure, built or troubleshoot. Other files, such as the
Collection bundles, are written without one; after a downgrade, Steam's file check brings them back.

Capture uploads are bounded to 48 MiB encoded PNG data and 64–4096 pixels per dimension. Capture methods that
accept PNG data have the larger HTTP body allowance. Assets use `/asset/thumbnails/<alt>/<id>.png` and
`/asset/thumbnails/default/<install-id>/<alt>.png?revision=<resource-version>` (verified official bundles, so a
changed install cannot serve another install's default). Game Apply replaces only the selected named textures of the
Collection card portrait (`UICharacterCollectionPopulatorUnit/Grid/UICollectCard/Char_Portrait`), with lossless
RGBA32 at native dimensions; restoring one texture inlines its exact official GPU payload, and all-default
compositions restore official bundle bytes. Generated composites are internal managed `other` Mods under the
existing protected writer, rollback journal, official backups and master-switch manifests.
