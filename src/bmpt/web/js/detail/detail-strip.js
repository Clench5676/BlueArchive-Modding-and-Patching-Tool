// Row 4 of the detail screen: a box, then cards on the columns of row 2's skill cards (faces, lobby dialogues, the
// chibi's actions), the pile in the button column.
import { notify } from "../ui/popup.js";
import * as NGUI from "../ui/ngui.js";
import { horizontalScroll } from "../ui/horizontal-scroll.js";
import { spineAllowed } from "../spine/spine-runtime.js";
import { withPreviewTimeout } from "../core/async.js";
import { previewError } from "./preview-error.js";
import * as ActionList from "../ui/action-list.js";
import { actionLabel, groupLabel, menuOf } from "./clip-names.js";
import { cardText, fitCards } from "../ui/action-list-layout.js";
import * as Lobby from "../lobby/lobby.js";
import { T } from "../core/i18n.js";
import { SPACE } from "../ui/ui-tokens.js";
import { row, toNode } from "../ui/ui-grid.js";
import { $, S, TAN10, glyphsOf, hitButton } from "../core/util.js";
import {
  node,
  sprite,
  label,
  rect,
  fitText,
  ORIGIN,
  toStage,
  tab,
  expression,
  expanded,
  exprScroll,
  mainPlayer,
  lobbySeq,
  modelActions,
  modelAction,
  modelMenu,
  modelLib,
  ModelLib,
  renderFailed,
  commit,
  applied,
  showable,
  showGen,
  thumbnailWork,
  picturesReady,
  fillPictures,
  alt,
  currentBase,
  costumeRun,
  costumesWanted,
  TEXTURES,
  setModelAction,
  setExpressionState,
  setExpanded,
  setExprScroll,
  setExprPics,
} from "./detail.js";
import { applyExpression } from "./detail-portrait.js";

// the action whose clips are being fetched ({ key, abort }): its group card shows the loading ring
let actionLoad = null;
let exprToken = 0;

// --- row 4 geometry ---
export const ROW4_Y = -552; // centre of row 4's visible part (Bg_W4: top -436, 234 tall, shadow below)
export const BOX_RIGHT = 332; // the first skill card's right edge: the box ends there
// UICharacterDetail's Skill + SkillData + Bg supply these columns, pitch and bodies; keep the prefab's own values.
const COLS = [461.9, 707.5, 953.2]; // skill cards 2-4 (Skill at 202 + SkillData x + Bg x 24)
const BTN_COL = [1079.8, 1284.8]; // expression pile's column, derived from the skill/Add cards; keep its x edges
const CARD = { w: 242, h: 209 }; // Common_Btn_BG like the skill cards; the visible body is h - 13 (shadow below)
const PITCH = 245.7;
export const LOCAL_FRAME = { cx: 0, cy: 0, w: 0, h: 0 };

// --- row 4: a box, then cards on the columns of row 2's skill cards, the pile in the button column ---
function expressionBoxes(count, nativeColumns = false) {
  // Slots retain the prefab's pitch (including its rounded column coordinates); the bodies remain 242 x 209.
  return row(
    Array.from({ length: count }, (_, i) => ({
      w: nativeColumns && i + 1 < COLS.length ? COLS[i + 1] - COLS[i] : PITCH,
      h: CARD.h,
    })),
    {
      x: COLS[0] - CARD.w / 2,
      y: -ROW4_Y + 6.5 - CARD.h / 2,
    },
  ).boxes.map((b) => ({ ...b, w: CARD.w }));
}
function pileBoxes() {
  const w = BTN_COL[1] - BTN_COL[0] - SPACE.l;
  // Overlapping row slots fan the pile inside its retained column; each step is SPACE.s across and in height.
  return row(
    Array.from({ length: 3 }, (_, i) => ({ w: SPACE.s, h: CARD.h - i * SPACE.s })),
    {
      x: BTN_COL[1] - w - 2 * SPACE.s,
      y: -ROW4_Y + 6.5 - CARD.h / 2,
    },
  ).boxes.map((b) => ({ ...b, w }));
}
// (captionFit: the caption's fit, drawn captionY over the card's centre, as the action bar's cards fit their names;
// loading: the game's loading ring turns in the caption's place, as on a button whose action is still loading)
export function stripCard(
  name,
  x,
  {
    w = CARD.w,
    h = CARD.h,
    thumb: pic = null,
    selected = false,
    dim = false,
    caption = "",
    body = "",
    placeholder = false,
    tone = "#ffffff",
    captionFit = null,
    captionY = 0,
    loading = false,
  } = {},
) {
  const vis = h - 13,
    cy = 6.5; // the visible body's centre sits 6.5 units above the widget's centre
  const kids = [
    node(
      "Line",
      [1, cy],
      [sprite("Common_Line_Raius20px", w - 10, vis, { incline: 10, depth: 9, color: "#bebebe/1.00" })],
    ),
  ];
  if (pic)
    kids.push(
      node(
        "Thumb",
        [1, cy],
        [
          {
            clipImage: pic,
            w_: w - 18,
            h_: vis - 8,
            pivot: 4,
            depth: 8,
            incline: 10,
            radius: 16,
            color: "#ffffff/1.00",
            imgSize: "cover",
            imgPosition: "center 50%",
          },
        ],
      ),
    );
  if (dim)
    kids.push(
      node(
        "Dim",
        [1, cy],
        [sprite("Common_Bg_Raius20px", w - 12, vis - 4, { incline: 10, depth: 10, color: "#000000/0.50" })],
      ),
    );
  if (caption) {
    const f = captionFit || fitText(caption, 56, 28, w - 50),
      ink = dim ? "#ffffff/1.00" : "#2c4564/1.00";
    const shown = label(f.text, f.fs, ink, w - 30, f.fs * 1.3 * f.lines, { depth: 12 });
    kids.push(
      node(
        "Caption",
        [0, cy + (loading ? 0 : captionY)],
        [loading ? { loadingRing: true, w_: 82, h_: 82, pivot: 4, depth: 12, color: ink, fallback: shown } : shown],
      ),
    );
  }
  if (body) {
    // a dialogue line: centred on the card, as large as fits in up to four lines; a longer line ends in an ellipsis
    // (the subtitle shows it in full). A placeholder (no line in the UI language) is grey and slanted.
    let f = fitText(body, 40, 32, w - 52, 4),
      cut = body;
    while (f.lines === 1 && glyphsOf(f.text, f.fs) > w - 52 && cut.length > 4) {
      cut = cut.slice(0, -2).trimEnd();
      f = fitText(`${cut}…`, 40, 32, w - 52, 4);
    }
    const ink = placeholder ? "#8f9aa6/1.00" : "#2c4564/1.00";
    kids.push(
      node(
        "Body",
        [0, cy],
        [label(f.text, f.fs, ink, w - 40, f.fs * 1.3 * f.lines, { depth: 12, italic: placeholder })],
      ),
    );
  }
  if (selected)
    kids.push(
      node(
        "Select",
        [1, cy],
        [sprite("Card_Select_Line_Blue", w + 6, vis + 8, { incline: 10, depth: 20 })],
        [
          node(
            "Check",
            [w / 2 - 30 + (vis / 2 - 26) * TAN10, vis / 2 - 26],
            [sprite("Common_Icon_Check_B", 56, 54, { type: 0, depth: 21 })],
          ),
        ],
      ),
    );
  return node(
    name,
    [x, ROW4_Y - cy],
    [sprite("Common_Btn_BG", w, h, { incline: 10, depth: 7, color: `${tone}/1.00` })],
    kids,
  );
}

// row 4 per tab: the portrait's faces, the memorial lobby's dialogues
function row4Items() {
  if (tab === "lobby") {
    // the dialogue's lines in the UI language, else a numbered placeholder
    return (Lobby.meta()?.sequences || []).map((sq) => {
      const text = Lobby.textOf(sq).join(" "),
        none = T("lobby.dialogue", { n: sq.n });
      return text ? { key: sq.anim, body: text } : { key: sq.anim, body: none, placeholder: true };
    });
  }
  if (tab === "model" && modelMenu) return barItems();
  // a story model's own cutscene clips read "Story 1", "Story 2", ...
  if (tab === "model")
    return modelActions.map((a) => ({
      key: a,
      caption: /^story\d+$/.test(a) ? T("model.action.story", { n: a.slice(5) }) : T(`model.action.${a}`),
    }));
  if (!spineAllowed()) return [];
  const base = currentBase();
  if (!base) return [];
  const names = mainPlayer?.skeleton
    ? mainPlayer.skeleton.data.animations
        .map((a) => a.name)
        .filter((n) => /^\d{2}$/.test(n))
        .sort()
    : [];
  const request = thumbnailWork.thumb;
  return (names.length ? names : ["00"]).map((e) => ({ key: e, thumb: () => request(base, "face", e) }));
}
// The 3D tab's action bar (docs/webui/model-actions.md): a card per action group the model has, its names one size;
// the playing action's group card is selected and shows that action's name alone, or the loading ring while its clips
// are fetched.
const playingAction = () => modelMenu?.find((a) => a.key === modelAction) || null;
function barItems() {
  const groups = menuOf(modelMenu),
    playing = playingAction(),
    names = groups.map((g) => groupLabel(g.id, T)),
    fits = fitCards(names);
  ActionList.warm();
  return groups.map((g, i) => {
    const now = playing?.group === g.id ? actionLabel(playing, T) : null,
      text = cardText(fits[i], now);
    return {
      key: `group:${g.id}`,
      group: g,
      caption: now || names[i],
      captionFit: text.name,
      captionY: text.nameY,
      loading: !!now && actionLoad?.key === playing.key,
    };
  });
}
// the bar's key of an action model.js names when it changes by itself (a tap's reaction, the idle after it)
export const actionKey = (name) =>
  (modelMenu?.find((a) => a.key === name) || modelMenu?.find((a) => a.id === name && (a.form || 0) <= 1))?.key || name;
const row4Selected = () =>
  tab === "lobby"
    ? lobbySeq
    : tab === "model" && modelMenu
      ? `group:${playingAction()?.group}`
      : tab === "model"
        ? modelAction
        : expression;
// A group card pressed: its action list opens over it (action-list.js); the same card closes it, another group's
// switches to that one. A picked action plays (pickAction) and the list closes.
let barCards = new Map(); // group card key -> its centre x in units, unscrolled
function toggleGroup(item) {
  if (ActionList.openGroup() === item.key) {
    ActionList.close();
    return;
  }
  const anchor = () => {
    const x = barCards.get(item.key) - (document.querySelector(".expr-clip")?.scrollLeft || 0) / S;
    return { left: x - CARD.w / 2, right: x + CARD.w / 2, top: ROW4_Y + (CARD.h - 13) / 2 };
  };
  ActionList.open({
    host: $("ngui-expr").parentElement,
    group: item.key,
    anchor,
    sections: item.group.sections,
    selected: modelAction,
    onPick: (key) => {
      const a = modelMenu?.find((x) => x.key === key);
      if (tab === "model" && a) void pickAction(a);
    },
  }).catch(renderFailed);
}
// An action picked on the bar plays at once when its clips are there (the model's GLB carries today's actions).
// Otherwise they are fetched (model.js loadClips: the model route's ?clip=) while its group card shows the
// loading ring, then it plays. The fetch counts only its own foreground time (withPreviewTimeout); leaving the
// 3D tab, the alt or the screen gives it up. A failure leaves the previous action playing and says the
// preview is unavailable.
const CLIP_CAP = 15000;
async function pickAction(a) {
  stopActionLoad();
  const M = await ModelLib();
  if (M.playAction(a)) {
    setModelAction(a.key);
    keepScroll();
    renderExpressions();
    return;
  }
  const v = alt(),
    run = costumeRun,
    before = M.current() ?? modelAction,
    abort = new AbortController();
  const load = (actionLoad = { key: a.key, abort });
  setModelAction(a.key);
  keepScroll();
  renderExpressions();
  let played = false,
    failure = null;
  try {
    played =
      (await withPreviewTimeout(M.loadClips(a.clips, { signal: abort.signal }), CLIP_CAP, "model clips", {
        signal: abort.signal,
      })) &&
      costumesWanted(v, run) &&
      actionLoad === load &&
      M.playAction(a);
  } catch (err) {
    failure = err;
    abort.abort(); // past its deadline: the request too
  }
  if (actionLoad !== load) return; // given up: another action picked, the model shown again or the tab left
  actionLoad = null;
  setModelAction(played ? a.key : actionKey(M.current() ?? before));
  if (failure) {
    previewError(failure, a.clips.join(","), "model-clip");
    notify(T("detail.failed"), { kind: "error", error: failure });
  }
  if (costumesWanted(v, run)) {
    keepScroll();
    renderExpressions();
  }
}
export function stopActionLoad() {
  const load = actionLoad;
  actionLoad = null;
  load?.abort.abort();
}
function row4Pick(key) {
  // the card follows the dialogue through Lobby.onTalk: selected while it plays, let go when it ends
  if (tab === "lobby") {
    if (Lobby.playing() === key) Lobby.stop();
    else Lobby.play(key);
  } else if (tab === "model") {
    setModelAction(key);
    modelLib?.then((M) => M.play(key));
  } else {
    setExpressionState(key);
    applyExpression();
  }
}
// play every dialogue in order; the cards and the play-all button follow along (Lobby.onTalk)
export function togglePlayAll() {
  if (Lobby.playingAll()) Lobby.stop();
  else Lobby.playAll(lobbySeq);
}

export function renderExpressions() {
  return drawExpressions().catch(renderFailed);
}
async function drawExpressions() {
  const my = ++exprToken,
    gen = showGen;
  const ok = () => showable("expr", my, exprToken, gen);
  const host = $("ngui-expr");
  // the 3D tab's action bar: every group card in a row that scrolls, no pile; its open list stays over a redraw of
  // the bar and closes when the bar goes
  const bar = tab === "model" && !!modelMenu;
  if (!bar) ActionList.close();
  const list = row4Items(),
    sel = row4Selected(),
    unfolded = expanded || bar,
    pics = [],
    thumbs = [];
  const picture = (it) => (typeof it.thumb === "function" ? it.thumb() : it.thumb);
  const placed = (name, box, options) =>
    Object.assign(stripCard(name, 0, { w: box.w, h: box.h, ...options }), { pos: toNode(box, 4, LOCAL_FRAME) });
  const cardOf = (it, box) =>
    placed(`Item_${it.key}`, box, {
      thumb: picture(it),
      body: it.body,
      placeholder: it.placeholder,
      caption: it.caption,
      selected: it.key === sel,
      captionFit: it.captionFit,
      captionY: it.captionY,
      loading: it.loading,
    });
  const cards = [];
  if (unfolded || list.length <= 4) {
    const boxes = expressionBoxes(list.length + (expanded && !bar ? 1 : 0));
    list.forEach((it, i) => cards.push({ tree: cardOf(it, boxes[i]), item: it }));
    if (bar) barCards = new Map(cards.map((cd) => [cd.item.key, cd.tree.pos[0]]));
    if (expanded && !bar)
      cards.push({
        tree: placed("Fold", boxes.at(-1), { caption: T("fold.label"), tone: "#eef4f8" }),
        fold: true,
        name: T("fold.label"),
      });
  } else {
    // three cards; a chosen item further along takes the third card so it stays in view
    const shown = list.slice(0, 3);
    const chosen = list.find((it) => it.key === sel);
    if (chosen && !shown.includes(chosen)) shown[2] = chosen;
    const boxes = expressionBoxes(shown.length, true);
    shown.forEach((it, i) => cards.push({ tree: cardOf(it, boxes[i]), item: it }));
    // the pile fills the button column: two plain cards behind, the top one darkened with the count
    const rest = list.filter((it) => !shown.includes(it));
    const pile = pileBoxes();
    cards.push({ tree: placed("Pile_2", pile[2], { tone: "#dfe8ef" }), pile: true, z: 100 });
    cards.push({ tree: placed("Pile_1", pile[1], { tone: "#eef4f8" }), pile: true, z: 140 });
    cards.push({
      tree: placed("Pile_0", pile[0], {
        thumb: picture(rest[0]),
        dim: !!rest[0].thumb,
        caption: `+${rest.length}`,
        tone: rest[0].thumb ? "#ffffff" : "#d3dee8",
      }),
      pile: true,
      name: T("chips.more", { n: rest.length }),
      z: 180,
    });
  }
  // clip: the row's height, from the box's slanted right edge to the row's inner right edge
  const Lx = 300,
    Rx = 1316,
    TOPU = -436,
    BOTU = -670;
  const [cl, ct] = toStage(Lx, TOPU);
  const clip = document.createElement("div");
  clip.className = "expr-clip";
  Object.assign(clip.style, {
    left: `${cl}px`,
    top: `${ct}px`,
    width: `${(Rx - Lx) * S}px`,
    height: `${(TOPU - BOTU) * S}px`,
  });
  const edgeTop = BOX_RIGHT + (TOPU - ROW4_Y) * TAN10 + 5 - Lx,
    edgeBottom = BOX_RIGHT + (BOTU - ROW4_Y) * TAN10 + 5 - Lx;
  clip.style.clipPath = `polygon(${edgeTop * S}px 0, 100% 0, 100% 100%, ${edgeBottom * S}px 100%)`;
  const track = document.createElement("div");
  track.className = "expr-track";
  clip.appendChild(track);
  const origin = [ORIGIN[0] - cl, ORIGIN[1] - ct];
  await Promise.all(
    cards.map(async (cd) => {
      const view = await NGUI.render(cd.tree, track, {
        scale: S,
        origin,
        z: cd.z || 30,
        textureNames: TEXTURES,
        deferImages: true,
      });
      pics.push(...view.images);
      thumbs.push(...view.thumbnails);
      const body = view.nodes.get(cd.tree.name)?.els[0];
      if (!body || (cd.pile && cd.tree.name !== "Pile_0")) return;
      const strip = hitButton(
        track,
        rect(body),
        () => {
          if (cd.item?.group) toggleGroup(cd.item);
          else if (cd.item) {
            row4Pick(cd.item.key);
            keepScroll();
            renderExpressions();
          }
          if (cd.pile) {
            setExpanded(true);
            setExprScroll(0);
            renderExpressions();
          }
          if (cd.fold) {
            setExpanded(false);
            renderExpressions();
          }
        },
        cd.name || cd.item?.body || cd.item?.caption || (cd.item ? faceName(cd.item.key) : ""),
        260,
      );
      // the strip's cards pick one item: the picked one is pressed; a group card opens its list (action-list.js), and a
      // press on one is not outside the open list
      if (cd.item) strip.setAttribute("aria-pressed", String(cd.item.key === sel));
      if (cd.item?.group) strip.dataset.actionGroup = cd.item.group.id;
    }),
  );
  if (!ok()) return; // a newer render took over (e.g. the tab changed while faces were rendering)
  if (my === exprToken) {
    setExprPics([picturesReady(pics, clip)]);
    fillPictures(thumbs, clip);
  }
  if (unfolded) {
    clip.classList.add("expanded");
    const last = cards.at(-1).tree;
    const width = (Rx - Lx) * S,
      contentW = (last.pos[0] + CARD.w / 2 + SPACE.m - Lx) * S;
    track.style.width = `${contentW}px`;
    // the wheel, a mouse drag and touch scroll the strip as the popups' card rows do (Popup.scrollArea); a drag
    // picks nothing
    horizontalScroll(clip, width, contentW);
    // an open action list follows its card; it closes once the card has left the row
    if (bar)
      clip.addEventListener("scroll", () => {
        setExprScroll(clip.scrollLeft);
        const x = barCards.get(ActionList.openGroup()) - clip.scrollLeft / S;
        if (x < Lx || x > Rx) ActionList.close();
        else ActionList.follow();
      });
  }
  commit(() => {
    if (!ok()) return;
    applied.expr = my;
    host.replaceChildren(clip);
    if (unfolded) clip.scrollLeft = exprScroll;
  });
}
// a portrait face's name, as the camera's expression list has it
const faceName = (key) => (key === "00" ? T("photo.natural") : `${T("photo.expressions")} ${key}`);
const keepScroll = () => {
  setExprScroll(document.querySelector(".expr-clip")?.scrollLeft || 0);
};
