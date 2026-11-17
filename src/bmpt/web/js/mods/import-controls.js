// The import page's controls (batch-import.js createImportPage): every visible widget drawn from the prefabs
// (import-layout.js) over plain DOM that only places, scrolls and takes the clicks: buttons, checkboxes, toggles, text
// inputs, radio groups, painted captions and the game's scrollbar over a scrolling area. The factory takes the page's
// state P (its artwork, its scrollbars) and keeps no state of its own.
import { T } from "../core/i18n.js";
import * as Popup from "../ui/popup.js";
import { S } from "../core/util.js";
import { BUTTON_BODY_H, clipButtonPatterns } from "./import-layout.js";
import { scrollbarTrack } from "../ui/scrollbar.js";

/**
 * Make an element with a class and a text.
 * @param {string} tag The tag name.
 * @param {string} [className] The class.
 * @param {string} [text] The text.
 * @returns {HTMLElement} The element.
 */
export const el = (tag, className = "", text = "") => {
  const element = document.createElement(tag);
  element.className = className;
  element.textContent = text;
  return element;
};
/**
 * Place an element absolutely in px of its parent (geometry only: what shows is drawn from the prefabs).
 * @param {HTMLElement} element The element.
 * @param {number} x The left edge.
 * @param {number} y The top edge.
 * @param {number} width The width.
 * @param {number} height The height.
 * @returns {HTMLElement} The element.
 */
export const place = (element, x, y, width, height) => {
  Object.assign(element.style, {
    position: "absolute",
    left: `${x}px`,
    top: `${y}px`,
    width: `${width}px`,
    height: `${height}px`,
  });
  return element;
};

/**
 * Make the import page's controls.
 * @param {object} P The page's state.
 * @param {object} services
 * @param {object} services.ui The NGUI renderer.
 * @param {Function} services.bars Draws the game's scrollbar.
 * @param {(work: Promise) => Promise} services.tracked Holds the page's loading cover while a drawing runs.
 * @returns {object} The controls: paint, textWidget, button, setCaption, checkbox, toggle, inputControl,
 *     radioOptions, bindBar and syncBar.
 */
export function createImportControls(P, { ui, bars, tracked }) {
  // The game list's scrollbar (UICharacterCollection/SeletControl/List/Scroll Bar) over a native scrolling area,
  // as Popup.scrollArea draws it: the area's own bar is hidden (app.css), this one follows its scroll position and
  // shows only while the content overflows. box: the area's [left, top, width, height] in host, the edge the
  // painted bar ends on.
  const syncBar = (area) => {
    P.scrollBars.get(area)?.bar?.sync(area.clientHeight, area.scrollHeight, area.scrollTop);
  };
  function bindBar(area, barHost, box, z = 60) {
    const key = JSON.stringify(box),
      mine = P.scrollBars.get(area);
    if (mine?.key === key) {
      syncBar(area);
      return;
    }
    mine?.bar?.remove();
    const record = { key, bar: null };
    P.scrollBars.set(area, record);
    if (!mine) area.addEventListener("scroll", () => syncBar(area));
    tracked(
      bars(barHost, scrollbarTrack(box, S), { collection: P.art.collection(), z, className: "import-bar" })
        .then((bar) => {
          if (P.scrollBars.get(area) !== record) {
            bar.remove();
            return;
          }
          record.bar = bar;
          syncBar(area);
        })
        .catch((error) => console.warn("import scrollbar unavailable", error)),
    );
  }
  function paint(element, tree, { scale = S, origin = [0, 0], textures = {}, onRender = null } = {}) {
    const version = (element.paintVersion = (element.paintVersion || 0) + 1);
    const next = el("div", "import-art");
    if (tree.rowHeight) element.style.height = `${tree.rowHeight * scale}px`;
    const drawing = ui.render(tree, next, { scale, origin, textures, deferImages: true }).then((view) => {
      clipButtonPatterns(view);
      if (element.paintVersion === version) {
        element.art?.remove();
        element.art = next;
        element.appendChild(next);
        onRender?.(view);
      }
      return Promise.allSettled(view.images || []).then(() => view);
    });
    return tracked(drawing);
  }
  function textWidget(element, caption, source = "note", { scale = 0.5, origin = [0, 0] } = {}) {
    if (element.caption === `${source}/${caption}`) return;
    element.caption = `${source}/${caption}`;
    paint(element, P.art[source](caption), { scale, origin });
  }
  // name: the accessible name where the painted caption is not one (a glyph on a pager button)
  function button(caption, action, { width = 180, primary = false, scale = 0.4, name = null, bodyHeight = null } = {}) {
    const b = el("button", "hit import-button");
    b.type = "button";
    b.dataset.action = action;
    b.accessibleName = name;
    const height = (bodyHeight ?? BUTTON_BODY_H) * scale;
    Object.assign(b.style, { width: `${width}px`, height: `${height}px` });
    b.refresh = () => {
      const key = JSON.stringify([b.caption, b.disabled, b.size]);
      if (key === b.drawKey) return;
      b.drawKey = key;
      paint(b, P.art.button(b.caption, width / scale, primary, !b.disabled, b.size, bodyHeight), {
        scale,
        origin: [width / 2, height / 2],
      });
    };
    b.buttonWidth = width / scale;
    setCaption(b, caption);
    return b;
  }
  function setCaption(b, caption) {
    b.caption = caption;
    b.setAttribute("aria-label", b.accessibleName || caption);
    b.refresh();
  }
  function checkbox(caption, checked, onChange, { labelWidth = 540 } = {}) {
    const label = el("label", "import-check"),
      input = el("input", "hit");
    input.type = "checkbox";
    input.checked = checked;
    const graphic = place(el("div", "import-check-art"), 0, 0, 50, 50);
    input.setAttribute("aria-label", caption || T("import.select"));
    label.append(graphic, input);
    let copy = null;
    if (caption) {
      copy = place(el("div"), 65, 0, labelWidth, 55);
      label.appendChild(copy);
      paint(copy, P.art.label(P.art.empty(caption), caption, labelWidth / 0.4), {
        scale: 0.4,
        origin: [labelWidth / 2, 26],
      });
    }
    let previous = null;
    const refresh = () => {
      const key = `${input.checked}/${input.disabled}`;
      if (key === previous) return;
      previous = key;
      paint(graphic, P.art.checkbox(input.checked, input.disabled), { scale: 0.5, origin: [25, 25] });
    };
    input.addEventListener("change", () => {
      onChange(input.checked);
      refresh();
    });
    refresh();
    return {
      label,
      input,
      refresh,
      relabel(value) {
        input.setAttribute("aria-label", value);
        if (copy)
          paint(copy, P.art.label(P.art.empty(value), value, labelWidth / 0.4), {
            scale: 0.4,
            origin: [labelWidth / 2, 26],
          });
      },
    };
  }
  function toggle(caption, checked, onChange) {
    const label = el("label", "import-toggle"),
      input = el("input", "hit");
    input.type = "checkbox";
    input.checked = checked;
    const graphic = el("div", "import-check-art");
    input.setAttribute("aria-label", caption);
    label.append(graphic, input);
    let previous = null;
    const refresh = () => {
      const key = JSON.stringify([caption, input.checked]);
      if (key === previous) return;
      previous = key;
      const tree = P.art.toggle(caption, input.checked),
        body = tree.widgets.find((w) => "sprite" in w);
      label.style.width = `${body.w_ * S}px`;
      label.style.height = `${body.h_ * S}px`;
      paint(graphic, tree, { origin: [0, (body.h_ * S) / 2] });
    };
    input.addEventListener("change", () => {
      onChange(input.checked);
      refresh();
    });
    refresh();
    return {
      label,
      input,
      refresh,
      relabel(value) {
        caption = value;
        input.setAttribute("aria-label", value);
        refresh();
      },
    };
  }
  // Match roster.js's native input and clear hit area to the rendered prefab labels/icons.
  function inputControl(caption, value, { type = "text", width = 650, search = false } = {}) {
    const wrap = el("div", "import-input-wrap"),
      input = el("input", `roster-search${search ? " import-search" : ""}`);
    input.type = type;
    input.value = value;
    input.setAttribute("aria-label", caption);
    input.autocomplete = "off";
    input.spellcheck = false;
    Object.assign(wrap.style, { position: "relative", width: `${width}px`, height: `${112 * S}px` });
    Object.assign(input.style, P.art.inputStyle(S));
    wrap.appendChild(input);
    const clear = search ? el("button", "hit import-search-clear") : null;
    if (clear) {
      clear.type = "button";
      clear.setAttribute("aria-label", T("display.reset"));
      wrap.appendChild(clear);
    }
    paint(wrap, P.art.search(width / S, search), {
      scale: S,
      origin: [width / 2, 0],
      onRender: (view) => {
        const [x, y, w, h] = Popup.nodeRect(view, "SearchNameArea/InputField/Label_Search");
        place(input, x, y + h / 2 - 46 * S, w, 92 * S);
        if (clear) {
          const [cx, cy, cw, ch] = Popup.nodeRect(view, "SearchNameArea/Btn_Close");
          place(clear, cx + cw / 2 - 55 * S, cy + ch / 2 - 55 * S, 110 * S, 110 * S);
        }
      },
    });
    return { wrap, input, clear };
  }
  // The state filter uses FilterSort/UISortObjectListUnit, as in display.js.
  function radioOptions(area, values, value, onChange, { width = 1370, columns = 1 } = {}) {
    const controls = [];
    const refresh = () => {
      for (const c of controls) {
        c.button.setAttribute("aria-checked", String(c.value === value));
        paint(c.button, P.art.radio(c.caption, c.value === value, width / columns / 0.5 - 50), {
          scale: 0.5,
          origin: [35, 44],
        });
      }
    };
    const group = el("div", "import-radio-group");
    group.setAttribute("role", "radiogroup");
    Object.assign(group.style, { position: "relative", height: `${Math.ceil(values.length / columns) * 96}px` });
    values.forEach(([key, caption], i) => {
      const button = place(
        el("button", "hit"),
        ((i % columns) * width) / columns,
        Math.floor(i / columns) * 96,
        width / columns - 15,
        92,
      );
      button.type = "button";
      button.setAttribute("role", "radio");
      button.setAttribute("aria-label", caption);
      button.dataset.value = key;
      button.addEventListener("click", () => {
        value = key;
        onChange(key);
        refresh();
      });
      controls.push({ value: key, caption, button });
      group.appendChild(button);
    });
    area.appendChild(group);
    refresh();
    return group;
  }
  return { paint, textWidget, button, setCaption, checkbox, toggle, inputControl, radioOptions, bindBar, syncBar };
}
