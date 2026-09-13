import React from 'react';
import { Switch, Text, View } from 'react-native';

import { useAuth } from '@/auth/AuthContext';
import { Card } from '@/components/Card';
import { useAppNotice } from '@/hooks/useAppNotice';
import { syncBackgroundLocationTracking } from '@/location/backgroundLocation';
import {
  isLocationTrackingEnabled,
  setLocationTrackingEnabled,
} from '@/location/trackingPreference';
import { theme } from '@/theme';

export function BackgroundLocationControl() {
  const { token } = useAuth();
  const { showError } = useAppNotice();
  const [enabled, setEnabled] = React.useState<boolean | null>(null);
  const [busy, setBusy] = React.useState(false);
  React.useEffect(() => {
    void isLocationTrackingEnabled().then(setEnabled);
  }, []);
  const update = async (next: boolean) => {
    setBusy(true);
    try {
      await setLocationTrackingEnabled(next);
      setEnabled(next);
      await syncBackgroundLocationTracking(Boolean(token));
    } catch (error) {
      showError(error instanceof Error ? error.message : 'Could not update location tracking.');
    } finally {
      setBusy(false);
    }
  };
  return (
    <Card style={{ marginBottom: 16, padding: 16 }}>
      <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
        <Text style={{ color: theme.colors.ink, fontWeight: '600', fontSize: 16 }}>
          Location tracking
        </Text>
        <Switch
          accessibilityLabel="Location tracking"
          value={enabled === true}
          disabled={enabled === null || busy}
          onValueChange={(next) => void update(next)}
        />
      </View>
      <Text style={{ color: theme.colors.mutedInk, marginTop: 8 }}>
        Request location about every ten minutes after moving at least 50 metres. Updates may arrive
        in batches with up to twenty minutes of delay. Active features share one Digital Brain
        notification.
      </Text>
    </Card>
  );
}
