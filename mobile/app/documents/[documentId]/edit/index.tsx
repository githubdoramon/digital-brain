import { Stack, useLocalSearchParams } from 'expo-router';
import React from 'react';

import { DocumentEditorScreen } from '@/components/document/DocumentEditorScreen';

type RouteParams = {
  documentId?: string;
};

export default function EditDocumentScreen() {
  const params = useLocalSearchParams<RouteParams>();
  const documentId = Array.isArray(params.documentId) ? params.documentId[0] : params.documentId;

  return (
    <>
      <Stack.Screen options={{ headerTitle: 'Edit document' }} />
      <DocumentEditorScreen mode="edit" documentId={documentId} />
    </>
  );
}
