// The game-data JSON the server exports (/asset/ui/layout/<root>.json, office layouts, a memorial lobby's memorial-lobby.json)
// names its serialized fields in full (localPosition, className, introMix, ...). The page draws from one shared node
// chibi that its own trees use as well, with the short field names below; this module reads the exported names into
// that chibi where the documents are fetched. Fields that are not listed keep their names and values.

const NODE = Object.freeze({ localPosition: "pos", localRotationZDegrees: "rotZ" });
const WIDGET = Object.freeze({
  className: "cls",
  spriteName: "sprite",
  width: "w_",
  height: "h_",
  fillAmount: "fill",
  fillDirection: "fillDir",
  inclineAngle: "incline",
  effectStyle: "effect",
  alignment: "align",
});
const CLIP = Object.freeze({
  finishMode: "finish",
  introMix: "intro",
  outroMix: "outro",
  outroStartOffset: "outroAt",
  blockInteractionOnPlay: "block",
});

function renamed(object, names) {
  const out = {};
  for (const [key, value] of Object.entries(object)) out[names[key] ?? key] = value;
  return out;
}

/**
 * Read an exported game-data document into the page's node chibi: NGUI nodes and widgets, memorial lobby clips and hit
 * boxes. A widget is an object with className; a clip one with finishMode or introMix; a hit box one with rect and
 * rotation. A document with no exported names is returned as it is.
 * @param {*} value The parsed JSON.
 * @returns {*} The same document with the page's field names.
 */
export function readSerialized(value) {
  if (Array.isArray(value)) {
    const items = value.map(readSerialized);
    return items.some((item, i) => item !== value[i]) ? items : value;
  }
  if (!value || typeof value !== "object") return value;
  let out = value;
  if ("localPosition" in out || "localRotationZDegrees" in out) out = renamed(out, NODE);
  if ("className" in out) out = renamed(out, WIDGET);
  if ("finishMode" in out || "introMix" in out || "blockInteractionOnPlay" in out) out = renamed(out, CLIP);
  if ("rect" in out && "rotation" in out && !("rot" in out)) out = renamed(out, { rotation: "rot" });
  for (const [key, item] of Object.entries(out)) {
    if (!item || typeof item !== "object") continue;
    const read = readSerialized(item);
    if (read === item) continue;
    if (out === value) out = { ...value };
    out[key] = read;
  }
  return out;
}
