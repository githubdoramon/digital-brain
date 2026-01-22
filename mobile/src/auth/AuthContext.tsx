import { GoogleSignin, SignInSuccessResponse } from '@react-native-google-signin/google-signin';
import React, { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';
import * as SecureStore from 'expo-secure-store';
import { Alert } from 'react-native';

import { apiFetch, setAuthRefreshHandler, setAuthTokenProvider } from '@/src/api/client';

const TOKEN_KEY = 'digitalbrain.googleIdToken';
const EMAIL_KEY = 'digitalbrain.userEmail';

type AuthContextValue = {
  token: string | null;
  isLoading: boolean;
  isSigningIn: boolean;
  email: string | null;
  authFetch: (path: string, options?: RequestInit) => Promise<unknown>;
  refreshToken: () => Promise<string | null>;
  signInWithGoogle: () => Promise<void>;
  signOut: () => Promise<void>;
};

const AuthContext = createContext<AuthContextValue | undefined>(undefined);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [token, setToken] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isSigningIn, setIsSigningIn] = useState(false);
  const [email, setEmail] = useState<string | null>(null);
  const tokenRef = useRef<string | null>(null);
  useEffect(() => {
    GoogleSignin.configure({
      webClientId: process.env.EXPO_PUBLIC_GOOGLE_WEB_CLIENT_ID,
      iosClientId: process.env.EXPO_PUBLIC_GOOGLE_IOS_CLIENT_ID,
      offlineAccess: true,
      scopes: ['profile', 'email'],
    });
  }, []);

  const refreshToken = useCallback(async () => {
    try {
      const userInfo = await GoogleSignin.signInSilently();
      const tokens = await GoogleSignin.getTokens();
      const idToken = tokens.idToken;
      if (!idToken) {
        throw new Error('Google sign-in did not return an ID token.');
      }
      setToken(idToken);
      await SecureStore.setItemAsync(TOKEN_KEY, idToken);
      if (userInfo?.user?.email) {
        setEmail(userInfo.user.email);
        await SecureStore.setItemAsync(EMAIL_KEY, userInfo.user.email);
      }
      return idToken;
    } catch (error) {
      console.warn('[auth] token refresh failed', error);
      setToken(null);
      setEmail(null);
      await SecureStore.deleteItemAsync(TOKEN_KEY);
      await SecureStore.deleteItemAsync(EMAIL_KEY);
      try {
        await GoogleSignin.signOut();
      } catch (signOutError) {
        console.warn('[auth] sign out failed', signOutError);
      }
      return null;
    }
  }, []);

  useEffect(() => {
    tokenRef.current = token;
  }, [token]);

  useEffect(() => {
    setAuthTokenProvider(() => tokenRef.current);
    setAuthRefreshHandler(refreshToken);
  }, [refreshToken]);

  useEffect(() => {
    let mounted = true;
    (async () => {
      if (mounted) {
        const storedToken = await SecureStore.getItemAsync(TOKEN_KEY);
        const storedEmail = await SecureStore.getItemAsync(EMAIL_KEY);
        setToken(storedToken);
        setEmail(storedEmail);
        setIsLoading(false);
        if (storedToken) {
          await refreshToken();
        }
      }
    })();
    return () => {
      mounted = false;
    };
  }, [refreshToken]);

  const signInWithGoogle = useCallback(async () => {
    setIsSigningIn(true);
    try {
      await GoogleSignin.hasPlayServices({ showPlayServicesUpdateDialog: true });
      const response = await GoogleSignin.signIn();
      const tokens = await GoogleSignin.getTokens();
      const idToken = tokens.idToken;
      if (!idToken) {
        throw new Error('Google sign-in did not return an ID token.');
      }
      try {
        await apiFetch('/mobile/system/versions', { token: idToken, onAuthExpired: refreshToken });
      } catch (error) {
        const status = (error as Error & { status?: number; authExpired?: boolean }).status;
        const authExpired = (error as Error & { authExpired?: boolean }).authExpired;
        if (status === 403) {
          await GoogleSignin.revokeAccess();
          await GoogleSignin.signOut();
          await SecureStore.deleteItemAsync(TOKEN_KEY);
          setToken(null);
          Alert.alert('Access denied', 'Your account is not authorized to use this app.');
        } else if (authExpired) {
          await GoogleSignin.revokeAccess();
          await GoogleSignin.signOut();
          await SecureStore.deleteItemAsync(TOKEN_KEY);
          setToken(null);
          Alert.alert('Session expired', 'Please sign in again.');
        }
        throw error;
      }
      setToken(idToken);
      const email = (response as SignInSuccessResponse)!.data!.user!.email;
      setEmail(email);
      await SecureStore.setItemAsync(TOKEN_KEY, idToken);
      await SecureStore.setItemAsync(EMAIL_KEY, email);
    } catch (error) {
      console.error(error);
      throw error;
    } finally {
      setIsSigningIn(false);
    }
  }, []);

  const signOut = useCallback(async () => {
    setToken(null);
    setEmail(null);
    await GoogleSignin.signOut();
    await SecureStore.deleteItemAsync(TOKEN_KEY);
    await SecureStore.deleteItemAsync(EMAIL_KEY);
  }, []);

  const authFetch = useCallback(
    async (path: string, options: RequestInit = {}) =>
      apiFetch(path, { ...options, token, onAuthExpired: refreshToken }),
    [token, refreshToken],
  );

  const value = useMemo(
    () => ({ token, isLoading, email, isSigningIn, authFetch, refreshToken, signInWithGoogle, signOut }),
    [token, isLoading, email, isSigningIn, authFetch, refreshToken, signInWithGoogle, signOut],
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
