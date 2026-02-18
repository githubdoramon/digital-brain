import React from 'react';
import { StyleSheet, Text, View } from 'react-native';

import { theme } from '@/theme';

type StreamingAssistantCardProps = {
  content: string;
  progressChip?: string;
};

export function StreamingAssistantCard({ content, progressChip }: StreamingAssistantCardProps) {
  return (
    <View style={styles.container}>
      <Text style={styles.content} selectable>
        {content}
      </Text>
      {progressChip ? (
        <View style={styles.progressChipWrap}>
          <View style={styles.progressChip}>
            <Text style={styles.progressChipText}>{progressChip}</Text>
          </View>
        </View>
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    alignSelf: 'flex-start',
    backgroundColor: '#EEF1F4',
    borderWidth: 1,
    borderColor: '#D7DEE6',
    borderRadius: theme.radius.lg,
    marginBottom: 12,
    maxWidth: '90%',
    paddingVertical: 12,
    paddingHorizontal: 16,
  },
  content: {
    fontSize: 13,
    lineHeight: 18,
    color: '#3A4756',
  },
  progressChipWrap: {
    marginTop: 10,
    flexDirection: 'row',
  },
  progressChip: {
    borderRadius: 999,
    borderWidth: 1,
    borderColor: '#CBD7E6',
    backgroundColor: '#EEF4FB',
    paddingHorizontal: 10,
    paddingVertical: 5,
  },
  progressChipText: {
    fontSize: 10,
    color: '#30445A',
    fontWeight: '600',
  },
});
