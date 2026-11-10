// The picture view of the repair review: zoom at a point, pan, fill, always inside the picture.
import assert from "node:assert/strict";
import { test } from "node:test";
import { MAX_ZOOM, REST, clamp, cover, panBy, style, zoomAt } from "../../src/bmpt/web/js/pan-zoom.js";

const near = (a, b) => assert.ok(Math.abs(a - b) < 1e-9, `${a} is not ${b}`);

test("zooming keeps the point under the pointer on the same spot of the picture", () => {
  for (const [px, py] of [[0.5, 0.5], [0.2, 0.9], [1, 0]]) {
    const before = { zoom: 2, x: -0.4, y: -0.3 }, after = zoomAt(before, 1.5, px, py);
    near(after.zoom, 3);
    // the picture's own coordinate under the pointer, before and after
    near((px - before.x) / before.zoom, (px - after.x) / after.zoom);
    near((py - before.y) / before.zoom, (py - after.y) / after.zoom);
  }
});

test("the frame never shows anything outside the picture", () => {
  assert.deepEqual(zoomAt(REST, 0.5), { zoom: 1, x: 0, y: 0 });  // not smaller than the frame
  assert.equal(zoomAt(REST, 1000).zoom, MAX_ZOOM);
  assert.deepEqual(panBy(REST, 0.4, -0.4), { zoom: 1, x: 0, y: 0 });  // nothing to move at rest
  assert.deepEqual(panBy({ zoom: 2, x: -0.5, y: -0.5 }, 5, -5), { zoom: 2, x: 0, y: -1 });
  assert.deepEqual(clamp({ zoom: NaN, x: Infinity, y: undefined }), { zoom: 1, x: 0, y: 0 });
  // zooming out from a corner pulls the picture back over the frame
  const out = zoomAt({ zoom: 4, x: -3, y: -3 }, 0.5, 0, 0);
  assert.ok(out.x >= 1 - out.zoom && out.y >= 1 - out.zoom && out.x <= 0 && out.y <= 0);
});

test("a contained picture fills its frame, centred", () => {
  // a 4:3 picture drawn whole in a 3:1 frame is as high as the frame: it takes 2.25 times to be as wide
  assert.deepEqual(cover(3, 4 / 3), { zoom: 2.25, x: -0.625, y: -0.625 });
  assert.deepEqual(cover(0.5, 2), { zoom: 4, x: -1.5, y: -1.5 });
  assert.deepEqual(cover(2, 2), { zoom: 1, x: 0, y: 0 });
  assert.equal(cover(100, 0.01).zoom, MAX_ZOOM);
  for (const bad of [[0, 1], [1, 0], [NaN, 1], [1, -2]]) assert.deepEqual(cover(...bad), { zoom: 1, x: 0, y: 0 });
});

test("the view is the picture's box in frame sizes", () => {
  assert.deepEqual(style({ zoom: 2.25, x: -0.625, y: -0.5 }), { width: "225%", height: "225%", left: "-62.5%", top: "-50%" });
});
