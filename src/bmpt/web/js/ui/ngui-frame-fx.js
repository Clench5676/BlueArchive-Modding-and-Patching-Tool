// The stationary UV particle in FX_UI_Mulligan_03 / FX_UI_Mulligan_Sub_03: its mesh and material come from
// UIPopup_Mulligan and the mask shader scrolls FX_TEX_Bulletline_03; no TweenAlpha, UISpriteAnimation or CSS
// animation is involved.
import { fragmentSource } from "../particles-gl.js";
import { toLinear, toSrgb } from "../app/touch-fx.js";
import { uiExportUrl } from "../core/api.js";
import { applyDir, euler } from "../particles.js";

/**
 * Build the vertex data of the frame effect's mesh, stretched like the frame's sliced sprite.
 * @param {object} effect The effect read from the prefab: its mesh, particle system, UV scale, widget and
 *     source sizes and incline.
 * @returns {Float32Array} Eight floats per vertex: x, y, u, v and the colour r, g, b, a.
 */
export function frameMesh(effect) {
  const { mesh, system, uvScale, width, height, sourceWidth, sourceHeight, incline } = effect;
  const size = system.main.size[0],
    lean = Math.tan((incline * Math.PI) / 180);
  const color = system.main.color.col;
  const rotation = system.main.rotation;
  const turn = euler(...rotation.map((v) => (v * 180) / Math.PI));
  const data = [];
  for (const i of mesh.i) {
    const point = applyDir(turn, ...mesh.v.slice(i * 3, i * 3 + 3));
    const px = point[0] * size * uvScale[0],
      py = point[1] * size * uvScale[1];
    // Stretch the straight spans like the frame's sliced UISprite, retaining its authored corner radii.
    const y = py + (Math.sign(py) * (height - sourceHeight)) / 2;
    const x = px + (Math.sign(px) * (width - sourceWidth)) / 2 + y * lean;
    data.push(x, y, mesh.uv[i * 2], mesh.uv[i * 2 + 1], ...color);
  }
  return new Float32Array(data);
}

/**
 * Turn the shader's linear light and coverage into the premultiplied sRGB layer the page composites over the
 * card.
 * The game's blend (One / OneMinusSrcAlpha in linear light) cannot be done on sRGB values directly. The layer
 * carries the light and, as its alpha, what the blend leaves of what is behind; the canvas draws nothing where
 * the mask is zero, so the card keeps its own pixels there.
 * @param {number[]} emission The shader's linear colour [r, g, b].
 * @returns {{color: number[], alpha: number}} The layer's sRGB colour and alpha.
 */
export function frameBlend(emission, alpha) {
  const clamp = (v) => Math.min(1, Math.max(0, v));
  return { color: emission.map((c) => toSrgb(clamp(c))), alpha: 1 - toSrgb(1 - clamp(alpha)) };
}
export const FRAME_OUTPUT = `
vec3 displayColor(vec3 c){return mix(12.92*c,1.055*pow(max(c,vec3(0.)),vec3(1./2.4))-.055,step(vec3(.0031308),c));}
void main(){frameMain();vec3 light=displayColor(clamp(o.rgb,vec3(0.),vec3(1.)));float cover=1.-displayColor(vec3(1.-clamp(o.a,0.,1.))).x;o=vec4(light,cover);}`;
/**
 * What the page makes of one channel: the premultiplied layer over what is behind it, in sRGB values.
 * @param {{color: number, alpha: number}} layer The layer's value for the channel, as frameBlend gives it.
 * @param {number} behind What is behind, 0 to 1.
 * @returns {number} The result, at most 1.
 */
export const frameOver = (layer, behind) => Math.min(1, layer.color + behind * (1 - layer.alpha));

const live = new Set();
const pending = new WeakMap();
let observer,
  request = null;
const reduced = globalThis.matchMedia?.("(prefers-reduced-motion: reduce)");
function refresh() {
  for (const item of live) {
    if (item.canvas.isConnected) item.connected = true;
    else if (item.connected) {
      item.dispose();
      live.delete(item);
    }
  }
  if (
    request === null &&
    !reduced?.matches &&
    [...live].some((item) => item.canvas.isConnected && !item.canvas.closest("[hidden]"))
  ) {
    request = requestAnimationFrame(tick);
  }
}
function observe(records) {
  for (const record of records)
    for (const node of record.addedNodes || []) {
      for (const canvas of [node, ...(node.querySelectorAll?.("canvas") || [])]) {
        const item = pending.get(canvas);
        if (item && canvas.isConnected) {
          pending.delete(canvas);
          item.connected = true;
          live.add(item);
        }
      }
    }
  refresh();
}
function tick(now) {
  request = null;
  for (const item of live) if (item.canvas.isConnected && !item.canvas.closest("[hidden]")) item.draw(now / 1000);
  refresh();
}
reduced?.addEventListener?.("change", () => {
  if (reduced.matches && request !== null) {
    cancelAnimationFrame(request);
    request = null;
  }
  refresh();
});

/**
 * Where renderFrameEffect puts its canvas.
 * @typedef {object} FramePlacement
 * @property {number} x The centre's x in stage pixels.
 * @property {number} y The centre's y in stage pixels.
 * @property {number} sx The horizontal scale of the node's frame in stage pixels per unit.
 * @property {number} sy The vertical scale.
 * @property {number} z The base z-index.
 * @property {(url: string) => Promise<HTMLImageElement>} load The image loader.
 */

/**
 * Draw the frame effect of a card into a canvas and keep it animated while it is on screen.
 * @param {object} effect The effect read from the prefab: mesh, material and particle system.
 * @param {FramePlacement} placement
 * @returns {Promise<void>} Settles once the textures are in and the canvas is in the host.
 */
export async function renderFrameEffect(effect, host, { x, y, sx, sy, z, load }) {
  const canvas = document.createElement("canvas");
  // Include the mesh's actual extents, rather than clipping its glow to the UISprite dimensions.
  const data = frameMesh(effect),
    points = Array.from({ length: data.length / 8 }, (_, i) => [data[i * 8], data[i * 8 + 1]]);
  const halfW = Math.max(...points.map((p) => Math.abs(p[0]))),
    halfH = Math.max(...points.map((p) => Math.abs(p[1])));
  const width = halfW * 2 * sx,
    height = halfH * 2 * sy;
  const density = Math.max(2, globalThis.devicePixelRatio || 1);
  canvas.width = Math.ceil(width * density);
  canvas.height = Math.ceil(height * density);
  Object.assign(canvas.style, {
    position: "absolute",
    left: `${x - width / 2}px`,
    top: `${y - height / 2}px`,
    width: `${width}px`,
    height: `${height}px`,
    zIndex: String(z + effect.depth),
    pointerEvents: "none",
  });
  const gl = canvas.getContext("webgl2", { alpha: true, premultipliedAlpha: true, antialias: true });
  if (!gl?.createShader) return; // The prefab's static frame and badge remain available without WebGL2.
  const shader = (type, source) => {
    const s = gl.createShader(type);
    gl.shaderSource(s, source);
    gl.compileShader(s);
    if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(s));
    return s;
  };
  const vs = shader(
    gl.VERTEX_SHADER,
    `#version 300 es
in vec2 a_pos; in vec2 a_uv; in vec4 a_color;
uniform vec2 u_half; out vec4 v_t0; out vec4 v_t1; out vec4 v_t2; out vec4 v_col; out vec3 v_n;
void main(){gl_Position=vec4(a_pos/u_half,0.,1.);v_t0=vec4(a_uv,0.,0.);v_t1=vec4(0.);v_t2=vec4(0.);v_col=a_color;v_n=vec3(0.,0.,1.);}`,
  );
  // Reuse the App's port of DSFX/FX_SHADER_AlphaBlend_Add_Mask_0. NGUI's panel clipping is the list viewport.
  const fs = shader(
    gl.FRAGMENT_SHADER,
    fragmentSource("mask").replace("void main()", "void frameMain()") + FRAME_OUTPUT,
  );
  const program = gl.createProgram();
  gl.attachShader(program, vs);
  gl.attachShader(program, fs);
  gl.linkProgram(program);
  gl.deleteShader(vs);
  gl.deleteShader(fs);
  if (!gl.getProgramParameter(program, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(program));
  gl.useProgram(program);
  const buffer = gl.createBuffer(),
    vao = gl.createVertexArray();
  gl.bindVertexArray(vao);
  gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
  gl.bufferData(gl.ARRAY_BUFFER, data, gl.STATIC_DRAW);
  for (const [name, count, offset] of [
    ["a_pos", 2, 0],
    ["a_uv", 2, 2],
    ["a_color", 4, 4],
  ]) {
    const at = gl.getAttribLocation(program, name);
    gl.enableVertexAttribArray(at);
    gl.vertexAttribPointer(at, count, gl.FLOAT, false, 32, offset * 4);
  }
  const loc = (name) => gl.getUniformLocation(program, name),
    material = effect.material;
  gl.uniform2f(loc("u_half"), halfW, halfH);
  for (const [name, value] of Object.entries(material.floats)) gl.uniform1f(loc(name), value);
  const color = material.colors._Color;
  gl.uniform4f(loc("_Color"), ...color.map((v, i) => (i < 3 ? toLinear(v) : v)));
  const textures = [];
  const dispose = () => {
    for (const tex of textures) gl.deleteTexture(tex);
    gl.deleteBuffer(buffer);
    gl.deleteVertexArray(vao);
    gl.deleteProgram(program);
  };
  try {
    await Promise.all(
      ["_Tex_Main", "_Tex_Mask"].map(async (name, unit) => {
        const meta = material.tex[name],
          image = meta.id
            ? await load(uiExportUrl(`/asset/ui/selection-frame/${encodeURIComponent(meta.id)}.png`))
            : null;
        const tex = gl.createTexture();
        textures.push(tex);
        gl.activeTexture(gl.TEXTURE0 + unit);
        gl.bindTexture(gl.TEXTURE_2D, tex);
        gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, true);
        gl.pixelStorei(gl.UNPACK_COLORSPACE_CONVERSION_WEBGL, gl.NONE);
        if (image)
          gl.texImage2D(gl.TEXTURE_2D, 0, meta.srgb ? gl.SRGB8_ALPHA8 : gl.RGBA8, gl.RGBA, gl.UNSIGNED_BYTE, image);
        else
          gl.texImage2D(
            gl.TEXTURE_2D,
            0,
            gl.RGBA8,
            1,
            1,
            0,
            gl.RGBA,
            gl.UNSIGNED_BYTE,
            new Uint8Array([255, 255, 255, 255]),
          );
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.REPEAT);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.REPEAT);
        gl.uniform1i(loc(name), unit);
        gl.uniform4f(loc(`${name}_ST`), ...(meta.st || [1, 1, 0, 0]));
      }),
    );
  } catch (error) {
    dispose();
    throw error;
  }
  host.appendChild(canvas);
  const item = {
    canvas,
    connected: false,
    draw(time) {
      gl.viewport(0, 0, canvas.width, canvas.height);
      gl.clearColor(0, 0, 0, 0);
      gl.clear(gl.COLOR_BUFFER_BIT);
      gl.uniform1f(loc("_TimeY"), time);
      gl.drawArrays(gl.TRIANGLES, 0, data.length / 8);
    },
    dispose,
  };
  item.draw(0);
  if (canvas.isConnected) {
    item.connected = true;
    live.add(item);
  } else pending.set(canvas, item);
  if (!observer && globalThis.MutationObserver) {
    observer = new MutationObserver(observe);
    observer.observe(document.documentElement, {
      childList: true,
      subtree: true,
      attributes: true,
      attributeFilter: ["hidden"],
    });
  }
  refresh();
}
