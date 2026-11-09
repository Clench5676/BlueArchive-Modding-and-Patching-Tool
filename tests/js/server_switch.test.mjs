import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {test} from 'node:test';
const source=readFileSync(new URL('../../src/bmpt/web/js/main.js',import.meta.url),'utf8');
const refresh=source.slice(source.indexOf('async function refreshConfiguration()'),source.indexOf('\nlet routeToken'));
test('old cross-server refreshes cannot commit after a newer refresh and recovery needs no restart',async()=>{
  const pending=[],routes=[],nav=[],started=[];
  const ctx={invalidateRoster(){}},env={ctx,
    previewCache:{configure(){}},
    rpc:method=>new Promise(resolve=>pending.push({method,resolve})),
    renderNav:async(_,options)=>nav.push(options),route:async()=>routes.push(ctx.info.resourceVersion),
    StudentHall:{hide(){}},Detail:{hide(){}},Roster:{hide(){}},BatchImport:{invalidate(){}},
    NGUI:{init:async()=>{}},noGame:()=>routes.push('missing'),
    // the first-run sequence (first_run.test.mjs): asked for after a committed refresh that found a game
    firstRun:async(...args)=>{started.push([ctx.info.resourceVersion,args.length]);}};
  const api=new Function('env',`const {${Object.keys(env)}}=env;let info,stepCards,configurationRevision=0,resourceEpoch=0,routeToken=0,routeAttempt=0;const endRoute=()=>{};${refresh};return refreshConfiguration;`)(env);
  const old=api(),latest=api();
  pending[2].resolve({gameFound:true,resourceVersion:'japan'});pending[3].resolve({fileSource:'windows_japan'});await latest;
  pending[0].resolve({gameFound:true,resourceVersion:'global'});pending[1].resolve({fileSource:'windows_global'});await old;
  assert.deepEqual(routes,['japan']);assert.equal(ctx.info.resourceVersion,'japan');assert.equal(nav.length,1);
  assert.deepEqual(started,[['japan',0]],'a stale refresh starts nothing');
  const missing=api();pending[4].resolve({gameFound:false});pending[5].resolve({fileSource:'windows_japan'});await missing;
  assert.equal(routes.at(-1),'missing');assert.equal(started.length,1,'no game: no first-run sequence');
  const recovery=api();pending[6].resolve({gameFound:true,resourceVersion:'recovered'});pending[7].resolve({fileSource:'windows_japan'});await recovery;
  assert.equal(routes.at(-1),'recovered');assert.ok(!refresh.includes('location.reload'));
  assert.deepEqual(started.at(-1),['recovered',0]);
});
test('old roster responses cannot replace a new server snapshot',async()=>{
  const code=source.slice(source.indexOf('// one roster.list at a time'),source.indexOf('// the portraits whose'));
  let resolve;const api=new Function('rpc',`let resourceEpoch=0,rosterData=null,rosterStale=true;const forgetReplaced=()=>{};${code};return {roster,change(){resourceEpoch++},data:()=>rosterData};`)(()=>new Promise(r=>resolve=r));
  const old=api.roster();api.change();resolve({families:['global']});await assert.rejects(old,{name:'AbortError'});assert.equal(api.data(),null);
});
test('roster readers share one roster.list, and a read begun before an invalidation does not settle it',async()=>{
  const code=source.slice(source.indexOf('// one roster.list at a time'),source.indexOf('// the portraits whose'));
  const calls=[];const api=new Function('rpc',`let resourceEpoch=0,rosterData=null,rosterStale=true;const forgetReplaced=()=>{};${code};return {roster,invalidate(){rosterStale=true;rosterGen+=1},stale:()=>rosterStale};`)(()=>new Promise(r=>calls.push(r)));
  const a=api.roster(),b=api.roster();assert.equal(calls.length,1,'one request for both');
  api.invalidate();const c=api.roster();assert.equal(calls.length,2,'an invalidation asks again');
  calls[0]({families:['old']});assert.deepEqual(await a,{families:['old']});assert.equal(await b,await a);
  assert.equal(api.stale(),true,'the older answer leaves the roster stale');
  calls[1]({families:['new']});assert.deepEqual(await c,{families:['new']});assert.equal(api.stale(),false);
  assert.deepEqual(await api.roster(),{families:['new']});assert.equal(calls.length,2);
});
