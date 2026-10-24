/* global __dirname */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const ts = require('typescript');

const code = ts.transpileModule(
  fs.readFileSync(path.join(__dirname, '../utils/proposalActions.ts'), 'utf8'),
  { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } },
).outputText;
const helpers = {};
new Function('exports', code)(helpers);
const { proposalAction, pendingContactChoices, contactChoiceOptions } = helpers;
const person = (ref, display_name, birthday = null) => ({ ref, display_name, birthday });
const contactProposal = (people, relationships = []) => ({
  kind: 'contact_change',
  contact_name: 'general',
  payload: { contact_changes: { people, relationships } },
});

test('a new person is a contact creation, never a profession', () => {
  const action = proposalAction(contactProposal([person('new:mira', 'Mira')]));
  assert.equal(action.title, 'Mira');
  assert.equal(action.label, 'Create contact');
  assert.equal(action.supported, true);
});

test('birthdays retain their actual action, with unknown birth year', () => {
  const existing = proposalAction(contactProposal([person('contact:mira', 'Mira', '--05-12')]));
  assert.equal(existing.label, 'Add birthday');
  assert.equal(existing.title, 'Mira');
  const create = proposalAction(contactProposal([person('new:mira', 'Mira', '--05-12')]));
  assert.equal(create.label, 'Create contact and add birthday');
});

test('matching a proposed person to an existing contact changes the action', () => {
  const proposal = contactProposal([person('new:mira', 'Mira', '--05-12')]);
  assert.equal(proposalAction(proposal, { 'new:mira': 'existing-contact' }).label, 'Add birthday');
  assert.equal(
    proposalAction(proposal, { 'new:mira': 'create' }).label,
    'Create contact and add birthday',
  );
});

test('relationship changes describe a real relationship action', () => {
  const proposal = contactProposal(
    [person('contact:mira', 'Mira'), person('contact:alex', 'Alex')],
    [
      {
        from_ref: 'contact:mira',
        to_ref: 'contact:alex',
        relationship_type: 'Daughter',
        reciprocal_type: 'Parent',
      },
    ],
  );
  assert.equal(proposalAction(proposal).label, 'Add relationship');
  assert.equal(proposalAction(proposal).title, 'Mira · Alex');
});

test('only the explicit profession kind offers Add profession', () => {
  assert.equal(proposalAction({ kind: 'profession' }).label, 'Add profession');
  for (const proposal of [
    { kind: 'unknown_change' },
    { kind: 'contact_change' },
    contactProposal([]),
  ]) {
    const action = proposalAction(proposal);
    assert.equal(action.supported, false);
    assert.equal(action.label, 'Review suggestion');
  }
  assert.equal(proposalAction({ kind: 'task' }).label, 'Create task');
});

test('clarifications require a valid answer for every person, without defaulting to create', () => {
  const proposal = contactProposal([
    person('new:mira', 'Mira', '--05-12'),
    person('new:alex', 'Alex'),
  ]);
  const choices = [
    {
      ref: 'new:mira',
      display_name: 'Mira',
      can_create: true,
      candidates: [{ contact_id: 'mira-brooks', display_name: 'Mira Brooks' }],
    },
    {
      ref: 'new:alex',
      display_name: 'Alex',
      can_create: false,
      candidates: [{ contact_id: 'alex-hill', display_name: 'Alex Hill' }],
    },
  ];
  proposal.payload.contact_plan = { choices };
  assert.deepEqual(pendingContactChoices(proposal), choices);
  assert.equal(contactChoiceOptions(choices[0]).at(-1).value, 'create');
  assert.equal(contactChoiceOptions(choices[1]).length, 1);
  assert.deepEqual(pendingContactChoices(proposal, { 'new:mira': 'create' }), [choices[1]]);
  assert.deepEqual(
    pendingContactChoices(proposal, {
      'new:mira': 'unknown-contact',
      'new:alex': 'create',
    }),
    choices,
  );
  const resolutions = { 'new:mira': 'mira-brooks', 'new:alex': 'alex-hill' };
  assert.deepEqual(pendingContactChoices(proposal, resolutions), []);
  assert.equal(proposalAction(proposal, resolutions).label, 'Add birthday');
  // A refreshed candidate list must invalidate an answer that is no longer offered.
  choices[0].candidates = [];
  assert.deepEqual(pendingContactChoices(proposal, resolutions), [choices[0]]);
});
