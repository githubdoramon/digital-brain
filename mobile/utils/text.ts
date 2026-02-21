/**
 * Normalise a string for accent-insensitive, case-insensitive search matching.
 * Strips diacritics (e.g. é → e, ñ → n) and lowercases the result.
 */
export const normalizeSearch = (value: string): string =>
  value
    .normalize('NFD')
    .replace(/\p{Diacritic}/gu, '')
    .toLowerCase();

/**
 * Decode a URL-encoded route parameter, falling back to the raw value on error.
 */
export function normalizeRouteParam(value: string | undefined): string {
  if (!value) return '';
  try {
    return decodeURIComponent(value);
  } catch {
    return value;
  }
}
