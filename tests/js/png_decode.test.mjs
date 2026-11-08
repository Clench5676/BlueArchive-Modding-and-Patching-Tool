// Tests for web/js/png-decode.js: the 3D viewer's own PNG decoding keeps colours where alpha is 0 (WebKit loses them).
import assert from "node:assert/strict";
import { deflateSync } from "node:zlib";
import { test } from "node:test";

import { decodePng } from "../../src/bmpt/web/js/png-decode.js";

const crcTable = Array.from({ length: 256 }, (_, n) => {
  let c = n;
  for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
  return c >>> 0;
});
const crc = (bytes) => {
  let c = 0xffffffff;
  for (const b of bytes) c = crcTable[(c ^ b) & 0xff] ^ (c >>> 8);
  return (c ^ 0xffffffff) >>> 0;
};
function chunk(name, body) {
  const out = Buffer.alloc(12 + body.length);
  out.writeUInt32BE(body.length, 0);
  out.write(name, 4, "latin1");
  Buffer.from(body).copy(out, 8);
  out.writeUInt32BE(crc(out.subarray(4, 8 + body.length)), 8 + body.length);
  return out;
}
// rows: one filter byte then raw (unfiltered) samples, filtered here as PNG encoders do
function png(width, height, type, channels, samples, filters, extra = []) {
  const stride = width * channels, lines = [];
  for (let y = 0; y < height; y++) {
    const row = samples.slice(y * stride, (y + 1) * stride), prev = y ? samples.slice((y - 1) * stride, y * stride) : null;
    const f = filters[y % filters.length], line = [f];
    for (let x = 0; x < stride; x++) {
      const a = x >= channels ? row[x - channels] : 0, b = prev ? prev[x] : 0, c = x >= channels && prev ? prev[x - channels] : 0;
      const p = a + b - c, pa = Math.abs(p - a), pb = Math.abs(p - b), pc = Math.abs(p - c);
      const pred = [0, a, b, (a + b) >> 1, pa <= pb && pa <= pc ? a : pb <= pc ? b : c][f];
      line.push((row[x] - pred) & 0xff);
    }
    lines.push(...line);
  }
  const header = Buffer.alloc(13);
  header.writeUInt32BE(width, 0); header.writeUInt32BE(height, 4);
  header[8] = 8; header[9] = type;
  return Buffer.concat([Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]), chunk("IHDR", header), ...extra,
    chunk("IDAT", deflateSync(Buffer.from(lines))), chunk("IEND", Buffer.alloc(0))]);
}

test("RGBA keeps the colour of pixels whose alpha is 0, through every filter", async () => {
  const w = 3, h = 5, samples = [];
  for (let i = 0; i < w * h; i++) samples.push((i * 37) & 255, (i * 91) & 255, (200 - i * 7) & 255, i % 2 ? 0 : 128);
  const { width, height, data } = await decodePng(png(w, h, 6, 4, samples, [0, 1, 2, 3, 4]));
  assert.equal(width, w);
  assert.equal(height, h);
  assert.deepEqual([...data], samples);
});

test("RGB, grey, grey with alpha and palette images come back as RGBA", async () => {
  const rgb = await decodePng(png(2, 2, 2, 3, [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12], [1, 4]));
  assert.deepEqual([...rgb.data], [1, 2, 3, 255, 4, 5, 6, 255, 7, 8, 9, 255, 10, 11, 12, 255]);
  const grey = await decodePng(png(2, 1, 0, 1, [9, 200], [2]));
  assert.deepEqual([...grey.data], [9, 9, 9, 255, 200, 200, 200, 255]);
  const greyAlpha = await decodePng(png(2, 1, 4, 2, [9, 0, 200, 77], [3]));
  assert.deepEqual([...greyAlpha.data], [9, 9, 9, 0, 200, 200, 200, 77]);
  const indexed = await decodePng(png(2, 1, 3, 1, [1, 0], [0],
    [chunk("PLTE", [10, 20, 30, 40, 50, 60]), chunk("tRNS", [255, 0])]));
  assert.deepEqual([...indexed.data], [40, 50, 60, 0, 10, 20, 30, 255]);
});

test("what it cannot read is refused, so the loader falls back to the browser", async () => {
  await assert.rejects(decodePng(new Uint8Array([1, 2, 3])), /Not a PNG/);
  const sixteen = png(1, 1, 6, 4, [1, 2, 3, 4], [0]);
  sixteen[24] = 16;  // IHDR bit depth
  await assert.rejects(decodePng(sixteen), /Unsupported PNG/);
});
