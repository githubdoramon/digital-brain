/**
 * Local device-command interception is deliberately empty in v1. Keeping the
 * boundary typed means a future safe, offline command can be added without
 * teaching the wake-word/transcription code about individual commands.
 */
export type DeviceCommandContext = {
  commandId: string;
  transcript: string;
  timezone?: string;
  location?: {
    lat: number;
    lon: number;
    accuracy_m?: number;
    captured_at: string;
    source: string;
  };
};

export type DeviceCommandResult = {
  outcome: 'control_completed' | 'shortcut_completed' | 'agent_response' | 'error';
  [key: string]: unknown;
};

export type DeviceCommandInterceptor = {
  id: string;
  matches: (transcript: string, context: DeviceCommandContext) => boolean;
  execute: (context: DeviceCommandContext) => Promise<DeviceCommandResult> | DeviceCommandResult;
};

const interceptors: DeviceCommandInterceptor[] = [];

export function registerDeviceCommandInterceptor(
  interceptor: DeviceCommandInterceptor,
): () => void {
  if (!interceptor.id.trim()) throw new Error('A device-command interceptor needs an id.');
  if (interceptors.some((candidate) => candidate.id === interceptor.id)) {
    throw new Error(`Device-command interceptor already registered: ${interceptor.id}`);
  }
  interceptors.push(interceptor);
  return () => {
    const index = interceptors.indexOf(interceptor);
    if (index >= 0) interceptors.splice(index, 1);
  };
}

export function getDeviceCommandInterceptors(): readonly DeviceCommandInterceptor[] {
  return interceptors;
}

export async function interceptDeviceCommand(
  context: DeviceCommandContext,
): Promise<DeviceCommandResult | null> {
  // Registration order is the precedence contract. No interceptor is
  // registered by the app in v1, so spoken `slash new` and every other transcript
  // continue through the backend command endpoint.
  const interceptor = interceptors.find((candidate) =>
    candidate.matches(context.transcript, context),
  );
  if (!interceptor) return null;
  return interceptor.execute(context);
}

/** Test-only reset that avoids leaking registrations across isolated tests. */
export function clearDeviceCommandInterceptorsForTests(): void {
  interceptors.splice(0, interceptors.length);
}
