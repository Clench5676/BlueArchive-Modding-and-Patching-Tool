import { previewTimer } from "./preview-lifecycle.js";

/**
 * Wait for the next animation frame. Hidden or unfocused WebKit windows may stop their frame clock; resource
 * setup can still finish there, and continuous playback remains governed by its lifecycle.
 * @param {object} [options] Options.
 * @param {AbortSignal} [options.signal] Ends the wait.
 */
export function nextFrame({ signal } = {}) {
  return new Promise((resolve) => {
    let frame,
      timer,
      done = false;
    const finish = () => {
      if (done) return;
      done = true;
      globalThis.cancelAnimationFrame?.(frame);
      clearTimeout(timer);
      signal?.removeEventListener("abort", finish);
      resolve();
    };
    const check = () => {
      if (
        globalThis.document?.hidden ||
        globalThis.document?.hasFocus?.() === false ||
        !globalThis.requestAnimationFrame
      )
        finish();
      else timer = setTimeout(check, 100);
    };
    if (signal?.aborted) {
      finish();
      return;
    }
    signal?.addEventListener("abort", finish, { once: true });
    frame = globalThis.requestAnimationFrame?.(finish);
    if (!done) timer = setTimeout(check, 100);
  });
}

/**
 * Bound a wait on an asset loader whose callbacks may never arrive; late rejections are observed too.
 * @param {Promise<*>} promise The wait.
 * @param {number} ms The deadline in milliseconds; Infinity is no deadline.
 * @param {string} name What the wait is for, in the timeout's message.
 * @param {object} [options] Options.
 * @param {AbortSignal} [options.signal] Rejects the wait when it aborts.
 */
export function withTimeout(promise, ms, name, { signal } = {}) {
  return bounded(
    promise,
    ms,
    name,
    (fn, delay) => {
      const timer = setTimeout(fn, delay);
      return () => clearTimeout(timer);
    },
    signal,
  );
}

/**
 * Bound a wait like withTimeout, counting foreground time only and not the time a read or a preview waits for
 * the library (previewTimer).
 * @param {Promise<*>} promise The wait.
 * @param {number} ms The deadline in milliseconds.
 * @param {string} name What the wait is for.
 * @param {object} [options] Options.
 * @param {AbortSignal} [options.signal] Rejects the wait when it aborts.
 */
export function withPreviewTimeout(promise, ms, name, { signal } = {}) {
  return bounded(promise, ms, name, previewTimer, signal);
}

function bounded(promise, ms, name, timer, signal) {
  return new Promise((resolve, reject) => {
    let done = false,
      cancel;
    const finish = (fn, value) => {
      if (done) return;
      done = true;
      cancel?.();
      signal?.removeEventListener("abort", aborted);
      fn(value);
    };
    const aborted = () => finish(reject, signal.reason || new DOMException("Aborted", "AbortError"));
    Promise.resolve(promise).then(
      (value) => finish(resolve, value),
      (err) => finish(reject, err),
    );
    if (signal?.aborted) {
      aborted();
      return;
    }
    signal?.addEventListener("abort", aborted, { once: true });
    if (Number.isFinite(ms)) cancel = timer(() => finish(reject, new Error(`${name}: timed out`)), ms);
  });
}
