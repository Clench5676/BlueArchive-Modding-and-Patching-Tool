// Freeze one complete real frame, restore playback, and persist it without a review modal.
function notify(callback, ...args) {
  try {
    Promise.resolve(callback?.(...args)).catch((error) => console.warn("Photography feedback failed", error));
  } catch (error) {
    console.warn("Photography feedback failed", error);
  }
}
/**
 * Take one picture: pause the scene, draw it until a complete real frame can be captured (up to 60 attempts,
 * each waiting a little longer), restore playback and save the picture without a review modal.
 * @param {object} options
 * @param {object} options.bridge The detail screen's hooks: pause, expression, action, info and redraw.
 * @param {"portrait"|"lobby"} options.mode What is photographed.
 * @param {boolean} options.paused Whether the scene was paused before; the pause is put back to this.
 * @param {() => Promise<{png: Blob}>} options.capture Takes the picture; throws code capture_not_ready until
 *     the renderer is ready.
 * @param {(ms: number) => Promise<void>} options.wait Waits between attempts.
 * @param {(payload: object) => Promise<object>} options.save Saves the picture with its pose.
 * @param {(shot: object) => void} [options.onCaptured] Called once the picture is taken (failures only logged).
 * @param {(result: object, payload: object) => void} [options.onSaved] Called once it is saved.
 * @param {() => boolean} [options.alive] False ends the shot with an AbortError.
 * @returns {Promise<object>}
 */
export async function photograph({
  bridge,
  mode,
  paused,
  capture,
  wait,
  save,
  onCaptured,
  onSaved,
  alive = () => true,
}) {
  const check = () => {
    if (!alive()) throw new DOMException("Photography ended", "AbortError");
  };
  check();
  bridge.pause(true);
  const pose = {
    mode,
    expression: mode === "portrait" ? bridge.expression() : "",
    action: bridge.action() || "",
    source: bridge.info().source || "",
  };
  let payload;
  try {
    let shot;
    for (let attempt = 0; attempt < 60; attempt++) {
      try {
        check();
        await wait(attempt ? Math.min(250, attempt * 25) : 0);
        check();
        await bridge.redraw();
        check();
        shot = await capture();
        check();
        break;
      } catch (error) {
        if (error.code !== "capture_not_ready") throw error;
        if (attempt === 59)
          throw Object.assign(new Error("Renderer did not recover"), { code: "photo_capture_unavailable" });
      }
    }
    payload = { png: shot.png, ...pose };
    notify(onCaptured, shot);
  } finally {
    bridge.pause(paused);
  }
  check();
  const result = await save(payload);
  check();
  notify(onSaved, result, payload);
  return result;
}
