import { Stack } from 'expo-router';

export default function ContactsLayout() {
  return (
    <Stack>
      <Stack.Screen name="index" options={{ headerShown: false }} />
      <Stack.Screen name="[contactId]" options={{ headerShown: false }} />
      <Stack.Screen name="[contactId]/relationships" options={{ headerShown: false }} />
    </Stack>
  );
}
