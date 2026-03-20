import Ionicons from '@expo/vector-icons/Ionicons';
import { LinearGradient } from 'expo-linear-gradient';
import { useRouter } from 'expo-router';
import React, { useCallback, useEffect, useState } from 'react';
import {
  ActivityIndicator,
  Alert,
  Animated,
  KeyboardAvoidingView,
  Modal,
  Platform,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { apiFetch } from '@/api/client';
import { useAuth } from '@/auth/AuthContext';
import { AppPressable as Pressable } from '@/components/AppPressable';
import { Button } from '@/components/Button';
import { Card } from '@/components/Card';
import {
  COLLAPSING_CONTENT_TOP_PADDING,
  COLLAPSING_SECONDARY_TITLE_BLOCK_HEIGHT,
  COLLAPSING_TOP_BAR_HEIGHT,
  CollapsingTopBar,
} from '@/components/CollapsingTopBar';
import { theme } from '@/theme';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type UserFact = {
  fact_id: string;
  content: string;
  category: string;
  importance: number;
  created_at: string | null;
  updated_at: string | null;
};

const CATEGORY_LABELS: Record<string, { label: string; icon: string }> = {
  preference: { label: 'Preference', icon: 'heart-outline' },
  biographical: { label: 'About me', icon: 'person-outline' },
  behavioral: { label: 'Habit', icon: 'repeat-outline' },
  goal: { label: 'Goal', icon: 'flag-outline' },
  opinion: { label: 'Opinion', icon: 'chatbubble-outline' },
  constraint: { label: 'Constraint', icon: 'alert-circle-outline' },
  general: { label: 'General', icon: 'ellipsis-horizontal-outline' },
};

const CATEGORY_OPTIONS = Object.keys(CATEGORY_LABELS);

// ---------------------------------------------------------------------------
// Screen
// ---------------------------------------------------------------------------

export default function AboutMeScreen() {
  const router = useRouter();
  const { token } = useAuth();
  const insets = useSafeAreaInsets();
  const scrollY = React.useRef(new Animated.Value(0)).current;

  const [facts, setFacts] = useState<UserFact[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [editingFact, setEditingFact] = useState<UserFact | null>(null);
  const [showForm, setShowForm] = useState(false);

  const loadFacts = useCallback(async () => {
    try {
      const res = (await apiFetch('/mobile/user/facts', { token })) as UserFact[];
      setFacts(res ?? []);
    } catch {
      Alert.alert('Error', 'Could not load your facts.');
    } finally {
      setIsLoading(false);
    }
  }, [token]);

  useEffect(() => {
    loadFacts();
  }, [loadFacts]);

  const deleteFact = useCallback(
    async (factId: string) => {
      Alert.alert('Delete fact', 'This cannot be undone.', [
        { text: 'Cancel', style: 'cancel' },
        {
          text: 'Delete',
          style: 'destructive',
          onPress: async () => {
            try {
              await apiFetch(`/mobile/user/facts/${encodeURIComponent(factId)}`, {
                method: 'DELETE',
                token,
              });
              await loadFacts();
            } catch {
              Alert.alert('Error', 'Could not delete fact.');
            }
          },
        },
      ]);
    },
    [token, loadFacts],
  );

  const saveFact = useCallback(
    async (factId: string, content: string, category: string, importance: number) => {
      try {
        await apiFetch(`/mobile/user/facts/${encodeURIComponent(factId)}`, {
          method: 'PUT',
          body: JSON.stringify({ content, category, importance }),
          token,
        });
        setShowForm(false);
        setEditingFact(null);
        await loadFacts();
      } catch {
        Alert.alert('Error', 'Could not update fact.');
      }
    },
    [token, loadFacts],
  );

  const openEdit = (fact: UserFact) => {
    setEditingFact(fact);
    setShowForm(true);
  };

  // Group facts by category
  const grouped = groupByCategory(facts);

  return (
    <LinearGradient
      colors={theme.gradients.sunrise}
      style={styles.container}
    >
      <Animated.FlatList
        data={grouped}
        onScroll={Animated.event([{ nativeEvent: { contentOffset: { y: scrollY } } }], {
          useNativeDriver: false,
        })}
        scrollEventThrottle={16}
        keyExtractor={(item) =>
          item.type === 'header' ? `header-${item.category}` : item.fact.fact_id
        }
        ListHeaderComponent={
          <View style={styles.header}>
            <Text style={styles.subtitle}>
              Things your Brain has learned about you from conversations. Edit or remove anything
              that's wrong.
            </Text>
          </View>
        }
        ListEmptyComponent={
          isLoading ? (
            <ActivityIndicator color={theme.colors.accent} style={styles.loader} />
          ) : (
            <Card style={styles.emptyCard}>
              <Ionicons
                name="sparkles-outline"
                size={32}
                color={theme.colors.teal}
                style={styles.emptyIcon}
              />
              <Text style={styles.emptyTitle}>Nothing here yet</Text>
              <Text style={styles.emptyText}>
                As you chat with your Brain, it will automatically learn your preferences, habits,
                and interests. They'll show up here.
              </Text>
            </Card>
          )
        }
        renderItem={({ item }) => {
          if (item.type === 'header') {
            return <CategoryHeader category={item.category} count={item.count} />;
          }
          return (
            <FactRow fact={item.fact} onEdit={openEdit} onDelete={deleteFact} />
          );
        }}
        contentContainerStyle={[
          {
            paddingTop:
              insets.top +
              COLLAPSING_TOP_BAR_HEIGHT +
              COLLAPSING_CONTENT_TOP_PADDING +
              COLLAPSING_SECONDARY_TITLE_BLOCK_HEIGHT,
            paddingBottom: insets.bottom + 24,
          },
        ]}
        showsVerticalScrollIndicator={false}
      />

      <CollapsingTopBar
        title="Profile"
        secondaryTitle="About me"
        scrollY={scrollY}
        onPressBack={() => router.back()}
      />

      <FactEditModal
        visible={showForm}
        fact={editingFact}
        onSave={saveFact}
        onDelete={(factId) => {
          setShowForm(false);
          setEditingFact(null);
          deleteFact(factId);
        }}
        onCancel={() => {
          setShowForm(false);
          setEditingFact(null);
        }}
      />
    </LinearGradient>
  );
}

// ---------------------------------------------------------------------------
// Grouping helper
// ---------------------------------------------------------------------------

type GroupedItem =
  | { type: 'header'; category: string; count: number }
  | { type: 'fact'; fact: UserFact };

function groupByCategory(facts: UserFact[]): GroupedItem[] {
  if (facts.length === 0) return [];

  const buckets = new Map<string, UserFact[]>();
  for (const f of facts) {
    const cat = f.category || 'general';
    if (!buckets.has(cat)) buckets.set(cat, []);
    buckets.get(cat)!.push(f);
  }

  // Sort categories by the order in CATEGORY_OPTIONS, unknown last
  const sortedCats = [...buckets.keys()].sort((a, b) => {
    const ia = CATEGORY_OPTIONS.indexOf(a);
    const ib = CATEGORY_OPTIONS.indexOf(b);
    return (ia === -1 ? 999 : ia) - (ib === -1 ? 999 : ib);
  });

  const result: GroupedItem[] = [];
  for (const cat of sortedCats) {
    const items = buckets.get(cat)!;
    result.push({ type: 'header', category: cat, count: items.length });
    for (const f of items) {
      result.push({ type: 'fact', fact: f });
    }
  }
  return result;
}

// ---------------------------------------------------------------------------
// Category header
// ---------------------------------------------------------------------------

function CategoryHeader({ category, count }: { category: string; count: number }) {
  const meta = CATEGORY_LABELS[category] ?? CATEGORY_LABELS.general;
  return (
    <View style={styles.catHeader}>
      <Ionicons name={meta.icon as any} size={16} color={theme.colors.teal} />
      <Text style={styles.catLabel}>
        {meta.label} ({count})
      </Text>
    </View>
  );
}

// ---------------------------------------------------------------------------
// Fact row
// ---------------------------------------------------------------------------

function FactRow({
  fact,
  onEdit,
  onDelete,
}: {
  fact: UserFact;
  onEdit: (f: UserFact) => void;
  onDelete: (id: string) => void;
}) {
  return (
    <Card style={styles.factCard}>
      <Pressable style={styles.factTap} onPress={() => onEdit(fact)}>
        <View style={styles.factContent}>
          <Text style={styles.factText}>{fact.content}</Text>
          <View style={styles.factMeta}>
            <ImportanceDots importance={fact.importance} />
          </View>
        </View>
        <Pressable onPress={() => onDelete(fact.fact_id)} style={styles.deleteHit}>
          <Ionicons name="trash-outline" size={16} color="#c0392b" />
        </Pressable>
      </Pressable>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Importance indicator (dots)
// ---------------------------------------------------------------------------

function ImportanceDots({ importance }: { importance: number }) {
  // Map 1-10 to 1-5 dots
  const dots = Math.max(1, Math.ceil(importance / 2));
  return (
    <View style={styles.dotsRow}>
      {Array.from({ length: 5 }, (_, i) => (
        <View
          key={i}
          style={[styles.dot, i < dots ? styles.dotFilled : styles.dotEmpty]}
        />
      ))}
    </View>
  );
}

// ---------------------------------------------------------------------------
// Edit modal
// ---------------------------------------------------------------------------

type FactEditModalProps = {
  visible: boolean;
  fact: UserFact | null;
  onSave: (factId: string, content: string, category: string, importance: number) => void;
  onDelete: (factId: string) => void;
  onCancel: () => void;
};

function FactEditModal({ visible, fact, onSave, onDelete, onCancel }: FactEditModalProps) {
  const [content, setContent] = useState('');
  const [category, setCategory] = useState('general');
  const [importance, setImportance] = useState(5);

  useEffect(() => {
    if (visible && fact) {
      setContent(fact.content);
      setCategory(fact.category || 'general');
      setImportance(fact.importance);
    }
  }, [visible, fact]);

  const handleSave = () => {
    const trimmed = content.trim();
    if (!trimmed) {
      Alert.alert('Validation', 'Content cannot be empty.');
      return;
    }
    if (!fact) return;
    onSave(fact.fact_id, trimmed, category, importance);
  };

  const handleDelete = () => {
    if (!fact) return;
    onDelete(fact.fact_id);
  };

  return (
    <Modal visible={visible} animationType="slide" transparent onRequestClose={onCancel}>
      <KeyboardAvoidingView
        style={styles.modalOverlay}
        behavior={Platform.OS === 'ios' ? 'padding' : 'height'}
        keyboardVerticalOffset={Platform.OS === 'ios' ? 16 : 0}
      >
        <View style={styles.modalContent}>
          <ScrollView
            keyboardShouldPersistTaps="handled"
            contentContainerStyle={styles.modalBody}
            showsVerticalScrollIndicator={false}
          >
            <Text style={styles.modalTitle}>Edit fact</Text>

            <Text style={styles.fieldLabel}>What your Brain knows</Text>
            <TextInput
              style={[styles.input, styles.contentInput]}
              value={content}
              onChangeText={setContent}
              placeholder="e.g. Prefers rock music"
              placeholderTextColor={theme.colors.mutedInk}
              multiline
              autoFocus
            />

            <Text style={styles.fieldLabel}>Category</Text>
            <View style={styles.categoryPicker}>
              {CATEGORY_OPTIONS.map((cat) => {
                const meta = CATEGORY_LABELS[cat];
                const selected = category === cat;
                return (
                  <Pressable
                    key={cat}
                    onPress={() => setCategory(cat)}
                    style={[styles.categoryChip, selected && styles.categoryChipSelected]}
                  >
                    <Ionicons
                      name={meta.icon as any}
                      size={14}
                      color={selected ? theme.colors.card : theme.colors.teal}
                    />
                    <Text
                      style={[styles.categoryChipText, selected && styles.categoryChipTextSelected]}
                    >
                      {meta.label}
                    </Text>
                  </Pressable>
                );
              })}
            </View>

            <Text style={styles.fieldLabel}>Importance ({importance}/10)</Text>
            <View style={styles.importanceRow}>
              {Array.from({ length: 10 }, (_, i) => {
                const val = i + 1;
                const active = val <= importance;
                return (
                  <Pressable key={val} onPress={() => setImportance(val)} style={styles.importanceHit}>
                    <View style={[styles.importanceBar, active && styles.importanceBarActive]} />
                  </Pressable>
                );
              })}
            </View>

            <View style={styles.modalActions}>
              <Button
                label="Delete"
                variant="danger"
                onPress={handleDelete}
                style={styles.modalBtn}
              />
              <Button label="Cancel" variant="secondary" onPress={onCancel} style={styles.modalBtn} />
              <Button label="Save" variant="primary" onPress={handleSave} style={styles.modalBtn} />
            </View>
          </ScrollView>
        </View>
      </KeyboardAvoidingView>
    </Modal>
  );
}

// ---------------------------------------------------------------------------
// Styles
// ---------------------------------------------------------------------------

const styles = StyleSheet.create({
  container: {
    flex: 1,
    paddingHorizontal: 18
  },

  // -- Header --
  header: {
    marginBottom: 20,
  },
  subtitle: {
    marginTop: 6,
    fontSize: 14,
    color: theme.colors.mutedInk,
    lineHeight: 20,
  },

  // -- Loading / empty --
  loader: {
    paddingVertical: 40,
  },
  emptyCard: {
    borderRadius: theme.radius.xl,
    padding: 28,
    alignItems: 'center',
  },
  emptyIcon: {
    marginBottom: 12,
  },
  emptyTitle: {
    fontSize: 17,
    fontWeight: '700',
    color: theme.colors.ink,
    marginBottom: 8,
  },
  emptyText: {
    textAlign: 'center',
    color: theme.colors.mutedInk,
    fontSize: 14,
    lineHeight: 20,
  },

  // -- Category header --
  catHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    marginTop: 20,
    marginBottom: 8,
    paddingHorizontal: 4,
  },
  catLabel: {
    fontSize: 13,
    fontWeight: '700',
    color: theme.colors.teal,
    textTransform: 'uppercase',
    letterSpacing: 1,
  },

  // -- Fact card --
  factCard: {
    borderRadius: theme.radius.lg,
    padding: 0,
    marginBottom: 8,
    overflow: 'hidden',
  },
  factTap: {
    flexDirection: 'row',
    alignItems: 'center',
    padding: 14,
    gap: 8,
  },
  factContent: {
    flex: 1,
  },
  factText: {
    fontSize: 15,
    color: theme.colors.ink,
    lineHeight: 21,
  },
  factMeta: {
    flexDirection: 'row',
    alignItems: 'center',
    marginTop: 6,
  },
  deleteHit: {
    padding: 8,
  },

  // -- Importance dots --
  dotsRow: {
    flexDirection: 'row',
    gap: 3,
  },
  dot: {
    width: 6,
    height: 6,
    borderRadius: 3,
  },
  dotFilled: {
    backgroundColor: theme.colors.teal,
  },
  dotEmpty: {
    backgroundColor: theme.colors.line,
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
    maxHeight: '90%',
  },
  modalBody: {
    padding: 24,
    paddingBottom: 40,
  },
  modalTitle: {
    fontSize: 20,
    fontWeight: '700',
    color: theme.colors.ink,
    marginBottom: 16,
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
  contentInput: {
    minHeight: 60,
    textAlignVertical: 'top',
  },

  // -- Category picker --
  categoryPicker: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 8,
  },
  categoryChip: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 4,
    paddingHorizontal: 10,
    paddingVertical: 6,
    borderRadius: 20,
    backgroundColor: theme.colors.paleTeal,
  },
  categoryChipSelected: {
    backgroundColor: theme.colors.teal,
  },
  categoryChipText: {
    fontSize: 12,
    fontWeight: '600',
    color: theme.colors.teal,
  },
  categoryChipTextSelected: {
    color: theme.colors.card,
  },

  // -- Importance bar picker --
  importanceRow: {
    flexDirection: 'row',
    alignItems: 'flex-end',
    gap: 4,
    height: 28,
  },
  importanceHit: {
    flex: 1,
    height: 28,
    justifyContent: 'flex-end',
  },
  importanceBar: {
    width: '100%',
    borderRadius: 3,
    height: 8,
    backgroundColor: theme.colors.line,
  },
  importanceBarActive: {
    backgroundColor: theme.colors.teal,
  },

  modalActions: {
    flexDirection: 'column',
    gap: 12,
    marginTop: 24,
  },
  modalBtn: {
    flex: 1,
  },
});
