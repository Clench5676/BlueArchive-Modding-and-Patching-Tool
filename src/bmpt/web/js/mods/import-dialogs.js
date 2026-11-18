// The import page's dialogs (batch-import.js createImportPage): the dialog frame every one of them opens in, the
// source of an entry, Advanced Options and the results. The factory takes the page's state P and the page functions
// it calls, and keeps no state of its own.
import * as Popup from "../ui/popup.js";
import { T } from "../core/i18n.js";
import { S } from "../core/util.js";
import { SPACE, POPUP_FRAME, POPUP_TYPE } from "../ui/ui-tokens.js";
import { stack as layoutStack, container } from "../ui/ui-grid.js";
import { ROW_W as OPTION_WIDTH, rowBase, radioRow } from "../settings/settings-rows.js";
import { importGroups } from "./import-groups.js";
import { ASSET_TYPES } from "./import-state.js";
import { el, place } from "./import-controls.js";
import { KIND_KEYS } from "./import-list.js";

// the locale key under import.options of each import option
const OPTION_LABELS = Object.freeze({
  skipUnchanged: "skipUnchanged",
  createBackup: "backup",
  matchStrategy: "strategy",
  crcCorrection: "crc",
  compression: "compression",
  extraBytes: "extraBytes",
  threads: "workers",
  outputFolder: "output",
});
// the locale key under import.strategy of a match strategy whose value differs from it
const STRATEGY_LABELS = Object.freeze({ containerNameType: "cont_name_type" });

// The dialogs' scrolling area [left, top, width, height] (px in the stage; app.css .import-dialog-content has
// the same numbers) and their frame's height: the area 83.5 px under the frame's top, and the XS footer every
// popup has about SPACE.xl under the area (POPUP_FOOTER.clearance).
export const DIALOG_H = 1320.5;
export const DIALOG_AREA = [228, 203, 1464, 640];

/**
 * Make the import page's dialogs.
 * @param {object} P The page's state.
 * @param {object} page The page's model, services and the page functions the dialogs call.
 * @returns {object} The dialogs: dialog, openSource, openOptions and results.
 */
export function createImportDialogs(P, page) {
  const { model, ui, popups, identified, report } = page;
  const { paint, textWidget, button, inputControl, radioOptions, bindBar } = page.controls;
  const { paragraphCopy, representative, bindTexture, thumbnail, prepareVisibleRows } = page.list;
  async function openSource(entry) {
    if (model.busy()) return;
    let server = entry.source?.server || entry.target.server,
      platform = entry.source?.platform || "android";
    await dialog(
      T("import.source.choose"),
      {
        action: T("picker.ok"),
        cancel: T("popup.cancel"),
        onAction: () => model.setSource(entry.id, server, platform),
      },
      (area, { width }) => {
        const choices = (key, values, value, change) => {
          const title = el("div");
          Object.assign(title.style, { height: "60px", position: "relative" });
          area.appendChild(title);
          textWidget(title, T(`import.source.${key}`), "empty", { scale: 0.5, origin: [width / 2, 30] });
          const group = radioOptions(area, values, value, change, { columns: values.length });
          group.setAttribute("aria-label", T(`import.source.${key}`));
        };
        choices(
          "server",
          ["global", "japan"].map((value) => [value, T(`settings.server.${value}`)]),
          server,
          (value) => {
            server = value;
          },
        );
        choices(
          "platform",
          ["android", "ios", "windows"].map((value) => [value, T(`import.source.${value}`)]),
          platform,
          (value) => {
            platform = value;
          },
        );
      },
    );
  }

  // Popup.popupFrame clones UIPopup_Sort/Center: Base/BG, Btn_Close and Btn/Btn_Ok / Btn_Cancel. content(area,
  // {primary(enabled)}): primary(false) blocks the confirm button (the game's disabled one, answering nothing)
  // until primary(true); it may be called while the content is built. width: the frame's (units); bodyWidth: the
  // content's, centred (px). padding instead: the content fills the frame's container (container: the frame less
  // padding units on each side), and content gets its width in px. the dialogs are the L class at most, their area
  // the frame's content box (padding SPACE.xl) unless one names its own
  async function dialog(
    title,
    {
      action = T("display.close"),
      cancel = null,
      onAction = () => true,
      width = POPUP_FRAME.L.w,
      bodyWidth = null,
      padding = bodyWidth ? null : SPACE.xl,
    } = {},
    content,
  ) {
    await popups.ready();
    if (!P.visible || P.dialogClose) return;
    const dialogHost = document.getElementById("dialog"),
      layer = el("div", "popup-layer import-dialog-layer"),
      before = document.activeElement;
    layer.tabIndex = -1;
    dialogHost.hidden = false;
    dialogHost.appendChild(layer);
    let ended = false,
      working = false;
    const close = (force = false) => {
      if (ended || (working && force !== true)) return;
      ended = true;
      layer.remove();
      popups.closed(dialogHost, layer);
      if (!dialogHost.children.length) dialogHost.hidden = true;
      P.dialogClose = null;
      if (before?.isConnected) before.focus?.({ preventScroll: true });
    };
    P.dialogClose = () => close(true);
    popups.opened(dialogHost, close, { el: layer, loading: true });
    try {
      const frame = popups.popupFrame(title, width, DIALOG_H, { ok: action, cancel });
      const view = await ui.render(frame, layer, { scale: S, origin: [960, 540], z: 10 });
      if (ended) return;
      let allowed = true,
        okHit = null,
        blocked = null;
      const sync = () => {
        if (okHit) {
          okHit.disabled = !allowed;
          okHit.setAttribute("aria-disabled", String(!allowed));
        }
        if (blocked) blocked.hidden = allowed;
      };
      // the confirm in the game's disabled look, over the live one (above its caption), shown while blocked
      if (popups.disabledConfirm) {
        blocked = el("div");
        blocked.hidden = true;
        layer.appendChild(blocked);
        await ui.render(popups.disabledConfirm(frame), blocked, { scale: S, origin: [960, 540], z: 40 });
        if (ended) return;
      }
      const area = el("div", "import-dialog-content"),
        box = [...DIALOG_AREA];
      if (padding != null) {
        const inner = container({ w: width, h: DIALOG_H, padding: [0, padding] }).inner;
        box[0] = 960 + (inner.x - width / 2) * S;
        box[2] = inner.w * S;
        Object.assign(area.style, { left: `${box[0]}px`, width: `${box[2]}px` });
      } else if (bodyWidth) {
        box[0] = 960 - bodyWidth / 2 - 16;
        box[2] = bodyWidth + 32;
        Object.assign(area.style, { left: `${box[0]}px`, width: `${box[2]}px` });
      }
      layer.appendChild(area);
      // the area keeps 16 px each side for the game's scrollbar
      await content(area, {
        width: box[2] - 32,
        primary: (enabled) => {
          allowed = enabled !== false;
          sync();
        },
      });
      bindBar(area, layer, box);
      const finish = async () => {
        if (working || !allowed) return;
        working = true;
        layer.setAttribute("aria-busy", "true");
        try {
          if (await onAction(area)) {
            working = false;
            close();
          }
        } finally {
          working = false;
          layer.setAttribute("aria-busy", "false");
        }
      };
      okHit = popups.hit(layer, view, "Center/Popup/Btn/Btn_Ok/BG (1)", finish, { name: action, action: "ok" });
      sync();
      if (cancel)
        popups.hit(layer, view, "Center/Popup/Btn/Btn_Cancel/BG (1)", close, { name: cancel, action: "cancel" });
      popups.hit(layer, view, "Center/Popup/Base/BG/Btn_Close", close, { name: T("display.close"), action: "close" });
      popups.closable(view, dialogHost, close, { el: layer });
      layer.setAttribute("role", "dialog");
      layer.setAttribute("aria-modal", "true");
      layer.setAttribute("aria-label", title);
      await (popups.reveal ? popups.reveal(layer, [...P.drawings]) : Promise.all([...P.drawings]));
      if (!ended) layer.focus?.({ preventScroll: true });
    } catch (error) {
      close();
      throw error;
    }
  }

  async function openOptions() {
    if (model.busy() || !(await model.loadOptions())) return;
    const draft = { ...model.state.options, assetTypes: [...model.state.options.assetTypes] };
    let error;
    // The L frame (POPUP_FRAME.L: UIPopup_Option_PC's) with its content in a container padded SPACE.xl on each side
    // (panels in a popup).
    await dialog(
      T("import.options.title"),
      {
        action: T("picker.ok"),
        cancel: T("popup.cancel"),
        width: POPUP_FRAME.L.w,
        padding: SPACE.xl,
        onAction: async () => {
          if (!draft.assetTypes.length || !Number.isSafeInteger(draft.threads) || draft.threads < 1) {
            textWidget(
              error,
              T(!draft.assetTypes.length ? "import.options.typesRequired" : "import.options.workersInvalid"),
              "error",
              { origin: [(OPTION_WIDTH * S) / 2, 35] },
            );
            return false;
          }
          const saved = await model.saveOptions(draft);
          if (!saved) textWidget(error, report(model.state.error), "error", { origin: [(OPTION_WIDTH * S) / 2, 35] });
          return saved;
        },
      },
      (area, { width: contentWidth }) => {
        // The option groups (Settings' rows, OPTION_WIDTH wide) stack SPACE.l apart, centred in the content; the hint
        // and the error line stack with them. parts: { element, w, h } in px, in order; holder: the element that stands
        // in the stack for a group.
        const parts = [];
        const add = (key, height, holder = null) => {
          const group = el("div", "import-option");
          group.dataset.option = key;
          Object.assign(group.style, { width: `${OPTION_WIDTH * S}px`, height: `${height * S}px` });
          parts.push({ element: holder || group, w: OPTION_WIDTH * S, h: height * S });
          if (holder) holder.appendChild(group);
          return group;
        };
        const choice = (key, title, values, getValue, onPick, { multiple = false, parent = null } = {}) => {
          const makeRow = () =>
            radioRow(`Option_${key}`, 0, title, values, getValue(), onPick, {
              columns: Math.min(values.length, 3),
              type: POPUP_TYPE.card,
            });
          const group = add(key, makeRow().h, parent),
            controls = [];
          group.setAttribute("role", multiple ? "group" : "radiogroup");
          group.setAttribute("aria-label", title);
          const refresh = () => {
            const row = makeRow();
            values.forEach(([value], i) => {
              const checked = multiple ? getValue().has(value) : value === getValue();
              const option = ui.find(row.tree, `Opt_${i}`),
                control = controls[i];
              if (multiple) {
                const check = P.art.checkbox(checked);
                check.scale = [0.6, 0.6];
                option.children = [check, ui.find(option, "Label")];
                control.checked = checked;
              } else control.setAttribute("aria-checked", String(checked));
            });
            paint(group, row.tree, { scale: S, origin: [(OPTION_WIDTH * S) / 2, (row.h * S) / 2] });
          };
          const row = makeRow();
          values.forEach(([value, caption], i) => {
            const option = ui.find(row.tree, `Opt_${i}`),
              hit = row.hits[i];
            const control = place(
              el(multiple ? "input" : "button", "hit import-option-control"),
              (OPTION_WIDTH / 2 + option.pos[0] - 30) * S,
              (row.h / 2 - option.pos[1] - 30) * S,
              hit.w * S,
              60 * S,
            );
            control.type = multiple ? "checkbox" : "button";
            if (!multiple) control.setAttribute("role", "radio");
            control.setAttribute("aria-label", caption);
            control.dataset.value = String(value);
            control.addEventListener(multiple ? "change" : "click", () => {
              onPick(value, control.checked);
              refresh();
            });
            controls.push(control);
            group.appendChild(control);
          });
          refresh();
          return refresh;
        };
        const onOff = [
          [true, T("settings.post.on")],
          [false, T("settings.post.off")],
        ];
        for (const [key, label] of [
          ["previewAfterImport", "selectPreview"],
          ["enableAfterImport", "applyGame"],
        ]) {
          choice(
            key,
            T(`import.${label}`),
            onOff,
            () => !!draft[key],
            (value) => {
              draft[key] = value;
            },
          );
        }
        for (const key of ["skipUnchanged", "createBackup"]) {
          choice(
            key,
            T(`import.options.${OPTION_LABELS[key]}`),
            onOff,
            () => draft[key],
            (value) => {
              draft[key] = value;
            },
          );
        }
        const radio = (key, values) =>
          choice(
            key,
            T(`import.options.${OPTION_LABELS[key]}`),
            values,
            () => draft[key],
            (value) => {
              draft[key] = value;
            },
          );
        radio(
          "matchStrategy",
          ["containerNameType", "path_id", "name_type"].map((key) => [
            key,
            T(`import.strategy.${STRATEGY_LABELS[key] || key}`),
          ]),
        );
        radio("crcCorrection", [
          ["auto", T("import.auto")],
          ["on", T("settings.post.on")],
          ["off", T("settings.post.off")],
        ]);
        radio("compression", [
          ["lzma", "LZMA"],
          ["lz4", "LZ4"],
          ["original", T("import.original")],
          ["none", T("import.uncompressed")],
        ]);
        const types = el("fieldset", "import-asset-types");
        types.setAttribute("aria-label", T("import.options.types"));
        choice(
          "assetTypes",
          T("import.options.types"),
          ASSET_TYPES.map((type) => [type, type === "ALL" ? T("import.options.allTypes") : type]),
          () => new Set(draft.assetTypes),
          (type, checked) => {
            const values = new Set(draft.assetTypes);
            if (checked) values.add(type);
            else values.delete(type);
            if (checked && type === "ALL") {
              values.clear();
              values.add("ALL");
            } else if (checked) values.delete("ALL");
            draft.assetTypes = [...values];
          },
          { multiple: true, parent: types },
        );
        for (const key of ["extraBytes", "threads", "outputFolder"]) {
          const group = add(key, 240);
          paint(group, rowBase(`Option_${key}`, 0, 240, T(`import.options.${OPTION_LABELS[key]}`), POPUP_TYPE.card), {
            scale: S,
            origin: [(OPTION_WIDTH * S) / 2, 120 * S],
          });
          const control = inputControl(T(`import.options.${OPTION_LABELS[key]}`), draft[key] ?? "", {
            type: key === "threads" ? "number" : "text",
            width: (OPTION_WIDTH - 110) * S,
          });
          place(control.wrap, 55 * S, 120 * S, (OPTION_WIDTH - 110) * S, 112 * S);
          group.appendChild(control.wrap);
          const input = control.input;
          if (key === "threads") {
            input.min = "1";
            input.step = "1";
          }
          input.addEventListener("input", () => {
            draft[key] =
              key === "threads"
                ? Number(input.value)
                : key === "outputFolder"
                  ? input.value.trim() || null
                  : input.value;
          });
        }
        const note = el("div", "import-option-note");
        note.style.height = "70px";
        parts.push({ element: note, w: OPTION_WIDTH * S, h: 70 });
        textWidget(note, T("import.options.bytesHint"), "empty", { origin: [(OPTION_WIDTH * S) / 2, 35] });
        error = el("div", "import-option-error");
        error.style.height = "80px";
        error.setAttribute("role", "alert");
        parts.push({ element: error, w: OPTION_WIDTH * S, h: 80 });
        const pile = layoutStack(
          parts.map(({ w, h }) => ({ w, h })),
          { w: contentWidth, gap: SPACE.l * S, align: "center" },
        );
        const body = el("div", "import-options");
        Object.assign(body.style, { position: "relative", width: `${contentWidth}px`, height: `${pile.h}px` });
        parts.forEach(({ element }, i) => {
          place(element, pile.boxes[i].x, pile.boxes[i].y, pile.boxes[i].w, pile.boxes[i].h);
          body.appendChild(element);
        });
        area.appendChild(body);
      },
    );
  }

  async function results() {
    await dialog(T("import.resultTitle"), {}, (area, { width }) => {
      area.classList.add("import-results");
      // the rows drawn for a 1400 px wide area, scaled to the L-class dialog's
      const k = Math.min(1, width / 1400);
      const pictures = [];
      const groups = importGroups(model.state.taskEntries, P.merged, identified);
      const textSize = P.art.rowTextSize(groups.map((group) => paragraphCopy(group, true)));
      const resultScale = 0.62 * k,
        resultTextSize = textSize;
      for (const group of groups) {
        const entry = representative(group.entries),
          row = el("div", "import-result-row");
        Object.assign(row.style, { width: `${1400 * k}px`, height: `${216 * k}px` });
        row.dataset.result = entry.id;
        const drawing = paint(
          row,
          P.art.row({ name: identified(entry).name, ...paragraphCopy(group, true), textSize: resultTextSize }),
          { scale: resultScale, origin: [700 * k, 104 * k], textures: { Icon_Arona: Promise.resolve(null) } },
        );
        let picture;
        pictures.push({
          row,
          height: 216 * k,
          prepare: () =>
            (picture ||= bindTexture(
              drawing,
              "List_01/BG_Icon/Icon_Arona",
              thumbnail(entry, { incoming: true }),
              "import-thumb",
            )),
        });
        const go = button(T("add.go"), "go", { width: 225 * k, scale: 0.4 * k });
        place(go, 1115 * k, 86 * k, 225 * k, 65 * k);
        const navigate = (item) => {
          P.dialogClose?.();
          const alt = identified(item).alt;
          if (item.resourceType === "other" || !alt) P.context?.goOther?.();
          else P.context?.go?.(alt.student.key, alt.key, item.resourceType);
        };
        go.addEventListener("click", () => navigate(entry));
        if (P.merged)
          drawing.then((view) => {
            for (const kind of Object.keys(group.counts).filter((kind) => group.counts[kind])) {
              const item = group.entries.find((item) => item.resourceType === kind);
              const path = `List_01/Label_From/${kind}`;
              const boxes = ["Count", "Icon"].map((part) => Popup.nodeRect(view, `${path}/${part}`)).filter(Boolean);
              const x = Math.min(...boxes.map(([x]) => x)),
                y = Math.min(...boxes.map(([, y]) => y));
              const right = Math.max(...boxes.map(([x, , w]) => x + w)),
                bottom = Math.max(...boxes.map(([, y, , h]) => y + h));
              const count = place(el("button", "hit import-result-count"), x, y, right - x, bottom - y);
              count.style.zIndex = "40";
              count.type = "button";
              count.dataset.kind = kind;
              count.setAttribute("aria-label", T(KIND_KEYS[kind] || KIND_KEYS.other));
              count.addEventListener("click", () => navigate(item));
              row.appendChild(count);
            }
          });
        row.appendChild(go);
        area.appendChild(row);
      }
      area.addEventListener("scroll", () => prepareVisibleRows(area, pictures));
      prepareVisibleRows(area, pictures);
    });
  }

  return { dialog, openSource, openOptions, results };
}
