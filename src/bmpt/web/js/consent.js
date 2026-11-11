// First-launch consent for caching light game assets. Before consent the UI reads every game asset straight from the
// install (in memory). After consent light UI assets (atlas sprites, UI textures, prefab layouts) are cached in the user
// data directory; previews and exports of portraits, memorial lobbies, backgrounds and 3D models follow the Preview
// Cache setting (on by default, bounded); voices are always read from the game files.
// Also the scan consent (scan()): whether the app may check the game files on its own (the start and the end of each
// portrait and memorial lobby file) and read Steam's records on this device (the install location to find the game,
// the update status, the file lists that tell official files); asked once like the asset consent, again when it came to
// cover more; Settings' Mods tab asks again or withdraws it.
import { notify } from "./popup.js";
import * as NGUI from "./ngui.js";
import * as Buttons from "./game-buttons.js";
import * as Popup from "./popup.js";
import { rpc } from "./api.js";
import { T, errorText } from "./i18n.js";
import { setSpineConsent } from "./spine-runtime.js";
import { COLOUR, CONTROL, POPUP_FRAME, SPACE, TYPE, XS_BUTTON } from "./ui-tokens.js";
import { frameGeometry, popupContent } from "./popup-frames.js";
import { container, row, stack, textItem, toNode } from "./ui-grid.js";
import { S, hitButton, wrapPath } from "./util.js";

const { node, sprite, label, find, rect } = NGUI;
let assetsAllowed = false, spinePrompt = null;

// The licence notice uses game assets only after the asset consent.
export function init(info) { assetsAllowed = info?.consent === true; }

// resolves with the choice (true / false) once a button is pressed; native: the folder picker is available
export async function show(host, cacheDir, native = false) {
  return new Promise((resolve) => draw(host, cacheDir, native, resolve));
}

// The consent sections are stacks inside the shared L content box. Paths keep the small-button row's centre line.
export function assetLayout(cacheDir) {
  const w = POPUP_FRAME.L.w, sectionW = w - 2 * SPACE.xl;
  const bf = NGUI.fitText(T("consent.change"), XS_BUTTON.fs, XS_BUTTON.fsMin, XS_BUTTON.w - XS_BUTTON.fitInset);
  const bw = Math.max(XS_BUTTON.minW, NGUI.glyphWidth(bf.text, bf.fs) + XS_BUTTON.glyphPad);
  const room = sectionW - 2 * SPACE.m - bw - SPACE.m;
  const to = wrapPath(T("cacheTo", { path: cacheDir }), TYPE.chip.fs, TYPE.chip.min, room, 2);
  const pathItem = textItem({ ...NGUI, fitText: () => to }, to.text, { fs: to.fs, min: to.fs }, { maxW: room, lines: Infinity });
  const pathH = pathItem.h;
  const pathRow = row([{ ...pathItem, w: room }, { w: bw, h: XS_BUTTON.h }],
    { x: SPACE.m, w: sectionW - 2 * SPACE.m, gap: SPACE.m, justify: "center" });
  const [, cachedBodyH] = section("Cached", 0, T("consent.cached.label"), [T("consent.cached.items"), T("consent.cached.previews")], "#2d4c72", sectionW);
  const [, neverH] = section("Never", 0, T("consent.never.label"), [T("consent.never.items"), T("consent.never.how")], COLOUR.neverBar, sectionW);
  const cachedH = cachedBodyH + SPACE.m + pathRow.h;
  const note = textItem(NGUI, T("consent.note"), TYPE.caption, { maxW: w - 2 * SPACE.xxxl, lines: 3 });
  const sections = stack([{ w: sectionW, h: cachedH }, { w: sectionW, h: neverH }, { w: note.w, h: note.h }],
    { w: sectionW, gap: SPACE.xl, align: "center" });
  const f = frameGeometry("L", POPUP_FRAME.contentTop + sections.h + POPUP_FRAME.footer.bottom + CONTROL.buttonL.h + SPACE.m);
  const content = popupContent(f.w, f.h), placed = stack(sections.boxes, { ...content, gap: SPACE.xl, align: "center", justify: "center" }).boxes;
  const cachedTop = toNode(placed[0], 1, f)[1], neverTop = toNode(placed[1], 1, f)[1];
  const path = pathRow.boxes[0], button = pathRow.boxes[1], rowY = -cachedBodyH - SPACE.m - pathRow.h / 2;
  return { ...f, content, sectionW, cachedTop, cachedH, cachedBodyH, neverTop, neverH,
    noteY: toNode(placed[2], 4, f)[1], noteBottom: toNode(placed[2], 7, f)[1], note, to, pathH, rowY, rowH: pathRow.h, room,
    pathY: -cachedBodyH - SPACE.m - path.y - path.h / 2,
    change: { x: button.x + button.w / 2 - sectionW / 2, w: bw, caption: bf },
    itemBox: [-sectionW / 2 + SPACE.m, -cachedBodyH + SPACE.m, -73 - 2 * SPACE.m] };
}

async function draw(host, cacheDir, native, resolve) {
  await Buttons.ready();
  await Popup.frameReady();
  const layout = assetLayout(cacheDir);
  // the shared popup frame: no close button, to be answered with one of its two buttons
  const { tree: t, popup } = consentFrame(T("consent.title"), layout.w, layout.h, [T("consent.no"), T("consent.yes")]);
  const [cached] = section("Cached", layout.cachedTop, T("consent.cached.label"), [T("consent.cached.items"), T("consent.cached.previews")], "#2d4c72", layout.sectionW);
  cached.children[0].widgets[0].h_ = layout.cachedH;
  const { to } = layout;
  cached.children.push(
    node("Path", [-layout.sectionW / 2 + SPACE.m, layout.pathY], [label(to.text, to.fs, "#2d4664/1.00", layout.room, layout.pathH, { pivot: 3, depth: 7 })]),
    Buttons.smallButton("Change", layout.change.x, layout.rowY, T("consent.change"), { w: layout.change.w, enabled: native, depth: 8 }));
  popup.children.push(
    cached,
    section("Never", layout.neverTop, T("consent.never.label"), [T("consent.never.items"), T("consent.never.how")], COLOUR.neverBar, layout.sectionW)[0],
    node("Note", [0, layout.noteY], [label(layout.note.text, layout.note.fs, `${COLOUR.note}/1.00`, layout.note.w, layout.note.h, { depth: 7 })]),
  );
  host.hidden = false;
  // on the popup stack, to be answered with a button: Escape does nothing, and the shared focus trap holds it
  Popup.opened(host, null);
  const next = document.createElement("div");
  const view = await NGUI.render(t, next, { scale: S, origin: [960, 540], z: 2000 });
  // one answer at a time, as the scan and Spine consents: a repeat click while the server answers sends nothing, and a
  // failure shows its error with the popup still up to answer again
  let choosing = false;
  for (const [name, granted] of [["Btn_Cancel", false], ["Btn_Ok", true]]) {
    const el = view.nodes.get(`Center/Popup/Btn/${name}/BG (1)`)?.els[0];
    if (!el) continue;
    hitButton(next, rect(el), async () => {
      if (choosing) return;
      choosing = true;
      try { await rpc("app.consent", { granted }); }
      catch (err) { choosing = false; notify(errorText(err), { kind: "error", error: err }); return; }
      assetsAllowed = granted;
      host.hidden = true;
      host.innerHTML = "";
      Popup.closed(host);
      resolve(granted);
    }, T(granted ? "consent.yes" : "consent.no"), 2100);
  }
  const change = view.nodes.get("Center/Popup/Cached/Change")?.els[0];
  if (change && native) {
    hitButton(next, rect(change), async () => {
      try {
        const folder = await rpc("settings.pickFolder", { start: cacheDir });
        if (!folder) return;
        const prefs = await rpc("settings.set", { key: "cacheDir", value: folder });
        draw(host, prefs.cacheDir, native, resolve);
      } catch (err) {
        notify(errorText(err), { kind: "error", error: err });
      }
    }, T("consent.change"), 2100);
  }
  host.replaceChildren(...next.childNodes);
}

// The same notice at startup and from Settings. Reading it in Settings never changes state or loads Spine.
export function spine(host, { over = false, readOnly = false } = {}) {
  if (spinePrompt) return spinePrompt;
  spinePrompt = new Promise((resolve) => {
    drawSpine(host, over, readOnly, resolve).catch((err) => {
      console.error("Spine notice", err);
      finishScan(host);
      resolve(null);
    });
  }).finally(() => { spinePrompt = null; });
  return spinePrompt;
}

// UIPopup_Sort with the ordinary message layout. A fixed message size wraps onto as many lines as needed,
// growing the frame above one centred button rather than shrinking the legal notice below the message minimum.
export function spineFrame() {
  const ok = T("picker.ok");
  const { body, width, height, y0, visible, scroll } = Popup.messageLayout({
    lines: [{ text: T("spine.license.notice"), fontSize: 52, minFontSize: 52 }], width: POPUP_FRAME.L.w, ok,
  });
  const tree = Popup.popupFrame(T("spine.license.title"), width, height, { ok, close: false });
  body.tree.pos = [0, y0];
  if (!scroll) find(tree, "Popup").children.push(body.tree);
  return { tree, width, height, body, y0, visible, scroll };
}

async function drawSpine(host, over, readOnly, resolve) {
  let choosing = false;
  const choose = async () => {
    if (choosing) return;
    choosing = true;
    let acknowledged = null;
    if (!readOnly) {
      try { acknowledged = await rpc("app.spineConsent", { granted: true }); }
      catch (err) { choosing = false; notify(errorText(err), { kind: "error", error: err }); return; }
      setSpineConsent(acknowledged);
    }
    finishScan(host);
    resolve(acknowledged);
  };
  host.classList.toggle("over", over);
  host.hidden = false;
  Popup.opened(host, null);
  const next = document.createElement("div");
  if (!assetsAllowed) {
    const fallback = Popup.plainMessage(next, { title: T("spine.license.title"), width: POPUP_FRAME.L.w,
      lines: [{ text: T("spine.license.notice"), fontSize: TYPE.heading.fs, minFontSize: TYPE.heading.fs }],
      ok: T("picker.ok"), dismissable: false }, choose);
    fallback.box.classList.add("spine-license-plain");
  } else {
    await Popup.frameReady();
    const { tree, width, body, y0, visible, scroll } = spineFrame();
    const view = await NGUI.render(tree, next, { scale: S, origin: [960, 540], z: 2000 });
    if (scroll) {
      const inner = Popup.scrollArea(next, [960 - width * S / 2, 540 - y0 * S, width * S, visible * S], body.height * S);
      await inner.ready;
      body.tree.pos = [0, 0];
      await NGUI.render(body.tree, inner, { scale: S, origin: [width * S / 2, 0], z: 2000 });
    }
    Popup.hit(next, view, "Center/Popup/Btn/Btn_Ok/BG (1)", choose, { name: T("picker.ok"), action: "ok", z: 2100 });
  }
  host.replaceChildren(...next.childNodes);
}

// --- the scan consent (decision 10) ---

// the shared popup frame at a size, its title and two buttons: [cancel, ok] captions; no close button
function consentFrame(title, w, h, [no, yes]) {
  const tree = Popup.popupFrame(title, w, h, { ok: yes, cancel: no, close: false });
  const popup = find(tree, "Popup");
  // the answer to a consent stays the plain blue button, not the gold affirmative one
  find(popup, "Btn/Btn_Ok").actionSemantic = "consent";
  return { tree, popup };
}

// a white section with the game's title bar and its lines (each wrapped to two lines at most); returns [node, height]
function section(name, top, titleText, items, tone, w = POPUP_FRAME.L.w - 2 * SPACE.xl) {
  const content = container({ w, h: 0, padding: SPACE.m }).inner;
  const texts = items.map(item => textItem(NGUI, item, TYPE.chip, { maxW: content.w, lines: 3 }));
  const title = textItem(NGUI, titleText, TYPE.heading, { maxW: content.w - 2 * SPACE.xl, lines: 2 });
  const bands = stack([{ w: content.w, h: Math.max(73, title.h) }, ...texts.map(f => ({ ...f, w: content.w }))],
    { x: SPACE.m, y: SPACE.m, w: content.w, gap: SPACE.m });
  const h = bands.h + 2 * SPACE.m, f = { w, h: 0, cx: 0, cy: 0 }, heading = bands.boxes[0];
  const kids = [node("Bg", [0, 0], [sprite("Common_Nonoutline_W_Bg", w, h, { pivot: 1, depth: 4 })]),
    node("Title_Bg", toNode(heading, 4, f), [sprite("Common_Bg_Raius10px", heading.w, heading.h,
      { depth: 5, color: `${tone}/1.00`, incline: 10 })], [
      // The title bar's decoration retains its prefab dimensions.
      node("Deco", [-heading.w / 2 + SPACE.l, 0], [sprite("Common_BGDeco", 68, 72, { depth: 6, color: "#2a5886/1.00", type: 0 })]),
      node("Label", [0, 0], [label(title.text, title.fs, "#ffffff/1.00", title.w, title.h, { depth: 7 })]),
    ]), ...texts.map((text, i) => node(`Item_${i}`, toNode(bands.boxes[i + 1], 3, f),
      [label(text.text, text.fs, "#2d4664/1.00", content.w, text.h, { pivot: 3, depth: 7 })]))];
  return [node(name, [0, top], [], kids), h];
}

// scan(host, {over}): the scan consent popup ("Check Game Files"): what is checked and what for, what never happens,
// and that declining keeps those checks off (Settings changes it). Resolves with the choice once a button is pressed:
// Allow saves consent and requests a scan. Don't Allow saves the refusal; declining does
// not quit. It must be answered: Escape and the backdrop do nothing. over: drawn over the settings and the message
// popups (opened from Settings' Check Game Files).
export function scan(host, { over = false } = {}) {
  return new Promise((resolve) => { drawScan(host, over, resolve).catch((err) => { console.error("scan consent", err); finishScan(host); resolve(null); }); });
}

function finishScan(host) {
  host.hidden = true;
  host.innerHTML = "";
  host.classList.remove("over");
  Popup.closed(host);
}

// Pure geometry shared by the scan consent builder and its six-locale fit checks.
export function scanLayout() {
  const W = POPUP_FRAME.L.w, panelW = W - 2 * SPACE.xl;
  const [checked, hc] = section("Checked", 0, T("mods.scan.will.label"), [T("mods.scan.will.steam"), T("mods.scan.will.items")], "#2d4c72", panelW);
  const [never, hn] = section("Never", 0, T("mods.scan.willNot.label"), [T("mods.scan.willNot.items")], COLOUR.neverBar, panelW);
  const note = textItem(NGUI, T("mods.scan.note"), TYPE.caption, { maxW: W - 2 * SPACE.xxxl, lines: 3 });
  const parts = [{ w: panelW, h: hc }, { w: panelW, h: hn }, { w: note.w, h: note.h }];
  const measured = stack(parts, { gap: SPACE.xl });
  const frame = frameGeometry("L", POPUP_FRAME.contentTop + measured.h + POPUP_FRAME.footer.bottom + CONTROL.buttonL.h + SPACE.m);
  const content = popupContent(frame.w, frame.h), boxes = stack(parts, { ...content, gap: SPACE.xl, align: "center", justify: "center" }).boxes;
  return { ...frame, content, boxes, checked, never, note };
}

async function drawScan(host, over, resolve) {
  await Popup.frameReady();
  const layout = scanLayout(), { checked, never, note, boxes } = layout;
  const { tree, popup } = consentFrame(T("mods.scan.title"), layout.w, layout.h, [T("mods.scan.no"), T("mods.scan.yes")]);
  checked.pos = toNode(boxes[0], 1, layout);
  never.pos = toNode(boxes[1], 1, layout);
  popup.children.push(checked, never, node("Note", toNode(boxes[2], 4, layout),
    [label(note.text, note.fs, `${COLOUR.note}/1.00`, note.w, note.h, { depth: 7 })]));
  host.classList.toggle("over", over);
  host.hidden = false;
  // on the popup stack, to be answered with a button: Escape does nothing, and nothing under it acts
  Popup.opened(host, null);
  const next = document.createElement("div");
  const view = await NGUI.render(tree, next, { scale: S, origin: [960, 540], z: 2000 });
  // the buttons over the drawn ones (placed from the prefab when a sprite is missing, so it can still be answered)
  for (const [name, granted, caption] of [["Btn_Cancel", false, T("mods.scan.no")], ["Btn_Ok", true, T("mods.scan.yes")]]) {
    Popup.hit(next, view, `Center/Popup/Btn/${name}/BG (1)`, async () => {
      try {
        await rpc("mods.consent", { granted });
      } catch (err) {
        notify(errorText(err), { kind: "error", error: err });
        return;
      }
      finishScan(host);
      resolve(granted);
    }, { name: caption, action: granted ? "ok" : "cancel", z: 2100 });
  }
  host.replaceChildren(...next.childNodes);
}

// detectGame(region, ask): the game folders Steam's records name for a region. Finding them reads those records, so
// without the Check Game Files consent the server refuses (scan_off): ask() shows the consent popup, and only an Allow
// searches; declined, nothing is searched (null).
export async function detectGame(region, ask) {
  try {
    return await rpc("settings.detectGame", { region });
  } catch (err) {
    if (err?.code !== "scan_off") throw err;
    if (!(await ask())) return null;
    return rpc("settings.detectGame", { region });
  }
}
