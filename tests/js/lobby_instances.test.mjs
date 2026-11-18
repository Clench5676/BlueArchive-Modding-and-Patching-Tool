// Tests that every memorial lobby instance (web/js/lobby/lobby.js createLobby) keeps its own state, and that the
// module's default instance offers exactly an instance's API.
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { test } from "node:test";
import { latestLoad } from "../../src/bmpt/web/js/spine/spine-load.js";
import { LOBBY_MODULES, joinedModules } from "./helpers/source-text.mjs";
const Lobby = await import("../../src/bmpt/web/js/lobby/lobby.js");

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

test("the default lobby exports the same names as an instance, and nothing an instance changes is shared", () => {
  const a = Lobby.createLobby(),
    b = Lobby.createLobby();
  const names = Object.keys(Lobby)
    .filter((name) => name !== "createLobby")
    .sort();
  assert.deepEqual(Object.keys(a).sort(), names);
  assert.deepEqual(Object.keys(b).sort(), names);
  for (const name of names)
    if (STATELESS.includes(name)) {
      assert.equal(a[name], Lobby[name], name);
      assert.equal(b[name], Lobby[name], name);
    } else {
      assert.equal(typeof a[name], "function", name);
      assert.notEqual(a[name], b[name], name);
      assert.notEqual(a[name], Lobby[name], name);
    }
  const seq = { cues: [{ clip: "one" }] };
  a.setLines({ one: { "zh-CN": "First", "en-US": "First (en)" } });
  assert.deepEqual(a.textOf(seq), ["First"]);
  assert.deepEqual(b.textOf(seq), [], "lines set on one instance stay its own");
  assert.deepEqual(Lobby.textOf(seq), []);
  b.setLines({ one: { "zh-CN": "Second", "en-US": "Second (en)" } }, (line) => line?.["en-US"] || "");
  assert.deepEqual(b.textOf(seq), ["Second (en)"]);
  assert.deepEqual(a.textOf(seq), ["First"], "the line picker stays per instance");
  for (const lobby of [a, b, Lobby]) {
    assert.equal(lobby.meta(), null);
    assert.equal(lobby.playing(), null);
    assert.equal(lobby.playingAll(), false);
  }
});

// lobby.js and the parts of its instances as one body, to read an instance's state S and functions L
const lobbyFile = (name) => readFile(new URL(`../../src/bmpt/web/js/lobby/${name}`, import.meta.url), "utf8");
const [lobbyMain, ...lobbyParts] = await Promise.all(LOBBY_MODULES.map(lobbyFile));
const source = joinedModules(lobbyMain, ...lobbyParts).replace(/^export /gm, "");
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
