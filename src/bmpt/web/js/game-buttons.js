// Complete option and disabled button branches; captions and dimensions are the only substitutions.
import * as NGUI from "./ngui.js";
import { resizeButton } from "./button-layout.js";
import { CONTROL, SPACE, TYPE, SMALL_BUTTON } from "./ui-tokens.js";
import { clone } from "./util.js";

let layouts, loading;
export function ready() {
  return loading ||= Promise.all([NGUI.layout("UIPopup_Option_PC"), NGUI.layout("UIPopup_FilterSort")])
    .then(([option, filter]) => { layouts = { option, filter }; })
    .catch((err) => { loading = null; throw err; });
}

export function disabledButton(filter, original) {
  const tree = clone(NGUI.find(filter, "Center/Popup/Btn_Section/Btn_Disable"));
  tree.name = original.name;
  tree.pos = [...original.pos];
  tree.scale = [...original.scale];
  tree.active = true;
  const body = NGUI.find(tree, "BG (1)"), oldBody = NGUI.find(original, "BG (1)") || original;
  const size = oldBody.widgets.find((w) => "sprite" in w);
  resizeButton(body, size.w_, size.h_);
  body.pos = oldBody !== original ? [...oldBody.pos] : [0, 0];
  for (const w of tree.widgets) if ("w_" in w) Object.assign(w, { w_: size.w_, h_: size.h_ });
  const oldLabel = NGUI.find(original, "Label_Locale"), caption = NGUI.find(tree, "Label_Locale");
  if (oldLabel) {
    const color = caption.widgets[0].color;
    caption.pos = [...oldLabel.pos];
    Object.assign(caption.widgets[0], oldLabel.widgets[0], { color });
  }
  return tree;
}

export const disable = (original) => disabledButton(layouts.filter, original);

// The popups' light-blue button (ETCSetting/ButtonBg): 402 x 136, caption 48 down to 34 fitted to the width less 60, its label
// box 30 less across and 20 less down. Callers vary the width (240 at least) and pass their own sizes where a screen has
// its own (the Hall's boxes).
export function optionButton(option, filter, name, x, y, caption, { w = SMALL_BUTTON.w, h = SMALL_BUTTON.h, enabled = true, fs = SMALL_BUTTON.fs, min = SMALL_BUTTON.fsMin, depth = 10, semantic = "secondary" } = {}) {
  let tree = clone(NGUI.find(option, "Center/Popup/List/Sound_Setting/ETCSetting/ButtonBg"));
  tree.name = name;
  tree.pos = [x, y];
  resizeButton(tree, w, h);
  const label = NGUI.find(tree, "Label_Locale");
  NGUI.fitLabel(label.widgets[0], caption, fs, min, w - SMALL_BUTTON.fitInset);
  label.widgets[0].w_ = w - SMALL_BUTTON.boxInsetW;
  label.widgets[0].h_ = h - SMALL_BUTTON.boxInsetH;
  if (!enabled) tree = disabledButton(filter, tree);
  const shift = (n, d = 0) => {
    for (const widget of n.widgets) if ("sprite" in widget || "text" in widget) widget.depth = depth + ("text" in widget ? 2 : d);
    for (const child of n.children || []) shift(child, d + 1);
  };
  tree.actionSemantic=enabled?semantic:"secondary";
  shift(tree);
  return tree;
}

export const smallButton = (...args) => optionButton(layouts.option, layouts.filter, ...args);

// A complete Sort/Btn_Ok in a DOM flow (preview licence prompts); plain text remains available without assets.
export async function skinPrimary(button, caption) {
  button.textContent = caption;
  try {
    const sort = await NGUI.layout("UIPopup_Sort");
    const tree = clone(NGUI.find(sort, "Center/Popup/Btn/Btn_Ok"));
    tree.pos = [0, 0];
    tree.active = true;
    tree.actionSemantic="confirm";
    resizeButton(NGUI.find(tree, "BG (1)"), CONTROL.buttonL.minW, CONTROL.buttonL.h);
    NGUI.find(tree, "BG (1)").pos = [0, 0];
    NGUI.find(tree, "Label_Locale").pos = [0, 0];
    NGUI.find(tree, "Label_Locale").widgets[0].h_ = CONTROL.buttonL.h - SPACE.m;
    NGUI.fitLabel(NGUI.find(tree, "Label_Locale").widgets[0], caption, TYPE.display.fs, TYPE.display.min,
      CONTROL.buttonL.minW - 2 * CONTROL.buttonL.pad, 2, { maxH: CONTROL.buttonL.h - SPACE.m });
    const next = document.createElement("div"), scale = 0.4;
    await NGUI.render(tree, next, { scale, origin: [CONTROL.buttonL.minW * scale / 2, CONTROL.buttonL.h * scale / 2] });
    await NGUI.decoded([...(next.children || [])], Infinity);
    Object.assign(button.style, { position: "relative", display: "block", margin: `${SPACE.xl * scale}px auto 0`, width: `${CONTROL.buttonL.minW * scale}px`, height: `${CONTROL.buttonL.h * scale}px` });
    button.setAttribute("aria-label", caption);
    button.replaceChildren(...next.childNodes);
  } catch (err) { console.warn("preview button without game assets", err); }
}

// Prefabs and derived UI values belong to the selected installation.
NGUI.onResourceChange?.(() => { layouts = loading = null; });
