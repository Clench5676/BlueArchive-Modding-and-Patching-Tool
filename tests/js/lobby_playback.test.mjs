import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const source = readFileSync(
  new URL("../../src/bmpt/web/js/memorial-lobby/memorial-lobby-talk.js", import.meta.url),
  "utf8",
);
const dialogue = source.slice(source.indexOf("function play(anim"), source.indexOf("const playingAll"));
const linesSetter = source.slice(source.indexOf("function setLines("), source.indexOf("function setVolume("));
const detail = readFileSync(new URL("../../src/bmpt/web/js/detail/detail-strip.js", import.meta.url), "utf8");
const toggle = detail.slice(
  detail.indexOf("function togglePlayAll()"),
  detail.indexOf("export function renderExpressions()"),
);

function player(cues = []) {
  const starts = [],
    subtitles = [],
    voices = [];
  const current = {
    meta: { sequences: [1, 2, 3].map((n) => ({ anim: `Talk_0${n}_M`, cues, duration: 1 })) },
    intro: { skip() {} },
  };
  const env = {
    S: {
      current,
      Idle: { interrupt() {} },
      talk: null,
      chain: null,
      talkIndex: 0,
      cueTimers: [],
      lines: {},
      muted: false,
      audios: [],
      volume: 0.8,
      lineOf: (line) => line?.["en-US"] || "",
    },
    L: {
      mainPlayer: () => ({ animationState: {} }),
      returnToIdle() {},
      playClip: (_player, anim) => starts.push(anim),
      clearClip() {},
      setTimeout: (fn, ms) => setTimeout(fn, ms),
      clearTimeout: (timer) => clearTimeout(timer),
    },
    emit() {},
    silence() {},
    subtitle: (text, immediate) => subtitles.push({ text, immediate }),
    Audio: class {
      constructor(url) {
        voices.push(url);
      }
      play() {
        return Promise.resolve();
      }
    },
  };
  const controls = new Function(
    ...Object.keys(env),
    `
    ${dialogue}
    ${linesSetter}
    return { play, playAll, stop, setLines, playingAll: () => !!S.chain, selected: () => S.talk?.seq.anim || null };
  `,
  )(...Object.values(env));
  const clickPlay = (selected = controls.selected()) =>
    new Function("MemorialLobby", "memorialLobbySeq", `${toggle}; togglePlayAll();`)(controls, selected);
  return { starts, subtitles, voices, controls, clickPlay };
}

test("detail Play starts at the selected dialogue and continues through subsequent lines", (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const h = player();
  try {
    h.controls.play("Talk_02_M");
    h.starts.length = 0;
    h.clickPlay();
    assert.deepEqual(h.starts, ["Talk_02_M"]);
    assert.equal(h.controls.playingAll(), true);
    t.mock.timers.tick(1300);
    t.mock.timers.tick(200);
    assert.deepEqual(h.starts, ["Talk_02_M", "Talk_03_M"]);
    t.mock.timers.tick(1300);
    assert.equal(h.controls.playingAll(), false);
    assert.equal(h.controls.selected(), null);
  } finally {
    h.controls.stop();
    t.mock.timers.reset();
  }
});

test("Play without a selection starts at the beginning, and the running button stops playback", (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const h = player();
  try {
    h.clickPlay();
    assert.deepEqual(h.starts, ["Talk_01_M"]);
    h.clickPlay();
    assert.equal(h.controls.playingAll(), false);
    assert.equal(h.controls.selected(), null);
    t.mock.timers.tick(10000);
    assert.deepEqual(h.starts, ["Talk_01_M"]);
    h.clickPlay("missing");
    assert.equal(h.starts.at(-1), "Talk_01_M");
  } finally {
    h.controls.stop();
    t.mock.timers.reset();
  }
});

test("changing language updates the reached dialogue cue without restarting audio or playback", (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const h = player([
    { clip: "one", t: 0 },
    { clip: "two", t: 0.4 },
    { clip: "three", t: 0.8 },
  ]);
  const table = {
    one: { "en-US": "First", "zh-CN": "First translation", voice: true },
    two: { voice: true },
    three: { "en-US": "Third", "zh-CN": "Third translation", voice: true },
  };
  try {
    h.controls.setLines(table);
    h.controls.playAll();
    t.mock.timers.tick(400);
    assert.equal(h.subtitles.at(-1).text, "First");
    assert.equal(h.voices.length, 2);
    h.controls.setLines({}, (line) => line?.["zh-CN"] || "");
    assert.deepEqual(h.subtitles.at(-1), { text: "", immediate: true });
    h.controls.setLines(table);
    assert.deepEqual(
      h.subtitles.at(-1),
      { text: "First translation", immediate: true },
      "a textless cue retains the earlier row in the new language",
    );
    assert.equal(h.controls.selected(), "Talk_01_M");
    assert.equal(h.controls.playingAll(), true);
    assert.deepEqual(h.starts, ["Talk_01_M"]);
    assert.equal(h.voices.length, 2);
    t.mock.timers.tick(400);
    assert.equal(h.subtitles.at(-1).text, "Third translation");
    assert.equal(h.voices.length, 3, "later cues keep their original schedule");
    h.controls.setLines(table, () => "");
    assert.deepEqual(
      h.subtitles.at(-1),
      { text: "", immediate: true },
      "an untranslated language clears the old subtitle",
    );
  } finally {
    h.controls.stop();
    t.mock.timers.reset();
  }
});

test("refreshing lines before any cue or after playback stops never reveals future or stale subtitles", (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const h = player([{ clip: "later", t: 0.5 }]),
    table = { later: { "en-US": "Later" } };
  try {
    h.controls.play("Talk_01_M");
    h.controls.setLines(table);
    assert.equal(h.subtitles.at(-1).text, "");
    t.mock.timers.tick(500);
    assert.equal(h.subtitles.at(-1).text, "Later");
    h.controls.stop();
    h.controls.setLines(table);
    assert.deepEqual(h.subtitles.at(-1), { text: "", immediate: true });
  } finally {
    h.controls.stop();
    t.mock.timers.reset();
  }
});

test("pending translations retain voice metadata for cues reached during the language request", (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const h = player([
    { clip: "one", t: 0 },
    { clip: "two", t: 0.5 },
  ]);
  try {
    h.controls.setLines({ one: { "en-US": "First", voice: true }, two: { "en-US": "Second", voice: true } });
    h.controls.play("Talk_01_M");
    t.mock.timers.tick(1);
    assert.equal(h.voices.length, 1);
    h.controls.setLines(undefined, (line) => line?.["zh-CN"] || "");
    assert.equal(h.subtitles.at(-1).text, "", "the new picker hides untranslated text immediately");
    t.mock.timers.tick(500);
    assert.equal(h.voices.length, 2, "voice cues keep playing while translations are pending");
    h.controls.setLines({
      one: { "zh-CN": "First translation", voice: true },
      two: { "zh-CN": "Second translation", voice: true },
    });
    assert.deepEqual(h.subtitles.at(-1), { text: "Second translation", immediate: true });
    assert.equal(h.voices.length, 2, "receiving translations never replays already reached voices");
    assert.deepEqual(h.starts, ["Talk_01_M"]);
  } finally {
    h.controls.stop();
    t.mock.timers.reset();
  }
});

test("subtitle forwards the actual selected fallback language to the shared balloon controller", () => {
  const shown = [],
    current = { caption: { show: (...args) => shown.push(args) } };
  const start = source.indexOf("function subtitle("),
    end = source.indexOf("\n  return {", start);
  const subtitle = new Function("S", "language", `${source.slice(start, end)}; return subtitle;`)(
    { current, talk: { cues: ["one"] }, lines: { one: { ja: "Fallback", th: "Translation" } } },
    () => "th-TH",
  );
  subtitle("Fallback");
  subtitle("Translation", true);
  assert.deepEqual(shown, [
    ["Fallback", false, "ja"],
    ["Translation", true, "th"],
  ]);
});
