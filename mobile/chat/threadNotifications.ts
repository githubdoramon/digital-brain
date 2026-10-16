import AsyncStorage from '@react-native-async-storage/async-storage';

import type { ThreadSummary } from '@/chat/threads';

const THREAD_READ_AT_KEY = 'chat.thread-read-at.v1';

type ThreadReadAt = Record<string, number>;

async function loadReadAt(): Promise<ThreadReadAt> {
  const raw = await AsyncStorage.getItem(THREAD_READ_AT_KEY);
  if (!raw) return {};
  try {
    const parsed = JSON.parse(raw) as ThreadReadAt;
    return parsed && typeof parsed === 'object' ? parsed : {};
  } catch {
    return {};
  }
}

export async function markThreadRead(threadId: string): Promise<void> {
  const readAt = await loadReadAt();
  readAt[threadId] = Date.now();
  await AsyncStorage.setItem(THREAD_READ_AT_KEY, JSON.stringify(readAt));
}

export async function getUnreadThreadIds(threads: ThreadSummary[]): Promise<Set<string>> {
  const readAt = await loadReadAt();
  let changed = false;
  const unreadIds = new Set<string>();

  for (const thread of threads) {
    if (readAt[thread.id] === undefined) {
      // Existing threads start as read when notifications are first introduced.
      readAt[thread.id] = Date.now();
      changed = true;
      continue;
    }
    const updatedAt = new Date(thread.updated_at).getTime();
    if (Number.isFinite(updatedAt) && updatedAt > readAt[thread.id]) {
      unreadIds.add(thread.id);
    }
  }

  if (changed) {
    await AsyncStorage.setItem(THREAD_READ_AT_KEY, JSON.stringify(readAt));
  }
  return unreadIds;
}
