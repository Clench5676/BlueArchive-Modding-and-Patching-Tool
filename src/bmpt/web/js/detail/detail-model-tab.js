// The detail screen's 3D tab: the chibi on the left and its actions.
import { notify } from "../ui/popup.js";
import * as ActionList from "../ui/action-list.js";
import { actionsOf } from "./clip-names.js";
import { T } from "../core/i18n.js";
import { modelSource } from "../mods/switch-sources.js";
import { $ } from "../core/util.js";
import {
  altId,
  shownStudent,
  tab,
  fullscreen,
  modelGeneration,
  modelShot,
  modelLib,
  ModelLib,
  leftToken,
  open,
  costumeState,
  reload,
  setModelAction,
  setModelActions,
  setModelMenu,
  setModelShot,
} from "./detail.js";
import { renderScreen } from "./detail-screen.js";
import { actionKey, stopActionLoad, renderExpressions } from "./detail-strip.js";
import { modsOn, overwrittenMessage } from "./detail-switchers.js";

// the 3D chibi: loaded, its shaders compiled and its first frame drawn; the actions fill row 4 and a still of it goes
// on the switch card
export async function showModel(v, my, alive = () => true) {
  const still = () => alive() && open && my === leftToken && tab === "model" && altId === v.key;
  try {
    const M = await ModelLib();
    if (!still()) return;
    const list = await M.show(v.key, $("model"), {
      frame: fullscreen ? "full" : "left",
      version: `${shownStudent.version}:${modelGeneration}`,
      source: modelSource(v, modsOn()),
      onAction: (a) => {
        if (tab === "model" && altId === v.key) {
          setModelAction(actionKey(a));
          renderExpressions();
        }
      },
    });
    if (!list || !still()) return;
    setModelActions(list);
    // the model's own clips by group, for the action bar (none: today's actions in a row)
    const menu = actionsOf(M.clipNames(), M.devName());
    setModelMenu(menu.length ? menu : null);
    setModelAction(actionKey(M.current()));
    if (!modelShot && !fullscreen) setModelShot(M.snapshot());
    await Promise.all([renderScreen(), renderExpressions()]);
  } catch (err) {
    // a model that cannot be read (a broken or unreadable GLB): the tab stays, empty, and the rest of the screen works
    console.error(err);
    if (!still()) return;
    // the official model asked for has a part with no official copy left: the install's own repair message, not
    // awaited (the left side's load has a time limit)
    if (err?.code === "official_unavailable") void overwrittenMessage(v, "chibi");
    else if (
      err?.code === "source_unsupported" &&
      v.previewSources?.chibi?.startsWith("alt:") &&
      !costumeRefusals.has(`${v.key}|${v.previewSources.chibi}`)
    ) {
      // a costume refused at its first build (saved before the check existed): read the student again, whose preview
      // sources leave it out now, instead of an error
      costumeRefusals.add(`${v.key}|${v.previewSources.chibi}`);
      costumeState.set(v.key, { ...(costumeState.get(v.key) || {}), [v.previewSources.chibi.slice(4)]: "refused" });
      void reload();
    } else notify(T("model.failed"), { kind: "error", error: err });
  }
}
const costumeRefusals = new Set();
export function hideModel() {
  setModelActions([]);
  setModelMenu(null);
  stopActionLoad();
  ActionList.close();
  setModelAction(null);
  modelLib?.then((M) => M.hide($("model")));
}
