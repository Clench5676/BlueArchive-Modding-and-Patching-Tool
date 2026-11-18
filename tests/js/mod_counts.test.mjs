import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

globalThis.document = {
  documentElement: { lang: "en" },
  createElement: () => ({ getContext: () => ({ measureText: (t) => ({ width: String(t).length * 20 }) }) }),
};
const { COUNT_ICONS, countNodes, countText, iconsOf } = await import("../../src/bmpt/web/js/mods/mod-counts.js");

test("count icons are the two named game sprites, found in the loaded atlases only", () => {
  const index = {
    Other: { Common_Icon_Lobby: { width: 80, height: 100 } },
    Common: {
      [COUNT_ICONS.portrait]: { width: 100, height: 80 },
      [COUNT_ICONS.memorialLobby]: { width: 90, height: 100 },
    },
  };
  const icons = iconsOf(index);
  assert.deepEqual(icons.portrait, { atlas: "Common", name: COUNT_ICONS.portrait, ratio: 1.25 });
  assert.equal(icons.memorialLobby.name, COUNT_ICONS.memorialLobby);
  assert.deepEqual(
    iconsOf({ Common: { [COUNT_ICONS.portrait]: { width: 40, height: 40 } } }).memorialLobby,
    undefined,
    "a missing sprite is never replaced by a guess",
  );
  assert.deepEqual(iconsOf({}), {});
  const [portraits, memorialLobbies] = countNodes({ portrait: 3, memorialLobby: 1 }, icons);
  assert.equal(portraits.children[0].widgets[0].text, "\u00d73");
  assert.equal(memorialLobbies.children[0].widgets[0].text, "\u00d71");
  assert.equal(memorialLobbies.children[1].widgets[0].sprite, icons.memorialLobby.name);
  assert.equal(memorialLobbies.children[1].widgets[0].atlas, icons.memorialLobby.atlas);
  const big = countNodes({ portrait: 1234567, memorialLobby: 0 }, icons);
  assert.ok(big[0].children[0].widgets[0].fontSize < 36);
  assert.equal(big[1].children[0].widgets[0].text, "\u00d70");
});

test("a count whose sprite is missing degrades to a word and the number", () => {
  const T = (key) => ({ "band.portrait": "Portrait", "band.memorialLobby": "Lobby" })[key];
  const icons = iconsOf({ Common: { [COUNT_ICONS.portrait]: { width: 40, height: 40 } } });
  const [portraits, memorialLobbies] = countNodes({ portrait: 3, memorialLobby: 1 }, icons, T);
  assert.equal(portraits.children.length, 2, "the portrait count keeps its icon");
  assert.equal(portraits.children[0].widgets[0].text, "\u00d73");
  assert.equal(memorialLobbies.children.length, 1, "no icon node for the missing sprite");
  assert.equal(memorialLobbies.children[0].widgets[0].text, "Lobby \u00d71");
  assert.ok(memorialLobbies.children[0].widgets[0].fontSize <= 36);
});

test("both counts have full localized text and the mod chip is kind neutral in all five locales", () => {
  const labels = { "zh-CN": "Mod", "zh-TW": "Mod", "en-US": "Mod", "ja-JP": "MOD", "ko-KR": "\ubaa8\ub4dc" };
  for (const [lang, mod] of Object.entries(labels)) {
    const ui = JSON.parse(readFileSync(new URL(`../../src/bmpt/locales/${lang}.json`, import.meta.url))).webui;
    assert.equal(ui.type.added, mod, lang);
    const T = (key, params) => {
      assert.equal(key, "band.modCounts");
      return ui.band.modCounts.replace(/\{(\w+)\}/g, (_, name) => params[name]);
    };
    const text = countText({ portrait: 3, memorialLobby: 1 }, T);
    assert.ok(text.includes(ui.tab.portrait) || lang === "en-US");
    assert.ok(text.includes(ui.tab.memorialLobby) || lang === "en-US");
    assert.ok(text.includes("3") && text.includes("1"));
    assert.ok(!text.includes("{"));
    assert.ok(ui.band.portrait && ui.band.memorialLobby && !("mods" in ui.band), lang);
    if (lang === "en-US") assert.equal(text, "Portraits: 3, Memorial lobbies: 1");
  }
});

test("the count nodes draw the student list icons and include chibis and generic assets", () => {
  const kinds = ["portrait", "memorialLobby", "chibi", "other"];
  const index = { Common: Object.fromEntries(kinds.map((kind) => [COUNT_ICONS[kind], { width: 40, height: 40 }])) };
  const nodes = countNodes({ portrait: 2, memorialLobby: 3, chibi: 4, other: 5 }, iconsOf(index), null, {
    kinds,
    width: 800,
  });
  assert.deepEqual(
    nodes.map((node) => node.name),
    kinds,
  );
  assert.deepEqual(
    nodes.map((node) => node.children[0].widgets[0].text),
    ["×2", "×3", "×4", "×5"],
  );
  assert.deepEqual(
    nodes.map((node) => node.pos[0]),
    [-300, -100, 100, 300],
  );
  assert.deepEqual(
    nodes.map((node) => node.children[1].widgets[0].sprite),
    kinds.map((kind) => COUNT_ICONS[kind]),
  );
});
