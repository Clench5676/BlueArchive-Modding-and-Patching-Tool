# Desktop

## One instance per data directory

`main()` takes an OS lock on `<data dir>/instance.lock` for the process lifetime (`webui/instance.py`) and names
itself in `instance.json` (`{pid, port, raise_token}`). A second start finds the lock held, posts
`/api/instance/raise` with the raise token (header `X-BAMT-Raise`, never the session token) and exits: the first
instance shows and raises its window. With `--serve` a second start refuses with a message. `app.quit` is refused
with `busy` while a writer job is queued or running, the same rule as the window's close button.

## Classic window

The bottom-bar entries without a web screen, and Settings' More Settings, hand over to the classic Tk GUI
(`webui/classic/`, its child side in `gui/control.py`). It runs in a process of its own on both systems: Tk and
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

## Local error reports and startup recovery

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
