import AsyncStorage from '@react-native-async-storage/async-storage';

export type StoredChatSession = {
  threadId: string | null;
  pendingEventId: string | null;
};

const CHAT_SESSION_KEY = 'chat.session';

export async function loadChatSession(): Promise<StoredChatSession | null> {
  const storedRaw = await AsyncStorage.getItem(CHAT_SESSION_KEY);
  if (!storedRaw) return null;
  try {
    return JSON.parse(storedRaw) as StoredChatSession;
  } catch (error) {
    return null;
  }
}

export async function saveChatSession(session: StoredChatSession): Promise<void> {
  await AsyncStorage.setItem(CHAT_SESSION_KEY, JSON.stringify(session));
}
