// Boot and routing: #/student-list (the student list) and #/student/<student>/<alt> (the detail screen). The game's
// loading screen covers the start and every change of screen until the new one is ready; a change inside the detail
// screen (an alt chip, the arrows) is covered too, through its first complete frame.
import { notify } from "../ui/popup.js";
import * as Consent from "./consent.js";
import * as BatchImport from "../mods/batch-import.js";
import * as Cursor from "./cursor.js";
import * as Detail from "../detail/detail.js";
import * as Loading from "./loading.js";
import * as ModSwitch from "../mods/mod-switch.js";
import * as NGUI from "../ui/ngui.js";
import * as Popup from "../ui/popup.js";
import * as Prompts from "./prompts.js";
import * as Impact from "./update-impact.js";
import * as StudentList from "../student-list/student-list.js";
import * as StudentHall from "../student-hall/student-hall.js";
import * as Settings from "../settings/settings.js";
import * as Update from "./update.js";
import * as Tours from "./tours.js";
import * as Tasks from "./tasks.js";
import { on, rpc, spinePrefix, waitJob } from "../core/api.js";
import { setLibraryWaiting } from "../detail/preview-lifecycle.js";
import { L, T, errorText, setStrings, languageRefresh } from "../core/i18n.js";
import { neighbour, visit } from "../student-list/student-list-order.js";
import { hasAsset, primaryKind, tabOfKind } from "../mods/switch-sources.js";
import { renderNav, setTitle, setTopActions } from "./nav.js";
import { confirmQuit, quitKey } from "./quit.js";
import { forget, thumbnailScope } from "../detail/thumbs.js";
import { applyDefaultThumbnails } from "./default-thumbnails.js";
import { setSpineConsent, spineAllowed } from "../spine/spine-runtime.js";
import { $, fitStage } from "../core/util.js";
import { studentName } from "../core/names.js";
import { previewCache } from "../detail/preview-cache.js";
import { ROOT_ROUTE, routeHistory } from "./route-history.js";
import { setupWindowChrome } from "../window/window-chrome.js";
import { chromeMotion } from "../window/chrome-motion.js";
import { installCloseGuard } from "../window/window-close.js";
import { installVerticalWheel } from "../ui/horizontal-scroll.js";
import { startGate, upgradedNoticeFor } from "./data-upgrade.js";
import { COLOUR } from "../ui/ui-tokens.js";
import { pageTitle } from "./dev-mark.js";

// the data upgrade notice first (data-upgrade.js): nothing of the App runs until the data is open in its format
const upgradeState = await startGate();
// the one-time notice of a first start after an upgrade, with the start popups; null when none is due
const upgradedNotice = upgradedNoticeFor(upgradeState, { message: Popup.message, T, warning: COLOUR.warning });
installCloseGuard({
  prepare: () => StudentHall.canLeave(),
  revision: StudentHall.closeRevision,
  root: () => [document.querySelector(".viewport"), document.querySelector(".window-header")].filter(Boolean),
});
setupWindowChrome();
installVerticalWheel();

globalThis.BAMTRecovery?.presentReport((result) =>
  Popup.message({
    title: T("recovery.report"),
    lines: result
      ? [
          T("recovery.saved"),
          result.path,
          T(result.copied ? "recovery.copied" : "recovery.copyFailed"),
          ...(!result.copied ? [result.summary] : []),
        ]
      : [T("recovery.actionFailed")],
    ok: T("picker.ok"),
    plain: true,
  }),
);

let info = null,
  studentListData = null,
  studentListStale = true,
  resourceEpoch = 0,
  configurationRevision = 0;
const startup = {};
async function timed(phase, work) {
  const began = performance.now();
  globalThis.BAMTRecovery?.begin(phase);
  try {
    return await work();
  } catch (error) {
    if (globalThis.BAMTRecovery?.isBooting()) globalThis.BAMTRecovery.fail(error);
    throw error;
  } finally {
    startup[phase] = performance.now() - began;
    globalThis.BAMTRecovery?.end(phase, startup[phase]);
  }
}
// the student list's cards as the list showed them when it was left (its filters, search and sort): the detail screen's
// arrows step through these, so visits and edits on the way do not reshuffle them; taken afresh once the student list
// shows
let stepCards = null;

function relabelShell() {
  document.title = pageTitle(T("top.students"));
  document.querySelector(".topbar .back").setAttribute("aria-label", T("a11y.back"));
  $("fs-exit").setAttribute("aria-label", T("memorialLobby.exit"));
  $("top-option").setAttribute("aria-label", T("settings.button"));
  $("top-home").setAttribute("aria-label", T("studentHall.home"));
  Tasks.relabel();
  const back = document.querySelector(".topbar .back").onclick;
  setTitle(
    T(
      !$("screen-student-hall").hidden
        ? "studentHall.title"
        : !$("screen-import").hidden
          ? "nav.import"
          : "top.students",
    ),
    back,
  );
  Loading.relabel();
}
const refreshLanguage = languageRefresh({
  load: (lang) => rpc("i18n.strings", { lang }),
  refresh: async (lang) => {
    info.language = lang;
    stepCards = null;
    relabelShell();
    await document.fonts.ready;
    await Promise.all([
      renderNav(!$("screen-student-hall").hidden ? -1 : !$("screen-import").hidden ? 1 : 0),
      ModSwitch.relabel(),
      StudentHall.relabel(),
      StudentList.relabel(),
      Detail.relabel(),
      BatchImport.relabel(),
    ]);
  },
});

const ctx = {
  dragged: false,
  back: () => goBack(),
  go: (student, alt, kind) => {
    const target = tabOfKind(kind) || (["portrait", "memorialLobby", "chibi"].includes(kind) ? kind : null);
    const tab = target ? `/${target}` : "";
    location.hash = `#/student/${encodeURIComponent(student)}/${encodeURIComponent(alt)}${tab}`;
  },
  invalidateStudentList: () => {
    studentListStale = true;
    studentListGen += 1;
    StudentList.invalidate();
  },
  // students.list's data (read again once invalidated): Switch Chibi's other students. ``stale``: the copy in
  // hand at once, even when invalidated, read again in the background (students.list waits for the library's
  // lock, held by a costume check for seconds)
  studentList: ({ stale = false } = {}) => {
    if (!stale || !studentListData || studentListEpoch !== resourceEpoch) return studentList();
    if (studentListStale) studentList().catch(() => {});
    return Promise.resolve(studentListData);
  },
  // the card before or after the shown alt (merged: student) in the student list's order, within its filters
  step: async (delta, student, alt) => {
    const data = await studentList();
    stepCards ||= StudentList.order(data);
    const next = neighbour(
      stepCards,
      () => StudentList.order(data, { unfiltered: true }),
      { student, alt, groupAlts: StudentList.groupAlts() },
      delta,
    );
    if (next) ctx.go(next.student, next.key);
  },
};

// one students.list at a time: callers while it runs share its answer, unless the student list was invalidated since it
// began (studentListGen); studentListEpoch: the resources the data was read for
let studentListRead = null,
  studentListGen = 0,
  studentListEpoch = -1;
function studentList() {
  if (studentListData && !studentListStale) return Promise.resolve(studentListData);
  if (studentListRead?.gen === studentListGen) return studentListRead.done;
  const gen = studentListGen;
  const done = (async () => {
    const epoch = resourceEpoch,
      next = await rpc("students.list");
    if (epoch !== resourceEpoch) throw new DOMException("Game resources changed", "AbortError");
    const before = studentListData;
    studentListData = next;
    studentListEpoch = epoch;
    if (gen === studentListGen) studentListStale = false;
    forgetReplaced(before, studentListData);
    return studentListData;
  })().finally(() => {
    if (studentListRead?.done === done) studentListRead = null;
  });
  studentListRead = { gen, done };
  return done;
}

// the portraits whose game files carry something else than before (the classic UI closed, a job that stopped midway:
// library.changed without its cores): their thumbnails are rendered again
function forgetReplaced(before, after) {
  if (!before) return;
  const was = new Map(before.students.flatMap((f) => f.alts.map((v) => [v.key, v.replaced ?? null])));
  for (const f of after.students) {
    for (const v of f.alts)
      if (hasAsset(v) && was.has(v.key) && was.get(v.key) !== (v.replaced ?? null)) {
        const kind = primaryKind(v);
        forget(spinePrefix(kind, kind === "portrait" ? v.portrait : v.memorialLobby));
      }
  }
}

// an entry's alt named as the student list names it (entries of cores the catalog does not map carry their shipped
// names, else their core); resolves once the student list's data is in
async function altNames() {
  const data = await studentList().catch(() => studentListData);
  const byKey = new Map((data?.students || []).flatMap((f) => f.alts.map((v) => [v.key, v])));
  return (entry) => {
    const v = byKey.get(entry?.alt);
    return studentName(v, (v && L(v.names)?.full) || L(entry?.names)?.full || entry?.alt || entry?.core || "");
  };
}

// the progress line's names (loading.js, job-progress.js modLine): the student list's as it is now, the key until it is
// in
let nameIndex = new Map(),
  nameIndexOf = null;
function progressNames(entry) {
  if (nameIndexOf !== studentListData) {
    nameIndexOf = studentListData;
    nameIndex = new Map((studentListData?.students || []).flatMap((f) => f.alts.map((v) => [v.key, v])));
  }
  const v = nameIndex.get(entry?.alt);
  return studentName(v, (v && L(v.names)?.full) || entry?.alt || entry?.core || "");
}

// Go To from the update summary or the held entries' Details (update-impact.js): the mod's student page on the tab of
// its kind, its switch open on the mod (Detail.takeWanted); Settings closes first
async function openMod({ id, alt, resourceType }) {
  const data = await studentList().catch(() => studentListData);
  const student = (data?.students || []).find((f) => f.alts.some((v) => v.key === alt));
  if (!student || !id) return;
  if (!$("settings").hidden) Settings.close();
  Detail.want({ id, alt, resourceType });
  const before = location.hash;
  ctx.go(student.key, alt, resourceType);
  if (location.hash === before) await Detail.takeWanted();
}

// --- library.changed: a writer job switched game files ({cores, resourceTypes}), or anything may have changed (no data: the
// classic UI closed, a job stopped midway, the mod list changed). The student list is read again, the thumbnails of
// those cores are forgotten (both live and official sources, including other installs' bases) and the detail screen
// reloads what it shows of them. files: only game files or the Enable Mods switch changed, the mod list is as it
// was (a change that names its cores is one too) ---
let heldChange,
  heldFiles = true,
  changeTimer = null,
  changeRoute = 0;
function libraryChanged(data, { files = false } = {}) {
  // An import changes the mod list without changing game files.
  const change = data && !data.mods && Array.isArray(data.cores) ? { cores: data.cores } : null;
  if (change && !change.cores.length) return;
  BatchImport.invalidate();
  for (const core of change?.cores || []) {
    const kinds = data.resourceTypes?.length
      ? data.resourceTypes
      : [/_spr$/i.test(core) ? "portrait" : "memorialLobby"];
    for (const kind of kinds) {
      forget(spinePrefix(kind, core));
      forget(spinePrefix(`official-${kind}`, core));
    }
  }
  ctx.invalidateStudentList();
  heldFiles = (heldChange === undefined || heldFiles) && (files || change !== null);
  heldChange =
    heldChange === undefined
      ? change
      : heldChange === null || change === null
        ? null
        : { cores: [...new Set([...heldChange.cores, ...change.cores])] };
  changeRoute = routeToken;
  clearTimeout(changeTimer);
  changeTimer = setTimeout(applyChange, 150); // the events of one job come together
}
// now: the end of the write itself (refreshAfterToggle), drawn at once
async function applyChange({ now = false } = {}) {
  const change = heldChange,
    files = heldFiles;
  heldChange = undefined;
  heldFiles = true;
  if (change === undefined) return;
  const my = changeRoute;
  if (!$("screen-detail").hidden) return Detail.filesChanged(change);
  // The Student Hall shows library mods and official copies, never the game files as they are: a write of game
  // files or the switch leaves it standing, and only its list of choices is read again.
  if (files && !$("screen-student-hall").hidden) {
    StudentHall.refreshOptions?.();
    return;
  }
  // The Student Hall and the list are drawn again once no popup is open and no write the user watches is under way: a
  // switch that flips midway (Enable Mods turned on or off) keeps the shown screen under Now Loading, and the
  // write's own end draws it, unless a route came meanwhile (it reads the list itself).
  const idle = () => Popup.whenIdle(200, () => !now && Loading.writing());
  if (!$("screen-student-hall").hidden) {
    await idle();
    if (my === routeToken && !$("screen-student-hall").hidden) await route();
    return;
  }
  if ($("screen-student-list").hidden) return;
  await idle();
  if (my === routeToken && !$("screen-student-list").hidden) await route();
}

// A completed toggle refreshes directly, even if its event has not arrived or is still being coalesced. A toggle
// that added mods to the library (an Off keeping the mods it found) changed the mod list too.
async function refreshAfterToggle(result = null) {
  libraryChanged(null, { files: !!result && !result.adopted?.length && !result.dropped?.length });
  clearTimeout(changeTimer);
  await applyChange({ now: true });
}

// the window back in front after 30 s or more: the server checks again with scan consent
let hiddenAt = 0;
function watchFront() {
  const away = () => {
    hiddenAt ||= Date.now();
  };
  const back = () => {
    if (hiddenAt && Date.now() - hiddenAt >= 30000 && ModSwitch.current()?.scan === true)
      rpc("mods.scan").catch(() => {});
    hiddenAt = 0;
  };
  addEventListener("blur", away);
  addEventListener("focus", back);
  document.addEventListener("visibilitychange", () => (document.hidden ? away() : back()));
}

// the visit the detail screen records (ui.visit) goes into the student list's data too, so "Recently Viewed" has it on
// the way back; the alt is the one the detail screen shows for the link
function visited(shownStudent, key) {
  const vs = shownStudent?.alts || [];
  const shown = vs.some((v) => v.key === key && hasAsset(v)) ? key : (vs.find(hasAsset) || vs[0])?.key;
  if (studentListData && shown) visit(studentListData, shown);
}

function showScreen(name) {
  setTopActions(name === "student-hall", !!info?.gameFound);
  for (const id of ["screen-student-hall", "screen-student-list", "screen-detail", "screen-import", "screen-message"])
    $(id).hidden = id !== `screen-${name}`;
}

function message(title, hint, actions = []) {
  showScreen("message");
  $("message-title").textContent = title;
  $("message-hint").textContent = hint || "";
  const row = $("message-actions");
  row.replaceChildren(
    ...actions.map(([caption, fn]) => {
      const b = document.createElement("button");
      b.className = "message-button";
      b.textContent = caption;
      b.addEventListener("click", fn);
      return b;
    }),
  );
}

// the quit prompt (quit.js): the student list's back button and Escape on the list. In the app's window the quit
// closes it; in browser mode the server stops once it has answered, and the page says so
function askQuit() {
  return confirmQuit({
    message: Popup.message,
    rpc,
    T,
    notify,
    native: !!info?.native,
    closed: () => {
      $("stage").classList.add("closed");
      message(T("app.closed"));
    },
  });
}
addEventListener("keydown", (ev) => {
  if (!quitKey(ev, { byId: (id) => document.getElementById(id), covering: Loading.covering() })) return;
  ev.preventDefault();
  askQuit();
});

// The top bar's Settings button. Settings is drawn from the game's UI, which a start without a game folder lacks: the
// click then says so in the plain notice (the frame is missing too), and a failure with a game is reported as one.
// Either way a click never fails silently.
async function openSettings(changed) {
  try {
    await Settings.open(changed);
  } catch (err) {
    console.error(err);
    if (!info?.gameFound) Popup.notice(T("app.noGame.hint"));
    else notify(errorText(err), { kind: "error", error: err });
  }
}

// without a game folder the game's UI cannot be drawn: plain buttons to find or choose one
function noGame() {
  const region = ctx.prefs?.fileSource === "windows_japan" ? "japan" : "global";
  const setDir = async (path) => {
    try {
      await rpc("settings.set", { key: `gameDir.${region}`, value: path });
      await refreshConfiguration();
    } catch (err) {
      notify(errorText(err), { kind: "error", error: err });
    }
  };
  message(T("app.noGame.title"), T("app.noGame.hint"), [
    [
      T("settings.detect"),
      async () => {
        const found = await Consent.detectGame(region, plainScanConsent).catch(() => []);
        if (found === null) return; // declined: the folder is chosen by hand
        if (found.length) setDir(found[0]);
        else notify(T("app.noGame.hint"), { kind: "error", error: { code: "game_directory_missing" } });
      },
    ],
    ...(info?.native
      ? [
          [
            T("settings.change"),
            async () => {
              const p = await rpc("settings.pickFolder", {}).catch(() => null);
              if (p) setDir(p);
            },
          ],
        ]
      : []),
  ]);
}

// the Check Game Files consent where the game's UI cannot be drawn (no game folder yet): the same text in the plain
// built-in style, answered with its two buttons; the no-game screen comes back after the answer
function plainScanConsent() {
  return new Promise((resolve) => {
    const lines = [
      T("mods.scan.will.label"),
      T("mods.scan.will.steam"),
      T("mods.scan.will.items"),
      T("mods.scan.willNot.label"),
      T("mods.scan.willNot.items"),
      T("mods.scan.note"),
    ];
    const answer = (granted) => async () => {
      try {
        await rpc("mods.consent", { granted });
      } catch (err) {
        notify(errorText(err), { kind: "error", error: err });
        return;
      }
      noGame();
      resolve(granted);
    };
    message(T("mods.scan.title"), lines.join("\n"), [
      [T("mods.scan.no"), answer(false)],
      [T("mods.scan.yes"), answer(true)],
    ]);
  });
}

async function refreshConfiguration() {
  const revision = ++configurationRevision;
  ++resourceEpoch;
  ++routeToken;
  ++routeAttempt;
  endRoute();
  StudentHall.hide();
  Detail.hide();
  StudentList.hide();
  BatchImport.invalidate();
  stepCards = null;
  ctx.invalidateStudentList();
  const [next, prefs] = await Promise.all([rpc("app.info"), rpc("settings.get")]);
  if (revision !== configurationRevision) return;
  info = next;
  ctx.info = next;
  ctx.prefs = prefs;
  previewCache.configure(prefs);
  NGUI.setTierTags?.(prefs?.buttonSizeTags === true);
  if (NGUI.setResourceContext?.(next.resourceContext)) forget();
  await renderNav(-1, { game: !!next.gameFound });
  if (revision !== configurationRevision) return;
  if (!next.gameFound) {
    noGame();
    return;
  }
  await NGUI.init();
  if (revision !== configurationRevision) return;
  await route();
  // a game found after a start without one: the first-run sequence runs now, not at the next launch
  if (revision === configurationRevision) firstRun().catch(console.error);
}

let routeToken = 0,
  routeAttempt = 0,
  permissionAttempt = 0,
  endRoute = () => {},
  homeRequested = false;
const navigation = routeHistory();
let shownRoute = ROOT_ROUTE;
let stagedDetail = null;
// The Student Hall screen loads hidden. A reload can start while another is still loading (a mod toggle refreshes
// directly and again on its library event), so the screen as it was before the first one is what is put back, never the
// hidden state an unfinished load left.
let stagedStudentHall = null;
function stageStudentHall(target) {
  stagedStudentHall ||= { hidden: target.hidden };
  target.hidden = false;
  target.style.visibility = "hidden";
}
function unstageStudentHall(target, committed) {
  if (!stagedStudentHall) return;
  if (!committed) target.hidden = stagedStudentHall.hidden;
  target.style.visibility = "";
  stagedStudentHall = null;
}
function goBack() {
  return withStudentHallPermission(() => {
    const destination = navigation.back(location.hash || ROOT_ROUTE);
    if (destination) location.hash = destination;
    else
      return Promise.resolve(askQuit()).then((result) => {
        if (result !== "quit" && !$("screen-student-hall").hidden) return StudentHall.settleView?.();
      });
  });
}
function withStudentHallPermission(action) {
  const permission = ++permissionAttempt;
  if ($("screen-student-hall").hidden) return action();
  return Promise.resolve(StudentHall.canLeave?.())
    .then((allowed) => {
      if (allowed !== false && permission === permissionAttempt) return action();
    })
    .catch((err) => {
      if (permission === permissionAttempt) notify(errorText(err), { kind: "error", error: err });
    });
}
function goHome() {
  return withStudentHallPermission(() => {
    if ((location.hash || ROOT_ROUTE) === ROOT_ROUTE && !$("screen-student-hall").hidden) {
      navigation.commit(ROOT_ROUTE, { home: true });
      return StudentHall.settleView?.();
    }
    homeRequested = true;
    location.hash = ROOT_ROUTE;
  });
}
async function route() {
  ++permissionAttempt;
  const attempt = ++routeAttempt,
    address = location.hash || ROOT_ROUTE;
  if (!$("screen-student-hall").hidden) {
    try {
      const allowed = await StudentHall.canLeave?.();
      if (attempt !== routeAttempt) return;
      if (allowed === false) {
        homeRequested = false;
        if (location.hash !== shownRoute) history.replaceState(null, "", shownRoute);
        return;
      }
    } catch (err) {
      if (attempt !== routeAttempt) return;
      homeRequested = false;
      if (location.hash !== shownRoute) history.replaceState(null, "", shownRoute);
      notify(errorText(err), { kind: "error", error: err });
      return;
    }
  }
  endRoute();
  if (stagedDetail) {
    stagedDetail.target.hidden = true;
    stagedDetail.target.style.visibility = "";
    stagedDetail.target.inert = false;
    stagedDetail = null;
  }
  const ended = new Promise((resolve) => {
    endRoute = resolve;
  });
  const my = ++routeToken;
  const current = () => my === routeToken && attempt === routeAttempt;
  const parts = address.replace(/^#\/?/, "").split("/").map(decodeURIComponent);
  if (parts[0] !== "import") BatchImport.hide();
  if (parts[0] !== "student-hall") {
    StudentHall.hide();
    unstageStudentHall($("screen-student-hall"), false);
  }
  renderNav(parts[0] === "student-hall" ? -1 : parts[0] === "import" ? 1 : 0);
  try {
    if (parts[0] === "student-hall") {
      StudentList.hide();
      await Loading.scene(
        async () => {
          Detail.suspend();
          Detail.hide();
          const target = $("screen-student-hall");
          let committed = false;
          stageStudentHall(target);
          try {
            await StudentHall.show(ctx);
            if (!current()) return;
            showScreen("student-hall");
            setTitle(T("studentHall.title"), goBack);
            committed = true;
          } finally {
            if (my === routeToken) unstageStudentHall(target, committed);
          }
        },
        { until: ended },
      );
    } else if (parts[0] === "import") {
      StudentList.hide();
      await Loading.scene(
        async (covered) => {
          await covered;
          if (!current()) return;
          Detail.suspend();
          const data = await studentList();
          if (!current()) return;
          await BatchImport.show(ctx, data);
          if (!current()) return;
          Detail.hide();
          showScreen("import");
          setTitle(T("nav.import"), goBack);
        },
        { until: ended },
      );
    } else if (parts[0] === "student" && parts[1]) {
      // leaving the list: its cards as shown are the arrows' order
      if (!stepCards && studentListData && !$("screen-student-list").hidden)
        stepCards = StudentList.order(studentListData);
      StudentList.invalidate();
      StudentList.hide();
      const inPlace = !$("screen-detail").hidden && !!Detail.currentAlt();
      await Loading.hold(
        () =>
          Loading.scene(
            async (covered) => {
              // no client deadline, as students.list: a writer job can hold the library lock for its whole run
              const shownStudent = await rpc("student.get", { key: parts[1] });
              await covered;
              if (!current()) return; // a later navigation took over
              visited(shownStudent, parts[2]);
              const target = $("screen-detail"),
                wasHidden = target.hidden;
              let committed = false;
              if (wasHidden) {
                target.hidden = false;
                target.style.visibility = "hidden";
                target.inert = true;
                stagedDetail = { target, token: my };
              }
              try {
                await Detail.show(ctx, shownStudent, parts[2], { inPlace, kind: parts[3] });
                if (!current()) return;
                showScreen("detail");
                committed = true;
              } finally {
                if (stagedDetail?.token === my) {
                  target.hidden = !committed;
                  target.style.visibility = "";
                  target.inert = false;
                  stagedDetail = null;
                }
              }
            },
            { until: ended },
          ),
        { until: ended },
      );
    } else {
      const reveal = () => {
        Detail.hide();
        showScreen("student-list");
        stepCards = null;
        setTitle(T("top.students"), goBack);
      };
      const open = async (covered) => {
        await covered;
        if (!current()) return;
        // Release detail page work before a student list RPC can wait on a writer's library lock.
        Detail.suspend();
        const data = await studentList();
        if (!current()) return;
        try {
          await StudentList.show(ctx, data);
        } catch (err) {
          if (current()) reveal();
          throw err;
        }
        if (!current()) return;
        reveal();
      };
      await Loading.scene(open, { until: ended });
    }
    if (current()) {
      shownRoute = navigation.commit(address, { home: homeRequested && address === ROOT_ROUTE });
      homeRequested = false;
    }
  } catch (err) {
    if (!current() || err.name === "AbortError") return;
    homeRequested = false;
    if (globalThis.BAMTRecovery?.isBooting() && err.code !== "game_not_found") throw err;
    console.error(err);
    if (err.code === "game_not_found") {
      noGame();
      return;
    }
    notify(errorText(err), { kind: "error", error: err });
    if (parts[0] === "student") Detail.stepFailed();
    // a student the catalog does not have (a stale or mistyped link): the screen shown before, its portrait,
    // memorial lobby or 3D view, does not stay under that address; back to the list, under the error popup
    if (parts[0] === "student" && my === routeToken && String(err.code || "").startsWith("catalog_")) {
      Detail.hide();
      location.replace("#/student-list");
    } else if (location.hash !== shownRoute) {
      // The previous page has released its players/input. Re-enter its route to rebuild it, rather than exposing
      // its closed DOM under the failed destination's address (detail tabs deliberately ignore clicks when closed).
      BatchImport.hide();
      location.replace(shownRoute);
    }
  }
}

// The generated default thumbnails after an upgrade (default-thumbnails.js), once the first screen is up: the cards'
// pictures rendered, then written by a start-up job shown in Tasks; nothing covers the screen
async function defaultThumbnails() {
  const scope = thumbnailScope();
  try {
    return await applyDefaultThumbnails({
      rpc,
      waitJob,
      students: studentList,
      render: (p) => scope.thumb(p.base, "studentList", null, p.memorialLobby, p.framing, p.background),
      spine: spineAllowed,
    });
  } finally {
    scope.cancel();
  }
}

// A quit while a start-up job runs: the job ends its step under way first (app.finishing), under the loading screen
let finishing = null;
function finishingStep(busy) {
  if (!busy) {
    finishing?.();
    finishing = null;
    return;
  }
  if (finishing) return;
  const done = new Promise((resolve) => {
    finishing = resolve;
  });
  Loading.task(() => done, { label: T("progress.finishingStep") }).catch(console.error);
}

// The first-run sequence: the cursor, the start popups, the update check and the feature tour. It needs a game
// folder, so it runs once per session: at the start, or, when the start found none, as soon as the folder is
// chosen on the no-game screen (refreshConfiguration), without a restart. sessionReady is set by boot() once the
// page has told the server it is up; until then boot() itself starts the sequence if a game is found.
// startWithGame: start() gave the consent and the loading screen the data of a start with a game; without it
// they still hold their no-game data and are given the data of the game that was found.
let sessionReady = null,
  firstRunStarted = false,
  startWithGame = false,
  dataDirNoticed = false;
async function firstRun() {
  if (!sessionReady || firstRunStarted || !info?.gameFound) return;
  firstRunStarted = true;
  if (!startWithGame) {
    startWithGame = true;
    Consent.init(info);
    setSpineConsent(info.spineConsent);
    Loading.init(info);
  }
  Cursor.init();
  // the start popups only once the first screen is revealed, the data folder notice first
  const popups = Popup.sequence([
    ...(upgradedNotice ? [["upgraded", upgradedNotice]] : []),
    ...(info.dataDirWritable === false ? [["data-dir", dataDirNotice]] : []),
    ...startPopups(),
  ]);
  // The network check never holds the start sequence or a screen. Keep a late offer until the UI is free.
  if (ModSwitch.startDue("update", { info })) {
    Update.check($("consent"), undefined, {
      beforeShow: async () => {
        await popups;
        await Popup.whenIdle(200, () => Loading.busy() || ModSwitch.current()?.busy);
      },
    }).catch((err) => console.error("update check", err));
  }
  await popups;
  const blocked = () =>
    Loading.busy() ||
    ModSwitch.current()?.busy ||
    Prompts.isBusy() ||
    Impact.isBusy() ||
    !!document.getElementById("bamt-recovery");
  // after an upgrade, the new version's release notes, then its feature tour
  if (!info.safeMode) {
    await sessionReady;
    await Update.afterUpgrade({ blocked }).catch((err) => console.warn("release notes after an upgrade", err));
  }
  Tours.start({ info, ready: sessionReady, blocked }).catch((err) => console.warn("feature tour", err));
}

async function boot() {
  fitStage();
  addEventListener("resize", fitStage);
  // the page starts behind the loading screen: the game's UI is read, then the first screen is built; the bars come in
  // as the game's home screen brings them (chrome-motion.js), also over a recovery page, with the window buttons
  try {
    await timed("boot", () => Loading.scene(start, { opaque: true }));
  } finally {
    chromeMotion().enter();
  }
  if (globalThis.BAMTRecovery && !globalThis.BAMTRecovery.ready()) return;
  sessionReady = rpc("app.ready", { timings: { ...startup, ...Loading.timings() } }).catch(console.error);
  // the first start after an upgrade: the generated default thumbnails, after the first screen, never awaited
  if (info?.gameFound)
    sessionReady.then(() => defaultThumbnails()).catch((err) => console.error("default thumbnails", err));
  if (!info?.gameFound) {
    // the no-game screen has no other start popup
    await upgradedNotice?.().catch(console.error);
    await dataDirNotice().catch(console.error);
  }
  await firstRun();
}

// A data folder that cannot be written: one notice per session, the quit dialog's title and Confirm only
function dataDirNotice() {
  if (!info || info.dataDirWritable !== false || dataDirNoticed) return Promise.resolve();
  dataDirNoticed = true;
  return Popup.message({
    title: T("quit.title"),
    lines: [T("error.data_dir_readonly", { path: info.dataDir })],
    ok: T("picker.ok"),
  });
}

// The start popups, one at a time and in this order, each only when it has something to show (mod-switch.js
// startDue); each step resolves once its popup has closed (docs/webui/front-end.md, "Start sequence"):
//   - the asset consent;
//   - the Spine licence notice, until it is acknowledged (a stored No shows it again);
//   - the scan consent, while it is unanswered and a game folder is known;
//   - the impact of a game update on the library's mods, once per resource version (update-impact.js);
//   - the catalog notices: the official catalog put back at the start, a fallback to file replacement (mod-switch.js);
//   - the prompts of mods back to official and of mods outside the library, if the start scan is already in;
//     a later scan shows them once no other popup is open, only with the scan consent
function startPopups() {
  const due = (name) => ModSwitch.startDue(name, { info, state: ModSwitch.current() });
  return [
    [
      "consent",
      async () => {
        if (!due("consent")) return;
        info.consent = await Consent.show($("consent"), info.cacheDir, info.native);
        Loading.init(info);
      },
    ],
    [
      "spine",
      async () => {
        if (!due("spine")) return;
        info.spineConsent = await Consent.spine($("consent"));
        if (info.spineConsent === true) await route();
      },
    ],
    ["scan", () => due("scan") && Consent.scan($("consent"))],
    ["impact", () => info.gameFound && Impact.ask()],
    ["catalog", () => ModSwitch.tellCatalog()],
    ["drift", () => Prompts.start("drift", info)],
    ["untracked", () => Prompts.start("untracked", info)],
  ];
}

async function start() {
  const prefs = timed("settings", () => rpc("settings.get"));
  info = await timed("info", () => rpc("app.info"));
  ctx.info = info;
  NGUI.setResourceContext?.(info.resourceContext);
  Consent.init(info);
  setSpineConsent(info.spineConsent);
  Loading.init(info); // the loading screen's backdrop from the game's prefab
  startWithGame = !!info.gameFound;
  const ui = info.gameFound ? timed("ui-indexes", () => NGUI.init()) : Promise.resolve();
  ui.catch(() => {}); // observed below after the strings and fonts
  ctx.prefs = await prefs;
  previewCache.configure(ctx.prefs);
  NGUI.setTierTags?.(ctx.prefs?.buttonSizeTags === true);
  StudentList.init(ctx.prefs?.studentList);
  setStrings(info.language, await timed("strings", () => rpc("i18n.strings", { lang: info.language })));
  Loading.relabel();
  document.title = pageTitle(T("top.students"));
  document.querySelector(".topbar .back").setAttribute("aria-label", T("a11y.back"));
  $("fs-exit").setAttribute("aria-label", T("memorialLobby.exit"));
  $("top-option").setAttribute("aria-label", T("settings.button"));
  $("top-home").setAttribute("aria-label", T("studentHall.home"));
  $("top-home").hidden = !info.gameFound;
  // subscribed before the no-game screen too, so choosing the game folder elsewhere reaches the page
  on("config.changed", () => refreshConfiguration().catch(console.error));
  // a refused quit also opens the Tasks panel (tasks.js), which shows the job that holds it
  on("app.busy", () => notify(T("error.busy"), { kind: "error", error: { code: "busy" } }));
  on("app.finishing", (data) => finishingStep(!!data?.busy));
  await timed("fonts", () => document.fonts.ready);
  // the bottom bar: its greyed entries hand over to the classic UI (with or without a game folder), their prompt
  // following the pref classicPrompt
  const navOptions = {
    game: !!info.gameFound,
    prefs: () => ctx.prefs,
    setPrefs: (prefs) => {
      ctx.prefs = prefs;
    },
    canLeave: () => $("screen-student-hall").hidden || StudentHall.canLeave?.(),
  };
  await ui;
  renderNav(-1, navOptions);
  Tasks.init({ nameOf: progressNames });
  // the Enable Mods switch in the top bar, and what the writer jobs switched
  await timed("mod-state", () => ModSwitch.init({ names: altNames, refreshPage: refreshAfterToggle }));
  let enabled = ModSwitch.current()?.enabled !== false;
  ModSwitch.onChange((state) => {
    const next = state.enabled !== false;
    if (next === enabled) return;
    enabled = next;
    libraryChanged(null, { files: true });
  });
  Prompts.init({ names: altNames });
  Impact.init({ names: altNames, go: openMod });
  Loading.setNames(progressNames);
  const settingsChanged = async (key, prefs) => {
    ctx.prefs = prefs;
    previewCache.configure(prefs);
    NGUI.setTierTags?.(prefs?.buttonSizeTags === true);
    if (key === "cacheCleared" || key === "previewCache" || key === "cacheDir") return;
    if (key === "language") {
      await refreshLanguage(prefs.language);
      return;
    }
    Detail.applyPrefs(prefs);
    StudentHall.applyPrefs?.(prefs);
    if (key === "fileSource" || key.startsWith("gameDir") || key === "libraryDir") {
      await refreshConfiguration();
    }
  };
  ctx.goOther = () => Settings.open(settingsChanged, "mods");
  on("library.changed", libraryChanged);
  on("library.waiting", (data) => setLibraryWaiting(!!data?.busy));
  on("preview.waiting", (data) => setLibraryWaiting(!!data?.busy, "job"));
  on("thumbnails.changed", () => {
    ctx.invalidateStudentList();
    if ($("screen-student-list").hidden) return;
    const token = routeToken;
    Popup.whenIdle()
      .then(() => {
        if (token === routeToken && !$("screen-student-list").hidden) return route();
      })
      .catch(console.error);
  });
  watchFront();
  // Only Import Mod takes files dropped on the window; every other page refuses them.
  const importing = () => location.hash.replace(/\/$/, "") === "#/import";
  addEventListener("dragover", (event) => {
    if (importing() || !event.dataTransfer) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = "none";
  });
  on("drop", async ({ paths }) => {
    if (!importing()) return;
    try {
      await BatchImport.drop(paths);
    } catch (err) {
      notify(errorText(err), { kind: "error", error: err });
    }
  });
  $("top-option").addEventListener("click", () => openSettings(settingsChanged));
  $("top-home").addEventListener("click", goHome);
  if (!location.hash) history.replaceState(null, "", ROOT_ROUTE);
  addEventListener("hashchange", route);
  if (!info.gameFound) {
    noGame();
    return;
  }
  // only the game catalogs the first screen shows are prepared under the loading screen (update-impact.js prepare);
  // the rebuilds follow app.ready in Tasks, and a game update's question comes with the start popups
  await timed("catalogs", () => Impact.prepare()).catch((err) => console.error("catalog preparation", err));
  await timed("first-screen", route);
  // the student list's prefabs, the slowest to export after a game update: asked for once the first screen is up
  // (earlier they slow its own exports), never awaited, and taken up by StudentList.show()
  StudentList.preload();
}

boot().catch((err) => {
  console.error(err);
  globalThis.BAMTRecovery?.fail(err);
});
