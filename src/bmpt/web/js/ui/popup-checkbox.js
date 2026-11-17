// The popups' checkbox row (Btn_Checkbox, from the prefab or drawn from its numbers) and its wiring.
import * as NGUI from "./ngui.js";
import { SPACE, TYPE } from "./ui-tokens.js";
import { clone } from "../core/util.js";
import { row, textItem } from "./ui-grid.js";
import { node, sprite, find, label, layouts, hit } from "./popup.js";

// A checkbox row's height.
export const CHECK_H = 104;
// Btn_Checkbox drawn from its numbers (without the prefab): the plate, and the box with its tick in Type_ON,
// without in Type_OFF
export function checkboxPrefab() {
  const box = (ticked) =>
    node(
      "Check",
      [-120, 8],
      [sprite("Common_Bg_Raius10px", 74, 72, { incline: 10, depth: 25, color: "#e4f3f8/1.00" })],
      [
        node(
          "Line",
          [0, 0],
          [sprite("Common_Line_Raius10px", 74, 72, { incline: 10, depth: 30, color: "#96b6c2/1.00" })],
        ),
        ...(ticked ? [node("Icon", [0.4, -1.8], [sprite("Common_Icon_Check_B", 59, 58, { depth: 35 })])] : []),
      ],
    );
  return node(
    "Btn_Checkbox",
    [0, 0],
    [sprite("Resource_Bg_Radius10px", 798, 110, { incline: 10, pivot: 3, depth: 20 })],
    [node("Type_ON", [178, -3.2], [], [box(true)]), node("Type_OFF", [178, -3.2], [], [box(false)])],
  );
}
/**
 * Draw the gacha list's checkbox (Btn_Checkbox: its white plate, the box and its tick, a label after it); the
 * tick shows when on. Without the prefab, the same pieces are drawn from its numbers.
 * @param {number} maxW The widest the whole checkbox may be.
 * @returns {{tree: NguiNode, w: number, on: boolean, caption: object}} The node, its width, the state and the
 *     fitted caption.
 */
export function checkbox(name, x, y, caption, on, maxW) {
  const fit = NGUI.fitText(caption, TYPE.body.fs, TYPE.body.min, maxW - 131 - SPACE.xl, 2, { maxH: CHECK_H });
  const f = textItem(NGUI, fit.text, { fs: fit.fs, min: fit.fs }, { maxW: maxW - 131 - SPACE.xl, lines: 2 });
  const parts = row(
    [
      { w: 131, h: CHECK_H },
      { w: f.w + SPACE.m, h: f.h },
    ],
    { gap: SPACE.m },
  );
  const w = parts.w; // The checkbox branch keeps its prefab check plate and its 131-unit label origin.
  const cb = layouts?.checkbox ? clone(layouts.checkbox) : checkboxPrefab();
  cb.name = name;
  cb.pos = [x, y];
  Object.assign(
    cb.widgets.find((x2) => "sprite" in x2),
    { w_: w, h_: CHECK_H },
  );
  // one state drawn (Type_ON's box with its tick); the tick is shown or hidden in place
  cb.children = cb.children.filter((c) => c.name === "Type_ON");
  for (const c of cb.children) {
    c.active = true;
    c.children = (c.children || []).filter((k) => k.name === "Check");
    const icon = find(c, "Check/Icon");
    if (icon) icon.active = true;
  }
  cb.children.push(
    node("Label", [131, 0], [label(f.text, f.fs, "#484e52/1.00", w - 131, f.h, { pivot: 3, depth: 31 })]),
  );
  return { tree: cb, w, on, caption: f };
}
/**
 * Wire a drawn checkbox: a click flips it (state.checked) and shows or hides the tick.
 * @param {HTMLElement} host The element to append the hit button to.
 * @param {RenderView} view What render() drew.
 * @param {string} path The checkbox node's path.
 * @param {{checked: boolean, label?: string}} state The checkbox's state, flipped on a click.
 * @param {(checked: boolean) => void} [onChange]
 * @returns {HTMLButtonElement|null}
 */
export function wireCheck(host, view, path, state, onChange) {
  const tick = view.nodes.get(`${path}/Type_ON/Check/Icon`)?.els[0];
  const show = () => {
    if (tick) tick.style.visibility = state.checked ? "" : "hidden";
  };
  show();
  const b = hit(
    host,
    view,
    path,
    () => {
      state.checked = !state.checked;
      show();
      b?.setAttribute("aria-checked", String(state.checked));
      onChange?.(state.checked);
    },
    { name: state.label, action: "check" },
  );
  if (b) {
    b.setAttribute("role", "checkbox");
    b.setAttribute("aria-checked", String(state.checked));
  }
  return b;
}
