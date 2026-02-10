import DateTimePicker, { DateType, useDefaultStyles } from 'react-native-ui-datepicker';
import React, { useEffect, useMemo, useState } from 'react';
import { Modal, Pressable, StyleSheet, Text, View } from 'react-native';

import { theme } from '@/theme';

import { formatValueForMode, parseValueToDate, PickerMode } from './helpers';

type Props = {
  visible: boolean;
  mode: PickerMode;
  value?: string;
  onClose: () => void;
  onConfirm: (value: string) => void;
};

function resolvePickerDate(value: DateType): Date | null {
  if (!value) return null;
  if (value instanceof Date) return value;
  if (typeof value === 'string' || typeof value === 'number') {
    const parsed = new Date(value);
    return Number.isNaN(parsed.getTime()) ? null : parsed;
  }
  const maybeDayjs = value as { toDate?: () => Date };
  if (typeof maybeDayjs.toDate === 'function') {
    const parsed = maybeDayjs.toDate();
    return parsed instanceof Date && !Number.isNaN(parsed.getTime()) ? parsed : null;
  }
  return null;
}

export function UiDirectiveDateTimePickerSheet({
  visible,
  mode,
  value,
  onClose,
  onConfirm,
}: Props) {
  const defaultPickerStyles = useDefaultStyles();
  const isTimeOnly = mode === 'time';
  console.log('mode', mode);
  const initialDate = useMemo(() => parseValueToDate(value, mode), [mode, value]);
  const [draftDate, setDraftDate] = useState<Date>(initialDate);

  useEffect(() => {
    if (visible) {
      setDraftDate(parseValueToDate(value, mode));
    }
  }, [visible, value, mode]);

  const title =
    mode === 'datetime' ? 'Pick date and time' : mode === 'time' ? 'Pick time' : 'Pick a date';

  return (
    <Modal visible={visible} transparent animationType="slide" onRequestClose={onClose}>
      <View style={styles.modalContainer} pointerEvents="box-none">
        <Pressable style={styles.modalBackdrop} onPress={onClose} />
        <View style={styles.modalSheet} pointerEvents="auto">
          <View style={styles.header}>
            <Pressable
              onPress={onClose}
              style={({ pressed }) => [styles.headerAction, pressed && styles.actionPressed]}
              accessibilityRole="button"
              accessibilityLabel="Cancel picker"
            >
              <Text style={styles.cancelText}>Cancel</Text>
            </Pressable>
            <Text style={styles.title}>{title}</Text>
            <Pressable
              onPress={() => {
                onConfirm(formatValueForMode(draftDate, mode));
              }}
              style={({ pressed }) => [styles.headerAction, pressed && styles.actionPressed]}
              accessibilityRole="button"
              accessibilityLabel="Confirm picker value"
            >
              <Text style={styles.doneText}>Done</Text>
            </Pressable>
          </View>

          <DateTimePicker
            mode="single"
            date={draftDate}
            timePicker={mode !== 'date'}
            initialView={isTimeOnly ? 'time' : 'day'}
            hideHeader={isTimeOnly}
            hideWeekdays={isTimeOnly}
            disableMonthPicker={isTimeOnly}
            disableYearPicker={isTimeOnly}
            onChange={({ date }) => {
              const resolved = resolvePickerDate(date);
              if (resolved) {
                setDraftDate(resolved);
              }
            }}
            styles={{
              ...defaultPickerStyles,
              today: {
                ...defaultPickerStyles.today,
                borderColor: theme.colors.accent,
              },
              selected: {
                ...defaultPickerStyles.selected,
                backgroundColor: theme.colors.accent,
              },
              selected_label: {
                ...defaultPickerStyles.selected_label,
                color: '#fff',
              },
              day: {
                ...defaultPickerStyles.day,
                borderRadius: 10,
              },
            }}
            style={[styles.picker, isTimeOnly && styles.timePicker]}
          />
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
    paddingTop: 12,
    borderTopLeftRadius: 22,
    borderTopRightRadius: 22,
    paddingBottom: 12,
    zIndex: 2,
  },
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingHorizontal: 16,
    paddingBottom: 8,
    borderBottomWidth: 1,
    borderBottomColor: theme.colors.line,
  },
  title: {
    fontSize: 14,
    fontWeight: '600',
    color: theme.colors.ink,
  },
  headerAction: {
    minHeight: 44,
    minWidth: 44,
    paddingVertical: 6,
    paddingHorizontal: 4,
    justifyContent: 'center',
    alignItems: 'center',
  },
  actionPressed: {
    opacity: 0.72,
  },
  doneText: {
    fontSize: 13,
    fontWeight: '600',
    color: theme.colors.accentDeep,
  },
  cancelText: {
    fontSize: 13,
    fontWeight: '600',
    color: theme.colors.mutedInk,
  },
  picker: {
    height: 360,
  },
  timePicker: {
    height: 300,
  },
});
