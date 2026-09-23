# Android foreground runtime

`DigitalBrainRuntimeService` owns one ongoing **Digital Brain is active**
notification for location tracking, the desired Mentra connection, automatic
capture, wake listening, audio recording and incoming-call alerts. Existing
feature coordinators continue to own their work. Unrelated WorkManager jobs
and iOS execution remain unchanged.

## Ownership and permissions

`RuntimeFeatures` gives each feature an independent claim. Location works
without connected glasses. Settings → Location tracking controls its persisted
preference, enabled by default to preserve existing behavior; sign-out releases
location ownership. Disabling capture, stopping a recording or ending a call
releases only that feature. The final release stops the foreground service.
The notification lists requested activities and offers the normal app-open tap.

Location, desired glasses connection and capture claims survive ordinary
process recreation. Recording, call and wake claims are transient: their real
coordinators must re-establish them. `START_STICKY` restores eligible native
capture and starts a bounded headless JS task registered before Expo Router.
The JS worker can reattach a saved glasses session through the existing single
connection owner, after checking Bluetooth permissions and firmware maintenance.
It never prompts for Bluetooth permission from headless work. Location handoff
failures do not block glasses recovery; the worker rechecks ownership after
uploads and permission checks before reconnecting.

The active mask contains `location` only for enabled location with location
services and required permissions. Starting from background also requires
background location permission unless the existing location foreground session
is continuing. Start the initial session from the visible app after permissions;
retry reconciliation on foreground resume. Rejected starts remain diagnosable.
Glasses work uses `connectedDevice`; glasses audio support uses `mediaPlayback`.
This service receives Bluetooth PCM, never opens the phone microphone and does
not use a `microphone` or `dataSync` foreground type. This avoids while-in-use
microphone promotion and the background `dataSync` time budget.

The versioned Mentra patch replaces the SDK's separate connection and recorder
services with a fixed reflective host adapter. Its only host methods are
`DigitalBrainRuntime.setFeature(Context, String, boolean)` and
`refresh(Context, boolean)`; consumer R8 rules preserve these entry points.
The SDK remains the sole owner of its Bluetooth controller and native recorder.
The notification-listener service remains separate as required by Android,
while its call alert holds a claim on the shared foreground runtime.

## Location capture and upload

Native Fused Location callbacks request balanced accuracy at 600,000 ms,
with a 50m movement filter and up to 1,200,000 ms of delivery batching.
Batching is provider/device dependent; original capture timestamps are retained.
Short visits and small movements may be missed; stationary fixes are not guaranteed.
The requested cadence is not an exact schedule or a stationary heartbeat.
`RuntimeLocationStore` atomically persists up to 200 samples, including capture
timezone. At capacity, it retains the newest samples and logs the dropped count.
Callbacks do not inspect authentication or use the network.

`transferNativeLocations` writes stable sample IDs into the existing durable JS
queue, then acknowledges the native IDs. A failed write keeps the native copy;
a lost acknowledgement can replay safely through JS queue deduplication.
Concurrent transfers join one operation. Invalid or unreadable native data is
reported without silently deleting the file.

A separate uploader sends bounded sequential requests through `/mobile/location`
on the frontend proxy. Concurrent foreground and scheduled triggers join the
same drain and share a 45-second budget; a request retains its existing
15-second timeout. A newly committed location batch offers a prompt upload opportunity.
Periodic JS work runs at most every five minutes for a desired glasses connection,
or every fifteen minutes for location alone. Transient audio and capture-only
owners do not start the location/connection worker. Native capture opportunities
remain every minute; other runtime reconciliation ticks run every five minutes.
Unchanged notifications are reused, and refreshing a running runtime does not
restart its service or reset its polling cadence. The 15-minute WorkManager drain remains a delayed
fallback and honors the location preference. Queues survive offline/error paths.
Location task/geofence registrations from older Android builds are removed and
late callbacks ignored; their definitions remain imported for migration. Older
native builds lacking the new module API retain the legacy Expo path until rebuilt.

## Verification

Automated checks cover independent owner release, durable versus transient
restart state, native-to-JS handoff failures, lost-ACK replay, concurrent drains,
upload time budgets and offline recovery. Run:

```sh
cd mobile
npm run test:background-runtime
npm run test:glasses-recordings
npm run test:glasses-reliability
npm run test:glasses-command
npx tsc --noEmit
cd android
./gradlew :digital-brain-glasses-alerts:compileDebugKotlin :mentra-bluetooth-sdk:compileDebugKotlin :app:processDebugMainManifest
cd ..
npm run test:glasses-native
```

Implementation validation passed on 2026-09-07: TypeScript, focused ESLint,
all listed behavior suites, compiled Kotlin ownership tests, the Android debug
APK build, and the Android Metro/Hermes export. The merged manifest contains
one shared runtime and no retired glasses service declarations. Applying the
SDK patch to the published package reproduces all four integration files
byte-for-byte. The network-based Codex closeout review awaits explicit approval;
automatic approval review rejected source export for that review. Local manual
review fixed startup publication order and atomic-file backup recovery.

Physical validation remains required; no Android device was connected during
initial implementation. Rebuild/install the APK and verify:

- One foreground notification with location alone, then with connected glasses,
  capture, wake listening, recording and a call. Android's own privacy/system
  indicators and the phone's ordinary call notification remain OS-owned.
- Disable each feature separately; location continues after glasses disconnect,
  and the final release removes the app notification.
- Background/lock the phone while moving for multiple ten-minute intervals.
  Inspect native registration/enqueue logs, exported foreground runtime/handoff
  events and backend receipt times separately.
- Lose connectivity, capture samples, restore connectivity and verify chronological
  delivery with original capture times/timezones and no duplicate history.
- Recreate the process, revoke/regrant permissions, and upgrade from a build with
  legacy Expo capture. Verify recovery and explicit blocked/error status.
- Confirm battery behavior during a longer stationary/moving session.

Foreground services and handler ticks do not guarantee exact timing, continuous
CPU wakefulness, survival after force-stop, or OEM restart behavior. No new
exact-alarm or battery-optimization exemption is requested. Native captures
provide durable recovery evidence even when JavaScript delivery is delayed.

## Battery diagnostics

Settings → Download background location log includes runtime owners, native tick
and work-request counts, the last native worker duration, and
`foreground_runtime_work_finished` with trigger reason, JS duration, battery percentage, charging and thermal state. Counters
reset when the process restarts. Native logcat tag `DigitalBrainRuntime` records
location registration parameters, batch sizes/commit durations, coalesced worker
requests, and worker service lifetime (including JS initialization). Worker
lifetime approximates the wake-lock opportunity, not measured CPU or energy.

For a reproducible battery report, export the location log and automatic glasses
capture log after a one-hour unplugged, screen-off session. Include start/end
battery percentage, phone/Android version, active capture schedules, whether
Hey Brain listening was enabled, movement, and connectivity. Compare location
alone with glasses/capture/listening enabled under similar conditions. Android
Settings → Battery → Digital Brain can show the app's background usage. A USB
`adb bugreport` provides system battery/wake-lock attribution when available.

Battery changes validated on 2026-09-12 with TypeScript, focused ESLint/Prettier,
background-runtime tests (including diagnostic failure isolation), compilation of
both Android modules, and native JVM contracts for idle work gating, startup,
periodic retry, capture-only/transient-owner exclusion and sample-trigger
coalescing. No Android device was attached; energy savings remain unmeasured.
These native changes require a rebuilt Android app.

Location log export reads only the newest 256KiB of the current file with legacy
Expo Base64 byte ranges, discarding a partial first JSONL record. The export
notes omitted historical bytes and merges recent bounded snapshot events.
Writes rotate the current JSONL file at 2MiB, retain one `.previous` file, and
cap oversized individual event payloads with an explicit truncated preview.
This also recovers existing oversized logs without reading them into memory.
The location API schema accepts `android_foreground_location`; deploy the backend
schema change so native foreground samples no longer fail HTTP 422 validation.

## Recovery and energy evidence

All automatic connection callers share a process-local cooldown after failure:
5, 10, 20, then 30 minutes (capped). Capture/resume calls cannot bypass it.
Native control-ready clears the cooldown; explicit Connect/Apply settings, repair
and pairing can retry immediately. Process recreation starts a fresh cooldown.

Each headless worker receives a unique token. Its JS finally path acknowledges
that token through the app's native module, stops only the matching worker service even if RN's normal JS completion route
fails. RN owns task bookkeeping, which completes normally or expires at its
existing timeout; the worker never finishes unrelated headless tasks.
Tokens from an old worker cannot stop a newer worker. The existing two-minute
native timeout remains a safety net; it does not cancel already-running JS.
Worker end logs distinguish acknowledgement, teardown near the timeout window
(an inferred timeout), or RN/external teardown.

`foreground_runtime_energy_sample` records process CPU time/deltas, device
awake and deep-sleep deltas, battery charge/current/average current/energy
where supported, charge delta, voltage and temperature, screen/idle/power-saving
state, and completed worker service lifetime. Unsupported properties are null.
Samples use existing work opportunities without adding polling or wake locks.
CPU counters cover this process; battery and awake counters cover the device.
Service lifetime is not a measured app energy total. Charging intervals cannot
be interpreted as discharge. No privileged BATTERY_STATS permission is requested.

For Android's system energy attribution, collect a bug report after an unplugged
screen-off reproduction via Developer options → Take bug report or `adb bugreport`.
The report includes system battery/wake-lock evidence beyond ordinary app access.
Keep it local when inspecting it; it contains device-wide diagnostic information.
These completion/energy APIs require a new native Android build.
