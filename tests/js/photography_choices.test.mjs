// The camera's mode button exists only when the student has the other mode. Run by Node's test runner.
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {test} from 'node:test';
import {otherMode} from '../../src/ba_modding_toolkit/web/js/photography/camera.js';

const web=new URL('../../src/ba_modding_toolkit/web/js/',import.meta.url);
const controller=readFileSync(new URL('photography/controller.js',web),'utf8');

test('the other mode is offered only when the student has it',()=>{
  assert.equal(otherMode('portrait',{hasPortrait:true,hasLobby:true}),'lobby');
  assert.equal(otherMode('lobby',{hasPortrait:true,hasLobby:true}),'portrait');
  assert.equal(otherMode('portrait',{hasPortrait:true,hasLobby:false}),null,'no memorial lobby: nothing to switch to');
  assert.equal(otherMode('lobby',{hasPortrait:false,hasLobby:true}),null,'no portrait');
  assert.equal(otherMode('portrait',{}),null);
  assert.equal(otherMode('portrait',undefined),null);
});

function harness({mode='portrait',info}={}){
  const code=controller.slice(controller.indexOf('async function modeButton('),controller.indexOf('async function backButton('));
  const drawn=[],hits=[],corners=[],host={children:[],append(child){this.children.push(child);}};
  const node=name=>({name,pos:[0,0],scale:[1,1],widgets:[{sprite:name,w_:1,h_:1}],children:[]});
  const tree={name:'Able_Count',children:[{...node('Bg'),widgets:[{sprite:'Bg',w_:156,h_:104,depth:1}]},{...node('Icon'),widgets:[{sprite:'Icon',w_:116,h_:84,depth:1}]}],widgets:[]};
  const find=(root,path)=>path.split('/').reduce((n,key)=>n.children.find(child=>child.name===key),root);
  const env={mode,otherMode,bridge:{info:()=>info},S:1920/2960,P:key=>key,safe:fn=>fn,switchMode(){},
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
test('a skin with both modes keeps the button where it was, named for the mode it switches to',async()=>{
  for(const [mode,name] of [['portrait','lobby'],['lobby','portrait']]){
    const h=harness({mode,info:{hasPortrait:true,hasLobby:true}});
    await h.run(h.host);
    assert.equal(h.hits.length,1);
    assert.deepEqual(h.hits[0].box,[1788,25,102,76]);
    assert.equal(h.hits[0].label,name);
    assert.equal(h.hits[0].dataset.photoAction,'Mode');
    assert.deepEqual(h.corners[0].slice(1),[[1352,25,538,76],{corner:'top-right'}]);
  }
});
test('the camera draws the mode button through that check, and switching still refuses without the mode',()=>{
  assert.match(controller,/async function modeButton\(host\)\{\s*\/\/[^\n]*\n\s*if\(!otherMode\(mode,bridge\.info\(\)\)\)return;/);
  assert.match(controller,/const next=otherMode\(mode,bridge\.info\(\)\);\s*if\(!next\)throw new Error\(P\('noAssets'\)\);/);
  assert.match(controller,/modeButton\(layer\)/);
});

