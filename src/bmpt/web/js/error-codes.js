// One registry for both service errors and import reasons. A missing registry leaves the UI usable.
let registry = {};
try {
  const response = await fetch("/error-codes.json");
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  const loaded = await response.json();
  if (!loaded || typeof loaded !== "object" || Array.isArray(loaded)) throw new Error("Invalid error code registry");
  registry = loaded;
} catch (error) {
  console.error("Error code registry unavailable", error);
}

/**
 * The reference code of an error name in the registry (/error-codes.json, one registry for service errors and
 * import reasons; a missing one leaves the UI usable).
 */
export const codeOf = (name) => registry[name]?.code;
/**
 * Whether an error name is an ordinary refusal that reads as a notice, not a failure.
 */
export const isNotice = (name) => registry[name]?.level === "notice";
/**
 * The error names that read as notices.
 */
export const noticeNames = () => Object.keys(registry).filter(isNotice);

/**
 * Add an error's reference code to its message.
 * @param {string} message The message.
 * @param {{code?: string, ref?: string}|null} error The error.
 * @param {(key: string, params?: object) => string} T The translate function.
 */
export function withCode(message, error, T) {
  const code = error?.ref || codeOf(error?.code);
  return code ? T("popup.withCode", { message, code }) : message;
}
