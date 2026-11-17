// Now Loading freezes the previews already shown beneath it (animation, sound, timers) and resumes them once it is
// gone. A preview that starts while Now Loading is up is being prepared behind it and keeps going; only the next
// Now Loading freezes it. loading.js switches this with freezeShown(locked()).
const freezable = new Set();
let frozenNow = false;
/**
 * Whether the previews shown are frozen under Now Loading.
 */
export const frozen = () => frozenNow;
/**
 * Whether a preview element is on screen: laid out and not hidden (a preview prepared in a hidden layer is not
 * shown).
 */
export function onScreen(el) {
  if (!el?.isConnected) return false;
  try {
    return el.checkVisibility ? el.checkVisibility({ visibilityProperty: true }) : !el.hidden;
  } catch {
    return true;
  }
}
/**
 * Freeze or thaw the previews already shown: Now Loading freezes them (animation, sound, timers) and resumes
 * them once it is gone. A preview that starts while Now Loading is up is being prepared behind it and keeps
 * going; only the next Now Loading freezes it. loading.js switches this with freezeShown(locked()).
 * @param {boolean} on True while Now Loading is up.
 */
export function freezeShown(on) {
  on = !!on;
  if (on === frozenNow) return;
  frozenNow = on;
  for (const member of [...freezable]) {
    if (!freezable.has(member)) continue;
    try {
      if (on && !member.held && member.shown()) {
        member.held = true;
        member.freeze();
      } else if (!on && member.held) {
        member.held = false;
        member.thaw();
      }
    } catch (err) {
      console.error(err);
    }
  }
}
/**
 * Register a preview that Now Loading freezes.
 * @param {object} [options]
 * @param {() => boolean} [options.shown] Whether the preview is on screen now.
 * @param {() => void} [options.freeze] Stops the preview.
 * @param {() => void} [options.thaw] Restarts it.
 * @returns {{held: () => boolean, dispose: () => void}} held tells whether it is frozen now; dispose
 *     unregisters it.
 */
export function freezeWhileLoading({ shown = () => true, freeze = () => {}, thaw = () => {} } = {}) {
  const member = { shown, freeze, thaw, held: false };
  freezable.add(member);
  return {
    held: () => member.held,
    dispose: () => {
      freezable.delete(member);
      member.held = false;
    },
  };
}

/**
 * Follow the page's visibility and focus for a preview: a background window has no reliable frame clock, so it
 * resumes with fresh clocks, and lost contexts need new GPU owners.
 * @param {object} [hooks] What the preview does.
 * @param {() => void} [hooks.suspend] The window went away.
 * @param {() => void} [hooks.resume] It came back; clocks restart.
 * @param {() => void} [hooks.recover] A lost context must be rebuilt.
 * @param {(() => boolean)|null} [hooks.shown] When given, the preview also stops while Now Loading is up over
 *     it (freezeWhileLoading).
 * @param {object} [env] The page, for the tests.
 */
export function previewLifecycle(
  { suspend = () => {}, resume = () => {}, recover = () => {}, shown = null } = {},
  { document: doc = globalThis.document, window: win = globalThis.window, now = () => performance.now() } = {},
) {
  let focused = doc?.hasFocus?.() ?? true,
    away = !!doc?.hidden || !focused,
    disposed = false,
    rebuilding = false;
  const lost = new Set(),
    cleanups = [];
  let freeze = null;
  const listen = (target, type, fn, capture = false) => {
    target?.addEventListener?.(type, fn, capture);
    cleanups.push(() => target?.removeEventListener?.(type, fn, capture));
  };
  const active = () => !disposed && !away && !lost.size && !rebuilding;
  const rebuild = () =>
    queueMicrotask(() => {
      if (disposed || away || lost.size || !rebuilding) return;
      rebuilding = false;
      recover();
    });
  const changed = () => {
    const hidden = !!doc?.hidden || !focused || !!freeze?.held();
    if (hidden === away) return;
    away = hidden;
    if (away) suspend();
    else if (rebuilding) rebuild();
    else if (!lost.size) resume(now());
  };
  listen(doc, "visibilitychange", () => {
    focused = doc?.hasFocus?.() ?? focused;
    changed();
  });
  listen(win, "blur", () => {
    focused = false;
    changed();
  });
  listen(win, "focus", () => {
    focused = true;
    changed();
  });
  if (shown) {
    freeze = freezeWhileLoading({ shown: () => !disposed && shown(), freeze: changed, thaw: changed });
    cleanups.push(() => freeze.dispose());
  }
  return {
    active,
    recovering: () => !!lost.size || rebuilding,
    watch(canvas) {
      listen(
        canvas,
        "webglcontextlost",
        (event) => {
          event.preventDefault();
          lost.add(canvas);
          rebuilding = true;
          suspend();
        },
        true,
      );
      listen(
        canvas,
        "webglcontextrestored",
        () => {
          lost.delete(canvas);
          rebuild();
        },
        true,
      );
    },
    invalidate() {
      rebuilding = true;
      suspend();
      rebuild();
    },
    dispose() {
      disposed = true;
      cleanups.splice(0).forEach((fn) => fn());
    },
  };
}

// While a read of the page waits for the Mod library (a job holds it; the backend's library.waiting event), or a
// preview waits for a job (the model lock, a costume build of the same pair; preview.waiting), preview deadlines
// stop: that wait is not the preview's own work. Each source is told apart, so one ending does not end the
// other. A spell longer than LIBRARY_WAIT_CAP counts again, in case its end was never heard (the event stream
// reconnected).
export const LIBRARY_WAIT_CAP = 120000;
const waitWatchers = new Set();
const waitingFor = new Map(); // source -> its cap timer
let libraryBusy = false;
/**
 * Whether a read or a preview is waiting for the library or a job.
 */
export const libraryWaiting = () => libraryBusy;
/**
 * Note that a source starts or stops waiting for the library or a job.
 * @param {boolean} busy Whether it waits.
 * @param {string} [source] Which source waits: "library" or "preview".
 */
export function setLibraryWaiting(busy, source = "library") {
  busy = !!busy;
  clearTimeout(waitingFor.get(source));
  waitingFor.delete(source);
  if (busy)
    waitingFor.set(
      source,
      setTimeout(() => setLibraryWaiting(false, source), LIBRARY_WAIT_CAP),
    );
  busy = waitingFor.size > 0;
  if (busy === libraryBusy) return;
  libraryBusy = busy;
  for (const update of [...waitWatchers]) update();
}

/**
 * Start a deadline that counts foreground time, including waits for requestAnimationFrame during initial
 * loading, and leaves out the time a read or a preview waits for the library or a job (setLibraryWaiting).
 * @param {() => void} callback Called when the deadline passes.
 * @param {number} ms The deadline in milliseconds.
 * @returns {() => void} Cancels the deadline.
 */
export function previewTimer(callback, ms, env = {}) {
  const now = env.now || (() => performance.now());
  let timer,
    started = null,
    remaining = ms,
    done = false;
  const pause = () => {
    clearTimeout(timer);
    if (started != null) remaining -= Math.max(0, now() - started);
    started = null;
  };
  const start = () => {
    if (done || started != null) return;
    started = now();
    timer = setTimeout(
      () => {
        cancel();
        callback();
      },
      Math.max(0, remaining),
    );
  };
  const update = () => {
    if (!done && lifecycle.active() && !libraryBusy) start();
    else pause();
  };
  const lifecycle = previewLifecycle({ suspend: update, resume: update }, env);
  const cancel = () => {
    done = true;
    clearTimeout(timer);
    lifecycle.dispose();
    waitWatchers.delete(update);
  };
  waitWatchers.add(update);
  update();
  return cancel;
}
