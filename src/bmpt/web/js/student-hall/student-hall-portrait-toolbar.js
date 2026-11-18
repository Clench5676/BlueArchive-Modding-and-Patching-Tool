// Student Hall: the selected portrait's toolbar (layer, flip, change, remove, scale) and who owns the hover.
import * as NGUI from "../ui/ngui.js";
import * as Popup from "../ui/popup.js";
import * as UI from "./student-hall-ui.js";
import { portraitReplacements } from "./student-hall-chibi.js";
import { alignIcon } from "./student-hall-icons.js";
import { portraitToolbarLayout, PORTRAIT_SIZE } from "./student-hall-framing.js";
import { T } from "../core/i18n.js";
import { clone, S } from "../core/util.js";
import {
  root,
  chrome,
  rectOf,
  portraits,
  current,
  store,
  changePortrait,
  options,
  hitGeneration,
  layouts,
  pendingWrites,
  arranging,
  selectPortrait,
  renderScene,
  fail,
  hits,
} from "./student-hall.js";

// The top bar and the Student Hall's own controls, in stage units: the portrait toolbar never covers them.
function toolbarObstacles() {
  if (!root) return [];
  const box = root.getBoundingClientRect(),
    scale = box.width > 0 ? 1920 / box.width : 1;
  return [...(chrome?.querySelectorAll?.("button, input") || []), ...document.querySelectorAll("header.topbar")]
    .map((element) => element.getBoundingClientRect())
    .filter((b) => b.width > 0 && b.height > 0)
    .map((b) => ({
      x: (b.left - box.left) * scale,
      y: (b.top - box.top) * scale,
      width: b.width * scale,
      height: b.height * scale,
    }));
}
export function toolbarBox(entry, rect = rectOf(entry)) {
  const offset = portraits?.head?.(entry.id),
    head = offset && { x: entry.x + offset.x, y: entry.y + offset.y };
  return portraitToolbarLayout(rect, portraitActions(entry).length, toolbarObstacles(), head);
}
function portraitActions(entry) {
  const list = current().portraits,
    index = list.findIndex((item) => item.id === entry.id);
  const actions = list.length > 1 ? [["Remove", "studentHall.remove", () => store.remove(entry.id), true]] : [];
  if (current().portraits.length >= 2)
    actions.push(
      ["Backward", "studentHall.layerBackward", () => store.layer(entry.id, -1), index > 0],
      ["Forward", "studentHall.layerForward", () => store.layer(entry.id, 1), index < list.length - 1],
      ["Front", "studentHall.layerFront", () => store.layer(entry.id, "front"), index < list.length - 1],
      ["Back", "studentHall.layerBack", () => store.layer(entry.id, "back"), index > 0],
      ["Flip", "studentHall.flipHorizontal", () => store.flip(entry.id), true],
    );
  actions.push([
    "Change",
    "studentHall.changePortrait",
    () => changePortrait(entry),
    portraitReplacements(entry, options.choices, list).length > 0,
  ]);
  return actions;
}
// Kept separate from behavior so the chosen game icons can be swapped without changing editor semantics.
const PORTRAIT_ICONS = {
  // UICardGame_MyDeck SelectDump, UIConquest MissionTab_Target, UICampaign ScenarioReplay, UIFormation PositionChange.
  Change: { atlas: "Common", sprite: "Campaign_Icon_ChangeUnit", width: 77, height: 68 },
  Remove: { atlas: "CardGame", sprite: "CCG_Icon_Dump", width: 64, height: 64 },
  Backward: { sprite: "Common_Icon_Top", width: 44, height: 26, rotZ: 180 },
  Forward: { sprite: "Common_Icon_Top", width: 44, height: 26 },
  Front: { sprite: "Common_Skip_Icon", width: 61, height: 36, rotZ: 90, type: 0 },
  Back: { sprite: "Common_Skip_Icon", width: 61, height: 36, rotZ: 270, type: 0 },
  Flip: { sprite: "Common_Icon_Change", width: 80, height: 80 },
};
export async function renderPortraitActions(host, entry, rect, version, alive = () => true) {
  // The toolbar is placed by the portrait's visible head, known once its atlas alpha is read (a few milliseconds).
  await portraits?.pickReady?.(entry.id);
  if (version !== hitGeneration || !alive()) return;
  const actions = portraitActions(entry),
    box = toolbarBox(entry, rect),
    width = box.icon;
  Object.assign(host.style, {
    left: `${box.x}px`,
    top: `${box.y}px`,
    width: `${box.width}px`,
    height: `${box.height}px`,
  });
  const destination = document.createElement("div");
  await Promise.all(
    actions.map(async ([name, key, action, enabled], index) => {
      const chosen = PORTRAIT_ICONS[name];
      if (!chosen) return;
      const tree = clone(NGUI.find(layouts.home, "Front/Right/Btn_RotateSpine/ConditionRoot/Able_Count"));
      tree.name = `Portrait${name}`;
      tree.pos = [0, 0];
      tree.children = tree.children.filter((node) => ["Bg", "Icon"].includes(node.name));
      const bg = NGUI.find(tree, "Bg").widgets.find((widget) => "sprite" in widget);
      bg.w_ = bg.h_ = width / S;
      const node = NGUI.find(tree, "Icon"),
        widget = node.widgets.find((item) => "sprite" in item);
      node.pos = [0, 0];
      node.scale = [1, 1];
      widget.color = NGUI.find(layouts.home, "Front/NUITop/Top/Right/Btn_LobbyCharacter/Able/Icon").widgets.find(
        (item) => "sprite" in item,
      ).color;
      Object.assign(widget, {
        type: chosen.type ?? 1,
        atlas: chosen.atlas || "Common",
        sprite: chosen.sprite,
        w_: chosen.width,
        h_: chosen.height,
        flip: chosen.flip || 0,
      });
      const size = 56 / Math.max(widget.w_, widget.h_);
      widget.w_ *= size;
      widget.h_ *= size;
      const at = box.icons[index];
      const view = await NGUI.render(tree, destination, { scale: S, origin: [at.x + at.w / 2, at.y + at.h / 2] });
      await alignIcon(view, `${tree.name}/Icon`, `${tree.name}/Bg`, {
        maxSize: 56 * S,
        rotation: chosen.rotZ || 0,
        enabled: enabled && !pendingWrites,
      });
      const button = Popup.hit(
        destination,
        view,
        `${tree.name}/Bg`,
        () => {
          if (version !== hitGeneration || !alive() || !arranging() || pendingWrites || !enabled) return;
          if (name === "Remove") selectPortrait("remove", entry.id);
          if (name === "Change") action();
          else if (action()) renderScene().catch(fail);
        },
        { name: T(key), action: `student-hall-portrait-${name.toLowerCase()}`, z: 90 },
      );
      if (button) {
        button.dataset.entryAction = entry.id;
        button.disabled = pendingWrites > 0 || !enabled;
      }
    }),
  );
  const enabled = () => version === hitGeneration && alive() && arranging() && !pendingWrites;
  // The toolbar moves only once a size is set: moving it under a dragged thumb would feed back into the slider.
  const place = () => {
    if (!enabled()) return;
    const next = toolbarBox(entry);
    Object.assign(host.style, { left: `${next.x}px`, top: `${next.y}px` });
  };
  let control;
  await UI.button(
    destination,
    "StudentHallPortraitScaleReset",
    T("studentHall.scaleReset"),
    box.reset,
    () => {
      control?.reset();
      place();
    },
    { enabled: enabled(), captionSite: "studentHall.scaleReset", height: box.reset[3] },
  );
  control = await UI.scaleControl(
    destination,
    entry.scale,
    (value) => {
      if (!enabled()) return;
      entry.scale = value;
      portraits?.setScale?.(current().portraitScale);
      const rect = rectOf(entry),
        button = hits.querySelector(`[data-entry="${entry.id}"]`);
      if (button)
        Object.assign(button.style, {
          left: `${rect.x}px`,
          top: `${rect.y}px`,
          width: `${rect.width}px`,
          height: `${rect.height}px`,
        });
    },
    { enabled, box: box.slider, limits: PORTRAIT_SIZE, focusKey: `student-hall-portrait-scale:${entry.id}` },
  );
  control?.range?.addEventListener("change", place);
  if (version === hitGeneration && alive() && root) host.replaceChildren(destination);
}
export function createHoverOwner(change) {
  let owner = null,
    pointer = null,
    palette = false,
    focus = null,
    pinned = null;
  const publish = () => {
    const next = pinned || (palette ? owner : pointer || focus);
    if (next !== owner) {
      owner = next;
      change(owner);
    }
  };
  return {
    pin(id) {
      pinned = id;
      publish();
    },
    actorEnter(id) {
      pointer = id;
      publish();
    },
    actorLeave(id, intoPalette = false) {
      if (pointer === id) pointer = null;
      if (intoPalette) palette = true;
      publish();
    },
    paletteEnter() {
      palette = true;
    },
    paletteLeave(next = null) {
      palette = false;
      pointer = next;
      publish();
    },
    focus(id) {
      focus = id;
      publish();
    },
    clearPointer() {
      pointer = null;
      palette = false;
      publish();
    },
    snapshot: () => ({ owner, pointer, palette, focus }),
    restore(saved) {
      pointer = saved.pointer || (saved.palette ? saved.owner : null);
      focus = saved.focus;
      owner = saved.owner;
      palette = false;
      change(owner);
    },
  };
}
