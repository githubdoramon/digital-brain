import { GoogleSignin, SignInSuccessResponse } from '@react-native-google-signin/google-signin';
import React, { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';
import * as SecureStore from 'expo-secure-store';
import { Alert } from 'react-native';

import { apiFetch, setAuthRefreshHandler, setAuthTokenProvider } from '@/api/client';

const TOKEN_KEY = 'digitalbrain.googleIdToken';
const EMAIL_KEY = 'digitalbrain.userEmail';
const NAME_KEY = 'digitalbrain.userName';
const PHOTO_KEY = 'digitalbrain.userPhoto';

type AuthContextValue = {
  token: string | null;
  isLoading: boolean;
  isSigningIn: boolean;
  email: string | null;
  name: string | null;
  photo: string | null;
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
  const [name, setName] = useState<string | null>(null);
  const [photo, setPhoto] = useState<string | null>(null);
  const tokenRef = useRef<string | null>(null);
  const refreshPromiseRef = useRef<Promise<string | null> | null>(null);
  useEffect(() => {
    GoogleSignin.configure({
      webClientId: process.env.EXPO_PUBLIC_GOOGLE_WEB_CLIENT_ID,
      iosClientId: process.env.EXPO_PUBLIC_GOOGLE_IOS_CLIENT_ID,
      offlineAccess: true,
      scopes: ['profile', 'email'],
    });
  }, []);

  const refreshToken = useCallback(() => {
    if (refreshPromiseRef.current) {
      return refreshPromiseRef.current;
    }

    refreshPromiseRef.current = (async () => {
      try {
        console.info('[auth] refreshToken started');
        const userInfo = await GoogleSignin.signInSilently();
        const tokens = await GoogleSignin.getTokens();
        const idToken = tokens.idToken;
        if (!idToken) {
          throw new Error('Google sign-in did not return an ID token.');
        }
        console.info('[auth] refreshToken success', {
          hasEmail: Boolean(userInfo?.user?.email),
        });
        setToken(idToken);
        await SecureStore.setItemAsync(TOKEN_KEY, idToken);
        console.log('[auth] userInfo', userInfo);
        const userEmail = userInfo?.user?.email ?? null;
        const userName = userInfo?.user?.name ?? null;
        const userPhoto = userInfo?.user?.photo ?? null;
        if (userEmail) {
          setEmail(userEmail);
          await SecureStore.setItemAsync(EMAIL_KEY, userEmail);
        }
        if (userName) {
          setName(userName);
          await SecureStore.setItemAsync(NAME_KEY, userName);
        }
        if (userPhoto) {
          setPhoto(userPhoto);
          await SecureStore.setItemAsync(PHOTO_KEY, userPhoto);
        }
        return idToken;
      } catch (error) {
        const errorCode = (error as { code?: string } | undefined)?.code;
        console.warn('[auth] token refresh failed', error);
        if (errorCode === 'ASYNC_OP_IN_PROGRESS') {
          return tokenRef.current;
        }
        setToken(null);
        setEmail(null);
        setName(null);
        setPhoto(null);
        await SecureStore.deleteItemAsync(TOKEN_KEY);
        await SecureStore.deleteItemAsync(EMAIL_KEY);
        await SecureStore.deleteItemAsync(NAME_KEY);
        await SecureStore.deleteItemAsync(PHOTO_KEY);
        try {
          await GoogleSignin.signOut();
        } catch (signOutError) {
          console.warn('[auth] sign out failed', signOutError);
        }
        return null;
      } finally {
        refreshPromiseRef.current = null;
      }
    })();

    return refreshPromiseRef.current;
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
        const storedName = await SecureStore.getItemAsync(NAME_KEY);
        const storedPhoto = await SecureStore.getItemAsync(PHOTO_KEY);
        console.info('[auth] restore session', {
          hasStoredToken: Boolean(storedToken),
          hasStoredEmail: Boolean(storedEmail),
          storedPhoto
        });
        setToken(storedToken);
        setEmail(storedEmail);
        setName(storedName);
        setPhoto(storedPhoto);
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
      console.info('[auth] signInWithGoogle started');
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
        console.warn('[auth] sign-in validation failed', {
          status,
          authExpired,
        });
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
      console.info('[auth] signInWithGoogle success');
      setToken(idToken);
      const email = (response as SignInSuccessResponse)!.data!.user!.email;
      const userName = (response as SignInSuccessResponse)!.data!.user!.name ?? null;
      const userPhoto = (response as SignInSuccessResponse)!.data!.user!.photo ?? null;
      setEmail(email);
      setName(userName);
      setPhoto(userPhoto);
      await SecureStore.setItemAsync(TOKEN_KEY, idToken);
      await SecureStore.setItemAsync(EMAIL_KEY, email);
      if (userName) {
        await SecureStore.setItemAsync(NAME_KEY, userName);
      }
      if (userPhoto) {
        await SecureStore.setItemAsync(PHOTO_KEY, userPhoto);
      }
    } catch (error) {
      console.error(error);
      throw error;
    } finally {
      setIsSigningIn(false);
    }
  }, [refreshToken]);

  const signOut = useCallback(async () => {
    console.info('[auth] signOut');
    setToken(null);
    setEmail(null);
    setName(null);
    setPhoto(null);
    await GoogleSignin.signOut();
    await SecureStore.deleteItemAsync(TOKEN_KEY);
    await SecureStore.deleteItemAsync(EMAIL_KEY);
    await SecureStore.deleteItemAsync(NAME_KEY);
    await SecureStore.deleteItemAsync(PHOTO_KEY);
  }, []);

  const authFetch = useCallback(
    async (path: string, options: RequestInit = {}) =>
      apiFetch(path, { ...options, token, onAuthExpired: refreshToken }),
    [token, refreshToken],
  );

  const value = useMemo(
    () => ({
      token,
      isLoading,
      email,
      name,
      photo,
      isSigningIn,
      authFetch,
      refreshToken,
      signInWithGoogle,
      signOut,
    }),
    [token, isLoading, email, name, photo, isSigningIn, authFetch, refreshToken, signInWithGoogle, signOut],
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
