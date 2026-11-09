// A greyed control's reason on hover, drawn as the game's description tooltip: UIDescriptionTooltip's Default box
// (Common_Tooltip_Bg) around the text in Label_Info's style, above the control (below it when the host has no room
// above), without its arrow (owner, 2026-11-08: the box alone).
import * as NGUI from "./ngui.js";
import { S } from "./util.js";

export const HINT_PREFAB = "UIDescriptionTooltip";
const GAP = 12; // units between the box's visible edge and the control
// Common_Tooltip_Bg's visible box inside its sprite rect (its soft shadow around it): left, top, right, bottom
export const BOX_INSET = { left: 25, top: 12, right: 25, bottom: 38 };

// the prefab's numbers: the box sprite, the text style and the text's room inside the box (BG's anchors: 46 above and
// 60 below the content; Label_Info sits 62 inside the box's left edge)
export function hintStyle(prefab) {
  const bg = NGUI.find(prefab, "Default/BG"), info = NGUI.find(prefab, "Label_Info");
  const box = bg.widgets.find((w) => w.sprite), label = info.widgets.find((w) => "text" in w);
  const padX = info.pos[0] - (bg.pos[0] - box.w_ / 2);
  return { box, fs: label.fontSize, color: label.color, padX, maxW: box.w_ - 2 * padX,
    padTop: box.anchors.topAnchor[1], padBottom: -box.anchors.bottomAnchor[1] };
}

// Where the hint goes for ``button`` ([left, top, width, height], stage px) in a host ``hostW`` x ``hostH`` px: the
// text's size and lines (two at most, down to 30 units), the box in units, its centre (the render origin, stage px) and
// the side ("top": above the control, "bottom": below it).
export function hintLayout(text, button, hostW, hostH, style) {
  const fit = NGUI.fitText(text, style.fs, Math.min(30, style.fs), style.maxW, 2);
  const lines = String(fit.text).split("\n");
  const textW = Math.max(...lines.map((line) => NGUI.glyphWidth(line, fit.fs)));
  const textH = fit.fs * NGUI.LINE_PITCH * lines.length;
  const w = Math.ceil(textW + 2 * style.padX), h = Math.ceil(textH + style.padTop + style.padBottom);
  const [l, t, bw, bh] = button, wpx = w * S;
  // the box's visible bottom (top) edge GAP above (below) the control: the box's centre, stage px
  const topCy = t - (GAP + h / 2 - BOX_INSET.bottom) * S;
  const side = topCy - (h / 2 - BOX_INSET.top) * S >= 0 || t + bh / 2 > hostH / 2 ? "top" : "bottom";
  const cy = side === "top" ? topCy : t + bh + (GAP + h / 2 - BOX_INSET.top) * S;
  const cx = Math.min(Math.max(l + bw / 2, wpx / 2), Math.max(wpx / 2, hostW - wpx / 2));
  return { fs: fit.fs, text: lines.join("\n"), textW, textH, w, h, origin: [cx, cy], side,
    textY: (style.padBottom - style.padTop) / 2 };
}

export function hintTree(layout, style) {
  const { box, color } = style;
  return NGUI.node("HoverHint", [0, 0], [], [
    NGUI.node("BG", [0, 0], [{ ...box, anchors: undefined, w_: layout.w, h_: layout.h }]),
    NGUI.node("Label_Info", [0, layout.textY], [NGUI.label(layout.text, layout.fs, color, Math.ceil(layout.textW) + 2,
      Math.ceil(layout.textH), { depth: 22 })]),
  ]);
}

// Where the hint of a control is drawn: the control's own layer, or, for a control inside a scrolling card list
// (popup.js scrollArea: .popup-cards around .popup-cards-inner, which clips), the layer around that list, so a card's
// hint is not cut off by the list's edges. Returns the host and the offset (px) from the control's layer to it.
export function hintHost(button) {
  let host = button.parentElement, dx = 0, dy = 0;
  while (host?.classList?.contains("popup-cards-inner") && host.parentElement?.parentElement) {
    const area = host.parentElement;
    dx += (parseFloat(area.style.left) || 0) - (area.scrollLeft || 0);
    dy += (parseFloat(area.style.top) || 0) - (area.scrollTop || 0);
    host = area.parentElement;
  }
  return { host, dx, dy };
}

// Show ``text`` by ``button`` while the pointer is over it (the button keeps the pointer: aria-disabled, not
// disabled). Nothing shows when the game's tooltip prefab cannot be read.
export function attachHint(button, text, { load = () => NGUI.layout(HINT_PREFAB) } = {}) {
  let shown = null, token = 0;
  const hide = () => { token++; shown?.remove(); shown = null; };
  button.addEventListener("pointerenter", async () => {
    hide();
    const my = token;
    let style;
    try { style = hintStyle(await load()); } catch (err) { console.error("Hover hint unavailable", err); return; }
    const { host, dx, dy } = hintHost(button);
    if (my !== token || !host || !button.isConnected) return;
    const [l, t, w, h] = NGUI.rect(button);
    const layout = hintLayout(text, [l + dx, t + dy, w, h], host.clientWidth || 1920, host.clientHeight || 1080, style);
    const layer = document.createElement("div");
    Object.assign(layer.style, { position: "absolute", left: "0", top: "0", pointerEvents: "none", zIndex: "200" });
    layer.className = "hover-hint";
    host.appendChild(layer);
    shown = layer;
    try { await NGUI.render(hintTree(layout, style), layer, { scale: S, origin: layout.origin, z: 200 }); }
    catch (err) { console.error("Hover hint failed", err); hide(); }
  });
  button.addEventListener("pointerleave", hide);
  // a list scrolled under the pointer moves the control away from its hint
  button.addEventListener("wheel", hide, { passive: true });
  button.addEventListener("pointerdown", hide);
  return hide;
}
