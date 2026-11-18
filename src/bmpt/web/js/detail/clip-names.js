// A chibi's animation clips by what they show. The controller names its clips "<Prefab>_<Slot>", where the
// slot's spelling varies between characters: a battle form's number may sit on any word (Normal01_Idle,
// Normal_Idle01, Normal_Idle_01), words change case (Move_ing, Normal_attack_Start) and some are misspelt
// (Rnadom, Fomation, Callsigh, Nomal, Publlic, "Formation Pickup"). parseClip folds a name to { slot, form,
// index }; only names with the character's own prefix count (the shared base controller's Kneel_Idle, Exs2 or
// PublicSkill01 are not the character's). actionsOf groups the clips into the 3D tab's actions, menuOf into the
// action bar's group cards and their lists' sections.

// misspellings, fixed before the words are read
const FIXES = [
  [/rnadom/gi, "Random"],
  [/fomation/gi, "Formation"],
  [/formation pickup/gi, "Formation_Pickup"],
  [/callsigh(?!n)/gi, "Callsign"],
  [/strart/gi, "Start"],
  [/publlic/gi, "Public"],
  [/^nomal(?=_|\d)/i, "Normal"],
  [/^norma(?=_)/i, "Normal"],
];
// each word's one spelling
const WORDS = new Map(
  [
    "Normal",
    "Attack",
    "Start",
    "Ing",
    "Delay",
    "End",
    "Idle",
    "Reload",
    "Callsign",
    "Random",
    "Move",
    "Jump",
    "Kneel",
    "Stand",
    "Vital",
    "Dying",
    "Panic",
    "Death",
    "Retreat",
    "Victory",
    "Formation",
    "Pickup",
    "Cafe",
    "Walk",
    "Reaction",
    "Tactical",
    "Exs",
    "Ex",
    "Public",
    "Rearrange",
    "TSS",
    "Interaction",
  ].map((w) => [w.toLowerCase(), w]),
);
// words whose number counts the skill or the interaction (EX 2, Normal Skill 1), not the battle form
const INDEXED = new Set(["Exs", "Ex", "Public", "Interaction"]);
// slots also written with a leading "Normal" word (Normal01_Public01, Normal01_Victory_Start)
const UNPREFIXED = new Set([
  "Exs",
  "Public",
  "Victory_Start",
  "Victory_End",
  "Victory_Start_Random",
  "Victory_End_Random",
  "Vital_Death",
  "Vital_Dying_Ing",
  "Vital_Panic",
  "Vital_Retreat",
  "Move_Ing",
  "Move_End_Normal",
  "Move_Jump",
]);
const RENAMED = { Ex: "Exs", Vital_Dying: "Vital_Dying_Ing" };

/**
 * Fold a clip name to its slot, battle form and index.
 * @param {string} name The clip's name.
 * @param {string} dev The character's prefab name.
 * @returns {{slot: string, form: number, index: number}|null} Form and index are 0 when the name has none; null
 *     for another prefab's clip or a slot no action plays.
 */
export function parseClip(name, dev) {
  const prefix = `${dev}_`.toLowerCase();
  if (typeof name !== "string" || !dev || !name.toLowerCase().startsWith(prefix)) return null;
  let rest = name.slice(prefix.length);
  for (const [re, to] of FIXES) rest = rest.replace(re, to);
  const words = [];
  let form = 0,
    index = 0;
  for (const token of rest.split("_")) {
    const m = /^([A-Za-z]*)(\d*)$/.exec(token);
    if (!m) return null;
    const [, letters, digits] = m;
    const n = digits ? Number(digits) : 0;
    if (letters) {
      const word = WORDS.get(letters.toLowerCase());
      if (!word) return null;
      words.push(word);
      if (digits) {
        if (INDEXED.has(word)) index = n;
        else form = n;
      }
    } else if (digits) {
      // a number on its own: the skill's after a skill word (Exs_02), else the form (Normal_Idle_01, Exs_1_02)
      if (INDEXED.has(words.at(-1)) && !index) index = n;
      else form = n;
    }
  }
  let slot = words.join("_");
  if (words[0] === "Normal" && UNPREFIXED.has(words.slice(1).join("_"))) slot = words.slice(1).join("_");
  slot = RENAMED[slot] || slot;
  return SLOTS.has(slot) ? { slot, form, index } : null;
}

// The 3D tab's actions in menu order: id, the group (its card on the action bar) and section it is listed in,
// and the clips it plays in turn (play: the first is required). A looping clip loops; a chain plays through once
// and holds its last clip (or loops it, as the attack's Ing and the idles do).
export const ACTION_DEFS = [
  { id: "idle", group: "basic", pick: ["Formation_Idle", "Cafe_Idle", "Idle"] },
  { id: "reaction", group: "basic", play: ["Cafe_Reaction"] },
  { id: "walk", group: "basic", play: ["Cafe_Walk"] },
  { id: "victory", group: "basic", play: ["Victory_Start", "Victory_End"] },
  { id: "pickup", group: "basic", play: ["Formation_Pickup"] },
  { id: "combatIdle", group: "combat", play: ["Normal_Idle"] },
  {
    id: "attack",
    group: "combat",
    play: ["Normal_Attack_Start", "Normal_Attack_Ing", "Normal_Attack_Delay", "Normal_Attack_End"],
  },
  { id: "reload", group: "combat", play: ["Normal_Reload"] },
  { id: "callsign", group: "combat", play: ["Normal_Callsign"] },
  { id: "tacticalEntry", group: "combat", play: ["Tactical_Start"] },
  { id: "run", group: "move", section: "movement", play: ["Move_Ing"] },
  { id: "stop", group: "move", section: "movement", play: ["Move_End_Normal"] },
  { id: "jump", group: "move", section: "movement", play: ["Move_Jump"] },
  { id: "moveCallsign", group: "move", section: "movement", play: ["Move_Callsign"] },
  { id: "kneelIn", group: "move", section: "kneel", play: ["Move_End_Kneel"] },
  { id: "kneelIdle", group: "move", section: "kneel", play: ["Kneel_Idle"] },
  {
    id: "kneelAttack",
    group: "move",
    section: "kneel",
    play: ["Kneel_Attack_Start", "Kneel_Attack_Ing", "Kneel_Attack_Delay", "Kneel_Attack_End"],
  },
  { id: "kneelReload", group: "move", section: "kneel", play: ["Kneel_Reload"] },
  { id: "kneelCallsign", group: "move", section: "kneel", play: ["Kneel_Callsign"] },
  { id: "standIn", group: "move", section: "stand", play: ["Move_End_Stand"] },
  { id: "standIdle", group: "move", section: "stand", play: ["Stand_Idle"] },
  {
    id: "standAttack",
    group: "move",
    section: "stand",
    play: ["Stand_Attack_Start", "Stand_Attack_Ing", "Stand_Attack_Delay", "Stand_Attack_End"],
  },
  { id: "standReload", group: "move", section: "stand", play: ["Stand_Reload"] },
  { id: "standCallsign", group: "move", section: "stand", play: ["Stand_Callsign"] },
  { id: "ex", group: "skills", play: ["Exs"], indexed: true },
  { id: "skill", group: "skills", play: ["Public"], indexed: true },
  { id: "panic", group: "status", play: ["Vital_Panic"] },
  { id: "dying", group: "status", play: ["Vital_Dying_Ing"] },
  { id: "retreat", group: "status", play: ["Vital_Retreat"] },
  { id: "knockdown", group: "status", play: ["Vital_Death"] },
  { id: "randomIdle", group: "random", play: ["Formation_Idle_Random"] },
  { id: "randomVictory", group: "random", play: ["Victory_Start_Random", "Victory_End_Random"] },
  { id: "randomCallsign", group: "random", play: ["Normal_Callsign_Random"] },
  { id: "randomReload", group: "random", play: ["Normal_Reload_Random"] },
  { id: "randomKneelReload", group: "random", play: ["Kneel_Reload_Random"] },
  { id: "randomTacticalEntry", group: "random", play: ["Tactical_Start_Random"] },
  { id: "randomJump", group: "random", play: ["Move_Jump_Random"] },
  { id: "rearrange", group: "other", play: ["Rearrange_Start", "Rearrange_Idle"] },
  { id: "rearrangeReaction", group: "other", play: ["Rearrange_Reaction"] },
  { id: "tss", group: "other", play: ["TSS_Interaction", "TSS_Interaction_Idle"], indexed: true },
];
// the action groups, in order; a group's sections in order (a group without sections has one, named like the group)
export const GROUPS = ["basic", "combat", "move", "skills", "status", "random", "other"];
export const SECTIONS = { move: ["movement", "kneel", "stand"] };
const SLOTS = new Set(ACTION_DEFS.flatMap((d) => [...(d.pick || []), ...(d.play || [])]));

/**
 * The actions a model's clip names give, in menu order.
 * @param {string[]} names The model's clip names.
 * @param {string} dev The character's prefab name.
 * @returns
 *     {{key: string, id: string, group: string, section: string, form: number, index: number, number: number|null, clips: string[]}[]}
 *     key is the id, the skill's number and "@form" (e.g. "attack@2", "ex2"); number is the skill's number when
 *     the model has several of that skill, else null; form is 0 unless the model has two or more battle forms.
 */
export function actionsOf(names, dev) {
  const parsed = [];
  for (const name of names || []) {
    const p = parseClip(name, dev);
    if (p) parsed.push({ name, ...p });
  }
  // one form number alone is no second form: fold it into the plain one
  const forms = new Set(parsed.map((p) => p.form).filter(Boolean));
  if (forms.size < 2)
    parsed.forEach((p) => {
      p.form = 0;
    });
  const named = new Map();
  for (const p of parsed) {
    const k = `${p.slot}|${p.form}|${p.index}`;
    if (!named.has(k)) named.set(k, p.name); // the first clip of a slot wins, as the export picks it
  }
  const alts = (slot) =>
    parsed
      .filter((p) => p.slot === slot)
      .map((p) => [p.form, p.index])
      .filter(([f, i], n, all) => all.findIndex(([g, j]) => g === f && j === i) === n)
      .sort((a, b) => a[0] - b[0] || a[1] - b[1]);
  const out = [];
  for (const def of ACTION_DEFS) {
    const lead = def.pick ? def.pick.find((s) => alts(s).length) : def.play[0];
    if (!lead) continue;
    const found = [];
    for (const [form, index] of alts(lead)) {
      const at = (slot) => named.get(`${slot}|${form}|${index}`);
      const clips = (def.pick ? [lead] : def.play).map(at).filter(Boolean);
      found.push({ id: def.id, group: def.group, section: def.section || def.group, form, index, clips });
    }
    for (const a of found) {
      const several = def.indexed && found.filter((b) => b.form === a.form).length > 1;
      a.number = several ? a.index || 1 : null;
      a.key = `${a.id}${def.indexed && a.index ? a.index : ""}${a.form ? `@${a.form}` : ""}`;
      out.push(a);
    }
  }
  return out;
}

/**
 * The action groups: [{ id, sections: [{ id, form, actions }] }], groups and sections in menu order, a section
 * per battle form after the shared one. A group's sections are in order; a group without sections has one,
 * named like the group.
 */
export function menuOf(actions) {
  const tabs = [];
  for (const group of GROUPS) {
    const mine = actions.filter((a) => a.group === group);
    if (!mine.length) continue;
    const sections = [];
    const forms = [...new Set(mine.map((a) => a.form))].sort((a, b) => a - b);
    for (const form of forms) {
      for (const section of SECTIONS[group] || [group]) {
        const list = mine.filter((a) => a.form === form && a.section === section);
        if (list.length) sections.push({ id: section, form, actions: list });
      }
    }
    tabs.push({ id: group, sections });
  }
  return tabs;
}

/**
 * The label of an action: the battle form with it on row 4's cards; the list's keys leave it to their section's
 * title.
 * @param {object} [options]
 * @param {boolean} [options.withForm] False leaves the battle form out.
 */
export function actionLabel(a, T, { withForm = true } = {}) {
  const name = a.number ? T(`model.action.${a.id}N`, { n: a.number }) : T(`model.action.${a.id}`);
  return a.form && withForm ? T("model.menu.form", { name, n: a.form }) : name;
}
/** The label of an action group. */
export const groupLabel = (id, T) => T(`model.menu.group.${id}`);
/** The label of a section; a section of a group without sections is named like the group. */
export function sectionLabel(section, T) {
  const name = GROUPS.includes(section.id) ? groupLabel(section.id, T) : T(`model.menu.section.${section.id}`);
  return section.form ? T("model.menu.form", { name, n: section.form }) : name;
}
