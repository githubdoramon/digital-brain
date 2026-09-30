# Android background location runtime

`DigitalBrainRuntimeService` owns Android background location capture. Location tracking is tied to the persisted Settings toggle; sign-out releases runtime ownership and cancels upload work while keeping already captured samples queued.

## Native capture and upload

Native Fused Location callbacks request balanced accuracy on a ten-minute interval, apply a 50m movement filter, and allow up to one hour of batching. Android can then deliver roughly six ten-minute samples together, reducing app/device wakeups while adding up to an hour of location-upload latency. Provider and device behavior vary, so neither capture nor delivery is an exact schedule or stationary heartbeat. The original capture timestamp and timezone are retained.

The callback atomically stores samples in a bounded native queue of up to 200 entries, then schedules a native WorkManager upload when a nonempty batch arrives. WorkManager waits for network connectivity and retries transient failures with backoff. The worker retrieves a fresh Google ID token silently, posts each sample through the configured frontend proxy to `/mobile/location`, and removes a sample only after a successful response. Authentication, networking, queue mutation, and retries do not start React Native or cross the JS bridge.

The uploader URL and public Google web client ID are configured from the foreground app when tracking is enabled and persisted as nonsecret native configuration. An absent Google account or invalid token leaves samples queued; signing in and enabling location schedules them again. Old JS queue entries are drained once while the app is open during migration. Android unregisters the former Expo drain worker and legacy location/geofence capture registrations. Their task definitions remain imported for upgrade cleanup. The existing iOS Expo location path remains separate; Android design does not depend on cross-platform JavaScript background execution.

## Diagnostics and limits

Runtime status exposes native upload run/sample totals, last duration/outcome/status, and remaining queue count. Sanitized `DigitalBrainRuntime` log entries include worker duration and app-process CPU delta. The diagnostic API may also sample OS battery/current and device awake/deep-sleep counters; those battery and awake counters are device-wide, not app energy attribution. Worker lifetime is not CPU time or energy.

The JSONL log rotates at 2MiB and keeps one previous file. Exports read at most the newest 256KiB of the active file. The location service and WorkManager do not guarantee exact delivery timing, survival after force-stop, continuous CPU wakefulness, or OEM restart behavior. Upload work is bounded per run; later native worker runs continue a backlog.

The location API accepts `android_foreground_location` sample provenance. Deploy the backend schema that recognizes this value so Android foreground samples are accepted.
