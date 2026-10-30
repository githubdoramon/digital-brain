/* global __dirname */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const ts = require('typescript');
const code = ts.transpileModule(fs.readFileSync(path.join(__dirname, '../chat/liveRefresh.ts'), 'utf8'),
  { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
const helpers = {};
new Function('exports', code)(helpers);
const flush = () => new Promise(setImmediate);

test('serial probes reload only changed revisions and stop on blur', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  let reads = 0, refreshes = 0, revision = 'one', resolve;
  const loop = helpers.createChatRefreshLoop({ canRefresh: () => true,
    readRevision: () => { reads++; return reads === 1 ? new Promise(r => { resolve = r; }) : Promise.resolve(revision); },
    refresh: async () => { refreshes++; return true; },
  });
  loop.start();
  t.mock.timers.tick(3000); await flush();
  assert.equal(reads, 1);
  resolve('one'); await flush();
  assert.equal(refreshes, 1);
  t.mock.timers.tick(3000); await flush();
  assert.equal(reads, 2); assert.equal(refreshes, 1);
  revision = 'two'; t.mock.timers.tick(3000); await flush();
  assert.equal(refreshes, 2);
  loop.pause(); t.mock.timers.tick(30000); await flush();
  assert.equal(reads, 3);
});

test('a response after backgrounding never updates the conversation', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  let resolve, updated = false, signal;
  const loop = helpers.createChatRefreshLoop({ canRefresh: () => true,
    readRevision: s => { signal = s; return new Promise(r => { resolve = r; }); },
    refresh: async () => { updated = true; return true; },
  });
  loop.start(); loop.pause(); assert.equal(signal.aborted, true);
  resolve('late'); await flush(); assert.equal(updated, false);
});

test('bounded requests recover even when a stalled transport ignores cancellation', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  let reads = 0;
  const loop = helpers.createChatRefreshLoop({ canRefresh: () => true,
    readRevision: () => { reads++; return new Promise(() => {}); }, refresh: async () => true,
  });
  loop.start(); t.mock.timers.tick(15000); await flush();
  t.mock.timers.tick(6000); await flush(); assert.equal(reads, 2);
  loop.pause(); await flush();
});

test('local sends pause refresh and cannot be overwritten by an earlier probe', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  let sending = false, resolve, refreshed = 0;
  const loop = helpers.createChatRefreshLoop({ canRefresh: () => !sending,
    readRevision: () => new Promise(r => { resolve = r; }),
    refresh: async () => { refreshed++; return true; },
  });
  loop.start(); sending = true; resolve('server'); await flush();
  assert.equal(refreshed, 0);
  loop.pause(); await flush();
});

// Execute the screen's real snapshot callback with controlled network/storage delays.
function screenSync(overrides = {}) {
  const source = ts.createSourceFile('brain.tsx', fs.readFileSync(path.join(__dirname, '../app/home/brain.tsx'), 'utf8'),
    ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  let arrow;
  function visit(node) {
    if (ts.isVariableDeclaration(node) && node.name.getText(source) === 'syncLatestThreadState') arrow = node.initializer.arguments[0];
    ts.forEachChild(node, visit);
  }
  visit(source);
  assert.ok(arrow);
  const updates = [];
  const scope = {
    token: 'test-token', allowed: true, isAuthLoading: false, isBootstrapping: false,
    isSendingRef: { current: false }, restoreGenerationRef: { current: 1 },
    chatIdentityRef: { current: { token: 'test-token', isMainChat: true, initialThreadId: null } },
    isMainChat: true, threadId: 'old-thread', pendingEventId: null,
    restoreChatHistory: async () => ({ threadId: null, pendingEventId: null, messages: [] }),
    loadThreadHistory: async () => { throw new Error('Unexpected history lookup'); },
    loadPendingRun: async () => null,
    setThreadId: id => updates.push(['thread', id]), setThreadTitle: () => {}, setPendingEventId: () => {},
    setMessages: fn => updates.push(['messages', fn([{ id: 'old', role: 'assistant', content: 'Old reply' }])]),
    starterMessages: [{ id: 'welcome', role: 'assistant', content: 'Welcome' }],
    mergePendingTurn: () => { throw new Error('Cannot merge a turn from the previous thread'); },
    ...overrides,
  };
  const compiled = ts.transpileModule(`exports.sync = ${arrow.getText(source)};`,
    { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText;
  const exports = {};
  new Function('exports', ...Object.keys(scope), compiled)(exports, ...Object.values(scope));
  return { sync: exports.sync, scope, updates };
}

test('a snapshot started before a local send cannot overwrite its completed answer', async () => {
  let resolve;
  const screen = screenSync({ restoreChatHistory: () => new Promise(r => { resolve = r; }) });
  const pending = screen.sync();
  screen.scope.isSendingRef.current = true;
  screen.scope.restoreGenerationRef.current++;
  screen.scope.isSendingRef.current = false;
  resolve({ threadId: 'old-thread', messages: [{ id: 'stale', content: 'Stale reply' }] });
  assert.equal(await pending, false); assert.deepEqual(screen.updates, []);
});

test('a result from another account or viewed thread is discarded', async () => {
  for (const identity of [
    { token: 'another-token', isMainChat: true, initialThreadId: null },
    { token: 'test-token', isMainChat: false, initialThreadId: 'another-thread' },
  ]) {
    let resolve;
    const screen = screenSync({ restoreChatHistory: () => new Promise(r => { resolve = r; }) });
    const pending = screen.sync(); screen.scope.chatIdentityRef.current = identity;
    resolve({ threadId: 'old-thread', messages: [] });
    assert.equal(await pending, false); assert.deepEqual(screen.updates, []);
  }
});

test('an externally reset main session clears the old conversation immediately', async () => {
  const screen = screenSync(); assert.equal(await screen.sync(), true);
  assert.deepEqual(screen.updates, [['thread', null], ['messages', screen.scope.starterMessages]]);
});
