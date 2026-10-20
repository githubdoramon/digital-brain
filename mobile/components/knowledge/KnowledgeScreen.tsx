import { KnowledgeProgress } from '@/components/knowledge/KnowledgeProgress';
import { KnowledgeRules } from '@/components/knowledge/KnowledgeRules';
import React from 'react';
import { useFocusEffect } from '@react-navigation/native';
import { useRouter } from 'expo-router';
import { LinearGradient } from 'expo-linear-gradient';
import {
  Animated,
  KeyboardAvoidingView,
  Platform,
  ScrollView,
  Text,
  TextInput,
  View,
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { apiFetch } from '@/api/client';
import { useAuth } from '@/auth/AuthContext';
import { AppPressable as Pressable } from '@/components/AppPressable';
import {
  CollapsingTopBar,
  COLLAPSING_CONTENT_TOP_PADDING,
  COLLAPSING_SECONDARY_TITLE_BLOCK_HEIGHT,
  COLLAPSING_TOP_BAR_HEIGHT,
} from '@/components/CollapsingTopBar';
import { KnowledgeFacts } from '@/components/knowledge/KnowledgeFacts';
import { theme } from '@/theme';

type Entity = { entity_ref: string; label: string; kind: string };

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
  const { token } = useAuth();
  const insets = useSafeAreaInsets();
  const scrollY = React.useRef(new Animated.Value(0)).current;
  const [query, setQuery] = React.useState('');
  const [search, setSearch] = React.useState('');
  const [entities, setEntities] = React.useState<Entity[]>([]);
  React.useEffect(() => {
    const id = setTimeout(() => setSearch(query), 300);
    return () => clearTimeout(id);
  }, [query]);
  useFocusEffect(
    React.useCallback(() => {
      let active = true;
      if (!subject)
        void (apiFetch('/mobile/knowledge/entities', { token }) as Promise<{ entities: Entity[] }>)
          .then((result) => {
            if (active) setEntities(result.entities);
          })
          .catch(() => undefined);
      return () => {
        active = false;
      };
    }, [subject, token]),
  );

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
          {!subject && entities.length ? (
            <View style={{ gap: 10 }}>
              <Text style={{ color: theme.colors.mutedInk, fontWeight: '600' }}>
                Explore by subject
              </Text>
              <ScrollView
                horizontal
                showsHorizontalScrollIndicator={false}
                keyboardShouldPersistTaps="handled"
              >
                {entities
                  .filter(
                    (entity) =>
                      !search || entity.label.toLowerCase().includes(search.toLowerCase()),
                  )
                  .map((entity) => (
                    <Pressable
                      key={entity.entity_ref}
                      accessibilityRole="button"
                      onPress={() =>
                        router.push({
                          pathname: '/knowledge/[entityRef]' as never,
                          params: { entityRef: entity.entity_ref, title: entity.label },
                        })
                      }
                      style={{
                        paddingVertical: 12,
                        paddingHorizontal: 16,
                        marginRight: 8,
                        backgroundColor: 'white',
                        borderRadius: 16,
                      }}
                    >
                      <Text style={{ color: theme.colors.ink, fontWeight: '600' }}>
                        {entity.label}
                      </Text>
                      <Text style={{ color: theme.colors.mutedInk, fontSize: 12, marginTop: 3 }}>
                        {entity.kind}
                      </Text>
                    </Pressable>
                  ))}
              </ScrollView>
            </View>
          ) : null}
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
