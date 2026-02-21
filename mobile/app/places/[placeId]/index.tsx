import React from 'react';
import { useLocalSearchParams } from 'expo-router';

import { PlaceEditorScreen } from '@/components/place/PlaceEditorScreen';

export default function PlaceDetailScreen() {
  const { placeId } = useLocalSearchParams<{ placeId: string }>();
  const placeParam = Array.isArray(placeId) ? placeId[0] : placeId;
  return <PlaceEditorScreen placeId={placeParam} />;
}
