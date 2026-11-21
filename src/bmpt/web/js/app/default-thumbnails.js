// The generated default thumbnails, once after an upgrade (webui/thumbnail_defaults.py): every alt still on its
// default thumbnail gets the student list card's picture as its App thumbnail and on the game's Collection portrait.
// Background work (class 4): it starts after the release notes, renders one picture at a time through the shared
// thumbnail queue (Preview Cache) while no preview is in the foreground, and hands the writes to one background job.
import { listPicture } from "../student-list/list-picture.js";

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

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

/**
 * @typedef {object} DefaultsOptions
 * @property {(method: string, params?: object) => Promise<*>} rpc The RPC call.
 * @property {() => Promise<{students: object[]}>} students The students.list answer the student list uses.
 * @property {(picture: object) => Promise<string|null>} render Renders a card's picture ("studentList" frame);
 *     a blob URL, or null when it failed.
 * @property {(url: string) => Promise<string>} [png] Turns a rendered picture into a PNG data URL.
 * @property {() => boolean} spine Whether Spine may be rendered.
 * @property {() => boolean} busy Whether a preview is in the foreground: no new picture starts meanwhile.
 * @property {(ms: number) => Promise<void>} [wait] Waits between two checks of busy().
 */

/**
 * Render, store and apply the generated default thumbnails when the server says they are due.
 * @param {DefaultsOptions} options The collaborators.
 * @returns {Promise<string|null>} The applying job's id; null when nothing was due or submitted.
 */
export async function applyDefaultThumbnails({ rpc, students, render, png = pngOf, spine, busy, wait = sleep }) {
  const listed = await rpc("thumbnails.defaults");
  if (!listed?.due || !spine()) return null;
  const ready = [];
  let complete = true;
  for (const { alt, builtin, picture } of shownPortraits(listed.candidates || [], await students(), true)) {
    if (builtin) {
      ready.push(alt);
      continue;
    }
    while (busy()) await wait(500);
    if (!spine()) {
      complete = false;
      break;
    }
    try {
      const url = await render(picture);
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
  return (await rpc("thumbnails.applyDefaults", { alts: ready, complete })) ?? null;
}
