// FUN-15: the first-run sequence (cursor, start popups, update check, feature tour) runs once per session, also when the
// game folder is chosen on the no-game screen after a start without one. main.js's boot(), firstRun() and
// refreshConfiguration() run for real on stub collaborators.
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {test} from 'node:test';
const source=readFileSync(new URL('../../src/bmpt/web/js/main.js',import.meta.url),'utf8');
const refresh=source.slice(source.indexOf('async function refreshConfiguration()'),source.indexOf('\nlet routeToken'));
const sequence=source.slice(source.indexOf('let sessionReady = null'),source.indexOf('// The start popups, one at a time'));
const flush=async()=>{for(let i=0;i<40;i++)await Promise.resolve();};
const deferred=()=>{let resolve;const promise=new Promise(r=>{resolve=r;});return {promise,resolve};};
const NO_GAME={gameFound:false,consent:null,spineConsent:null,compiled:true,startupUpdate:true};
const GAME={...NO_GAME,gameFound:true};

function harness({first=NO_GAME}={}){
  const log=[],pending=[],popups=deferred(),scene=deferred();
  let hold=false;
  const env={
    Cursor:{init:()=>log.push(['cursor'])},
    Consent:{init:value=>log.push(['consent.init',value.gameFound])},
    Loading:{init:value=>log.push(['loading.init',value.gameFound]),timings:()=>({}),busy:()=>false,
      scene:async work=>{await work();if(hold)await scene.promise;}},
    setSpineConsent:value=>log.push(['spine',value]),
    Popup:{sequence:async steps=>{log.push(['popups',steps.length]);await popups.promise;},whenIdle:async()=>{}},
    ModSwitch:{startDue:(name,{info})=>name==='update'&&!!info.compiled&&info.startupUpdate!==false,current:()=>null},
    Update:{check:async()=>{log.push(['update']);}},
    Tours:{start:async options=>{log.push(['tour',options.info.gameFound,options.ready===api.ready()]);}},
    Prompts:{isBusy:()=>false},
    startPopups:()=>[['consent',async()=>{}],['spine',async()=>{}]],
    $:()=>({}),document:{getElementById:()=>null},fitStage(){},addEventListener(){},
    timed:async(_phase,work)=>work(),startup:{},chromeMotion:()=>({enter(){}}),
    start:async()=>{api.setInfo(first);api.setStartWithGame(first.gameFound);},
    rpc:async method=>{
      if(method==='app.ready'){log.push(['app.ready']);return null;}
      return new Promise(resolve=>pending.push({method,resolve}));
    },
    renderNav:async()=>{},StudentHall:{hide(){}},Detail:{hide(){}},Roster:{hide(){}},BatchImport:{invalidate(){}},
    previewCache:{configure(){}},NGUI:{init:async()=>{},setResourceContext:()=>false},forget(){},
    ctx:{invalidateRoster(){}},noGame:()=>log.push(['noGame']),route:async()=>log.push(['route']),endRoute(){},
  };
  const api=new Function('env',`const {${Object.keys(env)}}=env;
    let info=null,stepCards=null,configurationRevision=0,resourceEpoch=0,routeToken=0,routeAttempt=0;
    ${refresh}
    ${sequence}
    return {boot,refreshConfiguration,ready:()=>sessionReady,started:()=>firstRunStarted,setInfo:value=>{info=value;},setStartWithGame:value=>{startWithGame=value;}};`)(env);
  // answers the two requests (app.info, settings.get) of the oldest refresh still waiting
  const answer=async info=>{await flush();const [a,b]=[pending.shift(),pending.shift()];assert.equal(a.method,'app.info');assert.equal(b.method,'settings.get');a.resolve(info);b.resolve({});await flush();};
  return {...api,log,pending,popups,scene,answer,hold:()=>{hold=true;}};
}
const count=(h,name)=>h.log.filter(item=>item[0]===name).length;

test('a start with a game runs the sequence once, after the page told the server it is up',async()=>{
  const h=harness({first:GAME});
  const booting=h.boot();await flush();
  assert.deepEqual(h.log.map(item=>item[0]),['app.ready','cursor','popups','update']);
  h.popups.resolve();await booting;await flush();
  assert.equal(count(h,'cursor'),1);assert.equal(count(h,'tour'),1);
  assert.deepEqual(h.log.find(item=>item[0]==='tour'),['tour',true,true],'the tour gets the page\'s ready promise');
  assert.equal(count(h,'consent.init'),0,'a start with a game already holds the game data: no late re-read');
});

test('a game found later runs the sequence once, with the consent and loading data read again',async()=>{
  const h=harness();
  await h.boot();
  assert.equal(h.started(),false);
  assert.equal(count(h,'cursor'),0,'no game: nothing yet');
  assert.equal(count(h,'app.ready'),1,'the page still told the server it is up');
  const refreshing=h.refreshConfiguration();await h.answer(GAME);await refreshing;await flush();
  assert.deepEqual(h.log.slice(1).map(item=>item[0]).filter(name=>['route','consent.init','loading.init','spine','cursor','popups','update'].includes(name)),
    ['route','consent.init','spine','loading.init','cursor','popups','update'], 'the first screen first, then the data, then the sequence');
  assert.deepEqual(h.log.find(item=>item[0]==='consent.init'),['consent.init',true]);
  assert.deepEqual(h.log.find(item=>item[0]==='loading.init'),['loading.init',true]);
  assert.deepEqual(h.log.find(item=>item[0]==='spine'),['spine',null]);
  assert.equal(h.started(),true);
  h.popups.resolve();await flush();
  assert.equal(count(h,'tour'),1);
  // later refreshes (a server switch, a library move, any config.changed) never start it again
  for(let i=0;i<3;i++){const again=h.refreshConfiguration();await h.answer(GAME);await again;await flush();}
  assert.deepEqual(['cursor','update','tour','popups','consent.init'].map(name=>count(h,name)),[1,1,1,1,1]);
});

test('a refresh that still finds no game starts nothing, and the first one that finds a game starts it',async()=>{
  const h=harness();
  await h.boot();
  for(let i=0;i<2;i++){const missing=h.refreshConfiguration();await h.answer(NO_GAME);await missing;}
  assert.equal(count(h,'cursor'),0);assert.equal(count(h,'noGame'),2);assert.equal(h.started(),false);
  const found=h.refreshConfiguration();await h.answer(GAME);await found;await flush();
  assert.equal(count(h,'cursor'),1);
});

test('a refresh replaced by a newer one starts nothing; the newer one starts the sequence, once',async()=>{
  const h=harness();
  await h.boot();
  const old=h.refreshConfiguration(),newer=h.refreshConfiguration();
  await flush();
  assert.deepEqual(h.pending.map(request=>request.method),['app.info','settings.get','app.info','settings.get']);
  const [oldInfo,oldPrefs,newInfo,newPrefs]=h.pending.splice(0);
  newInfo.resolve(GAME);newPrefs.resolve({});await newer;await flush();
  assert.equal(count(h,'cursor'),1);
  oldInfo.resolve(GAME);oldPrefs.resolve({});await old;await flush();
  assert.equal(count(h,'cursor'),1,'the replaced refresh returns before it routes or starts anything');
  assert.equal(count(h,'route'),1);
});

test('a game found while boot() is still waiting leaves the sequence to boot(), and it runs once',async()=>{
  const h=harness();
  h.hold();
  const booting=h.boot();await flush();
  // the start data is in (no game), the page has not reported ready: a refresh finds the game now
  const refreshing=h.refreshConfiguration();await h.answer(GAME);await refreshing;await flush();
  assert.equal(h.ready(),null);assert.equal(count(h,'cursor'),0,'boot() has not told the server it is up yet');
  h.scene.resolve();await flush();
  assert.equal(count(h,'cursor'),1,'boot() starts it with the game it now sees');
  assert.deepEqual(h.log.find(item=>item[0]==='consent.init'),['consent.init',true],'boot() read the data of the game that was found');
  h.popups.resolve();await booting;await flush();
  const again=h.refreshConfiguration();await h.answer(GAME);await again;await flush();
  assert.deepEqual(['cursor','update','tour'].map(name=>count(h,name)),[1,1,1]);
});

test('the sequence has one entry, guarded, and boot() and the refresh both go through it',()=>{
  assert.equal(source.split('Cursor.init()').length-1,1);
  assert.equal(source.split('...startPopups()]').length-1,1);
  assert.equal(source.split('Tours.start(').length-1,1);
  assert.match(refresh,/firstRun\(\)\.catch\(console\.error\)/);
  assert.match(source,/Loading\.init\(info\);[^\n]*\n\s*startWithGame = !!info\.gameFound;/);
  assert.match(source,/async function boot\(\)[\s\S]*sessionReady = rpc\("app\.ready"[\s\S]*await firstRun\(\);/);
  assert.match(sequence,/if \(!sessionReady \|\| firstRunStarted \|\| !info\?\.gameFound\) return;\s*firstRunStarted = true;/);
});
