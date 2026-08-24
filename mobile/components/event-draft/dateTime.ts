function twoDigits(value: number): string {
  return value.toString().padStart(2, '0');
}

type DraftDateTimeParts = {
  year: number;
  month: number;
  day: number;
  hour: number;
  minute: number;
};

function parseDraftDateTimeParts(value: string): DraftDateTimeParts | null {
  const match = value.trim().match(
    /^(\d{4})-(\d{2})-(\d{2})(?:[T\s](\d{2}):(\d{2})(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?)?$/,
  );
  if (!match) return null;

  const year = Number(match[1]);
  const month = Number(match[2]);
  const day = Number(match[3]);
  const hour = match[4] ? Number(match[4]) : 0;
  const minute = match[5] ? Number(match[5]) : 0;
  const date = new Date(year, month - 1, day, hour, minute);
  if (
    Number.isNaN(date.getTime()) ||
    date.getFullYear() !== year ||
    date.getMonth() !== month - 1 ||
    date.getDate() !== day ||
    date.getHours() !== hour ||
    date.getMinutes() !== minute
  ) {
    return null;
  }

  return {
    year,
    month,
    day,
    hour,
    minute,
  };
}

/**
 * Format an event draft's local wall-clock value without applying its offset.
 *
 * `/event` extraction stores user-stated times with the client's offset. The
 * draft editor treats those fields as local event values, so parsing them with
 * `new Date()` would shift the displayed clock time on devices in another
 * timezone.
 */
export function formatDraftDateTime(value: string): string {
  if (!value.trim()) return 'Not specified';

  const parts = parseDraftDateTimeParts(value);
  if (parts) {
    const localDate = new Date(
      parts.year,
      parts.month - 1,
      parts.day,
      parts.hour,
      parts.minute,
    );
    return localDate.toLocaleString(undefined, {
      year: 'numeric',
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    });
  }

  return formatInstantDateTime(value);
}

/**
 * Remove an extracted offset before passing a draft value to the native picker.
 * The picker operates on local `Date` fields and must not apply the offset a
 * second time.
 */
export function draftDateTimePickerValue(value: string): string {
  const parts = parseDraftDateTimeParts(value);
  if (!parts) return value;

  return `${parts.year.toString().padStart(4, '0')}-${twoDigits(parts.month)}-${twoDigits(parts.day)}T${twoDigits(parts.hour)}:${twoDigits(parts.minute)}`;
}

export function formatInstantDateTime(value: string): string {
  if (!value.trim()) return 'Not specified';
  const normalized = value.trim().replace('Z', '+00:00');
  const parsed = new Date(normalized);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleString(undefined, {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}
