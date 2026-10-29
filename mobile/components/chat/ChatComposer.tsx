import Ionicons from '@expo/vector-icons/Ionicons';
import * as FileSystem from 'expo-file-system/legacy';
import React, { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
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
import { useAudioRecorder, type RecordingConfig } from '@siteed/audio-studio';

import { ComposerMediaTray } from '@/components/chat/ComposerMediaTray';
import type { ComposerMediaAttachment } from '@/chat/mediaAttachments';
import {
  getTranscriptionErrorMessage,
  MAX_DICTATION_SECONDS,
  TranscriptionError,
  type TranscriptionStatus,
  transcribeAudioFile,
} from '@/chat/remoteTranscription';

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

const LOCK_THRESHOLD_PX = 56;
const LONG_PRESS_DELAY_MS = 260;
const SEND_BUTTON_SIZE = 40;
const SEND_BUTTON_EXPANDED_SIZE = 56;
const VOICE_RECORDING_OPTIONS: RecordingConfig = {
  sampleRate: 16000,
  channels: 1,
  encoding: 'pcm_16bit',
  output: {
    primary: {
      enabled: true,
      format: 'wav',
    },
    compressed: {
      enabled: false,
    },
  },
  ios: {
    audioSession: {
      category: 'PlayAndRecord',
      mode: 'Default',
      categoryOptions: ['DefaultToSpeaker'],
    },
  },
};

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
  onAttachPdf: () => void;
  onInputFocus: () => void;
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
  onAttachPdf,
  onInputFocus,
  onErrorMessage,
  attachDisabled,
}: Props) {
  const recorder = useAudioRecorder();
  const mountedRef = useRef(true);
  const allowedRef = useRef(allowed);
  allowedRef.current = allowed;
  const transcriptionControllerRef = useRef<AbortController | null>(null);
  const voiceGenerationRef = useRef(0);

  const [voicePhase, setVoicePhase] = useState<VoiceComposerPhase>('idle');
  const [voiceStatusText, setVoiceStatusText] = useState('');
  const [lockQueued, setLockQueued] = useState(false);
  const [isSendButtonPressed, setIsSendButtonPressed] = useState(false);
  const [dragLiftDistance, setDragLiftDistance] = useState(0);
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
  const sendButtonDisabled = !allowed || isSending || voicePhase === 'transcribing';
  const sendButtonExpanded =
    isSendButtonPressed || voicePhase === 'starting' || voicePhase === 'recording';
  const shouldShowSlideHint = sendButtonExpanded && voicePhase !== 'locked';

  const resetVoiceUi = useCallback(() => {
    longPressHandledRef.current = false;
    shouldFinalizeAfterStartRef.current = false;
    pointerStartYRef.current = null;
    setLockQueued(false);
    setIsSendButtonPressed(false);
    setDragLiftDistance(0);
    setVoiceStatusText('');
    voicePhaseRef.current = 'idle';
    setVoicePhase('idle');
  }, []);

  const stopRecorderSilently = useCallback(async () => {
    try {
      if (recorder.isRecording || recorder.isPaused) {
        const stopped = await recorder.stopRecording();
        if (stopped?.fileUri)
          await FileSystem.deleteAsync(stopped.fileUri, { idempotent: true }).catch(
            () => undefined,
          );
      }
    } catch {
      /* Best effort; keep the existing recovery path. */
    }
  }, [recorder]);

  const cancelVoiceCapture = useCallback(async () => {
    voiceGenerationRef.current++;
    transcriptionControllerRef.current?.abort();
    transcriptionControllerRef.current = null;
    await stopRecorderSilently();
    await restoreVoiceAudioMode().catch(() => undefined);
    if (mountedRef.current) resetVoiceUi();
  }, [resetVoiceUi, stopRecorderSilently]);

  useEffect(() => {
    cancelVoiceCaptureRef.current = cancelVoiceCapture;
  }, [cancelVoiceCapture]);

  const applyTranscriptionStatus = useCallback((status: TranscriptionStatus) => {
    setVoiceStatusText(status.stage === 'uploading' ? 'Sending your recording' : 'Transcribing');
  }, []);

  const finalizeVoiceCapture = useCallback(async () => {
    if (voicePhaseRef.current === 'transcribing' || voicePhaseRef.current === 'idle') {
      return;
    }

    voicePhaseRef.current = 'transcribing';
    setVoicePhase('transcribing');
    setVoiceStatusText('Finishing recording');
    const generation = ++voiceGenerationRef.current;
    const controller = new AbortController();
    transcriptionControllerRef.current = controller;
    let recordingUri: string | null = null;

    try {
      await restoreVoiceAudioMode().catch(() => undefined);
      const recording = await recorder.stopRecording();
      recordingUri = requireRecordingUri(recording?.fileUri);
      if (
        !mountedRef.current ||
        !allowedRef.current ||
        generation !== voiceGenerationRef.current ||
        controller.signal.aborted
      )
        return;

      const transcription = await transcribeAudioFile(
        recordingUri,
        (status) => {
          if (mountedRef.current && generation === voiceGenerationRef.current)
            applyTranscriptionStatus(status);
        },
        controller.signal,
      );

      if (
        !mountedRef.current ||
        !allowedRef.current ||
        generation !== voiceGenerationRef.current ||
        controller.signal.aborted
      )
        return;
      onChangeInput(mergeTranscriptIntoDraft(latestInputRef.current, transcription.text));
      requestAnimationFrame(() => {
        if (
          mountedRef.current &&
          generation === voiceGenerationRef.current &&
          !controller.signal.aborted
        )
          inputRef.current?.focus();
      });
      resetVoiceUi();
    } catch (error) {
      await restoreVoiceAudioMode().catch(() => undefined);
      if (!mountedRef.current || !allowedRef.current || generation !== voiceGenerationRef.current)
        return;
      resetVoiceUi();
      if (error instanceof TranscriptionError && error.code === 'cancelled') return;
      onErrorMessage(
        error instanceof VoiceRecordingError
          ? getVoiceRecordingErrorMessage(error)
          : getTranscriptionErrorMessage(error),
      );
    } finally {
      if (transcriptionControllerRef.current === controller)
        transcriptionControllerRef.current = null;
      if (recordingUri)
        await FileSystem.deleteAsync(recordingUri, { idempotent: true }).catch(() => undefined);
    }
  }, [applyTranscriptionStatus, inputRef, onChangeInput, onErrorMessage, recorder, resetVoiceUi]);

  const beginVoiceCapture = useCallback(async () => {
    const generation = ++voiceGenerationRef.current;
    longPressHandledRef.current = true;
    shouldFinalizeAfterStartRef.current = false;
    setLockQueued(false);
    setVoiceStatusText('Starting microphone');
    voicePhaseRef.current = 'starting';
    setVoicePhase('starting');

    try {
      await ensureVoiceRecordingReady();
      if (!mountedRef.current || !allowedRef.current || generation !== voiceGenerationRef.current)
        return;
      await recorder.startRecording(VOICE_RECORDING_OPTIONS);
      if (!mountedRef.current || !allowedRef.current || generation !== voiceGenerationRef.current) {
        const stopped = await recorder.stopRecording();
        if (stopped?.fileUri)
          await FileSystem.deleteAsync(stopped.fileUri, { idempotent: true }).catch(
            () => undefined,
          );
        return;
      }
      setVoiceStatusText('Recording');
      voicePhaseRef.current = 'recording';
      setVoicePhase('recording');

      if (shouldFinalizeAfterStartRef.current) {
        shouldFinalizeAfterStartRef.current = false;
        await finalizeVoiceCapture();
      }
    } catch (error) {
      await restoreVoiceAudioMode().catch(() => undefined);
      if (!mountedRef.current || generation !== voiceGenerationRef.current) return;
      resetVoiceUi();
      onErrorMessage(getVoiceRecordingErrorMessage(error));
    }
  }, [finalizeVoiceCapture, onErrorMessage, recorder, resetVoiceUi]);

  useEffect(() => {
    const subscription = AppState.addEventListener('change', (nextState) => {
      if (nextState === 'active') {
        return;
      }

      if (
        voicePhaseRef.current === 'starting' ||
        voicePhaseRef.current === 'recording' ||
        voicePhaseRef.current === 'locked' ||
        voicePhaseRef.current === 'transcribing'
      ) {
        void cancelVoiceCapture();
        onErrorMessage('Dictation stopped when the app left the foreground.');
      }
    });

    return () => {
      subscription.remove();
    };
  }, [cancelVoiceCapture, onErrorMessage]);

  useLayoutEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      void cancelVoiceCaptureRef.current();
    };
  }, []);

  useEffect(() => {
    if (!allowed && voicePhaseRef.current !== 'idle') void cancelVoiceCaptureRef.current();
  }, [allowed]);

  useEffect(() => {
    if (
      recorder.durationMs >= (MAX_DICTATION_SECONDS - 1) * 1000 &&
      (voicePhaseRef.current === 'recording' || voicePhaseRef.current === 'locked')
    ) {
      void finalizeVoiceCapture();
    }
  }, [recorder.durationMs, finalizeVoiceCapture]);

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
    setIsSendButtonPressed(true);
  }, []);

  const handleLongPress = useCallback(() => {
    if (!canUseVoice || !inputIsEmpty) {
      return;
    }

    void beginVoiceCapture();
  }, [beginVoiceCapture, canUseVoice, inputIsEmpty]);

  const handleTouchMove = useCallback(
    (event: GestureResponderEvent) => {
      if (voicePhaseRef.current !== 'recording' || lockQueued || pointerStartYRef.current == null) {
        return;
      }

      const nextLiftDistance = Math.max(0, pointerStartYRef.current - event.nativeEvent.pageY);
      setDragLiftDistance(nextLiftDistance);

      if (nextLiftDistance >= LOCK_THRESHOLD_PX) {
        setLockQueued(true);
        setIsSendButtonPressed(false);
        setDragLiftDistance(0);
        pointerStartYRef.current = null;
        setVoicePhase('locked');
        setVoiceStatusText('Recording locked');
      }
    },
    [lockQueued],
  );

  const handlePressOut = useCallback(() => {
    pointerStartYRef.current = null;
    setIsSendButtonPressed(false);
    setDragLiftDistance(0);

    if (voicePhaseRef.current === 'starting') {
      shouldFinalizeAfterStartRef.current = true;
      return;
    }

    if (voicePhaseRef.current !== 'recording') {
      return;
    }

    void finalizeVoiceCapture();
  }, [finalizeVoiceCapture]);

  const voiceBannerSubtitle = useMemo(() => {
    if (voicePhase === 'starting') {
      return 'Getting the microphone ready...';
    }

    if (lockQueued) {
      return 'Release to keep recording hands-free.';
    }

    return 'Release to transcribe with your server, or swipe up to lock.';
  }, [lockQueued, voicePhase]);

  const voicePanelSubtitle = useMemo(() => {
    if (voicePhase === 'locked') {
      return 'Tap stop to transcribe, or cancel to discard.';
    }

    return voiceStatusText || 'Transcribing your recording.';
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
              {voicePhase === 'starting'
                ? 'Preparing voice capture'
                : formatVoiceDuration(recorder.durationMs)}
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
                  ? formatVoiceDuration(recorder.durationMs)
                  : voiceStatusText || 'Transcribing'}
              </Text>
              <Text style={styles.voicePanelSubtitle}>{voicePanelSubtitle}</Text>
            </View>
          </View>
          {voicePhase === 'locked' || voicePhase === 'transcribing' ? (
            <View style={styles.voicePanelActions}>
              <Pressable
                accessibilityRole="button"
                accessibilityLabel={
                  voicePhase === 'transcribing' ? 'Cancel transcription' : 'Discard recording'
                }
                onPress={() => {
                  void cancelVoiceCapture();
                }}
                style={({ pressed }) => [
                  styles.voiceActionButton,
                  pressed && styles.voiceActionButtonPressed,
                ]}
              >
                <Ionicons name="close" size={16} color={theme.colors.ink} />
              </Pressable>
              {voicePhase === 'locked' ? (
                <Pressable
                  accessibilityRole="button"
                  accessibilityLabel="Stop recording and transcribe"
                  onPress={() => {
                    void finalizeVoiceCapture();
                  }}
                  style={({ pressed }) => [
                    styles.voiceActionButtonPrimary,
                    pressed && styles.voiceActionButtonPressed,
                  ]}
                >
                  <Ionicons name="stop" size={16} color="#fff" />
                </Pressable>
              ) : null}
            </View>
          ) : null}
        </View>
      ) : null}
      <View style={styles.inputWrap}>
        <Pressable
          accessibilityRole="button"
          accessibilityLabel="Attach PDF"
          onPress={onAttachPdf}
          disabled={attachDisabled || voicePhase !== 'idle'}
          style={({ pressed }) => [
            styles.attachButton,
            styles.attachPdfButton,
            pressed && styles.attachButtonPressed,
            (attachDisabled || voicePhase !== 'idle') && styles.attachButtonDisabled,
          ]}
        >
          <Ionicons name="document-attach-outline" size={18} color={theme.colors.ink} />
        </Pressable>
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
          editable={allowed}
          style={[
            styles.input,
            {
              minHeight: minInputHeight,
              maxHeight: maxInputHeight,
              width: '100%',
              paddingRight: 144,
            },
            !allowed && {
              backgroundColor: '#eee',
            },
          ]}
          onChangeText={(value) => {
            latestInputRef.current = value;
            onChangeInput(value);
          }}
          placeholder={placeholder}
          placeholderTextColor="#A7AFB7"
          multiline
          onFocus={onInputFocus}
          scrollEnabled
        />
        <View style={styles.sendButtonDock} pointerEvents="box-none">
          {!shouldShowSlideHint ? null : (
            <View
              pointerEvents="none"
              style={[
                styles.slideHintWrap,
                dragLiftDistance > 0 && {
                  transform: [{ translateY: -Math.min(dragLiftDistance * 0.45, 22) }],
                },
              ]}
            >
              <Ionicons name="arrow-up" size={16} color={theme.colors.accentDeep} />
            </View>
          )}
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
            disabled={sendButtonDisabled}
            pressRetentionOffset={{ top: 220, bottom: 60, left: 60, right: 60 }}
            hitSlop={16}
            {...({ onPressMove: handleTouchMove, onTouchMove: handleTouchMove } as Record<
              string,
              unknown
            >)}
            style={({ pressed }) => [
              styles.inlineSendButton,
              pressed && styles.inlineSendButtonPressed,
              sendButtonDisabled && styles.inlineSendButtonDisabled,
            ]}
          >
            <View
              style={[
                styles.inlineSendButtonVisual,
                sendButtonExpanded && styles.inlineSendButtonVisualExpanded,
                (voicePhase === 'starting' || voicePhase === 'recording') &&
                  styles.inlineSendButtonVisualRecording,
              ]}
            >
              {isSending ? (
                <ActivityIndicator size="small" color="#fff" />
              ) : buttonIconName ? (
                <Ionicons name={buttonIconName} size={sendButtonExpanded ? 26 : 18} color="#fff" />
              ) : null}
            </View>
          </Pressable>
        </View>
      </View>
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
  attachPdfButton: {
    right: 90,
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
    right: -(SEND_BUTTON_EXPANDED_SIZE - SEND_BUTTON_SIZE) / 2,
    bottom: -(SEND_BUTTON_EXPANDED_SIZE - SEND_BUTTON_SIZE) / 2,
    width: SEND_BUTTON_EXPANDED_SIZE,
    height: SEND_BUTTON_EXPANDED_SIZE,
    borderRadius: SEND_BUTTON_EXPANDED_SIZE / 2,
    alignItems: 'center',
    justifyContent: 'center',
  },
  inlineSendButtonVisual: {
    width: SEND_BUTTON_SIZE,
    height: SEND_BUTTON_SIZE,
    borderRadius: SEND_BUTTON_SIZE / 2,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: theme.colors.accent,
    shadowColor: theme.shadow.color,
    shadowOpacity: 0.24,
    shadowRadius: 10,
    shadowOffset: { width: 0, height: 6 },
    elevation: 5,
  },
  inlineSendButtonVisualExpanded: {
    width: SEND_BUTTON_EXPANDED_SIZE,
    height: SEND_BUTTON_EXPANDED_SIZE,
    borderRadius: SEND_BUTTON_EXPANDED_SIZE / 2,
  },
  inlineSendButtonVisualRecording: {
    backgroundColor: theme.colors.accentDeep,
    shadowOpacity: 0.34,
    shadowRadius: 18,
  },
  inlineSendButtonPressed: {
    opacity: 0.9,
  },
  inlineSendButtonDisabled: {
    opacity: 0.75,
  },
  sendButtonDock: {
    position: 'absolute',
    right: 6,
    top: '50%',
    width: SEND_BUTTON_SIZE,
    height: SEND_BUTTON_SIZE,
    transform: [{ translateY: -(SEND_BUTTON_SIZE / 2) }],
    alignItems: 'center',
    justifyContent: 'flex-end',
  },
  slideHintWrap: {
    position: 'absolute',
    bottom: SEND_BUTTON_EXPANDED_SIZE + 10,
    width: 34,
    height: 34,
    borderRadius: 999,
    backgroundColor: 'rgba(255,255,255,0.96)',
    borderWidth: 1,
    borderColor: '#f0cbc6',
    alignItems: 'center',
    justifyContent: 'center',
    shadowColor: theme.shadow.color,
    shadowOpacity: 0.12,
    shadowRadius: 10,
    shadowOffset: { width: 0, height: 6 },
    elevation: 3,
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
