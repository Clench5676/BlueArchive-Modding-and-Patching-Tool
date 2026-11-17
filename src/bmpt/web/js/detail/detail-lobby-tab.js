// The detail screen's Memorial Lobby tab: the lobby on the left, its lines, and its still for the switch card.
import { isSpecial, specialFraming } from "./special-view.js";
import { ensureSpine } from "../spine/spine-runtime.js";
import * as Lobby from "../lobby/lobby.js";
import { rpc } from "../core/api.js";
import { language } from "../core/i18n.js";
import { $ } from "../core/util.js";
import {
  variantKey,
  fam,
  tab,
  fullscreen,
  lobbyShot,
  leftToken,
  LINES_CAP,
  sleep,
  frames,
  renderFailed,
  open,
  thumb,
  baseOf,
  current,
  lobbyFrom,
  lobbyStale,
  setLobbyFrom,
  setLobbyStale,
  setLobbyShot,
} from "./detail.js";
import { renderScreen } from "./detail-screen.js";
import { renderExpressions } from "./detail-strip.js";
import { modsOn } from "./detail-switchers.js";

// the UI language -> its key in the lobby lines
const LINE_LANG = { "zh-CN": "zh-CN", "zh-TW": "zh-TW", "en-US": "en", "ja-JP": "ja", "ko-KR": "ko", "th-TH": "th" };
// the memorial lobby: every layer in and drawn, the still for the switch card, the dialogue strip; the lines when
// they come in time (later ones re-render the strip)
export async function showLobby(v, my, alive = () => true) {
  const still = () => alive() && open && my === leftToken && tab === "lobby" && variantKey === v.key;
  if (!(await ensureSpine())) {
    if (still()) Lobby.hide($("lobby"));
    return;
  }
  if (!still()) return;
  if (lobbyFrom !== fam.version || lobbyStale) Lobby.hide($("lobby"));
  setLobbyFrom(fam.version);
  setLobbyStale(false);
  const c = current("lobby");
  const options = {
    framing: specialFraming(v),
    mod: c.kind === "mod" && (!!v.previewSources?.lobby || !!v.configured || !modsOn() || !v.lobby) ? c.id : null,
    camera: !!c.mod?.camera,
    official: c.kind === "self" && (!!v.configured || !!v.previewSources?.lobby || !modsOn()),
  };
  let m = await Lobby.show(v.lobby, $("lobby"), v.key, options);
  if (!still()) return;
  // A cancelled attempt is not a ready preview. The newest detail render owns one fresh attempt.
  if (!m) m = await Lobby.show(v.lobby, $("lobby"), v.key, options);
  if (!still()) return;
  if (!m) throw new Error(`Lobby load superseded without a replacement: ${v.lobby}`);
  if (fullscreen) {
    Lobby.frame("full");
    Lobby.interactive(true);
  }
  const lines = refreshLobbyLines(v, m, still);
  // the still is taken in the left framing, once the players have drawn a few frames of it; while the intro plays (its
  // flash and scenes are not the lobby at rest), once it has ended
  await frames(4);
  if (!still()) return;
  if (Lobby.introPending()) {
    Lobby.introDone()
      .then(() => frames(4))
      .then(() => {
        if (!still() || lobbyShot || fullscreen) return;
        setLobbyShot(snapshotLobby());
        if (lobbyShot) return renderScreen();
      })
      .catch(renderFailed);
  } else if (!lobbyShot && !fullscreen) setLobbyShot(snapshotLobby());
  await Promise.all([renderScreen(), renderExpressions(), Promise.race([lines, sleep(LINES_CAP)])]);
}

let linesRevision = 0;
export function refreshLobbyLines(v, meta, alive = () => true) {
  const my = ++linesRevision,
    lang = language(),
    key = LINE_LANG[lang] || "en";
  const still = () =>
    my === linesRevision &&
    alive() &&
    open &&
    tab === "lobby" &&
    variantKey === v.key &&
    language() === lang &&
    Lobby.meta() === meta;
  const talks = Object.fromEntries(meta.sequences.map((sq) => [sq.anim, sq.cues.map((cue) => cue.clip)]));
  // Change the subtitle picker immediately, retaining voice metadata while the
  // next language's table is fetched. Pending voice cues must keep playing.
  Lobby.setLines(undefined, (line) => line?.[key] || "");
  return (isSpecial(v) ? Promise.resolve({}) : rpc("lobby.lines", { variant: v.key, talks, lang: key }))
    .then((table) => {
      if (!still()) return;
      Lobby.setLines(table, (line) => line?.[key] || "");
      return renderExpressions();
    })
    .catch((err) => {
      console.error("Lobby lines failed", err);
    });
}

// the lobby's switch card: a camera choice shows the view its camera keeps (the still of its Switch Memorial Lobby
// card); any other lobby the still below
export function lobbyCard(v) {
  const c = current("lobby");
  return c?.mod?.camera
    ? thumb(baseOf(c, "lobby"), "landscape", null, true, specialFraming(v), (c.origin || v).bg)
    : lobbyShot;
}
// a still of the lobby for the switch card, framed on the face: lobby.js picks the crop inside the left view, which
// lies inside the lobby's painted area, so the still is opaque everywhere and the card's mask fades it out softly
export function snapshotLobby() {
  const canvases = [...$("lobby").querySelectorAll(".lobby-view .lobby-layer:not(.lobby-off) canvas")];
  if (!canvases.length) return null;
  const W = canvases[0].width,
    H = canvases[0].height;
  const r = Lobby.stillRect(W, H) || { x: W * 0.1, y: H * 0.16, w: W * 0.5, h: W * 0.5 * (253 / 450) };
  const c = document.createElement("canvas");
  c.width = 900;
  c.height = Math.round(900 * (253 / 450));
  const g = c.getContext("2d");
  g.imageSmoothingQuality = "high";
  for (const src of canvases) g.drawImage(src, r.x, r.y, r.w, r.h, 0, 0, c.width, c.height);
  return new Promise((resolve) => c.toBlob((b) => resolve(b ? URL.createObjectURL(b) : null)));
}
