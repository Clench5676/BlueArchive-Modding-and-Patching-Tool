// A library-only name on the game's input row, above the source picker or detail panel. It is a layer of its own in
// #dialog (as the message popups), so a message opened from it, a failed save's, draws over it and Escape closes the
// topmost layer; closing it leaves any other layer of the host alone.
import { notify } from "./popup.js";
import * as NGUI from "./ngui.js";
import { rpc } from "./api.js";
import { T, errorText } from "./i18n.js";
import { closed, closable, frameReady, hit, popupFrame } from "./popup.js";
import { MOD_ALIAS } from "./popup-frames.js";
import * as Loading from "./loading.js";
import { $, S } from "./util.js";

let token = 0, dismiss = null;
export function close() { token += 1; dismiss?.(); }
export async function open({ mod, onSaved }) {
  close();
  const my = ++token;
  await Loading.wait(frameReady(), { immediate: true, cap: null });
  if (my !== token) return;
  const host = $("dialog"), next = document.createElement("div");
  const tree = popupFrame(T("modAlias.title"), MOD_ALIAS.w, MOD_ALIAS.h, { ok: T("popup.save"), cancel: T("popup.cancel") });
  const { y, w, h } = MOD_ALIAS.input;
  NGUI.find(tree, "Popup").children.push(
    NGUI.node("Input", [0, y], [NGUI.sprite("Common_Bg_Raius10px", w, h, { depth: 6, color: "#e6eaec/1.00" })], [
      NGUI.node("Line", [0, 0], [NGUI.sprite("Common_line_10px", w + 2, h, { depth: 7, color: "#cfd4da/1.00" })]),
    ]),
    NGUI.node("Hint", [0, y + MOD_ALIAS.hint.dy], [NGUI.label(T("modAlias.hint"), 36, "#2d4664/1.00", w, MOD_ALIAS.hint.h, { depth: 8 })]),
  );
  const layer = document.createElement("div");
  layer.className = "popup-layer";
  layer.tabIndex = -1;
  let ended = false;
  const finish = () => {
    if (ended) return;
    ended = true;
    token += 1;
    dismiss = null;
    layer.remove();
    closed(host, layer);
    if (!host.children.length) host.hidden = true;
  };
  dismiss = finish;
  host.appendChild(layer);
  host.hidden = false;
  let view;
  try { view = await NGUI.render(tree, next, { scale: S, origin: [960, 540], z: 10 }); }
  catch (err) { finish(); throw err; }
  if (my !== token || ended) return;
  const input = document.createElement("input");
  Object.assign(input, { className: "alias-input mod-alias-input", type: "text", maxLength: 120,
    spellcheck: false, autocomplete: "off", enterKeyHint: "done", value: mod.alias || "" });
  input.setAttribute("aria-label", T("modAlias.title"));
  Object.assign(input.style, { left: `${960 + (-w / 2 + 36) * S}px`, top: `${540 - (y + h / 2) * S}px`,
    width: `${(w - 72) * S}px`, height: `${h * S}px`, fontSize: `${50 * S}px` });
  let saving = false;
  const keep = async () => {
    if (saving || my !== token) return;
    saving = true;
    input.disabled = true;
    try {
      const saved = await rpc("mods.rename", { id: mod.id, label: input.value });
      if (my === token) finish();
      await onSaved(saved);
    } catch (err) {
      notify(errorText(err), { kind: "error", error: err });
    } finally { saving = false; input.disabled = false; }
  };
  input.addEventListener("keydown", (ev) => {
    if (ev.key !== "Enter" || ev.isComposing || ev.keyCode === 229) return;
    ev.preventDefault();
    return keep();
  });
  next.appendChild(input);
  hit(next, view, "Center/Popup/Btn/Btn_Ok/BG (1)", keep, { name: T("popup.save"), action: "ok" });
  hit(next, view, "Center/Popup/Btn/Btn_Cancel/BG (1)", finish, { name: T("popup.cancel"), action: "cancel" });
  hit(next, view, "Center/Popup/Base/BG/Btn_Close", finish, { name: T("display.close"), action: "close" });
  closable(view, host, finish, { el: layer });
  layer.replaceChildren(...next.childNodes);
  input.focus();
  input.select?.();
}
