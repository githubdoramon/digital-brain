import Ionicons from '@expo/vector-icons/Ionicons';
import { LinearGradient } from 'expo-linear-gradient';
import React, { useCallback, useEffect, useState } from 'react';
import {
  ActivityIndicator,
  Alert,
  FlatList,
  Linking,
  Modal,
  Platform,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { useHeaderHeight } from '@react-navigation/elements';

import { apiFetch } from '@/api/client';
import { useAuth } from '@/auth/AuthContext';
import { AppPressable as Pressable } from '@/components/AppPressable';
import { Button } from '@/components/Button';
import { Card } from '@/components/Card';
import { theme } from '@/theme';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type NewsTopic = {
  topic_id: string;
  label: string;
  keywords: string[];
  enabled: boolean;
};

type NewsArticle = {
  title: string;
  url: string;
  summary: string;
  source: string;
  published_at: string | null;
  topic_matches: string[];
};

// ---------------------------------------------------------------------------
// Screen
// ---------------------------------------------------------------------------

export default function NewsTopicsScreen() {
  const { token } = useAuth();
  const insets = useSafeAreaInsets();
  const headerHeight = useHeaderHeight();

  // -- Topic state --
  const [topics, setTopics] = useState<NewsTopic[]>([]);
  const [isLoadingTopics, setIsLoadingTopics] = useState(true);
  const [editingTopic, setEditingTopic] = useState<NewsTopic | null>(null);
  const [showForm, setShowForm] = useState(false);

  // -- News preview state --
  const [articles, setArticles] = useState<NewsArticle[]>([]);
  const [isFetchingNews, setIsFetchingNews] = useState(false);

  // -- Fetch topics --
  const loadTopics = useCallback(async () => {
    try {
      const res = (await apiFetch('/mobile/news-topics', { token })) as {
        topics: NewsTopic[];
      };
      setTopics(res?.topics ?? []);
    } catch {
      Alert.alert('Error', 'Could not load topics.');
    } finally {
      setIsLoadingTopics(false);
    }
  }, [token]);

  useEffect(() => {
    loadTopics();
  }, [loadTopics]);

  // -- Save topic --
  const saveTopic = useCallback(
    async (topic: NewsTopic) => {
      try {
        await apiFetch('/mobile/news-topics', {
          method: 'POST',
          body: JSON.stringify(topic),
          token,
        });
        setShowForm(false);
        setEditingTopic(null);
        await loadTopics();
      } catch {
        Alert.alert('Error', 'Could not save topic.');
      }
    },
    [token, loadTopics],
  );

  // -- Delete topic --
  const deleteTopic = useCallback(
    async (topicId: string) => {
      Alert.alert('Delete topic', 'This cannot be undone.', [
        { text: 'Cancel', style: 'cancel' },
        {
          text: 'Delete',
          style: 'destructive',
          onPress: async () => {
            try {
              await apiFetch(`/mobile/news-topics/${encodeURIComponent(topicId)}`, {
                method: 'DELETE',
                token,
              });
              await loadTopics();
            } catch {
              Alert.alert('Error', 'Could not delete topic.');
            }
          },
        },
      ]);
    },
    [token, loadTopics],
  );

  // -- News preview --
  const fetchNewsPreview = useCallback(async () => {
    setIsFetchingNews(true);
    setArticles([]);
    try {
      const res = (await apiFetch('/mobile/news-topics/preview', { token })) as {
        articles: NewsArticle[];
      };
      setArticles(res?.articles ?? []);
    } catch {
      Alert.alert('Error', 'Could not fetch news preview.');
    } finally {
      setIsFetchingNews(false);
    }
  }, [token]);

  // -- Handlers --
  const openCreate = () => {
    setEditingTopic(null);
    setShowForm(true);
  };

  const openEdit = (topic: NewsTopic) => {
    setEditingTopic(topic);
    setShowForm(true);
  };

  // -- Render --
  return (
    <LinearGradient
      colors={theme.gradients.sunrise}
      style={[
        styles.container,
        {
          paddingTop: Platform.OS === 'android' ? 12 : headerHeight + 12,
          paddingBottom: insets.bottom + 24,
        },
      ]}
    >
      <FlatList
        data={articles}
        keyExtractor={(item, idx) => item.url || `article-${idx}`}
        ListHeaderComponent={
          <TopicsSection
            topics={topics}
            isLoading={isLoadingTopics}
            onAdd={openCreate}
            onEdit={openEdit}
            onDelete={deleteTopic}
            isFetchingNews={isFetchingNews}
            onFetchNews={fetchNewsPreview}
            articleCount={articles.length}
          />
        }
        renderItem={({ item }) => <ArticleRow article={item} />}
        contentContainerStyle={styles.listContent}
        showsVerticalScrollIndicator={false}
      />

      <TopicFormModal
        visible={showForm}
        initial={editingTopic}
        onSave={saveTopic}
        onCancel={() => {
          setShowForm(false);
          setEditingTopic(null);
        }}
      />
    </LinearGradient>
  );
}

// ---------------------------------------------------------------------------
// Topics header section (rendered inside FlatList header)
// ---------------------------------------------------------------------------

type TopicsSectionProps = {
  topics: NewsTopic[];
  isLoading: boolean;
  onAdd: () => void;
  onEdit: (topic: NewsTopic) => void;
  onDelete: (topicId: string) => void;
  isFetchingNews: boolean;
  onFetchNews: () => void;
  articleCount: number;
};

function TopicsSection({
  topics,
  isLoading,
  onAdd,
  onEdit,
  onDelete,
  isFetchingNews,
  onFetchNews,
  articleCount,
}: TopicsSectionProps) {
  return (
    <>
      {/* Header */}
      <View style={styles.header}>
        <Text style={styles.kicker}>Daily briefing</Text>
        <Text style={styles.title}>News topics</Text>
      </View>

      {/* Topic list */}
      <Card style={styles.topicsCard}>
        {isLoading ? (
          <ActivityIndicator color={theme.colors.accent} style={styles.loader} />
        ) : topics.length === 0 ? (
          <Text style={styles.emptyText}>No topics yet. Add one to get started.</Text>
        ) : (
          topics.map((topic, idx) => (
            <View key={topic.topic_id}>
              {idx > 0 && <View style={styles.divider} />}
              <TopicRow topic={topic} onEdit={onEdit} onDelete={onDelete} />
            </View>
          ))
        )}
      </Card>

      <Button label="Add topic" variant="secondary" onPress={onAdd} style={styles.addButton} />

      {/* News preview */}
      <View style={styles.previewSection}>
        <Text style={styles.sectionTitle}>News preview</Text>
        <Text style={styles.sectionSubtitle}>
          Test your topic configuration by fetching today's news.
        </Text>
        <Button
          label={isFetchingNews ? 'Fetching...' : 'Fetch news'}
          variant="primary"
          onPress={onFetchNews}
          disabled={isFetchingNews}
          style={styles.fetchButton}
        />
        {isFetchingNews && (
          <ActivityIndicator color={theme.colors.accent} style={styles.fetchSpinner} />
        )}
        {!isFetchingNews && articleCount > 0 && (
          <Text style={styles.resultCount}>
            {articleCount} article{articleCount !== 1 ? 's' : ''} found
          </Text>
        )}
      </View>
    </>
  );
}

// ---------------------------------------------------------------------------
// Topic row
// ---------------------------------------------------------------------------

function TopicRow({
  topic,
  onEdit,
  onDelete,
}: {
  topic: NewsTopic;
  onEdit: (t: NewsTopic) => void;
  onDelete: (id: string) => void;
}) {
  return (
    <View style={styles.topicRow}>
      <Pressable style={styles.topicTap} onPress={() => onEdit(topic)}>
        <Text style={styles.topicLabel}>{topic.label}</Text>
        <Text style={styles.topicKeywords} numberOfLines={1}>
          {topic.keywords.join(', ')}
        </Text>
      </Pressable>
      <Pressable onPress={() => onDelete(topic.topic_id)} style={styles.deleteHit}>
        <Ionicons name="trash-outline" size={18} color="#c0392b" />
      </Pressable>
    </View>
  );
}

// ---------------------------------------------------------------------------
// Article row
// ---------------------------------------------------------------------------

function ArticleRow({ article }: { article: NewsArticle }) {
  const topicBadges = article.topic_matches ?? [];
  return (
    <Card style={styles.articleCard}>
      <Pressable
        onPress={() => article.url && Linking.openURL(article.url)}
        disabled={!article.url}
      >
        {topicBadges.length > 0 && (
          <View style={styles.badgeRow}>
            {topicBadges.map((t) => (
              <View key={t} style={styles.badge}>
                <Text style={styles.badgeText}>{t}</Text>
              </View>
            ))}
          </View>
        )}
        <Text style={styles.articleTitle} numberOfLines={2}>
          {article.title}
        </Text>
        {!!article.summary && (
          <Text style={styles.articleSummary} numberOfLines={3}>
            {article.summary}
          </Text>
        )}
        <View style={styles.articleMeta}>
          <Text style={styles.articleSource}>{article.source}</Text>
          {!!article.url && (
            <Ionicons name="open-outline" size={14} color={theme.colors.teal} />
          )}
        </View>
      </Pressable>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Topic form modal
// ---------------------------------------------------------------------------

type TopicFormModalProps = {
  visible: boolean;
  initial: NewsTopic | null;
  onSave: (topic: NewsTopic) => void;
  onCancel: () => void;
};

function TopicFormModal({ visible, initial, onSave, onCancel }: TopicFormModalProps) {
  const [label, setLabel] = useState('');
  const [keywordsText, setKeywordsText] = useState('');

  useEffect(() => {
    if (visible) {
      setLabel(initial?.label ?? '');
      setKeywordsText(initial?.keywords.join(', ') ?? '');
    }
  }, [visible, initial]);

  const handleSave = () => {
    const trimmedLabel = label.trim();
    if (!trimmedLabel) {
      Alert.alert('Validation', 'Label is required.');
      return;
    }
    const keywords = keywordsText
      .split(',')
      .map((k) => k.trim())
      .filter(Boolean);
    if (keywords.length === 0) {
      Alert.alert('Validation', 'At least one keyword is required.');
      return;
    }
    const topicId =
      initial?.topic_id ?? trimmedLabel.toLowerCase().replace(/[^a-z0-9]+/g, '-');
    onSave({
      topic_id: topicId,
      label: trimmedLabel,
      keywords,
      enabled: initial?.enabled ?? true,
    });
  };

  return (
    <Modal visible={visible} animationType="slide" transparent>
      <View style={styles.modalOverlay}>
        <View style={styles.modalContent}>
          <Text style={styles.modalTitle}>{initial ? 'Edit topic' : 'New topic'}</Text>

          <Text style={styles.fieldLabel}>Label</Text>
          <TextInput
            style={styles.input}
            value={label}
            onChangeText={setLabel}
            placeholder="e.g. Artificial Intelligence"
            placeholderTextColor={theme.colors.mutedInk}
            autoFocus
          />

          <Text style={styles.fieldLabel}>Keywords (comma-separated)</Text>
          <TextInput
            style={[styles.input, styles.keywordsInput]}
            value={keywordsText}
            onChangeText={setKeywordsText}
            placeholder="e.g. AI, LLM, GPT, machine learning"
            placeholderTextColor={theme.colors.mutedInk}
            multiline
          />

          <View style={styles.modalActions}>
            <Button label="Cancel" variant="secondary" onPress={onCancel} style={styles.modalBtn} />
            <Button label="Save" variant="primary" onPress={handleSave} style={styles.modalBtn} />
          </View>
        </View>
      </View>
    </Modal>
  );
}

// ---------------------------------------------------------------------------
// Styles
// ---------------------------------------------------------------------------

const styles = StyleSheet.create({
  container: {
    flex: 1,
    paddingHorizontal: 24,
  },
  listContent: {
    paddingBottom: 32,
  },

  // -- Header --
  header: {
    marginBottom: 24,
  },
  kicker: {
    textTransform: 'uppercase',
    letterSpacing: 3,
    fontSize: 12,
    fontWeight: '600',
    color: theme.colors.teal,
  },
  title: {
    fontSize: 26,
    fontWeight: '700',
    color: theme.colors.ink,
    marginTop: 6,
  },

  // -- Topics card --
  topicsCard: {
    borderRadius: theme.radius.xl,
    padding: 16,
  },
  loader: {
    paddingVertical: 20,
  },
  emptyText: {
    textAlign: 'center',
    color: theme.colors.mutedInk,
    fontSize: 14,
    paddingVertical: 16,
  },
  divider: {
    height: 1,
    backgroundColor: theme.colors.line,
    marginVertical: 8,
  },

  // -- Topic row --
  topicRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
  },
  topicTap: {
    flex: 1,
    paddingVertical: 6,
  },
  topicLabel: {
    fontSize: 15,
    fontWeight: '600',
    color: theme.colors.ink,
  },
  topicKeywords: {
    fontSize: 13,
    color: theme.colors.mutedInk,
    marginTop: 2,
  },
  deleteHit: {
    padding: 8,
  },

  addButton: {
    marginTop: 12,
  },

  // -- Preview section --
  previewSection: {
    marginTop: 28,
  },
  sectionTitle: {
    fontSize: 18,
    fontWeight: '700',
    color: theme.colors.ink,
  },
  sectionSubtitle: {
    fontSize: 13,
    color: theme.colors.mutedInk,
    marginTop: 4,
    marginBottom: 12,
  },
  fetchButton: {
    alignSelf: 'flex-start',
  },
  fetchSpinner: {
    marginTop: 12,
  },
  resultCount: {
    marginTop: 10,
    fontSize: 13,
    fontWeight: '600',
    color: theme.colors.teal,
  },

  // -- Article card --
  articleCard: {
    borderRadius: theme.radius.lg,
    padding: 16,
    marginTop: 12,
  },
  badgeRow: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 6,
    marginBottom: 8,
  },
  badge: {
    backgroundColor: theme.colors.paleTeal,
    borderRadius: 8,
    paddingHorizontal: 8,
    paddingVertical: 3,
  },
  badgeText: {
    fontSize: 11,
    fontWeight: '600',
    color: theme.colors.teal,
  },
  articleTitle: {
    fontSize: 15,
    fontWeight: '600',
    color: theme.colors.ink,
    lineHeight: 20,
  },
  articleSummary: {
    fontSize: 13,
    color: theme.colors.mutedInk,
    marginTop: 6,
    lineHeight: 18,
  },
  articleMeta: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    marginTop: 8,
  },
  articleSource: {
    fontSize: 12,
    fontWeight: '600',
    color: theme.colors.mutedInk,
    textTransform: 'uppercase',
    letterSpacing: 0.5,
  },

  // -- Modal --
  modalOverlay: {
    flex: 1,
    backgroundColor: 'rgba(0,0,0,0.4)',
    justifyContent: 'flex-end',
  },
  modalContent: {
    backgroundColor: theme.colors.card,
    borderTopLeftRadius: theme.radius.xl,
    borderTopRightRadius: theme.radius.xl,
    padding: 24,
    paddingBottom: 40,
  },
  modalTitle: {
    fontSize: 20,
    fontWeight: '700',
    color: theme.colors.ink,
    marginBottom: 20,
  },
  fieldLabel: {
    fontSize: 13,
    fontWeight: '600',
    color: theme.colors.mutedInk,
    marginBottom: 6,
    marginTop: 12,
  },
  input: {
    borderWidth: 1,
    borderColor: theme.colors.line,
    borderRadius: theme.radius.md,
    backgroundColor: '#fff',
    paddingHorizontal: 14,
    paddingVertical: 10,
    color: theme.colors.ink,
    fontSize: 14,
  },
  keywordsInput: {
    minHeight: 60,
    textAlignVertical: 'top',
  },
  modalActions: {
    flexDirection: 'row',
    gap: 12,
    marginTop: 24,
  },
  modalBtn: {
    flex: 1,
  },
});
