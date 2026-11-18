// The detail screen's Memorial Lobby tab: the memorial lobby on the left, its lines, and its still for the switch card.
import { isSpecial, specialFraming } from "./special-view.js";
import { ensureSpine } from "../spine/spine-runtime.js";
import * as MemorialLobby from "../memorial-lobby/memorial-lobby.js";
import { rpc } from "../core/api.js";
import { language } from "../core/i18n.js";
import { $ } from "../core/util.js";
import {
  altId,
  shownStudent,
  tab,
  fullscreen,
  memorialLobbyShot,
  leftToken,
  LINES_CAP,
  sleep,
  frames,
  renderFailed,
  open,
  thumb,
  baseOf,
  current,
  memorialLobbyFrom,
  memorialLobbyStale,
  setMemorialLobbyFrom,
  setMemorialLobbyStale,
  setMemorialLobbyShot,
} from "./detail.js";
import { renderScreen } from "./detail-screen.js";
import { renderExpressions } from "./detail-strip.js";
import { modsOn } from "./detail-switchers.js";

// the UI language -> its key in the memorial lobby lines
const LINE_LANG = { "zh-CN": "zh-CN", "zh-TW": "zh-TW", "en-US": "en", "ja-JP": "ja", "ko-KR": "ko", "th-TH": "th" };
// the memorial lobby: every layer in and drawn, the still for the switch card, the dialogue strip; the lines when
// they come in time (later ones re-render the strip)
export async function showMemorialLobby(v, my, alive = () => true) {
  const still = () => alive() && open && my === leftToken && tab === "memorialLobby" && altId === v.key;
  if (!(await ensureSpine())) {
    if (still()) MemorialLobby.hide($("memorial-lobby"));
    return;
  }
  if (!still()) return;
  if (memorialLobbyFrom !== shownStudent.version || memorialLobbyStale) MemorialLobby.hide($("memorial-lobby"));
  setMemorialLobbyFrom(shownStudent.version);
  setMemorialLobbyStale(false);
  const c = current("memorialLobby");
  const options = {
    framing: specialFraming(v),
    mod:
      c.kind === "mod" && (!!v.previewSources?.memorialLobby || !!v.configured || !modsOn() || !v.memorialLobby)
        ? c.id
        : null,
    camera: !!c.mod?.camera,
    official: c.kind === "self" && (!!v.configured || !!v.previewSources?.memorialLobby || !modsOn()),
  };
  let m = await MemorialLobby.show(v.memorialLobby, $("memorial-lobby"), v.key, options);
  if (!still()) return;
  // A cancelled attempt is not a ready preview. The newest detail render owns one fresh attempt.
  if (!m) m = await MemorialLobby.show(v.memorialLobby, $("memorial-lobby"), v.key, options);
  if (!still()) return;
  if (!m) throw new Error(`MemorialLobby load superseded without a replacement: ${v.memorialLobby}`);
  if (fullscreen) {
    MemorialLobby.frame("full");
    MemorialLobby.interactive(true);
  }
  const lines = refreshMemorialLobbyLines(v, m, still);
  // the still is taken in the left framing, once the players have drawn a few frames of it; while the intro plays (its
  // flash and scenes are not the memorial lobby at rest), once it has ended
  await frames(4);
  if (!still()) return;
  if (MemorialLobby.introPending()) {
    MemorialLobby.introDone()
      .then(() => frames(4))
      .then(() => {
        if (!still() || memorialLobbyShot || fullscreen) return;
        setMemorialLobbyShot(snapshotMemorialLobby());
        if (memorialLobbyShot) return renderScreen();
      })
      .catch(renderFailed);
  } else if (!memorialLobbyShot && !fullscreen) setMemorialLobbyShot(snapshotMemorialLobby());
  await Promise.all([renderScreen(), renderExpressions(), Promise.race([lines, sleep(LINES_CAP)])]);
}

let linesRevision = 0;
export function refreshMemorialLobbyLines(v, meta, alive = () => true) {
  const my = ++linesRevision,
    lang = language(),
    key = LINE_LANG[lang] || "en";
  const still = () =>
    my === linesRevision &&
    alive() &&
    open &&
    tab === "memorialLobby" &&
    altId === v.key &&
    language() === lang &&
    MemorialLobby.meta() === meta;
  const talks = Object.fromEntries(meta.sequences.map((sq) => [sq.anim, sq.cues.map((cue) => cue.clip)]));
  // Change the subtitle picker immediately, retaining voice metadata while the
  // next language's table is fetched. Pending voice cues must keep playing.
  MemorialLobby.setLines(undefined, (line) => line?.[key] || "");
  return (isSpecial(v) ? Promise.resolve({}) : rpc("memorialLobby.lines", { alt: v.key, talks, lang: key }))
    .then((table) => {
      if (!still()) return;
      MemorialLobby.setLines(table, (line) => line?.[key] || "");
      return renderExpressions();
    })
    .catch((err) => {
      console.error("MemorialLobby lines failed", err);
    });
}

// the memorial lobby's switch card: a camera choice shows the view its camera keeps (the still of its Switch
// Memorial Lobby card); any other memorial lobby the still below
export function memorialLobbyCard(v) {
  const c = current("memorialLobby");
  return c?.mod?.camera
    ? thumb(baseOf(c, "memorialLobby"), "landscape", null, true, specialFraming(v), (c.origin || v).background)
    : memorialLobbyShot;
}
// a still of the memorial lobby for the switch card, framed on the face: memorial-lobby.js picks the crop inside the
// left view, which lies inside the memorial lobby's painted area, so the still is opaque everywhere and the card's mask
// fades it out softly
export function snapshotMemorialLobby() {
  const canvases = [
    ...$("memorial-lobby").querySelectorAll(
      ".memorial-lobby-view .memorial-lobby-layer:not(.memorial-lobby-off) canvas",
    ),
  ];
  if (!canvases.length) return null;
  const W = canvases[0].width,
    H = canvases[0].height;
  const r = MemorialLobby.stillRect(W, H) || { x: W * 0.1, y: H * 0.16, w: W * 0.5, h: W * 0.5 * (253 / 450) };
  const c = document.createElement("canvas");
  c.width = 900;
  c.height = Math.round(900 * (253 / 450));
  const g = c.getContext("2d");
  g.imageSmoothingQuality = "high";
  for (const src of canvases) g.drawImage(src, r.x, r.y, r.w, r.h, 0, 0, c.width, c.height);
  return new Promise((resolve) => c.toBlob((b) => resolve(b ? URL.createObjectURL(b) : null)));
}
