// The action list of a group card on the 3D tab's action bar: the game's description tooltip box over the card
// (UIDescriptionTooltip, as the hover hints draw it), the group's sections as UIShortcutTooltip's list title
// bars and its actions as UISortObjectListUnit radio keys, one to a line; the playing one is on. It opens upward
// from the card, stays inside the stage (action-list-layout.js placeList), scrolls when taller than the room
// over the card, and follows the card when the bar scrolls. Picking a key plays it and closes the list; a press
// outside the list (other than on a group card, which switches the list) or Escape closes it.
import * as NGUI from "./ngui.js";
import { HINT_PREFAB, hintStyle } from "./hover-hint.js";
import { T } from "./i18n.js";
import { S, clone, hitButton } from "./util.js";
import { BOX, HEAD, ROW, contentWidth, fitList, keyLabelWidth, placeList, stackList } from "./action-list-layout.js";
import { actionLabel, sectionLabel } from "./clip-names.js";

const { node, find } = NGUI;
const ORIGIN = [960, 515]; // the detail screen's prefab centre on the stage (detail.js)
const BOX_COLOR = "#ffffff/0.95";
const toPx = (x, y) => [ORIGIN[0] + x * S, ORIGIN[1] - y * S];
let layouts = null,
  shown = null,
  token = 0;
const textOf = (n) => n.widgets.find((w) => "text" in w);
const widest = (fit) =>
  Math.max(
    ...String(fit.text)
      .split("\n")
      .map((line) => NGUI.glyphWidth(line, fit.fs)),
  );

function load() {
  layouts ||= Promise.all([
    NGUI.layout(HINT_PREFAB),
    NGUI.layout("UIShortcutTooltip"),
    NGUI.layout("UIPopup_FilterSort"),
  ])
    .then(([hint, shortcut, sort]) => ({
      style: hintStyle(hint),
      head: find(shortcut, "GetList/TitleBG"),
      key: find(sort, "Center/Popup/Sort/Scroll View/Sort_Normal/Option_Normal/Grid/UISortObjectListUnit"),
    }))
    .catch((err) => {
      layouts = null;
      throw err;
    });
  return layouts;
}

// the rows: a title bar per section, a radio key per action (x from the content's centre, y from its top)
function rowsTree(L, rows, fits, cw, selected) {
  const out = node("Rows", [0, 0]);
  let h = 0,
    k = 0;
  for (const r of rows) {
    if (r.kind === "head") {
      const bar = clone(L.head);
      bar.name = `Head_${h}`;
      bar.pos = [0, r.y];
      // narrower by its lean, so its tips stay inside the content (the scroll area clips at its edges)
      bar.widgets.find((w) => w.sprite).w_ = cw - HEAD.slant;
      Object.assign(textOf(find(bar, "Label_Locale_Getlist")), {
        text: fits.heads[h].text,
        fontSize: fits.heads[h].fs,
        w_: cw - HEAD.slant - 2 * HEAD.inset,
      });
      out.children.push(bar);
      h++;
      continue;
    }
    const u = clone(L.key),
      fit = fits.keys[k++];
    u.name = `Key_${r.action.key}`;
    u.pos = [-cw / 2 + ROW.radioX, r.y];
    for (const c of u.children) c.active = c.name === "Label_Text";
    const opt = find(u, "Label_Text"),
      on = r.action.key === selected;
    find(opt, "On").active = on;
    find(opt, "Label_On").active = !on;
    for (const path of ["On/Label_On", "Label_On"])
      Object.assign(textOf(find(opt, path)), { text: fit.text, fontSize: fit.fs, w_: keyLabelWidth(cw) });
    // the key's underline runs under the whole line
    const line = find(opt, "Line");
    if (line) {
      line.pos = [-ROW.radioX, -ROW.pitch / 2];
      line.widgets[0].w_ = cw;
    }
    out.children.push(u);
  }
  return out;
}

/**
 * Open the list of a group card.
 * @param {object} options
 * @param {HTMLElement} options.host The layer the list goes in (over the bar).
 * @param {() => {left: number, right: number, top: number}} options.anchor Gives the card's visible body now,
 *     in units.
 * @param {string|null} options.selected The playing action's key.
 * @param {(key: string) => void} options.onPick Called when a key is picked.
 */
export async function open({ host, group, anchor, sections, selected, onPick }) {
  close();
  const my = ++token,
    L = await load();
  if (my !== token) return;
  const heads = sections.length > 1 ? sections.map((s) => sectionLabel(s, T)) : [],
    actions = sections.flatMap((s) => s.actions);
  const fits = fitList(
    heads,
    actions.map((a) => actionLabel(a, T, { withForm: false })),
  );
  const cw = contentWidth(fits.keys.map(widest), fits.heads.map(widest));
  const { rows, height: stacked } = stackList(sections);
  // the content reaches past the last key's line by half its underline (it runs along the line's bottom edge): counted
  // in, so a list that fits neither scrolls nor fades
  const height = stacked + (find(L.key, "Label_Text/Line")?.widgets[0]?.h_ || 0) / 2;
  const layer = document.createElement("div");
  layer.className = "action-list";
  Object.assign(layer.style, { position: "absolute", zIndex: 5, pointerEvents: "auto" });
  const place = () => {
    const p = placeList(anchor(), cw, height),
      [l, t] = toPx(p.left, p.top);
    Object.assign(layer.style, { left: `${l}px`, top: `${t}px`, width: `${p.w * S}px`, height: `${p.h * S}px` });
    return p;
  };
  const p = place();
  // the box a little translucent (the prefab's Common_Tooltip_Bg is opaque white, #ffffff/1.00); keys and titles opaque
  await NGUI.render(
    node("BG", [0, 0], [{ ...L.style.box, anchors: undefined, w_: p.w, h_: p.h, color: BOX_COLOR }]),
    layer,
    { scale: S, origin: [(p.w * S) / 2, (p.h * S) / 2], z: 1 },
  );
  const area = document.createElement("div");
  area.className = "settings-rows action-list-rows";
  Object.assign(area.style, {
    left: `${BOX.padX * S}px`,
    top: `${BOX.padTop * S}px`,
    width: `${cw * S}px`,
    height: `${p.view * S}px`,
    zIndex: 100,
  }); // over the box's sprite (its depth)
  const inner = document.createElement("div");
  inner.className = "settings-rows-inner";
  inner.style.height = `${height * S}px`;
  area.appendChild(inner);
  layer.appendChild(area);
  await NGUI.render(rowsTree(L, rows, fits, cw, selected), inner, { scale: S, origin: [(cw * S) / 2, 0], z: 5 });
  rows.forEach((r) => {
    if (r.kind !== "key") return;
    hitButton(
      inner,
      [0, (-r.y - ROW.pitch / 2) * S, cw * S, ROW.pitch * S],
      () => {
        close();
        onPick(r.action.key);
      },
      actionLabel(r.action, T),
      20,
    ).setAttribute("aria-pressed", String(r.action.key === selected));
  });
  if (my !== token) return;
  host.appendChild(layer);
  // the playing key in view
  const on = rows.find((r) => r.kind === "key" && r.action.key === selected);
  if (on && p.scrolls) area.scrollTop = Math.max(0, (-on.y - p.view / 2) * S);
  const outside = (e) => {
    if (!layer.contains(e.target) && !e.target.closest?.("[data-action-group]")) close();
  };
  const key = (e) => {
    if (e.key === "Escape" && !e.defaultPrevented) {
      e.preventDefault();
      close();
    }
  };
  addEventListener("pointerdown", outside, true);
  addEventListener("keydown", key);
  shown = {
    group,
    layer,
    place,
    off: () => {
      removeEventListener("pointerdown", outside, true);
      removeEventListener("keydown", key);
    },
  };
}

/** Close the list. */
export function close() {
  token++;
  if (!shown) return;
  shown.off();
  shown.layer.remove();
  shown = null;
}
/**
 * The group whose list is open.
 * @returns {string|null} The group's id, or null.
 */
export const openGroup = () => shown?.group ?? null;
// the prefabs read and the box, a title bar and a key drawn once off screen, when the bar is drawn, so the first press
// finds their sprites sliced (the first draw of a sprite takes a while; display.js warms its popup the same way)
let warming = null;
/**
 * Draw the prefabs, the box, a title bar and a key once off screen when the bar is drawn, so the first press
 * finds their sprites sliced (the first draw of a sprite takes a while; display.js warms its popup the same
 * way).
 */
export function warm() {
  warming ||= load()
    .then((L) => {
      const sample = { heads: [{ text: "-", fs: HEAD.fs, lines: 1 }], keys: [{ text: "-", fs: 40, lines: 1 }] };
      const rows = [
        { kind: "head", y: -30 },
        { kind: "key", y: -120, action: { key: "" } },
      ];
      const scratch = document.createElement("div");
      return Promise.all([
        NGUI.render(
          node("BG", [0, 0], [{ ...L.style.box, anchors: undefined, w_: 600, h_: 400, color: BOX_COLOR }]),
          scratch,
          { scale: S, origin: [0, 0] },
        ),
        NGUI.render(rowsTree(L, rows, sample, 500, ""), scratch, { scale: S, origin: [0, 0] }),
      ]);
    })
    .catch((err) => {
      warming = null;
      console.warn("action list warm-up", err);
    });
  return warming;
}
/** Move the list to its card after the bar scrolled. */
export function follow() {
  shown?.place();
}

// Prefabs and derived UI values belong to the selected installation.
NGUI.onResourceChange?.(() => {
  layouts = null;
  warming = null;
  close();
});
