# V8 wake-word device-test build

V8 is the current Android glasses wake detector in this checkout. It accepts
“hey brain” and “okay brain”. This is a personal real-world test build, not a
validated general release.

The Mentra SDK supplies 16 kHz mono PCM16. A native Sherpa keyword spotter runs
continuously on 20 ms frames and proposes candidates. Native code keeps an
eight-second PCM ring and filtered ambient-level history, so idle PCM does not
cross the React Native bridge to JS. Only a candidate's aligned four-second
audio range crosses to JS for the existing openWakeWord mel/embedding ONNX
verifier. The personalized classifier accepts a candidate at a fixed score of
`0.7614435404638955` or higher. It does not transcribe or learn from audio.
After acceptance, native code snapshots the 1.8-second pre-roll plus audio
accumulated during verification, then forwards command PCM to JS in 80 ms
batches. A rejected proposal has no user-visible effect.

## Glasses VAD experiment and native recovery

Settings → Smart glasses → Troubleshooting → **Glasses speech detection**
controls a persisted, Android-only experiment for Mentra Live. It defaults on;
an explicit saved off choice remains respected.
While idle wake detection owns the microphone, the SDK requests BES VAD using
`cs_swit` type 8. It requests continuous audio before command capture and when
wake listening stops or hands the mic to recording. The app never discards
delivered PCM based on a VAD event; the existing native ring, verifier window
and command pre-roll remain in use. It cannot reconstruct audio clipped by
firmware. Firmware suppression of silence, onset retention, and detection
accuracy still require validation on the installed glasses. Disable the setting
if wake phrases are missed or clipped. This adds no keyword model to the glasses and does not keep their MTK
Android processor awake.

The patched Mentra Live watchdog uses monotonic time. Missing audio still
triggers recovery after five seconds, checked by the existing ten-second
watchdog, but repeated failures back off from 30 seconds to five minutes.
Actual audio resets the backoff. With the experiment enabled, a reported
silence transition grants up to 30 seconds of grace; repeated silence events
do not extend that grace. Unknown/stale silence never suppresses recovery
indefinitely. No VAD events within 15 seconds of listening, or reported speech
without audio for five seconds, requests continuous audio again at the next
watchdog opportunity. Pause/disconnect clears speech and recovery state;
other glasses retain their existing watchdog policy.

Micbeat start removes the old callback before replacing it. Callbacks check
identity, connection, mic intent and playback suspension before sending or
rescheduling. Stop clears callback ownership so old timers cannot restart the
microphone. Blocking wake-model initialization, snapshots and worker waits run
on `Dispatchers.IO`, leaving Expo's shared native function queue available.

`wake_debug_snapshot.native_spotter.glassesVad` includes the experiment
preference, supported-device status, requested VAD state, last speech/audio
ages, packet and silence-packet counters, audio resumes/gaps, silence deferrals,
recovery count/backoff and automatic fallback reason. These are observations,
not proof of reduced power or firmware acknowledgement. To evaluate it on a
production APK, compare enabled/disabled quiet periods and wake attempts,
including an immediate command, reconnect, and recording handoff. Export
snapshots and retained command WAVs; check for clipped wake/command onsets and
recovery loops as well as CPU/battery changes. No device validation has yet
been performed for this experiment.

## Freeze isolation switches

Settings → Smart glasses → Troubleshooting has two persisted Android switches:

- **Wake-word processing** off keeps the glasses mic stream and native PCM
  callback active, but native code counts packets and skips PCM copies, queueing,
  Sherpa decoding, and wake verification. Wake commands cannot trigger. This
  isolates detector work from continuous audio transport.
- **Continuous glasses listening** off stops the wake listener, mic stream, and
  wake runtime. Wake commands cannot trigger until it is turned on again. This
  isolates continuous glasses listening from app-side wake processing.

For a first comparison, record a baseline with both on, then try processing off
with listening on, followed by processing on with listening off. Keep each run
long enough to compare the same idle/scroll/navigation behavior and battery
conditions. The controls persist across app restarts. Wake snapshots include
both settings; the native spotter stats also report `wakeInputMode` as
`DETECTION`, `LISTENING_ONLY`, or `STOPPED`. These switches diagnose correlation;
they do not establish which component causes a freeze without repeated device
observations.

The selected Sherpa model is the 2025 zh-en 3M model from the upstream
`sherpa-onnx` keyword-spotting assets. The app packages its chunk-8 int8 encoder,
float decoder, int8 joiner, tokens and keyword list under the native module's
`android/src/main/assets/wake-word-v8/`. The verifier classifier is
`assets/wake-word/hey-brain-v8.json`; its ONNX feature models are in the same
asset directory. Its standalone classifier threshold is deliberately *not*
used for this two-stage detector. The old JSON classifier remains in the
repository for rollback but is not loaded by this runtime.

## Tests

From `mobile/`:

```bash
npm run test:wake-v8
npm run test:wake-v8:parity
npx tsc --noEmit --pretty false
```

The parity test needs the adjacent `mentra-ramon` lab checkout and compares the
app ONNX/backend score to its frozen context-replay implementation. Build an
installable Android APK with `cd android && ./gradlew :app:assembleRelease
--offline --no-daemon`. A fresh APK will be at
`mobile/android/app/build/outputs/apk/release/app-release.apk`. Building does
not install or test it on glasses.

The package and signing of a generated local Android project depend on the
variant used during prebuild; an `assembleRelease` task alone does not select
the production application ID. With your phone connected for USB debugging,
an appropriate APK can be installed from `mobile/`:

```bash
adb install -r android/app/build/outputs/apk/release/app-release.apk
```

An installed copy of the same package signed by a different key will require
an explicit migration or uninstall; do not remove it without preserving its
data first.

For the normal production-variant local EAS build, run
`npm run eas:build:android:apk` from `mobile/`. That script produces a separate
`com.appcalipse.digitalbrain` APK signed with the configured EAS credentials.
Expo prebuild must carry the ONNX duplicate-library `pickFirst` rules from
`app.config.ts`; editing only generated `android/gradle.properties` will not
fix a clean EAS build. The root Gradle configuration must also pin
`onnxruntime-android` to `1.24.3` across subprojects: Sherpa 1.13.2's JNI
library requires that exact ELF symbol version. A `pickFirst` rule alone can
produce an APK that builds but fails to initialize the wake spotter if
`onnxruntime-react-native` resolves a newer `latest.integration` binary.

For the device test, export the Mentra diagnostics after a few ordinary wake
attempts, missed attempts, and close phrases. Relevant events include
`wake_v8_candidate` (both rejected and accepted), `wake_detected`,
`wake_inference_backlog`, and `wake_pcm_backlog_dropped`. Keep the raw glasses
WAVs when possible so a surprising event can be replayed. Also observe wake
latency, command-audio completeness, battery use, and behavior after locking,
reconnecting, recording audio/video, and reopening the app. The four-second
verifier runs on demand, so phone-side latency and power still require actual
measurement.

For a silent detector, use **Settings → Smart glasses → Troubleshooting →
Record wake debug snapshot**. It writes one `wake_debug_snapshot` line and
shows listener state, valid PCM callback count and peak amplitude, native
samples/decodes, and native wake/reject keyword counts. No audio is saved.
The runtime also writes a snapshot every ten seconds while JS is running, so
the log should continue growing after a clear even if no wake candidates
occur. If the manual snapshot cannot be written, the screen shows the logging
error instead of silently ignoring it. Export with **Download diagnostics**.
`wake_detector_init_failed` and `wake_reconcile_failed` cover initialization
failures that previously could be silent on a connection callback.

The frozen lab result was 21/25 file-level wakes, one false accept in 24 close
confuser clips, and zero events in 2.306 hours of ambient audio. Those numbers
do not guarantee field accuracy. In particular, a close-phrase false accept is
known; raising the threshold alone was not a viable fix. See the lab's
`docs/WAKE_WORD_V8_LAB.md` for source partitions and caveats.

The upstream model-weight redistribution terms have not been established for
a public release. Keep this APK to personal device testing until that is
resolved.
