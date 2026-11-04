// Exercise the controller through its public setup and native events, using DOM
// and RPC fakes only. The raster and text-layout contracts have separate tests.
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { readFileSync } from 'node:fs';
import * as geometry from '../../src/ba_modding_toolkit/web/js/window-layout.js';
class Classes extends Set {
  contains(name){return this.has(name);}
  toggle(name,on=!this.has(name)){on?this.add(name):this.delete(name);return on;}
  remove(...names){names.forEach(name=>this.delete(name));}
}
class El {
  children=[];style={setProperty(key,value){this[key]=value;},removeProperty(key){delete this[key];}};
  classList=new Classes();hidden=false;inert=false;textContent='';attrs={};dataset={};
  constructor(name='div'){this.name=name;}
  set className(value){this.classList=new Classes(value.split(' '));}
  get className(){return [...this.classList].join(' ');}
  append(...nodes){for(const node of nodes){node.remove();node.parent=this;this.children.push(node);}}
  remove(){if(this.parent)this.parent.children=this.parent.children.filter(node=>node!==this);this.parent=null;}
  before(node){const parent=this.parent;node.parent=parent;parent.children.splice(parent.children.indexOf(this),0,node);}
  replaceWith(node){const parent=this.parent,index=parent.children.indexOf(this);node.remove();parent.children.splice(index,1,node);node.parent=parent;this.parent=null;}
  descendants(){return this.children.flatMap(node=>[node,...node.descendants()]);}
  matches(selector){return selector==='button'?this.name==='button':selector.startsWith('.')?this.classList.has(selector.slice(1)):false;}
  querySelectorAll(selector){return this.descendants().filter(node=>selector.split(',').some(s=>node.matches(s.trim())));}
  querySelector(selector){return this.querySelectorAll(selector)[0]||null;}
  setAttribute(key,value){this.attrs[key]=value;}
  addEventListener(){}
  getClientRects(){return this.hidden?[]:[this.getBoundingClientRect()];}
  getBoundingClientRect(){
    let left=0,top=0,width=960,height=44;
    if(this===stage){width=960;height=540;}
    if(this===back){left=88;top=4;width=54;height=56;}
    if(this===mods){left=300;top=4;width=250;height=32;}
    if(this===option){left=740;width=40;height=32;}
    if(this===sceneBack){left=12.5;top=10;width=54;height=56;}
    if(this.id==='native-controls'){left=852;width=108;height=24;}
    if(this===toggle||this===help){width=40;height=32;}
    if(this===header&&this.style.width){width=parseFloat(this.style.width)*.5;height=parseFloat(this.style.height)*.5;}
    return {left,top,right:left+width,bottom:top+height,width,height};
  }
}
const root=new El(),body=new El(),stage=new El(),header=new El(),mods=new El(),label=new El(),
  option=new El('button'),back=new El('button'),sceneBack=new El('button'),toggle=new El(),help=new El('button'),popup=new El();
header.className='topbar';mods.className='mod-switch';
label.className='ms-label';label.textContent='Enable Mods';option.className='top-option';back.className='back';
mods.append(toggle,label,help);header.append(back,mods,option);stage.append(header,sceneBack);body.append(stage);popup.hidden=true;
const frames=[],listeners={},handlers={},calls=[],observed=[];
const doc={body,documentElement:root,createElement:name=>new El(name),createComment:()=>new El('comment'),
  getElementById:id=>id==='stage'?stage:body.descendants().find(el=>el.id===id),
  querySelector:selector=>body.querySelector(selector),querySelectorAll:()=>[popup]};
globalThis.document=doc;globalThis.innerWidth=960;globalThis.innerHeight=540;globalThis.pywebview={};
globalThis.requestAnimationFrame=fn=>frames.push(fn);globalThis.addEventListener=(name,fn)=>listeners[name]=fn;
globalThis.getComputedStyle=el=>({visibility:el===back&&back.hidden?'hidden':'visible'});
globalThis.MutationObserver=class {constructor(fn){this.fn=fn;}observe(target,opts){observed.push({target,opts,fn:this.fn});}};
let alignmentGate=null,layoutFailure=null;
// chrome-motion.js has its own tests: here an immersive scene's bar is away unless a test brings it back
const motion={immersive:false,revealed:false,synced:0,setup(opts){this.opts=opts;},setImmersive(on){this.immersive=on;},
  hidden(){return this.immersive&&!this.revealed;},apply(){},syncLights(){this.synced++;}};
let snapshot={active:true,valid:true,platform:'darwin',sequence:1,bounds:{left:12,top:8,right:80,bottom:30}};
globalThis.chromeFixture={...geometry,chromeMotion:()=>motion,on:(name,fn)=>handlers[name]=fn,rpc:async(name,value)=>{
  if(name==='window.chrome')return snapshot;calls.push({name,value});
  if(name==='window.layout'&&layoutFailure){const error=layoutFailure;layoutFailure=null;throw error;}},T:key=>key,
  reflowTopActions(){},alignTopBar:async()=>{if(alignmentGate)await alignmentGate;},fitStage(){},
  naturalTextWidth:(el,scale)=>Math.ceil(el.textContent.length*13*scale),paintTopBar:async()=>{}};
let source=readFileSync(new URL('../../src/ba_modding_toolkit/web/js/window-chrome.js',import.meta.url),'utf8').replace(/^import .*;$/gm,'');
source=`const {on,rpc,T,reflowTopActions,alignTopBar,fitStage,naturalTextWidth,paintTopBar,headerGeometry,rowLayout,isImmersive,dragRegions,expandRect,chromeMotion}=globalThis.chromeFixture;\n${source}`;
const {setupWindowChrome}=await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);
async function settle(){for(let i=0;i<8;i++){frames.splice(0).forEach(fn=>fn());await new Promise(resolve=>setImmediate(resolve));}}
const payload=()=>calls.filter(c=>c.name==='window.layout').at(-1).value;
const change=async()=>{listeners.resize();await settle();};
test('native and fallback controllers hide and restore every immersive scene; the bar still drags under a popup',async()=>{
  setupWindowChrome();assert.equal(doc.getElementById('native-controls'),undefined);await settle();
  assert.ok(observed.some(({target,opts})=>target===root&&opts.attributeFilter.includes('class')));
  for(const platform of ['darwin']){
    snapshot={...snapshot,sequence:snapshot.sequence+1,platform,bounds:platform==='darwin'?{left:12,top:8,right:80,bottom:30}:{left:812,top:0,right:960,bottom:32}};
    handlers['window.chrome'](snapshot);await settle();
    assert.ok(body.classList.has('native-chrome'));assert.equal(doc.getElementById('native-controls'),undefined);
    for(const [stageFlags,rootFlags] of [[['fullscreen'],[]],[['fullscreen'],[]],[['fullscreen'],['photo-active']],[['fullscreen'],['photo-active','photo-lobby']],[['hall-fullscreen'],[]],[[],['photo-active']]]){
      stage.classList=new Classes(stageFlags);root.classList=new Classes(rootFlags);await change();
      assert.ok(body.classList.has('window-immersive'));assert.equal(header.inert,true);
      assert.equal(globalThis.bamtHeaderGeometry.extra,0);assert.equal(sceneBack.inert,false);
      assert.deepEqual(payload().drag,[]);assert.equal(payload().state,'immersive');
      assert.equal(body.querySelectorAll('.window-drag-region').length,0);
      popup.hidden=false;await change();assert.deepEqual(payload().drag,[]);
      assert.equal(body.querySelectorAll('.window-drag-region').length,0);
      popup.hidden=true;stage.classList.clear();root.classList.clear();await change();
      assert.equal(body.classList.has('window-immersive'),false);assert.equal(header.inert,false);
      assert.ok(payload().drag.some(rect=>rect.bottom>12));assert.equal(payload().state,'');
    }
    // BL-078: the bar draws above every popup, so it keeps moving the window; only its controls wait.
    const free=payload().drag;
    popup.hidden=false;await change();
    assert.equal(header.inert,true);assert.deepEqual(payload().drag,free);assert.equal(payload().state,'');
    popup.hidden=true;await change();assert.equal(header.inert,false);assert.deepEqual(payload().drag,free);
  }
  // A slow decoded sprite cannot leave the previous full drag row over the
  // scene, and a late alignment completion cannot re-enable it under a popup.
  let release;alignmentGate=new Promise(resolve=>release=resolve);
  stage.classList.add('fullscreen');await change();assert.deepEqual(payload().drag,[]);
  popup.hidden=false;await change();assert.deepEqual(payload().drag,[]);
  alignmentGate=null;release();await settle();assert.deepEqual(payload().drag,[]);
  popup.hidden=true;await change();assert.deepEqual(payload().drag,[]);
  stage.classList.clear();await change();
  snapshot={...snapshot,active:false,sequence:snapshot.sequence+1};handlers['window.chrome'](snapshot);await settle();
  assert.equal(header.parent,stage);assert.equal(body.classList.has('native-chrome'),false);
  assert.equal(body.descendants().filter(el=>el.id==='native-controls').length,1);
  root.classList.add('photo-active');await change();
  assert.ok(body.classList.has('window-immersive'));assert.equal(body.querySelectorAll('.window-drag-region').length,0);
  popup.hidden=false;await change();assert.equal(body.querySelectorAll('.window-drag-region').length,0);
  popup.hidden=true;root.classList.clear();await change();
  assert.equal(body.classList.has('window-immersive'),false);assert.equal(body.querySelectorAll('.window-drag-region').length,0);
  // An empty layout always says why, so the native log never reports an unexplained empty bar at startup.
  assert.ok(calls.filter(c=>c.name==='window.layout').every(c=>c.value.drag.length||c.value.state));
});

test('standard Windows title bar leaves the prefab in the stage without native clearance or HTML buttons',async()=>{
  for(const active of [true,false]){
    snapshot={active,valid:active,platform:'win32',mode:'standard',bounds:null,sequence:snapshot.sequence+1};
    handlers['window.chrome'](snapshot);await settle();
    assert.equal(header.parent,stage);
    assert.equal(body.classList.has('native-chrome'),false);
    assert.equal(globalThis.bamtHeaderGeometry,null);
    assert.equal(globalThis.bamtWindowChrome,null);
    assert.equal(doc.getElementById('native-controls'),undefined);
    assert.equal(header.inert,false);
    for(const immersive of [true,false]){
      stage.classList.toggle('fullscreen',immersive);await change();
      assert.equal(body.classList.has('window-immersive'),immersive);
      assert.equal(body.querySelectorAll('.window-drag-region').length,0);
    }
  }
});

test('a refused layout goes to the report and is sent again on the next layout pass',async()=>{
  // BL-078: the layout used to be remembered before the native side took it, so a refused one was never sent again
  // and the top bar kept the old (often empty) drag regions.
  snapshot={active:true,valid:true,platform:'darwin',sequence:snapshot.sequence+1,bounds:{left:12,top:8,right:80,bottom:30}};
  handlers['window.chrome'](snapshot);await settle();
  stage.classList.clear();root.classList.clear();popup.hidden=true;await change();await change();
  const sent=()=>calls.filter(c=>c.name==='window.layout').length;
  back.hidden=true;layoutFailure=new Error('bad_params');await change();
  const refused=payload(),before=sent();
  const report=calls.find(c=>c.name==='app.frontendError');
  assert.equal(report.value.event.kind,'window-chrome');assert.equal(report.value.event.message,'bad_params');
  await change();
  assert.equal(sent(),before+1);assert.deepEqual(payload(),refused);
  await change();
  assert.equal(sent(),before+1,'an accepted layout is not sent again');
  back.hidden=false;await change();
});

test('a bar the pointer brought back in an immersive scene drags and clicks; it leaves with no drag regions again',async()=>{
  snapshot={active:true,valid:true,platform:'darwin',sequence:snapshot.sequence+1,bounds:{left:12,top:8,right:80,bottom:30},state:{fullscreen:false}};
  const synced=motion.synced;handlers['window.chrome'](snapshot);await settle();
  assert.equal(motion.synced,synced+1,'new native chrome takes the top bar offset');
  assert.equal(motion.opts.lights(),true);
  stage.classList=new Classes(['fullscreen']);root.classList.clear();popup.hidden=true;await change();
  assert.deepEqual(payload().drag,[]);assert.equal(payload().state,'immersive');assert.equal(header.inert,true);
  motion.revealed=true;await change();
  assert.equal(header.inert,false);assert.equal(payload().state,'');
  assert.ok(payload().drag.some(rect=>rect.bottom>12),'the revealed bar moves the window');
  assert.ok(payload().drag.every(rect=>rect.right<=88||rect.left>=142||rect.top>=60),'its Back still takes its own clicks');
  motion.revealed=false;await change();
  assert.deepEqual(payload().drag,[]);assert.equal(header.inert,true);
  snapshot={...snapshot,sequence:snapshot.sequence+1,state:{fullscreen:true}};handlers['window.chrome'](snapshot);await settle();
  assert.equal(motion.opts.lights(),false,'macOS fullscreen shows and hides the window buttons itself');
  stage.classList.clear();await change();
});

test('an unfinished ink alignment no longer keeps the bar undraggable after an immersive scene (BL-078)',async()=>{
  snapshot={active:true,valid:true,platform:'darwin',sequence:snapshot.sequence+1,bounds:{left:12,top:8,right:80,bottom:30},state:{fullscreen:false}};
  handlers['window.chrome'](snapshot);stage.classList.clear();root.classList.clear();popup.hidden=true;motion.revealed=false;await change();
  let release;alignmentGate=new Promise(resolve=>release=resolve);
  stage.classList.add('hall-fullscreen');await change();assert.deepEqual(payload().drag,[]);
  stage.classList.clear();await change();
  assert.ok(payload().drag.some(rect=>rect.bottom>12),'published before the alignment finished');
  assert.equal(payload().state,'');assert.equal(header.inert,false);
  // the bar the pointer brings back in a scene drags at once as well
  stage.classList.add('fullscreen');await change();motion.revealed=true;await change();
  assert.ok(payload().drag.length>0);
  motion.revealed=false;stage.classList.clear();alignmentGate=null;release();await settle();await change();
});
