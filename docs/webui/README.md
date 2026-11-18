# Web UI

The GUI: a pywebview window showing a local web front end that reproduces the in-game Blue Archive UI. Game art is
read from the user's own install at runtime and never shipped with the program.

- [`services.md`](services.md): Rules for every service, gameassets, catalog, Languages
- [`library.md`](library.md): library, generation-2 layout, and game watch
- [`api.md`](api.md): RPC, data upgrade notice, asset routes, and events
- [`mods-switch.md`](mods-switch.md): Mods state, scan and the classic queue, Enable Mods switch, Troubleshoot Mods,
  Mods already in use
- [`front-end.md`](front-end.md): Student list, Quit, Bottom navigation, Student Hall, Portrait, Popups, Bar motion,
  Design tokens, Loading screen, Pointer and touch effect
- [`chibi-actions.md`](chibi-actions.md): the 3D tab's action bar and action lists, clip names, clips on demand
- [`memorial-lobby-and-thumbnails.md`](memorial-lobby-and-thumbnails.md): memorial lobby, camera framing, dialogue, and student thumbnails
- [`desktop.md`](desktop.md): One instance per data directory, Classic window, Tray, Local error reports and startup
  recovery, Release notes and feature tours

## Screens

Composed screens take their sizes, gaps and alignment from the design system ([`design-system/`](../design-system/README.md):
the scales in `ui-tokens.js`, the layout primitives in `ui-grid.js`).

- Screens: consent popup, Student Hall, student list, Import Mod (Batch Import), student detail (Portrait, Memorial
  Memorial Lobby and 3D tabs), the switch popups (portrait, memorial lobby, 3D chibi) and the alt picker. The memorial lobby is
  framed and touched like the game, plays its intro timeline when entered (a tap skips it; the settings' L2D tab
  turns it off) and plays its dialogue with voices and the lines in the UI language; the 3D tab shows the alt's
  chibi, exported from the install or a selected library Mod. A tab can remain available for a library-only Mod even
  when the installation has no corresponding original resource; application remains unavailable in that case.
- Nav entries without a web screen yet open the classic Tk GUI on that tab (`bamt --tab <name>`).
- `bamt` (and the compiled app) starts the web UI; `bamt --classic` starts the classic Tk GUI.
- Released builds look for a newer GitHub release at start and can update themselves (`host/updater.py`). The offer
  is the newest release above the running version that has this platform's build and its checksum; when none has
  them, it is the newest release, without an install button. The unset-preference default is on; an explicitly saved
  startup-update opt-out remains off.

## Process layout

The App lives in `src/bmpt/`; the upstream engine and classic GUI/CLI remain in `src/ba_modding_toolkit/`.
The App imports the engine explicitly. `bmpt/__init__.py` is empty so the engine's fork-added hooks can import
small App modules without initializing the web UI. The launcher (`bmpt.launcher`), `python -m bmpt` and
`python -m bmpt.webui` register `bmpt/locales` with the engine before dispatch; classic strings remain in
`ba_modding_toolkit/locales`. The `ba_modding_toolkit` package's entry point also dispatches the App.

```
pywebview window (main thread) ── HTTP ──> WebServer (threaded WSGI, 127.0.0.1, random port)
                                              /              static front end (bmpt/web)
                                              /upgrade/...   startup state and upgrade decisions
                                              /api/rpc       calls, JSON in and out
                                              /api/events    Server-Sent Events
                                              /asset/...     game assets read from the install
                                              /api/classic/* the classic window's channel (its own token)
                                              /api/instance/raise  a second start (the raise token)
```

The front end only speaks HTTP and SSE, never the pywebview `js_api`, so it runs in any browser during development
(`bamt --browser`, or `--serve` for tools) and tests drive the WSGI app without a window. Native features (file
dialogs, drag-and-drop paths, reveal in the file manager) run in Python; dropped paths reach the page as an event.

The bootstrap, RPC, events, and asset routes use the session token, carried by the port-specific `bamt` cookie set
from `/?t=<token>` or by `X-BAMT-Token`. The host must be `127.0.0.1:<port>` or `localhost:<port>`.

## Packages

```
src/bmpt/
  host/config.py Tk-free settings: same config.toml groups and keys as ba_modding_toolkit/gui/configs.py
  gameassets/    UI atlases, UI textures, prefab layouts, collection art and Spine skeletons from the install
  catalog/       students and their alts, names per language
  library/       portrait, memorial lobby, chibi and other Mods: add, remove, apply, restore, update, extract, compare
  webui/         server, RPC, events, jobs, pywebview window, tray icon, entry point
  web/           static front end, no build step, plain ES modules
```

App module dependencies follow `webui` → `library` → `catalog` → `gameassets` → the existing core modules (`bundle`,
`searching`, `naming`, `utils`, `core`, `mod_install`). `gameassets`, `catalog` and `library` never import `webui`,
`gui`, `tkinter` or `webview`.

## Tests

- `config`, `catalog` and `library` with fixtures and temporary folders (`library`: a fake game folder of header-only
  bundles; its end-to-end checks copy a few real bundles into a temporary folder, never writing into the install).
- `gameassets` and end-to-end checks against a real install; skipped unless one is found (`BAMT_TEST_GAME_DIR`, or
  `game_paths.find_game_dirs`).
- `webui` through its WSGI app, without a window; the tray's menu and controller with fake services.
- The front-end tests use a stand-in DOM. The upgrade notice tests cover the bootstrap flow and all six locales.
- The design tokens: `tests/js/ui_tokens.test.mjs` (the tokens, no retired literal in any web source, caption ranges
  through every locale) and `tests/test_ui_tokens.py` (the tokens against the prefabs of the development view;
  skipped without `BAMT_TEST_GAME_DIR`).
