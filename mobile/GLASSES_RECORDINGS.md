# Local glasses audio recordings

Android Mentra Live, SDK 3.1.1. A new native build is required.

## User behavior

Settings → Glasses → Recordings provides Start, Stop & save, a live duration, a folder-backed audio list, playback/pause, 15-second seek controls, rename and confirmed deletion. It follows the shared padded Card/custom collapsing header patterns. Inline rename is keyboard-aware and preserves the file extension. Existing supported audio files in the folder also appear, ordered by modification time.

The user starts recording while the app is visible. Capture uses only the glasses microphone and continues when locked, backgrounded or navigating away. The runtime notification includes a Stop recording action. No automatic time limit or resume is configured. Disconnect, sign-out, disable, OTA, detected calls, missing microphone audio, unsupported PCM, queue overflow or low storage stop capture and save the received prefix. Firmware updates interrupt capture before OTA begins.

Cellular call state is observed independently of call-alert preferences after Phone permission. Android 12+ audio-mode callbacks detect communication sessions; earlier versions check mode in the native watchdog. When notification access is granted, incoming/active app-call metadata also interrupts recording; missed-call notifications do not. Call visibility is platform-dependent. No caller identity or notification text is read. Microphone and Phone permissions are required; notification permission is requested so the ongoing indicator/action can be shown.

## Native capture and local storage

`GlassesRecording` owns the main-looper lifecycle. Start checks signed-in eligibility, connected readiness, permissions, no active call, foreground Activity visibility, writable destination and available private storage before enabling the microphone. `DigitalBrainRuntimeService` adds the microphone foreground-service type only during capture. Its existing connected-device/location ownership stays independent. Boot and reconnect do not enable capture.

The pinned SDK patch lazily initializes its LC3 decoder on microphone enable and frees it on disable. Phone microphone fallback stays unavailable. Hardware VAD gating is disabled; PCM flows continuously, with no transcript or LC3 retransmission. PCM goes directly from the native SDK listener to a bounded writer, never through JavaScript. The expected input is 16 kHz, mono signed 16-bit little-endian PCM. Invalid or oversized frames stop the session. A bounded queue stops on overflow instead of silently dropping samples.

During capture, private `filesDir/glasses-audio/capture.pcm` is streamed to disk and synced about once per second. Raw storage grows roughly 115 MB/hour. A private atomic journal tracks the chosen folder and destination for recovery. The app holds a partial wake lock during capture and local saving, releases it when saving completes/fails, and runs no idle recording work. Battery impact requires physical-device measurement.

Stop drains queued PCM and encodes AAC-LC at 48 kbps into an M4A container using MediaCodec/MediaMuxer. Timestamps derive from sample counts, not callback wall-clock timing. Encoding/copying run on native IO workers with bounded buffers. The selected Digital Brain main folder receives `Recordings/Glasses-<timestamp>-<suffix>.m4a`. No network, upload or transcription is part of this feature.

The phone retains its raw spool and encoded output until the destination copy closes successfully and its byte count matches. A failed save shows Retry saving and blocks new recording. The retained output is reused on retry. If the process dies, opening the recording screen salvages the existing spool (up to the last successful write); it never restarts capture. Abrupt power loss may lose recent unsynced audio. A partially copied destination can remain visible until retry overwrites it.

Playback uses native MediaPlayer and audio focus. It stops on screen exit/background or a new recording, and does not change system volume. The normal system media output is used for user-requested playback. Diagnostic export includes state, stop reason and captured byte count only, with no recording content, filenames or destination URIs.

## Validation

JVM tests cover long-session sample timestamps, PCM format/buffer bounds and missed-versus-active call interruption policy. Android compilation and TypeScript/lint checks validate integration; they do not prove physical glasses capture, AAC encoder behavior or storage-provider playback.

Device checks: start with connected glasses; record while locked for several minutes; stop and play the resulting M4A; rename/delete it; interrupt with a cellular and an app call and disconnect; verify no automatic resume; simulate a revoked destination grant and retry; kill the process during capture then open Recordings to recover. Compare battery use with recording stopped and active. Force-stop/power loss cannot guarantee continuous capture.
