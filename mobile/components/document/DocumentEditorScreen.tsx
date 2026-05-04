import Ionicons from '@expo/vector-icons/Ionicons';
import { File } from 'expo-file-system';
import { router } from 'expo-router';
import React from 'react';
import {
  ActivityIndicator,
  Alert,
  Keyboard,
  KeyboardAvoidingView,
  Platform,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { apiFetch } from '@/api/client';
import { useAuth } from '@/auth/AuthContext';
import { AppPressable as Pressable } from '@/components/AppPressable';
import { Button } from '@/components/Button';
import { Card } from '@/components/Card';
import { FloatingSaveButton } from '@/components/FloatingSaveButton';
import { useAppNotice } from '@/hooks/useAppNotice';
import { theme } from '@/theme';
import { matchesContactSearch } from '@/utils/contactSearch';

type LinkedContact = {
  contact_id: string;
  display_name: string;
};

type ContactOption = {
  contact_id: string;
  display_name: string;
  aliases?: string[];
};

type DocumentDetail = {
  document_id: string;
  title: string;
  tags?: string[];
  description?: string | null;
  document_date?: string | null;
  file_name: string;
  file_mime?: string | null;
  file_size?: number | null;
  content_preview?: string | null;
  linked_contacts?: LinkedContact[];
};

type Props = {
  mode: 'create' | 'edit';
  documentId?: string;
};

type PickedFile = {
  uri: string;
  name: string;
  mimeType: string;
};

type DocumentEditorSnapshot = {
  title: string;
  description: string;
  tagsText: string;
  documentDateText: string;
  contactIds: string[];
};

function listToComma(values: string[] | undefined): string {
  return (values || []).join(', ');
}

function commaToList(value: string): string[] {
  return value
    .split(',')
    .map((item) => item.trim())
    .filter(Boolean);
}

function formatLinkedContactNames(contacts: LinkedContact[]): string {
  return contacts.map((contact) => contact.display_name || contact.contact_id).join(', ');
}

function normalizeDateInput(value: string): string | null {
  const trimmed = value.trim();
  if (!trimmed) return null;
  const parsed = new Date(trimmed);
  if (Number.isNaN(parsed.getTime())) {
    return null;
  }
  return parsed.toISOString();
}

function inferMimeTypeFromName(name: string): string {
  const lower = name.toLowerCase();
  if (lower.endsWith('.pdf')) return 'application/pdf';
  if (lower.endsWith('.txt')) return 'text/plain';
  if (lower.endsWith('.md')) return 'text/markdown';
  if (lower.endsWith('.jpg') || lower.endsWith('.jpeg')) return 'image/jpeg';
  if (lower.endsWith('.png')) return 'image/png';
  if (lower.endsWith('.heic')) return 'image/heic';
  if (lower.endsWith('.docx')) return 'application/vnd.openxmlformats-officedocument.wordprocessingml.document';
  return 'application/octet-stream';
}

function formatFileSize(bytes?: number | null): string {
  if (!bytes || bytes <= 0) return 'Unknown size';
  const units = ['B', 'KB', 'MB', 'GB'];
  let size = bytes;
  let unitIndex = 0;
  while (size >= 1024 && unitIndex < units.length - 1) {
    size /= 1024;
    unitIndex += 1;
  }
  return `${size.toFixed(size >= 10 || unitIndex === 0 ? 0 : 1)} ${units[unitIndex]}`;
}

function buildSnapshot({
  title,
  description,
  tagsText,
  documentDateText,
  selectedContacts,
}: {
  title: string;
  description: string;
  tagsText: string;
  documentDateText: string;
  selectedContacts: LinkedContact[];
}): DocumentEditorSnapshot {
  return {
    title: title.trim(),
    description: description.trim(),
    tagsText: tagsText.trim(),
    documentDateText: documentDateText.trim(),
    contactIds: selectedContacts.map((contact) => contact.contact_id).sort(),
  };
}

function ContactSuggestions({
  contacts,
  query,
  selectedIds,
  onSelect,
}: {
  contacts: ContactOption[];
  query: string;
  selectedIds: Set<string>;
  onSelect: (contact: ContactOption) => void;
}) {
  const suggestions = React.useMemo(() => {
    const trimmed = query.trim();
    if (!trimmed) return [];
    return contacts
      .filter((contact) => !selectedIds.has(contact.contact_id))
      .filter((contact) => matchesContactSearch(contact, trimmed))
      .slice(0, 6);
  }, [contacts, query, selectedIds]);

  if (suggestions.length === 0) return null;

  return (
    <View style={styles.suggestionList}>
      {suggestions.map((contact) => (
        <Pressable
          key={contact.contact_id}
          accessibilityRole="button"
          accessibilityLabel={`Add ${contact.display_name}`}
          onPress={() => onSelect(contact)}
          style={({ pressed }) => [styles.suggestionRow, pressed && styles.suggestionPressed]}
        >
          <View style={styles.suggestionBody}>
            <Text style={styles.suggestionTitle}>{contact.display_name}</Text>
            {contact.aliases?.length ? (
              <Text style={styles.suggestionMeta}>{contact.aliases.join(', ')}</Text>
            ) : null}
          </View>
          <Ionicons name="person-add-outline" size={18} color={theme.colors.accentDeep} />
        </Pressable>
      ))}
    </View>
  );
}

export function DocumentEditorScreen({ mode, documentId }: Props) {
  const insets = useSafeAreaInsets();
  const { token } = useAuth();
  const { showSuccess, showError } = useAppNotice();
  const isCreate = mode === 'create';
  const [keyboardHeight, setKeyboardHeight] = React.useState(0);

  const [isLoading, setIsLoading] = React.useState(!isCreate);
  const [isSaving, setIsSaving] = React.useState(false);
  const [title, setTitle] = React.useState('');
  const [description, setDescription] = React.useState('');
  const [tagsText, setTagsText] = React.useState('');
  const [documentDateText, setDocumentDateText] = React.useState('');
  const [selectedContacts, setSelectedContacts] = React.useState<LinkedContact[]>([]);
  const [contactQuery, setContactQuery] = React.useState('');
  const [contacts, setContacts] = React.useState<ContactOption[]>([]);
  const [selectedFile, setSelectedFile] = React.useState<PickedFile | null>(null);
  const [existingDocument, setExistingDocument] = React.useState<DocumentDetail | null>(null);
  const [error, setError] = React.useState<string | null>(null);
  const [initialSnapshot, setInitialSnapshot] = React.useState<DocumentEditorSnapshot>({
    title: '',
    description: '',
    tagsText: '',
    documentDateText: '',
    contactIds: [],
  });

  React.useEffect(() => {
    const showEvent = Platform.OS === 'ios' ? 'keyboardWillShow' : 'keyboardDidShow';
    const hideEvent = Platform.OS === 'ios' ? 'keyboardWillHide' : 'keyboardDidHide';

    const showSubscription = Keyboard.addListener(showEvent, (event) => {
      setKeyboardHeight(event.endCoordinates.height);
    });
    const hideSubscription = Keyboard.addListener(hideEvent, () => {
      setKeyboardHeight(0);
    });

    return () => {
      showSubscription.remove();
      hideSubscription.remove();
    };
  }, []);

  React.useEffect(() => {
    let active = true;
    (async () => {
      try {
        const response = (await apiFetch('/mobile/contacts', { token })) as { contacts: ContactOption[] };
        if (active) {
          setContacts(response.contacts || []);
        }
      } catch (loadError) {
        if (active) {
          console.warn('[document-editor] contacts load failed', loadError);
        }
      }
    })();
    return () => {
      active = false;
    };
  }, [token]);

  React.useEffect(() => {
    if (isCreate || !documentId) {
      setInitialSnapshot({
        title: '',
        description: '',
        tagsText: '',
        documentDateText: '',
        contactIds: [],
      });
      setIsLoading(false);
      return;
    }
    let active = true;
    (async () => {
      try {
        const response = (await apiFetch(`/mobile/documents/${encodeURIComponent(documentId)}`, {
          token,
        })) as DocumentDetail;
        if (!active) return;
        setExistingDocument(response);
        setTitle(response.title || '');
        setDescription(response.description || '');
        setTagsText(listToComma(response.tags));
        setDocumentDateText(response.document_date || '');
        const nextContacts = response.linked_contacts || [];
        setSelectedContacts(nextContacts);
        setInitialSnapshot(
          buildSnapshot({
            title: response.title || '',
            description: response.description || '',
            tagsText: listToComma(response.tags),
            documentDateText: response.document_date || '',
            selectedContacts: nextContacts,
          }),
        );
      } catch (loadError) {
        if (!active) return;
        const message = loadError instanceof Error ? loadError.message : 'Unable to load document.';
        setError(message);
      } finally {
        if (active) {
          setIsLoading(false);
        }
      }
    })();
    return () => {
      active = false;
    };
  }, [documentId, isCreate, token]);

  const selectedContactIds = React.useMemo(
    () => new Set(selectedContacts.map((contact) => contact.contact_id)),
    [selectedContacts],
  );

  const hasUnsavedChanges = React.useMemo(() => {
    if (isCreate) {
      return Boolean(
        selectedFile ||
          title.trim() ||
          description.trim() ||
          tagsText.trim() ||
          documentDateText.trim() ||
          selectedContacts.length,
      );
    }
    if (error) {
      return false;
    }
    return (
      JSON.stringify(
        buildSnapshot({
          title,
          description,
          tagsText,
          documentDateText,
          selectedContacts,
        }),
      ) !== JSON.stringify(initialSnapshot)
    );
  }, [
    description,
    documentDateText,
    error,
    initialSnapshot,
    isCreate,
    selectedContacts,
    selectedFile,
    tagsText,
    title,
  ]);

  const handlePickFile = React.useCallback(async () => {
    try {
      const picked = await File.pickFileAsync();
      const file = Array.isArray(picked) ? picked[0] : picked;
      if (!file) return;
      const nextName = file.name || 'upload.bin';
      setSelectedFile({
        uri: file.uri,
        name: nextName,
        mimeType: file.type || inferMimeTypeFromName(nextName),
      });
      if (!title.trim()) {
        setTitle(nextName.replace(/\.[^.]+$/, ''));
      }
    } catch (pickError) {
      console.warn('[document-editor] file pick failed', pickError);
    }
  }, [title]);

  const handleAddContact = React.useCallback((contact: ContactOption) => {
    setSelectedContacts((current) => {
      if (current.some((item) => item.contact_id === contact.contact_id)) {
        return current;
      }
      return [...current, { contact_id: contact.contact_id, display_name: contact.display_name }];
    });
    setContactQuery('');
  }, []);

  const handleRemoveContact = React.useCallback((contactId: string) => {
    setSelectedContacts((current) => current.filter((contact) => contact.contact_id !== contactId));
  }, []);

  const handleClearContacts = React.useCallback(() => {
    setSelectedContacts([]);
    setContactQuery('');
  }, []);

  const handleSave = React.useCallback(async () => {
    const normalizedDate = normalizeDateInput(documentDateText);
    if (documentDateText.trim() && !normalizedDate) {
      Alert.alert('Invalid date', 'Use a valid ISO date/time or a date your device can parse.');
      return;
    }

    if (isCreate && !selectedFile) {
      Alert.alert('File required', 'Choose a file before uploading the document.');
      return;
    }

    setIsSaving(true);
    try {
      if (isCreate && selectedFile) {
        const formData = new FormData();
        if (title.trim()) formData.append('title', title.trim());
        if (description.trim()) formData.append('description', description.trim());
        if (tagsText.trim()) formData.append('tags', JSON.stringify(commaToList(tagsText)));
        if (normalizedDate) formData.append('document_date', normalizedDate);
        if (selectedContacts.length > 0) {
          formData.append(
            'contact_ids',
            JSON.stringify(selectedContacts.map((contact) => contact.contact_id)),
          );
        }
        formData.append('file', {
          uri: selectedFile.uri,
          name: selectedFile.name,
          type: selectedFile.mimeType,
        } as unknown as Blob);

        const created = (await apiFetch('/mobile/ingest/document', {
          method: 'POST',
          body: formData,
          token,
        })) as DocumentDetail;

        showSuccess('Document uploaded.');

        router.replace({
          pathname: '/documents/[documentId]',
          params: { documentId: created.document_id },
        });
        return;
      }

      if (!documentId) return;

      await apiFetch(`/mobile/documents/${encodeURIComponent(documentId)}`, {
        method: 'PATCH',
        token,
        body: JSON.stringify({
          title: title.trim(),
          description: description.trim() || null,
          tags: commaToList(tagsText),
          document_date: normalizedDate,
          contact_ids: selectedContacts.map((contact) => contact.contact_id),
        }),
      });

      showSuccess('Document updated.');

      router.back();
    } catch (saveError) {
      console.warn('[document-editor] save failed', saveError);
      showError(
        isCreate ? 'Unable to upload this document right now.' : 'Unable to save document metadata right now.',
      );
    } finally {
      setIsSaving(false);
    }
  }, [
    description,
    documentDateText,
    documentId,
    isCreate,
    selectedContacts,
    selectedFile,
    showError,
    showSuccess,
    tagsText,
    title,
    token,
  ]);

  if (isLoading) {
    return (
      <View style={[styles.centerState, { paddingTop: insets.top + 96 }]}> 
        <ActivityIndicator color={theme.colors.accentDeep} />
        <Text style={styles.stateText}>{isCreate ? 'Preparing editor...' : 'Loading document...'}</Text>
      </View>
    );
  }

  return (
    <KeyboardAvoidingView
      style={styles.container}
      behavior={Platform.OS === 'ios' ? 'padding' : undefined}
      keyboardVerticalOffset={Platform.OS === 'ios' ? insets.top + 24 : 0}
    >
      <ScrollView
        contentContainerStyle={{
          paddingTop: insets.top + 76,
          paddingHorizontal: 16,
          paddingBottom: insets.bottom + keyboardHeight + 128,
          gap: 14,
        }}
        keyboardShouldPersistTaps="handled"
      >
        {error ? (
          <Card style={styles.errorCard}>
            <Text style={styles.errorTitle}>Unable to open editor</Text>
            <Text style={styles.errorText}>{error}</Text>
          </Card>
        ) : null}

        <Card style={styles.sectionCard}>
          <Text style={styles.sectionTitle}>{isCreate ? 'File' : 'Document'}</Text>
          {isCreate ? (
            <>
              <Button label={selectedFile ? 'Choose another file' : 'Choose file'} variant="secondary" onPress={() => void handlePickFile()} />
              {selectedFile ? (
                <View style={styles.fileInfoBlock}>
                  <Text style={styles.fileMetaStrong}>{selectedFile.name}</Text>
                  <Text style={styles.fileMeta}>{selectedFile.mimeType}</Text>
                </View>
              ) : (
                <Text style={styles.helperText}>Pick the file you want to upload.</Text>
              )}
            </>
          ) : (
            <View style={styles.fileInfoBlock}>
              <Text style={styles.fileMetaStrong}>{existingDocument?.file_name || 'Document file'}</Text>
              <Text style={styles.fileMeta}>
                {[existingDocument?.file_mime || null, formatFileSize(existingDocument?.file_size)].filter(Boolean).join(' - ')}
              </Text>
              <Text style={styles.helperText}>Update the metadata and linked contacts for this document.</Text>
            </View>
          )}
        </Card>

        <Card style={styles.sectionCard}>
          <Text style={styles.sectionTitle}>Metadata</Text>
          <TextInput value={title} onChangeText={setTitle} placeholder="Title" placeholderTextColor={theme.colors.mutedInk} style={styles.input} />
          <TextInput value={description} onChangeText={setDescription} placeholder="Description" placeholderTextColor={theme.colors.mutedInk} style={[styles.input, styles.multilineInput]} multiline textAlignVertical="top" />
          <TextInput value={tagsText} onChangeText={setTagsText} placeholder="Tags, comma separated" placeholderTextColor={theme.colors.mutedInk} style={styles.input} />
          <TextInput value={documentDateText} onChangeText={setDocumentDateText} placeholder="Document date (ISO or parseable date)" placeholderTextColor={theme.colors.mutedInk} style={styles.input} autoCapitalize="none" />
        </Card>

        <Card style={styles.sectionCard}>
          <Text style={styles.sectionTitle}>Linked contacts</Text>
          {selectedContacts.length > 0 ? (
            <View style={styles.chipWrap}>
              {selectedContacts.map((contact) => (
                <Pressable
                  key={contact.contact_id}
                  onPress={() => handleRemoveContact(contact.contact_id)}
                  accessibilityRole="button"
                  accessibilityLabel={`Remove ${contact.display_name}`}
                  style={({ pressed }) => [styles.chip, pressed && styles.suggestionPressed]}
                >
                  <Text style={styles.chipText}>{contact.display_name}</Text>
                  <Ionicons name="close" size={14} color={theme.colors.accentDeep} />
                </Pressable>
              ))}
            </View>
          ) : (
            <Text style={styles.helperText}>No linked contacts yet.</Text>
          )}
          {selectedContacts.length > 0 ? (
            <Button label="Clear contacts" variant="clear" onPress={handleClearContacts} />
          ) : null}
          <TextInput value={contactQuery} onChangeText={setContactQuery} placeholder="Search contacts to link" placeholderTextColor={theme.colors.mutedInk} style={styles.input} />
          <ContactSuggestions contacts={contacts} query={contactQuery} selectedIds={selectedContactIds} onSelect={handleAddContact} />
          {!isCreate && selectedContacts.length > 0 ? (
            <Text style={styles.helperText}>Current links: {formatLinkedContactNames(selectedContacts)}</Text>
          ) : null}
        </Card>
      </ScrollView>

      <FloatingSaveButton
        visible={hasUnsavedChanges}
        onPress={() => void handleSave()}
        loading={isSaving}
        disabled={isSaving || (isCreate && !selectedFile)}
        bottomOffset={insets.bottom + keyboardHeight + 20}
      />
    </KeyboardAvoidingView>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: theme.colors.background,
  },
  centerState: {
    flex: 1,
    alignItems: 'center',
    gap: 10,
  },
  stateText: {
    color: theme.colors.mutedInk,
    fontSize: 14,
  },
  errorCard: {
    padding: 14,
    gap: 8,
  },
  errorTitle: {
    color: theme.colors.accentDeep,
    fontWeight: '700',
    fontSize: 16,
  },
  errorText: {
    color: theme.colors.mutedInk,
    fontSize: 14,
    lineHeight: 20,
  },
  sectionCard: {
    padding: 14,
    gap: 10,
  },
  sectionTitle: {
    color: theme.colors.ink,
    fontSize: 16,
    fontWeight: '700',
  },
  input: {
    borderWidth: 1,
    borderColor: theme.colors.line,
    borderRadius: theme.radius.md,
    backgroundColor: '#fff',
    color: theme.colors.ink,
    paddingHorizontal: 12,
    paddingVertical: Platform.select({ ios: 12, default: 10 }),
    fontSize: 14,
  },
  multilineInput: {
    minHeight: 92,
  },
  fileMeta: {
    color: theme.colors.ink,
    fontSize: 14,
    lineHeight: 20,
  },
  fileMetaStrong: {
    color: theme.colors.ink,
    fontSize: 15,
    lineHeight: 22,
    fontWeight: '700',
  },
  fileInfoBlock: {
    gap: 4,
  },
  helperText: {
    color: theme.colors.mutedInk,
    fontSize: 13,
    lineHeight: 18,
  },
  chipWrap: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 8,
  },
  chip: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    borderRadius: 999,
    paddingHorizontal: 10,
    paddingVertical: 8,
    backgroundColor: '#f6efe8',
    borderWidth: 1,
    borderColor: '#ead9ca',
  },
  chipText: {
    color: theme.colors.ink,
    fontSize: 13,
    fontWeight: '600',
  },
  suggestionList: {
    gap: 8,
  },
  suggestionRow: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: 10,
    borderRadius: theme.radius.md,
    borderWidth: 1,
    borderColor: theme.colors.line,
    backgroundColor: '#fff',
    paddingHorizontal: 12,
    paddingVertical: 10,
  },
  suggestionPressed: {
    opacity: 0.76,
  },
  suggestionBody: {
    flex: 1,
    gap: 2,
  },
  suggestionTitle: {
    color: theme.colors.ink,
    fontSize: 14,
    fontWeight: '600',
  },
  suggestionMeta: {
    color: theme.colors.mutedInk,
    fontSize: 12,
  },
});
