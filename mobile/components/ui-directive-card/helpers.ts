import type { UiDirectiveBlock, UiDirectiveField } from '@/chat/uiDirectives';

export type PickerMode = 'date' | 'datetime' | 'time';

function twoDigits(value: number) {
  return value.toString().padStart(2, '0');
}

export function actionIdForBlock(block: UiDirectiveBlock) {
  if (block.action_id?.trim()) {
    return block.action_id.trim();
  }
  if (block.type === 'clarification_form') return 'submit_form';
  if (block.type === 'choice_buttons') return 'select_option';
  return 'view_info';
}

export function isPickerField(field: UiDirectiveField) {
  return field.kind === 'date' || field.kind === 'datetime' || field.kind === 'time';
}

export function pickerModeFromField(field: UiDirectiveField): PickerMode {
  if (field.kind === 'time') return 'time';
  if (field.kind === 'datetime') return 'datetime';
  return 'date';
}

export function formatDateForValue(date: Date) {
  return `${date.getFullYear()}-${twoDigits(date.getMonth() + 1)}-${twoDigits(date.getDate())}`;
}

export function formatTimeForValue(date: Date) {
  return `${twoDigits(date.getHours())}:${twoDigits(date.getMinutes())}`;
}

export function formatDateTimeForValue(date: Date) {
  return `${formatDateForValue(date)}T${formatTimeForValue(date)}`;
}

export function formatValueForMode(date: Date, mode: PickerMode) {
  if (mode === 'time') return formatTimeForValue(date);
  if (mode === 'datetime') return formatDateTimeForValue(date);
  return formatDateForValue(date);
}

export function parseValueToDate(value: string | undefined, mode: PickerMode): Date {
  if (!value?.trim()) {
    return new Date();
  }

  if (mode === 'time') {
    const match = value.trim().match(/^(\d{1,2}):(\d{2})$/);
    if (match) {
      const date = new Date();
      const hours = Number.parseInt(match[1], 10);
      const minutes = Number.parseInt(match[2], 10);
      if (!Number.isNaN(hours) && !Number.isNaN(minutes)) {
        date.setHours(Math.min(23, Math.max(0, hours)));
        date.setMinutes(Math.min(59, Math.max(0, minutes)));
        date.setSeconds(0);
        date.setMilliseconds(0);
        return date;
      }
    }
    return new Date();
  }

  if (mode === 'date') {
    const parsed = new Date(`${value.trim()}T00:00:00`);
    return Number.isNaN(parsed.getTime()) ? new Date() : parsed;
  }

  const parsed = new Date(value.trim());
  if (!Number.isNaN(parsed.getTime())) {
    return parsed;
  }
  const localParsed = new Date(value.trim().replace(' ', 'T'));
  return Number.isNaN(localParsed.getTime()) ? new Date() : localParsed;
}

export function displayValueForField(field: UiDirectiveField, rawValue: string) {
  if (!rawValue) return '';

  if (field.kind === 'date') {
    const parsed = parseValueToDate(rawValue, 'date');
    return parsed.toLocaleDateString(undefined, {
      year: 'numeric',
      month: 'short',
      day: 'numeric',
    });
  }

  if (field.kind === 'datetime') {
    const parsed = parseValueToDate(rawValue, 'datetime');
    return parsed.toLocaleString(undefined, {
      year: 'numeric',
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    });
  }

  if (field.kind === 'time') {
    const parsed = parseValueToDate(rawValue, 'time');
    return parsed.toLocaleTimeString(undefined, {
      hour: '2-digit',
      minute: '2-digit',
    });
  }

  return rawValue;
}

export function keyboardTypeForFieldKind(kind: string) {
  if (kind === 'number') return 'numeric' as const;
  if (kind === 'email') return 'email-address' as const;
  if (kind === 'url') return 'url' as const;
  return 'default' as const;
}

