// Quitting from the student list, as the PC client's back key does on the home screen: the top bar's back button, or
// Escape on the list, opens the game's quit prompt (title Notice, line Quit?, Cancel and Confirm). Without the DOM
// (tested in Node from tests/test_quit.py); main.js wires it.
import { errorText } from "../core/i18n.js";

// the hosts of the popups and screens that take Escape themselves: the asset consent, scan consent and update popups,
// the display settings, the settings, the shared message and list popups, the detail screen's own
export const HOSTS = Object.freeze(["consent", "display", "settings", "dialog", "prompt", "popup", "picker"]);

/**
 * Whether a key press opens the quit prompt: Escape alone, on the student list, when nothing else takes it.
 * Never when another handler took the key (the search box, the display settings), while an input method
 * composes (keyCode 229 is WebKit's key that ends a conversion), for a held key's repeat, with a modifier,
 * while a popup is open, or behind the loading screen.
 * @param {object} options
 * @param {(id: string) => (HTMLElement|null)} options.byId The page's element of an id
 *     (document.getElementById).
 * @param {boolean} [options.covering] The loading screen covers the stage (Loading.covering()).
 */
export function quitKey(ev, { byId, covering = false }) {
  if (!ev || ev.key !== "Escape") return false;
  if (ev.defaultPrevented || ev.isComposing || ev.keyCode === 229 || ev.repeat) return false;
  if (ev.altKey || ev.ctrlKey || ev.metaKey || ev.shiftKey) return false;
  if (covering) return false;
  const studentList = byId("screen-student-list");
  if (!studentList || studentList.hidden) return false;
  return !HOSTS.some((id) => {
    const host = byId(id);
    return !!host && !host.hidden;
  });
}

// The PC client's key mapping (PCKeyMappingSO) binds the confirm of its popups (UIPopup_System and UIPopup_Sort,
// Btn_Ok: UI_KeyMapping_Common_BtnOK01) to Return, with Space as its second key; their cancel's Escape binding is
// unused, and Escape closes the popup in front as for any popup.
export const CONFIRM_KEYS = Object.freeze(["Enter", " "]);

// the quit prompt, then the quit:
//   message(opts)  the shared message popup (popup.js), resolving with {button}
//   rpc, T, notify  the page's
//   native         the app runs in its own window (app.quit closes it)
//   closed()       shows that the app has quit (browser mode: the server stops after answering)
// Resolves with "cancel", "quit" or the refusal's error code (busy while a writer job runs, queued while writer jobs
// only wait and the user kept them, native_unavailable with --serve). A queued refusal asks whether to cancel them
// (confirmCancelQueued), as the server's app.quitQueued does for a quit from the window or the tray.
let asking = false;
/**
 * Ask whether to quit and quit.
 * @param {object} options
 * @param {(opts: object) => Promise<{button: string}>} options.message The shared message popup (popup.js).
 * @param {Function} options.rpc The page's RPC call.
 * @param {Function} options.T The page's translate function.
 * @param {Function} options.notify The page's error notice.
 * @param {boolean} [options.native] The app runs in its own window (app.quit closes it).
 * @param {() => void} options.closed Shows that the app has quit.
 * @returns {Promise<string>} "cancel", "quit" or the refusal's error code (busy while a writer job runs, queued
 *     while writer jobs only wait, native_unavailable with --serve).
 */
export async function confirmQuit({ message, rpc, T, notify, native = false, closed }) {
  if (asking) return "cancel";
  asking = true;
  try {
    const { button } = await message({
      title: T("quit.title"),
      lines: [T("quit.ask")],
      ok: T("picker.ok"),
      cancel: T("popup.cancel"),
      confirmKeys: CONFIRM_KEYS,
    });
    if (button !== "ok") return "cancel";
    const result = await quit({ rpc, T, notify, native, closed });
    return result === "queued" ? await confirmCancelQueued({ message, rpc, T, notify, native, closed }) : result;
  } finally {
    asking = false;
  }
}

async function quit({ rpc, T, notify, native, closed }, params) {
  try {
    await (params ? rpc("app.quit", params) : rpc("app.quit"));
  } catch (err) {
    if (err?.code !== "queued") notify(errorText(err, T), { kind: "error", error: err });
    return err?.code || "internal";
  }
  if (!native) closed?.();
  return "quit";
}

let askingQueued = false;
/**
 * A quit with writer jobs only waiting in the queue (app.quitQueued): say they will be cancelled, and quit
 * cancelling them on Confirm. A running one refuses the quit again (busy).
 * @param {object} options As confirmQuit's.
 * @returns {Promise<string>} "queued" when the user keeps them, "quit" or the refusal's error code.
 */
export async function confirmCancelQueued({ message, rpc, T, notify, native = false, closed }) {
  if (askingQueued) return "cancel";
  askingQueued = true;
  try {
    const { button } = await message({
      title: T("quit.title"),
      lines: [T("quit.queued")],
      ok: T("picker.ok"),
      cancel: T("popup.cancel"),
      confirmKeys: CONFIRM_KEYS,
    });
    if (button !== "ok") return "queued";
    return await quit({ rpc, T, notify, native, closed }, { cancelQueued: true });
  } finally {
    askingQueued = false;
  }
}
