import Ionicons from '@expo/vector-icons/Ionicons';
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  ActivityIndicator,
  AppState,
  GestureResponderEvent,
  Platform,
  Pressable,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';
import { RecordingPresets, useAudioRecorder, useAudioRecorderState } from 'expo-audio';

import { ComposerMediaTray } from '@/components/chat/ComposerMediaTray';
import type { ComposerMediaAttachment } from '@/chat/mediaAttachments';
import {
  getLocalTranscriptionErrorMessage,
  type LocalTranscriptionStatus,
  transcribeAudioFile,
} from '@/chat/localTranscription';
import {
  ensureVoiceRecordingReady,
  getVoiceRecordingErrorMessage,
  requireRecordingUri,
  restoreVoiceAudioMode,
  VoiceRecordingError,
} from '@/chat/voiceRecording';
import {
  formatVoiceDuration,
  mergeTranscriptIntoDraft,
  type VoiceComposerPhase,
} from '@/chat/voiceState';
import { theme } from '@/theme';

const LOCK_THRESHOLD_PX = 72;
const LONG_PRESS_DELAY_MS = 260;

type Props = {
  attachments: ComposerMediaAttachment[];
  onRemoveAttachment: (attachmentId: string) => void;
  commandsEnabled: boolean;
  allowed: boolean;
  isSending: boolean;
  canSend: boolean;
  input: string;
  inputRef: React.RefObject<TextInput | null>;
  placeholder: string;
  minInputHeight: number;
  maxInputHeight: number;
  composerBottomOffset: number;
  composerPaddingBottom: number;
  onComposerHeightChange: (height: number) => void;
  onChangeInput: (value: string) => void;
  onSend: () => void;
  onAttachPhoto: () => void;
  onInputFocus: () => void;
  onInputBlur: () => void;
  onErrorMessage: (message: string) => void;
  attachDisabled: boolean;
};

export function ChatComposer({
  attachments,
  onRemoveAttachment,
  commandsEnabled,
  allowed,
  isSending,
  canSend,
  input,
  inputRef,
  placeholder,
  minInputHeight,
  maxInputHeight,
  composerBottomOffset,
  composerPaddingBottom,
  onComposerHeightChange,
  onChangeInput,
  onSend,
  onAttachPhoto,
  onInputFocus,
  onInputBlur,
  onErrorMessage,
  attachDisabled,
}: Props) {
  const recorder = useAudioRecorder(RecordingPresets.HIGH_QUALITY);
  const recorderState = useAudioRecorderState(recorder, 200);
  const [voicePhase, setVoicePhase] = useState<VoiceComposerPhase>('idle');
  const [voiceStatusText, setVoiceStatusText] = useState('');
  const [lockQueued, setLockQueued] = useState(false);
  const longPressHandledRef = useRef(false);
  const pointerStartYRef = useRef<number | null>(null);
  const shouldFinalizeAfterStartRef = useRef(false);
  const latestInputRef = useRef(input);
  const voicePhaseRef = useRef<VoiceComposerPhase>('idle');
  const cancelVoiceCaptureRef = useRef<() => Promise<void>>(async () => undefined);

  useEffect(() => {
    latestInputRef.current = input;
  }, [input]);

  useEffect(() => {
    voicePhaseRef.current = voicePhase;
  }, [voicePhase]);

  const canUseVoice = allowed && Platform.OS !== 'web' && !isSending;
  const inputIsEmpty = input.trim().length === 0;
  const shouldShowVoiceBanner = voicePhase === 'starting' || voicePhase === 'recording';
  const shouldShowVoicePanel = voicePhase === 'locked' || voicePhase === 'transcribing';

  const resetVoiceUi = useCallback(() => {
    longPressHandledRef.current = false;
    shouldFinalizeAfterStartRef.current = false;
    pointerStartYRef.current = null;
    setLockQueued(false);
    setVoiceStatusText('');
    setVoicePhase('idle');
  }, []);

  const stopRecorderSilently = useCallback(async () => {
    try {
      if (recorderState.isRecording) {
        await recorder.stop();
      }
    } catch {
      // Ignore cleanup failures during cancellation.
    }
  }, [recorder, recorderState.isRecording]);

  const cancelVoiceCapture = useCallback(async () => {
    await stopRecorderSilently();
    await restoreVoiceAudioMode().catch(() => undefined);
    resetVoiceUi();
  }, [resetVoiceUi, stopRecorderSilently]);

  useEffect(() => {
    cancelVoiceCaptureRef.current = cancelVoiceCapture;
  }, [cancelVoiceCapture]);

  const applyTranscriptionStatus = useCallback((status: LocalTranscriptionStatus) => {
    if (status.stage === 'downloading_model') {
      const progressText =
        typeof status.progress === 'number' ? ` ${Math.round(status.progress)}%` : '';
      setVoiceStatusText(`Downloading voice model${progressText}`);
      return;
    }

    if (status.stage === 'loading_model') {
      setVoiceStatusText('Loading voice model');
      return;
    }

    if (status.stage === 'transcribing') {
      const progressText =
        typeof status.progress === 'number' && Number.isFinite(status.progress)
          ? ` ${Math.round(status.progress)}%`
          : '';
      setVoiceStatusText(`Transcribing${progressText}`);
    }
  }, []);

  const finalizeVoiceCapture = useCallback(async () => {
    if (voicePhaseRef.current === 'transcribing' || voicePhaseRef.current === 'idle') {
      return;
    }

    setVoicePhase('transcribing');
    setVoiceStatusText('Finishing recording');

    try {
      await recorder.stop();
      await restoreVoiceAudioMode().catch(() => undefined);
      const recordingUri = requireRecordingUri(recorder.uri);
      const transcript = await transcribeAudioFile(recordingUri, applyTranscriptionStatus);
      onChangeInput(mergeTranscriptIntoDraft(latestInputRef.current, transcript));
      requestAnimationFrame(() => {
        inputRef.current?.focus();
      });
      resetVoiceUi();
    } catch (error) {
      await restoreVoiceAudioMode().catch(() => undefined);
      resetVoiceUi();
      onErrorMessage(
        error instanceof VoiceRecordingError
          ? getVoiceRecordingErrorMessage(error)
          : getLocalTranscriptionErrorMessage(error),
      );
    }
  }, [
    applyTranscriptionStatus,
    inputRef,
    onChangeInput,
    onErrorMessage,
    recorder,
    resetVoiceUi,
  ]);

  const beginVoiceCapture = useCallback(async () => {
    longPressHandledRef.current = true;
    shouldFinalizeAfterStartRef.current = false;
    setLockQueued(false);
    setVoiceStatusText('Starting microphone');
    setVoicePhase('starting');

    try {
      await ensureVoiceRecordingReady();
      await recorder.prepareToRecordAsync();
      recorder.record();
      setVoiceStatusText('Recording');
      setVoicePhase('recording');

      if (shouldFinalizeAfterStartRef.current) {
        shouldFinalizeAfterStartRef.current = false;
        await finalizeVoiceCapture();
      }
    } catch (error) {
      await restoreVoiceAudioMode().catch(() => undefined);
      resetVoiceUi();
      onErrorMessage(getVoiceRecordingErrorMessage(error));
    }
  }, [finalizeVoiceCapture, onErrorMessage, recorder, resetVoiceUi]);

  useEffect(() => {
    const subscription = AppState.addEventListener('change', (nextState) => {
      if (nextState === 'active') {
        return;
      }

      if (voicePhaseRef.current === 'starting' || voicePhaseRef.current === 'recording' || voicePhaseRef.current === 'locked') {
        void cancelVoiceCapture();
        onErrorMessage('Voice recording stopped when the app left the foreground.');
      }
    });

    return () => {
      subscription.remove();
    };
  }, [cancelVoiceCapture, onErrorMessage]);

  useEffect(() => {
    return () => {
      void cancelVoiceCaptureRef.current();
    };
  }, []);

  const handleSendPress = useCallback(() => {
    if (longPressHandledRef.current) {
      longPressHandledRef.current = false;
      return;
    }

    if (canSend) {
      onSend();
      return;
    }

    inputRef.current?.focus();
  }, [canSend, inputRef, onSend]);

  const handlePressIn = useCallback((event: GestureResponderEvent) => {
    pointerStartYRef.current = event.nativeEvent.pageY;
  }, []);

  const handleLongPress = useCallback(() => {
    if (!canUseVoice || !inputIsEmpty) {
      return;
    }

    void beginVoiceCapture();
  }, [beginVoiceCapture, canUseVoice, inputIsEmpty]);

  const handleTouchMove = useCallback((event: GestureResponderEvent) => {
    if (voicePhaseRef.current !== 'recording' || lockQueued || pointerStartYRef.current == null) {
      return;
    }

    if (pointerStartYRef.current - event.nativeEvent.pageY >= LOCK_THRESHOLD_PX) {
      setLockQueued(true);
    }
  }, [lockQueued]);

  const handlePressOut = useCallback(() => {
    pointerStartYRef.current = null;

    if (voicePhaseRef.current === 'starting') {
      shouldFinalizeAfterStartRef.current = true;
      return;
    }

    if (voicePhaseRef.current !== 'recording') {
      return;
    }

    if (lockQueued) {
      setLockQueued(false);
      setVoicePhase('locked');
      setVoiceStatusText('Recording locked');
      return;
    }

    void finalizeVoiceCapture();
  }, [finalizeVoiceCapture, lockQueued]);

  const voiceBannerSubtitle = useMemo(() => {
    if (voicePhase === 'starting') {
      return 'Getting the microphone ready...';
    }

    if (lockQueued) {
      return 'Release to keep recording hands-free.';
    }

    return 'Release to transcribe, or swipe up to lock.';
  }, [lockQueued, voicePhase]);

  const voicePanelSubtitle = useMemo(() => {
    if (voicePhase === 'locked') {
      return 'Tap stop to transcribe, or cancel to discard.';
    }

    return voiceStatusText || 'Processing your speech on this device.';
  }, [voicePhase, voiceStatusText]);

  const buttonIconName = useMemo(() => {
    if (isSending) {
      return null;
    }

    if (voicePhase === 'starting' || voicePhase === 'recording') {
      return 'mic';
    }

    if (!inputIsEmpty || attachments.length > 0) {
      return 'send';
    }

    return 'mic';
  }, [attachments.length, inputIsEmpty, isSending, voicePhase]);

  return (
    <View
      onLayout={(event) => {
        onComposerHeightChange(event.nativeEvent.layout.height);
      }}
      style={[
        styles.composer,
        {
          bottom: composerBottomOffset,
          paddingBottom: composerPaddingBottom,
        },
      ]}
    >
      <ComposerMediaTray attachments={attachments} onRemoveAttachment={onRemoveAttachment} />
      {!commandsEnabled ? (
        <Text style={styles.composerNotice}>Commands are disabled in historical threads.</Text>
      ) : null}

      {shouldShowVoiceBanner ? (
        <View style={styles.voiceBanner}>
          <View style={styles.voiceBannerDot} />
          <View style={styles.voiceBannerTextWrap}>
            <Text style={styles.voiceBannerTitle}>
              {voicePhase === 'starting' ? 'Preparing voice capture' : formatVoiceDuration(recorderState.durationMillis)}
            </Text>
            <Text style={styles.voiceBannerSubtitle}>{voiceBannerSubtitle}</Text>
          </View>
        </View>
      ) : null}

      {shouldShowVoicePanel ? (
        <View style={styles.voicePanel}>
          <View style={styles.voicePanelContent}>
            {voicePhase === 'transcribing' ? (
              <ActivityIndicator size="small" color={theme.colors.accentDeep} />
            ) : (
              <View style={styles.voiceBannerDot} />
            )}
            <View style={styles.voicePanelTextWrap}>
              <Text style={styles.voicePanelTitle}>
                {voicePhase === 'locked'
                  ? formatVoiceDuration(recorderState.durationMillis)
                  : voiceStatusText || 'Transcribing'}
              </Text>
              <Text style={styles.voicePanelSubtitle}>{voicePanelSubtitle}</Text>
            </View>
          </View>
          {voicePhase === 'locked' ? (
            <View style={styles.voicePanelActions}>
              <Pressable
                accessibilityRole="button"
                accessibilityLabel="Discard recording"
                onPress={() => {
                  void cancelVoiceCapture();
                }}
                style={({ pressed }) => [styles.voiceActionButton, pressed && styles.voiceActionButtonPressed]}
              >
                <Ionicons name="close" size={16} color={theme.colors.ink} />
              </Pressable>
              <Pressable
                accessibilityRole="button"
                accessibilityLabel="Stop recording and transcribe"
                onPress={() => {
                  void finalizeVoiceCapture();
                }}
                style={({ pressed }) => [styles.voiceActionButtonPrimary, pressed && styles.voiceActionButtonPressed]}
              >
                <Ionicons name="stop" size={16} color="#fff" />
              </Pressable>
            </View>
          ) : null}
        </View>
      ) : (
        <View style={styles.inputWrap}>
          <Pressable
            accessibilityRole="button"
            accessibilityLabel="Attach photo"
            onPress={onAttachPhoto}
            disabled={attachDisabled || voicePhase !== 'idle'}
            style={({ pressed }) => [
              styles.attachButton,
              pressed && styles.attachButtonPressed,
              (attachDisabled || voicePhase !== 'idle') && styles.attachButtonDisabled,
            ]}
          >
            <Ionicons name="image-outline" size={18} color={theme.colors.ink} />
          </Pressable>
          <TextInput
            ref={inputRef}
            value={input}
            editable={allowed && voicePhase === 'idle'}
            style={[
              styles.input,
              {
                minHeight: minInputHeight,
                maxHeight: maxInputHeight,
                width: '100%',
                paddingRight: 104,
              },
              !allowed && {
                backgroundColor: '#eee',
              },
            ]}
            onChangeText={onChangeInput}
            placeholder={placeholder}
            placeholderTextColor="#A7AFB7"
            multiline
            onFocus={onInputFocus}
            onBlur={onInputBlur}
            scrollEnabled
          />
          <Pressable
            accessibilityRole="button"
            accessibilityLabel={
              !inputIsEmpty || attachments.length > 0
                ? 'Send message'
                : 'Hold to record a voice message'
            }
            onPress={handleSendPress}
            onPressIn={handlePressIn}
            onLongPress={handleLongPress}
            onPressOut={handlePressOut}
            delayLongPress={LONG_PRESS_DELAY_MS}
            disabled={!allowed || isSending}
            {...({ onPressMove: handleTouchMove } as Record<string, unknown>)}
            style={({ pressed }) => [
              styles.inlineSendButton,
              (voicePhase === 'starting' || voicePhase === 'recording') && styles.inlineSendButtonRecording,
              pressed && styles.inlineSendButtonPressed,
              !allowed && styles.inlineSendButtonDisabled,
            ]}
          >
            {isSending ? (
              <ActivityIndicator size="small" color="#fff" />
            ) : buttonIconName ? (
              <Ionicons name={buttonIconName} size={16} color="#fff" />
            ) : null}
          </Pressable>
        </View>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  composer: {
    position: 'absolute',
    left: 0,
    right: 0,
    bottom: 0,
    zIndex: 2,
    paddingHorizontal: 16,
    paddingTop: 14,
    backgroundColor: 'transparent',
    gap: 10,
    alignItems: 'stretch',
  },
  inputWrap: {
    flex: 1,
    position: 'relative',
  },
  composerNotice: {
    fontSize: 12,
    lineHeight: 16,
    color: theme.colors.mutedInk,
    paddingHorizontal: 8,
  },
  attachButton: {
    position: 'absolute',
    right: 50,
    top: '50%',
    zIndex: 1,
    width: 34,
    height: 34,
    borderRadius: 17,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: '#f2ece5',
    transform: [{ translateY: -17 }],
  },
  attachButtonPressed: {
    opacity: 0.8,
  },
  attachButtonDisabled: {
    opacity: 0.45,
  },
  input: {
    fontSize: 16,
    lineHeight: 20,
    paddingHorizontal: 14,
    paddingVertical: 12,
    borderRadius: theme.radius.xl,
    borderWidth: 1,
    borderColor: theme.colors.line,
    backgroundColor: '#fff',
    color: theme.colors.ink,
    textAlignVertical: 'center',
    shadowColor: theme.shadow.color,
    shadowOpacity: 0.12,
    shadowRadius: 12,
    shadowOffset: { width: 0, height: 8 },
    elevation: 3,
  },
  inlineSendButton: {
    position: 'absolute',
    right: 6,
    top: '50%',
    width: 36,
    height: 36,
    borderRadius: 18,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: theme.colors.accent,
    shadowColor: theme.shadow.color,
    shadowOpacity: 0.24,
    shadowRadius: 10,
    shadowOffset: { width: 0, height: 6 },
    elevation: 5,
    transform: [{ translateY: -18 }],
  },
  inlineSendButtonRecording: {
    backgroundColor: theme.colors.accentDeep,
  },
  inlineSendButtonPressed: {
    opacity: 0.9,
  },
  inlineSendButtonDisabled: {
    opacity: 0.75,
  },
  voiceBanner: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
    paddingHorizontal: 14,
    paddingVertical: 12,
    borderRadius: theme.radius.lg,
    backgroundColor: 'rgba(255,255,255,0.94)',
    borderWidth: 1,
    borderColor: '#f0cbc6',
    shadowColor: theme.shadow.color,
    shadowOpacity: 0.08,
    shadowRadius: 10,
    shadowOffset: { width: 0, height: 6 },
    elevation: 2,
  },
  voiceBannerDot: {
    width: 10,
    height: 10,
    borderRadius: 5,
    backgroundColor: theme.colors.accentDeep,
  },
  voiceBannerTextWrap: {
    flex: 1,
    gap: 2,
  },
  voiceBannerTitle: {
    fontSize: 14,
    lineHeight: 18,
    fontWeight: '700',
    color: theme.colors.ink,
  },
  voiceBannerSubtitle: {
    fontSize: 12,
    lineHeight: 16,
    color: theme.colors.mutedInk,
  },
  voicePanel: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 12,
    paddingHorizontal: 14,
    paddingVertical: 12,
    borderRadius: theme.radius.xl,
    borderWidth: 1,
    borderColor: theme.colors.line,
    backgroundColor: '#fff',
    shadowColor: theme.shadow.color,
    shadowOpacity: 0.12,
    shadowRadius: 12,
    shadowOffset: { width: 0, height: 8 },
    elevation: 3,
  },
  voicePanelContent: {
    flex: 1,
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
  },
  voicePanelTextWrap: {
    flex: 1,
    gap: 2,
  },
  voicePanelTitle: {
    fontSize: 14,
    lineHeight: 18,
    fontWeight: '700',
    color: theme.colors.ink,
  },
  voicePanelSubtitle: {
    fontSize: 12,
    lineHeight: 16,
    color: theme.colors.mutedInk,
  },
  voicePanelActions: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
  },
  voiceActionButton: {
    width: 36,
    height: 36,
    borderRadius: 18,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: '#f2ece5',
  },
  voiceActionButtonPrimary: {
    width: 36,
    height: 36,
    borderRadius: 18,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: theme.colors.accent,
  },
  voiceActionButtonPressed: {
    opacity: 0.82,
  },
});
