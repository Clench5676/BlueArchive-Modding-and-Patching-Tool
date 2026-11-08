import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const source = readFileSync(new URL("../../src/bmpt/web/js/mod-compare.js", import.meta.url), "utf8");
const code = source.slice(source.indexOf("function previewPlayer("));
class El {
  children = []; listeners = {};
  appendChild(child) { this.children.push(child); }
  replaceChildren() { this.children = []; this.textContent = ""; }
  addEventListener(name, fn) { this.listeners[name] = fn; }
}
const flush = async () => { for (let i = 0; i < 10; i++) await Promise.resolve(); };

function harness(allowed = false) {
  const host = new El(), consentHost = {}, players = [], prompts = [];
  let decision = false, permission = allowed;
  const env = {
    skinPrimary: (button, caption) => { button.textContent = caption; },
    ensureSpine: async () => permission,
    confirmLicence: async (host, options) => { prompts.push([host, options]); permission = decision; return decision; },
    $: () => consentHost, T: (key) => key,
    document: { createElement: () => new El() },
    spine: {
      Downloader: class {}, TextureAtlas: class { pages = []; },
      SpinePlayer: class { constructor() { players.push(this); } dispose() { this.disposed = true; } },
    },
    loadPlayer: (create, config) => { const player = create(config); return { ready: Promise.resolve(player), dispose: () => player.dispose() }; },
  };
  const previewPlayer = new Function("env", `const { ${Object.keys(env).join(", ")} } = env; ${code}; return previewPlayer;`)(env);
  return { host, players, prompts, consentHost, confirm: (value) => { decision = value; },
    start: () => previewPlayer(host, { skeleton: "AA==", atlas: "", json: false }, "original", []) };
}

test("compare previews stay empty without constructing readers, players or licence actions", async () => {
  const h = harness(), handle = h.start(); await flush();
  assert.equal(h.host.children.length, 0);
  assert.ok(!h.host.textContent);
  assert.deepEqual(h.players, []);
  assert.deepEqual(h.prompts, []);
  handle.dispose();
});

test("closing compare before its licence gate settles prevents late players", async () => {
  const h = harness(true), handle = h.start();
  handle.dispose(); await flush();
  assert.deepEqual(h.players, []);
  assert.equal(h.host.children.length, 0);
});

// The compare callers retain their public frame fields while the shared geometry rounds their old width up to L.
import { compareFrame } from "../../src/bmpt/web/js/popup-frames.js";
import { CONTROL, POPUP_FRAME, SPACE } from "../../src/bmpt/web/js/ui-tokens.js";
test("compare and repair frames use L and keep their scrolling findings inside the content box", () => {
  for (const review of [false, true]) {
    const f = compareFrame(review);
    assert.equal(f.w, POPUP_FRAME.L.w);
    assert.ok(f.h >= POPUP_FRAME.minH && f.h <= POPUP_FRAME.maxH);
    assert.equal(f.scroll.width, f.w - 2 * SPACE.xxxl);
    assert.equal(f.scroll.top, f.cy + f.h / 2 - POPUP_FRAME.contentTop);
    assert.equal(f.bottom, f.scroll.top - f.scroll.height);
    if (review) assert.equal(f.bottom - (f.cy - f.h / 2 + POPUP_FRAME.footer.bottom + CONTROL.buttonL.h), SPACE.m);
    if (review) {
      assert.equal(f.h, 1180);
      assert.ok(f.rows.max * f.rows.pitch <= f.scroll.height);
    }
  }
});


test("comparison fits and centres zero, one or a capped set of rows; repair retains 1180", () => {
  const cap = Math.floor((1180 - POPUP_FRAME.contentTop - SPACE.xl) / SPACE.xxl);
  assert.equal(cap, 16);
  for (const count of [0, 1, cap, cap + 100]) {
    const f = compareFrame(false, count), used = Math.max(1, Math.min(count, cap)) * SPACE.xxl;
    assert.equal(f.rows.max, cap, "shrinking the frame never shrinks the row cap");
    assert.equal(f.h, Math.max(786, POPUP_FRAME.contentTop + used + SPACE.xl));
    assert.ok(Math.abs(f.rows.y + SPACE.xxl / 2 - used / 2
      - (f.cy + f.h / 2 - f.content.y - f.content.h / 2)) <= .5);
    assert.equal(compareFrame(true, count).h, 1180);
  }
  assert.match(source, /compareFrame\(!!review, changed.length\)/);
  assert.match(source, /node\("None", \[0, frame.rows.y\]/);
});
