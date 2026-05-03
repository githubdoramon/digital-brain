import { Stack } from 'expo-router';
import React from 'react';

import { DocumentEditorScreen } from '@/components/document/DocumentEditorScreen';

export default function NewDocumentScreen() {
  return (
    <>
      <Stack.Screen options={{ headerTitle: 'Upload document' }} />
      <DocumentEditorScreen mode="create" />
    </>
  );
}
