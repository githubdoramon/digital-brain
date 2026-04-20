import { GoogleSignin } from '@react-native-google-signin/google-signin';

let isConfigured = false;

export function configureGoogleSignIn(): void {
  if (isConfigured) {
    return;
  }

  GoogleSignin.configure({
    webClientId: process.env.EXPO_PUBLIC_GOOGLE_WEB_CLIENT_ID,
    iosClientId: process.env.EXPO_PUBLIC_GOOGLE_IOS_CLIENT_ID,
    offlineAccess: true,
    scopes: ['profile', 'email'],
  });
  isConfigured = true;
}
