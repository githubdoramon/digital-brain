import AsyncStorage from '@react-native-async-storage/async-storage';

const KEY = 'digitalbrain.glasses.firmware.maintenance.v1';
let startedAt: number | null = null;
let hydration: Promise<void> | null = null;

export function loadGlassesMaintenance(): Promise<void> {
  if (!hydration) {
    hydration = AsyncStorage.getItem(KEY).then((value) => {
      if (value) startedAt = Number(value) || Date.now();
    });
  }
  return hydration;
}

export function isGlassesMaintenanceActive(): boolean {
  return startedAt !== null;
}

export function getGlassesMaintenanceStartedAt(): number | null {
  return startedAt;
}

export async function setGlassesMaintenance(active: boolean): Promise<void> {
  await loadGlassesMaintenance();
  if (active) {
    const previous = startedAt;
    startedAt = Date.now();
    try {
      await AsyncStorage.setItem(KEY, String(startedAt));
    } catch (error) {
      startedAt = previous;
      throw error;
    }
  } else {
    await AsyncStorage.removeItem(KEY);
    startedAt = null;
  }
}

export async function assertGlassesNotUpdating(): Promise<void> {
  await loadGlassesMaintenance();
  if (isGlassesMaintenanceActive()) {
    throw new Error(
      'Glasses firmware update is in progress. Check its status in Smart glasses → Firmware.',
    );
  }
}
