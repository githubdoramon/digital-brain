# Glasses capture pipeline

The Android app owns the Mentra Bluetooth connection and configures the physical
camera button for local gallery capture. Short press captures a maximum-quality
photo with medium compression. Long press starts a 720p30 video with audio;
the next press stops it and the glasses enforce a 15-minute maximum.

## Lifecycle

`@mentra/bluetooth-sdk` emits capture/gallery signals. The app reconciles
immediately after those signals and via an Android best-effort background task
with a 15-minute minimum interval. It first probes the glasses' current local
Wi-Fi camera server and only opens the glasses hotspot when that path is not
reachable. The published SDK's scoped local-network bridge is used when
available so joining the hotspot does not permanently change the phone's
internet route.

Each capture is processed sequentially:

1. Discover it from the paginated v3 manifest (`/api/v3/manifest`), falling back
   to `/api/gallery` for older camera-server builds.
2. Download original bytes to the app-private `Digital Brain/Capture Queue`
   directory, or directly to the user-selected Android Documents folder. The
   picker should be used to grant the actual `Digital Brain/Capture Queue`
   folder (not a parent folder whose child URI the app then reconstructs).
   Android can revoke or reject a persisted grant; in that case the visible
   copy is best-effort and the app-private queue remains authoritative.
3. Validate that the committed local file is non-empty and matches the remote
   size when supplied.
4. Acknowledge it with `/api/v3/ack` when protocol v3 is available, otherwise
   use the legacy `/api/delete-files` endpoint. Mentra retains acknowledged
   captures in recoverable trash for its supported seven-day window.
5. Upload through `/mobile/glasses/captures`. The backend uploads to Immich,
   verifies the asset, finds or creates the exact `Ramon eyes capture` album,
   and commits the capture ID/checksum record.
6. Delete the phone copy only after that backend confirmation.

Before upload, the app resolves the nearest phone location sample within a
10-minute tolerance. It first checks the local location drain queue (which may
contain samples not uploaded yet), then queries
`/mobile/location/history/nearest`. The stored location includes the capture
timestamp, selected sample timestamp, source, and offset provenance. Missing
location never blocks media upload.

The local queue persists every pending/failed record and its retry/backoff
state; it is not silently size-trimmed while media is awaiting upload. Per-entry
upload failures are surfaced in the Glasses capture status card, rather than
being hidden behind a successful reconciliation pass.
Transfers stream directly to disk (including long videos), and the mobile
proxy forwards the request body as a stream. FastAPI keeps the incoming
`UploadFile` in a spooled temporary file; the backend hashes it in 1 MiB
chunks and sends a bounded multipart stream to Immich without materializing
the video in Python memory. Manually
deleting an unuploaded file marks it `missing` to prevent an infinite retry
loop. Captures are never discarded automatically before the backend confirms
the Immich asset.

Android's Storage Access Framework returns document/tree URIs whose exact shape
varies by provider. The app repairs malformed legacy values (including the
duplicated `Documents/Digital Brain` paths produced by older builds), but keeps
the picker-selected folder as the permission boundary. A SAF copy failure never
blocks the durable queue, glasses acknowledgement, or backend upload; the file
stays in app-private storage until the backend confirms it.

## Setup and test protocol

Install `@mentra/bluetooth-sdk` and build a native Android development or
production variant; Expo Go cannot provide the native Bluetooth module. On the
first search, Android must grant the app **Nearby devices** (Bluetooth scan and
connect) permission; the app requests it when **Search for glasses** is tapped.
Open Settings → Glasses capture, tap **Search for glasses**, select the
discovered Mentra Live, and wait for the connection to finish before applying
capture settings. Android's system Bluetooth pairing screen is not a substitute
for the SDK scan: the SDK also stores an app-local default device and BLE
address. No separate "pairing mode" is required when the glasses are awake and
advertising.
The app only attempts automatic reconnection after a default device has been
explicitly saved; an unpaired install remains idle instead of calling
`connectDefault()`. If you want the queue visible in Files, create or select
`Documents/Digital Brain/Capture Queue` itself in the folder picker so Android
grants that exact tree. Configure the backend with the existing Immich variables
(`IMMICH_SERVER_URL`, `IMMICH_API_KEY`) and run the database migrations.

On a physical device, test: one short press online; one short press while the
phone has no internet; a long press followed by stop; a 15-minute-cap recording
only when safe; disconnect during download; kill and relaunch during each
queue state; retry after restoring internet; and duplicate reconciliation.
For each case confirm the original file is present while pending, the capture
is acknowledged on the glasses only after local validation, Immich contains
one asset in `Ramon eyes capture`, and the phone file disappears only after
backend confirmation. Also confirm a user-deleted pending file becomes
`missing`, and that scoped hotspot cleanup restores normal internet access.

The camera-server and ACK behavior is supplied by MentraOS. The package is
MIT-licensed; verify the release's SDK/firmware terms before distribution.
