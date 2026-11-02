import assert from "node:assert/strict";
import { test } from "node:test";
import { readFile } from "node:fs/promises";
import { createDraft, sourceKey, portraitRows, studentChoices, memorialChoices } from "../../src/ba_modding_toolkit/web/js/student-hall-model.js";
import { normalizeView } from "../../src/ba_modding_toolkit/web/js/roster-order.js";
class Element {style={};dataset={};children=[];append(child){child.parentElement=this;this.children.push(child);}setAttribute(){}querySelector(){return null;}}
const drawn=[],cards=[],errors=[];
const signal=new AbortController().signal,layer=new Element(),modal={host:new Element(),live:()=>true,close(){},async draw(build){const promise=build(layer,()=>true,signal);drawn.push(promise);await promise;}};
globalThis.document={createElement:()=>new Element()};
globalThis.pickerFixture={normalizeView,sourceKey,portraitRows,studentChoices,memorialChoices,
  bindReorder(){},dragFeedback:()=>({}),Display:{},Popup:{top:()=>null},T:key=>key,L:names=>names?.en,language:()=>"en",spineBase:()=>"source",
  thumbnailScope:()=>({signal,cancel(){}}),lazyThumbnail:()=>({}),
  UI:{MEMORIAL_QUEUE_PITCH:244,createModal:()=>modal,rosterToolbar:async()=>{},caption:async()=>{},panel:async()=>{},studentCard:async()=>{},queuePanel:async()=>{},queueMarker:async()=>new Element(),button:async()=>{},
    async card(host,item,options){cards.push({item,options});return{visual:new Element(),hit:new Element(),actionBox:[0,0,180,36]};},
    virtualArea(host,box,count,pitch,render){const scroll=new Element(),inner=new Element();scroll.append(inner);host.append(scroll);const pending=[];
      for(let i=0;i<count;i++){const cell=new Element();inner.append(cell);pending.push(render(cell,i,signal));}return{inner,ready:Promise.all(pending)};}}};
let source=await readFile(new URL("../../src/ba_modding_toolkit/web/js/student-hall-picker.js",import.meta.url),"utf8");source=source.replace(/^import .*;$/gm,"");
const API=await import(`data:text/javascript;base64,${Buffer.from('const {UI,Display,Popup,bindReorder,dragFeedback,normalizeView,sourceKey,portraitRows,studentChoices,memorialChoices,thumbnailScope,lazyThumbnail,spineBase,L,T,language}=globalThis.pickerFixture;\n'+source+'\n//# sourceURL=hall-picker-fixture.js').toString('base64')}`);
const settle=async()=>{await new Promise(setImmediate);await drawn.at(-1);};
test("adding a memorial disables its current control synchronously and the redraw reports Added",async()=>{
  const draft=createDraft({revision:0,mode:"memorial",portraits:[],memorials:[],active:null});draft.begin("memorial");
  const choice={variant:"hero",family:"hero",kind:"lobby",source:"official",core:"hero_home",names:{en:{full:"Hero"}}};
  const picker=API.openPicker({kind:"lobby",choices:[choice],draft,onError:error=>errors.push(error),onCancel(){}});await settle();
  const candidate=cards.find(card=>card.options.onAdd);assert.equal(candidate.options.onPreview,undefined);
  const hit={disabled:false};const pending=candidate.options.onAdd({currentTarget:hit});
  assert.equal(hit.disabled,true);assert.equal(draft.current().memorials.length,1);await pending;
  const next=cards.filter(card=>card.options.onAdd).at(-1);assert.equal(next.options.addEnabled,false);assert.equal(next.options.addLabel,"hall.added");
  await candidate.options.onAdd({currentTarget:hit});assert.equal(draft.current().memorials.length,1);assert.deepEqual(errors,[]);picker.close();
});

// UI-34: Escape in the picker's search box closes the search (as its toolbar button does) and the picker stays
test("Escape in the picker's search closes the search and clears it; the picker stays",async()=>{
  const fixture=globalThis.pickerFixture.UI,saved={rosterToolbar:fixture.rosterToolbar,search:fixture.search};
  const toolbars=[],searches=[];let closed=0;
  fixture.rosterToolbar=async(host,view,searching,actions)=>{toolbars.push({searching,actions});};
  fixture.search=async(host,query,onChange,box,options)=>{searches.push({query,onChange,options});};
  const close=modal.close;modal.close=()=>{closed++;};
  try{
    const draft=createDraft({revision:0,mode:"memorial",portraits:[],memorials:[],active:null});draft.begin("memorial");
    const choice={variant:"hero",family:"hero",kind:"lobby",source:"official",core:"hero_home",names:{en:{full:"Hero"}}};
    const picker=API.openPicker({kind:"lobby",choices:[choice],draft,onError:error=>errors.push(error),onCancel(){}});await settle();
    assert.equal(toolbars.at(-1).searching,false);assert.equal(searches.length,0);
    toolbars.at(-1).actions.search();await settle();
    assert.equal(toolbars.at(-1).searching,true);assert.equal(searches.length,1);
    assert.equal(typeof searches[0].options?.onEscape,"function","the box closes the search on Escape");
    searches[0].onChange("Her");await settle(); // typing filters the list; the box keeps its own text
    searches.at(-1).options.onEscape();await settle();
    assert.equal(toolbars.at(-1).searching,false,"the search is closed");
    const count=searches.length;
    toolbars.at(-1).actions.search();await settle();
    assert.equal(searches.length,count+1);assert.equal(searches.at(-1).query,"","opened again, it starts empty");
    assert.equal(closed,0,"the picker stays");assert.deepEqual(errors,[]);picker.close();
  }finally{Object.assign(fixture,saved);modal.close=close;}
});
