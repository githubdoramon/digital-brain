import FontAwesome from '@expo/vector-icons/FontAwesome';
import { DefaultTheme, ThemeProvider } from '@react-navigation/native';
import { useFonts } from 'expo-font';
import { Stack, useRouter, useSegments } from 'expo-router';
import * as SplashScreen from 'expo-splash-screen';
import { StatusBar } from 'expo-status-bar';
import { useEffect } from 'react';
import { Platform } from 'react-native';
import 'react-native-reanimated';
import { SafeAreaProvider } from 'react-native-safe-area-context';
import * as Notifications from 'expo-notifications';

import { AuthProvider, useAuth } from '@/auth/AuthContext';
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
    responseListener = Notifications.addNotificationResponseReceivedListener(() => {
      router.push('/home');
    });

    return () => {
      receivedListener?.remove();
      responseListener?.remove();
    };
  }, [router]);

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
          headerShown: true,
          headerTransparent: Platform.OS !== 'android',
          headerTitle: '',
          headerBackTitle: ' ',
          headerBackTitleStyle: { fontSize: 0 },
          headerBackButtonMenuEnabled: false,
          headerShadowVisible: false,
          headerTintColor: theme.colors.ink,
          headerStyle: {
            backgroundColor: Platform.OS === 'android' ? theme.colors.background : 'transparent',
          },
        }}
      />
      <Stack.Screen
        name="settings/news-topics"
        options={{
          headerShown: true,
          headerTransparent: Platform.OS !== 'android',
          headerTitle: '',
          headerBackTitle: ' ',
          headerBackTitleStyle: { fontSize: 0 },
          headerBackButtonMenuEnabled: false,
          headerShadowVisible: false,
          headerTintColor: theme.colors.ink,
          headerStyle: {
            backgroundColor: Platform.OS === 'android' ? theme.colors.background : 'transparent',
          },
        }}
      />
      <Stack.Screen
        name="settings/about-me"
        options={{
          headerShown: true,
          headerTransparent: Platform.OS !== 'android',
          headerTitle: '',
          headerBackTitle: ' ',
          headerBackTitleStyle: { fontSize: 0 },
          headerBackButtonMenuEnabled: false,
          headerShadowVisible: false,
          headerTintColor: theme.colors.ink,
          headerStyle: {
            backgroundColor: Platform.OS === 'android' ? theme.colors.background : 'transparent',
          },
        }}
      />
        <Stack.Screen
          name="contacts/[contactId]/index"
          options={{
            headerShown: true,
            headerTransparent: true,
            headerTitle: '',
            headerBackTitle: ' ',
            headerBackTitleStyle: { fontSize: 1 },
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
            headerShown: true,
            headerTransparent: true,
            headerTitle: 'New todo',
            headerBackTitle: ' ',
            headerBackTitleStyle: { fontSize: 0 },
            headerBackButtonMenuEnabled: false,
            headerShadowVisible: false,
            headerTintColor: theme.colors.ink,
            headerStyle: { backgroundColor: 'transparent' },
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
        <Stack.Screen name="modal" options={{ presentation: 'modal' }} />
      </Stack>
    </ThemeProvider>
  );
}
