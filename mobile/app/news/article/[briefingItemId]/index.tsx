import Ionicons from '@expo/vector-icons/Ionicons';
import { useLocalSearchParams, useRouter } from 'expo-router';
import React from 'react';
import { ActivityIndicator, Linking, StyleSheet, Text, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { apiFetch } from '@/api/client';
import { useAuth } from '@/auth/AuthContext';
import { AppPressable as Pressable } from '@/components/AppPressable';
import { theme } from '@/theme';

type FeedbackState = 'up' | 'down' | null;

function getParam(value: string | string[] | undefined): string {
  if (Array.isArray(value)) {
    return value[0] ?? '';
  }
  return value ?? '';
}

function normalizeUrl(url: string): string {
  const raw = (url || '').trim();
  if (!raw) return '';
  if (raw.startsWith('http://') || raw.startsWith('https://')) {
    return raw;
  }
  return `https://${raw}`;
}

function loadWebViewComponent() {
  try {
    return require('react-native-webview').WebView as React.ComponentType<Record<string, unknown>>;
  } catch {
    return null;
  }
}

export default function NewsArticleScreen() {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const { token, refreshToken } = useAuth();
  const params = useLocalSearchParams<{
    briefingItemId?: string;
    url?: string;
    briefingId?: string;
    clusterId?: string;
    source?: string;
    sourceDomain?: string;
    topicLabel?: string;
    title?: string;
  }>();

  const briefingItemId = getParam(params.briefingItemId);
  const briefingId = getParam(params.briefingId);
  const clusterId = getParam(params.clusterId);
  const source = getParam(params.source);
  const sourceDomain = getParam(params.sourceDomain);
  const topicLabel = getParam(params.topicLabel);
  const title = getParam(params.title) || 'Article';
  const url = normalizeUrl(getParam(params.url));

  const [webLoading, setWebLoading] = React.useState(true);
  const [feedbackState, setFeedbackState] = React.useState<FeedbackState>(null);
  const [submittingFeedback, setSubmittingFeedback] = React.useState(false);
  const openTrackedRef = React.useRef(false);
  const WebViewComponent = React.useMemo(() => loadWebViewComponent(), []);

  const trackInteraction = React.useCallback(
    async (eventType: 'article_opened' | 'article_feedback_up' | 'article_feedback_down') => {
      if (!url) return;
      await apiFetch('/mobile/news/interactions', {
        method: 'POST',
        body: JSON.stringify({
          events: [
            {
              event_type: eventType,
              briefing_id: briefingId || null,
              briefing_item_id: briefingItemId || null,
              cluster_id: clusterId || null,
              source: source || null,
              source_domain: sourceDomain || null,
              topic_label: topicLabel || null,
              metadata: {
                article_url: url,
              },
            },
          ],
        }),
        token,
        onAuthExpired: refreshToken,
      });
    },
    [briefingId, briefingItemId, clusterId, refreshToken, source, sourceDomain, token, topicLabel, url],
  );

  React.useEffect(() => {
    if (!url || openTrackedRef.current) {
      return;
    }
    openTrackedRef.current = true;
    void trackInteraction('article_opened').catch(() => {
      openTrackedRef.current = false;
    });
  }, [trackInteraction, url]);

  const submitFeedback = React.useCallback(
    async (next: FeedbackState) => {
      if (!next || !url) return;
      if (submittingFeedback) return;
      setSubmittingFeedback(true);
      try {
        await trackInteraction(next === 'up' ? 'article_feedback_up' : 'article_feedback_down');
        setFeedbackState(next);
      } finally {
        setSubmittingFeedback(false);
      }
    },
    [submittingFeedback, trackInteraction, url],
  );

  const openExternal = React.useCallback(async () => {
    if (!url) return;
    try {
      await Linking.openURL(url);
    } catch {
      // no-op
    }
  }, [url]);

  return (
    <View style={styles.container}>
      <View style={[styles.topBar, { paddingTop: insets.top + 8 }]}> 
        <Pressable onPress={() => router.back()} style={styles.iconButton}>
          <Ionicons name="chevron-back" size={22} color={theme.colors.ink} />
        </Pressable>
        <Text style={styles.title} numberOfLines={1}>
          {title}
        </Text>
        <View style={styles.actions}>
          <Pressable onPress={openExternal} style={styles.iconButton}>
            <Ionicons name="open-outline" size={18} color={theme.colors.ink} />
          </Pressable>
          <Pressable
            onPress={() => {
              void submitFeedback('up');
            }}
            disabled={submittingFeedback}
            style={[styles.iconButton, feedbackState === 'up' && styles.feedbackPositive]}
          >
            <Ionicons name="thumbs-up-outline" size={18} color={theme.colors.ink} />
          </Pressable>
          <Pressable
            onPress={() => {
              void submitFeedback('down');
            }}
            disabled={submittingFeedback}
            style={[styles.iconButton, feedbackState === 'down' && styles.feedbackNegative]}
          >
            <Ionicons name="thumbs-down-outline" size={18} color={theme.colors.ink} />
          </Pressable>
        </View>
      </View>

      {!url ? (
        <View style={styles.emptyState}>
          <Text style={styles.emptyText}>Could not open this article URL.</Text>
        </View>
      ) : !WebViewComponent ? (
        <View style={styles.emptyState}>
          <Text style={styles.emptyText}>
            In-app reader is unavailable in this build. Use the open icon to read externally.
          </Text>
        </View>
      ) : (
        <View style={styles.webviewWrap}>
          <WebViewComponent
            source={{ uri: url }}
            onLoadEnd={() => setWebLoading(false)}
            startInLoadingState
            javaScriptEnabled
            domStorageEnabled
          />
          {webLoading ? (
            <View style={styles.loadingOverlay}>
              <ActivityIndicator color={theme.colors.accentDeep} />
            </View>
          ) : null}
        </View>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: theme.colors.background,
  },
  topBar: {
    paddingHorizontal: 12,
    paddingBottom: 10,
    borderBottomWidth: 1,
    borderBottomColor: theme.colors.line,
    backgroundColor: theme.colors.card,
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
  },
  iconButton: {
    width: 34,
    height: 34,
    borderRadius: 17,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: '#f4eee5',
  },
  actions: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
  },
  title: {
    flex: 1,
    fontSize: 14,
    fontWeight: '700',
    color: theme.colors.ink,
  },
  webviewWrap: {
    flex: 1,
  },
  loadingOverlay: {
    position: 'absolute',
    top: 0,
    left: 0,
    right: 0,
    bottom: 0,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: 'rgba(255,255,255,0.35)',
  },
  feedbackPositive: {
    backgroundColor: '#dff6e7',
  },
  feedbackNegative: {
    backgroundColor: '#fbe4e2',
  },
  emptyState: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
    padding: 24,
  },
  emptyText: {
    color: theme.colors.mutedInk,
    fontSize: 15,
    textAlign: 'center',
  },
});
