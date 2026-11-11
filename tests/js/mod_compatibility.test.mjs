// Drawing checks use the bundled Spine reader and synthetic assets, without WebGL or game files.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import vm from "node:vm";
import { jobProgress } from "../../src/bmpt/web/js/job-progress.js";
import { setStrings } from "../../src/bmpt/web/js/i18n.js";

const context = vm.createContext({ console, TextDecoder, TextEncoder, Float32Array, Uint8Array, Uint16Array, Int16Array, Int32Array, ArrayBuffer, DataView });
vm.runInContext(readFileSync(new URL("../../src/bmpt/web/vendor/spine-player-4.2.120/spine-player.min.js", import.meta.url), "utf8"), context);
globalThis.spine = context.spine;
const source = readFileSync(new URL("../../src/bmpt/web/js/mod-compatibility.js", import.meta.url), "utf8");
const fixture = JSON.parse(readFileSync(new URL("../assets/integrity/mismatched_skeleton.json", import.meta.url), "utf8"));
const encoded = (data) => ({ version: data.skeleton.spine, data: Buffer.from(JSON.stringify(data)).toString("base64") });
const atlas = (x = 1, name = "smile", size = 64) => `page.png\nsize: ${size},${size}\n${name}\n bounds: ${x},1,8,8\n`;
const inputs = (data = fixture.official) => ({ skeletons: { "test.skel": encoded(data) }, atlases: { "test.atlas": atlas() }, textures: { page: [64, 64] } });
const report = () => ({ official: inputs(), mod: inputs(), remappable: true });
const mixedReport = () => { const r = report(); r.mod.textures.page = [64, 32]; return r; };
let sequence = 0;
async function harness(locale = "en-US", button = "cancel", accept = false) {
  const strings = JSON.parse(readFileSync(new URL(`../../src/bmpt/locales/${locale}.json`, import.meta.url), "utf8")).webui;
  globalThis.document ||= { documentElement: {}, getElementById: () => null };
  const flat = (o, prefix = "webui.", out = {}) => {
    for (const [key, value] of Object.entries(o)) {
      if (value && typeof value === "object") flat(value, `${prefix}${key}.`, out); else out[prefix + key] = value;
    } return out;
  };
  setStrings(locale, flat(strings));
  const calls = [], popups = [], comparisons = [], covers = [], labels = [], progress = [];
  let nextReport = report(), rebuilt = null, held = false, covered = false, licensed = true, confirms = 0;
  let taskActive = false, stamp = "inputs-1", afterCheck = () => {}, redrawn = { redrawn: false, overlap: 0.99 };
  const geometries = [], posed = [];
  let steps = {};
  const saved = { mod: { id: "new-mod", repaired_from: "old-mod" } };
  globalThis.compatibilityDeps = {
    jobProgress,
    ensureSpine: async () => { if (licensed === "error") throw new Error("Runtime failed to load"); return licensed; },
    language: () => locale,
    T: (key, vars = {}) => key.split(".").reduce((o, k) => o?.[k], strings)?.replace(/\{(\w+)\}/g, (_, k) => vars[k] ?? "") || key,
    rpc: async (method, params) => {
      if (method === "actions.compatibilityStamp") return stamp;
      calls.push([method, params]);
      if (["actions.remap", "actions.oldRig", "actions.acceptRemap"].includes(method)) assert.ok(held && covered && taskActive);
      return method;
    },
    waitJob: async (id, update) => {
      assert.equal(typeof update, "function");
      update({ current: 2, total: 3, label: "assets-raw.bundle", step: "import.step.replace" });
      if (id === "actions.compatibility") afterCheck();
      if (id in steps) return steps[id];
      return ["actions.remap", "actions.oldRig"].includes(id) ? rebuilt : id === "actions.acceptRemap" ? saved : id === "actions.redrawn" ? redrawn : nextReport;
    },
    Loading: { task: async (fn, options) => {
        labels.push(options.label); taskActive = true;
        try { return await fn((value) => progress.push(value)); } finally { taskActive = false; }
      },
      hold: async (fn, opts) => { assert.equal(opts.all, true); held = true; try { return await fn(); } finally { held = false; } },
      page: async (fn) => { covered = true; covers.push(true); try { return await fn(); } finally { covered = false; } },
    },
    Popup: { message: async (options) => { assert.ok(!held && !covered && !taskActive); popups.push(options); return { button }; } },
    RigGeometry: { forRemap: (report) => { geometries.push(report); return { "test.atlas": { source: { slots: [], poses: { "": [] } }, target: { slots: [], poses: { "": [] } } } }; },
      restOnly: (geometry) => ({ ...geometry, rest: true }),
      setupOf: (_S, atlasText) => { posed.push(["setup", atlasText]); return { bones: {}, slotBones: {}, skin: [] }; },
      partPoses: (_S, atlasText, _skeleton, check) => { posed.push(["poses", atlasText, check]); return { slots: [], poses: { "": [] } }; } },
    Compare: { show: async (...args) => { assert.ok(!held && !covered && !taskActive); comparisons.push(args); return accept; } },
  };
  const code = `const { T, language, rpc, waitJob, Loading, Popup, Compare, ensureSpine, jobProgress, RigGeometry } = globalThis.compatibilityDeps;\n${source.replace(/^import .*;$/gm, "")}\n// ${sequence++}`;
  const mod = await import(`data:text/javascript;base64,${Buffer.from(code).toString("base64")}`);
  return { mod, calls, popups, comparisons, covers, labels, progress, strings, saved,
    use: (value) => { nextReport = value; }, build: (value) => { rebuilt = value; },
    stamp: (value) => { stamp = value; }, afterCheck: (fn) => { afterCheck = fn; },
    license: (value) => { licensed = value; }, confirms: () => confirms,
    redraw: (value) => { redrawn = value; }, geometries, posed, steps: (value) => { steps = value; } };
}

test("pending acknowledgement skips the runtime check without a licence popup or blocking update inputs", async () => {
  const h = await harness(); h.license(false);
  await h.mod.check("old-mod");
  assert.deepEqual(h.calls, []);
  assert.deepEqual(h.popups, []);
  assert.equal(h.confirms(), 0);
  h.use(mixedReport());
  assert.ok(await h.mod.inspect("old-mod"));
  assert.equal(h.mod.canRemap("old-mod"), false);
  assert.deepEqual(h.calls.filter(([method]) => method !== "actions.compatibilityStamp").map(([method]) => method), ["actions.compatibility"]);
});

test("valid custom skeletons, old bundle dates and patch versions do not report drawing failures", async () => {
  const { mod } = await harness();
  const r = report();
  r.outdated = true;
  const custom = structuredClone(fixture.mod);
  custom.animations = fixture.official.animations;
  r.mod = inputs(custom);
  r.mod.atlases["test.atlas"] = atlas(1, "coat");
  r.mod.skeletons["test.skel"].version = "4.2.120";
  assert.deepEqual(mod.compare(r).issues, []);
  assert.equal(mod.compare(r).remap, false);
});

test("binary and JSON readers identify the actual attachment paths without textures", async () => {
  const { mod } = await harness();
  const binary = readFileSync(new URL("../assets/integrity/synthetic42.skel", import.meta.url));
  const input = { version: "4.2.33", data: binary.toString("base64") };
  assert.deepEqual(Array.from(mod.readSkeleton(input).regions), ["smile"]);
  const r = report(); r.mod.skeletons["test.skel"] = input;
  assert.deepEqual(mod.compare(r).issues, []);
  const data = structuredClone(fixture.official);
  data.skins[0].attachments.face.smile.path = "face/paint";
  assert.deepEqual(mod.readSkeleton(encoded(data)).regions, ["face/paint"]);
});

test("only live missing regions, nonuniform page scales and cross-origin layouts are findings", async () => {
  const { mod } = await harness();
  const r = report();
  r.mod.textures.page = [32, 32];
  r.mod.atlases["test.atlas"] += "unused\n bounds: 40,40,2,2\n";
  assert.deepEqual(mod.compare(r).issues, []);
  r.mod.textures.page = [32, 24];
  assert.deepEqual(mod.compare(r).issues.map((i) => i.key), ["atlasSize"]);
  r.mod = inputs();
  r.mod.atlases["test.atlas"] = atlas(1, "unused");
  assert.deepEqual(mod.compare(r).issues.map((i) => i.key), ["regions"]);
  assert.deepEqual(mod.compare(mixedReport()).issues.map((i) => i.key), ["atlasSize"]);
  r.mod.atlases["test.atlas"] = atlas().replace("64,64", "128,128").replace("1,1,8,8", "2,2,16,16");
  r.mod.textures.page = [128, 128];
  assert.deepEqual(mod.compare(r).issues, []);
});

test("unreadable skeletons and atlases and unavailable originals never report a match", async () => {
  const { mod } = await harness();
  const r = report(); r.unavailable = true;
  r.mod.skeletons["test.skel"] = { version: "4.2.33", data: Buffer.from("broken").toString("base64") };
  r.mod.atlases["test.atlas"] = "";
  assert.deepEqual(mod.compare(r).issues.map((i) => i.key), ["unavailable", "unreadable", "atlasUnreadable"]);
});

test("the card's on-demand check is read only and never offers an in-place Update", async () => {
  const h = await harness("en-US", "ok");
  const r = report(); r.outdated = true; h.use(r);
  await h.mod.check("old-mod", "Painted", () => assert.fail("no in-place update"));
  assert.deepEqual(h.calls, [["actions.compatibility", { mod: "old-mod" }]]);
  assert.equal(h.popups[0].ok, h.strings.picker.ok);
  assert.ok(h.popups[0].lines.every((line) => line !== "Painted"));
  assert.equal(h.mod.canRemap("old-mod"), false);
});

test("compatibility popups omit custom mod and skin names in every locale", async () => {
  for (const locale of ["en-US", "zh-CN", "zh-TW", "ja-JP", "ko-KR", "th-TH"]) {
    const h = await harness(locale, "cancel");
    const alias = "\u8317\u732b";
    await h.mod.check("old-mod", alias);
    assert.deepEqual(h.popups[0].lines, [h.strings.compatibility.matches]);
    assert.ok(!JSON.stringify(h.popups[0]).includes(alias));
    await h.mod.show(report(), { label: "Custom skin" });
    assert.deepEqual(h.popups[1].lines, [h.strings.compatibility.matches]);
  }
});

test("dismissing a mixed-origin size report never prepares a repair", async () => {
  const h = await harness(); h.use(mixedReport());
  await h.mod.check("old-mod");
  assert.equal(h.mod.canRemap("old-mod"), true);
  assert.deepEqual(h.calls, [["actions.compatibility", { mod: "old-mod" }]]);
});

const prepared = () => ({ preview: "review", pages: [{ name: "page", original: "before", repaired: "after" }], compare: { assets: [] },
  report: { partial: true, moved: ["body"], added: ["halo"], dropped: ["old"],
    upscaled: [{ w: 4096, h: 4096 }], warnings: [{ key: "rig", name: "test.skel" }] } });

test("repair previews ask first in every locale; only Accept saves and refreshes", async () => {
  for (const locale of ["en-US", "zh-CN", "zh-TW", "ja-JP", "ko-KR"]) for (const accept of [false, true]) {
    const h = await harness(locale, "ok", accept), preview = prepared();
    h.build(preview); h.use(mixedReport());
    let changed = 0;
    await h.mod.check("old-mod", "Painted", (result) => { assert.equal(result, h.saved); changed++; });
    assert.equal(changed, Number(accept));
    assert.deepEqual(h.calls.map(([method]) => method), ["actions.compatibility", "actions.remap", ...(accept ? ["actions.acceptRemap"] : []), "actions.discardRemap"]);
    assert.deepEqual(h.labels, ["compatibility", "remap", ...(accept ? ["repair"] : [])]
      .map((action) => jobProgress({ action: `progress.action${action[0].toUpperCase()}${action.slice(1)}`, name: "Painted" })));
    assert.deepEqual(h.progress.map(({ label }) => label), ["compatibility", "remap", ...(accept ? ["repair"] : [])]
      .map((action) => jobProgress({ action: `progress.action${action[0].toUpperCase()}${action.slice(1)}`, name: "Painted",
        current: 2, total: 3, step: "import.step.replace" })));
    assert.ok(h.progress.every(({ label }) => !label.includes(".bundle")));
    const [data, title, host, options] = h.comparisons[0];
    assert.deepEqual(data.pages, preview.pages);
    assert.equal(title, "Painted"); assert.equal(host, "dialog");
    assert.equal(options.accept, h.strings.remap.accept);
    assert.equal(options.discard, h.strings.remap.discard);
    assert.equal(options.partial, true);
    const shown = options.lines.map((line) => line.text ?? line);
    assert.ok(shown.some((line) => line.includes("test.skel")));
    assert.ok(shown.every((line) => !/[{}]/.test(line) && !line.startsWith("remap.")));
    assert.equal(h.covers.length, accept ? 2 : 1);
    if (accept) {
      assert.equal(h.mod.canRemap("old-mod"), false);
      assert.equal(h.mod.canRemap("new-mod"), false);
    }
  }
});

test("checks and repair progress use the localized target fallback without a label", async () => {
  for (const locale of ["en-US", "zh-CN", "zh-TW", "ja-JP", "ko-KR"]) {
    const h = await harness(locale, "ok", true);
    h.use(mixedReport()); h.build(prepared());
    await h.mod.check("old-mod");
    assert.deepEqual(h.labels, ["compatibility", "remap", "repair"]
      .map((action) => jobProgress({ action: `progress.action${action[0].toUpperCase()}${action.slice(1)}`, name: h.strings.progress.mods })));
  }
});

test("an unchanged preview says no repair needed and creates no comparison or mod", async () => {
  const h = await harness(); h.build({ unchanged: true });
  await h.mod.remap("old-mod", () => assert.fail("unchanged"));
  assert.deepEqual(h.calls.map(([m]) => m), ["actions.remap"]);
  assert.equal(h.popups[0].lines[0], h.strings.remap.unneeded);
  assert.equal(h.comparisons.length, 0);
});

test("persisted repair markers suppress both original and repaired actions after a fresh check", async () => {
  for (const marker of [{ repaired_from: "original" }, { repaired_id: "repaired" }, { repaired_from: "legacy-remap:orphan" }]) {
    const h = await harness(), r = { ...mixedReport(), ...marker };
    h.use(r); await h.mod.check("stored");
    assert.equal(h.mod.canRemap("stored"), false);
    assert.equal(h.mod.compare(r).remap, false);
    assert.equal(h.mod.eligible(marker), false);
  }
});

test("long missing-region lists keep complete scrollable details in every locale", async () => {
  for (const locale of ["en-US", "zh-CN", "zh-TW", "ja-JP", "ko-KR"]) {
    const h = await harness(locale), r = report(), skeleton = structuredClone(fixture.official);
    const names = Array.from({ length: 50 }, (_, i) => `Intro_Shoulder_L/Intro_Shoulder_L_${String(i).padStart(2, "0")}`);
    skeleton.skins[0].attachments.face = Object.fromEntries(names.map((name) => [name, { width: 2, height: 2 }]));
    r.mod = inputs(skeleton);
    await h.mod.show(r, { onRemap: () => assert.fail("dismissed") });
    const popup = h.popups[0], row = popup.lines.find((line) => line.button);
    assert.equal(popup.scroll, true);
    assert.ok(row.text.includes(names[0]) && row.text.includes(names[2]) && row.text.includes("+47"));
    assert.ok(!row.text.includes(names[3]));
    await row.button.fn();
    assert.equal(h.popups[1].scroll, true);
    for (const name of names) assert.ok(h.popups[1].lines[0].includes(name));
  }
});

test("region references belong to their skeleton's atlas even when names repeat elsewhere", async () => {
  const { mod } = await harness(), r = report();
  const other = structuredClone(fixture.official);
  other.skins[0].attachments.face.smile.path = "coat";
  for (const input of [r.mod, r.official]) {
    input.skeletons["other.skel"] = encoded(other);
    input.atlases["other.atlas"] = atlas(1, "coat") + "smile\n bounds: 40,40,2,2\n";
    input.atlases["test.atlas"] += "coat\n bounds: 20,20,2,2\n";
  }
  r.mod.atlases["other.atlas"] = r.mod.atlases["other.atlas"].replace("40,40,2,2", "50,50,2,2");
  assert.deepEqual(mod.compare(r).issues, []);
  r.mod.atlases["other.atlas"] = r.mod.atlases["other.atlas"].replace("coat\n bounds: 1,1,8,8\n", "");
  assert.ok(mod.compare(r).issues.some(({ key, params }) => key === "regions" && params.names === "coat"));
});

test("custom atlas layouts are valid while missing runtime animations and versions are findings", async () => {
  const { mod } = await harness(), r = report();
  r.mod.atlases["test.atlas"] = atlas(20);
  r.atlasLayout = ["test.atlas"];
  assert.deepEqual(mod.compare(r).issues, []);
  assert.equal(mod.compare(r).remap, false);
  const data = structuredClone(fixture.official);
  delete data.animations.Talk_01;
  r.mod.skeletons["test.skel"] = encoded(data);
  assert.deepEqual(mod.compare(r).issues.map(({ key }) => key), ["missingAnimations"]);
  assert.equal(mod.compare(r).issues[0].params.names, "Talk_01");
  data.skeleton.spine = "4.1.99";
  r.mod.skeletons["test.skel"] = encoded(data);
  assert.ok(mod.compare(r).issues.some(({ key }) => key === "runtimeVersion"));
});

test("every live atlas page needs a texture even without an active attachment", async () => {
  const { mod } = await harness(), r = report();
  r.mod.atlases["test.atlas"] += "\nmissing.png\nsize: 32,32\nunused\n bounds: 1,1,4,4\n";
  const result = mod.compare(r);
  assert.deepEqual(result.issues.map(({ key }) => key), ["atlasSize"]);
  assert.equal(result.issues[0].params.name, "missing.png");
});


test("model checks use bundle integrity without requesting the Spine runtime or atlas remap", async () => {
  const h = await harness(); h.license(false);
  const empty = { skeletons: {}, atlases: {}, textures: {} };
  h.use({ kind: "model", official: empty, mod: empty, remappable: false });
  await h.mod.check("model-mod", "Model", () => assert.fail("no atlas remap"), { kind: "model" });
  assert.deepEqual(h.calls, [["actions.compatibility", { mod: "model-mod" }]]);
  assert.equal(h.confirms(), 0);
  assert.equal(h.mod.eligible({ kind: "model" }), false);
  assert.equal(h.mod.canRemap("model-mod"), false);
  assert.ok(h.popups.some((p) => p.title === h.strings.compatibility.result));
});


test("Update reuses a compatibility report only for the same Mod and unchanged current input stamp", async () => {
  const h = await harness(), count = () => h.calls.filter(([method]) => method === "actions.compatibility").length;
  await h.mod.check("old-mod"); assert.equal(count(), 1);
  assert.deepEqual((await h.mod.forUpdate("old-mod")).official, report().official);
  assert.equal(count(), 1, "Check Compatibility's report is reused");
  h.stamp("inputs-2"); h.use(mixedReport());
  const changed = await h.mod.forUpdate("old-mod");
  assert.ok(h.mod.compare(changed).issues.length); assert.equal(count(), 2);
  await h.mod.forUpdate("another-mod"); assert.equal(count(), 3, "reports never cross Mod identities");
});

test("inputs changed during the check invalidate the report and refuse the update", async () => {
  const h = await harness(); h.afterCheck(() => h.stamp("changed-during-check"));
  await assert.rejects(h.mod.forUpdate("old-mod"), { code: "entry_changed" });
  h.afterCheck(() => {});
  assert.ok(await h.mod.forUpdate("old-mod"));
  assert.equal(h.calls.filter(([method]) => method === "actions.compatibility").length, 2);
});


test("missing runtime, declined consent and runtime load failures remain unavailable instead of integrity findings", async () => {
  const h = await harness(), healthy = report();
  const missing = h.mod.compare(healthy, null);
  assert.equal(missing.available, false);
  assert.deepEqual(missing.issues.map(({ key }) => key), ["unavailable"]);
  assert.equal(missing.author, false); assert.equal(missing.remap, false);
  h.use({ ...mixedReport(), unavailable: true });
  const incomplete = await h.mod.forUpdate("incomplete-mod");
  assert.equal(incomplete.assessment.available, false);
  assert.equal(h.mod.canRemap("incomplete-mod"), false, "incomplete official inputs cannot bypass the update question through remap");
  h.use(healthy);
  for (const availability of [false, "error"]) {
    h.license(availability);
    const checked = await h.mod.forUpdate("healthy-mod");
    assert.equal(checked.assessment.available, false);
    assert.deepEqual(checked.assessment.issues.map(({ key }) => key), ["unavailable"]);
    assert.equal(h.mod.canRemap("healthy-mod"), false);
  }
  h.license(true);
  const checked = await h.mod.forUpdate("healthy-mod");
  assert.equal(checked.assessment.available, true, "a cached report is assessed again when the runtime becomes available");
  assert.deepEqual(checked.assessment.issues, []);
  assert.equal(h.calls.filter(([method, params]) => method === "actions.compatibility" && params.mod === "healthy-mod").length, 1);
});

test("a repack under the Mod's textures and official animations the Mod lacks are findings that offer the repair", async () => {
  const { mod } = await harness(), r = report();
  // nothing differs in what the Mod carries: only the library's record tells the atlas was repacked since
  r.rig = { layout: ["Folder/Test.atlas"], animations: {}, regions: {} };
  let result = mod.compare(r);
  assert.deepEqual(result.issues, [{ key: "atlasLayout", params: { name: "test.atlas" } }]);
  assert.equal(result.remap, true);
  assert.equal(mod.compare({ ...r, remappable: false }).remap, false);
  // the Mod's own skeleton lacks an animation the official one has: the library offers the official rig
  const data = structuredClone(fixture.official);
  delete data.animations.Talk_01;
  const old = report();
  old.mod.skeletons["test.skel"] = encoded(data);
  result = mod.compare(old);
  assert.deepEqual(result.issues.map(({ key }) => key), ["missingAnimations"]);
  assert.equal(result.remap, true);
  assert.equal(mod.compare({ ...old, remappable: false }).remap, false);
});

test("the repair preview counts each kind of region, with the names behind Details, and an update names the animations it lacks", async () => {
  for (const locale of ["en-US", "zh-CN", "zh-TW", "ja-JP", "ko-KR", "th-TH"]) {
    const h = await harness(locale, "ok", false), preview = prepared();
    preview.report.official = ["test.atlas - smile", "test.atlas - 15", "test.atlas - 16", "test.atlas - 17"];
    preview.report.dropped = ["test.atlas - old"]; preview.report.added = ["test.atlas - halo"];
    h.build(preview); h.use(mixedReport());
    await h.mod.check("old-mod", "Painted");
    const lines = h.comparisons[0][3].lines;
    const rows = lines.filter((line) => line.button);
    // moved and unused on the first row, then official, unpainted: a count each, no name on the row
    assert.deepEqual(rows.map((row) => row.text.match(/\d+/)?.[0]), ["1", "4", "1"], locale);
    assert.equal(lines[0], rows[0]);
    assert.ok(rows.every((row) => !/smile|body|old|halo|atlas/.test(row.text) && row.button.label === h.strings.info.details), locale);
    assert.ok(lines.every((line) => !/[{}]/.test(line.text ?? line) && !(line.text ?? line).startsWith("remap.")));
    const before = h.popups.length;
    await rows[0].button.fn();
    assert.ok(h.popups[before].lines[0].includes("old"), locale);  // the unused regions
    await rows[1].button.fn();
    const detail = h.popups[before + 1];
    assert.equal(detail.title, h.strings.info.details);
    assert.ok(["smile", "15", "16", "17"].every((name) => detail.lines[0].includes(name)), locale);
    assert.ok(!detail.lines[0].includes("test.atlas"), locale);  // one atlas: not named
    const notice = h.mod.rigNotice({ rig: { animations: { "a.skel": ["15", "16"], "b.skel": ["16", "17"] } } });
    assert.ok(notice.includes("3") && !/[{}]/.test(notice) && !notice.startsWith("compatibility."), locale);
    assert.equal(h.mod.rigNotice({ rig: {} }), "");
    assert.equal(h.mod.rigNotice({ rig: { regions: { "a.atlas": ["x"] } } }), "");
    assert.equal(h.mod.rigNotice(null), "");
  }
});

test("regions of several atlases keep their atlas in the details", async () => {
  const { mod, popups } = await harness();
  const lines = mod.reviewLines({ moved: [], dropped: [], added: [], upscaled: [], warnings: [],
    official: ["a.atlas - smile", "b.atlas - smile"] });
  await lines.find((line) => line.button).button.fn();
  assert.ok(popups[0].lines[0].includes("a.atlas - smile") && popups[0].lines[0].includes("b.atlas - smile"));
});

test("a portrait the game drew anew is named by the check and offers no remap", async () => {
  const h = await harness("zh-CN", "ok");
  const portrait = () => ({ ...mixedReport(), kind: "spr" });
  h.use(portrait()); h.redraw({ redrawn: true, overlap: 0.52 });
  const checked = await h.mod.inspect("old-mod");
  const redrawn = h.calls.find(([method]) => method === "actions.redrawn");
  assert.deepEqual(redrawn[1].geometry.rest, true);  // the rest poses only
  assert.deepEqual(checked.redrawn, { redrawn: true, overlap: 0.52 });
  assert.deepEqual(checked.assessment.issues.map(({ key }) => key).includes("redrawn"), true);
  assert.equal(checked.assessment.remap, false);
  assert.equal(h.mod.canRemap("old-mod", { kind: "spr" }), false);
  assert.equal(h.strings.compatibility.redrawn, "官方原版立绘已重绘");
  // drawn as before: the remap posts the whole geometry
  h.use(portrait()); h.redraw({ redrawn: false, overlap: 0.99 });
  const fine = await h.mod.inspect("old-mod");
  assert.equal(fine.assessment.remap, true);
  h.build({ unchanged: true });
  await h.mod.remap("old-mod");
  const posted = h.calls.filter(([method]) => method === "actions.remap").at(-1)[1];
  assert.ok(posted.geometry["test.atlas"] && !posted.geometry.rest);
  // other kinds neither ask nor post geometry
  const before = h.calls.length;
  h.use(mixedReport());
  await h.mod.inspect("old-mod");
  assert.ok(!h.calls.slice(before).some(([method]) => method === "actions.redrawn"));
});

test("a portrait the game drew anew keeps the rig its textures were drawn for, unless the official one gained animations", async () => {
  for (const locale of ["zh-CN", "en-US", "zh-TW", "ja-JP", "ko-KR", "th-TH"]) {
    const h = await harness(locale, "ok", true);
    const portrait = (oldRig) => ({ ...mixedReport(), kind: "spr", oldRig });
    h.use(portrait({ atlases: ["test.atlas"], animations: {}, available: true }));
    h.redraw({ redrawn: true, overlap: 0.52 });
    h.build({ preview: "review", compare: { differences: [] }, pages: [], drawings: {},
      report: { rigs: ["test"], moved: [], dropped: [], added: [], official: [], upscaled: [], warnings: [], partial: false } });
    const first = await h.mod.inspect("old-mod");
    assert.equal(first.assessment.oldRig, true, locale);
    assert.equal(first.assessment.remap, false, locale);
    assert.equal(h.mod.canRemap("old-mod", { kind: "spr" }), true, locale);  // Update takes this way
    h.calls.length = 0;
    await h.mod.check("old-mod", "Mod", () => {}, { kind: "spr" });
    assert.equal(h.mod.canRemap("old-mod", { kind: "spr" }), false, locale);  // repaired: its copy is the one now
    const [popup] = h.popups, texts = popup.lines.map((line) => (typeof line === "string" ? line : line.text));
    // plain and left-aligned: what changed as list items, the dotted rule, then what Update does (owner, 2026-11-11)
    assert.equal(popup.title, h.strings.compatibility.result, locale);
    assert.ok(popup.plain && popup.footer === "M", locale);
    assert.equal(texts[0], h.strings.compatibility.changes, locale);
    assert.ok(popup.lines.some((line) => line.text === h.strings.compatibility.redrawn && line.marker), locale);
    const rule = popup.lines.findIndex((line) => line.rule);
    assert.deepEqual(texts.slice(rule + 1), [h.strings.compatibility.autoUpdate, h.strings.compatibility.oldRig], locale);
    assert.ok(popup.lines.at(-1).marker, locale);
    // nothing a user does not act on (owner): the author and CRC hints are gone
    assert.equal(h.strings.compatibility.authorHint, undefined, locale);
    assert.equal(h.strings.compatibility.crcHint, undefined, locale);
    assert.equal(popup.ok, h.strings.remap.action, locale);
    // no geometry: the old rig is set up as a whole
    assert.deepEqual(h.calls.map(([method]) => method),
      ["actions.compatibility", "actions.redrawn", "actions.oldRig", "actions.acceptRemap", "actions.discardRemap"], locale);
    assert.deepEqual(h.calls[2][1], { mod: "old-mod" }, locale);
    const [, , , options] = h.comparisons[0];
    assert.deepEqual(options.lines, [h.strings.remap.oldRig.replace("{names}", "test")], locale);
    assert.ok(!/[{}]/.test(options.lines[0]) && !options.partial, locale);

    // the official skeleton gained animations the game calls: named, and Update offers nothing
    const g = await harness(locale, "ok");
    g.use(portrait({ atlases: ["test.atlas"], animations: { "test.skel": ["11", "12", "13", "14"] }, available: false }));
    g.redraw({ redrawn: true, overlap: 0.52 });
    const gained = await g.mod.inspect("old-mod");
    assert.equal(gained.assessment.oldRig, false, locale);
    assert.equal(g.mod.canRemap("old-mod", { kind: "spr" }), false, locale);
    const issue = gained.assessment.issues.find(({ key }) => key === "oldRigAnimations");
    assert.deepEqual(issue.params, { names: g.mod.compare(gained).issues.find(({ key }) => key === "oldRigAnimations").params.names }, locale);
    assert.ok(["11", "12", "13", "14"].every((name) => issue.params.names.includes(name)), locale);
    await g.mod.show(gained, { onRemap: () => assert.fail("nothing to repair") });
    assert.equal(g.popups[0].ok, g.strings.picker.ok, locale);
    const short = g.mod.compare(gained, globalThis.spine, true).issues.find(({ key }) => key === "oldRigAnimations").params.names;
    const listed = g.popups[0].lines.find((line) => line.text === g.strings.compatibility.oldRigAnimations.replace("{names}", short));
    // the whole list behind Details, a button as high as its line
    assert.ok(listed?.marker && listed.button?.inline, locale);
    // nothing to update: the list of changes alone, no rule
    assert.ok(!g.popups[0].lines.some((line) => line.rule), locale);
    assert.equal(g.popups[0].lines.at(-1), listed, locale);
    // drawn anew without a recorded rig: named as before, nothing offered
    const n = await harness(locale, "ok");
    n.use(portrait({ atlases: [], animations: {}, available: false })); n.redraw({ redrawn: true, overlap: 0.52 });
    const none = await n.mod.inspect("old-mod");
    assert.equal(none.assessment.oldRig, false, locale);
    assert.ok(!none.assessment.issues.some(({ key }) => key === "oldRigAnimations"), locale);
  }
});

test("a mod with its own skeleton the official one outgrew is offered the graft beside the remap, the graft first", async () => {
  for (const locale of ["zh-CN", "en-US", "zh-TW", "ja-JP", "ko-KR", "th-TH"]) {
    const h = await harness(locale, "graft", true);
    const own = { ...mixedReport(), kind: "spr", graft: { kind: "own", animations: { "test.skel": ["17", "18"] }, available: true } };
    h.use(own);
    const rig = (atlas) => ({ atlas, skeleton: "c2tlbA==" });
    h.steps({
      "actions.graft": { token: "t1", rigs: { "test.atlas": { kind: "own", animations: ["17", "18"], mod: rig("mod"), base: null,
        official: rig("official") } } },
      "actions.graftBuild": { rigs: { "test.atlas": { skeleton: "dGVzdA==", atlas: "test", check: { "17": ["face"], "bmpt~0~17": ["face"] },
        official: { "17": ["face"] } } }, refused: {} },
      "actions.graftCheck": { preview: "review", compare: { differences: [] }, pages: [], drawings: {},
        report: { grafted: ["17", "18"], corrected: ["18"], unfixed: [], refused: { "19": ["bone:hat"] }, grown: 132, unanchored: true,
          marks: ["17"], moved: [], dropped: [], added: [], official: [], upscaled: [], warnings: [], partial: true } },
    });
    const checked = await h.mod.inspect("old-mod");
    assert.equal(checked.assessment.graft, true, locale);
    assert.equal(checked.assessment.remap, true, locale);
    assert.deepEqual(h.mod.repairs(checked.assessment).map(({ kind }) => kind), ["graft", "remap"], locale);
    h.calls.length = 0;
    await h.mod.check("old-mod", "Mod", () => {}, { kind: "spr" });
    const [popup] = h.popups, texts = popup.lines.map((line) => (typeof line === "string" ? line : line.text));
    const rule = popup.lines.findIndex((line) => line.rule);
    assert.deepEqual(texts.slice(rule + 1), [h.strings.compatibility.autoUpdate, h.strings.compatibility.graft,
      h.strings.compatibility.remapUpdate], locale);
    // three buttons: Cancel, the remap, and the graft as the primary one
    assert.deepEqual(popup.buttons.map(({ label, value, primary }) => [label, value, !!primary]), [
      [h.strings.popup.cancel, "cancel", false], [h.strings.compatibility.remapAction, "remap", false],
      [h.strings.compatibility.graftAction, "graft", true]], locale);
    assert.deepEqual(h.calls.map(([method]) => method), ["actions.compatibility", "actions.redrawn", "actions.graft",
      "actions.graftBuild", "actions.graftCheck", "actions.acceptRemap", "actions.discardRemap"], locale);
    assert.deepEqual(h.calls[3][1], { token: "t1", geometry: { "test.atlas": { mod: { bones: {}, slotBones: {}, skin: [] },
      official: { bones: {}, slotBones: {}, skin: [] }, base: null } } }, locale);
    assert.deepEqual(h.posed.map(([kind, atlas]) => [kind, atlas]), [["setup", "mod"], ["setup", "official"], ["poses", "test"],
      ["poses", "official"]], locale);
    const [, , , options] = h.comparisons[0];
    const lines = options.lines.map((line) => (typeof line === "string" ? line : line.text));
    assert.deepEqual(lines, [h.strings.remap.grafted.replace("{n}", 2), h.strings.remap.graftCorrected.replace("{n}", 1),
      h.strings.remap.graftRefused.replace("{n}", 1), h.strings.remap.graftGrown.replace("{n}", 132),
      h.strings.remap.graftUnanchored], locale);
    assert.deepEqual(options.expressions, ["17", "18"], locale);
    assert.deepEqual(options.marks, ["17"], locale);
    assert.ok(options.partial, locale);
  }
});

test("a redrawn portrait whose official rig gained expressions is offered its old rig with them grafted", async () => {
  const h = await harness("zh-CN", "ok", false);
  h.use({ ...mixedReport(), kind: "spr", oldRig: { atlases: ["test.atlas"], animations: { "test.skel": ["11"] }, available: false },
    graft: { kind: "oldRig", animations: { "test.skel": ["11"] }, available: true } });
  h.redraw({ redrawn: true, overlap: 0.5 });
  const checked = await h.mod.inspect("old-mod");
  assert.equal(checked.assessment.graft, true);
  assert.equal(checked.assessment.oldRig, false);
  assert.ok(!checked.assessment.issues.some(({ key }) => key === "oldRigAnimations"));
  assert.ok(checked.assessment.issues.some(({ key }) => key === "missingAnimations"));
  assert.deepEqual(h.mod.repairs(checked.assessment).map(({ kind, text }) => [kind, text]),
    [["graft", h.strings.compatibility.graftOldRig]]);
  assert.equal(h.mod.canRemap("old-mod", { kind: "spr" }), true);
});
