// The detail screen's static screen: background, arrows, tabs, the right panel and the nameplate frame, drawn from
// the game's prefabs, with the switch captions and the screen's hit areas.
import * as ThumbnailCapture from "../thumbnail-capture/controller.js";
import { isSpecial } from "./special-view.js";
import * as NGUI from "../ui/ngui.js";
import { resizeButton } from "../ui/button-layout.js";
import { explorerLayout } from "../ui/explorer-button.js";
import { attachHint } from "../ui/hover-hint.js";
import * as MemorialLobby from "../memorial-lobby/memorial-lobby.js";
import { listSep } from "../mods/mod-switch.js";
import { T, platform } from "../core/i18n.js";
import { colourSwitchCard } from "../mods/switch-card.js";
import { hasUnseen as hasFresh, switchUnseen } from "./unseen-assets.js";
import { modOriginalName } from "../core/names.js";
import { COLOUR, CONTROL, SPACE, TYPE } from "../ui/ui-tokens.js";
import { container, row, stack, textItem, toNode } from "../ui/ui-grid.js";
import { detailStatus, selectionStatus, isApplied, coreOf, hasKind, switchKindOf } from "../mods/switch-sources.js";
import { $, S, TAN10, advanceOf, at, clone, glyphsOf, hitButton, off, text, widget } from "../core/util.js";
import {
  find,
  node,
  label,
  sprite,
  rect,
  fitText,
  fitIn,
  ORIGIN,
  INFO,
  layouts,
  shownStudent,
  ctx,
  altId,
  tab,
  renderFailed,
  commit,
  applied,
  showable,
  showGen,
  thumbnailWork,
  thumb,
  chibiPicture,
  picturesReady,
  fillPictures,
  originalName,
  fullName,
  customAliases,
  aliasList,
  alt,
  isFav,
  hasMemorialLobby,
  hasChibi,
  repairedId,
  repairAction,
  currentBase,
  applyChecking,
  costumeBlock,
  unfitCostume,
  tabKind,
  switchKind,
  dateOf,
  crcOf,
  shownSource,
  current,
  candidateLabel,
  sourceLabel,
  gameFile,
  baseName,
  TEXTURES,
  actionState,
  clipped,
  showTab,
  setScreenPics,
} from "./detail.js";
import {
  busy,
  call,
  reveal,
  copyName,
  editModName,
  toggleFav,
  addMods,
  shownMod,
  runUpdate,
  runCompatibility,
  jumpRepaired,
  runExport,
  applyCandidate,
} from "./detail-actions.js";
import { ROW4_Y, BOX_RIGHT, LOCAL_FRAME, togglePlayAll } from "./detail-strip.js";
import { PLATE_PATH } from "./detail-chips.js";
import { modsOn, openPopup } from "./detail-switchers.js";
import { openAliases } from "./detail-aliases.js";
import { showDetails } from "./detail-files.js";
import { memorialLobbyCard } from "./detail-memorial-lobby-tab.js";
import { setFullscreen } from "./detail-fullscreen.js";
import { thumbnailBridge } from "./detail-thumbnail.js";

let renderToken = 0;

// --- the static screen: background, arrows, tabs, right panel, nameplate frame ---
function screenTree() {
  const t = clone(layouts.list);
  off(
    t,
    "Top",
    "NUITop_Scenes",
    "LeftInfo/CharInfo_Disable",
    "LeftInfo/CharCombatStyle",
    "RightInfo/Target/Disable",
    "RightInfo/Target/Active/2_LevelUp",
    "RightInfo/Target/Active/3_Transcendence",
    "RightInfo/Target/Active/FullCollier",
    "RightInfo/Target/Active/Effect",
  );
  // The right panel is its Target only: buttons a game update adds beside it are game features the App does not
  // have (the 2026-11 update's Btn_Engrave, whose label is the prefab's untranslated text).
  for (const n of at(t, "RightInfo")?.children || []) if (n.name !== "Target") n.active = false;
  const v = alt(),
    sk = switchKind(),
    c = current(sk || "portrait"),
    kind = tabKind();
  // layer 1 of the panel: measured on the game screenshots it lets about 55 % of the background through
  widget(t, "RightInfo/Target/Active/Bg").color = "#0c284b/0.45";

  // tabs: portrait / memorial lobby / chibi (disabled where unavailable)
  const tabs = [
    ["Tab_1", "tab.portrait", "portrait", hasKind(v, "portrait")],
    ["Tab_2", "tab.memorialLobby", "memorialLobby", hasMemorialLobby(v)],
    ["Tab_3", "tab.chibi", "chibi", hasChibi(v)],
  ];
  // the three captions share one size: the largest at which every one fits its tab on one line
  const tabLabels = tabs.map(([, key, id]) => T(isSpecial(v) && id === "memorialLobby" ? "special.background" : key));
  const tabSize = NGUI.fitGroup(tabLabels, 52, 36, 360, 1)[0].fs;
  for (const [index, [node_, _key, id, enabled]] of tabs.entries()) {
    const base = `RightInfo/Target/Active/CharacterTab/${node_}`;
    const label = tabLabels[index];
    for (const state of ["Tab_On", "Tab_Off"]) text(t, `${base}/${state}/Label`, label, { w_: 400, fontSize: tabSize });
    if (!enabled) widget(t, `${base}/Tab_Off/Label`).color = "#ffffff/0.35";
    at(t, `${base}/Tab_On`).active = id === tab;
    at(t, `${base}/Tab_Off`).active = id !== tab;
    const dot = find(t, `${base}/${id === "portrait" ? "Dot" : id === "memorialLobby" ? "Dot (1)" : "Dot (2)"}`);
    if (dot) dot.active = hasFresh(v, switchKindOf(id));
  }
  // the separator on a tab's right edge goes when either neighbour is active
  if (tab !== "portrait") {
    const l = find(t, "RightInfo/Target/Active/CharacterTab/Tab_1/Tab_Off/Line");
    if (l) l.active = tab !== "memorialLobby";
  }
  {
    const l = find(t, "RightInfo/Target/Active/CharacterTab/Tab_2/Tab_Off/Line (1)");
    if (l) l.active = tab === "portrait";
  }
  // row 1: portrait information
  text(
    t,
    `${INFO}/Title_Bg/Label_Locale`,
    T(isSpecial(v) && tab === "memorialLobby" ? "special.background" : `info.title.${tab}`),
    { w_: 600 },
  );
  text(t, `${INFO}/Detail_Btn/Label_Locale`, T("info.details"));
  const crcState = crcOf(v, kind);
  const crc =
    crcState === "fixed"
      ? ["crc.fixed", COLOUR.success]
      : crcState === "needs_fix"
        ? ["crc.needed", COLOUR.warning]
        : ["crc.original", "#000000"];
  const now = tab === "portrait" ? c.label : sourceLabel(v, kind);
  const stats = [
    ["Label_Life", "info.current", "Data_Widget/Data", now],
    [
      "Label_Attact",
      "info.status",
      "Data_Widget/Data",
      !coreOf(v, kind) ? T("import.reason.no_target") : detailStatus(v, kind) ? T(detailStatus(v, kind)) : "—",
    ],
    ["Label_Defense", "info.gameFile", "Data", dateOf(v, kind) || "—"],
    ["Label_Healing", "info.crc", "Data_Widget/Data", T(crc[0]), crc[1]],
  ];
  // the value column starts after the longest caption of its column
  const capW = (keys) => Math.max(...keys.map((k) => advanceOf(T(k), 42))) + 24;
  const leftCap = capW(["info.current", "info.gameFile"]),
    rightCap = capW(["info.status", "info.crc"]);
  const rooms = stats.map(([name_]) => {
    const right = name_ === "Label_Attact" || name_ === "Label_Healing";
    return (right ? 1240 - 773 - rightCap : 773 - 293 - leftCap) - 24;
  });
  const editingMod = shownMod();
  if (editingMod) rooms[0] -= 62;
  stats.forEach(([name_, caption, dataPath, value, color], i) => {
    text(t, `${INFO}/Status/${name_}`, T(caption), { w_: 300 });
    const right = name_ === "Label_Attact" || name_ === "Label_Healing";
    const dataNode = at(t, `${INFO}/Status/${name_}/${dataPath.split("/")[0]}`);
    dataNode.pos[0] = right ? rightCap : leftCap;
    // Fit each value independently; the compact rows use an ellipsis once this value reaches the size floor.
    const original = i === 0 && editingMod?.mod.alias ? modOriginalName(editingMod.mod) : "";
    const fit = fitText(String(value), original ? 34 : 42, 28, rooms[i], 1);
    text(t, `${INFO}/Status/${name_}/${dataPath}`, fit.text, {
      w_: rooms[i],
      fontSize: fit.fs,
      ellipsis: true,
      ariaLabel: String(value),
      ...(original ? { h_: 42 } : {}),
      ...(color ? { color: `${color}/1.00` } : {}),
    });
    if (original) {
      const main = at(t, `${INFO}/Status/${name_}/${dataPath}`);
      main.pos[1] += 15;
      const secondary = fitText(original, 24, 20, rooms[i], 1);
      dataNode.children.push(
        node(
          "OriginalName",
          [main.pos[0], main.pos[1] - 38],
          [
            label(secondary.text, secondary.fs, "#2d4664/0.65", rooms[i], 28, {
              pivot: 3,
              depth: 22,
              ellipsis: true,
              ariaLabel: original,
            }),
          ],
        ),
      );
    }
    if (i === 0 && editingMod)
      dataNode.children.push(
        node(
          "ModAliasBtn",
          [Math.min(glyphsOf(String(value), fit.fs), rooms[i]) + 32, original ? 15 : 0],
          [sprite("Cafe_Icon_NameEdit", 46, 46, { type: 0, depth: 22, color: "#2d4664/1.00" })],
        ),
      );
    const pot = find(t, `${INFO}/Status/${name_}/Data_Widget/Potential_Widget`);
    if (pot) pot.active = false;
  });
  // the button beside the information: show the game file in Finder / Explorer (secondary style, like Cancel)
  const up = `${INFO}/Status/Btn_Up_Wrapper/Btn_Up`;
  widget(t, up).sprite = "Common_Empty";
  off(t, `${up}/Lock`, `${up}/Dot`);
  widget(t, `${up}/BG (1)`).color = "#dbeef5/1.00";
  for (const k of at(t, `${up}/BG (1)`).children) k.widgets[0].sprite = "Common_Btn_Normal_B_C_Pt";
  resizeButton(at(t, `${up}/BG (1)`));
  // the caption wraps onto up to three lines inside the 180-high body before it shrinks (explorer-button.js)
  const open_ = explorerLayout(T(platform === "mac" ? "open.mac" : "open.win"));
  text(t, `${up}/Label_Locale`, open_.text, { fontSize: open_.fs, w_: 160, h_: open_.label[2], color: "#2d5164/1.00" });
  at(t, `${up}/Label_Locale`).pos = [open_.label[0], open_.label[1]];
  at(t, up).children.push(
    node("Folder", open_.folder, [
      { texture: "svg:folder", w_: 56, h_: 50, pivot: 4, depth: 12, color: "#ffffff/1.00" },
    ]),
  );

  // row 2: four actions on what the shown tab's game files carry. Title and icon centred as a group on the card's
  // slanted midline.
  // Keep these action cards and row 3's Add card at their existing prefab-based positions and sizes.
  const isMod = !!sk && c.kind === "mod";
  const actions = [
    ["SkillData_EX", "compatibility.check", "compare", isMod && !busy],
    ["SkillData_EX (1)", repairAction(c), "update", isMod && !busy],
    ["SkillData_EX (2)", "export.title", "extract", !busy],
    [
      "SkillData_EX (3)",
      "popup.apply",
      "apply",
      !!sk &&
        !!coreOf(v, sk) &&
        !busy &&
        !isApplied(v, sk, shownSource(v, sk)) &&
        !unfitCostume(v, sk, shownSource(v, sk)),
      applyChecking(v, sk),
    ],
  ];
  // the row's white section spans x 58..1320 (Bg_W2 at 689, 1262 wide); cards keep the first card's 37-unit margins
  const ROW_L = 58 + 37,
    ROW_R = 1320 - 37,
    GAP = 6,
    CW = (ROW_R - ROW_L - 3 * GAP) / 4;
  actions.forEach(([card], i) => {
    const base = `${INFO}/Skill/${card}`,
      bg = at(t, `${base}/Bg`);
    at(t, base).pos[0] = ROW_L + CW / 2 + i * (CW + GAP) - 202 - bg.pos[0];
    resizeButton(bg, CW);
    find(t, `${base}/Bg/Bg_Line`).widgets[0].w_ = CW - 10;
  });
  off(t, `${INFO}/Skill/Btn_Up_Wrapper`); // the install button is not used
  // the four titles share one size: the largest at which every one fits its card after wrapping onto two lines
  const titles = NGUI.fitGroup(
    actions.map(([, key]) => T(key)),
    40,
    28,
    CW - 60,
    2,
    { maxH: 90 },
  );
  for (const [index, [card, _key, glyph, enabled, loading]] of actions.entries()) {
    const base = `${INFO}/Skill/${card}`;
    actionState(at(t, base), enabled, layouts.disabled);
    off(t, `${base}/Normal/Label_SkillType (1)`, `${base}/Normal/Line (1)`);
    const max = find(t, `${base}/Normal/Icon_Max`);
    if (max) max.active = false;
    const bgX = at(t, `${base}/Bg`).pos[0] + 1,
      midY = 158.3 - 126; // Bg_Line centre: the card's visible body
    const title = titles[index];
    const disabledInk =
      layouts.disabled && find(layouts.disabled, "Label_Locale")?.widgets.find((w) => "text" in w)?.color;
    text(t, `${base}/Normal/Label_SkillType`, title.text, {
      fontSize: title.fs,
      w_: CW - 56,
      h_: Math.max(46, title.fs * title.lines * 1.3),
      color: !enabled && disabledInk ? disabledInk : "#466399/1.00",
    });
    at(t, `${base}/Normal/Label_SkillType`).pos = [bgX + 66 * TAN10, midY + 66];
    const icon = at(t, `${base}/Normal/SkillIconBg`);
    icon.pos = [bgX - 30 * TAN10, midY - 30];
    icon.widgets = [];
    icon.children = icon.children.filter((child) => child.name === "Texture");
    const picture = widget(t, `${base}/Normal/SkillIconBg/Texture`);
    delete picture.texture;
    const [atlas, sprite] = {
      compare: ["Common", "School_Icon_Schedule"],
      update: ["Common", "Common_Icon_All_Download"],
      extract: ["Common", "Common_Icon_Inventory_Equip"],
      apply: ["Common", "Common_Icon_All_Download"],
    }[glyph];
    Object.assign(picture, { cls: "UISprite", sprite, atlas, type: 0, w_: 109, h_: 109, keepAspect: true });
    if (!enabled && disabledInk) picture.color = disabledInk;
    // the shown costume's check under way: the game's loading ring turns in the icon's place, the title stays
    if (loading)
      at(t, `${base}/Normal/SkillIconBg/Texture`).widgets = [
        {
          loadingRing: true,
          w_: 82,
          h_: 82,
          pivot: 4,
          depth: picture.depth,
          color: picture.color || "#466399/1.00",
          fallback: { ...picture },
        },
      ];
  }
  // row 3: portrait box, the switch card (the game's light weapon card), add portrait. The box's visible body (its
  // straight left side, its slanted right edge) has its centre at mid-height 3 units left of the box's; the title is
  // centred there.
  const boxTitle = fitIn(
    T(isSpecial(v) && tab === "memorialLobby" ? "special.background" : `row.box.${tab}`),
    48,
    42,
    160,
  );
  text(t, `${INFO}/Weapon/WeaponType/Label_WeaponType`, boxTitle.text, {
    fontSize: boxTitle.fs,
    w_: 200,
    h_: 60 * boxTitle.lines,
  });
  at(t, `${INFO}/Weapon/WeaponType/Label_WeaponType`).pos = [-3, -2];
  off(t, `${INFO}/Weapon/WeaponType/Icon`);
  const card = `${INFO}/Weapon/Equip_1/UIEquipmentCard/Btn_Target`;
  if (!sk || !switchUnseen(shownStudent, v, sk, modsOn())) off(t, `${INFO}/Weapon/Equip_1/UIEquipmentCard/Dot`);
  off(
    t,
    `${card}/Normal_NotEquip`,
    `${card}/Lock`,
    `${card}/Normal/Icon`,
    `${card}/Normal/Icon_Star`,
    `${card}/Normal/Card_Equipment`,
    `${card}/Normal/Card_Bg/Deco`,
  );
  text(t, `${card}/Normal/Card_Level`, "");
  // window = the card's inner line (Common_Line_Raius20px 766 x 201 at (1, 6)); the decoration and the picture are
  // clipped to it
  const win = { w: 766, h: 201, r: 20 };
  const deco = layouts.textures.Img_Deco_Weapon;
  at(t, `${card}/Normal`).children.push(
    ...(deco
      ? [clipped("DecoClip", [1, 6], deco, win, { dx: 30.8 - 1, dy: 7.5 - 6, w: 820, h: 198 }, { depth: 9 })]
      : []),
    tab === "portrait"
      ? clipped(
          "Bust",
          [1, 6],
          thumb(currentBase(), "bust"),
          win,
          { dx: -383 + 202 - 40, dy: -67.5, w: 404, h: 404 },
          { depth: 10, mask: "linear-gradient(90deg, #000 70%, transparent 98%)" },
        )
      : // wider than the window's slanted left edge; the memorial lobby fades out before the labels so their contrast is known
        clipped(
          "Bust",
          [1, 6],
          tab === "chibi" ? chibiPicture(current("chibi")) : memorialLobbyCard(v),
          win,
          { dx: -383 + 216, dy: 0, w: tab === "chibi" ? 253 : 450, h: 253 },
          {
            depth: 10,
            placeholder: T("import.previewUnavailable"),
            edge: 0.7,
            mask: `linear-gradient(90deg, transparent 0, #000 10%, #000 70%, transparent ${tab === "chibi" ? 96 : 82}%)`,
          },
        ),
  );
  // the wrapper holds an enabled (cyan) and a disabled (grey) body and label: the enabled ones while the tab has
  // something to add and switch to (switchKind), else the disabled ones
  const keep = switchKind() ? 1 : 2;
  let bodies = 0,
    labels = 0;
  // the cyan body is 205 wide, slanted
  const add = fitIn(T(isSpecial(v) && tab === "memorialLobby" ? "special.add" : `row.add.${tab}`), 52, 30, 160, 3);
  for (const k of at(t, `${INFO}/Weapon/Btn_Up_Wrapper/Btn_Up`).children) {
    if (k.name === "BG (1)") {
      resizeButton(k);
      bodies += 1;
      if (bodies !== keep) k.active = false;
    }
    if (k.name === "Label_Locale") {
      labels += 1;
      if (labels !== keep) k.active = false;
      else Object.assign(k.widgets[0], { text: add.text, fontSize: add.fs, w_: 200, h_: 160 });
    }
    if (k.name === "Bg" || k.name === "Lock" || k.name === "Dot") k.active = false;
  }

  // row 4: a box like row 3's in the first column of the skill grid; the cards are rendered separately
  off(t, `${INFO}/Equip/Grid`, `${INFO}/Equip/Btn_Up_Wrapper`);
  const box = clone(find(t, `${INFO}/Weapon/WeaponType`));
  box.name = "ExprType";
  box.pos = [374.7, ROW4_Y];
  find(box, "BG/BG (1)").widgets[0].w_ = BOX_RIGHT - 126; // ends where the first skill card of row 2 ends
  find(box, "Line/Line").widgets[0].w_ = BOX_RIGHT + 3 - 123.7;
  const boxLabel = find(box, "Label_WeaponType");
  // the box's visible body, as row 3's, has its centre at mid-height 1.5 units left of the span's middle; the label is
  // centred there
  const row4 = fitIn(T(`row.four.${tab}`), 48, 42, 200);
  Object.assign(boxLabel.widgets[0], { text: row4.text, fontSize: row4.fs, w_: 240, h_: 60 * row4.lines });
  boxLabel.pos = [(87.5 + BOX_RIGHT) / 2 - 183.7 - 1.5, -2];
  at(t, `${INFO}/Equip`).children.push(box);

  // nameplate: favourite pin, name, the white panel for the alt chips; no stars, level, heart, squad or terrain
  const np = PLATE_PATH;
  off(
    t,
    `${np}/Icon_Star`,
    `${np}/Text_Lv`,
    `${np}/FavorBG`,
    `${np}/SquadType`,
    `${np}/Type`,
    `${np}/Terrain`,
    `${np}/Char_ProfileBtn`,
  );
  const fav = isFav(v);
  at(t, `${np}/Pin/Pin_On`).active = fav;
  at(t, `${np}/Pin/Pin_Off`).active = !fav;
  for (const state of ["Pin_On", "Pin_Off"]) {
    const base = `${np}/Pin/${state}`;
    at(t, base).pos[1] = 392;
    const lblNode = find(t, `${base}/Bg/Icon/Label_Locale`) || find(t, `${base}/Bg/Label_Locale`);
    const label = lblNode.widgets.find((w) => "text" in w),
      tw = advanceOf(T("fav.label"), label.fontSize);
    // Keep each state's sprite, baked shading, icon tint and local positions.
    // Only the translated caption and its right anchor expand the prefab.
    const oldWidth = label.w_;
    Object.assign(label, { text: T("fav.label"), w_: Math.max(oldWidth, tw + 4) });
    widget(t, `${base}/Bg`).w_ += label.w_ - oldWidth;
  }
  // Both names share the existing dark band, leaving room for the pencil after the main name.
  const original = originalName(v),
    shown = fullName(v),
    aliased = customAliases().length > 0;
  const { room } = aliasPlate(at(t, np), shown, aliased);
  if (aliased) {
    const secondary = fitText(original, 28, 24, room, 1);
    at(t, np).children.push(
      node(
        "Label_OriginalName",
        [110, 258],
        [
          label(secondary.text, secondary.fs, "#ffffff/0.62", room, 36, {
            pivot: 3,
            depth: 22,
            ellipsis: true,
            ariaLabel: original,
          }),
        ],
      ),
    );
  }
  // the dark plate keeps its bottom edge and loses the level row; the game's shard texture is clipped to it
  widget(t, `${np}/BG`).h_ = 120;
  off(t, `${np}/BG/Deco`);
  const poli = layouts.textures.Img_Deco_Poli;
  if (poli) {
    at(t, `${np}/BG`).children.push(
      node(
        "DecoClip",
        [0, 0],
        [
          {
            clipImage: poli,
            tint: "#0e62b2",
            w_: 906,
            h_: 120,
            pivot: 6,
            depth: 5,
            incline: 10,
            radius: 10,
            color: "#ffffff/0.90",
            imgLeft: `${-13.4 * S}px`,
            imgTop: "0",
            imgWidth: `${388 * S}px`,
            imgHeight: `${172 * S}px`,
            imgSize: "100% 100%",
            imgPosition: "0 0",
          },
        ],
      ),
    );
  }
  // square buttons: fullscreen (the portrait's visual mode with the game's own icon, the memorial lobby, the chibi),
  // copy the file name, and on the memorial lobby tab play all dialogue
  at(t, `${np}/Char_CVCollectionBtn`).pos[0] = 1208.8;
  if (tab === "memorialLobby" || tab === "chibi")
    Object.assign(widget(t, `${np}/Char_ZoomBtn/Sprite`), {
      sprite: "Common_UIHide_Icon",
      w_: 74,
      h_: 66,
      color: "#2b496e/1.00",
    });
  Object.assign(widget(t, `${np}/Char_CVCollectionBtn/Sprite`), {
    sprite: "Common_Icon_Copy",
    w_: 64,
    h_: 76,
    color: "#2b496e/1.00",
  });
  if (tab === "memorialLobby") {
    const play = clone(find(t, `${np}/Char_ZoomBtn`));
    play.name = "Char_PlayBtn";
    play.pos[0] = 1362;
    const w = play.children[0].widgets[0];
    if (MemorialLobby.playingAll()) {
      Object.assign(w, { texture: "svg:stop", w_: 54, h_: 54, color: "#2b496e/1.00" });
      delete w.sprite;
    } else Object.assign(w, { sprite: "Common_Icon_Play", w_: 64, h_: 64, color: "#2b496e/1.00" });
    at(t, np).children.push(play);
  }
  return t;
}

// The game's input-popup pencil opens the alias editor; its accessible name includes every saved alias.
const PENCIL = CONTROL.icon.h; // App-created sprite, not a cloned prefab icon
function nameplateRow(np, caption, aliased, ui = NGUI) {
  const y = aliased ? 310 : 288,
    bg = find(np, "BG"),
    body = bg.widgets.find((w) => "sprite" in w);
  // The prefab band's right edge at the name's height, with the App's margin on the spacing scale.
  // Its retained body is 120 high; include its pivot and shear rather than a hand-set right limit.
  const [px, py] = toNode({ x: 0, y: 0, w: body.w_, h: 120 }, body.pivot, LOCAL_FRAME);
  const right =
    bg.pos[0] - px + body.w_ + (y - (bg.pos[1] - py - 60)) * Math.tan(((body.incline || 0) * Math.PI) / 180);
  const content = container({ x: 110, w: right - 110, h: 120, padding: [0, SPACE.xl, 0, 0] }).inner;
  const room = content.w - PENCIL - SPACE.xs;
  const type = { fs: aliased ? 54 : 64, min: 44 },
    height = aliased ? 64 : 76;
  let name;
  do {
    name = textItem(ui, caption, type, { maxW: room });
    if (name.h <= height || type.fs === type.min) break;
    type.fs = Math.max(type.min, type.fs - 2);
  } while (true);
  const line = row([name, { w: PENCIL, h: PENCIL }], { x: content.x, gap: SPACE.xs });
  return { ...line, room, frame: { ...LOCAL_FRAME, cy: y + line.centre } };
}
function aliasPlate(np, caption, aliased) {
  const layout = nameplateRow(np, caption, aliased),
    [name, pencil] = layout.boxes;
  const main = find(np, "Label_Name");
  // The box is the measured width; the ellipsis only applies when the name really exceeds the room, so a sub-pixel
  // difference between canvas measurement and the web view's layout never cuts a name that fits.
  const clipped = NGUI.glyphWidth(name.text, name.fs) > layout.room;
  Object.assign(
    main.widgets.find((w) => "text" in w),
    { text: name.text, w_: name.w, h_: name.h, fontSize: name.fs, pivot: 3, ellipsis: clipped, ariaLabel: caption },
  );
  main.pos = toNode(name, 3, layout.frame);
  np.children.push(
    node("Char_AliasBtn", toNode(pencil, 4, layout.frame), [
      sprite("Cafe_Icon_NameEdit", PENCIL, PENCIL, { type: 0, depth: 22, color: "#ffffff/0.85" }),
    ]),
  );
  return layout;
}

export function renderScreen() {
  return drawScreen().catch(renderFailed);
}
async function drawScreen() {
  const my = ++renderToken,
    gen = showGen,
    shown = tab;
  const ok = () => showable("screen", my, renderToken, gen);
  const host = $("ngui"),
    bgHost = $("ngui-bg");
  const v = alt();
  const t = screenTree();
  const bgNode = find(t, "BG");
  const RIGHT = ["RightInfo", "Arrow_R"];
  const left = { ...t, children: t.children.filter((k) => k !== bgNode && !RIGHT.includes(k.name)) };
  const right = { ...t, widgets: [], children: t.children.filter((k) => RIGHT.includes(k.name)) };
  // render off-screen, then swap in one go (no flicker while images load)
  const next = document.createElement("div");
  next.innerHTML = '<div class="side left"></div><div class="side right"></div>';
  const [leftHost, rightHost] = next.children;
  const [lv, rv] = await Promise.all([
    NGUI.render(left, leftHost, { scale: S, origin: ORIGIN, textureNames: TEXTURES, deferImages: true }),
    NGUI.render(right, rightHost, { scale: S, origin: ORIGIN, textureNames: TEXTURES, deferImages: true }),
  ]);
  const frag = document.createDocumentFragment();
  const bg = await NGUI.render(bgNode, frag, {
    scale: S,
    origin: ORIGIN,
    rootPath: "Inventory_ListControl",
    textures: { BG: v.background || undefined },
    deferImages: true,
  });
  if (!ok()) return;
  const view = { nodes: new Map([...lv.nodes, ...rv.nodes]) };
  const panel = rv.nodes.get("Inventory_ListControl/RightInfo/Target/Active/Bg")?.els[0];
  if (panel) {
    const [left, , , height] = NGUI.rect(panel);
    const incline =
      rv.nodes.get("Inventory_ListControl/RightInfo/Target/Active/Bg").node.widgets.find((w) => "sprite" in w)
        ?.incline || 0;
    MemorialLobby.setDialogueBounds({
      left: 0,
      top: 0,
      right: left - (Math.abs(Math.tan((incline * Math.PI) / 180)) * height) / 2,
      bottom: 1080,
    });
  }
  const cardReady = addSwitchText(rightHost, view);
  if (my === renderToken) {
    setScreenPics([picturesReady([...lv.images, ...rv.images, ...bg.images], leftHost, rightHost, frag)]);
    fillPictures([...lv.thumbnails, ...rv.thumbnails, ...bg.thumbnails, cardReady], leftHost, rightHost, frag);
  }
  ThumbnailCapture.attach(thumbnailBridge);
  hits(view, leftHost, rightHost);
  commit(() => {
    if (!ok()) return;
    applied.screen = my;
    // the control with the keyboard's focus keeps it in the new screen (an arrow stepped with Enter, a step key
    // pressed on a button), where the new screen has it
    const had = host.contains(document.activeElement) ? document.activeElement.dataset?.part : null;
    host.replaceChildren(...next.children);
    if (had) [...host.querySelectorAll(".hit")].find((b) => b.dataset.part === had)?.focus({ preventScroll: true });
    bgHost.replaceChildren(frag);
    bgHost.classList.toggle("dim", shown !== "portrait");
    bgHost.hidden = shown === "memorialLobby"; // the memorial lobby extends its own edges instead
  });
}

// the switch card has no label slot in the game; put the title and the portrait's name beside the picture
function switchTextLayout(w, h, title, sub, arrow) {
  // The prefab bust keeps the left half. The App composes the text and chevron inside the other half.
  const content = container({ x: w / 2, w: w / 2, h, padding: SPACE.s }).inner;
  const columns = row([{ grow: 1 }, ...(arrow ? [{ w: CONTROL.icon.h, h: CONTROL.icon.h }] : [])], {
    ...content,
    gap: SPACE.xs,
  });
  const room = columns.boxes[0];
  let titleSize = TYPE.body.fs,
    subSize = TYPE.caption.fs,
    texts;
  do {
    texts = stack(
      [
        textItem(NGUI, title, { ...TYPE.body, fs: titleSize }, { maxW: room.w, lines: 2 }),
        textItem(NGUI, sub, { ...TYPE.caption, fs: subSize }, { maxW: room.w, lines: 2 }),
      ],
      { x: room.x, gap: SPACE.xs },
    );
    if (texts.h <= room.h || (titleSize === TYPE.body.min && subSize === TYPE.caption.min)) break;
    titleSize = Math.max(TYPE.body.min, titleSize - 2);
    subSize = Math.max(TYPE.caption.min, subSize - 2);
  } while (true);
  return {
    texts: texts.boxes.map((b) => ({ ...b, y: b.y + room.y + (room.h - texts.h) / 2 })),
    arrow: columns.boxes[1],
    content,
  };
}
function addSwitchText(host, view) {
  const cardEl = view.nodes.get(
    `Inventory_ListControl/${INFO}/Weapon/Equip_1/UIEquipmentCard/Btn_Target/Normal/Card_Bg`,
  )?.els[0];
  if (!cardEl) return;
  const [l, t, w, h] = rect(cardEl);
  const box = document.createElement("div");
  box.className = "switch-text";
  Object.assign(box.style, {
    left: `${l}px`,
    top: `${t}px`,
    width: `${w}px`,
    height: `${h}px`,
    display: "block",
    padding: "0",
  });
  const selectedStatus = selectionStatus(alt(), tabKind());
  const sub = [
    tab === "portrait" ? candidateLabel(current()) : sourceLabel(alt(), tabKind()),
    selectedStatus && T(selectedStatus),
  ]
    .filter(Boolean)
    .join(" - ");
  const title = T(isSpecial(alt()) && tab === "memorialLobby" ? "special.switch" : `row.switch.${tab}`);
  const layout = switchTextLayout(w / S, h / S, title, sub, !!switchKind());
  const b = document.createElement("b"),
    span = document.createElement("span"),
    go = document.createElement("i");
  [b, span].forEach((el, i) => {
    const item = layout.texts[i];
    el.innerText = item.text;
    Object.assign(el.style, {
      position: "absolute",
      left: `${item.x * S}px`,
      top: `${item.y * S}px`,
      width: `${item.w * S}px`,
      height: `${item.h * S}px`,
      fontSize: `${item.px * S}px`,
      lineHeight: String(NGUI.LINE_PITCH),
      whiteSpace: "pre",
    });
  });
  go.className = "switch-go";
  if (layout.arrow)
    Object.assign(go.style, {
      left: `${layout.arrow.x * S}px`,
      top: `${layout.arrow.y * S}px`,
      width: `${layout.arrow.w * S}px`,
      height: `${layout.arrow.h * S}px`,
      right: "auto",
      marginTop: "0",
    });
  // The chevron opens the shared source popup.
  box.append(b, span, ...(switchKind() ? [go] : []));
  host.appendChild(box);
  const bust = view.nodes.get(`Inventory_ListControl/${INFO}/Weapon/Equip_1/UIEquipmentCard/Btn_Target/Normal/Bust`);
  const { signal } = thumbnailWork;
  const background = alt().background || layouts.textures[widget(layouts.list, "BG").texture];
  if (switchKind() && bust?.els[0])
    return colourSwitchCard(bust.els[0], box, bust.node.widgets[0], NGUI.load, {
      signal,
      kind: switchKindOf(tab),
      background,
    }).catch((err) => {
      if (!signal.aborted) console.warn("Switch card colours unavailable", err);
    });
}

function hits(view, leftHost, rightHost) {
  // each button names its part, so the focus finds it again in the next render
  const add = (host, path, fn, name) => {
    const el = view.nodes.get(`Inventory_ListControl/${path}`)?.els[0];
    if (!el) return null;
    const button = hitButton(host, rect(el), fn, name);
    button.dataset.part = path;
    return button;
  };
  // The switch card selects a source for the shown asset kind.
  const sk = switchKind();
  if (sk) {
    add(
      rightHost,
      `${INFO}/Weapon/Equip_1/UIEquipmentCard/Btn_Target/Normal/Card_Bg`,
      () => openPopup(sk),
      T(isSpecial(alt()) && sk === "memorialLobby" ? "special.switch" : `row.switch.${tab}`),
    );
    add(
      rightHost,
      `${INFO}/Weapon/Btn_Up_Wrapper/Btn_Up/BG (1)`,
      addMods,
      T(isSpecial(alt()) && sk === "memorialLobby" ? "special.add" : `row.add.${tab}`),
    );
  }
  add(
    rightHost,
    `${INFO}/Status/Btn_Up_Wrapper/Btn_Up/BG (1)`,
    () => reveal(gameFile(alt(), tabKind())),
    T(platform === "mac" ? "open.mac" : "open.win"),
  );
  add(rightHost, `${INFO}/Detail_Btn`, showDetails, T("info.details"));
  if (shownMod())
    add(
      rightHost,
      `${INFO}/Status/Label_Life/Data_Widget/ModAliasBtn`,
      () => editModName(shownMod().mod),
      T("modAlias.title"),
    );
  const act = (card, fn, name) => add(rightHost, `${INFO}/Skill/${card}/Bg`, fn, name);
  act("SkillData_EX", () => runCompatibility(), T("compatibility.check"));
  act(
    "SkillData_EX (1)",
    () => (repairedId(shownMod()) ? jumpRepaired(shownMod()) : runUpdate()),
    T(repairAction(shownMod())),
  );
  act("SkillData_EX (2)", () => runExport(), T("export.title"));
  const apply = act("SkillData_EX (3)", () => applyCandidate(shownSource(alt(), sk), sk), T("popup.apply"));
  if (apply) {
    const hold = costumeBlock(alt(), sk, shownSource(alt(), sk)),
      unfit = hold === "unfit";
    apply.disabled =
      !unfit && (!!hold || !sk || !coreOf(alt(), sk) || busy || isApplied(alt(), sk, shownSource(alt(), sk)));
    if (!coreOf(alt(), sk)) apply.setAttribute("aria-description", T("import.reason.no_target"));
    if (applyChecking(alt(), sk)) apply.setAttribute("aria-busy", "true");
    // a refused costume: greyed, the reason on hover (the click does nothing, applyCandidate); another
    // student's chibi waits for its check
    if (unfit) {
      apply.setAttribute("aria-disabled", "true");
      apply.setAttribute("aria-description", T("chibi.unfit"));
      attachHint(apply, T("chibi.unfit"));
    }
  }
  add(leftHost, "LeftInfo/CharInfo/Char_Target/Pin/Pin_On/Bg", toggleFav, T("fav.label"));
  add(leftHost, "LeftInfo/CharInfo/Char_Target/Pin/Pin_Off/Bg", toggleFav, T("fav.label"));
  // Copy, then the file it copies
  add(
    leftHost,
    "LeftInfo/CharInfo/Char_Target/Char_CVCollectionBtn",
    copyName,
    `${T("window.copy")}${listSep()}${baseName(gameFile(alt(), tabKind()))}`,
  );
  // The pencil opens the editor beside the displayed name.
  const aliasEls = ["Char_AliasBtn"]
    .map((n) => view.nodes.get(`Inventory_ListControl/LeftInfo/CharInfo/Char_Target/${n}`)?.els[0])
    .filter(Boolean);
  if (aliasEls.length) {
    const boxes = aliasEls.map(rect),
      pad = 14;
    const l = Math.min(...boxes.map((b) => b[0])) - pad,
      tp = Math.min(...boxes.map((b) => b[1])) - pad;
    const r = Math.max(...boxes.map((b) => b[0] + b[2])) + pad,
      bt = Math.max(...boxes.map((b) => b[1] + b[3])) + pad;
    const list = aliasList();
    hitButton(
      leftHost,
      [l, tp, r - l, bt - tp],
      () => call(() => openAliases()),
      list ? `${T("alias.title")} - ${list}` : T("alias.title"),
    );
  }
  // the previous or next card of the student list, within its filters and in its order
  add(leftHost, "Arrow_L/Left", () => ctx.step(-1, shownStudent.key, altId), T("studentHall.previous"));
  add(rightHost, "Arrow_R/Right", () => ctx.step(1, shownStudent.key, altId), T("studentHall.next"));
  add(leftHost, "LeftInfo/CharInfo/Char_Target/Char_ZoomBtn", () => setFullscreen(true), T("memorialLobby.fullscreen"));
  if (tab === "memorialLobby")
    add(
      leftHost,
      "LeftInfo/CharInfo/Char_Target/Char_PlayBtn",
      togglePlayAll,
      T(MemorialLobby.playingAll() ? "memorialLobby.stop" : "memorialLobby.playAll"),
    );
  const v = alt();
  [
    ["Tab_1", "portrait", hasKind(v, "portrait")],
    ["Tab_2", "memorialLobby", hasMemorialLobby(v)],
    ["Tab_3", "chibi", hasChibi(v)],
  ].forEach(([n, id, ok]) => {
    if (ok && id !== tab)
      add(
        rightHost,
        `RightInfo/Target/Active/CharacterTab/${n}/Tab_Off/Label`,
        () => showTab(id),
        T(isSpecial(v) && id === "memorialLobby" ? "special.background" : `tab.${id}`),
      );
  });
}
