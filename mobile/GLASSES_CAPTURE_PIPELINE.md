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
   directory, or directly to the managed `Glasses Capture Queue` subfolder of
   the user-selected Digital Brain base folder. Android can revoke or reject a
   persisted grant; in that case the visible copy is best-effort and the
   app-private queue remains authoritative.
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
upload failures are surfaced in the Smart glasses sync card, rather than
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
varies by provider. The app treats the selected Digital Brain base folder as the
permission boundary and creates managed subfolders through Android's document
provider rather than reconstructing a child tree URI. It repairs malformed
legacy values (including duplicated `Documents/Digital Brain` paths). A SAF
copy failure never blocks the durable queue, glasses acknowledgement, or
backend upload; the file stays in app-private storage until the backend
confirms it.

## Glasses audio recording (Android-first)

This is an Android-only feature for now. Settings → Smart glasses → Glasses
recordings provides the only start/stop controls: it does not bind or alter a
physical glasses button, so the photo/video controls above retain their current
behavior.

The app reuses its one Mentra SDK session and turns on the glasses microphone
only while a recording is active. Mentra's 16 kHz mono PCM reaches a native
AAC encoder before the React Native bridge, which writes a user-visible `.m4a`
to `Digital Brain/Recordings`. A connected-device foreground service keeps the
native encoder alive while the app is backgrounded or the phone is locked. The
recording does not survive a force-stop or terminated process, and v1 has no
upload, transcription, backend processing, or retention cap.

Each completed file is indexed locally and shown newest first with its
timestamp, duration, and size. The app can play, rename, and delete the actual
SAF file. Bluetooth loss or low free storage finalizes a playable file when the
MP4 container can be completed; otherwise it deletes the invalid partial file.
After a process interruption, the next app launch keeps a partial file only
when Android's media extractor can prove it contains an audio track.

## Setup and test protocol

Install `@mentra/bluetooth-sdk` and build a native Android development or
production variant; Expo Go cannot provide the native Bluetooth module. On the
first search, Android must grant the app **Nearby devices** (Bluetooth scan and
connect) permission; the app requests it when **Search for glasses** is tapped.
Open Settings → Smart glasses, tap **Search for glasses**, select the
discovered Mentra Live, and wait for the connection to finish before applying
capture settings. Android's system Bluetooth pairing screen is not a substitute
for the SDK scan: the SDK also stores an app-local default device and BLE
address. No separate "pairing mode" is required when the glasses are awake and
advertising.
The app only attempts automatic reconnection after a default device has been
explicitly saved; an unpaired install remains idle instead of calling
`connectDefault()`. In Settings → Storage, choose the shared Digital Brain base
folder. The app creates its `Recordings`, `Glasses Capture Queue`, and `Exports`
subfolders through the granted document tree. Configure the backend with the
existing Immich variables (`IMMICH_SERVER_URL`, `IMMICH_API_KEY`) and run the
database migrations.

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

For glasses audio on a physical Android device, test a start/stop recording,
lock/background the phone while recording, disconnect Bluetooth mid-recording,
attempt a recording with low free storage, force-close/relaunch during a
recording, then play, rename, and delete the saved file from both Digital Brain
and another Files-capable app. Confirm the `.m4a` is playable outside the app,
photo/video buttons still work as before, and interrupted partials are either
usable or removed.

## Glasses alerts (Android-first)

Settings → Smart glasses → Glasses alerts is a separate local-only notification
feature. It uses Android notification access to receive a notification's source
package name, filters that package against the user's explicit allow-list, then
plays a short two-note PCM chime only through the active Bluetooth audio output
whose device name matches the remembered Mentra audio device. It never retains,
uploads, logs, or displays notification titles, body text, sender/people data,
actions, or icons. A package-agnostic two-second cooldown prevents bursts from
several selected apps becoming repeated tones.

Incoming phone calls are intentionally independent of the app allow-list.
After the user separately grants `READ_PHONE_STATE`, the notification listener
observes only `RINGING`, `OFFHOOK`, and `IDLE`: ringing starts a distinct
repeating two-note glasses ring and either later state stops it. The app never
reads, retains, or presents a caller number. A temporary media-playback
foreground service keeps the repeating ring alive while the call is incoming;
it has a silent, low-importance Android system notification and stops promptly
when the call state changes.
The notification listener starts that repeat loop before requesting the
foreground service, so an Android/OEM refusal to promote background media
playback does not degrade the alert to a single tone. `CATEGORY_CALL`
notifications from a selected dialer are excluded from the ordinary app-chime
path; only telephony state creates the incoming-call ring.

Do not replace or suppress Android's normal phone notification/ring behavior.
The glasses alert requests transient ducking audio focus, so existing glasses
media can reduce briefly while the alert plays. If the expected Mentra audio
route is not connected, the feature remains silent rather than routing sound to
the handset speaker or an unrelated headset. It also stays silent while Android
reports the phone is both interactive and unlocked; that privacy-preserving
proxy avoids a redundant glasses alert while the user is already using the
phone, without collecting usage history or screen content.

On a physical Android device, pair and audio-pair a Mentra Live, grant
notification access and phone-state permission, select two launchable apps, and
test the app chime plus call-ring previews. Verify a selected app creates one
glasses chime, an unselected app creates none, notification text never appears
in Digital Brain diagnostics, the phone's regular notification sound remains,
media ducks briefly, an incoming call repeats only in the glasses until it is
answered/declined, and disconnecting the Mentra audio route produces no sound
from the phone or another Bluetooth device.
