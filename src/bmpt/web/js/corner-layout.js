// UICharacterDetail/Inventory_ListControl/NUITop_Scenes/Top/Right/Lobby:
// right anchor 1480, local x -98.8, centred 62-unit Home sprite => 98.8 - 62/2.
// Use this authored side inset for scene corners on both axes; retain the toolbar's own row alignment.
export const CORNER_UNITS = 98.8 - 62 / 2;
export const CORNER_MARGIN = CORNER_UNITS * 1920 / 2960;

// All inputs except the source widget box are viewport CSS pixels. Output is in stage pixels,
// including the area outside a letterboxed stage, so the inset is always measured from the window.
// insets (chrome-motion.js): how far an immersive scene's top bar and bottom menu reach into the window now, and the top
// bar's offset, which the macOS window buttons share. Scene controls make room for a bar the pointer brought back.
export function cornerBox(box, { corner, immersive = true, width, height, stage,
  barBottom = stage.top + 64 * stage.width / 1920, native = null, insets = null }) {
  const scale = stage.width / 1920, margin = CORNER_MARGIN * scale;
  const right = corner.endsWith('right'), bottom = corner.startsWith('bottom');
  let x = right ? width - margin - box[2] * scale : margin;
  const y = bottom ? height - margin - box[3] * scale - (immersive ? insets?.bottom || 0 : 0)
    : (immersive ? insets?.top || 0 : barBottom) + margin;
  // Only the visible body can displace a control. Keep its larger prefab hit area; native controls take their own clicks.
  const lift = insets?.offset || 0, edge = x + box[2] * scale, foot = y + box[3] * scale;
  const bounds = native?.bounds && { ...native.bounds, top: native.bounds.top - lift, bottom: native.bounds.bottom - lift };
  if (!bottom && native?.active && bounds && x < bounds.right && edge > bounds.left && y < bounds.bottom && foot > bounds.top) {
    x += right ? bounds.left - margin - edge : bounds.right + margin - x;
  }
  return [(x - stage.left) / scale, (y - stage.top) / scale, box[2], box[3]];
}

const controls = new Set(), registered = new WeakMap();
function place(element, entry) {
  const stage = globalThis.document?.getElementById?.('stage')?.getBoundingClientRect?.();
  if (!stage?.width || !Number.isFinite(globalThis.innerWidth)) return;
  const insets = globalThis.bamtChromeInsets || null;
  if (entry.options.edge) {
    // a centred control at the stage's top or bottom edge moves just as far as a corner control there
    const shift = (entry.options.edge === 'bottom' ? -insets?.bottom : insets?.top) || 0;
    element.style.translate = shift ? `0px ${shift / (stage.width / 1920)}px` : '';
    return;
  }
  const bar = globalThis.document.querySelector?.('.topbar')?.getBoundingClientRect?.();
  // the bar's resting place: its entry motion does not carry the controls under it along
  const next = cornerBox(entry.box, { ...entry.options, stage, width: innerWidth, height: innerHeight,
    barBottom: bar ? bar.bottom + (insets?.offset || 0) : undefined, native: globalThis.bamtWindowChrome, insets });
  // CSS translate composes with the prefab's slide animation on transform.
  element.style.translate = `${next[0] - entry.box[0]}px ${next[1] - entry.box[1]}px`;
}
export function bindCorner(element, box, options) {
  if (!element || !box) return;
  let entry = registered.get(element);
  if (!entry) { entry = { ref: new WeakRef(element) }; registered.set(element, entry); controls.add(entry); }
  Object.assign(entry, { box, options }); place(element, entry);
}
// An immersive scene's control centred on the stage's top or bottom edge (the photography shutter).
export function bindEdge(element, edge) {
  if (!element) return;
  let entry = registered.get(element);
  if (!entry) { entry = { ref: new WeakRef(element) }; registered.set(element, entry); controls.add(entry); }
  Object.assign(entry, { box: null, options: { edge } }); place(element, entry);
}
export function reflowCorners() {
  for (const entry of controls) {
    const element = entry.ref.deref();
    if (!element) { controls.delete(entry); continue; }
    if (element.isConnected !== false) place(element, entry);
  }
}
