const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const ts = require('typescript');

const source = fs.readFileSync(path.join(__dirname, '../chat/voiceState.ts'), 'utf8');
const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText;
const voice = {};
new Function('exports', code)(voice);

test('dictated command punctuation does not become part of the command', () => {
  for (const text of ['slash event. Yesterday I met a friend.', '/event, Yesterday I met a friend.']) {
    assert.equal(voice.mergeTranscriptIntoDraft('', text), '/event Yesterday I met a friend.');
  }
  assert.equal(voice.normalizeTranscriptText('slash event!'), '/event');
  assert.equal(voice.normalizeTranscriptText('A sentence. Another sentence.'), 'A sentence. Another sentence.');
});
