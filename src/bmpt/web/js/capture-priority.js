// A modal owns capture priority until its scope closes, even between lazy card requests.
const popups = new Set(),
  listeners = new Set();
const changed = () => {
  for (const listener of [...listeners]) listener();
};
/**
 * Whether any popup holds the capture priority.
 */
export const popupCapturesOpen = () => popups.size > 0;
/**
 * Whether a capture request belongs to a popup holding the priority.
 * @param {AbortSignal} signal The request's signal.
 */
export const popupCapture = (signal) => popups.has(signal);
/**
 * Hold the capture priority for a modal until its signal aborts: it owns the priority, even between lazy card
 * requests, so its captures go before the screen's.
 */
export function holdPopupCaptures(signal) {
  if (signal.aborted) return;
  popups.add(signal);
  signal.addEventListener(
    "abort",
    () => {
      popups.delete(signal);
      changed();
    },
    { once: true },
  );
  changed();
}
/**
 * Subscribe to changes of the capture priority.
 * @param {() => void} listener Called when a popup takes or gives up the priority.
 * @returns {() => void} Unsubscribes.
 */
export function onCapturePriorityChange(listener) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}
/**
 * Whether a queued capture has a client that belongs to a popup.
 */
export const popupItem = (item) => [...item.clients].some((client) => popupCapture(client.signal));
/**
 * Whether a queued capture may run now: no popup holds the priority, or the capture is a popup's.
 */
export const captureEligible = (item) => !popupCapturesOpen() || popupItem(item);
