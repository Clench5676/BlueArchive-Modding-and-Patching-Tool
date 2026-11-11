// Shared popup geometry in prefab units. The Sort nodes provide the art; POPUP_FRAME provides the anatomy.
import { CONTROL, POPUP_FOOTER, POPUP_FRAME, SPACE } from "./ui-tokens.js";
import { container, stack, toNode } from "./ui-grid.js";

export function frameClass(width = "S") {
  if (typeof width === "string" && POPUP_FRAME[width]?.w) return POPUP_FRAME[width];
  return ["S", "M", "L", "XL"].map(key => POPUP_FRAME[key]).find(size => size.w >= width) || POPUP_FRAME.XL;
}
// Explicit heights fit the content; omitted heights retain the scrolling viewer's exemplar height.
export function frameGeometry(width, height = 0) {
  const size = frameClass(width);
  return { w: size.w, h: height ? Math.min(POPUP_FRAME.maxH, Math.max(POPUP_FRAME.minH, height)) : size.h, cx: 0, cy: POPUP_FRAME.frameY };
}
// Text and panel content share their top and footer clearance (POPUP_FOOTER). With no footer, keep the panel padding at the
// bottom.
export function popupContent(width, height, { text = false, footer = true } = {}) {
  const { w, h } = frameGeometry(width, height);
  return container({ w, h, padding: [POPUP_FRAME.contentTop, text ? SPACE.xxxl : SPACE.xl,
    footer ? POPUP_FOOTER.clearance : SPACE.xl,
    text ? SPACE.xxxl : SPACE.xl] }).inner;
}
const frame = (w, h) => frameGeometry(w, h);
const place = (box, f, pivot = 4) => toNode(box, pivot, f);

// The content box the XL prefab (UIPopup_GachaCharList) leaves over its buttonL footer (885): the XL popups (the text viewer,
// the list popups) keep it, their frame that box over the XS footer (BL-176), so only the footer's padding goes.
export const XL_CONTENT_H = POPUP_FRAME.XL.h - POPUP_FRAME.contentTop - (POPUP_FRAME.footer.bottom + CONTROL.buttonL.h + SPACE.m);
const viewerFrame = frame("XL", POPUP_FRAME.contentTop + XL_CONTENT_H + POPUP_FOOTER.clearance);
const viewerBox = popupContent(viewerFrame.w, viewerFrame.h, { text: true });
export const TEXT_VIEWER = { ...viewerFrame, box: { top: place(viewerBox, viewerFrame, 1)[1], height: viewerBox.h, width: viewerBox.w } };
TEXT_VIEWER.bottom = TEXT_VIEWER.box.top - TEXT_VIEWER.box.height;

export const ALIAS = { w: POPUP_FRAME.M.w, rowW: POPUP_FRAME.M.w - 2 * SPACE.xxxl, rowH: SPACE.xxxl,
  pitch: SPACE.xxxl + SPACE.m, top: POPUP_FRAME.contentTop + SPACE.xxxl / 2,
  bottom: POPUP_FOOTER.clearance + SPACE.xxxl / 2, max: 8 };
export function aliasFrame(n) {
  const f = frame("M", POPUP_FRAME.contentTop + n * ALIAS.rowH + Math.max(0, n - 1) * SPACE.m
    + POPUP_FOOTER.clearance);
  const content = popupContent(f.w, f.h, { text: true });
  const rows = stack(Array.from({ length: n }, () => ({ w: content.w, h: ALIAS.rowH })), { ...content, gap: SPACE.m, justify: "center" });
  const ys = rows.boxes.map(b => place(b, f)[1]);
  return { ...f, ys, bottom: ys.at(-1) - ALIAS.rowH / 2, content };
}

export const DETAILS = { w: POPUP_FRAME.L.w, h: POPUP_FRAME.maxH, l: -POPUP_FRAME.L.w / 2 + SPACE.xl,
  r: POPUP_FRAME.L.w / 2 - SPACE.xl, title: POPUP_FRAME.contentTop,
  bottom: POPUP_FOOTER.clearance };
export function detailsFrame(bodyHeight) {
  const f = frame("L", bodyHeight + DETAILS.title + DETAILS.bottom), content = popupContent(f.w, f.h);
  const body = stack([{ w: content.w, h: Math.min(bodyHeight, content.h) }], { ...content, justify: "center" }).boxes[0];
  return { ...f, top: place(body, f, 1)[1], bottom: place(body, f, 7)[1], content };
}

const modFrame = frame("M", POPUP_FRAME.contentTop + 2 * SPACE.xxxl + SPACE.m
  + POPUP_FOOTER.clearance), modBox = popupContent(modFrame.w, modFrame.h, { text: true });
const modRows = stack([{ w: modBox.w, h: SPACE.xxxl }, { w: modBox.w, h: SPACE.xxxl }],
  { ...modBox, gap: SPACE.m, justify: "center" }).boxes;
export const MOD_ALIAS = { ...modFrame, input: { y: place(modRows[0], modFrame)[1], w: modBox.w, h: modRows[0].h },
  hint: { dy: place(modRows[1], modFrame)[1] - place(modRows[0], modFrame)[1], h: modRows[1].h } };
MOD_ALIAS.bottom = MOD_ALIAS.input.y + MOD_ALIAS.hint.dy - MOD_ALIAS.hint.h / 2;

export function compareFrame(review, count = 0) {
  const cap = Math.floor(popupContent("L", 1180, { text: true, footer: review }).h / SPACE.xxl);
  const bodyHeight = Math.max(1, Math.min(count, cap)) * SPACE.xxl;
  const f = frame("L", review ? 1180 : POPUP_FRAME.contentTop + bodyHeight + SPACE.xl);
  const content = popupContent(f.w, f.h, { text: true, footer: review });
  const top = place(content, f, 1)[1];
  const body = stack([{ w: content.w, h: bodyHeight }], { ...content, justify: "center" }).boxes[0];
  const rows = { y: (review ? top : place(body, f, 1)[1]) - SPACE.xxl / 2, pitch: SPACE.xxl, h: SPACE.xxl, max: cap };
  const scroll = { top, height: content.h, width: content.w };
  return { ...f, rows, scroll, lift: 0, bottom: top - content.h, content };
}

const exportFrame = frame("L", POPUP_FRAME.contentTop + 260 + 240 + 240 + 2 * SPACE.m
  + POPUP_FOOTER.clearance), exportBox = popupContent(exportFrame.w, exportFrame.h);
const exportRows = stack([{ w: exportBox.w, h: 260 }, { w: exportBox.w, h: 240 }, { w: exportBox.w, h: 240 }],
  { ...exportBox, gap: SPACE.m, justify: "center" }).boxes; // Settings option rows retain their prefab heights.
export const EXPORT = { ...exportFrame, rows: Object.fromEntries(["Destination", "Server", "Platform"].map((name, i) =>
  [name, { y: place(exportRows[i], exportFrame)[1], h: exportRows[i].h }])) };
EXPORT.bottom = Math.min(...Object.values(EXPORT.rows).map(({ y, h }) => y - h / 2));
