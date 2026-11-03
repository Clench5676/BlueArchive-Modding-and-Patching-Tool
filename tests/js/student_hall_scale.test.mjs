import assert from "node:assert/strict";
import { test } from "node:test";
import { bindScale } from "../../src/ba_modding_toolkit/web/js/student-hall-scale.js";
import { createDraft } from "../../src/ba_modding_toolkit/web/js/student-hall-model.js";
import { portraitScale, portraitRect } from "../../src/ba_modding_toolkit/web/js/student-hall-framing.js";
class Input {
  value=""; listeners={};
  addEventListener(name,fn){(this.listeners[name] ||= []).push(fn);}
  emit(name,event={}){for(const fn of this.listeners[name]||[])fn(event);}
}
function setup(value=1) {
  const range=new Input(),number=new Input(),reset=new Input(),changes=[],paints=[];
  let enabled=true;
  const control=bindScale(range,number,reset,{value,change:(v)=>changes.push(v),paint:(f)=>paints.push(f),enabled:()=>enabled});
  return {range,number,reset,changes,paints,control,disable(){enabled=false;}};
}
test("slider and numeric percent stay synchronized with exact integer percentage round trips",()=>{
  const s=setup();assert.equal(s.number.value,"100");assert.equal(s.range.value,"100");
  s.range.value="73";s.range.emit("input");assert.equal(s.number.value,"73");assert.equal(s.control.value(),.73);
  s.number.value="85";s.number.emit("input");assert.equal(s.range.value,"85");assert.equal(s.control.value(),.85);
  assert.deepEqual(s.changes,[.73,.85]);assert.equal(s.paints.at(-1),(85-25)/125);
});
test("numeric partial input is not prematurely clamped; commit clamps and invalid input restores last valid size",()=>{
  const s=setup(.8);s.number.value="7";s.number.emit("input");assert.equal(s.number.value,"7");assert.equal(s.range.value,"80");
  s.number.value="75";s.number.emit("input");assert.equal(s.range.value,"75");
  s.number.value="1";s.number.emit("change");assert.equal(s.number.value,"25");assert.equal(s.control.value(),.25);
  s.number.value="999";s.number.emit("blur");assert.equal(s.number.value,"150");assert.equal(s.control.value(),1.5);
  s.number.value="";s.number.emit("blur");assert.equal(s.number.value,"150");
  s.number.value="bad";s.number.emit("change");assert.equal(s.number.value,"150");
});
test("Reset restores100percent and a pending write prevents input mutations",()=>{
  const s=setup(.6);s.reset.emit("click");assert.equal(s.control.value(),1);assert.equal(s.number.value,"100");
  s.disable();s.range.value="40";s.range.emit("input");s.reset.emit("click");assert.equal(s.number.value,"100");assert.deepEqual(s.changes,[1]);
});
test("global size drafts cancel/save without changing any actor position or relative scale",()=>{
  const value={revision:0,mode:"portraits",background:"office-night",portraits:[{id:"a",variant:"a",source:"mod:a",x:600,y:1000,scale:.8}],memorials:[],active:null};
  const model=createDraft(value);assert.equal(model.current().portraitScale,1);assert.equal(value.portraitScale,undefined);
  model.begin("layout").portraitScale=.75;assert.ok(model.dirty());assert.deepEqual(model.current().portraits,value.portraits);
  model.cancel();assert.equal(model.current().portraitScale,1);
  model.begin("layout").portraitScale=.73;model.accept({...structuredClone(model.current()),revision:1});assert.equal(model.saved().portraitScale,.73);assert.equal(model.dirty(),false);
  const entry=value.portraits[0],bounds={x:-200,y:0,width:600,height:1400};
  assert.ok(Math.abs(portraitScale(entry,.5)-.72*.8*.5)<1e-12);
  const normal=portraitRect(entry,bounds,1),half=portraitRect(entry,bounds,.5);
  assert.equal(half.width,normal.width*.5);assert.equal(half.height,normal.height*.5);
  assert.equal(half.x,entry.x+(normal.x-entry.x)*.5);assert.equal(half.y,entry.y+(normal.y-entry.y)*.5);
});

test("slider-only binding supports the full25to150range without a numeric field or reset control",()=>{
  const range=new Input(),changes=[];
  const control=bindScale(range,null,null,{value:1,change:value=>changes.push(value)});
  assert.equal(range.min,"25");assert.equal(range.max,"150");
  for(const value of [25,73,150]){range.value=String(value);range.emit("input");assert.equal(control.value(),value/100);}
  assert.deepEqual(changes,[.25,.73,1.5]);
});

import { PORTRAIT_SIZE, PORTRAIT_TOOLBAR_BOUNDS, portraitToolbarLayout } from "../../src/ba_modding_toolkit/web/js/student-hall-framing.js";
test("individual size uses .5–2.0, hundredth steps, reset and a cancellable/saveable draft",()=>{
  const saved={revision:0,mode:"portraits",portraitScale:.8,portraits:[{id:"a",variant:"a",source:"mod:a",x:900,y:1100,scale:.8,mirror:true}],memorials:[],active:null};
  const draft=createDraft(saved);draft.begin("layout");const range=new Input(),entry=draft.current().portraits[0];
  const control=bindScale(range,null,null,{value:entry.scale,limits:PORTRAIT_SIZE,change:v=>{entry.scale=v;}});
  assert.equal(range.min,"50");assert.equal(range.max,"200");assert.equal(range.step,"1");
  for(const [raw,expected] of [["0",.5],["125",1.25],["999",2],["133.7",1.34]]){range.value=raw;range.emit("input");assert.equal(entry.scale,expected);}
  control.reset();assert.equal(entry.scale,1);assert.equal(control.value(),1);assert.equal(draft.current().portraitScale,.8);
  draft.cancel();assert.equal(draft.current().portraits[0].scale,.8);
  draft.begin("layout").portraits[0].scale=2;draft.accept({...draft.current(),revision:1});assert.equal(draft.saved().portraits[0].scale,2);
});
test("toolbar fits at every corner and both size limits, all window scales/densities, with separate icon and slider rows",()=>{
  for(const own of [.5,2])for(const global of [.25,1.5])for(const mirror of [false,true])for(const x of [0,1920])for(const y of [200,1800])for(const count of [0,1,6,7]){
    const rect=portraitRect({x,y,scale:own,mirror},{x:-200,y:0,width:900,height:1800},global),box=portraitToolbarLayout(rect,count);
    for(const windowWidth of [960,1600,2560])for(const density of [1,2,3]){
      const k=windowWidth/1920*density;
      assert.ok(box.x>=PORTRAIT_TOOLBAR_BOUNDS.left&&box.x+box.width<=PORTRAIT_TOOLBAR_BOUNDS.right);
      assert.ok(box.y>=PORTRAIT_TOOLBAR_BOUNDS.top&&box.y+box.height<=PORTRAIT_TOOLBAR_BOUNDS.bottom,"clear of the Hall's top-right controls");
      assert.ok(box.x*k>=0&&box.y*k>=0);assert.ok((box.x+box.width)*k<=1920*k);assert.ok((box.y+box.height)*k<=1080*k);
      assert.ok(box.icon<box.reset[1]);assert.ok(box.slider[1]-24>box.icon);
      assert.ok(box.slider[0]+box.slider[2]<box.reset[0]);assert.ok(box.reset[0]+box.reset[2]<=box.width);
      assert.ok(box.reset[1]+box.reset[3]<=box.height);
    }
  }
});

test("legacy own sizes outside the control range change only on explicit input, including the displayed endpoint",()=>{
  for(const [initial,endpoint] of [[.2,50],[2.5,200]]){
    const range=new Input(),changes=[];bindScale(range,null,null,{value:initial,limits:PORTRAIT_SIZE,change:value=>changes.push(value)});
    assert.deepEqual(changes,[]);assert.equal(range.value,String(endpoint));range.emit("input");assert.deepEqual(changes,[endpoint/100]);
  }
});

test("the toolbar sits beside the portrait level with its top, and above or below it only when neither side fits",()=>{
  const b=PORTRAIT_TOOLBAR_BOUNDS;
  let box=portraitToolbarLayout({x:400,y:420,width:500,height:600},7);assert.equal(box.x,912);assert.equal(box.y,420);
  box=portraitToolbarLayout({x:1300,y:500,width:500,height:500},7);assert.equal(box.x+box.width,1288);assert.equal(box.y,500);
  box=portraitToolbarLayout({x:100,y:150,width:1700,height:600},7);assert.equal(box.y,762);assert.ok(box.y+box.height<=b.bottom);
  box=portraitToolbarLayout({x:100,y:700,width:1700,height:300},7);assert.equal(box.y,700-box.height-12);
  box=portraitToolbarLayout({x:0,y:0,width:1920,height:1080},7);assert.equal(box.y,b.top);assert.equal(box.x+box.width,b.right);
});
