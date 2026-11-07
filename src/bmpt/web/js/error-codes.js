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

export const codeOf = (name) => registry[name]?.code;
export const isNotice = (name) => registry[name]?.level === "notice";
export const noticeNames = () => Object.keys(registry).filter(isNotice);

export function withCode(message, error, T) {
  const code = error?.ref || codeOf(error?.code);
  return code ? T("popup.withCode", { message, code }) : message;
}
