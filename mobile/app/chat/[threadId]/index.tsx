import { useLocalSearchParams } from 'expo-router';
import React from 'react';

import { ChatConversationScreen } from '@/app/home/brain';

export default function HistoricalChatThreadScreen() {
  const params = useLocalSearchParams<{
    threadId?: string | string[];
    messageId?: string | string[];
  }>();
  const threadIdParam = params.threadId;
  const threadId = Array.isArray(threadIdParam) ? threadIdParam[0] : threadIdParam;

  const messageId = Array.isArray(params.messageId) ? params.messageId[0] : params.messageId;
  return (
    <ChatConversationScreen
      mode="thread"
      initialThreadId={threadId ?? null}
      initialMessageId={messageId ?? null}
    />
  );
}
