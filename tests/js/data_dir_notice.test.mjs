import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';

const main = readFileSync(new URL('../../src/ba_modding_toolkit/web/js/main.js', import.meta.url), 'utf8');
const notice = main.slice(main.indexOf('function dataDirNotice()'), main.indexOf('// The start popups, one at a time'));

function harness(info) {
  const shown = [];
  const env = {
    info,
    T: (key, params) => `${key}${params ? JSON.stringify(params) : ''}`,
    Popup: { message: async (options) => { shown.push(options); return { button: 'ok' }; } },
  };
  const run = new Function('env', `const { ${Object.keys(env).join(', ')} } = env; ${notice} return dataDirNotice;`)(env);
  return { run, shown };
}

test('a writable or unreported data folder shows nothing', async () => {
  for (const info of [null, {}, { dataDirWritable: true, dataDir: '/data' }]) {
    const { run, shown } = harness(info);
    await run();
    assert.deepEqual(shown, []);
  }
});

test('a data folder that cannot be written shows one notice: the quit title, the path, Confirm only', async () => {
  const { run, shown } = harness({ dataDirWritable: false, dataDir: '/app/data' });
  await run();
  assert.equal(shown.length, 1);
  const [options] = shown;
  assert.equal(options.title, 'quit.title');
  assert.deepEqual(options.lines, ['error.data_dir_readonly{"path":"/app/data"}']);
  assert.equal(options.ok, 'picker.ok');
  assert.equal(options.cancel, undefined);
  assert.equal(options.buttons, undefined);
  assert.equal(options.error, undefined, 'a notice, not a failure report');
});

function bootHarness(info) {
  const messages = [], sequences = [];
  const env = {
    info, startup: {}, fitStage() {}, addEventListener() {}, timed: (_phase, work) => work(), start: async () => {},
    rpc: async () => {}, Cursor: { init() {} }, $: () => ({}), console,
    T: (key, params) => `${key}${params ? JSON.stringify(params) : ''}`,
    Loading: { scene: (work) => work(), timings: () => ({}), busy: () => false, init() {} },
    Popup: {
      message: async (options) => { messages.push(options); return { button: 'ok' }; },
      sequence: async (steps) => { sequences.push(steps.map(([name]) => name)); for (const [, run] of steps) await run(); },
      whenIdle: async () => {},
    },
    ModSwitch: { current: () => ({}), startDue: () => false },
    Tours: { start: async () => {} },
    document: { getElementById: () => null },
    Consent: { show: async () => true, spine: async () => true, scan: async () => {} },
    Prompts: { start: async () => {}, isBusy: () => false },
    Update: { check: async () => null },
    route: async () => {},
  };
  const code = main.slice(main.indexOf('async function boot('), main.indexOf('async function start()'));
  const boot = new Function('env', `const { ${Object.keys(env).join(', ')} } = env; ${code} return boot;`)(env);
  return { boot, messages, sequences };
}

test('the start shows the notice once, ahead of the other start popups', async () => {
  const h = bootHarness({ gameFound: true, dataDirWritable: false, dataDir: '/app/data', consent: true, spineConsent: true });
  await h.boot();
  assert.equal(h.sequences.length, 1);
  assert.deepEqual(h.sequences[0], ['data-dir', 'consent', 'spine', 'scan', 'drift', 'untracked']);
  assert.equal(h.messages.length, 1);
  assert.equal(h.messages[0].title, 'quit.title');
});

test('a writable data folder leaves the start popups as they were', async () => {
  for (const dataDirWritable of [true, undefined]) {
    const h = bootHarness({ gameFound: true, dataDirWritable, dataDir: '/app/data' });
    await h.boot();
    assert.deepEqual(h.sequences, [['consent', 'spine', 'scan', 'drift', 'untracked']]);
    assert.deepEqual(h.messages, []);
  }
});

test('the no-game screen shows the notice too, and only when the folder cannot be written', async () => {
  const bad = bootHarness({ gameFound: false, dataDirWritable: false, dataDir: '/app/data' });
  await bad.boot();
  assert.equal(bad.messages.length, 1);
  assert.deepEqual(bad.sequences, [], 'no other start popup without a game');
  const good = bootHarness({ gameFound: false, dataDirWritable: true, dataDir: '/app/data' });
  await good.boot();
  assert.deepEqual(good.messages, []);
});
