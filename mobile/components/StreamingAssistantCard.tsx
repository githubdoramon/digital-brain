import React, { useEffect, useRef, useState } from 'react';
import { ScrollView, StyleSheet, Text, View } from 'react-native';

import { theme } from '@/theme';

type StreamingAssistantCardProps = {
  content: string;
  progressChip?: string;
};

const MAX_CONTENT_HEIGHT = 90;

export function StreamingAssistantCard({ content, progressChip }: StreamingAssistantCardProps) {
  const contentScrollRef = useRef<ScrollView>(null);
  const [isOverflowing, setIsOverflowing] = useState(false);

  useEffect(() => {
    if (isOverflowing) {
      contentScrollRef.current?.scrollToEnd({ animated: true });
    }
  }, [content, isOverflowing]);

  return (
    <View style={styles.container}>
      <ScrollView
        ref={contentScrollRef}
        style={[styles.contentScrollBase, isOverflowing ? styles.contentScrollCapped : null]}
        contentContainerStyle={styles.contentScrollInner}
        showsVerticalScrollIndicator={false}
        nestedScrollEnabled
        onContentSizeChange={(_, contentHeight) => {
          setIsOverflowing(contentHeight > MAX_CONTENT_HEIGHT);
        }}
      >
        <Text style={styles.content} selectable>
          {content}
        </Text>
      </ScrollView>
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
    backgroundColor: '#E9ECF0',
    borderWidth: 1,
    borderColor: '#CCD2D9',
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
  contentScrollBase: {
    minHeight: 0,
    flexGrow: 0,
  },
  contentScrollCapped: {
    maxHeight: MAX_CONTENT_HEIGHT,
  },
  contentScrollInner: {
    paddingBottom: 2,
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
