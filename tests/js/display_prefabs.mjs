// Synthetic UIPopup_FilterSort and UIFilterObjectList: every node display.js reads, with the prefab's names, so the
// display settings popup can be opened and drawn on the stand-in DOM. Sizes and positions are stand-ins.
import { insert, lbl, n, spr } from "./control_prefabs.mjs";

// adds the display settings' nodes to a UIPopup_FilterSort root (the Btn_Section/Btn_Disable branch may be there)
// and returns the UIFilterObjectList root
export function displayLayouts(filter) {
  const put = (path, node) => insert(filter, path, node);
  put("Center/BlackBg/BlackBgClickArea", n("BlackBgClickArea", [0, 0], [spr("Common_Square_Bg", 2976, 2232, { color: "#000000/0.94" })]));
  put("Center/Popup/Base/BG", n("BG", [0, 8], [spr("Common_Popup_Bg", 2458, 1355)], [
    n("Label_Title_Locale", [0, 611], [{ ...lbl(), cls: "UILabel", w_: 146, h_: 72 }], [n("Line", [0, -52], [spr("Common_Square_Bg", 160, 12)])]),
  ]));
  for (const [widget, id] of [["FilterWidget", "Filter"], ["SortWidget", "Sort"]]) {
    for (const state of ["Tab_On", "Tab_Off"]) put(`Center/Popup/Tab/${widget}/${id}/${state}/Label_Locale`, n("Label_Locale", [0, 0], [lbl()]));
  }
  put("Center/Popup/Filter/BG_List", n("BG_List", [212, 0], [spr("Common_Square_Bg", 2244, 781)]));
  // the filter panel's header (pivot top) and its triangle pattern, from the header's right end outwards as in the prefab
  put("Center/Popup/Filter/BG_Tag", n("BG_Tag", [1, 516], [spr("Common_Bg_Raius10px", 2244, 139, { pivot: 1, depth: 9 })], [
    n("DecoTex_Normal", [1122, 0.6], [{ texture: "Img_Deco_Weapon", w_: 655, h_: 135, pivot: 0, depth: 11, color: "#d1edff/0.76" }])]));
  put("Center/Popup/Filter/BG_Tag/Label_Locale_ListAll", n("Label_Locale_ListAll", [0, 0], [lbl()]));
  put("Center/Popup/Filter/BG_Tag/Btn_Reset/LabelLocale", n("LabelLocale", [0, 0], [lbl()]));
  put("Center/Popup/Sort/BG_List", n("BG_List", [212, 0], [spr("Common_Square_Bg", 2244, 781)]));
  put("Center/Popup/Sort/Scroll View/Sort_Normal/Title_Normal/Label_Locale_UnitList", n("Label_Locale_UnitList", [0, 0], [lbl()]));
  put("Center/Popup/Sort/Scroll View/Sort_Normal/Option_Normal/Grid/UISortObjectListUnit/Label_Text", n("Label_Text", [0, 0], [], [
    n("On", [0, 0], [spr("Common_Square_Bg", 60, 60)], [n("Label_On", [0, 0], [lbl()])]),
    n("Label_On", [0, 0], [lbl()]),
  ]));
  // the footer's two buttons: the prefab's 521 x 180 bodies and their captions
  for (const name of ["Btn_Ok", "Btn_Cancel"]) {
    put(`Center/Popup/Btn_Section/${name}/BG (1)`, n("BG (1)", [0, 0], [spr("Common_Btn_BG", 521, 180)]));
    put(`Center/Popup/Btn_Section/${name}/Label_Locale`, n("Label_Locale", [0, 0], [lbl()]));
  }
  const group = n("UIFilterObjectList", [0, 0], [], [n("Label_Title", [0, 0], [lbl()])]);
  insert(group, "Table_Align/Line_Deco_Bottom", n("Line_Deco_Bottom", [0, -176], [spr("Common_Square_Bg", 2000, 4)]));
  insert(group, "Table_Align/Table_ListUnit/UIFilterObjectListUnit", n("UIFilterObjectListUnit", [0, 0], [], [
    n("UIFilter_CheckBox", [0, 0], [], [n("CheckBoxIcon", [0, 0], [spr("Common_Square_Bg", 82, 80)]), n("CheckBoxIcon_Disable", [0, 0], [spr("Common_Square_Bg", 82, 80)])]),
    n("SwitchObject_Columns_4", [0, 0], [], [n("Label_Text", [0, 0], [lbl()])]),
  ]));
  return group;
}
