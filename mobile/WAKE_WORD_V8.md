# V8 wake-word device-test build

V8 is the current Android glasses wake detector in this checkout. It accepts
“hey brain” and “okay brain”. This is a personal real-world test build, not a
validated general release.

The Mentra SDK supplies 16 kHz mono PCM16. A native Sherpa keyword spotter runs
continuously on 20 ms frames and proposes candidates. Only then does the app
run the existing openWakeWord mel/embedding ONNX models over the preceding four
seconds of untrimmed PCM. The personalized classifier accepts a candidate at a
fixed score of `0.7614435404638955` or higher. It does not transcribe or learn
from audio. A confirmed wake follows the existing blue-LED and command-capture
path; a rejected candidate has no user-visible effect.

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

The current test APK is arm64-only, uses package ID
`com.appcalipse.digitalbrain.dev`, and is signed with the local Android debug
key. With your phone connected for USB debugging, install it from `mobile/`:

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
