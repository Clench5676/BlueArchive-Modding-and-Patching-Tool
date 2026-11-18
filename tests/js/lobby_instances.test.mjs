// Tests that every memorial lobby instance (web/js/memorial-lobby/memorial-lobby.js createMemorialLobby) keeps its own
// state, and that the module's default instance offers exactly an instance's API.
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { test } from "node:test";
import { latestLoad } from "../../src/bmpt/web/js/spine/spine-load.js";
import { MEMORIAL_LOBBY_MODULES, joinedModules } from "./helpers/source-text.mjs";
const MemorialLobby = await import("../../src/bmpt/web/js/memorial-lobby/memorial-lobby.js");

const STATELESS = [
  "layersOf",
  "pairAtlases",
  "idleClips",
  "sceneBox",
  "around",
  "clipPoses",
  "shrinkRect",
  "framingLimits",
  "FRAMING_CELL",
  "framingFits",
  "framingView",
  "cameraParams",
  "layerViewport",
  "ZOOM_MODES",
  "zoomBounds",
];

test("the default memorial lobby exports the same names as an instance, and nothing an instance changes is shared", () => {
  const a = MemorialLobby.createMemorialLobby(),
    b = MemorialLobby.createMemorialLobby();
  const names = Object.keys(MemorialLobby)
    .filter((name) => name !== "createMemorialLobby")
    .sort();
  assert.deepEqual(Object.keys(a).sort(), names);
  assert.deepEqual(Object.keys(b).sort(), names);
  for (const name of names)
    if (STATELESS.includes(name)) {
      assert.equal(a[name], MemorialLobby[name], name);
      assert.equal(b[name], MemorialLobby[name], name);
    } else {
      assert.equal(typeof a[name], "function", name);
      assert.notEqual(a[name], b[name], name);
      assert.notEqual(a[name], MemorialLobby[name], name);
    }
  const seq = { cues: [{ clip: "one" }] };
  a.setLines({ one: { "zh-CN": "First", "en-US": "First (en)" } });
  assert.deepEqual(a.textOf(seq), ["First"]);
  assert.deepEqual(b.textOf(seq), [], "lines set on one instance stay its own");
  assert.deepEqual(MemorialLobby.textOf(seq), []);
  b.setLines({ one: { "zh-CN": "Second", "en-US": "Second (en)" } }, (line) => line?.["en-US"] || "");
  assert.deepEqual(b.textOf(seq), ["Second (en)"]);
  assert.deepEqual(a.textOf(seq), ["First"], "the line picker stays per instance");
  for (const memorialLobby of [a, b, MemorialLobby]) {
    assert.equal(memorialLobby.meta(), null);
    assert.equal(memorialLobby.playing(), null);
    assert.equal(memorialLobby.playingAll(), false);
  }
});

// memorial-lobby.js and the parts of its instances as one body, to read an instance's state S and functions L
const memorialLobbyFile = (name) =>
  readFile(new URL(`../../src/bmpt/web/js/memorial-lobby/${name}`, import.meta.url), "utf8");
const [memorialLobbyMain, ...memorialLobbyParts] = await Promise.all(MEMORIAL_LOBBY_MODULES.map(memorialLobbyFile));
const source = joinedModules(memorialLobbyMain, ...memorialLobbyParts).replace(/^export /gm, "");
const createContext = new Function("latestLoad", `${source}; return createContext;`)(latestLoad);

test("two instances hold their own settings, dialogue state, timers and caches", () => {
  const a = createContext(),
    b = createContext();
  assert.notEqual(a.S, b.S);
  for (const [key, value] of Object.entries(a.S))
    if (value && typeof value === "object") assert.notEqual(value, b.S[key], `${key} is not shared`);
  a.L.setZoomLimit({ mode: "custom", custom: 3 });
  a.L.setIntro(false);
  a.L.setVolume(0.25, true);
  a.L.setClock(0);
  a.L.setDialogueFullscreen(true);
  assert.deepEqual(
    [a.S.zoomMode, a.S.zoomCustom, a.S.introOn, a.S.volume, a.S.muted, a.S.fixedDt],
    ["custom", 3, false, 0.25, true, 0],
  );
  assert.deepEqual(
    [b.S.zoomMode, b.S.zoomCustom, b.S.introOn, b.S.volume, b.S.muted, b.S.fixedDt],
    ["fullArt", 2, true, 0.8, false, null],
  );
  assert.equal(a.S.dialogueFullscreen, true);
  assert.equal(b.S.dialogueFullscreen, null);
  const fired = [];
  a.L.setTimeout(() => fired.push("a"), 60000);
  assert.equal(a.S.sceneTimers.size, 1);
  assert.equal(b.S.sceneTimers.size, 0, "a scene timer belongs to the instance that set it");
  b.L.clearTimers();
  assert.equal(a.S.sceneTimers.size, 1, "clearing one instance's timers leaves the other's");
  a.L.clearTimers();
  assert.equal(a.S.sceneTimers.size, 0);
  a.S.paintedRects.set("key", { any: new Uint8Array(1) });
  assert.ok(a.S.paintedRects.get("key"));
  assert.equal(b.S.paintedRects.get("key"), null, "measured geometry is cached per instance");
  assert.notEqual(a.S.touchToken, b.S.touchToken);
  assert.deepEqual(fired, []);
});
