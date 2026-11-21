// What the student list card draws when the alt has no App thumbnail: a pure function of the alt that students.list
// returns, shared by the card and the generated default thumbnails (app/default-thumbnails.js), tested in Node.
import { spineBase } from "../core/api.js";
import { specialFraming } from "../detail/special-view.js";
import { configuredKey, previewBase, primaryKind } from "../mods/switch-sources.js";

/**
 * The generated picture of a student list card: the skeleton it renders and how.
 * @param {object} v The alt, as students.list gives it.
 * @param {object[]} alts The alts of its student.
 * @param {boolean} spine Whether Spine may be rendered.
 * @returns {{base: string, kind: string, memorialLobby: boolean, framing: string|null, background: *}|null} The
 *     picture, rendered with the "studentList" frame; null when the card shows the official art instead (no Spine,
 *     a chibi-only alt, no skeleton).
 */
export function listPicture(v, alts, spine) {
  if (!spine) return null;
  const kind = primaryKind(v);
  // Chibi-only entries stay navigable without requesting a Spine skeleton for a GLB.
  if (kind === "chibi") return null;
  let base = null;
  if (v.previewSources?.[kind] || configuredKey(v, kind)) base = previewBase(v, kind, false, alts);
  else {
    const core = kind === "portrait" ? v.portrait : v.memorialLobby;
    base = core ? spineBase(kind, core) : null;
  }
  return base
    ? { base, kind, memorialLobby: kind === "memorialLobby", framing: specialFraming(v), background: v.background }
    : null;
}
