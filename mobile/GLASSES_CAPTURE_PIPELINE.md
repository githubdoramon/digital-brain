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
2. Download original bytes to `Digital Brain/Capture Queue` (or the user-selected
   Android Documents folder).
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
state; it is not silently size-trimmed while media is awaiting upload.
Transfers stream directly to disk (including long videos), and the mobile
proxy forwards the request body as a stream. FastAPI keeps the incoming
`UploadFile` in a spooled temporary file; the backend hashes it in 1 MiB
chunks and sends a bounded multipart stream to Immich without materializing
the video in Python memory. Manually
deleting an unuploaded file marks it `missing` to prevent an infinite retry
loop. Captures are never discarded automatically before the backend confirms
the Immich asset.

## Setup and test protocol

Install `@mentra/bluetooth-sdk` and build a native Android development or
production variant; Expo Go cannot provide the native Bluetooth module. Pair a
Mentra Live, enable camera/gallery mode, sign in, and choose an Android
Documents folder from Settings → Glasses capture. Configure the backend with
the existing Immich variables (`IMMICH_SERVER_URL`, `IMMICH_API_KEY`) and run
the database migrations.

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
