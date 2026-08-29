import FontAwesome from '@expo/vector-icons/FontAwesome';
import { DefaultTheme, ThemeProvider } from '@react-navigation/native';
import { useFonts } from 'expo-font';
import * as IntentLauncher from 'expo-intent-launcher';
import { Stack, useRouter, useSegments } from 'expo-router';
import * as SplashScreen from 'expo-splash-screen';
import { StatusBar } from 'expo-status-bar';
import { useCallback, useEffect, useRef } from 'react';
import 'react-native-reanimated';
import { AppState, Linking, Platform } from 'react-native';
import { SafeAreaProvider } from 'react-native-safe-area-context';
import * as Notifications from 'expo-notifications';

import { AuthProvider, useAuth } from '@/auth/AuthContext';
import { TopNoticeProvider } from '@/components/top-notice';
import { syncBackgroundLocationTracking } from '@/location/backgroundLocation';
import { registerGlassesCaptureReconciliation } from '@/mentraCapture/backgroundTask';
import { ensureMentraConnection, subscribeMentraEvents } from '@/mentraCapture/sdk';
import { reconcileGlassesCaptures } from '@/mentraCapture/sync';
import { ensureAppStateTracking } from '@/location/runtimeState';
import { theme } from '@/theme';

export {
  // Catch any errors thrown by the Layout component.
  ErrorBoundary,
} from 'expo-router';

export const unstable_settings = {
  initialRouteName: 'home',
};

// Prevent the splash screen from auto-hiding before asset loading is complete.
SplashScreen.preventAutoHideAsync();

Notifications.setNotificationHandler({
  handleNotification: async () => ({
    shouldShowAlert: true,
    shouldShowBanner: true,
    shouldShowList: true,
    shouldPlaySound: true,
    shouldSetBadge: false,
  }),
});

export default function RootLayout() {
  const [loaded, error] = useFonts({
    SpaceMono: require('../assets/fonts/SpaceMono-Regular.ttf'),
    ...FontAwesome.font,
  });

  // Expo Router uses Error Boundaries to catch errors in the navigation tree.
  useEffect(() => {
    if (error) throw error;
  }, [error]);

  useEffect(() => {
    ensureAppStateTracking();
    const unsubscribe = subscribeMentraEvents(() => {
      void reconcileGlassesCaptures();
    });
    void registerGlassesCaptureReconciliation();

    // The native SDK's connection state is process-local. Restore the saved
    // device and apply camera/gallery defaults on the first app launch, then
    // reconnect on later foreground transitions without reconfiguring a live
    // camera session.
    let disposed = false;
    let foregroundSync: Promise<void> | null = null;
    const wait = (milliseconds: number) =>
      new Promise<void>((resolve) => setTimeout(resolve, milliseconds));
    const reconnect = (applyCaptureDefaults: boolean) => {
      if (disposed || foregroundSync) return;
      foregroundSync = (async () => {
        // A cold Android launch can race native SDK initialization, and a
        // resume can race Bluetooth reconnect. Retry the complete connection
        // and reconciliation sequence instead of silently losing the one
        // foreground opportunity and requiring a manual Sync tap.
        const retryDelays = [0, 1_000, 3_000];
        for (let attempt = 0; attempt < retryDelays.length; attempt += 1) {
          if (disposed) return;
          if (retryDelays[attempt] > 0) await wait(retryDelays[attempt]);
          try {
            const connected = await ensureMentraConnection({ applyCaptureDefaults });
            if (!connected || disposed) return;
            await reconcileGlassesCaptures();
            return;
          } catch {
            // The next bounded attempt handles transient SDK/Bluetooth/network
            // races. The reconciliation function publishes durable errors for
            // failures after connection succeeds.
          }
        }
      })().finally(() => {
        foregroundSync = null;
      });
      void foregroundSync;
    };
    reconnect(true);
    const appStateSubscription = AppState.addEventListener('change', (nextState) => {
      if (nextState === 'active') reconnect(false);
    });

    return () => {
      disposed = true;
      appStateSubscription.remove();
      unsubscribe();
    };
  }, []);

  if (!loaded) {
    return null;
  }

  return (
    <AuthProvider>
      <SafeAreaProvider>
        <TopNoticeProvider>
          <RootLayoutNav loaded={loaded} />
        </TopNoticeProvider>
      </SafeAreaProvider>
    </AuthProvider>
  );
}

function RootLayoutNav({ loaded }: { loaded: boolean }) {
  const router = useRouter();
  const segments = useSegments();
  const { token, isLoading } = useAuth();
  const lastNotificationResponse = Notifications.useLastNotificationResponse();
  const lastHandledNotificationIdRef = useRef<string | null>(null);

  useEffect(() => {
    if (isLoading || !loaded) return;

    SplashScreen.hideAsync();
    const inAuthGroup = segments[0] === '(auth)';
    if (!token && !inAuthGroup) {
      router.replace('/(auth)/sign-in');
    }
    if (token && inAuthGroup) {
      router.replace('/home');
    }
  }, [token, segments, isLoading, router, loaded]);

  useEffect(() => {
    if (isLoading) {
      return;
    }
    void syncBackgroundLocationTracking(Boolean(token));
  }, [isLoading, token]);

  const handleNotificationResponse = useCallback(
    (response: Notifications.NotificationResponse) => {
      const data = response.notification.request.content.data as {
        kind?: string;
        fileUri?: string;
        mimeType?: string;
        threadId?: string;
        isMainSession?: boolean;
      };

      if (data?.kind === 'chat_reply') {
        const threadId = typeof data.threadId === 'string' ? data.threadId.trim() : '';
        if (data.isMainSession || !threadId) {
          router.push('/home/brain');
          return;
        }
        router.push(`/chat/${encodeURIComponent(threadId)}`);
        return;
      }

      if (data?.kind === 'proposed_events_ready') {
        router.push('/settings/proposed-events' as never);
        return;
      }

      if (data?.kind === 'daily_briefing_ready') {
        router.push({
          pathname: '/home',
          params: { expandBriefing: '1' },
        });
        return;
      }

      if (data?.kind === 'document_download') {
        const fileUri = typeof data.fileUri === 'string' ? data.fileUri : null;
        const mimeType = typeof data.mimeType === 'string' ? data.mimeType : '*/*';
        if (fileUri) {
          if (Platform.OS === 'android') {
            void IntentLauncher.startActivityAsync('android.intent.action.VIEW', {
              data: fileUri,
              type: mimeType,
              flags: 1,
            }).catch(async () => {
              try {
                await Linking.sendIntent('android.intent.action.VIEW_DOWNLOADS');
                return;
              } catch {
                // Fall through to home navigation.
              }
              router.push('/home');
            });
          } else {
            void Linking.openURL(fileUri).catch(() => {
              router.push('/home');
            });
          }
          return;
        }
      }

      router.push('/home');
    },
    [router],
  );

  useEffect(() => {
    let responseListener: Notifications.Subscription | undefined;
    let receivedListener: Notifications.Subscription | undefined;

    (async () => {
      await Notifications.setNotificationChannelAsync('default', {
        name: 'Default',
        importance: Notifications.AndroidImportance.MAX,
        vibrationPattern: [0, 250, 250, 250],
        lightColor: '#FF231F7C',
      });
    })();

    receivedListener = Notifications.addNotificationReceivedListener(() => {
      // Foreground notifications are handled by the global handler.
    });
    responseListener = Notifications.addNotificationResponseReceivedListener(
      handleNotificationResponse,
    );

    return () => {
      receivedListener?.remove();
      responseListener?.remove();
    };
  }, [handleNotificationResponse]);

  useEffect(() => {
    if (!lastNotificationResponse) return;
    const notificationId = lastNotificationResponse.notification.request.identifier;
    if (!notificationId) return;
    if (lastHandledNotificationIdRef.current === notificationId) return;
    lastHandledNotificationIdRef.current = notificationId;
    handleNotificationResponse(lastNotificationResponse);
  }, [handleNotificationResponse, lastNotificationResponse]);

  return (
    <ThemeProvider
      value={{
        ...DefaultTheme,
        colors: {
          ...DefaultTheme.colors,
          primary: '#e45c4d',
          background: '#f7f2ec',
          card: '#ffffff',
          text: '#1a1d22',
          border: '#e7ded4',
          notification: '#2f6f74',
        },
      }}
    >
      <StatusBar style="dark" backgroundColor="#f7f2ec" />
      <Stack initialRouteName="home">
        <Stack.Screen name="(auth)" options={{ headerShown: false }} />
        <Stack.Screen
          name="home"
          options={{
            headerShown: false,
            animation: 'fade',
          }}
        />
        <Stack.Screen
          name="settings/index"
          options={{
            headerShown: false,
          }}
        />
        <Stack.Screen
          name="settings/notifications"
          options={{
            headerShown: false,
          }}
        />
        <Stack.Screen
          name="settings/proposed-events/index"
          options={{
            headerShown: false,
          }}
        />
        <Stack.Screen
          name="settings/news-topics"
          options={{
            headerShown: false,
          }}
        />
        <Stack.Screen
          name="settings/about-me"
          options={{
            headerShown: false,
          }}
        />
        <Stack.Screen
          name="settings/about"
          options={{
            headerShown: false,
          }}
        />
        <Stack.Screen
          name="settings/image-understanding/index"
          options={{
            headerShown: false,
          }}
        />
        <Stack.Screen
          name="settings/glasses-capture/index"
          options={{
            headerShown: false,
          }}
        />
        <Stack.Screen
          name="places/new/index"
          options={{
            headerShown: true,
            headerTransparent: true,
            headerTitle: '',
            headerBackTitle: ' ',
            headerBackTitleStyle: { fontSize: 0 },
            headerBackButtonMenuEnabled: false,
            headerShadowVisible: false,
            headerTintColor: theme.colors.ink,
            headerStyle: { backgroundColor: 'transparent' },
          }}
        />
        <Stack.Screen
          name="places/[placeId]/index"
          options={{
            headerShown: true,
            headerTransparent: true,
            headerTitle: '',
            headerBackTitle: ' ',
            headerBackTitleStyle: { fontSize: 0 },
            headerBackButtonMenuEnabled: false,
            headerShadowVisible: false,
            headerTintColor: theme.colors.ink,
            headerStyle: { backgroundColor: 'transparent' },
          }}
        />
        <Stack.Screen
          name="contacts/[contactId]/index"
          options={{
            headerShown: true,
            headerTransparent: true,
            headerTitle: '',
            headerBackTitle: ' ',
            headerBackTitleStyle: { fontSize: 0 },
            headerBackButtonMenuEnabled: false,
            headerShadowVisible: false,
            headerTintColor: theme.colors.ink,
            headerStyle: { backgroundColor: 'transparent' },
          }}
        />
        <Stack.Screen
          name="contacts/[contactId]/relationships"
          options={{
            headerShown: true,
            headerTransparent: true,
            headerTitle: '',
            headerBackTitle: ' ',
            headerBackTitleStyle: { fontSize: 0 },
            headerBackButtonMenuEnabled: false,
            headerShadowVisible: false,
            headerTintColor: theme.colors.ink,
            headerStyle: { backgroundColor: 'transparent' },
          }}
        />
        <Stack.Screen
          name="todos/index"
          options={{
            headerShown: false,
          }}
        />
        <Stack.Screen
          name="todos/[todoId]/index"
          options={{
            headerShown: true,
            headerTransparent: true,
            headerTitle: 'Edit todo',
            headerBackTitle: ' ',
            headerBackTitleStyle: { fontSize: 0 },
            headerBackButtonMenuEnabled: false,
            headerShadowVisible: false,
            headerTintColor: theme.colors.ink,
            headerStyle: { backgroundColor: 'transparent' },
          }}
        />
        <Stack.Screen
          name="events/[eventId]"
          options={{
            headerShown: false,
          }}
        />
        <Stack.Screen
          name="chat/history/index"
          options={{
            headerShown: true,
            headerTitle: 'Thread history',
            headerBackTitle: ' ',
            headerBackTitleStyle: { fontSize: 0 },
            headerBackButtonMenuEnabled: false,
            headerShadowVisible: false,
            headerTintColor: theme.colors.ink,
            headerStyle: { backgroundColor: '#f7f2ec' },
          }}
        />
        <Stack.Screen
          name="chat/[threadId]/index"
          options={{
            headerShown: false,
          }}
        />
        <Stack.Screen
          name="contacts/proposals/[previewId]/index"
          options={{
            headerShown: false,
          }}
        />
        <Stack.Screen
          name="documents/[documentId]/index"
          options={{
            headerShown: true,
            headerTransparent: true,
            headerTitle: 'Document',
            headerBackTitle: ' ',
            headerBackTitleStyle: { fontSize: 0 },
            headerBackButtonMenuEnabled: false,
            headerShadowVisible: false,
            headerTintColor: theme.colors.ink,
            headerStyle: { backgroundColor: 'transparent' },
          }}
        />
        <Stack.Screen
          name="documents/new/index"
          options={{
            headerShown: true,
            headerTransparent: true,
            headerTitle: 'Upload document',
            headerBackTitle: ' ',
            headerBackTitleStyle: { fontSize: 0 },
            headerBackButtonMenuEnabled: false,
            headerShadowVisible: false,
            headerTintColor: theme.colors.ink,
            headerStyle: { backgroundColor: 'transparent' },
          }}
        />
        <Stack.Screen
          name="documents/[documentId]/edit/index"
          options={{
            headerShown: true,
            headerTransparent: true,
            headerTitle: 'Edit document',
            headerBackTitle: ' ',
            headerBackTitleStyle: { fontSize: 0 },
            headerBackButtonMenuEnabled: false,
            headerShadowVisible: false,
            headerTintColor: theme.colors.ink,
            headerStyle: { backgroundColor: 'transparent' },
          }}
        />
        <Stack.Screen
          name="documents/[documentId]/file/index"
          options={{
            headerShown: false,
          }}
        />
        <Stack.Screen
          name="news/article/[briefingItemId]/index"
          options={{
            headerShown: false,
          }}
        />
        <Stack.Screen
          name="modal"
          options={{
            presentation: 'modal',
            headerShown: false,
          }}
        />
      </Stack>
    </ThemeProvider>
  );
}
