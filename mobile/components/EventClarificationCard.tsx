import React, { useState } from 'react';
import { StyleSheet, Text, TextInput, View } from 'react-native';

import { Button } from '@/components/Button';
import { theme } from '@/theme';

type EventClarificationData = {
  type: 'clarification_needed';
  questions: string[];
  partial_extraction: Record<string, unknown>;
  original_message: string;
  clarification_id?: string;
};

type EventClarificationCardProps = {
  data: EventClarificationData;
  onSubmit: (answer: string) => void;
};

export function EventClarificationCard({ data, onSubmit }: EventClarificationCardProps) {
  const [answer, setAnswer] = useState('');
  const [isSubmitting, setIsSubmitting] = useState(false);

  const handleSubmit = () => {
    if (!answer.trim() || isSubmitting) return;
    setIsSubmitting(true);
    try {
      onSubmit(answer.trim());
    } finally {
      setIsSubmitting(false);
      setAnswer('');
    }
  };

  return (
    <View style={styles.card}>
      <Text style={styles.kicker}>More details needed</Text>
      <View style={styles.questionList}>
        {data.questions.map((question, index) => (
          <Text key={`${index}-${question}`} style={styles.question}>
            {index + 1}. {question}
          </Text>
        ))}
      </View>
      <TextInput
        value={answer}
        onChangeText={setAnswer}
        placeholder="Add the missing details..."
        style={styles.input}
        multiline
      />
      <Button
        label={isSubmitting ? 'Sending...' : 'Submit details'}
        onPress={handleSubmit}
        disabled={!answer.trim() || isSubmitting}
        variant="primary"
        style={styles.submitButton}
      />
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
    color: theme.colors.accentDeep,
    fontWeight: '600',
  },
  questionList: {
    marginTop: 8,
    gap: 6,
  },
  question: {
    fontSize: 14,
    color: theme.colors.ink,
  },
  input: {
    marginTop: 12,
    minHeight: 70,
    borderWidth: 1,
    borderColor: theme.colors.line,
    borderRadius: theme.radius.md,
    paddingHorizontal: 12,
    paddingVertical: 10,
    backgroundColor: '#fff',
    color: theme.colors.ink,
  },
  submitButton: {
    marginTop: 12,
  },
});
