// The detail screen's keys for the previous and next student, as the PC client binds them: its key mapping gives
// UICharacterDetail's left and right arrows LeftArrow and RightArrow, with no second key, no key icon on the
// arrows and a press rather than a long press. No DOM: the tests run it in Node.

export const KEYS = Object.freeze({ ArrowLeft: -1, ArrowRight: 1 });

/**
 * The step a key press asks for.
 * @returns {-1|0|1} -1 or 1 for a step key pressed alone; 0 for another key, a modifier held, an input method
 *     composing, or a press another handler took.
 */
export function deltaOf(ev) {
  // keyCode 229: WebKit's key that ends a conversion, sent after compositionend
  if (!ev || ev.defaultPrevented || ev.isComposing || ev.keyCode === 229) return 0;
  if (ev.altKey || ev.ctrlKey || ev.metaKey || ev.shiftKey) return 0;
  return KEYS[ev.key] || 0;
}

/** Whether an element takes typed text or arrow keys itself (inputs, text areas, selects, editable text). */
export const typing = (el) =>
  !!el && (el.isContentEditable === true || /^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName || ""));

/**
 * Make the pacer that steps one student at a time: a press meanwhile waits for the step in flight (the latest
 * one only), and a held key's repeat only while the key is held, so a held key steps at the screen's pace.
 * @param {(delta: number) => Promise<void>} run Steps and resolves once the screen shows where it went.
 * @returns
 *     {{press: (delta: number, repeat?: boolean) => void, release: (delta?: number) => void, clear: () => void, busy: () => boolean}}
 *     release is for a step key let go (any, without delta: the window lost the keyboard): its repeat waits no
 *     longer.
 */
export function pacer(run) {
  let running = false,
    next = null;
  const go = (delta) => {
    running = true;
    const done = () => {
      running = false;
      const n = next;
      next = null;
      if (n) go(n.delta);
    };
    Promise.resolve()
      .then(() => run(delta))
      .then(done, (err) => {
        console.error(err);
        done();
      });
  };
  return {
    press(delta, repeat = false) {
      if (!delta) return;
      if (running) next = { delta, repeat: !!repeat };
      else go(delta);
    },
    // a step key let go (any, without delta: the window lost the keyboard): its repeat waits no longer
    release(delta) {
      if (next?.repeat && (delta === undefined || next.delta === delta)) next = null;
    },
    clear() {
      next = null;
    },
    get busy() {
      return running;
    },
  };
}
