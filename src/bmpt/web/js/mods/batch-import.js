// The batch import page over runtime Items, Mailbox and FilterSort prefab widgets (import-layout.js): its state, the
// toolbar, the row under the list, the progress under Now Loading, the error reports and the default page. The list is
// in import-list.js, the controls in import-controls.js, the dialogs in import-dialogs.js; each takes the page's state
// object P, created per page.
import { current as modsState } from "./mod-switch.js";
import { overwrittenLines } from "../app/repairs.js";
import { progressName } from "../core/job-progress.js";
import * as NGUI from "../ui/ngui.js";
import * as Popup from "../ui/popup.js";
import * as Loading from "../app/loading.js";
import { rpc, waitJob } from "../core/api.js";
import { L, T } from "../core/i18n.js";
import { withCode } from "../app/error-codes.js";
import { customAliases, studentName } from "../core/names.js";
import { spineAllowed } from "../spine/spine-runtime.js";
import { thumbnailScope } from "../detail/thumbs.js";
import { chibiThumbnail } from "../chibi/chibi-still.js";
import { S } from "../core/util.js";
import { XS_BUTTON, SPACE, CONTROL } from "../ui/ui-tokens.js";
import { row as layoutRow } from "../ui/ui-grid.js";
import { ROW_W as OPTION_WIDTH } from "../settings/settings-rows.js";
import { BUTTON_BODY_H, loadImportLayouts, importArtwork } from "./import-layout.js";
import { suggestImports } from "./import-assistants.js";
import {
  createImportState,
  countsOf,
  eligible,
  STATES,
  createMissingFlow,
  missingText,
  groupsText,
  NOTE_LEFT,
  noteRoom,
} from "./import-state.js";
import { characterPicker } from "../student-list/character-picker.js";
import { scrollbar as gameScrollbar } from "../ui/scrollbar.js";
import { createImportControls, el, place } from "./import-controls.js";
import { createImportList, KIND_KEYS, LIST_BOTTOM, LIST_TOP, LIST_W, LIST_X, REASONS } from "./import-list.js";
import { createImportDialogs } from "./import-dialogs.js";

export { DIALOG_AREA, DIALOG_H } from "./import-dialogs.js";

const baseName = (path) => path?.split(/[\\/]/).pop() || "";

/**
 * The student list's alts as a flat list, each with its student.
 * @param {{students?: object[]}} data The data of students.list.
 */
export function catalogEntries(data) {
  return (data?.students || []).flatMap((student) => (student.alts || []).map((alt) => ({ ...alt, student })));
}
/**
 * Who an import entry is for: the catalog alt its alt key or its core names, the student's display
 * name, the core and the search words.
 * @param {object} entry The import entry.
 * @param {object[]} catalog The alts (catalogEntries).
 */
export function entryIdentity(entry, catalog) {
  const alt =
    catalog.find((item) => item.key === entry.alt) ||
    catalog.find(
      (item) => entry.core && [item.fileId, item.portrait, item.memorialLobby, item.chibi].includes(entry.core),
    );
  const names = L(alt?.names);
  const name = studentName(alt, names?.full || names?.short || entry.core || baseName(entry.paths?.[0]) || entry.id);
  return {
    alt,
    name,
    core: entry.core || alt?.fileId || entry.alt || baseName(entry.paths?.[0]),
    search: [
      name,
      entry.core,
      entry.alt,
      ...(entry.paths || []),
      ...customAliases(alt),
      ...Object.values(alt?.names || {}).flatMap((value) =>
        typeof value === "string" ? [value] : Object.values(value),
      ),
    ]
      .filter(Boolean)
      .join(" ")
      .toLocaleLowerCase(),
  };
}

/**
 * Make the batch import page: the file list, the options, the progress and the result popups. Every other
 * option replaces a service, for the tests.
 * @param {object} [options]
 * @param {HTMLElement} options.host The element the page is drawn into.
 * @returns {object} The page's controls: show, hide, drop, invalidate and relabel.
 */
export function createImportPage({
  host,
  call = rpc,
  jobs = waitJob,
  ui = NGUI,
  popups = Popup,
  thumbnails = thumbnailScope,
  chibiThumbnails = chibiThumbnail,
  allowed = spineAllowed,
  suggest = suggestImports,
  loading = Loading,
  bars = gameScrollbar,
} = {}) {
  // Everything the page changes, in one object the list, controls and dialogs take too.
  const P = {
    context: null,
    catalog: [],
    visible: false,
    root: null,
    scope: null,
    query: "",
    filter: "all",
    ascending: true,
    searchOpen: false,
    rowsKey: null,
    previewKey: null,
    generation: 0,
    rows: new Map(),
    identities: new WeakMap(),
    dom: {},
    dialogClose: null,
    countsKey: null,
    art: null,
    merged: false,
    shownError: null,
    listTop: LIST_TOP,
    thumbnailJobs: new Map(),
    liveVersions: new Map(),
    refreshedResults: new WeakSet(),
    listGeneration: 0,
    liveRefreshPending: false,
    drawings: new Set(),
    loadingHold: null,
    left: Promise.resolve(),
    leave: () => {},
    waiters: new Set(),
    scrollBars: new Map(),
  };
  const importState = createImportState({
    rpc: call,
    waitJob: jobs,
    suggest,
    changed: () => P.context?.invalidateStudentList?.(),
  });
  const showError = (message, error) =>
    Popup.isRefusal(error)
      ? popups.message({ title: T("quit.title"), lines: [message], ok: T("picker.ok") })
      : popups.message({ title: T("job.failed"), lines: [withCode(message, error, T)], ok: T("picker.ok"), error });
  const wake = () => {
    for (const resolve of P.waiters) resolve();
    P.waiters.clear();
  };
  const waiting = () =>
    importState.state.phase !== "picking" &&
    (P.drawings.size || ["analyzing", "running", "saving"].includes(importState.state.phase));
  function importProgress() {
    const { phase, progress: p, reconnecting } = importState.state;
    if (phase === "running") {
      const done = p?.completed ?? p?.done ?? 0,
        total = p?.total || importState.state.taskEntries.length;
      if (reconnecting)
        return T("import.completedProgress", {
          done,
          total,
          remaining: p?.remaining ?? Math.max(0, total - done),
          action: T("import.reconnecting"),
          name: "",
          step: "",
        }).trim();
      const current = importState.state.taskEntries.find((entry) => entry.id === p?.current);
      const action = ["update", "convert", "add", "apply"].includes(p?.phase)
        ? `import.phase.${p.phase}`
        : "job.running";
      const identity = current && identified(current);
      const phase = T(action)
        .replace(/\{name\}/g, "")
        .replace(/[:：]\s*$/u, "")
        .trim();
      const step = p?.step || "import.step.check",
        detail = T(step);
      const repeated = { "import.phase.add": "import.step.add", "import.phase.apply": "import.step.apply" };
      return T("import.completedProgress", {
        done,
        total,
        remaining: p?.remaining ?? Math.max(0, total - done),
        action: phase,
        // who and the kind as a job over mods names them (job-progress.js modLine): "Hoshino (Swimsuit) - Chibi"
        name: current
          ? `${progressName(identity.alt, "", identity.name)}${T("progress.sep")}${T(KIND_KEYS[current.resourceType] || KIND_KEYS.other)}`
          : "",
        step: detail && detail !== phase && repeated[action] !== step ? T("progress.step", { step: detail }) : "",
      });
    }
    const label = T(
      reconnecting
        ? "import.reconnecting"
        : ["analyzing", "saving"].includes(phase)
          ? `import.${phase}`
          : "app.loading",
    );
    const done = p?.completed ?? p?.done ?? (Number.isFinite(p?.current) ? p.current : 0);
    return p?.total > 0 ? T("import.waitProgress", { label, done, total: p.total }) : label;
  }
  function holdArtwork() {
    if (!P.visible || P.loadingHold || !waiting()) return;
    const token = P.generation;
    const work = loading.hold(
      async () => {
        while (P.visible && token === P.generation && waiting()) {
          await new Promise((resolve) => P.waiters.add(resolve));
        }
      },
      { until: P.left, all: true, progress: importProgress },
    );
    P.loadingHold = work;
    work
      .catch((error) => console.error("Import loading", error))
      .finally(() => {
        if (P.loadingHold === work) {
          P.loadingHold = null;
          holdArtwork();
        }
      });
  }
  const report = (error, entry = null) => {
    const code = typeof error === "string" ? error : error?.code || error?.reason;
    if (code === "official_unavailable") {
      const state = modsState() || {};
      return [
        T("mods.overwrittenTitle"),
        ...overwrittenLines(
          {
            installs: (state.installs || []).filter((i) => i.key === state.current),
          },
          T,
        ),
      ].join("\n");
    }
    if (entry && REASONS.has(code)) return T(`import.reason.${code}`, error?.params || {});
    const key = `error.${code}`,
      text = T(key, { file: entry?.paths?.[0] || "", ...(error?.params || {}) });
    return code && code !== "internal" && text !== key ? text : T("import.failed");
  };
  // A failed row's note: the row's short reason, then the localized error text with its parameters when the code has
  // one that says more. The code and parameters stay on the result (and, for a blocked row, on its list entry) for the
  // error report.
  const resultNote = (item, entry) => {
    const detail = item.error || { code: item.reason, params: item.params || {} };
    const label = report(item, entry);
    if (item.reason === "official_unavailable") return label;
    const key = `error.${detail.code}`,
      text = detail.code ? T(key, { file: entry?.paths?.[0] || "", ...(detail.params || {}) }) : key;
    return text !== key && text !== label ? `${label} - ${text}` : label;
  };
  const tracked = (work) => {
    P.drawings.add(work);
    holdArtwork();
    work
      .catch((error) => console.error("Import artwork", error))
      .finally(() => {
        P.drawings.delete(work);
        wake();
      });
    return work;
  };
  const controls = createImportControls(P, { ui, bars, tracked });
  const { paint, textWidget, button, setCaption, checkbox, toggle, inputControl, radioOptions, syncBar } = controls;
  // The row under the list is one row of buttonXS controls (CONTROL.buttonXS: 104 high, caption 40 down to
  // XS_BUTTON.fsMin), SPACE.m apart, drawn from the prefabs' 162-high bodies. Import stands out by its gold, not
  // by its size. Its right edge is the list tools'. The 902 is the row's centre line in the stage (px; the App's
  // own: the row clears the bottom nav's 104 px area).
  const FOOTER_ROW = {
    scale: (CONTROL.buttonXS.h * S) / BUTTON_BODY_H,
    y: 902,
    right: 1848,
    wide: 330,
    width: XS_BUTTON.w * S,
    gap: SPACE.m * S,
    size: {
      max: (CONTROL.buttonXS.fs * BUTTON_BODY_H) / CONTROL.buttonXS.h,
      min: (XS_BUTTON.fsMin * BUTTON_BODY_H) / CONTROL.buttonXS.h,
    },
  };
  // The list's tools (the Items prefab's Btn_Receive bodies at TOOL_SCALE) fill the list's width, SPACE.m apart,
  // each in the share of it its caption needs in the longest language: [key, caption key, share].
  const TOOL_SCALE = (0.34 * CONTROL.buttonXS.h) / CONTROL.buttonS.h;
  const TOOLBAR = [
    ["files", "addFiles", 174],
    ["folder", "addFolder", 190],
    ["all", "all", 126],
    ["none", "none", 140],
    ["remove", "remove", 150],
    ["removeImported", "removeImported", 224],
    ["clear", "clear", 156],
  ];
  // the tools, in px of the panel: a row under the list, level with it, as high as the body
  const toolRow = () =>
    layoutRow(
      TOOLBAR.map(([, , share]) => ({ grow: share, h: BUTTON_BODY_H * TOOL_SCALE })),
      { x: LIST_X, y: LIST_BOTTOM + SPACE.m * S, w: LIST_W, gap: SPACE.m * S },
    );
  // Buttons side by side share one caption size: the list's seven tools, and the row under the list.
  function shareSizes() {
    for (const [keys, range] of [
      [TOOLBAR.map(([key]) => key)],
      [["fillAll", "options", "recheck", "start"], FOOTER_ROW.size],
    ]) {
      const buttons = keys.map((key) => P.dom[key]).filter((b) => b && !b.hidden);
      const size =
        buttons.length > 1 && P.art
          ? P.art.buttonSize(
              buttons.map((b) => b.caption),
              buttons.map((b) => b.buttonWidth),
              range,
            )
          : null;
      for (const b of buttons) {
        b.size = size;
        b.refresh();
      }
    }
  }
  const identified = (entry) => {
    if (!P.identities.has(entry)) P.identities.set(entry, entryIdentity(entry, P.catalog));
    return P.identities.get(entry);
  };
  const list = createImportList(P, {
    importState,
    ui,
    popups,
    allowed,
    chibiThumbnails,
    controls,
    identified,
    report,
    resultNote,
    tracked,
    showError,
    openSource: (entry) => dialogs.openSource(entry),
    openCharacter,
  });
  const { layoutList, renderRows, renderPreview, prepareVisibleRows, refreshCurrentPreviews, refreshAppliedPreviews } =
    list;
  const dialogs = createImportDialogs(P, { importState, ui, popups, controls, list, identified, report });
  const { dialog, openOptions, results } = dialogs;

  function build() {
    P.root = el("div", "import-page");
    P.root.dataset.phase = importState.state.phase;
    P.dom.background = el("div", "import-background");
    P.root.appendChild(P.dom.background);
    paint(P.dom.background, P.art.background(), { origin: [960, 540] });
    P.dom.preview = place(el("div", "import-detail"), 46, 132, 524, 707);
    P.root.appendChild(P.dom.preview);
    // Items/ListBG: the two-column frame retains its paper/inbox insets, with a mail list in place of the item grid.
    const panel = place(el("div", "import-panel"), 602, 132, 1272, 704);
    P.dom.panel = panel;
    paint(panel, P.art.panel(1272 / S, 704 / S), { origin: [636, 352] });
    P.dom.header = place(el("div", "import-header"), 0, 0, 1272, 136 * S);
    panel.append(P.dom.header);
    const search = inputControl(T("import.search"), P.query, { width: LIST_W, search: true });
    P.dom.search = search.input;
    P.dom.search.placeholder = T("import.search");
    P.dom.search.enterKeyHint = "search";
    P.dom.searchWrap = place(search.wrap, LIST_X, LIST_TOP, LIST_W, 112 * S);
    const runSearch = () => {
      P.query = P.dom.search.value.toLocaleLowerCase();
      renderRows();
    };
    P.dom.search.addEventListener("input", (event) => {
      if (!event.isComposing) runSearch();
    });
    P.dom.search.addEventListener("compositionend", runSearch);
    search.clear.addEventListener("click", () => {
      P.dom.search.value = "";
      runSearch();
      P.dom.search.focus();
    });
    P.dom.search.addEventListener("keydown", (event) => {
      if (!event.isComposing && event.keyCode !== 229 && event.key === "Enter") P.dom.search.blur();
    });
    for (const key of ["searchToggle", "filter", "order"]) {
      P.dom[key] = el("button", "hit");
      P.dom[key].type = "button";
      P.dom[key].dataset.action = key;
      P.dom.header.appendChild(P.dom[key]);
    }
    P.dom.searchToggle.setAttribute("aria-label", T("import.search"));
    panel.appendChild(search.wrap);
    P.dom.merge = toggle(T("import.merge"), P.merged, (checked) => {
      P.merged = checked;
      P.rowsKey = null;
      renderRows();
    });
    P.dom.merge.label.dataset.action = "merge";
    panel.appendChild(P.dom.merge.label);
    P.dom.list = el("div", "import-list");
    P.dom.list.setAttribute("aria-label", T("import.list"));
    panel.appendChild(P.dom.list);
    P.dom.list.addEventListener("scroll", () => prepareVisibleRows(P.dom.list, P.rows.values()));
    layoutList();
    toolRow().boxes.forEach((box, i) => {
      const [key, caption] = TOOLBAR[i];
      P.dom[key] = button(T(`import.${caption}`), key, { width: box.w, scale: TOOL_SCALE });
      place(P.dom[key], box.x, box.y, box.w, box.h);
      panel.appendChild(P.dom[key]);
    });
    P.root.appendChild(panel);
    const { scale: rowScale, wide, width: rowWidth, gap: rowGap } = FOOTER_ROW,
      rowHeight = BUTTON_BODY_H * rowScale;
    P.dom.options = button(T("import.options.title"), "options", { width: wide, scale: rowScale });
    P.dom.recheck = button(T("import.recheck"), "recheck", { width: rowWidth, scale: rowScale });
    P.dom.start = button(T("import.action", { n: 0 }), "start", { width: rowWidth, scale: rowScale, primary: true });
    shareSizes();
    P.dom.fillAll = button(missingText("fillAll", { n: 1 }), "fillAll", { width: wide, scale: rowScale });
    // As on the student-list/detail screens, reserve the nav's entire 104 px area, with 24 px above it. One row,
    // right-aligned: the count's room ends before Fill All (hidden until a row can be filled), which stands left of
    // Advanced Options.
    const bar = layoutRow(
      [wide, wide, rowWidth, rowWidth].map((w) => ({ w, h: rowHeight })),
      { x: 0, y: FOOTER_ROW.y - rowHeight / 2, w: FOOTER_ROW.right, gap: rowGap, justify: "end" },
    );
    for (const [part, box] of [
      [P.dom.fillAll, bar.boxes[0]],
      [P.dom.options, bar.boxes[1]],
      [P.dom.recheck, bar.boxes[2]],
      [P.dom.start, bar.boxes[3]],
    ]) {
      place(part, box.x, box.y, box.w, box.h);
    }
    P.dom.fillAll.hidden = true;
    P.root.append(P.dom.options, P.dom.recheck, P.dom.start);
    const footer = { left: bar.boxes[1].x, gap: rowGap, wide };
    P.dom.missingNote = place(el("div"), NOTE_LEFT, bar.boxes[0].y, noteRoom(footer), rowHeight);
    P.dom.missingNoteRoom = noteRoom(footer);
    P.dom.missingNoteHeight = rowHeight;
    P.root.append(P.dom.fillAll, P.dom.missingNote);
    P.root.addEventListener("click", (event) => {
      const target = event.target.closest?.("[data-action]");
      if (!target || target.disabled) return;
      const actions = {
        files: () => importState.pick(false),
        folder: pickFolder,
        filter: openFilter,
        searchToggle: () => {
          P.searchOpen = !P.searchOpen;
          layoutList();
          renderToolbar();
          if (P.searchOpen) P.dom.search.focus();
          else P.dom.search.blur();
        },
        order: () => {
          P.ascending = !P.ascending;
          renderToolbar();
          renderRows();
        },
        all: () => importState.checkAll(true),
        none: () => importState.checkAll(false),
        remove: () => importState.remove(),
        removeImported: () => importState.removeImported(),
        clear: () => importState.clear(),
        options: openOptions,
        fillAll: () => missingFlow.fillAll(),
        recheck: () => importState.recheck(),
        start: () => (importState.state.phase === "done" ? results() : start()),
      };
      Promise.resolve(actions[target.dataset.action]?.()).catch((error) => {
        showError(report(error), error);
      });
    });
    P.root.addEventListener("dragover", (event) => {
      event.preventDefault();
      if (event.dataTransfer) event.dataTransfer.dropEffect = importState.busy() ? "none" : "copy";
    });
    P.root.addEventListener("drop", (event) => {
      event.preventDefault();
      const paths = [...(event.dataTransfer?.files || [])].map((file) => file.path).filter(Boolean);
      if (paths.length) importState.add(paths);
      // Browser File objects do not expose native paths; the native window drop event handles them in the app.
      else if (!P.context?.info?.native) showError(T("error.native_unavailable"), { code: "native_unavailable" });
    });
    host.replaceChildren(P.root);
    P.rowsKey = P.previewKey = P.countsKey = null;
  }

  function renderToolbar() {
    const options = filterOptions(),
      caption = options.find(([value]) => value === P.filter)?.[1] || options[0][1];
    const key = JSON.stringify([T("import.list"), caption, P.filter, P.ascending, P.searchOpen]);
    if (P.countsKey === key) return;
    P.countsKey = key;
    P.dom.filter.setAttribute("aria-label", caption);
    P.dom.order.setAttribute("aria-label", T(P.ascending ? "studentList.asc" : "studentList.desc"));
    paint(
      P.dom.header,
      P.art.header(1272 / S, T("import.list"), caption, P.filter !== "all", P.ascending, P.searchOpen),
      {
        onRender: (view) => {
          for (const [key, path] of [
            ["searchToggle", `SearchBtn/${P.searchOpen ? "Btn_Pressed" : "Btn_Normal"}`],
            ["filter", "SortBtn/Btn_Normal"],
            ["order", "OrderBtn/Btn_Normal"],
          ]) {
            place(P.dom[key], ...Popup.nodeRect(view, `BG_Top_Widget/${path}`));
          }
          // as on the student list's top menu: the toggle follows the title, ending a gap before the first tool
          const [x, y, , h] = Popup.nodeRect(
              view,
              `BG_Top_Widget/SearchBtn/${P.searchOpen ? "Btn_Pressed" : "Btn_Normal"}`,
            ),
            style = P.dom.merge.label.style;
          Object.assign(style, {
            left: `${x - SPACE.l * S - parseFloat(style.width)}px`,
            top: `${y + (h - parseFloat(style.height)) / 2}px`,
          });
        },
      },
    );
  }
  const filterOptions = () => {
    const counts = countsOf(importState.state.entries);
    return [
      ["all", T("import.filterCount", { state: T("seg.all"), n: importState.state.entries.length })],
      ...STATES.map((state) => [
        state,
        T("import.filterCount", {
          state: state === "incomplete" ? missingText("state") : T(`import.state.${state}`),
          n: counts[state],
        }),
      ]),
    ];
  };
  async function openFilter() {
    let draft = P.filter;
    await dialog(
      T("import.filter"),
      {
        action: T("picker.ok"),
        cancel: T("popup.cancel"),
        width: 1800,
        bodyWidth: OPTION_WIDTH * S,
        onAction: () => {
          P.filter = draft;
          P.countsKey = null;
          render();
          return true;
        },
      },
      (area) =>
        radioOptions(
          area,
          filterOptions(),
          draft,
          (value) => {
            draft = value;
          },
          { width: OPTION_WIDTH * S },
        ),
    );
  }

  async function openCharacter(entry) {
    if (importState.busy()) return;
    const data = await call("import.catalog");
    // Installed entries supply the same aliases, favourites and Mod counts as the main student list.
    const current = new Map(P.catalog.map((alt) => [alt.key, alt]));
    for (const student of data.students)
      student.alts = student.alts.map((alt) => ({ ...alt, ...current.get(alt.key) }));
    P.catalog = catalogEntries(data);
    P.identities = new WeakMap();
    await characterPicker({
      data,
      selected: entry.alt,
      dialog,
      el,
      button,
      inputControl,
      checkbox,
      radioOptions,
      emptyNote: (host, caption) => textWidget(host, caption, "empty", { scale: 0.5, origin: [685, 50] }),
      refresh: syncBar,
      onSelect: (alt) => importState.assignAlt(entry.id, alt),
    });
  }

  function render() {
    if (!P.visible || !P.root) return;
    const state = importState.state,
      busy = importState.busy();
    P.root.dataset.phase = state.phase;
    P.root.setAttribute("aria-busy", String(busy));
    renderToolbar();
    P.dom.merge.input.disabled = busy;
    P.dom.merge.refresh();
    for (const key of ["files", "folder", "options", "clear", "remove", "removeImported", "all", "none", "recheck"])
      P.dom[key].disabled = busy;
    P.dom.removeImported.disabled ||= !state.entries.some((entry) =>
      ["done", "library", "installed"].includes(entry.state),
    );
    P.dom.clear.disabled ||= !state.entries.length;
    P.dom.remove.disabled ||= !state.selected;
    P.dom.recheck.disabled ||= !state.entries.length && !state.error;
    P.dom.all.disabled ||= !state.valid || state.phase === "done" || !state.entries.some(eligible);
    P.dom.none.disabled ||= !state.valid || state.phase === "done" || !state.checked.size;
    P.dom.start.disabled = busy || (state.phase !== "done" && (!state.valid || !importState.selected().length));
    setCaption(
      P.dom.start,
      state.phase === "done" ? T("import.results") : T("import.action", { n: importState.selected().length }),
    );
    for (const key of ["files", "folder", "options", "clear", "remove", "removeImported", "all", "none", "recheck"])
      P.dom[key].refresh();
    if (state.error && state.error !== P.shownError) showError(report(state.error), state.error);
    P.shownError = state.error;
    renderRows();
    renderPreview();
    const fillable = missingFlow.fillable().length,
      incomplete = state.entries.filter((entry) => entry.state === "incomplete").length;
    P.dom.fillAll.hidden = !fillable;
    P.dom.fillAll.disabled = busy;
    setCaption(P.dom.fillAll, missingText("fillAll", { n: fillable }));
    shareSizes();
    const note = incomplete ? groupsText(incomplete) : "";
    if (P.dom.missingNote.caption !== note) {
      P.dom.missingNote.caption = note;
      if (!note) P.dom.missingNote.replaceChildren();
      else {
        const tree = P.art.note(note),
          label = tree.widgets.find((w) => "text" in w),
          room = P.dom.missingNoteRoom / S;
        const fit = ui.fitText(note, label.fontSize, label.fontSize * 0.7, room, 2);
        Object.assign(label, {
          text: fit.text,
          fontSize: fit.fs,
          w_: room,
          h_: Math.max(label.h_, fit.fs * 1.3 * String(fit.text).split("\n").length),
          pivot: 3,
        });
        paint(P.dom.missingNote, tree, { scale: S, origin: [0, P.dom.missingNoteHeight / 2] });
      }
    }
  }

  function relabel() {
    P.identities = new WeakMap();
    P.rowsKey = P.previewKey = P.countsKey = null;
    for (const row of P.rows.values()) row.signature = null;
    if (!P.root) return;
    P.dom.search.placeholder = T("import.search");
    P.dom.search.setAttribute("aria-label", T("import.search"));
    P.dom.searchToggle.setAttribute("aria-label", T("import.search"));
    P.dom.searchWrap.querySelector("button")?.setAttribute("aria-label", T("display.reset"));
    P.dom.list.setAttribute("aria-label", T("import.list"));
    P.dom.merge.relabel(T("import.merge"));
    for (const [key, label] of [
      ["files", "addFiles"],
      ["folder", "addFolder"],
      ["all", "all"],
      ["none", "none"],
      ["remove", "remove"],
      ["removeImported", "removeImported"],
      ["clear", "clear"],
      ["options", "options.title"],
      ["recheck", "recheck"],
    ])
      setCaption(P.dom[key], T(`import.${label}`));
    shareSizes();
    render();
  }

  const start = () => importState.start();
  // Popup.message adds UIPopup_GachaCharList/Btn_Checkbox to the same Sort frame for recursive scanning.
  async function pickFolder() {
    const result = await popups.message({
      title: T("import.addFolder"),
      lines: [],
      check: { label: T("import.recursive"), checked: false },
      ok: T("picker.ok"),
      cancel: T("popup.cancel"),
    });
    if (P.visible && result.button === "ok") await importState.pick(true, result.checked);
  }

  async function reveal(id) {
    const entry = importState.state.entries.find((entry) => entry.id === id);
    if (entry && ((P.filter !== "all" && entry.state !== P.filter) || !identified(entry).search.includes(P.query))) {
      P.filter = "all";
      P.query = "";
      P.dom.search.value = "";
      P.countsKey = null;
      renderToolbar();
      renderRows();
    }
    // Row replacement itself is tracked; wait for it as well as the individual artwork jobs.
    while (P.drawings.size) await Promise.all([...P.drawings]);
    [...P.rows.values()]
      .find((record) => record.group.entries.some((entry) => entry.id === id))
      ?.row.scrollIntoView?.({ block: "nearest" });
  }
  const missingFlow = createMissingFlow({
    importState,
    identify: (entry) => identified(entry).name,
    kindName: (kind) => T(KIND_KEYS[kind]),
    title: () => T("nav.import"),
    reveal,
    active: () => P.visible,
    failed: (error) => showError(report(error), error),
    ask: async ({ title, lines, ok }) =>
      (
        await popups.message({
          title,
          lines,
          plain: true,
          fitTogether: lines.map((_, i) => i),
          ok,
          cancel: T("popup.cancel"),
        })
      ).button === "ok",
  });
  importState.subscribe(() => {
    if (P.liveRefreshPending && !importState.busy()) {
      P.liveRefreshPending = false;
      refreshCurrentPreviews();
    }
    refreshAppliedPreviews();
    render();
    wake();
    holdArtwork();
    loading.refresh?.();
    missingFlow.onChange(importState.state);
  });
  return {
    importState,
    async show(ctx, data) {
      P.leave();
      wake();
      P.left = new Promise((resolve) => {
        P.leave = resolve;
      });
      P.context = ctx;
      P.catalog = catalogEntries(data);
      P.identities = new WeakMap();
      P.visible = true;
      const token = ++P.generation;
      P.scope?.cancel();
      P.scope = thumbnails();
      P.thumbnailJobs.clear();
      if (!P.art) {
        await ui.init();
        P.art = importArtwork({ ...ui, translate: T }, await loadImportLayouts(ui));
      }
      if (!P.visible || token !== P.generation) return;
      if (!P.root) build();
      for (const row of P.rows.values()) row.signature = null;
      P.rowsKey = P.previewKey = null;
      render();
      // The page is usable while these read; a failed options read can be retried from Advanced Options or Import.
      importState.loadOptions();
      await Promise.all([...P.drawings]);
      await ui.decoded(P.root, 15000);
    },
    hide() {
      P.visible = false;
      P.generation++;
      P.leave();
      wake();
      P.scope?.cancel();
      P.dialogClose?.();
      importState.leave();
    },
    invalidate() {
      if (importState.state.phase !== "running") P.liveRefreshPending = true;
      importState.invalidate();
    },
    drop(paths) {
      return importState.add(paths);
    },
    openOptions,
    results,
    relabel,
    async settled() {
      await Promise.all([...P.drawings]);
    },
  };
}

let page;
/**
 * Show the batch import page.
 * @param {object} context The shared screen context.
 * @param {object} data The data of students.list.
 */
export function show(context, data) {
  page ||= createImportPage({ host: document.getElementById("import") });
  return page.show(context, data);
}
/**
 * Hide the batch import page.
 */
export function hide() {
  page?.hide();
}
/**
 * Take files dropped on the window into the page.
 * @param {string[]} paths The dropped paths.
 */
export function drop(paths) {
  return page?.drop(paths);
}
/**
 * Mark what the page shows as stale: the next show reads the catalog again.
 */
export function invalidate() {
  page?.invalidate();
}
/**
 * Draw the page again after a locale change.
 */
export function relabel() {
  return page?.relabel();
}
