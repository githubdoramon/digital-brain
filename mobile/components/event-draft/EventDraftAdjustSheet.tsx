import React, { useEffect, useMemo, useState } from 'react';
import { Modal, Pressable, StyleSheet, Text, TextInput, View } from 'react-native';

import { Button } from '@/components/Button';
import { theme } from '@/theme';

type Props = {
  visible: boolean;
  isSubmitting?: boolean;
  onClose: () => void;
  onSubmit: (instruction: string) => void;
};

const QUICK_PROMPTS = [
  'Move it to tomorrow morning',
  'Make the summary shorter',
  'Add someone who attended',
];

export function EventDraftAdjustSheet({
  visible,
  isSubmitting = false,
  onClose,
  onSubmit,
}: Props) {
  const [instruction, setInstruction] = useState('');

  useEffect(() => {
    if (visible) {
      setInstruction('');
    }
  }, [visible]);

  const canSubmit = useMemo(() => !isSubmitting && Boolean(instruction.trim()), [instruction, isSubmitting]);

  return (
    <Modal visible={visible} transparent animationType="slide" onRequestClose={onClose}>
      <View style={styles.modalContainer} pointerEvents="box-none">
        <Pressable
          style={styles.modalBackdrop}
          onPress={() => {
            if (!isSubmitting) onClose();
          }}
        />
        <View style={styles.modalSheet} pointerEvents="auto">
          <View style={styles.headerRow}>
            <Pressable
              accessibilityRole="button"
              accessibilityLabel="Close adjustment sheet"
              disabled={isSubmitting}
              onPress={onClose}
              style={({ pressed }) => [
                styles.headerAction,
                pressed && styles.headerActionPressed,
                isSubmitting && styles.headerActionDisabled,
              ]}
            >
              <Text style={styles.cancelText}>Close</Text>
            </Pressable>
            <Text style={styles.title}>Ask AI to adjust</Text>
            <View style={styles.headerActionSpacer} />
          </View>

          <View style={styles.content}>
            <Text style={styles.helperText}>
              Describe what should change. We will regenerate the event preview before creation.
            </Text>
            <TextInput
              value={instruction}
              editable={!isSubmitting}
              onChangeText={setInstruction}
              placeholder="Example: change location to HQ and move time to 3pm"
              placeholderTextColor={theme.colors.mutedInk}
              multiline
              style={styles.textarea}
            />

            <View style={styles.quickRow}>
              {QUICK_PROMPTS.map((prompt) => (
                <Pressable
                  key={prompt}
                  accessibilityRole="button"
                  accessibilityLabel={`Use prompt: ${prompt}`}
                  disabled={isSubmitting}
                  onPress={() => setInstruction(prompt)}
                  style={({ pressed }) => [
                    styles.quickChip,
                    pressed && styles.quickChipPressed,
                    isSubmitting && styles.headerActionDisabled,
                  ]}
                >
                  <Text style={styles.quickChipText}>{prompt}</Text>
                </Pressable>
              ))}
            </View>
          </View>

          <View style={styles.footer}>
            <Button
              label="Request adjustment"
              variant="primary"
              disabled={!canSubmit}
              onPress={() => onSubmit(instruction.trim())}
              style={styles.submitButton}
            />
          </View>
        </View>
      </View>
    </Modal>
  );
}

const styles = StyleSheet.create({
  modalBackdrop: {
    ...StyleSheet.absoluteFillObject,
    backgroundColor: 'rgba(15, 18, 20, 0.3)',
    zIndex: 1,
  },
  modalContainer: {
    flex: 1,
    justifyContent: 'flex-end',
  },
  modalSheet: {
    backgroundColor: '#fff',
    borderTopLeftRadius: 22,
    borderTopRightRadius: 22,
    zIndex: 2,
  },
  headerRow: {
    minHeight: 52,
    paddingHorizontal: 12,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    borderBottomWidth: 1,
    borderBottomColor: theme.colors.line,
  },
  headerAction: {
    minWidth: 56,
    minHeight: 44,
    alignItems: 'center',
    justifyContent: 'center',
    borderRadius: theme.radius.md,
  },
  headerActionSpacer: {
    minWidth: 56,
  },
  headerActionPressed: {
    opacity: 0.75,
  },
  headerActionDisabled: {
    opacity: 0.5,
  },
  cancelText: {
    fontSize: 13,
    fontWeight: '600',
    color: theme.colors.mutedInk,
  },
  title: {
    fontSize: 15,
    fontWeight: '700',
    color: theme.colors.ink,
  },
  content: {
    paddingHorizontal: 16,
    paddingTop: 14,
    paddingBottom: 8,
    gap: 12,
  },
  helperText: {
    fontSize: 13,
    lineHeight: 19,
    color: theme.colors.mutedInk,
  },
  textarea: {
    minHeight: 110,
    borderWidth: 1,
    borderColor: theme.colors.line,
    borderRadius: theme.radius.md,
    paddingHorizontal: 12,
    paddingVertical: 10,
    textAlignVertical: 'top',
    color: theme.colors.ink,
    fontSize: 14,
    backgroundColor: '#fff',
  },
  quickRow: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 8,
  },
  quickChip: {
    minHeight: 40,
    borderRadius: 18,
    borderWidth: 1,
    borderColor: 'rgba(47, 111, 116, 0.2)',
    backgroundColor: 'rgba(47, 111, 116, 0.1)',
    justifyContent: 'center',
    paddingHorizontal: 12,
    paddingVertical: 8,
  },
  quickChipPressed: {
    opacity: 0.78,
  },
  quickChipText: {
    fontSize: 12,
    lineHeight: 16,
    color: theme.colors.teal,
    fontWeight: '600',
  },
  footer: {
    borderTopWidth: 1,
    borderTopColor: theme.colors.line,
    paddingHorizontal: 16,
    paddingTop: 12,
    paddingBottom: 16,
  },
  submitButton: {
    minHeight: 46,
  },
});
