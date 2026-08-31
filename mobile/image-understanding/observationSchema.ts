import { IMAGE_OBSERVATION_SCHEMA_VERSION, type Confidence, type VisualObservation } from './types';

const confidenceValues = new Set<Confidence>(['low', 'medium', 'high']);
const presenceValues = new Set(['none', 'possible', 'present']);
const TOP_LEVEL_KEYS = new Set([
  'schema_version',
  'summary',
  'objects',
  'visible_text',
  'people_presence',
  'people_count_min',
  'people_count_max',
  'people_details',
  'setting',
  'interpretations',
  'uncertainties',
  'person_identification_attempted',
]);

export type VisualObservationParseResult = {
  observation: VisualObservation;
  repairs: string[];
};

export const IMAGE_OBSERVATION_JSON_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  required: [
    'schema_version',
    'summary',
    'objects',
    'visible_text',
    'people_presence',
    'people_count_min',
    'people_count_max',
    'people_details',
    'setting',
    'interpretations',
    'uncertainties',
    'person_identification_attempted',
  ],
  properties: {
    schema_version: { const: IMAGE_OBSERVATION_SCHEMA_VERSION },
    summary: { type: 'string' },
    objects: {
      type: 'array',
      items: {
        type: 'object',
        additionalProperties: false,
        required: ['label', 'count_min', 'count_max', 'details'],
        properties: {
          label: { type: 'string' },
          count_min: { type: 'integer', minimum: 0 },
          count_max: { type: 'integer', minimum: 0 },
          details: { type: 'array', items: { type: 'string' } },
        },
      },
    },
    visible_text: { type: 'array', items: { type: 'string' } },
    people_presence: { enum: ['none', 'possible', 'present'] },
    people_count_min: { type: 'integer', minimum: 0 },
    people_count_max: { type: 'integer', minimum: 0 },
    people_details: { type: 'array', items: { type: 'string' } },
    setting: { type: ['string', 'null'] },
    interpretations: {
      type: 'array',
      items: {
        type: 'object',
        additionalProperties: false,
        required: ['claim', 'evidence', 'confidence'],
        properties: {
          claim: { type: 'string' },
          evidence: { type: 'array', items: { type: 'string' } },
          confidence: { enum: ['low', 'medium', 'high'] },
        },
      },
    },
    uncertainties: { type: 'array', items: { type: 'string' } },
    person_identification_attempted: { const: false },
  },
} as const;

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value);
}

function isStringArray(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((item) => typeof item === 'string');
}

function isCountRange(min: unknown, max: unknown): min is number {
  return (
    Number.isInteger(min) &&
    Number.isInteger(max) &&
    (min as number) >= 0 &&
    (max as number) >= (min as number)
  );
}

function normalizePeopleDetails(value: unknown, repairs: string[]): string[] | null {
  if (!Array.isArray(value)) return null;
  const normalized: string[] = [];
  let flattenedObject = false;
  for (const item of value) {
    if (typeof item === 'string') {
      normalized.push(item);
      continue;
    }
    if (!isRecord(item) || typeof item.label !== 'string' || !isStringArray(item.details)) {
      return null;
    }
    flattenedObject = true;
    normalized.push(item.details.length ? `${item.label}: ${item.details.join(', ')}` : item.label);
  }
  if (flattenedObject) repairs.push('Flattened object-shaped people_details entries to strings.');
  return normalized;
}

function removePrematureRootClosures(json: string): { json: string; changed: boolean } {
  let result = '';
  let inString = false;
  let escaped = false;
  const stack: string[] = [];
  let changed = false;

  for (let index = 0; index < json.length; index += 1) {
    const character = json[index];
    if (inString) {
      result += character;
      if (escaped) escaped = false;
      else if (character === '\\') escaped = true;
      else if (character === '"') inString = false;
      continue;
    }
    if (character === '"') {
      inString = true;
      result += character;
      continue;
    }
    if (character === '{' || character === '[') {
      stack.push(character);
      result += character;
      continue;
    }
    if (character === '}' && stack.length === 1 && stack[0] === '{') {
      const remaining = json.slice(index + 1);
      const nextProperty = remaining.match(/^\s*,\s*"([a-z_]+)"\s*:/);
      if (nextProperty && TOP_LEVEL_KEYS.has(nextProperty[1])) {
        changed = true;
        continue;
      }
    }
    if (character === '}' || character === ']') stack.pop();
    result += character;
  }
  return { json: result, changed };
}

function removeTrailingCommas(json: string): { json: string; changed: boolean } {
  let result = '';
  let inString = false;
  let escaped = false;
  let changed = false;
  for (let index = 0; index < json.length; index += 1) {
    const character = json[index];
    if (inString) {
      result += character;
      if (escaped) escaped = false;
      else if (character === '\\') escaped = true;
      else if (character === '"') inString = false;
      continue;
    }
    if (character === '"') {
      inString = true;
      result += character;
      continue;
    }
    if (character === ',') {
      const next = json.slice(index + 1).match(/^\s*([}\]])/);
      if (next) {
        changed = true;
        continue;
      }
    }
    result += character;
  }
  return { json: result, changed };
}

function appendMissingClosures(json: string): { json: string; appended: number } {
  let inString = false;
  let escaped = false;
  const stack: string[] = [];
  for (const character of json) {
    if (inString) {
      if (escaped) escaped = false;
      else if (character === '\\') escaped = true;
      else if (character === '"') inString = false;
      continue;
    }
    if (character === '"') inString = true;
    else if (character === '{' || character === '[') stack.push(character);
    else if (character === '}' && stack.at(-1) === '{') stack.pop();
    else if (character === ']' && stack.at(-1) === '[') stack.pop();
  }
  if (inString) return { json, appended: 0 };
  const suffix = stack
    .reverse()
    .map((opening) => (opening === '{' ? '}' : ']'))
    .join('');
  return { json: json + suffix, appended: suffix.length };
}

function extractJson(raw: string): { value: unknown; repairs: string[] } {
  const trimmed = raw
    .trim()
    .replace(/^```(?:json)?\s*/i, '')
    .replace(/\s*```$/i, '');
  try {
    return { value: JSON.parse(trimmed), repairs: [] };
  } catch {
    const start = trimmed.indexOf('{');
    const end = trimmed.lastIndexOf('}');
    if (start < 0) throw new Error('Model output did not contain a JSON object.');
    const candidates = [trimmed.slice(start)];
    if (end > start && end < trimmed.length - 1) candidates.push(trimmed.slice(start, end + 1));
    let lastError: unknown;
    for (const initialCandidate of candidates) {
      const repairs: string[] = [];
      let candidate = initialCandidate;
      if (start > 0 || initialCandidate.length < trimmed.length - start) {
        repairs.push('Extracted the outer JSON object from surrounding model text.');
      }
      const rootRepair = removePrematureRootClosures(candidate);
      candidate = rootRepair.json;
      if (rootRepair.changed) repairs.push('Removed a premature top-level closing brace.');
      const commaRepair = removeTrailingCommas(candidate);
      candidate = commaRepair.json;
      if (commaRepair.changed) repairs.push('Removed trailing commas before JSON closers.');
      const closureRepair = appendMissingClosures(candidate);
      candidate = closureRepair.json;
      if (closureRepair.appended > 0) {
        repairs.push(`Appended ${closureRepair.appended} missing JSON closure(s).`);
      }
      try {
        return { value: JSON.parse(candidate), repairs };
      } catch (error) {
        lastError = error;
      }
    }
    throw lastError;
  }
}

export function parseVisualObservationDetailed(raw: string): VisualObservationParseResult {
  const extracted = extractJson(raw);
  const { value, repairs } = extracted;
  if (!isRecord(value) || value.schema_version !== IMAGE_OBSERVATION_SCHEMA_VERSION) {
    throw new Error(`Expected schema_version ${IMAGE_OBSERVATION_SCHEMA_VERSION}.`);
  }
  if (typeof value.summary !== 'string') throw new Error('summary must be a string.');
  if (!Array.isArray(value.objects)) throw new Error('objects must be an array.');
  const objects = value.objects.map((item) => {
    if (
      !isRecord(item) ||
      typeof item.label !== 'string' ||
      !isCountRange(item.count_min, item.count_max) ||
      !isStringArray(item.details)
    ) {
      throw new Error('An object observation is malformed.');
    }
    return {
      label: item.label,
      count_min: item.count_min,
      count_max: item.count_max as number,
      details: item.details,
    };
  });
  if (!isStringArray(value.visible_text)) throw new Error('visible_text must be a string array.');
  const peopleDetails = normalizePeopleDetails(value.people_details, repairs);
  if (
    !presenceValues.has(String(value.people_presence)) ||
    !isCountRange(value.people_count_min, value.people_count_max) ||
    !peopleDetails
  ) {
    throw new Error('People observation fields are malformed.');
  }
  if (value.setting !== null && typeof value.setting !== 'string') {
    throw new Error('setting must be a string or null.');
  }
  if (!Array.isArray(value.interpretations)) throw new Error('interpretations must be an array.');
  const interpretations = value.interpretations.map((item) => {
    if (
      !isRecord(item) ||
      typeof item.claim !== 'string' ||
      !isStringArray(item.evidence) ||
      !confidenceValues.has(item.confidence as Confidence)
    ) {
      throw new Error('An interpretation is malformed.');
    }
    return {
      claim: item.claim,
      evidence: item.evidence,
      confidence: item.confidence as Confidence,
    };
  });
  if (!isStringArray(value.uncertainties)) {
    throw new Error('uncertainties must be a string array.');
  }
  if (value.person_identification_attempted !== false) {
    throw new Error('person_identification_attempted must be false.');
  }

  return {
    observation: {
      schema_version: IMAGE_OBSERVATION_SCHEMA_VERSION,
      summary: value.summary,
      objects,
      visible_text: value.visible_text,
      people_presence: value.people_presence as 'none' | 'possible' | 'present',
      people_count_min: value.people_count_min,
      people_count_max: value.people_count_max as number,
      people_details: peopleDetails,
      setting: value.setting,
      interpretations,
      uncertainties: value.uncertainties,
      person_identification_attempted: false,
    },
    repairs,
  };
}

export function parseVisualObservation(raw: string): VisualObservation {
  return parseVisualObservationDetailed(raw).observation;
}
