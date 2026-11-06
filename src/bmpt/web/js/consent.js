// First-launch consent for caching light game assets. Before consent the UI reads every game asset straight from the
// install (in memory). After consent only light UI assets (atlas sprites, UI textures, prefab layouts) are cached in
// the user data directory; portraits, memorial lobbies, models, voices and backgrounds are always read from the game
// files and never copied.
// Also the scan consent (scan()): whether the app may check the game files on its own (the start and the end of each
// portrait and memorial lobby file), asked once like the asset consent; Settings' Mods tab asks again or withdraws it.
import { notify } from "./popup.js";
import * as NGUI from "./ngui.js";
import * as Buttons from "./game-buttons.js";
import * as Popup from "./popup.js";
import { rpc } from "./api.js";
import { T, errorText } from "./i18n.js";
import { setSpineConsent } from "./spine-runtime.js";
import { COLOUR, FOOTER, POPUP, SMALL_BUTTON } from "./ui-tokens.js";
import { S, hitButton, wrapPath } from "./util.js";

const { node, sprite, label, find, rect } = NGUI;
let assetsAllowed = false, spinePrompt = null;

// The licence notice uses game assets only after the asset consent.
export function init(info) { assetsAllowed = info?.consent === true; }

// resolves with the choice (true / false) once a button is pressed; native: the folder picker is available
export async function show(host, cacheDir, native = false) {
  return new Promise((resolve) => draw(host, cacheDir, native, resolve));
}

// The asset consent's layout, popup-local units. Its Cached section holds the item, then a line of its own with the cache path
// and the light-blue Change button (the small button: 136 high, as wide as its caption and 90 more, 240 at least, its right
// edge where the old button's was); the frame is as tall as the content needs, the Note 16 above the buttons' tops.
const ASSET = { w: 1900, minH: 1180, section: 1740, never: 300, item: { y: -150, h: 50 }, top: 148, between: 40,
  rowGap: 16, foot: 24, noteGap: 30, noteH: 50, left: -800, right: 815, pathGap: 30 };
export function assetLayout(cacheDir) {
  const bf = NGUI.fitText(T("consent.change"), SMALL_BUTTON.fs, SMALL_BUTTON.fsMin, SMALL_BUTTON.w - SMALL_BUTTON.fitInset);
  const bw = Math.max(SMALL_BUTTON.minW, NGUI.glyphWidth(bf.text, bf.fs) + SMALL_BUTTON.glyphPad);
  const room = ASSET.right - bw - ASSET.pathGap - ASSET.left;
  // where the cache goes, wrapped rather than cut
  const to = wrapPath(T("cacheTo", { path: cacheDir }), 40, 30, room, 2);
  const pathH = to.fs * 1.3 * to.lines + 8, rowH = Math.max(SMALL_BUTTON.h, pathH);
  const rowTop = ASSET.item.y - ASSET.item.h / 2 - ASSET.rowGap, rowY = rowTop - rowH / 2;
  const cachedH = -rowTop + rowH + ASSET.foot;
  const content = ASSET.top + cachedH + ASSET.between + ASSET.never + ASSET.noteGap + ASSET.noteH;
  const h = Math.max(ASSET.minH, content + POPUP.clearance + FOOTER.centre + FOOTER.body.h / 2);
  const cachedTop = POPUP.frameY + h / 2 - ASSET.top, neverTop = cachedTop - cachedH - ASSET.between;
  const noteBottom = neverTop - ASSET.never - ASSET.noteGap - ASSET.noteH;
  return { w: ASSET.w, h, cachedTop, cachedH, neverTop, noteY: noteBottom + ASSET.noteH / 2, noteBottom, to, rowY, rowH, room,
    change: { x: ASSET.right - bw / 2, w: bw, caption: bf }, itemBox: [ASSET.left, ASSET.item.y - ASSET.item.h / 2, ASSET.item.y + ASSET.item.h / 2] };
}

async function draw(host, cacheDir, native, resolve) {
  await Buttons.ready();
  await Popup.frameReady();
  const layout = assetLayout(cacheDir);
  // the shared popup frame: no close button, to be answered with one of its two buttons
  const { tree: t, popup } = consentFrame(T("consent.title"), layout.w, layout.h, [T("consent.no"), T("consent.yes")]);
  // two rows: what is cached, what never is; each a white section with the game's title bar
  const row = (name, y, h, titleText, items, tone) => node(name, [0, y], [], [
    node("Bg", [0, 0], [sprite("Common_Nonoutline_W_Bg", ASSET.section, h, { pivot: 1, depth: 4 })]),
    node("Title_Bg", [-850, -56], [sprite("Common_Bg_Raius10px", 1700, 73, { pivot: 3, depth: 5, color: `${tone}/1.00`, incline: 10 })], [
      node("Deco", [28.8, 0], [sprite("Common_BGDeco", 68, 72, { depth: 6, color: "#2a5886/1.00", type: 0 })]),
      node("Label", [40, 0], [label(titleText, 46, "#ffffff/1.00", 1500, 50, { pivot: 3, depth: 7 })]),
    ]),
    ...items.map((item, i) => node(`Item_${i}`, [-800, -150 - i * 62], [label(item, 40, "#2d4664/1.00", 1600, 50, { pivot: 3, depth: 7 })])),
  ]);
  const cached = row("Cached", layout.cachedTop, layout.cachedH, T("consent.cached.label"), [T("consent.cached.items")], "#2d4c72");
  const { to } = layout;
  cached.children.push(
    node("Path", [ASSET.left, layout.rowY], [label(to.text, to.fs, "#2d4664/1.00", layout.room, to.fs * 1.3 * to.lines + 8, { pivot: 3, depth: 7 })]),
    Buttons.smallButton("Change", layout.change.x, layout.rowY, T("consent.change"), { w: layout.change.w, enabled: native, depth: 8 }));
  popup.children.push(
    cached,
    row("Never", layout.neverTop, ASSET.never, T("consent.never.label"), [T("consent.never.items"), T("consent.never.how")], COLOUR.neverBar),
    node("Note", [0, layout.noteY], [label(T("consent.note"), 36, `${COLOUR.note}/1.00`, 1700, ASSET.noteH, { depth: 7 })]),
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
  const { body, width, height, y0 } = Popup.messageLayout({
    lines: [{ text: T("spine.license.notice"), fontSize: 52, minFontSize: 52 }], width: 1900, ok,
  });
  const tree = Popup.popupFrame(T("spine.license.title"), width, height, { ok, close: false });
  body.tree.pos = [0, y0];
  find(tree, "Popup").children.push(body.tree);
  return { tree, width, height };
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
    const back = document.createElement("div");
    back.className = "plain-popup-back";
    const box = document.createElement("div");
    box.className = "plain-popup spine-license-plain";
    for (const [tag, key] of [["h2", "title"], ["p", "notice"]]) {
      const el = document.createElement(tag);
      el.textContent = T(`spine.license.${key}`);
      box.appendChild(el);
    }
    const row = document.createElement("div");
    row.className = "message-actions";
    const button = document.createElement("button");
    button.className = "message-button";
    button.textContent = T("picker.ok");
    button.setAttribute("aria-label", button.textContent);
    button.dataset.action = "ok";
    button.addEventListener("click", choose);
    row.appendChild(button);
    box.appendChild(row);
    next.appendChild(back);
    next.appendChild(box);
  } else {
    await Popup.frameReady();
    const { tree } = spineFrame();
    const view = await NGUI.render(tree, next, { scale: S, origin: [960, 540], z: 2000 });
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
function section(name, top, titleText, items, tone, w = 1740) {
  // every line at the rows' size, a long one on two lines
  const lines = items.map((item) => NGUI.fitText(item, 40, 40, w - 140, 2));
  const h = 120 + lines.reduce((a, f) => a + f.lines * f.fs * 1.3 + 14, 0) + 20;
  let y = -120;
  const kids = lines.map((f, i) => {
    const lh = f.lines * f.fs * 1.3;
    const n = node(`Item_${i}`, [-w / 2 + 70, y - lh / 2], [label(f.text, f.fs, "#2d4664/1.00", w - 140, lh + 8, { pivot: 3, depth: 7 })]);
    y -= lh + 14;
    return n;
  });
  return [node(name, [0, top], [], [
    node("Bg", [0, 0], [sprite("Common_Nonoutline_W_Bg", w, h, { pivot: 1, depth: 4 })]),
    node("Title_Bg", [-w / 2 + 20, -56], [sprite("Common_Bg_Raius10px", w - 40, 73, { pivot: 3, depth: 5, color: `${tone}/1.00`, incline: 10 })], [
      node("Deco", [28.8, 0], [sprite("Common_BGDeco", 68, 72, { depth: 6, color: "#2a5886/1.00", type: 0 })]),
      node("Label", [40, 0], [label(titleText, 46, "#ffffff/1.00", w - 240, 50, { pivot: 3, depth: 7 })]),
    ]),
    ...kids,
  ]), h];
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

async function drawScan(host, over, resolve) {
  await Popup.frameReady();
  const W = 1900;
  const [checked, hc] = section("Checked", 0, T("mods.scan.will.label"), [T("mods.scan.will.items")], "#2d4c72");
  const [never, hn] = section("Never", 0, T("mods.scan.willNot.label"), [T("mods.scan.willNot.items")], COLOUR.neverBar);
  const note = NGUI.fitText(T("mods.scan.note"), 36, 28, 1700, 2);
  const noteH = note.lines * note.fs * 1.3;
  // the title 170, the sections with a gap, the note, the buttons 320
  const H = Math.max(1000, 170 + hc + 50 + hn + 40 + noteH + 320);
  const { tree, popup } = consentFrame(T("mods.scan.title"), W, H, [T("mods.scan.no"), T("mods.scan.yes")]);
  let y = H / 2 - 170;
  checked.pos = [0, y];
  y -= hc + 50;
  never.pos = [0, y];
  y -= hn + 40;
  popup.children.push(checked, never, node("Note", [0, y - noteH / 2], [label(note.text, note.fs, `${COLOUR.note}/1.00`, 1700, noteH + 8, { depth: 7 })]));
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
