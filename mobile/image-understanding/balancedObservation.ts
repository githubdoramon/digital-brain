import { IMAGE_OBSERVATION_SCHEMA_VERSION, type VisualObservation } from './types';

type BalancedFields = {
  scene: string | null;
  people: string | null;
  actions: string | null;
  importantObjects: string | null;
  setting: string | null;
  likelyEvent: string | null;
  uncertainty: string[];
};

const FIELD_NAMES = [
  'SCENE',
  'PEOPLE',
  'ACTIONS',
  'IMPORTANT_OBJECTS',
  'SETTING',
  'LIKELY_EVENT',
  'UNCERTAINTY',
] as const;

export const BALANCED_OBSERVATION_PROMPT_VERSION = 'balanced_observation_prompt.v2';

function compactDetectorEvidence(observation?: VisualObservation): string {
  if (!observation) return 'No detector evidence is available for this run.';
  return JSON.stringify({
    objects: observation.objects.map(({ label, count_min, count_max, details }) => ({
      label,
      count_min,
      count_max,
      positions: details,
    })),
    visible_text: observation.visible_text,
    people_presence: observation.people_presence,
    people_count_min: observation.people_count_min,
    people_count_max: observation.people_count_max,
    people_positions: observation.people_details,
  });
}

export function buildBalancedObservationPrompt(detectorObservation?: VisualObservation): string {
  return `You are creating a visual memory of what the user is seeing.

Study the attached image and describe what is happening. Prioritize:
1. The overall situation or event.
2. The people, animals, or important subjects involved.
3. Their actions, interactions, expressions, and visible roles.
4. Important objects and surroundings that explain the moment.

Supporting detector evidence appears below. It may contain people counts, object detections, positions, and OCR text. Use it as supporting evidence, especially for counts and readable text, but trust the image when the detector evidence is incomplete or clearly wrong.

The OCR text will be stored separately in the final observation. Use its meaning when it helps explain the scene, but do not transcribe or repeat it in your answer.

DETECTOR_EVIDENCE:
${compactDetectorEvidence(detectorObservation)}

Reply using these headings:

SCENE: A clear description of the visible moment.
PEOPLE: Who is present and useful visible details about them.
ACTIONS: What each person or important subject appears to be doing.
IMPORTANT_OBJECTS: Objects that help explain the activity or situation.
SETTING: Where the scene appears to take place.
LIKELY_EVENT: The most likely activity, interaction, or event.
UNCERTAINTY: Important uncertain details or plausible alternatives.

Be concise, but include all details needed to understand the moment. Clearly distinguish visible facts from interpretations. You may describe apparent age, gender, expression, relationships, and roles when useful. Do not invent a specific identity or name without supporting evidence.`;
}

function normalizeText(value: string): string | null {
  const normalized = value
    .replace(/\s+/g, ' ')
    .trim()
    .replace(/[.;]+$/, '');
  if (!normalized || /^(?:none|unknown|not visible|unclear)$/i.test(normalized)) return null;
  return normalized.slice(0, 1_200);
}

function extractField(raw: string, name: (typeof FIELD_NAMES)[number]): string {
  const headings = FIELD_NAMES.join('|');
  const match = raw.match(
    new RegExp(`(?:^|\\n|\\s)${name}:\\s*([\\s\\S]*?)(?=(?:\\n|\\s)(?:${headings}):|$)`, 'i'),
  );
  return match?.[1]?.trim() ?? '';
}

function splitUncertainties(value: string): string[] {
  const normalized = normalizeText(value);
  if (!normalized) return [];
  return normalized
    .split(/\s*\|\s*|\s*;\s*/)
    .map((item) => normalizeText(item))
    .filter((item): item is string => Boolean(item))
    .slice(0, 3);
}

function parseBalancedFields(raw: string): BalancedFields {
  const headingFriendlyRaw = raw.replace(/\*\*/g, '');
  const extracted = Object.fromEntries(
    FIELD_NAMES.map((name) => [name, normalizeText(extractField(headingFriendlyRaw, name))]),
  ) as Record<(typeof FIELD_NAMES)[number], string | null>;
  const hasStructuredField = Object.values(extracted).some(Boolean);
  return {
    scene: extracted.SCENE ?? (!hasStructuredField ? normalizeText(headingFriendlyRaw) : null),
    people: extracted.PEOPLE,
    actions: extracted.ACTIONS,
    importantObjects: extracted.IMPORTANT_OBJECTS,
    setting: extracted.SETTING,
    likelyEvent: extracted.LIKELY_EVENT,
    uncertainty: splitUncertainties(extracted.UNCERTAINTY ?? ''),
  };
}

function distinct(values: (string | null | undefined)[], limit: number): string[] {
  const seen = new Set<string>();
  const result: string[] = [];
  for (const value of values) {
    const normalized = value ? normalizeText(value) : null;
    if (!normalized) continue;
    const key = normalized.toLocaleLowerCase();
    if (seen.has(key)) continue;
    seen.add(key);
    result.push(normalized);
    if (result.length >= limit) break;
  }
  return result;
}

function fallbackSummary(fields: BalancedFields, detector?: VisualObservation): string {
  const generated = distinct(
    [fields.scene, fields.people, fields.actions, fields.importantObjects, fields.setting],
    5,
  );
  if (generated.length) return generated.join(' ');
  if (detector?.summary) return detector.summary;
  return 'The image was inspected, but the visual description could not be extracted.';
}

function peopleDetails(fields: BalancedFields, detector?: VisualObservation): string[] {
  return distinct([fields.people, ...(detector?.people_details ?? [])], 4);
}

function evidenceForInterpretation(fields: BalancedFields, detector?: VisualObservation): string[] {
  return distinct([fields.scene, fields.actions, fields.importantObjects, detector?.summary], 4);
}

export function buildBalancedObservation(
  rawOutput: string,
  detector?: VisualObservation,
): VisualObservation {
  const fields = parseBalancedFields(rawOutput);
  const summary = fallbackSummary(fields, detector);
  const interpretationEvidence = evidenceForInterpretation(fields, detector);
  const interpretations: VisualObservation['interpretations'] = fields.likelyEvent
    ? [
        {
          claim: fields.likelyEvent,
          evidence: interpretationEvidence.length ? interpretationEvidence : [summary],
          confidence: fields.uncertainty.length ? 'medium' : 'high',
        },
      ]
    : [];

  return {
    schema_version: IMAGE_OBSERVATION_SCHEMA_VERSION,
    summary,
    objects: detector?.objects ?? [],
    visible_text: detector?.visible_text ?? [],
    people_presence: detector?.people_presence ?? (fields.people ? 'possible' : 'none'),
    people_count_min: detector?.people_count_min ?? 0,
    people_count_max: detector?.people_count_max ?? (fields.people ? 1 : 0),
    people_details: peopleDetails(fields, detector),
    setting: fields.setting ?? detector?.setting ?? null,
    interpretations,
    uncertainties: [
      ...fields.uncertainty,
      ...(!detector
        ? ['No detector evidence was available to corroborate counts, objects, or OCR.']
        : []),
    ].slice(0, 3),
    person_identification_attempted: false,
  };
}
