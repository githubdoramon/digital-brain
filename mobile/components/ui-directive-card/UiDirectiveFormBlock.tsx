import React, { useState } from 'react';
import { Pressable, StyleSheet, Text, TextInput, View } from 'react-native';

import type { UiDirectiveBlock, UiDirectiveField } from '@/chat/uiDirectives';
import { theme } from '@/theme';

import {
  displayValueForField,
  isPickerField,
  keyboardTypeForFieldKind,
  pickerModeFromField,
} from './helpers';
import { UiDirectiveDateTimePickerSheet } from './UiDirectiveDateTimePickerSheet';

type Props = {
  block: UiDirectiveBlock;
  isSubmitting: boolean;
  getFieldValue: (field: UiDirectiveField) => string;
  setFieldValue: (field: UiDirectiveField, value: string) => void;
  onSubmit: () => void;
};

type ActivePicker = {
  fieldId: string;
};

export function UiDirectiveFormBlock({
  block,
  isSubmitting,
  getFieldValue,
  setFieldValue,
  onSubmit,
}: Props) {
  const fields = block.fields || [];
  const [activePicker, setActivePicker] = useState<ActivePicker | null>(null);

  const activeField = fields.find((field) => field.id === activePicker?.fieldId) || null;

  const activePickerValue = activeField ? getFieldValue(activeField) : '';

  return (
    <View style={styles.formWrap}>
      {fields.map((field) => {
        const value = getFieldValue(field);
        const options = field.options || [];
        const isMultiLine = field.kind === 'textarea';
        const isRequired = Boolean(field.required);

        return (
          <View key={`${block.id}:${field.id}`} style={styles.fieldWrap}>
            <Text style={styles.fieldLabel}>
              {field.label}
              {isRequired ? ' *' : ''}
            </Text>

            {field.kind === 'select' ? (
              <View style={styles.optionRow}>
                {options.map((option) => {
                  const selected = value === option.id;
                  return (
                    <Pressable
                      key={`${field.id}:${option.id}`}
                      accessibilityRole="button"
                      accessibilityLabel={`${field.label}: ${option.label}`}
                      onPress={() => setFieldValue(field, option.id)}
                      style={({ pressed }) => [
                        styles.optionButton,
                        selected && styles.optionButtonSelected,
                        pressed && styles.optionButtonPressed,
                      ]}
                    >
                      <Text
                        style={[
                          styles.optionButtonText,
                          selected && styles.optionButtonTextSelected,
                        ]}
                      >
                        {option.label}
                      </Text>
                    </Pressable>
                  );
                })}
              </View>
            ) : isPickerField(field) ? (
              <Pressable
                accessibilityRole="button"
                accessibilityLabel={`Pick ${field.label}`}
                onPress={() => setActivePicker({ fieldId: field.id })}
                style={({ pressed }) => [
                  styles.pickerField,
                  pressed && styles.pickerFieldPressed,
                ]}
              >
                <Text style={value ? styles.pickerText : styles.pickerPlaceholder}>
                  {value ? displayValueForField(field, value) : field.placeholder || `Select ${field.label}`}
                </Text>
              </Pressable>
            ) : (
              <TextInput
                value={value}
                onChangeText={(next) => setFieldValue(field, next)}
                placeholder={field.placeholder || ''}
                placeholderTextColor={theme.colors.mutedInk}
                keyboardType={keyboardTypeForFieldKind(field.kind)}
                multiline={isMultiLine}
                numberOfLines={isMultiLine ? 3 : 1}
                style={[styles.input, isMultiLine && styles.textareaInput]}
              />
            )}
          </View>
        );
      })}

      <Pressable
        accessibilityRole="button"
        accessibilityLabel={block.submit_label || 'Submit'}
        disabled={isSubmitting}
        onPress={onSubmit}
        style={({ pressed }) => [
          styles.primaryButton,
          pressed && styles.primaryButtonPressed,
          isSubmitting && styles.buttonDisabled,
        ]}
      >
        <Text style={styles.primaryButtonText}>{block.submit_label || 'Submit'}</Text>
      </Pressable>

      {activeField ? (
        <UiDirectiveDateTimePickerSheet
          visible
          mode={pickerModeFromField(activeField)}
          value={activePickerValue}
          onClose={() => setActivePicker(null)}
          onConfirm={(nextValue) => {
            setFieldValue(activeField, nextValue);
            setActivePicker(null);
          }}
        />
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  formWrap: {
    gap: 10,
  },
  fieldWrap: {
    gap: 6,
  },
  fieldLabel: {
    color: theme.colors.ink,
    fontSize: 13,
    lineHeight: 18,
    fontWeight: '600',
  },
  input: {
    minHeight: 44,
    borderWidth: 1,
    borderColor: theme.colors.line,
    borderRadius: 10,
    paddingHorizontal: 10,
    paddingVertical: 10,
    color: theme.colors.ink,
    backgroundColor: '#fff',
    fontSize: 14,
  },
  textareaInput: {
    minHeight: 88,
    textAlignVertical: 'top',
  },
  optionRow: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 8,
  },
  optionButton: {
    minHeight: 44,
    borderRadius: 999,
    borderWidth: 1,
    borderColor: theme.colors.line,
    paddingHorizontal: 14,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: '#fff',
  },
  optionButtonSelected: {
    borderColor: theme.colors.accentDeep,
    backgroundColor: '#fde9e6',
  },
  optionButtonPressed: {
    opacity: 0.8,
  },
  optionButtonText: {
    color: theme.colors.ink,
    fontSize: 13,
    lineHeight: 18,
    fontWeight: '600',
  },
  optionButtonTextSelected: {
    color: theme.colors.accentDeep,
  },
  pickerField: {
    minHeight: 44,
    borderWidth: 1,
    borderColor: theme.colors.line,
    borderRadius: 10,
    paddingHorizontal: 10,
    paddingVertical: 10,
    backgroundColor: '#fff',
    justifyContent: 'center',
  },
  pickerFieldPressed: {
    borderColor: theme.colors.accent,
  },
  pickerText: {
    color: theme.colors.ink,
    fontSize: 14,
    lineHeight: 20,
  },
  pickerPlaceholder: {
    color: theme.colors.mutedInk,
    fontSize: 14,
    lineHeight: 20,
  },
  primaryButton: {
    minHeight: 44,
    borderRadius: 12,
    backgroundColor: theme.colors.ink,
    alignItems: 'center',
    justifyContent: 'center',
    paddingHorizontal: 16,
  },
  primaryButtonPressed: {
    opacity: 0.86,
  },
  primaryButtonText: {
    color: '#fff',
    fontSize: 14,
    lineHeight: 18,
    fontWeight: '700',
  },
  buttonDisabled: {
    opacity: 0.6,
  },
});
