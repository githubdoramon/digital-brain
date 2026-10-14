# Glasses wake capture

Settings → Glasses → Listening is an independent account-scoped opt-in. It replaces continuous speech transcription. On upgrade, private `glasses-transcripts` logs are deleted and their old counters removed; listening is disabled until the user chooses the Digital Brain storage folder and explicitly enables the new capture behavior. Previously exported user files are not searched or deleted. Manual recordings keep their existing behavior.

## Detection

Android native ownership and firmware VAD remain unchanged. Valid 16 kHz mono signed little-endian PCM passes the firmware speaking gate and the persisted minimum-volume gate before reaching Sherpa. The relative volume scale remains 0–100 (-60 to -20 dBFS), with 0 disabling the volume filter. The gate requires five consecutive 20ms loud-enough windows; once opened, it stays open for the utterance to retain quiet phonemes. A bounded four-second PCM onset ring is replayed at gate opening. Quiet utterances never invoke Whisper. The foreground screen retains the received-audio meter; it cannot show firmware-suppressed sound.

The restored V8 Sherpa first-stage assets are byte-identical to those in Git revision `80f9361d^`: the 2025 zh-en 3M upstream KWS model, chunk-8 int8 encoder and joiner, float decoder, tokens and original keyword file. Assets live in this module's `android/src/main/assets/wake-word-v8/`. Configuration preserves 16 kHz/80 feature dimensions, dither 0, one CPU thread, 16 search paths, score 1, threshold 0.25 and one trailing blank. The original phrase aliases are `hey_brain` and `okay_brain`; rejection keywords never activate capture. This integrates the original Sherpa stage, not the removed personal embedding verifier, and does not claim wearer-only recognition. Source: <https://github.com/k2-fsa/sherpa-onnx/releases/tag/kws-models>.

One native serial executor owns all Sherpa contexts/streams and a bounded 64-event PCM/VAD queue per session. Overload or more than one second of queued input invalidates the session and asks the user to toggle listening again; stale audio never activates later. Generations reject callbacks after pause, calls, disconnect, recording, OTA, disable and account changes. Unknown PCM loss for three seconds discards any partial capture. VAD silence does not restart the microphone. When quiet PCM is suppressed, a short synthetic zero tail allows the decoder to emit its trailing blanks; those samples never enter the saved WAV. Silent utterance resets release/recreate the decoder stream once, without continuous inference or a permanent wake lock.

Upstream model-weight redistribution terms remain unverified, as recorded in the original V8 documentation; the restored assets are for the existing personal device test workflow.

## Command capture

An accepted wake calls the public `MentraBluetoothSdk.rgbLedControl` API from a coroutine using green, 250ms on, zero off, one blink and null package name; green replaces blue while the reported blue failure is investigated. No LED-specific SDK patches, custom dispatcher, raw UART commands or framing override remain. The coroutine bounds the optional response wait at five seconds without blocking capture. Native diagnostics count requests and dispatch errors; response timeout is not proof that a command was not sent. The LED command builder in installed SDK 3.2.1 was compared against downloaded 0.1.21-beta.5 and is identical ignoring whitespace. The six-second cooldown uses monotonic time from the accepted wake. Detection is bypassed during capture, so additional wake phrases do not split a command. It resumes after completion, subject to the remaining cooldown.

Capture includes up to four seconds of received pre-roll, preserving the wake phrase and an immediately spoken command; it may include preceding speech. The maximum saved PCM is thirty seconds. A capture completes after 1.5 seconds of firmware-reported or below-threshold silence, or at the thirty-second time/byte limit. Native timers are deadline-based and best-effort under Android sleep. Calls, disconnect, OTA, manual recording and disable discard unfinished capture without saving a partial file. Audio never received from the glasses cannot be reconstructed. Timestamped JSON distinguishes received audio duration from wall-clock start/end; suppressed silence is not filled into the WAV.

## Transcription and storage

Only completed wake captures enter the bounded four-job Whisper queue. One native worker uses the existing separate English Whisper context, two CPU threads, sixty-second inference abort request, ninety-second wake limit and sixty-second idle model release. Chat dictation retains its own context. No background path uses JS or Headless JS.

A completed capture is first staged atomically with fsync in private account-scoped recovery storage, then transcribed and copied to `<Digital Brain>/Wake captures` as matching timestamp/UUID `.wav` and `.json` files. JSON contains capture/wake/end timestamps, capture timezone, English language, transcript, received audio duration, completion reason and transcription outcome/duration. Raw PCM stays bounded in memory until completion; persisting completed wake audio is explicitly user-authorized. Failed transcription retains the WAV and an explicit outcome for analysis. SAF copy lengths and streaming SHA-256 checksums are checked before private cleanup. Target URIs are journaled for retry without creating a second filename pair. Only JSON metadata marks the final transcription outcome; consumers should wait for both matching files.

Failed copies retain private originals and expose Retry saving; process-recovered pending transcription is marked interrupted when exported by retry. Active inference files are excluded from concurrent retries. At four pending recovery captures, listening pauses until saves are retried. The directory is account-scoped; retry accesses only the signed-in account. Completion receipts keep the last 32 identifiers to avoid duplicate counter increments after interrupted cleanup. Sign-out cancels queued inference and suppresses stale callbacks. Existing completed captures remain user files.

No audio, transcript text, storage/network identifiers, raw exceptions or account identifiers enter general diagnostics, system logs, analytics, Mirador or backend requests. Safe diagnostics retain VAD/wake/quiet/drop/error/LED counts, capture state and inference/audio durations. There is no automatic command execution or upload.

## Validation

JVM tests cover VAD/volume ordering and onset replay, wake plus immediate command, 1.5s silence, six-second cooldown, repeated wakes during capture, interruption discard, thirty-second byte limit, unknown transport loss, synthetic decoder-tail exclusion, ring wrapping and WAV headers. Run native Java/Kotlin compilation/tests, release manifest processing and mobile TypeScript/lint/format checks.

Device validation remains required: Sherpa JNI inference on delivered glasses audio, onset clipping, alias recall, quiet speech and noisy environments, exact command endpointing, locked-phone capture/transcription, SAF writes/retries/process recovery, sign-out and call/recording/OTA/disconnect races. Compare quiet and conversational phone/glasses battery against connection-only and prior listening sessions. Host compilation and replay are not evidence of hardware accuracy or power consumption.

## LED validation and diagnostics

Device testing reported that red, green, orange and white work but blue fails even with a three-second hold. The manual light-test panel is removed at user request. Automatic wake feedback uses the public SDK RGB API with green quick-blink arguments; blue behavior remains unresolved. Historical manual-test presets and observations remain available in native diagnostics.

A persistent bounded 150-event `ledFeedback` trail retains safe request/outcome diagnostics: attempt sequence, timestamps, source/result enums, color, pattern, exact on/off milliseconds, count and response time. Historical manual-test observations remain readable. No request UUID, package, raw response/error, audio or transcript is exported. No LED-specific SDK patch, framing override or raw UART command remains.

A successful SDK reply or timeout alone does not prove physical visibility. Device confirmation establishes the observed light behavior; host compilation and tests do not establish hardware performance or battery consumption.

## Known commands

Completed English transcripts are validated and matched locally before action dispatch. The transcript must start with Hey Brain, Okay Brain or OK Brain; wake verification is exact after case/punctuation normalization and is not speaker authentication. Unknown commands and rejected wakes retain the same WAV/JSON capture but execute nothing. No LLM is called.

| Command | Aliases | Execution |
| --- | --- | --- |
| Open front gate | Open front door | Fixed backend action invokes the configured Home Assistant script. |
| Toggle car gate | Toggle car door | Fixed backend action invokes the configured Home Assistant script. |
| Take a picture | Take a photo | Native SDK save-only capture, followed by existing original-media sync. |

The pure `GlassesCommandMatcher` matches the whole command. Case, punctuation, articles and leading/trailing please are normalized. Verbs and front/car targets remain exact. Non-critical words of at least four letters may have one spelling edit; gait is explicitly accepted as a gate homophone. Extra words, negations and multiple commands are rejected. Only a unique intent is accepted.

`GlassesCommandExecutor` runs on the bounded native speech worker. It checks the original listening generation, active/armed enablement and account before dispatch; call/OTA/recording/disconnect/disable/sign-out invalidate pending action eligibility. Commands older than ninety seconds expire rather than executing later. Private capture JSON schema v2 stores `wakeVerified`, `commandMatch`, `command`, `commandRequestId` and `commandStatus` alongside the transcript. Command and match outcomes include rejected/unknown/cancelled/expired results. Retry saving and process recovery only save files; they never dispatch commands again. An interrupted dispatch can retain `dispatching` when its outcome is unknown.

Gate requests use the existing fresh native Google ID token and configured frontend proxy at `/mobile/glasses/commands`. Only a fixed intent, random request UUID and capture timestamp leave the phone. The backend owns script selection via `GLASSES_FRONT_GATE_SCRIPT` / `GLASSES_CAR_GATE_SCRIPT`; Home Assistant owns the two-second relay pulse. Backend configuration and durable at-most-once receipts are described in [GLASSES_COMMANDS.md](../backend/orchestrator/docs/architecture/GLASSES_COMMANDS.md). No offline queue, automatic network retry or LLM fallback is used. Accepted means Home Assistant accepted the script request, not that a gate sensor confirmed movement.

Photos use the public native SDK `requestPhoto` API with save=true, no webhook, PHOTO/MAX, no compression and sound=false. This invokes save-only gallery capture and bypasses button feedback sounds while preserving the firmware capture light. Existing photo-status events and an explicit sync scheduling fallback feed the same gallery/Immich flow as button captures. The optional SDK response wait is bounded to ten seconds; absent replies are recorded as unknown and never cause an automatic second photo. Actual timing, capture feedback and gallery sync need device validation on installed firmware. No scene analysis is enabled.
