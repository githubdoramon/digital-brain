export function birthdayPickerValue(value: string): string {
  return value.startsWith('--') ? `2000${value.slice(1)}` : value;
}

export function formatBirthday(value: string): string {
  if (!value) return '';
  if (!value.startsWith('--')) return value;
  const parsed = new Date(`${birthdayPickerValue(value)}T00:00:00`);
  return Number.isNaN(parsed.getTime())
    ? value
    : parsed.toLocaleDateString(undefined, { month: 'long', day: 'numeric' });
}
