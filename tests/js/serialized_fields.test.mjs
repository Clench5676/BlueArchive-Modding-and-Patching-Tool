import assert from "node:assert/strict";
import { test } from "node:test";
import { readSerialized } from "../../src/bmpt/web/js/core/serialized-fields.js";

test("an exported NGUI node and its widgets read into the page's node chibi", () => {
  const node = readSerialized({
    name: "Root",
    active: true,
    localPosition: [10, -4],
    scale: [1, 1],
    localRotationZDegrees: 90,
    widgets: [
      {
        className: "UISprite",
        spriteName: "Common_Btn",
        width: 200,
        height: 80,
        fillAmount: 0.5,
        fillDirection: 2,
        inclineAngle: 10,
      },
      { className: "UILabel", width: 300, height: 40, effectStyle: 1, alignment: 2, text: "A" },
    ],
    children: [{ name: "Child", localPosition: [0, 0], widgets: [] }],
  });
  assert.deepEqual(node.pos, [10, -4]);
  assert.equal(node.rotZ, 90);
  assert.ok(!("localPosition" in node));
  assert.deepEqual(node.widgets[0], {
    cls: "UISprite",
    sprite: "Common_Btn",
    w_: 200,
    h_: 80,
    fill: 0.5,
    fillDir: 2,
    incline: 10,
  });
  assert.deepEqual(node.widgets[1], { cls: "UILabel", w_: 300, h_: 40, effect: 1, align: 2, text: "A" });
  assert.deepEqual(node.children[0].pos, [0, 0]);
});

test("a memorial lobby's clips and hit boxes read their full serialized names; other fields keep theirs", () => {
  const doc = readSerialized({
    clips: {
      idle: {
        track: 0,
        loop: true,
        finishMode: 2,
        introMix: 0.2,
        outroMix: 0.3,
        outroStartOffset: null,
        blockInteractionOnPlay: true,
      },
    },
    touch: [{ rect: [0, 0, 10, 10], rotation: 15, kind: "pat" }],
    fx: { main: { rotation: [0, 0, 1] } },
  });
  assert.deepEqual(doc.clips.idle, {
    track: 0,
    loop: true,
    finish: 2,
    intro: 0.2,
    outro: 0.3,
    outroAt: null,
    block: true,
  });
  assert.deepEqual(doc.touch[0], { rect: [0, 0, 10, 10], rot: 15, kind: "pat" });
  assert.deepEqual(doc.fx.main.rotation, [0, 0, 1], "a particle's rotation is not a hit box's");
});

test("a document already in the page's chibi passes through unchanged", () => {
  const tree = { name: "A", pos: [1, 2], widgets: [{ cls: "UISprite", sprite: "S", w_: 1, h_: 2 }] };
  assert.deepEqual(readSerialized(tree), tree);
});
