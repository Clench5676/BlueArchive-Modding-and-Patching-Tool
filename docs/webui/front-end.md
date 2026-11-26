# Front end

`ngui.js` (prefab renderer), the student list, the detail screen, the popups, the consent popup and the memorial lobby player, as ES
modules under `web/js/`, one page with a hash router (`#/student-list`, `#/student/<student>/<alt>`). The modules sit in
folders by screen area: `app/` (start-up, navigation, loading, updates, prompts), `window/` (window chrome and top bar),
`core/` (RPC, strings, shared helpers), `ui/` (NGUI, popups, shared widgets and tokens), `student-list/`, `detail/`, `mods/`
(mod switching and import), `spine/`, `memorial-lobby/`, `chibi/` (3D), `student-hall/`, `settings/` and `thumbnails/`. Data comes from
the RPC methods, art from `/asset/...`, strings from the locale files. Invisible buttons over drawn controls carry their
accessible names from the locale files; a control that picks one of several carries `aria-pressed`.

## Student list

`student-list-order.js` decides which cards the list shows and in what order; `student-list.js` draws them and `display.js` is the game's display settings popup.

- One card per alt, or per student with "Group Alts". Cards with Mods appear before the "No Mod" bar;
  favourites lead each group whatever the sort.
- Segment filters, the display settings' filter groups (options in a group add up, groups narrow) and the sort
  (recently viewed, recently added, name with the UI language's collation, mod count, default). All of it is saved
  (`settings.set` with key `studentList`). The search lasts the session.
- The search matches every name language, the base names, the aliases (the CN server's and the user's own) and the
  file id, ignoring case, width, kana and separators; every word must match.
- Keyboard focus moves the list through its own scroll position; the left and right arrow keys step through students
  as in the PC client (`step-keys.js`), one step at a time, paced by the screen, inert with a modifier
  held, in a text field, under a popup, in a fullscreen view or during a job.
- Back from the detail screen, the list is built behind the loading cover: the visible rows finish their portraits and
  frames before reveal, and pictures are decoded first (`NGUI.decoded`, `image-decode.js`) — WebKit
  lets go of blob images a hidden screen draws, so without the wait the first frames painted bare.
- A visit to an alt (`ui.visit`) is mirrored in the page's student-list data, so "Recently Viewed" updates without a new
  `students.list`.

## Quit

The student list quits the app as the PC client's back key does in the memorial lobby (`quit.js`): the top
bar's round back button and Escape on the list open the game's quit prompt (Cancel / Confirm; Enter and Space confirm
while the prompt holds the focus, per the PC client's key mapping). Escape is left alone when another handler took the
key, while an input method composes, for a held key's repeat, with a modifier, while any popup is open, behind the
loading screen, and on any screen but the list. Confirm calls `app.quit` (refused with `busy` while a writer job
runs; with `queued` while writer jobs only wait, when the page asks again and quits with `cancelQueued`); in browser
mode the page shows Closed after the server stops.

## Bottom navigation

`nav-items.js` lists the bottom bar's entries and decides what a click does; `nav.js` draws them.
Student Hall (`#/student-hall`) is the root page, followed by Students and Batch Import; `route-history.js` records committed
page entries in memory (failed transitions do not become history; direct-entry routes fall back to Student Hall). An
entry is greyed exactly when it has a classic tab and no web screen: it keeps the game's disabled tint (read from
UILobby's locked entry, else NGUI's default grey), stays a focusable button (`aria-disabled`), and a click opens the
hand-off prompt ("Not migrated yet, opens in the classic UI", with Don't Ask Again; the pref `classicPrompt` skips
it). The hand-off is `app.openClassic {tab}` with the corner indicator until the window is up; refusals show as a
notice, one hand-off at a time. Without a game folder the no-game screen shows the navigation too, with plain hints
and Detect / Choose buttons; Settings cannot open without a game folder and says so instead of failing silently.

## Student Hall

`student-hall.js` renders portrait-composition and memorial-lobby modes with the game's `UILobby` controls and
`UILobbyCharacterSelectPopup_Slot` editor cards; `student-hall-fullscreen.js` owns the immersive state. Portrait mode
uses one shared canvas and camera; each instance has independent animation state, position and scale. Layout editing
picks actors by posed attachment vertices against cached atlas-page alpha (decoded off the main thread; until a page
is read its attachments count as opaque); dragging moves, hover controls remove, reorder and mirror, and a selection
pins the toolbar (a gold outline from the game's `SelectLine`). Each portrait toolbar has its own size slider and
Reset; a global size slider (25–150 %) edits the draft. The Add Portraits modal lists library portraits with student list
semantics. Layout changes stay drafts until Save. Memorial mode has an ordered playlist with pointer-capture
reordering (touch holds, edge scrolling, cancellation) and runs each source through the existing memorial lobby renderer, in one
memorial lobby instance per slot (`createMemorialLobby()`: the current scene and a hidden replacement).
`student_hall.py` validates state stored under `student_hall` in UI preferences; revision checks prevent a stale
window from overwriting another's arrangement, and dirty exits prompt. `student-hall-dialogue.js` draws the game's
`ChatDialog/Talk` balloon for portrait dialogue, with a Student Hall-wide turn lock, bounded caches and per-click aborts; no
voice is written to disk. `student-hall-camera.js` owns a temporary non-editing scene view (100–300 %; wheel and
pinch zoom, two-finger swipes pan, reusing the detail viewer's wheel classification); camera updates do not reload
actors or mutate saved state, and the renderer steps the camera at the top of its frame so portraits, background and
hit areas show one view. The office background comes from the game's layered `LobbyCommon` / `LobbyCommonNight`
prefabs (`gameassets/office.py`, served losslessly at native resolution); a saved available choice wins, else the
daytime office, else a collection background.

## Portrait

The detail screen frames the portrait like the game (`portrait-view.js`): the skeleton's origin sits on
UICharacterDetail's `CharacterPos`, never on the skeleton's bounds, so a long weapon neither shrinks nor shifts the
character. **Visual mode** (`visual-mode.js`, framing in `visual-view.js`): the nameplate's rotate-phone button opens
the game's UICharacterVisualMode — the UI slides out, the portrait glides into the whole art over the collection
background with the game's light patterns, press-drag moves, wheel and pinch zoom, and the game's own buttons
(sampled from its animation clip) slide in. Leaving glides back exactly; without the prefab the portrait still goes
fullscreen with the tool's exit button. While any fullscreen view is open the slid-out UI is inert and the focus is
given back on leave.

## Popups

The detail screen's popups are drawn on the game's popup frame (UIPopup_Sort) with its dark backdrop (`BACKDROP` in
`ui-tokens.js`); a click on the backdrop or Escape closes them. The shared module (`popup.js`, with the `popup-*.js`
modules split from it on a stand-in DOM) reads its prefabs once and provides:

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
  design system's ([`design-system/`](../design-system/README.md)). **`message({...})`**: lines of text, optional line
  buttons, checkbox, `plain`, `scroll`; resolves with `{button, checked}` however it closes; without the frame's
  prefab (no game folder) the same message draws plain and answers the same way. **`listPopup({...})`**: details on
  the left, cards in a framed list on the right, single or multi selection, optional student list tools.

**Before the first screen** only the game catalogs the first screen shows are prepared, under the loading screen
(`update-impact.js prepare`, `mods.prepareCatalogs` when `mods.updateDue`'s `catalog` says so: a journal's
recovery, an updated official catalog accepted, the live catalog check, the one-off move to the catalog). The
rebuilds (costume builds of an older rule, outputs of another builder, the reapply after a checked game update, the
retry of failed writes) are queued by the App after `app.ready`, ahead of the maintenance, and show in Tasks; the
generated default thumbnails follow the first screen too. Those start-up jobs never cover the screen: between
entries they yield to the shown page's previews and hand over to a job the user starts. A quit while one runs waits
for its step under way (`app.finishing`, the loading screen with `progress.finishingStep`) and drops the rest; the
next start takes it up from its records.

**Start sequence** (`main.js`, `Popup.sequence`), run once per session when a game folder is known: the start popups
come one at a time after the first screen is revealed, each only when due — the one-time notice of the first start
after a data upgrade ([api](api.md#data-upgrade-notice)), the read-only data folder notice, the
asset consent, the Spine licence notice, the scan consent; then the release update check (its own background check,
never holding startup; the offer waits for open popups and loading work; download and install stay in its popup);
then the game update impact gate (see [Mods already in use](mods-switch.md#mods-already-in-use)); then the prompts of mods back to official and mods
outside the library; then a newer version's release notes once, then the feature tour.

**Spine licence.** The notice says that continuing confirms the user holds a Spine Editor licence; one Confirm, no
close button. Confirm saves `prefs.spine_consent = true` once (`app.spineConsent`); an earlier Yes never shows it, a
stored No stays false and shows the notice until Confirm. `spine-runtime.js ensureSpine` loads the runtime only after
acknowledgement; the first screen uses a static portrait where one exists. Settings' General tab keeps a View button
that opens the same notice without changing state.

**Switch popups** (Switch Portrait / Memorial Lobby / Chibi). The sources (`switch-sources.js`):
the alt's own files and its added mods; for the portrait also the other alts' own and added ones (other
alts' memorial lobbies are not offered — untested in the game). Save changes only the preview (persisted per install and
alt in `webui.json`, exposed as `previewSources`); Apply updates the manifest through `actions.apply` (with Mods
off it only updates the intended source). On an overwritten resource type, Official is greyed with the Overwritten treatment;
greyed cards can still be previewed. Delete always confirms with the mod's name and explains affected entries.
Duplicates show Already in the Mod Library; exact outside game content is adopted without reapplying. Unseen mods
mark their alt chip, tab and student list card; opening a switch popup marks what it offers as seen (`mods.seen`,
independent of the library's legacy flags). **Aliases** (the pencil on the nameplate): the user's own search aliases,
edited in the game's input rows, IME-safe. **Details** (the info bars' button): the shown tab's game files, one row
per bundle with size, date, CRC state and contents.

## Bar motion

`chrome-motion.js` moves the top bar and the bottom menu as the game's home screen moves them (UILobby's `UIAni_Lobby`
clip, kept as `MEMORIAL_LOBBY_ENTRY` in `ui-tokens.js`): both bars come in on the clip's curves once the first screen shows;
immersive scenes (detail and Student Hall fullscreen, thumbnail capture) send them away, a pointer near the window's edge brings
that edge's bar back, and `corner-layout.js` places corner controls around the revealed bars. With the native macOS
toolbar the motion also drives the window buttons (`window.motion`, `CocoaHost.slide`).

## Design tokens

The shared numbers and colours live in `web/js/ui/ui-tokens.js`, each named after the prefab node it comes from; the
scales, controls, popup anatomy and alignment rules are documented in [`design-system/`](../design-system/README.md)
(`tests/js/ui_tokens.test.mjs` pins the call sites, `tests/test_ui_tokens.py` the prefab values).

## Loading screen

`loading.js` draws the game's own loading screen: UILoading's black backdrop and UIRepeater in the bottom-right
corner, animated in `app.css` from the game's clip. Before asset consent, covers use neutral system text and no game
assets; the server selects the initial document's style from consent and the disk cache, so a cached launch is
stylized before modules arrive. `scene(fn)` covers startup, navigation, student steps and detail tabs (fade to black,
indicator, reveal; it warns after 15 s and waits without a client deadline for `student.get` and `students.list`, since
a writer job can hold the library lock; a later navigation supersedes the wait). `wait(promise, {label, cap})` shows
the indicator alone for other waits. The detail screen resolves `show()` only when every part is ready and swaps
changes in one frame. Startup phase durations are recorded in the session log and `app.timings`.

## Pointer and touch effect

`cursor.js` gives the page the PC client's own pointer (`PCIcon_MousePoint`, a 32 CSS px cursor with the texture as
its 2x image) and `touch-fx.js` simulates the touch effect the game spawns on every press (two spinning rings, a
flash, triangles and a trail, drawn on two canvases over the window in linear light).
Both read their textures through the light texture route; without a game the system cursor stays. WebKit drops a
cursor whose box would cross the window's edge, so smaller cuts of the image follow in the list and the pointer is
cut at the edge instead of reverting to the arrow. With `prefers-reduced-motion`, a drag leaves no trail.
