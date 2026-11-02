import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {test} from 'node:test';
const source=readFileSync(new URL('../../src/ba_modding_toolkit/web/js/main.js',import.meta.url),'utf8');
const noGame=source.slice(source.indexOf('function noGame()'),source.indexOf('\nasync function refreshConfiguration()'));
const T=(key,params)=>`${key}${params?JSON.stringify(params):''}`;
function run(failure){
  const notes=[],buttons=[];
  const env={ctx:{prefs:{fileSource:'windows_global'}},info:{native:true},T,
    rpc:async method=>{if(method==='settings.pickFolder')return '/picked';if(method==='settings.set')throw failure;return [];},
    refreshConfiguration:async()=>{},notify:(text,options)=>notes.push({text,options}),
    message:(title,text,list)=>buttons.push(...list)};
  new Function('env',`const {${Object.keys(env)}}=env;${noGame};noGame();`)(env);
  return {notes,buttons};
}
test('choosing a folder the server refuses shows the refusal, and an error without a code shows the generic failure',async()=>{
  const flush=()=>new Promise(resolve=>setTimeout(resolve,0));
  for(const [failure,expected] of [[{code:'game_not_found'},'error.game_not_found'],[new Error('boom'),'error.internal'],[{},'error.internal']]){
    const {notes,buttons}=run(failure);
    await buttons.at(-1)[1]();await flush();
    assert.equal(notes.length,1);
    assert.ok(notes[0].text.startsWith(expected),notes[0].text);
    assert.ok(!notes[0].text.includes('undefined'),notes[0].text);
  }
});
test('no error text in main.js reads a missing code as a key',()=>{
  assert.ok(!/error\.\$\{err\.code\}/.test(source));
  assert.ok(!/error\.\$\{error\.code\}/.test(source));
});
