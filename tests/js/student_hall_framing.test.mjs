import assert from "node:assert/strict";
import { test } from "node:test";
import { LOBBY_CHARACTER_POS, PORTRAIT_ANCHOR, PORTRAIT_BASE_SCALE, portraitScale, portraitRect } from "../../src/ba_modding_toolkit/web/js/student-hall-framing.js";
import { portraitViewport, stagePoint } from "../../src/ba_modding_toolkit/web/js/portrait-view.js";
import { createDraft } from "../../src/ba_modding_toolkit/web/js/student-hall-model.js";
const close = (actual, expected) => assert.ok(Math.abs(actual - expected) < 1e-7, `${actual} != ${expected}`);
const box = { left: 0, top: 0, width: 1920, height: 1080 };

test("ordinary LobbyCommon portrait projection matches its authored CharPos and the game UI camera", () => {
  const view = portraitViewport(LOBBY_CHARACTER_POS, { origin: [960, 540], s: 1920 / 2960, box });
  close(PORTRAIT_BASE_SCALE, .72);
  close(PORTRAIT_ANCHOR.x, 887.3513513513514); close(PORTRAIT_ANCHOR.y, 1114.7027027027027);
  for (const [x, y] of [[0, 0], [200, 1000], [-600, 1600]]) for (const dpr of [1, 2, 3]) {
    const actual = stagePoint(view, box, x, y, dpr);
    close(actual[0], PORTRAIT_ANCHOR.x + .72 * x); close(actual[1], PORTRAIT_ANCHOR.y - .72 * y);
  }
});
test("different authored student sizes and weapons preserve their proportions rather than fitting to a box", () => {
  const entry = { ...PORTRAIT_ANCHOR, scale: 1 };
  const small = portraitRect(entry, { x: -200, y: 0, width: 400, height: 1200 });
  const tall = portraitRect(entry, { x: -800, y: 0, width: 1600, height: 1600 });
  close(small.height, 864); close(tall.height, 1152); close(tall.width / small.width, 4);
  close(portraitScale(entry), .72);
});
test("intentional stored positions and relative zoom remain untouched while hit and dialogue bounds use the corrected projection", () => {
  const entry = { x: 1400, y: 950, scale: .6 }, before = structuredClone(entry);
  const rect = portraitRect(entry, { x: -100, y: 40, width: 500, height: 1300 });
  close(rect.x, 1356.8); close(rect.y, 371.12); close(rect.width, 216); close(rect.height, 561.6);
  assert.deepEqual(entry, before);
});
test("new entries use the verified lobby anchor with relative scale one", () => {
  const state = createDraft({ revision: 0, mode: "portraits", portraits: [], memorials: [], active: null });
  state.begin("layout");
  const entry = state.add({ variant: "hero", source: "mod:a", kind: "spr" }, () => "first");
  assert.equal(entry.scale, 1); close(entry.x, PORTRAIT_ANCHOR.x); close(entry.y, PORTRAIT_ANCHOR.y);
});

test("horizontal flip reflects asymmetric bounds around the same anchor for hits and dialogue",()=>{
  const entry={x:1000,y:1100,scale:.8},bounds={x:-180,y:35,width:730,height:1450};
  for(const globalScale of [.25,1,1.5]){
    const plain=portraitRect(entry,bounds,globalScale),flipped=portraitRect({...entry,mirror:true},bounds,globalScale);
    close(flipped.x,2*entry.x-plain.x-plain.width);close(flipped.width,plain.width);close(flipped.height,plain.height);close(flipped.y,plain.y);
  }
});

// Geometry comes only from the runtime's public API, using synthetic atlas pages and skeletons.
import { readFileSync } from "node:fs";
import vm from "node:vm";
import { pickPortrait, sampleAlpha, skeletonContains, bindPortraitPointer, VISIBLE_ALPHA, pageAlpha, preparePickPages, visibleHead } from "../../src/ba_modding_toolkit/web/js/student-hall-framing.js";
const runtimeContext = vm.createContext({ console, TextDecoder, TextEncoder, Float32Array, Uint8Array, Uint16Array, Int16Array, Int32Array, ArrayBuffer, DataView });
vm.runInContext(readFileSync(new URL("../../src/ba_modding_toolkit/web/vendor/spine-player-4.2.120/spine-player.min.js", import.meta.url), "utf8"), runtimeContext);
const Runtime = runtimeContext.spine;
function pickActor(id, { blend = "normal", mesh = false, alpha = "ffffffff", slots = 1, weighted = false, rotate = 0 } = {}) {
  const atlas = new Runtime.TextureAtlas("page.png\nsize: 4,2\nbody\n bounds: 0,0,4,2\n rotate: " + rotate + "\n");
  atlas.setTextures({ get: () => ({ getImage: () => ({ width: 4, height: 2 }), setFilters() {}, setWraps() {} }) });
  const attachments = {}, slotData = [];
  for (let i = 0; i < slots; i++) {
    const name = `slot${i}`;
    slotData.push({ name, bone: "root", attachment: "body", blend, color: alpha });
    attachments[name] = { body: mesh ? { type: "mesh", path: "body", uvs: [0, 1, 1, 1, 1, 0, 0, 0], triangles: [0, 1, 2, 2, 3, 0], vertices: weighted ? [1,0,0,0,1,1,0,40,0,1,1,0,40,20,1,1,0,0,20,1] : [0, 0, 40, 0, 40, 20, 0, 20], hull: 4 } : { width: 40, height: 20, x: 20, y: 10 } };
  }
  const data = new Runtime.SkeletonJson(new Runtime.AtlasAttachmentLoader(atlas)).readSkeletonData({ skeleton: { spine: "4.2.33" }, bones: [{ name: "root" }], slots: slotData, skins: [{ name: "default", attachments }] });
  const skeleton = new Runtime.Skeleton(data); skeleton.updateWorldTransform(Runtime.Physics.update);
  return { entry: { id }, player: { skeleton } };
}
const opaquePage = { width: 4, height: 2, alpha: new Uint8Array(8).fill(255) };
const halfPage = { width: 4, height: 2, alpha: new Uint8Array([0, 0, 255, 255, 0, 0, 255, 255]) };
const clearPage = { width: 4, height: 2, alpha: new Uint8Array(8) };
test("front-most visible portrait wins; transparent pixels, non-Normal slots and inactive bones pass through", () => {
  const back = pickActor("back"), front = pickActor("front", { mesh: true });
  const actors = new Map([["back", back], ["front", front]]), texture = front.player.skeleton.slots[0].getAttachment().region.texture;
  const pages = t => t === texture ? halfPage : opaquePage;
  assert.equal(pickPortrait(actors, 35, 1070, Runtime, pages), "front");
  assert.equal(pickPortrait(actors, 5, 1070, Runtime, pages), "back");
  for (const blend of [1, 2, 3]) { front.player.skeleton.slots[0].data.blendMode = blend; assert.equal(pickPortrait(actors, 35, 1070, Runtime, pages), "back"); }
  front.player.skeleton.slots[0].data.blendMode = 0; front.player.skeleton.bones[0].active = false;
  assert.equal(pickPortrait(actors, 35, 1070, Runtime, pages), "back");
  assert.equal(pickPortrait(actors, 100, 100, Runtime, pages), null);
});
test("pick alpha includes slot, skeleton and attachment opacity with a linear page sample at the .1 threshold", () => {
  const actor = pickActor("a"), skeleton = actor.player.skeleton;
  assert.equal(VISIBLE_ALPHA, .1);
  skeleton.color.a = .5; skeleton.slots[0].color.a = .5; skeleton.slots[0].getAttachment().color.a = .4;
  assert.ok(skeletonContains(skeleton, 30, 10, Runtime, () => opaquePage));
  skeleton.slots[0].getAttachment().color.a = .399;
  assert.equal(skeletonContains(skeleton, 30, 10, Runtime, () => opaquePage), false);
  assert.equal(sampleAlpha(halfPage, .5, .5), .5); assert.equal(sampleAlpha(halfPage, 0, 0), 0);
});
test("posed public vertices apply both size multipliers, mirroring and mesh deformation", () => {
  for (const mesh of [false, true]) for (const mirror of [false, true]) for (const scale of [.5, 2]) for (const global of [.25, 1.5]) {
    const { player: { skeleton } } = pickActor("a", { mesh });
    skeleton.x = 900; skeleton.y = 200; skeleton.scaleY = portraitScale({ scale }, global); skeleton.scaleX = skeleton.scaleY * (mirror ? -1 : 1);
    if (mesh) skeleton.slots[0].deform = [10, 0, 50, 0, 50, 20, 10, 20];
    skeleton.updateWorldTransform(Runtime.Physics.update);
    assert.ok(skeletonContains(skeleton, 900 + (mirror ? -1 : 1) * 30 * skeleton.scaleY, 200 + 10 * skeleton.scaleY, Runtime, () => opaquePage));
    assert.equal(skeletonContains(skeleton, 900 + (mirror ? 1 : -1) * 30 * skeleton.scaleY, 200 + 10 * skeleton.scaleY, Runtime, () => opaquePage), false);
  }
});
test("five-portrait worst-case synthetic hover timing (500 posed mesh slots, all transparent)", t => {
  const actors = new Map(Array.from({ length: 5 }, (_, i) => [i, pickActor(String(i), { mesh: true, slots: 100 })]));
  const samples = [];
  for (let i = 0; i < 1200; i++) { const start = performance.now(); assert.equal(pickPortrait(actors, 20, 1070, Runtime, () => clearPage), null); if (i >= 200) samples.push(performance.now() - start); }
  samples.sort((a, b) => a - b); const p95 = samples[Math.floor(samples.length * .95)];
  t.diagnostic(`five portraits / 500 mesh slots / 1000 measured queries: median=${samples[500].toFixed(3)}ms p95=${p95.toFixed(3)}ms max=${samples.at(-1).toFixed(3)}ms`);
  assert.ok(p95 < 16, `p95 ${p95}ms exceeds a frame`);
});
test("one pointer surface routes hover, press and drag by the same pixel query and stage coordinates", () => {
  const events = {}, surface = { addEventListener: (n, f) => events[n] = f, setPointerCapture() {} }, calls = [], queries = [];
  bindPortraitPointer(surface, { root: { getBoundingClientRect: () => ({ left: 100, top: 50, width: 960 }) }, pick: (x, y) => { queries.push([x, y]); return x < 100 ? "back" : "front"; }, hover: id => calls.push(["hover", id]), down: h => calls.push(["down", h.id]), move: (h, p) => calls.push(["move", h.id, p.x]), end: (h, p) => calls.push(["end", h.id, p]) });
  events.pointermove({ clientX: 120, clientY: 70 }); events.pointerdown({ button: 0, clientX: 120, clientY: 70, pointerId: 1, preventDefault() {} });
  events.pointermove({ clientX: 200, clientY: 70, buttons: 1 }); events.pointercancel({ pointerId: 1 }); events.pointermove({ clientX: 200, clientY: 70 });
  assert.deepEqual(queries, [[40, 40], [40, 40], [200, 40]]); assert.deepEqual(calls.map(e => e.slice(0, 2)), [["hover", "back"], ["down", "back"], ["move", "back"], ["end", "back"], ["hover", "front"]]);
});

test("weighted meshes use the runtime pose, and atlas rotation is already present in the public UVs",()=>{
  const weighted=pickActor("weighted",{mesh:true,weighted:true}).player.skeleton;
  weighted.bones[0].x=100;weighted.bones[0].y=80;weighted.updateWorldTransform(Runtime.Physics.update);
  assert.ok(skeletonContains(weighted,120,90,Runtime,()=>opaquePage));assert.equal(skeletonContains(weighted,20,10,Runtime,()=>opaquePage),false);
  for(const rotate of [0,90,180,270]){
    const skeleton=pickActor("rotate",{rotate}).player.skeleton,a=skeleton.slots[0].getAttachment(),world=new Float32Array(8);a.computeWorldVertices(skeleton.slots[0],world,0,2);
    for(const weights of [[.8,.1,.1],[.1,.8,.1],[.1,.1,.8]]){
      const mix=values=>weights.reduce((sum,w,i)=>sum+w*values[i*2],0),x=mix(world),y=mix(world.subarray(1)),u=mix(a.uvs),v=mix(a.uvs.subarray(1));
      assert.equal(skeletonContains(skeleton,x,y,Runtime,()=>halfPage),sampleAlpha(halfPage,u,v)>=VISIBLE_ALPHA);
    }
  }
});
test("page alpha is decoded once at source readiness and picking reuses its cache",()=>{
  const previous=globalThis.document;let decodes=0,reads=0;
  globalThis.document={createElement:()=>({getContext:()=>({drawImage(){decodes++;},getImageData:()=>({data:new Uint8Array([1,2,3,25,4,5,6,26])})})})};
  const texture={getImage(){reads++;return{width:2,height:1};}},data={skins:[{getAttachments:()=>[{attachment:{region:{texture}}},{attachment:{region:{texture}}}]}]};
  try{preparePickPages(data);assert.deepEqual([...pageAlpha(texture).alpha],[25,26]);assert.equal(pageAlpha(texture),pageAlpha(texture));assert.equal(decodes,1);assert.equal(reads,1);}finally{globalThis.document=previous;}
});

test("the visible head ignores Spine's bounds: transparent props, additive halos and hidden slots never move it", () => {
  const atlas = new Runtime.TextureAtlas("page.png\nsize: 4,2\nbody\n bounds: 0,0,4,2\n\nclear.png\nsize: 4,2\nprop\n bounds: 0,0,4,2\n");
  const textures = new Map();
  atlas.setTextures({ get: (path) => { if (!textures.has(path)) textures.set(path, { path, getImage: () => ({ width: 4, height: 2 }), setFilters() {}, setWraps() {} }); return textures.get(path); } });
  const region = (path, width, height, x, y) => ({ path, width, height, x, y });
  const slots = [["body", "normal", "ffffffff"], ["head", "normal", "ffffffff"], ["halo", "additive", "ffffffff"], ["prop", "normal", "ffffffff"], ["faded", "normal", "ffffff10"]];
  const attachments = { body: { body: region("body", 400, 600, 200, 300) }, head: { head: region("body", 100, 100, 300, 650) },
    halo: { halo: region("body", 100, 60, 300, 780) }, prop: { prop: region("prop", 200, 900, 900, 450) }, faded: { faded: region("body", 100, 400, -300, 900) } };
  const data = new Runtime.SkeletonJson(new Runtime.AtlasAttachmentLoader(atlas)).readSkeletonData({ skeleton: { spine: "4.2.33" }, bones: [{ name: "root" }],
    slots: slots.map(([name, blend, color]) => ({ name, bone: "root", attachment: name, blend, color })), skins: [{ name: "default", attachments }] });
  const skeleton = new Runtime.Skeleton(data); skeleton.updateWorldTransform(Runtime.Physics.update);
  const pages = (texture) => texture.path === "clear.png" ? clearPage : opaquePage;
  const offset = new Runtime.Vector2(), size = new Runtime.Vector2(); skeleton.getBounds(offset, size, []);
  assert.ok(offset.x < -300 && offset.x + size.x > 900, "Spine's bounds also count the transparent prop and the faded slot");
  let head = visibleHead(skeleton, Runtime, pages);
  assert.ok(Math.abs(head.x - 300) <= 8, `centre ${head.x}`); assert.ok(head.y <= 700 && head.y > 692, `top ${head.y}`);
  skeleton.scaleX = -1; skeleton.updateWorldTransform(Runtime.Physics.update); head = visibleHead(skeleton, Runtime, pages);
  assert.ok(Math.abs(head.x + 300) <= 8, "mirrored"); skeleton.scaleX = 1;
  skeleton.bones[0].active = false; assert.equal(visibleHead(skeleton, Runtime, pages), null);
});
