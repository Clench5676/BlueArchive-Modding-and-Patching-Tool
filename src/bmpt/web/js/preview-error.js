// Preview failures also reach the session log in release builds.
import { rpc } from "./api.js";

/**
 * Report a failed preview step once, to the console and to the session log (preview.failed in release builds
 * too), and give the failure back as an Error.
 * @param {string} url The preview's URL.
 * @param {string} step The step that failed.
 */
export function previewError(error, url, step) {
  const cause = error instanceof Error ? error : new Error(String(error));
  if (cause.previewReported) return cause;
  cause.previewReported = true;
  // WebKit's stack is only the frames ("@http://.../async.js:54:75"): the message leads it
  const stack = cause.stack || "",
    text = stack.includes(cause.message) ? stack : [cause.message, stack].filter(Boolean).join("\n");
  const detail = { url: cause.url || url || "", step: cause.step || step, error: text };
  console.error("Preview failed", detail, cause);
  rpc("preview.failed", detail, { timeout: 3000 }).catch((err) => console.error("Preview log failed", err));
  return cause;
}

/** Make a failure an Error that carries the URL and step it happened at, unless it has them already. */
export function atStep(error, url, step) {
  const cause = error instanceof Error ? error : new Error(String(error));
  cause.url ||= url;
  cause.step ||= step;
  return cause;
}
