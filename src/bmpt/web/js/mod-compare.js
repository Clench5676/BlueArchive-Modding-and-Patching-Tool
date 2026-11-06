import * as NGUI from "./ngui.js";
import { T } from "./i18n.js";
import { $, S } from "./util.js";
import { closed as popupClosed, closable, hit as hitNode, popupFrame, ready, scrollArea } from "./popup.js";
import { compareFrame } from "./popup-frames.js";
import { COLOUR } from "./ui-tokens.js";
import { loadPlayer } from "./spine-load.js";
import { ensureSpine } from "./spine-runtime.js";

const { node, label, find } = NGUI;

// Compare assets on the existing popup frame, in a detail or stage-level host, as a layer of its own in that host (as
// the message popups): a message over it draws above it, Escape closes the topmost layer, and closing it leaves any
// other layer of the host alone.
export async function show(result, title = T("act.compare"), hostId = "popup", review = null) {
  await ready();
  const host = $(hostId);
  const frame = compareFrame(!!review);
  const t = popupFrame(title, frame.w, frame.h, review ? { ok: review.accept, cancel: review.discard } : {});
  const popup = find(t, "Popup");
  const rows = (result.assets || []).filter((d) => d.status !== "same").slice(0, frame.rows.max);
  const color = { changed: COLOUR.warning, added: COLOUR.success, removed: COLOUR.error };
  if (!review && !rows.length) popup.children.push(node("None", [0, 120], [label(T("compare.none"), 44, "#2d4664/1.00", 1600, 60, { depth: 8 })]));
  (review ? [] : rows).forEach((d, i) => {
    const y = frame.rows.y - i * frame.rows.pitch;
    popup.children.push(node(`R_${i}`, [-820, y], [label(`${d.type} - ${d.name}`, 38, "#2d4664/1.00", 1300, 50, { pivot: 3, depth: 8, ellipsis: true })]),
      node(`S_${i}`, [820, y], [label(T(`compare.${d.status}`), 38, `${color[d.status] || "#2d4664"}/1.00`, 300, 50, { pivot: 5, depth: 8 })]));
  });
  const layer = document.createElement("div");
  layer.className = "popup-layer";
  layer.tabIndex = -1;
  host.appendChild(layer);
  host.hidden = false;
  const next = document.createElement("div");
  let view;
  try { view = await NGUI.render(t, next, { scale: S, origin: [960, 540], z: 10 }); }
  catch (err) {
    layer.remove();
    if (!host.children.length) host.hidden = true;
    throw err;
  }
  const players = [], startPlayers = [];
  if (review) {
    const inner = scrollArea(next, [960 - frame.scroll.width / 2 * S, 540 - frame.scroll.top * S, frame.scroll.width * S, frame.scroll.height * S], 0);
    inner.classList.add("repair-compare");
    inner.style.fontSize = `${36 * S}px`;
    const text = (value) => {
      const line = document.createElement("div");
      line.textContent = value;
      inner.appendChild(line);
      return line;
    };
    text(T(review.partial ? "remap.partial" : "remap.preview"));
    for (const line of review.lines) text(line);
    const drawings = result.drawings || {};
    if (drawings.original?.length || drawings.repaired?.length) {
      const pair = text("");
      pair.className = "repair-pages";
      for (const side of ["original", "repaired"]) {
        const panel = document.createElement("div"), caption = document.createElement("div");
        caption.textContent = T(side === "original" ? "remap.original" : "remap.after");
        panel.appendChild(caption);
        pair.appendChild(panel);
        for (const scene of drawings[side] || []) {
          const playerHost = document.createElement("div");
          playerHost.className = "repair-player";
          playerHost.style.height = `${480 * S}px`;
          panel.appendChild(playerHost);
          startPlayers.push(() => {
            const player = previewPlayer(playerHost, scene, side, result.pages);
            if (player) players.push(player);
          });
        }
      }
    }
    for (const page of result.pages || []) {
      text(page.name);
      const pair = text("");
      pair.className = "repair-pages";
      for (const side of ["original", "repaired"]) {
        const panel = document.createElement("div"), caption = document.createElement("div");
        caption.textContent = T(side === "original" ? "remap.original" : "remap.after");
        panel.appendChild(caption);
        if (page[side]) {
          const img = document.createElement("img");
          img.src = page[side];
          img.alt = `${caption.textContent} - ${page.name}`;
          img.style.height = `${400 * S}px`;
          panel.appendChild(img);
        }
        pair.appendChild(panel);
      }
    }
    for (const row of rows) text(`${row.type} - ${row.name} - ${T(`compare.${row.status}`)}`);
    inner.style.height = "auto";
  }
  let finish;
  const closed = new Promise((resolve) => { finish = resolve; });
  let finished = false;
  const done = (accepted = false) => {
    if (finished) return;
    finished = true;
    for (const player of players) player.dispose();
    layer.remove();
    popupClosed(host, layer);
    if (!host.children.length) host.hidden = true;
    finish(accepted === true);
  };
  hitNode(next, view, "Center/Popup/Base/BG/Btn_Close", done, { name: T("display.close"), action: "close" });
  if (review) {
    hitNode(next, view, "Center/Popup/Btn/Btn_Ok/BG (1)", () => done(true), { name: review.accept, action: "ok" });
    hitNode(next, view, "Center/Popup/Btn/Btn_Cancel/BG (1)", () => done(false), { name: review.discard, action: "cancel" });
  }
  closable(view, host, done, { el: layer });
  layer.replaceChildren(...next.childNodes);
  for (const start of startPlayers) start();
  return closed;
}

function previewPlayer(host, scene, side, pages) {
  let disposed = false, load = null;
  const unavailable = () => { if (!disposed) host.textContent = T("remap.previewUnavailable"); };
  const start = async () => {
    try {
      const allowed = await ensureSpine();
      if (disposed) return;
      if (!allowed) return;
      host.replaceChildren();
      const skeleton = scene.json ? "skeleton.json" : "skeleton.skel";
      const bytes = Uint8Array.from(atob(scene.skeleton), (c) => c.charCodeAt(0));
      // These readers use the reviewed data directly, preserving UTF-8 names without fetching any files.
      const downloader = new spine.Downloader();
      downloader.downloadText = (_url, success) => success(scene.atlas);
      downloader.downloadBinary = (_url, success) => success(bytes);
      downloader.downloadJson = (_url, success, error) => {
        try { success(JSON.parse(new TextDecoder().decode(bytes).trim())); } catch (err) { error(String(err)); }
      };
      const rawDataURIs = {};
      for (const page of new spine.TextureAtlas(scene.atlas).pages) {
        const key = page.name.replaceAll("\\", "/").split("/").at(-1).toLowerCase().replace(/\.png$/i, "");
        const image = pages.find((p) => p.name === key)?.[side];
        if (image) rawDataURIs[page.name] = image;
      }
      load = loadPlayer((config) => new spine.SpinePlayer(host, config), {
        skeleton, atlas: "skeleton.atlas", downloader, rawDataURIs,
        premultipliedAlpha: false, alpha: true, backgroundColor: "#00000000",
        showControls: false, showLoading: false, animation: undefined,
        success: (p) => {
          const idle = p.skeleton.data.animations.find((a) => /^Idle_01$/i.test(a.name)) || p.skeleton.data.animations[0];
          if (idle) p.animationState.setAnimation(0, idle.name, true);
          else p.animationState.setEmptyAnimation(0, 0).trackEnd = Number.MAX_VALUE;
          p.animationState.timeScale = 0;
        },
      }, { onError: unavailable });
      load.ready.catch(unavailable);
    } catch { unavailable(); }
  };
  start();
  return { dispose: () => { disposed = true; load?.dispose(); } };
}
