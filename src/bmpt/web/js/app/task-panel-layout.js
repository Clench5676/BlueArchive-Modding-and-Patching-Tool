// The Tasks panel without the DOM (tested in Node): what it shows of the queued and running jobs (jobs.list), and
// its NGUI tree. The frame is the game's popup background (UIRatioNoticePopup Center/Popup/Base/BG: Common_Popup_Bg
// with the shard patterns Popup_Img_Deco_2 at the top left and Popup_Img_Deco_1 at the bottom right) under the
// popup's title; each job is a row of the Mission screen (UIMission's NUIMissionContent: its tag, title, Count and
// Gauge) on a plain card, the running one with the game's loading ring (UIRatioNoticePopup LoadingIcon) where the
// row has its Receive button; under the running job its items are small cards: the last finished ones, the one at
// hand with its step and share, and how many are left.
import { T } from "../core/i18n.js";
import { node, sprite, label } from "../ui/ngui.js";

/** The panel's geometry in NGUI units; it is drawn at SCALE of the stage's units. */
export const PANEL = Object.freeze({
  SCALE: 0.6,
  ROW_W: 1666, // NUIMissionContent's width
  ROW_H: 302, // its card's height (BG, Campaign_List_Normal)
  ROW_GAP: 16,
  JOB_GAP: 28,
  PAD: 64,
  TOP: 190, // the title's band: UIRatioNoticePopup's title sits 70 under the frame's top
  ITEM_H: 110,
  ITEM_INDENT: 90,
  BOTTOM: 104, // room for the bottom right shard pattern below the last card
  RING: [1440, -40], // the running ring, from the row's left middle (Btn_Receive's place)
  GAUGE_W: 732, // the row's Gauge/Fore width when full
  TITLE_Y: 78,
  // the frame's shard patterns, inset from its corners as on UIRatioNoticePopup's 2664 x 1266 frame
  DECO_TOP: [5, 68],
  DECO_BOTTOM: [5, 25],
});
const COLOURS = Object.freeze({
  running: "#42d368/1.00", // the row's own Tag colour (BG/Normal/Tag)
  queued: "#8a99a8/1.00",
  text: "#2c4663/1.00", // the row's Label_System
  muted: "#8a99a8/1.00",
  step: "#4a6a8e/1.00", // the row's Label_Locale_Count
  card: "#ffffff/1.00",
  waitingCard: "#f4f6f8/1.00",
  dot: "#b2bdc7/1.00", // the row's Line
  gaugeBack: "#d5dde5/1.00",
  gaugeFore: "#00c0ff/1.00", // the row's Gauge/Fore
});
export const KIND_ITEMS = Object.freeze({
  portrait: "tab.portrait",
  memorialLobby: "tab.memorialLobby",
  chibi: "tab.chibi",
  other: "import.other",
  thumbnail: "thumbnail.modName",
});

// the Tag sprite's anchors round Label_Tag (-35 / +35) and Label_System's left anchor after it (+52)
const TAG_PAD = 35,
  TAG_GAP = 52;
/** A text's width at a font size without a page to measure in: CJK and Thai full width, the rest about half. */
export const estimateWidth = (text, size) =>
  Array.from(String(text)).reduce(
    (sum, ch) => sum + (/[\u0e00-\u0e7f]/.test(ch) ? 0.6 : /[\u2e80-\uffef]/.test(ch) ? 1 : 0.56) * size,
    0,
  );

const has = (key) => T(key) !== key;
const share = (fraction) =>
  typeof fraction === "number" && Number.isFinite(fraction) && fraction > 0 ? Math.min(0.99, fraction) : 0;

/** A job's name: its kind's, else the label its progress gives, else a plain Background Task. */
export function jobName(job) {
  const key = `tasks.kind.${job?.kind}`;
  if (has(key)) return T(key);
  const p = job?.progress || {};
  if (p.label) return p.label;
  if (p.action && has(p.action))
    return T(p.action)
      .replace(/\{name\}/g, "")
      .replace(/[:：]\s*$/u, "")
      .trim();
  return T("tasks.other");
}

/**
 * An item of a job as its card names it: who it is and its kind ("Hoshino (Swimsuit) - Portrait"), the mod's own
 * name after; a job's own label for an item that is not a mod.
 * @param {object} p The item's progress.
 * @param {(entry: {alt?: string, core?: string}) => string} nameOf Names an alt as the student list does.
 */
export function itemName(p = {}, nameOf = (e) => e?.alt || e?.core || "") {
  if (p.alt || p.core) {
    const who = (p.resourceType === "other" ? p.core : nameOf({ alt: p.alt, core: p.core })) || p.core || p.alt;
    const kind = T(KIND_ITEMS[p.resourceType] || KIND_ITEMS.other);
    const own = p.name && p.name !== who && p.name !== p.core ? p.name : "";
    return [who, kind, own].filter(Boolean).join(T("progress.sep"));
  }
  return p.label || p.name || "";
}

/**
 * What the panel shows: one entry per job, the running one first (jobs.list's order).
 * @param {object[]} jobs jobs.list's answer.
 * @param {(entry: object) => string} [nameOf] Names an alt.
 * @returns {object[]} {id, name, running, current, total, done (0..1), count, items: [{state, name, step,
 *     fraction}]}; items only for the running job.
 */
export function panelView(jobs = [], nameOf) {
  return jobs.map((job) => {
    const p = job.progress || {};
    const total = Math.max(0, Number(p.total) || 0),
      current = Math.min(total, Math.max(0, Number(p.current) || 0));
    const running = job.state === "running";
    const fraction = share(p.fraction);
    const done = total > 0 && running ? Math.min(1, (Math.max(0, current - 1) + fraction) / total) : 0;
    const view = {
      id: job.id,
      name: jobName(job),
      running,
      current,
      total,
      done,
      count: total > 0 ? `${running ? current : 0}/${total}` : "",
      items: [],
    };
    if (!running) return view;
    for (const item of job.done || [])
      view.items.push({ state: "done", name: itemName(item, nameOf), step: T("tasks.done"), fraction: 0 });
    const name = itemName(p, nameOf);
    if (name) {
      const step = p.step && has(p.step) ? T(p.step) : "";
      const percent = fraction ? T("progress.percent", { percent: Math.floor(fraction * 100) }) : "";
      view.items.push({ state: "run", name, step: [step, percent].filter(Boolean).join(" "), fraction });
    }
    if (total > current && current > 0)
      view.items.push({ state: "wait", name: T("tasks.more", { count: total - current }), step: "", fraction: 0 });
    return view;
  });
}

const setText = (tree, path, text) => {
  const found = find(tree, path);
  const widget = found?.widgets?.find((w) => "text" in w);
  if (widget) widget.text = text;
  return found;
};
function find(tree, path) {
  let at = tree;
  for (const name of path.split("/")) {
    at = at?.children?.find((c) => c.name === name);
    if (!at) return null;
  }
  return at;
}

/** The item cards under the running job, from (x, y) down: [nodes, height]. */
function itemNodes(items, ring, x, y) {
  const { ROW_W, ITEM_H, ITEM_INDENT } = PANEL;
  const w = ROW_W - ITEM_INDENT,
    h = ITEM_H - 14;
  const nodes = [];
  items.forEach((item, i) => {
    const waiting = item.state === "wait";
    const children = [
      node(
        "Card",
        [w / 2, 0],
        [sprite("Common_Bg_Raius10px", w, h, { depth: 1, color: waiting ? COLOURS.waitingCard : COLOURS.card })],
      ),
    ];
    if (item.state === "done")
      children.push(node("Mark", [52, 0], [sprite("Common_Icon_Check_B", 46, 46, { depth: 30, type: 0 })]));
    else if (item.state === "run" && ring)
      children.push({ ...structuredClone(ring), pos: [52, 0], scale: [0.55, 0.55] });
    else
      children.push(node("Mark", [52, 0], [sprite("Common_Bg_Raius10px", 20, 20, { depth: 30, color: COLOURS.dot })]));
    children.push(
      node(
        "Name",
        [110, 0],
        [label(item.name, 46, waiting ? COLOURS.muted : COLOURS.text, w - 560, 58, { pivot: 3, depth: 30 })],
      ),
    );
    if (item.step)
      children.push(
        node("Step", [w - 40, 0], [label(item.step, 42, COLOURS.step, 380, 54, { pivot: 5, depth: 30, align: 2 })]),
      );
    if (item.state === "run" && item.fraction > 0) {
      const bar = w - 150;
      children.push(
        node(
          "GaugeBack",
          [110, -h / 2 + 14],
          [sprite("Common_Bg_Raius5px", bar, 10, { pivot: 3, depth: 8, color: COLOURS.gaugeBack })],
        ),
        node(
          "GaugeFore",
          [110, -h / 2 + 14],
          [
            sprite("Common_Bg_Raius5px", Math.max(10, Math.round(bar * item.fraction)), 10, {
              pivot: 3,
              depth: 9,
              color: COLOURS.gaugeFore,
            }),
          ],
        ),
      );
    }
    nodes.push(node(`Item${i}`, [x, y - i * ITEM_H - ITEM_H / 2], [], children));
  });
  return [nodes, items.length * ITEM_H];
}

/**
 * The panel's NGUI tree, its origin at the frame's top left.
 * @param {object[]} view panelView's answer.
 * @param {{row: object, frame: object, title: object, ring?: object}} prefabs UIMission's NUIMissionContent,
 *     UIRatioNoticePopup's Center/Popup/Base/BG and Label_Title, and its LoadingIcon.
 * @returns {{tree: object, width: number, height: number}} In NGUI units.
 */
export function panelTree(view, { row, frame, title, ring }, measure = estimateWidth) {
  const { ROW_W, ROW_H, ROW_GAP, JOB_GAP, PAD, TOP, ITEM_INDENT, BOTTOM, GAUGE_W } = PANEL;
  const W = ROW_W + 2 * PAD;
  const kids = [];
  let y = -TOP;
  if (!view.length) {
    kids.push(
      node("Empty", [W / 2, y - ROW_H / 4], [label(T("tasks.empty"), 52, COLOURS.muted, ROW_W, 68, { depth: 30 })]),
    );
    y -= ROW_H / 2;
  }
  for (const job of view) {
    const r = structuredClone(row);
    const centre = y - ROW_H / 2;
    r.pos = [PAD, centre];
    for (const path of [
      "Parcel",
      "Btn_Receive",
      "Gauge/CompleteTag",
      "Label_Locale_Limit",
      "CompleteExtensionTimeInfo",
    ]) {
      const hidden = find(r, path);
      if (hidden) hidden.active = false;
    }
    // a plain card: the row's own card sprite (Campaign_List_Normal) draws a shadow under it
    const card = find(r, "BG")?.widgets?.find((w) => "sprite" in w);
    if (card) Object.assign(card, { sprite: "Common_Bg_Raius10px", h_: ROW_H - 4 });
    const tagText = T(job.running ? "tasks.running" : "tasks.queued");
    // The tag fits its text as the prefab's anchors make it: Label_Tag resizes freely (overflow 2), the Tag sprite
    // spans it with TAG_PAD on each side, and the title starts TAG_GAP after the label and keeps its right edge.
    const tagLabelNode = setText(r, "BG/Normal/Label_Tag", tagText),
      tagLabel = tagLabelNode?.widgets?.find((w) => "text" in w),
      tag = find(r, "BG/Normal/Tag"),
      tagWidget = tag?.widgets?.find((w) => "sprite" in w),
      titleNode = setText(r, "BG/Normal/Label_System", job.name),
      titleWidget = titleNode?.widgets?.find((w) => "text" in w);
    if (tagLabel && tagLabelNode) {
      const width = Math.ceil(measure(tagText, tagLabel.fontSize));
      tagLabel.w_ = width;
      if (tagWidget && tag) {
        tagWidget.color = job.running ? COLOURS.running : COLOURS.queued;
        tagWidget.w_ = width + 2 * TAG_PAD;
        tag.pos = [tagLabelNode.pos[0] + width / 2, tag.pos[1]];
      }
      if (titleNode && titleWidget) {
        const right = titleNode.pos[0] + titleWidget.w_;
        titleNode.pos = [tagLabelNode.pos[0] + width + TAG_GAP, titleNode.pos[1]];
        titleWidget.w_ = right - titleNode.pos[0];
      }
    }
    const count = setText(r, "Label_Locale_Count", T("tasks.progress"));
    if (count) count.active = !!job.count;
    const gauge = find(r, "Gauge");
    if (gauge) gauge.active = !!job.count; // no count yet (queued): no Progress line and no gauge
    setText(r, "Label_Locale_Count/Label_Num", job.count);
    const fore = find(r, "Gauge/Fore");
    if (fore) {
      const w = fore.widgets.find((q) => "sprite" in q);
      if (w) w.w_ = Math.round(GAUGE_W * job.done);
      fore.active = job.done > 0;
    }
    kids.push(r);
    if (job.running && ring)
      kids.push({ ...structuredClone(ring), pos: [PAD + PANEL.RING[0], centre + PANEL.RING[1]], scale: [1.3, 1.3] });
    y -= ROW_H + ROW_GAP;
    const [items, height] = itemNodes(job.items, ring, PAD + ITEM_INDENT, y);
    kids.push(...items);
    y -= height + JOB_GAP;
  }
  const H = -y + BOTTOM;
  const bg = structuredClone(frame);
  bg.pos = [W / 2, -H / 2];
  const bgWidget = bg.widgets?.find((w) => "sprite" in w);
  if (bgWidget) Object.assign(bgWidget, { w_: W, h_: H, depth: -3 });
  for (const child of bg.children || []) child.widgets?.forEach((w) => (w.depth = -2));
  const top = find(bg, "Texture"),
    bottom = find(bg, "Texture (1)");
  if (top) top.pos = [-W / 2 + PANEL.DECO_TOP[0], H / 2 - PANEL.DECO_TOP[1]];
  if (bottom) {
    bottom.pos = [W / 2 - PANEL.DECO_BOTTOM[0], -H / 2 + PANEL.DECO_BOTTOM[1]];
    // a frame shorter than the pattern (few jobs) takes it scaled down, never past its top
    const widget = bottom.widgets?.find((w) => "texture" in w);
    const room = H - PANEL.DECO_BOTTOM[1] - PANEL.DECO_TOP[1];
    if (widget && widget.h_ > room) {
      const k = room / widget.h_;
      widget.w_ = Math.round(widget.w_ * k);
      widget.h_ = Math.round(widget.h_ * k);
    }
  }
  const head = structuredClone(title);
  head.pos = [W / 2, -PANEL.TITLE_Y];
  const headWidget = head.widgets?.find((w) => "text" in w);
  if (headWidget) headWidget.text = T("tasks.title");
  return { tree: node("TaskPanel", [0, 0], [], [bg, head, ...kids]), width: W, height: H };
}
