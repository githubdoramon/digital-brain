import { GoogleSignin } from '@react-native-google-signin/google-signin';
import * as SecureStore from 'expo-secure-store';

import { AUTH_EMAIL_KEY, AUTH_NAME_KEY, AUTH_PHOTO_KEY, AUTH_TOKEN_KEY } from '@/auth/storageKeys';
import { configureGoogleSignIn } from '@/auth/googleSignin';
import { getTokenDiagnostics } from '@/auth/tokenDiagnostics';
import { reportLocationDebugEvent } from '@/location/debugState';

async function clearStoredAuth(): Promise<void> {
  await Promise.all([
    SecureStore.deleteItemAsync(AUTH_TOKEN_KEY),
    SecureStore.deleteItemAsync(AUTH_EMAIL_KEY),
    SecureStore.deleteItemAsync(AUTH_NAME_KEY),
    SecureStore.deleteItemAsync(AUTH_PHOTO_KEY),
  ]);
}

export async function refreshStoredGoogleIdToken(): Promise<string | null> {
  try {
    configureGoogleSignIn();
    const userInfo = await GoogleSignin.signInSilently();
    const tokens = await GoogleSignin.getTokens();
    const idToken = tokens.idToken;
    if (!idToken) {
      throw new Error('Google sign-in did not return an ID token.');
    }

    await SecureStore.setItemAsync(AUTH_TOKEN_KEY, idToken);

    if (userInfo.type === 'success') {
      await SecureStore.setItemAsync(AUTH_EMAIL_KEY, userInfo.data.user.email);
      if (userInfo.data.user.name) {
        await SecureStore.setItemAsync(AUTH_NAME_KEY, userInfo.data.user.name);
      }
      if (userInfo.data.user.photo) {
        await SecureStore.setItemAsync(AUTH_PHOTO_KEY, userInfo.data.user.photo);
      }
    }

    reportLocationDebugEvent('background_auth_refresh_success', {
      payload: getTokenDiagnostics(idToken),
    });
    return idToken;
  } catch (error) {
    const errorWithMeta = error as Error & { status?: number; authExpired?: boolean };
    reportLocationDebugEvent('background_auth_refresh_error', {
      message: errorWithMeta?.message || 'Background auth refresh failed',
      error,
      payload: {
        status: errorWithMeta?.status,
        auth_expired: errorWithMeta?.authExpired,
        ...getTokenDiagnostics(null),
      },
    });
    await clearStoredAuth();
    return null;
  }
}

export async function getStoredGoogleIdToken(): Promise<string | null> {
  return SecureStore.getItemAsync(AUTH_TOKEN_KEY);
}

export async function getStoredGoogleIdTokenDiagnostics(): Promise<Record<string, unknown>> {
  return getTokenDiagnostics(await getStoredGoogleIdToken());
}
