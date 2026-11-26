// The Tasks panel (app/task-panel-layout.js) and the Tasks button's place in the top bar (window/top-action-layout.js):
// what the panel shows of jobs.list in every language, and its tree against the prefabs' geometry (UIMission's
// NUIMissionContent, UIRatioNoticePopup's frame, title and loading ring, as the 2026-11 resource version has them).
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
const stub = () => ({
  style: {},
  getContext: () => ({ measureText: (text) => ({ width: text.length * 16 }) }),
  appendChild() {},
  setAttribute() {},
});
globalThis.document = { documentElement: {}, getElementById: () => null, createElement: stub };
globalThis.addEventListener ??= () => {};
globalThis.Image ??= class {};
// ngui.js reads the page when it loads: imported once the stand-in document is up
const { setStrings, T } = await import("../../src/bmpt/web/js/core/i18n.js");
const { PANEL, estimateWidth, isFolded, itemHeight, jobName, panelTree, panelView } = await import(
  "../../src/bmpt/web/js/app/task-panel-layout.js"
);
const { topActionLayout } = await import("../../src/bmpt/web/js/window/top-action-layout.js");
const LOCALES = ["en-US", "zh-CN", "zh-TW", "ja-JP", "ko-KR", "th-TH"];
const flat = (data, prefix = "", out = {}) => {
  for (const [key, value] of Object.entries(data)) {
    if (value && typeof value === "object") flat(value, `${prefix}${key}.`, out);
    else out[prefix + key] = value;
  }
  return out;
};
const load = (locale) => JSON.parse(readFileSync(new URL(`../../src/bmpt/locales/${locale}.json`, import.meta.url)));
const use = (locale) => setStrings(locale, flat(load(locale)));

const W = (props) => ({ depth: 5, color: "#ffffff/1.00", pivot: 4, ...props });
const n = (name, pos, widgets = [], children = []) => ({
  name,
  active: true,
  pos,
  scale: [1, 1],
  rotZ: 0,
  widgets,
  children,
});
// NUIMissionContent: the nodes the panel fills or hides, at the prefab's positions and sizes
const ROW = n(
  "NUIMissionContent",
  [0, 0],
  [W({ w_: 1666, h_: 298, pivot: 3 })],
  [
    n(
      "Gauge",
      [1033, -47],
      [],
      [
        n("Back", [-1004, -76], [W({ sprite: "Common_Bg_Raius10px", w_: 740, h_: 48, pivot: 6, depth: 8 })]),
        n("Fore", [-1000, -72], [W({ sprite: "Common_Bg_Raius5px", w_: 732, h_: 40, pivot: 6, depth: 9 })]),
        n("CompleteTag", [-28.9, -34], [W({ sprite: "Common_Tag_ImageFont_Complete", w_: 172, h_: 34, pivot: 3 })]),
      ],
    ),
    n("Parcel", [1204, -49]),
    n("Btn_Receive", [1460, -55.9]),
    n("Label_Locale_Limit", [27, 13]),
    n(
      "Label_Locale_Count",
      [27, -42.6],
      [W({ text: "count", fontSize: 42, w_: 116, h_: 40, pivot: 3 })],
      [n("Label_Num", [176, -1], [W({ text: "300/300", fontSize: 42, w_: 561, h_: 40, pivot: 3 })])],
    ),
    n("CompleteExtensionTimeInfo", [77, 13]),
    n(
      "BG",
      [1665, 147],
      [W({ sprite: "Campaign_List_Normal", w_: 1664, h_: 302, pivot: 2, depth: 1 })],
      [
        n(
          "Normal",
          [-1604, -52],
          [],
          [
            n("Label_Tag", [0, 3], [W({ text: "tag", fontSize: 42, w_: 72, h_: 40, pivot: 3, depth: 22 })]),
            n("Tag", [36, 2], [W({ sprite: "Common_Bg_Raius10px", w_: 142, h_: 62, depth: 3 })]),
            n("Label_System", [124, -1], [W({ text: "title", fontSize: 52, w_: 1435, h_: 68, pivot: 3, depth: 22 })]),
          ],
        ),
      ],
    ),
  ],
);
const FRAME = n(
  "BG",
  [0, -8],
  [W({ sprite: "Common_Popup_Bg", w_: 2664, h_: 1266, depth: 2 })],
  [
    n("Texture", [-1327, 565], [W({ texture: "Popup_Img_Deco_2", w_: 494, h_: 132, pivot: 3, depth: 3 })]),
    n("Texture (1)", [1326.9, -608.2], [W({ texture: "Popup_Img_Deco_1", w_: 1458, h_: 692, pivot: 8, depth: 3 })]),
  ],
);
const TITLE = n("Label_Title", [0, 555], [W({ text: "確率情報", fontSize: 72, w_: 288, h_: 72, depth: 22 })]);
const RING = n("LoadingIcon", [0, 0], [W({ sprite: "Common_Login_Loading", w_: 75, h_: 75, depth: 10 })]);
const PREFABS = { row: ROW, frame: FRAME, title: TITLE, ring: RING };

const find = (tree, path) => path.split("/").reduce((at, name) => at?.children?.find((c) => c.name === name), tree);
const text = (node) => node?.widgets?.find((w) => "text" in w);
const sprite = (node) => node?.widgets?.find((w) => "sprite" in w || "texture" in w);

const JOBS = [
  {
    id: "j1",
    kind: "mods.catalogReapply",
    state: "running",
    writes: "game",
    resumable: true,
    progress: {
      current: 38,
      total: 245,
      alt: "hoshino_swimsuit",
      resourceType: "chibi",
      step: "progress.actionApply",
      fraction: 0.45,
    },
    done: [
      { current: 36, total: 245, alt: "hoshino", resourceType: "portrait" },
      { current: 37, total: 245, alt: "hina_dress", resourceType: "memorialLobby" },
    ],
  },
  { id: "j2", kind: "library.prune", state: "queued", writes: "library", progress: null, done: [] },
];

for (const locale of LOCALES)
  test(`every job kind and the panel's words have their own text in ${locale}`, () => {
    use(locale);
    const own = flat(load(locale).webui.tasks);
    const english = flat(load("en-US").webui.tasks);
    assert.deepEqual(Object.keys(own).sort(), Object.keys(english).sort(), "the same keys as en-US");
    for (const [key, value] of Object.entries(own)) {
      assert.ok(value.trim(), `${key} has a text`);
      assert.ok(!/[·・]/.test(value), `${key} uses no middle dot`);
    }
    assert.match(T("tasks.more", { count: 206 }), /206/);
    for (const kind of Object.keys(english).filter((key) => key.startsWith("kind.")))
      assert.equal(jobName({ kind: kind.slice(5) }), own[kind]);
  });

test("a job of an unknown kind is named by its progress label, else as a background task", () => {
  use("en-US");
  assert.equal(jobName({ kind: "library.adopt", progress: { label: "Adopting" } }), "Adopting");
  assert.equal(jobName({ kind: "library.adopt" }), T("tasks.other"));
});

for (const locale of LOCALES)
  test(`the running job lists its last finished items, the one at hand and how many are left, in ${locale}`, () => {
    use(locale);
    const names = { hoshino: "Hoshino", hina_dress: "Hina (Dress)", hoshino_swimsuit: "Hoshino (Swimsuit)" };
    const [running, queued] = panelView(JOBS, ({ alt }) => names[alt] || alt);
    assert.equal(running.name, T("tasks.kind.mods.catalogReapply"));
    assert.equal(running.count, "38/245");
    assert.ok(Math.abs(running.done - (37 + 0.45) / 245) < 1e-9);
    assert.deepEqual(
      running.items.map((item) => item.state),
      ["done", "done", "run", "wait"],
    );
    assert.equal(running.items[0].name, `Hoshino${T("progress.sep")}${T("tab.portrait")}`);
    assert.equal(running.items[2].name, `Hoshino (Swimsuit)${T("progress.sep")}${T("tab.chibi")}`);
    assert.equal(running.items[2].step, `${T("progress.actionApply")} ${T("progress.percent", { percent: 45 })}`);
    assert.equal(running.items[3].name, T("tasks.more", { count: 207 }));
    assert.equal(queued.running, false);
    assert.deepEqual(queued.items, []);
    assert.equal(queued.done, 0);
  });

for (const locale of LOCALES)
  test(`the panel stacks its cards inside the popup frame with its corner patterns in ${locale}`, () => {
    use(locale);
    const view = panelView(JOBS);
    const { tree, width, height } = panelTree(view, PREFABS);
    assert.equal(width, PANEL.ROW_W + 2 * PANEL.PAD);
    const [frame, head, first, ring, ...rest] = tree.children;
    // the frame spans the panel and lies under everything else
    assert.deepEqual(frame.pos, [width / 2, -height / 2]);
    assert.equal(sprite(frame).w_, width);
    assert.equal(sprite(frame).h_, height);
    const depths = [];
    const walk = (node) => {
      node.widgets?.forEach((w) => depths.push(w.depth));
      node.children?.forEach(walk);
    };
    tree.children.slice(2).forEach(walk);
    assert.ok(Math.min(...depths) > sprite(frame).depth, "cards above the frame");
    for (const deco of frame.children) assert.ok(sprite(deco).depth < Math.min(...depths), "patterns under the cards");
    // the shard pattern's bottom right corner in the frame's bottom right corner (pivot 8)
    const bottom = find(frame, "Texture (1)");
    assert.deepEqual(bottom.pos, [width / 2 - PANEL.DECO_BOTTOM[0], -height / 2 + PANEL.DECO_BOTTOM[1]]);
    assert.equal(text(head).text, T("tasks.title"));
    // the running job's card: no shadowed card sprite, its ring where the row has its Receive button
    assert.equal(sprite(find(first, "BG")).sprite, "Common_Bg_Raius10px");
    assert.equal(find(first, "Btn_Receive").active, false);
    assert.equal(find(first, "Parcel").active, false);
    assert.equal(ring.name, "LoadingIcon");
    assert.equal(ring.widgets[0].loadingRing, true, "drawn as a turning ring, not the still sprite");
    // the tag fits its text, the title starts after it and keeps the row's right edge
    const tagLabel = text(find(first, "BG/Normal/Label_Tag")),
      tag = sprite(find(first, "BG/Normal/Tag")),
      title = find(first, "BG/Normal/Label_System");
    assert.ok(tagLabel.w_ >= estimateWidth(T("tasks.running"), 42));
    assert.equal(tag.w_, tagLabel.w_ + 70);
    assert.equal(title.pos[0] + text(title).w_, 124 + 1435);
    assert.ok(title.pos[0] > find(first, "BG/Normal/Tag").pos[0] + tag.w_ / 2);
    assert.equal(text(title).text, T("tasks.kind.mods.catalogReapply"));
    assert.equal(sprite(find(first, "Gauge/Fore")).w_, Math.round(PANEL.GAUGE_W * view[0].done));
    // the item cards, then the queued job: stacked downwards without overlap, all inside the frame
    const items = rest.filter((node) => node.name.startsWith("Item"));
    assert.equal(items.length, 4);
    const queued = rest.find((node) => node.name === "NUIMissionContent");
    const tops = [
      first.pos[1] + PANEL.ROW_H / 2,
      ...items.map((i, k) => i.pos[1] + itemHeight(view[0].items[k]) / 2),
      queued.pos[1] + PANEL.FOLDED_H / 2,
    ];
    const bottoms = [
      first.pos[1] - PANEL.ROW_H / 2,
      ...items.map((i, k) => i.pos[1] - itemHeight(view[0].items[k]) / 2),
      queued.pos[1] - PANEL.FOLDED_H / 2,
    ];
    for (let i = 1; i < tops.length; i++) assert.ok(tops[i] <= bottoms[i - 1] + 1e-6, `card ${i} below card ${i - 1}`);
    assert.ok(tops[0] <= -PANEL.TOP + 1e-6);
    assert.ok(bottoms.at(-1) >= -height + PANEL.BOTTOM - 1e-6);
    // a queued job reports no count yet: no Progress line
    assert.equal(find(queued, "Label_Locale_Count").active, false);
    assert.equal(find(queued, "Gauge").active, false);
  });

// a widget's box in its parent's units: [left, top, right, bottom]
const PX = [0, 0.5, 1, 0, 0.5, 1, 0, 0.5, 1],
  PY = [1, 1, 1, 0.5, 0.5, 0.5, 0, 0, 0];
const box = (node, w, [x, y] = [0, 0]) => {
  const left = x + node.pos[0] - PX[w.pivot ?? 4] * w.w_,
    top = y + node.pos[1] + (PY[w.pivot ?? 4] - 1) * -w.h_;
  return [left, top, left + w.w_, top - w.h_];
};

for (const locale of LOCALES)
  test(`the ring, the steps and Done end at one right edge, in ${locale}`, () => {
    use(locale);
    const view = panelView(JOBS);
    const { tree } = panelTree(view, PREFABS);
    const edge = PANEL.PAD + PANEL.ROW_W - PANEL.RIGHT;
    const ring = tree.children.find((c) => c.name === "LoadingIcon");
    assert.ok(Math.abs(box(ring, ring.widgets[0])[2] - edge) < 1e-6, "the running ring");
    const items = tree.children.filter((c) => c.name.startsWith("Item"));
    for (const item of items) {
      const step = item.children.find((c) => c.name === "Step");
      if (!step) continue;
      const w = text(step);
      assert.equal(w.align, 3, "right-aligned text");
      assert.ok(Math.abs(box(step, w, item.pos)[2] - edge) < 1e-6, `${w.text} at the right edge`);
    }
  });

for (const locale of LOCALES)
  test(`the item at hand keeps its name clear of its gauge, in ${locale}`, () => {
    use(locale);
    const view = panelView(JOBS);
    const { tree } = panelTree(view, PREFABS);
    const item = tree.children.filter((c) => c.name.startsWith("Item"))[2];
    const name = item.children.find((c) => c.name === "Name"),
      gauge = item.children.find((c) => c.name === "GaugeBack"),
      card = item.children.find((c) => c.name === "Card");
    const n = box(name, text(name)),
      g = box(gauge, sprite(gauge)),
      c = box(card, sprite(card));
    assert.ok(n[3] - g[1] >= 12, `${n[3] - g[1]} units between the name and the gauge`);
    assert.ok(n[1] <= c[1] && g[3] >= c[3], "both inside the card");
    assert.ok(text(name).ellipsis, "a long name ends in an ellipsis, never under the step");
    const step = item.children.find((c) => c.name === "Step");
    assert.ok(n[2] <= box(step, text(step))[0], "the name's box ends before the step's");
  });

for (const locale of LOCALES)
  test(`a queued job without a count folds to its title line, in ${locale}`, () => {
    use(locale);
    const jobs = [
      { id: "a", kind: "library.prune", state: "queued", progress: null, done: [] },
      { id: "b", kind: "mods.retryFailed", state: "queued", writes: "game", progress: null, done: [] },
      { id: "c", kind: "mods.toggle", state: "queued", progress: { current: 0, total: 4 }, done: [] },
    ];
    const view = panelView(jobs);
    assert.deepEqual(view.map(isFolded), [true, true, false]);
    const { tree } = panelTree(view, PREFABS);
    const rows = tree.children.filter((c) => c.name === "NUIMissionContent");
    const [a, b, c] = rows;
    assert.equal(sprite(find(a, "BG")).h_, PANEL.FOLDED_H - 4);
    assert.equal(sprite(find(c, "BG")).h_, PANEL.ROW_H - 4);
    // the card spans the folded height round the row's middle, the title on that middle
    const bg = find(a, "BG"),
      card = box(bg, sprite(bg));
    assert.ok(Math.abs(card[1] + card[3]) < 1e-6, "card centred on the row");
    const normal = find(a, "BG/Normal");
    assert.ok(Math.abs(bg.pos[1] + normal.pos[1]) < 1e-6, "title line on the middle");
    assert.equal(text(find(b, "BG/Normal/Label_System")).text, T("tasks.kind.mods.retryFailed"), "queued: no note");
  });

for (const locale of LOCALES)
  test(`a running writer the user started says not to quit, its title still fitting, in ${locale}`, () => {
    use(locale);
    const english = flat(load("en-US").webui.tasks);
    for (const kind of Object.keys(english)
      .filter((key) => key.startsWith("kind."))
      .map((key) => key.slice(5))) {
      const [held, startUp, reader] = panelView([
        { id: "a", kind, state: "running", writes: "library", progress: null, done: [] },
        { id: "b", kind, state: "running", writes: "game", resumable: true, progress: null, done: [] },
        { id: "c", kind, state: "running", writes: null, progress: null, done: [] },
      ]);
      assert.equal(held.name, T("tasks.noQuit", { name: T(`tasks.kind.${kind}`) }));
      assert.ok(held.name.includes(T(`tasks.kind.${kind}`)) && held.name !== T(`tasks.kind.${kind}`));
      assert.equal(startUp.name, T(`tasks.kind.${kind}`), "a start-up job ends at its step on a quit");
      assert.equal(reader.name, T(`tasks.kind.${kind}`), "a reader does not hold a quit");
      const { tree } = panelTree([held], PREFABS);
      const title = text(find(tree.children[2], "BG/Normal/Label_System"));
      assert.ok(estimateWidth(title.text, title.fontSize) <= title.w_, `${title.text} fits`);
    }
  });

test("with no jobs the panel says so, its bottom pattern scaled to the short frame", () => {
  use("zh-CN");
  const { tree, height } = panelTree(panelView([]), PREFABS);
  assert.equal(text(tree.children.find((c) => c.name === "Empty")).text, T("tasks.empty"));
  const bottom = find(tree.children[0], "Texture (1)"),
    deco = sprite(bottom);
  assert.ok(bottom.pos[1] + deco.h_ <= height / 2 - PANEL.DECO_TOP[1] + 1e-6, "the pattern stays inside the frame");
  assert.ok(Math.abs(deco.w_ / deco.h_ - 1458 / 692) < 0.01, "the pattern keeps its proportions");
});

test("Tasks sits one slot left of Settings, and takes Settings' slot in the Student Hall", () => {
  const boxes = {
    home: [44, 7, 40, 38],
    option: [157, 7, 39, 40],
    line: [100, 12, 3, 32],
    tasks: [270, 7, 39, 40],
    taskLine: [213, 12, 3, 32],
  };
  const row = topActionLayout(boxes, false);
  assert.deepEqual(row.tasks, boxes.tasks);
  assert.deepEqual(row.taskLine, boxes.taskLine);
  const hall = topActionLayout(boxes, true);
  assert.deepEqual(hall.option.slice(0, 2), [44 + (40 - 39) / 2, 7 + (38 - 40) / 2]);
  assert.deepEqual(hall.tasks.slice(0, 2), [157, 7]);
  assert.deepEqual(hall.taskLine.slice(0, 2), [100, 12]);
  const shifted = topActionLayout(boxes, false, 100);
  assert.equal(shifted.tasks[0] - row.tasks[0], 100 - 44);
});
