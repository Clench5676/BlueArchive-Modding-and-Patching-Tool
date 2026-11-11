// The camera's mode button exists only when the student has the other mode (UI-16), and its Dialogue / Actions list names a
// dialogue as the detail screen does, never by its clip name (UI-17). Run by Node's test runner.
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {test} from 'node:test';
import {otherMode,actionCaption,choiceLayout} from '../../src/bmpt/web/js/photography/camera.js';

import { CONTROL, XS_BUTTON, SPACE, FULLSCREEN_BUTTON } from "../../src/bmpt/web/js/ui-tokens.js";
import { row } from "../../src/bmpt/web/js/ui-grid.js";
const web=new URL('../../src/bmpt/web/js/',import.meta.url);
const controller=readFileSync(new URL('photography/controller.js',web),'utf8');
const LOCALES=['en-US','zh-CN','zh-TW','ja-JP','ko-KR','th-TH'];
const ui=locale=>JSON.parse(readFileSync(new URL(`../../src/bmpt/locales/${locale}.json`,import.meta.url),'utf8')).webui;
const fill=(text,params={})=>text.replace(/\{(\w+)\}/g,(_,key)=>params[key]);

test('the other mode is offered only when the student has it',()=>{
  assert.equal(otherMode('portrait',{hasPortrait:true,hasLobby:true}),'lobby');
  assert.equal(otherMode('lobby',{hasPortrait:true,hasLobby:true}),'portrait');
  assert.equal(otherMode('portrait',{hasPortrait:true,hasLobby:false}),null,'no memorial lobby: nothing to switch to');
  assert.equal(otherMode('lobby',{hasPortrait:false,hasLobby:true}),null,'no portrait');
  assert.equal(otherMode('portrait',{}),null);
  assert.equal(otherMode('portrait',undefined),null);
});

function harness({mode='portrait',info}={}){
  const code=controller.slice(controller.indexOf('function photographyToolbarLayout('),controller.indexOf('// The captions of the two text buttons'))+controller.slice(controller.indexOf('async function modeButton('),controller.indexOf('async function backButton('));
  const drawn=[],hits=[],corners=[],host={children:[],append(child){this.children.push(child);}};
  const node=name=>({name,pos:[0,0],scale:[1,1],widgets:[{sprite:name,w_:1,h_:1}],children:[]});
  const tree={name:'Able_Count',children:[{...node('Bg'),widgets:[{sprite:'Bg',w_:156,h_:104,depth:1}]},{...node('Icon'),widgets:[{sprite:'Icon',w_:116,h_:84,depth:1}]}],widgets:[]};
  const find=(root,path)=>path.split('/').reduce((n,key)=>n.children.find(child=>child.name===key),root);
  const env={CONTROL,XS_BUTTON,SPACE,FULLSCREEN_BUTTON,row,mode,otherMode,bridge:{info:()=>info},S:1920/2960,P:key=>key,safe:fn=>fn,switchMode(){},
    clone:structuredClone,bindCorner:(...args)=>corners.push(args),alignIcon:async()=>{},
    document:{createElement:()=>({append(child){this.child=child;},dataset:{}})},
    NGUI:{layout:async()=>({}),find:(root,path)=>path==='Front/Right/Btn_RotateSpine/ConditionRoot/Able_Count'?tree:find(root,path),render:async(t)=>{drawn.push(t.name);return {};}},
    hitButton:(_wrapper,box,_fn,label)=>{const hit={box,label,dataset:{}};hits.push(hit);return hit;}};
  const run=new Function('env',`const {${Object.keys(env)}}=env;${code};return modeButton;`)(env);
  return {run,host,drawn,hits,corners};
}
test('a skin without a memorial lobby gets no mode button in the portrait camera',async()=>{
  const h=harness({mode:'portrait',info:{hasPortrait:true,hasLobby:false}});
  await h.run(h.host);
  assert.deepEqual([h.host.children.length,h.drawn.length,h.hits.length,h.corners.length],[0,0,0,0],'nothing drawn, no hit area');
});
test('a skin without a portrait gets no mode button in the memorial camera',async()=>{
  const h=harness({mode:'lobby',info:{hasPortrait:false,hasLobby:true}});
  await h.run(h.host);
  assert.deepEqual([h.host.children.length,h.drawn.length,h.hits.length],[0,0,0]);
});
test('a skin with both modes keeps the right edge and names the mode it switches to',async()=>{
  for(const [mode,name] of [['portrait','lobby'],['lobby','portrait']]){
    const h=harness({mode,info:{hasPortrait:true,hasLobby:true}});
    await h.run(h.host);
    assert.equal(h.hits.length,1);
    const box=h.hits[0].box;assert.ok(Math.abs(box[0]+box[2]-1890)<1e-9);assert.ok(Math.abs(box[2]-FULLSCREEN_BUTTON.w*(1920/2960))<1e-9);assert.ok(Math.abs(box[3]-FULLSCREEN_BUTTON.h*(1920/2960))<1e-9);
    assert.ok(Math.abs(box[1]+box[3]/2-(25+FULLSCREEN_BUTTON.h*(1920/2960)/2))<1e-9);
    assert.equal(h.hits[0].label,name);
    assert.equal(h.hits[0].dataset.photoAction,'Mode');
    const [anchor,options]=h.corners[0].slice(1);assert.ok(Math.abs(anchor[0]+anchor[2]-1890)<1e-9);assert.equal(anchor[1],25);assert.ok(Math.abs(anchor[3]-FULLSCREEN_BUTTON.h*(1920/2960))<1e-9);assert.deepEqual(options,{corner:'top-right'});
  }
});
test('the camera draws the mode button through that check, and switching still refuses without the mode',()=>{
  assert.match(controller,/async function modeButton\(host\)\{\s*\/\/[^\n]*\n\s*if\(!otherMode\(mode,bridge\.info\(\)\)\)return;/);
  assert.match(controller,/const next=otherMode\(mode,bridge\.info\(\)\);\s*if\(!next\)throw new Error\(P\('noAssets'\)\);/);
  assert.match(controller,/modeButton\(layer\)/);
});

test('a dialogue is captioned like the detail screen, never by its clip name',()=>{
  for(const locale of LOCALES){
    const strings=ui(locale),T=(key,params)=>{assert.equal(key,'lobby.dialogue');return fill(strings.lobby.dialogue,params);};
    for(const n of [1,2,9,10,12,99]){
      const caption=actionCaption({anim:`Talk_${String(n).padStart(2,'0')}_M`,n},T);
      assert.equal(caption,fill(strings.lobby.dialogue,{n}),locale);
      assert.ok(!/Talk_|_M$/.test(caption),`${locale}: no clip name in ${caption}`);
    }
  }
});
test('the number comes from the dialogue, whatever its place in the list',()=>{
  const T=(key,params)=>`${key}:${params.n}`;
  assert.equal(actionCaption({anim:'Talk_07_M',n:7},T),'lobby.dialogue:7');
  assert.equal(actionCaption({anim:'Talk_00_M',n:0},T),'lobby.dialogue:0','zero is a number');
});
test('an idle pose has no dialogue number and no label yet: it keeps its clip name',()=>{
  const T=()=>assert.fail('an idle pose is not a dialogue');
  assert.equal(actionCaption({anim:'Idle_01'},T),'Idle_01');
});
test('the Dialogue / Actions list captions its buttons through actionCaption in both name and label',()=>{
  assert.match(controller,/UI\.button\(actionArea,'Sequence'\+index,actionCaption\(sequences\[index\],T\),/);
  assert.doesNotMatch(controller,/UI\.button\(actionArea,'Sequence'\+index,sequences\[index\]\.anim,/);
  assert.equal(choiceLayout('lobby',5).columns,3,'unchanged: the list keeps its geometry');
});

// Blank space in the Expressions / Actions list is the list's, not the pan surface's: no grab cursor, no pan or zoom there.
const css=readFileSync(new URL('../../src/bmpt/web/css/photography.css',import.meta.url),'utf8');
const rule=selector=>[...css.matchAll(/([^{}]+)\{([^}]*)\}/g)].filter(([,sel])=>sel.split(',').map(x=>x.trim()).includes(selector)).map(([,,body])=>body).join(';');
test('the choice list covers its whole box and its scroll area takes the pointer',()=>{
  const code=controller.slice(controller.indexOf('function choiceShield('),controller.indexOf('async function choicePanel('));
  const made=[],host={prepend(child){made.push(child);}};
  const document={createElement:()=>({style:{}})};
  const choiceShield=new Function('document',`${code};return choiceShield;`)(document);
  for(const [kind,count] of [['portrait',1],['portrait',7],['portrait',30],['lobby',2],['lobby',14]]){
    const box=choiceLayout(kind,count),shield=choiceShield(host,box);
    assert.equal(made.at(-1),shield,'first in the panel, under the cards');
    assert.deepEqual(shield.style,{left:`${box.x}px`,top:`${box.y}px`,width:`${box.width}px`,height:`${box.height}px`},`${kind} ${count}`);
    assert.equal(shield.className,'photo-choice-shield');
  }
  assert.match(controller,/choiceShield\(panelHost,box\);await choicePanel\(panelHost,box\);/,'drawn with every expanded list');
  assert.match(rule('.photo-choice-shield'),/pointer-events:auto/);
  assert.match(rule('#photo-overlay .popup-cards'),/pointer-events:auto/,'the gaps between cards scroll the list');
  assert.doesNotMatch(rule('.photo-choice-shield'),/cursor/,'the game pointer, not the pan surface\'s grab');
});

// the caption fits its 185 x 53 button as the other captions of the row do (UI.button: 40 down to 26, up to two lines)
const WIDE=/[⺀-鿿　-ヿ＀-￯가-힯฀-๿]/;
const ctx={font:'',measureText(s){const fs=parseFloat(/([\d.]+)px/.exec(this.font)[1]);
  return {width:[...s].reduce((w,c)=>w+(WIDE.test(c)?1:.58)*fs,0),actualBoundingBoxAscent:.75*fs,actualBoundingBoxDescent:.2*fs,fontBoundingBoxAscent:1.16*fs,fontBoundingBoxDescent:.29*fs};}};
globalThis.document={createElement:()=>({getContext:()=>ctx,remove(){}}),documentElement:{lang:'en-US',appendChild(){}}};
globalThis.getComputedStyle=()=>({getPropertyValue:()=>''});
const NGUI=await import('../../src/bmpt/web/js/ngui.js');
const S=1920/2960;
for(const locale of LOCALES)test(`every dialogue caption fits the action button in ${locale}`,()=>{
  document.documentElement.lang=locale;
  const strings=ui(locale),widths=[185/S-60],heights=[53/S-20];
  for(let n=0;n<=99;n++){
    const caption=fill(strings.lobby.dialogue,{n}),f=NGUI.fitText(caption,40,26,widths[0],2);
    assert.ok(!f.overflow,`${locale}: ${caption}`);
    for(const line of f.text.split('\n'))assert.ok(NGUI.glyphWidth(line,f.fs)<=widths[0]+1e-9,`${locale}: ${line}`);
    assert.ok(f.lines*NGUI.fontSizeFor(f.text,f.fs)*NGUI.LINE_PITCH<=heights[0]+1e-9,`${locale}: ${caption} is too tall`);
    assert.equal(f.text.replace(/\s/g,''),caption.replace(/\s/g,''),'every character kept');
  }
});
