// The generated default thumbnails, once after an upgrade (webui/thumbnail_defaults.py): every alt still on its
// default thumbnail gets the student list card's picture as its App thumbnail and on the game's Collection portrait.
// Start-up work (class 3) under the start cover, before the first screen: the pictures are rendered one at a time
// through the shared thumbnail queue (Preview Cache), then one start-up job writes them, each with its progress line.
import { T } from "../core/i18n.js";
import { listPicture } from "../student-list/list-picture.js";

// the longest one picture may take; a slower one is skipped (the run is then incomplete and tried again next start)
export const PICTURE_CAP = 20000;

/**
 * A rendered picture as a PNG data URL, drawn the way the thumbnail capture draws its generated default.
 * @param {string} url The picture's blob URL.
 * @returns {Promise<string>} The PNG as a data URL.
 */
export async function pngOf(url) {
  const img = new Image();
  img.src = url;
  await img.decode();
  const canvas = document.createElement("canvas");
  canvas.width = img.naturalWidth;
  canvas.height = img.naturalHeight;
  canvas.getContext("2d").drawImage(img, 0, 0);
  return canvas.toDataURL("image/png");
}

/**
 * The candidates whose student list card shows a generated portrait, with that picture.
 * @param {{alt: string, builtin: string|null}[]} candidates The server's candidates.
 * @param {{students: object[]}} data The students.list answer.
 * @param {boolean} spine Whether Spine may be rendered.
 * @returns {{alt: string, builtin: string|null, picture: object}[]} The candidates to apply, in order.
 */
export function shownPortraits(candidates, data, spine) {
  const students = new Map((data?.students || []).map((f) => [f.key, f]));
  const alts = new Map((data?.students || []).flatMap((f) => f.alts.map((v) => [v.key, v])));
  return candidates.flatMap((candidate) => {
    const v = alts.get(candidate.alt);
    const picture = v && listPicture(v, students.get(v.student)?.alts || [], spine);
    return picture?.kind === "portrait" ? [{ ...candidate, picture }] : [];
  });
}

// a render that has not settled within cap ms counts as failed
function capped(work, cap) {
  let timer = null;
  const late = new Promise((resolve) => {
    timer = setTimeout(() => resolve(null), cap);
  });
  return Promise.race([work, late]).finally(() => clearTimeout(timer));
}

/**
 * @typedef {object} DefaultsOptions
 * @property {(method: string, params?: object) => Promise<*>} rpc The RPC call.
 * @property {(id: string, update: (progress: object) => void) => Promise<*>} waitJob Waits for a job's result,
 *     passing its progress on.
 * @property {() => Promise<{students: object[]}>} students The students.list answer the student list uses.
 * @property {(picture: object) => Promise<string|null>} render Renders a card's picture ("studentList" frame);
 *     a blob URL, or null when it failed.
 * @property {(url: string) => Promise<string>} [png] Turns a rendered picture into a PNG data URL.
 * @property {() => boolean} spine Whether Spine may be rendered.
 * @property {(run: (update: (progress: object) => void) => Promise<*>) => Promise<*>} [task] Runs the work under
 *     the start cover with its progress line (Loading.task); it is only called when something is due.
 * @property {number} [cap] The longest one picture may take, in ms.
 */

/**
 * Render, store and apply the generated default thumbnails when the server says they are due.
 * @param {DefaultsOptions} options The collaborators.
 * @returns {Promise<object|null>} The last job's result; null when nothing was due or written.
 */
export async function applyDefaultThumbnails({
  rpc,
  waitJob,
  students,
  render,
  png = pngOf,
  spine,
  task = (run) => run(() => {}),
  cap = PICTURE_CAP,
}) {
  const listed = await rpc("thumbnails.defaults");
  if (!listed?.due || !spine()) return null;
  const shown = shownPortraits(listed.candidates || [], await students(), true);
  return task(async (update) => {
    const ready = shown.filter((item) => item.builtin).map((item) => item.alt);
    const todo = shown.filter((item) => !item.builtin);
    let complete = true;
    for (const [index, { alt, picture }] of todo.entries()) {
      update({ label: T("progress.defaultThumbnails"), current: index + 1, total: todo.length });
      try {
        const url = await capped(render(picture), cap);
        if (!url) {
          complete = false;
          continue;
        }
        await rpc("thumbnails.storeDefault", { alt, png: await png(url) });
        ready.push(alt);
      } catch (err) {
        console.warn("default thumbnail", alt, err);
        complete = false;
      }
    }
    const job = await rpc("thumbnails.applyDefaults", { alts: ready, complete });
    let result = job ? await waitJob(job, update) : null;
    // a job of a higher class went first: the rest runs in the job that took over
    while (result?.next) result = await waitJob(result.next, update);
    return result;
  });
}
