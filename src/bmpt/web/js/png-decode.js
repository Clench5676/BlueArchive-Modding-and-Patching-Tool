// The 3D model's textures as the game stores them. Browsers decode images premultiplied by their alpha, and WebKit
// keeps them so: where a texture's alpha is 0 its colour is lost (black). The game's textures use alpha as data (a
// dress's sparkle mask, a mask's layer index) beside colours that must survive, so the viewer decodes PNGs itself and
// hands WebGL straight RGBA (tests/js/png_decode.test.mjs).

const SIGNATURE = [137, 80, 78, 71, 13, 10, 26, 10];
const CHANNELS = { 0: 1, 2: 3, 3: 1, 4: 2, 6: 4 };

async function inflate(bytes) {
  const stream = new Blob([bytes]).stream().pipeThrough(new DecompressionStream("deflate"));
  return new Uint8Array(await new Response(stream).arrayBuffer());
}

function paeth(a, b, c) {
  const p = a + b - c, pa = Math.abs(p - a), pb = Math.abs(p - b), pc = Math.abs(p - c);
  return pa <= pb && pa <= pc ? a : pb <= pc ? b : c;
}

// An 8-bit, non-interlaced PNG (grey, RGB, palette, grey + alpha, RGBA) as { width, height, data: RGBA bytes }.
export async function decodePng(buffer) {
  const bytes = new Uint8Array(buffer);
  if (SIGNATURE.some((b, i) => bytes[i] !== b)) throw new Error("Not a PNG");
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  let width = 0, height = 0, depth = 0, type = 0, interlace = 0, palette = null, transparency = null;
  const parts = [];
  for (let at = 8; at + 8 <= bytes.length;) {
    const length = view.getUint32(at), name = String.fromCharCode(...bytes.subarray(at + 4, at + 8));
    const body = bytes.subarray(at + 8, at + 8 + length);
    if (name === "IHDR") {
      width = view.getUint32(at + 8); height = view.getUint32(at + 12);
      depth = body[8]; type = body[9]; interlace = body[12];
    } else if (name === "PLTE") palette = body;
    else if (name === "tRNS") transparency = body;
    else if (name === "IDAT") parts.push(body);
    else if (name === "IEND") break;
    at += 12 + length;
  }
  const channels = CHANNELS[type];
  if (depth !== 8 || !channels || interlace !== 0 || !width || !height || (type === 3 && !palette)) {
    throw new Error(`Unsupported PNG (depth ${depth}, type ${type}, interlace ${interlace})`);
  }
  const packed = new Uint8Array(parts.reduce((n, p) => n + p.length, 0));
  parts.reduce((offset, p) => (packed.set(p, offset), offset + p.length), 0);
  const raw = await inflate(packed);
  const stride = width * channels;
  if (raw.length < height * (stride + 1)) throw new Error("Truncated PNG");
  const rows = new Uint8Array(height * stride);
  for (let y = 0; y < height; y++) {
    const filter = raw[y * (stride + 1)], line = raw.subarray(y * (stride + 1) + 1, (y + 1) * (stride + 1));
    const out = y * stride, up = out - stride;
    for (let x = 0; x < stride; x++) {
      const a = x >= channels ? rows[out + x - channels] : 0, b = y ? rows[up + x] : 0;
      const c = x >= channels && y ? rows[up + x - channels] : 0;
      const v = line[x];
      rows[out + x] = filter === 0 ? v : filter === 1 ? v + a : filter === 2 ? v + b
        : filter === 3 ? v + ((a + b) >> 1) : filter === 4 ? v + paeth(a, b, c) : NaN;
      if (filter > 4) throw new Error(`Unknown PNG filter ${filter}`);
    }
  }
  const data = new Uint8ClampedArray(width * height * 4);
  for (let i = 0, j = 0; i < width * height; i++, j += channels) {
    const o = i * 4;
    if (type === 6) { data[o] = rows[j]; data[o + 1] = rows[j + 1]; data[o + 2] = rows[j + 2]; data[o + 3] = rows[j + 3]; }
    else if (type === 2) { data[o] = rows[j]; data[o + 1] = rows[j + 1]; data[o + 2] = rows[j + 2]; data[o + 3] = 255; }
    else if (type === 0) { data[o] = data[o + 1] = data[o + 2] = rows[j]; data[o + 3] = 255; }
    else if (type === 4) { data[o] = data[o + 1] = data[o + 2] = rows[j]; data[o + 3] = rows[j + 1]; }
    else {
      const k = rows[j];
      data[o] = palette[k * 3]; data[o + 1] = palette[k * 3 + 1]; data[o + 2] = palette[k * 3 + 2];
      data[o + 3] = transparency && k < transparency.length ? transparency[k] : 255;
    }
  }
  return { width, height, data };
}

// A GLTFLoader texture loader that reads PNGs straight (an ImageBitmap where a texture is something else).
export const straightTextureLoader = {
  isImageBitmapLoader: true,
  load(url, onLoad, _onProgress, onError) {
    fetch(url).then((r) => r.arrayBuffer()).then(async (buffer) => {
      try {
        const { width, height, data } = await decodePng(buffer);
        return new ImageData(data, width, height);
      } catch {
        return createImageBitmap(new Blob([buffer]), { premultiplyAlpha: "none", colorSpaceConversion: "none" });
      }
    }).then(onLoad, onError);
  },
};
