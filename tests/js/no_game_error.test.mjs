import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {test} from 'node:test';
import {errorText as sharedErrorText} from '../../src/ba_modding_toolkit/web/js/i18n.js';
const source=readFileSync(new URL('../../src/ba_modding_toolkit/web/js/main.js',import.meta.url),'utf8');
const noGame=source.slice(source.indexOf('function noGame()'),source.indexOf('\nasync function refreshConfiguration()'));
const T=(key,params)=>`${key}${params?JSON.stringify(params):''}`;
const errorText=(err)=>sharedErrorText(err,T); // the shared error text (i18n.js) with this stand-in T
function run(failure){
  const notes=[],buttons=[];
  const env={ctx:{prefs:{fileSource:'windows_global'}},info:{native:true},T,errorText,
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

// UI-1: the screen's hint is the existing "Detect it, or choose the game folder", not the line that sends the user to a
// Settings that cannot open without a game; the Settings button always answers.
function screen({ native = false, detected = [] } = {}) {
  const shown = [], notes = [];
  const env = { ctx: { prefs: { fileSource: 'windows_global' } }, info: { native }, T, errorText,
    rpc: async method => (method === 'settings.detectGame' ? detected : '/picked'),
    refreshConfiguration: async () => {}, notify: (text, options) => notes.push({ text, options }),
    message: (title, hint, actions) => shown.push({ title, hint, actions }) };
  new Function('env', `const {${Object.keys(env)}}=env;${noGame};noGame();`)(env);
  return { shown, notes };
}
test('the no-game screen shows the no-game hint, and a Detect that finds nothing says the same as a notice', async () => {
  const { shown, notes } = screen();
  assert.equal(shown.length, 1);
  assert.equal(shown[0].title, 'app.noGame.title');
  assert.equal(shown[0].hint, 'app.noGame.hint');
  assert.ok(!noGame.includes('error.game_directory_missing'), 'the line that points at Settings is gone from this screen');
  await shown[0].actions[0][1]();
  assert.equal(notes.length, 1);
  assert.equal(notes[0].text, 'app.noGame.hint');
  assert.deepEqual(notes[0].options, { kind: 'error', error: { code: 'game_directory_missing' } });
  // a hit sets the folder instead of saying anything
  const found = screen({ detected: ['/found'] });
  await found.shown[0].actions[0][1]();
  assert.equal(found.notes.length, 0);
});

const settingsButton = source.slice(source.indexOf('async function openSettings('), source.indexOf('\n// without a game folder'));
function opener({ gameFound, failure = null } = {}) {
  const notices = [], notes = [], opened = [];
  const env = { info: { gameFound }, T, console: { error() {} }, Popup: { notice: text => notices.push(text) },
    notify: (text, options) => notes.push({ text, options }),
    Settings: { open: async (...args) => { opened.push(args); if (failure) throw failure; } } };
  const open = new Function('env', `const {${Object.keys(env)}}=env;${settingsButton};return openSettings;`)(env);
  return { open, notices, notes, opened };
}
test('the Settings button never fails silently: no game says the hint in the plain notice, a failure with a game is a failure', async () => {
  const changed = () => {};
  const noGameFound = opener({ gameFound: false, failure: new Error('layout UIPopup_Option_PC: HTTP 404') });
  await noGameFound.open(changed);
  assert.deepEqual(noGameFound.notices, ['app.noGame.hint']);
  assert.deepEqual(noGameFound.notes, []);
  assert.equal(noGameFound.opened[0][0], changed);
  const broken = opener({ gameFound: true, failure: Object.assign(new Error('x'), { code: 'internal', params: { error: 'x' } }) });
  await broken.open(changed);
  assert.deepEqual(broken.notices, []);
  assert.equal(broken.notes.length, 1);
  assert.equal(broken.notes[0].options.kind, 'error');
  assert.equal(broken.notes[0].options.error.code, 'internal');
  assert.ok(broken.notes[0].text.startsWith('error.internal'));
  const plain = opener({ gameFound: true, failure: new Error('boom') });
  await plain.open(changed);
  assert.ok(plain.notes[0].text.startsWith('error.internal'), 'an error without a code is the generic failure');
  const fine = opener({ gameFound: true });
  await fine.open(changed);
  assert.deepEqual([fine.notices, fine.notes], [[], []]);
});
test('the top bar button opens Settings through that handler, and no other path opens it with a bare click', () => {
  assert.match(source, /\$\("top-option"\)\.addEventListener\("click", \(\) => openSettings\(settingsChanged\)\)/);
  assert.ok(!/\$\("top-option"\)\.addEventListener\("click", \(\) => Settings\.open/.test(source));
});
