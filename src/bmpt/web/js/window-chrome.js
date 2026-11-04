import { on, rpc } from "./api.js";
import { T } from "./i18n.js";
import { reflowTopActions } from "./nav.js";
import { alignTopBar } from "./top-bar.js";
import { fitStage } from "./util.js";
import { paintTopBar } from "./top-bar-background.js";
import { naturalTextWidth } from "./top-bar-measure.js";
import { headerGeometry, rowLayout, isImmersive, dragRegions, expandRect } from "./window-layout.js";
import { chromeMotion } from "./chrome-motion.js";

let snapshot = null, wrapper = null, placeholder = null, compact = false, scheduled = false, lastLayout = "";
let layoutRevision = 0, lastScene = null;
const bar = () => document.querySelector('.topbar');
// The top bar's entry, its absence in immersive scenes and its return at the window's edge (chrome-motion.js).
const motion = chromeMotion();
export const nativeChrome = () => snapshot?.active && snapshot.mode !== 'standard' ? snapshot : null;
// The macOS window buttons travel with the top bar, except in macOS fullscreen, where AppKit shows and hides them.
const nativeLights = () => !!nativeChrome() && snapshot.platform === 'darwin' && !snapshot.state?.fullscreen;

function fallbackControls() {
  const existing = document.getElementById('native-controls');
  if (!snapshot || snapshot.active || snapshot.mode === 'standard' || !globalThis.pywebview) { existing?.remove(); return; }
  if (existing) {
    for (const button of existing.children) button.setAttribute('aria-label', T(`window.${button.dataset.action}`));
    return;
  }
  const controls = document.createElement('div'); controls.id = 'native-controls';
  for (const [action, icon] of [['minimize', '−'], ['maximize', '□'], ['close', '×']]) {
    const button = document.createElement('button'); button.textContent = icon; button.dataset.action = action;
    button.setAttribute('aria-label', T(`window.${action}`));
    button.addEventListener('click', () => rpc('window.control', { action }).catch(console.error)); controls.append(button);
  }
  document.body.append(controls);
}

function accept(next) {
  if (snapshot && (next.sequence || 0) < (snapshot.sequence || 0)) return;
  snapshot = next;
  globalThis.bamtWindowChrome = nativeChrome();
  motion.syncLights();
  fallbackControls(); schedule();
}

function schedule() {
  ++layoutRevision;
  if (scheduled) return;
  scheduled = true;
  requestAnimationFrame(() => { scheduled = false; layout().catch(layoutFailed); });
}

// A failed layout leaves the native drag regions as they were: put it in the diagnostic report, not only the console.
function layoutFailed(error) {
  console.error(error);
  const event = { kind: 'window-chrome', message: String(error?.message || error).slice(0, 2000),
    stack: String(error?.stack || '').slice(0, 4000), page: globalThis.location?.hash || '' };
  rpc('app.frontendError', { event }).catch(() => {});
}

function fitRow(header, scale, nativeLeft = 0) {
  const mods = header.querySelector('.mod-switch'), label = mods?.querySelector('.ms-label');
  const option = header.querySelector('.top-option').getBoundingClientRect();
  // Measure outside the potentially hidden compact caption.
  const captionWidth = label ? naturalTextWidth(label, scale) + 12 * scale : 0;
  const fixedMods = mods && !mods.hidden ? [...mods.children].filter((el) => el !== label)
    .reduce((width, el) => width + el.getBoundingClientRect().width, 12 * scale) : 0;
  const row = rowLayout({ scale, nativeLeft, rightStart: option.left - header.getBoundingClientRect().left,
    modsWidth: fixedMods + captionWidth, captionWidth, compact, hall: header.classList.contains("hall-home"), modsVisible: !!mods && !mods.hidden });
  compact = row.compact;
  header.style.setProperty('--native-left', `${row.shift / scale}px`);
  header.classList.toggle('mods-compact', compact);
  if (label) mods.querySelector('[role="switch"]')?.setAttribute('aria-label', label.textContent);
}

async function layout() {
  const revision = layoutRevision;
  fallbackControls();
  const header = bar(), stage = document.getElementById('stage');
  if (!header || !stage) return;
  const immersive = isImmersive(stage.classList, document.documentElement.classList);
  document.body.classList.toggle('window-immersive', immersive);
  motion.setImmersive(immersive);
  // An immersive scene whose bar is away has no toolbar and no drag regions (BL-037); a bar the pointer brought back
  // drags and clicks as anywhere else. A bar still waiting for its entry leaves the whole row to dragging.
  const topHidden = motion.hidden('top'), scene = immersive && topHidden;
  const blocked = [...document.querySelectorAll('.consent-host, .popup-host')].some((el) => !el.hidden && el.getClientRects().length);
  const visibleHits = (root) => [...root.querySelectorAll('button, [role="switch"], .mod-switch')]
    .filter((el) => !el.hidden && getComputedStyle(el).visibility !== 'hidden' && el.getClientRects().length)
    .map((el) => expandRect(el.getBoundingClientRect()));
  if (!nativeChrome()) {
    lastScene = null;
    if (wrapper) {
      placeholder.replaceWith(header); wrapper.remove(); wrapper = placeholder = null;
      header.style.removeProperty('width'); header.style.removeProperty('height'); header.style.removeProperty('transform');
      header.style.removeProperty('--native-left'); header.inert = false;
      header.classList.remove('mods-compact', 'chrome-waiting', 'native-toolbar');
    }
    document.body.classList.remove('native-chrome');
    globalThis.bamtHeaderGeometry = null; fitStage(); reflowTopActions();
    const scale = stage.getBoundingClientRect().width / 1920;
    fitRow(header, scale);
    paintTopBar(header, scale).catch((error) => console.warn("top bar background", error));
    motion.apply();
    await alignTopBar();
    if (revision !== layoutRevision || nativeChrome()) return;
    return;
  }
  if (!wrapper) {
    placeholder = document.createComment('window toolbar'); header.before(placeholder);
    wrapper = document.createElement('div'); wrapper.className = 'window-header';
    document.body.append(wrapper); wrapper.append(header); header.classList.add('native-toolbar'); document.body.classList.add('native-chrome');
  }
  const g = headerGeometry(innerWidth, innerHeight, 0, immersive);
  globalThis.bamtHeaderGeometry = g;
  wrapper.style.height = `${g.rowHeight}px`;
  Object.assign(header.style, { width: `${innerWidth / g.scale}px`, height: `${g.rowHeight / g.scale}px`, transform: `scale(${g.scale})` });
  header.classList.toggle('chrome-waiting', !snapshot.valid);
  // Hall/detail hold their own inert/focus state. Only add a temporary measurement guard.
  if (!topHidden) header.inert = !snapshot.valid;
  fitStage(); reflowTopActions();
  fitRow(header, g.scale, snapshot.platform === 'darwin' ? snapshot.bounds?.right || 0 : 0);
  paintTopBar(header, g.scale).catch((error) => console.warn("top bar background", error));
  motion.apply();
  // Why the native side has no drag regions, for its log: an immersive scene or chrome that is not valid yet.
  const state = scene ? 'immersive' : snapshot.valid ? '' : 'waiting';
  // Clear old page hit regions before any asynchronous art/ink measurement.
  // A slow asset must not leave a fullscreen scene under a broad drag row.
  if (lastScene !== scene || !snapshot.valid) {
    lastScene = scene;
    wrapper.querySelectorAll('.window-drag-region').forEach((el) => el.remove());
    header.inert = topHidden || blocked || !snapshot.valid;
    // Entering a scene or losing validity clears the regions at once; the first pass and leaving a scene measure below.
    if (state) await publishLayout(g, [], state);
    if (revision !== layoutRevision || !nativeChrome() || !wrapper || !snapshot.valid) return;
  }
  // The bar draws above every popup, so it keeps moving the window while one is open (BL-078); only its controls
  // wait for the popup. Native regions never cover hanging Back art. Immersive scenes keep only the real window
  // controls, with no toolbar or drag regions.
  const publish = () => {
    header.inert = !snapshot.valid || topHidden || blocked;
    const drag = dragRegions({ width: innerWidth, height: g.rowHeight, bounds: snapshot.bounds,
      platform: snapshot.platform, immersive: scene, blocked: !snapshot.valid,
      exclusions: topHidden ? [] : visibleHits(header) });
    wrapper.querySelectorAll('.window-drag-region').forEach((el) => el.remove());
    return publishLayout(g, drag, state);
  };
  // The regions follow the page at once; the ink alignment then moves the bar's pictures by a pixel or two and the
  // regions follow again. Waiting for the alignment first left the bar without regions whenever a pass did not finish
  // it (BL-078: after a popup or an immersive scene the bar stayed undraggable).
  await publish();
  if (revision !== layoutRevision || !nativeChrome() || !wrapper) return;
  const slow = setTimeout(() => layoutFailed(new Error('Top bar alignment still pending after 2 s')), 2000);
  try { await alignTopBar(); } finally { clearTimeout(slow); }
  if (revision !== layoutRevision || !nativeChrome() || !wrapper) return;
  await publish();
}

async function publishLayout(g, drag, state = '') {
  const payload = JSON.stringify({ centre: g.centre, height: g.rowHeight, drag, state });
  if (payload === lastLayout) return;
  // Remember a layout only once the native side took it: a refused one is sent again on the next layout pass.
  await rpc('window.layout', JSON.parse(payload));
  lastLayout = payload;
}

export function setupWindowChrome() {
  motion.setup({ onChange: schedule, lights: nativeLights });
  on('window.chrome', accept);
  rpc('window.chrome').then(accept).catch(console.error);
  fallbackControls();
  addEventListener('pywebviewready', () => { fallbackControls(); rpc('window.chrome').then(accept).catch(console.error); }, { once: true });
  addEventListener('resize', schedule);
  document.fonts?.ready.then(schedule);
  document.fonts?.addEventListener('loadingdone', schedule);
  new MutationObserver(schedule).observe(document.getElementById('stage'), { subtree: true, childList: true, characterData: true,
    attributes: true, attributeFilter: ['hidden', 'class'] });
  new MutationObserver(schedule).observe(document.documentElement, { attributes: true, attributeFilter: ['class'] });
  // The header leaves the stage only on active native hosts.
  new MutationObserver(schedule).observe(bar(), { subtree: true, childList: true, characterData: true,
    attributes: true, attributeFilter: ['hidden'] });
}
