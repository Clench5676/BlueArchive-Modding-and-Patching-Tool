import assert from "node:assert/strict";
import { test } from "node:test";
import { readFile } from "node:fs/promises";
import { dialogueText } from "../../src/bmpt/web/js/dialogue-text.js";
import { spriteBorders, sliceAxis, slicePoint } from "../../src/bmpt/web/js/sprite-slices.js";

class Element {
  constructor() { this.style = {}; this.children = []; this.attrs = {}; }
  setAttribute(key, value) { this.attrs[key] = value; }
  replaceChildren(...children) { this.children.forEach((child) => { child.parent = null; }); this.children = children; children.forEach((child) => { child.parent = this; }); }
  remove() { if (this.parent) this.parent.children = this.parent.children.filter((child) => child !== this); this.parent = null; }
}
globalThis.document = { createElement: () => new Element() };
let timers = new Map(), timerDelays = new Map(), nextTimer = 0, revoked = [];
const fireTimer = (delay) => {
  const id = [...timers.keys()].find((key) => timerDelays.get(key) === delay);
  assert.ok(id, `Expected ${delay} ms timer`);
  const callback = timers.get(id); timers.delete(id); timerDelays.delete(id); callback();
};
const pendingTimers = () => [...timers.keys()].filter((id) => timerDelays.get(id) !== 25000);
globalThis.hallDialogueHarness = {
  dialogueText, spriteBorders, sliceAxis, slicePoint, NGUI: {}, defaultRpc: () => {}, language: () => "zh-TW", S: 1920 / 2960, talkExpression: () => {},
  setTimeout: (callback, delay) => { timers.set(++nextTimer, callback); timerDelays.set(nextTimer, delay); return nextTimer; },
  clearTimeout: (id) => { timers.delete(id); timerDelays.delete(id); },
  URL: { createObjectURL: () => `blob:voice-${nextTimer}`, revokeObjectURL: (url) => revoked.push(url) },
};
let source = await readFile(new URL("../../src/bmpt/web/js/student-hall-dialogue.js", import.meta.url), "utf8");
source = source.replace(/^import .*;$/gm, "");
source = `const {NGUI, defaultRpc, language, S, talkExpression, setTimeout, clearTimeout, URL, spriteBorders, sliceAxis, slicePoint, dialogueText} = globalThis.hallDialogueHarness;\n${source}`;
const { createDialogue, memoryMemo, prepareBalloon, balloonPosition, balloonBody, wrapDialogue, lobbyPoint, lobbyBalloon, clampBalloon, createLobbyDialogue } = await import(`data:text/javascript;base64,${Buffer.from(source).toString("base64")}`);
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; };
const flush = async () => { for (let i = 0; i < 20; i++) await Promise.resolve(); };
const template = () => ({ name: "Talk", pos: [0, 0], scale: [1, 1], widgets: [], children: [
  { name: "Sprite", pos: [0, 0], widgets: [{ cls: "UISprite", sprite: "Lobby_balloon", atlas: "Common", type: 1,
    flip: 0, w_: 228, h_: 201, pivot: 0, depth: 7, color: "#ffffff/1.00" }], children: [] },
  { name: "Label", pos: [79, -45], widgets: [{ cls: "UILabel", text: "text\ntext", fontSize: 52, w_: 90, h_: 112,
    pivot: 0, depth: 22, color: "#3e444a/1.00", align: 1, spacingX: -2 }], children: [] },
] });
const find = (tree, path) => path.split("/").reduce((node, key) => node.children.find((child) => child.name === key), tree);
const entry = (id = "one", variant = "hoshino") => ({ id, variant, x: 600, y: 1120, scale: 1 });
function fixture(extra = {}) {
  timers = new Map(); timerDelays = new Map(); revoked = [];
  const host = new Element(), calls = [], players = [], talking = [], rendered = [], errors = [], diagnostics = [];
  const settings = { voiceVolume: 35, voiceMuted: false }, signal = new AbortController();
  const ui = { find, init: async () => {}, glyphWidth: (text, size) => [...text].length * size,
    render: async (tree, target, opts) => { rendered.push({ tree, target, opts }); if (extra.render) await extra.render(); } };
  const call = async (method, params = {}, options = {}) => {
    calls.push({ method, params, signal: options.signal });
    if (extra.call) { const result = extra.call(method, params, options); if (result !== undefined) return result; }
    if (method === "hall.dialogueLayout") return template();
    if (method === "hall.lines") return { lines: { hoshino_lobby_1: { "zh-TW": "\u6700\u8fd1\u5728\u70ba\u4e86\u63d0\u5347\u7bc0\u594f\u800c\u7df4\u7fd2\u3002", voice: true } } };
    if (method === "hall.voice") return { audio: btoa("original audio"), mime: "audio/ogg" };
    assert.fail(method);
  };
  const controller = createDialogue({ host, ui, call, signal: signal.signal, prefs: () => settings,
    actorOf: (value) => ({ actor: value.id, rect: { x: value.x - 150, y: 120, width: 300, height: 800 } }),
    expressions: (actor, on, expression) => talking.push([actor, on, expression]), onError: (error) => errors.push(error),
    onDiagnostic: (error) => diagnostics.push(error),
    audioFactory: (url) => {
      const player = { url, volume: 0, played: 0, paused: 0, pause() { this.paused++; },
        async play() { this.played++; if (extra.play) return extra.play(); } };
      players.push(player); return player;
    }, currentLanguage: () => extra.language?.() || "zh-TW" });
  return { controller, host, calls, players, talking, rendered, errors, diagnostics, settings, signal };
}

test("actual Lobby balloon keeps native font, sliced sprite and spacing while wrapping all text", () => {
  const ui = { find, glyphWidth: (text, size) => [...text].length * size };
  const saved = template(), text = "\u6700\u8fd1\u5728\u70ba\u4e86\u63d0\u5347\u7bc0\u594f\u800c\u7df4\u7fd2\u3002";
  const result = prepareBalloon(saved, text, { x: 500, y: 100, width: 300, height: 800 }, { ui, lang: "zh-TW" });
  const label = find(result.tree, "Label").widgets[0], sprite = find(result.tree, "Sprite").widgets[0];
  assert.equal(sprite.sprite, "Lobby_balloon"); assert.equal(sprite.atlas, "Common"); assert.equal(sprite.type, 1);
  assert.equal(label.fontSize, 52); assert.equal(label.color, "#3e444a/1.00"); assert.equal(label.spacingX, -2);
  assert.equal(label.text.replaceAll("\n", ""), text); assert.equal(label.text.split("\n").length, 2);
  const body = balloonBody(sprite), pos = find(result.tree, "Label").pos;
  assert.equal(label.pivot, 4); assert.equal(label.align, 2);
  assert.equal(pos[0], (body.left + body.right) / 2);
  assert.equal(-pos[1], (body.top + body.bottom) / 2);
  assert.ok(-pos[1] < sprite.h_ / 2, "The body excludes the larger bottom shadow");
  assert.deepEqual(saved, template());
});

test("edge placement flips the native tail and keeps full balloon on stage", () => {
  const position = balloonPosition({ x: 1650, y: 900, width: 200, height: 700 }, { width: 500, height: 250 });
  assert.equal(position.flip, true); assert.ok(position.x >= 24 && position.x + 500 <= 1896);
  assert.ok(position.y >= 36 && position.y + 250 <= 1044);
  const lines = wrapDialogue("AReallyLongUnbrokenWordAndMoreLetters", 52, 200, (text, fs) => text.length * fs);
  assert.equal(lines.join(""), "AReallyLongUnbrokenWordAndMoreLetters");
});

test("repeated clicks are ignored during audio and the next finished turn reuses the memory cache", async () => {
  const h = fixture();
  await h.controller.play(entry()); assert.equal(h.players[0].volume, .35); assert.equal(h.host.children.length, 1);
  await h.controller.play(entry());
  assert.equal(h.players.length, 1); assert.equal(h.players[0].paused, 0); assert.equal(h.rendered.length, 1);
  h.players[0].onended(); await h.controller.play(entry());
  assert.equal(h.players[1].played, 1);
  assert.equal(h.calls.filter((call) => call.method === "hall.lines").length, 1);
  assert.equal(h.calls.filter((call) => call.method === "hall.voice").length, 1);
  assert.equal(h.calls.filter((call) => call.method === "hall.dialogueLayout").length, 1);
  assert.equal(h.rendered[0].tree.children[1].widgets[0].text, h.rendered[1].tree.children[1].widgets[0].text);
  h.controller.dispose(); assert.equal(h.host.children.length, 0); assert.ok(revoked.length >= 2);
});

test("duplicate actors keep independent dialogue turns", async () => {
  const h = fixture({ call: (method) => method === "hall.lines" ? { lines: {
    hoshino_lobby_1: { "zh-TW": "First", voice: false }, hoshino_lobby_2: { "zh-TW": "Second", voice: false },
  } } : undefined });
  await h.controller.play(entry("one")); fireTimer(5000);
  await h.controller.play(entry("one")); fireTimer(5000); await h.controller.play(entry("two"));
  assert.deepEqual(h.rendered.map(({ tree }) => find(tree, "Label").widgets[0].text), ["First", "Second", "First"]);
  h.controller.dispose();
});

test("the first pending text request owns the turn and ignored clicks never fetch or advance", async () => {
  const first = deferred();
  const h = fixture({ call: (method, params) => method === "hall.lines" && params.variant === "old" ? first.promise : undefined });
  const old = h.controller.play(entry("old", "old")); await flush();
  const signal = h.calls[0].signal;
  await h.controller.play(entry("new")); assert.equal(signal.aborted, false);
  assert.equal(h.calls.length, 1);
  first.resolve({ lines: { old_lobby_1: { "zh-TW": "First", voice: true }, old_lobby_2: { "zh-TW": "Second", voice: true } } }); await old;
  assert.equal(h.players.length, 1); assert.equal(h.host.children[0].attrs["aria-label"], "First");
  await h.controller.play(entry("old", "old")); assert.equal(h.players.length, 1);
  h.players[0].onended(); await h.controller.play(entry("old", "old"));
  assert.equal(h.host.children[0].attrs["aria-label"], "Second"); h.controller.dispose();
});

test("page exit aborts pending voice fetch and cannot start late audio", async () => {
  const voice = deferred(); const h = fixture({ call: (method) => method === "hall.voice" ? voice.promise : undefined });
  const pending = h.controller.play(entry()); await flush();
  const request = h.calls.find((call) => call.method === "hall.voice"); assert.ok(request);
  h.signal.abort(); assert.equal(request.signal.aborted, true);
  voice.resolve({ audio: btoa("late"), mime: "audio/ogg" }); await pending;
  assert.equal(h.players.length, 0); assert.equal(h.host.children.length, 0);
});

test("a late audio play promise cannot restart the old actor expression", async () => {
  const playing = deferred(); const h = fixture({ play: () => playing.promise });
  const pending = h.controller.play(entry()); await flush(); await flush();
  assert.equal(h.players.length, 1); h.controller.stop();
  playing.resolve(); await pending;
  assert.equal(h.talking.some(([, on]) => on), false);
  assert.ok(h.players[0].paused); assert.equal(h.host.children.length, 0); h.controller.dispose();
});

test("cancelled staged rendering cannot append an old balloon", async () => {
  const drawing = deferred(); const h = fixture({ render: () => drawing.promise });
  const pending = h.controller.play(entry()); await flush(); h.controller.stop(); drawing.resolve(); await pending;
  assert.equal(h.host.children.length, 0); assert.equal(h.players.length, 0); h.controller.dispose();
});

test("voice failure keeps readable text and ends its expression without an error popup", async () => {
  const h = fixture({ call: (method) => method === "hall.voice" ? Promise.reject(new Error("offline")) : undefined });
  await h.controller.play(entry()); assert.equal(h.host.children.length, 1); assert.equal(h.errors.length, 0);
  assert.equal(h.talking.at(-1)[1], false); assert.equal(pendingTimers().length, 1);
  fireTimer(Math.min(...[...timers.keys()].map((id) => timerDelays.get(id)))); assert.equal(h.host.children.length, 0); h.controller.dispose();
});

test("mute blocks audio and muting an active stream leaves a bounded text-only lifetime", async () => {
  const h = fixture(); h.settings.voiceMuted = true;
  await h.controller.play(entry()); assert.equal(h.players.length, 0);
  assert.equal(h.calls.some((call) => call.method === "hall.voice"), false);
  h.settings.voiceMuted = false; fireTimer(5000); await h.controller.play(entry());
  h.settings.voiceMuted = true; h.controller.preferencesChanged();
  assert.ok(h.players[0].paused); assert.equal(pendingTimers().length, 1); assert.equal(h.host.children.length, 1);
  fireTimer(Math.min(...[...timers.keys()].map((id) => timerDelays.get(id)))); assert.equal(h.host.children.length, 0); h.controller.dispose();
});

test("failed balloon metadata is retried on the next click", async () => {
  let reads = 0;
  const h = fixture({ call: (method) => method === "hall.dialogueLayout" && ++reads === 1 ? Promise.reject(new Error("temporary")) : undefined });
  await h.controller.play(entry()); assert.equal(h.errors.length, 1);
  await h.controller.play(entry()); assert.equal(h.host.children.length, 1); assert.equal(reads, 2); h.controller.dispose();
});

test("memory cache coalesces in-flight requests and enforces byte and entry budgets", async () => {
  const memo = memoryMemo(10, 2), read = deferred(); let count = 0;
  const one = memo.get("one", () => { count++; return read.promise; }, (value) => value.length);
  const two = memo.get("one", () => { count++; return read.promise; }, (value) => value.length);
  read.resolve("1234"); assert.equal(await one, "1234"); assert.equal(await two, "1234"); assert.equal(count, 1);
  await memo.get("two", () => "1234", (value) => value.length); await memo.get("three", () => "1234", (value) => value.length);
  assert.equal(memo.size(), 8);
  await memo.get("one", () => { count++; return "1234"; }, (value) => value.length); assert.equal(count, 2);
  memo.clear(); assert.equal(memo.size(), 0);
});

test("an aborted same-key flight cannot poison its replacement cache entry", async () => {
  const memo = memoryMemo(), old = deferred(), first = new AbortController(), second = new AbortController();
  const pending = memo.get("student", () => old.promise, undefined, first.signal); await flush(); first.abort();
  assert.equal(await memo.get("student", () => "new", undefined, second.signal), "new");
  old.resolve("old"); await pending;
  assert.equal(await memo.get("student", () => assert.fail("Cached replacement should be reused")), "new");
  memo.clear();
});

test("a pending first click retries automatically and eventually displays and plays its first clip", async () => {
  let reads = 0;
  const h = fixture({ call: (method) => method === "hall.lines" ? ++reads === 1 ? { lines: {}, pending: true } : {
    lines: { hoshino_lobby_1: { "zh-TW": "First", voice: true }, hoshino_lobby_2: { "zh-TW": "Second", voice: true } }, pending: false,
  } : undefined });
  const pending = h.controller.play(entry()); await flush();
  assert.equal(h.host.children.length, 0); assert.equal(pendingTimers().length, 1);
  fireTimer(Math.min(...[...timers.keys()].map((id) => timerDelays.get(id)))); await pending;
  assert.equal(reads, 2); assert.equal(h.host.children[0].attrs["aria-label"], "First"); assert.equal(h.players.length, 1);
  const requests = h.calls.filter((call) => call.method === "hall.lines");
  assert.equal(requests[0].signal, requests[1].signal);
  h.players[0].onended(); await h.controller.play(entry());
  assert.equal(h.host.children[0].attrs["aria-label"], "Second");
  assert.deepEqual(h.calls.filter((call) => call.method === "hall.voice").map((call) => call.params.clip), ["hoshino_lobby_1", "hoshino_lobby_2"]);
  h.controller.dispose();
});

test("pending voice metadata preserves immediate text and attaches voice to the same clip without another turn", async () => {
  let reads = 0;
  const h = fixture({ call: (method) => method === "hall.lines" ? { lines: {
    hoshino_lobby_1: { "zh-TW": "First", voice: ++reads > 1 }, hoshino_lobby_2: { "zh-TW": "Second", voice: reads > 1 },
  }, pending: reads === 1 } : undefined });
  const pending = h.controller.play(entry()); await flush(); await flush();
  assert.equal(h.host.children[0].attrs["aria-label"], "First"); assert.equal(h.players.length, 0);
  assert.equal(pendingTimers().length, 1); fireTimer(Math.min(...[...timers.keys()].map((id) => timerDelays.get(id)))); await pending;
  assert.equal(h.players.length, 1); assert.equal(h.rendered.length, 1, "Voice-only metadata changes keep the displayed native balloon");
  assert.equal(h.calls.find((call) => call.method === "hall.voice").params.clip, "hoshino_lobby_1");
  h.players[0].onended(); await h.controller.play(entry()); assert.equal(h.host.children[0].attrs["aria-label"], "Second"); h.controller.dispose();
});

test("cancelling a pending metadata retry clears its delay and issues no follow-up request", async () => {
  const h = fixture({ call: (method) => method === "hall.lines" ? { lines: {}, pending: true } : undefined });
  const pending = h.controller.play(entry()); await flush(); assert.equal(pendingTimers().length, 1);
  const request = h.calls[0]; h.controller.stop(); await pending;
  assert.equal(request.signal.aborted, true); assert.equal(timers.size, 0);
  assert.equal(h.calls.filter((call) => call.method === "hall.lines").length, 1);
  assert.equal(h.host.children.length, 0); assert.equal(h.players.length, 0); h.controller.dispose();
});

test("pending retries are bounded and a later metadata failure keeps the existing text", async () => {
  let reads = 0;
  const h = fixture({ call: (method) => method === "hall.lines" ? ++reads === 1
    ? { lines: { hoshino_lobby_1: { "zh-TW": "Readable", voice: false } }, pending: true }
    : Promise.reject(new Error("metadata unavailable")) : undefined });
  const pending = h.controller.play(entry()); await flush(); await flush();
  fireTimer(Math.min(...[...timers.keys()].map((id) => timerDelays.get(id)))); await pending;
  assert.equal(h.host.children[0].attrs["aria-label"], "Readable"); assert.equal(h.errors.length, 0); h.controller.dispose();
  const forever = fixture({ call: (method) => method === "hall.lines" ? { lines: {}, pending: true } : undefined });
  const waiting = forever.controller.play(entry());
  for (let i = 0; i < 4; i++) { await flush(); assert.equal(pendingTimers().length, 1); fireTimer(Math.min(...[...timers.keys()].map((id) => timerDelays.get(id)))); }
  await waiting;
  assert.equal(forever.calls.filter((call) => call.method === "hall.lines").length, 5);
  assert.equal(timers.size, 0); assert.equal(forever.errors.length, 0); forever.controller.dispose();
});

test("a pending voice download blocks all portraits without interrupting the current balloon", async () => {
  const voice = deferred();
  const h = fixture({ call: (method) => method === "hall.voice" ? voice.promise : undefined });
  const playing = h.controller.play(entry()); await flush();
  const firstBubble = h.host.children[0], request = h.calls.find(({ method }) => method === "hall.voice");
  await h.controller.play(entry("two", "other"));
  assert.equal(request.signal.aborted, false); assert.equal(h.host.children[0], firstBubble);
  assert.equal(h.calls.filter(({ method }) => method === "hall.lines").length, 1);
  voice.resolve({ audio: btoa("voice"), mime: "audio/ogg" }); await playing;
  await h.controller.play(entry("two", "other")); assert.equal(h.players.length, 1);
  assert.equal(h.players[0].paused, 0); assert.equal(timers.size, 0, "Started audio owns its entire natural duration");
  h.players[0].onended(); await h.controller.play(entry("two", "other"));
  assert.equal(h.players.length, 2); assert.equal(h.calls.at(-1).params.variant, "other"); h.controller.dispose();
});

test("autoplay rejection keeps one bounded caption and then accepts another portrait", async () => {
  const h = fixture({ play: () => Promise.reject(new Error("NotAllowedError")) });
  await h.controller.play(entry());
  assert.equal(h.players[0].paused, 1); assert.equal(h.host.children.length, 1); assert.equal(h.errors.length, 0);
  await h.controller.play(entry("two")); assert.equal(h.players.length, 1); assert.equal(h.rendered.length, 1);
  fireTimer(5000); await h.controller.play(entry("two"));
  assert.equal(h.players.length, 2); assert.equal(h.rendered.length, 2); h.controller.dispose();
});

test("an audio error cannot allow a second bubble until its text fallback expires", async () => {
  const h = fixture(); await h.controller.play(entry());
  const failed = h.players[0].onerror, ended = h.players[0].onended;
  failed(); assert.equal(h.host.children.length, 1); assert.equal(h.players[0].paused, 1);
  await h.controller.play(entry("two")); assert.equal(h.players.length, 1);
  ended(); assert.equal(h.host.children.length, 1, "A queued old end event cannot clear the fallback");
  fireTimer(5000); await h.controller.play(entry("two"));
  failed(); assert.equal(h.players[1].paused, 0, "A queued old error cannot cancel the next speaker"); h.controller.dispose();
});

test("stalled setup expires with no caption and a late completion cannot steal the next turn", async () => {
  const stalled = deferred();
  const h = fixture({ call: (method, params) => method === "hall.lines" && params.variant === "stalled" ? stalled.promise : undefined });
  const pending = h.controller.play(entry("one", "stalled")); await flush();
  fireTimer(25000); assert.equal(h.errors.length, 0); assert.equal(h.diagnostics[0].code, "lines_unavailable"); assert.equal(h.calls[0].signal.aborted, true);
  await h.controller.play(entry("two"));
  stalled.resolve({ lines: { stalled_lobby_1: { "zh-TW": "Late", voice: true } } }); await pending;
  assert.equal(h.players.length, 1); assert.equal(h.rendered.length, 1); h.controller.dispose();
});

test("stalled voice or autoplay setup preserves the obtained caption for exactly one fallback period", async () => {
  for (const stage of ["voice", "play"]) {
    const stalled = deferred();
    const h = fixture(stage === "voice" ? { call: (method) => method === "hall.voice" ? stalled.promise : undefined }
      : { play: () => stalled.promise });
    const pending = h.controller.play(entry()); await flush();
    fireTimer(25000); assert.equal(h.host.children.length, 1); assert.equal(h.errors.length, 0);
    await h.controller.play(entry("two")); assert.equal(h.rendered.length, 1);
    fireTimer(5000); assert.equal(h.host.children.length, 0);
    stalled.resolve({ audio: btoa("late"), mime: "audio/ogg" }); await pending;
    assert.equal(h.talking.some(([, on]) => on), false); assert.equal(h.host.children.length, 0);
    h.controller.dispose();
  }
});

test("zero volume during voice download cancels late sound and retains one bounded readable caption", async () => {
  const voice = deferred();
  const h = fixture({ call: (method) => method === "hall.voice" ? voice.promise : undefined });
  const pending = h.controller.play(entry()); await flush();
  h.settings.voiceVolume = 0; h.controller.preferencesChanged();
  await h.controller.play(entry("two")); assert.equal(h.rendered.length, 1);
  voice.resolve({ audio: btoa("late"), mime: "audio/ogg" }); await pending;
  assert.equal(h.players.length, 0); assert.equal(h.host.children.length, 1);
  fireTimer(5000); h.settings.voiceVolume = 50; await h.controller.play(entry("two"));
  assert.equal(h.players[0].volume, .5); h.controller.dispose();
});

test("native body geometry mirrors around the tail while all line boxes remain vertically centred", () => {
  const ui = { find, glyphWidth: (text, size) => [...text].length * size, fontSizeFor: (_, size) => size };
  for (const text of ["One", "First\nSecond", "First\nSecond\nThird\nFourth"]) {
    const normal = prepareBalloon(template(), text, { x: 500, y: 100, width: 100, height: 800 }, { ui });
    const flipped = prepareBalloon(template(), text, { x: 1650, y: 100, width: 200, height: 800 }, { ui });
    const sprite = find(normal.tree, "Sprite").widgets[0], a = find(normal.tree, "Label"), b = find(flipped.tree, "Label");
    const body = balloonBody(sprite);
    assert.equal(flipped.flip, true); assert.equal(a.pos[1], b.pos[1]);
    assert.ok(Math.abs(a.pos[0] + b.pos[0] - sprite.w_) < 1e-6);
    assert.equal(-a.pos[1], (body.top + body.bottom) / 2);
    assert.ok(-a.pos[1] - a.widgets[0].h_ / 2 > body.top);
    assert.ok(-a.pos[1] + a.widgets[0].h_ / 2 < body.bottom);
    assert.equal(a.widgets[0].text, text);
  }
});

test("disposing a text-only fallback also aborts its in-flight translation request", async () => {
  let language = "zh-TW"; const translation = deferred();
  const h = fixture({ language: () => language, call: (method, params) => method === "hall.lines"
    ? params.lang === "en" ? translation.promise : { lines: { hoshino_lobby_1: { "zh-TW": "Original", voice: false } } }
    : undefined });
  await h.controller.play(entry()); language = "en-US";
  const relabel = h.controller.relabel(); await flush();
  const request = h.calls.at(-1); assert.equal(request.params.lang, "en"); assert.equal(request.signal.aborted, false);
  h.controller.dispose(); assert.equal(request.signal.aborted, true);
  translation.resolve({ lines: { hoshino_lobby_1: { en: "Late translation", voice: false } } }); await relabel;
  assert.equal(h.host.children.length, 0); assert.equal(timers.size, 0);
});

test("ordinary dialogue absence and missing catalog mappings are silent, throttled and release the global turn", async () => {
  for (const response of [{ lines: {}, unavailable: true }, { lines: { missing_lobby_1: { voice: false } } },
    Object.assign(new Error("missing"), { code: "lines_unavailable" }),
    Object.assign(new Error("missing"), { code: "catalog_variant_not_found" })]) {
    const h = fixture({ call: (method, params) => method === "hall.lines" && params.variant === "missing"
      ? response instanceof Error ? Promise.reject(response) : response : undefined });
    await h.controller.play(entry("one", "missing"));
    await h.controller.play(entry("one", "missing"));
    assert.equal(h.calls.length, 1); assert.equal(h.errors.length, 0); assert.equal(h.host.children.length, 0);
    assert.equal(timers.size, 0); await h.controller.play(entry("two")); assert.equal(h.players.length, 1);
    h.controller.dispose();
  }
});

test("unexpected dialogue failures retain their diagnostic error path", async () => {
  const fault = new TypeError("broken metadata adapter");
  const h = fixture({ call: (method) => method === "hall.lines" ? Promise.reject(fault) : undefined });
  await h.controller.play(entry()); assert.equal(h.errors[0], fault); assert.equal(timers.size, 0); h.controller.dispose();
});

test("late exact expression metadata enriches the active voice without another bubble or dialogue turn", async () => {
  let reads = 0;
  const h = fixture({ language: () => "zh-CN", call: (method) => method === "hall.lines" ? {
    lines: { hoshino_lobby_1: { "zh-CN": "Original caption", voice: true, ...(++reads > 1 ? {
      expression: "02", expressionCues: [{ time: 0, expression: "02" }, { time: 2.5, expression: "14" }],
    } : {}) }, hoshino_lobby_2: { "zh-CN": "Second", voice: true, expression: "10" } },
    pending: reads === 1, animationPending: reads === 1,
  } : undefined });
  await h.controller.play(entry()); const player = h.players[0], bubble = h.host.children[0];
  assert.equal(player.played, 1); assert.equal(h.talking.some(([, on]) => on), false);
  player.currentTime = 3; fireTimer(500); await flush();
  assert.deepEqual(h.talking.at(-1), ["one", true, "14"]);
  assert.equal(h.host.children[0], bubble); assert.equal(h.rendered.length, 1); assert.equal(player.played, 1);
  await h.controller.play(entry("two")); assert.equal(h.players.length, 1); assert.equal(timers.size, 0);
  player.onended(); await h.controller.play(entry());
  assert.equal(h.host.children[0].attrs["aria-label"], "Second"); h.controller.dispose();
});

test("authored expression cues follow audio time, and late metadata cannot touch a finished voice", async () => {
  const h = fixture({ call: (method) => method === "hall.lines" ? { lines: { hoshino_lobby_1: {
    "zh-TW": "Caption", voice: true, expression: "02", expressionCues: [{ time: 0, expression: "02" }, { time: 2.5, expression: "04" }],
  } } } : undefined });
  await h.controller.play(entry()); const player = h.players[0];
  assert.deepEqual(h.talking.at(-1), ["one", true, "02"]);
  player.currentTime = 2.4; player.ontimeupdate(); assert.equal(h.talking.length, 1);
  player.currentTime = 2.5; player.ontimeupdate(); assert.deepEqual(h.talking.at(-1), ["one", true, "04"]);
  player.currentTime = 1; player.onseeked(); assert.deepEqual(h.talking.at(-1), ["one", true, "02"]);
  player.onended(); assert.equal(h.talking.at(-1)[1], false); assert.equal(player.ontimeupdate, null); h.controller.dispose();
  const late = deferred(); let reads = 0;
  const stopped = fixture({ call: (method) => method === "hall.lines" ? ++reads === 1 ? {
    lines: { hoshino_lobby_1: { "zh-TW": "Caption", voice: true } }, pending: true, animationPending: true,
  } : late.promise : undefined });
  await stopped.controller.play(entry()); fireTimer(500); await flush(); stopped.players[0].onended();
  late.resolve({ lines: { hoshino_lobby_1: { "zh-TW": "Late", voice: true, expression: "14" } }, animationPending: false }); await flush();
  assert.equal(stopped.talking.some(([, on]) => on), false); assert.equal(stopped.host.children.length, 0); stopped.controller.dispose();
});

test("Chinese captions prefer the requested script and never select Japanese or English", () => {
  const row = {ja:"Original Japanese", en:"English", "zh-TW":"Traditional", "zh-CN":"Simplified"};
  assert.deepEqual(dialogueText(row,"zh-CN"),{text:"Simplified",language:"zh-CN"});
  assert.deepEqual(dialogueText(row,"zh-TW"),{text:"Traditional",language:"zh-TW"});
  assert.deepEqual(dialogueText({ja:"Original",en:"English","zh-TW":"Traditional"},"zh-CN"),{text:"Traditional",language:"zh-TW"});
  assert.equal(dialogueText({ja:"Original",en:"English","zh-CN":"  "},"zh-CN"),null);
  assert.equal(dialogueText({ja:"Original",en:"English"},"zh-TW"),null);
});

test("late Chinese metadata updates the current balloon without replaying or advancing its voice",async()=>{
  let reads=0;
  const h=fixture({language:()=>"zh-CN",call:method=>method==="hall.lines"?{
    lines:{hoshino_lobby_1:{ja:"Original",en:"English","zh-TW":"Traditional",voice:true,
      ...(++reads>1?{"zh-CN":"Simplified"}:{})},hoshino_lobby_2:{"zh-CN":"Second",voice:true}},
    pending:reads===1,animationPending:false,
  }:undefined});
  await h.controller.play(entry());assert.equal(h.host.children[0].attrs["aria-label"],"Traditional");
  fireTimer(500);await flush();
  assert.equal(h.host.children[0].attrs["aria-label"],"Simplified");assert.equal(h.players.length,1);assert.equal(h.players[0].played,1);
  assert.equal(h.calls.filter(value=>value.method==="hall.voice").length,1);
  h.players[0].onended();await h.controller.play(entry());assert.equal(h.host.children[0].attrs["aria-label"],"Second");h.controller.dispose();
});

test("switching into Chinese immediately clears a foreign balloon while the requested translation is loading",async()=>{
  let locale="ja-JP";const translated=deferred();
  const h=fixture({language:()=>locale,call:(method,params)=>method==="hall.lines"?
    params.lang==="zh-CN"?translated.promise:{lines:{hoshino_lobby_1:{ja:"Original",voice:true}}}:undefined});
  await h.controller.play(entry());assert.equal(h.host.children[0].attrs["aria-label"],"Original");
  locale="zh-CN";const update=h.controller.relabel();await flush();assert.equal(h.host.children.length,0);
  translated.resolve({lines:{hoshino_lobby_1:{"zh-CN":"Translated",voice:true}}});await update;
  assert.equal(h.host.children[0].attrs["aria-label"],"Translated");assert.equal(h.players[0].played,1);h.controller.dispose();
});

test("a late render from the old language cannot overwrite a newer Chinese balloon",async()=>{
  let locale="ja-JP",draws=0;const delayed=deferred();
  const h=fixture({language:()=>locale,render:()=>++draws===1?delayed.promise:undefined,
    call:(method,params)=>method==="hall.lines"?{lines:{hoshino_lobby_1:{[params.lang]:params.lang==="ja"?"Original":"Translated",voice:true}}}:undefined});
  const playing=h.controller.play(entry());await flush();locale="zh-CN";await h.controller.relabel();
  assert.equal(h.host.children[0].attrs["aria-label"],"Translated");
  delayed.resolve();await playing;assert.equal(h.host.children[0].attrs["aria-label"],"Translated");h.controller.dispose();
});

test("scene zoom moves an existing balloon with its actor without rerendering or interrupting its vocal",async()=>{
  const h=fixture(),value=entry();await h.controller.play(value);
  const before=parseFloat(h.host.children[0].style.left);value.x+=100;h.controller.reposition();
  assert.ok(parseFloat(h.host.children[0].style.left)>before);assert.equal(h.rendered.length,1);
  assert.equal(h.players[0].played,1);assert.equal(h.players[0].paused,0);h.controller.dispose();assert.equal(timers.size,0);
});

const lobbyGeometry = (extra = {}) => ({ info: { dialog: { Talk: [-320, 503] } },
  meta: { touch: { x: -769, y: 361 }, sparse: false }, view: { x: -1480, y: -832.5, w: 2960, h: 1665 },
  bounds: { left: 0, top: 0, right: 1000, bottom: 1080 }, ...extra });
const balloonUi = { find, glyphWidth: (text, size) => [...text].length * size / 2 };
test("memorial Talk maps its top-left prefab point through the same view as the pat point", () => {
  const g = lobbyGeometry(), at = lobbyPoint({ x: -320, y: 503 }, g.view);
  const placed = lobbyBalloon(template(), "Hello", g, { ui: balloonUi });
  assert.equal(placed.x, (1160 / 2960) * 1920); assert.ok(Math.abs(placed.y - (329.5 / 1665) * 1080) < 1e-9);
  assert.equal(placed.x, at.x); assert.equal(placed.y, at.y); assert.equal(placed.flip, false);
  const zoom = { x: -1100, y: -300, w: 1400, h: 787.5 };
  const moved = lobbyBalloon(template(), "Hello", lobbyGeometry({ view: zoom }), { ui: balloonUi });
  const point = lobbyPoint({ x: -320, y: 503 }, zoom);
  assert.equal(moved.x, Math.min(point.x, 1000 - moved.width)); assert.equal(moved.y, 0);
});
test("missing or nonfinite Talk, special framing and sparse art all use the pat point and flip inside the visible view", () => {
  for (const extra of [{ info: {} }, { info: { dialog: { Talk: [NaN, 503] } } }, { framing: "character" },
    { meta: { touch: { x: -769, y: 361 }, sparse: true } }]) {
    const g = lobbyGeometry(extra), placed = lobbyBalloon(template(), "Hello", g, { ui: balloonUi });
    const point = lobbyPoint(g.meta.touch, g.view);
    assert.deepEqual({ x: placed.x, y: placed.y, flip: placed.flip },
      balloonPosition({ ...point, width: 0, height: 0 }, placed, g.bounds));
  }
  const g = lobbyGeometry({ info: {}, meta: { touch: { x: 0, y: -700 } } });
  const result = lobbyBalloon(template(), "Hello", g, { ui: balloonUi });
  assert.equal(result.flip, true); assert.ok(result.x + result.width <= g.bounds.right);
  assert.ok(result.y + result.height <= g.bounds.bottom);
});
test("fullscreen keeps the Talk balloon by the head, bounded by the whole stage instead of the left view", () => {
  const g = lobbyGeometry(), line = "A line long enough to reach past the detail panel's edge";
  const normal = lobbyBalloon(template(), line, g, { ui: balloonUi });
  const wide = lobbyBalloon(template(), line, { ...g, full: true }, { ui: balloonUi });
  assert.equal(find(wide.tree, "Sprite").widgets[0].sprite, find(normal.tree, "Sprite").widgets[0].sprite);
  const at = lobbyPoint({ x: g.info.dialog.Talk[0], y: g.info.dialog.Talk[1] }, g.view);
  assert.deepEqual({ x: wide.x, y: wide.y, flip: wide.flip }, clampBalloon(at, wide, { left: 0, top: 0, right: 1920, bottom: 1080 }));
  assert.ok(normal.x + normal.width <= g.bounds.right && wide.x + wide.width > g.bounds.right, "only the left view held it in");
  const panned = lobbyBalloon(template(), line, { ...g, full: true, view: { ...g.view, x: g.view.x - 100 } }, { ui: balloonUi });
  assert.notEqual(panned.x, wide.x, "the balloon follows the art when the fullscreen view pans");
});
test("balloon subtitle transitions preserve the 260 ms sequence and immediate language replacement cancels it", async () => {
  timers = new Map(); timerDelays = new Map();
  const host = new Element(), classes = new Set(); host.hidden = true;
  host.classList = { add: (...keys) => keys.forEach((key) => classes.add(key)), remove: (...keys) => keys.forEach((key) => classes.delete(key)) };
  const h = createLobbyDialogue({ host, geometry: () => lobbyGeometry(), ui: { ...balloonUi, render: async () => {} },
    call: async () => template(), raf: (fn) => fn(), later: hallDialogueHarness.setTimeout, cancel: hallDialogueHarness.clearTimeout });
  h.show("First"); await flush(); assert.equal(host.attrs["aria-label"], "First");
  assert.equal(host.attrs.role, "status"); assert.equal(host.style.pointerEvents, "none");
  h.show("Next"); assert.equal(classes.has("out"), true); assert.equal(host.attrs["aria-label"], "First");
  fireTimer(260); await flush(); assert.equal(host.attrs["aria-label"], "Next");
  h.show("Stale"); h.show("Translation", true, "th"); await flush();
  assert.equal(host.attrs["aria-label"], "Translation"); assert.equal(classes.size, 0); assert.equal(timers.size, 0);
  h.show("", true); assert.equal(host.hidden, true); h.dispose();
});
test("the Hall and memorial lobby use one prepareBalloon and CSS contributes no subtitle look", async () => {
  const lobby = await readFile(new URL("../../src/bmpt/web/js/lobby.js", import.meta.url), "utf8");
  const css = await readFile(new URL("../../src/bmpt/web/css/app.css", import.meta.url), "utf8");
  assert.match(lobby, /import\("\.\/student-hall-dialogue.js"\)/);
  assert.equal((source.match(/function prepareBalloon\(/g) || []).length, 1);
  assert.equal((source.match(/prepareBalloon\(template|prepareBalloon\(saved/g) || []).length, 3);
  for (const rule of css.matchAll(/[^{}]*\.lobby-subtitle[^{}]*\{([^}]*)\}/g))
    assert.doesNotMatch(rule[1], /background|border-radius|color|font|padding|bottom:|left:/);
});

test("camera motion repositions an existing Talk drawing, flips when needed and fullscreen keeps it", async () => {
  const host = new Element(), classes = new Set(), frames = [], draws = [], reads = [];
  host.hidden = true; host.classList = { add: (...keys) => keys.forEach((key) => classes.add(key)), remove: (...keys) => keys.forEach((key) => classes.delete(key)) };
  let g = lobbyGeometry({ info: {} });
  const h = createLobbyDialogue({ host, geometry: () => g,
    ui: { ...balloonUi, render: async (tree) => draws.push(tree) },
    call: async (method, params) => { reads.push([method, params]); return template(); },
    raf: (fn) => frames.push(fn), later: hallDialogueHarness.setTimeout, cancel: hallDialogueHarness.clearTimeout });
  h.show("Hello"); await flush(); assert.equal(draws.length, 1); assert.equal(classes.has("in"), true);
  const first = host.children[0], x = parseFloat(first.style.left);
  g = { ...g, view: { ...g.view, x: g.view.x - 100 } }; h.reposition(); await flush();
  assert.equal(host.children[0], first); assert.notEqual(parseFloat(first.style.left), x); assert.equal(draws.length, 1);
  g = { ...g, meta: { touch: { x: -10, y: 361 } } }; h.reposition(); await flush();
  assert.equal(draws.length, 2); assert.equal(find(draws.at(-1), "Sprite").widgets[0].flip, 1);
  while (frames.length) frames.shift()(); assert.equal(classes.has("in"), false, "a camera redraw must not strand the entry in opacity zero");
  g = { ...g, full: true }; h.reposition(); await flush();
  assert.deepEqual(reads, [["hall.dialogueLayout", {}]], "fullscreen keeps the same Talk template");
  const shown = host.children[0], left = parseFloat(shown.style.left);
  g = { ...g, view: { ...g.view, x: g.view.x + 50 } }; h.reposition(); await flush();
  assert.notEqual(parseFloat(host.children[0].style.left), left, "the balloon follows the art while it pans"); h.dispose();
});
test("Talk remains inside the visible left area at minimum/default/large windows and 1x/2x/3x densities", () => {
  const g = lobbyGeometry({ info: {}, meta: { touch: { x: -10, y: -700 } } });
  const p = lobbyBalloon(template(), "Hello", g, { ui: balloonUi });
  for (const [w, h] of [[960, 540], [1600, 900], [2560, 1440]]) for (const dpr of [1, 2, 3]) {
    const factor = Math.min(w / 1920, h / 1080) * dpr;
    assert.ok(p.x * factor >= g.bounds.left * factor);
    assert.ok((p.x + p.width) * factor <= g.bounds.right * factor);
    assert.ok(p.y * factor >= g.bounds.top * factor);
    assert.ok((p.y + p.height) * factor <= g.bounds.bottom * factor);
  }
});
