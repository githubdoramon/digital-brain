import { useFocusEffect } from '@react-navigation/native';
import { LinearGradient } from 'expo-linear-gradient';
import { useRouter } from 'expo-router';
import React from 'react';
import {
  Animated,
  AppState,
  KeyboardAvoidingView,
  Platform,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { Button } from '@/components/Button';
import { Card } from '@/components/Card';
import {
  COLLAPSING_CONTENT_TOP_PADDING,
  COLLAPSING_SECONDARY_TITLE_BLOCK_HEIGHT,
  COLLAPSING_TOP_BAR_HEIGHT,
  CollapsingTopBar,
} from '@/components/CollapsingTopBar';
import { glassesNative } from '@/glasses/runtime';
import { theme } from '@/theme';

export default function GlassesReplyTestScreen() {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const scrollY = React.useRef(new Animated.Value(0)).current;
  const [text, setText] = React.useState('Hello. This is a spoken reply through your glasses.');
  const [busy, setBusy] = React.useState(false);
  const [message, setMessage] = React.useState<string | null>(null);
  const active = React.useRef(false);
  const generation = React.useRef(0);
  const running = React.useRef(false);
  const cancel = React.useCallback(() => {
    try {
      void glassesNative()
        .cancelGlassesReplyTest?.()
        .catch(() => {});
    } catch {
      /* A missing native module must not throw during screen cleanup. */
    }
  }, []);

  useFocusEffect(
    React.useCallback(() => {
      active.current = true;
      running.current = false;
      setBusy(false);
      const subscription = AppState.addEventListener('change', (state) => {
        if (state !== 'active') cancel();
      });
      return () => {
        active.current = false;
        generation.current++;
        cancel();
        subscription.remove();
      };
    }, [cancel]),
  );

  async function play() {
    if (running.current || !text.trim()) return;
    const id = ++generation.current;
    running.current = true;
    setBusy(true);
    setMessage('Preparing and playing your spoken reply…');
    try {
      const result = await glassesNative().testGlassesReply(text.trim());
      if (!active.current || generation.current !== id) return;
      setMessage(
        result.status === 'spoken'
          ? 'Playback finished.'
          : result.status === 'cancelled'
            ? 'Test cancelled.'
            : result.audioError === 'reply_auth_failed'
              ? 'Sign in again to test a spoken reply.'
              : result.audioError === 'reply_busy'
                ? 'Wait for your current request to finish, then try again.'
                : result.audioError?.startsWith('tts_')
                  ? 'Could not create the spoken reply. Please try again.'
                  : result.audioError?.startsWith('reply_request_')
                    ? 'Could not get the spoken reply. Check your connection and try again.'
                    : 'Could not play the spoken reply.',
      );
    } catch {
      if (active.current && generation.current === id) {
        setMessage(
          'Could not start the test. Check that your glasses are connected and try again.',
        );
      }
    } finally {
      if (generation.current === id) {
        running.current = false;
        if (active.current) setBusy(false);
      }
    }
  }

  return (
    <View style={styles.screen}>
      <LinearGradient colors={theme.gradients.sunrise} style={StyleSheet.absoluteFill} />
      <CollapsingTopBar
        title="Test spoken reply"
        secondaryTitle="Glasses"
        scrollY={scrollY}
        onPressBack={() => router.back()}
      />
      <KeyboardAvoidingView
        style={styles.screen}
        behavior={Platform.OS === 'ios' ? 'padding' : 'height'}
      >
        <Animated.ScrollView
          automaticallyAdjustKeyboardInsets
          keyboardShouldPersistTaps="handled"
          keyboardDismissMode="on-drag"
          scrollEventThrottle={16}
          onScroll={Animated.event([{ nativeEvent: { contentOffset: { y: scrollY } } }], {
            useNativeDriver: false,
          })}
          contentContainerStyle={[
            styles.content,
            {
              paddingTop:
                insets.top +
                COLLAPSING_TOP_BAR_HEIGHT +
                COLLAPSING_SECONDARY_TITLE_BLOCK_HEIGHT +
                COLLAPSING_CONTENT_TOP_PADDING,
              paddingBottom: insets.bottom + 40,
            },
          ]}
        >
          <Card style={styles.card}>
            <Text style={styles.title}>What should your glasses say?</Text>
            <Text style={styles.body}>
              Enter a short message, then listen through your glasses.
            </Text>
            <TextInput
              accessibilityLabel="Text to speak"
              value={text}
              onChangeText={setText}
              multiline
              maxLength={400}
              editable={!busy}
              style={styles.input}
              placeholder="Enter a short message"
              placeholderTextColor={theme.colors.mutedInk}
              textAlignVertical="top"
            />
            <Button
              label="Play through glasses"
              loading={busy}
              disabled={!text.trim()}
              onPress={() => void play()}
            />
            {busy && <Button label="Cancel" variant="secondary" onPress={cancel} />}
            {message && (
              <Text accessibilityLiveRegion="polite" style={styles.body}>
                {message}
              </Text>
            )}
          </Card>
        </Animated.ScrollView>
      </KeyboardAvoidingView>
    </View>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1 },
  content: { paddingHorizontal: 20, gap: 16 },
  card: { padding: 18, gap: 14 },
  title: { fontSize: 18, fontWeight: '600', color: theme.colors.ink },
  body: { fontSize: 14, lineHeight: 21, color: theme.colors.mutedInk },
  input: {
    minHeight: 140,
    padding: 14,
    borderRadius: 14,
    borderWidth: 1,
    borderColor: theme.colors.line,
    color: theme.colors.ink,
    backgroundColor: theme.colors.card,
    fontSize: 16,
    lineHeight: 24,
  },
});
