import React from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';

import { theme } from '@/theme';

type EventConfirmationData = {
  type: 'event_confirmation';
  preview_id: string;
  extracted: {
    title: string;
    summary: string;
    when: string | null;
    where: string | null;
    who: string[];
    documents: string[];
    tags: string[];
    types: string[];
  };
  resolution: {
    new_entities: {
      contacts: { display_name: string; query: string }[];
      places: { name: string; query: string }[];
      documents: { reference: string }[];
    };
  };
  relationship_suggestions?: {
    from_display_name: string;
    to_display_name: string;
    relationship_type: string;
    reciprocal_type: string;
    confidence: string;
    reasoning: string;
  }[];
};

type EventProposalCardProps = {
  data: EventConfirmationData;
  onConfirm: (previewId: string) => void | Promise<void>;
  onCancel: (previewId: string) => void | Promise<void>;
  isSubmitting?: boolean;
};

const formatDate = (value: string | null) => {
  if (!value) return 'Not specified';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString();
};

const renderList = (items: string[], emptyLabel: string) => {
  if (!items.length) {
    return <Text style={styles.valueMuted}>{emptyLabel}</Text>;
  }
  return <Text style={styles.value}>{items.join(', ')}</Text>;
};

export function EventProposalCard({ data, onConfirm, onCancel, isSubmitting }: EventProposalCardProps) {
  const { extracted, resolution, relationship_suggestions: relationships = [] } = data;
  const newContacts = resolution?.new_entities?.contacts ?? [];
  const newPlaces = resolution?.new_entities?.places ?? [];
  const newDocuments = resolution?.new_entities?.documents ?? [];

  return (
    <View style={styles.card}>
      <Text style={styles.kicker}>Event proposal</Text>
      <Text style={styles.title}>{extracted.title || 'Untitled event'}</Text>
      <Text style={styles.summary}>{extracted.summary || 'No summary provided.'}</Text>

      <View style={styles.section}>
        <Text style={styles.label}>When</Text>
        <Text style={styles.value}>{formatDate(extracted.when)}</Text>
      </View>

      <View style={styles.section}>
        <Text style={styles.label}>Where</Text>
        <Text style={styles.value}>{extracted.where || 'Not specified'}</Text>
      </View>

      <View style={styles.section}>
        <Text style={styles.label}>Who</Text>
        {renderList(extracted.who ?? [], 'No participants detected')}
      </View>

      <View style={styles.sectionRow}>
        <View style={styles.sectionItem}>
          <Text style={styles.label}>Tags</Text>
          {renderList(extracted.tags ?? [], 'None')}
        </View>
        <View style={styles.sectionItem}>
          <Text style={styles.label}>Types</Text>
          {renderList(extracted.types ?? [], 'Generic')}
        </View>
      </View>

      {(newContacts.length > 0 || newPlaces.length > 0 || newDocuments.length > 0) && (
        <View style={styles.section}>
          <Text style={styles.label}>New entities</Text>
          {newContacts.length > 0 && (
            <Text style={styles.value}>Contacts: {newContacts.map((contact) => contact.display_name).join(', ')}</Text>
          )}
          {newPlaces.length > 0 && (
            <Text style={styles.value}>Places: {newPlaces.map((place) => place.name).join(', ')}</Text>
          )}
          {newDocuments.length > 0 && (
            <Text style={styles.value}>Documents: {newDocuments.map((doc) => doc.reference).join(', ')}</Text>
          )}
        </View>
      )}

      {relationships.length > 0 && (
        <View style={styles.section}>
          <Text style={styles.label}>Suggested relationships</Text>
          {relationships.map((relationship, index) => (
            <Text key={`${relationship.from_display_name}-${relationship.to_display_name}-${index}`} style={styles.value}>
              {relationship.from_display_name} - {relationship.relationship_type} - {relationship.to_display_name}
            </Text>
          ))}
        </View>
      )}

      <View style={styles.actions}>
        <Pressable
          onPress={() => onCancel(data.preview_id)}
          disabled={isSubmitting}
          style={({ pressed }) => [styles.secondaryButton, pressed && styles.buttonPressed]}
        >
          <Text style={styles.secondaryButtonText}>{isSubmitting ? 'Working...' : 'Cancel'}</Text>
        </Pressable>
        <Pressable
          onPress={() => onConfirm(data.preview_id)}
          disabled={isSubmitting}
          style={({ pressed }) => [styles.primaryButton, pressed && styles.buttonPressed]}
        >
          <Text style={styles.primaryButtonText}>{isSubmitting ? 'Creating...' : 'Create event'}</Text>
        </Pressable>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  card: {
    marginTop: 12,
    padding: 16,
    borderRadius: theme.radius.md,
    backgroundColor: theme.colors.background,
    borderWidth: 1,
    borderColor: theme.colors.line,
  },
  kicker: {
    fontSize: 12,
    textTransform: 'uppercase',
    letterSpacing: 2,
    color: theme.colors.teal,
    fontWeight: '600',
  },
  title: {
    marginTop: 6,
    fontSize: 18,
    fontWeight: '700',
    color: theme.colors.ink,
  },
  summary: {
    marginTop: 8,
    fontSize: 14,
    lineHeight: 20,
    color: theme.colors.mutedInk,
  },
  section: {
    marginTop: 14,
  },
  sectionRow: {
    marginTop: 14,
    flexDirection: 'row',
    gap: 12,
  },
  sectionItem: {
    flex: 1,
  },
  label: {
    fontSize: 12,
    fontWeight: '600',
    color: theme.colors.mutedInk,
    textTransform: 'uppercase',
    letterSpacing: 1,
  },
  value: {
    marginTop: 4,
    fontSize: 14,
    color: theme.colors.ink,
  },
  valueMuted: {
    marginTop: 4,
    fontSize: 14,
    color: theme.colors.mutedInk,
  },
  actions: {
    marginTop: 16,
    flexDirection: 'row',
    gap: 12,
  },
  primaryButton: {
    flex: 1,
    backgroundColor: theme.colors.accent,
    paddingVertical: 12,
    borderRadius: theme.radius.md,
    alignItems: 'center',
  },
  secondaryButton: {
    flex: 1,
    backgroundColor: theme.colors.card,
    paddingVertical: 12,
    borderRadius: theme.radius.md,
    alignItems: 'center',
    borderWidth: 1,
    borderColor: theme.colors.line,
  },
  primaryButtonText: {
    color: '#fff',
    fontWeight: '600',
  },
  secondaryButtonText: {
    color: theme.colors.ink,
    fontWeight: '600',
  },
  buttonPressed: {
    transform: [{ scale: 0.98 }],
  },
});
