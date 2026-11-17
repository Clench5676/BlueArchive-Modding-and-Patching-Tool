import { rpc } from "./core/api.js";

const ROOT = "vendor/spine-player-4.2.120/";
let choice,
  reading = null,
  loading = null,
  started = false;

/**
 * Set the stored answer to the Spine licence notice. Only a saved Yes enables the runtime; an unanswered notice
 * or a stored No cannot.
 * @param {boolean|null} value The saved answer; anything but a boolean is unanswered.
 */
export function setSpineConsent(value) {
  choice = typeof value === "boolean" ? value : null;
}

/**
 * Whether the Spine runtime may be used: it is loaded already, or the answer is Yes.
 * @returns {boolean} True when it may.
 */
export const spineAllowed = () => started || choice === true;
/**
 * Whether the Spine runtime has been loaded.
 * @returns {boolean} True when it has.
 */
export const runtimeLoaded = () => started;

/**
 * Load the Spine runtime. Every preview entry awaits this before using the runtime, including readers that do
 * not create a player.
 * @returns {Promise<boolean>} True once the runtime is in; false without consent. Rejects when the files fail
 *     to load.
 */
export async function ensureSpine() {
  if (choice === undefined) {
    reading ||= rpc("app.spineConsent")
      .then((value) => {
        if (choice === undefined) setSpineConsent(value);
      })
      .finally(() => {
        reading = null;
      });
    await reading;
  }
  if (!spineAllowed()) return false;
  if (globalThis.spine) {
    started = true;
    return true;
  }
  if (!loading) {
    loading = new Promise((resolve, reject) => {
      const css = document.createElement("link"),
        script = document.createElement("script");
      css.rel = "stylesheet";
      css.href = `${ROOT}spine-player.css`;
      script.src = `${ROOT}spine-player.min.js`;
      const fail = (message) => {
        script.remove();
        css.remove();
        reject(new Error(message));
      };
      script.onload = () => (globalThis.spine ? resolve(true) : fail("Spine runtime unavailable"));
      script.onerror = () => fail("Spine runtime failed to load");
      started = true;
      document.head.appendChild(css);
      document.head.appendChild(script);
    }).catch((err) => {
      loading = null;
      throw err;
    });
  }
  return loading;
}
