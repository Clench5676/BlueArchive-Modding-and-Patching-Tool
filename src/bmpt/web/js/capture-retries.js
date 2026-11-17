// Backoff starts only when fresh work has drained. New work pauses and resets the
// idle interval, without consuming another attempt; cancellation retires its timer.
const DELAYS = [2000, 5000, 15000];
/**
 * Keep the captures that timed out for retry with a backoff of 2, 5 and 15 s. The backoff starts only when
 * fresh work has drained; new work pauses it and resets the idle interval without consuming another attempt,
 * and cancellation retires its timer.
 * @param {() => void} wake Called when a waiting capture may run again.
 * @returns {{defer: Function, idle: Function, busy: Function, take: Function, delete: Function}} defer(item,
 *     error) takes a timed-out item (false when the error is not a timeout or the attempts are spent),
 *     idle(match) starts the timers of the waiting items, busy() stops them, take(match) gives an item whose
 *     delay has passed, delete(item) forgets one.
 */
export function captureRetries(wake) {
  const waiting = new Map();
  return {
    defer(item, error) {
      const attempt = item.retries || 0;
      if (!/timed out/i.test(error?.message || "") || attempt >= DELAYS.length) return false;
      waiting.set(item, { ready: false, delay: DELAYS[attempt], timer: null });
      item.retries = attempt + 1;
      return true;
    },
    idle(match = () => true) {
      for (const [item, entry] of waiting) {
        if (!match(item) || entry.ready || entry.timer !== null) continue;
        entry.timer = setTimeout(() => {
          entry.timer = null;
          entry.ready = true;
          wake();
        }, entry.delay);
      }
    },
    busy() {
      for (const entry of waiting.values()) {
        clearTimeout(entry.timer);
        entry.timer = null;
        entry.ready = false;
      }
    },
    take(match = () => true) {
      for (const [item, entry] of waiting)
        if (entry.ready && match(item)) {
          waiting.delete(item);
          return item;
        }
      return null;
    },
    delete(item) {
      clearTimeout(waiting.get(item)?.timer);
      waiting.delete(item);
    },
  };
}
