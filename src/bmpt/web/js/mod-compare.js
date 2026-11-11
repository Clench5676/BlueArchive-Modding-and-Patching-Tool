import * as NGUI from "./ngui.js";
import { T } from "./i18n.js";
import { $, S } from "./util.js";
import { closed as popupClosed, closable, hit as hitNode, popupFrame, ready, scrollArea } from "./popup.js";
import { compareFrame } from "./popup-frames.js";
import { COLOUR } from "./ui-tokens.js";
import { loadPlayer } from "./spine-load.js";
import { ensureSpine } from "./spine-runtime.js";
import * as PanZoom from "./pan-zoom.js";

const { node, label, find } = NGUI;

// Compare assets on the existing popup frame, in a detail or stage-level host, as a layer of its own in that host (as
// the message popups): a message over it draws above it, Escape closes the topmost layer, and closing it leaves any
// other layer of the host alone.
export async function show(result, title = T("act.compare"), hostId = "popup", review = null) {
  await ready();
  const host = $(hostId);
  const changed = (result.assets || []).filter((d) => d.status !== "same");
  const frame = compareFrame(!!review, changed.length);
  const t = popupFrame(title, frame.w, frame.h, review ? { ok: review.accept, cancel: review.discard } : {});
  const popup = find(t, "Popup");
  const rows = changed.slice(0, frame.rows.max);
  const color = { changed: COLOUR.warning, added: COLOUR.success, removed: COLOUR.error };
  if (!review && !rows.length) popup.children.push(node("None", [0, frame.rows.y], [label(T("compare.none"), 44, "#2d4664/1.00", 1600, 60, { depth: 8 })]));
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
    // Pictures on the left, the report on the right, as the import page lays an entry out: a portrait as two upright
    // panels side by side, a memorial lobby as two wide ones above each other.
    const inner = scrollArea(next, [960 - frame.scroll.width / 2 * S, 540 - frame.scroll.top * S, frame.scroll.width * S, frame.scroll.height * S], 0);
    inner.classList.add("repair-compare");
    inner.style.fontSize = `${36 * S}px`;
    inner.style.height = `${frame.scroll.height * S}px`;
    const part = (cls, parent = inner, value = "") => {
      const el = document.createElement("div");
      el.className = cls;
      el.textContent = value;
      parent.appendChild(el);
      return el;
    };
    const previews = part("repair-previews"), notes = part("repair-notes");
    previews.dataset.kind = review.kind === "lobby" ? "wide" : "tall";
    const drawings = result.drawings || {};
    const drawn = !!(drawings.original?.length || drawings.repaired?.length);
    // Both sides are looked at through one view: the wheel zooms at the pointer, a drag moves the picture, a double
    // click goes back to where it started. A lobby starts filling its frame, a portrait whole.
    const views = [], marked = [];  // marked: per picture, where its rings go and what tells them
    let expression = null;  // the graft's expression the pictures show
    let view = { ...PanZoom.REST }, rest = view, framed = review.kind !== "lobby", marking = false;
    const show = (value) => {
      view = value;
      for (const el of views) Object.assign(el.style, PanZoom.style(view));
    };
    // Rings around the parts the report names (review.marks: region names), on both pictures; read when switched on.
    const mark = (on) => {
      marking = on;
      for (const { layer, rings, toggle } of marked) {
        layer.replaceChildren();
        toggle?.setAttribute("aria-pressed", String(on));
        for (const box of on ? rings() : []) Object.assign(part("repair-ring", layer).style, PanZoom.ringStyle(PanZoom.ring(box)));
      }
    };
    const tool = (bar, caption, name, fn) => {
      const button = document.createElement("button");
      button.className = "plain-line-button";
      button.textContent = caption;
      button.setAttribute("aria-label", name);
      // a press on a tool is not the start of a drag, nor half of a double click on the picture
      for (const type of ["pointerdown", "dblclick"]) button.addEventListener(type, (event) => event.stopPropagation());
      button.addEventListener("click", fn);
      bar.appendChild(button);
      return button;
    };
    const frameIn = (host) => {
      const el = part("repair-view", host);
      views.push(el);
      Object.assign(el.style, PanZoom.style(view));
      // shown while the pointer is over the picture: smaller, larger and, when the report names parts, the rings
      const bar = part("repair-tools", host), entry = { layer: null, rings: () => [], toggle: null };
      tool(bar, "\u2212", T("remap.zoomOut"), () => show(PanZoom.zoomAt(view, 1 / 1.5)));
      tool(bar, "+", T("remap.zoomIn"), () => show(PanZoom.zoomAt(view, 1.5)));
      if (review.marks?.length) {
        entry.toggle = tool(bar, "\u25ef", T("remap.marks"), () => mark(!marking));
        entry.toggle.classList.add("repair-mark-toggle");
        entry.toggle.setAttribute("aria-pressed", "false");
      }
      el.repairEntry = entry;
      marked.push(entry);
      const point = (event) => {
        const box = host.getBoundingClientRect();
        return [box, (event.clientX - box.left) / (box.width || 1), (event.clientY - box.top) / (box.height || 1)];
      };
      host.addEventListener("wheel", (event) => {
        event.preventDefault();
        const [, px, py] = point(event);
        show(PanZoom.zoomAt(view, 1.0015 ** -event.deltaY, px, py));
      }, { passive: false });
      let held = null;
      host.addEventListener("pointerdown", (event) => { held = [event.clientX, event.clientY]; host.setPointerCapture?.(event.pointerId); });
      host.addEventListener("pointermove", (event) => {
        if (!held) return;
        const [box] = point(event);
        show(PanZoom.panBy(view, (event.clientX - held[0]) / (box.width || 1), (event.clientY - held[1]) / (box.height || 1)));
        held = [event.clientX, event.clientY];
      });
      for (const type of ["pointerup", "pointercancel"]) host.addEventListener(type, () => { held = null; });
      host.addEventListener("dblclick", () => show(rest));
      return el;
    };
    // the first picture drawn tells its proportions: a lobby then fills the frame it is in
    const fill = (host, picture) => {
      if (framed) return;
      framed = true;
      const box = host.getBoundingClientRect?.();
      if (box?.width > 0 && box?.height > 0) show(rest = PanZoom.cover(box.width / box.height, picture));
    };
    for (const side of ["original", "repaired"]) {
      const panel = part("repair-panel", previews);
      const caption = part("repair-caption", panel, T(side === "original" ? "remap.original" : "remap.after"));
      const stage = part("repair-stage", panel);
      for (const scene of drawn ? drawings[side] || [] : []) {
        const playerHost = part("repair-player", stage), inside = frameIn(playerHost);
        // the player has a box of its own (it empties the one it is given); the rings lie over it, inside the view,
        // so they zoom and move with the drawing
        const canvas = part("repair-canvas", inside);
        inside.repairEntry.layer = part("repair-marks", inside);
        startPlayers.push(() => {
          const player = previewPlayer(canvas, scene, side, result.pages, (picture) => fill(playerHost, picture));
          if (!player) return;
          players.push(player);
          inside.repairEntry.rings = () => player.parts(review.marks || []);
          if (expression) player.play(expression);
        });
      }
      // without a drawing (no skeleton in the files, no runtime) the pages themselves are the picture
      for (const page of drawn ? [] : result.pages || []) {
        if (!page[side]) continue;
        const img = document.createElement("img");
        img.src = page[side];
        img.alt = `${caption.textContent} - ${page.name}`;
        frameIn(part("repair-player", stage)).appendChild(img);
      }
    }
    part("repair-verdict", notes, T(review.partial ? "remap.partial" : "remap.preview"));
    // a graft's expressions, one at a time on both pictures (a picture without it stays in its rest pose)
    if (review.expressions?.length) {
      const row = part("repair-line repair-steps", notes), caption = document.createElement("div");
      let at = 0;
      const go = (step) => {
        at = (at + step + review.expressions.length) % review.expressions.length;
        expression = review.expressions[at];
        caption.textContent = T("remap.expression", { name: expression, n: at + 1, total: review.expressions.length });
        for (const player of players) player.play(expression);
        if (marking) mark(true);
      };
      tool(row, "\u25c0", T("remap.previousExpression"), () => go(-1));
      row.appendChild(caption);
      tool(row, "\u25b6", T("remap.nextExpression"), () => go(1));
      go(0);
    }
    // a line is text, or {text, button: {label, fn}} for one with a small button at its end, as in message popups
    for (const line of review.lines) {
      if (typeof line === "string") { part("", notes, line); continue; }
      const row = part("repair-line", notes), caption = document.createElement("div"), button = document.createElement("button");
      caption.textContent = line.text;
      button.className = "plain-line-button";
      button.textContent = line.button.label;
      button.setAttribute("aria-label", `${line.button.label} - ${line.text}`);
      button.addEventListener("click", () => line.button.fn?.());
      row.appendChild(caption);
      row.appendChild(button);
    }
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

// `drawn(width / height)` is told once, when the skeleton is up, with the proportions of the picture the player fits.
function previewPlayer(host, scene, side, pages, drawn = () => {}) {
  let disposed = false, load = null, live = null;
  const unavailable = () => { if (!disposed) host.textContent = T("remap.previewUnavailable"); };
  let wanted = null;  // the animation on track 1, once the skeleton is up
  const show = () => {
    const p = live;
    if (!p?.skeleton || disposed) return;
    if (wanted && p.skeleton.data.findAnimation(wanted)) p.animationState.setAnimation(1, wanted, true);
    else p.animationState.setEmptyAnimation(1, 0);
    p.animationState.apply(p.skeleton);
    p.skeleton.updateWorldTransform(spine.Physics.update);
  };
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
        // the picture up to the frame's edges: the frame is small, and the view zooms and moves
        viewport: { padLeft: "2%", padRight: "2%", padTop: "2%", padBottom: "2%" },
        success: (p) => {
          live = p;
          const idle = p.skeleton.data.animations.find((a) => /^Idle_01$/i.test(a.name)) || p.skeleton.data.animations[0];
          if (idle) p.animationState.setAnimation(0, idle.name, true);
          else p.animationState.setEmptyAnimation(0, 0).trackEnd = Number.MAX_VALUE;
          p.animationState.timeScale = 0;
          show();
          // the player fits its picture when it draws its first frame
          const fitted = (tries) => {
            if (disposed) return;
            const box = p.currentViewport;
            if (box?.width > 0 && box?.height > 0) drawn(box.width / box.height);
            else if (tries > 0) requestAnimationFrame(() => fitted(tries - 1));
          };
          fitted(60);
        },
      }, { onError: unavailable });
      load.ready.catch(unavailable);
    } catch { unavailable(); }
  };
  start();
  // Where the skeleton, as posed, draws the atlas regions `names`: one [left, top, right, bottom] per attachment, in
  // sizes of the picture's box (0..1, from its top left), through the player's own camera. Nothing before it draws.
  const parts = (names) => {
    const player = live, wanted = new Set(names), boxes = [];
    if (disposed || !player?.skeleton || !wanted.size) return boxes;
    try {
      const camera = player.sceneRenderer.camera;
      camera.update();
      const m = camera.projectionView.values;
      for (const slot of player.skeleton.drawOrder) {
        const a = slot.getAttachment();
        if (!a || !slot.bone.active || !wanted.has(a.region?.name)) continue;
        let v;
        if (a instanceof spine.RegionAttachment) { v = new Float32Array(8); a.computeWorldVertices(slot, v, 0, 2); }
        else if (a instanceof spine.MeshAttachment) { v = new Float32Array(a.worldVerticesLength); a.computeWorldVertices(slot, 0, v.length, v, 0, 2); }
        else continue;
        let left = Infinity, top = Infinity, right = -Infinity, bottom = -Infinity;
        for (let i = 0; i < v.length; i += 2) {
          const x = (m[0] * v[i] + m[4] * v[i + 1] + m[12] + 1) / 2, y = (1 - (m[1] * v[i] + m[5] * v[i + 1] + m[13])) / 2;
          left = Math.min(left, x); right = Math.max(right, x); top = Math.min(top, y); bottom = Math.max(bottom, y);
        }
        if (Number.isFinite(left + top + right + bottom)) boxes.push([left, top, right, bottom]);
      }
    } catch { return []; }
    return boxes;
  };
  const play = (name) => { wanted = name; show(); };
  return { dispose: () => { disposed = true; load?.dispose(); }, parts, play };
}
