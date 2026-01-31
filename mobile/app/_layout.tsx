import FontAwesome from '@expo/vector-icons/FontAwesome';
import { DefaultTheme, ThemeProvider } from '@react-navigation/native';
import { useFonts } from 'expo-font';
import { Stack, useRouter, useSegments } from 'expo-router';
import * as SplashScreen from 'expo-splash-screen';
import { StatusBar } from 'expo-status-bar';
import { useEffect } from 'react';
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
  // Ensure that reloading on `/modal` keeps a back button present.
  initialRouteName: '(tabs)',
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

  useEffect(() => {
    if (loaded) {
      SplashScreen.hideAsync();
    }
  }, [loaded]);

  if (!loaded) {
    return null;
  }

  return (
    <AuthProvider>
      <SafeAreaProvider>
        <RootLayoutNav />
      </SafeAreaProvider>
    </AuthProvider>
  );
}

function RootLayoutNav() {
  const router = useRouter();
  const segments = useSegments();
  const { token, isLoading } = useAuth();

  useEffect(() => {
    if (isLoading) return;
    const inAuthGroup = segments[0] === '(auth)';
    if (!token && !inAuthGroup) {
      router.replace('/(auth)/sign-in');
    }
    if (token && inAuthGroup) {
      router.replace('/(tabs)');
    }
  }, [token, segments, isLoading, router]);

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
      router.push('/(tabs)');
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
      <Stack>
        <Stack.Screen name="(auth)" options={{ headerShown: false }} />
        <Stack.Screen name="(tabs)" options={{ headerShown: false }} />
        <Stack.Screen
          name="contacts/[contactId]"
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
          name="todos/new"
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
        <Stack.Screen name="modal" options={{ presentation: 'modal' }} />
      </Stack>
    </ThemeProvider>
  );
}
