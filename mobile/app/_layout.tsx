import FontAwesome from '@expo/vector-icons/FontAwesome';
import { DefaultTheme, ThemeProvider } from '@react-navigation/native';
import { useFonts } from 'expo-font';
import * as IntentLauncher from 'expo-intent-launcher';
import { Stack, useRouter, useSegments } from 'expo-router';
import * as SplashScreen from 'expo-splash-screen';
import { StatusBar } from 'expo-status-bar';
import { useCallback, useEffect, useRef } from 'react';
import 'react-native-reanimated';
import { Linking, Platform } from 'react-native';
import { SafeAreaProvider } from 'react-native-safe-area-context';
import * as Notifications from 'expo-notifications';

import { AuthProvider, useAuth } from '@/auth/AuthContext';
import { syncBackgroundLocationTracking } from '@/location/backgroundLocation';
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
  }, []);

  if (!loaded) {
    return null;
  }

  return (
    <AuthProvider>
      <SafeAreaProvider>
        <RootLayoutNav loaded={loaded} />
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
      };

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
    responseListener = Notifications.addNotificationResponseReceivedListener(handleNotificationResponse);

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
      <Stack initialRouteName='home'>
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
        name="settings/places"
        options={{
          headerShown: false,
        }}
      />
      <Stack.Screen
        name="settings/events"
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
