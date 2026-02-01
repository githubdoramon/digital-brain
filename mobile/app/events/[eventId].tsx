import Ionicons from '@expo/vector-icons/Ionicons';
import { LinearGradient } from 'expo-linear-gradient';
import { useLocalSearchParams } from 'expo-router';
import React from 'react';
import {
  Animated,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { apiFetch } from '@/api/client';
import { Card } from '@/components/Card';
import { theme } from '@/theme';

type EventDetail = {
  id: string;
  title?: string | null;
  summary?: string | null;
  start_date?: string | null;
  end_date?: string | null;
  tags?: string[] | null;
  people?: string[] | null;
  place?: {
    place_id: string;
    name?: string | null;
    city?: string | null;
    country?: string | null;
  } | null;
};

type Contact = {
  contact_id: string;
  display_name: string;
};

function formatDateRange(start?: string | null, end?: string | null) {
  if (!start) return 'Date TBD';
  const startDate = new Date(start);
  const startLabel = Number.isNaN(startDate.getTime())
    ? start
    : startDate.toLocaleString(undefined, {
        weekday: 'short',
        month: 'short',
        day: 'numeric',
        hour: 'numeric',
        minute: '2-digit',
      });
  if (!end) return startLabel;
  const endDate = new Date(end);
  const endLabel = Number.isNaN(endDate.getTime())
    ? end
    : endDate.toLocaleString(undefined, {
        weekday: 'short',
        month: 'short',
        day: 'numeric',
        hour: 'numeric',
        minute: '2-digit',
      });
  return `${startLabel} – ${endLabel}`;
}

export default function EventDetailScreen() {
  const { eventId } = useLocalSearchParams<{ eventId: string }>();
  const eventParam = Array.isArray(eventId) ? eventId[0] : eventId;
  const insets = useSafeAreaInsets();
  const [event, setEvent] = React.useState<EventDetail | null>(null);
  const [contactMap, setContactMap] = React.useState<Map<string, string>>(new Map());

  const heroOpacity = React.useRef(new Animated.Value(0)).current;
  const heroTranslate = React.useRef(new Animated.Value(12)).current;

  React.useEffect(() => {
    Animated.parallel([
      Animated.timing(heroOpacity, {
        toValue: 1,
        duration: 260,
        useNativeDriver: true,
      }),
      Animated.timing(heroTranslate, {
        toValue: 0,
        duration: 260,
        useNativeDriver: true,
      }),
    ]).start();
  }, [heroOpacity, heroTranslate]);

  React.useEffect(() => {
    let mounted = true;
    if (!eventParam) return () => undefined;
    (async () => {
      try {
        const result = (await apiFetch(
          `/mobile/events/${encodeURIComponent(eventParam)}`
        )) as EventDetail;
        if (mounted) {
          setEvent(result);
        }
      } catch (error) {
        if (mounted) {
          setEvent(null);
        }
      }
    })();
    return () => {
      mounted = false;
    };
  }, [eventParam]);

  React.useEffect(() => {
    (async () => {
      try {
        const result = (await apiFetch('/mobile/contacts')) as { contacts: Contact[] };
        const map = new Map<string, string>();
        result.contacts.forEach((item) => map.set(item.contact_id, item.display_name));
        setContactMap(map);
      } catch (error) {
        setContactMap(new Map());
      }
    })();
  }, []);

  const title = event?.title?.trim() || 'Event details';
  const summary = event?.summary?.trim();
  const dateLabel = formatDateRange(event?.start_date, event?.end_date);
  const attendees = (event?.people ?? []).map((id) => contactMap.get(id) || id);
  const tags = event?.tags ?? [];

  return (
    <LinearGradient colors={theme.gradients.dusk} style={styles.container}>
      <ScrollView
        contentContainerStyle={[
          styles.content,
          { paddingTop: insets.top + 64, paddingBottom: insets.bottom + 40 },
        ]}
        showsVerticalScrollIndicator={false}
      >
        <Animated.View
          style={{ opacity: heroOpacity, transform: [{ translateY: heroTranslate }] }}
        >
          <Text style={styles.kicker}>Linked event</Text>
          <Text style={styles.title}>{title}</Text>
          <Text style={styles.subtitle}>{dateLabel}</Text>
        </Animated.View>

        <Card style={styles.detailCard}>
          <View style={styles.sectionHeader}>
            <Ionicons name="document-text" size={16} color={theme.colors.accentDeep} />
            <Text style={styles.sectionTitle}>Notes</Text>
          </View>
          {summary ? renderMarkdown(summary) : (
            <Text style={styles.bodyText}>No notes captured yet.</Text>
          )}
        </Card>

        {event?.place ? (
          <Card style={styles.detailCard}>
            <View style={styles.sectionHeader}>
              <Ionicons name="location" size={16} color={theme.colors.accentDeep} />
              <Text style={styles.sectionTitle}>Location</Text>
            </View>
            <Text style={styles.bodyText}>{event.place.name || 'Location'}</Text>
            {event.place.city || event.place.country ? (
              <Text style={styles.metaText}>
                {[event.place.city, event.place.country].filter(Boolean).join(', ')}
              </Text>
            ) : null}
          </Card>
        ) : null}

        {attendees.length > 0 ? (
          <Card style={styles.detailCard}>
            <View style={styles.sectionHeader}>
              <Ionicons name="people" size={16} color={theme.colors.accentDeep} />
              <Text style={styles.sectionTitle}>Attendees</Text>
            </View>
            {attendees.map((person) => (
              <Text key={person} style={styles.bodyText}>
                {person}
              </Text>
            ))}
          </Card>
        ) : null}

        {tags.length > 0 ? (
          <Card style={styles.detailCard}>
            <View style={styles.sectionHeader}>
              <Ionicons name="pricetag" size={16} color={theme.colors.accentDeep} />
              <Text style={styles.sectionTitle}>Tags</Text>
            </View>
            <View style={styles.tagRow}>
              {tags.map((tag) => (
                <View key={tag} style={styles.tagChip}>
                  <Text style={styles.tagText}>{tag}</Text>
                </View>
              ))}
            </View>
          </Card>
        ) : null}
      </ScrollView>
    </LinearGradient>
  );
}

function renderMarkdown(markdown: string) {
  return markdown.split('\n').map((line, index) => {
    if (line.startsWith('# ')) {
      return (
        <Text key={`h1-${index}`} style={styles.markdownH1}>
          {renderInline(line.replace('# ', ''), `h1-${index}`)}
        </Text>
      );
    }
    if (line.startsWith('## ')) {
      return (
        <Text key={`h2-${index}`} style={styles.markdownH2}>
          {renderInline(line.replace('## ', ''), `h2-${index}`)}
        </Text>
      );
    }
    if (line.startsWith('### ')) {
      return (
        <Text key={`h3-${index}`} style={styles.markdownH3}>
          {renderInline(line.replace('### ', ''), `h3-${index}`)}
        </Text>
      );
    }
    if (line.startsWith('- ')) {
      return (
        <View key={`bullet-${index}`} style={styles.markdownBulletRow}>
          <Text style={styles.markdownBullet}>•</Text>
          <Text style={styles.markdownBulletText}>
            {renderInline(line.replace('- ', ''), `bullet-${index}`)}
          </Text>
        </View>
      );
    }
    if (!line.trim()) {
      return <View key={`space-${index}`} style={styles.markdownSpacer} />;
    }
    return (
      <Text key={`p-${index}`} style={styles.markdownParagraph}>
        {renderInline(line, `p-${index}`)}
      </Text>
    );
  });
}

function renderInline(text: string, keyPrefix: string) {
  const parts = text.split(/(\*\*[^*]+\*\*|\*[^*]+\*)/g).filter(Boolean);
  return parts.map((part, index) => {
    if (part.startsWith('**') && part.endsWith('**')) {
      return (
        <Text key={`${keyPrefix}-b-${index}`} style={styles.markdownBold}>
          {part.slice(2, -2)}
        </Text>
      );
    }
    if (part.startsWith('*') && part.endsWith('*')) {
      return (
        <Text key={`${keyPrefix}-i-${index}`} style={styles.markdownItalic}>
          {part.slice(1, -1)}
        </Text>
      );
    }
    return <Text key={`${keyPrefix}-t-${index}`}>{part}</Text>;
  });
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
  },
  content: {
    paddingHorizontal: 20,
    gap: 16,
  },
  kicker: {
    fontSize: 12,
    textTransform: 'uppercase',
    letterSpacing: 2.6,
    color: theme.colors.accentDeep,
    fontWeight: '600',
  },
  title: {
    marginTop: 8,
    fontSize: 26,
    fontWeight: '700',
    color: theme.colors.ink,
  },
  subtitle: {
    fontSize: 14,
    color: theme.colors.mutedInk,
    marginTop: 6,
  },
  detailCard: {
    padding: 18,
    gap: 10,
  },
  sectionHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
  },
  sectionTitle: {
    fontSize: 14,
    fontWeight: '600',
    color: theme.colors.ink,
  },
  bodyText: {
    fontSize: 14,
    color: theme.colors.ink,
    lineHeight: 20,
  },
  metaText: {
    fontSize: 12,
    color: theme.colors.mutedInk,
  },
  tagRow: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 8,
  },
  tagChip: {
    paddingHorizontal: 12,
    paddingVertical: 6,
    borderRadius: 14,
    backgroundColor: 'rgba(47, 111, 116, 0.12)',
    borderWidth: 1,
    borderColor: 'rgba(47, 111, 116, 0.24)',
  },
  tagText: {
    fontSize: 12,
    fontWeight: '600',
    color: theme.colors.ink,
  },
  markdownH1: {
    fontSize: 18,
    fontWeight: '700',
    color: theme.colors.ink,
    marginBottom: 6,
  },
  markdownH2: {
    fontSize: 16,
    fontWeight: '700',
    color: theme.colors.ink,
    marginBottom: 4,
  },
  markdownH3: {
    fontSize: 14,
    fontWeight: '700',
    color: theme.colors.ink,
    marginBottom: 4,
  },
  markdownParagraph: {
    fontSize: 14,
    color: theme.colors.ink,
    lineHeight: 20,
  },
  markdownBulletRow: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    gap: 8,
  },
  markdownBullet: {
    fontSize: 14,
    color: theme.colors.ink,
    lineHeight: 20,
  },
  markdownBulletText: {
    flex: 1,
    fontSize: 14,
    color: theme.colors.ink,
    lineHeight: 20,
  },
  markdownSpacer: {
    height: 10,
  },
  markdownBold: {
    fontWeight: '700',
  },
  markdownItalic: {
    fontStyle: 'italic',
  },
});
