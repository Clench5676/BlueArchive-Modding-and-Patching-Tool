// The data upgrade notice: before the App starts, the page asks the bootstrap API (GET /upgrade/state) whether the
// data directory and the Mod Library are open in the current format. Until they are, the recovery overlay shows the
// upgrade's state and the choices it waits for; once the state is "ready", the App starts as usual. The view and the
// loop are separate from the overlay renderer (recovery.js). The strings come with each state
// (webui.upgrade.*), so the notice works before the page's own strings are loaded. Once the App has started, the
// ready state may carry a one-time notice of the upgrade itself (upgradedNoticeFor).

/** The reasons a paused upgrade gives; each has its line under upgrade.reason. */
export const REASONS = Object.freeze([
  "busy",
  "olderVersion",
  "classic",
  "unavailable",
  "unsupportedStorage",
  "recoveryPending",
  "newerFormat",
]);
// a paused upgrade that another Mod Library may get past
const RELOCATABLE = new Set(["busy", "unavailable", "unsupportedStorage"]);
const WRITES = new Set(["retry", "keep", "import", "relocate"]);

/** The steps of an upgrade under way, in order; each has its line under upgrade.step. */
export const STEPS = Object.freeze(["check", "settings", "library", "verify", "open"]);
// the steps that count their items (the state's progress) while they run
const COUNTED = new Set(["settings", "library"]);
const STEP_STATES = new Set(["done", "running", "pending"]);

/**
 * The bootstrap request of a notice action: its path under /upgrade/ and its body.
 * @type {Readonly<Record<string, {path: string, body?: object}>>}
 */
export const ACTIONS = Object.freeze({
  retry: { path: "retry" },
  update: { path: "update" },
  openLog: { path: "openLog" },
  quit: { path: "quit" },
  import: { path: "import" },
  keep: { path: "keep" },
  relocate: { path: "relocate" },
});

/**
 * The translator of a state's strings: "upgrade.title" and the like, with {name} parameters filled in.
 * @param {{strings?: Object<string, string>}} state The bootstrap state.
 * @returns {(key: string, params?: object) => string} The key itself when a string is missing.
 */
export const stringsOf =
  (state) =>
  (key, params = {}) =>
    String(state?.strings?.[key] ?? key).replace(/\{(\w+)\}/g, (all, name) => (name in params ? params[name] : all));

/**
 * What the notice shows for a bootstrap state; null when the App may start.
 * @param {object} state GET /upgrade/state: status, phase, reason, steps, percent, progress, affected, strings.
 * @param {boolean} [canRelocate] Whether the host can open its folder picker.
 * @returns {{title: string, hint: string, lines: Array<{text: string, state?: string}>, actions: Array<{id: string, label: string}>, poll: boolean, busy?: boolean, progress?: {percent: number}}|null}
 *     The title and hint, the lines (an upgrade's steps carry their state), the action buttons in order, whether
 *     the state is read again by itself (an upgrade under way), and for an upgrade under way the spinner and its
 *     percentage.
 */
export function noticeView(state, canRelocate = true) {
  if (state.status === "ready") return null;
  const T = stringsOf(state);
  const action = (id) => ({ id, label: T(`upgrade.${id}`) });
  if (state.status === "changed")
    return {
      title: T("upgrade.changed"),
      hint: "",
      lines: [
        { text: T("upgrade.affected") },
        ...(state.affected || []).map((path) => ({ text: String(path) })),
        { text: T("upgrade.backup") },
      ],
      actions: [action("import"), action("keep"), action("quit")],
      poll: false,
    };
  if (state.status !== "running") {
    const reason = state.reason;
    const newer = reason === "newerFormat";
    const key = newer && state.phase === "data" ? "newerFormatData" : reason;
    return {
      title: T("upgrade.paused"),
      hint: REASONS.includes(reason) ? T(`upgrade.reason.${key}`) : "",
      lines: [],
      actions: newer
        ? [action("update"), action("openLog"), action("quit")]
        : [
            action("retry"),
            ...(canRelocate && RELOCATABLE.has(reason) ? [action("relocate")] : []),
            action("openLog"),
            action("quit"),
          ],
      poll: false,
    };
  }
  const p = state.progress;
  const count =
    p && Number.isFinite(p.total) && p.total > 0
      ? T("upgrade.count", { done: Math.max(0, Math.min(p.done || 0, p.total)), total: p.total })
      : "";
  const percent = Number.isFinite(state.percent) ? Math.max(0, Math.min(100, Math.round(state.percent))) : 0;
  return {
    title: T("upgrade.title"),
    hint: T("upgrade.subject"),
    lines: (Array.isArray(state.steps) ? state.steps : [])
      .filter(({ id }) => STEPS.includes(id))
      .map(({ id, status }) => {
        const step = STEP_STATES.has(status) ? status : "pending";
        return {
          text: T(`upgrade.step.${id}`) + (step === "running" && COUNTED.has(id) ? count : ""),
          state: step,
        };
      }),
    actions: [],
    poll: true,
    busy: true,
    progress: { percent },
  };
}

/**
 * Hold the App's start until the data upgrade is done: read the state, show the notice while it is not "ready",
 * send the chosen action and show its answer, and read the state again while the upgrade runs by itself.
 * @param {object} deps
 * @param {(path: string, body?: object) => Promise<object>} deps.request A bootstrap request: "state" is the GET.
 * @param {(view: object) => Promise<string|null>} deps.show Draws a view and settles with the chosen action's id,
 *     or null after the poll delay for a view that polls.
 * @param {() => void} deps.hide Removes the notice.
 * @param {() => (() => void)} [deps.hold] Holds the start watchdog while the notice waits; returns its release.
 * @param {Object<string, string>} [deps.strings] Page strings used before the first answer.
 * @param {boolean} [deps.canRelocate] Whether the host can open its folder picker.
 * @returns {Promise<object|null>} The ready state, or null when the user quits.
 */
export async function upgradeGate({ request, show, hide, hold, strings = {}, canRelocate = true }) {
  const release = hold?.();
  let currentStrings = strings;
  const fetchState = async (path, body) => {
    try {
      const state = await request(path, body);
      if (!state || typeof state.status !== "string") throw new Error("Invalid upgrade state");
      currentStrings = { ...currentStrings, ...state.strings };
      return { ...state, strings: currentStrings };
    } catch {
      return { status: "failed", reason: "error", strings: currentStrings };
    }
  };
  const submit = async ({ path, body }) => {
    const sent = fetchState(path, body).then((state) => ({ complete: state }));
    if (!WRITES.has(path)) return (await sent).complete;
    let running = {};
    for (;;) {
      const view = noticeView({ ...running, status: "running", strings: currentStrings });
      const displayed = await Promise.race([sent, show(view).then(() => ({}))]);
      if (displayed.complete) return displayed.complete;
      const observed = await Promise.race([sent, fetchState("state").then((state) => ({ state }))]);
      if (observed.complete) return observed.complete;
      const { steps, percent, progress } = observed.state;
      // a read before the write has started, or after it has ended, keeps the last steps seen on screen
      if (observed.state.status === "running") running = { steps, percent, progress };
    }
  };
  let quit = false;
  try {
    let state = await fetchState("state");
    while (state.status !== "ready") {
      const choice = await show(noticeView(state, canRelocate));
      if (choice === "quit") {
        try {
          await request("quit");
          quit = true;
          return null;
        } catch {
          state = { status: "failed", reason: "error", strings: currentStrings };
          continue;
        }
      }
      state = choice && Object.hasOwn(ACTIONS, choice) ? await submit(ACTIONS[choice]) : await fetchState("state");
    }
    return state;
  } finally {
    if (!quit) {
      hide();
      release?.();
    }
  }
}

/**
 * The page's bootstrap request: GET /upgrade/state, POST for an action, with the session's cookie.
 * @param {typeof fetch} [fetcher]
 * @returns {(path: string, body?: object) => Promise<object>} The server's state.
 */
export const bootstrapRequest =
  (fetcher = globalThis.fetch) =>
  async (path, body) => {
    const response = await fetcher(`/upgrade/${path}`, {
      method: path === "state" ? "GET" : "POST",
      cache: "no-store",
      credentials: "same-origin",
      ...(body ? { headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) } : {}),
    });
    if (!response.ok) throw new Error(`/upgrade/${path}: HTTP ${response.status}`);
    return response.json();
  };

/**
 * The notice of the first start after an upgrade: the message popup with the upgrade's title and one warning line in
 * the warning ink. Confirming it acknowledges the notice for good; closing it any other way leaves it due for the
 * next start.
 * @param {object} state The ready state of the gate; its notice is "upgraded" while the notice is due.
 * @param {object} deps
 * @param {(options: object) => Promise<{button: string|null}>} deps.message Popup.message.
 * @param {(key: string) => string} deps.T The page's translator.
 * @param {string} deps.warning The warning ink.
 * @param {(path: string) => Promise<object>} [deps.request] A bootstrap request.
 * @returns {(() => Promise<boolean>)|null} null when no notice is due; otherwise a function that shows the notice on
 *     its first call and settles with whether it was confirmed, the same answer on later calls.
 */
export function upgradedNoticeFor(state, { message, T, warning, request = bootstrapRequest() }) {
  if (state?.notice !== "upgraded") return null;
  let shown = null;
  return () => {
    shown ??= (async () => {
      const answer = await message({
        title: T("upgrade.title"),
        lines: [{ text: T("upgrade.upgradedWarning"), color: warning }],
        ok: T("picker.ok"),
      });
      if (answer?.button !== "ok") return false;
      await request("acknowledge");
      return true;
    })();
    return shown;
  };
}

const POLL_MS = 500; // how often a running upgrade's progress is read again

/**
 * The gate as the page runs it: the notice in the recovery overlay (recovery.js notice), the start watchdog held
 * while it waits. main.js awaits it before anything of the App runs, so no request reaches the services the upgrade
 * holds back.
 * @param {object} [recovery] The recovery script's API (globalThis.BAMTRecovery).
 * @param {object} [config] Early strings and desktop capabilities from the served page.
 * @returns {Promise<object>} The state that allows the Web UI to start.
 */
export async function startGate(recovery = globalThis.BAMTRecovery, config = globalThis.BAMT_CONFIG) {
  let timer;
  const show = (view) =>
    new Promise((resolve) => {
      clearTimeout(timer);
      timer = view.poll ? setTimeout(() => resolve(null), POLL_MS) : null;
      recovery?.notice?.(view, (id) => {
        clearTimeout(timer);
        resolve(id);
      });
    });
  const ready = await upgradeGate({
    request: bootstrapRequest(),
    show,
    hide: () => {
      clearTimeout(timer);
      recovery?.closeNotice?.();
    },
    hold: () => recovery?.hold?.() ?? (() => {}),
    strings: config?.strings,
    canRelocate: config?.canRelocateUpgrade !== false,
  });
  if (ready && config?.upgradeHandoff === "safeMode") location.reload();
  if (!ready || config?.upgradeHandoff) await new Promise(() => {});
  return ready;
}
