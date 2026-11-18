// Details (the info bar's Details button): the shown tab's game files on the popup frame.
import { isSpecial } from "./special-view.js";
import * as NGUI from "../ui/ngui.js";
import { T, platform } from "../core/i18n.js";
import { modOriginalName } from "../core/names.js";
import { closable, closed as popupClosed, hit as hitNode, popupFrame, scrollArea } from "../ui/popup.js";
import { DETAILS, detailsFrame, popupContent } from "../ui/popup-frames.js";
import { statsBox } from "../ui/stats-box.js";
import { COLOUR, SPACE, TYPE } from "../ui/ui-tokens.js";
import { row, stack, textItem, toNode } from "../ui/ui-grid.js";
import { appliedOf, partsOf } from "../mods/switch-sources.js";
import { $, S, glyphsOf, wrapPath } from "../core/util.js";
import {
  node,
  sprite,
  label,
  shownStudent,
  tab,
  fullName,
  shortName,
  alt,
  studentMods,
  modLabel,
  tabKind,
  current,
  sourceLabel,
  baseName,
  sizeText,
} from "./detail.js";
import { LOCAL_FRAME } from "./detail-strip.js";
import { reveal, editModName, shownMod } from "./detail-actions.js";

// --- details (the info bar's Details button): the shown tab's game files on the popup frame. A summary (the folder,
// what the files carry and, for a mod, its file and the alt it was made for), then one row per bundle: name,
// size, date, CRC and what it carries ---
const DT = DETAILS;
const DT_COLS = [
  ["field.file", 1040],
  ["field.size", 220],
  ["field.date", 270],
  ["field.crc", 290],
  ["field.status", 260],
];
const crcLabel = (state) =>
  state === "fixed"
    ? [T("crc.fixed"), COLOUR.success]
    : state === "needs_fix"
      ? [T("crc.needed"), COLOUR.warning]
      : [T("crc.original"), "#000000"];
// what a game file carries, as a label: the original, a mod's name, another alt's name, or "replaced"
function carriedLabel(applied) {
  if (!applied || applied === "official") return T("status.original");
  if (applied.startsWith("mod:")) {
    const m = studentMods().find((x) => x.id === applied.slice(4));
    if (m) return modLabel(m);
  }
  if (applied.startsWith("alt:")) {
    const o = shownStudent.alts.find((x) => x.key === applied.slice(4));
    if (o) return shortName(o);
  }
  return T("status.replaced");
}
function detailsInfo() {
  const v = alt(),
    kind = tabKind(),
    parts = partsOf(v, kind);
  const first = parts[0]?.file || null;
  const folder = first ? first.replace(/[\\/][^\\/]*$/, "") : "—";
  const applied = appliedOf(v, kind);
  const fields = [
    [T("field.folder"), folder],
    [T("info.current"), tab === "portrait" ? current().label : sourceLabel(v, kind), shownMod()?.mod],
  ];
  const mod = applied.startsWith("mod:") ? studentMods().find((m) => m.id === applied.slice(4)) : null;
  const from = mod
    ? shownStudent.alts.find((o) => o.key === mod.targetAlt)
    : applied.startsWith("alt:")
      ? shownStudent.alts.find((o) => o.key === applied.slice(4))
      : null;
  if (mod) fields.push([T("field.modFile"), mod.file || "—"]);
  if (from) fields.push([T("field.source"), fullName(from)]);
  const rows = parts.map((p) => {
    const crc = crcLabel(p.crc);
    return [baseName(p.file), sizeText(p.size), p.date || "—", [crc[0], crc[1]], carriedLabel(p.applied)];
  });
  return {
    title: T(isSpecial(v) && tab === "memorialLobby" ? "special.background" : `info.title.${tab}`),
    fields,
    rows,
    reveal: first,
  };
}
// the summary box and the file table, laid out from the top left of the scrolling area (units); returns the tree
// and its height
function detailsBody({ fields, rows }) {
  const W = popupContent(DT.w, DT.h).w,
    kids = [];
  // summary: captions, then values that wrap (paths), never cut; on the game's light stats box, as wide as the
  // file table under it. A mod's value leaves room at the right for its edit icon, and its original name stands
  // under it.
  const TOP = SPACE.s;
  // The light stats box retains STATS_BOX's own geometry and typography (stats-box.js).
  const box = statsBox({
    holder: "table",
    width: W,
    at: [W / 2, -TOP],
    name: "Summary",
    rows: fields.map(([k, v, mod]) => ({
      caption: k,
      value: String(v),
      ariaLabel: String(v),
      reserve: mod ? 70 : 0,
      secondary: mod?.alias ? modOriginalName(mod) : "",
      slot: mod
        ? ({ i, x, valueY, room, text }) => [
            node(
              `Edit_${i}`,
              [x + Math.min(glyphsOf(text.text, text.fs), room) + 32, valueY],
              [sprite("Cafe_Icon_NameEdit", 46, 46, { type: 0, depth: 9, color: "#2d4664/1.00" })],
            ),
          ]
        : undefined,
    })),
  });
  kids.push(...box.nodes);
  const boxH = box.height;
  // the file table: the list header's light blue bar with the captions, then the bundles
  const cols = DT_COLS.filter((_, i) => i < 4 || rows.some((r) => r[4] !== null));
  const extra = DT_COLS.reduce((a, [, w]) => a + w, 0) - cols.reduce((a, [, w]) => a + w, 0);
  const columns = row(
    cols.map(([, w], i) => ({ grow: w + (i === 0 ? extra : 0) })),
    { w: W },
  ).boxes;
  const widths = columns.map((b) => b.w - 2 * SPACE.s);
  const head = cols.map(([key], i) => textItem(NGUI, T(key), TYPE.caption, { maxW: widths[i], lines: 2 }));
  const data = rows.map((r) =>
    cols.map((_, i) => {
      const [value, color] = i === 3 ? r[3] : [r[i], "#000000"];
      // Dense rows keep their 42-unit floor. Wrap paths at separators and other text at language-aware boundaries;
      // longer values grow the row, keeping every character rather than escaping the range or clipping.
      let fit =
        i === 0
          ? wrapPath(value, TYPE.caption.fs, TYPE.caption.min, widths[i], Infinity)
          : NGUI.wrapText(String(value), TYPE.caption.fs, widths[i], Infinity);
      if (fit.overflow) fit = wrapPath(value, TYPE.caption.fs, TYPE.caption.min, widths[i], Infinity);
      return {
        ...textItem({ ...NGUI, fitText: () => fit }, value, TYPE.caption, { maxW: widths[i], lines: Infinity }),
        color,
      };
    }),
  );
  const lines = [head, ...data].map((items) =>
    row(
      items.map((item, i) => ({ ...item, w: widths[i] })),
      { x: SPACE.s, gap: 2 * SPACE.s },
    ),
  );
  const table = stack(
    lines.map((line) => ({ w: W, h: line.h + 2 * SPACE.s })),
    { y: TOP + boxH + SPACE.xl, gap: SPACE.s },
  );
  table.boxes.forEach((box, ri) => {
    const items = row(lines[ri].boxes, { x: SPACE.s, y: box.y + SPACE.s, gap: 2 * SPACE.s }).boxes;
    if (!ri)
      kids.push(
        node("Head", toNode(box, 4, LOCAL_FRAME), [
          sprite("Common_Bg_Raius10px", W, box.h, { depth: 5, color: "#d1edff/1.00" }),
        ]),
      );
    items.forEach((item, i) =>
      kids.push(
        node(ri ? `R_${ri - 1}_${i}` : `H_${i}`, toNode(item, 3, LOCAL_FRAME), [
          label(item.text, item.fs, ri ? `${item.color}/1.00` : "#294263/1.00", item.w, item.h, { pivot: 3, depth: 8 }),
        ]),
      ),
    );
    if (ri)
      kids.push(
        node(
          `RL_${ri - 1}`,
          [W / 2, -box.y - box.h],
          [sprite("Common_Line_Dot", W - 2 * SPACE.m, SPACE.hair, { type: 2, depth: 7, color: "#c0c9d4/1.00" })],
        ),
      );
  });
  return { tree: node("Details", [0, 0], [], kids), height: table.boxes.at(-1).y + table.boxes.at(-1).h + SPACE.m };
}
export async function showDetails() {
  const host = $("popup");
  const info = detailsInfo();
  const body = detailsBody(info);
  // popup.js supplies the L frame; popupContent keeps the table 40 from either side (popup-frames.js).
  const { h, top, bottom, content } = detailsFrame(body.height);
  const t = popupFrame(info.title, DT.w, h, {
    ok: T("picker.ok"),
    cancel: info.reveal ? T(platform === "mac" ? "open.mac" : "open.win") : null,
  });
  host.hidden = false;
  const next = document.createElement("div");
  const view = await NGUI.render(t, next, { scale: S, origin: [960, 540], z: 10 });
  const done = () => {
    host.hidden = true;
    host.innerHTML = "";
    popupClosed(host);
  };
  const hit = (path, fn, name, action) => hitNode(next, view, path, fn, { name, action });
  hit("Center/Popup/Base/BG/Btn_Close", done, T("display.close"), "close");
  hit("Center/Popup/Btn/Btn_Ok/BG (1)", done, T("picker.ok"), "ok");
  if (info.reveal)
    hit(
      "Center/Popup/Btn/Btn_Cancel/BG (1)",
      () => reveal(info.reveal),
      T(platform === "mac" ? "open.mac" : "open.win"),
      "cancel",
    );
  closable(view, host, done);
  const inner = scrollArea(
    next,
    [960 + (content.x - DT.w / 2) * S, 540 - top * S, content.w * S, (top - bottom) * S],
    body.height * S,
  );
  await inner.ready;
  const bodyView = await NGUI.render(body.tree, inner, { scale: S, origin: [0, 0], z: 20 });
  info.fields.forEach(([, , mod], i) => {
    if (mod)
      hitNode(
        inner,
        bodyView,
        `Details/Edit_${i}`,
        () => editModName(mod, () => (!host.hidden ? showDetails() : null)),
        { name: T("modAlias.title") },
      );
  });
  if (host.hidden) return;
  host.replaceChildren(...next.childNodes);
}
