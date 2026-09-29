# Android background location runtime

`DigitalBrainRuntimeService` owns the Android foreground notification for background location capture. Location tracking is independent of account sign-in; sign-out releases the persisted location owner.

## Capture and delivery

Native Fused Location callbacks request balanced accuracy at a ten-minute interval, use a 50m movement filter, and allow up to twenty minutes of delivery batching. Provider and device behavior can vary, so this is not an exact schedule or stationary heartbeat. Original capture timestamps and timezone are retained.

The native store atomically persists a bounded queue of up to 200 samples. Callbacks do not read authentication or make network requests. JavaScript first writes stable sample IDs to its durable queue and then acknowledges the native copies. A separate bounded uploader posts through the frontend proxy to `/mobile/location`; scheduled work is a delayed recovery path. Upload failures preserve queued data.

Location task and geofence registrations from older Android builds are removed and late callbacks ignored. Their task definitions remain imported for migration. iOS continues to use Expo location capture. Permission checks and foreground-service rejection diagnostics remain part of Android startup and resume reconciliation.

## Diagnostics and limits

Settings → Download background location log includes runtime ownership, native tick and work-request counts, worker duration, and permission-free battery/current, process CPU, device awake/deep-sleep, screen, and thermal samples at existing worker opportunities. Device-wide battery and awake counters do not attribute energy to this app; worker lifetime is not measured CPU or energy.

The JSONL log rotates at 2MiB and keeps one previous file. Exports read at most the newest 256KiB of the active file. Native capture enables durable recovery when JavaScript delivery is delayed; foreground services and handler ticks do not guarantee exact timing, survival after force-stop, continuous CPU wakefulness, or OEM restart behavior.

The location API accepts `android_foreground_location` sample provenance. Deploy the backend schema that recognizes this value so Android foreground samples are accepted.
