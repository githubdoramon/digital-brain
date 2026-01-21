import { GoogleSignin } from '@react-native-google-signin/google-signin';
import React, { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';
import * as SecureStore from 'expo-secure-store';
import { Alert } from 'react-native';

import { apiFetch } from '@/src/api/client';

const TOKEN_KEY = 'digitalbrain.googleIdToken';

type AuthContextValue = {
  token: string | null;
  isLoading: boolean;
  isSigningIn: boolean;
  signInWithGoogle: () => Promise<void>;
  signOut: () => Promise<void>;
};

const AuthContext = createContext<AuthContextValue | undefined>(undefined);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [token, setToken] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isSigningIn, setIsSigningIn] = useState(false);

  useEffect(() => {
    GoogleSignin.configure({
      webClientId: process.env.EXPO_PUBLIC_GOOGLE_WEB_CLIENT_ID,
      iosClientId: process.env.EXPO_PUBLIC_GOOGLE_IOS_CLIENT_ID,
      offlineAccess: true,
      scopes: ['profile', 'email'],
    });
  }, []);

  useEffect(() => {
    let mounted = true;
    (async () => {
      const stored = await SecureStore.getItemAsync(TOKEN_KEY);
      if (mounted) {
        setToken(stored);
        setIsLoading(false);
      }
    })();
    return () => {
      mounted = false;
    };
  }, []);

  const signInWithGoogle = useCallback(async () => {
    setIsSigningIn(true);
    try {
      await GoogleSignin.hasPlayServices({ showPlayServicesUpdateDialog: true });
      await GoogleSignin.signIn();
      const tokens = await GoogleSignin.getTokens();
      const idToken = tokens.idToken;
      if (!idToken) {
        throw new Error('Google sign-in did not return an ID token.');
      }
      try {
        await apiFetch('/system/versions', { token: idToken });
      } catch (error) {
        const status = (error as Error & { status?: number }).status;
        if (status === 403) {
          Alert.alert('Access denied', 'Your account is not authorized to use this app.');
        }
        throw error;
      }
      setToken(idToken);
      await SecureStore.setItemAsync(TOKEN_KEY, idToken);
    } catch (error) {
      console.error(error);
      throw error;
    } finally {
      setIsSigningIn(false);
    }
  }, []);

  const signOut = useCallback(async () => {
    setToken(null);
    await GoogleSignin.signOut();
    await SecureStore.deleteItemAsync(TOKEN_KEY);
  }, []);

  const value = useMemo(
    () => ({ token, isLoading, isSigningIn, signInWithGoogle, signOut }),
    [token, isLoading, isSigningIn, signInWithGoogle, signOut],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) {
    throw new Error('useAuth must be used within AuthProvider');
  }
  return ctx;
}
