import Ionicons from '@expo/vector-icons/Ionicons';
import { useFocusEffect } from '@react-navigation/native';
import { useRouter } from 'expo-router';
import React from 'react';
import { ScrollView, StyleSheet, Text, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { AppPressable as Pressable } from '@/components/AppPressable';
import { Button } from '@/components/Button';
import { Card } from '@/components/Card';
import { useAppNotice } from '@/hooks/useAppNotice';
import { imageUnderstandingCoordinator } from '@/image-understanding/coordinator';
import type {
  EngineModelState,
  EngineProgress,
  ImageUnderstandingEngineId,
} from '@/image-understanding/types';
import { theme } from '@/theme';

const EMPTY_STATE: EngineModelState = {
  downloaded: false,
  modelSizeBytes: null,
  loaded: false,
  compatibilityWarning: null,
};

const MODEL_COPY: Record<
  ImageUnderstandingEngineId,
  { name: string; role: string; runtime: string }
> = {
  'fast-vision': {
    name: 'Visual evidence',
    role: 'Detects objects and people, classifies the setting, and reads visible text.',
    runtime: 'ML Kit + EfficientDet + Places365',
  },
  'balanced-vlm': {
    name: 'Scene understanding',
    role: 'Describes the first-person moment, activity, people, and surroundings.',
    runtime: 'LFM2.5-VL-450M · ExecuTorch',
  },
};

function formatBytes(value: number | null): string {
  if (value == null || !Number.isFinite(value)) return 'Size unavailable';
  if (value < 1024 * 1024) return `${Math.round(value / 1024)} KB`;
  return `${(value / (1024 * 1024)).toFixed(value >= 100 * 1024 * 1024 ? 0 : 1)} MB`;
}

export default function GlassesAnalysisModelsScreen() {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const { showError, showSuccess } = useAppNotice();
  const [states, setStates] = React.useState<Record<ImageUnderstandingEngineId, EngineModelState>>({
    'fast-vision': EMPTY_STATE,
    'balanced-vlm': EMPTY_STATE,
  });
  const [progress, setProgress] = React.useState<EngineProgress | null>(null);
  const [busy, setBusy] = React.useState(false);

  const refresh = React.useCallback(async () => {
    setStates(await imageUnderstandingCoordinator.inspectPipeline());
  }, []);

  useFocusEffect(
    React.useCallback(() => {
      void refresh().catch((error) =>
        showError(error instanceof Error ? error.message : 'Could not inspect local models.'),
      );
    }, [refresh, showError]),
  );

  const run = React.useCallback(
    async (operation: () => Promise<unknown>, success: string) => {
      setBusy(true);
      try {
        await operation();
        await refresh();
        showSuccess(success);
      } catch (error) {
        showError(error instanceof Error ? error.message : 'Could not update local models.');
      } finally {
        setProgress(null);
        setBusy(false);
      }
    },
    [refresh, showError, showSuccess],
  );

  const onProgress = React.useCallback(
    (_engineId: ImageUnderstandingEngineId, next: EngineProgress) => {
      setProgress(next.stage === 'idle' ? null : next);
    },
    [],
  );

  const ready = states['fast-vision'].downloaded && states['balanced-vlm'].downloaded;
  const totalSize = Object.values(states).reduce(
    (total, state) => total + (state.modelSizeBytes ?? 0),
    0,
  );

  return (
    <View style={styles.screen}>
      <ScrollView
        contentContainerStyle={[
          styles.content,
          { paddingTop: insets.top + 12, paddingBottom: insets.bottom + 28 },
        ]}
      >
        <Pressable style={styles.back} onPress={() => router.back()}>
          <Ionicons name="arrow-back" size={22} color={theme.colors.ink} />
          <Text style={styles.backText}>Smart glasses</Text>
        </Pressable>

        <View style={styles.hero}>
          <View style={styles.heroIcon}>
            <Ionicons name="hardware-chip-outline" size={27} color={theme.colors.teal} />
          </View>
          <View style={styles.heroCopy}>
            <Text style={styles.eyebrow}>ON-DEVICE ANALYSIS</Text>
            <Text style={styles.title}>Scene analysis models</Text>
            <Text style={styles.subtitle}>
              Manage the private models used by automatic glasses capture.
            </Text>
          </View>
        </View>

        <Card style={styles.summaryCard}>
          <View style={styles.summaryRow}>
            <View style={[styles.statusDot, ready ? styles.readyDot : styles.setupDot]} />
            <View style={styles.flex}>
              <Text style={styles.cardTitle}>{ready ? 'Ready for capture' : 'Setup needed'}</Text>
              <Text style={styles.muted}>
                {totalSize > 0
                  ? `${formatBytes(totalSize)} stored locally`
                  : 'No model size recorded'}
              </Text>
            </View>
          </View>
          <Text style={styles.body}>
            The models run one at a time and unload after every photo. Images and inference remain
            on this phone during the current test.
          </Text>
          {progress ? <Text style={styles.progress}>{progress.detail}</Text> : null}
          <Button
            label={busy ? 'Working…' : ready ? 'Check and repair files' : 'Download models'}
            disabled={busy}
            onPress={() =>
              void run(
                () => imageUnderstandingCoordinator.downloadPipeline(onProgress),
                'Scene analysis models are ready.',
              )
            }
          />
          <Button
            label="Remove downloaded models"
            disabled={busy || !Object.values(states).some((state) => state.downloaded)}
            variant="danger"
            onPress={() =>
              void run(
                () => imageUnderstandingCoordinator.deletePipeline(onProgress),
                'Scene analysis models removed.',
              )
            }
          />
        </Card>

        {(Object.keys(MODEL_COPY) as ImageUnderstandingEngineId[]).map((id) => {
          const copy = MODEL_COPY[id];
          const state = states[id];
          return (
            <Card key={id} style={styles.modelCard}>
              <View style={styles.modelHeader}>
                <View style={styles.modelIcon}>
                  <Ionicons
                    name={id === 'fast-vision' ? 'scan-outline' : 'sparkles-outline'}
                    size={20}
                    color={theme.colors.teal}
                  />
                </View>
                <View style={styles.flex}>
                  <Text style={styles.cardTitle}>{copy.name}</Text>
                  <Text style={styles.runtime}>{copy.runtime}</Text>
                </View>
                <Text style={[styles.state, state.downloaded && styles.stateReady]}>
                  {state.downloaded ? 'Ready' : 'Missing'}
                </Text>
              </View>
              <Text style={styles.body}>{copy.role}</Text>
              <Text style={styles.muted}>{formatBytes(state.modelSizeBytes)}</Text>
              {state.compatibilityWarning ? (
                <Text style={styles.warning}>{state.compatibilityWarning}</Text>
              ) : null}
            </Card>
          );
        })}
      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: theme.colors.background },
  content: { paddingHorizontal: 20, gap: 14 },
  back: { flexDirection: 'row', alignItems: 'center', gap: 8, paddingVertical: 8 },
  backText: { color: theme.colors.ink, fontSize: 16, fontWeight: '600' },
  hero: { flexDirection: 'row', alignItems: 'center', gap: 14, paddingVertical: 4 },
  heroIcon: {
    width: 56,
    height: 56,
    borderRadius: 18,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: theme.colors.paleTeal,
  },
  heroCopy: { flex: 1, gap: 2 },
  eyebrow: { color: theme.colors.teal, fontSize: 11, fontWeight: '800', letterSpacing: 1.2 },
  title: { color: theme.colors.ink, fontSize: 28, lineHeight: 33, fontWeight: '800' },
  subtitle: { color: theme.colors.mutedInk, fontSize: 15, lineHeight: 21 },
  summaryCard: { padding: 18, gap: 14 },
  modelCard: { padding: 18, gap: 10 },
  summaryRow: { flexDirection: 'row', alignItems: 'center', gap: 11 },
  modelHeader: { flexDirection: 'row', alignItems: 'center', gap: 11 },
  modelIcon: {
    width: 38,
    height: 38,
    borderRadius: 12,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: theme.colors.paleTeal,
  },
  statusDot: { width: 10, height: 10, borderRadius: 5 },
  readyDot: { backgroundColor: theme.colors.teal },
  setupDot: { backgroundColor: theme.colors.accent },
  flex: { flex: 1 },
  cardTitle: { color: theme.colors.ink, fontSize: 17, fontWeight: '700' },
  body: { color: theme.colors.ink, fontSize: 14, lineHeight: 20 },
  muted: { color: theme.colors.mutedInk, fontSize: 13, lineHeight: 18 },
  runtime: { color: theme.colors.mutedInk, fontSize: 12, lineHeight: 17 },
  progress: { color: theme.colors.teal, fontSize: 13, lineHeight: 18, fontWeight: '600' },
  state: { color: theme.colors.accentDeep, fontSize: 12, fontWeight: '700' },
  stateReady: { color: theme.colors.teal },
  warning: { color: theme.colors.accentDeep, fontSize: 13, lineHeight: 18 },
});
