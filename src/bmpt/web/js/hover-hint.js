// A greyed control's reason on hover, drawn as the game's description tooltip: UIDescriptionTooltip's Default box
// (Common_Tooltip_Bg, Common_Tooltip_Arrow) around the text in Label_Info's style. The renderer draws no rotation, so
// the arrow is one of the prefab's side arrows: the box sits beside the control on the side with room, the left arrow
// pointing left as drawn, the right one flipped.
import * as NGUI from "./ngui.js";
import { S } from "./util.js";

export const HINT_PREFAB = "UIDescriptionTooltip";
const GAP = 12; // units between the arrow's tip and the control

// the prefab's numbers: the box sprite, the side arrow, the text style, and the text's room inside the box (BG's
// anchors: 46 above and 60 below the content; Label_Info sits 62 inside the box's left edge)
export function hintStyle(prefab) {
  const bg = NGUI.find(prefab, "Default/BG"), arrow = NGUI.find(prefab, "Default/BG/Arrow_L"), info = NGUI.find(prefab, "Label_Info");
  const box = bg.widgets.find((w) => w.sprite), tip = arrow.widgets.find((w) => w.sprite), label = info.widgets.find((w) => "text" in w);
  const padX = info.pos[0] - (bg.pos[0] - box.w_ / 2);
  return { box, tip, fs: label.fontSize, color: label.color, padX, maxW: box.w_ - 2 * padX,
    padTop: box.anchors.topAnchor[1], padBottom: -box.anchors.bottomAnchor[1],
    tipOut: -tip.anchors.leftAnchor[1], tipIn: tip.anchors.rightAnchor[1] };
}

// Where the hint goes beside ``button`` ([left, top, width, height], stage px) in a host ``hostW`` x ``hostH`` px:
// the text's size and lines (two at most, down to 30 units), the box in units, its centre (the render origin, stage px),
// the side the box is on and the arrow's x in units from the box's centre.
export function hintLayout(text, button, hostW, hostH, style) {
  const fit = NGUI.fitText(text, style.fs, Math.min(30, style.fs), style.maxW, 2);
  const lines = String(fit.text).split("\n");
  const textW = Math.max(...lines.map((line) => NGUI.glyphWidth(line, fit.fs)));
  const textH = fit.fs * NGUI.LINE_PITCH * lines.length;
  const w = Math.ceil(textW + 2 * style.padX), h = Math.ceil(textH + style.padTop + style.padBottom);
  const [l, t, bw, bh] = button, reach = (style.tipOut + GAP) * S, wpx = w * S, hpx = h * S;
  const rightRoom = hostW - (l + bw + reach), leftRoom = l - reach;
  const side = rightRoom >= wpx || rightRoom >= leftRoom ? "right" : "left";
  let cx = side === "right" ? l + bw + reach + wpx / 2 : l - reach - wpx / 2;
  cx = Math.min(Math.max(cx, wpx / 2), Math.max(wpx / 2, hostW - wpx / 2));
  const cy = Math.min(Math.max(t + bh / 2, hpx / 2), Math.max(hpx / 2, hostH - hpx / 2));
  const tipX = side === "right" ? -w / 2 + (style.tipIn - style.tipOut) / 2 : w / 2 - (style.tipIn - style.tipOut) / 2;
  // the arrow stays on the control's level while the box is held inside the host
  const tipY = Math.max(-h / 2 + style.tip.h_ / 2, Math.min(h / 2 - style.tip.h_ / 2, (cy - (t + bh / 2)) / S));
  return { fs: fit.fs, text: lines.join("\n"), textW, textH, w, h, origin: [cx, cy], side, tipX, tipY,
    textY: (style.padBottom - style.padTop) / 2 };
}

export function hintTree(layout, style) {
  const { box, tip, color } = style;
  return NGUI.node("HoverHint", [0, 0], [], [
    NGUI.node("BG", [0, 0], [{ ...box, anchors: undefined, w_: layout.w, h_: layout.h }]),
    NGUI.node(layout.side === "right" ? "Arrow_L" : "Arrow_R", [layout.tipX, layout.tipY],
      [{ ...tip, anchors: undefined, flip: layout.side === "right" ? 0 : 1 }]),
    NGUI.node("Label_Info", [0, layout.textY], [NGUI.label(layout.text, layout.fs, color, Math.ceil(layout.textW) + 2,
      Math.ceil(layout.textH), { depth: 22 })]),
  ]);
}

// Show ``text`` beside ``button`` while the pointer is over it (the button keeps the pointer: aria-disabled, not
// disabled). Nothing shows when the game's tooltip prefab cannot be read.
export function attachHint(button, text, { load = () => NGUI.layout(HINT_PREFAB) } = {}) {
  let shown = null, token = 0;
  const hide = () => { token++; shown?.remove(); shown = null; };
  button.addEventListener("pointerenter", async () => {
    hide();
    const my = token;
    let style;
    try { style = hintStyle(await load()); } catch (err) { console.error("Hover hint unavailable", err); return; }
    const host = button.parentElement;
    if (my !== token || !host || !button.isConnected) return;
    const layout = hintLayout(text, NGUI.rect(button), host.clientWidth || 1920, host.clientHeight || 1080, style);
    const layer = document.createElement("div");
    Object.assign(layer.style, { position: "absolute", left: "0", top: "0", pointerEvents: "none", zIndex: "200" });
    layer.className = "hover-hint";
    host.appendChild(layer);
    shown = layer;
    try { await NGUI.render(hintTree(layout, style), layer, { scale: S, origin: layout.origin, z: 200 }); }
    catch (err) { console.error("Hover hint failed", err); hide(); }
  });
  button.addEventListener("pointerleave", hide);
  button.addEventListener("pointerdown", hide);
  return hide;
}
