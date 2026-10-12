# Glasses listening and transcript export

Android Mentra Live with SDK 3.2.1. Settings → Glasses → Listening is a separate per-account setting, initially off. The user selected English-only on-phone transcription and retention of timestamped transcripts only.

## Lifecycle

Enablement prepares/reuses `ggml-base.en.bin`, requests microphone/Phone permissions and optional notification permission, and arms the microphone foreground-service type while the app is visible. The persistent preference never starts microphone capture from boot. After process death/reboot, opening the app re-arms listening when glasses ownership, sign-in, permission and model eligibility are satisfied. Android/OEM restrictions can prevent continuous capture.

Once armed, the service maintains microphone ownership across screen lock and navigation and waits through reconnect. Calls, OTA and manual M4A recording pause capture; eligible completion/reconnection resumes listening within the same armed session. The shared service notification has Stop listening. Disabling glasses/sign-out/service destruction disarms listening and clears pending speech; the next foreground activation checks eligibility. Phone permission and native audio mode detect cellular/communication sessions; granted notification access adds content-free incoming/active app-call lifecycle sources with a two-minute expiry. Platform call visibility remains best-effort.

Manual recording takes priority and keeps VAD disabled. Listening enables glasses-side VAD and consumes the SDK's `onSpeakingStatus` event separately from VAD setting status. Hardware VAD and audio delivery behavior must be validated on the installed firmware. Silence may suppress PCM: no idle microphone watchdog/restart is added. VAD detects nearby speech and cannot identify the wearer.

## Speech and native transcription

`GlassesSpeechSegmenter` accumulates validated 16 kHz mono PCM in memory after a hardware speech-start event. Speech-end has a 900ms grace period for short pauses; missing audio for two seconds flushes a phrase. Clips shorter than about a third of a second and all-zero silent clips are discarded. Continuous speech is split every twenty seconds; phrases crossing that boundary can lose context. There is no phone VAD model or raw-audio file.

The queue retains at most four pending clips plus one inference and one current capture buffer. Overflow drops the new clip and increments a visible safe diagnostic count. One native worker uses a separate Whisper context, two CPU threads, English decoding and no transcript callbacks or React context. `GlassesWhisperBridge` invokes the installed whisper.rn JNI API using Java-only options; existing interactive chat dictation retains its own context. Model downloading uses an atomic temporary file with minimum-size validation and a shared in-flight download promise.

Capture wake locks are bounded to active speech windows, not idle listening. Each inference has a 90-second wake limit and a 60-second abort request. The model is freed after sixty seconds of queue inactivity or worker shutdown. Inference/model loading cancellation remains cooperative. No idle full-session wake lock is held, so delivery and timers are best-effort while sleeping. No task runs through Headless JS. Compilation is not proof that JNI execution or sleep-time microphone delivery works on a device.

## Minimum speech volume

The account-scoped minimum defaults to 0 (Off), preserving current behavior. Higher/Lower adjusts it in steps of five on a relative digital level scale from 0–100, mapping -60 to -20 dBFS. This is not calibrated acoustic loudness; microphone gain/processing affects it. A segment requires five consecutive 20ms PCM windows above the threshold before queueing (100ms sustained audio). Accepted phrases remain intact, including quiet endings; isolated peaks are rejected. Loud noise can still pass, and quiet speech can be lost. Manual recording is unaffected. The threshold is applied at segment completion; queued jobs are unchanged.

The meter displays a 300ms-window maximum of received PCM RMS, expiring after 600ms without audio. Only the focused foreground screen polls status (500ms), with no additional audio stream or background UI polling. Firmware VAD remains enabled, so sounds not transmitted by the glasses cannot be measured. Preferences persist per account even while listening is off. Diagnostics add only threshold and quiet-segment rejection count. JVM tests cover quiet/normal levels, clicks, preserved quiet endings, signed PCM and disabled filtering. Device calibration and visual validation remain necessary.

## Persistence and export

The native writer appends/fsyncs timestamped transcript entries to private daily JSONL files, scoped by hashed signed-in account. Each entry contains speech start/end timestamps, capture timezone, English language, text, received audio duration and inference duration. These are experimental recognizer outputs and may contain omissions or hallucinations. A setting persists counters and latest transcript time for the interactive screen. Text is retained until app data is cleared; export does not delete originals. Audio buffers are discarded after transcription and never uploaded or saved to storage.

Export transcripts streams a snapshot into the chosen Digital Brain `Exports` directory. Export is JSONL for inspection, with verified byte count and partial-copy cleanup. Export contains private transcript text by explicit user request; general debug exports contain only VAD/event counts, queue/drop/error counts and durations. Never send transcript text to Logcat, analytics, Mirador or backend APIs. Sign-out does not erase existing account-scoped text.

## Validation

JVM tests cover silence exclusion, short-pause merging, maximum speech-buffer size, incomplete VAD events, interruption flushing and short-trigger/silent-clip rejection. Run Android Java/Kotlin compilation, native tests, TypeScript/lint and release-manifest validation.

Physical device checks remain required: initial toggle off; model preparation; speech-start/end delivery; English transcription/export; silence without invented transcripts; locked-screen delivery; long speech/queue overload; cellular and app calls; manual recording priority; disconnect/reconnect; sign-out; reboot then foreground resume. Compare phone/glasses battery with listening off, quiet listening and sustained speech, and inspect exported timing/drop counts. This first stage performs no commands, assistant responses or memory ingestion.
