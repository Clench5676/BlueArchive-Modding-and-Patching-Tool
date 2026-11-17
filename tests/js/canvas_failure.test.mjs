import assert from "node:assert/strict";
import { test } from "node:test";
import {
  canvasFailed,
  context2d,
  encodedPicture,
  hasAlpha,
  REPORT_EVERY,
} from "../../src/bmpt/web/js/core/canvas-failure.js";
import { maskWithSprite } from "../../src/bmpt/web/js/ui/sprite-mask.js";
import { maskCanvas } from "./sprite_mask_fixture.mjs";

const style = (x, y, w, h) => ({ left: `${x}px`, top: `${y}px`, width: `${w}px`, height: `${h}px`, transform: "" });

test("an empty canvas encoding is no picture", () => {
  assert.equal(encodedPicture("data:,"), false);
  assert.equal(encodedPicture("data:image/png;base64,"), false);
  assert.equal(encodedPicture(""), false);
  assert.equal(encodedPicture(null), false);
  assert.equal(encodedPicture("data:image/png;base64,iVBORw0KGgo="), true);
  assert.equal(hasAlpha(new Uint8ClampedArray(16)), false);
  assert.equal(hasAlpha(Uint8ClampedArray.from([0, 0, 0, 0, 9, 9, 9, 1])), true);
  assert.throws(() => context2d({ width: 4, height: 4, getContext: () => null }), /2D canvas unavailable/);
});

test("a failing step reaches the session log at most once per interval", () => {
  const sent = [],
    report = (...args) => sent.push(args),
    step = `test-step-${Math.random()}`;
  assert.equal(canvasFailed(new Error("a"), "u", step, { now: 1000, report }), true);
  assert.equal(canvasFailed(new Error("b"), "u", step, { now: 1000 + REPORT_EVERY - 1, report }), false);
  assert.equal(canvasFailed(new Error("c"), "u", step, { now: 1000 + REPORT_EVERY, report }), true);
  assert.deepEqual(
    sent.map(([error, , at]) => [error.message, at]),
    [
      ["a", step],
      ["c", step],
    ],
  );
});

// A canvas without a backing store: it draws nothing, reads back transparent and encodes to "data:,".
function starvedCanvas() {
  const fake = maskCanvas(),
    create = fake.document.createElement;
  let starved = true;
  fake.document.createElement = () => {
    const canvas = create(),
      live = canvas.getContext;
    const born = starved;
    canvas.getContext = () => {
      const context = live();
      if (born)
        context.getImageData = () => ({
          width: canvas.width,
          height: canvas.height,
          data: new Uint8ClampedArray(canvas.width * canvas.height * 4),
        });
      return context;
    };
    if (born) canvas.toDataURL = () => "data:,";
    return canvas;
  };
  return {
    document: fake.document,
    recover: () => {
      starved = false;
    },
  };
}

test("a mask drawn without canvas memory is not kept: the portrait shows, and the next render masks it", async () => {
  const previous = globalThis.document,
    starved = starvedCanvas();
  globalThis.document = starved.document;
  try {
    const reports = [],
      report = (error, url, step) => reports.push([error.message, url, step]);
    const card = () => ({
      source: { style: { ...style(0, 0, 280.13, 317.84), backgroundImage: 'url("starved-frame")' } },
      target: { style: { ...style(10, 20, 262.1, 295.7), backgroundImage: 'url("portrait")' } },
    });
    const load = async () => ({ naturalWidth: 560, naturalHeight: 636 });
    const first = card();
    assert.equal(await maskWithSprite(first.source, first.target, load, { surface: true, report }), false);
    assert.equal(first.target.style.maskImage, undefined, "no empty mask hides the portrait");
    assert.equal(first.target.style.backgroundImage, 'url("portrait")');
    assert.deepEqual(reports, [["Mask sprite drew no pixels", "starved-frame", "ui-mask"]]);
    starved.recover();
    const next = card();
    assert.equal(await maskWithSprite(next.source, next.target, load, { surface: true, report }), true);
    assert.match(next.target.style.maskImage, /^url\("data:image\/png;base64,/);
  } finally {
    globalThis.document = previous;
  }
});

test("a mask whose encoding fails is not kept either", async () => {
  const previous = globalThis.document,
    fake = maskCanvas();
  globalThis.document = fake.document;
  try {
    let broken = true;
    const create = fake.document.createElement;
    fake.document.createElement = () => {
      const c = create();
      const encode = c.toDataURL;
      c.toDataURL = () => (broken ? "data:," : encode());
      return c;
    };
    const reports = [];
    const run = async () => {
      const source = { style: { ...style(0, 0, 100, 80), backgroundImage: 'url("encode-frame")' } };
      const target = { style: { ...style(4, 4, 90, 70), backgroundImage: 'url("portrait")' } };
      const masked = await maskWithSprite(source, target, async () => ({ naturalWidth: 200, naturalHeight: 160 }), {
        report: (e) => reports.push(e.message),
      });
      return [masked, target.style.maskImage];
    };
    assert.deepEqual(await run(), [false, undefined]);
    assert.match(reports[0], /Mask encoding failed \(data:,\)/);
    broken = false;
    const [masked, image] = await run();
    assert.equal(masked, true);
    assert.match(image, /^url\("data:image\/png;base64,synthetic-/);
  } finally {
    globalThis.document = previous;
  }
});
