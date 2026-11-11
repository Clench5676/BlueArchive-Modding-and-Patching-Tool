import assert from "node:assert/strict";
import { test } from "node:test";
import { readFile } from "node:fs/promises";

import { CONTROL, XS_BUTTON, SPACE } from "../../src/bmpt/web/js/ui-tokens.js";
const S = 1920 / 2960;
const near = (a,b) => assert.ok(Math.abs(a-b)<1e-9, `${a} != ${b}`);
const node = (name, children = [], widgets = [], more = {}) => ({ name, children, widgets, pos: [0, 0], scale: [1, 1], active: true, ...more });
const sprite = (name, w, h, color, depth = 3) => ({ cls: "UISprite", atlas: "Common", sprite: name, w_: w, h_: h, color, depth, type: 1 });
// Derived metadata from UILobby's authored enabled loop and duty-selector controls.
const bg = node("Bg", [], [sprite("Common_Bg_Raius5px_Shadow", 156, 104, "#ffffff/0.92", 2)]);
const loop = node("Able_Count", [bg, node("Icon", [], [sprite("Common_Icon_Change_Count", 116, 84, "#466399/1.00")], { pos: [3, 8], scale: [.9, .9] }), node("Label_SpineCount", [], [{ text: "9/9", color: "#466399/1.00" }])]);
const duty = (name, tint) => node(name, [node("Icon", [], [sprite("Common_Icon_SpecialLobby", 68, 68, tint, 5)])]);
const lobby = node("UILobby", [node("Front", [node("Right", [node("Btn_RotateSpine", [node("ConditionRoot", [loop])]), node("Btn_Screen_On", [structuredClone(bg),node("Icon",[],[sprite("Common_UIHide_Icon",75,63,"#466399/1.00")],{pos:[3,8],scale:[.85,.85]})])]), node("NUITop", [node("Top", [node("Right", [node("Btn_LobbyCharacter", [duty("Able", "#466399/1.00"), duty("Disable", "#2f3c52/1.00")])])])])])]);
function find(tree, path) { return path.split("/").reduce((n, name) => n.children.find((c) => c.name === name), tree); }
let rendered, wired = [], drawnIcons = new Map(), alignedIcons = new Map();
globalThis.hallControlsHarness = { S, clone: structuredClone, NGUI: { find, fitGroup: captions => captions.map(text => ({text, fs:48})), node: (name,pos,widgets,children) => node(name,children,widgets,{pos}), async render(tree) {
    rendered = tree;
    const nodes = new Map();
    if (tree.name.startsWith("Portrait")) {
      const entry = { node: find(tree, "Icon"), els: [{ style: { transform: "scale(1, 1)" } }] };
      nodes.set(`${tree.name}/Icon`, entry); drawnIcons.set(tree.name, entry);
    }
    return { nodes };
  } },
  async alignIcon(view,path,backgroundPath,options){
    alignedIcons.set(path,options);const entry=view.nodes.get(path); if(!entry)return;
    Object.assign(entry.els[0].style,{transform:options.rotation?`rotate(${-options.rotation}deg)`:"",transformOrigin:"50% 50%"});
  },
  text(tree, path, value, options) { Object.assign(find(tree, path).widgets[0], options, {text:value}); },
  UI: { retainedButton: async () => {}, button: async () => {}, scaleControl: async () => {} }, T: (key) => globalThis.hallControlsHarness.strings ? key.split(".").reduce((v,k)=>v[k],globalThis.hallControlsHarness.strings) : `localized:${key}`, Popup: { hit(host, view, path, fn, meta) { const b = { path, fn, dataset: {}, ...meta }; wired.push(b); return b; } },
  document: { createElement: () => ({ style: {}, children: [], append(child) { this.children.push(child); } }), querySelectorAll: () => [] }, lobby };
let source = await readFile(new URL("../../src/bmpt/web/js/student-hall.js", import.meta.url), "utf8");
source = source.replace(/^import .*;$/gm, "");
source = `import { CONTROL, XS_BUTTON, SPACE } from "${new URL("../../src/bmpt/web/js/ui-tokens.js", import.meta.url).href}";\nimport { row } from "${new URL("../../src/bmpt/web/js/ui-grid.js", import.meta.url).href}";\n` + source;
source = `import { portraitReplacements } from "${new URL("../../src/bmpt/web/js/student-hall-model.js", import.meta.url).href}";\nimport { portraitRect, portraitToolbarLayout, PORTRAIT_SIZE } from "${new URL("../../src/bmpt/web/js/student-hall-framing.js", import.meta.url).href}";\nimport { bindCorner } from "${new URL("../../src/bmpt/web/js/corner-layout.js", import.meta.url).href}";\n` + source;
source = `const {S,clone,NGUI,alignIcon,text,T,Popup,document,UI}=globalThis.hallControlsHarness;\n${source}\nexport {lobbyControl,controls,renderChrome,renderPortraitActions};
export function setPortraits(value){portraits=value;}
export function fixture(mode,kind=null,writes=0,portraits=[],choices=[]){options={choices};layouts={lobby:globalThis.hallControlsHarness.lobby};store={current:()=>({mode,memorials:[],portraits}),kind:()=>kind};root={getBoundingClientRect:()=>({left:0,top:0,width:1920,height:1080})};chrome={replaceChildren(){},querySelectorAll:()=>[]};hits={querySelector:()=>null};pendingWrites=writes;hitGeneration=0;}
//# sourceURL=hall-controls-fixture.js`;
const Hall = await import(`data:text/javascript;base64,${Buffer.from(source).toString("base64")}`);

test("approved icon-only actions preserve the native background and normalize visual size and vertical center", () => {
  Hall.fixture("portraits");
  for (const [name, expected, width, height] of [["Layout", "Cafe_Icon_NameEdit", 50, 50], ["Background", "Cafe_Icon_Photo", 120, 104], ["Adjust", "Common_Icon_SpecialLobby", 68, 68], ["Add", "Common_Icon_SpecialLobby", 68, 68], ["Mode", "Common_Icon_Change_Count", 116, 84], ["Fullscreen", "Common_UIHide_Icon", 75, 63]]) {
    const tree = Hall.lobbyControl(name, "Unused caption", 1400, 156*S);
    assert.deepEqual(tree.children.map((c) => c.name), ["Bg", "Icon"]);
    assert.deepEqual(find(tree, "Bg"), bg);
    const icon = find(tree, "Icon"); assert.equal(icon.widgets[0].sprite, expected);
    assert.equal(Math.max(icon.widgets[0].w_, icon.widgets[0].h_), 76);
    assert.ok(Math.abs(icon.widgets[0].w_ / icon.widgets[0].h_ - width / height) < 1e-10);
    assert.equal(icon.widgets[0].color, "#466399/1.00");
    assert.deepEqual(icon.scale, [1,1]); assert.deepEqual(icon.pos, [0,0]);
    assert.equal(Hall.lobbyControl(name, "", 1400, 156*S, false).children[1].widgets[0].color, "#2f3c52/1.00");
  }
  assert.equal(find(lobby, "Front/Right/Btn_RotateSpine/ConditionRoot/Able_Count/Label_SpineCount").widgets[0].text, "9/9");
});

test("normal and edit actions pack immediately left of the right-aligned numberless loop", () => {
  for (const [mode, kind] of [["portraits", null], ["portraits", "layout"], ["memorial", null]]) {
    Hall.fixture(mode, kind); const all=Hall.controls(), actions=all.filter((action)=>!action[6]);
    assert.equal(all.some((action)=>action[0]==="Remove"),false);
    const text=all.filter((action)=>action[6]);
    assert.deepEqual(text.map((action)=>action[0]),kind==="layout"?["Cancel","Save"]:[]);
    assert.ok(text.every((action)=>action[6]===230));
    if(text.length){assert.equal(text[0][3],text[1][3]);near(text[0][2],actions[0][2]);near(text[1][2]+text[1][3],1875);near(text[1][2]-text[0][2]-text[0][3],SPACE.m*S);}
    assert.equal(actions.at(-1)[0], "Fullscreen"); near(actions.at(-1)[2]+actions.at(-1)[3], 1875);
    for (let i=0; i<actions.length-1; i++) near(actions[i+1][2]-(actions[i][2]+actions[i][3]),(actions[i+1][0]==="Mode"?SPACE.l:SPACE.m)*S);
    for (const [name,,x,width] of actions) if (["Layout","Background","Adjust","Add","Mode","Fullscreen"].includes(name)) assert.equal(width,156*S);
  }
});

test("icon actions retain localized accessible names and disable during writes", async () => {
  Hall.fixture("portraits", null, 1); wired=[];await Hall.renderChrome();
  assert.deepEqual(wired.map((b)=>b.name), ["localized:hall.changeLayout","localized:hall.background","localized:hall.memorial","localized:lobby.fullscreen"]);
  assert.ok(wired.every((b)=>b.disabled));
  assert.ok(rendered.children.every((c)=>c.children[1].widgets[0].color==="#2f3c52/1.00"));
  Hall.fixture("portraits", "layout"); wired=[];await Hall.renderChrome();
  assert.ok(wired.some((b)=>b.name==="localized:hall.addPortraits"));
  assert.equal(find(rendered,"Save/Label_SpineCount").widgets[0].text,"localized:popup.save");
  assert.equal(find(rendered,"Cancel/Label_SpineCount").widgets[0].text,"localized:popup.cancel");
});

test("rendered layer arrows rotate in screen coordinates while retaining centered native sprite geometry", async()=>{
  const entries=[{id:"a"},{id:"b"},{id:"c"}];Hall.fixture("portraits","layout",0,entries);drawnIcons=new Map();wired=[];
  const host={style:{},replaceChildren(){}};
  await Hall.renderPortraitActions(host,entries[1],{x:700,y:400,width:500},0);
  for(const [name,rotation] of [["Backward",-180],["Forward",0],["Front",-90],["Back",-270]]){
    const icon=drawnIcons.get(`Portrait${name}`),style=icon.els[0].style,widget=icon.node.widgets[0];
    assert.equal(style.transform,rotation ? `rotate(${rotation}deg)` : "");
    if(rotation)assert.equal(style.transformOrigin,"50% 50%");
    assert.deepEqual(icon.node.pos,[0,0]);assert.deepEqual(icon.node.scale,[1,1]);
    assert.ok(Math.abs(Math.max(widget.w_,widget.h_)-56)<1e-10);
  }
  assert.equal(wired.length,7);assert.ok(wired.filter(button=>button.action!=="hall-portrait-change").every(button=>!button.disabled));assert.equal(wired.find(button=>button.action==="hall-portrait-change").disabled,true);
});

test("top-right icons keep the75percent baseline with the photo-only8percent optical adjustment",async()=>{
  Hall.fixture("portraits","layout");alignedIcons=new Map();await Hall.renderChrome();
  assert.equal(alignedIcons.get("Hall/Add/Icon").height,64*S*.75);
  assert.equal(alignedIcons.get("Hall/Background/Icon").height,48*S*1.08);assert.equal(alignedIcons.get("Hall/Mode/Icon").height,48*S);
  Hall.fixture("memorial");await Hall.renderChrome();assert.equal(alignedIcons.get("Hall/Adjust/Icon").height,64*S*.75);
  Hall.fixture("portraits");await Hall.renderChrome();assert.equal(alignedIcons.get("Hall/Layout/Icon").height,48*S);
  for(const name of ["Add","Adjust","Layout","Background"]){assert.equal(alignedIcons.get(`Hall/${name}/Icon`).center,"bounds");assert.equal(alignedIcons.get(`Hall/${name}/Icon`).alphaCutoff,32);}
  assert.equal(alignedIcons.get("Hall/Mode/Icon").center,undefined);assert.equal(alignedIcons.get("Hall/Fullscreen/Icon").height,48*S);
});

test("Hall's real action row measures its top margin below the bar at minimum, default and large sizes", async () => {
  const { CORNER_MARGIN } = await import('../../src/bmpt/web/js/corner-layout.js');
  const { headerGeometry } = await import('../../src/bmpt/web/js/window-layout.js');
  for (const mode of ['portraits', 'memorial']) for (const platform of [null, 'darwin', 'win32']) {
    for (const [width, height] of [[960, 540], [1600, 900], [2560, 1440], [2560, 1080], [960, 1200]]) {
      const header = platform ? headerGeometry(width, height, platform === 'win32' ? 40 : 0) : null;
      const k = header?.contentScale || Math.min(width / 1920, height / 1080), stageTop = (height - 1080 * k + (header?.extra || 0)) / 2;
      const barBottom = header?.rowHeight || 64 * k;
      globalThis.document = { getElementById: () => ({ getBoundingClientRect: () => ({ left: (width - 1920 * k) / 2, top: stageTop, width: 1920 * k }) }),
        querySelector: () => ({ getBoundingClientRect: () => ({ bottom: barBottom }) }) };
      Object.assign(globalThis, { innerWidth: width, innerHeight: height, bamtHeaderGeometry: header,
        bamtWindowChrome: platform ? { active: true, platform, bounds: platform === 'darwin'
          ? { left: 12, top: 8, right: 80, bottom: 30 } : { left: width - 148, top: 8, right: width, bottom: 40 } } : null });
      Hall.fixture(mode); const destination = await Hall.renderChrome({ publish: false });
      const [dx, dy] = destination.children[0].style.translate.split(' ').map(parseFloat);
      const near = (a, b) => assert.ok(Math.abs(a - b) < 1e-7, `${a} != ${b}`);
      near(stageTop + (145 - 104 * S / 2 + dy) * k - barBottom, CORNER_MARGIN * k);
      near(width - ((width - 1920 * k) / 2 + (1875 + dx) * k), CORNER_MARGIN * k);
      assert.ok(rendered.children.every(node => node.children[0].widgets[0].h_ === 104));
    }
  }
});

test("the Common ChangeUnit portrait icon keeps its77x68ratio and enables only with a replacement candidate",async()=>{
  const entry={id:"a",variant:"hero",source:"mod:current",scale:1},choices=[{...entry,family:"hero",kind:"spr"},{variant:"alt",source:"official",family:"hero",kind:"spr"}];
  for(const available of [[],choices]){
    Hall.fixture("portraits","layout",0,[entry],available);wired=[];await Hall.renderPortraitActions({style:{},replaceChildren(){}},entry,{x:900,y:400,width:400,height:700},0);
    const hit=wired.find(b=>b.action==="hall-portrait-change"),widget=drawnIcons.get("PortraitChange").node.widgets[0];
    assert.equal(widget.atlas,"Common");assert.equal(widget.sprite,"Campaign_Icon_ChangeUnit");assert.ok(Math.abs(widget.w_/widget.h_-77/68)<1e-12);
    assert.equal(hit.disabled,available.length===0);assert.equal(hit.name,"localized:hall.changePortrait");
  }
});

test("the own-size slider moves the toolbar only once a size is set, never under a dragged thumb",async()=>{
  const { portraitRect, portraitToolbarLayout } = await import("../../src/bmpt/web/js/student-hall-framing.js");
  const entry={id:"a",variant:"hero",source:"mod:current",x:400,y:1100,scale:1,mirror:false},ui=globalThis.hallControlsHarness.UI,saved={...ui};
  let change,reset,listeners={};
  ui.scaleControl=async(host,value,fn,options)=>{assert.equal(value,1);assert.equal(options.limits.min,.5);change=fn;return {range:{addEventListener:(name,f)=>{listeners[name]=f;}},reset:()=>fn(1)};};
  ui.button=async(host,name,caption,box,fn)=>{if(name==="HallPortraitScaleReset")reset=fn;};
  try{
    Hall.fixture("portraits","layout",0,[entry],[]);wired=[];
    const host={style:{},replaceChildren(){}},rect=portraitRect(entry,null);
    await Hall.renderPortraitActions(host,entry,rect,0);
    const count=wired.length,start={...host.style},expected=(scale)=>portraitToolbarLayout(portraitRect({...entry,scale},null),count);
    assert.equal(start.left,`${expected(1).x}px`);assert.equal(start.top,`${expected(1).y}px`);
    change(1.8);assert.equal(entry.scale,1.8);assert.deepEqual(host.style,start,"input alone never moves the toolbar");
    assert.notEqual(expected(1.8).y,expected(1).y,"the fixture really moves the toolbar");
    listeners.change();assert.equal(host.style.left,`${expected(1.8).x}px`);assert.equal(host.style.top,`${expected(1.8).y}px`);
    reset();assert.equal(entry.scale,1);assert.equal(host.style.left,`${expected(1).x}px`);assert.equal(host.style.top,`${expected(1).y}px`);
  }finally{Object.assign(ui,saved);}
});

test("the toolbar centres on the portrait's visible head, not its Spine bounds",async()=>{
  const entry={id:"a",variant:"hero",source:"mod:current",x:700,y:1100,scale:1,mirror:false};
  Hall.fixture("portraits","layout",0,[entry],[]);Hall.setPortraits({actors:new Map(),head:(id)=>id==="a"?{x:-150,y:-600}:null});
  try{
    const host={style:{},replaceChildren(){}};await Hall.renderPortraitActions(host,entry,{x:0,y:0,width:1900,height:1080},0);
    const [x,y,w,h]=["left","top","width","height"].map(k=>parseFloat(host.style[k]));
    assert.equal(x+w/2,700-150,"centred on the head, not on the wide box");assert.equal(y+h,1100-600,"touching the head");
  }finally{Hall.setPortraits(null);}
});

for(const locale of ["en-US","zh-CN","zh-TW","ja-JP","ko-KR","th-TH"]) test(`Hall toolbar bodies, scaled gaps and Cancel/Save fitting in ${locale}`,async()=>{
  const ctx={font:"",measureText(value){const fs=parseFloat(/([\d.]+)px/.exec(this.font)?.[1]||16);return {width:[...value].reduce((w,c)=>w+fs*(/[\u2e80-\u9fff\u3000-\u30ff\uff00-\uffef\uac00-\ud7af\u0e00-\u0e7f]/.test(c)?1:.55),0),actualBoundingBoxAscent:.75*fs,actualBoundingBoxDescent:.2*fs,fontBoundingBoxAscent:1.16*fs,fontBoundingBoxDescent:.29*fs};}};
  globalThis.document={documentElement:{lang:locale,appendChild(){}},createElement:()=>({getContext:()=>ctx,remove(){}})};
  globalThis.getComputedStyle=()=>({getPropertyValue:()=>""});
  const real=await import("../../src/bmpt/web/js/ngui.js"),fixture=globalThis.hallControlsHarness,previous=fixture.NGUI.fitGroup;
  fixture.NGUI.fitGroup=real.fitGroup;fixture.strings=JSON.parse(await readFile(new URL(`../../src/bmpt/locales/${locale}.json`,import.meta.url),"utf8")).webui;
  try{
    Hall.fixture("portraits","layout");await Hall.renderChrome();
    const actions=Hall.controls(),icons=actions.filter(a=>!a[6]),pair=actions.filter(a=>a[6]);
    assert.equal(CONTROL.toolbar.h,104);
    for(const action of icons){const tree=find(rendered,action[0]),body=find(tree,"Bg").widgets[0];assert.deepEqual([body.w_,body.h_],[156,CONTROL.toolbar.h]);}
    icons.slice(1).forEach((a,i)=>near(a[2]-icons[i][2]-icons[i][3],(a[0]==="Mode"?SPACE.l:SPACE.m)*S));
    near(pair[1][2]-pair[0][2]-pair[0][3],SPACE.m*S);
    for(const viewport of [960,1600,2560])for(const density of [1,2]){
      const k=viewport/1920*density;
      for(const action of actions){const body=find(rendered,`${action[0]}/Bg`).widgets[0];assert.ok(action[2]*k>=0&&(action[2]+action[3])*k<=viewport*density);near(body.h_*S*k,(action[6]?CONTROL.buttonXS.h:CONTROL.toolbar.h)*S*k);}
      near((pair[1][2]-pair[0][2]-pair[0][3])*k,SPACE.m*S*k);
    }
    const labels=pair.map(([name])=>find(rendered,`${name}/Label_SpineCount`).widgets[0]);
    assert.equal(new Set(labels.map(w=>w.fontSize)).size,1);
    for(let i=0;i<pair.length;i++){
      const [name,key,,width]=pair[i],body=find(rendered,`${name}/Bg`).widgets[0],label=labels[i];
      assert.equal(body.h_,CONTROL.buttonXS.h);assert.ok(label.fontSize>=XS_BUTTON.fsMin&&label.fontSize<=CONTROL.buttonXS.fs);
      assert.equal(label.text.replace(/\s/g,""),fixture.T(key).replace(/\s/g,""));
      for(const line of label.text.split("\n"))assert.ok(real.glyphWidth(line,label.fontSize)<=width/S-2*CONTROL.buttonXS.pad+1e-9);
      assert.ok(label.text.split("\n").length*real.fontSizeFor(label.text,label.fontSize)*real.LINE_PITCH<=label.h_+1e-9);
    }
  }finally{fixture.NGUI.fitGroup=previous;delete fixture.strings;}
});
