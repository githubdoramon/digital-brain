import { AppState, type AppStateStatus } from 'react-native';

let currentAppState: AppStateStatus = AppState.currentState;
let lastAppStateChangeAt: string | null = null;
let trackingStarted = false;

export function ensureAppStateTracking(): void {
  if (trackingStarted) {
    return;
  }

  trackingStarted = true;
  currentAppState = AppState.currentState;
  AppState.addEventListener('change', (nextState) => {
    currentAppState = nextState;
    lastAppStateChangeAt = new Date().toISOString();
  });
}

export function getLocationRuntimeState(): {
  appState: AppStateStatus | 'unknown';
  lastAppStateChangeAt: string | null;
} {
  return {
    appState: currentAppState || 'unknown',
    lastAppStateChangeAt,
  };
}
