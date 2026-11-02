import assert from "node:assert/strict";
import { test } from "node:test";
import { readFile } from "node:fs/promises";
import { readFileSync } from "node:fs";
const text={"roster.title":"Student List","roster.merge":"Group Alts","seg.all":"All","seg.mods":"Modded","seg.noMods":"No Mod","seg.recruit":"Playable","seg.noRecruit":"NPC","sort.recent":"Recently Viewed","sort.added":"Recently Added","sort.name":"Name","sort.mods":"Mod Count","sort.default":"Default"};
const measure=(value,size)=>value.length*size*.45;
const fit=(value,size,min,width)=>({text:value,fs:Math.max(min,Math.min(size,width/(value.length*.45))),lines:1});
let source=await readFile(new URL("../../src/ba_modding_toolkit/web/js/roster-widgets.js",import.meta.url),"utf8");source=source.replace(/^import .*;$/gm,"");
globalThis.toolbarFixture={NGUI:{fitText:fit,fitGroup:(values,size,min,width)=>{const fits=values.map(value=>fit(value,size,min,width)),fs=Math.min(...fits.map(f=>f.fs));return fits.map(f=>({...f,fs}));}},T:key=>(globalThis.toolbarFixture.table||text)[key]||key,advanceOf:measure,glyphsOf:measure,SORTS:["recent","added","name","mods","default"]};
const {toolbarLayout,toggleHits}=await import(`data:text/javascript;base64,${Buffer.from('const {NGUI,T,advanceOf,glyphsOf,SORTS}=globalThis.toolbarFixture;\n'+source).toString('base64')}`);
test("shared roster toolbar fits every control in one row and keeps anchors fixed when sort changes",()=>{
  let anchors;
  for(const sort of globalThis.toolbarFixture.SORTS){
    const lay=toolbarLayout({sort});
    const boxes=[[lay.cbLeft,lay.cbW],[lay.seg1-3*lay.segW,3*lay.segW],[lay.seg2-3*lay.segW,3*lay.segW],[lay.search-75,150],[lay.sort-lay.sortW/2,lay.sortW],[lay.order-243/2,243]];
    assert.ok(boxes[0][0]>=-1270+measure(text["roster.title"],lay.titleFs)+40);
    for(let i=1;i<boxes.length;i++)assert.ok(boxes[i][0]>=boxes[i-1][0]+boxes[i-1][1]+15.99);
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
  assert.deepEqual(hits[6].box,[lay.cbLeft+lay.cbW/2,720-273,lay.cbW,110]);
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
      const ui=JSON.parse(readFileSync(new URL(`../../src/ba_modding_toolkit/locales/${lang}.json`,import.meta.url),"utf8")).webui;
      globalThis.toolbarFixture.table=Object.fromEntries([...Object.entries(ui.seg).map(([k,v])=>[`seg.${k}`,v]),["roster.merge",ui.roster.merge]]);
      const names=toggleHits({mods:"all",recruit:"all",merged:false},lay).map(h=>h.name);
      assert.deepEqual(names,[ui.seg.all,ui.seg.mods,ui.seg.noMods,ui.seg.all,ui.seg.recruit,ui.seg.noRecruit,ui.roster.merge],lang);
      assert.ok(names.every(Boolean));
    }
  }finally{delete globalThis.toolbarFixture.table;}
});
