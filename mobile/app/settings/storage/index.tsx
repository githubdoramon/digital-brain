import Ionicons from '@expo/vector-icons/Ionicons';
import { useFocusEffect } from '@react-navigation/native';
import { useRouter } from 'expo-router';
import React from 'react';
import { Platform, ScrollView, StyleSheet, Text, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { AppPressable as Pressable } from '@/components/AppPressable';
import { Button } from '@/components/Button';
import { Card } from '@/components/Card';
import { useAppNotice } from '@/hooks/useAppNotice';
import { syncImageEnhancementStorage } from '@/mentraCapture';
import {
  chooseDigitalBrainStorageBaseUri,
  digitalBrainStorageFolderLabel,
  DigitalBrainStorageFolder,
  getDigitalBrainStorageBaseUri,
  getDigitalBrainStorageFolder,
} from '@/storage/digitalBrainStorage';
import { theme } from '@/theme';

const managedFolders = [
  DigitalBrainStorageFolder.Recordings,
  DigitalBrainStorageFolder.GlassesCaptureQueue,
  DigitalBrainStorageFolder.ImagePipelineTemp,
  DigitalBrainStorageFolder.Exports,
];

export default function StorageSettingsScreen() {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const { showError, showSuccess } = useAppNotice();
  const [baseUri, setBaseUri] = React.useState<string | null>(null);
  const [selecting, setSelecting] = React.useState(false);

  const refresh = React.useCallback(async () => {
    setBaseUri(await getDigitalBrainStorageBaseUri());
  }, []);

  useFocusEffect(
    React.useCallback(() => {
      void refresh();
    }, [refresh]),
  );

  const chooseFolder = async () => {
    setSelecting(true);
    try {
      const selected = await chooseDigitalBrainStorageBaseUri();
      if (!selected) return;
      await Promise.all(managedFolders.map((folder) => getDigitalBrainStorageFolder(folder)));
      await syncImageEnhancementStorage();
      setBaseUri(selected);
      showSuccess('Digital Brain storage location saved.');
    } catch (error) {
      showError(
        error instanceof Error ? error.message : 'Could not choose a Digital Brain folder.',
      );
    } finally {
      setSelecting(false);
    }
  };

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
          <Text style={styles.backText}>Settings</Text>
        </Pressable>
        <View style={styles.hero}>
          <View style={styles.heroIcon}>
            <Ionicons name="folder-open-outline" size={28} color={theme.colors.teal} />
          </View>
          <View style={styles.heroCopy}>
            <Text style={styles.eyebrow}>DEVICE STORAGE</Text>
            <Text style={styles.title}>Digital Brain folder</Text>
            <Text style={styles.subtitle}>
              One place you can open from Files or any other Android app.
            </Text>
          </View>
        </View>
        {Platform.OS !== 'android' ? (
          <Text style={styles.warning}>
            Shared folders are currently available on Android only.
          </Text>
        ) : null}
        <Card style={styles.card}>
          <Text style={styles.cardTitle}>Storage location</Text>
          <Text style={styles.value}>
            {baseUri
              ? 'A Digital Brain base folder is selected. The app creates its own subfolders inside it.'
              : 'Choose or create a Digital Brain folder to make recordings and exports available outside the app.'}
          </Text>
          {baseUri ? (
            <View style={styles.selectedFolder}>
              <Ionicons name="folder-open-outline" size={20} color={theme.colors.teal} />
              <View style={styles.selectedFolderCopy}>
                <Text style={styles.selectedFolderLabel}>Selected folder</Text>
                <Text style={styles.selectedFolderName}>
                  {digitalBrainStorageFolderLabel(baseUri)}
                </Text>
              </View>
            </View>
          ) : null}
          <Button
            label={
              selecting
                ? 'Choosing…'
                : baseUri
                  ? 'Change Digital Brain folder'
                  : 'Choose Digital Brain folder'
            }
            onPress={() => void chooseFolder()}
            disabled={Platform.OS !== 'android'}
            loading={selecting}
          />
        </Card>
        <Card style={styles.card}>
          <Text style={styles.cardTitle}>Managed subfolders</Text>
          {managedFolders.map((folder) => (
            <View key={folder} style={styles.folderRow}>
              <Ionicons name="folder-outline" size={20} color={theme.colors.teal} />
              <Text style={styles.folderName}>{folder}</Text>
            </View>
          ))}
          <Text style={styles.helperText}>
            Changing this location affects future files. Existing external files stay where they
            are.
          </Text>
        </Card>
      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: theme.colors.background },
  content: { paddingHorizontal: 20, gap: 14 },
  back: { flexDirection: 'row', alignItems: 'center', gap: 8, paddingVertical: 8 },
  backText: { fontSize: 16, color: theme.colors.ink, fontWeight: '500' },
  hero: { flexDirection: 'row', alignItems: 'center', gap: 14, paddingTop: 4 },
  heroIcon: {
    width: 58,
    height: 58,
    borderRadius: 18,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: theme.colors.paleTeal,
  },
  heroCopy: { flex: 1, gap: 2 },
  eyebrow: { fontSize: 11, letterSpacing: 1.2, fontWeight: '700', color: theme.colors.teal },
  title: { fontSize: 30, fontWeight: '700', color: theme.colors.ink },
  subtitle: { fontSize: 15, lineHeight: 21, color: theme.colors.mutedInk },
  warning: { color: theme.colors.accentDeep },
  card: { padding: 16, gap: 12 },
  cardTitle: { color: theme.colors.ink, fontSize: 18, fontWeight: '700' },
  value: { color: theme.colors.mutedInk, lineHeight: 20 },
  selectedFolder: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
    padding: 12,
    borderRadius: theme.radius.md,
    backgroundColor: theme.colors.paleTeal,
  },
  selectedFolderCopy: { flex: 1, gap: 2 },
  selectedFolderLabel: { color: theme.colors.mutedInk, fontSize: 12, fontWeight: '600' },
  selectedFolderName: { color: theme.colors.ink, fontSize: 16, fontWeight: '700' },
  folderRow: { flexDirection: 'row', alignItems: 'center', gap: 10 },
  folderName: { color: theme.colors.ink, fontSize: 16, fontWeight: '600' },
  helperText: { color: theme.colors.mutedInk, fontSize: 13, lineHeight: 19 },
});
