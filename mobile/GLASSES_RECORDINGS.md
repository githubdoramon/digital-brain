# Local glasses audio recordings

Android Mentra Live, SDK 3.2.1. A new native build is required.

## User behavior

Settings → Glasses → Recordings provides Start, Stop & save, a live duration, a folder-backed audio list with queued processing rows, playback/pause, 15-second seek controls, rename and confirmed deletion. It follows the shared padded Card/custom collapsing header patterns. Inline rename is keyboard-aware and preserves the file extension. Existing supported audio files in the folder also appear, ordered by modification time.

The user starts recording while the app is visible. Capture uses only the glasses microphone and continues when locked, backgrounded or navigating away. The runtime notification includes a Stop recording action. No automatic time limit or resume is configured. Disconnect, sign-out, disable, OTA, detected calls, missing microphone audio, unsupported PCM, queue overflow or low storage stop capture and save the received prefix. Firmware updates interrupt capture before OTA begins.

Cellular call state is observed independently of call-alert preferences after Phone permission. Android 12+ audio-mode callbacks detect communication sessions; earlier versions check mode in the native watchdog. When notification access is granted, incoming/active app-call metadata also interrupts recording; missed-call notifications do not. Call visibility is platform-dependent. No caller identity or notification text is read. Microphone and Phone permissions are required; notification permission is requested so the ongoing indicator/action can be shown.

## Native capture and local storage

`GlassesRecording` owns the main-looper lifecycle. Start checks signed-in eligibility, connected readiness, permissions, no active call, foreground Activity visibility, writable destination and available private storage before enabling the microphone. `DigitalBrainRuntimeService` adds the microphone foreground-service type only during capture. Its existing connected-device/location ownership stays independent. Boot and reconnect do not enable capture.

The pinned SDK patch lazily initializes its LC3 decoder on microphone enable and frees it on disable. Phone microphone fallback stays unavailable. Hardware VAD gating is disabled; PCM flows continuously, with no transcript or LC3 retransmission. PCM goes directly from the native SDK listener to a bounded writer, never through JavaScript. The expected input is 16 kHz, mono signed 16-bit little-endian PCM. Invalid or oversized frames stop the session. A bounded queue stops on overflow instead of silently dropping samples.

Each capture owns a unique `filesDir/glasses-audio/jobs/<UUID>/capture.pcm` spool and atomic journal. PCM is streamed/synced about once per second and grows roughly 115 MB/hour. The capture state is IDLE/STARTING/RECORDING only. Stop detaches the current writer before disabling the microphone, marks its job FINISHING, requests a bounded drain and returns without waiting for encoding, folder preparation or copying. A new capture uses a separate spool and writer; old writer completion/failure and call callbacks cannot change the new capture. Folder validation/journal preparation happen on IO, leaving the UI/main looper available.

`GlassesRecordingSaves` owns a four-slot native queue, including the active capture and failed jobs. Only drained jobs become QUEUED; one job encodes/copies at a time. Failed jobs consume their slot, preserve recovery files, and offer a per-row retry; other free slots allow a new capture. Free-storage checks still stop capture on low disk. Completed-file playback/rename/delete become available after capture stops, independently of saving. Pending copy targets are filtered from the playable folder list until verified. A native library revision refreshes the folder list after completion without polling the storage provider for every progress update.

The runtime keeps microphone foreground-service type only during capture (or an independently armed Listening session). Saving/draining owns dataSync on the same notification/service, with bounded wake locks. Queued local saves may finish after glasses disable/sign-out, without retaining microphone ownership. Android dataSync timeout or service destruction stops further save work cooperatively and preserves jobs for retry/recovery; no teardown starts another service or microphone. Recovery starts from the interactive recording screen, never auto-resuming capture or starting Headless JS. Battery/latency still require device measurements.

Stop drains queued PCM and encodes AAC-LC at 48 kbps into an M4A container using MediaCodec/MediaMuxer. Timestamps derive from sample counts, not callback wall-clock timing. Encoding/copying run on native IO workers with bounded buffers. The screen reports when audio is being finalized, prepared, copied and verified, including copy progress. The selected Digital Brain main folder receives `Recordings/Glasses-<timestamp>-<suffix>.m4a`. No network, upload or transcription is part of this feature.

The phone retains each raw spool and encoded output until the destination copy closes successfully and its byte count matches. Failed jobs expose Retry saving in their own rows; retained encoded output and target URIs are reused. After process death, opening Recordings recovers all journals and salvages their spools without restarting capture. The previous singleton journal/spool is migrated into a separate recovery job. Corrupt job metadata remains a failed recoverable row instead of silently discarding its PCM. Abrupt power loss may lose recent unsynced audio. A partial destination is excluded from the app library while its job remains pending; the storage provider may still expose it outside the app. Each job preserves its original chosen folder unless the user selects a new folder during retry.

Playback uses native MediaPlayer and audio focus. It stops on screen exit/background or a new recording, and does not change system volume. The normal system media output is used for user-requested playback. Diagnostic export includes state, stop reason and captured byte count only, with no recording content, filenames or destination URIs.

## Validation

Queue tests cover concurrent isolated writers, drain-before-encode selection, capture/processing overlap, failed-job retention and the four-slot bound. Test rapid Stop → Start on a device, long recordings, concurrent save/playback, failed SAF permissions, process recovery, sign-out/disable while saving and Android dataSync timeout. Host tests do not prove hardware or storage-provider latency.

JVM tests cover long-session sample timestamps, PCM format/buffer bounds and missed-versus-active call interruption policy. Android compilation and TypeScript/lint checks validate integration; they do not prove physical glasses capture, AAC encoder behavior or storage-provider playback.

Device checks: start with connected glasses; record while locked for several minutes; stop and play the resulting M4A; rename/delete it; interrupt with a cellular and an app call and disconnect; verify no automatic resume; simulate a revoked destination grant and retry; kill the process during capture then open Recordings to recover. Compare battery use with recording stopped and active. Force-stop/power loss cannot guarantee continuous capture.

Optional glasses listening pauses before manual microphone capture starts and may resume as soon as capture stops if its separate setting and armed session remain active. Recording remains continuous, VAD-disabled M4A capture. See [GLASSES_LISTENING.md](GLASSES_LISTENING.md).
