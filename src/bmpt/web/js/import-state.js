import { T } from "./i18n.js";
// Batch import's RPC contract and session state, independent of the screen and its artwork.
export const STATES = Object.freeze(["ready", "convert", "update", "library", "installed", "blocked", "incomplete", "done"]);
export const RESOURCE_TYPES = Object.freeze(["ALL", "Texture2D", "TextAsset", "Mesh", "AnimationClip"]);
export const eligible = (entry) => ["ready", "convert", "update"].includes(entry?.state);
export const pathsOf = (entries) => [...new Set(entries.flatMap((entry) => entry.paths || []))];
export function sourceFolder(path) {
  const end = Math.max(path.lastIndexOf("/"), path.lastIndexOf("\\"));
  return end < 0 ? "" : path.slice(0, end + (end === 0 || end === 2 && path[1] === ":" ? 1 : 0));
}
const snapshot = (options) => ({ ...options, types: [...options.types] });

export function countsOf(entries) {
  const counts = Object.fromEntries(STATES.map((state) => [state, 0]));
  for (const entry of entries) if (entry.state in counts) counts[entry.state]++;
  return counts;
}

export function createImportState({ rpc, waitJob, changed = () => {}, suggest = async () => [] }) {
  const state = { entries: [], checked: new Set(), selected: null, phase: "idle", progress: null, error: null,
    options: null, results: [], taskEntries: [], taskOptions: null, suggestions: [], valid: false, analysisRevision: null,
    jobId: null, reconnecting: false, analysisCause: null };
  const listeners = new Set();
  const pendingAnalyses = new Set();
  let revision = 0, contextRevision = 0, retryPaths = [], retryRecursive = false, starting = false, optionsRead = null, waitGeneration = 0;
  const emit = () => { for (const fn of listeners) fn(state); };
  const busy = () => starting || ["picking", "analyzing", "running", "saving"].includes(state.phase);
  const selected = () => state.entries.filter((entry) => eligible(entry) && state.checked.has(entry.id));
  const fail = (error) => { state.error = error; state.phase = "error"; emit(); };
  const progress = (data) => { state.progress = { ...data }; emit(); };
  const resetResults = () => { state.results = []; state.taskEntries = []; state.taskOptions = null; state.progress = null; };
  globalThis.BAMTRecovery?.importList?.(() => ({ phase: state.phase, revision: state.analysisRevision,
    rows: state.entries.map((entry) => ({ ...entry, selected: eligible(entry) && state.checked.has(entry.id) })) }));

  async function waitForJob(id) {
    const generation = ++waitGeneration;
    state.jobId = id; state.reconnecting = false;
    const active = () => generation === waitGeneration && state.jobId === id;
    try {
      return await waitJob(id, (data) => { if (active()) progress(data); }, { onConnection: (connected) => {
        if (!active() || state.reconnecting === !connected) return;
        state.reconnecting = !connected; emit();
      } });
    } finally {
      if (active()) { state.jobId = null; state.reconnecting = false; }
    }
  }

  async function loadOptions() {
    if (state.options) return true;
    optionsRead ||= Promise.resolve().then(() => rpc("import.options")).then((options) => {
      state.options = snapshot(options); emit(); return true;
    }).catch((error) => { if (!busy() || starting) fail(error); return false; }).finally(() => { optionsRead = null; });
    return optionsRead;
  }

  async function analyze(paths, recursive = false, cause = "add") {
    if (busy() || !paths.length) return false;
    retryPaths = [...new Set(paths)]; retryRecursive = recursive;
    const before = new Map(state.entries.map((entry) => [entry.id, entry]));
    const checked = new Set(state.checked);
    const contextAtStart = contextRevision;
    const removed = new Set();
    pendingAnalyses.add(removed);
    state.phase = "analyzing"; state.error = null; state.valid = false; state.analysisCause = cause;
    resetResults(); revision++; emit();
    try {
      const id = await rpc("import.analyze", { paths: retryPaths, recursive });
      const result = await waitForJob(id);
      state.analysisRevision = result.revision;
      state.entries = [...new Map(result.entries.filter((entry) => !removed.has(entry.id)).map((entry) => [entry.id, entry])).values()];
      state.checked = new Set(state.entries.filter((entry) => eligible(entry)
        && (!eligible(before.get(entry.id)) || checked.has(entry.id))).map((entry) => entry.id));
      if (!state.entries.some((entry) => entry.id === state.selected)) state.selected = state.entries[0]?.id ?? null;
      retryPaths = pathsOf(state.entries); retryRecursive = false;
      state.valid = contextAtStart === contextRevision; state.phase = state.valid ? "idle" : "error";
      state.error = state.valid ? null : { code: "entry_changed" }; state.progress = null; revision++; emit();
      const at = revision;
      // Providers receive copies and can only suggest; authoritative matches remain the analyzer's.
      Promise.resolve(suggest({ entries: structuredClone(state.entries), selection: [...state.checked] }))
        .then((suggestions) => { if (revision === at) state.suggestions = suggestions; }).catch(() => {});
      return true;
    } catch (error) { fail(error); return false; }
    finally { pendingAnalyses.delete(removed); }
  }

  async function add(paths, recursive = false) {
    if (busy()) return false;
    const known = pathsOf(state.entries), fresh = paths.filter((path) => typeof path === "string" && path && !known.includes(path));
    if (!fresh.length) return false;
    return analyze([...known, ...fresh], recursive);
  }

  return {
    state, busy, selected, loadOptions,
    invalidate() {
      if (state.phase === "running") return;
      contextRevision++; revision++; state.valid = false;
      if (state.entries.length && !busy() && state.phase !== "done") { state.phase = "error"; state.error = { code: "entry_changed" }; }
      emit();
    },
    subscribe(fn) { listeners.add(fn); return () => listeners.delete(fn); },
    add,
    async pick(folder, recursive = false, directory = null) {
      if (busy()) return false;
      const previous = state.phase;
      state.phase = "picking"; state.error = null; emit();
      try {
        const { paths } = await rpc("import.pick", { folder, recursive: folder && recursive, ...(directory ? { directory } : {}) });
        state.phase = previous; emit();
        return paths.length ? add(paths, folder && recursive) : false;
      } catch (error) { fail(error); return false; }
    },
    recheck() { return analyze(retryPaths.length ? retryPaths : pathsOf(state.entries), retryRecursive, "recheck"); },
    async assignCharacter(id, variant) {
      if (busy() || !state.entries.some((entry) => entry.id === id)) return false;
      const contextAtStart = contextRevision;
      state.phase = "analyzing"; state.error = null; state.analysisCause = "assignment"; resetResults(); revision++; emit();
      try {
        const job = await rpc("import.assignCharacter", { id, variant });
        const entry = await waitForJob(job);
        state.analysisRevision = entry.revision;
        state.entries = state.entries.map((item) => item.id === id ? entry : item);
        if (eligible(entry)) state.checked.add(id); else state.checked.delete(id);
        state.valid = contextAtStart === contextRevision; state.phase = state.valid ? "idle" : "error";
        state.error = state.valid ? null : { code: "entry_changed" };
        state.progress = null; revision++; emit(); return state.valid;
      } catch (error) { state.valid = false; fail(error); return false; }
    },
    async setSource(id, server, platform) {
      if (busy() || !state.entries.some((entry) => entry.id === id)) return false;
      const contextAtStart = contextRevision;
      state.phase = "analyzing"; state.error = null; state.analysisCause = "source"; resetResults(); revision++; emit();
      try {
        const job = await rpc("import.setSource", { id, server, platform });
        const entry = await waitForJob(job);
        state.analysisRevision = entry.revision;
        state.entries = state.entries.map((item) => item.id === id ? entry : item);
        if (eligible(entry)) state.checked.add(id); else state.checked.delete(id);
        state.valid = contextAtStart === contextRevision;
        state.phase = state.valid ? "idle" : "error";
        state.error = state.valid ? null : { code: "entry_changed" };
        state.progress = null; revision++; emit(); return state.valid;
      } catch (error) { state.valid = false; fail(error); return false; }
    },
    toggle(id, checked) {
      if (busy() || state.phase === "done" || !state.valid || !eligible(state.entries.find((entry) => entry.id === id))) return;
      if (checked) state.checked.add(id); else state.checked.delete(id);
      state.selected = id;
      revision++; emit();
    },
    toggleGroup(ids, checked) {
      if (busy() || state.phase === "done" || !state.valid) return;
      for (const entry of state.entries) if (ids.includes(entry.id) && eligible(entry)) {
        if (checked) state.checked.add(entry.id); else state.checked.delete(entry.id);
      }
      state.selected = state.entries.find((entry) => ids.includes(entry.id) && eligible(entry))?.id || state.selected;
      revision++; emit();
    },
    select(id) {
      if (!state.entries.some((entry) => entry.id === id)) return;
      state.selected = id; emit();
    },
    checkAll(checked) {
      if (busy() || state.phase === "done" || !state.valid) return;
      state.checked = new Set(checked ? state.entries.filter(eligible).map((entry) => entry.id) : []);
      revision++; emit();
    },
    remove(id = state.selected) {
      if (busy()) return;
      state.entries = state.entries.filter((entry) => entry.id !== id); state.checked.delete(id);
      if (state.selected === id) state.selected = state.entries[0]?.id ?? null;
      retryPaths = pathsOf(state.entries); retryRecursive = false;
      revision++; emit();
    },
    removeImported() {
      if (busy()) return;
      const completed = new Set(state.entries.filter((entry) => ["done", "library", "installed"].includes(entry.state)).map((entry) => entry.id));
      state.entries = state.entries.filter((entry) => !completed.has(entry.id));
      for (const id of completed) state.checked.delete(id);
      if (completed.has(state.selected)) state.selected = state.entries[0]?.id ?? null;
      retryPaths = pathsOf(state.entries); retryRecursive = false;
      if (state.phase === "done") { state.phase = "idle"; resetResults(); }
      revision++; emit();
    },
    leave() {
      // The navigation session owns the list; only explicit removal discards completed rows.
    },
    clear() {
      if (busy()) return;
      state.entries = []; state.checked.clear(); state.selected = null; state.error = null; state.phase = "idle";
      state.valid = false; state.suggestions = []; retryPaths = []; resetResults(); revision++; emit();
    },
    async saveOptions(options) {
      if (busy()) return false;
      const previous = state.phase;
      state.phase = "saving"; emit();
      try {
        state.options = snapshot(await rpc("import.setOptions", snapshot(options)));
        state.phase = previous; state.error = null; revision++; emit(); return true;
      } catch (error) { fail(error); return false; }
    },
    async start() {
      if (busy() || !state.valid || state.phase === "done" || !selected().length) return false;
      const at = revision;
      starting = true; emit();
      try {
        if (!await loadOptions() || at !== revision || !state.valid || !selected().length) return false;
      } finally { starting = false; emit(); }
      if (at !== revision || !state.valid || !selected().length) return false;
      const entries = structuredClone(selected()), options = snapshot(state.options);
      state.taskEntries = entries; state.taskOptions = options; state.results = [];
      state.phase = "running"; state.error = null;
      state.progress = { done: 0, total: entries.length, current: entries[0].id, index: 1,
        phase: entries[0].state === "ready" ? "add" : entries[0].state === "convert" ? "convert" : "update",
        step: "import.step.check" }; emit();
      try {
        const id = await rpc("import.run", { ids: entries.map((entry) => entry.id), options,
          ...(state.analysisRevision == null ? {} : { revision: state.analysisRevision }) });
        const result = await waitForJob(id);
        state.results = result.results;
        const completed = new Map(state.results.filter((item) => ["imported", "updated", "unchanged"].includes(item.outcome)).map((item) => [item.id, item]));
        const blocked = new Map(state.results.filter((item) => item.state === "blocked").map((item) => [item.id, item]));
        state.entries = state.entries.map((entry) => completed.has(entry.id)
          ? { ...entry, state: "done", libraryId: completed.get(entry.id).libraryId || entry.libraryId }
          : blocked.has(entry.id) ? { ...entry, ...blocked.get(entry.id) } : entry);
        for (const id of blocked.keys()) state.checked.delete(id);
        for (const id of completed.keys()) state.checked.delete(id);
        state.phase = "done"; state.valid = false;
        state.progress = null; changed(); emit(); return true;
      } catch (error) { state.valid = false; changed(); fail(error); return false; }
    },
  };
}

// Import Mod's missing-file prompts and feedback, over the real import session model.

export const missingText = (key, params = {}) => T(`import.missing.${key}`, params);
export const isIncomplete = (entry) => entry?.state === "incomplete";
export const hasFound = (entry) => isIncomplete(entry) && !!entry.missing?.found.length;
export const needsChoose = (entry) => isIncomplete(entry) && !entry.missing?.found.length;
export const statusText = (missing, text = missingText) => missing.found.length
  ? text("count", { n: missing.found.length }) : text("notFound", { n: missing.unresolved });
export const groupsText = (n, text = missingText) => text(n === 1 ? "groupsOne" : "groups", { n });
export const CHOOSE = Object.freeze({ scale: 0.34 });
export const chooseSize = (art) => art.controlSize(missingText("choose"), CHOOSE.scale);
export const NOTE_LEFT = 46, NOTE_GAP = 24;
export const noteRoom = (footer) => footer.left - footer.gap - footer.wide - NOTE_GAP - NOTE_LEFT;
export const promptLines = (entry, { student, kind, text = missingText }) => [text("promptHead"),
  ...entry.missing.found.map((file) => text("promptFile", { name: file.name })), text("promptTail", { student, kind })];

export function createMissingFlow({ model, identify, kindName, ask, title = "", reveal = () => {}, text = missingText,
  active = () => true, failed = () => {}, defer = queueMicrotask }) {
  let seen = null, incomplete = [], shown = new Set();
  const key = (entry) => JSON.stringify([entry.paths.slice().sort(), entry.missing.found.map((file) => file.path).sort()]);
  const fillable = () => model.state.entries.filter(hasFound);
  async function promptRow(entry) {
    shown.add(key(entry));
    const revision = model.state.analysisRevision;
    if (!await ask({ title: typeof title === "function" ? title() : title,
      lines: promptLines(entry, { student: identify(entry), kind: kindName(entry.kind), text }), ok: text("addTogether") })) return false;
    if (model.state.analysisRevision !== revision || !model.state.entries.includes(entry)) return false;
    return model.add(entry.missing.found.map((file) => file.path));
  }
  async function fillAll(rows = fillable()) {
    const revision = model.state.analysisRevision;
    if (!rows.length || model.busy()) return false;
    rows.forEach((entry) => shown.add(key(entry)));
    const lines = rows.map((entry) => text("fillLine", { student: identify(entry), kind: kindName(entry.kind), n: entry.missing.found.length }));
    if (!await ask({ title: text("fillAll", { n: rows.length }), lines, ok: text("addTogether") })) return false;
    if (model.state.analysisRevision !== revision || !rows.every((row) => model.state.entries.includes(row))) return false;
    return model.add(rows.flatMap((entry) => entry.missing.found.map((file) => file.path)));
  }
  function onChange(state) {
    if (!state.entries.length && !model.busy()) { seen = null; incomplete = []; shown.clear(); return; }
    if (state.phase !== "idle" || !state.valid || state.analysisRevision == null || state.analysisRevision === seen) return;
    seen = state.analysisRevision;
    if (state.analysisCause === "recheck") shown.clear();
    const completed = state.entries.find((entry) => !["incomplete", "blocked"].includes(entry.state) && incomplete.some((old) =>
      old.paths.some((path) => entry.paths.includes(path))));
    incomplete = state.entries.filter(isIncomplete);
    if (completed) { model.select(completed.id); reveal(completed.id); }
    // Rows not asked about yet prompt by themselves: one row with its files, several with one list confirmation.
    // A declined row stays on Complete All until a Recheck.
    const rows = fillable().filter((entry) => !shown.has(key(entry))), revision = seen;
    if (rows.length) defer(() => {
      if (active() && !model.busy() && model.state.analysisRevision === revision
        && rows.every((entry) => model.state.entries.includes(entry)))
        (rows.length === 1 ? promptRow(rows[0]) : fillAll(rows)).catch(failed);
    });
  }
  return { onChange, fillable, promptRow, fillAll };
}
