import { KnowledgeProgress } from '@/components/knowledge/KnowledgeProgress';
import { KnowledgeRules } from '@/components/knowledge/KnowledgeRules';
import React from 'react';
import { useRouter } from 'expo-router';
import { LinearGradient } from 'expo-linear-gradient';
import { Animated, KeyboardAvoidingView, Platform, TextInput } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import {
  CollapsingTopBar,
  COLLAPSING_CONTENT_TOP_PADDING,
  COLLAPSING_SECONDARY_TITLE_BLOCK_HEIGHT,
  COLLAPSING_TOP_BAR_HEIGHT,
} from '@/components/CollapsingTopBar';
import { KnowledgeFacts } from '@/components/knowledge/KnowledgeFacts';
import { theme } from '@/theme';

export function KnowledgeScreen({
  subject,
  title = 'Knowledge',
  profile = false,
}: {
  subject?: string;
  title?: string;
  profile?: boolean;
}) {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const scrollY = React.useRef(new Animated.Value(0)).current;
  const [query, setQuery] = React.useState('');
  const [search, setSearch] = React.useState('');
  React.useEffect(() => {
    const id = setTimeout(() => setSearch(query), 300);
    return () => clearTimeout(id);
  }, [query]);

  return (
    <LinearGradient colors={theme.gradients.sunrise} style={{ flex: 1 }}>
      <KeyboardAvoidingView
        style={{ flex: 1 }}
        behavior={Platform.OS === 'ios' ? 'padding' : 'height'}
      >
        <Animated.ScrollView
          automaticallyAdjustKeyboardInsets
          keyboardShouldPersistTaps="handled"
          keyboardDismissMode="on-drag"
          onScroll={Animated.event([{ nativeEvent: { contentOffset: { y: scrollY } } }], {
            useNativeDriver: false,
          })}
          scrollEventThrottle={16}
          contentContainerStyle={{
            paddingHorizontal: 20,
            gap: 20,
            paddingTop:
              insets.top +
              COLLAPSING_TOP_BAR_HEIGHT +
              COLLAPSING_CONTENT_TOP_PADDING +
              COLLAPSING_SECONDARY_TITLE_BLOCK_HEIGHT,
            paddingBottom: insets.bottom + 100,
          }}
        >
          <TextInput
            value={query}
            onChangeText={setQuery}
            placeholder="Search what your Brain knows"
            accessibilityLabel="Search knowledge"
            placeholderTextColor={theme.colors.mutedInk}
            returnKeyType="search"
            style={{
              backgroundColor: 'white',
              color: theme.colors.ink,
              padding: 16,
              borderRadius: 16,
              fontSize: 16,
            }}
          />
          {!subject ? <KnowledgeProgress /> : null}
          <KnowledgeFacts subject={subject} query={search} profile={profile} />
          {profile ? <KnowledgeRules /> : null}
        </Animated.ScrollView>
      </KeyboardAvoidingView>
      <CollapsingTopBar
        title="Your Brain"
        secondaryTitle={title}
        scrollY={scrollY}
        onPressBack={() => router.back()}
      />
    </LinearGradient>
  );
}
