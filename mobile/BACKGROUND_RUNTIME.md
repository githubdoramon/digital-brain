# Android background location runtime

`DigitalBrainRuntimeService` owns Android background location capture and the optional Mentra Live glasses connection. These have independent persisted owners and share one foreground notification. Glasses uses the `connectedDevice` service type; location retains its existing eligibility and capture policy. See `GLASSES_CONNECTION.md` for pairing, reconnect, OTA and event-driven native chime/call-alert behavior. Alerts use Android notification/call callbacks and the existing glasses owner; they add no separate persistent foreground owner or Headless JS. Location tracking is tied to the persisted Settings toggle; sign-out releases runtime ownership and cancels upload work while keeping already captured samples queued.

## Native capture and upload

Native Fused Location uses two balanced-accuracy requests to implement an OR policy: a timed lane requests a location every ten minutes without a movement filter, and a movement lane requests a callback after 50m displacement with a one-minute fastest interval. A sample is persisted when either ten minutes have elapsed since the last saved sample or movement reaches 50m. A movement-triggered sample restarts the timed request's countdown. The timed lane may batch delivery for up to one hour to reduce wakeups; movement callbacks are not intentionally batched. The capture timestamp and timezone are retained, so batched fixes are evaluated in capture-time order. Android treats intervals as best-effort, not exact deadlines.

Location updates with a reported accuracy worse than 50m are discarded before they advance the capture cadence or enter either mobile queue. The backend also acknowledges these updates with HTTP 204 without storing them, so older queued samples drain instead of retrying. Updates with no reported accuracy remain eligible; the accuracy cutoff is separate from the 50m movement trigger.

The callback atomically stores samples in a bounded native queue of up to 200 entries, then schedules a native WorkManager upload when a nonempty batch arrives. WorkManager waits for network connectivity and retries transient failures with backoff. The worker retrieves a fresh Google ID token silently, posts each sample through the configured frontend proxy to `/mobile/location`, and removes a sample only after a successful response. Authentication, networking, queue mutation, and retries do not start React Native or cross the JS bridge.

The uploader URL and public Google web client ID are configured from the foreground app when tracking is enabled and persisted as nonsecret native configuration. An absent Google account or invalid token leaves samples queued; signing in and enabling location schedules them again. Old JS queue entries are drained once while the app is open during migration. Android unregisters the former Expo drain worker and legacy location/geofence capture registrations. Their task definitions remain imported for upgrade cleanup. The existing iOS Expo location path remains separate; Android design does not depend on cross-platform JavaScript background execution.

## Diagnostics and limits

Runtime status retains current queue, permission, upload totals, and last outcome for runtime health. Settings no longer includes a location debug panel or log export. JS location event logging, the native capture/upload event trail, and per-run diagnostic history have been retired. Upgrades delete the old private JS snapshot/log files and native event history; location capture, queued samples, and delivery remain unchanged. Glasses diagnostics remain available under Settings → Glasses.

The JSONL log rotates at 2MiB and keeps one previous file. Exports read at most the newest 256KiB of the active file. The location service and WorkManager do not guarantee exact delivery timing, survival after force-stop, continuous CPU wakefulness, or OEM restart behavior. Upload work is bounded per run; later native worker runs continue a backlog.

The location API accepts `android_foreground_location` sample provenance. Deploy the backend schema that recognizes this value so Android foreground samples are accepted.

## Glasses media transfers

`GlassesMediaWorker` owns original downloads, resumable proxy uploads, and confirmed cleanup without React Native or Headless JS. Capture/video/button callbacks debounce work for five seconds; a ready connection catches up missed captures. Runs yield after seven minutes and continue durable queues. Existing Wi-Fi gallery reachability is checked only for requested work; hotspot fallback uses a `WifiNetworkSpecifier` scoped to gallery sockets, with no process-wide network binding. Internet requests retain Android routing, including cellular. First hotspot approval can require the foreground Settings screen. App-owned hotspot state is recorded so a later run can clean up after interruption. Disable/sign-out cancel work and preserve account-scoped original files. Fresh silent Google tokens are obtained natively and requests refresh once on 401. See [GLASSES_MEDIA.md](GLASSES_MEDIA.md) for upload acknowledgement and device qualification.

## Local recording processing

Completed manual glasses recordings use a bounded native queue and dataSync ownership in DigitalBrainRuntimeService while draining/encoding/saving. This is independent of microphone capture, glasses signed-in eligibility and location. No second notification/service or Headless JS starts. Stop releases capture immediately; each queue job retains a private spool/journal until verified SAF saving. Android dataSync timeout and service teardown suspend processing cooperatively and retain recovery files for an interactive retry. Recovery is initiated from Recordings, not boot; it never resumes microphone capture. See GLASSES_RECORDINGS.md.

## Settings diagnostics cleanup

Settings lists Glasses (Android), Proposed items, About me, News topics, Notifications, Location tracking (Android), Storage location, and App version, followed by Sign out. Event photo and chat voice transcription debug panels, file logging, and retained voice debug samples have been removed. On launch, the app removes their old private files and the retired location files without reading their contents. Previously exported user files are not deleted. Photo uploads and on-device dictation continue normally. Native diagnostic removal requires a new Android build.
