import { AppState, type AppStateStatus } from 'react-native';

import { reportLocationDebugEvent } from '@/location/debugState';

let currentAppState: AppStateStatus = AppState.currentState;
let lastAppStateChangeAt: string | null = null;
let trackingStarted = false;

export function ensureAppStateTracking(): void {
  if (trackingStarted) {
    return;
  }

  trackingStarted = true;
  currentAppState = AppState.currentState;
  reportLocationDebugEvent('app_state_initialized', {
    payload: {
      app_state: currentAppState,
    },
    recordInHistory: false,
  });
  AppState.addEventListener('change', (nextState) => {
    const previousState = currentAppState;
    currentAppState = nextState;
    lastAppStateChangeAt = new Date().toISOString();
    reportLocationDebugEvent('app_state_changed', {
      payload: {
        previous_app_state: previousState,
        app_state: nextState,
        changed_at: lastAppStateChangeAt,
      },
      recordInHistory: false,
    });
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
