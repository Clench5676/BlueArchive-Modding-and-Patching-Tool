// Shared constants and small helpers for the screens.
import * as NGUI from "../ui/ngui.js";
import { stageCoverBleed } from "../window/window-layout.js";
import { reflowCorners } from "../window/corner-layout.js";

export const S = 1920 / 2960; // stage px per NGUI unit (the game fits 2960 units to the width)
export const TAN10 = Math.tan((10 * Math.PI) / 180); // the game's 10 degree incline
/** Deep-copy JSON data. */
export const clone = (o) => JSON.parse(JSON.stringify(o));
/** Find an element by id. */
export const $ = (id) => document.getElementById(id);

/**
 * The ink width of the widest line of a text.
 * @returns {number} The width in pixels.
 */
export const glyphsOf = (str, fs) => NGUI.glyphWidth(str, fs);
/**
 * The advance width of the widest line of a text.
 * @returns {number} The width in pixels.
 */
export const advanceOf = (str, fs) => NGUI.textWidth(str, fs);

/** Find a node of a prefab tree for editing, warning when it is missing. */
export function at(tree, path) {
  const n = NGUI.find(tree, path);
  if (!n) console.warn("missing node", path);
  return n;
}
/**
 * Switch nodes of a prefab tree off.
 * @param {...string} paths The paths of the nodes; missing ones are skipped with a warning.
 */
export const off = (tree, ...paths) => {
  for (const p of paths) {
    const n = at(tree, p);
    if (n) n.active = false;
  }
};
/**
 * Set the text of the first label widget on a node.
 * @param {object} [extra] Further fields to set on the widget.
 */
export function text(tree, path, value, extra = {}) {
  const n = at(tree, path);
  const w = n?.widgets.find((x) => "text" in x);
  if (w) Object.assign(w, { text: value }, extra);
}
/** Find the first sized widget on a node. */
export const widget = (tree, path) => at(tree, path)?.widgets.find((x) => "w_" in x);

/**
 * Break a path, or a short text ending in one, after its separators to fit a width.
 * The separators are slashes, spaces, dashes and underscores of bundle names; a part longer than a line is
 * broken between characters, never cut. The size is the largest from fsMax down to fsMin that needs at most
 * maxLines lines.
 * @param {number} fsMax The largest font size to try.
 * @param {number} fsMin The smallest font size to try.
 * @returns {{text: string, fs: number, lines: number}} The text with its line breaks, the size and the line
 *     count.
 */
export function wrapPath(path, fsMax, fsMin, maxW, maxLines = 3) {
  const pieces = String(path ?? "").split(/(?<=[\\/ _-])/);
  let best = null;
  for (let fs = fsMax; fs >= fsMin; fs -= 2) {
    // a piece wider than a line at this size is broken between characters
    const parts = pieces.flatMap((piece) => {
      if (glyphsOf(piece.trimEnd(), fs) <= maxW) return [piece];
      const out = [];
      let cur = "";
      for (const ch of piece) {
        if (cur && glyphsOf(cur + ch, fs) > maxW) {
          out.push(cur);
          cur = ch;
        } else cur += ch;
      }
      return cur ? [...out, cur] : out;
    });
    const lines = [];
    let cur = "";
    for (const part of parts) {
      if (cur && glyphsOf((cur + part).trimEnd(), fs) > maxW) {
        lines.push(cur.trimEnd());
        cur = part;
      } else cur += part;
    }
    if (cur) lines.push(cur.trimEnd());
    best = { text: lines.join("\n"), fs, lines: lines.length };
    if (lines.length <= maxLines) return best;
  }
  return best;
}

/**
 * Put an invisible button over a rendered widget.
 * @param {number[]} rect [left, top, width, height] in stage pixels.
 */
export function hitButton(host, [l, t, w, h], fn, name, z = 50) {
  const b = document.createElement("button");
  b.className = "hit";
  if (name) b.setAttribute("aria-label", name);
  Object.assign(b.style, { left: `${l}px`, top: `${t}px`, width: `${w}px`, height: `${h}px`, zIndex: z });
  b.addEventListener("click", fn);
  host.appendChild(b);
  return b;
}

// recruitable marker: the game's recruitment (gacha) icon from the memorialLobby
export const RECRUIT = { sprite: "Common_Icon_Gacha2", atlas: "Common", ratio: 143 / 126 };
/**
 * The recruitment icon as a node.
 * @param {number[]} pos Local position [x, y] in NGUI units.
 */
export const recruitIcon = (pos, h, depth = 15) =>
  NGUI.node("Recruit", pos, [
    NGUI.sprite(RECRUIT.sprite, h / RECRUIT.ratio, h, { atlas: RECRUIT.atlas, type: 0, depth }),
  ]);

/**
 * A label, optionally led by the recruit icon, centred as one group around x = 0; it shrinks and wraps to fit.
 * @param {object} options
 * @param {number} options.maxW The width the group may take.
 * @param {boolean} [options.mark=false] Lead the text with the recruit icon.
 * @returns {{nodes: NguiNode[], fs: number, lines: number}} The nodes to place, the font size used and the line
 *     count.
 */
export function markedLabel(caption, { fs = 40, fsMin = 26, color, maxW, mark = false, depth = 14, maxLines = 2 }) {
  const iconH = fs * 1.35,
    iconW = mark ? iconH / RECRUIT.ratio + 6 : 0;
  const f = NGUI.fitText(caption, fs, fsMin, maxW - iconW, maxLines);
  const tw = glyphsOf(f.text, f.fs),
    adv = Math.max(...f.text.split("\n").map((l) => advanceOf(l, f.fs)));
  const left = -(iconW + tw) / 2;
  const textNode = NGUI.node(
    "Text",
    [left + iconW + adv / 2, 0],
    [NGUI.label(f.text, f.fs, color, adv + 2, f.fs * 1.2 * f.lines, { depth })],
  );
  return {
    nodes: mark ? [recruitIcon([left + iconW / 2 - 3, 0], iconH), textNode] : [textNode],
    fs: f.fs,
    lines: f.lines,
  };
}

/**
 * The game's red "new" dot as a node.
 * @param {number[]} pos Local position [x, y] in NGUI units.
 */
export const DOT = (pos, size = 34) =>
  NGUI.node("Dot", pos, [NGUI.sprite("Common_Icon_Reddot2", size, (size * 31) / 34, { type: 0, depth: 45 })]);

/** Scale the stage to the window and place the top bar, so the 1920 x 1080 stage fills the page. */
export function fitStage() {
  const stage = document.getElementById("stage"),
    header = globalThis.bamtHeaderGeometry;
  const scale = header?.contentScale ?? Math.min(innerWidth / 1920, innerHeight / 1080);
  stage.style.setProperty("--scale", scale);
  // hidden in app.css until the first fit: no unscaled stage on the black start screen
  stage.style.visibility = "visible";
  stage.style.setProperty("--cover-bleed", `${stageCoverBleed(scale, globalThis.devicePixelRatio || 1)}px`);
  if (header) stage.style.top = `calc(50% + ${header.extra / 2}px)`;
  else stage.style.removeProperty("top");
  const bar = document.querySelector?.(".topbar");
  if (bar && !header) {
    const box = stage.getBoundingClientRect(),
      scale = box.width / 1920;
    Object.assign(bar.style, {
      left: `${-box.left / scale}px`,
      top: `${-box.top / scale}px`,
      width: `${innerWidth / scale}px`,
    });
  } else if (bar) {
    bar.style.removeProperty("left");
    bar.style.removeProperty("top");
  }
  reflowCorners();
}
