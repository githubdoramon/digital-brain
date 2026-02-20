import AsyncStorage from '@react-native-async-storage/async-storage';

export type StoredChatSession = {
  threadId: string | null;
  pendingEventId: string | null;
};

export type StoredPendingRun = {
  runId: string;
  pendingMessageId: string;
  threadId: string | null;
  question: string;
  startedAt: number;
};

const CHAT_SESSION_KEY = 'chat.session';
const CHAT_PENDING_RUN_KEY = 'chat.pending-run';

export async function loadChatSession(): Promise<StoredChatSession | null> {
  const storedRaw = await AsyncStorage.getItem(CHAT_SESSION_KEY);
  if (!storedRaw) return null;
  try {
    return JSON.parse(storedRaw) as StoredChatSession;
  } catch {
    return null;
  }
}

export async function saveChatSession(session: StoredChatSession): Promise<void> {
  await AsyncStorage.setItem(CHAT_SESSION_KEY, JSON.stringify(session));
}

export async function loadPendingRun(): Promise<StoredPendingRun | null> {
  const storedRaw = await AsyncStorage.getItem(CHAT_PENDING_RUN_KEY);
  if (!storedRaw) return null;
  try {
    return JSON.parse(storedRaw) as StoredPendingRun;
  } catch {
    return null;
  }
}

export async function savePendingRun(run: StoredPendingRun): Promise<void> {
  await AsyncStorage.setItem(CHAT_PENDING_RUN_KEY, JSON.stringify(run));
}

export async function clearPendingRun(): Promise<void> {
  await AsyncStorage.removeItem(CHAT_PENDING_RUN_KEY);
}
