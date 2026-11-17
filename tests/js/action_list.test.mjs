// The 3D tab's action bar: the clip-name normaliser and grouping (clip-names.js), and the bar's and the action list's
// layout and text fit against the prefabs' geometry in every UI language (action-list-layout.js).
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { LOCALES, installFitPage, text } from "./helpers/fit_canvas.mjs";

const page = installFitPage();
const { glyphWidth, fontSizeFor, LINE_PITCH, fitText } = await import("../../src/bmpt/web/js/ngui.js");
const { ACTION_DEFS, actionLabel, actionsOf, groupLabel, menuOf, parseClip, sectionLabel } = await import(
  "../../src/bmpt/web/js/clip-names.js"
);
const L = await import("../../src/bmpt/web/js/action-list-layout.js");
// the clip names of three official chibis' controllers (names only), as their GLB lists them (extras.clips)
const MODELS = JSON.parse(readFileSync(new URL("./fixtures/model_clips.json", import.meta.url), "utf8"));

const GROUPS_ALL = [...new Set(ACTION_DEFS.map((d) => d.group))];
const parse = (suffix, dev = "Hoshino_Swimsuit") => parseClip(`${dev}_${suffix}`, dev.toLowerCase());

test("battle form numbers on any word fold to the slot and its form", () => {
  assert.deepEqual(parse("Normal01_Idle"), { slot: "Normal_Idle", form: 1, index: 0 });
  assert.deepEqual(parse("Normal_Idle01"), { slot: "Normal_Idle", form: 1, index: 0 });
  assert.deepEqual(parse("Normal_Idle_02"), { slot: "Normal_Idle", form: 2, index: 0 });
  assert.deepEqual(parse("Normal_Attack01_Start"), { slot: "Normal_Attack_Start", form: 1, index: 0 });
  assert.deepEqual(parse("Vital_Dying_ing02"), { slot: "Vital_Dying_Ing", form: 2, index: 0 });
  assert.deepEqual(parse("Normal_Callsign1"), { slot: "Normal_Callsign", form: 1, index: 0 });
  assert.deepEqual(parse("Kneel01_Idle"), { slot: "Kneel_Idle", form: 1, index: 0 });
  assert.deepEqual(parse("Move_End_Normal02"), { slot: "Move_End_Normal", form: 2, index: 0 });
  assert.deepEqual(parse("Normal_Reload02_Random"), { slot: "Normal_Reload_Random", form: 2, index: 0 });
});

test("skill numbers stay the skill's; a leading Normal word before a shared slot is dropped", () => {
  assert.deepEqual(parse("Exs"), { slot: "Exs", form: 0, index: 0 });
  assert.deepEqual(parse("Exs02"), { slot: "Exs", form: 0, index: 2 });
  assert.deepEqual(parse("Exs_03"), { slot: "Exs", form: 0, index: 3 });
  assert.deepEqual(parse("Exs1_02"), { slot: "Exs", form: 2, index: 1 });
  assert.deepEqual(parse("Ex01"), { slot: "Exs", form: 0, index: 1 });
  assert.deepEqual(parse("Public01"), { slot: "Public", form: 0, index: 1 });
  assert.deepEqual(parse("Normal02_Public01"), { slot: "Public", form: 2, index: 1 });
  assert.deepEqual(parse("Normal01_Victory_Start"), { slot: "Victory_Start", form: 1, index: 0 });
  assert.deepEqual(parse("TSS_Interaction01_Idle"), { slot: "TSS_Interaction_Idle", form: 0, index: 1 });
});

test("misspellings and odd case fold to the slot", () => {
  assert.equal(parse("Victory_Start_Rnadom").slot, "Victory_Start_Random");
  assert.equal(parse("Fomation_Pickup").slot, "Formation_Pickup");
  assert.equal(parse("Formation Pickup").slot, "Formation_Pickup");
  assert.equal(parse("Normal_Callsigh").slot, "Normal_Callsign");
  assert.equal(parse("Stand_Attack_STart").slot, "Stand_Attack_Start");
  assert.equal(parse("Normal_Attack_Strart").slot, "Normal_Attack_Start");
  assert.equal(parse("Nomal_Idle").slot, "Normal_Idle");
  assert.equal(parse("Norma_Attack_Delay").slot, "Normal_Attack_Delay");
  assert.equal(parse("Publlic01").slot, "Public");
  assert.equal(parse("Move_ing").slot, "Move_Ing");
  assert.equal(parse("Normal_attack_End").slot, "Normal_Attack_End");
  assert.equal(parse("Tactical_Start_random").slot, "Tactical_Start_Random");
});

test("only the character's own clips count: the shared base controller's and unknown slots are left out", () => {
  const dev = "hihumi_original";
  for (const name of ["Kneel_Idle", "Exs2", "PublicSkill01", "CH0000_Normal_Idle", "Hoshino_Swimsuit_Normal_Idle"])
    assert.equal(parseClip(name, dev), null, name);
  for (const suffix of [
    "Appear",
    "Vital_Groggy",
    "Helicopter_Normal_Idle",
    "Exs01_Idle",
    "Normal_Attack_ReadyStart",
    "Rhythm_Attack01",
  ]) {
    assert.equal(parse(suffix, "Hihumi_Original"), null, suffix);
  }
});

const ofModel = (core) => actionsOf(MODELS[core].names, MODELS[core].dev);

test("Hifumi: six groups, both cover sets, the attack's four clips in order, no random variants", () => {
  const actions = ofModel("hihumi_original"),
    tabs = menuOf(actions);
  assert.deepEqual(
    tabs.map((t) => t.id),
    ["basic", "combat", "move", "skills", "status", "other"],
  );
  assert.deepEqual(
    tabs[2].sections.map((s) => s.id),
    ["movement", "kneel", "stand"],
  );
  const attack = actions.find((a) => a.key === "attack");
  assert.deepEqual(
    attack.clips.map((c) => c.replace("Hihumi_Original_", "")),
    ["Normal_Attack_Start", "Normal_Attack_Ing", "Normal_Attack_Delay", "Normal_Attack_End"],
  );
  assert.deepEqual(
    actions.slice(0, 3).map((a) => a.key),
    ["idle", "reaction", "walk"],
  );
  assert.ok(
    actions.every((a) => !a.form),
    "one battle form",
  );
  assert.deepEqual(
    actions.filter((a) => a.group === "skills").map((a) => [a.key, a.number]),
    [
      ["ex", null],
      ["skill1", null],
    ],
  );
  assert.deepEqual(
    actions.filter((a) => a.group === "other").map((a) => a.key),
    ["rearrange", "rearrangeReaction", "tss1"],
  );
  assert.equal(new Set(actions.map((a) => a.key)).size, actions.length, "keys are unique");
});

test("Hoshino (Swimsuit): a section per battle form after the shared one, random variants of either", () => {
  const actions = ofModel("hoshino_swimsuit"),
    tabs = menuOf(actions);
  assert.deepEqual(
    tabs.map((t) => t.id),
    ["basic", "combat", "move", "skills", "status", "random"],
  );
  const combat = tabs.find((t) => t.id === "combat");
  assert.deepEqual(
    combat.sections.map((s) => [s.id, s.form]),
    [
      ["combat", 0],
      ["combat", 1],
      ["combat", 2],
    ],
  );
  assert.deepEqual(
    combat.sections[2].actions.map((a) => a.key),
    ["combatIdle@2", "attack@2", "reload@2"],
  );
  const random = tabs.find((t) => t.id === "random");
  assert.deepEqual(
    random.sections.map((s) => s.actions.map((a) => a.key)),
    [["randomVictory", "randomCallsign"], ["randomReload@2"]],
  );
  const T = (key, params) => text("zh-CN", key, params);
  assert.equal(sectionLabel(combat.sections[1], T), "战斗（形态 1）");
  assert.equal(sectionLabel(tabs.find((t) => t.id === "move").sections[0], T), "移动（形态 1）");
  assert.equal(
    actionLabel(
      actions.find((a) => a.key === "attack@2"),
      T,
    ),
    "普通攻击（形态 2）",
  );
  assert.equal(
    sectionLabel(combat.sections[1], (key, params) => text("en-US", key, params)),
    "Combat (Form 1)",
  );
  // each form's retreat plays that form's crying clip alone (the first's from the GLB, the second's added on demand)
  const retreats = actions.filter((a) => a.id === "retreat");
  assert.deepEqual(
    retreats.map((a) => [a.key, a.clips.map((c) => c.replace("Hoshino_Swimsuit_", ""))]),
    [
      ["retreat@1", ["Vital_Retreat01"]],
      ["retreat@2", ["Vital_Retreat02"]],
    ],
  );
});

test("Ayane: two group cards, Basic and Combat", () => {
  const actions = ofModel("ayane_original");
  assert.deepEqual(
    actions.map((a) => a.key),
    ["idle", "reaction", "walk", "pickup", "tacticalEntry"],
  );
  assert.deepEqual(
    menuOf(actions).map((t) => t.id),
    ["basic", "combat"],
  );
});

test("several EX skills are numbered, one is not", () => {
  const dev = "x_y",
    names = ["X_Y_Formation_Idle", "X_Y_Exs01", "X_Y_Exs02", "X_Y_Exs03", "X_Y_Public01"];
  const actions = actionsOf(names, dev);
  const T = (key, params) => text("zh-CN", key, params);
  assert.deepEqual(
    actions.filter((a) => a.id === "ex").map((a) => actionLabel(a, T)),
    ["EX 技能 1", "EX 技能 2", "EX 技能 3"],
  );
  assert.equal(
    actionLabel(
      actions.find((a) => a.id === "skill"),
      T,
    ),
    "普通技能",
  );
});

test("the retreat plays the crying clip alone, in the status group; without it no retreat", () => {
  const dev = "x_y",
    status = (names) =>
      actionsOf(names, dev)
        .filter((a) => a.group === "status")
        .map((a) => [a.key, a.clips]);
  assert.deepEqual(status(["X_Y_Formation_Idle", "X_Y_Vital_Retreat", "X_Y_Vital_Death"]), [
    ["retreat", ["X_Y_Vital_Retreat"]],
    ["knockdown", ["X_Y_Vital_Death"]],
  ]);
  assert.deepEqual(status(["X_Y_Formation_Idle", "X_Y_Vital_Retreat"]), [["retreat", ["X_Y_Vital_Retreat"]]]);
  assert.deepEqual(status(["X_Y_Formation_Idle", "X_Y_Vital_Death"]), [["knockdown", ["X_Y_Vital_Death"]]]);
  // a Special student's controller: only the base's bare placeholders, no action of the status group
  assert.deepEqual(status(["X_Y_Formation_Idle", "Vital_Retreat", "VitalHit", "VitalDeathGroggy"]), []);
});

// --- the action list over its card ---
const { STAGE, EDGE, GAP, BOX, HEAD, ROW, CARD_TEXT } = L;
const card = (x) => ({ left: x - 121, right: x + 121, top: -552 + 98 }); // detail.js: CARD 242 wide, ROW4_Y -552
const inside = (p) =>
  p.visible.left >= STAGE.left + EDGE - 1e-6 &&
  p.visible.right <= STAGE.right - EDGE + 1e-6 &&
  p.visible.top <= STAGE.top - EDGE + 1e-6 &&
  p.visible.bottom >= card(0).top;

test("the list opens upward centred on its card, shifted left only as far as the stage needs", () => {
  const p = L.placeList(card(461.9), 500, 600);
  const visW = p.visible.right - p.visible.left;
  assert.ok(Math.abs((p.visible.left + p.visible.right) / 2 - 461.9) < 1e-6, "centred on the card");
  assert.equal(p.visible.bottom, card(0).top + GAP, "GAP over the card");
  assert.equal(p.view, 600);
  assert.equal(p.scrolls, false);
  // near the stage's right edge: flush with the stage's right edge (less EDGE), still over the card
  const right = L.placeList(card(1380), 500, 600);
  assert.ok(Math.abs(right.visible.right - (STAGE.right - EDGE)) < 1e-6);
  assert.ok(
    (right.visible.left < 1380 - 121 && right.visible.right > 1380 + 121 - 1e-6) ||
      right.visible.right >= STAGE.right - EDGE - 1e-6,
  );
  assert.ok(inside(right));
  // every card the bar can show (x 461.9 to 1316): the list stays over it
  for (let x = 461.9; x <= 1316; x += 61.4) {
    const q = L.placeList(card(x), 500, 600);
    assert.ok(q.visible.left <= x && q.visible.right >= Math.min(x, STAGE.right - EDGE), `card at ${x}`);
    assert.ok(
      Math.abs((q.visible.left + q.visible.right) / 2 - x) < 1e-6 || q.visible.right >= STAGE.right - EDGE - 1e-6,
      `card at ${x}: shifted only at the edge`,
    );
    assert.ok(visW > 0);
  }
});

test("the list never leaves the stage: every card position on a scrolled bar, every content size", () => {
  for (let x = -1400; x <= 1400; x += 37)
    for (const cw of [L.BOX.minW, 560, BOX.maxW - 2 * BOX.padX])
      for (const h of [120, 700, 3000]) {
        const p = L.placeList(card(x), cw, h);
        assert.ok(inside(p), `card at ${x}, ${cw} x ${h}: ${JSON.stringify(p.visible)}`);
        assert.ok(p.view <= h + 1e-6 && p.scrolls === p.view < h);
      }
});

test("a list taller than the room over its card is capped at the top bar and scrolls", () => {
  const p = L.placeList(card(461.9), 500, 3000);
  assert.equal(p.scrolls, true);
  assert.ok(Math.abs(p.visible.top - (STAGE.top - EDGE)) < 1e-6, "up to the top bar, less EDGE");
});

test("sections stack as title bars over one key to a line", () => {
  const s = (n) => ({ actions: Array.from({ length: n }, (_, i) => ({ key: `a${i}` })) });
  const { rows, height } = L.stackList([s(4), s(5), s(5)]);
  assert.deepEqual(
    rows.filter((r) => r.kind === "head").map((r) => r.y),
    [-30, -30 - 60 - 4 * 120 - 20, -30 - 2 * 60 - 9 * 120 - 2 * 20],
  );
  assert.equal(height, 3 * HEAD.h + 14 * ROW.pitch + 2 * HEAD.gap);
  for (let i = 1; i < rows.length; i++) assert.ok(rows[i].y < rows[i - 1].y);
  // one section: no title bar, its card names it
  const one = L.stackList([s(3)]);
  assert.deepEqual(
    one.rows.map((r) => [r.kind, r.y]),
    [
      ["key", -60],
      ["key", -180],
      ["key", -300],
    ],
  );
  assert.equal(one.height, 3 * ROW.pitch);
});

test("the widest content stays in the prefab's box", () => {
  assert.equal(L.contentWidth([10], [10]), BOX.minW);
  const cw = L.contentWidth([ROW.maxLabelW], [100]);
  assert.ok(cw <= BOX.maxW - 2 * BOX.padX);
  assert.ok(ROW.radioX + ROW.labelX + ROW.maxLabelW <= cw, "the widest label inside the box");
});

// every action, numbered and in a second form, so every string is measured
function everyAction() {
  return ACTION_DEFS.flatMap((d) =>
    [0, 2].map((form) => ({
      id: d.id,
      group: d.group,
      section: d.section || d.group,
      form,
      index: d.indexed ? 3 : 0,
      number: d.indexed && form ? 3 : null,
      key: `${d.id}@${form}`,
      clips: [],
    })),
  );
}
const lines = (s) => String(s).split("\n");
const widest = (s, fs) => Math.max(...lines(s).map((l) => glyphWidth(l, fs)));
const tall = (f) => f.lines * fontSizeFor(f.text, f.fs) * LINE_PITCH;

for (const locale of LOCALES)
  test(`the action list's text fits its widgets in ${locale}`, () => {
    page.lang = locale;
    const T = (key, params) => text(locale, key, params);
    const tabs = menuOf(everyAction());
    for (const tab of tabs) {
      // one list per group: its titles one line in the box, its keys one size, two lines at most, inside a line
      const heads = tab.sections.map((s) => sectionLabel(s, T)),
        keys = tab.sections.flatMap((s) => s.actions.map((a) => actionLabel(a, T, { withForm: false })));
      const fit = L.fitList(heads, keys);
      const cw = L.contentWidth(
        fit.keys.map((f) => widest(f.text, f.fs)),
        fit.heads.map((f) => widest(f.text, f.fs)),
      );
      for (const f of fit.heads)
        assert.ok(
          f.lines === 1 && widest(f.text, f.fs) <= cw - 2 * HEAD.inset && f.fs >= HEAD.min,
          `${locale} ${f.text}`,
        );
      assert.equal(new Set(fit.keys.map((f) => f.fs)).size, 1);
      assert.ok(fit.keys[0].fs >= L.KEY_TYPE.min, `${locale} ${tab.id} keys at ${fit.keys[0].fs}`);
      for (const f of fit.keys) {
        assert.ok(!f.overflow && f.lines <= 2 && widest(f.text, f.fs) <= ROW.maxLabelW, `${locale} ${f.text}`);
        assert.ok(ROW.radioX + ROW.labelX + widest(f.text, f.fs) <= cw, `${locale} ${f.text} inside the box`);
        // the label's box too: nothing reaches past the content's right edge, so the list never scrolls sideways
        assert.ok(
          widest(f.text, f.fs) <= L.keyLabelWidth(cw) && ROW.radioX + ROW.labelX + L.keyLabelWidth(cw) <= cw,
          `${locale} ${f.text} label box`,
        );
        assert.ok(tall(f) <= ROW.pitch - 24, `${locale} ${f.text} height`);
      }
    }
  });

for (const locale of LOCALES)
  test(`the group cards' names keep the caption size, wrapped, and the playing action fits in ${locale}`, () => {
    page.lang = locale;
    const T = (key, params) => text(locale, key, params);
    const names = GROUPS_ALL.map((g) => groupLabel(g, T)),
      fits = L.fitCards(names);
    assert.equal(new Set(fits.map((f) => f.fs)).size, 1, "one size for the sibling cards");
    for (const f of fits)
      assert.ok(
        !f.overflow && f.lines <= 2 && widest(f.text, f.fs) <= CARD_TEXT.w && tall(f) <= CARD_TEXT.room,
        `${locale} ${f.text}`,
      );
    // a name too wide for one line wraps at the names' size instead of shrinking (CJK names, Move & Cover)
    const move = L.fitCards([groupLabel("move", T)])[0],
      cjk = /^(zh|ja|ko)/.test(locale);
    assert.equal(move.lines, 2, `${locale} ${move.text} wraps`);
    if (cjk) assert.equal(move.fs, CARD_TEXT.name.fs, `${locale} ${move.text} at ${move.fs}`);
    // the list keys' size at most, with clear space at the card's leaning sides; never under 36 with every group
    assert.ok(fits[0].fs <= L.KEY_TYPE.fs && fits[0].fs >= 36, `${locale} names at ${fits[0].fs}`);
    assert.ok(
      CARD_TEXT.w + 2 * (CARD_TEXT.room / 2) * Math.tan(Math.PI / 18) <= 242 - 2 * 12,
      "clear of the leaning sides",
    );
    // a name alone sits on the card's centre
    assert.deepEqual(L.cardText(fits[0]), { name: fits[0], nameY: 0 });
    // the playing card: the action's name alone, never larger than its siblings, inside the body, clear of the check,
    // and centred unless its first line would reach under the check
    const half = CARD_TEXT.room / 2,
      { check } = CARD_TEXT,
      sizes = [];
    for (const a of everyAction()) {
      const c = L.cardText(fits[0], actionLabel(a, T)),
        h = tall(c.name),
        first = glyphWidth(lines(c.name.text)[0], c.name.fs);
      assert.ok(
        !c.name.overflow && c.name.lines <= CARD_TEXT.playing.lines && widest(c.name.text, c.name.fs) <= CARD_TEXT.w,
        `${locale} ${c.name.text}: ${c.name.fs}`,
      );
      assert.ok(
        c.name.fs >= CARD_TEXT.playing.min && c.name.fs <= fits[0].fs,
        `${locale} ${c.name.text}: ${c.name.fs} next to ${fits[0].fs}`,
      );
      assert.equal(
        c.name.text.replace(/\n/g, "").replace(/\s/g, ""),
        actionLabel(a, T).replace(/\s/g, ""),
        "the action's name only",
      );
      assert.ok(
        c.nameY + h / 2 <= half + 1e-6 && c.nameY - h / 2 >= -half - 1e-6,
        `${locale} ${c.name.text} inside the body`,
      );
      const ink = L.inkHeight(c.name),
        top = c.nameY + ink / 2,
        reaches = first / 2 > check.left - check.clear;
      assert.ok(!reaches || top <= check.bottom - check.clear + 1e-6, `${locale} ${c.name.text} under the check`);
      if (c.nameY !== 0)
        assert.ok(reaches && ink / 2 > check.bottom - check.clear, `${locale} ${c.name.text} moved only for the check`);
      sizes.push([a.form, c.name.fs, c.name.fs === fits[0].fs]);
    }
    // most actions show at their siblings' size
    const plain = sizes.filter(([form]) => !form);
    assert.ok(
      plain.filter(([, , same]) => same).length >= plain.length * 0.6,
      `${locale}: ${plain.map(([, fs]) => fs).join(" ")}`,
    );
    // the bar's usual actions keep the siblings' size and the card's centre (within a unit or two of it)
    for (const id of ["idle", "combatIdle", "attack", "ex"]) {
      const c = L.cardText(fits[0], T(`model.action.${id}`));
      assert.ok(
        c.name.fs === fits[0].fs && Math.abs(c.nameY) <= 2,
        `${locale} ${c.name.text} at ${c.name.fs}, ${c.nameY}`,
      );
    }
  });

test("the section title bars lean inside the content", () => {
  const cw = 500,
    half = (cw - L.HEAD.slant) / 2,
    lean = (L.HEAD.h / 2) * Math.tan((10 * Math.PI) / 180);
  assert.ok(half + lean <= cw / 2, "the bar's tips inside the scroll area");
});
