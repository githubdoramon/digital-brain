import Ionicons from '@expo/vector-icons/Ionicons';
import * as Clipboard from 'expo-clipboard';
import { LinearGradient } from 'expo-linear-gradient';
import { useRouter } from 'expo-router';
import React from 'react';
import { Animated, Image, type ImageStyle, Share, StyleSheet, Text, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { Button } from '@/components/Button';
import { Card } from '@/components/Card';
import {
  COLLAPSING_CONTENT_TOP_PADDING,
  COLLAPSING_SECONDARY_TITLE_BLOCK_HEIGHT,
  COLLAPSING_TOP_BAR_HEIGHT,
  CollapsingTopBar,
} from '@/components/CollapsingTopBar';
import { useAppNotice } from '@/hooks/useAppNotice';
import { useSingleImagePicker } from '@/hooks/useImagePicker';
import { imageUnderstandingCoordinator } from '@/image-understanding/coordinator';
import {
  clearImageUnderstandingRunHistory,
  readImageUnderstandingRunHistory,
  serializeImageUnderstandingRuns,
} from '@/image-understanding/runHistory';
import type {
  EngineModelState,
  EngineProgress,
  ImageUnderstandingEngineId,
  ImageUnderstandingRunRecord,
  VisualObservation,
} from '@/image-understanding/types';
import { theme } from '@/theme';

type SelectedPhoto = {
  uri: string;
  width: number | null;
  height: number | null;
};

const emptyState: EngineModelState = {
  downloaded: false,
  modelSizeBytes: null,
  loaded: false,
  compatibilityWarning: null,
};

function formatBytes(value: number | null): string {
  if (value == null || !Number.isFinite(value)) return 'Not exposed';
  if (value < 1024) return `${value} B`;
  const units = ['KB', 'MB', 'GB', 'TB'];
  let size = value;
  let unit = -1;
  do {
    size /= 1024;
    unit += 1;
  } while (size >= 1024 && unit < units.length - 1);
  return `${size.toFixed(size >= 100 ? 0 : 1)} ${units[unit]}`;
}

function formatDuration(value: number | null): string {
  if (value == null) return 'Not available';
  return value >= 1000 ? `${(value / 1000).toFixed(2)} s` : `${value} ms`;
}

function formatCount(value: number | null | undefined): string {
  return value == null || !Number.isFinite(value) ? 'Not exposed' : String(value);
}

function formatRate(value: number | null | undefined): string {
  return value == null || !Number.isFinite(value) ? 'Not exposed' : `${value.toFixed(2)} tok/s`;
}

function currentObservation(value: unknown): VisualObservation | null {
  if (
    !value ||
    typeof value !== 'object' ||
    (value as { schema_version?: unknown }).schema_version !== 'visual_observation.v2'
  ) {
    return null;
  }
  return value as VisualObservation;
}

function PipelineCard({
  states,
  progress,
  busy,
  hasPhoto,
  onDownload,
  onRun,
  onDelete,
}: {
  states: Record<ImageUnderstandingEngineId, EngineModelState>;
  progress: EngineProgress | null;
  busy: boolean;
  hasPhoto: boolean;
  onDownload: () => void;
  onRun: () => void;
  onDelete: () => void;
}) {
  const progressPercent =
    progress?.progress == null
      ? null
      : Math.round(Math.max(0, Math.min(1, progress.progress)) * 100);
  return (
    <Card style={styles.card}>
      <View style={styles.titleRow}>
        <View style={styles.engineIcon}>
          <Ionicons name="hardware-chip-outline" size={21} color={theme.colors.teal} />
        </View>
        <View style={styles.flex}>
          <Text style={styles.cardTitle}>Full image-understanding pipeline</Text>
          <Text style={styles.caption}>Fast detector/OCR evidence + Balanced visual memory</Text>
        </View>
        <View
          style={[
            styles.pill,
            states['fast-vision'].downloaded && states['balanced-vlm'].downloaded
              ? styles.readyPill
              : styles.emptyPill,
          ]}
        >
          <Text style={styles.pillText}>
            {states['fast-vision'].downloaded && states['balanced-vlm'].downloaded
              ? 'Ready'
              : 'Setup needed'}
          </Text>
        </View>
      </View>

      <View style={styles.metaBlock}>
        <Text style={styles.meta}>Narrative model: LFM2.5-VL-450M quantized</Text>
        <Text style={styles.meta}>Evidence: ML Kit OCR + EfficientDet-Lite0</Text>
        <Text style={styles.meta}>Execution: serialized CPU / XNNPACK</Text>
        <Text style={styles.meta}>
          Local size:{' '}
          {formatBytes(
            states['fast-vision'].modelSizeBytes != null &&
              states['balanced-vlm'].modelSizeBytes != null
              ? states['fast-vision'].modelSizeBytes + states['balanced-vlm'].modelSizeBytes
              : null,
          )}
        </Text>
        <Text style={styles.meta}>Models unload after every image.</Text>
      </View>

      {states['fast-vision'].compatibilityWarning || states['balanced-vlm'].compatibilityWarning ? (
        <View style={styles.warningBox}>
          <Ionicons name="warning-outline" size={18} color="#9a5a20" />
          <Text style={styles.warningText}>
            {states['fast-vision'].compatibilityWarning ??
              states['balanced-vlm'].compatibilityWarning}
          </Text>
        </View>
      ) : null}

      {progress ? (
        <View style={styles.progressBlock}>
          <Text style={styles.progressText}>{progress.detail}</Text>
          {progressPercent != null ? (
            <View style={styles.progressTrack}>
              <View style={[styles.progressFill, { width: `${progressPercent}%` }]} />
            </View>
          ) : null}
        </View>
      ) : null}

      <View style={styles.buttonRow}>
        <Button
          label={
            states['fast-vision'].downloaded && states['balanced-vlm'].downloaded
              ? 'Check pipeline files'
              : 'Prepare pipeline'
          }
          onPress={onDownload}
          disabled={busy}
          variant="secondary"
          style={styles.rowButton}
        />
        <Button
          label="Analyze selected image"
          onPress={onRun}
          disabled={
            busy ||
            !hasPhoto ||
            Boolean(
              states['fast-vision'].compatibilityWarning ||
              states['balanced-vlm'].compatibilityWarning,
            )
          }
          style={styles.rowButton}
        />
      </View>
      <Button
        label="Unload and delete pipeline files"
        onPress={onDelete}
        disabled={busy || (!states['fast-vision'].downloaded && !states['balanced-vlm'].downloaded)}
        variant="danger"
      />
    </Card>
  );
}

function RunCard({ run, onCopy }: { run: ImageUnderstandingRunRecord; onCopy: () => void }) {
  const observation = currentObservation(run.observation);
  return (
    <Card style={styles.card}>
      <View style={styles.titleRow}>
        <View style={styles.flex}>
          <Text style={styles.cardTitle}>Full pipeline result</Text>
          <Text style={styles.caption}>{new Date(run.timestamp).toLocaleString()}</Text>
        </View>
        <View style={[styles.pill, run.outputValid ? styles.readyPill : styles.errorPill]}>
          <Text style={styles.pillText}>
            {run.outputValid ? 'Valid output' : 'Invalid / failed'}
          </Text>
        </View>
      </View>

      <View style={styles.metricsGrid}>
        <View style={styles.metricCell}>
          <Text style={styles.metricLabel}>Cold load</Text>
          <Text style={styles.metricValue}>{formatDuration(run.measurements.coldLoadMs)}</Text>
        </View>
        <View style={styles.metricCell}>
          <Text style={styles.metricLabel}>Inference</Text>
          <Text style={styles.metricValue}>{formatDuration(run.measurements.inferenceMs)}</Text>
        </View>
        <View style={styles.metricCell}>
          <Text style={styles.metricLabel}>Model</Text>
          <Text style={styles.metricValue}>{formatBytes(run.measurements.modelSizeBytes)}</Text>
        </View>
        <View style={styles.metricCell}>
          <Text style={styles.metricLabel}>Peak memory</Text>
          <Text style={styles.metricValue}>{formatBytes(run.measurements.peakMemoryBytes)}</Text>
        </View>
        <View style={styles.metricCell}>
          <Text style={styles.metricLabel}>Current memory</Text>
          <Text style={styles.metricValue}>{formatBytes(run.measurements.currentMemoryBytes)}</Text>
        </View>
        <View style={styles.metricCell}>
          <Text style={styles.metricLabel}>Prompt tokens</Text>
          <Text style={styles.metricValue}>{formatCount(run.measurements.promptTokens)}</Text>
        </View>
        <View style={styles.metricCell}>
          <Text style={styles.metricLabel}>Output tokens</Text>
          <Text style={styles.metricValue}>{formatCount(run.measurements.completionTokens)}</Text>
        </View>
        <View style={styles.metricCell}>
          <Text style={styles.metricLabel}>First token</Text>
          <Text style={styles.metricValue}>
            {formatDuration(run.measurements.timeToFirstTokenMs)}
          </Text>
        </View>
        <View style={styles.metricCell}>
          <Text style={styles.metricLabel}>Throughput</Text>
          <Text style={styles.metricValue}>{formatRate(run.measurements.tokensPerSecond)}</Text>
        </View>
        {run.measurements.imageDecodeMs != null ? (
          <View style={styles.metricCell}>
            <Text style={styles.metricLabel}>Image decode</Text>
            <Text style={styles.metricValue}>{formatDuration(run.measurements.imageDecodeMs)}</Text>
          </View>
        ) : null}
        {run.measurements.textRecognitionMs != null ? (
          <View style={styles.metricCell}>
            <Text style={styles.metricLabel}>OCR</Text>
            <Text style={styles.metricValue}>
              {formatDuration(run.measurements.textRecognitionMs)}
            </Text>
          </View>
        ) : null}
        {run.measurements.imageLabelingMs != null ? (
          <View style={styles.metricCell}>
            <Text style={styles.metricLabel}>Image labels</Text>
            <Text style={styles.metricValue}>
              {formatDuration(run.measurements.imageLabelingMs)}
            </Text>
          </View>
        ) : null}
        {run.measurements.objectDetectionMs != null ? (
          <View style={styles.metricCell}>
            <Text style={styles.metricLabel}>Object detection</Text>
            <Text style={styles.metricValue}>
              {formatDuration(run.measurements.objectDetectionMs)}
            </Text>
          </View>
        ) : null}
        {run.measurements.sceneClassificationMs != null ? (
          <View style={styles.metricCell}>
            <Text style={styles.metricLabel}>Scene</Text>
            <Text style={styles.metricValue}>
              {formatDuration(run.measurements.sceneClassificationMs)}
            </Text>
          </View>
        ) : null}
      </View>

      {run.error ? <Text style={styles.errorText}>{run.error}</Text> : null}
      {(run.parseRepairs ?? []).length ? (
        <View style={styles.resultBlock}>
          <Text style={styles.resultHeading}>Parser repairs</Text>
          <Text style={styles.body}>{(run.parseRepairs ?? []).join('\n')}</Text>
        </View>
      ) : null}
      {observation ? (
        <View style={styles.resultBlock}>
          <Text style={styles.resultHeading}>Visible evidence</Text>
          <Text style={styles.body}>{observation.summary}</Text>
          <Text style={styles.resultHeading}>Setting</Text>
          <Text style={styles.body}>{observation.setting ?? 'Unknown'}</Text>
          <Text style={styles.resultHeading}>People observation</Text>
          <Text style={styles.body}>
            {observation.people_presence}; count {observation.people_count_min}–
            {observation.people_count_max}
            {observation.people_details.length ? `\n${observation.people_details.join('\n')}` : ''}
          </Text>
          <Text style={styles.resultHeading}>Detected objects</Text>
          <Text style={styles.body}>
            {observation.objects.length
              ? observation.objects
                  .map(
                    (item) =>
                      `${item.label}: ${item.count_min}–${item.count_max}${
                        item.details.length ? ` (${item.details.join('; ')})` : ''
                      }`,
                  )
                  .join('\n')
              : 'None'}
          </Text>
          <Text style={styles.resultHeading}>OCR text</Text>
          <Text style={styles.body}>
            {observation.visible_text.length ? observation.visible_text.join('\n') : 'None'}
          </Text>
          <Text style={styles.resultHeading}>Interpretations</Text>
          <Text style={styles.body}>
            {observation.interpretations.length
              ? observation.interpretations
                  .map((item) => `${item.claim} (${item.confidence})`)
                  .join('\n')
              : 'None'}
          </Text>
          <Text style={styles.resultHeading}>Uncertainties</Text>
          <Text style={styles.body}>
            {observation.uncertainties.length ? observation.uncertainties.join('\n') : 'None'}
          </Text>
        </View>
      ) : null}
      {run.rawOutput ? (
        <View style={styles.rawBlock}>
          <Text style={styles.resultHeading}>Raw output</Text>
          <Text selectable style={styles.mono}>
            {run.rawOutput}
          </Text>
        </View>
      ) : null}
      <View style={styles.rawBlock}>
        <Text style={styles.resultHeading}>Process trace</Text>
        <Text selectable style={styles.mono}>
          {(run.processLog ?? []).length
            ? (run.processLog ?? [])
                .map((entry) => {
                  const measurements = entry.measurements
                    ? ` ${JSON.stringify(entry.measurements)}`
                    : '';
                  return `${entry.timestamp} +${entry.elapsedMs}ms [${entry.stage}] ${entry.message}${measurements}`;
                })
                .join('\n')
            : 'No process trace was captured for this older run.'}
        </Text>
      </View>
      <Button label="Copy run JSON" onPress={onCopy} variant="secondary" />
    </Card>
  );
}

export default function ImageUnderstandingPocScreen() {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const scrollY = React.useRef(new Animated.Value(0)).current;
  const { showError, showSuccess } = useAppNotice();
  const { pickSingleImage, imagePickerSheet } = useSingleImagePicker();
  const [selectedPhoto, setSelectedPhoto] = React.useState<SelectedPhoto | null>(null);
  const [states, setStates] = React.useState<Record<ImageUnderstandingEngineId, EngineModelState>>({
    'fast-vision': emptyState,
    'balanced-vlm': emptyState,
    'litert-lm': emptyState,
  });
  const [progress, setProgress] = React.useState<
    Record<ImageUnderstandingEngineId, EngineProgress | null>
  >({ 'fast-vision': null, 'balanced-vlm': null, 'litert-lm': null });
  const [history, setHistory] = React.useState<ImageUnderstandingRunRecord[]>([]);
  const [busy, setBusy] = React.useState(false);

  const refresh = React.useCallback(async () => {
    try {
      const [nextStates, nextHistory] = await Promise.all([
        imageUnderstandingCoordinator.inspectAll(),
        readImageUnderstandingRunHistory(),
      ]);
      setStates(nextStates);
      setHistory(nextHistory);
    } catch (error) {
      showError(error instanceof Error ? error.message : String(error));
    }
  }, [showError]);

  React.useEffect(() => {
    void refresh();
    return () => {
      void imageUnderstandingCoordinator.unloadAll();
    };
  }, [refresh]);

  const updateProgress = React.useCallback(
    (engineId: ImageUnderstandingEngineId, next: EngineProgress) => {
      setProgress((current) => ({ ...current, [engineId]: next.stage === 'idle' ? null : next }));
    },
    [],
  );

  const pickPhoto = React.useCallback(async () => {
    try {
      const asset = await pickSingleImage();
      if (!asset) return;
      setSelectedPhoto({
        uri: asset.uri,
        width: typeof asset.width === 'number' ? asset.width : null,
        height: typeof asset.height === 'number' ? asset.height : null,
      });
    } catch (error) {
      showError(error instanceof Error ? error.message : String(error));
    }
  }, [pickSingleImage, showError]);

  const runOperation = React.useCallback(
    async (operation: () => Promise<unknown>, success?: string) => {
      setBusy(true);
      let succeeded = false;
      try {
        await operation();
        succeeded = true;
      } catch (error) {
        showError(error instanceof Error ? error.message : String(error));
      } finally {
        await refresh();
        setBusy(false);
      }
      if (succeeded && success) showSuccess(success);
    },
    [refresh, showError, showSuccess],
  );

  const runPipeline = React.useCallback(() => {
    if (!selectedPhoto) return;
    void runOperation(async () => {
      const { finalRun } = await imageUnderstandingCoordinator.runPipeline(
        selectedPhoto.uri,
        updateProgress,
      );
      if (finalRun.error) {
        throw new Error(
          `The final visual-memory stage failed: ${finalRun.error} The raw output and process trace were saved below.`,
        );
      }
    }, 'Image analyzed and all native resources were released.');
  }, [runOperation, selectedPhoto, updateProgress]);

  const finalHistory = React.useMemo(
    () => history.filter((run) => run.runtime.engineId === 'balanced-vlm'),
    [history],
  );

  const activePipelineProgress = progress['balanced-vlm'] ?? progress['fast-vision'] ?? null;

  const copyRun = React.useCallback(
    async (run: ImageUnderstandingRunRecord) => {
      await Clipboard.setStringAsync(JSON.stringify(run, null, 2));
      showSuccess('Copied run JSON.');
    },
    [showSuccess],
  );

  const exportHistory = React.useCallback(async () => {
    if (!history.length) return;
    await Share.share({
      title: 'Image understanding POC results',
      message: serializeImageUnderstandingRuns(history),
    });
  }, [history]);

  const clearHistory = React.useCallback(() => {
    void runOperation(async () => {
      await clearImageUnderstandingRunHistory();
      setHistory([]);
    }, 'Cleared local run history.');
  }, [runOperation]);

  return (
    <LinearGradient colors={theme.gradients.sunrise} style={styles.container}>
      <Animated.ScrollView
        onScroll={Animated.event([{ nativeEvent: { contentOffset: { y: scrollY } } }], {
          useNativeDriver: false,
        })}
        scrollEventThrottle={16}
        contentContainerStyle={[
          styles.content,
          {
            paddingTop:
              insets.top +
              COLLAPSING_TOP_BAR_HEIGHT +
              COLLAPSING_CONTENT_TOP_PADDING +
              COLLAPSING_SECONDARY_TITLE_BLOCK_HEIGHT,
            paddingBottom: insets.bottom + 32,
          },
        ]}
      >
        <Card style={styles.heroCard}>
          <Text style={styles.eyebrow}>LOCAL ONLY</Text>
          <Text style={styles.heroTitle}>Understand what is happening in a photo</Text>
          <Text style={styles.body}>
            A fast local detector and OCR pass supplies evidence to a compact visual-language model,
            which creates one useful description of the moment. The selected photo stays on this
            device.
          </Text>
          {selectedPhoto ? (
            <View style={styles.photoWrap}>
              <Image
                source={{ uri: selectedPhoto.uri }}
                style={styles.photo as ImageStyle}
                resizeMode="cover"
              />
              <Text style={styles.caption}>
                {selectedPhoto.width && selectedPhoto.height
                  ? `${selectedPhoto.width} × ${selectedPhoto.height}`
                  : 'Selected photo'}
              </Text>
            </View>
          ) : null}
          <Button
            label={selectedPhoto ? 'Choose another photo' : 'Choose a photo'}
            onPress={() => void pickPhoto()}
            disabled={busy}
          />
        </Card>

        <PipelineCard
          states={states}
          progress={activePipelineProgress}
          busy={busy}
          hasPhoto={Boolean(selectedPhoto)}
          onDownload={() =>
            void runOperation(
              () => imageUnderstandingCoordinator.downloadPipeline(updateProgress),
              'The full pipeline is available locally.',
            )
          }
          onRun={runPipeline}
          onDelete={() =>
            void runOperation(
              () => imageUnderstandingCoordinator.deletePipeline(updateProgress),
              'Pipeline models deleted.',
            )
          }
        />

        <View style={styles.historyHeader}>
          <View style={styles.flex}>
            <Text style={styles.sectionTitle}>Run history</Text>
            <Text style={styles.caption}>
              Latest {finalHistory.length} final observations, stored only on this device.
            </Text>
          </View>
        </View>
        <View style={styles.buttonRow}>
          <Button
            label="Export results"
            onPress={() => void exportHistory()}
            disabled={busy || !finalHistory.length}
            variant="secondary"
            style={styles.rowButton}
          />
          <Button
            label="Clear history"
            onPress={clearHistory}
            disabled={busy || !history.length}
            variant="danger"
            style={styles.rowButton}
          />
        </View>

        {finalHistory.length ? (
          finalHistory.map((run) => (
            <RunCard key={run.id} run={run} onCopy={() => void copyRun(run)} />
          ))
        ) : (
          <Card style={styles.card}>
            <Text style={styles.caption}>No local runs yet.</Text>
          </Card>
        )}
      </Animated.ScrollView>

      <CollapsingTopBar
        title="Image understanding"
        secondaryTitle="On-device visual memory"
        scrollY={scrollY}
        onPressBack={() => router.back()}
      />
      {imagePickerSheet}
    </LinearGradient>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1 },
  content: { paddingHorizontal: 16, gap: 14 },
  card: { padding: 18, gap: 14 },
  heroCard: { padding: 20, gap: 14, overflow: 'hidden' },
  eyebrow: { color: theme.colors.teal, fontSize: 11, fontWeight: '800', letterSpacing: 2.2 },
  heroTitle: { color: theme.colors.ink, fontSize: 25, lineHeight: 30, fontWeight: '800' },
  body: { color: theme.colors.mutedInk, fontSize: 14, lineHeight: 21 },
  caption: { color: theme.colors.mutedInk, fontSize: 12, lineHeight: 17 },
  flex: { flex: 1 },
  titleRow: { flexDirection: 'row', alignItems: 'center', gap: 10 },
  cardTitle: { color: theme.colors.ink, fontSize: 18, lineHeight: 23, fontWeight: '700' },
  engineIcon: {
    width: 40,
    height: 40,
    borderRadius: 20,
    backgroundColor: theme.colors.paleTeal,
    alignItems: 'center',
    justifyContent: 'center',
  },
  pill: { borderRadius: 999, paddingHorizontal: 9, paddingVertical: 5 },
  readyPill: { backgroundColor: theme.colors.paleTeal },
  emptyPill: { backgroundColor: '#eee8e0' },
  errorPill: { backgroundColor: '#f8deda' },
  pillText: { color: theme.colors.ink, fontSize: 10, fontWeight: '700' },
  metaBlock: { gap: 4 },
  meta: { color: theme.colors.mutedInk, fontSize: 12, lineHeight: 17 },
  warningBox: {
    flexDirection: 'row',
    gap: 8,
    padding: 11,
    borderRadius: theme.radius.md,
    backgroundColor: '#fff1dc',
  },
  warningText: { flex: 1, color: '#754319', fontSize: 12, lineHeight: 17 },
  progressBlock: { gap: 7 },
  progressText: { color: theme.colors.teal, fontSize: 12, fontWeight: '600' },
  progressTrack: {
    height: 5,
    borderRadius: 999,
    backgroundColor: theme.colors.line,
    overflow: 'hidden',
  },
  progressFill: { height: 5, borderRadius: 999, backgroundColor: theme.colors.teal },
  buttonRow: { flexDirection: 'row', gap: 10 },
  rowButton: { flex: 1 },
  secondaryButton: { marginTop: -4 },
  photoWrap: { gap: 7 },
  photo: {
    width: '100%',
    aspectRatio: 4 / 3,
    borderRadius: theme.radius.md,
    backgroundColor: theme.colors.line,
  },
  metricsGrid: { flexDirection: 'row', flexWrap: 'wrap', gap: 8 },
  metricCell: {
    width: '48%',
    borderRadius: theme.radius.md,
    backgroundColor: '#f8f5f1',
    padding: 10,
    gap: 3,
  },
  metricLabel: {
    color: theme.colors.mutedInk,
    fontSize: 10,
    textTransform: 'uppercase',
    letterSpacing: 0.7,
  },
  metricValue: { color: theme.colors.ink, fontSize: 13, fontWeight: '700' },
  errorText: { color: '#a23c34', fontSize: 12, lineHeight: 18 },
  resultBlock: {
    borderTopWidth: StyleSheet.hairlineWidth,
    borderTopColor: theme.colors.line,
    paddingTop: 12,
    gap: 6,
  },
  rawBlock: { gap: 7 },
  resultHeading: { color: theme.colors.ink, fontSize: 12, fontWeight: '800' },
  mono: { color: theme.colors.mutedInk, fontFamily: 'SpaceMono', fontSize: 10, lineHeight: 15 },
  historyHeader: {
    flexDirection: 'row',
    alignItems: 'flex-end',
    paddingHorizontal: 2,
    paddingTop: 8,
  },
  sectionTitle: { color: theme.colors.ink, fontSize: 21, fontWeight: '800' },
});
