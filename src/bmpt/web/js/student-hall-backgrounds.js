// LobbyCommon/LobbyCommonNight use authored NGUI layers and serialized shader pass blending.
import * as NGUI from "./ui/ngui.js";
import { withTimeout } from "./core/async.js";
import { T } from "./core/i18n.js";
import { S } from "./core/util.js";

/**
 * Choose the Hall background: the saved one, else office-day, else the first with a picture, else the first.
 * @param {string|undefined} saved The saved background's name.
 * @param {object[]} choices
 * @returns {object|null} The background; null without any.
 */
export function backgroundChoice(saved, choices) {
  return (
    choices.find((b) => b.name === saved) ||
    choices.find((b) => b.name === "office-day") ||
    choices.find((b) => b.url) ||
    choices[0] ||
    null
  );
}

/** The caption of a background: its locale key's text, or its name without the BG_ and _Collection marks. */
export const label = (background) =>
  background.labelKey ? T(background.labelKey) : background.name.replace(/^BG_|_Collection$/g, "").replaceAll("_", " ");

/**
 * The CSS and canvas blend modes of a background layer's material.
 * @param {object|undefined} material The material, with its shader and blend factors.
 * @returns {{css: string, canvas: string}} The blend modes; normal for a plain or unsupported material.
 */
export function blending(material) {
  if (!material) return { css: "normal", canvas: "source-over" };
  const { shader, src, dst, srcA = src, dstA = dst, op = 0, opA = 0, mask = 15 } = material;
  if (
    !["Unlit/Transparent Colored", "Unlit/Transparent Colored Additive"].includes(shader) ||
    op !== 0 ||
    opA !== 0 ||
    mask !== 15 ||
    src !== 5 ||
    ![1, 10].includes(dst) ||
    srcA !== src ||
    dstA !== dst
  )
    throw new Error(`Unsupported office shader pass: ${shader}`);
  // SrcAlpha/One is additive light, not Screen and not source-over. Authored alpha stays untouched.
  return dst === 1 ? { css: "plus-lighter", canvas: "lighter" } : { css: "normal", canvas: "source-over" };
}

/**
 * Draw a background's layers into an element, with the native lossless images instead of NGUI's optional
 * small-widget downsample.
 * @returns {Promise<RenderView|null>} What was drawn; null when cancelled.
 */
export async function layers(background, target, { signal } = {}) {
  const response = await withTimeout(fetch(background.layoutUrl, { signal }), 15000, "Office background", { signal });
  if (!response.ok) throw new Error(`Office background: HTTP ${response.status}`);
  const asset = await response.json();
  if (signal?.aborted) return null;
  const textures = {},
    modes = new Map(),
    tasks = [];
  function visit(node, parent = "") {
    if (node.active === false) return;
    const path = parent ? `${parent}/${node.name}` : node.name;
    for (const widget of node.widgets || []) {
      if (!widget.texture) continue;
      const url = asset.textures[widget.texture];
      if (!url) throw new Error(`Office texture unavailable: ${widget.texture}`);
      modes.set(path, blending(widget.material));
      tasks.push(
        NGUI.load(url, { signal }).then(async () => {
          const tint = String(widget.color || "#ffffff/1.00").split("/")[0];
          textures[path] = await NGUI.tinted(url, tint);
        }),
      );
    }
    for (const child of node.children || []) visit(child, path);
  }
  visit(asset.tree);
  await Promise.all(tasks);
  if (signal?.aborted) return null;
  // Prepare offscreen so an additive image can never flash as an ordinary black rectangle while loading.
  const staged = document.createElement("div");
  const view = await NGUI.render(asset.tree, staged, { scale: S, origin: [960, 540], textures, signal });
  await Promise.all(view.pictures || []);
  if (signal?.aborted) return null;
  for (const [path, entry] of view.nodes) {
    const mode = modes.get(path);
    if (!mode) continue;
    for (const el of entry.els) {
      // Retain the native lossless image instead of NGUI's optional small-widget downsample.
      el.style.backgroundImage = `url("${textures[path]}")`;
      el.style.mixBlendMode = mode.css;
      el.dataset.officeComposite = mode.canvas;
    }
  }
  // All background layers share an isolated blending group below the portrait actors.
  target.style.isolation = "isolate";
  target.style.overflow = "hidden";
  target.replaceChildren(...(staged.childNodes || []));
  return view;
}

/**
 * Draw a background into an element: its layers, or its picture.
 * @param {object} [options]
 * @param {object[]} [options.choices] The available backgrounds, for the fallback.
 * @returns {Promise<void>} Settles when it has finished.
 */
export async function render(background, target, { signal, choices = [] } = {}) {
  if (!background || signal?.aborted) return;
  try {
    if (background.layoutUrl) {
      await layers(background, target, { signal });
      if (!signal?.aborted) {
        target.dataset.background = background.name;
        target.dataset.backgroundMotion = "static";
      }
    } else {
      const image = await NGUI.load(background.url, { signal });
      if (signal?.aborted) return;
      image.alt = "";
      Object.assign(image.style, {
        position: "absolute",
        inset: "0",
        width: "100%",
        height: "100%",
        objectFit: "cover",
      });
      target.replaceChildren(image);
      target.dataset.background = background.name;
    }
  } catch (error) {
    if (signal?.aborted) return;
    const fallback = choices.find((b) => b.url && b.name !== background.name);
    if (!background.layoutUrl || !fallback) throw error;
    console.warn("Office background unavailable", error);
    target.replaceChildren();
    await render(fallback, target, { signal, choices });
  }
}

/**
 * Paint a drawn background's layers onto a canvas. Both picker previews and full-scene layers use the same pass
 * blend and NGUI geometry.
 * @param {RenderView} view What layers() drew.
 * @param {CanvasRenderingContext2D} context The canvas context, 1920 x 1080.
 * @returns {Promise<void>} Settles when it has finished.
 */
export async function paint(view, context, { signal } = {}) {
  const ordered = [...view.nodes.values()]
    .flatMap((n) => n.els)
    .sort((a, b) => Number(a.style.zIndex) - Number(b.style.zIndex));
  for (const layer of ordered) {
    if (signal?.aborted) return;
    const style = layer.style,
      source = /^url\((?:"([^"]+)"|'([^']+)'|(.+))\)$/.exec(style.backgroundImage);
    if (!source) continue;
    const image = await NGUI.load(source[1] || source[2] || source[3], { signal });
    if (signal?.aborted) return;
    const x = parseFloat(style.left),
      y = parseFloat(style.top),
      width = parseFloat(style.width),
      height = parseFloat(style.height);
    context.save();
    context.globalAlpha = Number(style.opacity || 1);
    context.globalCompositeOperation = layer.dataset.officeComposite || "source-over";
    context.translate(x + width / 2, y + height / 2);
    if (style.transform) {
      const m = new DOMMatrix(style.transform);
      context.transform(m.a, m.b, m.c, m.d, m.e, m.f);
    }
    context.drawImage(image, -width / 2, -height / 2, width, height);
    context.restore();
  }
}

/**
 * A picture of a background for the picker: its URL, or a drawn layout as an object URL.
 * @returns {Promise<string|null>} The picture's URL; null when cancelled.
 */
export async function preview(background, { signal } = {}) {
  if (!background.layoutUrl) return background.url;
  const target = document.createElement("div"),
    view = await layers(background, target, { signal });
  if (!view || signal?.aborted) return null;
  const canvas = document.createElement("canvas");
  canvas.width = 1920;
  canvas.height = 1080;
  await paint(view, canvas.getContext("2d"), { signal });
  if (signal?.aborted) return null;
  const blob = await new Promise((resolve) => canvas.toBlob(resolve, "image/png"));
  if (!blob || signal?.aborted) return null;
  const url = URL.createObjectURL(blob);
  signal?.addEventListener("abort", () => URL.revokeObjectURL(url), { once: true });
  return url;
}
