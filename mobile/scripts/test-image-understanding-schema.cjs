/* eslint-env node */

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const ts = require('typescript');

const projectRoot = path.resolve(__dirname, '..');

function compile(fileName, requireModule) {
  const source = fs.readFileSync(path.join(projectRoot, fileName), 'utf8');
  const output = ts.transpileModule(source, {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2022,
    },
    fileName,
  }).outputText;
  const module = { exports: {} };
  const evaluate = new Function('require', 'module', 'exports', output);
  evaluate(requireModule, module, module.exports);
  return module.exports;
}

const types = compile('image-understanding/types.ts', require);
const schema = compile('image-understanding/observationSchema.ts', (specifier) => {
  if (specifier === './types') return types;
  return require(specifier);
});
const fastVision = compile('image-understanding/fastVisionObservation.ts', (specifier) => {
  if (specifier === './types') return types;
  return require(specifier);
});
const balanced = compile('image-understanding/balancedObservation.ts', (specifier) => {
  if (specifier === './types') return types;
  return require(specifier);
});

const validObservation = {
  schema_version: 'visual_observation.v2',
  summary: 'A table with two cups.',
  objects: [
    {
      label: 'cup',
      count_min: 2,
      count_max: 2,
      details: ['one light cup', 'one dark cup'],
    },
  ],
  visible_text: [],
  people_presence: 'possible',
  people_count_min: 0,
  people_count_max: 1,
  people_details: ['a partially visible arm'],
  setting: 'indoor table',
  interpretations: [
    {
      claim: 'The table may be in use.',
      evidence: ['two cups are visible'],
      confidence: 'low',
    },
  ],
  uncertainties: ['A person cannot be confirmed because only an arm-like shape is visible.'],
  person_identification_attempted: false,
};

const parsed = schema.parseVisualObservation(
  `\`\`\`json\n${JSON.stringify(validObservation)}\n\`\`\``,
);
assert.equal(parsed.schema_version, 'visual_observation.v2');
assert.equal(parsed.people_count_max, 1);

const invalid = structuredClone(validObservation);
invalid.people_count_min = 2;
invalid.people_count_max = 1;
assert.throws(
  () => schema.parseVisualObservation(JSON.stringify(invalid)),
  /People observation fields are malformed/,
);
assert.match(schema.IMAGE_OBSERVATION_PROMPT, /Never label or guess identity/);
assert.match(schema.IMAGE_OBSERVATION_PROMPT, /person_identification_attempted/);
assert.match(schema.IMAGE_OBSERVATION_PROMPT, /exact flat structure/);
assert.match(schema.IMAGE_OBSERVATION_PROMPT, /Do not use labels such as woman/);
assert.equal(types.IMAGE_OBSERVATION_PROMPT_VERSION, 'visual_observation_prompt.v4');

const nestedPeopleDetails = {
  ...validObservation,
  people_presence: 'present',
  people_count_min: 1,
  people_count_max: 1,
  people_details: [{ label: 'person 1', details: ['striped shirt', 'center'] }],
};
assert.deepEqual(
  schema.parseVisualObservationDetailed(JSON.stringify(nestedPeopleDetails)).observation
    .people_details,
  ['person 1: striped shirt, center'],
);
assert.match(
  schema.parseVisualObservationDetailed(JSON.stringify(nestedPeopleDetails)).repairs.join(' '),
  /Flattened object-shaped people_details/,
);

const prematureRoot = JSON.stringify(validObservation).replace(
  ',"people_presence"',
  '},"people_presence"',
);
const repairedRoot = schema.parseVisualObservationDetailed(prematureRoot);
assert.equal(repairedRoot.observation.people_presence, 'possible');
assert.match(repairedRoot.repairs.join(' '), /premature top-level closing brace/);

const trailingComma = JSON.stringify(validObservation).replace(
  '"person_identification_attempted":false}',
  '"person_identification_attempted":false,}',
);
const repairedComma = schema.parseVisualObservationDetailed(trailingComma);
assert.match(repairedComma.repairs.join(' '), /trailing commas/);

const missingClosure = JSON.stringify(validObservation).slice(0, -1);
const repairedClosure = schema.parseVisualObservationDetailed(missingClosure);
assert.match(repairedClosure.repairs.join(' '), /missing JSON closure/);

const privacyViolation = JSON.stringify({
  ...validObservation,
  person_identification_attempted: true,
}).replace(/}$/, ',}');
assert.throws(
  () => schema.parseVisualObservationDetailed(privacyViolation),
  /person_identification_attempted must be false/,
);

const fastObservation = fastVision.buildFastVisionObservation({
  imageWidth: 1000,
  imageHeight: 800,
  labels: [
    { text: 'Room', confidence: 0.91, index: 1 },
    { text: 'Table', confidence: 0.72, index: 2 },
    { text: 'Model', confidence: 0.96, index: 3 },
    { text: 'Muscle', confidence: 0.89, index: 4 },
    { text: 'Screenshot', confidence: 0.81, index: 5 },
  ],
  visibleText: ['Sample text'],
  textBlocks: [
    {
      text: 'Sample visible text spanning enough characters',
      lines: ['Sample visible text', 'spanning enough characters'],
      box: { left: 100, top: 20, right: 900, bottom: 160 },
    },
  ],
  scenes: [{ label: 'living room', confidence: 0.22, settingType: 'indoor' }],
  indoorProbability: 0.87,
  outdoorProbability: 0.13,
  componentErrors: [],
  detections: [
    {
      label: 'person',
      confidence: 0.88,
      index: 0,
      box: { left: 100, top: 100, right: 400, bottom: 700 },
    },
    {
      label: 'person',
      confidence: 0.72,
      index: 0,
      box: { left: 650, top: 150, right: 900, bottom: 650 },
    },
    {
      label: 'cup',
      confidence: 0.75,
      index: 47,
      box: { left: 430, top: 400, right: 520, bottom: 540 },
    },
    {
      label: 'frisbee',
      confidence: 0.26,
      index: 29,
      box: { left: 500, top: 400, right: 570, bottom: 470 },
    },
  ],
  timings: {
    imageDecodeMs: 10,
    textRecognitionMs: 20,
    imageLabelingMs: 30,
    objectDetectionMs: 40,
    sceneClassificationMs: 50,
    totalMs: 100,
  },
});
assert.equal(fastObservation.people_presence, 'present');
assert.equal(fastObservation.people_count_min, 2);
assert.equal(fastObservation.people_count_max, 2);
assert.equal(fastObservation.setting, 'likely indoor');
assert.equal(fastObservation.visible_text[0], 'Sample visible text spanning enough characters');
assert.match(fastObservation.summary, /Two people are detected/);
assert.match(fastObservation.summary, /scene classifier favors an indoor setting/);
assert.doesNotMatch(JSON.stringify(fastObservation), /muscle|may depict model/i);
assert.doesNotMatch(JSON.stringify(fastObservation.objects), /frisbee/i);
assert.match(JSON.stringify(fastObservation.interpretations), /prominent text/i);
assert.equal(fastObservation.person_identification_attempted, false);
assert.doesNotMatch(JSON.stringify(fastObservation.people_details), /woman|man|girl|boy/i);
assert.deepEqual(schema.parseVisualObservation(JSON.stringify(fastObservation)), fastObservation);

const partialFastObservation = fastVision.buildFastVisionObservation({
  imageWidth: 1000,
  imageHeight: 800,
  labels: [],
  visibleText: [],
  textBlocks: [],
  detections: [],
  scenes: [],
  indoorProbability: 0,
  outdoorProbability: 0,
  componentErrors: [
    {
      stage: 'text_recognition',
      message: 'ML Kit component failed after one client reinitialization retry.',
      errorCode: 13,
    },
  ],
  timings: {
    imageDecodeMs: 10,
    textRecognitionMs: 400,
    imageLabelingMs: 30,
    objectDetectionMs: 40,
    sceneClassificationMs: 50,
    totalMs: 480,
  },
});
assert.match(partialFastObservation.uncertainties.join(' '), /text recognition was unavailable/);
assert.deepEqual(
  schema.parseVisualObservation(JSON.stringify(partialFastObservation)),
  partialFastObservation,
);

const balancedRaw = [
  'SCENE: Two children are seated on a couch and looking toward a screen.',
  'PEOPLE: A girl in a striped dress and a boy in a blue shirt are sitting together.',
  'ACTIONS: Both children are watching the same screen while the boy holds a cup.',
  'IMPORTANT_OBJECTS: A couch, screen, and cup help explain the moment.',
  'SETTING: living room',
  'LIKELY_EVENT: The children are likely watching a program together.',
  'UNCERTAINTY: The screen content is not visible | Their relationship is uncertain.',
].join('\n');
const balancedObservation = balanced.buildBalancedObservation(balancedRaw, fastObservation);
assert.match(balancedObservation.summary, /seated on a couch/);
assert.match(balancedObservation.summary, /holds a cup/);
assert.equal(balancedObservation.people_count_min, fastObservation.people_count_min);
assert.deepEqual(balancedObservation.objects, fastObservation.objects);
assert.equal(balancedObservation.setting, 'living room');
assert.match(balancedObservation.people_details[0], /girl.*boy/i);
assert.match(balancedObservation.interpretations[0].claim, /watching a program/);
assert.deepEqual(balancedObservation.visible_text, fastObservation.visible_text);
assert.deepEqual(
  schema.parseVisualObservation(JSON.stringify(balancedObservation)),
  balancedObservation,
);

const inlineBalanced = balanced.buildBalancedObservation(
  'SCENE: A woman is preparing food. PEOPLE: One woman in a red apron. ACTIONS: She chops vegetables. IMPORTANT_OBJECTS: Knife and cutting board. SETTING: Kitchen. LIKELY_EVENT: Meal preparation. UNCERTAINTY: The recipe is unknown.',
  fastObservation,
);
assert.match(inlineBalanced.summary, /woman is preparing food/i);
assert.match(inlineBalanced.people_details[0], /woman in a red apron/i);
assert.equal(inlineBalanced.setting, 'Kitchen');
assert.match(inlineBalanced.interpretations[0].claim, /Meal preparation/);

const proseFallback = balanced.buildBalancedObservation(
  'One man in a yellow sweater is working at a desk with a laptop and notebook.',
  fastObservation,
);
assert.match(proseFallback.summary, /man in a yellow sweater/i);
const markdownHeadings = balanced.buildBalancedObservation(
  '**SCENE:** A family is eating outside.\n**SETTING:** A garden patio.\n**LIKELY_EVENT:** A shared meal.',
  fastObservation,
);
assert.match(markdownHeadings.summary, /family is eating outside/i);
assert.equal(markdownHeadings.setting, 'A garden patio');
assert.match(
  balanced.buildBalancedObservationPrompt(fastObservation),
  /OCR text will be stored separately/,
);
assert.doesNotMatch(balanced.buildBalancedObservationPrompt(fastObservation), /under 140 words/);
assert.doesNotMatch(balanced.buildBalancedObservationPrompt(fastObservation), /^TEXT:/m);
assert.match(balanced.buildBalancedObservationPrompt(fastObservation), /apparent age, gender/);

console.log('image-understanding schema tests passed');
