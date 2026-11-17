/**
 * Guard the native window's close: input is frozen only after the Hall's permission has saved or discarded its
 * draft, and a cancelled or timed out native request cannot consume a later page approval.
 * @param {object} options What it guards.
 * @param {() => Promise<boolean|void>} options.prepare Asks the page whether it may close; false refuses.
 * @param {() => HTMLElement[]} options.root The elements frozen while the close is approved.
 * @param {() => *} [options.revision] A token of the page's state; an approval holds only for it.
 * @param {() => Element} [options.focus] Gives the element that has the focus now.
 * @param {object} [options.target] The window that hosts the native callbacks.
 */
export function installCloseGuard({
  prepare,
  root,
  revision = () => null,
  focus = () => globalThis.document.activeElement,
  target = globalThis,
}) {
  const cancelled = new Set();
  let current = null,
    held = null,
    previous = null,
    generation = 0,
    approvedRevision = null;
  target.bamtPrepareClose = async (token) => {
    if (cancelled.has(token) || current !== null) return false;
    current = token;
    const version = ++generation;
    previous = focus();
    try {
      const allowed = await prepare();
      if (version !== generation || current !== token) return false;
      if (allowed === false) {
        target.bamtCancelClose(token);
        return false;
      }
      held = root().map((element) => [element, element.inert]);
      for (const [element] of held) element.inert = true;
      approvedRevision = revision();
      return true;
    } catch {
      target.bamtCancelClose(token);
      return false;
    }
  };
  target.bamtValidateClose = (token) => current === token && held !== null && revision() === approvedRevision;
  target.bamtCancelClose = (token) => {
    cancelled.add(token);
    if (current !== token) return;
    ++generation;
    current = null;
    for (const [element, inert] of held || []) element.inert = inert;
    held = null;
    if (previous?.isConnected && !previous.closest?.("[inert]")) previous.focus({ preventScroll: true });
    previous = null;
  };
}
