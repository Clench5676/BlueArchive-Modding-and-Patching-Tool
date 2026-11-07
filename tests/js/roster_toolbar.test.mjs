import assert from "node:assert/strict";
import { test } from "node:test";
import { readFile } from "node:fs/promises";
import { readFileSync } from "node:fs";
import { emptyBlock } from "../../src/bmpt/web/js/empty-block.js";
import { CONTROL, SPACE } from "../../src/bmpt/web/js/ui-tokens.js";
import { row } from "../../src/bmpt/web/js/ui-grid.js";
const text={"roster.title":"Student List","roster.merge":"Group Alts","seg.all":"All","seg.mods":"Modded","seg.noMods":"No Mod","seg.recruit":"Playable","seg.noRecruit":"NPC","sort.recent":"Recently Viewed","sort.added":"Recently Added","sort.name":"Name","sort.mods":"Mod Count","sort.default":"Default"};
const measure=(value,size)=>globalThis.toolbarFixture?.real?.glyphWidth(value,size)??value.length*size*.45;
const fit=(value,size,min,width)=>({text:value,fs:Math.max(min,Math.min(size,width/(value.length*.45))),lines:1});
let source=await readFile(new URL("../../src/bmpt/web/js/roster-widgets.js",import.meta.url),"utf8");source=source.replace(/^import .*;$/gm,"");
// ink as the page font draws it: 0.72 em above the baseline, 0.2 below for a letter with a descender, in a box 1.15 em a line
const ink=(str,px)=>{const lines=String(str).split("\n"),span=(lines.length-1)*px*1.15,ascent=.72*px,descent=/[gjpqy]/.test(lines.at(-1))?.2*px:0;
  const shift=(ascent-descent-1.16*px+.29*px)/2,half=(ascent+descent+span)/2;return{top:-shift-half,bottom:-shift+half};};
globalThis.toolbarFixture={CONTROL,SPACE,row,S:1920/2960,emptyBlock,clone:structuredClone,filtering:()=>false,NGUI:{find:(tree,path)=>path.split("/").reduce((n,key)=>n.children.find(c=>c.name===key),tree),node:(name,pos,widgets)=>({name,pos,widgets,children:[]}),label:(text,fontSize,color,w_,h_,extra)=>({text,fontSize,color,w_,h_,...extra}),inkBand:ink,fontSizeFor:(str,fs)=>fs,fitText:(...args)=>globalThis.toolbarFixture.real?.fitText(...args)??fit(...args),fitGroup:(values,size,min,width)=>{const fits=values.map(value=>fit(value,size,min,width)),fs=Math.min(...fits.map(f=>f.fs));return fits.map(f=>({...f,fs}));}},T:key=>(globalThis.toolbarFixture.table||text)[key]||key,advanceOf:measure,glyphsOf:measure,SORTS:["recent","added","name","mods","default"]};
const {toolbarLayout,toolbarTree,toggleHits,emptyListY,EMPTY_LIST}=await import(`data:text/javascript;base64,${Buffer.from('const {clone,filtering,CONTROL,SPACE,row,NGUI,T,S,emptyBlock,advanceOf,glyphsOf,SORTS}=globalThis.toolbarFixture;\n'+source).toString('base64')}`);
test("shared roster toolbar fits every control in one row and keeps anchors fixed when sort changes",()=>{
  let anchors;
  for(const sort of globalThis.toolbarFixture.SORTS){
    const lay=toolbarLayout({sort});
    const boxes=[[lay.cbLeft,lay.cbW],[lay.seg1-3*lay.segW,3*lay.segW],[lay.seg2-3*lay.segW,3*lay.segW],[lay.search-75,150],[lay.sort-lay.sortW/2,lay.sortW],[lay.order-75,150]];
    assert.ok(boxes[0][0]>=-1270+measure(text["roster.title"],lay.titleFs)+SPACE.l);
    for(let i=1;i<boxes.length;i++)assert.ok(boxes[i][0]>=boxes[i-1][0]+boxes[i-1][1]+(i<4?SPACE.l:SPACE.m));
    assert.equal(boxes.at(-1)[0]+boxes.at(-1)[1],1412);
    if(anchors)assert.deepEqual(boxes,anchors);anchors=boxes;
    assert.equal(lay.sortText.text,text[`sort.${sort}`]);
  }
});

// UI-13: the six segments and Group Alts have names and pressed states
test("the segments and Group Alts are named by their captions and pressed while their setting is on",()=>{
  const lay=toolbarLayout({sort:"name"});
  const view={mods:"mods",recruit:"all",merged:true};
  const hits=toggleHits(view,lay);
  assert.deepEqual(hits.map(h=>h.name),["All","Modded","No Mod","All","Playable","NPC","Group Alts"]);
  assert.deepEqual(hits.map(h=>h.pressed),[false,true,false,true,false,false,true]);
  // where they were: the filters' three slots each (the right edge of a filter is its centre plus 2.5 slots), Group Alts over its toggle
  assert.deepEqual(hits.slice(0,3).map(h=>h.box[0]),[0,1,2].map(i=>lay.seg1-(2.5-i)*lay.segW));
  assert.deepEqual(hits.slice(3,6).map(h=>h.box[0]),[0,1,2].map(i=>lay.seg2-(2.5-i)*lay.segW));
  assert.ok(hits.slice(0,6).every(h=>h.box[1]===720-219-50.2&&h.box[2]===lay.segW&&h.box[3]===104));
  assert.deepEqual(hits[6].box,[lay.cbLeft+lay.cbW/2,720-219-50.2,lay.cbW,CONTROL.toolbar.h]);
  // apply sets the setting and nothing else
  const next={mods:"all",recruit:"all",merged:false};
  hits[2].apply(next);assert.deepEqual(next,{mods:"none",recruit:"all",merged:false});
  hits[4].apply(next);assert.deepEqual(next,{mods:"none",recruit:"yes",merged:false});
  hits[6].apply(next);assert.equal(next.merged,true);hits[6].apply(next);assert.equal(next.merged,false);
  assert.equal(toggleHits({mods:"all",recruit:"no",merged:false},lay).filter(h=>h.pressed).length,2,"one segment of each filter is pressed");
});

test("every shipped language names the segments and Group Alts with its own captions",()=>{
  const lay=toolbarLayout({sort:"name"});
  try{
    for(const lang of["en-US","zh-CN","zh-TW","ja-JP","ko-KR","th-TH"]){
      const ui=JSON.parse(readFileSync(new URL(`../../src/bmpt/locales/${lang}.json`,import.meta.url),"utf8")).webui;
      globalThis.toolbarFixture.table=Object.fromEntries([...Object.entries(ui.seg).map(([k,v])=>[`seg.${k}`,v]),["roster.merge",ui.roster.merge]]);
      const names=toggleHits({mods:"all",recruit:"all",merged:false},lay).map(h=>h.name);
      assert.deepEqual(names,[ui.seg.all,ui.seg.mods,ui.seg.noMods,ui.seg.all,ui.seg.recruit,ui.seg.noRecruit,ui.roster.merge],lang);
      assert.ok(names.every(Boolean));
    }
  }finally{delete globalThis.toolbarFixture.table;}
});

// UI-18: the empty list's picture and message sit on the middle of the paper the eye sees
const UNITS=2960/1920;  // NGUI units per stage px: S is stage px per unit
const PAPER_TOP=334.5,PAPER_BOTTOM=-714.5,SEARCH_LIST_TOP=314-112-20;  // roster.js: the whole paper, and what the open search box leaves
const inkOf=(root,label)=>{ // [top, bottom] of the block's ink in units (y up), the root at `root`
  const {picture}=EMPTY_LIST,pictureTop=root+picture.y+picture.size/2-picture.inkTop*picture.size;
  const band=globalThis.toolbarFixture.NGUI.inkBand(label.text,label.fontSize*globalThis.toolbarFixture.S);
  return[pictureTop,root+EMPTY_LIST.label-band.bottom/globalThis.toolbarFixture.S];
};
for(const [name,top] of [["without search",PAPER_TOP],["with the search box open",SEARCH_LIST_TOP]])test(`the roster's empty block is centred on the visible paper, ${name}`,()=>{
  const centre=(top+PAPER_BOTTOM)/2;
  for(const text of["No Results","无结果","該当なし","결과 없음","ไม่พบผลลัพธ์","Pending Conversion Results Of A Long Search"]){
    const label={text,fontSize:48},root=emptyListY(centre,label),[inkTop,inkBottom]=inkOf(root,label);
    assert.ok(Math.abs((inkTop+inkBottom)/2-centre)<1e-6,`${text}: block centre ${(inkTop+inkBottom)/2} against the paper's ${centre}`);
    // the block stays on the paper
    assert.ok(inkTop<=top&&inkBottom>=PAPER_BOTTOM,`${text}: ${inkTop}..${inkBottom} on ${top}..${PAPER_BOTTOM}`);
  }
});

test("the search box moves the empty block up by half of what it covers, and the old placement was off the paper",()=>{
  const label={text:"No Results",fontSize:48};
  const open=emptyListY((SEARCH_LIST_TOP+PAPER_BOTTOM)/2,label),closed=emptyListY((PAPER_TOP+PAPER_BOTTOM)/2,label);
  assert.ok(Math.abs((closed-open)-(PAPER_TOP-SEARCH_LIST_TOP)/2)<1e-6);
  // roster.js before: the prefab's root (53) shifted by the box's whole height, so the label ended below the paper
  const old=53-(PAPER_TOP-SEARCH_LIST_TOP),oldBottom=old+EMPTY_LIST.label-28;  // the label box is 56 high: its bottom at -716.5
  assert.ok(oldBottom<PAPER_BOTTOM,"the old label reached past the paper's bottom with the search open");
  assert.ok(inkOf(open,label)[1]>PAPER_BOTTOM);
});

for(const locale of ["en-US","zh-CN"])test(`roster toolbar uses centred shared icon bodies and design-system gaps in ${locale}`,()=>{
  const ui=JSON.parse(readFileSync(new URL(`../../src/bmpt/locales/${locale}.json`,import.meta.url),"utf8")).webui;
  const flatten=(value,prefix="")=>Object.entries(value).flatMap(([key,item])=>typeof item==="string"?[[prefix+key,item]]:flatten(item,prefix+key+"."));
  globalThis.toolbarFixture.table=Object.fromEntries(flatten(ui));
  try{
    const lay=toolbarLayout({sort:"name"}),boxes=lay.boxes;
    assert.deepEqual([boxes[3].w,boxes[3].h],[boxes[5].w,boxes[5].h]);
    for(const b of boxes)assert.equal(b.y+b.h/2,116/2);
    for(let i=1;i<boxes.length;i++)assert.ok(Math.abs(boxes[i].x-boxes[i-1].x-boxes[i-1].w-(i<4?SPACE.l:SPACE.m))<1e-9);
    assert.equal(new Set(lay.segLabels.map(f=>f.fs)).size,1);
    assert.equal(boxes.at(-1).x+boxes.at(-1).w,1412);
  }finally{delete globalThis.toolbarFixture.table;}
});

const n=(name,pos=[0,0],widgets=[],children=[])=>({name,pos,widgets,children,scale:[1,1]});
const spr=(w_,h_,pivot=4)=>({sprite:"Body",w_,h_,pivot});
function toolbarPrefab(){
  const marker=name=>n(name,[0,0],[],[n("Icon"),n("Filter_02",[0,0],[spr(432,4)])]);
  const sort=n("Btn_Normal",[-83,0],[spr(622,116)],[marker("FilterMarker_Off"),marker("FilterMarker_On"),n("Icon"),n("Type_1"),n("Type_2",[0,0],[],[n("Text",[0,0],[{text:"",fontSize:56}])])]);
  const order=n("Btn_Normal",[-45.1,0],[spr(243,116)],[n("Icon_down"),n("Icon_Up")]);
  const filter=n("FilterBtn",[0,-219],[],["All","Main","Support"].map(name=>n(name,[0,0],[],["Btn_Normal","Btn_Pressed"].map(state=>n(state,[0,0],[],[n("Caption",[0,0],[{texture:"Caption",color:"#ffffff"}])])))));
  const menu=n("TopMenu",[0,0],[],[filter,n("SearchBtn",[369.5,-268],[],[n("Btn_Normal",[89,-6],[spr(150,116)]),n("Btn_Pressed",[89,-6],[spr(150,116)])]),n("SortBtn",[934.1,-273.5],[],[sort]),n("OrderBtn",[1335.9,-273.2],[],[order]),n("Menubar",[0,0])]);
  return {collection:n("Collection",[0,0],[],[n("SeletControl",[0,0],[],[menu])]),checkbox:n("Checkbox",[0,0],[spr(300,104,3)],[n("Type_ON",[0,0],[],[n("Check")]),n("Type_OFF",[0,0],[],[n("Check")]),n("Locale_Text")])};
}
for(const locale of ["en-US","zh-CN","zh-TW","ja-JP","ko-KR","th-TH"])test(`roster clones draw the layout boxes and fit captions in ${locale}`,async()=>{
  const ctx={font:"",measureText(value){const fs=parseFloat(/([\d.]+)px/.exec(this.font)?.[1]||16);return {width:[...value].reduce((w,c)=>w+fs*(/[\u2e80-\u9fff\u3000-\u30ff\uff00-\uffef\uac00-\ud7af\u0e00-\u0e7f]/.test(c)?1:.55),0),actualBoundingBoxAscent:.75*fs,actualBoundingBoxDescent:.2*fs,fontBoundingBoxAscent:1.16*fs,fontBoundingBoxDescent:.29*fs};}};
  globalThis.document={createElement:()=>({getContext:()=>ctx,remove(){}}),documentElement:{lang:locale,appendChild(){}}};globalThis.getComputedStyle=()=>({getPropertyValue:()=>""});
  const real=await import("../../src/bmpt/web/js/ngui.js"),fixture=globalThis.toolbarFixture,previous=fixture.NGUI.fitGroup;
  const ui=JSON.parse(readFileSync(new URL(`../../src/bmpt/locales/${locale}.json`,import.meta.url),"utf8")).webui;
  fixture.real=real;fixture.NGUI.fitGroup=real.fitGroup;
  fixture.table=Object.fromEntries([...["seg","sort","roster"].flatMap(group=>Object.entries(ui[group]).map(([key,value])=>[group+"."+key,value]))]);
  try{
    const view={sort:"recent",mods:"all",recruit:"all",order:"asc",merged:true},lay=toolbarLayout(view),prefab=toolbarPrefab(),before=structuredClone(prefab);
    const tree=toolbarTree(prefab.collection,prefab.checkbox,view,true,lay),find=fixture.NGUI.find;
    for(const [name,box] of [["SearchBtn",lay.boxes[3]],["SortBtn",lay.boxes[4]],["OrderBtn",lay.boxes[5]]]){
      const control=find(tree,name);assert.deepEqual(control.pos,[box.x+box.w/2,-269.2]);
      for(const state of control.children){const body=state.widgets.find(w=>w.sprite);if(!body)continue;assert.deepEqual([body.w_,body.h_],[box.w,box.h]);
        const pivot=body.pivot??4;assert.equal(control.pos[0]+state.pos[0]+(.5-(pivot%3)/2)*body.w_,box.x+box.w/2);
        assert.equal(control.pos[1]+state.pos[1]+(Math.floor(pivot/3)/2-.5)*body.h_,-269.2);}
    }
    const menubar=find(tree,"Menubar"),merge=find(menubar,"Merge");assert.equal(menubar.pos[1]+merge.pos[1],-269.2);
    assert.deepEqual(prefab,before,"prefabs remain reusable");
    for(const viewport of [960,1600,2560])for(const density of [1,2])for(const box of lay.boxes){
      const k=viewport/1920*density,S=fixture.S;assert.ok((960+box.x*S)*k>=0&&(960+(box.x+box.w)*S)*k<=viewport*density);
      assert.equal(box.h*S*k,(lay.boxes.indexOf(box)<3?CONTROL.toolbar.h:116)*S*k);
    }
    const fits=[...lay.segLabels,lay.sortText,lay.cbText];for(const fit of fits)assert.ok(!fit.overflow,`${locale}: ${fit.text}`);
    assert.equal(new Set(lay.segLabels.map(f=>f.fs)).size,1);
    for(const fit of lay.segLabels){for(const line of fit.text.split("\n"))assert.ok(real.glyphWidth(line,fit.fs)<=lay.segW-26+1e-9);assert.ok(fit.lines*real.fontSizeFor(fit.text,fit.fs)*real.LINE_PITCH<=96);}
  }finally{delete fixture.real;delete fixture.table;fixture.NGUI.fitGroup=previous;}
});
