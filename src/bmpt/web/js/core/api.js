// Talks to the Python side: POST /api/rpc for calls, /api/events (Server-Sent Events) for jobs, drops and changes.
import { withPreviewTimeout as withTimeout } from "./async.js";

export class RpcError extends Error {
  constructor(code, params = {}, ref = null) {
    super(code);
    this.code = code;
    this.params = params;
    if (ref) this.ref = ref;
  }
}

/**
 * Call a method of the Python side (POST /api/rpc). The timeout is opt-in: a read can wait on a writer's
 * library lock for as long as an apply runs.
 * @param {object} [options]
 * @param {number} [options.timeout] The longest wait in milliseconds; 0 waits as long as it takes.
 * @param {AbortSignal} [options.signal] Cancels the call.
 * @returns {Promise<*>} The method's result. Rejects with an RpcError.
 */
export async function rpc(method, params = {}, { timeout = 0, signal } = {}) {
  const controller = new AbortController();
  const abort = () => controller.abort();
  signal?.addEventListener("abort", abort, { once: true });
  if (signal?.aborted) abort();
  const timer = timeout > 0 ? setTimeout(() => controller.abort(), timeout) : null;
  const endRequest = globalThis.BAMTRecovery?.request?.();
  try {
    const res = await fetch("/api/rpc", {
      signal: controller.signal,
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ method, params }),
    });
    if (!res.ok) throw new RpcError("internal", { error: `HTTP ${res.status}` });
    const body = await res.json();
    if (body.error) throw new RpcError(body.error.code, body.error.params || {}, body.error.ref);
    return body.result;
  } catch (err) {
    if (err instanceof RpcError) throw err;
    throw new RpcError("internal", { error: String(err) });
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener("abort", abort);
    endRequest?.();
  }
}

const listeners = new Map();
let source = null;

/**
 * Subscribe to a server event (Server-Sent Events from /api/events): on("job.done", (data) => ...).
 * @param {(data: object) => void} fn Called with the event's data.
 * @returns {() => void} Unsubscribes.
 */
export function on(event, fn) {
  if (!listeners.has(event)) listeners.set(event, new Set());
  listeners.get(event).add(fn);
  connect();
  if (source && !source.bamtEvents.has(event)) {
    source.bamtEvents.add(event);
    source.addEventListener(event, (e) => dispatch(event, e.data));
  }
  return () => listeners.get(event)?.delete(fn);
}

function dispatch(event, raw) {
  let data = {};
  try {
    data = JSON.parse(raw);
  } catch {
    /* keep {} */
  }
  for (const fn of listeners.get(event) || []) {
    try {
      fn(data);
    } catch (err) {
      console.error(err);
    }
  }
}

function connect() {
  if (source) return;
  source = new EventSource("/api/events");
  source.bamtEvents = new Set();
  for (const event of listeners.keys()) {
    source.bamtEvents.add(event);
    source.addEventListener(event, (e) => dispatch(event, e.data));
  }
}

/**
 * A job's end as a promise (resolves with the result, rejects with RpcError; "cancelled" for a job the page
 * stopped, jobs.cancel). Events can arrive before this subscribes, or be lost while the event stream
 * reconnects, so the job's state is also polled. Runtime is unbounded by default: sleeping or preparing a large
 * batch does not mean the backend job failed. Each status read is bounded.
 * @param {(progress: object) => void} [onProgress] Called with the job's progress.
 * @param {object} [options]
 * @param {number} [options.pollEvery] The polling interval in milliseconds.
 * @param {number} [options.maxErrors] The most failed status reads in a row before the wait fails.
 * @param {number} [options.timeout] The longest wait in milliseconds; 0 waits as long as it takes.
 * @param {number} [options.requestTimeout] The longest one status read may take in milliseconds.
 * @param {(connected: boolean) => void} [options.onConnection] Called when the event stream drops or comes
 *     back.
 */
export function waitJob(
  id,
  onProgress,
  { pollEvery = 1500, maxErrors = Infinity, timeout = 0, requestTimeout = 15000, onConnection = () => {} } = {},
) {
  return new Promise((resolve, reject) => {
    let settled = false,
      timer,
      deadline,
      errors = 0,
      progressSequence = 0,
      generation = 0;
    let activeRead = null,
      connected = true;
    const offs = [];
    const finish = (fn, value) => {
      if (settled) return;
      settled = true;
      generation++;
      clearTimeout(timer);
      clearTimeout(deadline);
      activeRead?.abort();
      activeRead = null;
      offs.forEach((f) => f());
      fn(value);
    };
    const failed = (error) => finish(reject, error);
    const notify = (observer, value) => {
      try {
        observer?.(value);
      } catch (error) {
        console.error("Job observer failed", error);
      }
    };
    const connection = (value) => {
      if (connected === value) return;
      connected = value;
      notify(onConnection, value);
    };
    const progress = (data) => {
      if (settled) return;
      if (Number.isSafeInteger(data?.sequence)) {
        if (data.sequence <= progressSequence) return;
        progressSequence = data.sequence;
      } else if (progressSequence) return;
      notify(onProgress, data);
    };
    const schedule = (delay) => {
      clearTimeout(timer);
      const current = generation;
      timer = setTimeout(() => {
        if (!settled && current === generation) void poll();
      }, delay);
    };
    const poll = async () => {
      if (settled) return;
      clearTimeout(timer);
      const current = ++generation,
        read = new AbortController();
      activeRead?.abort();
      activeRead = read;
      try {
        const job = await rpc("jobs.get", { id }, { timeout: requestTimeout, signal: read.signal });
        if (settled || current !== generation) return;
        errors = 0;
        connection(true);
        if (job.progress) progress(job.progress);
        if (job.state === "done") return finish(resolve, job.result);
        if (job.state === "failed")
          return failed(new RpcError(job.error?.code || "internal", job.error?.params || {}, job.error?.ref));
        if (job.state === "cancelled") return failed(new RpcError("cancelled"));
        if (job.state !== "queued" && job.state !== "running")
          return failed(new RpcError("internal", { error: `Job ${job.state}` }));
      } catch (err) {
        if (settled || current !== generation) return;
        if (err.code === "job_not_found" || ++errors >= maxErrors) return failed(err);
        connection(false);
      } finally {
        if (activeRead === read) activeRead = null;
      }
      if (!settled && current === generation) schedule(Math.min(15000, pollEvery * 2 ** Math.min(errors, 4)));
    };
    const wake = () => {
      if (settled) return;
      // Drop pre-sleep reads before they can report an expired timeout or stale snapshot.
      generation++;
      errors = 0;
      activeRead?.abort();
      activeRead = null;
      schedule(0);
    };
    const listen = (target, name, fn) => {
      target?.addEventListener?.(name, fn);
      offs.push(() => target?.removeEventListener?.(name, fn));
    };
    try {
      offs.push(
        on("job.progress", (d) => {
          if (d.id === id) {
            errors = 0;
            connection(true);
            progress(d);
          }
        }),
        on("job.done", (d) => {
          if (d.id === id) finish(resolve, d.result);
        }),
        on("job.failed", (d) => {
          if (d.id === id) failed(new RpcError(d.code, d.params, d.ref));
        }),
        on("job.cancelled", (d) => {
          if (d.id === id) failed(new RpcError("cancelled"));
        }),
        on("server.closing", () => failed(new RpcError("internal", { error: "Server closed" }))),
        on("open", wake),
      );
      listen(globalThis, "focus", wake);
      listen(globalThis, "pageshow", wake);
      listen(globalThis.document, "visibilitychange", () => {
        if (!globalThis.document.hidden) wake();
      });
    } catch (err) {
      failed(err);
      return;
    }
    // Explicit deadlines remain available to bounded callers, never imposed on imports by default.
    if (timeout > 0)
      deadline = setTimeout(() => failed(new RpcError("internal", { error: "Job wait timed out" })), timeout);
    void poll();
  });
}

// asset URLs
// The bootstrap epoch bypasses stale max-age entries. Every new light response
// also revalidates its schema/content ETag, including after game resource changes.
let assetContext = "";
/**
 * Set the context of the asset URLs (the game resource version): the bootstrap epoch bypasses stale max-age
 * entries, and every new light response also revalidates its schema and content ETag, including after game
 * resource changes.
 */
export function setAssetContext(value) {
  assetContext = value || "";
  forgetSpineAssets();
}
/** The URL of a UI export, kept to its path and query so it works behind any origin. */
export function uiExportUrl(url) {
  const epoch = globalThis.document?.querySelector?.('meta[name="bamt-ui-exports"]')?.content;
  if ((!epoch && !assetContext) || !url.startsWith("/asset/ui/")) return url;
  const parsed = new URL(url, "http://exports.invalid");
  if (epoch) parsed.searchParams.set("export", epoch);
  if (assetContext) parsed.searchParams.set("resource", assetContext);
  return `${parsed.pathname}${parsed.search}`;
}
/** The URL of an atlas sprite's image. */
export const spriteUrl = (atlas, name) =>
  uiExportUrl(`/asset/ui/sprite/${encodeURIComponent(atlas)}/${encodeURIComponent(name)}.png`);
/** The URL of a UI texture's image. */
export const textureUrl = (name) => uiExportUrl(`/asset/ui/texture/${encodeURIComponent(name)}.png`);
/**
 * What every skeleton URL of a core starts with, whatever the file: the library.changed handler forgets the
 * thumbnails under it once the core's game files were switched.
 */
export const spinePrefix = (kind, key) => `/asset/spine/${kind}/${encodeURIComponent(key)}`;
/**
 * A skeleton's base path: <base>.skel and <base>.atlas; the atlas pages resolve next to them. The id is in the
 * path, not a query: the player fetches every file relative to the base.
 * @param {string} kind "portrait", "lobby" or "model".
 * @param {string} key The core.
 * @param {string|null} [install] The id of another known install (mods.state.installs[].id), whose own files
 *     the core segment <core>@<id> reads (an entry card of that install); none for the current install's.
 */
export const spineBase = (kind, key, install = null) =>
  `${spinePrefix(kind, key)}${install ? `@${encodeURIComponent(install)}` : ""}/skeleton`;

// spineAssetBase resolves on every load: the logical source can carry different files after a switch or a mod
// update. The revision is kept in the directory so the player also requests atlas pages from that same revision.
// A missing original returns null and uses the existing empty thumbnail treatment. Requests for one directory
// that overlap (a card and its big picture, the same skeleton asked for twice) share one answer; a request after
// it settled asks again.
const resolving = new Map();
/** Forget the resolved skeleton directories under a prefix (all of them by default). */
export function forgetSpineAssets(prefix = "") {
  const want = prefix.toLowerCase();
  for (const dir of resolving.keys()) if (dir.toLowerCase().startsWith(want)) resolving.delete(dir);
}

/**
 * Resolve a skeleton's base to its current revision's directory.
 * @param {string} base A skeleton's base path (spineBase).
 * @param {object} [options]
 * @param {AbortSignal} [options.signal] Cancels the caller's wait; it does not cancel an unrelated reader of
 *     the same directory.
 */
export function spineAssetBase(base, { signal } = {}) {
  if (!base || /\/[a-f0-9]{64}\/skeleton$/.test(base)) return Promise.resolve(base);
  const dir = base.slice(0, base.lastIndexOf("/"));
  if (signal || !resolving.has(dir)) {
    const url = `${dir}/manifest.json`;
    const asking = withTimeout(
      fetch(url, { cache: "no-store", ...(signal ? { signal } : {}) }).then(async (res) => {
        if (!res.ok) {
          const error = new Error(`HTTP ${res.status}`);
          if (res.status !== 404) throw error;
          console.warn(new Error(`Spine manifest ${url}: ${error.message}`, { cause: error }));
          return null;
        }
        let manifest;
        try {
          manifest = await res.json();
        } catch (cause) {
          throw new Error(`HTTP ${res.status}: invalid JSON`, { cause });
        }
        const revision = manifest?.revision;
        if (typeof revision !== "string" || !/^[a-f0-9]{64}$/.test(revision))
          throw new Error(`HTTP ${res.status}: invalid revision`);
        return `${dir}/${revision}/skeleton`;
      }),
      15000,
      `Spine manifest ${url}`,
      { signal },
    )
      .catch((cause) => {
        const error = new Error(`Spine manifest ${url}: ${cause?.message || String(cause)}`, { cause });
        error.url = url;
        error.step = "manifest";
        throw error;
      })
      .finally(() => {
        if (resolving.get(dir) === asking) resolving.delete(dir);
      });
    // A scoped reader owns its cancellation; it must not cancel an unrelated reader of the same directory.
    if (signal) return asking;
    resolving.set(dir, asking);
  }
  return resolving.get(dir);
}

/**
 * Load a skeleton's files from one revision. A cold extraction can overlap a source change after its manifest
 * answered; the composition is retried once with the current manifest, so every skeleton, atlas and page of an
 * attempt still belongs to one revision.
 * @param {string} base A skeleton's base path.
 * @param {(resolved: string) => Promise<*>} load Loads the files under the resolved directory.
 * @param {object} [options]
 * @param {AbortSignal} [options.signal] Cancels the load.
 */
export async function loadSpineAssets(base, load, { signal } = {}) {
  let resolved = await spineAssetBase(base, { signal });
  for (let attempt = 0; ; attempt++) {
    if (signal?.aborted) return null;
    if (!resolved) throw new Error(`Spine source unavailable: ${base}`);
    try {
      return await load(resolved);
    } catch (err) {
      if (signal?.aborted || attempt) throw err;
      forgetSpineAssets(base.slice(0, base.lastIndexOf("/")));
      let fresh;
      try {
        fresh = await spineAssetBase(base, { signal });
      } catch (cause) {
        console.warn("Cannot refresh Spine source", base, cause);
        throw err;
      }
      if (!fresh || fresh === resolved) throw err;
      console.warn("Spine source changed during load", base, err);
      resolved = fresh;
    }
  }
}
