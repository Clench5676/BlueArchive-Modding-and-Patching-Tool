import assert from "node:assert/strict";
import { test } from "node:test";
import { readFile } from "node:fs/promises";
import { createDraft, sourceKey, portraitRows, studentChoices, memorialChoices } from "../../src/bmpt/web/js/student-hall-model.js";
import { normalizeView } from "../../src/bmpt/web/js/roster-order.js";
class Element {style={};dataset={};children=[];append(child){child.parentElement=this;this.children.push(child);}setAttribute(){}querySelector(){return null;}}
const drawn=[],cards=[],errors=[],captions=[],plates=[];
const signal=new AbortController().signal,layer=new Element(),frames=[],modal={host:new Element(),live:()=>true,close(){},async draw(build,actions,options){frames.push({actions,options});const promise=build(layer,()=>true,signal);drawn.push(promise);await promise;}};
globalThis.document={createElement:()=>new Element()};
globalThis.pickerFixture={normalizeView,sourceKey,portraitRows,studentChoices,memorialChoices,
  bindReorder(){},dragFeedback:()=>({}),Display:{},Popup:{top:()=>null},T:key=>key,L:names=>names?.en,language:()=>"en",spineBase:()=>"source",
  thumbnailScope:()=>({signal,cancel(){}}),lazyThumbnail:()=>({}),
  UI:{buttonSize:()=>48,compactButtonSize:()=>41,PORTRAIT_FRAME:{width:2098,height:1300,compactFooter:true,titleCentre:66},portraitRowLayout:()=>({height:393,pitch:397,width:1309,name:[110,170,148],cardX:117,cardY:138,stripX:228,stripW:972}),portraitPickerLayout:()=>({toolbar:{x:305.5,y:270,width:1309},search:[305.5,311,1309],list:[305.5,330,1309,551]}),MEMORIAL_QUEUE_PITCH:244,WIDE:{left:305.5,width:1308.6},MEMORIAL:{students:650,gap:20,heads:4,headPitch:155,cardScale:.37,cardPitch:290,cardX:165},createModal:()=>modal,rowPlate:async(...args)=>{plates.push(args);},rosterToolbar:async()=>{},caption:async(...args)=>{captions.push(args);},panel:async()=>{},studentCard:async()=>{},queuePanel:async()=>{},queueMarker:async()=>new Element(),retainedButton:async()=>{},button:async()=>{},
    async card(host,item,options){cards.push({item,options});return{visual:new Element(),hit:new Element(),actionBox:[0,0,180,36]};},
    virtualArea(host,box,count,pitch,render){const scroll=new Element(),inner=new Element();scroll.append(inner);host.append(scroll);const pending=[];
      for(let i=0;i<count;i++){const cell=new Element();inner.append(cell);pending.push(render(cell,i,signal));}return{inner,ready:Promise.all(pending)};}}};
let source=await readFile(new URL("../../src/bmpt/web/js/student-hall-picker.js",import.meta.url),"utf8");source=source.replace(/^import .*;$/gm,"");
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

test("portrait replacement reuses Hall rows and explicit choice buttons without adding another entry",async()=>{
  cards.length=0;const replaced=[],draft=createDraft({revision:0,mode:"portraits",portraits:[{id:"a",variant:"hero",source:"mod:old",x:800,y:1100,scale:1}],memorials:[],active:null});draft.begin("layout");
  const choice={variant:"hero-alt",family:"hero",kind:"spr",source:"official",core:"hero_alt_spr",names:{en:{full:"Hero Alt"}}};
  const picker=API.openPicker({kind:"spr",choices:[choice],draft,onError:e=>errors.push(e),onDone(){},onReplace:c=>replaced.push(c)});await settle();
  const candidate=cards.find(c=>c.options.onAdd);assert.equal(candidate.options.addLabel,"import.select");assert.equal(candidate.options.fitAdd,true);assert.equal(candidate.options.addSize,41,"the Add buttons share the compact caption size");assert.equal(frames.at(-1).options,pickerFixture.UI.PORTRAIT_FRAME,"the portrait picker draws in its own compact frame");assert.deepEqual(frames.at(-1).actions.map(a=>a.label),["popup.cancel"]);assert.equal(candidate.options.addEnabled,true);
  const hit={disabled:false};await candidate.options.onAdd({currentTarget:hit});assert.equal(hit.disabled,true);assert.deepEqual(replaced,[choice]);assert.equal(draft.current().portraits.length,1);picker.close();
});
test("the portrait picker draws names and plates from the row layout, with Done in the compact frame",async()=>{
  cards.length=0;captions.length=0;plates.length=0;
  const draft=createDraft({revision:0,mode:"portraits",portraits:[],memorials:[],active:null});draft.begin("layout");
  const choice={variant:"hero",family:"hero",kind:"spr",source:"official",core:"hero_spr",names:{en:{full:"Hero"}}};
  const picker=API.openPicker({kind:"spr",choices:[choice],draft,onError:e=>errors.push(e),onDone(){}});await settle();
  assert.deepEqual(plates.at(-1)[1],[1309,393],"the plate is as wide as the picker's panels");
  assert.deepEqual(captions.at(-1)[2],[110,170,148],"the name stands where the row layout puts it");
  assert.equal(frames.at(-1).options,pickerFixture.UI.PORTRAIT_FRAME);assert.deepEqual(frames.at(-1).actions.map(a=>a.label),["hall.doneAdding"]);
  assert.equal(cards.find(c=>c.options.onAdd).options.addSize,41);assert.deepEqual(errors,[]);picker.close();
});
test("Change Portrait is present beside background in all six locales, with the owner's exact en/zh wording",async()=>{
  const words={"en-US":"Change Portrait","zh-CN":"\u66f4\u6362\u7acb\u7ed8"};
  for(const lang of ["en-US","zh-CN","zh-TW","ja-JP","ko-KR","th-TH"]){
    const raw=await readFile(new URL(`../../src/bmpt/locales/${lang}.json`,import.meta.url),"utf8"),hall=JSON.parse(raw).webui.hall;
    assert.ok(hall.changePortrait);if(words[lang])assert.equal(hall.changePortrait,words[lang]);assert.match(raw,/"background": [^\n]+\n\s*"changePortrait":/);
  }
});
