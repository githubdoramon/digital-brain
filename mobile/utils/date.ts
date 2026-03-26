function twoDigits(value: number): string {
  return value.toString().padStart(2, '0');
}

export function formatDateOnlyLocal(value: Date): string {
  return `${value.getFullYear()}-${twoDigits(value.getMonth() + 1)}-${twoDigits(value.getDate())}`;
}

export function formatTodayLocal(): string {
  return formatDateOnlyLocal(new Date());
}
