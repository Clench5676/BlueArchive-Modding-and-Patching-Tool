// The alias editor (the nameplate's pencil): the user's own aliases of the alt on the popup frame.
import * as NGUI from "../ui/ngui.js";
import * as Loading from "../app/loading.js";
import { rpc } from "../core/api.js";
import { T } from "../core/i18n.js";
import { closable, closed as popupClosed, frameReady, hit as hitNode, popupFrame } from "../ui/popup.js";
import { ALIAS, aliasFrame } from "../ui/popup-frames.js";
import { $, S, hitButton } from "../core/util.js";
import { find, sprite, node, altId, ctx, open, ALIAS_MAX, ALIAS_LENGTH, customAliases, alt } from "./detail.js";
import { renderScreen } from "./detail-screen.js";
import { call } from "./detail-actions.js";

// --- the alias editor (the nameplate's pencil) --- The user's own aliases of the alt on the popup frame,
// one input row each (like UIPopup_Input's Input_NickName), a cross removing it, then an empty row with the
// pencil to add one while there is room. OK keeps them (aliases.set); Cancel, the close button, the backdrop and
// Escape drop the changes. Enter in the empty row adds what it holds, in another row goes to the next one, in an
// empty last one keeps them; not while an input method composes (isComposing, or keyCode 229, which WebKit sends
// for the key that ends a conversion).
const AL = ALIAS;
export let aliasToken = 0;
// draft: the rows; focusAt: the row to focus (the empty one when null); carry: the empty row of the editor drawn
// before, which keeps the focus while this one is drawn: what is typed there meanwhile goes into the new empty
// row
export async function openAliases(draft = customAliases(), focusAt = null, key = altId, carry = null) {
  const my = ++aliasToken;
  await Loading.wait(frameReady(), { immediate: true, cap: null });
  if (my !== aliasToken || !open || altId !== key) return;
  const host = $("popup");
  const names = draft.slice(0, ALIAS_MAX);
  const adding = names.length < ALIAS_MAX;
  const n = names.length + (adding ? 1 : 0);
  const { h, ys } = aliasFrame(n);
  const t = popupFrame(T("alias.title"), AL.w, h, { ok: T("picker.ok"), cancel: T("popup.cancel") });
  const popup = find(t, "Popup");
  ys.forEach((y, i) => {
    const add = adding && i === n - 1;
    const icon = add
      ? sprite("Cafe_Icon_NameEdit", 50, 50, { type: 0, depth: 9, color: "#2d4664/1.00" })
      : sprite("Common_Icon_Close", 42, 42, { type: 0, depth: 9, color: "#2d4664/1.00" });
    popup.children.push(
      node(
        `Row_${i}`,
        [0, y],
        [sprite("Common_Bg_Raius10px", AL.rowW, AL.rowH, { depth: 6, color: "#e6eaec/1.00" })],
        [
          node(
            "BG_Line",
            [0, 0],
            [sprite("Common_line_10px", AL.rowW + 2, AL.rowH, { depth: 7, color: "#cfd4da/1.00" })],
          ),
          node("Icon", [AL.rowW / 2 - 58, 0], [icon]),
        ],
      ),
    );
  });
  host.hidden = false;
  const next = document.createElement("div");
  const view = await NGUI.render(t, next, { scale: S, origin: [960, 540], z: 10 });
  if (my !== aliasToken) return;
  const close = () => {
    aliasToken += 1;
    host.hidden = true;
    host.innerHTML = "";
    popupClosed(host);
  };
  // the screen was left or shows another alt meanwhile
  if (!open || altId !== key) {
    close();
    return;
  }
  const inputs = [];
  // the rows' aliases as edited, and what the empty row holds
  const rows = () => inputs.slice(0, names.length).map((el) => el.value);
  const typed = () => (adding ? inputs[n - 1].value : "");
  const keep = async () => {
    const saved = await call(() => rpc("aliases.set", { alt: key, aliases: [...rows(), typed()] }));
    if (!saved || my !== aliasToken) return; // an error is shown and the editor stays
    close();
    ctx.invalidateRoster();
    if (altId !== key) return;
    alt().custom = saved;
    renderScreen();
  };
  ys.forEach((y, i) => {
    const add = adding && i === n - 1;
    const el = document.createElement("input");
    el.className = "alias-input";
    el.type = "text";
    el.maxLength = ALIAS_LENGTH;
    el.spellcheck = false;
    el.autocomplete = "off";
    el.enterKeyHint = add ? "done" : "next";
    el.value = add ? carry?.value || "" : names[i];
    el.placeholder = add ? T("alias.add") : "";
    el.setAttribute("aria-label", add ? T("alias.add") : T("alias.title"));
    Object.assign(el.style, {
      left: `${960 + (-AL.rowW / 2 + 36) * S}px`,
      top: `${540 - (y + AL.rowH / 2) * S}px`,
      width: `${(AL.rowW - 150) * S}px`,
      height: `${AL.rowH * S}px`,
      fontSize: `${50 * S}px`,
    });
    el.addEventListener("keydown", (ev) => {
      if (ev.isComposing || ev.keyCode === 229 || ev.key !== "Enter") return;
      ev.preventDefault();
      if (add && el.value.trim()) {
        const text = el.value;
        el.value = "";
        openAliases([...rows(), text], names.length + 1, key, el);
      } else if (i < n - 1) inputs[i + 1].focus();
      else keep();
    });
    next.appendChild(el);
    inputs.push(el);
    const [ix, iy] = [960 + (AL.rowW / 2 - 58) * S, 540 - y * S],
      size = 84 * S;
    const b = hitButton(
      next,
      [ix - size / 2, iy - size / 2, size, size],
      add
        ? () => el.focus()
        : () =>
            openAliases(
              rows().filter((_, j) => j !== i),
              i,
              key,
              adding ? inputs[n - 1] : null,
            ),
      add ? T("alias.add") : T("alias.remove"),
      86,
    );
    if (add) b.tabIndex = -1;
  });
  const hit = (path, fn, name, action) => hitNode(next, view, path, fn, { name, action });
  hit("Center/Popup/Btn/Btn_Ok/BG (1)", keep, T("picker.ok"), "ok");
  hit("Center/Popup/Btn/Btn_Cancel/BG (1)", close, T("popup.cancel"), "cancel");
  hit("Center/Popup/Base/BG/Btn_Close", close, T("display.close"), "close");
  closable(view, host, close);
  host.replaceChildren(...next.childNodes);
  const target = inputs[focusAt ?? inputs.length - 1] || inputs[inputs.length - 1];
  target?.focus();
}

// Setters of state of detail-aliases.js for the other detail modules: an imported binding is read-only.
export function setAliasToken(value) {
  aliasToken = value;
}
