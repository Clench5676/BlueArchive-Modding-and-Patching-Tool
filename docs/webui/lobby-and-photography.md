# Memorial lobby and photography

## Memorial lobby

`lobby.js` frames and plays the lobby, `lobby-touch.js` adds the game's touch interaction in fullscreen. Each lobby is
an instance of its own (`createLobby()`): its state (the lobby shown, players, dialogue, timers, settings, caches) is
made per instance, and its functions come from parts that take that state and keep none: `lobby-players.js` (show,
hide, context recovery), `lobby-view.js` (views, zoom, camera framing, edge extension), `lobby-geometry.js` (painted
rect and limits), `lobby-scene.js` (timers, touch ownership, suspension, the first frame, the intro's controls),
`lobby-clips.js` and `lobby-talk.js` (clips, dialogues, sounds); `lobby-layers.js` and `lobby-measure.js` are
stateless. `lobby.js`'s own exports are the detail screen's instance; the Student Hall makes one per slot. The player
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
- **Particle effects** (`gameassets/particles.py`, `particles.js`, `particles-geometry.js`, `particles-gl.js`): the
  prefabs' ParticleSystems simulated and drawn with the game's DSFX shaders ported from their compiled programs, in
  linear light, sorted with the skeleton parts; emission, shapes, curves, sub-emitters, trails and ring buffers follow
  Unity's rules. Cost is about 0.2–0.6 ms a frame; everything is freed with the lobby. Without WebGL2 nothing is drawn.
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

## Lobby camera framing

- **Entry.** In the fullscreen memorial lobby a visual-mode button (`lobby-camera.js`) enters the framing: the lobby
  rests, and Back, Reset, the photography shutter and an album at the bottom left (the photography gallery's button)
  replace the fullscreen controls. The wheel zooms at the pointer's height, any drag moves the view up or down; the
  view stays 16:9 and centred across.
- **Limits.** `measureFraming` paints every pose the lobby rests in; a cell outside the game's view that some pose
  leaves unpainted may not be shown. Zoom-in stops at 2x the game's view.
- **RPC `lobby.camera.save`** `{alt, lobby, source, view, scale, offset}` starts a library writer job. The saved
  camera is a generated generic mod of the lobby's own UI prefab bundle (`library/lobby_camera.py`): the prefab's
  scale targets are reframed through a camera node, every other object byte for byte, saved with the CRC fix and
  checked after; a prefab whose structure does not fit is refused (`lobby_camera_unsupported`). The intro is held at
  the official framing by a track of the prefab's own copy of the intro timeline (`library/lobby_camera_intro.py`);
  the few prefabs with an Animator on the root keep the older framing (targets scaled directly).
- **Shutter and album.** Each press saves a new choice under Now Loading; then the new choice's still (else, after
  1.5 s, the view on screen) flies into the album. The album opens Switch Memorial Lobby itself (`detail.js`, through
  the camera bridge) over the framing, whose controls take no input meanwhile; a lobby changed there is framed anew
  from the game's view.
- **The choice.** The App lists a camera as a lobby mod of its alt (`camera: {source, number, scale, offset}`),
  named after its source, in Switch Memorial Lobby; rename and delete as for mods. Apply writes the source's art
  through the lobby's own apply, then the prefab through the generic one; any other lobby choice puts the prefab back
  to the camera's base. Before a master-switch plan and a camera's apply, a camera framed on an official prefab the
  game replaced is made again on the new ones in place.
- **Preview.** `/asset/spine/mod/<camera>/` serves the source's art and the camera's own prefab as `lobby.json`; the
  official route reads the official prefab while a camera holds the game's, so no other choice's preview shows a
  camera's framing. The camera's `framing.json` answers its own prefab's view and main layer, so its stills (Switch
  Memorial Lobby cards, the detail page's lobby card) show what its lobby shows at rest.

## Lobby lines

Online data, fetched on demand and kept in memory only (never written to disk):

- Kivo (`lines/kivo.py`): one student page per alt (zh-CN, ja and the voices, by voice clip name).
- The Global server's CharacterDialog table (`lines/global_lines.py`), as ba-data publishes it (one bounded download per
  session, only when the UI language is not zh-CN; `lobby.lines` waits up to 3 s for it, then answers with Kivo's
  lines alone). A dialogue's rows go on the talk animation's voice clips in order.

The page shows only the UI language's lines: a dialogue card without one shows a grey, slanted "Dialogue N". The
dialogue host draws the game's `ChatDialog/Talk` balloon (shared with the Hall's `student-hall-dialogue.js`),
anchored by the lobby's own Talk position mapped through the current view and clamped to the visible area; fullscreen
keeps the same balloon with widened bounds. The pointer-transparent host exposes the current text through
`role="status"`.

## Student photography

The native fullscreen camera entry on the student detail opens the actual portrait and memorial renderers; it never
enlarges a thumbnail as its capture source. The centered 404:456 guide matches the student roster, optional thirds
stay outside output, and the shutter writes a lossless native-pixel PNG directly to the library. The mode button is
drawn only when the student has the other mode (`bridge.info()` tells `hasPortrait` and `hasLobby`). The camera's
controls reuse the game's prefabs and are laid out with `ui-grid.js`. The gallery (the button at the bottom left) is
the Switch pickers' list popup (`popup-list.js` `listPopup`, Switch 3D Model's square cards, layout kind `photo`): the chosen photo with Apply,
Export and Delete in the details at the left, the photos scrolling at the right, Restore Default, Cancel and Save in
the footer.

`photographs/` belongs to the movable/shared Mod library, not Preview Cache. Its atomic manifest keeps per-student
captures and App choices, IDs and SHA-256 digests; image publication rolls back if metadata cannot commit. Chosen
App photographs and configured/applied game photographs cannot be deleted (`photo_in_use`). The manifest is read per
entry: a record that cannot be used costs that entry only and is written back unchanged; a manifest that cannot be
read as a whole is never rewritten and the photograph RPCs answer `photo_library_invalid`. Save and App selection
hold only the manifest's own lock, never the services lock.

| RPC | Parameters | Result |
| --- | --- | --- |
| `photography.list` | `alt` | Captures, App choice, verified default URL, game intent/availability/error |
| `photography.removalInfo` | `alt, id` | What a deletion would affect |
| `photography.save` | `alt, png, mode, face?, action?, source?` | Captures/App choice and saved ID |
| `photography.select` | `alt, id` (nullable) | Captures and updated App choice |
| `photography.delete` | `alt, id` | Updated gallery after protected deletion |
| `photography.export` | `alt, id, png?` | Lossless export of a capture |
| `photography.apply` | `alt, id` (nullable) | Actual writer job ID |
| `photography.restore` | `alt` | Default restore job ID; clears App choice after success |

Capture uploads are bounded to 48 MiB encoded PNG data and 4096 pixels per dimension; only the save RPC gets the
larger HTTP body allowance. Assets use `/asset/photography/<alt>/<id>.png` and
`/asset/photography/default/<install-id>/<alt>.png?revision=<resource-version>` (verified original bundles, so a
changed install cannot serve another install's default). Game Apply replaces only the selected named textures of the
Collection card portrait (`UICharacterCollectionPopulatorUnit/Grid/UICollectCard/Char_Portrait`), with lossless
RGBA32 at native dimensions; restoring one texture inlines its exact original GPU payload, and all-default
compositions restore original bundle bytes. Generated composites are internal managed `other` Mods under the
existing protected writer, rollback journal, official backups and master-switch manifests.
