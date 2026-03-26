import { DateType } from 'react-native-ui-datepicker';
import React, { useEffect, useMemo, useState } from 'react';
import { StyleSheet, Text, View } from 'react-native';

import { AppPressable as Pressable } from '@/components/AppPressable';
import { BottomSheet } from '@/components/BottomSheet';
import { LightDateTimePicker } from '@/components/LightDateTimePicker';
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
  const isTimeOnly = mode === 'time';
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
    <BottomSheet visible={visible} onClose={onClose} baseBottomPadding={12}>
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

      <LightDateTimePicker
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
          today: {
            borderColor: theme.colors.accent,
          },
          selected: {
            backgroundColor: theme.colors.accent,
          },
          selected_label: {
            color: '#fff',
          },
          day: {
            borderRadius: 10,
          },
        }}
        style={[styles.picker, isTimeOnly && styles.timePicker]}
      />
    </BottomSheet>
  );
}

const styles = StyleSheet.create({
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
