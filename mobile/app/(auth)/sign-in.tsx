import { LinearGradient } from 'expo-linear-gradient';
import React from 'react';
import FontAwesome from '@expo/vector-icons/FontAwesome';
import { Pressable, StyleSheet, Text, View } from 'react-native';

import { useAuth } from '@/src/auth/AuthContext';
import { theme } from '@/src/theme';

export default function SignInScreen() {
  const { signInWithGoogle, isSigningIn } = useAuth();

  return (
    <LinearGradient colors={theme.gradients.sunrise} style={styles.container}>
      <View style={styles.glow} />
      <View style={styles.content}>
        <View style={styles.hero}>
          <Text style={styles.kicker}>Digital Brain</Text>
          <Text style={styles.title}>Your memory, orchestrated.</Text>
          <Text style={styles.subtitle}>
            Capture, search, and chat with the moments that matter. Sign in to
            sync your personal brain.
          </Text>
        </View>

        <View style={styles.card}>
          <Text style={styles.cardTitle}>Continue with Google</Text>
          <Text style={styles.cardCopy}>
            We use your Google account to securely personalize your memory vault.
          </Text>
          <Pressable
            onPress={signInWithGoogle}
            disabled={isSigningIn}
            style={({ pressed }) => [
              styles.button,
              pressed && styles.buttonPressed,
              isSigningIn && styles.buttonDisabled,
            ]}
          >
            <FontAwesome name="google" size={18} color="#fff" />
            <Text style={styles.buttonText}>
              {isSigningIn ? 'Signing in...' : 'Continue with Google'}
            </Text>
          </Pressable>
        </View>
      </View>
    </LinearGradient>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
  },
  glow: {
    position: 'absolute',
    top: -120,
    right: -80,
    width: 260,
    height: 260,
    borderRadius: 999,
    backgroundColor: theme.colors.paleTeal,
    opacity: 0.7,
  },
  content: {
    flex: 1,
    paddingHorizontal: 24,
    paddingTop: 80,
    paddingBottom: 40,
    justifyContent: 'space-between',
  },
  hero: {
    gap: 16,
  },
  kicker: {
    textTransform: 'uppercase',
    letterSpacing: 3,
    fontSize: 12,
    fontWeight: '600',
    color: theme.colors.teal,
  },
  title: {
    fontSize: 36,
    lineHeight: 40,
    fontWeight: '700',
    color: theme.colors.ink,
  },
  subtitle: {
    fontSize: 16,
    lineHeight: 24,
    color: theme.colors.mutedInk,
  },
  card: {
    backgroundColor: theme.colors.card,
    borderRadius: theme.radius.xl,
    padding: 24,
    shadowColor: theme.shadow.color,
    shadowOpacity: theme.shadow.opacity,
    shadowRadius: theme.shadow.radius,
    shadowOffset: theme.shadow.offset,
    elevation: 6,
  },
  cardTitle: {
    fontSize: 18,
    fontWeight: '600',
    color: theme.colors.ink,
  },
  cardCopy: {
    marginTop: 8,
    fontSize: 14,
    lineHeight: 20,
    color: theme.colors.mutedInk,
  },
  button: {
    marginTop: 20,
    flexDirection: 'row',
    alignItems: 'center',
    gap: 12,
    backgroundColor: theme.colors.ink,
    paddingVertical: 14,
    paddingHorizontal: 18,
    borderRadius: theme.radius.md,
    justifyContent: 'center',
  },
  buttonPressed: {
    transform: [{ scale: 0.98 }],
  },
  buttonDisabled: {
    opacity: 0.7,
  },
  buttonText: {
    color: '#fff',
    fontSize: 15,
    fontWeight: '600',
  },
});
