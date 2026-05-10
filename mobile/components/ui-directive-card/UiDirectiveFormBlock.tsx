import React, { useState } from 'react';
import { StyleSheet, Text, TextInput, View } from 'react-native';
import Ionicons from '@expo/vector-icons/Ionicons';

import type { UiDirectiveBlock, UiDirectiveField } from '@/chat/uiDirectives';
import { AppPressable as Pressable } from '@/components/AppPressable';
import { Button } from '@/components/Button';
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
  isSubmitLoading?: boolean;
  getFieldValue: (field: UiDirectiveField) => string;
  setFieldValue: (field: UiDirectiveField, value: string) => void;
  onFieldFocus?: () => void;
  onSubmit: () => void;
};

type ActivePicker = {
  fieldId: string;
};

function pickerIconName(field: UiDirectiveField): React.ComponentProps<typeof Ionicons>['name'] {
  if (field.kind === 'time') return 'time-outline';
  return 'calendar-outline';
}

export function UiDirectiveFormBlock({
  block,
  isSubmitting,
  isSubmitLoading = false,
  getFieldValue,
  setFieldValue,
  onFieldFocus,
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
                      disabled={isSubmitting}
                      onPress={() => setFieldValue(field, option.id)}
                      style={({ pressed }) => [
                        styles.optionButton,
                        selected && styles.optionButtonSelected,
                        pressed && styles.optionButtonPressed,
                        isSubmitting && styles.disabled,
                      ]}
                    >
                      <Text style={[styles.optionButtonText, selected && styles.optionButtonTextSelected]}>
                        {option.label}
                      </Text>
                      <Ionicons
                        name={selected ? 'checkmark-circle' : 'ellipse-outline'}
                        size={18}
                        color={selected ? theme.colors.accentDeep : theme.colors.mutedInk}
                      />
                    </Pressable>
                  );
                })}
              </View>
            ) : isPickerField(field) ? (
              <Pressable
                accessibilityRole="button"
                accessibilityLabel={`Pick ${field.label}`}
                disabled={isSubmitting}
                onPress={() => setActivePicker({ fieldId: field.id })}
                style={({ pressed }) => [
                  styles.pickerField,
                  isSubmitting && styles.disabled,
                  pressed && styles.pickerFieldPressed,
                ]}
              >
                <View style={styles.pickerFieldLeft}>
                  <Ionicons name={pickerIconName(field)} size={17} color={theme.colors.teal} />
                  <Text style={value ? styles.pickerText : styles.pickerPlaceholder}>
                    {value
                      ? displayValueForField(field, value)
                      : field.placeholder || `Select ${field.label}`}
                  </Text>
                </View>
                <Ionicons name="chevron-forward" size={16} color={theme.colors.mutedInk} />
              </Pressable>
            ) : (
              <TextInput
                value={value}
                onChangeText={(next) => setFieldValue(field, next)}
                onFocus={onFieldFocus}
                placeholder={field.placeholder || ''}
                placeholderTextColor={theme.colors.mutedInk}
                keyboardType={keyboardTypeForFieldKind(field.kind)}
                multiline={isMultiLine}
                numberOfLines={isMultiLine ? 3 : 1}
                editable={!isSubmitting}
                style={[styles.input, isMultiLine && styles.textareaInput]}
              />
            )}
          </View>
        );
      })}

      <Button
        label={block.submit_label || 'Submit'}
        variant="primary"
        disabled={isSubmitting}
        loading={isSubmitLoading}
        onPress={onSubmit}
        style={styles.submitButton}
      />

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
    gap: 12,
  },
  fieldWrap: {
    gap: 7,
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
    borderRadius: theme.radius.lg,
    paddingHorizontal: 14,
    paddingVertical: 11,
    color: theme.colors.ink,
    backgroundColor: '#fff',
    fontSize: 14,
  },
  textareaInput: {
    minHeight: 88,
    textAlignVertical: 'top',
  },
  optionRow: {
    flexDirection: 'column',
    gap: 8,
  },
  optionButton: {
    minHeight: 44,
    borderRadius: theme.radius.md,
    borderWidth: 1,
    borderColor: theme.colors.line,
    paddingHorizontal: 12,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
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
    fontSize: 14,
    lineHeight: 20,
    fontWeight: '600',
    flexShrink: 1,
  },
  optionButtonTextSelected: {
    color: theme.colors.accentDeep,
  },
  pickerField: {
    minHeight: 44,
    borderWidth: 1,
    borderColor: theme.colors.line,
    borderRadius: theme.radius.lg,
    paddingHorizontal: 12,
    paddingVertical: 10,
    backgroundColor: '#fff',
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
  },
  pickerFieldLeft: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    flexShrink: 1,
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
  submitButton: {
    minHeight: 46,
    marginTop: 2,
  },
  disabled: {
    opacity: 0.6,
  },
});
