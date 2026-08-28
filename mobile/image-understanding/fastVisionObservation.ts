import type {
  FastVisionAnalysis,
  FastVisionDetection,
  FastVisionLabel,
} from '@/modules/fast-vision/src';

import { IMAGE_OBSERVATION_SCHEMA_VERSION, type Confidence, type VisualObservation } from './types';

const MIN_POSSIBLE_DETECTION_CONFIDENCE = 0.25;
const MIN_PRESENT_DETECTION_CONFIDENCE = 0.5;
const MIN_OBJECT_CONFIDENCE = 0.5;
const FORMAT_LABELS = new Set(['document', 'poster', 'receipt', 'screenshot', 'selfie']);
const CONTEXT_LABELS = new Set([
  'architecture',
  'auditorium',
  'beach',
  'building',
  'cafe',
  'classroom',
  'concert',
  'event',
  'factory',
  'farm',
  'forest',
  'garden',
  'gym',
  'kitchen',
  'landscape',
  'library',
  'market',
  'mountain',
  'office',
  'park',
  'restaurant',
  'road',
  'room',
  'stadium',
  'street',
  'vehicle',
]);

function confidence(value: number): Confidence {
  if (value >= 0.8) return 'high';
  if (value >= 0.55) return 'medium';
  return 'low';
}

function countLabel(value: number): string {
  return ['No', 'One', 'Two', 'Three', 'Four'][value] ?? String(value);
}

function normalizedLabel(label: string, index: number): string {
  return label.trim().toLowerCase() || `class ${index}`;
}

function detectionPosition(
  detection: FastVisionDetection,
  width: number,
  height: number,
): { horizontal: string; detail: string } {
  const centerX = (detection.box.left + detection.box.right) / 2 / width;
  const centerY = (detection.box.top + detection.box.bottom) / 2 / height;
  const horizontal = centerX < 0.34 ? 'left' : centerX > 0.66 ? 'right' : 'center';
  const vertical = centerY < 0.34 ? 'upper' : centerY > 0.66 ? 'lower' : 'middle';
  const area =
    Math.max(0, detection.box.right - detection.box.left) *
    Math.max(0, detection.box.bottom - detection.box.top);
  const coverage = Math.round((area / Math.max(1, width * height)) * 100);
  return {
    horizontal,
    detail: `${horizontal} ${vertical}, about ${Math.max(1, coverage)}% of the image; detector confidence ${Math.round(detection.confidence * 100)}%`,
  };
}

function selectVisibleText(analysis: FastVisionAnalysis): string[] {
  const blocks = analysis.textBlocks
    .map((block) => block.text.replace(/\s+/g, ' ').trim())
    .filter(Boolean);
  if (blocks.length) return blocks.slice(0, 20);
  const joined = analysis.visibleText
    .map((line) => line.trim())
    .filter(Boolean)
    .join(' ');
  return joined ? [joined] : [];
}

function findLabel(
  labels: FastVisionLabel[],
  candidates: Set<string>,
): FastVisionLabel | undefined {
  return labels.find(
    (label) => label.confidence >= 0.5 && candidates.has(label.text.trim().toLowerCase()),
  );
}

export function buildFastVisionObservation(analysis: FastVisionAnalysis): VisualObservation {
  const grouped = new Map<string, FastVisionDetection[]>();
  for (const detection of analysis.detections) {
    if (detection.confidence < MIN_POSSIBLE_DETECTION_CONFIDENCE) continue;
    const label = normalizedLabel(detection.label, detection.index);
    const current = grouped.get(label) ?? [];
    current.push(detection);
    grouped.set(label, current);
  }

  const objects = [...grouped.entries()]
    .filter(([label, detections]) => {
      return (
        label === 'person' || detections.some((item) => item.confidence >= MIN_OBJECT_CONFIDENCE)
      );
    })
    .map(([label, detections]) => {
      const reliable = detections.filter(
        (item) => item.confidence >= MIN_PRESENT_DETECTION_CONFIDENCE,
      );
      const retained = label === 'person' ? detections : reliable;
      return {
        label,
        count_min: reliable.length,
        count_max: retained.length,
        details: retained.slice(0, 4).map((item) => {
          return detectionPosition(item, analysis.imageWidth, analysis.imageHeight).detail;
        }),
      };
    })
    .sort((left, right) => right.count_min - left.count_min)
    .slice(0, 8);

  const people = (grouped.get('person') ?? []).sort(
    (left, right) => right.confidence - left.confidence,
  );
  const peopleCountMin = people.filter(
    (item) => item.confidence >= MIN_PRESENT_DETECTION_CONFIDENCE,
  ).length;
  const peopleCountMax = people.length;
  const visibleText = selectVisibleText(analysis);
  const visibleTextCharacters = visibleText.join(' ').length;
  const topScene = analysis.scenes[0];
  const indoorLikely = analysis.indoorProbability >= 0.65;
  const outdoorLikely = analysis.outdoorProbability >= 0.65;
  const setting = indoorLikely ? 'likely indoor' : outdoorLikely ? 'likely outdoor' : null;

  const summary: string[] = [];
  if (peopleCountMax > 0) {
    const positions = [
      ...new Set(
        people.map(
          (item) => detectionPosition(item, analysis.imageWidth, analysis.imageHeight).horizontal,
        ),
      ),
    ];
    const countDescription =
      peopleCountMin === peopleCountMax
        ? `${countLabel(peopleCountMax)} ${peopleCountMax === 1 ? 'person is' : 'people are'}`
        : `${countLabel(peopleCountMin)} ${peopleCountMin === 1 ? 'person is' : 'people are'} confirmed and up to ${peopleCountMax} people are`;
    summary.push(
      `${countDescription} detected in the ${positions.join(' and ')} portion${positions.length === 1 ? '' : 's'} of the image.`,
    );
  } else {
    summary.push('No people were detected above the configured threshold.');
  }
  if (setting) {
    const probability = indoorLikely ? analysis.indoorProbability : analysis.outdoorProbability;
    summary.push(
      `The scene classifier favors an ${setting.replace('likely ', '')} setting (${Math.round(probability * 100)}%).`,
    );
  }
  if (visibleTextCharacters >= 25) {
    summary.push(`Prominent visible text is present in ${visibleText.length} block(s).`);
  } else if (visibleText.length) {
    summary.push('A small amount of visible text was recognized.');
  }
  const otherObjects = objects.filter((item) => item.label !== 'person');
  if (otherObjects.length) {
    summary.push(`Other detected objects: ${otherObjects.map((item) => item.label).join(', ')}.`);
  }

  const interpretations: VisualObservation['interpretations'] = [];
  const formatLabel = findLabel(analysis.labels, FORMAT_LABELS);
  if (visibleTextCharacters >= 25) {
    interpretations.push({
      claim: formatLabel
        ? 'The image may be a screenshot, poster, receipt, document, or social-media frame with prominent text.'
        : 'The image may be a text-overlaid frame, poster, screenshot, or document.',
      evidence: [
        `${visibleTextCharacters} visible text characters were recognized across ${visibleText.length} block(s).`,
        ...(formatLabel
          ? [
              `The broad image labeler returned ${formatLabel.text.toLowerCase()} at ${Math.round(formatLabel.confidence * 100)}%.`,
            ]
          : []),
      ],
      confidence: formatLabel ? confidence(formatLabel.confidence) : 'low',
    });
  }
  if (topScene && topScene.confidence >= 0.05) {
    interpretations.push({
      claim: `The scene may resemble ${topScene.label}.`,
      evidence: [
        `Places365 ranked ${topScene.label} first at ${Math.round(topScene.confidence * 100)}%.`,
        ...(setting ? [`The aggregate classifier favors ${setting.replace('likely ', '')}.`] : []),
      ],
      confidence: topScene.confidence >= 0.2 ? 'medium' : 'low',
    });
  }
  const contextLabels = analysis.labels.filter(
    (label) => label.confidence >= 0.6 && CONTEXT_LABELS.has(label.text.trim().toLowerCase()),
  );
  if (contextLabels.length >= 2) {
    interpretations.push({
      claim: `The image may include ${contextLabels
        .slice(0, 2)
        .map((label) => label.text.toLowerCase())
        .join(' and ')} context.`,
      evidence: contextLabels.slice(0, 2).map((label) => {
        return `Broad label ${label.text.toLowerCase()} scored ${Math.round(label.confidence * 100)}%.`;
      }),
      confidence: 'low',
    });
  }

  return {
    schema_version: IMAGE_OBSERVATION_SCHEMA_VERSION,
    summary: summary.join(' '),
    objects,
    visible_text: visibleText,
    people_presence: peopleCountMin > 0 ? 'present' : peopleCountMax > 0 ? 'possible' : 'none',
    people_count_min: peopleCountMin,
    people_count_max: peopleCountMax,
    people_details: people.slice(0, 4).map((item, index) => {
      return `person ${index + 1}: ${detectionPosition(item, analysis.imageWidth, analysis.imageHeight).detail}`;
    }),
    setting,
    interpretations: interpretations.slice(0, 3),
    uncertainties: [
      'Small, occluded, cropped, or distant objects and people may not be detected.',
      ...(setting
        ? ['Scene classification is approximate and does not establish the activity or event.']
        : ['The scene classifier did not strongly distinguish indoor from outdoor.']),
      ...analysis.componentErrors.map((error) => {
        return `${error.stage.replaceAll('_', ' ')} was unavailable for this run.`;
      }),
    ].slice(0, 3),
    person_identification_attempted: false,
  };
}
