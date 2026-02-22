import { Linking, Platform } from 'react-native';

type OpenMapInput = {
  lat: number | null;
  lon: number | null;
  address?: string | null;
  name?: string | null;
};

function isValidCoordinate(value: number | null): value is number {
  return typeof value === 'number' && Number.isFinite(value);
}

function buildNativeUrl(input: OpenMapInput): string | null {
  if (!isValidCoordinate(input.lat) || !isValidCoordinate(input.lon)) {
    return null;
  }

  const lat = input.lat;
  const lon = input.lon;
  const label = (input.address || input.name || `${lat},${lon}`).trim();

  if (Platform.OS === 'ios') {
    return `http://maps.apple.com/?ll=${lat},${lon}&q=${encodeURIComponent(label)}`;
  }

  if (Platform.OS === 'android') {
    const query = label ? `${lat},${lon}(${label})` : `${lat},${lon}`;
    return `geo:${lat},${lon}?q=${encodeURIComponent(query)}`;
  }

  return `https://www.google.com/maps/search/?api=1&query=${lat},${lon}`;
}

export async function openNativeMapForPlace(input: OpenMapInput): Promise<boolean> {
  const nativeUrl = buildNativeUrl(input);
  if (!nativeUrl) return false;

  const supported = await Linking.canOpenURL(nativeUrl);
  if (supported) {
    await Linking.openURL(nativeUrl);
    return true;
  }

  const fallbackUrl = `https://www.google.com/maps/search/?api=1&query=${input.lat},${input.lon}`;
  const fallbackSupported = await Linking.canOpenURL(fallbackUrl);
  if (!fallbackSupported) return false;
  await Linking.openURL(fallbackUrl);
  return true;
}
